"""Tests for the auto-create-from-PRD hook (`app/project_from_prd.py`,
AD-P9) + its wiring into `routes/prd.py`'s three conversation-binding call
sites (`generate_from_task`'s existing-PRD and new-PRD branches, `import_prd`).

Fast (fake-DB) tests exercise the helper's logic directly, mirroring the
existing `test_prd_conversation_binding.py` fixture style (`tenant_client` +
the in-memory fake Supabase from `isolated_settings`). The bottom section
runs the same "Creation" assertions against a real local Supabase
(`RUN_PROJECT_FROM_PRD_LIVE=1`, `[[reference_local-supabase-real-db-verification]]`,
same skip-cleanly posture as `test_projects_crud_live.py`) — kept in this
one ticket-scoped module rather than a separate `_live.py` file so both
evidence tiers live together.
"""
from __future__ import annotations

import os
import uuid

import pytest

from app.db.client import require_client


def _save_current_brief(db_mod, dataset):
    payload = {
        "summary_headline": "stub",
        "insights": [{"title": "Brief insight 0", "theme_id": "brief-theme"}],
        "_schema_version": 1,
    }
    return db_mod.save_brief(
        dataset=dataset, week_label="Week of stub", payload=payload, schema_version=1
    )


def _seed_brief_and_prd(db_mod, dataset: str, title: str = "Dark mode PRD") -> int:
    _save_current_brief(db_mod, dataset)
    resp = require_client().table("briefs").select("id").eq("dataset", dataset).eq(
        "is_current", True
    ).execute()
    brief_id = resp.data[0]["id"]
    prd_resp = require_client().table("prds").insert(
        {"brief_id": brief_id, "insight_index": 0, "title": title, "status": "ready"}
    ).execute()
    return prd_resp.data[0]["id"]


def _new_conversation(company_id: str, user_id: str) -> int:
    row = {
        "company_id": company_id,
        "user_id": user_id,
        "title": "generate prd",
        "query": "generate prd",
        "agent_type": "ask",
    }
    resp = require_client().table("conversations").insert(row).execute()
    return resp.data[0]["id"]


def _conversation(conv_id: int) -> dict:
    return require_client().table("conversations").select("*").eq("id", conv_id).execute().data[0]


# ── Creation (AC1/AC7) ────────────────────────────────────────────────────


def test_auto_create_makes_prd_auto_project_with_artifact(tenant_client, isolated_settings):
    t = tenant_client.make(slug="acme")
    prd_id = _seed_brief_and_prd(isolated_settings["db"], "acme", title="Dark mode on mobile")
    conv_id = _new_conversation(t.company_id, t.user_id)

    from app.project_from_prd import maybe_auto_create_project_for_prd

    project_id = maybe_auto_create_project_for_prd(
        company_id=t.company_id, workspace_id="ws-1", user_id=t.user_id,
        prd_id=prd_id, prd_title="Dark mode on mobile", conversation_id=conv_id,
    )
    assert project_id is not None

    client = require_client()
    projects = client.table("projects").select("*").eq("id", project_id).execute().data
    assert len(projects) == 1
    assert projects[0]["origin"] == "prd_auto"

    members = client.table("project_members").select("*").eq("project_id", project_id).execute().data
    assert [m["user_id"] for m in members] == [t.user_id]

    artifacts = client.table("project_artifacts").select("*").eq("project_id", project_id).execute().data
    assert len(artifacts) == 1
    assert artifacts[0]["artifact_type"] == "prd"
    assert artifacts[0]["artifact_id"] == prd_id

    assert _conversation(conv_id)["project_id"] == project_id


def test_auto_create_names_from_prd_title(tenant_client, isolated_settings):
    t = tenant_client.make(slug="acme")
    prd_id = _seed_brief_and_prd(isolated_settings["db"], "acme", title="Instant-quote flow — v3")
    conv_id = _new_conversation(t.company_id, t.user_id)

    from app.project_from_prd import maybe_auto_create_project_for_prd

    project_id = maybe_auto_create_project_for_prd(
        company_id=t.company_id, workspace_id="ws-1", user_id=t.user_id,
        prd_id=prd_id, prd_title="Instant-quote flow — v3", conversation_id=conv_id,
    )
    project = require_client().table("projects").select("name").eq("id", project_id).execute().data[0]
    assert project["name"] == "Instant-quote flow — v3"


# ── Idempotence / edge (AC2/AC4) ────────────────────────────────────────────


def test_auto_create_first_write_wins(tenant_client, isolated_settings):
    t = tenant_client.make(slug="acme")
    db_mod = isolated_settings["db"]
    prd_id = _seed_brief_and_prd(db_mod, "acme", title="Dark mode")
    conv_id = _new_conversation(t.company_id, t.user_id)

    from app.project_from_prd import maybe_auto_create_project_for_prd

    first = maybe_auto_create_project_for_prd(
        company_id=t.company_id, workspace_id="ws-1", user_id=t.user_id,
        prd_id=prd_id, prd_title="Dark mode", conversation_id=conv_id,
    )
    assert first is not None

    second_prd_id = _seed_brief_and_prd(db_mod, "acme", title="Something else entirely")
    second = maybe_auto_create_project_for_prd(
        company_id=t.company_id, workspace_id="ws-1", user_id=t.user_id,
        prd_id=second_prd_id, prd_title="Something else entirely", conversation_id=conv_id,
    )
    assert second == first

    projects = (
        require_client().table("projects").select("id").eq("company_id", t.company_id).execute().data
    )
    assert len(projects) == 1

    # The second PRD was never attached to the (unchanged) project.
    artifacts = (
        require_client()
        .table("project_artifacts")
        .select("artifact_id")
        .eq("project_id", first)
        .execute()
        .data
    )
    assert [a["artifact_id"] for a in artifacts] == [prd_id]


def test_auto_create_none_conversation_skips(tenant_client, isolated_settings):
    t = tenant_client.make(slug="acme")
    prd_id = _seed_brief_and_prd(isolated_settings["db"], "acme")

    from app.project_from_prd import maybe_auto_create_project_for_prd

    result = maybe_auto_create_project_for_prd(
        company_id=t.company_id, workspace_id="ws-1", user_id=t.user_id,
        prd_id=prd_id, prd_title="Dark mode PRD", conversation_id=None,
    )
    assert result is None

    projects = (
        require_client().table("projects").select("id").eq("company_id", t.company_id).execute().data
    )
    assert projects == []


def test_auto_create_none_conversation_still_skips_with_flag_explicitly_false(tenant_client, isolated_settings):
    """Same as above, but pins the default explicitly — `allow_without_
    conversation=False` is byte-for-byte the original (pre-this-ticket)
    guard, so the chat path's behavior is provably unchanged."""
    t = tenant_client.make(slug="acme")
    prd_id = _seed_brief_and_prd(isolated_settings["db"], "acme")

    from app.project_from_prd import maybe_auto_create_project_for_prd

    result = maybe_auto_create_project_for_prd(
        company_id=t.company_id, workspace_id="ws-1", user_id=t.user_id,
        prd_id=prd_id, prd_title="Dark mode PRD", conversation_id=None,
        allow_without_conversation=False,
    )
    assert result is None

    projects = (
        require_client().table("projects").select("id").eq("company_id", t.company_id).execute().data
    )
    assert projects == []


# ── prd-auto project for briefs/ideation (conversation-less fork) ──────────
# The weekly-brief `/generate` and ideation `/generate-from-ideation` paths
# have no chat thread to fork from, so `allow_without_conversation=True`
# dedups on the PRD-artifact fact (`find_existing_prd_auto_project`) instead
# of a conversation binding, and skips conversation-bind + origin-memory
# seeding entirely (nothing to seed from).


def test_allow_without_conversation_creates_prd_auto_project(tenant_client, isolated_settings):
    t = tenant_client.make(slug="acme")
    prd_id = _seed_brief_and_prd(isolated_settings["db"], "acme", title="Weekly-brief PRD")

    from app.project_from_prd import maybe_auto_create_project_for_prd

    project_id = maybe_auto_create_project_for_prd(
        company_id=t.company_id, workspace_id="ws-1", user_id=t.user_id,
        prd_id=prd_id, prd_title="Weekly-brief PRD", conversation_id=None,
        allow_without_conversation=True,
    )
    assert project_id is not None

    client = require_client()
    project = client.table("projects").select("*").eq("id", project_id).execute().data[0]
    assert project["origin"] == "prd_auto"
    assert project["name"] == "Weekly-brief PRD"

    artifacts = client.table("project_artifacts").select("*").eq("project_id", project_id).execute().data
    assert len(artifacts) == 1
    assert artifacts[0]["artifact_type"] == "prd"
    assert artifacts[0]["artifact_id"] == prd_id

    # No conversation to bind — the member roster still seeds the creator
    # (create_project's own contract), but no `conversations` row is touched.
    members = client.table("project_members").select("*").eq("project_id", project_id).execute().data
    assert [m["user_id"] for m in members] == [t.user_id]


def test_allow_without_conversation_dedups_same_prd_to_same_project(tenant_client, isolated_settings):
    """A second conversation-less generate of the SAME PRD reuses the SAME
    project instead of spawning a duplicate (dedup on the PRD-artifact
    fact, since there is no conversation to key on)."""
    t = tenant_client.make(slug="acme")
    prd_id = _seed_brief_and_prd(isolated_settings["db"], "acme", title="Weekly-brief PRD")

    from app.project_from_prd import maybe_auto_create_project_for_prd

    first = maybe_auto_create_project_for_prd(
        company_id=t.company_id, workspace_id="ws-1", user_id=t.user_id,
        prd_id=prd_id, prd_title="Weekly-brief PRD", conversation_id=None,
        allow_without_conversation=True,
    )
    second = maybe_auto_create_project_for_prd(
        company_id=t.company_id, workspace_id="ws-1", user_id=t.user_id,
        prd_id=prd_id, prd_title="Weekly-brief PRD", conversation_id=None,
        allow_without_conversation=True,
    )
    assert second == first

    projects = (
        require_client().table("projects").select("id").eq("company_id", t.company_id).execute().data
    )
    assert len(projects) == 1

    artifacts = (
        require_client()
        .table("project_artifacts")
        .select("artifact_id")
        .eq("project_id", first)
        .execute()
        .data
    )
    assert len(artifacts) == 1  # not re-attached on the dedup hit


def test_generate_route_returns_project_id_and_attaches_prd(tenant_client, isolated_settings):
    """Route-level (AC2): `/v1/prd/generate`'s FRESH-create branch returns a
    `project_id` and the resulting project holds the generated PRD."""
    t = tenant_client.make(slug="acme")
    brief_id = _save_current_brief(isolated_settings["db"], dataset="acme")

    resp = t.client.post("/v1/prd/generate", json={"brief_id": brief_id, "insight_index": 0})
    assert resp.status_code == 200
    body = resp.json()
    assert body["project_id"] is not None

    artifacts = (
        require_client()
        .table("project_artifacts")
        .select("artifact_id, artifact_type")
        .eq("project_id", body["project_id"])
        .execute()
        .data
    )
    assert [(a["artifact_id"], a["artifact_type"]) for a in artifacts] == [(body["prd_id"], "prd")]


def test_generate_route_existing_prd_branch_returns_project_id(tenant_client, isolated_settings):
    """Route-level (AC2): the EXISTING-PRD early-return branch of `/generate`
    also forks/reuses a project — re-issuing the same generate is not force,
    so it hits `find_existing_prd`'s early return, which must still resolve
    (and dedupe against) the conversation-less project."""
    t = tenant_client.make(slug="acme")
    brief_id = _save_current_brief(isolated_settings["db"], dataset="acme")

    first = t.client.post("/v1/prd/generate", json={"brief_id": brief_id, "insight_index": 0}).json()
    second = t.client.post("/v1/prd/generate", json={"brief_id": brief_id, "insight_index": 0}).json()

    assert second["prd_id"] == first["prd_id"]
    assert second["project_id"] == first["project_id"]

    projects = (
        require_client().table("projects").select("id").eq("company_id", t.company_id).execute().data
    )
    assert len(projects) == 1


def test_generate_from_ideation_route_returns_project_id_and_attaches_prd(tenant_client, isolated_settings):
    """Route-level (AC2): `/v1/prd/generate-from-ideation`'s fresh-create
    branch also returns a `project_id` with the PRD attached."""
    from app.db import ideation as ideation_db

    t = tenant_client.make(slug="acme")
    _save_current_brief(isolated_settings["db"], dataset="acme")
    ideation_db.upsert_ideation_item(
        t.company_id, theme_id="theme-x", title="Bulk onboarding",
        rank=4, score=9.0, shortlisted=True, reasoning="Churn evidence.",
    )
    item = next(i for i in ideation_db.list_ideation_items(t.company_id) if i["theme_id"] == "theme-x")

    resp = t.client.post("/v1/prd/generate-from-ideation", json={"ideation_item_id": item["id"]})
    assert resp.status_code == 200
    body = resp.json()
    assert body["project_id"] is not None

    artifacts = (
        require_client()
        .table("project_artifacts")
        .select("artifact_id, artifact_type")
        .eq("project_id", body["project_id"])
        .execute()
        .data
    )
    assert [(a["artifact_id"], a["artifact_type"]) for a in artifacts] == [(body["prd_id"], "prd")]


# ── Error handling (mutation-proofed, AC3) ─────────────────────────────────


def test_auto_create_swallows_failure(tenant_client, isolated_settings, monkeypatch):
    """A forced failure inside the hook must never propagate into the caller
    (the generate-from-task route) — the PRD response is unaffected, the
    conversation<->PRD bind (which runs BEFORE the hook) still lands, and no
    partial project is left behind."""
    t = tenant_client.make(slug="acme")
    _save_current_brief(isolated_settings["db"], dataset="acme")
    conv_id = _new_conversation(t.company_id, t.user_id)

    import app.project_from_prd as pfp

    def _boom(*_a, **_k):
        raise RuntimeError("forced failure")

    monkeypatch.setattr(pfp, "create_project", _boom)

    resp = t.client.post(
        "/v1/prd/generate-from-task",
        json={"task": "dark mode on mobile", "conversation_id": conv_id},
    )
    assert resp.status_code == 200
    prd_id = resp.json()["prd_id"]

    conv = _conversation(conv_id)
    assert conv["prd_id"] == prd_id  # bind_conversation_to_prd still ran
    assert conv["project_id"] is None  # the hook's own failure was swallowed

    projects = (
        require_client().table("projects").select("id").eq("company_id", t.company_id).execute().data
    )
    assert projects == []


def test_auto_create_swallows_failure_direct_call_returns_none(tenant_client, isolated_settings, monkeypatch):
    """Same forced failure, exercised directly against the helper (not just
    through the route) — pins the no-raise contract at the unit level too."""
    t = tenant_client.make(slug="acme")
    prd_id = _seed_brief_and_prd(isolated_settings["db"], "acme")
    conv_id = _new_conversation(t.company_id, t.user_id)

    import app.project_from_prd as pfp

    monkeypatch.setattr(pfp, "add_artifact", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("boom")))

    result = pfp.maybe_auto_create_project_for_prd(
        company_id=t.company_id, workspace_id="ws-1", user_id=t.user_id,
        prd_id=prd_id, prd_title="Dark mode PRD", conversation_id=conv_id,
    )
    assert result is None


# ── Non-breakage (AC5/AC6) ──────────────────────────────────────────────────


def test_prd_generate_unbroken(tenant_client, isolated_settings, repo_root):
    """AC6 — the hook wiring didn't touch `bind_conversation_to_prd`'s call
    sites (still exactly 3, the conversation-bound `generate-from-task` /
    `import_prd` paths) and `generate`'s response shape is unchanged aside
    from the new `project_id` key.

    UPDATED invariant (prd-auto project for briefs/ideation): `generate`
    and `generate-from-ideation` now EACH carry two conversation-less
    `maybe_auto_create_project_for_prd(..., conversation_id=None,
    allow_without_conversation=True)` calls (the existing-PRD early-return
    branch + the fresh-create branch), on top of the 3 pre-existing
    conversation-bound call sites — 7 total, not 3. This assertion was
    bumped from the prior `== 3` closed-world count to reflect that real
    change (not narrowed to force green); `bind_conversation_to_prd`'s
    count is untouched by this ticket and stays pinned at 3."""
    import re

    prd_src = (repo_root / "app" / "routes" / "prd.py").read_text()
    assert len(re.findall(r"bind_conversation_to_prd\(", prd_src)) == 3
    assert len(re.findall(r"maybe_auto_create_project_for_prd\(", prd_src)) == 7

    t = tenant_client.make(slug="acme")
    brief_id = _save_current_brief(isolated_settings["db"], dataset="acme")
    resp = t.client.post("/v1/prd/generate", json={"brief_id": brief_id, "insight_index": 0})
    assert resp.status_code == 200
    body = resp.json()
    assert set(body.keys()) >= {"prd_id", "status", "project_id"}

    # `generate` carries no conversation_id — but it DOES now fork a
    # conversation-less `prd_auto` project (this ticket's change): exactly
    # one project, holding the generated PRD as its only artifact.
    assert body["project_id"] is not None
    projects = (
        require_client().table("projects").select("id, origin").eq("company_id", t.company_id).execute().data
    )
    assert len(projects) == 1
    assert projects[0]["origin"] == "prd_auto"
    assert projects[0]["id"] == body["project_id"]


def test_generate_from_task_new_prd_branch_creates_prd_auto_project(tenant_client, isolated_settings):
    """AC5 — the second (new-PRD) hook site in generate_from_task."""
    t = tenant_client.make(slug="acme")
    _save_current_brief(isolated_settings["db"], dataset="acme")
    conv_id = _new_conversation(t.company_id, t.user_id)

    resp = t.client.post(
        "/v1/prd/generate-from-task",
        json={"task": "dark mode on mobile", "conversation_id": conv_id},
    )
    assert resp.status_code == 200
    prd_id = resp.json()["prd_id"]

    project_row = _conversation(conv_id)
    assert project_row["project_id"] is not None
    project_id = project_row["project_id"]

    project = require_client().table("projects").select("*").eq("id", project_id).execute().data[0]
    assert project["origin"] == "prd_auto"

    artifacts = (
        require_client()
        .table("project_artifacts")
        .select("artifact_id, artifact_type")
        .eq("project_id", project_id)
        .execute()
        .data
    )
    assert [(a["artifact_id"], a["artifact_type"]) for a in artifacts] == [(prd_id, "prd")]

    # Reachable via GET /v1/projects for that member.
    listed = t.client.get("/v1/projects")
    assert project_id in [p["id"] for p in listed.json()["projects"]]


def test_import_prd_creates_prd_auto_project(tenant_client, isolated_settings):
    """AC5 — the third hook site, in import_prd."""
    import io

    t = tenant_client.make(slug="acme")
    conv_id = _new_conversation(t.company_id, t.user_id)

    resp = t.client.post(
        "/v1/prd/import",
        files={"file": ("deck.txt", io.BytesIO(b"# Requirements\n\nUsers want dark mode."), "text/plain")},
        data={"dataset": "acme", "conversation_id": str(conv_id)},
    )
    assert resp.status_code == 200
    prd_id = resp.json()["prd_id"]

    conv = _conversation(conv_id)
    assert conv["project_id"] is not None
    project = require_client().table("projects").select("*").eq("id", conv["project_id"]).execute().data[0]
    assert project["origin"] == "prd_auto"

    artifacts = (
        require_client()
        .table("project_artifacts")
        .select("artifact_id")
        .eq("project_id", conv["project_id"])
        .execute()
        .data
    )
    assert [a["artifact_id"] for a in artifacts] == [prd_id]


def test_generate_from_task_existing_prd_branch_creates_prd_auto_project(tenant_client, isolated_settings):
    """AC5 — the first hook site: re-issuing the same chat-task command
    resolves the SAME (existing) PRD, and the NEW conversation still forks
    its own project off that resolution branch."""
    t = tenant_client.make(slug="acme")
    _save_current_brief(isolated_settings["db"], dataset="acme")

    first_conv = _new_conversation(t.company_id, t.user_id)
    first = t.client.post(
        "/v1/prd/generate-from-task",
        json={"task": "dark mode on mobile", "conversation_id": first_conv},
    ).json()
    assert first["prd_id"]

    second_conv = _new_conversation(t.company_id, t.user_id)
    second = t.client.post(
        "/v1/prd/generate-from-task",
        json={"task": "dark mode on mobile", "conversation_id": second_conv},
    ).json()
    assert second["prd_id"] == first["prd_id"]

    second_project_id = _conversation(second_conv)["project_id"]
    assert second_project_id is not None
    project = (
        require_client().table("projects").select("*").eq("id", second_project_id).execute().data[0]
    )
    assert project["origin"] == "prd_auto"


# ── Reverse dedup lookup — find_existing_prd_auto_project ───────────────────
# Backs the create-modal's "Auto · from PRD" tab (POST /v1/projects,
# origin=prd_auto), which never resolves a conversation_id — only the PRD
# the user picked. Keyed on the `project_artifacts` ref (artifact_type=
# 'prd', artifact_id=prd_id) — the ONE fact both fork paths (this module's
# hook AND the create-modal's follow-up POST .../artifacts call) always
# write, scoped to origin='prd_auto' so a manual project with the same
# artifact is never matched.


def _fork_via_hook(company_id: str, user_id: str, workspace_id: str, prd_id: int, conv_id: int) -> int | None:
    from app.project_from_prd import maybe_auto_create_project_for_prd

    return maybe_auto_create_project_for_prd(
        company_id=company_id, workspace_id=workspace_id, user_id=user_id,
        prd_id=prd_id, prd_title="Dark mode PRD", conversation_id=conv_id,
    )


def test_find_existing_prd_auto_project_finds_the_hook_forked_project(tenant_client, isolated_settings):
    t = tenant_client.make(slug="acme")
    prd_id = _seed_brief_and_prd(isolated_settings["db"], "acme")
    conv_id = _new_conversation(t.company_id, t.user_id)

    project_id = _fork_via_hook(t.company_id, t.user_id, "ws-1", prd_id, conv_id)
    assert project_id is not None

    from app.project_from_prd import find_existing_prd_auto_project

    found = find_existing_prd_auto_project(prd_id, t.company_id)
    assert found == project_id


def test_find_existing_prd_auto_project_finds_the_modal_forked_project(tenant_client, isolated_settings):
    """The create-modal's own fork path (create, then a follow-up
    POST .../artifacts — no conversation ever bound) must dedupe too, not
    just the hook's."""
    t = tenant_client.make(slug="acme")
    prd_id = _seed_brief_and_prd(isolated_settings["db"], "acme")

    from app.db.projects import add_artifact, create_project
    from app.project_from_prd import find_existing_prd_auto_project

    project = create_project(
        company_id=t.company_id, workspace_id="ws-1", name="Dark mode PRD",
        created_by=t.user_id, origin="prd_auto",
    )
    add_artifact(project["id"], "prd", prd_id)

    assert find_existing_prd_auto_project(prd_id, t.company_id) == project["id"]


def test_find_existing_prd_auto_project_none_when_unbound(tenant_client, isolated_settings):
    t = tenant_client.make(slug="acme")
    prd_id = _seed_brief_and_prd(isolated_settings["db"], "acme")

    from app.project_from_prd import find_existing_prd_auto_project

    assert find_existing_prd_auto_project(prd_id, t.company_id) is None


def test_find_existing_prd_auto_project_ignores_manual_projects_with_same_artifact(
    tenant_client, isolated_settings
):
    """A MANUAL project that happens to include this PRD as one of its
    artifacts must NEVER be matched — only an `origin='prd_auto'` project
    dedupes against another auto-created fork."""
    t = tenant_client.make(slug="acme")
    prd_id = _seed_brief_and_prd(isolated_settings["db"], "acme")

    from app.db.projects import add_artifact, create_project
    from app.project_from_prd import find_existing_prd_auto_project

    manual_project = create_project(
        company_id=t.company_id, workspace_id="ws-1", name="Manual project",
        created_by=t.user_id, origin="manual",
    )
    add_artifact(manual_project["id"], "prd", prd_id)

    assert find_existing_prd_auto_project(prd_id, t.company_id) is None


def test_find_existing_prd_auto_project_ignores_different_artifact_type_same_id(
    tenant_client, isolated_settings
):
    """A `prd_auto` project holding an EVIDENCE artifact that happens to
    share the numeric id with this PRD must not false-positive — the match
    is scoped to `artifact_type='prd'`."""
    t = tenant_client.make(slug="acme")
    prd_id = _seed_brief_and_prd(isolated_settings["db"], "acme")

    from app.db.projects import add_artifact, create_project
    from app.project_from_prd import find_existing_prd_auto_project

    other_project = create_project(
        company_id=t.company_id, workspace_id="ws-1", name="Different artifact",
        created_by=t.user_id, origin="prd_auto",
    )
    add_artifact(other_project["id"], "evidence", prd_id)

    assert find_existing_prd_auto_project(prd_id, t.company_id) is None


def test_find_existing_prd_auto_project_is_company_scoped(tenant_client, isolated_settings):
    t = tenant_client.make(slug="acme")
    other = tenant_client.make(slug="widgets")
    prd_id = _seed_brief_and_prd(isolated_settings["db"], "acme")
    conv_id = _new_conversation(t.company_id, t.user_id)

    project_id = _fork_via_hook(t.company_id, t.user_id, "ws-1", prd_id, conv_id)
    assert project_id is not None

    from app.project_from_prd import find_existing_prd_auto_project

    # Same numeric prd_id, DIFFERENT company — must not resolve.
    assert find_existing_prd_auto_project(prd_id, other.company_id) is None


# ── Real local-Supabase round-trip (ship-gate tier) ────────────────────────
#
# `[[reference_local-supabase-real-db-verification]]` — proves the helper
# actually round-trips through real Postgres/PostgREST, not just the
# in-memory fake. Same posture as test_projects_crud_live.py /
# test_project_artifacts_fanout_live.py: skips cleanly unless the local rig
# is up and the env var is set.

_RUN_LIVE = os.getenv("RUN_PROJECT_FROM_PRD_LIVE") == "1"

live = pytest.mark.skipif(
    not _RUN_LIVE,
    reason=(
        "needs a real local Supabase — set RUN_PROJECT_FROM_PRD_LIVE=1 with "
        "SUPABASE_URL/SUPABASE_SERVICE_ROLE_KEY/SUPABASE_JWT_SECRET pointed "
        "at the local rig and the projects/chat/memory migrations applied"
    ),
)


def _sb():
    from app.config import settings
    from supabase import create_client

    url = settings.supabase_url
    assert "127.0.0.1" in url or "localhost" in url, (
        "refusing to run the live round-trip against a non-loopback "
        f"SUPABASE_URL ({url!r}) — this test mutates real rows"
    )
    assert settings.supabase_service_role_key, "SUPABASE_SERVICE_ROLE_KEY is not set"
    return create_client(url, settings.supabase_service_role_key)


@pytest.fixture(scope="module")
def sb():
    return _sb()


@pytest.fixture(scope="module")
def live_ids(sb):
    companies = sb.table("companies").select("id").limit(1).execute().data
    assert companies, "no company row in the local rig — seed one before running this test"
    company_id = companies[0]["id"]

    workspaces = (
        sb.table("workspaces").select("id").eq("company_id", company_id).limit(1).execute().data
    )
    assert workspaces, f"no workspace for company {company_id}"
    workspace_id = workspaces[0]["id"]

    owners = (
        sb.table("company_members")
        .select("user_id")
        .eq("company_id", company_id)
        .in_("role", ["owner", "admin"])
        .limit(1)
        .execute()
        .data
    )
    assert owners, f"need >=1 owner/admin company_members row for company {company_id}"
    user_id = owners[0]["user_id"]

    brief = sb.table("briefs").insert(
        {
            "dataset": f"live-pfp-{uuid.uuid4().hex[:8]}",
            "week_label": "Live auto-create-from-prd",
            "payload": {},
            "is_current": False,
        }
    ).execute().data[0]
    prd = sb.table("prds").insert(
        {
            "brief_id": brief["id"],
            "insight_index": 0,
            "title": f"Live PRD {uuid.uuid4().hex[:8]}",
            "status": "ready",
        }
    ).execute().data[0]
    conversation = sb.table("conversations").insert(
        {"company_id": company_id, "user_id": user_id, "title": "generate prd", "query": "generate prd"}
    ).execute().data[0]

    yield {
        "company_id": company_id,
        "workspace_id": workspace_id,
        "user_id": user_id,
        "prd_id": prd["id"],
        "prd_title": prd["title"],
        "conversation_id": conversation["id"],
    }

    sb.table("conversations").delete().eq("id", conversation["id"]).execute()
    sb.table("prds").delete().eq("id", prd["id"]).execute()
    sb.table("briefs").delete().eq("id", brief["id"]).execute()


@pytest.fixture
def _project_ids():
    created: list[int] = []
    yield created


@pytest.fixture
def _cleanup_live_projects(sb, _project_ids):
    # NOT autouse — this module mixes fast fake-DB tests with these live
    # ones; an autouse fixture here would force `sb()` (and its loopback-URL
    # assertion) to run for every fake-DB test too. Each live test below
    # requests this explicitly instead.
    yield
    for pid in _project_ids:
        sb.table("projects").delete().eq("id", pid).execute()


@live
def test_auto_create_makes_prd_auto_project_with_artifact_live(sb, live_ids, _project_ids, _cleanup_live_projects):
    from app.project_from_prd import maybe_auto_create_project_for_prd

    project_id = maybe_auto_create_project_for_prd(
        company_id=live_ids["company_id"], workspace_id=live_ids["workspace_id"],
        user_id=live_ids["user_id"], prd_id=live_ids["prd_id"],
        prd_title=live_ids["prd_title"], conversation_id=live_ids["conversation_id"],
    )
    assert project_id is not None
    _project_ids.append(project_id)

    project = sb.table("projects").select("*").eq("id", project_id).execute().data[0]
    assert project["origin"] == "prd_auto"
    assert project["name"] == live_ids["prd_title"]

    members = sb.table("project_members").select("*").eq("project_id", project_id).execute().data
    assert [m["user_id"] for m in members] == [live_ids["user_id"]]

    artifacts = sb.table("project_artifacts").select("*").eq("project_id", project_id).execute().data
    assert len(artifacts) == 1
    assert artifacts[0]["artifact_type"] == "prd"
    assert artifacts[0]["artifact_id"] == live_ids["prd_id"]

    conv = sb.table("conversations").select("project_id").eq("id", live_ids["conversation_id"]).execute().data[0]
    assert conv["project_id"] == project_id


@live
def test_auto_create_names_from_prd_title_live(sb, live_ids, _project_ids, _cleanup_live_projects):
    from app.project_from_prd import maybe_auto_create_project_for_prd

    project_id = maybe_auto_create_project_for_prd(
        company_id=live_ids["company_id"], workspace_id=live_ids["workspace_id"],
        user_id=live_ids["user_id"], prd_id=live_ids["prd_id"],
        prd_title=live_ids["prd_title"], conversation_id=live_ids["conversation_id"],
    )
    assert project_id is not None
    _project_ids.append(project_id)

    project = sb.table("projects").select("name").eq("id", project_id).execute().data[0]
    assert project["name"] == live_ids["prd_title"]
