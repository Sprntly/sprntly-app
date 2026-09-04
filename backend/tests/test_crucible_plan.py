"""`crucible/plan.py`'s framework-mismatch gap. Pure — no DB, no LLM. Live
`build_plan(...)` coverage (framework selection over a real company's real
source inventory) lives in `test_routes_crucible.py`, which already owns the
fake-Supabase `ctx` fixture this needs."""
from app.crucible.framework import FrameworkChoice
from app.crucible.plan import SourceInventory, derive_gaps_and_promises


def _src(source_type: str, n: int = 10) -> SourceInventory:
    return SourceInventory(source_type, n, source_type, "witnesses")


# ─── The chosen framework, and the gap when it could not honour the
#     company's own onboarding setting ─────────────────────────────────────


def test_a_declared_framework_mismatch_becomes_a_gap_not_a_silent_swap():
    """The plan calls out what THIS run needs and cannot get — including the
    company's own stated framework, when the data cannot support it."""
    choice = FrameworkChoice(
        framework="moscow",
        reason="your team set RICE at onboarding, but nothing connected "
               "here carries a number",
        declared="rice", honoured_declared=False,
        remedy="connect an analytics or revenue source",
    )
    gaps, _ = derive_gaps_and_promises([_src("customer_voice")], (), framework_choice=choice)
    questions = " ".join(f"{g.question} {g.because} {g.remedy}" for g in gaps)
    assert "RICE" in questions
    assert "connect an analytics or revenue source" in questions


def test_an_honoured_declared_framework_adds_no_extra_gap():
    choice = FrameworkChoice(
        framework="rice", reason="numeric data connected",
        declared="rice", honoured_declared=True,
    )
    gaps, _ = derive_gaps_and_promises([_src("analytics")], (), framework_choice=choice)
    assert not any("isn't this ranked by" in g.question for g in gaps)


def test_no_declared_framework_at_all_adds_no_mismatch_gap():
    choice = FrameworkChoice(framework="moscow", reason="no numeric source")
    gaps, _ = derive_gaps_and_promises([_src("customer_voice")], (), framework_choice=choice)
    assert not any("isn't this ranked by" in g.question for g in gaps)


def test_no_framework_choice_at_all_is_still_a_valid_call():
    """`derive_gaps_and_promises` predates `framework_choice` and every other
    existing caller/test omits it — the parameter must be fully optional."""
    gaps, produce = derive_gaps_and_promises([_src("customer_voice")])
    assert isinstance(gaps, tuple)
    assert isinstance(produce, tuple)


# ─── The method on the plan: steps and observations, added additively ───────
#
# `crucible_runs.prioritisation` is a single JSONB blob with no version field
# and every reader guards with `.get()`. These two fields therefore have to be
# additive with defaults — a rename or a required field strands every plan
# already stored — and no migration is involved at all.

from app.crucible import planner as _planner, recon as _recon  # noqa: E402
from app.crucible.plan import RunPlan  # noqa: E402
from tests import _tabular_recon_fixtures as _fx  # noqa: E402


def test_a_plan_with_no_method_is_still_a_valid_plan():
    """What every existing caller builds, and what every plan stored before
    this shipped reads back as."""
    plan = RunPlan(goal_text="g", definition_text="d", currency="accounts")
    blob = plan.to_json()
    assert blob["steps"] == []
    assert blob["observations"] == []
    assert blob["account_value_derived"] is None


def test_the_steps_and_observations_round_trip_through_the_stored_blob():
    """They are written once and read back on every later open, approve and
    render, so a lossy round-trip would change the document after the reader
    approved it."""
    report = _recon.observe(_fx.full_pack())
    steps = _planner.minimal_plan(
        goal_text="grow revenue", currency="accounts", report=report,
        source_types=("revenue", "customer_voice"))
    plan = RunPlan(
        goal_text="grow revenue", definition_text="d", currency="accounts",
        steps=tuple(steps), observations=report.observations,
    )
    blob = plan.to_json()
    assert [s["primitive"] for s in blob["steps"]] == [s.primitive for s in steps]
    restored_steps = _planner.steps_from_json(blob["steps"])
    restored_obs = _recon.observations_from_json(blob["observations"])
    assert [s.why for s in restored_steps] == [s.why for s in steps]
    assert [o.figures for o in restored_obs] == [o.figures for o in report.observations]


def test_a_derived_account_value_never_lands_in_the_field_the_report_calls_an_estimate():
    """`report.py` renders `account_value` with the words "an estimate you
    gave rather than something measured". True of a number a reader typed and
    false of one read off their contracts — so a derived figure goes in its
    own field, or the finished document misattributes measured data to the
    reader."""
    plan = RunPlan(
        goal_text="g", definition_text="d", currency="accounts",
        account_value_derived=283526.0,
        account_value_derived_note="taken from total_acv_usd",
    )
    blob = plan.to_json()
    assert blob["account_value"] is None
    assert blob["account_value_derived"] == 283526.0
    assert blob["account_value_derived_note"]
