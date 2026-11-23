"""Authentication.

Dashboard calls carry a Supabase JWT, signed either with the project's asymmetric key (ES256 or RS256, checked against
the project's public keys) or with the legacy shared secret (HS256). The user's role comes from the profiles table,
never from the token. Public endpoints use an API key or a signed widget session token.
"""
import hmac
import uuid
from datetime import datetime, timedelta, timezone

import jwt
from fastapi import Depends, Header
from sqlalchemy.orm import Session

from app.config import get_settings
from app.db import get_db
from app.errors import AppError
from app.models import Profile


_jwks_clients: dict[str, jwt.PyJWKClient] = {}


def _jwks(url: str) -> jwt.PyJWKClient:
    client = _jwks_clients.get(url)
    if client is None:
        client = _jwks_clients[url] = jwt.PyJWKClient(url, cache_keys=True, lifespan=3600, timeout=5)
    return client


def verify_supabase_token(token: str) -> dict:
    """Return the token's claims or raise jwt.PyJWTError. The algorithm comes from our own allow-list, never from the token."""
    s = get_settings()
    alg = jwt.get_unverified_header(token).get("alg")
    if alg == "HS256":
        if s.environment == "production" and (not s.supabase_jwt_secret or s.supabase_jwt_secret == "dev-jwt-secret-change-me"):
            raise jwt.InvalidAlgorithmError("HS256 is not enabled")
        return jwt.decode(token, s.supabase_jwt_secret, algorithms=["HS256"], audience="authenticated")
    if alg in ("ES256", "RS256") and s.supabase_url:
        base = s.supabase_url.rstrip("/") + "/auth/v1"
        key = _jwks(base + "/.well-known/jwks.json").get_signing_key_from_jwt(token).key
        return jwt.decode(token, key, algorithms=[alg], audience="authenticated", issuer=base)
    raise jwt.InvalidAlgorithmError("Unsupported token")


def current_profile(authorization: str | None = Header(default=None), db: Session = Depends(get_db)) -> Profile:
    if not authorization or not authorization.lower().startswith("bearer "):
        raise AppError("not_authenticated", "Please sign in.", 401)
    token = authorization.split(" ", 1)[1]
    try:
        claims = verify_supabase_token(token)
        user_id = uuid.UUID(claims["sub"])
    except (jwt.PyJWTError, KeyError, ValueError):
        raise AppError("not_authenticated", "Your session is not valid. Please sign in again.", 401)
    profile = db.get(Profile, user_id)
    if profile is None or not profile.active:
        raise AppError("no_permission", "This account has no access.", 403)
    return profile


def require_roles(*roles: str):
    def dep(profile: Profile = Depends(current_profile)) -> Profile:
        if profile.role not in roles:
            raise AppError("no_permission", "You do not have permission for this action.", 403)
        return profile

    return dep


def require_lead_api_key(x_api_key: str | None = Header(default=None)) -> None:
    expected = get_settings().lead_webhook_api_key
    if not x_api_key or not hmac.compare_digest(x_api_key, expected):
        raise AppError("invalid_api_key", "The API key is missing or wrong.", 401)


# ---- Web chat widget session tokens -------------------------------------------------

def make_widget_token(conversation_id: uuid.UUID, lead_id: uuid.UUID) -> str:
    s = get_settings()
    now = datetime.now(timezone.utc)
    payload = {
        "cid": str(conversation_id),
        "lid": str(lead_id),
        "iat": now,
        "exp": now + timedelta(hours=s.widget_session_ttl_hours),
    }
    return jwt.encode(payload, s.widget_session_secret, algorithm="HS256")


def widget_session(
    conversation_id: uuid.UUID,
    authorization: str | None = Header(default=None),
    x_session_token: str | None = Header(default=None),
) -> dict:
    token = x_session_token or (authorization.split(" ", 1)[1] if authorization and " " in authorization else None)
    if not token:
        raise AppError("session_required", "Chat session token is missing.", 401)
    try:
        claims = jwt.decode(token, get_settings().widget_session_secret, algorithms=["HS256"])
    except jwt.PyJWTError:
        raise AppError("session_expired", "This chat session has expired.", 401)
    if claims.get("cid") != str(conversation_id):
        raise AppError("no_permission", "This token is for a different chat.", 403)
    return claims
