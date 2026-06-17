"""Brief de-duplication — "don't resurface a finding unless the issue changed".

A theme that converges highly would otherwise reappear in the brief's top-N
every single week. This module suppresses a previously-surfaced theme from brief
candidacy UNLESS its convergence has materially changed since it was last
surfaced, comparing the live `ThemeConvergence` against the fingerprint stored
in `brief_finding_state` (see db/finding_state.py).

"Materially changed" (per product decision) = new evidence OR a ≥20% metric
shift:
  - new evidence  — more contributing signals than last time, or a fresher
                    signal than the one on record;
  - revenue moved — |Δ revenue-at-stake| ≥ 20% (revenue is summed RAW, not
                    recency-decayed, so any move is a real change);
  - breadth moved — the set of agreeing source types changed size;
  - intensified   — effective_weight rose ≥20% (UPWARD only: effective_weight
                    decays with time via the recency half-life, so a *drop* is
                    just staleness, not a new development — counting it would
                    falsely resurface an untouched issue every week).

Suppressed (unchanged, already-surfaced) themes simply don't enter brief
candidacy; they still flow to the backlog via the normal sequencer.
"""
from __future__ import annotations

from datetime import datetime

RESURFACE_DELTA = 0.20  # ≥20% metric move counts as "materially changed"


def _parse_ts(value) -> datetime | None:
    """Parse a stored timestamp (ISO string from Supabase, or already a
    datetime from the in-memory fake) into a datetime, or None."""
    if value is None:
        return None
    if isinstance(value, datetime):
        return value
    try:
        return datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except (TypeError, ValueError):
        return None


def _rel_increase(curr: float, prev: float) -> float:
    """Fractional UPWARD change of curr vs prev. 0 if not higher; treats a rise
    from zero as a full change."""
    if curr <= prev:
        return 0.0
    if prev <= 0:
        return float("inf")
    return (curr - prev) / prev


def _rel_delta(curr: float, prev: float) -> float:
    """Absolute fractional change (either direction). A move away from zero is a
    full change."""
    if prev <= 0:
        return float("inf") if curr > 0 else 0.0
    return abs(curr - prev) / prev


def is_materially_changed(prev: dict, tc) -> bool:
    """True if theme `tc`'s convergence has changed enough since the fingerprint
    `prev` (a brief_finding_state row) to justify resurfacing it in the brief."""
    # New evidence: more distinct contributing signals than last surface…
    if tc.signal_count > int(prev.get("fp_signal_count") or 0):
        return True
    # …or a strictly fresher signal than the one on record.
    prev_latest = _parse_ts(prev.get("fp_latest_signal_at"))
    if tc.latest_signal_at and prev_latest and tc.latest_signal_at > prev_latest:
        return True
    # Revenue at stake moved materially (raw sum, decay-invariant → any direction).
    if _rel_delta(tc.revenue_at_stake_usd, float(prev.get("fp_revenue_at_stake") or 0.0)) >= RESURFACE_DELTA:
        return True
    # The breadth of agreeing source types changed.
    if tc.breadth != int(prev.get("fp_breadth") or 0):
        return True
    # The issue intensified — effective weight rose materially (upward only).
    if _rel_increase(tc.effective_weight, float(prev.get("fp_effective_weight") or 0.0)) >= RESURFACE_DELTA:
        return True
    return False


def suppress_unchanged(convergence: list, states: dict[str, dict]) -> list:
    """Filter a convergence list down to brief-eligible themes: keep every theme
    that was never surfaced before, plus previously-surfaced themes whose issue
    materially changed. Drop previously-surfaced, unchanged themes.

    `states` maps theme_id → brief_finding_state row. Order is preserved."""
    kept = []
    for tc in convergence:
        prev = states.get(tc.theme_id)
        if prev is None or is_materially_changed(prev, tc):
            kept.append(tc)
    return kept
