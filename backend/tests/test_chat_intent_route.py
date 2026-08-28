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


def _seed_prd(db_mod, dataset="acme", title="Dark mode", theme_id="chat:seed"):
    brief_id = db_mod.save_brief(
        dataset=dataset, week_label="Week of stub",
        payload={"summary_headline": "s", "insights": [{"title": "I0"}],
                 "_schema_version": 1},
        schema_version=1,
    )
    # A DISTINCT theme_id per seeded PRD: the artifact listing collapses one
    # regeneration family to its newest row, and a shared theme would make two
    # separately-seeded documents look like two generations of one.
    prd_id = db_mod.start_prd(
        brief_id=brief_id, insight_index=0, title=title,
        template_version=1, variant="v3", source="chat", theme_id=theme_id,
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
                 prd_title=None, has_attachments=False, open_artifact=None,
                 thread_artifact=None):
        seen.update(
            enterprise_id=enterprise_id, message=message, history=history,
            prd_id=prd_id, prd_title=prd_title, has_attachments=has_attachments,
            open_artifact=open_artifact,
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


def _open_envelope(monkeypatch, query, artifact_type="prd"):
    """Force an open_artifact verdict so the route's LOOKUP is what's tested."""
    return _capture_resolver(monkeypatch, {
        "intent": "open_artifact", "confidence": 0.95, "task": None,
        "instruction": None, "artifact_type": artifact_type,
        "artifact_query": query, "reason": "open request", "source": "llm",
    })


def test_open_artifact_resolves_a_single_match(
    tenant_client, isolated_settings, monkeypatch
):
    t = tenant_client.make(slug="acme")
    db = isolated_settings["db"]
    prd_id = _seed_prd(db, title="Compliance Reporting Automation", theme_id="chat:a")
    _seed_prd(db, title="Dark mode", theme_id="chat:b")
    _open_envelope(monkeypatch, "compliance reporting")

    body = t.client.post(
        "/v1/chat/intent", json={"message": "open the PRD for compliance reporting"},
    ).json()
    assert body["intent"] == "open_artifact"
    assert body["open"]["status"] == "resolved"
    assert body["open"]["artifact"]["prd_id"] == prd_id
    assert body["open"]["artifact"]["title"] == "Compliance Reporting Automation"


def test_open_artifact_reports_every_tied_candidate(
    tenant_client, isolated_settings, monkeypatch
):
    """The live baseline: two PRDs match and the assistant asks which. The
    candidates must arrive with their ids, or the chips it offers are inert."""
    t = tenant_client.make(slug="acme")
    db = isolated_settings["db"]
    a = _seed_prd(db, title="Compliance Reporting", theme_id="chat:a")
    b = _seed_prd(db, title="Compliance Reporting", theme_id="chat:b")
    _open_envelope(monkeypatch, "compliance reporting")

    body = t.client.post(
        "/v1/chat/intent", json={"message": "open the PRD for compliance reporting"},
    ).json()
    assert body["open"]["status"] == "ambiguous"
    assert body["open"]["artifact"] is None
    assert {c["prd_id"] for c in body["open"]["candidates"]} == {a, b}


def test_open_artifact_with_no_match_opens_nothing(
    tenant_client, isolated_settings, monkeypatch
):
    t = tenant_client.make(slug="acme")
    _seed_prd(isolated_settings["db"], title="Dark mode")
    _open_envelope(monkeypatch, "compliance reporting")

    body = t.client.post(
        "/v1/chat/intent", json={"message": "open the PRD for compliance reporting"},
    ).json()
    assert body["open"]["status"] == "not_found"
    assert body["open"]["artifact"] is None
    assert body["open"]["candidates"] == []
    # And it stays an OPEN request — the route never rewrites a miss into a
    # generation.
    assert body["intent"] == "open_artifact"


def test_open_artifact_never_reaches_another_tenants_documents(
    tenant_client, isolated_settings, monkeypatch
):
    t = tenant_client.make(slug="acme")
    tenant_client.make(slug="rival")
    _seed_prd(isolated_settings["db"], dataset="rival",
              title="Compliance Reporting", theme_id="chat:rival")
    _open_envelope(monkeypatch, "compliance reporting")

    body = t.client.post(
        "/v1/chat/intent", json={"message": "open the PRD for compliance reporting"},
    ).json()
    assert body["open"]["status"] == "not_found"


def test_open_artifact_survives_a_restart_invalidated_newest_generation(
    tenant_client, isolated_settings, monkeypatch
):
    """End-to-end shape of the restart case: the family's newest row is dead,
    the one behind it is fine, and the open must land on the live one rather
    than report `not_found` about a document the Artifacts tab is showing."""
    t = tenant_client.make(slug="acme")
    db = isolated_settings["db"]
    brief_id = db.save_brief(
        dataset="acme", week_label="Week of stub",
        payload={"summary_headline": "s", "insights": [{"title": "I0"}],
                 "_schema_version": 1},
        schema_version=1,
    )

    def _prd(title, status):
        pid = db.start_prd(
            brief_id=brief_id, insight_index=0, title=title,
            template_version=1, variant="v3", source="brief",
        )
        if status == "ready":
            db.complete_prd(pid, title=title, md="<html><body>Doc</body></html>")
        else:
            require_client().table("prds").update({"status": status}).eq(
                "id", pid
            ).execute()
        return pid

    ready = _prd("Compliance Reporting", "ready")
    _prd("Compliance Reporting", "invalidated")  # newer, killed by a restart
    _open_envelope(monkeypatch, "compliance reporting")

    body = t.client.post(
        "/v1/chat/intent", json={"message": "open the PRD for compliance reporting"},
    ).json()
    assert body["open"]["status"] == "resolved"
    assert body["open"]["artifact"]["prd_id"] == ready


def test_open_artifact_reports_an_unopenable_kind_instead_of_substituting(
    tenant_client, isolated_settings, monkeypatch
):
    """A dark mode PRD exists; the user asked for the dark mode PROTOTYPE.
    Handing back the PRD would be the wrong document with no sign of a swap."""
    t = tenant_client.make(slug="acme")
    _seed_prd(isolated_settings["db"], title="Dark Mode")
    _open_envelope(monkeypatch, "dark mode", artifact_type="prototype")

    body = t.client.post(
        "/v1/chat/intent", json={"message": "open the dark mode prototype"},
    ).json()
    assert body["open"]["status"] == "unsupported_type"
    assert body["open"]["artifact_type"] == "prototype"
    assert body["open"]["artifact"] is None


def test_open_artifact_marks_a_chat_prd_as_not_brief_anchored(
    tenant_client, isolated_settings, monkeypatch
):
    """A chat PRD's insight_index 0 is a sentinel; the client must be told so it
    doesn't point the panel's Evidence tab at the brief's first finding."""
    t = tenant_client.make(slug="acme")
    _seed_prd(isolated_settings["db"], title="Compliance Reporting",
              theme_id="chat:seed")
    _open_envelope(monkeypatch, "compliance reporting")

    body = t.client.post(
        "/v1/chat/intent", json={"message": "open the PRD for compliance reporting"},
    ).json()
    assert body["open"]["status"] == "resolved"
    assert body["open"]["artifact"]["brief_anchored"] is False


def test_a_generate_envelope_carries_no_lookup(
    tenant_client, isolated_settings, monkeypatch
):
    """"write a PRD for X" must not acquire an open payload — the two verbs stay
    on opposite sides of the envelope."""
    t = tenant_client.make(slug="acme")
    _seed_prd(isolated_settings["db"], title="Compliance Reporting")
    _capture_resolver(monkeypatch, {
        "intent": "generate_prd", "confidence": 0.95, "task": "compliance reporting",
        "instruction": None, "artifact_type": None, "artifact_query": None,
        "reason": "authoring verb", "source": "llm",
    })

    body = t.client.post(
        "/v1/chat/intent", json={"message": "write a PRD for compliance reporting"},
    ).json()
    assert body["intent"] == "generate_prd"
    assert "open" not in body


def test_unauthenticated_is_401(unauth_client, monkeypatch):
    seen = _capture_resolver(monkeypatch)
    resp = unauth_client.post("/v1/chat/intent", json={"message": "hello"})
    assert resp.status_code == 401
    assert not seen
