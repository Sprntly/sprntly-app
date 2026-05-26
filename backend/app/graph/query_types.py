"""Query-result types for the KG query API (spec §7 query patterns + §10).

These models are the *outputs* of the high-level query functions on the
GraphFacade. They are Pydantic models so the FastAPI layer can serialize
them straight to JSON without manual marshaling.

Engineering decision (Apurva, 2026-05-26): keep these distinct from the
entity models in `entities.py`. Entity models are write-time invariants
(bitemporal stamps, tenant scoping, immutability rules); query types are
read-time aggregations that combine entities and may include derived
fields (e.g. ProvenanceChain.walk_steps). Mixing the two would make the
write-side validators noisy and the read-side responses bloated.

Spec citations:
  §7 query patterns 1 (load_session_context), 2 (get_brief_context),
     3 (get_prd_context), 4 (trace_decision_provenance), 5 (query_as_of)
  §10 — 12 public functions including run_maintenance_sweep + bitemporal
"""
from __future__ import annotations

from datetime import datetime
from typing import Any, Optional

from pydantic import BaseModel, ConfigDict, Field

from app.graph.entities import (
    Artifact,
    Decision,
    Hypothesis,
    Outcome,
    Signal,
    Workspace,
)


class SessionContext(BaseModel):
    """Spec §7 query pattern 1 — what we load at the start of every
    /v1/brief and /v1/ask request. Hard latency budget: ≤500ms (spec §10).

    Returned by `GraphFacade.load_session_context()`. The shape is fixed:
    1 workspace + ≤10 active hypotheses + ≤5 recent decisions + ≤3 measured
    outcomes. The caller pins these into the synthesis/brief context window.
    """

    model_config = ConfigDict(extra="forbid")

    workspace: Optional[Workspace] = Field(
        default=None,
        description="None when the workspace_id is unknown — caller should treat as cold start.",
    )
    active_hypotheses: list[Hypothesis] = Field(default_factory=list)
    recent_decisions: list[Decision] = Field(default_factory=list)
    recent_outcomes: list[Outcome] = Field(default_factory=list)
    loaded_at: datetime = Field(
        ...,
        description="When this snapshot was assembled. Used by the in-process TTL cache.",
    )


class BriefContext(BaseModel):
    """Spec §7 query pattern 2 — additional context the synthesis_agent_run
    needs beyond SessionContext: fresh uncited signals + per-source accuracy
    + similar prior outcomes."""

    model_config = ConfigDict(extra="forbid")

    workspace_id: str
    uncited_signals: list[Signal] = Field(
        default_factory=list,
        description="Active signals that have not yet been cited by any active hypothesis.",
    )
    source_accuracy: dict[str, float] = Field(
        default_factory=dict,
        description="source_tool → prediction_hit rate from prior Outcomes. 0.0 if no history.",
    )
    similar_outcomes: list[Outcome] = Field(
        default_factory=list,
        description="Recent measured outcomes the synthesis agent can use for prior-grounding.",
    )
    loaded_at: datetime


class PrdContext(BaseModel):
    """Spec §7 query pattern 3 — Decision + workspace + KPI tree snapshot
    for PRD generation."""

    model_config = ConfigDict(extra="forbid")

    decision: Decision
    workspace: Workspace
    kpi_tree_snapshot: list[dict[str, Any]] = Field(
        default_factory=list,
        description="The Decision's frozen kpi_tree_snapshot, surfaced verbatim.",
    )
    source_hypothesis: Optional[Hypothesis] = None
    loaded_at: datetime


class ProvenanceChain(BaseModel):
    """Spec §7 query pattern 4 — full Signals→Hypothesis→Decision→Outcome
    chain for a Decision. Used by the "Why this brief?" UI and by audits.

    walk_steps is an ordered list of {edge_type, source_id, target_id,
    valid_at} dicts so the UI can render the chain step-by-step without
    re-walking the graph.
    """

    model_config = ConfigDict(extra="forbid")

    decision_id: str
    hypothesis_id: str
    supporting_signal_ids: list[str] = Field(default_factory=list)
    contradicting_signal_ids: list[str] = Field(default_factory=list)
    outcome_id: Optional[str] = None
    walk_steps: list[dict[str, Any]] = Field(
        default_factory=list,
        description="Ordered (edge_type, source_entity_id, target_entity_id, valid_at) tuples.",
    )


class WorkspaceSnapshot(BaseModel):
    """Spec §7 query pattern 5 — bitemporal point-in-time view of an entire
    workspace. Returned by `GraphFacade.query_as_of(workspace_id, as_of)`.

    Semantics:
      - "what did we know at time T?" → transaction_at <= T
      - "what was true at time T?"    → valid_at <= T (and not superseded)
    v1 returns the most-recent-per-id where both conditions hold.
    """

    model_config = ConfigDict(extra="forbid")

    workspace_id: str
    as_of: datetime
    workspace: Optional[Workspace] = None
    signals: list[Signal] = Field(default_factory=list)
    hypotheses: list[Hypothesis] = Field(default_factory=list)
    decisions: list[Decision] = Field(default_factory=list)
    outcomes: list[Outcome] = Field(default_factory=list)
    artifacts: list[Artifact] = Field(default_factory=list)


class SweepReport(BaseModel):
    """Output of `GraphFacade.run_maintenance_sweep(workspace_id)`.

    The sweep is a background job that ages signals, recomputes hypothesis
    evidence counts, and tags expired entities. v1 runs synchronously and
    returns this report; once event_5_8/5_9 are wired we'll move it to
    `asyncio.create_task` and return a job_id.
    """

    model_config = ConfigDict(extra="forbid")

    workspace_id: str
    ran_at: datetime
    expired_signals: int = Field(default=0, ge=0)
    updated_signal_weights: int = Field(default=0, ge=0)
    hypotheses_evidence_recomputed: int = Field(default=0, ge=0)
    errors: list[str] = Field(default_factory=list)


class ArtifactDelta(BaseModel):
    """Output of `GraphFacade.write_artifact_delta(...)`.

    Captures one PM edit against an Artifact (PRD section, sprint plan step,
    etc.). The classification field is filled by a keyword heuristic for now;
    spec §5.7 calls for a Claude-backed classifier (preference / data-driven
    / scope-cut / wording) that will replace the heuristic in a follow-up.
    """

    model_config = ConfigDict(extra="forbid")

    delta_id: str
    workspace_id: str
    artifact_id: str
    artifact_type: str
    section: str
    original_text: str
    edited_text: str
    user_id: str
    classification: str = Field(
        ...,
        description="preference / data-driven / scope-cut / wording / unknown — heuristic for now.",
    )
    valid_at: datetime
    transaction_at: datetime


__all__ = [
    "SessionContext",
    "BriefContext",
    "PrdContext",
    "ProvenanceChain",
    "WorkspaceSnapshot",
    "SweepReport",
    "ArtifactDelta",
]
