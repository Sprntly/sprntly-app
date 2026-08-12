"""§A — the private-chat bounded tool-loop responder (`respond_individual`) and
its `ask_job_runner` integration.

Covers: the loop round-trips a tool_use → tool_result → text (AC1); it honours
`max_iters=5` on a runaway model (AC2); a no-tool turn returns single-pass
(AC3); a NON-project ask still uses single-shot and never calls the responder
(AC4); dispatch is scoped to this ask's ids (AC5); exactly one identifiers-only
cost line per project reply and zero for a non-project ask (AC7); and a
`run_tool_loop` raise degrades to the single-shot answer (AC8).

The Anthropic client is faked (mirrors `test_llm_tool_loop.py`); the
`ask_job_runner` branch tests drive `run_ask_job` with the qa_agent answer +
job I/O monkeypatched (mirrors `test_ask_project_promotion.py`).
"""
from __future__ import annotations

import asyncio
import logging

import app.llm as llm
import app.ask_job_runner as ajr
import app.project_individual_agent as pia


# ── Fake Anthropic client (tool loop) ────────────────────────────────────────
class _Block:
    def __init__(self, **kw):
        self.__dict__.update(kw)


class _Msg:
    def __init__(self, content, stop_reason, model="claude-sonnet-4-6"):
        self.content = content
        self.stop_reason = stop_reason
        self.model = model
        self.usage = _Block(
            input_tokens=11, output_tokens=7,
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


def _single_shot_stub(answer="SINGLE-SHOT"):
    return lambda: {"answer": answer, "key_points": [], "citations": []}


# ── AC1 / AC3 / AC7 — loop round-trip + one cost line ────────────────────────
def test_individual_loop_tool_roundtrip(monkeypatch, caplog):
    scripted = [
        _Msg(
            [
                _Block(type="text", text="checking artifacts"),
                _Block(type="tool_use", id="tu1", name="list_project_artifacts", input={}),
            ],
            stop_reason="tool_use",
        ),
        _Msg([_Block(type="text", text="You have 2 PRDs.")], stop_reason="end_turn"),
    ]
    monkeypatch.setattr(llm, "get_client", lambda: _FakeClient(scripted))
    monkeypatch.setattr(
        pia, "dispatch_read_tool",
        lambda name, ti, **kw: "prd id=1: Alpha\nprd id=2: Beta",
    )

    with caplog.at_level(logging.INFO):
        payload = pia.respond_individual(
            project_id=9, dataset="d", company_id="c1",
            question="how many PRDs?", history=[], allow_prd_edit=False,
            single_shot=_single_shot_stub(),
        )
    assert payload == {"answer": "You have 2 PRDs.", "citations": []}
    # AC7 — exactly one cost line, identifiers only (no question/answer body).
    cost = [r.getMessage() for r in caplog.records if "individual_chat.reply" in r.getMessage()]
    assert len(cost) == 1
    assert "project_id" in cost[0]
    assert "how many PRDs" not in cost[0] and "You have 2 PRDs" not in cost[0]


def test_individual_loop_no_tool_call_single_pass(monkeypatch):
    # AC3 — a model turn with no tool_use returns its text directly, no dispatch.
    scripted = [_Msg([_Block(type="text", text="Hi there.")], stop_reason="end_turn")]
    monkeypatch.setattr(llm, "get_client", lambda: _FakeClient(scripted))

    def _no_dispatch(*a, **kw):  # noqa: ARG001
        raise AssertionError("no tool should be dispatched on a single pass")

    monkeypatch.setattr(pia, "dispatch_read_tool", _no_dispatch)
    payload = pia.respond_individual(
        project_id=9, dataset="d", company_id="c1",
        question="hi", history=[], allow_prd_edit=False,
        single_shot=_single_shot_stub(),
    )
    assert payload["answer"] == "Hi there."


def test_individual_loop_bounds_at_max_iters(monkeypatch):
    # AC2 — a model that emits tool_use every turn returns after ≤5 model calls.
    class _Eternal:
        def __init__(self):
            self.messages = self
            self.n = 0

        def create(self, **kwargs):
            self.n += 1
            return _Msg(
                [_Block(type="tool_use", id="t", name="get_task_ledger", input={})],
                stop_reason="tool_use",
            )

    fake = _Eternal()
    monkeypatch.setattr(llm, "get_client", lambda: fake)
    monkeypatch.setattr(pia, "dispatch_read_tool", lambda *a, **kw: "no tasks")
    payload = pia.respond_individual(
        project_id=9, dataset="d", company_id="c1",
        question="status?", history=[], allow_prd_edit=False,
        single_shot=_single_shot_stub(),
    )
    assert fake.n == 5  # bounded at max_iters
    assert isinstance(payload["answer"], str)


def test_dispatch_scoped_to_this_project(monkeypatch):
    # AC5 — _dispatch forwards THIS ask's project_id/company_id/dataset only.
    seen = {}

    def _spy(name, ti, **kw):
        seen.update(kw)
        return "ok"

    scripted = [
        _Msg([_Block(type="tool_use", id="t", name="get_project_memory", input={})],
             stop_reason="tool_use"),
        _Msg([_Block(type="text", text="done")], stop_reason="end_turn"),
    ]
    monkeypatch.setattr(llm, "get_client", lambda: _FakeClient(scripted))
    monkeypatch.setattr(pia, "dispatch_read_tool", _spy)
    pia.respond_individual(
        project_id=42, dataset="acme", company_id="c-only",
        question="q", history=[], allow_prd_edit=False,
        single_shot=_single_shot_stub(),
    )
    assert seen == {"project_id": 42, "dataset": "acme", "company_id": "c-only"}


def test_individual_loop_raise_degrades_to_single_shot(monkeypatch):
    # AC8 — when run_tool_loop raises, respond_individual returns the caller's
    # single-shot payload rather than propagating (no 500).
    def _boom(**kw):  # noqa: ARG001
        raise RuntimeError("loop exploded")

    monkeypatch.setattr(pia, "run_tool_loop", _boom)
    called = {"n": 0}

    def _ss():
        called["n"] += 1
        return {"answer": "fallback answer", "citations": []}

    payload = pia.respond_individual(
        project_id=9, dataset="d", company_id="c1",
        question="q", history=[], allow_prd_edit=False, single_shot=_ss,
    )
    assert payload == {"answer": "fallback answer", "citations": []}
    assert called["n"] == 1


def test_allow_prd_edit_registers_propose_tool(monkeypatch):
    # AC25 (belt) — the propose tool is present only when allow_prd_edit=True.
    captured = {}

    def _fake_loop(*, tools, **kw):
        captured["names"] = [t["name"] for t in tools]
        return "ok"

    monkeypatch.setattr(pia, "run_tool_loop", _fake_loop)
    monkeypatch.setattr(pia, "log_llm_run", lambda **kw: None)

    pia.respond_individual(
        project_id=9, dataset="d", company_id="c1", question="q", history=[],
        allow_prd_edit=False, single_shot=_single_shot_stub(),
    )
    assert "propose_prd_patch" not in captured["names"]

    pia.respond_individual(
        project_id=9, dataset="d", company_id="c1", question="q", history=[],
        allow_prd_edit=True, single_shot=_single_shot_stub(),
    )
    assert "propose_prd_patch" in captured["names"]


# ── AC4 / AC7 — ask_job_runner branch: non-project unchanged ─────────────────
def _payload(answer):
    return {"answer": answer, "key_points": [], "citations": []}


def test_non_project_ask_uses_single_shot_unchanged(isolated_settings, monkeypatch):
    # AC4 — project_id=None goes through qa_agent.answer; respond_individual is
    # never called; the completed answer is the single-shot one.
    monkeypatch.setattr(ajr.qa_agent, "answer", lambda **kw: _payload("plain"))
    monkeypatch.setattr(ajr, "complete_ask_job", lambda i, p: completed.setdefault(i, p))
    monkeypatch.setattr(ajr, "is_ask_cancelled", lambda i: False)

    def _boom(**kw):  # noqa: ARG001
        raise AssertionError("respond_individual must not run for a non-project ask")

    monkeypatch.setattr(pia, "respond_individual", _boom)
    completed: dict = {}
    asyncio.run(ajr.run_ask_job(ask_id=1, enterprise_id="c1", question="q", dataset="d"))
    assert completed[1]["answer"] == "plain"


def test_project_ask_routes_through_respond_individual(isolated_settings, monkeypatch):
    # AC1/AC7 (integration) — a project ask calls respond_individual with the
    # threaded ids + allow_prd_edit from the flag, and completes its payload.
    seen = {}

    def _spy(**kw):
        seen.update(kw)
        return {"answer": "project answer", "citations": []}

    monkeypatch.setattr(pia, "respond_individual", _spy)
    monkeypatch.setattr(
        "app.project_prd_patch_tool.project_prd_edit_enabled", lambda: True
    )
    monkeypatch.setattr(ajr.qa_agent, "answer",
                        lambda **kw: (_ for _ in ()).throw(AssertionError("no single-shot")))
    monkeypatch.setattr(ajr, "complete_ask_job", lambda i, p: completed.setdefault(i, p))
    monkeypatch.setattr(ajr, "is_ask_cancelled", lambda i: False)
    import app.project_memory as pm
    monkeypatch.setattr(pm, "maybe_promote_turn", lambda *a, **kw: None)

    completed: dict = {}
    asyncio.run(ajr.run_ask_job(
        ask_id=2, enterprise_id="ent-co", question="q", dataset="ds",
        conversation_id=5, project_id=9,
    ))
    assert completed[2]["answer"] == "project answer"
    # company_id threaded is the enterprise_id (== company.company_id) and
    # workspace parity is proven in the propose-tool tests.
    assert seen["project_id"] == 9
    assert seen["company_id"] == "ent-co"
    assert seen["allow_prd_edit"] is True
