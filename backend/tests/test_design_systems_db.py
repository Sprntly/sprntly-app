"""Tests for the `design_systems` cache DB helpers + migration schema.

Runs fully in isolation against the in-memory FakeSupabaseClient — no live
Supabase required. We reuse conftest's `isolated_settings` fixture for env +
module-reload + fake-client wiring, then add the new table to the already-seeded
in-memory DB so we never touch the shared test scaffolding (`conftest.py` /
`_fake_supabase.py`).

The migration's Postgres-only constructs (uuid PK, jsonb, timestamptz, RLS) are
translated/omitted in the SQLite DDL below the same way conftest does for the
existing tables — the fake exercises SQL semantics (unique key, upsert), and the
RLS policy is verified at the migration-text level.
"""
from __future__ import annotations

import importlib
import re
from pathlib import Path

import pytest

# SQLite-compatible translation of
# supabase/migrations/20260607000001_design_systems.sql.
_DESIGN_SYSTEMS_DDL = """
CREATE TABLE design_systems (
    id                  TEXT PRIMARY KEY,
    company_id          TEXT NOT NULL REFERENCES companies (id) ON DELETE CASCADE,
    source_category     TEXT NOT NULL,
    source_provider     TEXT NOT NULL,
    source_ref          TEXT NOT NULL,
    source_version      TEXT,
    data                TEXT NOT NULL DEFAULT '{}',
    has_explicit_system INTEGER,
    confidence          TEXT,
    status              TEXT NOT NULL DEFAULT 'active',
    extracted_at        TEXT,
    created_at          TEXT NOT NULL DEFAULT (datetime('now')),
    updated_at          TEXT NOT NULL DEFAULT (datetime('now')),
    UNIQUE (company_id, source_provider, source_ref)
);
CREATE INDEX design_systems_company_id_idx ON design_systems (company_id);
"""

_MIGRATION_PATH = (
    Path(__file__).resolve().parents[2]
    / "supabase" / "migrations" / "20260607000001_design_systems.sql"
)


@pytest.fixture
def design_systems_db(isolated_settings, monkeypatch):
    """The reloaded app.db.design_systems module wired to the fake Supabase, with
    the new table present and its jsonb `data` column registered so it round-trips
    as a real dict."""
    from tests import _fake_supabase

    _fake_supabase.get_fake_db().executescript(_DESIGN_SYSTEMS_DDL)
    # The fake only translates columns it knows about: register the jsonb `data`
    # column so it round-trips as a real dict, and the boolean column so it comes
    # back as a real bool (Postgres surfaces these as bool; SQLite stores 0/1).
    monkeypatch.setitem(
        _fake_supabase._JSONB_COLUMNS, "design_systems", {"data"}
    )
    monkeypatch.setitem(
        _fake_supabase._BOOL_COLUMNS, "design_systems", {"has_explicit_system"}
    )

    import app.db.design_systems as ds_mod
    importlib.reload(ds_mod)  # rebind require_client/utc_now from the reloaded client
    return ds_mod


def _seed_company(slug: str = "acme") -> str:
    from app.db.client import require_client
    import uuid

    cid = uuid.uuid4().hex
    require_client().table("companies").insert(
        {"id": cid, "slug": slug, "display_name": slug.title()}
    ).execute()
    return cid


# ─── Migration file content (string-level — isolation-friendly) ──────────


def _migration_sql_only() -> str:
    """Migration content with `--` line comments stripped, lowercased."""
    lines = []
    for line in _MIGRATION_PATH.read_text().splitlines():
        lines.append(line.split("--", 1)[0])
    return "\n".join(lines).lower()


def test_migration_file_exists():
    assert _MIGRATION_PATH.exists()
    assert _MIGRATION_PATH.name == "20260607000001_design_systems.sql"


def test_migration_is_idempotent_by_construction():
    sql = _migration_sql_only()
    for m in re.finditer(r"create\s+table\s+(?!if\s+not\s+exists)", sql):
        pytest.fail(f"non-idempotent CREATE TABLE near offset {m.start()}")
    for m in re.finditer(r"create\s+index\s+(?!if\s+not\s+exists)", sql):
        pytest.fail(f"non-idempotent CREATE INDEX near offset {m.start()}")
    # The policy must be dropped before it's recreated.
    assert "drop policy if exists design_systems_member_all" in sql


def test_migration_has_company_scoped_for_all_rls_policy():
    sql = _migration_sql_only()
    assert "enable row level security" in sql
    # A FOR ALL policy (reads AND writes), scoped through company membership.
    assert "for all" in sql
    assert "company_members.company_id = design_systems.company_id" in sql
    assert "company_members.user_id" in sql and "auth.uid()" in sql


def test_migration_has_cache_key_unique_constraint():
    sql = _migration_sql_only()
    assert re.search(
        r"unique\s*\(\s*company_id\s*,\s*source_provider\s*,\s*source_ref\s*\)", sql
    ), "missing the (company_id, source_provider, source_ref) cache-key unique constraint"


# ─── Helper round-trip behaviour ─────────────────────────────────────────


def test_upsert_then_lookup_round_trip(design_systems_db):
    company_id = _seed_company()
    data = {"confidence": "high", "tokens": {"is_dark": True}}

    stored = design_systems_db.upsert_design_system(
        company_id=company_id,
        source_category="design_tool",
        source_provider="figma",
        source_ref="file-key-123",
        source_version="2026-06-07T00:00:00Z",
        data=data,
        has_explicit_system=True,
        confidence="high",
        extracted_at="2026-06-07T00:00:00Z",
    )
    assert stored["company_id"] == company_id
    assert stored["source_provider"] == "figma"
    assert stored["data"] == data  # jsonb round-trips as a real dict

    loaded = design_systems_db.lookup_design_system(company_id, "figma", "file-key-123")
    assert loaded is not None
    assert loaded["id"] == stored["id"]
    assert loaded["confidence"] == "high"
    assert loaded["has_explicit_system"] is True


def test_lookup_miss_returns_none(design_systems_db):
    company_id = _seed_company()
    assert design_systems_db.lookup_design_system(company_id, "figma", "nope") is None


def test_cache_key_is_company_provider_ref(design_systems_db):
    """Re-extracting the same (company, provider, ref) overwrites in place; a
    different provider OR ref is a distinct cache entry."""
    company_id = _seed_company()

    first = design_systems_db.upsert_design_system(
        company_id=company_id,
        source_category="design_tool",
        source_provider="figma",
        source_ref="file-key-123",
        source_version="v1",
        data={"confidence": "low"},
        has_explicit_system=False,
        confidence="low",
        extracted_at=None,
    )
    # Same triple → same row, updated in place.
    second = design_systems_db.upsert_design_system(
        company_id=company_id,
        source_category="design_tool",
        source_provider="figma",
        source_ref="file-key-123",
        source_version="v2",
        data={"confidence": "high"},
        has_explicit_system=True,
        confidence="high",
        extracted_at=None,
    )
    assert second["id"] == first["id"]
    assert second["source_version"] == "v2"
    assert second["confidence"] == "high"

    # Different ref → a separate cache entry.
    other_ref = design_systems_db.upsert_design_system(
        company_id=company_id,
        source_category="website",
        source_provider="web",
        source_ref="https://example.com",
        source_version="etag-1",
        data={},
        has_explicit_system=False,
        confidence="medium",
        extracted_at=None,
    )
    assert other_ref["id"] != first["id"]


def test_lookup_is_scoped_to_company(design_systems_db):
    co_a = _seed_company("acme")
    co_b = _seed_company("globex")
    design_systems_db.upsert_design_system(
        company_id=co_b,
        source_category="design_tool",
        source_provider="figma",
        source_ref="shared-key",
        source_version=None,
        data={},
        has_explicit_system=None,
        confidence=None,
        extracted_at=None,
    )
    # Company A must not see company B's cached system under the same provider/ref.
    assert design_systems_db.lookup_design_system(co_a, "figma", "shared-key") is None
    assert design_systems_db.lookup_design_system(co_b, "figma", "shared-key") is not None


# ─── mark_github_design_systems_stale ────────────────────────────────────


def _seed_github_row(design_systems_db, company_id: str, source_ref: str) -> str:
    """Seed a github-provider row and return its id."""
    row = design_systems_db.upsert_design_system(
        company_id=company_id,
        source_category="codebase",
        source_provider="github",
        source_ref=source_ref,
        source_version="sha-abc",
        data={},
        has_explicit_system=False,
        confidence="low",
        extracted_at=None,
    )
    return row["id"]


def test_mark_codebase_github_design_systems_stale_matches_repo_and_branch(design_systems_db):
    """The helper marks bare 'owner/repo' rows AND 'owner/repo@branch' rows stale
    while leaving rows for a different repo and rows from a non-github provider
    untouched. Returns the count of rows that were marked."""
    from app.db.design_systems import mark_github_design_systems_stale

    company_id = _seed_company("mark-stale-co")

    # Bare repo match (no branch qualifier).
    _seed_github_row(design_systems_db, company_id, "owner/repo")
    # Branch-qualified variant of the same repo.
    _seed_github_row(design_systems_db, company_id, "owner/repo@main")
    # A different repo — must NOT be marked stale.
    _seed_github_row(design_systems_db, company_id, "owner/other")
    # A non-github provider with the same ref string — must NOT be affected.
    design_systems_db.upsert_design_system(
        company_id=company_id,
        source_category="design_tool",
        source_provider="figma",
        source_ref="owner/repo",
        source_version=None,
        data={},
        has_explicit_system=None,
        confidence=None,
        extracted_at=None,
    )

    count = mark_github_design_systems_stale("owner/repo")
    assert count == 2

    # The two matched rows must now carry status='stale'.
    bare = design_systems_db.lookup_design_system(company_id, "github", "owner/repo")
    branch = design_systems_db.lookup_design_system(company_id, "github", "owner/repo@main")
    assert bare["status"] == "stale"
    assert branch["status"] == "stale"

    # The unrelated repo stays active.
    other = design_systems_db.lookup_design_system(company_id, "github", "owner/other")
    assert other["status"] == "active"

    # The non-github row is unaffected.
    figma_row = design_systems_db.lookup_design_system(company_id, "figma", "owner/repo")
    assert figma_row["status"] == "active"


def test_mark_codebase_github_design_systems_stale_empty_name_noops(design_systems_db):
    """Calling the helper with an empty repo name returns 0 and touches nothing."""
    from app.db.design_systems import mark_github_design_systems_stale

    assert mark_github_design_systems_stale("") == 0
    assert mark_github_design_systems_stale("   ") == 0
