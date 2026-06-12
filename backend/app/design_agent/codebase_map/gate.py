"""Gate decision function: turns a ranked LocateResult into one of three outcomes.

Pure function — no LLM, no network, no logging, no side effects.
The endpoint (the route that calls decide_gate) resolves the repo-specific
threshold via threshold_for_repo and passes it in explicitly, keeping this
module stateless and easily testable.
"""
from __future__ import annotations

from typing import Literal, Optional

from pydantic import BaseModel, Field

from app.design_agent.codebase_map.locate import LocateCandidate, LocateResult

GateDecision = Literal["auto_proceed", "proceed_with_note", "ranked_confirm"]

# Starting threshold for the auto-proceed gate. 100% precision on the
# evaluation set. Re-calibrate per repo via _PER_REPO_THRESHOLD entries as
# telemetry accumulates.
_DEFAULT_AUTO_PROCEED_THRESHOLD = 80

# Per-repo overrides. Empty in the initial release — the calibration report
# feeds future entries. No DB; this is an in-code starting value.
_PER_REPO_THRESHOLD: dict[str, int] = {}


def threshold_for_repo(repo: str) -> int:
    """Per-repo auto-proceed threshold.

    Returns the per-repo override when one is registered, else the default.
    The override map is empty in the initial release; telemetry and the
    calibration report feed future entries (no DB; this is an in-code
    starting value, not persisted state).
    """
    return _PER_REPO_THRESHOLD.get(repo, _DEFAULT_AUTO_PROCEED_THRESHOLD)


class GateResult(BaseModel):
    decision: GateDecision
    chosen: list[LocateCandidate] = Field(default_factory=list)
    # auto_proceed / proceed_with_note → the screen(s) generation runs on
    # ranked_confirm                    → [] (the user picks from `ranked`)
    ranked: list[LocateCandidate] = Field(default_factory=list)
    # the full ranked top-3 the picker shows (always populated when non-empty)
    threshold: int  # threshold this decision was made against (for telemetry)
    top_confidence: int = 0  # leading candidate's confidence; 0 when no candidates


def decide_gate(
    result: LocateResult,
    *,
    threshold: Optional[int] = None,
) -> GateResult:
    """Apply the location gate to a LocateResult and return a structured decision.

    Precedence (first match wins):
    1. No candidates                        → ranked_confirm
    2. Leading candidate is ambiguous       → ranked_confirm
    3. is_multi_node + confidence >= t + not ambiguous → proceed_with_note
    4. confidence >= t + not ambiguous + not multi-node → auto_proceed
    5. confidence < t                       → ranked_confirm

    locate.py guarantees candidates are ordered descending by confidence; this
    function does NOT re-sort them — the invariant is asserted below.

    The caller (the endpoint) is responsible for resolving the per-repo
    threshold via threshold_for_repo(repo) and passing it in. When threshold
    is None, the default is used directly (the gate does not look up the repo
    itself — this keeps the function pure).
    """
    t = _DEFAULT_AUTO_PROCEED_THRESHOLD if threshold is None else threshold

    if not result.candidates:
        return GateResult(
            decision="ranked_confirm",
            chosen=[],
            ranked=[],
            threshold=t,
            top_confidence=0,
        )

    # The locate service guarantees descending-by-confidence order; the gate
    # relies on this invariant and must not re-sort.
    leading = result.candidates[0]
    top_confidence = leading.confidence

    if leading.ambiguous:
        # The forced-abstention flag is authoritative; a candidate the model
        # itself flagged ambiguous never auto-proceeds even at a high numeric
        # confidence.
        return GateResult(
            decision="ranked_confirm",
            chosen=[],
            ranked=list(result.candidates),
            threshold=t,
            top_confidence=top_confidence,
        )

    if result.is_multi_node and leading.confidence >= t:
        # Superset: the PRD legitimately spans a screen set. Recreating an
        # adjacent screen is a cost issue, not a wrongness issue. Proceed with
        # the whole set and surface a note to the user.
        return GateResult(
            decision="proceed_with_note",
            chosen=list(result.candidates),
            ranked=list(result.candidates),
            threshold=t,
            top_confidence=top_confidence,
        )

    if leading.confidence >= t:
        # Threshold is inclusive: confidence == threshold auto-proceeds (>=).
        # Single-node case only — multi-node was handled above.
        return GateResult(
            decision="auto_proceed",
            chosen=[leading],
            ranked=list(result.candidates),
            threshold=t,
            top_confidence=top_confidence,
        )

    # Below threshold.
    return GateResult(
        decision="ranked_confirm",
        chosen=[],
        ranked=list(result.candidates),
        threshold=t,
        top_confidence=top_confidence,
    )
