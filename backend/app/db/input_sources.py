"""CRUD for the enterprise_input_sources table.

Each row records a data-source type (csv_upload, google_drive, figma, …)
and whether it is enabled for a given dataset/company.  The ``config``
JSONB holds source-specific settings (folder_id, project_id, etc.).
"""
from __future__ import annotations

from typing import Any

from app.db.client import require_client, utc_now


def list_input_sources(dataset: str) -> list[dict[str, Any]]:
    """Return all input-source rows for *dataset*, ordered by source_type."""
    c = require_client()
    resp = (
        c.table("enterprise_input_sources")
        .select("*")
        .eq("dataset", dataset)
        .order("source_type")
        .execute()
    )
    return resp.data or []


def upsert_input_source(
    dataset: str,
    source_type: str,
    enabled: bool = True,
    config: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Create or update an input-source row.  Returns the upserted row."""
    c = require_client()
    row = {
        "dataset": dataset,
        "source_type": source_type,
        "enabled": enabled,
        "config": config or {},
        "updated_at": utc_now(),
    }
    resp = (
        c.table("enterprise_input_sources")
        .upsert(row, on_conflict="dataset,source_type")
        .execute()
    )
    return (resp.data or [{}])[0]


def delete_input_source(dataset: str, source_type: str) -> bool:
    """Delete a single input-source row.  Returns True if a row was removed."""
    c = require_client()
    resp = (
        c.table("enterprise_input_sources")
        .delete()
        .eq("dataset", dataset)
        .eq("source_type", source_type)
        .execute()
    )
    return bool(resp.data)
