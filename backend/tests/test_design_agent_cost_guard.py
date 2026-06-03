"""Unit tests for the AD15 cost guard (P5-03).

Two surfaces:

1. The PURE decision helpers in `app/llm_telemetry.py`
   (`project_next_iter_cost` / `should_wrap_up`) — deterministic, no network,
   reuse `MODEL_PRICING` via `RunUsage.est_cost_usd`, fail closed on an unpriced
   model. No second pricing table.

2. The ~10-line wire in `agent_loop`: when the projected next-iteration spend
   crosses `SOFT_CAP_USD`, the loop injects the EXISTING `_wrap_up_nudge` into the
   trailing user turn ONCE and emits a `cost_guard.degraded` log line. The guard is
   SOFT — the run still completes (returns a `RunResult`, never aborts) — and
   coexists with the iteration-count graduated nudge.

The Anthropic client is replaced by a recording fake whose `messages.create` is a
sync callable (the runner invokes it via `asyncio.to_thread`). The fake records a
DEEP COPY of the `messages` kwarg per call so the post-injection snapshot survives
the runner's in-place mutation of the list. Mirrors test_design_agent_runner.py.
"""
from __future__ import annotations

import asyncio
import copy
import inspect
import logging
import py_compile
import types

import pytest

from app.design_agent import runner
from app.design_agent.runner import MODEL, SOFT_CAP_USD, RunResult, agent_loop, generate_prototype
from app.design_agent.tools import ToolContext
from app.llm_telemetry import (
    MODEL_PRICING,
    RunUsage,
    UnknownModelError,
    log_llm_run,
    project_next_iter_cost,
    should_wrap_up,
)

RUNNER_LOGGER = "app.design_agent.runner"
TELEMETRY_LOGGER = "app.llm_telemetry"
SONNET = "claude-sonnet-4-6"

# At sonnet output pricing (15.0 / 1M = 1.5e-5 $/tok), 20_000 output tokens =
# $0.30 current spend; projection (2x) = $0.60 >= $0.50 cap. 1_000 output tokens =
# $0.015; projection = $0.03 < cap. These anchor the loop-wire crossings.
_OVER_CAP_OUT = 20_000
_UNDER_CAP_OUT = 1_000


# ─── Fakes (compact mirror of test_design_agent_runner.py) ──────────────────


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
    """Sync `messages.create` replaying a list of responses; LAST entry replays
    when calls outrun the list (handy for 'always tool_use' loops)."""

    def __init__(self, responses):
        self._responses = list(responses)
        self.calls: list[dict] = []
        self.messages = types.SimpleNamespace(create=self._create)

    def _create(self, **kwargs):
        self.calls.append({"messages": copy.deepcopy(kwargs.get("messages"))})
        i = len(self.calls) - 1
        resp = self._responses[i] if i < len(self._responses) else self._responses[-1]
        if isinstance(resp, BaseException):
            raise resp
        return resp


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


def _tool_use(id: str, name: str, inp: dict) -> dict:
    return {"type": "tool_use", "id": id, "name": name, "input": inp}


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


def _ctx(**overrides) -> ToolContext:
    base = dict(prototype_id=1, workspace_id="app", virtual_fs={})
    base.update(overrides)
    return ToolContext(**base)


def _install_client(monkeypatch, responses) -> _RecordingClient:
    client = _RecordingClient(responses)
    monkeypatch.setattr(runner, "get_design_agent_client", lambda: client)
    return client


def _stub_dispatch(monkeypatch):
    """Make tool dispatch a no-op success so loop tests don't depend on real tool
    semantics (and so the .tsx autofixer never runs — we only use `view`)."""

    async def fake_dispatch(name, inp, ctx, allowed_names=None):
        return {"content": "ok", "tool_name": name}

    monkeypatch.setattr(runner, "dispatch", fake_dispatch)


def _run(coro):
    return asyncio.run(coro)


def _all_call_text(client: _RecordingClient) -> str:
    """Flatten every text block across every recorded create call's messages."""
    chunks: list[str] = []
    for call in client.calls:
        for m in call["messages"] or []:
            content = m.get("content")
            if isinstance(content, list):
                for b in content:
                    if isinstance(b, dict) and b.get("type") == "text":
                        chunks.append(b.get("text", ""))
    return "\n".join(chunks)


# ─── Pure helper — creation / projection (AC1) ──────────────────────────────


def test_project_next_iter_cost_doubles_current():
    usage = RunUsage(output_tokens=1_000)  # $0.015 current
    current = usage.est_cost_usd(SONNET)
    assert current > 0
    assert project_next_iter_cost(usage, SONNET) == pytest.approx(2 * current)


def test_project_next_iter_cost_zero_on_empty_usage():
    assert project_next_iter_cost(RunUsage(), SONNET) == 0.0


# ─── Pure helper — soft-cap decision (AC2) ──────────────────────────────────


def test_should_wrap_up_true_at_and_above_cap():
    # Clearly above: projection $0.60 >= $0.50.
    over = RunUsage(output_tokens=_OVER_CAP_OUT)
    assert should_wrap_up(over, SONNET, 0.50) is True
    # Boundary-exact (inclusive): cap == projection -> True. Computed from the
    # usage itself so the equality is exact regardless of token granularity.
    proj = project_next_iter_cost(over, SONNET)
    assert should_wrap_up(over, SONNET, proj) is True


def test_should_wrap_up_false_below_cap():
    under = RunUsage(output_tokens=_UNDER_CAP_OUT)  # projection $0.03
    assert should_wrap_up(under, SONNET, 0.50) is False
    # Just above the projection the cap is not reached.
    proj = project_next_iter_cost(under, SONNET)
    assert should_wrap_up(under, SONNET, proj + 1e-9) is False


# ─── Pure helper — fail closed + single pricing table (AC3) ─────────────────


def test_cost_guard_helpers_fail_closed_unknown_model():
    usage = RunUsage(output_tokens=1_000)
    with pytest.raises(UnknownModelError):
        project_next_iter_cost(usage, "claude-not-a-real-model")
    with pytest.raises(UnknownModelError):
        should_wrap_up(usage, "claude-not-a-real-model", 0.50)


def test_single_pricing_table():
    import app.llm_telemetry as telemetry

    src = inspect.getsource(telemetry)
    # Exactly one pricing-table definition in the module.
    assert src.count("MODEL_PRICING: dict[str, dict[str, float]] = {") == 1
    # The new helpers carry NO pricing literals — they reuse MODEL_PRICING via
    # est_cost_usd rather than duplicating any per-token math.
    assert "1_000_000" not in inspect.getsource(project_next_iter_cost)
    assert "1_000_000" not in inspect.getsource(should_wrap_up)
    assert "est_cost_usd" in inspect.getsource(project_next_iter_cost)


# ─── Loop wire — degradation (AC4) ──────────────────────────────────────────


def test_agent_loop_injects_wrap_up_when_over_cap(monkeypatch, caplog):
    client = _install_client(monkeypatch, [
        _msg("tool_use", [_tool_use("t1", "view", {"path": "x"})],
             usage=_usage(out=_OVER_CAP_OUT)),  # iter1 crosses the cap
        _msg("end_turn", [_text("done")]),
    ])
    _stub_dispatch(monkeypatch)
    with caplog.at_level(logging.INFO):
        result = _run(agent_loop(_system(), _user(), _ctx()))

    assert result.status == "complete"
    # The hard-stop nudge (_wrap_up_nudge(0)) landed in the trailing user turn and
    # is visible on the next create call. No graduated nudge fired (default
    # max_iters=40 -> remaining never in the schedule on iters 1-2), so the only
    # STOP-now text is the cost guard's.
    text = _all_call_text(client)
    assert "0 tool-call turn(s) left" in text
    guard_logs = [
        r for r in caplog.records
        if r.name == RUNNER_LOGGER and "cost_guard.degraded" in r.getMessage()
    ]
    assert len(guard_logs) == 1
    msg = guard_logs[0].getMessage()
    assert "prototype_id=1" in msg
    assert "reason=soft_cap_projection" in msg
    assert "mode=scaffold" in msg


def test_agent_loop_cost_guard_fires_once(monkeypatch, caplog):
    # Two consecutive iterations both project over the cap; the guard must inject
    # and log exactly once (a second nudge would just burn tokens).
    client = _install_client(monkeypatch, [
        _msg("tool_use", [_tool_use("t1", "view", {"path": "x"})],
             usage=_usage(out=_OVER_CAP_OUT)),
        _msg("tool_use", [_tool_use("t2", "view", {"path": "y"})],
             usage=_usage(out=_OVER_CAP_OUT)),
        _msg("end_turn", [_text("done")]),
    ])
    _stub_dispatch(monkeypatch)
    with caplog.at_level(logging.INFO):
        result = _run(agent_loop(_system(), _user(), _ctx()))

    assert result.status == "complete"
    guard_logs = [
        r for r in caplog.records
        if r.name == RUNNER_LOGGER and "cost_guard.degraded" in r.getMessage()
    ]
    assert len(guard_logs) == 1
    # The nudge is appended to the trailing user turn ONCE — within the final
    # messages snapshot the cost-guard text block appears exactly once (it recurs
    # across call snapshots only because the same list is re-sent each iter).
    last_call_text = "\n".join(
        b.get("text", "")
        for m in (client.calls[-1]["messages"] or [])
        if isinstance(m.get("content"), list)
        for b in m["content"]
        if isinstance(b, dict) and b.get("type") == "text"
    )
    assert last_call_text.count("0 tool-call turn(s) left") == 1


# ─── Loop wire — soft, not hard (AC5) ───────────────────────────────────────


def test_agent_loop_completes_after_cost_guard(monkeypatch):
    client = _install_client(monkeypatch, [
        _msg("tool_use", [_tool_use("t1", "view", {"path": "x"})],
             usage=_usage(out=_OVER_CAP_OUT)),
        _msg("end_turn", [_text("done")]),
    ])
    _stub_dispatch(monkeypatch)
    result = _run(agent_loop(_system(), _user(), _ctx()))
    # The guard NUDGES; it does not cancel/raise. A RunResult comes back.
    assert isinstance(result, RunResult)
    assert result.status == "complete"
    assert len(client.calls) == 2


# ─── Loop wire — coexistence with the count nudge (AC6) ─────────────────────


def test_cost_guard_coexists_with_count_nudge(monkeypatch, caplog):
    # max_iters=4 -> graduated schedule fires at remaining in {2, 1}. The cost
    # guard fires on iter1 (spend crossing). Both signals land in one run with no
    # error, each appended to the trailing user turn (alternation-safe).
    client = _install_client(monkeypatch, [
        _msg("tool_use", [_tool_use("t1", "view", {"path": "a"})],
             usage=_usage(out=_OVER_CAP_OUT)),   # iter1: cost guard
        _msg("tool_use", [_tool_use("t2", "view", {"path": "b"})],
             usage=_usage(out=_UNDER_CAP_OUT)),  # iter2: remaining=2 graduated
        _msg("tool_use", [_tool_use("t3", "view", {"path": "c"})],
             usage=_usage(out=_UNDER_CAP_OUT)),  # iter3: remaining=1 graduated
        _msg("end_turn", [_text("done")]),
    ])
    _stub_dispatch(monkeypatch)
    with caplog.at_level(logging.INFO):
        result = _run(agent_loop(_system(), _user(), _ctx(), max_iters=4))

    assert result.status == "complete"
    text = _all_call_text(client)
    # Cost-guard hard-stop (remaining=0) AND a graduated nudge (remaining=2) both
    # present — independent signals, both push convergence.
    assert "0 tool-call turn(s) left" in text
    assert "2 tool-call turn(s) left" in text
    guard_logs = [
        r for r in caplog.records
        if r.name == RUNNER_LOGGER and "cost_guard.degraded" in r.getMessage()
    ]
    assert len(guard_logs) == 1


# ─── Observability (AC7) ────────────────────────────────────────────────────


def test_cost_summary_log_still_emitted_with_guard(monkeypatch, caplog):
    # Drive the full generate_prototype so the per-run log_llm_run cost-summary
    # line is emitted; the iter also crosses the cap so cost_guard.degraded fires.
    # Both lines must coexist, and the guard line must carry identifiers + numbers
    # only (no system/user content).
    _install_client(monkeypatch, [
        _msg("end_turn", [_text("done")], usage=_usage(cache_read=10, inp=100, out=_OVER_CAP_OUT)),
    ])
    system_blocks = [
        {"type": "text", "text": "SECRET_SYSTEM_PROMPT_BODY do-not-log"},
        {"type": "text", "text": "tools", "cache_control": {"type": "ephemeral", "ttl": "1h"}},
    ]
    user_message = {"role": "user", "content": [_text("USER_PII_jane.doe@example.com")]}
    with caplog.at_level(logging.INFO):
        result, _vfs = _run(generate_prototype(
            prototype_id=77, workspace_id="app", system_blocks=system_blocks,
            user_message=user_message, figma_file_key=None, scenario="A",
        ))

    assert result.status == "complete"
    summary = [r for r in caplog.records if r.name == TELEMETRY_LOGGER]
    assert len(summary) == 1
    summary_msg = summary[0].getMessage()
    assert "design_agent.run.complete" in summary_msg
    assert "est_cost_usd=" in summary_msg

    guard = [
        r for r in caplog.records
        if r.name == RUNNER_LOGGER and "cost_guard.degraded" in r.getMessage()
    ]
    assert len(guard) == 1
    guard_msg = guard[0].getMessage()
    assert "prototype_id=77" in guard_msg
    assert "mode=scaffold" in guard_msg
    assert "est_cost_usd=" in guard_msg
    assert "cap=0.50" in guard_msg
    # Identifiers + numbers only — no prompt/source content leaks.
    assert "SECRET_SYSTEM_PROMPT_BODY" not in guard_msg
    assert "jane.doe@example.com" not in guard_msg


# ─── Non-breakage of the shared module (AC8) ────────────────────────────────


def test_llm_telemetry_existing_exports_unchanged():
    # Existing public surface intact: pricing table, RunUsage shape, log_llm_run
    # signature. The cost-guard helpers are PURELY additive.
    assert set(MODEL_PRICING.keys()) >= {"claude-sonnet-4-6", "claude-opus-4-7"}
    u = RunUsage()
    for field in ("cache_creation_input_tokens", "cache_read_input_tokens",
                  "input_tokens", "output_tokens"):
        assert hasattr(u, field)
    assert hasattr(u, "est_cost_usd")

    params = inspect.signature(log_llm_run).parameters
    for name in ("operation", "identifier", "usage", "duration_ms", "status", "model", "error_class"):
        assert name in params, f"log_llm_run lost keyword {name!r}"

    # The module still compiles cleanly with the additions.
    py_compile.compile(inspect.getsourcefile(__import__("app.llm_telemetry", fromlist=["x"])),
                       doraise=True)


def test_runner_imports_should_wrap_up_from_shared_module():
    # The wire reuses the shared helper (no local reimplementation) and the model
    # key is the AD2 sonnet identifier passed to it.
    assert runner.should_wrap_up is should_wrap_up
    assert MODEL == SONNET
    assert SOFT_CAP_USD == 0.50
