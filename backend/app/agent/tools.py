"""Tools the agent can call. The model never supplies prices; the server computes them."""
import re

from sqlalchemy.orm import Session

from app.agent.context import RunContext
from app.models import Lead
from app.services import guardrails, handoff, knowledge, notifications, quotes
from app.services.leads import apply_lead_update
from app.services.pricing import PriceError, compute_price

SIGNALS = ("budget_timeline", "asked_quote", "asked_person", "fast_replies")
STRONG_SIGNALS = {"budget_timeline", "asked_quote", "asked_person"}
QUOTE_WORDS = re.compile(r"\b(quot\w*|price|pricing|cost|how much|rate|estimate|proposal)\b", re.I)
PERSON_WORDS = re.compile(r"\b(call|speak|talk|meet\w*|visit|demo|human|person|representative|phone me|contact me)\b", re.I)

TOOL_SCHEMAS = [
    {
        "name": "search_knowledge",
        "description": (
            "Search the company knowledge base (products, services, FAQs, policies). "
            "Always call this before answering a question about the business."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "What to look for, in a few words"},
                "top_k": {"type": "integer", "description": "Number of passages, default 5", "minimum": 1, "maximum": 8},
            },
            "required": ["query"],
        },
    },
    {
        "name": "update_lead",
        "description": "Save details the customer has told you. Only include fields the customer actually stated.",
        "input_schema": {
            "type": "object",
            "properties": {
                "name": {"type": "string"},
                "phone": {"type": "string"},
                "email": {"type": "string"},
                "company": {"type": "string"},
                "city": {"type": "string"},
                "requirement": {"type": "string", "description": "What the customer needs"},
                "budget_text": {"type": "string", "description": "Budget as the customer said it"},
                "timeline_text": {"type": "string", "description": "When they need it"},
                "location_text": {"type": "string", "description": "Where it is needed"},
                "deal_size_estimate": {"type": "number"},
            },
        },
    },
    {
        "name": "set_grade",
        "description": (
            "Grade the lead hot, warm or cold and record why. Hot means ready to buy soon: the customer has given both "
            "budget and timeline, asked for a quotation or price, or asked to talk to a person. Call this whenever the "
            "picture changes."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "grade": {"type": "string", "enum": ["hot", "warm", "cold"]},
                "reason": {"type": "string", "description": "One sentence a salesperson can read"},
                "signals": {"type": "array", "items": {"type": "string", "enum": list(SIGNALS)}},
            },
            "required": ["grade", "reason"],
        },
    },
    {
        "name": "get_price",
        "description": (
            "Calculate the price for a product and quantity from the company price rules. This is the only valid source "
            "of prices. If it returns an error, do not guess: say a team member will confirm and call hand_off_to_human."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "product": {"type": "string"},
                "quantity": {"type": "number"},
                "attributes": {"type": "object", "description": "Inputs such as width or height, only if the customer gave them"},
            },
            "required": ["product", "quantity"],
        },
    },
    {
        "name": "draft_quotation",
        "description": (
            "Prepare a formal quotation for a team member to approve. The server calculates the figures. Write only a short "
            "cover message with no prices. The customer receives it only after approval, so tell them a team member will "
            "send it shortly and do not state the amounts."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "product": {"type": "string"},
                "quantity": {"type": "number"},
                "attributes": {"type": "object"},
                "cover_message": {"type": "string", "description": "One or two polite sentences, no amounts"},
            },
            "required": ["product", "quantity", "cover_message"],
        },
    },
    {
        "name": "hand_off_to_human",
        "description": (
            "Bring in a team member. Use it for discount requests, delivery dates, custom or complex requests, complaints, "
            "anything not in the knowledge, or when the customer asks for a person."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "reason": {"type": "string"},
                "summary": {"type": "string", "description": "Optional short note for the salesperson"},
            },
            "required": ["reason"],
        },
    },
    {
        "name": "book_meeting",
        "description": "Record that the customer wants a call or meeting. A team member will confirm the time.",
        "input_schema": {
            "type": "object",
            "properties": {
                "preferred_time": {"type": "string", "description": "As the customer said it"},
                "notes": {"type": "string"},
            },
            "required": ["preferred_time"],
        },
    },
    {
        "name": "stop_contact",
        "description": "Use when the customer clearly asks not to be contacted again, in any words. Stops all messages.",
        "input_schema": {"type": "object", "properties": {"reason": {"type": "string"}}},
    },
]


def _verified_signals(lead: Lead, claimed: set[str], ctx: RunContext) -> set[str]:
    """Keep only the signals the stored data supports."""
    said = "\n".join(ctx.inbound_texts)
    ok = set()
    if "budget_timeline" in claimed and (lead.budget_text or lead.budget_amount) and lead.timeline_text:
        ok.add("budget_timeline")
    if "asked_quote" in claimed and QUOTE_WORDS.search(said):
        ok.add("asked_quote")
    if "asked_person" in claimed and PERSON_WORDS.search(said):
        ok.add("asked_person")
    if "fast_replies" in claimed:
        ok.add("fast_replies")
    return ok


def _set_grade(db: Session, lead: Lead, ctx: RunContext, inp: dict) -> dict:
    grade = inp.get("grade")
    if grade not in ("hot", "warm", "cold"):
        return {"error": "grade must be hot, warm or cold"}
    reason = str(inp.get("reason") or "").strip()[:500]
    claimed = {s for s in (inp.get("signals") or []) if s in SIGNALS}
    signals = _verified_signals(lead, claimed, ctx)
    note = None
    if grade == "hot" and not (signals & STRONG_SIGNALS):
        grade, note = "warm", "Hot needs budget and timeline both given, a quote request, or a request for a person. Graded warm."
    previous = lead.grade
    lead.grade, lead.grade_reason, lead.score_signals = grade, reason, sorted(signals)
    if grade == "hot" and previous != "hot":
        if lead.stage in ("new", "qualifying"):
            lead.stage = "qualified"
        summary = handoff.build_summary(db, lead, ctx.conversation.id)
        notifications.notify(
            db, "hot_lead", f"Hot lead: {lead.name or lead.phone_e164 or lead.email or 'new lead'}",
            f"{summary}\n{notifications.lead_link(lead)}", lead,
        )
    db.flush()
    result = {"grade": grade, "saved": True}
    if note:
        result["note"] = note
    return result


def _price(db: Session, ctx: RunContext, inp: dict):
    try:
        res = compute_price(db, str(inp.get("product", "")), inp.get("quantity"), inp.get("attributes") or {})
    except PriceError as e:
        return None, {
            "error": str(e),
            "note": "Do not guess. Tell the customer a team member will confirm, and call hand_off_to_human.",
        }
    return res, None


def execute_tool(db: Session, lead: Lead, name: str, tool_input: dict, ctx: RunContext) -> tuple[dict, list[str]]:
    """Run a tool. Returns (result for the model, retrieved chunk ids)."""
    if name == "search_knowledge":
        top_k = int(tool_input.get("top_k") or 5)
        hits = knowledge.search(db, str(tool_input.get("query", "")), max(1, min(top_k, 8)))
        if not hits:
            return {"results": [], "note": "Nothing found. Tell the customer you will check with the team."}, []
        ctx.knowledge_text.extend(c.content for c, _ in hits)
        return {"results": [{"title": c.item.title, "content": c.content} for c, _ in hits]}, [str(c.id) for c, _ in hits]
    if name == "update_lead":
        changed = apply_lead_update(lead, tool_input)
        db.flush()
        return {"saved": sorted(changed.keys())}, []
    if name == "set_grade":
        return _set_grade(db, lead, ctx, tool_input), []
    if name == "get_price":
        res, err = _price(db, ctx, tool_input)
        if err:
            return err, []
        ctx.price_results.append(res)
        return res.as_dict(), []
    if name == "draft_quotation":
        res, err = _price(db, ctx, tool_input)
        if err:
            return err, []
        cover = str(tool_input.get("cover_message") or "")
        problems = guardrails.check_text(cover, ctx, allow_amounts=False)
        if problems:
            return {"error": "Cover message rejected: " + " ".join(problems)}, []
        q = quotes.create_pending(db, lead, ctx.conversation, res, tool_input.get("attributes") or {}, cover)
        ctx.quotation_ids.append(str(q.id))
        ctx.quote_totals |= {str(res.unit_price), str(res.total)}
        return {
            "quotation_id": str(q.id),
            "status": "pending_approval",
            "note": "Not sent. Tell the customer a team member will send the quotation shortly. Do not state the amounts.",
        }, []
    if name == "hand_off_to_human":
        reason = str(tool_input.get("reason") or "AI requested a human").strip()
        handoff.create_handoff(db, lead, reason, tool_input.get("summary"), ctx.conversation)
        ctx.handoff_reasons.append(reason)
        return {"handed_off": True, "note": "Tell the customer a team member will follow up. Make no promises about timing."}, []
    if name == "book_meeting":
        when = str(tool_input.get("preferred_time") or "").strip()
        reason = f"Meeting requested for: {when}" + (f". {tool_input['notes']}" if tool_input.get("notes") else "")
        handoff.create_handoff(db, lead, reason, None, ctx.conversation)
        lead.stage = "booked"
        ctx.meeting_requested = True
        ctx.handoff_reasons.append(reason)
        return {"recorded": True, "note": "Tell the customer a team member will confirm the time. Do not confirm it yourself."}, []
    if name == "stop_contact":
        ctx.stop_requested = True
        return {"stopped": True, "note": "Do not write any further message to the customer."}, []
    return {"error": f"Unknown tool {name}"}, []
