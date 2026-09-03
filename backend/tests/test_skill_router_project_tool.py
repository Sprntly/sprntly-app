"""Unit tests for `skill_router.is_project_tool_request` — the sixth
ladder branch's intent gate. No `test_skill_router.py` exists
in this repo, so this is a new, self-contained collected test home for the
new gate function (sibling of `is_jira_lookup`/`is_connector_lookup`/
`document_lookup_candidates`, none of which have their own dedicated file
either — each is tested inline in the suites that exercise the interceptor
built on it; this file is the equivalent home for the new one).
"""
from __future__ import annotations

from app.skill_router import (
    is_project_completion_request,
    is_project_content_request,
    is_project_tool_request,
)


def test_delegate_phrasings_match():
    for q in (
        "please delegate this to Fortune",
        "assign the export review to Ada",
        "hand this off to Femi",
        "have Fortune handle the deploy",
        "send this to Ada",
        "can you ask Femi to take this",
        "give this to Fortune",
    ):
        assert is_project_tool_request(q) is True, q


def test_execute_phrasings_match():
    for q in (
        "draft a PRD for the onboarding flow",
        "can you write up a PRD for the pricing page",
        "create a PRD for this",
        "execute this task",
        "execute the task",
    ):
        assert is_project_tool_request(q) is True, q


def test_mention_without_action_is_vetoed():
    for q in (
        "what did Ada say about the PRD?",
        "summarize the PRD",
        "what's blocking the launch?",
        "who is on this project?",
        "catch me up on this project",
        "what tasks are open?",
        "how many PRDs do we have?",
        "what's the status of the onboarding task?",
        "tell me about the export review",
        "read the PRD",
    ):
        assert is_project_tool_request(q) is False, q


def test_empty_and_none_question_decline():
    assert is_project_tool_request("") is False
    assert is_project_tool_request(None) is False


# ── Imperative "tell/get/have <member> to <verb>" (delegation-confabulation
# fix, part 1) ───────────────────────────────────────────────────────────


def test_tell_get_have_imperative_delegation_phrasings_match():
    """The gate gap this fix closes: "tell David to review the prd" fell
    through to the tool-less composer (no `delegate_task`), which is how the
    confabulated "I've asked David…" handoff got composed with no
    `project_delegations` row ever written. "ask X to Y" already matched —
    "tell/get X to Y" and "have X <verb>" (bare infinitive) did not."""
    for q in (
        "tell David to review the prd",
        "tell David to look at which requirements are important",
        "get David to look at the export review",
        "get Ada to take a pass on the pricing page",
        "have David draft the export section",
    ):
        assert is_project_tool_request(q) is True, q


def test_tell_get_have_do_not_match_pronoun_objects():
    """Same negative-lookahead guard the "ask" branch already carries: "tell
    me/us/him/her/them ..." must NEVER become a delegation — it is the
    speaker asking the AGENT (or asking about someone else), not handing a
    task to a named teammate."""
    for q in (
        "tell me the tasks",
        "tell me about the prd",
        "tell us what's open",
        "tell him about the export review",
        "tell them the status",
        "get me the report",
        "get us up to speed",
    ):
        assert is_project_tool_request(q) is False, q


def test_tell_does_not_overbroaden_plain_reads():
    """"show me tasks assigned to David" names David but is a plain read
    ("show me"), not an imperative hand-off — must not become a
    delegation. ("tasks assigned to David" also must not trip the
    assign-shaped branch: "assigned" is past tense, not the imperative verb
    "assign".)"""
    for q in (
        "show me tasks assigned to David",
        "who is assigned to the export review",
        "what has David been assigned",
    ):
        assert is_project_tool_request(q) is False, q


def test_history_param_accepted_but_not_required():
    # Signature parity with `is_jira_lookup`/`is_connector_lookup` — accepts
    # `history` without requiring it; a positional/keyword-free call and a
    # history-carrying call both work identically for v1 (see the function's
    # own docstring on why continuation judgment is out of scope for v1).
    assert is_project_tool_request("please delegate this to Fortune", None) is True
    assert is_project_tool_request(
        "please delegate this to Fortune",
        history=[{"role": "user", "content": "earlier turn"}],
    ) is True


def test_project_content_read_phrasings_match():
    for q in (
        "summarize the PRD",
        "who's on this project",
        "what artifacts do we have",
        "what's the status of the tasks",
        "read me the report",
        "tell me about the project memory",
        "who's working on this",
    ):
        assert is_project_content_request(q) is True, q


def test_project_content_gate_vetoes_non_project():
    for q in (
        "what's the capital of France",
        "how do I center a div",
        "hey there",
        "thanks!",
    ):
        assert is_project_content_request(q) is False, q


def test_project_content_gate_empty_and_none_decline():
    assert is_project_content_request("") is False
    assert is_project_content_request(None) is False


def test_content_gate_matches_owe_phrasings():
    for q in (
        "who owes what",
        "who owes me anything",
        "what's outstanding on this project",
        "who's still outstanding on this",
    ):
        assert is_project_content_request(q) is True, q


def test_content_gate_still_rejects_non_project_interrogative():
    for q in (
        "what's the capital of France",
        "how do I center a div",
    ):
        assert is_project_content_request(q) is False, q


def test_content_gate_matches_open_and_context_phrasings():
    # "open"/"give me" are read-shaped, and the project's own generated
    # artifacts (PRD, report) plus "context" are project-content nouns —
    # this is the project's OWN linked content, not a company-wide search.
    for q in (
        "open the PRD",
        "open PRD",
        "give me the context",
        "give me context on this project",
        "open the report",
    ):
        assert is_project_content_request(q) is True, q


def test_content_gate_open_and_context_do_not_overbroaden():
    # Guard against the noun/verb additions pulling plain out-of-domain
    # chatter into the project-tool loop.
    for q in (
        "what's the weather",
        "who won the game",
        "open the door",
        "give me a break",
    ):
        assert is_project_content_request(q) is False, q


def test_is_project_tool_request_unchanged():
    # Regression guard: the parallel positive gate must not perturb the
    # existing delegate/execute gate's match/veto outcomes.
    for q in (
        "please delegate this to Fortune",
        "assign the export review to Ada",
        "hand this off to Femi",
        "have Fortune handle the deploy",
        "send this to Ada",
        "can you ask Femi to take this",
        "give this to Fortune",
        "draft a PRD for the onboarding flow",
        "can you write up a PRD for the pricing page",
        "create a PRD for this",
        "execute this task",
        "execute the task",
    ):
        assert is_project_tool_request(q) is True, q
    for q in (
        "what did Ada say about the PRD?",
        "summarize the PRD",
        "what's blocking the launch?",
        "who is on this project?",
        "catch me up on this project",
        "what tasks are open?",
        "how many PRDs do we have?",
        "what's the status of the onboarding task?",
        "tell me about the export review",
        "read the PRD",
    ):
        assert is_project_tool_request(q) is False, q
    assert is_project_tool_request("") is False
    assert is_project_tool_request(None) is False


# ── is_project_completion_request (re-armed for the `complete_task` re-wire) ──


def test_completion_claims_match():
    """First-person completion CLAIMS — the assignee (or the ledger tick,
    which submits the same shape) reporting THEIR OWN task done — admit."""
    for q in (
        "I've finished the export review",
        "I have finished the pricing one-pager",
        "finished that",
        "done with the onboarding checklist",
        "wrapped up the deck",
        "I'm done with the review",
        "it's ready",
        "sent it over",
        "all set",
        "the deck is done",
    ):
        assert is_project_completion_request(q) is True, q


def test_completion_questions_and_reads_decline():
    """Interrogative / status-QUESTION phrasings (wh-led, "status of …") are
    vetoed — they are not first-person completion claims and must not route to
    `complete_task`."""
    for q in (
        "what's the status of the review?",
        "who is working on the deck?",
        "how is the export review going?",
        "when will the deck be done?",
        "summarize the PRD",
        "what tasks are open?",
    ):
        assert is_project_completion_request(q) is False, q
    assert is_project_completion_request("") is False
    assert is_project_completion_request(None) is False


def test_completion_signature_accepts_history():
    """Signature parity with the sibling gates: `history` is accepted (not
    consulted in v1) so the admission ladder can call it uniformly."""
    assert is_project_completion_request("finished that", [{"role": "user", "content": "hi"}]) is True
