"""Knowledge Graph types — Entity / Signal / Relationship / Source records.

Matches contract S3 in `~/sprntly-shared-contracts.md`. Embedding is `list[float]`
in Python; persisted as `vector(1536)` in Postgres (pgvector).

`Signal.stale_after` is auto-computed from `source_type` per the #1 staleness
window table (KG_Engineering_Spec §3.2.1, locked 2026-05-28). `outcome_measured`
signals never expire (stale_after stays None).
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Optional
import uuid


# Closed relationship vocabulary (S3). Novel relationships from the extractor
# must be bucketed into RELATES_TO and flagged for human vocab review.
RELATIONSHIP_VOCAB: frozenset[str] = frozenset({
    "SUPPORTS", "CONTRADICTS", "ADDRESSES", "BLOCKED_BY", "AFFECTS",
    "REQUESTS", "PRESSURES", "SERVES", "IMPACTS", "ON", "PART_OF",
    "PROMOTED_TO", "EXPRESSED_AS", "VISUALIZES", "RESULTED_IN",
    "VALIDATES", "UPDATES_WEIGHT", "IMPLEMENTS", "REALIZES",
    "SCOPED_TO", "INFORMS", "RELATES_TO",
})

# Reserved entity types — the §2 decision/learning ledger spine. These carry
# extra required props in `properties` validated at the application layer.
RESERVED_ENTITY_TYPES: frozenset[str] = frozenset({
    "hypothesis", "decision", "outcome", "artifact",
})

# Signal source_type enum (must match the DB CHECK constraint in the migration).
SIGNAL_SOURCE_TYPES: frozenset[str] = frozenset({
    "analytics", "project_mgmt", "communication", "customer_voice", "revenue",
    "verbal_claim", "pm_manual", "agent_inferred", "outcome_measured",
})

# Per-source-type staleness window (#1, locked 2026-05-28 — reuses the
# KG_Engineering_Spec §3.2.1 table). None ⇒ never expires.
SOURCE_STALE_WINDOW_DAYS: dict[str, Optional[int]] = {
    "analytics":         30,
    "project_mgmt":      14,
    "communication":      7,
    "customer_voice":    30,
    "revenue":           30,
    "verbal_claim":       7,
    "pm_manual":         60,
    "agent_inferred":    14,
    "outcome_measured": None,
}


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _uuid() -> str:
    return str(uuid.uuid4())


@dataclass
class Source:
    """A connected source (connector instance or agent) for an enterprise."""
    enterprise_id: str
    source_type: str
    label: Optional[str] = None
    config: dict = field(default_factory=dict)
    status: str = "active"
    id: str = field(default_factory=_uuid)


@dataclass
class Entity:
    """Universal node. `type` is emergent (theme/account/...) OR a reserved
    ledger type (hypothesis/decision/outcome/artifact). Themes/accounts/etc.
    are resolved+deduped via embedding similarity (#2)."""
    enterprise_id: str
    type: str
    canonical_label: str
    id: str = field(default_factory=_uuid)
    aliases: list[str] = field(default_factory=list)
    properties: dict = field(default_factory=dict)
    embedding: Optional[list[float]] = None
    valid_at: datetime = field(default_factory=_now)
    transaction_at: datetime = field(default_factory=_now)
    provenance: dict = field(default_factory=dict)
    confidence: float = 1.0


@dataclass
class Signal:
    """Atomic evidence. `stale_after` is auto-computed from `source_type`
    if not supplied — outcome_measured stays None (never expires)."""
    enterprise_id: str
    source_type: str
    kind: str
    content: str
    id: str = field(default_factory=_uuid)
    source_id: Optional[str] = None
    properties: dict = field(default_factory=dict)
    embedding: Optional[list[float]] = None
    valid_at: datetime = field(default_factory=_now)
    transaction_at: datetime = field(default_factory=_now)
    stale_after: Optional[datetime] = None
    confidence: float = 1.0
    weight: float = 1.0
    provenance: dict = field(default_factory=dict)

    def __post_init__(self) -> None:
        if self.source_type not in SIGNAL_SOURCE_TYPES:
            raise ValueError(
                f"Unknown signal source_type: {self.source_type!r}. "
                f"Allowed: {sorted(SIGNAL_SOURCE_TYPES)}"
            )
        if self.stale_after is None:
            window = SOURCE_STALE_WINDOW_DAYS.get(self.source_type)
            if window is not None:
                self.stale_after = self.valid_at + timedelta(days=window)


@dataclass
class Relationship:
    """Typed edge between two nodes (entity↔entity or entity↔signal).
    `type` must be in RELATIONSHIP_VOCAB."""
    enterprise_id: str
    type: str
    source_kind: str  # 'entity' | 'signal'
    source_id: str
    target_kind: str  # 'entity' | 'signal'
    target_id: str
    properties: dict = field(default_factory=dict)
    confidence: float = 1.0
    valid_at: datetime = field(default_factory=_now)
    transaction_at: datetime = field(default_factory=_now)
    provenance: dict = field(default_factory=dict)
    id: Optional[int] = None

    def __post_init__(self) -> None:
        if self.type not in RELATIONSHIP_VOCAB:
            raise ValueError(
                f"Relationship type {self.type!r} not in closed vocabulary. "
                f"Novel relationships → use 'RELATES_TO' and flag for vocab review."
            )
        if self.source_kind not in ("entity", "signal"):
            raise ValueError(f"source_kind must be 'entity' or 'signal': {self.source_kind!r}")
        if self.target_kind not in ("entity", "signal"):
            raise ValueError(f"target_kind must be 'entity' or 'signal': {self.target_kind!r}")
