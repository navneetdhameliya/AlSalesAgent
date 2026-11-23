"""Business settings editable from the dashboard. Stored in the settings table; env values are the defaults."""
import re
from ipaddress import ip_address
from urllib.parse import urlparse

from sqlalchemy.orm import Session

from app.config import get_settings
from app.errors import AppError
from app.models import Setting

EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
SECRET_KEYS = {"webhook_export_secret"}


def defaults() -> dict:
    s = get_settings()
    return {
        "business_name": s.business_name,
        "persona_name": s.persona_name,
        "followup_days": s.followup_days,
        "send_window_start": s.followup_window_start,
        "send_window_end": s.followup_window_end,
        "stuck_lead_hours": 24,
        "daily_report_time": "09:00",
        "report_recipients": [],
        "webhook_export_url": "",
        "webhook_export_secret": "",
        "allowed_origins": [],
    }


def get_all(db: Session) -> dict:
    out = defaults()
    for row in db.query(Setting).all():
        if row.key in out and isinstance(row.value, dict) and "v" in row.value:
            out[row.key] = row.value["v"]
    return out


def get(db: Session, key: str):
    return get_all(db)[key]


def public_view(values: dict) -> dict:
    """Settings as the dashboard sees them: secrets are never returned."""
    out = {k: v for k, v in values.items() if k not in SECRET_KEYS}
    out["webhook_export_secret_set"] = bool(values.get("webhook_export_secret"))
    return out


def _bad(key: str, msg: str):
    raise AppError("validation_error", msg, 422, {key: msg})


def _text(key, v, maxlen, allow_empty=False):
    if not isinstance(v, str) or (not v.strip() and not allow_empty) or len(v) > maxlen:
        _bad(key, f"Enter text up to {maxlen} characters.")
    return v.strip()


def validate_webhook_url(url: str) -> str:
    """Only https (http for localhost in development) and never a private address in production."""
    if not url:
        return ""
    p = urlparse(url)
    dev = get_settings().environment == "development"
    if p.scheme not in ("https", "http") or not p.hostname or (p.scheme == "http" and not dev):
        raise AppError("validation_error", "Use an https:// URL.", 422, {"webhook_export_url": "Use an https:// URL."})
    host = p.hostname.lower()
    private = host in ("localhost", "metadata.google.internal") or host.endswith(".internal") or host.endswith(".local")
    try:
        ip = ip_address(host)
        private = private or ip.is_private or ip.is_loopback or ip.is_link_local or ip.is_reserved
    except ValueError:
        pass
    if private and not dev:
        raise AppError("validation_error", "That address is not allowed.", 422, {"webhook_export_url": "That address is not allowed."})
    return url


def validate(key: str, v):
    if key in ("business_name", "persona_name"):
        return _text(key, v, 100 if key == "business_name" else 50)
    if key == "followup_days":
        try:
            days = [int(x) for x in str(v).split(",") if x.strip()]
        except ValueError:
            days = []
        if not days or len(days) > 5 or any(d < 1 or d > 30 for d in days) or days != sorted(set(days)):
            _bad(key, "Use up to 5 increasing day numbers from 1 to 30, like 1,3,7.")
        return ",".join(str(d) for d in days)
    if key in ("send_window_start", "send_window_end"):
        if not isinstance(v, int) or isinstance(v, bool) or not 0 <= v <= 24:
            _bad(key, "Use an hour from 0 to 24.")
        return v
    if key == "stuck_lead_hours":
        if not isinstance(v, int) or isinstance(v, bool) or not 1 <= v <= 720:
            _bad(key, "Use 1 to 720 hours.")
        return v
    if key == "daily_report_time":
        if not isinstance(v, str) or not re.fullmatch(r"([01]\d|2[0-3]):[0-5]\d", v):
            _bad(key, "Use 24-hour HH:MM, like 09:00.")
        return v
    if key == "report_recipients":
        if not isinstance(v, list) or len(v) > 10 or not all(isinstance(x, str) and EMAIL_RE.match(x.strip()) for x in v):
            _bad(key, "Give up to 10 valid email addresses.")
        return [x.strip().lower() for x in v]
    if key == "webhook_export_url":
        if not isinstance(v, str):
            _bad(key, "Enter a URL.")
        return validate_webhook_url(v.strip())
    if key == "webhook_export_secret":
        return _text(key, v, 200, allow_empty=True)
    if key == "allowed_origins":
        if not isinstance(v, list) or len(v) > 20:
            _bad(key, "Give up to 20 origins.")
        out = []
        for o in v:
            p = urlparse(str(o).strip())
            if p.scheme not in ("https", "http") or not p.hostname or p.path not in ("", "/"):
                _bad(key, "Each origin looks like https://example.com")
            out.append(f"{p.scheme}://{p.netloc}")
        return out
    raise AppError("validation_error", f"Unknown setting: {key}", 422, {key: "Unknown setting."})


def update(db: Session, changes: dict) -> tuple[dict, dict]:
    """Validate and save. Returns (before, after) with secrets masked, for the audit log."""
    clean = {k: validate(k, v) for k, v in changes.items()}
    if "send_window_start" in clean or "send_window_end" in clean:
        cur = get_all(db)
        if clean.get("send_window_start", cur["send_window_start"]) >= clean.get("send_window_end", cur["send_window_end"]):
            _bad("send_window_start", "The start hour must be before the end hour.")
    before = public_view(get_all(db))
    for k, v in clean.items():
        row = db.get(Setting, k)
        if row is None:
            db.add(Setting(key=k, value={"v": v}))
        else:
            row.value = {"v": v}
    db.flush()
    return before, public_view(get_all(db))
