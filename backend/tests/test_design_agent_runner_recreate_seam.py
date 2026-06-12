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
