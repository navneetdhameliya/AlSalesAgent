"""Provider webhooks. Every event is stored first; a unique event id makes retries harmless."""
import json
from datetime import datetime, timezone

from fastapi import APIRouter, BackgroundTasks, Depends, Query, Request
from fastapi.responses import PlainTextResponse
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.channels import email as em
from app.channels import whatsapp as wa
from app.config import get_settings
from app.db import get_db
from app.errors import AppError
from app.models import OptOut, WebhookEvent
from app.services import flow, messaging
from app.services.leads import find_or_create_lead, get_or_create_conversation, normalize_email, normalize_phone

router = APIRouter(prefix="/webhooks", tags=["webhooks"])


@router.get("/whatsapp")
def verify(
    mode: str = Query("", alias="hub.mode"),
    token: str = Query("", alias="hub.verify_token"),
    challenge: str = Query("", alias="hub.challenge"),
):
    if mode == "subscribe" and token == get_settings().whatsapp_verify_token:
        return PlainTextResponse(challenge)
    raise AppError("forbidden", "Verification failed.", 403)


def _store_event(db: Session, provider: str, event_id: str, payload: dict) -> WebhookEvent | None:
    """Insert the event. Returns None if this event id was already received."""
    ev = WebhookEvent(provider=provider, event_id=event_id, payload=payload)
    db.add(ev)
    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        return None
    return ev


@router.post("/whatsapp")
async def whatsapp_inbound(request: Request, background: BackgroundTasks, db: Session = Depends(get_db)):
    raw = await request.body()
    if not wa.verify_signature(raw, request.headers.get("x-hub-signature-256")):
        raise AppError("invalid_signature", "Signature check failed.", 403)
    try:
        payload = json.loads(raw or b"{}")
    except json.JSONDecodeError:
        raise AppError("validation_error", "Body is not valid JSON.", 400)

    messages, statuses = wa.parse_webhook(payload)

    for st in statuses:
        ev = _store_event(db, "whatsapp", f"{st['id']}:{st['status']}", st)
        if ev is None:
            continue
        messaging.apply_status_update(db, st["id"], st["status"])
        ev.processed_at = datetime.now(timezone.utc)
        db.commit()

    for m in messages:
        ev = _store_event(db, "whatsapp", m["id"], m)
        if ev is None:
            continue  # duplicate delivery from Meta: already handled
        phone = normalize_phone(m["from"])
        if not phone:
            ev.error = "unusable sender number"
            db.commit()
            continue
        lead, _ = find_or_create_lead(db, phone=phone, name=m.get("name"), source="whatsapp")
        conv = get_or_create_conversation(db, lead, "whatsapp", phone.lstrip("+"))
        messaging.record_inbound(db, conv, lead, m["body"], provider_message_id=m["id"])
        db.commit()
        background.add_task(flow.process_inbound, conv.id, m["body"], ev.id)

    return {"received": True}  # Meta needs a fast 200


@router.post("/email/inbound")
async def email_inbound(request: Request, background: BackgroundTasks, db: Session = Depends(get_db)):
    """Inbound customer email and delivery events (bounce, complaint), as normalized JSON."""
    raw = await request.body()
    if not em.verify_signature(raw, request.headers.get("x-email-signature")):
        raise AppError("invalid_signature", "Signature check failed.", 403)
    try:
        payload = json.loads(raw or b"{}")
    except json.JSONDecodeError:
        raise AppError("validation_error", "Body is not valid JSON.", 400)
    item = em.parse_inbound(payload)
    if item is None:
        return {"received": True, "ignored": True}
    ev = _store_event(db, "email", f"{item['type']}:{item['event_id']}", item)
    if ev is None:
        return {"received": True, "duplicate": True}

    if item["type"] in ("bounce", "complaint"):
        # A bounce or spam complaint stops email to that address only.
        addr = normalize_email(item["email"])
        db.add(OptOut(email=addr, channel="email", source=item["type"]))
        ev.processed_at = datetime.now(timezone.utc)
        db.commit()
        return {"received": True}

    email = normalize_email(item["from_email"])
    if not email:
        ev.error = "unusable sender address"
        db.commit()
        return {"received": True, "ignored": True}
    lead, _ = find_or_create_lead(db, email=email, name=item.get("from_name"), source="email")
    conv = get_or_create_conversation(db, lead, "email", email)
    if not conv.subject and item.get("subject"):
        conv.subject = item["subject"]
    messaging.record_inbound(db, conv, lead, item["text"], provider_message_id=item.get("message_id"))
    db.commit()
    background.add_task(flow.process_inbound, conv.id, item["text"], ev.id)
    return {"received": True}
