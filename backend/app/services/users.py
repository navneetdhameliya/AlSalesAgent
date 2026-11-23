"""Inviting dashboard users through the Supabase admin API."""
import httpx

from app.channels.whatsapp import ChannelError
from app.config import get_settings


class SupabaseAdmin:
    def invite(self, email: str) -> str:
        """Send the invitation email and return the new auth user id."""
        s = get_settings()
        if not (s.supabase_url and s.supabase_service_role_key):
            raise ChannelError("Supabase is not configured (SUPABASE_URL or SUPABASE_SERVICE_ROLE_KEY missing).")
        try:
            r = httpx.post(
                f"{s.supabase_url.rstrip('/')}/auth/v1/invite",
                json={"email": email},
                headers={"apikey": s.supabase_service_role_key, "Authorization": f"Bearer {s.supabase_service_role_key}"},
                timeout=15,
            )
        except httpx.HTTPError as e:
            raise ChannelError(f"Invite request failed: {e}")
        if r.status_code >= 300:
            raise ChannelError(f"Supabase refused the invitation ({r.status_code}).")
        return r.json()["id"]


supabase_admin = SupabaseAdmin()
