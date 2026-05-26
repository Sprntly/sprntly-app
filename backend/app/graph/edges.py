"""Knowledge Graph edge vocabulary — 9 typed relationships between entities.

Spec source: KG_Engineering_Spec §4 (edge vocabulary) + §5 (when each
edge is written, mapped onto write events).

Every edge carries: valid_at, transaction_at, source, confidence. Source
identifies which write event minted the edge — used by the maintenance
sweep to age + audit edges.

Engineering decision (Apurva): edge types are an Enum to prevent silent
typos at write sites. Allowed (source_type → target_type) pairs are
encoded in EDGE_DIRECTION_TABLE; the facade asserts on write.
"""
from __future__ import annotations

from datetime import datetime
from enum import Enum
from typing import Optional

from pydantic import BaseModel, ConfigDict, Field

from app.graph.entities import BitemporalMixin, TenantMixin


class EdgeType(str, Enum):
    """All 9 edge types per spec §4. Names match the spec verbatim so
    write-event logs and KG dumps are spec-grep-able."""

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


# (source_entity_type_name → set of allowed target_entity_type_names)
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
        description="Edge-type-specific properties — e.g. RESULTED_IN carries shipped_at, VALIDATES carries actual_impact + prediction_hit.",
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


__all__ = ["EdgeType", "Edge", "EDGE_DIRECTION_TABLE"]
