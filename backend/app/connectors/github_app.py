"""GitHub App helpers.

Two distinct token modes live in a GitHub App:

  - User-to-server OAuth — the user clicks "Connect GitHub",
    redirects through GitHub's consent screen, returns with a code,
    we exchange for an access_token (+ refresh_token if expiration
    is enabled on the app). This is what /v1/connectors/github/*
    handles. Stored under provider="github".

  - App-as-app JWT + installation tokens — the app proves it is
    itself by signing a short JWT with its RSA private key, then
    swaps the JWT for an installation access_token (1-hour TTL) that
    grants the App's declared permissions on whichever repos the
    installer chose. Used for server-side repo operations (creating
    PRs, reading repos) without a user present. Cached in-process
    with a 55-min TTL.

The stored user-OAuth payload is GitHub's literal token response plus
an `obtained_at` epoch, JSON-encoded then Fernet-encrypted (same
pattern as the Google + Figma connectors).
"""
from __future__ import annotations

import json
import logging
import time
import uuid
from typing import Any

import jwt
import requests
from fastapi import HTTPException

from app.config import settings

logger = logging.getLogger(__name__)

GITHUB_PROVIDER = "github"
GITHUB_AUTH_URL = "https://github.com/login/oauth/authorize"
GITHUB_TOKEN_URL = "https://github.com/login/oauth/access_token"
GITHUB_USER_URL = "https://api.github.com/user"
DEFAULT_SCOPES = "read:user user:email"  # most repo perms come from the App install, not the OAuth scope
JWT_ALG_STATE = "HS256"
JWT_ALG_APP = "RS256"
STATE_TTL_SECONDS = 600
# GitHub max JWT lifetime is 10 minutes; we use 8 to be safe.
APP_JWT_TTL_SECONDS = 8 * 60


def github_oauth_configured() -> bool:
    return bool(
        settings.github_app_client_id
        and settings.github_app_client_secret
        and settings.github_oauth_redirect_uri
    )


def github_app_configured() -> bool:
    return bool(settings.github_app_id and settings.github_app_private_key_pem)


# ─────────────────────── user OAuth flow ───────────────────────

def authorize_url(state: str, scopes: str | None = None) -> str:
    if not github_oauth_configured():
        raise HTTPException(500, "GitHub OAuth is not configured on the server")
    from urllib.parse import urlencode
    params = {
        "client_id": settings.github_app_client_id,
        "redirect_uri": settings.github_oauth_redirect_uri,
        "scope": scopes or DEFAULT_SCOPES,
        "state": state,
        "allow_signup": "true",
    }
    return f"{GITHUB_AUTH_URL}?{urlencode(params)}"


def sign_oauth_state(
    *, company_id: str, return_to: str | None = None,
) -> str:
    """Mint a signed state JWT that binds the OAuth round-trip to a
    specific company. The callback (which has no user session) trusts
    only this signature to know which company gets the new token.

    `return_to` is an optional relative path the callback redirects
    to instead of the default /settings?section=connectors."""
    now = int(time.time())
    payload = {
        "provider": GITHUB_PROVIDER,
        "company_id": company_id,
        "return_to": return_to,
        "nonce": uuid.uuid4().hex,
        "iat": now,
        "exp": now + STATE_TTL_SECONDS,
    }
    return jwt.encode(payload, settings.jwt_secret, algorithm=JWT_ALG_STATE)


def verify_oauth_state(state: str) -> dict:
    try:
        payload = jwt.decode(state, settings.jwt_secret, algorithms=[JWT_ALG_STATE])
    except jwt.PyJWTError as e:
        raise HTTPException(400, "Invalid or expired OAuth state") from e
    if payload.get("provider") != GITHUB_PROVIDER:
        raise HTTPException(400, "OAuth state provider mismatch")
    if not payload.get("company_id"):
        raise HTTPException(400, "OAuth state missing company_id")
    return payload


def exchange_code_for_token(code: str) -> dict[str, Any]:
    if not github_oauth_configured():
        raise HTTPException(500, "GitHub OAuth is not configured on the server")
    resp = requests.post(
        GITHUB_TOKEN_URL,
        headers={"Accept": "application/json"},
        data={
            "client_id": settings.github_app_client_id,
            "client_secret": settings.github_app_client_secret,
            "code": code,
            "redirect_uri": settings.github_oauth_redirect_uri,
        },
        timeout=15,
    )
    if not resp.ok:
        logger.warning("GitHub token exchange failed: %s %s", resp.status_code, resp.text[:300])
        raise HTTPException(400, "GitHub token exchange failed")
    data = resp.json()
    if "error" in data:
        # GitHub returns 200 + {"error": "..."} on application errors
        logger.warning("GitHub token exchange error payload: %s", data.get("error"))
        raise HTTPException(400, f"GitHub token exchange error: {data.get('error')}")
    if "access_token" not in data:
        raise HTTPException(400, "GitHub did not return an access_token")
    return data


def refresh_user_token(refresh_token: str) -> dict[str, Any]:
    """User tokens expire ~8h if the App was registered with token expiration on."""
    if not github_oauth_configured():
        raise HTTPException(500, "GitHub OAuth is not configured on the server")
    resp = requests.post(
        GITHUB_TOKEN_URL,
        headers={"Accept": "application/json"},
        data={
            "client_id": settings.github_app_client_id,
            "client_secret": settings.github_app_client_secret,
            "grant_type": "refresh_token",
            "refresh_token": refresh_token,
        },
        timeout=15,
    )
    if not resp.ok:
        logger.warning("GitHub refresh failed: %s %s", resp.status_code, resp.text[:300])
        raise HTTPException(400, "GitHub token refresh failed")
    return resp.json()


def fetch_authenticated_user(access_token: str) -> dict[str, Any]:
    resp = requests.get(
        GITHUB_USER_URL,
        headers={
            "Authorization": f"Bearer {access_token}",
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28",
        },
        timeout=10,
    )
    if not resp.ok:
        logger.warning("GitHub /user failed: %s %s", resp.status_code, resp.text[:200])
        return {}
    return resp.json()


def token_payload_to_store(token_json: dict[str, Any]) -> str:
    payload = dict(token_json)
    payload["obtained_at"] = int(time.time())
    return json.dumps(payload)


# ─────────────────────── app-as-app JWT ───────────────────────


def make_app_jwt() -> str:
    """Sign a short-lived JWT that proves we are the GitHub App itself.

    Use this with /app/installations and /app/installations/{id}/access_tokens
    to obtain installation tokens for server-side repo operations.
    """
    if not github_app_configured():
        raise HTTPException(500, "GitHub App private key / app_id not configured")
    now = int(time.time())
    payload = {
        "iat": now - 30,                            # clock skew tolerance
        "exp": now + APP_JWT_TTL_SECONDS,
        "iss": str(settings.github_app_id),
    }
    return jwt.encode(payload, settings.github_app_private_key_pem, algorithm=JWT_ALG_APP)


# ─────────────────────── installation tokens ───────────────────────

# Tokens live 1 hour; we refresh ~5 min early.
_INSTALL_TOKEN_TTL_SAFETY_S = 5 * 60
_install_token_cache: dict[int, tuple[str, int]] = {}


def get_installation_token(installation_id: int) -> str:
    """Return a fresh installation access_token, caching by installation_id.

    Hits POST https://api.github.com/app/installations/{id}/access_tokens with
    an App JWT in the Authorization header. The returned token grants the
    App's declared permissions on whichever repos this installation has access
    to. Auto-refreshes 5 minutes before GitHub-side expiry.
    """
    now = int(time.time())
    cached = _install_token_cache.get(installation_id)
    if cached and cached[1] - now > _INSTALL_TOKEN_TTL_SAFETY_S:
        return cached[0]

    if not github_app_configured():
        raise HTTPException(500, "GitHub App not configured")

    resp = requests.post(
        f"https://api.github.com/app/installations/{installation_id}/access_tokens",
        headers={
            "Authorization": f"Bearer {make_app_jwt()}",
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28",
        },
        timeout=15,
    )
    if not resp.ok:
        logger.warning(
            "Installation token fetch failed: %s %s",
            resp.status_code,
            resp.text[:300],
        )
        raise HTTPException(502, f"GitHub installation token fetch failed: {resp.status_code}")
    payload = resp.json()
    token = payload.get("token")
    if not token:
        raise HTTPException(502, "GitHub did not return an installation token")

    # Parse ISO-8601 expires_at; fall back to "now + 1 hour" if missing.
    exp_iso = payload.get("expires_at")
    expires_epoch = now + 3600
    if exp_iso:
        try:
            import datetime as _dt
            expires_epoch = int(
                _dt.datetime.strptime(exp_iso, "%Y-%m-%dT%H:%M:%SZ")
                .replace(tzinfo=_dt.timezone.utc)
                .timestamp()
            )
        except ValueError:
            pass
    _install_token_cache[installation_id] = (token, expires_epoch)
    # Self-heal: on a REAL mint (cache-miss path only), backfill a thin install
    # row's account metadata. The picker, grounding, and the health probe all
    # mint through here, so a skeleton row heals on its first token use whichever
    # path runs first. Fully fail-open — it can never affect the returned token.
    maybe_hydrate_installation(installation_id)
    return token


def maybe_hydrate_installation(installation_id: int) -> bool:
    """Best-effort self-heal of a thin ``github_installations`` row.

    A thin row (``account_login=''`` or ``account_id=0``) is a usable install
    whose intrinsic GitHub account metadata was never populated (pre-hydration
    install, or an install-time ``fetch_app_installation`` race). When a token is
    minted for it, backfill that metadata from GitHub's App API so the
    ``account_login`` resolution path works and the row stops looking like a
    skeleton.

    Tenant safety (load-bearing): hydration only writes install-INTRINSIC account
    fields (login/id/type from GitHub) and passes the row's EXISTING ``company_id``
    straight through to the upsert — it NEVER changes or re-keys the tenant
    binding. ``account_login`` is a property of the installation, not of the
    tenant, so backfilling it cannot leak an install across companies.

    Fail-open: any error logs a warning and returns False. A hydration failure
    must never break token mint or generation. Returns True iff a thin row was
    hydrated.
    """
    try:
        # Lazy import avoids any import cycle at module load (app.db.github does
        # not import this module, but keep the boundary explicit).
        from app import db

        row = db.get_github_installation(installation_id)
        if not row:
            return False
        thin = (not row.get("account_login")) or int(row.get("account_id") or 0) == 0
        if not thin:
            return False  # cheap no-op for healthy rows
        detail = fetch_app_installation(installation_id)
        if not detail:
            return False
        acct = (detail or {}).get("account") or {}
        db.upsert_github_installation(
            installation_id=installation_id,
            account_id=int(acct.get("id") or 0),
            account_login=str(acct.get("login") or ""),
            account_type=str(acct.get("type") or "User"),
            repository_selection=str(
                detail.get("repository_selection")
                or row.get("repository_selection")
                or "selected"
            ),
            suspended=bool(row.get("suspended") or False),
            permissions=detail.get("permissions")
            or json.loads(row.get("permissions_json") or "{}"),
            events=detail.get("events")
            or json.loads(row.get("events_json") or "[]"),
            # Preserve the existing tenant binding; never re-key across companies.
            company_id=row.get("company_id"),
        )
        return True
    except Exception:
        logger.warning(
            "GitHub installation lazy-hydration failed for %s",
            installation_id,
            exc_info=True,
        )
        return False


def fetch_app_installation(installation_id: int) -> dict | None:
    """Fetch an installation's detail via the App JWT: GET /app/installations/{id}.
    Returns the raw GitHub object (account/permissions/events/repository_selection)
    or None if the App isn't configured or GitHub rejects the lookup. Used to
    backfill install details when the OAuth callback binds a company before the
    webhook has populated the row."""
    if not github_app_configured():
        return None
    try:
        resp = requests.get(
            f"{GITHUB_API_BASE}/app/installations/{installation_id}",
            headers={
                "Authorization": f"Bearer {make_app_jwt()}",
                "Accept": "application/vnd.github+json",
                "X-GitHub-Api-Version": "2022-11-28",
            },
            timeout=15,
        )
    except Exception:
        return None
    if not resp.ok:
        logger.warning(
            "GitHub /app/installations/%s failed: %s %s",
            installation_id, resp.status_code, resp.text[:200],
        )
        return None
    return resp.json() or None


def headers_for_installation(installation_id: int) -> dict[str, str]:
    """Convenience: ready-to-use Authorization/Accept headers for the GitHub REST API."""
    return {
        "Authorization": f"Bearer {get_installation_token(installation_id)}",
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
    }


def clear_installation_token_cache(installation_id: int | None = None) -> None:
    """Drop one or all cached installation tokens. Used on uninstall events."""
    if installation_id is None:
        _install_token_cache.clear()
    else:
        _install_token_cache.pop(installation_id, None)


# ─────────────────────── webhook signature verification ───────────────────────


def verify_webhook_signature(raw_body: bytes, sig_header: str | None) -> bool:
    """Verify the `X-Hub-Signature-256` header against GITHUB_WEBHOOK_SECRET.

    GitHub sends `sha256=<hex>`; we hmac-sha256 the raw body with the
    configured secret and compare in constant time. Returns False if the
    secret isn't configured, the header is missing, or the digests don't
    match.
    """
    import hashlib
    import hmac
    secret = (settings.github_webhook_secret or "").strip()
    if not secret or not sig_header:
        return False
    if not sig_header.startswith("sha256="):
        return False
    expected = sig_header.split("=", 1)[1]
    digest = hmac.new(secret.encode("utf-8"), raw_body, hashlib.sha256).hexdigest()
    return hmac.compare_digest(expected, digest)


# ─────────────────────── data API helpers (Engineer Agent input) ───────────────────────

GITHUB_API_BASE = "https://api.github.com"


def fetch_user_repos(access_token: str, per_page: int = 50) -> list[dict[str, Any]]:
    """List repos the user can access via their OAuth token (first page).

    Returns the trimmed list — full_name, name, private, html_url, default_branch,
    description, updated_at, stargazers_count. The full Engineer Agent will
    later pull file trees via installation tokens; this is the lightweight
    inventory call the UI uses.
    """
    resp = requests.get(
        f"{GITHUB_API_BASE}/user/repos",
        headers={
            "Authorization": f"Bearer {access_token}",
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28",
        },
        params={"per_page": per_page, "sort": "updated", "affiliation": "owner,collaborator,organization_member"},
        timeout=20,
    )
    if not resp.ok:
        logger.warning(
            "GitHub /user/repos failed: %s %s", resp.status_code, resp.text[:200]
        )
        raise HTTPException(resp.status_code, "GitHub repos fetch failed")
    raw = resp.json()
    return [
        {
            "full_name": r.get("full_name"),
            "name": r.get("name"),
            "private": bool(r.get("private")),
            "html_url": r.get("html_url"),
            "default_branch": r.get("default_branch"),
            "description": r.get("description"),
            "updated_at": r.get("updated_at"),
            "stargazers_count": r.get("stargazers_count", 0),
        }
        for r in (raw or [])
    ]


def fetch_installation_repos(
    installation_id: int, per_page: int = 100
) -> list[dict[str, Any]]:
    """Repos this installation can read, using the App INSTALLATION token.

    GitHub endpoint: GET /installation/repositories. Unlike /user/repos
    (which is OAuth-token-gated and scope-limited), this returns exactly
    the repos the App was granted access to during install — which is
    what users actually expect to see in the prototype picker.

    Returns the same trimmed shape as fetch_user_repos so callers can
    treat the two interchangeably.
    """
    resp = requests.get(
        f"{GITHUB_API_BASE}/installation/repositories",
        headers=headers_for_installation(installation_id),
        params={"per_page": per_page},
        timeout=20,
    )
    if not resp.ok:
        logger.warning(
            "GitHub /installation/repositories failed for install %s: %s %s",
            installation_id, resp.status_code, resp.text[:200],
        )
        # Honest empty rather than raise — one bad install in an aggregate
        # call shouldn't sink the whole list.
        return []
    raw = (resp.json() or {}).get("repositories") or []
    return [
        {
            "id": r.get("id"),
            "full_name": r.get("full_name"),
            "name": r.get("name"),
            "private": bool(r.get("private")),
            "html_url": r.get("html_url"),
            "default_branch": r.get("default_branch"),
            "description": r.get("description"),
            "updated_at": r.get("updated_at"),
        }
        for r in raw
    ]


def _api_get(access_token: str, path: str, params: dict | None = None,
             *, accept: str = "application/vnd.github+json", timeout: int = 20):
    """GET against the GitHub REST API with a bearer token. Returns the
    requests.Response (caller inspects status — some callers tolerate 404/403)."""
    return requests.get(
        f"{GITHUB_API_BASE}{path}",
        headers={
            "Authorization": f"Bearer {access_token}",
            "Accept": accept,
            "X-GitHub-Api-Version": "2022-11-28",
        },
        params=params or {},
        timeout=timeout,
    )


def fetch_recent_pull_requests(
    access_token: str, repo_full_name: str, per_page: int = 20
) -> list[dict[str, Any]]:
    """Recent PRs (any state) for a repo — title/body/state/author distilled.

    Used by the activity puller as a code-insight signal. Returns trimmed
    dicts; raises on a hard failure so the puller can decide to skip the repo."""
    resp = _api_get(
        access_token, f"/repos/{repo_full_name}/pulls",
        params={"state": "all", "sort": "updated", "direction": "desc",
                "per_page": per_page},
    )
    if not resp.ok:
        logger.warning("GitHub pulls failed for %s: %s %s",
                       repo_full_name, resp.status_code, resp.text[:200])
        resp.raise_for_status()
    return [
        {
            "number": pr.get("number"),
            "title": pr.get("title") or "",
            "body": pr.get("body") or "",
            "state": "merged" if pr.get("merged_at") else (pr.get("state") or "open"),
            "author": (pr.get("user") or {}).get("login"),
            "updated_at": pr.get("updated_at"),
        }
        for pr in (resp.json() or [])
    ]


def fetch_recent_commits(
    access_token: str, repo_full_name: str, per_page: int = 30
) -> list[dict[str, Any]]:
    """Recent commits for a repo — message + author + sha distilled."""
    resp = _api_get(
        access_token, f"/repos/{repo_full_name}/commits",
        params={"per_page": per_page},
    )
    if not resp.ok:
        logger.warning("GitHub commits failed for %s: %s %s",
                       repo_full_name, resp.status_code, resp.text[:200])
        resp.raise_for_status()
    out: list[dict[str, Any]] = []
    for c in (resp.json() or []):
        commit = c.get("commit") or {}
        out.append({
            "sha": (c.get("sha") or "")[:10],
            "message": (commit.get("message") or "").strip(),
            "author": (commit.get("author") or {}).get("name"),
            "date": (commit.get("author") or {}).get("date"),
        })
    return out


# ─────────────────────── deep-read helpers (on-demand) ───────────────────────


def fetch_repo_languages(access_token: str, repo_full_name: str) -> dict[str, int]:
    """Language → byte-count map for a repo. Empty dict on any failure."""
    resp = _api_get(access_token, f"/repos/{repo_full_name}/languages")
    if not resp.ok:
        return {}
    data = resp.json()
    return data if isinstance(data, dict) else {}


def fetch_repo_readme(access_token: str, repo_full_name: str,
                      max_chars: int = 8000) -> str:
    """Decoded README text (truncated). Empty string if none / on failure."""
    resp = _api_get(access_token, f"/repos/{repo_full_name}/readme")
    if not resp.ok:
        return ""
    payload = resp.json()
    content = payload.get("content") or ""
    if (payload.get("encoding") or "").lower() == "base64":
        import base64
        try:
            text = base64.b64decode(content).decode("utf-8", errors="replace")
        except (ValueError, TypeError):
            return ""
    else:
        text = content
    return text[:max_chars]


def fetch_repo_tree(access_token: str, repo_full_name: str, branch: str,
                    max_entries: int = 200) -> list[str]:
    """Top-of-repo file paths (recursive tree, capped). Empty list on failure.

    Returns only the path strings — a structural map, never file contents."""
    resp = _api_get(
        access_token, f"/repos/{repo_full_name}/git/trees/{branch}",
        params={"recursive": "1"},
    )
    if not resp.ok:
        return []
    tree = (resp.json() or {}).get("tree") or []
    paths = [t.get("path") for t in tree if t.get("type") == "blob" and t.get("path")]
    return paths[:max_entries]


def fetch_repo_meta(access_token: str, repo_full_name: str) -> dict[str, Any]:
    """Repo metadata (description, default_branch, topics…). {} on failure."""
    resp = _api_get(access_token, f"/repos/{repo_full_name}")
    if not resp.ok:
        logger.warning("GitHub repo meta failed for %s: %s",
                       repo_full_name, resp.status_code)
        return {}
    r = resp.json()
    return {
        "full_name": r.get("full_name"),
        "description": r.get("description"),
        "default_branch": r.get("default_branch") or "main",
        "topics": r.get("topics") or [],
        "language": r.get("language"),
        "private": bool(r.get("private")),
    }
