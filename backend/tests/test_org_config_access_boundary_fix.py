"""Access-boundary tests for org-wide company config (v0 checklist 2.2).

The fix: org-wide company-config WRITES must be admin-only — a `member` or a
read-only `viewer` must not be able to mutate the org's KPI tree, coworker
names, or business-context (the bug was that these routes gated on
`require_company` alone, so non-admins got 200). READS stay open to any member
(members/viewers can still view org config).

Routes under test:
  PUT  /v1/company/kpi-tree                  — admin only (write)
  PUT  /v1/company/coworkers                 — admin only (write)
  PUT  /v1/company/business-context          — admin only (write)
  POST /v1/company/business-context/refresh  — admin only (mutation)

  GET  /v1/company/kpi-tree                   — open to members
  GET  /v1/company/coworkers                  — open to members
  GET  /v1/company/business-context           — open to members

Permission model mirrors the Settings → Team write routes
(app/routes/team.py::_require_admin): owner/admin may mutate; member/viewer
get 403. This is the SAME helper the team write tests pin.

Companion note (merge ordering): PR #331 (branch qa/v0-test-backfill) added
tests in test_org_config_access_boundary.py whose names end `_FLAGGED` and
assert the CURRENT buggy 200s. Once BOTH this branch and #331 merge, those
`_FLAGGED` tests must be flipped to assert 403 (they pin the bug, this file
pins the fix).
"""
from __future__ import annotations

import app.auth  # noqa: F401 — ensure app.config/app.auth in sys.modules

from app.business_context import BusinessContext, Meta
from tests._company_helpers import company_client


# ─────────────────────── helpers ───────────────────────


def _set_role(*, company_id: str, user_id: str, role: str) -> None:
    """Demote/promote the seeded owner to a different role for permission tests."""
    from app.db.client import require_client

    require_client().table("company_members").update({"role": role}).eq(
        "company_id", company_id
    ).eq("user_id", user_id).execute()


_KPI_BODY = {
    "north_star": {"metric": "Weekly active teams", "description": "north star"},
    "primary_metrics": [{"metric": "Activation rate", "description": "first value"}],
    "secondary_signals": [],
}

_COWORKER_BODY = {"pm": "Ada", "pd": "Grace", "ds": "Linus", "admin": "Hopper"}


def _bc_body() -> dict:
    body = BusinessContext()
    body.identity.legal_name = Meta(value="Acme", src="user", conf="high")
    return body.model_dump()


# ─────────────────────── PUT /v1/company/kpi-tree (write) ───────────────────────


def test_kpi_tree_put_403_when_member(isolated_settings, monkeypatch):
    ctx = company_client(monkeypatch)
    _set_role(company_id=ctx.company_id, user_id=ctx.user_id, role="member")
    r = ctx.client.put("/v1/company/kpi-tree", json=_KPI_BODY)
    assert r.status_code == 403, r.text


def test_kpi_tree_put_403_when_viewer(isolated_settings, monkeypatch):
    ctx = company_client(monkeypatch)
    _set_role(company_id=ctx.company_id, user_id=ctx.user_id, role="viewer")
    r = ctx.client.put("/v1/company/kpi-tree", json=_KPI_BODY)
    assert r.status_code == 403, r.text


def test_kpi_tree_put_200_when_admin(isolated_settings, monkeypatch):
    ctx = company_client(monkeypatch)
    _set_role(company_id=ctx.company_id, user_id=ctx.user_id, role="admin")
    r = ctx.client.put("/v1/company/kpi-tree", json=_KPI_BODY)
    assert r.status_code == 200, r.text
    assert r.json()["ok"] is True


def test_kpi_tree_put_200_when_owner(isolated_settings, monkeypatch):
    ctx = company_client(monkeypatch)  # seeded as owner
    r = ctx.client.put("/v1/company/kpi-tree", json=_KPI_BODY)
    assert r.status_code == 200, r.text


# ─────────────────────── PUT /v1/company/coworkers (write) ───────────────────────


def test_coworkers_put_403_when_member(isolated_settings, monkeypatch):
    ctx = company_client(monkeypatch)
    _set_role(company_id=ctx.company_id, user_id=ctx.user_id, role="member")
    r = ctx.client.put("/v1/company/coworkers", json=_COWORKER_BODY)
    assert r.status_code == 403, r.text


def test_coworkers_put_403_when_viewer(isolated_settings, monkeypatch):
    ctx = company_client(monkeypatch)
    _set_role(company_id=ctx.company_id, user_id=ctx.user_id, role="viewer")
    r = ctx.client.put("/v1/company/coworkers", json=_COWORKER_BODY)
    assert r.status_code == 403, r.text


def test_coworkers_put_200_when_admin(isolated_settings, monkeypatch):
    ctx = company_client(monkeypatch)
    _set_role(company_id=ctx.company_id, user_id=ctx.user_id, role="admin")
    r = ctx.client.put("/v1/company/coworkers", json=_COWORKER_BODY)
    assert r.status_code == 200, r.text
    assert r.json()["ok"] is True


# ─────────────────── PUT /v1/company/business-context (write) ───────────────────


def test_business_context_put_403_when_member(isolated_settings, monkeypatch):
    ctx = company_client(monkeypatch)
    _set_role(company_id=ctx.company_id, user_id=ctx.user_id, role="member")
    r = ctx.client.put("/v1/company/business-context", json=_bc_body())
    assert r.status_code == 403, r.text


def test_business_context_put_403_when_viewer(isolated_settings, monkeypatch):
    ctx = company_client(monkeypatch)
    _set_role(company_id=ctx.company_id, user_id=ctx.user_id, role="viewer")
    r = ctx.client.put("/v1/company/business-context", json=_bc_body())
    assert r.status_code == 403, r.text


def test_business_context_put_200_when_admin(isolated_settings, monkeypatch):
    ctx = company_client(monkeypatch)
    _set_role(company_id=ctx.company_id, user_id=ctx.user_id, role="admin")
    r = ctx.client.put("/v1/company/business-context", json=_bc_body())
    assert r.status_code == 200, r.text
    assert r.json()["ok"] is True


# ─────────────── POST /v1/company/business-context/refresh (mutation) ───────────────


def test_business_context_refresh_403_when_member(isolated_settings, monkeypatch):
    ctx = company_client(monkeypatch)
    _set_role(company_id=ctx.company_id, user_id=ctx.user_id, role="member")
    r = ctx.client.post("/v1/company/business-context/refresh")
    # 403 from the admin gate — and the gate must run BEFORE the agent, so we
    # never reach the (network-touching) refresh path for a non-admin.
    assert r.status_code == 403, r.text


def test_business_context_refresh_403_when_viewer(isolated_settings, monkeypatch):
    ctx = company_client(monkeypatch)
    _set_role(company_id=ctx.company_id, user_id=ctx.user_id, role="viewer")
    r = ctx.client.post("/v1/company/business-context/refresh")
    assert r.status_code == 403, r.text


def test_business_context_refresh_runs_for_admin(isolated_settings, monkeypatch):
    """Admin passes the gate; stub the agent so we assert reachability, not network."""
    ctx = company_client(monkeypatch)
    _set_role(company_id=ctx.company_id, user_id=ctx.user_id, role="admin")

    import app.routes.business_context as routes

    monkeypatch.setattr(
        routes,
        "run_business_context",
        lambda facade, company_id: {"version": 1, "fields_filled": []},
    )
    r = ctx.client.post("/v1/company/business-context/refresh")
    assert r.status_code == 200, r.text
    assert r.json()["ok"] is True


# ─────────────────────── READS stay open to members ───────────────────────


def test_kpi_tree_get_open_to_member(isolated_settings, monkeypatch):
    """A member can READ the org KPI tree (only writes are gated)."""
    ctx = company_client(monkeypatch)
    # Seed a tree as the owner first.
    assert ctx.client.put("/v1/company/kpi-tree", json=_KPI_BODY).status_code == 200
    _set_role(company_id=ctx.company_id, user_id=ctx.user_id, role="member")
    r = ctx.client.get("/v1/company/kpi-tree")
    assert r.status_code == 200, r.text
    assert r.json()["north_star"]["metric"] == "Weekly active teams"


def test_kpi_tree_get_open_to_viewer(isolated_settings, monkeypatch):
    ctx = company_client(monkeypatch)
    assert ctx.client.put("/v1/company/kpi-tree", json=_KPI_BODY).status_code == 200
    _set_role(company_id=ctx.company_id, user_id=ctx.user_id, role="viewer")
    r = ctx.client.get("/v1/company/kpi-tree")
    assert r.status_code == 200, r.text


def test_coworkers_get_open_to_member(isolated_settings, monkeypatch):
    ctx = company_client(monkeypatch)
    _set_role(company_id=ctx.company_id, user_id=ctx.user_id, role="member")
    r = ctx.client.get("/v1/company/coworkers")
    assert r.status_code == 200, r.text


def test_business_context_get_open_to_member(isolated_settings, monkeypatch):
    """A member can READ business-context. Seed a doc as admin, demote, then GET."""
    ctx = company_client(monkeypatch)
    assert (
        ctx.client.put("/v1/company/business-context", json=_bc_body()).status_code
        == 200
    )
    _set_role(company_id=ctx.company_id, user_id=ctx.user_id, role="member")
    r = ctx.client.get("/v1/company/business-context")
    assert r.status_code == 200, r.text
    assert r.json()["identity"]["legal_name"]["value"] == "Acme"
