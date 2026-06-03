"""OAuth and status for third-party connectors.

  GET    /v1/connectors                         -> list (no secrets)

  GET    /v1/connectors/google-drive/authorize  -> redirect to Google
  GET    /v1/connectors/google-drive/callback   -> OAuth callback
  GET    /v1/connectors/google-drive/folders    -> browse folders to select
  POST   /v1/connectors/google-drive/config     -> save folder + dataset
  POST   /v1/connectors/google-drive/sync       -> pull folder into corpus
  DELETE /v1/connectors/google-drive            -> disconnect

  GET    /v1/connectors/figma/authorize         -> redirect to Figma
  GET    /v1/connectors/figma/callback          -> OAuth callback
  DELETE /v1/connectors/figma                   -> disconnect
  GET    /v1/connectors/figma/files/{key}       -> file structure (Design Agent input)
  GET    /v1/connectors/figma/files/{key}/styles -> design tokens (Design Agent input)

  GET    /v1/connectors/github/authorize        -> redirect to GitHub
  GET    /v1/connectors/github/callback         -> OAuth callback
  DELETE /v1/connectors/github                  -> disconnect
  POST   /v1/connectors/github/webhook          -> GitHub App event sink
  GET    /v1/connectors/github/installations    -> list installs we know about
  GET    /v1/connectors/github/pull-requests    -> list tracked open PRs
  GET    /v1/connectors/github/repos            -> user's accessible repos (Engineer Agent input)
"""
from __future__ import annotations

import json
import logging
from typing import Annotated
from urllib.parse import urlencode

from fastapi import APIRouter, Depends, Header, HTTPException, Request
from fastapi.responses import RedirectResponse
from google.auth.transport.requests import Request as GoogleAuthRequest
from pydantic import BaseModel

from app import db
from app.auth import require_session, require_workspace_membership
from app.config import settings
from app.connectors import (
    clickup_oauth,
    figma_oauth,
    fireflies_apikey,
    github_app,
    google_oauth,
    hubspot_oauth,
)
from app.connectors.google_drive_sync import (
    SyncConfigError,
    browse_folders,
    merge_config,
    parse_folder_id,
    sync_google_drive,
)
from app.connectors.tokens import (
    TokenEncryptionError,
    decrypt_token_json,
    encrypt_token_json,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/v1/connectors", tags=["connectors"])


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
        "created_at": row.get("created_at"),
        "updated_at": row.get("updated_at"),
    }


@router.get("")
def list_connections(
    workspace_id: str = Depends(require_workspace_membership),
):
    rows = db.list_connections(workspace_id)
    return {"connections": [_public_connection(r) for r in rows]}


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


class StartOauthIn(BaseModel):
    dataset: str | None = None


@router.post("/{provider}/start-oauth")
def start_oauth(
    provider: str,
    body: StartOauthIn | None = None,
    workspace_id: str = Depends(require_workspace_membership),
):
    payload = body or StartOauthIn()

    if provider == google_oauth.GOOGLE_DRIVE_PROVIDER:
        state = google_oauth.sign_oauth_state(
            workspace_id=workspace_id, dataset=payload.dataset
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
            state=figma_oauth.sign_oauth_state(workspace_id=workspace_id)
        )
        return {"authorize_url": url}

    if provider == github_app.GITHUB_PROVIDER:
        if not github_app.github_oauth_configured():
            raise HTTPException(500, "GitHub OAuth is not configured on the server")
        url = github_app.authorize_url(
            state=github_app.sign_oauth_state(workspace_id=workspace_id)
        )
        return {"authorize_url": url}

    if provider == clickup_oauth.CLICKUP_PROVIDER:
        if not clickup_oauth.clickup_configured():
            raise HTTPException(500, "ClickUp OAuth is not configured on the server")
        url = clickup_oauth.authorize_url(
            state=clickup_oauth.sign_oauth_state(workspace_id=workspace_id)
        )
        return {"authorize_url": url}

    if provider == hubspot_oauth.HUBSPOT_PROVIDER:
        if not hubspot_oauth.hubspot_configured():
            raise HTTPException(500, "HubSpot OAuth is not configured on the server")
        url = hubspot_oauth.authorize_url(
            state=hubspot_oauth.sign_oauth_state(workspace_id=workspace_id)
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
    workspace_id: str = Depends(require_workspace_membership),
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

    row = db.get_connection(workspace_id, provider)
    if not row:
        raise HTTPException(404, f"{provider!r} is not connected")

    try:
        token_json = json.loads(decrypt_token_json(row["token_json_encrypted"]))
    except (TokenEncryptionError, json.JSONDecodeError) as e:
        raise HTTPException(500, "Stored token unreadable") from e

    user_obj: dict = {}

    if provider == google_oauth.GOOGLE_DRIVE_PROVIDER:
        # Drive: prove the token chain is healthy by attempting refresh.
        try:
            creds = google_oauth.credentials_from_token_json(
                json.dumps(token_json)
            )
            if creds.expired and creds.refresh_token:
                creds.refresh(GoogleAuthRequest())
            user_obj = {
                "email": row.get("google_email") or row.get("account_label") or "",
            }
        except Exception as e:
            raise HTTPException(400, f"Google Drive token rejected: {e}") from e
    elif provider == figma_oauth.FIGMA_PROVIDER:
        access_token = token_json.get("access_token") or ""
        user_obj = figma_oauth.fetch_me(access_token) or {}
    elif provider == github_app.GITHUB_PROVIDER:
        access_token = token_json.get("access_token") or ""
        user_obj = github_app.fetch_authenticated_user(access_token) or {}
    elif provider == clickup_oauth.CLICKUP_PROVIDER:
        access_token = token_json.get("access_token") or ""
        user_obj = clickup_oauth.fetch_authenticated_user(access_token) or {}
    elif provider == hubspot_oauth.HUBSPOT_PROVIDER:
        access_token = token_json.get("access_token") or ""
        user_obj = hubspot_oauth.fetch_token_info(access_token) or {}
    elif provider == fireflies_apikey.FIREFLIES_PROVIDER:
        api_key = token_json.get("api_key") or ""
        user_obj = fireflies_apikey.fetch_authenticated_user(api_key) or {}
    else:
        raise HTTPException(
            404, f"Test connection not supported for provider {provider!r}"
        )

    if not user_obj:
        raise HTTPException(
            400,
            f"{provider} rejected the stored credential — disconnect and reconnect.",
        )

    label = (
        user_obj.get("email")
        or user_obj.get("user")
        or user_obj.get("username")
        or user_obj.get("login")
        or user_obj.get("handle")
        or user_obj.get("name")
        or ""
    )
    tested_at = datetime.now(timezone.utc).isoformat()
    return {"ok": True, "account_label": str(label), "tested_at": tested_at}


@router.get("/google-drive/authorize")
def google_drive_authorize(
    dataset: str | None = None,
    workspace_id: str = Depends(require_workspace_membership),
):
    state = google_oauth.sign_oauth_state(workspace_id=workspace_id, dataset=dataset)
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
    workspace_id = payload["workspace_id"]

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
        workspace_id=workspace_id,
        provider=google_oauth.GOOGLE_DRIVE_PROVIDER,
        token_encrypted=token_encrypted,
        scopes=google_oauth.DRIVE_READONLY_SCOPE,
        google_email=email,
        config_json=json.dumps(config),
    )

    q = urlencode({"section": "connectors", "connected": google_oauth.GOOGLE_DRIVE_PROVIDER})
    return RedirectResponse(f"{settings.frontend_url.rstrip('/')}/settings?{q}")


class GoogleDriveConfigIn(BaseModel):
    folder_id: str
    folder_name: str | None = None
    dataset: str | None = None


class GoogleDriveSyncIn(BaseModel):
    dataset: str | None = None
    folder_id: str | None = None


@router.get("/google-drive/folders")
def google_drive_list_folders(
    parent_id: str | None = None,
    workspace_id: str = Depends(require_workspace_membership),
):
    try:
        return browse_folders(workspace_id, parent_id)
    except SyncConfigError as e:
        logger.warning("Drive folder browse failed: %s", e)
        raise HTTPException(400, str(e)) from e


@router.post("/google-drive/config")
def google_drive_config(
    body: GoogleDriveConfigIn,
    workspace_id: str = Depends(require_workspace_membership),
):
    row = db.get_connection(workspace_id, google_oauth.GOOGLE_DRIVE_PROVIDER)
    if not row:
        raise HTTPException(404, "Google Drive is not connected")
    try:
        fid = parse_folder_id(body.folder_id)
    except SyncConfigError as e:
        raise HTTPException(422, str(e)) from e
    patch: dict = {"folder_id": fid}
    if body.folder_name:
        patch["folder_name"] = body.folder_name.strip()
    if body.dataset:
        patch["dataset"] = body.dataset.strip()
    updated = merge_config(row, patch)
    return {"ok": True, "config": updated}


@router.post("/google-drive/sync")
def google_drive_sync(
    body: GoogleDriveSyncIn | None = None,
    workspace_id: str = Depends(require_workspace_membership),
):
    payload = body or GoogleDriveSyncIn()
    try:
        result = sync_google_drive(
            workspace_id=workspace_id,
            dataset=payload.dataset,
            folder_id=payload.folder_id,
        )
    except SyncConfigError as e:
        raise HTTPException(400, str(e)) from e
    return result.to_dict()


@router.delete("/google-drive")
def google_drive_disconnect(
    workspace_id: str = Depends(require_workspace_membership),
):
    row = db.get_connection(workspace_id, google_oauth.GOOGLE_DRIVE_PROVIDER)
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

    db.delete_connection(workspace_id, google_oauth.GOOGLE_DRIVE_PROVIDER)
    return {"deleted": True, "provider": google_oauth.GOOGLE_DRIVE_PROVIDER}


# ─────────────────────── Figma ───────────────────────


@router.get("/figma/authorize")
def figma_authorize(
    workspace_id: str = Depends(require_workspace_membership),
):
    if not figma_oauth.figma_configured():
        raise HTTPException(500, "Figma OAuth is not configured on the server")
    url = figma_oauth.authorize_url(
        state=figma_oauth.sign_oauth_state(workspace_id=workspace_id)
    )
    return RedirectResponse(url)


@router.get("/figma/callback")
def figma_callback(code: str, state: str):
    payload = figma_oauth.verify_oauth_state(state)
    workspace_id = payload["workspace_id"]
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
        workspace_id=workspace_id,
        provider=figma_oauth.FIGMA_PROVIDER,
        token_encrypted=token_encrypted,
        scopes=figma_oauth.DEFAULT_SCOPES,
        account_label=label,
        config_json=json.dumps({"user": me}) if me else "{}",
    )

    q = urlencode({"section": "connectors", "connected": figma_oauth.FIGMA_PROVIDER})
    return RedirectResponse(f"{settings.frontend_url.rstrip('/')}/settings?{q}")


@router.delete("/figma")
def figma_disconnect(
    workspace_id: str = Depends(require_workspace_membership),
):
    row = db.get_connection(workspace_id, figma_oauth.FIGMA_PROVIDER)
    if not row:
        raise HTTPException(404, "Figma is not connected")
    # Figma has no documented revoke endpoint; just drop our copy of the token.
    db.delete_connection(workspace_id, figma_oauth.FIGMA_PROVIDER)
    return {"deleted": True, "provider": figma_oauth.FIGMA_PROVIDER}


def _figma_access_token(workspace_id: str) -> str:
    """Decrypt the stored Figma token. Raises 404 if not connected."""
    row = db.get_connection(workspace_id, figma_oauth.FIGMA_PROVIDER)
    if not row:
        raise HTTPException(404, "Figma is not connected")
    try:
        token_json = json.loads(decrypt_token_json(row["token_json_encrypted"]))
    except (TokenEncryptionError, json.JSONDecodeError) as e:
        raise HTTPException(500, "Figma token unreadable") from e
    access_token = token_json.get("access_token")
    if not access_token:
        raise HTTPException(500, "Figma token has no access_token")
    return access_token


@router.get("/figma/files/{key}")
def figma_get_file(
    key: str,
    depth: int = 2,
    workspace_id: str = Depends(require_workspace_membership),
):
    """Fetch a Figma file's top-level structure. Used by Design Agent to
    extract frames/pages and to ground prototype generation in the team's
    actual canvases."""
    token = _figma_access_token(workspace_id)
    return figma_oauth.fetch_file(token, key, depth=depth)


@router.get("/figma/files/{key}/styles")
def figma_get_file_styles(
    key: str,
    workspace_id: str = Depends(require_workspace_membership),
):
    """Fetch published styles for a Figma file. Used by Design Agent to
    extract design tokens (colors, fonts, effects) for Scenario A
    (Figma-connected) prototype generation."""
    token = _figma_access_token(workspace_id)
    return figma_oauth.fetch_file_styles(token, key)


# ─────────────────────── GitHub (App, user-OAuth half) ───────────────────────


@router.get("/github/authorize")
def github_authorize(
    workspace_id: str = Depends(require_workspace_membership),
):
    if not github_app.github_oauth_configured():
        raise HTTPException(500, "GitHub OAuth is not configured on the server")
    url = github_app.authorize_url(
        state=github_app.sign_oauth_state(workspace_id=workspace_id)
    )
    return RedirectResponse(url)


@router.get("/github/callback")
def github_callback(code: str, state: str):
    payload = github_app.verify_oauth_state(state)
    workspace_id = payload["workspace_id"]
    token_json = github_app.exchange_code_for_token(code)
    access_token = token_json.get("access_token")
    if not access_token:
        raise HTTPException(400, "GitHub did not return an access_token")

    me = github_app.fetch_authenticated_user(access_token)
    label = me.get("login")
    if label:
        label = f"@{label}"

    try:
        token_encrypted = encrypt_token_json(github_app.token_payload_to_store(token_json))
    except TokenEncryptionError as e:
        raise HTTPException(500, str(e)) from e

    scopes = token_json.get("scope") or github_app.DEFAULT_SCOPES
    db.upsert_connection(
        workspace_id=workspace_id,
        provider=github_app.GITHUB_PROVIDER,
        token_encrypted=token_encrypted,
        scopes=scopes,
        account_label=label,
        config_json=json.dumps({"user": me}) if me else "{}",
    )

    q = urlencode({"section": "connectors", "connected": github_app.GITHUB_PROVIDER})
    return RedirectResponse(f"{settings.frontend_url.rstrip('/')}/settings?{q}")


@router.delete("/github")
def github_disconnect(
    workspace_id: str = Depends(require_workspace_membership),
):
    row = db.get_connection(workspace_id, github_app.GITHUB_PROVIDER)
    if not row:
        raise HTTPException(404, "GitHub is not connected")
    db.delete_connection(workspace_id, github_app.GITHUB_PROVIDER)
    return {"deleted": True, "provider": github_app.GITHUB_PROVIDER}


@router.get("/github/installations")
def github_list_installations(_session: dict = Depends(require_session)):
    return {"installations": db.list_github_installations()}


@router.get("/github/pull-requests")
def github_list_open_prs(
    installation_id: int | None = None,
    _session: dict = Depends(require_session),
):
    return {"pull_requests": db.list_open_pull_requests(installation_id)}


def _github_access_token(workspace_id: str) -> str:
    """Decrypt the stored GitHub user OAuth token. Raises 404 if not connected."""
    row = db.get_connection(workspace_id, github_app.GITHUB_PROVIDER)
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
    workspace_id: str = Depends(require_workspace_membership),
):
    """List repos the connected user can access. Engineer Agent uses this
    to discover the codebase context for a workspace; installation tokens
    will be used later for read-write operations."""
    token = _github_access_token(workspace_id)
    return {"repositories": github_app.fetch_user_repos(token, per_page=per_page)}


# ─────────────────────── ClickUp ───────────────────────
#
# Commit H. OAuth-only — no data sync into the corpus yet. Follow-on
# slice will add task → markdown sync similar to Drive's pattern.


@router.get("/clickup/callback")
def clickup_callback(code: str, state: str):
    payload = clickup_oauth.verify_oauth_state(state)
    workspace_id = payload["workspace_id"]
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
        workspace_id=workspace_id,
        provider=clickup_oauth.CLICKUP_PROVIDER,
        token_encrypted=token_encrypted,
        scopes="",
        account_label=label or None,
        config_json=json.dumps({"user": user}) if user else "{}",
    )

    q = urlencode({"section": "connectors", "connected": clickup_oauth.CLICKUP_PROVIDER})
    return RedirectResponse(f"{settings.frontend_url.rstrip('/')}/settings?{q}")


@router.delete("/clickup")
def clickup_disconnect(
    workspace_id: str = Depends(require_workspace_membership),
):
    row = db.get_connection(workspace_id, clickup_oauth.CLICKUP_PROVIDER)
    if not row:
        raise HTTPException(404, "ClickUp is not connected")
    db.delete_connection(workspace_id, clickup_oauth.CLICKUP_PROVIDER)
    return {"deleted": True, "provider": clickup_oauth.CLICKUP_PROVIDER}


# ─────────────────────── HubSpot ───────────────────────
#
# Commit I. OAuth-only — no corpus sync yet.


@router.get("/hubspot/callback")
def hubspot_callback(code: str, state: str):
    payload = hubspot_oauth.verify_oauth_state(state)
    workspace_id = payload["workspace_id"]
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
        workspace_id=workspace_id,
        provider=hubspot_oauth.HUBSPOT_PROVIDER,
        token_encrypted=token_encrypted,
        scopes=" ".join(info.get("scopes") or []) if isinstance(info.get("scopes"), list) else "",
        account_label=label or None,
        config_json=json.dumps({"info": info}) if info else "{}",
    )

    q = urlencode({"section": "connectors", "connected": hubspot_oauth.HUBSPOT_PROVIDER})
    return RedirectResponse(f"{settings.frontend_url.rstrip('/')}/settings?{q}")


@router.delete("/hubspot")
def hubspot_disconnect(
    workspace_id: str = Depends(require_workspace_membership),
):
    row = db.get_connection(workspace_id, hubspot_oauth.HUBSPOT_PROVIDER)
    if not row:
        raise HTTPException(404, "HubSpot is not connected")
    db.delete_connection(workspace_id, hubspot_oauth.HUBSPOT_PROVIDER)
    return {"deleted": True, "provider": hubspot_oauth.HUBSPOT_PROVIDER}


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
    workspace_id: str = Depends(require_workspace_membership),
):
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
        workspace_id=workspace_id,
        provider=fireflies_apikey.FIREFLIES_PROVIDER,
        token_encrypted=token_encrypted,
        scopes="",
        account_label=label,
        config_json=json.dumps({"user": user}) if user else "{}",
    )
    return {
        "ok": True,
        "provider": fireflies_apikey.FIREFLIES_PROVIDER,
        "account_label": label,
    }


@router.delete("/fireflies")
def fireflies_disconnect(
    workspace_id: str = Depends(require_workspace_membership),
):
    row = db.get_connection(workspace_id, fireflies_apikey.FIREFLIES_PROVIDER)
    if not row:
        raise HTTPException(404, "Fireflies is not connected")
    db.delete_connection(workspace_id, fireflies_apikey.FIREFLIES_PROVIDER)
    return {"deleted": True, "provider": fireflies_apikey.FIREFLIES_PROVIDER}


# ─────────────────────── GitHub webhook ───────────────────────

# We acknowledge anything we don't act on with 200 so GitHub doesn't
# keep retrying. Only signature failures + unparseable bodies 4xx.
_WEBHOOK_HANDLED_EVENTS = {
    "installation",
    "installation_repositories",
    "pull_request",
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
    else:
        logger.info("GitHub webhook: ignoring event %s delivery=%s", event, x_github_delivery)
        return {"ok": True, "event": event, "handled": False}
    return {"ok": True, "event": event, "handled": True}
