"""Group chat routed through the SHARED `run_execution_job` lifecycle.

Deterministic (FakeSupabaseClient, no real LLM) proofs that the group send
*inherits* the execution lifecycle by using the same primitive main/private
use — not a status-column wrapper:

  * a `generating` ask_jobs row is inserted SYNCHRONOUSLY before the reply is
    backgrounded (AC5);
  * the reply runs through the primitive — success posts a turn + flips the
    row `ready`; a forced failure writes `status='error'` + `error_class` and
    fabricates NO turn (the old log-only `except` is gone) (AC6);
  * report/promote/ingest inheritance via `on_committed` (AC7);
  * a group run leaves `active_project_id()` unset (AC8);
  * idempotent retry: 409 while live, 422 on recorded side effects, 202 +
    new run_id/attempt when clean, no duplicate side effects (AC12–AC14);
  * run-status on the load/poll read + DTO keys + payload plumbing
    (AC15–AC17);
  * the retry-claim partial-unique enforces one live attempt (AC12/AC18).

The real-DB migration proof and the real-LLM behaviour arm live in
`test_ask_jobs_active_attempt_migration.py` and
`test_group_execution_lifecycle_live.py`.
"""
from __future__ import annotations

from types import SimpleNamespace

import pytest

import app.ask_runner as ask_runner
import app.routes.projects as projects_route
from app.db import conversations as conversations_db
from app.db import project_delegations as project_delegations_db
from app.db.client import require_client
from app.db.workspaces import ensure_default_workspace


def _seed_project(t, *, name="Group lifecycle") -> int:
    from app.db import projects as projects_db

    ws_id = ensure_default_workspace(t.company_id)["id"]
    return projects_db.create_project(
        company_id=t.company_id, workspace_id=ws_id, name=name, created_by=t.user_id,
    )["id"]


def _ctx(t) -> SimpleNamespace:
    return SimpleNamespace(
        company_id=t.company_id, workspace_id=ensure_default_workspace(t.company_id)["id"],
        user_id=t.user_id, user_email=None,
    )


def _group_runs(conversation_id: int) -> list[dict]:
    return (
        require_client()
        .table("ask_jobs")
        .select("*")
        .eq("conversation_id", conversation_id)
        .eq("kind", "project_group")
        .execute()
        .data
        or []
    )


def _stub_answer(monkeypatch, reply="Sure, here's the answer."):
    monkeypatch.setattr(projects_route, "resolve_chat_intent", lambda *a, **kw: {"intent": "answer"})
    monkeypatch.setattr(
        projects_route.qa_agent, "answer",
        lambda **kw: {"answer": reply, "citations": []},
    )


# ─────────────────────────── AC5 — sync insert ─────────────────────────────

def test_group_send_inserts_generating_row_before_background(
    tenant_client, isolated_settings, monkeypatch
):
    t = tenant_client.make(slug="acme")
    project_id = _seed_project(t)
    monkeypatch.setattr(projects_route, "resolve_chat_intent", lambda *a, **kw: {"intent": "answer"})

    # The reply is a no-op, so the row can only exist because the insert ran
    # SYNCHRONOUSLY in `_schedule_group_reply` BEFORE the background reply.
    async def _noop_reply(*a, **kw):
        return None

    monkeypatch.setattr(projects_route, "_respond_as_group_agent", _noop_reply)

    r = t.client.post(
        f"/v1/projects/{project_id}/group/turns",
        json={"content": "@Sprntly hello", "client_message_id": "cm-1"},
    )
    assert r.status_code == 200, r.text
    turn_id = r.json()["id"]

    conv = conversations_db.get_group_chat(project_id)
    runs = _group_runs(conv["id"])
    assert len(runs) == 1
    row = runs[0]
    assert row["status"] == "generating"  # reply no-op'd, so never completed
    assert row["project_id"] == project_id
    assert row["source_turn_id"] == turn_id
    assert row["run_id"]
    assert row["client_message_id"] == "cm-1"


# ─────────────────────── AC6 — through the primitive ───────────────────────

def test_group_reply_runs_through_primitive_success(
    tenant_client, isolated_settings, monkeypatch
):
    t = tenant_client.make(slug="acme")
    project_id = _seed_project(t)
    _stub_answer(monkeypatch, reply="Reply text")

    r = t.client.post(
        f"/v1/projects/{project_id}/group/turns", json={"content": "@Sprntly hi"},
    )
    assert r.status_code == 200, r.text
    conv = conversations_db.get_group_chat(project_id)
    turns = conversations_db.list_group_turns(conv["id"])
    assistant = [x for x in turns if x["role"] == "assistant"]
    assert len(assistant) == 1 and assistant[0]["content"] == "Reply text"
    runs = _group_runs(conv["id"])
    assert len(runs) == 1 and runs[0]["status"] == "ready"


def test_group_reply_failure_writes_error_and_error_class(
    tenant_client, isolated_settings, monkeypatch
):
    """MUTATION (AC6): a forced failure now PERSISTS `status='error'` +
    `error_class` and fabricates NO assistant turn — the old
    `except`-that-only-logs no longer swallows. Reinstating a swallow would
    leave the row absent/generating (RED)."""
    t = tenant_client.make(slug="acme")
    project_id = _seed_project(t)
    monkeypatch.setattr(projects_route, "resolve_chat_intent", lambda *a, **kw: {"intent": "answer"})

    def _boom(**kw):
        raise RuntimeError("model exploded: secret detail")

    monkeypatch.setattr(projects_route.qa_agent, "answer", _boom)

    r = t.client.post(
        f"/v1/projects/{project_id}/group/turns", json={"content": "@Sprntly hi"},
    )
    assert r.status_code == 200, r.text  # the human turn still persisted
    conv = conversations_db.get_group_chat(project_id)
    turns = conversations_db.list_group_turns(conv["id"])
    assert [x for x in turns if x["role"] == "assistant"] == [], "no fabricated turn on failure"
    runs = _group_runs(conv["id"])
    assert len(runs) == 1
    assert runs[0]["status"] == "error"
    assert runs[0]["error_class"] == "app"
    # The raw exception text is NOT exposed on the read DTO.
    src_turn = next(x for x in turns if x["id"] == runs[0]["source_turn_id"])
    assert "secret detail" not in str(src_turn)


def test_group_inherits_capture_report_promote_ingest(
    tenant_client, isolated_settings, monkeypatch
):
    t = tenant_client.make(slug="acme")
    project_id = _seed_project(t)
    _stub_answer(monkeypatch)
    fired: list[str] = []
    monkeypatch.setattr(projects_route, "capture_report", lambda *a, **kw: fired.append("capture"))
    monkeypatch.setattr(projects_route, "maybe_promote_turn", lambda *a, **kw: fired.append("promote"))
    monkeypatch.setattr(projects_route, "maybe_ingest_status", lambda *a, **kw: fired.append("ingest"))

    r = t.client.post(
        f"/v1/projects/{project_id}/group/turns", json={"content": "@Sprntly hi"},
    )
    assert r.status_code == 200, r.text
    assert fired == ["capture", "promote", "ingest"]


def test_group_run_leaves_active_project_id_unset(
    tenant_client, isolated_settings, monkeypatch
):
    t = tenant_client.make(slug="acme")
    project_id = _seed_project(t)
    monkeypatch.setattr(projects_route, "resolve_chat_intent", lambda *a, **kw: {"intent": "answer"})
    seen: list = []

    def _answer(**kw):
        seen.append(ask_runner.active_project_id())
        return {"answer": "ok", "citations": []}

    monkeypatch.setattr(projects_route.qa_agent, "answer", _answer)
    r = t.client.post(
        f"/v1/projects/{project_id}/group/turns", json={"content": "@Sprntly hi"},
    )
    assert r.status_code == 200, r.text
    assert seen == [None], "a group run must never set active_project_id"


# ─────────────────────────────── retry ─────────────────────────────────────

def _seed_group_turn(t, project_id) -> tuple[int, int]:
    conv = conversations_db.create_group_chat(project_id, t.user_id)
    turn = conversations_db.post_group_turn(conv["id"], t.user_id, "@Sprntly do it")
    return conv["id"], turn["id"]


def test_retry_refuses_while_attempt_live(tenant_client, isolated_settings, monkeypatch):
    from app.db.asks import start_ask_job

    t = tenant_client.make(slug="acme")
    project_id = _seed_project(t)
    conv_id, turn_id = _seed_group_turn(t, project_id)
    # A live (generating) attempt for this turn.
    start_ask_job(
        company_id=t.company_id, dataset="acme", question="",
        conversation_id=conv_id, kind="project_group", project_id=project_id,
        source_turn_id=turn_id, run_id="live-run",
    )
    r = t.client.post(f"/v1/projects/{project_id}/group/turns/{turn_id}/retry")
    assert r.status_code == 409, r.text


def test_retry_blocked_when_side_effects_recorded(
    tenant_client, isolated_settings, monkeypatch
):
    from app.db.asks import fail_ask_job, start_ask_job

    t = tenant_client.make(slug="acme")
    project_id = _seed_project(t)
    conv_id, turn_id = _seed_group_turn(t, project_id)
    job = start_ask_job(
        company_id=t.company_id, dataset="acme", question="",
        conversation_id=conv_id, kind="project_group", project_id=project_id,
        source_turn_id=turn_id, run_id="r0",
    )
    fail_ask_job(job, "TypeError: boom", "app")
    # A delegation recorded for this turn ⇒ NOT auto-retryable.
    project_delegations_db.record_delegation(
        project_id=project_id, assigner_user_id=t.user_id, assignee_user_id=t.user_id,
        task_summary="ship it", source_conversation_id=conv_id, source_turn_id=turn_id,
        delivered_conversation_id=conv_id, delivered_turn_id=turn_id,
    )
    r = t.client.post(f"/v1/projects/{project_id}/group/turns/{turn_id}/retry")
    assert r.status_code == 422, r.text
    assert r.json()["detail"] == {"error": "resend_as_new_turn"}


def test_retry_side_effect_seeded_flips_to_422_is_red(
    tenant_client, isolated_settings, monkeypatch
):
    """MUTATION (AC13): with NO side effect the retry is granted (202); seeding
    a delegation for the turn flips the SAME retry to 422."""
    from app.db.asks import fail_ask_job, start_ask_job

    t = tenant_client.make(slug="acme")
    project_id = _seed_project(t)
    _stub_answer(monkeypatch)
    conv_id, turn_id = _seed_group_turn(t, project_id)
    job = start_ask_job(
        company_id=t.company_id, dataset="acme", question="",
        conversation_id=conv_id, kind="project_group", project_id=project_id,
        source_turn_id=turn_id, run_id="r0",
    )
    fail_ask_job(job, "TypeError: boom", "app")

    # Clean ⇒ granted.
    granted = t.client.post(f"/v1/projects/{project_id}/group/turns/{turn_id}/retry")
    assert granted.status_code == 202, granted.text

    # Fail that new attempt, seed a delegation, retry again ⇒ 422.
    runs = _group_runs(conv_id)
    live = [x for x in runs if x["status"] == "generating"]
    for x in live:
        fail_ask_job(x["id"], "TypeError: boom", "app")
    project_delegations_db.record_delegation(
        project_id=project_id, assigner_user_id=t.user_id, assignee_user_id=t.user_id,
        task_summary="ship it", source_conversation_id=conv_id, source_turn_id=turn_id,
        delivered_conversation_id=conv_id, delivered_turn_id=turn_id,
    )
    blocked = t.client.post(f"/v1/projects/{project_id}/group/turns/{turn_id}/retry")
    assert blocked.status_code == 422, blocked.text


def test_retry_grants_new_run_id_and_attempt_when_clean(
    tenant_client, isolated_settings, monkeypatch
):
    from app.db.asks import fail_ask_job, start_ask_job

    t = tenant_client.make(slug="acme")
    project_id = _seed_project(t)
    _stub_answer(monkeypatch)
    conv_id, turn_id = _seed_group_turn(t, project_id)
    job = start_ask_job(
        company_id=t.company_id, dataset="acme", question="",
        conversation_id=conv_id, kind="project_group", project_id=project_id,
        source_turn_id=turn_id, run_id="r0", attempt=1,
    )
    fail_ask_job(job, "TypeError: boom", "app")
    r = t.client.post(f"/v1/projects/{project_id}/group/turns/{turn_id}/retry")
    assert r.status_code == 202, r.text
    body = r.json()
    assert body["run_id"] != "r0"
    assert body["attempt"] == 2


def test_two_retries_no_duplicate_side_effects(
    tenant_client, isolated_settings, monkeypatch
):
    """AC14: two sequential granted retries of a clean (no-delegation) failed
    run don't manufacture delegations — the plain-answer body creates none."""
    from app.db.asks import fail_ask_job, start_ask_job

    t = tenant_client.make(slug="acme")
    project_id = _seed_project(t)
    _stub_answer(monkeypatch)
    conv_id, turn_id = _seed_group_turn(t, project_id)
    job = start_ask_job(
        company_id=t.company_id, dataset="acme", question="",
        conversation_id=conv_id, kind="project_group", project_id=project_id,
        source_turn_id=turn_id, run_id="r0",
    )
    fail_ask_job(job, "TypeError: boom", "app")

    for _ in range(2):
        r = t.client.post(f"/v1/projects/{project_id}/group/turns/{turn_id}/retry")
        assert r.status_code == 202, r.text
        for x in _group_runs(conv_id):
            if x["status"] == "generating":
                fail_ask_job(x["id"], "TypeError: boom", "app")

    assert project_delegations_db.has_delegation_for_source_turn(turn_id) is False


# ────────────────────── status on read + payload ───────────────────────────

def test_list_group_turns_attaches_run_status_on_failure(
    tenant_client, isolated_settings, monkeypatch
):
    from app.db.asks import fail_ask_job, start_ask_job

    t = tenant_client.make(slug="acme")
    project_id = _seed_project(t)
    conv_id, turn_id = _seed_group_turn(t, project_id)
    job = start_ask_job(
        company_id=t.company_id, dataset="acme", question="",
        conversation_id=conv_id, kind="project_group", project_id=project_id,
        source_turn_id=turn_id, run_id="r0", attempt=1,
    )
    fail_ask_job(job, "TypeError: boom", "timeout")

    turns = conversations_db.list_group_turns(conv_id)
    src = next(x for x in turns if x["id"] == turn_id)
    assert src["run_status"] == "failed"
    assert src["error_class"] == "timeout"


def test_run_status_mapping_and_latest_attempt(
    tenant_client, isolated_settings, monkeypatch
):
    from app.db.asks import complete_ask_job, fail_ask_job, start_ask_job

    t = tenant_client.make(slug="acme")
    project_id = _seed_project(t)
    conv_id, turn_id = _seed_group_turn(t, project_id)
    # attempt 1 failed; attempt 2 (latest) succeeded → status should read 'done'.
    j1 = start_ask_job(
        company_id=t.company_id, dataset="acme", question="", conversation_id=conv_id,
        kind="project_group", project_id=project_id, source_turn_id=turn_id,
        run_id="r1", attempt=1,
    )
    fail_ask_job(j1, "TypeError: boom", "app")
    j2 = start_ask_job(
        company_id=t.company_id, dataset="acme", question="", conversation_id=conv_id,
        kind="project_group", project_id=project_id, source_turn_id=turn_id,
        run_id="r2", attempt=2,
    )
    complete_ask_job(j2, {"answer": "ok"})
    turns = conversations_db.list_group_turns(conv_id)
    src = next(x for x in turns if x["id"] == turn_id)
    assert src["run_status"] == "done", "the LATEST (max attempt) run wins"


def test_group_dto_keys_include_run_status_error_class():
    assert "run_status" in projects_route._GROUP_TURN_DTO_KEYS
    assert "error_class" in projects_route._GROUP_TURN_DTO_KEYS


def test_post_group_turn_request_accepts_new_fields(
    tenant_client, isolated_settings, monkeypatch
):
    t = tenant_client.make(slug="acme")
    project_id = _seed_project(t)
    _stub_answer(monkeypatch)
    r = t.client.post(
        f"/v1/projects/{project_id}/group/turns",
        json={
            "content": "@Sprntly hi", "client_message_id": "cm-42",
            "pinned_skill": {"id": "x"}, "attachments": [{"name": "a"}],
        },
    )
    assert r.status_code == 200, r.text
    conv = conversations_db.get_group_chat(project_id)
    runs = _group_runs(conv["id"])
    assert runs[0]["client_message_id"] == "cm-42"


def test_active_attempt_index_enforces_one_live_attempt(
    tenant_client, isolated_settings
):
    """AC12/AC18: a second `generating` insert for the SAME source_turn_id
    violates `ask_jobs_active_attempt_uidx`."""
    import sqlite3

    from app.db.asks import start_ask_job

    t = tenant_client.make(slug="acme")
    project_id = _seed_project(t)
    conv_id, turn_id = _seed_group_turn(t, project_id)
    start_ask_job(
        company_id=t.company_id, dataset="acme", question="", conversation_id=conv_id,
        kind="project_group", project_id=project_id, source_turn_id=turn_id, run_id="r1",
    )
    with pytest.raises(sqlite3.IntegrityError):
        start_ask_job(
            company_id=t.company_id, dataset="acme", question="", conversation_id=conv_id,
            kind="project_group", project_id=project_id, source_turn_id=turn_id, run_id="r2",
        )
