"""Dashboard API (authenticated). Day 1: profile, leads, messages, knowledge base."""
import uuid

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel, Field
from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session

from app.api.serializers import knowledge_out, lead_out, message_out
from app.audit import write_audit
from app.auth import current_profile, require_roles
from app.db import get_db
from app.errors import AppError
from app.models import Handoff, KnowledgeItem, Lead, Message, Profile
from app.services import knowledge as kb
from app.services.leads import find_or_create_lead, normalize_email, normalize_phone

router = APIRouter(tags=["dashboard"])


@router.get("/me")
def me(profile: Profile = Depends(current_profile)):
    return {"data": {"id": str(profile.id), "name": profile.full_name, "email": profile.email, "role": profile.role}}


# ---- Leads ----------------------------------------------------------------------------

def _visible_leads(profile: Profile):
    q = select(Lead).where(Lead.deleted_at.is_(None))
    if profile.role == "sales":
        q = q.where(Lead.assigned_to == profile.id)
    return q


@router.get("/leads")
def list_leads(
    stage: str | None = None,
    grade: str | None = None,
    q: str | None = Query(default=None, max_length=100),
    page: int = Query(1, ge=1),
    page_size: int = Query(25, ge=1, le=100),
    profile: Profile = Depends(require_roles("owner", "manager", "sales")),
    db: Session = Depends(get_db),
):
    base = _visible_leads(profile)
    if stage:
        base = base.where(Lead.stage == stage)
    if grade:
        base = base.where(Lead.grade == grade)
    if q:
        like = f"%{q.lower()}%"
        base = base.where(or_(func.lower(Lead.name).like(like), Lead.phone_e164.like(like), func.lower(Lead.email).like(like), func.lower(Lead.company).like(like)))
    total = db.scalar(select(func.count()).select_from(base.subquery()))
    rows = db.scalars(base.order_by(Lead.created_at.desc()).offset((page - 1) * page_size).limit(page_size)).all()
    return {"data": [lead_out(l) for l in rows], "meta": {"page": page, "page_size": page_size, "total": total}}


class LeadCreate(BaseModel):
    name: str | None = Field(default=None, max_length=200)
    phone: str | None = None
    email: str | None = None
    company: str | None = Field(default=None, max_length=200)
    requirement: str | None = Field(default=None, max_length=2000)
    notes: str | None = Field(default=None, max_length=4000)


@router.post("/leads", status_code=201)
def create_lead(body: LeadCreate, profile: Profile = Depends(require_roles("owner", "manager", "sales")), db: Session = Depends(get_db)):
    phone, email = normalize_phone(body.phone), normalize_email(body.email)
    if not phone and not email and not body.name:
        raise AppError("validation_error", "Give at least a name, a phone or an email.", 422)
    lead, created = find_or_create_lead(
        db, phone=phone, email=email, name=body.name, source="manual",
        company=body.company, requirement=body.requirement, notes=body.notes,
    )
    if created and profile.role == "sales":
        lead.assigned_to = profile.id
    write_audit(db, profile.id, "lead.create" if created else "lead.duplicate", "lead", str(lead.id))
    db.commit()
    return {"data": lead_out(lead), "meta": {"created": created}}


def _get_lead(db: Session, profile: Profile, lead_id: uuid.UUID) -> Lead:
    lead = db.scalar(_visible_leads(profile).where(Lead.id == lead_id))
    if lead is None:
        raise AppError("not_found", "Lead not found.", 404)
    return lead


@router.get("/leads/{lead_id}")
def get_lead(lead_id: uuid.UUID, profile: Profile = Depends(require_roles("owner", "manager", "sales")), db: Session = Depends(get_db)):
    lead = _get_lead(db, profile, lead_id)
    h = db.scalar(select(Handoff).where(Handoff.lead_id == lead.id, Handoff.status == "open").order_by(Handoff.created_at.desc()))
    data = lead_out(lead) | {
        "handoff": {"id": str(h.id), "reason": h.reason, "summary": h.summary} if h else None,
        "score_signals": lead.score_signals or [],
        "next_followup_at": lead.next_followup_at.isoformat() if lead.next_followup_at else None,
    }
    return {"data": data}


@router.get("/leads/{lead_id}/messages")
def lead_messages(lead_id: uuid.UUID, profile: Profile = Depends(require_roles("owner", "manager", "sales")), db: Session = Depends(get_db)):
    lead = _get_lead(db, profile, lead_id)
    rows = db.scalars(select(Message).where(Message.lead_id == lead.id).order_by(Message.created_at, Message.id)).all()
    return {"data": [message_out(m) for m in rows]}


# ---- Knowledge base ---------------------------------------------------------------------

class KnowledgeCreate(BaseModel):
    title: str = Field(min_length=1, max_length=300)
    text: str = Field(min_length=1, max_length=200_000)
    kind: str = Field(default="other", pattern="^(product|faq|policy|transcript|other)$")


class KnowledgeUpdate(BaseModel):
    title: str | None = Field(default=None, max_length=300)
    text: str | None = Field(default=None, min_length=1, max_length=200_000)


class SearchBody(BaseModel):
    query: str = Field(min_length=1, max_length=300)
    top_k: int = Field(default=5, ge=1, le=10)


KB_WRITERS = ("owner", "admin")
KB_READERS = ("owner", "admin", "manager")


@router.get("/knowledge")
def list_knowledge(profile: Profile = Depends(require_roles(*KB_READERS)), db: Session = Depends(get_db)):
    items = db.scalars(select(KnowledgeItem).where(KnowledgeItem.status == "active").order_by(KnowledgeItem.created_at.desc())).all()
    return {"data": [knowledge_out(i) for i in items]}


@router.post("/knowledge", status_code=201)
def add_knowledge(body: KnowledgeCreate, profile: Profile = Depends(require_roles(*KB_WRITERS)), db: Session = Depends(get_db)):
    item = kb.create_item(db, body.title, body.text, body.kind, "pasted", profile.id)
    write_audit(db, profile.id, "knowledge.create", "knowledge_item", str(item.id), after={"title": item.title, "chunks": len(item.chunks)})
    db.commit()
    return {"data": knowledge_out(item)}


@router.patch("/knowledge/{item_id}")
def edit_knowledge(item_id: uuid.UUID, body: KnowledgeUpdate, profile: Profile = Depends(require_roles(*KB_WRITERS)), db: Session = Depends(get_db)):
    item = db.get(KnowledgeItem, item_id)
    if item is None or item.status != "active":
        raise AppError("not_found", "Knowledge item not found.", 404)
    before = {"title": item.title}
    if body.title:
        item.title = body.title
    if body.text:
        kb.replace_text(db, item, body.text, profile.id)
    write_audit(db, profile.id, "knowledge.update", "knowledge_item", str(item.id), before=before, after={"title": item.title})
    db.commit()
    return {"data": knowledge_out(item)}


@router.delete("/knowledge/{item_id}", status_code=204)
def archive_knowledge(item_id: uuid.UUID, profile: Profile = Depends(require_roles(*KB_WRITERS)), db: Session = Depends(get_db)):
    item = db.get(KnowledgeItem, item_id)
    if item is None:
        raise AppError("not_found", "Knowledge item not found.", 404)
    item.status = "archived"
    write_audit(db, profile.id, "knowledge.archive", "knowledge_item", str(item.id))
    db.commit()


@router.post("/knowledge/test-search")
def test_search(body: SearchBody, profile: Profile = Depends(require_roles(*KB_READERS)), db: Session = Depends(get_db)):
    hits = kb.search(db, body.query, body.top_k)
    return {"data": [{"chunk_id": str(c.id), "title": c.item.title, "score": round(s, 3), "content": c.content} for c, s in hits]}
