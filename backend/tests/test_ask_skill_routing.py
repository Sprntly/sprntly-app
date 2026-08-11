"""Integration tests for the routed branches of POST /v1/ask.

Before this, ask.py's skill branch called `gateway.llm_call` with the wrong
signature and an undefined schema name, so it threw on every request and
silently fell back to the generic corpus answer — the routed path never ran.
These tests pin the wire end to end.

The BUILT-IN skill branch they were originally written against is gone: a chat
turn no longer selects a `SKILL.md` method, so "write user stories for the
checkout flow" is now answered directly and that test was retired with its
subject. What is left is the branch that still exists and still matters — a
question reaching a dedicated PIPELINE — plus the default, which is now the
common case rather than the fallback.

The `fake_llm` fixture patches `app.llm.call_json` and the per-route refs, but
NOT `app.graph.gateway.call_json` (the gateway imported it into its own
namespace). The skill branch flows through the gateway, so these tests patch
that reference directly.
"""
from __future__ import annotations

import time

import app.graph.gateway as gateway_mod


def _seed_corpus(data_dir, dataset, body="some corpus body"):
    ds = data_dir / dataset
    ds.mkdir(exist_ok=True)
    (ds / "a.md").write_text(body)


def _ask_and_wait(client, question, dataset, *, timeout=5.0):
    """POST /v1/ask (fire-and-forget) then poll GET /v1/ask/{id} until terminal,
    returning the status body (same citation-stripped shape the old sync POST
    returned, plus any extra qa_agent fields like `_skill`)."""
    start = client.post("/v1/ask", json={"question": question, "dataset": dataset})
    assert start.status_code == 200, start.text
    ask_id = start.json()["ask_id"]
    deadline = time.monotonic() + timeout
    body = None
    while time.monotonic() < deadline:
        resp = client.get(f"/v1/ask/{ask_id}")
        assert resp.status_code == 200, resp.text
        body = resp.json()
        if body["status"] != "generating":
            return body
        time.sleep(0.02)
    return body


def _patch_gateway_call_json(monkeypatch, payload):
    """Patch the gateway's own `call_json` ref; record each call's kwargs."""
    calls: list[dict] = []

    def _fake(system, user, **kwargs):  # noqa: ARG001
        calls.append({"system": system, "user": user, "kwargs": kwargs})
        return payload

    monkeypatch.setattr(gateway_mod, "call_json", _fake, raising=True)
    return calls


def test_ask_ordinary_question_is_answered_directly(
    tenant_client, isolated_settings, fake_llm, monkeypatch
):
    """A question no pipeline claims: the LLM router (gateway) returns 'none',
    so the answer comes from compose_ask_answer (fake_llm) and carries no
    _skill tag. This is the DEFAULT path now, not a fallback."""
    t = tenant_client.make(slug="acme")
    _seed_corpus(isolated_settings["data_dir"], dataset="acme")

    # Gateway router call → "none" decision.
    gw_calls = _patch_gateway_call_json(
        monkeypatch, {"skill_id": "none", "confidence": 0.0, "reason": "general"}
    )
    fake_llm["payload"] = {
        "answer": "generic answer",
        "key_points": [],
        "citations": [],
        "confidence": 0.5,
        "unanswered": "",
    }

    body = _ask_and_wait(
        t.client, "What happened in our business last week?", "acme"
    )
    assert body["status"] == "ready"
    assert body["answer"] == "generic answer"
    assert "_skill" not in body  # answered directly, not via a skill
    # The one gateway call was the router, not a skill answer — and it carried
    # NO cacheable prefix: the ~9.6k-token built-in menu that used to ride there
    # is gone, and the four pipelines the router still picks between are
    # described in the (tenant-invariant, already cached) system prompt.
    assert len(gw_calls) == 1
    assert gw_calls[0]["kwargs"].get("user_cacheable_prefix") is None
    assert "competitive-intelligence-review" in gw_calls[0]["system"]


def test_ask_company_research_reaches_the_dedicated_pipeline(
    tenant_client, isolated_settings, fake_llm, monkeypatch
):
    """A 'do some deep research on our company' question must reach the
    dedicated staged web-research pipeline, not the generic single-shot skill
    answer — the generic path would answer from a KG that, for a company this
    ask is typically made by, is still empty. Pins the whole wire: the regex
    fast-path in skill_router → qa_agent's dispatch branch → company_research.

    The wire pinned here is the UNPLANNED one. Every unpinned ask is now
    planned first (`ask_planner.plan_for_answer` in the job runner), and on a
    planned turn the plan — not this ladder — names the pipeline; the planned
    company-research dispatch is locked by test_ask_planner.py. This ladder is
    what a planner outage degrades to, so the planner is stubbed to decline
    (None), exactly its contract on any failure. Without that stub the
    router-shaped gateway patch below double-books as the PLANNER's reply,
    which tolerant parsing reads as "generic answer" — and the turn never
    reaches routing at all.
    """
    import app.ask_planner as ap
    import app.company_research as cr

    t = tenant_client.make(slug="acme")
    _seed_corpus(isolated_settings["data_dir"], dataset="acme")
    monkeypatch.setattr(ap, "plan_for_answer", lambda **k: None)

    calls: list[dict] = []

    def fake_answer(**kwargs):
        calls.append(kwargs)
        return {
            "answer": "Researched acme.com and recorded 4 sourced facts.",
            "key_points": [], "citations": [], "confidence": 0.6,
            "unanswered": "", "_skill": "company-research",
            "_skill_action": "Company research · 4 facts",
            "_skill_source": "company-research",
        }

    monkeypatch.setattr(cr, "answer", fake_answer)
    # Any gateway call here would mean routing fell through to the LLM router or
    # the generic skill answer — the regex fast-path must have handled it.
    gw_calls = _patch_gateway_call_json(monkeypatch, {"skill_id": "none"})

    body = _ask_and_wait(
        t.client, "do some deep research on our company and pricing", "acme"
    )
    assert body["status"] == "ready"
    assert body["_skill_source"] == "company-research"
    assert body["answer"].startswith("Researched acme.com")
    assert len(calls) == 1
    assert calls[0]["question"] == "do some deep research on our company and pricing"
    assert gw_calls == []
