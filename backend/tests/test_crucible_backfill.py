"""Tests for the deterministic commercial-figure backfill (`app.crucible.backfill`)
and its audit table (`app.db.crucible_backfill_runs`).

Split into two tiers:

  * Pure-function tests on `find_dollar_figures`/`decide_for_signal` — no DB at
    all, cover the parsing pattern and the per-signal decision (R4, ambiguity,
    provenance marking).
  * `run_backfill` integration tests against the in-memory FakeSupabaseClient
    (conftest's `isolated_settings`, `kg_signal` already in the shared fake
    schema) — cover R1/R2/R3/R5/R6 end to end without touching a live DB.

Live-DB dry-run/apply/idempotency proof against real local Supabase is
reported separately (not a unit test — this repo runs no live Postgres in the
unit tier, matching every other crucible migration test's own convention).
"""
from __future__ import annotations

import importlib

import pytest

from app.crucible.backfill import (
    BACKFILL_CERTAINTY,
    decide_for_signal,
    find_dollar_figures,
)
from app.graph.extractor import _AMOUNT_ELIGIBLE_KINDS, _COMMERCIAL_CERTAINTY_VALUES

# SQLite-compatible end-state of `crucible_backfill_runs`. No FK to `companies`
# (same convention as `test_routes_crucible.py`'s local crucible DDL) — the
# fake exercises SQL semantics, not Postgres DDL.
_DDL = """
CREATE TABLE crucible_backfill_runs (
    id                 INTEGER PRIMARY KEY AUTOINCREMENT,
    company_id         TEXT NOT NULL,
    phase              TEXT NOT NULL DEFAULT 'deterministic_sweep',
    mode               TEXT NOT NULL,
    pattern_version    TEXT NOT NULL,
    status             TEXT NOT NULL DEFAULT 'running',
    examined_count     INTEGER NOT NULL DEFAULT 0,
    enriched_count     INTEGER NOT NULL DEFAULT 0,
    skipped_counts     TEXT NOT NULL DEFAULT '{}',
    error              TEXT,
    started_at         TEXT NOT NULL DEFAULT (datetime('now')),
    finished_at        TEXT,
    created_by         TEXT,
    updated_at         TEXT NOT NULL DEFAULT (datetime('now'))
);
"""


# ─── Pure parsing: find_dollar_figures ───────────────────────────────────────

def test_parses_a_properly_comma_grouped_amount():
    assert find_dollar_figures("the contract is worth $12,345,678 total") == [12345678.0]


def test_parses_a_plain_ungrouped_amount():
    assert find_dollar_figures("they pay $1500 a month") == [1500.0]


def test_applies_k_scale_suffix():
    assert find_dollar_figures("quoted at $500k for the year") == [500000.0]


def test_applies_million_word_scale_suffix():
    assert find_dollar_figures("targeting $2.4 million in ARR") == [2400000.0]


def test_ignores_a_bare_digit_with_no_dollar_sign():
    """The looser digit+k/m subset the costing pass measured as producing
    false positives (819 hits, vs 1,989 for the `$`-prefixed set) is
    deliberately excluded by the pattern itself, not filtered at runtime."""
    assert find_dollar_figures("10m users signed up this quarter") == []
    assert find_dollar_figures("about 3k accounts churned") == []


def test_the_parser_itself_still_reports_a_stated_zero():
    """`find_dollar_figures` finds "$0" as a real parse — the zero-is-not-a-
    real-amount guard lives in the shared extractor validator
    (`decide_for_signal` -> `_grounded_amount_properties`), not here. Keeping
    the parse boundary and the amount-validity boundary separate is what
    lets `decide_for_signal`'s zero-skip test prove it goes through the same
    gate ingest does, rather than a private re-implementation."""
    assert find_dollar_figures("it came down to $0 after the credit") == [0.0]


def test_a_malformed_comma_grouping_never_yields_a_clipped_wrong_number():
    """The costing pass's own probe sample showed clipping mid-number
    (`$NN,NNN,`). A number with an invalid group width ("$12,34,567" — a
    2-digit second group) must not silently resolve to a truncated prefix
    like 12.0; it is acceptable for this to find nothing, never a wrong
    figure."""
    figures = find_dollar_figures("a strange figure of $12,34,567 was mentioned")
    assert 12.0 not in figures
    assert 1234567.0 not in figures


def test_dedupes_the_same_figure_mentioned_twice():
    text = "the deal is worth $50,000 — so $50,000 total across the contract"
    assert find_dollar_figures(text) == [50000.0]


def test_returns_every_distinct_figure_present():
    text = "it's $500 a month on the starter plan or $10,000 for the enterprise tier"
    assert find_dollar_figures(text) == [500.0, 10000.0]


def test_no_dollar_figure_in_plain_prose():
    assert find_dollar_figures("the customer seemed happy with the demo") == []


# ─── decide_for_signal ───────────────────────────────────────────────────────

def test_skips_a_signal_that_already_has_an_amount():
    """R4 — never overwrite ingest-time data, even when content also parses."""
    decision = decide_for_signal(
        {"amount": 25000.0, "currency": "USD", "certainty": "quoted"},
        "the customer confirmed $25,000 for the annual plan",
    )
    assert decision.outcome == "already_has_amount"
    assert decision.new_properties is None


def test_skips_when_no_figure_is_found():
    decision = decide_for_signal({}, "no numbers mentioned in this call at all")
    assert decision.outcome == "no_figure_found"
    assert decision.new_properties is None


def test_skips_ambiguous_multiple_distinct_figures():
    decision = decide_for_signal(
        {}, "either $500 a month or $10,000 up front, they hadn't decided",
    )
    assert decision.outcome == "ambiguous_multiple_figures"
    assert decision.new_properties is None


def test_a_stated_figure_of_zero_is_skipped_not_written():
    """A parsed "$0" must never be written as a real amount — same exclusion
    the shared extractor validator applies at ingest (a stated figure of
    zero is not a real quoted amount, only ever a defaulted-looking
    placeholder). Reaches `_grounded_amount_properties`'s own `_is_number`
    zero-guard, not a check this module reimplements."""
    decision = decide_for_signal({}, "the discount brought it down to $0 this month")
    assert decision.outcome == "no_figure_found"
    assert decision.new_properties is None


def test_enriches_and_marks_provenance_distinctly():
    decision = decide_for_signal({}, "they quoted $75,000 for the full rollout")
    assert decision.outcome == "enriched"
    assert decision.new_properties["amount"] == 75000.0
    assert decision.new_properties["currency"] == "USD"
    assert decision.new_properties["certainty"] == BACKFILL_CERTAINTY


def test_backfill_certainty_marker_would_never_survive_the_real_extractor_gate():
    """The distinguishability proof: `_grounded_amount_properties` (the same
    validator ingest uses) silently drops any `certainty` outside its closed
    vocabulary. `BACKFILL_CERTAINTY` is deliberately outside it, so an
    ingest-time row can never organically end up carrying this value — a
    reader can trust it as "this came from the backfill, not extraction"."""
    assert BACKFILL_CERTAINTY not in _COMMERCIAL_CERTAINTY_VALUES
    from app.graph.extractor import _grounded_amount_properties

    validated = _grounded_amount_properties({"amount": 100, "certainty": BACKFILL_CERTAINTY})
    assert "certainty" not in validated


def test_only_amount_currency_certainty_change_on_the_new_properties_dict():
    """R6 at the decision level: every other existing property key passes
    through completely unchanged."""
    existing = {"reality_confidence": 0.8, "superseded_by": None, "basis": None}
    decision = decide_for_signal(existing, "the account is worth $9,000 this year")
    assert decision.outcome == "enriched"
    new_props = decision.new_properties
    assert new_props["reality_confidence"] == 0.8
    assert new_props["superseded_by"] is None
    # `basis` is left exactly as it was found (still None) — never guessed.
    assert new_props["basis"] is None
    assert set(new_props) - set(existing) == {"amount", "currency", "certainty"}


def test_a_non_numeric_amount_key_is_not_treated_as_already_enriched():
    """Defensive: a stray non-numeric `amount` (malformed legacy data) must
    not silently block a real backfill opportunity."""
    decision = decide_for_signal({"amount": "TBD"}, "confirmed at $8,000 flat")
    assert decision.outcome == "enriched"


# ─── run_backfill (fake-DB integration) ─────────────────────────────────────

@pytest.fixture
def backfill_env(isolated_settings):
    from tests import _fake_supabase

    _fake_supabase.get_fake_db().executescript(_DDL)
    import app.crucible.backfill as backfill_mod
    import app.db.crucible_backfill_runs as runs_mod

    importlib.reload(runs_mod)
    importlib.reload(backfill_mod)
    return backfill_mod


def _insert_signal(client, *, company_id, kind="commercial_term", content="", properties=None,
                    source_type="verbal_claim"):
    import uuid

    sig_id = str(uuid.uuid4())
    row = {
        "id": sig_id,
        "enterprise_id": company_id,
        "source_type": source_type,
        "kind": kind,
        "content": content,
        "properties": properties or {},
        "valid_at": "2026-01-01T00:00:00+00:00",
        "transaction_at": "2026-01-01T00:00:00+00:00",
    }
    client.table("kg_signal").insert(row).execute()
    return sig_id


def _get_signal(client, sig_id):
    return client.table("kg_signal").select("*").eq("id", sig_id).execute().data[0]


def test_dry_run_writes_nothing(backfill_env, isolated_settings):
    client = isolated_settings["supabase"]
    company_id = "company-a"
    sig_id = _insert_signal(
        client, company_id=company_id, content="the annual contract is $40,000",
    )

    result = backfill_env.run_backfill(company_id=company_id, apply=False)

    assert result["examined"] == 1
    assert result["enriched"] == 1
    row = _get_signal(client, sig_id)
    assert row["properties"].get("amount") is None, "dry-run must write nothing"


def test_apply_writes_amount_currency_and_certainty(backfill_env, isolated_settings):
    client = isolated_settings["supabase"]
    company_id = "company-b"
    sig_id = _insert_signal(
        client, company_id=company_id, content="the deal closed at $60,000",
    )

    result = backfill_env.run_backfill(company_id=company_id, apply=True)

    assert result["enriched"] == 1
    row = _get_signal(client, sig_id)
    assert row["properties"]["amount"] == 60000.0
    assert row["properties"]["currency"] == "USD"
    assert row["properties"]["certainty"] == BACKFILL_CERTAINTY


def test_second_run_is_idempotent_and_enriches_zero(backfill_env, isolated_settings):
    client = isolated_settings["supabase"]
    company_id = "company-c"
    _insert_signal(client, company_id=company_id, content="renewed at $22,500")

    first = backfill_env.run_backfill(company_id=company_id, apply=True)
    second = backfill_env.run_backfill(company_id=company_id, apply=True)

    assert first["enriched"] == 1
    assert second["enriched"] == 0
    assert second["skipped"]["already_has_amount"] == 1


def test_never_overwrites_an_existing_ingest_time_amount(backfill_env, isolated_settings):
    """R4 at the run level: an ingest-time figure is left exactly as-is even
    when `content` parses to a DIFFERENT figure."""
    client = isolated_settings["supabase"]
    company_id = "company-d"
    sig_id = _insert_signal(
        client, company_id=company_id,
        content="mentioned $99,000 at one point in the call",
        properties={"amount": 30000.0, "currency": "USD", "certainty": "quoted"},
    )

    result = backfill_env.run_backfill(company_id=company_id, apply=True)

    assert result["enriched"] == 0
    assert result["skipped"]["already_has_amount"] == 1
    row = _get_signal(client, sig_id)
    assert row["properties"]["amount"] == 30000.0
    assert row["properties"]["certainty"] == "quoted"


def test_ineligible_kind_is_never_examined_or_touched(backfill_env, isolated_settings):
    """Backfill eligibility mirrors ingest exactly — only
    `_AMOUNT_ELIGIBLE_KINDS` (`commercial_term`, `pricing`)."""
    client = isolated_settings["supabase"]
    company_id = "company-e"
    assert "objection" not in _AMOUNT_ELIGIBLE_KINDS
    sig_id = _insert_signal(
        client, company_id=company_id, kind="objection",
        content="they said it would cost $18,000",
    )

    result = backfill_env.run_backfill(company_id=company_id, apply=True)

    assert result["examined"] == 0
    row = _get_signal(client, sig_id)
    assert row["properties"].get("amount") is None


def test_bounded_blast_radius_only_amount_currency_certainty_change(backfill_env, isolated_settings):
    """R6, proved by diffing the full row before/after — every other column
    and every other properties key is byte-identical."""
    client = isolated_settings["supabase"]
    company_id = "company-f"
    sig_id = _insert_signal(
        client, company_id=company_id, kind="pricing", source_type="revenue",
        content="settled on $14,250 for the pilot",
        properties={"reality_confidence": 0.9},
    )
    before = _get_signal(client, sig_id)

    backfill_env.run_backfill(company_id=company_id, apply=True)

    after = _get_signal(client, sig_id)
    for col in ("content", "kind", "source_type", "enterprise_id", "valid_at"):
        assert after[col] == before[col], f"{col} must never change"
    before_props = dict(before["properties"])
    after_props = dict(after["properties"])
    assert set(after_props) - set(before_props) == {"amount", "currency", "certainty"}
    assert after_props["reality_confidence"] == before_props["reality_confidence"]


def test_ambiguous_signal_is_left_untouched_and_counted(backfill_env, isolated_settings):
    client = isolated_settings["supabase"]
    company_id = "company-g"
    sig_id = _insert_signal(
        client, company_id=company_id,
        content="either $500 monthly or $10,000 annually, undecided",
    )

    result = backfill_env.run_backfill(company_id=company_id, apply=True)

    assert result["enriched"] == 0
    assert result["skipped"]["ambiguous_multiple_figures"] == 1
    row = _get_signal(client, sig_id)
    assert row["properties"].get("amount") is None


def test_a_stated_zero_is_never_written_even_in_apply_mode(backfill_env, isolated_settings):
    client = isolated_settings["supabase"]
    company_id = "company-zero"
    sig_id = _insert_signal(
        client, company_id=company_id, content="after the credit it was $0 this cycle",
    )

    result = backfill_env.run_backfill(company_id=company_id, apply=True)

    assert result["enriched"] == 0
    assert result["skipped"]["no_figure_found"] == 1
    row = _get_signal(client, sig_id)
    assert row["properties"].get("amount") is None


def test_company_id_is_required(backfill_env):
    with pytest.raises(ValueError):
        backfill_env.run_backfill(company_id="", apply=False)


def test_run_is_scoped_to_one_company_only(backfill_env, isolated_settings):
    """R1 — a second company's eligible signal is never touched by a run
    targeting the first."""
    client = isolated_settings["supabase"]
    target = "company-h"
    other = "company-i"
    other_sig = _insert_signal(client, company_id=other, content="worth $5,000")
    _insert_signal(client, company_id=target, content="worth $7,000")

    result = backfill_env.run_backfill(company_id=target, apply=True)

    assert result["examined"] == 1
    other_row = _get_signal(client, other_sig)
    assert other_row["properties"].get("amount") is None


def test_records_an_audit_row_with_counts_and_pattern_version(backfill_env, isolated_settings):
    client = isolated_settings["supabase"]
    company_id = "company-j"
    _insert_signal(client, company_id=company_id, content="closed at $3,300")
    _insert_signal(client, company_id=company_id, content="no figure mentioned here")

    result = backfill_env.run_backfill(company_id=company_id, apply=True)

    runs = (
        client.table("crucible_backfill_runs").select("*")
        .eq("company_id", company_id).execute().data
    )
    assert len(runs) == 1
    run = runs[0]
    assert run["status"] == "completed"
    assert run["mode"] == "apply"
    assert run["pattern_version"] == result["pattern_version"]
    assert run["examined_count"] == 2
    assert run["enriched_count"] == 1
    assert run["skipped_counts"]["no_figure_found"] == 1


def test_records_failed_status_on_unexpected_error(backfill_env, isolated_settings, monkeypatch):
    client = isolated_settings["supabase"]
    company_id = "company-k"

    def _boom(*args, **kwargs):
        raise RuntimeError("simulated page failure")

    monkeypatch.setattr(backfill_env, "_page_eligible_signals", _boom)

    with pytest.raises(RuntimeError):
        backfill_env.run_backfill(company_id=company_id, apply=True)

    run = (
        client.table("crucible_backfill_runs").select("*")
        .eq("company_id", company_id).execute().data[0]
    )
    assert run["status"] == "failed"
    assert "simulated page failure" in run["error"]


def test_respects_the_limit_parameter(backfill_env, isolated_settings):
    client = isolated_settings["supabase"]
    company_id = "company-l"
    for i in range(3):
        _insert_signal(client, company_id=company_id, content=f"deal {i} worth ${1000 + i}")

    result = backfill_env.run_backfill(company_id=company_id, apply=True, limit=1)

    assert result["examined"] == 1
