"""Schema tests for the multitenant connections migration (commit 1).

`connections` used to have a global UNIQUE on (provider), so only one
Figma/GitHub/etc. could exist across the entire installation — letting
one workspace see another's connector tokens. Commit 1 re-keys
uniqueness to (workspace_id, provider) with a hard FK to companies(id).

These tests go through the raw Supabase client rather than
`db.upsert_connection`, because the DB helper doesn't accept
workspace_id yet — commit 2 lands that. After commit 2 the higher-level
tenant-isolation tests will live in test_db_connections.py.
"""
from __future__ import annotations

import sqlite3
import uuid

import pytest

from app.db.client import require_client


def _seed_company(slug: str) -> str:
    cid = uuid.uuid4().hex
    require_client().table("companies").insert({
        "id": cid,
        "slug": slug,
        "display_name": slug.title(),
    }).execute()
    return cid


def _insert_connection(workspace_id: str | None, provider: str = "figma") -> None:
    row: dict = {
        "id": uuid.uuid4().hex,
        "provider": provider,
        "token_json_encrypted": "x",
    }
    if workspace_id is not None:
        row["workspace_id"] = workspace_id
    require_client().table("connections").insert(row).execute()


def test_workspace_id_is_required(isolated_settings):
    _seed_company("acme")  # at least one valid company so the FK target table is populated
    with pytest.raises(sqlite3.IntegrityError):
        _insert_connection(workspace_id=None)


def test_same_provider_in_two_workspaces_is_allowed(isolated_settings):
    ws1 = _seed_company("acme")
    ws2 = _seed_company("globex")
    _insert_connection(ws1, "figma")
    _insert_connection(ws2, "figma")  # must not raise


def test_duplicate_workspace_provider_pair_is_rejected(isolated_settings):
    ws = _seed_company("acme")
    _insert_connection(ws, "figma")
    with pytest.raises(sqlite3.IntegrityError):
        _insert_connection(ws, "figma")


def test_workspace_id_must_reference_existing_company(isolated_settings):
    """FK enforced — can't attach a connection to a nonexistent company."""
    with pytest.raises(sqlite3.IntegrityError):
        _insert_connection(workspace_id=uuid.uuid4().hex)
