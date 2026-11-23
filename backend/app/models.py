"""Database tables for the AI Sales Executive MVP (Day 1 + Day 2 sets).
Conventions: UUID primary keys, UTC timestamps, E.164 phone numbers, soft delete where noted.
"""
import uuid
from datetime import datetime, timezone

from sqlalchemy import (
    JSON,
    Boolean,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    Uuid,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db import Base

JSONType = JSON().with_variant(JSONB(), "postgresql")


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def new_id() -> uuid.UUID:
    return uuid.uuid4()


class TimestampMixin:
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow)


class Profile(Base, TimestampMixin):
    __tablename__ = "profiles"
    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True)  # equals the Supabase auth user id
    full_name: Mapped[str] = mapped_column(String(200), default="")
    email: Mapped[str | None] = mapped_column(String(320), unique=True)
    phone_e164: Mapped[str | None] = mapped_column(String(20))
    role: Mapped[str] = mapped_column(String(20), default="sales")  # owner|manager|sales|admin
    whatsapp_alerts_enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    active: Mapped[bool] = mapped_column(Boolean, default=True)


class Lead(Base, TimestampMixin):
    __tablename__ = "leads"
    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=new_id)
    name: Mapped[str | None] = mapped_column(String(200))
    phone_e164: Mapped[str | None] = mapped_column(String(20), index=True)
    email: Mapped[str | None] = mapped_column(String(320), index=True)
    company: Mapped[str | None] = mapped_column(String(200))
    city: Mapped[str | None] = mapped_column(String(120))
    country: Mapped[str | None] = mapped_column(String(80))
    source: Mapped[str] = mapped_column(String(30), default="manual")
    source_detail: Mapped[str | None] = mapped_column(String(300))
    requirement: Mapped[str | None] = mapped_column(Text)
    budget_text: Mapped[str | None] = mapped_column(String(200))
    budget_amount: Mapped[float | None] = mapped_column(Float)
    budget_currency: Mapped[str | None] = mapped_column(String(8))
    timeline_text: Mapped[str | None] = mapped_column(String(200))
    location_text: Mapped[str | None] = mapped_column(String(200))
    deal_size_estimate: Mapped[float | None] = mapped_column(Float)
    lead_type: Mapped[str] = mapped_column(String(20), default="client")
    stage: Mapped[str] = mapped_column(String(20), default="new", index=True)
    grade: Mapped[str] = mapped_column(String(12), default="ungraded", index=True)
    grade_reason: Mapped[str | None] = mapped_column(Text)
    score_signals: Mapped[list] = mapped_column(JSONType, default=list)
    assigned_to: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("profiles.id"), index=True)
    human_takeover: Mapped[bool] = mapped_column(Boolean, default=False)
    ai_paused: Mapped[bool] = mapped_column(Boolean, default=False)
    opted_out_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    opt_out_channel: Mapped[str | None] = mapped_column(String(20))
    first_reply_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    first_response_seconds: Mapped[int | None] = mapped_column(Integer)
    last_inbound_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_outbound_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    next_followup_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), index=True)
    followup_step: Mapped[int] = mapped_column(Integer, default=0)
    tags: Mapped[list] = mapped_column(JSONType, default=list)
    notes: Mapped[str | None] = mapped_column(Text)
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    conversations: Mapped[list["Conversation"]] = relationship(back_populates="lead")


class Conversation(Base, TimestampMixin):
    __tablename__ = "conversations"
    __table_args__ = (UniqueConstraint("channel", "channel_thread_id", name="uq_conversation_thread"),)
    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=new_id)
    lead_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("leads.id"), index=True)
    channel: Mapped[str] = mapped_column(String(20))  # whatsapp|web_chat|email
    channel_thread_id: Mapped[str | None] = mapped_column(String(200))
    status: Mapped[str] = mapped_column(String(10), default="open")
    last_message_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    running_summary: Mapped[str | None] = mapped_column(Text)
    subject: Mapped[str | None] = mapped_column(String(300))  # email subject for threading

    lead: Mapped[Lead] = relationship(back_populates="conversations")


class Message(Base, TimestampMixin):
    __tablename__ = "messages"
    __table_args__ = (Index("ix_messages_conv_created", "conversation_id", "created_at"),)
    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=new_id)
    conversation_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("conversations.id"), index=True)
    lead_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("leads.id"), index=True)
    direction: Mapped[str] = mapped_column(String(10))  # inbound|outbound
    sender: Mapped[str] = mapped_column(String(10))  # lead|ai|human|system
    channel: Mapped[str] = mapped_column(String(20))
    body: Mapped[str] = mapped_column(Text)
    attachments: Mapped[list] = mapped_column(JSONType, default=list)
    status: Mapped[str] = mapped_column(String(20), default="received")
    provider_message_id: Mapped[str | None] = mapped_column(String(200), index=True)
    error: Mapped[str | None] = mapped_column(Text)
    approved_by: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("profiles.id"))
    approved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    ai_run_id: Mapped[uuid.UUID | None] = mapped_column(Uuid)
    is_quote: Mapped[bool] = mapped_column(Boolean, default=False)


class AiRun(Base, TimestampMixin):
    __tablename__ = "ai_runs"
    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=new_id)
    lead_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("leads.id"), index=True)
    conversation_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("conversations.id"))
    trigger: Mapped[str] = mapped_column(String(30))  # inbound_message|new_lead|followup|manual
    model: Mapped[str] = mapped_column(String(80))
    prompt_version: Mapped[str] = mapped_column(String(40), default="v1")
    input_tokens: Mapped[int] = mapped_column(Integer, default=0)
    output_tokens: Mapped[int] = mapped_column(Integer, default=0)
    latency_ms: Mapped[int] = mapped_column(Integer, default=0)
    tool_calls: Mapped[list] = mapped_column(JSONType, default=list)
    retrieved_chunk_ids: Mapped[list] = mapped_column(JSONType, default=list)
    decision: Mapped[str] = mapped_column(String(30), default="reply")
    guardrail_flags: Mapped[dict] = mapped_column(JSONType, default=dict)
    error: Mapped[str | None] = mapped_column(Text)


class KnowledgeItem(Base, TimestampMixin):
    __tablename__ = "knowledge_items"
    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=new_id)
    title: Mapped[str] = mapped_column(String(300))
    kind: Mapped[str] = mapped_column(String(20), default="other")  # product|faq|policy|transcript|other
    source: Mapped[str] = mapped_column(String(10), default="pasted")
    file_key: Mapped[str | None] = mapped_column(String(500))
    raw_text: Mapped[str] = mapped_column(Text, default="")
    status: Mapped[str] = mapped_column(String(10), default="active")
    updated_by: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("profiles.id"))

    chunks: Mapped[list["KnowledgeChunk"]] = relationship(
        back_populates="item", cascade="all, delete-orphan"
    )


class KnowledgeChunk(Base, TimestampMixin):
    __tablename__ = "knowledge_chunks"
    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=new_id)
    knowledge_item_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("knowledge_items.id"), index=True)
    chunk_index: Mapped[int] = mapped_column(Integer)
    content: Mapped[str] = mapped_column(Text)
    token_count: Mapped[int] = mapped_column(Integer, default=0)
    # The embedding vector column is added by a later migration once the embeddings
    # provider is chosen. Until then search uses keyword scoring (see services/knowledge.py).

    item: Mapped[KnowledgeItem] = relationship(back_populates="chunks")


class WebhookEvent(Base, TimestampMixin):
    __tablename__ = "webhook_events"
    __table_args__ = (UniqueConstraint("provider", "event_id", name="uq_webhook_event"),)
    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=new_id)
    provider: Mapped[str] = mapped_column(String(30))
    event_id: Mapped[str] = mapped_column(String(200))
    payload: Mapped[dict] = mapped_column(JSONType, default=dict)
    processed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    error: Mapped[str | None] = mapped_column(Text)


class OptOut(Base, TimestampMixin):
    __tablename__ = "opt_outs"
    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=new_id)
    phone_e164: Mapped[str | None] = mapped_column(String(20), index=True)
    email: Mapped[str | None] = mapped_column(String(320), index=True)
    channel: Mapped[str] = mapped_column(String(20))
    source: Mapped[str] = mapped_column(String(20))  # stop_reply|unsubscribe_link|manual


class Setting(Base, TimestampMixin):
    __tablename__ = "settings"
    key: Mapped[str] = mapped_column(String(80), primary_key=True)
    value: Mapped[dict] = mapped_column(JSONType, default=dict)


class AuditLog(Base):
    __tablename__ = "audit_logs"
    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=new_id)
    at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, index=True)
    actor_id: Mapped[uuid.UUID | None] = mapped_column(Uuid)
    action: Mapped[str] = mapped_column(String(60))
    target_type: Mapped[str] = mapped_column(String(40))
    target_id: Mapped[str | None] = mapped_column(String(60))
    before: Mapped[dict | None] = mapped_column(JSONType)
    after: Mapped[dict | None] = mapped_column(JSONType)


class Product(Base, TimestampMixin):
    __tablename__ = "products"
    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=new_id)
    name: Mapped[str] = mapped_column(String(200), unique=True)
    description: Mapped[str | None] = mapped_column(Text)
    unit: Mapped[str] = mapped_column(String(30), default="unit")
    currency: Mapped[str] = mapped_column(String(8), default="INR")
    attributes: Mapped[dict] = mapped_column(JSONType, default=dict)  # declared inputs, e.g. {"width": "number"}
    active: Mapped[bool] = mapped_column(Boolean, default=True)

    rules: Mapped[list["PriceRule"]] = relationship(back_populates="product", cascade="all, delete-orphan")


class PriceRule(Base, TimestampMixin):
    __tablename__ = "price_rules"
    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=new_id)
    product_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("products.id"), index=True)
    rule_type: Mapped[str] = mapped_column(String(12))  # fixed|per_unit|tiered|formula
    min_qty: Mapped[float] = mapped_column(Float, default=0)
    max_qty: Mapped[float | None] = mapped_column(Float)
    amount: Mapped[str | None] = mapped_column(String(40))  # Decimal as text (fixed, per_unit)
    tiers: Mapped[list] = mapped_column(JSONType, default=list)  # [{"up_to": 10, "unit_price": "5"}]
    formula: Mapped[str | None] = mapped_column(String(300))
    note: Mapped[str | None] = mapped_column(String(300))
    active: Mapped[bool] = mapped_column(Boolean, default=True)

    product: Mapped[Product] = relationship(back_populates="rules")


class Followup(Base, TimestampMixin):
    __tablename__ = "followups"
    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=new_id)
    lead_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("leads.id"), index=True)
    step: Mapped[int] = mapped_column(Integer)  # 1,2,3 -> day 1,3,7
    day_offset: Mapped[int] = mapped_column(Integer)
    due_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    status: Mapped[str] = mapped_column(String(12), default="scheduled")  # scheduled|sent|skipped|failed
    skip_reason: Mapped[str | None] = mapped_column(String(40))
    sent_message_id: Mapped[uuid.UUID | None] = mapped_column(Uuid)
    done_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class Quotation(Base, TimestampMixin):
    __tablename__ = "quotations"
    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=new_id)
    lead_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("leads.id"), index=True)
    conversation_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("conversations.id"))
    product_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("products.id"))
    product_name: Mapped[str] = mapped_column(String(200))
    quantity: Mapped[float] = mapped_column(Float)
    attributes: Mapped[dict] = mapped_column(JSONType, default=dict)
    unit_price: Mapped[str] = mapped_column(String(40))
    total: Mapped[str] = mapped_column(String(40))
    currency: Mapped[str] = mapped_column(String(8), default="INR")
    cover_message: Mapped[str] = mapped_column(Text, default="")
    status: Mapped[str] = mapped_column(String(12), default="pending")  # pending|approved|rejected|sent
    message_id: Mapped[uuid.UUID | None] = mapped_column(Uuid)
    decided_by: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("profiles.id"))
    decided_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    reject_reason: Mapped[str | None] = mapped_column(Text)


class Handoff(Base, TimestampMixin):
    __tablename__ = "handoffs"
    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=new_id)
    lead_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("leads.id"), index=True)
    reason: Mapped[str] = mapped_column(String(300))
    summary: Mapped[str] = mapped_column(Text, default="")
    assigned_to: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("profiles.id"))
    status: Mapped[str] = mapped_column(String(12), default="open")  # open|resolved
    resolved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class Notification(Base, TimestampMixin):
    __tablename__ = "notifications"
    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=new_id)
    profile_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("profiles.id"), index=True)
    lead_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("leads.id"))
    kind: Mapped[str] = mapped_column(String(30))  # hot_lead|handoff|quote_approval|draft_approval|guardrail
    title: Mapped[str] = mapped_column(String(300))
    body: Mapped[str] = mapped_column(Text, default="")
    whatsapp_status: Mapped[str | None] = mapped_column(String(20))  # sent|failed|skipped
    read_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class WebhookOutbox(Base, TimestampMixin):
    """Lead events waiting to be POSTed to the owner's export webhook (with retry)."""

    __tablename__ = "webhook_outbox"
    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=new_id)
    event: Mapped[str] = mapped_column(String(30))  # lead.created | lead.stage_changed | test
    lead_id: Mapped[uuid.UUID | None] = mapped_column(Uuid, index=True)
    payload: Mapped[dict] = mapped_column(JSONType, default=dict)
    status: Mapped[str] = mapped_column(String(10), default="pending", index=True)  # pending|sent|failed
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    next_attempt_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    last_error: Mapped[str | None] = mapped_column(Text)
