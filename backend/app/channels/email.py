"""Email channel: sending (Resend over HTTPS), inbound webhook parsing, unsubscribe links.

The provider is behind EmailClient so it can be swapped. Inbound mail arrives at
POST /webhooks/email/inbound as normalized JSON (an inbound-parse service or a small
forwarder produces it); the body is signed with EMAIL_WEBHOOK_SECRET (HMAC-SHA256, hex).
"""
import hashlib
import hmac
import re

import httpx
import jwt

from app.channels.whatsapp import ChannelError
from app.config import get_settings


def verify_signature(raw_body: bytes, header: str | None) -> bool:
    s = get_settings()
    if not s.email_webhook_secret:
        return s.environment == "development"  # never skip verification outside development
    if not header:
        return False
    expected = hmac.new(s.email_webhook_secret.encode(), raw_body, hashlib.sha256).hexdigest()
    return hmac.compare_digest(expected, header.removeprefix("sha256="))


# ---- unsubscribe links --------------------------------------------------------------

def make_unsubscribe_token(lead_id) -> str:
    return jwt.encode({"lead": str(lead_id), "purpose": "unsubscribe"}, get_settings().unsubscribe_signing_key, algorithm="HS256")


def read_unsubscribe_token(token: str) -> str | None:
    """Return the lead id for a valid token, else None."""
    try:
        claims = jwt.decode(token, get_settings().unsubscribe_signing_key, algorithms=["HS256"])
    except jwt.PyJWTError:
        return None
    return claims.get("lead") if claims.get("purpose") == "unsubscribe" else None


def unsubscribe_url(lead_id) -> str:
    s = get_settings()
    return f"{s.api_base_url}/api/v1/public/unsubscribe?token={make_unsubscribe_token(lead_id)}"


def compose_body(text: str, unsub_url: str) -> str:
    return f"{text.rstrip()}\n\n--\nIf you do not want to hear from us, unsubscribe here: {unsub_url}"


# ---- inbound parsing ----------------------------------------------------------------

_QUOTE_START = re.compile(r"^(on .{5,120} wrote:|-{2,}\s*original message\s*-{2,}|from:\s.+|sent from my .+)$", re.I)


def strip_quoted(text: str) -> str:
    """Keep what the customer wrote; drop the quoted earlier thread and signatures."""
    kept = []
    for line in (text or "").splitlines():
        if line.lstrip().startswith(">") or _QUOTE_START.match(line.strip()):
            break
        kept.append(line)
    return "\n".join(kept).strip()


def parse_inbound(payload: dict) -> dict | None:
    """Normalize an inbound-mail or delivery-event payload, or return None if unusable."""
    kind = payload.get("type", "inbound")
    event_id = str(payload.get("event_id") or payload.get("message_id") or "").strip()
    if kind in ("bounce", "complaint"):
        email = (payload.get("email") or "").strip().lower()
        return {"type": kind, "event_id": event_id, "email": email} if email and event_id else None
    sender = (payload.get("from_email") or "").strip().lower()
    body = strip_quoted(payload.get("text") or "")
    if not sender or not event_id or not body:
        return None
    return {
        "type": "inbound",
        "event_id": event_id,
        "from_email": sender,
        "from_name": payload.get("from_name"),
        "subject": (payload.get("subject") or "").strip()[:300],
        "text": body,
        "message_id": payload.get("message_id"),
    }


# ---- sending ------------------------------------------------------------------------

class EmailClient:
    def send(self, to: str, subject: str, text: str, unsub_url: str, in_reply_to: str | None = None) -> str:
        s = get_settings()
        if not (s.email_api_key and s.email_from_address):
            raise ChannelError("Email is not configured (API key or from address missing).")
        headers = {"List-Unsubscribe": f"<{unsub_url}>", "List-Unsubscribe-Post": "List-Unsubscribe=One-Click"}
        if in_reply_to:
            headers["In-Reply-To"] = in_reply_to
            headers["References"] = in_reply_to
        payload = {
            "from": f"{s.email_from_name} <{s.email_from_address}>",
            "to": [to],
            "subject": subject,
            "text": compose_body(text, unsub_url),
            "headers": headers,
        }
        try:
            r = httpx.post("https://api.resend.com/emails", json=payload, headers={"Authorization": f"Bearer {s.email_api_key}"}, timeout=15)
        except httpx.HTTPError as e:
            raise ChannelError(f"Email request failed: {e}")
        if r.status_code >= 300:
            raise ChannelError(f"Email provider rejected the message ({r.status_code}): {r.text[:200]}")
        return str(r.json().get("id", ""))


    def send_internal(self, to: list[str], subject: str, text: str) -> str:
        """Mail to our own team (reports). No unsubscribe footer."""
        s = get_settings()
        if not (s.email_api_key and s.email_from_address):
            raise ChannelError("Email is not configured (API key or from address missing).")
        payload = {"from": f"{s.email_from_name} <{s.email_from_address}>", "to": to, "subject": subject, "text": text}
        try:
            r = httpx.post("https://api.resend.com/emails", json=payload, headers={"Authorization": f"Bearer {s.email_api_key}"}, timeout=15)
        except httpx.HTTPError as e:
            raise ChannelError(f"Email request failed: {e}")
        if r.status_code >= 300:
            raise ChannelError(f"Email provider rejected the message ({r.status_code}): {r.text[:200]}")
        return str(r.json().get("id", ""))


email_client = EmailClient()
