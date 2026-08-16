"""The `conversation_turns` idempotency migration (owned-write dedup spine).

Deterministic proofs (no real Postgres needed, mirrors
`test_ask_jobs_active_attempt_migration.py`'s own split): the migration is
additive / nullable / partial / `if not exists` with NO table rewrite and NO
status/CHECK/role-default change, and the two partial-uniques enforce
exactly one row per `(conversation_id, role, client_message_id)` / `(...,
role, ask_job_id)` while leaving every existing NULL-keyed row untouched.

`conversation_turns` is a prod-shared table (main + group + individual chat
all write it); the real `CREATE UNIQUE INDEX` lock-window / idempotent
double-apply against a DB seeded with pre-existing rows is proven on the
live rig in Live Verification and by the ship-gate verifier (addendum §2 —
migration = blast-radius, not full-suite).
"""
from __future__ import annotations

import pathlib
import sqlite3

import pytest

from app.db.workspaces import ensure_default_workspace

_MIGRATIONS_DIR = pathlib.Path(__file__).resolve().parents[2] / "supabase" / "migrations"
_MIGRATION_FILE = "20260816140000_conversation_turns_idempotency.sql"


def _migration_sql() -> str:
    return (_MIGRATIONS_DIR / _MIGRATION_FILE).read_text().lower()


def _migration_sql_statements_only() -> str:
    """The migration text with `--` comment lines stripped — so a prose
    comment explaining "no default" doesn't trip a literal `"default"`
    substring check on the actual SQL statements."""
    return "\n".join(
        line for line in _migration_sql().splitlines() if not line.strip().startswith("--")
    )


def test_idempotency_columns_additive_no_rewrite():
    """AC11: nullable columns, no default, `if not exists` throughout; no
    CHECK/status/role-default change, no table rewrite."""
    sql = _migration_sql()
    assert "add column if not exists client_message_id text" in sql
    assert "add column if not exists ask_job_id bigint" in sql
    assert "create unique index if not exists conversation_turns_client_msg_uidx" in sql
    assert "create unique index if not exists conversation_turns_ask_job_uidx" in sql
    assert "on public.conversation_turns (conversation_id, role, client_message_id)" in sql
    assert "on public.conversation_turns (conversation_id, role, ask_job_id)" in sql
    assert "where client_message_id is not null" in sql
    assert "where ask_job_id is not null" in sql
    # No volatile default — a default on ADD COLUMN forces a full rewrite of
    # a live, prod-shared table. Checked against the STATEMENTS only, since
    # the header prose legitimately discusses "no default" in English.
    statements = _migration_sql_statements_only()
    assert "default" not in statements
    # No rewrite / status / CHECK / role-default touch.
    for forbidden in ("drop table", "drop column", "check (", "alter column", "role text"):
        assert forbidden not in statements, forbidden


def test_client_msg_partial_unique_enforces_one_row(tenant_client, isolated_settings):
    """AC4: a second row for the SAME (conversation_id, role,
    client_message_id) is rejected; NULL-keyed rows never collide."""
    t = tenant_client.make(slug="acme")
    from app.db.client import require_client
    from app.db import conversations as conversations_db

    conv = conversations_db.create_individual_project_chat(
        _seed_project(t, isolated_settings), t.user_id,
    )
    client = require_client()
    client.table("conversation_turns").insert(
        {"conversation_id": conv["id"], "role": "user", "content": "one", "client_message_id": "dup"}
    ).execute()
    with pytest.raises(sqlite3.IntegrityError):
        client.table("conversation_turns").insert(
            {"conversation_id": conv["id"], "role": "user", "content": "two", "client_message_id": "dup"}
        ).execute()

    # Many NULL-keyed rows (the pre-existing brief/group-turn shape) never
    # collide with each other or with a real key.
    client.table("conversation_turns").insert(
        {"conversation_id": conv["id"], "role": "user", "content": "no key A"}
    ).execute()
    client.table("conversation_turns").insert(
        {"conversation_id": conv["id"], "role": "user", "content": "no key B"}
    ).execute()


def test_ask_job_partial_unique_enforces_one_answer(tenant_client, isolated_settings):
    """AC4: same enforcement on the (conversation_id, role='assistant',
    ask_job_id) key — the `/v1/ask` answer's durable run link."""
    t = tenant_client.make(slug="acme")
    from app.db.client import require_client
    from app.db import conversations as conversations_db

    conv = conversations_db.create_individual_project_chat(
        _seed_project(t, isolated_settings), t.user_id,
    )
    client = require_client()
    client.table("conversation_turns").insert(
        {"conversation_id": conv["id"], "role": "assistant", "content": "v1", "ask_job_id": 100}
    ).execute()
    with pytest.raises(sqlite3.IntegrityError):
        client.table("conversation_turns").insert(
            {"conversation_id": conv["id"], "role": "assistant", "content": "v2", "ask_job_id": 100}
        ).execute()


def test_client_msg_unique_dropped_double_write_is_red(tenant_client, isolated_settings):
    """MUTATION PROOF (AC4, PI13): drop the partial-unique index and prove a
    concurrent double-submit creates TWO user rows for the same key (RED);
    restoring the index (the migration's actual, unmutated shape) makes the
    identical sequence raise instead (GREEN) — proven by the two tests above
    already running against the real, unmutated schema."""
    t = tenant_client.make(slug="acme")
    from app.db.client import require_client
    from app.db import conversations as conversations_db
    from tests._fake_supabase import get_fake_db

    conv = conversations_db.create_individual_project_chat(
        _seed_project(t, isolated_settings), t.user_id,
    )
    client = require_client()
    db = get_fake_db()

    # RED: drop the migration's own index — the "invariant broken" state.
    db.execute("DROP INDEX IF EXISTS conversation_turns_client_msg_uidx")
    db.commit()
    client.table("conversation_turns").insert(
        {"conversation_id": conv["id"], "role": "user", "content": "one", "client_message_id": "dup-red"}
    ).execute()
    client.table("conversation_turns").insert(
        {"conversation_id": conv["id"], "role": "user", "content": "two", "client_message_id": "dup-red"}
    ).execute()
    rows = (
        client.table("conversation_turns").select("id").eq("conversation_id", conv["id"])
        .eq("client_message_id", "dup-red").execute().data
    )
    assert len(rows) == 2, "mutation setup: dropping the index must let a duplicate through"

    # Clear the RED-mode duplicate before restoring the index (CREATE UNIQUE
    # INDEX validates existing data — the mutated-broken rows must not
    # survive into the GREEN half, same as reverting the flip in any other
    # mutation proof).
    db.execute(
        "DELETE FROM conversation_turns WHERE conversation_id = ? AND client_message_id = ?",
        (conv["id"], "dup-red"),
    )
    db.commit()

    # GREEN: restore the migration's index and prove the SAME sequence now
    # raises — one row per key, DB-enforced.
    db.execute(
        "CREATE UNIQUE INDEX conversation_turns_client_msg_uidx "
        "ON conversation_turns (conversation_id, role, client_message_id) "
        "WHERE client_message_id IS NOT NULL"
    )
    db.commit()
    with pytest.raises(sqlite3.IntegrityError):
        client.table("conversation_turns").insert(
            {"conversation_id": conv["id"], "role": "user", "content": "three", "client_message_id": "dup-green"}
        ).execute()
        client.table("conversation_turns").insert(
            {"conversation_id": conv["id"], "role": "user", "content": "four", "client_message_id": "dup-green"}
        ).execute()


def _seed_project(t, isolated_settings, *, name: str = "Migration project") -> int:
    from app.db import projects as projects_db

    ws_id = ensure_default_workspace(t.company_id)["id"]
    return projects_db.create_project(
        company_id=t.company_id, workspace_id=ws_id, name=name, created_by=t.user_id,
    )["id"]
