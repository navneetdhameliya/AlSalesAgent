"""Application settings. Every secret comes from an environment variable."""
from functools import lru_cache
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict

REPO_ROOT = Path(__file__).resolve().parents[2]


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    environment: str = "development"
    api_base_url: str = "http://localhost:8000"
    dashboard_url: str = "http://localhost:3000"
    cors_origins: str = "http://localhost:3000"
    log_level: str = "INFO"

    # Database (Supabase Postgres in production, SQLite for local tests)
    database_url: str = "sqlite:///./dev.db"
    supabase_jwt_secret: str = "dev-jwt-secret-change-me"

    # AI
    anthropic_api_key: str = ""
    claude_model: str = "claude-sonnet-5-5"
    agent_max_tool_rounds: int = 5
    agent_history_messages: int = 20

    # Business profile (moves to the settings table in the dashboard on Day 3)
    business_name: str = "Our Company"
    persona_name: str = "Aisha"
    first_reply_delay_seconds: int = 0
    default_country_code: str = "91"  # added to 10-digit numbers typed without a country code

    # WhatsApp (Meta Cloud API)
    whatsapp_phone_number_id: str = ""
    whatsapp_access_token: str = ""
    whatsapp_app_secret: str = ""
    whatsapp_verify_token: str = "dev-verify-token"
    whatsapp_graph_version: str = "v20.0"

    # Public endpoints
    lead_webhook_api_key: str = "dev-lead-key"
    widget_session_secret: str = "dev-widget-secret-change-me"
    widget_session_ttl_hours: int = 24

    # Supabase admin API (used only to invite dashboard users)
    supabase_url: str = ""
    supabase_service_role_key: str = ""

    # Email (provider assumed Resend until confirmed)
    email_provider: str = "resend"
    email_api_key: str = ""
    email_from_name: str = "Our Company"
    email_from_address: str = ""
    email_webhook_secret: str = ""
    unsubscribe_signing_key: str = "dev-unsubscribe-key-change-me-please-32b"

    # Follow-ups
    business_timezone: str = "Asia/Kolkata"
    followup_window_start: int = 9
    followup_window_end: int = 19
    followup_days: str = "1,3,7"
    scheduler_enabled: bool = False
    scheduler_interval_seconds: int = 60

    prompts_dir: str = str(REPO_ROOT / "shared" / "prompts")


DEV_DEFAULTS = {
    "supabase_jwt_secret": "dev-jwt-secret-change-me",
    "widget_session_secret": "dev-widget-secret-change-me",
    "lead_webhook_api_key": "dev-lead-key",
    "whatsapp_verify_token": "dev-verify-token",
    "unsubscribe_signing_key": "dev-unsubscribe-key-change-me-please-32b",
}


def assert_production_ready(s: Settings) -> None:
    """Refuse to start in production with development secrets or secrets shorter than 32 characters."""
    if s.environment != "production":
        return
    bad = [k for k, dev in DEV_DEFAULTS.items() if getattr(s, k) == dev or len(getattr(s, k)) < (0 if k == "whatsapp_verify_token" else 32)]
    if not s.whatsapp_app_secret:
        bad.append("whatsapp_app_secret")
    if not s.email_webhook_secret:
        bad.append("email_webhook_secret")
    if bad:
        raise RuntimeError("Unsafe production configuration, set strong values for: " + ", ".join(sorted(bad)))


@lru_cache
def get_settings() -> Settings:
    return Settings()
