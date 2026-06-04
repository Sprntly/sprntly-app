"""Sprntly Knowledge Graph package — the brain.

Public API:
    from app.graph import GraphFacade, Entity, Signal, Relationship, Source
    from app.graph import log_agent_decision
    from app.graph import TenantViolationError

See `~/sprntly-shared-contracts.md` (S1 facade · S2 decision log · S3 types)
and `~/sprntly-agent-design.md` (§2 KG model · §4d decision log).
"""
from app.graph.facade import GraphFacade, TenantViolationError
from app.graph.decision_log import log_agent_decision
from app.graph.types import (
    Entity,
    Relationship,
    Signal,
    Source,
    RELATIONSHIP_VOCAB,
    RESERVED_ENTITY_TYPES,
    SIGNAL_SOURCE_TYPES,
    SOURCE_STALE_WINDOW_DAYS,
)

__all__ = [
    "GraphFacade",
    "TenantViolationError",
    "Entity",
    "Relationship",
    "Signal",
    "Source",
    "RELATIONSHIP_VOCAB",
    "RESERVED_ENTITY_TYPES",
    "SIGNAL_SOURCE_TYPES",
    "SOURCE_STALE_WINDOW_DAYS",
    "log_agent_decision",
]
