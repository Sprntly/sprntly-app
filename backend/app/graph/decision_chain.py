"""Decision → Outcome chain — the write path for the §2 ledger spine.

`hypothesis`/`decision`/`outcome`/`artifact` are already reserved
(`RESERVED_ENTITY_TYPES`) and already read (`GraphFacade.load_session_context`);
`PROMOTED_TO`/`RESULTED_IN`/`VALIDATES` are already closed-vocabulary edges
(`RELATIONSHIP_VOCAB`) — nothing wrote any of them until this module. Four real
product triggers close the loop, each firing off something that already
happens in the product (no new UI):

  1. Hypothesis --PROMOTED_TO--> Decision
     trigger: the "Generate PRD" click (prd_runner.py, human PRD flow), once
     the insight resolves a REAL hypothesis via
     `graph.retrieval.resolve_insight_hypothesis`.
  2. Decision   --RESULTED_IN-->  Artifact
     trigger: that SAME PRD reaching status='ready' (prd_runner.py finalizes
     status='ready' in the same call that generates the PRD, so triggers 1
     and 2 fire together from `prd_runner._finalize_part_a`).
  3. Artifact   --REALIZES-->     Outcome
     trigger: an idea moving to status='done' (routes/ideation.py,
     PATCHABLE_STATUSES). `REALIZES` is the closed-vocabulary word chosen for
     this edge — the ticket names PROMOTED_TO/RESULTED_IN/VALIDATES for steps
     1, 2 and 4 but leaves step 3 unnamed; REALIZES ("the shipped artifact
     realizes this outcome") is already in `RELATIONSHIP_VOCAB`, so no schema
     change is needed.
  4. Outcome    --VALIDATES-->    Hypothesis
     trigger: fires alongside step 3 (we already have the hypothesis in hand
     from the PROMOTED_TO/RESULTED_IN walk an outcome write requires) — but
     `actual_impact` has NO automatic source today (no live analytics
     connector), so it is written null/manual at first, per the ticket's
     "don't silently skip this edge; the value may start null/manual".
     `validate_hypothesis_from_outcome` is the manual/PM-annotatable entry
     point a future write can call again to fill it in.

All writes go through `GraphFacade` (never `kg_entity`/`kg_relationship`
directly), are tenant-scoped, and are best-effort by convention — the actual
product action (a PRD finishing, an idea being marked done) has ALREADY
succeeded and been persisted by the time these run, so callers wrap them in
try/except and log rather than let a KG hiccup fail the user-visible action.
Matches the resilience posture of `graph.retrieval`'s trail resolvers.
"""
from __future__ import annotations

import logging
from typing import Any, Optional

from app.graph.facade import GraphFacade
from app.graph.types import Entity, Relationship

logger = logging.getLogger(__name__)

# The vocabulary word for the Artifact -> Outcome edge (step 3 — see module
# docstring for why it isn't one of the three the ticket names explicitly).
ARTIFACT_TO_OUTCOME_TYPE = "REALIZES"


def promote_hypothesis_to_decision(
    facade: GraphFacade,
    enterprise_id: str,
    hypothesis_id: str,
    *,
    label: str,
    properties: Optional[dict[str, Any]] = None,
    provenance: Optional[dict[str, Any]] = None,
) -> Entity:
    """Create a `decision` Entity and a PROMOTED_TO edge FROM the hypothesis.

    Trigger: the "Generate PRD" click (prd_runner.py, Part A / human PRD
    flow), once the insight resolves a real hypothesis."""
    decision = Entity(
        enterprise_id=enterprise_id,
        type="decision",
        canonical_label=label[:200],
        properties={"hypothesis_id": hypothesis_id, **(properties or {})},
        provenance=provenance or {},
    )
    facade.create_entity(enterprise_id, decision)
    facade.write_relationship(enterprise_id, Relationship(
        enterprise_id=enterprise_id,
        type="PROMOTED_TO",
        source_kind="entity", source_id=hypothesis_id,
        target_kind="entity", target_id=decision.id,
        provenance=provenance or {},
    ))
    return decision


def create_artifact_from_decision(
    facade: GraphFacade,
    enterprise_id: str,
    decision_id: str,
    *,
    label: str,
    properties: Optional[dict[str, Any]] = None,
    provenance: Optional[dict[str, Any]] = None,
) -> Entity:
    """Create an `artifact` Entity and a RESULTED_IN edge FROM the decision.

    Trigger: that same PRD reaching status='ready'."""
    artifact = Entity(
        enterprise_id=enterprise_id,
        type="artifact",
        canonical_label=label[:200],
        properties={"decision_id": decision_id, **(properties or {})},
        provenance=provenance or {},
    )
    facade.create_entity(enterprise_id, artifact)
    facade.write_relationship(enterprise_id, Relationship(
        enterprise_id=enterprise_id,
        type="RESULTED_IN",
        source_kind="entity", source_id=decision_id,
        target_kind="entity", target_id=artifact.id,
        provenance=provenance or {},
    ))
    return artifact


def artifacts_for_hypothesis(
    facade: GraphFacade, enterprise_id: str, hypothesis_id: str,
) -> list[Entity]:
    """Walk hypothesis --PROMOTED_TO--> decision --RESULTED_IN--> artifact,
    over every decision this hypothesis was ever promoted to. Most recent
    artifact first (by `transaction_at`).

    Used by the artifact→outcome trigger to find which artifact(s) a
    completed idea's hypothesis resulted in — reuses the same `edges_from`
    facade primitive the evidence-trail readers (`graph.retrieval`,
    `evidence_kg`) walk with, rather than a bespoke traversal API.

    Best-effort: any read failure logs and yields whatever was already
    collected (never raises) — an unreachable graph must not break the
    caller's product action."""
    artifacts: list[Entity] = []
    try:
        promo_edges = facade.edges_from(enterprise_id, hypothesis_id, type="PROMOTED_TO")
    except Exception as exc:  # noqa: BLE001 — best-effort read
        logger.info(
            "decision chain: edges_from(hypothesis=%s, PROMOTED_TO) failed (%s)",
            hypothesis_id, exc,
        )
        return artifacts
    for edge in promo_edges:
        if edge.target_kind != "entity":
            continue
        try:
            result_edges = facade.edges_from(enterprise_id, edge.target_id, type="RESULTED_IN")
        except Exception as exc:  # noqa: BLE001 — best-effort read
            logger.info(
                "decision chain: edges_from(decision=%s, RESULTED_IN) failed (%s)",
                edge.target_id, exc,
            )
            continue
        for redge in result_edges:
            if redge.target_kind != "entity":
                continue
            artifact = facade.get_entity(enterprise_id, redge.target_id)
            if artifact is not None:
                artifacts.append(artifact)
    artifacts.sort(key=lambda a: a.transaction_at, reverse=True)
    return artifacts


def create_outcome_from_artifact(
    facade: GraphFacade,
    enterprise_id: str,
    artifact_id: str,
    *,
    label: str,
    properties: Optional[dict[str, Any]] = None,
    provenance: Optional[dict[str, Any]] = None,
) -> Entity:
    """Create an `outcome` Entity and a REALIZES edge FROM the artifact.

    Trigger: an idea moving to status='done' (routes/ideation.py,
    PATCHABLE_STATUSES)."""
    outcome = Entity(
        enterprise_id=enterprise_id,
        type="outcome",
        canonical_label=label[:200],
        properties={"artifact_id": artifact_id, **(properties or {})},
        provenance=provenance or {},
    )
    facade.create_entity(enterprise_id, outcome)
    facade.write_relationship(enterprise_id, Relationship(
        enterprise_id=enterprise_id,
        type=ARTIFACT_TO_OUTCOME_TYPE,
        source_kind="entity", source_id=artifact_id,
        target_kind="entity", target_id=outcome.id,
        provenance=provenance or {},
    ))
    return outcome


def validate_hypothesis_from_outcome(
    facade: GraphFacade,
    enterprise_id: str,
    outcome_id: str,
    hypothesis_id: str,
    *,
    actual_impact: Optional[Any] = None,
    annotated_by: Optional[str] = None,
    provenance: Optional[dict[str, Any]] = None,
) -> Relationship:
    """Record the outcome's measured (or manually annotated) impact against
    the hypothesis it validates: merges `actual_impact` (+ who annotated it)
    into the outcome's properties and writes a VALIDATES edge outcome ->
    hypothesis.

    No automatic source for `actual_impact` exists today (no live analytics
    connector) — this is the manual/PM-annotatable path the ticket calls for.
    Called with `actual_impact=None` at outcome-creation time so the edge is
    never silently missing (the ticket: "don't silently skip this edge; the
    value may start null/manual"); a later call with a real value re-merges
    the property and writes a fresh edge recording the annotation, so the
    graph keeps an auditable history rather than mutating the one edge in
    place (mirrors the append-only bitemporal posture the rest of the KG
    uses)."""
    patch: dict[str, Any] = {"actual_impact": actual_impact}
    if annotated_by:
        patch["actual_impact_annotated_by"] = annotated_by
    facade.update_entity_properties(enterprise_id, outcome_id, patch)
    return facade.write_relationship(enterprise_id, Relationship(
        enterprise_id=enterprise_id,
        type="VALIDATES",
        source_kind="entity", source_id=outcome_id,
        target_kind="entity", target_id=hypothesis_id,
        properties={"actual_impact": actual_impact},
        provenance=provenance or {},
    ))
