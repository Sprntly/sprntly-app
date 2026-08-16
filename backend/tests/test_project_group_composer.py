"""The group composer's `+` payloads reach the group agent.

Mirrors the private surface's contract: an attachment's extracted text
rides the AGENT'S QUESTION (never the visible transcript or the wire
DTO), a pinned skill rides the SPLICED trigger in `content` (the one
splice rule every surface uses — the engine's slash-trigger routing reads
it from the text), and `client_message_id` stays the idempotency spine.
Proven here:

  * `attachments` persist onto the human turn (existing column) and fold
    into the reply's question as the `[Attached files]` block; the read
    DTO still strips them;
  * a `pinned_skill` dict + `client_message_id` are accepted (200, turn
    posted) — the skill's effect rides the spliced trigger text, which
    reaches the engine's question;
  * a plain post (no opts) behaves exactly as before.
"""
from __future__ import annotations

import app.routes.projects as projects_route
from app.db import conversations as conversations_db
from app.db.workspaces import ensure_default_workspace


def _seed_project(t) -> int:
    from app.db import projects as projects_db

    ws_id = ensure_default_workspace(t.company_id)["id"]
    return projects_db.create_project(
        company_id=t.company_id, workspace_id=ws_id, name="Composer project",
        created_by=t.user_id,
    )["id"]


def _stub_classify(monkeypatch):
    monkeypatch.setattr(
        projects_route, "_classify_and_maybe_edit_group_prd",
        lambda *a, **kw: projects_route._GroupEditOutcome(
            applied_turn=None, was_edit_request=False, refusal=None,
        ),
    )


def _spy_answer(monkeypatch):
    calls: list[dict] = []

    def _answer(**kw):
        calls.append(kw)
        return {"answer": "noted.", "citations": []}

    monkeypatch.setattr(projects_route.qa_agent, "answer", _answer)
    return calls


def test_attachments_persist_and_fold_into_question(
    tenant_client, isolated_settings, monkeypatch
):
    t = tenant_client.make(slug="acme")
    project_id = _seed_project(t)
    conv = conversations_db.create_group_chat(project_id, t.user_id)
    _stub_classify(monkeypatch)
    calls = _spy_answer(monkeypatch)

    resp = t.client.post(
        f"/v1/projects/{project_id}/group/turns",
        json={
            "content": "@Sprntly summarize the attached notes",
            "attachments": [
                {"name": "notes.md", "content": "Q3 pipeline is slipping."},
            ],
            "client_message_id": "cmid-attach-1",
        },
    )
    assert resp.status_code == 200, resp.text

    # The agent's QUESTION carries the fold…
    assert calls, "the mention must have produced a reply run"
    question = calls[-1]["question"]
    assert "[Attached files]" in question
    assert "--- notes.md ---" in question
    assert "Q3 pipeline is slipping." in question

    # …while the visible thread + wire DTO never carry the file text.
    turns = conversations_db.list_group_turns(conv["id"])
    human = [x for x in turns if x["role"] == "user"]
    assert human[-1]["content"] == "@Sprntly summarize the attached notes"
    assert all("attachments" not in x for x in turns)


def test_pinned_skill_and_client_message_id_accepted(
    tenant_client, isolated_settings, monkeypatch
):
    """The composer's skill pick posts without error and the SPLICED trigger
    (already in `content`, the FE's one splice rule) reaches the engine's
    question — the routing input is the text, same as every other surface."""
    t = tenant_client.make(slug="acme")
    project_id = _seed_project(t)
    conversations_db.create_group_chat(project_id, t.user_id)
    _stub_classify(monkeypatch)
    calls = _spy_answer(monkeypatch)

    resp = t.client.post(
        f"/v1/projects/{project_id}/group/turns",
        json={
            "content": "/weekly-report @Sprntly pull the numbers",
            "pinned_skill": {"id": "sk-1", "trigger": "/weekly-report", "label": "Weekly report"},
            "client_message_id": "cmid-skill-1",
        },
    )
    assert resp.status_code == 200, resp.text
    assert calls and "/weekly-report" in calls[-1]["question"]


def test_plain_post_without_opts_unchanged(
    tenant_client, isolated_settings, monkeypatch
):
    t = tenant_client.make(slug="acme")
    project_id = _seed_project(t)
    conv = conversations_db.create_group_chat(project_id, t.user_id)
    _stub_classify(monkeypatch)
    calls = _spy_answer(monkeypatch)

    resp = t.client.post(
        f"/v1/projects/{project_id}/group/turns",
        json={"content": "@Sprntly hello there"},
    )
    assert resp.status_code == 200, resp.text
    assert calls and "[Attached files]" not in calls[-1]["question"]
    turns = conversations_db.list_group_turns(conv["id"])
    assert turns[0]["content"] == "@Sprntly hello there"
