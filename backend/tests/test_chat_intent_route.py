"""POST /v1/chat/intent — the action-envelope route.

Contract under test (the resolver itself is covered in
test_chat_intent_evals.py; here it is mocked at the module seam
app.routes.chat.resolve_chat_intent):

  - the envelope passes through, with the resolved target prd_id/prd_title
    echoed on it
  - an explicit tab prd_id is ownership-gated: a foreign company's PRD → 404
  - with no tab prd_id, the conversation's bound PRD is resolved as the
    target and fed to the resolver
  - conversation history is loaded server-side (ownership-scoped) and handed
    to the resolver
  - a foreign conversation_id yields no history and no bound target
  - validation: empty message → 422, no resolver call
  - unauthenticated → 401
"""
from __future__ import annotations

import app.routes.chat as chat_route
from app.db.client import require_client


def _seed_prd(db_mod, dataset="acme", title="Dark mode"):
    brief_id = db_mod.save_brief(
        dataset=dataset, week_label="Week of stub",
        payload={"summary_headline": "s", "insights": [{"title": "I0"}],
                 "_schema_version": 1},
        schema_version=1,
    )
    prd_id = db_mod.start_prd(
        brief_id=brief_id, insight_index=0, title=title,
        template_version=1, variant="v3", source="chat", theme_id="chat:seed",
    )
    db_mod.complete_prd(prd_id, title=title, md="<html><body>Doc</body></html>")
    return prd_id


def _seed_conversation(company_id, user_id, prd_id=None, turns=()):
    row = {
        "company_id": company_id,
        "user_id": user_id,
        "title": "chat",
        "query": "chat",
        "agent_type": "ask",
    }
    if prd_id is not None:
        row["prd_id"] = prd_id
    conv = require_client().table("conversations").insert(row).execute().data[0]
    for role, content in turns:
        require_client().table("conversation_turns").insert(
            {"conversation_id": conv["id"], "role": role, "content": content}
        ).execute()
    return conv["id"]


def _capture_resolver(monkeypatch, envelope=None):
    seen: dict = {}

    def _resolve(enterprise_id, message, history=None, *, prd_id=None,
                 prd_title=None, has_attachments=False):
        seen.update(
            enterprise_id=enterprise_id, message=message, history=history,
            prd_id=prd_id, prd_title=prd_title, has_attachments=has_attachments,
        )
        return dict(envelope or {
            "intent": "answer", "confidence": 0.9, "task": None,
            "instruction": None, "reason": "plain question", "source": "llm",
        })

    monkeypatch.setattr(chat_route, "resolve_chat_intent", _resolve)
    return seen


def test_envelope_passthrough_with_tab_prd(tenant_client, isolated_settings, monkeypatch):
    t = tenant_client.make(slug="acme")
    prd_id = _seed_prd(isolated_settings["db"])
    seen = _capture_resolver(monkeypatch, {
        "intent": "edit_prd", "confidence": 0.92, "task": None,
        "instruction": "Shorten every section", "reason": "edit on open PRD",
        "source": "llm",
    })

    resp = t.client.post(
        "/v1/chat/intent",
        json={"message": "make it shorter", "prd_id": prd_id},
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["intent"] == "edit_prd"
    assert body["instruction"] == "Shorten every section"
    assert body["prd_id"] == prd_id
    assert body["prd_title"] == "Dark mode"

    # The resolver was grounded on the same target the envelope echoes.
    assert seen["enterprise_id"] == t.company_id
    assert seen["prd_id"] == prd_id
    assert seen["prd_title"] == "Dark mode"
    assert seen["message"] == "make it shorter"


def test_foreign_prd_is_404(tenant_client, isolated_settings, monkeypatch):
    t = tenant_client.make(slug="acme")
    other = tenant_client.make(slug="rival")
    foreign_prd = _seed_prd(isolated_settings["db"], dataset="rival")
    seen = _capture_resolver(monkeypatch)

    resp = t.client.post(
        "/v1/chat/intent",
        json={"message": "make it shorter", "prd_id": foreign_prd},
    )
    assert resp.status_code == 404
    assert not seen  # no resolver call, no LLM spend
    # Sanity: the owner CAN use it.
    ok = other.client.post(
        "/v1/chat/intent",
        json={"message": "make it shorter", "prd_id": foreign_prd},
    )
    assert ok.status_code == 200


def test_conversation_bound_prd_is_resolved_as_target(
    tenant_client, isolated_settings, monkeypatch
):
    t = tenant_client.make(slug="acme")
    prd_id = _seed_prd(isolated_settings["db"])
    conv_id = _seed_conversation(
        t.company_id, t.user_id, prd_id=prd_id,
        turns=[("user", "generate a prd for dark mode"),
               ("assistant", "Here is the PRD.")],
    )
    seen = _capture_resolver(monkeypatch)

    resp = t.client.post(
        "/v1/chat/intent",
        json={"message": "make it shorter", "conversation_id": conv_id},
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["prd_id"] == prd_id
    assert seen["prd_id"] == prd_id
    # History reached the resolver, oldest first.
    roles = [h["role"] for h in seen["history"]]
    assert roles == ["user", "assistant"]
    assert "dark mode" in seen["history"][0]["content"]


def test_foreign_conversation_yields_no_history_or_target(
    tenant_client, isolated_settings, monkeypatch
):
    t = tenant_client.make(slug="acme")
    other = tenant_client.make(slug="rival")
    foreign_prd = _seed_prd(isolated_settings["db"], dataset="rival")
    foreign_conv = _seed_conversation(
        other.company_id, other.user_id, prd_id=foreign_prd,
        turns=[("user", "secret rival plans")],
    )
    seen = _capture_resolver(monkeypatch)

    resp = t.client.post(
        "/v1/chat/intent",
        json={"message": "draft it up", "conversation_id": foreign_conv},
    )
    assert resp.status_code == 200
    assert resp.json()["prd_id"] is None
    assert seen["prd_id"] is None
    assert seen["history"] == []


def test_empty_message_is_422_without_resolver_call(
    tenant_client, isolated_settings, monkeypatch
):
    t = tenant_client.make(slug="acme")
    seen = _capture_resolver(monkeypatch)
    resp = t.client.post("/v1/chat/intent", json={"message": ""})
    assert resp.status_code == 422
    assert not seen


def test_unauthenticated_is_401(unauth_client, monkeypatch):
    seen = _capture_resolver(monkeypatch)
    resp = unauth_client.post("/v1/chat/intent", json={"message": "hello"})
    assert resp.status_code == 401
    assert not seen
