"""Unit tests for the codebase_map/locate.py LLM service.

All tests use a FakeClient — no real network calls. Tests cover the happy
path, schema normalization, error handling, LLM surface invariants (cache
placement, cost-summary log), and prompt-content properties.
"""
from __future__ import annotations

import json
import logging
import re
import subprocess
import sys
from pathlib import Path
from unittest.mock import patch

import pytest

from app.design_agent.codebase_map.locate import (
    LocateCandidate,
    LocateResult,
    _COMPACT_MAP_CHAR_CAP,
    _MAX_RATIONALE_CHARS,
    _MODEL,
    compact_map,
    locate_screen,
)
from app.design_agent.codebase_map.types import (
    MapResult,
    NavItem,
    ScreenNode,
    ShellModel,
)
from app.design_agent.prompts import DESIGN_AGENT_TEMPLATE_VERSION, LOCATE_SYSTEM


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_usage(
    input_tokens: int = 100,
    output_tokens: int = 50,
    cache_read_input_tokens: int = 0,
    cache_creation_input_tokens: int = 0,
):
    """Return a lightweight usage stand-in with the fields RunUsage.add reads."""

    class _Usage:
        pass

    u = _Usage()
    u.input_tokens = input_tokens
    u.output_tokens = output_tokens
    u.cache_read_input_tokens = cache_read_input_tokens
    u.cache_creation_input_tokens = cache_creation_input_tokens
    return u


def _make_response(payload: dict | str, usage=None):
    """Return an object matching the Anthropic response shape."""

    class _Content:
        def __init__(self, text):
            self.text = text

    class _Resp:
        pass

    resp = _Resp()
    resp.content = [_Content(payload if isinstance(payload, str) else json.dumps(payload))]
    resp.usage = usage if usage is not None else _make_usage()
    return resp


class FakeClient:
    """Records every call to messages.create and returns canned responses."""

    def __init__(self, responses):
        self._responses = responses if isinstance(responses, list) else [responses]
        self._call_index = 0
        self.calls: list[dict] = []

        outer = self

        class _Messages:
            def create(self, **kwargs):
                outer.calls.append(kwargs)
                resp = outer._responses[outer._call_index]
                outer._call_index += 1
                return resp

        self.messages = _Messages()


def _map_with_nodes(*routes: str) -> MapResult:
    """Build a MapResult whose nodes cover the given routes."""
    nodes = [
        ScreenNode(
            route=r,
            entry_component=r.lstrip("/").replace("/", "_").capitalize() + "Screen",
            composed_components=["CompA", "CompB"],
        )
        for r in routes
    ]
    shell = ShellModel(
        brand="Acme",
        nav_items=[NavItem(label=r.lstrip("/").capitalize(), route=r) for r in routes[:3]],
    )
    return MapResult(
        repo="org/repo",
        commit_sha="abc123",
        posture="CLEAN",
        nodes=nodes,
        shell=shell,
    )


def _happy_payload(route: str = "/team", confidence: int = 92) -> dict:
    return {
        "candidates": [
            {
                "route": route,
                "entry_component": "TeamScreen",
                "confidence": confidence,
                "rationale": "The PRD describes team management.",
                "ambiguous": False,
            }
        ],
        "is_multi_node": False,
    }


# ---------------------------------------------------------------------------
# Creation / happy-path
# ---------------------------------------------------------------------------


def test_happy_locate_returns_ranked_candidate():
    """A well-formed response with a real route → first candidate matches."""
    m = _map_with_nodes("/team", "/settings/members", "/admin")
    fake = FakeClient([_make_response(_happy_payload())])

    result = locate_screen("team management PRD", m, client=fake)

    assert len(result.candidates) >= 1
    first = result.candidates[0]
    assert first.route == "/team"
    assert first.confidence == 92
    assert first.ambiguous is False


def test_multi_node_returns_screen_set():
    """A PRD spanning two screens → is_multi_node True and both candidates present."""
    m = _map_with_nodes("/team", "/settings/members", "/admin")
    payload = {
        "candidates": [
            {
                "route": "/team",
                "entry_component": "TeamScreen",
                "confidence": 85,
                "rationale": "Handles team management.",
                "ambiguous": False,
            },
            {
                "route": "/settings/members",
                "entry_component": "MembersSettings",
                "confidence": 80,
                "rationale": "Handles membership settings.",
                "ambiguous": False,
            },
        ],
        "is_multi_node": True,
    }
    fake = FakeClient([_make_response(payload)])

    result = locate_screen("multi-step team and settings PRD", m, client=fake)

    assert result.is_multi_node is True
    routes = {c.route for c in result.candidates}
    assert "/team" in routes
    assert "/settings/members" in routes


def test_json_fence_is_stripped():
    """JSON wrapped in ```json ... ``` fences is parsed correctly."""
    m = _map_with_nodes("/team", "/settings/members", "/admin")
    raw = "```json\n" + json.dumps(_happy_payload()) + "\n```"
    fake = FakeClient([_make_response(raw)])

    result = locate_screen("team management PRD", m, client=fake)

    assert result.candidates[0].route == "/team"
    assert result.candidates[0].confidence == 92


# ---------------------------------------------------------------------------
# Serialization / schema guards
# ---------------------------------------------------------------------------


def test_confidence_is_clamped_to_range():
    """Out-of-range confidence values are always clamped to [0, 100]."""
    m = _map_with_nodes("/team")

    # Case 1: 150 → 100
    payload_over = {
        "candidates": [
            {"route": "/team", "entry_component": "T", "confidence": 150, "rationale": "r", "ambiguous": False}
        ],
        "is_multi_node": False,
    }
    fake = FakeClient([_make_response(payload_over)])
    result = locate_screen("prd", m, client=fake)
    assert result.candidates[0].confidence == 100

    # Case 2: 0.92 (float fraction) → int(0.92) = 0, clamped to [0,100] → 0
    payload_fraction = {
        "candidates": [
            {"route": "/team", "entry_component": "T", "confidence": 0.92, "rationale": "r", "ambiguous": False}
        ],
        "is_multi_node": False,
    }
    fake2 = FakeClient([_make_response(payload_fraction)])
    result2 = locate_screen("prd", m, client=fake2)
    assert 0 <= result2.candidates[0].confidence <= 100


def test_hallucinated_route_is_dropped():
    """A route not in the map is silently dropped from the result."""
    m = _map_with_nodes("/team", "/admin")
    payload = {
        "candidates": [
            {"route": "/nonexistent", "entry_component": "X", "confidence": 90, "rationale": "r", "ambiguous": False},
            {"route": "/team", "entry_component": "TeamScreen", "confidence": 85, "rationale": "r", "ambiguous": False},
        ],
        "is_multi_node": False,
    }
    fake = FakeClient([_make_response(payload)])

    result = locate_screen("prd", m, client=fake)

    routes = {c.route for c in result.candidates}
    assert "/nonexistent" not in routes
    assert "/team" in routes


def test_candidates_truncated_to_three():
    """Five valid candidates in the response are truncated to three."""
    routes = ["/a", "/b", "/c", "/d", "/e"]
    m = _map_with_nodes(*routes)
    payload = {
        "candidates": [
            {"route": r, "entry_component": "X", "confidence": 90 - i, "rationale": "r", "ambiguous": False}
            for i, r in enumerate(routes)
        ],
        "is_multi_node": False,
    }
    fake = FakeClient([_make_response(payload)])

    result = locate_screen("prd", m, client=fake)

    assert len(result.candidates) == 3


def test_ambiguous_flag_is_preserved():
    """A candidate returned with ambiguous=True and confidence=72 is passed through unchanged."""
    m = _map_with_nodes("/team")
    payload = {
        "candidates": [
            {"route": "/team", "entry_component": "T", "confidence": 72, "rationale": "Unclear PRD.", "ambiguous": True}
        ],
        "is_multi_node": False,
    }
    fake = FakeClient([_make_response(payload)])

    result = locate_screen("vague prd", m, client=fake)

    assert result.candidates[0].ambiguous is True
    assert result.candidates[0].confidence == 72


def test_compact_map_has_no_source_and_is_bounded():
    """compact_map output contains structural fields but no file-body content."""
    # Build a map with many nodes to verify the cap.
    nodes = [
        ScreenNode(route=f"/screen{i}", entry_component=f"Screen{i}", composed_components=["A"])
        for i in range(200)
    ]
    shell = ShellModel(
        brand="BigApp",
        nav_items=[NavItem(label="Home", route="/screen0"), NavItem(label="Dash", route="/screen1")],
    )
    m = MapResult(repo="org/big", commit_sha="sha999", posture="CLEAN", nodes=nodes, shell=shell)

    output = compact_map(m)

    # Contains structural fields.
    assert "POSTURE:" in output
    assert "SHELL:" in output
    assert "BigApp" in output
    assert "SCREENS:" in output

    # Does not contain file paths or source bodies.
    assert ".tsx" not in output
    assert ".jsx" not in output
    assert "import " not in output

    # Stays within the char cap.
    assert len(output) <= _COMPACT_MAP_CHAR_CAP


def test_compact_map_carries_id_and_kind_marker():
    """Each projection line leads with the node id; non-route kinds are marked."""
    nodes = [
        ScreenNode(route="/team", entry_component="TeamScreen", composed_components=["A", "B"]),
        ScreenNode(route="/inbox", entry_component="InboxScreen", composed_components=["C"]),
        ScreenNode(route="", entry_component="AppShell", kind="shell", id="app-shell"),
        ScreenNode(
            route="/team",
            entry_component="TeamMembers",
            kind="section",
            id="/team-members",
        ),
    ]
    m = MapResult(repo="org/repo", commit_sha="abc123", posture="CLEAN", nodes=nodes)

    output = compact_map(m)

    # Every node's id is surfaced in its line.
    assert "[/team]" in output
    assert "[/inbox]" in output
    assert "[app-shell]" in output
    assert "[/team-members]" in output

    # Non-route kinds are marked; route nodes carry no kind marker.
    assert "(shell)" in output
    assert "(section)" in output
    team_line = next(ln for ln in output.splitlines() if ln.startswith("- [/team]"))
    assert "(shell)" not in team_line
    assert "(section)" not in team_line

    # Existing per-node content is preserved.
    assert "TeamScreen" in output
    assert "components" in output


def test_compact_map_still_bounded():
    """An oversized map still truncates at the char cap with the id/kind line shape."""
    nodes = [
        ScreenNode(route=f"/screen{i}", entry_component=f"Screen{i}", composed_components=["A"])
        for i in range(400)
    ]
    m = MapResult(repo="org/big", commit_sha="sha999", posture="CLEAN", nodes=nodes)

    output = compact_map(m)

    assert len(output) <= _COMPACT_MAP_CHAR_CAP
    assert output.endswith("...")  # the truncation guard fired


# ---------------------------------------------------------------------------
# Error handling
# ---------------------------------------------------------------------------


def test_malformed_output_returns_empty_never_raises():
    """Non-JSON garbage from the model → LocateResult(candidates=[]) without raising."""
    m = _map_with_nodes("/team")
    fake = FakeClient([_make_response("this is not json at all {{{broken")])

    result = locate_screen("prd", m, client=fake)

    assert isinstance(result, LocateResult)
    assert result.candidates == []


def test_client_factory_used_when_none_injected():
    """When client=None, get_design_agent_client is called exactly once."""
    m = _map_with_nodes("/team")
    canned = _make_response(_happy_payload())
    fake = FakeClient([canned])
    call_count: list[int] = [0]

    def mock_factory():
        call_count[0] += 1
        return fake

    with patch("app.design_agent.client.get_design_agent_client", mock_factory):
        locate_screen("prd", m)  # client not injected

    assert call_count[0] == 1


# ---------------------------------------------------------------------------
# LLM surface (required for LLM-calling tickets)
# ---------------------------------------------------------------------------


def test_cache_control_on_last_system_block_only():
    """Cache breakpoint lands on the last system block; user message has none."""
    m = _map_with_nodes("/team")
    fake = FakeClient([_make_response(_happy_payload())])

    locate_screen("prd", m, client=fake)

    assert fake.calls, "No call was recorded"
    kwargs = fake.calls[0]

    system = kwargs["system"]
    assert isinstance(system, list)
    # Only the last block carries cache_control.
    assert "cache_control" in system[-1]
    assert system[-1]["cache_control"] == {"type": "ephemeral", "ttl": "1h"}
    for block in system[:-1]:
        assert "cache_control" not in block

    # User message has no cache_control.
    messages = kwargs["messages"]
    assert len(messages) == 1
    assert "cache_control" not in messages[0]


def test_cache_verification_second_call_reads_cache(caplog):
    """Second call within the cache window reports non-zero cached_input_tokens in the log."""
    m = _map_with_nodes("/team")
    usage1 = _make_usage(input_tokens=100, cache_read_input_tokens=0)
    usage2 = _make_usage(input_tokens=20, cache_read_input_tokens=80)
    resp1 = _make_response(_happy_payload(), usage1)
    resp2 = _make_response(_happy_payload(), usage2)
    fake = FakeClient([resp1, resp2])

    with caplog.at_level(logging.INFO):
        locate_screen("prd", m, client=fake)
        locate_screen("prd", m, client=fake)

    log_lines = [
        r.getMessage()
        for r in caplog.records
        if "design_agent.locate.complete" in r.getMessage()
    ]
    assert len(log_lines) == 2, f"Expected 2 cost-summary lines, got: {log_lines}"
    assert "cached_input_tokens=80" in log_lines[1]


def test_cost_summary_line_emitted_no_prd_leak(caplog):
    """Exactly one cost-summary line per call, containing required fields but no PRD text."""
    prd_text = "UNIQUE-PRD-CONTENT-XYZ: manage the widget inventory dashboard"
    m = _map_with_nodes("/team")
    fake = FakeClient([_make_response(_happy_payload())])

    with caplog.at_level(logging.INFO):
        locate_screen(prd_text, m, client=fake)

    lines = [
        r.getMessage()
        for r in caplog.records
        if "design_agent.locate.complete" in r.getMessage()
    ]
    assert len(lines) == 1, f"Expected exactly one cost line, got: {lines}"
    line = lines[0]

    # Required fields present.
    assert "repo=org/repo" in line
    assert "sha=abc123" in line
    assert "input_tokens=" in line
    assert "output_tokens=" in line
    assert "est_cost_usd=" in line
    assert "status=" in line
    assert "n_candidates=" in line

    # No PRD content in the log line.
    assert "UNIQUE-PRD-CONTENT-XYZ" not in line
    assert "widget inventory" not in line


def test_model_is_sonnet_4_6():
    """The module constant and the actual call both use claude-sonnet-4-6."""
    assert _MODEL == "claude-sonnet-4-6"

    m = _map_with_nodes("/team")
    fake = FakeClient([_make_response(_happy_payload())])
    locate_screen("prd", m, client=fake)

    assert fake.calls[0]["model"] == "claude-sonnet-4-6"


# ---------------------------------------------------------------------------
# Prompt content (property tests)
# ---------------------------------------------------------------------------


def test_locate_system_forces_ambiguous_abstention():
    """LOCATE_SYSTEM instructs the model to set ambiguous=true when the PRD is unclear."""
    lower = LOCATE_SYSTEM.lower()
    # Must mention setting the ambiguous flag.
    assert "ambiguous" in lower
    # Must mention keeping confidence below the threshold when uncertain.
    assert "80" in LOCATE_SYSTEM
    assert "uncertain" in lower or "unclear" in lower or "genuine" in lower


def test_locate_system_constrains_to_given_screens():
    """LOCATE_SYSTEM instructs the model to choose only from the provided list."""
    lower = LOCATE_SYSTEM.lower()
    assert "only from" in lower or "only from the" in lower or "choose only" in lower
    assert "never invent" in lower or "do not invent" in lower


# ---------------------------------------------------------------------------
# Placement classification (3-way) + spans flag + separate confidence
# ---------------------------------------------------------------------------


def _classified_payload(
    route: str = "/team",
    *,
    confidence: int = 92,
    classification=None,
    spans_multi_surface=None,
    classification_confidence=None,
) -> dict:
    """A happy candidate, optionally carrying the placement-classification fields.

    Any field left as None is OMITTED from the JSON entirely, so the same helper
    exercises both the present-field and the absent-field (default) paths.
    """
    cand: dict = {
        "route": route,
        "entry_component": "TeamScreen",
        "confidence": confidence,
        "rationale": "The PRD describes team management.",
        "ambiguous": False,
    }
    if classification is not None:
        cand["classification"] = classification
    if spans_multi_surface is not None:
        cand["spans_multi_surface"] = spans_multi_surface
    if classification_confidence is not None:
        cand["classification_confidence"] = classification_confidence
    return {"candidates": [cand], "is_multi_node": False}


def test_classification_parses():
    """A candidate labelled attach-to-host is carried through with that value."""
    m = _map_with_nodes("/team")
    payload = _classified_payload(classification="attach-to-host")
    fake = FakeClient([_make_response(payload)])

    result = locate_screen("attach a new feature PRD", m, client=fake)

    assert result.candidates[0].classification == "attach-to-host"


def test_classification_defaults_and_bad_label_safe():
    """Omitted classification defaults to modify-existing; a bad label coerces safely."""
    m = _map_with_nodes("/team")

    # Omitted → default.
    fake_default = FakeClient([_make_response(_classified_payload())])
    result_default = locate_screen("prd", m, client=fake_default)
    assert result_default.candidates[0].classification == "modify-existing"

    # Unrecognized label → coerced to modify-existing, candidate still present.
    fake_bad = FakeClient([_make_response(_classified_payload(classification="frobnicate"))])
    result_bad = locate_screen("prd", m, client=fake_bad)
    assert len(result_bad.candidates) == 1
    assert result_bad.candidates[0].classification == "modify-existing"


def test_spans_multi_surface_carried():
    """spans_multi_surface=true is carried as True; omitted defaults to False."""
    m = _map_with_nodes("/team")

    fake_true = FakeClient([_make_response(_classified_payload(spans_multi_surface=True))])
    result_true = locate_screen("multi-surface feature PRD", m, client=fake_true)
    assert result_true.candidates[0].spans_multi_surface is True

    fake_default = FakeClient([_make_response(_classified_payload())])
    result_default = locate_screen("prd", m, client=fake_default)
    assert result_default.candidates[0].spans_multi_surface is False


def test_classification_confidence_separate_and_clamped():
    """classification_confidence is independent from confidence and clamps to [0,100]."""
    m = _map_with_nodes("/team")

    # Both confidences present and distinct → carried independently.
    fake = FakeClient(
        [_make_response(_classified_payload(confidence=95, classification_confidence=60))]
    )
    result = locate_screen("prd", m, client=fake)
    first = result.candidates[0]
    assert first.confidence == 95
    assert first.classification_confidence == 60

    # Out-of-range int → clamped to 100.
    fake_over = FakeClient([_make_response(_classified_payload(classification_confidence=150))])
    result_over = locate_screen("prd", m, client=fake_over)
    assert result_over.candidates[0].classification_confidence == 100

    # 0-1 float → truncated to int and stays within [0,100].
    fake_frac = FakeClient([_make_response(_classified_payload(classification_confidence=0.85))])
    result_frac = locate_screen("prd", m, client=fake_frac)
    cc = result_frac.candidates[0].classification_confidence
    assert isinstance(cc, int)
    assert 0 <= cc <= 100

    # Default when omitted is 0, independent of a high which-surface confidence.
    fake_default = FakeClient([_make_response(_classified_payload(confidence=95))])
    result_default = locate_screen("prd", m, client=fake_default)
    assert result_default.candidates[0].confidence == 95
    assert result_default.candidates[0].classification_confidence == 0


def test_malformed_still_empty_never_raises_with_new_fields():
    """Garbage output still yields an empty LocateResult and never raises."""
    m = _map_with_nodes("/team")
    fake = FakeClient([_make_response("totally not json {{{ broken ]]]")])

    result = locate_screen("prd", m, client=fake)

    assert isinstance(result, LocateResult)
    assert result.candidates == []

    # A candidate whose new fields are malformed (but the route is valid) must
    # also never raise — it survives with the safe defaults.
    bad_fields = {
        "candidates": [
            {
                "route": "/team",
                "entry_component": "T",
                "confidence": 80,
                "rationale": "r",
                "ambiguous": False,
                "classification": 123,                       # not a string label
                "spans_multi_surface": "garblestring",       # not a bool
                "classification_confidence": "not-a-number", # not numeric
            }
        ],
        "is_multi_node": False,
    }
    fake2 = FakeClient([_make_response(bad_fields)])
    result2 = locate_screen("prd", m, client=fake2)
    assert len(result2.candidates) == 1
    surviving = result2.candidates[0]
    assert surviving.classification == "modify-existing"
    assert surviving.spans_multi_surface is False
    assert surviving.classification_confidence == 0


def test_pre_amendment_shape_backward_compatible():
    """A pre-amendment payload (no new fields) parses with defaults, rest unchanged."""
    m = _map_with_nodes("/team", "/settings/members", "/admin")
    fake = FakeClient([_make_response(_happy_payload())])

    result = locate_screen("team management PRD", m, client=fake)
    first = result.candidates[0]

    # Pre-amendment fields behave exactly as before.
    assert first.route == "/team"
    assert first.confidence == 92
    assert first.ambiguous is False
    # New fields fall back to their defaults.
    assert first.classification == "modify-existing"
    assert first.spans_multi_surface is False
    assert first.classification_confidence == 0


def test_locate_system_carries_classification_rules():
    """LOCATE_SYSTEM instructs the 3-way classification + carry-forward rules."""
    lower = LOCATE_SYSTEM.lower()

    # The three classification values are named.
    assert "modify-existing" in LOCATE_SYSTEM
    assert "attach-to-host" in LOCATE_SYSTEM
    assert "no-host-decline" in LOCATE_SYSTEM

    # The modify-vs-attach sub-label is advisory, not a routing gate.
    assert "advisory" in lower

    # classification_confidence is a separate signal from which-surface confidence.
    assert "classification_confidence" in LOCATE_SYSTEM
    assert "separate" in lower

    # The spans signal is described.
    assert "spans_multi_surface" in LOCATE_SYSTEM


def test_locate_system_reconciles_decline_with_empty_array():
    """The prompt directs a no-host-decline candidate (not an empty array) when nothing hosts."""
    lower = LOCATE_SYSTEM.lower()
    assert "no-host-decline" in LOCATE_SYSTEM
    # It explicitly addresses the previously-silent empty-array case.
    assert "empty" in lower
    # And it instructs echoing the surface id shown in square brackets.
    assert "square brackets" in lower


# ---------------------------------------------------------------------------
# Candidate-validity keyed on node id (admits shell/section; exempts decline)
# ---------------------------------------------------------------------------


def _map_with_typed_nodes() -> MapResult:
    """A map carrying a routed screen, an in-page section, and the app shell —
    each with a distinct stable id, mirroring the kind/id node model."""
    nodes = [
        ScreenNode(route="/team", entry_component="TeamScreen", composed_components=["A", "B"]),
        ScreenNode(
            route="/team",
            entry_component="TeamMembers",
            kind="section",
            id="/team-members",
        ),
        ScreenNode(
            route="(app layout — global chrome, not a route)",
            entry_component="AppShell",
            kind="shell",
            id="app-shell",
        ),
    ]
    shell = ShellModel(brand="Acme", nav_items=[NavItem(label="Team", route="/team")])
    return MapResult(
        repo="org/repo", commit_sha="abc123", posture="CLEAN", nodes=nodes, shell=shell
    )


def test_candidate_validity_is_node_id_keyed():
    """A candidate whose id is in the map's node-id set survives; an unknown id is dropped."""
    m = _map_with_typed_nodes()
    payload = {
        "candidates": [
            {
                "route": "/team",
                "id": "/team",
                "entry_component": "TeamScreen",
                "confidence": 90,
                "rationale": "r",
                "ambiguous": False,
            },
            {
                "route": "/ghost",
                "id": "/ghost",
                "entry_component": "X",
                "confidence": 88,
                "rationale": "r",
                "ambiguous": False,
            },
        ],
        "is_multi_node": False,
    }
    fake = FakeClient([_make_response(payload)])

    result = locate_screen("prd", m, client=fake)

    ids = {c.id for c in result.candidates}
    assert "/team" in ids        # real node id survives
    assert "/ghost" not in ids   # hallucinated id dropped — protection now id-keyed


def test_shell_and_section_candidates_survive_id_keying():
    """A shell-id and a section-id candidate survive — route-string keying dropped them."""
    m = _map_with_typed_nodes()
    payload = {
        "candidates": [
            {
                "route": "(app layout — global chrome, not a route)",
                "id": "app-shell",
                "entry_component": "AppShell",
                "confidence": 84,
                "rationale": "Global chrome hosts the notification center.",
                "ambiguous": False,
                "classification": "attach-to-host",
                "spans_multi_surface": True,
                "classification_confidence": 80,
            },
            {
                "route": "/team",
                "id": "/team-members",
                "entry_component": "TeamMembers",
                "confidence": 80,
                "rationale": "r",
                "ambiguous": False,
            },
        ],
        "is_multi_node": False,
    }
    fake = FakeClient([_make_response(payload)])

    result = locate_screen("global notification center PRD", m, client=fake)

    ids = {c.id for c in result.candidates}
    assert "app-shell" in ids       # previously deleted by route-string keying
    assert "/team-members" in ids
    # The spans signal on the shell candidate now actually surfaces.
    shell_cand = next(c for c in result.candidates if c.id == "app-shell")
    assert shell_cand.spans_multi_surface is True


def test_no_host_decline_candidate_survives_without_node():
    """A no-host-decline candidate has no backing node yet survives as the decline signal."""
    m = _map_with_typed_nodes()
    payload = {
        "candidates": [
            {
                "route": "",
                "id": "",
                "entry_component": "",
                "confidence": 0,
                "rationale": "No surface can host standalone video conferencing.",
                "ambiguous": False,
                "classification": "no-host-decline",
                "spans_multi_surface": False,
                "classification_confidence": 85,
            }
        ],
        "is_multi_node": False,
    }
    fake = FakeClient([_make_response(payload)])

    result = locate_screen("standalone video conferencing PRD", m, client=fake)

    assert len(result.candidates) == 1
    decline = result.candidates[0]
    assert decline.classification == "no-host-decline"
    assert decline.classification_confidence == 85


# ---------------------------------------------------------------------------
# Plain-English / integrity
# ---------------------------------------------------------------------------


# Construct the pattern via concatenation so this file doesn't self-match.
_PROHIBITED_PATTERN = re.compile(
    "|".join([
        r"C\d+-\d+",
        "C" + "-series",
        r"H\d+-\d+",
        r"P\d+-\d+",
        r"\bAD\d+\b",
        r"\bF\d{1,2}\b",
        "D" + "BD",
        "Baba" + "jide",
        "spi" + "ke",
    ])
)

_SOURCE_FILES = [
    Path(__file__).parent.parent / "app" / "design_agent" / "codebase_map" / "locate.py",
    Path(__file__),
]


def test_no_prohibited_tokens_in_source():
    """Committed source files contain no internal project coordinates."""
    for path in _SOURCE_FILES:
        text = path.read_text()
        matches = _PROHIBITED_PATTERN.findall(text)
        assert not matches, (
            f"{path.name} contains prohibited tokens: {matches[:5]}"
        )

    # Also check the LOCATE_SYSTEM constant that was appended to prompts.py.
    assert not _PROHIBITED_PATTERN.search(LOCATE_SYSTEM), (
        "LOCATE_SYSTEM contains prohibited tokens"
    )


def test_template_version_at_current():
    """DESIGN_AGENT_TEMPLATE_VERSION is 5 after the recreate-discipline bump."""
    assert DESIGN_AGENT_TEMPLATE_VERSION == 5
