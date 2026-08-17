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

import asyncio
from types import SimpleNamespace

import app.qa_agent as qa
import app.routes.projects as projects_route
from app.db import conversations as conversations_db
from app.db.workspaces import ensure_default_workspace


def _ctx(company_id, user_id):
    return SimpleNamespace(
        company_id=company_id,
        workspace_id=ensure_default_workspace(company_id)["id"],
        user_id=user_id,
        user_email=None,
    )


def _seed_project(t) -> int:
    from app.db import projects as projects_db

    ws_id = ensure_default_workspace(t.company_id)["id"]
    return projects_db.create_project(
        company_id=t.company_id, workspace_id=ws_id, name="Composer project",
        created_by=t.user_id,
    )["id"]


def _stub_classify(monkeypatch):
    monkeypatch.setattr(
        projects_route, "_classify_group_envelope",
        lambda *a, **kw: {"intent": "answer"},
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


def test_group_pinned_skill_reaches_qa_agent_fresh_path(
    tenant_client, isolated_settings, monkeypatch
):
    """The FE's skill pick id is threaded to `qa_agent.answer(pinned_skill=…)`
    on the fresh mention path — an EXPLICIT routing input that skips routing,
    like main/private (not merely the spliced trigger text)."""
    t = tenant_client.make(slug="acme")
    project_id = _seed_project(t)
    conversations_db.create_group_chat(project_id, t.user_id)
    _stub_classify(monkeypatch)
    calls = _spy_answer(monkeypatch)

    resp = t.client.post(
        f"/v1/projects/{project_id}/group/turns",
        json={
            "content": "/weekly-report @Sprntly pull the numbers",
            "pinned_skill": {"id": "weekly-report", "trigger": "/weekly-report", "label": "Weekly report"},
            "client_message_id": "cmid-fresh",
        },
    )
    assert resp.status_code == 200, resp.text
    assert calls and calls[-1].get("pinned_skill") == "weekly-report"


def test_group_pinned_skill_recovered_on_retry_keyed_to_source_turn(
    tenant_client, isolated_settings, monkeypatch
):
    """On a RETRY the FE resends no pinned_skill, so the id is recovered from
    the SOURCE turn's own spliced trigger — keyed to `source_turn_id`, resolved
    inside `_schedule_group_reply`. With an INTERVENING message present, the
    resolution must still key off the source turn (turn A), NOT the latest turn
    (turn B) that `_respond_as_group_agent`'s self-derived trigger would pick."""
    from app.db.asks import fail_ask_job, start_ask_job

    t = tenant_client.make(slug="acme")
    project_id = _seed_project(t)
    _stub_classify(monkeypatch)
    calls = _spy_answer(monkeypatch)
    # No custom skill is uploaded in this test DB, so make the parsed trigger
    # routable directly (the same gate the engine's slash fast-path uses).
    monkeypatch.setattr(
        projects_route.qa_agent, "_routable", lambda token, eid=None: token == "weekly-report"
    )

    conv = conversations_db.create_group_chat(project_id, t.user_id)
    # Source turn A carries the spliced skill trigger in its content.
    turn_a = conversations_db.post_group_turn(
        conv["id"], t.user_id, "/weekly-report @Sprntly pull the numbers"
    )
    # Seed a FAILED run for A so the retry is granted (202).
    job = start_ask_job(
        company_id=t.company_id, dataset="acme", question="",
        conversation_id=conv["id"], kind="project_group", project_id=project_id,
        source_turn_id=turn_a["id"], run_id="r0",
    )
    fail_ask_job(job, "TypeError: boom", "app")
    # An INTERVENING turn B lands after A — it is now the LATEST authored turn.
    conversations_db.post_group_turn(conv["id"], t.user_id, "@Sprntly what about pricing?")

    calls.clear()
    resp = t.client.post(f"/v1/projects/{project_id}/group/turns/{turn_a['id']}/retry")
    assert resp.status_code == 202, resp.text
    assert calls, "the retry must produce a reply run"
    # Recovered from turn A's trigger, not turn B's content.
    assert calls[-1].get("pinned_skill") == "weekly-report"


def test_group_no_skill_passes_none(tenant_client, isolated_settings, monkeypatch):
    """A post with no pinned skill and no slash trigger passes
    `pinned_skill=None` to the engine (regression — no accidental pin)."""
    t = tenant_client.make(slug="acme")
    project_id = _seed_project(t)
    conversations_db.create_group_chat(project_id, t.user_id)
    _stub_classify(monkeypatch)
    calls = _spy_answer(monkeypatch)

    resp = t.client.post(
        f"/v1/projects/{project_id}/group/turns",
        json={"content": "@Sprntly hello there", "client_message_id": "cmid-none"},
    )
    assert resp.status_code == 200, resp.text
    assert calls and calls[-1].get("pinned_skill") is None


def test_schedule_reply_new_kwarg_defaults_none_no_migration():
    """`_schedule_group_reply`/`_respond_as_group_agent` accept the new
    `pinned_skill` kwarg defaulting None (no existing caller breaks). NO
    migration is added: the pin is RECOVERED from the source turn's durable
    spliced trigger text (`_pin_from_source_turn` reads the turn content and
    runs the engine's slash parse), never persisted to a new ask_jobs column."""
    import inspect

    assert (
        inspect.signature(projects_route._schedule_group_reply)
        .parameters["pinned_skill"].default is None
    )
    assert (
        inspect.signature(projects_route._respond_as_group_agent)
        .parameters["pinned_skill"].default is None
    )
    # The resolver reads the source turn's own trigger text (no new column).
    resolver_src = inspect.getsource(projects_route._pin_from_source_turn)
    assert "_get_group_turn" in resolver_src
    assert 'startswith("/")' in resolver_src


# ── Group passes conversation history to the engine (connector parity) ──────
# The group surface must hand the prior turns to `qa_agent.answer` as
# `history` (recent-minus-trigger), exactly like the private surface — without
# it, every history-dependent router/interceptor signal dies on group and a
# source-named follow-up ("what did they say?") loses its connector thread.
# `history or None` keeps the trigger-less degenerate path at None so a
# transcript-as-question is not ALSO rendered as history.


def test_group_answer_receives_history_recent_minus_trigger(
    tenant_client, isolated_settings, monkeypatch
):
    """AC2: a group reply with a human trigger passes `history` = recent turns
    EXCLUDING the trigger turn (the prior assistant turn only, here)."""
    t = tenant_client.make(slug="acme")
    project_id = _seed_project(t)
    conv = conversations_db.create_group_chat(project_id, t.user_id)
    conversations_db.post_group_turn(
        conv["id"], None, "Slack: the launch slipped to Q3.", role="assistant",
    )
    _stub_classify(monkeypatch)
    calls = _spy_answer(monkeypatch)

    resp = t.client.post(
        f"/v1/projects/{project_id}/group/turns",
        json={"content": "@Sprntly what did they say?"},
    )
    assert resp.status_code == 200, resp.text
    assert calls, "the mention must have produced a reply run"
    assert calls[-1]["history"] == [
        {"role": "assistant", "content": "Slack: the launch slipped to Q3."}
    ]


def test_group_triggerless_passes_history_none(
    tenant_client, isolated_settings, monkeypatch
):
    """AC2/AC3: a reply with NO human trigger (an assistant-only thread) passes
    `history=None` (`[] or None`), so the transcript-as-question is not also
    rendered as history — no double-count on the degenerate path."""
    t = tenant_client.make(slug="acme")
    project_id = _seed_project(t)
    conv = conversations_db.create_group_chat(project_id, t.user_id)
    conversations_db.post_group_turn(
        conv["id"], None, "System note: standup at 10.", role="assistant",
    )
    calls = _spy_answer(monkeypatch)

    asyncio.run(
        projects_route._respond_as_group_agent(
            project_id, conv["id"], _ctx(t.company_id, t.user_id),
            "mention", job_id=1, run_id="r",
        )
    )
    assert calls, "the group agent must have produced a reply run"
    assert calls[-1]["history"] is None


def test_group_no_double_count_in_prompt(
    tenant_client, isolated_settings, monkeypatch
):
    """AC3: the group question is the latest trigger content and history is
    recent-minus-trigger, so each recent turn's text appears EXACTLY ONCE across
    `_render_history(history)` + question — the prior turn only in history, the
    trigger only in the question. (The former `_GROUP_TRANSCRIPT_AS_QUESTION`
    toggle was retired in the surface-collapse refactor; `test_project_answer_
    collapse.test_group_question_is_latest_turn_transcript_rides_scope` pins its
    absence and the collapsed input shape.)"""
    t = tenant_client.make(slug="acme")
    project_id = _seed_project(t)
    conv = conversations_db.create_group_chat(project_id, t.user_id)
    conversations_db.post_group_turn(
        conv["id"], None, "The Q3 launch is on track.", role="assistant",
    )
    _stub_classify(monkeypatch)
    calls = _spy_answer(monkeypatch)

    resp = t.client.post(
        f"/v1/projects/{project_id}/group/turns",
        json={"content": "@Sprntly anything else I should know?"},
    )
    assert resp.status_code == 200, resp.text
    assert calls
    history = calls[-1]["history"]
    question = calls[-1]["question"]
    rendered = qa._render_history(history)

    # Prior turn: once in history, never in the question.
    assert rendered.count("The Q3 launch is on track.") == 1
    assert "The Q3 launch is on track." not in question
    # Trigger turn: in the question, never re-rendered into history.
    assert "@Sprntly anything else I should know?" in question
    assert "@Sprntly anything else I should know?" not in rendered
