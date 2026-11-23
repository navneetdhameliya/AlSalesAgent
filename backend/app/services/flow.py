"""Conversation flow: what happens when something arrives from a lead.

Runs in a background task with its own database session.
"""
import logging
import time
import uuid
from datetime import datetime, timezone

from sqlalchemy.orm import Session

from app import db as dbmod
from app.agent.runner import run_agent
from app.config import get_settings
from app.models import Conversation, Lead, WebhookEvent
from app.services import followups, handoff, messaging, notifications
from app.services.leads import is_opted_out

log = logging.getLogger("flow")


def respond(db: Session, conv: Conversation, lead: Lead, trigger: str, synthetic_user: str | None = None) -> str:
    """Run the agent and deliver (or hold) its reply.

    Returns what happened: sent | draft | stopped | guardrail_handoff | no_reply | failed.
    """
    try:
        result = run_agent(db, lead, conv.id, trigger, synthetic_user)
    except Exception as e:  # e.g. the model is not configured: a person must see this lead
        log.exception("agent could not start")
        handoff.create_handoff(db, lead, f"AI unavailable: {type(e).__name__}", None, conv)
        return "failed"
    ctx = result.ctx
    if ctx and ctx.stop_requested:
        messaging.handle_stop(db, conv, lead, source="ai_detected")
        return "stopped"
    if result.decision == "error":
        handoff.create_handoff(db, lead, "AI error: no reply could be generated", None, conv)
        return "failed"
    if result.decision == "guardrail_failure":
        handoff.create_handoff(db, lead, "guardrail failure: the AI could not produce a safe reply", None, conv)
        return "guardrail_handoff"
    if not result.reply:
        return "no_reply"
    if lead.human_takeover:
        # A human owns this lead: the AI only drafts. Nothing is sent until someone approves it.
        msg = messaging.queue_outbound(db, conv, lead, result.reply, sender="ai", ai_run_id=result.run_id)
        msg.status = "pending_approval"
        notifications.notify(
            db, "draft_approval", f"AI draft waiting for {lead.name or lead.phone_e164 or lead.email or 'a lead'}",
            f"{result.reply[:300]}\n{notifications.lead_link(lead)}", lead,
        )
        return "draft"
    delay = get_settings().first_reply_delay_seconds
    if delay > 0 and lead.first_reply_at is None:
        time.sleep(min(delay, 120))
    msg = messaging.queue_outbound(db, conv, lead, result.reply, sender="ai", ai_run_id=result.run_id)
    messaging.deliver(db, msg, conv, lead)
    if msg.status != "sent":
        return "failed"
    if lead.stage == "new":
        lead.stage = "qualifying"
    if trigger in ("inbound_message", "new_lead"):
        followups.schedule(db, lead)
    return "sent"


def process_inbound(conversation_id: uuid.UUID, body: str, event_id: uuid.UUID | None = None) -> None:
    """Handle one inbound message that is already stored."""
    db = dbmod.SessionLocal()
    try:
        conv = db.get(Conversation, conversation_id)
        lead = db.get(Lead, conv.lead_id)
        if messaging.is_stop_message(body):
            messaging.handle_stop(db, conv, lead)
        elif is_opted_out(db, lead, conv.channel):
            pass  # silence: nothing is sent to an opted-out lead
        else:
            followups.cancel_pending(db, lead, "replied")  # they answered, so the old schedule is void
            if lead.ai_paused:
                pass  # AI switched off for this lead
            else:
                respond(db, conv, lead, "inbound_message")  # in takeover mode this only stores a draft
        _finish_event(db, event_id, None)
        db.commit()
    except Exception as e:
        db.rollback()
        log.exception("process_inbound failed")
        _finish_event(db, event_id, f"{type(e).__name__}: {e}")
        db.commit()
    finally:
        db.close()


def process_new_lead(conversation_id: uuid.UUID) -> None:
    """First outreach to a lead that arrived through the website form."""
    db = dbmod.SessionLocal()
    try:
        conv = db.get(Conversation, conversation_id)
        lead = db.get(Lead, conv.lead_id)
        if is_opted_out(db, lead, conv.channel) or lead.human_takeover or lead.ai_paused:
            return
        note = (
            "[System note, not from the customer: this person just submitted the website enquiry form. "
            f"What they entered: requirement={lead.requirement or 'not given'}. "
            "Write your first message to them: greet them by name if known, thank them for their enquiry, "
            "and ask one relevant question.]"
        )
        respond(db, conv, lead, "new_lead", synthetic_user=note)
        db.commit()
    except Exception:
        db.rollback()
        log.exception("process_new_lead failed")
    finally:
        db.close()


def _finish_event(db: Session, event_id: uuid.UUID | None, error: str | None) -> None:
    if not event_id:
        return
    ev = db.get(WebhookEvent, event_id)
    if ev:
        ev.processed_at = datetime.now(timezone.utc)
        ev.error = error
