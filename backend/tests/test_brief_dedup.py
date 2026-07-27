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

# ── Phase 2A: classify_candidates (ledger semantics) ─────────────────────────

from app.synthesis.dedup import ROTATION_LIMIT, classify_candidates


def _classify(cands, states):
    return classify_candidates(cands, states, now=NOW)


def test_classify_new_theme_is_new_and_eligible():
    eligible, freshness, backlog = _classify([_tc("t-new")], {})
    assert [t.theme_id for t in eligible] == ["t-new"]
    assert freshness == {"t-new": "new"}
    assert backlog == []


def test_classify_changed_theme_is_updated():
    eligible, freshness, backlog = _classify(
        [_tc("t1", revenue=200000)], {"t1": _fp(revenue=100000)})
    assert freshness == {"t1": "updated"}
    assert backlog == []


def test_classify_unchanged_theme_is_carried_to_backlog():
    eligible, freshness, backlog = _classify([_tc("t1")], {"t1": _fp()})
    assert eligible == []
    assert backlog == [("t1", "carried")]


def test_classify_dismissed_stays_out_unless_changed():
    dismissed_row = {**_fp(), "action": "dismissed"}
    eligible, _f, backlog = _classify([_tc("t1")], {"t1": dismissed_row})
    assert eligible == [] and backlog == [("t1", "dismissed")]
    # Materially worse → resurfaces as updated ("flagged again, now worse").
    eligible, freshness, backlog = _classify(
        [_tc("t1", revenue=250000)], {"t1": dismissed_row})
    assert [t.theme_id for t in eligible] == ["t1"]
    assert freshness["t1"] == "updated"
    assert backlog == []


def test_classify_active_deferral_suppresses_even_when_changed():
    row = {**_fp(), "action": "deferred",
           "deferred_until": (NOW + timedelta(days=3)).isoformat()}
    eligible, _f, backlog = _classify([_tc("t1", revenue=999999)], {"t1": row})
    assert eligible == [] and backlog == [("t1", "deferred")]


def test_classify_expired_deferral_returns_at_full_rank_even_unchanged():
    row = {**_fp(), "action": "deferred",
           "deferred_until": (NOW - timedelta(days=1)).isoformat()}
    eligible, freshness, backlog = _classify([_tc("t1")], {"t1": row})
    assert [t.theme_id for t in eligible] == ["t1"]
    assert freshness["t1"] == "updated"  # framed as a return, not news
    assert backlog == []


def test_classify_acted_on_theme_vacates_slot_even_when_changed():
    for action in ("prd_created", "done"):
        row = {**_fp(), "action": action}
        eligible, _f, backlog = _classify([_tc("t1", revenue=999999)], {"t1": row})
        assert eligible == [], action
        assert backlog == [("t1", "in_progress")], action


def test_classify_rotation_exhaustion_retires_no_action_theme():
    # Shown ROTATION_LIMIT times with no action → retired even though changed.
    row = {**_fp(revenue=100000), "action": "surfaced",
           "times_shown": ROTATION_LIMIT}
    eligible, _f, backlog = _classify([_tc("t1", revenue=500000)], {"t1": row})
    assert eligible == [] and backlog == [("t1", "rotation_exhausted")]
    # One show below the limit → still eligible.
    row2 = {**row, "times_shown": ROTATION_LIMIT - 1}
    eligible, freshness, backlog = _classify([_tc("t1", revenue=500000)], {"t1": row2})
    assert [t.theme_id for t in eligible] == ["t1"]
    assert freshness["t1"] == "updated"


def test_classify_deferral_does_not_count_toward_rotation():
    # A deferred theme at the rotation limit still returns after expiry —
    # deferral is "interested, wrong moment", never a strike.
    row = {**_fp(), "action": "deferred", "times_shown": ROTATION_LIMIT,
           "deferred_until": (NOW - timedelta(days=1)).isoformat()}
    eligible, _f, backlog = _classify([_tc("t1")], {"t1": row})
    assert [t.theme_id for t in eligible] == ["t1"]
    assert backlog == []


# ── Sibling suppression (evidence overlap) ───────────────────────────────────

from app.synthesis.dedup import is_evidence_sibling


def _tc_ev(theme_id, signal_ids, **kw):
    tc = _tc(theme_id, **kw)
    tc.evidence = [
        {"signal_id": sid, "source_type": "customer_voice", "kind": "s",
         "content": "x"}
        for sid in signal_ids
    ]
    return tc


def test_evidence_sibling_requires_shared_and_overlap():
    a = _tc_ev("a", ["s1", "s2", "s3"])
    assert is_evidence_sibling(a, _tc_ev("b", ["s2", "s3", "s4"])) is True
    # Only one shared signal → not a sibling (min-shared gate).
    assert is_evidence_sibling(a, _tc_ev("c", ["s3", "s9", "s8", "s7"])) is False
    # Two shared but a small fraction of the smaller set → not a sibling.
    e = _tc_ev("e", ["s1", "s2", "x1", "x2", "x3", "x4"])
    f = _tc_ev("f", ["s1", "s2", "y1", "y2", "y3", "y4"])
    assert is_evidence_sibling(e, f) is False
    # No evidence on either side → never a sibling.
    assert is_evidence_sibling(_tc("g"), a) is False


def test_classify_sibling_of_deferred_theme_is_held_back():
    # t-deferred: user deferred it, window open. t-new: NEVER surfaced, but it
    # rests on the same signals — the same issue wearing a fresh theme_id.
    deferred_row = {**_fp(), "action": "deferred",
                    "deferred_until": (NOW + timedelta(days=3)).isoformat()}
    t_deferred = _tc_ev("t-deferred", ["s1", "s2", "s3"])
    t_sibling = _tc_ev("t-sibling", ["s2", "s3", "s4"])
    t_distinct = _tc_ev("t-distinct", ["z1", "z2"])
    eligible, freshness, backlog = _classify(
        [t_deferred, t_sibling, t_distinct], {"t-deferred": deferred_row})
    assert [t.theme_id for t in eligible] == ["t-distinct"]
    assert "t-sibling" not in freshness
    assert ("t-deferred", "deferred") in backlog
    assert ("t-sibling", "sibling_deferred") in backlog


def test_classify_sibling_of_dismissed_theme_is_held_back():
    dismissed_row = {**_fp(), "action": "dismissed"}
    t_dismissed = _tc_ev("t-dismissed", ["s1", "s2", "s3"])
    t_sibling = _tc_ev("t-sibling", ["s1", "s2"])
    eligible, _f, backlog = _classify(
        [t_dismissed, t_sibling], {"t-dismissed": dismissed_row})
    assert eligible == []
    assert ("t-sibling", "sibling_dismissed") in backlog


def test_classify_sibling_of_carried_theme_still_cards():
    # 'carried' is the system's hold-back, not the reader's — it must NOT
    # propagate to siblings (a new theme over the same evidence IS the news).
    t_carried = _tc_ev("t-carried", ["s1", "s2", "s3"])
    t_new = _tc_ev("t-new", ["s2", "s3"])
    eligible, freshness, backlog = _classify(
        [t_carried, t_new], {"t-carried": _fp()})
    assert [t.theme_id for t in eligible] == ["t-new"]
    assert freshness["t-new"] == "new"
    assert ("t-carried", "carried") in backlog
