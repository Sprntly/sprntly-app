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


# ── PRD-auto dedup (create-modal "Auto · from PRD" tab, AD-P9) ────────────
#
# FIX: re-selecting an already-forked PRD in the create-modal used to call
# `projectsApi.create` unconditionally, minting a SECOND identical
# `prd_auto` project. The route now dedupes server-side
# (`find_existing_prd_auto_project`, `app/project_from_prd.py`) whenever
# `origin="prd_auto"` + `prd_id` are both sent — keyed on the
# `project_artifacts` ref (`artifact_type='prd', artifact_id=prd_id`) scoped
# to `origin='prd_auto'` projects, which is the ONE fact BOTH fork paths
# (the generation-time hook and the create-modal's own follow-up
# `POST .../artifacts` call) always write — so it catches a re-fork via
# EITHER path, not just a hook-then-modal sequence.


def _seed_owned_prd(*, dataset: str, title: str = "Dark mode PRD") -> int:
    """A real `prds` row `require_owned_prd` (AD-P12) will accept for a
    caller whose company slug is `dataset` — mirrors the ownership chain
    `deps/ownership.py` walks (prd -> brief -> dataset (slug) -> company),
    same helper shape as `test_project_artifacts_fanout.py`'s `_seed_prd`.
    `company_client` seeds its company with slug "acme" by default."""
    from app.db.client import require_client

    brief = (
        require_client()
        .table("briefs")
        .insert({"dataset": dataset, "week_label": "wk", "payload": {}, "is_current": True})
        .execute()
        .data[0]
    )
    prd = (
        require_client()
        .table("prds")
        .insert({"brief_id": brief["id"], "insight_index": 0, "title": title, "status": "ready"})
        .execute()
        .data[0]
    )
    return prd["id"]


def test_create_prd_auto_two_consecutive_modal_forks_dedupe(isolated_settings, monkeypatch):
    """THE exact repro: two consecutive create-modal forks of the SAME PRD
    — `POST /v1/projects {origin:'prd_auto', prd_id:X}` then the client's
    follow-up `POST .../artifacts`, TWICE in a row. Neither call ever binds
    a conversation (the modal path never does). The second create must
    return the FIRST's project — one project, one artifact ref, total."""
    ctx = company_client(monkeypatch)
    prd_id = _seed_owned_prd(dataset="acme")

    first = ctx.client.post(
        "/v1/projects", json={"name": "Dark mode PRD", "origin": "prd_auto", "prd_id": prd_id}
    )
    assert first.status_code == 200
    first_project_id = first.json()["id"]
    add_ref = ctx.client.post(
        f"/v1/projects/{first_project_id}/artifacts",
        json={"artifact_type": "prd", "artifact_id": prd_id},
    )
    assert add_ref.status_code == 200

    # Second modal-path fork of the SAME PRD — the repro.
    second = ctx.client.post(
        "/v1/projects", json={"name": "Dark mode PRD", "origin": "prd_auto", "prd_id": prd_id}
    )
    assert second.status_code == 200
    assert second.json()["id"] == first_project_id

    from app.db.client import require_client

    projects = (
        require_client()
        .table("projects")
        .select("id")
        .eq("company_id", ctx.company_id)
        .execute()
        .data
    )
    assert len(projects) == 1

    artifact_refs = (
        require_client()
        .table("project_artifacts")
        .select("project_id, artifact_type, artifact_id")
        .eq("artifact_type", "prd")
        .eq("artifact_id", prd_id)
        .execute()
        .data
    )
    assert len(artifact_refs) == 1
    assert artifact_refs[0]["project_id"] == first_project_id


def test_create_prd_auto_reuses_project_already_forked_by_generation_hook(
    isolated_settings, monkeypatch
):
    ctx = company_client(monkeypatch)
    ws_id = _default_workspace_id(ctx.company_id)

    from app.db.client import require_client

    conv_id = (
        require_client()
        .table("conversations")
        .insert(
            {
                "company_id": ctx.company_id,
                "user_id": ctx.user_id,
                "title": "generate prd",
                "query": "generate prd",
                "agent_type": "ask",
            }
        )
        .execute()
        .data[0]["id"]
    )

    from app.project_from_prd import maybe_auto_create_project_for_prd

    prd_id = 909
    forked_id = maybe_auto_create_project_for_prd(
        company_id=ctx.company_id,
        workspace_id=ws_id,
        user_id=ctx.user_id,
        prd_id=prd_id,
        prd_title="Dark mode PRD",
        conversation_id=conv_id,
    )
    assert forked_id is not None

    # The create-modal's "Auto · from PRD" tab re-selects the SAME PRD.
    r = ctx.client.post(
        "/v1/projects",
        json={"name": "Dark mode PRD", "origin": "prd_auto", "prd_id": prd_id},
    )
    assert r.status_code == 200
    assert r.json()["id"] == forked_id

    projects = (
        require_client()
        .table("projects")
        .select("id")
        .eq("company_id", ctx.company_id)
        .execute()
        .data
    )
    assert len(projects) == 1


def test_create_prd_auto_no_existing_fork_creates_normally(isolated_settings, monkeypatch):
    """No `project_artifacts` ref for this `prd_id` exists yet — the dedup
    check finds nothing, so a real project is created (the
    not-a-duplicate control for the tests above)."""
    ctx = company_client(monkeypatch)

    r = ctx.client.post(
        "/v1/projects",
        json={"name": "Fresh fork", "origin": "prd_auto", "prd_id": 12345},
    )
    assert r.status_code == 200
    assert r.json()["origin"] == "prd_auto"

    from app.db.client import require_client

    projects = (
        require_client()
        .table("projects")
        .select("id")
        .eq("company_id", ctx.company_id)
        .execute()
        .data
    )
    assert len(projects) == 1


def test_create_prd_auto_without_prd_id_is_unaffected(isolated_settings, monkeypatch):
    """Backward-compat: `origin="prd_auto"` with no `prd_id` sent (an older
    client, or a caller that never resolves one) skips the dedup check
    entirely rather than erroring."""
    ctx = company_client(monkeypatch)

    r = ctx.client.post("/v1/projects", json={"name": "No prd_id", "origin": "prd_auto"})
    assert r.status_code == 200
    assert r.json()["origin"] == "prd_auto"


def test_create_manual_and_artifact_origins_ignore_prd_id(isolated_settings, monkeypatch):
    """The dedup check is scoped to `origin="prd_auto"` ONLY — a stray
    `prd_id` on a manual/artifact create (which should never happen from
    the real client, but the field is present on the request model) must
    never trigger the dedup branch or affect creation."""
    ctx = company_client(monkeypatch)

    r = ctx.client.post(
        "/v1/projects",
        json={"name": "Manual with stray prd_id", "origin": "manual", "prd_id": 999},
    )
    assert r.status_code == 200
    assert r.json()["origin"] == "manual"


def test_create_prd_auto_ignores_manual_project_with_same_prd_artifact(
    isolated_settings, monkeypatch
):
    """A MANUAL project that happens to hold this PRD as an artifact must
    NOT be treated as an existing fork — only `origin='prd_auto'` projects
    dedupe against each other."""
    ctx = company_client(monkeypatch)

    from app.db import projects as projects_db

    manual = projects_db.create_project(
        company_id=ctx.company_id, workspace_id=_default_workspace_id(ctx.company_id),
        name="Manual project", created_by=ctx.user_id, origin="manual",
    )
    projects_db.add_artifact(manual["id"], "prd", 7777)

    r = ctx.client.post(
        "/v1/projects",
        json={"name": "Dark mode PRD", "origin": "prd_auto", "prd_id": 7777},
    )
    assert r.status_code == 200
    assert r.json()["id"] != manual["id"]
    assert r.json()["origin"] == "prd_auto"


def test_create_prd_auto_dedup_is_company_scoped(isolated_settings, monkeypatch):
    """A same-numbered `prd_id` forked in a DIFFERENT company must never
    dedupe across tenants — the lookup is company-scoped, same as every
    other cross-tenant existence check in this router."""
    ctx = company_client(monkeypatch)

    from app.db.client import require_client
    from app.db.workspaces import ensure_default_workspace

    foreign_ws = ensure_default_workspace("foreign-co")["id"]

    from app.project_from_prd import maybe_auto_create_project_for_prd

    prd_id = 606
    foreign_project_id = maybe_auto_create_project_for_prd(
        company_id="foreign-co",
        workspace_id=foreign_ws,
        user_id="someone-else",
        prd_id=prd_id,
        prd_title="Foreign PRD",
        conversation_id=(
            require_client()
            .table("conversations")
            .insert(
                {
                    "company_id": "foreign-co",
                    "user_id": "someone-else",
                    "title": "generate prd",
                    "query": "generate prd",
                    "agent_type": "ask",
                }
            )
            .execute()
            .data[0]["id"]
        ),
    )
    assert foreign_project_id is not None

    r = ctx.client.post(
        "/v1/projects",
        json={"name": "Same prd_id, different tenant", "origin": "prd_auto", "prd_id": prd_id},
    )
    assert r.status_code == 200
    assert r.json()["id"] != foreign_project_id
    assert r.json()["company_id"] == ctx.company_id


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
    from app.db.workspaces import upsert_workspace_member

    # POST-IDOR-FIX (AD-TNM1): the add_member route now resolves the email
    # through `resolve_candidate`, which only accepts an IN-TENANT existing
    # user. The invitee must therefore be a real member of the caller's
    # company + this project's workspace — a bare cross-tenant profile (what
    # this test used to seed) now correctly 404s (see
    # test_add_member_route_rejects_cross_company in test_tag_candidate_api.py).
    require_client().table("profiles").insert(
        {"id": "invitee-1", "email": "invitee@co.com"}
    ).execute()
    require_client().table("company_members").insert(
        {"id": "cm-invitee-1", "company_id": ctx.company_id, "user_id": "invitee-1", "role": "member"}
    ).execute()
    upsert_workspace_member(project["workspace_id"], "invitee-1", "member")

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


# ── Save a chat output as a project artifact (item-14 substrate) ──────────
# POST /v1/projects/{id}/artifacts/from-chat — mirrors the fixture style
# above: `_seed_foreign_project`/`seed_same_tenant_non_member` prove the
# AD-P11 membership gate; the fake `reports` table (conftest schema) proves
# the write.


def _report_rows(company_id: str) -> list[dict]:
    from app.db.client import require_client

    return (
        require_client()
        .table("reports")
        .select("*")
        .eq("company_id", company_id)
        .execute()
        .data
    )


def _artifact_refs(project_id: int) -> list[dict]:
    from app.db.client import require_client

    return (
        require_client()
        .table("project_artifacts")
        .select("*")
        .eq("project_id", project_id)
        .execute()
        .data
    )


def test_from_chat_creates_report_and_ref(isolated_settings, monkeypatch):
    ctx = company_client(monkeypatch)
    project = ctx.client.post("/v1/projects", json={"name": "Saved-chat project"}).json()

    r = ctx.client.post(
        f"/v1/projects/{project['id']}/artifacts/from-chat",
        json={"content": "## Prioritization\n\n- Ship A first"},
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["artifact_type"] == "report"
    assert body["project_id"] == project["id"]
    report_id = body["artifact_id"]

    ws_id = _default_workspace_id(ctx.company_id)
    reports = _report_rows(ctx.company_id)
    assert len(reports) == 1
    assert reports[0]["id"] == report_id
    assert reports[0]["skill"] == "saved-chat"
    assert reports[0]["company_id"] == ctx.company_id
    assert reports[0]["workspace_id"] == ws_id
    assert reports[0]["ask_id"] is None
    # Stored verbatim — raw markdown, not an HTML document (rendered by the
    # frontend's SavedChatMarkdown, routed on skill=="saved-chat").
    assert reports[0]["html"] == "## Prioritization\n\n- Ship A first"

    refs = _artifact_refs(project["id"])
    assert [(ref["artifact_type"], ref["artifact_id"]) for ref in refs] == [("report", report_id)]


# `list_artifacts_for_company` (the reader `GET .../artifacts` reuses)
# unconditionally selects from `prototypes` too — a table intentionally NOT
# in the shared base fake schema (see test_project_artifacts_fanout.py's
# docstring). Only the ONE test below reads that endpoint, so it gets its
# own minimal copy of the same DDL rather than pulling in the sibling
# fixture module.
_PROTOTYPE_DDL = """
CREATE TABLE IF NOT EXISTS prototypes (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    prd_id       INTEGER,
    workspace_id TEXT NOT NULL,
    status       TEXT NOT NULL DEFAULT 'generating',
    variant      TEXT NOT NULL DEFAULT 'v1'
);
"""


def test_saved_report_appears_in_project_artifacts(isolated_settings, monkeypatch):
    from tests import _fake_supabase

    _fake_supabase.get_fake_db().executescript(_PROTOTYPE_DDL)

    ctx = company_client(monkeypatch)
    project = ctx.client.post("/v1/projects", json={"name": "Listed project"}).json()

    ctx.client.post(
        f"/v1/projects/{project['id']}/artifacts/from-chat",
        json={"content": "First saved chat output"},
    )

    r = ctx.client.get(f"/v1/projects/{project['id']}/artifacts")
    assert r.status_code == 200
    items = r.json()["artifacts"]
    reports = [item for item in items if item["type"] == "report"]
    assert len(reports) == 1
    assert reports[0]["title"] == "First saved chat output"
    assert reports[0]["skill"] == "saved-chat"


def test_two_saves_distinct_reports(isolated_settings, monkeypatch):
    ctx = company_client(monkeypatch)
    project = ctx.client.post("/v1/projects", json={"name": "Two saves"}).json()

    r1 = ctx.client.post(
        f"/v1/projects/{project['id']}/artifacts/from-chat", json={"content": "First output"}
    )
    r2 = ctx.client.post(
        f"/v1/projects/{project['id']}/artifacts/from-chat", json={"content": "Second output"}
    )
    assert r1.status_code == 200 and r2.status_code == 200
    assert r1.json()["artifact_id"] != r2.json()["artifact_id"]

    refs = _artifact_refs(project["id"])
    assert len(refs) == 2
    assert len(_report_rows(ctx.company_id)) == 2


def test_from_chat_non_member_403(isolated_settings, monkeypatch):
    ctx = company_client(monkeypatch)
    project = ctx.client.post("/v1/projects", json={"name": "Gated save"}).json()
    _, non_member_headers = seed_same_tenant_non_member(ctx)

    r = ctx.client.post(
        f"/v1/projects/{project['id']}/artifacts/from-chat",
        json={"content": "Body"},
        headers=non_member_headers,
    )
    assert r.status_code == 403
    assert _report_rows(ctx.company_id) == []
    assert _artifact_refs(project["id"]) == []


def test_from_chat_foreign_tenant_404(isolated_settings, monkeypatch):
    ctx = company_client(monkeypatch)
    foreign = _seed_foreign_project()

    r = ctx.client.post(
        f"/v1/projects/{foreign['id']}/artifacts/from-chat", json={"content": "Body"}
    )
    assert r.status_code == 404
    assert _report_rows(ctx.company_id) == []
    assert _artifact_refs(foreign["id"]) == []


def test_from_chat_empty_content_400(isolated_settings, monkeypatch):
    ctx = company_client(monkeypatch)
    project = ctx.client.post("/v1/projects", json={"name": "Empty save"}).json()

    r = ctx.client.post(
        f"/v1/projects/{project['id']}/artifacts/from-chat", json={"content": "   \n  "}
    )
    assert r.status_code == 400
    assert _report_rows(ctx.company_id) == []
    assert _artifact_refs(project["id"]) == []


def test_from_chat_save_returns_none_502(isolated_settings, monkeypatch):
    ctx = company_client(monkeypatch)
    project = ctx.client.post("/v1/projects", json={"name": "502 save"}).json()

    import app.db as db

    monkeypatch.setattr(db, "save_report", lambda *a, **k: None)

    r = ctx.client.post(
        f"/v1/projects/{project['id']}/artifacts/from-chat", json={"content": "Body"}
    )
    assert r.status_code == 502
    assert _artifact_refs(project["id"]) == []


def test_from_chat_db_error_propagates_500(isolated_settings, monkeypatch):
    ctx = company_client(monkeypatch)
    project = ctx.client.post("/v1/projects", json={"name": "500 save"}).json()

    import app.db as db

    def boom(*a, **k):
        raise RuntimeError("db down")

    monkeypatch.setattr(db, "save_report", boom)

    import app.main as main_mod
    from fastapi.testclient import TestClient

    raising_client = TestClient(main_mod.app, headers=ctx.headers, raise_server_exceptions=False)
    r = raising_client.post(
        f"/v1/projects/{project['id']}/artifacts/from-chat", json={"content": "Body"}
    )
    assert r.status_code == 500
    assert _artifact_refs(project["id"]) == []


def test_from_chat_client_cannot_supply_report_id(isolated_settings, monkeypatch):
    ctx = company_client(monkeypatch)
    project = ctx.client.post("/v1/projects", json={"name": "No client id"}).json()

    r = ctx.client.post(
        f"/v1/projects/{project['id']}/artifacts/from-chat",
        json={"content": "Body", "artifact_id": 999999, "report_id": 999999},
    )
    assert r.status_code == 200, r.text
    body = r.json()
    # The extra fields are silently ignored (Pydantic drops unknown fields by
    # default) — the artifact id comes only from the fresh save, never 999999.
    assert body["artifact_id"] != 999999
    reports = _report_rows(ctx.company_id)
    assert len(reports) == 1
    assert reports[0]["id"] == body["artifact_id"]


def test_from_chat_emits_saved_log_line(isolated_settings, monkeypatch, caplog):
    ctx = company_client(monkeypatch)
    project = ctx.client.post("/v1/projects", json={"name": "Logged save"}).json()

    with caplog.at_level(logging.INFO, logger="app.routes.projects"):
        r = ctx.client.post(
            f"/v1/projects/{project['id']}/artifacts/from-chat",
            json={"content": "Some secret-looking content that must not leak"},
        )
    assert r.status_code == 200
    report_id = r.json()["artifact_id"]

    lines = [rec.getMessage() for rec in caplog.records]
    expected = f"project_chat_artifact_saved project_id={project['id']} report_id={report_id}"
    assert any(line == expected for line in lines)
    # No content, PII or secret in any log line this call produced.
    assert not any("secret-looking" in line for line in lines)
