"""Unit checks for the safe formula evaluator, price rules and guardrail rules."""
from decimal import Decimal
from types import SimpleNamespace

import pytest

from app.agent.context import RunContext
from app.models import PriceRule, Product
from app.services import guardrails
from app.services.pricing import PriceError, compute_price, evaluate_formula


# ---- formula evaluator ----------------------------------------------------------------------

def test_formula_does_arithmetic_with_declared_names():
    assert evaluate_formula("width * height * 450", {"width": 4, "height": 2}) == Decimal("3600")
    assert evaluate_formula("max(qty * 10, 100) + ceil(2.2)", {"qty": 3}) == Decimal("103")


@pytest.mark.parametrize(
    "expr",
    [
        "__import__('os').system('id')",
        "width.__class__",
        "[x for x in range(3)]",
        "open('/etc/passwd')",
        "2 ** 100",
        "9 ** 9 ** 9",
        "unknown_name * 2",
        "width // 0",
        "width / 0",
        "'a' * 3",
        "lambda: 1",
        "1 if width else 2",
        "x" * 300,
        "",
    ],
)
def test_formula_rejects_anything_unsafe(expr):
    with pytest.raises(PriceError):
        evaluate_formula(expr, {"width": 4})


# ---- rule selection and types ---------------------------------------------------------------

def make_product(db, name="Board", attributes=None):
    p = Product(name=name, currency="INR", attributes=attributes or {})
    db.add(p)
    db.flush()
    return p


def add_rule(db, p, **kw):
    r = PriceRule(product_id=p.id, **kw)
    db.add(r)
    db.commit()
    return r


def test_per_unit_tiered_fixed_and_formula(db):
    a = make_product(db, "Nameplate")
    add_rule(db, a, rule_type="tiered", min_qty=1, tiers=[{"up_to": 10, "unit_price": "200"}, {"up_to": 50, "unit_price": "150"}, {"up_to": None, "unit_price": "120"}])
    assert compute_price(db, "Nameplate", 5).total == Decimal("1000.00")
    assert compute_price(db, "Nameplate", 20).total == Decimal("3000.00")
    assert compute_price(db, "Nameplate", 100).total == Decimal("12000.00")

    b = make_product(db, "Install")
    add_rule(db, b, rule_type="fixed", min_qty=1, amount="2500")
    r = compute_price(db, "Install", 2)
    assert r.total == Decimal("2500.00") and r.unit_price == Decimal("1250.00")

    c = make_product(db, "Sign", {"width": "number", "height": "number"})
    add_rule(db, c, rule_type="formula", min_qty=1, formula="width * height * 450")
    r = compute_price(db, "Sign", 3, {"width": 4, "height": 2})
    assert r.unit_price == Decimal("3600.00") and r.total == Decimal("10800.00")
    with pytest.raises(PriceError, match="missing inputs"):
        compute_price(db, "Sign", 1, {"width": 4})


def test_highest_min_qty_wins_and_gaps_are_errors(db):
    p = make_product(db, "Bulk")
    add_rule(db, p, rule_type="per_unit", min_qty=1, amount="100")
    add_rule(db, p, rule_type="per_unit", min_qty=50, amount="80")
    assert compute_price(db, "Bulk", 10).unit_price == Decimal("100.00")
    assert compute_price(db, "Bulk", 60).unit_price == Decimal("80.00")
    with pytest.raises(PriceError, match="no price rule"):
        compute_price(db, "Bulk", 0.5)
    with pytest.raises(PriceError, match="greater than zero"):
        compute_price(db, "Bulk", 0)
    with pytest.raises(PriceError, match="not found"):
        compute_price(db, "Nothing", 1)


def test_inactive_rules_and_products_are_ignored(db):
    p = make_product(db, "Old")
    add_rule(db, p, rule_type="per_unit", min_qty=1, amount="100", active=False)
    with pytest.raises(PriceError, match="no price rule"):
        compute_price(db, "Old", 1)
    p.active = False
    db.commit()
    with pytest.raises(PriceError, match="not found"):
        compute_price(db, "Old", 1)


# ---- guardrails -----------------------------------------------------------------------------

def ctx(channel="whatsapp", inbound=(), knowledge=(), prices=(), quote_totals=()):
    return RunContext(
        conversation=SimpleNamespace(channel=channel),
        inbound_texts=list(inbound),
        knowledge_text=list(knowledge),
        price_results=list(prices),
        quote_totals=set(quote_totals),
    )


def test_invented_amounts_are_blocked_but_sourced_ones_pass():
    assert guardrails.check_text("The board costs Rs 5,000.", ctx())
    assert guardrails.check_text("It is about 50k for that.", ctx())
    assert not guardrails.check_text("Our nameplates start at INR 200.", ctx(knowledge=["Nameplates start at INR 200 each."]))
    assert not guardrails.check_text("You mentioned a budget of ₹50,000.", ctx(inbound=["my budget is 50k"]))
    price = SimpleNamespace(unit_price=Decimal("3600.00"), total=Decimal("10800.00"))
    assert not guardrails.check_text("That would be INR 10,800 in total.", ctx(prices=[price]))
    assert guardrails.check_text("That would be INR 10,900 in total.", ctx(prices=[price]))


def test_quotation_amounts_cannot_be_shown_before_approval():
    price = SimpleNamespace(unit_price=Decimal("3600.00"), total=Decimal("10800.00"))
    problems = guardrails.check_text("The total is INR 10,800.", ctx(prices=[price], quote_totals={"10800.00"}))
    assert any("approves" in p for p in problems)
    assert guardrails.check_text("Cover note with INR 500.", ctx(), allow_amounts=False)


def test_discount_language_needs_a_pointer_to_the_team_and_no_figures():
    assert guardrails.check_text("We can offer a discount on bulk orders.", ctx())
    assert guardrails.check_text("I can give you 10% off.", ctx())
    assert guardrails.check_text("A team member will discuss a discount of 10 percent.", ctx())
    assert not guardrails.check_text("A team member will discuss any discount request with you.", ctx())


def test_delivery_promises_need_support_in_knowledge():
    assert guardrails.check_text("We will deliver within 5 days.", ctx())
    assert guardrails.check_text("Installation is done by tomorrow.", ctx())
    assert not guardrails.check_text("Standard delivery takes 5 days.", ctx(knowledge=["Standard delivery takes 5 days."]))
    assert guardrails.check_text("Standard delivery takes 7 days.", ctx(knowledge=["Standard delivery takes 5 days."]))
    assert not guardrails.check_text("When do you need delivery, within 2 weeks?", ctx())
    assert not guardrails.check_text("A team member will confirm the delivery date.", ctx())


def test_length_limits_differ_by_channel():
    long_chat = "\n".join(f"Line {i}" for i in range(5))
    assert guardrails.check_text(long_chat, ctx("whatsapp"))
    assert guardrails.check_text("x" * 800, ctx("web_chat"))
    assert not guardrails.check_text("x " * 300, ctx("email"))
    assert guardrails.check_text("x" * 1600, ctx("email"))


def test_ai_disclosure_is_required_when_asked():
    asked = ctx(inbound=["Am I talking to a real person or a bot?"])
    assert guardrails.check_reply("Yes, I can help with that.", asked)
    assert not guardrails.check_reply("I am an AI assistant for Acme. I can connect you with a team member.", asked)
    assert not guardrails.check_reply("Happy to help.", ctx(inbound=["What are your hours?"]))


# ---- production safety ------------------------------------------------------------------------

def test_production_refuses_development_secrets():
    from app.config import Settings, assert_production_ready

    assert_production_ready(Settings(environment="development"))  # fine locally
    with pytest.raises(RuntimeError, match="supabase_jwt_secret"):
        assert_production_ready(Settings(environment="production"))
    strong = "x" * 40
    ok = Settings(
        environment="production", supabase_jwt_secret=strong, widget_session_secret=strong, lead_webhook_api_key=strong,
        unsubscribe_signing_key=strong, whatsapp_verify_token="v", whatsapp_app_secret="s", email_webhook_secret="e",
    )
    assert_production_ready(ok)


def test_postgres_urls_use_the_psycopg2_driver():
    from app.db import normalize_url

    assert normalize_url("postgresql://u:p@h:5432/db") == "postgresql+psycopg2://u:p@h:5432/db"
    assert normalize_url("postgres://u:p@h/db") == "postgresql+psycopg2://u:p@h/db"
    assert normalize_url("postgresql+psycopg2://u@h/db") == "postgresql+psycopg2://u@h/db"
    assert normalize_url("sqlite:///:memory:") == "sqlite:///:memory:"


def test_phone_numbers_get_the_default_country_code_only_when_national():
    from app.services.leads import normalize_phone

    assert normalize_phone("98765 43210") == "+919876543210"
    assert normalize_phone("098765 43210") == "+919876543210"
    assert normalize_phone("+91 98765 43210") == "+919876543210"
    assert normalize_phone("919876543210") == "+919876543210"  # WhatsApp style, already has the code
    assert normalize_phone("0091 98765 43210") == "+919876543210"
    assert normalize_phone("+1 415 555 2671") == "+14155552671"
    assert normalize_phone("12345") is None and normalize_phone("") is None and normalize_phone(None) is None


def test_a_made_up_percentage_is_blocked_but_one_from_knowledge_or_the_customer_is_fine():
    assert guardrails.check_text("Okay, 15% then.", ctx())
    assert guardrails.check_text("We can do 12.5 percent for you.", ctx())
    assert not guardrails.check_text("The panel is covered by a 100% parts warranty.", ctx(knowledge=["Panels carry a 100% parts warranty."]))
    assert not guardrails.check_text("Yes, GST is 18%.", ctx(inbound=["Is GST 18% extra?"]))
    assert not guardrails.check_text("Is a 20% advance fine?", ctx(inbound=["I can pay 20% advance"]))
