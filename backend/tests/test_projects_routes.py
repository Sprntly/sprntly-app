"""Route/DB tests for `db/projects.py` + `routes/projects.py`: project +
membership CRUD, the virtual agent member (AD-P6), and the workspace-scoped
tenant gate (`WorkspaceContext`/`require_workspace` — NOT a dataset slug).

Memory-entry CRUD + the summary read live in `test_project_memory_entries.py`.
"""
from __future__ import annotations

import logging

from tests._company_helpers import company_client
from tests._project_helpers import seed_same_tenant_non_member


def _seed_foreign_project(*, name: str = "Not mine") -> dict:
    """A project scoped to a company/workspace that never resolves through
    `require_workspace` for the caller under test — the tenant-gate probe."""
    from app.db import projects as projects_db

    return projects_db.create_project(
        company_id="foreign-co",
        workspace_id="foreign-ws",
        name=name,
        created_by="someone-else",
    )


def _default_workspace_id(company_id: str) -> str:
    from app.db.workspaces import ensure_default_workspace

    return ensure_default_workspace(company_id)["id"]


# ── Creation ─────────────────────────────────────────────────────────────


def test_create_project_scopes_company_workspace(isolated_settings, monkeypatch):
    ctx = company_client(monkeypatch)
    ws_id = _default_workspace_id(ctx.company_id)

    r = ctx.client.post("/v1/projects", json={"name": "New client launch"})
    assert r.status_code == 200
    body = r.json()
    assert body["company_id"] == ctx.company_id
    assert body["workspace_id"] == ws_id
    assert body["created_by"] == ctx.user_id

    from app.db.client import require_client

    members = (
        require_client()
        .table("project_members")
        .select("*")
        .eq("project_id", body["id"])
        .execute()
        .data
    )
    assert [m["user_id"] for m in members] == [ctx.user_id]


def test_create_project_origin_defaults_manual(isolated_settings, monkeypatch):
    ctx = company_client(monkeypatch)

    r = ctx.client.post("/v1/projects", json={"name": "No origin given"})
    assert r.json()["origin"] == "manual"

    r2 = ctx.client.post(
        "/v1/projects", json={"name": "From an artifact", "origin": "artifact"}
    )
    assert r2.json()["origin"] == "artifact"


# ── Retrieval ────────────────────────────────────────────────────────────


def test_list_projects_recency_order(isolated_settings, monkeypatch):
    ctx = company_client(monkeypatch)
    ws_id = _default_workspace_id(ctx.company_id)

    from app.db import projects as projects_db
    from app.db.client import require_client

    older = projects_db.create_project(
        company_id=ctx.company_id, workspace_id=ws_id, name="Older", created_by=ctx.user_id
    )
    newer = projects_db.create_project(
        company_id=ctx.company_id, workspace_id=ws_id, name="Newer", created_by=ctx.user_id
    )
    client = require_client()
    client.table("projects").update({"updated_at": "2020-01-01T00:00:00"}).eq(
        "id", older["id"]
    ).execute()
    client.table("projects").update({"updated_at": "2030-01-01T00:00:00"}).eq(
        "id", newer["id"]
    ).execute()
    client.table("project_artifacts").insert(
        {"project_id": newer["id"], "artifact_type": "prd", "artifact_id": 1}
    ).execute()

    r = ctx.client.get("/v1/projects")
    assert r.status_code == 200
    rows = r.json()["projects"]
    ids = [row["id"] for row in rows]
    assert ids.index(newer["id"]) < ids.index(older["id"])

    newer_row = next(row for row in rows if row["id"] == newer["id"])
    assert newer_row["artifact_counts"] == {"prd": 1}
    assert newer_row["member_count"] == 1
    assert newer_row["has_group_chat"] is False
    assert newer_row["memory_count"] == 0


def test_get_project_prepends_virtual_agent_member(isolated_settings, monkeypatch):
    ctx = company_client(monkeypatch)
    project = ctx.client.post("/v1/projects", json={"name": "Team project"}).json()

    r = ctx.client.get(f"/v1/projects/{project['id']}")
    assert r.status_code == 200
    members = r.json()["members"]
    assert members[0] == {
        "user_id": None,
        "kind": "agent",
        "name": "Sprntly",
        "role_label": "Agent coworker · dispatches tasks",
        "status": "working",
    }
    assert any(m["user_id"] == ctx.user_id for m in members[1:])

    from app.db.client import require_client

    db_rows = (
        require_client()
        .table("project_members")
        .select("user_id")
        .eq("project_id", project["id"])
        .execute()
        .data
    )
    # DB has exactly the human creator — the agent member is rendered from
    # a constant at the route layer, never a stored row (AD-P6, AC4).
    assert [row["user_id"] for row in db_rows] == [ctx.user_id]


def test_get_project_no_members_still_returns_agent(isolated_settings, monkeypatch):
    """Agent-prepend still happens on an empty human roster. Note: the
    caller themselves must remain a real `project_members` row (the
    membership gate, AD-P11, would otherwise 403 them) — this simulates an
    empty roster at the render layer (`list_members`) rather than deleting
    the caller's own membership, which would just prove the gate again."""
    ctx = company_client(monkeypatch)
    project = ctx.client.post("/v1/projects", json={"name": "Solo"}).json()

    monkeypatch.setattr("app.db.projects.list_members", lambda project_id: [])

    r = ctx.client.get(f"/v1/projects/{project['id']}")
    assert r.status_code == 200
    members = r.json()["members"]
    assert len(members) == 1
    assert members[0]["kind"] == "agent"


def test_get_project_returns_group_chat_id_or_null(isolated_settings, monkeypatch):
    ctx = company_client(monkeypatch)
    project = ctx.client.post("/v1/projects", json={"name": "No chat yet"}).json()

    r = ctx.client.get(f"/v1/projects/{project['id']}")
    assert r.json()["group_chat_id"] is None

    from app.db.client import require_client

    convo = (
        require_client()
        .table("conversations")
        .insert(
            {
                "company_id": ctx.company_id,
                "user_id": ctx.user_id,
                "project_id": project["id"],
                "kind": "group",
            }
        )
        .execute()
        .data[0]
    )

    r2 = ctx.client.get(f"/v1/projects/{project['id']}")
    assert r2.json()["group_chat_id"] == convo["id"]


def test_empty_project_list(isolated_settings, monkeypatch):
    ctx = company_client(monkeypatch)
    r = ctx.client.get("/v1/projects")
    assert r.status_code == 200
    assert r.json()["projects"] == []


# ── Tenant gate (mutation-proofed: this is exactly what a foreign-tenant
# fetch must NOT be able to do) ─────────────────────────────────────────


def test_list_scoped_to_ctx_workspace(isolated_settings, monkeypatch):
    ctx = company_client(monkeypatch)
    ctx.client.post("/v1/projects", json={"name": "Mine"})
    _seed_foreign_project(name="Not mine")

    r = ctx.client.get("/v1/projects")
    assert [p["name"] for p in r.json()["projects"]] == ["Mine"]

    # A `dataset` query param — not declared by this router — has zero
    # effect on scoping (proves the router never reads it, AC5).
    r2 = ctx.client.get("/v1/projects", params={"dataset": "some-other-slug"})
    assert [p["name"] for p in r2.json()["projects"]] == ["Mine"]


def test_get_foreign_workspace_project_404(isolated_settings, monkeypatch):
    ctx = company_client(monkeypatch)
    foreign = _seed_foreign_project()

    r = ctx.client.get(f"/v1/projects/{foreign['id']}")
    assert r.status_code == 404


def test_get_nonexistent_project_404(isolated_settings, monkeypatch):
    ctx = company_client(monkeypatch)
    r = ctx.client.get("/v1/projects/999999")
    assert r.status_code == 404


# ── Membership gate (AD-P11): a SAME-TENANT caller who is not a project
# member must be distinguishable from a fully foreign tenant — 403, not
# 404, and not silently allowed through. ─────────────────────────────────


def test_get_project_same_tenant_non_member_403(isolated_settings, monkeypatch):
    ctx = company_client(monkeypatch)
    project = ctx.client.post("/v1/projects", json={"name": "Owner's project"}).json()
    _, non_member_headers = seed_same_tenant_non_member(ctx)

    r = ctx.client.get(f"/v1/projects/{project['id']}", headers=non_member_headers)
    assert r.status_code == 403

    # The owner (a real member) still reaches it fine — proves this is a
    # membership gate, not an accidental blanket lockout.
    r_owner = ctx.client.get(f"/v1/projects/{project['id']}")
    assert r_owner.status_code == 200


def test_list_excludes_projects_caller_is_not_a_member_of(isolated_settings, monkeypatch):
    ctx = company_client(monkeypatch)
    ctx.client.post("/v1/projects", json={"name": "Owner's project"})
    non_member_id, non_member_headers = seed_same_tenant_non_member(ctx)

    r = ctx.client.get("/v1/projects", headers=non_member_headers)
    assert r.status_code == 200
    assert r.json()["projects"] == []

    # Adding them as a member makes it appear.
    from app.db import projects as projects_db

    owner_project_id = ctx.client.get("/v1/projects").json()["projects"][0]["id"]
    projects_db.add_member(owner_project_id, non_member_id)
    r2 = ctx.client.get("/v1/projects", headers=non_member_headers)
    assert [p["id"] for p in r2.json()["projects"]] == [owner_project_id]


# ── Membership ───────────────────────────────────────────────────────────


def test_add_member_non_member_forbidden(isolated_settings, monkeypatch):
    ctx = company_client(monkeypatch)
    ws_id = _default_workspace_id(ctx.company_id)

    from app.db import projects as projects_db
    from app.db.client import require_client

    project = projects_db.create_project(
        company_id=ctx.company_id,
        workspace_id=ws_id,
        name="Not caller's",
        created_by="someone-else",
    )

    r = ctx.client.post(
        f"/v1/projects/{project['id']}/members", json={"email": "whoever@co.com"}
    )
    assert r.status_code in (403, 404)

    rows = (
        require_client()
        .table("project_members")
        .select("*")
        .eq("project_id", project["id"])
        .execute()
        .data
    )
    assert [row["user_id"] for row in rows] == ["someone-else"]


def test_add_member_by_email_succeeds_for_member(isolated_settings, monkeypatch):
    ctx = company_client(monkeypatch)
    project = ctx.client.post("/v1/projects", json={"name": "Growing team"}).json()

    from app.db.client import require_client

    require_client().table("profiles").insert(
        {"id": "invitee-1", "email": "invitee@co.com"}
    ).execute()

    r = ctx.client.post(
        f"/v1/projects/{project['id']}/members", json={"email": "invitee@co.com"}
    )
    assert r.status_code == 200

    rows = (
        require_client()
        .table("project_members")
        .select("user_id")
        .eq("project_id", project["id"])
        .execute()
        .data
    )
    assert {row["user_id"] for row in rows} == {ctx.user_id, "invitee-1"}


def test_add_member_unknown_email_404(isolated_settings, monkeypatch):
    ctx = company_client(monkeypatch)
    project = ctx.client.post("/v1/projects", json={"name": "Growing team"}).json()

    r = ctx.client.post(
        f"/v1/projects/{project['id']}/members", json={"email": "nobody@co.com"}
    )
    assert r.status_code == 404


# ── Remove member (WAVE INVARIANT: gated on _require_project_member, same
# as every other members/memory/group route) ───────────────────────────────


def test_remove_member_by_another_member_succeeds(isolated_settings, monkeypatch):
    ctx = company_client(monkeypatch)
    project = ctx.client.post("/v1/projects", json={"name": "Growing team"}).json()

    from app.db import projects as projects_db
    from app.db.client import require_client

    other_id, other_headers = seed_same_tenant_non_member(ctx)
    projects_db.add_member(project["id"], other_id)

    r = ctx.client.delete(f"/v1/projects/{project['id']}/members/{other_id}")
    assert r.status_code == 200, r.text
    assert r.json() == {"removed": True}

    rows = (
        require_client()
        .table("project_members")
        .select("user_id")
        .eq("project_id", project["id"])
        .execute()
        .data
    )
    assert [row["user_id"] for row in rows] == [ctx.user_id]

    # AC1: membership is re-checked per-request — the removed user is now a
    # same-tenant non-member and gets 403, not a stale 200.
    r2 = ctx.client.get(f"/v1/projects/{project['id']}", headers=other_headers)
    assert r2.status_code == 403


def test_remove_member_creator_cannot_be_removed(isolated_settings, monkeypatch):
    ctx = company_client(monkeypatch)
    project = ctx.client.post("/v1/projects", json={"name": "Solo→team"}).json()

    from app.db import projects as projects_db
    from app.db.client import require_client

    other_id, other_headers = seed_same_tenant_non_member(ctx)
    projects_db.add_member(project["id"], other_id)

    # Another member (not the creator) tries to remove the creator.
    r = ctx.client.delete(
        f"/v1/projects/{project['id']}/members/{ctx.user_id}", headers=other_headers
    )
    assert r.status_code == 409

    rows = (
        require_client()
        .table("project_members")
        .select("user_id")
        .eq("project_id", project["id"])
        .execute()
        .data
    )
    assert ctx.user_id in [row["user_id"] for row in rows]


def test_remove_member_self_removal_rejected(isolated_settings, monkeypatch):
    ctx = company_client(monkeypatch)
    project = ctx.client.post("/v1/projects", json={"name": "No leaving yet"}).json()

    from app.db.client import require_client

    r = ctx.client.delete(f"/v1/projects/{project['id']}/members/{ctx.user_id}")
    assert r.status_code == 400

    rows = (
        require_client()
        .table("project_members")
        .select("user_id")
        .eq("project_id", project["id"])
        .execute()
        .data
    )
    assert ctx.user_id in [row["user_id"] for row in rows]


def test_remove_member_non_member_target_404(isolated_settings, monkeypatch):
    ctx = company_client(monkeypatch)
    project = ctx.client.post("/v1/projects", json={"name": "Just the creator"}).json()

    r = ctx.client.delete(f"/v1/projects/{project['id']}/members/never-added")
    assert r.status_code == 404


def test_remove_member_non_member_caller_403(isolated_settings, monkeypatch):
    ctx = company_client(monkeypatch)
    project = ctx.client.post("/v1/projects", json={"name": "Gated"}).json()
    _, non_member_headers = seed_same_tenant_non_member(ctx)

    r = ctx.client.delete(
        f"/v1/projects/{project['id']}/members/{ctx.user_id}", headers=non_member_headers
    )
    assert r.status_code == 403


def test_remove_member_foreign_tenant_project_404(isolated_settings, monkeypatch):
    ctx = company_client(monkeypatch)
    foreign = _seed_foreign_project()

    r = ctx.client.delete(f"/v1/projects/{foreign['id']}/members/{ctx.user_id}")
    assert r.status_code == 404


def test_member_removed_log_carries_only_identifiers(isolated_settings, monkeypatch, caplog):
    ctx = company_client(monkeypatch)
    project = ctx.client.post("/v1/projects", json={"name": "Logged team"}).json()

    from app.db import projects as projects_db

    other_id, _ = seed_same_tenant_non_member(ctx)
    projects_db.add_member(project["id"], other_id)

    with caplog.at_level(logging.INFO, logger="app.routes.projects"):
        r = ctx.client.delete(f"/v1/projects/{project['id']}/members/{other_id}")
    assert r.status_code == 200
    lines = [rec.getMessage() for rec in caplog.records]
    expected = f"member_removed project_id={project['id']} target_user_id={other_id} by={ctx.user_id}"
    assert any(line == expected for line in lines)


# ── Observability ────────────────────────────────────────────────────────


def test_project_created_log_carries_only_identifiers(isolated_settings, monkeypatch, caplog):
    ctx = company_client(monkeypatch)
    with caplog.at_level(logging.INFO, logger="app.routes.projects"):
        r = ctx.client.post("/v1/projects", json={"name": "Logged project"})
    assert r.status_code == 200
    project_id = r.json()["id"]
    lines = [rec.getMessage() for rec in caplog.records]
    assert any(f"project_created project_id={project_id}" == line for line in lines)
