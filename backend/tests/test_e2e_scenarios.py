"""Ten scripted end-to-end scenarios. Each one is a whole customer journey through the public API,
with the dashboard endpoints used the way the dashboard uses them. The model is scripted; everything else is real."""
import json
from datetime import datetime, timedelta, timezone

from sqlalchemy import select

from app.channels import email as em
from app.models import Conversation, Followup, Handoff, Lead, Message, Notification, OptOut, Quotation, WebhookOutbox
from app.services import followups, reports, webhook_export
from tests.conftest import make_user, text, tool, wa_payload
from tests.test_day2_acceptance import (  # noqa: F401  (fixtures are imported so pytest registers them)
    KEY, email_in, fake_email, hot_script, inbound, only_lead, quote_script, seed_catalog, sent_texts, setup_takeover,
    start_silent_lead, window_time, with_phone,
)
from tests.test_day3_backend import http, mailer, upload  # noqa: F401

API = "/api/v1"
CUSTOMER = "+919876543210"


# 1. Lead to Hot to handoff, run through the dashboard and WhatsApp ---------------------------------------

def test_01_whatsapp_lead_goes_hot_then_to_a_person(client, db, fake_wa, fake_llm):
    _, mgr = with_phone(db, "manager", "+919000000010", "Mina")
    sales, sales_h = with_phone(db, "sales", "+919000000011", "Sam")

    fake_llm.script = [text("Hello, how can I help?")]
    inbound(client, "wamid.E1", "Hi")
    rows = client.get(f"{API}/leads", headers=mgr).json()["data"]
    assert len(rows) == 1 and rows[0]["grade"] == "ungraded" and rows[0]["source"] == "whatsapp"
    lead_id = rows[0]["id"]

    assert client.post(f"{API}/leads/{lead_id}/assign", headers=mgr, json={"profile_id": str(sales.id)}).status_code == 200
    assert any("assigned to you" in b.lower() for b in sent_texts(fake_wa, "+919000000011"))

    fake_llm.script = hot_script()
    inbound(client, "wamid.E2", "I need an LED signboard, budget 50,000, within 3 weeks")
    hot = client.get(f"{API}/leads", headers=sales_h, params={"grade": "hot"}).json()["data"]
    assert [l["id"] for l in hot] == [lead_id]
    detail = client.get(f"{API}/leads/{lead_id}", headers=sales_h).json()["data"]
    assert detail["budget"] == "INR 50,000" and detail["score_signals"] == ["budget_timeline"] and detail["stage"] == "qualified"
    assert any("Hot lead: Ravi Shah" in b for b in sent_texts(fake_wa, "+919000000011"))

    fake_llm.script = [tool("hand_off_to_human", {"reason": "Customer wants to speak to a person"}), text("A team member will call you.")]
    inbound(client, "wamid.E3", "Can someone call me?")
    detail = client.get(f"{API}/leads/{lead_id}", headers=sales_h).json()["data"]
    assert detail["handoff"]["reason"] == "Customer wants to speak to a person" and "INR 50,000" in detail["handoff"]["summary"]

    assert client.post(f"{API}/leads/{lead_id}/takeover", headers=sales_h).status_code == 200
    assert client.post(f"{API}/leads/{lead_id}/send", headers=sales_h, json={"text": "Hi Ravi, Sam here. Calling now."}).status_code == 201
    assert sent_texts(fake_wa, CUSTOMER)[-1] == "Hi Ravi, Sam here. Calling now."
    assert client.post(f"{API}/handoffs/{detail['handoff']['id']}/resolve", headers=sales_h).status_code == 200
    assert client.get(f"{API}/leads/{lead_id}", headers=sales_h).json()["data"]["handoff"] is None
    thread = client.get(f"{API}/leads/{lead_id}/messages", headers=sales_h).json()["data"]
    assert [m["sender"] for m in thread if m["direction"] == "outbound"][-1] == "human"


# 2. Website form, silence, follow-ups on day 1, 3, 7, then cold -------------------------------------------

def test_02_silent_form_lead_is_followed_up_three_times_then_marked_cold(client, db, fake_wa, fake_llm):
    _, mgr = make_user(db, "manager")
    base = start_silent_lead(client, db, fake_llm, fake_wa)
    fake_llm.script = []
    for step, days in ((1, 1), (2, 3), (3, 7)):
        res = followups.run_due_followups(window_time(base + timedelta(days=days)))
        assert [(r["step"], r["status"]) for r in res] == [(step, "sent")]
    assert len(fake_wa.sent) == 4
    lead = client.get(f"{API}/leads", headers=mgr).json()["data"][0]
    assert lead["grade"] == "cold"


# 3. Website chat widget ----------------------------------------------------------------------------------

def test_03_web_chat_visitor_is_answered_from_the_knowledge_base_and_shows_in_the_dashboard(client, db, fake_llm):
    _, owner = make_user(db, "owner")
    r = client.post(f"{API}/knowledge", headers=owner, json={"title": "Warranty", "kind": "policy", "text": "LED signboards carry a two year warranty on the panel and driver."})
    assert r.status_code == 201
    fake_llm.script = [tool("search_knowledge", {"query": "warranty"}), text("Our LED signboards come with a two year warranty.")]
    s = client.post(f"{API}/public/chat/sessions", json={"name": "Dev"}).json()["data"]
    cid, tok = s["conversation_id"], {"X-Session-Token": s["session_token"]}
    assert client.post(f"{API}/public/chat/sessions/{cid}/messages", headers=tok, json={"text": "What warranty do you give?"}).status_code == 202
    with client.stream("GET", f"{API}/public/chat/sessions/{cid}/stream", params={"after": s["stream_cursor"]}, headers=tok) as stream:
        body = "".join(stream.iter_text())
    assert "event: done" in body and "two year warranty" in body
    hits = client.get(f"{API}/leads", headers=owner, params={"q": "dev"}).json()["data"]
    assert len(hits) == 1 and hits[0]["source"] == "web_chat"
    history = client.get(f"{API}/public/chat/sessions/{cid}/history", headers=tok).json()["data"]
    assert [m["direction"] for m in history][-2:] == ["inbound", "outbound"]


# 4. Quotation held for approval --------------------------------------------------------------------------

def test_04_quote_request_waits_for_a_manager_then_reaches_the_customer(client, db, fake_wa, fake_llm):
    _, owner = make_user(db, "owner")
    _, mgr = with_phone(db, "manager", "+919000000010")
    _, sales = make_user(db, "sales")
    seed_catalog(client, owner)
    fake_llm.script = quote_script()
    inbound(client, "wamid.Q1", "Quote for 3 LED signboards 4 by 2 please")
    queue = client.get(f"{API}/approvals", headers=mgr).json()["data"]
    assert len(queue["quotations"]) == 1 and queue["quotations"][0]["total"] == "10800.00"
    assert all("10,800" not in b for b in sent_texts(fake_wa, CUSTOMER))
    qid = queue["quotations"][0]["id"]
    assert client.post(f"{API}/quotations/{qid}/approve", headers=sales, json={}).status_code == 403
    assert client.post(f"{API}/quotations/{qid}/approve", headers=mgr, json={}).status_code == 200
    assert "INR 10,800" in sent_texts(fake_wa, CUSTOMER)[-1]
    assert client.get(f"{API}/approvals", headers=mgr).json()["data"]["quotations"] == []


# 5. Discount request goes to a person, and the AI never invents one -----------------------------------------

def test_05_discount_request_is_handed_over_and_an_invented_discount_is_never_sent(client, db, fake_wa, fake_llm):
    _, mgr = with_phone(db, "manager", "+919000000010")
    fake_llm.script = [text("I can do 10% off for you."), text("Okay, 15% then.")]
    inbound(client, "wamid.X1", "Any discount?")
    assert sent_texts(fake_wa, CUSTOMER) == []
    h = db.scalar(select(Handoff))
    assert h.status == "open" and "guardrail" in h.reason
    notes = client.get(f"{API}/notifications", headers=mgr).json()["data"]
    assert any(n["kind"] == "handoff" for n in notes)
    assert client.post(f"{API}/handoffs/{h.id}/resolve", headers=mgr).status_code == 200
    assert client.get(f"{API}/handoffs", headers=mgr).json()["data"] == []


# 6. Email enquiry, threaded reply, unsubscribe ---------------------------------------------------------------

def test_06_email_enquiry_is_answered_in_thread_and_unsubscribe_stops_everything(client, db, fake_email, fake_wa, fake_llm):
    fake_llm.script = [text("Thanks Asha. What size of board do you need?")]
    client.post(f"{API}/public/leads", headers=KEY, json={"name": "Asha", "email": "asha@example.com"})
    assert len(fake_email.sent) == 1
    fake_llm.script = [text("We make LED boards of every size.")]
    assert email_in(client, "e-1", "Do you make 4x2?").status_code == 200
    assert len(fake_email.sent) == 2 and len(db.scalars(select(Conversation)).all()) == 1
    lead = only_lead(db)
    assert client.get(f"{API}/public/unsubscribe", params={"token": em.make_unsubscribe_token(lead.id)}).status_code == 200
    before = len(fake_email.sent)
    email_in(client, "e-2", "one more thing")
    assert len(fake_email.sent) == before and fake_wa.sent == []
    assert followups.run_due_followups(window_time(datetime.now(timezone.utc) + timedelta(days=10))) == []
    assert len(fake_email.sent) == before


# 7. STOP on WhatsApp ------------------------------------------------------------------------------------------

def test_07_stop_ends_contact_follow_ups_and_human_sends(client, db, fake_wa, fake_llm):
    _, mgr = make_user(db, "manager")
    base = start_silent_lead(client, db, fake_llm, fake_wa)
    inbound(client, "wamid.S1", "STOP", sender="919876543210")
    lead = only_lead(db)
    assert lead.opted_out_at is not None and db.scalar(select(OptOut)) is not None
    assert followups.run_due_followups(window_time(base + timedelta(days=2))) == []
    r = client.post(f"{API}/leads/{lead.id}/send", headers=mgr, json={"text": "Hello again"})
    assert r.status_code == 409 and r.json()["error"]["code"] == "opted_out"
    assert client.get(f"{API}/leads/{lead.id}", headers=mgr).json()["data"]["opted_out"] is True


# 8. Takeover, AI drafts, edit before send, hand back ------------------------------------------------------------

def test_08_takeover_turns_ai_replies_into_drafts_a_person_can_edit(client, db, fake_wa, fake_llm):
    lead, mgr = setup_takeover(client, db, fake_llm)
    fake_wa.sent.clear()
    fake_llm.script = [text("We open at 10am.")]
    inbound(client, "wamid.T9", "What time do you open?")
    assert sent_texts(fake_wa, CUSTOMER) == []
    draft = client.get(f"{API}/approvals", headers=mgr).json()["data"]["drafts"][0]
    r = client.post(f"{API}/messages/{draft['id']}/approve", headers=mgr, json={"text": "We open at 10am, Monday to Saturday."})
    assert r.status_code == 200 and sent_texts(fake_wa, CUSTOMER) == ["We open at 10am, Monday to Saturday."]
    assert client.post(f"{API}/leads/{lead.id}/release", headers=mgr).status_code == 200
    fake_llm.script = [text("Happy to help further.")]
    inbound(client, "wamid.T10", "Thanks")
    assert sent_texts(fake_wa, CUSTOMER)[-1] == "Happy to help further."  # AI is back in charge


# 9. CSV import, export and webhook export ------------------------------------------------------------------------

def test_09_imported_leads_are_not_messaged_and_flow_out_through_export_and_webhook(client, db, fake_wa, fake_llm, http):
    _, owner = make_user(db, "owner")
    _, mgr = make_user(db, "manager")
    client.patch(f"{API}/settings", headers=owner, json={"webhook_export_url": "http://localhost:9000/hook"})
    csv_text = "Name,Phone,Email\nRavi,98765 43210,ravi@example.com\nAsha,,asha@example.com\n"
    d = upload(client, mgr, csv_text.encode()).json()["data"]
    assert d["created"] == 2 and fake_wa.sent == [] and fake_llm.calls == []
    exported = client.get(f"{API}/export/leads", headers=mgr).text
    assert "Ravi" in exported and "Asha" in exported
    assert webhook_export.deliver_outbox()["sent"] == 2
    assert sorted(c["body"]["lead"]["name"] for c in http.calls) == ["Asha", "Ravi"]


# 10. Roles and the daily report ----------------------------------------------------------------------------------

def test_10_each_role_sees_only_what_it_should_and_the_daily_report_counts_are_right(client, db, fake_wa, fake_llm, mailer):
    owner_p, owner = make_user(db, "owner")
    _, admin = make_user(db, "admin")
    mgr_p, mgr = make_user(db, "manager")
    sales_p, sales = make_user(db, "sales")
    other_p, other = make_user(db, "sales")
    fake_llm.script = hot_script()
    inbound(client, "wamid.R1", "LED signboard, budget 50,000, within 3 weeks")
    inbound(client, "wamid.R2", "Hi there", sender="919811112222")
    ids = [l["id"] for l in client.get(f"{API}/leads", headers=mgr).json()["data"]]
    assert len(ids) == 2
    client.post(f"{API}/leads/{ids[0]}/assign", headers=mgr, json={"profile_id": str(sales_p.id)})

    assert client.get(f"{API}/leads", headers=admin).status_code == 403
    assert [l["id"] for l in client.get(f"{API}/leads", headers=sales).json()["data"]] == [ids[0]]
    assert client.get(f"{API}/leads", headers=other).json()["data"] == []
    assert client.get(f"{API}/leads/{ids[0]}", headers=other).status_code == 404
    assert client.get(f"{API}/settings", headers=mgr).status_code == 403
    assert client.get(f"{API}/settings", headers=admin).status_code == 200
    assert client.get(f"{API}/analytics/summary", headers=sales).status_code == 403

    s = client.get(f"{API}/analytics/summary", headers=owner).json()["data"]
    assert s["new_leads"] == 2 and s["hot_leads"] == 1
    assert client.post(f"{API}/reports/daily/send-now", headers=owner).json()["data"]["sent"] is True
    assert "New leads (last 24 hours): 2" in mailer.internal[0]["body"] and "Hot leads open now: 1" in mailer.internal[0]["body"]
