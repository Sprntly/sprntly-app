"""Entity resolution for KG writes.

Spec source: KG_Engineering_Spec §6 (Entity Resolution).

The full resolution pipeline lives behind FalkorDB + Cognee: embed candidate
content, run cosine-similarity against the existing nodes scoped to the
workspace, merge above a threshold, otherwise create a new node. Below
threshold = create new node AND flag for human review.

This module is the **seam** between today's SQLite-backed transitional
backend and tomorrow's Cognee pipeline. Today we do a simple exact-match
on (content, source_tool) — good enough for the §5.2 connector_sync use
case where the same Amplitude event keeps re-arriving with fresh
`valid_at`. Once Cognee lands, swap the implementation; the call-site
contract does not change.

Engineering decisions (Apurva, 2026-05-26):
  1. Threshold 0.8 lives in this module — when we wire up real
     embeddings the comparator changes, but the threshold stays as a
     module-level constant the spec can grep for.
  2. Resolution is **per-entity-type**. Signals resolve by content +
     source_tool; Hypotheses resolve by claim (when we get there);
     other entities don't auto-resolve (immutable / strict-create).
  3. The `(entity, was_new)` return tuple lets the caller decide what
     to do on a hit — connector_sync bumps `valid_at`/cited_by, the
     delta classifier writes new Signals, etc.
"""
from __future__ import annotations

import logging
from typing import Optional, Tuple

from app.graph.entities import Signal
from app.graph.facade import GraphFacade

logger = logging.getLogger(__name__)


# Spec §6: above this similarity score → merge into existing node.
# Below → create new + flag for human review. Today we only use exact
# match (similarity 1.0 for a hit, 0.0 for a miss).
RESOLUTION_THRESHOLD: float = 0.8


def resolve_or_create_signal(
    graph: GraphFacade,
    workspace_id: str,
    candidate: Signal,
    threshold: float = RESOLUTION_THRESHOLD,
) -> Tuple[Signal, bool]:
    """Resolve a candidate Signal against existing ones in the workspace.

    Returns (existing_or_new_signal, was_new).
    - was_new=False → caller should treat the candidate as an evidence
      bump on the existing Signal (update valid_at, cited_by, etc.).
    - was_new=True  → write the candidate as-is.

    Today's implementation is exact-match on (content, source_tool).
    When Cognee + embedding similarity arrives, the body of this
    function changes; the signature is the contract.

    Note: `threshold` is accepted for forward-compat — the exact-match
    impl uses it only to short-circuit (1.0 >= threshold always true).
    """
    # Scope: only check signals from the same source_tool — different
    # tools producing identical text are conceptually distinct evidence.
    candidates = graph.list_active_signals(
        workspace_id,
        source_types=[candidate.source_type.value],
        limit=200,
    )
    for existing in candidates:
        if (
            existing.source_tool == candidate.source_tool
            and existing.content.strip() == candidate.content.strip()
        ):
            logger.debug(
                "entity_resolution hit: signal_id=%s for candidate content=%r",
                existing.signal_id,
                candidate.content[:60],
            )
            return existing, False
    return candidate, True


def resolve_or_create(
    graph: GraphFacade,
    workspace_id: str,
    candidate_entity,
    threshold: float = RESOLUTION_THRESHOLD,
) -> Tuple[object, bool]:
    """Generic dispatcher — routes to per-entity-type resolvers.

    Other entity types currently have no auto-resolution behavior and
    fall through with `(candidate, True)`. The seam is here for when
    Hypothesis content-resolution lands (deduping cross-customer
    bootstrap Hypotheses, etc.).
    """
    if isinstance(candidate_entity, Signal):
        return resolve_or_create_signal(graph, workspace_id, candidate_entity, threshold)
    # No resolver for this type yet — treat as new.
    return candidate_entity, True


def below_threshold_flag(similarity: float, threshold: float = RESOLUTION_THRESHOLD) -> Optional[str]:
    """Convenience helper for the spec-mandated 'flag for human review'
    side effect when similarity is below threshold but above zero.
    Returns a flag string (logged + persisted as Signal metadata) or
    None if no flag is warranted.
    """
    if 0.0 < similarity < threshold:
        return f"entity_resolution_review_required:similarity={similarity:.2f}"
    return None


__all__ = [
    "RESOLUTION_THRESHOLD",
    "resolve_or_create",
    "resolve_or_create_signal",
    "below_threshold_flag",
]
