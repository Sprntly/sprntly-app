"""The generated document, measured against the sample Apurva supplied.

Source: `~/Downloads/Improving-revenue-by-2-percent.pdf` — an 11-page product
decision memo. Apurva: "I want the generated document to be exactly like the
sample document I gave you in terms of format and type of details, hence let's
test on those guidelines."

WHY THIS FILE EXISTS AS TESTS RATHER THAN AS A DOCUMENT. "Make it like the PDF"
is not checkable by reading the code, and every previous conformance claim in
this feature has been made by eye and been wrong at least once. Each element of
the memo is one assertion here, so the distance to the target is a number that
CI reports rather than a judgement someone re-forms each time.

THREE TIERS, and the tiering is the honest part:

  PRESENT      the memo has it and the document has it. Asserted.
  BUILDABLE    the memo has it, the document does not, and every input exists
               in the corpus. `xfail(strict=True)` — so the day one is built
               the test flips to a failure that says "this now passes, promote
               it", and nobody has to remember.
  NOT DERIVABLE the memo has it and the corpus cannot support it: money mapped
               to accounts, engineering effort, owners, dates. These are
               `skip`, with the reason, because a test that can never pass
               without inventing data is not a target — it is a decision to be
               taken with Apurva about what the gate should ASK for.
"""
from __future__ import annotations

import re

import pytest

from app.crucible.report import render_report_html


def _findings() -> list[dict]:
    return _DOC_FINDINGS


def _run_dict() -> dict:
    import copy
    return copy.deepcopy(_DOC_RUN)


def _doc() -> str:
    """A rendered report with everything the pipeline can currently produce."""
    return render_report_html(_run_dict(), _findings(), _DOC_LEDGER)


def _text(html: str) -> str:
    return re.sub(r"<[^>]+>", " ", html)


# ─── PRESENT: the memo has it, and so do we ─────────────────────────────────

def test_the_goal_is_the_title():
    """Memo p1: the goal is the headline — "Improving revenue by 2%"."""
    assert "improve revenue by 2%" in _doc()


def test_a_funnel_before_the_findings():
    """Memo p1: "Twelve initiatives can move revenue. Two are high confidence.
    One is the recommendation." — the shape of the answer before the answer."""
    assert "bear on this goal" in _text(_doc())


def test_a_scoring_table_with_its_definitions():
    """Memo §04: RICE, with a definitions table and a scores table, so the
    ranking is reviewable rather than asserted."""
    doc = _doc()
    assert "How this was ranked (RICE)" in doc
    assert "<table>" in doc
    for term in ("Reach", "Impact", "Confidence", "Effort"):
        assert term in doc


def test_each_item_carries_a_recommendation_and_its_reasoning():
    """Memo §01: the recommendation, then "We recommend Option 1 for three
    reasons"."""
    t = _text(_doc())
    assert "Recommended." in t
    assert "Repair the wide-table export path" in t
    assert "three accounts named it in renewal calls" in t


def test_an_appendix_of_what_was_set_aside_and_why():
    """Memo §06: "The other ten initiatives, and why each was set aside" — the
    column that matters is the last one."""
    t = _text(_doc())
    assert "Considered and set aside" in t
    assert "describes our own product" in t


def test_evidence_is_quoted_in_its_source_s_own_words():
    """Memo §02 quotes participants by role, never by name."""
    assert "exports return empty files" in _text(_doc())


def test_what_the_analysis_cannot_tell_you_is_stated():
    """Memo §04: "What RICE does not capture, and why it does not change the
    answer" — the limits are part of the document, not an omission."""
    assert "What this cannot tell you" in _doc()


def test_a_missing_number_is_named_not_zeroed():
    """Memo §06's REVENUE THIS CYCLE column: `Unquantified`, `Not
    attributable`, `Direction unknown`, `Unsized`. Never 0."""
    assert "Unquantified" in _doc()


# ─── BUILDABLE: the memo has it, the corpus can support it, we have not ─────

def test_a_stat_strip_of_the_headline_numbers():
    """Memo p1: the cover closes with a strip of the numbers that shape the
    answer, so a reader knows it at a glance rather than assembling it from
    four paragraphs."""
    t = _text(_doc())
    for label in ("Signals read", "Themes found", "Bear on this goal",
                  "Sized", "High confidence"):
        assert label in t


def test_the_strip_has_no_data_window_because_the_dates_are_the_ingest_clock():
    """The memo's DATA WINDOW is the period the evidence covers. On this
    substrate claim dates are when we READ the evidence — `call_digest` and the
    coverage notes both say so — so a window printed from them would be the
    ingest clock wearing the corpus's clothes."""
    assert "data window" not in _text(_doc()).lower()


def test_money_reaches_the_strip_only_as_the_readers_own_estimate():
    """A number in a strip is read as a fact. This one is the reader's estimate
    multiplied out, so the LABEL carries that rather than a footnote three
    sections down."""
    run = _run_dict()
    run["prioritisation"]["plan"]["account_value"] = 12000
    t = _text(render_report_html(run, _findings(), []))
    assert "Reach × your estimate" in t
    assert "48,000" in t


@pytest.mark.xfail(strict=True, reason=(
    "Memo headings are CLAIMS — 'We tell customers their export succeeded "
    "roughly 72,000 times, and it did not'. Ours are labels: 'What the "
    "evidence says'. The claim is derivable from the top finding."))
def test_section_headings_are_claims_not_labels():
    # NOT A WORD COUNT. The first version asserted "some heading is longer than
    # six words" and XPASSED against "Considered and set aside for this goal
    # (1)" — a label that happens to be long. The property is that the findings
    # heading SAYS something about this corpus, so the test is that it is no
    # longer the constant label.
    heads = [re.sub(r"<[^>]+>", "", h) for h in re.findall(r"<h2>(.*?)</h2>", _doc())]
    assert not any(h.startswith("What the evidence says") for h in heads), (
        "the findings heading is still a label; the memo's is a claim"
    )


def test_the_appendix_is_a_table_with_a_value_column():
    """Memo §06: INITIATIVE | WHAT IT IS | REVENUE THIS CYCLE | WHY NOT
    PRIORITISED.

    A bullet list carried two of those — the label and the reason — so what the
    theme actually SAID and what it was worth were dropped, which are the two a
    reader needs in order to disagree with the verdict."""
    doc = _doc()
    i = doc.find("Considered and set aside")
    tail = doc[i:]
    assert "<table>" in tail
    for col in ("Theme", "What it is", "Worth this cycle", "Why it was set aside"):
        assert col in tail
    # The value column carries the theme's actual worth. THIS fixture's
    # set-aside finding is sized, so it reads as a reach; the unsized case is
    # its own test below, because the two say different things.
    assert "2 accounts" in tail


def test_an_unsized_set_aside_theme_reads_Unsized_not_zero():
    """Memo §06's REVENUE THIS CYCLE column never prints a misleading zero: it
    prints `Unsized`, `Not attributable`, `Unquantified`. That is I3 expressed
    as a vocabulary, and it is why the column can exist at all on a corpus with
    no money in it — the honest answer to "what is this worth" is usually a
    word."""
    run = _run_dict()
    findings = _findings()
    import copy
    findings = copy.deepcopy(findings)
    findings[1]["impact_value"] = None
    findings[1]["impact"] = {"value": None, "affected_population": None}
    tail = render_report_html(run, findings, [])
    tail = tail[tail.find("Considered and set aside"):]
    assert "Unsized" in tail
    assert "<td>0</td>" not in tail


@pytest.mark.xfail(strict=True, reason=(
    "Memo §01 names ONE recommendation for the whole memo ('Option 1'). We "
    "recommend per finding and never synthesise across them."))
def test_one_recommendation_for_the_whole_document():
    assert "The recommendation" in _text(_doc())


# ─── NOT DERIVABLE: the corpus cannot support it ────────────────────────────

@pytest.mark.skip(reason=(
    "Memo sizes everything in money ($944K, $7.01M reach, $186K renewing). "
    "Needs revenue mapped to accounts; the plan step already declares this "
    "corpus has none. Would require an ingest change or a gate that ASKS what "
    "an account is worth — Apurva's call, not a rendering fix."))
def test_sizing_in_currency():
    ...


@pytest.mark.skip(reason=(
    "Memo §04 scores Effort in person-months, sourced from the product team. "
    "`KIND_TO_CLAIM_TYPE` maps `milestone -> attempt` and no estimate is "
    "ingested anywhere; the `prioritize` skill is explicit that 'Effort comes "
    "from engineering, not the PM's hope'."))
def test_effort_in_person_months():
    ...


def test_a_decision_box_when_the_gate_was_answered():
    """Memo p1: DECISION OWNER / NEEDED BY.

    The corpus knows no one's name and no one's calendar — so the gate asks.
    Apurva: "the plan gate can start asking questions it doesn't know answers
    to."
    """
    run = _run_dict()
    run["prioritisation"]["plan"].update(
        {"decision_owner": "VP Product", "needed_by": "1 September"})
    t = _text(render_report_html(run, _findings(), []))
    assert "The decision" in t
    assert "VP Product" in t
    assert "1 September" in t


def test_no_decision_box_when_nobody_answered():
    """A box of blanks is worse than no box: it implies the decision has a home
    when it does not."""
    assert "The decision" not in _text(_doc())


def test_the_stake_is_counted_not_forecast():
    """The memo writes "if we do nothing" as a forecast. This corpus cannot
    forecast, so the line states what the evidence COUNTS."""
    run = _run_dict()
    run["prioritisation"]["plan"].update({"decision_owner": "VP Product"})
    t = _text(render_report_html(run, _findings(), []))
    assert "what the evidence counts, not a forecast" in t


def test_money_appears_only_when_the_reader_supplied_a_value_and_is_labelled():
    """Memo sizes in money. The corpus has none — so the gate asks what an
    account is worth, and the product is labelled an estimate in the same
    breath rather than in a footnote."""
    run = _run_dict()
    run["prioritisation"]["plan"].update(
        {"decision_owner": "VP Product", "account_value": 12000})
    t = _text(render_report_html(run, _findings(), []))
    # 4 accounts x 12,000. FOUR, not six: the second finding is set aside by
    # the relevance gate, and the stake counts only what bears on the goal —
    # which is the point of the gate and worth pinning here.
    assert "48,000" in t
    assert "an estimate you gave rather than something measured" in t


@pytest.mark.skip(reason=(
    "Memo §05 plan: 8 actions with owners and dates. The gate now supplies the "
    "DECISION owner and date; per-action owners are a different artifact and "
    "would need the recommendations turned into a sequenced plan."))
def test_a_dated_plan_of_actions():
    ...


@pytest.mark.skip(reason=(
    "Memo §05 success metrics need a baseline and a target read from an "
    "instrumented series ('57.2% -> 91%+ by 22 September'). The plan step "
    "states the analysis reads documents, not a metric series."))
def test_success_metrics_with_baselines_and_a_kill_condition():
    ...


_DOC_RUN = {
        "id": 1, "goal_text": "improve revenue by 2%", "status": "ready",
        "claim_count": 40, "coverage_notes": [],
        "prioritisation": {
            "plan": {
                "goal_text": "improve revenue by 2%",
                "definition_text": "new business plus expansion, net of churn",
                "definition_adopted": True,
                "currency": "accounts", "total_signals": 1200,
                "framework": "RICE",
                "sources": [{"source_type": "customer_voice", "signal_count": 900,
                             "label": "calls and customer tickets",
                             "witnesses": "what customers asked for"}],
                "cannot_answer": [{"question": "How much is it worth?",
                                   "because": "no revenue is mapped to accounts",
                                   "remedy": "connect billing"}],
                "will_produce": ["Themes ranked by reach"],
                "hypotheses": [], "excluded_sources": [],
            },
            "set_aside_by_rank": [None, "describes our own product"],
            "findings_extra_by_rank": [
                {"label": "export defect", "example": "exports return empty files",
                 "claim_types": ["constraint"],
                 "recommendation": {"action": "Repair the wide-table export path",
                                    "because": "three accounts named it in renewal calls"}},
                {"label": "our own platform", "example": "the platform supports X",
                 "claim_types": ["existence"]},
            ],
        },
    }

_DOC_FINDINGS = [
        {"statement": "9 claims across 4 accounts concern export defect",
         "claim_ids": ["c1", "c2"], "adjudication": "corroborated",
         "impact_value": 4, "currency": "accounts", "confidence_band": "medium",
         "surfaced_by": ["Fireflies call transcripts (9)"], "assumed_params": [],
         "impact": {"value": 4, "affected_population": 4},
         "confidence": {"band": "medium", "weakest_leg": "outcome",
                        "weakest_leg_reason": "no outcome evidence", "cap_reason": None}},
        {"statement": "5 claims across 2 accounts concern our own platform",
         "claim_ids": ["c3"], "adjudication": "corroborated",
         "impact_value": 2, "currency": "accounts", "confidence_band": "low",
         "surfaced_by": ["Fireflies call transcripts (5)"], "assumed_params": [],
         "impact": {"value": 2, "affected_population": 2},
         "confidence": {"band": "low", "weakest_leg": "outcome",
                        "weakest_leg_reason": "no outcome evidence", "cap_reason": None}},
    ]

_DOC_LEDGER = [{"label": "a one-off", "reason": "a single claim",
               "stopped_at_stage": "corroboration", "claim_ids": ["c9"]}]
