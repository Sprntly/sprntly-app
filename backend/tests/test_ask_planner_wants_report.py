"""Report or answer — the planner's own verdict, not a regex over the words.

Reported 2026-09-03, three times in one day, each a different phrasing of the
same complaint: a QUESTION about customers came back as a multi-minute document
in a panel nobody opened. "look at all customer conversation in the last month
and show me what product features users are asking for" was the last of them.

The verdict lived in `call_digest.is_voc_query`, which reads the question's
surface words. That shape can only ever be widened one phrasing at a time — a
summary ask and a table ask were both added that same day — and until each is
found, the DEFAULT is the document. So the owner's rule: a report is written
when the user asks for one, in so many words ("report", "document",
"write-up", "one-pager", "deck", "digest", or the document's own name — "voice
of customer"). Everything else answers in the chat.

`wants_report` is where that rule lives now. What is tested here is the GATE
around it — that it can only ever describe a pipeline that writes documents,
and that it survives the plan intact. The prompt half (which questions the
model should call true) is prompt text; the rules it must state are pinned at
the bottom.

No network, no LLM: `apply_gates` is a pure function over one payload.
"""
from __future__ import annotations

from app.ask_planner import apply_gates

CO = "co-test"


def _gate(**payload):
    base = {
        "action": "answer",
        "pipeline_id": "voice-of-customer-report",
        "confidence": 0.9,
        "wants_report": True,
    }
    base.update(payload)
    return apply_gates(base, enterprise_id=CO, connected=[])


def test_a_document_ask_survives_the_gate():
    plan = _gate()
    assert plan.pipeline_id == "voice-of-customer-report"
    assert plan.wants_report is True


def test_a_question_is_not_a_document():
    """The default and the common case. The pipeline still runs — it is what
    reads the calls — and the answer lands in the thread."""
    plan = _gate(wants_report=False)
    assert plan.pipeline_id == "voice-of-customer-report"
    assert plan.wants_report is False


def test_a_missing_field_reads_as_a_question():
    """FAILS TOWARDS ANSWERING. A payload that predates the field, or one the
    model returned without it, must not open a panel: an answer in the thread
    costs one more message, a document nobody asked for costs minutes."""
    plan = apply_gates(
        {"action": "answer", "pipeline_id": "call-digest", "confidence": 0.9},
        enterprise_id=CO, connected=[],
    )
    assert plan.wants_report is False


def test_only_a_pipeline_that_writes_documents_can_want_one():
    """The same belongs-to-its-argument clamp every other field carries. A
    lookup writes nothing, so a `wants_report` riding along on one describes a
    document that was never going to exist."""
    for pipeline in ("tracker-lookup", "call-listing", "data-analysis",
                     "single-call-read", "ticket-update"):
        plan = _gate(pipeline_id=pipeline)
        assert plan.pipeline_id == pipeline, pipeline
        assert plan.wants_report is False, pipeline


def test_no_pipeline_means_no_document():
    plan = _gate(pipeline_id="none", confidence=0.0)
    assert plan.pipeline_id is None
    assert plan.wants_report is False


def test_a_pipeline_under_the_confidence_bar_takes_the_flag_with_it():
    """`_gate_pipeline` drops a low-confidence pick to None, and a document
    verdict about a pipeline that will not run is not a verdict at all."""
    plan = _gate(confidence=0.2)
    assert plan.pipeline_id is None
    assert plan.wants_report is False


def test_a_build_action_carries_no_report_verdict():
    """Action exclusivity already clears the pipeline; the flag goes with it.
    A `generate_prd` plan that also claimed a report would make the log say a
    document was written beside the PRD."""
    plan = _gate(action="generate_prd", task="Checkout v2")
    assert plan.action == "generate_prd"
    assert plan.pipeline_id is None
    assert plan.wants_report is False


def test_every_report_pipeline_can_want_a_document():
    """Not just the voice-of-customer pair. The rule is one rule, and a
    pipeline missing from it would keep the old default — a document for every
    question that reached it."""
    from app.qa_agent import _REPORT_PIPELINE_IDS

    for pipeline in sorted(_REPORT_PIPELINE_IDS):
        plan = _gate(pipeline_id=pipeline)
        assert plan.wants_report is True, pipeline


def test_the_verdict_is_logged():
    """The first question asked of a report nobody wanted — and of an answer
    someone expected a document for — is what the plan thought."""
    assert _gate().as_log_dict()["wants_report"] is True
    assert _gate(wants_report=False).as_log_dict()["wants_report"] is False


# ── the prompt half ──────────────────────────────────────────────────────────
# The model cannot be tested here without the model, but the RULES it is given
# can be: each of these was a reported failure, and a prompt that stops stating
# one is how that failure comes back.


def test_the_prompt_states_the_rule_and_its_words():
    from app.ask_planner import _PLANNER_SYSTEM

    block = _PLANNER_SYSTEM
    assert "REPORT OR ANSWER" in block
    # The document words the owner named — asking for one of these is the bar.
    for word in ('"report"', '"write-up"', '"one-pager"', '"document"'):
        assert word in block, word
    # …and the document's own name still counts as asking for it.
    assert "voice of customer" in block.lower()


def test_the_prompt_keeps_the_reported_question_on_the_answer_side():
    """The sentence the owner reported, in the prompt as a worked example. A
    rule the model has to generalise from one abstract sentence is a rule it
    will apply inconsistently."""
    from app.ask_planner import _PLANNER_SYSTEM

    lowered = " ".join(_PLANNER_SYSTEM.lower().split())
    assert "look at all customer conversations in the last month" in lowered
    assert "summarize last week's customer calls" in lowered


def test_the_prompt_says_the_pipeline_still_gathers():
    """The trap this rule could walk into: a model reading "not a report" as
    "read less", answering a question about a month of calls from nothing."""
    from app.ask_planner import _PLANNER_SYSTEM

    assert "THE SAME PIPELINE SERVES BOTH" in _PLANNER_SYSTEM


def test_the_prompt_version_moved_with_the_field():
    """Rows pool by version, and a v17 row was never asked where its answer
    should go — its pipeline pick says nothing about whether a document was
    wanted."""
    from app.ask_planner import _PROMPT_VERSION

    assert _PROMPT_VERSION == "ask-planner-v19"
