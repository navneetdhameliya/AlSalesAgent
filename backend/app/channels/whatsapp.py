"""WhatsApp Cloud API: webhook signature check, payload parsing, sending.

Test mode: the free Meta test number can message only numbers added as test recipients.
Outside the 24-hour customer window, production needs approved templates (not built yet).
"""
import hashlib
import hmac

import httpx

from app.config import get_settings


class ChannelError(Exception):
    pass


def verify_signature(raw_body: bytes, header: str | None) -> bool:
    s = get_settings()
    if not s.whatsapp_app_secret:
        return s.environment == "development"  # never skip verification outside development
    if not header or not header.startswith("sha256="):
        return False
    expected = hmac.new(s.whatsapp_app_secret.encode(), raw_body, hashlib.sha256).hexdigest()
    return hmac.compare_digest(expected, header.split("=", 1)[1])


def parse_webhook(payload: dict) -> tuple[list[dict], list[dict]]:
    """Return (inbound_messages, status_updates)."""
    messages: list[dict] = []
    statuses: list[dict] = []
    for entry in payload.get("entry", []):
        for change in entry.get("changes", []):
            value = change.get("value", {})
            names = {c.get("wa_id"): (c.get("profile") or {}).get("name") for c in value.get("contacts", [])}
            for m in value.get("messages", []):
                if m.get("type") == "text":
                    body = (m.get("text") or {}).get("body", "")
                else:
                    body = f"[{m.get('type', 'unsupported')} message received]"
                messages.append({"id": m["id"], "from": m["from"], "body": body, "name": names.get(m["from"])})
            for st in value.get("statuses", []):
                statuses.append({"id": st["id"], "status": st["status"], "to": st.get("recipient_id")})
    return messages, statuses


class WhatsAppClient:
    def send_text(self, to_e164: str, body: str) -> str:
        s = get_settings()
        if not (s.whatsapp_phone_number_id and s.whatsapp_access_token):
            raise ChannelError("WhatsApp is not configured (phone number id or access token missing).")
        url = f"https://graph.facebook.com/{s.whatsapp_graph_version}/{s.whatsapp_phone_number_id}/messages"
        payload = {
            "messaging_product": "whatsapp",
            "to": to_e164.lstrip("+"),
            "type": "text",
            "text": {"body": body, "preview_url": False},
        }
        try:
            r = httpx.post(url, json=payload, headers={"Authorization": f"Bearer {s.whatsapp_access_token}"}, timeout=15)
        except httpx.HTTPError as e:
            raise ChannelError(f"WhatsApp request failed: {e}") from e
        if r.status_code >= 400:
            raise ChannelError(f"WhatsApp error {r.status_code}: {r.text[:300]}")
        return r.json()["messages"][0]["id"]


whatsapp_client = WhatsAppClient()  # tests replace this object
