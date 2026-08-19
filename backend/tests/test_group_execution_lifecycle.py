"""Group-turn run-status surfacing + idempotent send (deterministic).

SCOPE NOTE (post-rewrite): the group answer path was collapsed into the shared
`qa_agent` + `/v1/ask` + `ask_job_runner` lifecycle. `post_group_turn_route` is
now "mount-not-scheduler" — it persists + broadcasts the human turn and does NOT
schedule a reply or own a retry route. The reply is produced by the shared
`/v1/ask` execution job (the SAME primitive main/private use), so the primitive's
own behaviour (generating row, error_class, capture/report/promote/ingest
inheritance, `active_project_id` isolation, retry idempotency) is covered by the
primitive's tests, not by group-specific duplicates. The group assistant-reply
BROADCAST is covered by `test_realtime_publish.py`; the group no-fabrication
guard by `test_group_trigger_and_no_fabrication.py`.

What remains here is the group-SPECIFIC surfacing that is still live:
  * `list_group_turns` attaches the source turn's latest ask_jobs run status +
    error_class onto the group-turn DTO (AC15);
  * the group-turn DTO whitelist carries `run_status` / `error_class` (AC16);
  * the partial-unique `ask_jobs_active_attempt_uidx` enforces one live attempt
    per source turn (AC12/AC18);
  * a duplicate `client_message_id` send replays the original turn idempotently
    (DEFECT-2: no 500, no second human turn).

The real-DB migration proof is in `test_ask_jobs_active_attempt_migration.py`.
"""
from __future__ import annotations

import pytest

import app.routes.projects as projects_route
from app.db import conversations as conversations_db
from app.db.workspaces import ensure_default_workspace


def _seed_project(t, *, name="Group lifecycle") -> int:
    from app.db import projects as projects_db

    ws_id = ensure_default_workspace(t.company_id)["id"]
    return projects_db.create_project(
        company_id=t.company_id, workspace_id=ws_id, name=name, created_by=t.user_id,
    )["id"]


def _seed_group_turn(t, project_id) -> tuple[int, int]:
    conv = conversations_db.create_group_chat(project_id, t.user_id)
    turn = conversations_db.post_group_turn(conv["id"], t.user_id, "@Sprntly do it")
    return conv["id"], turn["id"]


# ────────────────────── status on read + DTO shape ─────────────────────────

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
    """The group POST accepts the execution-identity / SendCommand plumbing
    fields (`client_message_id`, `pinned_skill`, `attachments`) without error,
    and the returned DTO carries the send-identity key back to the poster.

    Retargeted from the pre-rewrite assertion on a scheduled `ask_jobs` run:
    the POST no longer mints a run (mount-not-scheduler), so the identity key
    now rides the persisted turn's DTO instead."""
    t = tenant_client.make(slug="acme")
    project_id = _seed_project(t)
    r = t.client.post(
        f"/v1/projects/{project_id}/group/turns",
        json={
            "content": "@Sprntly hi", "client_message_id": "cm-42",
            "pinned_skill": {"id": "x"}, "attachments": [{"name": "a"}],
        },
    )
    assert r.status_code == 200, r.text
    assert r.json()["client_message_id"] == "cm-42"


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


def test_duplicate_client_message_id_is_idempotent_on_send(
    tenant_client, isolated_settings, monkeypatch
):
    """DEFECT-2 symptom test: submitting the SAME client_message_id twice must
    NOT 500 and must NOT post a second human turn — the duplicate replays the
    original turn idempotently.

    Retargeted: the pre-rewrite version also asserted "no 2nd run" — the POST no
    longer mints a run at all (mount-not-scheduler), so that clause is dropped;
    the turn-level idempotency this guard exists for is unchanged."""
    t = tenant_client.make(slug="acme")
    project_id = _seed_project(t)
    body = {"content": "@Sprntly hi", "client_message_id": "dup-1"}

    r1 = t.client.post(f"/v1/projects/{project_id}/group/turns", json=body)
    assert r1.status_code == 200, r1.text
    r2 = t.client.post(f"/v1/projects/{project_id}/group/turns", json=body)
    assert r2.status_code == 200, r2.text  # no 500 on the duplicate

    assert r1.json()["id"] == r2.json()["id"], "the duplicate replays the same turn"
    conv = conversations_db.get_group_chat(project_id)
    turns = conversations_db.list_group_turns(conv["id"])
    human = [x for x in turns if x["role"] == "user"]
    assert len(human) == 1, "a duplicate client_message_id must not post a 2nd human turn"
