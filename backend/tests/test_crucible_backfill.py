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
    SKIP_BELOW_DEAL_FLOOR,
    SKIP_IMPLAUSIBLE_MAGNITUDE,
    SKIP_NON_DEAL_CONTEXT,
    amount_distribution,
    decide_for_signal,
    decide_purge_for_signal,
    find_dollar_figures,
    scan_dollar_figures,
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


def test_a_stated_zero_is_refused_by_the_floor_that_now_subsumes_it():
    """A stated $0 used to reach `decide_for_signal` and be refused there by
    the SHARED extractor validator. The deal floor now catches it earlier,
    which is correct — $0 is below any plausible deal value — but it means
    that path no longer proves the shared gate is wired.

    So the proof moved rather than disappeared: see
    `test_the_zero_guard_is_still_the_shared_extractor_validators`, which
    asserts the same property directly instead of through a figure the floor
    would reject anyway."""
    scan = scan_dollar_figures("it came down to $0 after the credit")
    assert scan.figures == ()
    assert scan.skips == (SKIP_BELOW_DEAL_FLOOR,)


def test_the_zero_guard_is_still_the_shared_extractor_validators():
    """THE PROPERTY THE ZERO CASE WAS REALLY PROTECTING: this module must not
    privately re-implement "is this a real amount". It reuses the same
    validator ingest does, so the two can never drift about what counts."""
    from app.graph.extractor import _grounded_amount_properties

    assert _grounded_amount_properties({"amount": 0, "currency": "USD"}) == {}
    assert _grounded_amount_properties({"amount": 0.0, "currency": "USD"}) == {}
    assert "amount" in _grounded_amount_properties(
        {"amount": 5000, "currency": "USD"}
    )


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
    text = ("it's $4,000 a month on the starter plan or $10,000 for the "
            "enterprise tier")
    assert find_dollar_figures(text) == [4000.0, 10000.0]


def test_no_dollar_figure_in_plain_prose():
    assert find_dollar_figures("the customer seemed happy with the demo") == []


# ── Scale-word resolution: the exact shapes the real corpus contains ────────
#
# A staging dry run examined 871 eligible signals and would enrich 218 of
# them, in these shapes: $NM, $NNM, $N.NM, $NB, $NNK, $NNNK. A mis-parsed
# scale is a 1,000x-or-more magnitude error — "$1.5M" resolving to 1.5 or
# 1500 instead of 1,500,000 is worse than no figure at all, because unlike a
# missing figure a wrong one is invisible: it looks like data.
#
# THAT INSTINCT WAS RIGHT AND ITS SCOPE WAS TOO NARROW, which the run that
# followed proved. It asked only "is this figure resolved to the right
# magnitude", and every one of these cases passed while the sweep imported a
# fifty-billion-dollar market size as a customer-stated deal value. The
# question it never asked is the one that matters just as much: IS THIS
# FIGURE A DEAL FACT AT ALL. A valuation and a book of business are not, and
# both were asserted here as correct parses. They are now asserted as
# refusals, with the reason recorded — see the semantic-gate block below.

def test_letter_scale_suffixes_resolve_to_the_right_magnitude():
    assert find_dollar_figures("renewed at $1.5M") == [1_500_000.0]
    assert find_dollar_figures("renewed at $2M") == [2_000_000.0]
    assert find_dollar_figures("quoted $50k for the pilot") == [50_000.0]
    assert find_dollar_figures("quoted $50K for the pilot") == [50_000.0]
    assert find_dollar_figures("a $250K expansion") == [250_000.0]


def test_word_scale_suffixes_with_a_space_resolve_to_the_right_magnitude():
    assert find_dollar_figures("closed at $1.5 million ARR") == [1_500_000.0]
    assert find_dollar_figures("saved $50 thousand a year") == [50_000.0]


def test_scale_suffix_case_is_never_significant():
    assert find_dollar_figures("worth $50k") == [50_000.0]
    assert find_dollar_figures("worth $50K") == [50_000.0]
    assert find_dollar_figures("worth $50m") == [50_000_000.0]
    assert find_dollar_figures("worth $50M") == [50_000_000.0]
    assert find_dollar_figures("worth $50 thousand") == [50_000.0]
    assert find_dollar_figures("worth $50 THOUSAND") == [50_000.0]
    assert find_dollar_figures("worth $50 Million") == [50_000_000.0]
    assert find_dollar_figures("worth $50 MILLION") == [50_000_000.0]


# ── The semantic gate: is this figure a DEAL FACT? ──────────────────────────
#
# At ingest that question is answered by the model under a prompt contract —
# `amount` is attached only after it judges the item a commercial term. The
# regex has no such gate, and this is the approximation of it. Every case
# here is a REFUSAL WITH A RECORDED REASON rather than a clamp: a clamped
# figure is a wrong figure that looks like data, and there is no honest way
# to render "we shrank this number because it seemed too big".

def test_a_book_of_business_is_not_a_deal_value():
    """Previously asserted as a correct parse, at full magnitude. A book of
    business is the size of a customer's own operation, not the size of
    anything transacted with us."""
    scan = scan_dollar_figures("a $250M book of business")
    assert scan.figures == ()
    assert scan.skips == (SKIP_NON_DEAL_CONTEXT,)


def test_a_valuation_is_not_a_deal_value():
    """Also previously asserted as a correct parse. The comment above it
    worried about scale errors and never asked whether a valuation is a deal
    value at all."""
    scan = scan_dollar_figures("a $2 billion valuation")
    assert scan.figures == ()
    assert scan.skips == (SKIP_NON_DEAL_CONTEXT,)


@pytest.mark.parametrize("text", [
    "sizing the $4 billion market for this",
    "the TAM here is $30M",
    "they raised $12M in a Series B",
    "fresh funding of $8M just landed",
])
def test_market_sizing_and_fundraising_figures_are_refused_with_a_reason(text):
    scan = scan_dollar_figures(text)
    assert scan.figures == ()
    assert scan.skips == (SKIP_NON_DEAL_CONTEXT,)


def test_a_billions_scale_word_is_refused_even_with_no_stop_word_nearby():
    """The scale word alone is enough. No quoted deal in this corpus is
    billions, so the only work `b`/`billion` does is import figures about
    the market rather than about a customer."""
    scan = scan_dollar_figures("the contract came to $2 billion")
    assert scan.figures == ()
    assert scan.skips == (SKIP_IMPLAUSIBLE_MAGNITUDE,)


def test_deleting_the_billion_scale_word_never_degrades_it_to_a_bare_figure():
    """THE TRAP IN THE OBVIOUS FIX. Dropping `b`/`billion` from the scale
    table alone would leave the pattern matching a bare "$2" out of "$2
    billion" and enriching the row with 2.0 — trading an inflated figure for
    a deflated one, both wrong, and the deflated one far harder to notice.
    The scale word must still be consumed and the whole figure refused."""
    scan = scan_dollar_figures("the contract came to $2 billion")
    assert 2.0 not in scan.figures
    assert scan.figures == ()


def test_a_plainly_written_figure_above_the_ceiling_is_refused():
    """A figure written out in full carries no scale word to catch it, so
    the numeric ceiling is a separate gate from the scale-word one."""
    scan = scan_dollar_figures("the agreement is for $4,000,000,000 flat")
    assert scan.figures == ()
    assert scan.skips == (SKIP_IMPLAUSIBLE_MAGNITUDE,)


# ── The floor: the tail of a real headline sum was noise ────────────────────
#
# A live run recovered a finding whose grounded sum was rendered as
# twenty-one addends ending "+ $500 + $400 + $200 + $100 + $25". Those five
# were 24% of the figures and 0.024% of the money — a quarter of the addends
# that could not change the answer, each one another chance to be wrong.

def test_a_three_figure_amount_is_refused_as_below_the_deal_floor():
    """A line item, a credit or a per-unit price — not a deal value."""
    for text, amount in (
        ("a credit of $25 was applied", 25.0),
        ("they were billed $100 for the overage", 100.0),
        ("the adjustment came to $500", 500.0),
    ):
        scan = scan_dollar_figures(text)
        assert scan.figures == (), text
        assert scan.skips == (SKIP_BELOW_DEAL_FLOOR,), text
        assert amount not in scan.figures


def test_the_floor_sits_inside_the_gap_the_real_distribution_showed():
    """DERIVED, NOT PICKED. The largest multiplicative gap anywhere in the
    live distribution was 3,000 -> 500 (6.0x, wider than any gap in the
    head). The floor has to sit inside that break: above every figure in the
    noise cluster and below every figure in the body."""
    assert find_dollar_figures("the pilot was $3,000") == [3_000.0]
    assert find_dollar_figures("renewed at $3,500") == [3_500.0]
    assert scan_dollar_figures("a $500 credit").figures == ()
    assert scan_dollar_figures("a $400 charge").figures == ()


def test_the_smallest_accepted_figure_is_four_digits():
    """The boundary itself, both sides. A four-figure amount is a plausible
    small deal or pilot; a three-figure one is not."""
    assert find_dollar_figures("the deal was $1,000") == [1_000.0]
    scan = scan_dollar_figures("the deal was $999")
    assert scan.figures == ()
    assert scan.skips == (SKIP_BELOW_DEAL_FLOOR,)


def test_the_floor_applies_at_sweep_time_so_junk_is_never_stored(
    backfill_env, isolated_settings,
):
    """NOT A READ-TIME FILTER. Storing a figure and then declining to read it
    leaves junk in the graph for anything else that queries it, and makes the
    stored data disagree with the rendered data. The sweep simply never
    writes it."""
    client = isolated_settings["supabase"]
    company_id = "company-floor"
    sig_id = _insert_signal(
        client, company_id=company_id, content="a credit of $25 was applied",
    )

    result = backfill_env.run_backfill(company_id=company_id, apply=True)

    assert result["enriched"] == 0
    assert result["skipped"][SKIP_BELOW_DEAL_FLOOR] == 1
    assert _get_signal(client, sig_id)["properties"].get("amount") is None


def test_the_floor_skip_is_counted_in_the_funnel_not_silently_dropped(
    backfill_env, isolated_settings,
):
    """A filter whose losses are invisible reads as "this never happens"."""
    client = isolated_settings["supabase"]
    company_id = "company-floor-funnel"
    _insert_signal(client, company_id=company_id, content="a $25 credit")
    _insert_signal(client, company_id=company_id, content="a $400 adjustment")
    _insert_signal(client, company_id=company_id, content="closed at $30,000")

    result = backfill_env.run_backfill(company_id=company_id, apply=True)

    assert result["enriched"] == 1
    assert result["skipped"][SKIP_BELOW_DEAL_FLOOR] == 2
    assert result["total_skipped"] == 2
    run = (
        client.table("crucible_backfill_runs").select("*")
        .eq("company_id", company_id).execute().data[0]
    )
    assert run["skipped_counts"][SKIP_BELOW_DEAL_FLOOR] == 2


def test_the_floor_has_its_own_reason_distinct_from_the_ceilings():
    """Too small and "that is a market size" are different facts about the
    corpus. Collapsing them would hide how much the floor removes behind a
    count that already means something else."""
    assert SKIP_BELOW_DEAL_FLOOR != SKIP_IMPLAUSIBLE_MAGNITUDE
    small = scan_dollar_figures("a $25 credit")
    huge = scan_dollar_figures("the contract came to $2 billion")
    assert small.skips == (SKIP_BELOW_DEAL_FLOOR,)
    assert huge.skips == (SKIP_IMPLAUSIBLE_MAGNITUDE,)


def test_the_pattern_version_moved_so_old_runs_are_never_compared():
    """A run under the old pattern and one under this one are not
    comparable, and the version string is how a reader can tell."""
    from app.crucible.backfill import PATTERN_VERSION

    assert PATTERN_VERSION == "dollar-v3"


def test_a_large_but_plausible_deal_figure_still_parses():
    """The control. The ceiling must not be so low that it eats the real
    eight-figure shapes the corpus actually contains."""
    assert find_dollar_figures("renewed at $12M for three years") == [12_000_000.0]
    assert find_dollar_figures("the total was $99,000,000") == [99_000_000.0]


def test_the_ceiling_refuses_rather_than_clamps():
    """A clamped figure is a wrong figure wearing a real one's shape. The
    refused magnitude must not reappear anywhere in the result, at any
    size."""
    scan = scan_dollar_figures("worth $50 billion")
    assert scan.figures == ()
    assert scan.skips and all(s == SKIP_IMPLAUSIBLE_MAGNITUDE for s in scan.skips)


def test_a_stop_word_far_from_the_figure_does_not_veto_it():
    """The stop-list reads a window around the match, not the whole
    paraphrase. A figure whose own clause is clean is kept even when an
    unrelated sentence much later happens to say "market"."""
    text = (
        "they signed at $80,000 for the year. "
        + "x" * 200
        + " separately, the market for this is growing."
    )
    assert find_dollar_figures(text) == [80_000.0]


def test_the_stop_list_is_lossy_in_a_way_the_reason_count_makes_visible():
    """Named honestly rather than hidden: a real deal figure stated in the
    same breath as a market size is refused along with it. That is the
    intended trade — a missing figure can be recovered by a better pass, a
    wrong one cannot be recovered once it has been read as data — and the
    per-reason counts are what make the size of the loss visible."""
    scan = scan_dollar_figures("we closed $80,000 in a $4 billion market")
    assert scan.figures == ()
    assert SKIP_NON_DEAL_CONTEXT in scan.skips


def test_a_signal_refused_on_semantics_is_not_reported_as_no_figure_found():
    """"There was nothing here" and "there was something here and we judged
    it not a deal fact" are different facts about the corpus, and only one of
    them is a reason to improve the parser."""
    refused = decide_for_signal({}, "the company hit a $2 billion valuation")
    assert refused.outcome == SKIP_NON_DEAL_CONTEXT
    assert refused.new_properties is None

    nothing = decide_for_signal({}, "the customer seemed happy with the demo")
    assert nothing.outcome == "no_figure_found"


def test_a_surviving_figure_is_still_enriched_when_another_match_was_refused():
    """One outcome per signal. A figure that passed every gate on its own —
    including the context window around itself — is not punished for
    something at the other end of the paraphrase."""
    decision = decide_for_signal(
        {},
        "they signed at $80,000 for the year. "
        + "x" * 200
        + " separately, the market for this is growing.",
    )
    assert decision.outcome == "enriched"
    assert decision.new_properties["amount"] == 80_000.0


def test_a_bare_figure_with_no_scale_word_is_never_inflated():
    """THE NEGATIVE CASE THAT MATTERS MOST: a bare `$5` must never become
    5,000 or larger just because a scale vocabulary exists.

    The deal floor now refuses `$5` outright, and the assertion is written so
    that the floor CANNOT hide a regression here: an inflated 5,000 would sit
    comfortably above the floor and be enriched, so asserting the inflated
    values are absent still fails loudly if the scale handling ever breaks.
    Asserting only `figures == ()` would pass either way."""
    scan = scan_dollar_figures("it came to $5 total")
    assert 5_000.0 not in scan.figures
    assert 5_000_000.0 not in scan.figures
    assert scan.figures == ()
    assert scan.skips == (SKIP_BELOW_DEAL_FLOOR,)


def test_a_trailing_word_starting_with_a_scale_letter_is_never_consumed_as_a_scale():
    """"$50 monthly" / "$50 Kubernetes" / "$5 by March" each have a word right
    after the figure that HAPPENS to start with a scale letter (m/k/b). The
    scale group's own trailing `\\b` requires the match to end on a real word
    boundary, so a partial-word match ("m" out of "monthly") is rejected and
    the pattern falls back to no scale at all — never a 1,000x-or-more
    inflation from a word that was never a scale suffix."""
    for text, inflated in (
        ("billed at $50 monthly", 50_000_000.0),
        ("cost $50 Kubernetes nodes", 50_000.0),
        ("due $5 by March", 5_000_000_000.0),
    ):
        scan = scan_dollar_figures(text)
        # The 1,000x-or-more inflation is what this guards, and an inflated
        # value would clear the deal floor — so its absence is the real
        # assertion, not the empty result the floor also produces.
        assert inflated not in scan.figures, text
        assert scan.figures == (), text
        assert scan.skips == (SKIP_BELOW_DEAL_FLOOR,), text


def test_the_clipping_guard_still_refuses_a_malformed_group_with_a_scale_word_nearby():
    """Same malformed-comma-grouping guard as
    `test_a_malformed_comma_grouping_never_yields_a_clipped_wrong_number`,
    re-run with a scale word in the sentence so the scale-suffix handling
    cannot accidentally reopen the clipping hole — confirms the interaction
    between `(?!,?\\d)` and the scale group, not just the scale group alone."""
    figures = find_dollar_figures(
        "a strange figure of $12,34,567 million was mentioned"
    )
    assert 12.0 not in figures
    assert 12_000_000.0 not in figures
    assert 1_234_567.0 not in figures
    assert 1_234_567_000_000.0 not in figures


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
        {}, "either $5,000 a month or $10,000 up front, they hadn't decided",
    )
    assert decision.outcome == "ambiguous_multiple_figures"
    assert decision.new_properties is None


def test_a_stated_figure_of_zero_is_skipped_not_written():
    """A parsed "$0" must never be written as a real amount. It is now
    refused by the deal floor rather than by the shared validator downstream
    — an earlier, cheaper refusal of the same figure, for a reason that is
    also true. What must not change is that nothing is written."""
    decision = decide_for_signal({}, "the discount brought it down to $0 this month")
    assert decision.outcome == SKIP_BELOW_DEAL_FLOOR
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
        content="either $5,000 monthly or $10,000 annually, undecided",
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
    assert result["skipped"][SKIP_BELOW_DEAL_FLOOR] == 1
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


# ─── The value distribution: what the counts cannot tell you ────────────────
#
# The previous revision reported examined/enriched/skipped and nothing else,
# and every one of those numbers was CORRECT on the run that wrote a
# fifty-billion-dollar figure into a customer-stated field. Counts answer
# "did the tool do what it was told". Only the distribution answers "was
# what it wrote true", which is why it is a permanent part of the report and
# not a diagnostic someone remembers to ask for.

def test_the_distribution_is_none_when_nothing_was_minted():
    assert amount_distribution([]) is None


def test_the_distribution_reports_min_median_max_and_the_top_ten():
    dist = amount_distribution([float(n) for n in range(1, 16)])
    assert dist["count"] == 15
    assert dist["min"] == 1.0
    assert dist["max"] == 15.0
    assert dist["median"] == 8.0
    assert dist["top_10"] == [15.0, 14.0, 13.0, 12.0, 11.0, 10.0, 9.0, 8.0, 7.0, 6.0]


def test_a_run_reports_the_distribution_of_what_it_minted(backfill_env, isolated_settings):
    client = isolated_settings["supabase"]
    company_id = "company-dist"
    for amount in ("$1,000", "$50,000", "$9,000"):
        _insert_signal(client, company_id=company_id, content=f"closed at {amount}")

    result = backfill_env.run_backfill(company_id=company_id, apply=True)

    assert result["amounts"]["count"] == 3
    assert result["amounts"]["min"] == 1000.0
    assert result["amounts"]["median"] == 9000.0
    assert result["amounts"]["max"] == 50000.0


def test_a_dry_run_reports_the_distribution_too(backfill_env, isolated_settings):
    """THE READING THAT WOULD HAVE CAUGHT THE BAD RUN. A dry run is where an
    operator is supposed to see the magnitudes before anything is written, so
    withholding the distribution until `--apply` would put the number on the
    wrong side of the decision it exists to inform."""
    client = isolated_settings["supabase"]
    company_id = "company-dist-dry"
    _insert_signal(client, company_id=company_id, content="renewed at $75,000")

    result = backfill_env.run_backfill(company_id=company_id, apply=False)

    assert result["mode"] == "dry_run"
    assert result["amounts"]["max"] == 75000.0
    row = _get_signal(
        client,
        client.table("kg_signal").select("id").eq("enterprise_id", company_id)
        .execute().data[0]["id"],
    )
    assert row["properties"].get("amount") is None


def test_the_new_skip_reasons_are_counted_in_the_audit_breakdown(backfill_env, isolated_settings):
    client = isolated_settings["supabase"]
    company_id = "company-reasons"
    _insert_signal(client, company_id=company_id, content="a $2 billion valuation was floated")
    _insert_signal(client, company_id=company_id, content="the agreement is for $4,000,000,000 flat")
    _insert_signal(client, company_id=company_id, content="closed at $30,000")

    result = backfill_env.run_backfill(company_id=company_id, apply=True)

    assert result["enriched"] == 1
    assert result["skipped"][SKIP_NON_DEAL_CONTEXT] == 1
    assert result["skipped"][SKIP_IMPLAUSIBLE_MAGNITUDE] == 1
    assert result["total_skipped"] == 2
    run = (
        client.table("crucible_backfill_runs").select("*")
        .eq("company_id", company_id).execute().data[0]
    )
    assert run["skipped_counts"][SKIP_NON_DEAL_CONTEXT] == 1
    assert run["skipped_counts"][SKIP_IMPLAUSIBLE_MAGNITUDE] == 1


# ─── The undo: purge_backfilled_amounts ─────────────────────────────────────
#
# The idempotency guard that makes a re-run safe is exactly what makes a
# CORRECTED re-run useless: a row already carrying `amount` is skipped
# forever. Without an undo, the sweep's first mistake is permanent.

def test_the_purge_decision_only_matches_rows_this_sweep_minted():
    assert decide_purge_for_signal(
        {"amount": 1.0, "currency": "USD", "certainty": "quoted"}
    ) is None
    assert decide_purge_for_signal({"amount": 1.0, "currency": "USD"}) is None
    assert decide_purge_for_signal({}) is None
    assert decide_purge_for_signal(None) is None


def test_the_purge_decision_clears_exactly_the_three_keys_it_wrote():
    cleared = decide_purge_for_signal({
        "amount": 50.0, "currency": "USD", "certainty": BACKFILL_CERTAINTY,
        "basis": None, "reality_confidence": 0.8,
    })
    assert cleared == {"basis": None, "reality_confidence": 0.8}


def test_purge_dry_run_clears_nothing(backfill_env, isolated_settings):
    client = isolated_settings["supabase"]
    company_id = "company-purge-dry"
    sig_id = _insert_signal(client, company_id=company_id, content="closed at $40,000")
    backfill_env.run_backfill(company_id=company_id, apply=True)

    result = backfill_env.purge_backfilled_amounts(company_id=company_id, apply=False)

    assert result["cleared"] == 1
    assert result["amounts"]["max"] == 40000.0
    assert _get_signal(client, sig_id)["properties"]["amount"] == 40000.0


def test_purge_apply_clears_the_row_and_lets_a_re_run_repair_it(backfill_env, isolated_settings):
    """THE WHOLE REASON THE UNDO EXISTS, end to end: enrich, purge, re-run,
    and observe the second sweep actually touch the row instead of skipping
    it as already-enriched."""
    client = isolated_settings["supabase"]
    company_id = "company-purge-apply"
    sig_id = _insert_signal(client, company_id=company_id, content="closed at $40,000")
    backfill_env.run_backfill(company_id=company_id, apply=True)

    blocked = backfill_env.run_backfill(company_id=company_id, apply=True)
    assert blocked["enriched"] == 0
    assert blocked["skipped"]["already_has_amount"] == 1

    purge = backfill_env.purge_backfilled_amounts(company_id=company_id, apply=True)
    assert purge["cleared"] == 1
    props = _get_signal(client, sig_id)["properties"]
    assert "amount" not in props
    assert "currency" not in props
    assert "certainty" not in props

    repaired = backfill_env.run_backfill(company_id=company_id, apply=True)
    assert repaired["enriched"] == 1


def test_purge_never_touches_an_ingest_time_figure(backfill_env, isolated_settings):
    """The line that must never move. An ingest-time amount came off a
    customer's own verbatim-grounded words; nothing in an undo may reach
    it."""
    client = isolated_settings["supabase"]
    company_id = "company-purge-ingest"
    sig_id = _insert_signal(
        client, company_id=company_id, content="they mentioned $99,000 once",
        properties={"amount": 30000.0, "currency": "USD", "certainty": "quoted"},
    )

    result = backfill_env.purge_backfilled_amounts(company_id=company_id, apply=True)

    assert result["cleared"] == 0
    props = _get_signal(client, sig_id)["properties"]
    assert props["amount"] == 30000.0
    assert props["certainty"] == "quoted"


def test_purge_is_scoped_to_one_company(backfill_env, isolated_settings):
    """The sentinel is global and the database is shared. A predicate that
    matched on `certainty` alone would be correct-looking and would reach
    another tenant's rows."""
    client = isolated_settings["supabase"]
    target = "company-purge-target"
    other = "company-purge-other"
    other_sig = _insert_signal(client, company_id=other, content="closed at $5,000")
    _insert_signal(client, company_id=target, content="closed at $7,000")
    backfill_env.run_backfill(company_id=other, apply=True)
    backfill_env.run_backfill(company_id=target, apply=True)

    result = backfill_env.purge_backfilled_amounts(company_id=target, apply=True)

    assert result["cleared"] == 1
    other_props = _get_signal(client, other_sig)["properties"]
    assert other_props["amount"] == 5000.0
    assert other_props["certainty"] == BACKFILL_CERTAINTY


def test_purge_leaves_every_other_property_and_column_untouched(backfill_env, isolated_settings):
    client = isolated_settings["supabase"]
    company_id = "company-purge-blast"
    sig_id = _insert_signal(
        client, company_id=company_id, kind="pricing", source_type="revenue",
        content="settled on $14,250 for the pilot",
        properties={"reality_confidence": 0.9},
    )
    backfill_env.run_backfill(company_id=company_id, apply=True)
    before = _get_signal(client, sig_id)

    backfill_env.purge_backfilled_amounts(company_id=company_id, apply=True)

    after = _get_signal(client, sig_id)
    for col in ("content", "kind", "source_type", "enterprise_id", "valid_at"):
        assert after[col] == before[col], f"{col} must never change"
    assert set(before["properties"]) - set(after["properties"]) == {
        "amount", "currency", "certainty"
    }
    assert after["properties"]["reality_confidence"] == 0.9


def test_purge_requires_a_company(backfill_env):
    with pytest.raises(ValueError):
        backfill_env.purge_backfilled_amounts(company_id="", apply=False)
