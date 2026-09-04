"""`app.crucible.recon` — the structural checks that run before the plan.

EVERY POSITIVE ASSERTION HAS A NEGATIVE TWIN, and that is the point of this
file rather than a nicety. These checks are heuristics over shape, and the way
a shape heuristic fails is not by missing things — it is by firing on
everything, at which point it looks like it works and reports noise. So each
check is exercised against a table WITH the property and the same table with
only that property removed (`tests._tabular_recon_fixtures`'s `clean` twins), and the
second half is what makes the first half mean anything.

Pure: no DB, no LLM, no clock.
"""
from __future__ import annotations

from tests import _tabular_recon_fixtures as fx

from app.crucible import recon


def _kinds(tables) -> set[str]:
    return {o.kind for o in recon.observe(tables).observations}


def _only(tables, kind: str):
    hits = [o for o in recon.observe(tables).observations if o.kind == kind]
    assert len(hits) == 1, f"expected exactly one {kind}, got {len(hits)}"
    return hits[0]


# ─── 1. Two value columns that disagree, and the third that explains it ─────


def test_a_third_column_that_explains_the_gap_between_two_value_columns():
    o = _only([fx.contracts()], "value_columns_disagree")
    assert o.fields == ("base_acv_usd", "total_acv_usd", "expansion_acv_usd")
    # The numbers, not the sentence: these are what the planner may cite.
    assert o.figures["rows_differing"] == 2
    assert o.figures["rows_explained"] == 8
    assert o.figures["gap_total"] == 273378
    assert round(o.figures["gap_share"], 4) == 0.1185


def test_two_value_columns_that_agree_everywhere_are_not_a_finding():
    """The negative twin: same table, expansion zeroed, nothing to reconcile."""
    assert "value_columns_disagree" not in _kinds([fx.contracts(clean=True)])


def test_columns_that_disagree_on_every_row_are_two_quantities_not_two_readings():
    """`expansion + base == total` is also true, and reporting it as "these two
    disagree, reading the smaller understates the book by 91%" is arithmetic
    dressed as a mistake nobody could make. The differing-share bar is what
    separates a rival reading from a component."""
    o = _only([fx.contracts()], "value_columns_disagree")
    assert "expansion_acv_usd" not in (o.fields[0], o.fields[1])


# ─── 2. A coded field the coder gave up on ─────────────────────────────────


def test_a_coded_field_missing_where_the_free_text_is_present():
    o = _only([fx.feedback()], "coding_gap")
    assert o.fields == ("rating_out_of_5", "text")
    assert o.figures["missing"] == 4
    assert o.figures["missing_with_text"] == 4
    assert o.figures["text_present_share"] == 1.0


def test_a_coded_field_and_its_text_missing_together_is_not_a_coding_gap():
    """The row simply has not happened yet — an open deal has no loss reason
    and no loss note. Firing here would send every run chasing a hole that is
    not there, which is the single most likely way this check goes wrong."""
    assert "coding_gap" not in _kinds([fx.feedback(clean=True)])


# ─── 3. Two funnel stages that are one measurement written twice ───────────


def test_two_adjacent_stages_holding_identical_values_across_every_cohort():
    o = _only([fx.funnel()], "stage_collapse")
    assert o.fields == ("reached_scenario_setup", "ran_first_exercise")
    assert o.figures["rows"] == 3


def test_one_cohort_differing_is_enough_to_make_them_two_real_stages():
    assert "stage_collapse" not in _kinds([fx.funnel(clean=True)])


def test_two_columns_that_are_constant_are_not_a_collapsed_stage():
    """Identical and meaningless. An empty sheet is not a funnel finding, and
    without the distinct-values bar every pair of zero columns would be one."""
    flat = recon.make_table(
        "flat",
        [{"a": 0, "b": 0}, {"a": 0, "b": 0}, {"a": 0, "b": 0}],
        columns=("a", "b"),
    )
    assert not recon.identical_columns(flat, ["a", "b"])


# ─── 4. Activity concentrated away from the money ──────────────────────────


def test_the_loudest_accounts_are_not_the_valuable_ones():
    o = _only([fx.tickets(), fx.contracts()], "concentration_divergence")
    assert o.fields == ("account", "total_acv_usd")
    assert round(o.figures["volume_share"], 4) == 0.8792
    assert round(o.figures["value_share"], 4) == 0.3379
    assert o.figures["ratio"] > 2.0


def test_activity_spread_evenly_across_accounts_is_not_a_divergence():
    assert "concentration_divergence" not in _kinds(
        [fx.tickets(aligned=True), fx.contracts()])


def test_a_divergence_is_scored_only_over_accounts_both_sides_know():
    """Otherwise the finding is an artefact of the join: an account with
    tickets and no contract contributes to one share and not the other."""
    div = recon.concentration_divergence(
        {"a": 10.0, "b": 1.0, "c": 1.0, "d": 1.0, "e": 1.0, "ghost": 900.0},
        {"a": 1.0, "b": 10.0, "c": 10.0, "d": 10.0, "e": 10.0},
        group_field="account", volume_label="tickets", value_label="acv",
    )
    assert div is not None
    assert div.group_count == 5  # the ghost is not counted on either side


# ─── The per-account value, which exists to SUPPRESS a question ────────────


def test_a_per_account_annual_value_is_derived_from_the_contracts():
    o = _only([fx.contracts()], "unit_value_derivable")
    assert o.fields == ("account", "total_acv_usd")
    assert o.figures["accounts"] == 8
    assert o.figures["median"] == 283526


def test_a_deal_amount_is_not_a_per_account_annual_value():
    """A pipeline `amount_usd` is money and is not what an account is worth
    per year. Suppressing "what is one account worth?" on the strength of it
    would answer the question with the wrong number, which is worse than
    asking."""
    opps = recon.make_table(
        "opportunities",
        [{"account": f"Account {i}", "amount_usd": 1000 * i} for i in range(1, 9)],
        columns=("account", "amount_usd"),
    )
    assert "unit_value_derivable" not in _kinds([opps])


# ─── Properties of the pass itself ─────────────────────────────────────────


def test_the_whole_pack_produces_all_four_structural_findings():
    assert _kinds(fx.full_pack()) >= {
        "value_columns_disagree", "coding_gap", "stage_collapse",
        "concentration_divergence",
    }


def test_the_pass_is_deterministic():
    """The engine's central claim is that the same substrate produces the same
    answer. A recon pass whose observations reshuffled between reads would put
    different numbers into a plan the reader has already approved."""
    a = recon.observe(fx.full_pack())
    b = recon.observe(fx.full_pack())
    assert [o.id for o in a.observations] == [o.id for o in b.observations]
    assert [o.figures for o in a.observations] == [o.figures for o in b.observations]


def test_coverage_names_what_is_missing_rather_than_leaving_it_to_be_inferred():
    report = recon.observe(
        fx.full_pack(),
        expected_sources=["revenue", "analytics", "customer_voice",
                          "outcome_measured"],
    )
    assert report.missing == ("outcome_measured",)
    assert report.total_records == sum(len(t.rows) for t in fx.full_pack())


def test_a_table_the_pass_cannot_read_costs_that_table_and_not_the_run():
    """Total by construction: the plan gate must never fail because one sheet
    had a shape a check did not like."""

    class Exploding(dict):
        def get(self, *_a, **_k):  # noqa: D102
            raise RuntimeError("unreadable cell")

    bad = recon.Table(name="bad", rows=(Exploding(),), columns=("a",))
    report = recon.observe([bad, fx.contracts(), fx.tickets()])
    kinds = {o.kind for o in report.observations}
    # The per-table checks on the good tables still ran…
    assert "value_columns_disagree" in kinds
    # …AND SO DID THE CROSS-TABLE ONES, which is the half that regressed: they
    # took the raw table list inside a single try, so one unreadable sheet
    # anywhere silently cost the whole run its concentration finding.
    assert "concentration_divergence" in kinds


def test_observations_round_trip_through_json():
    """They are stored on the run and read back on every later render, so a
    lossy round-trip would silently change what a plan cites."""
    original = recon.observe(fx.full_pack()).observations
    restored = recon.observations_from_json([o.to_json() for o in original])
    assert [o.id for o in restored] == [o.id for o in original]
    assert [o.figures for o in restored] == [o.figures for o in original]


def test_a_plan_stored_before_observations_existed_reads_back_as_none_not_broken():
    assert recon.observations_from_json(None) == ()
    assert recon.observations_from_json([{"kind": "nonsense"}])[0].figures == {}
