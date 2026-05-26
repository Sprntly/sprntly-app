"""Tests for the P1-11 KG query API.

Covers the 12 public functions per spec §10:
  1. seed_workspace                        2. load_session_context (≤500ms)
  3. ingest_signal                         4. create_hypothesis
  5. approve_hypothesis                    6. dismiss_hypothesis
  7. write_artifact_delta                  8. write_outcome_for_decision
  9. update_outcome_measurement           10. trace_provenance
 11. query_as_of (bitemporal)             12. run_maintenance_sweep

Plus the spec §7 convenience patterns: get_brief_context, get_prd_context.

Uses the SqliteBackend for isolation. FalkorDB integration tests live in a
follow-up; the FalkorBackend's bitemporal methods raise NotImplementedError
for now and that's verified separately.
"""
from __future__ import annotations

import time
from datetime import datetime, timedelta, timezone

import pytest

from app.graph import (
    ArtifactType,
    ConfidenceTier,
    DismissedReason,
    Edge,
    EdgeType,
    GraphError,
    HypothesisPromotionError,
    HypothesisStatus,
    KpiTreeNode,
    ProvenanceChain,
    ProvenanceTag,
    SessionContext,
    SignalSourceType,
    SweepReport,
    TenantViolationError,
    WorkspaceSnapshot,
    WorkspacePlan,
    WorkspaceStage,
)
from app.graph.backends.sqlite_backend import SqliteBackend
from app.graph.facade import GraphFacade


# ─────────────────────── fixtures ───────────────────────


@pytest.fixture
def facade(tmp_path) -> GraphFacade:
    backend = SqliteBackend(db_path=str(tmp_path / "graph.db"))
    backend.initialize_schema()
    return GraphFacade(backend)


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _seed_ws(facade: GraphFacade, workspace_id: str = "ws-1"):
    return facade.seed_workspace(
        workspace_id,
        {
            "company_name": "Acme",
            "industry": "SaaS",
            "stage": "growth",
            "business_model": "B2B SaaS",
            "kpi_tree": [{"name": "WAU", "role": "north_star"}],
            "strategy": {"okrs": ["Grow retention"]},
            "competitors": ["Notion"],
        },
    )


# ═══════════════════════════════════════════════════════
# (1) seed_workspace
# ═══════════════════════════════════════════════════════


def test_seed_workspace_creates_workspace(facade):
    ws = _seed_ws(facade)
    assert ws.workspace_id == "ws-1"
    assert ws.company_name == "Acme"
    assert ws.stage == WorkspaceStage.GROWTH
    # Round-trip
    got = facade.get_workspace("ws-1")
    assert got is not None
    assert got.company_name == "Acme"
    assert len(got.kpi_tree) == 1
    assert got.kpi_tree[0].name == "WAU"


def test_seed_workspace_tenant_isolation(facade):
    """Two workspaces with the same seed payload stay isolated."""
    _seed_ws(facade, "ws-A")
    _seed_ws(facade, "ws-B")
    assert facade.get_workspace("ws-A").workspace_id == "ws-A"
    assert facade.get_workspace("ws-B").workspace_id == "ws-B"


def test_seed_workspace_with_explicit_plan(facade):
    ws = facade.seed_workspace(
        "ws-paid",
        {
            "company_name": "PaidCo",
            "industry": "Fintech",
            "stage": "scale",
            "business_model": "B2B",
            "plan": "team",
        },
    )
    assert ws.plan == WorkspacePlan.TEAM


# ═══════════════════════════════════════════════════════
# (2) load_session_context (≤500ms)
# ═══════════════════════════════════════════════════════


def test_load_session_context_returns_typed_model(facade):
    _seed_ws(facade)
    ctx = facade.load_session_context("ws-1")
    assert isinstance(ctx, SessionContext)
    assert ctx.workspace is not None
    assert ctx.workspace.workspace_id == "ws-1"
    assert ctx.active_hypotheses == []
    assert ctx.recent_decisions == []
    assert ctx.recent_outcomes == []


def test_load_session_context_unknown_workspace(facade):
    """Unknown workspace returns a SessionContext with workspace=None."""
    ctx = facade.load_session_context("ws-unknown")
    assert ctx.workspace is None


def test_load_session_context_caps_at_spec_limits(facade):
    """Top 10 active hypotheses, last 5 decisions, last 3 measured outcomes."""
    _seed_ws(facade)
    # 20 hypotheses
    for i in range(20):
        facade.create_hypothesis(
            workspace_id="ws-1",
            claim=f"Hypothesis number {i} that we should test soon enough.",
            evidence_signal_ids=[f"sig-{i}-1"],
            predicted_metric="WAU",
            predicted_impact_low=1.0,
            predicted_impact_high=2.0,
            reversal_condition="If WAU drops, revert.",
            hypothesis_id=f"hyp-{i}",
        )
    ctx = facade.load_session_context("ws-1")
    assert len(ctx.active_hypotheses) == 10


def test_load_session_context_latency_under_500ms(facade):
    """Spec §10 hard budget. Warm DB with 50 hypotheses + 10 decisions
    + 5 outcomes, then measure wall-clock latency."""
    _seed_ws(facade)

    # 50 hypotheses
    hyp_ids = []
    for i in range(50):
        h = facade.create_hypothesis(
            workspace_id="ws-1",
            claim=f"Latency-test hypothesis {i} — long enough claim.",
            evidence_signal_ids=[f"sig-lat-{i}"],
            predicted_metric="WAU",
            predicted_impact_low=1.0,
            predicted_impact_high=2.0,
            reversal_condition="If we break something, revert.",
            hypothesis_id=f"hyp-lat-{i}",
        )
        hyp_ids.append(h.hypothesis_id)

    # 10 decisions promoted from the first 10 hypotheses
    for i in range(10):
        facade.approve_hypothesis(
            workspace_id="ws-1",
            hypothesis_id=hyp_ids[i],
            approved_by_user_id="user-test",
        )

    # 5 outcomes (4 measured, just to exercise the measured-only filter)
    decisions = facade._backend.list_recent_decisions("ws-1", limit=10)
    for i, dec in enumerate(decisions[:5]):
        out = facade.write_outcome_for_decision(
            workspace_id="ws-1",
            decision_id=dec.decision_id,
            feature_name=f"Feature {i}",
            shipped_at=_now() - timedelta(days=10),
        )
        if i < 4:
            facade.update_outcome_measurement(
                workspace_id="ws-1",
                outcome_id=out.outcome_id,
                actual_impact=1.5,
                measured_at=_now() - timedelta(days=1),
            )

    # Force a cold load by clearing the in-process cache.
    facade._session_ctx_cache.clear()

    # Measure: 5 trials, take the median.
    samples_ms = []
    for _ in range(5):
        facade._session_ctx_cache.clear()
        t0 = time.perf_counter()
        ctx = facade.load_session_context("ws-1")
        elapsed_ms = (time.perf_counter() - t0) * 1000.0
        samples_ms.append(elapsed_ms)

    samples_ms.sort()
    median = samples_ms[len(samples_ms) // 2]
    p95 = samples_ms[-1]  # max of 5 ~= p95

    assert ctx.workspace is not None
    assert len(ctx.active_hypotheses) == 10
    # The hard budget is ≤500ms; we'd typically see <50ms on SQLite.
    assert p95 < 500.0, (
        f"load_session_context p95={p95:.1f}ms exceeds 500ms budget "
        f"(samples={samples_ms})"
    )
    # Log for the PR description.
    print(f"\nload_session_context latency: median={median:.2f}ms p95={p95:.2f}ms")


def test_load_session_context_cache_hit_is_fast(facade):
    """Second call within the TTL window should be sub-millisecond."""
    _seed_ws(facade)
    facade.load_session_context("ws-1")  # warm
    t0 = time.perf_counter()
    for _ in range(50):
        facade.load_session_context("ws-1")
    elapsed_ms = (time.perf_counter() - t0) * 1000.0
    # 50 cache hits should comfortably fit in 10ms total.
    assert elapsed_ms < 10.0, f"cache hits too slow: {elapsed_ms:.2f}ms / 50 calls"


def test_session_context_cache_invalidated_on_write(facade):
    """Writing a hypothesis must drop the cached SessionContext so the next
    call sees the new entity."""
    _seed_ws(facade)
    ctx1 = facade.load_session_context("ws-1")
    assert len(ctx1.active_hypotheses) == 0
    facade.create_hypothesis(
        workspace_id="ws-1",
        claim="A new hypothesis after the first session-context load.",
        evidence_signal_ids=["sig-X"],
        predicted_metric="WAU",
        predicted_impact_low=1.0,
        predicted_impact_high=2.0,
        reversal_condition="If something bad happens, roll back.",
    )
    ctx2 = facade.load_session_context("ws-1")
    assert len(ctx2.active_hypotheses) == 1


# ═══════════════════════════════════════════════════════
# (3) ingest_signal
# ═══════════════════════════════════════════════════════


def test_ingest_signal_generates_id_and_stale_after(facade):
    _seed_ws(facade)
    sig = facade.ingest_signal(
        workspace_id="ws-1",
        raw_text="Activation dropped 12% WoW",
        source_type=SignalSourceType.ANALYTICS,
        source_tool="amplitude",
        valid_at=_now() - timedelta(days=1),
    )
    assert sig.signal_id.startswith("sig-")
    assert sig.workspace_id == "ws-1"
    # ANALYTICS default staleness = 30 days
    assert sig.stale_after is not None
    days_until_stale = (sig.stale_after - sig.valid_at).days
    assert days_until_stale == 30


def test_ingest_signal_outcome_measured_never_expires(facade):
    _seed_ws(facade)
    sig = facade.ingest_signal(
        workspace_id="ws-1",
        raw_text="Actual D30 retention measured at 42%",
        source_type=SignalSourceType.ANALYTICS,
        source_tool="amplitude",
        valid_at=_now() - timedelta(days=1),
        provenance_tag=ProvenanceTag.OUTCOME_MEASURED,
    )
    # Spec invariant: outcome-measured signals have stale_after=None.
    assert sig.stale_after is None


def test_ingest_signal_tenant_isolation(facade):
    _seed_ws(facade, "ws-1")
    _seed_ws(facade, "ws-2")
    s1 = facade.ingest_signal(
        workspace_id="ws-1",
        raw_text="ws-1 signal",
        source_type=SignalSourceType.ANALYTICS,
        source_tool="amplitude",
        valid_at=_now() - timedelta(days=1),
    )
    # Lookup from the wrong workspace returns nothing.
    assert facade.get_signal("ws-1", s1.signal_id) is not None
    assert facade.get_signal("ws-2", s1.signal_id) is None


# ═══════════════════════════════════════════════════════
# (4) create_hypothesis
# ═══════════════════════════════════════════════════════


def test_create_hypothesis_candidate_status_when_low_evidence(facade):
    """<3 signals OR <2 source types → status=candidate."""
    _seed_ws(facade)
    s1 = facade.ingest_signal(
        "ws-1",
        "Signal A",
        SignalSourceType.ANALYTICS,
        "amplitude",
        _now() - timedelta(days=1),
    )
    hyp = facade.create_hypothesis(
        workspace_id="ws-1",
        claim="If we ship X, retention improves materially.",
        evidence_signal_ids=[s1.signal_id],
        predicted_metric="D30 retention",
        predicted_impact_low=1.0,
        predicted_impact_high=3.0,
        reversal_condition="If retention drops >2pp post-launch, revert.",
    )
    assert hyp.status == HypothesisStatus.CANDIDATE


def test_create_hypothesis_proposed_when_diverse_evidence(facade):
    """≥3 signals from ≥2 distinct source_types → status=proposed."""
    _seed_ws(facade)
    s1 = facade.ingest_signal(
        "ws-1", "A", SignalSourceType.ANALYTICS, "amplitude", _now() - timedelta(days=1)
    )
    s2 = facade.ingest_signal(
        "ws-1", "B", SignalSourceType.CUSTOMER_VOICE, "zendesk", _now() - timedelta(days=1)
    )
    s3 = facade.ingest_signal(
        "ws-1", "C", SignalSourceType.PROJECT_MGMT, "linear", _now() - timedelta(days=1)
    )
    hyp = facade.create_hypothesis(
        workspace_id="ws-1",
        claim="Strong cross-source evidence supports shipping X.",
        evidence_signal_ids=[s1.signal_id, s2.signal_id, s3.signal_id],
        predicted_metric="D30 retention",
        predicted_impact_low=1.0,
        predicted_impact_high=3.0,
        reversal_condition="If retention drops >2pp post-launch, revert.",
    )
    assert hyp.status == HypothesisStatus.PROPOSED
    assert hyp.evidence_count == 3


def test_create_hypothesis_tenant_isolation(facade):
    _seed_ws(facade, "ws-1")
    _seed_ws(facade, "ws-2")
    h1 = facade.create_hypothesis(
        "ws-1",
        "Tenant 1 hypothesis that needs to be over ten chars.",
        ["sig-1"],
        "WAU",
        1.0,
        2.0,
        "If we break WAU, revert immediately.",
    )
    assert facade.get_hypothesis("ws-1", h1.hypothesis_id) is not None
    assert facade.get_hypothesis("ws-2", h1.hypothesis_id) is None


# ═══════════════════════════════════════════════════════
# (5) approve_hypothesis
# ═══════════════════════════════════════════════════════


def test_approve_hypothesis_promotes_to_decision(facade):
    _seed_ws(facade)
    s1 = facade.ingest_signal(
        "ws-1", "Sig A", SignalSourceType.ANALYTICS, "amplitude", _now() - timedelta(days=1)
    )
    hyp = facade.create_hypothesis(
        "ws-1",
        "Approve me — this hypothesis should be promoted.",
        [s1.signal_id],
        "WAU",
        1.0,
        2.0,
        "If WAU drops, revert immediately.",
    )
    dec = facade.approve_hypothesis(
        workspace_id="ws-1",
        hypothesis_id=hyp.hypothesis_id,
        approved_by_user_id="user-99",
    )
    assert dec.promoted_from_hypothesis_id == hyp.hypothesis_id
    assert dec.approved_by_user_id == "user-99"
    # Hypothesis flipped to CONFIRMED.
    after = facade.get_hypothesis("ws-1", hyp.hypothesis_id)
    assert after.status == HypothesisStatus.CONFIRMED
    assert after.promoted_to_decision_id == dec.decision_id
    # PROMOTED_TO edge written.
    edges = facade.edges_from("ws-1", hyp.hypothesis_id, EdgeType.PROMOTED_TO)
    assert len(edges) == 1
    assert edges[0].target_entity_id == dec.decision_id


def test_approve_hypothesis_freezes_evidence_snapshot(facade):
    """Decision.evidence_snapshot is immutable — frozen at approval time."""
    _seed_ws(facade)
    s1 = facade.ingest_signal(
        "ws-1", "Sig A frozen", SignalSourceType.ANALYTICS, "amplitude", _now() - timedelta(days=1)
    )
    hyp = facade.create_hypothesis(
        "ws-1",
        "Freeze the evidence at approval — no late edits.",
        [s1.signal_id],
        "WAU",
        1.0,
        2.0,
        "If we lose users, revert.",
    )
    dec = facade.approve_hypothesis("ws-1", hyp.hypothesis_id, "user-77")
    assert "signals" in dec.evidence_snapshot
    assert dec.evidence_snapshot["signals"][0]["signal_id"] == s1.signal_id
    # Modifying the original Signal must not affect the snapshot.
    s1_updated = s1.model_copy(update={"content": "MUTATED"})
    facade._backend.write_signal(s1_updated)
    dec_after = facade.get_decision("ws-1", dec.decision_id)
    assert dec_after.evidence_snapshot["signals"][0]["content"] == "Sig A frozen"


def test_approve_hypothesis_missing_hypothesis(facade):
    _seed_ws(facade)
    with pytest.raises(HypothesisPromotionError, match="not found"):
        facade.approve_hypothesis("ws-1", "hyp-missing", "user-1")


def test_approve_hypothesis_tenant_isolation(facade):
    _seed_ws(facade, "ws-1")
    _seed_ws(facade, "ws-2")
    facade.create_hypothesis(
        "ws-1",
        "Tenant isolation matters at approval time too.",
        ["sig-1"],
        "WAU",
        1.0,
        2.0,
        "If we break WAU, revert.",
        hypothesis_id="hyp-tenant",
    )
    # Approving from ws-2 must not find the ws-1 hypothesis.
    with pytest.raises(HypothesisPromotionError):
        facade.approve_hypothesis("ws-2", "hyp-tenant", "user-1")


# ═══════════════════════════════════════════════════════
# (6) dismiss_hypothesis
# ═══════════════════════════════════════════════════════


def test_dismiss_hypothesis_sets_status_and_reason(facade):
    _seed_ws(facade)
    hyp = facade.create_hypothesis(
        "ws-1",
        "This hypothesis is going to be dismissed by PM.",
        ["sig-1"],
        "WAU",
        1.0,
        2.0,
        "If WAU drops we revert.",
    )
    dismissed = facade.dismiss_hypothesis(
        "ws-1", hyp.hypothesis_id, DismissedReason.WRONG_PRIORITY
    )
    assert dismissed.status == HypothesisStatus.REJECTED
    assert dismissed.dismissed_reason == DismissedReason.WRONG_PRIORITY


def test_dismiss_hypothesis_accepts_string_reason(facade):
    _seed_ws(facade)
    hyp = facade.create_hypothesis(
        "ws-1",
        "Dismiss with a string reason argument.",
        ["sig-1"],
        "WAU",
        1.0,
        2.0,
        "If WAU drops we revert.",
    )
    dismissed = facade.dismiss_hypothesis("ws-1", hyp.hypothesis_id, "already_in_backlog")
    assert dismissed.dismissed_reason == DismissedReason.ALREADY_IN_BACKLOG


def test_dismiss_hypothesis_missing(facade):
    _seed_ws(facade)
    with pytest.raises(GraphError, match="not found"):
        facade.dismiss_hypothesis("ws-1", "hyp-nope", DismissedReason.NOT_RELEVANT)


def test_dismiss_hypothesis_tenant_isolation(facade):
    _seed_ws(facade, "ws-1")
    _seed_ws(facade, "ws-2")
    facade.create_hypothesis(
        "ws-1",
        "Tenant isolation on dismiss matters too.",
        ["sig-1"],
        "WAU",
        1.0,
        2.0,
        "If WAU drops we revert.",
        hypothesis_id="hyp-iso",
    )
    with pytest.raises(GraphError, match="not found"):
        facade.dismiss_hypothesis("ws-2", "hyp-iso", DismissedReason.NOT_RELEVANT)


# ═══════════════════════════════════════════════════════
# (7) write_artifact_delta
# ═══════════════════════════════════════════════════════


def test_write_artifact_delta_classification_data_driven(facade):
    _seed_ws(facade)
    delta = facade.write_artifact_delta(
        workspace_id="ws-1",
        artifact_id="art-prd-1",
        artifact_type=ArtifactType.PRD,
        section="success_metrics",
        original_text="Target: 5% retention lift",
        edited_text="Target: 12% retention lift",
        user_id="user-1",
    )
    assert delta.classification == "data-driven"
    assert delta.artifact_id == "art-prd-1"


def test_write_artifact_delta_classification_scope_cut(facade):
    _seed_ws(facade)
    delta = facade.write_artifact_delta(
        workspace_id="ws-1",
        artifact_id="art-prd-1",
        artifact_type="prd",
        section="scope",
        original_text="A long paragraph of detailed scope description with lots of details and edge cases handled.",
        edited_text="Short scope.",
        user_id="user-1",
    )
    assert delta.classification == "scope-cut"


def test_write_artifact_delta_classification_preference(facade):
    _seed_ws(facade)
    delta = facade.write_artifact_delta(
        workspace_id="ws-1",
        artifact_id="art-prd-1",
        artifact_type=ArtifactType.PRD,
        section="tone",
        original_text="The team should consider including a chart.",
        edited_text="We always include a chart in PRDs.",
        user_id="user-1",
    )
    assert delta.classification == "preference"


def test_list_artifact_deltas_filters_by_artifact(facade):
    _seed_ws(facade)
    facade.write_artifact_delta(
        "ws-1", "art-1", ArtifactType.PRD, "s", "orig text", "new text", "u1"
    )
    facade.write_artifact_delta(
        "ws-1", "art-2", ArtifactType.PRD, "s", "orig text 2", "new text 2", "u1"
    )
    deltas_all = facade.list_artifact_deltas("ws-1")
    deltas_art1 = facade.list_artifact_deltas("ws-1", "art-1")
    assert len(deltas_all) == 2
    assert len(deltas_art1) == 1
    assert deltas_art1[0]["artifact_id"] == "art-1"


def test_write_artifact_delta_tenant_isolation(facade):
    _seed_ws(facade, "ws-1")
    _seed_ws(facade, "ws-2")
    facade.write_artifact_delta(
        "ws-1", "art-shared", ArtifactType.PRD, "s", "a 12345", "b 67890", "u1"
    )
    assert len(facade.list_artifact_deltas("ws-1")) == 1
    assert len(facade.list_artifact_deltas("ws-2")) == 0


# ═══════════════════════════════════════════════════════
# (8) write_outcome_for_decision
# ═══════════════════════════════════════════════════════


def _setup_decision(facade, workspace_id: str = "ws-1") -> tuple[str, str, str]:
    """Helper: seed workspace + signal + hypothesis + decision. Returns
    (signal_id, hypothesis_id, decision_id)."""
    _seed_ws(facade, workspace_id)
    sig = facade.ingest_signal(
        workspace_id, "Evidence", SignalSourceType.ANALYTICS, "amplitude",
        _now() - timedelta(days=1)
    )
    hyp = facade.create_hypothesis(
        workspace_id,
        "Outcome-pipeline test hypothesis with enough length.",
        [sig.signal_id],
        "WAU",
        1.0,
        3.0,
        "If WAU drops 2pp, revert immediately.",
    )
    dec = facade.approve_hypothesis(workspace_id, hyp.hypothesis_id, "user-1")
    return sig.signal_id, hyp.hypothesis_id, dec.decision_id


def test_write_outcome_copies_predicted_impact_from_hypothesis(facade):
    """Spec invariant: predicted_impact_low/high copied from Decision at
    creation time, NOT measured at ship time."""
    sig_id, hyp_id, dec_id = _setup_decision(facade)
    out = facade.write_outcome_for_decision(
        "ws-1", dec_id, "Onboarding nudge v1", _now() - timedelta(days=10)
    )
    assert out.predicted_impact_low == 1.0
    assert out.predicted_impact_high == 3.0
    assert out.provenance_tag == ProvenanceTag.OUTCOME_MEASURED
    # Cross-link on Decision.
    dec_after = facade.get_decision("ws-1", dec_id)
    assert dec_after.outcome_id == out.outcome_id


def test_write_outcome_missing_decision(facade):
    _seed_ws(facade)
    with pytest.raises(GraphError, match="not found"):
        facade.write_outcome_for_decision(
            "ws-1", "dec-missing", "X", _now() - timedelta(days=1)
        )


def test_write_outcome_tenant_isolation(facade):
    _, _, dec_id = _setup_decision(facade, "ws-1")
    _seed_ws(facade, "ws-2")
    with pytest.raises(GraphError, match="not found"):
        facade.write_outcome_for_decision(
            "ws-2", dec_id, "X", _now() - timedelta(days=1)
        )


# ═══════════════════════════════════════════════════════
# (9) update_outcome_measurement
# ═══════════════════════════════════════════════════════


def test_update_outcome_measurement_within_range_is_hit(facade):
    _, _, dec_id = _setup_decision(facade)
    out = facade.write_outcome_for_decision(
        "ws-1", dec_id, "feature", _now() - timedelta(days=10)
    )
    updated = facade.update_outcome_measurement(
        "ws-1", out.outcome_id, actual_impact=2.0, measured_at=_now() - timedelta(days=1)
    )
    # Predicted range was [1.0, 3.0]; 2.0 is in range → hit.
    assert updated.prediction_hit is True
    # Delta from midpoint (2.0) = 0.
    assert updated.prediction_delta == 0.0
    assert updated.actual_impact == 2.0


def test_update_outcome_measurement_outside_range_is_miss(facade):
    _, _, dec_id = _setup_decision(facade)
    out = facade.write_outcome_for_decision(
        "ws-1", dec_id, "feature", _now() - timedelta(days=10)
    )
    updated = facade.update_outcome_measurement(
        "ws-1", out.outcome_id, actual_impact=5.0, measured_at=_now() - timedelta(days=1)
    )
    assert updated.prediction_hit is False
    # Midpoint = 2.0; actual = 5.0 → delta = 3.0
    assert updated.prediction_delta == 3.0


def test_update_outcome_measurement_missing(facade):
    _seed_ws(facade)
    with pytest.raises(GraphError, match="not found"):
        facade.update_outcome_measurement(
            "ws-1", "out-missing", 1.0, _now()
        )


def test_update_outcome_measurement_tenant_isolation(facade):
    _, _, dec_id = _setup_decision(facade, "ws-1")
    out = facade.write_outcome_for_decision(
        "ws-1", dec_id, "feature", _now() - timedelta(days=10)
    )
    _seed_ws(facade, "ws-2")
    with pytest.raises(GraphError, match="not found"):
        facade.update_outcome_measurement(
            "ws-2", out.outcome_id, 1.0, _now()
        )


# ═══════════════════════════════════════════════════════
# (10) trace_provenance
# ═══════════════════════════════════════════════════════


def test_trace_provenance_walks_signal_to_outcome(facade):
    """Build Signal → Hypothesis → Decision → Outcome and walk back.
    All IDs returned in the chain."""
    sig_id, hyp_id, dec_id = _setup_decision(facade)
    # Add explicit SUPPORTS edge to mirror what synthesis_agent_run writes.
    facade.write_edge(
        "ws-1",
        Edge(
            workspace_id="ws-1",
            valid_at=_now() - timedelta(seconds=2),
            transaction_at=_now() - timedelta(seconds=1),
            edge_type=EdgeType.SUPPORTS,
            source_entity_id=sig_id,
            source_entity_type="Signal",
            target_entity_id=hyp_id,
            target_entity_type="Hypothesis",
            source="synthesis_agent_run",
            confidence=0.9,
        ),
    )
    out = facade.write_outcome_for_decision(
        "ws-1", dec_id, "feature", _now() - timedelta(days=10)
    )

    chain = facade.trace_provenance("ws-1", dec_id)
    assert isinstance(chain, ProvenanceChain)
    assert chain.decision_id == dec_id
    assert chain.hypothesis_id == hyp_id
    assert sig_id in chain.supporting_signal_ids
    assert chain.outcome_id == out.outcome_id
    # walk_steps contains SUPPORTS, PROMOTED_TO, RESULTED_IN.
    edge_types = {step["edge_type"] for step in chain.walk_steps}
    assert "SUPPORTS" in edge_types
    assert "PROMOTED_TO" in edge_types
    assert "RESULTED_IN" in edge_types


def test_trace_provenance_falls_back_to_evidence_signal_ids(facade):
    """If no SUPPORTS edges exist (early Sprntly), the trace falls back to
    Hypothesis.evidence_signal_ids."""
    sig_id, hyp_id, dec_id = _setup_decision(facade)
    chain = facade.trace_provenance("ws-1", dec_id)
    assert sig_id in chain.supporting_signal_ids


def test_trace_provenance_missing_decision(facade):
    _seed_ws(facade)
    with pytest.raises(GraphError, match="not found"):
        facade.trace_provenance("ws-1", "dec-missing")


def test_trace_provenance_tenant_isolation(facade):
    _, _, dec_id = _setup_decision(facade, "ws-1")
    _seed_ws(facade, "ws-2")
    with pytest.raises(GraphError, match="not found"):
        facade.trace_provenance("ws-2", dec_id)


# ═══════════════════════════════════════════════════════
# (11) query_as_of (bitemporal)
# ═══════════════════════════════════════════════════════


def test_query_as_of_returns_workspace_snapshot(facade):
    _seed_ws(facade)
    snap = facade.query_as_of("ws-1", _now())
    assert isinstance(snap, WorkspaceSnapshot)
    assert snap.workspace_id == "ws-1"
    assert snap.workspace is not None


def test_query_as_of_filters_by_transaction_at(facade):
    """Entities written at T2 are not visible when querying at T1 < T2.

    Strategy: write entity-1 (transaction_at = T1), then entity-2 at T2.
    Query at midpoint T1.5 → only entity-1 returned.
    """
    _seed_ws(facade)

    T1 = _now() - timedelta(hours=2)
    T2 = _now() - timedelta(hours=1)
    T1_5 = _now() - timedelta(minutes=90)  # midpoint

    # Manually write signals with explicit timestamps via the underlying
    # entity model so we can control transaction_at exactly.
    from app.graph.entities import Signal

    facade._backend.write_signal(
        Signal(
            workspace_id="ws-1",
            valid_at=T1 - timedelta(seconds=1),
            transaction_at=T1,
            signal_id="sig-early",
            content="Early signal at T1",
            source_type=SignalSourceType.ANALYTICS,
            source_tool="amplitude",
            provenance_tag=ProvenanceTag.CONNECTOR_INGEST,
            confidence=0.7,
            stale_after=T1 + timedelta(days=30),
        )
    )
    facade._backend.write_signal(
        Signal(
            workspace_id="ws-1",
            valid_at=T2 - timedelta(seconds=1),
            transaction_at=T2,
            signal_id="sig-late",
            content="Late signal at T2",
            source_type=SignalSourceType.ANALYTICS,
            source_tool="amplitude",
            provenance_tag=ProvenanceTag.CONNECTOR_INGEST,
            confidence=0.7,
            stale_after=T2 + timedelta(days=30),
        )
    )

    snap_at_midpoint = facade.query_as_of("ws-1", T1_5)
    sig_ids = {s.signal_id for s in snap_at_midpoint.signals}
    assert "sig-early" in sig_ids
    assert "sig-late" not in sig_ids

    # Query at T2+1s → both visible.
    snap_at_now = facade.query_as_of("ws-1", T2 + timedelta(seconds=1))
    sig_ids_now = {s.signal_id for s in snap_at_now.signals}
    assert {"sig-early", "sig-late"}.issubset(sig_ids_now)


def test_query_as_of_filters_by_valid_at(facade):
    """A row where transaction_at <= T but valid_at > T is NOT returned.

    Models a backfill: we recorded today (transaction_at=T2) that something
    happened tomorrow (valid_at=T3). Querying at T2 should not surface it.
    Note: valid_at <= transaction_at is required by BitemporalMixin, so a
    pure forward-dated valid_at is not valid input.
    To exercise the valid_at filter, write a row where valid_at = T3 and
    transaction_at = T3 + microsecond, then query at T2 < T3.
    """
    _seed_ws(facade)

    T2 = _now() - timedelta(hours=2)
    T3 = _now() - timedelta(hours=1)

    from app.graph.entities import Signal

    facade._backend.write_signal(
        Signal(
            workspace_id="ws-1",
            valid_at=T3,
            transaction_at=T3 + timedelta(microseconds=1),
            signal_id="sig-future-valid",
            content="Backfill signal",
            source_type=SignalSourceType.ANALYTICS,
            source_tool="amplitude",
            provenance_tag=ProvenanceTag.CONNECTOR_INGEST,
            confidence=0.7,
            stale_after=T3 + timedelta(days=30),
        )
    )
    snap = facade.query_as_of("ws-1", T2)
    assert all(s.signal_id != "sig-future-valid" for s in snap.signals)


def test_query_as_of_tenant_isolation(facade):
    _seed_ws(facade, "ws-1")
    _seed_ws(facade, "ws-2")
    facade.ingest_signal(
        "ws-1", "ws-1 sig", SignalSourceType.ANALYTICS, "amplitude",
        _now() - timedelta(days=1)
    )
    snap = facade.query_as_of("ws-2", _now())
    assert snap.signals == []


# ═══════════════════════════════════════════════════════
# (12) run_maintenance_sweep
# ═══════════════════════════════════════════════════════


def test_run_maintenance_sweep_returns_report(facade):
    _seed_ws(facade)
    report = facade.run_maintenance_sweep("ws-1")
    assert isinstance(report, SweepReport)
    assert report.workspace_id == "ws-1"
    assert report.errors == []


def test_run_maintenance_sweep_counts_expired_signals(facade):
    _seed_ws(facade)
    # Write a signal that's already past its stale_after.
    from app.graph.entities import Signal

    past = _now() - timedelta(days=60)
    facade._backend.write_signal(
        Signal(
            workspace_id="ws-1",
            valid_at=past - timedelta(seconds=1),
            transaction_at=past,
            signal_id="sig-expired",
            content="Expired",
            source_type=SignalSourceType.ANALYTICS,
            source_tool="amplitude",
            provenance_tag=ProvenanceTag.CONNECTOR_INGEST,
            confidence=0.5,
            stale_after=past + timedelta(days=10),  # expired 50 days ago
        )
    )
    # Plus a fresh signal that should NOT count.
    facade.ingest_signal(
        "ws-1", "Fresh", SignalSourceType.ANALYTICS, "amplitude", _now() - timedelta(days=1)
    )
    report = facade.run_maintenance_sweep("ws-1")
    assert report.expired_signals == 1


def test_run_maintenance_sweep_tenant_isolation(facade):
    _seed_ws(facade, "ws-1")
    _seed_ws(facade, "ws-2")
    facade.ingest_signal(
        "ws-1", "ws-1 sig", SignalSourceType.ANALYTICS, "amplitude",
        _now() - timedelta(days=1)
    )
    report = facade.run_maintenance_sweep("ws-2")
    # ws-2 has no signals.
    assert report.expired_signals == 0
    assert report.hypotheses_evidence_recomputed == 0


# ═══════════════════════════════════════════════════════
# Spec §7 convenience patterns (get_brief_context, get_prd_context)
# ═══════════════════════════════════════════════════════


def test_get_brief_context_returns_uncited_signals(facade):
    """Signal not cited by any active hypothesis → in uncited_signals."""
    _seed_ws(facade)
    s_cited = facade.ingest_signal(
        "ws-1", "Cited", SignalSourceType.ANALYTICS, "amplitude", _now() - timedelta(days=1)
    )
    s_uncited = facade.ingest_signal(
        "ws-1", "Uncited", SignalSourceType.ANALYTICS, "amplitude", _now() - timedelta(days=1)
    )
    facade.create_hypothesis(
        "ws-1",
        "Hypothesis citing one signal only here.",
        [s_cited.signal_id],
        "WAU",
        1.0,
        2.0,
        "If WAU drops we revert.",
    )
    brief = facade.get_brief_context("ws-1")
    uncited_ids = {s.signal_id for s in brief.uncited_signals}
    assert s_uncited.signal_id in uncited_ids
    assert s_cited.signal_id not in uncited_ids


def test_get_prd_context_returns_decision_and_workspace(facade):
    _, _, dec_id = _setup_decision(facade)
    ctx = facade.get_prd_context("ws-1", dec_id)
    assert ctx.decision.decision_id == dec_id
    assert ctx.workspace.workspace_id == "ws-1"
    assert ctx.source_hypothesis is not None


def test_get_prd_context_missing_decision(facade):
    _seed_ws(facade)
    with pytest.raises(GraphError, match="not found"):
        facade.get_prd_context("ws-1", "dec-missing")
