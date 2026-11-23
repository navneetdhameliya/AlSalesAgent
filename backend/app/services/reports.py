"""Analytics, stuck leads and the daily email report."""
import logging
import uuid
from datetime import datetime, timedelta, timezone
from statistics import median
from zoneinfo import ZoneInfo

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app import db as dbmod
from app.channels import email as em
from app.channels.whatsapp import ChannelError
from app.config import get_settings
from app.models import Lead, Message, Notification, Profile, Setting
from app.services import app_settings, notifications
from app.services.leads import as_utc

log = logging.getLogger("reports")
CLOSED = ("won", "lost", "stopped", "booked")


def summary(db: Session, days: int = 7, now: datetime | None = None) -> dict:
    now = now or datetime.now(timezone.utc)
    since = now - timedelta(days=days)
    leads = db.scalars(select(Lead).where(Lead.deleted_at.is_(None), Lead.created_at >= since)).all()

    def tally(attr):
        out: dict[str, int] = {}
        for l in leads:
            out[getattr(l, attr)] = out.get(getattr(l, attr), 0) + 1
        return out

    firsts = [l.first_response_seconds for l in leads if l.first_response_seconds is not None]
    inbound = db.scalar(select(func.count()).select_from(Message).where(Message.direction == "inbound", Message.created_at >= since))
    ai_sent = db.scalar(
        select(func.count()).select_from(Message).where(
            Message.direction == "outbound", Message.sender == "ai", Message.status.in_(["sent", "delivered", "read"]), Message.created_at >= since
        )
    )
    cfg = app_settings.get_all(db)
    return {
        "period_days": days,
        "since": since.isoformat(),
        "new_leads": len(leads),
        "leads_replied": sum(1 for l in leads if l.last_inbound_at is not None),
        "inbound_messages": inbound,
        "ai_replies_sent": ai_sent,
        "hot_leads": sum(1 for l in leads if l.grade == "hot"),
        "avg_first_reply_seconds": round(sum(firsts) / len(firsts)) if firsts else None,
        "median_first_reply_seconds": round(median(firsts)) if firsts else None,
        "by_grade": tally("grade"),
        "by_stage": tally("stage"),
        "by_source": tally("source"),
        "stuck_leads": len(stuck_leads(db, cfg["stuck_lead_hours"], now)),
    }


def stuck_leads(db: Session, hours: int, now: datetime | None = None) -> list[dict]:
    """Leads we messaged more than `hours` ago that have not answered since."""
    now = now or datetime.now(timezone.utc)
    cutoff = now - timedelta(hours=hours)
    rows = db.scalars(
        select(Lead).where(
            Lead.deleted_at.is_(None), Lead.opted_out_at.is_(None), Lead.stage.notin_(CLOSED),
            Lead.last_outbound_at.is_not(None), Lead.last_outbound_at <= cutoff,
        ).order_by(Lead.last_outbound_at)
    ).all()
    out = []
    for l in rows:
        out_at, in_at = as_utc(l.last_outbound_at), as_utc(l.last_inbound_at)
        if in_at and in_at >= out_at:
            continue
        out.append({
            "id": str(l.id), "name": l.name, "phone": l.phone_e164, "email": l.email, "stage": l.stage, "grade": l.grade,
            "assigned_to": str(l.assigned_to) if l.assigned_to else None,
            "hours_waiting": int((now - out_at).total_seconds() // 3600),
        })
    return out


def flag_stuck_leads(db: Session, now: datetime | None = None) -> int:
    """One dashboard alert per stuck lead per silence (not repeated every hour)."""
    now = now or datetime.now(timezone.utc)
    hours = app_settings.get(db, "stuck_lead_hours")
    count = 0
    for item in stuck_leads(db, hours, now):
        lead = db.get(Lead, uuid.UUID(item["id"]))
        last_out = as_utc(lead.last_outbound_at)
        already = db.scalar(
            select(Notification.id).where(Notification.lead_id == lead.id, Notification.kind == "stuck_lead", Notification.created_at >= last_out).limit(1)
        )
        if already:
            continue
        notifications.notify(
            db, "stuck_lead", f"No reply for {item['hours_waiting']}h: {lead.name or lead.phone_e164 or lead.email or 'lead'}",
            f"Stage {lead.stage}, grade {lead.grade}.\n{notifications.lead_link(lead)}", lead,
        )
        count += 1
    return count


def fmt_seconds(sec: int | None) -> str:
    if sec is None:
        return "no data"
    if sec < 90:
        return f"{sec} seconds"
    if sec < 5400:
        return f"{round(sec / 60)} minutes"
    return f"{sec / 3600:.1f} hours"


def build_daily_report(db: Session, now: datetime | None = None) -> tuple[str, str]:
    """Subject and plain-text body covering the last 24 hours."""
    now = now or datetime.now(timezone.utc)
    s = summary(db, days=1, now=now)
    cfg = app_settings.get_all(db)
    day = now.astimezone(ZoneInfo(get_settings().business_timezone)).strftime("%d %b %Y")
    stuck = stuck_leads(db, cfg["stuck_lead_hours"], now)
    hot = db.scalars(select(Lead).where(Lead.deleted_at.is_(None), Lead.grade == "hot", Lead.stage.notin_(CLOSED))).all()
    lines = [
        f"Daily sales report for {cfg['business_name']}, {day}",
        "",
        f"New leads (last 24 hours): {s['new_leads']}",
        f"Customer messages received: {s['inbound_messages']}",
        f"AI replies sent: {s['ai_replies_sent']}",
        f"Average first reply time (new leads): {fmt_seconds(s['avg_first_reply_seconds'])}",
        f"Hot leads open now: {len(hot)}",
        f"Stuck leads (no reply for {cfg['stuck_lead_hours']}+ hours): {len(stuck)}",
    ]
    if hot:
        lines += ["", "Hot leads:"] + [f"- {l.name or l.phone_e164 or l.email or 'Unknown'}: {l.grade_reason or 'no reason recorded'}" for l in hot[:15]]
    if stuck:
        lines += ["", "Stuck leads:"] + [f"- {x['name'] or x['phone'] or x['email'] or 'Unknown'} ({x['hours_waiting']}h waiting, stage {x['stage']})" for x in stuck[:15]]
    return f"Daily sales report, {day}", "\n".join(lines)


def report_recipients(db: Session) -> list[str]:
    configured = app_settings.get(db, "report_recipients")
    if configured:
        return configured
    rows = db.scalars(select(Profile).where(Profile.active.is_(True), Profile.role.in_(["owner", "manager"]), Profile.email.is_not(None))).all()
    return [p.email for p in rows]


def send_daily_report(db: Session, now: datetime | None = None) -> dict:
    to = report_recipients(db)
    if not to:
        return {"sent": False, "reason": "no recipients"}
    subject, body = build_daily_report(db, now)
    try:
        em.email_client.send_internal(to, subject, body)
    except ChannelError as e:
        return {"sent": False, "reason": str(e)[:200]}
    return {"sent": True, "recipients": to, "subject": subject}


def maybe_send_daily_report(db: Session, now: datetime | None = None) -> dict | None:
    """Send once per local day, at or after the configured time."""
    now = now or datetime.now(timezone.utc)
    local = now.astimezone(ZoneInfo(get_settings().business_timezone))
    hh, mm = (int(x) for x in app_settings.get(db, "daily_report_time").split(":"))
    if (local.hour, local.minute) < (hh, mm):
        return None
    today = local.date().isoformat()
    row = db.get(Setting, "last_report_date")
    if row and row.value.get("v") == today:
        return None
    result = send_daily_report(db, now)
    if result["sent"]:  # a failed send is retried on the next tick
        if row is None:
            db.add(Setting(key="last_report_date", value={"v": today}))
        else:
            row.value = {"v": today}
    return result


def housekeeping(now: datetime | None = None) -> dict:
    """Everything the scheduler does besides follow-ups."""
    from app.services import webhook_export

    out = {"webhooks": webhook_export.deliver_outbox(now)}
    db = dbmod.SessionLocal()
    try:
        out["stuck_alerts"] = flag_stuck_leads(db, now)
        out["report"] = maybe_send_daily_report(db, now)
        db.commit()
    except Exception:
        db.rollback()
        log.exception("housekeeping failed")
    finally:
        db.close()
    return out
