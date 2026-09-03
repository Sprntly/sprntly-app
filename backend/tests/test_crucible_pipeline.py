"""Stages 4–8 — and the refutation step the Phase 0 spike paid for.

The spike proposed a finding. It was well-sourced, specific, and WRONG: every
supporting signal was an echo of one meeting rather than a pattern over months.
Only pulling the evidence in date order killed it. That is why refutation runs
INSIDE the pipeline, before anything renders — a finding that cannot survive its
own evidence is dropped with its reason, not shipped with a caveat.

No network, no DB, no LLM.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from app.crucible.pipeline import (
    ECHO_WINDOW,
    MIN_CLAIMS_PER_FINDING,
    NARRATED_DROPS,
    SIZE_BANDS,
    _rank_fractions,
    build_findings,
)
from app.crucible.types import Claim, PopulationFilter

NOW = datetime(2026, 8, 19, tzinfo=timezone.utc)


def claim(
    cid: str, *, subject="export latency", days_ago=1, accounts=("Northwind",),
    authoritative=True, strength="reported", ctype="mechanism",
    source="customer_voice", direction="neutral", assertion=None,
    artifact_id="a", magnitude=None, raw=None, artifact_type="t",
) -> Claim:
    return Claim(
        id=cid, assertion=(f"claim {cid}" if assertion is None else assertion),
        type=ctype, subject=subject,
        source_id=source, artifact_id=artifact_id, artifact_type=artifact_type,
        strength=strength, observed_at=NOW - timedelta(days=days_ago),
        authoritative=authoritative,
        population=PopulationFilter(
            segments={"accounts": tuple(accounts), "customer_side": tuple(accounts)},
            estimated_size=len(accounts) or None,
        ),
        direction=direction, magnitude=magnitude, raw=raw,
    )


def run(claims, **kw):
    return build_findings(claims, currency="accounts", now=NOW, **kw)


# ── Refutation: the step the spike paid for ──────────────────────────────────

def test_evidence_that_all_lands_in_one_window_is_refuted():
    """THE spike's failure, reproduced. Four claims looks like a pattern; four
    claims inside ten days is one conversation echoing through the corpus."""
    claims = [
        claim(f"c{i}", days_ago=d, accounts=(f"Acct {i}",))
        for i, d in enumerate([1, 2, 3, 4])
    ]
    out = run(claims)
    assert out.findings == ()
    assert len(out.rejected) == 1
    assert "echoing" in out.rejected[0].reason
    assert out.rejected[0].stopped_at == "verification"


def test_the_same_evidence_spread_over_months_survives():
    """The control. If the window check also killed real patterns it would be
    trading one failure for another."""
    claims = [
        claim(f"c{i}", days_ago=d, accounts=(f"Acct {i}",))
        for i, d in enumerate([5, 40, 90, 150])
    ]
    out = run(claims)
    assert len(out.findings) == 1
    assert out.rejected == ()


def test_a_pattern_from_one_account_is_refuted():
    """One account's situation is not a pattern across the book, however many
    times it was written down."""
    claims = [
        claim(f"c{i}", days_ago=d, accounts=("Northwind",))
        for i, d in enumerate([5, 40, 90, 150])
    ]
    out = run(claims)
    assert out.findings == ()
    assert "single account" in out.rejected[0].reason


def test_a_finding_with_no_authoritative_source_is_refuted():
    claims = [
        claim(f"c{i}", days_ago=d, accounts=(f"Acct {i}",), authoritative=False)
        for i, d in enumerate([5, 40, 90, 150])
    ]
    out = run(claims)
    assert out.findings == ()
    assert "outside its source's authority" in out.rejected[0].reason


# ── Nothing is silently dropped ──────────────────────────────────────────────

def test_a_lone_claim_is_an_anecdote_and_is_recorded_as_one():
    out = run([claim("c1")])
    assert out.findings == ()
    assert out.rejected[0].stopped_at == "clustering"
    assert str(MIN_CLAIMS_PER_FINDING - 1) in out.rejected[0].reason


def test_every_rejection_keeps_its_claim_ids_so_it_can_be_reopened():
    """The considered list is the credibility of the ranking. A reader who asks
    why something placed where it did gets real analysis resumed, not the
    one-line dismissal restated."""
    out = run([claim("c1")])
    assert out.rejected[0].claim_ids == ("c1",)


def test_count_in_equals_count_out():
    """Every cluster that entered appears in findings or rejections."""
    claims = [claim("a1", subject="alpha"), claim("a2", subject="alpha", days_ago=60,
                                                  accounts=("Other",)),
              claim("b1", subject="beta")]
    out = run(claims)
    assert len(out.findings) + len(out.rejected) == out.stats["clusters"]


# ── Sizing ───────────────────────────────────────────────────────────────────

def test_a_finding_with_no_named_account_is_unsizeable_not_zero():
    claims = [claim(f"c{i}", days_ago=d, accounts=()) for i, d in enumerate([5, 60])]
    out = run(claims)
    assert len(out.findings) == 1
    assert out.impacts[0].value is None


def test_the_goal_population_filter_excludes_accounts_outside_it():
    """Against a retention goal a finding about prospects scores zero, however
    loud it is."""
    claims = [
        claim("c1", days_ago=5, accounts=("Northwind",)),
        claim("c2", days_ago=60, accounts=("Prospecto",)),
    ]
    out = run(claims, goal_accounts=frozenset({"Northwind"}))
    assert out.impacts[0].affected_population == 1.0


def test_a_sized_finding_discloses_the_missing_value_per_account():
    """I8. Accounts-as-currency is a reach measure standing in for a value
    measure, and rendering it without that disclosure reads as a price."""
    claims = [claim(f"c{i}", days_ago=d, accounts=(f"A{i}",))
              for i, d in enumerate([5, 60])]
    out = run(claims)
    names = {p.name for p in out.impacts[0].assumed_params}
    assert "value_per_account" in names


# ── Grounded commercial figures ride alongside Impact, never inside it ───────
# `native_units` carries the evidence; `value`/`affected_population`/
# `movable_gap`/`value_per_unit` are computed EXACTLY as before this ticket —
# proving the addition is additive, not a change to how anything is sized.


def test_grounded_commercial_amounts_are_summed_into_native_units():
    claims = [
        claim("c1", days_ago=5, accounts=("Northwind",), ctype="magnitude",
              source="revenue", magnitude=100000.0, raw={"currency": "USD"}),
        claim("c2", days_ago=40, accounts=("Acme",), ctype="magnitude",
              source="revenue", magnitude=50000.0, raw={"currency": "USD"}),
    ]
    out = run(claims)
    assert len(out.findings) == 1
    units = out.impacts[0].native_units
    assert units["commercial_committed_usd"] == 150000.0
    assert units["commercial_grounded_accounts"] == 2.0
    assert units["commercial_grounded_claims"] == 2.0


def test_grounded_amounts_never_change_the_scored_impact_value():
    """The additive guarantee, checked directly: a finding's `value`/
    `affected_population`/`movable_gap`/`value_per_unit` are BYTE-IDENTICAL
    whether or not its claims happen to carry a grounded commercial figure —
    summing named figures across N accounts must never read as sizing the
    WHOLE finding, which would be exactly the extrapolation this evidence is
    forbidden from doing."""
    plain = [claim(f"c{i}", days_ago=d, accounts=(f"A{i}",))
             for i, d in enumerate([5, 60])]
    grounded = [claim(f"g{i}", days_ago=d, accounts=(f"A{i}",),
                       ctype="magnitude" if i == 0 else "mechanism",
                       source="revenue" if i == 0 else "customer_voice",
                       magnitude=100000.0 if i == 0 else None,
                       raw={"currency": "USD"} if i == 0 else None)
                for i, d in enumerate([5, 60])]
    out_plain = run(plain)
    out_grounded = run(grounded)
    assert out_plain.impacts[0].value == out_grounded.impacts[0].value
    assert (out_plain.impacts[0].affected_population
            == out_grounded.impacts[0].affected_population)
    assert out_plain.impacts[0].movable_gap == out_grounded.impacts[0].movable_gap
    assert out_plain.impacts[0].value_per_unit == out_grounded.impacts[0].value_per_unit
    assert out_plain.impacts[0].native_units == {}
    assert out_grounded.impacts[0].native_units["commercial_committed_usd"] == 100000.0


def test_a_non_usd_grounded_figure_is_counted_but_excluded_from_the_dollar_sum():
    """Currency-conservative: a claim naming a different currency is not
    silently summed into a USD figure."""
    claims = [
        claim("c1", days_ago=5, accounts=("Northwind",), ctype="magnitude",
              source="revenue", magnitude=100000.0, raw={"currency": "USD"}),
        claim("c2", days_ago=40, accounts=("Acme",), ctype="magnitude",
              source="revenue", magnitude=80000.0, raw={"currency": "EUR"}),
    ]
    out = run(claims)
    units = out.impacts[0].native_units
    assert units["commercial_committed_usd"] == 100000.0
    assert units["commercial_grounded_claims"] == 2.0


def test_no_grounded_claims_leaves_native_units_empty():
    claims = [claim(f"c{i}", days_ago=d, accounts=(f"A{i}",))
              for i, d in enumerate([5, 60])]
    out = run(claims)
    assert out.impacts[0].native_units == {}


# ── Repetition is not magnitude ──────────────────────────────────────────────
#
# The sum is deduplicated before it is taken, and that is not tidiness. One
# deal restated in five messages is five claims carrying ONE figure; adding
# them turns how often something was said into how big it is, which is
# corroboration deciding size — the single failure the wall between impact
# and confidence exists to prevent. It is only a display error while the sum
# is display-only. It stops being one the moment anything downstream reads
# the sum to order findings.


def test_one_figure_restated_by_the_same_account_is_counted_once():
    """THE DEFECT. Five messages about one $50,000 deal are not $250,000, and
    no amount of restatement can make them so."""
    claims = [
        claim(f"c{i}", days_ago=d, accounts=("Northwind",), ctype="magnitude",
              source="revenue", magnitude=50000.0, raw={"currency": "USD"})
        for i, d in enumerate([5, 20, 40, 90, 150])
    ]
    out = run(claims, goal_accounts=None)
    # One account only, so this cluster is refuted before it can be a
    # finding — the dedup is asserted on the helper directly below. This
    # case is kept at the pipeline level to pin the surrounding behaviour.
    assert out.findings == ()


def test_the_same_amount_from_two_accounts_is_two_real_figures():
    """The control that stops the fix over-correcting. Two DIFFERENT
    accounts each naming $50,000 genuinely is $100,000 of opportunity."""
    claims = [
        claim("c1", days_ago=5, accounts=("Northwind",), ctype="magnitude",
              source="revenue", magnitude=50000.0, raw={"currency": "USD"}),
        claim("c2", days_ago=40, accounts=("Acme",), ctype="magnitude",
              source="revenue", magnitude=50000.0, raw={"currency": "USD"}),
    ]
    out = run(claims)
    assert out.impacts[0].native_units["commercial_committed_usd"] == 100000.0


def test_a_restated_figure_never_inflates_the_sum_across_accounts():
    """Two accounts, one figure each, every one of them said three times.
    The sum is the two figures, not the six claims."""
    claims = []
    for i, (account, amount) in enumerate(
        [("Northwind", 50000.0), ("Acme", 30000.0)]
    ):
        for j, days in enumerate([5, 40, 120]):
            claims.append(claim(
                f"c{i}{j}", days_ago=days, accounts=(account,), ctype="magnitude",
                source="revenue", magnitude=amount, raw={"currency": "USD"},
            ))
    out = run(claims)
    units = out.impacts[0].native_units
    assert units["commercial_committed_usd"] == 80000.0
    # The CLAIM count is deliberately still the raw count: it is a statement
    # about the evidence, not about the money, and a reader must be able to
    # reconcile it against the claim list.
    assert units["commercial_grounded_claims"] == 6.0
    assert units["commercial_grounded_accounts"] == 2.0


def test_an_anonymous_restatement_of_an_attributed_figure_is_dropped():
    """Most often the same deal in a message that did not name the customer.
    Between double-counting a real figure and under-counting a duplicate,
    only one of those errors inflates."""
    claims = [
        claim("c1", days_ago=5, accounts=("Northwind",), ctype="magnitude",
              source="revenue", magnitude=50000.0, raw={"currency": "USD"}),
        claim("c2", days_ago=40, accounts=("Acme",), ctype="magnitude",
              source="revenue", magnitude=30000.0, raw={"currency": "USD"}),
        claim("c3", days_ago=90, accounts=(), ctype="magnitude",
              source="revenue", magnitude=50000.0, raw={"currency": "USD"}),
    ]
    out = run(claims)
    assert out.impacts[0].native_units["commercial_committed_usd"] == 80000.0


def test_two_anonymous_statements_of_the_same_amount_are_one_figure():
    claims = [
        claim("c1", days_ago=5, accounts=(), ctype="magnitude",
              source="revenue", magnitude=50000.0, raw={"currency": "USD"}),
        claim("c2", days_ago=60, accounts=(), ctype="magnitude",
              source="revenue", magnitude=50000.0, raw={"currency": "USD"}),
    ]
    out = run(claims)
    assert out.impacts[0].native_units["commercial_committed_usd"] == 50000.0


def test_distinct_anonymous_amounts_are_all_summed():
    claims = [
        claim("c1", days_ago=5, accounts=(), ctype="magnitude",
              source="revenue", magnitude=50000.0, raw={"currency": "USD"}),
        claim("c2", days_ago=60, accounts=(), ctype="magnitude",
              source="revenue", magnitude=30000.0, raw={"currency": "USD"}),
    ]
    out = run(claims)
    assert out.impacts[0].native_units["commercial_committed_usd"] == 80000.0


def test_the_deduped_sum_is_unchanged_by_how_many_claims_repeat_it():
    """Stated as the property rather than as an example: the sum is a
    function of the DISTINCT figures, so multiplying the number of claims
    saying each one must not move it at all."""
    def sum_for(repeats: int) -> float:
        claims = []
        for i, (account, amount) in enumerate(
            [("Northwind", 50000.0), ("Acme", 30000.0)]
        ):
            for j in range(repeats):
                claims.append(claim(
                    # Spread well past ECHO_WINDOW so the cluster survives
                    # refutation at every repeat count — the property under
                    # test is the sum, not the echo check.
                    f"c{i}{j}", days_ago=5 + 45 * (i + 2 * j),
                    accounts=(account,),
                    ctype="magnitude", source="revenue", magnitude=amount,
                    raw={"currency": "USD"},
                ))
        return run(claims).impacts[0].native_units["commercial_committed_usd"]

    assert sum_for(1) == sum_for(2) == sum_for(5) == 80000.0


# ── Committed money and list pricing are different things ───────────────────
#
# A 61-row sample of the real corpus found ONE list price — "$30,000 for 50
# users" — quoted to SIXTEEN different accounts across sixteen sales calls.
# Those are not duplicates: sixteen genuine prospects, sixteen genuine
# mentions, so deduplication never touched them. They are also not $480,000
# of anything. Summing was the wrong OPERATION for that population.


def _priced(prefix, subject, *, pairs, kind="pricing", text="a price"):
    """Claims of a given extractor kind, one per (account, amount)."""
    return [
        claim(f"{prefix}{i}", subject=subject, days_ago=5 + 45 * i,
              accounts=(account,), ctype="magnitude", source="revenue",
              magnitude=amount, raw={"currency": "USD"},
              assertion=text, artifact_type=kind)
        for i, (account, amount) in enumerate(pairs)
    ]


def test_one_price_quoted_to_many_accounts_is_never_summed():
    """THE DEFECT, in its real shape. Sixteen accounts, one $30,000 tier."""
    claims = _priced(
        "p", "alpha",
        pairs=[(f"Prospect {i}", 30000.0) for i in range(16)],
    )
    out = run(claims)
    units = out.impacts[0].native_units
    assert "commercial_committed_usd" not in units
    assert units["commercial_list_price_min"] == 30000.0
    assert units["commercial_list_price_max"] == 30000.0
    assert units["commercial_list_price_accounts"] == 16.0
    # The number that must never exist anywhere.
    assert 480000.0 not in units.values()


def test_a_list_price_never_reaches_the_committed_sum_however_often_quoted():
    for repeats in (3, 8, 16):
        claims = _priced(
            "p", "alpha",
            pairs=[(f"Prospect {i}", 30000.0) for i in range(repeats)],
        )
        units = run(claims).impacts[0].native_units
        assert "commercial_committed_usd" not in units, repeats
        assert units["commercial_list_price_max"] == 30000.0, repeats


def test_the_committed_figures_from_the_sample_are_summed():
    """The genuine `commercial_term` rows: an issued quote, an updated
    contract value, and a named pair of deals."""
    claims = [
        claim("c1", subject="alpha", days_ago=5, accounts=("Northwind",),
              ctype="magnitude", source="revenue", magnitude=9000.0,
              raw={"currency": "USD"}, artifact_type="commercial_term",
              assertion="A $9,000 quote was issued"),
        claim("c2", subject="alpha", days_ago=60, accounts=("Acme",),
              ctype="magnitude", source="revenue", magnitude=165000.0,
              raw={"currency": "USD"}, artifact_type="commercial_term",
              assertion="deals nearing closure with two accounts, together "
                        "valued at $165k"),
    ]
    units = run(claims).impacts[0].native_units
    assert units["commercial_committed_usd"] == 174000.0
    assert "commercial_list_price_min" not in units


def test_a_price_and_a_deal_in_one_finding_stay_in_separate_lines():
    """THE HARD REQUIREMENT: the two numbers must not be addable. The
    committed line must not contain the price and the price line must not
    contain the deal."""
    claims = [
        claim("c1", subject="alpha", days_ago=5, accounts=("Northwind",),
              ctype="magnitude", source="revenue", magnitude=165000.0,
              raw={"currency": "USD"}, artifact_type="commercial_term",
              assertion="deals valued at $165k"),
    ] + _priced("p", "alpha",
                pairs=[(f"Prospect {i}", 30000.0) for i in range(4)])
    units = run(claims).impacts[0].native_units
    assert units["commercial_committed_usd"] == 165000.0
    assert units["commercial_list_price_min"] == 30000.0
    # No key anywhere holds the two added together.
    assert 195000.0 not in units.values()


def test_modality_overrides_the_kind_when_a_price_wears_the_wrong_label():
    """The one signal that generalises to a tenant whose phrasing we have
    never seen: three or more distinct accounts quoted the identical figure
    is a rate card, whatever the extractor called it."""
    claims = _priced(
        "p", "alpha",
        pairs=[(f"Prospect {i}", 30000.0) for i in range(4)],
        kind="commercial_term", text="the figure came up",
    )
    units = run(claims).impacts[0].native_units
    assert "commercial_committed_usd" not in units
    assert units["commercial_list_price_max"] == 30000.0


def test_two_accounts_at_the_same_figure_are_still_committed_deals():
    """The control on modality: two negotiated deals landing on the same
    round number is a plausible coincidence, and the threshold is set above
    it deliberately."""
    claims = [
        claim(f"c{i}", subject="alpha", days_ago=5 + 45 * i,
              accounts=(acct,), ctype="magnitude", source="revenue",
              magnitude=30000.0, raw={"currency": "USD"},
              artifact_type="commercial_term", assertion="deal closed")
        for i, acct in enumerate(("Northwind", "Acme"))
    ]
    units = run(claims).impacts[0].native_units
    assert units["commercial_committed_usd"] == 60000.0
    assert "commercial_list_price_min" not in units


def test_a_list_price_phrase_beats_the_kind_for_a_single_mention():
    """Where modality is thin — a value seen once could be either — the
    phrase decides. This is the majority of distinct list prices in the
    sample: twelve of about thirteen were quoted exactly once."""
    claims = [
        claim(f"c{i}", subject="alpha", days_ago=5 + 45 * i, accounts=(a,),
              ctype="magnitude", source="revenue", magnitude=m,
              raw={"currency": "USD"}, artifact_type="commercial_term",
              assertion=t)
        for i, (a, m, t) in enumerate([
            ("Northwind", 12000.0, "the annual subscription starts at $12,000"),
            ("Acme", 400.0 * 25, "billed per seat on the standard plan"),
        ])
    ]
    units = run(claims).impacts[0].native_units
    assert "commercial_committed_usd" not in units
    assert units["commercial_list_price_min"] == 10000.0


def test_an_unclassifiable_figure_stays_out_of_the_sum():
    """Ties go to NOT committed. Every failure in this feature's history has
    been over-claiming, and a figure wrongly left out understates a total
    where one wrongly added invents money."""
    claims = _priced("p", "alpha",
                     pairs=[("Northwind", 12000.0), ("Acme", 8000.0)],
                     kind="pricing", text="a figure was mentioned")
    units = run(claims).impacts[0].native_units
    assert "commercial_committed_usd" not in units
    assert units["commercial_list_price_min"] == 8000.0
    assert units["commercial_list_price_max"] == 12000.0
    assert units["commercial_list_price_distinct"] == 2.0


def test_the_pricing_line_never_carries_a_total():
    """No total is printed and none may be computed from what is stored: two
    ends, a count of prices and a count of accounts cannot be recombined into
    a sum that means anything."""
    claims = _priced("p", "alpha",
                     pairs=[(f"P{i}", 10000.0 + 1000 * i) for i in range(5)])
    units = run(claims).impacts[0].native_units
    price_keys = {k for k in units if "list_price" in k}
    assert price_keys == {
        "commercial_list_price_min", "commercial_list_price_max",
        "commercial_list_price_distinct", "commercial_list_price_accounts",
    }
    figures = run(claims).impacts[0].grounded_figures
    assert sum(f.amount for f in figures) not in units.values()


# ── Provenance rides with the figure ─────────────────────────────────────────
#
# The backfill stamps a distinct `certainty` on any figure recovered from a
# written summary, precisely so a reader could tell it from one captured
# against a verified verbatim quote. Nothing read it, so the two were
# indistinguishable downstream — the promise was stored and never kept.


def test_the_derived_marker_matches_the_one_the_backfill_actually_writes():
    """Pinned on BOTH sides. The read path declares the string rather than
    importing the operator tool (which drags in the extractor and a DB
    client), so the only thing keeping them in step is this assertion."""
    from app.crucible.backfill import BACKFILL_CERTAINTY
    from app.crucible.pipeline import BACKFILL_CERTAINTY_MARKER

    assert BACKFILL_CERTAINTY_MARKER == BACKFILL_CERTAINTY


def test_a_figure_read_back_from_a_summary_is_marked_derived():
    claims = _dollar_claims("d", "alpha", amounts=[250000.0, 90000.0],
                            derived=True)
    out = run(claims)
    units = out.impacts[0].native_units
    assert units["commercial_committed_usd"] == 340000.0
    assert units["commercial_committed_usd_derived"] == 340000.0
    assert all(f.derived for f in out.impacts[0].grounded_figures)


def test_a_quoted_figure_carries_no_derived_portion_at_all():
    """Proportionate means silent when there is nothing to hedge — the key is
    absent rather than present at zero, so a renderer cannot accidentally
    hedge a fully quoted figure."""
    claims = _dollar_claims("d", "alpha", amounts=[250000.0, 90000.0])
    out = run(claims)
    units = out.impacts[0].native_units
    assert units["commercial_committed_usd"] == 340000.0
    assert "commercial_committed_usd_derived" not in units


def test_only_the_derived_portion_of_a_mixed_sum_is_marked():
    quoted = _dollar_claims("q", "alpha", amounts=[250000.0])
    derived = _dollar_claims("d", "alpha", amounts=[90000.0], derived=True)
    # Same subject, so they cluster into one finding; dates already spread.
    derived = [claim(
        "d9", subject="alpha", days_ago=200, accounts=(), ctype="magnitude",
        source="revenue", magnitude=90000.0,
        raw={"currency": "USD", "certainty": "derived-from-summary"},
    )]
    out = run(quoted + derived)
    units = out.impacts[0].native_units
    assert units["commercial_committed_usd"] == 340000.0
    assert units["commercial_committed_usd_derived"] == 90000.0


def test_the_same_figure_seen_both_ways_keeps_the_stronger_provenance():
    """If any claim carried this money against a verified quote, it IS quoted
    money. Hedging it because a summary restated it would understate what we
    know."""
    claims = [
        claim("c1", subject="alpha", days_ago=5, accounts=("Northwind",),
              ctype="magnitude", source="revenue", magnitude=50000.0,
              raw={"currency": "USD", "certainty": "derived-from-summary"}),
        claim("c2", subject="alpha", days_ago=60, accounts=("Northwind",),
              ctype="magnitude", source="revenue", magnitude=50000.0,
              raw={"currency": "USD", "certainty": "quoted"}),
        claim("c3", subject="alpha", days_ago=120, accounts=("Acme",),
              ctype="magnitude", source="revenue", magnitude=30000.0,
              raw={"currency": "USD"}),
    ]
    out = run(claims)
    units = out.impacts[0].native_units
    assert units["commercial_committed_usd"] == 80000.0
    assert "commercial_committed_usd_derived" not in units


# ── Cross-finding identity: the reason figures travel as identities ─────────

def test_the_figures_behind_the_sum_travel_with_the_impact():
    """The sum alone cannot be deduplicated a second time. Carrying the
    identities is what lets a consumer summing ACROSS findings apply the same
    rule again — see `recommend._quoted_money_toward_target`."""
    claims = _dollar_claims("d", "alpha", amounts=[250000.0, 90000.0])
    out = run(claims)
    figures = out.impacts[0].grounded_figures
    assert sorted(f.amount for f in figures) == [90000.0, 250000.0]
    assert sum(f.amount for f in figures) == (
        out.impacts[0].native_units["commercial_committed_usd"]
    )


def test_a_figure_identity_never_carries_the_account_name():
    """These ride on scored objects that get logged and diffed. Deduplication
    needs to know two figures belong to the same customer; nothing downstream
    needs to know which customer."""
    claims = [
        claim(f"c{i}", subject="alpha", days_ago=d, accounts=(a,),
              ctype="magnitude", source="revenue", magnitude=m,
              raw={"currency": "USD"})
        for i, (d, a, m) in enumerate(
            [(5, "Northwind Trading", 50000.0), (60, "Acme Corp", 30000.0)]
        )
    ]
    out = run(claims)
    keys = {f.account_key for f in out.impacts[0].grounded_figures}
    assert len(keys) == 2
    blob = repr(out.impacts[0])
    assert "Northwind" not in blob and "Acme" not in blob


def test_the_account_key_is_stable_across_processes():
    """Reproducibility is the claim this engine makes against asking a
    general model the same question, so the key cannot come from Python's own
    per-process-salted `hash()`."""
    import subprocess
    import sys

    from app.crucible.pipeline import _account_key

    here = _account_key(("Northwind Trading", "Acme Corp"))
    out = subprocess.run(
        [sys.executable, "-c",
         "from app.crucible.pipeline import _account_key;"
         "print(_account_key(('Northwind Trading', 'Acme Corp')))"],
        capture_output=True, text=True, check=True,
    )
    assert out.stdout.strip() == here


# ── The ordinal size band ────────────────────────────────────────────────────
#
# A finding carrying a real quoted figure but no named account scored
# `value=None` and sorted below everything we could size, however trivially —
# findings holding actual money ranked last. Adding the dollars to `value` is
# worse than the disease: `value` is denominated in accounts here, so dollars
# would win by six orders of magnitude and naming any figure at all would beat
# every reach-based finding in the corpus. So the comparison is ORDINAL and
# each finding competes in its own currency.


def _dollar_claims(prefix, subject, *, amounts, accounts=None, derived=False):
    """One finding's worth of quoted-figure claims.

    NO NAMED ACCOUNTS BY DEFAULT, and that is what makes these tests mean
    anything. A claim that names an account puts its finding in the REACH
    population too, so the band would come back 4 from the reach side and a
    test of dollar banding would pass without ever exercising it.
    """
    return [
        claim(f"{prefix}{i}", subject=subject, days_ago=5 + 45 * i,
              accounts=(accounts[i],) if accounts else (),
              ctype="magnitude", source="revenue",
              magnitude=amount,
              raw={"currency": "USD",
                   **({"certainty": "derived-from-summary"} if derived else {})})
        for i, amount in enumerate(amounts)
    ]


def _reach_claims(prefix, subject, *, accounts):
    """One finding's worth of reach claims: ALWAYS exactly two, sharing
    `accounts` between them.

    Two rather than one-per-account so that a finding's REACH can be varied
    without also varying how many claims support it. Claim count feeds
    confidence, and confidence is the final tie-break in ordering — so a
    fixture that scales accounts and claims together confounds "did reach
    move this finding" with "did confidence move this finding", and an
    ordering assertion built on it proves neither.
    """
    accounts = tuple(accounts)
    assert len(accounts) >= 2, "a single-account cluster is refuted, not ranked"
    half = max(1, len(accounts) // 2)
    return [
        claim(f"{prefix}{i}", subject=subject, days_ago=5 + 45 * i, accounts=group)
        for i, group in enumerate((accounts[:half], accounts[half:]))
    ]


def _by_label(out):
    return {f.label: (f, im) for f, im in zip(out.findings, out.impacts)}


# ── The ranking function itself ──────────────────────────────────────────────

def test_an_empty_population_ranks_nothing():
    assert _rank_fractions([]) == {}


def test_the_only_member_of_a_population_sits_at_the_top_of_it():
    """Deliberate, not a degenerate case: a lone quoted figure IS the largest
    quoted figure in the run. The rank says only that — never that the figure
    is large in absolute terms."""
    assert _rank_fractions([42.0]) == {42.0: 1.0}


def test_equal_sizes_always_share_a_rank():
    """Ordering has to be reproducible run over run, which is the claim this
    engine makes against asking a general model the same question. Two
    findings of identical size must never be separated by an accident of
    iteration order."""
    ranks = _rank_fractions([5.0, 5.0, 5.0, 100.0])
    assert len(set(ranks.values())) == 2
    assert ranks[5.0] == 0.75
    assert ranks[100.0] == 1.0


def test_the_rank_never_falls_as_the_value_rises():
    """Monotonicity is what makes reach-only ordering provably unchanged: if
    rank never disagrees with value, sorting on rank IS sorting on value for
    everything measured in the same currency."""
    values = [1.0, 3.0, 7.0, 7.0, 12.0, 40.0, 900.0]
    ranks = _rank_fractions(values)
    ordered = [ranks[v] for v in sorted(set(values))]
    assert ordered == sorted(ordered)
    assert min(ordered) > 0.0 and max(ordered) == 1.0


def test_the_reported_band_is_the_quartile_of_the_underlying_rank():
    """The band is DERIVED, never stored beside the rank, so the number a
    reader sees and the number that sorts cannot drift apart."""
    from app.crucible.types import ImpactInputs

    def band(rank):
        return ImpactInputs(currency="accounts", affected_population=None,
                            movable_gap=None, value_per_unit=None,
                            size_rank=rank).size_band

    assert band(None) is None
    assert band(1.0) == SIZE_BANDS
    assert band(0.76) == 4
    assert band(0.75) == 3
    assert band(0.5) == 2
    assert band(0.25) == 1
    # Clamped at both ends: a rank of exactly 0 is not band 0, and float
    # drift just past 1.0 is not band 5.
    assert band(0.0) == 1
    assert band(1.0000000001) == SIZE_BANDS


# ── I1's sibling: the band reads DEDUPED AMOUNTS, never claim counts ─────────
#
# This is why the deduplication had to land first. The band is the one number
# in this pipeline computed by comparing findings against each other, so it is
# the one place corroboration could re-enter size without touching a field
# named like corroboration: if the sum it reads grew with restatement, then
# "said more often" would band higher, and impact would be reading agreement.


def test_the_band_is_identical_for_a_figure_stated_once_and_repeatedly():
    """THE GUARD. Two findings holding the SAME money — one from two claims,
    one from six — must band identically. Any difference is corroboration
    deciding size."""
    quiet = _dollar_claims("q", "alpha", amounts=[10000.0, 20000.0])
    loud = _dollar_claims("l", "beta", amounts=[
        10000.0, 10000.0, 10000.0, 20000.0, 20000.0, 20000.0,
    ])
    # A third, larger finding so the dollar population is not degenerate and
    # the shared band is a real position rather than "everyone is band 4".
    other = _dollar_claims("o", "gamma", amounts=[900000.0, 800000.0])
    out = run(quiet + loud + other)
    found = _by_label(out)
    quiet_i, loud_i = found["alpha"][1], found["beta"][1]
    # Same money, three times the agreement about it.
    assert quiet_i.native_units["commercial_committed_usd"] == 30000.0
    assert loud_i.native_units["commercial_committed_usd"] == 30000.0
    assert quiet_i.native_units["commercial_grounded_claims"] == 2.0
    assert loud_i.native_units["commercial_grounded_claims"] == 6.0
    assert quiet_i.size_band == loud_i.size_band
    assert quiet_i.size_band < found["gamma"][1].size_band


def test_a_bigger_sum_bands_above_a_louder_one():
    """The direction that proves the band reads MONEY. The finding with more
    money and less agreement must band above the finding with less money and
    more agreement — the exact inversion a corroboration-contaminated sizer
    would get backwards."""
    big_quiet = _dollar_claims("b", "alpha", amounts=[400000.0, 500000.0])
    small_loud = _dollar_claims("s", "beta", amounts=[
        500.0, 500.0, 500.0, 600.0, 600.0, 600.0,
    ])
    out = run(big_quiet + small_loud)
    found = _by_label(out)
    big, small = found["alpha"][1], found["beta"][1]
    assert big.native_units["commercial_committed_usd"] == 900000.0
    assert small.native_units["commercial_committed_usd"] == 1100.0
    assert big.native_units["commercial_grounded_claims"] == 2.0
    assert small.native_units["commercial_grounded_claims"] == 6.0
    assert big.size_band > small.size_band


# ── A quoted figure with no named account can finally rank ───────────────────

def test_a_figure_only_finding_is_banded_and_still_honestly_unsized():
    """Both halves matter. It BANDS, so it can rank at all. Its `value` stays
    `None`, because we did not measure its reach and saying we did would be a
    fabricated number wearing a quoted figure's credibility."""
    claims = [
        claim(f"c{i}", subject="alpha", days_ago=d, accounts=(),
              ctype="magnitude", source="revenue", magnitude=m,
              raw={"currency": "USD"})
        for i, (d, m) in enumerate([(5, 250000.0), (60, 90000.0)])
    ]
    out = run(claims)
    assert len(out.findings) == 1
    impact = out.impacts[0]
    assert impact.value is None
    assert impact.affected_population is None
    assert impact.size_band is not None
    assert impact.native_units["commercial_committed_usd"] == 340000.0


def test_each_finding_is_banded_against_its_own_currencys_peers():
    """D2 in one assertion. A tiny reach finding is the top of the REACH
    population and a large one the top of the DOLLAR population; both land in
    the top band, because "biggest of its kind" is the comparable statement
    and their raw numbers are not."""
    reach = _reach_claims("r", "alpha", accounts=("A1", "A2"))
    dollars = _dollar_claims("d", "beta", amounts=[250000.0, 90000.0])
    out = run(reach + dollars)
    found = _by_label(out)
    assert found["alpha"][1].value == 2.0
    assert found["alpha"][1].size_band == SIZE_BANDS
    assert found["beta"][1].value is None
    assert found["beta"][1].size_band == SIZE_BANDS


def test_carrying_a_figure_can_lift_a_finding_and_never_demotes_one():
    """A finding in BOTH populations takes the higher of its two bands.
    Evidence must never cost a finding its position: a reach finding that
    also happens to carry a quoted figure cannot rank below the identical
    finding without one.

    `alpha` and `beta` have the SAME reach (two accounts each) and only
    `alpha` carries a figure; `gamma` has more reach than either, so the
    reach population is not degenerate and the two-account findings do not
    start at the top band."""
    with_figure = _dollar_claims(
        "w", "alpha", amounts=[900000.0, 800000.0], accounts=("A1", "A2"),
    )
    plain = _reach_claims("p", "beta", accounts=("B1", "B2"))
    wider = _reach_claims("g", "gamma", accounts=("C1", "C2", "C3", "C4"))
    out = run(with_figure + plain + wider)
    found = _by_label(out)
    assert found["alpha"][1].value == found["beta"][1].value == 2.0
    assert found["beta"][1].size_band < SIZE_BANDS, "reach population is not degenerate"
    assert found["alpha"][1].size_band >= found["beta"][1].size_band
    assert found["alpha"][1].size_band == SIZE_BANDS, "lifted by its own figure"


# ── What the band must NOT change ────────────────────────────────────────────

def test_reach_only_ordering_is_unchanged_by_the_band():
    """The promise made to every finding with no figure. Because the band is
    monotone in reach, sorting reach findings by band and then by value is the
    same order as sorting them by value alone."""
    claims = []
    for n, subject in enumerate(("alpha", "beta", "gamma", "delta")):
        # 2..5 accounts, so the reach values are 2.0, 3.0, 4.0, 5.0 and every
        # cluster clears the two-claim / two-account bars.
        claims.extend(_reach_claims(
            subject[0], subject,
            accounts=tuple(f"{subject}-{i}" for i in range(n + 2)),
        ))
    out = run(claims)
    values = [im.value for im in out.impacts]
    assert len(values) == 4
    assert values == sorted(values, reverse=True)
    assert values == [5.0, 4.0, 3.0, 2.0]


def test_an_unbanded_finding_still_sorts_last_and_is_never_dropped():
    """No figure and no measured reach is genuinely unrankable. It keeps its
    place at the end of the list — never removed, never rendered as zero."""
    sized = _reach_claims("s", "alpha", accounts=("A1", "A2"))
    unsized = [
        claim(f"u{i}", subject="beta", days_ago=d, accounts=())
        for i, d in enumerate([5, 60])
    ]
    out = run(sized + unsized)
    assert len(out.findings) == 2
    assert out.impacts[-1].value is None
    assert out.impacts[-1].size_band is None
    assert out.findings[-1].label == "beta"


def test_ordering_does_not_change_a_single_score(monkeypatch):
    """I10 at the pipeline level, and it matters more now than it did: this
    is the first change where the same function both feeds the scorer and
    reads its output, so "ordering reads frozen scores and never writes back"
    needs proving here and not only against a harness fake.

    Run the pipeline normally, then again with ordering replaced by identity,
    and demand the scores are byte-identical as a SET — the order changes,
    every number in it does not.
    """
    import app.crucible.pipeline as pipeline_mod

    claims = []
    for n in range(4):
        subject = f"reach-{n}"
        claims.extend(_reach_claims(
            subject, subject,
            accounts=tuple(f"{subject}-a{i}" for i in range(n + 2)),
        ))
    claims.extend(_dollar_claims("m", "money", amounts=[250000.0, 90000.0]))

    ranked = run(claims)
    monkeypatch.setattr(
        pipeline_mod, "_rank",
        lambda findings, impacts, confidences, *, deep_cap: list(range(len(findings))),
    )
    unranked = run(claims)

    assert [f.label for f in ranked.findings] != [f.label for f in unranked.findings], (
        "ordering must actually have done something, or this proves nothing"
    )
    assert sorted(repr(i) for i in ranked.impacts) == sorted(
        repr(i) for i in unranked.impacts
    )
    assert sorted(repr(c) for c in ranked.confidences) == sorted(
        repr(c) for c in unranked.confidences
    )


def test_a_figure_only_finding_ranks_by_its_position_not_behind_its_whole_band():
    """THE ORDERING PROPERTY, AND THE HISTORY BEHIND IT.

    An earlier revision sorted on the QUARTILE and then broke ties on
    `value`. Measured on a probe corpus of 201 findings, that put a
    figure-only finding in the top band and then 51st overall — behind every
    reach finding in its own band — because the tie-break asked `value` to
    rank "fifty accounts" against "no account measure", and `None` loses that
    every time. The quartile lifted the finding and the tie-break dropped it,
    using exactly the cross-currency comparison the quartile existed to
    avoid.

    So the quartile became a REPORTING number and ordering moved to the
    underlying position among a finding's own currency peers. Here the quoted
    figure is the largest of its population and every reach finding is
    smaller than the largest of its own, so the figure sorts at the very
    front rather than behind the pack it was banded with.
    """
    claims = []
    for n in range(8):
        subject = f"reach-{n}"
        claims.extend(_reach_claims(
            subject, subject,
            accounts=tuple(f"{subject}-a{i}" for i in range(n + 2)),
        ))
    claims.extend(_dollar_claims("m", "money", amounts=[250000.0, 90000.0]))

    out = run(claims)
    labels = [f.label for f in out.findings]
    money_at = labels.index("money")
    money_impact = out.impacts[money_at]

    assert money_impact.value is None
    assert money_impact.size_rank == 1.0
    assert money_impact.size_band == SIZE_BANDS
    # Nothing ahead of it may be ranked below it — the ordering is total and
    # by rank, not by whether a finding happens to carry a `value`.
    assert all(
        i.size_rank >= money_impact.size_rank for i in out.impacts[:money_at]
    )
    assert money_at <= 1, "the top of its own currency should sort at the front"


def test_a_figure_only_finding_does_not_sink_as_its_band_gets_more_crowded():
    """THE PROPERTY THAT SEPARATES THE TWO DESIGNS, and the one an earlier
    version of this test failed to actually test.

    Under quartile-then-`value` ordering, a figure-only finding sat behind
    EVERY valued finding in its band, so its position was a function of how
    many findings happened to share that band — on a real corpus, a quarter
    of everything. Ordering on the underlying position instead, only a
    finding that genuinely ties it can precede it, and with distinct reach
    values exactly one does.

    So: grow the corpus so the top band holds more findings, and the
    figure-only finding must not move.

    (The first draft of this test scaled every reach value by 50x and
    asserted the position was stable. It passed under BOTH designs — because
    the ranking is relative, scaling everything changes nothing — so it
    proved neither. Kept as a note: an ordering test that cannot fail on the
    ordering it replaced is not testing the ordering.)
    """
    def money_position(reach_findings: int) -> tuple[int, int]:
        claims = []
        for n in range(reach_findings):
            subject = f"reach-{n}"
            claims.extend(_reach_claims(
                subject, subject,
                accounts=tuple(f"{subject}-a{i}" for i in range(n + 2)),
            ))
        claims.extend(_dollar_claims("m", "money", amounts=[250000.0, 90000.0]))
        out = run(claims)
        at = [f.label for f in out.findings].index("money")
        top_band = sum(1 for i in out.impacts if i.size_band == SIZE_BANDS)
        return at, top_band

    four_at, four_top = money_position(4)
    eight_at, eight_top = money_position(8)

    assert eight_top > four_top, "the top band must actually have grown"
    assert four_at == eight_at


def test_a_small_quoted_figure_is_not_promoted_for_being_a_figure():
    """THE FAILURE THE ORDINAL DESIGN EXISTS TO AVOID, pinned so nobody
    "fixes" it back. Guaranteeing that a dollar line renders would mean a
    small one-off outranking a much larger quoted deal purely for carrying a
    currency symbol. The smallest of several quoted figures sits in a low
    band and stays there."""
    small = _dollar_claims("s", "alpha", amounts=[500.0, 500.0])
    mid = _dollar_claims("m", "beta", amounts=[40000.0, 40000.0])
    big = _dollar_claims("b", "gamma", amounts=[900000.0, 900000.0])
    out = run(small + mid + big)
    found = _by_label(out)
    assert found["gamma"][1].size_band > found["alpha"][1].size_band
    assert found["alpha"][1].size_band < SIZE_BANDS
    assert [f.label for f in out.findings][:1] == ["gamma"]


# ── Adjudication ─────────────────────────────────────────────────────────────

def test_opposing_authoritative_claims_are_a_conflict_not_an_average():
    """Two sources that may both speak disagreeing means the model of the
    business is wrong somewhere — worth more than either claim."""
    claims = [
        claim("c1", days_ago=5, accounts=("A",), direction="positive"),
        claim("c2", days_ago=60, accounts=("B",), direction="negative"),
    ]
    out = run(claims)
    assert out.findings[0].adjudication == "conflict"


def test_a_conflict_outranks_a_bigger_sized_finding():
    claims = [
        claim("x1", subject="conflicted", days_ago=5, accounts=("A",), direction="positive"),
        claim("x2", subject="conflicted", days_ago=60, accounts=("B",), direction="negative"),
    ] + [
        claim(f"y{i}", subject="big", days_ago=d, accounts=(f"Acct{i}",))
        for i, d in enumerate([5, 40, 90, 150])
    ]
    out = run(claims)
    assert out.findings[0].adjudication == "conflict"


def test_a_single_authoritative_claim_keeps_full_weight():
    claims = [
        claim("c1", days_ago=5, accounts=("A",), authoritative=True),
        claim("c2", days_ago=60, accounts=("B",), authoritative=False),
    ]
    out = run(claims)
    assert out.findings[0].adjudication == "single_authoritative"


# ── Output discipline ────────────────────────────────────────────────────────

def test_every_statement_passes_the_causal_lint():
    """Built to survive it rather than checked afterwards: says what was
    observed and in what population, and stops."""
    from app.crucible.lint import lint_claim

    claims = [claim(f"c{i}", days_ago=d, accounts=(f"A{i}",))
              for i, d in enumerate([5, 60, 120])]
    out = run(claims)
    for f in out.findings:
        assert lint_claim(f.statement, "reported").ok, f.statement


def test_corpus_only_is_the_default():
    """Until a lever library exists there is no outcome evidence for anyone,
    and the combined formula would band every finding low regardless of
    evidence. Defaulting the other way renders a number carrying no
    information."""
    claims = [claim(f"c{i}", days_ago=d, accounts=(f"A{i}",))
              for i, d in enumerate([5, 60])]
    out = run(claims)
    assert out.confidences[0].cap_reason is not None


def test_the_pipeline_is_deterministic():
    """Reproducibility is the differentiator. Same claims, same ranking."""
    claims = [claim(f"c{i}", subject=f"s{i%3}", days_ago=d, accounts=(f"A{i}",))
              for i, d in enumerate([5, 40, 90, 150, 200, 260])]
    first, second = run(claims), run(claims)
    assert [f.id for f in first.findings] == [f.id for f in second.findings]
    assert [repr(i) for i in first.impacts] == [repr(i) for i in second.impacts]


def test_unsizeable_findings_sort_last_but_are_never_dropped():
    claims = [
        claim(f"s{i}", subject="sized", days_ago=d, accounts=(f"A{i}",))
        for i, d in enumerate([5, 60])
    ] + [
        claim(f"u{i}", subject="unsized", days_ago=d, accounts=())
        for i, d in enumerate([5, 60])
    ]
    out = run(claims)
    assert len(out.findings) == 2
    assert out.impacts[0].value is not None
    assert out.impacts[-1].value is None


# ─── What a dry run against 2,777 real signals exposed ───────────────────────

def test_only_the_leading_findings_are_marked_deep():
    """`deep_cap` was accepted, documented and never applied, so a run that
    produced 168 findings presented all 168 as equally analysed. That is the
    corpus handed back, not a decision aid."""
    claims = []
    for c_i in range(8):
        claims += [claim(f"x{c_i}a", subject=f"theme {c_i}", days_ago=5,
                         accounts=(f"A{c_i}",)),
                   claim(f"x{c_i}b", subject=f"theme {c_i}", days_ago=60,
                         accounts=(f"B{c_i}",))]
    out = run(claims, deep_cap=3)
    assert len(out.findings) == 8      # nothing dropped
    assert out.deep_count == 3


def test_the_echo_check_is_skipped_when_dates_are_the_ingest_clock():
    """A backfill stamps thousands of signals within seconds whatever the real
    events' dates were, so every cluster looks like one conversation and the
    run returns nothing — with a reason stated confidently and false. Measured
    on a real tenant: 2,410 of 2,777 rows had valid_at == created_at."""
    claims = [claim(f"c{i}", days_ago=1, accounts=(f"A{i}",)) for i in range(4)]
    assert run(claims).findings == ()                       # the honest default
    out = run(claims, dates_are_ingest_clock=True)
    assert len(out.findings) == 1
    assert out.stats["echo_check_skipped"] is True


def test_the_skip_is_a_skip_not_a_free_pass():
    """The other two refutations still run — a single-account pattern is still
    that account's situation however the corpus is dated."""
    claims = [claim(f"c{i}", days_ago=1, accounts=("OnlyOne",)) for i in range(4)]
    out = run(claims, dates_are_ingest_clock=True)
    assert out.findings == ()
    assert "single account" in out.rejected[0].reason


def test_a_group_is_named_by_its_commonest_subject_not_its_first_claim():
    """The cluster leader is whichever claim appeared first in id order.
    Naming a theme after an arbitrary member is how nine claims about billing
    end up titled with the one sentence about a calendar invite."""
    claims = [
        claim("c1", subject="calendar invite", days_ago=5, accounts=("A",)),
        claim("c2", subject="billing retries", days_ago=40, accounts=("B",)),
        claim("c3", subject="billing retries", days_ago=90, accounts=("C",)),
    ]
    for c in claims:
        object.__setattr__(c, "subject_cluster_id", "c0")
    out = run(claims)
    assert "billing retries" in out.findings[0].statement
    assert "c0" not in out.findings[0].statement


# ─── The third review: fixes that stopped at their module boundary ───────────

def test_ungroupable_claims_do_not_regroup_by_kind_one_call_later():
    """THE ONE THAT MATTERED. `assign_clusters` excluded degenerate-embedding
    claims correctly, and then `_cluster`'s fallback chain picked them straight
    back up by `subject` — which for a real signal is its KIND. So the 400
    claims just excluded became "finding", "sentiment", "feature_request":
    verbatim the category error the clustering module exists to prevent, under
    a coverage note saying they were never grouped with anything.
    """
    from app.crucible.cluster import UNGROUPABLE_PREFIX

    claims = []
    for i in range(6):
        c = claim(f"c{i}", subject="finding", days_ago=i * 30,
                  accounts=(f"A{i}",))
        object.__setattr__(c, "subject_cluster_id", f"{UNGROUPABLE_PREFIX}c{i}")
        claims.append(c)
    out = run(claims)
    assert out.findings == ()
    # ONE ledger row, not six: a tenant with no embeddings produces one per
    # signal (2,777 on a real one), which buries every genuine rejection.
    assert len(out.rejected) == 1
    assert "no usable embedding" in out.rejected[0].reason
    assert len(out.rejected[0].claim_ids) == 6


def test_an_ungroupable_claim_is_not_blamed_for_being_an_anecdote():
    """"Only one supporting claim" blames the evidence for a vector we could
    not compute. The two lead to different actions: one says the business is
    quiet, the other says our pipeline is broken."""
    from app.crucible.cluster import UNGROUPABLE_PREFIX

    c = claim("c1")
    object.__setattr__(c, "subject_cluster_id", f"{UNGROUPABLE_PREFIX}c1")
    out = run([c])
    assert "anecdote" not in out.rejected[0].reason
    assert "unknown rather than false" in out.rejected[0].reason
    assert out.rejected[0].claim_ids == ("c1",)


def test_evidence_with_no_recorded_source_document_cannot_be_called_an_echo():
    """`len(sources) <= 1` read "no artifact recorded" as "one conversation",
    so the rule returned a verdict on a column that was empty on every row —
    and the ledger asserted a provenance the system did not have."""
    claims = [claim(f"c{i}", days_ago=1, accounts=(f"A{i}",)) for i in range(4)]
    for c in claims:
        object.__setattr__(c, "artifact_id", "")
    out = run(claims)
    assert len(out.findings) == 1
    assert out.stats["claims_without_artifact"] == 4


def test_evidence_from_two_documents_is_not_one_conversation():
    """Two accounts, two connectors, three days apart is not an echo however
    tight the window."""
    claims = [claim(f"c{i}", days_ago=i + 1, accounts=(f"A{i}",)) for i in range(2)]
    object.__setattr__(claims[0], "artifact_id", "slack/#demos")
    object.__setattr__(claims[1], "artifact_id", "fireflies-batch-3")
    assert len(run(claims).findings) == 1


def test_evidence_from_one_document_in_one_window_still_is():
    """The control: the rule must still fire on the shape it exists for."""
    claims = [claim(f"c{i}", days_ago=i + 1, accounts=(f"A{i}",)) for i in range(4)]
    for c in claims:
        object.__setattr__(c, "artifact_id", "slack/#demos")
    out = run(claims)
    assert out.findings == ()
    assert "one source document" in out.rejected[0].reason


# ── The funnel the running view narrates ─────────────────────────────────────
#
# The panel renders `stats["dropped"]` as "N set aside because X". Two ways
# that number can be a lie, and both are guarded here:
#
#   1. Counting `rejected` instead of counting drops. Over MAX_LISTED_REJECTIONS
#      the ledger collapses into one summary row, so a run that dropped 1,576
#      anecdotes would narrate 100.
#   2. Attributing a drop to the wrong rule. `_refute` has THREE kill reasons,
#      not one, and matching on its prose to tell them apart breaks the first
#      time someone improves a sentence — hence the codes.

def test_every_drop_reason_is_counted_under_its_own_rule():
    claims = [
        # An anecdote: one claim, so it never reaches refutation.
        claim("lonely", subject="billing"),
        # Two claims, one account -> the single-account rule.
        claim("s1", subject="exports", accounts=("Northwind",)),
        claim("s2", subject="exports", accounts=("Northwind",), days_ago=40),
        # Two claims, two accounts, neither authoritative -> the authority rule.
        claim("a1", subject="mobile", accounts=("Initech",), authoritative=False),
        claim("a2", subject="mobile", accounts=("Globex",), authoritative=False,
              days_ago=40),
    ]
    out = run(claims)
    dropped = out.stats["dropped"]

    assert dropped["anecdote"] == 1
    assert dropped["single_account"] == 1
    assert dropped["no_authority"] == 1
    # PRESENT AT ZERO, not absent. The panel distinguishes "this rule dropped
    # nothing" from "this rule did not run", and a missing key cannot carry
    # that difference.
    assert dropped["echo"] == 0
    assert dropped["uncausal"] == 0
    assert dropped["ungroupable"] == 0


def test_the_echo_rule_is_counted_separately_from_the_other_refutations():
    # One document, one window, two accounts -> echo, NOT single-account.
    claims = [
        claim("e1", subject="latency", accounts=("Northwind",), days_ago=1),
        claim("e2", subject="latency", accounts=("Initech",), days_ago=2),
    ]
    out = run(claims)
    assert out.stats["dropped"]["echo"] == 1
    assert out.stats["dropped"]["single_account"] == 0


def test_the_funnel_counts_real_drops_not_the_truncated_ledger():
    """The bug this exists to stop: narrating the ledger's length.

    Over `MAX_LISTED_REJECTIONS` the ledger collapses to a summary row, so
    `len(rejected)` stops being the number of things that were dropped. The
    funnel has to be the truth, not the excerpt."""
    from app.crucible.pipeline import MAX_LISTED_REJECTIONS

    n = MAX_LISTED_REJECTIONS + 40
    # Each is its own subject, so each is its own one-claim cluster.
    out = run([claim(f"c{i}", subject=f"subject {i}") for i in range(n)])

    assert out.stats["dropped"]["anecdote"] == n
    # The ledger DID truncate — otherwise this test proves nothing.
    assert len(out.rejected) <= MAX_LISTED_REJECTIONS + 1
    assert out.stats["dropped"]["anecdote"] > len(out.rejected)


def test_conflicts_are_counted_for_the_funnel():
    out = run([
        claim("c1", subject="pricing", accounts=("Northwind",), direction="up"),
        claim("c2", subject="pricing", accounts=("Initech",), direction="down",
              days_ago=40),
    ])
    assert out.stats["conflicts"] == sum(
        1 for f in out.findings if f.adjudication == "conflict"
    )


def test_ungroupable_groups_is_counted_not_assumed():
    """The theme count is `clusters - ungroupable_groups`, so that number has
    to be measured rather than inferred from the ungroupable CLAIM count.

    `_cluster` lowercases its key, so two claim ids differing only in case
    share one cluster — the two counts would then disagree and a derived
    subtraction would silently under-report the themes."""
    # Two claims, no embedding => each gets its own ungroupable cluster.
    from app.crucible.cluster import UNGROUPABLE_PREFIX
    from dataclasses import replace

    a = replace(claim("u1"), subject_cluster_id=f"{UNGROUPABLE_PREFIX}u1")
    b = replace(claim("u2"), subject_cluster_id=f"{UNGROUPABLE_PREFIX}u2")
    # A real theme alongside them, so `clusters` is not purely ungroupable.
    c1 = claim("g1", subject="exports", accounts=("Northwind",))
    c2 = claim("g2", subject="exports", accounts=("Initech",), days_ago=40)

    out = run([a, b, c1, c2])
    assert out.stats["dropped"]["ungroupable"] == 2      # claims
    assert out.stats["ungroupable_groups"] == 2          # groups
    # The identity the panel's headline rests on.
    themes = out.stats["clusters"] - out.stats["ungroupable_groups"]
    group_drops = sum(
        out.stats["dropped"][c] for c in NARRATED_DROPS if c != "ungroupable"
    )
    assert themes == len(out.findings) + group_drops


def test_two_claims_sharing_one_ungroupable_cluster_do_not_break_the_theme_count():
    """The case the derived subtraction got wrong: one cluster, two claims.
    `ungroupable` counts 2 and `ungroupable_groups` counts 1, and only the
    latter keeps `themes` correct."""
    from app.crucible.cluster import UNGROUPABLE_PREFIX
    from dataclasses import replace

    shared = f"{UNGROUPABLE_PREFIX}same"
    a = replace(claim("s1"), subject_cluster_id=shared)
    b = replace(claim("s2"), subject_cluster_id=shared)
    c1 = claim("g1", subject="exports", accounts=("Northwind",))
    c2 = claim("g2", subject="exports", accounts=("Initech",), days_ago=40)

    out = run([a, b, c1, c2])
    assert out.stats["dropped"]["ungroupable"] == 2, "claims"
    assert out.stats["ungroupable_groups"] == 1, "groups — the whole point"
    themes = out.stats["clusters"] - out.stats["ungroupable_groups"]
    group_drops = sum(
        out.stats["dropped"][c] for c in NARRATED_DROPS if c != "ungroupable"
    )
    assert themes == len(out.findings) + group_drops
    # And the old, derived arithmetic would have been wrong here.
    assert out.stats["clusters"] - out.stats["dropped"]["ungroupable"] != themes

# ── The statement a reader actually has to judge ─────────────────────────────

def test_a_finding_quotes_one_of_its_claims():
    """`N claims concern "X"` is a table-of-contents entry: it names a topic and
    says how often it came up, and a reader cannot judge it, argue with it, or
    take it to anyone. Against the same corpus the chat surface answers with
    quotes and account counts; the report answered with a label. So the
    strongest claim comes with it, as reported speech."""
    claims = [
        claim("c1", assertion="PDF export fails on decks over 200 slides"),
        claim("c2", days_ago=40),
        claim("c3", days_ago=80, accounts=("Vandelay Industries",)),
    ]
    out = run(claims)
    said = out.findings[0].statement
    assert "for example" in said
    assert "PDF export fails on decks over 200 slides" in said


def test_the_quoted_example_is_cut_at_a_causal_connective():
    """The example is reported speech, not our analysis. It goes through the
    same cut the label gets, so a source's own "because" cannot arrive in a
    sentence the reader will read as ours."""
    claims = [
        claim("c1", assertion="Keystrokes drop in the editor because sync stalls"),
        claim("c2", days_ago=40),
        claim("c3", days_ago=80, accounts=("Initech",)),
    ]
    said = run(claims).findings[0].statement
    assert "because sync stalls" not in said
    assert "Keystrokes drop in the editor" in said


def test_an_example_contained_in_the_topic_is_left_out():
    """A quote the topic already contains teaches nothing and costs a line.
    NOT just an identical one: the equality check alone left "export latency"
    quoted under the topic "export latency issues", which is the same
    redundancy one word short of being caught."""
    from app.crucible.pipeline import _statement
    exact = _statement("export latency", [claim("c1", assertion="export latency"),
                                          claim("c2", assertion="export latency")], ())
    assert "for example" not in exact
    inside = _statement("export latency issues",
                        [claim("c1", assertion="export latency"),
                         claim("c2", assertion="export latency")], ())
    assert "for example" not in inside


def test_an_assertion_that_reduces_to_nothing_is_not_quoted():
    """`label_for` returns the literal "unlabelled" for anything that strips to
    nothing, so quoting its output put quotation marks around a word no source
    ever said. NOT just the empty string: a claim of pure punctuation is
    non-empty, passes the emptiness guard, and comes back "unlabelled"."""
    from app.crucible.pipeline import _statement
    empty = _statement("export latency", [claim("c1", assertion=""),
                                          claim("c2", assertion="")], ())
    assert "unlabelled" not in empty
    punctuation = _statement("export latency", [claim("c1", assertion=" ,;: "),
                                                claim("c2", assertion=" ,;: ")], ())
    assert "unlabelled" not in punctuation


def test_the_count_agrees_with_itself():
    """"1 claims concern" shipped in every report this engine ever produced. A
    count that cannot get its own plural right is the first thing a reader
    stops trusting, and everything after it is numbers."""
    from app.crucible.pipeline import _statement
    one = _statement("export latency", [claim("c1")], ())
    many = _statement("export latency", [claim("c1"), claim("c2")], ())
    assert one.startswith("1 claim concerns")
    assert many.startswith("2 claims concern ")
    acct = _statement("export latency", [claim("c1")], ("Northwind",))
    assert "1 claim across 1 account concerns" in acct

# ── A blocker is not an anecdote ─────────────────────────────────────────────

def test_a_lone_constraint_is_a_finding_not_an_anecdote():
    """The corroboration rule is right for THEMES — "customers keep asking for
    X" means nothing on one mention — and wrong for CONSTRAINTS. A deal blocker
    is specific to one deal by definition, so it is mentioned once by
    definition, and requiring a second mention deletes exactly the items a PM
    most needs. Measured on a real corpus of 160 blocker signals: 85 of 101
    rejections were `anecdote`, including "336K USD in renewals is at risk"."""
    out = run([claim("c1", ctype="constraint",
                     assertion="Their budget freeze blocks the renewal")])
    assert len(out.findings) == 1
    assert out.stats["dropped"]["anecdote"] == 0


def test_a_lone_claim_of_any_other_type_is_still_an_anecdote():
    """The control. One person saying a thing once is still not a pattern, and
    the exemption must not have quietly turned the rule off."""
    out = run([claim("c1", ctype="mechanism")])
    assert out.findings == ()
    assert out.stats["dropped"]["anecdote"] == 1


def test_a_constraint_about_one_account_is_that_account_being_blocked():
    """"That account's situation rather than a pattern across the book" is right
    about a preference and wrong about a constraint, where being about one
    account IS the content. Exempting the anecdote rule alone left 34
    named-account blockers still dropped on the measured corpus."""
    out = run([
        claim("c1", ctype="constraint", accounts=("Northwind",), days_ago=1,
              assertion="Their security review has not started"),
        claim("c2", ctype="constraint", accounts=("Northwind",), days_ago=40,
              assertion="Procurement has still not approved the vendor form"),
    ])
    assert len(out.findings) == 1
    assert out.stats["dropped"]["single_account"] == 0


def test_a_preference_from_one_account_is_still_that_accounts_opinion():
    """The control for the other half."""
    out = run([
        claim("c1", ctype="preference", accounts=("Northwind",), days_ago=1),
        claim("c2", ctype="preference", accounts=("Northwind",), days_ago=40),
    ])
    assert out.findings == ()
    assert out.stats["dropped"]["single_account"] == 1


def test_a_mixed_group_is_not_exempted_by_one_constraint_member():
    """ALL of them, not any: a mixed group still contains claim types that need
    corroboration, and one exempt member must not carry them."""
    out = run([
        claim("c1", ctype="constraint", accounts=("Northwind",), days_ago=1),
        claim("c2", ctype="preference", accounts=("Northwind",), days_ago=40),
    ])
    assert out.findings == ()
    assert out.stats["dropped"]["single_account"] == 1


def test_one_claim_cannot_be_one_conversation_echoing():
    """A group of one has nothing to echo. The rule fired anyway once singletons
    could reach it, and printed "all 1 supporting claims come from one source
    document within 0 days" — not a sentence about evidence. It cost 93 of the
    103 echo drops on the measured corpus."""
    out = run([claim("c1", ctype="constraint")])
    assert len(out.findings) == 1
    assert out.stats["dropped"]["echo"] == 0


def test_two_claims_from_one_document_in_one_window_still_echo():
    """The control: the rule the spike was fooled by is untouched."""
    out = run([
        claim("c1", ctype="constraint", days_ago=1, accounts=("Northwind",)),
        claim("c2", ctype="constraint", days_ago=2, accounts=("Vandelay Industries",)),
    ])
    assert out.findings == ()
    assert out.stats["dropped"]["echo"] == 1


# ─── The source line names a SOURCE, not an ingest chunk ────────────────────


def test_sync_batches_of_one_provider_collapse_into_one_source():
    """Apurva, reading a real report: "it says fireflies-sync-batch-9 (6) ·
    fireflies-sync-batch-0 (2) · fireflies-sync-batch-14 (2), a user might not
    know what's in the sync batch".

    The number is an INGEST CHUNK. A connector sync slices its pull into
    arbitrary batches and stamps each with its index, so one source chopped
    three ways reads as evidence spread across three documents — and breadth
    across documents is exactly what a reader uses to judge support. It does not
    merely fail to inform; it inflates.
    """
    from app.crucible.pipeline import _sources_of

    out = _sources_of([
        claim("c1", artifact_id="fireflies-sync-batch-9"),
        claim("c2", artifact_id="fireflies-sync-batch-9"),
        claim("c3", artifact_id="fireflies-sync-batch-0"),
        claim("c4", artifact_id="fireflies-sync-batch-14"),
    ])
    assert out == ("Fireflies call transcripts (4)",)
    # The counts are SUMMED, not the largest chunk kept: four claims came from
    # that source and the line has to say four.
    assert "batch" not in " ".join(out)


def test_a_real_document_name_is_left_alone():
    """Only the sync-batch shape is rewritten. A Slack channel or a filename is
    something a reader can go and open, and renaming it would destroy the one
    thing the line is for."""
    from app.crucible.pipeline import _sources_of

    out = _sources_of([
        claim("c1", artifact_id="slack/#mvp-product (part 2/3)"),
        claim("c2", artifact_id="Q3 renewal deck.pdf"),
    ])
    assert set(out) == {"slack/#mvp-product (part 2/3) (1)", "Q3 renewal deck.pdf (1)"}


def test_two_providers_do_not_collapse_into_each_other():
    """The provider is the only real attribution in the string, so it is the
    one thing that must survive."""
    from app.crucible.pipeline import _sources_of

    out = _sources_of([
        claim("c1", artifact_id="fireflies-sync-batch-1"),
        claim("c2", artifact_id="zoom-sync-batch-4"),
    ])
    assert set(out) == {"Fireflies call transcripts (1)", "Zoom call transcripts (1)"}


def test_an_unknown_provider_still_reads_as_a_name():
    """A connector added later must not render as a raw slug."""
    from app.crucible.pipeline import _document_label

    assert _document_label("hubspot-sync-batch-2") == "Hubspot"
    assert _document_label("some_tool-sync-batch-0") == "Some Tool"


def test_the_finding_carries_its_theme_and_its_example_separately():
    """The renderer leads with the theme and sets the quote as a quote, so both
    have to arrive as fields rather than be dug back out of the sentence."""
    # Distinct accounts AND distinct documents, or the group is refuted as a
    # single account or as one conversation echoing, and there is no finding to
    # inspect.
    out = run([
        claim("c1", accounts=("Northwind",), artifact_id="call-a", days_ago=1,
              assertion="export runs time out at 10k rows"),
        claim("c2", accounts=("Vandelay",), artifact_id="call-b", days_ago=20,
              assertion="export runs time out at 10k rows"),
        claim("c3", accounts=("Initech",), artifact_id="call-c", days_ago=40,
              assertion="export runs time out at 10k rows"),
    ])
    f = out.findings[0]
    assert f.label
    assert f.label in f.statement
    # The example, when there is one, is the words a source actually used.
    if f.example:
        assert f.example in f.statement
