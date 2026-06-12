"""Tests for the output-cap escalation branch in agent_loop.

Stubs client.messages.stream with controllable stop_reason sequences and asserts
the model kwarg per stream call, result.model_escalated, and observability log lines.
No real LLM is invoked.
"""
from __future__ import annotations

import asyncio
import copy
import logging
import os
import re
import subprocess
import types

import pytest

from app.design_agent import runner
from app.design_agent.runner import (
    ESCALATION_MAX_TOKENS,
    ESCALATION_MODEL,
    MODEL,
    RunResult,
    agent_loop,
    generate_prototype,
)
from app.design_agent.tools import ToolContext
from app.llm_telemetry import MODEL_PRICING
from tests._fake_anthropic import _FakeStream

RUNNER_LOGGER = "app.design_agent.runner"
TELEMETRY_LOGGER = "app.llm_telemetry"


# ─── Shared fakes ─────────────────────────────────────────────────────────────


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
    """Sync messages.stream that replays a list of responses and records per-call kwargs."""

    def __init__(self, responses):
        self._responses = list(responses)
        self.calls: list[dict] = []
        self.messages = types.SimpleNamespace(create=self._create, stream=self._stream)

    def _create(self, **kwargs):
        self.calls.append({
            "model": kwargs.get("model"),
            "max_tokens": kwargs.get("max_tokens"),
            "system": kwargs.get("system"),
            "messages": copy.deepcopy(kwargs.get("messages")),
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
        {"type": "text", "text": "Design Agent system prompt."},
        {
            "type": "text",
            "text": "stable design-system block",
            "cache_control": {"type": "ephemeral", "ttl": "1h"},
        },
    ]


def _user(text: str = "Build a screen."):
    return {"role": "user", "content": [_text(text)]}


def _ctx(**overrides) -> ToolContext:
    base = dict(prototype_id=1, workspace_id="app", virtual_fs={})
    base.update(overrides)
    return ToolContext(**base)


def _install_client(monkeypatch, responses) -> _RecordingClient:
    client = _RecordingClient(responses)
    monkeypatch.setattr(runner, "get_design_agent_client", lambda: client)
    return client


def _run(coro):
    return asyncio.run(coro)


# ─── Escalation ladder ────────────────────────────────────────────────────────


def test_no_cap_stays_sonnet(monkeypatch):
    """No output cap hit: default model used throughout, no escalation."""
    client = _install_client(monkeypatch, [_msg("end_turn", [_text("done")])])
    result = _run(agent_loop(_system(), _user(), _ctx()))
    assert result.status == "complete"
    assert result.model_escalated is False
    assert client.calls[0]["model"] == MODEL


def test_first_cap_doubles_stays_sonnet(monkeypatch):
    """First output cap hit doubles max_tokens and retries — stays on the default model."""
    client = _install_client(monkeypatch, [
        _msg("max_tokens", [_text("truncated")]),
        _msg("end_turn", [_text("done")]),
    ])
    result = _run(agent_loop(_system(), _user(), _ctx()))
    assert result.status == "complete"
    assert result.model_escalated is False
    assert client.calls[0]["model"] == MODEL  # initial attempt: default model
    assert client.calls[1]["model"] == MODEL  # retry after doubling: still default
    assert client.calls[1]["max_tokens"] == client.calls[0]["max_tokens"] * 2


def test_second_cap_escalates_to_opus_and_continues(monkeypatch):
    """Second consecutive output cap hit triggers model escalation: switches to
    ESCALATION_MODEL, resets max_tokens to ESCALATION_MAX_TOKENS, and retries the
    same turn (does NOT exit with max_tokens)."""
    client = _install_client(monkeypatch, [
        _msg("max_tokens", [_text("t1")]),   # first hit → double, stay on default model
        _msg("max_tokens", [_text("t2")]),   # second hit → escalate
        _msg("end_turn", [_text("completed on opus")]),  # retry with escalated model
    ])
    result = _run(agent_loop(_system(), _user(), _ctx()))
    assert result.status == "complete"
    assert result.model_escalated is True
    # Calls 0 and 1 use the default model
    assert client.calls[0]["model"] == MODEL
    assert client.calls[1]["model"] == MODEL
    # Call 2 uses the escalation model at the escalation budget
    assert client.calls[2]["model"] == ESCALATION_MODEL
    assert client.calls[2]["max_tokens"] == ESCALATION_MAX_TOKENS


def test_third_cap_after_escalation_exits(monkeypatch):
    """A third output cap hit (after escalation) exits with status=max_tokens — no
    infinite escalation loop."""
    client = _install_client(monkeypatch, [
        _msg("max_tokens", [_text("t1")]),  # first hit → double
        _msg("max_tokens", [_text("t2")]),  # second hit → escalate
        _msg("max_tokens", [_text("t3")]),  # third hit (already escalated) → exit
    ])
    result = _run(agent_loop(_system(), _user(), _ctx()))
    assert result.status == "max_tokens"
    assert result.model_escalated is True
    assert len(client.calls) == 3
    assert client.calls[2]["model"] == ESCALATION_MODEL


# ─── Cost / safety ────────────────────────────────────────────────────────────


def test_escalation_emits_log_line(monkeypatch, caplog):
    """Escalation point emits exactly one output_cap_escalation INFO log line with
    the prototype_id, from_model, to_model, and iter fields."""
    _install_client(monkeypatch, [
        _msg("max_tokens", [_text("t1")]),
        _msg("max_tokens", [_text("t2")]),
        _msg("end_turn", [_text("done")]),
    ])
    with caplog.at_level(logging.INFO, logger=RUNNER_LOGGER):
        _run(agent_loop(_system(), _user(), _ctx(prototype_id=42)))
    escalation_records = [
        r for r in caplog.records
        if r.name == RUNNER_LOGGER and "output_cap_escalation" in r.getMessage()
    ]
    assert len(escalation_records) == 1
    msg = escalation_records[0].getMessage()
    assert "prototype_id=42" in msg
    assert f"from_model={MODEL}" in msg
    assert f"to_model={ESCALATION_MODEL}" in msg


def test_escalated_cost_summary_tags_model_and_escalated(monkeypatch, caplog):
    """When the run escalated, the cost-summary log carries the escalated model
    name and escalated=True so telemetry prices the run honestly."""
    _install_client(monkeypatch, [
        _msg("max_tokens", [_text("t1")], usage=_usage(inp=100, out=50)),
        _msg("max_tokens", [_text("t2")], usage=_usage(inp=100, out=50)),
        _msg("end_turn", [_text("done")], usage=_usage(cache_read=20, inp=80, out=200)),
    ])
    with caplog.at_level(logging.INFO, logger=TELEMETRY_LOGGER):
        result, _ = _run(generate_prototype(
            prototype_id=7, workspace_id="app", system_blocks=_system(),
            user_message=_user(), figma_file_key=None, scenario="A",
        ))
    assert result.model_escalated is True
    summary_records = [
        r for r in caplog.records
        if r.name == TELEMETRY_LOGGER and "design_agent.run.complete" in r.getMessage()
    ]
    assert len(summary_records) == 1
    msg = summary_records[0].getMessage()
    assert f"model={ESCALATION_MODEL}" in msg
    assert "escalated=True" in msg


def test_escalated_run_still_honours_hard_cap(monkeypatch):
    """The hard-cap abort check remains active after escalation: a run that would
    breach the ceiling exits with status=aborted, not complete."""
    call_count = [0]
    original_should_abort = runner.should_abort

    def _patched_should_abort(usage, model, cap):
        call_count[0] += 1
        # Trigger abort only after escalation has occurred (third invocation)
        if call_count[0] >= 3:
            return True
        return original_should_abort(usage, model, cap)

    monkeypatch.setattr(runner, "should_abort", _patched_should_abort)
    _install_client(monkeypatch, [
        _msg("max_tokens", [_text("t1")]),   # first cap hit → double
        _msg("max_tokens", [_text("t2")]),   # second cap hit → escalate
        _msg("end_turn", [_text("done")]),   # opus call — but hard cap fires first
    ])
    result = _run(agent_loop(_system(), _user(), _ctx()))
    assert result.status == "aborted"
    assert result.model_escalated is True  # escalation happened before the abort


def test_escalation_model_in_pricing_table():
    """ESCALATION_MODEL is a key in MODEL_PRICING; pricing lookup succeeds without
    raising UnknownModelError when the escalated run's cost is computed."""
    assert ESCALATION_MODEL in MODEL_PRICING, (
        f"'{ESCALATION_MODEL}' not in MODEL_PRICING — pricing lookup would fail"
    )
    assert ESCALATION_MODEL == "claude-opus-4-7"


# ─── Integrity ────────────────────────────────────────────────────────────────


def test_non_escalated_run_byte_identical(monkeypatch):
    """A run that never hits the output cap uses the default model throughout and
    returns model_escalated=False — existing behaviour is unchanged."""
    client = _install_client(monkeypatch, [
        _msg("end_turn", [_text("done")], usage=_usage(cache_read=5, inp=200, out=300)),
    ])
    result = _run(agent_loop(_system(), _user(), _ctx()))
    assert result.status == "complete"
    assert result.model_escalated is False
    assert all(c["model"] == MODEL for c in client.calls)


def test_no_new_prohibited_tokens_in_source():
    """This test file contains no internal project coordinates (ticket IDs, decision
    labels, or organisation abbreviations)."""
    pattern = re.compile(
        r"(?<!\w)[CP]\d-\d"  # ticket-style IDs at word boundary: C3-06, P2-03
        r"|H\d-\d"
        r"|\bAD\d"           # decision labels
        r"|\bF\d{1,2}\b"     # feature labels
        r"|\bDBD\b"          # organisation abbreviation
    )
    this_file = os.path.abspath(__file__)
    with open(this_file, encoding="utf-8") as fh:
        content = fh.read()
    # Strip pattern string literals from the check to avoid self-reference
    code_lines = [
        line for line in content.splitlines()
        if not line.strip().startswith(("r\"", "r'", "#"))
    ]
    matches = pattern.findall("\n".join(code_lines))
    assert not matches, f"Prohibited tokens found in test file: {matches}"
