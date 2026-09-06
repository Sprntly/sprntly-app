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


# ─── The assumption every run makes about which side of the sale an account
#     is on ─────────────────────────────────────────────────────────────────


def _sides_gap(gaps):
    return next((g for g in gaps if "prospects" in g.question), None)


def test_the_customer_side_assumption_is_disclosed_rather_than_silent():
    """`claims.infer_account_sides` resolves an account it cannot place to
    `customer` and says in its own docstring that this "is disclosed as an
    assumed parameter (I8) by the caller rather than hidden here". No caller
    disclosed it: before this, `plan`, `planner` and `report` between them
    contained not one occurrence of the word."""
    gaps, _ = derive_gaps_and_promises([_src("customer_voice")])
    gap = _sides_gap(gaps)
    assert gap is not None, "the assumption is made on every run and stated on none"
    assert "which side of the sale an account is on" in gap.because
    assert "every named account is counted as a customer" in gap.because
    assert gap.remedy, "a gap without a remedy is an apology"


def test_the_assumption_is_stated_whatever_is_connected():
    """UNCONDITIONAL, BECAUSE THE ABSENCE IS. `claims.PROSPECT_KEYS` is read
    in four places and written in none, so no source this engine can ingest
    carries the distinction. A gap that came and went with the kept inventory
    would imply that connecting something closes it."""
    for kept in ([], [_src("analytics")], [_src("revenue"), _src("customer_voice")]):
        assert _sides_gap(derive_gaps_and_promises(kept)[0]) is not None, (
            f"the assumption went unstated for kept={[s.source_type for s in kept]}"
        )


def test_the_disclosure_does_not_claim_a_goal_filter_production_never_runs():
    """THE SENTENCE THIS GAP MUST NOT GROW. The obvious follow-on — that a
    prospect scores zero against a retention goal — describes
    `build_findings(goal_accounts=...)`, and every production caller leaves
    that argument `None`. Saying it would be a fresh instance of the defect
    this work exists to remove."""
    gap = _sides_gap(derive_gaps_and_promises([_src("customer_voice")])[0])
    said = f"{gap.question} {gap.because} {gap.remedy}".lower()
    for claim in ("score", "scores zero", "retention goal", "filtered out",
                  "excluded from", "ranked lower", "does not count towards"):
        assert claim not in said, (
            f"the disclosure claims a goal filter production does not run: "
            f"{claim!r}"
        )


def test_no_prospect_writer_exists_or_this_disclosure_is_stale():
    """THE PREMISE, PINNED. The gap says the distinction is not recorded. It
    is true because nothing in `app/` ever writes a prospect key — the day
    something does, this sentence becomes a lie in the reader's own document
    and the failure has to land here rather than in production.

    Read as SOURCE over the tree, with each file asserted non-empty: a text
    search that excludes a source file can be satisfied by that file's
    `.pyc`, and the failure mode of a guard like this is passing because it
    read nothing.

    `prospect` ONLY, THOUGH `PROSPECT_KEYS` HAS TWO. `candidate` is an
    ordinary English word this codebase already uses for unrelated things —
    a goal resolution status, a column-rename suggestion — so scanning for it
    as a dict key would fire on noise, and a guard that cries wolf is one
    somebody deletes. The realistic way this premise dies is a connector
    landing a `prospect` field, which this does catch. It is deliberately
    loose in the safe direction: it cannot manufacture a writer that is not
    there.
    """
    from pathlib import Path

    from app.crucible import claims

    assert "prospect" in claims.PROSPECT_KEYS, (
        "the key this scans for is no longer one the engine reads")

    root = Path(claims.__file__).resolve().parents[2] / "app"
    files = sorted(root.rglob("*.py"))
    assert len(files) > 50, f"only {len(files)} sources scanned; guard is vacuous"

    writers = []
    for p in files:
        text = p.read_text(encoding="utf-8", errors="replace")
        assert text.strip() or p.name == "__init__.py", f"{p} read as empty"
        # An ASSIGNMENT into a mapping under the key. Reading one
        # (`props.get("prospect")`) is what the engine does today and is not
        # a writer; `claims.py` itself is the reader and is exempt.
        if p.name == "claims.py":
            continue
        if '"prospect":' in text or "'prospect':" in text:
            writers.append(str(p.relative_to(root)))
    assert not writers, (
        "something now records which side of the sale an account is on, so "
        "the plan's disclosure that nothing does is false:\n  "
        + "\n  ".join(writers)
    )
