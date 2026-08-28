"""Unit test for app.llm.run_tool_loop with a faked Anthropic client.

Simulates: turn 1 returns a tool_use, turn 2 returns end_turn text. Verifies
the dispatch runs, the tool_result is fed back, and the final text is returned.
"""
from __future__ import annotations

import app.llm as llm


class _Block:
    def __init__(self, **kw):
        self.__dict__.update(kw)


class _Msg:
    def __init__(self, content, stop_reason):
        self.content = content
        self.stop_reason = stop_reason
        self.usage = _Block(
            input_tokens=10, output_tokens=5,
            cache_creation_input_tokens=0, cache_read_input_tokens=0,
        )


class _FakeMessages:
    def __init__(self, scripted):
        self._scripted = scripted
        self.calls = []

    def create(self, **kwargs):
        self.calls.append(kwargs)
        return self._scripted.pop(0)


class _FakeClient:
    def __init__(self, scripted):
        self.messages = _FakeMessages(scripted)


def test_run_tool_loop_dispatches_then_finishes(monkeypatch):
    scripted = [
        _Msg(
            [
                _Block(type="text", text="let me compute"),
                _Block(type="tool_use", id="tu_1", name="calc", input={"x": 2}),
            ],
            stop_reason="tool_use",
        ),
        _Msg([_Block(type="text", text="the answer is 4")], stop_reason="end_turn"),
    ]
    fake = _FakeClient(scripted)
    monkeypatch.setattr(llm, "get_client", lambda: fake)

    dispatched = []

    def dispatch(name, inp):
        dispatched.append((name, inp))
        return "4"

    meta: dict = {}
    out = llm.run_tool_loop(
        system="sys",
        user="what is 2+2",
        tools=[{"name": "calc", "description": "d", "input_schema": {"type": "object"}}],
        dispatch=dispatch,
        meta_out=meta,
    )
    assert out == "the answer is 4"
    assert dispatched == [("calc", {"x": 2})]
    # second turn carried the tool_result back
    second = fake.messages.calls[1]
    tool_result_turn = second["messages"][-1]
    assert tool_result_turn["content"][0]["type"] == "tool_result"
    assert tool_result_turn["content"][0]["content"] == "4"
    assert meta["model"]  # usage captured from the last turn


def test_run_tool_loop_respects_max_iters(monkeypatch):
    # Always asks for a tool → must stop at max_iters, not loop forever.
    def always_tool(**_):
        return _Msg(
            [_Block(type="tool_use", id="tu", name="calc", input={})],
            stop_reason="tool_use",
        )

    class _Eternal:
        def __init__(self):
            self.messages = self
            self.n = 0

        def create(self, **kwargs):
            self.n += 1
            return always_tool()

    fake = _Eternal()
    monkeypatch.setattr(llm, "get_client", lambda: fake)
    out = llm.run_tool_loop(
        system="s", user="u",
        tools=[{"name": "calc", "description": "d", "input_schema": {"type": "object"}}],
        dispatch=lambda n, i: "x",
        max_iters=3,
    )
    assert fake.n == 3  # bounded
    assert isinstance(out, str)


def test_run_tool_loop_warns_when_exhausted_empty(monkeypatch, caplog):
    """Exhaustion guard: when the model calls a tool on every turn and never
    composes a closing text turn, the loop returns "" — and now emits a
    WARNING so the empty-answer failure mode stops being invisible in logs."""
    import logging

    def always_tool(**_):
        return _Msg(
            [_Block(type="tool_use", id="tu", name="calc", input={})],
            stop_reason="tool_use",
        )

    class _Eternal:
        def __init__(self):
            self.messages = self

        def create(self, **kwargs):
            return always_tool()

    monkeypatch.setattr(llm, "get_client", lambda: _Eternal())
    with caplog.at_level(logging.WARNING, logger="app.llm"):
        out = llm.run_tool_loop(
            system="s", user="u",
            tools=[{"name": "calc", "description": "d", "input_schema": {"type": "object"}}],
            dispatch=lambda n, i: "x",
            max_iters=2,
        )
    assert out == ""  # no closing text ever produced
    assert any(
        "run_tool_loop exhausted" in r.getMessage() and "max_iters=2" in r.getMessage()
        for r in caplog.records
    )
