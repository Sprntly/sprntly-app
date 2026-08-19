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
    build_findings,
)
from app.crucible.types import Claim, PopulationFilter

NOW = datetime(2026, 8, 19, tzinfo=timezone.utc)


def claim(
    cid: str, *, subject="export latency", days_ago=1, accounts=("Northwind",),
    authoritative=True, strength="reported", ctype="mechanism",
    source="customer_voice", direction="neutral",
) -> Claim:
    return Claim(
        id=cid, assertion=f"claim {cid}", type=ctype, subject=subject,
        source_id=source, artifact_id="a", artifact_type="t",
        strength=strength, observed_at=NOW - timedelta(days=days_ago),
        authoritative=authoritative,
        population=PopulationFilter(
            segments={"accounts": tuple(accounts), "customer_side": tuple(accounts)},
            estimated_size=len(accounts) or None,
        ),
        direction=direction,
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
