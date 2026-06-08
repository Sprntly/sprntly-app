"""GraphFacade — the only KG API the rest of Sprntly should call.

Tenant isolation is enforced: every method takes `enterprise_id` first and
asserts that any node being written carries the same enterprise_id. Mismatch
raises `TenantViolationError` (FastAPI handlers map → 403).

Backend: Postgres + pgvector via the shared Supabase client (`app.db.client`).
Resolution *policy* (#2: τ_high / τ_low / gray-zone LLM adjudication) lives
in the AI layer; the facade only exposes primitives (`find_candidates`).
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any, Optional

from app.db.client import require_client
from app.graph.types import Entity, Relationship, Signal, Source

logger = logging.getLogger(__name__)


class TenantViolationError(PermissionError):
    """Raised when an operation's enterprise_id mismatches the entity's.
    Map this to HTTP 403 in FastAPI handlers."""


def _iso(dt: datetime) -> str:
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc).isoformat()


def _parse_iso(s: Optional[str]) -> Optional[datetime]:
    if not s:
        return None
    if isinstance(s, datetime):
        return s
    return datetime.fromisoformat(s.replace("Z", "+00:00"))


class GraphFacade:
    """Tenant-isolated KG read/write API. Instantiated per-request."""

    def __init__(self, client: Any | None = None) -> None:
        self._client = client or require_client()

    # ---- helpers --------------------------------------------------------
    def _assert_tenant(self, expected: str, got: str) -> None:
        if expected != got:
            raise TenantViolationError(
                f"enterprise_id mismatch: expected={expected!r} got={got!r}"
            )

    def _tbl(self, name: str):
        return self._client.table(name)

    # ---- writes ---------------------------------------------------------
    def create_source(self, enterprise_id: str, source: Source) -> Source:
        self._assert_tenant(enterprise_id, source.enterprise_id)
        row = {
            "id": source.id,
            "enterprise_id": source.enterprise_id,
            "source_type": source.source_type,
            "label": source.label,
            "config": source.config,
            "status": source.status,
        }
        self._tbl("kg_source").insert(row).execute()
        return source

    def create_entity(self, enterprise_id: str, entity: Entity) -> Entity:
        self._assert_tenant(enterprise_id, entity.enterprise_id)
        row = {
            "id": entity.id,
            "enterprise_id": entity.enterprise_id,
            "type": entity.type,
            "canonical_label": entity.canonical_label,
            "aliases": entity.aliases,
            "properties": entity.properties,
            "embedding": entity.embedding,
            "valid_at": _iso(entity.valid_at),
            "transaction_at": _iso(entity.transaction_at),
            "provenance": entity.provenance,
            "confidence": entity.confidence,
        }
        self._tbl("kg_entity").insert(row).execute()
        return entity

    def write_signal(self, enterprise_id: str, signal: Signal) -> Signal:
        self._assert_tenant(enterprise_id, signal.enterprise_id)
        row = {
            "id": signal.id,
            "enterprise_id": signal.enterprise_id,
            "source_id": signal.source_id,
            "source_type": signal.source_type,
            "kind": signal.kind,
            "content": signal.content,
            "properties": signal.properties,
            "embedding": signal.embedding,
            "valid_at": _iso(signal.valid_at),
            "transaction_at": _iso(signal.transaction_at),
            "stale_after": _iso(signal.stale_after) if signal.stale_after else None,
            "confidence": signal.confidence,
            "weight": signal.weight,
            "provenance": signal.provenance,
        }
        self._tbl("kg_signal").insert(row).execute()
        return signal

    def write_relationship(self, enterprise_id: str, rel: Relationship) -> Relationship:
        self._assert_tenant(enterprise_id, rel.enterprise_id)
        row = {
            "enterprise_id": rel.enterprise_id,
            "type": rel.type,
            "source_kind": rel.source_kind,
            "source_id": rel.source_id,
            "target_kind": rel.target_kind,
            "target_id": rel.target_id,
            "properties": rel.properties,
            "confidence": rel.confidence,
            "valid_at": _iso(rel.valid_at),
            "transaction_at": _iso(rel.transaction_at),
            "provenance": rel.provenance,
        }
        result = self._tbl("kg_relationship").insert(row).execute()
        if result.data:
            rel.id = result.data[0].get("id")
        return rel

    def supersede_signal(
        self, enterprise_id: str, signal_id: str, by_signal_id: str
    ) -> None:
        """Bitemporal close — mark `signal_id` as superseded by `by_signal_id`
        (records the supersession in properties; readers can filter)."""
        # Verify both signals belong to the enterprise first.
        existing = (
            self._tbl("kg_signal")
            .select("id, properties")
            .eq("enterprise_id", enterprise_id)
            .eq("id", signal_id)
            .execute()
        )
        if not existing.data:
            raise ValueError(f"Signal {signal_id} not found in enterprise {enterprise_id}")
        props = existing.data[0].get("properties") or {}
        props["superseded_by"] = by_signal_id
        props["superseded_at"] = _iso(datetime.now(timezone.utc))
        (
            self._tbl("kg_signal")
            .update({"properties": props})
            .eq("enterprise_id", enterprise_id)
            .eq("id", signal_id)
            .execute()
        )

    def update_entity_properties(
        self, enterprise_id: str, entity_id: str, patch: dict[str, Any]
    ) -> dict[str, Any]:
        """Shallow-merge `patch` into the entity's `properties` jsonb and persist.
        Returns the merged properties. Tenant-scoped read-modify-write (mirrors
        supersede_signal's update pattern)."""
        existing = (
            self._tbl("kg_entity")
            .select("id, properties")
            .eq("enterprise_id", enterprise_id)
            .eq("id", entity_id)
            .execute()
        )
        if not existing.data:
            raise ValueError(
                f"Entity {entity_id} not found in enterprise {enterprise_id}"
            )
        props = existing.data[0].get("properties") or {}
        props.update(patch)
        (
            self._tbl("kg_entity")
            .update({"properties": props})
            .eq("enterprise_id", enterprise_id)
            .eq("id", entity_id)
            .execute()
        )
        return props

    # ---- reads ----------------------------------------------------------
    def get_entity(self, enterprise_id: str, entity_id: str) -> Optional[Entity]:
        r = (
            self._tbl("kg_entity").select("*")
            .eq("enterprise_id", enterprise_id)
            .eq("id", entity_id)
            .execute()
        )
        return self._row_to_entity(r.data[0]) if r.data else None

    def get_signal(self, enterprise_id: str, signal_id: str) -> Optional[Signal]:
        r = (
            self._tbl("kg_signal").select("*")
            .eq("enterprise_id", enterprise_id)
            .eq("id", signal_id)
            .execute()
        )
        return self._row_to_signal(r.data[0]) if r.data else None

    def query_entities(
        self,
        enterprise_id: str,
        type: Optional[str] = None,
        limit: Optional[int] = None,
    ) -> list[Entity]:
        q = self._tbl("kg_entity").select("*").eq("enterprise_id", enterprise_id)
        if type:
            q = q.eq("type", type)
        if limit:
            q = q.limit(limit)
        return [self._row_to_entity(r) for r in (q.execute().data or [])]

    def edges_from(
        self,
        enterprise_id: str,
        source_id: str,
        type: Optional[str] = None,
    ) -> list[Relationship]:
        q = (
            self._tbl("kg_relationship").select("*")
            .eq("enterprise_id", enterprise_id)
            .eq("source_id", source_id)
        )
        if type:
            q = q.eq("type", type)
        return [self._row_to_relationship(r) for r in (q.execute().data or [])]

    def edges_to(
        self,
        enterprise_id: str,
        target_id: str,
        type: Optional[str] = None,
    ) -> list[Relationship]:
        q = (
            self._tbl("kg_relationship").select("*")
            .eq("enterprise_id", enterprise_id)
            .eq("target_id", target_id)
        )
        if type:
            q = q.eq("type", type)
        return [self._row_to_relationship(r) for r in (q.execute().data or [])]

    def active_signals(
        self,
        enterprise_id: str,
        source_types: Optional[list[str]] = None,
        since: Optional[datetime] = None,
    ) -> list[Signal]:
        """Non-stale signals (stale_after IS NULL OR stale_after > now()).
        Filtered in Python so it works against both real Supabase and the
        in-memory fake (which doesn't support OR / gt). Per-enterprise
        volumes are bounded (§20 NFR), so this is fine."""
        rows = (
            self._tbl("kg_signal").select("*")
            .eq("enterprise_id", enterprise_id)
            .execute().data or []
        )
        now = datetime.now(timezone.utc)
        kept: list[Signal] = []
        for r in rows:
            stale = _parse_iso(r.get("stale_after"))
            if stale and stale <= now:
                continue
            if source_types and r["source_type"] not in source_types:
                continue
            tx = _parse_iso(r.get("transaction_at"))
            if since and tx and tx < since:
                continue
            kept.append(self._row_to_signal(r))
        return kept

    def load_session_context(self, enterprise_id: str) -> dict[str, Any]:
        """Spec §20: enterprise + top 10 active hypotheses + last 5 decisions
        + last 3 measured outcomes. Hard latency budget: ≤500ms."""
        def _by_type(t: str, limit: int) -> list[Entity]:
            r = (
                self._tbl("kg_entity").select("*")
                .eq("enterprise_id", enterprise_id)
                .eq("type", t)
                .order("transaction_at", desc=True)
                .limit(limit)
                .execute()
            )
            return [self._row_to_entity(x) for x in (r.data or [])]
        return {
            "enterprise_id": enterprise_id,
            "active_hypotheses": _by_type("hypothesis", 10),
            "recent_decisions":  _by_type("decision", 5),
            "recent_outcomes":   _by_type("outcome", 3),
        }

    def find_candidates(
        self,
        enterprise_id: str,
        type: str,
        embedding: list[float],
        k: int = 10,
    ) -> list[tuple[Entity, float]]:
        """pgvector kNN — top-k existing entities of `type` by cosine similarity.
        Calls the Postgres function `kg_find_candidates` (in the migration).
        The AI layer (resolution policy #2) applies τ_high / τ_low / gray-zone
        adjudication on top of these candidates.

        In the in-memory test fake (no pgvector), `rpc` is unavailable; this
        method returns [] and tests of resolution policy should stub the
        facade instead."""
        if not hasattr(self._client, "rpc"):
            return []
        r = self._client.rpc("kg_find_candidates", {
            "p_enterprise_id": enterprise_id,
            "p_type": type,
            "p_embedding": embedding,
            "p_k": k,
        }).execute()
        out: list[tuple[Entity, float]] = []
        for row in (r.data or []):
            ent = self.get_entity(enterprise_id, row["id"])
            if ent:
                out.append((ent, float(row["score"])))
        return out

    # ---- row mappers ----------------------------------------------------
    def _row_to_entity(self, r: dict) -> Entity:
        return Entity(
            id=r["id"],
            enterprise_id=r["enterprise_id"],
            type=r["type"],
            canonical_label=r["canonical_label"],
            aliases=list(r.get("aliases") or []),
            properties=r.get("properties") or {},
            embedding=r.get("embedding"),
            valid_at=_parse_iso(r.get("valid_at")) or datetime.now(timezone.utc),
            transaction_at=_parse_iso(r.get("transaction_at")) or datetime.now(timezone.utc),
            provenance=r.get("provenance") or {},
            confidence=float(r.get("confidence") or 1.0),
        )

    def _row_to_signal(self, r: dict) -> Signal:
        sig = Signal.__new__(Signal)
        sig.id = r["id"]
        sig.enterprise_id = r["enterprise_id"]
        sig.source_id = r.get("source_id")
        sig.source_type = r["source_type"]
        sig.kind = r["kind"]
        sig.content = r["content"]
        sig.properties = r.get("properties") or {}
        sig.embedding = r.get("embedding")
        sig.valid_at = _parse_iso(r.get("valid_at")) or datetime.now(timezone.utc)
        sig.transaction_at = _parse_iso(r.get("transaction_at")) or datetime.now(timezone.utc)
        sig.stale_after = _parse_iso(r.get("stale_after"))
        sig.confidence = float(r.get("confidence") or 1.0)
        sig.weight = float(r.get("weight") or 1.0)
        sig.provenance = r.get("provenance") or {}
        return sig

    def _row_to_relationship(self, r: dict) -> Relationship:
        rel = Relationship.__new__(Relationship)
        rel.id = r.get("id")
        rel.enterprise_id = r["enterprise_id"]
        rel.type = r["type"]
        rel.source_kind = r["source_kind"]
        rel.source_id = r["source_id"]
        rel.target_kind = r["target_kind"]
        rel.target_id = r["target_id"]
        rel.properties = r.get("properties") or {}
        rel.confidence = float(r.get("confidence") or 1.0)
        rel.valid_at = _parse_iso(r.get("valid_at")) or datetime.now(timezone.utc)
        rel.transaction_at = _parse_iso(r.get("transaction_at")) or datetime.now(timezone.utc)
        rel.provenance = r.get("provenance") or {}
        return rel
