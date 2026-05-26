"""Tests for app.graph.entities — Pydantic schema invariants.

Every test corresponds to a spec invariant or to an engineering safety
guard documented in the entity docstrings.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest
from pydantic import ValidationError

from app.graph.entities import (
    Artifact,
    ArtifactType,
    ConfidenceTier,
    Decision,
    DsAgentTier,
    Hypothesis,
    HypothesisStatus,
    KpiTreeNode,
    Outcome,
    ProvenanceTag,
    SIGNAL_STALENESS_BY_SOURCE_TYPE,
    SIGNAL_STALENESS_DAYS,
    Signal,
    SignalSourceType,
    TrustLevel,
    Workspace,
    WorkspacePlan,
    WorkspaceStage,
    WorkspaceStrategy,
)


# ─────────────────────── helpers ───────────────────────


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _valid_workspace(**overrides) -> Workspace:
    now = _now()
    base = dict(
        workspace_id="ws-1",
        valid_at=now - timedelta(seconds=1),
        transaction_at=now,
        company_name="Acme Corp",
        industry="SaaS",
        stage=WorkspaceStage.GROWTH,
        business_model="B2B SaaS",
        created_at=now - timedelta(days=7),
        updated_at=now,
    )
    base.update(overrides)
    return Workspace(**base)


def _valid_signal(**overrides) -> Signal:
    now = _now()
    base = dict(
        workspace_id="ws-1",
        valid_at=now - timedelta(seconds=1),
        transaction_at=now,
        signal_id="sig-1",
        content="Day-7 retention dropped 5pp last week.",
        source_type=SignalSourceType.ANALYTICS,
        source_tool="amplitude",
        provenance_tag=ProvenanceTag.CONNECTOR_INGEST,
        confidence=0.8,
        stale_after=now + timedelta(days=30),
    )
    base.update(overrides)
    return Signal(**base)


def _valid_hypothesis(**overrides) -> Hypothesis:
    now = _now()
    base = dict(
        workspace_id="ws-1",
        valid_at=now - timedelta(seconds=1),
        transaction_at=now,
        hypothesis_id="hyp-1",
        claim="If we add a Day-3 nudge, retention will improve.",
        predicted_metric="D30 retention",
        predicted_impact_low=2.0,
        predicted_impact_high=5.0,
        predicted_impact_basis="DS Comprehensive Stage 1 SHAP top feature.",
        status=HypothesisStatus.PROPOSED,
        evidence_signal_ids=["sig-1"],
        evidence_count=1,
        confidence_composite=0.7,
        confidence_tier=ConfidenceTier.HIGH,
        reversal_condition="If Day-7 retention drops >2pp post-launch, revert.",
        created_at=now - timedelta(hours=1),
        status_updated_at=now,
    )
    base.update(overrides)
    return Hypothesis(**base)


def _valid_decision(**overrides) -> Decision:
    now = _now()
    base = dict(
        workspace_id="ws-1",
        valid_at=now - timedelta(seconds=1),
        transaction_at=now,
        decision_id="dec-1",
        promoted_from_hypothesis_id="hyp-1",
        claim="If we add a Day-3 nudge, retention will improve.",
        reasoning="Top SHAP feature, supported by 3 signals across analytics + customer voice.",
        approved_by_user_id="user-99",
        approved_at=now - timedelta(minutes=5),
        evidence_snapshot={"signals": ["sig-1"], "ds_finding": {"feature": "nudge"}},
        kpi_tree_snapshot=[
            KpiTreeNode(name="WAU", role="north_star", target_value=10000)
        ],
        reversal_condition="If Day-7 retention drops >2pp post-launch, revert.",
    )
    base.update(overrides)
    return Decision(**base)


def _valid_outcome(**overrides) -> Outcome:
    now = _now()
    base = dict(
        workspace_id="ws-1",
        valid_at=now - timedelta(seconds=1),
        transaction_at=now,
        outcome_id="out-1",
        linked_decision_id="dec-1",
        linked_hypothesis_id="hyp-1",
        feature_name="Day-3 onboarding nudge",
        shipped_at=now - timedelta(days=7),
        metric_measured="D30 retention",
        predicted_impact_low=2.0,
        predicted_impact_high=5.0,
    )
    base.update(overrides)
    return Outcome(**base)


def _valid_artifact(**overrides) -> Artifact:
    now = _now()
    base = dict(
        workspace_id="ws-1",
        valid_at=now - timedelta(seconds=1),
        transaction_at=now,
        artifact_id="art-1",
        artifact_type=ArtifactType.PRD,
        agent_output_snapshot={"title": "Day-3 nudge PRD", "sections": []},
        source_decision_id="dec-1",
    )
    base.update(overrides)
    return Artifact(**base)


# ─────────────────────── bitemporal invariants ───────────────────────


def test_bitemporal_valid_and_transaction_must_differ():
    """Spec invariant: valid_at != transaction_at always."""
    now = _now()
    with pytest.raises(ValidationError, match="differ"):
        _valid_workspace(valid_at=now, transaction_at=now)


def test_bitemporal_transaction_cannot_precede_valid():
    """transaction_at can't be before valid_at — recording can't predate the fact."""
    now = _now()
    with pytest.raises(ValidationError, match="transaction_at < valid_at"):
        _valid_workspace(
            valid_at=now,
            transaction_at=now - timedelta(seconds=1),
        )


def test_bitemporal_naive_datetime_normalized_to_utc():
    """A timezone-naive datetime should be coerced to UTC, not rejected."""
    naive = datetime(2026, 5, 1, 10, 0, 0)
    later = datetime(2026, 5, 1, 10, 0, 1)
    ws = _valid_workspace(valid_at=naive, transaction_at=later)
    assert ws.valid_at.tzinfo is not None
    assert ws.transaction_at.tzinfo is not None


# ─────────────────────── Workspace ───────────────────────


def test_workspace_requires_company_name_and_industry():
    with pytest.raises(ValidationError):
        _valid_workspace(company_name="")
    with pytest.raises(ValidationError):
        _valid_workspace(industry="")


def test_workspace_defaults():
    ws = _valid_workspace()
    assert ws.trust_level == TrustLevel.ALPHA
    assert ws.plan == WorkspacePlan.FREE
    assert ws.kpi_tree == []
    assert ws.competitors == []


def test_kpi_tree_role_must_be_valid():
    """KPI roles are constrained to the spec's 4 values."""
    with pytest.raises(ValidationError):
        KpiTreeNode(name="WAU", role="random")  # type: ignore[arg-type]


def test_workspace_strategy_extra_forbidden():
    """Strategy is the surface delta-classifier writes into; prevent
    silent field additions that would never get read."""
    with pytest.raises(ValidationError):
        WorkspaceStrategy(unknown="value")  # type: ignore[call-arg]


# ─────────────────────── Signal ───────────────────────


def test_signal_content_capped_at_2000():
    with pytest.raises(ValidationError):
        _valid_signal(content="x" * 2001)


def test_signal_confidence_must_be_unit_range():
    with pytest.raises(ValidationError):
        _valid_signal(confidence=1.5)
    with pytest.raises(ValidationError):
        _valid_signal(confidence=-0.1)


def test_outcome_measured_signal_must_have_no_stale_after():
    """Spec invariant: outcome-measured signals never expire."""
    with pytest.raises(ValidationError, match="never expire"):
        _valid_signal(
            provenance_tag=ProvenanceTag.OUTCOME_MEASURED,
            stale_after=_now() + timedelta(days=1),
        )


def test_outcome_measured_signal_with_none_stale_after_is_valid():
    s = _valid_signal(provenance_tag=ProvenanceTag.OUTCOME_MEASURED, stale_after=None)
    assert s.provenance_tag == ProvenanceTag.OUTCOME_MEASURED
    assert s.stale_after is None


def test_signal_staleness_table_covers_all_source_types():
    for st in SignalSourceType:
        assert st in SIGNAL_STALENESS_BY_SOURCE_TYPE


def test_signal_staleness_table_outcome_measured_is_none():
    assert SIGNAL_STALENESS_DAYS[ProvenanceTag.OUTCOME_MEASURED] is None


# ─────────────────────── Hypothesis ───────────────────────


def test_hypothesis_requires_at_least_one_evidence_signal():
    with pytest.raises(ValidationError):
        _valid_hypothesis(evidence_signal_ids=[])


def test_hypothesis_predicted_impact_high_gte_low():
    with pytest.raises(ValidationError, match="predicted_impact_high"):
        _valid_hypothesis(predicted_impact_low=5.0, predicted_impact_high=2.0)


def test_hypothesis_reversal_condition_required():
    with pytest.raises(ValidationError):
        _valid_hypothesis(reversal_condition="")


def test_hypothesis_status_must_be_known():
    with pytest.raises(ValidationError):
        _valid_hypothesis(status="strange-status")  # type: ignore[arg-type]


def test_hypothesis_brief_rank_constrained_to_1_5():
    with pytest.raises(ValidationError):
        _valid_hypothesis(brief_rank=6)
    with pytest.raises(ValidationError):
        _valid_hypothesis(brief_rank=0)


# ─────────────────────── Decision ───────────────────────


def test_decision_promoted_from_hypothesis_id_required():
    """Decisions are never created directly — promoted_from_hypothesis_id is non-null."""
    with pytest.raises(ValidationError):
        _valid_decision(promoted_from_hypothesis_id="")


def test_decision_carries_immutable_snapshots():
    """evidence_snapshot and kpi_tree_snapshot are payload fields,
    not by-reference. Confirm they're freely settable but the schema
    accepts arbitrary dicts/lists for them (the immutability constraint
    is enforced at the facade layer on re-writes, not at the schema)."""
    d = _valid_decision(
        evidence_snapshot={"complex": {"nested": [1, 2, 3]}},
        kpi_tree_snapshot=[
            KpiTreeNode(name="WAU", role="north_star"),
            KpiTreeNode(name="signups", role="primary", parent="WAU"),
        ],
    )
    assert d.evidence_snapshot == {"complex": {"nested": [1, 2, 3]}}
    assert len(d.kpi_tree_snapshot) == 2


def test_decision_reversal_condition_inherited_from_hypothesis():
    """The spec says reversal_condition is copied; we just enforce it
    must exist on the Decision regardless."""
    with pytest.raises(ValidationError):
        _valid_decision(reversal_condition="")


# ─────────────────────── Outcome ───────────────────────


def test_outcome_provenance_locked_to_outcome_measured():
    """Outcome.provenance_tag is always outcome-measured (immutable)."""
    with pytest.raises(ValidationError):
        _valid_outcome(provenance_tag=ProvenanceTag.PM_MANUAL)


def test_outcome_can_have_actual_impact_null_initially():
    o = _valid_outcome()
    assert o.actual_impact is None
    assert o.prediction_hit is None


def test_outcome_round_trip_with_measurement_filled_in():
    o = _valid_outcome(
        actual_impact=3.4,
        actual_impact_measured_at=_now(),
        prediction_hit=True,
        prediction_delta=-0.1,
    )
    blob = o.model_dump_json()
    reloaded = Outcome.model_validate_json(blob)
    assert reloaded.prediction_hit is True
    assert reloaded.actual_impact == 3.4


# ─────────────────────── Artifact ───────────────────────


def test_artifact_version_starts_at_1_and_min_1():
    """A v0 artifact would mean we never actually saved it; reject."""
    with pytest.raises(ValidationError):
        _valid_artifact(version=0)


def test_artifact_current_version_must_be_gte_version():
    """current_version is the latest in the version chain; can't be
    less than the version this entity represents."""
    with pytest.raises(ValidationError, match="current_version"):
        _valid_artifact(version=3, current_version=2)


def test_artifact_edit_distance_non_negative():
    with pytest.raises(ValidationError):
        _valid_artifact(edit_distance_from_v1=-1)


def test_artifact_visualizes_artifact_id_is_optional():
    """Only prototype Artifacts that visualize a PRD set this."""
    a = _valid_artifact(artifact_type=ArtifactType.PROTOTYPE, visualizes_artifact_id="art-prd-1")
    assert a.visualizes_artifact_id == "art-prd-1"
