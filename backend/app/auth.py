"""Auth gate for sprntly-app + sprntly-demo.

Two surfaces share the same `DEMO_PASSWORD` but get distinct sessions:

  - `sprntly_app_session`  — minted when logging in from app.sprntly.ai;
                             JWT has aud="app".
  - `sprntly_demo_session` — minted when logging in from demo.sprntly.ai;
                             JWT has aud="demo".

Both cookies are set on `.sprntly.ai` (parent domain) so each subdomain
sees its own, but the JWT `aud` claim makes the two non-interchangeable —
copy-pasting one cookie's value into the other slot will fail signature
validation. The shared parent domain is fine because the cookie *names*
differ; cookie scoping per subdomain isn't required for isolation.

A request is authenticated if EITHER cookie validates against its
expected audience. Routes that want to restrict to a single surface can
use the audience-specific deps (`require_app_session`,
`require_demo_session`).

The legacy single-audience cookie (`sprintly_session`) is no longer
accepted; old logins will 401 once and re-login via the new flow.
"""
import time
from datetime import datetime, timezone
from typing import Literal

import jwt
from fastapi import APIRouter, Cookie, HTTPException, Response
from pydantic import BaseModel

from app.config import settings

router = APIRouter(prefix="/v1/auth", tags=["auth"])

Audience = Literal["app", "demo"]

APP_COOKIE = "sprntly_app_session"
DEMO_COOKIE = "sprntly_demo_session"
LEGACY_COOKIE = "sprintly_session"  # cleared on login/logout; never accepted

JWT_ALG = "HS256"


def _cookie_name(audience: Audience) -> str:
    return APP_COOKIE if audience == "app" else DEMO_COOKIE


def _make_token(audience: Audience) -> str:
    now = int(time.time())
    payload = {
        "iat": now,
        "exp": now + settings.session_ttl_hours * 3600,
        "aud": audience,
        "scope": audience,
    }
    return jwt.encode(payload, settings.jwt_secret, algorithm=JWT_ALG)


def _decode_token(token: str, expected_audience: Audience) -> dict:
    return jwt.decode(
        token,
        settings.jwt_secret,
        algorithms=[JWT_ALG],
        audience=expected_audience,
    )


def require_session(
    sprntly_app_session: str | None = Cookie(default=None),
    sprntly_demo_session: str | None = Cookie(default=None),
) -> dict:
    """Generic gate — accepts either audience.

    Use as `Depends(require_session)` on routes shared by app + demo. The
    function returns the decoded JWT payload (with `aud` set) so callers
    can branch on audience if they need to.
    """
    for token, expected_aud in (
        (sprntly_app_session, "app"),
        (sprntly_demo_session, "demo"),
    ):
        if not token:
            continue
        try:
            return _decode_token(token, expected_aud)
        except jwt.PyJWTError:
            continue
    raise HTTPException(401, "Not signed in")


def require_app_session(
    sprntly_app_session: str | None = Cookie(default=None),
) -> dict:
    """Locked to the app audience. Demo sessions are rejected even if present."""
    if not sprntly_app_session:
        raise HTTPException(401, "Not signed in")
    try:
        return _decode_token(sprntly_app_session, "app")
    except jwt.PyJWTError as e:
        raise HTTPException(401, "Invalid or expired session") from e


def require_demo_session(
    sprntly_demo_session: str | None = Cookie(default=None),
) -> dict:
    """Locked to the demo audience."""
    if not sprntly_demo_session:
        raise HTTPException(401, "Not signed in")
    try:
        return _decode_token(sprntly_demo_session, "demo")
    except jwt.PyJWTError as e:
        raise HTTPException(401, "Invalid or expired session") from e


class LoginIn(BaseModel):
    password: str
    # Default is "demo" so old clients that don't pass audience continue
    # working against demo.sprntly.ai. App clients must opt in explicitly.
    audience: Audience = "demo"


def _set_session_cookie(response: Response, audience: Audience, token: str) -> None:
    response.set_cookie(
        key=_cookie_name(audience),
        value=token,
        max_age=settings.session_ttl_hours * 3600,
        httponly=True,
        secure=settings.cookie_secure,
        samesite="none" if settings.cookie_secure else "lax",
        domain=settings.cookie_domain or None,
        path="/",
    )


def _clear_legacy_cookie(response: Response) -> None:
    response.delete_cookie(
        key=LEGACY_COOKIE,
        domain=settings.cookie_domain or None,
        path="/",
    )


@router.post("/login")
def login(body: LoginIn, response: Response):
    if not settings.demo_password:
        raise HTTPException(500, "Demo password not configured on server")
    if body.password != settings.demo_password:
        raise HTTPException(401, "Wrong password")
    token = _make_token(body.audience)
    _set_session_cookie(response, body.audience, token)
    # Sweep up the pre-split cookie so transitioning users don't end up
    # carrying both.
    _clear_legacy_cookie(response)
    return {"ok": True, "audience": body.audience}


@router.post("/logout")
def logout(
    response: Response,
    sprntly_app_session: str | None = Cookie(default=None),
    sprntly_demo_session: str | None = Cookie(default=None),
):
    """Drop whichever cookies this browser is carrying (and the legacy one)."""
    domain = settings.cookie_domain or None
    if sprntly_app_session is not None:
        response.delete_cookie(APP_COOKIE, domain=domain, path="/")
    if sprntly_demo_session is not None:
        response.delete_cookie(DEMO_COOKIE, domain=domain, path="/")
    _clear_legacy_cookie(response)
    return {"ok": True}


@router.get("/me")
def me(
    sprntly_app_session: str | None = Cookie(default=None),
    sprntly_demo_session: str | None = Cookie(default=None),
):
    """Return which audiences this browser has a live session for.

    Shape:
        {
            "app":  {"expires_at": "..."} | None,
            "demo": {"expires_at": "..."} | None,
        }

    401 if neither cookie validates.
    """
    out: dict[str, dict | None] = {"app": None, "demo": None}
    for token, audience in (
        (sprntly_app_session, "app"),
        (sprntly_demo_session, "demo"),
    ):
        if not token:
            continue
        try:
            payload = _decode_token(token, audience)
            out[audience] = {
                "expires_at": datetime.fromtimestamp(
                    payload["exp"], tz=timezone.utc
                ).isoformat(),
            }
        except jwt.PyJWTError:
            continue
    if not out["app"] and not out["demo"]:
        raise HTTPException(401, "Not signed in")
    return out
