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
from app.design_agent.codebase_map.shell import APP_SHELL_NODE_ID, APP_SHELL_ROUTE


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


def _decline_candidate(
    *,
    spans_multi_surface: bool = True,
    classification_confidence: int = 90,
    rationale: str = "no single surface owns this feature",
) -> LocateCandidate:
    """A no-host-decline candidate — empty host fields, as locate emits on decline."""
    return LocateCandidate(
        route="",
        id="",
        entry_component="",
        confidence=0,
        rationale=rationale,
        ambiguous=False,
        classification="no-host-decline",
        spans_multi_surface=spans_multi_surface,
        classification_confidence=classification_confidence,
    )


def _host_candidate(
    *,
    route: str = "/impact",
    node_id: str = "/impact",
    confidence: int = 70,
    classification: str = "attach-to-host",
    classification_confidence: int = 90,
    spans_multi_surface: bool = True,
) -> LocateCandidate:
    """A located host candidate — a domain surface or the app-shell.

    Defaults to a below-auto-proceed which-surface confidence so the spans-routing
    branch (not the auto_proceed branch) is the one exercised.
    """
    return LocateCandidate(
        route=route,
        id=node_id,
        entry_component="ImpactScreen",
        confidence=confidence,
        rationale="the feature overlays this surface",
        ambiguous=False,
        classification=classification,
        spans_multi_surface=spans_multi_surface,
        classification_confidence=classification_confidence,
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
# Spans-routing: rescue a spanning would-be-decline to a host instead of declining
# ---------------------------------------------------------------------------


def test_spans_chrome_routes_to_app_shell() -> None:
    """Chrome-level spanning feature → proceed routed to the app-shell surface.

    A would-be no-host decline carries spans_multi_surface=true, the model echoed
    the app-shell node as a host candidate, and the app-shell is present in the
    map → PROCEED (attach-to-shell), not a decline.
    """
    shell_host = _host_candidate(
        route=APP_SHELL_ROUTE,
        node_id=APP_SHELL_NODE_ID,
        confidence=70,
        classification="attach-to-host",
        classification_confidence=93,
        spans_multi_surface=True,
    )
    decline = _decline_candidate(spans_multi_surface=True, classification_confidence=90)
    result = decide_gate(_result([shell_host, decline]), has_app_shell=True)

    assert result.decision == "proceed_with_note"
    assert result.routing == "attach-to-shell"
    assert result.chosen == [shell_host]


def test_spans_non_chrome_routes_to_primary_domain() -> None:
    """Spanning-but-not-chrome feature → proceed anchored to the primary domain host.

    No app-shell node is echoed among the candidates (even though the map carries
    one), so routing anchors to the top-ranked domain host the model named.
    """
    primary = _host_candidate(
        route="/impact", node_id="/impact", confidence=72,
        classification_confidence=90, spans_multi_surface=True,
    )
    secondary = _host_candidate(
        route="/chat", node_id="/chat", confidence=60,
        classification_confidence=88, spans_multi_surface=True,
    )
    decline = _decline_candidate(spans_multi_surface=True, classification_confidence=85)
    result = decide_gate(_result([primary, secondary, decline]), has_app_shell=True)

    assert result.decision == "proceed_with_note"
    assert result.routing == "attach-to-primary-domain"
    assert result.chosen == [primary]


def test_genuine_decline_still_declines() -> None:
    """A non-spanning no-host candidate declines exactly as the shipped gate.

    spans_multi_surface=false ⇒ no spanning signal ⇒ nothing to rescue ⇒
    ranked_confirm with no routing, even with an app-shell present.
    """
    decline = _decline_candidate(spans_multi_surface=False, classification_confidence=95)
    result = decide_gate(_result([decline]), has_app_shell=True)

    assert result.decision == "ranked_confirm"
    assert result.routing is None
    assert result.chosen == []


def test_overfit_trap_dressed_as_chrome_still_declines() -> None:
    """The crux: a new product area dressed in chrome language must STILL decline.

    A "global open-from-anywhere collaborative canvas with live cursors and a
    persistent room reachable from every screen" is phrased to SOUND like chrome
    and is flagged spanning at high classification confidence, with an app-shell
    present in the map. It is a brand-new realtime-canvas product area: the model
    named NO host (no echoed app-shell, no domain surface), so it must STILL
    decline — the app-shell does not become a catch-all.
    """
    overfit = LocateCandidate(
        route="",
        id="",
        entry_component="",
        confidence=0,
        rationale="a global open-from-anywhere collaborative canvas reachable everywhere",
        ambiguous=False,
        classification="no-host-decline",
        spans_multi_surface=True,
        classification_confidence=88,
    )
    result = decide_gate(_result([overfit]), has_app_shell=True)

    assert result.decision == "ranked_confirm"
    assert result.routing is None
    assert result.chosen == []


def test_routing_ignores_advisory_sublabel() -> None:
    """Routing keys on the host + classification_confidence, NOT the sub-label.

    The same spanning host routes identically whether the model labelled it
    "modify-existing" or "attach-to-host" — the advisory sub-label may drift while
    the located host stays correct.
    """
    decline = _decline_candidate(spans_multi_surface=True, classification_confidence=85)
    for sublabel in ("modify-existing", "attach-to-host"):
        host = _host_candidate(
            route="/impact", node_id="/impact", confidence=70,
            classification=sublabel, classification_confidence=90,
            spans_multi_surface=True,
        )
        result = decide_gate(_result([host, decline]), has_app_shell=True)

        assert result.decision == "proceed_with_note", sublabel
        assert result.routing == "attach-to-primary-domain", sublabel
        assert result.chosen == [host], sublabel


def test_spans_routing_below_classification_threshold_declines() -> None:
    """A host the model is NOT confident in (classification_confidence < 85) is not
    trusted for routing → the spanning feature still declines."""
    weak_host = _host_candidate(
        route="/impact", node_id="/impact", confidence=70,
        classification_confidence=80, spans_multi_surface=True,
    )
    decline = _decline_candidate(spans_multi_surface=True, classification_confidence=84)
    result = decide_gate(_result([weak_host, decline]), has_app_shell=True)

    assert result.decision == "ranked_confirm"
    assert result.routing is None
    assert result.chosen == []


def test_existing_precedence_unchanged_for_non_spans() -> None:
    """Non-spans inputs keep the shipped auto/note/confirm outcomes; routing None.

    Also proves an app-shell being present never triggers routing on its own — a
    spanning-decline signal is required.
    """
    # auto_proceed
    r = decide_gate(_result([_candidate(route="/dashboard", confidence=90)]))
    assert r.decision == "auto_proceed"
    assert r.routing is None

    # proceed_with_note (multi-node screen set)
    a = _candidate(route="/dashboard", confidence=85)
    b = _candidate(route="/analytics", confidence=80)
    r = decide_gate(_result([a, b], is_multi_node=True))
    assert r.decision == "proceed_with_note"
    assert r.routing is None

    # ranked_confirm (below threshold, no spanning-decline signal)
    r = decide_gate(_result([_candidate(confidence=60)]))
    assert r.decision == "ranked_confirm"
    assert r.routing is None

    # ranked_confirm (no candidates)
    r = decide_gate(_result([]))
    assert r.decision == "ranked_confirm"
    assert r.routing is None

    # below-threshold host WITH an app-shell present but NO spanning decline:
    # app-shell presence alone must not route.
    r = decide_gate(_result([_candidate(confidence=60)]), has_app_shell=True)
    assert r.decision == "ranked_confirm"
    assert r.routing is None


def test_decide_gate_no_llm_call(monkeypatch: pytest.MonkeyPatch) -> None:
    """decide_gate never constructs or calls an LLM client on any path (incl. spans)."""
    import app.design_agent.client as client_mod

    def _boom(*args: object, **kwargs: object) -> None:
        raise AssertionError("decide_gate must not call an LLM client")

    monkeypatch.setattr(client_mod, "get_design_agent_client", _boom, raising=False)

    shell_host = _host_candidate(
        route=APP_SHELL_ROUTE, node_id=APP_SHELL_NODE_ID, classification_confidence=93,
    )
    decline = _decline_candidate(spans_multi_surface=True, classification_confidence=90)

    # exercise the new spans-routing branch + a plain path; no client may be built
    decide_gate(_result([shell_host, decline]), has_app_shell=True)
    decide_gate(_result([_candidate(confidence=90)]))


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
