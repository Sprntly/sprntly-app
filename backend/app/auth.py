"""Demo gate: single shared password → JWT in HttpOnly cookie."""
import time
from datetime import datetime, timezone

import jwt
from fastapi import APIRouter, Cookie, HTTPException, Response
from pydantic import BaseModel

from app.config import settings

router = APIRouter(prefix="/v1/auth", tags=["auth"])

COOKIE_NAME = "sprintly_session"
JWT_ALG = "HS256"


def _make_token() -> str:
    now = int(time.time())
    payload = {
        "iat": now,
        "exp": now + settings.session_ttl_hours * 3600,
        "scope": "demo",
    }
    return jwt.encode(payload, settings.jwt_secret, algorithm=JWT_ALG)


def _decode_token(token: str) -> dict:
    return jwt.decode(token, settings.jwt_secret, algorithms=[JWT_ALG])


def require_session(token: str | None) -> dict:
    if not token:
        raise HTTPException(401, "Not signed in")
    try:
        return _decode_token(token)
    except jwt.PyJWTError:
        raise HTTPException(401, "Invalid or expired session")


class LoginIn(BaseModel):
    password: str


@router.post("/login")
def login(body: LoginIn, response: Response):
    if not settings.demo_password:
        raise HTTPException(500, "Demo password not configured on server")
    if body.password != settings.demo_password:
        raise HTTPException(401, "Wrong password")
    token = _make_token()
    response.set_cookie(
        key=COOKIE_NAME,
        value=token,
        max_age=settings.session_ttl_hours * 3600,
        httponly=True,
        secure=settings.cookie_secure,
        samesite="none" if settings.cookie_secure else "lax",
        domain=settings.cookie_domain or None,
        path="/",
    )
    return {"ok": True}


@router.post("/logout")
def logout(response: Response):
    response.delete_cookie(
        key=COOKIE_NAME,
        domain=settings.cookie_domain or None,
        path="/",
    )
    return {"ok": True}


@router.get("/me")
def me(sprintly_session: str | None = Cookie(default=None)):
    payload = require_session(sprintly_session)
    return {
        "scope": payload.get("scope"),
        "expires_at": datetime.fromtimestamp(payload["exp"], tz=timezone.utc).isoformat(),
    }
