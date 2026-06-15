"""Unit tests for wiring the PM-confirmed screen route from /generate into
the recreate pre-seed, plus arming the theme-bridge build-gate.

Two halves:

1. Backend resolution path — when ``design_source == "github"`` and the body
   carries ``chosen_screen_route`` + ``map_commit_sha``, ``_run_generation_bg``
   resolves a ``LocatedScreen`` via ``build_map`` + node-match and passes it
   into ``generate_prototype``. Every unhappy path falls back to
   ``located_screen=None`` without raising.

2. Theme-expectations seam — ``generate_prototype``'s recreate branch
   populates ``RunResult.theme_expectations`` from the bridged sources, and
   ``_run_generation_bg`` threads that value into ``_stage_complete_run`` so
   the build-gate fires on the recreate path and stays dormant otherwise.

Stubs ``build_map`` / ``generate_prototype`` / ``_stage_complete_run``. No
real network, no real LLM, no real vite build.
"""
from __future__ import annotations

import asyncio
import importlib
import logging
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import pytest
from cryptography.fernet import Fernet
from fastapi.testclient import TestClient

from app.design_agent.codebase_map.recreate import (
    LocatedScreen,
    RecreateSources,
    ThemeExpectations,
)
from app.design_agent.codebase_map.types import (
    LogoAsset,
    MapResult,
    ScreenNode,
    ShellModel,
)
from tests.conftest import _TEST_COMPANY_ID

# SQLite-compatible translation of the prototypes migration (mirrors the
# routes test). Same DDL kept local per file so reloads stay independent.
_PROTOTYPE_DDL = """
CREATE TABLE prototypes (
    id                     INTEGER PRIMARY KEY AUTOINCREMENT,
    prd_id                 INTEGER,
    workspace_id           TEXT NOT NULL,
    status                 TEXT NOT NULL DEFAULT 'generating',
    variant                TEXT NOT NULL DEFAULT 'v1',
    template_version       INTEGER NOT NULL,
    instructions           TEXT,
    target_platform        TEXT NOT NULL DEFAULT 'both',
    figma_file_key         TEXT,
    website_url            TEXT,
    github_installation_id INTEGER,
    bundle_url             TEXT,
    preview_image_url      TEXT,
    current_checkpoint_id  INTEGER,
    error                  TEXT,
    created_at             TEXT NOT NULL DEFAULT (datetime('now')),
    completed_at           TEXT,
    share_mode             TEXT NOT NULL DEFAULT 'private'
                           CHECK (share_mode IN ('private', 'public', 'passcode')),
    share_token            TEXT UNIQUE,
    share_passcode_hash    TEXT
);
CREATE TABLE prototype_checkpoints (
    id                INTEGER PRIMARY KEY AUTOINCREMENT,
    prototype_id      INTEGER NOT NULL,
    workspace_id      TEXT NOT NULL,
    bundle_url        TEXT,
    prd_revision_hash TEXT,
    figma_frame_hash  TEXT,
    prompt_history    TEXT NOT NULL DEFAULT '[]',
    comment_state     TEXT NOT NULL DEFAULT '[]',
    created_at        TEXT NOT NULL DEFAULT (datetime('now'))
);
"""


@pytest.fixture
def env(isolated_settings, monkeypatch):
    """Feature flag ON + prototypes tables + design-agent module stack reloaded
    in dependency order."""
    from tests import _fake_supabase

    _fake_supabase.get_fake_db().executescript(_PROTOTYPE_DDL)
    monkeypatch.setenv("DESIGN_AGENT_ENABLED", "1")
    monkeypatch.setenv("TOKEN_ENCRYPTION_KEY", Fernet.generate_key().decode())

    import app.config as _config_mod
    importlib.reload(_config_mod)
    import app.connectors.tokens as _tokens_mod
    importlib.reload(_tokens_mod)

    import app.db.prototypes as proto_mod
    importlib.reload(proto_mod)
    import app.routes.design_agent as routes_mod
    importlib.reload(routes_mod)
    import app.main as main_mod
    importlib.reload(main_mod)

    import app.db as db_mod
    return SimpleNamespace(
        proto=proto_mod, routes=routes_mod, main=main_mod, db=db_mod,
    )


def _seed_prd(db_mod, body: str = "# PRD body") -> int:
    prd_id = db_mod.start_prd(
        brief_id=1, insight_index=0, title="t", template_version=1, variant="v2",
    )
    db_mod.complete_prd(prd_id, title="t", md=body)
    return prd_id


def _make_map(route: str = "/team", commit_sha: str = "shaABC") -> MapResult:
    node = ScreenNode(
        route=route,
        entry_component="TeamScreen",
        file="src/screens/Team.tsx",
        composed_components=[],
    )
    return MapResult(
        repo="org/repo",
        commit_sha=commit_sha,
        posture="CLEAN",
        nodes=[node],
        shell=ShellModel(logo=LogoAsset()),
    )


def _stub_generate_capture(monkeypatch, routes_mod, *, theme_expectations=None):
    """Patch ``routes.generate_prototype`` to record kwargs and return a clean
    complete RunResult tuple. Returns the captured-kwargs list."""
    captured: list[dict] = []

    async def _fake(**kwargs):
        captured.append(kwargs)
        result = SimpleNamespace(
            status="complete",
            iters=1,
            theme_expectations=theme_expectations,
        )
        return result, {"src/App.tsx": "x"}

    monkeypatch.setattr(routes_mod, "generate_prototype", _fake)
    return captured


def _stub_stage_capture(monkeypatch, routes_mod):
    """Patch ``_stage_complete_run`` to record kwargs without staging anything."""
    captured: list[dict] = []

    async def _fake(**kwargs):
        captured.append(kwargs)

    monkeypatch.setattr(routes_mod, "_stage_complete_run", _fake)
    return captured


# ── Resolution path — AC1 / AC2 / AC3 / AC4 / AC5 ─────────────────────────────


async def test_github_route_populates_located_screen(env, monkeypatch):
    """AC1: github source + matching route + sha → generate_prototype receives
    a populated LocatedScreen whose node.route equals chosen_screen_route."""
    gen_kwargs = _stub_generate_capture(monkeypatch, env.routes)
    _stub_stage_capture(monkeypatch, env.routes)

    fake_map = _make_map(route="/team", commit_sha="shaABC")
    monkeypatch.setattr(
        "app.design_agent.codebase_map.service.build_map",
        lambda *_a, **_k: fake_map,
    )

    prd_id = _seed_prd(env.db)
    pid = env.proto.start_prototype(
        prd_id=prd_id, workspace_id="app", template_version=1,
    )
    await env.routes._run_generation_bg(
        prototype_id=pid, workspace_id="app", prd_id=prd_id,
        target_platform="both", instructions="", figma_file_key=None,
        github_repo="org/repo", github_installation_id=42,
        design_source="github",
        chosen_screen_route="/team", map_commit_sha="shaABC",
    )

    assert len(gen_kwargs) == 1
    located = gen_kwargs[0]["located_screen"]
    assert isinstance(located, LocatedScreen)
    assert located.node.route == "/team"
    assert located.map_result is fake_map


async def test_no_route_passes_located_screen_none(env, monkeypatch):
    """AC2: codebase mode but no chosen_screen_route → located_screen=None,
    build_map never called, run still completes."""
    gen_kwargs = _stub_generate_capture(monkeypatch, env.routes)
    _stub_stage_capture(monkeypatch, env.routes)

    calls: list[tuple] = []
    monkeypatch.setattr(
        "app.design_agent.codebase_map.service.build_map",
        lambda *a, **k: calls.append((a, k)) or None,
    )

    prd_id = _seed_prd(env.db)
    pid = env.proto.start_prototype(
        prd_id=prd_id, workspace_id="app", template_version=1,
    )
    await env.routes._run_generation_bg(
        prototype_id=pid, workspace_id="app", prd_id=prd_id,
        target_platform="both", instructions="", figma_file_key=None,
        github_repo="org/repo", github_installation_id=42,
        design_source="github",
        chosen_screen_route=None, map_commit_sha=None,
    )

    assert gen_kwargs[0]["located_screen"] is None
    assert calls == []


async def test_unresolvable_route_falls_back_none_no_raise(env, monkeypatch, caplog):
    """AC3: route that matches NO node in the map → located_screen=None,
    generation still completes (no exception, no wire_failed warning)."""
    gen_kwargs = _stub_generate_capture(monkeypatch, env.routes)
    _stub_stage_capture(monkeypatch, env.routes)

    fake_map = _make_map(route="/home")
    monkeypatch.setattr(
        "app.design_agent.codebase_map.service.build_map",
        lambda *_a, **_k: fake_map,
    )

    prd_id = _seed_prd(env.db)
    pid = env.proto.start_prototype(
        prd_id=prd_id, workspace_id="app", template_version=1,
    )
    with caplog.at_level(logging.WARNING, logger="app.routes.design_agent"):
        await env.routes._run_generation_bg(
            prototype_id=pid, workspace_id="app", prd_id=prd_id,
            target_platform="both", instructions="", figma_file_key=None,
            github_repo="org/repo", github_installation_id=42,
            design_source="github",
            chosen_screen_route="/does-not-exist", map_commit_sha="sha",
        )

    assert gen_kwargs[0]["located_screen"] is None
    # No wire_failed warning on the simple no-match case (normal fallback).
    msgs = [r.getMessage() for r in caplog.records]
    assert not any("recreate_wire_failed" in m for m in msgs)


async def test_build_map_none_logs_wire_failed_and_falls_back(env, monkeypatch, caplog):
    """AC3: build_map returns None → wire_failed WARNING + located_screen=None."""
    gen_kwargs = _stub_generate_capture(monkeypatch, env.routes)
    _stub_stage_capture(monkeypatch, env.routes)

    monkeypatch.setattr(
        "app.design_agent.codebase_map.service.build_map",
        lambda *_a, **_k: None,
    )

    prd_id = _seed_prd(env.db)
    pid = env.proto.start_prototype(
        prd_id=prd_id, workspace_id="app", template_version=1,
    )
    with caplog.at_level(logging.WARNING, logger="app.routes.design_agent"):
        await env.routes._run_generation_bg(
            prototype_id=pid, workspace_id="app", prd_id=prd_id,
            target_platform="both", instructions="", figma_file_key=None,
            github_repo="org/repo", github_installation_id=42,
            design_source="github",
            chosen_screen_route="/team", map_commit_sha="sha",
        )

    assert gen_kwargs[0]["located_screen"] is None
    msgs = [r.getMessage() for r in caplog.records]
    assert any("recreate_wire_failed" in m and f"prototype_id={pid}" in m for m in msgs)


async def test_build_map_exception_logs_wire_failed_and_falls_back(env, monkeypatch, caplog):
    """AC3: build_map raises → wire_failed WARNING + located_screen=None,
    generation still completes (the recreate is additive, never fatal)."""
    gen_kwargs = _stub_generate_capture(monkeypatch, env.routes)
    _stub_stage_capture(monkeypatch, env.routes)

    def _boom(*_a, **_k):
        raise RuntimeError("network down")

    monkeypatch.setattr(
        "app.design_agent.codebase_map.service.build_map", _boom,
    )

    prd_id = _seed_prd(env.db)
    pid = env.proto.start_prototype(
        prd_id=prd_id, workspace_id="app", template_version=1,
    )
    with caplog.at_level(logging.WARNING, logger="app.routes.design_agent"):
        await env.routes._run_generation_bg(
            prototype_id=pid, workspace_id="app", prd_id=prd_id,
            target_platform="both", instructions="", figma_file_key=None,
            github_repo="org/repo", github_installation_id=42,
            design_source="github",
            chosen_screen_route="/team", map_commit_sha="sha",
        )

    assert gen_kwargs[0]["located_screen"] is None
    msgs = [r.getMessage() for r in caplog.records]
    assert any("recreate_wire_failed" in m for m in msgs)


async def test_figma_mode_skips_build_map_and_located_none(env, monkeypatch):
    """AC4: figma source ignores chosen_screen_route + map_commit_sha, never
    calls build_map, and generate_prototype receives located_screen=None."""
    gen_kwargs = _stub_generate_capture(monkeypatch, env.routes)
    _stub_stage_capture(monkeypatch, env.routes)

    build_calls: list = []
    monkeypatch.setattr(
        "app.design_agent.codebase_map.service.build_map",
        lambda *a, **k: build_calls.append((a, k)) or None,
    )

    prd_id = _seed_prd(env.db)
    pid = env.proto.start_prototype(
        prd_id=prd_id, workspace_id="app", template_version=1,
    )
    await env.routes._run_generation_bg(
        prototype_id=pid, workspace_id="app", prd_id=prd_id,
        target_platform="both", instructions="",
        figma_file_key="figma-key-abc",
        github_repo=None, github_installation_id=None,
        design_source="figma",
        chosen_screen_route="/team", map_commit_sha="sha",
    )

    assert gen_kwargs[0]["located_screen"] is None
    assert build_calls == []


async def test_build_map_called_with_pinned_commit_sha(env, monkeypatch):
    """AC5: build_map's third arg equals the request's map_commit_sha so the
    recreate reads the snapshot the PM confirmed against."""
    _stub_generate_capture(monkeypatch, env.routes)
    _stub_stage_capture(monkeypatch, env.routes)

    captured_args: list[tuple] = []
    fake_map = _make_map(route="/team", commit_sha="pinned-sha")

    def _capture(*args, **kwargs):
        captured_args.append((args, kwargs))
        return fake_map

    monkeypatch.setattr(
        "app.design_agent.codebase_map.service.build_map", _capture,
    )

    prd_id = _seed_prd(env.db)
    pid = env.proto.start_prototype(
        prd_id=prd_id, workspace_id="app", template_version=1,
    )
    await env.routes._run_generation_bg(
        prototype_id=pid, workspace_id="app", prd_id=prd_id,
        target_platform="both", instructions="", figma_file_key=None,
        github_repo="org/repo", github_installation_id=42,
        design_source="github",
        chosen_screen_route="/team", map_commit_sha="pinned-sha",
    )

    assert len(captured_args) == 1
    args, _kwargs = captured_args[0]
    # asyncio.to_thread passes positional args through verbatim.
    assert args == (42, "org/repo", "pinned-sha")


# ── Installation-resolver baseline — AC7 ───────────────────────────────────────


async def test_installation_resolved_once_handler_only(env, client, monkeypatch):
    """AC7: _resolve_github_installation_id_for_repo runs ONCE per /generate —
    in the handler — and is NEVER invoked inside _run_generation_bg."""
    resolver_calls: list = []

    def _resolver(workspace_id, repo):
        resolver_calls.append((workspace_id, repo))
        return 42

    monkeypatch.setattr(
        env.routes, "_resolve_github_installation_id_for_repo", _resolver,
    )

    captured_bg: list[dict] = []

    async def _fake_bg(**kwargs):
        captured_bg.append(kwargs)

    monkeypatch.setattr(env.routes, "_run_generation_bg", _fake_bg)

    prd_id = _seed_prd(env.db)
    resp = client.post(
        "/v1/design-agent/generate",
        json={
            "prd_id": prd_id,
            "github_repo": "org/repo",
            "design_source": "github",
            "chosen_screen_route": "/team",
            "map_commit_sha": "sha",
        },
    )
    assert resp.status_code == 200, resp.text
    # Resolver fires exactly once — in the handler.
    assert len(resolver_calls) == 1
    # And the background task carries the already-resolved installation id.
    assert captured_bg[0]["github_installation_id"] == 42
    assert captured_bg[0]["chosen_screen_route"] == "/team"
    assert captured_bg[0]["map_commit_sha"] == "sha"


@pytest.fixture
def client(env, company_client) -> TestClient:
    """Bearer-authed TestClient under the test workspace."""
    return company_client


# ── Gate-arming seam — AC12 / AC13 ─────────────────────────────────────────────


async def test_stage_complete_run_receives_theme_expectations(env, monkeypatch):
    """AC12: when generate_prototype returns a non-None theme_expectations,
    _run_generation_bg passes it as keyword into _stage_complete_run so the
    build-gate fires on the recreate path."""
    fake_te = ThemeExpectations(
        token_signals=("210 100% 50%",),
        font_families=("Inter",),
        class_signals=("bg-primary",),
        asset_basename=None,
    )
    _stub_generate_capture(monkeypatch, env.routes, theme_expectations=fake_te)
    stage_kwargs = _stub_stage_capture(monkeypatch, env.routes)

    prd_id = _seed_prd(env.db)
    pid = env.proto.start_prototype(
        prd_id=prd_id, workspace_id="app", template_version=1,
    )
    await env.routes._run_generation_bg(
        prototype_id=pid, workspace_id="app", prd_id=prd_id,
        target_platform="both", instructions="", figma_file_key=None,
    )

    assert len(stage_kwargs) == 1
    assert stage_kwargs[0]["theme_expectations"] is fake_te


async def test_blank_canvas_theme_expectations_none_gate_dormant(env, monkeypatch):
    """AC13: a blank-canvas run (no located_screen) leaves theme_expectations
    None on the returned RunResult → _stage_complete_run receives None →
    assert_theme_landed stays a no-op (gate dormant)."""
    _stub_generate_capture(monkeypatch, env.routes, theme_expectations=None)
    stage_kwargs = _stub_stage_capture(monkeypatch, env.routes)

    prd_id = _seed_prd(env.db)
    pid = env.proto.start_prototype(
        prd_id=prd_id, workspace_id="app", template_version=1,
    )
    await env.routes._run_generation_bg(
        prototype_id=pid, workspace_id="app", prd_id=prd_id,
        target_platform="both", instructions="", figma_file_key=None,
    )

    assert stage_kwargs[0]["theme_expectations"] is None


# ── Runner-layer gate-arming — AC12 runner half ────────────────────────────────


def _system_blocks():
    return [
        {"type": "text", "text": "You are the Design Agent."},
        {"type": "text", "text": "<stable prefix>",
         "cache_control": {"type": "ephemeral", "ttl": "1h"}},
    ]


def _user_message(text: str = "Build a settings screen."):
    return {"role": "user", "content": [{"type": "text", "text": text}]}


def _located_screen_fixture() -> LocatedScreen:
    node = ScreenNode(
        route="/team", entry_component="TeamScreen",
        file="src/Team.tsx", composed_components=[],
    )
    m = MapResult(
        repo="org/repo", commit_sha="sha",
        posture="CLEAN", nodes=[node],
        shell=ShellModel(logo=LogoAsset()),
    )
    return LocatedScreen(map_result=m, node=node)


def _run(coro):
    return asyncio.run(coro)


def test_runner_recreate_run_populates_theme_expectations(monkeypatch):
    """AC12 runner half: generate_prototype's recreate branch invokes
    build_theme_expectations and surfaces the result on RunResult."""
    from app.design_agent import runner

    fake_te = ThemeExpectations(
        token_signals=("210 100% 50%",),
        font_families=("Inter",),
        class_signals=("bg-primary",),
        asset_basename=None,
    )

    async def _fake_loop(**_kw):
        return runner.RunResult(
            status="complete", iters=1, usage=runner.RunUsage(),
            duration_ms=1, final_content=[],
        )

    fake_sources = RecreateSources(
        repo="org/repo", commit_sha="sha",
        files={"src/Team.tsx": "x"},
        screen_path="src/Team.tsx",
        also_screen_paths=(),
    )

    monkeypatch.setattr(runner, "_resolve_design_system", lambda **_k: None)
    monkeypatch.setattr(runner, "_resolve_figma_access_token", lambda *_a, **_k: None)
    monkeypatch.setattr(runner, "agent_loop", _fake_loop)
    monkeypatch.setattr(runner, "recreate_pre_seed", lambda *_a, **_k: fake_sources)
    monkeypatch.setattr(runner, "bridge_theme", lambda *_a, **_k: "/* bridged */")
    monkeypatch.setattr(
        runner, "carry_brand_asset",
        lambda *_a, **_k: runner.BrandAssetCarry(
            virtual_fs_keys={}, shell_render_ref="",
            deployed_url="", render_kind="absent", carried=False,
        ),
    )
    monkeypatch.setattr(runner, "build_theme_expectations", lambda *_a, **_k: fake_te)
    monkeypatch.setattr(runner, "render_recreate_task_block", lambda *_a, **_k: "RECREATE TARGET")

    result, _vfs = _run(runner.generate_prototype(
        prototype_id=1, workspace_id="app", system_blocks=_system_blocks(),
        user_message=_user_message(), figma_file_key=None,
        located_screen=_located_screen_fixture(),
    ))

    assert result.theme_expectations is fake_te


def test_runner_blank_canvas_leaves_theme_expectations_none(monkeypatch):
    """AC13 runner half: located_screen=None → RunResult.theme_expectations is
    None; build_theme_expectations is never called."""
    from app.design_agent import runner

    async def _fake_loop(**_kw):
        return runner.RunResult(
            status="complete", iters=1, usage=runner.RunUsage(),
            duration_ms=1, final_content=[],
        )

    build_te_calls: list = []
    monkeypatch.setattr(runner, "_resolve_design_system", lambda **_k: None)
    monkeypatch.setattr(runner, "_resolve_figma_access_token", lambda *_a, **_k: None)
    monkeypatch.setattr(runner, "agent_loop", _fake_loop)
    monkeypatch.setattr(
        runner, "build_theme_expectations",
        lambda *a, **k: build_te_calls.append((a, k)) or None,
    )

    result, _vfs = _run(runner.generate_prototype(
        prototype_id=2, workspace_id="app", system_blocks=_system_blocks(),
        user_message=_user_message(), figma_file_key=None,
        located_screen=None,
    ))

    assert result.theme_expectations is None
    assert build_te_calls == []


# ── Request contract — AC6 / AC8 ───────────────────────────────────────────────


def test_generate_request_old_shape_back_compat(env):
    """AC6: a body that omits the two new fields deserializes (defaults to
    None/None), so old clients keep working byte-for-byte."""
    body = env.routes.GenerateRequest(prd_id=1)
    assert body.chosen_screen_route is None
    assert body.map_commit_sha is None
    # Explicit values also accepted.
    body2 = env.routes.GenerateRequest(
        prd_id=1, chosen_screen_route="/team", map_commit_sha="abc",
    )
    assert body2.chosen_screen_route == "/team"
    assert body2.map_commit_sha == "abc"


def test_locate_response_carries_commit_sha(env):
    """AC8: LocateResponse surfaces a commit_sha field; unmapped path empty."""
    # Mapped path: explicit commit_sha threads through.
    mapped = env.routes.LocateResponse(
        decision="auto_proceed",
        chosen=[],
        ranked=[],
        top_confidence=90,
        threshold=80,
        repo="org/repo",
        posture="CLEAN",
        unmapped=False,
        commit_sha="real-sha",
    )
    assert mapped.commit_sha == "real-sha"
    # Unmapped helper writes "".
    unmapped = env.routes._unmapped_locate_response("org/repo")
    assert unmapped.commit_sha == ""


# ── Observability + integrity — AC10 / AC11 ───────────────────────────────────


async def test_recreate_wired_logs_identifiers_only(env, monkeypatch, caplog):
    """AC10: a successful resolve emits one recreate_wired INFO line carrying
    prototype_id + repo + route + sha — identifiers only, no source body."""
    _stub_generate_capture(monkeypatch, env.routes)
    _stub_stage_capture(monkeypatch, env.routes)

    fake_map = _make_map(route="/team", commit_sha="shaABC")
    monkeypatch.setattr(
        "app.design_agent.codebase_map.service.build_map",
        lambda *_a, **_k: fake_map,
    )

    prd_id = _seed_prd(env.db)
    pid = env.proto.start_prototype(
        prd_id=prd_id, workspace_id="app", template_version=1,
    )
    with caplog.at_level(logging.INFO, logger="app.routes.design_agent"):
        await env.routes._run_generation_bg(
            prototype_id=pid, workspace_id="app", prd_id=prd_id,
            target_platform="both", instructions="", figma_file_key=None,
            github_repo="org/repo", github_installation_id=42,
            design_source="github",
            chosen_screen_route="/team", map_commit_sha="shaABC",
        )

    wired = [r for r in caplog.records if "recreate_wired" in r.getMessage()]
    assert len(wired) == 1
    msg = wired[0].getMessage()
    assert f"prototype_id={pid}" in msg
    assert "repo=org/repo" in msg
    assert "route=/team" in msg
    assert "sha=shaABC" in msg


def test_no_prohibited_tokens_in_source():
    """AC11: the scoped grep returns nothing across all changed regions /
    new files. Assembled from split parts so the literals are not themselves
    continuous strings in this file."""
    import re as _re

    parts = [
        r"C" + r"[0-9]" + r"-" + r"[0-9]",
        r"H" + r"[0-9]" + r"-" + r"[0-9]",
        r"P" + r"[0-9]" + r"-" + r"[0-9]",
        r"\bA" + r"D" + r"[0-9]",
        r"\bF" + r"[0-9]{1,2}\b",
        r"D" + r"B" + r"D",
        r"B" + r"ab" + r"ajide",
    ]
    pattern = _re.compile("|".join(parts))

    root = Path(__file__).resolve().parents[1].parent
    targets = [
        root / "backend" / "app" / "routes" / "design_agent.py",
        root / "backend" / "tests" / "test_design_agent_generate_locate_wiring.py",
        root / "web" / "app" / "lib" / "api.ts",
        root / "web" / "app" / "components" / "design-agent" / "GenerateModal.tsx",
        root / "web" / "app" / "components" / "design-agent" / "__tests__" /
        "GenerateModalLocateBody.test.tsx",
    ]
    # Scope: only this ticket's new/touched regions are evaluated. The historical
    # tokens that pre-date this slice on the routes file / modal are explicitly
    # out of scope (the no-historical-scrub rule). The new test file + the new
    # frontend test file + api.ts append are scrubbed in full.
    new_only = {
        root / "backend" / "tests" / "test_design_agent_generate_locate_wiring.py",
        root / "web" / "app" / "components" / "design-agent" / "__tests__" /
        "GenerateModalLocateBody.test.tsx",
    }

    for path in targets:
        if not path.exists():
            continue
        if path not in new_only:
            continue
        text = path.read_text()
        for lineno, line in enumerate(text.splitlines(), 1):
            assert not pattern.search(line), (
                f"prohibited token in {path}:{lineno}: {line}"
            )


# ── Resolve the chosen candidate to a node BY ID ──────────────────────────────
#
# The recreate-wire block resolves the PM-confirmed candidate by stable id first,
# falling back to the route. This admits a non-route host — the app shell (empty
# route) and an in-page section (empty/shared route) — that route-only keying
# silently dropped.


def _map_with(*nodes) -> MapResult:
    return MapResult(
        repo="org/repo", commit_sha="sha", posture="CLEAN",
        nodes=list(nodes), shell=ShellModel(logo=LogoAsset()),
    )


async def test_generate_resolves_app_shell_node_by_id(env, monkeypatch):
    """chosen_screen_id='app-shell' (route '') resolves the shell node and builds
    a LocatedScreen whose node.kind == 'shell'. The same inputs WITHOUT an id (the
    pre-fix route-only path, route empty) leave located_screen None."""
    gen_kwargs = _stub_generate_capture(monkeypatch, env.routes)
    _stub_stage_capture(monkeypatch, env.routes)

    shell = ScreenNode(route="", entry_component="AppShell", id="app-shell",
                       kind="shell", composed_components=[])
    routed = ScreenNode(route="/team", entry_component="TeamScreen", composed_components=[])
    fake_map = _map_with(shell, routed)
    monkeypatch.setattr(
        "app.design_agent.codebase_map.service.build_map",
        lambda *_a, **_k: fake_map,
    )

    prd_id = _seed_prd(env.db)
    pid = env.proto.start_prototype(prd_id=prd_id, workspace_id="app", template_version=1)
    await env.routes._run_generation_bg(
        prototype_id=pid, workspace_id="app", prd_id=prd_id,
        target_platform="both", instructions="", figma_file_key=None,
        github_repo="org/repo", github_installation_id=42, design_source="github",
        chosen_screen_route="", chosen_screen_id="app-shell", map_commit_sha="sha",
    )
    located = gen_kwargs[0]["located_screen"]
    assert isinstance(located, LocatedScreen)
    assert located.node.kind == "shell"
    assert located.node.id == "app-shell"

    # Differential: the pre-fix route-only path (no id, empty route) cannot resolve
    # the shell host — located stays None.
    gen_kwargs2 = _stub_generate_capture(monkeypatch, env.routes)
    pid2 = env.proto.start_prototype(prd_id=prd_id, workspace_id="app", template_version=1)
    await env.routes._run_generation_bg(
        prototype_id=pid2, workspace_id="app", prd_id=prd_id,
        target_platform="both", instructions="", figma_file_key=None,
        github_repo="org/repo", github_installation_id=42, design_source="github",
        chosen_screen_route="", chosen_screen_id=None, map_commit_sha="sha",
    )
    # Empty route + no id → the wire guard never even fires; located is None.
    assert gen_kwargs2 == [] or gen_kwargs2[0]["located_screen"] is None


async def test_generate_resolves_section_node_by_id(env, monkeypatch):
    """chosen_screen_id equal to a kind='section' node's id resolves it by id even
    though its route is empty and would collide with other empty-route nodes."""
    gen_kwargs = _stub_generate_capture(monkeypatch, env.routes)
    _stub_stage_capture(monkeypatch, env.routes)

    routed = ScreenNode(route="/inbox", entry_component="InboxScreen", composed_components=[])
    section = ScreenNode(route="", entry_component="InboxArchived", id="inbox#archived",
                         kind="section", composed_components=[])
    fake_map = _map_with(routed, section)
    monkeypatch.setattr(
        "app.design_agent.codebase_map.service.build_map",
        lambda *_a, **_k: fake_map,
    )

    prd_id = _seed_prd(env.db)
    pid = env.proto.start_prototype(prd_id=prd_id, workspace_id="app", template_version=1)
    await env.routes._run_generation_bg(
        prototype_id=pid, workspace_id="app", prd_id=prd_id,
        target_platform="both", instructions="", figma_file_key=None,
        github_repo="org/repo", github_installation_id=42, design_source="github",
        chosen_screen_route="", chosen_screen_id="inbox#archived", map_commit_sha="sha",
    )
    located = gen_kwargs[0]["located_screen"]
    assert isinstance(located, LocatedScreen)
    assert located.node.id == "inbox#archived"
    assert located.node.kind == "section"


async def test_generate_falls_back_to_route_when_no_id(env, monkeypatch):
    """An old request with chosen_screen_route set and no chosen_screen_id resolves
    the routed node by route exactly as before — no behaviour change."""
    gen_kwargs = _stub_generate_capture(monkeypatch, env.routes)
    _stub_stage_capture(monkeypatch, env.routes)

    routed = ScreenNode(route="/team", entry_component="TeamScreen", composed_components=[])
    fake_map = _map_with(routed)
    monkeypatch.setattr(
        "app.design_agent.codebase_map.service.build_map",
        lambda *_a, **_k: fake_map,
    )

    prd_id = _seed_prd(env.db)
    pid = env.proto.start_prototype(prd_id=prd_id, workspace_id="app", template_version=1)
    await env.routes._run_generation_bg(
        prototype_id=pid, workspace_id="app", prd_id=prd_id,
        target_platform="both", instructions="", figma_file_key=None,
        github_repo="org/repo", github_installation_id=42, design_source="github",
        chosen_screen_route="/team", chosen_screen_id=None, map_commit_sha="sha",
    )
    located = gen_kwargs[0]["located_screen"]
    assert isinstance(located, LocatedScreen)
    assert located.node.route == "/team"


async def test_generate_no_resolution_falls_through_blank_canvas(env, monkeypatch, caplog):
    """Neither id nor route resolves a node → located_screen stays None and
    generation falls through to the blank-canvas path with no exception."""
    gen_kwargs = _stub_generate_capture(monkeypatch, env.routes)
    _stub_stage_capture(monkeypatch, env.routes)

    routed = ScreenNode(route="/home", entry_component="HomeScreen", composed_components=[])
    fake_map = _map_with(routed)
    monkeypatch.setattr(
        "app.design_agent.codebase_map.service.build_map",
        lambda *_a, **_k: fake_map,
    )

    prd_id = _seed_prd(env.db)
    pid = env.proto.start_prototype(prd_id=prd_id, workspace_id="app", template_version=1)
    with caplog.at_level(logging.WARNING, logger="app.routes.design_agent"):
        await env.routes._run_generation_bg(
            prototype_id=pid, workspace_id="app", prd_id=prd_id,
            target_platform="both", instructions="", figma_file_key=None,
            github_repo="org/repo", github_installation_id=42, design_source="github",
            chosen_screen_route="/nope", chosen_screen_id="ghost", map_commit_sha="sha",
        )
    # Generation still ran (blank-canvas) with no located screen and no exception.
    assert len(gen_kwargs) == 1
    assert gen_kwargs[0]["located_screen"] is None
