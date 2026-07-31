"""Route tests for the Marvin connector.

Marvin is the first connector whose reads go over MCP rather than REST, and
the first whose OAuth client is self-registered at connect time. Both show up
in the routes: start-oauth carries a region, and the callback treats the MCP
handshake as a hard gate rather than a best-effort label lookup.

All outbound HTTP is mocked; nothing here touches heymarvin.com.
"""
from __future__ import annotations

import importlib
import json
import sys
from unittest.mock import MagicMock, patch

import pytest
from cryptography.fernet import Fernet

from tests._company_helpers import company_client, seed_connection

REDIRECT_URI = "http://testserver/v1/connectors/marvin/callback"

_METADATA = {
    "issuer": "https://app.heymarvin.com",
    "authorization_endpoint": "https://app.heymarvin.com/api/v1/oauth/authorize",
    "token_endpoint": "https://app.heymarvin.com/api/v1/oauth/token",
    "registration_endpoint": "https://app.heymarvin.com/api/v1/oauth/register",
    "scopes_supported": ["mcp:read"],
}


def _reload_app_modules():
    for name in (
        "app.config",
        "app.connectors.tokens",
        "app.connectors.marvin_oauth",
        "app.connector_probe",
        "app.routes.connectors",
        "app.main",
    ):
        if name in sys.modules:
            importlib.reload(sys.modules[name])


@pytest.fixture
def marvin_env(isolated_settings, monkeypatch):
    monkeypatch.setenv("TOKEN_ENCRYPTION_KEY", Fernet.generate_key().decode())
    monkeypatch.setenv("MARVIN_OAUTH_REDIRECT_URI", REDIRECT_URI)
    # A statically configured client keeps these tests off the registration
    # path — dynamic registration has its own coverage in test_connectors_marvin.
    monkeypatch.setenv("MARVIN_CLIENT_ID", "test-client-id")
    monkeypatch.setenv("MARVIN_CLIENT_SECRET", "test-client-secret")
    monkeypatch.setenv("FRONTEND_URL", "http://localhost:3000")
    _reload_app_modules()
    from app.connectors import marvin_oauth

    marvin_oauth._metadata_cache.clear()
    yield
    marvin_oauth._metadata_cache.clear()


def _metadata_resp() -> MagicMock:
    resp = MagicMock(ok=True, status_code=200)
    resp.json.return_value = _METADATA
    return resp


def _token_resp(**overrides) -> MagicMock:
    resp = MagicMock(ok=True, status_code=200)
    resp.json.return_value = {
        "access_token": "marvin-access",
        "refresh_token": "marvin-refresh",
        "expires_in": 3600,
        "token_type": "Bearer",
        **overrides,
    }
    return resp


def _fake_mcp(tools: list[dict] | None = None) -> MagicMock:
    """A context-managing McpSession stand-in."""
    session = MagicMock()
    session.__enter__ = lambda self: self
    session.__exit__ = lambda self, *a: None
    session.server_info = {"name": "Marvin", "version": "2.0"}
    session.list_tools.return_value = (
        tools if tools is not None else [{"name": "list_projects"}]
    )
    return session


# ─────────────────────────── start-oauth ───────────────────────────


def test_start_oauth_requires_auth(unauth_client, marvin_env):
    r = unauth_client.post("/v1/connectors/marvin/start-oauth", json={})
    assert r.status_code == 401


def test_start_oauth_returns_a_us_authorize_url_by_default(marvin_env, monkeypatch):
    ctx = company_client(monkeypatch)
    with patch(
        "app.connectors.marvin_oauth.requests.get", return_value=_metadata_resp()
    ):
        r = ctx.client.post("/v1/connectors/marvin/start-oauth", json={})

    assert r.status_code == 200, r.text
    url = r.json()["authorize_url"]
    assert url.startswith(_METADATA["authorization_endpoint"])
    assert "client_id=test-client-id" in url
    assert "code_challenge_method=S256" in url
    assert "resource=https%3A%2F%2Fmcp.heymarvin.com" in url


def test_start_oauth_honours_the_eu_region(marvin_env, monkeypatch):
    ctx = company_client(monkeypatch)
    eu_metadata = MagicMock(ok=True, status_code=200)
    eu_metadata.json.return_value = {
        **_METADATA,
        "issuer": "https://app.eu.heymarvin.com",
        "authorization_endpoint": "https://app.eu.heymarvin.com/api/v1/oauth/authorize",
        "token_endpoint": "https://app.eu.heymarvin.com/api/v1/oauth/token",
    }
    with patch(
        "app.connectors.marvin_oauth.requests.get", return_value=eu_metadata
    ) as get:
        r = ctx.client.post(
            "/v1/connectors/marvin/start-oauth", json={"region": "eu"},
        )

    assert r.status_code == 200, r.text
    url = r.json()["authorize_url"]
    assert url.startswith("https://app.eu.heymarvin.com/api/v1/oauth/authorize")
    assert "resource=https%3A%2F%2Fmcp-eu.heymarvin.com" in url
    # Discovery must target the EU issuer, not the US one.
    assert "app.eu.heymarvin.com" in get.call_args.args[0]


def test_start_oauth_500s_when_the_redirect_uri_is_unset(marvin_env, monkeypatch):
    monkeypatch.setenv("MARVIN_OAUTH_REDIRECT_URI", "")
    _reload_app_modules()
    ctx = company_client(monkeypatch)
    r = ctx.client.post("/v1/connectors/marvin/start-oauth", json={})
    assert r.status_code == 500


def test_start_oauth_runs_the_org_connector_admin_gate(marvin_env, monkeypatch):
    """Marvin is org-wide (not personal like Slack), so a non-admin must not be
    able to rebind the whole workspace's research source."""
    from fastapi import HTTPException

    ctx = company_client(monkeypatch)
    with patch(
        "app.routes.connectors._require_admin_for_org_connector",
        side_effect=HTTPException(403, "admins only"),
    ) as gate:
        r = ctx.client.post("/v1/connectors/marvin/start-oauth", json={})

    assert r.status_code == 403
    assert gate.call_args.args[1] == "marvin"


# ─────────────────────────── callback ───────────────────────────


def _state(company_id: str, region: str = "us") -> str:
    from app.connectors import marvin_oauth

    return marvin_oauth.sign_oauth_state(company_id=company_id, region=region)


def test_callback_stores_the_connection_with_region_and_label(marvin_env, monkeypatch):
    ctx = company_client(monkeypatch)
    state = _state(ctx.company_id, "eu")

    with (
        patch("app.connectors.marvin_oauth.requests.get", return_value=_metadata_resp()),
        patch("app.connectors.marvin_oauth.requests.post", return_value=_token_resp()),
        patch("app.connectors.mcp_client.McpSession", return_value=_fake_mcp()),
    ):
        r = ctx.client.get(
            "/v1/connectors/marvin/callback",
            params={"code": "auth-code", "state": state},
            follow_redirects=False,
        )

    assert r.status_code == 307, r.text
    assert "connected=marvin" in r.headers["location"]

    rows = [
        c for c in ctx.client.get("/v1/connectors").json()["connections"]
        if c["provider"] == "marvin"
    ]
    assert len(rows) == 1
    row = rows[0]
    assert row["account_label"] == "Marvin · EU"
    assert row["types"] == ["customer-voice"]
    assert row["scopes"] == "mcp:read"
    assert row["config"]["region"] == "eu"
    assert row["config"]["mcp_url"] == "https://mcp-eu.heymarvin.com"
    assert "token_json_encrypted" not in row


def test_callback_stores_a_credential_the_puller_can_parse(marvin_env, monkeypatch):
    ctx = company_client(monkeypatch)
    state = _state(ctx.company_id, "us")

    with (
        patch("app.connectors.marvin_oauth.requests.get", return_value=_metadata_resp()),
        patch("app.connectors.marvin_oauth.requests.post", return_value=_token_resp()),
        patch("app.connectors.mcp_client.McpSession", return_value=_fake_mcp()),
    ):
        ctx.client.get(
            "/v1/connectors/marvin/callback",
            params={"code": "auth-code", "state": state},
            follow_redirects=False,
        )

    from app import db
    from app.connectors import marvin_oauth
    from app.connectors.tokens import decrypt_token_json
    from app.kg_ingest.runner import token_for

    row = db.get_connection(ctx.company_id, "marvin")
    token_json = json.loads(decrypt_token_json(row["token_json_encrypted"]))
    access_token, mcp_url = marvin_oauth.parse_credential(
        token_for("marvin", token_json)
    )
    assert access_token == "marvin-access"
    assert mcp_url == "https://mcp.heymarvin.com"
    assert token_json["refresh_token"] == "marvin-refresh"


def test_callback_sends_the_pkce_verifier_and_the_resource(marvin_env, monkeypatch):
    ctx = company_client(monkeypatch)
    from app.connectors import marvin_oauth

    state = _state(ctx.company_id)
    nonce = marvin_oauth.verify_oauth_state(state)["nonce"]

    with (
        patch("app.connectors.marvin_oauth.requests.get", return_value=_metadata_resp()),
        patch(
            "app.connectors.marvin_oauth.requests.post", return_value=_token_resp()
        ) as post,
        patch("app.connectors.mcp_client.McpSession", return_value=_fake_mcp()),
    ):
        ctx.client.get(
            "/v1/connectors/marvin/callback",
            params={"code": "auth-code", "state": state},
            follow_redirects=False,
        )

    body = post.call_args.kwargs["data"]
    assert body["grant_type"] == "authorization_code"
    assert body["code_verifier"] == marvin_oauth.code_verifier_for(nonce)
    assert body["client_secret"] == "test-client-secret"
    assert body["resource"] == "https://mcp.heymarvin.com"
    assert body["redirect_uri"] == REDIRECT_URI


def test_callback_rejects_a_workspace_with_mcp_disabled(marvin_env, monkeypatch):
    """Consent succeeds but the server exposes nothing — storing that would
    leave the user with a connector that syncs zero records forever."""
    ctx = company_client(monkeypatch)
    state = _state(ctx.company_id)

    with (
        patch("app.connectors.marvin_oauth.requests.get", return_value=_metadata_resp()),
        patch("app.connectors.marvin_oauth.requests.post", return_value=_token_resp()),
        patch("app.connectors.mcp_client.McpSession", return_value=_fake_mcp(tools=[])),
    ):
        r = ctx.client.get(
            "/v1/connectors/marvin/callback",
            params={"code": "auth-code", "state": state},
            follow_redirects=False,
        )

    assert r.status_code == 400
    assert "Enable MCP" in r.json()["detail"]
    assert not [
        c for c in ctx.client.get("/v1/connectors").json()["connections"]
        if c["provider"] == "marvin"
    ]


def test_callback_rejects_a_state_signed_for_another_provider(marvin_env, monkeypatch):
    ctx = company_client(monkeypatch)
    from app.connectors import jira_oauth

    foreign = jira_oauth.sign_oauth_state(company_id=ctx.company_id)
    r = ctx.client.get(
        "/v1/connectors/marvin/callback",
        params={"code": "auth-code", "state": foreign},
        follow_redirects=False,
    )
    assert r.status_code == 400


def test_callback_400s_when_no_access_token_comes_back(marvin_env, monkeypatch):
    ctx = company_client(monkeypatch)
    state = _state(ctx.company_id)
    empty = MagicMock(ok=True, status_code=200)
    empty.json.return_value = {"token_type": "Bearer"}

    with (
        patch("app.connectors.marvin_oauth.requests.get", return_value=_metadata_resp()),
        patch("app.connectors.marvin_oauth.requests.post", return_value=empty),
    ):
        r = ctx.client.get(
            "/v1/connectors/marvin/callback",
            params={"code": "auth-code", "state": state},
            follow_redirects=False,
        )
    assert r.status_code == 400


def test_callback_400s_when_marvin_rejects_the_code(marvin_env, monkeypatch):
    ctx = company_client(monkeypatch)
    state = _state(ctx.company_id)
    rejected = MagicMock(ok=False, status_code=400, text="invalid_grant")

    with (
        patch("app.connectors.marvin_oauth.requests.get", return_value=_metadata_resp()),
        patch("app.connectors.marvin_oauth.requests.post", return_value=rejected),
    ):
        r = ctx.client.get(
            "/v1/connectors/marvin/callback",
            params={"code": "stale-code", "state": state},
            follow_redirects=False,
        )
    assert r.status_code == 400


# ─────────────────────────── disconnect ───────────────────────────


def test_disconnect_removes_the_connection(marvin_env, monkeypatch):
    ctx = company_client(monkeypatch)
    seed_connection(
        company_id=ctx.company_id, provider="marvin",
        token_blob={"access_token": "at", "region": "us"},
        label="Marvin · US / Global",
    )
    r = ctx.client.delete("/v1/connectors/marvin")
    assert r.status_code == 200
    assert r.json() == {"deleted": True, "provider": "marvin"}
    assert not [
        c for c in ctx.client.get("/v1/connectors").json()["connections"]
        if c["provider"] == "marvin"
    ]


def test_disconnect_404s_when_not_connected(marvin_env, monkeypatch):
    ctx = company_client(monkeypatch)
    assert ctx.client.delete("/v1/connectors/marvin").status_code == 404


# ─────────────────────────── health probe ───────────────────────────


def _seed_live_connection(company_id: str, **token_overrides) -> None:
    import time

    seed_connection(
        company_id=company_id,
        provider="marvin",
        token_blob={
            "access_token": "at-live",
            "refresh_token": "rt-live",
            "expires_in": 3600,
            "obtained_at": int(time.time()),
            "region": "us",
            "mcp_url": "https://mcp.heymarvin.com",
            **token_overrides,
        },
        label="Marvin · US / Global",
    )


def test_test_connection_passes_on_a_live_handshake(marvin_env, monkeypatch):
    ctx = company_client(monkeypatch)
    _seed_live_connection(ctx.company_id)

    with patch("app.connectors.mcp_client.McpSession", return_value=_fake_mcp()):
        r = ctx.client.post("/v1/connectors/marvin/test")

    assert r.status_code == 200, r.text
    assert r.json()["account_label"] == "Marvin · US / Global"


def test_test_connection_400s_when_the_handshake_fails(marvin_env, monkeypatch):
    ctx = company_client(monkeypatch)
    _seed_live_connection(ctx.company_id)

    from app.connectors.mcp_client import McpError

    failing = MagicMock()
    failing.__enter__ = MagicMock(side_effect=McpError("token expired", status_code=401))
    failing.__exit__ = lambda self, *a: None
    with patch("app.connectors.mcp_client.McpSession", return_value=failing):
        r = ctx.client.post("/v1/connectors/marvin/test")

    assert r.status_code == 400


def test_probe_refreshes_an_expired_token_and_persists_it(marvin_env, monkeypatch):
    """Access tokens are short-lived; without this a day-old connection would
    read as unhealthy every time the user opened Settings."""
    ctx = company_client(monkeypatch)
    _seed_live_connection(ctx.company_id, obtained_at=0)  # long expired

    refreshed = _token_resp(access_token="at-fresh", refresh_token="rt-rotated")
    with (
        patch("app.connectors.marvin_oauth.requests.get", return_value=_metadata_resp()),
        patch("app.connectors.marvin_oauth.requests.post", return_value=refreshed),
        patch("app.connectors.mcp_client.McpSession", return_value=_fake_mcp()),
    ):
        r = ctx.client.post("/v1/connectors/marvin/test")

    assert r.status_code == 200, r.text

    from app import db
    from app.connectors.tokens import decrypt_token_json

    row = db.get_connection(ctx.company_id, "marvin")
    token_json = json.loads(decrypt_token_json(row["token_json_encrypted"]))
    assert token_json["access_token"] == "at-fresh"
    assert token_json["refresh_token"] == "rt-rotated"
    # The packed puller credential must track the refreshed token, not the old.
    assert "at-fresh" in token_json["marvin_credential"]


def test_probe_surfaces_a_dead_refresh_token_as_a_rejection(marvin_env, monkeypatch):
    ctx = company_client(monkeypatch)
    _seed_live_connection(ctx.company_id, obtained_at=0)

    rejected = MagicMock(ok=False, status_code=400, text="invalid_grant")
    with (
        patch("app.connectors.marvin_oauth.requests.get", return_value=_metadata_resp()),
        patch("app.connectors.marvin_oauth.requests.post", return_value=rejected),
    ):
        r = ctx.client.post("/v1/connectors/marvin/test")

    assert r.status_code == 400


# ─────────────────────── catalog + ingest wiring ───────────────────────


def test_marvin_is_an_evidence_bearing_customer_voice_provider(marvin_env):
    from app.connectors.catalog import is_evidence_provider, types_for

    assert types_for("marvin") == ["customer-voice"]
    assert is_evidence_provider("marvin") is True


def test_marvin_is_registered_as_an_ingestable_puller(marvin_env):
    from app.kg_ingest.runner import PULLERS

    assert "marvin" in PULLERS
    assert PULLERS["marvin"][1] == "marvin_credential"


def test_connector_status_reports_marvin_as_ingestable(marvin_env, monkeypatch):
    ctx = company_client(monkeypatch)
    _seed_live_connection(ctx.company_id)
    statuses = ctx.client.get("/v1/connectors/status").json()["statuses"]
    marvin_status = next(s for s in statuses if s["provider"] == "marvin")
    assert marvin_status["ingestable"] is True
    assert marvin_status["types"] == ["customer-voice"]
