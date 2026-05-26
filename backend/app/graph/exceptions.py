"""Knowledge Graph error hierarchy."""
from __future__ import annotations


class GraphError(Exception):
    """Base for all graph-related errors."""


class NotConnectedError(GraphError):
    """The graph backend is not reachable (FalkorDB down, etc.)."""


class TenantViolationError(GraphError):
    """An operation attempted to cross workspace boundaries — must never happen.

    Raised when a caller passes a workspace_id that doesn't match the
    entity being read/written. The facade enforces tenant isolation at
    every public function.
    """


class EdgeDirectionError(GraphError):
    """An edge was written with an invalid (source_type, target_type) combination."""


class ImmutabilityError(GraphError):
    """Tried to modify an immutable field — e.g. Decision.evidence_snapshot."""


class HypothesisPromotionError(GraphError):
    """Decision creation attempted without a parent Hypothesis."""
