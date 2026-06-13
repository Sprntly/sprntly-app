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
    """A 'prioritize …' question routes to the prioritize skill and is answered
    through the gateway with the skill bound — not the generic fallback."""
    t = tenant_client.make(slug="acme")
    _seed_corpus(isolated_settings["data_dir"], dataset="acme")

    skill_payload = {
        "answer": "## Ranked\n\n1. SSO 2. export 3. dark mode",
        "key_points": ["RICE applied"],
        "citations": [],
        "confidence": 0.92,
        "unanswered": "",
    }
    gw_calls = _patch_gateway_call_json(monkeypatch, skill_payload)

    resp = t.client.post(
        "/v1/ask",
        json={
            "question": "Prioritize these features: SSO, dark mode, export",
            "dataset": "acme",
        },
    )

    assert resp.status_code == 200, resp.text
    body = resp.json()
    # Answer came from the skill payload, tagged with the matched skill.
    assert body["answer"].startswith("## Ranked")
    assert body["_skill"] == "prioritize"
    # The gateway was used exactly once, and the prioritize SKILL.md method was
    # injected into the cacheable prefix.
    assert len(gw_calls) == 1
    prefix = gw_calls[0]["kwargs"].get("user_cacheable_prefix") or ""
    assert "## METHOD (skill: prioritize" in prefix


def test_ask_non_skill_question_uses_generic_path(
    tenant_client, isolated_settings, fake_llm, monkeypatch
):
    """A question with no skill match never touches the gateway skill branch —
    it answers via the generic compose_ask_answer path (fake_llm)."""
    t = tenant_client.make(slug="acme")
    _seed_corpus(isolated_settings["data_dir"], dataset="acme")

    gw_calls = _patch_gateway_call_json(monkeypatch, {"answer": "should-not-run"})
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
    assert resp.json()["answer"] == "generic answer"
    assert gw_calls == []  # skill branch not taken
