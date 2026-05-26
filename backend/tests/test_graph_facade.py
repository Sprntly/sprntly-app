"""Tests for app.graph.facade — tenant isolation + promotion-only Decision.

Uses the SqliteBackend with an isolated tmp DB so we don't touch the
production schema. FalkorBackend integration tests are deferred to the
P1-10/P1-11 PRs since they need the container running.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from app.graph import (
    Decision,
    Edge,
    EdgeType,
    GraphError,
    HypothesisPromotionError,
    HypothesisStatus,
    ImmutabilityError,
    KpiTreeNode,
    ProvenanceTag,
    Signal,
    SignalSourceType,
    TenantViolationError,
    Workspace,
    WorkspaceStage,
)
from app.graph.backends.sqlite_backend import SqliteBackend
from app.graph.entities import (
    Artifact,
    ArtifactType,
    ConfidenceTier,
    Hypothesis,
)
from app.graph.facade import GraphFacade


@pytest.fixture
def facade(tmp_path) -> GraphFacade:
    db_path = tmp_path / "graph.db"
    backend = SqliteBackend(db_path=str(db_path))
    backend.initialize_schema()
    return GraphFacade(backend)


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _ws(workspace_id: str = "ws-1") -> Workspace:
    now = _now()
    return Workspace(
        workspace_id=workspace_id,
        valid_at=now - timedelta(seconds=1),
        transaction_at=now,
        company_name="Acme",
        industry="SaaS",
        stage=WorkspaceStage.GROWTH,
        business_model="B2B SaaS",
        created_at=now - timedelta(days=1),
        updated_at=now,
    )


def _sig(workspace_id: str = "ws-1", signal_id: str = "sig-1") -> Signal:
    now = _now()
    return Signal(
        workspace_id=workspace_id,
        valid_at=now - timedelta(seconds=1),
        transaction_at=now,
        signal_id=signal_id,
        content="content",
        source_type=SignalSourceType.ANALYTICS,
        source_tool="amplitude",
        provenance_tag=ProvenanceTag.CONNECTOR_INGEST,
        confidence=0.8,
        stale_after=now + timedelta(days=30),
    )


def _hyp(workspace_id: str = "ws-1", hypothesis_id: str = "hyp-1") -> Hypothesis:
    now = _now()
    return Hypothesis(
        workspace_id=workspace_id,
        valid_at=now - timedelta(seconds=1),
        transaction_at=now,
        hypothesis_id=hypothesis_id,
        claim="If we ship X, retention improves.",
        predicted_metric="D30 retention",
        predicted_impact_low=2.0,
        predicted_impact_high=5.0,
        predicted_impact_basis="SHAP top feature in comprehensive run.",
        status=HypothesisStatus.PROPOSED,
        evidence_signal_ids=["sig-1"],
        evidence_count=1,
        confidence_composite=0.7,
        confidence_tier=ConfidenceTier.HIGH,
        reversal_condition="If retention drops >2pp post-launch, revert.",
        created_at=now - timedelta(hours=1),
        status_updated_at=now,
    )


def _dec(
    workspace_id: str = "ws-1",
    decision_id: str = "dec-1",
    promoted_from: str = "hyp-1",
    evidence: dict | None = None,
) -> Decision:
    now = _now()
    return Decision(
        workspace_id=workspace_id,
        valid_at=now - timedelta(seconds=1),
        transaction_at=now,
        decision_id=decision_id,
        promoted_from_hypothesis_id=promoted_from,
        claim="If we ship X, retention improves.",
        reasoning="Top SHAP feature, supported by 3 signals.",
        approved_by_user_id="user-99",
        approved_at=now,
        evidence_snapshot=evidence or {"signals": ["sig-1"]},
        kpi_tree_snapshot=[KpiTreeNode(name="WAU", role="north_star")],
        reversal_condition="If retention drops >2pp post-launch, revert.",
    )


# ─────────────────────── tenant isolation ───────────────────────


def test_write_workspace_rejects_mismatched_tenant(facade):
    """Caller asks 'write into workspace X' but the Workspace entity is
    tagged with workspace_id Y → TenantViolationError."""
    ws = _ws("ws-1")
    with pytest.raises(TenantViolationError):
        facade.write_workspace("ws-2", ws)


def test_write_signal_rejects_mismatched_tenant(facade):
    sig = _sig("ws-1")
    with pytest.raises(TenantViolationError):
        facade.write_signal("ws-other", sig)


def test_write_hypothesis_rejects_mismatched_tenant(facade):
    hyp = _hyp("ws-1")
    with pytest.raises(TenantViolationError):
        facade.write_hypothesis("ws-other", hyp)


def test_write_outcome_rejects_mismatched_tenant(facade):
    from app.graph.entities import Outcome

    out = Outcome(
        workspace_id="ws-1",
        valid_at=_now() - timedelta(seconds=1),
        transaction_at=_now(),
        outcome_id="out-1",
        linked_decision_id="dec-1",
        linked_hypothesis_id="hyp-1",
        feature_name="thing",
        shipped_at=_now() - timedelta(days=1),
        metric_measured="D30",
        predicted_impact_low=1.0,
        predicted_impact_high=3.0,
    )
    with pytest.raises(TenantViolationError):
        facade.write_outcome("ws-other", out)


def test_write_artifact_rejects_mismatched_tenant(facade):
    art = Artifact(
        workspace_id="ws-1",
        valid_at=_now() - timedelta(seconds=1),
        transaction_at=_now(),
        artifact_id="art-1",
        artifact_type=ArtifactType.PRD,
        agent_output_snapshot={"title": "X"},
    )
    with pytest.raises(TenantViolationError):
        facade.write_artifact("ws-other", art)


# ─────────────────────── promotion-only Decision ───────────────────────


def test_decision_requires_source_hypothesis_exists(facade):
    """Decision creation must reference a Hypothesis that's already in
    the same workspace."""
    facade.write_workspace("ws-1", _ws())
    # No hypothesis written yet.
    dec = _dec()
    with pytest.raises(HypothesisPromotionError, match="not found"):
        facade.promote_hypothesis_to_decision("ws-1", dec)


def test_decision_requires_promoted_from_field(facade):
    facade.write_workspace("ws-1", _ws())
    facade.write_hypothesis("ws-1", _hyp())
    # Construct a Decision with an empty promoted_from_hypothesis_id —
    # Pydantic will reject that at construction. So we test the facade's
    # additional guard via a direct attribute mutation.
    # Pydantic v2 lets us bypass validation only via model_construct.
    dec = Decision.model_construct(
        **_dec().model_dump(),
    )
    dec.promoted_from_hypothesis_id = ""  # bypass pydantic to test facade guard
    with pytest.raises(HypothesisPromotionError):
        facade.promote_hypothesis_to_decision("ws-1", dec)


def test_decision_idempotent_on_identical_snapshot(facade):
    """Writing the same Decision twice (same evidence_snapshot) is OK —
    idempotent retries shouldn't fail."""
    facade.write_workspace("ws-1", _ws())
    facade.write_hypothesis("ws-1", _hyp())
    dec = _dec()
    facade.promote_hypothesis_to_decision("ws-1", dec)
    facade.promote_hypothesis_to_decision("ws-1", dec)  # no-op
    got = facade.get_decision("ws-1", "dec-1")
    assert got is not None and got.decision_id == "dec-1"


def test_decision_immutable_on_different_snapshot(facade):
    """Re-writing the same decision_id with a different evidence_snapshot
    is an immutability violation — spec-mandated."""
    facade.write_workspace("ws-1", _ws())
    facade.write_hypothesis("ws-1", _hyp())
    dec1 = _dec(evidence={"signals": ["sig-1"]})
    facade.promote_hypothesis_to_decision("ws-1", dec1)
    dec2 = _dec(evidence={"signals": ["sig-1", "sig-2"]})  # changed snapshot
    with pytest.raises(ImmutabilityError):
        facade.promote_hypothesis_to_decision("ws-1", dec2)


# ─────────────────────── round-trip basics ───────────────────────


def test_round_trip_workspace(facade):
    ws = _ws()
    facade.write_workspace("ws-1", ws)
    got = facade.get_workspace("ws-1")
    assert got is not None
    assert got.company_name == "Acme"


def test_round_trip_signal(facade):
    facade.write_signal("ws-1", _sig())
    got = facade.get_signal("ws-1", "sig-1")
    assert got is not None
    assert got.confidence == 0.8


def test_round_trip_hypothesis(facade):
    facade.write_hypothesis("ws-1", _hyp())
    got = facade.get_hypothesis("ws-1", "hyp-1")
    assert got is not None
    assert got.status == HypothesisStatus.PROPOSED


def test_round_trip_artifact(facade):
    art = Artifact(
        workspace_id="ws-1",
        valid_at=_now() - timedelta(seconds=1),
        transaction_at=_now(),
        artifact_id="art-prd-1",
        artifact_type=ArtifactType.PRD,
        agent_output_snapshot={"title": "Onboarding nudge PRD"},
        source_decision_id="dec-1",
    )
    facade.write_artifact("ws-1", art)
    got = facade.get_artifact("ws-1", "art-prd-1")
    assert got is not None
    assert got.artifact_type == ArtifactType.PRD


def test_load_session_context_returns_top_n(facade):
    """The session-context query collects workspace + 10 active hypotheses
    + 5 recent decisions + 3 measured outcomes per spec §7."""
    facade.write_workspace("ws-1", _ws())
    # Write 15 hypotheses; expect only 10 in context.
    for i in range(15):
        h = _hyp(hypothesis_id=f"hyp-{i}")
        facade.write_hypothesis("ws-1", h)
    ctx = facade.load_session_context("ws-1")
    assert ctx["workspace"] is not None
    assert len(ctx["active_hypotheses"]) == 10


# ─────────────────────── edges ───────────────────────


def test_edge_direction_enforced(facade):
    """SUPPORTS edges must go Signal → Hypothesis. Anything else raises."""
    edge_bad = Edge(
        workspace_id="ws-1",
        valid_at=_now() - timedelta(seconds=1),
        transaction_at=_now(),
        edge_type=EdgeType.SUPPORTS,
        source_entity_id="dec-1",
        source_entity_type="Decision",  # wrong — should be Signal
        target_entity_id="hyp-1",
        target_entity_type="Hypothesis",
        source="synthesis_agent_run",
        confidence=0.8,
    )
    with pytest.raises(ValueError, match="SUPPORTS"):
        facade.write_edge("ws-1", edge_bad)


def test_edge_round_trip(facade):
    edge = Edge(
        workspace_id="ws-1",
        valid_at=_now() - timedelta(seconds=1),
        transaction_at=_now(),
        edge_type=EdgeType.SUPPORTS,
        source_entity_id="sig-1",
        source_entity_type="Signal",
        target_entity_id="hyp-1",
        target_entity_type="Hypothesis",
        source="synthesis_agent_run",
        confidence=0.9,
    )
    facade.write_edge("ws-1", edge)
    edges = facade.edges_from("ws-1", "sig-1", edge_type=EdgeType.SUPPORTS)
    assert len(edges) == 1
    assert edges[0].target_entity_id == "hyp-1"


def test_edge_tenant_isolation(facade):
    edge = Edge(
        workspace_id="ws-1",
        valid_at=_now() - timedelta(seconds=1),
        transaction_at=_now(),
        edge_type=EdgeType.SUPPORTS,
        source_entity_id="sig-1",
        source_entity_type="Signal",
        target_entity_id="hyp-1",
        target_entity_type="Hypothesis",
        source="synthesis_agent_run",
        confidence=0.9,
    )
    with pytest.raises(TenantViolationError):
        facade.write_edge("ws-other", edge)


# ─────────────────────── tenant boundary (multi-workspace) ───────────────────────


def test_workspaces_are_isolated(facade):
    """Writing the same entity ID in two workspaces stays separate."""
    facade.write_workspace("ws-1", _ws("ws-1"))
    facade.write_workspace("ws-2", _ws("ws-2"))
    facade.write_signal("ws-1", _sig(workspace_id="ws-1", signal_id="sig-shared"))
    facade.write_signal("ws-2", _sig(workspace_id="ws-2", signal_id="sig-shared"))
    # Reading from ws-1 only returns ws-1's signal.
    assert facade.get_signal("ws-1", "sig-shared") is not None
    assert facade.get_signal("ws-2", "sig-shared") is not None
    # No cross-contamination: list_active_signals scoped.
    ws1_sigs = facade.list_active_signals("ws-1")
    ws2_sigs = facade.list_active_signals("ws-2")
    assert all(s.workspace_id == "ws-1" for s in ws1_sigs)
    assert all(s.workspace_id == "ws-2" for s in ws2_sigs)


def test_healthy_returns_true_after_init(facade):
    assert facade.healthy() is True
