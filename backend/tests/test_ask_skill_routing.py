"""Integration tests for the skill-routed branch of POST /v1/ask.

Before this, ask.py's skill branch called `gateway.llm_call` with the wrong
signature and an undefined schema name, so it threw on every request and
silently fell back to the generic corpus answer — the skill path never ran.
These tests pin the now-working behaviour: a question that matches a skill is
answered via `gateway.llm_call(skill=...)`, which injects the skill's SKILL.md
method into the call.

The `fake_llm` fixture patches `app.llm.call_json` and the per-route refs, but
NOT `app.graph.gateway.call_json` (the gateway imported it into its own
namespace). The skill branch flows through the gateway, so these tests patch
that reference directly.
"""
from __future__ import annotations

import app.graph.gateway as gateway_mod


def _seed_corpus(data_dir, dataset, body="some corpus body"):
    ds = data_dir / dataset
    ds.mkdir(exist_ok=True)
    (ds / "a.md").write_text(body)


def _patch_gateway_call_json(monkeypatch, payload):
    """Patch the gateway's own `call_json` ref; record each call's kwargs."""
    calls: list[dict] = []

    def _fake(system, user, **kwargs):  # noqa: ARG001
        calls.append({"system": system, "user": user, "kwargs": kwargs})
        return payload

    monkeypatch.setattr(gateway_mod, "call_json", _fake, raising=True)
    return calls


def test_ask_skill_route_executes_via_gateway(
    tenant_client, isolated_settings, fake_llm, monkeypatch
):
    """A 'write user stories …' question routes to the user-stories skill (a
    non-script skill) and is answered through the gateway with the SKILL.md
    method bound — not the generic fallback. (Script skills like prioritize
    take the tool-loop path instead; see test_qa_agent.)"""
    t = tenant_client.make(slug="acme")
    _seed_corpus(isolated_settings["data_dir"], dataset="acme")

    skill_payload = {
        "answer": "## Stories\n\n- As a user…",
        "key_points": ["INVEST"],
        "citations": [],
        "confidence": 0.92,
        "unanswered": "",
    }
    gw_calls = _patch_gateway_call_json(monkeypatch, skill_payload)

    resp = t.client.post(
        "/v1/ask",
        json={
            "question": "Write user stories for the checkout flow",
            "dataset": "acme",
        },
    )

    assert resp.status_code == 200, resp.text
    body = resp.json()
    # Answer came from the skill payload, tagged with the matched skill.
    assert body["answer"].startswith("## Stories")
    assert body["_skill"] == "user-stories"
    # The gateway was used, and the user-stories SKILL.md method was injected
    # into the cacheable prefix.
    assert len(gw_calls) >= 1
    prefix = gw_calls[-1]["kwargs"].get("user_cacheable_prefix") or ""
    assert "## METHOD (skill: user-stories" in prefix


def test_ask_non_skill_question_uses_generic_path(
    tenant_client, isolated_settings, fake_llm, monkeypatch
):
    """A question with no skill match: the LLM router (gateway) returns 'none',
    so the answer comes from the generic compose_ask_answer path (fake_llm) and
    carries no _skill tag."""
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

    resp = t.client.post(
        "/v1/ask",
        json={"question": "What happened in our business last week?", "dataset": "acme"},
    )

    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["answer"] == "generic answer"
    assert "_skill" not in body  # answered directly, not via a skill
    # The one gateway call was the router (skill menu), not a skill answer.
    assert len(gw_calls) == 1
    assert "Available skills:" in (gw_calls[0]["kwargs"].get("user_cacheable_prefix") or "")
