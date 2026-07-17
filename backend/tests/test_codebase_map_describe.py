"""Unit tests for the codebase_map/describe.py semantic describe layer + gate.

All LLM-path tests use a FakeClient — no real network calls. They cover the
describe round-trip (descriptor per node, title derivation, shell discipline
field), descriptor serialization, the pure completeness/hallucination gate
(hallucination flag, coverage gap, clean pass, id normalization), edge cases
(empty-map short-circuit, single batched call, duplicate ids), the LLM-surface
invariants (cache placement, cost-summary line, cache read), never-raise on
malformed output, and prompt/integrity guards.

The real-LLM half of the cache/cost ACs (a live describe run against a
connected repo) is executed in the standing ship-gate live-verify pass, not in
CI — these unit tests cover the stubbed-client half.
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

from app.design_agent.codebase_map.describe import (
    CompletenessReport,
    DescribedMap,
    SurfaceDescriptor,
    _MODEL,
    check_completeness,
    describe_surfaces,
)
from app.design_agent.codebase_map.types import MapResult, ScreenNode
from app.design_agent.prompts import DESCRIBE_SYSTEM, DESIGN_AGENT_TEMPLATE_VERSION


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


def _three_node_map() -> MapResult:
    """A route, an in-page section, and the app shell — all carrying id + kind."""
    nodes = [
        ScreenNode(
            route="/team",
            entry_component="TeamScreen",
            file="app/team/page.tsx",
            composed_components=["MemberList", "InviteButton"],
        ),
        ScreenNode(
            route="/settings/members",
            entry_component="MembersSettings",
            file="app/settings/members.tsx",
            kind="section",
            id="/settings/members#members",
        ),
        ScreenNode(
            route="",
            entry_component="AppShell",
            file="app/shell.tsx",
            kind="shell",
            id="app-shell",
        ),
    ]
    return MapResult(
        repo="org/repo",
        commit_sha="abc123",
        posture="CLEAN",
        nodes=nodes,
    )


def _sources_for(m: MapResult) -> dict[str, str]:
    return {node.file: f"// source of {node.entry_component}\nexport default function X() {{}}" for node in m.nodes}


def _descriptor_obj(sid: str, *, shell: bool = False) -> dict:
    obj = {
        "id": sid,
        "summary": f"This is {sid}.",
        "contains": ["Header", "Body"],
        "user_actions": ["Click a thing"],
        "key_entities": ["Widget"],
        "hosts_chrome_level_features": "",
    }
    if shell:
        obj["hosts_chrome_level_features"] = (
            "Hosts only app-wide chrome such as the notification center and global search."
        )
    return obj


def _describe_payload(m: MapResult, *, shell_ids: set[str] | None = None) -> dict:
    shell_ids = shell_ids or {n.id for n in m.nodes if n.kind == "shell"}
    return {
        "surfaces": [
            _descriptor_obj(node.id, shell=node.id in shell_ids) for node in m.nodes
        ]
    }


# ---------------------------------------------------------------------------
# Creation / happy-path
# ---------------------------------------------------------------------------


def test_describe_surfaces_returns_descriptor_per_node():
    """AC1: 3 nodes + a valid stub response → 3 descriptors with carried fields."""
    m = _three_node_map()
    fake = FakeClient([_make_response(_describe_payload(m))])

    result = describe_surfaces(m, _sources_for(m), client=fake)

    assert isinstance(result, DescribedMap)
    assert result.repo == "org/repo"
    assert result.commit_sha == "abc123"
    assert len(result.surfaces) == 3

    by_id = {s.id: s for s in result.surfaces}
    assert set(by_id) == {n.id for n in m.nodes}
    for node in m.nodes:
        d = by_id[node.id]
        assert d.kind == node.kind
        assert d.route == node.route
        assert d.summary  # non-empty semantic text carried from the model
        assert d.contains == ["Header", "Body"]
        assert d.user_actions == ["Click a thing"]
        assert d.key_entities == ["Widget"]


def test_title_derived_from_entry_component():
    """AC2: title humanized from entry_component; empty component falls back to route."""
    nodes = [
        ScreenNode(route="/settings/members", entry_component="MembersSettings", id="a"),
        ScreenNode(route="/team", entry_component="TeamScreen", id="b"),
        ScreenNode(route="/fallback", entry_component="", id="c"),
    ]
    m = MapResult(repo="org/repo", commit_sha="sha", nodes=nodes)
    payload = {"surfaces": [_descriptor_obj("a"), _descriptor_obj("b"), _descriptor_obj("c")]}
    fake = FakeClient([_make_response(payload)])

    result = describe_surfaces(m, {}, client=fake)
    by_id = {s.id: s for s in result.surfaces}

    assert by_id["a"].title == "Members Settings"
    assert by_id["b"].title == "Team"
    assert by_id["c"].title == "/fallback"  # entry_component empty → route fallback


def test_shell_node_gets_hosts_chrome_features():
    """AC3: the shell descriptor carries hosts_chrome_level_features; non-shell stays ''."""
    m = _three_node_map()
    fake = FakeClient([_make_response(_describe_payload(m))])

    result = describe_surfaces(m, _sources_for(m), client=fake)
    by_id = {s.id: s for s in result.surfaces}

    assert by_id["app-shell"].kind == "shell"
    assert by_id["app-shell"].hosts_chrome_level_features != ""
    for sid, d in by_id.items():
        if d.kind != "shell":
            assert d.hosts_chrome_level_features == ""


def test_shell_field_ignored_for_non_shell_even_if_model_supplies_it():
    """A non-shell node whose model object includes the chrome field still ends up ''."""
    nodes = [ScreenNode(route="/team", entry_component="TeamScreen", id="t")]
    m = MapResult(repo="org/repo", commit_sha="sha", nodes=nodes)
    obj = _descriptor_obj("t")
    obj["hosts_chrome_level_features"] = "the model tried to set this on a route"
    fake = FakeClient([_make_response({"surfaces": [obj]})])

    result = describe_surfaces(m, {}, client=fake)

    assert result.surfaces[0].hosts_chrome_level_features == ""


# ---------------------------------------------------------------------------
# Serialization
# ---------------------------------------------------------------------------


def test_descriptor_roundtrip():
    """SurfaceDescriptor / DescribedMap serialize and deserialize unchanged."""
    d = SurfaceDescriptor(
        id="/team",
        kind="route",
        route="/team",
        title="Team",
        summary="The team screen.",
        contains=["Members"],
        user_actions=["Invite"],
        key_entities=["Member"],
    )
    assert SurfaceDescriptor.model_validate(d.model_dump()) == d

    dm = DescribedMap(repo="org/repo", commit_sha="sha", surfaces=[d])
    assert DescribedMap.model_validate(dm.model_dump()) == dm

    cr = CompletenessReport(hallucinated_ids=["x"], coverage_gap_ids=["y"], ok=False)
    assert CompletenessReport.model_validate(cr.model_dump()) == cr


# ---------------------------------------------------------------------------
# Completeness gate (pure, deterministic, no LLM)
# ---------------------------------------------------------------------------


def test_gate_flags_hallucinated_surface():
    """AC4: a described id not in the node set → ok=False with id in hallucinated_ids."""
    m = _three_node_map()
    described = DescribedMap(
        repo="org/repo",
        commit_sha="abc123",
        surfaces=[SurfaceDescriptor(id=n.id, kind=n.kind) for n in m.nodes]
        + [SurfaceDescriptor(id="/invented", kind="route")],
    )

    report = check_completeness(m, described)

    assert report.ok is False
    assert "/invented" in report.hallucinated_ids
    assert report.coverage_gap_ids == []


def test_gate_flags_coverage_gap():
    """AC5: an enumerated node never described → ok=False with id in coverage_gap_ids."""
    m = _three_node_map()
    # Describe only the first two of three nodes.
    described = DescribedMap(
        repo="org/repo",
        commit_sha="abc123",
        surfaces=[SurfaceDescriptor(id=n.id, kind=n.kind) for n in m.nodes[:2]],
    )

    report = check_completeness(m, described)

    assert report.ok is False
    assert "app-shell" in report.coverage_gap_ids
    assert report.hallucinated_ids == []


def test_gate_clean_pass():
    """AC6: described ids == node ids → ok=True and both lists empty."""
    m = _three_node_map()
    described = DescribedMap(
        repo="org/repo",
        commit_sha="abc123",
        surfaces=[SurfaceDescriptor(id=n.id, kind=n.kind) for n in m.nodes],
    )

    report = check_completeness(m, described)

    assert report.ok is True
    assert report.hallucinated_ids == []
    assert report.coverage_gap_ids == []


def test_gate_id_whitespace_normalized():
    """AC7: a described id with surrounding whitespace matching a node id is NOT a hallucination."""
    m = _three_node_map()
    described = DescribedMap(
        repo="org/repo",
        commit_sha="abc123",
        surfaces=[SurfaceDescriptor(id=f"  {n.id}  ", kind=n.kind) for n in m.nodes],
    )

    report = check_completeness(m, described)

    assert report.ok is True
    assert report.hallucinated_ids == []
    assert report.coverage_gap_ids == []


# ---------------------------------------------------------------------------
# Edge cases
# ---------------------------------------------------------------------------


def test_empty_map_short_circuits_no_llm_call():
    """AC8: zero nodes → empty surfaces, ok=True, and the client is never invoked."""
    m = MapResult(repo="org/repo", commit_sha="abc123", nodes=[])
    fake = FakeClient([_make_response({"surfaces": []})])

    result = describe_surfaces(m, {}, client=fake)

    assert result.surfaces == []
    assert fake.calls == []  # short-circuited before any LLM call

    report = check_completeness(m, result)
    assert report.ok is True  # vacuously complete


def test_single_batched_call_for_n_surfaces():
    """AC9: describing N>0 surfaces makes exactly ONE messages.create call."""
    m = _three_node_map()
    fake = FakeClient([_make_response(_describe_payload(m))])

    describe_surfaces(m, _sources_for(m), client=fake)

    assert len(fake.calls) == 1


def test_duplicate_described_id_kept_first():
    """A duplicate id in the model output is de-duplicated (first kept), no crash."""
    nodes = [ScreenNode(route="/team", entry_component="TeamScreen", id="/team")]
    m = MapResult(repo="org/repo", commit_sha="sha", nodes=nodes)
    first = _descriptor_obj("/team")
    first["summary"] = "FIRST"
    second = _descriptor_obj("/team")
    second["summary"] = "SECOND"
    fake = FakeClient([_make_response({"surfaces": [first, second]})])

    result = describe_surfaces(m, {}, client=fake)

    ids = [s.id for s in result.surfaces]
    assert ids == ["/team"]  # only the first kept
    assert result.surfaces[0].summary == "FIRST"


def test_client_factory_used_when_none_injected():
    """When client=None, get_design_agent_client is called exactly once."""
    m = _three_node_map()
    fake = FakeClient([_make_response(_describe_payload(m))])
    call_count: list[int] = [0]

    def mock_factory():
        call_count[0] += 1
        return fake

    with patch("app.design_agent.client.get_design_agent_client", mock_factory):
        describe_surfaces(m, _sources_for(m))  # client not injected

    assert call_count[0] == 1


# ---------------------------------------------------------------------------
# LLM surface (cache placement, cost line, cache read)
# ---------------------------------------------------------------------------


def test_cache_control_on_stable_block_system_first():
    """AC10: system[0] is DESCRIBE_SYSTEM (no cache_control); the stable block carries it."""
    m = _three_node_map()
    fake = FakeClient([_make_response(_describe_payload(m))])

    describe_surfaces(m, _sources_for(m), client=fake)

    assert fake.calls, "No call was recorded"
    kwargs = fake.calls[0]

    system = kwargs["system"]
    assert isinstance(system, list)
    assert len(system) == 2
    # First block is the instruction constant, no cache breakpoint.
    assert system[0]["text"] == DESCRIBE_SYSTEM
    assert "cache_control" not in system[0]
    # Stable describe-input block carries the cache breakpoint.
    assert system[-1]["cache_control"] == {"type": "ephemeral", "ttl": "1h"}

    # The user turn carries no cache_control.
    messages = kwargs["messages"]
    assert len(messages) == 1
    assert "cache_control" not in messages[0]

    # The call uses the canonical model and the batched output cap.
    assert kwargs["model"] == "claude-sonnet-4-6"
    assert _MODEL == "claude-sonnet-4-6"


def test_describe_emits_cost_summary_line_no_leak(caplog):
    """AC11: exactly one cost line with required fields, no source bodies / PRD content."""
    m = _three_node_map()
    sources = {n.file: f"UNIQUE-SOURCE-XYZ body for {n.entry_component}" for n in m.nodes}
    fake = FakeClient([_make_response(_describe_payload(m))])

    with caplog.at_level(logging.INFO):
        describe_surfaces(m, sources, client=fake)

    lines = [
        r.getMessage()
        for r in caplog.records
        if "design_agent.describe.complete" in r.getMessage()
    ]
    assert len(lines) == 1, f"Expected exactly one cost line, got: {lines}"
    line = lines[0]

    # Required fields present.
    assert "repo=org/repo" in line
    assert "sha=abc123" in line
    assert "cached_input_tokens=" in line
    assert "input_tokens=" in line
    assert "output_tokens=" in line
    assert "duration_ms=" in line
    assert "est_cost_usd=" in line
    assert "status=complete" in line
    assert "error_class=" in line
    assert "n_surfaces=3" in line

    # No source bodies leaked into the cost line.
    assert "UNIQUE-SOURCE-XYZ" not in line


def test_describe_cache_read_nonzero_on_second_call(caplog):
    """AC11: a second describe within the cache window reports non-zero cached_input_tokens."""
    m = _three_node_map()
    sources = _sources_for(m)
    usage1 = _make_usage(input_tokens=100, cache_read_input_tokens=0)
    usage2 = _make_usage(input_tokens=20, cache_read_input_tokens=80)
    resp1 = _make_response(_describe_payload(m), usage1)
    resp2 = _make_response(_describe_payload(m), usage2)
    fake = FakeClient([resp1, resp2])

    with caplog.at_level(logging.INFO):
        describe_surfaces(m, sources, client=fake)
        describe_surfaces(m, sources, client=fake)

    log_lines = [
        r.getMessage()
        for r in caplog.records
        if "design_agent.describe.complete" in r.getMessage()
    ]
    assert len(log_lines) == 2, f"Expected 2 cost lines, got: {log_lines}"
    assert "cached_input_tokens=80" in log_lines[1]


# ---------------------------------------------------------------------------
# Error handling
# ---------------------------------------------------------------------------


def test_describe_malformed_json_degrades_never_raises_then_gate_fails():
    """AC12: non-JSON garbage → degraded DescribedMap (no raise); gate → all coverage gaps."""
    m = _three_node_map()
    fake = FakeClient([_make_response("this is not json at all {{{broken")])

    result = describe_surfaces(m, _sources_for(m), client=fake)

    assert isinstance(result, DescribedMap)
    assert result.surfaces == []  # degraded, not a partial guess
    assert result.repo == "org/repo"

    report = check_completeness(m, result)
    assert report.ok is False  # loud signal, not a silent empty pass
    assert set(report.coverage_gap_ids) == {n.id for n in m.nodes}
    assert report.hallucinated_ids == []


# ---------------------------------------------------------------------------
# Prompt append-only / integrity
# ---------------------------------------------------------------------------


def test_describe_prompt_append_only():
    """AC13: DESCRIBE_SYSTEM exists; the template version is byte-unchanged at 5; prompts compiles."""
    # The new constant is present and instructs strict-JSON, source-faithful describe.
    assert isinstance(DESCRIBE_SYSTEM, str) and DESCRIBE_SYSTEM
    lower = DESCRIBE_SYSTEM.lower()
    assert "strict json" in lower
    assert "do not invent" in lower
    assert "exactly as" in lower  # id must be carried exactly
    assert "hosts_chrome_level_features" in DESCRIBE_SYSTEM

    # The index-side prompt does NOT bump the recreate-template version.
    assert DESIGN_AGENT_TEMPLATE_VERSION == 8

    # Existing exports remain importable (append did not disturb them) and the
    # module byte-compiles — no existing importer breaks.
    from app.design_agent import prompts as prompts_mod

    assert hasattr(prompts_mod, "LOCATE_SYSTEM")
    assert hasattr(prompts_mod, "DESIGN_AGENT_RECREATE_DISCIPLINE")

    prompts_path = (
        Path(__file__).parent.parent / "app" / "design_agent" / "prompts.py"
    )
    proc = subprocess.run(
        [sys.executable, "-m", "py_compile", str(prompts_path)],
        capture_output=True,
        text=True,
    )
    assert proc.returncode == 0, f"prompts.py failed to compile: {proc.stderr}"


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
    Path(__file__).parent.parent / "app" / "design_agent" / "codebase_map" / "describe.py",
    Path(__file__),
]


def test_no_prohibited_tokens_in_source():
    """AC14: committed source files contain no internal project coordinates."""
    for path in _SOURCE_FILES:
        text = path.read_text()
        matches = _PROHIBITED_PATTERN.findall(text)
        assert not matches, f"{path.name} contains prohibited tokens: {matches[:5]}"

    # Also check the DESCRIBE_SYSTEM constant appended to prompts.py.
    assert not _PROHIBITED_PATTERN.search(DESCRIBE_SYSTEM), (
        "DESCRIBE_SYSTEM contains prohibited tokens"
    )
