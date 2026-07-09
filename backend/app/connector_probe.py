"""Shared per-provider connector token probe.

The single source of truth for "is this connection's stored credential still
valid?". Both the on-open "Test connection" route (routes/connectors.py::
test_connection) and the scheduled connector health monitor (connector_health.py)
call this — so the per-provider validation logic lives in ONE place and can't
drift between the interactive check and the background sweep.

Each probe decrypts the stored token and runs the provider's cheap "who am I"
identity call (Drive is the exception: it only refreshes the token if expired,
never touching the Drive API). The return is:

    (True,  account_label)   — credential accepted; label is the resolved identity
    (False, error_detail)    — provider rejected the credential (reconnect needed)

A raised exception means a definitive provider rejection (mapped from the route's
400 path) OR an unreadable stored token; the scheduled job catches those and
applies its own fail-open policy, while the route maps them to HTTP status codes.
"""
from __future__ import annotations

import json
import logging

from google.auth.transport.requests import Request as GoogleAuthRequest

from app.connectors import (
    clickup_oauth,
    figma_oauth,
    fireflies_apikey,
    github_app,
    google_oauth,
    hubspot_oauth,
    jira_oauth,
    slack_oauth,
)
from app.connectors.tokens import (
    TokenEncryptionError,
    decrypt_token_json,
    encrypt_token_json,
)

logger = logging.getLogger(__name__)


class ProbeError(Exception):
    """A definitive probe failure (token unreadable or provider hard-rejected).

    Carries a stable ``reason`` the caller maps to its own surface:
    ``"unreadable"`` (decrypt/parse failed → route 500) vs ``"rejected"``
    (provider rejected the credential → route 400)."""

    def __init__(self, message: str, *, reason: str):
        super().__init__(message)
        self.reason = reason


def _label_from_user(user_obj: dict) -> str:
    """Resolve the generic account label from a provider identity payload.
    Mirrors the field-precedence the test_connection route has always used."""
    return str(
        user_obj.get("email")
        or user_obj.get("user")
        or user_obj.get("username")
        or user_obj.get("login")
        or user_obj.get("handle")
        or user_obj.get("name")
        or ""
    )


def probe_connection(provider: str, row: dict) -> tuple[bool, str]:
    """Re-validate ``row``'s stored credential for ``provider``.

    Returns ``(healthy, detail)`` where ``detail`` is the resolved account
    label on success and a short error string on a soft rejection. Raises
    ``ProbeError`` on an unreadable token (reason="unreadable") or a hard
    provider rejection (reason="rejected") so the route can preserve its
    existing 500/400 behavior; the scheduled job catches everything and
    fails open.

    Drive keeps its long-standing behavior: prove the token chain by
    refreshing ONLY when expired (no Drive API call).
    """
    try:
        token_json = json.loads(decrypt_token_json(row["token_json_encrypted"]))
    except (TokenEncryptionError, json.JSONDecodeError) as e:
        raise ProbeError("Stored token unreadable", reason="unreadable") from e

    user_obj: dict = {}

    if provider == google_oauth.GOOGLE_DRIVE_PROVIDER:
        # Drive: prove the token chain is healthy by attempting refresh.
        try:
            creds = google_oauth.credentials_from_token_json(json.dumps(token_json))
            if creds.expired and creds.refresh_token:
                creds.refresh(GoogleAuthRequest())
            user_obj = {
                "email": row.get("google_email") or row.get("account_label") or "",
            }
        except Exception as e:  # noqa: BLE001 — surface as a hard rejection
            raise ProbeError(
                f"Google Drive token rejected: {e}", reason="rejected"
            ) from e
        # Drive's refresh path never returns empty when it succeeds; label may
        # be "" if we never stored an email, which is fine (still healthy).
        return True, _label_from_user(user_obj)
    elif provider == figma_oauth.FIGMA_PROVIDER:
        access_token = token_json.get("access_token") or ""
        user_obj = figma_oauth.fetch_me(access_token) or {}
    elif provider == github_app.GITHUB_PROVIDER:
        # Company-shared connector: health reflects the App INSTALLATION token
        # (self-minting, no 8h clock), not a member's personal OAuth token.
        # Fall back to the personal token only when no install exists yet.
        from app import db

        for inst in db.list_github_installations(row.get("company_id") or ""):
            if inst.get("suspended"):
                continue
            try:
                github_app.get_installation_token(int(inst["installation_id"]))
            except Exception:
                continue
            user_obj = {
                "login": inst.get("account_login") or row.get("account_label") or "",
            }
            break
        if not user_obj:
            access_token = token_json.get("access_token") or ""
            user_obj = github_app.fetch_authenticated_user(access_token) or {}
    elif provider == clickup_oauth.CLICKUP_PROVIDER:
        access_token = token_json.get("access_token") or ""
        user_obj = clickup_oauth.fetch_authenticated_user(access_token) or {}
    elif provider == hubspot_oauth.HUBSPOT_PROVIDER:
        access_token = token_json.get("access_token") or ""
        user_obj = hubspot_oauth.fetch_token_info(access_token) or {}
    elif provider == jira_oauth.JIRA_PROVIDER:
        # Jira access tokens expire in ~1h. Refresh (and persist) an expired
        # token before probing, so a connection stays "healthy" past the first
        # hour. Persisting is REQUIRED: Atlassian rotates refresh tokens, so a
        # throwaway refresh would strand the stored one.
        import time

        from app import db

        obtained_at = token_json.get("obtained_at", 0)
        expires_in = token_json.get("expires_in", 3600)
        refresh_token = token_json.get("refresh_token")
        if refresh_token and time.time() > obtained_at + expires_in - 120:
            try:
                new_json = jira_oauth.refresh_access_token(refresh_token)
                token_json = json.loads(jira_oauth.token_payload_to_store(new_json))
                db.update_connection_tokens(
                    row.get("company_id") or "",
                    jira_oauth.JIRA_PROVIDER,
                    encrypt_token_json(json.dumps(token_json)),
                )
            except jira_oauth.JiraAuthExpiredError as e:
                raise ProbeError(f"Jira token rejected: {e}", reason="rejected") from e
            except Exception:  # noqa: BLE001 — non-auth refresh error → treat as soft
                logger.warning("Jira probe refresh failed", exc_info=True)
        access_token = token_json.get("access_token") or ""
        cloud_id = (json.loads(row.get("config_json") or "{}")).get("cloud_id") \
            or jira_oauth.first_cloud_id(access_token)
        raw_user = (
            jira_oauth.fetch_authenticated_user(access_token, cloud_id)
            if cloud_id else {}
        )
        # Normalize Jira's field names onto the keys _label_from_user expects.
        if raw_user:
            user_obj = {
                "email": raw_user.get("emailAddress"),
                "name": raw_user.get("displayName"),
            }
    elif provider == slack_oauth.SLACK_PROVIDER:
        access_token = token_json.get("access_token") or ""
        # Canonical token-validity check: team.info returns {id, name, domain},
        # so the account_label resolves to the Slack workspace name.
        user_obj = slack_oauth.fetch_team_info(access_token) or {}
    elif provider == fireflies_apikey.FIREFLIES_PROVIDER:
        api_key = token_json.get("api_key") or ""
        user_obj = fireflies_apikey.fetch_authenticated_user(api_key) or {}
    else:
        raise ProbeError(
            f"Probe not supported for provider {provider!r}", reason="unsupported"
        )

    if not user_obj:
        # Empty identity payload = provider rejected the credential.
        return False, f"{provider} rejected the stored credential"

    return True, _label_from_user(user_obj)
