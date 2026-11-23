"""Public endpoints: website form webhook and the web chat widget."""
import asyncio
import json
import uuid

from fastapi import APIRouter, BackgroundTasks, Depends
from pathlib import Path

from fastapi.responses import HTMLResponse, Response, StreamingResponse
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.auth import make_widget_token, require_lead_api_key, widget_session
from app.channels import email as em
from app.channels.webchat import broker
from app.config import get_settings
from app.db import get_db
from app.errors import AppError
from app.services import flow, messaging
from app.services.leads import find_or_create_lead, get_or_create_conversation, normalize_email, normalize_phone

router = APIRouter(prefix="/public", tags=["public"])


class FormLead(BaseModel):
    name: str | None = Field(default=None, max_length=200)
    phone: str | None = Field(default=None, max_length=40)
    email: str | None = Field(default=None, max_length=320)
    company: str | None = Field(default=None, max_length=200)
    requirement: str | None = Field(default=None, max_length=2000)
    source_detail: str | None = Field(default=None, max_length=300, description="Page URL or campaign name")


@router.post("/leads", dependencies=[Depends(require_lead_api_key)], status_code=201)
def create_form_lead(body: FormLead, background: BackgroundTasks, db: Session = Depends(get_db)):
    phone, email = normalize_phone(body.phone), normalize_email(body.email)
    if not phone and not email:
        raise AppError("validation_error", "A phone number or an email is required.", 422, {"phone": "Provide a phone or an email."})
    lead, created = find_or_create_lead(
        db, phone=phone, email=email, name=body.name, source="website_form",
        company=body.company, requirement=body.requirement, source_detail=body.source_detail,
    )
    conv = None
    if phone:  # WhatsApp is the first channel when a phone number is given
        conv = get_or_create_conversation(db, lead, "whatsapp", phone.lstrip("+"))
    elif email:
        conv = get_or_create_conversation(db, lead, "email", email)
        if not conv.subject:
            conv.subject = f"Your enquiry with {get_settings().business_name}"
    db.commit()
    if conv is not None and created:
        background.add_task(flow.process_new_lead, conv.id)
    return {"data": {"lead_id": str(lead.id), "created": created, "first_reply": "queued" if conv is not None and created else "not_sent"}}


# ---- Web chat widget ----------------------------------------------------------------

class ChatStart(BaseModel):
    name: str | None = Field(default=None, max_length=200)
    page_url: str | None = Field(default=None, max_length=300)


class ChatMessage(BaseModel):
    text: str = Field(min_length=1, max_length=2000)


@router.post("/chat/sessions", status_code=201)
def start_chat(body: ChatStart, background: BackgroundTasks, db: Session = Depends(get_db)):
    lead, _ = find_or_create_lead(db, name=body.name, source="web_chat", source_detail=body.page_url)
    conv = get_or_create_conversation(db, lead, "web_chat", str(uuid.uuid4()))
    db.commit()
    return {
        "data": {
            "conversation_id": str(conv.id),
            "session_token": make_widget_token(conv.id, lead.id),
            "stream_cursor": broker.last_id(conv.id),
        }
    }


@router.get("/chat/sessions/{conversation_id}/history")
def chat_history(conversation_id: uuid.UUID, _claims: dict = Depends(widget_session), db: Session = Depends(get_db)):
    """Restore the conversation after a page reload. Drafts and failed messages are never shown."""
    from app.models import Message

    rows = db.scalars(
        select(Message)
        .where(Message.conversation_id == conversation_id, Message.status.notin_(["failed", "pending_approval", "rejected", "draft"]))
        .order_by(Message.created_at, Message.id)
    ).all()
    return {"data": [{"direction": m.direction, "body": m.body} for m in rows]}


@router.post("/chat/sessions/{conversation_id}/messages", status_code=202)
def send_chat_message(
    conversation_id: uuid.UUID,
    body: ChatMessage,
    background: BackgroundTasks,
    claims: dict = Depends(widget_session),
    db: Session = Depends(get_db),
):
    from app.models import Conversation, Lead

    conv = db.get(Conversation, conversation_id)
    if conv is None or conv.channel != "web_chat":
        raise AppError("not_found", "Chat not found.", 404)
    lead = db.get(Lead, conv.lead_id)
    cursor = broker.last_id(conv.id)
    messaging.record_inbound(db, conv, lead, body.text.strip())
    db.commit()
    background.add_task(flow.process_inbound, conv.id, body.text.strip())
    return {"data": {"accepted": True, "stream_cursor": cursor}}


@router.get("/chat/sessions/{conversation_id}/stream")
async def stream_chat(conversation_id: uuid.UUID, after: int = 0, _claims: dict = Depends(widget_session)):
    """Server-Sent Events. Ends after one complete AI message ('done') or after about 25 seconds."""

    async def gen():
        cursor = after
        for _ in range(100):  # 100 x 0.25s
            events = broker.events_after(conversation_id, cursor)
            for e in events:
                cursor = e["id"]
                yield f"id: {e['id']}\nevent: {e['event']}\ndata: {json.dumps(e['data'])}\n\n"
                if e["event"] == "done":
                    return
            if not events:
                await asyncio.sleep(0.25)
        yield f"event: timeout\ndata: {json.dumps({'cursor': cursor})}\n\n"

    return StreamingResponse(gen(), media_type="text/event-stream", headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})


# ---- Email unsubscribe (link in every email footer; also the one-click List-Unsubscribe target) ----

def _unsubscribe(token: str, db: Session) -> HTMLResponse:
    from app.models import Lead

    lead_id = em.read_unsubscribe_token(token)
    lead = db.get(Lead, uuid.UUID(lead_id)) if lead_id else None
    if lead is None:
        return HTMLResponse("<h3>This unsubscribe link is not valid.</h3>", status_code=400)
    if not lead.opted_out_at:
        messaging.opt_out_lead(db, lead, "email", "unsubscribe_link")
        db.commit()
    name = get_settings().business_name
    return HTMLResponse(f"<h3>You have been unsubscribed.</h3><p>{name} will not contact you again.</p>")


@router.get("/unsubscribe")
def unsubscribe_page(token: str, db: Session = Depends(get_db)):
    return _unsubscribe(token, db)


@router.post("/unsubscribe")
def unsubscribe_one_click(token: str, db: Session = Depends(get_db)):
    return _unsubscribe(token, db)


WIDGET_JS = Path(__file__).resolve().parents[1] / "static" / "widget.js"


@router.get("/widget.js")
def widget_script():
    return Response(WIDGET_JS.read_text(encoding="utf-8"), media_type="application/javascript", headers={"Cache-Control": "public, max-age=300"})
