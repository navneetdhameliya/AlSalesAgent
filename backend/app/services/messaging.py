"""Recording and delivering messages, plus STOP handling."""
import re
from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.channels import email as em
from app.channels import whatsapp as wa
from app.channels.webchat import broker
from app.config import get_settings
from app.models import Conversation, Lead, Message, OptOut
from app.services.leads import as_utc, is_opted_out

STOP_WORDS = {"stop", "unsubscribe", "stopall", "end", "quit", "cancel"}
STATUS_RANK = {"queued": 0, "sent": 1, "delivered": 2, "read": 3}


def is_stop_message(text: str) -> bool:
    cleaned = re.sub(r"[^a-z ]", "", text.lower()).strip()
    return cleaned in STOP_WORDS


def record_inbound(
    db: Session, conv: Conversation, lead: Lead, body: str, provider_message_id: str | None = None
) -> Message:
    now = datetime.now(timezone.utc)
    msg = Message(
        conversation_id=conv.id,
        lead_id=lead.id,
        direction="inbound",
        sender="lead",
        channel=conv.channel,
        body=body,
        status="received",
        provider_message_id=provider_message_id,
    )
    db.add(msg)
    lead.last_inbound_at = now
    conv.last_message_at = now
    db.flush()
    return msg


def queue_outbound(
    db: Session, conv: Conversation, lead: Lead, body: str, sender: str = "ai", ai_run_id=None, is_quote: bool = False
) -> Message:
    msg = Message(
        conversation_id=conv.id,
        lead_id=lead.id,
        direction="outbound",
        sender=sender,
        channel=conv.channel,
        body=body,
        status="queued",
        ai_run_id=ai_run_id,
        is_quote=is_quote,
    )
    db.add(msg)
    db.flush()
    return msg


def deliver(db: Session, msg: Message, conv: Conversation, lead: Lead, allow_after_opt_out: bool = False) -> Message:
    """Send a queued message. Opted-out leads receive nothing (except the opt-out confirmation)."""
    if not allow_after_opt_out and is_opted_out(db, lead, conv.channel):
        msg.status = "failed"
        msg.error = "blocked: lead opted out"
        return msg
    try:
        if conv.channel == "whatsapp":
            if not lead.phone_e164:
                raise wa.ChannelError("Lead has no phone number.")
            msg.provider_message_id = wa.whatsapp_client.send_text(lead.phone_e164, msg.body)
        elif conv.channel == "web_chat":
            broker.publish_message(conv.id, msg.id, msg.body)
        elif conv.channel == "email":
            if not lead.email:
                raise wa.ChannelError("Lead has no email address.")
            reply_to = _last_inbound_message_id(db, conv)
            msg.provider_message_id = em.email_client.send(
                lead.email, _email_subject(conv, replying=reply_to is not None), msg.body, em.unsubscribe_url(lead.id), reply_to
            )
        else:
            raise wa.ChannelError(f"Channel '{conv.channel}' is not available yet.")
        msg.status = "sent"
        now = datetime.now(timezone.utc)
        lead.last_outbound_at = now
        conv.last_message_at = now
        if msg.sender in ("ai", "human") and lead.first_reply_at is None:
            lead.first_reply_at = now
            created = as_utc(lead.created_at)
            if created:
                lead.first_response_seconds = max(0, int((now - created).total_seconds()))
    except wa.ChannelError as e:
        msg.status = "failed"
        msg.error = str(e)[:500]
    return msg


def _email_subject(conv: Conversation, replying: bool) -> str:
    if not conv.subject:
        return f"Your enquiry with {get_settings().business_name}"
    if replying and not conv.subject.lower().startswith("re:"):
        return f"Re: {conv.subject}"
    return conv.subject


def _last_inbound_message_id(db: Session, conv: Conversation) -> str | None:
    return db.scalar(
        select(Message.provider_message_id)
        .where(Message.conversation_id == conv.id, Message.direction == "inbound", Message.provider_message_id.is_not(None))
        .order_by(Message.created_at.desc())
        .limit(1)
    )


def opt_out_lead(db: Session, lead: Lead, channel: str, source: str) -> None:
    """Stop all contact with a lead and cancel anything scheduled for them."""
    from app.services import followups  # local import: followups imports messaging

    lead.opted_out_at = datetime.now(timezone.utc)
    lead.opt_out_channel = channel
    lead.stage = "stopped"
    lead.next_followup_at = None
    db.add(OptOut(phone_e164=lead.phone_e164, email=lead.email, channel=channel, source=source))
    followups.cancel_pending(db, lead, "opted_out")
    db.flush()


def handle_stop(db: Session, conv: Conversation, lead: Lead, source: str = "stop_reply") -> None:
    opt_out_lead(db, lead, conv.channel, source)
    confirm = queue_outbound(
        db,
        conv,
        lead,
        f"You have been unsubscribed and will not receive further messages from {get_settings().business_name}.",
        sender="system",
    )
    deliver(db, confirm, conv, lead, allow_after_opt_out=True)


def apply_status_update(db: Session, provider_message_id: str, status: str) -> None:
    msg = db.query(Message).filter(Message.provider_message_id == provider_message_id).first()
    if not msg:
        return
    if status == "failed":
        msg.status = "failed"
    elif STATUS_RANK.get(status, -1) > STATUS_RANK.get(msg.status, -1):
        msg.status = status
