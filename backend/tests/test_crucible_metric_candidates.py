"""GOAL-RESOLUTION §5 — the ask has to arrive after effort, not instead of it.

The shipped ask met none of §5's four mandatory requirements. What is guarded
here is the half that makes the other three possible: candidates that carry a
live value, a history, and a source, derived from the company's own data rather
than invented.

No network, no LLM. The Supabase client is the suite's SQLite fake.
"""
from __future__ import annotations

import pytest

from tests._fake_supabase import FakeSupabaseClient, reset_fake_db

from app.crucible.metric_candidates import (
    MAX_CANDIDATES,
    MIN_OBSERVATIONS,
    candidates_for_goal,
    scan_metric_observations,
)

CID = "co-metrics"


def _sig(client, i: int, *, metric=None, value=None, period=None,
         source_type="revenue", company=CID):
    props = {"customer": f"Acct{i}"}
    if metric:
        props.update({"metric": metric, "value": value, "period": period})
    client.table("kg_signal").insert({
        "id": f"m-{i:04d}", "enterprise_id": company, "kind": "metric_anomaly",
        "source_type": source_type, "content": f"observation {i}",
        "properties": props, "provenance": {"doc": "ledger"},
        "valid_at": f"{period or '2026-01'}-01T00:00:00+00:00",
        "created_at": "2026-08-19T00:00:00+00:00",
        "transaction_at": "2026-08-19T00:00:00+00:00",
    }).execute()


_DDL = """
CREATE TABLE IF NOT EXISTS kg_signal (
    id             TEXT PRIMARY KEY,
    enterprise_id  TEXT NOT NULL,
    source_id      TEXT,
    source_type    TEXT NOT NULL,
    kind           TEXT NOT NULL,
    content        TEXT NOT NULL,
    properties     TEXT NOT NULL DEFAULT '{}',
    embedding      TEXT,
    valid_at       TEXT NOT NULL,
    transaction_at TEXT NOT NULL,
    provenance     TEXT NOT NULL DEFAULT '{}',
    created_at     TEXT NOT NULL DEFAULT (datetime('now'))
);
"""


@pytest.fixture()
def db(monkeypatch):
    reset_fake_db(_DDL)
    client = FakeSupabaseClient()
    monkeypatch.setattr("app.db.client.require_client", lambda: client)
    return client


def test_a_metric_series_becomes_a_candidate_with_its_live_number(db):
    for i, period in enumerate(["2025-09", "2025-10", "2025-11", "2025-12"]):
        _sig(db, i, metric="interchange_revenue_usd",
             value=2_264_810 - i * 25_000, period=period)

    cands, stats = candidates_for_goal(CID, "grow interchange revenue")
    assert len(cands) == 1
    c = cands[0]

    # §5 requirement 2: a live value, its freshness, its history, its home.
    assert c.key == "interchange_revenue_usd"
    assert c.current_value == 2_264_810 - 3 * 25_000, "must be the NEWEST period"
    assert c.current_period == "2025-12"
    assert c.first_period == "2025-09" and c.last_period == "2025-12"
    assert c.observations == 4
    assert c.source_label, "a candidate with no home cannot be pointed at"
    # §5 requirement 3.
    assert c.consequence and "observations" in c.consequence
    assert stats["distinct_metrics"] == 1


def test_the_label_is_readable_but_the_key_is_what_travels(db):
    for i, p in enumerate(["2026-01", "2026-02"]):
        _sig(db, i, metric="deposit_volume_usd", value=100 + i, period=p)
    c = candidates_for_goal(CID, "deposits")[0][0]
    assert c.key == "deposit_volume_usd", "the raw key must survive verbatim"
    assert c.label == "Deposit volume (usd)"


def test_a_metric_seen_once_is_not_offered_as_something_to_steer_by(db):
    _sig(db, 0, metric="one_off_number", value=5, period="2026-01")
    for i, p in enumerate(["2026-01", "2026-02"], start=1):
        _sig(db, i, metric="real_series", value=10 * i, period=p)

    cands, stats = candidates_for_goal(CID, "anything")
    keys = {c.key for c in cands}
    assert "real_series" in keys
    assert "one_off_number" not in keys, f"below MIN_OBSERVATIONS={MIN_OBSERVATIONS}"
    # But it was still SEARCHED — §5 req 1 shows the effort, not just the hits.
    assert stats["distinct_metrics"] == 2


def test_a_goal_that_matches_nothing_still_gets_something_to_point_at(db):
    """RANKED, NOT FILTERED. Filtering to a confident match would be Step 2's
    'exactly one match' adoption arriving through the back door, without the
    confirmation I9 requires."""
    for i, p in enumerate(["2026-01", "2026-02"]):
        _sig(db, i, metric="deposit_volume_usd", value=1, period=p)

    cands, _ = candidates_for_goal(CID, "improve nurse scheduling satisfaction")
    assert cands, "an unmatched goal must still be able to point at something"


def test_the_best_name_match_leads(db):
    for i, p in enumerate(["2026-01", "2026-02", "2026-03"]):
        _sig(db, i, metric="deposit_volume_usd", value=1, period=p)
    for i, p in enumerate(["2026-01", "2026-02"], start=50):
        _sig(db, i, metric="churn_rate_pct", value=2, period=p)

    cands, _ = candidates_for_goal(CID, "reduce churn")
    assert cands[0].key == "churn_rate_pct", (
        "token overlap with the goal must outrank a longer series")


def test_the_list_is_short_enough_to_be_a_decision(db):
    for m in range(MAX_CANDIDATES + 4):
        for i, p in enumerate(["2026-01", "2026-02"]):
            _sig(db, m * 10 + i, metric=f"metric_{m}", value=1, period=p)
    cands, stats = candidates_for_goal(CID, "anything")
    assert len(cands) == MAX_CANDIDATES
    assert stats["distinct_metrics"] == MAX_CANDIDATES + 4, (
        "the count searched must be the truth even when the list is capped")


def test_a_signal_with_no_metric_key_is_not_a_candidate(db):
    _sig(db, 1)          # ordinary signal, no metric in properties
    _sig(db, 2)
    cands, stats = candidates_for_goal(CID, "anything")
    assert cands == []
    assert stats["metric_bearing"] == 0
    assert stats["signals_seen"] == 2, "it was still read, and says so"


def test_another_companys_metrics_never_appear(db):
    for i, p in enumerate(["2026-01", "2026-02"]):
        _sig(db, i, metric="theirs", value=1, period=p, company="someone-else")
    rows, seen = scan_metric_observations(CID)
    assert rows == [] and seen == 0


def test_a_value_that_is_not_a_number_is_absent_never_zero(db):
    """I3, at the ask. 'No value recorded' and 'the value is 0' lead to
    opposite reads of whether the metric is worth steering by."""
    for i, p in enumerate(["2026-01", "2026-02"]):
        _sig(db, i, metric="odd_metric", value="n/a", period=p)
    c = candidates_for_goal(CID, "odd")[0][0]
    assert c.current_value is None
