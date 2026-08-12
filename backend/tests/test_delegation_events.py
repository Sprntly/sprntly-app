"""Real local-Supabase round-trip proof for `delegation_events` +
`v_delegation_status` — the accountability ledger's spine.

Mirrors `test_project_delegations.py`'s own pattern (same fixture shape,
same non-loopback guard, same reused-fixture-row convention) for the
identical reason: `FakeSupabaseClient` is an in-memory store with no real
SQL engine behind it, so it cannot enforce a CHECK constraint, an
`on delete cascade`, a real index/RLS-policy catalog lookup, or evaluate a
`left join lateral` view. Those are exactly the invariants this migration
adds, so proving them needs the real thing: a real local Postgres,
through the real supabase-py client, over real HTTP via PostgREST.

Why it is not in CI. Neither CI lane runs a Postgres/Supabase service, and
`delegation_events`/`v_delegation_status` do not exist without applying
the migration first. This file skips cleanly wherever the ingredients are
absent, and runs where they are present: a local dev rig with
`supabase start` running and the migration applied.

Run it with:

    RUN_DELEGATION_EVENTS_ROUNDTRIP=1 \\
        pytest tests/test_delegation_events.py -m integration

It reads real fixture rows already in the local rig (an existing company,
its workspace, and two of its members) rather than minting new
`auth.users` rows, exactly like `test_project_delegations.py`. Everything
this file itself creates (projects, conversations, delegations, events) is
deleted in fixture teardown; the reused company/workspace/user rows are
read, never mutated.
"""
from __future__ import annotations

import os
import pathlib
import shutil
import subprocess
import uuid

import pytest

_RUN_LIVE = os.getenv("RUN_DELEGATION_EVENTS_ROUNDTRIP") == "1"

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(
        not _RUN_LIVE,
        reason=(
            "needs a real local Supabase — set RUN_DELEGATION_EVENTS_ROUNDTRIP=1 "
            "with SUPABASE_URL/SUPABASE_SERVICE_ROLE_KEY pointed at the local rig "
            "and the delegation_events migration applied"
        ),
    ),
]

_MIGRATIONS_DIR = pathlib.Path(__file__).resolve().parents[2] / "supabase" / "migrations"
_MIGRATION_FILE = "20260812120100_delegation_events.sql"
_DB_CONTAINER = os.getenv("PROJECTS_SCHEMA_TEST_DB_CONTAINER", "supabase_db_Sprntly")


def _client():
    from app.config import settings
    from supabase import create_client

    url = settings.supabase_url
    assert "127.0.0.1" in url or "localhost" in url, (
        "refusing to run the live delegation-events round-trip against a "
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
    """A fresh project row, deleted (cascade) at teardown."""
    row = (
        sb.table("projects")
        .insert(
            {
                "company_id": fixture_ids["company_id"],
                "workspace_id": fixture_ids["workspace_id"],
                "name": f"delegation-events-roundtrip-{uuid.uuid4().hex[:8]}",
                "created_by": fixture_ids["assigner"],
            }
        )
        .execute()
        .data[0]
    )
    yield row
    sb.table("projects").delete().eq("id", row["id"]).execute()


@pytest.fixture
def delegation(sb, fixture_ids, project):
    """A real `project_delegations` row (with a real delivered conversation
    + turn behind it) for events to hang off. Deleted at teardown (cascades
    its own `delegation_events` rows)."""
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
        task_summary="delegation-events round-trip fixture",
        source_conversation_id=None,
        source_turn_id=None,
        delivered_conversation_id=conv["id"],
        delivered_turn_id=turn["id"],
    )
    yield row
    sb.table("project_delegations").delete().eq("id", row["id"]).execute()
    sb.table("conversations").delete().eq("id", conv["id"]).execute()


# ── Migration apply / idempotency (AC2) ─────────────────────────────────


def test_migration_idempotent_double_apply():
    """Re-applying the actual committed SQL file against the real DB is a
    documented no-op — shells out to `psql` inside the local Supabase
    Postgres container since there is no REST surface for re-running DDL."""
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


# ── Schema shape / integrity (AC1, AC3, AC8) ────────────────────────────


def test_event_check_constraint_rejects_unknown(sb, delegation):
    """AC1 — the `event` CHECK rejects any value outside the fixed
    vocabulary."""
    from postgrest.exceptions import APIError

    with pytest.raises(APIError) as exc:
        sb.table("delegation_events").insert(
            {
                "delegation_id": delegation["id"],
                "event": "bogus",
                "actor_user_id": delegation["assigner_user_id"],
            }
        ).execute()
    assert getattr(exc.value, "code", None) in ("23514", "PGRST204") or "check" in str(
        exc.value
    ).lower()


def test_view_exposes_required_columns_one_row_per_delegation(sb, delegation):
    """AC3 — `v_delegation_status` returns EXACTLY the 11 named columns and
    exactly one row per `project_delegations` row."""
    rows = (
        sb.table("v_delegation_status")
        .select("*")
        .eq("delegation_id", delegation["id"])
        .execute()
        .data
    )
    assert len(rows) == 1, "expected exactly one status row for this delegation"
    assert set(rows[0].keys()) == {
        "delegation_id",
        "project_id",
        "assigner_user_id",
        "assignee_user_id",
        "task_summary",
        "delivered_conversation_id",
        "delivered_turn_id",
        "delegated_at",
        "status",
        "status_at",
        "status_actor",
    }


def test_index_and_rls_present():
    """AC8 — the `(delegation_id, id desc)` index and the
    `srv_delegation_events` RLS policy exist, queryable from
    `pg_indexes`/`pg_policies`."""
    idx_result = subprocess.run(
        [
            "docker", "exec", _DB_CONTAINER,
            "psql", "-U", "postgres", "-d", "postgres", "-tAc",
            "select indexname from pg_indexes where tablename='delegation_events' order by 1;",
        ],
        capture_output=True,
        timeout=15,
    )
    assert idx_result.returncode == 0, idx_result.stderr.decode(errors="replace")
    names = {line.strip() for line in idx_result.stdout.decode().splitlines() if line.strip()}
    assert "idx_delegation_events_delegation" in names

    rls_result = subprocess.run(
        [
            "docker", "exec", _DB_CONTAINER,
            "psql", "-U", "postgres", "-d", "postgres", "-tAc",
            "select relrowsecurity from pg_class where relname='delegation_events';",
        ],
        capture_output=True,
        timeout=15,
    )
    assert rls_result.returncode == 0, rls_result.stderr.decode(errors="replace")
    assert rls_result.stdout.decode().strip() == "t"

    policy_result = subprocess.run(
        [
            "docker", "exec", _DB_CONTAINER,
            "psql", "-U", "postgres", "-d", "postgres", "-tAc",
            "select policyname from pg_policies where tablename='delegation_events';",
        ],
        capture_output=True,
        timeout=15,
    )
    assert policy_result.returncode == 0, policy_result.stderr.decode(errors="replace")
    assert policy_result.stdout.decode().strip() == "srv_delegation_events"


def test_delegation_delete_cascades_events(sb, fixture_ids, project):
    """AC8 — deleting the parent `project_delegations` row removes all its
    `delegation_events` rows."""
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
                "content": "delivered",
                "author_user_id": fixture_ids["assignee"],
            }
        )
        .execute()
        .data[0]
    )

    from app.db.project_delegations import record_delegation

    deleg = record_delegation(
        project_id=project["id"],
        assigner_user_id=fixture_ids["assigner"],
        assignee_user_id=fixture_ids["assignee"],
        task_summary="cascade proof",
        source_conversation_id=None,
        source_turn_id=None,
        delivered_conversation_id=conv["id"],
        delivered_turn_id=turn["id"],
    )

    from app.db.delegation_events import record_event

    record_event(delegation_id=deleg["id"], event="assigned", actor_user_id=fixture_ids["assigner"])
    record_event(delegation_id=deleg["id"], event="accepted", actor_user_id=fixture_ids["assignee"])

    events_before = (
        sb.table("delegation_events").select("id").eq("delegation_id", deleg["id"]).execute().data
    )
    assert len(events_before) == 2

    sb.table("project_delegations").delete().eq("id", deleg["id"]).execute()

    events_after = (
        sb.table("delegation_events").select("id").eq("delegation_id", deleg["id"]).execute().data
    )
    assert events_after == []

    sb.table("conversations").delete().eq("id", conv["id"]).execute()


# ── Derive-at-read / retrieval (round-trip; AC4, AC5, AC6, AC7) ──────────


def test_status_derivation_flips_with_each_event(sb, fixture_ids, delegation):
    from app.db.delegation_events import current_status, record_event

    record_event(
        delegation_id=delegation["id"], event="assigned", actor_user_id=fixture_ids["assigner"]
    )
    assert current_status(delegation["id"]) == "assigned"

    record_event(
        delegation_id=delegation["id"], event="accepted", actor_user_id=fixture_ids["assignee"]
    )
    assert current_status(delegation["id"]) == "accepted"

    record_event(
        delegation_id=delegation["id"], event="completed", actor_user_id=fixture_ids["assignee"]
    )
    assert current_status(delegation["id"]) == "completed"

    # No prior row was ever updated — the log is strictly append-only.
    all_events = (
        sb.table("delegation_events")
        .select("event")
        .eq("delegation_id", delegation["id"])
        .order("id")
        .execute()
        .data
    )
    assert [e["event"] for e in all_events] == ["assigned", "accepted", "completed"]


def test_empty_events_defaults_to_assigned(sb, delegation):
    """AC5 — a delegation with zero `delegation_events` rows derives
    `status='assigned'`, `status_at==delegated_at`, `status_actor==assigner`."""
    row = (
        sb.table("v_delegation_status")
        .select("*")
        .eq("delegation_id", delegation["id"])
        .execute()
        .data[0]
    )
    assert row["status"] == "assigned"
    assert row["status_at"] == row["delegated_at"]
    assert row["status_actor"] == delegation["assigner_user_id"]


def test_current_status_reads_view(fixture_ids, delegation):
    from app.db.delegation_events import current_status, record_event

    # Zero events -> fallback.
    assert current_status(delegation["id"]) == "assigned"

    record_event(
        delegation_id=delegation["id"], event="in_progress", actor_user_id=fixture_ids["assignee"]
    )
    assert current_status(delegation["id"]) == "in_progress"

    assert current_status(-1) is None


def test_list_status_for_assignee_filters(sb, fixture_ids, project, delegation):
    from app.db.delegation_events import list_status_for_assignee

    # A second delegation on the SAME project addressed to a DIFFERENT
    # assignee (the assigner, standing in for "some other user") must
    # never show up in the first assignee's list.
    conv2 = (
        sb.table("conversations")
        .insert(
            {
                "company_id": fixture_ids["company_id"],
                "user_id": fixture_ids["assigner"],
                "project_id": project["id"],
                "kind": "individual",
            }
        )
        .execute()
        .data[0]
    )
    turn2 = (
        sb.table("conversation_turns")
        .insert(
            {
                "conversation_id": conv2["id"],
                "role": "assistant",
                "content": "delivered to the other party",
                "author_user_id": fixture_ids["assigner"],
            }
        )
        .execute()
        .data[0]
    )
    from app.db.project_delegations import record_delegation

    other = record_delegation(
        project_id=project["id"],
        assigner_user_id=fixture_ids["assignee"],
        assignee_user_id=fixture_ids["assigner"],
        task_summary="addressed to the other party",
        source_conversation_id=None,
        source_turn_id=None,
        delivered_conversation_id=conv2["id"],
        delivered_turn_id=turn2["id"],
    )

    try:
        results = list_status_for_assignee(project["id"], fixture_ids["assignee"])
        ids = [r["delegation_id"] for r in results]
        assert delegation["id"] in ids
        assert other["id"] not in ids
        assert all(r["assignee_user_id"] == fixture_ids["assignee"] for r in results)
        assert all(r["project_id"] == project["id"] for r in results)
    finally:
        sb.table("project_delegations").delete().eq("id", other["id"]).execute()
        sb.table("conversations").delete().eq("id", conv2["id"]).execute()


def test_list_status_for_assigner_filters(sb, fixture_ids, project, delegation):
    from app.db.delegation_events import list_status_for_assigner

    other_project = (
        sb.table("projects")
        .insert(
            {
                "company_id": fixture_ids["company_id"],
                "workspace_id": fixture_ids["workspace_id"],
                "name": f"delegation-events-crossproject-{uuid.uuid4().hex[:8]}",
                "created_by": fixture_ids["assigner"],
            }
        )
        .execute()
        .data[0]
    )
    conv2 = (
        sb.table("conversations")
        .insert(
            {
                "company_id": fixture_ids["company_id"],
                "user_id": fixture_ids["assignee"],
                "project_id": other_project["id"],
                "kind": "individual",
            }
        )
        .execute()
        .data[0]
    )
    turn2 = (
        sb.table("conversation_turns")
        .insert(
            {
                "conversation_id": conv2["id"],
                "role": "assistant",
                "content": "delivered on a different project",
                "author_user_id": fixture_ids["assignee"],
            }
        )
        .execute()
        .data[0]
    )
    from app.db.project_delegations import record_delegation

    other = record_delegation(
        project_id=other_project["id"],
        assigner_user_id=fixture_ids["assigner"],
        assignee_user_id=fixture_ids["assignee"],
        task_summary="same assigner, different project",
        source_conversation_id=None,
        source_turn_id=None,
        delivered_conversation_id=conv2["id"],
        delivered_turn_id=turn2["id"],
    )

    try:
        results = list_status_for_assigner(project["id"], fixture_ids["assigner"])
        ids = [r["delegation_id"] for r in results]
        assert delegation["id"] in ids
        assert other["id"] not in ids, "a row scoped to a different project must never be returned"
        assert all(r["assigner_user_id"] == fixture_ids["assigner"] for r in results)
        assert all(r["project_id"] == project["id"] for r in results)
    finally:
        sb.table("project_delegations").delete().eq("id", other["id"]).execute()
        sb.table("conversations").delete().eq("id", conv2["id"]).execute()
        sb.table("projects").delete().eq("id", other_project["id"]).execute()
