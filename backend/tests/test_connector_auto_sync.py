"""Tests for auto-sync-on-connect (Task #17):
  * kickoff fires on a successful connect callback with (company, provider)
  * kickoff is a no-op for providers without an ingest puller
  * the background body stamps last_sync_at / last_sync_error
  * the GET /v1/connectors/status endpoint surfaces the stamps
"""
from __future__ import annotations

from unittest.mock import patch

from fastapi.testclient import TestClient

from app.kg_ingest import auto_sync


# ---------- kickoff_sync unit behavior ----------

def test_kickoff_noop_for_provider_without_puller():
    # figma has no kg_ingest puller — nothing to kick off
    assert auto_sync.kickoff_sync("co-1", "figma") is False


def test_kickoff_starts_thread_for_ingestable_provider(monkeypatch):
    started = {}

    class FakeThread:
        def __init__(self, target=None, args=(), name=None, daemon=None):
            started["args"] = args
            started["name"] = name
            started["daemon"] = daemon

        def start(self):
            started["started"] = True

    monkeypatch.setattr(auto_sync.threading, "Thread", FakeThread)
    assert auto_sync.kickoff_sync("co-9", "hubspot") is True
    assert started["started"] is True
    assert started["args"] == ("co-9", "hubspot")
    assert started["daemon"] is True


def test_run_sync_stamps_success(monkeypatch):
    monkeypatch.setattr(auto_sync.db, "get_connection",
                        lambda cid, prov: {"token_json_encrypted": "enc"})
    monkeypatch.setattr(auto_sync, "decrypt_token_json", lambda enc: '{"access_token": "t"}')
    monkeypatch.setattr(auto_sync, "token_for", lambda prov, tj: "t")
    monkeypatch.setattr(auto_sync, "GraphFacade", lambda *a, **k: object())
    monkeypatch.setattr(auto_sync, "sync_provider",
                        lambda *a, **k: {"records": 5, "signals": 3, "errors": []})
    stamps = {}
    monkeypatch.setattr(auto_sync.db, "update_connection_sync",
                        lambda cid, prov, **kw: stamps.update(kw))
    auto_sync._run_sync("co-1", "clickup")
    assert stamps["last_sync_error"] is None
    assert stamps["last_sync_at"]


def test_run_sync_stamps_error_on_failure(monkeypatch):
    monkeypatch.setattr(auto_sync.db, "get_connection",
                        lambda cid, prov: {"token_json_encrypted": "enc"})
    monkeypatch.setattr(auto_sync, "decrypt_token_json", lambda enc: '{"access_token": "t"}')
    monkeypatch.setattr(auto_sync, "token_for", lambda prov, tj: "t")
    monkeypatch.setattr(auto_sync, "GraphFacade", lambda *a, **k: object())

    def boom(*a, **k):
        raise RuntimeError("API down")

    monkeypatch.setattr(auto_sync, "sync_provider", boom)
    stamps = {}
    monkeypatch.setattr(auto_sync.db, "update_connection_sync",
                        lambda cid, prov, **kw: stamps.update(kw))
    auto_sync._run_sync("co-1", "clickup")          # must not raise
    assert "API down" in stamps["last_sync_error"]


# ---------- kickoff fires from the connect callback ----------

def test_fireflies_connect_kicks_off_sync(isolated_settings, monkeypatch):
    """Connecting Fireflies via API key must fire the background ingest with
    (company_id, 'fireflies')."""
    import app.main as main_mod
    from app.auth import CompanyContext
    import app.routes.connectors as conn_route

    monkeypatch.setattr(conn_route.fireflies_apikey, "fetch_authenticated_user",
                        lambda key: {"email": "u@co.com"})
    monkeypatch.setattr(conn_route, "encrypt_token_json", lambda payload: "enc")
    monkeypatch.setattr(conn_route.fireflies_apikey, "token_payload_to_store",
                        lambda key: "{}")
    monkeypatch.setattr(conn_route.db, "upsert_connection", lambda **kw: {"id": "c1"})

    calls = []
    monkeypatch.setattr(conn_route, "kickoff_sync",
                        lambda cid, prov: calls.append((cid, prov)))

    require_company = conn_route.require_company
    main_mod.app.dependency_overrides[require_company] = lambda: CompanyContext(
        company_id="co-X", role="member", user_id="u1")
    try:
        client = TestClient(main_mod.app)
        r = client.post("/v1/connectors/fireflies/apikey", json={"api_key": "ff-key"})
    finally:
        main_mod.app.dependency_overrides.pop(require_company, None)
    assert r.status_code == 200
    assert calls == [("co-X", "fireflies")]


def test_github_callback_kicks_off_sync(isolated_settings, monkeypatch):
    """The GitHub OAuth callback must fire the background ingest with the
    company resolved from the signed state."""
    import app.main as main_mod
    import app.routes.connectors as conn_route

    monkeypatch.setattr(conn_route.github_app, "verify_oauth_state",
                        lambda state: {"company_id": "co-G", "return_to": None})
    monkeypatch.setattr(conn_route.github_app, "exchange_code_for_token",
                        lambda code: {"access_token": "gho_x", "scope": "read:user"})
    monkeypatch.setattr(conn_route.github_app, "fetch_authenticated_user",
                        lambda tok: {"login": "octocat"})
    monkeypatch.setattr(conn_route, "encrypt_token_json", lambda payload: "enc")
    monkeypatch.setattr(conn_route.github_app, "token_payload_to_store",
                        lambda tj: "{}")
    monkeypatch.setattr(conn_route.db, "upsert_connection", lambda **kw: {"id": "c1"})
    # already-installed so the callback takes the normal redirect path
    monkeypatch.setattr(conn_route, "_has_github_install_for", lambda login: True)

    calls = []
    monkeypatch.setattr(conn_route, "kickoff_sync",
                        lambda cid, prov: calls.append((cid, prov)))

    client = TestClient(main_mod.app, follow_redirects=False)
    r = client.get("/v1/connectors/github/callback?code=abc&state=signed")
    assert r.status_code in (302, 307)
    assert calls == [("co-G", "github")]


# ---------- status endpoint ----------

def test_status_endpoint_surfaces_sync_stamps(isolated_settings, monkeypatch):
    import app.main as main_mod
    from app.auth import CompanyContext
    import app.routes.connectors as conn_route

    rows = [
        {"provider": "hubspot", "status": "active", "account_label": "u@co.com",
         "last_sync_at": "2026-06-07T00:00:00Z", "last_sync_error": None},
        {"provider": "figma", "status": "active", "account_label": "@x",
         "last_sync_at": None, "last_sync_error": None},
    ]
    monkeypatch.setattr(conn_route.db, "list_connections", lambda cid: rows)

    require_company = conn_route.require_company
    main_mod.app.dependency_overrides[require_company] = lambda: CompanyContext(
        company_id="co-X", role="member", user_id="u1")
    try:
        client = TestClient(main_mod.app)
        r = client.get("/v1/connectors/status")
    finally:
        main_mod.app.dependency_overrides.pop(require_company, None)
    assert r.status_code == 200
    statuses = {s["provider"]: s for s in r.json()["statuses"]}
    assert statuses["hubspot"]["ingestable"] is True
    assert statuses["hubspot"]["last_sync_at"] == "2026-06-07T00:00:00Z"
    assert statuses["figma"]["ingestable"] is False   # no kg_ingest puller
