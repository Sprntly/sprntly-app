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
    failed."""
    _, steps = _prose_plan()
    step = next(s for s in steps
                if s.params.get("field") == "properties.account")
    assert "count accounts instead of weighting" in step.why
    assert "0.0%" in step.why or "0 of 200" in step.why


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


def test_weighting_by_revenue_is_registered_and_never_emitted():
    """It is what a reader assumes is happening when a plan calls a theme
    "big", and nothing performs it — `score_impact` counts accounts. Declared
    so the gap has a name; never emitted, so the plan cannot promise it."""
    assert not prim.REGISTRY["weight_by_account_value"].is_implemented
    _, steps = _prose_plan(attributed=True)
    assert "weight_by_account_value" not in {s.primitive for s in steps}


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
