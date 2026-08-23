"""Public whitelist / early-access signup form.

  POST /v1/whitelist   {email, source?}  ->  {ok: true}

NO-AUTH BY DESIGN, like reports_public.py. The whole point is that an anonymous
visitor on the marketing site can put their email down before they have an
account, so there is no session, no company, and nothing to scope the write to.

Kept in its own module for the same reason the public report viewer is: a
no-auth surface should be reviewable in isolation and must never grow an authed
sibling by accident.

Three properties this surface deliberately has:

  - ALWAYS 200 (never "already signed up"). The db layer upserts DO NOTHING, so
    a repeat submission is a silent success. Distinguishing "added" from
    "already there" would let a stranger probe whether a given address is on the
    list, which is exactly the leak the endpoint has no reason to allow.
  - RATE LIMITED PER IP. It is an unauthenticated write to a table, i.e. the one
    place anonymous traffic can grow the database. 10/hour is far above any
    honest human's use of a signup form and far below anything that matters.
  - NO EMAIL DELIVERY. Nothing is sent to the address, so this cannot be used to
    mail an arbitrary stranger. If a confirmation email is ever wanted it needs
    its own thought about that, not a line added here.

CORS: the marketing site is a DIFFERENT origin from the app, so its origin must
be in ALLOWED_ORIGINS for the browser to accept the response — that is an
operator step (see backend/docs/CONNECTORS.md for where env lives), not
something this module can do. There is no CSRF/Origin dependency here on
purpose: this route carries no session, so there is no cookie for a cross-site
POST to abuse (app/design_agent/csrf.py explains the same exemption for the
public share routes).
"""
from __future__ import annotations

import logging
import re

from fastapi import APIRouter, HTTPException, Request, status
from pydantic import BaseModel, Field, field_validator

from app.db.whitelist import add_to_whitelist
from app.design_agent.rate_limit import SlidingWindowLimiter

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/v1/whitelist", tags=["whitelist"])

# Per-IP budget. A person signs up once; anything past this is a script.
SIGNUP_LIMITER = SlidingWindowLimiter(max_events=10, window_seconds=3600)

# Deliberately loose. The job here is to reject obvious junk ("", "hello",
# "a@b") and cap the length, NOT to decide which addresses RFC 5322 permits —
# every strict regex on the internet rejects real deliverable addresses, and the
# only real proof an address exists is mail arriving at it. `email-validator` is
# not a dependency and is not worth adding for one form field.
_EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s.]+(\.[^@\s.]+)+$")


class WhitelistIn(BaseModel):
    email: str = Field(..., max_length=320)  # 320 = the RFC address-length ceiling
    # Where the signup came from — a landing-page slug, a campaign tag. Untrusted
    # free text from a public form, so it is capped and otherwise unexamined; it
    # is only ever read back in reporting, never used to branch on.
    source: str | None = Field(default=None, max_length=100)

    @field_validator("email")
    @classmethod
    def _validate_email(cls, v: str) -> str:
        v = (v or "").strip()
        if not _EMAIL_RE.match(v):
            raise ValueError("a valid email address is required")
        return v

    @field_validator("source")
    @classmethod
    def _clean_source(cls, v: str | None) -> str | None:
        v = (v or "").strip()
        return v or None


class WhitelistOut(BaseModel):
    ok: bool = True


@router.post("", response_model=WhitelistOut, status_code=status.HTTP_200_OK)
def join_whitelist(payload: WhitelistIn, request: Request) -> WhitelistOut:
    client_ip = request.client.host if request.client else "0.0.0.0"
    if not SIGNUP_LIMITER.check(client_ip):
        retry_after = SIGNUP_LIMITER.retry_after(client_ip)
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail="Too many signups from this address. Try again later.",
            headers={"Retry-After": str(retry_after)},
        )
    SIGNUP_LIMITER.register(client_ip)

    add_to_whitelist(email=payload.email, source=payload.source)
    return WhitelistOut()
