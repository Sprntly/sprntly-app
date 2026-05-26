"""Tests for app.synthesis.brief_assembly — the 11-step scheduled-mode pipeline.

Spec source: Synthesis_Agent_Spec.docx §3.2 Brief Assembly Algorithm.

Each step is exercised in isolation where the contract is testable
without the full pipeline (Steps 1, 6, 7, 10). The end-to-end test
covers the orchestrator with mocked LLM + a real SqliteBackend
GraphFacade so the tenant isolation invariant is also asserted.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any

import pytest

from app.graph import (
    ConfidenceTier,
    GraphFacade,
    Hypothesis,
    HypothesisStatus,
    KpiTreeNode,
    ProvenanceTag,
    Signal,
    SignalSourceType,
    Workspace,
    WorkspaceStage,
    WorkspaceStrategy,
)
from app.graph.backends.sqlite_backend import SqliteBackend
from app.synthesis.brief_assembly import (
    Brief,
    CompetitivePulse,
    PROMOTION_MIN_DISTINCT_SOURCE_TYPES,
    PROMOTION_MIN_EVIDENCE,
    _Candidate,
    _evidence_score,
    _impact_score,
    _promotion_status,
    _step1_load_session_context,
    _step3_cross_reference,
    _step5_weight_customer_feedback,
    _step6_score_candidates,
    _step7_filter_dead_ends,
    _step9_kpi_status,
    _strategy_score,
    assemble_brief,
)


# ─────────────────────── fixtures + helpers ───────────────────────


def _now() -> datetime:
    return datetime.now(timezone.utc)


@pytest.fixture
def facade(tmp_path) -> GraphFacade:
    backend = SqliteBackend(db_path=str(tmp_path / "graph.db"))
    backend.initialize_schema()
    return GraphFacade(backend)


def _workspace(
    workspace_id: str = "ws-1",
    okrs: list[str] | None = None,
    dead_ends: list[str] | None = None,
    competitors: list[str] | None = None,
    kpi_tree: list[KpiTreeNode] | None = None,
) -> Workspace:
    now = _now()
    return Workspace(
        workspace_id=workspace_id,
        valid_at=now - timedelta(seconds=1),
        transaction_at=now,
        company_name="Acme",
        industry="SaaS",
        stage=WorkspaceStage.GROWTH,
        business_model="B2B SaaS",
        strategy=WorkspaceStrategy(
            okrs=okrs or ["Increase activation"],
            current_priorities=["onboarding"],
            dead_ends=dead_ends or [],
        ),
        kpi_tree=kpi_tree or [
            KpiTreeNode(name="Activation", role="north_star", target_value=0.5, current_value=0.3),
        ],
        competitors=competitors or [],
        created_at=now - timedelta(days=1),
        updated_at=now,
    )


def _signal(
    sid: str,
    *,
    workspace_id: str = "ws-1",
    source_type: SignalSourceType = SignalSourceType.ANALYTICS,
    source_tool: str = "amplitude",
    provenance: ProvenanceTag = ProvenanceTag.CONNECTOR_INGEST,
    confidence: float = 0.8,
    content: str = "metric trended up 12% week-over-week",
) -> Signal:
    now = _now()
    return Signal(
        workspace_id=workspace_id,
        valid_at=now - timedelta(seconds=1),
        transaction_at=now,
        signal_id=sid,
        content=content,
        source_type=source_type,
        source_tool=source_tool,
        provenance_tag=provenance,
        confidence=confidence,
        stale_after=now + timedelta(days=30),
    )


def _hypothesis(
    *,
    workspace_id: str = "ws-1",
    hypothesis_id: str = "hyp-existing",
    predicted_metric: str = "Activation",
    status: HypothesisStatus = HypothesisStatus.PROPOSED,
) -> Hypothesis:
    now = _now()
    return Hypothesis(
        workspace_id=workspace_id,
        valid_at=now - timedelta(seconds=1),
        transaction_at=now,
        hypothesis_id=hypothesis_id,
        claim="If we ship X, activation improves.",
        predicted_metric=predicted_metric,
        predicted_impact_low=2.0,
        predicted_impact_high=5.0,
        predicted_impact_basis="DS agent finding",
        status=status,
        evidence_signal_ids=["sig-existing"],
        evidence_count=1,
        confidence_composite=0.6,
        confidence_tier=ConfidenceTier.MEDIUM,
        reversal_condition="If activation drops >2pp post-launch, revert.",
        created_at=now - timedelta(hours=1),
        status_updated_at=now,
    )


def _candidate(
    candidate_id: str = "c1",
    *,
    title: str = "Improve onboarding step 3",
    claim: str = "Reduce step-3 drop-off by simplifying form fields.",
    predicted_metric: str = "Activation",
    predicted_impact_low: float = 2.0,
    predicted_impact_high: float = 6.0,
    signal_summary: str = "Activation drops 30% at step 3 across mobile + web.",
    hypothesis_text: str = "Cutting the form to 2 fields will lift step-3 completion.",
    supporting: list[str] | None = None,
    confidence: str = "medium",
) -> _Candidate:
    return _Candidate(
        candidate_id=candidate_id,
        title=title,
        claim=claim,
        predicted_metric=predicted_metric,
        predicted_impact_low=predicted_impact_low,
        predicted_impact_high=predicted_impact_high,
        predicted_impact_basis="Comprehensive DS agent finding (effect size 0.4).",
        signal_summary=signal_summary,
        hypothesis_text=hypothesis_text,
        supporting_signal_ids=supporting if supporting is not None else ["sig-1"],
        confidence=confidence,
        reversal_condition="If activation falls >1pp after rollout, revert.",
    )


def _ds_finding(
    *,
    title: str = "Step-3 drop-off",
    predicted_metric: str = "Activation",
    impact_low: float = 2.0,
    impact_high: float = 6.0,
    supporting: list[str] | None = None,
) -> dict[str, Any]:
    return {
        "title": title,
        "claim": "Reduce step-3 drop-off by simplifying form fields.",
        "predicted_metric": predicted_metric,
        "predicted_impact_low": impact_low,
        "predicted_impact_high": impact_high,
        "predicted_impact_basis": "DS agent finding",
        "signal_summary": "Activation drops 30% at step 3.",
        "hypothesis": "Cut the form to 2 fields.",
        "supporting_signal_ids": supporting if supporting is not None else ["sig-1"],
        "reversal_condition": "If activation falls >1pp, revert.",
        "confidence": "medium",
    }


def _llm_factory(recommendations: list[dict[str, Any]]):
    """Return a fake_llm callable that emits the given recommendations."""
    calls: list[dict[str, Any]] = []

    def _fake(*, system: str, user: str, schema: dict | None = None, **kwargs):
        calls.append({"system": system, "user": user, "schema": schema, "kwargs": kwargs})
        return {"recommendations": recommendations}

    _fake.calls = calls  # type: ignore[attr-defined]
    return _fake


# ─────────────────────── Step 1: session context ───────────────────────


def test_step1_loads_workspace_and_signals(facade):
    """Step 1 reads Workspace + active Signals via the facade."""
    ws = _workspace()
    facade.write_workspace("ws-1", ws)
    facade.write_signal("ws-1", _signal("sig-1"))
    facade.write_signal("ws-1", _signal("sig-2", source_tool="mixpanel"))

    ctx = _step1_load_session_context("ws-1", facade)

    assert ctx["workspace"] is not None
    assert ctx["workspace"].workspace_id == "ws-1"
    assert {s.signal_id for s in ctx["active_signals"]} == {"sig-1", "sig-2"}
    assert ctx["active_hypotheses"] == []


def test_step1_empty_workspace_returns_blank_context(facade):
    """Unknown workspace yields a structurally-valid empty context."""
    ctx = _step1_load_session_context("ws-missing", facade)
    assert ctx["workspace"] is None
    assert ctx["active_signals"] == []


# ─────────────────────── Step 3: cross-reference ───────────────────────


def test_step3_cross_reference_marks_known_hypothesis():
    """Step 3 annotates findings whose metric matches an open Hypothesis."""
    findings = [_ds_finding(predicted_metric="Activation")]
    known = [_hypothesis(predicted_metric="Activation", hypothesis_id="hyp-1")]

    out = _step3_cross_reference(findings, known)

    assert out[0]["is_known_hypothesis"] is True
    assert out[0]["reinforcement_of"] == "hyp-1"


def test_step3_cross_reference_marks_novel_finding():
    findings = [_ds_finding(predicted_metric="Revenue")]
    known = [_hypothesis(predicted_metric="Activation", hypothesis_id="hyp-1")]

    out = _step3_cross_reference(findings, known)

    assert out[0]["is_known_hypothesis"] is False
    assert out[0]["reinforcement_of"] is None


# ─────────────────────── Step 5: customer feedback weighting ───────────────────────


def test_step5_weights_only_customer_voice_signals():
    """Step 5 ignores non-customer_voice signals."""
    signals = [
        _signal("s1", source_type=SignalSourceType.ANALYTICS, source_tool="amplitude", confidence=0.9),
        _signal("s2", source_type=SignalSourceType.CUSTOMER_VOICE, source_tool="zendesk", confidence=0.7),
        _signal("s3", source_type=SignalSourceType.CUSTOMER_VOICE, source_tool="zendesk", confidence=0.5),
        _signal("s4", source_type=SignalSourceType.CUSTOMER_VOICE, source_tool="intercom", confidence=0.8),
    ]
    weights = _step5_weight_customer_feedback(signals)
    assert pytest.approx(weights["zendesk"], abs=0.001) == 1.2
    assert pytest.approx(weights["intercom"], abs=0.001) == 0.8
    assert "amplitude" not in weights


# ─────────────────────── Step 6: scoring ───────────────────────


def test_step6_score_formula_orders_candidates():
    """Higher impact + strategy alignment + evidence → higher composite score."""
    ws = _workspace(okrs=["Improve activation"])
    sigs = [
        _signal("sig-1", confidence=0.9),
        _signal("sig-2", source_tool="mixpanel", confidence=0.7),
        _signal("sig-3", source_tool="amplitude", confidence=0.85),
    ]
    # candidate A: aligned with OKR, large impact, 3 strong signals
    a = _candidate("a", predicted_metric="Activation",
                   predicted_impact_low=10.0, predicted_impact_high=20.0,
                   supporting=["sig-1", "sig-2", "sig-3"])
    # candidate B: not aligned, modest impact, weak evidence
    b = _candidate("b", predicted_metric="Random",
                   predicted_impact_low=0.5, predicted_impact_high=1.0,
                   supporting=["sig-1"])
    _step6_score_candidates([a, b], ws, sigs)

    assert a.composite_score > b.composite_score
    assert a.strategy_score == 1.0  # OKR overlap
    assert b.strategy_score == 0.3  # no overlap


def test_step6_evidence_score_zero_when_no_supporting_signals():
    assert _evidence_score([], {}) == 0.0


def test_step6_evidence_score_caps_at_one():
    """min(1.0, count/5) clamps evidence_count > 5."""
    sigs = {f"s{i}": _signal(f"s{i}", confidence=1.0) for i in range(10)}
    score = _evidence_score(list(sigs.keys()), sigs)
    # count_part clamps at 1, avg_conf=1.0 → score=1.0
    assert score == 1.0


def test_step6_impact_score_monotone():
    assert _impact_score(0, 0) == 0.0
    assert _impact_score(1, 1) < _impact_score(10, 10) < _impact_score(100, 100)


def test_step6_strategy_score_picks_up_kpi_tree_names():
    ws = _workspace(
        okrs=[],
        kpi_tree=[
            KpiTreeNode(name="Day-30 Retention", role="north_star", target_value=0.4, current_value=0.3),
        ],
    )
    assert _strategy_score("day-30 retention", ws) == 1.0
    assert _strategy_score("unrelated", ws) == 0.3


# ─────────────────────── Step 7: dead-end filter ───────────────────────


def test_step7_drops_candidates_matching_dead_end():
    """Step 7: workspace.dead_ends filter drops + records caveat."""
    ws = _workspace(dead_ends=["manual onboarding"])
    candidates = [
        _candidate("c1", title="Build manual onboarding wizard",
                   claim="Add a manual onboarding step that hand-holds users.",
                   hypothesis_text="A manual onboarding flow will help.",
                   ),
        _candidate("c2", title="Self-serve activation tour"),
    ]
    kept, caveats = _step7_filter_dead_ends(candidates, ws)
    assert [c.candidate_id for c in kept] == ["c2"]
    assert len(caveats) == 1
    assert "manual onboarding" in caveats[0].lower()


def test_step7_no_dead_ends_keeps_everything():
    ws = _workspace(dead_ends=[])
    candidates = [_candidate("c1"), _candidate("c2")]
    kept, caveats = _step7_filter_dead_ends(candidates, ws)
    assert len(kept) == 2
    assert caveats == []


def test_step7_token_match_is_case_insensitive():
    ws = _workspace(dead_ends=["AI Chatbot"])
    c = _candidate("c1", title="Add an ai chatbot to onboarding",
                   claim="Embed an AI chatbot to help new users.",
                   hypothesis_text="An ai chatbot will lift activation.")
    kept, caveats = _step7_filter_dead_ends([c], ws)
    assert kept == []
    assert len(caveats) == 1


# ─────────────────────── Step 9: KPI status ───────────────────────


def test_step9_kpi_status_computes_pct_to_target():
    ws = _workspace(kpi_tree=[
        KpiTreeNode(name="Activation", role="north_star", target_value=0.5, current_value=0.25),
        KpiTreeNode(name="Step-3 completion", role="primary", target_value=None, current_value=None),
    ])
    out = _step9_kpi_status(ws)
    assert len(out) == 2
    assert out[0].name == "Activation"
    assert out[0].pct_to_target == pytest.approx(0.5)
    assert out[1].pct_to_target is None


# ─────────────────────── Step 10: promotion logic ───────────────────────


def test_step10_promotion_proposed_with_3_signals_2_source_types():
    """status=proposed when evidence_count >= 3 from >= 2 distinct source_types."""
    sigs = {
        "s1": _signal("s1", source_type=SignalSourceType.ANALYTICS),
        "s2": _signal("s2", source_type=SignalSourceType.CUSTOMER_VOICE, source_tool="zendesk"),
        "s3": _signal("s3", source_type=SignalSourceType.ANALYTICS),
    }
    status = _promotion_status(["s1", "s2", "s3"], sigs)
    assert status == HypothesisStatus.PROPOSED


def test_step10_promotion_candidate_when_one_source_type():
    """3 signals but all from the same source_type → still candidate."""
    sigs = {
        "s1": _signal("s1", source_type=SignalSourceType.ANALYTICS),
        "s2": _signal("s2", source_type=SignalSourceType.ANALYTICS),
        "s3": _signal("s3", source_type=SignalSourceType.ANALYTICS),
    }
    status = _promotion_status(["s1", "s2", "s3"], sigs)
    assert status == HypothesisStatus.CANDIDATE


def test_step10_promotion_candidate_when_fewer_than_three_signals():
    sigs = {
        "s1": _signal("s1", source_type=SignalSourceType.ANALYTICS),
        "s2": _signal("s2", source_type=SignalSourceType.CUSTOMER_VOICE, source_tool="zendesk"),
    }
    status = _promotion_status(["s1", "s2"], sigs)
    assert status == HypothesisStatus.CANDIDATE


def test_step10_promotion_thresholds_are_named_constants():
    """Guardrail: the named constants are the ones the test relies on
    (lets a reviewer change the spec rule in one place)."""
    assert PROMOTION_MIN_EVIDENCE == 3
    assert PROMOTION_MIN_DISTINCT_SOURCE_TYPES == 2


def test_step10_writes_hypothesis_with_expected_status(facade):
    """End-to-end check that a candidate with 3 signals from 2 distinct
    source_types ends up as a `proposed` Hypothesis in the KG."""
    ws = _workspace()
    facade.write_workspace("ws-1", ws)
    sigs = [
        _signal("s1", source_type=SignalSourceType.ANALYTICS, source_tool="amplitude"),
        _signal("s2", source_type=SignalSourceType.CUSTOMER_VOICE, source_tool="zendesk"),
        _signal("s3", source_type=SignalSourceType.ANALYTICS, source_tool="amplitude"),
    ]
    for s in sigs:
        facade.write_signal("ws-1", s)

    rec = {
        "title": "Reduce step-3 drop-off",
        "claim": "Cut form fields at step 3.",
        "signal_summary": "30% drop at step 3 confirmed cross-source.",
        "hypothesis": "Trimming fields lifts completion.",
        "predicted_metric": "Activation",
        "predicted_impact_low": 5.0,
        "predicted_impact_high": 10.0,
        "predicted_impact_basis": "DS agent comprehensive finding.",
        "supporting_signal_ids": ["s1", "s2", "s3"],
        "reversal_condition": "Roll back if activation falls >2pp.",
        "confidence": "high",
    }
    llm = _llm_factory([rec])
    brief = assemble_brief("ws-1", None, facade, llm)

    assert len(brief.recommendations) == 1
    # The promoted Hypothesis is now in the KG with status=proposed.
    all_ids = facade._backend.all_entity_ids("ws-1")["hypotheses"]
    assert len(all_ids) == 1
    h = facade.get_hypothesis("ws-1", all_ids[0])
    assert h is not None
    assert h.status == HypothesisStatus.PROPOSED


def test_step10_writes_hypothesis_candidate_when_single_source_type(facade):
    """Same shape as above but all signals analytics → status=candidate."""
    ws = _workspace()
    facade.write_workspace("ws-1", ws)
    sigs = [
        _signal("s1", source_type=SignalSourceType.ANALYTICS, source_tool="amplitude"),
        _signal("s2", source_type=SignalSourceType.ANALYTICS, source_tool="amplitude"),
        _signal("s3", source_type=SignalSourceType.ANALYTICS, source_tool="mixpanel"),
    ]
    for s in sigs:
        facade.write_signal("ws-1", s)

    rec = {
        "title": "Reduce step-3 drop-off",
        "claim": "Cut form fields at step 3.",
        "signal_summary": "30% drop at step 3 from analytics.",
        "hypothesis": "Trimming fields lifts completion.",
        "predicted_metric": "Activation",
        "predicted_impact_low": 5.0,
        "predicted_impact_high": 10.0,
        "predicted_impact_basis": "DS finding.",
        "supporting_signal_ids": ["s1", "s2", "s3"],
        "reversal_condition": "Roll back if activation falls >2pp.",
        "confidence": "medium",
    }
    llm = _llm_factory([rec])
    assemble_brief("ws-1", None, facade, llm)

    all_ids = facade._backend.all_entity_ids("ws-1")["hypotheses"]
    h = facade.get_hypothesis("ws-1", all_ids[0])
    assert h is not None
    assert h.status == HypothesisStatus.CANDIDATE


# ─────────────────────── End-to-end orchestrator ───────────────────────


def test_assemble_brief_end_to_end_produces_brief(facade):
    """Full assemble_brief with mocked LLM + real facade → valid Brief
    with 3–5 HypothesisOutputs."""
    ws = _workspace()
    facade.write_workspace("ws-1", ws)
    for sid in ("s1", "s2", "s3", "s4"):
        facade.write_signal("ws-1", _signal(sid, source_tool="amplitude" if sid in ("s1", "s2") else "zendesk",
                                            source_type=SignalSourceType.ANALYTICS if sid in ("s1", "s2")
                                            else SignalSourceType.CUSTOMER_VOICE))

    llm = _llm_factory([
        {
            "title": f"Recommendation {i}",
            "claim": f"Build feature {i} to lift activation.",
            "signal_summary": f"Pattern {i} confirmed by mixed sources.",
            "hypothesis": f"Ship feature {i}.",
            "predicted_metric": "Activation",
            "predicted_impact_low": 2.0 + i,
            "predicted_impact_high": 6.0 + i,
            "predicted_impact_basis": "DS agent finding.",
            "supporting_signal_ids": ["s1", "s2", "s3"],
            "reversal_condition": f"Roll back feature {i} if metric falls.",
            "confidence": "high",
        }
        for i in range(1, 5)
    ])

    brief = assemble_brief("ws-1", None, facade, llm)

    assert isinstance(brief, Brief)
    assert brief.workspace_id == "ws-1"
    assert 3 <= len(brief.recommendations) <= 5
    assert brief.signal_health.total_active == 4
    assert all(r.supporting_signals for r in brief.recommendations)
    assert all(r.reversal_condition for r in brief.recommendations)
    # ranking property: ranks are 1..N
    assert [r.rank for r in brief.recommendations] == list(
        range(1, len(brief.recommendations) + 1)
    )


def test_assemble_brief_writes_one_hypothesis_per_recommendation(facade):
    ws = _workspace()
    facade.write_workspace("ws-1", ws)
    facade.write_signal("ws-1", _signal("s1"))

    llm = _llm_factory([
        {
            "title": "Rec 1",
            "claim": "Build a thing.",
            "signal_summary": "Pattern X.",
            "hypothesis": "Ship the thing.",
            "predicted_metric": "Activation",
            "predicted_impact_low": 1.0,
            "predicted_impact_high": 3.0,
            "predicted_impact_basis": "Comprehensive finding.",
            "supporting_signal_ids": ["s1"],
            "reversal_condition": "Revert if metric drops.",
            "confidence": "medium",
        },
        {
            "title": "Rec 2",
            "claim": "Build another thing.",
            "signal_summary": "Pattern Y.",
            "hypothesis": "Ship the other thing.",
            "predicted_metric": "Activation",
            "predicted_impact_low": 2.0,
            "predicted_impact_high": 4.0,
            "predicted_impact_basis": "DS finding.",
            "supporting_signal_ids": ["s1"],
            "reversal_condition": "Revert if metric drops.",
            "confidence": "low",
        },
    ])
    brief = assemble_brief("ws-1", None, facade, llm)
    assert len(brief.recommendations) == 2
    hyp_ids = facade._backend.all_entity_ids("ws-1")["hypotheses"]
    assert len(hyp_ids) == 2


def test_assemble_brief_caps_at_five_recommendations(facade):
    """Spec §3.2 Step 8 — Brief surface caps at 5."""
    ws = _workspace()
    facade.write_workspace("ws-1", ws)
    facade.write_signal("ws-1", _signal("s1"))

    llm = _llm_factory([
        {
            "title": f"Rec {i}",
            "claim": f"Build feature {i} to lift activation by reducing friction.",
            "signal_summary": f"Pattern {i} observed across mixed sources.",
            "hypothesis": f"Shipping feature {i} should increase activation.",
            "predicted_metric": "Activation",
            "predicted_impact_low": float(i),
            "predicted_impact_high": float(i + 2),
            "predicted_impact_basis": "DS agent finding from comprehensive run.",
            "supporting_signal_ids": ["s1"],
            "reversal_condition": "Revert if metric drops post-launch.",
            "confidence": "medium",
        }
        for i in range(1, 11)
    ])
    brief = assemble_brief("ws-1", None, facade, llm)
    assert len(brief.recommendations) == 5


def test_assemble_brief_empty_signals_returns_caveat(facade):
    """Edge case: empty signal pool + no DS findings → empty recs +
    caveat indicating no actionable recommendations this cycle."""
    ws = _workspace()
    facade.write_workspace("ws-1", ws)

    # LLM shouldn't be called; supply one that explodes if invoked.
    def _angry_llm(**_kwargs):
        raise AssertionError("LLM should not be called when there's nothing to feed it")

    brief = assemble_brief("ws-1", None, facade, _angry_llm)
    assert brief.recommendations == []
    assert any("no actionable recommendations" in c.lower() for c in brief.caveats)
    assert brief.signal_health.total_active == 0


def test_assemble_brief_missing_workspace_returns_empty_brief(facade):
    """If the Workspace doesn't exist, return a structurally-valid empty
    Brief with a caveat (caller decides whether to deliver)."""
    def _angry_llm(**_kwargs):
        raise AssertionError("LLM should not be called for an unknown workspace")

    brief = assemble_brief("ws-ghost", None, facade, _angry_llm)
    assert brief.workspace_id == "ws-ghost"
    assert brief.recommendations == []
    assert any("not found" in c.lower() for c in brief.caveats)


def test_assemble_brief_dead_end_filter_short_circuits_recs(facade):
    """Dead-end filter removes all candidates → empty recs + caveats."""
    ws = _workspace(dead_ends=["manual onboarding"])
    facade.write_workspace("ws-1", ws)
    facade.write_signal("ws-1", _signal("s1"))

    llm = _llm_factory([
        {
            "title": "Add manual onboarding wizard",
            "claim": "Build a manual onboarding wizard.",
            "signal_summary": "Users get lost.",
            "hypothesis": "A manual onboarding flow will help.",
            "predicted_metric": "Activation",
            "predicted_impact_low": 1.0,
            "predicted_impact_high": 3.0,
            "predicted_impact_basis": "Hunch.",
            "supporting_signal_ids": ["s1"],
            "reversal_condition": "Revert if drop.",
            "confidence": "low",
        }
    ])
    brief = assemble_brief("ws-1", None, facade, llm)
    assert brief.recommendations == []
    assert any("manual onboarding" in c.lower() for c in brief.caveats)


# ─────────────────────── Tenant isolation ───────────────────────


def test_assemble_brief_tenant_isolation(facade):
    """Bug-class guard: assemble_brief on workspace X must never read or
    write workspace Y. We seed both tenants and assert the cross-tenant
    signal never appears in the Brief or in the LLM prompt payload."""
    # Tenant X
    ws_x = _workspace(workspace_id="ws-x")
    facade.write_workspace("ws-x", ws_x)
    facade.write_signal("ws-x", _signal("sig-x1", workspace_id="ws-x"))
    facade.write_signal("ws-x", _signal("sig-x2", workspace_id="ws-x"))

    # Tenant Y — must NOT leak into X's brief.
    ws_y = _workspace(workspace_id="ws-y")
    facade.write_workspace("ws-y", ws_y)
    facade.write_signal(
        "ws-y",
        _signal("sig-y-secret", workspace_id="ws-y",
                content="SECRET-Y-only competitor strategy memo"),
    )

    llm = _llm_factory([
        {
            "title": "X-specific recommendation",
            "claim": "Ship feature for ws-x users.",
            "signal_summary": "Patterns seen in ws-x.",
            "hypothesis": "It will help ws-x.",
            "predicted_metric": "Activation",
            "predicted_impact_low": 1.0,
            "predicted_impact_high": 3.0,
            "predicted_impact_basis": "DS finding.",
            "supporting_signal_ids": ["sig-x1"],
            "reversal_condition": "Revert if drop.",
            "confidence": "medium",
        }
    ])
    brief = assemble_brief("ws-x", None, facade, llm)

    # Brief is for ws-x only.
    assert brief.workspace_id == "ws-x"
    # The LLM prompt must NEVER contain anything from ws-y.
    assert llm.calls, "LLM was not called"
    prompt_user = llm.calls[0]["user"]
    assert "sig-y-secret" not in prompt_user
    assert "SECRET-Y" not in prompt_user
    # Hypothesis written must be in ws-x.
    x_hyp_ids = facade._backend.all_entity_ids("ws-x")["hypotheses"]
    y_hyp_ids = facade._backend.all_entity_ids("ws-y")["hypotheses"]
    assert len(x_hyp_ids) == 1
    assert len(y_hyp_ids) == 0
    h = facade.get_hypothesis("ws-x", x_hyp_ids[0])
    assert h is not None
    assert h.workspace_id == "ws-x"


# ─────────────────────── Competitive pulse (Step 4) ───────────────────────


def test_step4_competitive_pulse_inactive_when_no_competitors(facade):
    ws = _workspace(competitors=[])
    facade.write_workspace("ws-1", ws)
    facade.write_signal("ws-1", _signal("s1"))

    llm = _llm_factory([])
    brief = assemble_brief("ws-1", None, facade, llm)
    assert brief.competitive_pulse.active is False
    assert brief.competitive_pulse.highlights == []


def test_step4_competitive_pulse_degrades_gracefully_if_research_missing(
    facade, monkeypatch
):
    """If the research package isn't importable, Step 4 returns inactive
    rather than raising."""
    import sys

    ws = _workspace(competitors=["RivalCorp"])
    facade.write_workspace("ws-1", ws)
    facade.write_signal("ws-1", _signal("s1"))

    # Force ImportError on the lazy import inside _step4_competitive_pulse.
    monkeypatch.setitem(sys.modules, "app.research", None)
    monkeypatch.setitem(sys.modules, "app.research.digest", None)

    llm = _llm_factory([])
    brief = assemble_brief("ws-1", None, facade, llm)
    assert brief.competitive_pulse.active is False
