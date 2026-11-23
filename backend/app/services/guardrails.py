"""Pre-send checks on everything the AI writes to a customer.

The checks are deterministic. A reply that fails is regenerated once; if it fails again it is
not sent and the lead is handed to a human (see agent/runner.py).
"""
import re
from decimal import Decimal, InvalidOperation

from app.agent.context import RunContext

_NUM = r"\d[\d,]*(?:\.\d+)?"
_PREFIX = r"(?:[₹$€£]|\b(?:rs\.?|inr|usd|eur|gbp|aed))"
_SUFFIX = r"(?:₹|\b(?:rupees|rs|inr|usd|dollars|euros|eur|aed)\b)"
_SCALE = {"k": Decimal(1000), "lakh": Decimal(100000), "lakhs": Decimal(100000), "lac": Decimal(100000), "crore": Decimal(10000000)}

MONEY_RE = re.compile(
    rf"{_PREFIX}\s*({_NUM})\s*(k|lakhs?|lac|crore)?\b|({_NUM})\s*(k|lakhs?|lac|crore)?\s*{_SUFFIX}"
    rf"|\b({_NUM})\s*(k|lakhs?|lac|crore)\b",
    re.I,
)
NUMBER_RE = re.compile(rf"({_NUM})\s*(k|lakhs?|lac|crore)?\b", re.I)
DISCOUNT_RE = re.compile(
    r"\b(discount\w*|\d+\s*%\s*off|percent off|special (?:price|rate|offer)|lower price|reduce[d]? (?:the )?price|"
    r"best price|price match|negotiat\w*|cheaper|waive\w*|coupon|promo code)",
    re.I,
)
PERCENT_RE = re.compile(rf"({_NUM})\s*(?:%|per\s?cent|percent)", re.I)
TEAM_RE = re.compile(
    r"\b(team member|our team|sales team|colleague|specialist|account manager|someone from|a member of)", re.I
)
DELIVERY_WORD_RE = re.compile(
    r"\b(deliver\w*|ship\w*|dispatch\w*|lead time|turnaround|install\w*|ready (?:in|by|within)|complet\w+ (?:in|by|within))\b",
    re.I,
)
DURATION_RE = re.compile(
    r"\d+(?:\s*(?:-|–|to)\s*\d+)?\s*(?:working\s+|business\s+)?(?:days?|weeks?|months?|hours?)\b", re.I
)
DATE_WORD_RE = re.compile(
    r"\b(tomorrow|today|next week|next month|this week|by (?:mon|tues|wednes|thurs|fri|satur|sun)day|by the end of|asap|immediately)\b",
    re.I,
)
BOT_QUESTION_RE = re.compile(
    r"(are you|r u|am i (?:talking|chatting|speaking)|is this|is it|who am i).{0,30}(bot|robot|\bai\b|artificial|human|real person|machine|automated)"
    r"|\b(bot|robot)\b.*\?",
    re.I,
)
MAX_LINES = 4
MAX_CHARS_CHAT = 700
MAX_CHARS_EMAIL = 1500


def _dec(text: str) -> Decimal | None:
    try:
        return Decimal(text.replace(",", ""))
    except InvalidOperation:
        return None


def _norm(d: Decimal) -> Decimal:
    return d.normalize() if d != 0 else Decimal(0)


def numbers_in(text: str) -> set[Decimal]:
    """Every number in the text, including 50k / 2 lakh style amounts."""
    out: set[Decimal] = set()
    for m in NUMBER_RE.finditer(text or ""):
        d = _dec(m.group(1))
        if d is None:
            continue
        out.add(_norm(d))
        if m.group(2):
            out.add(_norm(d * _SCALE[m.group(2).lower()]))
    return out


def money_amounts(text: str) -> list[Decimal]:
    """Amounts in the text that are written as money."""
    found: list[Decimal] = []
    for m in MONEY_RE.finditer(text or ""):
        num = m.group(1) or m.group(3) or m.group(5)
        scale = m.group(2) or m.group(4) or m.group(6)
        d = _dec(num or "")
        if d is None:
            continue
        if scale:
            d *= _SCALE[scale.lower()]
        found.append(_norm(d))
    return found


def _squash(text: str) -> str:
    return re.sub(r"\s+", "", text.lower())


def check_text(text: str, ctx: RunContext, *, allow_amounts: bool = True) -> list[str]:
    """Return the list of rule violations in a customer-facing text (empty means it is safe)."""
    problems: list[str] = []
    body = text or ""
    knowledge = "\n".join(ctx.knowledge_text)

    # 1. Money must come from the price tool, retrieved knowledge or the customer's own words.
    allowed: set[Decimal] = set()
    for pr in ctx.price_results:
        allowed |= {_norm(pr.unit_price), _norm(pr.total)}
    allowed |= numbers_in(knowledge)
    allowed |= numbers_in("\n".join(ctx.inbound_texts))
    quote_blocked = {_norm(Decimal(a)) for a in ctx.quote_totals}
    for amount in money_amounts(body):
        if not allow_amounts:
            problems.append("The message must not contain any price; the system adds quotation figures itself.")
            break
        if amount in quote_blocked:
            problems.append("Do not show the quotation amount; it is sent only after a team member approves it.")
        elif amount not in allowed:
            problems.append(f"The amount {amount} is not from the price tool or the knowledge base. Remove it.")

    # 2. No discounts. Only a pointer to the team, with no figures, is allowed.
    if DISCOUNT_RE.search(body) and (not TEAM_RE.search(body) or re.search(r"\d", body)):
        problems.append("Do not mention discounts. Say a team member will discuss it, with no figures.")

    # 2b. A percentage the AI makes up is a discount in disguise. It may repeat one from the knowledge base or the customer.
    pct_allowed = numbers_in(knowledge) | numbers_in("\n".join(ctx.inbound_texts))
    for m in PERCENT_RE.finditer(body):
        value = _dec(m.group(1))
        if value is not None and _norm(value) not in pct_allowed:
            problems.append("Do not quote a percentage that is not in the knowledge base. Say a team member will discuss it.")
            break

    # 3. No delivery promises unless the knowledge states that duration.
    squashed_knowledge = _squash(knowledge)
    for sentence in re.split(r"(?<=[.!?])\s+|\n", body):
        if not sentence.strip() or sentence.strip().endswith("?") or not DELIVERY_WORD_RE.search(sentence):
            continue
        for m in list(DURATION_RE.finditer(sentence)) + list(DATE_WORD_RE.finditer(sentence)):
            if _squash(m.group(0)) not in squashed_knowledge:
                problems.append("Do not promise a delivery date or lead time. Say a team member will confirm it.")
                break
        else:
            continue
        break

    # 4. Length and format.
    if ctx.channel == "email":
        if len(body) > MAX_CHARS_EMAIL:
            problems.append(f"Too long. Keep emails under {MAX_CHARS_EMAIL} characters.")
    else:
        lines = [ln for ln in body.splitlines() if ln.strip()]
        if len(lines) > MAX_LINES or len(body) > MAX_CHARS_CHAT:
            problems.append(f"Too long. Use at most {MAX_LINES} short lines.")

    return problems


def check_reply(reply: str, ctx: RunContext) -> list[str]:
    problems = check_text(reply, ctx)
    # 5. Be honest about being an AI when asked.
    if BOT_QUESTION_RE.search(ctx.last_inbound) and not re.search(r"\bAI\b|artificial intelligence", reply):
        problems.append("The customer asked whether they are talking to a person. Say you are an AI assistant.")
    return problems
