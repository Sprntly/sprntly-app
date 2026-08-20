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

from app.crucible.report import (
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
    from app.crucible.report import MAX_FULL_FINDING_BLOCKS

    n = MAX_FULL_FINDING_BLOCKS + 40
    html = render_report_html(
        {"id": 1, "goal_text": "g", "coverage_notes": []},
        _many_findings(n), [], {},
    )
    assert "The remaining 40 findings" in html
    assert "nothing has been dropped" in html
    # The last one is still named, in rank order.
    assert f"{n}." in html


def test_a_small_run_is_not_truncated_at_all():
    """The control: the cap must not touch an ordinary run."""
    html = render_report_html(
        {"id": 1, "goal_text": "g", "coverage_notes": []},
        _many_findings(12), [], {},
    )
    assert "The remaining" not in html


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


def test_the_block_budget_is_arithmetic_not_a_measurement():
    """A calibrated constant holds until someone's data is shaped differently.
    The import-time assertion is what makes it a bound."""
    from app.crucible import report as r

    assert (r.MAX_FULL_FINDING_BLOCKS * r._MAX_FINDING_BLOCK_CHARS
            + r._OTHER_SECTIONS_BUDGET_CHARS) <= r._BODY_LIMIT
    assert r._MAX_FINDING_BLOCK_CHARS > r.MAX_STATEMENT_CHARS
