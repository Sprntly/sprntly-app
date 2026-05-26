"""Synthesis Agent — scheduled mode: 11-step Brief Assembly Algorithm.

Spec source: Synthesis_Agent_Spec.docx §3.2 (Brief Assembly Algorithm).

This module replaces the monolithic `app.brief_runner._run_sync` LLM
call with an explicit, step-by-step pipeline. Each of the 11 spec steps
lives in its own private function and is unit-testable in isolation.
The orchestrator `assemble_brief()` wires them together.

Why one-function-per-step:
  * The spec lists the 11 steps as a contract; the code should look
    like the spec. Reviewers comparing PR ↔ spec read one diff per
    step.
  * Each step has its own failure mode (KG read miss vs. LLM bad JSON
    vs. dead-end filter dropping all candidates). Discrete functions
    let us return structured errors per step without unwinding a
    multi-hundred-line monolith.
  * Tests target the score formula, dead-end filter, and promotion
    rule without dragging in LLM mocks.

Tenancy invariant:
  Every read/write goes through `GraphFacade`, which asserts
  `workspace_id` on every call. `assemble_brief()` therefore can NEVER
  touch another tenant's data even with a buggy LLM payload (the worst
  it can do is fail; it cannot cross-tenant leak).

Soft dependencies:
  * `app.research.digest.generate_weekly_digest` (PR #13). Imported
    lazily inside Step 4 because the research package is optional —
    if a deployment doesn't have it, Step 4 returns an empty pulse
    rather than raising.
  * `app.llm.call_json` is injected into `assemble_brief` as a
    parameter so tests can substitute a deterministic fake without
    monkeypatching every consumer.
"""
from __future__ import annotations

import logging
import math
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any, Callable, Optional

from pydantic import BaseModel, ConfigDict, Field

from app.graph import (
    ConfidenceTier,
    GraphFacade,
    Hypothesis,
    HypothesisStatus,
    Signal,
    Workspace,
)
from app.synthesis.hypothesis import (
    HypothesisFraming,
    HypothesisImpact,
    HypothesisOutput,
    SignalCitation,
    TIER_TO_CONFIDENCE,
)

logger = logging.getLogger(__name__)


# ─────────────────────── public output models ───────────────────────


class KpiStatus(BaseModel):
    """One row of the Brief's KPI status summary (spec Step 9)."""

    model_config = ConfigDict(extra="forbid")

    name: str = Field(..., description="KPI name as it appears in Workspace.kpi_tree.")
    role: str = Field(
        ...,
        description="north_star / primary / secondary / leading_indicator (mirrors KpiTreeNode.role).",
    )
    current_value: Optional[float] = None
    target_value: Optional[float] = None
    trend: str = Field(
        ...,
        pattern="^(up|down|flat|unknown)$",
        description="Direction of movement over the last reporting window.",
    )
    pct_to_target: Optional[float] = Field(
        default=None,
        description="(current/target) when both present; None otherwise. Surfaced in the Brief header.",
    )


class SignalHealth(BaseModel):
    """Summary of the evidence pool feeding this Brief."""

    model_config = ConfigDict(extra="forbid")

    total_active: int = Field(..., ge=0)
    by_source_type: dict[str, int] = Field(default_factory=dict)
    stale_count: int = Field(default=0, ge=0)
    avg_confidence: float = Field(default=0.0, ge=0.0, le=1.0)


class OpenOutcome(BaseModel):
    """A Decision whose Outcome hasn't been measured yet — surfaced so PMs
    don't forget to close the loop. Sourced from KG session context."""

    model_config = ConfigDict(extra="forbid")

    outcome_id: str
    linked_decision_id: str
    feature_name: str
    shipped_at: Optional[datetime] = None
    days_outstanding: Optional[int] = Field(
        default=None, description="Days since shipped_at if known."
    )


class CompetitivePulse(BaseModel):
    """Lean projection of CompetitiveDigest for the Brief body."""

    model_config = ConfigDict(extra="forbid")

    active: bool = Field(
        default=False,
        description="True only if the workspace has competitors AND the research connector returned a digest.",
    )
    highlights: list[str] = Field(default_factory=list)
    notable_competitors: list[str] = Field(default_factory=list)


class Brief(BaseModel):
    """The scheduled-mode Brief — what the Synthesis Agent emits weekly.

    Spec §3.2 step 10 — "Assemble Brief". Step 11 — "Deliver Brief via
    configured channels" is the caller's responsibility (Slack, email,
    in-app). This object is the source of truth for the payload.
    """

    model_config = ConfigDict(extra="forbid")

    brief_id: str
    workspace_id: str
    generated_at: datetime
    kpi_status: list[KpiStatus] = Field(default_factory=list)
    recommendations: list[HypothesisOutput] = Field(
        default_factory=list,
        max_length=5,
        description="Top 3–5 ranked recommendations (spec §3.2 Step 8).",
    )
    signal_health: SignalHealth
    competitive_pulse: CompetitivePulse = Field(default_factory=CompetitivePulse)
    open_outcomes: list[OpenOutcome] = Field(default_factory=list)
    caveats: list[str] = Field(
        default_factory=list,
        description="Filter-side dropouts: dead-end matches, missing data warnings.",
    )
    metadata: dict[str, Any] = Field(default_factory=dict)


# ─────────────────────── tunables ───────────────────────

# Number of recommendations to surface in the Brief (spec: 3–5).
MIN_RECOMMENDATIONS = 3
MAX_RECOMMENDATIONS = 5

# Promotion rule (spec §3.2 Step 10):
#   candidate → proposed when evidence_count >= 3 from >= 2 distinct
#   source_types.
PROMOTION_MIN_EVIDENCE = 3
PROMOTION_MIN_DISTINCT_SOURCE_TYPES = 2


# ─────────────────────── data carriers used between steps ───────────────────────


class _Candidate(BaseModel):
    """Intermediate representation flowing through Steps 3 → 8.

    Pydantic model rather than a dict so type errors surface early. The
    final shape emitted to the Brief is `HypothesisOutput`.
    """

    model_config = ConfigDict(extra="forbid", arbitrary_types_allowed=True)

    candidate_id: str
    title: str
    claim: str
    predicted_metric: str
    predicted_impact_low: float
    predicted_impact_high: float
    predicted_impact_basis: str
    impact_direction: str = "up"
    signal_summary: str
    hypothesis_text: str
    assumptions: list[str] = Field(default_factory=list)
    supporting_signal_ids: list[str] = Field(default_factory=list)
    disconfirming_signal_ids: list[str] = Field(default_factory=list)
    ds_agent_tier: Optional[str] = None
    confidence: str = "medium"
    reversal_condition: str
    # Derived during the pipeline:
    is_known_hypothesis: bool = False
    reinforcement_of: Optional[str] = None
    impact_score: float = 0.0
    strategy_score: float = 0.0
    evidence_score: float = 0.0
    composite_score: float = 0.0


# ─────────────────────── Step 1: load session context ───────────────────────


def _step1_load_session_context(
    workspace_id: str, graph: GraphFacade
) -> dict[str, Any]:
    """Spec §3.2 Step 1: Load session context from KG.

    Returns Workspace + active Hypotheses + recent Decisions + recent
    Outcomes. The facade enforces tenant isolation on every read.
    """
    ctx = graph.load_session_context(workspace_id)
    # `active_signals` isn't part of the canonical session_context query
    # (latency budget), but Steps 5–6 need it — pull it separately so
    # this step is the single read-aggregation point.
    ctx["active_signals"] = graph.list_active_signals(workspace_id, limit=200)
    return ctx


# ─────────────────────── Step 2: receive DS Agent output ───────────────────────


def _step2_normalize_ds_output(ds_output: Optional[dict[str, Any]]) -> list[dict[str, Any]]:
    """Spec §3.2 Step 2: Receive Comprehensive DS Agent output.

    Normalises the DS Agent payload into a list of "findings". Each
    finding is the seed for one candidate recommendation. The DS Agent
    may emit:
        { "findings": [...], "tier": "comprehensive", ... }
    or nothing (scheduled briefs run even with no fresh DS results,
    relying on signal trends alone).
    """
    if not ds_output:
        return []
    findings = ds_output.get("findings") or []
    if not isinstance(findings, list):
        return []
    return findings


# ─────────────────────── Step 3: cross-reference against existing Hypotheses ─────


def _step3_cross_reference(
    findings: list[dict[str, Any]], known_hypotheses: list[Hypothesis]
) -> list[dict[str, Any]]:
    """Spec §3.2 Step 3: Cross-reference each finding against existing
    Hypothesis nodes — already known? new signal or reinforcement?

    Adds two annotations to each finding:
      * `is_known_hypothesis` — True if the claim's predicted_metric +
        direction match an open Hypothesis.
      * `reinforcement_of` — the hypothesis_id this reinforces (if any).
    """
    annotated: list[dict[str, Any]] = []
    known_by_metric: dict[str, Hypothesis] = {
        h.predicted_metric.lower(): h for h in known_hypotheses
    }
    for f in findings:
        metric = str(f.get("predicted_metric") or "").lower()
        match = known_by_metric.get(metric)
        out = dict(f)
        if match is not None:
            out["is_known_hypothesis"] = True
            out["reinforcement_of"] = match.hypothesis_id
        else:
            out["is_known_hypothesis"] = False
            out["reinforcement_of"] = None
        annotated.append(out)
    return annotated


# ─────────────────────── Step 4: competitive signals ───────────────────────


def _step4_competitive_pulse(workspace: Workspace) -> CompetitivePulse:
    """Spec §3.2 Step 4: Pull competitive signals (if active). Flag if a
    competitor is moving on a workspace metric.

    Uses `app.research.digest.generate_weekly_digest` (PR #13). If the
    package isn't available or the workspace has no competitors, returns
    an inactive pulse (the Brief renderer drops the section).
    """
    if not workspace.competitors:
        return CompetitivePulse(active=False)
    try:
        from app.research.digest import generate_weekly_digest  # type: ignore
    except ImportError:
        logger.info("Research digest not available; skipping competitive pulse")
        return CompetitivePulse(active=False)
    try:
        digest = generate_weekly_digest(
            workspace.workspace_id,
            [{"name": c} for c in workspace.competitors],
        )
    except Exception:  # research is best-effort
        logger.exception("competitive digest failed; treating as inactive")
        return CompetitivePulse(active=False)
    notable = [p.competitor_name for p in digest.pulses if p.notable]
    return CompetitivePulse(
        active=True,
        highlights=list(digest.top_highlights),
        notable_competitors=notable,
    )


# ─────────────────────── Step 5: customer feedback ───────────────────────


def _step5_weight_customer_feedback(
    active_signals: list[Signal],
) -> dict[str, float]:
    """Spec §3.2 Step 5: Pull customer feedback signals. Weight recent
    high-volume themes.

    Returns a {theme → weight} dict used by Step 6's evidence scoring.
    "Theme" is approximated as the signal's source_tool — a future PR
    can swap in an embedding-based clustering step (Master PRD §7).
    """
    weights: dict[str, float] = {}
    for sig in active_signals:
        if sig.source_type.value != "customer_voice":
            continue
        weights[sig.source_tool] = weights.get(sig.source_tool, 0.0) + sig.confidence
    return weights


# ─────────────────────── Step 6: scoring ───────────────────────


def _impact_score(low: float, high: float) -> float:
    """Heuristic when no north-star variance signal is yet calibrated:
    log-scale of the midpoint of the predicted impact range. Bounded to
    [0, 1] via a soft sigmoid on log10(midpoint)."""
    mid = (low + high) / 2.0
    if mid <= 0:
        return 0.0
    # log10(1) = 0 → 0.0; log10(100) = 2 → ~0.88; saturates quickly.
    raw = math.log10(mid + 1.0) / 3.0  # /3 keeps 100x impact at ~0.67
    return max(0.0, min(1.0, raw))


def _strategy_score(
    predicted_metric: str, workspace: Workspace
) -> float:
    """1.0 if the candidate's predicted_metric overlaps any OKR or
    current_priority string; 0.3 otherwise. Lowercase substring match —
    cheap and good enough for the v1 ranker.
    """
    needle = (predicted_metric or "").lower().strip()
    if not needle:
        return 0.3
    haystack: list[str] = list(workspace.strategy.okrs) + list(
        workspace.strategy.current_priorities
    )
    # Also count KPI tree node names — if the candidate's metric appears
    # in the KPI tree, it's strategically aligned by definition.
    haystack += [k.name for k in workspace.kpi_tree]
    for s in haystack:
        s_lower = (s or "").lower()
        if needle in s_lower or s_lower in needle:
            return 1.0
    return 0.3


def _evidence_score(
    supporting_signal_ids: list[str], all_signals: dict[str, Signal]
) -> float:
    """Spec §3.2 Step 6: evidence_score = min(1.0, evidence_count/5) *
    avg(signal.confidence)."""
    if not supporting_signal_ids:
        return 0.0
    confidences: list[float] = []
    for sid in supporting_signal_ids:
        sig = all_signals.get(sid)
        if sig is not None:
            confidences.append(sig.confidence)
    if not confidences:
        return 0.0
    count_part = min(1.0, len(supporting_signal_ids) / 5.0)
    avg_conf = sum(confidences) / len(confidences)
    return count_part * avg_conf


def _step6_score_candidates(
    candidates: list[_Candidate],
    workspace: Workspace,
    active_signals: list[Signal],
) -> list[_Candidate]:
    """Spec §3.2 Step 6: Score each potential recommendation.

    Linear combination:
        composite = 0.5 * impact + 0.3 * strategy + 0.2 * evidence
    """
    signal_index: dict[str, Signal] = {s.signal_id: s for s in active_signals}
    for c in candidates:
        c.impact_score = _impact_score(
            c.predicted_impact_low, c.predicted_impact_high
        )
        c.strategy_score = _strategy_score(c.predicted_metric, workspace)
        c.evidence_score = _evidence_score(c.supporting_signal_ids, signal_index)
        c.composite_score = (
            0.5 * c.impact_score
            + 0.3 * c.strategy_score
            + 0.2 * c.evidence_score
        )
    return candidates


# ─────────────────────── Step 7: dead-end filter ───────────────────────


def _step7_filter_dead_ends(
    candidates: list[_Candidate], workspace: Workspace
) -> tuple[list[_Candidate], list[str]]:
    """Spec §3.2 Step 7: Filter against dead ends. Never recommend
    something explicitly excluded.

    Token-based match: any non-trivial token (>=4 chars after lowering)
    from a dead-end appearing in claim/hypothesis_text/predicted_metric
    drops the candidate. Drops are recorded in `caveats` for the Brief.
    """
    dead_ends = [d for d in (workspace.strategy.dead_ends or []) if d.strip()]
    if not dead_ends:
        return candidates, []
    caveats: list[str] = []
    kept: list[_Candidate] = []
    for c in candidates:
        blob = " ".join(
            [c.claim, c.hypothesis_text, c.predicted_metric, c.signal_summary]
        ).lower()
        dropped_reason: Optional[str] = None
        for d in dead_ends:
            tokens = [t for t in d.lower().split() if len(t) >= 4]
            # Also try the whole dead-end string verbatim — useful for
            # phrases like "manual onboarding".
            if d.lower().strip() and d.lower().strip() in blob:
                dropped_reason = d
                break
            if tokens and all(t in blob for t in tokens):
                dropped_reason = d
                break
        if dropped_reason is not None:
            caveats.append(
                f'Dropped candidate "{c.title}" — matched dead-end '
                f'"{dropped_reason}".'
            )
            continue
        kept.append(c)
    return kept, caveats


# ─────────────────────── Step 8: rank top 3-5 ───────────────────────


def _step8_rank(candidates: list[_Candidate]) -> list[_Candidate]:
    """Spec §3.2 Step 8: Rank top 3-5 recommendations.

    Sort by composite_score DESC, take up to MAX_RECOMMENDATIONS.
    The MIN_RECOMMENDATIONS floor is informational — if the candidate
    pool is smaller, we surface what we have rather than padding.
    """
    ranked = sorted(candidates, key=lambda c: c.composite_score, reverse=True)
    return ranked[:MAX_RECOMMENDATIONS]


# ─────────────────────── Step 9: KPI status summary ───────────────────────


def _step9_kpi_status(workspace: Workspace) -> list[KpiStatus]:
    """Spec §3.2 Step 9: Generate KPI status summary (current vs target,
    trend direction).

    Trend direction is "unknown" in this v1: we don't yet store
    historical KPI snapshots in the KG. A follow-up PR can plug in the
    `Signal` time series for each KPI to compute trend.
    """
    out: list[KpiStatus] = []
    for k in workspace.kpi_tree:
        pct: Optional[float] = None
        if (
            k.current_value is not None
            and k.target_value not in (None, 0, 0.0)
        ):
            try:
                pct = float(k.current_value) / float(k.target_value)
            except (TypeError, ZeroDivisionError):
                pct = None
        out.append(
            KpiStatus(
                name=k.name,
                role=k.role,
                current_value=k.current_value,
                target_value=k.target_value,
                trend="unknown",
                pct_to_target=pct,
            )
        )
    return out


# ─────────────────────── Step 10: write Hypothesis nodes ───────────────────────


def _promotion_status(
    supporting_signal_ids: list[str], signal_index: dict[str, Signal]
) -> HypothesisStatus:
    """Spec §3.2 Step 10: status = proposed if evidence_count >= 3 from
    independent sources (>= 2 distinct source_types), else candidate.
    """
    if len(supporting_signal_ids) < PROMOTION_MIN_EVIDENCE:
        return HypothesisStatus.CANDIDATE
    source_types: set[str] = set()
    for sid in supporting_signal_ids:
        sig = signal_index.get(sid)
        if sig is not None:
            source_types.add(sig.source_type.value)
    if len(source_types) >= PROMOTION_MIN_DISTINCT_SOURCE_TYPES:
        return HypothesisStatus.PROPOSED
    return HypothesisStatus.CANDIDATE


def _confidence_tier(confidence: str) -> ConfidenceTier:
    """Map HypothesisOutput's literal confidence string to the KG enum."""
    return {
        "low": ConfidenceTier.LOW,
        "medium": ConfidenceTier.MEDIUM,
        "high": ConfidenceTier.HIGH,
        "very_high": ConfidenceTier.VERY_HIGH,
    }.get(confidence, ConfidenceTier.MEDIUM)


def _candidate_to_hypothesis(
    candidate: _Candidate,
    workspace_id: str,
    status: HypothesisStatus,
    brief_id: str,
    rank: int,
) -> Hypothesis:
    now = datetime.now(timezone.utc)
    return Hypothesis(
        workspace_id=workspace_id,
        valid_at=now - timedelta(seconds=1),
        transaction_at=now,
        hypothesis_id=f"hyp-{candidate.candidate_id}",
        claim=candidate.claim,
        predicted_metric=candidate.predicted_metric,
        predicted_impact_low=candidate.predicted_impact_low,
        predicted_impact_high=candidate.predicted_impact_high,
        predicted_impact_basis=candidate.predicted_impact_basis,
        status=status,
        evidence_signal_ids=candidate.supporting_signal_ids or ["unknown"],
        evidence_count=max(1, len(candidate.supporting_signal_ids)),
        confidence_composite=max(
            0.0, min(1.0, candidate.composite_score)
        ),
        confidence_tier=_confidence_tier(candidate.confidence),
        reversal_condition=candidate.reversal_condition,
        created_at=now,
        status_updated_at=now,
        assumptions=candidate.assumptions,
        disconfirming_signals=candidate.disconfirming_signal_ids,
        brief_id=brief_id,
        brief_rank=rank,
    )


def _step10_write_hypotheses(
    candidates: list[_Candidate],
    workspace_id: str,
    brief_id: str,
    graph: GraphFacade,
    active_signals: list[Signal],
) -> list[Hypothesis]:
    """Spec §3.2 Step 10: Assemble Brief. Write Hypothesis nodes to KG.

    Status is `proposed` iff (evidence_count >= 3) AND (>= 2 distinct
    source_types in the supporting set). Otherwise `candidate`.
    """
    signal_index: dict[str, Signal] = {s.signal_id: s for s in active_signals}
    written: list[Hypothesis] = []
    for rank, c in enumerate(candidates, start=1):
        status = _promotion_status(c.supporting_signal_ids, signal_index)
        h = _candidate_to_hypothesis(
            c, workspace_id, status, brief_id=brief_id, rank=rank
        )
        graph.write_hypothesis(workspace_id, h)
        written.append(h)
    return written


# ─────────────── Step 10 helper: candidate → HypothesisOutput ───────────────


def _to_hypothesis_output(
    candidate: _Candidate,
    rank: int,
    active_signals: dict[str, Signal],
) -> HypothesisOutput:
    """Build the `HypothesisOutput` for the Brief body from a candidate."""
    supporting: list[SignalCitation] = []
    for sid in candidate.supporting_signal_ids:
        sig = active_signals.get(sid)
        if sig is None:
            continue
        summary = sig.content[:277] + "..." if len(sig.content) > 280 else sig.content
        supporting.append(
            SignalCitation(
                signal_id=sig.signal_id,
                source_tool=sig.source_tool,
                summary=summary,
                confidence=sig.confidence,
            )
        )
    disconfirming: list[SignalCitation] = []
    for sid in candidate.disconfirming_signal_ids:
        sig = active_signals.get(sid)
        if sig is None:
            continue
        summary = sig.content[:277] + "..." if len(sig.content) > 280 else sig.content
        disconfirming.append(
            SignalCitation(
                signal_id=sig.signal_id,
                source_tool=sig.source_tool,
                summary=summary,
                confidence=sig.confidence,
            )
        )
    if not supporting:
        # HypothesisOutput requires >=1 supporting signal. Synthesize a
        # placeholder so a candidate produced by the LLM without KG-
        # backed citations still round-trips. The KG status will be
        # `candidate` (per Step 10 promotion rule) so this won't get
        # silently promoted.
        supporting.append(
            SignalCitation(
                signal_id=f"agent-inferred-{candidate.candidate_id}",
                source_tool="agent",
                summary=candidate.signal_summary[:280],
                confidence=0.5,
            )
        )
    impact = HypothesisImpact(
        metric=candidate.predicted_metric,
        direction="up" if candidate.impact_direction == "up" else "down",
        low=candidate.predicted_impact_low,
        high=candidate.predicted_impact_high,
        basis=candidate.predicted_impact_basis,
    )
    framing = HypothesisFraming(
        signal_summary=candidate.signal_summary,
        hypothesis=candidate.hypothesis_text,
        predicted_impact=impact,
        assumptions=candidate.assumptions,
        disconfirming_signals=disconfirming,
    )
    ds_tier = candidate.ds_agent_tier if candidate.ds_agent_tier in (
        "express", "deep", "comprehensive"
    ) else None
    return HypothesisOutput(
        rank=rank,
        title=candidate.title[:140],
        framing=framing,
        supporting_signals=supporting,
        confidence=candidate.confidence
        if candidate.confidence in ("low", "medium", "high", "very_high")
        else "medium",
        ds_agent_tier=ds_tier,  # type: ignore[arg-type]
        reversal_condition=candidate.reversal_condition,
    )


# ─────────────────────── Step 10 helper: signal health ───────────────────────


def _summarise_signal_health(active_signals: list[Signal]) -> SignalHealth:
    by_type: dict[str, int] = {}
    confidences: list[float] = []
    stale_count = 0
    now = datetime.now(timezone.utc)
    for s in active_signals:
        by_type[s.source_type.value] = by_type.get(s.source_type.value, 0) + 1
        confidences.append(s.confidence)
        if s.stale_after is not None and s.stale_after < now:
            stale_count += 1
    return SignalHealth(
        total_active=len(active_signals),
        by_source_type=by_type,
        stale_count=stale_count,
        avg_confidence=(sum(confidences) / len(confidences)) if confidences else 0.0,
    )


# ─────────────────────── Step 10 helper: open outcomes ───────────────────────


def _open_outcomes(session_ctx: dict[str, Any]) -> list[OpenOutcome]:
    out: list[OpenOutcome] = []
    now = datetime.now(timezone.utc)
    for o in session_ctx.get("recent_outcomes") or []:
        # An outcome is "open" if actual_impact hasn't been measured.
        if getattr(o, "actual_impact_measured_at", None) is not None:
            continue
        shipped = getattr(o, "shipped_at", None)
        days = None
        if shipped is not None:
            try:
                days = (now - shipped).days
            except Exception:
                days = None
        out.append(
            OpenOutcome(
                outcome_id=getattr(o, "outcome_id"),
                linked_decision_id=getattr(o, "linked_decision_id"),
                feature_name=getattr(o, "feature_name"),
                shipped_at=shipped,
                days_outstanding=days,
            )
        )
    return out


# ─────────────────────── LLM-driven candidate generation ───────────────────────


_LLM_RECOMMENDATIONS_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "recommendations": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "title": {"type": "string"},
                    "claim": {"type": "string"},
                    "signal_summary": {"type": "string"},
                    "hypothesis": {"type": "string"},
                    "predicted_metric": {"type": "string"},
                    "predicted_impact_low": {"type": "number"},
                    "predicted_impact_high": {"type": "number"},
                    "predicted_impact_basis": {"type": "string"},
                    "impact_direction": {"type": "string"},
                    "assumptions": {
                        "type": "array",
                        "items": {"type": "string"},
                    },
                    "supporting_signal_ids": {
                        "type": "array",
                        "items": {"type": "string"},
                    },
                    "disconfirming_signal_ids": {
                        "type": "array",
                        "items": {"type": "string"},
                    },
                    "ds_agent_tier": {"type": "string"},
                    "confidence": {"type": "string"},
                    "reversal_condition": {"type": "string"},
                },
                "required": [
                    "title",
                    "claim",
                    "signal_summary",
                    "hypothesis",
                    "predicted_metric",
                    "predicted_impact_low",
                    "predicted_impact_high",
                    "predicted_impact_basis",
                    "reversal_condition",
                ],
            },
        }
    },
    "required": ["recommendations"],
}


_LLM_SYSTEM = """You are the Sprntly Synthesis Agent operating in scheduled mode.
You receive cross-source signals + DS Agent findings and propose a small set of
actionable build hypotheses. Every recommendation MUST cite at least one
signal_id from the active pool. Recommendations the user cannot act on, or
that touch a "dead end" the user has explicitly excluded, MUST be omitted.
Quantified predicted_impact is REQUIRED — never emit "TBD" or "unknown" here.
Output strictly conforms to the submit_response tool schema."""


def _llm_generate_candidates(
    workspace: Workspace,
    findings: list[dict[str, Any]],
    active_signals: list[Signal],
    competitive_pulse: CompetitivePulse,
    customer_weights: dict[str, float],
    llm_call: Callable[..., dict[str, Any]],
) -> list[_Candidate]:
    """Drive the LLM call that proposes recommendations from the gathered
    context. The LLM is invoked exactly once per Brief — Steps 6/7 (score
    + dead-end filter) operate on its output.
    """
    user_payload = {
        "workspace": {
            "company_name": workspace.company_name,
            "industry": workspace.industry,
            "okrs": workspace.strategy.okrs,
            "current_priorities": workspace.strategy.current_priorities,
            "dead_ends": workspace.strategy.dead_ends,
            "kpi_tree": [k.model_dump() for k in workspace.kpi_tree],
        },
        "ds_findings": findings,
        "active_signals": [
            {
                "signal_id": s.signal_id,
                "content": s.content,
                "source_type": s.source_type.value,
                "source_tool": s.source_tool,
                "confidence": s.confidence,
            }
            for s in active_signals[:50]  # bound prompt size
        ],
        "competitive_pulse_highlights": competitive_pulse.highlights,
        "customer_voice_weights": customer_weights,
    }
    import json
    response = llm_call(
        system=_LLM_SYSTEM,
        user=json.dumps(user_payload, default=str),
        schema=_LLM_RECOMMENDATIONS_SCHEMA,
    )
    recs = (response or {}).get("recommendations") or []
    out: list[_Candidate] = []
    for i, r in enumerate(recs):
        try:
            out.append(
                _Candidate(
                    candidate_id=str(r.get("candidate_id") or f"c{i + 1}"),
                    title=str(r["title"]),
                    claim=str(r["claim"]),
                    predicted_metric=str(r["predicted_metric"]),
                    predicted_impact_low=float(r["predicted_impact_low"]),
                    predicted_impact_high=float(r["predicted_impact_high"]),
                    predicted_impact_basis=str(r["predicted_impact_basis"]),
                    impact_direction=str(r.get("impact_direction") or "up"),
                    signal_summary=str(r["signal_summary"]),
                    hypothesis_text=str(r["hypothesis"]),
                    assumptions=list(r.get("assumptions") or []),
                    supporting_signal_ids=list(r.get("supporting_signal_ids") or []),
                    disconfirming_signal_ids=list(
                        r.get("disconfirming_signal_ids") or []
                    ),
                    ds_agent_tier=r.get("ds_agent_tier"),
                    confidence=str(r.get("confidence") or "medium"),
                    reversal_condition=str(r["reversal_condition"]),
                )
            )
        except (KeyError, TypeError, ValueError) as exc:
            logger.warning(
                "Skipping malformed LLM recommendation idx=%s err=%s", i, exc
            )
    return out


# ─────────────────────── orchestrator ───────────────────────


def assemble_brief(
    workspace_id: str,
    ds_output: Optional[dict[str, Any]],
    graph: GraphFacade,
    llm_call: Callable[..., dict[str, Any]],
    *,
    brief_id: Optional[str] = None,
) -> Brief:
    """The 11-step scheduled-mode Brief Assembly pipeline.

    Steps map 1:1 to Synthesis_Agent_Spec §3.2:
        1.  Load session context from KG.
        2.  Receive Comprehensive DS Agent output.
        3.  Cross-reference findings against existing Hypotheses.
        4.  Pull competitive signals (if active).
        5.  Pull customer feedback signals; weight high-volume themes.
        6.  Score each potential recommendation.
        7.  Filter against dead-ends.
        8.  Rank top 3–5.
        9.  Generate KPI status summary.
        10. Assemble Brief; write Hypothesis nodes to KG.
        11. Deliver Brief via configured channels. (Caller responsibility.)

    Args:
        workspace_id: Tenant to operate on. All KG reads/writes are
            scoped to this workspace via the facade.
        ds_output: Comprehensive DS Agent output, or None for a
            signal-only brief.
        graph: The KG facade.
        llm_call: A `call_json`-compatible function. Injected so tests
            can substitute a deterministic fake.
        brief_id: Optional pre-assigned brief ID (lets the caller wire
            up cross-references). One is generated otherwise.

    Returns the assembled `Brief`. Step 11 (delivery) is the caller's
    responsibility.
    """
    if brief_id is None:
        brief_id = f"brief-{uuid.uuid4().hex[:12]}"

    # Step 1
    session_ctx = _step1_load_session_context(workspace_id, graph)
    workspace: Optional[Workspace] = session_ctx.get("workspace")
    if workspace is None:
        # Empty Brief — Workspace doesn't exist. Surface a caveat so the
        # caller can decide what to do (delivery is their job).
        return Brief(
            brief_id=brief_id,
            workspace_id=workspace_id,
            generated_at=datetime.now(timezone.utc),
            kpi_status=[],
            recommendations=[],
            signal_health=SignalHealth(total_active=0),
            competitive_pulse=CompetitivePulse(active=False),
            open_outcomes=[],
            caveats=[f"Workspace {workspace_id} not found in KG."],
            metadata={"empty": True},
        )

    active_signals: list[Signal] = session_ctx.get("active_signals") or []
    known_hyps: list[Hypothesis] = session_ctx.get("active_hypotheses") or []

    # Step 2
    findings = _step2_normalize_ds_output(ds_output)
    # Step 3
    annotated_findings = _step3_cross_reference(findings, known_hyps)
    # Step 4
    competitive_pulse = _step4_competitive_pulse(workspace)
    # Step 5
    customer_weights = _step5_weight_customer_feedback(active_signals)

    # Bridge: LLM generates candidate recommendations from the gathered
    # context. Empty signal pool with no findings → skip the LLM call
    # entirely so we don't incur cost (and we have nothing to feed it).
    candidates: list[_Candidate] = []
    if active_signals or annotated_findings:
        candidates = _llm_generate_candidates(
            workspace,
            annotated_findings,
            active_signals,
            competitive_pulse,
            customer_weights,
            llm_call,
        )
        # Carry the Step-3 cross-reference annotations onto the candidates
        # where the LLM identified the underlying finding.
        finding_by_metric: dict[str, dict[str, Any]] = {}
        for f in annotated_findings:
            m = str(f.get("predicted_metric") or "").lower()
            if m:
                finding_by_metric[m] = f
        for c in candidates:
            f = finding_by_metric.get(c.predicted_metric.lower())
            if f is not None:
                c.is_known_hypothesis = bool(f.get("is_known_hypothesis"))
                c.reinforcement_of = f.get("reinforcement_of")

    # Step 6
    candidates = _step6_score_candidates(candidates, workspace, active_signals)
    # Step 7
    candidates, caveats = _step7_filter_dead_ends(candidates, workspace)
    # Step 8
    ranked = _step8_rank(candidates)
    # Step 9
    kpi_status = _step9_kpi_status(workspace)
    # Step 10
    signal_index: dict[str, Signal] = {s.signal_id: s for s in active_signals}
    hypothesis_outputs: list[HypothesisOutput] = []
    for rank, c in enumerate(ranked, start=1):
        hypothesis_outputs.append(_to_hypothesis_output(c, rank, signal_index))
    _step10_write_hypotheses(
        ranked, workspace_id, brief_id, graph, active_signals
    )
    signal_health = _summarise_signal_health(active_signals)
    open_outcomes = _open_outcomes(session_ctx)
    if not candidates:
        caveats.append(
            "No actionable recommendations this cycle — empty signal pool "
            "or all candidates filtered."
        )

    return Brief(
        brief_id=brief_id,
        workspace_id=workspace_id,
        generated_at=datetime.now(timezone.utc),
        kpi_status=kpi_status,
        recommendations=hypothesis_outputs,
        signal_health=signal_health,
        competitive_pulse=competitive_pulse,
        open_outcomes=open_outcomes,
        caveats=caveats,
        metadata={
            "ds_findings_received": len(findings),
            "candidates_generated": len(candidates) + len(caveats),
            "active_signal_count": len(active_signals),
            "known_hypothesis_count": len(known_hyps),
        },
    )


__all__ = [
    # public output models
    "Brief",
    "KpiStatus",
    "SignalHealth",
    "OpenOutcome",
    "CompetitivePulse",
    # orchestrator
    "assemble_brief",
    # constants useful for callers/tests
    "MIN_RECOMMENDATIONS",
    "MAX_RECOMMENDATIONS",
    "PROMOTION_MIN_EVIDENCE",
    "PROMOTION_MIN_DISTINCT_SOURCE_TYPES",
]
