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
