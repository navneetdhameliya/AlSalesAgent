"""Export lead events to the owner's webhook: signed JSON, retried with back-off.

Events are written to webhook_outbox in the same transaction that changes the lead, so none are lost;
deliver_outbox() (run by the scheduler) sends them.
"""
import hashlib
import hmac
import json
import logging
import uuid
from datetime import datetime, timedelta, timezone

import httpx
from sqlalchemy import event, inspect, select
from sqlalchemy.orm import Session

from app import db as dbmod
from app.errors import AppError
from app.models import Lead, WebhookOutbox, new_id
from app.services import app_settings

log = logging.getLogger("webhook_export")
MAX_ATTEMPTS = 6


def sign(secret: str, body: bytes) -> str:
    return "sha256=" + hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()


def _payload(kind: str, lead: Lead) -> dict:
    from app.api.serializers import lead_out

    return {"event": kind, "lead": lead_out(lead)}


@event.listens_for(Session, "before_flush")
def _capture_lead_events(session: Session, _ctx, _instances) -> None:
    new_leads = [o for o in session.new if isinstance(o, Lead)]
    changed = [
        o for o in session.dirty
        if isinstance(o, Lead) and inspect(o).attrs.stage.history.has_changes() and inspect(o).attrs.stage.history.deleted
    ]
    if not new_leads and not changed:
        return
    with session.no_autoflush:
        url = app_settings.get(session, "webhook_export_url")
    if not url:
        return
    for lead in new_leads:
        if lead.id is None:
            lead.id = new_id()
        lead.stage = lead.stage or "new"
        session.add(WebhookOutbox(event="lead.created", lead_id=lead.id, payload=_payload("lead.created", lead)))
    for lead in changed:
        session.add(WebhookOutbox(event="lead.stage_changed", lead_id=lead.id, payload=_payload("lead.stage_changed", lead)))


def _post(url: str, secret: str, kind: str, payload: dict, delivery_id: uuid.UUID) -> int:
    app_settings.validate_webhook_url(url)  # re-check at send time
    body = json.dumps({**payload, "delivery_id": str(delivery_id), "sent_at": datetime.now(timezone.utc).isoformat()}).encode()
    headers = {"Content-Type": "application/json", "X-Event": kind, "X-Delivery-Id": str(delivery_id)}
    if secret:
        headers["X-Signature"] = sign(secret, body)
    r = httpx.post(url, content=body, headers=headers, timeout=10, follow_redirects=False)
    return r.status_code


def send_test(db: Session) -> dict:
    cfg = app_settings.get_all(db)
    if not cfg["webhook_export_url"]:
        raise AppError("not_configured", "Set the webhook URL first.", 409)
    try:
        status = _post(cfg["webhook_export_url"], cfg["webhook_export_secret"], "test", {"event": "test", "message": "Test from AI Sales Executive"}, new_id())
    except (httpx.HTTPError, AppError) as e:
        return {"ok": False, "status": None, "error": str(getattr(e, "message", e))[:200]}
    return {"ok": status < 300, "status": status}


def deliver_outbox(now: datetime | None = None) -> dict:
    now = now or datetime.now(timezone.utc)
    db = dbmod.SessionLocal()
    sent = failed = retry = 0
    try:
        cfg = app_settings.get_all(db)
        rows = db.scalars(
            select(WebhookOutbox).where(WebhookOutbox.status == "pending", WebhookOutbox.next_attempt_at <= now).order_by(WebhookOutbox.created_at).limit(50)
        ).all()
        for row in rows:
            row.attempts += 1
            try:
                if not cfg["webhook_export_url"]:
                    raise RuntimeError("no webhook URL configured")
                status = _post(cfg["webhook_export_url"], cfg["webhook_export_secret"], row.event, row.payload, row.id)
                if status >= 300:
                    raise RuntimeError(f"receiver answered {status}")
                row.status, row.last_error = "sent", None
                sent += 1
            except Exception as e:
                row.last_error = f"{type(e).__name__}: {getattr(e, 'message', e)}"[:300]
                if row.attempts >= MAX_ATTEMPTS:
                    row.status = "failed"
                    failed += 1
                else:
                    row.next_attempt_at = now + timedelta(minutes=2 ** row.attempts)
                    retry += 1
        db.commit()
    except Exception:
        db.rollback()
        log.exception("deliver_outbox failed")
    finally:
        db.close()
    return {"sent": sent, "retry": retry, "failed": failed}
