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
from app.crucible.claims import self_account_keys as recon_self_keys


def _kinds(tables, signals=()) -> set[str]:
    return {o.kind for o in recon.observe(tables, signals=signals).observations}


def _only(tables, kind: str, signals=()):
    hits = [o for o in recon.observe(tables, signals=signals).observations
            if o.kind == kind]
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


# ─── 5. A retention grid is not a funnel ───────────────────────────────────


def test_a_retention_grid_does_not_report_collapsed_funnel_stages():
    """It used to, twice, and both were definitions rather than findings:
    `month_1` equals `cohort_size` because retention starts at 100%, and
    `month_1` equals `month_2` because nobody left in the first month.
    Adjacent-column equality carries no information in a period grid, and a
    false alarm at the top of a plan spends the reader's trust on a non-event.
    """
    assert "stage_collapse" not in _kinds([fx.retention()])


def test_a_real_funnel_still_reports_its_collapsed_stage():
    """The suppression must be narrow. Two adjacent stages that ARE one
    measurement recorded twice is the finding the check exists for."""
    assert "stage_collapse" in _kinds([fx.funnel()])


def test_what_suppresses_the_check_is_raggedness_not_the_column_names():
    """THE DISCRIMINATOR, ISOLATED. Keep only the cohorts with a full window
    and the same columns, with the same names, become a fully populated
    ordinal series — a stage sequence as far as any shape test can tell — and
    the check speaks up again. A name match on "month" or "cohort" could not
    tell these two tables apart."""
    assert "stage_collapse" in _kinds([fx.retention(mature_only=True)])


# ─── 6. Trailing zeros that are the calendar, not the customers ────────────


def test_periods_that_have_not_happened_yet_are_not_observed_zeros():
    o = _only([fx.retention()], "censored_periods")
    assert o.figures["cohorts"] == 16
    assert o.figures["mature_cohorts"] == 8
    # The arithmetic, both ways round. 28/30 against 28/60.
    assert o.figures["mature_numerator"] == 28
    assert o.figures["mature_denominator"] == 30
    assert o.figures["naive_denominator"] == 60
    assert round(o.figures["mature_rate"], 4) == 0.9333
    assert round(o.figures["naive_rate"], 4) == 0.4667
    assert round(o.figures["error_points"], 1) == 46.7


def test_a_grid_with_no_immature_cohorts_has_nothing_to_censor():
    assert "censored_periods" not in _kinds([fx.retention(mature_only=True)])


def test_a_cohort_that_actually_emptied_out_is_not_called_censoring():
    """THE SIGNATURE IS THAT EVERY RAGGED COHORT STOPS AT THE SAME MOMENT —
    cohort start plus periods observed is one constant, and that constant is
    the last date the data covers. Walk one cohort's window off that frontier
    and it is a cohort that genuinely lost everyone, which must not be
    explained away as the calendar. Inventing the opposite error is not an
    improvement on the one this fixes."""
    assert "censored_periods" not in _kinds([fx.retention(unaligned=True)])


def test_the_grid_detector_finds_the_series_the_denominator_and_the_key():
    grid = recon.period_grid(fx.retention())
    assert grid is not None
    assert grid.width == 12
    assert grid.key_field == "cohort_month"
    assert grid.size_field == "cohort_size"
    assert grid.censored is True
    assert len(grid.mature) == 8


def test_a_funnel_is_not_a_period_grid_at_all():
    assert recon.period_grid(fx.funnel()) is None


# ─── 7. What KIND of evidence this is, from the triage category ────────────


def test_the_triage_category_the_ingest_pass_already_writes_is_read():
    """A Haiku triage call classifies every ingested document and writes the
    answer to `provenance.triage_category`. Both signal reads already select
    `provenance`, so this costs no extra query, no tokens and no latency — and
    until now nothing anywhere read it."""
    mix = recon.evidence_mix(fx.signals())
    assert mix.signals == 1416
    assert mix.categorised == 1275
    assert round(mix.coverage, 3) == 0.900
    assert mix.top[0] == "product_prd"


def test_the_fail_open_sentinel_is_tolerated_and_never_counted_as_a_category():
    """`uncategorized` is stamped when the triage call itself errors and is
    NOT a member of the declared taxonomy. "Triage ran and failed" is a
    different fact from "triage never ran here", and merging them would
    overstate coverage."""
    mix = recon.evidence_mix(fx.signals(fail_open=50))
    assert mix.fail_open == 50
    assert "uncategorized" not in mix.counts
    assert mix.categorised == 1275


def test_signals_with_no_category_at_all_are_simply_uncounted():
    """The checklist pass and the batched extract path both pin the field to
    `None`, so on a call-heavy tenant a large share carries nothing. That has
    to read as absent, never as an error."""
    mix = recon.evidence_mix([{"provenance": {}} for _ in range(30)])
    assert mix.signals == 30 and mix.categorised == 0
    assert recon._observe_evidence_mix(mix) == []


def test_the_mix_says_how_much_is_a_customer_speaking():
    o = _only(fx.full_pack(), "evidence_mix", signals=fx.signals())
    # Measured on a real tenant: 4.5% firsthand, and the run should say so
    # before it answers a revenue question from internal documents.
    assert round(o.figures["firsthand_share"], 3) == 0.045
    assert o.severity == "high"
    assert o.figures["coverage_share"] > 0.8


def test_the_mix_needs_signals_and_is_absent_without_them():
    """Every caller with only spreadsheets, and every tenant read before this
    existed."""
    assert "evidence_mix" not in _kinds(fx.full_pack())


# ─── Storage keys are for joins; readers get words ─────────────────────────


def test_a_sheet_key_is_rendered_as_something_a_reader_recognises():
    assert recon.source_label("03_product_analytics:activation_funnel") == (
        "product analytics — activation funnel")
    assert recon.origin_label("03_product_analytics:activation_funnel") == (
        "product analytics")
    # Several sheets of one workbook are ONE source to a reader; counting them
    # as several is how a plan claims eleven sources over four files.
    assert recon.origin_label("03_product_analytics:cohort_retention") == (
        recon.origin_label("03_product_analytics:activation_funnel"))


def test_every_observation_can_name_its_source_without_the_key():
    for o in recon.observe(fx.full_pack(), signals=fx.signals()).observations:
        assert ":" not in o.source_label


# ─── 8. The knowledge-graph side, where the table checks find nothing ──────
#
# Measured on two real local tenants: the whole pass produced exactly ONE
# observation each and spent ~29ms on the larger building and profiling
# seventeen tables that every check then declined to speak about. The facts a
# plan needs on a prose corpus were all there, in fields already fetched.
#
# Every assertion below has a negative twin driven by a single fixture flag, so
# a check that fires on both settings is detecting the fixture, not a property.


def test_dates_that_are_the_import_and_not_the_events():
    o = _only([], "dating_unreliable", signals=fx.kg_signals())
    assert o.figures["ingest_clock_share"] == 1.0
    assert o.figures["signals"] == 200


def test_a_corpus_with_real_event_dates_says_nothing_about_dating():
    """The smaller real tenant measures 0% ingest-clock, so this is the shape
    that must stay quiet — otherwise the check reports a dating problem on
    every corpus and the plan learns nothing from it."""
    assert "dating_unreliable" not in _kinds(
        [], signals=fx.kg_signals(ingest_clock=False))


def test_the_pipeline_and_the_plan_share_one_dating_answer():
    """`pipeline._refute` switches off its echo rule on exactly this shape.
    Two implementations of "are these dates real" would drift, and the likely
    direction is the bad one — a plan promising a rule the run disabled."""
    assert recon.dates_are_ingest_clock(fx.kg_signals()) is True
    assert recon.dates_are_ingest_clock(fx.kg_signals(ingest_clock=False)) is False
    assert recon.dates_are_ingest_clock([]) is False


def test_evidence_that_names_almost_no_account():
    """THE HONEST VERSION OF GRACEFUL DEGRADATION. The engine already counts
    rather than weighting, on every corpus, silently — and a reader cannot tell
    a considered count from a weighting that quietly failed."""
    o = _only([], "account_attribution_gap", signals=fx.kg_signals())
    assert o.figures["present"] == 0
    assert o.figures["signals"] == 200
    assert o.severity == "high"


def test_a_corpus_that_does_name_its_accounts_raises_no_attribution_gap():
    assert "account_attribution_gap" not in _kinds(
        [], signals=fx.kg_signals(attributed=True))


def test_evidence_that_carries_no_figure_at_all():
    o = _only([], "monetary_coverage_gap", signals=fx.kg_signals())
    assert o.figures["present"] == 0
    assert "accounts touched" in o.what


def test_a_corpus_carrying_figures_raises_no_monetary_gap():
    assert "monetary_coverage_gap" not in _kinds(
        [], signals=fx.kg_signals(monetary=True))


def test_both_gaps_are_measured_by_one_parameterised_check():
    """"How much of this names an account" and "how much carries a figure" are
    the same question asked of two keys. The next one will be a third key, not
    a third function."""
    signals = fx.kg_signals(attributed=True)
    assert recon.signal_field_presence(
        signals, path=("properties", "account")).share == 1.0
    assert recon.signal_field_presence(
        signals, path=("properties", "amount")).share == 0.0
    assert recon.signal_field_presence(
        signals, path=("properties", "nothing_here")).present == 0


def test_a_large_row_count_resting_on_a_few_documents():
    """The row count is not a count of independent observations. Every
    corroboration rule in the pipeline reads differently once that is known."""
    o = _only([], "source_concentration", signals=fx.kg_signals(documents=4))
    assert o.figures["documents"] == 4
    assert o.figures["per_document"] == 50


def test_evidence_spread_across_many_documents_is_not_concentrated():
    assert "source_concentration" not in _kinds(
        [], signals=fx.kg_signals(documents=60))


def test_a_corpus_that_is_mostly_one_kind_of_thing():
    o = _only([], "claim_mix", signals=fx.kg_signals())
    assert o.figures["top_share"] == 1.0


def test_a_mixed_corpus_says_nothing_about_its_mix():
    assert "claim_mix" not in _kinds([], signals=fx.kg_signals(one_kind=False))


def test_none_of_the_graph_checks_need_a_table():
    """They are aggregates over rows the run already has — no new query, no new
    page, no extra round trip."""
    kinds = _kinds([], signals=fx.kg_signals())
    assert kinds == {
        "dating_unreliable", "account_attribution_gap", "monetary_coverage_gap",
        "source_concentration", "claim_mix",
    }


def test_a_corpus_too_small_to_have_a_shape_is_not_described():
    """A proportion computed from a handful of signals is not a proportion."""
    assert _kinds([], signals=fx.kg_signals(n=5)) <= {
        "dating_unreliable", "account_attribution_gap", "monetary_coverage_gap",
    }


# ─── 9. The table checks stay off tables they could not speak about ───────


def test_a_sparse_union_of_keys_is_not_a_table():
    """The forty-column shape a prose corpus produces: those columns are the
    union of keys across heterogeneous signals and each row carries a couple.
    Measured on real data at 0.032–0.111 dense."""
    assert recon.density(fx.sparse_table()) < 0.1
    assert recon.is_rectangular(fx.sparse_table()) is False


def test_every_real_spreadsheet_is_still_a_table():
    """Measured 0.878–1.000 on a real multi-source upload. An upload-shaped
    tenant must keep everything it has today."""
    for table in fx.full_pack():
        assert recon.is_rectangular(table), table.name


def test_a_prose_corpus_runs_no_table_checks_at_all():
    signals = fx.kg_signals()
    kinds = _kinds(fx.kg_tables(signals), signals=signals)
    assert not (kinds & {
        "value_columns_disagree", "coding_gap", "stage_collapse",
        "censored_periods", "concentration_divergence", "unit_value_derivable",
    })


def test_an_upload_keeps_every_table_finding_it_had():
    assert _kinds(fx.full_pack()) >= {
        "value_columns_disagree", "coding_gap", "stage_collapse",
        "concentration_divergence", "censored_periods",
    }


def test_the_gate_reads_shape_rather_than_column_names():
    """THE OBVIOUS GATE DOES NOT WORK ON REAL DATA. "Does any column look like
    an account or an amount" opens on essentially every prose tenant: the
    measured corpus carries `valuation_usd`, `raise_usd`, `ask_usd`,
    `market_size_usd_2035` and `customer_name` among its extracted keys. Shape
    is what separates the two, not vocabulary."""
    money_named = recon.make_table(
        "pm_manual:finding",
        [{"valuation_usd": 1}, {"customer_name": "x"}, {"ask_usd": 2},
         {"raise_usd": 3}, {"market_size_usd_2035": 4}],
        columns=("valuation_usd", "customer_name", "ask_usd", "raise_usd",
                 "market_size_usd_2035"),
    )
    assert any(recon._looks_monetary(c) or recon._looks_like_account(c)
               for c in money_named.columns)
    assert recon.is_rectangular(money_named) is False


def test_the_payload_reads_the_way_the_card_writes_numbers():
    """The sentence that reaches the reader, checked as a sentence. It shipped
    as "4 of 1275 signals (0.3%) names an account" — subject/verb disagreement,
    and a bare 1275 in a document that writes 1,275 everywhere else."""
    signals = fx.kg_signals(n=300)
    o = _only([], "account_attribution_gap", signals=signals)
    # The percentage sits between the noun and the verb, so the agreement is
    # asserted where it actually appears.
    assert ") name an account" in o.what
    assert "names an account" not in o.what
    assert "of 300 signals" in o.what

    with_commas = _only([], "account_attribution_gap",
                        signals=fx.kg_signals(n=1275))
    assert "of 1,275 signals" in with_commas.what


# ─── The attribution gate is not satisfied by the vendor's own name ─────────
#
# THE SECOND TOUCH POINT, AND IT DOES NOT GO THROUGH `_population`.
# `_observe_attribution` reads `properties.account` directly, so the exclusion
# that fixes reach does not reach this gate — and this gate is the guard that
# stops the engine weighting a corpus it cannot attribute.
#
# Measured on a real tenant: 7,711 of 11,402 signals carried an account, which
# is 67.6% and passes `ATTRIBUTABLE_MIN_SHARE`. 2,357 of those named the
# TENANT ITSELF, across four spellings. Excluding them the true figure is
# 5,354/11,402 = 47.0%, which fails. The proportions below are that corpus,
# scaled down.

def _attribution_corpus(n_self: int, n_real: int, n_bare: int):
    """`n_self` rows naming the tenant (in four spellings), `n_real` naming a
    real customer, `n_bare` naming nobody."""
    spellings = ("AdventureWorks", "Adventure Works", "Adventureworks", "adventureworks")
    rows = []
    for i in range(n_self):
        rows.append({"id": f"self-{i}", "kind": "finding",
                     "source_type": "communication", "content": "x",
                     "valid_at": "2026-08-01T12:00:00+00:00",
                     "properties": {"account": spellings[i % len(spellings)]}})
    for i in range(n_real):
        rows.append({"id": f"real-{i}", "kind": "finding",
                     "source_type": "communication", "content": "x",
                     "valid_at": "2026-08-01T12:00:00+00:00",
                     "properties": {"account": f"Customer {i % 40}"}})
    for i in range(n_bare):
        rows.append({"id": f"bare-{i}", "kind": "finding",
                     "source_type": "communication", "content": "x",
                     "valid_at": "2026-08-01T12:00:00+00:00",
                     "properties": {}})
    return rows


def test_the_vendors_own_name_does_not_count_as_attribution():
    """The presence count is what the gate reads. A row whose only account is
    the tenant's own name has not attributed anything."""
    signals = _attribution_corpus(n_self=2357, n_real=5354, n_bare=3691)
    assert len(signals) == 11402

    counted = recon.signal_field_presence(signals, path=("properties", "account"))
    assert counted.present == 7711 and round(counted.share, 3) == 0.676

    excluded = recon.signal_field_presence(
        signals, path=("properties", "account"),
        self_names=recon_self_keys("AdventureWorks Inc"))
    assert excluded.present == 5354, "a spelling variant survived the exclusion"
    assert round(excluded.share, 3) == 0.470


def test_the_attribution_gate_fails_once_the_vendor_is_excluded():
    """THE WHOLE POINT. The guard that stops the engine weighting an
    unattributable corpus was being satisfied by the vendor counting itself."""
    signals = _attribution_corpus(n_self=2357, n_real=5354, n_bare=3691)

    passing = recon.observe([], signals=signals).observations
    assert "account_attribution_gap" not in {o.kind for o in passing}, (
        "the fixture must PASS the gate before exclusion or this is vacuous")

    failing = recon.observe(
        [], signals=signals, self_names=recon_self_keys("AdventureWorks Inc"),
    ).observations
    gap = [o for o in failing if o.kind == "account_attribution_gap"]
    assert gap, "the gate must fail once the vendor is not counted"
    assert gap[0].figures["present"] == 5354
    assert gap[0].severity == "high"


def test_what_the_exclusion_took_out_is_counted_not_just_taken():
    """A presence count that silently drops rows renders a coverage figure the
    reader cannot reconcile against their own corpus, with no way to see that
    a deliberate exclusion is part of why it is low."""
    signals = _attribution_corpus(n_self=2357, n_real=5354, n_bare=3691)
    p = recon.signal_field_presence(
        signals, path=("properties", "account"),
        self_names=recon_self_keys("AdventureWorks Inc"))
    assert p.excluded == 2357
    # The three populations account for the whole corpus, so `excluded` is a
    # real third number and not a re-spelling of the misses.
    assert p.present + p.excluded + 3691 == p.signals == 11402
    # THE CONTROL: no name resolved, nothing excluded, and NOT absent.
    plain = recon.signal_field_presence(signals, path=("properties", "account"))
    assert plain.excluded == 0 and plain.present == 7711


def test_the_attribution_gap_says_how_many_were_the_vendors_own():
    """The number has to reach a reader, not just a dataclass."""
    signals = _attribution_corpus(n_self=2357, n_real=5354, n_bare=3691)
    gap = [o for o in recon.observe(
        [], signals=signals,
        self_names=recon_self_keys("AdventureWorks Inc")).observations
        if o.kind == "account_attribution_gap"]
    assert gap, "the gate must fail once the vendor is not counted"
    assert "2,357 name your own company" in gap[0].what
    assert gap[0].figures["self_excluded"] == 2357


def test_the_exclusion_does_not_touch_the_monetary_gap():
    """`signal_field_presence` is parameterised over the path and BOTH gaps
    use it. A company name is not a figure, and the money question must be
    measured exactly as it was."""
    signals = fx.kg_signals(monetary=True)
    before = recon.signal_field_presence(signals, path=("properties", "amount"))
    after = recon.observe(
        [], signals=signals, self_names=recon_self_keys("AdventureWorks")
    ).observations
    assert before.present > 0
    assert "monetary_coverage_gap" not in {o.kind for o in after}


def test_no_company_name_leaves_every_count_exactly_as_it_was():
    """The default path — and every caller that cannot resolve a display
    name — must be byte-identical to the previous behaviour."""
    signals = _attribution_corpus(n_self=2357, n_real=5354, n_bare=3691)
    plain = recon.signal_field_presence(signals, path=("properties", "account"))
    empty = recon.signal_field_presence(
        signals, path=("properties", "account"), self_names=frozenset())
    assert plain.present == empty.present == 7711


# ─── The per-account value map, and whether the run may weight by it ────────
#
# The map is a copy of the reader's contract rows and is never persisted; what
# the plan carries is the VERDICT it produces. Both halves are tested here:
# that the join is keyed the way the graph names accounts, and that the gate
# says no on a corpus the book cannot reach.


def _priced_signal(account: str, sid: str) -> dict:
    return {"id": sid, "kind": "sentiment", "source_type": "customer_voice",
            "content": "an assertion", "valid_at": "2026-08-01T12:00:00+00:00",
            "properties": {"account": account}}


def test_the_value_map_is_keyed_the_way_the_graph_names_accounts():
    """The contracts sheet writes `Account B` and the graph writes whatever a
    speaker said. Joining on the raw strings matches neither; one
    normalisation used on both sides is the entire value of the join."""
    from app.crucible.claims import account_key

    book = recon.account_value_map([fx.contracts()])
    assert book is not None
    assert book.source == "08_sales_data:contracts"
    assert book.field == "total_acv_usd"
    assert book.key_field == "account"
    assert book.values[account_key("account b")] == 546844
    assert book.values[account_key("Account B Inc.")] == 546844
    assert book.accounts == 8


def test_two_spellings_in_the_contracts_file_are_added_together_and_named():
    """Summing two rows into one account is the one operation here that can
    silently overstate a customer, so the raw spellings are carried."""
    rows = [{"account": a, "total_acv_usd": v} for a, v in (
        ("Northwind", 100.0), ("Northwind Inc.", 25.0),
        ("Contoso", 50.0), ("AdventureWorks", 75.0), ("Fabrikam", 10.0))]
    book = recon.account_value_map(
        [recon.make_table("08_sales_data:contracts", rows,
                          columns=("account", "total_acv_usd"))])
    from app.crucible.claims import account_key

    assert book.values[account_key("Northwind")] == 125.0
    assert book.merged == ("Northwind", "Northwind Inc.")


def test_nothing_carrying_a_per_account_value_yields_no_map():
    """The negative twin, and the normal case: no contracts, no map, and the
    run stays counted."""
    assert recon.account_value_map([fx.tickets()]) is None
    assert recon.account_value_map([]) is None


def test_a_corpus_the_book_can_price_clears_the_gate():
    """Three of four named accounts are in the book. Deliberately not two of
    four — a fixture sitting exactly on the bar is satisfied by a `>` rule and
    a `>=` rule alike, and proves neither."""
    signals = [_priced_signal(a, f"s{i}") for i, a in enumerate(
        ("Account B", "Account C", "Account D", "Someone Else"))]
    o = _only([fx.contracts()], "priceable_coverage", signals=signals)
    assert o.figures["priceable_share"] == 0.75
    assert o.figures["threshold"] == recon.WEIGHTING_MIN_PRICEABLE_SHARE
    assert o.figures["priced_accounts"] == 3
    assert o.figures["named_accounts"] == 4
    assert o.figures["book_accounts"] == 8
    assert o.severity == "medium"
    assert "weighted by the revenue behind them" in o.what


def test_a_corpus_the_book_cannot_reach_is_counted_and_says_so():
    """THE HONEST HALF, AND THE ONE THAT SHIPS FIRST. Measured on a real
    tenant the join reaches a small fraction of the accounts named; the plan
    has to say so rather than degrade to a count in silence."""
    signals = [_priced_signal("Account B", "s0")]
    signals += [_priced_signal(f"Stranger {i}", f"s{i + 1}") for i in range(24)]
    o = _only([fx.contracts()], "priceable_coverage", signals=signals)
    assert o.figures["priceable_share"] == 0.04
    assert o.severity == "high"
    assert "counted, not weighted" in o.what
    assert "4.0% of the accounts named in your evidence could be priced" in o.what


def test_the_gate_divides_by_accounts_and_not_by_how_loud_they_are():
    """THE DENOMINATOR, PINNED — and pinned on the case where the two answers
    DISAGREE, because on any fixture where they agree this asserts nothing.

    One priced account that talks twenty times, five unpriced accounts that
    speak once each. By signal share the book covers 80% and the run would
    WEIGH; by account share it covers 17% and the run COUNTS.

    Signal share is loudness-weighted, and loudness is the exact variable this
    feature exists to stop trusting — asking "may I stop ranking by mention
    count?" and answering in mention counts is circular. It also fails in the
    unsafe direction: contracted customers talk more than prospects, so the
    signal view reads systematically higher and errs toward weighting a run
    that should have counted. A false count is honest and disclosed; a false
    weight is the invariant violation."""
    signals = [_priced_signal("Account B", f"s{i}") for i in range(20)]
    signals += [_priced_signal(f"Stranger {i}", f"q{i}") for i in range(5)]
    cov = recon.priceable_coverage(
        signals, recon.account_value_map([fx.contracts()]).values)

    assert cov.signal_share == 0.8, "the fixture must actually diverge"
    assert round(cov.share, 4) == round(1 / 6, 4)
    assert cov.share < recon.WEIGHTING_MIN_PRICEABLE_SHARE < cov.signal_share

    o = _only([fx.contracts()], "priceable_coverage", signals=signals)
    assert o.figures["priceable_share"] == cov.share
    assert o.figures["priceable_signal_share"] == 0.8
    assert "counted, not weighted" in o.what, (
        "the loud priced account weighted a run that should have counted")


def test_the_signal_counts_are_still_reported_just_not_divided_by():
    """They are a true fact about the corpus and a reader wants them. Dropping
    them would trade one silence for another."""
    signals = [_priced_signal("Account B", f"s{i}") for i in range(20)]
    signals += [_priced_signal(f"Stranger {i}", f"q{i}") for i in range(5)]
    o = _only([fx.contracts()], "priceable_coverage", signals=signals)
    assert o.figures["priceable_signals"] == 20
    assert o.figures["attributed_signals"] == 25
    assert "20 of 25 attributed signals" in o.what
    assert "not what the decision above divides by" in o.what


def test_no_minimum_signal_floor_trims_the_long_tail():
    """A floor would reintroduce loudness through the back door: dropping
    one-signal accounts from the denominator quietly RAISES the share and
    walks the gate back toward the measure it just stopped using."""
    loud_only = [_priced_signal("Account B", f"s{i}") for i in range(20)]
    with_tail = loud_only + [
        _priced_signal(f"Stranger {i}", f"q{i}") for i in range(5)]
    book = recon.account_value_map([fx.contracts()]).values
    assert recon.priceable_coverage(loud_only, book).share == 1.0
    assert recon.priceable_coverage(with_tail, book).share < 0.2, (
        "a quiet unpriced account must count exactly as much as a loud one")


def test_the_priceable_share_ignores_signals_that_name_nobody():
    """The denominator is what NAMES an account, not the whole corpus — a
    share taken over every row would be a statement about attribution, which
    the attribution gate already makes."""
    signals = [_priced_signal("Account B", "s0"),
               {"id": "s1", "kind": "sentiment", "source_type": "customer_voice",
                "content": "x", "valid_at": "2026-08-01T12:00:00+00:00",
                "properties": {}}]
    cov = recon.priceable_coverage(
        signals, recon.account_value_map([fx.contracts()]).values)
    assert cov.attributed_signals == 1
    assert cov.share == 1.0


def test_the_vendors_own_name_cannot_satisfy_the_priceable_gate():
    """Same defect as the attribution gate, one join over: a contracts file
    that lists the vendor would otherwise price the vendor's own rows."""
    rows = [{"account": a, "total_acv_usd": v} for a, v in (
        ("AdventureWorks", 900.0), ("Contoso", 50.0),
        ("Fabrikam", 10.0), ("Northwind", 20.0))]
    book = recon.account_value_map(
        [recon.make_table("08_sales_data:contracts", rows,
                          columns=("account", "total_acv_usd"))])
    signals = [_priced_signal("AdventureWorks", f"s{i}") for i in range(9)]
    signals.append(_priced_signal("Contoso", "s9"))
    cov = recon.priceable_coverage(
        signals, book.values, recon_self_keys("AdventureWorks"))
    assert cov.attributed_signals == 1
    assert cov.priced_names == ("contoso",)


def test_the_gate_says_nothing_at_all_when_there_is_no_book():
    assert "priceable_coverage" not in _kinds(
        [fx.tickets()], signals=[_priced_signal("Account B", "s0")])
