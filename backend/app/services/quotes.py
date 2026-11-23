"""Quotations: drafted by the AI, priced by the server, sent only after a human approves."""
import uuid
from datetime import datetime, timezone
from decimal import Decimal

from sqlalchemy.orm import Session

from app.errors import AppError
from app.models import Conversation, Lead, Message, Quotation
from app.services import messaging, notifications
from app.services.pricing import PriceResult, format_money


def create_pending(db: Session, lead: Lead, conv: Conversation, price: PriceResult, attributes: dict, cover: str) -> Quotation:
    q = Quotation(
        lead_id=lead.id,
        conversation_id=conv.id,
        product_id=uuid.UUID(price.product_id),
        product_name=price.product_name,
        quantity=float(price.quantity),
        attributes=attributes or {},
        unit_price=str(price.unit_price),
        total=str(price.total),
        currency=price.currency,
        cover_message=cover.strip(),
        status="pending",
    )
    db.add(q)
    db.flush()
    notifications.notify(
        db,
        "quote_approval",
        f"Quotation to approve for {lead.name or lead.phone_e164 or lead.email or 'a lead'}",
        f"{price.product_name} x {price.quantity}: {format_money(price.total, price.currency)}\n{notifications.lead_link(lead)}",
        lead,
    )
    return q


def render(q: Quotation) -> str:
    qty = Decimal(str(q.quantity)).normalize()
    lines = [q.cover_message] if q.cover_message else []
    lines.append(
        f"Quotation: {q.product_name}, quantity {qty:f}, "
        f"unit price {format_money(q.unit_price, q.currency)}, total {format_money(q.total, q.currency)}."
    )
    return "\n".join(lines)


def approve(db: Session, q: Quotation, approver_id, cover_message: str | None = None) -> Message:
    if q.status not in ("pending",):
        raise AppError("invalid_state", f"Quotation is already {q.status}.", status=409)
    if cover_message is not None:
        q.cover_message = cover_message.strip()
    lead = db.get(Lead, q.lead_id)
    conv = db.get(Conversation, q.conversation_id)
    now = datetime.now(timezone.utc)
    q.decided_by, q.decided_at, q.status = approver_id, now, "approved"
    msg = messaging.queue_outbound(db, conv, lead, render(q), sender="human", is_quote=True)
    msg.approved_by, msg.approved_at = approver_id, now
    messaging.deliver(db, msg, conv, lead)
    q.message_id = msg.id
    if msg.status == "sent":
        q.status = "sent"
    return msg


def reject(db: Session, q: Quotation, approver_id, reason: str | None) -> None:
    if q.status != "pending":
        raise AppError("invalid_state", f"Quotation is already {q.status}.", status=409)
    q.status, q.decided_by, q.decided_at, q.reject_reason = "rejected", approver_id, datetime.now(timezone.utc), reason
