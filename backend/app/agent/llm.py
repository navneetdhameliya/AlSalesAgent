"""LLM access. Claude is called from the backend only.

The runner talks to the small `LLM` interface so tests can use a scripted fake.
Content blocks use Anthropic's dict format: text, tool_use, tool_result.
"""
from dataclasses import dataclass, field
from typing import Protocol

from app.config import get_settings


@dataclass
class LLMResult:
    content: list[dict]
    stop_reason: str = "end_turn"
    input_tokens: int = 0
    output_tokens: int = 0


class LLM(Protocol):
    model: str

    def create(self, system: str, messages: list[dict], tools: list[dict]) -> LLMResult: ...


class AnthropicLLM:
    def __init__(self) -> None:
        import anthropic

        s = get_settings()
        if not s.anthropic_api_key:
            raise RuntimeError("ANTHROPIC_API_KEY is not set.")
        self.client = anthropic.Anthropic(api_key=s.anthropic_api_key, timeout=45.0, max_retries=2)
        self.model = s.claude_model

    def create(self, system: str, messages: list[dict], tools: list[dict]) -> LLMResult:
        resp = self.client.messages.create(
            model=self.model, max_tokens=700, system=system, messages=messages, tools=tools
        )
        content: list[dict] = []
        for block in resp.content:
            if block.type == "text":
                content.append({"type": "text", "text": block.text})
            elif block.type == "tool_use":
                content.append({"type": "tool_use", "id": block.id, "name": block.name, "input": dict(block.input)})
        return LLMResult(
            content=content,
            stop_reason=resp.stop_reason or "end_turn",
            input_tokens=resp.usage.input_tokens,
            output_tokens=resp.usage.output_tokens,
        )


@dataclass
class FakeLLM:
    """Scripted responses for tests. Each script item is a list of content blocks."""

    script: list[list[dict]] = field(default_factory=list)
    model: str = "fake-model"
    calls: list[dict] = field(default_factory=list)

    def create(self, system: str, messages: list[dict], tools: list[dict]) -> LLMResult:
        self.calls.append({"system": system, "messages": [dict(m) for m in messages]})
        content = self.script.pop(0) if self.script else [{"type": "text", "text": "Thank you for your message."}]
        has_tool = any(b["type"] == "tool_use" for b in content)
        return LLMResult(content=content, stop_reason="tool_use" if has_tool else "end_turn", input_tokens=10, output_tokens=5)


_override: LLM | None = None
_default: LLM | None = None


def set_llm(llm: LLM | None) -> None:
    global _override
    _override = llm


def get_llm() -> LLM:
    global _default
    if _override is not None:
        return _override
    if _default is None:
        _default = AnthropicLLM()
    return _default
