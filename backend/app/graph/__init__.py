"""Sprntly Knowledge Graph package.

Public API:
    from app.graph import GraphFacade
    graph = GraphFacade.from_env()
    graph.initialize()

Entity + edge models:
    from app.graph.entities import Workspace, Signal, Hypothesis, Decision, Outcome, Artifact
    from app.graph.edges import Edge, EdgeType
"""
from app.graph.edges import (
    Edge,
    EdgeType,
    EDGE_DIRECTION_TABLE,
    EDGE_METADATA_SCHEMA,
    SupportsMetadata,
    ContradictsMetadata,
    PromotedToMetadata,
    MotivatedMetadata,
    ResultedInMetadata,
    ValidatesMetadata,
    UpdatesWeightMetadata,
    ScopedToMetadata,
    InformsMetadata,
    ExpressedAsMetadata,
    VisualizesMetadata,
    make_supports_edge,
    make_contradicts_edge,
    make_promoted_to_edge,
    make_motivated_edge,
    make_resulted_in_edge,
    make_validates_edge,
    make_updates_weight_edge,
    make_scoped_to_edge,
    make_informs_edge,
    make_expressed_as_edge,
    make_visualizes_edge,
)
from app.graph.provenance import (
    ProvenanceChain,
    ProvenanceWalkStep,
    trace_outcome_provenance,
    trace_provenance,
)
from app.graph.entities import (
    Artifact,
    ArtifactType,
    BitemporalMixin,
    ConfidenceTier,
    Decision,
    DismissedReason,
    DsAgentTier,
    Hypothesis,
    HypothesisStatus,
    KpiTreeNode,
    Outcome,
    ProvenanceTag,
    Signal,
    SignalSourceType,
    SIGNAL_STALENESS_BY_SOURCE_TYPE,
    SIGNAL_STALENESS_DAYS,
    TenantMixin,
    TrustLevel,
    Workspace,
    WorkspacePlan,
    WorkspaceStage,
    WorkspaceStrategy,
)
from app.graph.exceptions import (
    EdgeDirectionError,
    GraphError,
    HypothesisPromotionError,
    ImmutabilityError,
    NotConnectedError,
    TenantViolationError,
)
from app.graph.facade import GraphFacade

__all__ = [
    # facade
    "GraphFacade",
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
    # edges
    "Edge",
    "EdgeType",
    "EDGE_DIRECTION_TABLE",
    "EDGE_METADATA_SCHEMA",
    # edge metadata schemas
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
    # edge helper constructors
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
    # provenance
    "ProvenanceChain",
    "ProvenanceWalkStep",
    "trace_provenance",
    "trace_outcome_provenance",
    # exceptions
    "GraphError",
    "NotConnectedError",
    "TenantViolationError",
    "EdgeDirectionError",
    "ImmutabilityError",
    "HypothesisPromotionError",
    # tables + mixins
    "SIGNAL_STALENESS_DAYS",
    "SIGNAL_STALENESS_BY_SOURCE_TYPE",
    "BitemporalMixin",
    "TenantMixin",
]
