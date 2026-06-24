"""Unit tests for the recreate seam in ``generate_prototype``.

These tests stub ``agent_loop`` + ``_resolve_design_system`` + ``read_repo`` so
they exercise the seam without touching the LLM client or the GitHub App.
They verify:

- The seam is INERT when ``located_screen=None`` (byte-identical user_message,
  no ``__reference__/*`` keys in the returned virtual filesystem).
- The seam INJECTS reference files + rewrites the user prompt when a located
  screen is supplied and ``read_repo`` returns sources.
- Reference files are STRIPPED from the build-facing virtual filesystem.
- An unreadable repo (``read_repo`` returns None) leaves the token / primitive
  pre-seed in place and logs a WARNING.
- The recreate INFO log line carries identifiers + counts only — never a body.
- Existing call-sites compile against the optional keyword param.
- The source files contain no internal engagement coordinates.

Plain-engineering note: the prohibited-tokens test assembles its pattern from
split parts so the literals it checks for are not themselves continuous
strings in this file.
"""
from __future__ import annotations

import asyncio
import ast
import copy
import logging
import re
import subprocess
import sys
import types
from pathlib import Path
from unittest.mock import patch

import pytest

from app.design_agent import runner
from app.design_agent.codebase_map.recreate import LocatedScreen
from app.design_agent.codebase_map.repo_reader import RepoSnapshot
from app.design_agent.codebase_map.types import (
    LogoAsset,
    MapResult,
    ScreenNode,
    ShellModel,
)
from app.design_agent.runner import RunResult, generate_prototype


_REPO = "org/repo"
_SHA = "abc123def456"


# ── helpers ─────────────────────────────────────────────────────────────────────


def _run(coro):
    return asyncio.run(coro)


def _system():
    return [
        {"type": "text", "text": "You are the Design Agent. Build prototypes."},
        {
            "type": "text",
            "text": "<stable prefix>",
            "cache_control": {"type": "ephemeral", "ttl": "1h"},
        },
    ]


def _user(text: str = "Build a settings screen."):
    return {"role": "user", "content": [{"type": "text", "text": text}]}


def _located_screen() -> LocatedScreen:
    home = ScreenNode(
        route="/",
        entry_component="Home",
        file="src/Home.tsx",
        composed_components=["Hero"],
    )
    hero = ScreenNode(
        route="",
        entry_component="Hero",
        file="src/Hero.tsx",
        composed_components=[],
    )
    m = MapResult(
        repo=_REPO,
        commit_sha=_SHA,
        posture="CLEAN",
        nodes=[home, hero],
        shell=ShellModel(logo=LogoAsset()),
    )
    return LocatedScreen(map_result=m, node=home)


def _snapshot(files: dict[str, str]) -> RepoSnapshot:
    return RepoSnapshot(
        repo=_REPO,
        commit_sha=_SHA,
        branch="main",
        tree_paths=list(files.keys()),
        files=dict(files),
        truncated=False,
    )


def _stub_agent_loop_capture():
    """Return a (fake_loop, captured) pair. fake_loop records its ctx and the
    user_message it received, then returns a clean complete RunResult so the
    runner's cost-summary path runs end-to-end without an Anthropic client."""
    captured: dict = {}

    async def fake_loop(*, system_blocks, user_message, ctx, scenario, mode):
        captured["ctx"] = ctx
        captured["user_message"] = copy.deepcopy(user_message)
        captured["virtual_fs_at_loop_entry"] = dict(ctx.virtual_fs)
        return RunResult(
            status="complete",
            iters=1,
            usage=runner.RunUsage(),
            duration_ms=1,
            final_content=[],
        )

    return fake_loop, captured


def _stub_design_system(monkeypatch, *, present: bool = False):
    """Force ``_resolve_design_system`` + ``_should_pre_seed`` paths.

    present=False: design_system resolves to None — _should_pre_seed returns
    False, no design-system index.css / primitives are seeded. The recreate
    branch still runs (it is gated only on located_screen)."""
    monkeypatch.setattr(runner, "_resolve_design_system", lambda **_kw: None)
    monkeypatch.setattr(runner, "_resolve_figma_access_token", lambda *_a, **_k: None)


# ── AC1: inert when located_screen=None ─────────────────────────────────────────


def test_generate_inert_when_located_screen_none(monkeypatch):
    """AC1: with located_screen=None the user_message keeps its original
    content and the returned virtual_fs has no ``__reference__/*`` keys."""
    _stub_design_system(monkeypatch)
    fake_loop, captured = _stub_agent_loop_capture()
    monkeypatch.setattr(runner, "agent_loop", fake_loop)

    user_msg = _user("ORIGINAL_PROMPT")
    original_content = copy.deepcopy(user_msg["content"])

    result, vfs = _run(generate_prototype(
        prototype_id=1, workspace_id="app", system_blocks=_system(),
        user_message=user_msg, figma_file_key=None, scenario="A",
        located_screen=None,
    ))

    assert result.status == "complete"
    # user_message reaching the loop is byte-identical to the original
    assert captured["user_message"]["content"] == original_content
    # virtual_fs has no recreate residue
    assert all(not k.startswith("__reference__/") for k in vfs.keys())
    # No "RECREATE TARGET" preamble injected
    assert not any(
        isinstance(b.get("text"), str) and "RECREATE TARGET" in b["text"]
        for b in captured["user_message"]["content"]
    )


# ── AC4: inject references + rewrite prompt ─────────────────────────────────────


def test_generate_injects_references_and_rewrites_prompt(monkeypatch):
    """AC4: with located_screen set and the read succeeds, ``virtual_fs``
    contains real sources under ``__reference__/<path>`` keys and the user
    message gains the recreate task block listing each reference path + the
    re-express instruction."""
    _stub_design_system(monkeypatch)
    fake_loop, captured = _stub_agent_loop_capture()
    monkeypatch.setattr(runner, "agent_loop", fake_loop)

    with patch(
        "app.design_agent.codebase_map.recreate.read_repo",
        return_value=_snapshot({
            "src/Home.tsx": "export const Home = () => null",
            "src/Hero.tsx": "export const Hero = () => null",
        }),
    ):
        _run(generate_prototype(
            prototype_id=2, workspace_id="app", system_blocks=_system(),
            user_message=_user("Add a search box."), figma_file_key=None,
            github_installation_id=9001,
            located_screen=_located_screen(),
        ))

    # Reference files were injected into the loop's virtual_fs
    loop_vfs = captured["virtual_fs_at_loop_entry"]
    assert loop_vfs["__reference__/src/Home.tsx"].startswith("export const Home")
    assert loop_vfs["__reference__/src/Hero.tsx"].startswith("export const Hero")
    # The recreate block was appended to user_message content
    texts = [b.get("text", "") for b in captured["user_message"]["content"]]
    blob = "\n".join(texts)
    assert "RECREATE TARGET" in blob
    assert "__reference__/src/Home.tsx" in blob
    assert "__reference__/src/Hero.tsx" in blob
    assert "re-expressed screen" in blob


# ── AC5: reference files stripped before return ─────────────────────────────────


def test_reference_files_stripped_before_return(monkeypatch):
    """AC5: the build-facing virtual_fs returned from ``generate_prototype``
    contains NO ``__reference__/*`` key — the strip happens after the loop
    exits, so vite_build / staging never sees reference bytes."""
    _stub_design_system(monkeypatch)
    fake_loop, _captured = _stub_agent_loop_capture()
    monkeypatch.setattr(runner, "agent_loop", fake_loop)

    with patch(
        "app.design_agent.codebase_map.recreate.read_repo",
        return_value=_snapshot({"src/Home.tsx": "h", "src/Hero.tsx": "x"}),
    ):
        _result, vfs = _run(generate_prototype(
            prototype_id=3, workspace_id="app", system_blocks=_system(),
            user_message=_user(), figma_file_key=None,
            github_installation_id=9001,
            located_screen=_located_screen(),
        ))

    assert all(not k.startswith("__reference__/") for k in vfs.keys())


# ── AC7: unreadable repo keeps token/primitive pre-seed ─────────────────────────


def test_unreadable_repo_keeps_token_pre_seed(monkeypatch, caplog):
    """AC7: when ``read_repo`` returns None, generation continues without the
    recreate block; the user_message is byte-identical to the inert path and
    a WARNING line is logged with the prototype_id."""
    _stub_design_system(monkeypatch)
    fake_loop, captured = _stub_agent_loop_capture()
    monkeypatch.setattr(runner, "agent_loop", fake_loop)

    user_msg = _user("Add a filter.")
    original_content = copy.deepcopy(user_msg["content"])

    with patch(
        "app.design_agent.codebase_map.recreate.read_repo",
        return_value=None,
    ):
        with caplog.at_level(
            logging.WARNING, logger="app.design_agent.codebase_map.recreate"
        ):
            _run(generate_prototype(
                prototype_id=99, workspace_id="app", system_blocks=_system(),
                user_message=user_msg, figma_file_key=None,
                github_installation_id=9001,
                located_screen=_located_screen(),
            ))

    # No recreate residue in either fs or prompt
    loop_vfs = captured["virtual_fs_at_loop_entry"]
    assert all(not k.startswith("__reference__/") for k in loop_vfs.keys())
    assert captured["user_message"]["content"] == original_content

    warnings = [
        r for r in caplog.records
        if r.levelno == logging.WARNING and "design_agent.recreate_pre_seed" in r.getMessage()
    ]
    assert any("prototype_id=99" in r.getMessage() for r in warnings)


# ── AC10: log line carries identifiers + counts only ───────────────────────────


def test_recreate_pre_seed_logs_identifiers_only(monkeypatch, caplog):
    """AC10: the recreate INFO line carries prototype_id + repo + sha + screen
    + n_reference_files + posture. No source body substring, no installation
    token, no PRD content."""
    _stub_design_system(monkeypatch)
    fake_loop, _captured = _stub_agent_loop_capture()
    monkeypatch.setattr(runner, "agent_loop", fake_loop)

    body = "DO_NOT_LEAK_THIS_BODY"
    located = _located_screen()
    with patch(
        "app.design_agent.codebase_map.recreate.read_repo",
        return_value=_snapshot({"src/Home.tsx": body, "src/Hero.tsx": "<<TOKEN_VALUE>>"}),
    ):
        with caplog.at_level(
            logging.INFO, logger="app.design_agent.codebase_map.recreate"
        ):
            _run(generate_prototype(
                prototype_id=123, workspace_id="app", system_blocks=_system(),
                user_message=_user("PRD_BODY_CONTENT_HERE"), figma_file_key=None,
                github_installation_id=9001,
                located_screen=located,
            ))

    recreate_records = [
        r for r in caplog.records
        if "design_agent.recreate_pre_seed" in r.getMessage()
        and r.levelno == logging.INFO
    ]
    assert len(recreate_records) == 1
    msg = recreate_records[0].getMessage()
    # Identifier fields present
    for needle in (
        f"prototype_id=123",
        f"repo={_REPO}",
        f"sha={_SHA}",
        "screen=Home",
        "n_reference_files=2",
        "posture=CLEAN",
    ):
        assert needle in msg, f"missing {needle!r} in {msg!r}"
    # NO source bytes, NO PRD body, NO simulated token
    assert body not in msg
    assert "<<TOKEN_VALUE>>" not in msg
    assert "PRD_BODY_CONTENT_HERE" not in msg


# ── AC8: existing callers still compile with optional keyword ───────────────────


def test_existing_callers_compile_with_optional_param():
    """AC8: ``generate_prototype`` is invoked from other sites with no
    located_screen — the new keyword-optional param must not break them.

    Static check: every ``generate_prototype(`` call across backend/ parses
    without error after the param is added; this is the closest analogue to
    "py_compile still succeeds for each caller" without running them."""
    repo_root = Path(__file__).parent.parent
    result = subprocess.run(
        ["grep", "-rln", "generate_prototype(", str(repo_root / "app")],
        capture_output=True, text=True,
    )
    callers = [Path(p) for p in result.stdout.strip().splitlines() if p]
    assert callers, "expected at least one production caller of generate_prototype"
    for path in callers:
        # Each caller file must parse cleanly.
        text = path.read_text()
        ast.parse(text, filename=str(path))


# ── AC8 (signature): the new param is keyword-optional with a None default ──────


def test_located_screen_param_is_keyword_optional_with_none_default():
    """AC8 (positive): existing positional/keyword call patterns remain valid
    because the new param defaults to None."""
    import inspect
    sig = inspect.signature(generate_prototype)
    param = sig.parameters.get("located_screen")
    assert param is not None
    assert param.default is None
    assert param.kind in (
        inspect.Parameter.KEYWORD_ONLY,
        inspect.Parameter.POSITIONAL_OR_KEYWORD,
    )


# ── Shell-grounded fallback (Tier-2): no located screen ─────────────────────────


def _shell_map(
    *,
    shell_file_path: str = "src/components/Sidebar.tsx",
    logo: LogoAsset | None = None,
    nav: bool = True,
) -> MapResult:
    """A MapResult carrying a non-empty shell but NO node the run will locate."""
    from app.design_agent.codebase_map.types import NavItem

    nav_items = [NavItem(label="Home", order=0, route="/")] if nav else []
    return MapResult(
        repo=_REPO,
        commit_sha=_SHA,
        posture="CLEAN",
        nodes=[],
        shell=ShellModel(
            brand="Acme",
            nav_items=nav_items,
            shell_file_path=shell_file_path,
            logo=logo or LogoAsset(),
        ),
    )


def test_shell_read_helper_without_located_screen(monkeypatch):
    """read_shell_sources returns shell bytes with NO LocatedScreen — the shell
    file + globals land in `files`, screen fields are empty."""
    from app.design_agent.codebase_map.recreate import read_shell_sources

    sm = _shell_map(shell_file_path="src/components/Sidebar.tsx")
    with patch(
        "app.design_agent.codebase_map.recreate.read_repo",
        return_value=_snapshot({
            "src/components/Sidebar.tsx": "export const Sidebar = () => null",
            "src/index.css": ":root { --x: 1; }",
        }),
    ):
        sources = read_shell_sources(sm, installation_id=9001)

    assert sources is not None
    assert sources.screen_path == ""
    assert sources.also_screen_paths == ()
    assert sources.shell_file_path == "src/components/Sidebar.tsx"
    assert "src/components/Sidebar.tsx" in sources.files
    assert sources.files["src/components/Sidebar.tsx"].startswith("export const Sidebar")


def test_shell_read_helper_returns_none_when_no_shell_body(monkeypatch):
    """No readable shell body (only unrelated files) → None, so the caller
    degrades to the design-system-only pre-seed."""
    from app.design_agent.codebase_map.recreate import read_shell_sources

    sm = _shell_map(shell_file_path="src/components/Sidebar.tsx")
    with patch(
        "app.design_agent.codebase_map.recreate.read_repo",
        return_value=_snapshot({"README.md": "nope"}),
    ):
        assert read_shell_sources(sm, installation_id=9001) is None


def test_shell_read_helper_derives_monorepo_prefix(monkeypatch):
    """With a monorepo shell path (``web/app/...``) and NO app_prefix passed,
    read_shell_sources derives the ``web/`` prefix from the carried shell path
    so the prefixed globals/theme candidates resolve — not just the carried
    shell. This is the regression that proves the no-prefix monorepo fix.
    """
    from app.design_agent.codebase_map.recreate import read_shell_sources

    sm = _shell_map(shell_file_path="web/app/components/shared/Sidebar.tsx")
    captured: dict = {}

    def _fake_read_repo(*args, **kwargs):
        captured["frontend_root"] = kwargs.get("frontend_root")
        return _snapshot({
            "web/app/components/shared/Sidebar.tsx": "export const Sidebar = () => null",
            "web/app/globals.css": ":root { --x: 1; }",
            "web/app/layout.tsx": "export default function Layout(){}",
        })

    monkeypatch.setattr(
        "app.design_agent.codebase_map.recreate.read_repo", _fake_read_repo
    )

    sources = read_shell_sources(sm, installation_id=9001)

    assert sources is not None
    # prefix was derived from the carried shell path
    assert sources.app_root_prefix == "web/"
    # globals got read via the derived prefix (not just the carried shell)
    assert "web/app/globals.css" in sources.files
    assert len(sources.files) > 1
    # read_repo was scoped to the derived frontend root
    assert captured["frontend_root"] == "web/"


def test_github_no_screen_injects_shell(monkeypatch):
    """generate_prototype with located_screen=None + a shell_map injects the
    shell `__reference__/*` files, theme bridge, and brand logo into the loop's
    virtual_fs. Fails on the pre-fallback code, passes after."""
    _stub_design_system(monkeypatch)
    fake_loop, captured = _stub_agent_loop_capture()
    monkeypatch.setattr(runner, "agent_loop", fake_loop)

    sm = _shell_map(
        shell_file_path="src/components/Sidebar.tsx",
        logo=LogoAsset(render_kind="text", asset_ref="Acme"),
    )
    with patch(
        "app.design_agent.codebase_map.recreate.read_repo",
        return_value=_snapshot({
            "src/components/Sidebar.tsx": "export const Sidebar = () => <nav>Home</nav>",
            "src/index.css": ":root { --brand: #123456; }",
        }),
    ):
        _run(generate_prototype(
            prototype_id=200, workspace_id="app", system_blocks=_system(),
            user_message=_user("Add a reports screen."), figma_file_key=None,
            github_repo=_REPO, github_installation_id=9001,
            design_source="github",
            located_screen=None, shell_map=sm,
        ))

    loop_vfs = captured["virtual_fs_at_loop_entry"]
    assert loop_vfs["__reference__/src/components/Sidebar.tsx"].startswith("export const Sidebar")
    # Theme bridged into index.css (the real --brand token inlined after the scaffold).
    assert "--brand: #123456" in loop_vfs["src/index.css"]
    # The shell task block was appended to the user message.
    texts = [b.get("text", "") for b in captured["user_message"]["content"]]
    blob = "\n".join(texts)
    assert "APP SHELL" in blob
    assert "__reference__/src/components/Sidebar.tsx" in blob


def test_github_no_screen_still_applies_design_system(monkeypatch):
    """Tier-2 still runs the design-system pre-seed: a real-ish design system
    seeds src/index.css + the design-brief block, alongside the shell bridge."""
    fake_loop, captured = _stub_agent_loop_capture()
    monkeypatch.setattr(runner, "agent_loop", fake_loop)
    monkeypatch.setattr(runner, "_resolve_figma_access_token", lambda *_a, **_k: None)

    # A design system that pre-seeds CSS + a brief, with deterministic primitives.
    monkeypatch.setattr(runner, "_resolve_design_system", lambda **_k: object())
    monkeypatch.setattr(runner, "_should_pre_seed", lambda _ds: True)
    monkeypatch.setattr(
        runner, "_render_design_system_css", lambda _ds: "/* DS_MARKER */ body{}"
    )
    monkeypatch.setattr(
        runner, "_render_design_brief_block", lambda _ds: "DESIGN_BRIEF_MARKER"
    )
    # GitHub primitives extraction is patched off (no live GitHub App).
    import app.design_agent.design_system.adapters as adapters_mod
    monkeypatch.setattr(
        adapters_mod.GithubExtractor, "extract_ui_primitives",
        lambda self, _ref: {"src/components/ui/button.tsx": "export const Button = () => null"},
    )

    sm = _shell_map(shell_file_path="src/components/Sidebar.tsx")
    with patch(
        "app.design_agent.codebase_map.recreate.read_repo",
        return_value=_snapshot({
            "src/components/Sidebar.tsx": "export const Sidebar = () => null",
            "src/index.css": ":root { --brand: #654321; }",
        }),
    ):
        _run(generate_prototype(
            prototype_id=201, workspace_id="app", system_blocks=_system(),
            user_message=_user("Add a billing screen."), figma_file_key=None,
            github_repo=_REPO, github_installation_id=9001,
            design_source="github",
            located_screen=None, shell_map=sm,
        ))

    loop_vfs = captured["virtual_fs_at_loop_entry"]
    # The design-system CSS pre-seed ran (its marker is in the seeded index.css),
    # then the shell theme bridge inlined the real --brand token on top of it —
    # proving both the design-system pre-seed AND the shell bridge coexist on Tier-2.
    assert "DS_MARKER" in loop_vfs["src/index.css"]
    assert "--brand: #654321" in loop_vfs["src/index.css"]
    # Both the design brief AND the shell block reached the prompt.
    blob = "\n".join(b.get("text", "") for b in captured["user_message"]["content"])
    assert "DESIGN_BRIEF_MARKER" in blob
    assert "APP SHELL" in blob


def test_github_no_shell_falls_back_to_design_system_only(monkeypatch):
    """A shell_map with an EMPTY shell (no file, no nav, no logo) injects no
    shell reference, raises nothing, and generation proceeds (Tier-3)."""
    _stub_design_system(monkeypatch)
    fake_loop, captured = _stub_agent_loop_capture()
    monkeypatch.setattr(runner, "agent_loop", fake_loop)

    empty_shell_map = MapResult(
        repo=_REPO, commit_sha=_SHA, posture="CLEAN", nodes=[],
        shell=ShellModel(logo=LogoAsset()),  # render_kind 'absent', no path, no nav
    )
    user_msg = _user("Add a screen.")
    original_content = copy.deepcopy(user_msg["content"])

    result, vfs = _run(generate_prototype(
        prototype_id=202, workspace_id="app", system_blocks=_system(),
        user_message=user_msg, figma_file_key=None,
        github_repo=_REPO, github_installation_id=9001,
        design_source="github",
        located_screen=None, shell_map=empty_shell_map,
    ))

    assert result.status == "complete"
    loop_vfs = captured["virtual_fs_at_loop_entry"]
    assert all(not k.startswith("__reference__/") for k in loop_vfs.keys())
    assert all(not k.startswith("__reference__/") for k in vfs.keys())
    # No shell block appended — user message unchanged.
    assert captured["user_message"]["content"] == original_content


def test_located_screen_path_unchanged(monkeypatch):
    """Tier-1 (located_screen set) still injects the same shell+screen reference
    files + recreate block as before the shell-helper factor — a guard against
    the read_located_sources refactor regressing Tier-1."""
    _stub_design_system(monkeypatch)
    fake_loop, captured = _stub_agent_loop_capture()
    monkeypatch.setattr(runner, "agent_loop", fake_loop)

    with patch(
        "app.design_agent.codebase_map.recreate.read_repo",
        return_value=_snapshot({
            "src/Home.tsx": "export const Home = () => null",
            "src/Hero.tsx": "export const Hero = () => null",
        }),
    ):
        _run(generate_prototype(
            prototype_id=203, workspace_id="app", system_blocks=_system(),
            user_message=_user("Add a search box."), figma_file_key=None,
            github_installation_id=9001,
            located_screen=_located_screen(),
            # A shell_map is irrelevant on the located path — Tier-1 wins.
            shell_map=_shell_map(),
        ))

    loop_vfs = captured["virtual_fs_at_loop_entry"]
    assert loop_vfs["__reference__/src/Home.tsx"].startswith("export const Home")
    assert loop_vfs["__reference__/src/Hero.tsx"].startswith("export const Hero")
    blob = "\n".join(b.get("text", "") for b in captured["user_message"]["content"])
    assert "RECREATE TARGET" in blob
    # The shell-only block must NOT appear on the located path.
    assert "APP SHELL" not in blob


# ── AC11: plain-English source guarantee ────────────────────────────────────────


def test_no_prohibited_tokens_in_source():
    """This seam test file must not carry internal engagement coordinates.

    The runner module is pre-existing and carries historical references from
    prior work that this ticket does not own; the commit-time grep diff
    enforces that the runner diff added by this ticket is clean. Here we
    only guard the new test file. The pattern is assembled at runtime so the
    literals it checks for are not themselves continuous strings in this
    file.
    """
    targets = [Path(__file__)]
    parts = [
        r"C[0-9]-[0-9]",
        "C" + "-series",
        r"H[0-9]-[0-9]",
        r"P[0-9]-[0-9]",
        r"\bAD[0-9]",
        r"\bF[0-9]{1,2}\b",
        "DB" + "D",
        "Babaji" + "de",
    ]
    pattern = "|".join(parts)
    for target in targets:
        text = target.read_text()
        matches = re.findall(pattern, text)
        assert not matches, f"Prohibited token(s) {matches} found in {target.name}"
