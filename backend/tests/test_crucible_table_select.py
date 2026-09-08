"""`app.crucible.table_select` — a goal plus a schema view, turned into
computed comparisons.

NO `_offline()` HERE. The model call is injected (`call=...`) so every test
below that exercises selection runs the real parse/validate/reject/coverage
path against a payload shaped the way a real response is shaped — never a
mocked-out `_parse`.

Pure otherwise: no DB, no network. `select_comparisons`'s real gateway path
(`_default_call`) is exercised once, with `app.graph.gateway.llm_call`
stubbed, to prove it routes through the existing gateway with the locked
model rather than opening a new client.
"""
from __future__ import annotations

from datetime import datetime, timezone

from app.crucible import evidence, table_select as ts
from app.crucible.recon import Table, band_label, make_table

_NOW = datetime(2026, 9, 8, tzinfo=timezone.utc)


# ── fixtures ──────────────────────────────────────────────────────────────


def _accounts_table() -> Table:
    """10 rows, 5 per group, hand-computable by every measure this module
    supports and each group large enough (n=5) to clear `MIN_GROUP_N` on its
    own — the dedicated min-n test below uses its own smaller fixture.

    plan_tier groups: Basic = {A1..A5}, Pro = {A6..A10}.
    churned=="Yes": Basic has A1, A3 (2 of 5, rate 0.4); Pro has A6, A8, A10
    (3 of 5, rate 0.6).
    mrr: Basic = [10, 20, 30, 40, 45] (median 30, mean 29.0); Pro =
    [100, 150, 200, 250, 300] (median 200, mean 200.0).
    """
    return make_table(
        "workbook:accounts",
        [
            {"account": "A1", "plan_tier": "Basic", "churned": "Yes", "mrr": 10},
            {"account": "A2", "plan_tier": "Basic", "churned": "No", "mrr": 20},
            {"account": "A3", "plan_tier": "Basic", "churned": "Yes", "mrr": 30},
            {"account": "A4", "plan_tier": "Basic", "churned": "No", "mrr": 40},
            {"account": "A5", "plan_tier": "Basic", "churned": "No", "mrr": 45},
            {"account": "A6", "plan_tier": "Pro", "churned": "Yes", "mrr": 100},
            {"account": "A7", "plan_tier": "Pro", "churned": "No", "mrr": 150},
            {"account": "A8", "plan_tier": "Pro", "churned": "Yes", "mrr": 200},
            {"account": "A9", "plan_tier": "Pro", "churned": "No", "mrr": 250},
            {"account": "A10", "plan_tier": "Pro", "churned": "Yes", "mrr": 300},
        ],
        columns=["account", "plan_tier", "churned", "mrr"],
    )


def _snapshot(tables) -> evidence.SchemaSnapshot:
    return evidence.schema_view(tables, today=_NOW)


def _lead_name(snapshot: evidence.SchemaSnapshot, suffix: str) -> str:
    for t in snapshot.tables:
        if t.name.endswith(suffix):
            return t.name
    raise AssertionError(f"no table ending in {suffix!r}")


# ── AC1 / rejection: malformed tuples are rejected, never repaired ─────────


def test_a_well_formed_response_produces_comparisons_and_a_declined_list():
    table = _accounts_table()
    snap = _snapshot([table])
    lead = _lead_name(snap, "accounts")

    def call(**kw):
        return {
            "comparisons": [
                {"table": lead, "dimension": "plan_tier", "outcome": "churned",
                 "measure": "rate", "level": "Yes", "why": "churn by tier"},
                {"table": lead, "dimension": "mrr", "outcome": "churned",
                 "measure": "rate", "level": "Yes", "edges": [0, 50, 350],
                 "why": "churn by size band"},
            ],
            "declined": [
                {"table": lead, "dimension": "account", "why": "an identifier, not a group"},
            ],
        }

    selection = ts.select_comparisons(
        enterprise_id="e1", goal_text="reduce churn", schema=snap,
        lead_table=lead, call=call,
    )
    assert len(selection.comparisons) == 2
    assert len(selection.declined) == 1
    assert selection.rejected == ()
    assert selection.missed_dimensions == ()
    assert selection.comparisons[0].orientation() == "plan_tier -> rate(churned == 'Yes')"


def test_a_tuple_missing_orientation_is_rejected_not_repaired():
    table = _accounts_table()
    snap = _snapshot([table])
    lead = _lead_name(snap, "accounts")

    def call(**kw):
        return {"comparisons": [
            # No `dimension` at all.
            {"table": lead, "outcome": "churned", "measure": "rate",
             "level": "Yes", "why": "missing dimension"},
        ], "declined": []}

    selection = ts.select_comparisons(
        enterprise_id="e1", goal_text="reduce churn", schema=snap,
        lead_table=lead, call=call,
    )
    assert selection.comparisons == ()
    assert len(selection.rejected) == 1
    assert "orientation" in selection.rejected[0].reason
    # Never repaired: the raw tuple is preserved exactly as returned, not
    # filled in with a guessed dimension.
    assert "dimension" not in selection.rejected[0].raw


def test_a_numeric_dimension_missing_edges_is_rejected_not_defaulted():
    table = _accounts_table()
    snap = _snapshot([table])
    lead = _lead_name(snap, "accounts")

    def call(**kw):
        return {"comparisons": [
            {"table": lead, "dimension": "mrr", "outcome": "churned",
             "measure": "rate", "level": "Yes", "why": "no edges given"},
        ], "declined": []}

    selection = ts.select_comparisons(
        enterprise_id="e1", goal_text="reduce churn", schema=snap,
        lead_table=lead, call=call,
    )
    assert selection.comparisons == ()
    assert len(selection.rejected) == 1
    assert "band edges" in selection.rejected[0].reason


def test_a_rate_measure_without_a_level_is_rejected():
    table = _accounts_table()
    snap = _snapshot([table])
    lead = _lead_name(snap, "accounts")

    def call(**kw):
        return {"comparisons": [
            {"table": lead, "dimension": "plan_tier", "outcome": "churned",
             "measure": "rate", "why": "forgot the level"},
        ], "declined": []}

    selection = ts.select_comparisons(
        enterprise_id="e1", goal_text="reduce churn", schema=snap,
        lead_table=lead, call=call,
    )
    assert selection.comparisons == ()
    assert "level" in selection.rejected[0].reason


def test_dimension_equal_to_outcome_is_rejected_as_missing_orientation():
    table = _accounts_table()
    snap = _snapshot([table])
    lead = _lead_name(snap, "accounts")

    def call(**kw):
        return {"comparisons": [
            {"table": lead, "dimension": "churned", "outcome": "churned",
             "measure": "rate", "level": "Yes", "why": "same column twice"},
        ], "declined": []}

    selection = ts.select_comparisons(
        enterprise_id="e1", goal_text="reduce churn", schema=snap,
        lead_table=lead, call=call,
    )
    assert selection.comparisons == ()
    assert "same column" in selection.rejected[0].reason


def test_an_unknown_table_is_rejected():
    table = _accounts_table()
    snap = _snapshot([table])
    lead = _lead_name(snap, "accounts")

    def call(**kw):
        return {"comparisons": [
            {"table": "not-attached", "dimension": "plan_tier", "outcome": "churned",
             "measure": "rate", "level": "Yes", "why": "wrong table"},
        ], "declined": []}

    selection = ts.select_comparisons(
        enterprise_id="e1", goal_text="reduce churn", schema=snap,
        lead_table=lead, call=call,
    )
    assert selection.comparisons == ()
    assert "unknown table" in selection.rejected[0].reason


def test_a_malformed_decline_is_also_rejected_not_silently_dropped():
    table = _accounts_table()
    snap = _snapshot([table])
    lead = _lead_name(snap, "accounts")

    def call(**kw):
        return {"comparisons": [], "declined": [{"table": lead, "dimension": "account"}]}

    selection = ts.select_comparisons(
        enterprise_id="e1", goal_text="reduce churn", schema=snap,
        lead_table=lead, call=call,
    )
    assert selection.declined == ()
    assert len(selection.rejected) == 1
    assert selection.rejected[0].kind == "declined"


def test_a_non_dict_response_produces_an_empty_selection_never_raises():
    table = _accounts_table()
    snap = _snapshot([table])
    lead = _lead_name(snap, "accounts")

    selection = ts.select_comparisons(
        enterprise_id="e1", goal_text="reduce churn", schema=snap,
        lead_table=lead, call=lambda **kw: "not a dict",
    )
    assert selection.comparisons == ()
    assert selection.declined == ()
    # Every lead-table candidate is now missed, visibly — not silently lost.
    assert set(selection.missed_dimensions) == set(
        ts.candidate_dimensions(next(t for t in snap.tables if t.name == lead)))


def test_a_call_that_raises_is_caught_and_produces_an_empty_selection():
    table = _accounts_table()
    snap = _snapshot([table])
    lead = _lead_name(snap, "accounts")

    def boom(**kw):
        raise RuntimeError("gateway exploded")

    selection = ts.select_comparisons(
        enterprise_id="e1", goal_text="reduce churn", schema=snap,
        lead_table=lead, call=boom,
    )
    assert selection.comparisons == ()
    assert selection.rejected == ()


# ── AC2: the coverage sweep names every lead-table candidate ───────────────


def test_a_candidate_dimension_never_addressed_shows_up_as_missed():
    table = _accounts_table()
    snap = _snapshot([table])
    lead = _lead_name(snap, "accounts")

    def call(**kw):
        # Addresses plan_tier and churned and account, but never mentions
        # mrr at all — the exact shape the spec's measurement describes.
        return {
            "comparisons": [
                {"table": lead, "dimension": "plan_tier", "outcome": "churned",
                 "measure": "rate", "level": "Yes", "why": "churn by tier"},
            ],
            "declined": [
                {"table": lead, "dimension": "account", "why": "an identifier"},
                {"table": lead, "dimension": "churned", "why": "already the outcome"},
            ],
        }

    selection = ts.select_comparisons(
        enterprise_id="e1", goal_text="reduce churn", schema=snap,
        lead_table=lead, call=call,
    )
    assert selection.missed_dimensions == ("mrr",)


def test_a_response_that_addresses_every_candidate_leaves_nothing_missed():
    table = _accounts_table()
    snap = _snapshot([table])
    lead = _lead_name(snap, "accounts")
    candidates = ts.candidate_dimensions(next(t for t in snap.tables if t.name == lead))
    assert set(candidates) == {"account", "plan_tier", "churned", "mrr"}

    def call(**kw):
        return {
            "comparisons": [
                {"table": lead, "dimension": "plan_tier", "outcome": "churned",
                 "measure": "rate", "level": "Yes", "why": "churn by tier"},
                {"table": lead, "dimension": "mrr", "outcome": "churned",
                 "measure": "rate", "level": "Yes", "edges": [0, 50, 350],
                 "why": "churn by size"},
            ],
            "declined": [
                {"table": lead, "dimension": "account", "why": "an identifier"},
                {"table": lead, "dimension": "churned", "why": "already the outcome"},
            ],
        }

    selection = ts.select_comparisons(
        enterprise_id="e1", goal_text="reduce churn", schema=snap,
        lead_table=lead, call=call,
    )
    assert selection.missed_dimensions == ()


# ── AC4: persist before compute, read back; second run computes identically ─


def _selected(call_calls: list) -> ts.Selection:
    table = _accounts_table()
    snap = _snapshot([table])
    lead = _lead_name(snap, "accounts")

    def call(**kw):
        call_calls.append(kw)
        return {
            "comparisons": [
                {"table": lead, "dimension": "plan_tier", "outcome": "mrr",
                 "measure": "median", "why": "typical spend by tier"},
            ],
            "declined": [
                {"table": lead, "dimension": "account", "why": "identifier"},
                {"table": lead, "dimension": "churned", "why": "not this goal"},
                {"table": lead, "dimension": "mrr", "why": "used as outcome"},
            ],
        }

    return ts.select_comparisons(
        enterprise_id="e1", goal_text="typical spend", schema=snap,
        lead_table=lead, call=call,
    )


def test_dump_and_load_selection_round_trips_exactly():
    calls: list = []
    selection = _selected(calls)
    assert len(calls) == 1

    blob = ts.dump_selection(selection)
    loaded = ts.load_selection({ts.SELECTION_KEY: blob})

    assert loaded == selection


def test_a_stored_selection_is_read_back_not_re_drawn():
    calls: list = []
    selection = _selected(calls)
    blob = ts.dump_selection(selection)

    table = _accounts_table()
    snap = _snapshot([table])
    lead = _lead_name(snap, "accounts")

    def explode(**kw):
        raise AssertionError("select_comparisons re-drew instead of reading back")

    reused = ts.select_comparisons(
        enterprise_id="e1", goal_text="typical spend", schema=snap,
        lead_table=lead, run_meta={ts.SELECTION_KEY: blob}, call=explode,
    )
    assert reused == selection


def test_computing_twice_from_the_same_stored_selection_is_identical():
    calls: list = []
    selection = _selected(calls)
    blob = ts.dump_selection(selection)
    loaded = ts.load_selection({ts.SELECTION_KEY: blob})

    table = _accounts_table()
    tables_by_name = {table.name: table}

    out1 = ts.compute_comparisons(tables_by_name, selection, today=_NOW)
    out2 = ts.compute_comparisons(tables_by_name, loaded, today=_NOW)
    assert out1 == out2


def test_load_selection_returns_none_for_an_unrecognised_version():
    assert ts.load_selection({ts.SELECTION_KEY: {"version": 999}}) is None


def test_load_selection_distinguishes_none_from_an_empty_selection():
    empty = ts.Selection(lead_table="workbook:accounts", comparisons=(),
                          declined=(), rejected=(), missed_dimensions=())
    blob = ts.dump_selection(empty)
    # An empty stored selection is a real, drawn answer — reading it back
    # must not look the same as "nothing has ever been drawn here".
    assert ts.load_selection({ts.SELECTION_KEY: blob}) == empty
    assert ts.load_selection({}) is None


# ── AC5: arithmetic is correct, and every guard fires once on purpose ──────


def test_rate_median_and_mean_match_hand_computed_values():
    table = _accounts_table()
    comparison = ts.Comparison(
        table=table.name, dimension="plan_tier", outcome="mrr",
        measure="median", level=None, edges=(), why="median spend by tier")
    computed = ts._compute_one(comparison, table, today=_NOW)
    by_group = {g.group: g.value for g in computed.groups}
    assert by_group == {"Basic": 30.0, "Pro": 200.0}

    mean_comparison = ts.Comparison(
        table=table.name, dimension="plan_tier", outcome="mrr",
        measure="mean", level=None, edges=(), why="mean spend by tier")
    mean_computed = ts._compute_one(mean_comparison, table, today=_NOW)
    mean_by_group = {g.group: g.value for g in mean_computed.groups}
    assert mean_by_group == {"Basic": 29.0, "Pro": 200.0}

    rate_comparison = ts.Comparison(
        table=table.name, dimension="plan_tier", outcome="churned",
        measure="rate", level="Yes", edges=(), why="churn rate by tier")
    rate_computed = ts._compute_one(rate_comparison, table, today=_NOW)
    rate_by_group = {g.group: g.value for g in rate_computed.groups}
    assert rate_by_group["Basic"] == 2 / 5
    assert rate_by_group["Pro"] == 3 / 5


def test_band_label_edges_are_ascending_half_open_except_the_last():
    edges = (0, 50, 350)
    assert band_label(-1, edges) is None
    assert band_label(0, edges) == "[0, 50)"
    assert band_label(49.9, edges) == "[0, 50)"
    assert band_label(50, edges) == "[50, 350]"
    assert band_label(350, edges) == "[50, 350]"
    assert band_label(351, edges) is None


def test_a_numeric_dimension_is_banded_before_grouping():
    table = _accounts_table()
    comparison = ts.Comparison(
        table=table.name, dimension="mrr", outcome="churned",
        measure="rate", level="Yes", edges=(0, 50, 350), why="churn by spend band")
    computed = ts._compute_one(comparison, table, today=_NOW)
    by_group = {g.group: g.value for g in computed.groups}
    # [0, 50) collects Basic's mrr values (all < 50) -> the same 2/5 churn
    # rate as the plan_tier comparison, by construction. [50, 350] collects
    # Pro's -> 3/5.
    assert by_group["[0, 50)"] == 2 / 5
    assert by_group["[50, 350]"] == 3 / 5


def test_the_min_group_n_guard_fires_and_says_why():
    table = make_table(
        "workbook:segments",
        [{"segment": "Tiny", "mrr": 10}, {"segment": "Tiny", "mrr": 20},
         *[{"segment": "Big", "mrr": v} for v in (30, 40, 50, 60, 70, 80)]],
        columns=["segment", "mrr"],
    )
    comparison = ts.Comparison(
        table=table.name, dimension="segment", outcome="mrr",
        measure="median", level=None, edges=(), why="median by segment")
    computed = ts._compute_one(comparison, table, today=_NOW)
    assert computed.suppressed is False  # the comparison itself still runs
    by_group = {g.group: g for g in computed.groups}
    tiny = by_group["Tiny"]
    assert tiny.suppressed is True
    assert tiny.value is None
    assert tiny.n == 2
    assert "minimum" in tiny.suppression_reason
    big = by_group["Big"]
    assert big.suppressed is False
    assert big.value == 55.0


def test_the_group_count_ceiling_guard_fires_and_says_why():
    rows = [{"account": f"A{i}", "mrr": i, "churned": "Yes" if i % 2 else "No"}
            for i in range(1, 14)]  # 13 distinct accounts, over MAX_GROUP_COUNT
    table = make_table("workbook:wide", rows, columns=["account", "mrr", "churned"])
    comparison = ts.Comparison(
        table=table.name, dimension="account", outcome="churned",
        measure="rate", level="Yes", edges=(), why="rate per account")
    computed = ts._compute_one(comparison, table, today=_NOW)
    assert computed.suppressed is True
    assert computed.groups == ()
    assert "12" in computed.suppression_reason


def test_the_null_share_ceiling_guard_fires_and_says_why():
    rows = [{"segment": "A", "mrr": 10}, {"segment": "A", "mrr": None},
            {"segment": "A", "mrr": None}, {"segment": "A", "mrr": None},
            {"segment": "B", "mrr": 20}]
    table = make_table("workbook:sparse", rows, columns=["segment", "mrr"])
    comparison = ts.Comparison(
        table=table.name, dimension="segment", outcome="mrr",
        measure="median", level=None, edges=(), why="median with sparse outcome")
    computed = ts._compute_one(comparison, table, today=_NOW)
    assert computed.suppressed is True
    assert "null-share" in computed.suppression_reason
    assert "mrr" in computed.suppression_reason


def test_a_missing_table_is_withheld_with_a_reason_and_no_as_of():
    comparison = ts.Comparison(
        table="workbook:gone", dimension="plan_tier", outcome="mrr",
        measure="median", level=None, edges=(), why="table went away")
    out = ts.compute_comparisons({}, ts.Selection(
        lead_table="workbook:accounts", comparisons=(comparison,), declined=(),
        rejected=(), missed_dimensions=()), today=_NOW)
    assert len(out) == 1
    assert out[0].suppressed is True
    assert out[0].as_of is None
    assert "not attached" in out[0].suppression_reason


# ── AC7: every computed comparison carries as_of and today ─────────────────


def test_every_computed_comparison_carries_as_of_and_today():
    table = _accounts_table()
    dated = Table(name=table.name, rows=table.rows, columns=table.columns,
                  source_type=table.source_type, as_of=_NOW,
                  as_of_rule=evidence.AS_OF_RULE_UPLOAD_TIMESTAMP)
    comparison = ts.Comparison(
        table=dated.name, dimension="plan_tier", outcome="mrr", measure="median",
        level=None, edges=(), why="median by tier")
    out = ts.compute_comparisons({dated.name: dated}, ts.Selection(
        lead_table=dated.name, comparisons=(comparison,), declined=(),
        rejected=(), missed_dimensions=()), today=_NOW)
    assert out[0].as_of == _NOW
    assert out[0].today == _NOW


# ── AC6: passthroughs reach the computed output verbatim ───────────────────


def test_passthrough_sheet_content_is_carried_through_verbatim():
    small = make_table(
        "workbook:methodology",
        [{"field": "source", "value": "CRM export"},
         {"field": "owner", "value": "Revenue Ops"},
         {"field": "definition", "value": "churned = cancelled in the billing period"}],
        columns=["field", "value"],
    )
    sheets, columns = ts.passthrough_payload([small])
    assert len(sheets) == 1
    assert sheets[0].table == small.name
    assert sheets[0].rows == small.rows
    assert columns == ()


def test_passthrough_column_content_is_carried_through_verbatim():
    long_note = (
        "Customer cited a competitor's lower per-seat price and an unresolved "
        "support escalation from the prior quarter as the deciding factors."
    )
    # 25 rows — over `PASSTHROUGH_SHEET_MAX_ROWS` (20), so this table is NOT
    # also a passthrough SHEET; it isolates the passthrough-COLUMN mark.
    rows = [
        {"account": f"A{i}", "loss_reason": "Price" if i % 2 else "Support",
         "loss_notes": long_note}
        for i in range(1, 26)
    ]
    table = make_table("workbook:losses", rows,
                        columns=["account", "loss_reason", "loss_notes"])
    sheets, columns = ts.passthrough_payload([table])
    assert sheets == ()
    assert len(columns) == 1
    assert columns[0].column == "loss_notes"
    assert columns[0].values == tuple(long_note for _ in range(25))


# ── Routes through the existing gateway with the locked model ──────────────


def test_default_call_routes_through_the_existing_gateway_with_the_locked_model(
    monkeypatch,
):
    table = _accounts_table()
    snap = _snapshot([table])
    lead = _lead_name(snap, "accounts")
    captured: dict = {}

    class _Result:
        output = {"comparisons": [], "declined": []}

    def _capture(**kwargs):
        captured.update(kwargs)
        return _Result()

    monkeypatch.setattr("app.graph.gateway.llm_call", _capture, raising=False)

    out = ts._default_call(
        enterprise_id="e1", goal_text="reduce churn", schema=snap,
        lead_table=lead, candidates=("plan_tier", "mrr"),
    )
    assert out == {"comparisons": [], "declined": []}
    assert captured["model"] == "claude-sonnet-4-6"
    assert captured["json_schema"] == ts.SELECT_SCHEMA
    assert captured["agent"] == "crucible"
    assert "reduce churn" in captured["input"]
    assert "plan_tier" in captured["input"] and "mrr" in captured["input"]
