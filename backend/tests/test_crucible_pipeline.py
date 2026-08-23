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
