"""Deterministic open-artifact classification + generic (no-title) resolution.

A bare "open the PRD" (an opening verb + an artifact-type noun, no title) used
to mis-classify as `answer` — the client then sent it to /v1/ask and the answer
engine refused ("that's a UI action"). Now:

  * `detect_open_intent` forces `open_artifact` WITHOUT the model, for a clear
    open request, and never for a generate/list request;
  * `resolve_open_artifact` resolves a GENERIC (empty) query to the sole
    openable artifact of that kind — the project's PRD in a project chat, the
    workspace's single PRD (or an ambiguous chip list) on main.

These route tests do NOT mock `resolve_chat_intent`: the deterministic force
returns before any planner/model call, so the real classifier runs offline.
"""
from __future__ import annotations

import pytest

from app.chat_intent import detect_open_intent
from app.db import conversations as conversations_db
from app.db import projects as projects_db
from app.db.workspaces import ensure_default_workspace

_PROTOTYPE_DDL = """
CREATE TABLE IF NOT EXISTS prototypes (
    id                INTEGER PRIMARY KEY AUTOINCREMENT,
    prd_id            INTEGER,
    workspace_id      TEXT NOT NULL,
    status            TEXT NOT NULL DEFAULT 'generating',
    preview_image_url TEXT,
    is_complete       INTEGER NOT NULL DEFAULT 0,
    created_at        TEXT NOT NULL DEFAULT (datetime('now'))
);
"""


@pytest.fixture(autouse=True)
def _with_prototypes_table(isolated_settings):
    from tests import _fake_supabase

    _fake_supabase.get_fake_db().executescript(_PROTOTYPE_DDL)
    return isolated_settings


def _seed_prd(db_mod, *, dataset: str, title: str, theme_id: str) -> int:
    brief_id = db_mod.save_brief(
        dataset=dataset, week_label="Week of stub",
        payload={"summary_headline": "s", "insights": [{"title": "I0"}],
                 "_schema_version": 1},
        schema_version=1,
    )
    prd_id = db_mod.start_prd(
        brief_id=brief_id, insight_index=0, title=title,
        template_version=1, variant="v3", source="chat", theme_id=theme_id,
    )
    db_mod.complete_prd(prd_id, title=title, md="<html><body>Doc</body></html>")
    return prd_id


def _seed_project_with_prd(t, prd_id: int) -> int:
    ws_id = ensure_default_workspace(t.company_id)["id"]
    project = projects_db.create_project(
        company_id=t.company_id, workspace_id=ws_id, name="Launch",
        created_by=t.user_id,
    )
    projects_db.add_artifact(project["id"], "prd", prd_id)
    return project["id"]


def _insert_legacy_group_conversation(t, project_id: int) -> dict:
    """A `kind='group'` conversation row inserted directly (the group-chat
    WRITE path — `create_group_chat` — was removed with the group-chat
    backend; pre-existing group rows are explicitly NOT deleted from the
    database, so project-scope resolution must keep working for them)."""
    from app.db.client import require_client

    ws_id = ensure_default_workspace(t.company_id)["id"]
    return (
        require_client()
        .table("conversations")
        .insert(
            {
                "company_id": t.company_id,
                "workspace_id": ws_id,
                "user_id": t.user_id,
                "project_id": project_id,
                "kind": "group",
            }
        )
        .execute()
        .data[0]
    )


# ─── the pure detector ───────────────────────────────────────────────────────


@pytest.mark.parametrize(
    "message, expected",
    [
        ("open the PRD", ("prd", None)),
        ("open prd", ("prd", None)),
        ("show me the PRD", ("prd", None)),
        ("pull up the PRD", ("prd", None)),
        ("let me see the PRD", ("prd", None)),
        ("open the evidence", ("evidence", None)),
        ("open the report", ("report", None)),
        ("open the tickets", ("tickets", None)),
        ("open the PRD for compliance reporting", ("prd", "compliance reporting")),
        ("open the checkout prd", ("prd", "checkout")),
        ("show me the dark mode prototype", ("prototype", "dark mode")),
    ],
)
def test_detect_open_intent_fires(message, expected):
    assert detect_open_intent(message) == expected


@pytest.mark.parametrize(
    "message",
    [
        "generate a PRD for checkout",
        "create the PRD",
        "write a PRD",
        "redo the PRD",
        "which PRDs exist?",
        "how many reports do we have",
        "what is the PRD about",
        "list my specs",
        "open the door",
        "tell me about the roadmap",
    ],
)
def test_detect_open_intent_declines(message):
    """Authoring, list/count, and non-artifact requests are left to the model."""
    assert detect_open_intent(message) is None


# ─── route: bare open resolves project-scoped, no model mock ──────────────────


def test_individual_chat_bare_open_resolves_project_prd(
    tenant_client, isolated_settings, monkeypatch
):
    """The reported bug: bare "open the PRD" in a private project chat now
    classifies open_artifact (deterministically) and RESOLVES to the project's
    PRD, amid identically-titled workspace PRDs — not a "UI action" answer."""
    t = tenant_client.make(slug="acme")
    db = isolated_settings["db"]
    project_prd = _seed_prd(db, dataset="acme", title="Compliance Reporting", theme_id="in")
    _seed_prd(db, dataset="acme", title="Compliance Reporting", theme_id="ws1")
    _seed_prd(db, dataset="acme", title="Something Else", theme_id="ws2")
    project_id = _seed_project_with_prd(t, project_prd)
    conv = conversations_db.create_individual_project_chat(project_id, t.user_id)

    body = t.client.post(
        "/v1/chat/intent",
        json={"message": "open the PRD", "conversation_id": conv["id"]},
    ).json()

    assert body["intent"] == "open_artifact"
    assert body["source"] == "open_intent"
    assert body["open"]["status"] == "resolved"
    assert body["open"]["artifact"]["prd_id"] == project_prd


def test_group_chat_bare_open_resolves_project_prd(
    tenant_client, isolated_settings, monkeypatch
):
    t = tenant_client.make(slug="acme")
    db = isolated_settings["db"]
    project_prd = _seed_prd(db, dataset="acme", title="Compliance Reporting", theme_id="g")
    _seed_prd(db, dataset="acme", title="Compliance Reporting", theme_id="gws")
    project_id = _seed_project_with_prd(t, project_prd)
    group = _insert_legacy_group_conversation(t, project_id)

    body = t.client.post(
        "/v1/chat/intent",
        json={"message": "open prd", "conversation_id": group["id"]},
    ).json()

    assert body["intent"] == "open_artifact"
    assert body["open"]["status"] == "resolved"
    assert body["open"]["artifact"]["prd_id"] == project_prd


def test_titled_open_in_project_still_resolves_by_title(
    tenant_client, isolated_settings, monkeypatch
):
    """A titled open in a project keeps title matching AND project scope — the
    project's PRD, not the identically-titled workspace twin."""
    t = tenant_client.make(slug="acme")
    db = isolated_settings["db"]
    project_prd = _seed_prd(db, dataset="acme", title="Compliance Reporting", theme_id="t-in")
    _seed_prd(db, dataset="acme", title="Compliance Reporting", theme_id="t-ws")
    _seed_prd(db, dataset="acme", title="Dark Mode", theme_id="t-dm")
    project_id = _seed_project_with_prd(t, project_prd)
    conv = conversations_db.create_individual_project_chat(project_id, t.user_id)

    body = t.client.post(
        "/v1/chat/intent",
        json={"message": "open the PRD for compliance reporting",
              "conversation_id": conv["id"]},
    ).json()

    assert body["intent"] == "open_artifact"
    assert body["open"]["status"] == "resolved"
    assert body["open"]["artifact"]["prd_id"] == project_prd


# ─── route: main chat bare open (no project) ─────────────────────────────────


def test_main_chat_bare_open_single_prd_resolves(
    tenant_client, isolated_settings, monkeypatch
):
    """On main chat a bare open resolves to the workspace's sole PRD (not a
    refusal, not not_found)."""
    t = tenant_client.make(slug="acme")
    db = isolated_settings["db"]
    prd = _seed_prd(db, dataset="acme", title="Only PRD", theme_id="m-only")

    body = t.client.post(
        "/v1/chat/intent", json={"message": "open the PRD"},
    ).json()

    assert body["intent"] == "open_artifact"
    assert body["open"]["status"] == "resolved"
    assert body["open"]["artifact"]["prd_id"] == prd


def test_main_chat_bare_open_many_prds_is_ambiguous(
    tenant_client, isolated_settings, monkeypatch
):
    """Several workspace PRDs and no title → ask which, with real chips (still an
    open verdict, never the "UI action" answer)."""
    t = tenant_client.make(slug="acme")
    db = isolated_settings["db"]
    a = _seed_prd(db, dataset="acme", title="Alpha", theme_id="m-a")
    b = _seed_prd(db, dataset="acme", title="Beta", theme_id="m-b")

    body = t.client.post(
        "/v1/chat/intent", json={"message": "open the PRD"},
    ).json()

    assert body["intent"] == "open_artifact"
    assert body["open"]["status"] == "ambiguous"
    assert {c["prd_id"] for c in body["open"]["candidates"]} == {a, b}
