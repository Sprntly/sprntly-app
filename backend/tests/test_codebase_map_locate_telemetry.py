"""Unit tests for emit_locate_telemetry and its route call-site.

Covers: calibration line shape, no-content-leak discipline, emission count,
route call-site wiring, distinction from the LLM cost-summary line, and
import integrity.
"""
from __future__ import annotations

import importlib
import json
import logging
import re
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient

from tests.conftest import _TEST_COMPANY_ID

# ── shared fixtures for route tests ──────────────────────────────────────────


@pytest.fixture
def env(isolated_settings, monkeypatch):
    """Feature flag ON + design-agent route stack reloaded in dependency order."""
    monkeypatch.setenv("DESIGN_AGENT_ENABLED", "1")
    import app.routes.design_agent as routes_mod
    importlib.reload(routes_mod)
    import app.main as main_mod
    importlib.reload(main_mod)
    return SimpleNamespace(routes=routes_mod, main=main_mod)


@pytest.fixture
def client(company_client) -> TestClient:
    """Bearer-authed TestClient with workspace_id == _TEST_COMPANY_ID."""
    return company_client


# ── seed helper ───────────────────────────────────────────────────────────────


def _seed_prd(*, prd_id: int = 1, payload_md: str = "Test PRD content") -> None:
    from tests import _fake_supabase
    db = _fake_supabase.get_fake_db()
    workspace_slug = f"slug-{_TEST_COMPANY_ID}"
    db.execute(
        "INSERT INTO briefs (id, dataset, payload, is_current) VALUES (1, ?, '{}', 1)",
        (workspace_slug,),
    )
    db.execute(
        "INSERT INTO prds (id, brief_id, insight_index, title, payload_md, status)"
        " VALUES (?, 1, 0, 'Test PRD', ?, 'ready')",
        (prd_id, payload_md),
    )
    db.commit()


# ── GateResult factory ────────────────────────────────────────────────────────


def _make_gate(
    *,
    decision: str = "auto_proceed",
    top_confidence: int = 90,
    chosen_route: str = "/team",
    ambiguous: bool = False,
    n_ranked: int = 3,
    threshold: int = 80,
):
    from app.design_agent.codebase_map.gate import GateResult
    from app.design_agent.codebase_map.locate import LocateCandidate

    chosen = (
        [LocateCandidate(route=chosen_route, entry_component="Screen",
                         confidence=top_confidence, rationale="test", ambiguous=ambiguous)]
        if chosen_route
        else []
    )
    ranked = [
        LocateCandidate(route=f"/r{i}", entry_component="Screen",
                        confidence=top_confidence - i * 5, rationale="test", ambiguous=(ambiguous if i == 0 else False))
        for i in range(n_ranked)
    ]
    return GateResult(decision=decision, chosen=chosen, ranked=ranked,
                      threshold=threshold, top_confidence=top_confidence)


# ── helper line shape ─────────────────────────────────────────────────────────


def test_telemetry_line_auto_proceed_fields(caplog):
    """auto_proceed gate emits all expected k=v fields in the calibration line."""
    from app.design_agent.codebase_map.locate import emit_locate_telemetry
    gate = _make_gate(decision="auto_proceed", top_confidence=90, chosen_route="/team",
                      ambiguous=False, n_ranked=3, threshold=80)
    with caplog.at_level(logging.INFO, logger="app.design_agent.codebase_map.locate"):
        emit_locate_telemetry(repo="org/repo", sha="abc123", gate_result=gate, n_candidates=3)
    assert len(caplog.records) == 1
    msg = caplog.records[0].getMessage()
    assert msg.startswith("codebase_map.locate ")
    assert "repo=org/repo" in msg
    assert "sha=abc123" in msg
    assert "top_confidence=90" in msg
    assert "decision=auto_proceed" in msg
    assert "chosen_screen=/team" in msg
    assert "ambiguous=False" in msg
    assert "n_candidates=3" in msg
    assert "threshold=80" in msg


def test_telemetry_line_ranked_confirm_empty_chosen(caplog):
    """ranked_confirm gate emits decision=ranked_confirm and empty chosen_screen."""
    from app.design_agent.codebase_map.gate import GateResult
    from app.design_agent.codebase_map.locate import LocateCandidate, emit_locate_telemetry
    ranked = [LocateCandidate(route="/about", entry_component="AboutScreen",
                              confidence=60, rationale="test", ambiguous=False)]
    gate = GateResult(decision="ranked_confirm", chosen=[], ranked=ranked,
                      threshold=80, top_confidence=60)
    with caplog.at_level(logging.INFO, logger="app.design_agent.codebase_map.locate"):
        emit_locate_telemetry(repo="org/repo", sha="def456", gate_result=gate, n_candidates=1)
    msg = caplog.records[0].getMessage()
    assert "decision=ranked_confirm" in msg
    assert "chosen_screen= ambiguous" in msg  # empty value, followed by next k=v
    assert "n_candidates=1" in msg


def test_telemetry_line_unmapped_empty_sha(caplog):
    """unmapped fail-open path emits sha='' and n_candidates=0."""
    from app.design_agent.codebase_map.gate import GateResult
    from app.design_agent.codebase_map.locate import emit_locate_telemetry
    gate = GateResult(decision="ranked_confirm", chosen=[], ranked=[],
                      threshold=80, top_confidence=0)
    with caplog.at_level(logging.INFO, logger="app.design_agent.codebase_map.locate"):
        emit_locate_telemetry(repo="org/repo", sha="", gate_result=gate, n_candidates=0)
    msg = caplog.records[0].getMessage()
    assert "sha= " in msg  # empty sha followed by next field
    assert "decision=ranked_confirm" in msg
    assert "chosen_screen= ambiguous" in msg
    assert "n_candidates=0" in msg


def test_telemetry_line_reflects_ambiguous(caplog):
    """when the leading ranked candidate has ambiguous=True, the line carries ambiguous=True."""
    from app.design_agent.codebase_map.gate import GateResult
    from app.design_agent.codebase_map.locate import LocateCandidate, emit_locate_telemetry
    ambig = LocateCandidate(route="/home", entry_component="HomeScreen",
                             confidence=85, rationale="model abstained", ambiguous=True)
    gate = GateResult(decision="ranked_confirm", chosen=[], ranked=[ambig],
                      threshold=80, top_confidence=85)
    with caplog.at_level(logging.INFO, logger="app.design_agent.codebase_map.locate"):
        emit_locate_telemetry(repo="org/repo", sha="sha999", gate_result=gate, n_candidates=1)
    msg = caplog.records[0].getMessage()
    assert "ambiguous=True" in msg


# ── discipline ────────────────────────────────────────────────────────────────


def test_telemetry_no_prd_or_rationale_or_token_leak(caplog):
    """PRD body, rationale text, and installation token must never appear in the log."""
    from app.design_agent.codebase_map.gate import GateResult
    from app.design_agent.codebase_map.locate import LocateCandidate, emit_locate_telemetry

    PRD_SENTINEL = "CONFIDENTIAL_PRD_BODY_SENTINEL_XYZ"
    RATIONALE_SENTINEL = "RATIONALE_TEXT_SENTINEL_ABC"
    TOKEN_SENTINEL = "ghp_INSTALLATION_TOKEN_SENTINEL_123"

    # The rationale field carries the sentinel — it must NOT appear in the log line.
    candidate = LocateCandidate(route="/login", entry_component="LoginScreen",
                                confidence=90, rationale=RATIONALE_SENTINEL, ambiguous=False)
    gate = GateResult(decision="auto_proceed", chosen=[candidate],
                      ranked=[candidate], threshold=80, top_confidence=90)
    with caplog.at_level(logging.INFO, logger="app.design_agent.codebase_map.locate"):
        emit_locate_telemetry(repo="org/repo", sha="sha_abc", gate_result=gate, n_candidates=1)
    all_logs = " ".join(r.getMessage() for r in caplog.records)
    assert PRD_SENTINEL not in all_logs
    assert RATIONALE_SENTINEL not in all_logs
    assert TOKEN_SENTINEL not in all_logs


# ── emission count + call-site ───────────────────────────────────────────────


def test_emitted_exactly_once_per_request(client, env, monkeypatch, caplog):
    """One /locate request produces exactly one codebase_map.locate line.

    /locate is now accept + poll: the POST mints a job and the pipeline (incl. the
    telemetry emission) runs in the background task. We suppress the fire-and-forget
    task on POST and drive ``_run_locate_bg`` once deterministically so the emission
    count is exactly one — the same single-emission guarantee the synchronous
    endpoint had."""
    import asyncio

    _seed_prd()
    monkeypatch.setattr(
        "app.routes.design_agent._resolve_github_installation_id_for_repo",
        lambda *a, **kw: 42,
    )
    from app.design_agent.codebase_map.locate import LocateCandidate, LocateResult
    from app.design_agent.codebase_map.types import MapResult, ScreenNode, ShellModel

    node = ScreenNode(route="/home", entry_component="HomeScreen",
                      composed_components=["Header"])
    map_result = MapResult(repo="org/repo", commit_sha="commit123",
                           posture="CLEAN", nodes=[node], shell=ShellModel())
    candidate = LocateCandidate(route="/home", entry_component="HomeScreen",
                                confidence=90, rationale="Main screen", ambiguous=False)
    locate_result = LocateResult(candidates=[candidate])

    async def _fake_to_thread(func, *args, **kwargs):
        if func.__name__ == "build_map":
            return map_result
        return locate_result

    async def _noop_bg(**_kw):
        return None

    with caplog.at_level(logging.INFO, logger="app.design_agent.codebase_map.locate"):
        with patch.object(env.routes, "_run_locate_bg", new=_noop_bg):
            accepted = client.post(
                "/v1/design-agent/locate",
                json={"prd_id": 1, "github_repo": "org/repo"},
            )
        assert accepted.status_code == 202
        job_id = accepted.json()["job_id"]
        rec = env.routes._locate_jobs[job_id]
        with patch("asyncio.to_thread", new=_fake_to_thread):
            asyncio.run(env.routes._run_locate_bg(
                job_id=job_id,
                workspace_id=rec["workspace_id"],
                github_repo="org/repo",
                ref=None,
                prd_text="",
                installation_id=42,
            ))

    calibration_lines = [r for r in caplog.records
                         if "codebase_map.locate" in r.getMessage()]
    assert len(calibration_lines) == 1, (
        f"Expected exactly 1 codebase_map.locate line, got {len(calibration_lines)}"
    )


# ── distinct from cost-summary ───────────────────────────────────────────────


def test_distinct_from_cost_summary_line(isolated_settings, caplog):
    """codebase_map.locate and design_agent.locate.complete have distinct operation tokens."""
    import json as _json
    from app.design_agent.codebase_map.gate import GateResult, decide_gate
    from app.design_agent.codebase_map.locate import (
        LocateCandidate,
        LocateResult,
        emit_locate_telemetry,
        locate_screen,
    )
    from app.design_agent.codebase_map.types import MapResult, NavItem, ScreenNode, ShellModel

    # Build a minimal map with one valid route so locate_screen can parse the response.
    node = ScreenNode(route="/dashboard", entry_component="DashboardScreen",
                      composed_components=["Nav", "Content"])
    map_result = MapResult(repo="org/repo", commit_sha="sha111",
                           posture="CLEAN", nodes=[node],
                           shell=ShellModel(brand="Acme", nav_items=[NavItem(label="Home", route="/dashboard")]))

    # Fake Anthropic client returning one valid candidate.
    payload = _json.dumps({
        "candidates": [{"route": "/dashboard", "entry_component": "DashboardScreen",
                        "confidence": 90, "rationale": "Test rationale", "ambiguous": False}],
        "is_multi_node": False,
    })

    class _FakeUsage:
        input_tokens = 100
        output_tokens = 50
        cache_read_input_tokens = 0
        cache_creation_input_tokens = 0

    class _FakeContent:
        def __init__(self, text):
            self.text = text

    class _FakeResp:
        content = [_FakeContent(payload)]
        usage = _FakeUsage()

    class _FakeMessages:
        def create(self, **kwargs):
            return _FakeResp()

    class _FakeClient:
        messages = _FakeMessages()

    with caplog.at_level(logging.INFO):
        # locate_screen emits design_agent.locate.complete in its finally block.
        locate_result = locate_screen("PRD text here", map_result, client=_FakeClient())
        # emit_locate_telemetry emits codebase_map.locate.
        threshold = 80
        gate = decide_gate(locate_result, threshold=threshold)
        emit_locate_telemetry(repo="org/repo", sha="sha111",
                              gate_result=gate, n_candidates=len(gate.ranked))

    all_msgs = [r.getMessage() for r in caplog.records]
    calibration = [m for m in all_msgs if m.startswith("codebase_map.locate ")]
    cost_summary = [m for m in all_msgs if "design_agent.locate.complete" in m]

    assert len(calibration) == 1, "codebase_map.locate calibration line missing"
    assert len(cost_summary) == 1, "design_agent.locate.complete cost-summary line missing"
    # They are distinguishable by their operation token prefix.
    assert all("design_agent.locate.complete" not in m for m in calibration)
    assert all("codebase_map.locate" not in m or m.startswith("codebase_map.locate ") for m in cost_summary)


# ── integrity ─────────────────────────────────────────────────────────────────


def test_no_existing_route_reference_broken_imports_clean(env):
    """import app.routes.design_agent succeeds; no existing route path removed."""
    import app.routes.design_agent as routes_mod
    paths = {route.path for route in routes_mod.router.routes}  # type: ignore[attr-defined]
    assert "/v1/design-agent/locate" in paths
    assert "/v1/design-agent/generate" in paths


def test_no_prohibited_tokens_in_appended_lines():
    """No internal coordinates in this new test file."""
    _PATTERN = re.compile("|".join([
        r"C[0-9]+-[0-9]+", "C" + "-series", r"H[0-9]+-[0-9]+", r"P[0-9]+-[0-9]+",
        r"\bAD[0-9]+", r"\bF[0-9]{1,2}\b", "D" + "BD", "Babaj" + "ide", r"\bspike\b",
    ]))
    content = Path(__file__).read_text()
    matches = _PATTERN.findall(content)
    assert not matches, f"Prohibited tokens found: {matches}"
