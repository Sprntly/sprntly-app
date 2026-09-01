"""Route-level both-sides persistence for the private project chat:

  - `POST /v1/ask` on the project branch persists the user's turn at
    dispatch and the assistant's answer after `complete_ask_job` (AC1)
  - a MAIN-chat ask (no `project_id`) is byte-unchanged: no new persist, no
    double write (AC7)
  - `POST /{project_id}/prd/chat-edit` persists both sides for every exit
    shape (AC2/AC3)
  - the new owned turn-pair route `POST /{project_id}/individual/turns`
    covers the generate/clarify/terminal branches, idempotent and
    member-gated (AC2/AC3/AC4/AC6)

Fake-Supabase tier (mirrors `test_routes_ask.py` / `test_projects_prd_chat_
edit_route.py`'s own split). A project-scoped ask engages `scope.extra_tools`
and takes the sixth ladder branch (`qa_agent._try_scoped_tool_answer`), which
does not read the `fake_llm` fixture's stub seam — so, like
`test_project_answer_collapse.py`, these tests monkeypatch
`app.ask_job_runner.qa_agent.answer` directly for a project-scoped ask. The
real cross-project/cross-tenant Postgres fan-out + the real-LLM round trip
are the live suite's job.
"""
from __future__ import annotations

from types import SimpleNamespace

import app.ask_job_runner as ajr
import app.prd_edit as prd_edit
from app.db.client import require_client
from app.db.workspaces import ensure_default_workspace, upsert_workspace_member
from tests import _fake_supabase
from tests._project_helpers import seed_same_tenant_non_member

# The ★ cross-project gate's manifest read walks `list_artifacts_for_project`
# -> `list_artifacts_for_company`, which queries `prototypes` unconditionally
# — deliberately NOT in conftest's shared fake schema (mirrors
# `test_projects_prd_chat_edit_route.py`'s own trimmed copy).
_PROTOTYPE_DDL = """
CREATE TABLE IF NOT EXISTS prototypes (
    id                     INTEGER PRIMARY KEY AUTOINCREMENT,
    prd_id                 INTEGER,
    workspace_id           TEXT NOT NULL,
    status                 TEXT NOT NULL DEFAULT 'generating',
    created_at             TEXT NOT NULL DEFAULT (datetime('now'))
);
"""


def _mock_project_ask_answer(monkeypatch, answer_text: str) -> None:
    """A project-scoped ask always engages `scope.extra_tools` and takes the
    sixth ladder branch — patch `qa_agent.answer` at the module `ask_job_
    runner` actually calls it through, same seam
    `test_project_answer_collapse.py` uses."""
    def _fake_answer(**kw):
        return {"answer": answer_text, "citations": [], "key_points": [], "confidence": 0.9, "unanswered": ""}

    monkeypatch.setattr(ajr.qa_agent, "answer", _fake_answer)


def _seed_corpus(data_dir, dataset, body="some corpus body"):
    ds = data_dir / dataset
    ds.mkdir(exist_ok=True)
    (ds / "a.md").write_text(body)


import pytest  # noqa: E402


@pytest.fixture(autouse=True)
def _prototypes_table(isolated_settings):
    """The ★ cross-project gate's manifest read (`assert_prd_on_project`,
    inside `apply_chat_edit_scoped`) walks `list_artifacts_for_project` ->
    `list_artifacts_for_company`, which queries `prototypes` unconditionally
    — not in conftest's shared fake schema (see that table's own NOTE
    comment)."""
    _fake_supabase.get_fake_db().executescript(_PROTOTYPE_DDL)
    yield


def _seed_project(t, isolated_settings, *, name: str = "Persistence route project") -> int:
    from app.db import projects as projects_db

    ws_id = ensure_default_workspace(t.company_id)["id"]
    project = projects_db.create_project(
        company_id=t.company_id, workspace_id=ws_id, name=name, created_by=t.user_id,
    )
    return project["id"]


def _turns(conversation_id: int, user_id: str):
    from app.db import conversations as conversations_db

    return conversations_db.list_individual_turns(conversation_id, user_id)


# RETIRED (2026-08-20): test_ask_project_branch_persists_user_at_dispatch and
# test_run_sync_persists_answer_after_complete asserted that `/v1/ask` itself
# persists the individual project chat's user turn (at dispatch) and assistant
# turn (after complete_ask_job), gated on a TOP-LEVEL `body.project_id`. Both are
# deprecated: (1) the live individual chat persists both sides CLIENT-side via
# `api.addTurn` -> `POST /v1/conversations/{id}/turns` (see
# `web/.../projects/useProjectConversation.ts` + `lib/chatPersistence.ts`), NOT
# through `/v1/ask`; and (2) it carries its project on `context_source.params`,
# never the top-level `project_id` these tests POST (which has no live client
# caller). The owned writers themselves (exercised by the /individual/turns and
# /prd/chat-edit route tests below) remain live. Re-add if a future slice moves
# individual persistence back onto the server ask path.


# ── Main chat NOT double-written (AC7, security spine) ───────────────────


def test_ask_main_branch_not_double_written(tenant_client, isolated_settings, fake_llm):
    t = tenant_client.make(slug="acme")
    _seed_corpus(isolated_settings["data_dir"], dataset="acme")
    fake_llm["payload"] = {
        "answer": "plain answer", "key_points": [], "citations": [], "confidence": 0.9, "unanswered": "",
    }

    # A bare main-chat ask carries no project_id at all.
    resp = t.client.post(
        "/v1/ask", json={"question": "plain question", "dataset": "acme"},
    )
    assert resp.status_code == 200, resp.text

    # No individual project chat exists for this user in ANY project — the
    # new persist code never ran (there is no project_id to gate it open).
    rows = (
        require_client().table("conversations").select("id")
        .eq("user_id", t.user_id).eq("kind", "individual").execute().data
    )
    assert rows == []


# ── PRD-edit route — both sides on every exit shape (AC2/AC3) ────────────


def test_prd_edit_route_persists_both_sides(tenant_client, isolated_settings, monkeypatch):
    monkeypatch.setenv("PROJECT_PRD_EDIT_ENABLED", "1")
    t = tenant_client.make(slug="acme")
    project_id = _seed_project(t, isolated_settings)

    monkeypatch.setattr(prd_edit, "apply_chat_edit", lambda *a, **kw: {
        "html": "<html><body><h1>Doc v2</h1></body></html>",
        "sections_changed": ["Requirements"],
        "summary": "Tightened requirements.",
    })

    # No PRD attached → the no-target-resolved branch, which still persists
    # both sides (AC3 — a terminal-shaped outcome).
    resp = t.client.post(
        f"/v1/projects/{project_id}/prd/chat-edit",
        json={"instruction": "tighten requirements", "client_message_id": "edit-cmid-1"},
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["edited"] is False

    from app.db import conversations as conversations_db

    conv = conversations_db.get_individual_project_chat(project_id, t.user_id)
    turns = _turns(conv["id"], t.user_id)
    assert [tn["role"] for tn in turns] == ["user", "assistant"]
    assert turns[0]["content"] == "tighten requirements"
    assert turns[1]["content"] == resp.json()["answer"]


def test_prd_edit_route_persists_success_shape(tenant_client, isolated_settings, monkeypatch):
    from app.db import projects as projects_db

    monkeypatch.setenv("PROJECT_PRD_EDIT_ENABLED", "1")
    t = tenant_client.make(slug="acme")
    project_id = _seed_project(t, isolated_settings)

    # Seed a PRD via the module's own DB helpers (mirrors
    # `test_projects_prd_chat_edit_route.py::_seed_prd`).
    from app.db import save_brief, start_prd, complete_prd

    brief_id = save_brief(
        dataset="acme", week_label="Week of stub",
        payload={"summary_headline": "s", "insights": [{"title": "I0"}], "_schema_version": 1},
        schema_version=1,
    )
    prd_id = start_prd(
        brief_id=brief_id, insight_index=0, title="Doc",
        template_version=1, variant="v3", source="chat", theme_id="chat:seed",
    )
    complete_prd(prd_id, title="Doc", md="<html><body><h1>Doc</h1></body></html>")
    projects_db.add_artifact(project_id, "prd", prd_id)

    monkeypatch.setattr(prd_edit, "apply_chat_edit", lambda *a, **kw: {
        "html": "<html><body><h1>Doc v2</h1></body></html>",
        "sections_changed": ["Requirements"],
        "summary": "Tightened requirements.",
    })

    # Applies DIRECTLY through the shared editor — one call, no confirm step
    # — against the explicit open-drawer `prd_id`; the turn pair persists on
    # the APPLIED result, keyed by the same client_message_id the call sent.
    resp = t.client.post(
        f"/v1/projects/{project_id}/prd/chat-edit",
        json={
            "instruction": "tighten requirements", "prd_id": prd_id,
            "client_message_id": "edit-cmid-2",
        },
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["edited"] is True

    from app.db import conversations as conversations_db

    conv = conversations_db.get_individual_project_chat(project_id, t.user_id)
    turns = _turns(conv["id"], t.user_id)
    assert [tn["role"] for tn in turns] == ["user", "assistant"]
    # The stored ORIGINAL instruction is the user turn; the assistant turn is
    # the completed 'Done' narration carrying the edit summary.
    assert turns[0]["content"] == "tighten requirements"
    assert turns[1]["content"].startswith("Done — I've updated the PRD.")
    assert "Tightened requirements." in turns[1]["content"]


# ── The owned turn-pair route (AC2/AC3/AC4/AC6) ──────────────────────────


def test_individual_turns_route_persists_pair_owned(tenant_client, isolated_settings):
    t = tenant_client.make(slug="acme")
    project_id = _seed_project(t, isolated_settings)

    resp = t.client.post(
        f"/v1/projects/{project_id}/individual/turns",
        json={"client_message_id": "gen-1", "question": "make me a PRD", "answer": "Generated and attached."},
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert "user_turn_id" in body and "assistant_turn_id" in body

    from app.db import conversations as conversations_db

    conv = conversations_db.get_individual_project_chat(project_id, t.user_id)
    turns = _turns(conv["id"], t.user_id)
    assert [tn["role"] for tn in turns] == ["user", "assistant"]

    # Idempotent — a double-submit returns the SAME pair, no duplicate rows.
    resp2 = t.client.post(
        f"/v1/projects/{project_id}/individual/turns",
        json={"client_message_id": "gen-1", "question": "make me a PRD", "answer": "Generated and attached."},
    )
    assert resp2.status_code == 200, resp2.text
    assert resp2.json() == body
    turns_after = _turns(conv["id"], t.user_id)
    assert len(turns_after) == 2


def test_terminal_outcome_persisted(tenant_client, isolated_settings):
    """AC3: an error/clarify/artifact-attach-failed settle persists its
    shown text via the same owned route — a reload shows the real outcome,
    not a blank."""
    t = tenant_client.make(slug="acme")
    project_id = _seed_project(t, isolated_settings)

    resp = t.client.post(
        f"/v1/projects/{project_id}/individual/turns",
        json={
            "client_message_id": "terminal-1",
            "question": "generate tickets for this",
            "answer": "I generated that PRD but couldn't attach it. Try again.",
        },
    )
    assert resp.status_code == 200, resp.text

    from app.db import conversations as conversations_db

    conv = conversations_db.get_individual_project_chat(project_id, t.user_id)
    turns = _turns(conv["id"], t.user_id)
    assert turns[1]["content"] == "I generated that PRD but couldn't attach it. Try again."


def test_non_member_cannot_persist(tenant_client, isolated_settings):
    t = tenant_client.make(slug="acme")
    project_id = _seed_project(t, isolated_settings)
    non_member_id, _ = seed_same_tenant_non_member(SimpleNamespace(company_id=t.company_id))
    headers = tenant_client.bearer(non_member_id)

    resp = t.client.post(
        f"/v1/projects/{project_id}/individual/turns",
        json={"client_message_id": "x", "question": "q", "answer": "a"},
        headers=headers,
    )
    assert resp.status_code == 403

    from app.db import conversations as conversations_db

    conv = conversations_db.get_individual_project_chat(project_id, non_member_id)
    assert conv is None


