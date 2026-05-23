"""Shared-password auth.

Sessions ride on a signed token. The client receives it in the login
response body, stashes it in `localStorage`, and sends it back on every
request as `Authorization: Bearer <token>`. We do NOT use cookies —
they were unreliable through the Vercel rewrite for some browser
configurations (privacy blockers, strict SameSite, etc.).
"""

from __future__ import annotations

import os
import secrets
from dataclasses import dataclass

from fastapi import Header, HTTPException
from itsdangerous import BadSignature, URLSafeSerializer


_TOKEN_MAX_AGE_S = 60 * 60 * 24 * 7  # 7 days; enforced via signed payload, see below


@dataclass(frozen=True)
class AuthConfig:
    password: str
    cookie_secret: str  # still called cookie_secret for env-var continuity


def load_config() -> AuthConfig:
    """Read auth config from env. Fails loudly if AGENT_PASSWORD is missing."""
    password = os.environ.get("AGENT_PASSWORD")
    if not password:
        raise RuntimeError(
            "AGENT_PASSWORD env var is required. Set it in "
            "/home/ec2-user/Sprntly/ds-agent/.env before starting "
            "sprntly-agent.service."
        )
    cookie_secret = os.environ.get("AGENT_COOKIE_SECRET") or secrets.token_urlsafe(32)
    return AuthConfig(password=password, cookie_secret=cookie_secret)


def make_serializer(cfg: AuthConfig) -> URLSafeSerializer:
    return URLSafeSerializer(cfg.cookie_secret, salt="sprntly-agent-session")


def issue_token(serializer: URLSafeSerializer) -> tuple[str, str]:
    """Create a fresh session id, return (session_id, signed_token)."""
    session_id = secrets.token_urlsafe(24)
    token = serializer.dumps({"sid": session_id})
    return session_id, token


def require_session(serializer: URLSafeSerializer):
    """FastAPI dependency. Reads the token from the Authorization header
    (`Bearer <token>`); 401s if absent or invalid."""

    def _dep(authorization: str | None = Header(default=None)) -> str:
        sid = _decode_authorization(serializer, authorization)
        if not sid:
            raise HTTPException(status_code=401, detail="not_authenticated")
        return sid

    return _dep


def optional_session(serializer: URLSafeSerializer):
    """Like require_session but returns None instead of raising."""

    def _dep(authorization: str | None = Header(default=None)) -> str | None:
        return _decode_authorization(serializer, authorization)

    return _dep


def _decode_authorization(
    serializer: URLSafeSerializer, authorization: str | None
) -> str | None:
    if not authorization:
        return None
    parts = authorization.split(" ", 1)
    if len(parts) != 2 or parts[0].lower() != "bearer":
        return None
    token = parts[1].strip()
    if not token:
        return None
    try:
        payload = serializer.loads(token)
    except BadSignature:
        return None
    if not isinstance(payload, dict):
        return None
    sid = payload.get("sid")
    if not isinstance(sid, str) or not sid:
        return None
    return sid
