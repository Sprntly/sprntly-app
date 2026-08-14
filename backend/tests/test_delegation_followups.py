"""Real local-Supabase round-trip proof for `delegation_followups` — the
durable per-task cadence-scheduling table this ticket adds.

Mirrors `test_project_delegations.py`'s / `test_delegation_events.py`'s own
pattern (same fixture shape, same non-loopback guard, same reused-fixture-
row convention) for the identical reason: `FakeSupabaseClient` is an
in-memory store with no real SQL engine behind it, so it cannot enforce a
FK, an `on delete cascade`, a real index/RLS-policy catalog lookup, or a
partial index predicate. Those are exactly the invariants this migration
adds, so proving them needs the real thing: a real local Postgres, through
the real supabase-py client, over real HTTP via PostgREST.

Why it is not in CI. Neither CI lane runs a Postgres/Supabase service, and
`delegation_followups` does not exist without applying the migration first.
This file skips cleanly wherever the ingredients are absent, and runs where
they are present: a local dev rig with `supabase start` running and the
migration applied.

Run it with:

    RUN_DELEGATION_FOLLOWUPS_ROUNDTRIP=1 \\
        pytest tests/test_delegation_followups.py -m integration

It reads real fixture rows already in the local rig (an existing company,
its workspace, and two of its members) rather than minting new
`auth.users` rows, exactly like its siblings. Everything this file itself
creates (projects, conversations, delegations, followups) is deleted in
fixture teardown; the reused company/workspace/user rows are read, never
mutated.
"""
from __future__ import annotations

import os
import pathlib
import shutil
import subprocess
import uuid
from datetime import datetime, timedelta, timezone

import pytest

_RUN_LIVE = os.getenv("RUN_DELEGATION_FOLLOWUPS_ROUNDTRIP") == "1"

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(
        not _RUN_LIVE,
        reason=(
            "needs a real local Supabase — set RUN_DELEGATION_FOLLOWUPS_ROUNDTRIP=1 "
            "with SUPABASE_URL/SUPABASE_SERVICE_ROLE_KEY pointed at the local rig "
            "and the delegation_followups migration applied"
        ),
    ),
]

_MIGRATIONS_DIR = pathlib.Path(__file__).resolve().parents[2] / "supabase" / "migrations"
_MIGRATION_FILE = "20260814140000_delegation_followups.sql"
_DB_CONTAINER = os.getenv("PROJECTS_SCHEMA_TEST_DB_CONTAINER", "supabase_db_Sprntly")


def _client():
    from app.config import settings
    from supabase import create_client

    url = settings.supabase_url
    assert "127.0.0.1" in url or "localhost" in url, (
        "refusing to run the live delegation-followups round-trip against a "
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
                "name": f"delegation-followups-roundtrip-{uuid.uuid4().hex[:8]}",
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
    """A real `project_delegations` row for followups to hang off. Deleted
    at teardown (cascades its own `delegation_followups` row)."""
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
        task_summary="delegation-followups round-trip fixture",
        source_conversation_id=None,
        source_turn_id=None,
        delivered_conversation_id=conv["id"],
        delivered_turn_id=turn["id"],
    )
    yield row
    sb.table("project_delegations").delete().eq("id", row["id"]).execute()
    sb.table("conversations").delete().eq("id", conv["id"]).execute()


# ── Migration apply / idempotency (AC1) ──────────────────────────────────


def test_migration_double_apply_idempotent():
    """Re-applying the actual committed SQL file against the real DB is a
    documented no-op."""
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

    # Exactly one table, one policy — the idempotency proof's other half.
    table_count = subprocess.run(
        [
            "docker", "exec", _DB_CONTAINER,
            "psql", "-U", "postgres", "-d", "postgres", "-tAc",
            "select count(*) from pg_tables where tablename='delegation_followups';",
        ],
        capture_output=True, timeout=15,
    )
    assert table_count.returncode == 0, table_count.stderr.decode(errors="replace")
    assert table_count.stdout.decode().strip() == "1"

    policy_count = subprocess.run(
        [
            "docker", "exec", _DB_CONTAINER,
            "psql", "-U", "postgres", "-d", "postgres", "-tAc",
            "select count(*) from pg_policies where tablename='delegation_followups';",
        ],
        capture_output=True, timeout=15,
    )
    assert policy_count.returncode == 0, policy_count.stderr.decode(errors="replace")
    assert policy_count.stdout.decode().strip() == "1"


# ── Schema shape (AC1, AC2) ────────────────────────────────────────────────


def test_columns_present(sb, delegation):
    """AC1 — the exact column set exists, PK is `delegation_id`, and a bare
    insert (all-defaults) round-trips `muted=False` and every other column
    NULL."""
    row = (
        sb.table("delegation_followups")
        .insert({"delegation_id": delegation["id"]})
        .execute()
        .data[0]
    )
    assert set(row.keys()) == {
        "delegation_id", "expected_completion", "next_check_in", "last_checked_in",
        "muted", "pending_done_since", "updated_at",
    }
    assert row["delegation_id"] == delegation["id"]
    assert row["muted"] is False
    assert row["expected_completion"] is None
    assert row["next_check_in"] is None
    assert row["last_checked_in"] is None
    assert row["pending_done_since"] is None
    assert row["updated_at"] is not None


def test_followups_no_workspace_id_column_rls_enabled(sb, delegation):
    """AC2 — `workspace_id` is absent, RLS is enabled, and exactly the one
    `srv_delegation_followups` server-role policy exists."""
    from postgrest.exceptions import APIError

    with pytest.raises(APIError) as exc:
        sb.table("delegation_followups").select("workspace_id").limit(1).execute()
    assert getattr(exc.value, "code", None) in ("42703", "PGRST204", "PGRST116") or (
        "workspace_id" in str(exc.value).lower() or "column" in str(exc.value).lower()
    )

    rls_result = subprocess.run(
        [
            "docker", "exec", _DB_CONTAINER,
            "psql", "-U", "postgres", "-d", "postgres", "-tAc",
            "select relrowsecurity from pg_class where relname='delegation_followups';",
        ],
        capture_output=True, timeout=15,
    )
    assert rls_result.returncode == 0, rls_result.stderr.decode(errors="replace")
    assert rls_result.stdout.decode().strip() == "t"

    policy_result = subprocess.run(
        [
            "docker", "exec", _DB_CONTAINER,
            "psql", "-U", "postgres", "-d", "postgres", "-tAc",
            "select policyname from pg_policies where tablename='delegation_followups';",
        ],
        capture_output=True, timeout=15,
    )
    assert policy_result.returncode == 0, policy_result.stderr.decode(errors="replace")
    assert policy_result.stdout.decode().strip() == "srv_delegation_followups"


def test_index_present():
    idx_result = subprocess.run(
        [
            "docker", "exec", _DB_CONTAINER,
            "psql", "-U", "postgres", "-d", "postgres", "-tAc",
            "select indexname from pg_indexes where tablename='delegation_followups' order by 1;",
        ],
        capture_output=True, timeout=15,
    )
    assert idx_result.returncode == 0, idx_result.stderr.decode(errors="replace")
    names = {line.strip() for line in idx_result.stdout.decode().splitlines() if line.strip()}
    assert "idx_delegation_followups_due" in names


# ── Retrieval (round-trip; AC3, AC4) ─────────────────────────────────────


def test_upsert_partial_merges_fields(sb, delegation):
    """AC3 — `upsert_followup(id, next_check_in=T)` on a fresh delegation
    inserts a row with `next_check_in=T` and everything else NULL/default;
    a second `upsert_followup(id, expected_completion=E)` updates
    `expected_completion=E` and leaves `next_check_in=T` unchanged."""
    from app.db.delegation_followups import get_followup, upsert_followup

    t = datetime(2026, 8, 20, 9, 0, tzinfo=timezone.utc)
    first = upsert_followup(delegation["id"], next_check_in=t)
    assert first["next_check_in"].startswith("2026-08-20T09:00:00")
    assert first["expected_completion"] is None
    assert first["muted"] is False

    e = datetime(2026, 8, 21, 9, 0, tzinfo=timezone.utc)
    second = upsert_followup(delegation["id"], expected_completion=e)
    assert second["expected_completion"].startswith("2026-08-21T09:00:00")
    assert second["next_check_in"].startswith("2026-08-20T09:00:00"), (
        "a field not passed to the second upsert must be left unchanged"
    )

    reread = get_followup(delegation["id"])
    assert reread["expected_completion"].startswith("2026-08-21T09:00:00")
    assert reread["next_check_in"].startswith("2026-08-20T09:00:00")


def test_upsert_pending_done_since_explicit_none_clears(sb, delegation):
    """A caller passing `pending_done_since=None` explicitly clears the
    column to NULL — distinct from simply not passing it at all."""
    from app.db.delegation_followups import get_followup, upsert_followup

    marked = datetime(2026, 8, 10, 0, 0, tzinfo=timezone.utc)
    upsert_followup(delegation["id"], pending_done_since=marked)
    assert get_followup(delegation["id"])["pending_done_since"] is not None

    upsert_followup(delegation["id"], pending_done_since=None)
    assert get_followup(delegation["id"])["pending_done_since"] is None


def test_get_followup_missing_returns_none(sb):
    from app.db.delegation_followups import get_followup

    assert get_followup(-1) is None


# ── Edge / integrity (AC1) ────────────────────────────────────────────────


def test_followup_cascades_on_delegation_delete(sb, fixture_ids, project):
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

    from app.db.delegation_followups import upsert_followup

    upsert_followup(deleg["id"], next_check_in=datetime.now(timezone.utc) + timedelta(hours=24))

    rows_before = (
        sb.table("delegation_followups").select("delegation_id").eq("delegation_id", deleg["id"]).execute().data
    )
    assert len(rows_before) == 1

    sb.table("project_delegations").delete().eq("id", deleg["id"]).execute()

    rows_after = (
        sb.table("delegation_followups").select("delegation_id").eq("delegation_id", deleg["id"]).execute().data
    )
    assert rows_after == []

    sb.table("conversations").delete().eq("id", conv["id"]).execute()
