"""Tests for the Design Agent sharing helpers (P2-06).

Sibling to `test_db_prototypes.py` (kept separate so the P2-06 diff stays focused
per the sizing rule). Covers `set_share_config`, `find_prototype_by_share_token`,
`hash_share_passcode` / `verify_share_passcode`, the in-memory passcode
rate-limit primitive, the `DESIGN_AGENT_TOKEN_SECRET` env binding, and the new
migration's idempotency / CHECK-constraint shape.

Runs fully in isolation against the in-memory FakeSupabaseClient (no live
Supabase). We reuse conftest's `isolated_settings` fixture for env + module
reload + fake-client wiring, then create the `prototypes` table with the five
P2-06 columns already present (the SQLite fake cannot run Postgres's
multi-column `ALTER TABLE ... ADD COLUMN IF NOT EXISTS`, so — exactly as
`test_db_prototypes.py` does — we translate the end-state DDL and verify the
migration's idempotency at the SQL-string level).
"""
from __future__ import annotations

import importlib
import logging
import re
import sqlite3
import uuid
from pathlib import Path

import pytest

# SQLite-compatible end-state of `prototypes` AFTER the P1-06 migration and the
# P2-06 sharing migration: the base columns plus share_mode / share_token /
# share_passcode_hash / is_complete / complete_checkpoint_id. The CHECK + UNIQUE
# constraints are inlined so the fake enforces the same semantics Postgres will.
_PROTOTYPE_DDL = """
CREATE TABLE prototypes (
    id                     INTEGER PRIMARY KEY AUTOINCREMENT,
    prd_id                 INTEGER,
    workspace_id           TEXT NOT NULL,
    status                 TEXT NOT NULL DEFAULT 'generating',
    variant                TEXT NOT NULL DEFAULT 'v1',
    template_version       INTEGER NOT NULL,
    instructions           TEXT,
    target_platform        TEXT NOT NULL DEFAULT 'both',
    figma_file_key         TEXT,
    website_url            TEXT,
    github_installation_id INTEGER,
    bundle_url             TEXT,
    current_checkpoint_id  INTEGER,
    error                  TEXT,
    created_at             TEXT NOT NULL DEFAULT (datetime('now')),
    completed_at           TEXT,
    share_mode             TEXT NOT NULL DEFAULT 'private'
                           CHECK (share_mode IN ('private', 'public', 'passcode')),
    share_token            TEXT UNIQUE,
    share_passcode_hash    TEXT,
    is_complete            INTEGER NOT NULL DEFAULT 0,
    complete_checkpoint_id INTEGER
);
CREATE TABLE prototype_checkpoints (
    id                INTEGER PRIMARY KEY AUTOINCREMENT,
    prototype_id      INTEGER NOT NULL,
    workspace_id      TEXT NOT NULL,
    bundle_url        TEXT,
    prd_revision_hash TEXT,
    figma_frame_hash  TEXT,
    prompt_history    TEXT NOT NULL DEFAULT '[]',
    comment_state     TEXT NOT NULL DEFAULT '[]',
    created_at        TEXT NOT NULL DEFAULT (datetime('now'))
);
"""

_MIGRATION_PATH = (
    Path(__file__).resolve().parents[2]
    / "supabase" / "migrations" / "20260530000000_design_agent_sharing.sql"
)


@pytest.fixture
def proto(isolated_settings, monkeypatch):
    """The reloaded app.db.prototypes module wired to the fake Supabase, with the
    prototypes + prototype_checkpoints tables present (sharing columns included).
    The reload also resets the module-level passcode rate-limit state per test.
    """
    from tests import _fake_supabase

    _fake_supabase.get_fake_db().executescript(_PROTOTYPE_DDL)

    import app.db.prototypes as proto_mod
    importlib.reload(proto_mod)  # rebind require_client/utc_now + fresh rate-limit state
    return proto_mod


# ─── Migration file (string-level — isolation-friendly, no live Postgres) ──


def _migration_raw() -> str:
    return _MIGRATION_PATH.read_text()


def _migration_sql_only() -> str:
    """Migration content with `--` line comments stripped, lowercased."""
    lines = []
    for line in _migration_raw().splitlines():
        code = line.split("--", 1)[0]
        lines.append(code)
    return "\n".join(lines).lower()


def test_migration_file_exists_and_is_dated_correctly():
    assert _MIGRATION_PATH.exists()
    assert _MIGRATION_PATH.name == "20260530000000_design_agent_sharing.sql"


def test_migration_applies_idempotently():
    # AC #1 — structural idempotency (apply-twice is verified at the SQL-string
    # level here; a live-Postgres apply-twice is deferred to a phase smoke, same
    # convention as test_db_prototypes.test_migration_applies_idempotently).
    sql = _migration_sql_only()
    for m in re.finditer(r"add\s+column\s+(?!if\s+not\s+exists)", sql):
        pytest.fail(f"non-idempotent ADD COLUMN near offset {m.start()}")
    for m in re.finditer(r"create\s+index\s+(?!if\s+not\s+exists)", sql):
        pytest.fail(f"non-idempotent CREATE INDEX near offset {m.start()}")
    # Both added constraints must be preceded by a DROP CONSTRAINT IF EXISTS.
    for name in ("prototypes_complete_checkpoint_id_fkey", "prototypes_share_mode_check"):
        assert f"drop constraint if exists {name}" in sql, f"{name} not dropped-before-add"
        assert f"add constraint {name}" in sql
        assert sql.index(f"drop constraint if exists {name}") < sql.index(f"add constraint {name}")


def test_migration_declares_all_five_columns():
    # AC #2 — the five columns + their key attributes are present in the migration.
    sql = _migration_sql_only()
    for col in (
        "share_mode", "share_token", "share_passcode_hash",
        "is_complete", "complete_checkpoint_id",
    ):
        assert col in sql, f"migration missing column {col}"
    assert "share_mode             text    not null default 'private'" in sql
    assert "is_complete            boolean not null default false" in sql
    assert "references prototype_checkpoints(id)" in sql
    assert "on delete set null" in sql
    # share_token must be unique (opacity primitive).
    assert "share_token            uuid    unique" in sql


def test_migration_check_constraint_lists_three_modes():
    # AC #2 — the CHECK constraint constrains share_mode to exactly the 3 modes.
    sql = _migration_sql_only()
    assert "check (share_mode in ('private', 'public', 'passcode'))" in sql


def test_share_mode_check_constraint_rejects_invalid(proto):
    # AC #2 — inserting share_mode='broken' raises a DB integrity error.
    from tests import _fake_supabase
    db = _fake_supabase.get_fake_db()
    with pytest.raises(sqlite3.IntegrityError):
        db.execute(
            "INSERT INTO prototypes (prd_id, workspace_id, template_version, share_mode) "
            "VALUES (?, ?, ?, ?)",
            [1, "app", 1, "broken"],
        )


# ─── Creation (set_share_config) ───────────────────────────────────────────


def _seed(proto, *, workspace_id="app") -> int:
    return proto.start_prototype(prd_id=1, workspace_id=workspace_id, template_version=1)


def test_start_prototype_mints_share_token(proto):
    # Static-URL invariant: every prototype is BORN with a share_token (share_mode
    # stays its 'private' default so the token is present but not yet exposed). This
    # is what lets set_share_config flip the mode without ever rotating the token.
    pid = _seed(proto)
    row = proto.get_prototype(prototype_id=pid, workspace_id="app")
    assert row is not None
    assert row["share_token"] is not None
    assert row["share_mode"] == "private"  # token present but not exposed yet
    uuid.UUID(str(row["share_token"]))     # a valid UUID


def test_set_share_config_public_generates_uuid_token(proto):
    # AC #3
    pid = _seed(proto)
    row = proto.set_share_config(prototype_id=pid, workspace_id="app", share_mode="public")
    assert row["share_mode"] == "public"
    token = row["share_token"]
    assert token is not None
    # A valid UUID, and NOT derived from prototype_id.
    parsed = uuid.UUID(str(token))
    assert str(parsed) != str(pid)
    assert row["share_passcode_hash"] is None


def test_set_share_config_passcode_stores_argon2_hash(proto):
    # AC #4
    pid = _seed(proto)
    row = proto.set_share_config(
        prototype_id=pid, workspace_id="app", share_mode="passcode", passcode="hunter2",
    )
    assert row["share_mode"] == "passcode"
    assert row["share_token"] is not None
    assert row["share_passcode_hash"].startswith("$argon2id$")
    assert proto.verify_share_passcode("hunter2", row["share_passcode_hash"]) is True


def test_set_share_config_private_preserves_token_and_nulls_hash(proto):
    # Static-URL invariant: going private PRESERVES the share_token (the URL is
    # stable across toggles) and only clears the passcode hash.
    pid = _seed(proto)
    # Make it public first so there is a token in place.
    pub = proto.set_share_config(prototype_id=pid, workspace_id="app", share_mode="public")
    token = pub["share_token"]
    row = proto.set_share_config(prototype_id=pid, workspace_id="app", share_mode="private")
    assert row["share_mode"] == "private"
    assert row["share_token"] is not None       # preserved, not nulled
    assert row["share_token"] == token          # same token, not rotated
    assert row["share_passcode_hash"] is None


def test_set_share_config_token_stable_across_public_private_toggles(proto):
    # One stable URL across toggles: public (mint T) → private (keep T) →
    # public again (still T). The /p/<slug>/<token> URL never changes.
    pid = _seed(proto)
    token = proto.set_share_config(
        prototype_id=pid, workspace_id="app", share_mode="public"
    )["share_token"]
    assert token is not None
    after_private = proto.set_share_config(
        prototype_id=pid, workspace_id="app", share_mode="private"
    )
    assert after_private["share_token"] == token
    after_public_again = proto.set_share_config(
        prototype_id=pid, workspace_id="app", share_mode="public"
    )
    assert after_public_again["share_token"] == token


def test_set_share_config_public_idempotent_preserves_token(proto):
    # AC #3 / F7 — re-setting public→public does NOT rotate the token.
    pid = _seed(proto)
    first = proto.set_share_config(prototype_id=pid, workspace_id="app", share_mode="public")
    second = proto.set_share_config(prototype_id=pid, workspace_id="app", share_mode="public")
    assert first["share_token"] == second["share_token"]


def test_set_share_config_invalid_mode_raises_valueerror(proto):
    pid = _seed(proto)
    with pytest.raises(ValueError):
        proto.set_share_config(prototype_id=pid, workspace_id="app", share_mode="broken")


def test_set_share_config_passcode_without_passcode_raises_valueerror(proto):
    pid = _seed(proto)
    with pytest.raises(ValueError):
        proto.set_share_config(
            prototype_id=pid, workspace_id="app", share_mode="passcode", passcode=None,
        )


# ─── Retrieval (find_prototype_by_share_token) ─────────────────────────────


def test_find_by_share_token_returns_row(proto):
    # AC #5
    pid = _seed(proto)
    configured = proto.set_share_config(prototype_id=pid, workspace_id="app", share_mode="public")
    token = configured["share_token"]
    found = proto.find_prototype_by_share_token(token)
    assert found is not None
    assert found["id"] == pid


def test_find_by_share_token_returns_none_for_missing(proto):
    # AC #5 — a random UUID with no row → None (this is what makes /p/<uuid>
    # return 404 not 401).
    assert proto.find_prototype_by_share_token(str(uuid.uuid4())) is None


def test_find_by_share_token_cross_workspace(proto):
    # AC #5 / AC #12 — row seeded under workspace 'app', looked up with NO
    # workspace context, is returned. This is the intentional F6 access primitive.
    pid = _seed(proto, workspace_id="app")
    configured = proto.set_share_config(
        prototype_id=pid, workspace_id="app", share_mode="passcode", passcode="pw",
    )
    token = configured["share_token"]
    found = proto.find_prototype_by_share_token(token)  # no workspace arg at all
    assert found is not None
    assert found["id"] == pid
    assert found["workspace_id"] == "app"
    # And the standard workspace-filtered helper still enforces isolation:
    assert proto.get_prototype(prototype_id=pid, workspace_id="demo") is None


# ─── Hashing (hash_share_passcode / verify_share_passcode) ─────────────────


def test_hash_share_passcode_empty_raises(proto):
    with pytest.raises(ValueError):
        proto.hash_share_passcode("")


def test_verify_share_passcode_correct_returns_true(proto):
    h = proto.hash_share_passcode("correct horse")
    assert proto.verify_share_passcode("correct horse", h) is True


def test_verify_share_passcode_wrong_returns_false(proto):
    h = proto.hash_share_passcode("correct horse")
    assert proto.verify_share_passcode("battery staple", h) is False


def test_verify_share_passcode_none_hash_returns_false(proto):
    # AC #4 — never raises on a missing hash.
    assert proto.verify_share_passcode("anything", None) is False


def test_verify_share_passcode_malformed_hash_returns_false(proto):
    # Never raises on a garbage / non-argon2 hash.
    assert proto.verify_share_passcode("anything", "garbage-not-a-hash") is False


# ─── Rate limit (in-memory token bucket) ───────────────────────────────────


def test_rate_limit_initially_allows(proto):
    assert proto.passcode_rate_limit_check(token="fresh", ip="1.2.3.4") is True


def test_rate_limit_blocks_after_5_failures(proto):
    # AC #6
    for _ in range(5):
        proto.passcode_rate_limit_register_failure(token="t")
    assert proto.passcode_rate_limit_check(token="t", ip="1.2.3.4") is False


def test_rate_limit_clears_failures_on_clear(proto):
    # AC #6
    for _ in range(5):
        proto.passcode_rate_limit_register_failure(token="t")
    assert proto.passcode_rate_limit_check(token="t", ip="1.2.3.4") is False
    proto.passcode_rate_limit_clear(token="t")
    assert proto.passcode_rate_limit_check(token="t", ip="1.2.3.4") is True


def test_rate_limit_window_expires(proto, monkeypatch):
    # AC #6 — failures older than the 60s window are pruned.
    clock = {"t": 1000.0}
    monkeypatch.setattr(proto.time, "monotonic", lambda: clock["t"])
    for _ in range(5):
        proto.passcode_rate_limit_register_failure(token="t")
    assert proto.passcode_rate_limit_check(token="t", ip="1.2.3.4") is False
    clock["t"] += 61  # advance past the window
    assert proto.passcode_rate_limit_check(token="t", ip="1.2.3.4") is True


def test_rate_limit_per_token_isolation(proto):
    for _ in range(5):
        proto.passcode_rate_limit_register_failure(token="A")
    assert proto.passcode_rate_limit_check(token="A", ip="1.2.3.4") is False
    assert proto.passcode_rate_limit_check(token="B", ip="1.2.3.4") is True


# ─── Env binding (DESIGN_AGENT_TOKEN_SECRET) ───────────────────────────────


def test_design_agent_token_secret_bound_from_env(monkeypatch):
    # AC #8 — bound at startup from the env var; env var wins over any .env value.
    monkeypatch.setenv("DESIGN_AGENT_TOKEN_SECRET", "test-token-secret-value")
    import app.config as config_mod
    importlib.reload(config_mod)
    try:
        assert config_mod.settings.design_agent_token_secret == "test-token-secret-value"
    finally:
        # Restore the default-bound settings so later tests see a clean config.
        monkeypatch.delenv("DESIGN_AGENT_TOKEN_SECRET", raising=False)
        importlib.reload(config_mod)


# ─── AC7 guard: no JWT_SECRET reuse; token secret not consumed here ────────


def _module_source() -> str:
    return (
        Path(__file__).resolve().parents[1] / "app" / "db" / "prototypes.py"
    ).read_text()


def test_no_jwt_secret_reference_in_module():
    # AC #7 — zero JWT_SECRET / jwt_secret hits in db/prototypes.py.
    src = _module_source()
    assert "JWT_SECRET" not in src
    assert "jwt_secret" not in src


def test_token_secret_not_consumed_in_module():
    # AC #7 — at most ONE design_agent_token_secret reference (zero is expected:
    # P2-06 binds the secret in config.py but does not consume it in this module).
    src = _module_source()
    assert src.count("design_agent_token_secret") <= 1


# ─── Observability (AC #13) ────────────────────────────────────────────────


def test_set_share_config_logs_mode_no_secrets(proto, caplog):
    # AC #13 — INFO line with mode; NO passcode plaintext and NO share_token.
    pid = _seed(proto)
    secret_passcode = "SUPER_SECRET_PASSCODE_VALUE"
    with caplog.at_level(logging.INFO, logger="app.db.prototypes"):
        row = proto.set_share_config(
            prototype_id=pid, workspace_id="app",
            share_mode="passcode", passcode=secret_passcode,
        )
    blob = "\n".join(r.getMessage() for r in caplog.records)
    assert "prototype_share_configured" in blob
    assert f"prototype_id={pid}" in blob
    assert "mode=passcode" in blob
    assert secret_passcode not in blob
    assert str(row["share_token"]) not in blob


# ─── Edge cases ────────────────────────────────────────────────────────────


def test_set_share_config_for_nonexistent_prototype_raises(proto):
    with pytest.raises(ValueError):
        proto.set_share_config(prototype_id=999999, workspace_id="app", share_mode="public")


def test_set_share_config_wrong_workspace_raises(proto):
    # Row under 'app'; calling with workspace='demo' → get_prototype returns None
    # upstream, so set_share_config raises "not found in workspace".
    pid = _seed(proto, workspace_id="app")
    with pytest.raises(ValueError):
        proto.set_share_config(prototype_id=pid, workspace_id="demo", share_mode="public")
