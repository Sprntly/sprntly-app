"""DB helpers for the knowledge graph (entities + relationships)."""
from __future__ import annotations

from typing import Any

from app.db.client import require_client, utc_now


def upsert_entity(
    dataset: str,
    entity_type: str,
    name: str,
    attributes: dict | None = None,
    source_file: str | None = None,
    confidence: float = 0.5,
) -> dict[str, Any]:
    """Insert or update a knowledge entity. Returns the row."""
    c = require_client()
    row = {
        "dataset": dataset,
        "entity_type": entity_type,
        "name": name,
        "attributes": attributes or {},
        "source_file": source_file,
        "confidence": confidence,
        "updated_at": utc_now(),
    }
    resp = c.table("knowledge_entities").upsert(
        row, on_conflict="dataset,entity_type,name",
    ).execute()
    return resp.data[0] if resp.data else row


def upsert_relationship(
    dataset: str,
    source_entity_id: int,
    target_entity_id: int,
    relation: str,
    weight: float = 1.0,
    evidence: str | None = None,
) -> dict[str, Any]:
    """Insert or update a knowledge relationship. Returns the row."""
    c = require_client()
    row = {
        "dataset": dataset,
        "source_entity_id": source_entity_id,
        "target_entity_id": target_entity_id,
        "relation": relation,
        "weight": weight,
        "evidence": evidence,
    }
    resp = c.table("knowledge_relationships").upsert(
        row, on_conflict="dataset,source_entity_id,target_entity_id,relation",
    ).execute()
    return resp.data[0] if resp.data else row


def list_entities(dataset: str) -> list[dict[str, Any]]:
    c = require_client()
    resp = c.table("knowledge_entities").select("*").eq("dataset", dataset).execute()
    return resp.data or []


def list_relationships(dataset: str) -> list[dict[str, Any]]:
    c = require_client()
    resp = c.table("knowledge_relationships").select("*").eq("dataset", dataset).execute()
    return resp.data or []


def clear_entities(dataset: str) -> None:
    """Remove all entities (and cascade relationships) for a dataset."""
    c = require_client()
    c.table("knowledge_entities").delete().eq("dataset", dataset).execute()
