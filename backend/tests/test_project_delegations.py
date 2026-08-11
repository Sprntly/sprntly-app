"""Real local-Supabase round-trip proof for `project_delegations` — the
schema and DB helpers introduced alongside this ticket.

Every other backend test substitutes `FakeSupabaseClient` (see
`tests/_fake_supabase.py`) — an in-memory store with no real SQL engine
behind it, so it cannot enforce a FK, an `on delete cascade`/`on delete set
null`, or a real index/RLS-policy catalog lookup. Those are exactly the
invariants this migration adds, so proving them needs the real thing: a
real local Postgres, through the real supabase-py client, over real HTTP
via PostgREST — mirrors `test_projects_schema_roundtrip.py`'s own pattern
(same fixture shape, same non-loopback guard) for the identical reason.

Why it is not in CI. Neither CI lane runs a Postgres/Supabase service (see
`test-backend.yml`), and `project_delegations` does not exist without
applying the migration first. This file skips cleanly wherever the
ingredients are absent, and runs where they are present: a local dev rig
with `supabase start` running and the migration applied.

Run it with:

    RUN_PROJECT_DELEGATIONS_ROUNDTRIP=1 \\
        pytest tests/test_project_delegations.py -m integration

It reads real fixture rows already in the local rig (an existing company,
its workspace, and two of its members) rather than minting new
`auth.users` rows — inserting into `auth.users` needs the GoTrue admin
API, more moving parts than a schema round-trip needs. Everything this
file itself creates (projects, conversations, delegations) is deleted in
fixture teardown; the reused company/workspace/user rows are read, never
mutated.
"""
from __future__ import annotations

import os
import pathlib
import shutil
import subprocess
import uuid

import pytest

_RUN_LIVE = os.getenv("RUN_PROJECT_DELEGATIONS_ROUNDTRIP") == "1"

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(
        not _RUN_LIVE,
        reason=(
            "needs a real local Supabase — set RUN_PROJECT_DELEGATIONS_ROUNDTRIP=1 "
            "with SUPABASE_URL/SUPABASE_SERVICE_ROLE_KEY pointed at the local rig "
            "and the project_delegations migration applied"
        ),
    ),
]

_MIGRATIONS_DIR = pathlib.Path(__file__).resolve().parents[2] / "supabase" / "migrations"
_MIGRATION_FILE = "20260811120300_project_delegations.sql"
_DB_CONTAINER = os.getenv("PROJECTS_SCHEMA_TEST_DB_CONTAINER", "supabase_db_Sprntly")


def _client():
    from app.config import settings
    from supabase import create_client

    url = settings.supabase_url
    assert "127.0.0.1" in url or "localhost" in url, (
        "refusing to run the live delegations round-trip against a non-loopback "
        f"SUPABASE_URL ({url!r}) — this test mutates real rows"
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
                "name": f"delegations-roundtrip-{uuid.uuid4().hex[:8]}",
                "created_by": fixture_ids["assigner"],
            }
        )
        .execute()
        .data[0]
    )
    yield row
    sb.table("projects").delete().eq("id", row["id"]).execute()


@pytest.fixture
def conversation_ids(sb):
    """Tests append ids they create here; deleted at teardown."""
    created: list[int] = []
    yield created
    for cid in created:
        sb.table("conversations").delete().eq("id", cid).execute()


def _make_conversation(sb, fixture_ids, project_id, conversation_ids) -> int:
    row = (
        sb.table("conversations")
        .insert(
            {
                "company_id": fixture_ids["company_id"],
                "user_id": fixture_ids["assignee"],
                "project_id": project_id,
                "kind": "individual",
            }
        )
        .execute()
        .data[0]
    )
    conversation_ids.append(row["id"])
    return row["id"]


def _make_turn(sb, conversation_id: int, *, author_user_id: str) -> int:
    row = (
        sb.table("conversation_turns")
        .insert(
            {
                "conversation_id": conversation_id,
                "role": "assistant",
                "content": "delivered brief turn",
                "author_user_id": author_user_id,
            }
        )
        .execute()
        .data[0]
    )
    return row["id"]


# ── Migration apply / idempotency ───────────────────────────────────────


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


# ── Schema shape ─────────────────────────────────────────────────────────


def test_no_status_column(sb):
    """AD-P17 inputs-only guard: `status` is absent from the table."""
    from postgrest.exceptions import APIError

    with pytest.raises(APIError) as exc:
        sb.table("project_delegations").select("status").limit(1).execute()
    # PostgREST surfaces an undefined-column error (Postgres code 42703)
    # rather than a silent empty result.
    assert getattr(exc.value, "code", None) in ("42703", "PGRST204", "PGRST116") or "status" in str(
        exc.value
    ).lower()


def test_indexes_present():
    """The three named indexes exist, queryable from `pg_indexes` — this
    goes straight at Postgres since PostgREST has no index-introspection
    endpoint."""
    result = subprocess.run(
        [
            "docker", "exec", _DB_CONTAINER,
            "psql", "-U", "postgres", "-d", "postgres", "-tAc",
            "select indexname from pg_indexes where tablename='project_delegations' order by 1;",
        ],
        capture_output=True,
        timeout=15,
    )
    assert result.returncode == 0, result.stderr.decode(errors="replace")
    names = {line.strip() for line in result.stdout.decode().splitlines() if line.strip()}
    assert {
        "idx_project_delegations_project",
        "idx_project_delegations_assignee",
        "idx_project_delegations_assigner",
    } <= names


def test_rls_policy_present():
    """RLS is enabled and the server-role policy exists, matching the
    `project_memory_entries` policy shape."""
    rls_result = subprocess.run(
        [
            "docker", "exec", _DB_CONTAINER,
            "psql", "-U", "postgres", "-d", "postgres", "-tAc",
            "select relrowsecurity from pg_class where relname='project_delegations';",
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
            "select policyname from pg_policies where tablename='project_delegations';",
        ],
        capture_output=True,
        timeout=15,
    )
    assert policy_result.returncode == 0, policy_result.stderr.decode(errors="replace")
    assert policy_result.stdout.decode().strip() == "srv_project_delegations"


# ── Retrieval (round-trip) ──────────────────────────────────────────────


def test_record_delegation_round_trip(sb, fixture_ids, project, conversation_ids):
    from app.db.project_delegations import record_delegation

    conv_id = _make_conversation(sb, fixture_ids, project["id"], conversation_ids)
    turn_id = _make_turn(sb, conv_id, author_user_id=fixture_ids["assignee"])

    inserted = record_delegation(
        project_id=project["id"],
        assigner_user_id=fixture_ids["assigner"],
        assignee_user_id=fixture_ids["assignee"],
        task_summary="write the migration",
        source_conversation_id=None,
        source_turn_id=None,
        delivered_conversation_id=conv_id,
        delivered_turn_id=turn_id,
    )

    assert inserted["id"] is not None
    assert inserted["created_at"] is not None
    assert inserted["task_summary"] == "write the migration"
    assert inserted["assigner_user_id"] == fixture_ids["assigner"]
    assert inserted["assignee_user_id"] == fixture_ids["assignee"]
    assert inserted["delivered_conversation_id"] == conv_id
    assert inserted["delivered_turn_id"] == turn_id

    reread = (
        sb.table("project_delegations")
        .select("*")
        .eq("id", inserted["id"])
        .execute()
        .data[0]
    )
    assert reread["task_summary"] == "write the migration"
    assert reread["assigner_user_id"] == fixture_ids["assigner"]
    assert reread["assignee_user_id"] == fixture_ids["assignee"]
    assert reread["delivered_conversation_id"] == conv_id
    assert reread["delivered_turn_id"] == turn_id


def test_list_for_assignee_filters(sb, fixture_ids, project, conversation_ids):
    from app.db.project_delegations import list_delegations_for_assignee, record_delegation

    conv_id = _make_conversation(sb, fixture_ids, project["id"], conversation_ids)
    turn_a = _make_turn(sb, conv_id, author_user_id=fixture_ids["assignee"])
    turn_b = _make_turn(sb, conv_id, author_user_id=fixture_ids["assigner"])

    row_to_assignee = record_delegation(
        project_id=project["id"],
        assigner_user_id=fixture_ids["assigner"],
        assignee_user_id=fixture_ids["assignee"],
        task_summary="task for the assignee",
        source_conversation_id=None,
        source_turn_id=None,
        delivered_conversation_id=conv_id,
        delivered_turn_id=turn_a,
    )
    # A second row addressed to a DIFFERENT assignee (the assigner, here,
    # standing in for "some other user") must never show up in the first
    # user's list.
    record_delegation(
        project_id=project["id"],
        assigner_user_id=fixture_ids["assignee"],
        assignee_user_id=fixture_ids["assigner"],
        task_summary="task for the other user",
        source_conversation_id=None,
        source_turn_id=None,
        delivered_conversation_id=conv_id,
        delivered_turn_id=turn_b,
    )

    results = list_delegations_for_assignee(fixture_ids["assignee"])
    ids = [r["id"] for r in results]
    assert row_to_assignee["id"] in ids
    assert all(r["assignee_user_id"] == fixture_ids["assignee"] for r in results)


def test_list_for_assigner_and_project_filter(sb, fixture_ids, project, conversation_ids):
    from app.db.project_delegations import (
        list_delegations_for_assigner,
        list_delegations_for_project,
        record_delegation,
    )

    conv_id = _make_conversation(sb, fixture_ids, project["id"], conversation_ids)
    turn_id = _make_turn(sb, conv_id, author_user_id=fixture_ids["assignee"])

    row = record_delegation(
        project_id=project["id"],
        assigner_user_id=fixture_ids["assigner"],
        assignee_user_id=fixture_ids["assignee"],
        task_summary="assigner/project filter proof",
        source_conversation_id=None,
        source_turn_id=None,
        delivered_conversation_id=conv_id,
        delivered_turn_id=turn_id,
    )

    by_assigner = list_delegations_for_assigner(fixture_ids["assigner"])
    assert row["id"] in [r["id"] for r in by_assigner]
    assert all(r["assigner_user_id"] == fixture_ids["assigner"] for r in by_assigner)

    by_project = list_delegations_for_project(project["id"])
    assert row["id"] in [r["id"] for r in by_project]
    assert all(r["project_id"] == project["id"] for r in by_project)


# ── Edge / integrity ─────────────────────────────────────────────────────


def test_project_delete_cascades_delegation(sb, fixture_ids):
    proj = (
        sb.table("projects")
        .insert(
            {
                "company_id": fixture_ids["company_id"],
                "workspace_id": fixture_ids["workspace_id"],
                "name": f"delegations-cascade-{uuid.uuid4().hex[:8]}",
                "created_by": fixture_ids["assigner"],
            }
        )
        .execute()
        .data[0]
    )
    pid = proj["id"]

    conv = (
        sb.table("conversations")
        .insert(
            {
                "company_id": fixture_ids["company_id"],
                "user_id": fixture_ids["assignee"],
                "project_id": pid,
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

    delegation = record_delegation(
        project_id=pid,
        assigner_user_id=fixture_ids["assigner"],
        assignee_user_id=fixture_ids["assignee"],
        task_summary="cascade proof",
        source_conversation_id=None,
        source_turn_id=None,
        delivered_conversation_id=conv["id"],
        delivered_turn_id=turn["id"],
    )

    sb.table("projects").delete().eq("id", pid).execute()

    remaining = (
        sb.table("project_delegations").select("*").eq("id", delegation["id"]).execute().data
    )
    assert remaining == []

    # cleanup (project already gone; conversation cascades from nothing —
    # remove explicitly since it wasn't scoped to the project's own cascade)
    sb.table("conversations").delete().eq("id", conv["id"]).execute()


def test_conversation_delete_nulls_refs(sb, fixture_ids, project, conversation_ids):
    conv_id = _make_conversation(sb, fixture_ids, project["id"], conversation_ids)
    turn_id = _make_turn(sb, conv_id, author_user_id=fixture_ids["assignee"])

    from app.db.project_delegations import record_delegation

    delegation = record_delegation(
        project_id=project["id"],
        assigner_user_id=fixture_ids["assigner"],
        assignee_user_id=fixture_ids["assignee"],
        task_summary="null-on-delete proof",
        source_conversation_id=conv_id,
        source_turn_id=turn_id,
        delivered_conversation_id=conv_id,
        delivered_turn_id=turn_id,
    )

    sb.table("conversations").delete().eq("id", conv_id).execute()
    conversation_ids.remove(conv_id)  # already deleted; don't double-delete in teardown

    survivor = (
        sb.table("project_delegations").select("*").eq("id", delegation["id"]).execute().data
    )
    assert len(survivor) == 1
    assert survivor[0]["source_conversation_id"] is None
    assert survivor[0]["delivered_conversation_id"] is None
    # the fact itself survives, including the non-FK turn ids
    assert survivor[0]["source_turn_id"] == turn_id
    assert survivor[0]["delivered_turn_id"] == turn_id

    # cleanup: delete the delegation row (its parent project is cleaned by
    # the `project` fixture's own teardown)
    sb.table("project_delegations").delete().eq("id", delegation["id"]).execute()
