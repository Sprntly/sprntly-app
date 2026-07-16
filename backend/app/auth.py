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
from fastapi import APIRouter, Cookie, Depends, Header, HTTPException, Query, Response
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


def session_email(session: dict) -> str:
    """The signed-in user's email, lowercased. Falls back to the stored
    profile row when the JWT omits `email` (Supabase user-context tokens
    sometimes do). Empty string when neither source has one."""
    email = (session.get("email") or "").strip().lower()
    if email:
        return email
    user_id = session.get("sub")
    if not user_id:
        return ""
    try:
        from app.db.client import require_client

        prof = (
            require_client()
            .table("profiles")
            .select("email")
            .eq("id", user_id)
            .limit(1)
            .execute()
            .data
            or []
        )
        return ((prof[0] if prof else {}).get("email") or "").strip().lower()
    except Exception:  # noqa: BLE001 — treat a lookup failure as "no email"
        return ""


# ─────────────────────── Staff admin surface (/v1/staff) ───────────────────────
#
# The staff admin panel is for the company owner only and deliberately does
# NOT use normal Sprntly (Supabase) login. POST /v1/staff/login (see
# app.routes.staff_admin) checks a dedicated credential pair from env
# (STAFF_ADMIN_ID + STAFF_ADMIN_PASSWORD_HASH) and mints a short-lived JWT
# signed with the app's jwt_secret but with a DISTINCT claim set —
# aud=sprntly-staff, sub=staff, role=staff_admin — so a staff token can never
# pass a user gate and no user/demo/app token can ever pass require_staff
# (Supabase tokens fail the signature; demo/app session tokens share the
# secret but fail the audience check).

STAFF_AUD = "sprntly-staff"
STAFF_SUB = "staff"
STAFF_ROLE = "staff_admin"
STAFF_TOKEN_TTL_HOURS = 12


def staff_surface_enabled() -> bool:
    """True iff BOTH dedicated-credential env vars are set. Either missing ⇒
    every /v1/staff route — login included — 404s (fail closed, invisible),
    the same posture the old STAFF_EMAILS allowlist had when empty."""
    return bool((settings.staff_admin_id or "").strip()) and bool(
        (settings.staff_admin_password_hash or "").strip()
    )


def verify_staff_credentials(admin_id: str, password: str) -> bool:
    """Check the dedicated staff credential pair. Never raises.

    The id compare is constant-time (hmac.compare_digest) and the argon2id
    password verify runs even when the id already failed, so a wrong id costs
    the same as a wrong password — no early exit to time, no enumeration.
    """
    if not staff_surface_enabled():
        return False
    import hmac

    from argon2 import PasswordHasher

    id_ok = hmac.compare_digest(
        (admin_id or "").encode(), settings.staff_admin_id.strip().encode()
    )
    try:
        pw_ok = PasswordHasher().verify(
            settings.staff_admin_password_hash.strip(), password or ""
        )
    except Exception:  # noqa: BLE001 — wrong password / malformed hash ⇒ False
        pw_ok = False
    return bool(id_ok and pw_ok)


def make_staff_token() -> str:
    """Mint the short-lived (12h) staff JWT after a successful login."""
    now = int(time.time())
    payload = {
        "sub": STAFF_SUB,
        "role": STAFF_ROLE,
        "aud": STAFF_AUD,
        "iat": now,
        "exp": now + STAFF_TOKEN_TTL_HOURS * 3600,
    }
    return jwt.encode(payload, settings.jwt_secret, algorithm=JWT_ALG)


def require_staff(authorization: str | None = Header(default=None)) -> dict:
    """Gate for the Sprntly-staff admin surface (/v1/staff).

    Requires the dedicated staff JWT as `Authorization: Bearer …`. Anything
    else — no token, an expired/garbage token, a Supabase user token, a
    demo/app session token, or the surface being disabled via env — gets a
    404 (not 401/403) so the staff surface is invisible, mirroring the
    design-agent feature gate.
    """
    if not staff_surface_enabled():
        raise HTTPException(404, "Not found")
    token = ""
    if authorization and authorization.startswith("Bearer "):
        token = authorization.removeprefix("Bearer ").strip()
    if not token:
        raise HTTPException(404, "Not found")
    try:
        payload = jwt.decode(
            token, settings.jwt_secret, algorithms=[JWT_ALG], audience=STAFF_AUD
        )
    except jwt.PyJWTError as e:
        raise HTTPException(404, "Not found") from e
    if payload.get("sub") != STAFF_SUB or payload.get("role") != STAFF_ROLE:
        raise HTTPException(404, "Not found")
    return payload


def require_app_session(
    sprntly_app_session: str | None = Cookie(default=None),
) -> dict:
    """Locked to the app audience. Demo sessions are rejected even if present.

    Cookie-only by design: the Supabase Bearer path lives in `require_session`
    / `require_company` (the app-wide signed-in-user + tenant deps). Folding a
    Bearer fallback in here was a scratch dev-hack (#143 excluded it) and broke
    the positional `require_app_session(<session>)` contract the auth tests
    assert — the session cookie is the first and only positional argument.
    """
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
    user_name: str | None = None


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
    try:
        from app.db.client import require_client
        c = require_client()
        profile_resp = c.table("profiles").select("full_name, first_name, last_name").eq("id", user_id).limit(1).execute()
        profile = profile_resp.data[0] if profile_resp.data else {}
        _first = profile.get("first_name") or ""
        _last = profile.get("last_name") or ""
        user_name = (
            profile.get("full_name")
            or f"{_first} {_last}".strip()
            or None
        )
    except Exception:
        user_name = None
    return CompanyContext(
        company_id=only["company_id"], role=only["role"], user_id=user_id,
        user_email=session.get("email"),
        user_name=user_name,
    )


def company_id_for_request(
    authorization: str | None,
    sprntly_app_session: str | None,
    sprntly_demo_session: str | None,
) -> str | None:
    """Best-effort tenant id from raw request credentials — for the LLM-key
    binding middleware (app.middleware_llm_key). No profile lookup, never raises:
    any non-resolvable case (no/invalid token, legacy cookie session, no or
    ambiguous membership) returns None, leaving the request unbound (platform
    key). Not a FastAPI dependency."""
    try:
        session = require_session(
            authorization=authorization,
            sprntly_app_session=sprntly_app_session,
            sprntly_demo_session=sprntly_demo_session,
        )
    except HTTPException:
        return None
    user_id = session.get("sub")
    if session.get("aud") != "supabase" or not user_id:
        return None
    try:
        from app.db.companies import memberships_for_user

        memberships = memberships_for_user(user_id)
    except Exception:  # noqa: BLE001 — never break a request on a lookup error
        return None
    if len(memberships) == 1:
        return memberships[0].get("company_id")
    return None


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


def resolve_company_optional(
    authorization: str | None = Header(default=None),
    sprntly_app_session: str | None = Cookie(default=None),
    sprntly_demo_session: str | None = Cookie(default=None),
) -> CompanyContext | None:
    """Best-effort tenant resolution for surfaces that work WITH or WITHOUT a
    company (e.g. Ask: corpus-only for legacy cookie sessions, corpus+KG when a
    company resolves). Returns the CompanyContext when a Supabase-authenticated
    user has exactly one membership; returns None for any non-resolvable case
    (legacy cookie session, no membership, integrity anomaly) instead of
    raising. The route's primary auth gate still runs via require_session."""
    try:
        return require_company(
            authorization=authorization,
            sprntly_app_session=sprntly_app_session,
            sprntly_demo_session=sprntly_demo_session,
        )
    except HTTPException:
        return None


# ─────────────────── Active-workspace resolution (require_workspace) ───────────────────
#
# Multi-workspace (2026-07): a company has N workspaces; workspace-scoped
# surfaces (briefs/PRDs/tickets/chat) additionally resolve WHICH workspace the
# request acts in. The client sends the active workspace as the
# `X-Workspace-Id` header (SSE: `?workspace_id=`); a missing header falls back
# to the company's default workspace so old clients keep working.
#
# Two-level roles: org owner/admin implicitly administer every workspace
# (workspace_role='admin', no workspace_members row needed); plain org
# members/viewers need a workspace_members row or the request 403s. A
# workspace id from another company 404s (existence non-disclosure).


class WorkspaceContext(CompanyContext):
    """CompanyContext + the resolved active workspace."""

    workspace_id: str
    workspace_role: str  # 'admin' | 'member' | 'viewer'
    workspace_is_default: bool = False


def _resolve_workspace(
    company: CompanyContext, workspace_id: str | None
) -> WorkspaceContext:
    from app.db.workspaces import (
        ensure_default_workspace,
        get_workspace,
        get_workspace_member,
    )

    if workspace_id:
        ws = get_workspace(workspace_id)
        if not ws or ws.get("company_id") != company.company_id:
            raise HTTPException(404, "Workspace not found")
    else:
        # Backward compat: no header → the default workspace (self-healing
        # for companies whose default row was never created).
        ws = ensure_default_workspace(company.company_id)

    if company.role in ("owner", "admin"):
        workspace_role = "admin"
    else:
        member = get_workspace_member(ws["id"], company.user_id)
        if not member:
            raise HTTPException(403, "Not a member of this workspace")
        workspace_role = member.get("role") or "member"

    return WorkspaceContext(
        **company.model_dump(),
        workspace_id=ws["id"],
        workspace_role=workspace_role,
        workspace_is_default=bool(ws.get("is_default")),
    )


def require_workspace(
    company: CompanyContext = Depends(require_company),
    x_workspace_id: str | None = Header(default=None, alias="X-Workspace-Id"),
) -> WorkspaceContext:
    """FastAPI dependency — resolve company AND active workspace.

    Use on workspace-scoped routes:
      ctx: WorkspaceContext = Depends(require_workspace)
    then filter by ctx.workspace_id (and keep writing ctx.company_id).

    Chains require_company as a REAL sub-dependency (not a direct call) so
    dependency_overrides on require_company keep working — the test suites
    override the company gate and expect every downstream gate to honor it.
    """
    return _resolve_workspace(company, x_workspace_id)


def require_workspace_from_query(
    company: CompanyContext = Depends(require_company_from_query),
    workspace_id: str | None = Query(default=None),
) -> WorkspaceContext:
    """SSE-only workspace gate (EventSource can't send headers, so the bearer
    rides as ?token= and the active workspace as ?workspace_id= — optional,
    with the same default-workspace fallback)."""
    return _resolve_workspace(company, workspace_id)


def require_workspace_admin(ctx: WorkspaceContext) -> None:
    """Guard for workspace-mutating handlers (rename/member management)."""
    if ctx.workspace_role != "admin":
        raise HTTPException(403, "Workspace management is restricted to admins")
