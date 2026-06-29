"""Tests for auto-sync-on-connect (Task #17):
  * kickoff fires on a successful connect callback with (company, provider)
  * kickoff is a no-op for providers without an ingest puller
  * the background body stamps last_sync_at / last_sync_error
  * the GET /v1/connectors/status endpoint surfaces the stamps
"""
from __future__ import annotations

import time
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


def test_run_sync_handles_expired_token_gracefully(monkeypatch, caplog):
    """A 401/403 (expired or revoked OAuth token) is expected/recoverable: it is
    logged at WARNING (no ERROR traceback) and stamped as a reconnect prompt —
    not flooded as an ERROR every sync cycle (the prod GitHub auto-sync noise)."""
    from fastapi import HTTPException

    monkeypatch.setattr(auto_sync.db, "get_connection",
                        lambda cid, prov: {"token_json_encrypted": "enc"})
    monkeypatch.setattr(auto_sync, "decrypt_token_json", lambda enc: '{"access_token": "t"}')
    monkeypatch.setattr(auto_sync, "token_for", lambda prov, tj: "t")
    monkeypatch.setattr(auto_sync, "GraphFacade", lambda *a, **k: object())

    def expired(*a, **k):
        raise HTTPException(status_code=401, detail="GitHub repos fetch failed")

    monkeypatch.setattr(auto_sync, "sync_provider", expired)
    stamps = {}
    monkeypatch.setattr(auto_sync.db, "update_connection_sync",
                        lambda cid, prov, **kw: stamps.update(kw))

    import logging
    with caplog.at_level(logging.WARNING, logger="app.kg_ingest.auto_sync"):
        auto_sync._run_sync("co-1", "github")        # must not raise

    # Graceful: reconnect prompt stamped, WARNING (not ERROR) logged.
    assert stamps["last_sync_error"] == "github authorization expired — reconnect required"
    recs = [r for r in caplog.records if r.name == "app.kg_ingest.auto_sync"]
    assert any(r.levelno == logging.WARNING and "reconnect required" in r.getMessage()
               for r in recs)
    assert not any(r.levelno >= logging.ERROR for r in recs)  # no ERROR traceback


# ---------- github token refresh-on-sync ----------

def _expired_github_tj() -> str:
    # obtained_at far in the past → _token_is_fresh() is False
    return ('{"access_token": "OLD", "refresh_token": "r1", '
            '"obtained_at": 1, "expires_in": 28800}')


def _fresh_github_tj() -> str:
    return ('{"access_token": "OLD", "refresh_token": "r1", '
            f'"obtained_at": {int(time.time())}, "expires_in": 28800}}')


def _patch_github_refresh(monkeypatch, *, new_access="NEW"):
    """Stub the github_app refresh + token tokens-encrypt + persist used by
    auto_sync._maybe_refresh_token. Returns a dict recording the persist call."""
    import app.connectors.github_app as gh
    import app.connectors.tokens as toks

    persisted: dict = {}
    monkeypatch.setattr(gh, "refresh_user_token",
                        lambda rt: {"access_token": new_access, "refresh_token": "r2",
                                    "expires_in": 28800})
    monkeypatch.setattr(
        gh, "token_payload_to_store",
        lambda tj: ('{"access_token": "%s", "refresh_token": "r2", '
                    '"expires_in": 28800, "obtained_at": 999}') % tj["access_token"],
    )
    monkeypatch.setattr(toks, "encrypt_token_json", lambda s: "enc-new")
    monkeypatch.setattr(auto_sync.db, "update_connection_tokens",
                        lambda cid, prov, enc: persisted.update(cid=cid, prov=prov, enc=enc))
    return persisted


def test_run_sync_proactively_refreshes_expired_github_token(monkeypatch):
    """An expired GitHub access token is refreshed BEFORE the pull (using the
    stored refresh_token), persisted, and the sync runs with the new token —
    no 401, no reconnect prompt."""
    monkeypatch.setattr(auto_sync.db, "get_connection",
                        lambda cid, prov: {"token_json_encrypted": "enc"})
    monkeypatch.setattr(auto_sync, "decrypt_token_json", lambda enc: _expired_github_tj())
    persisted = _patch_github_refresh(monkeypatch)
    monkeypatch.setattr(auto_sync, "GraphFacade", lambda *a, **k: object())

    used = {}

    def fake_sync(facade, cid, prov, *, token, **k):
        used["token"] = token
        return {"records": 3, "signals": 2, "errors": []}

    monkeypatch.setattr(auto_sync, "sync_provider", fake_sync)
    stamps = {}
    monkeypatch.setattr(auto_sync.db, "update_connection_sync",
                        lambda cid, prov, **kw: stamps.update(kw))

    auto_sync._run_sync("co-1", "github")

    assert used["token"] == "NEW"            # synced with the refreshed token
    assert persisted["enc"] == "enc-new"     # new token persisted
    assert stamps["last_sync_error"] is None


def test_run_sync_does_not_refresh_a_fresh_token(monkeypatch):
    """A still-valid token is used as-is — no refresh round-trip."""
    monkeypatch.setattr(auto_sync.db, "get_connection",
                        lambda cid, prov: {"token_json_encrypted": "enc"})
    monkeypatch.setattr(auto_sync, "decrypt_token_json", lambda enc: _fresh_github_tj())
    import app.connectors.github_app as gh
    called = {"n": 0}
    monkeypatch.setattr(gh, "refresh_user_token",
                        lambda rt: called.__setitem__("n", called["n"] + 1) or {})
    monkeypatch.setattr(auto_sync, "GraphFacade", lambda *a, **k: object())
    monkeypatch.setattr(auto_sync, "sync_provider",
                        lambda f, c, p, *, token, **k: {"records": 1, "signals": 1, "errors": []})
    monkeypatch.setattr(auto_sync.db, "update_connection_sync", lambda *a, **k: None)

    auto_sync._run_sync("co-1", "github")
    assert called["n"] == 0                  # fresh token → no refresh attempted


def test_run_sync_reactive_refresh_on_401_then_retry(monkeypatch):
    """A token that looks fresh but 401s mid-sync triggers ONE force-refresh +
    retry with the new token, which succeeds."""
    from fastapi import HTTPException

    monkeypatch.setattr(auto_sync.db, "get_connection",
                        lambda cid, prov: {"token_json_encrypted": "enc"})
    monkeypatch.setattr(auto_sync, "decrypt_token_json", lambda enc: _fresh_github_tj())
    _patch_github_refresh(monkeypatch)
    monkeypatch.setattr(auto_sync, "GraphFacade", lambda *a, **k: object())

    seen = []

    def fake_sync(facade, cid, prov, *, token, **k):
        seen.append(token)
        if len(seen) == 1:
            raise HTTPException(status_code=401, detail="expired")
        return {"records": 1, "signals": 1, "errors": []}

    monkeypatch.setattr(auto_sync, "sync_provider", fake_sync)
    stamps = {}
    monkeypatch.setattr(auto_sync.db, "update_connection_sync",
                        lambda cid, prov, **kw: stamps.update(kw))

    auto_sync._run_sync("co-1", "github")
    assert seen == ["OLD", "NEW"]            # first 401 on OLD, retried on refreshed
    assert stamps["last_sync_error"] is None


def test_run_sync_reconnect_required_when_refresh_fails(monkeypatch, caplog):
    """If refresh itself fails (refresh token expired/revoked), we fall back to
    the graceful 'reconnect required' prompt — not an ERROR traceback."""
    import logging

    from fastapi import HTTPException
    import app.connectors.github_app as gh

    monkeypatch.setattr(auto_sync.db, "get_connection",
                        lambda cid, prov: {"token_json_encrypted": "enc"})
    monkeypatch.setattr(auto_sync, "decrypt_token_json", lambda enc: _expired_github_tj())

    def refresh_boom(rt):
        raise HTTPException(status_code=400, detail="GitHub token refresh failed")

    monkeypatch.setattr(gh, "refresh_user_token", refresh_boom)
    monkeypatch.setattr(auto_sync, "GraphFacade", lambda *a, **k: object())
    monkeypatch.setattr(auto_sync, "sync_provider",
                        lambda *a, **k: (_ for _ in ()).throw(
                            HTTPException(status_code=401, detail="expired")))
    stamps = {}
    monkeypatch.setattr(auto_sync.db, "update_connection_sync",
                        lambda cid, prov, **kw: stamps.update(kw))

    with caplog.at_level(logging.WARNING, logger="app.kg_ingest.auto_sync"):
        auto_sync._run_sync("co-1", "github")

    assert stamps["last_sync_error"] == "github authorization expired — reconnect required"
    assert not any(r.levelno >= logging.ERROR for r in caplog.records
                   if r.name == "app.kg_ingest.auto_sync")


def test_maybe_refresh_noop_without_refresh_token_or_non_github(monkeypatch):
    """No refresh_token, or a non-github provider, returns the token unchanged."""
    tj = {"access_token": "x"}
    assert auto_sync._maybe_refresh_token("co", "github", tj) is tj
    tj2 = {"access_token": "x", "refresh_token": "r"}
    assert auto_sync._maybe_refresh_token("co", "clickup", tj2) is tj2


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
        company_id="co-X", role="admin", user_id="u1")
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
    monkeypatch.setattr(conn_route, "_has_github_install_for", lambda login, company_id: True)

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
