"""Follow-ups on day 1, 3 and 7 after the last message we sent, with the skip rules.

Scheduling happens whenever the AI sends a message. The scheduler (a loop started in main.py,
or a call to run_due_followups) sends each due follow-up through the same agent and guardrails
as any other reply.
"""
import logging
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

from sqlalchemy import select
from sqlalchemy.orm import Session

from app import db as dbmod
from app.config import get_settings
from app.models import Conversation, Followup, Handoff, Lead
from app.services import app_settings, messaging
from app.services.leads import as_utc, is_opted_out

log = logging.getLogger("followups")
DONE_STAGES = {"won", "lost", "stopped", "booked"}
MIN_GAP = timedelta(hours=12)  # never two follow-ups back to back, even after downtime


def offsets(db: Session) -> list[int]:
    return [int(x) for x in app_settings.get(db, "followup_days").split(",") if x.strip()]


def cancel_pending(db: Session, lead: Lead, reason: str) -> int:
    rows = db.scalars(select(Followup).where(Followup.lead_id == lead.id, Followup.status == "scheduled")).all()
    now = datetime.now(timezone.utc)
    for r in rows:
        r.status, r.skip_reason, r.done_at = "skipped", reason, now
    lead.next_followup_at = None
    db.flush()
    return len(rows)


def schedule(db: Session, lead: Lead, base: datetime | None = None) -> None:
    """Start a fresh follow-up cycle counted from `base` (default: now). Replaces any pending rows."""
    if lead.stage in DONE_STAGES or lead.human_takeover or lead.ai_paused:
        return
    cancel_pending(db, lead, "rescheduled")
    base = base or datetime.now(timezone.utc)
    first_due = None
    for step, days in enumerate(offsets(db), start=1):
        due = base + timedelta(days=days)
        db.add(Followup(lead_id=lead.id, step=step, day_offset=days, due_at=due))
        first_due = first_due or due
    lead.next_followup_at = first_due
    lead.followup_step = 0
    db.flush()


def in_send_window(db: Session, now: datetime) -> bool:
    cfg = app_settings.get_all(db)
    local = now.astimezone(ZoneInfo(get_settings().business_timezone))
    return cfg["send_window_start"] <= local.hour < cfg["send_window_end"]


def skip_reason(db: Session, lead: Lead, fu: Followup, conv: Conversation | None) -> str | None:
    if lead.stage in DONE_STAGES:
        return f"stage_{lead.stage}"
    if conv is None or is_opted_out(db, lead, conv.channel):
        return "opted_out"
    if lead.human_takeover:
        return "human_takeover"
    if lead.ai_paused:
        return "ai_paused"
    inbound = as_utc(lead.last_inbound_at)
    if inbound and inbound > as_utc(fu.created_at):
        return "replied"
    if db.scalar(select(Handoff.id).where(Handoff.lead_id == lead.id, Handoff.status == "open").limit(1)):
        return "handoff_open"
    return None


def latest_conversation(db: Session, lead: Lead) -> Conversation | None:
    return db.scalars(
        select(Conversation).where(Conversation.lead_id == lead.id).order_by(Conversation.last_message_at.desc().nulls_last()).limit(1)
    ).first()


def _send_one(db: Session, lead: Lead, fu: Followup, conv: Conversation) -> str:
    from app.services import flow  # local import: flow imports followups

    note = (
        f"[System note, not from the customer: this person has not replied for {fu.day_offset} day(s). "
        "Write one short, polite follow-up that restarts the conversation with a single relevant question. "
        "Do not repeat your previous message. Do not mention prices, discounts or delivery dates.]"
    )
    outcome = flow.respond(db, conv, lead, "followup", synthetic_user=note)
    return outcome


def run_due_followups(now: datetime | None = None) -> list[dict]:
    """Send every follow-up that is due. Returns one record per follow-up that was sent or skipped."""
    now = now or datetime.now(timezone.utc)
    db = dbmod.SessionLocal()
    results: list[dict] = []
    try:
        due = db.scalars(
            select(Followup).where(Followup.status == "scheduled", Followup.due_at <= now).order_by(Followup.lead_id, Followup.step)
        ).all()
        handled_leads: set = set()
        for fu in due:
            lead = db.get(Lead, fu.lead_id)
            conv = latest_conversation(db, lead)
            reason = skip_reason(db, lead, fu, conv)
            if reason:
                fu.status, fu.skip_reason, fu.done_at = "skipped", reason, datetime.now(timezone.utc)
                results.append({"lead_id": str(lead.id), "step": fu.step, "status": "skipped", "reason": reason})
                continue
            if lead.id in handled_leads:
                continue  # one per lead per run; the next step waits for a later run
            last_out = as_utc(lead.last_outbound_at)
            if (last_out and now - last_out < MIN_GAP) or not in_send_window(db, now):
                continue  # stays scheduled
            handled_leads.add(lead.id)
            outcome = _send_one(db, lead, fu, conv)
            fu.done_at = datetime.now(timezone.utc)
            if outcome == "sent":
                fu.status = "sent"
                lead.followup_step = fu.step
                if fu.step >= len(offsets(db)):
                    lead.grade, lead.grade_reason = "cold", f"No reply after {fu.step} follow-ups"
            else:
                fu.status, fu.skip_reason = "skipped", outcome
            db.flush()  # sessions here do not autoflush; the query below must see this follow-up as sent
            pending = db.scalars(
                select(Followup).where(Followup.lead_id == lead.id, Followup.status == "scheduled").order_by(Followup.step)
            ).first()
            lead.next_followup_at = pending.due_at if pending else None
            results.append({"lead_id": str(lead.id), "step": fu.step, "status": fu.status, "reason": fu.skip_reason})
            db.commit()
        db.commit()
    except Exception:
        db.rollback()
        log.exception("run_due_followups failed")
    finally:
        db.close()
    return results
