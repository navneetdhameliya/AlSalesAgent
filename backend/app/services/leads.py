import re
import uuid
from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import get_settings
from app.models import Conversation, Lead, OptOut


def as_utc(dt: datetime | None) -> datetime | None:
    if dt is None:
        return None
    return dt.replace(tzinfo=timezone.utc) if dt.tzinfo is None else dt.astimezone(timezone.utc)


def normalize_phone(raw: str | None) -> str | None:
    """Return E.164 (+digits) or None.

    A national number written without a country code (10 digits, or 0 + 10 digits) gets DEFAULT_COUNTRY_CODE.
    Anything with a + or 00 prefix, or longer than that, is taken as already international.
    """
    if not raw:
        return None
    raw = raw.strip()
    digits = re.sub(r"\D", "", raw)
    international = raw.startswith("+") or raw.startswith("00")
    if raw.startswith("00"):
        digits = digits[2:]
    if not international:
        if len(digits) == 11 and digits.startswith("0"):
            digits = digits[1:]
        if len(digits) == 10:
            digits = get_settings().default_country_code + digits
    if not 8 <= len(digits) <= 15:
        return None
    return "+" + digits


def normalize_email(raw: str | None) -> str | None:
    if not raw:
        return None
    raw = raw.strip().lower()
    return raw if re.fullmatch(r"[^@\s]+@[^@\s]+\.[^@\s]+", raw) else None


def find_lead(db: Session, phone: str | None, email: str | None) -> Lead | None:
    if phone:
        lead = db.scalar(select(Lead).where(Lead.phone_e164 == phone, Lead.deleted_at.is_(None)))
        if lead:
            return lead
    if email:
        return db.scalar(select(Lead).where(Lead.email == email, Lead.deleted_at.is_(None)))
    return None


def find_or_create_lead(
    db: Session,
    *,
    phone: str | None = None,
    email: str | None = None,
    name: str | None = None,
    source: str = "manual",
    **extra,
) -> tuple[Lead, bool]:
    phone, email = normalize_phone(phone), normalize_email(email)
    lead = find_lead(db, phone, email)
    if lead:
        # fill gaps only, never overwrite what the lead or a human already set
        for k, v in {"name": name, "phone_e164": phone, "email": email, **extra}.items():
            if v and not getattr(lead, k, None):
                setattr(lead, k, v)
        return lead, False
    lead = Lead(phone_e164=phone, email=email, name=name, source=source, **{k: v for k, v in extra.items() if v})
    db.add(lead)
    db.flush()
    return lead, True


def get_or_create_conversation(db: Session, lead: Lead, channel: str, thread_id: str | None) -> Conversation:
    conv = None
    if thread_id:
        conv = db.scalar(
            select(Conversation).where(Conversation.channel == channel, Conversation.channel_thread_id == thread_id)
        )
    if conv is None:
        conv = Conversation(lead_id=lead.id, channel=channel, channel_thread_id=thread_id)
        db.add(conv)
        db.flush()
    return conv


UPDATABLE_FIELDS = {
    "name", "phone", "email", "company", "city", "requirement",
    "budget_text", "timeline_text", "location_text", "deal_size_estimate",
}


def apply_lead_update(lead: Lead, fields: dict) -> dict:
    """Apply qualifying answers from the agent. Returns the fields actually changed."""
    changed: dict = {}
    for key, value in fields.items():
        if key not in UPDATABLE_FIELDS or value in (None, ""):
            continue
        if key == "phone":
            value = normalize_phone(str(value))
            key = "phone_e164"
            if not value or lead.phone_e164:  # never replace the number the lead contacted us from
                continue
        elif key == "email":
            value = normalize_email(str(value))
            if not value:
                continue
        elif key == "deal_size_estimate":
            try:
                value = float(value)
            except (TypeError, ValueError):
                continue
        else:
            value = str(value).strip()[:2000]
        if getattr(lead, key) != value:
            setattr(lead, key, value)
            changed[key] = value
    return changed


def is_opted_out(db: Session, lead: Lead, channel: str | None = None) -> bool:
    """True if we must not contact this lead. A bounce only blocks email; every other opt-out blocks all channels."""
    if lead.opted_out_at:
        return True
    conds = []
    if lead.phone_e164:
        conds.append(OptOut.phone_e164 == lead.phone_e164)
    if lead.email:
        conds.append(OptOut.email == lead.email)
    if not conds:
        return False
    from sqlalchemy import or_

    for row in db.scalars(select(OptOut).where(or_(*conds))).all():
        if row.source in ("bounce", "complaint") and channel != "email":
            continue
        return True
    return False


def lead_missing_fields(lead: Lead) -> list[str]:
    checks = {
        "name": lead.name,
        "phone": lead.phone_e164,
        "requirement": lead.requirement,
        "budget": lead.budget_text or lead.budget_amount,
        "timeline": lead.timeline_text,
        "location": lead.location_text or lead.city,
    }
    return [k for k, v in checks.items() if not v]


def lead_summary_dict(lead: Lead) -> dict:
    return {
        "id": str(lead.id),
        "name": lead.name,
        "phone": lead.phone_e164,
        "email": lead.email,
        "company": lead.company,
        "city": lead.city,
        "requirement": lead.requirement,
        "budget": lead.budget_text,
        "timeline": lead.timeline_text,
        "location": lead.location_text,
        "stage": lead.stage,
        "grade": lead.grade,
    }


def new_uuid() -> uuid.UUID:
    return uuid.uuid4()
