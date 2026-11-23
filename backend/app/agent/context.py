"""Per-run state shared between the agent loop, the tools and the guardrails."""
from dataclasses import dataclass, field

from app.models import Conversation
from app.services.pricing import PriceResult


@dataclass
class RunContext:
    conversation: Conversation
    inbound_texts: list[str] = field(default_factory=list)  # recent customer messages, oldest first
    knowledge_text: list[str] = field(default_factory=list)  # passages retrieved in this run
    price_results: list[PriceResult] = field(default_factory=list)
    quotation_ids: list[str] = field(default_factory=list)
    quote_totals: set[str] = field(default_factory=set)  # amounts that must not be shown before approval
    handoff_reasons: list[str] = field(default_factory=list)
    stop_requested: bool = False
    meeting_requested: bool = False

    @property
    def channel(self) -> str:
        return self.conversation.channel

    @property
    def last_inbound(self) -> str:
        return self.inbound_texts[-1] if self.inbound_texts else ""
