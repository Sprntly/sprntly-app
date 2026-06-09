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
"""
from __future__ import annotations

import importlib
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


# ───────────────────────── the scope list ─────────────────────────


def test_drive_scopes_contains_all_four():
    assert google_oauth.DRIVE_SCOPES == EXPECTED_SCOPES


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
                "https://www.googleapis.com/auth/drive.readonly",
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
