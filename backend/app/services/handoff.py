"""Handing a lead to a human: summary, assignment, alerts."""
from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import Conversation, Handoff, Lead, Message
from app.services import notifications


def build_summary(db: Session, lead: Lead, conv_id=None, last_n: int = 3) -> str:
    """A short, factual summary for the salesperson, built only from stored lead data and messages."""
    lines = []
    who = lead.name or "Unknown name"
    contact = ", ".join(x for x in (lead.phone_e164, lead.email) if x)
    lines.append(f"{who}" + (f" ({contact})" if contact else ""))
    if lead.company:
        lines.append(f"Company: {lead.company}")
    for label, value in (
        ("Needs", lead.requirement),
        ("Budget", lead.budget_text or (str(lead.budget_amount) if lead.budget_amount else None)),
        ("Timeline", lead.timeline_text),
        ("Location", lead.location_text or lead.city),
    ):
        if value:
            lines.append(f"{label}: {value}")
    if lead.grade and lead.grade != "ungraded":
        lines.append(f"Grade: {lead.grade}" + (f" ({lead.grade_reason})" if lead.grade_reason else ""))
    q = select(Message).where(Message.lead_id == lead.id, Message.status.notin_(["failed", "rejected"]))
    if conv_id:
        q = q.where(Message.conversation_id == conv_id)
    recent = list(reversed(db.scalars(q.order_by(Message.created_at.desc()).limit(last_n)).all()))
    if recent:
        lines.append("Recent messages:")
        for m in recent:
            who_said = "Customer" if m.direction == "inbound" else "We"
            lines.append(f"- {who_said}: {m.body[:160]}")
    return "\n".join(lines)


def create_handoff(db: Session, lead: Lead, reason: str, summary: str | None = None, conv: Conversation | None = None) -> Handoff:
    """Open a handoff and alert the team. An open handoff on the same lead is reused, not duplicated."""
    existing = db.scalar(select(Handoff).where(Handoff.lead_id == lead.id, Handoff.status == "open"))
    if existing:
        if reason not in existing.reason:
            existing.reason = f"{existing.reason}; {reason}"[:300]
        return existing
    body = build_summary(db, lead, conv.id if conv else None)
    if summary:
        body = f"{summary.strip()}\n\n{body}"
    h = Handoff(lead_id=lead.id, reason=reason[:300], summary=body, assigned_to=lead.assigned_to)
    db.add(h)
    if lead.stage not in ("won", "lost", "stopped"):
        lead.stage = "handoff"
    db.flush()
    notifications.notify(
        db,
        "handoff",
        f"Needs a human: {lead.name or lead.phone_e164 or lead.email or 'lead'}",
        f"Reason: {reason}\n{body}\n{notifications.lead_link(lead)}",
        lead,
    )
    return h


def resolve_handoff(db: Session, h: Handoff) -> None:
    h.status = "resolved"
    h.resolved_at = datetime.now(timezone.utc)
    lead = db.get(Lead, h.lead_id)
    if lead and lead.stage == "handoff":
        lead.stage = "qualifying"
