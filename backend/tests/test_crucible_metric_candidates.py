"""GOAL-RESOLUTION §5 — the ask has to arrive after effort, not instead of it.

WHICH STORE. The first version of this module read `kg_signal.properties.metric`,
which is written by the DS agent's ANOMALY log (`source_type='agent_inferred'`,
one row per detected spike) and by the unfiltered LLM extractor. Neither is a
metric registry: a metric that moves smoothly writes nothing and could never be
offered, a metric that spiked twice looked like a two-point series whose
"current value" was a months-old outlier, and an extracted number could be a
competitor's, lifted out of a competitive analysis and shown to the PM as their
own. `app/db/metric_points.py` is the registry and is the only store read here.

Fixtures are SYNTHETIC per CONVENTIONS' public-repo hygiene — the repo is
public, and a real metric name carrying a real figure is a commercial
disclosure.

No network, no LLM.
"""
from __future__ import annotations

import pytest

from tests._fake_supabase import FakeSupabaseClient, reset_fake_db

from app.crucible.metric_candidates import (
    MAX_CANDIDATES,
    MIN_PERIODS,
    candidates_for_goal,
    searched_summary,
)

CID = "co-registry"

_DDL = """
CREATE TABLE IF NOT EXISTS metric_points (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    enterprise_id TEXT NOT NULL,
    metric        TEXT NOT NULL,
    period_start  TEXT NOT NULL,
    value         REAL NOT NULL,
    source        TEXT NOT NULL,
    computed_at   TEXT NOT NULL DEFAULT (datetime('now')),
    UNIQUE (enterprise_id, metric, period_start, source)
);
CREATE TABLE IF NOT EXISTS companies (
    id            TEXT PRIMARY KEY,
    kpi_tree      TEXT NOT NULL DEFAULT '{}'
);
"""


@pytest.fixture()
def db(monkeypatch):
    reset_fake_db(_DDL)
    client = FakeSupabaseClient()
    # `metric_points` and `kpi_tree` bind `require_client` at import time, so
    # patching `app.db.client` alone never reaches them.
    monkeypatch.setattr("app.db.client.require_client", lambda: client)
    monkeypatch.setattr("app.db.metric_points.require_client", lambda: client,
                        raising=False)
    return client


def _point(db, metric: str, period: str, value, *, source="amplitude",
           company=CID):
    db.table("metric_points").insert({
        "enterprise_id": company, "metric": metric, "period_start": period,
        "value": value, "source": source,
    }).execute()


def test_a_registry_series_becomes_a_candidate_with_its_live_number(db):
    for i, p in enumerate(["2026-02-02", "2026-03-02", "2026-04-06", "2026-05-04"]):
        _point(db, "weekly_signups_count", p, 4000 + i * 32)

    cands, stats = candidates_for_goal(CID, "grow weekly signups")
    assert len(cands) == 1
    c = cands[0]

    # §5 requirement 2: a live value, its freshness, its history, its home.
    assert c.key == "weekly_signups_count"
    assert c.current_value == 4000 + 3 * 32, "must be the NEWEST period"
    assert c.current_period == "2026-05-04"
    assert c.first_period == "2026-02-02"
    assert c.points == 4
    assert c.source == "amplitude"
    # §5 requirement 3, and it must not read like the old broken prose.
    assert "Picking it fixes what the run is steering by" in c.consequence
    assert "recorded, never counted" not in c.consequence
    assert stats["distinct_metrics"] == 1 and stats["registry_readable"] is True


def test_the_newest_point_wins_regardless_of_insert_order(db):
    """`list_metric_points` sorts by a NORMALISED period. The version this
    replaced sorted the raw string, which put "Sep 2025" after "Nov 2025"."""
    _point(db, "active_accounts_count", "2026-05-04", 99)
    _point(db, "active_accounts_count", "2026-02-02", 11)
    _point(db, "active_accounts_count", "2026-03-02", 22)
    c = candidates_for_goal(CID, "accounts")[0][0]
    assert c.current_value == 99 and c.current_period == "2026-05-04"
    assert c.first_period == "2026-02-02"


def test_the_label_is_a_reading_aid_and_the_key_is_what_travels(db):
    for p in ("2026-01-05", "2026-01-12"):
        _point(db, "deposit_volume_usd", p, 100)
    c = candidates_for_goal(CID, "deposits")[0][0]
    assert c.key == "deposit_volume_usd", "the raw key must survive verbatim"
    assert c.label == "Deposit volume (usd)"


def test_a_metric_with_one_point_is_not_something_to_steer_by(db):
    _point(db, "one_reading", "2026-01-05", 5)
    for p in ("2026-01-05", "2026-01-12"):
        _point(db, "real_series", p, 10)

    cands, stats = candidates_for_goal(CID, "anything")
    keys = {c.key for c in cands}
    assert "real_series" in keys
    assert "one_reading" not in keys, f"below MIN_PERIODS={MIN_PERIODS}"
    # But it was still SEARCHED — §5 req 1 shows effort, not only hits.
    assert stats["distinct_metrics"] == 2


# ── Two trackers, one metric name ────────────────────────────────────────────
#
# `ds/analyses.py` gives ClickUp and Jira the SAME metric tuple on purpose, so
# a tenant with both connected writes two `tasks_open` rows for the same week.
# That is a normal shape, and grouping on the metric name alone got it wrong in
# two ways at once.

def test_two_providers_writing_one_metric_are_two_candidates(db):
    """Grouped on the name alone they collapsed into one series whose "current
    value" was whichever row the store returned last — reporting one tracker's
    number as the company's while a disagreeing source for the same week went
    unmentioned."""
    for p in ("2026-08-03", "2026-08-10", "2026-08-17"):
        _point(db, "tasks_open", p, 40, source="clickup")
        _point(db, "tasks_open", p, 15, source="jira")

    cands, stats = candidates_for_goal(CID, "reduce open tasks")
    by_source = {c.source: c for c in cands}
    assert set(by_source) == {"clickup", "jira"}, (
        "two providers measuring the same thing are two candidates")
    assert by_source["clickup"].current_value == 40
    assert by_source["jira"].current_value == 15
    # The label has to distinguish them or the list shows one name twice with
    # different numbers and no way to tell which is which.
    assert by_source["clickup"].label != by_source["jira"].label
    assert "clickup" in by_source["clickup"].label
    # One metric NAME, two series.
    assert stats["distinct_metrics"] == 1 and stats["distinct_series"] == 2


def test_two_trackers_reading_the_same_week_is_not_a_two_point_series(db):
    """`MIN_PERIODS` counts DISTINCT PERIODS. Counting rows let two simultaneous
    readings of one week clear the bar with zero historical depth, render as
    "2 points" beside genuine multi-week series, and outrank them because the
    tie-break prefers the longer list."""
    _point(db, "tasks_open", "2026-08-17", 40, source="clickup")
    _point(db, "tasks_open", "2026-08-17", 15, source="jira")
    # A real series to be outranked by it.
    for p in ("2026-05-04", "2026-06-01", "2026-07-06"):
        _point(db, "tasks_open_real", p, 1, source="clickup")

    cands, _ = candidates_for_goal(CID, "tasks")
    keys = {(c.key, c.source) for c in cands}
    assert ("tasks_open", "clickup") not in keys, (
        "one week read twice is not a series")
    assert ("tasks_open", "jira") not in keys
    assert ("tasks_open_real", "clickup") in keys


def test_a_multi_source_metric_still_reports_its_own_span(db):
    for p in ("2026-08-03", "2026-08-10"):
        _point(db, "bugs_open", p, 7, source="clickup")
    c = next(c for c in candidates_for_goal(CID, "bugs")[0]
             if c.source == "clickup")
    assert c.points == 2
    assert c.first_period == "2026-08-03" and c.last_period == "2026-08-10"


def test_a_goal_that_matches_nothing_still_gets_something_to_point_at(db):
    """RANKED, NOT FILTERED. Filtering to a confident match would be Step 2's
    'exactly one match' adoption without the confirmation I9 requires."""
    for p in ("2026-01-05", "2026-01-12"):
        _point(db, "deposit_volume_usd", p, 1)
    cands, _ = candidates_for_goal(CID, "improve nurse scheduling satisfaction")
    assert cands, "an unmatched goal must still be able to point at something"


def test_the_best_name_match_leads(db):
    for p in ("2026-01-05", "2026-01-12", "2026-01-19"):
        _point(db, "deposit_volume_usd", p, 1)
    for p in ("2026-01-05", "2026-01-12"):
        _point(db, "churn_rate_pct", p, 2)
    cands, _ = candidates_for_goal(CID, "reduce churn")
    assert cands[0].key == "churn_rate_pct", (
        "token overlap with the goal must outrank a longer series")


def test_the_list_is_short_enough_to_be_a_decision(db):
    for m in range(MAX_CANDIDATES + 4):
        for p in ("2026-01-05", "2026-01-12"):
            _point(db, f"metric_{m}_count", p, 1)
    cands, stats = candidates_for_goal(CID, "anything")
    assert len(cands) == MAX_CANDIDATES
    assert stats["distinct_metrics"] == MAX_CANDIDATES + 4, (
        "the count searched must be the truth even when the list is capped")


def test_another_companys_metrics_never_appear(db):
    for p in ("2026-01-05", "2026-01-12"):
        _point(db, "their_secret_metric", p, 1, company="someone-else")
    cands, stats = candidates_for_goal(CID, "anything")
    assert cands == [] and stats["distinct_metrics"] == 0


def test_the_column_type_is_what_guarantees_a_measurement_is_numeric(db):
    """HONEST VERSION of a test I first wrote wrong.

    I asserted that a stored `true` renders as "no value". It does not, and it
    cannot: `metric_points.value` is `REAL NOT NULL`, so the database coerces a
    bool to 1.0 before anything reads it. The `isinstance(..., bool)` guard in
    `candidates_for_goal` is defence-in-depth against a future store, not a
    reachable branch through this one — and a test asserting the unreachable
    branch is a fixture describing a state the writer cannot produce.

    What IS worth pinning: the value that reaches the panel is a number, so the
    renderer's "absent, never zero" rule is exercised by absence of the FIELD,
    not by a non-numeric value in it."""
    for p in ("2026-01-05", "2026-01-12"):
        _point(db, "flag_metric", p, True)
    c = candidates_for_goal(CID, "flag")[0][0]
    assert isinstance(c.current_value, float), (
        "REAL NOT NULL means the panel always receives a number here")


def test_an_unreadable_registry_costs_candidates_not_the_run(db, monkeypatch):
    import app.db.metric_points as mp

    monkeypatch.setattr(mp, "list_metric_points",
                        lambda *a, **k: (_ for _ in ()).throw(RuntimeError("down")))
    cands, stats = candidates_for_goal(CID, "anything")
    assert cands == []
    assert stats["registry_readable"] is False


# ── §5 requirement 1: the search, and it must be the DEFINITION search ───────

def test_the_search_summary_reports_the_ladder_not_the_corpus(db):
    """The first version printed `plan.source_inventory` — every signal per
    source, i.e. the corpus the RUN will read. It produced lines like "8,412
    Slack and email", which the definition search never consulted, while
    omitting the KPI tree, which is the one rung it actually read. Inflating
    diligence is worse than not claiming it."""
    db.table("companies").insert({"id": CID, "kpi_tree": {}}).execute()
    for p in ("2026-01-05", "2026-01-12"):
        _point(db, "weekly_signups_count", p, 1)

    _cands, stats = candidates_for_goal(CID, "signups")
    rungs = searched_summary(CID, registry_stats=stats)
    labels = [r["rung"] for r in rungs]

    assert "your KPI tree" in labels, "the rung actually searched is missing"
    assert "your measured metrics" in labels
    # No corpus source types — those are what the RUN reads, not this search.
    assert not any("Slack" in r["rung"] for r in rungs)
    # An EMPTY rung still reports, because that is the evidence of looking.
    tree = next(r for r in rungs if r["rung"] == "your KPI tree")
    assert tree["found"] == 0 and "no metrics defined" in tree["detail"]


def test_the_search_summary_says_so_when_the_registry_is_unreadable(db):
    rungs = searched_summary(CID, registry_stats={"registry_readable": False})
    reg = next(r for r in rungs if r["rung"] == "your measured metrics")
    assert "could not be read" in reg["detail"], (
        "an unreadable rung must not report as an empty one")


def test_the_unique_key_is_what_makes_rows_and_periods_agree(db):
    """PINS THE PREMISE, not an unreachable branch.

    `MIN_PERIODS` counts distinct periods, and mutating it to count rows passes
    every test — because grouping on `(metric, source)` plus the table's unique
    key `(enterprise_id, metric, period_start, source)` makes `period_start`
    unique inside a group. That invariant is the reason the two counts agree,
    so it is what is worth asserting: if a future change to the grouping or the
    key breaks it, THIS fails and names the reason, rather than a candidate
    quietly reporting a depth it does not have.
    """
    import sqlite3

    from app.db.metric_points import list_metric_points

    for p in ("2026-08-03", "2026-08-10"):
        _point(db, "tasks_open", p, 40, source="clickup")
        _point(db, "tasks_open", p, 15, source="jira")

    # THE GUARANTEE ITSELF: a second row for the same (metric, period, source)
    # is refused by the unique key. That refusal is why rows and periods cannot
    # diverge inside a group.
    with pytest.raises(sqlite3.IntegrityError):
        _point(db, "tasks_open", "2026-08-10", 99, source="clickup")

    # And two SOURCES for one week are fine — that is the shape the grouping
    # change exists to separate.
    rows = list_metric_points(CID)
    grouped: dict[tuple[str, str], list[str]] = {}
    for r in rows:
        grouped.setdefault((r["metric"], r["source"]), []).append(r["period_start"])
    assert len(grouped) == 2, "two providers should be two series"
    for (metric, source), periods in grouped.items():
        assert len(periods) == len(set(periods)), (
            f"{metric}/{source} has a repeated period — rows and periods can "
            f"now diverge, so MIN_PERIODS must stay counted in periods")
