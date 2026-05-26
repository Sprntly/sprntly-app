"""KG write-event hooks. Stubs today; will be wired to the real graph
client in a follow-up PR. Synthesis Agent + PRD generation call these
to record state changes.

The signatures match §5.6 / §5.7 of KG_Engineering_Spec so the wire-up
PR is a pure body swap. Until then, every call is a structured log line
the wire-up PR can replay to backfill the graph.
"""
import logging

logger = logging.getLogger(__name__)


def write_prd_generated(
    decision_id: str, prd_json: dict, *, workspace_id: str
) -> None:
    """KG write event 5.6 — prd_generated. Creates Artifact(type=prd, v=1)
    and MOTIVATED edge from Decision.

    Args:
        decision_id: The parent Decision node id that motivated this PRD.
        prd_json: Full PRD payload snapshot (agent_output_snapshot).
        workspace_id: Scopes the write to a tenant's subgraph.
    """
    logger.info(
        "KG hook stub: prd_generated decision_id=%s ws=%s", decision_id, workspace_id
    )
    # No-op until FalkorDB+Graphiti land. Will become:
    #   graph.create_artifact(...)
    #   graph.add_edge(decision_id, feature_id, "MOTIVATED", source="prd_generated")


def write_artifact_edit(
    artifact_id: str,
    original: str,
    edited: str,
    *,
    workspace_id: str,
    user_id: str,
) -> None:
    """KG write event 5.7 — artifact_edit. Runs delta classifier."""
    logger.info(
        "KG hook stub: artifact_edit artifact=%s ws=%s", artifact_id, workspace_id
    )
