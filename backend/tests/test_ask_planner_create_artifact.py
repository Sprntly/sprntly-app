"""Gates for the planner's `create_artifact` action.

The action itself is chosen by a model, so what CAN be pinned in CI is the
deterministic half: that the action is reachable, that its argument survives,
that a missing argument degrades safely, and that no OTHER action can carry a
stray artifact kind. The prompt rules that decide "asked for a document" vs
"asked about one" need a live model and are exercised by the labelled evals,
not here — the same split every other planner action uses.
"""
from __future__ import annotations

from app.ask_planner import (
    ACTION_ANSWER,
    _ACTIONS,
    _TASK_CHARS,
    _attached_document_brief,
    apply_gates,
)


def _gate(payload: dict, question: str = ""):
    return apply_gates(payload, enterprise_id="co-test", connected=[], question=question)


# What ChatScreen.tsx `submitAsk` actually sends: the typed words, then the
# marker `qa_agent._ATTACHED_FILES_MARKER` inlines, then the extracted text.
_WITH_PDF = (
    "generate a report for me\n\n[Attached files]\n--- Sprntly-How-To-Guide.pdf ---\n"
    "## Page 1\nS P R N T L Y How-To Guide\nFrom signals to PRD to prototype to build."
)


def test_the_action_exists():
    """An action the planner cannot name is an action nothing can choose."""
    assert "create_artifact" in _ACTIONS


def test_a_well_formed_create_survives_with_its_kind():
    plan = _gate({
        "action": "create_artifact",
        "task": "Summarize the Q3 reliability work for leadership",
        "artifact_kind": "leadership update",
        "action_confidence": 0.9,
    })
    assert plan.action == "create_artifact"
    assert plan.artifact_kind == "leadership update"
    assert "Q3 reliability" in plan.task


def test_a_create_with_no_task_and_nothing_attached_degrades_to_answer():
    """An action whose ARGUMENT is missing is worse than no action: a document
    generated from an empty brief is a blank page with a title on it.

    Unlike `generate_prd` — which has a chat surface that asks "what should it
    cover?" and waits — there is no such prompt for an arbitrary document kind,
    so `answer` is the recoverable landing. THIS STILL HOLDS when nothing is
    attached (the case below is the one exception): a bare "write me a memo"
    with no subject anywhere really is a blank page.
    """
    plan = _gate({"action": "create_artifact", "artifact_kind": "memo"})
    assert plan.action == ACTION_ANSWER
    assert plan.artifact_kind is None
    # The same, with a question that merely has no attachment in it.
    plan = _gate(
        {"action": "create_artifact", "artifact_kind": "report"},
        question="generate a report for me",
    )
    assert plan.action == ACTION_ANSWER


def test_a_create_with_no_task_but_an_attached_document_builds_from_it():
    """THE STAGING BUG (2026-09-03). "generate a report for me" + a PDF: the
    planner chose create_artifact / "report" — correctly — but left the brief
    empty ("unresolvable without clarification"), and the gate dropped it to
    a generic answer. The user got Q&A over the PDF instead of a report, and
    was never asked anything (there is no clarify action to ask with).

    With a document attached the subject is not missing — it is the document.
    The request is kept, and the brief is the user's words plus the attached
    text, so the generator has real material."""
    plan = _gate(
        {"action": "create_artifact", "artifact_kind": "report", "task": ""},
        question=_WITH_PDF,
    )
    assert plan.action == "create_artifact"
    assert plan.artifact_kind == "report"
    assert "generate a report for me" in plan.task
    assert "Sprntly-How-To-Guide.pdf" in plan.task
    assert "From signals to PRD" in plan.task


def test_the_filenames_only_marker_also_counts_as_an_attachment():
    """The router's view (`_routing_text_with_filenames`) names files rather
    than inlining them; that is still a document to build from."""
    plan = _gate(
        {"action": "create_artifact", "artifact_kind": "report"},
        question="generate a report for me\n\n[Attached document names]\nQ3-review.pdf",
    )
    assert plan.action == "create_artifact"
    assert "Q3-review.pdf" in plan.task


def test_the_attached_document_brief_is_capped_like_every_other_brief():
    """A PDF can be megabytes; the brief gets the same cap a model-written one
    does, keeping its opening (where the subject is) rather than its tail."""
    huge = "generate a report for me\n\n[Attached files]\n--- big.pdf ---\n" + ("lorem " * 5000)
    brief = _attached_document_brief(huge)
    assert len(brief) == _TASK_CHARS
    assert brief.startswith("generate a report for me")


def test_an_attachment_rescues_only_create_artifact():
    """The exception is scoped to the one action a document can BE the subject
    of. A ticket set or a prototype with no brief is still built from nothing,
    attachment or not — those keep degrading exactly as before."""
    for action in ("generate_tickets", "generate_prototype"):
        plan = _gate({"action": action, "task": ""}, question=_WITH_PDF)
        assert plan.action == ACTION_ANSWER, action


def test_a_written_brief_still_wins_over_the_fallback():
    """The fallback is for an EMPTY brief only. When the model wrote one, that
    is the brief — the attachment does not overwrite what it decided."""
    plan = _gate(
        {"action": "create_artifact", "artifact_kind": "report", "task": "Summarize the guide for new PMs"},
        question=_WITH_PDF,
    )
    assert plan.action == "create_artifact"
    assert plan.task == "Summarize the guide for new PMs"


def test_no_question_means_no_fallback_and_no_change_for_existing_callers():
    """`question` is optional so every existing caller and test keeps its
    exact behaviour — the same contract `known_documents` and `templates`
    carry on `apply_gates`."""
    assert _attached_document_brief("") == ""
    assert _attached_document_brief(None) == ""
    assert _attached_document_brief("no marker here at all") == ""


def test_a_create_with_no_kind_still_builds():
    """The mirror case, and it goes the OTHER way on purpose. The user asked
    for a document and the task says what it is about; the generator titles it
    from its own <h1>. Refusing here would answer with prose someone who asked
    for a document."""
    plan = _gate({"action": "create_artifact", "task": "write up the outage"})
    assert plan.action == "create_artifact"
    assert plan.artifact_kind is None


def test_a_kind_on_any_other_action_is_dropped():
    """Arguments belong to their action. A stray kind on an `answer` is noise a
    downstream reader would otherwise try to honour."""
    for action, extra in (
        (ACTION_ANSWER, {}),
        ("generate_prd", {"task": "a PRD about billing"}),
        ("generate_tickets", {"task": "break this down"}),
    ):
        plan = _gate({"action": action, "artifact_kind": "leadership update", **extra})
        assert plan.artifact_kind is None, action


def test_a_create_gathers_nothing():
    """ACTION EXCLUSIVITY, which `apply_gates` already enforces for every
    builder: a plan that BUILDS does not also gather for an answer nobody
    composes. Asserted for this action too, because a create that carried
    sources would make the log claim reads that never happened."""
    plan = _gate({
        "action": "create_artifact",
        "task": "write the launch plan",
        "artifact_kind": "launch plan",
        "sources": ["slack", "jira"],
        "web_search": True,
        "pipeline_id": "call-digest",
    })
    assert plan.action == "create_artifact"
    assert plan.sources == []
    assert plan.web_search is False
    assert plan.pipeline_id is None


def test_an_overlong_kind_is_clamped():
    """`kind` is free text from a model and is stored in a bounded column."""
    plan = _gate({
        "action": "create_artifact", "task": "t", "artifact_kind": "x" * 500,
    })
    assert len(plan.artifact_kind) <= 120
