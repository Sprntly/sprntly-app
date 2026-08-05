"""OAuth and status for third-party connectors.

  GET    /v1/connectors                         -> list (no secrets)

  GET    /v1/connectors/google-drive/authorize  -> redirect to Google
  GET    /v1/connectors/google-drive/callback   -> OAuth callback
  POST   /v1/connectors/google-drive/files      -> save Picker-picked files + sync
  POST   /v1/connectors/google-drive/sync       -> pull picked files into corpus
  DELETE /v1/connectors/google-drive            -> disconnect

  GET    /v1/connectors/figma/authorize         -> redirect to Figma
  GET    /v1/connectors/figma/callback          -> OAuth callback
  DELETE /v1/connectors/figma                   -> disconnect
  GET    /v1/connectors/figma/files/{key}       -> file structure (Design Agent input)
  GET    /v1/connectors/figma/files/{key}/styles -> design tokens (Design Agent input)

  GET    /v1/connectors/slack/callback           -> OAuth callback
  DELETE /v1/connectors/slack                   -> disconnect
  POST   /v1/connectors/slack/dm                 -> DM the user (Sprntly -> user)
  GET    /v1/connectors/slack/history            -> read channel/DM messages
  GET    /v1/connectors/slack/search             -> search the user's own content
  POST   /v1/connectors/slack/sync-to-corpus    -> sync messages into corpus
  POST   /v1/connectors/slack/events             -> Events API sink (signature-auth)
  POST   /v1/connectors/slack/commands          -> slash-command sink (signature-auth)

  GET    /v1/connectors/github/authorize        -> redirect to GitHub
  GET    /v1/connectors/github/callback         -> OAuth callback
  DELETE /v1/connectors/github                  -> disconnect
  POST   /v1/connectors/github/webhook          -> GitHub App event sink
  GET    /v1/connectors/github/installations    -> list installs we know about
  GET    /v1/connectors/github/pull-requests    -> list tracked open PRs
  GET    /v1/connectors/github/repos            -> user's accessible repos (Engineer Agent input)
"""
from __future__ import annotations

import asyncio
import itertools
import json
import logging
import re
import sys
import time
from typing import Annotated
from urllib.parse import parse_qs, urlencode

import requests

from fastapi import (
    APIRouter,
    Depends,
    File,
    Form,
    Header,
    HTTPException,
    Query,
    Request,
    UploadFile,
)
from fastapi.responses import RedirectResponse
from google.auth.transport.requests import Request as GoogleAuthRequest
from pydantic import BaseModel, Field

from app import db
from app import datasets as datasets_service
from app import html_report
from app.auth import (  # noqa: F401 — require_workspace re-exported for tests' dependency_overrides
    CompanyContext,
    WorkspaceContext,
    require_company,
    require_workspace,
)
from app.config import settings
from app.connectors import (
    asana_oauth,
    clickup_oauth,
    confluence_oauth,
    figma_oauth,
    fireflies_apikey,
    github_app,
    google_meet,
    google_oauth,
    hubspot_oauth,
    jira_oauth,
    slack_oauth,
    sprinklr_oauth,
    superset_auth,
    uploads,
    zoom_oauth,
)
from app.connectors.google_drive_sync import (
    SyncConfigError,
    _refresh_credentials,
    normalize_picked_files,
    sync_google_drive,
)
from app.connectors.tokens import (
    TokenEncryptionError,
    decrypt_token_json,
    encrypt_token_json,
)
from app.deps.ownership import require_owned_dataset
from app.kg_ingest.auto_sync import (
    kickoff_call_index_sync,
    kickoff_corpus_seed,
    kickoff_slack_corpus_sync,
    kickoff_sync,
)
from app.prompt_history import clamp_turn_text
from app.skill_router import is_competitive_report_request

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/v1/connectors", tags=["connectors"])


def _design_agent_enabled() -> bool:
    """Request-time read of the Design Agent flag (mirrors routes/design_agent's
    ``_feature_enabled``; default-off). Read here — not
    imported from the route module — to avoid a connectors→design_agent import
    cycle. Gates the codebase-map pre-warm so it no-ops cleanly when the feature
    is dark."""
    import os

    val = (os.environ.get("DESIGN_AGENT_ENABLED") or "").strip().lower()
    return val in {"1", "true", "yes"}


def _prewarm_codebase_map_on_connect(installation_id: int) -> None:
    """Best-effort: warm the codebase map for a just-bound installation so the
    first /locate is hot. No-ops when the Design Agent is disabled. NEVER blocks or
    raises into the connect flow — coalescing + a single build permit live inside
    the pre-warm module, so this stays load-safe even on a many-repo install."""
    if not _design_agent_enabled():
        return
    try:
        from app.design_agent.codebase_map.prewarm import prewarm_installation

        prewarm_installation(int(installation_id))
    except Exception:  # noqa: BLE001 — pre-warm must never break connect.
        logger.warning(
            "codebase-map connect pre-warm skipped for installation %s",
            installation_id, exc_info=True,
        )


def _prewarm_codebase_map_on_push(installation_id: int, repo: str, ref: str | None) -> None:
    """Best-effort: a push is a new commit_sha, hence a natural L1/L2 cache miss;
    warm the new sha in the bounded background lane so the NEXT /locate is hot
    instead of paying the cold rebuild inline. No-ops when the Design Agent is
    disabled. NEVER blocks or raises into the webhook flow."""
    if not _design_agent_enabled():
        return
    try:
        from app.design_agent.codebase_map.prewarm import prewarm_map

        # ref=None lets build_map resolve the default-branch SHA itself; we pass it
        # so a non-default-branch push doesn't warm the wrong ref. build_map keys on
        # the resolved commit_sha regardless.
        prewarm_map(int(installation_id), repo, ref)
    except Exception:  # noqa: BLE001 — pre-warm must never break the webhook.
        logger.warning(
            "codebase-map push pre-warm skipped for installation %s repo %s",
            installation_id, repo, exc_info=True,
        )


def _public_connection(row: dict) -> dict:
    config = {}
    if row.get("config_json"):
        try:
            config = json.loads(row["config_json"])
        except (TypeError, ValueError):
            config = {}
    # account_label is the generic identifier ("alice@co.com" for Figma,
    # "@octocat" for GitHub, the user's email for Google). google_email
    # is preserved for the existing Drive frontend; new providers should
    # read account_label.
    from app.connectors.catalog import types_for

    return {
        "id": row["id"],
        "provider": row["provider"],
        "status": row["status"],
        # What this provider IS (one type for now, list-shaped for the future,
        # e.g. ["task-management"]) — the
        # web derives feature availability (ticket sync, …) from these
        # instead of hardcoding provider names. See app/connectors/catalog.py.
        "types": types_for(row["provider"]),
        "google_email": row.get("google_email"),
        "account_label": row.get("account_label") or row.get("google_email"),
        "scopes": row.get("scopes") or "",
        "config": config,
        "last_sync_at": row.get("last_sync_at"),
        "last_sync_error": row.get("last_sync_error"),
        # Token-health, set by the scheduled connector health monitor (and the
        # on-open test). health: 'connected' | 'disconnected' | null (unchecked).
        "health": row.get("health"),
        "last_health_error": row.get("last_health_error"),
        "last_health_check_at": row.get("last_health_check_at"),
        "created_at": row.get("created_at"),
        "updated_at": row.get("updated_at"),
    }


# ── RBAC helpers ──────────────────────────────────────────────────────
# Personal connectors (Slack) are open to any member. Org-wide connectors
# (GitHub, Figma, Google Drive, ClickUp, HubSpot, Fireflies) are admin-only
# for mutations (connect, disconnect, sync, config) but readable by all.
_PERSONAL_PROVIDERS = {slack_oauth.SLACK_PROVIDER}


def _require_admin_for_org_connector(
    company: CompanyContext, provider: str
) -> None:
    """Raise 403 if a non-admin tries to mutate an org-wide connector."""
    if provider in _PERSONAL_PROVIDERS:
        return  # any member can manage their own personal connector
    if company.role not in ("owner", "admin"):
        raise HTTPException(
            403,
            "Only admins can manage org-wide connectors. "
            "Ask your workspace admin to connect this integration.",
        )


def _gate_dataset(dataset: str, company_id: str) -> str:
    """Shape-validate + tenant-gate a CLIENT-SUPPLIED dataset slug, returning
    the normalized slug to use downstream.

    Every connector→corpus sync route takes its target dataset from the request
    body. `require_company` proves who the caller is and
    `_require_admin_for_org_connector` proves they're an admin OF THEIR OWN
    company — neither has ever looked at that slug. Since the backend holds the
    service-role key (RLS bypassed), nothing below the route re-checks it
    either: `corpus.load_corpus` is a slug→directory reader by design, and
    `google_drive_sync` only asserts `dataset_exists` (existence, not
    ownership — which doubles as a slug-enumeration oracle). So the guard has to
    live here, and it has to run before the slug reaches any of three sinks:
    the `settings.data_path / slug` write (fixed filenames, so a hit OVERWRITES
    the victim's file), `_seed_corpus_after_sync` (which would glob another
    tenant's whole corpus into THIS company's kg_signal rows), and
    `upsert_input_source` (which flips rows on another tenant's dataset).

    Two checks, in this order:

      * Shape (422). Reusing `datasets.validate_slug` rather than a second
        regex — a slug is `[a-z0-9][a-z0-9_-]{1,62}`, which also covers the
        multi-workspace `{company}--{workspace}` form. This is what stops
        `../../escaped` being joined onto DATA_DIR; the ownership check alone
        would reject it, but shape-first means a traversal string never reaches
        a path join at all. Format before tenancy matches routes/datasets.py.
      * Ownership (404, never 403 — `require_owned_dataset`), so a foreign
        tenant can't tell "exists but not yours" from "doesn't exist". Company
        slugs are low-entropy (`acme`), so existence disclosure alone would be
        enough to enumerate tenants.

    Company-level, not workspace-level: these routes run on `require_company`
    and the connections they sync are themselves company-scoped, so
    `workspace_id` is deliberately left off (see the commit body).
    """
    try:
        slug = datasets_service.validate_slug(dataset)
    except datasets_service.InvalidSlug as e:
        raise HTTPException(422, str(e)) from e
    return require_owned_dataset(slug, company_id)


def _visible_connection_rows(company: CompanyContext) -> list[dict]:
    """Connection rows the CURRENT user may see: every company-scoped
    provider (shared) plus exactly ONE Slack row.

    Slack's two roles have two scopes. Delivery config is per-user, so a
    member sees their OWN Slack row untouched. But the voice-of-customer
    side is company-level: a member who never installed the bot still sees
    the COMPANY's sync connection (see slack_company.py) so the Voice shelf
    reads Connected and the shared pull selection is visible — SANITIZED,
    with the owner's personal delivery target stripped, so one member never
    reads another's delivery config. Legacy NULL-user Slack rows stay
    hidden."""
    rows = db.list_connections(company.company_id)
    out: list[dict] = []
    own_slack = False
    for r in rows:
        if r.get("provider") == slack_oauth.SLACK_PROVIDER:
            if r.get("user_id") != company.user_id:
                continue
            own_slack = True
        out.append(r)
    if not own_slack:
        shared = _company_slack_row_sanitized(company.company_id)
        if shared:
            out.append(shared)
    return out


def _company_slack_row_sanitized(company_id: str) -> dict | None:
    """The company's Slack sync row, safe to show to a member who doesn't
    own it: personal delivery config removed, company-level pull config
    kept, and flagged `company_connection` so the web knows this is the
    shared workspace connection rather than the member's own install."""
    from app.connectors.slack_company import (
        resolve_company_slack_row,
        row_config,
    )
    from app.connectors.slack_sync import (
        CONFIG_SYNC_CHANNEL_IDS,
        CONFIG_SYNC_CHANNEL_NAMES,
    )

    row = resolve_company_slack_row(company_id)
    if not row:
        return None
    cfg = row_config(row)
    sanitized = {
        k: v
        for k, v in cfg.items()
        if k in (CONFIG_SYNC_CHANNEL_IDS, CONFIG_SYNC_CHANNEL_NAMES, "team")
    }
    sanitized["company_connection"] = True
    return {**row, "config_json": json.dumps(sanitized)}


@router.get("")
def list_connections(
    company: CompanyContext = Depends(require_company),
):
    rows = _visible_connection_rows(company)
    return {"connections": [_public_connection(r) for r in rows]}


@router.get("/status")
def connector_status(
    company: CompanyContext = Depends(require_company),
):
    """Company-scoped sync status for every connected provider.

    Backs the Settings status indicators: per provider, whether it has a
    background ingest puller and its last_sync_at / last_sync_error stamp
    (set by the auto-sync-on-connect kickoff and by manual /v1/ingest runs)."""
    from app.connectors.catalog import types_for
    from app.kg_ingest.runner import PULLERS

    rows = _visible_connection_rows(company)
    out = []
    for r in rows:
        provider = r["provider"]
        out.append({
            "provider": provider,
            "status": r["status"],
            "types": types_for(provider),
            "account_label": r.get("account_label") or r.get("google_email"),
            "ingestable": provider in PULLERS,
            "last_sync_at": r.get("last_sync_at"),
            "last_sync_error": r.get("last_sync_error"),
        })
    return {"statuses": out}


# ─────────────────────── Start-OAuth (fetch-friendly) ───────────────────────
#
# POST /v1/connectors/{provider}/start-oauth — returns the OAuth
# authorize URL as JSON so the frontend can call it with a Bearer
# token (fetch) and then navigate the browser to the returned URL.
#
# The legacy GET .../authorize routes (300+ redirect) only work when
# the request carries a session cookie — browser URL-bar navigation
# can't set an Authorization header, so the Connect button needs this
# variant for Supabase-only sessions. Both routes remain available.


def _is_safe_return_to(value: str | None) -> bool:
    """True iff value is a safe relative path to redirect to after OAuth.

    Defends against open-redirect by requiring a relative path with no
    scheme and no host. Specifically rejects:
      - protocol-relative URLs (`//evil.com/...`)
      - absolute URLs (`https://evil.com`, `javascript:alert(1)`, etc.)
      - backslash tricks (browsers normalize `\\` to `/`)
      - anything `urlparse` thinks has a scheme or netloc
      - excessively long values (path-bomb DoS guard)
    None means "no return_to, use the default" — caller treats as safe.
    """
    if value is None:
        return True
    if not isinstance(value, str) or len(value) > 1024:
        return False
    if not value.startswith("/") or value.startswith("//"):
        return False
    if "\\" in value:
        return False
    from urllib.parse import urlparse
    parsed = urlparse(value)
    if parsed.scheme or parsed.netloc:
        return False
    return True


def _build_post_oauth_redirect(payload: dict, provider: str) -> RedirectResponse:
    """Construct the post-callback redirect URL pointing at the lightweight
    `/connectors/return` page (NOT a full re-load of the app).

    That page broadcasts the new connection to the original Sprntly tab and
    then closes itself, so the user lands back where they started with the
    connector already showing connected. We pass `connected=<provider>` plus
    the original (validated, relative) `return_to` so the return page can
    fall back to navigating there if the tab can't self-close.

    `return_to` is only forwarded when it passes `_is_safe_return_to`
    (open-redirect guard); unsafe/empty values are dropped and the return
    page uses its own default.
    """
    return_to = payload.get("return_to")
    frontend = settings.frontend_url.rstrip("/")
    params = {"connected": provider}
    if return_to and _is_safe_return_to(return_to):
        params["return_to"] = return_to
    target = f"{frontend}/connectors/return?{urlencode(params)}"
    return RedirectResponse(target)


def _build_post_oauth_error_redirect(
    payload: dict, provider: str, code: str
) -> RedirectResponse:
    """Same return-page redirect as above, but carrying a failure the user has
    to act on rather than a new connection.

    `code` is a STABLE SPRNTLY CODE (e.g. "zoom_app_not_approved"), never the
    provider's own error string. Two reasons: a provider's wording changes
    without notice and would leak straight onto a screen, and the copy for
    "ask your Zoom admin to approve Sprntly" is the web layer's to write — the
    backend's job is to say precisely which failure happened.
    """
    return_to = payload.get("return_to")
    frontend = settings.frontend_url.rstrip("/")
    params = {"provider": provider, "error": code}
    if return_to and _is_safe_return_to(return_to):
        params["return_to"] = return_to
    target = f"{frontend}/connectors/return?{urlencode(params)}"
    return RedirectResponse(target)


class StartOauthIn(BaseModel):
    dataset: str | None = None
    # Optional relative path the callback redirects to instead of the
    # default /settings?section=connectors. Validated as a safe path
    # before being signed into state (open-redirect guard).
    return_to: str | None = None


@router.post("/{provider}/start-oauth")
def start_oauth(
    provider: str,
    body: StartOauthIn | None = None,
    company: CompanyContext = Depends(require_company),
):
    _require_admin_for_org_connector(company, provider)
    payload = body or StartOauthIn()
    if not _is_safe_return_to(payload.return_to):
        raise HTTPException(422, "return_to must be a safe relative path")
    return_to = payload.return_to

    if provider == google_oauth.GOOGLE_DRIVE_PROVIDER:
        state = google_oauth.sign_oauth_state(
            company_id=company.company_id,
            dataset=payload.dataset,
            return_to=return_to,
        )
        flow = google_oauth.build_flow()
        url, _ = flow.authorization_url(
            access_type="offline",
            include_granted_scopes="true",
            prompt="consent",
            state=state,
        )
        return {"authorize_url": url}

    if provider == figma_oauth.FIGMA_PROVIDER:
        if not figma_oauth.figma_configured():
            raise HTTPException(500, "Figma OAuth is not configured on the server")
        url = figma_oauth.authorize_url(
            state=figma_oauth.sign_oauth_state(
                company_id=company.company_id, return_to=return_to,
            )
        )
        return {"authorize_url": url}

    if provider == github_app.GITHUB_PROVIDER:
        if not github_app.github_oauth_configured():
            raise HTTPException(500, "GitHub OAuth is not configured on the server")
        url = github_app.authorize_url(
            state=github_app.sign_oauth_state(
                company_id=company.company_id, return_to=return_to,
            )
        )
        return {"authorize_url": url}

    if provider == clickup_oauth.CLICKUP_PROVIDER:
        if not clickup_oauth.clickup_configured():
            raise HTTPException(500, "ClickUp OAuth is not configured on the server")
        url = clickup_oauth.authorize_url(
            state=clickup_oauth.sign_oauth_state(
                company_id=company.company_id, return_to=return_to,
            )
        )
        return {"authorize_url": url}

    if provider == hubspot_oauth.HUBSPOT_PROVIDER:
        if not hubspot_oauth.hubspot_configured():
            raise HTTPException(500, "HubSpot OAuth is not configured on the server")
        url = hubspot_oauth.authorize_url(
            state=hubspot_oauth.sign_oauth_state(
                company_id=company.company_id, return_to=return_to,
            )
        )
        return {"authorize_url": url}

    if provider == jira_oauth.JIRA_PROVIDER:
        if not jira_oauth.jira_configured():
            raise HTTPException(500, "Jira OAuth is not configured on the server")
        url = jira_oauth.authorize_url(
            state=jira_oauth.sign_oauth_state(
                company_id=company.company_id, return_to=return_to,
            )
        )
        return {"authorize_url": url}

    if provider == confluence_oauth.CONFLUENCE_PROVIDER:
        if not confluence_oauth.confluence_configured():
            raise HTTPException(500, "Confluence OAuth is not configured on the server")
        url = confluence_oauth.authorize_url(
            state=confluence_oauth.sign_oauth_state(
                company_id=company.company_id, return_to=return_to,
            )
        )
        return {"authorize_url": url}

    if provider == zoom_oauth.ZOOM_PROVIDER:
        if not zoom_oauth.zoom_configured():
            raise HTTPException(500, "Zoom OAuth is not configured on the server")
        url = zoom_oauth.authorize_url(
            state=zoom_oauth.sign_oauth_state(
                company_id=company.company_id, return_to=return_to,
            )
        )
        return {"authorize_url": url}

    if provider == google_meet.GOOGLE_MEET_PROVIDER:
        if not google_meet.google_meet_configured():
            raise HTTPException(
                500, "Google Meet OAuth is not configured on the server"
            )
        url = google_meet.authorize_url(
            state=google_meet.sign_oauth_state(
                company_id=company.company_id, return_to=return_to,
            )
        )
        return {"authorize_url": url}

    if provider == sprinklr_oauth.SPRINKLR_PROVIDER:
        if not sprinklr_oauth.sprinklr_configured():
            raise HTTPException(500, "Sprinklr OAuth is not configured on the server")
        url = sprinklr_oauth.authorize_url(
            state=sprinklr_oauth.sign_oauth_state(
                company_id=company.company_id, return_to=return_to,
            )
        )
        return {"authorize_url": url}

    if provider == asana_oauth.ASANA_PROVIDER:
        if not asana_oauth.asana_configured():
            raise HTTPException(500, "Asana OAuth is not configured on the server")
        url = asana_oauth.authorize_url(
            state=asana_oauth.sign_oauth_state(
                company_id=company.company_id, return_to=return_to,
            )
        )
        return {"authorize_url": url}

    if provider == slack_oauth.SLACK_PROVIDER:
        if not slack_oauth.slack_configured():
            raise HTTPException(500, "Slack OAuth is not configured on the server")
        # Slack is per-user: bind the OAuth round-trip to the connecting
        # user so the callback stores the bot under THEIR connection, not a
        # company-shared one.
        url = slack_oauth.authorize_url(
            state=slack_oauth.sign_oauth_state(
                company_id=company.company_id,
                user_id=company.user_id,
                return_to=return_to,
            )
        )
        return {"authorize_url": url}

    raise HTTPException(
        404,
        f"OAuth start is not available for provider {provider!r}",
    )


# ─────────────────────── Test connection ───────────────────────
#
# POST /v1/connectors/{provider}/test — re-runs the provider's identity
# lookup using the stored (decrypted) token. Backs the "Test connection"
# button in the Configure drawer (commit K).


@router.post("/{provider}/test")
def test_connection(
    provider: str,
    company: CompanyContext = Depends(require_company),
):
    """Re-validate a stored connection by re-running the provider's
    identity lookup with the decrypted token.

    Returns:
        200 {ok: true, account_label, tested_at}  — token still valid
        400 {detail}                              — provider rejected token
        404                                       — provider not connected
                                                    or unknown
    """
    from datetime import datetime, timezone

    from app.connector_probe import ProbeError, probe_connection

    # Slack: validate THIS user's own connection when they have one; a
    # member without their own install probes the COMPANY sync connection
    # instead (read-only health check — the drawer's status pill must read
    # Connected for the shared voice-of-customer connection they can see).
    if provider == slack_oauth.SLACK_PROVIDER:
        row = db.get_slack_connection(company.company_id, company.user_id)
        if not row:
            from app.connectors.slack_company import resolve_company_slack_row

            row = resolve_company_slack_row(company.company_id)
    else:
        row = db.get_connection(company.company_id, provider)
    if not row:
        raise HTTPException(404, f"{provider!r} is not connected")

    # The per-provider validation lives in app.connector_probe so this on-open
    # check and the scheduled health monitor share ONE implementation. Re-raise
    # its failures as the HTTP status codes this route has always returned.
    try:
        healthy, detail = probe_connection(provider, row)
    except ProbeError as e:
        if e.reason == "unreadable":
            raise HTTPException(500, "Stored token unreadable") from e
        if e.reason == "unsupported":
            raise HTTPException(
                404, f"Test connection not supported for provider {provider!r}"
            ) from e
        # "rejected" — e.g. Drive token refresh failed.
        raise HTTPException(400, str(e)) from e

    if not healthy:
        raise HTTPException(
            400,
            f"{provider} rejected the stored credential — disconnect and reconnect.",
        )

    tested_at = datetime.now(timezone.utc).isoformat()
    return {"ok": True, "account_label": str(detail), "tested_at": tested_at}


@router.get("/google-drive/authorize")
def google_drive_authorize(
    dataset: str | None = None,
    company: CompanyContext = Depends(require_company),
):
    state = google_oauth.sign_oauth_state(company_id=company.company_id, dataset=dataset)
    flow = google_oauth.build_flow()
    url, _ = flow.authorization_url(
        access_type="offline",
        include_granted_scopes="true",
        prompt="consent",
        state=state,
    )
    return RedirectResponse(url)


@router.get("/google-drive/callback")
def google_drive_callback(code: str, state: str):
    # Unauthenticated route — the user is bouncing back from Google with
    # no Bearer token, so the signed state is the trust boundary. Workspace
    # was verified at /authorize time and burned into the state JWT.
    payload = google_oauth.verify_oauth_state(state)
    company_id = payload["company_id"]

    flow = google_oauth.build_flow()
    try:
        flow.fetch_token(code=code)
    except Exception as e:
        logger.exception("Google OAuth token exchange failed")
        raise HTTPException(400, f"Google authorization failed: {e}") from e

    creds = flow.credentials
    if not creds or not creds.token:
        raise HTTPException(400, "Google did not return credentials")

    try:
        token_encrypted = encrypt_token_json(creds.to_json())
    except TokenEncryptionError as e:
        raise HTTPException(500, str(e)) from e

    email = google_oauth.fetch_google_account_email(creds)
    config = {}
    if payload.get("dataset"):
        config["dataset"] = payload["dataset"]

    db.upsert_connection(
        company_id=company_id,
        provider=google_oauth.GOOGLE_DRIVE_PROVIDER,
        token_encrypted=token_encrypted,
        scopes=" ".join(google_oauth.DRIVE_SCOPES),
        google_email=email,
        config_json=json.dumps(config),
    )

    return _build_post_oauth_redirect(payload, google_oauth.GOOGLE_DRIVE_PROVIDER)


def _drive_config_dataset(company_id: str) -> str | None:
    """The dataset slug stored on this company's Drive connection config, if any.

    Written at OAuth time from `?dataset=` on /google-drive/authorize (and
    start-oauth), which is not ownership-checked either. `sync_google_drive`,
    `_auto_enable_drive_input_source` and `_seed_corpus_after_sync` all fall
    back to it when the request body omits a dataset, so it is a second,
    equally client-controlled route into the same sinks — which is why the Drive
    routes gate the EFFECTIVE slug (body value, else this) rather than just the
    body value."""
    row = db.get_connection(company_id, google_oauth.GOOGLE_DRIVE_PROVIDER)
    if not row or not row.get("config_json"):
        return None
    try:
        cfg = json.loads(row["config_json"])
    except (TypeError, ValueError):
        return None
    slug = cfg.get("dataset")
    return str(slug) if slug else None


def _gate_effective_drive_dataset(
    dataset: str | None, company_id: str
) -> str | None:
    """Tenant-gate the slug a Drive sync will ACTUALLY act on, and return the
    value to pass downstream (None when the request named none).

    `dataset` is optional on both Drive routes, and when it's absent
    `sync_google_drive` / `_auto_enable_drive_input_source` fall back to the
    slug on the connection config. Gating only the body value would therefore
    leave that stored slug — written from an unchecked `?dataset=` at OAuth
    time — as a live bypass into the same sinks, so whichever one will be used
    is the one that gets checked.

    Returns the NORMALIZED body slug when one was supplied, so the string that
    was verified is the string that reaches the `data_path / slug` join. When
    the body named nothing it returns None rather than the stored slug: the
    existing fallbacks stay exactly as they were (notably
    `_seed_corpus_after_sync`, which falls back to the company's own slug and
    not to the config value), now that the value they resolve to has been
    verified. A request naming nothing anywhere is unchanged — every fallback
    already lands on the caller's own company.
    """
    if dataset:
        return _gate_dataset(dataset, company_id)
    stored = _drive_config_dataset(company_id)
    if stored:
        _gate_dataset(stored, company_id)
    return None


def _auto_enable_drive_input_source(company_id: str, dataset: str | None) -> None:
    """Flip the dataset's google_drive input source on after a sync. Falls back
    to the dataset stored in the connection config when not passed explicitly."""
    dataset_slug = dataset or _drive_config_dataset(company_id)
    if dataset_slug:
        try:
            db.upsert_input_source(
                dataset_slug, "google_drive", enabled=True,
                config={"last_sync_at": db.utc_now()},
            )
        except Exception:
            logger.warning(
                "Failed to auto-enable google_drive input source", exc_info=True
            )


def _seed_corpus_after_sync(company_id: str, dataset: str | None) -> None:
    """Kick a background KG corpus seed after a connector→corpus sync.

    Drive/Slack/Figma write docs to the corpus but have no kg_ingest puller, so
    without this their content never reaches the KG until the next brief's seed.
    This eagerly extracts it (incremental + content-hash deduped). Resolves the
    corpus slug from the sync's dataset, falling back to the company's slug.
    Best-effort: a missing slug is logged and skipped, never raised."""
    from app.db.companies import slug_for_company_id

    slug = dataset or slug_for_company_id(company_id)
    if not slug:
        logger.warning("corpus-seed: no dataset slug for company=%s — skipping",
                       company_id)
        return
    kickoff_corpus_seed(company_id, slug)


class GoogleDrivePickedFile(BaseModel):
    id: str
    name: str | None = None


class GoogleDriveFilesIn(BaseModel):
    # The Google Picker frontend POSTs the files the user selected. Each entry
    # carries the Drive file id and (optionally) its name for nicer ingest
    # naming. Replaces the whole stored picked-file list (not a merge).
    files: list[GoogleDrivePickedFile]
    dataset: str | None = None


class GoogleDriveSyncIn(BaseModel):
    dataset: str | None = None


@router.post("/google-drive/files")
def google_drive_save_files(
    body: GoogleDriveFilesIn,
    company: CompanyContext = Depends(require_company),
):
    """Store the files the Google Picker selected (per-company) and sync them.

    The Picker frontend must POST {"files": [{"id","name"}, ...]} — the file
    ids it gets back from picker.getResponse(). Under the drive.file scope this
    app can only read those specific files. We persist them in the connection
    config under config["files"], then run a sync so the picked files land in
    the corpus immediately."""
    # Tenant gate first — `dataset` is optional here, so what gets checked is
    # the EFFECTIVE slug (body value, else the one stored on the connection).
    dataset = _gate_effective_drive_dataset(body.dataset, company.company_id)
    _require_admin_for_org_connector(company, google_oauth.GOOGLE_DRIVE_PROVIDER)
    row = db.get_connection(company.company_id, google_oauth.GOOGLE_DRIVE_PROVIDER)
    if not row:
        raise HTTPException(404, "Google Drive is not connected")

    picked = [f.model_dump() for f in body.files]
    try:
        # Validate the ids up front (422 on a bad id) before kicking sync.
        normalize_picked_files(picked)
        result = sync_google_drive(
            company_id=company.company_id,
            dataset=dataset,
            files=picked,
        )
    except SyncConfigError as e:
        raise HTTPException(400, str(e)) from e

    _auto_enable_drive_input_source(company.company_id, dataset)
    _seed_corpus_after_sync(company.company_id, dataset)
    return result.to_dict()


@router.post("/google-drive/sync")
def google_drive_sync(
    body: GoogleDriveSyncIn | None = None,
    company: CompanyContext = Depends(require_company),
):
    payload = body or GoogleDriveSyncIn()
    # Tenant gate first — see google_drive_save_files: the effective slug is the
    # body value, else the one stored on the connection at OAuth time.
    dataset = _gate_effective_drive_dataset(payload.dataset, company.company_id)
    _require_admin_for_org_connector(company, google_oauth.GOOGLE_DRIVE_PROVIDER)
    try:
        result = sync_google_drive(
            company_id=company.company_id,
            dataset=dataset,
        )
    except SyncConfigError as e:
        raise HTTPException(400, str(e)) from e

    _auto_enable_drive_input_source(company.company_id, dataset)
    _seed_corpus_after_sync(company.company_id, dataset)
    return result.to_dict()


@router.get("/google-drive/picker-token")
def google_drive_picker_token(
    company: CompanyContext = Depends(require_company),
):
    """Mint a short-lived Drive access token for the browser-side Google Picker.

    The Google Picker JS widget needs an OAuth access token to render the
    user's own Drive in their browser. We hold the user's Fernet-encrypted
    refresh token (``drive.file`` scope only), so we refresh it server-side
    here — reusing the same refresh helper the sync uses, never duplicating
    that logic — and hand back ONLY the resulting access token. This is the
    intended least-privilege Picker pattern: the token is ``drive.file``-scoped
    (it can read/write only files the user explicitly picks, never the whole
    Drive), it is returned solely to the authenticated owner of the connection
    over HTTPS, and it expires within the hour. So exposing this narrow token
    to the owner's own browser grants them nothing they couldn't already do
    with their own Google account.

    Also returns ``app_id``: the Google Cloud project number, required by the
    Picker's ``setAppId()`` under the ``drive.file`` scope so a picked file is
    bound to this app (without it Drive answers "File not found" for files
    picked but never otherwise granted). Google OAuth client ids are shaped
    ``<PROJECT_NUMBER>-<random>.apps.googleusercontent.com``, so we derive the
    project number from ``settings.google_client_id`` rather than adding a new
    config value that could drift from the OAuth client if it's ever rotated
    into another project. Empty string if the client id is unset or
    unexpectedly shaped (missing ``-``) — the route still returns 200.

    Returns ``{"access_token", "expires_in", "app_id"}`` (seconds until
    expiry). 404 if Drive isn't connected — matching how the other Drive
    routes signal that.
    """
    _require_admin_for_org_connector(company, google_oauth.GOOGLE_DRIVE_PROVIDER)
    row = db.get_connection(company.company_id, google_oauth.GOOGLE_DRIVE_PROVIDER)
    if not row:
        raise HTTPException(404, "Google Drive is not connected")

    try:
        creds = _refresh_credentials(row)
    except SyncConfigError as e:
        # Refresh helper raises SyncConfigError when the session is expired
        # with no refresh token — surface as 409 "reconnect needed", mirroring
        # the sync's 400-on-config-error handling but distinguishing the
        # "must reconnect" state for the Picker UI.
        raise HTTPException(409, str(e)) from e

    if not creds.token:
        raise HTTPException(409, "Google Drive session is invalid — reconnect.")

    # creds.expiry is a naive UTC datetime (google-auth convention). Compute
    # seconds-until-expiry; fall back to ~3000s (a hair under Google's hour)
    # when expiry is missing so the browser refreshes well before it lapses.
    expires_in = 3000
    expiry = getattr(creds, "expiry", None)
    if expiry is not None:
        from datetime import datetime, timezone

        now = datetime.now(timezone.utc).replace(tzinfo=None)
        remaining = int((expiry - now).total_seconds())
        if remaining > 0:
            expires_in = remaining

    client_id = settings.google_client_id or ""
    app_id = client_id.split("-", 1)[0] if "-" in client_id else ""

    return {"access_token": creds.token, "expires_in": expires_in, "app_id": app_id}


@router.delete("/google-drive")
def google_drive_disconnect(
    company: CompanyContext = Depends(require_company),
):
    _require_admin_for_org_connector(company, google_oauth.GOOGLE_DRIVE_PROVIDER)
    row = db.get_connection(company.company_id, google_oauth.GOOGLE_DRIVE_PROVIDER)
    if not row:
        raise HTTPException(404, "Google Drive is not connected")

    try:
        creds = google_oauth.credentials_from_token_json(
            decrypt_token_json(row["token_json_encrypted"])
        )
        if creds.expired and creds.refresh_token:
            creds.refresh(GoogleAuthRequest())
        google_oauth.try_revoke_credentials(creds)
    except Exception:
        logger.warning("Could not revoke Google token on disconnect", exc_info=True)

    db.delete_connection(company.company_id, google_oauth.GOOGLE_DRIVE_PROVIDER)
    return {"deleted": True, "provider": google_oauth.GOOGLE_DRIVE_PROVIDER}


# ─────────────────────── Figma ───────────────────────


@router.get("/figma/authorize")
def figma_authorize(
    company: CompanyContext = Depends(require_company),
):
    if not figma_oauth.figma_configured():
        raise HTTPException(500, "Figma OAuth is not configured on the server")
    url = figma_oauth.authorize_url(
        state=figma_oauth.sign_oauth_state(company_id=company.company_id)
    )
    return RedirectResponse(url)


@router.get("/figma/callback")
def figma_callback(code: str, state: str):
    payload = figma_oauth.verify_oauth_state(state)
    company_id = payload["company_id"]
    token_json = figma_oauth.exchange_code_for_token(code)
    access_token = token_json.get("access_token")
    if not access_token:
        raise HTTPException(400, "Figma did not return an access_token")

    me = figma_oauth.fetch_me(access_token)
    label = me.get("email") or me.get("handle") or token_json.get("user_id")

    try:
        token_encrypted = encrypt_token_json(figma_oauth.token_payload_to_store(token_json))
    except TokenEncryptionError as e:
        raise HTTPException(500, str(e)) from e

    db.upsert_connection(
        company_id=company_id,
        provider=figma_oauth.FIGMA_PROVIDER,
        token_encrypted=token_encrypted,
        scopes=figma_oauth.DEFAULT_SCOPES,
        account_label=label,
        config_json=json.dumps({"user": me}) if me else "{}",
    )

    return _build_post_oauth_redirect(payload, figma_oauth.FIGMA_PROVIDER)


# Figma is OAuth-only. The legacy Personal Access Token (PAT) connect path was
# removed — Figma's app review requires OAuth as the sole connect mechanism, so
# no PAT endpoint exists for a reviewer to flag.


@router.delete("/figma")
def figma_disconnect(
    company: CompanyContext = Depends(require_company),
):
    _require_admin_for_org_connector(company, figma_oauth.FIGMA_PROVIDER)
    row = db.get_connection(company.company_id, figma_oauth.FIGMA_PROVIDER)
    if not row:
        raise HTTPException(404, "Figma is not connected")
    # Figma has no documented revoke endpoint; just drop our copy of the token.
    db.delete_connection(company.company_id, figma_oauth.FIGMA_PROVIDER)
    return {"deleted": True, "provider": figma_oauth.FIGMA_PROVIDER}


def _figma_access_token(company_id: str) -> str:
    """Return a valid Figma access token for the company, refreshing it
    first if the stored token is expired or near expiry.

    The stored token JSON is Figma's response (access_token, refresh_token,
    expires_in) plus an `obtained_at` epoch. We refresh proactively (2 min
    early) so fetches never silently degrade once the token lapses, persist
    the refreshed token+refresh+expiry back onto the connection, and return
    the fresh access token. Mirrors the HubSpot valid-access-token pattern.

    Raises 404 if not connected; raises a clear 502 if a refresh is required
    but fails (rather than handing back a dead token).
    """
    row = db.get_connection(company_id, figma_oauth.FIGMA_PROVIDER)
    if not row:
        raise HTTPException(404, "Figma is not connected")
    try:
        token_json = json.loads(decrypt_token_json(row["token_json_encrypted"]))
    except (TokenEncryptionError, json.JSONDecodeError) as e:
        raise HTTPException(500, "Figma token unreadable") from e
    access_token = token_json.get("access_token")
    if not access_token:
        raise HTTPException(500, "Figma token has no access_token")

    # Refresh proactively if expired / within 2 min of expiry.
    obtained_at = token_json.get("obtained_at", 0)
    expires_in = token_json.get("expires_in", 0)
    refresh_token = token_json.get("refresh_token")
    if expires_in and time.time() > obtained_at + expires_in - 120:
        if not refresh_token:
            raise HTTPException(
                401, "Figma token expired and no refresh_token — reconnect Figma"
            )
        logger.info("Figma token expired for company, refreshing")
        try:
            new_tokens = figma_oauth.refresh_access_token(refresh_token)
        except HTTPException as e:
            # Don't silently use a dead token — surface a clear error.
            logger.warning("Figma token refresh failed: %s", e.detail)
            raise HTTPException(
                502, "Figma token refresh failed — reconnect Figma"
            ) from e
        # Merge fresh values, preserving refresh_token if Figma omits it,
        # and re-stamp obtained_at so subsequent expiry checks are correct.
        token_json["access_token"] = new_tokens["access_token"]
        token_json["refresh_token"] = new_tokens.get("refresh_token", refresh_token)
        token_json["expires_in"] = new_tokens.get("expires_in", expires_in)
        token_json["obtained_at"] = int(time.time())
        try:
            encrypted = encrypt_token_json(json.dumps(token_json))
            db.update_connection_tokens(
                company_id, figma_oauth.FIGMA_PROVIDER, encrypted
            )
        except Exception:
            logger.warning("Failed to persist refreshed Figma token", exc_info=True)
        access_token = token_json["access_token"]

    return access_token


@router.get("/figma/files/{key}")
def figma_get_file(
    key: str,
    depth: int = 2,
    company: CompanyContext = Depends(require_company),
):
    """Fetch a Figma file's top-level structure. Used by Design Agent to
    extract frames/pages and to ground prototype generation in the team's
    actual canvases."""
    token = _figma_access_token(company.company_id)
    return figma_oauth.fetch_file(token, key, depth=depth)


@router.get("/figma/files/{key}/styles")
def figma_get_file_styles(
    key: str,
    company: CompanyContext = Depends(require_company),
):
    """Fetch published styles for a Figma file. Used by Design Agent to
    extract design tokens (colors, fonts, effects) for Scenario A
    (Figma-connected) prototype generation."""
    token = _figma_access_token(company.company_id)
    return figma_oauth.fetch_file_styles(token, key)


class FigmaSyncCorpusIn(BaseModel):
    file_key: str
    dataset: str


@router.post("/figma/sync-to-corpus")
def figma_sync_to_corpus(
    body: FigmaSyncCorpusIn,
    company: CompanyContext = Depends(require_company),
):
    """Sync Figma file structure and design tokens into the corpus.

    Fetches file tree + published styles and writes a markdown summary
    into DATA_DIR/{dataset}/figma_design_context.md. Company-scoped: uses
    the caller's company's Figma connection only, and writes only into a
    dataset the caller's company owns.
    """
    dataset = _gate_dataset(body.dataset, company.company_id)
    _require_admin_for_org_connector(company, figma_oauth.FIGMA_PROVIDER)
    token = _figma_access_token(company.company_id)

    # Fetch file structure + styles
    file_data = figma_oauth.fetch_file(token, body.file_key, depth=2)
    styles_data = figma_oauth.fetch_file_styles(token, body.file_key)

    # Build markdown
    lines: list[str] = ["# Figma Design Context\n"]
    lines.append(f"**File:** {file_data.get('name', body.file_key)}")
    lines.append(f"**Last Modified:** {file_data.get('lastModified', 'unknown')}\n")

    # Pages and frames
    doc = file_data.get("document", {})
    for page in doc.get("children", []):
        lines.append(f"## Page: {page.get('name', 'Untitled')}")
        for frame in page.get("children", []):
            fname = frame.get("name", "Untitled")
            ftype = frame.get("type", "")
            lines.append(f"- **{fname}** ({ftype})")

    # Design tokens
    styles_meta = styles_data.get("meta", {})
    styles_list = styles_meta.get("styles", [])
    if styles_list:
        lines.append("\n## Design Tokens\n")
        for style in styles_list:
            sname = style.get("name", "")
            stype = style.get("style_type", "")
            desc = style.get("description", "")
            entry = f"- **{sname}** ({stype})"
            if desc:
                entry += f" — {desc}"
            lines.append(entry)

    md_text = "\n".join(lines) + "\n"
    target = settings.data_path / dataset / "figma_design_context.md"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(md_text, encoding="utf-8")

    # Auto-enable figma input source
    try:
        db.upsert_input_source(
            dataset, "figma", enabled=True,
            config={"file_key": body.file_key, "last_sync_at": db.utc_now()},
        )
    except Exception:
        logger.warning("Failed to auto-enable figma input source", exc_info=True)

    _seed_corpus_after_sync(company.company_id, dataset)
    return {"ok": True, "chars": len(md_text), "path": str(target)}


# ─────────────────────── GitHub (App, user-OAuth half) ───────────────────────


@router.get("/github/authorize")
def github_authorize(
    company: CompanyContext = Depends(require_company),
):
    if not github_app.github_oauth_configured():
        raise HTTPException(500, "GitHub OAuth is not configured on the server")
    url = github_app.authorize_url(
        state=github_app.sign_oauth_state(company_id=company.company_id)
    )
    return RedirectResponse(url)


@router.get("/github/callback")
def github_callback(
    code: str | None = None,
    state: str | None = None,
    setup_action: str | None = None,
    installation_id: int | None = None,
):
    # GitHub re-uses this URL for BOTH the post-OAuth redirect AND the
    # post-install redirect. Two trigger shapes:
    #   1. Post-OAuth:     ?code=X&state=Y                  (handled below)
    #   2. Post-install:   ?setup_action=install&installation_id=N[&state=Y]
    #                      OR ?code=X&setup_action=request
    # When the install URL we redirected to includes our `state` JWT
    # (see the install-URL build below), GitHub preserves it through to
    # the Setup URL — so `return_to` (e.g. /onboarding/6) survives the
    # round-trip and we can bounce the user back to their original page
    # instead of always defaulting to /settings.
    if setup_action or state is None or not code:
        base = (settings.frontend_url or "http://localhost:3000").rstrip("/")
        return_to: str | None = None
        if state:
            try:
                payload = github_app.verify_oauth_state(state)
                rt = payload.get("return_to")
                if rt and _is_safe_return_to(rt):
                    return_to = rt
                # Post-install round-trip: GitHub preserved our signed state
                # (carrying company_id) through to the Setup URL. This is the
                # ONE place we know both the installation_id AND the company,
                # so bind the installation to the caller's company here. The
                # webhook (no company context) may have created the row first;
                # we set/overwrite company_id without disturbing other fields.
                if installation_id is not None and payload.get("company_id"):
                    _bind_installation_company(
                        int(installation_id), str(payload["company_id"])
                    )
            except HTTPException:
                # state expired or invalid — fall back to /settings
                return_to = None

        # Route through the lightweight /connectors/return page so this tab
        # closes and the original Sprntly tab refreshes — same as the OAuth
        # branch. The post-install extras (setup_action / installation_id)
        # are meaningful to the app, so fold them onto the `return_to` path
        # the return page navigates to if it can't self-close.
        extra = {}
        if setup_action:
            extra["setup_action"] = setup_action
        if installation_id is not None:
            extra["installation_id"] = str(installation_id)

        effective_return_to = return_to or "/settings?section=connectors"
        if extra:
            sep = "&" if "?" in effective_return_to else "?"
            effective_return_to = f"{effective_return_to}{sep}{urlencode(extra)}"

        params = {"connected": "github"}
        if _is_safe_return_to(effective_return_to):
            params["return_to"] = effective_return_to
        target = f"{base}/connectors/return?{urlencode(params)}"
        return RedirectResponse(target, status_code=307)

    payload = github_app.verify_oauth_state(state)
    company_id = payload["company_id"]
    token_json = github_app.exchange_code_for_token(code)
    access_token = token_json.get("access_token")
    if not access_token:
        raise HTTPException(400, "GitHub did not return an access_token")

    me = github_app.fetch_authenticated_user(access_token)
    login = me.get("login") or ""
    label = f"@{login}" if login else None

    try:
        token_encrypted = encrypt_token_json(github_app.token_payload_to_store(token_json))
    except TokenEncryptionError as e:
        raise HTTPException(500, str(e)) from e

    scopes = token_json.get("scope") or github_app.DEFAULT_SCOPES
    db.upsert_connection(
        company_id=company_id,
        provider=github_app.GITHUB_PROVIDER,
        token_encrypted=token_encrypted,
        scopes=scopes,
        account_label=label,
        config_json=json.dumps({"user": me}) if me else "{}",
    )

    # Populate the KG immediately — fire-and-forget, never blocks the redirect.
    kickoff_sync(company_id, github_app.GITHUB_PROVIDER)

    # Two-step GitHub auth: OAuth tells us who the user is, but they ALSO
    # need to install the Sprntly App on at least one repo so we have an
    # installation_id (without that, the agent has no repo access — the
    # /lab/code-chat installation picker stays empty).
    #
    # If the user has no matching installation yet, redirect to GitHub's
    # App install page instead of bouncing them back to /settings. The
    # webhook fires on completion and creates the github_installations
    # row; the user lands back at the App's Setup URL (configured in
    # the App settings on GitHub) — which should point at this same
    # /github/callback so the original `state` (carrying return_to) is
    # threaded all the way through to the post-install branch above.
    if login and not _has_github_install_for(login, company_id) and settings.github_app_slug:
        # Include the original state JWT on the install URL so GitHub
        # preserves it through to the Setup URL redirect. That lets us
        # bounce the user back to wherever they started (e.g.
        # /onboarding/6) instead of always /settings.
        install_url = (
            f"https://github.com/apps/{settings.github_app_slug}/installations/new"
            f"?{urlencode({'state': state})}"
        )
        return RedirectResponse(install_url, status_code=307)

    return _build_post_oauth_redirect(payload, github_app.GITHUB_PROVIDER)


def _bind_installation_company(installation_id: int, company_id: str) -> None:
    """Attach `company_id` to an installation row (idempotent), keyed on
    installation_id. The webhook may have created the row first with no company;
    this binds it. If the row is missing or thin (callback fired before the
    webhook), backfill the real account details from GitHub's App API so we
    never persist an empty skeleton.

    Called from the post-install callback — the only flow that knows both the
    installation_id (from GitHub's Setup-URL redirect) and the company (from the
    signed state)."""
    try:
        existing = db.get_github_installation(installation_id)
        # Never re-key an installation already bound to a DIFFERENT company.
        # First-time bind (company_id None/empty) and same-company rebind both
        # fall through and proceed as before; only a cross-company rebind is a
        # no-op, so one tenant's callback can't steal another's installation.
        if (
            existing
            and existing.get("company_id")
            and existing["company_id"] != company_id
        ):
            logger.info(
                "connectors.github_install_rebind_skipped_cross_company installation=%s",
                installation_id,
            )
            return
        thin = (
            not existing
            or not existing.get("account_login")
            or int(existing.get("account_id") or 0) == 0
        )
        detail = github_app.fetch_app_installation(installation_id) if thin else None
        acct = (detail or {}).get("account") or {}
        ex = existing or {}
        db.upsert_github_installation(
            installation_id=installation_id,
            account_id=int(acct.get("id") or ex.get("account_id") or 0),
            account_login=str(acct.get("login") or ex.get("account_login") or ""),
            account_type=str(acct.get("type") or ex.get("account_type") or "User"),
            repository_selection=str(
                (detail or {}).get("repository_selection")
                or ex.get("repository_selection") or "selected"
            ),
            suspended=bool(ex.get("suspended") or False),
            permissions=(detail or {}).get("permissions")
                or json.loads(ex.get("permissions_json") or "{}"),
            events=(detail or {}).get("events")
                or json.loads(ex.get("events_json") or "[]"),
            company_id=company_id,
        )
    except Exception:
        logger.warning(
            "Failed to bind GitHub installation %s to company", installation_id,
            exc_info=True,
        )
        return

    # Connection is established: warm the codebase map ahead of the first /locate.
    # Best-effort + bounded + coalesced + non-blocking (see helper); a failure here
    # must never affect the just-completed bind.
    _prewarm_codebase_map_on_connect(installation_id)


def _has_github_install_for(account_login: str, company_id: str) -> bool:
    """True iff THIS company already has a Sprntly App installation for the
    given GitHub account login. Read-only — webhook handlers populate this
    table when users install/uninstall the App. Company-scoped so one company's
    install never suppresses another company's install prompt."""
    try:
        rows = db.list_github_installations(company_id) or []
    except Exception:
        # Table may not exist in some local-dev / test contexts; be lenient
        # and assume "no install" so we still redirect to the install page.
        return False
    needle = account_login.lower()
    return any(
        (row.get("account_login") or "").lower() == needle for row in rows
    )


@router.delete("/github")
def github_disconnect(
    company: CompanyContext = Depends(require_company),
):
    _require_admin_for_org_connector(company, github_app.GITHUB_PROVIDER)
    row = db.get_connection(company.company_id, github_app.GITHUB_PROVIDER)
    if not row:
        raise HTTPException(404, "GitHub is not connected")
    db.delete_connection(company.company_id, github_app.GITHUB_PROVIDER)
    return {"deleted": True, "provider": github_app.GITHUB_PROVIDER}


@router.get("/github/installations")
def github_list_installations(
    company: CompanyContext = Depends(require_company),
):
    """Installations owned by the caller's company (member-shared).

    Company-scoped: a signed-in user only sees their own company's GitHub
    installs, never another tenant's. Legacy NULL-company rows are excluded."""
    return {"installations": db.list_github_installations(company.company_id)}


# ─────────── Per-installation repository management ───────────
#
# These wrap GitHub's `/user/installations/{id}/repositories` family,
# which is gated on the USER's OAuth token (not the App JWT). The user
# can add/remove repos from a "selected repositories" install. For an
# "all repositories" install GitHub returns 422 and the UI should
# disable the per-repo toggles (deep-link to GitHub settings instead).


def _require_company_owns_installation(installation_id: int, company_id: str) -> None:
    """404 unless `installation_id` is bound to the caller's company.

    Guards the per-installation repo-management routes (already require_company)
    so a member of company A can't manipulate company B's installation by
    guessing its numeric id. Legacy NULL-company installs are also rejected
    (they must be reconnected to bind a company first)."""
    if not db.get_github_installation_for_company(installation_id, company_id):
        raise HTTPException(404, "GitHub installation not found")


def _github_user_install_url(installation_id: int, repository_id: int | None = None) -> str:
    base = (
        f"https://api.github.com/user/installations/{installation_id}/repositories"
    )
    if repository_id is not None:
        return f"{base}/{repository_id}"
    return base


def _github_user_token_headers(company_id: str) -> dict[str, str]:
    """User-OAuth Bearer headers for /user/installations/* endpoints."""
    return {
        "Authorization": f"Bearer {_github_access_token(company_id)}",
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
    }


@router.get("/github/installations/{installation_id}/repositories")
def github_list_install_repos(
    installation_id: int,
    company: CompanyContext = Depends(require_company),
):
    """List repositories accessible to this installation, using the App
    INSTALLATION token (self-minting, no 8h OAuth clock) so the picker works
    for any company member long after the connecting member's personal token
    has aged out."""
    _require_company_owns_installation(installation_id, company.company_id)
    repos = github_app.fetch_installation_repos(installation_id)
    return {
        "installation_id": installation_id,
        "total": len(repos),
        "repositories": [
            {
                "id": r.get("id"),
                "name": r.get("name"),
                "full_name": r.get("full_name"),
                "private": r.get("private"),
                "html_url": r.get("html_url"),
                "default_branch": r.get("default_branch"),
                "description": r.get("description"),
            }
            for r in repos
        ],
    }


@router.put(
    "/github/installations/{installation_id}/repositories/{repository_id}"
)
def github_add_install_repo(
    installation_id: int,
    repository_id: int,
    company: CompanyContext = Depends(require_company),
):
    """Add a repo to this installation. 422 if the install is in
    'all repositories' mode (per-repo control disallowed there)."""
    _require_company_owns_installation(installation_id, company.company_id)
    r = requests.put(
        _github_user_install_url(installation_id, repository_id),
        headers=_github_user_token_headers(company.company_id),
        timeout=10,
    )
    if r.status_code == 422:
        raise HTTPException(
            422,
            "This installation is set to 'All repositories'. "
            "Switch it to 'Only select repositories' on GitHub to "
            "manage repos per-app.",
        )
    if not r.ok:
        raise HTTPException(r.status_code, f"GitHub: {r.text[:200]}")
    return {"added": True, "installation_id": installation_id, "repository_id": repository_id}


@router.delete(
    "/github/installations/{installation_id}/repositories/{repository_id}"
)
def github_remove_install_repo(
    installation_id: int,
    repository_id: int,
    company: CompanyContext = Depends(require_company),
):
    """Remove a repo from this installation."""
    _require_company_owns_installation(installation_id, company.company_id)
    r = requests.delete(
        _github_user_install_url(installation_id, repository_id),
        headers=_github_user_token_headers(company.company_id),
        timeout=10,
    )
    if not r.ok:
        raise HTTPException(r.status_code, f"GitHub: {r.text[:200]}")
    return {"removed": True, "installation_id": installation_id, "repository_id": repository_id}


@router.get("/github/pull-requests")
def github_list_open_prs(
    installation_id: int | None = None,
    company: CompanyContext = Depends(require_company),
):
    """Open PRs tracked for the caller's company (member-shared).

    Company-scoped. If `installation_id` is given it must belong to the
    caller's company (else 404), so it can't be used to read another tenant's
    PRs."""
    if installation_id is not None and not db.get_github_installation_for_company(
        installation_id, company.company_id
    ):
        raise HTTPException(404, "GitHub installation not found")
    return {
        "pull_requests": db.list_open_pull_requests(
            company.company_id, installation_id
        )
    }


def _github_access_token(company_id: str) -> str:
    """Decrypt the stored GitHub user OAuth token. Raises 404 if not connected."""
    row = db.get_connection(company_id, github_app.GITHUB_PROVIDER)
    if not row:
        raise HTTPException(404, "GitHub is not connected")
    try:
        token_json = json.loads(decrypt_token_json(row["token_json_encrypted"]))
    except (TokenEncryptionError, json.JSONDecodeError) as e:
        raise HTTPException(500, "GitHub token unreadable") from e
    access_token = token_json.get("access_token")
    if not access_token:
        raise HTTPException(500, "GitHub token has no access_token")
    return access_token


@router.get("/github/repos")
def github_list_repos(
    per_page: int = 50,
    company: CompanyContext = Depends(require_company),
):
    """List repos the connected user can access. Engineer Agent uses this
    to discover the codebase context for a workspace; installation tokens
    will be used later for read-write operations."""
    token = _github_access_token(company.company_id)
    return {"repositories": github_app.fetch_user_repos(token, per_page=per_page)}


@router.get("/github/accessible-repos")
def github_list_accessible_repos(
    company: CompanyContext = Depends(require_company),
):
    """Repos the Sprntly App can read, aggregated across every installation
    owned by the caller's company. Uses each install's App TOKEN, not the
    OAuth user token — so the list matches what was granted at App-install
    time, not the OAuth scope (read:user user:email, which is too narrow
    to enumerate private repos via /user/repos).

    Returns an empty list (never 5xx) when the company has no install or
    when every install's token-mint / GitHub call fails — the picker UI
    surfaces that as "no repos accessible" rather than an error toast.

    Company-scoped, member-shared: any member of the company that owns
    the installation can list the repos."""
    installs = db.list_github_installations(company.company_id)
    if not installs:
        return {"repositories": []}
    seen: set[str] = set()
    out: list[dict] = []
    for install in installs:
        install_id = install.get("installation_id")
        if not install_id:
            continue
        try:
            repos = github_app.fetch_installation_repos(int(install_id))
        except Exception:
            logger.warning(
                "accessible-repos: install %s lookup failed",
                install_id, exc_info=True,
            )
            continue
        for r in repos:
            fn = r.get("full_name")
            if not fn or fn in seen:
                continue
            seen.add(fn)
            out.append(r)
    out.sort(key=lambda r: (r.get("full_name") or "").lower())
    return {"repositories": out}


class GitHubSyncCorpusIn(BaseModel):
    dataset: str
    installation_id: int | None = None


@router.post("/github/sync-to-corpus")
def github_sync_to_corpus(
    body: GitHubSyncCorpusIn,
    company: CompanyContext = Depends(require_company),
):
    """Sync tracked GitHub PRs into the corpus as a markdown file.

    Reads open PRs from the github_pull_requests table and writes
    a summary into DATA_DIR/{dataset}/github_active_prs.md.

    Company-scoped: only the caller's company's PRs are read, a supplied
    installation_id must belong to the caller's company (else 404), and the
    target dataset must be one the caller's company owns (else 404)."""
    dataset = _gate_dataset(body.dataset, company.company_id)
    _require_admin_for_org_connector(company, github_app.GITHUB_PROVIDER)
    if body.installation_id is not None and not db.get_github_installation_for_company(
        body.installation_id, company.company_id
    ):
        raise HTTPException(404, "GitHub installation not found")
    prs = db.list_open_pull_requests(company.company_id, body.installation_id)

    lines: list[str] = ["# GitHub Active Pull Requests\n"]
    if not prs:
        lines.append("_No open pull requests tracked._\n")
    else:
        lines.append(f"**Total open PRs:** {len(prs)}\n")
        for pr in prs:
            title = pr.get("title", "Untitled")
            repo = pr.get("repo_full_name", "")
            number = pr.get("pr_number", "")
            author = pr.get("author_login", "unknown")
            state = pr.get("state", "open")
            draft = " (DRAFT)" if pr.get("is_draft") else ""
            head = pr.get("head_ref", "")
            base = pr.get("base_ref", "")
            body_text = pr.get("body_excerpt") or ""

            lines.append(f"## PR #{number}: {title}{draft}")
            lines.append(f"- **Repo:** {repo}")
            lines.append(f"- **Author:** @{author}")
            lines.append(f"- **State:** {state}")
            lines.append(f"- **Branch:** {head} → {base}")
            if body_text:
                lines.append(f"- **Description:** {body_text[:200]}")
            lines.append("")

    md_text = "\n".join(lines) + "\n"
    target = settings.data_path / dataset / "github_active_prs.md"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(md_text, encoding="utf-8")

    # Auto-enable github input source
    try:
        db.upsert_input_source(
            dataset, "github", enabled=True,
            config={"last_sync_at": db.utc_now()},
        )
    except Exception:
        logger.warning("Failed to auto-enable github input source", exc_info=True)

    return {"ok": True, "chars": len(md_text), "pr_count": len(prs), "path": str(target)}


# ─────────────────────── Connector sync status ───────────────────────


@router.get("/sync-status")
def connector_sync_status(
    company: CompanyContext = Depends(require_company),
):
    """Summary of all connector sync states + corpus stats.

    Returns per-connector status and per-dataset corpus size for the
    caller's company only. Used for dashboards to verify data capture.
    """
    connections = db.list_connections(company.company_id)
    connectors_out = []
    for row in connections:
        config = {}
        if row.get("config_json"):
            try:
                config = json.loads(row["config_json"])
            except (TypeError, ValueError):
                pass
        connectors_out.append({
            "provider": row["provider"],
            "status": row["status"],
            "account_label": row.get("account_label") or row.get("google_email"),
            "last_sync_at": row.get("last_sync_at"),
            "last_sync_error": row.get("last_sync_error"),
            "dataset": config.get("dataset"),
        })

    # Corpus stats per dataset
    datasets_out = []
    for ds in db.list_datasets():
        slug = ds["slug"]
        base = settings.data_path / slug
        md_count = 0
        total_chars = 0
        if base.exists():
            for p in base.glob("*.md"):
                if not p.name.startswith("_"):
                    md_count += 1
                    total_chars += p.stat().st_size
        datasets_out.append({
            "slug": slug,
            "display_name": ds.get("display_name", slug),
            "md_file_count": md_count,
            "total_chars": total_chars,
        })

    return {"connectors": connectors_out, "datasets": datasets_out}
# ─────────────────────── ClickUp ───────────────────────
#
# Commit H. OAuth-only — no data sync into the corpus yet. Follow-on
# slice will add task → markdown sync similar to Drive's pattern.


@router.get("/clickup/callback")
def clickup_callback(code: str, state: str):
    payload = clickup_oauth.verify_oauth_state(state)
    company_id = payload["company_id"]
    token_json = clickup_oauth.exchange_code_for_token(code)
    access_token = token_json.get("access_token")
    if not access_token:
        raise HTTPException(400, "ClickUp did not return an access_token")

    user = clickup_oauth.fetch_authenticated_user(access_token)
    label = user.get("email") or user.get("username") or str(user.get("id") or "")

    try:
        token_encrypted = encrypt_token_json(
            clickup_oauth.token_payload_to_store(token_json)
        )
    except TokenEncryptionError as e:
        raise HTTPException(500, str(e)) from e

    db.upsert_connection(
        company_id=company_id,
        provider=clickup_oauth.CLICKUP_PROVIDER,
        token_encrypted=token_encrypted,
        scopes="",
        account_label=label or None,
        config_json=json.dumps({"user": user}) if user else "{}",
    )

    kickoff_sync(company_id, clickup_oauth.CLICKUP_PROVIDER)

    # Connect-time vocabulary pull: cache every list's statuses/fields NOW so
    # the ticket detail is ClickUp-native immediately — no push/binding first.
    from app.connectors.tracker_meta import kick_company_meta_warm

    kick_company_meta_warm(company_id, clickup_oauth.CLICKUP_PROVIDER)

    return _build_post_oauth_redirect(payload, clickup_oauth.CLICKUP_PROVIDER)


@router.delete("/clickup")
def clickup_disconnect(
    company: CompanyContext = Depends(require_company),
):
    _require_admin_for_org_connector(company, clickup_oauth.CLICKUP_PROVIDER)
    row = db.get_connection(company.company_id, clickup_oauth.CLICKUP_PROVIDER)
    if not row:
        raise HTTPException(404, "ClickUp is not connected")
    db.delete_connection(company.company_id, clickup_oauth.CLICKUP_PROVIDER)
    return {"deleted": True, "provider": clickup_oauth.CLICKUP_PROVIDER}


@router.get("/jira/callback")
def jira_callback(code: str, state: str):
    payload = jira_oauth.verify_oauth_state(state)
    company_id = payload["company_id"]
    token_json = jira_oauth.exchange_code_for_token(code)
    access_token = token_json.get("access_token")
    if not access_token:
        raise HTTPException(400, "Jira did not return an access_token")

    # Resolve the Jira site(s) this token can act on. cloud_id is required for
    # every subsequent REST call (ingest + issue creation) and is NOT in the
    # token response, so we cache it (and the site list) on the connection.
    sites = jira_oauth.get_accessible_resources(access_token)
    cloud_id = sites[0].get("id") if sites else None
    user = jira_oauth.fetch_authenticated_user(access_token, cloud_id) if cloud_id else {}
    label = (
        user.get("emailAddress")
        or user.get("displayName")
        or (sites[0].get("name") if sites else None)
    )

    try:
        token_encrypted = encrypt_token_json(
            jira_oauth.token_payload_to_store(token_json)
        )
    except TokenEncryptionError as e:
        raise HTTPException(500, str(e)) from e

    db.upsert_connection(
        company_id=company_id,
        provider=jira_oauth.JIRA_PROVIDER,
        token_encrypted=token_encrypted,
        scopes=jira_oauth.JIRA_SCOPES,
        account_label=label or None,
        config_json=json.dumps({"cloud_id": cloud_id, "sites": sites, "user": user}),
    )

    kickoff_sync(company_id, jira_oauth.JIRA_PROVIDER)

    # Connect-time vocabulary pull: cache every project's statuses/priorities/
    # custom fields NOW so the ticket detail is Jira-native immediately — no
    # push/binding first.
    from app.connectors.tracker_meta import kick_company_meta_warm

    kick_company_meta_warm(company_id, jira_oauth.JIRA_PROVIDER)

    return _build_post_oauth_redirect(payload, jira_oauth.JIRA_PROVIDER)


@router.delete("/jira")
def jira_disconnect(
    company: CompanyContext = Depends(require_company),
):
    _require_admin_for_org_connector(company, jira_oauth.JIRA_PROVIDER)
    row = db.get_connection(company.company_id, jira_oauth.JIRA_PROVIDER)
    if not row:
        raise HTTPException(404, "Jira is not connected")
    db.delete_connection(company.company_id, jira_oauth.JIRA_PROVIDER)
    return {"deleted": True, "provider": jira_oauth.JIRA_PROVIDER}


# ─────────────────────── Confluence ───────────────────────


@router.get("/confluence/callback")
def confluence_callback(code: str, state: str):
    payload = confluence_oauth.verify_oauth_state(state)
    company_id = payload["company_id"]
    token_json = confluence_oauth.exchange_code_for_token(code)
    access_token = token_json.get("access_token")
    if not access_token:
        raise HTTPException(400, "Confluence did not return an access_token")

    # Same cloud_id quirk as Jira: it is required for every subsequent REST
    # call and is NOT in the token response, so cache it (and the site list)
    # on the connection.
    sites = confluence_oauth.get_accessible_resources(access_token)
    cloud_id = sites[0].get("id") if sites else None
    user = (
        confluence_oauth.fetch_current_user(access_token, cloud_id)
        if cloud_id else {}
    )
    label = confluence_oauth.account_label_from(user, sites)

    try:
        token_encrypted = encrypt_token_json(
            # company_id rides inside the encrypted payload because it IS the
            # credential the kg_ingest puller will be handed (PULLERS key
            # "company_id") — the puller needs the connection's config, which
            # a lone access token can't reach. See token_payload_to_store.
            confluence_oauth.token_payload_to_store(
                token_json, company_id=company_id
            )
        )
    except TokenEncryptionError as e:
        raise HTTPException(500, str(e)) from e

    db.upsert_connection(
        company_id=company_id,
        provider=confluence_oauth.CONFLUENCE_PROVIDER,
        token_encrypted=token_encrypted,
        scopes=confluence_oauth.CONFLUENCE_SCOPES,
        account_label=label,
        config_json=json.dumps(
            {
                confluence_oauth.CONFIG_CLOUD_ID: cloud_id,
                "sites": sites,
                "user": user,
            }
        ),
    )

    # No-op today (confluence has no entry in kg_ingest PULLERS yet) but wired
    # now so the puller PR is a one-line registration rather than a route edit.
    kickoff_sync(company_id, confluence_oauth.CONFLUENCE_PROVIDER)

    return _build_post_oauth_redirect(payload, confluence_oauth.CONFLUENCE_PROVIDER)


@router.delete("/confluence")
def confluence_disconnect(
    company: CompanyContext = Depends(require_company),
):
    _require_admin_for_org_connector(company, confluence_oauth.CONFLUENCE_PROVIDER)
    row = db.get_connection(company.company_id, confluence_oauth.CONFLUENCE_PROVIDER)
    if not row:
        raise HTTPException(404, "Confluence is not connected")
    db.delete_connection(company.company_id, confluence_oauth.CONFLUENCE_PROVIDER)
    return {"deleted": True, "provider": confluence_oauth.CONFLUENCE_PROVIDER}


@router.get("/confluence/spaces")
def confluence_list_spaces(
    company: CompanyContext = Depends(require_company),
):
    """The spaces the connected account can read, for the picker.

    Readable by any member (mirrors slack_list_channels, which is not
    admin-gated) — seeing what COULD be synced is not a privileged action;
    changing the selection is.

    Personal spaces are excluded. Note this list is bounded by the connecting
    user's own Confluence permissions: a space they cannot read simply is not
    here, and there is no scope that would widen it."""
    try:
        ctx = confluence_oauth.sync_context(company.company_id)
        spaces = confluence_oauth.list_spaces(ctx.access_token, ctx.cloud_id)
    except confluence_oauth.ConfluenceNotConnectedError as e:
        raise HTTPException(404, str(e)) from e
    except confluence_oauth.ConfluenceAuthExpiredError as e:
        # Must be caught: an escaping exception becomes an unhandled 500 with
        # no CORS headers, which the browser reports as a bare "Failed to
        # fetch" — the picker then shows a network error for what is really a
        # reconnect prompt. The commonest cause is a token minted before the
        # granular v2 scopes were added, which fails with
        # "Unauthorized; scope does not match".
        raise HTTPException(400, str(e)) from e
    return {"spaces": spaces, "selected_ids": ctx.space_ids}


class ConfluenceSpaceIn(BaseModel):
    id: str
    key: str | None = None

    def model_post_init(self, _context) -> None:
        self.id = (self.id or "").strip()
        if not self.id:
            raise ValueError("space id cannot be empty")


class ConfluenceSyncSpacesIn(BaseModel):
    spaces: list[ConfluenceSpaceIn]

    def model_post_init(self, _context) -> None:
        # The puller caps at _MAX_SPACES — refuse a selection it could never
        # honor rather than silently truncating one.
        if len(self.spaces) > 25:
            raise ValueError("select at most 25 spaces")


@router.post("/confluence/spaces")
def confluence_save_sync_spaces(
    body: ConfluenceSyncSpacesIn,
    company: CompanyContext = Depends(require_company),
):
    """Save which spaces the KG ingest pulls from — COMPANY-WIDE.

    An EMPTY list clears the selection, which means every readable space
    again. That is the backwards-compatible default (same rule as Slack's
    channel selection): a connection made before this picker existed has no
    stored selection and must keep working.

    Keys are stored alongside the ids so a space that later becomes
    unreadable can be reported BY NAME in the sync log rather than as an
    opaque id."""
    _require_admin_for_org_connector(company, confluence_oauth.CONFLUENCE_PROVIDER)
    row = db.get_connection(company.company_id, confluence_oauth.CONFLUENCE_PROVIDER)
    if not row:
        raise HTTPException(404, "Confluence is not connected")
    # Dedupe preserving order — the puller walks the selection in order.
    ids = list(dict.fromkeys(s.id for s in body.spaces))
    keys = {
        s.id: s.key.strip()
        for s in body.spaces
        if s.key and s.key.strip()
    }
    updated = db.patch_connection_config(
        company.company_id,
        confluence_oauth.CONFLUENCE_PROVIDER,
        {
            confluence_oauth.CONFIG_SYNC_SPACE_IDS: ids,
            confluence_oauth.CONFIG_SYNC_SPACE_KEYS: keys,
        },
    )
    try:
        config = json.loads((updated or {}).get("config_json") or "{}")
    except (TypeError, ValueError):
        config = {}
    # Pull the new selection now rather than waiting for the 6-hourly sweep —
    # the user just told us what they want ingested.
    kickoff_sync(company.company_id, confluence_oauth.CONFLUENCE_PROVIDER)
    return {"ok": True, "config": config}


# ─────────────────────── Zoom ───────────────────────
#
# Cloud recordings + transcripts. ORG-WIDE, not per-user: every scope is an
# `:admin` scope so one connection reads every host's recordings, which is why
# zoom is deliberately NOT in _PERSONAL_PROVIDERS and every mutating route here
# goes through _require_admin_for_org_connector.
#
# CURRENT SCOPE: connect / disconnect / probe / host picker. No kg_ingest
# puller yet, so a connected Zoom is healthy and ingests nothing.


@router.get("/zoom/callback")
def zoom_callback(
    state: str,
    code: str | None = None,
    error: str | None = None,
    error_description: str | None = None,
):
    """Zoom's OAuth redirect target.

    UNAUTHENTICATED by construction — Zoom calls this, not the user's app tab,
    so there is no session and no company header. The signed `state` JWT is the
    entire trust boundary: `verify_oauth_state` is what decides whose company
    this token gets written to, and its provider claim is what stops another
    connector's state being replayed here.
    """
    payload = zoom_oauth.verify_oauth_state(state)
    company_id = payload["company_id"]

    # Consent did not produce a code. THREE different things end up here and
    # they need three different sentences from the return page, so they get
    # three stable codes rather than one catch-all:
    #
    #   access_denied         the user clicked Decline. Nothing is wrong; they
    #                         just have to try again and accept. Telling them to
    #                         "ask your Zoom admin to approve Sprntly" would
    #                         send them to bother a colleague over their own
    #                         click.
    #   approval prose        a Zoom admin has not pre-approved the app
    #                         (Marketplace → Manage → Approved Apps). This one
    #                         genuinely does need another person.
    #   anything else         honest generic failure.
    #
    # Zoom's own error string is never forwarded: it changes without notice and
    # would land straight on a screen.
    if error:
        logger.warning(
            "Zoom consent failed for %s: %s (%s)",
            company_id, error, (error_description or "")[:200],
        )
        if zoom_oauth.looks_like_app_not_approved(error, error_description):
            code_out = "zoom_app_not_approved"
        elif (error or "").strip().lower() == "access_denied":
            code_out = "zoom_consent_declined"
        else:
            code_out = "zoom_oauth_failed"
        return _build_post_oauth_error_redirect(
            payload, zoom_oauth.ZOOM_PROVIDER, code_out,
        )
    if not code:
        raise HTTPException(400, "Zoom did not return an authorization code")

    try:
        token_json = zoom_oauth.exchange_code_for_token(code)
    except zoom_oauth.ZoomAppNotApprovedError:
        return _build_post_oauth_error_redirect(
            payload, zoom_oauth.ZOOM_PROVIDER, "zoom_app_not_approved",
        )
    access_token = token_json.get("access_token")
    if not access_token:
        raise HTTPException(400, "Zoom did not return an access_token")

    # Best-effort label only. fetch_current_user answers on `user:read:user:admin`
    # while everything this connector reads answers on the cloud_recording
    # scopes, so a failure here says nothing about whether the connection works
    # — it must not block the connect. The probe validates the read that
    # matters.
    user = zoom_oauth.fetch_current_user(access_token)
    label = zoom_oauth.account_label_from(user)

    try:
        token_encrypted = encrypt_token_json(
            # company_id rides INSIDE the encrypted payload because it IS the
            # credential the kg_ingest puller will be handed: the puller needs
            # the picked hosts off connections.config, which a lone access token
            # can't reach. See zoom_oauth.token_payload_to_store.
            zoom_oauth.token_payload_to_store(token_json, company_id=company_id)
        )
    except TokenEncryptionError as e:
        raise HTTPException(500, str(e)) from e

    # RECONNECT SAFETY. `upsert_connection` REPLACES config_json wholesale, and
    # this callback runs on every reconnect — which is not a rare event here:
    # Zoom refresh tokens expire at 90 days, so every long-lived customer
    # reconnects on a schedule. Writing a fresh `{"user": …}` would silently
    # drop `sync_user_ids`, and an empty selection means EVERY HOST — so a
    # workspace that deliberately narrowed sync to three sales hosts would
    # quietly widen to the whole company every quarter, with no event to trace
    # it to. Start from the existing config and only add.
    existing = db.get_connection(company_id, zoom_oauth.ZOOM_PROVIDER)
    try:
        config = json.loads((existing or {}).get("config_json") or "{}")
    except (TypeError, ValueError):
        config = {}
    if not isinstance(config, dict):
        config = {}
    # Only three fields of the identity payload are kept — config_json is
    # returned verbatim to every company member by GET /v1/connectors, and
    # Zoom's user object carries the admin's personal meeting URL, phone number
    # and department. `id` is the one that matters: it is the real userId the
    # probe addresses instead of `me`. And an identity lookup that failed writes
    # nothing at all rather than stamping an empty dict over a good one.
    if user:
        config[zoom_oauth.CONFIG_USER] = zoom_oauth.identity_to_store(user)

    db.upsert_connection(
        company_id=company_id,
        provider=zoom_oauth.ZOOM_PROVIDER,
        token_encrypted=token_encrypted,
        scopes=zoom_oauth.ZOOM_SCOPES,
        account_label=label,
        config_json=json.dumps(config),
    )

    # Pull now rather than waiting for the 6-hourly sweep — a first connect
    # backfills three months of recordings, and a customer who just connected
    # should see calls in the graph, not an empty one until this evening.
    kickoff_sync(company_id, zoom_oauth.ZOOM_PROVIDER)

    return _build_post_oauth_redirect(payload, zoom_oauth.ZOOM_PROVIDER)


@router.delete("/zoom")
def zoom_disconnect(
    company: CompanyContext = Depends(require_company),
):
    """Disconnect Zoom, revoking the grant on Zoom's side first.

    The revoke is best-effort and deliberately ordered BEFORE the delete: a
    refresh token we merely forget stays valid on Zoom's side for the rest of
    its 90 days, so forgetting without revoking leaves a live credential to this
    customer's recordings in a token store we no longer show them. If the revoke
    fails we still delete — the user asked to disconnect, and keeping our copy
    would be the worse of the two outcomes.
    """
    _require_admin_for_org_connector(company, zoom_oauth.ZOOM_PROVIDER)
    row = db.get_connection(company.company_id, zoom_oauth.ZOOM_PROVIDER)
    if not row:
        raise HTTPException(404, "Zoom is not connected")
    try:
        token_json = json.loads(decrypt_token_json(row["token_json_encrypted"]))
        zoom_oauth.revoke_token(token_json.get("access_token") or "")
    except Exception:  # noqa: BLE001 — an unreadable token is still deletable
        logger.warning(
            "Zoom revoke skipped for %s — deleting the row anyway",
            company.company_id, exc_info=True,
        )
    db.delete_connection(company.company_id, zoom_oauth.ZOOM_PROVIDER)
    # Drop Zoom's half of the call index with the connection — same reasoning as
    # the Fireflies disconnect: chat answering call questions from indexed Zoom
    # rows while the connector list correctly reports Zoom as disconnected puts
    # two contradictory claims in one answer. SCOPED, so a company that also
    # runs Fireflies keeps its Fireflies index intact.
    try:
        from app import call_index

        call_index.clear_company(company.company_id, call_index.PROVIDER_ZOOM)
    except Exception:  # noqa: BLE001 — a cleanup failure must not fail a disconnect
        logger.warning("zoom: could not clear call index for %s",
                       company.company_id, exc_info=True)
    return {"deleted": True, "provider": zoom_oauth.ZOOM_PROVIDER}


#: How many hosts the picker will return. Zoom accounts run to thousands of
#: users; past this a picker needs search rather than a longer scroll, and
#: `truncated` is how the UI says so honestly instead of silently showing a
#: prefix.
_ZOOM_USER_PICKER_LIMIT = 500


@router.get("/zoom/users")
def zoom_list_users(
    company: CompanyContext = Depends(require_company),
):
    """The hosts on the connected Zoom account, for the picker.

    Readable by any member (mirrors confluence_list_spaces and
    slack_list_channels) — seeing what COULD be synced is not a privileged
    action; changing the selection is.

    `recording_count` is present on every row and is null in this slice: the
    count needs one windowed recordings call PER HOST, which is the puller's
    job. It ships as a declared null rather than a missing key so the client
    renders "—" from day one instead of gaining a new field later.

    Three fields describe the size of the answer, and they mean different
    things. `total` is HOW MANY WE FETCHED, not how many exist. `fetch_capped`
    says Zoom still had more pages when the listing budget ran out — on a
    5,000-host account that is the difference between an honest "showing the
    first 500 of at least 1,200" and a flat lie about the customer's own org.
    `truncated` says the response itself was cut to the picker limit.

    `selected_names` returns the names stored WITH the selection rather than
    only the ids. That is the entire reason they are persisted: a host who has
    since been deactivated is gone from `users` (the listing is active-only), so
    without their name the picker can only render a bare opaque id — or, worse,
    silently show a shorter selection than the one actually in force.
    """
    try:
        ctx = zoom_oauth.sync_context(company.company_id)
        found, fetch_capped = zoom_oauth.list_users(ctx.access_token)
    except zoom_oauth.ZoomNotConnectedError as e:
        raise HTTPException(404, str(e)) from e
    except zoom_oauth.ZoomAuthExpiredError as e:
        # Must be caught. An escaping exception becomes an unhandled 500 with no
        # CORS headers, which the browser reports as a bare "Failed to fetch" —
        # the picker then shows a network error for what is really a reconnect
        # prompt. That exact defect shipped on Confluence's space picker
        # (a1e16c40); the likeliest trigger here is the same one: a token minted
        # before a scope changed, or the connecting admin losing their admin role.
        raise HTTPException(400, str(e)) from e
    truncated = len(found) > _ZOOM_USER_PICKER_LIMIT
    users = [
        {**u, "recording_count": None} for u in found[:_ZOOM_USER_PICKER_LIMIT]
    ]
    return {
        "users": users,
        "selected_ids": ctx.user_ids,
        "selected_names": ctx.user_names,
        # Fetched, NOT the account's true user count — see the docstring.
        "total": len(found),
        "fetch_capped": fetch_capped,
        "truncated": truncated,
    }


#: How many hosts one selection may name. Mirrors Confluence's 25-space cap and
#: exists for the same reason: refuse a selection the puller could never honor
#: rather than silently truncating one. Sized for Zoom's cost shape — the puller
#: pays ONE windowed recordings call per selected host per sync window, so 100
#: hosts is 100 calls a pass before a single transcript is read.
_ZOOM_MAX_SYNC_USERS = 100


class ZoomUserIn(BaseModel):
    id: str
    email: str | None = None

    def model_post_init(self, _context) -> None:
        self.id = (self.id or "").strip()
        if not self.id:
            raise ValueError("user id cannot be empty")


class ZoomSyncUsersIn(BaseModel):
    users: list[ZoomUserIn]

    def model_post_init(self, _context) -> None:
        if len(self.users) > _ZOOM_MAX_SYNC_USERS:
            raise ValueError(
                f"select at most {_ZOOM_MAX_SYNC_USERS} hosts — "
                "leave the selection empty to sync every host instead"
            )


@router.post("/zoom/users")
def zoom_save_sync_users(
    body: ZoomSyncUsersIn,
    company: CompanyContext = Depends(require_company),
):
    """Save which hosts' recordings the KG ingest pulls — COMPANY-WIDE.

    An EMPTY list clears the selection, which means every host on the account
    again. That is the backwards-compatible default (the same rule as
    Confluence's spaces and Slack's channels): a connection made before this
    picker existed has no stored selection and must keep working.

    Emails are stored alongside the ids so a host who is later deactivated can
    be reported BY NAME in the sync log rather than as an opaque Zoom user id.
    """
    _require_admin_for_org_connector(company, zoom_oauth.ZOOM_PROVIDER)
    row = db.get_connection(company.company_id, zoom_oauth.ZOOM_PROVIDER)
    if not row:
        raise HTTPException(404, "Zoom is not connected")
    # Dedupe preserving order — the puller walks the selection in order, one
    # windowed recordings call per host, so a duplicated id is a duplicated call.
    ids = list(dict.fromkeys(u.id for u in body.users))
    names = {
        u.id: u.email.strip()
        for u in body.users
        if u.email and u.email.strip()
    }
    updated = db.patch_connection_config(
        company.company_id,
        zoom_oauth.ZOOM_PROVIDER,
        {
            zoom_oauth.CONFIG_SYNC_USER_IDS: ids,
            zoom_oauth.CONFIG_SYNC_USER_NAMES: names,
        },
    )
    try:
        config = json.loads((updated or {}).get("config_json") or "{}")
    except (TypeError, ValueError):
        config = {}
    # Pull the new selection now rather than waiting for the next sweep — the
    # user just told us what they want ingested.
    kickoff_sync(company.company_id, zoom_oauth.ZOOM_PROVIDER)
    return {"ok": True, "config": config}


# ─────────────────────── Google Meet ───────────────────────
#
# Meeting transcripts, read from the Meet REST API v2. Shares the Google Cloud
# project and OAuth client with the Drive connector but is a fully separate
# provider — its own redirect URI, its own connection row, its own state signer
# and its own scope list (see connectors/google_meet.py for why the scope lists
# must never merge).
#
# NOT ORG-WIDE, and this is the opposite of Zoom. Google exposes only the
# conferences the connected account ORGANIZED; there is no admin-level listing
# and no scope that would add one. It is nevertheless treated as an ORG
# connector for RBAC (absent from _PERSONAL_PROVIDERS, every mutation behind
# _require_admin_for_org_connector), because the connection row is
# company-scoped: one row per company, so whoever holds it decides what the
# whole workspace ingests. Per-user Meet connections would need the per-user
# row shape Slack has, which is its own change.
#
# There is no picker route. There is nothing to pick: coverage is fixed to the
# connecting account's own meetings, and the 30-day retention window is Google's
# to set, not the customer's.


@router.get("/google-meet/callback")
def google_meet_callback(
    state: str,
    code: str | None = None,
    error: str | None = None,
    error_description: str | None = None,
):
    """Google's OAuth redirect target.

    UNAUTHENTICATED by construction — Google calls this, not the user's app tab,
    so there is no session and no company header. The signed `state` JWT is the
    entire trust boundary: `verify_oauth_state` is what decides whose company
    this token gets written to, and its provider claim is what stops another
    connector's state (including the Drive connector's, minted by the very same
    OAuth client) being replayed here.
    """
    payload = google_meet.verify_oauth_state(state)
    company_id = payload["company_id"]

    # Consent did not produce a code. Google's own error string is never
    # forwarded — it changes without notice and would land straight on a screen
    # — so this collapses to two stable codes the return page maps to copy:
    # the user declined (nothing is wrong, try again and accept), or anything
    # else (honest generic failure).
    if error:
        logger.warning(
            "Google Meet consent failed for %s: %s (%s)",
            company_id, error, (error_description or "")[:200],
        )
        code_out = (
            "google_meet_consent_declined"
            if (error or "").strip().lower() == "access_denied"
            else "google_meet_oauth_failed"
        )
        return _build_post_oauth_error_redirect(
            payload, google_meet.GOOGLE_MEET_PROVIDER, code_out,
        )
    if not code:
        raise HTTPException(400, "Google Meet did not return an authorization code")

    token_json = google_meet.exchange_code_for_token(code)
    access_token = token_json.get("access_token")
    if not access_token:
        raise HTTPException(400, "Google Meet did not return an access_token")

    # The email comes free out of the OIDC id_token (we request openid +
    # userinfo.email), so the common path costs no extra round trip. The
    # userinfo call is only the fallback, and a failure there costs the LABEL,
    # never the connection: userinfo answers on a different scope from every
    # meeting read, so it says nothing about whether the connector works. The
    # probe validates the read that matters.
    email = google_meet.email_from_id_token(token_json)
    user = {} if email else google_meet.fetch_current_user(access_token)
    label = google_meet.account_label_from(user, email=email)

    try:
        token_encrypted = encrypt_token_json(
            # company_id rides INSIDE the encrypted payload because it IS the
            # credential the kg_ingest puller will be handed — see
            # google_meet.token_payload_to_store.
            google_meet.token_payload_to_store(token_json, company_id=company_id)
        )
    except TokenEncryptionError as e:
        raise HTTPException(500, str(e)) from e

    # RECONNECT SAFETY. `upsert_connection` REPLACES config_json wholesale, and
    # this callback runs on every reconnect. Writing a fresh dict would drop
    # whatever else lives there — today the puller's sync counters, tomorrow
    # anything a config surface adds — which is exactly the regression the Zoom
    # callback had to be fixed for (there it silently widened a narrowed host
    # selection back to every host, once a quarter, with no event to trace it
    # to). Start from the existing config and only add.
    existing = db.get_connection(company_id, google_meet.GOOGLE_MEET_PROVIDER)
    try:
        config = json.loads((existing or {}).get("config_json") or "{}")
    except (TypeError, ValueError):
        config = {}
    if not isinstance(config, dict):
        config = {}
    # Only {id, email} of the identity payload is kept — config_json is returned
    # verbatim to every company member by GET /v1/connectors, and Google's
    # userinfo carries the connecting person's full name, profile picture URL,
    # locale and hosted domain. An identity lookup that produced nothing writes
    # nothing at all rather than stamping an empty dict over a good one.
    identity = google_meet.identity_to_store(user, email=email)
    if identity.get("id") or identity.get("email"):
        config[google_meet.CONFIG_USER] = identity

    db.upsert_connection(
        company_id=company_id,
        provider=google_meet.GOOGLE_MEET_PROVIDER,
        token_encrypted=token_encrypted,
        scopes=google_meet.MEET_SCOPE_STRING,
        account_label=label,
        config_json=json.dumps(config),
    )

    # Pull now rather than waiting for the 6-hourly sweep. It matters more here
    # than on any other connector: the corpus is only ever the last 30 days, so
    # every hour of delay is an hour of the window that will expire unread.
    kickoff_sync(company_id, google_meet.GOOGLE_MEET_PROVIDER)

    return _build_post_oauth_redirect(payload, google_meet.GOOGLE_MEET_PROVIDER)


@router.delete("/google-meet")
def google_meet_disconnect(
    company: CompanyContext = Depends(require_company),
):
    """Disconnect Google Meet, revoking the grant on Google's side first.

    The revoke is best-effort and deliberately ordered BEFORE the delete. It
    matters more than on the connectors with a token clock: Google refresh
    tokens do not expire on a schedule, so one we merely forget stays live
    indefinitely — a permanent credential to this customer's meeting
    transcripts, in a token store we no longer show them. If the revoke fails we
    still delete: the user asked to disconnect, and keeping our copy would be
    the worse of the two outcomes.
    """
    _require_admin_for_org_connector(company, google_meet.GOOGLE_MEET_PROVIDER)
    row = db.get_connection(company.company_id, google_meet.GOOGLE_MEET_PROVIDER)
    if not row:
        raise HTTPException(404, "Google Meet is not connected")
    try:
        token_json = json.loads(decrypt_token_json(row["token_json_encrypted"]))
        # The refresh token is the one worth killing — revoking any token of a
        # grant invalidates the whole grant, and the access token would have
        # expired within the hour anyway.
        google_meet.revoke_token(
            token_json.get("refresh_token") or token_json.get("access_token") or ""
        )
    except Exception:  # noqa: BLE001 — an unreadable token is still deletable
        logger.warning(
            "Google Meet revoke skipped for %s — deleting the row anyway",
            company.company_id, exc_info=True,
        )
    db.delete_connection(company.company_id, google_meet.GOOGLE_MEET_PROVIDER)
    return {"deleted": True, "provider": google_meet.GOOGLE_MEET_PROVIDER}


# ─────────────────────── HubSpot ───────────────────────
#
# Commit I. OAuth-only — no corpus sync yet.


@router.get("/hubspot/callback")
def hubspot_callback(code: str, state: str):
    payload = hubspot_oauth.verify_oauth_state(state)
    company_id = payload["company_id"]
    token_json = hubspot_oauth.exchange_code_for_token(code)
    access_token = token_json.get("access_token")
    if not access_token:
        raise HTTPException(400, "HubSpot did not return an access_token")

    info = hubspot_oauth.fetch_token_info(access_token)
    # `user` is the authenticated user's email per the token-info endpoint
    # (https://api.hubapi.com/oauth/v1/access-tokens/{token}).
    label = info.get("user") or info.get("hub_domain") or str(info.get("hub_id") or "")

    try:
        token_encrypted = encrypt_token_json(
            hubspot_oauth.token_payload_to_store(token_json)
        )
    except TokenEncryptionError as e:
        raise HTTPException(500, str(e)) from e

    db.upsert_connection(
        company_id=company_id,
        provider=hubspot_oauth.HUBSPOT_PROVIDER,
        token_encrypted=token_encrypted,
        scopes=" ".join(info.get("scopes") or []) if isinstance(info.get("scopes"), list) else "",
        account_label=label or None,
        config_json=json.dumps({"info": info}) if info else "{}",
    )

    kickoff_sync(company_id, hubspot_oauth.HUBSPOT_PROVIDER)

    return _build_post_oauth_redirect(payload, hubspot_oauth.HUBSPOT_PROVIDER)


@router.delete("/hubspot")
def hubspot_disconnect(
    company: CompanyContext = Depends(require_company),
):
    _require_admin_for_org_connector(company, hubspot_oauth.HUBSPOT_PROVIDER)
    row = db.get_connection(company.company_id, hubspot_oauth.HUBSPOT_PROVIDER)
    if not row:
        raise HTTPException(404, "HubSpot is not connected")
    db.delete_connection(company.company_id, hubspot_oauth.HUBSPOT_PROVIDER)
    return {"deleted": True, "provider": hubspot_oauth.HUBSPOT_PROVIDER}


# ─────────────────────── Sprinklr ───────────────────────
#
# Customer-voice connector: OAuth + KG ingestion (cases + inbound social
# messages via app/kg_ingest/pullers/sprinklr.py). No corpus sync.


@router.get("/sprinklr/callback")
def sprinklr_callback(code: str, state: str):
    payload = sprinklr_oauth.verify_oauth_state(state)
    company_id = payload["company_id"]
    token_json = sprinklr_oauth.exchange_code_for_token(code)
    access_token = token_json.get("access_token")
    if not access_token:
        raise HTTPException(400, "Sprinklr did not return an access_token")

    # Best-effort identity for the account label — a /me hiccup must not
    # fail the connect (the token itself already proved valid above).
    info = sprinklr_oauth.fetch_authenticated_user(access_token)
    label = (
        info.get("email")
        or info.get("emailId")
        or info.get("fullName")
        or " ".join(x for x in [info.get("firstName"), info.get("lastName")] if x)
        or ""
    )

    try:
        token_encrypted = encrypt_token_json(
            sprinklr_oauth.token_payload_to_store(token_json)
        )
    except TokenEncryptionError as e:
        raise HTTPException(500, str(e)) from e

    db.upsert_connection(
        company_id=company_id,
        provider=sprinklr_oauth.SPRINKLR_PROVIDER,
        token_encrypted=token_encrypted,
        scopes="",
        account_label=label or None,
        config_json=json.dumps({"info": info}) if info else "{}",
    )

    kickoff_sync(company_id, sprinklr_oauth.SPRINKLR_PROVIDER)

    return _build_post_oauth_redirect(payload, sprinklr_oauth.SPRINKLR_PROVIDER)


@router.delete("/sprinklr")
def sprinklr_disconnect(
    company: CompanyContext = Depends(require_company),
):
    _require_admin_for_org_connector(company, sprinklr_oauth.SPRINKLR_PROVIDER)
    row = db.get_connection(company.company_id, sprinklr_oauth.SPRINKLR_PROVIDER)
    if not row:
        raise HTTPException(404, "Sprinklr is not connected")
    db.delete_connection(company.company_id, sprinklr_oauth.SPRINKLR_PROVIDER)
    return {"deleted": True, "provider": sprinklr_oauth.SPRINKLR_PROVIDER}


# ─────────────────────── Asana ───────────────────────
#
# OAuth connect ONLY for now: no KG puller (kickoff_sync no-ops until an
# `asana` entry lands in kg_ingest PULLERS) and no ticket-sync branch, so
# Asana never appears on the sync button (stories/sync.py SYNC_PROVIDERS).


@router.get("/asana/callback")
def asana_callback(code: str, state: str):
    payload = asana_oauth.verify_oauth_state(state)
    company_id = payload["company_id"]
    token_json = asana_oauth.exchange_code_for_token(code)
    access_token = token_json.get("access_token")
    if not access_token:
        raise HTTPException(400, "Asana did not return an access_token")

    # The token response embeds the authorizing user ({gid, name, email});
    # fall back to a users/me call only when it's absent. Best-effort — a
    # missing label must not fail the connect.
    info = token_json.get("data")
    if not isinstance(info, dict) or not info:
        info = asana_oauth.fetch_authenticated_user(access_token)
    label = (info.get("email") or info.get("name") or "") if info else ""

    try:
        token_encrypted = encrypt_token_json(
            asana_oauth.token_payload_to_store(token_json)
        )
    except TokenEncryptionError as e:
        raise HTTPException(500, str(e)) from e

    db.upsert_connection(
        company_id=company_id,
        provider=asana_oauth.ASANA_PROVIDER,
        token_encrypted=token_encrypted,
        scopes=settings.asana_scopes,
        account_label=label or None,
        config_json=json.dumps({"info": info}) if info else "{}",
    )

    kickoff_sync(company_id, asana_oauth.ASANA_PROVIDER)

    return _build_post_oauth_redirect(payload, asana_oauth.ASANA_PROVIDER)


@router.delete("/asana")
def asana_disconnect(
    company: CompanyContext = Depends(require_company),
):
    _require_admin_for_org_connector(company, asana_oauth.ASANA_PROVIDER)
    row = db.get_connection(company.company_id, asana_oauth.ASANA_PROVIDER)
    if not row:
        raise HTTPException(404, "Asana is not connected")
    db.delete_connection(company.company_id, asana_oauth.ASANA_PROVIDER)
    return {"deleted": True, "provider": asana_oauth.ASANA_PROVIDER}


class HubSpotSyncCorpusIn(BaseModel):
    dataset: str


@router.post("/hubspot/sync")
def hubspot_sync(
    body: HubSpotSyncCorpusIn,
    company: CompanyContext = Depends(require_company),
):
    """Sync HubSpot CRM data (contacts, companies, deals) into the corpus.

    Fetches data from HubSpot API, converts to markdown, and writes
    into DATA_DIR/{dataset}/ so it enters the knowledge base. Company-scoped:
    uses the caller's company's HubSpot connection only, and writes only into
    a dataset the caller's company owns.
    """
    dataset = _gate_dataset(body.dataset, company.company_id)
    _require_admin_for_org_connector(company, hubspot_oauth.HUBSPOT_PROVIDER)
    from app.connectors.hubspot_sync import HubSpotSyncError, sync_hubspot

    try:
        result = sync_hubspot(dataset, company_id=company.company_id)
    except HubSpotSyncError as e:
        raise HTTPException(400, str(e)) from e
    return result.to_dict()


@router.post("/hubspot/sync-to-corpus")
def hubspot_sync_to_corpus(
    body: HubSpotSyncCorpusIn,
    company: CompanyContext = Depends(require_company),
):
    """Alias for /hubspot/sync — matches Figma/GitHub sync-to-corpus pattern."""
    dataset = _gate_dataset(body.dataset, company.company_id)
    _require_admin_for_org_connector(company, hubspot_oauth.HUBSPOT_PROVIDER)
    from app.connectors.hubspot_sync import HubSpotSyncError, sync_hubspot

    try:
        result = sync_hubspot(dataset, company_id=company.company_id)
    except HubSpotSyncError as e:
        raise HTTPException(400, str(e)) from e
    return result.to_dict()


# ─────────────────────── Slack ───────────────────────
#
# Slack v2 bot install: token is the bot token (xoxb-...), stored
# encrypted. OAuth callback + disconnect. start-oauth is handled by the
# generic POST /{provider}/start-oauth above; helpers live in slack_oauth.py.
# The "Slack as notification target" use case posts into a user-chosen
# channel using `chat.postMessage` — that lives in slack_oauth.post_message.


@router.get("/slack/callback")
def slack_callback(code: str, state: str):
    payload = slack_oauth.verify_oauth_state(state)
    company_id = payload["company_id"]
    # Slack is per-user — the owning user rides in the signed state (the
    # callback has no session). verify_oauth_state guarantees it's present.
    user_id = payload["user_id"]
    token_json = slack_oauth.exchange_code_for_token(code)

    access_token = token_json.get("access_token")
    if not access_token:
        raise HTTPException(400, "Slack did not return a bot access_token")

    auth_info = slack_oauth.fetch_auth_test(access_token)
    team = token_json.get("team") or {}
    # Display "Acme (acme.slack.com)" when domain is present, else just team name.
    label = (
        auth_info.get("user")
        or team.get("name")
        or team.get("id")
        or str(token_json.get("bot_user_id") or "")
        or "Slack"
    )

    try:
        token_encrypted = encrypt_token_json(
            slack_oauth.token_payload_to_store(token_json)
        )
    except TokenEncryptionError as e:
        raise HTTPException(500, str(e)) from e

    db.upsert_slack_connection(
        company_id=company_id,
        user_id=user_id,
        token_encrypted=token_encrypted,
        scopes=token_json.get("scope") or "",
        account_label=str(label),
        config_json=json.dumps({
            "team": team,
            "bot_user_id": token_json.get("bot_user_id"),
        }),
    )

    # Populate the KG immediately, like every other connector callback — but
    # via the corpus path, not kickoff_sync. Slack has no kg_ingest PULLERS
    # entry, so `kickoff_sync(company_id, "slack")` is a documented no-op
    # (auto_sync.kickoff_sync); its ingest is sync_slack → corpus → seed.
    # Until this call existed the ONLY trigger was the 6-hourly connector
    # refresh (scheduler._refresh_all_company_connectors), so a brief asked for
    # right after connecting Slack synthesized zero Slack signal and then
    # silently started working an interval later.
    #
    # Company-level on purpose: the row is per-user, but voice-of-customer
    # pulling resolves the company's sync row + its shared pull-channel
    # selection (slack_company.resolve_company_slack_row), the same contract
    # the scheduler and the manual Sync button already use.
    #
    # Guarded even though the callee swallows its own failures: its lazy
    # `from app.connectors.slack_company import ...` sits outside that guard,
    # and the connection is already committed above — a raise here would 500 a
    # connect that in fact succeeded, stranding the user on an error page with
    # Slack connected.
    try:
        kickoff_slack_corpus_sync(company_id)
    except Exception:  # noqa: BLE001 — a sync kickoff must never fail a connect
        logger.warning(
            "slack: corpus-sync kickoff failed after connect for %s",
            company_id, exc_info=True,
        )

    return _build_post_oauth_redirect(payload, slack_oauth.SLACK_PROVIDER)


@router.delete("/slack")
def slack_disconnect(
    company: CompanyContext = Depends(require_company),
):
    # Disconnect only THIS user's Slack — never another member's. That guard
    # is unconditional: it applies before we even look at role.
    row = db.get_slack_connection(company.company_id, company.user_id)
    owned_by_caller = row is not None

    # This member has no personal Slack row of their own. The only Slack
    # connection they can even SEE is the shared company sync row (see
    # _company_slack_row_sanitized / resolve_company_slack_row) — someone
    # else's install doing double duty as the company's voice-of-customer
    # source — or a pre-per-user-migration orphan (user_id IS NULL). Both
    # are workspace-scoped, not personal, so an admin/owner may tear them
    # down on the company's behalf. A non-admin member gets the same 404 as
    # before: they may never reach, let alone delete, another member's
    # personal connection.
    if row is None and company.role in ("owner", "admin"):
        from app.connectors.slack_company import resolve_company_slack_row

        row = resolve_company_slack_row(company.company_id) \
            or db.get_orphan_slack_connection(company.company_id)

    if not row:
        raise HTTPException(404, "Slack is not connected")

    # Revoke the token on Slack's side first (best-effort), so the install is
    # torn down for the workspace, not just deleted locally — Slack Marketplace
    # expects a clean uninstall. A revoke failure must not block the local
    # delete — the whole point of this path is recovering a connection whose
    # credential is already dead.
    try:
        token_json = json.loads(decrypt_token_json(row["token_json_encrypted"]))
        bot_token = token_json.get("access_token")
        if bot_token:
            slack_oauth.revoke_token(bot_token)
    except Exception:  # noqa: BLE001 — never let revoke block the disconnect
        logger.warning("Slack token revoke on disconnect failed", exc_info=True)

    if owned_by_caller:
        db.delete_slack_connection(company.company_id, company.user_id)
    else:
        db.delete_slack_connection_by_id(company.company_id, row["id"])
    return {"deleted": True, "provider": slack_oauth.SLACK_PROVIDER}


# Strong refs to in-flight Slack reply tasks so the event loop doesn't GC a
# bare create_task() mid-run (same pattern as routes/ask.py _inflight_tasks).
_slack_inflight_tasks: set[asyncio.Task] = set()


@router.post("/slack/events")
async def slack_events(request: Request):
    """Slack Events API webhook. Unauthenticated by design — Slack calls it
    directly and the signing-secret request signature is the auth. Handles the
    url_verification handshake, app_uninstalled (tear down the workspace's
    connections — clean uninstall for Marketplace), and app_home_opened
    (publish the App Home view). Always returns 200 fast so Slack won't retry."""
    raw = await request.body()
    ts = request.headers.get("X-Slack-Request-Timestamp", "")
    sig = request.headers.get("X-Slack-Signature", "")
    if not slack_oauth.verify_signature(ts, raw, sig):
        raise HTTPException(401, "invalid Slack signature")
    try:
        payload = json.loads(raw)
    except json.JSONDecodeError as e:
        raise HTTPException(400, "invalid JSON body") from e

    if payload.get("type") == "url_verification":
        return {"challenge": payload.get("challenge", "")}

    if payload.get("type") == "event_callback":
        event = payload.get("event") or {}
        etype = event.get("type")
        team_id = payload.get("team_id") or ""
        if etype == "app_uninstalled":
            for row in db.list_slack_connections_by_team(team_id):
                try:
                    db.delete_slack_connection(row["company_id"], row["user_id"])
                except Exception:  # noqa: BLE001 — one failure shouldn't stop teardown
                    logger.warning("app_uninstalled: delete failed for a conn", exc_info=True)
            logger.info("slack app_uninstalled: team=%s torn down", team_id)
        elif etype == "app_home_opened":
            slack_user = event.get("user") or ""
            conns = db.list_slack_connections_by_team(team_id)
            if conns and slack_user:
                try:
                    token_json = json.loads(decrypt_token_json(conns[0]["token_json_encrypted"]))
                    bot_token = token_json.get("access_token")
                    if bot_token:
                        slack_oauth.publish_app_home(bot_token, slack_user)
                except Exception:  # noqa: BLE001 — best-effort App Home
                    logger.warning("app_home_opened: publish failed", exc_info=True)
        elif etype in ("message", "app_mention"):
            # Inbound user message → run the Q&A agent and reply in Slack.
            # Slack retries the webhook if we don't 200 within ~3s; the first
            # attempt is already processing in the background, so never
            # reprocess a retry (it would double-answer).
            if request.headers.get("X-Slack-Retry-Num"):
                return {"ok": True}
            # Only plain user messages start a turn. Skip bot posts (our own
            # replies carry bot_id — the primary reply-loop guard) and any
            # subtype (edits, deletes, joins, channel_topic, …).
            if event.get("bot_id") or event.get("subtype"):
                return {"ok": True}
            text = (event.get("text") or "").strip()
            channel = event.get("channel") or ""
            slack_user = event.get("user") or ""
            if not (text and channel and slack_user):
                return {"ok": True}
            # app_mention answers thread under the mention; DMs stay flat.
            thread_ts = (
                (event.get("thread_ts") or event.get("ts"))
                if etype == "app_mention"
                else None
            )
            coro = _handle_slack_message(
                team_id=team_id,
                slack_user=slack_user,
                channel=channel,
                text=text,
                is_mention=(etype == "app_mention"),
                thread_ts=thread_ts,
            )
            # Under pytest the TestClient event loop doesn't persist between
            # requests, so a fire-and-forget task would never run — await inline
            # for deterministic tests (mirrors routes/ask.py). Production keeps
            # the non-blocking create_task path so the webhook returns fast.
            if "pytest" in sys.modules:
                await coro
            else:
                task = asyncio.create_task(coro)
                _slack_inflight_tasks.add(task)
                task.add_done_callback(_slack_inflight_tasks.discard)

    return {"ok": True}


async def _handle_slack_message(
    *,
    team_id: str,
    slack_user: str,
    channel: str,
    text: str,
    is_mention: bool,
    thread_ts: str | None,
) -> None:
    """Resolve an inbound Slack message to a Sprntly company, run the Q&A agent
    over it (multi-turn for DMs, using the conversation's recent history), and
    post the answer back to the same channel/DM. Best-effort: every failure is
    logged and swallowed so one bad event can never crash the webhook task."""
    from app import qa_agent
    from app.db.companies import slug_for_company_id

    marker: str | None = None
    try:
        resolved = _resolve_slack_inbound(team_id, slack_user)
        if not resolved:
            logger.info("slack inbound: no connection for team=%s — ignoring", team_id)
            return
        company_id, _user_id, bot_token, bot_user_id, scopes = resolved
        # Defence-in-depth self-message guard (bot_id is checked at the webhook;
        # this also catches the rare bot_id-less self post).
        if bot_user_id and slack_user == bot_user_id:
            return
        question = (_strip_leading_mention(text) if is_mention else text).strip()
        if not question:
            return
        dataset = slug_for_company_id(company_id)
        if not dataset:
            logger.warning("slack inbound: no dataset slug for company=%s", company_id)
            return
        # Multi-turn only for DMs, where the flat channel history IS the
        # conversation. Channel @mentions answer single-turn (reading channel
        # history would feed unrelated chatter to the agent as context).
        history = (
            _slack_conversation_history(bot_token, channel, bot_user_id)
            if not is_mention
            else []
        )
        # A report ask takes MINUTES (staged web research), and Slack gives no
        # typing indicator to a bot. Without an up-front acknowledgement the
        # channel just goes quiet and the user asks again, which starts a second
        # run. Ack first, with the duration, then run.
        if is_competitive_report_request(question):
            marker = _register_slack_report(
                team_id=team_id, channel=channel, thread_ts=thread_ts,
                question=question,
            )
            await _post_best_effort(
                bot_token, channel=channel, thread_ts=thread_ts,
                text=_REPORT_ACK,
            )
        payload = await asyncio.to_thread(
            qa_agent.answer,
            enterprise_id=company_id,
            question=question,
            dataset=dataset,
            history=history,
        )
        answer_text = (payload.get("answer") or "").strip()
        if not answer_text:
            answer_text = (
                "I couldn't find an answer to that one. Try rephrasing, or ask "
                "me something about your product data."
            )
        await _deliver_slack_answer(
            bot_token, channel=channel, thread_ts=thread_ts,
            answer_text=answer_text, skill=payload.get("_skill"), scopes=scopes,
        )
        # Analytics parity with the web Ask path (never fail the answer on this).
        try:
            from app.db import log_ask

            log_ask(
                question=question,
                answer=answer_text,
                citations=payload.get("citations", []),
            )
        except Exception:  # noqa: BLE001 — analytics logging is best-effort
            logger.exception("slack inbound: log_ask failed")
    except Exception:  # noqa: BLE001 — a webhook task must never crash the loop
        logger.exception("slack inbound: handler failed team=%s", team_id)
    finally:
        _clear_slack_report(marker)


def _resolve_slack_inbound(
    team_id: str, slack_user: str
) -> tuple[str, str, str, str, str] | None:
    """Map an inbound Slack (team_id, slack_user) to one Sprntly connection.

    Returns (company_id, user_id, bot_token, bot_user_id, scopes), or None if the
    team has no usable connection. The installing user's Slack id (authed_user_id)
    lives inside the encrypted token blob — not an indexed column — so we list
    the team's connections and prefer the one whose authed_user_id matches the
    messaging user; absent a match we fall back to the team's first connection
    (1 install = 1 company by design, so its bot token + company apply).

    `scopes` is the granted-scope string recorded at install time; report
    delivery reads it to decide between a file upload and a text fallback."""
    chosen: tuple[str, str, str, str, str] | None = None
    fallback: tuple[str, str, str, str, str] | None = None
    for row in db.list_slack_connections_by_team(team_id):
        try:
            tj = json.loads(decrypt_token_json(row["token_json_encrypted"]))
        except (TokenEncryptionError, json.JSONDecodeError):
            continue
        bot_token = tj.get("access_token")
        if not bot_token:
            continue
        cand = (
            row["company_id"],
            row["user_id"],
            bot_token,
            tj.get("bot_user_id") or "",
            row.get("scopes") or tj.get("scope") or "",
        )
        if fallback is None:
            fallback = cand
        if tj.get("authed_user_id") and tj.get("authed_user_id") == slack_user:
            chosen = cand
            break
    return chosen or fallback


_MENTION_RE = re.compile(r"^\s*<@[^>]+>\s*")


def _strip_leading_mention(text: str) -> str:
    """Drop the leading <@BOTID> token Slack prepends to app_mention text."""
    return _MENTION_RE.sub("", text, count=1)


def _slack_conversation_history(
    bot_token: str, channel: str, bot_user_id: str
) -> list[dict]:
    """Build a chronological [{role, content}] history for a DM so the agent can
    answer follow-ups. Slack itself is the store: read recent messages, map the
    bot's own posts to 'assistant', and drop the latest (it's the current
    question, passed separately). Best-effort — returns [] if it can't read."""
    try:
        data = slack_oauth.fetch_conversation_history(
            bot_token, channel=channel, limit=20
        )
    except Exception:  # noqa: BLE001 — history is a nicety, not required
        return []
    # conversations.history returns newest-first; reverse to chronological then
    # drop the final entry (the message that triggered this event).
    msgs = list(reversed(data.get("messages") or []))[:-1]
    history: list[dict] = []
    for m in msgs:
        if m.get("subtype"):
            continue
        content = (m.get("text") or "").strip()
        if not content:
            continue
        is_bot = bool(m.get("bot_id")) or (
            bool(bot_user_id) and m.get("user") == bot_user_id
        )
        history.append(
            {
                "role": "assistant" if is_bot else "user",
                # Clamped at construction (#949's per-turn clamp). The consuming
                # fold in qa_agent clamps too, but this path is the one that now
                # carries REPORT answers: a Slack DM thread can hold the bot's
                # own report summary, and before file delivery it could hold a
                # whole HTML document. Bounding it here means every downstream
                # fold — qa_agent's and every divert's — sees a sane turn.
                "content": clamp_turn_text(
                    content if is_bot else _strip_leading_mention(content)
                ),
            }
        )
    return history[-12:]


# ───── Report delivery into Slack (ack → summary → file) ─────

CIR_SKILL = "competitive-intelligence-review"

# Posted BEFORE the run starts. Names the duration, because the alternative is a
# silent channel and a second ask that starts a second multi-minute run.
_REPORT_ACK = (
    ":mag: On it — running your competitive scan now. This takes about 5-10 "
    "minutes (a full quarterly review can take 10-20), because I research each "
    "competitor on the live web rather than answering from memory. I'll post "
    "the report here when it's ready."
)

_REPORT_FILENAME = {
    CIR_SKILL: "competitive-intelligence-report.html",
    "public-feedback-report": "public-feedback-report.html",
    "voice-of-customer-report": "voice-of-customer-report.html",
}
_REPORT_TITLE = {
    CIR_SKILL: "Competitive Intelligence report",
    "public-feedback-report": "Public Feedback report",
    "voice-of-customer-report": "Voice of Customer report",
}
# Shown when the install predates file delivery (no files:write). Adding a scope
# forces a workspace reinstall, which is the user's call — so we say where the
# report is instead of failing.
_NO_UPLOAD_SCOPE = (
    "The full report is a formatted document, and this Slack install can't "
    "receive file uploads yet — open Sprntly chat to read it, or reconnect "
    "Slack from Settings → Connectors to enable file delivery here."
)
# ...and a DIFFERENT message when the scope IS present and the upload still
# failed. Telling someone their install "can't receive uploads yet" when it can
# sends them to reinstall Slack for nothing; this is a transient failure, and the
# honest line says so.
_UPLOAD_FAILED = (
    "I couldn't attach the full report here just now — open Sprntly chat to "
    "read it, or ask me again to retry the upload."
)


async def _post_best_effort(bot_token: str, *, channel: str, text: str,
                            thread_ts: str | None) -> bool:
    """post_message off the event loop, swallowing failures. Used for the ack and
    the fallbacks, where a delivery failure must not lose the answer that
    follows it."""
    try:
        await asyncio.to_thread(
            slack_oauth.post_message, bot_token,
            channel=channel, text=text, thread_ts=thread_ts,
        )
        return True
    except Exception:  # noqa: BLE001 — one failed post can't break delivery
        logger.warning("slack: post failed for channel=%s", channel, exc_info=True)
        return False


async def _deliver_slack_answer(
    bot_token: str, *, channel: str, thread_ts: str | None, answer_text: str,
    skill: str | None, scopes: str,
) -> None:
    """Post an agent answer to Slack, handling the HTML-report case.

    A prose answer posts as-is (unchanged behaviour). An answer that IS a
    self-contained HTML document — the CIR / public-feedback / VoC reports —
    would post as a wall of CSS that Slack then truncates, so instead we post a
    short text summary read from the document's own opening and attach the
    document as a file. Missing `files:write` degrades to the summary plus a
    pointer to Sprntly chat; the answer is never lost.
    """
    if not html_report.looks_like_html_report(answer_text):
        await _post_best_effort(bot_token, channel=channel, text=answer_text,
                                thread_ts=thread_ts)
        return

    label = _REPORT_TITLE.get(skill or "", "report")
    summary = html_report.summarize_report(answer_text)
    header = f":page_facing_up: Your {label} is ready."
    await _post_best_effort(
        bot_token, channel=channel, thread_ts=thread_ts,
        text=f"{header}\n\n{summary}" if summary else header,
    )
    if not slack_oauth.has_file_upload_scope(scopes):
        await _post_best_effort(bot_token, channel=channel, thread_ts=thread_ts,
                                text=_NO_UPLOAD_SCOPE)
        return
    filename = _REPORT_FILENAME.get(skill or "", "sprntly-report.html")
    uploaded = False
    try:
        uploaded = await asyncio.to_thread(
            slack_oauth.upload_file, bot_token,
            channel=channel, filename=filename, content=answer_text,
            title=label, thread_ts=thread_ts,
        )
    except Exception:  # noqa: BLE001 — upload_file already swallows; belt and braces
        logger.warning("slack: report upload raised", exc_info=True)
    if not uploaded:
        # The scope IS present (checked above), so this is a transient upload
        # failure — do NOT tell them to reinstall Slack.
        await _post_best_effort(bot_token, channel=channel, thread_ts=thread_ts,
                                text=_UPLOAD_FAILED)


# ───── In-flight report markers (shutdown interrupt) ─────
#
# A Slack report run lives in a fire-and-forget task, so a restart mid-run drops
# it silently: the user got an ack promising a report in ~5-10 minutes and then
# nothing, forever. These markers let `sweep_interrupted_slack_reports` say so.
#
# The sweep is called from the lifespan's SHUTDOWN half (app/main.py), not
# startup. That is load-bearing: this registry is in-process, so a fresh
# process's dict is empty by construction and a startup sweep could never fire.
# At shutdown the loop is still alive and the bot token still readable, so the
# notice actually goes out.
#
# KNOWN LIMIT: this covers an orderly shutdown (SIGTERM on deploy/restart) and
# a task lost inside one process lifetime. A SIGKILL or a hard crash skips the
# lifespan entirely and the markers die with the process. Durable markers need a
# table and a migration; the sweep is already wired, so making it durable later
# is a schema change rather than a rewrite.
_slack_report_markers: dict[str, dict] = {}
# Marker keys must be unique for the lifetime of the process. len(dict) is not:
# register → clear → register reuses the same suffix, and two concurrent report
# runs in one channel/thread could collide and lose a marker. A monotonic
# counter cannot.
_slack_report_seq = itertools.count(1)
_INTERRUPTED_REPORT = (
    ":warning: That report run was interrupted before it finished — nothing was "
    "posted. Ask again and I'll rerun it."
)


def _register_slack_report(*, team_id: str, channel: str,
                           thread_ts: str | None, question: str) -> str:
    key = f"{team_id}:{channel}:{thread_ts or ''}:{next(_slack_report_seq)}"
    _slack_report_markers[key] = {
        "team_id": team_id, "channel": channel, "thread_ts": thread_ts,
        "question": question, "started_at": time.time(),
    }
    return key


def _clear_slack_report(marker: str | None) -> None:
    if marker:
        _slack_report_markers.pop(marker, None)


def sweep_interrupted_slack_reports() -> list[dict]:
    """Post "interrupted — ask again" for every report run still marked
    in-flight, and clear the markers. Returns the markers it swept (so the
    caller can log a count). Best-effort per marker: a failed post is logged and
    the sweep continues.

    Called from the lifespan SHUTDOWN half — see the note above the registry for
    why it cannot be startup."""
    swept = list(_slack_report_markers.values())
    _slack_report_markers.clear()
    for m in swept:
        resolved = None
        try:
            resolved = _resolve_slack_inbound(m.get("team_id") or "", "")
        except Exception:  # noqa: BLE001 — a sweep must never break startup
            logger.warning("slack sweep: connection lookup failed", exc_info=True)
        if not resolved:
            continue
        try:
            slack_oauth.post_message(
                resolved[2], channel=m.get("channel") or "",
                text=_INTERRUPTED_REPORT, thread_ts=m.get("thread_ts"),
            )
        except Exception:  # noqa: BLE001
            logger.warning("slack sweep: retry notice post failed", exc_info=True)
    return swept


# ───── Slash commands ─────


@router.post("/slack/commands")
async def slack_commands(request: Request):
    """Slack slash-command sink. Unauthenticated by design — the signing-secret
    request signature is the auth, exactly as for /slack/events.

    Slack requires a response within 3 SECONDS or the user sees an operation
    timeout, and a competitive scan takes minutes. So this endpoint does no LLM
    work at all: it verifies the signature, returns an ephemeral ack
    immediately, and runs the report in a background task that delivers via the
    command's `response_url` plus an in-channel post (with the HTML document
    attached as a file).

    `/competitive-scan [competitor names…]` is the intended command; any
    competitor names in the text override the stored roster for that run. The
    endpoint accepts whatever command Slack sends, because ACTIVATING a slash
    command is a Slack app-manifest change made per app (prod and dev are
    SEPARATE apps) — deliberately NOT part of this code. Until the command is
    registered nothing reaches here, and this endpoint is simply idle; it never
    assumes registration."""
    raw = await request.body()
    ts = request.headers.get("X-Slack-Request-Timestamp", "")
    sig = request.headers.get("X-Slack-Signature", "")
    if not slack_oauth.verify_signature(ts, raw, sig):
        raise HTTPException(401, "invalid Slack signature")
    form = {k: v[0] for k, v in parse_qs(raw.decode("utf-8", "replace")).items()}
    command = (form.get("command") or "").strip()
    text = (form.get("text") or "").strip()
    team_id = (form.get("team_id") or "").strip()
    channel = (form.get("channel_id") or "").strip()
    slack_user = (form.get("user_id") or "").strip()
    response_url = (form.get("response_url") or "").strip()
    if not (team_id and channel):
        # Nothing actionable; still 200 so Slack doesn't show an error.
        return {"response_type": "ephemeral",
                "text": "I couldn't read that command — try again."}

    coro = _run_slack_report_command(
        team_id=team_id, channel=channel, slack_user=slack_user,
        text=text, command=command, response_url=response_url,
    )
    if "pytest" in sys.modules:
        # Same reason as /slack/events: the TestClient loop doesn't persist
        # between requests, so a fire-and-forget task would never run.
        await coro
    else:
        task = asyncio.create_task(coro)
        _slack_inflight_tasks.add(task)
        task.add_done_callback(_slack_inflight_tasks.discard)

    return {
        "response_type": "ephemeral",
        "text": _REPORT_ACK,
    }


def _command_question(text: str) -> str:
    """The question the command runs. Names in the command text override the
    stored roster for this run (they reach the pipeline through the question,
    which is where `named_competitors` reads an ad-hoc set from)."""
    names = text.strip()
    if not names:
        return "Run a competitive intelligence report"
    return f"Run a competitive intelligence report vs {names}"


async def _run_slack_report_command(
    *, team_id: str, channel: str, slack_user: str, text: str, command: str,
    response_url: str,
) -> None:
    """Background half of a slash command: run the report pinned to the CIR
    skill and deliver it. Best-effort throughout — a slash command must never
    crash the event loop, and every failure path still tells the user."""
    from app import qa_agent
    from app.db.companies import slug_for_company_id

    marker: str | None = None
    try:
        resolved = _resolve_slack_inbound(team_id, slack_user)
        if not resolved:
            await _respond_to_command(
                response_url,
                "Sprntly isn't connected to this workspace yet — connect Slack "
                "from Settings → Connectors and try again.",
            )
            return
        company_id, _user_id, bot_token, _bot_user_id, scopes = resolved
        dataset = slug_for_company_id(company_id) or ""
        question = _command_question(text)
        marker = _register_slack_report(
            team_id=team_id, channel=channel, thread_ts=None, question=question,
        )
        payload = await asyncio.to_thread(
            qa_agent.answer,
            enterprise_id=company_id,
            question=question,
            dataset=dataset,
            pinned_skill=CIR_SKILL,
        )
        answer_text = (payload.get("answer") or "").strip()
        if not answer_text:
            await _respond_to_command(
                response_url,
                "I couldn't complete that competitive scan. Please try again.",
            )
            return
        await _deliver_slack_answer(
            bot_token, channel=channel, thread_ts=None,
            answer_text=answer_text, skill=payload.get("_skill") or CIR_SKILL,
            scopes=scopes,
        )
        await _respond_to_command(response_url, "Your report is posted above. :white_check_mark:")
    except Exception:  # noqa: BLE001 — a background task must never crash the loop
        logger.exception("slack command failed: %s team=%s", command, team_id)
        await _respond_to_command(
            response_url,
            "Something went wrong running that competitive scan. Please try again.",
        )
    finally:
        _clear_slack_report(marker)


async def _respond_to_command(response_url: str, text: str) -> None:
    """Post a follow-up to a slash command's `response_url` (valid for 30
    minutes, 5 uses). Ephemeral so a long report's progress chatter doesn't
    clutter the channel. No-op without a URL; never raises."""
    if not response_url:
        return

    def _post() -> None:
        requests.post(
            response_url,
            json={"response_type": "ephemeral", "text": text},
            timeout=15,
        )

    try:
        await asyncio.to_thread(_post)
    except Exception:  # noqa: BLE001 — the in-channel post is the real delivery
        logger.warning("slack command response_url post failed", exc_info=True)


def _slack_token_json(company_id: str, user_id: str) -> tuple[dict, dict]:
    """Decrypt and return (token_json, connection_row) for THIS user's own
    Slack connection. 404 if not connected, 500 if unreadable. token_json
    holds both the bot token (access_token) and, when the install granted
    user scopes, the user token (user_access_token)."""
    row = db.get_slack_connection(company_id, user_id)
    if not row:
        raise HTTPException(404, "Slack is not connected")
    try:
        token_json = json.loads(decrypt_token_json(row["token_json_encrypted"]))
    except (TokenEncryptionError, json.JSONDecodeError) as e:
        raise HTTPException(500, "Slack token unreadable") from e
    return token_json, row


def _slack_bot_token(company_id: str, user_id: str) -> tuple[str, dict]:
    """Decrypt and return (bot_token, connection_row) for THIS user's own
    Slack connection. 404 if not connected, 500 if the token is unreadable."""
    token_json, row = _slack_token_json(company_id, user_id)
    bot_token = token_json.get("access_token")
    if not bot_token:
        raise HTTPException(500, "Slack token has no bot access_token")
    return bot_token, row


def _slack_user_token(company_id: str, user_id: str) -> tuple[str, dict]:
    """Decrypt and return (user_token, connection_row) for THIS user's own
    Slack connection. 404 if not connected, 500 if unreadable, 400 if the
    install was bot-only (no user token granted — reconnect to grant the
    read-as-user scopes)."""
    token_json, row = _slack_token_json(company_id, user_id)
    user_token = token_json.get("user_access_token")
    if not user_token:
        raise HTTPException(
            400,
            "Slack is connected without read-as-user access — reconnect Slack "
            "to grant it.",
        )
    return user_token, row


# Slack is OAuth-only. The legacy bot-token (xoxb-) paste connect path was
# removed — Slack Marketplace requires OAuth install ("Add to Slack"), not a
# pasted token, so no /slack/apikey endpoint exists for a reviewer to flag.


@router.get("/slack/channels")
def slack_list_channels(
    company: CompanyContext = Depends(require_company),
):
    """List channels the bot can post into. Backs both channel pickers
    (delivery target + pull channels). Resolves THIS user's own Slack,
    falling back to the COMPANY's sync connection so members who never
    installed their own bot can still see the shared pull selection —
    channel names only, never another member's delivery config."""
    from app.connectors.slack_company import (
        CompanySlackError,
        company_slack_token,
    )

    if db.get_slack_connection(company.company_id, company.user_id):
        token, _row = _slack_bot_token(company.company_id, company.user_id)
        return {"channels": slack_oauth.list_channels(token)}
    try:
        resolved = company_slack_token(company.company_id)
    except CompanySlackError as e:
        raise HTTPException(500, str(e)) from e
    if not resolved:
        raise HTTPException(404, "Slack is not connected")
    token, _row = resolved
    return {"channels": slack_oauth.list_channels(token)}


class SlackDmIn(BaseModel):
    text: str

    def model_post_init(self, _context) -> None:
        if not self.text or not self.text.strip():
            raise ValueError("text cannot be empty")


@router.post("/slack/dm")
def slack_dm_user(
    body: SlackDmIn,
    company: CompanyContext = Depends(require_company),
):
    """Send a direct message to THIS user via Slack (Sprntly → user's DM).

    Uses the bot token to open a DM with the connection's own installing
    user (authed_user_id captured at OAuth time) and post to it. Needs the
    `im:write` + `chat:write` bot scopes."""
    token_json, _row = _slack_token_json(company.company_id, company.user_id)
    bot_token = token_json.get("access_token")
    if not bot_token:
        raise HTTPException(500, "Slack token has no bot access_token")
    target = token_json.get("authed_user_id")
    if not target:
        raise HTTPException(
            400,
            "Slack connection has no user to DM — reconnect Slack.",
        )
    result = slack_oauth.post_dm_to_user(
        bot_token, slack_user_id=target, text=body.text.strip()
    )
    return {"ok": True, "ts": result.get("ts"), "channel": result.get("channel")}


@router.get("/slack/history")
def slack_history(
    channel: str,
    limit: int = 100,
    oldest: str | None = None,
    latest: str | None = None,
    cursor: str | None = None,
    company: CompanyContext = Depends(require_company),
):
    """Read messages from a Slack channel/DM (user's Slack → Sprntly).

    Reads as the user (xoxp) so it can reach the user's own DMs and private
    channels; requires Slack connected with read-as-user access."""
    user_token, _row = _slack_user_token(company.company_id, company.user_id)
    return slack_oauth.fetch_conversation_history(
        user_token,
        channel=channel,
        limit=limit,
        oldest=oldest,
        latest=latest,
        cursor=cursor,
    )


@router.get("/slack/search")
def slack_search(
    q: str,
    count: int = 20,
    page: int = 1,
    company: CompanyContext = Depends(require_company),
):
    """Search the user's own Slack content (user's Slack → Sprntly).

    Uses the user token (xoxp) + `search:read`; spans everything the
    authorizing user can see."""
    user_token, _row = _slack_user_token(company.company_id, company.user_id)
    return slack_oauth.search_messages(user_token, query=q, count=count, page=page)


class SlackConfigIn(BaseModel):
    # "channel" (post to channel_id) or "dm" (DM the installing user).
    target_type: str = slack_oauth.TARGET_CHANNEL
    channel_id: str | None = None
    channel_name: str | None = None

    def model_post_init(self, _context) -> None:
        self.target_type = (self.target_type or slack_oauth.TARGET_CHANNEL).strip()
        if self.target_type not in (slack_oauth.TARGET_CHANNEL, slack_oauth.TARGET_DM):
            raise ValueError("target_type must be 'channel' or 'dm'")
        # A channel target needs a channel; a DM target ignores channel fields.
        if self.target_type == slack_oauth.TARGET_CHANNEL and not (
            self.channel_id or "").strip():
            raise ValueError("channel_id is required when target_type is 'channel'")


@router.post("/slack/config")
def slack_save_config(
    body: SlackConfigIn,
    company: CompanyContext = Depends(require_company),
):
    """Save the user's notification target — either a channel, or a DM to
    themselves. Stored on THIS user's own Slack connection row's config so
    the brief-delivery + nudge paths can read it at post-time.

    For a channel target, best-effort self-joins the chosen public channel
    right away (idempotent, needs `channels:join`) so the very first brief
    posts cleanly instead of failing not_in_channel. Private channels can't be
    self-joined — `joined` comes back False and the UI can prompt the user to
    /invite the bot. DM targets never need a join, so `joined` stays False."""
    row = db.get_slack_connection(company.company_id, company.user_id)
    if not row:
        raise HTTPException(404, "Slack is not connected")
    is_dm = body.target_type == slack_oauth.TARGET_DM
    channel_id = "" if is_dm else (body.channel_id or "").strip()
    patch: dict = {"target_type": body.target_type, "channel_id": channel_id}
    if not is_dm and body.channel_name:
        patch["channel_name"] = body.channel_name.strip()
    updated = db.patch_slack_connection_config(
        company.company_id, company.user_id, patch
    )
    config: dict = {}
    if updated:
        try:
            config = json.loads(updated.get("config_json") or "{}")
        except (TypeError, ValueError):
            config = {}
    joined = False
    if not is_dm:
        try:
            bot_token, _row = _slack_bot_token(company.company_id, company.user_id)
            joined = slack_oauth.join_channel(bot_token, channel_id)
        except Exception:  # noqa: BLE001 — join is best-effort; never block save
            logger.exception("slack auto-join on config save failed")
    return {"ok": True, "config": config, "joined": joined}


class SlackSyncChannelIn(BaseModel):
    id: str
    # Display name, stored alongside the id so an unjoined channel can be
    # reported by name at sync time.
    name: str | None = None

    def model_post_init(self, _context) -> None:
        self.id = (self.id or "").strip()
        if not self.id:
            raise ValueError("channel id cannot be empty")


class SlackSyncChannelsIn(BaseModel):
    channels: list[SlackSyncChannelIn]

    def model_post_init(self, _context) -> None:
        # The sync caps at 50 channels (slack_sync.MAX_CHANNELS) — refuse a
        # selection it could never honor.
        if len(self.channels) > 50:
            raise ValueError("select at most 50 channels")


def _slack_delivery_channel_ids(company_id: str) -> set[str] | None:
    """Every channel this company currently DELIVERS to over Slack, or None
    when the lookup itself failed.

    Delivery is per-user (each member picks their own target — see
    connectors/slack_company.py), so this reads every member's Slack row, not
    just the company sync row. A channel in this set must never be left even
    when it is unticked in the pull picker: the bot has to stay a member to
    post there, and `post_to_target` only re-joins PUBLIC channels — walking
    out of a private delivery channel would silently break that member's
    briefs, nudges and design-agent notifications with no way back but a
    manual /invite.

    Matches `slack_oauth.post_to_target`'s own reading of the config, including
    its default: an absent `target_type` means "channel", so a row carrying
    only `channel_id` still counts.

    Returns None — NOT an empty set — when the connection lookup blows up. The
    two mean opposite things to the caller: empty is "nothing is a delivery
    target, every leave is safe", None is "we don't know", and the only safe
    response to not knowing is to leave nothing at all. An empty set here would
    turn a transient DB hiccup into a silently broken brief channel.
    """
    from app.connectors.slack_company import row_config

    targets: set[str] = set()
    try:
        rows = db.list_slack_connections(company_id)
    except Exception:  # noqa: BLE001 — unknown targets ⇒ skip every leave
        logger.exception("slack delivery-target lookup failed for %s", company_id)
        return None
    for row in rows:
        cfg = row_config(row)
        target_type = (
            cfg.get("target_type") or slack_oauth.TARGET_CHANNEL
        ).strip()
        if target_type != slack_oauth.TARGET_CHANNEL:
            continue
        channel_id = str(cfg.get("channel_id") or "").strip()
        if channel_id:
            targets.add(channel_id)
    return targets


@router.post("/slack/sync-channels")
def slack_save_sync_channels(
    body: SlackSyncChannelsIn,
    company: CompanyContext = Depends(require_company),
):
    """Save which channels the Slack corpus sync pulls from — COMPANY-WIDE.

    Voice-of-customer pulling is company-level (one selection, one scheduled
    sync per company — see slack_company.py), so the selection is stored on
    the company's Slack sync connection, whichever member installed it, and
    only admins may change it. An empty list clears the selection (back to
    every channel the bot is a member of). Selected public channels are
    best-effort self-joined right away (idempotent, `channels:join`) so the
    first sync doesn't skip them as not_in_channel; private channels can't
    be self-joined and need a manual /invite.

    UNTICKING REVERSES TICKING. A channel dropped from the selection is
    diffed out of the PREVIOUS selection and then undone in both directions
    ticking it went: the bot leaves the channel in Slack
    (`slack_oauth.leave_channel`), and the messages that channel already
    contributed are stripped out of the company's synced corpus
    (`slack_sync.purge_channels_from_synced_data`), which then re-seeds the KG
    the way an ordinary sync does. Two rules bound that:

      * A channel that is somebody's DELIVERY target is never left, only
        purged — see `_slack_delivery_channel_ids`.
      * Every leave and every purge is best-effort. The selection write above
        them is the source of truth and has already committed; cleanup that
        fails is logged and reported in the response, never turned into an
        error the admin's save disappears into.
    """
    from app.connectors.slack_company import (
        CompanySlackError,
        company_slack_token,
        resolve_company_slack_row,
        row_config,
    )
    from app.connectors.slack_sync import (
        CONFIG_SYNC_CHANNEL_IDS,
        CONFIG_SYNC_CHANNEL_NAMES,
        purge_channels_from_synced_data,
    )

    # Company-wide config → org-connector RBAC, even though the underlying
    # connection row is a member's install.
    if company.role not in ("owner", "admin"):
        raise HTTPException(
            403,
            "Only admins can choose the channels Sprntly pulls from. "
            "Ask your workspace admin to update the selection.",
        )
    row = resolve_company_slack_row(company.company_id)
    if not row:
        raise HTTPException(404, "Slack is not connected")
    # Snapshot the OUTGOING selection before the patch overwrites it — this is
    # the only moment both halves of the diff exist. The names map matters as
    # much as the ids: the corpus keys its per-channel sections by channel NAME
    # (`## #support`), and the new payload no longer carries a name for a
    # channel that was just unticked.
    previous = row_config(row)
    previous_ids = [
        str(cid) for cid in (previous.get(CONFIG_SYNC_CHANNEL_IDS) or []) if cid
    ]
    previous_names = previous.get(CONFIG_SYNC_CHANNEL_NAMES)
    if not isinstance(previous_names, dict):
        # A hand-edited or pre-picker config can carry anything here; a bad
        # names map must degrade to "no name known", not to a 500 on save.
        previous_names = {}
    # Dedupe preserving order — the sync pulls in selection order.
    ids = list(dict.fromkeys(c.id for c in body.channels))
    names = {
        c.id: c.name.strip()
        for c in body.channels
        if c.name and c.name.strip()
    }
    updated = db.patch_slack_connection_config(
        company.company_id,
        row.get("user_id") or "",
        {CONFIG_SYNC_CHANNEL_IDS: ids, CONFIG_SYNC_CHANNEL_NAMES: names},
    )
    config = row_config(updated) if updated else {}
    joined: list[str] = []
    if ids:
        try:
            resolved = company_slack_token(company.company_id)
            if resolved:
                bot_token, _row = resolved
                for cid in ids:
                    try:
                        if slack_oauth.join_channel(bot_token, cid):
                            joined.append(cid)
                    except Exception:  # noqa: BLE001 — join is best-effort per channel
                        logger.exception("slack auto-join failed for %s", cid)
        except CompanySlackError:
            logger.exception("slack auto-join on sync-channels save failed")
        except Exception:  # noqa: BLE001 — join must never block the save
            logger.exception("slack auto-join on sync-channels save failed")

    # ── Unticked channels: leave in Slack, then un-sync their messages ──
    #
    # An empty incoming list is "clear the selection back to every bot-member
    # channel", NOT "unsubscribe from everything the bot is in" — clearing
    # widens what the sync reads, so nothing is removed and nothing is left.
    selected = set(ids)
    removed_ids = (
        [cid for cid in previous_ids if cid not in selected] if ids else []
    )
    left: list[str] = []
    leave_failed: list[str] = []
    delivery_skipped: list[str] = []
    purged: dict = {"datasets": [], "sections_removed": 0, "reseeded": []}

    if removed_ids:
        delivery_targets = _slack_delivery_channel_ids(company.company_id)
        if delivery_targets is None:
            # Couldn't establish which channels deliver — skip every leave
            # rather than risk walking out of a brief channel.
            delivery_skipped = list(removed_ids)
            logger.warning(
                "slack un-sync: delivery targets unknown for company=%s — "
                "skipping all %d leave(s); data purge still runs",
                company.company_id, len(removed_ids),
            )
        else:
            try:
                resolved = company_slack_token(company.company_id)
            except CompanySlackError:
                resolved = None
                logger.exception("slack leave on sync-channels save failed")
            except Exception:  # noqa: BLE001 — never block the save
                resolved = None
                logger.exception("slack leave on sync-channels save failed")
            if resolved:
                bot_token, _leave_row = resolved
                for cid in removed_ids:
                    if cid in delivery_targets:
                        # Still purged below — the guard protects DELIVERY, not
                        # the corpus. The admin asked for this channel's
                        # messages to stop being read; the bot just has to stay
                        # in the room to keep posting there.
                        delivery_skipped.append(cid)
                        logger.info(
                            "slack un-sync: keeping bot in %s — it is a Slack "
                            "delivery target for this company", cid,
                        )
                        continue
                    try:
                        if slack_oauth.leave_channel(bot_token, cid):
                            left.append(cid)
                        else:
                            leave_failed.append(cid)
                    except Exception:  # noqa: BLE001 — per-channel isolation
                        leave_failed.append(cid)
                        logger.exception("slack leave failed for %s", cid)
            else:
                leave_failed = list(removed_ids)

        # Purge every removed channel's pulled messages, delivery targets
        # included. Names come from the OUTGOING map; a channel we never
        # stored a name for has no addressable corpus section, so it is
        # reported rather than guessed at.
        removed_names = [
            str(previous_names.get(cid) or "").strip() for cid in removed_ids
        ]
        purge_names = [n for n in removed_names if n]
        if len(purge_names) < len(removed_ids):
            logger.warning(
                "slack un-sync: %d of %d removed channels had no stored name — "
                "their corpus sections cannot be located and are left for the "
                "next full sync to overwrite",
                len(removed_ids) - len(purge_names), len(removed_ids),
            )
        try:
            purged = purge_channels_from_synced_data(
                company.company_id, purge_names
            )
        except Exception:  # noqa: BLE001 — cleanup must never fail the save
            logger.exception(
                "slack un-sync: purge failed for company=%s", company.company_id
            )

    return {
        "ok": True,
        "config": config,
        "joined": joined,
        "left": left,
        "leave_failed": leave_failed,
        "delivery_skipped": delivery_skipped,
        "purged": purged,
    }


class SlackSyncCorpusIn(BaseModel):
    dataset: str
    history_days: int = 90


@router.post("/slack/sync-to-corpus")
def slack_sync_to_corpus(
    body: SlackSyncCorpusIn,
    company: CompanyContext = Depends(require_company),
):
    """Sync Slack channels, messages, and threads into the corpus.

    Fetches data from the Slack API, converts to markdown, and writes
    into DATA_DIR/{dataset}/ so it enters the knowledge base. COMPANY-LEVEL:
    whoever clicks Sync, the sync uses the company's Slack connection and
    its shared pull-channel selection (see slack_company.py) — the same
    corpus every member reads. Also runs on the scheduled connector refresh.

    Slack is a `_PERSONAL_PROVIDERS` connector, so there is deliberately no
    admin gate here — any member can sync. That makes the dataset gate below
    the ONLY thing between an ordinary member and another tenant's corpus.
    """
    dataset = _gate_dataset(body.dataset, company.company_id)
    from app.connectors.slack_sync import SlackSyncError, sync_slack

    try:
        result = sync_slack(
            dataset,
            company_id=company.company_id,
            history_days=body.history_days,
        )
    except SlackSyncError as e:
        raise HTTPException(400, str(e)) from e
    _seed_corpus_after_sync(company.company_id, dataset)
    return result.to_dict()


# ─────────────────────── Fireflies (API key) ───────────────────────
#
# Commit J. Fireflies doesn't expose self-serve OAuth — auth is a user-
# issued API key (fireflies.ai → Settings → Integrations → Fireflies API).
# Per the Onboarding Spec line 150, "API key flow" is explicitly allowed
# alongside OAuth. The frontend collects the key in a modal and POSTs it
# here for validation + storage.


class FirefliesApiKeyIn(BaseModel):
    api_key: str

    def model_post_init(self, _context) -> None:
        if not self.api_key or not self.api_key.strip():
            raise ValueError("api_key cannot be empty")


@router.post("/fireflies/apikey")
def fireflies_connect_apikey(
    body: FirefliesApiKeyIn,
    company: CompanyContext = Depends(require_company),
):
    _require_admin_for_org_connector(company, fireflies_apikey.FIREFLIES_PROVIDER)
    api_key = body.api_key.strip()
    user = fireflies_apikey.fetch_authenticated_user(api_key)
    if not user:
        raise HTTPException(
            400,
            "Fireflies rejected this API key — double-check the value at "
            "fireflies.ai → Settings → Integrations → Fireflies API.",
        )

    label = user.get("email") or user.get("name") or "Fireflies user"

    try:
        token_encrypted = encrypt_token_json(
            fireflies_apikey.token_payload_to_store(api_key)
        )
    except TokenEncryptionError as e:
        raise HTTPException(500, str(e)) from e

    db.upsert_connection(
        company_id=company.company_id,
        provider=fireflies_apikey.FIREFLIES_PROVIDER,
        token_encrypted=token_encrypted,
        scopes="",
        account_label=label,
        config_json=json.dumps({"user": user}) if user else "{}",
    )

    kickoff_sync(company.company_id, fireflies_apikey.FIREFLIES_PROVIDER)
    # Populate the CALL INDEX at connect time too. kickoff_sync above fills the
    # KG (distilled summaries); this fills the metadata index chat answers call
    # listings from. Without it the index stays empty until the next 6-hourly
    # scheduler cycle, and an empty index is not an error anyone can see — every
    # interception in qa_agent just returns None and the question silently
    # degrades to the old expensive path. Same gap, same fix as d30ca7ee's
    # "sync Slack the moment it's connected, not six hours later".
    kickoff_call_index_sync(company.company_id)

    return {
        "ok": True,
        "provider": fireflies_apikey.FIREFLIES_PROVIDER,
        "account_label": label,
    }


@router.delete("/fireflies")
def fireflies_disconnect(
    company: CompanyContext = Depends(require_company),
):
    _require_admin_for_org_connector(company, fireflies_apikey.FIREFLIES_PROVIDER)
    row = db.get_connection(company.company_id, fireflies_apikey.FIREFLIES_PROVIDER)
    if not row:
        raise HTTPException(404, "Fireflies is not connected")
    db.delete_connection(company.company_id, fireflies_apikey.FIREFLIES_PROVIDER)
    # Drop the call index with the connection. Leaving it is NOT harmless: chat
    # would keep answering call questions from indexed rows while
    # prompts.connected_sources_line correctly reports that no transcript source
    # is connected — two contradictory claims inside one answer, with no way for
    # the reader to tell which half is wrong. Best-effort; call_index.ensure_fresh
    # independently refuses to serve once the source is gone, so a failure here
    # degrades to "no answer from the index" rather than to a stale one.
    try:
        from app import call_index

        # SCOPED to Fireflies. An unscoped wipe would also destroy a working
        # Zoom index — disconnect one tool, silently lose another tool's call
        # history — now that two sources populate this table.
        call_index.clear_company(
            company.company_id, call_index.PROVIDER_FIREFLIES
        )
    except Exception:  # noqa: BLE001 — a cleanup failure must not fail a disconnect
        logger.warning("fireflies: could not clear call index for %s",
                       company.company_id, exc_info=True)
    return {"deleted": True, "provider": fireflies_apikey.FIREFLIES_PROVIDER}


# ─────────────────────── Superset (credentials, not OAuth) ───────────────────
#
# Self-hosted BI: the user supplies their instance URL + a service-account
# login. We validate by logging in, store the triple encrypted, and every
# consumer re-logs-in on use (no token persistence — see superset_auth).


class SupersetConnectIn(BaseModel):
    base_url: str = Field(..., min_length=1)
    username: str = Field(..., min_length=1)
    password: str = Field(..., min_length=1)


@router.post("/superset/connect")
def superset_connect(
    body: SupersetConnectIn,
    company: CompanyContext = Depends(require_company),
):
    _require_admin_for_org_connector(company, superset_auth.SUPERSET_PROVIDER)
    try:
        base_url = superset_auth.normalize_base_url(body.base_url)
    except ValueError as e:
        raise HTTPException(422, str(e)) from e

    username = body.username.strip()
    try:
        tokens = superset_auth.login(base_url, username, body.password)
    except superset_auth.SupersetAuthError as e:
        raise HTTPException(400, str(e)) from e

    # Best-effort identity for the account label — /me failing must not
    # fail the connect (the login above already proved the credential).
    user = superset_auth.fetch_current_user(base_url, tokens["access_token"])
    label = user.get("email") or user.get("username") or username

    try:
        token_encrypted = encrypt_token_json(
            superset_auth.credential_to_store(base_url, username, body.password)
        )
    except TokenEncryptionError as e:
        raise HTTPException(500, str(e)) from e

    db.upsert_connection(
        company_id=company.company_id,
        provider=superset_auth.SUPERSET_PROVIDER,
        token_encrypted=token_encrypted,
        scopes="",
        account_label=label,
        # base_url is non-secret and handy for the UI; credentials stay
        # exclusively in the encrypted token payload.
        config_json=json.dumps({"base_url": base_url, "user": user or None}),
    )

    kickoff_sync(company.company_id, superset_auth.SUPERSET_PROVIDER)

    return {
        "ok": True,
        "provider": superset_auth.SUPERSET_PROVIDER,
        "account_label": label,
    }


@router.delete("/superset")
def superset_disconnect(
    company: CompanyContext = Depends(require_company),
):
    _require_admin_for_org_connector(company, superset_auth.SUPERSET_PROVIDER)
    row = db.get_connection(company.company_id, superset_auth.SUPERSET_PROVIDER)
    if not row:
        raise HTTPException(404, "Superset is not connected")
    db.delete_connection(company.company_id, superset_auth.SUPERSET_PROVIDER)
    return {"deleted": True, "provider": superset_auth.SUPERSET_PROVIDER}


# ─────────────────── Uploaded documents (no third party) ─────────────────────
#
# The user's own business documents as a first-class connector. There is no
# OAuth and no API key: the "credential" is the corpus they uploaded, so the
# connect gesture IS the first upload. A source has a NAME, an OPTIONAL
# description of what the documents are, and any number of files of any type.
#
#   GET    /v1/connectors/uploads/sources                  -> list sources
#   POST   /v1/connectors/uploads/sources                  -> create + upload
#   POST   /v1/connectors/uploads/sources/{id}/files       -> add more files
#   DELETE /v1/connectors/uploads/sources/{id}             -> remove a source
#   DELETE /v1/connectors/uploads                          -> disconnect
#
# Every write ends in kickoff_sync, the same fire-and-forget ingest every other
# connector runs on connect — so uploaded documents reach the KG immediately
# instead of waiting for the weekly scheduler.

#: 20 MB per file, mirroring the dataset / roadmap / company-document caps.
UPLOAD_MAX_FILE_BYTES = 20 * 1024 * 1024

#: A single source can't be an unbounded dumping ground; the cap keeps one
#: upload gesture (and its background extraction) bounded.
UPLOAD_MAX_FILES_PER_REQUEST = 50


def _ensure_uploads_connection(company_id: str) -> None:
    """Create/refresh the `uploads` connection row so the connector shows
    Active in Settings and is picked up by the scheduler + brief data-source
    gate. Idempotent — re-uploading just re-stamps the row."""
    db.upsert_connection(
        company_id=company_id,
        provider=uploads.UPLOADS_PROVIDER,
        token_encrypted=encrypt_token_json(uploads.credential_to_store(company_id)),
        scopes="",
        account_label=uploads.ACCOUNT_LABEL,
        config_json="{}",
    )


async def _store_uploaded_files(
    company_id: str,
    source_id: str,
    files: list[UploadFile],
) -> tuple[list[dict], list[dict]]:
    """Read + convert + persist each upload. Partial success is fine: an
    oversized or unreadable file is reported per-file so the frontend can show
    ✓/✗ on each, exactly like the dataset upload route."""
    from app.document_sources import add_document_file

    stored: list[dict] = []
    errors: list[dict] = []

    def _store_one(name: str, blob: bytes, content_type: str | None) -> None:
        """Convert + persist one file, recording a per-file error on failure."""
        try:
            saved = add_document_file(
                company_id, source_id,
                filename=name, data=blob, content_type=content_type,
            )
        except Exception as e:  # noqa: BLE001 — one bad file must not fail the batch
            logger.exception("uploads: could not store %s", name)
            errors.append({"filename": name, "error": f"Could not store: {e}"})
            return
        stored.append({
            "id": saved.id,
            "filename": saved.filename,
            "content_type": saved.content_type,
            "size_bytes": saved.size_bytes,
            # Never the text itself — the list surfaces how much we extracted,
            # the same shape the company-document list uses.
            "extracted_chars": len(saved.extracted_text or ""),
            "uploaded_at": saved.uploaded_at,
        })

    for upload in files[:UPLOAD_MAX_FILES_PER_REQUEST]:
        filename = upload.filename or "untitled"
        data = await upload.read()
        if not data:
            errors.append({"filename": filename, "error": "Empty file"})
            continue
        if len(data) > UPLOAD_MAX_FILE_BYTES:
            errors.append({
                "filename": filename,
                "error": f"File exceeds {UPLOAD_MAX_FILE_BYTES // (1024 * 1024)}MB limit",
            })
            continue
        # A .zip is a container, not a document: expand it and store each
        # member as its own file, matching the dataset upload route. Without
        # this the archive itself went through the converter — i.e. landed as
        # an unreadable placeholder — and none of its contents reached the KG.
        if filename.lower().endswith(".zip"):
            from app.datasets import DatasetError, expand_zip_members

            try:
                members, zip_errors = expand_zip_members(
                    filename, data, per_member_max_bytes=UPLOAD_MAX_FILE_BYTES,
                )
            except DatasetError as e:
                errors.append({"filename": filename, "error": str(e)})
                continue
            errors.extend(zip_errors)
            if not members:
                errors.append({
                    "filename": filename,
                    "error": "Archive contained no usable files",
                })
            for member_name, member_bytes in members:
                _store_one(member_name, member_bytes, None)
            continue
        _store_one(filename, data, upload.content_type)

    if len(files) > UPLOAD_MAX_FILES_PER_REQUEST:
        errors.append({
            "filename": "",
            "error": f"Only the first {UPLOAD_MAX_FILES_PER_REQUEST} files were "
                     "accepted — upload the rest in another batch.",
        })
    return stored, errors


def _public_source(src, files: list) -> dict:
    return {
        "id": src.id,
        "name": src.name,
        "description": src.description,
        "created_at": src.created_at,
        "file_count": len(files),
        "files": [
            {
                "id": f.id,
                "filename": f.filename,
                "content_type": f.content_type,
                "size_bytes": f.size_bytes,
                "extracted_chars": len(f.extracted_text or ""),
                "uploaded_at": f.uploaded_at,
            }
            for f in files
        ],
    }


@router.get("/uploads/sources")
def uploads_list_sources(
    company: CompanyContext = Depends(require_company),
):
    """Every named document source for the company, newest first, with files.
    Readable by any member (mutations below are admin-only, like every other
    org-wide connector)."""
    from app.document_sources import list_document_sources, list_source_files

    out = [
        _public_source(src, list_source_files(company.company_id, src.id))
        for src in list_document_sources(company.company_id)
    ]
    return {"sources": out}


@router.post("/uploads/sources")
async def uploads_create_source(
    files: Annotated[list[UploadFile], File(description="Documents of any type")],
    name: Annotated[str, Form(description="What to call this source")],
    description: Annotated[str, Form()] = "",
    company: WorkspaceContext = Depends(require_workspace),
):
    """Create a named document source from one or more uploaded files.

    `name` is required, `description` is the optional "what are these documents
    and why do they matter" step — both are carried into every KG record the
    uploads puller emits, so the agents read the user's own framing of the
    corpus, not just filenames.

    Any file type is accepted: the shared ingest converter extracts pdf / docx /
    xlsx / csv / txt / md / pptx richly, passes other textual formats through,
    and stores a stub for binary content rather than failing the upload.
    """
    _require_admin_for_org_connector(company, uploads.UPLOADS_PROVIDER)
    label = (name or "").strip()
    if not label:
        raise HTTPException(422, "name is required")
    if len(label) > 200:
        raise HTTPException(422, "name must be 200 characters or fewer")
    if not files:
        raise HTTPException(400, "No files uploaded")

    from app.document_sources import create_document_source, list_source_files

    src = create_document_source(
        company.company_id,
        name=label,
        description=(description or "").strip(),
        workspace_id=company.workspace_id,
    )
    stored, errors = await _store_uploaded_files(company.company_id, src.id, files)
    if not stored:
        # Nothing landed — don't leave an empty source (or claim a connection).
        from app.document_sources import delete_document_source

        delete_document_source(company.company_id, src.id)
        raise HTTPException(
            400,
            "; ".join(f"{e['filename']}: {e['error']}" for e in errors)
            or "No files could be stored",
        )

    _ensure_uploads_connection(company.company_id)
    # Same fire-and-forget ingest every other connector runs on connect.
    kickoff_sync(company.company_id, uploads.UPLOADS_PROVIDER)

    return {
        "ok": True,
        "provider": uploads.UPLOADS_PROVIDER,
        "source": _public_source(src, list_source_files(company.company_id, src.id)),
        "errors": errors,
    }


@router.post("/uploads/sources/{source_id}/files")
async def uploads_add_files(
    source_id: str,
    files: Annotated[list[UploadFile], File(description="Documents of any type")],
    company: WorkspaceContext = Depends(require_workspace),
):
    """Add more documents to an existing source."""
    _require_admin_for_org_connector(company, uploads.UPLOADS_PROVIDER)
    from app.document_sources import get_document_source, list_source_files

    src = get_document_source(company.company_id, source_id)
    if src is None:
        raise HTTPException(404, "Document source not found")
    if not files:
        raise HTTPException(400, "No files uploaded")

    stored, errors = await _store_uploaded_files(company.company_id, source_id, files)
    if stored:
        _ensure_uploads_connection(company.company_id)
        kickoff_sync(company.company_id, uploads.UPLOADS_PROVIDER)

    return {
        "ok": bool(stored),
        "source": _public_source(src, list_source_files(company.company_id, source_id)),
        "errors": errors,
    }


@router.delete("/uploads/sources/{source_id}")
def uploads_delete_source(
    source_id: str,
    company: CompanyContext = Depends(require_company),
):
    """Remove a source and its documents. Signals already extracted into the KG
    stay (same soft-delete semantics as disconnecting any other connector)."""
    _require_admin_for_org_connector(company, uploads.UPLOADS_PROVIDER)
    from app.document_sources import delete_document_source, list_document_sources

    if not delete_document_source(company.company_id, source_id):
        raise HTTPException(404, "Document source not found")
    # Last source gone → the connector has nothing behind it; drop the row so
    # Settings shows "Off" rather than an Active connector with no data.
    if not list_document_sources(company.company_id):
        db.delete_connection(company.company_id, uploads.UPLOADS_PROVIDER)
    return {"deleted": True, "id": source_id}


@router.delete("/uploads")
def uploads_disconnect(
    company: CompanyContext = Depends(require_company),
):
    """Disconnect the connector. Like every other disconnect this drops the
    connection row only — the uploaded documents (and anything already
    extracted into the KG) are left in place, so reconnecting is a re-upload
    of nothing."""
    _require_admin_for_org_connector(company, uploads.UPLOADS_PROVIDER)
    row = db.get_connection(company.company_id, uploads.UPLOADS_PROVIDER)
    if not row:
        raise HTTPException(404, "Uploaded documents is not connected")
    db.delete_connection(company.company_id, uploads.UPLOADS_PROVIDER)
    return {"deleted": True, "provider": uploads.UPLOADS_PROVIDER}


# ─────────────────────── GitHub webhook ───────────────────────

# We acknowledge anything we don't act on with 200 so GitHub doesn't
# keep retrying. Only signature failures + unparseable bodies 4xx.
_WEBHOOK_HANDLED_EVENTS = {
    "installation",
    "installation_repositories",
    "pull_request",
    "push",
    "ping",
}


def _excerpt(body: str | None, limit: int = 500) -> str | None:
    if not body:
        return None
    body = body.strip()
    return body[:limit]


def _handle_installation_event(payload: dict) -> None:
    action = payload.get("action")
    install = payload.get("installation") or {}
    install_id = install.get("id")
    if not install_id:
        return
    if action in {"created", "new_permissions_accepted", "unsuspend"}:
        account = install.get("account") or {}
        db.upsert_github_installation(
            installation_id=int(install_id),
            account_id=int(account.get("id") or 0),
            account_login=str(account.get("login") or ""),
            account_type=str(account.get("type") or "User"),
            repository_selection=str(install.get("repository_selection") or "selected"),
            suspended=False,
            permissions=install.get("permissions") or {},
            events=install.get("events") or [],
        )
    elif action == "suspend":
        existing = db.get_github_installation(int(install_id))
        if existing:
            account = install.get("account") or {}
            db.upsert_github_installation(
                installation_id=int(install_id),
                account_id=int(account.get("id") or existing["account_id"]),
                account_login=str(account.get("login") or existing["account_login"]),
                account_type=str(account.get("type") or existing["account_type"]),
                repository_selection=str(
                    install.get("repository_selection") or existing["repository_selection"]
                ),
                suspended=True,
                permissions=install.get("permissions") or {},
                events=install.get("events") or [],
            )
    elif action == "deleted":
        db.delete_github_installation(int(install_id))
        github_app.clear_installation_token_cache(int(install_id))
        from app.design_agent.codebase_map.service import clear_map_cache
        clear_map_cache(int(install_id))


def _handle_installation_repositories_event(payload: dict) -> None:
    install = payload.get("installation") or {}
    install_id = install.get("id")
    if not install_id:
        return
    # repository_selection may flip "selected" <-> "all".
    existing = db.get_github_installation(int(install_id))
    if not existing:
        return
    account = install.get("account") or {}
    db.upsert_github_installation(
        installation_id=int(install_id),
        account_id=int(account.get("id") or existing["account_id"]),
        account_login=str(account.get("login") or existing["account_login"]),
        account_type=str(account.get("type") or existing["account_type"]),
        repository_selection=str(
            install.get("repository_selection") or existing["repository_selection"]
        ),
        suspended=bool(existing["suspended"]),
        permissions=install.get("permissions") or json.loads(existing["permissions_json"] or "{}"),
        events=install.get("events") or json.loads(existing["events_json"] or "[]"),
    )


def _handle_push_event(payload: dict) -> None:
    """A push to a connected repo may have changed its design tokens, so mark
    any cached design system extracted from that repo stale. The next design
    generation then re-extracts instead of serving a now-outdated cached row.

    A push is also a NEW commit_sha, hence a natural L1/L2 codebase-map cache miss
    — so we additionally fire a best-effort, bounded, coalesced background pre-warm
    of the new sha so the NEXT /locate is hot instead of paying the cold
    rebuild inline. No explicit cache deletion is needed: commit_sha keying already
    makes the old map unreachable, so the warm is purely a latency optimization."""
    repo = payload.get("repository") or {}
    repo_full_name = str(repo.get("full_name") or "").strip()
    if not repo_full_name:
        return
    db.mark_github_design_systems_stale(repo_full_name)

    # Pre-warm. The installation id rides on the push payload's `installation`
    # block; the pushed branch is `refs/heads/<branch>` in `ref`. We ONLY warm the
    # default branch — that is what /locate resolves against, so warming a feature
    # branch nobody will locate against would be wasted cold-build load. We pass
    # ref=None and let build_map resolve the current default-branch SHA itself
    # (avoids trusting a possibly-stale payload sha). A non-default-branch push, or
    # a payload missing the installation id, simply skips the warm (best-effort).
    install = payload.get("installation") or {}
    install_id = install.get("id")
    pushed_ref = str(payload.get("ref") or "")  # e.g. "refs/heads/main"
    default_branch = str(repo.get("default_branch") or "")
    is_default_push = bool(default_branch) and pushed_ref == f"refs/heads/{default_branch}"
    if install_id and is_default_push:
        _prewarm_codebase_map_on_push(int(install_id), repo_full_name, None)


def _handle_pull_request_event(payload: dict) -> None:
    install = payload.get("installation") or {}
    install_id = install.get("id")
    pr = payload.get("pull_request") or {}
    repo = payload.get("repository") or {}
    if not install_id or not pr or not repo:
        return
    state = pr.get("state") or "open"
    if pr.get("merged"):
        state = "merged"
    # Inherit the tenant from the PR's installation. A PR for an unbound
    # (legacy NULL-company) installation gets company_id=None and is excluded
    # from all scoped reads until that installation is reconnected.
    owner = db.get_github_installation(int(install_id)) or {}
    company_id = owner.get("company_id")
    db.upsert_github_pull_request(
        installation_id=int(install_id),
        repo_full_name=str(repo.get("full_name") or ""),
        pr_number=int(pr.get("number") or 0),
        title=str(pr.get("title") or ""),
        state=state,
        is_draft=bool(pr.get("draft")),
        author_login=(pr.get("user") or {}).get("login"),
        head_ref=(pr.get("head") or {}).get("ref"),
        base_ref=(pr.get("base") or {}).get("ref"),
        html_url=pr.get("html_url"),
        body_excerpt=_excerpt(pr.get("body")),
        pr_created_at=pr.get("created_at"),
        pr_updated_at=pr.get("updated_at"),
        company_id=company_id,
    )


@router.post("/github/webhook")
async def github_webhook(
    request: Request,
    x_github_event: Annotated[str | None, Header(alias="X-GitHub-Event")] = None,
    x_hub_signature_256: Annotated[str | None, Header(alias="X-Hub-Signature-256")] = None,
    x_github_delivery: Annotated[str | None, Header(alias="X-GitHub-Delivery")] = None,
):
    raw = await request.body()
    if not github_app.verify_webhook_signature(raw, x_hub_signature_256):
        raise HTTPException(401, "Invalid webhook signature")
    try:
        payload = json.loads(raw.decode("utf-8") or "{}")
    except (UnicodeDecodeError, json.JSONDecodeError) as e:
        raise HTTPException(400, "Webhook body is not valid JSON") from e

    event = (x_github_event or "").strip()
    if event == "ping":
        return {"ok": True, "event": "ping"}
    if event == "installation":
        _handle_installation_event(payload)
    elif event == "installation_repositories":
        _handle_installation_repositories_event(payload)
    elif event == "pull_request":
        _handle_pull_request_event(payload)
    elif event == "push":
        _handle_push_event(payload)
    else:
        logger.info("GitHub webhook: ignoring event %s delivery=%s", event, x_github_delivery)
        return {"ok": True, "event": event, "handled": False}
    return {"ok": True, "event": event, "handled": True}


# ───────────────────── Bring-your-own-LLM context ─────────────────────
#
#   GET    /v1/connectors/llm-context/prompt          -> the prompt to paste
#   POST   /v1/connectors/llm-context/import          -> upload the .md
#   GET    /v1/connectors/llm-context/import/{job_id} -> the LLM pass's result
#
# The user runs our prompt in whichever assistant they already use — Claude,
# ChatGPT, Gemini — and uploads the Markdown it returns. Deliberately
# integration-free: there is no OAuth path here, because an Anthropic token
# authorises Messages API calls and cannot read a user's claude.ai
# conversation history, so a "connect your account" flow could not actually
# produce the context it promised. One prompt works everywhere instead.
#
# ONE READ, IN THE BACKGROUND. The POST files the .md as a company document,
# hands it to the knowledge-graph ingest and kicks an LLM extraction, then
# returns a job id — no fields. Until v3 a deterministic heading walk also ran
# inline, but the v3 prompt is the product team's own document rather than our
# heading contract, so that reader was deleted (see app/llm_context.py). The
# extraction reads documents of any shape, which is what the walk never could.
#
# That background pass is why the import step hands off to CONNECTORS rather
# than product: connecting tools is the one step in the flow the import cannot
# prefill, so it is the step worth spending the extraction's latency on. By the
# time the user reaches metrics and product, the job has landed.
#
# It does not write to the workspace: it returns `fields` for the onboarding
# form to prefill and the user to confirm. An import must never silently
# overwrite something the user already typed.
#
# Strong refs to in-flight extraction tasks — asyncio holds only a weak
# reference to a bare create_task result, so without this the task can be
# garbage-collected mid-run and the row would be stuck 'generating' (mirrors
# routes/onboarding.py).
_context_tasks: set[asyncio.Task] = set()

#: Context exports are prose, not corpora — a few hundred KB at the outside.
#: A tighter cap than the 20MB document limit keeps a mis-drop (a video, a DB
#: dump) from being read into memory before we reject it.
LLM_CONTEXT_MAX_BYTES = 2 * 1024 * 1024

#: The document source the uploaded export is filed under, so the context the
#: user handed over also grounds the agents instead of only prefilling a form.
LLM_CONTEXT_SOURCE_NAME = "LLM context export"
LLM_CONTEXT_SOURCE_DESCRIPTION = (
    "Company, product, user and strategy context exported from the user's own "
    "AI assistant during onboarding."
)


def _context_result(parsed, *, note: str | None = None) -> dict:
    """The response shape the import returns."""
    return {
        "ok": not parsed.is_empty,
        "fields": parsed.fields,
        "unmapped": parsed.unmapped,
        "format_version": parsed.format_version,
        # Honest reporting: an export we could not read anything out of is a
        # failed import the user should hear about, not a silent no-op.
        "note": note
        or (
            None
            if not parsed.is_empty
            else "We couldn't read anything usable out of that file. Check it's "
            "the .md your assistant produced, or fill the steps in manually."
        ),
    }


async def _run_context_extraction(job_id: int, markdown: str) -> None:
    """Background worker: run the LLM extraction and complete the job row.

    Never raises — `extract_context_fields` already degrades to an empty read
    on any LLM failure, and anything left (a DB blip while writing the row) is
    logged and marked `error` so the client's poll terminates instead of
    spinning on 'generating' forever.
    """
    from app.db.llm_context_jobs import complete_context_job, fail_context_job
    from app.llm_context import extract_context_fields

    try:
        parsed = await asyncio.to_thread(extract_context_fields, markdown)
        complete_context_job(job_id, _context_result(parsed))
    except Exception as exc:  # noqa: BLE001 — a stuck job is worse than a failed one
        logger.exception("llm-context: extraction job %s failed", job_id)
        try:
            fail_context_job(job_id, str(exc))
        except Exception:  # noqa: BLE001
            logger.exception("llm-context: could not mark job %s failed", job_id)


async def _start_context_extraction(company_id: str, markdown: str) -> int | None:
    """Kick the background LLM pass, returning its job id (None if it couldn't
    start). It is the ONLY reader (see app/llm_context.py), so a job that never
    starts means the upload prefills nothing — the caller says so in `note`
    rather than leaving the client polling an id it never got."""
    from app.db.llm_context_jobs import start_context_job

    try:
        job_id = start_context_job(company_id)
    except Exception:  # noqa: BLE001 — the inline parse still stands
        logger.exception("llm-context: could not start the extraction job")
        return None

    if "pytest" in sys.modules:
        # The TestClient doesn't keep the app's event loop alive between
        # requests, so a fire-and-forget create_task would never run and a
        # client's status poll would spin forever. Run the worker inline under
        # pytest for deterministic results (mirrors routes/onboarding.py).
        await _run_context_extraction(job_id, markdown)
        return job_id

    task = asyncio.create_task(_run_context_extraction(job_id, markdown))
    _context_tasks.add(task)
    task.add_done_callback(_context_tasks.discard)
    return job_id


async def _import_context_markdown(company, markdown: str) -> dict:
    """File the raw document, kick the LLM pass, and hand back its job id.

    Since v3 there is no deterministic reader (see app/llm_context.py), so this
    response carries no fields — only the version we recognised the file as and
    the `job_id` the client polls. The shape is unchanged so the onboarding form
    applies a POST result and a poll result through one code path.

    Filing is best-effort and never fails the import: it is the half of the
    upload that does not depend on an LLM, so a storage hiccup is reported in
    `note` rather than swallowed, and never blocks the extraction.
    """
    from app.llm_context import ParsedContext, detect_format_version

    parsed = ParsedContext(format_version=detect_format_version(markdown))
    note = None
    # Whether the raw .md was actually filed as a document source AND handed to
    # the KG ingest. This is the real "your context reached the knowledge graph"
    # signal — distinct from `ok` (did the extraction read structured fields).
    # The Settings/Business-Context card leans on it: it never prefills, so
    # filing IS the whole outcome there, and it must not claim a KG feed that a
    # storage hiccup silently swallowed.
    filed = False
    try:
        from app.document_sources import add_document_file, create_document_source

        src = create_document_source(
            company.company_id,
            name=LLM_CONTEXT_SOURCE_NAME,
            description=LLM_CONTEXT_SOURCE_DESCRIPTION,
            workspace_id=getattr(company, "workspace_id", None),
        )
        add_document_file(
            company.company_id,
            src.id,
            filename="llm-context-export.md",
            data=markdown.encode("utf-8"),
            content_type="text/markdown",
        )
        _ensure_uploads_connection(company.company_id)
        kickoff_sync(company.company_id, uploads.UPLOADS_PROVIDER)
        filed = True
    except Exception:  # noqa: BLE001 — filing must not cost the user their prefill
        logger.exception("llm-context: could not file the export as a document source")
        note = (
            "We read your context, but couldn't also save the file to your "
            "documents. Nothing is lost — you can upload it again from Settings."
        )

    job_id = await _start_context_extraction(company.company_id, markdown)
    result = _context_result(parsed, note=note)
    # `ok` is about whether the USER's upload produced anything usable, and the
    # only reader runs in that job. So this response is never the verdict while
    # a job is live — the client shows "reading…" and the poll settles it either
    # way. Preserve a FILING-failure note though (filed is False): that isn't
    # the "found nothing" verdict the job can overturn, it's the KG feed the
    # user needs to know about.
    if job_id is not None and not result["ok"] and filed:
        result["note"] = None
    return {**result, "job_id": job_id, "filed": filed}


@router.get("/llm-context/prompt")
def llm_context_prompt(
    company_name: str = Query(default="", max_length=500),
    company_website: str = Query(default="", max_length=500),
):
    """The prompt the user pastes into Claude / ChatGPT / Gemini.

    The caller passes what it already knows about the company and gets those
    values written into the prompt's confirmed-values block. Since 2026-07-27
    the company step runs BEFORE the import step, so onboarding always has the
    name and website by the time it asks for this — and the assistant starts
    with the entity locked rather than inferring it from a search.

    Both are optional and unauthenticated on purpose: the values come from the
    caller's own form, they are written into a prompt returned to that same
    caller (who can edit it in the textarea anyway), and a caller that has
    neither still gets a usable prompt with an empty block. Making this
    tenant-scoped would buy nothing and would break the fetch for anyone who
    reaches the step without a company row yet.

    It lives on the connectors router so the frontend has one place to fetch it
    and the copy shown in the UI can never drift from what the extraction
    expects to read back.
    """
    from app.llm_context import CONTEXT_FORMAT_VERSION, build_context_prompt

    return {
        "prompt": build_context_prompt(
            company_name=company_name, company_website=company_website
        ),
        "format_version": CONTEXT_FORMAT_VERSION,
    }


@router.post("/llm-context/import")
async def llm_context_import(
    file: Annotated[UploadFile, File(description="The .md the assistant produced")],
    company: WorkspaceContext = Depends(require_workspace),
):
    """Upload the Markdown export and get back onboarding prefill fields."""
    data = await file.read()
    if not data:
        raise HTTPException(400, "That file is empty")
    if len(data) > LLM_CONTEXT_MAX_BYTES:
        raise HTTPException(
            413,
            "Context exports are text — this one is over "
            f"{LLM_CONTEXT_MAX_BYTES // (1024 * 1024)}MB. "
            "Upload the .md the prompt produced.",
        )
    try:
        markdown = data.decode("utf-8")
    except UnicodeDecodeError:
        # A PDF/DOCX dropped on a Markdown field is the likely cause; say so
        # rather than surfacing a decoder error.
        raise HTTPException(
            415,
            "That doesn't look like a text file. Upload the .md your assistant "
            "produced, or add other documents under Settings -> Connectors.",
        ) from None
    return await _import_context_markdown(company, markdown)


@router.get("/llm-context/import/{job_id}")
def llm_context_import_status(
    job_id: int,
    company: WorkspaceContext = Depends(require_workspace),
):
    """Status + result for the background LLM extraction.

    Returns `{status, result, error}`. Once `status == 'ready'`, `result`
    carries the SAME {ok, fields, unmapped, format_version, note} shape the
    POST returns, so the onboarding form applies it through one code path
    regardless of which read produced it. 404 when the job doesn't belong to
    the caller's company (no cross-tenant existence disclosure).
    """
    from app.db.llm_context_jobs import get_context_job

    row = get_context_job(job_id)
    if not row or row.get("company_id") != company.company_id:
        raise HTTPException(404, "Import job not found")
    return {
        "status": row.get("status") or "generating",
        "result": row.get("result"),
        "error": row.get("error"),
    }
