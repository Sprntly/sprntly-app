"""Routing engine — picks the right tier for a question.

Spec sources: DS_Routing_Spec.docx.

Step 0: data quality assessment (HIGH/MEDIUM/LOW/INSUFFICIENT)
  - INSUFFICIENT → REJECT
  - LOW          → CLAUDE_CODE_FALLBACK
  - HIGH/MEDIUM  → continue

Step 1: hard constraints
  - free tier              → EXPRESS
  - Monday Brief           → COMPREHENSIVE (bypass)
  - cache hit              → LOOKUP

Step 2: question classification — keyword-based
  - EXPLORATORY / DECISION / MEASUREMENT / STRATEGIC / COMPARATIVE

Step 3: 4 guardrails
  - rare segment expected  → bump to COMPREHENSIVE
  - strategic depth        → require COMPREHENSIVE
  - over-analysis          → demote to EXPRESS (recent duplicate runs)
  - redundant              → LOOKUP

Step 4: default routing table
  - EXPLORATORY            → EXPRESS
  - MEASUREMENT            → DEEP
  - DECISION               → DEEP
  - STRATEGIC              → COMPREHENSIVE
  - COMPARATIVE            → DEEP
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

import pandas as pd

from .types import QualityTier, QuestionType, RoutingDecision, Tier


# ─────────────────────────── Step 0 ───────────────────────────


def assess_quality(rows: pd.DataFrame | int) -> QualityTier:
    """Quick data-quality bucket.

    Accepts either a DataFrame (preferred) or a raw row-count int.
    Thresholds match the spec:
      - n < 500           → INSUFFICIENT
      - n < 2000 or compl<0.50 → LOW
      - n < 10000 or compl<0.80 → MEDIUM
      - else              → HIGH
    """
    if isinstance(rows, int):
        n = rows
        completeness = 1.0
    else:
        n = len(rows)
        completeness = float(1.0 - rows.isna().mean().mean()) if n > 0 else 0.0

    if n < 500:
        return "INSUFFICIENT"
    if n < 2000 or completeness < 0.50:
        return "LOW"
    if n < 10_000 or completeness < 0.80:
        return "MEDIUM"
    return "HIGH"


# ─────────────────────────── Step 2 ───────────────────────────

_KEYWORDS: dict[QuestionType, tuple[str, ...]] = {
    "EXPLORATORY": ("explore", "what drives", "patterns", "anything interesting", "show me", "overview"),
    "DECISION": ("should we", "decide", "go/no-go", "pick", "choose", "recommend"),
    "MEASUREMENT": ("how much", "measure", "lift", "impact of", "how big", "estimate"),
    "STRATEGIC": ("strategy", "roadmap", "long-term", "north star", "vision", "quarter", "annual"),
    "COMPARATIVE": ("compare", "versus", "vs", "difference between", "a/b", "ab test"),
}


def classify_question(text: str) -> QuestionType:
    t = text.lower()
    scores: dict[QuestionType, int] = {q: 0 for q in _KEYWORDS}
    for q, kws in _KEYWORDS.items():
        for kw in kws:
            if kw in t:
                scores[q] += 1
    best = max(scores.items(), key=lambda kv: kv[1])
    if best[1] == 0:
        return "EXPLORATORY"  # default
    return best[0]


# ─────────────────────────── Step 3 ───────────────────────────


@dataclass
class RoutingOverride:
    tier: Tier
    reason: str


_RARE_SEGMENT_HINTS = ("power user", "whales", "rare", "top 1%", "long tail", "outlier")
_STRATEGIC_DEPTH_HINTS = ("strategic", "deep dive", "comprehensive", "full analysis", "thorough")


def check_guardrails(
    question_text: str,
    *,
    recent_runs: list[dict[str, Any]] | None = None,
    base_tier: Tier = "DEEP",
) -> tuple[RoutingOverride | None, list[str]]:
    """Apply the 4 guardrails. Returns (override or None, list of triggered names)."""
    text = question_text.lower()
    triggered: list[str] = []

    recent_runs = recent_runs or []

    # 1) Rare segment expected → bump up
    if any(h in text for h in _RARE_SEGMENT_HINTS):
        triggered.append("rare_segment_expected")
        return RoutingOverride("COMPREHENSIVE", "rare_segment_requires_isolation_forest"), triggered

    # 2) Strategic depth → require COMPREHENSIVE
    if any(h in text for h in _STRATEGIC_DEPTH_HINTS):
        triggered.append("strategic_depth")
        return RoutingOverride("COMPREHENSIVE", "strategic_depth_requested"), triggered

    # 3) Redundant — same question hash within last 24h → LOOKUP
    q_normalised = " ".join(text.split())
    for r in recent_runs:
        if r.get("question", "").lower().strip() == q_normalised and r.get("age_hours", 9999) < 24:
            triggered.append("redundant")
            return RoutingOverride("LOOKUP", "redundant_question_cache_hit"), triggered

    # 4) Over-analysis — more than 3 runs on this dataset in the last 7 days → demote to EXPRESS
    week_count = sum(1 for r in recent_runs if r.get("age_hours", 9999) < 24 * 7)
    if week_count >= 3 and base_tier in ("DEEP", "COMPREHENSIVE"):
        triggered.append("over_analysis")
        return RoutingOverride("EXPRESS", "over_analysis_protection"), triggered

    return None, triggered


# ─────────────────────────── Step 4 — main entry ───────────────────────────

_DEFAULT_TIER: dict[QuestionType, Tier] = {
    "EXPLORATORY": "EXPRESS",
    "DECISION": "DEEP",
    "MEASUREMENT": "DEEP",
    "STRATEGIC": "COMPREHENSIVE",
    "COMPARATIVE": "DEEP",
}


def route(
    question: str,
    *,
    user_plan: str = "pro",
    data_quality: QualityTier | pd.DataFrame | int | None = None,
    cache_state: dict[str, Any] | None = None,
    recent_runs: list[dict[str, Any]] | None = None,
    is_monday_brief: bool = False,
) -> RoutingDecision:
    # ─── Step 0: data quality
    if isinstance(data_quality, (pd.DataFrame, int)):
        quality = assess_quality(data_quality)
    elif data_quality is None:
        quality = "HIGH"  # caller didn't check — assume ok
    else:
        quality = data_quality

    if quality == "INSUFFICIENT":
        return RoutingDecision(
            tier="REJECT",
            question_type=None,
            quality=quality,
            reason="insufficient_data_hard_reject",
        )
    if quality == "LOW":
        return RoutingDecision(
            tier="CLAUDE_CODE_FALLBACK",
            question_type=None,
            quality=quality,
            reason="low_quality_data_fallback_to_claude_code",
        )

    # ─── Step 1: hard constraints
    if is_monday_brief:
        return RoutingDecision(
            tier="COMPREHENSIVE",
            question_type=None,
            quality=quality,
            reason="monday_brief_bypass",
        )

    if user_plan == "free":
        return RoutingDecision(
            tier="EXPRESS",
            question_type=classify_question(question),
            quality=quality,
            reason="free_tier_capped_at_express",
        )

    if cache_state and cache_state.get("hit"):
        return RoutingDecision(
            tier="LOOKUP",
            question_type=classify_question(question),
            quality=quality,
            reason="cache_hit",
        )

    # ─── Step 2: classify
    qtype: QuestionType = classify_question(question)

    # ─── Step 3: guardrails (run before final routing so they can override)
    base = _DEFAULT_TIER[qtype]
    override, triggered = check_guardrails(question, recent_runs=recent_runs, base_tier=base)
    if override:
        return RoutingDecision(
            tier=override.tier,
            question_type=qtype,
            quality=quality,
            reason=override.reason,
            guardrails_triggered=triggered,
        )

    # ─── Step 4: default routing
    return RoutingDecision(
        tier=base,
        question_type=qtype,
        quality=quality,
        reason=f"default_for_{qtype}",
        guardrails_triggered=triggered,
    )
