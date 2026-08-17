"""Real local-Supabase round-trip proof for `delegation_followup_sends` (the
idempotent per-company send-ledger the autonomous task follow-up sweep
writes to) and for `db.delegation_followups.list_due_followups` (the
cheap SQL pre-filter the sweep reads, which needs the real
`v_delegation_status` view — a `FakeSupabaseClient` has no SQL engine
behind it and cannot evaluate a view, enforce a FK/unique constraint, or
look up an RLS-policy catalog entry).

Mirrors `test_delegation_followups.py`'s fixture shape (same non-loopback
guard, same reused-fixture-row convention, same module-scope company/
workspace/member fixture) for the identical reason.

Run it with:

    RUN_DELEGATION_FOLLOWUP_SENDS_ROUNDTRIP=1 \\
        pytest tests/test_delegation_followup_sends.py -m integration
"""
from __future__ import annotations

import os
import pathlib
import shutil
import subprocess
import uuid
from datetime import datetime, timedelta, timezone

import pytest

_RUN_LIVE = os.getenv("RUN_DELEGATION_FOLLOWUP_SENDS_ROUNDTRIP") == "1"

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(
        not _RUN_LIVE,
        reason=(
            "needs a real local Supabase — set RUN_DELEGATION_FOLLOWUP_SENDS_ROUNDTRIP=1 "
            "with SUPABASE_URL/SUPABASE_SERVICE_ROLE_KEY pointed at the local rig "
            "and the delegation_followup_sends migration applied"
        ),
    ),
]

_MIGRATIONS_DIR = pathlib.Path(__file__).resolve().parents[2] / "supabase" / "migrations"
_MIGRATION_FILE = "20260814150000_delegation_followup_sends.sql"
_DB_CONTAINER = os.getenv("PROJECTS_SCHEMA_TEST_DB_CONTAINER", "supabase_db_Sprntly")


def _client():
    from app.config import settings
    from supabase import create_client

    url = settings.supabase_url
    assert "127.0.0.1" in url or "localhost" in url, (
        "refusing to run the live delegation-followup-sends round-trip against a "
        f"non-loopback SUPABASE_URL ({url!r}) — this test mutates real rows"
    )
    assert settings.supabase_service_role_key, "SUPABASE_SERVICE_ROLE_KEY is not set"
    return create_client(url, settings.supabase_service_role_key)


@pytest.fixture(scope="module")
def sb():
    return _client()


@pytest.fixture(scope="module")
def fixture_ids(sb):
    """A real (company, workspace, user, user) tuple already in the rig."""
    companies = sb.table("companies").select("id").limit(1).execute().data
    assert companies, "no company row in the local rig — seed one before running this test"
    company_id = companies[0]["id"]

    workspaces = (
        sb.table("workspaces").select("id").eq("company_id", company_id).limit(1).execute().data
    )
    assert workspaces, f"no workspace for company {company_id}"
    workspace_id = workspaces[0]["id"]

    members = (
        sb.table("company_members")
        .select("user_id")
        .eq("company_id", company_id)
        .limit(2)
        .execute()
        .data
    )
    assert len(members) >= 2, f"need >=2 company_members rows for company {company_id}"

    return {
        "company_id": company_id,
        "workspace_id": workspace_id,
        "assigner": members[0]["user_id"],
        "assignee": members[1]["user_id"],
    }


@pytest.fixture
def project(sb, fixture_ids):
    row = (
        sb.table("projects")
        .insert(
            {
                "company_id": fixture_ids["company_id"],
                "workspace_id": fixture_ids["workspace_id"],
                "name": f"delegation-followup-sends-roundtrip-{uuid.uuid4().hex[:8]}",
                "created_by": fixture_ids["assigner"],
            }
        )
        .execute()
        .data[0]
    )
    yield row
    sb.table("projects").delete().eq("id", row["id"]).execute()


def _make_delegation(sb, fixture_ids, project, *, task_summary: str) -> dict:
    conv = (
        sb.table("conversations")
        .insert(
            {
                "company_id": fixture_ids["company_id"],
                "user_id": fixture_ids["assignee"],
                "project_id": project["id"],
                "kind": "individual",
            }
        )
        .execute()
        .data[0]
    )
    turn = (
        sb.table("conversation_turns")
        .insert(
            {
                "conversation_id": conv["id"],
                "role": "assistant",
                "content": "delivered brief turn",
                "author_user_id": fixture_ids["assignee"],
            }
        )
        .execute()
        .data[0]
    )
    from app.db.project_delegations import record_delegation

    row = record_delegation(
        project_id=project["id"],
        assigner_user_id=fixture_ids["assigner"],
        assignee_user_id=fixture_ids["assignee"],
        task_summary=task_summary,
        source_conversation_id=None,
        source_turn_id=None,
        delivered_conversation_id=conv["id"],
        delivered_turn_id=turn["id"],
    )
    row["_conv_id"] = conv["id"]
    return row


@pytest.fixture
def delegation(sb, fixture_ids, project):
    row = _make_delegation(sb, fixture_ids, project, task_summary="followup-sends round-trip fixture")
    yield row
    sb.table("project_delegations").delete().eq("id", row["id"]).execute()
    sb.table("conversations").delete().eq("id", row["_conv_id"]).execute()


# ── Migration apply / idempotency + shape (AC1) ─────────────────────────


def test_migration_double_apply_idempotent():
    if shutil.which("docker") is None:
        pytest.skip("docker not on PATH — cannot re-apply the migration for the idempotency proof")

    path = _MIGRATIONS_DIR / _MIGRATION_FILE
    assert path.is_file(), f"migration file missing: {path}"
    for _ in range(2):
        with path.open("rb") as f:
            result = subprocess.run(
                [
                    "docker", "exec", "-i", _DB_CONTAINER,
                    "psql", "-U", "postgres", "-d", "postgres", "-v", "ON_ERROR_STOP=1",
                ],
                stdin=f,
                capture_output=True,
                timeout=30,
            )
        assert result.returncode == 0, (
            f"applying {_MIGRATION_FILE} was not idempotent:\n"
            f"stdout: {result.stdout.decode(errors='replace')}\n"
            f"stderr: {result.stderr.decode(errors='replace')}"
        )

    table_count = subprocess.run(
        [
            "docker", "exec", _DB_CONTAINER,
            "psql", "-U", "postgres", "-d", "postgres", "-tAc",
            "select count(*) from pg_tables where tablename='delegation_followup_sends';",
        ],
        capture_output=True, timeout=15,
    )
    assert table_count.returncode == 0, table_count.stderr.decode(errors="replace")
    assert table_count.stdout.decode().strip() == "1"

    policy_result = subprocess.run(
        [
            "docker", "exec", _DB_CONTAINER,
            "psql", "-U", "postgres", "-d", "postgres", "-tAc",
            "select policyname from pg_policies where tablename='delegation_followup_sends';",
        ],
        capture_output=True, timeout=15,
    )
    assert policy_result.returncode == 0, policy_result.stderr.decode(errors="replace")
    assert policy_result.stdout.decode().strip() == "srv_delegation_followup_sends"


def test_no_workspace_id_column_rls_enabled(sb, delegation):
    """AC1 — `workspace_id` is absent; RLS is enabled with exactly the one
    `srv_delegation_followup_sends` server-role policy; `unique
    (delegation_id, check_key, channel)` is enforced (proven by AC2 below);
    `company_id`/`assignee_user_id` columns exist (proven by the insert
    round-trip below)."""
    from postgrest.exceptions import APIError

    with pytest.raises(APIError) as exc:
        sb.table("delegation_followup_sends").select("workspace_id").limit(1).execute()
    assert getattr(exc.value, "code", None) in ("42703", "PGRST204", "PGRST116") or (
        "workspace_id" in str(exc.value).lower() or "column" in str(exc.value).lower()
    )

    rls_result = subprocess.run(
        [
            "docker", "exec", _DB_CONTAINER,
            "psql", "-U", "postgres", "-d", "postgres", "-tAc",
            "select relrowsecurity from pg_class where relname='delegation_followup_sends';",
        ],
        capture_output=True, timeout=15,
    )
    assert rls_result.returncode == 0, rls_result.stderr.decode(errors="replace")
    assert rls_result.stdout.decode().strip() == "t"


# ── Ledger round-trip (AC1, AC2) ─────────────────────────────────────────


def test_record_send_idempotent_on_unique(sb, fixture_ids, delegation):
    """AC2 — a duplicate `(delegation_id, check_key, channel)` insert
    raises (caught by the caller as already-sent); the ledger holds
    exactly one row; `send_exists` is True after the first."""
    from postgrest.exceptions import APIError

    from app.db.delegation_followup_sends import record_send, send_exists

    check_key = datetime(2026, 8, 21, 9, 0, tzinfo=timezone.utc).isoformat()
    row = record_send(
        delegation_id=delegation["id"],
        company_id=fixture_ids["company_id"],
        assignee_user_id=fixture_ids["assignee"],
        check_key=check_key,
        channel="dm",
    )
    assert row["delegation_id"] == delegation["id"]
    assert row["channel"] == "dm"
    assert row["status"] == "sent"
    assert send_exists(delegation["id"], check_key, "dm") is True

    with pytest.raises(APIError):
        record_send(
            delegation_id=delegation["id"],
            company_id=fixture_ids["company_id"],
            assignee_user_id=fixture_ids["assignee"],
            check_key=check_key,
            channel="dm",
        )

    rows = (
        sb.table("delegation_followup_sends")
        .select("id")
        .eq("delegation_id", delegation["id"])
        .eq("check_key", check_key)
        .eq("channel", "dm")
        .execute()
        .data
    )
    assert len(rows) == 1, "the ledger must hold exactly one row after the duplicate insert"


def test_sends_for_person_isolated(sb, fixture_ids, project):
    """AC3 — counts only the target assignee's rows, excluding a different
    assignee's rows (and, by the same `assignee_user_id`-only filter, a
    different company's rows for a different assignee)."""
    from app.db.delegation_followup_sends import record_send, sends_for_person_since

    target = _make_delegation(sb, fixture_ids, project, task_summary="isolation target")
    other = _make_delegation(sb, fixture_ids, project, task_summary="isolation other")
    try:
        # A second, distinct "assignee" id standing in for a different
        # person/company's row — isolation only needs the ids to differ.
        other_assignee = str(uuid.uuid4())

        since = datetime.now(timezone.utc) - timedelta(hours=1)
        record_send(
            delegation_id=target["id"], company_id=fixture_ids["company_id"],
            assignee_user_id=fixture_ids["assignee"],
            check_key="k1", channel="dm",
        )
        record_send(
            delegation_id=other["id"], company_id=fixture_ids["company_id"],
            assignee_user_id=other_assignee,
            check_key="k1", channel="dm",
        )

        rows = sends_for_person_since(fixture_ids["assignee"], since)
        ids = {r["delegation_id"] for r in rows}
        assert target["id"] in ids
        assert other["id"] not in ids
    finally:
        sb.table("project_delegations").delete().eq("id", target["id"]).execute()
        sb.table("conversations").delete().eq("id", target["_conv_id"]).execute()
        sb.table("project_delegations").delete().eq("id", other["id"]).execute()
        sb.table("conversations").delete().eq("id", other["_conv_id"]).execute()


def test_sends_cascade_on_delegation_delete(sb, fixture_ids, project):
    """AC1 — deleting the parent `project_delegations` row cascades its
    `delegation_followup_sends` rows."""
    from app.db.delegation_followup_sends import record_send

    deleg = _make_delegation(sb, fixture_ids, project, task_summary="cascade proof")
    record_send(
        delegation_id=deleg["id"], company_id=fixture_ids["company_id"],
        assignee_user_id=fixture_ids["assignee"], check_key="k1", channel="dm",
    )
    rows_before = (
        sb.table("delegation_followup_sends").select("id").eq("delegation_id", deleg["id"]).execute().data
    )
    assert len(rows_before) == 1

    sb.table("project_delegations").delete().eq("id", deleg["id"]).execute()

    rows_after = (
        sb.table("delegation_followup_sends").select("id").eq("delegation_id", deleg["id"]).execute().data
    )
    assert rows_after == []
    sb.table("conversations").delete().eq("id", deleg["_conv_id"]).execute()


# ── list_due_followups pre-filter (AC4) ───────────────────────────────────


def test_list_due_excludes_muted_future_cleared_completed(sb, fixture_ids, project):
    """AC4 — returns a task whose `next_check_in <= now`, `muted=false`,
    and derived status in OPEN_STATES; excludes a muted task, a
    future-scheduled task, a `cleared` task, and a `completed` task."""
    from app.db.delegation_followups import list_due_followups, upsert_followup
    from app.db.delegation_events import record_event

    now = datetime.now(timezone.utc)
    past = now - timedelta(hours=1)
    future = now + timedelta(hours=1)

    due_open = _make_delegation(sb, fixture_ids, project, task_summary="due open")
    muted = _make_delegation(sb, fixture_ids, project, task_summary="due but muted")
    not_yet_due = _make_delegation(sb, fixture_ids, project, task_summary="not yet due")
    cleared = _make_delegation(sb, fixture_ids, project, task_summary="due but cleared")
    completed = _make_delegation(sb, fixture_ids, project, task_summary="due but completed")

    made = [due_open, muted, not_yet_due, cleared, completed]
    try:
        upsert_followup(due_open["id"], next_check_in=past)
        upsert_followup(muted["id"], next_check_in=past, muted=True)
        upsert_followup(not_yet_due["id"], next_check_in=future)
        upsert_followup(cleared["id"], next_check_in=past)
        record_event(delegation_id=cleared["id"], event="cleared", actor_user_id=fixture_ids["assigner"])
        upsert_followup(completed["id"], next_check_in=past)
        record_event(delegation_id=completed["id"], event="completed", actor_user_id=fixture_ids["assignee"])

        due = list_due_followups(now)
        due_ids = {row["delegation_id"] for row in due}

        assert due_open["id"] in due_ids
        assert muted["id"] not in due_ids
        assert not_yet_due["id"] not in due_ids
        assert cleared["id"] not in due_ids
        assert completed["id"] not in due_ids

        due_row = next(row for row in due if row["delegation_id"] == due_open["id"])
        assert due_row["status"] == "assigned"
        assert due_row["project_id"] == project["id"]
        assert due_row["assignee_user_id"] == fixture_ids["assignee"]
        assert due_row["assigner_user_id"] == fixture_ids["assigner"]
    finally:
        for row in made:
            sb.table("project_delegations").delete().eq("id", row["id"]).execute()
            sb.table("conversations").delete().eq("id", row["_conv_id"]).execute()
