"""Agent loop: build context, call Claude with tools, record the run."""
import json
import time
import uuid
from dataclasses import dataclass
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.agent.context import RunContext
from app.agent.llm import LLM, get_llm
from app.agent.tools import TOOL_SCHEMAS, execute_tool
from app.config import get_settings
from app.models import AiRun, Conversation, Lead, Message
from app.services import app_settings, guardrails
from app.services.leads import lead_missing_fields, lead_summary_dict

PROMPT_VERSION = "v2"


@dataclass
class AgentResult:
    reply: str | None
    run_id: uuid.UUID
    decision: str  # reply | no_action | error | guardrail_failure
    ctx: RunContext | None = None


def load_system_prompt(lead: Lead, db: Session | None = None) -> str:
    s = get_settings()
    cfg = app_settings.get_all(db) if db is not None else app_settings.defaults()
    template = (Path(s.prompts_dir) / "system.md").read_text(encoding="utf-8")
    known = {k: v for k, v in lead_summary_dict(lead).items() if v and k not in ("id", "stage", "grade")}
    missing = lead_missing_fields(lead)
    context = "Known so far: " + (", ".join(f"{k}={v}" for k, v in known.items()) or "nothing") + ".\n"
    context += "Still to learn: " + (", ".join(missing) if missing else "nothing, the lead is fully qualified") + "."
    return template.format(persona_name=cfg["persona_name"], business_name=cfg["business_name"], lead_context=context)


def build_messages(history: list[Message], synthetic_user: str | None) -> list[dict]:
    """Map stored messages to alternating user/assistant turns that start with a user turn."""
    turns: list[dict] = []
    for m in history:
        role = "user" if m.direction == "inbound" else "assistant"
        if not m.body.strip() or m.status == "failed":
            continue
        if turns and turns[-1]["role"] == role:
            turns[-1]["content"] += "\n" + m.body
        else:
            turns.append({"role": role, "content": m.body})
    if synthetic_user:
        if turns and turns[-1]["role"] == "user":
            turns[-1]["content"] += "\n" + synthetic_user
        else:
            turns.append({"role": "user", "content": synthetic_user})
    while turns and turns[0]["role"] != "user":
        turns.pop(0)
    return turns


def run_agent(
    db: Session,
    lead: Lead,
    conversation_id: uuid.UUID,
    trigger: str,
    synthetic_user: str | None = None,
    llm: LLM | None = None,
) -> AgentResult:
    s = get_settings()
    llm = llm or get_llm()
    started = time.monotonic()
    run = AiRun(lead_id=lead.id, conversation_id=conversation_id, trigger=trigger, model=llm.model, prompt_version=PROMPT_VERSION)
    db.add(run)
    db.flush()

    history = list(
        reversed(
            db.scalars(
                select(Message)
                .where(Message.conversation_id == conversation_id)
                .order_by(Message.created_at.desc(), Message.id.desc())
                .limit(s.agent_history_messages)
            ).all()
        )
    )
    messages = build_messages(history, synthetic_user)
    system = load_system_prompt(lead, db)
    tool_log: list[dict] = []
    chunk_ids: list[str] = []
    in_tok = out_tok = 0
    reply: str | None = None
    decision: str | None = None
    ctx = RunContext(
        conversation=db.get(Conversation, conversation_id),
        inbound_texts=[m.body for m in history if m.direction == "inbound"][-6:],
    )
    flags: dict = {"violations": [], "regenerated": False}

    try:
        for _ in range(s.agent_max_tool_rounds + 1):  # one extra round for the guardrail rewrite
            res = llm.create(system, messages, TOOL_SCHEMAS)
            in_tok += res.input_tokens
            out_tok += res.output_tokens
            messages.append({"role": "assistant", "content": res.content})
            tool_uses = [b for b in res.content if b["type"] == "tool_use"]
            if not tool_uses:
                candidate = "\n".join(b["text"].strip() for b in res.content if b["type"] == "text").strip() or None
                if ctx.stop_requested:
                    break  # the customer asked not to be contacted: nothing more is written
                problems = guardrails.check_reply(candidate, ctx) if candidate else []
                if problems:
                    flags["violations"].append(problems)
                    if not flags["regenerated"]:
                        flags["regenerated"] = True
                        messages.append(
                            {
                                "role": "user",
                                "content": "[System note, not from the customer: your draft was blocked. "
                                + " ".join(problems)
                                + " Write a corrected reply that follows the rules.]",
                            }
                        )
                        continue
                    decision = "guardrail_failure"
                    break
                reply = candidate
                break
            results = []
            for tu in tool_uses:
                output, ids = execute_tool(db, lead, tu["name"], tu["input"], ctx)
                chunk_ids.extend(ids)
                tool_log.append({"name": tu["name"], "input": tu["input"]})
                results.append({"type": "tool_result", "tool_use_id": tu["id"], "content": json.dumps(output)})
            messages.append({"role": "user", "content": results})
        else:
            run.error = "tool round limit reached without a final reply"
        run.decision = decision or ("reply" if reply else "no_action")
    except Exception as e:  # recorded, never sent to the customer
        run.error = f"{type(e).__name__}: {e}"[:1000]
        run.decision = "error"
        reply = None

    run.input_tokens, run.output_tokens = in_tok, out_tok
    run.latency_ms = int((time.monotonic() - started) * 1000)
    run.tool_calls = tool_log
    run.guardrail_flags = flags
    run.retrieved_chunk_ids = chunk_ids
    db.flush()
    return AgentResult(reply=reply, run_id=run.id, decision=run.decision, ctx=ctx)
