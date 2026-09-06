"""Two goals over the SAME evidence must not produce the same plan.

THE MEASUREMENT THAT PRODUCED THIS FILE. Two runs on one tenant with one set of
twelve attachments — "what should I do to drive revenue?" and "how do we reduce
churn?" — resolved correctly different definitions and then emitted twenty-five
BYTE-IDENTICAL steps, including a funnel-shape check on an activation table
under the churn question. The cause was structural rather than a wording
failure: the steps are composed from the reconnaissance observations, which are
computed from the SHAPE of the evidence and had never seen the goal.

WHAT IS BEING PINNED, AND WHAT IS DELIBERATELY NOT. The assertion is not "a
churn goal writes fewer steps" — a keyword filter would satisfy that and would
be a worse engine wearing the appearance of a fix. It is that the routing is
BIDIRECTIONAL and REPORTED:

  · a funnel-shape finding produces a step under an activation goal and is set
    aside under a retention one,
  · a cohort-maturity finding does the exact opposite over the same code path,
  · a set-aside is NAMED with its reason and its observation stays on the plan,
  · and a goal the classifier cannot place changes nothing at all.

The last is the safety property. Every other assertion here describes a
narrowing, and a narrowing that fires on a goal nobody recognised is a run that
quietly stopped looking.

Pure, offline and deterministic: no model, no database, no clock. `planner.
_offline()` is true under pytest, so the composed path is the deterministic
plan and these assertions are about code rather than about a draw.
"""
from __future__ import annotations

import pytest

from app.crucible import recon, routing
from app.crucible.planner import compose_deterministic
from tests import _tabular_recon_fixtures as fx


# ─── 1. Reading the goal ────────────────────────────────────────────────────
#
# THE DEFINITION IS READ BEFORE THE GOAL TEXT, and the two cases below are the
# ones actually measured on staging: the goal strings are short and vague and
# the definitions are precise, which is the normal shape of a real run.


@pytest.mark.parametrize("goal, definition, expected", [
    # Measured, run 1. The definition names BOTH populations, which is a
    # whole-book question and must narrow nothing.
    ("What should I do to drive revenue?",
     "expansion and upsell minus churn and contraction, across accounts that "
     "reached a renewal date in the period",
     routing.BOOK_WIDE),
    # Measured, run 2. One population, unambiguously.
    ("How do we reduce churn?",
     "accounts lost in the period over accounts held at its start",
     routing.RETENTION),
    ("improve activation",
     "share of new signups that reach the first exercise",
     routing.ACTIVATION),
    # No definition yet — the goal text alone still has to work, because the
    # plan is built before the definition is adopted.
    ("reduce churn", "", routing.RETENTION),
    # Nothing recognised. The safe reading, and a DIFFERENT statement from
    # "your whole book".
    ("make the team faster", "", routing.UNCLASSIFIED),
])
def test_a_goal_is_placed_against_one_part_of_the_book(goal, definition, expected):
    assert routing.classify_goal(goal, definition) == expected


def test_a_goal_naming_two_populations_narrows_nothing():
    """AMBIGUITY RESOLVES TOWARDS DOING MORE. The two errors are not
    symmetric: an over-long plan is still a plan, and a plan that quietly
    stopped looking at half the evidence is the failure this gate exists to
    prevent."""
    cls = routing.classify_goal("", "churn and expansion together")
    assert cls == routing.BOOK_WIDE
    assert routing.bears_on("stage_collapse", cls)
    assert routing.bears_on("censored_periods", cls)


def test_a_column_name_does_not_classify_the_goal():
    """Word boundaries rather than substrings: a classification that fires on
    `churn_risk_bucket` is reading the evidence instead of the question."""
    assert routing.classify_goal("look at churn_risk_bucket", "") == \
        routing.UNCLASSIFIED


# ─── 2. The same evidence, two goals, two methods ───────────────────────────


def _steps(report, goal_class):
    steps, set_aside = compose_deterministic(
        goal_text="g", currency="accounts", report=report,
        source_types=("analytics",), goal_class=goal_class,
    )
    return steps, set_aside


def _funnel_report():
    """One activation funnel. Produces `stage_collapse` and nothing else."""
    report = recon.observe([fx.funnel()])
    assert {o.kind for o in report.observations} == {"stage_collapse"}
    return report


def _cohort_report():
    """One retention grid. Produces `censored_periods` and nothing else."""
    report = recon.observe([fx.retention()])
    assert {o.kind for o in report.observations} == {"censored_periods"}
    return report


def test_the_same_funnel_produces_a_step_for_one_goal_and_not_the_other():
    """THE DEFECT, AS AN ASSERTION. One table, one observation, two goals."""
    report = _funnel_report()
    activation, _ = _steps(report, routing.ACTIVATION)
    retention, _ = _steps(report, routing.RETENTION)

    assert [s.primitive for s in activation] != [s.primitive for s in retention]
    assert "check_stage_collapse" in {s.primitive for s in activation}
    assert "check_stage_collapse" not in {s.primitive for s in retention}


def test_the_same_cohort_grid_routes_the_opposite_way():
    """THE ASSERTION THAT STOPS THE ONE ABOVE FROM BEING SATISFIED BY A
    NARROWER ENGINE. A rule that simply wrote fewer steps for a retention goal
    would pass the funnel test and fail this one: here retention is the goal
    that KEEPS the step and acquisition is the one that sets it aside, over
    the same code path and the same table."""
    report = _cohort_report()
    retention, _ = _steps(report, routing.RETENTION)
    acquisition, _ = _steps(report, routing.ACQUISITION)

    assert "check_period_censoring" in {s.primitive for s in retention}
    assert "check_period_censoring" not in {s.primitive for s in acquisition}


def test_a_goal_that_could_not_be_placed_gets_the_plan_it_always_got():
    """THE SAFETY PROPERTY. Every other assertion in this file describes a
    narrowing; a narrowing that fires on a goal nobody recognised is a run
    that quietly stopped looking. `unclassified` and the empty default must
    both reproduce the pre-routing plan exactly."""
    report = _funnel_report()
    before, no_set_aside = _steps(report, "")
    unclassified, _ = _steps(report, routing.UNCLASSIFIED)
    activation, _ = _steps(report, routing.ACTIVATION)

    assert no_set_aside == ()
    assert [s.to_json() for s in before] == [s.to_json() for s in unclassified]
    assert [s.to_json() for s in before] == [s.to_json() for s in activation]


def test_the_corpus_integrity_checks_are_never_set_aside():
    """WHY THE TABLE IS SHORT ENOUGH TO DEFEND. Whether two columns both claim
    to be the account's value does not stop mattering because the question
    changed — it is a fact about whether the evidence can be trusted at all,
    and a run that stopped checking it for a churn goal would be unsafe in a
    way no reader could see."""
    report = recon.observe([fx.contracts()])
    assert "value_columns_disagree" in {o.kind for o in report.observations}
    for goal_class in routing.GOAL_CLASSES:
        steps, set_aside = _steps(report, goal_class)
        assert "reconcile_value_columns" in {s.primitive for s in steps}, goal_class
        assert set_aside == (), goal_class


# ─── 3. Set aside is a statement, not a deletion ────────────────────────────


def test_a_step_that_is_not_written_is_named_with_its_reason():
    """An omission the reader cannot see is indistinguishable from a check
    that quietly failed — and the reader is the only person who can say "no,
    that one does matter here", which is the whole reason this is a gate."""
    report = _funnel_report()
    _steps_out, set_aside = _steps(report, routing.RETENTION)

    assert len(set_aside) == 1
    entry = set_aside[0]
    assert "funnel" in entry.what.lower()
    assert "funnel check" in entry.why
    assert "keeping the accounts you already have" in entry.why
    # And it points at a real observation, which is still on the report.
    assert entry.observation in {o.id for o in report.observations}


def test_the_finding_itself_stays_on_the_plan_when_the_step_does_not():
    """SET ASIDE IS ABOUT THE METHOD, NOT ABOUT THE EVIDENCE. Dropping the
    observation would make the plan's own audit trail shorter and would stop
    the reader from overruling the decision."""
    report = _funnel_report()
    _steps_out, set_aside = _steps(report, routing.RETENTION)
    assert set_aside
    assert report.of_kind("stage_collapse")


def test_at_most_one_set_aside_per_kind_because_at_most_one_step_is_written():
    """`MAX_DETERMINISTIC_PER_KIND` is one. Listing eight set-asides would
    tell the reader eight steps were dropped when only one was ever going to
    be written."""
    report = recon.observe([fx.funnel(), fx.funnel()])
    entries = routing.set_asides_for(report, routing.RETENTION, per_kind=1)
    assert len(entries) == 1


# ─── 4. The decisions are made in code, and handed to the model frozen ──────


def test_the_routing_is_derived_from_the_role_the_engine_already_assigns():
    """NOT A SECOND TABLE OF WHAT A SOURCE IS FOR. The engine already decides
    what each source may witness (`claims.AUTHORITATIVE_FOR`), the plan's role
    column is a reading of that decision, and a routing table that restated it
    would drift from the thing that actually gates a claim later — silently,
    and in the direction of promising more than the run will accept."""
    from app.crucible.plan import ROLE_BACKGROUND, SourceInventory, role_for

    sizing = SourceInventory("revenue", 10, "revenue data", "", role=role_for("revenue"))
    background = SourceInventory("verbal_claim", 3, "verbal claims", "",
                                 role=ROLE_BACKGROUND)
    out = routing.resolve(goal_text="reduce churn", sources=[sizing, background])

    by_type = {r.source_type: r for r in out.sources}
    assert by_type["revenue"].disposition == routing.USE
    assert by_type["verbal_claim"].disposition == routing.DISCOUNT
    assert "may be counted" not in by_type["revenue"].why
    assert "nothing it carries may be counted" in by_type["verbal_claim"].why


def test_the_run_says_what_unit_it_can_size_in():
    """The single most consequential thing a reader learns before approving,
    and the one the engine used to degrade to silently."""
    with_value = routing.resolve(goal_text="reduce churn", unit_value_available=True)
    without = routing.resolve(goal_text="reduce churn", unit_value_available=False)
    assert any("in money" in n for n in with_value.notes)
    assert any("never in money" in n for n in without.notes)


def test_an_unrecorded_business_type_is_stated_rather_than_assumed_away():
    """`companies.business_type` is populated at onboarding and this engine had
    never read it. Reading it is only honest if NOT having it is also said out
    loud — otherwise a reader cannot tell an assumption from a fact."""
    known = routing.resolve(goal_text="reduce churn", business_type="B2B SaaS")
    unknown = routing.resolve(goal_text="reduce churn", business_type="")
    assert any("B2B SaaS" in n for n in known.notes)
    assert any("not recorded" in n for n in unknown.notes)


def test_the_prompt_hands_the_decisions_over_as_settled():
    """The model narrates; it does not decide. The block it is given says so,
    and `planner._SYSTEM` forbids changing any of it."""
    report = _funnel_report()
    out = routing.resolve(goal_text="reduce churn",
                          definition_text="accounts lost in the period")
    set_aside = routing.set_asides_for(report, out.goal_class, per_kind=1)
    block = routing.prompt_block(out, set_aside)

    assert "decided already, not by you" in block
    assert "write NO step for these" in block
    assert set_aside[0].observation in block


def test_a_set_aside_observation_is_not_offered_to_the_model_as_material():
    """A model shown a finding under "here is what you may use" writes a step
    for it. Being told elsewhere not to is a weaker instruction than not being
    handed it in the first place."""
    from app.crucible.planner import _prompt

    report = _funnel_report()
    out = routing.resolve(goal_text="reduce churn",
                          definition_text="accounts lost in the period")
    set_aside = routing.set_asides_for(report, out.goal_class, per_kind=1)
    prompt = _prompt(
        goal_text="reduce churn", definition_text="accounts lost",
        currency="accounts", report=report, source_types=("analytics",),
        routing=out, set_aside=set_aside,
    )
    observations_block = prompt.split("OBSERVATIONS")[1].split("HOW THIS GOAL")[0]
    assert set_aside[0].observation not in observations_block
    assert "write no numbers at all" in observations_block


def test_a_step_drawn_from_a_set_aside_observation_is_dropped():
    """THE GATE BEHIND THE PROMPT. Without it the plan could set a check aside
    in one section and carry out the same check four steps later — describing
    work it said it would not do, which is the mirror image of the failure
    this whole stage exists to remove."""
    from app.crucible.planner import PlanStep, verify

    report = _funnel_report()
    o = report.of_kind("stage_collapse")[0]
    step = PlanStep(
        n=1, part="Understand why", primitive="check_stage_collapse",
        params={"source": o.source, "fields": list(o.fields)},
        what="Check whether two stages are really two steps",
        why=f"They hold the same value on every one of {o.figures['rows']:.0f} rows.",
        observations=(o.id,),
    )
    kept_ok, _ = verify([step], report.observations,
                        available_sources=[o.source, o.source_label])
    assert len(kept_ok) == 1

    kept, dropped = verify(
        [step], report.observations,
        available_sources=[o.source, o.source_label],
        set_aside_observations=[o.id],
    )
    assert kept == []
    assert any("set aside" in d for d in dropped)


# ─── 5. End to end, through the function a run actually calls ───────────────


def _stub_company(monkeypatch, *, business_type="B2B SaaS"):
    """`build_plan` reads three things off the company row. None of them is
    what these assertions are about, and all three already fail soft."""
    from app.crucible import plan as plan_mod

    monkeypatch.setattr(
        plan_mod, "source_inventory",
        lambda _cid: ([plan_mod.SourceInventory(
            "analytics", 120, "product analytics", "how much something moved",
            role=plan_mod.role_for("analytics"),
            role_note=plan_mod.ROLE_NOTES[plan_mod.role_for("analytics")],
        )], 120))
    monkeypatch.setattr(
        "app.db.companies.business_type_for_company", lambda _cid: business_type)
    monkeypatch.setattr(
        "app.db.companies.declared_prioritization_framework", lambda _cid: None)


def test_a_built_plan_differs_by_goal_over_identical_evidence(monkeypatch):
    """THE WHOLE DEFECT, AT THE CALL SITE A RUN USES.

    Same company, same reconnaissance report, two goals. Before this existed
    the two `steps` lists were byte-identical, which is what a reader saw on
    staging.
    """
    from app.crucible.plan import build_plan

    _stub_company(monkeypatch)
    report = _funnel_report()

    churn = build_plan(
        company_id="co-1", goal_text="How do we reduce churn?",
        definition_text="accounts lost in the period over accounts held at "
                        "its start",
        recon_report=report)
    activation = build_plan(
        company_id="co-1", goal_text="How do we improve activation?",
        definition_text="share of new signups that reach the first exercise",
        recon_report=report)

    assert churn.to_json()["steps"] != activation.to_json()["steps"]
    assert "check_stage_collapse" not in {s.primitive for s in churn.steps}
    assert "check_stage_collapse" in {s.primitive for s in activation.steps}
    # And the reading itself is on the plan, so the reader can disagree with it.
    assert churn.to_json()["routing"]["goal_class"] == routing.RETENTION
    assert activation.to_json()["routing"]["goal_class"] == routing.ACTIVATION
    # And what it set aside is stated rather than silently absent.
    assert [sa["what"] for sa in churn.to_json()["set_aside"]]
    assert activation.to_json()["set_aside"] == []


def test_a_built_plan_reads_the_business_type_this_engine_never_read(monkeypatch):
    """`companies.business_type` is populated at onboarding and every other
    reasoning path in the product uses it. A plan deciding what a source is
    for without it was deciding with less than the product knows."""
    from app.crucible.plan import build_plan

    _stub_company(monkeypatch, business_type="marketplace")
    built = build_plan(
        company_id="co-1", goal_text="reduce churn", definition_text="",
        recon_report=_funnel_report())
    assert built.to_json()["routing"]["business_type"] == "marketplace"
    assert any("marketplace" in n for n in built.to_json()["routing"]["notes"])


def test_a_company_row_that_will_not_read_still_produces_a_plan(monkeypatch):
    """Same posture as the declared-framework read directly above it: a
    company row that will not load must never fail a plan, and "not recorded"
    is a statement the routing is already required to be able to make."""
    from app.crucible.plan import build_plan

    _stub_company(monkeypatch)

    def _boom(_cid):
        raise RuntimeError("connection reset")

    monkeypatch.setattr("app.db.companies.business_type_for_company", _boom)
    built = build_plan(
        company_id="co-1", goal_text="reduce churn", definition_text="",
        recon_report=_funnel_report())
    assert built.to_json()["routing"]["business_type"] == ""
    assert any("not recorded" in n for n in built.to_json()["routing"]["notes"])


def test_a_plan_built_without_a_reconnaissance_pass_carries_no_routing(monkeypatch):
    """Every existing caller passes no report and must be unaffected — and a
    routing derived from no evidence would be a set of decisions about
    material nobody looked at."""
    from app.crucible.plan import build_plan

    _stub_company(monkeypatch)
    built = build_plan(company_id="co-1", goal_text="reduce churn",
                       definition_text="")
    assert built.to_json()["routing"] == {}
    assert built.to_json()["set_aside"] == []
