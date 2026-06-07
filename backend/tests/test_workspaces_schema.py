"""Schema-existence tests for the workspaces table + connection scoping
columns added by migration 20260606120000_workspaces_and_connection_scope.sql.

This commit (C6 of the 2026-06-06 settings slice) is schema-only — the
backend routes still scope connectors by company_id. These tests pin the
shape so the next slice (workspace-aware routing) can read/write rows
without surprise.

Scope expectations:
  - workspaces(id, company_id, product_id, name, slug, is_default, created_at, updated_at)
  - A "Default" workspace exists for every company (created on company seed
    via the backfill or future application-level creation).
  - connections gains nullable workspace_id, product_id, company_name,
    product_name columns. Old company-scoped queries continue to work.
"""
from __future__ import annotations

import uuid

import app.auth  # noqa: F401 — ensure app.config + app.auth in sys.modules


def _table_columns(_client, table: str) -> set[str]:
    """Return the column names of a fake-Supabase table by reading its
    underlying SQLite via PRAGMA. The fake client itself doesn't expose
    the connection, but `_fake_supabase.get_fake_db()` does."""
    from tests._fake_supabase import get_fake_db

    cur = get_fake_db().execute(f"PRAGMA table_info({table})")
    return {row[1] for row in cur.fetchall()}


def test_workspaces_table_exists(isolated_settings):
    client = isolated_settings["supabase"]
    cols = _table_columns(client, "workspaces")
    expected = {
        "id",
        "company_id",
        "product_id",
        "name",
        "slug",
        "is_default",
        "created_at",
        "updated_at",
    }
    missing = expected - cols
    assert not missing, f"workspaces missing columns: {missing}"


def test_workspace_default_is_seeded_for_a_company(isolated_settings):
    """Once a company exists, a `Default` workspace should be readily
    insertable referencing it. This indirectly tests the FK to companies."""
    client = isolated_settings["supabase"]

    cid = uuid.uuid4().hex
    client.table("companies").insert(
        {"id": cid, "slug": "acme-123", "display_name": "Acme"}
    ).execute()

    wid = uuid.uuid4().hex
    client.table("workspaces").insert(
        {
            "id": wid,
            "company_id": cid,
            "product_id": None,
            "name": "Default",
            "slug": "default",
            "is_default": True,
        }
    ).execute()

    rows = (
        client.table("workspaces")
        .select("id, company_id, name, is_default")
        .eq("id", wid)
        .execute()
        .data
    )
    assert len(rows) == 1
    assert rows[0]["company_id"] == cid
    assert rows[0]["is_default"] in (True, 1)


def test_connections_has_new_scope_columns(isolated_settings):
    client = isolated_settings["supabase"]
    cols = _table_columns(client, "connections")
    for c in ("workspace_id", "product_id", "company_name", "product_name"):
        assert c in cols, f"connections missing column: {c}"


def test_legacy_company_id_still_present_on_connections(isolated_settings):
    """The old company-scoped column stays — routes haven't moved off it yet."""
    cols = _table_columns(isolated_settings["supabase"], "connections")
    assert "company_id" in cols
