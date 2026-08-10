"""Real local-Supabase round-trip proof for the projects/chat/memory schema.

Every other backend test substitutes `FakeSupabaseClient` for the Supabase
client (see `tests/_fake_supabase.py`) — an in-memory store with no real SQL
engine behind it, so it cannot enforce a CHECK constraint, a FK, or a partial
unique index. Those are exactly the invariants this migration set adds (the
XOR provenance CHECK on `project_memory_entries`, the one-group-chat-per-
project partial unique index on `conversations`), so proving them needs the
real thing: a real local Postgres, through the real supabase-py client, over
real HTTP via PostgREST — not raw SQL review, not a mock.

Why it is not in CI. Neither CI lane runs a Postgres/Supabase service (see
`test-backend.yml`), and none of the seven tables this suite touches exist
without applying the three migrations in this set first. So this file skips
cleanly wherever the ingredients are absent — mirroring
`test_document_catalog_ranking_live.py`'s pattern for the same reason — and
runs where they are present: a local dev rig with `supabase start` running
and the three migrations applied.

Run it with:

    RUN_PROJECTS_SCHEMA_ROUNDTRIP=1 \\
        pytest tests/test_projects_schema_roundtrip.py -m integration

It reads real fixture rows already in the local rig (an existing company, its
workspace, and a couple of its members) rather than minting new `auth.users`
rows — inserting into `auth.users` needs the GoTrue admin API, which is more
moving parts than a schema round-trip needs. Everything this file itself
creates (projects, memory entries, conversations, ...) is deleted in fixture
teardown; the reused company/workspace/user rows are read, never mutated.
"""
from __future__ import annotations

import os
import pathlib
import subprocess
import uuid

import pytest

_RUN_LIVE = os.getenv("RUN_PROJECTS_SCHEMA_ROUNDTRIP") == "1"

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(
        not _RUN_LIVE,
        reason=(
            "needs a real local Supabase — set RUN_PROJECTS_SCHEMA_ROUNDTRIP=1 "
            "with SUPABASE_URL/SUPABASE_SERVICE_ROLE_KEY pointed at the local "
            "rig and the three projects/chat/memory migrations applied"
        ),
    ),
]

_MIGRATIONS_DIR = pathlib.Path(__file__).resolve().parents[2] / "supabase" / "migrations"
_MIGRATION_FILES = [
    "20260811120000_projects.sql",
    "20260811120100_conversations_project_columns.sql",
    "20260811120200_project_memory.sql",
]
_DB_CONTAINER = os.getenv("PROJECTS_SCHEMA_TEST_DB_CONTAINER", "supabase_db_Sprntly")


def _client():
    from app.config import settings
    from supabase import create_client

    url = settings.supabase_url
    assert "127.0.0.1" in url or "localhost" in url, (
        "refusing to run the live schema round-trip against a non-loopback "
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
        "user_a": members[0]["user_id"],
        "user_b": members[1]["user_id"],
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
                "name": f"schema-roundtrip-{uuid.uuid4().hex[:8]}",
                "created_by": fixture_ids["user_a"],
            }
        )
        .execute()
        .data[0]
    )
    yield row
    sb.table("projects").delete().eq("id", row["id"]).execute()


@pytest.fixture
def conversation_ids(sb):
    """Tests append ids they create here; deleted at teardown (cascades
    project_chat_members + conversation_turns for each)."""
    created: list[int] = []
    yield created
    for cid in created:
        sb.table("conversations").delete().eq("id", cid).execute()


def _api_error_code(exc) -> str | None:
    return getattr(exc, "code", None)


# ── Creation / migration ────────────────────────────────────────────────


def test_all_three_files_apply(sb):
    """All seven new tables + new columns are selectable post-apply."""
    for table in (
        "projects",
        "project_members",
        "project_artifacts",
        "project_chat_members",
        "project_memory_entries",
        "project_memory_summary",
    ):
        resp = sb.table(table).select("*").limit(1).execute()
        assert resp.data is not None  # no exception raised == table exists & selectable

    conv = sb.table("conversations").select("id,project_id,kind").limit(1).execute()
    assert conv.data is not None

    turns = sb.table("conversation_turns").select("id,author_user_id").limit(1).execute()
    assert turns.data is not None


def test_migrations_idempotent_double_apply():
    """Re-applying all three files against the real DB is a documented no-op.

    Re-runs the actual committed SQL files (not a paraphrase of them) through
    `psql` inside the local Supabase Postgres container — there is no REST
    surface for re-running DDL, so this is the one assertion in the suite
    that shells out rather than going through supabase-py.
    """
    import shutil

    if shutil.which("docker") is None:
        pytest.skip("docker not on PATH — cannot re-apply migrations for the idempotency proof")

    for name in _MIGRATION_FILES:
        path = _MIGRATIONS_DIR / name
        assert path.is_file(), f"migration file missing: {path}"
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
            f"re-applying {name} was not idempotent:\n"
            f"stdout: {result.stdout.decode(errors='replace')}\n"
            f"stderr: {result.stderr.decode(errors='replace')}"
        )


# ── Retrieval (round-trip) ──────────────────────────────────────────────


def test_project_member_artifact_roundtrip(sb, fixture_ids, project):
    sb.table("project_members").insert(
        {"project_id": project["id"], "user_id": fixture_ids["user_b"]},
    ).execute()
    members = (
        sb.table("project_members").select("user_id").eq("project_id", project["id"]).execute().data
    )
    assert members == [{"user_id": fixture_ids["user_b"]}]

    sb.table("project_artifacts").insert(
        {"project_id": project["id"], "artifact_type": "prd", "artifact_id": 999999},
    ).execute()
    artifacts = (
        sb.table("project_artifacts")
        .select("artifact_type,artifact_id")
        .eq("project_id", project["id"])
        .execute()
        .data
    )
    assert artifacts == [{"artifact_type": "prd", "artifact_id": 999999}]


def test_conversation_group_kind_roundtrip(sb, fixture_ids, project, conversation_ids):
    row = (
        sb.table("conversations")
        .insert(
            {
                "company_id": fixture_ids["company_id"],
                "user_id": fixture_ids["user_a"],
                "project_id": project["id"],
                "kind": "group",
            },
        )
        .execute()
        .data[0]
    )
    conversation_ids.append(row["id"])

    read_back = (
        sb.table("conversations")
        .select("id,project_id,kind")
        .eq("id", row["id"])
        .execute()
        .data[0]
    )
    assert read_back["project_id"] == project["id"]
    assert read_back["kind"] == "group"


def test_turn_author_roundtrip(sb, fixture_ids, project, conversation_ids):
    conv = (
        sb.table("conversations")
        .insert(
            {
                "company_id": fixture_ids["company_id"],
                "user_id": fixture_ids["user_a"],
                "project_id": project["id"],
                "kind": "group",
            },
        )
        .execute()
        .data[0]
    )
    conversation_ids.append(conv["id"])

    turn = (
        sb.table("conversation_turns")
        .insert(
            {
                "conversation_id": conv["id"],
                "role": "user",
                "content": "roundtrip turn",
                "author_user_id": fixture_ids["user_b"],
            },
        )
        .execute()
        .data[0]
    )

    read_back = (
        sb.table("conversation_turns")
        .select("author_user_id")
        .eq("id", turn["id"])
        .execute()
        .data[0]
    )
    assert read_back["author_user_id"] == fixture_ids["user_b"]


def test_memory_entry_user_and_agent_roundtrip(sb, fixture_ids, project):
    user_entry = (
        sb.table("project_memory_entries")
        .insert(
            {
                "project_id": project["id"],
                "body": "user-authored insight",
                "author_user_id": fixture_ids["user_a"],
            },
        )
        .execute()
        .data[0]
    )
    agent_entry = (
        sb.table("project_memory_entries")
        .insert(
            {
                "project_id": project["id"],
                "body": "agent-promoted insight",
                "promoted_by": "agent",
            },
        )
        .execute()
        .data[0]
    )

    rows = (
        sb.table("project_memory_entries")
        .select("id,author_user_id,promoted_by")
        .eq("project_id", project["id"])
        .execute()
        .data
    )
    by_id = {r["id"]: r for r in rows}
    assert by_id[user_entry["id"]]["author_user_id"] == fixture_ids["user_a"]
    assert by_id[user_entry["id"]]["promoted_by"] is None
    assert by_id[agent_entry["id"]]["promoted_by"] == "agent"
    assert by_id[agent_entry["id"]]["author_user_id"] is None


def test_memory_summary_roundtrip(sb, project):
    sb.table("project_memory_summary").insert(
        {
            "project_id": project["id"],
            "summary_md": "what this project knows",
            "entry_count": 2,
            "stale": False,
        },
    ).execute()

    read_back = (
        sb.table("project_memory_summary")
        .select("entry_count,stale")
        .eq("project_id", project["id"])
        .execute()
        .data[0]
    )
    assert read_back["entry_count"] == 2
    assert read_back["stale"] is False

    sb.table("project_memory_summary").update({"stale": True}).eq(
        "project_id", project["id"],
    ).execute()
    reread = (
        sb.table("project_memory_summary")
        .select("stale")
        .eq("project_id", project["id"])
        .execute()
        .data[0]
    )
    assert reread["stale"] is True


# ── Error handling / constraints ────────────────────────────────────────


def test_origin_and_artifact_type_checks(sb, fixture_ids, project):
    from postgrest.exceptions import APIError

    with pytest.raises(APIError) as bad_origin:
        sb.table("projects").insert(
            {
                "company_id": fixture_ids["company_id"],
                "workspace_id": fixture_ids["workspace_id"],
                "name": "bad-origin",
                "origin": "not-a-real-origin",
                "created_by": fixture_ids["user_a"],
            },
        ).execute()
    assert _api_error_code(bad_origin.value) == "23514"

    with pytest.raises(APIError) as bad_type:
        sb.table("project_artifacts").insert(
            {"project_id": project["id"], "artifact_type": "not-a-real-type", "artifact_id": 1},
        ).execute()
    assert _api_error_code(bad_type.value) == "23514"


def test_one_group_chat_per_project(sb, fixture_ids, project, conversation_ids):
    from postgrest.exceptions import APIError

    first_group = (
        sb.table("conversations")
        .insert(
            {
                "company_id": fixture_ids["company_id"],
                "user_id": fixture_ids["user_a"],
                "project_id": project["id"],
                "kind": "group",
            },
        )
        .execute()
        .data[0]
    )
    conversation_ids.append(first_group["id"])

    with pytest.raises(APIError) as second_group:
        sb.table("conversations").insert(
            {
                "company_id": fixture_ids["company_id"],
                "user_id": fixture_ids["user_b"],
                "project_id": project["id"],
                "kind": "group",
            },
        ).execute()
    assert _api_error_code(second_group.value) == "23505"

    # Many individual rows for the same project are unaffected by the
    # partial index — it only governs kind='group'.
    individual_one = (
        sb.table("conversations")
        .insert(
            {
                "company_id": fixture_ids["company_id"],
                "user_id": fixture_ids["user_a"],
                "project_id": project["id"],
                "kind": "individual",
            },
        )
        .execute()
        .data[0]
    )
    conversation_ids.append(individual_one["id"])
    individual_two = (
        sb.table("conversations")
        .insert(
            {
                "company_id": fixture_ids["company_id"],
                "user_id": fixture_ids["user_b"],
                "project_id": project["id"],
                "kind": "individual",
            },
        )
        .execute()
        .data[0]
    )
    conversation_ids.append(individual_two["id"])


def test_memory_xor_both_set_rejected(sb, fixture_ids, project):
    from postgrest.exceptions import APIError

    with pytest.raises(APIError) as exc:
        sb.table("project_memory_entries").insert(
            {
                "project_id": project["id"],
                "body": "both provenance fields set",
                "author_user_id": fixture_ids["user_a"],
                "promoted_by": "agent",
            },
        ).execute()
    assert _api_error_code(exc.value) == "23514"
    assert "pme_one_provenance" in str(exc.value)


def test_memory_xor_neither_set_rejected(sb, project):
    from postgrest.exceptions import APIError

    with pytest.raises(APIError) as exc:
        sb.table("project_memory_entries").insert(
            {"project_id": project["id"], "body": "no provenance field set"},
        ).execute()
    assert _api_error_code(exc.value) == "23514"
    assert "pme_one_provenance" in str(exc.value)


def test_existing_conversation_defaults(sb):
    """A row that predates this migration set reads back the column
    defaults untouched: `project_id=NULL, kind='individual'`."""
    rows = (
        sb.table("conversations")
        .select("id,project_id,kind")
        .order("id")
        .limit(1)
        .execute()
        .data
    )
    assert rows, "expected at least one pre-existing conversation row in the rig"
    assert rows[0]["project_id"] is None
    assert rows[0]["kind"] == "individual"


# ── Edge cases ───────────────────────────────────────────────────────────


def test_project_delete_cascades(sb, fixture_ids):
    proj = (
        sb.table("projects")
        .insert(
            {
                "company_id": fixture_ids["company_id"],
                "workspace_id": fixture_ids["workspace_id"],
                "name": f"cascade-test-{uuid.uuid4().hex[:8]}",
                "created_by": fixture_ids["user_a"],
            },
        )
        .execute()
        .data[0]
    )
    pid = proj["id"]

    sb.table("project_members").insert({"project_id": pid, "user_id": fixture_ids["user_a"]}).execute()
    sb.table("project_artifacts").insert(
        {"project_id": pid, "artifact_type": "prototype", "artifact_id": 1},
    ).execute()
    sb.table("project_memory_entries").insert(
        {"project_id": pid, "body": "x", "author_user_id": fixture_ids["user_a"]},
    ).execute()
    sb.table("project_memory_summary").insert(
        {"project_id": pid, "summary_md": "x", "entry_count": 1},
    ).execute()

    conv = (
        sb.table("conversations")
        .insert(
            {
                "company_id": fixture_ids["company_id"],
                "user_id": fixture_ids["user_a"],
                "project_id": pid,
                "kind": "group",
            },
        )
        .execute()
        .data[0]
    )
    cid = conv["id"]
    sb.table("project_chat_members").insert(
        {"conversation_id": cid, "user_id": fixture_ids["user_a"]},
    ).execute()

    # Delete the project itself.
    sb.table("projects").delete().eq("id", pid).execute()

    assert sb.table("project_members").select("*").eq("project_id", pid).execute().data == []
    assert sb.table("project_artifacts").select("*").eq("project_id", pid).execute().data == []
    assert sb.table("project_memory_entries").select("*").eq("project_id", pid).execute().data == []
    assert sb.table("project_memory_summary").select("*").eq("project_id", pid).execute().data == []

    # The conversation itself is NOT deleted — only unlinked from the project.
    conv_after = (
        sb.table("conversations").select("id,project_id").eq("id", cid).execute().data
    )
    assert conv_after == [{"id": cid, "project_id": None}]

    # project_chat_members is untouched by the project delete: it cascades
    # from the CONVERSATION's own deletion, not the project's.
    assert (
        sb.table("project_chat_members").select("*").eq("conversation_id", cid).execute().data
        != []
    )

    # Deleting the conversation now cascades project_chat_members (the
    # "via conversation cascade" half of the invariant).
    sb.table("conversations").delete().eq("id", cid).execute()
    assert sb.table("project_chat_members").select("*").eq("conversation_id", cid).execute().data == []


def test_workspace_id_uuid_not_text(sb, fixture_ids):
    from postgrest.exceptions import APIError

    # No default: omitting workspace_id fails NOT NULL, it is never silently
    # filled in (there is no `aud`/session-derived default on this column).
    with pytest.raises(APIError) as no_ws:
        sb.table("projects").insert(
            {
                "company_id": fixture_ids["company_id"],
                "name": "no-workspace",
                "created_by": fixture_ids["user_a"],
            },
        ).execute()
    assert _api_error_code(no_ws.value) == "23502"

    # uuid, not text: an arbitrary non-uuid string is rejected at the type
    # level (a TEXT column would have accepted it and only the (nonexistent)
    # FK lookup would fail).
    with pytest.raises(APIError) as bad_type:
        sb.table("projects").insert(
            {
                "company_id": fixture_ids["company_id"],
                "workspace_id": "not-a-uuid-text-value",
                "name": "bad-workspace-type",
                "created_by": fixture_ids["user_a"],
            },
        ).execute()
    assert _api_error_code(bad_type.value) == "22P02"
