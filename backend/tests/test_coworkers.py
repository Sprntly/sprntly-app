"""Tests for coworker names (design-v4 onboarding page 07) — model,
storage, and the GET/PUT /v1/company/coworkers routes."""
from __future__ import annotations

from types import SimpleNamespace

from fastapi.testclient import TestClient

from app.coworkers import COWORKER_SLOTS, CoworkerNames


# ---------- model ----------

def test_slots_and_defaults():
    assert COWORKER_SLOTS == ("pm", "pd", "ds", "admin")
    n = CoworkerNames()
    assert n.model_dump() == {"pm": "", "pd": "", "ds": "", "admin": ""}


def test_names_are_trimmed_and_clamped():
    n = CoworkerNames(pm="  Atlas  ", pd="x" * 80)
    assert n.pm == "Atlas"
    assert len(n.pd) == 40


# ---------- storage (inline fake, mirrors test_kpi_tree) ----------

def _client_with(names_raw):
    class FakeQ:
        def __init__(self): self.updated = None
        def select(self, *_): return self
        def eq(self, *_): return self
        def update(self, patch): self.updated = patch; return self
        def execute(self):
            return SimpleNamespace(data=[{"coworker_names": names_raw}])
    q = FakeQ()
    return type("C", (), {"table": lambda s, n: q})(), q


def test_load_returns_empty_for_unset(monkeypatch):
    import app.coworkers as cw
    client, _ = _client_with({})
    monkeypatch.setattr(cw, "require_client", lambda: client)
    assert cw.load_coworker_names("e").model_dump() == \
        {"pm": "", "pd": "", "ds": "", "admin": ""}


def test_load_ignores_unknown_slots(monkeypatch):
    import app.coworkers as cw
    client, _ = _client_with({"pm": "Atlas", "bogus": "nope"})
    monkeypatch.setattr(cw, "require_client", lambda: client)
    loaded = cw.load_coworker_names("e")
    assert loaded.pm == "Atlas"
    assert "bogus" not in loaded.model_dump()


def test_save_writes_only_known_slots(monkeypatch):
    import app.coworkers as cw
    client, q = _client_with({})
    monkeypatch.setattr(cw, "require_client", lambda: client)
    cw.save_coworker_names("e", CoworkerNames(pm="Atlas", ds="Vera"))
    assert q.updated["coworker_names"] == \
        {"pm": "Atlas", "pd": "", "ds": "Vera", "admin": ""}


# ---------- routes (override require_company via the route module's
#            OWN captured reference — module reloads make a fresh
#            import a different object) ----------

def _route_client(isolated_settings, company_id: str):
    import app.main as main_mod
    from app.auth import CompanyContext
    import app.routes.company as company_route

    # Seed the company row so the real fake-supabase update lands.
    db = isolated_settings["supabase"]
    db.table("companies").insert(
        {"id": company_id, "slug": "acme", "display_name": "Acme"}
    ).execute()

    main_mod.app.dependency_overrides[company_route.require_company] = (
        lambda: CompanyContext(company_id=company_id, role="owner", user_id="u1")
    )
    return TestClient(main_mod.app), company_route


def test_get_coworkers_defaults_to_empty(isolated_settings):
    client, route = _route_client(isolated_settings, "co-1")
    try:
        r = client.get("/v1/company/coworkers")
    finally:
        main_clear(route)
    assert r.status_code == 200
    assert r.json() == {"pm": "", "pd": "", "ds": "", "admin": ""}


def test_put_then_get_roundtrips(isolated_settings):
    client, route = _route_client(isolated_settings, "co-2")
    try:
        put = client.put(
            "/v1/company/coworkers",
            json={"pm": "Atlas", "pd": "Juno", "ds": "Vera", "admin": "Ada"},
        )
        get = client.get("/v1/company/coworkers")
    finally:
        main_clear(route)
    assert put.status_code == 200
    assert put.json()["ok"] is True
    assert get.json() == {"pm": "Atlas", "pd": "Juno", "ds": "Vera", "admin": "Ada"}


def main_clear(company_route):
    import app.main as main_mod
    main_mod.app.dependency_overrides.pop(company_route.require_company, None)
