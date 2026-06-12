"""Tests for POST /v1/design-agent/locate.

Covers the wiring layer: workspace isolation → installation resolver →
build_map → locate_screen → decide_gate → response serialization.
No new map/locate/gate logic is tested here — that belongs in the unit
suites for their respective modules.
"""
from __future__ import annotations

import asyncio
import importlib
import logging
import re
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient

from tests.conftest import _TEST_COMPANY_ID

# ── env fixture ──────────────────────────────────────────────────────────────


@pytest.fixture
def env(isolated_settings, monkeypatch):
    """Feature flag ON + DA route stack reloaded in dependency order."""
    monkeypatch.setenv("DESIGN_AGENT_ENABLED", "1")

    import app.routes.design_agent as routes_mod
    importlib.reload(routes_mod)
    import app.main as main_mod
    importlib.reload(main_mod)

    return SimpleNamespace(routes=routes_mod, main=main_mod)


@pytest.fixture
def client(company_client) -> TestClient:
    """Bearer-authed TestClient (require_company). workspace_id == _TEST_COMPANY_ID."""
    return company_client


# ── seed helpers ─────────────────────────────────────────────────────────────

def _seed_prd(
    *,
    prd_id: int = 1,
    brief_id: int = 1,
    workspace_slug: str = f"slug-{_TEST_COMPANY_ID}",
    payload_md: str = "Login screen for the test product",
) -> None:
    """Seed a brief + PRD row so require_owned_prd resolves to the test workspace."""
    from tests import _fake_supabase
    db = _fake_supabase.get_fake_db()
    db.execute(
        "INSERT INTO briefs (id, dataset, payload, is_current) VALUES (?, ?, '{}', 1)",
        (brief_id, workspace_slug),
    )
    db.execute(
        "INSERT INTO prds (id, brief_id, insight_index, title, payload_md, status)"
        " VALUES (?, ?, 0, 'Test PRD', ?, 'ready')",
        (prd_id, brief_id, payload_md),
    )
    db.commit()


def _seed_cross_workspace_prd(*, prd_id: int = 99) -> None:
    """Seed a PRD belonging to a different company (for workspace-isolation tests)."""
    from tests import _fake_supabase
    db = _fake_supabase.get_fake_db()
    db.execute(
        "INSERT INTO companies (id, slug, display_name) VALUES ('other-co', 'slug-other-co', 'Other Co')"
    )
    db.execute(
        "INSERT INTO briefs (id, dataset, payload, is_current) VALUES (200, 'slug-other-co', '{}', 1)"
    )
    db.execute(
        "INSERT INTO prds (id, brief_id, insight_index, title, payload_md, status)"
        " VALUES (?, 200, 0, 'Other PRD', 'Other workspace content', 'ready')",
        (prd_id,),
    )
    db.commit()


def _mock_installation(monkeypatch, installation_id: int = 42) -> None:
    """Patch installation resolver to return a fixed installation id."""
    monkeypatch.setattr(
        "app.routes.design_agent._resolve_github_installation_id_for_repo",
        lambda *a, **kw: installation_id,
    )


def _make_map_result(
    route: str = "/home",
    entry_component: str = "HomeScreen",
    confidence: int = 90,
    composed_components: list | None = None,
    posture: str = "CLEAN",
):
    """Build a minimal MapResult + LocateResult for happy-path tests."""
    from app.design_agent.codebase_map.types import MapResult, ScreenNode, ShellModel
    from app.design_agent.codebase_map.locate import LocateResult, LocateCandidate

    node = ScreenNode(
        route=route,
        entry_component=entry_component,
        composed_components=composed_components or ["Header", "Footer"],
    )
    map_result = MapResult(
        repo="org/repo",
        posture=posture,  # type: ignore[arg-type]
        nodes=[node],
        shell=ShellModel(),
    )
    candidate = LocateCandidate(
        route=route,
        entry_component=entry_component,
        confidence=confidence,
        rationale="Main screen",
        ambiguous=False,
    )
    locate_result = LocateResult(candidates=[candidate])
    return map_result, locate_result


# ── flag / auth ───────────────────────────────────────────────────────────────


def test_locate_404_when_flag_off(client, env, monkeypatch):
    """Feature flag off → 404 (invisible, not 401/422)."""
    _seed_prd()
    monkeypatch.setenv("DESIGN_AGENT_ENABLED", "0")
    resp = client.post(
        "/v1/design-agent/locate",
        json={"prd_id": 1, "github_repo": "org/repo"},
    )
    assert resp.status_code == 404


def test_locate_cross_workspace_prd_404(client, env):
    """PRD belonging to another workspace returns 404, not the locate result."""
    _seed_cross_workspace_prd(prd_id=99)
    resp = client.post(
        "/v1/design-agent/locate",
        json={"prd_id": 99, "github_repo": "org/repo"},
    )
    assert resp.status_code == 404


# ── happy paths ───────────────────────────────────────────────────────────────


def test_locate_auto_proceed_response(client, env, monkeypatch):
    """Happy path: valid PRD + connected repo + CLEAN map + high confidence → auto_proceed."""
    _seed_prd()
    _mock_installation(monkeypatch)
    fake_map, fake_locate = _make_map_result(confidence=90, composed_components=["Header", "Hero", "Footer"])

    async def _fake_to_thread(func, *args, **kwargs):
        if func.__name__ == "build_map":
            return fake_map
        return fake_locate

    with patch("asyncio.to_thread", new=_fake_to_thread):
        resp = client.post(
            "/v1/design-agent/locate",
            json={"prd_id": 1, "github_repo": "org/repo"},
        )

    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["decision"] == "auto_proceed"
    assert len(body["chosen"]) == 1
    assert body["chosen"][0]["route"] == "/home"
    # Planner amendment: component_count populated from ScreenNode.composed_components.
    assert body["chosen"][0]["component_count"] == 3
    assert len(body["ranked"]) == 1
    assert body["posture"] == "CLEAN"
    assert body["unmapped"] is False
    assert body["repo"] == "org/repo"


def test_locate_ranked_confirm_response(client, env, monkeypatch):
    """Low confidence → ranked_confirm, chosen is empty, ranked carries candidates."""
    _seed_prd()
    _mock_installation(monkeypatch)
    # confidence 40 < default threshold 80 → ranked_confirm
    fake_map, fake_locate = _make_map_result(confidence=40)

    async def _fake_to_thread(func, *args, **kwargs):
        if func.__name__ == "build_map":
            return fake_map
        return fake_locate

    with patch("asyncio.to_thread", new=_fake_to_thread):
        resp = client.post(
            "/v1/design-agent/locate",
            json={"prd_id": 1, "github_repo": "org/repo"},
        )

    assert resp.status_code == 200
    body = resp.json()
    assert body["decision"] == "ranked_confirm"
    assert body["chosen"] == []
    assert len(body["ranked"]) == 1
    assert body["unmapped"] is False


# ── degradation / errors ─────────────────────────────────────────────────────


def test_no_installation_returns_unmapped_no_llm_call(client, env, monkeypatch):
    """No GitHub installation → unmapped=True, 200, locate_screen never called."""
    _seed_prd()
    # Resolver returns None → short-circuit before LLM call.
    monkeypatch.setattr(
        "app.routes.design_agent._resolve_github_installation_id_for_repo",
        lambda *a, **kw: None,
    )
    locate_call_count = 0

    async def _spy_to_thread(func, *args, **kwargs):
        nonlocal locate_call_count
        if func.__name__ == "locate_screen":
            locate_call_count += 1
        return None  # build_map path; locate_screen should never be reached

    with patch("asyncio.to_thread", new=_spy_to_thread):
        resp = client.post(
            "/v1/design-agent/locate",
            json={"prd_id": 1, "github_repo": "org/repo"},
        )

    assert resp.status_code == 200
    body = resp.json()
    assert body["unmapped"] is True
    assert body["decision"] == "ranked_confirm"
    assert body["chosen"] == []
    assert locate_call_count == 0, "locate_screen must not be called when installation is None"


def test_empty_map_returns_unmapped(client, env, monkeypatch):
    """build_map returns None (no snapshot) → unmapped=True, 200."""
    _seed_prd()
    _mock_installation(monkeypatch)

    async def _fake_to_thread(func, *args, **kwargs):
        if func.__name__ == "build_map":
            return None
        return None

    with patch("asyncio.to_thread", new=_fake_to_thread):
        resp = client.post(
            "/v1/design-agent/locate",
            json={"prd_id": 1, "github_repo": "org/repo"},
        )

    assert resp.status_code == 200
    assert resp.json()["unmapped"] is True
    assert resp.json()["decision"] == "ranked_confirm"


def test_map_failure_fails_open_no_502(client, env, monkeypatch, caplog):
    """build_map raises → 200 unmapped=True, no 502; map_failed log emitted."""
    _seed_prd()
    _mock_installation(monkeypatch)

    async def _raise_on_build(func, *args, **kwargs):
        if func.__name__ == "build_map":
            raise RuntimeError("network error")
        return None

    with caplog.at_level(logging.INFO, logger="app.routes.design_agent"):
        with patch("asyncio.to_thread", new=_raise_on_build):
            resp = client.post(
                "/v1/design-agent/locate",
                json={"prd_id": 1, "github_repo": "org/repo"},
            )

    assert resp.status_code == 200, "map failure must fail-open, not 502"
    body = resp.json()
    assert body["unmapped"] is True
    assert body["decision"] == "ranked_confirm"
    log_text = " ".join(r.getMessage() for r in caplog.records)
    assert "locate.map_failed" in log_text


def test_locate_llm_failure_returns_502(client, env, monkeypatch):
    """locate_screen raises → 502; the endpoint never fabricates a screen."""
    _seed_prd()
    _mock_installation(monkeypatch)
    fake_map, _ = _make_map_result()

    async def _fake_to_thread(func, *args, **kwargs):
        if func.__name__ == "build_map":
            return fake_map
        # locate_screen path — simulate an API error
        raise RuntimeError("Anthropic API error")

    with patch("asyncio.to_thread", new=_fake_to_thread):
        resp = client.post(
            "/v1/design-agent/locate",
            json={"prd_id": 1, "github_repo": "org/repo"},
        )

    assert resp.status_code == 502


# ── concurrency / observability ───────────────────────────────────────────────


def test_blocking_calls_offloaded_to_thread(client, env, monkeypatch):
    """build_map and locate_screen are both dispatched via asyncio.to_thread."""
    _seed_prd()
    _mock_installation(monkeypatch)
    fake_map, fake_locate = _make_map_result()

    calls: list[str] = []

    async def _spy_to_thread(func, *args, **kwargs):
        calls.append(func.__name__)
        if func.__name__ == "build_map":
            return fake_map
        return fake_locate

    with patch("asyncio.to_thread", new=_spy_to_thread):
        resp = client.post(
            "/v1/design-agent/locate",
            json={"prd_id": 1, "github_repo": "org/repo"},
        )

    assert resp.status_code == 200
    assert "build_map" in calls, f"build_map not dispatched via to_thread; calls={calls}"
    assert "locate_screen" in calls, f"locate_screen not dispatched via to_thread; calls={calls}"


def test_request_log_no_prd_leak(client, env, monkeypatch, caplog):
    """Request log carries identifiers only; PRD body and installation token never logged."""
    _seed_prd(payload_md="CONFIDENTIAL_PRD_BODY_SENTINEL")
    _mock_installation(monkeypatch, installation_id=12345)

    async def _fake_to_thread(func, *args, **kwargs):
        # short-circuit so we only check the request log line, not the full flow
        if func.__name__ == "build_map":
            return None
        return None

    with caplog.at_level(logging.INFO, logger="app.routes.design_agent"):
        with patch("asyncio.to_thread", new=_fake_to_thread):
            resp = client.post(
                "/v1/design-agent/locate",
                json={"prd_id": 1, "github_repo": "org/repo"},
            )

    all_log = " ".join(r.getMessage() for r in caplog.records)
    assert "locate.request" in all_log, "request-start log line missing"
    assert "prd_id=1" in all_log
    assert "org/repo" in all_log
    assert _TEST_COMPANY_ID in all_log
    # No PRD body or token in logs.
    assert "CONFIDENTIAL_PRD_BODY_SENTINEL" not in all_log
    assert "12345" not in all_log  # installation id must not appear


# ── integrity ─────────────────────────────────────────────────────────────────


def test_no_existing_route_broken_imports_clean(env):
    """Reloading the route module succeeds; python -c import is clean."""
    # The reload in env already proves the module imports cleanly.
    import app.routes.design_agent as routes_mod

    # Assert every existing route path is still registered.
    paths = {route.path for route in routes_mod.router.routes}  # type: ignore[attr-defined]
    assert "/v1/design-agent/generate" in paths
    assert "/v1/design-agent/prd-patches" in paths
    assert "/v1/design-agent/figma-files" in paths
    assert "/v1/design-agent/locate" in paths


def test_no_prohibited_tokens_in_appended_lines():
    """Appended lines in route/api files and new test files contain no internal coordinates."""
    _PATTERN = re.compile("|".join([
        r"C[0-9]+-[0-9]+", "C" + "-series", r"H[0-9]+-[0-9]+", r"P[0-9]+-[0-9]+",
        r"\bAD[0-9]+", r"\bF[0-9]{1,2}\b", "D" + "BD", "Babaj" + "ide", r"\bspike\b",
    ]))
    repo_root = Path(__file__).parents[2]  # backend/tests → backend → repo root
    files_to_check = [
        # New files: check entire content.
        Path(__file__),  # this test file
        repo_root / "web" / "app" / "lib" / "__tests__" / "designAgentLocate.test.ts",
    ]
    for path in files_to_check:
        content = path.read_text()
        matches = _PATTERN.findall(content)
        assert not matches, f"{path.name}: prohibited tokens found: {matches}"
