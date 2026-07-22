"""Unit tests for the AD15 hard cost-abort ceiling (P6-06).

The fail-closed BACKSTOP above the P5-03 soft cap. Three surfaces:

1. The PURE decision helper `app/llm_telemetry.should_abort` — deterministic,
   no network, reuses `MODEL_PRICING`/`project_next_iter_cost` via
   `RunUsage.est_cost_usd`, fails closed on an unpriced model. No second pricing
   table (mirrors `should_wrap_up`).

2. The ~8-line wire in `agent_loop`: when projected next-iteration spend crosses
   `HARD_CAP_USD`, the loop emits a `cost_guard.aborted` log line and returns a
   clean terminal `RunResult(status="aborted", ...)` salvaging the current
   assistant turn — it NEVER raises. The hard cap is ADDITIVE to the soft nudge
   (the soft `cost_guard.degraded` still records first on a single iteration
   crossing both caps); the abort then wins (returns immediately).

3. The `design_agent_hard_cap_usd` env knob on `Settings` (default 5.00).

The Anthropic client is replaced by a recording fake whose `messages.create` is
a sync callable (the runner invokes it via `asyncio.to_thread`). Mirrors
test_design_agent_cost_guard.py.
"""
from __future__ import annotations

import asyncio
import copy
import inspect
import logging
import py_compile
import types

import pytest

from app.config import Settings
from app.design_agent import runner
from app.design_agent.runner import (
    DEFAULT_MAX_ITERS,
    HARD_CAP_USD,
    MODEL,
    SOFT_CAP_USD,
    RunResult,
    agent_loop,
    generate_prototype,
)
from app.design_agent.tools import ToolContext
from app.llm_telemetry import (
    MODEL_PRICING,
    RunUsage,
    UnknownModelError,
    log_llm_run,
    project_next_iter_cost,
    should_abort,
    should_wrap_up,
)
from tests._fake_anthropic import _FakeStream

RUNNER_LOGGER = "app.design_agent.runner"
TELEMETRY_LOGGER = "app.llm_telemetry"
SONNET = "claude-sonnet-4-6"

# At sonnet output pricing (15.0 / 1M = 1.5e-5 $/tok):
#   200_000 out → $3.00 realized → projection (2x) = $6.00 >= $5.00 hard cap.
#    50_667 out → $0.7600 realized → projection = $1.52 < $5.00 (observed-legit run).
#     1_000 out → $0.015 realized → projection = $0.03 (far under both caps).
_ABORT_OUT = 200_000
_LEGIT_076_OUT = 50_667
_UNDER_CAP_OUT = 1_000


# ─── Fakes (compact mirror of test_design_agent_cost_guard.py) ──────────────


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
    when calls outrun the list (handy for 'always tool_use' runaway loops)."""

    def __init__(self, responses):
        self._responses = list(responses)
        self.calls: list[dict] = []
        self.messages = types.SimpleNamespace(create=self._create, stream=self._stream)

    def _create(self, **kwargs):
        self.calls.append({"messages": copy.deepcopy(kwargs.get("messages"))})
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
    """No-op tool dispatch so loop tests don't depend on real tool semantics."""

    async def fake_dispatch(name, inp, ctx, allowed_names=None):
        return {"content": "ok", "tool_name": name}

    monkeypatch.setattr(runner, "dispatch", fake_dispatch)


def _run(coro):
    return asyncio.run(coro)


def _records(caplog, logger_name, needle):
    return [
        r for r in caplog.records
        if r.name == logger_name and needle in r.getMessage()
    ]


# ─── Pure helper — boundary decision (AC1) ──────────────────────────────────


def test_should_abort_true_at_and_above_cap():
    # Clearly above: realized $3.00 → projection $6.00 >= the $2.00 arg.
    over = RunUsage(output_tokens=_ABORT_OUT)
    assert should_abort(over, SONNET, 2.00) is True
    # Boundary-exact (inclusive): cap == projection -> True. Computed from the
    # usage itself so the equality is exact regardless of token granularity.
    proj = project_next_iter_cost(over, SONNET)
    assert should_abort(over, SONNET, proj) is True


def test_should_abort_false_below_cap():
    # Observed-legit run: realized $0.76 → projection $1.52 < the $2.00 arg.
    legit = RunUsage(output_tokens=_LEGIT_076_OUT)
    assert should_abort(legit, SONNET, 2.00) is False
    # Just above the projection the cap is not reached.
    proj = project_next_iter_cost(legit, SONNET)
    assert should_abort(legit, SONNET, proj + 1e-9) is False


def test_should_abort_is_pure_and_deterministic():
    usage = RunUsage(output_tokens=_ABORT_OUT)
    a = should_abort(usage, SONNET, 2.00)
    b = should_abort(usage, SONNET, 2.00)
    assert a is b is True
    # No mutation of the usage object.
    assert usage.output_tokens == _ABORT_OUT


# ─── Pure helper — fail closed + single pricing table (AC2) ─────────────────


def test_should_abort_fails_closed_unknown_model():
    usage = RunUsage(output_tokens=1_000)
    with pytest.raises(UnknownModelError):
        should_abort(usage, "claude-not-a-real-model", 2.00)


def test_single_pricing_table():
    import app.llm_telemetry as telemetry

    src = inspect.getsource(telemetry)
    # Exactly one pricing-table definition in the module.
    assert src.count("MODEL_PRICING: dict[str, dict[str, float]] = {") == 1
    # The new helper carries NO pricing literals — it reuses MODEL_PRICING via
    # project_next_iter_cost rather than duplicating any per-token math.
    abort_src = inspect.getsource(should_abort)
    assert "1_000_000" not in abort_src
    assert "cache_write_1h" not in abort_src
    assert "project_next_iter_cost" in abort_src


# ─── Pure helper — hard cap > soft cap invariant (AC3) ──────────────────────


def test_hard_cap_implies_soft_cap():
    # For any usage: if should_abort(hard) is True, should_wrap_up(soft) is too —
    # both use the same projection and HARD_CAP_USD (5.00) > SOFT_CAP_USD (0.50),
    # so the hard cap can never fire before the soft nudge for a given run.
    assert HARD_CAP_USD == 5.00
    assert SOFT_CAP_USD == 0.50
    for out in (_ABORT_OUT, _ABORT_OUT * 2, _ABORT_OUT * 5):
        usage = RunUsage(output_tokens=out)
        if should_abort(usage, SONNET, HARD_CAP_USD):
            assert should_wrap_up(usage, SONNET, SOFT_CAP_USD) is True


# ─── Regression (fail on unfixed code) ───────────────────────────────────────


def test_runaway_run_aborts_over_hard_cap(monkeypatch):
    # Every response is tool_use with a pathological output size; the LAST entry
    # replays so an unfixed loop runs to max_iters. With the hard cap, iter1's
    # projected spend ($6.00) crosses $5.00 and the loop ABORTS.
    client = _install_client(monkeypatch, [
        _msg("tool_use", [_tool_use("t1", "view", {"path": "x"})],
             usage=_usage(out=_ABORT_OUT)),
    ])
    _stub_dispatch(monkeypatch)
    result = _run(agent_loop(_system(), _user(), _ctx()))
    assert result.status == "aborted"  # fails on unfixed code (would be max_iters)


def test_pathological_run_does_not_reach_max_iters(monkeypatch):
    client = _install_client(monkeypatch, [
        _msg("tool_use", [_tool_use("t1", "view", {"path": "x"})],
             usage=_usage(out=_ABORT_OUT)),
    ])
    _stub_dispatch(monkeypatch)
    result = _run(agent_loop(_system(), _user(), _ctx()))
    # The hard cap trips on iter1 — well before the 40-iter rail. On unfixed code
    # only DEFAULT_MAX_ITERS stops it (iters == 40, status max_iters).
    assert result.iters < DEFAULT_MAX_ITERS
    assert result.iters == 1
    assert len(client.calls) == 1


# ─── Loop wire — abort with salvaged content (AC4) ──────────────────────────


def test_agent_loop_aborts_with_salvaged_content(monkeypatch, caplog):
    tool_block = _tool_use("t1", "view", {"path": "x"})
    client = _install_client(monkeypatch, [
        _msg("tool_use", [tool_block], usage=_usage(out=_ABORT_OUT)),
    ])
    _stub_dispatch(monkeypatch)
    with caplog.at_level(logging.INFO):
        result = _run(agent_loop(_system(), _user(), _ctx()))

    assert result.status == "aborted"
    # final_content is the CURRENT (aborting) iteration's assistant turn — salvaged,
    # not empty, not the initial [] from iteration-1 init.
    assert result.final_content  # non-empty
    assert result.final_content[0]["type"] == "tool_use"
    assert result.final_content[0]["id"] == "t1"

    aborted = _records(caplog, RUNNER_LOGGER, "cost_guard.aborted")
    assert len(aborted) == 1
    msg = aborted[0].getMessage()
    assert "reason=hard_cap_projection" in msg
    assert "prototype_id=1" in msg
    assert "mode=scaffold" in msg
    assert "est_cost_usd=" in msg
    assert "hard_cap=5.00" in msg
    assert "soft_cap=0.50" in msg
    assert "iters=1" in msg
    # WARNING level — a budget kill is worth surfacing above the degrade nudge.
    assert aborted[0].levelno == logging.WARNING


# ─── Loop wire — clean terminal, no raise (AC7) ─────────────────────────────


def test_agent_loop_does_not_raise_on_abort(monkeypatch):
    client = _install_client(monkeypatch, [
        _msg("tool_use", [_tool_use("t1", "view", {"path": "x"})],
             usage=_usage(out=_ABORT_OUT)),
    ])
    _stub_dispatch(monkeypatch)
    # Returns via the NORMAL return path (not the except branch) — a RunResult,
    # not an exception. error_class/error_message stay None (abort is not an error).
    result = _run(agent_loop(_system(), _user(), _ctx()))
    assert isinstance(result, RunResult)
    assert result.status == "aborted"
    assert result.error_class is None
    assert result.error_message is None


# ─── Loop wire — no trip on a legit run (AC5) ───────────────────────────────


def test_legit_076_run_does_not_abort(monkeypatch, caplog):
    # Realized $0.76 (projects to $1.52 < $5.00) — a legit run must NOT abort.
    # The soft-cap degrade MAY fire ($1.52 >= $0.50), but no abort.
    client = _install_client(monkeypatch, [
        _msg("tool_use", [_tool_use("t1", "view", {"path": "x"})],
             usage=_usage(out=_LEGIT_076_OUT)),
        _msg("end_turn", [_text("done")]),
    ])
    _stub_dispatch(monkeypatch)
    with caplog.at_level(logging.INFO):
        result = _run(agent_loop(_system(), _user(), _ctx()))

    assert result.status == "complete"
    assert len(client.calls) == 2
    assert _records(caplog, RUNNER_LOGGER, "cost_guard.aborted") == []


# ─── Loop wire — soft nudge recorded before abort (AC6) ─────────────────────


def test_soft_nudge_recorded_before_abort(monkeypatch, caplog):
    # A single iteration crossing BOTH caps: the soft-cap block records its
    # cost_guard.degraded FIRST, then the abort block returns immediately. The
    # loop does not run a further iteration.
    client = _install_client(monkeypatch, [
        _msg("tool_use", [_tool_use("t1", "view", {"path": "x"})],
             usage=_usage(out=_ABORT_OUT)),  # projects $6.00 — crosses soft AND hard
        _msg("end_turn", [_text("should never be reached")]),
    ])
    _stub_dispatch(monkeypatch)
    with caplog.at_level(logging.INFO):
        result = _run(agent_loop(_system(), _user(), _ctx()))

    assert result.status == "aborted"
    assert len(client.calls) == 1  # abort returned before a second create call

    degraded = _records(caplog, RUNNER_LOGGER, "cost_guard.degraded")
    aborted = _records(caplog, RUNNER_LOGGER, "cost_guard.aborted")
    assert len(degraded) == 1
    assert len(aborted) == 1
    # Chronological order: soft nudge logged before the abort.
    assert caplog.records.index(degraded[0]) < caplog.records.index(aborted[0])


# ─── Observability — cost-summary still fires on abort (AC8) ────────────────


def test_cost_summary_still_emitted_on_abort(monkeypatch, caplog):
    # Drive the full generate_prototype so the per-run run.complete cost-summary
    # line is emitted; the single turn crosses the hard cap so the run aborts.
    # Both lines must coexist; the cost_guard.aborted line carries identifiers +
    # numbers only (no system/user content leaks).
    _install_client(monkeypatch, [
        _msg("end_turn", [_text("done")],
             usage=_usage(cache_read=10, inp=100, out=_ABORT_OUT)),
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

    assert result.status == "aborted"

    # P5-08 cost-summary line fires with status=aborted (unchanged path).
    summary = _records(caplog, TELEMETRY_LOGGER, "design_agent.run.complete")
    assert len(summary) == 1
    assert "status=aborted" in summary[0].getMessage()

    # The in-loop abort line — identifiers + numbers only.
    aborted = _records(caplog, RUNNER_LOGGER, "cost_guard.aborted")
    assert len(aborted) == 1
    abort_msg = aborted[0].getMessage()
    assert "prototype_id=77" in abort_msg
    assert "mode=scaffold" in abort_msg
    assert "est_cost_usd=" in abort_msg
    assert "hard_cap=5.00" in abort_msg
    assert "SECRET_SYSTEM_PROMPT_BODY" not in abort_msg
    assert "jane.doe@example.com" not in abort_msg


# ─── publish_step visibility on cost-guard abort ────────────────────────────


def test_publish_step_emits_cost_guard_message_before_abort_finish(monkeypatch):
    order: list[str] = []
    monkeypatch.setattr(runner, "publish_step", lambda pid, ev: order.append(f"publish_step:{ev.get('text')}"))
    monkeypatch.setattr(runner, "_sse_close", lambda *a, **kw: order.append("_sse_close"))
    _install_client(monkeypatch, [
        _msg("tool_use", [_tool_use("t1", "view", {"path": "x"})], usage=_usage(out=_ABORT_OUT)),
    ])
    _stub_dispatch(monkeypatch)
    result = _run(agent_loop(_system(), _user(), _ctx()))
    assert result.status == "aborted"
    cost_guard_entries = [e for e in order if e == "publish_step:Generation stopped early to control cost"]
    assert len(cost_guard_entries) == 1
    assert order[-1] == "_sse_close"
    assert order[-2] == "publish_step:Generation stopped early to control cost"


# ─── Config / env knob ───────────────────────────────────────────────────────


def test_hard_cap_default_is_five_dollars(monkeypatch):
    # No env set → default 5.00. _env_file=None disables .env so a local
    # backend/.env can never perturb the default assertion.
    monkeypatch.delenv("DESIGN_AGENT_HARD_CAP_USD", raising=False)
    assert Settings(_env_file=None).design_agent_hard_cap_usd == 5.00


def test_hard_cap_env_override(monkeypatch):
    monkeypatch.setenv("DESIGN_AGENT_HARD_CAP_USD", "3.50")
    assert Settings(_env_file=None).design_agent_hard_cap_usd == 3.50


def test_runner_hard_cap_is_above_soft_cap():
    # The wired constants honour the invariant: hard strictly above soft, and the
    # default never dips below the $1.52 legit-run projection floor.
    assert HARD_CAP_USD > SOFT_CAP_USD
    assert HARD_CAP_USD > 1.52


# ─── Non-breakage of the shared module (AC9) ────────────────────────────────


def test_llm_telemetry_existing_exports_unchanged():
    # Existing public surface intact: pricing table, RunUsage shape, log_llm_run
    # signature, should_wrap_up/project_next_iter_cost. should_abort is PURELY
    # additive.
    assert set(MODEL_PRICING.keys()) >= {"claude-sonnet-4-6", "claude-opus-4-7"}
    u = RunUsage()
    for field in ("cache_creation_input_tokens", "cache_read_input_tokens",
                  "input_tokens", "output_tokens"):
        assert hasattr(u, field)
    assert hasattr(u, "est_cost_usd")

    # should_wrap_up / project_next_iter_cost signatures unchanged.
    assert list(inspect.signature(should_wrap_up).parameters) == ["usage", "model", "soft_cap"]
    assert list(inspect.signature(project_next_iter_cost).parameters) == ["usage", "model"]
    # should_abort mirrors the soft-cap helper's shape.
    assert list(inspect.signature(should_abort).parameters) == ["usage", "model", "hard_cap"]

    params = inspect.signature(log_llm_run).parameters
    for name in ("operation", "identifier", "usage", "duration_ms", "status", "model", "error_class"):
        assert name in params, f"log_llm_run lost keyword {name!r}"

    # The module still compiles cleanly with the addition.
    py_compile.compile(inspect.getsourcefile(__import__("app.llm_telemetry", fromlist=["x"])),
                       doraise=True)


def test_runner_imports_should_abort_from_shared_module():
    # The wire reuses the shared helper (no local reimplementation).
    assert runner.should_abort is should_abort
    assert MODEL == SONNET
