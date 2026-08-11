"""Tests for `db/project_memory_entries.py` + the `/v1/projects/{id}/memory*`
routes: entry CRUD, provenance, stale-flipping, tenant/project isolation,
and the read-only cached summary (never an LLM call, AD-P7/§5.4).
"""
from __future__ import annotations

import logging

from tests._company_helpers import company_client
from tests._project_helpers import seed_same_tenant_non_member


def _create_project(ctx, *, name: str = "Memory project") -> dict:
    return ctx.client.post("/v1/projects", json={"name": name}).json()


# ── Memory CRUD ──────────────────────────────────────────────────────────


def test_add_user_memory_entry_provenance(isolated_settings, monkeypatch):
    ctx = company_client(monkeypatch)
    project = _create_project(ctx)

    r = ctx.client.post(
        f"/v1/projects/{project['id']}/memory", json={"body": "Ship by Friday."}
    )
    assert r.status_code == 200
    entry = r.json()
    assert entry["project_id"] == project["id"]
    assert entry["body"] == "Ship by Friday."
    assert entry["author_user_id"] == ctx.user_id
    assert entry["promoted_by"] is None


def test_list_memory_entries(isolated_settings, monkeypatch):
    ctx = company_client(monkeypatch)
    project = _create_project(ctx)
    ctx.client.post(f"/v1/projects/{project['id']}/memory", json={"body": "First"})
    ctx.client.post(f"/v1/projects/{project['id']}/memory", json={"body": "Second"})

    r = ctx.client.get(f"/v1/projects/{project['id']}/memory")
    assert r.status_code == 200
    bodies = {e["body"] for e in r.json()["entries"]}
    assert bodies == {"First", "Second"}


def test_edit_memory_entry_updates_body(isolated_settings, monkeypatch):
    ctx = company_client(monkeypatch)
    project = _create_project(ctx)
    entry = ctx.client.post(
        f"/v1/projects/{project['id']}/memory", json={"body": "Original"}
    ).json()

    r = ctx.client.patch(
        f"/v1/projects/{project['id']}/memory/{entry['id']}", json={"body": "Edited"}
    )
    assert r.status_code == 200
    assert r.json()["body"] == "Edited"


def test_delete_memory_entry_removes_row(isolated_settings, monkeypatch):
    ctx = company_client(monkeypatch)
    project = _create_project(ctx)
    entry = ctx.client.post(
        f"/v1/projects/{project['id']}/memory", json={"body": "Gone soon"}
    ).json()

    r = ctx.client.delete(f"/v1/projects/{project['id']}/memory/{entry['id']}")
    assert r.status_code == 200
    assert r.json()["deleted"] is True

    from app.db.client import require_client

    rows = (
        require_client()
        .table("project_memory_entries")
        .select("id")
        .eq("id", entry["id"])
        .execute()
        .data
    )
    assert rows == []


# ── Isolation (mutation-proofed: an entry from another project must never
# be reachable through a different project's id) ────────────────────────


def test_memory_patch_foreign_entry_404(isolated_settings, monkeypatch):
    ctx = company_client(monkeypatch)
    project_a = _create_project(ctx, name="Project A")
    project_b = _create_project(ctx, name="Project B")
    entry = ctx.client.post(
        f"/v1/projects/{project_a['id']}/memory", json={"body": "Belongs to A"}
    ).json()

    r = ctx.client.patch(
        f"/v1/projects/{project_b['id']}/memory/{entry['id']}", json={"body": "Hijacked"}
    )
    assert r.status_code == 404

    from app.db.client import require_client

    row = (
        require_client()
        .table("project_memory_entries")
        .select("body")
        .eq("id", entry["id"])
        .execute()
        .data[0]
    )
    assert row["body"] == "Belongs to A"


def test_memory_delete_foreign_entry_404(isolated_settings, monkeypatch):
    ctx = company_client(monkeypatch)
    project_a = _create_project(ctx, name="Project A")
    project_b = _create_project(ctx, name="Project B")
    entry = ctx.client.post(
        f"/v1/projects/{project_a['id']}/memory", json={"body": "Belongs to A"}
    ).json()

    r = ctx.client.delete(f"/v1/projects/{project_b['id']}/memory/{entry['id']}")
    assert r.status_code == 404

    from app.db.client import require_client

    rows = (
        require_client()
        .table("project_memory_entries")
        .select("id")
        .eq("id", entry["id"])
        .execute()
        .data
    )
    assert len(rows) == 1


# ── Stale-flipping ───────────────────────────────────────────────────────


def test_memory_mutation_flips_summary_stale(isolated_settings, monkeypatch):
    """`db/project_memory_entries.py`'s add/update/delete each flip an
    EXISTING summary row's `stale` flag — exercised at the DB-helper level
    directly, not the HTTP route. Since the memory-synthesis ticket, the
    route layer ALSO fires a synthesis regen after each mutation
    (`app.project_memory.schedule_regen`), which — inline under pytest —
    would immediately clear `stale` again within the same request and mask
    this specific `_flip_summary_stale` assertion. `db/project_memory_entries.py`
    itself is unchanged by that ticket, so this stays the right layer to
    prove its stale-flipping contract in isolation. The route-level
    add/edit/delete → regen → stale-clears behavior is covered by
    `test_project_memory.py::test_add_memory_triggers_regen` /
    `test_edit_delete_memory_triggers_regen`."""
    ctx = company_client(monkeypatch)
    project = _create_project(ctx)

    from app.db import project_memory_entries as memory_db_mod
    from app.db.client import require_client

    require_client().table("project_memory_summary").insert(
        {
            "project_id": project["id"],
            "summary_md": "Stale-checking summary.",
            "entry_count": 0,
            "stale": False,
        }
    ).execute()

    entry = memory_db_mod.add_entry(project["id"], body="New insight", author_user_id=ctx.user_id)

    def _summary_stale() -> bool:
        row = (
            require_client()
            .table("project_memory_summary")
            .select("stale")
            .eq("project_id", project["id"])
            .execute()
            .data[0]
        )
        return bool(row["stale"])

    assert _summary_stale() is True

    # Clear it, then prove edit + delete each flip it again independently.
    require_client().table("project_memory_summary").update({"stale": False}).eq(
        "project_id", project["id"]
    ).execute()
    memory_db_mod.update_entry(project["id"], entry["id"], body="Edited insight")
    assert _summary_stale() is True

    require_client().table("project_memory_summary").update({"stale": False}).eq(
        "project_id", project["id"]
    ).execute()
    memory_db_mod.delete_entry(project["id"], entry["id"])
    assert _summary_stale() is True


def test_memory_mutation_without_summary_row_is_a_noop(isolated_settings, monkeypatch):
    """No `project_memory_summary` row exists yet — a bare `_flip_summary_stale`
    call must not error trying to flip a flag on a row that isn't there.
    Exercises `db/project_memory_entries.py::add_entry` directly (not the
    HTTP route) so this stays a DB-helper-level assertion: since the memory-
    synthesis ticket, POSTing via the route ALSO triggers a synthesis regen
    that legitimately DOES create a summary row when none existed — that new
    behavior is covered by `test_project_memory.py::test_add_memory_triggers_regen`."""
    ctx = company_client(monkeypatch)
    project = _create_project(ctx)

    from app.db import project_memory_entries as memory_db_mod

    entry = memory_db_mod.add_entry(
        project["id"], body="First ever insight", author_user_id=ctx.user_id
    )
    assert entry["body"] == "First ever insight"

    from app.db.client import require_client

    rows = (
        require_client()
        .table("project_memory_summary")
        .select("*")
        .eq("project_id", project["id"])
        .execute()
        .data
    )
    assert rows == []


# ── Membership gate (AD-P11): a SAME-TENANT caller who is not a project
# member must be blocked from every memory route — this is the exact gap
# a fully-foreign-tenant probe cannot catch (that only proves 404). ─────


def test_memory_routes_same_tenant_non_member_403(isolated_settings, monkeypatch):
    ctx = company_client(monkeypatch)
    project = _create_project(ctx)
    owned_entry = ctx.client.post(
        f"/v1/projects/{project['id']}/memory", json={"body": "Owner's guardrail"}
    ).json()
    _, non_member_headers = seed_same_tenant_non_member(ctx)

    r_list = ctx.client.get(f"/v1/projects/{project['id']}/memory", headers=non_member_headers)
    assert r_list.status_code == 403

    r_add = ctx.client.post(
        f"/v1/projects/{project['id']}/memory",
        json={"body": "Injected by a non-member"},
        headers=non_member_headers,
    )
    assert r_add.status_code == 403

    r_summary = ctx.client.get(
        f"/v1/projects/{project['id']}/memory/summary", headers=non_member_headers
    )
    assert r_summary.status_code == 403

    r_edit = ctx.client.patch(
        f"/v1/projects/{project['id']}/memory/{owned_entry['id']}",
        json={"body": "Hijacked by a non-member"},
        headers=non_member_headers,
    )
    assert r_edit.status_code == 403

    r_delete = ctx.client.delete(
        f"/v1/projects/{project['id']}/memory/{owned_entry['id']}",
        headers=non_member_headers,
    )
    assert r_delete.status_code == 403

    # None of the blocked attempts touched anything: the owner's entry is
    # untouched, and no injected entry exists.
    from app.db.client import require_client

    rows = (
        require_client()
        .table("project_memory_entries")
        .select("id, body")
        .eq("project_id", project["id"])
        .execute()
        .data
    )
    assert [(r["id"], r["body"]) for r in rows] == [(owned_entry["id"], "Owner's guardrail")]


def test_get_project_detail_same_tenant_non_member_403_via_memory_fixture(
    isolated_settings, monkeypatch
):
    """Cross-check against `test_projects_routes.py`'s equivalent using a
    project seeded with a real memory entry — the gate applies identically
    whether or not the project has memory yet."""
    ctx = company_client(monkeypatch)
    project = _create_project(ctx)
    _, non_member_headers = seed_same_tenant_non_member(ctx)

    r = ctx.client.get(f"/v1/projects/{project['id']}", headers=non_member_headers)
    assert r.status_code == 403


# ── Summary read (never an LLM call — AD-P7) ────────────────────────────


def test_get_summary_serves_cached_row_no_llm(fake_llm, monkeypatch):
    ctx = company_client(monkeypatch)
    project = _create_project(ctx)

    from app.db.client import require_client

    require_client().table("project_memory_summary").insert(
        {
            "project_id": project["id"],
            "summary_md": "What this project knows so far.",
            "entry_count": 3,
            "stale": False,
        }
    ).execute()

    r = ctx.client.get(f"/v1/projects/{project['id']}/memory/summary")
    assert r.status_code == 200
    body = r.json()
    assert body["summary_md"] == "What this project knows so far."
    assert body["entry_count"] == 3
    assert body["stale"] is False
    assert fake_llm["calls"] == []


def test_get_summary_fallback_when_absent(fake_llm, monkeypatch):
    """GET .../memory/summary is read-only and never calls an LLM itself —
    proven by adding entries directly via `db/project_memory_entries.py`
    (not the HTTP route), so no synthesis regen has run and the fallback
    branch is genuinely reached. (Since the memory-synthesis ticket, POSTing
    via the route WOULD trigger a regen — a different, correctly-tested
    path; see `test_project_memory.py`. This test's job is narrowly the read
    endpoint's own no-LLM-call guarantee, unchanged by that ticket.)"""
    ctx = company_client(monkeypatch)
    project = _create_project(ctx)

    from app.db import project_memory_entries as memory_db_mod

    memory_db_mod.add_entry(project["id"], body="One", author_user_id=ctx.user_id)
    memory_db_mod.add_entry(project["id"], body="Two", author_user_id=ctx.user_id)

    r = ctx.client.get(f"/v1/projects/{project['id']}/memory/summary")
    assert r.status_code == 200
    body = r.json()
    assert body["summary_md"] is None
    assert body["entry_count"] == 2
    assert fake_llm["calls"] == []


# ── Observability — no memory body text ever reaches a log line ─────────


def test_memory_entry_added_log_has_no_body_text(isolated_settings, monkeypatch, caplog):
    ctx = company_client(monkeypatch)
    project = _create_project(ctx)

    with caplog.at_level(logging.INFO, logger="app.routes.projects"):
        r = ctx.client.post(
            f"/v1/projects/{project['id']}/memory",
            json={"body": "SECRET_GUARDRAIL_DO_NOT_LOG"},
        )
    assert r.status_code == 200
    entry_id = r.json()["id"]

    joined = "\n".join(rec.getMessage() for rec in caplog.records)
    assert "SECRET_GUARDRAIL_DO_NOT_LOG" not in joined
    assert f"memory_entry_added project_id={project['id']} entry_id={entry_id}" in joined
