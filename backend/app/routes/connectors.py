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

  GET    /v1/connectors/slack/callback           -> OAuth callback
  DELETE /v1/connectors/slack                   -> disconnect
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

import json
import logging
from typing import Annotated
from urllib.parse import urlencode

from fastapi import APIRouter, Depends, Header, HTTPException, Request
from fastapi.responses import RedirectResponse
from google.auth.transport.requests import Request as GoogleAuthRequest
from pydantic import BaseModel

from app import db
from app import datasets as datasets_service
from app.auth import CompanyContext, require_company, require_session
from app.config import settings
from app.connectors import (
    clickup_oauth,
    figma_oauth,
    figma_pat,
    fireflies_apikey,
    github_app,
    google_oauth,
    hubspot_oauth,
    slack_oauth,
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
from app.kg_ingest.auto_sync import kickoff_sync

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
    company: CompanyContext = Depends(require_company),
):
    rows = db.list_connections(company.company_id)
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

    rows = db.list_connections(company.company_id)
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
    """Construct the post-callback redirect URL using state.return_to if
    present, else the default settings URL. Both forms get
    `?connected=<provider>` appended (or `&connected=` if return_to
    already has a query string)."""
    return_to = payload.get("return_to")
    frontend = settings.frontend_url.rstrip("/")
    if return_to and _is_safe_return_to(return_to):
        sep = "&" if "?" in return_to else "?"
        target = f"{frontend}{return_to}{sep}connected={provider}"
    else:
        q = urlencode({"section": "connectors", "connected": provider})
        target = f"{frontend}/settings?{q}"
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
        url = slack_oauth.authorize_url(
            state=slack_oauth.sign_oauth_state(
                company_id=company.company_id, return_to=return_to,
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

    row = db.get_connection(company.company_id, provider)
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
    elif provider == slack_oauth.SLACK_PROVIDER:
        access_token = token_json.get("access_token") or ""
        # Canonical token-validity check: team.info returns {id, name, domain},
        # so the account_label below resolves to the Slack workspace name.
        user_obj = slack_oauth.fetch_team_info(access_token) or {}
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
        scopes=google_oauth.DRIVE_READONLY_SCOPE,
        google_email=email,
        config_json=json.dumps(config),
    )

    return _build_post_oauth_redirect(payload, google_oauth.GOOGLE_DRIVE_PROVIDER)


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
    company: CompanyContext = Depends(require_company),
):
    try:
        return browse_folders(company.company_id, parent_id)
    except SyncConfigError as e:
        logger.warning("Drive folder browse failed: %s", e)
        raise HTTPException(400, str(e)) from e


@router.post("/google-drive/config")
def google_drive_config(
    body: GoogleDriveConfigIn,
    company: CompanyContext = Depends(require_company),
):
    row = db.get_connection(company.company_id, google_oauth.GOOGLE_DRIVE_PROVIDER)
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
    company: CompanyContext = Depends(require_company),
):
    payload = body or GoogleDriveSyncIn()
    try:
        result = sync_google_drive(
            company_id=company.company_id,
            dataset=payload.dataset,
            folder_id=payload.folder_id,
        )
    except SyncConfigError as e:
        raise HTTPException(400, str(e)) from e

    # Auto-enable the Google Drive input source for this dataset.
    dataset_slug = payload.dataset
    if not dataset_slug:
        row = db.get_connection(google_oauth.GOOGLE_DRIVE_PROVIDER)
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
            logger.warning("Failed to auto-enable google_drive input source", exc_info=True)

    return result.to_dict()


@router.delete("/google-drive")
def google_drive_disconnect(
    company: CompanyContext = Depends(require_company),
):
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


# ─────────── Figma Personal Access Token (PAT) ───────────
#
# Stopgap auth path while the Sprntly public OAuth app is in Figma's
# review queue. Customers paste a PAT from Figma → Account settings →
# Personal Access Tokens; we validate by hitting /v1/me, then store the
# PAT in connections.token_json_encrypted (same column OAuth tokens use).


class FigmaPatIn(BaseModel):
    pat: str

    def model_post_init(self, _context) -> None:
        if not self.pat or not self.pat.strip():
            raise ValueError("pat cannot be empty")


@router.post("/figma/pat")
def figma_connect_pat(
    body: FigmaPatIn,
    company: CompanyContext = Depends(require_company),
):
    pat = body.pat.strip()
    user = figma_pat.fetch_me(pat)
    if not user:
        raise HTTPException(
            400,
            "Figma rejected this Personal Access Token — double-check "
            "the value at Figma → Account settings → Personal Access Tokens.",
        )

    label = user.get("handle") or user.get("email") or "Figma user"

    try:
        token_encrypted = encrypt_token_json(
            figma_pat.token_payload_to_store(pat)
        )
    except TokenEncryptionError as e:
        raise HTTPException(500, str(e)) from e

    db.upsert_connection(
        company_id=company.company_id,
        provider=figma_pat.FIGMA_PROVIDER,
        token_encrypted=token_encrypted,
        scopes="",
        account_label=label,
        config_json=json.dumps({"user": user, "auth_kind": "pat"}),
    )
    return {
        "ok": True,
        "provider": figma_pat.FIGMA_PROVIDER,
        "account_label": label,
    }


@router.delete("/figma")
def figma_disconnect(
    company: CompanyContext = Depends(require_company),
):
    row = db.get_connection(company.company_id, figma_oauth.FIGMA_PROVIDER)
    if not row:
        raise HTTPException(404, "Figma is not connected")
    # Figma has no documented revoke endpoint; just drop our copy of the token.
    db.delete_connection(company.company_id, figma_oauth.FIGMA_PROVIDER)
    return {"deleted": True, "provider": figma_oauth.FIGMA_PROVIDER}


def _figma_access_token(company_id: str) -> str:
    """Decrypt the stored Figma token. Raises 404 if not connected."""
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
    _session: dict = Depends(require_session),
):
    """Sync Figma file structure and design tokens into the corpus.

    Fetches file tree + published styles and writes a markdown summary
    into DATA_DIR/{dataset}/figma_design_context.md.
    """
    token = _figma_access_token()

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
    # post-install redirect (when 'Request OAuth during install' is on,
    # or when the App's Setup URL is left blank). The post-install
    # redirect has `setup_action` + `installation_id` but no `state`.
    # If we hit this branch, OAuth has either already completed in a
    # prior round or wasn't required — just acknowledge and bounce
    # back to the connectors page with the setup_action carried in the
    # query so the UI can show 'approval pending' vs 'install complete'.
    if state is None or not code:
        base = (settings.frontend_url or "http://localhost:3000").rstrip("/")
        params = {"section": "connectors", "connected": "github"}
        if setup_action:
            params["setup_action"] = setup_action
        if installation_id is not None:
            params["installation_id"] = str(installation_id)
        return RedirectResponse(
            f"{base}/settings?{urlencode(params)}", status_code=307
        )

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
    # the App settings on GitHub).
    if login and not _has_github_install_for(login) and settings.github_app_slug:
        install_url = (
            f"https://github.com/apps/{settings.github_app_slug}/installations/new"
        )
        return RedirectResponse(install_url, status_code=307)

    return _build_post_oauth_redirect(payload, github_app.GITHUB_PROVIDER)


def _has_github_install_for(account_login: str) -> bool:
    """True iff a Sprntly App installation already exists for the given
    GitHub account login. Read-only — webhook handlers populate this
    table when users install/uninstall the App."""
    try:
        rows = db.list_github_installations() or []
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
    row = db.get_connection(company.company_id, github_app.GITHUB_PROVIDER)
    if not row:
        raise HTTPException(404, "GitHub is not connected")
    db.delete_connection(company.company_id, github_app.GITHUB_PROVIDER)
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


class GitHubSyncCorpusIn(BaseModel):
    dataset: str
    installation_id: int | None = None


@router.post("/github/sync-to-corpus")
def github_sync_to_corpus(
    body: GitHubSyncCorpusIn,
    _session: dict = Depends(require_session),
):
    """Sync tracked GitHub PRs into the corpus as a markdown file.

    Reads open PRs from the github_pull_requests table and writes
    a summary into DATA_DIR/{dataset}/github_active_prs.md.
    """
    prs = db.list_open_pull_requests(body.installation_id)

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
def connector_sync_status(_session: dict = Depends(require_session)):
    """Summary of all connector sync states + corpus stats.

    Returns per-connector status and per-dataset corpus size.
    Used for demo dashboards to verify data capture.
    """
    connections = db.list_connections()
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
    _session: dict = Depends(require_session),
):
    """Sync HubSpot CRM data (contacts, companies, deals) into the corpus.

    Fetches data from HubSpot API, converts to markdown, and writes
    into DATA_DIR/{dataset}/ so it enters the knowledge base.
    """
    from app.connectors.hubspot_sync import HubSpotSyncError, sync_hubspot

    try:
        result = sync_hubspot(body.dataset)
    except HubSpotSyncError as e:
        raise HTTPException(400, str(e)) from e
    return result.to_dict()


@router.post("/hubspot/sync-to-corpus")
def hubspot_sync_to_corpus(
    body: HubSpotSyncCorpusIn,
    _session: dict = Depends(require_session),
):
    """Alias for /hubspot/sync — matches Figma/GitHub sync-to-corpus pattern."""
    from app.connectors.hubspot_sync import HubSpotSyncError, sync_hubspot

    try:
        result = sync_hubspot(body.dataset)
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

    db.upsert_connection(
        company_id=company_id,
        provider=slack_oauth.SLACK_PROVIDER,
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
    row = db.get_connection(company.company_id, slack_oauth.SLACK_PROVIDER)
    if not row:
        raise HTTPException(404, "Slack is not connected")
    db.delete_connection(company.company_id, slack_oauth.SLACK_PROVIDER)
    return {"deleted": True, "provider": slack_oauth.SLACK_PROVIDER}


def _slack_bot_token(company_id: str) -> tuple[str, dict]:
    """Decrypt and return (bot_token, connection_row) for the company's
    Slack connection. 404 if not connected, 500 if the token is unreadable."""
    row = db.get_connection(company_id, slack_oauth.SLACK_PROVIDER)
    if not row:
        raise HTTPException(404, "Slack is not connected")
    try:
        token_json = json.loads(decrypt_token_json(row["token_json_encrypted"]))
    except (TokenEncryptionError, json.JSONDecodeError) as e:
        raise HTTPException(500, "Slack token unreadable") from e
    bot_token = token_json.get("access_token")
    if not bot_token:
        raise HTTPException(500, "Slack token has no bot access_token")
    return bot_token, row


class SlackBotTokenIn(BaseModel):
    api_key: str

    def model_post_init(self, _context) -> None:
        if not self.api_key or not self.api_key.strip():
            raise ValueError("api_key cannot be empty")


@router.post("/slack/apikey")
def slack_connect_bot_token(
    body: SlackBotTokenIn,
    _session: dict = Depends(require_session),
):
    """Connect Slack using a Bot User OAuth Token (xoxb-...).

    Alternative to the full OAuth flow — useful when the Slack app is not
    distributed. The user copies the token from api.slack.com/apps →
    Install App → Bot User OAuth Token.
    """
    token = body.api_key.strip()
    auth_info = slack_oauth.fetch_auth_test(token)
    if not auth_info:
        raise HTTPException(
            400,
            "Slack rejected this token — verify the Bot User OAuth Token "
            "at api.slack.com/apps → Install App.",
        )

    label = (
        auth_info.get("user")
        or auth_info.get("team")
        or "Slack workspace"
    )

    payload = json.dumps({
        "access_token": token,
        "token_type": "bot",
        "team_id": auth_info.get("team_id"),
        "team_name": auth_info.get("team"),
        "bot_user_id": auth_info.get("user_id"),
        "obtained_at": int(__import__("time").time()),
    })

    try:
        token_encrypted = encrypt_token_json(payload)
    except TokenEncryptionError as e:
        raise HTTPException(500, str(e)) from e

    db.upsert_connection(
        provider=slack_oauth.SLACK_PROVIDER,
        token_encrypted=token_encrypted,
        scopes=settings.slack_scopes.replace(",", " "),
        account_label=label,
        config_json=json.dumps({"auth_info": auth_info}) if auth_info else "{}",
    )
    return {
        "ok": True,
        "provider": slack_oauth.SLACK_PROVIDER,
        "account_label": label,
    }


@router.get("/slack/channels")
def slack_list_channels(
    company: CompanyContext = Depends(require_company),
):
    """List channels the bot can post into. Backs the channel-picker
    in the Configure drawer."""
    token, _row = _slack_bot_token(company.company_id)
    return {"channels": slack_oauth.list_channels(token)}


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
    the connection row's config so the Comms Agent can read it at
    post-time without a separate lookup table."""
    row = db.get_connection(company.company_id, slack_oauth.SLACK_PROVIDER)
    if not row:
        raise HTTPException(404, "Slack is not connected")
    patch: dict = {"channel_id": body.channel_id.strip()}
    if body.channel_name:
        patch["channel_name"] = body.channel_name.strip()
    updated = db.patch_connection_config(
        company.company_id, slack_oauth.SLACK_PROVIDER, patch
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
    _session: dict = Depends(require_session),
):
    """Sync Slack channels, messages, and threads into the corpus.

    Fetches data from the Slack API, converts to markdown, and writes
    into DATA_DIR/{dataset}/ so it enters the knowledge base.
    """
    from app.connectors.slack_sync import SlackSyncError, sync_slack

    try:
        result = sync_slack(body.dataset, history_days=body.history_days)
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
    generation then re-extracts instead of serving a now-outdated cached row."""
    repo = payload.get("repository") or {}
    repo_full_name = str(repo.get("full_name") or "").strip()
    if not repo_full_name:
        return
    db.mark_github_design_systems_stale(repo_full_name)


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
    elif event == "push":
        _handle_push_event(payload)
    else:
        logger.info("GitHub webhook: ignoring event %s delivery=%s", event, x_github_delivery)
        return {"ok": True, "event": event, "handled": False}
    return {"ok": True, "event": event, "handled": True}
