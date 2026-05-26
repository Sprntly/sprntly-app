"""Knowledge Graph entity schemas — bitemporal, tenant-isolated.

Spec source: KG_Engineering_Spec §3 (Workspace, Signal, Hypothesis,
Decision, Outcome) + §5.6/§5.7 references (Artifact, formalized here as
the 6th entity type).

Every entity:
  - belongs to exactly one Workspace (group_id == workspace_id at the
    backend layer; enforced in the facade — see facade.py)
  - carries bitemporal stamps: `valid_at` (when the fact was true in the
    real world) and `transaction_at` (when we recorded it). They MUST
    differ — same value indicates a missed timestamp, surfaced as a
    validation error.
  - has its own immutable invariants:
      * Decision: evidence_snapshot + kpi_tree_snapshot frozen at creation
      * Outcome: predicted_impact_low/high copied at Decision creation,
        not ship time
      * Hypothesis: reversal_condition required (non-empty)
      * Decision is NEVER created directly — always promoted_from a
        Hypothesis (enforced in the facade)

This module is the source of truth for what each entity *is*. The KG
backend (FalkorDB via Graphiti) and the transitional SQLite backend
both serialize/deserialize against these models.

Engineering decision (Apurva, 2026-05-26): the spec is ambiguous about
whether Artifact is a first-class entity. It's referenced in §5.6 (PRD
generated) and §5.7 (artifact_edit) but absent from the formal §3
schema. I'm modeling it explicitly because:
  1. It carries its own fields (artifact_type, version, agent_output_snapshot,
     current_version, edit_distance_from_v1) that don't fit cleanly on
     Decision or Hypothesis.
  2. The delta classifier operates over Artifact edits — if Artifact is
     implicit, the classifier API becomes awkward.
  3. EXPRESSED_AS edges in the spec already point to Artifacts.
"""
from __future__ import annotations

from datetime import datetime, timezone
from enum import Enum
from typing import Any, Optional

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    field_validator,
    model_validator,
)


# ─────────────────────── enums + literal sets ───────────────────────


class TrustLevel(str, Enum):
    ALPHA = "alpha"
    BETA = "beta"
    GRADUATED = "graduated"


class WorkspaceStage(str, Enum):
    SEED = "seed"
    GROWTH = "growth"
    SCALE = "scale"


class WorkspacePlan(str, Enum):
    FREE = "free"
    TEAM = "team"
    INTELLIGENCE_BUILD = "intelligence_build"
    ENTERPRISE = "enterprise"


class SignalSourceType(str, Enum):
    ANALYTICS = "analytics"
    PROJECT_MGMT = "project_mgmt"
    COMMUNICATION = "communication"
    CUSTOMER_VOICE = "customer_voice"
    REVENUE = "revenue"
    MANUAL = "manual"
    AGENT_INFERRED = "agent_inferred"


class ProvenanceTag(str, Enum):
    CONNECTOR_INGEST = "connector-ingest"
    PM_MANUAL = "pm-manual"
    AGENT_INFERRED = "agent-inferred"
    VERBAL_CLAIM = "verbal-claim"
    OUTCOME_MEASURED = "outcome-measured"


class HypothesisStatus(str, Enum):
    CANDIDATE = "candidate"
    PROPOSED = "proposed"
    CONFIRMED = "confirmed"
    REJECTED = "rejected"
    EXPIRED = "expired"


class ConfidenceTier(str, Enum):
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    VERY_HIGH = "very_high"


class DsAgentTier(str, Enum):
    EXPRESS = "express"
    DEEP = "deep"
    COMPREHENSIVE = "comprehensive"


class DismissedReason(str, Enum):
    WRONG_PRIORITY = "wrong_priority"
    ALREADY_IN_BACKLOG = "already_in_backlog"
    NOT_RELEVANT = "not_relevant"


class ArtifactType(str, Enum):
    """Types of generated artifacts the KG tracks. PRD is the canonical
    initial type; prototype/sprint_plan/comms_doc land as we ship the
    respective agents."""
    PRD = "prd"
    PROTOTYPE = "prototype"
    SPRINT_PLAN = "sprint_plan"
    COMMUNICATIONS = "communications"
    DOCUMENTATION = "documentation"


# Staleness windows (days) by source_type — applied to Signal.stale_after
# at write time. outcome-measured Signals are special: they never expire.
SIGNAL_STALENESS_DAYS: dict[ProvenanceTag, Optional[int]] = {
    ProvenanceTag.CONNECTOR_INGEST: 30,  # default for connector data
    ProvenanceTag.PM_MANUAL: 60,
    ProvenanceTag.AGENT_INFERRED: 14,
    ProvenanceTag.VERBAL_CLAIM: 7,
    ProvenanceTag.OUTCOME_MEASURED: None,  # never expires
}

SIGNAL_STALENESS_BY_SOURCE_TYPE: dict[SignalSourceType, int] = {
    SignalSourceType.ANALYTICS: 30,
    SignalSourceType.PROJECT_MGMT: 14,
    SignalSourceType.COMMUNICATION: 7,
    SignalSourceType.CUSTOMER_VOICE: 30,
    SignalSourceType.REVENUE: 30,
    SignalSourceType.MANUAL: 60,
    SignalSourceType.AGENT_INFERRED: 14,
}


# ─────────────────────── shared mixins ───────────────────────


class BitemporalMixin(BaseModel):
    """Every node and edge in the KG carries these two timestamps.

    valid_at      — when the fact was true in the real world
    transaction_at — when the system recorded it

    Enforcing both fields here (and a model_validator that they differ)
    means there's no path to a malformed entity — the validator runs
    before any backend write.
    """

    valid_at: datetime = Field(
        ...,
        description="When the fact was true in the real world (ISO-8601 UTC).",
    )
    transaction_at: datetime = Field(
        ...,
        description="When the system recorded the fact (ISO-8601 UTC). Always >= valid_at.",
    )

    @field_validator("valid_at", "transaction_at", mode="before")
    @classmethod
    def _ensure_utc(cls, v: Any) -> datetime:
        """Normalize naive datetimes to UTC so cross-tenant comparison is sane."""
        if isinstance(v, str):
            v = datetime.fromisoformat(v.replace("Z", "+00:00"))
        if isinstance(v, datetime) and v.tzinfo is None:
            return v.replace(tzinfo=timezone.utc)
        return v

    @model_validator(mode="after")
    def _check_bitemporal_distinct(self):
        """Spec invariant: `valid_at != transaction_at` always."""
        if self.valid_at == self.transaction_at:
            raise ValueError(
                "valid_at and transaction_at must differ (spec invariant). "
                "Same value indicates a missing/copied timestamp."
            )
        if self.transaction_at < self.valid_at:
            raise ValueError(
                "transaction_at < valid_at — recording cannot precede the fact"
            )
        return self


class TenantMixin(BaseModel):
    """All KG entities are scoped to a Workspace. The graph backend uses
    workspace_id as the group_id at every query (enforced in the facade,
    not by discipline)."""

    workspace_id: str = Field(
        ...,
        min_length=1,
        description="The owning Workspace's ID. Equals FalkorDB group_id; enforced at facade.",
    )


# ─────────────────────── entity 1: Workspace ───────────────────────


class KpiTreeNode(BaseModel):
    """One node in the workspace's KPI tree (north star → primary → secondary)."""

    model_config = ConfigDict(extra="forbid")

    name: str = Field(..., min_length=1, max_length=120)
    role: str = Field(
        ...,
        description="north_star / primary / secondary / leading_indicator",
        pattern="^(north_star|primary|secondary|leading_indicator)$",
    )
    target_value: Optional[float] = None
    current_value: Optional[float] = None
    parent: Optional[str] = Field(
        default=None,
        description="Name of the parent KPI in the tree. None for north_star.",
    )


class WorkspaceStrategy(BaseModel):
    """Strategic context captured at onboarding and refined over time."""

    model_config = ConfigDict(extra="forbid")

    okrs: list[str] = Field(default_factory=list, description="Current OKRs (free text).")
    dead_ends: list[str] = Field(
        default_factory=list,
        description="Explicit exclusions — areas the team has decided NOT to pursue. Recommendation filter at Brief assembly Step 7.",
    )
    current_priorities: list[str] = Field(default_factory=list)
    biggest_risk: Optional[str] = Field(
        default=None, description="Free text — calibrates Synthesis hypothesis framing."
    )


class Workspace(TenantMixin, BitemporalMixin):
    """Root entity per tenant. workspace_id == group_id in FalkorDB."""

    model_config = ConfigDict(extra="forbid")

    company_name: str = Field(..., min_length=1, max_length=200)
    industry: str = Field(..., min_length=1, max_length=80)
    stage: WorkspaceStage
    business_model: str = Field(
        ...,
        max_length=120,
        description="B2B SaaS / Consumer / Marketplace / etc.",
    )
    kpi_tree: list[KpiTreeNode] = Field(default_factory=list)
    strategy: WorkspaceStrategy = Field(default_factory=WorkspaceStrategy)
    competitors: list[str] = Field(default_factory=list)
    trust_level: TrustLevel = TrustLevel.ALPHA
    plan: WorkspacePlan = WorkspacePlan.FREE

    # Optional / derived
    team_capacity: Optional[int] = Field(
        default=None, description="Approximate sprints per quarter."
    )
    trust_upgraded_at: Optional[datetime] = None
    preferences: dict[str, Any] = Field(
        default_factory=dict,
        description="Updated by the delta classifier on preference-type edits.",
    )

    created_at: datetime
    updated_at: datetime


# ─────────────────────── entity 2: Signal ───────────────────────


class Signal(TenantMixin, BitemporalMixin):
    """Atomic unit of evidence. Every Brief recommendation traces back to
    one or more Signals via SUPPORTS / CONTRADICTS edges."""

    model_config = ConfigDict(extra="forbid")

    signal_id: str = Field(..., min_length=1)
    content: str = Field(..., min_length=1, max_length=2000)
    source_type: SignalSourceType
    source_tool: str = Field(
        ...,
        min_length=1,
        max_length=80,
        description="amplitude / mixpanel / zendesk / intercom / linear / jira / slack / manual / agent.",
    )
    provenance_tag: ProvenanceTag
    confidence: float = Field(
        ...,
        ge=0.0,
        le=1.0,
        description="Composite confidence at the time of ingest.",
    )

    # Spec §3.2.1: staleness window varies by source_type AND provenance_tag.
    # outcome-measured signals never expire.
    stale_after: Optional[datetime] = Field(
        default=None,
        description="UTC timestamp after which the signal is considered stale. None = never expires (outcome-measured only).",
    )
    stale_windows: dict[str, int] = Field(
        default_factory=dict,
        description="Per-provenance stale windows in days, copied from SIGNAL_STALENESS_DAYS at write time.",
    )

    # Optional fields
    kpi_relevance: list[str] = Field(
        default_factory=list,
        description="KPI tree node names this signal touches.",
    )
    cited_by_hypothesis_ids: list[str] = Field(default_factory=list)
    raw_source_ref: Optional[str] = Field(
        default=None,
        description="Pointer back to the original raw connector record (e.g. amplitude event ID).",
    )

    @model_validator(mode="after")
    def _outcome_signals_never_expire(self):
        """Spec invariant: outcome-measured signals have stale_after=None."""
        if self.provenance_tag == ProvenanceTag.OUTCOME_MEASURED:
            if self.stale_after is not None:
                raise ValueError(
                    "outcome-measured signals must have stale_after=None (never expire)"
                )
        return self


# ─────────────────────── entity 3: Hypothesis ───────────────────────


class Hypothesis(TenantMixin, BitemporalMixin):
    """A ranked recommendation candidate. Promoted to Decision on PM approval."""

    model_config = ConfigDict(extra="forbid")

    hypothesis_id: str = Field(..., min_length=1)
    claim: str = Field(..., min_length=10, max_length=600)
    predicted_metric: str = Field(..., min_length=1, max_length=120)
    predicted_impact_low: float
    predicted_impact_high: float
    predicted_impact_basis: str = Field(..., min_length=10, max_length=500)

    status: HypothesisStatus
    evidence_signal_ids: list[str] = Field(
        ...,
        min_length=1,
        description="Spec: at least 1 supporting signal required. Candidate → proposed needs evidence_count >= 3 from >= 2 source_types (enforced at facade write).",
    )
    evidence_count: int = Field(..., ge=1)
    confidence_composite: float = Field(..., ge=0.0, le=1.0)
    confidence_tier: ConfidenceTier
    reversal_condition: str = Field(
        ...,
        min_length=10,
        max_length=400,
        description="What observation would force a rollback. REQUIRED on every Hypothesis per spec.",
    )

    created_at: datetime
    status_updated_at: datetime

    # Optional
    ds_agent_tier: Optional[DsAgentTier] = None
    ds_agent_finding_json: Optional[dict[str, Any]] = None
    assumptions: list[str] = Field(default_factory=list)
    disconfirming_signals: list[str] = Field(
        default_factory=list,
        description="Signal IDs that contradict. NEVER hidden — CONTRADICTS edges in the KG mirror this list.",
    )
    brief_id: Optional[str] = None
    brief_rank: Optional[int] = Field(default=None, ge=1, le=5)
    promoted_to_decision_id: Optional[str] = None
    dismissed_reason: Optional[DismissedReason] = None

    @model_validator(mode="after")
    def _high_gte_low(self):
        if self.predicted_impact_high < self.predicted_impact_low:
            raise ValueError(
                "predicted_impact_high must be >= predicted_impact_low"
            )
        return self


# ─────────────────────── entity 4: Decision ───────────────────────


class Decision(TenantMixin, BitemporalMixin):
    """A confirmed Hypothesis. Immutable after creation. Never created directly."""

    model_config = ConfigDict(extra="forbid")

    decision_id: str = Field(..., min_length=1)
    promoted_from_hypothesis_id: str = Field(
        ...,
        min_length=1,
        description="REQUIRED, never null. Decisions are never created directly; they are always promoted from a Hypothesis.",
    )
    claim: str = Field(
        ...,
        min_length=10,
        max_length=600,
        description="Copied from the Hypothesis at promotion. Immutable.",
    )
    reasoning: str = Field(..., min_length=10, max_length=2000)
    approved_by_user_id: str = Field(..., min_length=1)
    approved_at: datetime
    evidence_snapshot: dict[str, Any] = Field(
        ...,
        description="Frozen view of all supporting signals + DS findings at approval time. IMMUTABLE.",
    )
    kpi_tree_snapshot: list[KpiTreeNode] = Field(
        ...,
        description="Frozen Workspace.kpi_tree at approval time. IMMUTABLE.",
    )
    reversal_condition: str = Field(..., min_length=10, max_length=400)
    reversal_triggered: bool = False
    reversal_triggered_at: Optional[datetime] = None

    # Optional
    alternatives_considered: list[str] = Field(default_factory=list)
    feature_id: Optional[str] = None
    prd_generated_at: Optional[datetime] = None
    outcome_id: Optional[str] = None


# ─────────────────────── entity 5: Outcome ───────────────────────


class Outcome(TenantMixin, BitemporalMixin):
    """Closes the loop on a Decision — what actually happened after shipping."""

    model_config = ConfigDict(extra="forbid")

    outcome_id: str = Field(..., min_length=1)
    linked_decision_id: str = Field(..., min_length=1)
    linked_hypothesis_id: str = Field(..., min_length=1)
    linked_signal_ids: list[str] = Field(
        default_factory=list,
        description="Signals that were the basis for the prediction. Frozen at Decision creation.",
    )
    feature_name: str = Field(..., min_length=1, max_length=200)
    shipped_at: datetime
    metric_measured: str = Field(..., min_length=1, max_length=120)
    # COPIED from Decision at creation time, NOT at ship time.
    predicted_impact_low: float
    predicted_impact_high: float
    provenance_tag: ProvenanceTag = Field(
        default=ProvenanceTag.OUTCOME_MEASURED,
        description="Fixed at outcome-measured. NEVER changes — these signals never expire.",
    )

    # Filled in later by the maintenance sweep
    actual_impact: Optional[float] = None
    actual_impact_measured_at: Optional[datetime] = None
    prediction_hit: Optional[bool] = None
    prediction_delta: Optional[float] = None
    pm_annotation: Optional[str] = Field(default=None, max_length=2000)
    confounding_factors: list[str] = Field(default_factory=list)
    signal_accuracy_updates: list[dict[str, Any]] = Field(default_factory=list)

    @model_validator(mode="after")
    def _outcome_measured_only(self):
        if self.provenance_tag != ProvenanceTag.OUTCOME_MEASURED:
            raise ValueError("Outcome.provenance_tag must always be outcome-measured")
        return self


# ─────────────────────── entity 6: Artifact (formalized) ───────────────────────


class Artifact(TenantMixin, BitemporalMixin):
    """Generated artifact (PRD, prototype, sprint plan, comms doc, doc).

    Engineering decision: the spec is ambiguous about whether Artifact is
    a first-class entity. We formalize it because the delta classifier
    operates over Artifact edits and EXPRESSED_AS edges already point to
    Artifacts. Without explicit modeling, implementations diverge.
    """

    model_config = ConfigDict(extra="forbid")

    artifact_id: str = Field(..., min_length=1)
    artifact_type: ArtifactType
    version: int = Field(default=1, ge=1)
    agent_output_snapshot: dict[str, Any] = Field(
        ...,
        description="The original agent output. Becomes the v1 baseline for edit-distance computation.",
    )
    current_version: int = Field(default=1, ge=1)
    edit_distance_from_v1: int = Field(
        default=0,
        ge=0,
        description="Cumulative edit distance from v1. Updated on every artifact_edit event.",
    )

    # Optional — what produced this artifact
    source_decision_id: Optional[str] = Field(
        default=None,
        description="The Decision this artifact was generated from (for PRDs).",
    )
    source_hypothesis_id: Optional[str] = None

    # Linked artifacts (e.g. PRD <- VISUALIZES <- Prototype)
    visualizes_artifact_id: Optional[str] = Field(
        default=None,
        description="If this artifact visualizes another (prototype → PRD), the parent artifact's ID. Maps to the VISUALIZES edge.",
    )

    @model_validator(mode="after")
    def _current_version_consistent(self):
        if self.current_version < self.version:
            raise ValueError(
                "current_version must be >= version (the version this entity represents)"
            )
        return self


# ─────────────────────── re-exports ───────────────────────


ENTITY_TYPES = (Workspace, Signal, Hypothesis, Decision, Outcome, Artifact)

__all__ = [
    # entities
    "Workspace",
    "Signal",
    "Hypothesis",
    "Decision",
    "Outcome",
    "Artifact",
    # sub-models
    "KpiTreeNode",
    "WorkspaceStrategy",
    # enums
    "TrustLevel",
    "WorkspaceStage",
    "WorkspacePlan",
    "SignalSourceType",
    "ProvenanceTag",
    "HypothesisStatus",
    "ConfidenceTier",
    "DsAgentTier",
    "DismissedReason",
    "ArtifactType",
    # tables
    "SIGNAL_STALENESS_DAYS",
    "SIGNAL_STALENESS_BY_SOURCE_TYPE",
    # mixins (for testing)
    "BitemporalMixin",
    "TenantMixin",
    # convenience
    "ENTITY_TYPES",
]
