"""Tests for the design-brief injection into the scaffold agent's user prompt.

Covers three areas:
  1. _render_design_brief_block — unit tests for the pure helper.
  2. Injection logic — the brief block is appended to user_message content
     (list-shaped and string-shaped) without disturbing existing blocks.
  3. Egress seam — the unique marker text that reaches the model's user prompt
     must NEVER appear in any kind:"step" activity event or in system_blocks.

All tests are fully offline — no network calls. The Anthropic client and
design-system resolver are replaced with lightweight fakes.
"""
from __future__ import annotations

import asyncio
import copy
import types

import pytest

from app.design_agent import runner
from app.design_agent.runner import RunResult, generate_prototype, _render_design_brief_block
from app.design_agent.design_system.models import (
    Buttons,
    ComponentLanguage,
    DesignSystem,
)
from app.design_agent.tools import ToolContext
from tests._fake_anthropic import _FakeStream


# ──────────────────────────────────────────────────────────────────────────────
# Shared fakes (mirrored from test_design_agent_runner.py / _fake_anthropic.py)
# ──────────────────────────────────────────────────────────────────────────────


class _FakeBlock:
    def __init__(self, data: dict):
        self._data = data

    def model_dump(self) -> dict:
        return copy.deepcopy(self._data)


class _FakeMessage:
    def __init__(self, stop_reason, blocks, usage):
        self.stop_reason = stop_reason
        self.content = [_FakeBlock(b) for b in blocks]
        self.usage = usage


class _RecordingClient:
    """Sync messages.create / messages.stream that replays a list of responses
    and records each call's kwargs.

    The runner uses client.messages.stream() inside asyncio.to_thread, so the
    fake exposes _stream as well (delegates to _create and wraps in _FakeStream).
    """

    def __init__(self, responses):
        self._responses = list(responses)
        self.calls: list[dict] = []
        self.messages = types.SimpleNamespace(
            create=self._create,
            stream=self._stream,
        )

    def _create(self, **kwargs):
        self.calls.append({
            "system": kwargs.get("system"),
            "messages": copy.deepcopy(kwargs.get("messages")),
            "model": kwargs.get("model"),
            "max_tokens": kwargs.get("max_tokens"),
            "tools": kwargs.get("tools"),
        })
        i = len(self.calls) - 1
        resp = self._responses[i] if i < len(self._responses) else self._responses[-1]
        if isinstance(resp, BaseException):
            raise resp
        return resp

    def _stream(self, **kwargs):
        return _FakeStream(self._create(**kwargs))


def _usage(cache_creation=0, cache_read=0, inp=0, out=0):
    return types.SimpleNamespace(
        cache_creation_input_tokens=cache_creation,
        cache_read_input_tokens=cache_read,
        input_tokens=inp,
        output_tokens=out,
    )


def _msg(stop_reason, blocks=None, usage=None):
    return _FakeMessage(stop_reason, blocks or [], usage or _usage())


def _text(s: str) -> dict:
    return {"type": "text", "text": s}


def _system():
    return [
        {"type": "text", "text": "You are the Design Agent. Build prototypes."},
        {
            "type": "text",
            "text": "<design system + tool defs — the stable prefix>",
            "cache_control": {"type": "ephemeral", "ttl": "1h"},
        },
    ]


def _user(text: str = "Build a landing page."):
    return {"role": "user", "content": [_text(text)]}


def _install_client(monkeypatch, responses) -> _RecordingClient:
    client = _RecordingClient(responses)
    monkeypatch.setattr(runner, "get_design_agent_client", lambda: client)
    return client


def _run(coro):
    return asyncio.run(coro)


def _ds_with_brief(brief: str) -> DesignSystem:
    ds = DesignSystem()
    ds.component_language = ComponentLanguage(
        radius="sharp",
        density="compact",
        separation="borders",
        buttons=Buttons(style="outline", radius="sharp", weight="bold"),
        accent_usage="heavy",
        brief=brief,
    )
    return ds


# ──────────────────────────────────────────────────────────────────────────────
# 1. _render_design_brief_block — pure helper unit tests
# ──────────────────────────────────────────────────────────────────────────────


def test_render_design_brief_block_none_ds_returns_none():
    """None design system → None (no block to inject)."""
    assert _render_design_brief_block(None) is None


def test_render_design_brief_block_empty_brief_returns_none():
    """A design system whose brief is empty string → None."""
    ds = DesignSystem()
    ds.component_language.brief = ""
    assert _render_design_brief_block(ds) is None


def test_render_design_brief_block_blank_brief_returns_none():
    """A design system whose brief is only whitespace → None."""
    ds = DesignSystem()
    ds.component_language.brief = "   "
    assert _render_design_brief_block(ds) is None


def test_render_design_brief_block_contains_brief_text():
    """When a brief is present, the output string contains the brief text."""
    ds = _ds_with_brief("Minimal, high-contrast interface with sharp edges.")
    result = _render_design_brief_block(ds)
    assert result is not None
    assert "Minimal, high-contrast interface with sharp edges." in result


def test_render_design_brief_block_mentions_structured_cues():
    """The rendered block mentions all key structured cues."""
    ds = _ds_with_brief("Clean and modern.")
    result = _render_design_brief_block(ds)
    assert result is not None
    # density, separation, radius, button style, accent_usage
    assert "compact" in result
    assert "borders" in result
    assert "sharp" in result
    assert "outline" in result
    assert "heavy" in result


def test_render_design_brief_block_is_deterministic():
    """Same input → same output on every call."""
    ds = _ds_with_brief("A warm, approachable style.")
    assert _render_design_brief_block(ds) == _render_design_brief_block(ds)


def test_render_design_brief_block_prefixed_with_design_language():
    """The output begins with a 'Design language' prefix so the model knows
    this paragraph is design guidance, not part of the user request."""
    ds = _ds_with_brief("Rounded and friendly.")
    result = _render_design_brief_block(ds)
    assert result is not None
    assert result.startswith("Design language")


# ──────────────────────────────────────────────────────────────────────────────
# 2. Injection logic — list-shaped and string-shaped content
# ──────────────────────────────────────────────────────────────────────────────


def test_injection_list_content_appends_brief_block():
    """With list-shaped content, the brief block is appended as the last block
    and the original block is preserved."""
    brief = "A bold, expressive visual style."
    ds = _ds_with_brief(brief)
    brief_block = _render_design_brief_block(ds)
    assert brief_block is not None

    original_text = "Build a dashboard."
    content = [{"type": "text", "text": original_text}]
    user_message: dict = {"role": "user", "content": content}

    # Replicate the injection logic from generate_prototype.
    existing = user_message.get("content")
    if isinstance(existing, list):
        user_message["content"] = list(existing) + [
            {"type": "text", "text": brief_block}
        ]

    result_content = user_message["content"]
    assert isinstance(result_content, list)
    assert len(result_content) == 2
    assert result_content[0]["text"] == original_text  # original preserved
    assert result_content[-1]["text"] == brief_block   # brief is the last block
    assert brief in result_content[-1]["text"]


def test_injection_string_content_carries_both_texts():
    """With string-shaped content, after injection both the original text and
    the brief are present without raising."""
    brief = "Minimal dark theme."
    ds = _ds_with_brief(brief)
    brief_block = _render_design_brief_block(ds)
    assert brief_block is not None

    original_text = "Build a login screen."
    user_message: dict = {"role": "user", "content": original_text}

    # Replicate the injection logic from generate_prototype (string branch).
    existing = user_message.get("content")
    if isinstance(existing, list):
        user_message["content"] = list(existing) + [
            {"type": "text", "text": brief_block}
        ]
    elif isinstance(existing, str):
        user_message["content"] = [
            {"type": "text", "text": existing},
            {"type": "text", "text": brief_block},
        ]

    result_content = user_message["content"]
    assert isinstance(result_content, list)
    assert len(result_content) == 2
    # Both original and brief text must be present.
    all_text = " ".join(b["text"] for b in result_content)
    assert original_text in all_text
    assert brief in all_text


def test_injection_missing_content_does_not_raise():
    """If content is absent from user_message, the injection is skipped silently."""
    brief = "Airy, spacious layout."
    ds = _ds_with_brief(brief)
    brief_block = _render_design_brief_block(ds)
    assert brief_block is not None

    user_message: dict = {"role": "user"}  # no content key

    # Replicate the injection logic — must not raise.
    try:
        existing = user_message.get("content")
        if isinstance(existing, list):
            user_message["content"] = list(existing) + [
                {"type": "text", "text": brief_block}
            ]
        elif isinstance(existing, str):
            user_message["content"] = [
                {"type": "text", "text": existing},
                {"type": "text", "text": brief_block},
            ]
        # Other types (None, missing) → skip silently.
    except Exception as exc:
        pytest.fail(f"Injection raised unexpectedly: {exc}")

    # Content stays absent — nothing was injected.
    assert "content" not in user_message


# ──────────────────────────────────────────────────────────────────────────────
# 3. Egress seam — the brief marker must reach the model, never a step event
# ──────────────────────────────────────────────────────────────────────────────

UNIQUE_BRIEF_MARKER = "UNIQUE_BRIEF_MARKER_XYZ"


def test_design_brief_reaches_model_but_never_step_events(monkeypatch):
    """Egress seam (load-bearing): the design brief text is injected into the
    agent's user prompt so the model can read it, but it NEVER appears in any
    kind:'step' activity event.

    POSITIVE assertion: the marker appears in the user-turn messages sent to
    the fake Anthropic client (proves the wiring works end-to-end).

    NEGATIVE assertion: the marker is absent from every collected step event
    (proves the egress seam holds) and absent from system_blocks (proves the
    cached prefix is not contaminated).
    """
    # Arrange: inject a design system whose brief contains the unique marker.
    ds = _ds_with_brief(UNIQUE_BRIEF_MARKER)

    monkeypatch.setattr(
        runner, "_resolve_design_system", lambda **_kw: ds
    )
    monkeypatch.setattr(
        runner, "_resolve_figma_access_token", lambda key, ws: None
    )

    # Collect every step event that publish_step emits.
    step_events: list[dict] = []
    monkeypatch.setattr(
        runner, "publish_step", lambda pid, step: step_events.append(step)
    )

    # Fake Anthropic client that ends the turn immediately (one pass only).
    client = _install_client(monkeypatch, [_msg("end_turn", [_text("done")])])

    # Act: run generate_prototype with a real user message (list-shaped, as the
    # route builds it).
    _run(generate_prototype(
        prototype_id=99,
        workspace_id="app",
        system_blocks=_system(),
        user_message=_user("Build a landing page."),
        figma_file_key=None,
        scenario="A",
    ))

    # POSITIVE: the marker reached the model — it must appear in at least one
    # content block of the first user-turn message sent to the client.
    assert client.calls, "Expected at least one Anthropic call"
    first_call_messages = client.calls[0]["messages"]
    all_user_text = []
    for msg in first_call_messages:
        content = msg.get("content", [])
        if isinstance(content, list):
            for block in content:
                if isinstance(block, dict) and block.get("type") == "text":
                    all_user_text.append(block.get("text", ""))
        elif isinstance(content, str):
            all_user_text.append(content)
    assert any(UNIQUE_BRIEF_MARKER in t for t in all_user_text), (
        "The brief marker must appear in the messages forwarded to the model "
        "(injection did not wire through to agent_loop)"
    )

    # NEGATIVE: the marker must NOT appear in any step event.
    for event in step_events:
        event_text = event.get("text", "")
        assert UNIQUE_BRIEF_MARKER not in event_text, (
            f"Brief marker leaked into a step event: {event!r}"
        )

    # NEGATIVE: the marker must NOT appear in system_blocks forwarded to the model.
    first_call_system = client.calls[0]["system"] or []
    for block in first_call_system:
        system_text = block.get("text", "") if isinstance(block, dict) else ""
        assert UNIQUE_BRIEF_MARKER not in system_text, (
            f"Brief marker leaked into system_blocks: {block!r}"
        )
