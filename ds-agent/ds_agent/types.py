"""Shared dataclasses for Stages 2-5 + tiers + routing.

We use stdlib dataclasses (no Pydantic dependency) — the spec calls for a
BaseModel-shaped contract but the rest of the package is dataclass-based
and we want to keep import surface minimal. Both serialise to dicts via
``dataclasses.asdict`` which is what the existing assemble() expects.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Literal

Confidence = Literal["LOW", "MEDIUM", "HIGH"]
Direction = Literal["positive", "negative"]


@dataclass
class Finding:
    feature: str
    importance: float
    direction: Direction
    confidence: Confidence
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass
class StageOutput:
    stage: int
    findings: list[Finding] = field(default_factory=list)
    elapsed_seconds: float = 0.0
    cost_estimate_usd: float = 0.0
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass
class PSMResult:
    treatment: str
    estimate: float  # ATT in natural units of goal_metric
    p_value: float
    n_treated_matched: int
    n_control_matched: int
    caliper: float


@dataclass
class DiDResult:
    treatment: str
    estimate: float  # coefficient on time:treatment interaction
    p_value: float
    n_obs: int


@dataclass
class ImpactConcentration:
    segment_size_pct: float
    contribution_pct: float
    ratio: float  # contribution / size — >1 means a concentrated driver


@dataclass
class RobustnessResult:
    consistency_score: float  # fraction of runs in which finding survived
    n_runs: int
    boost_applied: bool


@dataclass
class LiftScenarios:
    conservative_pp: float
    realistic_pp: float
    optimistic_pp: float
    capped_at: float


# ─── Routing ─────────────────────────────────────────────────────────

QualityTier = Literal["HIGH", "MEDIUM", "LOW", "INSUFFICIENT"]
QuestionType = Literal["EXPLORATORY", "DECISION", "MEASUREMENT", "STRATEGIC", "COMPARATIVE"]
Tier = Literal["EXPRESS", "DEEP", "COMPREHENSIVE", "LOOKUP", "CLAUDE_CODE_FALLBACK", "REJECT"]


@dataclass
class RoutingDecision:
    tier: Tier
    question_type: QuestionType | None
    quality: QualityTier
    reason: str
    guardrails_triggered: list[str] = field(default_factory=list)


@dataclass
class DataSummary:
    """Compact JSON-ish blob fed to Express tier prompt."""

    n_rows: int
    n_features: int
    goal_metric: str
    completeness: float
    feature_means: dict[str, float] = field(default_factory=dict)
    feature_corr_with_goal: dict[str, float] = field(default_factory=dict)
    extras: dict[str, Any] = field(default_factory=dict)
