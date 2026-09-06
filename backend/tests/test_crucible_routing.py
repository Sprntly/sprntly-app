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

import re

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
    and the one the engine used to degrade to silently.

    WHAT THIS ASSERTION USED TO BE, AND WHY IT HAD TO CHANGE. It read
    `any("in money" in n for n in with_value.notes)` — which pinned the
    with-value branch to the claim that sizes ARE stated in money. Nothing in
    the engine performs that (see the test below), so the test was holding a
    false sentence in place. Both branches must now say the size is a count of
    accounts; what differs between them is whether the per-account value was
    found and read at all, which is a statement about the EVIDENCE and is the
    only thing the flag actually changes.
    """
    with_value = routing.resolve(goal_text="reduce churn", unit_value_available=True)
    without = routing.resolve(goal_text="reduce churn", unit_value_available=False)

    # The flag changes what the run says it FOUND ...
    assert any("read from your own data rather than asked of you" in n
               for n in with_value.notes)
    assert any("Nothing read here carries a per-account value" in n
               for n in without.notes)
    # ... and never what the run says it COUNTS.
    assert any("count of the accounts it touches" in n for n in with_value.notes)
    assert any("stated in accounts touched" in n for n in without.notes)


#: Prose that asserts the run SIZES something in money — the capability
#: `weight_by_account_value` describes and does not perform. Deliberately
#: narrow: it must not fire on the disclaimers, which are the honest form of
#: the same subject ("never in money", "stated in accounts touched and never in
#: money"), so it matches only a positive claim that a size IS money.
_CLAIMS_MONEY_SIZING = re.compile(
    r"sizes?\b[^.]{0,40}\b(?:are|is)\s+stated\s+in\s+money"
    r"|weight(?:s|ed|ing)?\s+(?:a\s+theme\s+)?by\s+(?:the\s+)?revenue"
    r"|sized\s+in\s+money"
    # ADDED WITH THE WEIGHTING PATH. The engine can now genuinely rank by
    # revenue, and the sentence it uses to say so is "ranked by the revenue
    # behind them" — which the three patterns above do not match. A guard that
    # cannot see the wording the feature actually ships is not a guard.
    r"|rank(?:s|ed|ing)?\s+by\s+(?:the\s+)?revenue",
    re.IGNORECASE)


def test_a_routing_note_never_promises_a_capability_the_registry_calls_declared():
    """THE CLASS OF DEFECT, NOT THE ONE SENTENCE.

    A plan may only promise what the engine performs — that is the whole claim
    the audit trail makes, and `primitives.REGISTRY` is where the engine
    records which half of the vocabulary is real. `validate_steps` already
    enforces it for STEPS. The routing notes are free prose that never passed
    through that gate, and `prompt_block` hands them to the composition model
    as SETTLED FACT to restate in the reader's language — so a false note is
    not merely rendered, it is laundered through the model into the reader's
    own vocabulary.

    The measured failure: the `unit_value_available` branch stated "sizes here
    are stated in money rather than in a count of accounts" while
    `weight_by_account_value` was `declared`, nothing built an `ImpactInputs`
    with a `value_per_unit`, and `score_impact` returned a count of accounts.

    THIS TEST READS THE RUN'S VERDICT RATHER THAN HARDCODING THE ANSWER.
    `weight_by_account_value` is now implemented, so reading the registry's
    STATUS would make the assertion vacuously true on every run — the capability
    existing is not the same fact as this run using it. The condition is
    `weighting_unit`, which is what `pipeline.build_findings` is actually
    handed, so the note and the arithmetic cannot disagree.
    """
    offenders: list[tuple[str, bool, str]] = []
    for unit in ("", "count"):
        for available in (True, False):
            out = routing.resolve(
                goal_text="reduce churn",
                definition_text="accounts lost in the period",
                unit_value_available=available,
                weighting_unit=unit,
            )
            for note in out.notes:
                if _CLAIMS_MONEY_SIZING.search(note):
                    offenders.append((unit, available, note))

    assert not offenders, (
        "A routing note claims the run sizes by money on a run whose approved "
        "unit is a count of accounts:\n"
        + "\n".join(f"  weighting_unit={u!r} unit_value_available={a}: {n}"
                    for u, a, n in offenders)
    )


def test_a_weighted_run_is_allowed_to_say_it_weighs_and_must():
    """The other direction, and it matters as much. A guard that only ever
    demands a disclaimer would keep the disclaimer standing after the engine
    started weighting — a run stating the wrong unit is the same defect
    whichever way it points."""
    out = routing.resolve(
        goal_text="reduce churn",
        definition_text="accounts lost in the period",
        unit_value_available=True,
        weighting_unit="value",
        weighting_because=("62.0% of what I read names an account your "
                           "contracts can price, so themes are ranked by the "
                           "revenue behind them rather than by how many "
                           "accounts raised them."),
    )
    assert any(_CLAIMS_MONEY_SIZING.search(n) for n in out.notes)


def test_score_impact_sizes_in_accounts_so_the_note_above_is_the_truthful_one():
    """The other half of the pair. The test above pins what the plan SAYS;
    this pins what the engine DOES, so the two cannot drift apart silently and
    a future reader can see why the note is worded as it is."""
    from app.crucible.scoring import score_impact
    from app.crucible.types import ConfidenceInputs, Finding, ImpactInputs

    finding = Finding(
        id="f-1",
        statement="renewals slip when onboarding stalls",
        claim_ids=("c-1",),
        confidence_inputs=ConfidenceInputs(
            strengths=("reported",), claim_types=("preference",),
            observed_ats=(), authoritative_count=1, claim_count=1,
            independent_authoritative_source_types=1,
        ),
        impact_inputs=ImpactInputs(
            currency="accounts",
            affected_population=7.0,
            movable_gap=1.0,
            # WHAT EVERY CONSTRUCTION SITE IN `app/` PASSES. Both of them —
            # `pipeline._findings` and the read path in `routes/crucible` —
            # hardcode None, which is why the money branch of `score_impact`
            # is unreachable in production and the plan may not promise it.
            value_per_unit=None,
        ),
    )

    impact = score_impact(finding)
    assert impact.value == 7.0, "the size is the count of accounts touched"
    assert impact.value_per_unit is None


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


# ─── The other half of the same pair, one branch over ──────────────────────


#: Prose asserting that a theme's size IS a count and never money. The honest
#: sentence on a counted run and a false one on a weighted run — the same
#: defect as `_CLAIMS_MONEY_SIZING` catches, pointing the other way.
_CLAIMS_COUNT_SIZING = re.compile(
    r"size\b[^.]{0,60}\bcount of the accounts"
    r"|stated in accounts touched"
    r"|count of accounts",
    re.IGNORECASE)


def test_the_derived_value_note_drops_its_disclaimer_on_a_weighted_run():
    """A DISCLAIMER LEFT STANDING AFTER THE ENGINE STARTED WEIGHTING.

    The `unit_value_available` branch ends "It does not change how anything is
    sized here: a theme's size is still a count of the accounts it touches,
    never money." That was true of every run when it was written. It is now
    false on a weighted one, and it renders IMMEDIATELY ABOVE
    `weighting_because`, which says themes are ranked by the revenue behind
    them — so the plan contradicts itself on the one field this feature exists
    to get right, and `prompt_block` hands both to the composition model as
    settled fact.

    The planner's own version of this sentence is already gated (its step is
    emitted only when there is no `priceable_coverage`); this note was the one
    site that was not.
    """
    out = routing.resolve(
        goal_text="reduce churn",
        definition_text="accounts lost in the period",
        unit_value_available=True,
        weighting_unit="value",
        weighting_because=("62.0% of what I read names an account your "
                           "contracts can price, so themes are ranked by the "
                           "revenue behind them rather than by how many "
                           "accounts raised them."),
    )
    assert any("read from your own data" in n for n in out.notes), (
        "the note must still tell the reader the figure was recognised, or "
        "the fix was a deletion and this test is vacuous"
    )
    offenders = [n for n in out.notes if _CLAIMS_COUNT_SIZING.search(n)]
    assert not offenders, (
        "A routing note tells the reader a theme's size is a count of "
        "accounts on a run whose approved unit is revenue:\n"
        + "\n".join(f"  {n}" for n in offenders)
    )


def test_a_counted_run_keeps_the_disclaimer_and_must():
    """The guard above must not be satisfiable by deleting the sentence
    everywhere. On a counted run the disclaimer is the whole point of the
    note — it is what stops a reader who connected contract data assuming the
    figure moved the sizing."""
    for unit in ("", "count"):
        out = routing.resolve(
            goal_text="reduce churn",
            definition_text="accounts lost in the period",
            unit_value_available=True,
            weighting_unit=unit,
        )
        assert any(_CLAIMS_COUNT_SIZING.search(n) for n in out.notes), (
            f"weighting_unit={unit!r} lost the disclaimer: {out.notes}"
        )
