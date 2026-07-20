"""Route-level tests for POST /v1/ingest/github/deep-read (Task #20)."""
from __future__ import annotations

from fastapi.testclient import TestClient


def _override_company(main_mod, ingest_route, company_id="co-X"):
    from app.auth import CompanyContext
    require_company = ingest_route.require_company
    main_mod.app.dependency_overrides[require_company] = lambda: CompanyContext(
        company_id=company_id, role="admin", user_id="u1")
    return require_company


def test_deep_read_route_rejects_bad_repo(isolated_settings, monkeypatch):
    import app.main as main_mod
    import app.routes.ingest as ingest_route

    require_company = _override_company(main_mod, ingest_route)
    try:
        client = TestClient(main_mod.app)
        r = client.post("/v1/ingest/github/deep-read", json={"repo": "no-slash"})
    finally:
        main_mod.app.dependency_overrides.pop(require_company, None)
    assert r.status_code == 422


def test_deep_read_route_calls_deep_read_with_token(isolated_settings, monkeypatch):
    import app.main as main_mod
    import app.routes.ingest as ingest_route
    from app.kg_ingest import github_deep_read

    # Stored github connection → decryptable token
    monkeypatch.setattr(ingest_route.db, "get_connection",
                        lambda cid, prov: {"token_json_encrypted": "enc"})
    monkeypatch.setattr(ingest_route, "decrypt_token_json",
                        lambda enc: '{"access_token": "gho_x"}')

    captured = {}

    def fake_deep_read(facade, eid, repo, *, access_token):
        captured.update(eid=eid, repo=repo, token=access_token)
        return {"ok": True, "repo": repo, "signals": 2, "themes": 1,
                "skipped": 0, "analysis": {"summary": "x", "product_areas": []}}

    monkeypatch.setattr(github_deep_read, "deep_read_repo", fake_deep_read)

    require_company = _override_company(main_mod, ingest_route)
    try:
        client = TestClient(main_mod.app)
        r = client.post("/v1/ingest/github/deep-read", json={"repo": "acme/api"})
    finally:
        main_mod.app.dependency_overrides.pop(require_company, None)
    assert r.status_code == 200
    body = r.json()
    assert body["ok"] is True and body["signals"] == 2
    assert captured == {"eid": "co-X", "repo": "acme/api", "token": "gho_x"}


def test_deep_read_route_404_when_github_not_connected(isolated_settings, monkeypatch):
    import app.main as main_mod
    import app.routes.ingest as ingest_route

    monkeypatch.setattr(ingest_route.db, "get_connection", lambda cid, prov: None)

    require_company = _override_company(main_mod, ingest_route)
    try:
        client = TestClient(main_mod.app)
        r = client.post("/v1/ingest/github/deep-read", json={"repo": "acme/api"})
    finally:
        main_mod.app.dependency_overrides.pop(require_company, None)
    assert r.status_code == 404
