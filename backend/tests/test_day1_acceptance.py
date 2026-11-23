"""Day 1 acceptance checks from the build plan."""
import hashlib
import hmac
import json

from sqlalchemy import select

from app.config import get_settings
from app.models import AiRun, Conversation, Lead, Message, OptOut, WebhookEvent
from tests.conftest import make_user, text, tool, wa_payload

KEY = {"X-API-Key": "dev-lead-key"}


def seed_knowledge(client, db):
    _, h = make_user(db, "owner")
    r = client.post(
        "/api/v1/knowledge",
        headers=h,
        json={"title": "Signboards", "kind": "product", "text": "We make LED signboards and acrylic nameplates.\n\nOur showroom is open 10am to 6pm Monday to Saturday."},
    )
    assert r.status_code == 201
    return h


# 1. A WhatsApp message gets a correct reply -------------------------------------------

def test_whatsapp_message_gets_reply_and_lead_is_filled(client, db, fake_wa, fake_llm):
    seed_knowledge(client, db)
    fake_llm.script = [
        tool("search_knowledge", {"query": "LED signboards"}),
        tool("update_lead", {"name": "Ravi Shah", "requirement": "LED signboard for a shop"}, "tu_2"),
        text("Good afternoon Ravi. We make LED signboards. What size are you considering?"),
    ]
    r = client.post("/api/v1/webhooks/whatsapp", json=wa_payload("wamid.A1", "919876543210", "Hi, do you make LED boards?"))
    assert r.status_code == 200
    assert fake_wa.sent == [("+919876543210", "Good afternoon Ravi. We make LED signboards. What size are you considering?")]
    lead = db.scalar(select(Lead))
    assert lead.phone_e164 == "+919876543210"
    assert lead.name == "Ravi Shah"  # WhatsApp profile name "Ravi" is replaced by the fuller name the customer stated
    assert lead.requirement == "LED signboard for a shop"
    assert lead.stage == "qualifying"
    assert lead.first_reply_at is not None and lead.first_response_seconds < 5
    out = db.scalar(select(Message).where(Message.direction == "outbound"))
    assert out.status == "sent" and out.provider_message_id == "wamid.OUT1"
    run = db.scalar(select(AiRun))
    assert run.decision == "reply" and [t["name"] for t in run.tool_calls] == ["search_knowledge", "update_lead"]
    assert run.retrieved_chunk_ids


def test_replaying_same_webhook_creates_no_duplicates(client, db, fake_wa, fake_llm):
    fake_llm.script = [text("Hello, how can I help?")]
    payload = wa_payload("wamid.DUP", "919800000001", "Hello")
    assert client.post("/api/v1/webhooks/whatsapp", json=payload).status_code == 200
    assert client.post("/api/v1/webhooks/whatsapp", json=payload).status_code == 200
    assert len(db.scalars(select(Lead)).all()) == 1
    assert len(db.scalars(select(Message).where(Message.direction == "inbound")).all()) == 1
    assert len(fake_wa.sent) == 1
    assert len(db.scalars(select(WebhookEvent)).all()) == 1


def test_whatsapp_signature_is_enforced_when_secret_is_set(client, monkeypatch, fake_llm):
    monkeypatch.setattr(get_settings(), "whatsapp_app_secret", "s3cret")
    raw = json.dumps(wa_payload("wamid.SIG", "919800000002", "Hi")).encode()
    bad = client.post("/api/v1/webhooks/whatsapp", content=raw, headers={"x-hub-signature-256": "sha256=deadbeef"})
    assert bad.status_code == 403 and bad.json()["error"]["code"] == "invalid_signature"
    good_sig = "sha256=" + hmac.new(b"s3cret", raw, hashlib.sha256).hexdigest()
    ok = client.post("/api/v1/webhooks/whatsapp", content=raw, headers={"x-hub-signature-256": good_sig})
    assert ok.status_code == 200


def test_whatsapp_verification_handshake(client):
    ok = client.get("/api/v1/webhooks/whatsapp", params={"hub.mode": "subscribe", "hub.verify_token": "dev-verify-token", "hub.challenge": "12345"})
    assert ok.status_code == 200 and ok.text == "12345"
    bad = client.get("/api/v1/webhooks/whatsapp", params={"hub.mode": "subscribe", "hub.verify_token": "nope", "hub.challenge": "1"})
    assert bad.status_code == 403


def test_status_updates_move_message_forward_only(client, db, fake_llm):
    fake_llm.script = [text("Hello.")]
    client.post("/api/v1/webhooks/whatsapp", json=wa_payload("wamid.S1", "919800000003", "Hi"))

    def status(st):
        return {"entry": [{"changes": [{"value": {"statuses": [{"id": "wamid.OUT1", "status": st, "recipient_id": "919800000003"}]}}]}]}

    client.post("/api/v1/webhooks/whatsapp", json=status("read"))
    client.post("/api/v1/webhooks/whatsapp", json=status("delivered"))  # late, must not move back
    db.expire_all()
    assert db.scalar(select(Message).where(Message.direction == "outbound")).status == "read"


# 2. Website form lead -> first reply ---------------------------------------------------

def test_form_lead_gets_first_reply_on_whatsapp(client, db, fake_wa, fake_llm):
    fake_llm.script = [text("Good morning Meera, thank you for your enquiry. What size of board do you need?")]
    r = client.post("/api/v1/public/leads", headers=KEY, json={"name": "Meera", "phone": "+91 98765 11111", "requirement": "Shop front board"})
    assert r.status_code == 201 and r.json()["data"]["first_reply"] == "queued"
    assert fake_wa.sent[0][0] == "+919876511111"
    lead = db.scalar(select(Lead))
    assert lead.source == "website_form" and lead.stage == "qualifying"
    # the synthetic note is sent to the model but never stored as a customer message
    assert db.scalars(select(Message).where(Message.direction == "inbound")).all() == []
    assert "System note" in fake_llm.calls[0]["messages"][0]["content"]


def test_form_lead_validation_and_api_key(client):
    assert client.post("/api/v1/public/leads", json={"name": "x", "phone": "+919800000009"}).status_code == 401
    r = client.post("/api/v1/public/leads", headers=KEY, json={"name": "No contact"})
    assert r.status_code == 422 and r.json()["error"]["code"] == "validation_error"


def test_repeat_form_submission_does_not_message_twice(client, db, fake_wa, fake_llm):
    fake_llm.script = [text("Welcome.")]
    body = {"name": "Meera", "phone": "+919876511111"}
    client.post("/api/v1/public/leads", headers=KEY, json=body)
    again = client.post("/api/v1/public/leads", headers=KEY, json=body)
    assert again.json()["data"]["created"] is False
    assert len(fake_wa.sent) == 1


# 3. Web chat with streamed reply -----------------------------------------------------------

def test_web_chat_message_gets_streamed_reply(client, db, fake_llm):
    fake_llm.script = [text("Thank you for contacting Acme Signs. How may I help you today?")]
    s = client.post("/api/v1/public/chat/sessions", json={"name": "Dev"}).json()["data"]
    cid, tok = s["conversation_id"], {"X-Session-Token": s["session_token"]}
    r = client.post(f"/api/v1/public/chat/sessions/{cid}/messages", headers=tok, json={"text": "Hello"})
    assert r.status_code == 202
    with client.stream("GET", f"/api/v1/public/chat/sessions/{cid}/stream", params={"after": s["stream_cursor"]}, headers=tok) as stream:
        body = "".join(stream.iter_text())
    assert "event: chunk" in body and "event: done" in body
    chunks = [json.loads(l[6:])["text"] for l in body.splitlines() if l.startswith("data: ") and '"text"' in l]
    assert "".join(chunks).strip() == "Thank you for contacting Acme Signs. How may I help you today?"
    assert db.scalar(select(Lead)).source == "web_chat"


def test_web_chat_tokens_are_scoped(client):
    a = client.post("/api/v1/public/chat/sessions", json={}).json()["data"]
    b = client.post("/api/v1/public/chat/sessions", json={}).json()["data"]
    no_token = client.post(f"/api/v1/public/chat/sessions/{a['conversation_id']}/messages", json={"text": "hi"})
    assert no_token.status_code == 401
    wrong = client.post(f"/api/v1/public/chat/sessions/{a['conversation_id']}/messages", headers={"X-Session-Token": b["session_token"]}, json={"text": "hi"})
    assert wrong.status_code == 403


# 4. Safety basics (opt-out is enforced from Day 1) ---------------------------------------------

def test_stop_opts_out_and_nothing_more_is_sent(client, db, fake_wa, fake_llm):
    fake_llm.script = [text("Hello.")]
    client.post("/api/v1/webhooks/whatsapp", json=wa_payload("wamid.B1", "919800000004", "Hi"))
    assert len(fake_wa.sent) == 1
    client.post("/api/v1/webhooks/whatsapp", json=wa_payload("wamid.B2", "919800000004", "STOP"))
    assert "unsubscribed" in fake_wa.sent[-1][1]
    lead = db.scalar(select(Lead))
    assert lead.opted_out_at is not None and lead.stage == "stopped"
    assert db.scalar(select(OptOut)).source == "stop_reply"
    calls_before, sent_before = len(fake_llm.calls), len(fake_wa.sent)
    client.post("/api/v1/webhooks/whatsapp", json=wa_payload("wamid.B3", "919800000004", "Actually I want a quote"))
    assert len(fake_llm.calls) == calls_before and len(fake_wa.sent) == sent_before


def test_agent_failure_sends_nothing_and_is_recorded(client, db, fake_wa, fake_llm):
    def boom(*a, **k):
        raise RuntimeError("model unavailable")

    fake_llm.create = boom
    client.post("/api/v1/webhooks/whatsapp", json=wa_payload("wamid.C1", "919800000005", "Hello"))
    assert fake_wa.sent == []
    run = db.scalar(select(AiRun))
    assert run.decision == "error" and "model unavailable" in run.error


def test_send_failure_is_stored_on_the_message(client, db, fake_wa, fake_llm):
    fake_wa.fail = True
    fake_llm.script = [text("Hello.")]
    client.post("/api/v1/webhooks/whatsapp", json=wa_payload("wamid.D1", "919800000006", "Hi"))
    out = db.scalar(select(Message).where(Message.direction == "outbound"))
    assert out.status == "failed" and "boom" in out.error
    assert db.scalar(select(Lead)).stage == "new"  # no reply went out, so the lead is not "qualifying"


def test_deliver_refuses_opted_out_leads_even_if_called_directly(db, fake_wa):
    """The last line of defence: any code path that sends must be blocked for an opted-out lead."""
    from datetime import datetime, timezone

    from app.services import messaging

    lead = Lead(phone_e164="+919800000020", opted_out_at=datetime.now(timezone.utc))
    db.add(lead)
    db.flush()
    conv = Conversation(lead_id=lead.id, channel="whatsapp", channel_thread_id="919800000020")
    db.add(conv)
    db.flush()
    msg = messaging.queue_outbound(db, conv, lead, "Special offer for you", sender="ai")
    messaging.deliver(db, msg, conv, lead)
    assert msg.status == "failed" and "opted out" in msg.error
    assert fake_wa.sent == []


def test_opt_out_by_number_also_blocks_a_new_lead_record(db, fake_wa):
    """An opt-out stored for a number applies even if the lead row is created again later."""
    from app.services import messaging

    db.add(OptOut(phone_e164="+919800000021", channel="whatsapp", source="stop_reply"))
    lead = Lead(phone_e164="+919800000021")
    db.add(lead)
    db.flush()
    conv = Conversation(lead_id=lead.id, channel="whatsapp", channel_thread_id="919800000021")
    db.add(conv)
    db.flush()
    msg = messaging.queue_outbound(db, conv, lead, "Hello again", sender="ai")
    messaging.deliver(db, msg, conv, lead)
    assert msg.status == "failed" and fake_wa.sent == []
