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
    # SPLIT ON PURPOSE: the scores table reads with the answer, the
    # definitions read with the method. Both still render, and the memo owes
    # both.
    assert "The ranking (RICE)" in doc
    assert "How the ranking works (RICE)" in doc
    assert re.search(r"<table[^>]*>", doc)
    for term in ("Reach", "Impact", "Confidence", "Effort"):
        assert term in doc


def test_each_item_carries_a_recommendation_and_its_reasoning():
    """Memo §01: the recommendation, then "We recommend Option 1 for three
    reasons"."""
    t = _text(_doc())
    # "Suggested." is the one-line pass's word; "Recommended." belongs to the
    # deep card and to the single synthesized section. Either satisfies §01 —
    # what the memo owes is a recommendation with its reasoning attached, not
    # one particular label.
    assert "Suggested." in t or "Recommended." in t
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


def test_section_headings_are_claims_not_labels():
    # NOT A WORD COUNT. The first version asserted "some heading is longer than
    # six words" and XPASSED against "Considered and set aside for this goal
    # (1)" — a label that happens to be long. The property is that the findings
    # heading SAYS something about this corpus, so the test is that it is no
    # longer the constant label.
    # EVERY HEADING, NOT JUST THE `<h2>`s. The findings section's own `<h2>`
    # is a plain label again ("Each one, in full") and that is deliberate: the
    # memo now leads with the answer, so the section heading's job is to say
    # where you are, and the CLAIM is the heading of each write-up under it.
    # The property is unchanged — a heading in this document says something
    # about this corpus — so the search is over the headings a reader
    # actually meets.
    heads = [re.sub(r"<[^>]+>", "", h)
             for h in re.findall(r"<h[23][^>]*>(.*?)</h[23]>", _doc())]
    assert not any(h.startswith("What the evidence says") for h in heads), (
        "the findings heading is still the old label; the memo's is a claim"
    )
    # NOT JUST "DIFFERENT" — DERIVED. Any different string would satisfy the
    # assertion above (an empty heading, a typo'd label, anything). The claim
    # has to actually be about THIS corpus, so this proves it by finding a
    # fragment of the top finding's own statement inside a rendered heading.
    assert any(h.strip() for h in heads), "no non-empty h2 headings rendered"
    assert any("export defect" in h for h in heads), (
        "a heading should carry a fragment of the top finding's own statement "
        "('...concern export defect'), proving the claim is derived from the "
        "corpus rather than any different string"
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
    assert re.search(r"<table[^>]*>", tail)
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


def test_one_recommendation_for_the_whole_document():
    """Memo §01 names ONE recommendation for the whole memo ('Option 1'). A
    synthesized recommendation (`recommend.build_synthesized_recommendation`)
    now sits above the per-finding write-ups, narrated from what the deep
    pass already decided (I2) — never a second ranking, and additive: the
    per-finding recommendation below is untouched.

    NOT A LITERAL-STRING MATCH on wording nobody chose deliberately — the
    property is that exactly ONE such section exists, and it reads as
    distinct from the per-finding "Recommended." cards it sits above.
    """
    run = _run_dict()
    run["prioritisation"]["synthesized_recommendation"] = {
        "action": "Repair the wide-table export path and fix it before the "
                  "next renewal cycle",
        "because": "Both top findings point at the same failure surface, "
                   "and it is the one already named as the export defect "
                   "recommendation below.",
        "citations": [
            {"claim_id": "c1", "evidence": "exports return empty files",
             "cited_claim": "exports return empty files"},
        ],
    }
    html = render_report_html(run, _findings(), _DOC_LEDGER)
    t = _text(html)
    # EXACTLY ONE SECTION IS THE ASK, and it is the first screen. The
    # synthesis used to be headed "The recommendation" two screens above
    # options headed "recommended", so a reader met the word three times and
    # could not tell which of them was the ask; it is now the argument FOR the
    # answer and is headed as one.
    assert len(re.findall(r"<h2[^>]*>What we recommend</h2>", html)) == 1
    # Exactly one synthesized section — not zero (it must render) and not
    # duplicated across the document.
    assert t.count("Why we would start here") == 1
    # It carries the synthesized content, distinct from the per-finding
    # recommendation's own action text ("Repair the wide-table export path")
    # that still renders separately below it.
    assert "fix it before the next renewal cycle" in t
    assert "Repair the wide-table export path" in t  # the per-finding one


# ─── NOT DERIVABLE: the corpus cannot support it ────────────────────────────

def test_sizing_in_currency():
    """Memo sizes EVERY finding in money ($944K, $7.01M reach, $186K
    renewing) — mapped independently from a revenue system this corpus does
    not have. That exact form is still NOT DERIVABLE: nothing here connects
    an account to its actual revenue.

    What is buildable without new data, and now built: each finding's own
    reach, multiplied by the SAME reader-supplied per-account figure the
    corpus-wide stat strip and decision box already use — in their exact
    words, so a per-finding size in money is never a different voice from
    the aggregate one. DISPLAY-ONLY: this proves two DIFFERENT findings
    carry two DIFFERENT dollar figures tied to their OWN reach, not one
    total smeared across the section, and that ranking-relevant fields
    (`impact_value` itself) are untouched by this."""
    run = _run_dict()
    run["prioritisation"]["plan"]["account_value"] = 10000
    # A fresh two-finding corpus, neither set aside — the frozen
    # `_DOC_FINDINGS` fixture keeps only one finding after the goal-relevance
    # gate, which would make a per-finding assertion indistinguishable from a
    # corpus-wide total.
    run["prioritisation"]["set_aside_by_rank"] = [None, None]
    run["prioritisation"]["findings_extra_by_rank"] = []
    findings = [
        {"statement": "3 accounts named export latency", "impact_value": 3,
         "currency": "accounts", "confidence_band": "medium", "confidence": {}},
        {"statement": "9 accounts named renewal churn", "impact_value": 9,
         "currency": "accounts", "confidence_band": "medium", "confidence": {}},
    ]
    t = _text(render_report_html(run, findings, []))
    assert "30,000" in t, "the smaller finding's own reach x estimate"
    assert "90,000" in t, "the larger finding's own reach x estimate"
    assert "on your own figure of 10,000 per account" in t
    assert "an estimate you gave rather than something measured" in t
    # RANKING IS UNTOUCHED (I10): the finding dicts this test built its own
    # assertions from still carry their original `impact_value` — nothing
    # in the render path wrote a derived number back onto them.
    assert findings[0]["impact_value"] == 3
    assert findings[1]["impact_value"] == 9


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
    assert "Who decides, and by when" in t
    assert "VP Product" in t
    assert "1 September" in t


def test_no_decision_box_when_nobody_answered():
    """A box of blanks is worse than no box: it implies the decision has a home
    when it does not."""
    assert "Who decides, and by when" not in _text(_doc())
    assert "Needed by" not in _text(_doc())


def test_the_stake_is_counted_not_forecast():
    """The memo writes "if we do nothing" as a forecast. This corpus cannot
    forecast, so the line states what the evidence COUNTS."""
    run = _run_dict()
    run["prioritisation"]["plan"].update({"decision_owner": "VP Product"})
    t = _text(render_report_html(run, _findings(), []))
    assert "what we counted, not a forecast" in t


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
