"""Unit tests for the Design Agent tool-use loop (P1-04).

The Anthropic client is replaced by a recording fake whose `messages.create`
is a sync callable (the runner invokes it via `asyncio.to_thread`, matching
prd_runner.py). The fake records, per call, a DEEP COPY of the `messages`
kwarg (so per-call snapshots survive the runner's in-place mutation of the
list) and a REFERENCE to the `system` kwarg (so the cache-identity test can
assert object identity).

Tests drive `agent_loop` via `asyncio.run(...)`, matching the
test_design_agent_tools.py convention.
"""
from __future__ import annotations

import asyncio
import copy
import logging
import sys
import time
import types

import pytest

from app.design_agent import runner
from app.design_agent.runner import RunResult, agent_loop, generate_prototype
from app.design_agent.tools import ToolContext

TELEMETRY_LOGGER = "app.llm_telemetry"


# ─── Fake Anthropic client ──────────────────────────────────────────────────


class _FakeBlock:
    """Mimics an Anthropic content block exposing `.model_dump()`."""

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
    """Sync `messages.create` that replays a list of responses.

    Each entry may be a `_FakeMessage` (returned) or an `Exception`
    instance (raised). When calls outrun the response list, the LAST
    response is replayed — convenient for "always tool_use" loop tests.
    """

    def __init__(self, responses):
        self._responses = list(responses)
        self.calls: list[dict] = []
        self.messages = types.SimpleNamespace(create=self._create)

    def _create(self, **kwargs):
        self.calls.append({
            "system": kwargs.get("system"),                       # by reference
            "messages": copy.deepcopy(kwargs.get("messages")),    # per-call snapshot
            "model": kwargs.get("model"),
            "max_tokens": kwargs.get("max_tokens"),
            "tools": kwargs.get("tools"),
        })
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


def _run(coro):
    return asyncio.run(coro)


def _all_content_blocks(messages: list[dict]) -> list[dict]:
    out: list[dict] = []
    for m in messages:
        content = m.get("content")
        if isinstance(content, list):
            out.extend(content)
    return out


# ─── Creation / basic exits ─────────────────────────────────────────────────


def test_agent_loop_end_turn_exits_clean(monkeypatch):
    _install_client(monkeypatch, [_msg("end_turn", [_text("done")])])
    result = _run(agent_loop(_system(), _user(), _ctx()))
    assert isinstance(result, RunResult)
    assert result.status == "complete"
    assert result.iters == 1
    assert result.final_content == [_text("done")]


def test_agent_loop_no_tool_calls_zero_iters_exits(monkeypatch):
    client = _install_client(monkeypatch, [_msg("end_turn", [_text("hi")])])
    result = _run(agent_loop(_system(), _user(), _ctx()))
    assert result.status == "complete"
    assert result.iters == 1
    assert len(client.calls) == 1


def test_agent_loop_unknown_stop_reason_treated_as_complete(monkeypatch):
    _install_client(monkeypatch, [_msg("surprise_stop", [_text("?")])])
    result = _run(agent_loop(_system(), _user(), _ctx()))
    assert result.status == "complete"


def test_model_identifier_is_sonnet_4_6(monkeypatch):
    client = _install_client(monkeypatch, [_msg("end_turn", [_text("done")])])
    _run(agent_loop(_system(), _user(), _ctx()))
    assert client.calls[0]["model"] == "claude-sonnet-4-6"


def test_agent_loop_handles_single_tool_call(monkeypatch):
    client = _install_client(monkeypatch, [
        _msg("tool_use", [_tool_use("t1", "view", {"path": "src/App.tsx"})]),
        _msg("end_turn", [_text("done")]),
    ])
    ctx = _ctx(virtual_fs={"src/App.tsx": "export default function App() {}"})
    result = _run(agent_loop(_system(), _user(), ctx))
    assert result.status == "complete"
    assert len(client.calls) == 2
    last_msg = client.calls[1]["messages"][-1]
    assert last_msg["role"] == "user"
    assert last_msg["content"][0]["type"] == "tool_result"


# ─── Loop bound + stop-reason handling ──────────────────────────────────────


def test_agent_loop_bounds_at_max_iters(monkeypatch):
    client = _install_client(monkeypatch, [
        _msg("tool_use", [_tool_use("t1", "view", {"path": "x"})]),  # replayed forever
    ])
    # Uses the production default (no explicit max_iters) so this also pins
    # DEFAULT_MAX_ITERS == 40 (raised from 24 by the convergence fix) — revert
    # the constant and this fails.
    result = _run(agent_loop(_system(), _user(), _ctx()))
    assert result.status == "max_iters"
    assert result.iters == 40
    assert len(client.calls) == 40


def test_wrap_up_nudge_escalates_by_remaining_count():
    """The nudge text escalates as the budget shrinks: a 'start converging'
    heads-up while there's room, a hard 'STOP now' in the last 1-2 turns."""
    soft = runner._wrap_up_nudge(10)
    assert "Start converging" in soft
    assert "10 tool-call turns left" in soft
    assert "STOP now" not in soft
    # Boundary: 2 remaining is already the hard stop.
    for n in (2, 1):
        hard = runner._wrap_up_nudge(n)
        assert "STOP now" in hard
        assert f"{n} tool-call turn(s) left" in hard
        assert "Start converging" not in hard


def test_graduated_wrap_up_nudge_fires_at_scheduled_remaining(monkeypatch):
    client = _install_client(monkeypatch, [
        _msg("tool_use", [_tool_use("t1", "view", {"path": "x"})]),
    ])
    # Pin max_iters=8 → nudge schedule remaining ∈ {8//2, max(2, 8//4), 1} = {4, 2, 1}.
    # Fixed call indices regardless of the production DEFAULT_MAX_ITERS — this
    # test exercises the graduated firing schedule, not the production cap.
    _run(agent_loop(_system(), _user(), _ctx(), max_iters=8))
    # remaining=4 at iters=4 → the soft 'converging' nudge is appended before
    # call index 3, and is NOT present yet at call index 2.
    assert any(
        "Start converging" in b.get("text", "")
        for b in _all_content_blocks(client.calls[3]["messages"])
    )
    assert not any(
        "Start converging" in b.get("text", "")
        for b in _all_content_blocks(client.calls[2]["messages"])
    )
    # remaining=2 at iters=6 → the hard 'STOP now' nudge appears at call index 5,
    # and is NOT present at call index 4 (iters=5, remaining=3 → no nudge fires).
    assert any(
        "STOP now" in b.get("text", "")
        for b in _all_content_blocks(client.calls[5]["messages"])
    )
    assert not any(
        "STOP now" in b.get("text", "")
        for b in _all_content_blocks(client.calls[4]["messages"])
    )


def test_max_iters_exit_salvages_last_assistant_content(monkeypatch):
    """On a max_iters exit the runner returns the LAST assistant turn's content
    (was discarded as []), so a build that ran out of turns mid-flow is staged
    rather than thrown away."""
    last_turn = [
        _text("Built the dashboard shell; wiring the detail view."),
        _tool_use("t9", "view", {"path": "src/App.tsx"}),
    ]
    client = _install_client(monkeypatch, [
        _msg("tool_use", last_turn),  # replayed forever → final iteration's content
    ])
    ctx = _ctx(virtual_fs={"src/App.tsx": "export default function App() {}"})
    result = _run(agent_loop(_system(), _user(), ctx, max_iters=3))
    assert result.status == "max_iters"
    assert result.iters == 3
    # Salvage is non-empty and equals the last assistant turn (by value).
    assert result.final_content != []
    assert result.final_content == last_turn
    # Sanity: it is genuinely the last turn the model produced, the 3rd call.
    assert len(client.calls) == 3


def test_wrap_up_nudge_does_not_create_consecutive_user_turns(monkeypatch):
    """Regression: the nudge must ride on the trailing user turn, never as a
    second consecutive user message (the Messages API treats turns as
    alternating)."""
    client = _install_client(monkeypatch, [
        _msg("tool_use", [_tool_use("t1", "view", {"path": "x"})]),
    ])
    _run(agent_loop(_system(), _user(), _ctx()))
    for call in client.calls:
        roles = [m["role"] for m in call["messages"]]
        for a, b in zip(roles, roles[1:]):
            assert a != b, f"consecutive same-role turns: {roles}"


def test_agent_loop_max_tokens_doubles_then_exits(monkeypatch):
    client = _install_client(monkeypatch, [
        _msg("max_tokens", [_text("...")]),
        _msg("max_tokens", [_text("...")]),
    ])
    result = _run(agent_loop(_system(), _user(), _ctx()))
    assert result.status == "max_tokens"
    assert client.calls[0]["max_tokens"] == 4096
    assert client.calls[1]["max_tokens"] == 8192  # doubled exactly once


def test_agent_loop_refusal_exits(monkeypatch):
    _install_client(monkeypatch, [_msg("refusal", [_text("I can't help with that.")])])
    result = _run(agent_loop(_system(), _user(), _ctx()))
    assert result.status == "refused"


# ─── max_tokens truncation contract (P2-03 regression) ───────────────────────


def _assert_no_orphan_tool_use(messages: list[dict]) -> None:
    """The exact Messages API contract the production 400 enforced (P2-03):
    every assistant `tool_use` block must be answered by a `tool_result` block
    (carrying its id) in the immediately-following user turn. A dangling
    tool_use — or a tool_use with no next message at all — is a 400.
    """
    for i, m in enumerate(messages):
        if m.get("role") != "assistant":
            continue
        content = m.get("content")
        if not isinstance(content, list):
            continue
        tool_use_ids = [b["id"] for b in content if b.get("type") == "tool_use"]
        if not tool_use_ids:
            continue
        assert i + 1 < len(messages), (
            f"assistant tool_use {tool_use_ids} is the last message — "
            f"no tool_result turn follows (would 400)"
        )
        nxt = messages[i + 1]
        assert nxt.get("role") == "user", (
            f"assistant tool_use {tool_use_ids} not followed by a user turn"
        )
        result_ids = {
            b.get("tool_use_id")
            for b in nxt["content"]
            if isinstance(b, dict) and b.get("type") == "tool_result"
        }
        for tid in tool_use_ids:
            assert tid in result_ids, f"tool_use {tid} has no matching tool_result"


def test_max_tokens_truncation_mid_tool_use_does_not_orphan(monkeypatch):
    """P2-03 root-cause regression. When the response is truncated by max_tokens
    WHILE the model is emitting a tool_use (e.g. a `write` whose `content` arg
    never finished serialising — observed as input keys == ['path'] only), the
    runner must NOT re-send that dangling tool_use turn. Doing so produced the
    real failure: BadRequestError 400 'messages.N: `tool_use` ids were found
    without `tool_result` blocks immediately after'. The fix discards the
    truncated turn and retries the SAME turn with a doubled budget.
    """
    # Truncated mid-tool_use: only `path` made it out, `content` was cut off.
    partial_write = _tool_use("toolu_partial", "write", {"path": "src/App.tsx"})
    client = _install_client(monkeypatch, [
        _msg("max_tokens", [partial_write]),       # truncated mid-emission
        _msg("end_turn", [_text("recovered")]),    # retry with doubled budget succeeds
    ])
    result = _run(agent_loop(_system(), _user(), _ctx()))

    # The loop recovered instead of 400-ing.
    assert result.status == "complete"
    assert result.final_content == [_text("recovered")]
    # Budget doubled exactly once.
    assert client.calls[0]["max_tokens"] == 4096
    assert client.calls[1]["max_tokens"] == 8192
    # The dangling tool_use turn was discarded — the retry re-sends the SAME
    # turn (back to the prior user turn), never the partial assistant turn.
    assert client.calls[1]["messages"] == client.calls[0]["messages"]
    partial_ids = {
        b.get("id")
        for m in client.calls[1]["messages"]
        for b in (m.get("content") or [])
        if isinstance(b, dict) and b.get("type") == "tool_use"
    }
    assert "toolu_partial" not in partial_ids
    # Every recorded request honours the tool_use↔tool_result pairing contract.
    for call in client.calls:
        _assert_no_orphan_tool_use(call["messages"])


def test_max_tokens_truncation_text_no_consecutive_assistant_turns(monkeypatch):
    """The pure-text truncation case (no tool_use): re-sending the partial
    assistant turn would create two consecutive assistant turns (also a 400).
    The discard-and-retry fix keeps roles strictly alternating."""
    client = _install_client(monkeypatch, [
        _msg("max_tokens", [_text("partial answer that got cut off ...")]),
        _msg("end_turn", [_text("the complete answer")]),
    ])
    result = _run(agent_loop(_system(), _user(), _ctx()))
    assert result.status == "complete"
    assert result.final_content == [_text("the complete answer")]
    for call in client.calls:
        roles = [m["role"] for m in call["messages"]]
        for a, b in zip(roles, roles[1:]):
            assert a != b, f"consecutive same-role turns: {roles}"


def test_max_tokens_twice_mid_tool_use_exits_clean(monkeypatch):
    """If BOTH the first attempt and the doubled-budget retry truncate mid-
    tool_use, the loop exits with status=max_tokens (second hit = exit) and
    still never emits an orphaned tool_use request."""
    partial = _tool_use("toolu_p", "write", {"path": "src/App.tsx"})
    client = _install_client(monkeypatch, [
        _msg("max_tokens", [partial]),
        _msg("max_tokens", [partial]),
    ])
    result = _run(agent_loop(_system(), _user(), _ctx()))
    assert result.status == "max_tokens"
    assert client.calls[0]["max_tokens"] == 4096
    assert client.calls[1]["max_tokens"] == 8192
    for call in client.calls:
        _assert_no_orphan_tool_use(call["messages"])


# ─── Cache verification ──────────────────────────────────────────────────────


def test_cache_control_breakpoint_preserved_across_iters(monkeypatch):
    client = _install_client(monkeypatch, [
        _msg("tool_use", [_tool_use("t1", "view", {"path": "x"})]),
    ])
    system_blocks = _system()
    # Pin max_iters=8 — this verifies cache-prefix stability across iterations,
    # not the production cap (24 per P2-01); a fixed count keeps the assertion
    # anchored.
    _run(agent_loop(system_blocks, _user(), _ctx(), max_iters=8))
    assert len(client.calls) == 8
    for call in client.calls:
        # Same object every iteration — no mutation invalidates the cache prefix.
        assert call["system"] is system_blocks
    # The breakpoint stayed at the END of the stable prefix.
    assert system_blocks[-1]["cache_control"] == {"type": "ephemeral", "ttl": "1h"}


def test_cache_read_tokens_counted_on_second_call(monkeypatch):
    _install_client(monkeypatch, [
        _msg("tool_use", [_tool_use("t1", "view", {"path": "src/A.tsx"})], usage=_usage(inp=500, out=100)),
        _msg("end_turn", [_text("done")], usage=_usage(cache_read=100, inp=10, out=20)),
    ])
    ctx = _ctx(virtual_fs={"src/A.tsx": "x"})
    result = _run(agent_loop(_system(), _user(), ctx))
    assert result.usage.cache_read_input_tokens >= 100


# ─── Parallel tool use ───────────────────────────────────────────────────────


def test_parallel_tool_use_bundled_in_one_user_message(monkeypatch):
    client = _install_client(monkeypatch, [
        _msg("tool_use", [
            _tool_use("a", "view", {"path": "src/A.tsx"}),
            _tool_use("b", "view", {"path": "src/B.tsx"}),
        ]),
        _msg("end_turn", [_text("done")]),
    ])
    ctx = _ctx(virtual_fs={"src/A.tsx": "a", "src/B.tsx": "b"})
    _run(agent_loop(_system(), _user(), ctx))
    last_msg = client.calls[1]["messages"][-1]
    assert last_msg["role"] == "user"
    content = last_msg["content"]
    # Both tool_result blocks present and FIRST in the array.
    assert content[0]["type"] == "tool_result"
    assert content[1]["type"] == "tool_result"
    assert {content[0]["tool_use_id"], content[1]["tool_use_id"]} == {"a", "b"}


def test_parallel_tool_dispatch_concurrent(monkeypatch):
    async def slow_dispatch(name, input, ctx, allowed_names=None):
        await asyncio.sleep(0.05)
        return {"content": "ok"}

    monkeypatch.setattr(runner, "dispatch", slow_dispatch)
    _install_client(monkeypatch, [
        _msg("tool_use", [
            _tool_use("a", "view", {"path": "A"}),
            _tool_use("b", "view", {"path": "B"}),
        ]),
        _msg("end_turn", [_text("done")]),
    ])
    start = time.perf_counter()
    _run(agent_loop(_system(), _user(), _ctx()))
    elapsed = time.perf_counter() - start
    # Concurrent gather → ~0.05s for both; serial would be ~0.10s+.
    assert elapsed < 0.15


# ─── Tool result formatting ──────────────────────────────────────────────────


def test_tool_result_block_carries_matching_tool_use_id(monkeypatch):
    client = _install_client(monkeypatch, [
        _msg("tool_use", [_tool_use("abc", "view", {"path": "src/A.tsx"})]),
        _msg("end_turn", [_text("done")]),
    ])
    ctx = _ctx(virtual_fs={"src/A.tsx": "x"})
    _run(agent_loop(_system(), _user(), ctx))
    tr = client.calls[1]["messages"][-1]["content"][0]
    assert tr["type"] == "tool_result"
    assert tr["tool_use_id"] == "abc"
    assert isinstance(tr["content"], str)  # JSON string


def test_tool_result_is_error_propagated(monkeypatch):
    async def err_dispatch(name, input, ctx, allowed_names=None):
        return {"is_error": True, "content": "boom"}

    monkeypatch.setattr(runner, "dispatch", err_dispatch)
    client = _install_client(monkeypatch, [
        _msg("tool_use", [_tool_use("t1", "view", {"path": "x"})]),
        _msg("end_turn", [_text("done")]),
    ])
    _run(agent_loop(_system(), _user(), _ctx()))
    tr = client.calls[1]["messages"][-1]["content"][0]
    assert tr["type"] == "tool_result"
    assert tr["is_error"] is True


def test_tool_result_content_truncated_at_25k(monkeypatch):
    async def big_dispatch(name, input, ctx, allowed_names=None):
        return {"content": "x" * 30000}

    monkeypatch.setattr(runner, "dispatch", big_dispatch)
    client = _install_client(monkeypatch, [
        _msg("tool_use", [_tool_use("t1", "view", {"path": "x"})]),
        _msg("end_turn", [_text("done")]),
    ])
    _run(agent_loop(_system(), _user(), _ctx()))
    tr = client.calls[1]["messages"][-1]["content"][0]
    assert len(tr["content"]) == 25000


# ─── Pathology detection ─────────────────────────────────────────────────────


def test_same_tool_call_3x_in_5_iters_injects_warning(monkeypatch):
    client = _install_client(monkeypatch, [
        _msg("tool_use", [_tool_use("t1", "view", {"path": "src/App.tsx"})]),  # identical, replayed
        _msg("tool_use", [_tool_use("t2", "view", {"path": "src/App.tsx"})]),
        _msg("tool_use", [_tool_use("t3", "view", {"path": "src/App.tsx"})]),
        _msg("end_turn", [_text("done")]),
    ])
    ctx = _ctx(virtual_fs={"src/App.tsx": "x"})  # view succeeds → isolates pathology warning
    _run(agent_loop(_system(), _user(), ctx))
    # Warning rides on the user turn built after the 3rd identical call (call index 3).
    blocks = _all_content_blocks(client.calls[3]["messages"])
    assert any("identical input" in b.get("text", "") for b in blocks)


def test_3_consecutive_tool_errors_inject_step_back_nudge(monkeypatch):
    async def err_dispatch(name, input, ctx, allowed_names=None):
        return {"is_error": True, "content": "kaboom"}

    monkeypatch.setattr(runner, "dispatch", err_dispatch)
    client = _install_client(monkeypatch, [
        _msg("tool_use", [_tool_use("t1", "view", {"path": "a"})]),  # distinct inputs:
        _msg("tool_use", [_tool_use("t2", "view", {"path": "b"})]),  # isolates the consec-error
        _msg("tool_use", [_tool_use("t3", "view", {"path": "c"})]),  # path from pathology
        _msg("end_turn", [_text("done")]),
    ])
    _run(agent_loop(_system(), _user(), _ctx()))
    blocks = _all_content_blocks(client.calls[3]["messages"])
    assert any("Step back" in b.get("text", "") for b in blocks)


# ─── Error handling ──────────────────────────────────────────────────────────


def test_anthropic_api_exception_returns_error_status(monkeypatch):
    _install_client(monkeypatch, [
        _msg("tool_use", [_tool_use("t1", "view", {"path": "x"})], usage=_usage(inp=50)),
        RuntimeError("boom"),
    ])
    result = _run(agent_loop(_system(), _user(), _ctx()))
    assert result.status == "error"
    assert result.error_class == "RuntimeError"
    assert result.error_message == "boom"
    assert result.usage.input_tokens == 50  # partial usage retained


def test_dispatch_exception_returns_is_error_tool_result(monkeypatch):
    # The real dispatch (P1-03) never raises — it converts an execute exception
    # into an is_error payload. Drive search with an invalid regex so the real
    # executor raises re.error and dispatch wraps it.
    client = _install_client(monkeypatch, [
        _msg("tool_use", [_tool_use("s1", "search", {"pattern": "["})]),
        _msg("end_turn", [_text("done")]),
    ])
    _run(agent_loop(_system(), _user(), _ctx()))
    tr = client.calls[1]["messages"][-1]["content"][0]
    assert tr["type"] == "tool_result"
    assert tr["is_error"] is True
    assert "error" in tr["content"]  # carries the exception class + message


# ─── _hash_tool_call ─────────────────────────────────────────────────────────


def test_hash_tool_call_is_deterministic():
    h1 = runner._hash_tool_call("view", {"path": "src/App.tsx"})
    h2 = runner._hash_tool_call("view", {"path": "src/App.tsx"})
    assert h1 == h2
    assert len(h1) == 16
    # Key order in the input dict must not change the hash.
    assert runner._hash_tool_call("view", {"a": 1, "b": 2}) == \
        runner._hash_tool_call("view", {"b": 2, "a": 1})


def test_hash_tool_call_differs_on_input():
    assert runner._hash_tool_call("view", {"path": "a"}) != \
        runner._hash_tool_call("view", {"path": "b"})


# ─── Figma token resolution (carry-forward flag #1) ──────────────────────────


def test_resolve_figma_token_none_when_no_file_key():
    # No file to fetch → no token resolution, no import side effects.
    assert runner._resolve_figma_access_token(None) is None


def test_resolve_figma_token_happy(monkeypatch):
    fake = types.ModuleType("app.routes.connectors")
    fake._figma_access_token = lambda: "figd_tok_123"
    monkeypatch.setitem(sys.modules, "app.routes.connectors", fake)
    assert runner._resolve_figma_access_token("FILEKEY") == "figd_tok_123"


def test_resolve_figma_token_nonfatal_on_connector_error(monkeypatch):
    fake = types.ModuleType("app.routes.connectors")

    def _raise():
        raise RuntimeError("Figma is not connected")

    fake._figma_access_token = _raise
    monkeypatch.setitem(sys.modules, "app.routes.connectors", fake)
    # Non-fatal: resolver swallows the error and returns None so the run proceeds.
    assert runner._resolve_figma_access_token("FILEKEY") is None


def test_generate_prototype_injects_figma_token_onto_ctx(monkeypatch):
    captured = {}

    async def fake_loop(*, system_blocks, user_message, ctx, scenario, mode):
        captured["ctx"] = ctx
        return RunResult(status="complete", iters=1, usage=runner.RunUsage(),
                         duration_ms=1, final_content=[])

    monkeypatch.setattr(runner, "_resolve_figma_access_token", lambda key: "tok-xyz")
    monkeypatch.setattr(runner, "agent_loop", fake_loop)
    _run(generate_prototype(
        prototype_id=7, workspace_id="app", system_blocks=_system(),
        user_message=_user(), figma_file_key="ABC", scenario="A",
    ))
    assert captured["ctx"].figma_access_token == "tok-xyz"
    assert captured["ctx"].figma_file_key == "ABC"
    assert captured["ctx"].prototype_id == 7
    assert captured["ctx"].workspace_id == "app"


# ─── Cost-summary log line ───────────────────────────────────────────────────


def test_generate_prototype_emits_cost_summary_log(monkeypatch, caplog):
    _install_client(monkeypatch, [
        _msg("end_turn", [_text("done")], usage=_usage(cache_read=10, inp=100, out=50)),
    ])
    with caplog.at_level(logging.INFO, logger=TELEMETRY_LOGGER):
        # P1-08: generate_prototype now returns (RunResult, virtual_fs).
        result, _virtual_fs = _run(generate_prototype(
            prototype_id=42, workspace_id="app", system_blocks=_system(),
            user_message=_user(), figma_file_key=None, scenario="A",
        ))
    assert result.status == "complete"
    records = [r for r in caplog.records if r.name == TELEMETRY_LOGGER]
    assert len(records) == 1
    msg = records[0].getMessage()
    assert "design_agent.run.complete" in msg
    assert "prototype_id=42" in msg
    assert "scenario=A" in msg
    assert "mode=scaffold" in msg
    for field in ("cached_input_tokens=", "input_tokens=", "output_tokens=",
                  "duration_ms=", "est_cost_usd=", "status=complete", "iters="):
        assert field in msg, f"missing {field!r}"


def test_cost_summary_redacts_pii_and_secrets(monkeypatch, caplog):
    _install_client(monkeypatch, [_msg("end_turn", [_text("done")])])
    system_blocks = [
        {"type": "text", "text": "SECRET_SYSTEM_PROMPT_BODY do-not-log"},
        {"type": "text", "text": "tools", "cache_control": {"type": "ephemeral", "ttl": "1h"}},
    ]
    user_message = {"role": "user", "content": [_text("USER_PII_jane.doe@example.com")]}
    with caplog.at_level(logging.INFO, logger=TELEMETRY_LOGGER):
        _run(generate_prototype(
            prototype_id=1, workspace_id="app", system_blocks=system_blocks,
            user_message=user_message, figma_file_key=None,
        ))
    msg = next(r.getMessage() for r in caplog.records if r.name == TELEMETRY_LOGGER)
    assert "SECRET_SYSTEM_PROMPT_BODY" not in msg
    assert "jane.doe@example.com" not in msg


def test_cost_summary_emitted_even_on_error(monkeypatch, caplog):
    _install_client(monkeypatch, [RuntimeError("api exploded")])
    with caplog.at_level(logging.INFO, logger=TELEMETRY_LOGGER):
        # P1-08: generate_prototype now returns (RunResult, virtual_fs).
        result, _virtual_fs = _run(generate_prototype(
            prototype_id=9, workspace_id="app", system_blocks=_system(),
            user_message=_user(), figma_file_key=None,
        ))
    assert result.status == "error"
    msg = next(r.getMessage() for r in caplog.records if r.name == TELEMETRY_LOGGER)
    assert "status=error" in msg
    assert "error_class=RuntimeError" in msg


# ─── P7-01 regression: lock the AD15 soft-cap envelope ──────────────────────
# These guard against the S2 ux-explore DO-NOT-COMMIT dev-hack
# (DEFAULT_MAX_TOKENS 4096 -> 16000) ever silently riding into a commit, and
# pin the AD2 model. They assert the working-tree module symbols directly — no
# historical git rev is consulted (CI uses shallow clones).


def test_default_max_tokens_is_4096():
    """P7-01 AC1/AC3: the per-turn cap stays at 4096; fails on the 16000 hack."""
    assert runner.DEFAULT_MAX_TOKENS == 4096


def test_agent_loop_default_max_tokens_kwarg_is_4096():
    """P7-01: the default ``max_tokens`` kwarg resolves to 4096.

    The ticket calls this entry point ``run``; the actual public function
    carrying ``max_tokens: int = DEFAULT_MAX_TOKENS`` (runner.py:250) is
    ``agent_loop``. Asserting the resolved default catches both a constant
    change and a signature override.
    """
    import inspect

    default = inspect.signature(runner.agent_loop).parameters["max_tokens"].default
    assert default == 4096


def test_model_pin_is_sonnet_4_6():
    """P7-01 AC2 / AD2 / D4: model constant stays sonnet-4-6 — no drift to 4-7."""
    assert runner.MODEL == "claude-sonnet-4-6"


# ─── SSE publish_step wiring ─────────────────────────────────────────────────


def test_publish_step_called_once_per_loop_iteration(monkeypatch):
    """publish_step fires at the start of every loop iteration so the SSE
    stream gets a progress breadcrumb for each tool-use cycle."""
    calls: list[tuple] = []

    def _capture(pid, event):
        calls.append((pid, event))

    monkeypatch.setattr(runner, "publish_step", _capture)

    # Two iterations: tool_use on iter 1, end_turn on iter 2.
    client = _install_client(monkeypatch, [
        _msg("tool_use", [_tool_use("t1", "view", {"path": "/"})]),
        _msg("end_turn", [_text("done")]),
    ])

    async def _fake_dispatch(name, input, ctx, allowed_names=None):
        return {"content": "ok"}

    monkeypatch.setattr(runner, "dispatch", _fake_dispatch)

    _run(agent_loop(_system(), _user(), _ctx(prototype_id=42)))

    assert len(calls) == 2, f"expected 2 publish_step calls, got {len(calls)}"
    for pid, ev in calls:
        assert pid == 42
        assert ev["kind"] == "step"
        assert ev["state"] == "active"


def test_sse_stream_closed_with_done_on_complete_run(monkeypatch):
    """_sse_close is called with kind='done' when the run completes normally."""
    closed: list[tuple] = []

    def _capture_close(pid, *, kind):
        closed.append((pid, kind))

    monkeypatch.setattr(runner, "_sse_close", _capture_close)

    _install_client(monkeypatch, [_msg("end_turn", [_text("done")])])
    _run(agent_loop(_system(), _user(), _ctx(prototype_id=7)))

    assert closed == [(7, "done")]


def test_sse_stream_closed_with_error_on_non_complete_exit(monkeypatch):
    """_sse_close is called with kind='error' for any non-complete exit (max_iters)."""
    closed: list[tuple] = []

    def _capture_close(pid, *, kind):
        closed.append((pid, kind))

    monkeypatch.setattr(runner, "_sse_close", _capture_close)

    # One tool_use and max_iters=1 → the loop exits via max_iters (not complete).
    async def _fake_dispatch(name, input, ctx, allowed_names=None):
        return {"content": "ok"}

    monkeypatch.setattr(runner, "dispatch", _fake_dispatch)

    _install_client(monkeypatch, [_msg("tool_use", [_tool_use("t1", "view", {"path": "/"})])])
    _run(agent_loop(_system(), _user(), _ctx(prototype_id=8), max_iters=1))

    assert closed == [(8, "error")]


def test_sse_not_closed_on_awaiting_clarification(monkeypatch):
    """_sse_close is NOT called when the run pauses for a clarifying question —
    the SSE stream stays open while the user composes their reply.

    The loop detects clarifying_question in the tool_use block list and returns
    early BEFORE dispatching any tool, so no _dispatch_tool stub is needed.
    """
    closed: list[tuple] = []

    def _capture_close(pid, *, kind):
        closed.append((pid, kind))

    monkeypatch.setattr(runner, "_sse_close", _capture_close)

    cq_block = _tool_use("cq1", "clarifying_question", {"question": "Which palette?"})
    _install_client(monkeypatch, [_msg("tool_use", [cq_block])])

    result = _run(agent_loop(_system(), _user(), _ctx(prototype_id=9)))
    assert result.status == "awaiting_clarification"
    assert closed == [], "stream must remain open during awaiting_clarification pause"
