"""Document-intent routing — a wiki question that never names the wiki.

`is_connector_lookup` needs the message to NAME a source. That is how people
talk about Slack and not how they talk about a wiki: "what does our onboarding
spec say" names the document, not Confluence. `document_lookup_candidates` is
the second trigger, and this file pins both halves of it:

  1. The regex half (`skill_router.document_lookup_candidates`) — what a
     document question looks like, and the four families of veto that keep it
     off Sprntly's own surfaces.
  2. The CONNECTION half (`qa_agent.answer`) — candidates are only acted on for
     a company that actually has Confluence connected. That intersection is the
     safety mechanism for a trigger this broad, so it is tested as behaviour,
     not left implicit.

No network/LLM/DB: the lookup entry point and the connection list are patched.
"""
from __future__ import annotations

import app.qa_agent as qa
from app.skill_router import document_lookup_candidates, is_connector_lookup


# ── the regex half ───────────────────────────────────────────────────────────

def test_document_questions_naming_no_source_are_candidates():
    # The shape the named path could never catch: the DOCUMENT is the subject.
    for question in [
        "what does our onboarding spec say?",
        "find the design doc for checkout",
        "what's in the engineering runbook?",
        "do we have any documentation on SSO?",
        "what does the wiki say about deployments?",
        "is there a spec for the new billing flow?",
        "where's the RFC on event streaming",
        "show me the docs about rate limiting",
        "explain what the deployment playbook covers",
        "summarise the incident runbook",
        "which handbook covers the on-call rotation",
        "anything documented about our SSO rollout?",
    ]:
        assert document_lookup_candidates(question) == {"confluence"}, question
        # …and none of them names a source, which is the whole point.
        assert is_connector_lookup(question) is None, question


def test_being_told_about_documents_counts_as_asking(monkeypatch):
    """Not every request for a document is a question.

    Reported 2026-08-03: "brief me on the company documents" carried no
    interrogative and no fetch verb, so it fell to the generic path and was
    answered from KG signals extracted when the pages were still blank
    templates — reporting three now-populated Confluence pages as "Empty".
    """
    for question in [
        "brief me on the company documents",
        "tell me about our specs",
        "give me a rundown of the docs",
        "catch me up on the engineering documentation",
        "overview of our runbooks",
    ]:
        assert document_lookup_candidates(question) == {"confluence"}, question


def test_the_brief_NOUN_still_belongs_to_sprntly():
    """The same word, the other part of speech. "brief me" is a request to be
    told; "the weekly brief" is a surface Sprntly generates."""
    for question in [
        "what's in the weekly brief?",
        "summarise the brief",
        "show me the latest brief document",
    ]:
        assert document_lookup_candidates(question) == set(), question


def test_a_document_noun_alone_is_not_enough():
    # No read verb — a passing mention of a doc is not a request to open one.
    for question in [
        "the spec landed yesterday",
        "our documentation is out of date",
    ]:
        assert document_lookup_candidates(question) == set(), question


def test_sprntly_own_surfaces_keep_their_routing():
    # PRD/brief/insights/report/persona/roadmap/prototype are things Sprntly
    # GENERATES. Answering these from a customer wiki would break the core loop.
    for question in [
        "summarise the PRD for the billing project",
        "what's in the weekly brief?",
        "show me top insights",
        "what does the competitive report say?",
        "what's on our roadmap doc?",
        "which persona doc did we agree on",
        "find the user stories doc",
    ]:
        assert document_lookup_candidates(question) == set(), question


def test_creation_is_not_a_lookup():
    for question in [
        "write a spec for onboarding",
        "draft a design doc for the new API",
        "create documentation for the API",
        "generate a runbook for failover",
    ]:
        assert document_lookup_candidates(question) == set(), question


def test_a_creation_verb_inside_the_subject_does_not_veto():
    # Position decides, not presence — "build" here is part of what is being
    # searched FOR, not a command to make something. The PM-noun-anchored
    # _vetoed_as_creation gets this wrong, which is why this path has its own.
    assert document_lookup_candidates(
        "get me the spec for the checkout build"
    ) == {"confluence"}


def test_a_document_in_the_conversation_is_not_a_wiki_read():
    # Pasted/uploaded/on-screen content — the subject is already in the thread.
    for question in [
        "summarize this document",
        "what does the attached spec say",
        "rewrite this one-pager",
        "explain that design doc",
    ]:
        assert document_lookup_candidates(question) == set(), question


def test_a_tool_named_as_a_subject_is_not_a_lookup():
    # Same two vetoes the named path runs: comparison framing and possessed
    # artifacts are product questions, and the skill router answers them.
    for question in [
        "should we prioritise the confluence integration or the notion one?",
        "confluence vs notion for our docs",
        "what are the alternatives to our documentation tool",
    ]:
        assert document_lookup_candidates(question) == set(), question


def test_a_named_source_is_left_to_the_named_path():
    # is_connector_lookup runs first and reaches sources this trigger does not,
    # so a message that names one must not be claimed here.
    assert is_connector_lookup("check confluence for the onboarding spec") == {
        "confluence"
    }
    assert document_lookup_candidates("check confluence for the onboarding spec") == set()
    assert document_lookup_candidates("what's in google drive about the launch doc") == set()


def test_ordinary_product_questions_are_untouched():
    for question in [
        "what are customers complaining about?",
        "what's our churn rate?",
        "how do we improve activation?",
        "get me the open tickets",
        "prioritize these features",
        "analyze my data",
    ]:
        assert document_lookup_candidates(question) == set(), question


# ── the connection half, through qa_agent.answer ─────────────────────────────

_DOC_Q = "what does our onboarding spec say?"


def _patch_lookup(monkeypatch) -> dict:
    """Capture what registry.answer_for_hints was called with, if at all."""
    from app.connector_lookup import registry

    seen: dict = {}

    def fake_answer_for_hints(**kwargs):
        seen.update(kwargs)
        return {"answer": "wiki answer", "skill_action": "Connector lookup"}

    monkeypatch.setattr(registry, "answer_for_hints", fake_answer_for_hints)
    return seen


def _patch_connected(monkeypatch, providers: list[str]) -> None:
    from app.connector_lookup import registry

    monkeypatch.setattr(registry, "connected_providers", lambda _eid: providers)


class _ReachedGenericRouter(Exception):
    """Raised by the patched `route` so a fall-through is asserted directly
    rather than inferred from whatever the generic path would have answered."""


def _stop_at_generic_router(monkeypatch) -> dict:
    seen: dict = {}

    def fake_route(question, **kwargs):
        seen["question"] = question
        raise _ReachedGenericRouter

    monkeypatch.setattr(qa, "route", fake_route)
    return seen


def test_connected_confluence_reaches_the_live_read(monkeypatch):
    seen = _patch_lookup(monkeypatch)
    _patch_connected(monkeypatch, ["confluence", "slack"])

    out = qa.answer(enterprise_id="ent", question=_DOC_Q, dataset="acme")

    assert out["answer"] == "wiki answer"
    # Only Confluence, even though Slack is connected too — a document question
    # is not a reason to open a chat tool.
    assert seen["hints"] == {"confluence"}
    assert seen["question"] == _DOC_Q
    assert seen["enterprise_id"] == "ent"


def test_without_confluence_the_question_routes_normally(monkeypatch):
    """The safety mechanism for a trigger this broad. A company with no wiki
    connected must be completely unaffected — no live read, and no "Confluence
    isn't connected" dead-end either, just the routing it had before."""
    seen = _patch_lookup(monkeypatch)
    _patch_connected(monkeypatch, ["slack", "hubspot"])
    routed = _stop_at_generic_router(monkeypatch)

    try:
        qa.answer(enterprise_id="ent", question=_DOC_Q, dataset="acme")
    except _ReachedGenericRouter:
        pass

    assert seen == {}, "no live read for a company without Confluence"
    assert routed["question"] == _DOC_Q, "fell through to the generic router"


def _did_lookup_fire(monkeypatch, **answer_kwargs) -> bool:
    """Run qa.answer far enough to pass the interception block and report whether
    the document trigger claimed the question.

    Everything downstream is swallowed on purpose. An explicit skill runs the
    real answer path, which reaches the LLM and the DB — neither is available
    here, and neither is what this asserts. The claim is only ever "the
    interception above did not fire", and `seen` records that exactly.
    """
    seen = _patch_lookup(monkeypatch)
    _patch_connected(monkeypatch, ["confluence"])
    try:
        qa.answer(enterprise_id="ent", dataset="acme", **answer_kwargs)
    except Exception:  # noqa: BLE001 — see docstring
        pass
    return bool(seen)


def test_the_document_path_asks_for_the_knowledge_graph_too(monkeypatch):
    """The other half of the reported failure. The question named neither the
    wiki nor the graph, so the answer must have access to both — reading only
    one is what produced "connect your onboarding spec" while it was connected."""
    seen = _patch_lookup(monkeypatch)
    _patch_connected(monkeypatch, ["confluence"])

    qa.answer(enterprise_id="ent", question=_DOC_Q, dataset="acme")

    assert seen["include_knowledge_graph"] is True


def test_a_named_source_also_gets_the_knowledge_graph(monkeypatch):
    """Naming a source says which tool to OPEN, not that the answer should be
    thinner. A KG-only answer is how "brief me on the company documents"
    reported three populated Confluence pages as empty — it read the sync
    snapshot taken while they were still blank templates and never opened them.
    Both readers on every lookup; the prompt makes the model attribute each."""
    seen = _patch_lookup(monkeypatch)
    _patch_connected(monkeypatch, ["confluence", "slack"])

    qa.answer(
        enterprise_id="ent", dataset="acme",
        question="check confluence for the onboarding spec",
    )

    assert seen["hints"] == {"confluence"}
    assert seen["include_knowledge_graph"] is True


def test_a_pinned_skill_is_never_hijacked(monkeypatch):
    # No need to make the pin RUNNABLE (_invocable): the interception is gated on
    # `not pinned_skill` and sits above that check, so the presence of the pin is
    # the whole test.
    assert not _did_lookup_fire(
        monkeypatch, question=_DOC_Q, pinned_skill="prd-author"
    ), "an explicitly pinned skill outranks the document trigger"


def test_a_slash_command_is_never_hijacked(monkeypatch):
    assert not _did_lookup_fire(
        monkeypatch, question="/prd-author a spec for the onboarding flow"
    ), "an explicit slash command outranks the document trigger"
