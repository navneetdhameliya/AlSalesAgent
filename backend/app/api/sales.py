"""Sales workflow API (authenticated): takeover, assignment, approvals, quotations, products, price rules, handoffs, notifications."""
import uuid
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel, Field
from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from app.api.serializers import message_out
from app.audit import write_audit
from app.auth import current_profile, require_roles
from app.db import get_db
from app.errors import AppError
from app.models import Handoff, Lead, Message, Notification, PriceRule, Product, Profile, Quotation
from app.services import followups, handoff as handoff_svc, messaging, notifications, quotes
from app.services.leads import is_opted_out
from app.services.pricing import PriceError, compute_price, evaluate_formula

router = APIRouter(tags=["sales"])
MANAGERS = ("owner", "manager")  # may assign leads and approve quotations
STAFF = ("owner", "manager", "sales")  # work with leads
ALL_ROLES = ("owner", "admin", "manager", "sales")  # catalogue and notifications


def _lead(db: Session, profile: Profile, lead_id: uuid.UUID) -> Lead:
    lead = db.get(Lead, lead_id)
    if lead is None or lead.deleted_at is not None or (profile.role == "sales" and lead.assigned_to != profile.id):
        raise AppError("not_found", "Lead not found.", 404)
    return lead


# ---- Takeover, assignment, AI pause, human messages ---------------------------------------

@router.post("/leads/{lead_id}/takeover")
def takeover(lead_id: uuid.UUID, profile: Profile = Depends(require_roles(*STAFF)), db: Session = Depends(get_db)):
    """A human takes over. From now on the AI only writes drafts that need approval."""
    lead = _lead(db, profile, lead_id)
    lead.human_takeover = True
    if lead.assigned_to is None:
        lead.assigned_to = profile.id
    followups.cancel_pending(db, lead, "human_takeover")
    write_audit(db, profile.id, "lead.takeover", "lead", str(lead.id), None, {"assigned_to": str(lead.assigned_to)})
    db.commit()
    return {"data": {"human_takeover": True, "assigned_to": str(lead.assigned_to)}}


@router.post("/leads/{lead_id}/release")
def release(lead_id: uuid.UUID, profile: Profile = Depends(require_roles(*STAFF)), db: Session = Depends(get_db)):
    lead = _lead(db, profile, lead_id)
    lead.human_takeover = False
    write_audit(db, profile.id, "lead.release", "lead", str(lead.id), None, None)
    db.commit()
    return {"data": {"human_takeover": False}}


class AssignBody(BaseModel):
    profile_id: uuid.UUID


@router.post("/leads/{lead_id}/assign")
def assign(lead_id: uuid.UUID, body: AssignBody, profile: Profile = Depends(require_roles(*MANAGERS)), db: Session = Depends(get_db)):
    lead = db.get(Lead, lead_id)
    if lead is None or lead.deleted_at is not None:
        raise AppError("not_found", "Lead not found.", 404)
    target = db.get(Profile, body.profile_id)
    if target is None or not target.active:
        raise AppError("validation_error", "That team member does not exist or is inactive.", 422, {"profile_id": "Unknown or inactive."})
    before = str(lead.assigned_to) if lead.assigned_to else None
    lead.assigned_to = target.id
    write_audit(db, profile.id, "lead.assign", "lead", str(lead.id), {"assigned_to": before}, {"assigned_to": str(target.id)})
    notifications.notify(
        db, "assignment", f"Lead assigned to you: {lead.name or lead.phone_e164 or lead.email or 'lead'}",
        f"{handoff_svc.build_summary(db, lead)}\n{notifications.lead_link(lead)}", lead, [target],
    )
    db.commit()
    return {"data": {"assigned_to": str(target.id)}}


class PauseBody(BaseModel):
    paused: bool


@router.post("/leads/{lead_id}/ai-pause")
def ai_pause(lead_id: uuid.UUID, body: PauseBody, profile: Profile = Depends(require_roles(*STAFF)), db: Session = Depends(get_db)):
    lead = _lead(db, profile, lead_id)
    lead.ai_paused = body.paused
    if body.paused:
        followups.cancel_pending(db, lead, "ai_paused")
    write_audit(db, profile.id, "lead.ai_pause", "lead", str(lead.id), None, {"paused": body.paused})
    db.commit()
    return {"data": {"ai_paused": lead.ai_paused}}


class HumanMessage(BaseModel):
    text: str = Field(min_length=1, max_length=4000)


@router.post("/leads/{lead_id}/send", status_code=201)
def send_human_message(lead_id: uuid.UUID, body: HumanMessage, profile: Profile = Depends(require_roles(*STAFF)), db: Session = Depends(get_db)):
    lead = _lead(db, profile, lead_id)
    conv = followups.latest_conversation(db, lead)
    if conv is None:
        raise AppError("no_conversation", "This lead has no conversation to reply in.", 409)
    if is_opted_out(db, lead, conv.channel):
        raise AppError("opted_out", "This lead has opted out. Nothing can be sent.", 409)
    msg = messaging.queue_outbound(db, conv, lead, body.text.strip(), sender="human")
    msg.approved_by, msg.approved_at = profile.id, datetime.now(timezone.utc)
    messaging.deliver(db, msg, conv, lead)
    db.commit()
    return {"data": message_out(msg)}


# ---- Approval queue: AI drafts (takeover mode) and quotations -------------------------------

def quotation_out(q: Quotation) -> dict:
    return {
        "id": str(q.id),
        "lead_id": str(q.lead_id),
        "product": q.product_name,
        "quantity": q.quantity,
        "attributes": q.attributes,
        "unit_price": q.unit_price,
        "total": q.total,
        "currency": q.currency,
        "cover_message": q.cover_message,
        "preview": quotes.render(q),
        "status": q.status,
        "reject_reason": q.reject_reason,
        "created_at": q.created_at.isoformat() if q.created_at else None,
    }


@router.get("/approvals")
def approvals(profile: Profile = Depends(require_roles(*STAFF)), db: Session = Depends(get_db)):
    drafts_q = select(Message).where(Message.status == "pending_approval").order_by(Message.created_at)
    quotes_q = select(Quotation).where(Quotation.status == "pending").order_by(Quotation.created_at)
    if profile.role == "sales":
        mine = select(Lead.id).where(Lead.assigned_to == profile.id)
        drafts_q = drafts_q.where(Message.lead_id.in_(mine))
        quotes_q = quotes_q.where(Quotation.lead_id.in_(mine))
    return {
        "data": {
            "drafts": [message_out(m) | {"lead_id": str(m.lead_id)} for m in db.scalars(drafts_q).all()],
            "quotations": [quotation_out(q) for q in db.scalars(quotes_q).all()],
        }
    }


class DraftApproval(BaseModel):
    text: str | None = Field(default=None, min_length=1, max_length=4000)


def _pending_draft(db: Session, profile: Profile, message_id: uuid.UUID) -> Message:
    msg = db.get(Message, message_id)
    if msg is None:
        raise AppError("not_found", "Message not found.", 404)
    _lead(db, profile, msg.lead_id)  # also enforces the sales-role scope
    if msg.status != "pending_approval":
        raise AppError("invalid_state", f"This message is already {msg.status}.", 409)
    return msg


@router.post("/messages/{message_id}/approve")
def approve_draft(message_id: uuid.UUID, body: DraftApproval, profile: Profile = Depends(require_roles(*STAFF)), db: Session = Depends(get_db)):
    msg = _pending_draft(db, profile, message_id)
    lead = db.get(Lead, msg.lead_id)
    from app.models import Conversation

    conv = db.get(Conversation, msg.conversation_id)
    if is_opted_out(db, lead, conv.channel):
        raise AppError("opted_out", "This lead has opted out. Nothing can be sent.", 409)
    if body.text and body.text.strip() != msg.body:
        msg.body, msg.sender = body.text.strip(), "human"  # edited by a person
    msg.approved_by, msg.approved_at, msg.status = profile.id, datetime.now(timezone.utc), "queued"
    messaging.deliver(db, msg, conv, lead)
    write_audit(db, profile.id, "message.approve", "message", str(msg.id), None, {"status": msg.status})
    db.commit()
    return {"data": message_out(msg)}


@router.post("/messages/{message_id}/reject")
def reject_draft(message_id: uuid.UUID, profile: Profile = Depends(require_roles(*STAFF)), db: Session = Depends(get_db)):
    msg = _pending_draft(db, profile, message_id)
    msg.status = "rejected"
    write_audit(db, profile.id, "message.reject", "message", str(msg.id), None, None)
    db.commit()
    return {"data": message_out(msg)}


@router.get("/quotations")
def list_quotations(status: str | None = None, profile: Profile = Depends(require_roles(*STAFF)), db: Session = Depends(get_db)):
    q = select(Quotation).order_by(Quotation.created_at.desc())
    if status:
        q = q.where(Quotation.status == status)
    if profile.role == "sales":
        q = q.where(Quotation.lead_id.in_(select(Lead.id).where(Lead.assigned_to == profile.id)))
    return {"data": [quotation_out(x) for x in db.scalars(q).all()]}


class QuoteApproval(BaseModel):
    cover_message: str | None = Field(default=None, max_length=1000)


class QuoteRejection(BaseModel):
    reason: str | None = Field(default=None, max_length=500)


def _quotation(db: Session, quotation_id: uuid.UUID) -> Quotation:
    q = db.get(Quotation, quotation_id)
    if q is None:
        raise AppError("not_found", "Quotation not found.", 404)
    return q


@router.post("/quotations/{quotation_id}/approve")
def approve_quotation(quotation_id: uuid.UUID, body: QuoteApproval, profile: Profile = Depends(require_roles(*MANAGERS)), db: Session = Depends(get_db)):
    q = _quotation(db, quotation_id)
    lead = db.get(Lead, q.lead_id)
    from app.models import Conversation

    conv = db.get(Conversation, q.conversation_id)
    if is_opted_out(db, lead, conv.channel):
        raise AppError("opted_out", "This lead has opted out. The quotation cannot be sent.", 409)
    msg = quotes.approve(db, q, profile.id, body.cover_message)
    write_audit(db, profile.id, "quotation.approve", "quotation", str(q.id), None, {"status": q.status, "message_status": msg.status})
    db.commit()
    return {"data": quotation_out(q) | {"message": message_out(msg)}}


@router.post("/quotations/{quotation_id}/reject")
def reject_quotation(quotation_id: uuid.UUID, body: QuoteRejection, profile: Profile = Depends(require_roles(*MANAGERS)), db: Session = Depends(get_db)):
    q = _quotation(db, quotation_id)
    quotes.reject(db, q, profile.id, body.reason)
    write_audit(db, profile.id, "quotation.reject", "quotation", str(q.id), None, {"reason": body.reason})
    db.commit()
    return {"data": quotation_out(q)}


# ---- Products and price rules ----------------------------------------------------------------

def product_out(p: Product) -> dict:
    return {
        "id": str(p.id),
        "name": p.name,
        "description": p.description,
        "unit": p.unit,
        "currency": p.currency,
        "attributes": p.attributes or {},
        "active": p.active,
        "rules": [rule_out(r) for r in p.rules],
    }


def rule_out(r: PriceRule) -> dict:
    return {
        "id": str(r.id),
        "rule_type": r.rule_type,
        "min_qty": r.min_qty,
        "max_qty": r.max_qty,
        "amount": r.amount,
        "tiers": r.tiers or [],
        "formula": r.formula,
        "note": r.note,
        "active": r.active,
    }


class ProductBody(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    description: str | None = Field(default=None, max_length=2000)
    unit: str = Field(default="unit", max_length=30)
    currency: str = Field(default="INR", min_length=3, max_length=8)
    attributes: dict[str, str] = Field(default_factory=dict, description='Declared formula inputs, e.g. {"width": "number"}')


class ProductPatch(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=200)
    description: str | None = None
    unit: str | None = None
    currency: str | None = None
    attributes: dict[str, str] | None = None
    active: bool | None = None


@router.get("/products")
def list_products(include_inactive: bool = False, _p: Profile = Depends(require_roles(*ALL_ROLES)), db: Session = Depends(get_db)):
    q = select(Product).order_by(Product.name)
    if not include_inactive:
        q = q.where(Product.active.is_(True))
    return {"data": [product_out(p) for p in db.scalars(q).all()]}


@router.post("/products", status_code=201)
def create_product(body: ProductBody, profile: Profile = Depends(require_roles("owner", "admin")), db: Session = Depends(get_db)):
    if db.scalar(select(Product.id).where(Product.name == body.name)):
        raise AppError("conflict", "A product with this name already exists.", 409)
    p = Product(**body.model_dump())
    db.add(p)
    db.flush()
    write_audit(db, profile.id, "product.create", "product", str(p.id), None, {"name": p.name})
    db.commit()
    return {"data": product_out(p)}


@router.patch("/products/{product_id}")
def update_product(product_id: uuid.UUID, body: ProductPatch, profile: Profile = Depends(require_roles("owner", "admin")), db: Session = Depends(get_db)):
    p = db.get(Product, product_id)
    if p is None:
        raise AppError("not_found", "Product not found.", 404)
    for k, v in body.model_dump(exclude_unset=True).items():
        setattr(p, k, v)
    write_audit(db, profile.id, "product.update", "product", str(p.id), None, body.model_dump(exclude_unset=True))
    db.commit()
    return {"data": product_out(p)}


class RuleBody(BaseModel):
    rule_type: str = Field(pattern="^(fixed|per_unit|tiered|formula)$")
    min_qty: float = Field(default=0, ge=0)
    max_qty: float | None = Field(default=None, gt=0)
    amount: str | None = None
    tiers: list[dict] = Field(default_factory=list)
    formula: str | None = Field(default=None, max_length=300)
    note: str | None = Field(default=None, max_length=300)
    active: bool = True


def _validate_rule(body: RuleBody, product: Product) -> None:
    def bad(field: str, msg: str):
        raise AppError("validation_error", msg, 422, {field: msg})

    if body.max_qty is not None and body.max_qty < body.min_qty:
        bad("max_qty", "max_qty must not be below min_qty.")
    if body.rule_type in ("fixed", "per_unit"):
        try:
            if Decimal(str(body.amount)) < 0:
                bad("amount", "Amount must not be negative.")
        except (InvalidOperation, ValueError):
            bad("amount", "Amount must be a number.")
    elif body.rule_type == "tiered":
        if not body.tiers:
            bad("tiers", "At least one tier is required.")
        try:
            for t in body.tiers:
                Decimal(str(t["unit_price"]))
                if t.get("up_to") is not None:
                    Decimal(str(t["up_to"]))
        except (InvalidOperation, ValueError, KeyError):
            bad("tiers", 'Each tier needs "unit_price" and an optional "up_to", both numbers.')
    else:
        if not body.formula:
            bad("formula", "A formula is required.")
        try:  # a trial run with every declared input set to 1 catches syntax errors and unknown names
            evaluate_formula(body.formula, {"qty": 1, **{k: 1 for k in (product.attributes or {})}})
        except PriceError as e:
            bad("formula", f"Formula rejected: {e}")


@router.post("/products/{product_id}/rules", status_code=201)
def add_rule(product_id: uuid.UUID, body: RuleBody, profile: Profile = Depends(require_roles("owner", "admin")), db: Session = Depends(get_db)):
    p = db.get(Product, product_id)
    if p is None:
        raise AppError("not_found", "Product not found.", 404)
    _validate_rule(body, p)
    r = PriceRule(product_id=p.id, **body.model_dump())
    db.add(r)
    db.flush()
    write_audit(db, profile.id, "price_rule.create", "price_rule", str(r.id), None, body.model_dump())
    db.commit()
    return {"data": rule_out(r)}


@router.delete("/price-rules/{rule_id}")
def deactivate_rule(rule_id: uuid.UUID, profile: Profile = Depends(require_roles("owner", "admin")), db: Session = Depends(get_db)):
    r = db.get(PriceRule, rule_id)
    if r is None:
        raise AppError("not_found", "Rule not found.", 404)
    r.active = False
    write_audit(db, profile.id, "price_rule.deactivate", "price_rule", str(r.id), None, None)
    db.commit()
    return {"data": rule_out(r)}


class PriceTest(BaseModel):
    product: str
    quantity: float
    attributes: dict = Field(default_factory=dict)


@router.post("/price/test")
def price_test(body: PriceTest, _p: Profile = Depends(require_roles(*ALL_ROLES)), db: Session = Depends(get_db)):
    try:
        return {"data": compute_price(db, body.product, body.quantity, body.attributes).as_dict()}
    except PriceError as e:
        raise AppError("price_error", str(e), 422)


# ---- Handoffs and notifications --------------------------------------------------------------

def handoff_out(h: Handoff) -> dict:
    return {
        "id": str(h.id),
        "lead_id": str(h.lead_id),
        "reason": h.reason,
        "summary": h.summary,
        "assigned_to": str(h.assigned_to) if h.assigned_to else None,
        "status": h.status,
        "created_at": h.created_at.isoformat() if h.created_at else None,
    }


@router.get("/handoffs")
def list_handoffs(status: str | None = "open", profile: Profile = Depends(require_roles(*STAFF)), db: Session = Depends(get_db)):
    q = select(Handoff).order_by(Handoff.created_at.desc())
    if status:
        q = q.where(Handoff.status == status)
    if profile.role == "sales":
        q = q.where(Handoff.lead_id.in_(select(Lead.id).where(Lead.assigned_to == profile.id)))
    return {"data": [handoff_out(h) for h in db.scalars(q).all()]}


@router.post("/handoffs/{handoff_id}/resolve")
def resolve_handoff(handoff_id: uuid.UUID, profile: Profile = Depends(require_roles(*STAFF)), db: Session = Depends(get_db)):
    h = db.get(Handoff, handoff_id)
    if h is None:
        raise AppError("not_found", "Handoff not found.", 404)
    _lead(db, profile, h.lead_id)
    handoff_svc.resolve_handoff(db, h)
    write_audit(db, profile.id, "handoff.resolve", "handoff", str(h.id), None, None)
    db.commit()
    return {"data": handoff_out(h)}


def notification_out(n: Notification) -> dict:
    return {
        "id": str(n.id),
        "kind": n.kind,
        "title": n.title,
        "body": n.body,
        "lead_id": str(n.lead_id) if n.lead_id else None,
        "whatsapp_status": n.whatsapp_status,
        "read": n.read_at is not None,
        "created_at": n.created_at.isoformat() if n.created_at else None,
    }


@router.get("/notifications")
def list_notifications(unread_only: bool = False, limit: int = Query(50, ge=1, le=200), profile: Profile = Depends(require_roles(*ALL_ROLES)), db: Session = Depends(get_db)):
    cond = Notification.profile_id == profile.id
    if profile.role in MANAGERS or profile.role == "admin":
        cond = or_(cond, Notification.profile_id.is_(None))  # alerts nobody could be sent to
    q = select(Notification).where(cond).order_by(Notification.created_at.desc()).limit(limit)
    if unread_only:
        q = q.where(Notification.read_at.is_(None))
    return {"data": [notification_out(n) for n in db.scalars(q).all()]}


@router.post("/notifications/{notification_id}/read")
def mark_read(notification_id: uuid.UUID, profile: Profile = Depends(require_roles(*ALL_ROLES)), db: Session = Depends(get_db)):
    n = db.get(Notification, notification_id)
    if n is None or (n.profile_id not in (profile.id, None)):
        raise AppError("not_found", "Notification not found.", 404)
    n.read_at = datetime.now(timezone.utc)
    db.commit()
    return {"data": notification_out(n)}
