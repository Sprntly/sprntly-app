"""KG provenance traversal — walk SUPPORTS + PROMOTED_TO + MOTIVATED +
RESULTED_IN edges backward from a Decision / Outcome to the originating
Signals.

Spec source: KG_Engineering_Spec §7 (query patterns) — "given a
decision, list every signal that supported the underlying hypothesis".
The Brief assembly Step 7 and the Outcome review screen both use this
walk to render the evidence trail.

Engineering note (Apurva, 2026-05-26): we deliberately do the walk in
Python over the facade's edges_to() / edges_from() rather than pushing
it into the SQLite backend. Two reasons:
  1. The walk is small (2-3 hops, fan-out of single-digit signals);
     no real performance benefit to a single Cypher round-trip.
  2. When we flip GRAPH_BACKEND=falkor in P1-10/P1-11, this code keeps
     working without change. The backend-specific walks can be added
     later as an optimization.
"""
from __future__ import annotations

from typing import TYPE_CHECKING, Optional

from pydantic import BaseModel, ConfigDict, Field

from app.graph.edges import EdgeType

if TYPE_CHECKING:  # avoid circular import; only needed for type hints
    from app.graph.facade import GraphFacade


class ProvenanceWalkStep(BaseModel):
    """One step in a provenance walk — captures the edge that was
    traversed so callers can render an audit trail."""

    model_config = ConfigDict(extra="forbid")

    edge_type: str
    source_entity_id: str
    source_entity_type: str
    target_entity_id: str
    target_entity_type: str
    confidence: float
    edge_source: str = Field(
        ...,
        description="The write event that minted the edge — useful for "
        "telling apart synthesis_agent_run vs PM-confirmed edges.",
    )


class ProvenanceChain(BaseModel):
    """Result of walking the KG backwards from a Decision (or Outcome)
    to its originating Signals."""

    model_config = ConfigDict(extra="forbid")

    decision_id: Optional[str] = None
    hypothesis_id: Optional[str] = None
    signal_ids: list[str] = Field(default_factory=list)
    outcome_id: Optional[str] = None
    artifact_ids: list[str] = Field(default_factory=list)
    walk_steps: list[ProvenanceWalkStep] = Field(default_factory=list)


def _step_from_edge(edge) -> ProvenanceWalkStep:
    return ProvenanceWalkStep(
        edge_type=edge.edge_type.value,
        source_entity_id=edge.source_entity_id,
        source_entity_type=edge.source_entity_type,
        target_entity_id=edge.target_entity_id,
        target_entity_type=edge.target_entity_type,
        confidence=edge.confidence,
        edge_source=edge.source,
    )


def trace_provenance(
    facade: "GraphFacade",
    workspace_id: str,
    decision_id: str,
) -> ProvenanceChain:
    """Walk PROMOTED_TO (Hypothesis→Decision) backwards to the source
    Hypothesis, then SUPPORTS (Signal→Hypothesis) backwards to the
    originating Signals.

    Tenant isolation: every facade call is scoped to workspace_id, so
    cross-workspace walks are impossible by construction.
    """
    chain = ProvenanceChain(decision_id=decision_id)

    # Step 1: find the Hypothesis(es) that promoted to this Decision.
    promoted_edges = facade.edges_to(
        workspace_id, decision_id, edge_type=EdgeType.PROMOTED_TO
    )
    if not promoted_edges:
        return chain

    # In practice there's exactly one PROMOTED_TO edge per Decision
    # (Decisions are 1:1 with the promoting Hypothesis). If we see
    # more than one, we take the first deterministically but still
    # record every step.
    for pe in promoted_edges:
        chain.walk_steps.append(_step_from_edge(pe))
    chain.hypothesis_id = promoted_edges[0].source_entity_id

    # Step 2: find the Signals that SUPPORTS this Hypothesis.
    supports_edges = facade.edges_to(
        workspace_id, chain.hypothesis_id, edge_type=EdgeType.SUPPORTS
    )
    seen_signals: set[str] = set()
    for se in supports_edges:
        chain.walk_steps.append(_step_from_edge(se))
        if se.source_entity_id not in seen_signals:
            seen_signals.add(se.source_entity_id)
            chain.signal_ids.append(se.source_entity_id)

    return chain


def trace_outcome_provenance(
    facade: "GraphFacade",
    workspace_id: str,
    outcome_id: str,
) -> ProvenanceChain:
    """Extend trace_provenance from an Outcome:
      Outcome ←RESULTED_IN← Artifact ←MOTIVATED← Decision ←PROMOTED_TO← Hypothesis ←SUPPORTS← Signal(s)

    Returns a ProvenanceChain populated with outcome_id, artifact_ids,
    decision_id, hypothesis_id, signal_ids, and the full walk_steps.
    """
    chain = ProvenanceChain(outcome_id=outcome_id)

    # Step 1: find Artifact(s) that RESULTED_IN this Outcome.
    resulted_in = facade.edges_to(
        workspace_id, outcome_id, edge_type=EdgeType.RESULTED_IN
    )
    if not resulted_in:
        return chain
    seen_artifacts: set[str] = set()
    for re in resulted_in:
        chain.walk_steps.append(_step_from_edge(re))
        if re.source_entity_id not in seen_artifacts:
            seen_artifacts.add(re.source_entity_id)
            chain.artifact_ids.append(re.source_entity_id)

    # Step 2: from each Artifact, find the Decision via MOTIVATED.
    # We take the first one we find (decisions are 1:N with artifacts
    # but for a single outcome there's typically one source decision).
    decision_id: Optional[str] = None
    for art_id in chain.artifact_ids:
        motivated = facade.edges_to(
            workspace_id, art_id, edge_type=EdgeType.MOTIVATED
        )
        for me in motivated:
            chain.walk_steps.append(_step_from_edge(me))
            if decision_id is None:
                decision_id = me.source_entity_id
    if decision_id is None:
        return chain
    chain.decision_id = decision_id

    # Step 3: walk PROMOTED_TO + SUPPORTS the same way trace_provenance does.
    promoted = facade.edges_to(
        workspace_id, decision_id, edge_type=EdgeType.PROMOTED_TO
    )
    if not promoted:
        return chain
    for pe in promoted:
        chain.walk_steps.append(_step_from_edge(pe))
    chain.hypothesis_id = promoted[0].source_entity_id

    supports = facade.edges_to(
        workspace_id, chain.hypothesis_id, edge_type=EdgeType.SUPPORTS
    )
    seen_signals: set[str] = set()
    for se in supports:
        chain.walk_steps.append(_step_from_edge(se))
        if se.source_entity_id not in seen_signals:
            seen_signals.add(se.source_entity_id)
            chain.signal_ids.append(se.source_entity_id)

    return chain


__all__ = [
    "ProvenanceChain",
    "ProvenanceWalkStep",
    "trace_provenance",
    "trace_outcome_provenance",
]
