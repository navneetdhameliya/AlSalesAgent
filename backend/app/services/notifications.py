"""In-app notifications plus WhatsApp alerts to the sales team."""
import logging

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.channels import whatsapp as wa
from app.config import get_settings
from app.models import Lead, Notification, Profile

log = logging.getLogger("notifications")
MANAGER_ROLES = ("owner", "manager")


def recipients_for(db: Session, lead: Lead | None, roles: tuple[str, ...] = MANAGER_ROLES) -> list[Profile]:
    """The assigned salesperson, or every active owner and manager when nobody is assigned."""
    if lead is not None and lead.assigned_to:
        p = db.get(Profile, lead.assigned_to)
        if p and p.active:
            return [p]
    return list(db.scalars(select(Profile).where(Profile.active.is_(True), Profile.role.in_(roles))).all())


def notify(
    db: Session, kind: str, title: str, body: str, lead: Lead | None = None, recipients: list[Profile] | None = None
) -> list[Notification]:
    """Create a dashboard notification for each recipient and send a WhatsApp alert when they have a number."""
    people = recipients if recipients is not None else recipients_for(db, lead)
    rows: list[Notification] = []
    if not people:  # keep a record even when nobody can be alerted
        n = Notification(profile_id=None, lead_id=lead.id if lead else None, kind=kind, title=title, body=body, whatsapp_status="skipped")
        db.add(n)
        rows.append(n)
    for p in people:
        n = Notification(profile_id=p.id, lead_id=lead.id if lead else None, kind=kind, title=title, body=body)
        if p.whatsapp_alerts_enabled and p.phone_e164:
            try:
                wa.whatsapp_client.send_text(p.phone_e164, f"{title}\n{body}"[:1500])
                n.whatsapp_status = "sent"
            except wa.ChannelError as e:
                n.whatsapp_status = "failed"
                log.warning("WhatsApp alert to %s failed: %s", p.id, e)
        else:
            n.whatsapp_status = "skipped"
        db.add(n)
        rows.append(n)
    db.flush()
    return rows


def lead_link(lead: Lead) -> str:
    return f"{get_settings().dashboard_url}/leads/{lead.id}"
