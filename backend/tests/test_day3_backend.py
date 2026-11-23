"""Day 3 backend checks: lead edit, CSV, analytics, report, settings, webhook export, widget, users."""
import hashlib
import hmac
import io
import json
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import select

from app.channels import email as em
from app.models import AuditLog, Followup, Lead, Notification, Profile, Setting, WebhookOutbox
from app.services import reports, users, webhook_export
from app.services.leads import find_or_create_lead
from tests.conftest import make_user, text, tool, wa_payload

KEY = {"X-API-Key": "dev-lead-key"}


class FakeMailer:
    def __init__(self):
        self.internal = []
        self.fail = False

    def send_internal(self, to, subject, body):
        if self.fail:
            from app.channels.whatsapp import ChannelError

            raise ChannelError("smtp down")
        self.internal.append({"to": to, "subject": subject, "body": body})
        return "id"

    def send(self, *a, **k):
        return "id"


@pytest.fixture
def mailer(monkeypatch):
    m = FakeMailer()
    monkeypatch.setattr(em, "email_client", m)
    return m


def mk_lead(db, **kw):
    lead = Lead(**kw)
    db.add(lead)
    db.commit()
    return lead


# ---- lead edit ---------------------------------------------------------------------------------------

def test_manager_edits_lead_and_stage_won_cancels_followups(client, db):
    _, mgr = make_user(db, "manager", "Mina")
    lead = mk_lead(db, name="Ravi", phone_e164="+919811111111", stage="qualifying")
    from app.services import followups

    followups.schedule(db, lead)
    db.commit()
    r = client.patch(f"/api/v1/leads/{lead.id}", headers=mgr, json={"company": "Shah Traders", "tags": ["vip"], "stage": "won", "grade": "hot"})
    assert r.status_code == 200
    d = r.json()["data"]
    assert d["company"] == "Shah Traders" and d["stage"] == "won" and d["grade"] == "hot" and "Mina" in d["grade_reason"]
    db.expire_all()
    assert all(f.status == "skipped" and f.skip_reason == "stage_won" for f in db.scalars(select(Followup)).all())
    assert db.scalar(select(AuditLog).where(AuditLog.action == "lead.update")) is not None


def test_lead_edit_rules(client, db):
    _, mgr = make_user(db, "manager")
    sales, sales_h = make_user(db, "sales")
    a = mk_lead(db, name="A", phone_e164="+919811111111")
    b = mk_lead(db, name="B", phone_e164="+919822222222", email="b@example.com", assigned_to=sales.id)
    stopped = mk_lead(db, name="S", phone_e164="+919833333333", stage="stopped")
    assert client.patch(f"/api/v1/leads/{b.id}", headers=mgr, json={"phone": "+91 98111 11111"}).status_code == 409
    assert client.patch(f"/api/v1/leads/{b.id}", headers=mgr, json={"stage": "invalid"}).status_code == 422
    assert client.patch(f"/api/v1/leads/{stopped.id}", headers=mgr, json={"stage": "new"}).status_code == 409
    # sales: only own leads, only notes and tags
    assert client.patch(f"/api/v1/leads/{a.id}", headers=sales_h, json={"notes": "x"}).status_code == 404
    assert client.patch(f"/api/v1/leads/{b.id}", headers=sales_h, json={"stage": "won"}).status_code == 403
    assert client.patch(f"/api/v1/leads/{b.id}", headers=sales_h, json={"notes": "Called, will confirm Monday"}).status_code == 200


# ---- CSV import / export -------------------------------------------------------------------------------

def upload(client, headers, content, mapping=None):
    data = {"mapping": mapping} if mapping else {}
    return client.post("/api/v1/leads/import", headers=headers, files={"file": ("leads.csv", content, "text/csv")}, data=data)


def test_csv_import_maps_columns_skips_duplicates_and_sends_nothing(client, db, fake_wa, fake_llm):
    _, mgr = make_user(db, "manager")
    mk_lead(db, name="Existing", phone_e164="+919811111111")
    csv_text = "Full Name,Mobile,E-mail,Company,Remarks\nRavi,98765 43210,ravi@example.com,Shah,vip\nExisting,+91 98111 11111,,,\nNoContact,abc,,,\nAsha,,asha@example.com,,\n"
    r = upload(client, mgr, csv_text.encode())
    assert r.status_code == 200, r.text
    d = r.json()["data"]
    assert d["created"] == 2 and d["duplicates"] == 1 and d["error_count"] == 1 and d["errors"][0]["row"] == 4
    assert d["mapping"]["phone"] == "Mobile" and d["mapping"]["notes"] == "Remarks"
    db.expire_all()
    ravi = db.scalar(select(Lead).where(Lead.email == "ravi@example.com"))
    assert ravi.phone_e164 == "+919876543210" and ravi.source == "csv_import" and ravi.notes == "vip"
    assert fake_wa.sent == [] and fake_llm.calls == []  # importing never messages anyone


def test_csv_import_validation_and_roles(client, db):
    _, mgr = make_user(db, "manager")
    _, sales = make_user(db, "sales")
    assert upload(client, sales, b"phone\n+919811111111\n").status_code == 403
    assert upload(client, mgr, b"colour,size\nred,1\n").status_code == 422  # no phone or email column
    assert upload(client, mgr, b"phone\n+919811111111\n", mapping='{"phone": "nope"}').status_code == 422
    assert upload(client, mgr, b"phone\n+919811111111\n", mapping="not json").status_code == 422
    assert upload(client, mgr, b"x" * 1_100_000).status_code == 413
    ok = upload(client, mgr, b"Number\n+919811111111\n", mapping='{"phone": "Number"}')
    assert ok.json()["data"]["created"] == 1


def test_csv_export_blocks_formula_injection_and_checks_role(client, db):
    _, mgr = make_user(db, "manager")
    _, sales = make_user(db, "sales")
    mk_lead(db, name="=HYPERLINK(\"http://evil\")", phone_e164="+919811111111", requirement="+cmd", stage="qualifying", grade="hot")
    mk_lead(db, name="Calm", phone_e164="+919822222222", grade="cold")
    assert client.get("/api/v1/export/leads", headers=sales).status_code == 403
    r = client.get("/api/v1/export/leads", headers=mgr)
    assert r.status_code == 200 and r.headers["content-type"].startswith("text/csv")
    rows = r.text.splitlines()
    assert rows[0].startswith("id,name,phone")
    assert "'=HYPERLINK" in r.text and "'+cmd" in r.text
    assert len(client.get("/api/v1/export/leads", headers=mgr, params={"grade": "cold"}).text.splitlines()) == 2


# ---- analytics, stuck leads, daily report ----------------------------------------------------------------

def seed_activity(db):
    now = datetime.now(timezone.utc)
    a = mk_lead(db, name="Hot Hari", phone_e164="+919811111111", grade="hot", grade_reason="Gave budget and timeline", stage="qualified",
                first_response_seconds=20, last_inbound_at=now, last_outbound_at=now - timedelta(minutes=5), source="whatsapp")
    b = mk_lead(db, name="Quiet Qadir", phone_e164="+919822222222", grade="warm", stage="qualifying", first_response_seconds=60,
                last_outbound_at=now - timedelta(hours=30), source="website_form")
    c = mk_lead(db, name="Done Dev", phone_e164="+919833333333", stage="won", last_outbound_at=now - timedelta(hours=90), source="manual")
    d = mk_lead(db, name="Old Olga", phone_e164="+919844444444", created_at=now - timedelta(days=20), source="manual")
    return a, b, c, d


def test_analytics_summary_and_stuck_leads(client, db):
    _, mgr = make_user(db, "manager")
    _, sales = make_user(db, "sales")
    seed_activity(db)
    assert client.get("/api/v1/analytics/summary", headers=sales).status_code == 403
    s = client.get("/api/v1/analytics/summary", headers=mgr, params={"days": 7}).json()["data"]
    assert s["new_leads"] == 3 and s["hot_leads"] == 1 and s["leads_replied"] == 1
    assert s["avg_first_reply_seconds"] == 40 and s["by_source"] == {"whatsapp": 1, "website_form": 1, "manual": 1}
    assert s["stuck_leads"] == 1
    stuck = client.get("/api/v1/analytics/stuck-leads", headers=mgr).json()["data"]
    assert [x["name"] for x in stuck] == ["Quiet Qadir"] and stuck[0]["hours_waiting"] >= 29  # won and replied leads are not stuck


def test_stuck_alert_is_sent_once_per_silence(db):
    mgr, _ = make_user(db, "manager")
    seed_activity(db)
    assert reports.flag_stuck_leads(db) == 1
    db.commit()
    assert reports.flag_stuck_leads(db) == 0
    assert db.scalar(select(Notification).where(Notification.kind == "stuck_lead")).title.startswith("No reply for")


def test_daily_report_has_correct_counts_and_goes_to_recipients(client, db, mailer):
    owner, owner_h = make_user(db, "owner")
    seed_activity(db)
    r = client.post("/api/v1/reports/daily/send-now", headers=owner_h)
    assert r.json()["data"]["sent"] is True
    mail = mailer.internal[0]
    assert mail["to"] == [owner.email]  # no recipients configured: owners and managers
    body = mail["body"]
    assert "New leads (last 24 hours): 3" in body and "Hot leads open now: 1" in body and "Stuck leads (no reply for 24+ hours): 1" in body
    assert "Average first reply time (new leads): 40 seconds" in body
    assert "Hot Hari: Gave budget and timeline" in body and "Quiet Qadir" in body
    client.patch("/api/v1/settings", headers=owner_h, json={"report_recipients": ["boss@example.com"]})
    client.post("/api/v1/reports/daily/send-now", headers=owner_h)
    assert mailer.internal[1]["to"] == ["boss@example.com"]


def test_daily_report_runs_once_a_day_at_the_set_time_and_retries_failures(db, mailer):
    make_user(db, "owner")
    db.commit()
    ist = timezone(timedelta(hours=5, minutes=30))
    before = datetime(2026, 11, 22, 8, 30, tzinfo=ist)
    after = datetime(2026, 11, 22, 9, 5, tzinfo=ist)
    assert reports.maybe_send_daily_report(db, before) is None  # too early (default 09:00)
    mailer.fail = True
    assert reports.maybe_send_daily_report(db, after)["sent"] is False
    db.commit()
    mailer.fail = False
    assert reports.maybe_send_daily_report(db, after + timedelta(minutes=1))["sent"] is True  # failed send is retried
    db.commit()
    assert reports.maybe_send_daily_report(db, after + timedelta(hours=3)) is None  # already sent today
    assert reports.maybe_send_daily_report(db, after + timedelta(days=1)) is not None  # next day
    assert len(mailer.internal) == 2


# ---- settings -----------------------------------------------------------------------------------------------

def test_settings_validation_roles_masking_and_audit(client, db):
    _, owner = make_user(db, "owner")
    _, mgr = make_user(db, "manager")
    assert client.get("/api/v1/settings", headers=mgr).status_code == 403
    for bad in (
        {"followup_days": "3,1"}, {"followup_days": "1,40"}, {"followup_days": "a"}, {"send_window_start": 20, "send_window_end": 9},
        {"daily_report_time": "9am"}, {"report_recipients": ["nope"]}, {"stuck_lead_hours": 0}, {"unknown_key": 1},
        {"allowed_origins": ["https://x.com/path"]}, {"webhook_export_url": "ftp://x.com"}, {"business_name": ""},
    ):
        r = client.patch("/api/v1/settings", headers=owner, json=bad)
        assert r.status_code == 422, bad
    r = client.patch("/api/v1/settings", headers=owner, json={"business_name": "Acme Signs Pvt", "webhook_export_secret": "s3cret", "followup_days": "2,5"})
    assert r.status_code == 200
    d = client.get("/api/v1/settings", headers=owner).json()["data"]
    assert d["business_name"] == "Acme Signs Pvt" and d["followup_days"] == "2,5"
    assert "webhook_export_secret" not in d and d["webhook_export_secret_set"] is True
    audit = db.scalar(select(AuditLog).where(AuditLog.action == "settings.update"))
    assert "s3cret" not in json.dumps(audit.after) + json.dumps(audit.before)


def test_settings_change_real_behaviour(client, db, fake_wa, fake_llm):
    _, owner = make_user(db, "owner")
    client.patch("/api/v1/settings", headers=owner, json={"persona_name": "Priya", "business_name": "Zeta Boards", "followup_days": "2,5", "send_window_start": 8})
    fake_llm.script = [text("Hello.")]
    r = client.post("/api/v1/public/leads", headers=KEY, json={"name": "R", "phone": "+919876543210"})
    assert r.status_code == 201
    assert "You are Priya" in fake_llm.calls[0]["system"] and "Zeta Boards" in fake_llm.calls[0]["system"]
    db.expire_all()
    assert [f.day_offset for f in db.scalars(select(Followup).order_by(Followup.step)).all()] == [2, 5]


# ---- webhook export --------------------------------------------------------------------------------------------

class FakeHttp:
    def __init__(self):
        self.calls = []
        self.status = 200

    def __call__(self, url, content=None, headers=None, timeout=None, follow_redirects=None):
        self.calls.append({"url": url, "body": json.loads(content), "raw": content, "headers": headers})

        class R:
            status_code = self.status

        return R()


@pytest.fixture
def http(monkeypatch):
    f = FakeHttp()
    monkeypatch.setattr(webhook_export.httpx, "post", f)
    return f


def test_webhook_export_sends_signed_events_on_create_and_stage_change(client, db, http):
    _, owner = make_user(db, "owner")
    _, mgr = make_user(db, "manager")
    client.patch("/api/v1/settings", headers=owner, json={"webhook_export_url": "http://localhost:9000/hook", "webhook_export_secret": "topsecret"})
    r = client.post("/api/v1/leads", headers=mgr, json={"name": "Ravi", "phone": "+919876543210"})
    lead_id = r.json()["data"]["id"]
    client.patch(f"/api/v1/leads/{lead_id}", headers=mgr, json={"stage": "qualified"})
    client.patch(f"/api/v1/leads/{lead_id}", headers=mgr, json={"company": "no stage change"})
    db.expire_all()
    assert [o.event for o in db.scalars(select(WebhookOutbox).order_by(WebhookOutbox.created_at)).all()] == ["lead.created", "lead.stage_changed"]
    assert webhook_export.deliver_outbox() == {"sent": 2, "retry": 0, "failed": 0}
    first, second = http.calls
    assert first["body"]["event"] == "lead.created" and first["body"]["lead"]["id"] == lead_id and first["body"]["lead"]["name"] == "Ravi"
    assert second["body"]["lead"]["stage"] == "qualified"
    expected = "sha256=" + hmac.new(b"topsecret", first["raw"], hashlib.sha256).hexdigest()
    assert first["headers"]["X-Signature"] == expected and first["headers"]["X-Event"] == "lead.created"
    assert webhook_export.deliver_outbox() == {"sent": 0, "retry": 0, "failed": 0}  # nothing is sent twice


def test_no_events_are_queued_without_a_webhook_url(client, db, http):
    _, mgr = make_user(db, "manager")
    client.post("/api/v1/leads", headers=mgr, json={"name": "Ravi", "phone": "+919876543210"})
    assert db.scalars(select(WebhookOutbox)).all() == []


def test_failed_deliveries_back_off_then_give_up(client, db, http):
    _, owner = make_user(db, "owner")
    client.patch("/api/v1/settings", headers=owner, json={"webhook_export_url": "http://localhost:9000/hook"})
    client.post("/api/v1/leads", headers=owner, json={"name": "Ravi", "phone": "+919876543210"})
    http.status = 500
    now = datetime.now(timezone.utc)
    assert webhook_export.deliver_outbox(now) == {"sent": 0, "retry": 1, "failed": 0}
    assert webhook_export.deliver_outbox(now) == {"sent": 0, "retry": 0, "failed": 0}  # not due yet (2 minute back-off)
    for i in range(1, 6):
        res = webhook_export.deliver_outbox(now + timedelta(hours=i))
    assert res["failed"] == 1
    db.expire_all()
    row = db.scalar(select(WebhookOutbox))
    assert row.status == "failed" and row.attempts == 6 and "500" in row.last_error
    http.status = 200
    assert webhook_export.deliver_outbox(now + timedelta(days=1)) == {"sent": 0, "retry": 0, "failed": 0}  # given up for good


def test_webhook_test_button_and_url_safety(client, db, http, monkeypatch):
    _, owner = make_user(db, "owner")
    assert client.post("/api/v1/export/webhook-test", headers=owner).status_code == 409  # no URL yet
    client.patch("/api/v1/settings", headers=owner, json={"webhook_export_url": "http://localhost:9000/hook"})
    assert client.post("/api/v1/export/webhook-test", headers=owner).json()["data"] == {"ok": True, "status": 200}
    http.status = 404
    assert client.post("/api/v1/export/webhook-test", headers=owner).json()["data"] == {"ok": False, "status": 404}
    from app.config import get_settings
    from app.services import app_settings

    monkeypatch.setattr(get_settings(), "environment", "production")
    for url in ("http://example.com/x", "https://localhost/x", "https://127.0.0.1/x", "https://10.0.0.5/x", "https://169.254.169.254/latest", "https://db.internal/x"):
        with pytest.raises(Exception):
            app_settings.validate_webhook_url(url)
    assert app_settings.validate_webhook_url("https://hooks.example.com/x") == "https://hooks.example.com/x"


# ---- widget ------------------------------------------------------------------------------------------------------

def test_widget_script_embed_snippet_and_history(client, db, fake_llm):
    _, owner = make_user(db, "owner")
    js = client.get("/api/v1/public/widget.js")
    assert js.status_code == 200 and "javascript" in js.headers["content-type"] and "AI assistant" in js.text
    emb = client.get("/api/v1/settings/widget-embed", headers=owner).json()["data"]
    assert emb["snippet"].startswith("<script src=") and "/api/v1/public/widget.js" in emb["snippet"] and emb["warning"]
    s = client.post("/api/v1/public/chat/sessions", json={"name": "Asha"}).json()["data"]
    h = {"X-Session-Token": s["session_token"]}
    fake_llm.script = [text("Hello Asha.")]
    client.post(f"/api/v1/public/chat/sessions/{s['conversation_id']}/messages", headers=h, json={"text": "Hi"})
    hist = client.get(f"/api/v1/public/chat/sessions/{s['conversation_id']}/history", headers=h).json()["data"]
    assert hist == [{"direction": "inbound", "body": "Hi"}, {"direction": "outbound", "body": "Hello Asha."}]
    assert client.get(f"/api/v1/public/chat/sessions/{s['conversation_id']}/history").status_code == 401


def test_widget_only_works_from_allowed_websites(client, db):
    _, owner = make_user(db, "owner")
    pre = client.options("/api/v1/public/chat/sessions", headers={"Origin": "https://anywhere.com", "Access-Control-Request-Method": "POST"})
    assert pre.status_code == 204 and pre.headers["access-control-allow-origin"] == "https://anywhere.com"  # nothing restricted yet
    client.patch("/api/v1/settings", headers=owner, json={"allowed_origins": ["https://shop.example.com"]})
    ok = client.post("/api/v1/public/chat/sessions", json={"name": "A"}, headers={"Origin": "https://shop.example.com"})
    assert ok.status_code == 201 and ok.headers["access-control-allow-origin"] == "https://shop.example.com"
    bad = client.post("/api/v1/public/chat/sessions", json={"name": "A"}, headers={"Origin": "https://evil.example.org"})
    assert bad.status_code == 403 and bad.json()["error"]["code"] == "origin_not_allowed"
    assert client.options("/api/v1/public/chat/sessions", headers={"Origin": "https://evil.example.org"}).status_code == 403
    assert client.get("/api/v1/settings/widget-embed", headers=owner).json()["data"]["warning"] is None


# ---- users -------------------------------------------------------------------------------------------------------

class FakeSupabase:
    def __init__(self):
        self.invited = []

    def invite(self, email):
        import uuid

        self.invited.append(email)
        return str(uuid.uuid4())


@pytest.fixture
def supabase(monkeypatch):
    f = FakeSupabase()
    monkeypatch.setattr(users, "supabase_admin", f)
    return f


def test_invite_users_and_role_rules(client, db, supabase):
    owner_p, owner = make_user(db, "owner")
    _, admin = make_user(db, "admin")
    _, mgr = make_user(db, "manager")
    assert client.post("/api/v1/users/invite", headers=mgr, json={"email": "x@example.com", "role": "sales"}).status_code == 403
    r = client.post("/api/v1/users/invite", headers=admin, json={"email": "Sam@Example.com", "role": "sales", "full_name": "Sam", "phone": "98765 43210"})
    assert r.status_code == 201 and r.json()["data"]["email"] == "sam@example.com" and r.json()["data"]["phone"] == "+919876543210"
    assert supabase.invited == ["sam@example.com"]
    assert client.post("/api/v1/users/invite", headers=admin, json={"email": "sam@example.com", "role": "sales"}).status_code == 409
    assert client.post("/api/v1/users/invite", headers=admin, json={"email": "boss@example.com", "role": "owner"}).status_code == 403  # only an owner grants owner
    assert client.post("/api/v1/users/invite", headers=owner, json={"email": "boss@example.com", "role": "owner"}).status_code == 201
    sam = r.json()["data"]["id"]
    assert client.patch(f"/api/v1/users/{sam}", headers=admin, json={"role": "admin"}).status_code == 403
    assert client.patch(f"/api/v1/users/{sam}", headers=admin, json={"role": "manager", "active": False}).json()["data"]["active"] is False
    assert client.get("/api/v1/users", headers=mgr).status_code == 403
    assert len(client.get("/api/v1/users", headers=owner).json()["data"]) == 5
    team = client.get("/api/v1/team", headers=mgr).json()["data"]
    assert {t["role"] for t in team} == {"owner", "manager"} and all(set(t) == {"id", "name", "role"} for t in team)  # no admin, no inactive users, no contact details


def test_unconfigured_supabase_is_a_clear_error_and_last_owner_is_protected(client, db):
    owner_p, owner = make_user(db, "owner")
    r = client.post("/api/v1/users/invite", headers=owner, json={"email": "a@example.com", "role": "sales"})
    assert r.status_code == 503 and "not configured" in r.json()["error"]["message"]
    assert client.patch(f"/api/v1/users/{owner_p.id}", headers=owner, json={"role": "manager"}).status_code == 409
    assert client.patch(f"/api/v1/users/{owner_p.id}", headers=owner, json={"active": False}).status_code == 409


def test_notifications_read_all(client, db, fake_wa, fake_llm):
    mgr_p, mgr = make_user(db, "manager")
    for i in range(3):
        db.add(Notification(profile_id=mgr_p.id, kind="hot_lead", title=f"t{i}"))
    db.commit()
    assert client.post("/api/v1/notifications/read-all", headers=mgr).json()["data"]["marked"] == 3
    assert client.get("/api/v1/notifications", headers=mgr, params={"unread_only": "true"}).json()["data"] == []


def test_a_lead_who_answered_after_our_last_message_is_not_stuck(db):
    now = datetime.now(timezone.utc)
    mk_lead(db, name="Answered", phone_e164="+919855555555", stage="qualifying", last_outbound_at=now - timedelta(hours=40), last_inbound_at=now - timedelta(hours=39))
    mk_lead(db, name="Silent", phone_e164="+919866666666", stage="qualifying", last_outbound_at=now - timedelta(hours=40), last_inbound_at=now - timedelta(hours=50))
    assert [x["name"] for x in reports.stuck_leads(db, 24)] == ["Silent"]
