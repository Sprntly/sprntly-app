"""Unit tests for brief de-dup logic (synthesis/dedup.py).

Covers the "don't resurface unless the issue changed" decision: new evidence OR
a ≥20% metric move resurfaces; pure recency decay does NOT.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

from app.synthesis.convergence import ThemeConvergence
from app.synthesis.dedup import is_materially_changed, suppress_unchanged

NOW = datetime(2026, 6, 16, tzinfo=timezone.utc)


def _tc(theme_id="t1", *, signals=3, weight=2.0, revenue=100000.0,
        sources=("revenue", "customer_voice"), latest=NOW):
    return ThemeConvergence(
        theme_id=theme_id, theme_label=theme_id,
        signal_count=signals, source_types=set(sources),
        effective_weight=weight, revenue_at_stake_usd=revenue,
        latest_signal_at=latest,
    )


def _fp(*, signals=3, weight=2.0, revenue=100000.0, breadth=2, latest=NOW):
    return {
        "fp_signal_count": signals, "fp_effective_weight": weight,
        "fp_revenue_at_stake": revenue, "fp_breadth": breadth,
        "fp_latest_signal_at": latest.isoformat(),
    }


def test_identical_fingerprint_is_not_changed():
    assert is_materially_changed(_fp(), _tc()) is False


def test_more_signals_is_changed():
    assert is_materially_changed(_fp(signals=3), _tc(signals=4)) is True


def test_fresher_signal_is_changed():
    prev = _fp(latest=NOW - timedelta(days=7))
    assert is_materially_changed(prev, _tc(latest=NOW)) is True


def test_revenue_up_20pct_is_changed():
    assert is_materially_changed(_fp(revenue=100000), _tc(revenue=120000)) is True


def test_revenue_down_20pct_is_changed():
    # Revenue is decay-invariant, so a real drop counts (issue shrank).
    assert is_materially_changed(_fp(revenue=100000), _tc(revenue=80000)) is True


def test_revenue_small_move_is_not_changed():
    assert is_materially_changed(_fp(revenue=100000), _tc(revenue=110000)) is False


def test_breadth_change_is_changed():
    prev = _fp(breadth=2)
    assert is_materially_changed(prev, _tc(sources=("revenue", "customer_voice", "project_mgmt"))) is True


def test_effective_weight_rise_is_changed():
    assert is_materially_changed(_fp(weight=2.0), _tc(weight=2.5)) is True


def test_effective_weight_decay_drop_is_not_changed():
    # The KEY case: effective_weight decays with time. A drop with no new
    # evidence / revenue move must NOT resurface an untouched issue.
    assert is_materially_changed(_fp(weight=2.0), _tc(weight=1.5)) is False


def test_suppress_keeps_never_surfaced_and_changed_drops_unchanged():
    never = _tc("new-theme")
    changed = _tc("worse-theme", revenue=200000)
    unchanged = _tc("stale-theme")
    states = {
        "worse-theme": _fp(revenue=100000),   # revenue doubled → changed
        "stale-theme": _fp(),                  # identical → unchanged
        # "new-theme" has no state → never surfaced
    }
    kept = suppress_unchanged([never, changed, unchanged], states)
    ids = [t.theme_id for t in kept]
    assert ids == ["new-theme", "worse-theme"]  # unchanged dropped, order preserved
