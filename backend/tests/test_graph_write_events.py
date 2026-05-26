"""Tests for app.graph.write_events — the 9 spec §5 named events.

All tests use SqliteBackend with an isolated tmp DB. We assert
end-to-end behavior per spec section, plus the cross-cutting invariants
(async maintenance sweep, partial-write rollback, etc.).
"""
from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone
from typing import Optional
from unittest import mock

import pytest

from app.graph import (
    Edge,
    EdgeType,
    GraphError,
    HypothesisStatus,
    KpiTreeNode,
    ProvenanceTag,
    Signal,
    SignalSourceType,
    Workspace,
    WorkspaceStage,
)
from app.graph.backends.sqlite_backend import SqliteBackend
from app.graph.entities import (
    Artifact,
    ArtifactType,
    ConfidenceTier,
    DismissedReason,
    Hypothesis,
)
from app.graph.facade import GraphFacade
from app.graph.maintenance import (
    SIGNAL_RELIABILITY_DELTA_HIT,
    SIGNAL_RELIABILITY_DELTA_MISS,
    run_maintenance_sweep,
)
from app.graph.write_events import (
    ArtifactEditPayload,
    BriefRecommendationApprovedPayload,
    BriefRecommendationDismissedPayload,
    ConnectorSyncPayload,
    FeatureShippedPayload,
    OnboardingCompletePayload,
    OutcomeMeasuredPayload,
    PrdGeneratedPayload,
    RECURRING_PATTERN_MIN_HITS,
    SynthesisAgentRunPayload,
    _DeltaCategory,
    _RECURRING_PATTERN_COUNTS,
    event_5_1_onboarding_complete,
    event_5_2_connector_sync,
    event_5_3_synthesis_agent_run,
    event_5_4_brief_recommendation_dismissed,
    event_5_5_brief_recommendation_approved,
    event_5_6_prd_generated,
    event_5_7_artifact_edit,
    event_5_8_feature_shipped,
    event_5_9_outcome_measured,
)


# ─────────────────────── fixtures ───────────────────────


@pytest.fixture
def facade(tmp_path) -> GraphFacade:
    db_path = tmp_path / "graph-events.db"
    backend = SqliteBackend(db_path=str(db_path))
    backend.initialize_schema()
    return GraphFacade(backend)


@pytest.fixture(autouse=True)
def _reset_pattern_counter():
    _RECURRING_PATTERN_COUNTS.clear()
    yield
    _RECURRING_PATTERN_COUNTS.clear()


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _onboard(facade: GraphFacade, ws_id: str = "ws-1") -> None:
    """Helper: run §5.1 with sensible defaults so subsequent events have a workspace."""
    payload = OnboardingCompletePayload(
        workspace_id=ws_id,
        company_name="Acme",
        industry="SaaS",
        stage=WorkspaceStage.GROWTH,
        business_model="B2B SaaS",
        kpi_tree=[KpiTreeNode(name="WAU", role="north_star")],
        initial_signals=[
            {"content": f"PM signal {i}", "source_type": "manual"}
            for i in range(3)
        ],
        bootstrap_hypotheses=[
            {
                "claim": "Onboarding nudge will lift activation by 1-3%.",
                "predicted_metric": "activation_d1",
                "predicted_impact_low": 1.0,
                "predicted_impact_high": 3.0,
            }
        ],
    )
    event_5_1_onboarding_complete(facade, payload)


# ─────────────────────── §5.1 onboarding_complete ───────────────────────


def test_event_5_1_creates_workspace_signals_and_candidate_hypotheses(facade):
    payload = OnboardingCompletePayload(
        workspace_id="ws-onb",
        company_name="Acme",
        industry="SaaS",
        stage=WorkspaceStage.GROWTH,
        business_model="B2B SaaS",
        kpi_tree=[KpiTreeNode(name="WAU", role="north_star")],
        initial_signals=[
            {"content": "Activation drop after week 2", "source_type": "manual"},
            {"content": "Sales reports churn rising", "source_type": "manual"},
            {"content": "Support tickets mention onboarding friction", "source_type": "manual"},
        ],
        bootstrap_hypotheses=[
            {
                "claim": "If we add an onboarding nudge, activation lifts.",
                "predicted_metric": "activation_d1",
                "predicted_impact_low": 1.0,
                "predicted_impact_high": 4.0,
            },
            {
                "claim": "If we reduce time-to-value, retention improves.",
                "predicted_metric": "d30_retention",
                "predicted_impact_low": 2.0,
                "predicted_impact_high": 5.0,
            },
        ],
    )
    result = event_5_1_onboarding_complete(facade, payload)

    assert facade.get_workspace("ws-onb") is not None
    assert len(result["signal_ids"]) == 3
    assert len(result["hypothesis_ids"]) == 2

    # Spec §5.1: bootstrap hypotheses are CANDIDATE w/ confidence 0.3.
    for hid in result["hypothesis_ids"]:
        h = facade.get_hypothesis("ws-onb", hid)
        assert h is not None
        assert h.status == HypothesisStatus.CANDIDATE
        assert h.confidence_composite == pytest.approx(0.3)

    # Spec §5.1: pm-manual signals have stale_after ~ +60d.
    for sid in result["signal_ids"]:
        s = facade.get_signal("ws-onb", sid)
        assert s is not None
        assert s.provenance_tag == ProvenanceTag.PM_MANUAL
        assert s.stale_after is not None
        assert s.stale_after - _now() > timedelta(days=55)


def test_event_5_1_rolls_back_on_failure(facade, monkeypatch):
    """If a downstream write fails, workspace + earlier signals must roll back."""
    payload = OnboardingCompletePayload(
        workspace_id="ws-bad",
        company_name="Acme",
        industry="SaaS",
        stage=WorkspaceStage.GROWTH,
        business_model="B2B SaaS",
        initial_signals=[{"content": "x", "source_type": "manual"}],
        bootstrap_hypotheses=[
            {
                "claim": "Bootstrapped prior hypothesis claim.",
                "predicted_metric": "x",
                "predicted_impact_low": 1.0,
                "predicted_impact_high": 2.0,
            }
        ],
    )
    # Force the hypothesis write to blow up.
    real_write = facade.write_hypothesis

    def boom(*args, **kwargs):
        raise RuntimeError("simulated DB failure")

    monkeypatch.setattr(facade, "write_hypothesis", boom)
    with pytest.raises(GraphError, match="event_5_1"):
        event_5_1_onboarding_complete(facade, payload)

    # Restore for the assertion read path.
    monkeypatch.setattr(facade, "write_hypothesis", real_write)
    # Workspace + signal should be gone (rolled back).
    assert facade.get_workspace("ws-bad") is None


# ─────────────────────── §5.2 connector_sync ───────────────────────


def test_event_5_2_creates_new_signals(facade):
    _onboard(facade)
    payload = ConnectorSyncPayload(
        workspace_id="ws-1",
        connector="amplitude",
        new_signals=[
            {"content": "Activation drops 8% week 2", "source_type": "analytics"},
            {"content": "Power users open feature X daily", "source_type": "analytics"},
        ],
    )
    result = event_5_2_connector_sync(facade, payload)
    assert len(result["created"]) == 2


def test_event_5_2_bumps_existing_signal_on_exact_match(facade):
    _onboard(facade)
    # First write.
    payload1 = ConnectorSyncPayload(
        workspace_id="ws-1",
        connector="amplitude",
        new_signals=[{"content": "Activation 8% drop", "source_type": "analytics"}],
    )
    r1 = event_5_2_connector_sync(facade, payload1)
    first_sig_id = r1["created"][0]
    first = facade.get_signal("ws-1", first_sig_id)

    # Second sync with the same content → entity resolution hit → bumped.
    payload2 = ConnectorSyncPayload(
        workspace_id="ws-1",
        connector="amplitude",
        new_signals=[{"content": "Activation 8% drop", "source_type": "analytics"}],
    )
    r2 = event_5_2_connector_sync(facade, payload2)
    assert r2["created"] == []
    assert len(r2["bumped"]) == 1
    assert r2["bumped"][0] == first_sig_id

    # valid_at should have advanced.
    after = facade.get_signal("ws-1", first_sig_id)
    assert after.valid_at >= first.valid_at


def test_event_5_2_promotes_candidate_to_proposed_at_threshold(facade):
    """A CANDIDATE hypothesis with >=3 SUPPORTS edges from >=2 source_types
    must auto-promote to PROPOSED."""
    _onboard(facade)
    # Add 3 signals across 2 source_types and SUPPORTS edges to the
    # bootstrap candidate hypothesis.
    ids = facade._backend.all_entity_ids("ws-1")
    cand_hyp_id = ids["hypotheses"][0]

    sig_specs = [
        ("analytics", "amplitude", "evidence A"),
        ("analytics", "amplitude", "evidence B"),
        ("customer_voice", "zendesk", "evidence C"),
    ]
    new_sig_ids: list[str] = []
    for src_type, tool, content in sig_specs:
        sync = ConnectorSyncPayload(
            workspace_id="ws-1",
            connector=tool,
            new_signals=[{"content": content, "source_type": src_type, "source_tool": tool}],
        )
        result = event_5_2_connector_sync(facade, sync)
        new_sig_ids.extend(result["created"])

    # Manually wire SUPPORTS edges (synthesis_agent_run would do this in prod).
    for sid in new_sig_ids:
        facade.write_edge(
            "ws-1",
            Edge(
                workspace_id="ws-1",
                valid_at=_now() - timedelta(seconds=1),
                transaction_at=_now(),
                edge_type=EdgeType.SUPPORTS,
                source_entity_id=sid,
                source_entity_type="Signal",
                target_entity_id=cand_hyp_id,
                target_entity_type="Hypothesis",
                source="test_setup",
                confidence=0.7,
            ),
        )

    # Trigger another connector_sync — its evidence_count recompute path
    # is where promotion happens.
    event_5_2_connector_sync(
        facade,
        ConnectorSyncPayload(workspace_id="ws-1", connector="amplitude", new_signals=[]),
    )
    h = facade.get_hypothesis("ws-1", cand_hyp_id)
    assert h.status == HypothesisStatus.PROPOSED, (
        f"expected PROPOSED, got {h.status} evidence_count={h.evidence_count}"
    )


def test_event_5_2_does_not_promote_with_single_source_type(facade):
    """Below the >=2 distinct source_types threshold, stays CANDIDATE."""
    _onboard(facade)
    cand_hyp_id = facade._backend.all_entity_ids("ws-1")["hypotheses"][0]
    # 3 signals, all source_type=analytics — same single type.
    new_sig_ids: list[str] = []
    for i in range(3):
        sync = ConnectorSyncPayload(
            workspace_id="ws-1",
            connector="amplitude",
            new_signals=[{"content": f"evidence {i}", "source_type": "analytics", "source_tool": "amplitude"}],
        )
        new_sig_ids.extend(event_5_2_connector_sync(facade, sync)["created"])

    for sid in new_sig_ids:
        facade.write_edge(
            "ws-1",
            Edge(
                workspace_id="ws-1",
                valid_at=_now() - timedelta(seconds=1),
                transaction_at=_now(),
                edge_type=EdgeType.SUPPORTS,
                source_entity_id=sid,
                source_entity_type="Signal",
                target_entity_id=cand_hyp_id,
                target_entity_type="Hypothesis",
                source="test_setup",
                confidence=0.7,
            ),
        )
    event_5_2_connector_sync(
        facade,
        ConnectorSyncPayload(workspace_id="ws-1", connector="amplitude", new_signals=[]),
    )
    h = facade.get_hypothesis("ws-1", cand_hyp_id)
    assert h.status == HypothesisStatus.CANDIDATE


# ─────────────────────── §5.3 synthesis_agent_run ───────────────────────


def test_event_5_3_reads_before_writing(facade, monkeypatch):
    """Spec §5.3 invariant: must call load_session_context BEFORE any write."""
    _onboard(facade)
    seen_order: list[str] = []

    real_load = facade.load_session_context
    real_write = facade.write_hypothesis

    def tracked_load(*args, **kwargs):
        seen_order.append("load_session_context")
        return real_load(*args, **kwargs)

    def tracked_write(*args, **kwargs):
        seen_order.append("write_hypothesis")
        return real_write(*args, **kwargs)

    monkeypatch.setattr(facade, "load_session_context", tracked_load)
    monkeypatch.setattr(facade, "write_hypothesis", tracked_write)

    sig_ids = facade._backend.all_entity_ids("ws-1")["signals"]
    payload = SynthesisAgentRunPayload(
        workspace_id="ws-1",
        brief_id="brief-1",
        recommendations=[
            {
                "claim": "If we ship X, retention improves by 2-5%.",
                "predicted_metric": "d30_retention",
                "evidence_signal_ids": [sig_ids[0]],
                "confidence_composite": 0.72,
            }
        ],
    )
    event_5_3_synthesis_agent_run(facade, payload)

    assert seen_order[0] == "load_session_context"
    assert "write_hypothesis" in seen_order
    assert seen_order.index("load_session_context") < seen_order.index("write_hypothesis")


def test_event_5_3_creates_one_hypothesis_per_rec(facade):
    _onboard(facade)
    sig_id = facade._backend.all_entity_ids("ws-1")["signals"][0]
    payload = SynthesisAgentRunPayload(
        workspace_id="ws-1",
        brief_id="brief-1",
        recommendations=[
            {
                "claim": f"Brief rec {i}: ship feature {i}.",
                "evidence_signal_ids": [sig_id],
                "confidence_composite": 0.7,
            }
            for i in range(3)
        ],
    )
    result = event_5_3_synthesis_agent_run(facade, payload)
    assert len(result["hypothesis_ids"]) == 3
    for hid in result["hypothesis_ids"]:
        h = facade.get_hypothesis("ws-1", hid)
        assert h.brief_id == "brief-1"


def test_event_5_3_raises_when_workspace_missing(facade):
    payload = SynthesisAgentRunPayload(
        workspace_id="ws-nope", brief_id="b-1", recommendations=[]
    )
    with pytest.raises(GraphError, match="workspace"):
        event_5_3_synthesis_agent_run(facade, payload)


# ─────────────────────── §5.4 brief_recommendation_dismissed ─────────


def test_event_5_4_rejects_hypothesis_and_writes_learning_signal(facade):
    _onboard(facade)
    hyp_id = facade._backend.all_entity_ids("ws-1")["hypotheses"][0]
    result = event_5_4_brief_recommendation_dismissed(
        facade,
        BriefRecommendationDismissedPayload(
            workspace_id="ws-1",
            hypothesis_id=hyp_id,
            dismissed_reason=DismissedReason.WRONG_PRIORITY,
            pm_note="Q3 focus is different.",
        ),
    )
    h = facade.get_hypothesis("ws-1", hyp_id)
    assert h.status == HypothesisStatus.REJECTED
    assert h.dismissed_reason == DismissedReason.WRONG_PRIORITY

    learning = facade.get_signal("ws-1", result["learning_signal_id"])
    assert learning is not None
    assert learning.provenance_tag == ProvenanceTag.PM_MANUAL
    assert hyp_id in learning.content


# ─────────────────────── §5.5 brief_recommendation_approved ─────────


def test_event_5_5_creates_decision_with_frozen_snapshots_and_edge(facade):
    _onboard(facade)
    hyp_id = facade._backend.all_entity_ids("ws-1")["hypotheses"][0]
    result = event_5_5_brief_recommendation_approved(
        facade,
        BriefRecommendationApprovedPayload(
            workspace_id="ws-1",
            hypothesis_id=hyp_id,
            approved_by_user_id="user-1",
            reasoning="High-conviction; ship it.",
        ),
    )
    dec_id = result["decision_id"]
    dec = facade.get_decision("ws-1", dec_id)
    assert dec is not None
    # evidence_snapshot frozen at approval — contains the supporting signals.
    assert "signals" in dec.evidence_snapshot
    # kpi_tree_snapshot frozen.
    assert any(node.role == "north_star" for node in dec.kpi_tree_snapshot)
    # Hypothesis is CONFIRMED + back-linked.
    h = facade.get_hypothesis("ws-1", hyp_id)
    assert h.status == HypothesisStatus.CONFIRMED
    assert h.promoted_to_decision_id == dec_id
    # PROMOTED_TO edge from hypothesis → decision.
    edges = facade.edges_from("ws-1", hyp_id, edge_type=EdgeType.PROMOTED_TO)
    assert len(edges) == 1
    assert edges[0].target_entity_id == dec_id


# ─────────────────────── §5.6 prd_generated ───────────────────────


def test_event_5_6_creates_artifact_and_motivated_edge(facade):
    _onboard(facade)
    hyp_id = facade._backend.all_entity_ids("ws-1")["hypotheses"][0]
    approval = event_5_5_brief_recommendation_approved(
        facade,
        BriefRecommendationApprovedPayload(
            workspace_id="ws-1",
            hypothesis_id=hyp_id,
            approved_by_user_id="user-1",
            reasoning="High-conviction; ship it now.",
        ),
    )
    dec_id = approval["decision_id"]
    result = event_5_6_prd_generated(
        facade,
        PrdGeneratedPayload(
            workspace_id="ws-1",
            decision_id=dec_id,
            feature_id="feat-onboarding-nudge",
            prd_json={"title": "Onboarding Nudge", "sections": [{"title": "Intro"}]},
        ),
    )
    art_id = result["artifact_id"]
    art = facade.get_artifact("ws-1", art_id)
    assert art is not None
    assert art.artifact_type == ArtifactType.PRD
    assert art.version == 1
    assert art.edit_distance_from_v1 == 0
    assert art.agent_output_snapshot["title"] == "Onboarding Nudge"
    # MOTIVATED edge from decision → artifact.
    edges = facade.edges_from("ws-1", dec_id, edge_type=EdgeType.MOTIVATED)
    assert len(edges) == 1
    assert edges[0].target_entity_id == art_id
    # Decision feature_id + prd_generated_at set.
    dec = facade.get_decision("ws-1", dec_id)
    assert dec.feature_id == "feat-onboarding-nudge"
    assert dec.prd_generated_at is not None


# ─────────────────────── §5.7 artifact_edit ───────────────────────


def _prd_artifact(facade: GraphFacade, ws_id: str = "ws-1") -> str:
    _onboard(facade, ws_id)
    hyp_id = facade._backend.all_entity_ids(ws_id)["hypotheses"][0]
    approval = event_5_5_brief_recommendation_approved(
        facade,
        BriefRecommendationApprovedPayload(
            workspace_id=ws_id,
            hypothesis_id=hyp_id,
            approved_by_user_id="user-1",
            reasoning="High-conviction; ship it now.",
        ),
    )
    prd = event_5_6_prd_generated(
        facade,
        PrdGeneratedPayload(
            workspace_id=ws_id,
            decision_id=approval["decision_id"],
            feature_id="feat-x",
            prd_json={"title": "PRD", "sections": []},
        ),
    )
    return prd["artifact_id"]


def test_event_5_7_context_gap_creates_new_signal(facade):
    art_id = _prd_artifact(facade)
    result = event_5_7_artifact_edit(
        facade,
        ArtifactEditPayload(
            workspace_id="ws-1",
            artifact_id=art_id,
            edit_description="Add missing context on competitor pricing data on the homepage.",
            edit_distance=3,
        ),
    )
    assert result["delta_category"] == _DeltaCategory.CONTEXT_GAP
    sig = facade.get_signal("ws-1", result["signal_id"])
    assert sig is not None
    assert sig.provenance_tag == ProvenanceTag.AGENT_INFERRED
    # Artifact bumped.
    art = facade.get_artifact("ws-1", art_id)
    assert art.current_version == 2
    assert art.edit_distance_from_v1 == 3


def test_event_5_7_preference_updates_workspace_preferences(facade):
    art_id = _prd_artifact(facade)
    result = event_5_7_artifact_edit(
        facade,
        ArtifactEditPayload(
            workspace_id="ws-1",
            artifact_id=art_id,
            edit_description="Always use bullet style for the success metrics section.",
        ),
    )
    assert result["delta_category"] == _DeltaCategory.PREFERENCE
    ws = facade.get_workspace("ws-1")
    assert "prd" in ws.preferences
    assert any("bullet style" in p for p in ws.preferences["prd"])


def test_event_5_7_recurring_pattern_creates_candidate_hypothesis(facade):
    art_id = _prd_artifact(facade)
    # Fire the same pattern N times; only the Nth crosses the threshold.
    for i in range(RECURRING_PATTERN_MIN_HITS):
        result = event_5_7_artifact_edit(
            facade,
            ArtifactEditPayload(
                workspace_id="ws-1",
                artifact_id=art_id,
                edit_description="Move success metrics section to top, same as last time and again.",
            ),
        )
    assert result["delta_category"] == _DeltaCategory.RECURRING_PATTERN
    assert "candidate_hypothesis_id" in result
    h = facade.get_hypothesis("ws-1", result["candidate_hypothesis_id"])
    assert h is not None
    assert h.status == HypothesisStatus.CANDIDATE


def test_event_5_7_other_category_no_writeback_only_version_bump(facade):
    art_id = _prd_artifact(facade)
    result = event_5_7_artifact_edit(
        facade,
        ArtifactEditPayload(
            workspace_id="ws-1",
            artifact_id=art_id,
            edit_description="Tweaked one sentence.",
        ),
    )
    assert result["delta_category"] == _DeltaCategory.OTHER
    # No signal_id, no candidate_hypothesis_id keys in writeback.
    assert "signal_id" not in result
    assert "candidate_hypothesis_id" not in result
    art = facade.get_artifact("ws-1", art_id)
    assert art.current_version == 2


def test_event_5_7_classifier_override(facade):
    """The classifier seam — tests should not need an LLM."""
    art_id = _prd_artifact(facade)
    result = event_5_7_artifact_edit(
        facade,
        ArtifactEditPayload(
            workspace_id="ws-1",
            artifact_id=art_id,
            edit_description="literally anything",
        ),
        classifier_override=lambda _: _DeltaCategory.CONTEXT_GAP,
    )
    assert result["delta_category"] == _DeltaCategory.CONTEXT_GAP
    assert "signal_id" in result


# ─────────────────────── §5.8 feature_shipped ───────────────────────


def test_event_5_8_creates_outcome_and_resulted_in_edge(facade):
    art_id = _prd_artifact(facade)
    dec_id = facade.get_artifact("ws-1", art_id).source_decision_id
    result = event_5_8_feature_shipped(
        facade,
        FeatureShippedPayload(
            workspace_id="ws-1",
            decision_id=dec_id,
            feature_name="Onboarding Nudge v1",
            metric_measured="activation_d1",
        ),
    )
    out = facade.get_outcome("ws-1", result["outcome_id"])
    assert out is not None
    assert out.actual_impact is None
    assert out.linked_decision_id == dec_id
    # RESULTED_IN edge: Artifact → Outcome.
    edges = facade.edges_from("ws-1", art_id, edge_type=EdgeType.RESULTED_IN)
    assert len(edges) == 1
    assert edges[0].target_entity_id == result["outcome_id"]
    # Decision back-link.
    dec = facade.get_decision("ws-1", dec_id)
    assert dec.outcome_id == result["outcome_id"]


def test_event_5_8_triggers_async_maintenance_sweep(facade):
    """The sweep must be scheduled as a Task, never awaited inline."""
    art_id = _prd_artifact(facade)
    dec_id = facade.get_artifact("ws-1", art_id).source_decision_id

    async def runner():
        called = {"ran": False}

        def mock_sweep(graph, workspace_id):
            called["ran"] = True
            from app.graph.maintenance import SweepReport
            return SweepReport(workspace_id=workspace_id, started_at=_now(), finished_at=_now())

        with mock.patch(
            "app.graph.write_events.run_maintenance_sweep", side_effect=mock_sweep
        ):
            event_5_8_feature_shipped(
                facade,
                FeatureShippedPayload(
                    workspace_id="ws-1",
                    decision_id=dec_id,
                    feature_name="X",
                    metric_measured="m",
                ),
            )
            # Sweep is scheduled — give it a moment to run.
            await asyncio.sleep(0.05)
        assert called["ran"] is True

    asyncio.run(runner())


# ─────────────────────── §5.9 outcome_measured ───────────────────────


def _setup_outcome(facade: GraphFacade) -> tuple[str, str, str]:
    """Helper: full pipeline through §5.8. Returns (outcome_id, hyp_id, signal_id)."""
    art_id = _prd_artifact(facade)
    art = facade.get_artifact("ws-1", art_id)
    dec_id = art.source_decision_id
    dec = facade.get_decision("ws-1", dec_id)
    hyp_id = dec.promoted_from_hypothesis_id
    # Make the Decision's evidence_snapshot have a real signal so the
    # Outcome.linked_signal_ids gets populated.
    sig_id = facade._backend.all_entity_ids("ws-1")["signals"][0]
    # Refresh decision evidence_snapshot via direct backend (test-only).
    refreshed = dec.model_copy(
        update={
            "evidence_snapshot": {
                "signals": [{"signal_id": sig_id, "content": "x", "confidence": 0.7,
                             "source_type": "manual", "source_tool": "manual"}]
            },
            "transaction_at": _now() + timedelta(microseconds=10),
        }
    )
    facade._backend.write_decision(refreshed)
    shipped = event_5_8_feature_shipped(
        facade,
        FeatureShippedPayload(
            workspace_id="ws-1",
            decision_id=dec_id,
            feature_name="Onboarding nudge",
            metric_measured="activation_d1",
        ),
    )
    return shipped["outcome_id"], hyp_id, sig_id


def test_event_5_9_fills_actual_impact_and_writes_edges(facade):
    out_id, hyp_id, sig_id = _setup_outcome(facade)
    # Hypothesis predicted_impact_low=1.0, high=4.0 (from _onboard).
    # actual_impact=2.5 → within range → prediction_hit=True.
    result = event_5_9_outcome_measured(
        facade,
        OutcomeMeasuredPayload(
            workspace_id="ws-1",
            outcome_id=out_id,
            actual_impact=2.5,
        ),
    )
    out = facade.get_outcome("ws-1", out_id)
    assert out.actual_impact == 2.5
    assert out.prediction_hit is True
    assert result["prediction_hit"] is True

    # VALIDATES edge: Outcome → Hypothesis.
    validates = facade.edges_from("ws-1", out_id, edge_type=EdgeType.VALIDATES)
    assert len(validates) == 1
    assert validates[0].target_entity_id == hyp_id

    # UPDATES_WEIGHT edge: Outcome → Signal.
    weights = facade.edges_from("ws-1", out_id, edge_type=EdgeType.UPDATES_WEIGHT)
    assert len(weights) == 1
    assert weights[0].target_entity_id == sig_id


def test_event_5_9_prediction_miss(facade):
    out_id, _hyp_id, _sig_id = _setup_outcome(facade)
    # actual_impact=10.0 way above predicted_impact_high=4.0 → miss.
    result = event_5_9_outcome_measured(
        facade,
        OutcomeMeasuredPayload(
            workspace_id="ws-1", outcome_id=out_id, actual_impact=10.0
        ),
    )
    assert result["prediction_hit"] is False
    assert result["prediction_delta"] > 0  # actual exceeded midpoint


# ─────────────────────── maintenance sweep ───────────────────────


def test_maintenance_sweep_expires_stale_signals(facade):
    _onboard(facade)
    # Manually plant a stale signal (stale_after in the past).
    past = _now() - timedelta(days=1)
    stale_sig = Signal(
        workspace_id="ws-1",
        valid_at=_now() - timedelta(days=10),
        transaction_at=_now() - timedelta(days=9),
        signal_id="sig-stale-1",
        content="ancient connector data",
        source_type=SignalSourceType.ANALYTICS,
        source_tool="amplitude",
        provenance_tag=ProvenanceTag.CONNECTOR_INGEST,
        confidence=0.7,
        stale_after=past,
    )
    facade.write_signal("ws-1", stale_sig)
    report = run_maintenance_sweep(facade, "ws-1")
    assert report.expired_signals >= 1
    assert report.errors == []


def test_maintenance_sweep_updates_signal_weights_from_outcome_edges(facade):
    out_id, _hyp_id, sig_id = _setup_outcome(facade)
    # Trigger §5.9 to mint an UPDATES_WEIGHT edge.
    event_5_9_outcome_measured(
        facade,
        OutcomeMeasuredPayload(workspace_id="ws-1", outcome_id=out_id, actual_impact=2.5),
    )
    sig_before = facade.get_signal("ws-1", sig_id)
    report = run_maintenance_sweep(facade, "ws-1")
    assert report.updated_signal_weights >= 1
    sig_after = facade.get_signal("ws-1", sig_id)
    # prediction_hit=True → linear nudge upward.
    assert sig_after.confidence == pytest.approx(
        min(1.0, sig_before.confidence + SIGNAL_RELIABILITY_DELTA_HIT)
    )


def test_maintenance_sweep_recomputes_hypothesis_evidence(facade):
    _onboard(facade)
    hyp_id = facade._backend.all_entity_ids("ws-1")["hypotheses"][0]
    sig_ids = facade._backend.all_entity_ids("ws-1")["signals"]
    # Manually wire 2 SUPPORTS edges (more than the bootstrap default of 1).
    for sid in sig_ids[:2]:
        facade.write_edge(
            "ws-1",
            Edge(
                workspace_id="ws-1",
                valid_at=_now() - timedelta(seconds=1),
                transaction_at=_now(),
                edge_type=EdgeType.SUPPORTS,
                source_entity_id=sid,
                source_entity_type="Signal",
                target_entity_id=hyp_id,
                target_entity_type="Hypothesis",
                source="test",
                confidence=0.7,
            ),
        )
    report = run_maintenance_sweep(facade, "ws-1")
    assert report.hypothesis_evidence_recomputed >= 1
    h = facade.get_hypothesis("ws-1", hyp_id)
    assert set(h.evidence_signal_ids) == set(sig_ids[:2])
    assert h.evidence_count == 2


# ─────────────────────── atomicity: partial-write rollback ─────────


def test_partial_write_rollback_on_intermediate_failure(facade, monkeypatch):
    """If event_5_5 fails mid-way (e.g. edge write blows up), the Decision
    and Hypothesis status change must roll back."""
    _onboard(facade)
    hyp_id = facade._backend.all_entity_ids("ws-1")["hypotheses"][0]
    hyp_before = facade.get_hypothesis("ws-1", hyp_id)

    real_write_edge = facade.write_edge

    def fail_on_promoted_to(workspace_id, edge):
        if edge.edge_type == EdgeType.PROMOTED_TO:
            raise RuntimeError("simulated edge write failure")
        return real_write_edge(workspace_id, edge)

    monkeypatch.setattr(facade, "write_edge", fail_on_promoted_to)
    with pytest.raises(GraphError, match="event_5_5"):
        event_5_5_brief_recommendation_approved(
            facade,
            BriefRecommendationApprovedPayload(
                workspace_id="ws-1",
                hypothesis_id=hyp_id,
                approved_by_user_id="user-1",
                reasoning="ship it now",
            ),
        )
    # Restore for read-back.
    monkeypatch.setattr(facade, "write_edge", real_write_edge)
    # Hypothesis status reverted; no Decision exists.
    h = facade.get_hypothesis("ws-1", hyp_id)
    assert h.status == hyp_before.status
    decision_ids = facade._backend.all_entity_ids("ws-1")["decisions"]
    assert decision_ids == []
