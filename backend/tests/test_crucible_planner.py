"""`app.crucible.planner` — composing the plan, and the gates on the draw.

TWO PATHS ARE TESTED AND THEY MATTER FOR DIFFERENT REASONS. The deterministic
plan is what runs offline and under test, so it is exercised constantly and
must be a real document rather than a failure state. The generated plan is
what a customer gets, and everything worth testing about it is a GATE: that a
model cannot name an operation the engine will not run, cannot cite a figure
nobody measured, and cannot be asked the same question twice for the same run.
"""
from __future__ import annotations

import re

from tests import _tabular_recon_fixtures as fx

from app.crucible import planner, primitives as prim, recon

SOURCES = ("revenue", "analytics", "customer_voice")


def _report():
    return recon.observe(fx.full_pack(), expected_sources=SOURCES,
                         signals=fx.signals())


def _plan():
    return planner.minimal_plan(
        goal_text="grow revenue this year", currency="accounts",
        report=_report(), source_types=SOURCES,
    )


# ─── The deterministic plan is a real plan ─────────────────────────────────


def test_the_deterministic_plan_names_only_implemented_operations():
    """THE HONESTY INVARIANT, at the level a reader experiences it: every line
    of the plan is something the run actually does."""
    for step in _plan():
        p = prim.REGISTRY[step.primitive]
        assert p.is_implemented, f"{step.primitive} is declared, not implemented"


def test_the_deterministic_plan_validates_against_the_registry():
    report = _report()
    problems = prim.validate_steps(
        [s.to_json() for s in _plan()],
        available_sources=(
            [t.name for t in fx.full_pack()] + list(SOURCES)
            + [s.label for s in report.sources]
            + [o.source for o in report.observations]
            + [o.source_label for o in report.observations]
        ),
    )
    assert problems == ()


def test_the_steps_are_numbered_from_one_with_no_gaps_and_grouped_in_part_order():
    steps = _plan()
    assert [s.n for s in steps] == list(range(1, len(steps) + 1))
    seen: list[str] = []
    for s in steps:
        if not seen or seen[-1] != s.part:
            seen.append(s.part)
    assert seen == [p for p in planner.PARTS if p in seen]
    assert len(seen) == len(set(seen)), "a part must not appear twice"


def test_every_figure_in_the_deterministic_plan_traces_to_an_observation():
    """Written by hand, checked by the same gate the model's draw goes
    through. It caught a real one: interpolating the reader's goal text into a
    sentence put "15%" from a goal of "grow revenue 15%" into a plan step —
    a figure from nowhere, in a document whose whole claim is that its numbers
    come from the evidence."""
    report = _report()
    engine = list(planner._engine_figures().values()) + list(
        report.inventory_figures())
    for step in _plan():
        extra = engine + [
            float(v) for v in step.params.values()
            if isinstance(v, (int, float)) and not isinstance(v, bool)
        ]
        assert planner.untraceable_figures(
            f"{step.what} {step.why}", report.observations, extra=extra,
        ) == (), f"step {step.n} ({step.primitive}) states an unbacked figure"


def test_the_plan_reflects_what_the_reconnaissance_pass_actually_found():
    named = {s.primitive for s in _plan()}
    assert "reconcile_value_columns" in named
    assert "compare_measures_across_groups" in named
    assert "audit_field_coverage" in named
    assert "check_stage_collapse" in named


def test_a_run_with_no_reconnaissance_still_gets_a_plan_and_no_numbers():
    """Every entry point that has not run the pass — and every plan stored
    before it existed. The document is shorter, not broken."""
    steps = planner.minimal_plan(
        goal_text="grow revenue", currency="accounts", source_types=SOURCES)
    assert steps
    named = {s.primitive for s in steps}
    assert "reconcile_value_columns" not in named
    for s in steps:
        assert planner.untraceable_figures(
            f"{s.what} {s.why}", (),
            extra=list(planner._engine_figures().values()),
        ) == ()


def test_one_step_per_observation_kind_however_many_the_pass_found():
    """The pass legitimately finds the same divergence against three
    activity sources. Three consecutive steps saying the same thing is how a
    plan stops being read."""
    report = _report()
    doubled = recon.ReconReport(
        observations=report.observations + report.observations,
        sources=report.sources,
    )
    steps = planner.minimal_plan(
        goal_text="g", currency="accounts", report=doubled,
        source_types=SOURCES)
    named = [s.primitive for s in steps]
    assert named.count("compare_measures_across_groups") == 1
    assert named.count("reconcile_value_columns") == 1


# ─── The figure gate ───────────────────────────────────────────────────────


def test_a_number_nobody_measured_is_caught():
    report = _report()
    assert planner.untraceable_figures(
        "This affects 41% of your accounts.", report.observations)


def test_a_number_the_pass_measured_is_accepted_however_it_is_written():
    """One figure appears three ways in real prose — as itself, rounded, and
    as a percentage of a share — and a gate that only matched one of them
    would delete correct sentences."""
    report = _report()
    obs = [o for o in report.observations if o.kind == "value_columns_disagree"]
    assert planner.untraceable_figures("understates it by 273,378", obs) == ()
    assert planner.untraceable_figures("understates it by $273,378", obs) == ()
    assert planner.untraceable_figures("by 11.9% of the book", obs) == ()


def test_a_step_citing_an_observation_this_run_never_made_is_dropped():
    report = _report()
    step = planner.PlanStep(
        n=1, part=planner.PARTS[0], primitive="score_impact",
        what="Size each theme", why="Because size matters.",
        observations=("contracts:value_columns:invented",),
    )
    kept, dropped = planner.verify([step], report.observations)
    assert kept == []
    assert "did not make" in dropped[0]


def test_a_step_naming_a_declared_primitive_is_dropped():
    report = _report()
    step = planner.PlanStep(
        n=1, part=planner.PARTS[1], primitive="decompose_by_cohort",
        params={"source": "contracts", "cohort_field": "contract_start",
                "measure_field": "total_acv_usd"},
        what="Break it down by cohort", why="Because cohorts.",
    )
    kept, dropped = planner.verify([step], report.observations)
    assert kept == []
    assert "declared but not implemented" in dropped[0]


def test_surviving_steps_are_renumbered_so_the_reader_sees_no_gap():
    report = _report()
    good = planner.PlanStep(n=1, part=planner.PARTS[4], primitive="score_impact",
                            what="a", why="b")
    bad = planner.PlanStep(n=2, part=planner.PARTS[4], primitive="not_a_thing",
                           what="c", why="d")
    also = planner.PlanStep(n=3, part=planner.PARTS[4], primitive="rank_findings",
                            what="e", why="f")
    kept, _ = planner.verify([good, bad, also], report.observations)
    assert [s.n for s in kept] == [1, 2]


# ─── Drawn once ────────────────────────────────────────────────────────────


def test_a_plan_already_drawn_for_this_run_is_read_back_never_re_drawn(monkeypatch):
    """A model call is a draw, not a lookup, and the reader has APPROVED one
    of the samples. Re-composing on a later render would hand them a document
    that is not the one they said yes to."""
    stored = [{"n": 1, "part": planner.PARTS[0], "primitive": "score_impact",
               "what": "stored", "why": "stored"}]
    monkeypatch.setattr(planner, "_offline", lambda: False)

    def _explode(*_a, **_k):
        raise AssertionError("a stored plan must not reach the model")

    monkeypatch.setattr("app.graph.gateway.llm_call", _explode)
    steps = planner.build_steps(
        enterprise_id="e", goal_text="g", report=_report(),
        run_meta={"plan": {"steps": stored}},
    )
    assert [s.what for s in steps] == ["stored"]


def test_an_empty_stored_plan_is_an_answer_and_not_an_absence():
    """`None` and `()` are different: a planner that ran and produced nothing
    must not be asked again, or the run quietly takes a second sample."""
    assert planner.load_steps({}) is None
    assert planner.load_steps({"plan": {}}) is None
    assert planner.load_steps({"plan": {"steps": []}}) == ()


def test_offline_takes_the_deterministic_plan():
    steps = planner.build_steps(
        enterprise_id="e", goal_text="g", report=_report(),
        source_types=SOURCES)
    assert steps
    assert all(prim.REGISTRY[s.primitive].is_implemented for s in steps)


# ─── The generated path ────────────────────────────────────────────────────


class _Result:
    def __init__(self, output):
        self.output = output


def _drawn(monkeypatch, payload):
    monkeypatch.setattr(planner, "_offline", lambda: False)
    calls: list[dict] = []

    def _fake(**kwargs):
        calls.append(kwargs)
        return _Result(payload)

    monkeypatch.setattr("app.graph.gateway.llm_call", _fake)
    return calls


def test_a_good_draw_is_kept_and_its_parameters_are_coerced(monkeypatch):
    """The schema can only say a parameter value is a string, so `top_n="3"`
    arrives as text and would be rejected for not being a whole number —
    correctly. Coercion happens against the registry's declared types."""
    payload = {"steps": [
        {"part": planner.PARTS[4], "primitive": "select_top_n",
         "params": [{"name": "top_n", "value": "3"}],
         "what": "Write up only a few", "why": "A long list is not a decision."},
    ]}
    _drawn(monkeypatch, payload)
    steps = planner.build_steps(enterprise_id="e", goal_text="g",
                                report=_report(), source_types=SOURCES)
    assert len(steps) == 1
    assert steps[0].params == {"top_n": 3}
    assert steps[0].what == "Write up only a few"


def test_a_draw_that_invents_a_figure_falls_back_rather_than_shipping(monkeypatch):
    payload = {"steps": [
        {"part": planner.PARTS[4], "primitive": "score_impact",
         "params": [], "what": "Size it",
         "why": "This reaches 63% of your accounts."},
    ]}
    _drawn(monkeypatch, payload)
    steps = planner.build_steps(enterprise_id="e", goal_text="g",
                                report=_report(), source_types=SOURCES)
    # Nothing survived the draw, so the deterministic plan is used — which is
    # a real document, not an error state.
    assert len(steps) > 1
    assert all(s.what != "Size it" for s in steps)


def test_a_failed_model_call_never_costs_the_run_its_plan(monkeypatch):
    monkeypatch.setattr(planner, "_offline", lambda: False)

    def _boom(**_k):
        raise RuntimeError("the model is down")

    monkeypatch.setattr("app.graph.gateway.llm_call", _boom)
    steps = planner.build_steps(enterprise_id="e", goal_text="g",
                                report=_report(), source_types=SOURCES)
    assert steps


def test_a_rejected_draw_is_retried_exactly_once(monkeypatch):
    """A second retry is a third sample, and choosing among samples by which
    one passed validation is a slower way of drawing until you like the
    answer."""
    payload = {"steps": [
        {"part": planner.PARTS[4], "primitive": "invented_operation",
         "params": [], "what": "a", "why": "b"},
    ]}
    calls = _drawn(monkeypatch, payload)
    planner.build_steps(enterprise_id="e", goal_text="g", report=_report(),
                        source_types=SOURCES)
    assert len(calls) == planner.MAX_REGENERATIONS + 1


def test_the_model_is_shown_only_operations_it_may_name(monkeypatch):
    payload = {"steps": []}
    calls = _drawn(monkeypatch, payload)
    planner.build_steps(enterprise_id="e", goal_text="g", report=_report(),
                        source_types=SOURCES)
    prompt = calls[0]["input"]
    for p in prim.declared_only():
        assert p.id not in prompt
    # …and every number it is allowed to use, with the id to cite it by.
    for o in _report().observations:
        assert o.id in prompt


def test_steps_round_trip_through_json():
    original = _plan()
    restored = planner.steps_from_json([s.to_json() for s in original])
    assert [s.primitive for s in restored] == [s.primitive for s in original]
    assert [s.why for s in restored] == [s.why for s in original]
    assert [s.params for s in restored] == [dict(s.params) for s in original]


# ─── The plan opens with a sentence, not an inventory ──────────────────────


def test_the_first_step_summarises_the_evidence_instead_of_listing_it():
    """It used to join every source it had into one line, which on a real
    upload rendered sixteen storage keys as the opening of a document whose
    whole purpose is to be read. Enumerating is not describing."""
    first = _plan()[0]
    assert first.primitive == "select_evidence"
    # Five sheets drawn from four workbooks: the sentence counts BOTH, because
    # several sheets of one workbook are one source to a reader and calling
    # them five would overstate what is connected.
    assert "5 tables across 4 sources" in first.why
    # The count is in the prose; the full list is on the parameters, where an
    # operation is being addressed rather than a person.
    assert first.params["sources"]


def test_no_storage_key_ever_reaches_a_readers_eye():
    """`03_product_analytics:activation_funnel` is the right handle for a join
    and the wrong thing to put in a document."""
    # A storage key is `word:word` with no space — an English colon in a
    # sentence is not one, and a check that cannot tell them apart would push
    # the prose into avoiding punctuation.
    key = re.compile(r"\S+:\S+")
    for step in _plan():
        for text in (step.what, step.why, *step.sources):
            assert not key.search(text), (
                f"step {step.n} shows a storage key: {text}")


def test_the_opening_sentence_names_a_few_sources_and_counts_the_rest():
    """A sentence naming four things is a list, and a reader skims a list."""
    why = _plan()[0].why
    assert "sales data" in why and "and 1 more" in why


def test_a_step_may_say_how_much_it_will_read_without_that_being_a_claim():
    """The run's own inventory counts are facts about the plan, not claims
    about the evidence — and the figure gate deleted the opening sentence for
    citing them until `inventory_figures` existed."""
    report = _report()
    first = _plan()[0]
    kept, dropped = planner.verify(
        [first], report.observations,
        inventory=report.inventory_figures(),
    )
    assert kept and not dropped


# ─── The two checks the follow-up added reach the plan ─────────────────────


def test_the_censoring_finding_becomes_a_step_with_both_figures():
    step = next(s for s in _plan() if s.primitive == "check_period_censoring")
    assert "46.7" in step.why and "93.3" in step.why
    assert step.part == planner.PARTS[1]


def test_the_evidence_mix_becomes_a_step_about_what_the_answer_rests_on():
    step = next(s for s in _plan()
                if s.primitive == "characterise_evidence_mix")
    assert "4.5%" in step.why


def test_both_new_operations_are_implemented_not_declared():
    for pid in ("check_period_censoring", "characterise_evidence_mix"):
        assert prim.REGISTRY[pid].is_implemented


# ─── The plan a PROSE tenant gets ─────────────────────────────────────────
#
# The table checks find nothing on a knowledge-graph corpus, so before the
# graph-side checks existed such a tenant got a plan with no measured fact in
# it at all. These cover what it says now, and the one thing it must stop
# saying.


def _prose_report(**kw):
    signals = fx.kg_signals(**kw)
    return recon.observe(fx.kg_tables(signals), signals=signals)


def _prose_plan(**kw):
    report = _prose_report(**kw)
    return report, planner.minimal_plan(
        goal_text="grow revenue", currency="accounts", report=report,
        source_types=("pm_manual", "customer_voice"))


def test_a_prose_corpus_gets_a_plan_with_measured_facts_in_it():
    report, steps = _prose_plan()
    named = {s.primitive for s in steps}
    assert "audit_signal_field_coverage" in named
    assert "check_dating_reliability" in named
    assert "check_source_concentration" in named
    assert "characterise_claim_mix" in named
    assert len(report.observations) >= 5


def test_the_plan_says_it_will_count_rather_than_weight_and_why():
    """THE GRACEFUL-DEGRADATION RULE, STATED OUT LOUD. The engine already
    counts rather than weighting by revenue, on every corpus, silently — and a
    reader has no way to tell a considered count from a weighting that quietly
    failed.

    THE WORDING MOVED AND THE RULE DID NOT. This used to pin the literal
    phrase "count accounts instead of weighting", which the step said on every
    run that saw the attribution gap — including a weighted one, because the
    gap does not decide the unit. The sentence is now read off the settled
    unit, so this asserts the CLAIM at the unit this fixture actually runs at
    rather than the string it happened to use, and the weighted branch is
    covered by its own test below.
    """
    _, steps = _prose_plan()
    step = next(s for s in steps
                if s.params.get("field") == "properties.account")
    assert "counts accounts rather than weighting them" in step.why
    assert "not what they are worth" in step.why
    assert "0.0%" in step.why or "0 of 200" in step.why


def test_the_attribution_gap_step_does_not_claim_a_count_on_a_weighted_run():
    """THE BRANCH THE OLD WORDING GOT WRONG.

    `account_attribution_gap` measures signals naming any account;
    `weighting_verdict` reads `priceable_coverage`, which is priced accounts
    over accounts NAMED in the evidence. Different denominators, moving
    independently — so a transcript-heavy tenant with a contracts export could
    have most signals unattributed and every named account priceable, and got
    a step promising a count directly above the step that weighs.
    """
    report = _prose_report()
    steps = planner.minimal_plan(
        goal_text="grow revenue", currency="accounts", report=report,
        source_types=("pm_manual", "customer_voice"),
        weighting_unit="value",
        weighting_because="themes are ranked by the revenue behind them.",
    )
    step = next(s for s in steps
                if s.params.get("field") == "properties.account")
    assert "counts accounts rather than weighting" not in step.why, (
        f"the step still promises a count on a weighted run: {step.why}"
    )
    assert "revenue a theme is ranked by" in step.why
    assert "0.0%" in step.why or "0 of 200" in step.why, (
        "the measurement must survive the correction or this is vacuous"
    )


def test_the_plan_never_promises_the_echo_rule_a_prose_run_switches_off():
    """`pipeline._refute` skips it on a corpus dated by the ingest clock,
    because over those dates every cluster looks like one conversation. The
    plan listed it anyway, so the document said the rule was off in one step
    and promised it four steps later — describing work that does not happen."""
    _, steps = _prose_plan()
    named = [s.primitive for s in steps]
    assert "check_dating_reliability" in named
    assert "refute_echo" not in named


def test_it_still_promises_the_echo_rule_where_the_dates_are_real():
    _, steps = _prose_plan(ingest_clock=False)
    named = [s.primitive for s in steps]
    assert "check_dating_reliability" not in named
    assert "refute_echo" in named


def test_weighting_by_revenue_is_never_emitted_on_a_counted_run():
    """It is what a reader assumes is happening when a plan calls a theme
    "big", and it is now something the engine CAN do — which makes the
    condition, not the capability, the thing that has to be enforced.

    `pipeline.build_findings` performs it only when the run's stored verdict
    says `value`. A plan built without that verdict must not name it, and the
    check is not "the model was told not to": the operation is withheld from
    the catalogue AND rejected by `validate_steps`, because a model handed an
    operation writes a step for it."""
    assert prim.REGISTRY["weight_by_account_value"].is_implemented
    _, steps = _prose_plan(attributed=True)
    assert "weight_by_account_value" not in {s.primitive for s in steps}
    assert prim.validate_steps(
        [{"primitive": "weight_by_account_value", "params": {}}]) != ()


def test_the_unit_step_states_whichever_unit_the_run_actually_settled_on():
    """SITE ONE OF THE OVERCLAIM, GENERALISED. The plan may only promise what
    the engine performs, and the engine now performs two different things —
    so the step is emitted from the stored verdict on the deterministic path,
    never composed by the model, and there is exactly one sentence for each
    outcome."""
    import tests._tabular_recon_fixtures as tfx

    signals = [
        {"id": f"s{i}", "kind": "sentiment", "source_type": "customer_voice",
         "content": "an assertion", "valid_at": "2026-08-01T12:00:00+00:00",
         "properties": {"account": "Account B"}}
        for i in range(10)
    ]
    report = recon.observe([tfx.contracts()], signals=signals)
    assert report.of_kind("priceable_coverage"), "fixture must reach the gate"

    counted = planner.minimal_plan(
        goal_text="grow revenue", currency="accounts", report=report,
        source_types=("customer_voice",), weighting_unit="count",
        weighting_because="Counted because you have not said how you sell.")
    named = {s.primitive for s in counted}
    assert "weight_by_account_value" not in named
    unit = next(s for s in counted if s.primitive == "set_counting_unit")
    assert "never money" in unit.why

    weighted = planner.minimal_plan(
        goal_text="grow revenue", currency="accounts", report=report,
        source_types=("customer_voice",), weighting_unit="value",
        weighting_because="100.0% of what I read names a priced account.")
    step = next(s for s in weighted
                if s.primitive == "weight_by_account_value")
    assert "contracted value of the accounts" in step.why
    assert "listed separately" in step.why, (
        "a weighted plan must say what happens to the themes it cannot price")


def test_every_figure_in_a_prose_plan_traces_to_an_observation():
    report, steps = _prose_plan()
    engine = list(planner._engine_figures().values()) + list(
        report.inventory_figures())
    for step in steps:
        extra = engine + [
            float(v) for v in step.params.values()
            if isinstance(v, (int, float)) and not isinstance(v, bool)
        ]
        assert planner.untraceable_figures(
            f"{step.what} {step.why}", report.observations, extra=extra,
        ) == (), f"step {step.n} ({step.primitive}) states an unbacked figure"


def test_a_prose_plan_validates_and_names_only_implemented_operations():
    report, steps = _prose_plan()
    for step in steps:
        assert prim.REGISTRY[step.primitive].is_implemented
    assert planner.verify(
        steps, report.observations, inventory=report.inventory_figures(),
    )[1] == []


# ─── A step drawn from a measurement has to report it ─────────────────────
#
# THE GATE WAS ONE-DIRECTIONAL. It punished a step for citing a figure it could
# not support and nothing punished a step for withholding one it was handed, so
# a step derived from "4 of 1,275 signals name an account" could say "very few"
# and pass. Measured against the real model over a real run's six observations:
# eleven observation-backed steps, not one figure among them.
#
# The prompt is what stopped that happening — the gate runs after generation and
# the model never sees it. These are what stop it SHIPPING if the prompt drifts.


def _obs_step(primitive: str, why: str, obs_id: str,
              **params) -> planner.PlanStep:
    """A step that is valid in every OTHER respect, so the citation gate is the
    only thing that can reject it. Registry validation runs first, so a missing
    required parameter would fail these for the wrong reason."""
    return planner.PlanStep(
        n=1, part=planner.PARTS[0], primitive=primitive, params=params,
        what="Check something", why=why, observations=(obs_id,))


def test_a_step_that_hedges_where_it_has_the_number_is_dropped():
    report = _prose_report()
    o = report.of_kind("account_attribution_gap")[0]
    step = _obs_step(
        "audit_signal_field_coverage",
        "With very few signals naming an account, themes can only be counted.",
        o.id, field="properties.account")
    kept, dropped = planner.verify([step], report.observations)
    assert kept == []
    assert "quotes none of its figures" in dropped[0]


def test_the_same_step_passes_once_it_quotes_the_measurement():
    report = _prose_report()
    o = report.of_kind("account_attribution_gap")[0]
    step = _obs_step(
        "audit_signal_field_coverage",
        f"Only {o.figures['present']:,.0f} of {o.figures['signals']:,.0f} "
        f"signals name an account, so themes can only be counted.",
        o.id, field="properties.account")
    kept, dropped = planner.verify([step], report.observations)
    assert len(kept) == 1 and dropped == []


def test_a_figure_that_is_not_the_observation_s_does_not_count_as_citing_it():
    """Otherwise a step could satisfy the citation half by inventing a number
    that the traceability half would then reject — two gates cancelling out."""
    report = _prose_report()
    o = report.of_kind("account_attribution_gap")[0]
    assert planner.cites_a_figure("this reaches 63% of accounts", [o]) is False
    assert planner.cites_a_figure(
        f"{o.figures['signals']:,.0f} signals", [o]) is True


def test_a_spine_step_resting_on_no_observation_is_untouched():
    """Most of the method states a RULE, not a quantity. Requiring a figure of
    those would delete the refutation rules and the ranking."""
    report = _prose_report()
    step = planner.PlanStep(
        n=1, part=planner.PARTS[3], primitive="refute_anecdote",
        what="Drop anything resting on a single mention",
        why="One person saying something once is not a pattern.")
    kept, dropped = planner.verify([step], report.observations)
    assert len(kept) == 1 and dropped == []


def test_every_observation_backed_step_the_deterministic_plan_writes_cites_one():
    """The fallback has to satisfy the gate it is the floor for — otherwise a
    rejected draw falls through to a plan that is itself rejected."""
    report, steps = _prose_plan()
    backed = [s for s in steps if s.observations]
    assert backed, "this test is vacuous with no observation-backed steps"
    by_id = {o.id: o for o in report.observations}
    for step in backed:
        cited = [by_id[i] for i in step.observations]
        assert planner.cites_a_figure(f"{step.what} {step.why}", cited), (
            f"step {step.n} ({step.primitive}) rests on a measurement and "
            f"quotes none of it")


def test_the_prompt_asks_for_the_figure_rather_than_only_forbidding_others():
    """THE ACTUAL FIX. Naming figures only under "what you must not do", with a
    discard threat attached, produced 0 citations in 11 backed steps against
    the real model; requiring citation produced 15 in 15. The gate above cannot
    have caused either — it runs after generation."""
    assert "CITE THE NUMBER" in planner._SYSTEM
    assert "MUST quote at least one of that observation's figures" in planner._SYSTEM
    # And the hedges it has to name to rule out.
    for hedge in ('"very few"', '"a large share"'):
        assert hedge in planner._SYSTEM


# ─── The parameter the server knows and the model cannot ──────────────────


def test_a_draw_that_omits_the_sources_keeps_its_step():
    """A real draw left `sources` off `select_evidence` and the step was
    dropped, so the plan lost its opening move — the one that says what the
    answer is allowed to rest on. That parameter is not a creative choice: it
    is the run's own inventory, which the model can only copy and can get
    wrong."""
    step = planner.PlanStep(
        n=1, part=planner.PARTS[0], primitive="select_evidence",
        what="Fix which sources this may rest on", why="So a figure traces.")
    filled = planner.fill_known_params(
        [step], source_labels=["revenue data", "the tracker"])
    assert filled[0].params["sources"] == ["revenue data", "the tracker"]
    assert prim.validate_steps([filled[0].to_json()]) == ()


def test_a_parameter_the_model_did_supply_is_never_overwritten():
    step = planner.PlanStep(
        n=1, part=planner.PARTS[0], primitive="select_evidence",
        params={"sources": ["revenue data"]}, what="w", why="y")
    filled = planner.fill_known_params(step and [step], source_labels=["a", "b"])
    assert filled[0].params["sources"] == ["revenue data"]


def test_nothing_else_is_filled_in_for_the_model():
    """NOT A GENERAL LENIENCY. A missing parameter is normally the model
    inventing an operation it does not understand, and dropping that step is
    the right answer."""
    step = planner.PlanStep(
        n=1, part=planner.PARTS[1], primitive="reconcile_value_columns",
        what="w", why="y")
    filled = planner.fill_known_params([step], source_labels=["a"])
    assert filled[0].params == {}
    assert prim.validate_steps([filled[0].to_json()]) != ()


def test_with_no_inventory_to_fill_from_nothing_is_invented():
    step = planner.PlanStep(
        n=1, part=planner.PARTS[0], primitive="select_evidence",
        what="w", why="y")
    assert planner.fill_known_params([step], source_labels=[])[0].params == {}


# ─── The two-phase write ──────────────────────────────────────────────────


def test_a_pending_plan_is_not_treated_as_already_drawn():
    """The gate writes the deterministic method first so the reader sees
    something true in a few hundred milliseconds. Reading those placeholder
    steps back as "already drawn" would mean the composition never ran — the
    plan silently deterministic for ever, with nothing to show it happened."""
    pending = {"plan": {"steps": [{"n": 1, "primitive": "score_impact"}],
                        "steps_pending": True}}
    assert planner.load_steps(pending) is None


def test_a_completed_plan_is_read_back_and_never_re_drawn():
    completed = {"plan": {"steps": [{"n": 1, "part": planner.PARTS[4],
                                     "primitive": "score_impact",
                                     "what": "stored", "why": ""}],
                          "steps_pending": False}}
    steps = planner.load_steps(completed)
    assert steps is not None and [s.what for s in steps] == ["stored"]


# ─── The plan may not promise the run prices anything ──────────────────────
#
# `score_impact` computes `affected_population * movable_gap` on every path
# this engine has: every `ImpactInputs` construction passes
# `value_per_unit=None`, and `weight_by_account_value` is `declared` in the
# registry precisely because nothing performs it. A theme's size is a COUNT
# OF ACCOUNTS. Two places used to tell the reader otherwise — the plan step
# and the derived-value note carried into the stored plan — and a fix to
# either one alone leaves the claim standing in the other.

#: Phrasings that assert the run sizes, prices or weights by account value.
#: Matched case-insensitively against both surfaces.
_PRICING_CLAIMS = (
    "price an account",
    "price of a typical",
    "sizing against the median",
    "stops a handful of very large accounts from making every theme",
)


def _unit_step():
    for step in _plan():
        if step.primitive == "set_counting_unit":
            return step
    raise AssertionError("the plan has no set_counting_unit step")


def test_the_counting_unit_step_does_not_claim_the_run_prices_accounts():
    """SITE ONE. The step fires only when the contracts carry a per-account
    value, which is exactly when a reader is most likely to believe the
    figure moved the sizing."""
    step = _unit_step()
    text = f"{step.what} {step.why}".lower()
    for claim in _PRICING_CLAIMS:
        assert claim not in text, f"the plan step still claims: {claim!r}"
    assert "count" in text, "the step must still say what the unit IS"


def test_the_counting_unit_step_says_the_value_does_not_move_the_sizing():
    """Not deleted, DISCLAIMED. The median is genuinely derived and it does
    genuine work — it is why the reader is not asked for a number their own
    contracts answer — so a reader whose contracts were read should still
    learn the figure was found, and learn in the same breath what it did
    not do."""
    step = _unit_step()
    text = f"{step.what} {step.why}".lower()
    assert "median" in text, "the derived figure must still be reported"
    assert "count of the accounts" in text, (
        "the step must state that size stays a count of accounts")


def test_the_derived_note_does_not_claim_the_run_prices_accounts():
    """SITE TWO, AND THE ONE THAT HIDES. This note is carried onto the plan
    as `account_value_derived_note` and SERIALISED, so it outlives the screen
    that produced it — fixing the step alone leaves the claim in the JSON.

    THE DISCLAIMER IS NOW ASSERTED AS A CONDITIONAL, NOT AS A PHRASE. This
    used to require "count of the accounts" unconditionally. That was true of
    every run when it was written and became false when the weighting path
    landed: `pipeline.build_findings` ranks a `value` run by revenue, so the
    note was requiring the stored plan to contradict its own verdict. A test
    that pins a stale sentence is protecting a phrase rather than behaviour,
    so this pins the PROPERTY — the disclaimer belongs on a counted run and
    must be gone from a weighted one — and fails on unfixed code in both
    directions.
    """
    from app.crucible.framework import derived_account_value

    for unit in ("", "count"):
        _value, note = derived_account_value(
            _report().observations, weighting_unit=unit)
        assert note, "the fixture must derive a value or this test is vacuous"
        for claim in _PRICING_CLAIMS:
            assert claim not in note.lower(), (
                f"weighting_unit={unit!r} still claims: {claim!r}")
        assert "count of the accounts" in note.lower(), (
            f"weighting_unit={unit!r} must state that size stays a count")

    _value, weighted = derived_account_value(
        _report().observations, weighting_unit="value")
    assert weighted, "the weighted branch must still produce a note"
    assert "count of the accounts" not in weighted.lower(), (
        f"the note disclaims a count on a run ranked by revenue: {weighted}")
    assert "median" in weighted.lower(), (
        "the figure must still be reported on a weighted run, or the fix was "
        "a deletion and this test is vacuous")
    for claim in _PRICING_CLAIMS:
        assert claim not in weighted.lower(), (
            f"the weighted note overclaims in the other direction: {claim!r}")


def test_the_stored_plan_payload_carries_the_corrected_note():
    """The assertion above reads the function; this one reads the BLOB, which
    is what a stored run actually carries. Both branches, because the defect
    the conditional replaced was invisible in exactly this serialised copy."""
    from app.crucible.framework import derived_account_value
    from app.crucible.plan import RunPlan

    def _stored(unit: str) -> str:
        value, note = derived_account_value(
            _report().observations, weighting_unit=unit)
        blob = RunPlan(
            goal_text="g", definition_text="d", currency="accounts",
            account_value_derived=value, account_value_derived_note=note,
        ).to_json()
        text = blob["account_value_derived_note"].lower()
        assert text, "the note must survive serialisation or this is vacuous"
        return text

    for unit in ("", "count"):
        stored = _stored(unit)
        for claim in _PRICING_CLAIMS:
            assert claim not in stored, (
                f"the stored plan at weighting_unit={unit!r} claims: {claim!r}")
        assert "count of the accounts" in stored

    stored = _stored("value")
    assert "count of the accounts" not in stored, (
        f"the stored plan disclaims a count on a weighted run: {stored}")
    for claim in _PRICING_CLAIMS:
        assert claim not in stored, f"the stored plan claims: {claim!r}"


# ─── The plan may promise only what the run performs ───────────────────────
#
# THE GOVERNING INVARIANT, AND THE ONE THAT KEEPS COMING BACK. A plan step is
# read as a commitment — it is the screen a reader approves — so a step saying
# the run will report something is a promise, and a promise the engine has no
# code path to keep is the coverage-note apology moved to the front of the
# document, where it does more damage. The failures have all had the same
# shape: a reconnaissance-time measurement, described accurately, followed by
# a clause handing it to a run that never receives it.
#
# So the pair below is written to catch the NEXT one rather than only the last.
# The specific test pins the sentence; the sweep pins the property across every
# declared kind, including a kind added after this was written.


#: A sentence telling the reader that THE RUN, or the finished report, will
#: carry out a reporting act.
#:
#: DELIBERATELY NARROW. It matches a claim about the run or the report DOING
#: something with a finding. It does not match a claim about which unit the
#: finished document states its sizes in — that rests on the pipeline's own
#: arithmetic rather than on the observation the step was written from, so the
#: consumer check below would be judging it against the wrong evidence and
#: would fail an honest sentence.
#:
#: `restates` IS IN THE REPORT ALTERNATION ON PURPOSE, and it is the verb the
#: censoring step now uses. A detector that skipped it would let the one
#: sentence this pair exists to police pass unexamined — the point is not to
#: forbid the claim, it is to make every claim of this shape get checked
#: against the document.
_RUN_REPORTS = re.compile(
    r"\bthe run (?:reports|records|says|re-?derives|restates|will\b)"
    r"|\bthe (?:finished )?report "
    r"(?:will\b|reports|records|says|restates)",
    re.I,
)


class _AnyFigure(dict):
    """Every figure key a step might format, so ONE synthetic observation can
    render any kind's step text without this test having to know which numbers
    that kind measures — which is what lets the sweep below cover a kind added
    to `recon.KINDS` tomorrow. `1.0` rather than `0.0` because a step is free
    to divide by a figure it was handed."""

    def __missing__(self, key: str) -> float:
        return 1.0


def _texts_for_kind(kind: str, *, weighting_unit: str) -> list[str]:
    """The step text this kind ALONE adds to a plan, at a settled unit.

    Diffed against the same call on an empty report, so the `_plain` steps
    every plan carries drop out and what remains is attributable to `kind`.

    BEHAVIOURAL, NOT A SOURCE SCAN, AND THAT IS THE POINT. A sentence the
    planner emits on one branch of a settled unit is a promise on that branch
    alone; reading the file would see both branches at once and could not tell
    a gated clause from an ungated one. It is also why the unit is swept in
    both of its values rather than left at its default.
    """
    o = recon.Observation(
        id=f"fixture:{kind}", kind=kind, severity="high", source="fixture",
        fields=("field_a", "field_b", "field_c"), what="fixture",
        figures=_AnyFigure(),
    )
    kw = dict(goal_text="grow revenue this year", currency="accounts",
              weighting_unit=weighting_unit,
              weighting_because="because the fixture says so.")
    base = {s.why for s in
            planner.compose_deterministic(report=recon.ReconReport(), **kw)[0]}
    return [s.why for s in planner.compose_deterministic(
        report=recon.ReconReport(observations=(o,)), **kw)[0]
        if s.why not in base]


#: Where a run-side code path would have to live for a step to be entitled to
#: say the run RE-DERIVES something.
_RUN_SIDE_FILES = ("app/crucible/report.py", "app/crucible/pipeline.py",
                   "app/routes/crucible.py")


def _run_side_source() -> str:
    """The run-side modules, READ AS SOURCE.

    Not a tree-level scan and not an import-time attribute: a text search that
    excludes a source file can be satisfied by that file's `.pyc`, and a scan
    over a checkout cannot see the file it was pointed away from. Each path is
    asserted to exist, because the failure mode of this helper is a guard that
    passes because it read nothing.

    NARROWED TO ONE QUESTION. This used to stand in for "can the run see this
    finding at all", which a source-text search answers badly now that
    `report._observations_section` reads every kind generically and names
    none. What a source search still answers well is the other half: whether
    any run-side module SWITCHES ON a kind, which is what re-deriving a
    measurement would require. Rendering a stored sentence verbatim needs no
    such reference, and computing a second rate cannot avoid one.
    """
    from pathlib import Path

    root = Path(planner.__file__).resolve().parents[2]
    out = []
    for rel in _RUN_SIDE_FILES:
        p = root / rel
        assert p.is_file(), f"{p} is missing; this guard would be vacuous"
        text = p.read_text(encoding="utf-8")
        assert text.strip(), f"{p} is empty; this guard would be vacuous"
        out.append(text)
    return "\n".join(out)


def _report_restates(kind: str, *, stored: bool = True) -> bool:
    """Does the FINISHED DOCUMENT restate a finding of this kind?

    BEHAVIOURAL, AND THAT IS THE WHOLE UPGRADE. The consumer test used to be
    the kind's own name in the run-side source — a proxy, chosen when
    `report.py` held no reference to an observation of any kind and the only
    available question was whether the run could see the finding at all. It
    was loose in the safe direction then and is simply wrong now:
    `report._observations_section` reads the stored observations generically
    and switches on no kind, so a source search would report every kind as
    unread while the document restates all of them.

    So this renders the document and looks for the observation's own sentence
    in it. It asks whether the report DOES the thing rather than whether a
    file mentions a word, which is narrower, stronger, and the only form that
    can fail for the right reason if someone deletes the section.

    `stored=False` builds the plan a run created before observations existed —
    no `observations` key at all. It must render nothing, and asserting that
    is what proves this helper is not a constant that returns True.
    """
    from app.crucible.report import render_report_html
    from app.crucible.routing import classify_goal

    goal = "grow revenue this year"
    sentence = f"THE STORED MEASUREMENT FOR {kind.upper()}"
    o = recon.Observation(
        id=f"fixture:{kind}", kind=kind, severity="high", source="fixture",
        fields=("field_a",), what=sentence, figures=_AnyFigure(),
    )
    plan: dict = {"routing": {"goal_class": classify_goal(goal)}}
    if stored:
        plan["observations"] = [o.to_json()]
    run = {"id": 1, "goal_text": goal, "prioritisation": {"plan": plan}}
    return sentence in render_report_html(run, [], [], plan)


def test_no_plan_step_says_the_run_reports_something_it_cannot_report():
    """THE GENERAL FORM, AND ITS PREMISE MOVED.

    A step may tell a reader the run or the report will do something with a
    finding only if the finished document actually does it. That is the same
    invariant this was written for; what changed is that it can now be checked
    against the document instead of against a substring of a source file,
    because there is finally a report-side reader of observations to check.

    The assertion is therefore narrower than the one it replaces — "the report
    restates this kind" rather than "some run-side module mentions this kind"
    — and fails in the direction that matters: when a step claims something
    the document does NOT do.
    """
    assert not _report_restates("censored_periods", stored=False), (
        "a plan with no `observations` key still rendered the fixture "
        "sentence, so the consumer check below is a constant and this sweep "
        "proves nothing"
    )
    offenders: list[tuple[str, str, str]] = []
    rendered: set[str] = set()
    for kind in recon.KINDS:
        for unit in ("count", "value"):
            for why in _texts_for_kind(kind, weighting_unit=unit):
                rendered.add(kind)
                m = _RUN_REPORTS.search(why)
                if m and not _report_restates(kind):
                    offenders.append((kind, unit, why))

    assert len(rendered) >= 8, (
        f"only {len(rendered)} of {len(recon.KINDS)} kinds rendered a step, "
        f"so this sweep is mostly testing nothing: {sorted(rendered)}"
    )
    assert not offenders, (
        "A plan step tells the reader the run or the report will carry out an "
        "act on a finding the finished document does not restate:\n"
        + "\n".join(f"  [{k}] at weighting_unit={u!r}: {w}"
                    for k, u, w in offenders)
    )


def test_the_censoring_step_promises_exactly_what_the_document_does():
    """THE SPECIFIC ONE, PINNED AT THE SENTENCE — TWICE WRONG, NOW CHECKED.

    Wording one ended "so the run reports the second figure and says which
    cohorts it counted", which no code path kept: the correction was computed
    on the plan path, shown once, and dropped. Wording two replaced it with
    "the run does not re-derive it and the finished report does not restate
    it", which was true of that engine and false of this one the moment
    `report._observations_section` landed.

    So the assertion is no longer the ABSENCE of a run-side promise. The step
    makes a promise with two halves and each is checked against the thing that
    would keep it: the report restates the measurement (verified by rendering
    a document), and the run does not produce a second figure of its own
    (verified by no run-side module switching on the kind).

    The figures are asserted too, because no revision of this sentence may be
    a deletion: the correction is real, measured off the reader's own cohorts,
    and worth having before you approve.
    """
    step = next(s for s in _plan() if s.primitive == "check_period_censoring")
    assert "46.7" in step.why and "93.3" in step.why, (
        "the measured figures must survive the correction, or the fix was a "
        "deletion and this test is vacuous"
    )
    assert "the finished report restates this measurement" in step.why, (
        f"the step no longer states what the document does with the figure: "
        f"{step.why}"
    )
    assert _RUN_REPORTS.search(step.why), (
        "the sentence must be one the general sweep above examines, or this "
        "step is making a claim nothing checks"
    )
    assert _report_restates("censored_periods"), (
        "the step says the finished report restates this measurement and the "
        "rendered document does not contain it"
    )
    # THE HALF THAT IS STILL A DENIAL. "does not re-derive it" is a claim
    # about arithmetic, not about rendering: no run-side module may switch on
    # this kind to compute a second rate. `_observations_section` renders a
    # stored sentence verbatim and names no kind, so it does not trip this.
    assert "does not re-derive it" in step.why
    assert "censored_periods" not in _run_side_source(), (
        "a run-side module now switches on this kind, so it may be computing "
        "a rate of its own — the step's 'does not re-derive it' needs "
        "re-checking rather than this guard needs deleting"
    )


def _amount_step(**kw):
    """The monetary-coverage step off the prose fixture, at a settled unit."""
    steps = planner.minimal_plan(
        goal_text="grow revenue", currency="accounts", report=_prose_report(),
        source_types=("pm_manual", "customer_voice"), **kw)
    return next(s for s in steps
                if s.params.get("field") == "properties.amount")


def test_the_monetary_gap_step_states_the_unit_it_is_actually_run_at():
    """THE SAME DEFECT AS THE ATTRIBUTION GAP, ONE STEP OVER.

    This observation measures whether SIGNALS carry a figure. The unit is
    settled from a priced BOOK and the recorded business model — a different
    question about different evidence — so a tenant whose transcripts carry no
    amounts and whose contracts export prices every account is weighted while
    this step told it every size in the document was stated in accounts
    touched. Both branches are asserted, so the guard cannot be satisfied by
    deleting the sentence.
    """
    counted = _amount_step()
    assert "stated in accounts touched, never in money" in counted.why
    assert "nothing connected here measures it" in counted.why

    weighted = _amount_step(
        weighting_unit="value",
        weighting_because="themes are ranked by the revenue behind them.")
    assert "stated in accounts touched, never in money" not in weighted.why, (
        f"the step still claims a count on a weighted run: {weighted.why}")
    assert "comes from your contracts" in weighted.why
    for step in (counted, weighted):
        assert "signals do." in step.why, (
            "the measurement must survive the correction or this is vacuous")
