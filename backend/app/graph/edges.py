"""Knowledge Graph edge vocabulary — typed relationships between entities.

Spec source: KG_Engineering_Spec §4 (edge vocabulary) + §5 (when each
edge is written, mapped onto write events).

Every edge carries: valid_at, transaction_at, source, confidence. Source
identifies which write event minted the edge — used by the maintenance
sweep to age + audit edges.

Engineering decision (Apurva): edge types are an Enum to prevent silent
typos at write sites. Allowed (source_type → target_type) pairs are
encoded in EDGE_DIRECTION_TABLE; the facade asserts on write.

P1-12 (Apurva, 2026-05-26): edge-property schemas are codified per
edge type as TypedDicts, plus a registry (EDGE_METADATA_SCHEMA) and
helper constructors (make_*_edge) so higher layers (write events,
P1-10) don't repeat the boilerplate of building bitemporal stamps and
direction-typed entity refs. Helpers default valid_at to now() and
transaction_at to now() + 1ms so the bitemporal-distinct invariant
holds without callers having to think about it.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from enum import Enum
from typing import Optional, TypedDict

from pydantic import BaseModel, ConfigDict, Field

from app.graph.entities import BitemporalMixin, TenantMixin


class EdgeType(str, Enum):
    """All edge types per spec §4 + Artifact extensions (§5.6/5.7). Names
    match the spec verbatim so write-event logs and KG dumps are
    spec-grep-able."""

    SUPPORTS = "SUPPORTS"
    CONTRADICTS = "CONTRADICTS"
    PROMOTED_TO = "PROMOTED_TO"
    MOTIVATED = "MOTIVATED"
    RESULTED_IN = "RESULTED_IN"
    VALIDATES = "VALIDATES"
    UPDATES_WEIGHT = "UPDATES_WEIGHT"
    SCOPED_TO = "SCOPED_TO"
    INFORMS = "INFORMS"
    # Beyond spec §4 but used in §5.6/5.7 (Artifact-related edges).
    # Modelling them here keeps the vocabulary closed.
    EXPRESSED_AS = "EXPRESSED_AS"
    VISUALIZES = "VISUALIZES"


# (source_entity_type_name → target_entity_type_name)
# Source code uses class.__name__ at edge-write time to validate.
EDGE_DIRECTION_TABLE: dict[EdgeType, tuple[str, str]] = {
    EdgeType.SUPPORTS: ("Signal", "Hypothesis"),
    EdgeType.CONTRADICTS: ("Signal", "Hypothesis"),
    EdgeType.PROMOTED_TO: ("Hypothesis", "Decision"),
    # Decision → Feature is conceptual in the spec; we model "Feature" as
    # an Artifact of type prototype/prd from the Decision's source perspective.
    EdgeType.MOTIVATED: ("Decision", "Artifact"),
    EdgeType.RESULTED_IN: ("Artifact", "Outcome"),
    EdgeType.VALIDATES: ("Outcome", "Hypothesis"),
    EdgeType.UPDATES_WEIGHT: ("Outcome", "Signal"),
    # Tenancy / membership edges
    EdgeType.SCOPED_TO: ("*", "Workspace"),  # any entity → workspace
    EdgeType.INFORMS: ("Signal", "Workspace"),
    EdgeType.EXPRESSED_AS: ("Decision", "Artifact"),
    EdgeType.VISUALIZES: ("Artifact", "Artifact"),
}


# ─────────────────────── per-edge metadata schemas ───────────────────────
#
# Spec §5 describes write events that mint each edge type; the metadata
# columns called out below are what those events carry. TypedDicts here
# are total=False because metadata is best-effort — we never want a
# valid write to fail because (say) relevance_score wasn't computed.


class SupportsMetadata(TypedDict, total=False):
    """SUPPORTS Signal→Hypothesis. Minted by synthesis_agent_run."""
    relevance_score: float  # 0..1 — how strongly the Signal supports
    contribution_weight: float  # share of overall hypothesis confidence


class ContradictsMetadata(TypedDict, total=False):
    """CONTRADICTS Signal→Hypothesis. Minted alongside SUPPORTS when the
    synthesis agent finds a disconfirming signal."""
    severity: float  # 0..1 — how strongly the Signal contradicts
    contribution_weight: float


class PromotedToMetadata(TypedDict, total=False):
    """PROMOTED_TO Hypothesis→Decision. Minted by brief_recommendation_approved."""
    approved_at: str  # ISO-8601 UTC, copied from Decision.approved_at
    approved_by_user_id: str


class MotivatedMetadata(TypedDict, total=False):
    """MOTIVATED Decision→Artifact. Minted when an artifact is generated
    in response to a Decision (e.g. prd_generated)."""
    artifact_kind: str  # "prd" / "prototype" / "sprint_plan" etc.
    generated_at: str  # ISO-8601 UTC


class ResultedInMetadata(TypedDict, total=False):
    """RESULTED_IN Artifact→Outcome. Minted by feature_shipped."""
    shipped_at: str  # ISO-8601 UTC — when the artifact's feature shipped
    release_tag: str  # optional CI/CD tag for cross-referencing


class ValidatesMetadata(TypedDict, total=False):
    """VALIDATES Outcome→Hypothesis. Minted by outcome_measured.

    These are the fields the maintenance sweep needs to age the
    underlying Signals; if prediction_hit is True the supporting
    Signals get a confidence bump (UPDATES_WEIGHT)."""
    actual_impact: float
    prediction_hit: bool
    prediction_delta: float  # actual - midpoint(predicted_impact_low, high)


class UpdatesWeightMetadata(TypedDict, total=False):
    """UPDATES_WEIGHT Outcome→Signal. Minted by the maintenance sweep
    after an Outcome is measured."""
    delta_weight: float  # signed; positive on prediction hit
    new_signal_confidence: float
    reason: str  # e.g. "prediction_hit" / "prediction_miss"


class ScopedToMetadata(TypedDict, total=False):
    """SCOPED_TO Any→Workspace. Minted at every entity write."""
    entity_kind: str  # class.__name__ of the source entity


class InformsMetadata(TypedDict, total=False):
    """INFORMS Signal→Workspace. Minted at signal ingestion."""
    ingest_run_id: str
    source_tool: str  # mirrors Signal.source_tool for quick filter


class ExpressedAsMetadata(TypedDict, total=False):
    """EXPRESSED_AS Decision→Artifact. Minted by prd_generated /
    prototype_generated. Sibling of MOTIVATED; semantic difference is
    that EXPRESSED_AS is the canonical 1:1 representation while
    MOTIVATED captures the looser "this decision led to this artifact"
    relationship."""
    artifact_kind: str
    generated_at: str


class VisualizesMetadata(TypedDict, total=False):
    """VISUALIZES Artifact→Artifact. Minted when one artifact is a
    visualization/embodiment of another (e.g. prototype of a PRD)."""
    parent_version: int
    relationship: str  # "prototype_of" / "diagram_of" / etc.


# Registry: EdgeType → metadata TypedDict class. Higher layers can
# inspect this for forms/typing without hard-coding the mapping.
EDGE_METADATA_SCHEMA: dict[EdgeType, type] = {
    EdgeType.SUPPORTS: SupportsMetadata,
    EdgeType.CONTRADICTS: ContradictsMetadata,
    EdgeType.PROMOTED_TO: PromotedToMetadata,
    EdgeType.MOTIVATED: MotivatedMetadata,
    EdgeType.RESULTED_IN: ResultedInMetadata,
    EdgeType.VALIDATES: ValidatesMetadata,
    EdgeType.UPDATES_WEIGHT: UpdatesWeightMetadata,
    EdgeType.SCOPED_TO: ScopedToMetadata,
    EdgeType.INFORMS: InformsMetadata,
    EdgeType.EXPRESSED_AS: ExpressedAsMetadata,
    EdgeType.VISUALIZES: VisualizesMetadata,
}


class Edge(TenantMixin, BitemporalMixin):
    """Generic edge representation — written to the graph backend via the facade."""

    model_config = ConfigDict(extra="forbid")

    edge_type: EdgeType
    source_entity_id: str = Field(..., min_length=1)
    source_entity_type: str = Field(..., min_length=1)
    target_entity_id: str = Field(..., min_length=1)
    target_entity_type: str = Field(..., min_length=1)
    source: str = Field(
        ...,
        min_length=1,
        description="The write event that minted this edge — e.g. 'synthesis_agent_run', 'brief_recommendation_approved', 'prd_generated'.",
    )
    confidence: float = Field(..., ge=0.0, le=1.0)
    metadata: dict[str, str | int | float | bool] = Field(
        default_factory=dict,
        description="Edge-type-specific properties — see EDGE_METADATA_SCHEMA for the TypedDict per edge type.",
    )

    def validate_direction(self) -> None:
        """Raise ValueError if the (source_type, target_type) combination
        is not in the EDGE_DIRECTION_TABLE."""
        allowed_source, allowed_target = EDGE_DIRECTION_TABLE[self.edge_type]
        if allowed_source != "*" and self.source_entity_type != allowed_source:
            raise ValueError(
                f"{self.edge_type} edge requires source_entity_type={allowed_source}, "
                f"got {self.source_entity_type}"
            )
        if self.target_entity_type != allowed_target:
            raise ValueError(
                f"{self.edge_type} edge requires target_entity_type={allowed_target}, "
                f"got {self.target_entity_type}"
            )


# ─────────────────────── helper constructors ───────────────────────
#
# Each make_*_edge() takes the entity IDs + edge-type-specific metadata
# and returns a fully-validated Edge. They default valid_at to now()
# and transaction_at to now() + 1ms so the spec's bitemporal-distinct
# invariant holds without callers reasoning about it.
#
# Confidence defaults reflect spec conventions:
#   - synthesis-time edges (SUPPORTS, CONTRADICTS) → caller-supplied
#     because the synthesis agent computes them; we default to 0.7
#     when omitted.
#   - approval-time edges (PROMOTED_TO) → 1.0 (PM-confirmed).
#   - mechanical edges (SCOPED_TO, MOTIVATED, EXPRESSED_AS,
#     VISUALIZES, RESULTED_IN, INFORMS) → 1.0 (deterministic facts).
#   - inferential edges (VALIDATES, UPDATES_WEIGHT) → caller-supplied,
#     default 0.9 (post-outcome we're quite sure).


def _bitemporal_stamps(
    valid_at: Optional[datetime] = None,
    transaction_at: Optional[datetime] = None,
) -> tuple[datetime, datetime]:
    """Default valid_at to now(), transaction_at to valid_at + 1ms so
    the BitemporalMixin invariant `valid_at != transaction_at` holds."""
    now = datetime.now(timezone.utc)
    va = valid_at or now
    if va.tzinfo is None:
        va = va.replace(tzinfo=timezone.utc)
    if transaction_at is None:
        ta = va + timedelta(milliseconds=1)
    else:
        ta = transaction_at
        if ta.tzinfo is None:
            ta = ta.replace(tzinfo=timezone.utc)
    return va, ta


def make_supports_edge(
    *,
    workspace_id: str,
    signal_id: str,
    hypothesis_id: str,
    source: str = "synthesis_agent_run",
    confidence: float = 0.7,
    relevance_score: Optional[float] = None,
    contribution_weight: Optional[float] = None,
    valid_at: Optional[datetime] = None,
    transaction_at: Optional[datetime] = None,
) -> Edge:
    """SUPPORTS Signal→Hypothesis. Minted by the synthesis agent when a
    signal supports a candidate hypothesis."""
    va, ta = _bitemporal_stamps(valid_at, transaction_at)
    metadata: dict[str, str | int | float | bool] = {}
    if relevance_score is not None:
        metadata["relevance_score"] = relevance_score
    if contribution_weight is not None:
        metadata["contribution_weight"] = contribution_weight
    edge = Edge(
        workspace_id=workspace_id,
        valid_at=va,
        transaction_at=ta,
        edge_type=EdgeType.SUPPORTS,
        source_entity_id=signal_id,
        source_entity_type="Signal",
        target_entity_id=hypothesis_id,
        target_entity_type="Hypothesis",
        source=source,
        confidence=confidence,
        metadata=metadata,
    )
    edge.validate_direction()
    return edge


def make_contradicts_edge(
    *,
    workspace_id: str,
    signal_id: str,
    hypothesis_id: str,
    source: str = "synthesis_agent_run",
    confidence: float = 0.7,
    severity: Optional[float] = None,
    contribution_weight: Optional[float] = None,
    valid_at: Optional[datetime] = None,
    transaction_at: Optional[datetime] = None,
) -> Edge:
    """CONTRADICTS Signal→Hypothesis. Mirror of SUPPORTS for
    disconfirming signals."""
    va, ta = _bitemporal_stamps(valid_at, transaction_at)
    metadata: dict[str, str | int | float | bool] = {}
    if severity is not None:
        metadata["severity"] = severity
    if contribution_weight is not None:
        metadata["contribution_weight"] = contribution_weight
    edge = Edge(
        workspace_id=workspace_id,
        valid_at=va,
        transaction_at=ta,
        edge_type=EdgeType.CONTRADICTS,
        source_entity_id=signal_id,
        source_entity_type="Signal",
        target_entity_id=hypothesis_id,
        target_entity_type="Hypothesis",
        source=source,
        confidence=confidence,
        metadata=metadata,
    )
    edge.validate_direction()
    return edge


def make_promoted_to_edge(
    *,
    workspace_id: str,
    hypothesis_id: str,
    decision_id: str,
    approved_by_user_id: str,
    approved_at: datetime,
    source: str = "brief_recommendation_approved",
    confidence: float = 1.0,
    valid_at: Optional[datetime] = None,
    transaction_at: Optional[datetime] = None,
) -> Edge:
    """PROMOTED_TO Hypothesis→Decision. Minted at PM approval."""
    if approved_at.tzinfo is None:
        approved_at = approved_at.replace(tzinfo=timezone.utc)
    va, ta = _bitemporal_stamps(valid_at or approved_at, transaction_at)
    edge = Edge(
        workspace_id=workspace_id,
        valid_at=va,
        transaction_at=ta,
        edge_type=EdgeType.PROMOTED_TO,
        source_entity_id=hypothesis_id,
        source_entity_type="Hypothesis",
        target_entity_id=decision_id,
        target_entity_type="Decision",
        source=source,
        confidence=confidence,
        metadata={
            "approved_at": approved_at.isoformat(),
            "approved_by_user_id": approved_by_user_id,
        },
    )
    edge.validate_direction()
    return edge


def make_motivated_edge(
    *,
    workspace_id: str,
    decision_id: str,
    artifact_id: str,
    artifact_kind: str,
    source: str = "prd_generated",
    confidence: float = 1.0,
    generated_at: Optional[datetime] = None,
    valid_at: Optional[datetime] = None,
    transaction_at: Optional[datetime] = None,
) -> Edge:
    """MOTIVATED Decision→Artifact. Minted when an artifact is generated
    in response to a Decision."""
    gen = generated_at or datetime.now(timezone.utc)
    if gen.tzinfo is None:
        gen = gen.replace(tzinfo=timezone.utc)
    va, ta = _bitemporal_stamps(valid_at or gen, transaction_at)
    edge = Edge(
        workspace_id=workspace_id,
        valid_at=va,
        transaction_at=ta,
        edge_type=EdgeType.MOTIVATED,
        source_entity_id=decision_id,
        source_entity_type="Decision",
        target_entity_id=artifact_id,
        target_entity_type="Artifact",
        source=source,
        confidence=confidence,
        metadata={
            "artifact_kind": artifact_kind,
            "generated_at": gen.isoformat(),
        },
    )
    edge.validate_direction()
    return edge


def make_resulted_in_edge(
    *,
    workspace_id: str,
    artifact_id: str,
    outcome_id: str,
    shipped_at: datetime,
    source: str = "feature_shipped",
    confidence: float = 1.0,
    release_tag: Optional[str] = None,
    valid_at: Optional[datetime] = None,
    transaction_at: Optional[datetime] = None,
) -> Edge:
    """RESULTED_IN Artifact→Outcome. Minted when the feature backed by
    the artifact ships."""
    if shipped_at.tzinfo is None:
        shipped_at = shipped_at.replace(tzinfo=timezone.utc)
    va, ta = _bitemporal_stamps(valid_at or shipped_at, transaction_at)
    metadata: dict[str, str | int | float | bool] = {
        "shipped_at": shipped_at.isoformat(),
    }
    if release_tag is not None:
        metadata["release_tag"] = release_tag
    edge = Edge(
        workspace_id=workspace_id,
        valid_at=va,
        transaction_at=ta,
        edge_type=EdgeType.RESULTED_IN,
        source_entity_id=artifact_id,
        source_entity_type="Artifact",
        target_entity_id=outcome_id,
        target_entity_type="Outcome",
        source=source,
        confidence=confidence,
        metadata=metadata,
    )
    edge.validate_direction()
    return edge


def make_validates_edge(
    *,
    workspace_id: str,
    outcome_id: str,
    hypothesis_id: str,
    actual_impact: float,
    prediction_hit: bool,
    prediction_delta: float,
    source: str = "outcome_measured",
    confidence: float = 0.9,
    valid_at: Optional[datetime] = None,
    transaction_at: Optional[datetime] = None,
) -> Edge:
    """VALIDATES Outcome→Hypothesis. Minted after the maintenance sweep
    fills in actual_impact on an Outcome."""
    va, ta = _bitemporal_stamps(valid_at, transaction_at)
    edge = Edge(
        workspace_id=workspace_id,
        valid_at=va,
        transaction_at=ta,
        edge_type=EdgeType.VALIDATES,
        source_entity_id=outcome_id,
        source_entity_type="Outcome",
        target_entity_id=hypothesis_id,
        target_entity_type="Hypothesis",
        source=source,
        confidence=confidence,
        metadata={
            "actual_impact": actual_impact,
            "prediction_hit": prediction_hit,
            "prediction_delta": prediction_delta,
        },
    )
    edge.validate_direction()
    return edge


def make_updates_weight_edge(
    *,
    workspace_id: str,
    outcome_id: str,
    signal_id: str,
    delta_weight: float,
    new_signal_confidence: float,
    reason: str,
    source: str = "outcome_measured_sweep",
    confidence: float = 0.9,
    valid_at: Optional[datetime] = None,
    transaction_at: Optional[datetime] = None,
) -> Edge:
    """UPDATES_WEIGHT Outcome→Signal. Minted by the maintenance sweep
    after an Outcome is measured — bumps or penalizes the underlying
    Signal's confidence."""
    va, ta = _bitemporal_stamps(valid_at, transaction_at)
    edge = Edge(
        workspace_id=workspace_id,
        valid_at=va,
        transaction_at=ta,
        edge_type=EdgeType.UPDATES_WEIGHT,
        source_entity_id=outcome_id,
        source_entity_type="Outcome",
        target_entity_id=signal_id,
        target_entity_type="Signal",
        source=source,
        confidence=confidence,
        metadata={
            "delta_weight": delta_weight,
            "new_signal_confidence": new_signal_confidence,
            "reason": reason,
        },
    )
    edge.validate_direction()
    return edge


def make_scoped_to_edge(
    *,
    workspace_id: str,
    entity_id: str,
    entity_kind: str,
    source: str = "entity_write",
    confidence: float = 1.0,
    valid_at: Optional[datetime] = None,
    transaction_at: Optional[datetime] = None,
) -> Edge:
    """SCOPED_TO Any→Workspace. Minted at every entity write for tenant
    membership accounting."""
    va, ta = _bitemporal_stamps(valid_at, transaction_at)
    edge = Edge(
        workspace_id=workspace_id,
        valid_at=va,
        transaction_at=ta,
        edge_type=EdgeType.SCOPED_TO,
        source_entity_id=entity_id,
        source_entity_type=entity_kind,
        target_entity_id=workspace_id,
        target_entity_type="Workspace",
        source=source,
        confidence=confidence,
        metadata={"entity_kind": entity_kind},
    )
    edge.validate_direction()
    return edge


def make_informs_edge(
    *,
    workspace_id: str,
    signal_id: str,
    ingest_run_id: str,
    source_tool: str,
    source: str = "signal_ingest",
    confidence: float = 1.0,
    valid_at: Optional[datetime] = None,
    transaction_at: Optional[datetime] = None,
) -> Edge:
    """INFORMS Signal→Workspace. Minted at signal ingestion."""
    va, ta = _bitemporal_stamps(valid_at, transaction_at)
    edge = Edge(
        workspace_id=workspace_id,
        valid_at=va,
        transaction_at=ta,
        edge_type=EdgeType.INFORMS,
        source_entity_id=signal_id,
        source_entity_type="Signal",
        target_entity_id=workspace_id,
        target_entity_type="Workspace",
        source=source,
        confidence=confidence,
        metadata={
            "ingest_run_id": ingest_run_id,
            "source_tool": source_tool,
        },
    )
    edge.validate_direction()
    return edge


def make_expressed_as_edge(
    *,
    workspace_id: str,
    decision_id: str,
    artifact_id: str,
    artifact_kind: str,
    source: str = "prd_generated",
    confidence: float = 1.0,
    generated_at: Optional[datetime] = None,
    valid_at: Optional[datetime] = None,
    transaction_at: Optional[datetime] = None,
) -> Edge:
    """EXPRESSED_AS Decision→Artifact. The canonical 1:1 representation
    of a Decision in an artifact (PRD)."""
    gen = generated_at or datetime.now(timezone.utc)
    if gen.tzinfo is None:
        gen = gen.replace(tzinfo=timezone.utc)
    va, ta = _bitemporal_stamps(valid_at or gen, transaction_at)
    edge = Edge(
        workspace_id=workspace_id,
        valid_at=va,
        transaction_at=ta,
        edge_type=EdgeType.EXPRESSED_AS,
        source_entity_id=decision_id,
        source_entity_type="Decision",
        target_entity_id=artifact_id,
        target_entity_type="Artifact",
        source=source,
        confidence=confidence,
        metadata={
            "artifact_kind": artifact_kind,
            "generated_at": gen.isoformat(),
        },
    )
    edge.validate_direction()
    return edge


def make_visualizes_edge(
    *,
    workspace_id: str,
    artifact_id: str,
    parent_artifact_id: str,
    relationship: str,
    parent_version: int = 1,
    source: str = "prototype_generated",
    confidence: float = 1.0,
    valid_at: Optional[datetime] = None,
    transaction_at: Optional[datetime] = None,
) -> Edge:
    """VISUALIZES Artifact→Artifact. Minted when one artifact is a
    visualization/embodiment of another (e.g. prototype of a PRD)."""
    va, ta = _bitemporal_stamps(valid_at, transaction_at)
    edge = Edge(
        workspace_id=workspace_id,
        valid_at=va,
        transaction_at=ta,
        edge_type=EdgeType.VISUALIZES,
        source_entity_id=artifact_id,
        source_entity_type="Artifact",
        target_entity_id=parent_artifact_id,
        target_entity_type="Artifact",
        source=source,
        confidence=confidence,
        metadata={
            "parent_version": parent_version,
            "relationship": relationship,
        },
    )
    edge.validate_direction()
    return edge


__all__ = [
    # core
    "EdgeType",
    "Edge",
    "EDGE_DIRECTION_TABLE",
    "EDGE_METADATA_SCHEMA",
    # metadata schemas
    "SupportsMetadata",
    "ContradictsMetadata",
    "PromotedToMetadata",
    "MotivatedMetadata",
    "ResultedInMetadata",
    "ValidatesMetadata",
    "UpdatesWeightMetadata",
    "ScopedToMetadata",
    "InformsMetadata",
    "ExpressedAsMetadata",
    "VisualizesMetadata",
    # helpers
    "make_supports_edge",
    "make_contradicts_edge",
    "make_promoted_to_edge",
    "make_motivated_edge",
    "make_resulted_in_edge",
    "make_validates_edge",
    "make_updates_weight_edge",
    "make_scoped_to_edge",
    "make_informs_edge",
    "make_expressed_as_edge",
    "make_visualizes_edge",
]
