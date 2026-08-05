"""Route-level coverage for the bare-link guest-access primitive
(/v1/prd-access/*) — the token-less sibling of artifact_share.py, keyed by
a PRD's public_id instead of a minted share token. Mirrors
test_routes_artifact_share.py's coverage shape for the equivalent routes.
"""
from __future__ import annotations

import importlib
import sys
import uuid

import pytest
from fastapi.testclient import TestClient

from tests._company_helpers import (
    company_client,
    seed_company,
    setup_supabase_auth,
    supabase_bearer,
)


def _bare_client(monkeypatch, user_id: str) -> TestClient:
    setup_supabase_auth(monkeypatch)
    import app.main as main_mod

    importlib.reload(sys.modules["app.main"])
    return TestClient(main_mod.app, headers=supabase_bearer(user_id))


def _seed_prd_with_public_id(db, dataset: str, *, title: str = "t") -> tuple[int, str]:
    """Seed a real PRD and stamp it with a real uuid4 public_id (the sqlite
    test schema has no gen_random_uuid() default — see conftest's own note
    on the prds table). Returns (prd_id, public_id)."""
    from app.db.client import require_client

    brief_id = db.save_brief(dataset, "W", {"insights": []}, schema_version=1)
    prd_id = db.start_prd(
        brief_id=brief_id, insight_index=0, title=title, template_version=1, variant="v2"
    )
    public_id = str(uuid.uuid4())
    require_client().table("prds").update({"public_id": public_id}).eq("id", prd_id).execute()
    return prd_id, public_id


# ── metadata ────────────────────────────────────────────────────────────


def test_metadata_route_returns_fields(isolated_settings, monkeypatch):
    ctx = company_client(monkeypatch)
    db = isolated_settings["db"]
    from app.db.client import require_client

    require_client().table("profiles").insert(
        {"id": ctx.user_id, "email": f"{ctx.user_id}@acme.com"}
    ).execute()
    prd_id, public_id = _seed_prd_with_public_id(db, "acme", title="Q3 Retention PRD")

    r = ctx.client.get(f"/v1/prd-access/{public_id}")

    assert r.status_code == 200
    body = r.json()
    assert body["title"] == "Q3 Retention PRD"
    assert body["owning_company_name"]
    assert "sharer_name" not in body  # no share row here — nothing to attribute


def test_metadata_route_404_on_unknown_uuid(isolated_settings, monkeypatch):
    ctx = company_client(monkeypatch)
    r = ctx.client.get(f"/v1/prd-access/{uuid.uuid4()}")
    assert r.status_code == 404


def test_metadata_route_404_not_500_on_malformed_non_uuid(isolated_settings, monkeypatch):
    """Regression: a non-UUID string (e.g. a stale bare-integer ?prd= link)
    must 404 cleanly — resolve_prd_id_by_public_id validates UUID shape
    before ever querying, so a malformed value never reaches the DB layer
    and can't raise a query error."""
    ctx = company_client(monkeypatch)
    r = ctx.client.get("/v1/prd-access/not-a-uuid-at-all")
    assert r.status_code == 404

    r2 = ctx.client.get("/v1/prd-access/1881")
    assert r2.status_code == 404


# ── resolve ─────────────────────────────────────────────────────────────


def test_resolve_route_same_company_member(isolated_settings, monkeypatch):
    """Was `..._guest_view` until 2026-08-04. A caller who can act in the
    prd's owning workspace now resolves to `member` and is routed into the
    real, EDITABLE app — handing every same-company caller the read-only
    guest shell is what stopped colleagues editing shared PRDs and tickets.
    `guest_view` is still returned when the caller has no access to that
    workspace; see tests/test_share_member_edit_access.py for both sides."""
    ctx = company_client(monkeypatch)
    db = isolated_settings["db"]
    prd_id, public_id = _seed_prd_with_public_id(db, "acme")
    # Bind the dataset, or `user_can_act_in_workspace` takes its unbound
    # short-circuit and this asserts nothing about workspace scoping.
    # seed_company creates no datasets row — see _bind_dataset in
    # tests/test_share_member_edit_access.py for the full explanation.
    from app.db.client import require_client

    default_ws = ctx.client.get("/v1/workspaces").json()["workspaces"][0]
    require_client().table("datasets").insert(
        {"slug": "acme", "display_name": "Acme", "workspace_id": default_ws["id"]}
    ).execute()

    r = ctx.client.get(f"/v1/prd-access/{public_id}/resolve")

    assert r.status_code == 200
    body = r.json()
    assert body["outcome"] == "member"
    assert body["artifact_id"] == prd_id


def test_resolve_route_different_company_blocked(isolated_settings, monkeypatch):
    ctx = company_client(monkeypatch)
    db = isolated_settings["db"]
    prd_id, public_id = _seed_prd_with_public_id(db, "acme")

    other_user = "other-" + uuid.uuid4().hex[:8]
    seed_company(user_id=other_user, slug="rival")
    client = _bare_client(monkeypatch, other_user)

    r = client.get(f"/v1/prd-access/{public_id}/resolve")

    assert r.status_code == 200
    assert r.json() == {"outcome": "blocked", "reason": "different_company"}


def test_resolve_route_zero_company_blocked(isolated_settings, monkeypatch):
    ctx = company_client(monkeypatch)
    db = isolated_settings["db"]
    prd_id, public_id = _seed_prd_with_public_id(db, "acme")

    fresh_user = "fresh-" + uuid.uuid4().hex[:8]
    client = _bare_client(monkeypatch, fresh_user)

    r = client.get(f"/v1/prd-access/{public_id}/resolve")

    assert r.status_code == 200
    assert r.json() == {"outcome": "blocked", "reason": "different_company"}


def test_resolve_route_unknown_public_id_returns_404(isolated_settings, monkeypatch):
    client = _bare_client(monkeypatch, "whoever-" + uuid.uuid4().hex[:8])
    r = client.get(f"/v1/prd-access/{uuid.uuid4()}/resolve")
    assert r.status_code == 404


# ── auto-join-company ────────────────────────────────────────────────────


def test_auto_join_company_route_grants_on_matching_domain(isolated_settings, monkeypatch):
    creator_id = "creator-" + uuid.uuid4().hex[:8]
    company_id = seed_company(user_id=creator_id, slug="acme-prdaccess1")
    from app.db.client import require_client

    require_client().table("profiles").insert(
        {"id": creator_id, "email": "creator@acme-prdaccess1.com"}
    ).execute()

    db = isolated_settings["db"]
    prd_id, public_id = _seed_prd_with_public_id(db, "acme-prdaccess1")

    fresh_user = "fresh-" + uuid.uuid4().hex[:8]
    require_client().table("profiles").insert(
        {"id": fresh_user, "email": "joiner@acme-prdaccess1.com"}
    ).execute()
    client = _bare_client(monkeypatch, fresh_user)

    r = client.post(f"/v1/prd-access/{public_id}/auto-join-company")

    assert r.status_code == 200
    assert r.json() == {"joined_company_id": company_id}


def test_auto_join_company_route_200_no_op_on_mismatched_domain(isolated_settings, monkeypatch):
    seed_company(user_id="creator-" + uuid.uuid4().hex[:8], slug="acme-prdaccess2")
    db = isolated_settings["db"]
    prd_id, public_id = _seed_prd_with_public_id(db, "acme-prdaccess2")

    fresh_user = "fresh-" + uuid.uuid4().hex[:8]
    from app.db.client import require_client

    require_client().table("profiles").insert(
        {"id": fresh_user, "email": "joiner@other.com"}
    ).execute()
    client = _bare_client(monkeypatch, fresh_user)

    r = client.post(f"/v1/prd-access/{public_id}/auto-join-company")

    assert r.status_code == 200
    assert r.json() == {"joined_company_id": None}


def test_auto_join_company_route_200_no_op_on_unknown_public_id(isolated_settings, monkeypatch):
    client = _bare_client(monkeypatch, "whoever-" + uuid.uuid4().hex[:8])
    r = client.post(f"/v1/prd-access/{uuid.uuid4()}/auto-join-company")
    assert r.status_code == 200
    assert r.json() == {"joined_company_id": None}


def test_auto_join_company_route_200_no_op_on_malformed_public_id(isolated_settings, monkeypatch):
    """Same non-disclosure convention as the metadata route — a malformed
    value never reaches the DB layer and never 500s."""
    client = _bare_client(monkeypatch, "whoever-" + uuid.uuid4().hex[:8])
    r = client.post("/v1/prd-access/not-a-uuid/auto-join-company")
    assert r.status_code == 200
    assert r.json() == {"joined_company_id": None}


# ── join ──────────────────────────────────────────────────────────────────


def test_join_grants_workspace_membership(isolated_settings, monkeypatch):
    ctx = company_client(monkeypatch)
    default_ws = ctx.client.get("/v1/workspaces").json()["workspaces"][0]
    db = isolated_settings["db"]
    prd_id, public_id = _seed_prd_with_public_id(db, "acme")

    from app.db.client import require_client

    # seed_company (a raw DB helper, not the real signup flow) doesn't bind
    # the "acme" dataset to a workspace the way real onboarding would —
    # owning_info_for_prd needs that binding to resolve a real workspace_id
    # (join must fail-closed to 409 without one, per its own docstring).
    require_client().table("datasets").upsert(
        {"slug": "acme", "display_name": "Acme", "workspace_id": default_ws["id"]}
    ).execute()

    member_id = "member-" + uuid.uuid4().hex[:8]
    require_client().table("company_members").insert(
        {"id": uuid.uuid4().hex, "company_id": ctx.company_id, "user_id": member_id, "role": "member"}
    ).execute()
    client = _bare_client(monkeypatch, member_id)

    r = client.post(f"/v1/prd-access/{public_id}/join")

    assert r.status_code == 200
    body = r.json()
    assert body["workspace_id"] == default_ws["id"]

    from app.db.workspaces import get_workspace_member

    assert get_workspace_member(default_ws["id"], member_id) is not None


def test_join_denies_blocked_caller(isolated_settings, monkeypatch):
    db = isolated_settings["db"]
    seed_company(user_id="creator-" + uuid.uuid4().hex[:8], slug="acme-join-blocked")
    prd_id, public_id = _seed_prd_with_public_id(db, "acme-join-blocked")

    fresh_user = "fresh-" + uuid.uuid4().hex[:8]
    client = _bare_client(monkeypatch, fresh_user)

    r = client.post(f"/v1/prd-access/{public_id}/join")

    assert r.status_code == 403


def test_join_unknown_public_id_returns_404(isolated_settings, monkeypatch):
    client = _bare_client(monkeypatch, "whoever-" + uuid.uuid4().hex[:8])
    r = client.post(f"/v1/prd-access/{uuid.uuid4()}/join")
    assert r.status_code == 404


# ── content ───────────────────────────────────────────────────────────────


def test_content_route_returns_rendered_prd(isolated_settings, monkeypatch):
    ctx = company_client(monkeypatch)
    db = isolated_settings["db"]
    prd_id, public_id = _seed_prd_with_public_id(db, "acme", title="Bare-link content PRD")

    r = ctx.client.get(f"/v1/prd-access/{public_id}/content")

    assert r.status_code == 200
    body = r.json()
    assert body["prd"]["title"] == "Bare-link content PRD"
    assert body["prd"]["public_id"] == public_id


def test_content_route_denies_blocked_caller(isolated_settings, monkeypatch):
    db = isolated_settings["db"]
    seed_company(user_id="creator-" + uuid.uuid4().hex[:8], slug="acme-content-blocked")
    prd_id, public_id = _seed_prd_with_public_id(db, "acme-content-blocked")

    fresh_user = "fresh-" + uuid.uuid4().hex[:8]
    client = _bare_client(monkeypatch, fresh_user)

    r = client.get(f"/v1/prd-access/{public_id}/content")

    assert r.status_code == 404
