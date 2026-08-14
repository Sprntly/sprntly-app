"""Tests for the assignee-awareness unread badge (inputs-only read cursor,
AD-P3/AD-P20): `conversation_read_cursors` migration + `db/conversation_read_
cursors.py` + the two `GET/POST /v1/projects/{id}/individual/unread|read`
routes.

Split, mirroring the wave's established two-tier pattern:

  - Real local-Supabase round-trip (`RUN_CONVERSATION_READ_CURSORS_
    ROUNDTRIP`, mirrors `test_project_delegations.py`): the migration's PK
    shape, RLS + policy, and a `set_cursor`/`get_cursor` round trip against
    the REAL Postgres — the fake-Supabase tier has no SQL engine behind it
    and cannot enforce a composite PK or an RLS/policy catalog lookup.
  - Fake-Supabase blast-radius tier (mirrors `test_individual_turns.py`):
    unread derivation, read-clears-unread, advance-only clamping, the own-
    cursor isolation gate (mutation-proofed RED->GREEN), and the membership
    gate — all logic that does not depend on a real SQL engine.

Run the real-DB tier with:

    RUN_CONVERSATION_READ_CURSORS_ROUNDTRIP=1 \\
        pytest tests/test_conversation_read_cursors.py -m integration
"""
from __future__ import annotations

import os
import pathlib
import subprocess
import uuid

import pytest

from tests._company_helpers import company_client
from tests._project_helpers import seed_same_tenant_non_member

# ── Real local-Supabase round-trip ────────────────────────────────────────

_RUN_LIVE = os.getenv("RUN_CONVERSATION_READ_CURSORS_ROUNDTRIP") == "1"

_MIGRATIONS_DIR = pathlib.Path(__file__).resolve().parents[2] / "supabase" / "migrations"
_MIGRATION_FILE = "20260813130400_conversation_read_cursors.sql"
_DB_CONTAINER = os.getenv("PROJECTS_SCHEMA_TEST_DB_CONTAINER", "supabase_db_Sprntly")


def _client():
    from app.config import settings
    from supabase import create_client

    url = settings.supabase_url
    assert "127.0.0.1" in url or "localhost" in url, (
        "refusing to run the live read-cursor round-trip against a non-loopback "
        f"SUPABASE_URL ({url!r}) — this test mutates real rows"
    )
    assert settings.supabase_service_role_key, "SUPABASE_SERVICE_ROLE_KEY is not set"
    return create_client(url, settings.supabase_service_role_key)


@pytest.fixture(scope="module")
def sb():
    if not _RUN_LIVE:
        pytest.skip("live tier disabled")
    return _client()


@pytest.fixture(scope="module")
def fixture_ids(sb):
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
    row = (
        sb.table("projects")
        .insert(
            {
                "company_id": fixture_ids["company_id"],
                "workspace_id": fixture_ids["workspace_id"],
                "name": f"read-cursor-roundtrip-{uuid.uuid4().hex[:8]}",
                "created_by": fixture_ids["user_a"],
            }
        )
        .execute()
        .data[0]
    )
    yield row
    sb.table("projects").delete().eq("id", row["id"]).execute()


@pytest.fixture
def conversation(sb, fixture_ids, project):
    row = (
        sb.table("conversations")
        .insert(
            {
                "company_id": fixture_ids["company_id"],
                "user_id": fixture_ids["user_a"],
                "project_id": project["id"],
                "kind": "individual",
            }
        )
        .execute()
        .data[0]
    )
    yield row
    sb.table("conversation_read_cursors").delete().eq("conversation_id", row["id"]).execute()
    sb.table("conversations").delete().eq("id", row["id"]).execute()


@pytest.mark.integration
@pytest.mark.skipif(not _RUN_LIVE, reason=(
    "needs a real local Supabase — set RUN_CONVERSATION_READ_CURSORS_ROUNDTRIP=1 "
    "with SUPABASE_URL/SUPABASE_SERVICE_ROLE_KEY pointed at the local rig and the "
    "conversation_read_cursors migration applied"
))
def test_cursor_migration_idempotent_and_rls():
    """Double-apply of the actual committed SQL is a documented no-op; RLS
    is enabled and the server-role policy exists; the PK is the composite
    (conversation_id, user_id) — all real-Postgres-only proofs (AC1)."""
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

    rls_result = subprocess.run(
        [
            "docker", "exec", _DB_CONTAINER,
            "psql", "-U", "postgres", "-d", "postgres", "-tAc",
            "select relrowsecurity from pg_class where relname='conversation_read_cursors';",
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
            "select policyname from pg_policies where tablename='conversation_read_cursors';",
        ],
        capture_output=True,
        timeout=15,
    )
    assert policy_result.returncode == 0, policy_result.stderr.decode(errors="replace")
    assert policy_result.stdout.decode().strip() == "srv_conversation_read_cursors"

    pk_result = subprocess.run(
        [
            "docker", "exec", _DB_CONTAINER,
            "psql", "-U", "postgres", "-d", "postgres", "-tAc",
            "select a.attname from pg_index i "
            "join pg_attribute a on a.attrelid = i.indrelid and a.attnum = any(i.indkey) "
            "where i.indrelid = 'conversation_read_cursors'::regclass and i.indisprimary "
            "order by a.attname;",
        ],
        capture_output=True,
        timeout=15,
    )
    assert pk_result.returncode == 0, pk_result.stderr.decode(errors="replace")
    pk_cols = {line.strip() for line in pk_result.stdout.decode().splitlines() if line.strip()}
    assert pk_cols == {"conversation_id", "user_id"}


def test_get_set_cursor_round_trip(sb, fixture_ids, project, conversation):
    """`set_cursor` upserts against the real DB; `get_cursor` reads it back;
    a caller with no cursor row yet defaults to 0 (AC2/AC4)."""
    from app.db.conversation_read_cursors import get_cursor, set_cursor

    conv_id = conversation["id"]
    user_id = fixture_ids["user_a"]

    assert get_cursor(conv_id, user_id) == 0

    updated = set_cursor(conv_id, user_id, 42)
    assert updated["last_read_turn_id"] == 42
    assert get_cursor(conv_id, user_id) == 42

    reread = (
        sb.table("conversation_read_cursors")
        .select("*")
        .eq("conversation_id", conv_id)
        .eq("user_id", user_id)
        .execute()
        .data[0]
    )
    assert reread["last_read_turn_id"] == 42
    assert reread["updated_at"] is not None


# ── Fake-Supabase blast-radius tier ────────────────────────────────────────


def _create_project(ctx, *, name: str = "Read-cursor project") -> dict:
    return ctx.client.post("/v1/projects", json={"name": name}).json()


def _open_individual_chat(ctx, project_id) -> dict:
    """Get-or-create the caller's individual chat + post one turn into it,
    mirroring how a real delivered brief lands."""
    from app.db import conversations as conversations_db

    conv = conversations_db.create_individual_project_chat(project_id, ctx.user_id)
    conversations_db.post_individual_turn(conv["id"], "assistant", "delivered brief")
    return conv


def test_unread_true_when_turn_beyond_cursor(isolated_settings, monkeypatch):
    """A delivered turn with id > cursor makes `unread_for` true (AC2/AC3)."""
    ctx = company_client(monkeypatch)
    project = _create_project(ctx)
    conv = _open_individual_chat(ctx, project["id"])

    from app.db.conversation_read_cursors import unread_for

    assert unread_for(conv["id"], ctx.user_id) is True

    r = ctx.client.get(f"/v1/projects/{project['id']}/individual/unread")
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["unread"] is True
    assert body["latest_turn_id"] is not None
    assert body["last_read_turn_id"] == 0


def test_unread_false_on_empty_conversation(isolated_settings, monkeypatch):
    """No turns at all → unread is false, even with no cursor row (AC2)."""
    ctx = company_client(monkeypatch)
    project = _create_project(ctx)

    from app.db import conversations as conversations_db
    from app.db.conversation_read_cursors import unread_for

    conv = conversations_db.create_individual_project_chat(project["id"], ctx.user_id)
    assert unread_for(conv["id"], ctx.user_id) is False

    r = ctx.client.get(f"/v1/projects/{project['id']}/individual/unread")
    assert r.status_code == 200, r.text
    assert r.json() == {"unread": False, "latest_turn_id": None, "last_read_turn_id": 0}

    # And with no conversation created at all yet — same zero-state, no 404.
    project2 = _create_project(ctx, name="No chat opened yet")
    r2 = ctx.client.get(f"/v1/projects/{project2['id']}/individual/unread")
    assert r2.status_code == 200, r2.text
    assert r2.json() == {"unread": False, "latest_turn_id": None, "last_read_turn_id": 0}


def test_read_clears_unread(isolated_settings, monkeypatch):
    """POST /individual/read advances the cursor to the latest turn; a
    subsequent GET /individual/unread returns unread: false (AC4)."""
    ctx = company_client(monkeypatch)
    project = _create_project(ctx)
    conv = _open_individual_chat(ctx, project["id"])

    r_before = ctx.client.get(f"/v1/projects/{project['id']}/individual/unread")
    assert r_before.json()["unread"] is True

    r_read = ctx.client.post(f"/v1/projects/{project['id']}/individual/read")
    assert r_read.status_code == 200, r_read.text

    from app.db.conversation_read_cursors import get_cursor, latest_individual_turn_id

    assert get_cursor(conv["id"], ctx.user_id) == latest_individual_turn_id(conv["id"])

    r_after = ctx.client.get(f"/v1/projects/{project['id']}/individual/unread")
    assert r_after.status_code == 200, r_after.text
    assert r_after.json()["unread"] is False


def test_read_route_no_conversation_yet(isolated_settings, monkeypatch):
    """POST /individual/read with no individual chat created yet — a no-op
    zero-state, never a 500/404 (not having opened the chat is legitimate)."""
    ctx = company_client(monkeypatch)
    project = _create_project(ctx)

    r = ctx.client.post(f"/v1/projects/{project['id']}/individual/read")
    assert r.status_code == 200, r.text
    assert r.json() == {"last_read_turn_id": 0}


def test_set_cursor_advance_only(isolated_settings, monkeypatch):
    """Posting an older/stale latest id never moves the cursor backward —
    `max(existing, new)` (AC5)."""
    ctx = company_client(monkeypatch)
    project = _create_project(ctx)
    conv = _open_individual_chat(ctx, project["id"])

    from app.db.conversation_read_cursors import get_cursor, set_cursor

    latest = get_cursor(conv["id"], ctx.user_id)  # 0, no cursor yet
    advanced = set_cursor(conv["id"], ctx.user_id, 999)
    assert advanced["last_read_turn_id"] == 999

    # A stale re-post carrying an OLDER id (e.g. a race with a slow client)
    # must not move the cursor back down.
    stale = set_cursor(conv["id"], ctx.user_id, 1)
    assert stale["last_read_turn_id"] == 999
    assert get_cursor(conv["id"], ctx.user_id) == 999
    assert latest == 0  # sanity: our baseline really was the pre-cursor state


# ── Isolation (mutation-proofed — AC6/AC7, load-bearing) ──────────────────


def test_cursor_isolated_per_user(isolated_settings, monkeypatch):
    """User A advancing their own cursor never changes user B's unread for
    the SAME conversation-owning project's chats — each user's individual
    chat + cursor is fully separate (AC6)."""
    ctx = company_client(monkeypatch)
    project = _create_project(ctx)

    from app.db import conversations as conversations_db
    from app.db import projects as projects_db
    from app.db.client import require_client
    from app.db.conversation_read_cursors import unread_for

    require_client().table("profiles").insert(
        {"id": "member-b", "email": "b@co.com"}
    ).execute()
    projects_db.add_member(project["id"], "member-b")

    conv_a = conversations_db.create_individual_project_chat(project["id"], ctx.user_id)
    conversations_db.post_individual_turn(conv_a["id"], "assistant", "A's brief")

    conv_b = conversations_db.create_individual_project_chat(project["id"], "member-b")
    conversations_db.post_individual_turn(conv_b["id"], "assistant", "B's brief")

    assert unread_for(conv_a["id"], ctx.user_id) is True
    assert unread_for(conv_b["id"], "member-b") is True

    # A reads/advances only A's own cursor via the route.
    r = ctx.client.post(f"/v1/projects/{project['id']}/individual/read")
    assert r.status_code == 200, r.text

    assert unread_for(conv_a["id"], ctx.user_id) is False
    # B's unread state is completely untouched by A's read.
    assert unread_for(conv_b["id"], "member-b") is True


def test_unread_endpoint_membership_gated(isolated_settings, monkeypatch):
    """Same-tenant non-member gets 403; cross-tenant caller gets 404
    (existence not disclosed) — for both endpoints (AC7)."""
    ctx = company_client(monkeypatch)
    project = _create_project(ctx)
    _, non_member_headers = seed_same_tenant_non_member(ctx)

    r_unread = ctx.client.get(
        f"/v1/projects/{project['id']}/individual/unread", headers=non_member_headers
    )
    assert r_unread.status_code == 403

    r_read = ctx.client.post(
        f"/v1/projects/{project['id']}/individual/read", headers=non_member_headers
    )
    assert r_read.status_code == 403

    from app.db import projects as projects_db

    foreign = projects_db.create_project(
        company_id="foreign-co", workspace_id="foreign-ws", name="Not mine",
        created_by="someone-else",
    )
    assert ctx.client.get(f"/v1/projects/{foreign['id']}/individual/unread").status_code == 404
    assert ctx.client.post(f"/v1/projects/{foreign['id']}/individual/read").status_code == 404


def test_endpoints_ignore_client_supplied_ids(isolated_settings, monkeypatch):
    """Neither endpoint accepts a client-supplied `user_id`/`conversation_id`
    — they always resolve the caller's OWN individual conversation via
    `ctx.user_id`. This is the RED/GREEN mutation proof: an UNGATED stand-in
    read (identical query, minus the owner-gate clause) DOES return another
    user's unread state for their conversation_id; the real route, given the
    exact same query-string injection attempt, ignores it entirely and
    returns the caller's own (different) state (AC7)."""
    ctx = company_client(monkeypatch)
    project = _create_project(ctx)

    from app.db import conversations as conversations_db
    from app.db import projects as projects_db
    from app.db.client import require_client
    from app.db.conversation_read_cursors import latest_individual_turn_id

    require_client().table("profiles").insert(
        {"id": "member-b", "email": "b@co.com"}
    ).execute()
    projects_db.add_member(project["id"], "member-b")

    conv_b = conversations_db.create_individual_project_chat(project["id"], "member-b")
    conversations_db.post_individual_turn(conv_b["id"], "assistant", "B's private brief")

    # RED: an ungated stand-in — the identical "latest turn for this
    # conversation_id" computation, with no ownership check at all. It DOES
    # resolve B's real unread turn, proving B's data genuinely exists and
    # would leak if the route accepted a client id.
    assert latest_individual_turn_id(conv_b["id"]) is not None

    # GREEN: the real route, sent B's conversation_id as an (unsupported,
    # ignored) query param — it resolves ctx.user_id's OWN conversation
    # (which doesn't exist yet), never conv_b.
    r = ctx.client.get(
        f"/v1/projects/{project['id']}/individual/unread",
        params={"conversation_id": conv_b["id"], "user_id": "member-b"},
    )
    assert r.status_code == 200, r.text
    assert r.json() == {"unread": False, "latest_turn_id": None, "last_read_turn_id": 0}

    r_read = ctx.client.post(
        f"/v1/projects/{project['id']}/individual/read",
        params={"conversation_id": conv_b["id"], "user_id": "member-b"},
    )
    assert r_read.status_code == 200, r_read.text
    assert r_read.json() == {"last_read_turn_id": 0}

    # B's cursor is completely untouched by A's attempted injection.
    from app.db.conversation_read_cursors import get_cursor

    assert get_cursor(conv_b["id"], "member-b") == 0
