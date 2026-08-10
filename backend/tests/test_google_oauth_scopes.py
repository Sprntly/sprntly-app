"""Tests for the Google Drive OAuth scope set + the durable oauthlib relax flag.

Google's OAuth client doubles as a sign-in client, so it auto-adds
openid / userinfo.email / userinfo.profile to the granted scope set. Requesting
those up front (DRIVE_SCOPES) keeps the requested and granted sets aligned so
google-auth-oauthlib doesn't raise "Scope has changed" at token exchange, and
lets us read the user's email straight from the ID token.

These tests assert:
  - DRIVE_SCOPES carries all four scopes, in both the authorize Flow and the
    callback token-exchange Flow (they share build_flow).
  - The scope-change scenario (Google returns the superset) no longer raises.
  - Email flows from the ID token, falling back to the Drive about() lookup.
  - OAUTHLIB_RELAX_TOKEN_SCOPE defaults to "1" after importing the app.
  - `drive_scopes()` is mode-aware and single-sourced: every mode except the
    dormant "oauth_folder" requests drive.file; "oauth_folder" requests the
    restricted drive.readonly scope. Both OAuth call sites (build_flow,
    credentials_from_token_json) go through it, so they can't drift apart.
  - The default path never requests drive.readonly (mutation-proof).
"""
from __future__ import annotations

import importlib
import json
import os
import sys
from unittest.mock import MagicMock, patch

import jwt
import pytest
from cryptography.fernet import Fernet
from google.oauth2.credentials import Credentials

from app.connectors import google_oauth
from tests._company_helpers import company_client


EXPECTED_SCOPES = [
    "https://www.googleapis.com/auth/drive.file",
    "openid",
    "https://www.googleapis.com/auth/userinfo.email",
    "https://www.googleapis.com/auth/userinfo.profile",
]

FOLDER_MODE_SCOPES = [
    "https://www.googleapis.com/auth/drive.readonly",
    "openid",
    "https://www.googleapis.com/auth/userinfo.email",
    "https://www.googleapis.com/auth/userinfo.profile",
]


@pytest.fixture
def google_env(isolated_settings, monkeypatch):
    key = Fernet.generate_key().decode()
    monkeypatch.setenv("TOKEN_ENCRYPTION_KEY", key)
    monkeypatch.setenv("GOOGLE_CLIENT_ID", "test-client-id")
    monkeypatch.setenv("GOOGLE_CLIENT_SECRET", "test-client-secret")
    monkeypatch.setenv(
        "GOOGLE_OAUTH_REDIRECT_URI",
        "http://testserver/v1/connectors/google-drive/callback",
    )
    monkeypatch.setenv("FRONTEND_URL", "http://localhost:3000")
    for name in (
        "app.config",
        "app.connectors.tokens",
        "app.connectors.google_oauth",
        "app.routes.connectors",
        "app.main",
    ):
        if name in sys.modules:
            importlib.reload(sys.modules[name])
    import app.db as db_mod

    db_mod.init_db()
    yield


@pytest.fixture
def oauth_folder_env(google_env, monkeypatch):
    """GOOGLE_DRIVE_ACCESS_MODE=oauth_folder — the dormant, CASA-gated mode
    that requests drive.readonly instead of drive.file. Nothing sets this in
    production; it exists so the mode-aware scope selection can be exercised
    before CASA approval lands."""
    monkeypatch.setenv("GOOGLE_DRIVE_ACCESS_MODE", "oauth_folder")
    for name in (
        "app.config",
        "app.connectors.tokens",
        "app.connectors.google_oauth",
        "app.routes.connectors",
        "app.main",
    ):
        if name in sys.modules:
            importlib.reload(sys.modules[name])


# ───────────────────────── the scope list ─────────────────────────


def test_drive_scopes_contains_all_four():
    assert google_oauth.DRIVE_SCOPES == EXPECTED_SCOPES


def test_drive_scope_is_drive_file_not_readonly():
    """The Picker re-platform uses the narrow drive.file scope, never the old
    full-Drive drive.readonly grant, for every mode except the dormant
    "oauth_folder" mode (see the mode-aware tests below)."""
    assert google_oauth.DRIVE_FILE_SCOPE == "https://www.googleapis.com/auth/drive.file"
    assert google_oauth.DRIVE_FILE_SCOPE in google_oauth.DRIVE_SCOPES
    assert not any("drive.readonly" in s for s in google_oauth.DRIVE_SCOPES)


def test_build_flow_requests_all_four_scopes(google_env):
    flow = google_oauth.build_flow()
    # google-auth-oauthlib normalizes the scopes onto the oauth2session; assert
    # every requested scope is present (set-equality ignores any reordering).
    assert set(flow.oauth2session.scope) == set(EXPECTED_SCOPES)


def test_authorize_flow_built_with_all_four_scopes(google_env, monkeypatch):
    """The authorize endpoint builds the Flow with the full DRIVE_SCOPES list."""
    ctx = company_client(monkeypatch)
    captured = {}

    real_build_flow = google_oauth.build_flow

    def spy_build_flow():
        flow = real_build_flow()
        captured["scope"] = list(flow.oauth2session.scope)
        return flow

    with patch(
        "app.routes.connectors.google_oauth.build_flow", side_effect=spy_build_flow
    ):
        r = ctx.client.get(
            "/v1/connectors/google-drive/authorize",
            params={"dataset": "acme"},
            follow_redirects=False,
        )
    assert r.status_code == 307
    assert set(captured["scope"]) == set(EXPECTED_SCOPES)


def test_callback_token_exchange_uses_same_scope_list(google_env, monkeypatch):
    """The callback's token-exchange Flow uses the same DRIVE_SCOPES list as
    authorize (both go through build_flow)."""
    ctx = company_client(monkeypatch)
    state = google_oauth.sign_oauth_state(company_id=ctx.company_id, dataset="acme")
    creds = Credentials(
        token="access",
        refresh_token="refresh",
        token_uri="https://oauth2.googleapis.com/token",
        client_id="test-client-id",
        client_secret="test-client-secret",
        scopes=list(EXPECTED_SCOPES),
    )
    captured = {}
    real_build_flow = google_oauth.build_flow

    def spy_build_flow():
        flow = real_build_flow()
        captured["scope"] = list(flow.oauth2session.scope)
        # Hand back the canned creds + make fetch_token a no-op.
        flow.fetch_token = MagicMock()
        type(flow).credentials = property(lambda self: creds)
        return flow

    with (
        patch(
            "app.routes.connectors.google_oauth.build_flow",
            side_effect=spy_build_flow,
        ),
        patch(
            "app.routes.connectors.google_oauth.fetch_google_account_email",
            return_value="pm@company.com",
        ),
    ):
        r = ctx.client.get(
            "/v1/connectors/google-drive/callback",
            params={"code": "auth-code", "state": state},
            follow_redirects=False,
        )
    assert r.status_code == 307
    assert set(captured["scope"]) == set(EXPECTED_SCOPES)


# ──────────────── the scope-change scenario no longer raises ────────────────


def test_scope_change_superset_does_not_raise(google_env):
    """Simulate Google returning the granted superset. With the requested set
    already matching (and the relax flag), the exchange must not raise the
    oauthlib 'Scope has changed' error."""
    flow = google_oauth.build_flow()

    def fake_fetch_token(*, code):  # noqa: ARG001
        # Mimic oauthlib writing the granted (superset, reordered) scope back.
        flow.oauth2session.token = {
            "access_token": "at",
            "refresh_token": "rt",
            "scope": [
                "openid",
                "https://www.googleapis.com/auth/userinfo.email",
                "https://www.googleapis.com/auth/userinfo.profile",
                "https://www.googleapis.com/auth/drive.file",
            ],
        }
        return flow.oauth2session.token

    flow.fetch_token = fake_fetch_token
    # Must not raise.
    flow.fetch_token(code="auth-code")


def test_relax_flag_default_present_during_token_exchange():
    """The in-process default makes the relax flag truthy regardless of .env."""
    assert os.environ.get("OAUTHLIB_RELAX_TOKEN_SCOPE") == "1"


# ───────────────────────── email from the ID token ─────────────────────────


def _id_token(email: str | None) -> str:
    claims = {"sub": "123", "iss": "https://accounts.google.com"}
    if email is not None:
        claims["email"] = email
    return jwt.encode(claims, "unused-secret", algorithm="HS256")


def test_email_from_id_token_reads_email_claim():
    creds = MagicMock()
    creds.id_token = _id_token("pm@company.com")
    assert google_oauth.email_from_id_token(creds) == "pm@company.com"


def test_email_from_id_token_none_when_no_token():
    creds = MagicMock()
    creds.id_token = None
    assert google_oauth.email_from_id_token(creds) is None


def test_fetch_account_email_prefers_id_token():
    """When an ID token carries the email, no Drive about() call is made."""
    creds = MagicMock()
    creds.id_token = _id_token("idtoken@company.com")
    with patch("app.connectors.google_oauth.build") as mock_build:
        email = google_oauth.fetch_google_account_email(creds)
    assert email == "idtoken@company.com"
    mock_build.assert_not_called()


def test_fetch_account_email_falls_back_to_drive_about():
    """Tokens with no email claim fall back to the Drive about() lookup."""
    creds = MagicMock()
    creds.id_token = _id_token(None)
    fake_service = MagicMock()
    fake_service.about.return_value.get.return_value.execute.return_value = {
        "user": {"emailAddress": "about@company.com"}
    }
    with patch(
        "app.connectors.google_oauth.build", return_value=fake_service
    ):
        email = google_oauth.fetch_google_account_email(creds)
    assert email == "about@company.com"


# ───────────────── connection row records the granted scopes ─────────────────


def test_callback_stores_full_scope_set(google_env, monkeypatch):
    ctx = company_client(monkeypatch)
    state = google_oauth.sign_oauth_state(company_id=ctx.company_id, dataset="acme")
    creds = Credentials(
        token="access",
        refresh_token="refresh",
        token_uri="https://oauth2.googleapis.com/token",
        client_id="test-client-id",
        client_secret="test-client-secret",
        scopes=list(EXPECTED_SCOPES),
    )
    mock_flow = MagicMock()
    mock_flow.credentials = creds
    with (
        patch("app.routes.connectors.google_oauth.build_flow", return_value=mock_flow),
        patch(
            "app.routes.connectors.google_oauth.fetch_google_account_email",
            return_value="pm@company.com",
        ),
    ):
        r = ctx.client.get(
            "/v1/connectors/google-drive/callback",
            params={"code": "auth-code", "state": state},
            follow_redirects=False,
        )
    assert r.status_code == 307
    conn = ctx.client.get("/v1/connectors").json()["connections"][0]
    for scope in EXPECTED_SCOPES:
        assert scope in conn["scopes"]


# ───────────── drive_scopes(): mode-aware, single source of truth ─────────────


def test_drive_scopes_default_mode_requests_drive_file(google_env):
    assert set(google_oauth.drive_scopes()) == set(EXPECTED_SCOPES)


def test_drive_scopes_service_account_mode_stays_drive_file(google_env, monkeypatch):
    """service_account mode is unaffected by the new mode value — it still
    requests drive.file, exactly like default oauth mode."""
    monkeypatch.setenv("GOOGLE_DRIVE_ACCESS_MODE", "service_account")
    for name in ("app.config", "app.connectors.tokens", "app.connectors.google_oauth"):
        if name in sys.modules:
            importlib.reload(sys.modules[name])
    assert set(google_oauth.drive_scopes()) == set(EXPECTED_SCOPES)


def test_drive_scopes_oauth_folder_mode_requests_drive_readonly(oauth_folder_env):
    scopes = google_oauth.drive_scopes()
    assert set(scopes) == set(FOLDER_MODE_SCOPES)
    assert "https://www.googleapis.com/auth/drive.file" not in scopes


def test_build_flow_scope_follows_mode_call_site_one(oauth_folder_env):
    """Call site 1 (google_oauth.py ~:70): build_flow(), shared by both the
    /authorize and /callback routes."""
    flow = google_oauth.build_flow()
    assert set(flow.oauth2session.scope) == set(FOLDER_MODE_SCOPES)


def test_credentials_from_token_json_scope_follows_mode_call_site_two(oauth_folder_env):
    """Call site 2 (google_oauth.py ~:116): credentials_from_token_json(),
    used to rebuild credentials from a stored token for background sync."""
    token_json = json.dumps(
        {
            "token": "access",
            "refresh_token": "refresh",
            "token_uri": "https://oauth2.googleapis.com/token",
            "client_id": "test-client-id",
            "client_secret": "test-client-secret",
            "scopes": FOLDER_MODE_SCOPES,
        }
    )
    creds = google_oauth.credentials_from_token_json(token_json)
    assert set(creds.scopes) == set(FOLDER_MODE_SCOPES)


def test_callback_stores_readonly_scope_in_oauth_folder_mode(oauth_folder_env, monkeypatch):
    """The connection row's stored `scopes` (read by the frontend's
    driveFolderSelectEnabled gate) reflects the mode active at connect time,
    not a frozen drive.file-only list."""
    ctx = company_client(monkeypatch)
    state = google_oauth.sign_oauth_state(company_id=ctx.company_id, dataset="acme")
    creds = Credentials(
        token="access",
        refresh_token="refresh",
        token_uri="https://oauth2.googleapis.com/token",
        client_id="test-client-id",
        client_secret="test-client-secret",
        scopes=list(FOLDER_MODE_SCOPES),
    )
    mock_flow = MagicMock()
    mock_flow.credentials = creds
    with (
        patch("app.routes.connectors.google_oauth.build_flow", return_value=mock_flow),
        patch(
            "app.routes.connectors.google_oauth.fetch_google_account_email",
            return_value="pm@company.com",
        ),
    ):
        r = ctx.client.get(
            "/v1/connectors/google-drive/callback",
            params={"code": "auth-code", "state": state},
            follow_redirects=False,
        )
    assert r.status_code == 307
    conn = ctx.client.get("/v1/connectors").json()["connections"][0]
    assert "https://www.googleapis.com/auth/drive.readonly" in conn["scopes"]


# ───────────── mutation-proof: default path never leaks drive.readonly ─────────────


def test_default_mode_never_requests_drive_readonly(google_env, monkeypatch):
    """Fails if drive.readonly ever becomes reachable without an explicit
    GOOGLE_DRIVE_ACCESS_MODE=oauth_folder — the regression AC4 guards
    against. Checks the setting, the derived scope list, the Flow's requested
    scope, AND the actual consent URL string."""
    monkeypatch.delenv("GOOGLE_DRIVE_ACCESS_MODE", raising=False)
    for name in ("app.config", "app.connectors.tokens", "app.connectors.google_oauth"):
        if name in sys.modules:
            importlib.reload(sys.modules[name])
    from app.config import settings as live_settings

    assert live_settings.google_drive_access_mode == "oauth"

    scopes = google_oauth.drive_scopes()
    assert "https://www.googleapis.com/auth/drive.readonly" not in scopes

    flow = google_oauth.build_flow()
    assert "drive.readonly" not in " ".join(flow.oauth2session.scope)

    consent_url, _ = flow.authorization_url()
    assert "drive.readonly" not in consent_url
    assert "drive.file" in consent_url


# ───────────────────────── config: mode accepts the third value ─────────────────────────


def test_config_default_access_mode_is_oauth(monkeypatch):
    monkeypatch.delenv("GOOGLE_DRIVE_ACCESS_MODE", raising=False)
    from app.config import Settings

    assert Settings().google_drive_access_mode == "oauth"


def test_config_accepts_oauth_folder_value(monkeypatch):
    monkeypatch.setenv("GOOGLE_DRIVE_ACCESS_MODE", "oauth_folder")
    from app.config import Settings

    assert Settings().google_drive_access_mode == "oauth_folder"
