"""Day 2 acceptance checks from the build plan."""
import hashlib
import hmac
import json
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

import pytest
from sqlalchemy import select

from app.channels import email as em
from app.config import get_settings
from app.models import AiRun, Conversation, Followup, Handoff, Lead, Message, Notification, OptOut, Quotation
from app.services import followups
from app.services.leads import as_utc
from tests.conftest import make_user, text, tool, wa_payload

KEY = {"X-API-Key": "dev-lead-key"}
IST = ZoneInfo("Asia/Kolkata")


class FakeEmail:
    def __init__(self):
        self.sent = []

    def send(self, to, subject, body, unsub_url, in_reply_to=None):
        self.sent.append({"to": to, "subject": subject, "body": body, "unsub": unsub_url, "in_reply_to": in_reply_to})
        return f"email-{len(self.sent)}"


@pytest.fixture
def fake_email(monkeypatch):
    f = FakeEmail()
    monkeypatch.setattr(em, "email_client", f)
    return f


def with_phone(db, role, phone, name="Team"):
    p, h = make_user(db, role, name)
    p.phone_e164 = phone
    db.commit()
    return p, h


def seed_catalog(client, owner_headers):
    r = client.post("/api/v1/products", headers=owner_headers, json={"name": "LED Signboard", "attributes": {"width": "number", "height": "number"}})
    assert r.status_code == 201, r.text
    pid = r.json()["data"]["id"]
    r = client.post(f"/api/v1/products/{pid}/rules", headers=owner_headers, json={"rule_type": "formula", "min_qty": 1, "formula": "width * height * 450"})
    assert r.status_code == 201, r.text
    r = client.post("/api/v1/products", headers=owner_headers, json={"name": "Nameplate"})
    client.post(f"/api/v1/products/{r.json()['data']['id']}/rules", headers=owner_headers, json={"rule_type": "per_unit", "min_qty": 1, "amount": "200"})


def inbound(client, msg_id, body, sender="919876543210"):
    r = client.post("/api/v1/webhooks/whatsapp", json=wa_payload(msg_id, sender, body))
    assert r.status_code == 200


def window_time(dt):
    """The next 11:00 Asia/Kolkata at or after dt, inside the default 9-19 send window."""
    local = dt.astimezone(IST).replace(hour=11, minute=0, second=0, microsecond=0)
    if local < dt:
        local += timedelta(days=1)
    return local.astimezone(timezone.utc)


def only_lead(db):
    db.expire_all()
    return db.scalar(select(Lead))


def sent_texts(fake_wa, to):
    return [b for t, b in fake_wa.sent if t == to]


# 1. Qualifying flow: budget + timeline = Hot, salesperson gets a WhatsApp alert with a summary ----------

def hot_script():
    return [
        tool("update_lead", {"name": "Ravi Shah", "requirement": "LED signboard", "budget_text": "INR 50,000", "timeline_text": "within 3 weeks"}),
        tool("set_grade", {"grade": "hot", "reason": "Gave budget and timeline", "signals": ["budget_timeline"]}, "tu_2"),
        text("Thank you Ravi. A team member will contact you shortly."),
    ]


def test_budget_and_timeline_make_a_hot_lead_and_alert_the_assigned_salesperson(client, db, fake_wa, fake_llm):
    _, mgr = with_phone(db, "manager", "+919000000010", "Mina")
    sales, _ = with_phone(db, "sales", "+919000000011", "Sam")
    fake_llm.script = [text("Hello, how can I help?")]
    inbound(client, "wamid.H0", "Hi")
    lead = only_lead(db)
    assert client.post(f"/api/v1/leads/{lead.id}/assign", headers=mgr, json={"profile_id": str(sales.id)}).status_code == 200
    fake_wa.sent.clear()

    fake_llm.script = hot_script()
    inbound(client, "wamid.H1", "I need an LED signboard, budget 50,000, within 3 weeks")

    lead = only_lead(db)
    assert lead.grade == "hot" and lead.score_signals == ["budget_timeline"] and lead.stage == "qualified"
    alerts = sent_texts(fake_wa, "+919000000011")
    assert len(alerts) == 1 and "Hot lead: Ravi Shah" in alerts[0]
    assert "INR 50,000" in alerts[0] and "within 3 weeks" in alerts[0] and "LED signboard" in alerts[0]
    assert not sent_texts(fake_wa, "+919000000010")  # the assigned person only, not the manager
    note = db.scalar(select(Notification).where(Notification.kind == "hot_lead"))
    assert note.whatsapp_status == "sent" and str(note.profile_id) == str(sales.id)


def test_unassigned_hot_lead_alerts_owners_and_managers(client, db, fake_wa, fake_llm):
    with_phone(db, "manager", "+919000000010")
    with_phone(db, "owner", "+919000000012")
    fake_llm.script = hot_script()
    inbound(client, "wamid.H2", "I need a signboard, budget 50,000, within 3 weeks")
    assert any("Hot lead" in b for b in sent_texts(fake_wa, "+919000000010"))
    assert any("Hot lead" in b for b in sent_texts(fake_wa, "+919000000012"))


def test_hot_is_downgraded_when_the_stored_data_does_not_support_it(client, db, fake_wa, fake_llm):
    with_phone(db, "manager", "+919000000010")
    fake_llm.script = [
        tool("set_grade", {"grade": "hot", "reason": "Seems keen", "signals": ["budget_timeline", "fast_replies"]}),
        text("Thank you. What is your budget?"),
    ]
    inbound(client, "wamid.H3", "Tell me more")
    lead = only_lead(db)
    assert lead.grade == "warm" and lead.score_signals == ["fast_replies"]  # budget_timeline dropped: nothing stored
    assert not db.scalars(select(Notification).where(Notification.kind == "hot_lead")).all()
    assert not sent_texts(fake_wa, "+919000000010")


def test_hot_alert_fires_once_not_on_every_regrade(client, db, fake_wa, fake_llm):
    with_phone(db, "manager", "+919000000010")
    fake_llm.script = hot_script()
    inbound(client, "wamid.H4", "budget 50,000 within 3 weeks")
    fake_llm.script = [tool("set_grade", {"grade": "hot", "reason": "Still keen", "signals": ["budget_timeline"]}), text("Noted.")]
    inbound(client, "wamid.H5", "Yes still interested")
    assert len(db.scalars(select(Notification).where(Notification.kind == "hot_lead")).all()) == 1


# 2. Follow-ups on day 1, 3, 7 and never after a reply or STOP ------------------------------------------

def start_silent_lead(client, db, fake_llm, fake_wa):
    fake_llm.script = [text("Good morning Ravi. Thank you for your enquiry. What do you need?")]
    r = client.post("/api/v1/public/leads", headers=KEY, json={"name": "Ravi", "phone": "+919876543210", "requirement": "signboard"})
    assert r.status_code == 201
    assert len(fake_wa.sent) == 1
    db.expire_all()
    rows = db.scalars(select(Followup).order_by(Followup.step)).all()
    assert [(f.step, f.day_offset, f.status) for f in rows] == [(1, 1, "scheduled"), (2, 3, "scheduled"), (3, 7, "scheduled")]
    return as_utc(rows[0].due_at) - timedelta(days=1)  # the base time


def test_silent_lead_gets_followups_on_day_1_3_7_then_goes_cold(client, db, fake_wa, fake_llm):
    base = start_silent_lead(client, db, fake_llm, fake_wa)
    fake_llm.script = []
    assert followups.run_due_followups(window_time(base)) == []  # nothing is due on day 0
    assert len(fake_wa.sent) == 1

    for step, days in ((1, 1), (2, 3), (3, 7)):
        res = followups.run_due_followups(window_time(base + timedelta(days=days)))
        assert [(r["step"], r["status"]) for r in res] == [(step, "sent")]
        assert len(fake_wa.sent) == 1 + step
    assert followups.run_due_followups(window_time(base + timedelta(days=30))) == []
    assert len(fake_wa.sent) == 4
    lead = only_lead(db)
    assert lead.grade == "cold" and "No reply after 3 follow-ups" in lead.grade_reason and lead.next_followup_at is None
    kinds = [r.trigger for r in db.scalars(select(AiRun)).all()]
    assert kinds.count("followup") == 3


def test_a_reply_cancels_the_old_schedule_and_starts_a_new_one(client, db, fake_wa, fake_llm):
    base = start_silent_lead(client, db, fake_llm, fake_wa)
    followups.run_due_followups(window_time(base + timedelta(days=1)))
    assert len(fake_wa.sent) == 2
    fake_llm.script = [text("Thank you, noted.")]
    inbound(client, "wamid.F1", "Yes I am interested, send details")
    assert len(fake_wa.sent) == 3
    reply_time = datetime.now(timezone.utc)
    db.expire_all()
    rows = db.scalars(select(Followup).order_by(Followup.created_at, Followup.step)).all()
    old_pending = [f for f in rows if f.skip_reason in ("replied", "rescheduled")]
    assert [f.step for f in old_pending] == [2, 3]  # the original day-3 and day-7 follow-ups are void
    fresh = [f for f in rows if f.status == "scheduled"]
    assert [f.day_offset for f in fresh] == [1, 3, 7] and all(as_utc(f.due_at) > reply_time for f in fresh)
    # Counted from the reply: nothing is due for the rest of that day, then exactly one follow-up.
    assert followups.run_due_followups(window_time(reply_time)) == []
    res = followups.run_due_followups(window_time(reply_time + timedelta(days=1)))
    assert [(r["step"], r["status"]) for r in res] == [(1, "sent")] and len(fake_wa.sent) == 4


def test_reply_voids_the_schedule_even_when_the_ai_sends_no_answer(client, db, fake_wa, fake_llm):
    base = start_silent_lead(client, db, fake_llm, fake_wa)
    fake_llm.script = [[]]  # the model produces nothing, so no new cycle is started
    inbound(client, "wamid.F3", "ok")
    db.expire_all()
    assert all(f.status == "skipped" and f.skip_reason == "replied" for f in db.scalars(select(Followup)).all())
    assert followups.run_due_followups(window_time(base + timedelta(days=30))) == []
    assert len(fake_wa.sent) == 1


def test_due_followup_is_skipped_if_the_lead_replied_after_it_was_scheduled(client, db, fake_wa, fake_llm):
    """Second line of defence: even a row that was never cancelled checks for a reply when it comes due."""
    base = start_silent_lead(client, db, fake_llm, fake_wa)
    lead = only_lead(db)
    lead.last_inbound_at = datetime.now(timezone.utc) + timedelta(seconds=5)
    db.commit()
    res = followups.run_due_followups(window_time(base + timedelta(days=1)))
    assert res and res[0]["status"] == "skipped" and res[0]["reason"] == "replied"
    assert len(fake_wa.sent) == 1


def test_no_followups_are_scheduled_for_takeover_paused_or_closed_leads(client, db, fake_wa, fake_llm):
    lead = Lead(name="X", phone_e164="+919811111111")
    db.add(lead)
    db.commit()
    for change in ({"human_takeover": True}, {"ai_paused": True}, {"stage": "won"}, {"stage": "lost"}, {"stage": "booked"}):
        for k, v in change.items():
            setattr(lead, k, v)
        followups.schedule(db, lead)
        db.commit()
        assert db.scalars(select(Followup).where(Followup.status == "scheduled")).all() == [], change
        lead.human_takeover, lead.ai_paused, lead.stage = False, False, "new"


def test_reply_while_ai_is_paused_still_skips_followups(client, db, fake_wa, fake_llm):
    base = start_silent_lead(client, db, fake_llm, fake_wa)
    _, mgr = make_user(db, "manager")
    lead = only_lead(db)
    inbound(client, "wamid.F2", "hello")  # AI replies, schedule restarts
    client.post(f"/api/v1/leads/{lead.id}/ai-pause", headers=mgr, json={"paused": True})
    sent_before = len(fake_wa.sent)
    assert followups.run_due_followups(window_time(base + timedelta(days=30))) == []
    assert len(fake_wa.sent) == sent_before


def test_stop_ends_all_followups(client, db, fake_wa, fake_llm):
    base = start_silent_lead(client, db, fake_llm, fake_wa)
    inbound(client, "wamid.S1", "STOP")
    assert "unsubscribed" in fake_wa.sent[-1][1]
    sent_before = len(fake_wa.sent)
    assert followups.run_due_followups(window_time(base + timedelta(days=30))) == []
    assert len(fake_wa.sent) == sent_before
    db.expire_all()
    assert all(f.status == "skipped" for f in db.scalars(select(Followup)).all())


def test_followups_wait_for_the_send_window_and_for_won_or_open_handoff_leads(client, db, fake_wa, fake_llm):
    base = start_silent_lead(client, db, fake_llm, fake_wa)
    night = (base + timedelta(days=1)).astimezone(IST).replace(hour=3, minute=0).astimezone(timezone.utc)
    night = night if night >= base + timedelta(days=1) else night + timedelta(days=1)
    assert followups.run_due_followups(night) == []  # 3 a.m. IST: stays scheduled
    db.expire_all()
    assert db.scalar(select(Followup).where(Followup.step == 1)).status == "scheduled"

    lead = only_lead(db)
    db.add(Handoff(lead_id=lead.id, reason="discount question"))
    db.commit()
    res = followups.run_due_followups(window_time(base + timedelta(days=1)))
    assert res and res[0]["status"] == "skipped" and res[0]["reason"] == "handoff_open"


# 3. A quotation is never sent without approval ------------------------------------------------------

def quote_script(final="Thank you. A team member will send the quotation shortly."):
    return [
        tool("draft_quotation", {
            "product": "LED Signboard", "quantity": 3, "attributes": {"width": 4, "height": 2},
            "cover_message": "Thank you for your interest. Please find the quotation below.",
        }),
        text(final),
    ]


def test_quotation_is_held_until_a_manager_approves_it(client, db, fake_wa, fake_llm):
    _, owner = make_user(db, "owner")
    _, mgr = with_phone(db, "manager", "+919000000010")
    _, sales = make_user(db, "sales")
    seed_catalog(client, owner)
    fake_llm.script = quote_script()
    inbound(client, "wamid.Q1", "Please send a quotation for 3 LED signboards, 4 by 2")

    # Only the holding reply went out. No figures, no quotation message.
    assert [b for b in sent_texts(fake_wa, "+919876543210") if "Quotation:" in b or "10,800" in b] == []
    assert sent_texts(fake_wa, "+919876543210") == ["Thank you. A team member will send the quotation shortly."]
    q = db.scalar(select(Quotation))
    assert q.status == "pending" and q.total == "10800.00" and q.unit_price == "3600.00" and q.message_id is None
    assert any("Quotation to approve" in b for b in sent_texts(fake_wa, "+919000000010"))
    assert not db.scalars(select(Message).where(Message.is_quote.is_(True))).all()

    # A salesperson cannot approve; a manager can.
    assert client.post(f"/api/v1/quotations/{q.id}/approve", headers=sales, json={}).status_code == 403
    assert len(sent_texts(fake_wa, "+919876543210")) == 1
    r = client.post(f"/api/v1/quotations/{q.id}/approve", headers=mgr, json={})
    assert r.status_code == 200 and r.json()["data"]["status"] == "sent"
    final = sent_texts(fake_wa, "+919876543210")[-1]
    assert "Please find the quotation below." in final and "INR 3,600" in final and "INR 10,800" in final
    db.expire_all()
    msg = db.scalar(select(Message).where(Message.is_quote.is_(True)))
    assert msg.status == "sent" and msg.approved_by is not None and msg.sender == "human"
    # Approving twice does not send twice.
    assert client.post(f"/api/v1/quotations/{q.id}/approve", headers=mgr, json={}).status_code == 409
    assert len(sent_texts(fake_wa, "+919876543210")) == 2


def test_rejected_quotation_is_never_sent(client, db, fake_wa, fake_llm):
    _, owner = make_user(db, "owner")
    seed_catalog(client, owner)
    fake_llm.script = quote_script()
    inbound(client, "wamid.Q2", "quotation please for 3 LED signboards")
    q = db.scalar(select(Quotation))
    r = client.post(f"/api/v1/quotations/{q.id}/reject", headers=owner, json={"reason": "Wrong size"})
    assert r.status_code == 200 and r.json()["data"]["status"] == "rejected"
    assert client.post(f"/api/v1/quotations/{q.id}/approve", headers=owner, json={}).status_code == 409
    assert len(sent_texts(fake_wa, "+919876543210")) == 1


def test_ai_cannot_state_the_quotation_amount_before_approval(client, db, fake_wa, fake_llm):
    _, owner = make_user(db, "owner")
    seed_catalog(client, owner)
    fake_llm.script = quote_script(final="Your quotation total is INR 10,800.")
    fake_llm.script.append(text("A team member will send the quotation shortly."))
    inbound(client, "wamid.Q3", "quotation for 3 LED signboards 4 by 2")
    assert sent_texts(fake_wa, "+919876543210") == ["A team member will send the quotation shortly."]
    rerun_note = fake_llm.calls[-1]["messages"][-1]["content"]
    assert "draft was blocked" in rerun_note and "approves" in rerun_note
    run = db.scalar(select(AiRun))
    assert run.guardrail_flags["regenerated"] is True and run.decision == "reply"


def test_quotation_price_comes_from_rules_not_from_the_model(client, db, fake_wa, fake_llm):
    _, owner = make_user(db, "owner")
    seed_catalog(client, owner)
    fake_llm.script = [
        tool("draft_quotation", {"product": "Unknown Thing", "quantity": 1, "cover_message": "Here is your quotation."}),
        text("A team member will confirm the price shortly."),
    ]
    inbound(client, "wamid.Q4", "quotation for an unknown thing")
    assert db.scalar(select(Quotation)) is None  # no rule, no quotation
    tool_msg = fake_llm.calls[1]["messages"][-1]["content"][0]["content"]
    assert "error" in tool_msg and "hand_off_to_human" in tool_msg


# 4. Discounts and delivery dates are never invented ------------------------------------------------

def test_discount_reply_is_rewritten_once(client, db, fake_wa, fake_llm):
    fake_llm.script = [text("Yes, I can give you a 10% discount."), text("A team member will discuss any discount request with you.")]
    inbound(client, "wamid.D1", "Can I get a discount?")
    assert sent_texts(fake_wa, "+919876543210") == ["A team member will discuss any discount request with you."]
    assert "discount" in fake_llm.calls[-1]["messages"][-1]["content"].lower()


def test_delivery_date_reply_is_rewritten_once(client, db, fake_wa, fake_llm):
    fake_llm.script = [text("We will deliver in 5 days."), text("A team member will confirm the delivery date for you.")]
    inbound(client, "wamid.D2", "How soon can you deliver?")
    assert sent_texts(fake_wa, "+919876543210") == ["A team member will confirm the delivery date for you."]


def test_second_guardrail_failure_sends_nothing_and_hands_off(client, db, fake_wa, fake_llm):
    _, mgr = with_phone(db, "manager", "+919000000010")
    fake_llm.script = [text("I can offer a 10% discount."), text("Fine, 15% discount it is.")]
    inbound(client, "wamid.D3", "Any discount?")
    assert sent_texts(fake_wa, "+919876543210") == []  # the customer received nothing
    h = db.scalar(select(Handoff))
    assert "guardrail failure" in h.reason and h.status == "open"
    run = db.scalar(select(AiRun))
    assert run.decision == "guardrail_failure" and len(run.guardrail_flags["violations"]) == 2
    assert any("guardrail failure" in b for b in sent_texts(fake_wa, "+919000000010"))


def test_ai_hands_off_a_discount_request_with_summary_and_alert(client, db, fake_wa, fake_llm):
    _, mgr = with_phone(db, "manager", "+919000000010")
    fake_llm.script = [
        tool("update_lead", {"name": "Ravi Shah", "requirement": "LED signboard"}),
        tool("hand_off_to_human", {"reason": "Customer asked for a discount"}, "tu_2"),
        text("A team member will discuss that with you."),
    ]
    inbound(client, "wamid.D4", "Can you give me a discount on 3 boards?")
    h = db.scalar(select(Handoff))
    assert h.reason == "Customer asked for a discount" and "Ravi Shah" in h.summary and "discount on 3 boards" in h.summary
    assert only_lead(db).stage == "handoff"
    alert = sent_texts(fake_wa, "+919000000010")[0]
    assert "Needs a human" in alert and "Ravi Shah" in alert
    # a second request does not open a second handoff
    fake_llm.script = [tool("hand_off_to_human", {"reason": "Asked again"}), text("A team member will follow up.")]
    inbound(client, "wamid.D5", "Any update on the discount?")
    assert len(db.scalars(select(Handoff)).all()) == 1


def test_ai_discloses_it_is_an_ai_when_asked(client, db, fake_wa, fake_llm):
    fake_llm.script = [text("Of course, I am here to help."), text("I am an AI assistant for Acme Signs. I can connect you with a team member.")]
    inbound(client, "wamid.D6", "Am I talking to a real person?")
    assert "AI assistant" in sent_texts(fake_wa, "+919876543210")[0]


def test_stop_contact_tool_stops_everything_without_a_reply(client, db, fake_wa, fake_llm):
    fake_llm.script = [tool("stop_contact", {"reason": "asked not to be contacted"}), text("Sure, goodbye!")]
    inbound(client, "wamid.D7", "Please do not message me again")
    lead = only_lead(db)
    assert lead.opted_out_at is not None and lead.stage == "stopped"
    assert all("goodbye" not in b for _, b in fake_wa.sent)
    inbound(client, "wamid.D8", "hello?")
    assert len([1 for _, b in fake_wa.sent if "unsubscribed" not in b]) == 0


# 5. After takeover the AI only drafts; nothing is sent until approved --------------------------------

def setup_takeover(client, db, fake_llm):
    _, mgr = with_phone(db, "manager", "+919000000010")
    fake_llm.script = [text("Hello, how can I help?")]
    inbound(client, "wamid.T0", "Hi")
    lead = only_lead(db)
    r = client.post(f"/api/v1/leads/{lead.id}/takeover", headers=mgr)
    assert r.status_code == 200 and r.json()["data"]["human_takeover"] is True
    return lead, mgr


def test_after_takeover_ai_output_is_a_draft_until_approved(client, db, fake_wa, fake_llm):
    lead, mgr = setup_takeover(client, db, fake_llm)
    fake_wa.sent.clear()
    fake_llm.script = [text("Our showroom opens at 10am. Would you like to visit?")]
    inbound(client, "wamid.T1", "When are you open?")
    assert sent_texts(fake_wa, "+919876543210") == []  # nothing sent
    db.expire_all()
    draft = db.scalar(select(Message).where(Message.status == "pending_approval"))
    assert draft.sender == "ai" and "showroom" in draft.body and draft.approved_by is None
    queue = client.get("/api/v1/approvals", headers=mgr).json()["data"]
    assert [d["id"] for d in queue["drafts"]] == [str(draft.id)]
    assert any("AI draft waiting" in b for b in sent_texts(fake_wa, "+919000000010"))

    r = client.post(f"/api/v1/messages/{draft.id}/approve", headers=mgr, json={})
    assert r.status_code == 200 and r.json()["data"]["status"] == "sent"
    assert sent_texts(fake_wa, "+919876543210") == ["Our showroom opens at 10am. Would you like to visit?"]
    assert client.post(f"/api/v1/messages/{draft.id}/approve", headers=mgr, json={}).status_code == 409
    assert len(sent_texts(fake_wa, "+919876543210")) == 1


def test_draft_can_be_edited_before_sending_or_rejected(client, db, fake_wa, fake_llm):
    lead, mgr = setup_takeover(client, db, fake_llm)
    fake_wa.sent.clear()
    fake_llm.script = [text("Draft one."), text("Draft two.")]
    inbound(client, "wamid.T2", "first question")
    inbound(client, "wamid.T3", "second question")
    drafts = db.scalars(select(Message).where(Message.status == "pending_approval").order_by(Message.created_at)).all()
    assert len(drafts) == 2
    r = client.post(f"/api/v1/messages/{drafts[0].id}/approve", headers=mgr, json={"text": "Edited by a person."})
    assert r.json()["data"]["sender"] == "human"
    r = client.post(f"/api/v1/messages/{drafts[1].id}/reject", headers=mgr)
    assert r.json()["data"]["status"] == "rejected"
    assert sent_texts(fake_wa, "+919876543210") == ["Edited by a person."]
    assert client.post(f"/api/v1/messages/{drafts[1].id}/approve", headers=mgr, json={}).status_code == 409


def test_drafts_still_obey_the_guardrails_and_no_followups_run_in_takeover(client, db, fake_wa, fake_llm):
    lead, mgr = setup_takeover(client, db, fake_llm)
    fake_wa.sent.clear()
    fake_llm.script = [text("I can offer a discount."), text("I can offer a discount today.")]
    inbound(client, "wamid.T4", "discount?")
    assert not db.scalars(select(Message).where(Message.status == "pending_approval")).all()
    assert db.scalar(select(Handoff)) is not None
    assert followups.run_due_followups(datetime.now(timezone.utc) + timedelta(days=30)) == []
    assert fake_wa.sent == [] or all(t == "+919000000010" for t, _ in fake_wa.sent)


def test_paused_ai_neither_replies_nor_drafts(client, db, fake_wa, fake_llm):
    _, mgr = with_phone(db, "manager", "+919000000010")
    fake_llm.script = [text("Hello.")]
    inbound(client, "wamid.P0", "Hi")
    lead = only_lead(db)
    client.post(f"/api/v1/leads/{lead.id}/ai-pause", headers=mgr, json={"paused": True})
    calls = len(fake_llm.calls)
    inbound(client, "wamid.P1", "anyone there?")
    assert len(fake_llm.calls) == calls
    assert not db.scalars(select(Message).where(Message.status == "pending_approval")).all()


def test_human_can_send_directly_but_not_to_an_opted_out_lead(client, db, fake_wa, fake_llm):
    lead, mgr = setup_takeover(client, db, fake_llm)
    fake_wa.sent.clear()
    r = client.post(f"/api/v1/leads/{lead.id}/send", headers=mgr, json={"text": "Hi, this is Mina from the team."})
    assert r.status_code == 201 and r.json()["data"]["sender"] == "human" and r.json()["data"]["status"] == "sent"
    assert sent_texts(fake_wa, "+919876543210") == ["Hi, this is Mina from the team."]
    inbound(client, "wamid.T9", "STOP")
    assert client.post(f"/api/v1/leads/{lead.id}/send", headers=mgr, json={"text": "One more thing"}).status_code == 409


def test_sales_role_only_sees_and_acts_on_assigned_leads(client, db, fake_wa, fake_llm):
    lead, mgr = setup_takeover(client, db, fake_llm)
    other, other_h = make_user(db, "sales", "Other")
    mine, mine_h = make_user(db, "sales", "Mine")
    fake_llm.script = [text("A draft.")]
    inbound(client, "wamid.R1", "question")
    draft = db.scalar(select(Message).where(Message.status == "pending_approval"))
    assert client.post(f"/api/v1/messages/{draft.id}/approve", headers=other_h, json={}).status_code == 404
    assert client.post(f"/api/v1/leads/{lead.id}/takeover", headers=other_h).status_code == 404
    client.post(f"/api/v1/leads/{lead.id}/assign", headers=mgr, json={"profile_id": str(mine.id)})
    assert client.post(f"/api/v1/messages/{draft.id}/approve", headers=mine_h, json={}).status_code == 200
    assert client.post(f"/api/v1/leads/{lead.id}/assign", headers=mine_h, json={"profile_id": str(mine.id)}).status_code == 403


# 6. Email channel ------------------------------------------------------------------------------------

def sign(body: bytes, secret: str) -> str:
    return hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()


def email_in(client, event_id, body, sender="asha@example.com", subject="Signboard enquiry", message_id=None, headers=None):
    payload = {"event_id": event_id, "from_email": sender, "from_name": "Asha", "subject": subject, "text": body, "message_id": message_id or f"<{event_id}@mail>"}
    return client.post("/api/v1/webhooks/email/inbound", json=payload, headers=headers or {})


def test_email_enquiry_gets_an_email_with_unsubscribe_link(client, db, fake_email, fake_wa, fake_llm):
    fake_llm.script = [text("Good morning Asha. Thank you for your enquiry. What size of board do you need?")]
    r = client.post("/api/v1/public/leads", headers=KEY, json={"name": "Asha", "email": "Asha@Example.com", "requirement": "board"})
    assert r.status_code == 201 and r.json()["data"]["first_reply"] == "queued"
    assert len(fake_email.sent) == 1 and fake_wa.sent == []
    sent = fake_email.sent[0]
    assert sent["to"] == "asha@example.com" and sent["subject"] == "Your enquiry with Acme Signs"
    assert "/api/v1/public/unsubscribe?token=" in sent["unsub"]
    lead = only_lead(db)
    assert em.read_unsubscribe_token(sent["unsub"].split("token=")[1]) == str(lead.id)
    assert db.scalar(select(Followup)) is not None  # follow-ups apply to email too


def test_inbound_email_is_threaded_and_answered(client, db, fake_email, fake_wa, fake_llm):
    fake_llm.script = [text("First email.")]
    client.post("/api/v1/public/leads", headers=KEY, json={"name": "Asha", "email": "asha@example.com"})
    fake_llm.script = [text("Thank you for the details. We make LED boards.")]
    body = "I need a 4x2 board.\n\nOn Mon, 3 Nov 2026, Acme wrote:\n> Good morning Asha"
    r = email_in(client, "evt1", body, subject="Re: Your enquiry with Acme Signs", message_id="<abc123@mail.example.com>")
    assert r.status_code == 200
    db.expire_all()
    inbound_msg = db.scalar(select(Message).where(Message.direction == "inbound"))
    assert inbound_msg.body == "I need a 4x2 board."  # quoted thread stripped
    assert len(db.scalars(select(Conversation)).all()) == 1  # same thread as the first outreach
    reply = fake_email.sent[-1]
    assert reply["in_reply_to"] == "<abc123@mail.example.com>" and reply["subject"] == "Re: Your enquiry with Acme Signs"
    # duplicate delivery of the same event does nothing
    assert email_in(client, "evt1", body).json().get("duplicate") is True
    assert len(fake_email.sent) == 2


def test_unsubscribe_link_stops_all_contact(client, db, fake_email, fake_wa, fake_llm):
    fake_llm.script = [text("First email.")]
    client.post("/api/v1/public/leads", headers=KEY, json={"name": "Asha", "email": "asha@example.com", "phone": "+919876543210"})
    lead = only_lead(db)
    token = em.make_unsubscribe_token(lead.id)
    assert client.get("/api/v1/public/unsubscribe", params={"token": "garbage"}).status_code == 400
    r = client.get("/api/v1/public/unsubscribe", params={"token": token})
    assert r.status_code == 200 and "unsubscribed" in r.text
    lead = only_lead(db)
    assert lead.opted_out_at is not None and lead.stage == "stopped"
    assert db.scalar(select(OptOut).where(OptOut.source == "unsubscribe_link")) is not None
    before = (len(fake_email.sent), len(fake_wa.sent))
    email_in(client, "evt2", "Actually I want to talk")
    inbound(client, "wamid.U1", "hello again")
    assert (len(fake_email.sent), len(fake_wa.sent)) == before
    assert client.post("/api/v1/public/unsubscribe", params={"token": token}).status_code == 200  # one-click is idempotent


def test_bounce_blocks_email_only(client, db, fake_email, fake_wa, fake_llm):
    fake_llm.script = [text("Hello on WhatsApp.")]
    client.post("/api/v1/public/leads", headers=KEY, json={"name": "Asha", "email": "asha@example.com", "phone": "+919876543210"})
    r = client.post("/api/v1/webhooks/email/inbound", json={"type": "bounce", "event_id": "b1", "email": "Asha@example.com"})
    assert r.status_code == 200
    assert db.scalar(select(OptOut).where(OptOut.source == "bounce")) is not None
    lead = only_lead(db)
    from app.services.leads import is_opted_out

    assert is_opted_out(db, lead, "email") is True and is_opted_out(db, lead, "whatsapp") is False
    fake_llm.script = [text("Still here on WhatsApp.")]
    inbound(client, "wamid.B1", "hi")
    assert "Still here on WhatsApp." in sent_texts(fake_wa, "+919876543210")
    assert fake_email.sent == []


def test_email_webhook_signature_is_enforced_when_a_secret_is_set(client, db, fake_email, fake_llm):
    s = get_settings()
    s.email_webhook_secret = "topsecret"
    try:
        payload = {"event_id": "sig1", "from_email": "asha@example.com", "text": "hello", "subject": "Hi"}
        raw = json.dumps(payload).encode()
        bad = client.post("/api/v1/webhooks/email/inbound", content=raw, headers={"x-email-signature": "nope"})
        assert bad.status_code == 403
        assert client.post("/api/v1/webhooks/email/inbound", content=raw).status_code == 403
        good = client.post("/api/v1/webhooks/email/inbound", content=raw, headers={"x-email-signature": sign(raw, "topsecret")})
        assert good.status_code == 200 and len(fake_email.sent) == 1
    finally:
        s.email_webhook_secret = ""


def test_quoted_reply_stripping():
    assert em.strip_quoted("Yes please.\n> old text\nmore") == "Yes please."
    assert em.strip_quoted("Sounds good\n\n-----Original Message-----\nFrom: x") == "Sounds good"
    assert em.parse_inbound({"event_id": "e", "from_email": "a@b.co", "text": "> only a quote"}) is None


# 7. Catalogue API ---------------------------------------------------------------------------------------

def test_product_and_rule_validation_and_roles(client, db):
    _, owner = make_user(db, "owner")
    _, sales = make_user(db, "sales")
    assert client.post("/api/v1/products", headers=sales, json={"name": "X"}).status_code == 403
    pid = client.post("/api/v1/products", headers=owner, json={"name": "Board", "attributes": {"w": "number"}}).json()["data"]["id"]
    assert client.post("/api/v1/products", headers=owner, json={"name": "Board"}).status_code == 409
    for bad in (
        {"rule_type": "formula", "formula": "w.__class__"},
        {"rule_type": "formula", "formula": "zzz * 2"},
        {"rule_type": "formula", "formula": "2 ** 100"},
        {"rule_type": "per_unit", "amount": "abc"},
        {"rule_type": "tiered", "tiers": [{"up_to": 5}]},
        {"rule_type": "fixed", "amount": "10", "min_qty": 5, "max_qty": 1},
    ):
        r = client.post(f"/api/v1/products/{pid}/rules", headers=owner, json=bad)
        assert r.status_code == 422, bad
    ok = client.post(f"/api/v1/products/{pid}/rules", headers=owner, json={"rule_type": "formula", "formula": "w * 100"})
    assert ok.status_code == 201
    r = client.post("/api/v1/price/test", headers=sales, json={"product": "Board", "quantity": 2, "attributes": {"w": 3}})
    assert r.status_code == 200 and r.json()["data"]["total_text"] == "INR 600"
    assert client.post("/api/v1/price/test", headers=sales, json={"product": "Nope", "quantity": 1}).status_code == 422
    client.delete(f"/api/v1/price-rules/{ok.json()['data']['id']}", headers=owner)
    assert client.post("/api/v1/price/test", headers=sales, json={"product": "Board", "quantity": 1, "attributes": {"w": 3}}).status_code == 422


def test_notifications_are_listed_and_can_be_marked_read(client, db, fake_wa, fake_llm):
    mgr_p, mgr = with_phone(db, "manager", "+919000000010")
    fake_llm.script = hot_script()
    inbound(client, "wamid.N1", "budget 50,000 within 3 weeks")
    rows = client.get("/api/v1/notifications", headers=mgr).json()["data"]
    assert rows and rows[0]["kind"] == "hot_lead" and rows[0]["read"] is False
    assert client.post(f"/api/v1/notifications/{rows[0]['id']}/read", headers=mgr).json()["data"]["read"] is True
    assert client.get("/api/v1/notifications", headers=mgr, params={"unread_only": "true"}).json()["data"] == []


# 8. If the AI cannot answer at all, a person is told ------------------------------------------------------

def test_ai_failure_hands_the_lead_to_a_human_instead_of_going_silent(client, db, fake_wa, fake_llm, monkeypatch):
    from app.agent import llm as llmmod

    class Broken:
        model = "broken"

        def create(self, *a, **k):
            raise RuntimeError("model unavailable")

    _, mgr = with_phone(db, "manager", "+919000000010")
    llmmod.set_llm(Broken())
    inbound(client, "wamid.E1", "Hello, I need a board")
    assert sent_texts(fake_wa, "+919876543210") == []
    h = db.scalar(select(Handoff))
    assert h is not None and "AI error" in h.reason
    assert any("Needs a human" in b for b in sent_texts(fake_wa, "+919000000010"))

    # a missing API key at startup is handled the same way
    llmmod.set_llm(None)
    monkeypatch.setattr(llmmod, "_default", None)
    monkeypatch.setattr(get_settings(), "anthropic_api_key", "")
    inbound(client, "wamid.E2", "Anyone there?", sender="919812345678")
    assert any("AI unavailable" in x.reason for x in db.scalars(select(Handoff)).all())
