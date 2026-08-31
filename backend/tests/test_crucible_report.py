"""`render_report_html` — the run as a document.

The renderer is the one place a run's findings become prose, and it is what
chat, export and the editable document all read. Three classes of failure it
can have, all of them silent:

  1. IT DROPS AN HONESTY. An unsized finding rendered as 0, a coverage note
     omitted, a ledger entry without its reason, a limits section quietly
     skipped. Each one leaves a report that still reads correctly and claims
     more than the run can support.
  2. IT DOES NOT SURVIVE STORAGE. The output is stored in
     `custom_artifacts.body_html`, which sanitizes to an allowlist. Markup that
     looks right here and is stripped on write produces a report that renders
     bare in the editor.
  3. IT TRUSTS ITS INPUT. Finding statements are projected from a customer's
     own documents. They are untrusted strings by the time they reach here.
"""
from __future__ import annotations

import re

from app.crucible.report import (
    MAX_DETAILED_FINDINGS,
    MAX_LEDGER_REASON_CHARS,
    MAX_RICE_ROWS,
    body_fingerprint,
    render_report_html,
    report_title,
)
from app.custom_artifact_html import sanitize_artifact_html


def _run(**over) -> dict:
    row = {
        "id": 1,
        "goal_text": "raise renewal rate",
        "coverage_notes": [
            {"reason": "undated evidence",
             "actual": "40 of 1200 signals carried no usable date"},
        ],
        "prioritisation": {"plan": _plan()},
    }
    row.update(over)
    return row


def _plan(**over) -> dict:
    plan = {
        "definition_text": "renewals closed in the quarter",
        "total_signals": 1200,
        "sources": [{"source_type": "support", "signal_count": 300,
                     "label": "Support tickets", "witnesses": "what broke"}],
        "cannot_answer": [{"question": "How much revenue?",
                           "because": "nothing carries account revenue",
                           "remedy": "connect billing"}],
        "hypotheses": ["onboarding is the problem"],
        "excluded_sources": ["project_mgmt"],
    }
    plan.update(over)
    return plan


def _finding(**over) -> dict:
    f = {
        "statement": "Renewals stall on the parts flow",
        "claim_ids": ["c1", "c2"],
        "adjudication": "corroborated",
        "impact_value": 14,
        "currency": "accounts",
        "confidence_band": "medium",
        "surfaced_by": ["calls/renewals-q3"],
        "assumed_params": [{"name": "seat count", "basis": "cohort median"}],
        "confidence": {"band": "medium", "weakest_leg_reason": "one quarter only",
                       "cap_reason": None},
    }
    f.update(over)
    return f


# ─── 1. The honesties ───────────────────────────────────────────────────────

def test_an_unsized_finding_says_so_and_is_never_a_number():
    """I3. NULL means "we could not size this"; 0 means "we sized it and it is
    nothing". They lead to opposite decisions."""
    html = render_report_html(_run(), [_finding(impact_value=None)])
    assert "Could not be sized" in html
    assert "0 account" not in html


def test_one_account_is_singular():
    """A count that reads "1 accounts" is the kind of thing that makes a
    reader stop trusting the numbers around it."""
    html = render_report_html(_run(), [_finding(impact_value=1)])
    assert "1 account" in html
    assert "1 accounts" not in html


def test_coverage_notes_come_before_the_findings_they_qualify():
    """A note that a third of the evidence was undated changes how every line
    beneath it should be read. A degradation discovered after the conclusion
    has already done its damage — which is why the panel puts these in "What
    was read" rather than in a footer, and why this renderer must too."""
    html = render_report_html(_run(), [_finding()])
    assert html.index("40 of 1200 signals") < html.index("Renewals stall")


def test_a_finding_carries_the_documents_it_rests_on():
    """Without provenance beside the claim, "14 accounts mentioned this" cannot
    be checked against anything — it is an assertion, not an argument."""
    html = render_report_html(_run(), [_finding()])
    assert "calls/renewals-q3" in html


def test_every_assumed_parameter_is_disclosed_where_the_number_is_read():
    """I8. A methodology page nobody opens is not a disclosure."""
    html = render_report_html(_run(), [_finding()])
    assert "seat count" in html and "cohort median" in html


def test_a_conflict_is_called_a_conflict():
    """Two sources that may both speak disagreeing is worth more than either of
    them alone, which is also why the ranking puts it first."""
    html = render_report_html(_run(), [_finding(adjudication="conflict")])
    assert "sources disagree" in html


def test_the_ruled_out_ledger_keeps_its_reasons():
    """A ranking whose rejections are invisible is one you have to take on
    faith."""
    html = render_report_html(
        _run(), [_finding()],
        [{"label": "Mobile parity", "reason": "no claim survived the echo check",
          "stopped_at_stage": "verification"}],
    )
    assert "Mobile parity" in html
    assert "no claim survived the echo check" in html
    assert "verification" in html


def test_a_run_with_no_findings_says_the_ledger_is_the_result():
    """Silence would read as "nothing was wrong". The rejections ARE the
    output of a run that verified nothing."""
    html = render_report_html(_run(), [])
    assert "Nothing survived verification" in html


def test_the_limits_section_is_built_from_the_plan_s_own_gaps():
    """What the user was warned about BEFORE the run is what they are reminded
    of after it — otherwise the warning was a formality."""
    html = render_report_html(_run(), [_finding()])
    assert "How much revenue?" in html
    assert "connect billing" in html


def test_a_run_without_a_recorded_definition_says_so_rather_than_skipping_it():
    """A report whose subject is unknown must not look like the ordinary
    case."""
    html = render_report_html(
        _run(prioritisation={}), [_finding()], plan={},
    )
    assert "No confirmed definition was recorded" in html


def test_hypotheses_are_reported_as_untested():
    """The engine does not test a stated hypothesis. Listing these beside the
    findings without saying so lets a reader infer that silence meant "not
    supported" — a conclusion nothing produced."""
    html = render_report_html(_run(), [_finding()])
    assert "onboarding is the problem" in html
    assert "did not test these" in html


def test_a_source_the_user_dropped_is_named_in_the_readers_language():
    html = render_report_html(_run(), [_finding()])
    assert "project mgmt" in html
    assert "project_mgmt" not in html


# ─── 2. It has to survive the sanitizer ─────────────────────────────────────

def test_the_rendered_report_survives_the_artifact_sanitizer_intact():
    """The output is stored through `sanitize_artifact_html`, which strips
    everything outside the editor's allowlist. A renderer that leans on
    `class`, `data-*` or `<section>` produces markup that reads correctly here
    and arrives at the editor bare, so this asserts the round trip rather than
    the string."""
    html = render_report_html(
        _run(), [_finding(), _finding(impact_value=None)],
        [{"label": "Mobile parity", "reason": "dropped", "stopped_at_stage": "v"}],
    )
    cleaned = sanitize_artifact_html(html)
    for phrase in (
        "raise renewal rate", "renewals closed in the quarter",
        "Could not be sized", "calls/renewals-q3",
        "40 of 1200 signals", "Mobile parity", "connect billing",
    ):
        assert phrase in cleaned, phrase
    # And the structure a reader navigates by is still there.
    assert "<h1>" in cleaned and "<h2>" in cleaned and "<li>" in cleaned


def test_the_renderer_emits_no_markup_the_sanitizer_would_drop():
    """Stronger than "the words survive": the TAGS have to as well, or the
    stored document is a different document from the one this file was
    reviewed as."""
    html = render_report_html(_run(), [_finding()], [{"label": "x", "reason": "y"}])
    for banned in ("class=", "data-testid", "<section", "<article", "<details"):
        assert banned not in html, banned


# ─── 3. Untrusted input ─────────────────────────────────────────────────────

def test_tenant_text_is_escaped():
    """A finding statement is projected out of a customer's own documents, so
    it is untrusted by the time it reaches here. The sanitizer downstream is a
    second line of defence, not the only one."""
    html = render_report_html(
        _run(goal_text="<img src=x onerror=alert(1)>"),
        [_finding(statement="<script>alert('x')</script> stalls")],
    )
    # The point is that the ANGLE BRACKETS are gone, not that the words are.
    # `onerror=alert(1)` as inert text is exactly what escaping produces, and
    # asserting its absence would be asserting censorship instead of safety.
    assert "<script>" not in html
    assert "<img" not in html
    assert "&lt;script&gt;" in html
    assert "&lt;img src=x onerror=alert(1)&gt;" in html
    # And it stays inert through the write path.
    cleaned = sanitize_artifact_html(html)
    assert "<script" not in cleaned and "<img" not in cleaned


# ─── 4. Determinism and the fingerprint ─────────────────────────────────────

def test_the_same_run_renders_the_same_bytes():
    """`body_fingerprint` means "has a human touched this" only if rendering is
    deterministic. A timestamp, a set iteration or a dict ordering leaking into
    the output would make every report read as edited."""
    args = (_run(), [_finding()], [{"label": "x", "reason": "y"}])
    assert render_report_html(*args) == render_report_html(*args)


def test_the_fingerprint_changes_when_the_body_does_and_not_otherwise():
    a = render_report_html(_run(), [_finding()])
    b = render_report_html(_run(), [_finding(statement="Something else")])
    assert body_fingerprint(a) == body_fingerprint(a)
    assert body_fingerprint(a) != body_fingerprint(b)


def test_the_title_names_the_goal_so_two_reports_are_tellable_apart():
    assert report_title({"goal_text": "raise renewal rate"}) == (
        "Goal analysis: raise renewal rate"
    )
    # A goal long enough to overrun the title column is cut at a word.
    long_goal = " ".join(["renewal"] * 60)
    title = report_title({"goal_text": long_goal})
    assert len(title) < 300 and title.endswith("…")
    # A run with no goal text still has a name rather than a bare colon.
    assert report_title({"goal_text": "  "}) == "Goal analysis"


# ─── The size ceiling, found on staging ──────────────────────────────────────

def _many_findings(n: int) -> list[dict]:
    """Findings shaped like real ones — statements and provenance are what make
    a block big, and a fixture of `{"statement": "x"}` cannot find a size bug."""
    return [{
        "id": i,
        "statement": f"{7 + i % 20} claims across {1 + i % 9} accounts concern "
                     f"“Theme {i}: a realistically long label of the kind the "
                     f"knowledge graph actually produces”.",
        "claim_ids": [f"c{i}-{k}" for k in range(9)],
        "impact_value": None if i % 3 else float(i % 9),
        "currency": "accounts",
        "confidence_band": "medium",
        "adjudication": "corroborated",
        "surfaced_by": [f"fireflies-sync-batch-{i % 12} ({4 + i % 6})",
                        f"slack/#channel-{i % 7} ({1 + i % 3})",
                        "+3 more documents"],
        "assumed_params": [{"name": "value_per_account",
                            "basis": "no revenue data connected; accounts "
                                     "weighted equally"}],
        "confidence": {"band": "medium", "weakest_leg": "problem",
                       "weakest_leg_reason": "nothing in this company's "
                                             "connected sources records whether "
                                             "a fix like this has ever worked",
                       "cap_reason": "capped at medium: no outcome evidence"},
        "tier": "deep" if i < 5 else "shallow",
    } for i in range(n)]


def _full_ledger(n: int = 101) -> list[dict]:
    """The ledger a real run carries — capped at ~101 rows by the pipeline."""
    return [{
        "id": i,
        "label": f"Theme {i}: a rejected candidate with a realistic label",
        "reason": "all 4 supporting claims land within 6 days and come from "
                  "one source document — this is one conversation echoing "
                  "through the corpus, not a pattern over time",
        "stopped_at_stage": "verification",
        "claim_ids": [f"c{i}-{k}" for k in range(6)],
    } for i in range(n)]


def _full_plan() -> dict:
    """A plan with every section populated, as `build_plan` produces."""
    return {
        "goal_text": "improve revenue",
        "definition_text": "recognised revenue from paying accounts, net of "
                           "refunds, as finance books it",
        "total_signals": 15570,
        "sources": [
            {"source_type": "project_mgmt", "signal_count": 12604,
             "label": "the tracker",
             "witnesses": "what was built, broken, blocked or attempted"},
            {"source_type": "pm_manual", "signal_count": 2007,
             "label": "your own business context",
             "witnesses": "the company's stated constraints and goals"},
            {"source_type": "communication", "signal_count": 387,
             "label": "Slack and email",
             "witnesses": "what was discussed, hit and attempted"},
        ],
        "cannot_answer": [
            {"question": "How many points will this move the metric?",
             "because": "the engine cannot yet size a finding in the goal's "
                        "own unit — it reports reach instead",
             "remedy": "no action needed from you; this is the next capability"},
            {"question": "Did a change like this work last time?",
             "because": "no measured outcomes are connected",
             "remedy": "connect your experiment tool, or upload the history"},
        ],
        "will_produce": ["Themes ranked by reach", "A considered list",
                         "Every degradation disclosed"],
        "hypotheses": ["expansion is stalling because onboarding takes too long",
                       "customers are blocked on the Jira connector"],
        "excluded_sources": [],
    }


def test_a_run_with_hundreds_of_findings_still_fits_the_document_store():
    """THE STAGING BUG. A real 831-finding run rendered to 421,696 characters
    against a 400,000 limit, so `custom_artifacts` refused the body, the route
    had no handler, and the browser got a dropped connection — an outage, for
    what was really a refused write.

    Every unit test passed because every fixture had a handful of findings.
    """
    from app.db.custom_artifacts import MAX_BODY_CHARS

    # THE WHOLE DOCUMENT, not just the section the cap bounds. The first
    # version of this test passed `[]` for the ledger and `{}` for the plan —
    # so it measured the one part that is now bounded and none of the parts
    # that are not, which is the same too-small-fixture mistake that let the
    # original bug ship.
    html = render_report_html(
        {"id": 1, "goal_text": "improve revenue",
         "coverage_notes": [
             {"reason": "evidence is dated by ingest, not by when it happened",
              "actual": "most signals carry the timestamp we read them at"},
             {"reason": "most findings could not be sized",
              "actual": "829 of 831 findings name no account"}]},
        _many_findings(831), _full_ledger(), _full_plan(),
    )
    assert len(html) < MAX_BODY_CHARS, (
        f"rendered {len(html)} chars against a {MAX_BODY_CHARS} limit"
    )


def test_the_findings_beyond_the_cap_are_listed_and_counted_not_dropped():
    """A document that stopped at the cap without a word would read as "these
    are all the findings" — the quiet degradation this whole feature exists to
    avoid."""
    from app.crucible.report import MAX_DETAILED_FINDINGS

    n = MAX_DETAILED_FINDINGS + 40
    html = render_report_html(
        {"id": 1, "goal_text": "g", "coverage_notes": []},
        _many_findings(n), [], {},
    )
    assert "The next 40 findings" in html
    # The last one is still named, in rank order.
    assert f"{n}." in html


def test_a_small_run_is_not_truncated_at_all():
    """The control: the cap must not touch an ordinary run."""
    html = render_report_html(
        {"id": 1, "goal_text": "g", "coverage_notes": []},
        _many_findings(4), [], {},
    )
    assert "listed below in rank order" not in html


def test_a_pathologically_long_statement_cannot_blow_the_budget():
    """THE ROOT OF WHY 150 WAS CALIBRATED, NOT BOUNDED. `cluster.label_for`
    caps the embedding path at 90 chars — but that is the FALLBACK. The primary
    path takes `kg_entity.canonical_label` verbatim with no truncation
    anywhere, so a statement was unbounded in code. Largest observed was 126;
    nothing stopped it being 100,000."""
    from app.crucible.report import MAX_STATEMENT_CHARS
    from app.db.custom_artifacts import MAX_BODY_CHARS

    monstrous = [{**f, "statement": "word " * 20_000} for f in _many_findings(200)]
    html = render_report_html(
        {"id": 1, "goal_text": "g", "coverage_notes": []},
        monstrous, _full_ledger(), _full_plan(),
    )
    assert len(html) < MAX_BODY_CHARS
    assert "word " * 500 not in html          # actually truncated
    assert MAX_STATEMENT_CHARS < 1_000        # and bounded to something sane


def test_ten_enormous_hypotheses_cannot_blow_the_budget():
    """`ApprovePlan.hypotheses` is `max_length=10` on a `list[str]` — that
    bounds the LIST, not the strings. Ten 40,000-char hypotheses rendered
    410,960 chars with only a dozen findings, and the size test still passed
    because it passed `{}` for the plan."""
    from app.db.custom_artifacts import MAX_BODY_CHARS

    plan = _full_plan()
    plan["hypotheses"] = ["believed " * 5_000 for _ in range(10)]
    html = render_report_html(
        {"id": 1, "goal_text": "g", "coverage_notes": []},
        _many_findings(12), _full_ledger(), plan,
    )
    assert len(html) < MAX_BODY_CHARS


def test_the_document_fits_even_when_every_field_is_hostile():
    """The budget is MEASURED, not asserted.

    The assertion this replaces multiplied constants together and called the
    result derived. It passed while two real paths broke it: an unclipped
    source-document name, and an overflow list that grew with the run. This
    renders both at once, plus a full ledger and plan, and looks at the actual
    length — the only form of the claim that can fail when it is false.
    """
    from app.crucible import report as r

    findings = [
        _finding(
            statement="quote " * 8_000,            # 48,000 chars, all escapable
            surfaced_by=[f'"{c}" report {"x" * 400}.pdf' for c in "abcdefghij"],
            claim_ids=list(range(50)),
        )
        for _ in range(2_000)
    ]
    html = r.render_report_html(
        _run(goal_text="g" * 5_000), findings, _full_ledger(101), _full_plan()
    )

    assert len(html) <= r._BODY_LIMIT, f"rendered {len(html)} > {r._BODY_LIMIT}"
    # It shed detail rather than truncating mid-tag.
    assert html.rstrip().endswith(">")
    # And it SAYS that it did, rather than implying these are all the findings.
    # NOT an `or`. The version of this assertion that used one passed while the
    # document said "the remaining 681 are listed below … nothing has been
    # dropped" three lines above "a further 281 are not listed here" — the two
    # sentences contradicting each other is precisely the bug, so an assertion
    # satisfied by either of them cannot see it.
    if "not listed here" in html:
        # It conceded a remainder, so it must NOT also claim completeness.
        assert "nothing has been dropped" not in html
    # The count in the sentence must be the number of rows actually printed.
    # An earlier version compared against `html.count("<li>")`, which counts
    # every list item in the document — ledger rows and assumed parameters
    # included — so it could never have matched and the check was decoration.
    said = re.search(r"The next (\d+) findings", html)
    assert said, "the overflow list did not say how many it was listing"
    overflow_rows = re.findall(r"<li>\d+\. ", html)
    assert int(said.group(1)) == len(overflow_rows), (
        f"it said {said.group(1)} but printed {len(overflow_rows)}"
    )


def test_it_sheds_detail_when_the_first_rung_does_not_fit():
    """The shed ladder is load-bearing, not decoration.

    Every field is individually bounded now, so it takes a run that is large in
    EVERY dimension at once to overflow the first rung — which is exactly the
    case the old constant-multiplying assertion claimed was impossible.
    """
    from app.crucible import report as r

    # ASSUMPTIONS VARY PER FINDING, and that is not incidental. Identical
    # assumptions across every finding are now hoisted to a single paragraph at
    # the top of the section, which is a large enough saving that a fixture
    # sharing one assumption no longer overflows the first rung at all — this
    # test went green for the wrong reason and stopped exercising the ladder.
    # A run that is genuinely large in every dimension has assumptions that
    # differ, so varying them by index is both what keeps the ladder under test
    # and the more honest fixture.
    fat = [
        _finding(
            statement="s" * 2_000,
            surfaced_by=[f"{'n' * 500}.pdf ({i})" for i in range(10)],
            assumed_params=[{"name": f"{j}-{k}-{'p' * 500}",
                             "basis": "b" * 500}
                            for k in range(20)],
        )
        for j in range(1_500)
    ]
    html = r.render_report_html(_run(), fat, _full_ledger(101), _full_plan())

    assert len(html) <= r._BODY_LIMIT, f"rendered {len(html)}"
    # It really did drop to a lower rung rather than squeaking under.
    blocks = html.count("<h3>")
    assert blocks < r.MAX_FULL_FINDING_BLOCKS, (
        f"rendered {blocks} full blocks — the ladder never fired, so this test "
        f"is not exercising what it claims"
    )
    assert "not listed here" in html


def test_a_long_source_document_name_cannot_inflate_a_block():
    """The one string in a finding block that nothing used to truncate."""
    from app.crucible import report as r

    modest = r._finding_block(_finding(surfaced_by=["report.pdf (3)"]), 1)
    hostile = r._finding_block(
        _finding(surfaced_by=[f"{'n' * 4_000}.pdf (3)"] * 40), 1
    )
    # Bounded by the render caps, not by what the row happens to hold.
    assert len(hostile) < len(modest) + (
        r.MAX_RENDERED_SOURCES * (r.MAX_SOURCE_NAME_CHARS + 8) + 40
    )
    assert "+35 more" in hostile


def test_the_ledger_and_limits_cannot_overrun_the_document():
    """The shed ladder sheds FINDINGS. These two sections are out of its reach.

    `_ledger_section`'s `label` traces to `kg_entity.canonical_label`, the same
    untruncated tenant string that had to be clipped for `statement`; and
    `cannot_answer` is uncapped in count and in all three of its fields. Before
    this, 102 ledger rows at 4,000-char labels rendered 828,071 characters and
    500 gaps rendered 800,349 — over the limit at every rung, so the ladder
    could shed every finding it had and still not fit.
    """
    from app.crucible import report as r

    ledger = [{"label": "L" * 4_000, "reason": "R" * 4_000,
               "stopped_at_stage": "S" * 4_000} for _ in range(500)]
    plan = {"cannot_answer": [{"question": "q" * 4_000, "because": "b" * 4_000,
                               "remedy": "m" * 4_000} for _ in range(500)]}

    html = r.render_report_html(_run(), [], ledger, plan)

    assert len(html) <= r._BODY_LIMIT, f"rendered {len(html)}"
    # Bounded, and the remainder is COUNTED rather than silently gone.
    assert "200 further rejections" in html
    assert "460 further gaps" in html


def test_assumed_parameters_are_disclosed_not_reproduced_whole():
    """I8 requires the assumption be visible; it does not require it verbatim.

    Direct, because the shed ladder would otherwise absorb an unbounded field
    by dropping whole findings — the document would still fit while quietly
    losing findings to one fat parameter list.
    """
    from app.crucible import report as r

    block = r._finding_block(
        _finding(assumed_params=[{"name": "p" * 3_000, "basis": "b" * 3_000}
                                 for _ in range(30)]),
        1,
    )
    assert len(block) < 6_000, f"block is {len(block)} chars"
    assert "and 22 further assumed parameters" in block


def test_clipping_never_emits_a_half_escaped_entity():
    """Cutting escaped text can land inside `&quot;` and emit `&am`."""
    from app.crucible import report as r

    for n in range(1, 40):
        out = r._esc_clipped('"' * 50, n)
        assert len(out) <= n
        assert "&" not in out or out.count("&") == out.count(";")


# ── The report must not claim more than the run established ──────────────────

def test_an_unsized_top_finding_is_not_called_the_largest():
    """THE SUPERLATIVE HAS TO BE EARNED.

    `_rank` orders by size then confidence, and its size term is constant when
    nothing could be sized — so on a corpus with no account attribution the
    order collapses onto the confidence tie-break. Calling row one "the largest
    thing this reading found" then asserts a comparison that was never made,
    and the reader can see it is false: the observed report named a 5-claim
    finding largest with a 27-claim one below it.
    """
    html = render_report_html(_run(), [
        _finding(impact_value=None, claim_ids=["c1"]),
        _finding(impact_value=None, claim_ids=["c2", "c3", "c4"]),
    ])
    assert "largest thing this reading found" not in html
    # And it says what IS true of the order. NOT "arbitrary": with every value
    # None the sort key is (1, 1, -confidence), so the order is strictly
    # confidence-descending — a real ordering, just not the one the heading
    # used to imply.
    assert "ordered by confidence rather than by size" in html
    assert "Nothing in this reading could be sized" in html


def test_a_sized_top_finding_still_says_largest():
    """The claim is not banned, it is CONDITIONAL. A run that could size its
    findings has earned the word and keeps it."""
    html = render_report_html(_run(), [
        _finding(impact_value=120, claim_ids=["c1"]),
        _finding(impact_value=10, claim_ids=["c2"]),
    ])
    assert "largest thing this reading found" in html


def test_the_report_says_findings_were_not_filtered_to_the_goal():
    """The definition gate establishes what the goal means, and claim selection
    never sees it — `_load_signals` reads the whole corpus and `build_findings`
    takes no goal argument. A reader cannot tell that from the output, so a run
    about churn returns receipt-scanning accuracy looking exactly like an
    answer. Stated, rather than left to be discovered."""
    html = render_report_html(_run(), [_finding(impact_value=None)])
    assert "not selected for your goal" in html


def test_largest_is_not_claimed_while_anything_went_unsized():
    """"The largest thing this reading found" quantifies over EVERYTHING, and
    an unsized finding is not a small one — its size is unknown, and an unknown
    can be bigger. So the superlative is only earned when every row has a size;
    otherwise the true sentence is the weaker one, about the sized ones."""
    html = render_report_html(_run(), [
        _finding(impact_value=900, claim_ids=["c1"]),
        _finding(impact_value=None, claim_ids=["c2"]),
    ])
    assert "largest thing this reading found" not in html
    assert "largest of the ones that could be sized" in html
    # And it says HOW MANY it could not size, so "the largest known size" is
    # something the reader can weigh rather than a hedge they have to trust.
    assert "One of these could not be sized" in html


def test_largest_is_still_claimed_when_everything_was_sized():
    """The weaker sentence must not swallow the strong one: when every finding
    has a size, "the largest thing this reading found" is exactly true and
    saying less than that is its own kind of inaccuracy."""
    html = render_report_html(_run(), [
        _finding(impact_value=900, claim_ids=["c1"]),
        _finding(impact_value=3, claim_ids=["c2"]),
    ])
    assert "largest thing this reading found" in html
    assert "largest of the ones that could be sized" not in html


def test_the_definition_does_not_claim_to_have_selected_the_findings():
    """The limits section says findings were NOT filtered or ranked by the
    definition. This section used to say "everything below is measured against
    that sentence and nothing else" — the same document asserting both, with
    the false one three sections higher and in the more prominent position."""
    html = render_report_html(_run(), [_finding()], plan=_full_plan())
    assert "measured against that sentence" not in html
    assert "did not decide which findings appear below" in html
    # Both halves in one document, and they must not contradict.
    assert "not selected for your goal" in html


def test_an_order_the_reader_cannot_check_says_so():
    """`_rank`'s last term is a confidence SCORE, which is never rendered — the
    reader sees bands. On a corpus with no outcome evidence every band comes
    out the same, so "ordered by confidence" describes an ordering they cannot
    check against anything on the page, and a list that LOOKS ranked is read as
    ranked. Position is the most persuasive thing in a document."""
    same = {"band": "medium"}
    html = render_report_html(_run(), [
        _finding(impact_value=None, confidence_band="medium", confidence=same,
                 claim_ids=["c1"]),
        _finding(impact_value=None, confidence_band="medium", confidence=same,
                 claim_ids=["c2"]),
    ])
    assert "Not ranked by reach" in html
    assert "same confidence band" in html
    assert "not as a verdict on which matters more" in html


def test_a_real_confidence_spread_is_not_disclaimed():
    """The control. When the bands actually differ the order IS checkable from
    the page, and telling the reader to discount it would be its own inaccuracy."""
    html = render_report_html(_run(), [
        _finding(impact_value=None, confidence_band="high",
                 confidence={"band": "high"}, claim_ids=["c1"]),
        _finding(impact_value=None, confidence_band="low",
                 confidence={"band": "low"}, claim_ids=["c2"]),
    ])
    assert "Not ranked by reach" in html
    assert "same confidence band" not in html


def test_the_overflow_line_does_not_invent_a_reach_ranking():
    """The overflow paragraph called the remainder "ranked lower by reach"
    unconditionally — directly under a lede that, on an all-unsized run, had
    just said nothing here could be sized at all."""
    findings = [
        _finding(impact_value=None, claim_ids=[f"c{i}"]) for i in range(200)
    ]
    html = render_report_html(_run(), findings)
    assert "The next" in html, "expected the overflow paragraph to render"
    assert "rank lower by reach" not in html
    assert "not by size, which nothing here had" in html


def test_one_weakest_link_shared_by_all_is_stated_once():
    """A corpus with no outcome evidence anywhere gives EVERY finding the same
    weakest link. Printing it on all 32 rows reads as 32 separate judgements
    about 32 different themes when it is one fact about the corpus — and a
    reader who skims an identical sentence three times stops seeing the section
    at all, which is how a genuine per-finding difference goes unnoticed."""
    same = {"band": "medium", "weakest_leg_reason": "no outcome evidence exists"}
    html = render_report_html(_run(), [
        _finding(confidence=same, claim_ids=["c1"]),
        _finding(confidence=same, claim_ids=["c2"]),
        _finding(confidence=same, claim_ids=["c3"]),
    ])
    assert "Every finding below has the same weakest link" in html
    # Once in the lede, and NOT again on any row.
    assert html.count("no outcome evidence exists") == 1
    assert "Weakest link." not in html


def test_the_cap_joins_the_weakest_link_as_a_clause():
    """`cap_reason` arrives uncapitalised, so a full stop before it rendered
    "…the diagnosis are not. capped at medium" — shipped once and caught only
    by reading the rendered panel."""
    same = {"band": "medium", "weakest_leg_reason": "no outcome evidence exists",
            "cap_reason": "capped at medium: no outcome evidence in the corpus"}
    html = render_report_html(_run(), [
        _finding(confidence=same, claim_ids=["c1"]),
        _finding(confidence=same, claim_ids=["c2"]),
    ])
    assert "exists; capped at medium" in html
    assert not re.search(r"\.\s*capped at medium", html)


def test_two_different_weakest_links_stay_on_their_own_rows():
    """The control, and the reason this is detected rather than assumed: the
    moment two findings differ, the sentence is about the finding again and
    belongs beside it."""
    html = render_report_html(_run(), [
        _finding(confidence={"band": "medium",
                             "weakest_leg_reason": "no outcome evidence"},
                 claim_ids=["c1"]),
        _finding(confidence={"band": "low",
                             "weakest_leg_reason": "one account carries it"},
                 claim_ids=["c2"]),
    ])
    assert "Every finding below has the same weakest link" not in html
    assert html.count("Weakest link.") == 2
    assert "no outcome evidence" in html
    assert "one account carries it" in html


def test_a_single_finding_keeps_its_weakest_link_on_the_row():
    """One finding is not a corpus-wide pattern. Hoisting a lone sentence into
    a "every finding below" lede would be a claim about a set of one."""
    html = render_report_html(_run(), [
        _finding(confidence={"band": "medium",
                             "weakest_leg_reason": "no outcome evidence"},
                 claim_ids=["c1"]),
    ])
    assert "Every finding below" not in html
    assert "Weakest link." in html


def test_a_ledger_that_died_for_one_reason_says_so_once():
    """One group is the degenerate case of grouping: the reason belongs to the
    group heading, not to each of the four rows under it."""
    ledger = [
        {"id": i, "label": f"candidate {i}",
         "reason": "no source that may speak to this claim type reported it",
         "stopped_at_stage": "verification"}
        for i in range(4)
    ]
    html = render_report_html(_run(), [_finding()], ledger)
    assert "every one of them died for the same one" in html
    assert html.count(
        "no source that may speak to this claim type reported it") == 1
    assert "<strong>4</strong> died because" in html
    for i in range(4):
        assert f"candidate {i}" in html


def test_a_ledger_groups_by_reason_biggest_cause_first():
    """THE SHAPE OF THE ANSWER. A real run rejected 102 candidates for five
    different reasons — 49 one way, 47 another — and the flat list repeated
    each reason beside each label, so a reader could not see that half the
    ledger died one way and half another without counting by hand."""
    ledger = (
        [{"id": i, "label": f"a{i}", "reason": "no authoritative source",
          "stopped_at_stage": "verification"} for i in range(3)]
        + [{"id": 90 + i, "label": f"b{i}", "reason": "only 1 supporting claim",
            "stopped_at_stage": "clustering"} for i in range(5)]
    )
    html = render_report_html(_run(), [_finding()], ledger)
    # Each reason appears ONCE, as a heading over its own group.
    assert html.count("no authoritative source") == 1
    assert html.count("only 1 supporting claim") == 1
    assert "<strong>5</strong> died because only 1 supporting claim" in html
    assert "<strong>3</strong> died because no authoritative source" in html
    # Biggest cause first: the 5 come before the 3.
    assert html.index("only 1 supporting claim") < html.index("no authoritative source")
    # And every candidate is still named, under its own cause.
    for lbl in ["a0", "a1", "a2", "b0", "b4"]:
        assert lbl in html


def test_the_grouped_rejection_reason_is_still_clipped():
    """Grouping must not drop a bound the per-row render was carrying.
    `reason` is tenant text of any length and this is the section with the
    hard body budget."""
    huge = "z" * 5_000
    ledger = [
        {"id": i, "label": f"c{i}", "reason": huge, "stopped_at_stage": "verification"}
        for i in range(3)
    ]
    html = render_report_html(_run(), [_finding()], ledger)
    assert "died because" in html
    longest = max((len(m) for m in re.findall(r"z+", html)), default=0)
    assert longest <= MAX_LEDGER_REASON_CHARS, (
        f"grouped reason rendered {longest} chars, cap is {MAX_LEDGER_REASON_CHARS}"
    )


def test_a_ledger_with_different_reasons_keeps_them_per_row():
    ledger = [
        {"id": 1, "label": "alpha", "reason": "one account only",
         "stopped_at_stage": "verification"},
        {"id": 2, "label": "beta", "reason": "one conversation echoing",
         "stopped_at_stage": "verification"},
    ]
    html = render_report_html(_run(), [_finding()], ledger)
    assert "every one of them died for the same one" not in html
    assert "grouped below by that reason" in html
    assert "one account only" in html
    assert "one conversation echoing" in html


def test_two_conflicts_are_not_described_as_ordered_regardless_of_size():
    """The rule is "a conflict outranks everything that is not one", NOT "this
    row is first regardless of size". With two conflicts `_rank` orders them
    against EACH OTHER by size, so the row that surfaces is the largest
    conflict and size did decide which — and the old wording told the reader
    the ordering carried less information than it actually does."""
    html = render_report_html(_run(), [
        _finding(adjudication="conflict", impact_value=900, claim_ids=["c1"]),
        _finding(adjudication="conflict", impact_value=3, claim_ids=["c2"]),
    ])
    assert "regardless of size" not in html
    assert "placed above every finding that is not one" in html


def test_a_conflict_placed_first_is_not_called_the_largest_either():
    """`_rank`'s DOMINANT term is `0 if conflict else 1` — an authoritative
    disagreement outranks every finding that is not one. The first version of
    this fix knew only about the size term, so a conflict-led run announced its
    top row as the largest thing found with a bigger finding directly beneath
    it."""
    html = render_report_html(_run(), [
        _finding(adjudication="conflict", impact_value=3, claim_ids=["c1"]),
        _finding(adjudication="corroborated", impact_value=900, claim_ids=["c2"]),
    ])
    assert "largest thing this reading found" not in html
    assert "placed first because two sources" in html
    assert "placed above every finding that is not one" in html


def test_an_unsized_top_above_sized_findings_does_not_deny_the_sizes():
    """The unsized sentence quantifies over EVERY finding, so gating it on the
    top row's own value made a document say "nothing here could be sized" with
    412 accounts rendered on the row below."""
    html = render_report_html(_run(), [
        _finding(adjudication="conflict", impact_value=None, claim_ids=["c1"]),
        _finding(adjudication="corroborated", impact_value=412, claim_ids=["c2"]),
    ])
    assert "Nothing in this reading could be sized" not in html
    assert "not ordered by size at all" not in html


def test_the_findings_heading_agrees_with_the_headline():
    """One document read "not ordered by size at all" and, two lines later,
    "Ranked by reach — how many accounts each theme touches"."""
    unsized = render_report_html(_run(), [_finding(impact_value=None)])
    assert "Ranked by reach" not in unsized
    assert "Not ranked by reach" in unsized
    sized = render_report_html(_run(), [_finding(impact_value=7)])
    assert "Ranked by reach" in sized


def test_the_relevance_disclosure_does_not_also_claim_completeness():
    """The relevance half is true; "every theme is listed" is not — findings
    are capped, and anecdote/ungroupable/refuted candidates never reach the
    list. Bundling them would let the false half discredit the true one."""
    html = render_report_html(_run(), [_finding(impact_value=None)])
    assert "not selected for your goal" in html
    assert "Every theme in the sources you approved is listed" not in html


# ─── Said once: the report stops repeating itself ───────────────────────────
#
# Apurva, on a real report: "poorly formatted (not human readable), lots of
# irrelevant information and it did not answer the question." Two of the
# repetitions were mechanical and are pinned here.


def test_an_assumption_every_finding_makes_is_stated_once_not_on_each():
    """I8 says disclose the assumption where the number is read. It does not
    say disclose it 279 times.

    On a corpus with no revenue connected EVERY finding carries the identical
    line — "value_per_account: no revenue data connected; accounts weighted
    equally" — and a real report printed it on all 279 findings. That is not
    disclosure; it is the noise a reader has to look past to find the
    assumptions that ARE per-finding."""
    shared = [{"name": "value_per_account",
               "basis": "no revenue data connected; accounts weighted equally"}]
    findings = [_finding(statement=f"finding {i}", assumed_params=list(shared))
                for i in range(6)]
    html = render_report_html(_run(), findings)

    # Stated — the disclosure is not lost.
    assert "value_per_account" in html
    # Once.
    assert html.count("value_per_account") == 1
    assert "same assumption" in html


def test_assumptions_that_differ_stay_on_their_own_findings():
    """The moment two findings assume different things, the hoist is wrong: it
    would attribute one finding's assumption to every other. Only a single
    distinct value across more than one finding is a statement about the
    corpus."""
    findings = [
        _finding(statement="a", assumed_params=[{"name": "seats", "basis": "median"}]),
        _finding(statement="b", assumed_params=[{"name": "seats", "basis": "mean"}]),
    ]
    html = render_report_html(_run(), findings)

    assert "same assumption" not in html
    # Both bases survive, each on its own finding.
    assert "median" in html and "mean" in html


def test_a_lone_finding_keeps_its_assumption_on_itself():
    """Hoisting out of a single finding moves the disclosure AWAY from the
    number it qualifies for no saving at all."""
    html = render_report_html(_run(), [_finding()])
    assert "same assumption" not in html
    assert "cohort median" in html


def test_the_unsized_caveat_is_not_stated_twice_in_adjacent_paragraphs():
    """The headline and the findings lede both disclosed how many findings had
    no size, and both explained that a missing size is not a small one — three
    lines apart, in a real report:

        …257 of these could not be sized at all, and a missing size is not a
        small one — so this is the largest known size…

        Ranked by reach …, and 257 of them could not be sized at all. An
        unsized theme sorts last without being small: its size is unknown…
    """
    findings = [_finding(impact_value=14), _finding(statement="b", impact_value=None)]
    html = render_report_html(_run(), findings)

    # Still disclosed.
    assert "could not be sized" in html
    # Once.
    assert html.count("could not be sized") == 1
    assert "is not a small" in html
    assert html.count("is not a small") == 1


def test_the_count_survives_when_the_headline_states_only_the_caveat():
    """THE CASE A BOOLEAN GOT WRONG, and it got it wrong by losing data.

    When the TOP finding is unsized but others below it are sized, the headline
    says "a missing size is not a small one" and never names how many. Treating
    that as "the headline covered it" suppressed the whole lede clause and
    dropped "N of them could not be sized" out of the document entirely — a
    de-duplication quietly turning into a deletion.

    So the count is still made here, and the caveat — which WAS said above — is
    not repeated."""
    findings = [
        _finding(statement="top", impact_value=None),
        _finding(statement="b", impact_value=None),
        _finding(statement="c", impact_value=9),
    ]
    html = render_report_html(_run(), findings)

    # The count is here, because nothing above it said one.
    assert "2 of them could not be sized" in html
    # The caveat is not repeated: the headline made it.
    assert "its size is unknown, not zero" not in html
    assert html.count("is not a small") == 1


def test_the_unsized_fact_survives_when_the_headline_cannot_carry_it():
    """The branch where NOTHING could be sized. The headline says something
    else entirely, so this paragraph is the only place the fact can appear —
    silence here drops an honesty disclosure rather than de-duplicating one."""
    findings = [_finding(statement="a", impact_value=None),
                _finding(statement="b", impact_value=None)]
    html = render_report_html(_run(), findings)
    # The actual disclosure, in this branch's own words — and on each row.
    assert "Nothing in this reading could be sized" in html
    assert "nothing here could be sized" in html
    assert html.count("Could not be sized") == 2


def test_the_hoist_fires_when_only_the_sized_findings_carry_an_assumption():
    """THE SHAPE REAL DATA ACTUALLY HAS, and the reason the first version of
    this never fired.

    Asking whether EVERY finding carried the identical set sounded right and
    was useless: a live run had 326 findings of which 30 were sized and carried
    `value_per_account`, and 296 were unsized and carried nothing. An unsized
    finding has no size to qualify, so it has no assumption — that is not
    disagreement, and counting it as disagreement left the line repeated 30
    times on the page written to de-duplicate it."""
    sized = [_finding(statement=f"sized {i}", impact_value=3,
                      assumed_params=[{"name": "value_per_account",
                                       "basis": "no revenue data connected"}])
             for i in range(4)]
    unsized = [_finding(statement=f"unsized {i}", impact_value=None,
                        assumed_params=[])
               for i in range(9)]
    html = render_report_html(_run(), sized + unsized)

    assert html.count("value_per_account") == 1
    # AND SAYS HOW MANY IT SPEAKS FOR. "Every finding below" is false here.
    assert "4 of the findings below rest on the same assumption" in html
    assert "Every finding below rests on the same assumption" not in html


def test_it_still_says_every_when_it_really_is_every():
    findings = [_finding(statement=f"f{i}") for i in range(3)]
    html = render_report_html(_run(), findings)
    assert "Every finding below rests on the same assumption" in html


def test_one_finding_with_an_assumption_is_not_a_corpus_statement():
    """A single carrier is not a pattern, and hoisting moves its disclosure
    away from the number it qualifies for no saving at all."""
    findings = [
        _finding(statement="a", assumed_params=[{"name": "seats", "basis": "median"}]),
        _finding(statement="b", assumed_params=[]),
        _finding(statement="c", assumed_params=[]),
    ]
    html = render_report_html(_run(), findings)
    assert "same assumption" not in html
    assert html.count("seats") == 1


# ─── The finding is a card, not a sentence ──────────────────────────────────


def _as_meta(run: dict) -> dict:
    """The run's existing prioritisation, so a test adding extras keeps the plan."""
    return dict(run.get("prioritisation") or {})


def _findings_html(html: str) -> str:
    """Everything from the findings heading on.

    The document has `<h3>` section headings of its own above this point, so a
    test that reads "the first h3" reads the wrong one.
    """
    i = html.find("What the evidence says")
    assert i != -1, "no findings section"
    return html[i:]


def test_the_heading_is_the_theme_and_the_counts_are_not_repeated_in_it():
    """Apurva: make the document "display data in a more beautiful manner, so
    that the user is able to understand the wins".

    The heading used to be the whole sentence — "25 claims across 2 accounts
    concern “AI tabletop exercise generation” — for example, …" — so the one
    word a reader scans for sat mid-clause, in quotes, behind two numbers that
    the chips on the very next line repeat verbatim."""
    html = render_report_html(_run(), [_finding(
        statement='25 claims across 2 accounts concern “AI tabletop generation” — for example, “x”.',
        label="AI tabletop generation",
        example="Northwind tailors scenarios by role and complexity",
    )])
    head = _findings_html(html).split("<h3>")[1].split("</h3>")[0]
    assert head == "1. AI tabletop generation"
    # The counts live in the chips, once.
    assert "claims across" not in head
    # The quote is set as a quote rather than trailing the heading.
    assert "<blockquote>“Northwind tailors scenarios by role and complexity”" in html


def test_a_finding_stored_before_the_theme_existed_still_has_a_heading():
    """Every run stored before `label` shipped has none, and an empty heading
    would be a worse regression than the run-on it replaced."""
    html = render_report_html(_run(), [_finding(
        statement="9 claims across 4 accounts concern “export latency”.",
    )])
    head = _findings_html(html).split("<h3>")[1].split("</h3>")[0]
    assert "export latency" in head
    # And no empty quote block appears for it.
    assert "<blockquote>“”" not in html


def test_the_quote_is_not_shown_twice_when_the_sentence_is_the_heading():
    """With no label the sentence IS the heading, and it already contains the
    example — printing the quote underneath would say it twice."""
    html = render_report_html(_run(), [_finding(
        statement='4 claims concern “x” — for example, “the export times out”.',
        example="the export times out",
    )])
    # Scoped to the findings section: the headline above legitimately restates
    # the top finding's sentence, which is a summary, not a repetition.
    assert _findings_html(html).count("the export times out") == 1


# ─── The card leads with what to do ─────────────────────────────────────────


def test_the_recommendation_leads_the_card_and_carries_its_justification():
    """Apurva: "we should start with a recommendation on how to solve this,
    this is only the issues, no suggestion on how to solve"."""
    run = _run()
    run["prioritisation"] = {
        **_as_meta(run),
        "findings_extra_by_rank": [{
            "label": "export latency",
            "recommendation": {
                "action": "Route export tickets to the rendering on-call team",
                "because": "three accounts named export in a renewal call",
            },
        }],
    }
    html = render_report_html(run, [_finding()])
    body = _findings_html(html)

    assert "Recommended." in body
    assert "Route export tickets to the rendering on-call team" in body
    assert "three accounts named export in a renewal call" in body
    # It leads: the suggestion is above the counts, not a footnote under them.
    assert body.index("Recommended.") < body.index("medium confidence")


def test_a_finding_with_no_recommendation_renders_exactly_as_before():
    """Only the top findings get one, and a suggestion that failed a check was
    dropped rather than repaired. Absent is the normal case, not an error."""
    html = render_report_html(_run(), [_finding()])
    body = _findings_html(html)
    assert "Recommended." not in body
    assert "Why." not in body
    # And the finding is still fully rendered.
    assert "medium confidence" in body


def test_half_a_recommendation_is_not_rendered():
    """An action with no justification is the thing this feature replaces."""
    run = _run()
    run["prioritisation"] = {
        **_as_meta(run),
        "findings_extra_by_rank": [
            {"recommendation": {"action": "Do the thing", "because": ""}},
        ],
    }
    html = render_report_html(run, [_finding()])
    assert "Do the thing" not in html


def test_extras_are_ignored_when_they_do_not_line_up_with_the_findings():
    """The merge is positional. A length mismatch means the two lists are not
    the same sequence, and attaching one finding's recommendation to another is
    far worse than showing none."""
    run = _run()
    run["prioritisation"] = {
        **_as_meta(run),
        "findings_extra_by_rank": [
            {"recommendation": {"action": "A", "because": "b"}},
        ],
    }
    html = render_report_html(run, [_finding(), _finding(statement="second")])
    assert "Recommended." not in html


# ─── The goal-relevance gate, in the document ───────────────────────────────


def _with_aside(run: dict, reasons: list) -> dict:
    run = dict(run)
    run["prioritisation"] = {**dict(run.get("prioritisation") or {}),
                             "set_aside_by_rank": reasons}
    return run


def test_a_set_aside_finding_leaves_the_main_list_and_keeps_its_reason():
    """Apurva ruled for a goal-relevance gate: a run for "grow revenue by 5%"
    led with three descriptions of the company's own product, because the order
    is how many accounts mentioned a theme."""
    findings = [_finding(statement="a", label="export latency"),
                _finding(statement="b", label="our own platform features")]
    html = render_report_html(
        _with_aside(_run(), [None, "describes our own product, not a problem"]),
        findings)

    body = _findings_html(html)
    assert "export latency" in body
    # It moved, and it took its reason with it.
    assert "Considered and set aside" in html
    assert "describes our own product, not a problem" in html


def test_the_funnel_is_stated_before_the_findings():
    """The first thing a filtered list owes its reader. A filtered list that
    does not say it was filtered is the more confident-looking of the two, and
    the less honest."""
    findings = [_finding(statement=f"f{i}", label=f"theme {i}") for i in range(4)]
    html = render_report_html(
        _with_aside(_run(), [None, "off-topic", "off-topic", None]), findings)

    assert "4 themes were found. 2 bear on this goal." in html
    assert html.index("bear on this goal") < html.index("What the evidence says")


def test_no_funnel_when_nothing_was_set_aside():
    """A funnel with one step is not a funnel, and "4 of 4" is noise."""
    findings = [_finding(statement=f"f{i}") for i in range(4)]
    html = render_report_html(_with_aside(_run(), [None] * 4), findings)
    assert "bear on this goal" not in html
    assert "Considered and set aside" not in html


def test_a_length_mismatch_sets_nothing_aside():
    """The split is positional. Setting aside the WRONG finding is far worse
    than setting none aside."""
    findings = [_finding(statement="a"), _finding(statement="b")]
    html = render_report_html(_with_aside(_run(), ["off-topic"]), findings)
    assert "Considered and set aside" not in html


def test_the_headline_describes_the_kept_findings_not_all_of_them():
    """The summary must agree with the list under it. A headline computed over
    findings the reader cannot see would name a theme that appears nowhere."""
    # DISTINCT STATEMENTS, because the headline renders the STATEMENT — an
    # earlier version of this test asserted on the label and passed against its
    # own mutation, which is a test that checks nothing.
    findings = [
        _finding(statement="our platform supports scenario building",
                 label="ours", impact_value=99),
        _finding(statement="renewals stall on the parts flow",
                 label="theirs", impact_value=2),
    ]
    html = render_report_html(
        _with_aside(_run(), ["describes our own product", None]), findings)

    head = html[html.index("The short version"):html.index("What the evidence says")]
    assert "renewals stall on the parts flow" in head
    assert "our platform supports scenario building" not in head


# ─── RICE: the ranking, and the arithmetic behind it ────────────────────────


def _rice_run(framework: str = "RICE") -> dict:
    run = _run()
    run["prioritisation"] = {"plan": {"framework": framework,
                                      "definition_text": "d", "sources": []}}
    return run


def _rice_html(html: str) -> str:
    i = html.index("How this was ranked")
    return html[i:html.index("The short version", i)]


def test_the_table_shows_every_term_and_marks_the_one_it_cannot_fill():
    """The skill's output spec: a "how we scored it" table so the ranking is
    reviewable, never a black box, with every input marked real vs
    [ASSUMPTION]."""
    findings = [_finding(statement="a", label="blocked", impact_value=5,
                         claim_types=["constraint"])]
    body = _rice_html(render_report_html(_rice_run(), findings))

    for header in ("Reach", "Impact", "Confidence", "Effort", "Score", "Inputs"):
        assert header in body
    # NAMED IN THE CELL, not merely mentioned in the key above it. Asserting
    # the word appears anywhere passed against a mutation that filled the cell
    # with "1" and left the definitions untouched — a table that quietly
    # supplies the one number nothing supports.
    assert "<td>Unquantified</td>" in body
    assert "person-month" in body


def test_a_blocker_outranks_a_bigger_theme_that_only_describes():
    """THE POINT OF SCORING AT ALL. Reach alone put commentary above blocked
    revenue: a theme mentioned on eleven accounts outranked the one blocker in
    the list. Impact read from the claim type is what separates them."""
    findings = [
        _finding(statement="a", label="chatter", impact_value=11,
                 claim_types=["mechanism"]),
        _finding(statement="b", label="blocked", impact_value=5,
                 claim_types=["constraint"]),
    ]
    body = _rice_html(render_report_html(_rice_run(), findings))
    scores = [float(x) for x in re.findall(r"<td>(\d+\.\d)</td>", body)]
    assert len(scores) == 2
    assert scores[1] > scores[0], "the blocker must outscore the chatter"


def test_an_unsized_finding_scores_nothing_rather_than_zero():
    """I3 again, in the place it would be easiest to lose: a table cell."""
    findings = [_finding(statement="a", label="unsized", impact_value=None,
                         claim_types=["preference"])]
    body = _rice_html(render_report_html(_rice_run(), findings))
    assert "<td>—</td>" in body
    assert "<td>0.0</td>" not in body


def test_it_says_why_a_missing_effort_does_not_change_the_order():
    """An effort applied equally to every row is a common divisor. Saying so is
    the derived form of the reference memo's "cheapness is not the constraint
    here" — and it stops a reader discounting the whole table for a gap that
    cannot have moved it."""
    findings = [_finding(statement="a", label="x", impact_value=5,
                         claim_types=["constraint"]),
                _finding(statement="b", label="y", impact_value=None,
                         claim_types=["preference"])]
    body = _rice_html(render_report_html(_rice_run(), findings))
    assert "cannot change their order" in body


def test_no_framework_means_no_table():
    findings = [_finding(statement="a", label="x", impact_value=5)]
    html = render_report_html(_rice_run(framework=""), findings)
    assert "How this was ranked" not in html


def test_the_table_does_not_reorder_the_findings():
    """`_rank` froze the order before this ran. A scoring table that re-sorted
    would be the prioritisation step mutating the ranking, which is I10."""
    findings = [_finding(statement="a", label="first", impact_value=1,
                         claim_types=["mechanism"]),
                _finding(statement="b", label="second", impact_value=50,
                         claim_types=["constraint"])]
    body = _rice_html(render_report_html(_rice_run(), findings))
    assert body.index("first") < body.index("second")


def test_the_table_says_when_it_stopped_short():
    """NO SILENT CAPS. A table that stops at ten without saying so reads as the
    whole ranking — the rule this file applies everywhere else."""
    findings = [_finding(statement=f"f{i}", label=f"t{i}", impact_value=i + 1,
                         claim_types=["mechanism"]) for i in range(14)]
    body = _rice_html(render_report_html(_rice_run(), findings))
    assert "4 findings below these" in body


# ─── The body is a memo, not a dump ─────────────────────────────────────────
#
# A real run produced 549 findings that bore on the goal and rendered 150 of
# them in full — inside every size budget, and far past what anyone reads.


def _many(n: int) -> list[dict]:
    """n findings, descending reach, so rank order is unambiguous."""
    return [
        _finding(statement=f"Theme {i} stalls the renewal",
                 label=f"Theme {i}", impact_value=n - i)
        for i in range(n)
    ]


def _full_blocks(html: str) -> int:
    """Full write-ups are the numbered <h3> cards `_finding_block` emits."""
    return len(re.findall(r"<h3>\d+\.\s", html))


def test_only_the_ranked_findings_get_a_full_write_up():
    html = render_report_html(_run(), _many(60), [])
    assert _full_blocks(html) == MAX_DETAILED_FINDINGS


def test_the_detailed_cap_agrees_with_the_table_that_ranked_them():
    # If these two drift, the document shows ten rows in RICE and a different
    # number of write-ups, and the reader cannot tell which set "mattered".
    assert MAX_DETAILED_FINDINGS == MAX_RICE_ROWS


def test_the_findings_past_the_cap_are_still_listed_and_counted():
    html = render_report_html(_run(), _many(60), [])
    # every one of the 50 beyond the cap is present as a one-line row
    assert html.count("<li>") >= 60 - MAX_DETAILED_FINDINGS
    assert "The next 50 findings are listed below" in html
    # and the heading still tells the truth about the total
    assert "What the evidence says (60)" in html


def test_the_overflow_does_not_blame_a_size_limit_it_did_not_hit():
    # 60 findings fits every budget: the reason the rest are one-liners is the
    # editorial cap, not size. Saying "size limit" here would be a lie.
    html = render_report_html(_run(), _many(60), [])
    para = html[html.find("The next 50 findings"):][:240]
    assert "size limit" not in para
    assert "the ranking put first" in para


def test_no_shed_rung_can_raise_the_number_of_write_ups():
    """A document big enough to MISS rung 0 must not come back with more
    write-ups than it started with.

    The ladder raises full_cap again on its lower rungs (100, 50, 20) so that
    overflow rows are shed before detail is. Passing a rung straight through
    would take a run capped at ten and re-render it with a hundred. Rendered,
    not reasoned about: an assertion on the constants alone is true by
    arithmetic and would pass against the bug."""
    findings = [
        _finding(statement="renewals stall on the parts flow " * 60,
                 label=f"Theme {i} " * 12, impact_value=2_000 - i)
        for i in range(2_000)
    ]
    html = render_report_html(_run(), findings, [])
    assert _full_blocks(html) <= MAX_DETAILED_FINDINGS


def test_a_run_smaller_than_the_cap_is_untouched():
    html = render_report_html(_run(), _many(3), [])
    assert _full_blocks(html) == 3
    assert "listed below in rank order" not in html
