"""DETECT — convergence computation over the brain (design §4 step 2).

Pure code, no LLM: for each theme, gather its inbound signal edges and
compute the quantitative dimensions of the §4c base score:
  - convergence breadth (distinct source_types agreeing)
  - effective evidence weight (confidence × source-accuracy weight × recency
    half-life decay, per #1)
  - revenue at stake (summed from signal properties)
  - competitive pressure (PRESSURES edges)
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field
from datetime import datetime, timezone

from app.graph.facade import GraphFacade
from app.graph.types import SOURCE_STALE_WINDOW_DAYS, Signal


@dataclass
class ThemeConvergence:
    theme_id: str
    theme_label: str
    signal_count: int = 0
    source_types: set[str] = field(default_factory=set)
    effective_weight: float = 0.0
    revenue_at_stake_usd: float = 0.0
    competitor_pressure: int = 0
    evidence: list[dict] = field(default_factory=list)  # top signals for the LLM pass

    @property
    def breadth(self) -> int:
        return len(self.source_types)


def _recency_factor(signal: Signal, now: datetime) -> float:
    """Half-life decay using the per-source_type window (#1). Never-expiring
    source types (outcome_measured) don't decay."""
    window = SOURCE_STALE_WINDOW_DAYS.get(signal.source_type)
    if not window:
        return 1.0
    age_days = max(0.0, (now - signal.valid_at).total_seconds() / 86400)
    return math.pow(0.5, age_days / window)


def compute_convergence(
    facade: GraphFacade,
    enterprise_id: str,
    *,
    max_evidence_per_theme: int = 8,
) -> list[ThemeConvergence]:
    """Score every theme by multi-source convergence. Returns themes sorted
    by (breadth, effective_weight) descending."""
    now = datetime.now(timezone.utc)
    themes = facade.query_entities(enterprise_id, type="theme")
    out: list[ThemeConvergence] = []

    for theme in themes:
        tc = ThemeConvergence(theme_id=theme.id, theme_label=theme.canonical_label)
        scored_evidence: list[tuple[float, dict]] = []
        for edge in facade.edges_to(enterprise_id, theme.id):
            if edge.source_kind != "signal":
                continue
            sig = facade.get_signal(enterprise_id, edge.source_id)
            if sig is None or sig.properties.get("superseded_by"):
                continue
            w = sig.confidence * sig.weight * _recency_factor(sig, now)
            tc.signal_count += 1
            tc.source_types.add(sig.source_type)
            tc.effective_weight += w
            rev = sig.properties.get("revenue_at_risk_usd") or sig.properties.get("revenue_usd") or 0
            try:
                tc.revenue_at_stake_usd += float(rev)
            except (TypeError, ValueError):
                pass
            if edge.type == "PRESSURES" or sig.kind == "competitor_move":
                tc.competitor_pressure += 1
            scored_evidence.append((w, {
                "content": sig.content, "kind": sig.kind,
                "source_type": sig.source_type, "edge": edge.type,
                "weight": round(w, 3), "signal_id": sig.id,
            }))
        scored_evidence.sort(key=lambda t: -t[0])
        tc.evidence = [e for _, e in scored_evidence[:max_evidence_per_theme]]
        if tc.signal_count:
            out.append(tc)

    out.sort(key=lambda t: (-t.breadth, -t.effective_weight))
    return out
