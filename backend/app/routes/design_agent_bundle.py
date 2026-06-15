"""Design-Agent bundle PROXY (Option B — same-origin serving, Decision 2).

This router is the ONE authorizing streaming front door for every prototype
bundle object. Per the approved plan it replaces the old "signed Supabase URL to
index.html only" path, which blank-rendered in prod (relative `./assets/*`
resolved against a signed URL, dropping the `?token=`) and could not serve
sub-assets at all.

Three serving modes, one shared serve function (auth check at the TOP, before any
storage read — index.html and deep assets BOTH flow through it):

    GET  /v1/design-agent/by-token/{token}/bundle/{asset_path:path}   public/passcode
    GET  /v1/design-agent/{prototype_id}/bundle/{asset_path:path}     authed twin
    POST /v1/design-agent/{prototype_id}/view-grant                   mint da_view_grant

Guardrails (all load-bearing — see the plan §2-§8 + §16):
  1. TRAVERSAL — `storage._is_safe_bundle_relpath` (single unquote, reject
     `..`/leading-`/`/absolute/backslash/`%2e%2e`/NUL/CR/LF/control). Containment
     re-asserted in `storage._safe_object_key` BEFORE any create_signed_url (SSRF).
  2. PER-OBJECT AUTH — every GET re-resolves share_mode from the DB. public →
     find_by_token deny logic; passcode → grant cookie required; authed →
     da_view_grant HMAC + per-object DB re-read.
  3. URL↔GRANT EQUALITY — the URL's {prototype_id}/{token} is compared against the
     HMAC payload's bound value (path-scope is browser-side only; this is the
     server-side gate against cross-prototype grant replay).
  4. REVOCATION + CACHE — authed/passcode: `private, no-store` + `Vary: Cookie`,
     per-object DB re-read makes a flip-to-private deny the NEXT asset instantly.
     public: `public, max-age=60, must-revalidate` (no checkpoint in public URL),
     so public revocation is bounded by cache (~60s if a CDN sits in front;
     instant-to-backend if browser-only) — the asymmetry is intentional.
  5. TOKEN-SECRET FAIL-CLOSED — if DESIGN_AGENT_TOKEN_SECRET is empty, mint AND
     validate BOTH refuse (503 mint / reject validate). Never serve with a
     forgeable/unsigned grant.
  6. MIME — Content-Type set at serve time via storage._content_type; the table is
     extended for fonts/images/maps. `.b64` sentinel assets decode before serve.
  7. STREAM + RANGE — Supabase: server-side sign + httpx.stream forwarding Range.
     Filesystem: Starlette FileResponse. Bytes stream; objects are never buffered
     whole (except rare `.b64` binaries).
  8. HOST-TRUST — the proxy base in bundle_url is built from a CONFIG-derived
     app-origin constant (settings.design_agent_bundle_base), NEVER the Host header.
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import json
import logging
import time
from urllib.parse import unquote

from fastapi import APIRouter, Cookie, Depends, HTTPException, Request, Response

from app.auth import CompanyContext, require_company
from app.config import settings
from app.db.prototypes import (
    find_prototype_by_share_token,
    get_prototype,
    passcode_rate_limit_check,
    passcode_rate_limit_register_failure,
)
from app.design_agent import storage

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/v1/design-agent", tags=["design-agent-bundle"])

# Grant cookie names — DISTINCT from the auth session cookies (auth.py) so they
# never collide. HttpOnly+Secure; the iframe never reads them, only sends them.
VIEW_GRANT_COOKIE = "da_view_grant"
SHARE_GRANT_COOKIE = "da_share_grant"

# Short grant TTL — the per-object DB re-read is the hard revocation gate; the TTL
# is the backstop (plan §1.3 / §16-1). 600s keeps a long authed viewing session
# from re-minting more than ~once.
_GRANT_TTL_SECONDS = 600

# Cache headers per mode (plan §4 asymmetry — STATED here intentionally):
#   - authed/passcode: never cache (per-object DB re-read = INSTANT revocation;
#     a flip-to-private denies the NEXT asset with zero lag). Vary: Cookie so a
#     shared cache never serves one viewer's asset to another.
#   - public: short public cache (no checkpoint in the public URL). Public
#     revocation is therefore bounded by this cache (~60s if a CDN is in front;
#     instant-to-backend if browser-only) — a deliberate, documented asymmetry vs
#     the instant authed/passcode revocation.
_CACHE_PRIVATE = "private, no-store"
_CACHE_PUBLIC = "public, max-age=60, must-revalidate"

# Per-(workspace, prototype) view-grant mint rate limit. Reuses the in-process
# passcode limiter primitive (same per-process caveat as the passcode path — NOT
# distributed; resets on restart / under multi-instance). Keyed below.
_VIEW_GRANT_RL_PREFIX = "viewgrant:"


# ─── token-secret fail-closed (plan §16-token / Part C) ──────────────────────


def _require_token_secret() -> str:
    """Return DESIGN_AGENT_TOKEN_SECRET or FAIL CLOSED (503).

    Called before any hmac.new(...) on BOTH the mint and validate paths. An empty
    secret means the grant HMAC is forgeable, so we NEVER sign or compare with
    b"" — we refuse the request. This guards mint (no Set-Cookie) AND validate
    (any presented grant rejected) so there is no silent/forgeable serve."""
    secret = settings.design_agent_token_secret or ""
    if not secret:
        logger.error("da_bundle_token_secret_unset — failing closed (no grant serve)")
        raise HTTPException(status_code=503, detail="bundle proxy unavailable")
    return secret


# ─── grant cookie mint / validate (HMAC over the bound tuple) ────────────────


def _sign_grant(payload: dict) -> str:
    """`b64url(json(payload)) + "." + hex(HMAC-SHA256(secret, b64url))`."""
    secret = _require_token_secret()
    body = base64.urlsafe_b64encode(json.dumps(payload, separators=(",", ":")).encode()).decode().rstrip("=")
    mac = hmac.new(secret.encode(), body.encode(), hashlib.sha256).hexdigest()
    return f"{body}.{mac}"


def _verify_grant(raw: str | None) -> dict | None:
    """Constant-time-verify a grant cookie; return the payload dict or None.

    Fails closed when the secret is unset (raises 503 via _require_token_secret —
    a forgeable grant is never accepted). Returns None on a malformed value, a
    bad/forged MAC, or an expired payload."""
    secret = _require_token_secret()  # fail-closed on empty secret (validate half)
    if not raw or "." not in raw:
        return None
    body, _, mac = raw.partition(".")
    expected = hmac.new(secret.encode(), body.encode(), hashlib.sha256).hexdigest()
    if not hmac.compare_digest(mac, expected):
        return None
    try:
        padded = body + "=" * (-len(body) % 4)
        payload = json.loads(base64.urlsafe_b64decode(padded.encode()).decode())
    except Exception:
        return None
    exp = payload.get("exp")
    if not isinstance(exp, (int, float)) or exp < time.time():
        return None
    return payload


def _grant_cookie_kwargs(path: str) -> dict:
    """Cookie attrs for a grant. Chosen SameSite=Lax (plan §16-3): under
    Decision 2 the iframe is same-origin to the app parent (`app.sprntly.ai`), so
    its subresource asset GETs are SAME-SITE → Lax is attached. This is
    TESTER-VERIFY-REQUIRED in the same-origin repro; if the iframe ever moves off
    the app origin, fall back to `SameSite=None; Secure; Partitioned`. HttpOnly so
    the iframe never reads it; Secure in prod (settings.cookie_secure)."""
    return {
        "max_age": _GRANT_TTL_SECONDS,
        "httponly": True,
        "secure": settings.cookie_secure,
        "samesite": "lax",
        "path": path,
        "domain": settings.cookie_domain or None,
    }


def _view_grant_path(prototype_id: int) -> str:
    """Browser-side path-scope for da_view_grant — the cookie is only sent to this
    prototype's bundle route (a grant for one prototype is never sent to another's
    asset GETs). The SERVER-SIDE gate is URL↔grant equality (#3); path-scope is
    defence in depth only."""
    prefix = "/" + (settings.design_agent_bundle_path_prefix or "").strip("/")
    if prefix == "/":
        prefix = ""
    return f"{prefix}/v1/design-agent/{prototype_id}/bundle"


def _share_grant_path(token: str) -> str:
    prefix = "/" + (settings.design_agent_bundle_path_prefix or "").strip("/")
    if prefix == "/":
        prefix = ""
    return f"{prefix}/v1/design-agent/by-token/{token}/bundle"


# ─── feature gate (mirror design_agent._require_feature_enabled) ─────────────


def _feature_enabled() -> bool:
    import os

    val = (os.environ.get("DESIGN_AGENT_ENABLED") or "").strip().lower()
    return val in ("1", "true", "yes", "on")


def _require_feature() -> None:
    if not _feature_enabled():
        raise HTTPException(status_code=404, detail="Not found")


# ─── shared asset-path validation ────────────────────────────────────────────


def _decode_asset_path(asset_path: str) -> str:
    """Single unquote + reject traversal/header-metachars. 404 on any rejection
    (invisibility — never disclose why). Strips a `?v=` cache-buster defensively
    (FastAPI keeps the query separate, but the {asset_path:path} capture must not
    carry one). Returns the safe relative path."""
    # Strip an accidental query echo on the path segment.
    decoded = unquote(asset_path)
    decoded = decoded.split("?", 1)[0].split("#", 1)[0]
    if not storage._is_safe_bundle_relpath(decoded):
        raise HTTPException(status_code=404, detail="Not found")
    return decoded


# ─── the ONE shared serve function ───────────────────────────────────────────


async def _serve(
    *,
    prototype_id: int,
    checkpoint_id: int,
    rel_path: str,
    cache_control: str,
    request: Request,
    extra_headers: dict | None = None,
) -> Response:
    """Stream the bundle object. Auth was ALREADY enforced by the caller (per-mode
    resolver); this only does the contained read + stream. Containment is
    re-asserted inside storage.serve_bundle_object before any sign/open."""
    range_header = request.headers.get("range")
    try:
        return await storage.serve_bundle_object(
            prototype_id=prototype_id,
            checkpoint_id=checkpoint_id,
            rel_path=rel_path,
            range_header=range_header,
            cache_control=cache_control,
            extra_headers=extra_headers,
        )
    except storage.BundleObjectNotFound:
        raise HTTPException(status_code=404, detail="Not found")


def _checkpoint_for_row(row: dict) -> int | None:
    return row.get("current_checkpoint_id")


# ─── PUBLIC / PASSCODE serve ─────────────────────────────────────────────────


@router.get("/by-token/{token}/bundle/{asset_path:path}")
async def serve_public_bundle(
    token: str,
    asset_path: str,
    request: Request,
    da_share_grant: str | None = Cookie(default=None),
) -> Response:
    """Serve a bundle object for a PUBLIC or PASSCODE share (token-in-URL, F6).

    Per-object auth (plan §3): every GET re-runs find_prototype_by_share_token and
    re-applies the get_by_token deny logic (404 on missing / private / not-ready).
    For PASSCODE mode the da_share_grant cookie must also validate (HMAC bound to
    this token); no valid grant → 404. Cache: public = short public cache;
    passcode = private/no-store (revocation-instant)."""
    _require_feature()
    rel_path = _decode_asset_path(asset_path)
    row = find_prototype_by_share_token(token)
    # Re-apply the deny logic verbatim (design_agent.get_by_token).
    if not row or row.get("share_mode") == "private" or row.get("status") != "ready":
        raise HTTPException(status_code=404, detail="Not found")

    checkpoint_id = _checkpoint_for_row(row)
    if checkpoint_id is None:
        raise HTTPException(status_code=404, detail="Not found")

    mode = row["share_mode"]
    if mode == "passcode":
        # PASSCODE: require a valid da_share_grant bound to THIS token (plan §5).
        payload = _verify_grant(da_share_grant)
        if (
            not payload
            or payload.get("share_mode") != "passcode"
            or payload.get("token") != token
            or payload.get("checkpoint_id") != checkpoint_id
        ):
            # No valid grant → 404 (invisibility, same as a wrong token).
            raise HTTPException(status_code=404, detail="Not found")
        cache = _CACHE_PRIVATE
        extra = {"Vary": "Cookie"}
    else:
        # PUBLIC: token suffices; short public cache (no checkpoint in URL).
        cache = _CACHE_PUBLIC
        extra = None

    return await _serve(
        prototype_id=row["id"],
        checkpoint_id=checkpoint_id,
        rel_path=rel_path,
        cache_control=cache,
        request=request,
        extra_headers=extra,
    )


def set_share_grant_cookie(response: Response, *, token: str, checkpoint_id: int) -> None:
    """Set the scoped da_share_grant cookie on a passcode-verify success (plan §5).

    Called from the EXISTING `design_agent.verify_passcode` route (NOT a second
    route at the same path) so the public-view response body is preserved and the
    grant cookie is added. HMAC bound to token + checkpoint_id + share_mode +
    exp; fails closed (503) on an unset token secret via `_sign_grant`."""
    grant = _sign_grant({
        "token": token,
        "checkpoint_id": checkpoint_id,
        "share_mode": "passcode",
        "exp": int(time.time()) + _GRANT_TTL_SECONDS,
    })
    response.set_cookie(SHARE_GRANT_COOKIE, grant, **_grant_cookie_kwargs(_share_grant_path(token)))
    logger.info("da_share_grant_minted token_hash=%s", hashlib.sha256(token.encode()).hexdigest()[:8])


# ─── AUTHED twin: serve + view-grant mint ────────────────────────────────────


@router.post("/{prototype_id}/view-grant", status_code=204)
def mint_view_grant(
    prototype_id: int,
    request: Request,
    company: CompanyContext = Depends(require_company),
) -> Response:
    """Mint a da_view_grant for the AUTHED viewer (plan §1.2 / §16-2).

    Bearer-authed via require_company. RE-RESOLVES that the caller's workspace
    OWNS the prototype (get_prototype filtered by workspace_id) — 404 on miss
    (MUST NOT mint for a non-owned prototype; matches the workspace-isolation deny
    shape). Rate-limited per (workspace, prototype) — over-limit → 429. Fails
    closed on an unset token secret (_sign_grant → _require_token_secret → 503).
    Returns 204; the HttpOnly cookie is the payload."""
    _require_feature()

    # Rate-limit the mint per (workspace, prototype) — reuse the passcode limiter.
    rl_key = f"{_VIEW_GRANT_RL_PREFIX}{company.company_id}:{prototype_id}"
    if not passcode_rate_limit_check(token=rl_key, ip="0.0.0.0"):
        raise HTTPException(status_code=429, detail="Too many attempts")

    row = get_prototype(prototype_id=prototype_id, workspace_id=company.company_id)
    if not row:
        # Not owned by this workspace (or gone) → 404, NOT 403 (invisibility).
        passcode_rate_limit_register_failure(token=rl_key)
        raise HTTPException(status_code=404, detail="Not found")

    checkpoint_id = _checkpoint_for_row(row)
    if checkpoint_id is None:
        raise HTTPException(status_code=404, detail="Not found")

    grant = _sign_grant({
        "prototype_id": prototype_id,
        "checkpoint_id": checkpoint_id,
        "workspace_id": company.company_id,
        # Bind the row's share_mode AT MINT TIME. The serve path denies if the
        # current mode no longer matches — so a flip to a different mode (e.g.
        # public→private) AFTER the grant was minted revokes it on the NEXT asset
        # even under a still-valid unexpired grant (plan §16-5 crux). A normal
        # owner-only prototype mints+serves as 'private' (match → serve).
        "share_mode": row.get("share_mode") or "private",
        "grant_kind": "authed",
        "exp": int(time.time()) + _GRANT_TTL_SECONDS,
    })
    out = Response(status_code=204)
    out.set_cookie(VIEW_GRANT_COOKIE, grant, **_grant_cookie_kwargs(_view_grant_path(prototype_id)))
    logger.info("da_view_grant_minted prototype_id=%s", prototype_id)
    return out


@router.get("/{prototype_id}/bundle/{asset_path:path}")
async def serve_authed_bundle(
    prototype_id: int,
    asset_path: str,
    request: Request,
    da_view_grant: str | None = Cookie(default=None),
) -> Response:
    """Serve a bundle object for the AUTHED twin via the da_view_grant cookie.

    Per-object auth + REVOCATION (plan §1.3 / §16-5): the grant proves IDENTITY,
    not current authorization. EVERY GET:
      1. validates the HMAC + expiry (fail-closed on unset secret);
      2. URL↔GRANT EQUALITY (#3) — the URL's prototype_id MUST equal the grant's
         bound prototype_id (defeats cross-prototype replay; path-scope is
         browser-side only);
      3. re-reads the prototype from the DB filtered by the grant's workspace_id —
         a flip to private / a workspace mismatch / a checkpoint advance denies the
         NEXT asset even with a still-valid unexpired grant (the crux of Option B).
    Cache: private, no-store + Vary: Cookie (instant revocation)."""
    _require_feature()
    rel_path = _decode_asset_path(asset_path)

    payload = _verify_grant(da_view_grant)  # fail-closed on unset secret
    if not payload or payload.get("grant_kind") != "authed":
        raise HTTPException(status_code=401, detail="grant required")

    # (#3) URL↔GRANT EQUALITY — bound prototype_id must match the URL's.
    if payload.get("prototype_id") != prototype_id:
        raise HTTPException(status_code=401, detail="grant mismatch")

    workspace_id = payload.get("workspace_id")
    if not isinstance(workspace_id, str) or not workspace_id:
        raise HTTPException(status_code=401, detail="grant invalid")

    # Per-object DB re-read — the authorization gate (NOT the grant). A
    # workspace-mismatch / checkpoint-advance / share-mode-flip denies here, on
    # the NEXT asset, even under a still-valid unexpired grant.
    row = get_prototype(prototype_id=prototype_id, workspace_id=workspace_id)
    if not row or row.get("status") != "ready":
        raise HTTPException(status_code=404, detail="Not found")

    checkpoint_id = _checkpoint_for_row(row)
    if checkpoint_id is None:
        raise HTTPException(status_code=404, detail="Not found")
    # A Resume that advanced the checkpoint invalidates the grant → 401 (re-mint).
    if payload.get("checkpoint_id") != checkpoint_id:
        raise HTTPException(status_code=401, detail="grant stale")

    # REVOCATION CRUX (plan §16-5): the grant binds the row's share_mode at mint
    # time. If the current mode no longer matches (e.g. an owner flipped a shared
    # prototype to 'private', or a private one to 'public'), DENY the NEXT asset
    # even though the grant HMAC is still valid + unexpired. The per-object DB
    # re-read — not the grant — is the authorization gate.
    if row.get("share_mode") != payload.get("share_mode"):
        raise HTTPException(status_code=404, detail="Not found")

    return await _serve(
        prototype_id=prototype_id,
        checkpoint_id=checkpoint_id,
        rel_path=rel_path,
        cache_control=_CACHE_PRIVATE,
        request=request,
        extra_headers={"Vary": "Cookie"},
    )
