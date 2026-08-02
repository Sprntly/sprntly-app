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
candidacy; they still flow to the ideation pool via the normal sequencer.
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

    `states` maps theme_id → brief_finding_state row. Order is preserved.
    Thin compat wrapper over classify_candidates (which also yields freshness
    states and backlog reasons — the phase-2A ledger semantics)."""
    eligible, _freshness, _backlog = classify_candidates(convergence, states)
    return eligible


# ── Phase 2A: full ledger classification ─────────────────────────────────────
#
# skills/top-insights/SKILL.md step 5 (freshness states) + step 3 (gates),
# adapted to the pipeline's per-theme ledger rows (brief_finding_state):
#
#   new       — never surfaced before → card.
#   updated   — surfaced before AND materially changed → card, framed as the
#               change (the compose prompt receives the previous fingerprint).
#   carried   — surfaced before, unchanged → NOT shown (backlog, reason
#               'carried'); the ideation pool still receives it separately.
#   dismissed — stays out unless materially changed (then it re-cards as
#               'updated' — "flagged again, now worse").
#   deferred  — stays out until `deferred_until`, then re-enters at full rank
#               even if unchanged ("interested, wrong moment" must come back —
#               that is the difference from dismissed).
#   in_progress / done (action prd_created|done) — the reader acted; the slot
#               is vacated and the theme stays out (resolved work returns via
#               the celebrate feed when that adapter lands, not as a re-card).
#   rotation-exhausted — times_shown ≥ ROTATION_LIMIT with no user action →
#               retired to the backlog even when still changing. Nagging is
#               not persistence.

ROTATION_LIMIT = 3  # cards shown with no action before a theme is retired

#: freshness value for a theme re-entering after its deferral expired without
#: a material change — compose frames it as a return, not news.
_DEFER_RETURN = "updated"

# ── Sibling suppression ──────────────────────────────────────────────────────
# Convergence can mint DISTINCT theme_ids over the same underlying issue (e.g.
# "Dashboard / build board latency" and "Dashboard load"), so a user's deferral
# or dismissal keyed on one theme_id would not stop a sibling theme from
# carding the very topic they said no to (observed live on staging
# 2026-07-27). The discriminator is the EVIDENCE: sibling themes converge on
# overlapping KG signals. A candidate sharing enough of its evidence signals
# with a user-held theme is held back with it — with its own backlog reason,
# so the transparency line stays honest ("held with a deferred finding").
#
# Thresholds: at least SIBLING_MIN_SHARED common signals AND the overlap
# covering at least SIBLING_OVERLAP of the smaller evidence set. Both gates so
# a single coincidentally-shared signal between two evidence-rich themes does
# not couple them.
SIBLING_MIN_SHARED = 2
SIBLING_OVERLAP = 0.5


def _evidence_signal_ids(tc) -> set:
    """The candidate's contributing KG signal ids (empty set when unknown)."""
    out = set()
    for ev in getattr(tc, "evidence", None) or []:
        sid = ev.get("signal_id") if isinstance(ev, dict) else None
        if sid:
            out.add(sid)
    return out


def is_evidence_sibling(a, b) -> bool:
    """True when candidates `a` and `b` rest on substantially the same
    evidence — the same underlying issue wearing two theme_ids."""
    ev_a, ev_b = _evidence_signal_ids(a), _evidence_signal_ids(b)
    if not ev_a or not ev_b:
        return False
    shared = len(ev_a & ev_b)
    if shared < SIBLING_MIN_SHARED:
        return False
    return shared / min(len(ev_a), len(ev_b)) >= SIBLING_OVERLAP


def _deferral_active(prev: dict, now: datetime | None = None) -> bool:
    until = _parse_ts(prev.get("deferred_until"))
    if until is None:
        return False
    now = now or datetime.now(until.tzinfo)
    return now < until


def classify_candidates(
    convergence: list,
    states: dict[str, dict],
    now: datetime | None = None,
) -> tuple[list, dict[str, str], list[tuple[str, str]]]:
    """Classify ranked candidates against the ledger.

    Returns (eligible, freshness, backlog):
      eligible  — candidates that may card this run, order preserved;
      freshness — theme_id → 'new' | 'updated' for every eligible candidate;
      backlog   — (theme_id, reason) for every candidate held back, reason ∈
                  {'carried', 'dismissed', 'deferred', 'in_progress',
                   'rotation_exhausted', 'sibling_deferred',
                   'sibling_dismissed'}. Emitted onto the brief payload so
                  "what am I not seeing" has a real answer.
    """
    eligible: list = []
    freshness: dict[str, str] = {}
    backlog: list[tuple[str, str]] = []
    # Themes held back by an ACTIVE user decision this run — deferred (window
    # open) or dismissed (unchanged). Eligible candidates resting on the same
    # evidence are held with them below; carried/rotation hold-backs do NOT
    # propagate (they are the system's call, not the reader's).
    user_held: list = []
    for tc in convergence:
        prev = states.get(tc.theme_id)
        if prev is None:
            eligible.append(tc)
            freshness[tc.theme_id] = "new"
            continue
        action = (prev.get("action") or "surfaced").strip()
        changed = is_materially_changed(prev, tc)
        if action in ("prd_created", "done"):
            backlog.append((tc.theme_id, "in_progress"))
            continue
        if action == "deferred":
            if _deferral_active(prev, now):
                backlog.append((tc.theme_id, "deferred"))
                user_held.append((tc, "deferred"))
            else:
                # Expired deferral: back at full rank even if unchanged.
                eligible.append(tc)
                freshness[tc.theme_id] = "updated" if changed else _DEFER_RETURN
            continue
        if action == "dismissed":
            if changed:
                eligible.append(tc)
                freshness[tc.theme_id] = "updated"
            else:
                backlog.append((tc.theme_id, "dismissed"))
                user_held.append((tc, "dismissed"))
            continue
        # action == 'surfaced' (no user action yet)
        if int(prev.get("times_shown") or 0) >= ROTATION_LIMIT:
            backlog.append((tc.theme_id, "rotation_exhausted"))
            continue
        if changed:
            eligible.append(tc)
            freshness[tc.theme_id] = "updated"
        else:
            backlog.append((tc.theme_id, "carried"))

    # Sibling pass: an eligible candidate resting on substantially the same
    # evidence as an actively-deferred/dismissed theme is held back with it
    # (reason 'sibling_deferred' / 'sibling_dismissed'). The user said no to
    # this issue; a fresh theme_id over the same signals is the same issue.
    if user_held:
        kept: list = []
        for tc in eligible:
            held_as = next(
                (reason for held_tc, reason in user_held
                 if is_evidence_sibling(tc, held_tc)),
                None,
            )
            if held_as is None:
                kept.append(tc)
            else:
                freshness.pop(tc.theme_id, None)
                backlog.append((tc.theme_id, f"sibling_{held_as}"))
        eligible = kept
    return eligible, freshness, backlog
