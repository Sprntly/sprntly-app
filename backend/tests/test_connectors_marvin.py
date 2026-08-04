"""Unit tests for the Marvin connector's three moving parts.

  * mcp_client — Streamable-HTTP transport: JSON vs SSE responses, session
    handling, error surfacing (including the status_code auto-sync keys its
    token-refresh retry off).
  * marvin_oauth — dynamic client registration, PKCE derivation, region
    handling, credential packing.
  * pullers/marvin — capability resolution over an UNDOCUMENTED tool list,
    which is the part most likely to meet a surprise in production.

All outbound HTTP is mocked; nothing here touches heymarvin.com.
"""
from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
import requests
from cryptography.fernet import Fernet

from app.connectors import mcp_client
from app.connectors.mcp_client import (
    McpError,
    McpSession,
    records_from_result,
    text_from_result,
)


# ─────────────────────────── mcp_client ───────────────────────────


def _json_response(payload: dict, *, headers: dict | None = None) -> MagicMock:
    resp = MagicMock()
    resp.ok = True
    resp.status_code = 200
    resp.headers = {"Content-Type": "application/json", **(headers or {})}
    resp.text = json.dumps(payload)
    return resp


def _sse_response(payload: dict, *, headers: dict | None = None) -> MagicMock:
    resp = MagicMock()
    resp.ok = True
    resp.status_code = 200
    resp.headers = {"Content-Type": "text/event-stream", **(headers or {})}
    resp.text = f"event: message\ndata: {json.dumps(payload)}\n\n"
    return resp


def _accepted() -> MagicMock:
    resp = MagicMock()
    resp.ok = True
    resp.status_code = 202
    resp.headers = {}
    resp.text = ""
    return resp


def test_initialize_captures_session_id_and_server_info():
    init = _json_response(
        {
            "jsonrpc": "2.0",
            "id": 1,
            "result": {
                "protocolVersion": "2025-06-18",
                "serverInfo": {"name": "Marvin", "version": "1.4.0"},
            },
        },
        headers={"Mcp-Session-Id": "sess-abc"},
    )
    with patch(
        "app.connectors.mcp_client.requests.post",
        side_effect=[init, _accepted()],
    ) as post:
        session = McpSession("https://mcp.heymarvin.com", "tok")
        session.initialize()

    assert session.server_info == {"name": "Marvin", "version": "1.4.0"}
    # The initialized notification must follow the handshake, and must carry
    # the session id the server just handed us.
    assert post.call_count == 2
    notify_headers = post.call_args_list[1].kwargs["headers"]
    assert notify_headers["Mcp-Session-Id"] == "sess-abc"
    assert notify_headers["Authorization"] == "Bearer tok"
    assert post.call_args_list[1].kwargs["json"]["method"] == (
        "notifications/initialized"
    )


def test_initialize_is_idempotent():
    """A second initialize() must not re-handshake — sessions are reused."""
    init = _json_response(
        {"jsonrpc": "2.0", "id": 1, "result": {"serverInfo": {"name": "Marvin"}}}
    )
    with patch(
        "app.connectors.mcp_client.requests.post",
        side_effect=[init, _accepted()],
    ) as post:
        session = McpSession("https://mcp.heymarvin.com", "tok")
        session.initialize()
        session.initialize()

    assert post.call_count == 2


def test_sse_framed_response_is_parsed():
    """Servers may answer any request as a one-message SSE stream."""
    init = _json_response({"jsonrpc": "2.0", "id": 1, "result": {}})
    tools = _sse_response(
        {
            "jsonrpc": "2.0",
            "id": 2,
            "result": {"tools": [{"name": "search_knowledge"}]},
        }
    )
    with patch(
        "app.connectors.mcp_client.requests.post",
        side_effect=[init, _accepted(), tools],
    ):
        with McpSession("https://mcp.heymarvin.com", "tok") as session:
            found = session.list_tools()

    assert [t["name"] for t in found] == ["search_knowledge"]


def test_list_tools_follows_pagination_cursor():
    init = _json_response({"jsonrpc": "2.0", "id": 1, "result": {}})
    page1 = _json_response({
        "jsonrpc": "2.0", "id": 2,
        "result": {"tools": [{"name": "a"}], "nextCursor": "c1"},
    })
    page2 = _json_response({
        "jsonrpc": "2.0", "id": 3, "result": {"tools": [{"name": "b"}]},
    })
    with patch(
        "app.connectors.mcp_client.requests.post",
        side_effect=[init, _accepted(), page1, page2],
    ) as post:
        with McpSession("https://mcp.heymarvin.com", "tok") as session:
            found = session.list_tools()

    assert [t["name"] for t in found] == ["a", "b"]
    assert post.call_args_list[3].kwargs["json"]["params"]["cursor"] == "c1"


def test_http_401_surfaces_status_code_for_the_refresh_retry():
    """auto_sync forces a token refresh on 401/403 — the code must survive."""
    resp = MagicMock()
    resp.ok = False
    resp.status_code = 401
    resp.headers = {}
    resp.text = "token expired"
    with patch("app.connectors.mcp_client.requests.post", return_value=resp):
        with pytest.raises(McpError) as excinfo:
            McpSession("https://mcp.heymarvin.com", "stale").initialize()

    assert excinfo.value.status_code == 401


def test_jsonrpc_error_object_raises():
    resp = _json_response({
        "jsonrpc": "2.0", "id": 1,
        "error": {"code": -32601, "message": "Method not found"},
    })
    with patch("app.connectors.mcp_client.requests.post", return_value=resp):
        with pytest.raises(McpError, match="Method not found"):
            McpSession("https://mcp.heymarvin.com", "tok").initialize()


def test_transport_failure_raises_mcp_error():
    with patch(
        "app.connectors.mcp_client.requests.post",
        side_effect=requests.RequestException("connection reset"),
    ):
        with pytest.raises(McpError, match="connection reset"):
            McpSession("https://mcp.heymarvin.com", "tok").initialize()


def test_tool_level_is_error_raises_with_its_text():
    init = _json_response({"jsonrpc": "2.0", "id": 1, "result": {}})
    failed = _json_response({
        "jsonrpc": "2.0", "id": 2,
        "result": {
            "isError": True,
            "content": [{"type": "text", "text": "project not found"}],
        },
    })
    with patch(
        "app.connectors.mcp_client.requests.post",
        side_effect=[init, _accepted(), failed],
    ):
        with McpSession("https://mcp.heymarvin.com", "tok") as session:
            with pytest.raises(McpError, match="project not found"):
                session.call_tool("get_file", {"file_id": "1"})


def test_text_from_result_concatenates_text_and_embedded_resources():
    result = {
        "content": [
            {"type": "text", "text": "summary line"},
            {"type": "image", "data": "…"},
            {"type": "resource", "resource": {"text": "resource line"}},
        ]
    }
    assert text_from_result(result) == "summary line\nresource line"


@pytest.mark.parametrize(
    "result,expected_ids",
    [
        # structuredContent as a bare list
        ({"structuredContent": [{"id": "1"}, {"id": "2"}]}, ["1", "2"]),
        # structuredContent wrapping the list under an arbitrary key
        ({"structuredContent": {"results": [{"id": "3"}]}}, ["3"]),
        # JSON hiding inside a text block
        (
            {"content": [{"type": "text", "text": '{"files": [{"id": "4"}]}'}]},
            ["4"],
        ),
        # a single record returned as a bare object
        ({"structuredContent": {"id": "5", "name": "Onboarding"}}, ["5"]),
    ],
)
def test_records_from_result_handles_the_shapes_servers_actually_use(
    result, expected_ids
):
    assert [r["id"] for r in records_from_result(result)] == expected_ids


def test_records_from_result_returns_empty_for_prose():
    result = {"content": [{"type": "text", "text": "No files matched."}]}
    assert records_from_result(result) == []


def test_parse_sse_skips_undecodable_payloads():
    body = "data: not json\n\ndata: {\"id\": 1}\n\n"
    assert mcp_client._parse_sse(body) == [{"id": 1}]


# ─────────────────────────── marvin_oauth ───────────────────────────


_METADATA = {
    "issuer": "https://app.heymarvin.com",
    "authorization_endpoint": "https://app.heymarvin.com/api/v1/oauth/authorize",
    "token_endpoint": "https://app.heymarvin.com/api/v1/oauth/token",
    "registration_endpoint": "https://app.heymarvin.com/api/v1/oauth/register",
    "scopes_supported": ["mcp:read"],
    "code_challenge_methods_supported": ["S256"],
}


@pytest.fixture
def marvin_env(isolated_settings, monkeypatch):
    """Configured Marvin connector with a clean metadata cache per test."""
    import importlib
    import sys

    monkeypatch.setenv("TOKEN_ENCRYPTION_KEY", Fernet.generate_key().decode())
    monkeypatch.setenv("JWT_SECRET", "test-jwt-secret")
    monkeypatch.setenv(
        "MARVIN_OAUTH_REDIRECT_URI",
        "https://api.sprntly.ai/v1/connectors/marvin/callback",
    )
    for name in ("app.config", "app.connectors.tokens", "app.connectors.marvin_oauth"):
        if name in sys.modules:
            importlib.reload(sys.modules[name])
    from app.connectors import marvin_oauth

    marvin_oauth._metadata_cache.clear()
    yield marvin_oauth
    marvin_oauth._metadata_cache.clear()


def test_regions_map_to_distinct_issuers_and_endpoints(marvin_env):
    us = marvin_env.region_config("us")
    eu = marvin_env.region_config("eu")
    assert us["mcp_url"] != eu["mcp_url"]
    assert us["issuer"] != eu["issuer"]
    # An unknown or empty region must not raise — it falls back to US.
    assert marvin_env.region_config("moon")["issuer"] == us["issuer"]
    assert marvin_env.normalize_region(None) == "us"
    assert marvin_env.normalize_region("EU") == "eu"


def test_discover_metadata_is_cached(marvin_env):
    resp = MagicMock(ok=True)
    resp.json.return_value = _METADATA
    with patch(
        "app.connectors.marvin_oauth.requests.get", return_value=resp
    ) as get:
        marvin_env.discover_metadata("https://app.heymarvin.com")
        marvin_env.discover_metadata("https://app.heymarvin.com")
    assert get.call_count == 1


def test_pkce_verifier_is_deterministic_and_nonce_bound(marvin_env):
    """The callback recomputes the verifier from the state's nonce, so the
    same nonce must always yield the same verifier — and a different one
    must not."""
    first = marvin_env.code_verifier_for("nonce-a")
    assert first == marvin_env.code_verifier_for("nonce-a")
    assert first != marvin_env.code_verifier_for("nonce-b")
    # base64url, no padding — PKCE forbids '+', '/' and '='.
    assert not set("+/=") & set(first)


def test_authorize_url_carries_pkce_resource_and_registered_client(marvin_env):
    meta_resp = MagicMock(ok=True)
    meta_resp.json.return_value = _METADATA
    with patch("app.connectors.marvin_oauth.requests.get", return_value=meta_resp), \
         patch.object(marvin_env, "ensure_client", return_value=("client-123", "sec")):
        state = marvin_env.sign_oauth_state(company_id="c1", region="us")
        nonce = marvin_env.verify_oauth_state(state)["nonce"]
        url = marvin_env.authorize_url(state)

    assert url.startswith(_METADATA["authorization_endpoint"])
    assert "client_id=client-123" in url
    assert "code_challenge_method=S256" in url
    assert "scope=mcp%3Aread" in url
    # RFC 8707: the token must be pinned to the MCP resource server.
    assert "resource=https%3A%2F%2Fmcp.heymarvin.com" in url
    # The verifier itself must NEVER travel in the URL.
    assert marvin_env.code_verifier_for(nonce) not in url


def test_state_round_trip_preserves_region(marvin_env):
    state = marvin_env.sign_oauth_state(
        company_id="c1", region="eu", return_to="/settings",
    )
    payload = marvin_env.verify_oauth_state(state)
    assert payload["company_id"] == "c1"
    assert payload["region"] == "eu"
    assert payload["return_to"] == "/settings"


def test_state_from_another_provider_is_rejected(marvin_env):
    import jwt as pyjwt

    from app.config import settings

    forged = pyjwt.encode(
        {"provider": "jira", "company_id": "c1", "nonce": "n", "exp": 9999999999},
        settings.jwt_secret,
        algorithm="HS256",
    )
    from fastapi import HTTPException

    with pytest.raises(HTTPException):
        marvin_env.verify_oauth_state(forged)


def test_ensure_client_prefers_the_env_override(marvin_env, monkeypatch):
    monkeypatch.setattr(marvin_env.settings, "marvin_client_id", "static-id")
    monkeypatch.setattr(marvin_env.settings, "marvin_client_secret", "static-secret")
    with patch("app.connectors.marvin_oauth.requests.post") as post:
        assert marvin_env.ensure_client("us") == ("static-id", "static-secret")
    post.assert_not_called()


def test_ensure_client_registers_once_then_reuses_the_stored_row(marvin_env):
    meta_resp = MagicMock(ok=True)
    meta_resp.json.return_value = _METADATA
    reg_resp = MagicMock(ok=True)
    reg_resp.json.return_value = {"client_id": "dyn-1", "client_secret": "dyn-secret"}

    stored: dict = {}

    def fake_get(provider, issuer, **_kw):
        return stored.get((provider, issuer))

    def fake_save(provider, issuer, *, client_id, client_secret, registration=None, **_kw):
        stored[(provider, issuer)] = {
            "client_id": client_id,
            "client_secret": client_secret,
            "registration": registration or {},
        }

    with patch("app.connectors.marvin_oauth.requests.get", return_value=meta_resp), \
         patch("app.connectors.marvin_oauth.requests.post", return_value=reg_resp) as post, \
         patch("app.db.oauth_clients.get_oauth_client", side_effect=fake_get), \
         patch("app.db.oauth_clients.save_oauth_client", side_effect=fake_save):
        first = marvin_env.ensure_client("us")
        second = marvin_env.ensure_client("us")

    assert first == ("dyn-1", "dyn-secret")
    assert second == first
    assert post.call_count == 1, "registration must happen exactly once"
    body = post.call_args.kwargs["json"]
    assert body["redirect_uris"] == [marvin_env.settings.marvin_oauth_redirect_uri]
    assert body["scope"] == "mcp:read"
    assert "refresh_token" in body["grant_types"]


def test_registered_clients_are_keyed_per_region(marvin_env):
    """A client registered at the US issuer is meaningless at the EU one."""
    meta_resp = MagicMock(ok=True)
    meta_resp.json.return_value = _METADATA
    reg_resp = MagicMock(ok=True)
    reg_resp.json.return_value = {"client_id": "dyn", "client_secret": "s"}
    issuers: list[str] = []

    with patch("app.connectors.marvin_oauth.requests.get", return_value=meta_resp), \
         patch("app.connectors.marvin_oauth.requests.post", return_value=reg_resp), \
         patch("app.db.oauth_clients.get_oauth_client", return_value=None), \
         patch(
             "app.db.oauth_clients.save_oauth_client",
             side_effect=lambda p, i, **kw: issuers.append(i),
         ):
        marvin_env.ensure_client("us")
        marvin_env.ensure_client("eu")

    assert issuers == [
        marvin_env.REGIONS["us"]["issuer"],
        marvin_env.REGIONS["eu"]["issuer"],
    ]


def test_refresh_rejection_raises_the_reconnect_error(marvin_env):
    meta_resp = MagicMock(ok=True)
    meta_resp.json.return_value = _METADATA
    rejected = MagicMock(ok=False, status_code=400, text="invalid_grant")

    with patch("app.connectors.marvin_oauth.requests.get", return_value=meta_resp), \
         patch("app.connectors.marvin_oauth.requests.post", return_value=rejected), \
         patch.object(marvin_env, "ensure_client", return_value=("id", "sec")):
        with pytest.raises(marvin_env.MarvinAuthExpiredError):
            marvin_env.refresh_access_token("dead-refresh", region="us")


def test_token_payload_packs_the_puller_credential(marvin_env):
    payload = json.loads(
        marvin_env.token_payload_to_store(
            {"access_token": "at-1", "refresh_token": "rt-1", "expires_in": 3600},
            region="eu",
        )
    )
    assert payload["region"] == "eu"
    assert payload["mcp_url"] == marvin_env.REGIONS["eu"]["mcp_url"]
    assert isinstance(payload["obtained_at"], int)

    token, mcp_url = marvin_env.parse_credential(payload[marvin_env.CREDENTIAL_KEY])
    assert token == "at-1"
    assert mcp_url == marvin_env.REGIONS["eu"]["mcp_url"]


def test_refresh_without_a_new_refresh_token_carries_the_old_one_forward(marvin_env):
    """Some servers only issue a refresh token once. Dropping it on a refresh
    would strand the connection at the next expiry."""
    payload = json.loads(
        marvin_env.token_payload_to_store(
            {"access_token": "at-2"}, region="us", keep_refresh_token="rt-original",
        )
    )
    assert payload["refresh_token"] == "rt-original"
    # And the packed credential tracks the NEW access token, not a stale one.
    token, _ = marvin_env.parse_credential(payload[marvin_env.CREDENTIAL_KEY])
    assert token == "at-2"


def test_fetch_server_identity_rejects_a_workspace_with_no_tools(marvin_env):
    """MCP disabled by an admin authorizes fine and then exposes nothing —
    that is a failed connection, not a healthy empty one."""
    session = MagicMock()
    session.__enter__ = lambda self: self
    session.__exit__ = lambda self, *a: None
    session.server_info = {"name": "Marvin"}
    session.list_tools.return_value = []
    with patch("app.connectors.mcp_client.McpSession", return_value=session):
        assert marvin_env.fetch_server_identity("tok", "https://mcp.heymarvin.com") == {}


def test_fetch_server_identity_returns_server_info_with_tool_count(marvin_env):
    session = MagicMock()
    session.__enter__ = lambda self: self
    session.__exit__ = lambda self, *a: None
    session.server_info = {"name": "Marvin", "version": "1.0"}
    session.list_tools.return_value = [{"name": "search"}, {"name": "list_projects"}]
    with patch("app.connectors.mcp_client.McpSession", return_value=session):
        info = marvin_env.fetch_server_identity("tok", "https://mcp.heymarvin.com")

    assert info["tool_count"] == 2
    assert marvin_env.account_label(info, "eu") == "Marvin · EU"


def test_account_label_falls_back_when_the_server_names_itself_nothing(marvin_env):
    assert marvin_env.account_label({}, "us") == "Marvin · US / Global"


# ───────────────────── oauth_dynamic_clients schema ─────────────────────


_MIGRATIONS = Path(__file__).resolve().parents[2] / "supabase" / "migrations"


def test_oauth_dynamic_clients_has_row_level_security_enabled():
    """The table holds `client_secret_encrypted` — the credential that mints
    Marvin access tokens — so it must be unreachable with an anon/authenticated
    key, exactly like `connections` (20260525120500) and `call_index`
    (20260802160000).

    Asserted across the WHOLE migration set rather than one file, because RLS
    was added forward-only in a later migration: the original create-table is
    already applied to the shared database and is deliberately left untouched.
    """
    sql = "\n".join(
        path.read_text(encoding="utf-8").lower()
        for path in sorted(_MIGRATIONS.glob("*oauth_dynamic_clients*.sql"))
    )
    assert sql, "no oauth_dynamic_clients migration found"
    assert "create table if not exists oauth_dynamic_clients" in sql
    assert "alter table oauth_dynamic_clients enable row level security" in sql
    # Forward-only: nothing in this table's history may drop or truncate it.
    for destructive in ("drop table", "drop column", "delete from"):
        assert destructive not in sql, destructive
