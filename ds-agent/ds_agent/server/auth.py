"""Shared-password auth + signed session cookies.

We keep this deliberately small: one env-var password, signed session
cookies that carry just a `session_id`, and a short helper to load
config on startup. No DB, no user table.
"""

from __future__ import annotations

import os
import secrets
from dataclasses import dataclass

from fastapi import Cookie, HTTPException, Response
from itsdangerous import BadSignature, URLSafeSerializer


COOKIE_NAME = "sprntly_agent_session"
COOKIE_MAX_AGE_S = 60 * 60 * 24 * 7  # 7 days


@dataclass(frozen=True)
class AuthConfig:
    password: str
    cookie_secret: str
    cookie_secure: bool


def load_config() -> AuthConfig:
    """Read auth config from env. Fails loudly if AGENT_PASSWORD is missing.

    We *don't* generate a random AGENT_PASSWORD if unset — that would let
    the service boot in an unreachable state. We *do* derive a stable
    fallback for AGENT_COOKIE_SECRET if it's not provided, which means
    sessions reset on each restart (acceptable for a pilot tool).
    """
    password = os.environ.get("AGENT_PASSWORD")
    if not password:
        raise RuntimeError(
            "AGENT_PASSWORD env var is required. Set it in /etc/sprntly-agent.env "
            "before starting sprntly-agent.service."
        )
    cookie_secret = os.environ.get("AGENT_COOKIE_SECRET") or secrets.token_urlsafe(32)
    cookie_secure = os.environ.get("AGENT_COOKIE_SECURE", "1") == "1"
    return AuthConfig(
        password=password, cookie_secret=cookie_secret, cookie_secure=cookie_secure
    )


def make_serializer(cfg: AuthConfig) -> URLSafeSerializer:
    return URLSafeSerializer(cfg.cookie_secret, salt="sprntly-agent-session")


def issue_session(response: Response, cfg: AuthConfig, serializer: URLSafeSerializer) -> str:
    """Create a fresh session id, sign it into the cookie, return the id."""
    session_id = secrets.token_urlsafe(24)
    token = serializer.dumps({"sid": session_id})
    response.set_cookie(
        key=COOKIE_NAME,
        value=token,
        max_age=COOKIE_MAX_AGE_S,
        httponly=True,
        secure=cfg.cookie_secure,
        samesite="lax",
        path="/",
    )
    return session_id


def clear_session(response: Response) -> None:
    response.delete_cookie(key=COOKIE_NAME, path="/")


def require_session(
    serializer: URLSafeSerializer,
):
    """Build a FastAPI dependency that returns the session id or 401s."""

    def _dep(sprntly_agent_session: str | None = Cookie(default=None, alias=COOKIE_NAME)) -> str:
        if not sprntly_agent_session:
            raise HTTPException(status_code=401, detail="not_authenticated")
        try:
            payload = serializer.loads(sprntly_agent_session)
        except BadSignature:
            raise HTTPException(status_code=401, detail="bad_session")
        sid = payload.get("sid")
        if not sid:
            raise HTTPException(status_code=401, detail="malformed_session")
        return sid

    return _dep


def optional_session(
    serializer: URLSafeSerializer,
):
    """Like require_session but returns None instead of raising."""

    def _dep(sprntly_agent_session: str | None = Cookie(default=None, alias=COOKIE_NAME)) -> str | None:
        if not sprntly_agent_session:
            return None
        try:
            payload = serializer.loads(sprntly_agent_session)
        except BadSignature:
            return None
        return payload.get("sid")

    return _dep
