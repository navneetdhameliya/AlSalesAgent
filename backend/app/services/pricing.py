"""Price engine. The model never supplies prices; the server computes them from rules.

Rule types:
  fixed     flat total for the quantity range (amount)
  per_unit  unit price (amount) x quantity
  tiered    unit price depends on quantity: tiers [{"up_to": 10, "unit_price": "5"}, {"up_to": null, ...}]
  formula   expression yields the unit price; total = unit price x quantity
Formulas run through a restricted AST evaluator, never eval().
"""
import ast
import math
from dataclasses import dataclass
from decimal import ROUND_HALF_UP, Decimal, InvalidOperation

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import PriceRule, Product

MAX_FORMULA_LEN = 200
MAX_EXPONENT = 6
MAX_VALUE = Decimal("1e12")
CENT = Decimal("0.01")


class PriceError(Exception):
    pass


def D(value) -> Decimal:
    try:
        return Decimal(str(value))
    except (InvalidOperation, ValueError):
        raise PriceError(f"not a number: {value!r}")


_FUNCS = {
    "min": min,
    "max": max,
    "abs": abs,
    "round": lambda x, n=0: round(x, int(n)),
    "ceil": lambda x: Decimal(math.ceil(x)),
}
_BINOPS = {
    ast.Add: lambda a, b: a + b,
    ast.Sub: lambda a, b: a - b,
    ast.Mult: lambda a, b: a * b,
    ast.Div: lambda a, b: a / b,
    ast.Mod: lambda a, b: a % b,
}


def _check(value: Decimal) -> Decimal:
    if abs(value) > MAX_VALUE:
        raise PriceError("value too large")
    return value


def evaluate_formula(expr: str, variables: dict) -> Decimal:
    """Evaluate a pricing expression with only arithmetic, a few functions and declared names."""
    if not expr or len(expr) > MAX_FORMULA_LEN:
        raise PriceError("formula is empty or too long")
    try:
        tree = ast.parse(expr, mode="eval")
    except SyntaxError:
        raise PriceError("formula syntax error")
    values = {k: D(v) for k, v in variables.items()}

    def ev(node) -> Decimal:
        if isinstance(node, ast.Expression):
            return ev(node.body)
        if isinstance(node, ast.Constant) and isinstance(node.value, (int, float)) and not isinstance(node.value, bool):
            return D(node.value)
        if isinstance(node, ast.Name):
            if node.id not in values:
                raise PriceError(f"unknown name: {node.id}")
            return values[node.id]
        if isinstance(node, ast.UnaryOp) and isinstance(node.op, (ast.USub, ast.UAdd)):
            v = ev(node.operand)
            return -v if isinstance(node.op, ast.USub) else v
        if isinstance(node, ast.BinOp):
            left, right = ev(node.left), ev(node.right)
            if isinstance(node.op, ast.Pow):
                if right != right.to_integral_value() or abs(right) > MAX_EXPONENT:
                    raise PriceError("exponent not allowed")
                return _check(left ** int(right))
            op = _BINOPS.get(type(node.op))
            if op is None:
                raise PriceError("operator not allowed")
            try:
                return _check(op(left, right))
            except (InvalidOperation, ZeroDivisionError):
                raise PriceError("division by zero")
        if isinstance(node, ast.Call):
            if not isinstance(node.func, ast.Name) or node.func.id not in _FUNCS or node.keywords:
                raise PriceError("function not allowed")
            args = [ev(a) for a in node.args]
            if not args or len(args) > 4:
                raise PriceError("bad function arguments")
            return _check(D(_FUNCS[node.func.id](*args)))
        raise PriceError("expression not allowed")

    return ev(tree)


def money(value: Decimal) -> Decimal:
    return value.quantize(CENT, rounding=ROUND_HALF_UP)


def format_money(value, currency: str = "") -> str:
    d = money(D(value))
    text = f"{d:,.2f}"
    if text.endswith(".00"):
        text = text[:-3]
    return f"{currency} {text}".strip()


@dataclass
class PriceResult:
    product_id: str
    product_name: str
    quantity: Decimal
    unit_price: Decimal
    total: Decimal
    currency: str
    rule_type: str
    rule_id: str
    note: str | None = None

    def as_dict(self) -> dict:
        return {
            "product": self.product_name,
            "quantity": str(self.quantity),
            "unit_price": str(self.unit_price),
            "total": str(self.total),
            "currency": self.currency,
            "unit_price_text": format_money(self.unit_price, self.currency),
            "total_text": format_money(self.total, self.currency),
            "note": self.note,
        }

    def amounts(self) -> set[str]:
        return {str(money(self.unit_price)), str(money(self.total))}


def find_product(db: Session, name: str) -> Product | None:
    name = (name or "").strip()
    if not name:
        return None
    rows = db.scalars(select(Product).where(Product.active.is_(True))).all()
    lowered = name.lower()
    for p in rows:
        if p.name.lower() == lowered:
            return p
    matches = [p for p in rows if lowered in p.name.lower() or p.name.lower() in lowered]
    return matches[0] if len(matches) == 1 else None


def pick_rule(product: Product, quantity: Decimal) -> PriceRule | None:
    candidates = []
    for r in product.rules:
        if not r.active:
            continue
        lo = D(r.min_qty or 0)
        hi = D(r.max_qty) if r.max_qty is not None else None
        if quantity >= lo and (hi is None or quantity <= hi):
            candidates.append((lo, r))
    if not candidates:
        return None
    candidates.sort(key=lambda t: t[0], reverse=True)  # highest min_qty wins
    return candidates[0][1]


def _unit_price_for(rule: PriceRule, product: Product, quantity: Decimal, attrs: dict) -> Decimal:
    if rule.rule_type == "fixed":
        return D(rule.amount) / quantity
    if rule.rule_type == "per_unit":
        return D(rule.amount)
    if rule.rule_type == "tiered":
        tiers = rule.tiers or []
        for tier in tiers:
            up_to = tier.get("up_to")
            if up_to is None or quantity <= D(up_to):
                return D(tier["unit_price"])
        raise PriceError("no tier covers this quantity")
    if rule.rule_type == "formula":
        declared = set((product.attributes or {}).keys())
        variables = {"qty": quantity}
        for k, v in (attrs or {}).items():
            if k in declared:
                variables[k] = v
        missing = [k for k in declared if k not in variables]
        if missing:
            raise PriceError("missing inputs: " + ", ".join(sorted(missing)))
        return evaluate_formula(rule.formula or "", variables)
    raise PriceError(f"unknown rule type: {rule.rule_type}")


def compute_price(db: Session, product_name: str, quantity, attributes: dict | None = None) -> PriceResult:
    product = find_product(db, product_name)
    if product is None:
        raise PriceError("product not found")
    try:
        qty = D(quantity)
    except PriceError:
        raise PriceError("quantity must be a number")
    if qty <= 0:
        raise PriceError("quantity must be greater than zero")
    rule = pick_rule(product, qty)
    if rule is None:
        raise PriceError("no price rule for this quantity")
    unit = money(_unit_price_for(rule, product, qty, attributes or {}))
    if unit < 0:
        raise PriceError("negative price")
    total = money(unit * qty) if rule.rule_type != "fixed" else money(D(rule.amount))
    return PriceResult(
        product_id=str(product.id),
        product_name=product.name,
        quantity=qty,
        unit_price=unit,
        total=total,
        currency=product.currency,
        rule_type=rule.rule_type,
        rule_id=str(rule.id),
        note=rule.note,
    )
