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
import logging
import time
from datetime import datetime, timezone
from typing import Literal

import jwt
from fastapi import APIRouter, Cookie, Header, HTTPException, Query, Response
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


# Supabase signs user-session JWTs with one of:
#   - HS256 (legacy, shared secret) → verified with settings.supabase_jwt_secret
#   - ES256 / RS256 (modern, asymmetric) → public key fetched from the
#     project's JWKS endpoint at /auth/v1/.well-known/jwks.json
#
# `_decode_supabase_token` dispatches on the JWT `alg` header so both
# signing models are accepted; new asymmetric tokens work without
# changes to env config beyond SUPABASE_URL already being present.
_SUPPORTED_ASYMMETRIC_ALGS = frozenset({"ES256", "RS256", "ES384", "RS384"})

# Memoised so we don't re-instantiate PyJWKClient (and re-fetch the JWK
# set) on every incoming request. Reset on module reload via `del`.
_jwks_client_cache: object | None = None


def _get_jwks_client():
    """Memoised PyJWKClient pointed at this project's JWKS endpoint.

    Returns None if SUPABASE_URL isn't configured. PyJWKClient itself
    caches the JWK set for 5 minutes; clients re-fetch on key rotation.
    """
    global _jwks_client_cache
    if _jwks_client_cache is not None:
        return _jwks_client_cache
    base = (settings.supabase_url or "").rstrip("/")
    if not base:
        return None
    from jwt import PyJWKClient

    jwks_url = f"{base}/auth/v1/.well-known/jwks.json"
    _jwks_client_cache = PyJWKClient(jwks_url, cache_keys=True, lifespan=300)
    return _jwks_client_cache


def _decode_supabase_token(token: str) -> dict:
    """Verify a Supabase-issued JWT and return its payload.

    Dispatches on the JWT's `alg` header so projects on either signing
    model (HS256 shared-secret or ES256/RS256 asymmetric via JWKS) work
    without further configuration beyond what's already in `.env`.
    """
    header = jwt.get_unverified_header(token)
    alg = header.get("alg", "")

    if alg == "HS256":
        if not settings.supabase_jwt_secret:
            raise jwt.PyJWTError("Supabase JWT secret not configured")
        return jwt.decode(
            token,
            settings.supabase_jwt_secret,
            algorithms=["HS256"],
            audience="authenticated",
        )

    if alg in _SUPPORTED_ASYMMETRIC_ALGS:
        client = _get_jwks_client()
        if client is None:
            raise jwt.PyJWTError(
                "Supabase URL not configured — cannot verify asymmetric JWT"
            )
        signing_key = client.get_signing_key_from_jwt(token)
        return jwt.decode(
            token,
            signing_key.key,
            algorithms=[alg],
            audience="authenticated",
        )

    raise jwt.PyJWTError(f"Unsupported JWT algorithm: {alg or 'none'}")


def require_session(
    authorization: str | None = Header(default=None),
    sprntly_app_session: str | None = Cookie(default=None),
    sprntly_demo_session: str | None = Cookie(default=None),
) -> dict:
    """Generic gate — accepts Supabase Bearer JWT or legacy demo cookies.

    Use as `Depends(require_session)` on routes shared by app + demo. The
    function returns the decoded JWT payload (with `aud` set) so callers
    can branch on audience if they need to.
    """
    if authorization and authorization.startswith("Bearer "):
        bearer = authorization.removeprefix("Bearer ").strip()
        if bearer:
            try:
                payload = _decode_supabase_token(bearer)
                return {**payload, "aud": "supabase", "scope": "app"}
            except jwt.PyJWTError:
                pass

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


# ─────────────────────── Tenant resolution (require_company) ───────────────────────
#
# `require_session` answers "who is this user?". `require_company` answers
# "which company (tenant) are they acting in?" — the claim every tenant-
# isolated surface (GraphFacade, connectors, agent routes) scopes by.
#
# Tenancy model (product decision 2026-06-04): a user belongs to exactly ONE
# company; a company has many users. Resolution is therefore a pure lookup —
# the client never passes a company id at all:
#   - Supabase-authenticated sessions only (legacy demo/app cookies carry no
#     user id → 403).
#   - No membership → 403 (finish onboarding first).
#   - Multiple membership rows → data-integrity anomaly (the schema enforces
#     one); fail closed with 500 rather than guess a tenant.


class CompanyContext(BaseModel):
    """Resolved tenant context: pass `company_id` to GraphFacade et al."""

    company_id: str
    role: str
    user_id: str
    user_email: str | None = None


def require_company(
    authorization: str | None = Header(default=None),
    sprntly_app_session: str | None = Cookie(default=None),
    sprntly_demo_session: str | None = Cookie(default=None),
) -> CompanyContext:
    """FastAPI dependency — resolve the authenticated user's company.

    Use as `company: CompanyContext = Depends(require_company)` on any
    tenant-scoped route, then pass `company.company_id` as the
    enterprise_id to GraphFacade / db helpers.
    """
    session = require_session(
        authorization=authorization,
        sprntly_app_session=sprntly_app_session,
        sprntly_demo_session=sprntly_demo_session,
    )
    user_id = session.get("sub")
    if session.get("aud") != "supabase" or not user_id:
        # Legacy cookie sessions have no user identity — they cannot be
        # resolved to a company membership.
        raise HTTPException(403, "Company context requires a signed-in user")

    from app.db.companies import memberships_for_user

    memberships = memberships_for_user(user_id)
    if not memberships:
        raise HTTPException(403, "No company membership — complete onboarding first")
    if len(memberships) > 1:
        # One-company-per-user is the product invariant (and the schema
        # enforces it — see 20260604*_one_company_per_user.sql). More than
        # one row means corrupted membership data; never guess a tenant.
        logging.getLogger(__name__).error(
            "User %s has %d company memberships — data-integrity violation",
            user_id, len(memberships),
        )
        raise HTTPException(500, "Membership data integrity error — contact support")

    only = memberships[0]
    return CompanyContext(
        company_id=only["company_id"], role=only["role"], user_id=user_id,
        user_email=session.get("email"),
    )


def require_company_from_query(
    token: str | None = Query(default=None),
) -> CompanyContext:
    """SSE-only company gate. EventSource cannot send Authorization headers, so
    the bearer rides as ?token=. Validated through the SAME Supabase-JWT decode
    and company-membership resolution as require_company — identical trust. Never
    logs the token.
    """
    if not token:
        raise HTTPException(401, "Not signed in")
    return require_company(authorization=f"Bearer {token}")
