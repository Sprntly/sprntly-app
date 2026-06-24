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
import json
import logging
import re
import sys
import time
from typing import Annotated
from urllib.parse import urlencode

import requests

from fastapi import APIRouter, Depends, Header, HTTPException, Request
from fastapi.responses import RedirectResponse
from google.auth.transport.requests import Request as GoogleAuthRequest
from pydantic import BaseModel

from app import db
from app import datasets as datasets_service
from app.auth import CompanyContext, require_company
from app.config import settings
from app.connectors import (
    clickup_oauth,
    figma_oauth,
    fireflies_apikey,
    github_app,
    google_oauth,
    hubspot_oauth,
    slack_oauth,
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
from app.kg_ingest.auto_sync import kickoff_sync

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
    return {
        "id": row["id"],
        "provider": row["provider"],
        "status": row["status"],
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


def _visible_connection_rows(company: CompanyContext) -> list[dict]:
    """Connection rows the CURRENT user may see: every company-scoped
    provider (shared) plus only THIS user's own Slack row. Other members'
    per-user Slack rows (and legacy NULL-user Slack rows) are filtered out
    so one member never sees another's personal Slack."""
    rows = db.list_connections(company.company_id)
    out: list[dict] = []
    for r in rows:
        if r.get("provider") == slack_oauth.SLACK_PROVIDER:
            if r.get("user_id") != company.user_id:
                continue
        out.append(r)
    return out


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
    from app.kg_ingest.runner import PULLERS

    rows = _visible_connection_rows(company)
    out = []
    for r in rows:
        provider = r["provider"]
        out.append({
            "provider": provider,
            "status": r["status"],
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

    # Slack is per-user: validate THIS user's own connection, never a
    # company-shared one. Every other provider stays company-scoped.
    if provider == slack_oauth.SLACK_PROVIDER:
        row = db.get_slack_connection(company.company_id, company.user_id)
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


def _auto_enable_drive_input_source(company_id: str, dataset: str | None) -> None:
    """Flip the dataset's google_drive input source on after a sync. Falls back
    to the dataset stored in the connection config when not passed explicitly."""
    dataset_slug = dataset
    if not dataset_slug:
        row = db.get_connection(company_id, google_oauth.GOOGLE_DRIVE_PROVIDER)
        if row and row.get("config_json"):
            try:
                cfg = json.loads(row["config_json"])
                dataset_slug = cfg.get("dataset")
            except (TypeError, ValueError):
                pass
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
            dataset=body.dataset,
            files=picked,
        )
    except SyncConfigError as e:
        raise HTTPException(400, str(e)) from e

    _auto_enable_drive_input_source(company.company_id, body.dataset)
    return result.to_dict()


@router.post("/google-drive/sync")
def google_drive_sync(
    body: GoogleDriveSyncIn | None = None,
    company: CompanyContext = Depends(require_company),
):
    _require_admin_for_org_connector(company, google_oauth.GOOGLE_DRIVE_PROVIDER)
    payload = body or GoogleDriveSyncIn()
    try:
        result = sync_google_drive(
            company_id=company.company_id,
            dataset=payload.dataset,
        )
    except SyncConfigError as e:
        raise HTTPException(400, str(e)) from e

    _auto_enable_drive_input_source(company.company_id, payload.dataset)
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

    Returns ``{"access_token", "expires_in"}`` (seconds until expiry). 404 if
    Drive isn't connected — matching how the other Drive routes signal that.
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

    return {"access_token": creds.token, "expires_in": expires_in}


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
    _require_admin_for_org_connector(company, figma_oauth.FIGMA_PROVIDER)
    """Sync Figma file structure and design tokens into the corpus.

    Fetches file tree + published styles and writes a markdown summary
    into DATA_DIR/{dataset}/figma_design_context.md. Company-scoped: uses
    the caller's company's Figma connection only.
    """
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
    target = settings.data_path / body.dataset / "figma_design_context.md"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(md_text, encoding="utf-8")

    # Auto-enable figma input source
    try:
        db.upsert_input_source(
            body.dataset, "figma", enabled=True,
            config={"file_key": body.file_key, "last_sync_at": db.utc_now()},
        )
    except Exception:
        logger.warning("Failed to auto-enable figma input source", exc_info=True)

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
    _require_admin_for_org_connector(company, github_app.GITHUB_PROVIDER)
    """Sync tracked GitHub PRs into the corpus as a markdown file.

    Reads open PRs from the github_pull_requests table and writes
    a summary into DATA_DIR/{dataset}/github_active_prs.md.

    Company-scoped: only the caller's company's PRs are read. A supplied
    installation_id must belong to the caller's company (else 404)."""
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
    target = settings.data_path / body.dataset / "github_active_prs.md"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(md_text, encoding="utf-8")

    # Auto-enable github input source
    try:
        db.upsert_input_source(
            body.dataset, "github", enabled=True,
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


class HubSpotSyncCorpusIn(BaseModel):
    dataset: str


@router.post("/hubspot/sync")
def hubspot_sync(
    body: HubSpotSyncCorpusIn,
    company: CompanyContext = Depends(require_company),
):
    _require_admin_for_org_connector(company, hubspot_oauth.HUBSPOT_PROVIDER)
    """Sync HubSpot CRM data (contacts, companies, deals) into the corpus.

    Fetches data from HubSpot API, converts to markdown, and writes
    into DATA_DIR/{dataset}/ so it enters the knowledge base. Company-scoped:
    uses the caller's company's HubSpot connection only.
    """
    from app.connectors.hubspot_sync import HubSpotSyncError, sync_hubspot

    try:
        result = sync_hubspot(body.dataset, company_id=company.company_id)
    except HubSpotSyncError as e:
        raise HTTPException(400, str(e)) from e
    return result.to_dict()


@router.post("/hubspot/sync-to-corpus")
def hubspot_sync_to_corpus(
    body: HubSpotSyncCorpusIn,
    company: CompanyContext = Depends(require_company),
):
    _require_admin_for_org_connector(company, hubspot_oauth.HUBSPOT_PROVIDER)
    """Alias for /hubspot/sync — matches Figma/GitHub sync-to-corpus pattern."""
    from app.connectors.hubspot_sync import HubSpotSyncError, sync_hubspot

    try:
        result = sync_hubspot(body.dataset, company_id=company.company_id)
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

    return _build_post_oauth_redirect(payload, slack_oauth.SLACK_PROVIDER)


@router.delete("/slack")
def slack_disconnect(
    company: CompanyContext = Depends(require_company),
):
    # Disconnect only THIS user's Slack — never another member's.
    row = db.get_slack_connection(company.company_id, company.user_id)
    if not row:
        raise HTTPException(404, "Slack is not connected")
    # Revoke the token on Slack's side first (best-effort), so the install is
    # torn down for the workspace, not just deleted locally — Slack Marketplace
    # expects a clean uninstall. A revoke failure must not block the local delete.
    try:
        token_json = json.loads(decrypt_token_json(row["token_json_encrypted"]))
        bot_token = token_json.get("access_token")
        if bot_token:
            slack_oauth.revoke_token(bot_token)
    except Exception:  # noqa: BLE001 — never let revoke block the disconnect
        logger.warning("Slack token revoke on disconnect failed", exc_info=True)
    db.delete_slack_connection(company.company_id, company.user_id)
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

    try:
        resolved = _resolve_slack_inbound(team_id, slack_user)
        if not resolved:
            logger.info("slack inbound: no connection for team=%s — ignoring", team_id)
            return
        company_id, _user_id, bot_token, bot_user_id = resolved
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
        await asyncio.to_thread(
            slack_oauth.post_message,
            bot_token,
            channel=channel,
            text=answer_text,
            thread_ts=thread_ts,
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


def _resolve_slack_inbound(
    team_id: str, slack_user: str
) -> tuple[str, str, str, str] | None:
    """Map an inbound Slack (team_id, slack_user) to one Sprntly connection.

    Returns (company_id, user_id, bot_token, bot_user_id), or None if the team
    has no usable connection. The installing user's Slack id (authed_user_id)
    lives inside the encrypted token blob — not an indexed column — so we list
    the team's connections and prefer the one whose authed_user_id matches the
    messaging user; absent a match we fall back to the team's first connection
    (1 install = 1 company by design, so its bot token + company apply)."""
    chosen: tuple[str, str, str, str] | None = None
    fallback: tuple[str, str, str, str] | None = None
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
                "content": content if is_bot else _strip_leading_mention(content),
            }
        )
    return history[-12:]


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
    """List channels the bot can post into. Backs the channel-picker
    in the Configure drawer. Resolves THIS user's own Slack."""
    token, _row = _slack_bot_token(company.company_id, company.user_id)
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
    channel_id: str
    channel_name: str | None = None

    def model_post_init(self, _context) -> None:
        if not self.channel_id or not self.channel_id.strip():
            raise ValueError("channel_id cannot be empty")


@router.post("/slack/config")
def slack_save_config(
    body: SlackConfigIn,
    company: CompanyContext = Depends(require_company),
):
    """Save the user's selected notification-target channel. Stored on
    THIS user's own Slack connection row's config so the Comms Agent can
    read it at post-time without a separate lookup table."""
    row = db.get_slack_connection(company.company_id, company.user_id)
    if not row:
        raise HTTPException(404, "Slack is not connected")
    patch: dict = {"channel_id": body.channel_id.strip()}
    if body.channel_name:
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
    return {"ok": True, "config": config}


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
    into DATA_DIR/{dataset}/ so it enters the knowledge base. Uses THIS
    user's own Slack bot token (per-user scope; also fixes the prior
    company-less token lookup).
    """
    from app.connectors.slack_sync import SlackSyncError, sync_slack

    try:
        result = sync_slack(
            body.dataset,
            company_id=company.company_id,
            user_id=company.user_id,
            history_days=body.history_days,
        )
    except SlackSyncError as e:
        raise HTTPException(400, str(e)) from e
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
    return {"deleted": True, "provider": fireflies_apikey.FIREFLIES_PROVIDER}


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
