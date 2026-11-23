"""Owner and manager tools: lead edit, CSV import and export, analytics, settings, users, webhook export, widget code."""
import csv
import io
import json
import uuid

from fastapi import APIRouter, Depends, File, Form, Query, UploadFile
from fastapi.responses import Response
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.serializers import lead_out
from app.audit import write_audit
from app.auth import require_roles
from app.channels.whatsapp import ChannelError
from app.config import get_settings
from app.db import get_db
from app.errors import AppError
from app.models import Lead, Notification, Profile
from app.services import app_settings, followups, reports, users, webhook_export
from app.services.leads import find_lead, find_or_create_lead, normalize_email, normalize_phone

router = APIRouter(tags=["admin"])
STAFF = ("owner", "manager", "sales")
ALL_ROLES = ("owner", "admin", "manager", "sales")
STAGES_MANUAL = ("new", "qualifying", "qualified", "handoff", "booked", "won", "lost")
GRADES = ("ungraded", "hot", "warm", "cold")


# ---- Lead edit --------------------------------------------------------------------------------

class LeadPatch(BaseModel):
    name: str | None = Field(default=None, max_length=200)
    phone: str | None = None
    email: str | None = None
    company: str | None = Field(default=None, max_length=200)
    city: str | None = Field(default=None, max_length=120)
    requirement: str | None = Field(default=None, max_length=2000)
    budget_text: str | None = Field(default=None, max_length=200)
    timeline_text: str | None = Field(default=None, max_length=200)
    location_text: str | None = Field(default=None, max_length=200)
    stage: str | None = Field(default=None, pattern="^(" + "|".join(STAGES_MANUAL) + ")$")
    grade: str | None = Field(default=None, pattern="^(" + "|".join(GRADES) + ")$")
    tags: list[str] | None = Field(default=None, max_length=20)
    notes: str | None = Field(default=None, max_length=4000)


@router.patch("/leads/{lead_id}")
def patch_lead(lead_id: uuid.UUID, body: LeadPatch, profile: Profile = Depends(require_roles(*STAFF)), db: Session = Depends(get_db)):
    lead = db.get(Lead, lead_id)
    if lead is None or lead.deleted_at is not None or (profile.role == "sales" and lead.assigned_to != profile.id):
        raise AppError("not_found", "Lead not found.", 404)
    changes = body.model_dump(exclude_unset=True)
    if profile.role == "sales" and set(changes) - {"notes", "tags"}:
        raise AppError("no_permission", "Salespeople can add notes and tags only.", 403)
    if "stage" in changes and lead.stage == "stopped":
        raise AppError("invalid_state", "This lead opted out and cannot be moved to another stage.", 409)
    before = {k: getattr(lead, {"phone": "phone_e164"}.get(k, k)) for k in changes}
    if "phone" in changes:
        phone = normalize_phone(changes.pop("phone"))
        other = find_lead(db, phone, None) if phone else None
        if other and other.id != lead.id:
            raise AppError("conflict", "Another lead already has this phone number.", 409)
        lead.phone_e164 = phone
    if "email" in changes:
        email = normalize_email(changes.pop("email"))
        other = find_lead(db, None, email) if email else None
        if other and other.id != lead.id:
            raise AppError("conflict", "Another lead already has this email.", 409)
        lead.email = email
    if "grade" in changes:
        lead.grade, lead.grade_reason = changes.pop("grade"), f"Set manually by {profile.full_name or 'a team member'}"
    for k, v in changes.items():
        setattr(lead, k, v)
    if lead.stage in ("won", "lost", "booked"):
        followups.cancel_pending(db, lead, f"stage_{lead.stage}")
    write_audit(db, profile.id, "lead.update", "lead", str(lead.id), {k: str(v) for k, v in before.items()}, body.model_dump(exclude_unset=True))
    db.commit()
    return {"data": lead_out(lead)}


# ---- CSV import / export --------------------------------------------------------------------------

IMPORT_FIELDS = ("name", "phone", "email", "company", "city", "requirement", "budget_text", "timeline_text", "notes")
MAX_IMPORT_BYTES = 1_000_000
MAX_IMPORT_ROWS = 2000
ALIASES = {
    "name": ("name", "full name", "customer", "contact"),
    "phone": ("phone", "mobile", "whatsapp", "phone number", "contact number"),
    "email": ("email", "e-mail", "email address"),
    "company": ("company", "organisation", "organization", "business"),
    "city": ("city", "town"),
    "requirement": ("requirement", "need", "enquiry", "inquiry", "message", "interest"),
    "budget_text": ("budget",),
    "timeline_text": ("timeline", "needed by", "when"),
    "notes": ("notes", "note", "remarks"),
}


def auto_map(headers: list[str]) -> dict[str, str]:
    lowered = {h.strip().lower(): h for h in headers}
    out = {}
    for field, names in ALIASES.items():
        for n in names:
            if n in lowered:
                out[field] = lowered[n]
                break
    return out


@router.post("/leads/import")
async def import_leads(
    file: UploadFile = File(...),
    mapping: str | None = Form(default=None, description='JSON like {"phone": "Mobile No"}; columns are auto-detected when omitted'),
    profile: Profile = Depends(require_roles("owner", "manager")),
    db: Session = Depends(get_db),
):
    """Import leads from CSV. Imported leads are NOT messaged automatically. Duplicates (same phone or email) are skipped."""
    raw = await file.read(MAX_IMPORT_BYTES + 1)
    if len(raw) > MAX_IMPORT_BYTES:
        raise AppError("file_too_large", "The CSV is larger than 1 MB.", 413)
    try:
        text = raw.decode("utf-8-sig")
    except UnicodeDecodeError:
        raise AppError("validation_error", "Save the file as UTF-8 CSV.", 422)
    reader = csv.DictReader(io.StringIO(text))
    headers = reader.fieldnames or []
    if not headers:
        raise AppError("validation_error", "The CSV has no header row.", 422)
    try:
        chosen = json.loads(mapping) if mapping else auto_map(headers)
    except json.JSONDecodeError:
        raise AppError("validation_error", "The column mapping is not valid JSON.", 422)
    bad = [f for f in chosen if f not in IMPORT_FIELDS] + [c for c in chosen.values() if c not in headers]
    if bad:
        raise AppError("validation_error", f"Unknown field or column in mapping: {bad[0]}", 422)
    if "phone" not in chosen and "email" not in chosen:
        raise AppError("validation_error", "Map at least a phone or an email column.", 422)

    created = duplicates = 0
    errors: list[dict] = []
    for n, row in enumerate(reader, start=2):  # row 1 is the header
        if n - 1 > MAX_IMPORT_ROWS:
            errors.append({"row": n, "error": f"Stopped at {MAX_IMPORT_ROWS} rows."})
            break
        vals = {f: (row.get(col) or "").strip() for f, col in chosen.items()}
        phone, email = normalize_phone(vals.get("phone")), normalize_email(vals.get("email"))
        if not phone and not email:
            errors.append({"row": n, "error": "No valid phone or email."})
            continue
        extra = {k: v[:2000] for k, v in vals.items() if k in ("company", "city", "requirement", "budget_text", "timeline_text", "notes") and v}
        lead, is_new = find_or_create_lead(db, phone=phone, email=email, name=vals.get("name") or None, source="csv_import", **extra)
        if is_new:
            created += 1
        else:
            duplicates += 1
    write_audit(db, profile.id, "leads.import", "lead", None, None, {"created": created, "duplicates": duplicates, "errors": len(errors)})
    db.commit()
    return {"data": {"created": created, "duplicates": duplicates, "errors": errors[:100], "error_count": len(errors), "mapping": chosen}}


EXPORT_COLUMNS = ["id", "name", "phone", "email", "company", "city", "source", "requirement", "budget", "timeline", "location", "stage", "grade", "grade_reason", "assigned_to", "first_response_seconds", "created_at"]


def csv_safe(value) -> str:
    """Stop spreadsheet apps from running a cell as a formula."""
    text = "" if value is None else str(value)
    return "'" + text if text[:1] in ("=", "+", "-", "@", "\t", "\r") else text


@router.get("/export/leads")
def export_leads(stage: str | None = None, grade: str | None = None, profile: Profile = Depends(require_roles("owner", "manager")), db: Session = Depends(get_db)):
    q = select(Lead).where(Lead.deleted_at.is_(None)).order_by(Lead.created_at.desc())
    if stage:
        q = q.where(Lead.stage == stage)
    if grade:
        q = q.where(Lead.grade == grade)
    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow(EXPORT_COLUMNS)
    for lead in db.scalars(q).all():
        d = lead_out(lead)
        w.writerow([csv_safe(d.get(c, getattr(lead, c, None))) for c in EXPORT_COLUMNS])
    write_audit(db, profile.id, "leads.export", "lead", None, None, {"stage": stage, "grade": grade})
    db.commit()
    return Response(buf.getvalue(), media_type="text/csv", headers={"Content-Disposition": 'attachment; filename="leads.csv"'})


@router.post("/export/webhook-test")
def webhook_test(_p: Profile = Depends(require_roles("owner", "admin")), db: Session = Depends(get_db)):
    return {"data": webhook_export.send_test(db)}


# ---- Analytics -------------------------------------------------------------------------------------

@router.get("/analytics/summary")
def analytics_summary(days: int = Query(7, ge=1, le=90), _p: Profile = Depends(require_roles("owner", "manager")), db: Session = Depends(get_db)):
    return {"data": reports.summary(db, days)}


@router.get("/analytics/stuck-leads")
def analytics_stuck(_p: Profile = Depends(require_roles("owner", "manager")), db: Session = Depends(get_db)):
    return {"data": reports.stuck_leads(db, app_settings.get(db, "stuck_lead_hours"))}


@router.post("/reports/daily/send-now")
def send_report_now(_p: Profile = Depends(require_roles("owner", "manager")), db: Session = Depends(get_db)):
    return {"data": reports.send_daily_report(db)}


# ---- Settings -------------------------------------------------------------------------------------

@router.get("/settings")
def get_settings_api(_p: Profile = Depends(require_roles("owner", "admin")), db: Session = Depends(get_db)):
    return {"data": app_settings.public_view(app_settings.get_all(db))}


@router.patch("/settings")
def patch_settings(body: dict, profile: Profile = Depends(require_roles("owner", "admin")), db: Session = Depends(get_db)):
    if not body:
        raise AppError("validation_error", "Nothing to update.", 422)
    before, after = app_settings.update(db, body)
    write_audit(db, profile.id, "settings.update", "settings", None, before, after)
    db.commit()
    return {"data": after}


@router.get("/settings/widget-embed")
def widget_embed(_p: Profile = Depends(require_roles("owner", "admin")), db: Session = Depends(get_db)):
    api = get_settings().api_base_url.rstrip("/")
    name = app_settings.get(db, "business_name")
    snippet = f'<script src="{api}/api/v1/public/widget.js" data-api="{api}" data-title="Chat with {name}" async></script>'
    origins = app_settings.get(db, "allowed_origins")
    return {"data": {"snippet": snippet, "allowed_origins": origins, "warning": None if origins else "No allowed websites set: any website can use this chat."}}


# ---- Users ------------------------------------------------------------------------------------------

ROLES = ("owner", "admin", "manager", "sales")


def user_out(p: Profile) -> dict:
    return {"id": str(p.id), "name": p.full_name, "email": p.email, "phone": p.phone_e164, "role": p.role, "active": p.active, "whatsapp_alerts": p.whatsapp_alerts_enabled}


@router.get("/users")
def list_users(_p: Profile = Depends(require_roles("owner", "admin")), db: Session = Depends(get_db)):
    return {"data": [user_out(p) for p in db.scalars(select(Profile).order_by(Profile.full_name, Profile.email)).all()]}


@router.get("/team")
def team(_p: Profile = Depends(require_roles("owner", "manager")), db: Session = Depends(get_db)):
    """Names for the assign-a-salesperson picker. No contact details."""
    rows = db.scalars(select(Profile).where(Profile.active.is_(True), Profile.role.in_(["owner", "manager", "sales"])).order_by(Profile.full_name)).all()
    return {"data": [{"id": str(p.id), "name": p.full_name or p.email, "role": p.role} for p in rows]}


class Invite(BaseModel):
    email: str = Field(max_length=320)
    role: str = Field(pattern="^(owner|admin|manager|sales)$")
    full_name: str | None = Field(default=None, max_length=200)
    phone: str | None = None


def _can_grant(actor: Profile, role: str) -> None:
    if role in ("owner", "admin") and actor.role != "owner":
        raise AppError("no_permission", "Only an owner can give the owner or admin role.", 403)


@router.post("/users/invite", status_code=201)
def invite_user(body: Invite, actor: Profile = Depends(require_roles("owner", "admin")), db: Session = Depends(get_db)):
    _can_grant(actor, body.role)
    email = normalize_email(body.email)
    if not email:
        raise AppError("validation_error", "Enter a valid email.", 422, {"email": "Not a valid email."})
    if db.scalar(select(Profile.id).where(Profile.email == email)):
        raise AppError("conflict", "A user with this email already exists.", 409)
    try:
        user_id = users.supabase_admin.invite(email)
    except ChannelError as e:
        raise AppError("invite_failed", str(e), 503)
    p = Profile(id=uuid.UUID(str(user_id)), email=email, full_name=body.full_name or "", role=body.role, phone_e164=normalize_phone(body.phone))
    db.add(p)
    write_audit(db, actor.id, "user.invite", "profile", str(p.id), None, {"email": email, "role": body.role})
    db.commit()
    return {"data": user_out(p)}


class UserPatch(BaseModel):
    full_name: str | None = Field(default=None, max_length=200)
    phone: str | None = None
    role: str | None = Field(default=None, pattern="^(owner|admin|manager|sales)$")
    active: bool | None = None
    whatsapp_alerts: bool | None = None


@router.patch("/users/{user_id}")
def patch_user(user_id: uuid.UUID, body: UserPatch, actor: Profile = Depends(require_roles("owner", "admin")), db: Session = Depends(get_db)):
    p = db.get(Profile, user_id)
    if p is None:
        raise AppError("not_found", "User not found.", 404)
    ch = body.model_dump(exclude_unset=True)
    if "role" in ch:
        _can_grant(actor, ch["role"])
        if p.role == "owner" and ch["role"] != "owner" and db.scalar(select(Profile.id).where(Profile.role == "owner", Profile.active.is_(True), Profile.id != p.id)) is None:
            raise AppError("invalid_state", "There must be at least one active owner.", 409)
    if p.role in ("owner", "admin") and actor.role != "owner" and p.id != actor.id:
        raise AppError("no_permission", "Only an owner can change an owner or admin.", 403)
    if ch.get("active") is False and p.role == "owner" and db.scalar(select(Profile.id).where(Profile.role == "owner", Profile.active.is_(True), Profile.id != p.id)) is None:
        raise AppError("invalid_state", "There must be at least one active owner.", 409)
    before = user_out(p)
    if "full_name" in ch:
        p.full_name = ch["full_name"] or ""
    if "phone" in ch:
        p.phone_e164 = normalize_phone(ch["phone"])
    if "role" in ch:
        p.role = ch["role"]
    if "active" in ch:
        p.active = ch["active"]
    if "whatsapp_alerts" in ch:
        p.whatsapp_alerts_enabled = ch["whatsapp_alerts"]
    write_audit(db, actor.id, "user.update", "profile", str(p.id), before, user_out(p))
    db.commit()
    return {"data": user_out(p)}


# ---- Notifications: mark everything read --------------------------------------------------------------

@router.post("/notifications/read-all")
def read_all(profile: Profile = Depends(require_roles(*ALL_ROLES)), db: Session = Depends(get_db)):
    from datetime import datetime, timezone

    rows = db.scalars(select(Notification).where(Notification.profile_id == profile.id, Notification.read_at.is_(None))).all()
    now = datetime.now(timezone.utc)
    for n in rows:
        n.read_at = now
    db.commit()
    return {"data": {"marked": len(rows)}}
