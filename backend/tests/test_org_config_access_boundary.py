"""Access-boundary tests for org-wide company config routes.

Covers the "Roles & Access" checklist item: assert who can mutate the
company's org-wide configuration and that no caller can ever reach another
company's config (cross-tenant isolation).

Routes under test (all behind `require_company` only — see
app/routes/company.py + app/routes/business_context.py):

  PUT  /v1/company/kpi-tree
  PUT  /v1/company/coworkers
  PUT  /v1/company/business-context
  POST /v1/company/business-context/refresh
  GET  variants for read access

Tenancy model (app/auth.require_company): a user belongs to exactly ONE
company; the active company is resolved purely from the JWT — the client
never passes a company id. That makes the org-config routes structurally
single-tenant: a caller can only ever read/write their OWN company's
config, so there is no cross-tenant *path* to leak through. The
cross-tenant tests below pin that invariant (two seeded companies, each
caller only ever sees its own row).

⚠️ ROLE-BOUNDARY FINDING (flagged for triage, NOT a product fix here):
Unlike the Settings → Team write routes (which gate non-admins with 403 —
see test_team_writes.py / test_team_viewer_role.py), these org-wide config
routes are gated on `require_company` ALONE. They do NOT call
`_require_admin`, so a `member` — and even a `viewer` (documented as
"read-only, can comment but not edit", see test_team_viewer_role.py) — can
mutate org-wide config today. The `test_*_is_currently_open_to_*`
characterization tests below PIN that ACTUAL behavior (200, write
persisted) so the gap is visible and a future fix that adds the admin gate
will turn these red on purpose, prompting them to be flipped to assert 403.
These are intentionally written as characterization tests, not as the
desired contract.
"""
from __future__ import annotations

import uuid

import app.auth  # noqa: F401 — ensure app.config/app.auth loaded into sys.modules

from tests._company_helpers import (
    company_client,
    seed_company,
    supabase_bearer,
)


# valid minimal request bodies ------------------------------------------------

_KPI_BODY = {"north_star": {"metric": "WAU", "description": "weekly active users"}}
_COWORKERS_BODY = {"pm": "Ada", "pd": "Bo", "ds": "Cy", "admin": "Dee"}


def _bc_body() -> dict:
    """A minimal-but-valid BusinessContext payload (all-default model)."""
    from app.business_context import BusinessContext

    return BusinessContext().model_dump()


def _set_role(*, company_id: str, user_id: str, role: str) -> None:
    from app.db.client import require_client

    require_client().table("company_members").update({"role": role}).eq(
        "company_id", company_id
    ).eq("user_id", user_id).execute()


def _stored_kpi(company_id: str) -> dict | None:
    from app.kpi_tree import load_kpi_tree

    tree = load_kpi_tree(company_id)
    return tree.model_dump() if tree else None


# ─────────────────────── Auth gate ───────────────────────


def test_org_config_requires_auth(unauth_client, isolated_settings):
    """No bearer → 401 on every org-config route (gate is require_company)."""
    assert unauth_client.put("/v1/company/kpi-tree", json=_KPI_BODY).status_code == 401
    assert unauth_client.put("/v1/company/coworkers", json=_COWORKERS_BODY).status_code == 401
    assert unauth_client.put("/v1/company/business-context", json=_bc_body()).status_code == 401
    assert unauth_client.post("/v1/company/business-context/refresh").status_code == 401


def test_org_config_requires_membership(isolated_settings, monkeypatch):
    """A signed-in user with NO company membership → 403 (require_company)."""
    from tests._company_helpers import setup_supabase_auth
    import importlib
    import sys
    from fastapi.testclient import TestClient
    import app.main as main_mod

    setup_supabase_auth(monkeypatch)
    importlib.reload(sys.modules["app.main"])
    orphan = TestClient(main_mod.app, headers=supabase_bearer("orphan-" + uuid.uuid4().hex[:8]))
    r = orphan.put("/v1/company/kpi-tree", json=_KPI_BODY)
    assert r.status_code == 403


# ─────────────────────── Owner / admin can mutate ───────────────────────


def test_owner_can_write_kpi_tree(isolated_settings, monkeypatch):
    ctx = company_client(monkeypatch)  # seeded as owner
    r = ctx.client.put("/v1/company/kpi-tree", json=_KPI_BODY)
    assert r.status_code == 200, r.text
    assert _stored_kpi(ctx.company_id)["north_star"]["metric"] == "WAU"


def test_admin_can_write_coworkers(isolated_settings, monkeypatch):
    ctx = company_client(monkeypatch)
    _set_role(company_id=ctx.company_id, user_id=ctx.user_id, role="admin")
    r = ctx.client.put("/v1/company/coworkers", json=_COWORKERS_BODY)
    assert r.status_code == 200, r.text
    assert r.json()["coworker_names"]["pm"] == "Ada"


# ─────────────────── Role-boundary CHARACTERIZATION (flagged gap) ───────────────────
#
# These assert the ACTUAL current behavior, which is that non-admins CAN
# mutate org-wide config. They are written so a future admin-gate fix flips
# them red, surfacing the boundary for triage. See the module docstring.


def test_member_can_currently_write_kpi_tree_FLAGGED(isolated_settings, monkeypatch):
    """⚠️ Characterization: a MEMBER can mutate org-wide KPI tree today (200).

    Expected end-state once the admin gate lands: 403. Until then this pins
    the open boundary so the regression is visible."""
    ctx = company_client(monkeypatch)
    _set_role(company_id=ctx.company_id, user_id=ctx.user_id, role="member")
    r = ctx.client.put("/v1/company/kpi-tree", json=_KPI_BODY)
    assert r.status_code == 200, (
        "FLAGGED: member is currently allowed to mutate org-wide config. "
        "If this is now 403, the admin gate landed — flip this test to "
        "assert 403."
    )
    assert _stored_kpi(ctx.company_id) is not None


def test_viewer_can_currently_write_business_context_FLAGGED(isolated_settings, monkeypatch):
    """⚠️ Characterization: a VIEWER (read-only by design) can overwrite the
    org-wide Business Context today (200). Expected end-state: 403."""
    ctx = company_client(monkeypatch)
    _set_role(company_id=ctx.company_id, user_id=ctx.user_id, role="viewer")
    r = ctx.client.put("/v1/company/business-context", json=_bc_body())
    assert r.status_code == 200, (
        "FLAGGED: viewer is currently allowed to mutate org-wide config. "
        "If this is now 403, the admin gate landed — flip this test to "
        "assert 403."
    )


def test_viewer_can_currently_write_coworkers_FLAGGED(isolated_settings, monkeypatch):
    """⚠️ Characterization: a VIEWER can rewrite coworker names today (200).
    Expected end-state: 403."""
    ctx = company_client(monkeypatch)
    _set_role(company_id=ctx.company_id, user_id=ctx.user_id, role="viewer")
    r = ctx.client.put("/v1/company/coworkers", json=_COWORKERS_BODY)
    assert r.status_code == 200, (
        "FLAGGED: viewer is currently allowed to mutate org-wide config. "
        "If this is now 403, the admin gate landed — flip this test to "
        "assert 403."
    )


# ─────────────────────── Read access is open to all members ───────────────────────


def test_member_can_read_kpi_tree(isolated_settings, monkeypatch):
    """Reads are open to any member (typical SaaS 'see the config' UX)."""
    ctx = company_client(monkeypatch)
    ctx.client.put("/v1/company/kpi-tree", json=_KPI_BODY)  # seed as owner
    _set_role(company_id=ctx.company_id, user_id=ctx.user_id, role="member")
    r = ctx.client.get("/v1/company/kpi-tree")
    assert r.status_code == 200
    assert r.json()["north_star"]["metric"] == "WAU"


def test_viewer_can_read_coworkers(isolated_settings, monkeypatch):
    ctx = company_client(monkeypatch)
    _set_role(company_id=ctx.company_id, user_id=ctx.user_id, role="viewer")
    r = ctx.client.get("/v1/company/coworkers")
    assert r.status_code == 200


# ─────────────────────── Cross-tenant isolation ───────────────────────


def test_caller_only_writes_own_company_kpi_tree(isolated_settings, monkeypatch):
    """The active company is resolved from the JWT, so a write lands ONLY in
    the caller's own company — a co-existing foreign company is untouched and
    never reachable. Pins the structural single-tenancy of these routes."""
    ctx = company_client(monkeypatch)  # owns company A
    other_cid = seed_company(user_id="other-owner", slug="rival-co")

    r = ctx.client.put("/v1/company/kpi-tree", json=_KPI_BODY)
    assert r.status_code == 200

    # The caller's company got the write...
    assert _stored_kpi(ctx.company_id)["north_star"]["metric"] == "WAU"
    # ...and the foreign company's config is untouched (no cross-tenant write).
    assert _stored_kpi(other_cid) is None


def test_caller_reads_only_own_company_business_context(isolated_settings, monkeypatch):
    """Two companies, each with distinct business context — each caller sees
    only its own (no cross-tenant read leakage)."""
    ctx = company_client(monkeypatch)  # company A
    # Seed company A's business context via the route (as its owner).
    body = _bc_body()
    body["identity"]["legal_name"] = {
        "value": "Acme Inc", "src": "user", "conf": "high", "as_of": "2026-06-14",
    }
    assert ctx.client.put("/v1/company/business-context", json=body).status_code == 200

    # Company B, different owner + different context, written directly.
    from app.business_context import BusinessContext, Identity, Meta, save_business_context

    other_cid = seed_company(user_id="b-owner", slug="beta-co")
    b_doc = BusinessContext()
    b_doc.identity = Identity(
        legal_name=Meta(value="Beta LLC", src="user", conf="high", as_of="2026-06-14")
    )
    save_business_context(other_cid, b_doc)

    # Caller A reads → sees Acme, never Beta.
    r = ctx.client.get("/v1/company/business-context")
    assert r.status_code == 200
    assert r.json()["identity"]["legal_name"]["value"] == "Acme Inc"

    # Caller B reads → sees Beta, never Acme.
    from fastapi.testclient import TestClient
    import app.main as main_mod

    b_client = TestClient(main_mod.app, headers=supabase_bearer("b-owner"))
    rb = b_client.get("/v1/company/business-context")
    assert rb.status_code == 200
    assert rb.json()["identity"]["legal_name"]["value"] == "Beta LLC"
