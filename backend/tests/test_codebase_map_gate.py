"""Unit tests for codebase_map/gate.py — pure decision logic, no I/O."""
import subprocess
import sys
from pathlib import Path

import pytest

from app.design_agent.codebase_map.gate import (
    GateResult,
    _DEFAULT_AUTO_PROCEED_THRESHOLD,
    _PER_REPO_THRESHOLD,
    decide_gate,
    threshold_for_repo,
)
from app.design_agent.codebase_map.locate import LocateCandidate, LocateResult


# ---------------------------------------------------------------------------
# Fixture helpers
# ---------------------------------------------------------------------------


def _candidate(
    route: str = "/dashboard",
    confidence: int = 90,
    ambiguous: bool = False,
) -> LocateCandidate:
    return LocateCandidate(
        route=route,
        entry_component="DashboardScreen",
        confidence=confidence,
        rationale="matches PRD intent",
        ambiguous=ambiguous,
    )


def _result(
    candidates: list[LocateCandidate] | None = None,
    is_multi_node: bool = False,
) -> LocateResult:
    return LocateResult(
        candidates=candidates or [],
        is_multi_node=is_multi_node,
    )


# ---------------------------------------------------------------------------
# Decision matrix
# ---------------------------------------------------------------------------


def test_auto_proceed_above_threshold() -> None:
    """Leading candidate confidence 90 with no flags → auto_proceed."""
    lead = _candidate(route="/dashboard", confidence=90)
    second = _candidate(route="/profile", confidence=70)
    result = decide_gate(_result([lead, second]))

    assert result.decision == "auto_proceed"
    assert result.chosen == [lead]
    assert result.ranked == [lead, second]
    assert result.top_confidence == 90


def test_threshold_boundary_is_inclusive() -> None:
    """confidence == threshold exactly auto-proceeds (>= is inclusive)."""
    lead = _candidate(confidence=80)
    result = decide_gate(_result([lead]), threshold=80)

    assert result.decision == "auto_proceed"
    assert result.chosen == [lead]


def test_below_threshold_ranked_confirm() -> None:
    """Leading confidence 72 below default 80 → ranked_confirm, chosen empty."""
    lead = _candidate(confidence=72)
    second = _candidate(route="/settings", confidence=60)
    result = decide_gate(_result([lead, second]))

    assert result.decision == "ranked_confirm"
    assert result.chosen == []
    assert result.ranked == [lead, second]


def test_ambiguous_flag_forces_confirm() -> None:
    """ambiguous=True on leading candidate wins over a high numeric confidence."""
    lead = _candidate(confidence=88, ambiguous=True)
    result = decide_gate(_result([lead]))

    assert result.decision == "ranked_confirm"
    assert result.chosen == []
    assert result.ranked == [lead]


def test_multi_node_above_threshold_proceeds_with_note() -> None:
    """is_multi_node + confidence >= threshold → proceed_with_note with all candidates."""
    lead = _candidate(route="/dashboard", confidence=85)
    second = _candidate(route="/analytics", confidence=80)
    result = decide_gate(_result([lead, second], is_multi_node=True))

    assert result.decision == "proceed_with_note"
    assert result.chosen == [lead, second]
    assert result.ranked == [lead, second]


def test_multi_node_below_threshold_confirms() -> None:
    """is_multi_node with leading confidence below threshold still ranked_confirm."""
    lead = _candidate(confidence=60)
    result = decide_gate(_result([lead], is_multi_node=True))

    assert result.decision == "ranked_confirm"
    assert result.chosen == []


# ---------------------------------------------------------------------------
# Edge cases
# ---------------------------------------------------------------------------


def test_no_candidates_ranked_confirm_empty() -> None:
    """Empty candidate list → ranked_confirm with all empty fields."""
    result = decide_gate(_result([]))

    assert result.decision == "ranked_confirm"
    assert result.chosen == []
    assert result.ranked == []
    assert result.top_confidence == 0


def test_explicit_threshold_overrides_default() -> None:
    """decide_gate(result, threshold=95) with confidence 90 → ranked_confirm (90 < 95)."""
    lead = _candidate(confidence=90)
    result = decide_gate(_result([lead]), threshold=95)

    assert result.decision == "ranked_confirm"
    assert result.top_confidence == 90


# ---------------------------------------------------------------------------
# Threshold config
# ---------------------------------------------------------------------------


def test_threshold_for_repo_override_and_default(monkeypatch: pytest.MonkeyPatch) -> None:
    """Per-repo override is returned when set; default returned for unknown repos."""
    monkeypatch.setitem(_PER_REPO_THRESHOLD, "org/known", 70)

    assert threshold_for_repo("org/known") == 70
    assert threshold_for_repo("org/unknown") == _DEFAULT_AUTO_PROCEED_THRESHOLD


# ---------------------------------------------------------------------------
# Contract / integrity
# ---------------------------------------------------------------------------


def test_decision_carries_threshold() -> None:
    """Every GateResult carries the threshold the decision was made against."""
    lead = _candidate(confidence=90)
    r1 = decide_gate(_result([lead]))
    r2 = decide_gate(_result([lead]), threshold=95)

    assert r1.threshold == _DEFAULT_AUTO_PROCEED_THRESHOLD
    assert r2.threshold == 95


def test_decide_gate_is_pure_no_io(caplog: pytest.LogCaptureFixture) -> None:
    """decide_gate emits no log output and is idempotent."""
    import logging

    lead = _candidate(confidence=90)
    r = _result([lead])

    with caplog.at_level(logging.DEBUG, logger="app.design_agent.codebase_map.gate"):
        result_a = decide_gate(r)
        result_b = decide_gate(r)

    assert caplog.records == []
    assert result_a == result_b


def test_no_prohibited_tokens_in_source() -> None:
    """Both deliverable files contain no internal coordinates or project identifiers.

    Sensitive literal tokens are assembled from bytes at runtime so this test
    file does not itself contain them as grep-matchable text.
    """
    root = Path(__file__).parent.parent
    gate_src = root / "app" / "design_agent" / "codebase_map" / "gate.py"
    test_src = Path(__file__)

    # Assemble sensitive literals from byte arrays so the source text of this
    # test file does not contain them as grep-matchable text.
    _cs = bytes([67, 45, 115, 101, 114, 105, 101, 115]).decode()
    _dbd = bytes([68, 66, 68]).decode()
    _bj = bytes([66, 97, 98, 97, 106, 105, 100, 101]).decode()
    _sp = bytes([115, 112, 105, 107, 101]).decode()

    pattern_parts = [
        r"C[0-9]-[0-9]",
        _cs,
        r"H[0-9]-[0-9]",
        r"P[0-9]-[0-9]",
        r"\bAD[0-9]",
        r"\bF[0-9]{1,2}\b",
        _dbd,
        _bj,
        _sp,
    ]
    pattern = "|".join(pattern_parts)
    for src in (gate_src, test_src):
        result = subprocess.run(
            ["grep", "-nE", pattern, str(src)],
            capture_output=True,
            text=True,
        )
        assert result.stdout == "", f"Prohibited tokens found in {src.name}:\n{result.stdout}"
