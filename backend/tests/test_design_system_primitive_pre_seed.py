"""Tests for the primitive pre-seed wiring in generate_prototype.

Verifies that the primitive seeding is correctly merged into virtual_fs
for each source path (figma/website, github, low-confidence) and that a
failure in the primitive factory does not propagate or corrupt the CSS seed.

Pure unit tests — no DB, no network, no Anthropic API.
"""
from __future__ import annotations

import asyncio
from unittest.mock import MagicMock, patch

import pytest

from app.design_agent import runner
from app.design_agent.runner import RunResult, generate_prototype
from app.design_agent.design_system.models import DesignSystem


def _run(coro):
    return asyncio.run(coro)


def _system():
    return [{"type": "text", "text": "system"}]


def _user():
    return {"role": "user", "content": [{"type": "text", "text": "build it"}]}


def _fake_loop_returning_fs(captured: dict):
    async def fake_loop(*, system_blocks, user_message, ctx, scenario, mode):
        captured["virtual_fs"] = ctx.virtual_fs
        return RunResult(status="complete", iters=1, usage=runner.RunUsage(),
                         duration_ms=1, final_content=[])
    return fake_loop


def _install_noop_client(monkeypatch):
    """Suppress the real Anthropic client — loop is fully replaced by fake_loop."""
    monkeypatch.setattr(runner, "get_design_agent_client", lambda: None)


# ─── Figma / website: non-low confidence → button.tsx in virtual_fs ──────────


def test_pre_seed_primitives_figma_path_seeds_virtual_fs(monkeypatch):
    ds_medium = DesignSystem(confidence="medium")
    captured: dict = {}

    monkeypatch.setattr(runner, "_resolve_figma_access_token", lambda k, ws: None)
    monkeypatch.setattr(runner, "_resolve_design_system", lambda **_: ds_medium)
    monkeypatch.setattr(runner, "agent_loop", _fake_loop_returning_fs(captured))
    _install_noop_client(monkeypatch)

    _run(generate_prototype(
        prototype_id=1, workspace_id="ws", system_blocks=_system(),
        user_message=_user(), figma_file_key=None, scenario="A",
    ))

    fs = captured["virtual_fs"]
    assert "src/index.css" in fs, "CSS pre-seed must still be present"
    assert "src/components/ui/button.tsx" in fs
    assert "src/components/ui/card.tsx" in fs
    assert "export" in fs["src/components/ui/button.tsx"]


def test_pre_seed_primitives_website_path_seeds_virtual_fs(monkeypatch):
    ds_high = DesignSystem(confidence="high")
    captured: dict = {}

    monkeypatch.setattr(runner, "_resolve_figma_access_token", lambda k, ws: None)
    monkeypatch.setattr(runner, "_resolve_design_system", lambda **_: ds_high)
    monkeypatch.setattr(runner, "agent_loop", _fake_loop_returning_fs(captured))
    _install_noop_client(monkeypatch)

    _run(generate_prototype(
        prototype_id=2, workspace_id="ws", system_blocks=_system(),
        user_message=_user(), figma_file_key=None,
        website_url="https://example.com", scenario="B",
    ))

    fs = captured["virtual_fs"]
    assert "src/components/ui/input.tsx" in fs
    assert "var(--" in fs["src/components/ui/input.tsx"]


# ─── GitHub path: extract_ui_primitives called with correct ref ───────────────


def test_pre_seed_primitives_github_path_calls_extract_ui_primitives(monkeypatch):
    ds_high = DesignSystem(confidence="high")
    captured: dict = {}
    calls: list = []

    class FakeGithubExtractor:
        def __init__(self, installation_id):
            self.installation_id = installation_id

        def extract_ui_primitives(self, ref: str) -> dict:
            calls.append({"installation_id": self.installation_id, "ref": ref})
            return {"src/components/ui/button.tsx": "export function Button() {}"}

    monkeypatch.setattr(runner, "_resolve_figma_access_token", lambda k, ws: None)
    monkeypatch.setattr(runner, "_resolve_design_system", lambda **_: ds_high)
    monkeypatch.setattr(runner, "agent_loop", _fake_loop_returning_fs(captured))
    _install_noop_client(monkeypatch)

    with patch(
        "app.design_agent.design_system.adapters.GithubExtractor",
        FakeGithubExtractor,
    ):
        _run(generate_prototype(
            prototype_id=3, workspace_id="ws", system_blocks=_system(),
            user_message=_user(), figma_file_key=None,
            github_repo="owner/repo@main", github_installation_id=99,
            scenario="A",
        ))

    assert len(calls) == 1
    assert calls[0]["ref"] == "owner/repo@main"
    assert calls[0]["installation_id"] == 99
    fs = captured["virtual_fs"]
    assert "src/components/ui/button.tsx" in fs
    assert "src/index.css" in fs


# ─── Low-confidence: no components/ui keys in virtual_fs ─────────────────────


def test_pre_seed_primitives_low_confidence_skips_primitives(monkeypatch):
    ds_low = DesignSystem(confidence="low")
    captured: dict = {}

    monkeypatch.setattr(runner, "_resolve_figma_access_token", lambda k, ws: None)
    monkeypatch.setattr(runner, "_resolve_design_system", lambda **_: ds_low)
    monkeypatch.setattr(runner, "agent_loop", _fake_loop_returning_fs(captured))
    _install_noop_client(monkeypatch)

    _run(generate_prototype(
        prototype_id=4, workspace_id="ws", system_blocks=_system(),
        user_message=_user(), figma_file_key=None, scenario="A",
    ))

    fs = captured["virtual_fs"]
    assert not any("components/ui" in k for k in fs), (
        "Low-confidence should produce no components/ui keys"
    )


# ─── Exception resilience: CSS still present even if primitive factory raises ──


def test_pre_seed_primitives_factory_raises_does_not_block_generation(monkeypatch):
    ds_high = DesignSystem(confidence="high")
    captured: dict = {}

    def exploding_render_primitive_set(ds):
        raise RuntimeError("primitive factory exploded")

    monkeypatch.setattr(runner, "_resolve_figma_access_token", lambda k, ws: None)
    monkeypatch.setattr(runner, "_resolve_design_system", lambda **_: ds_high)
    monkeypatch.setattr(runner, "agent_loop", _fake_loop_returning_fs(captured))
    _install_noop_client(monkeypatch)

    with patch(
        "app.design_agent.design_system.primitives.render_primitive_set",
        exploding_render_primitive_set,
    ):
        result, _ = _run(generate_prototype(
            prototype_id=5, workspace_id="ws", system_blocks=_system(),
            user_message=_user(), figma_file_key=None, scenario="A",
        ))

    assert result.status == "complete"
    fs = captured["virtual_fs"]
    assert "src/index.css" in fs, "CSS pre-seed must survive a primitive factory error"
    assert not any("components/ui" in k for k in fs)
