"""Structured attachments on the private project chat's `/v1/ask` send:

  - the project branch PERSISTS structured attachments onto
    `conversation_turns.attachments` and persists the CLEAN question (no
    inline `[Attached files]` concat) — the chip survives a reload;
  - it FOLDS the current turn's attachment text into the question the ANSWER
    sees, reusing `_load_history`'s exact `[Attached: {name}]` format;
  - a FOLLOW-UP turn's `_load_history` folds the prior turn's attachment
    exactly ONCE (persisting the clean question is what prevents a
    double-count);
  - a MAIN-chat ask (no `project_id`) never reads attachments and never
    persists a turn here — the shared route is provably unchanged;
  - the new optional `attachments` kwarg on the owned writer defaults to a
    no-op, so the pre-existing call shape is unaffected.

Fake-Supabase tier (mirrors `test_individual_persistence_routes.py`): a
project-scoped ask engages `scope.extra_tools` and takes the sixth ladder
branch, which does not read the `fake_llm` stub — so, like that suite, these
tests monkeypatch `app.ask_job_runner.qa_agent.answer` directly and CAPTURE
the question it is handed. The real cross-tenant Postgres fan-out + real-LLM
round trip are the live suite's job.
"""
from __future__ import annotations

import pytest

import app.ask_job_runner as ajr
from app.db.client import require_client
from app.db.workspaces import ensure_default_workspace
from tests import _fake_supabase

# `_resolve_prd_id` walks a path that queries `prototypes` unconditionally —
# not in conftest's shared fake schema (mirrors the sibling suite's copy).
_PROTOTYPE_DDL = """
CREATE TABLE IF NOT EXISTS prototypes (
    id                     INTEGER PRIMARY KEY AUTOINCREMENT,
    prd_id                 INTEGER,
    workspace_id           TEXT NOT NULL,
    status                 TEXT NOT NULL DEFAULT 'generating',
    created_at             TEXT NOT NULL DEFAULT (datetime('now'))
);
"""


@pytest.fixture(autouse=True)
def _prototypes_table(isolated_settings):
    _fake_supabase.get_fake_db().executescript(_PROTOTYPE_DDL)
    yield


def _capture_project_ask_answer(monkeypatch, answer_text: str) -> list[str]:
    """Patch the sixth-ladder `qa_agent.answer` seam and CAPTURE the question
    it is handed (the answer-facing, attachment-folded text)."""
    seen: list[str] = []

    def _fake_answer(**kw):
        seen.append(kw.get("question", ""))
        return {"answer": answer_text, "citations": [], "key_points": [], "confidence": 0.9, "unanswered": ""}

    monkeypatch.setattr(ajr.qa_agent, "answer", _fake_answer)
    return seen


def _seed_corpus(data_dir, dataset, body="some corpus body"):
    ds = data_dir / dataset
    ds.mkdir(exist_ok=True)
    (ds / "a.md").write_text(body)


def _seed_project(t, *, name: str = "Attachments project") -> int:
    from app.db import projects as projects_db

    ws_id = ensure_default_workspace(t.company_id)["id"]
    project = projects_db.create_project(
        company_id=t.company_id, workspace_id=ws_id, name=name, created_by=t.user_id,
    )
    return project["id"]


def _turns(conversation_id: int, user_id: str):
    from app.db import conversations as conversations_db

    return conversations_db.list_individual_turns(conversation_id, user_id)


# ── Persist structured attachments + clean question (AC13) ───────────────


def test_project_ask_persists_structured_attachments(tenant_client, isolated_settings, monkeypatch):
    t = tenant_client.make(slug="acme")
    _seed_corpus(isolated_settings["data_dir"], dataset="acme")
    project_id = _seed_project(t)
    _capture_project_ask_answer(monkeypatch, "the answer")

    conv_resp = t.client.post(f"/v1/projects/{project_id}/individual")
    conversation_id = conv_resp.json()["id"]

    resp = t.client.post(
        "/v1/ask",
        json={
            "question": "summarize the notes",
            "dataset": "acme",
            "project_id": project_id,
            "conversation_id": conversation_id,
            "attachments": [{"name": "notes.txt", "content": "the attached body text"}],
        },
    )
    assert resp.status_code == 200, resp.text

    from app.db import conversations as conversations_db

    conv = conversations_db.get_individual_project_chat(project_id, t.user_id)
    turns = _turns(conv["id"], t.user_id)
    user_turn = next(tn for tn in turns if tn["role"] == "user")
    # The CLEAN question is persisted — no inline `[Attached files]` block.
    assert user_turn["content"] == "summarize the notes"
    assert "[Attached" not in user_turn["content"]

    # The STRUCTURED attachments landed on the column.
    row = (
        require_client().table("conversation_turns").select("attachments")
        .eq("id", user_turn["id"]).execute().data[0]
    )
    attachments = row["attachments"]
    assert isinstance(attachments, list)
    assert attachments[0]["name"] == "notes.txt"
    assert attachments[0]["content"] == "the attached body text"


def test_project_ask_folds_attachment_into_answer_question(tenant_client, isolated_settings, monkeypatch):
    t = tenant_client.make(slug="acme")
    _seed_corpus(isolated_settings["data_dir"], dataset="acme")
    project_id = _seed_project(t)
    seen = _capture_project_ask_answer(monkeypatch, "the answer")

    conv_resp = t.client.post(f"/v1/projects/{project_id}/individual")
    conversation_id = conv_resp.json()["id"]

    resp = t.client.post(
        "/v1/ask",
        json={
            "question": "summarize the notes",
            "dataset": "acme",
            "project_id": project_id,
            "conversation_id": conversation_id,
            "attachments": [{"name": "notes.txt", "content": "the attached body text"}],
        },
    )
    assert resp.status_code == 200, resp.text

    # The ANSWER saw the folded question, in the exact `[Attached: {name}]`
    # format `_load_history` uses.
    assert seen, "qa_agent.answer was never reached"
    folded = seen[-1]
    assert folded.startswith("summarize the notes")
    assert "[Attached: notes.txt]\nthe attached body text" in folded


def test_followup_history_folds_attachment_once(tenant_client, isolated_settings, monkeypatch):
    t = tenant_client.make(slug="acme")
    _seed_corpus(isolated_settings["data_dir"], dataset="acme")
    project_id = _seed_project(t)
    _capture_project_ask_answer(monkeypatch, "the answer")

    conv_resp = t.client.post(f"/v1/projects/{project_id}/individual")
    conversation_id = conv_resp.json()["id"]

    # Turn 1 carries the attachment.
    resp = t.client.post(
        "/v1/ask",
        json={
            "question": "summarize the notes",
            "dataset": "acme",
            "project_id": project_id,
            "conversation_id": conversation_id,
            "attachments": [{"name": "notes.txt", "content": "the attached body text"}],
        },
    )
    assert resp.status_code == 200, resp.text

    # A follow-up read (`_load_history`) folds the PRIOR turn's attachment onto
    # its content EXACTLY ONCE — the clean-persist is what prevents a
    # double-count.
    from app.routes.ask import _load_history

    history = _load_history(conversation_id, t.company_id, t.user_id)
    user_rows = [h for h in history if h["role"] == "user"]
    assert user_rows, "the persisted user turn is missing from history"
    folded_content = user_rows[0]["content"]
    assert folded_content.count("[Attached: notes.txt]") == 1
    assert folded_content.count("the attached body text") == 1


# ── Main chat unchanged (AC15) ───────────────────────────────────────────


def test_non_project_ask_ignores_attachments_and_persists_nothing(tenant_client, isolated_settings, fake_llm):
    t = tenant_client.make(slug="acme")
    _seed_corpus(isolated_settings["data_dir"], dataset="acme")
    fake_llm["payload"] = {
        "answer": "plain answer", "key_points": [], "citations": [], "confidence": 0.9, "unanswered": "",
    }

    # A main-chat ask carries NO project_id — attachments (if a client ever
    # sent them) are ignored and no individual turn is persisted here.
    resp = t.client.post(
        "/v1/ask",
        json={
            "question": "plain question",
            "dataset": "acme",
            "attachments": [{"name": "x.txt", "content": "should be ignored"}],
        },
    )
    assert resp.status_code == 200, resp.text

    rows = (
        require_client().table("conversations").select("id")
        .eq("user_id", t.user_id).eq("kind", "individual").execute().data
    )
    assert rows == []


# ── The owned writer's new optional kwarg is a no-op default (AC22) ───────


def test_post_owned_individual_user_turn_without_attachments_unchanged(tenant_client, isolated_settings):
    t = tenant_client.make(slug="acme")
    project_id = _seed_project(t)

    from app.db.conversations import post_owned_individual_user_turn

    # The pre-existing call shape (no attachments kwarg) still works and writes
    # no attachments — byte-identical to the pre-attachment insert.
    row = post_owned_individual_user_turn(
        project_id=project_id,
        user_id=t.user_id,
        content="plain turn",
        client_message_id="cmid-noattach",
    )
    assert row["content"] == "plain turn"

    stored = (
        require_client().table("conversation_turns").select("attachments")
        .eq("id", row["id"]).execute().data[0]
    )
    assert not stored["attachments"]
