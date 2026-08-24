"""Stage 0 — the invariant that protects the identity of the question.

I9 sits above the other nine. They protect the quality of an answer; this one
protects whether it answers the right question. A wrong definition produces a
fully coherent, well-sized, well-argued answer to a DIFFERENT question — the
causal lint passes, the scoring is sound, every claim traces to a real
document, and nothing downstream can tell.

So the tests that matter are the refusals: never paraphrase, never break a tie,
never lock without a human. A suite that only checked the happy adopt path
would pass against an implementation that guesses.

No network, no DB, no LLM.
"""
from __future__ import annotations

from datetime import datetime, timezone
from types import SimpleNamespace

import pytest

from app.crucible.goal import (
    GoalResolution,
    KpiTreeSource,
    MetricCandidate,
    confirm,
    definition_hash,
    has_drifted,
    resolve,
)
from app.crucible.invariants import assert_goal_locked
from app.crucible.types import GoalNotLockedError

NOW = datetime(2026, 8, 19, tzinfo=timezone.utc)

VERBATIM = (
    "Expansion minus churn across the existing customer base; standard health "
    "metric for a B2B security SaaS."
)


def tree(*metrics, north=("Net Revenue Retention (NRR)", VERBATIM)):
    return SimpleNamespace(
        north_star=SimpleNamespace(metric=north[0], description=north[1]),
        primary_metrics=[
            SimpleNamespace(metric=m, description=d) for m, d in metrics
        ],
    )


def resolve_with(source, goal="improve net revenue retention"):
    return resolve(
        company_id="co-1", raw_goal_text=goal,
        currency="arr_dollars", sources=[source],
    )


# ── Adopt ────────────────────────────────────────────────────────────────────

def test_an_existing_definition_is_adopted_verbatim():
    """Byte-identical. "Revenue" tidied into "recognised revenue" is a
    different metric, asserted by us, wearing their authority."""
    out = resolve_with(KpiTreeSource(tree()))
    assert out.status == "candidate"
    assert out.definition is not None
    assert out.definition.definition_text == VERBATIM
    assert out.definition.origin == "adopted"


def test_resolve_never_returns_a_locked_definition():
    """No code path here, and no LLM output anywhere, may produce `locked`."""
    out = resolve_with(KpiTreeSource(tree()))
    assert out.definition is not None
    assert out.definition.status == "candidate"
    with pytest.raises(GoalNotLockedError):
        assert_goal_locked(out.definition)


def test_the_confirmation_shows_the_definition_and_says_it_is_theirs():
    out = resolve_with(KpiTreeSource(tree()))
    assert VERBATIM in out.ask
    assert "haven't reworded" in out.ask


# ── Ask ──────────────────────────────────────────────────────────────────────

def test_no_definition_anywhere_asks_rather_than_inventing_one():
    """The staging test tenant has no KPI tree at all, so this is the path the
    live verification will actually take.

    PINS THE BEHAVIOUR, NOT THE SPELLING. This asserted `"can't find" in
    out.ask`, which is the wording rather than the rule — so it broke on an
    intentional copy change (§5 requirement 1: the ask must not OPEN with what
    was not found, because the search block above it has already said so) while
    never having checked the thing it is named for. What matters is that
    nothing was invented and that the user is asked."""
    out = resolve_with(KpiTreeSource(None), goal="improve activation")
    assert out.status == "needs_input"
    assert out.definition is None
    assert out.ask, "a miss has to produce a question"
    # It must not open with the gap — that is the defect §5 req 1 names.
    assert not out.ask.lower().startswith("i can't find")
    # And it must ask for the parts a definition actually needs.
    assert "what is counted" in out.ask.lower()


def test_the_ask_says_why_guessing_would_be_worse():
    """An ask that just says "I don't know" reads as incapacity. Naming the
    consequence is what makes it a reasonable thing to be asked."""
    out = resolve_with(KpiTreeSource(None), goal="improve activation")
    assert "confident answer to a question you didn't ask" in out.ask


def test_a_metric_named_but_never_defined_is_an_ask_not_an_adopt():
    """The KPI tree carries the NAME with an empty description. Adopting that
    would mean sizing everything against a word — and it is the common case:
    two teams point at the same metric and mean gross versus net."""
    out = resolve_with(
        KpiTreeSource(tree(("Incremental revenue", ""))),
        goal="grow incremental revenue",
    )
    assert out.status == "needs_input"
    assert "no definition written down" in out.ask
    assert "gross versus net" in out.ask


def test_a_goal_that_matches_nothing_falls_through_to_the_ask():
    out = resolve_with(KpiTreeSource(tree()), goal="reduce moderation escalations")
    assert out.status == "needs_input"


# ── Conflict ─────────────────────────────────────────────────────────────────

class _TwoSources:
    label = "two systems"

    def candidates(self, company_id, goal_text):
        return (
            MetricCandidate("Revenue", "billed spend net of credits",
                            "finance.metrics", "the finance layer"),
            MetricCandidate("Revenue", "gross bookings including marketplace",
                            "crm.reports", "the CRM"),
        )


def test_two_definitions_of_one_metric_surface_as_a_conflict():
    """NEVER resolved silently. Picking the more recently updated one is the
    exact failure I9 exists to prevent, and it is invisible afterwards."""
    out = resolve(company_id="co-1", raw_goal_text="drive revenue",
                  currency="arr_dollars", sources=[_TwoSources()])
    assert out.status == "conflict"
    assert out.definition is None
    assert len(out.conflicts) == 1


def test_the_conflict_ask_shows_both_and_refuses_to_choose():
    out = resolve(company_id="co-1", raw_goal_text="drive revenue",
                  currency="arr_dollars", sources=[_TwoSources()])
    assert "billed spend net of credits" in out.ask
    assert "gross bookings including marketplace" in out.ask
    assert "won't pick for you" in out.ask


def test_whitespace_does_not_manufacture_a_conflict():
    class _Whitespace:
        label = "x"

        def candidates(self, company_id, goal_text):
            return (
                MetricCandidate("Revenue", "billed  spend", "a", "A"),
                MetricCandidate("Revenue", "billed spend", "b", "B"),
            )

    out = resolve(company_id="co-1", raw_goal_text="revenue",
                  currency="arr_dollars", sources=[_Whitespace()])
    assert out.status == "candidate"


# ── Lock ─────────────────────────────────────────────────────────────────────

def test_confirm_is_the_only_way_to_lock():
    out = resolve_with(KpiTreeSource(tree()))
    locked = confirm(out.definition, user_id="u-1", at=NOW)
    assert locked.status == "locked"
    assert_goal_locked(locked)


def test_locking_without_a_user_is_refused():
    out = resolve_with(KpiTreeSource(tree()))
    with pytest.raises(ValueError, match="I9"):
        confirm(out.definition, user_id="", at=NOW)


def test_an_edited_definition_becomes_elicited_not_adopted():
    """It is now the user's words, not their system's. Calling it adopted would
    misdescribe where it came from on every later run."""
    out = resolve_with(KpiTreeSource(tree()))
    locked = confirm(out.definition, user_id="u-1", at=NOW,
                     definition_text="Expansion minus churn, excluding pilots.")
    assert locked.origin == "elicited"
    assert locked.definition_text == "Expansion minus churn, excluding pilots."


def test_an_unedited_definition_stays_adopted():
    out = resolve_with(KpiTreeSource(tree()))
    locked = confirm(out.definition, user_id="u-1", at=NOW, definition_text=VERBATIM)
    assert locked.origin == "adopted"


def test_editing_rehashes_so_drift_is_measured_against_what_was_confirmed():
    out = resolve_with(KpiTreeSource(tree()))
    locked = confirm(out.definition, user_id="u-1", at=NOW,
                     definition_text="Something else entirely.")
    assert locked.definition_hash == definition_hash(
        "Something else entirely.", out.definition.definition_source_ref
    )


# ── Drift ────────────────────────────────────────────────────────────────────

def test_an_unchanged_source_does_not_drift():
    locked = confirm(resolve_with(KpiTreeSource(tree())).definition,
                     user_id="u-1", at=NOW)
    assert has_drifted(locked, VERBATIM) is False


def test_whitespace_alone_is_not_drift():
    locked = confirm(resolve_with(KpiTreeSource(tree())).definition,
                     user_id="u-1", at=NOW)
    assert has_drifted(locked, f"  {VERBATIM}  ") is False


def test_a_reworded_source_definition_is_drift():
    locked = confirm(resolve_with(KpiTreeSource(tree())).definition,
                     user_id="u-1", at=NOW)
    assert has_drifted(locked, "Expansion minus churn, now excluding pilots.") is True


def test_the_hash_covers_the_source_not_only_the_words():
    """The same words from a different system is a different definition."""
    assert definition_hash("same words", "finance") != definition_hash("same words", "crm")


# ── Robustness ───────────────────────────────────────────────────────────────

def test_a_broken_source_does_not_end_stage_zero():
    """One rung failing must not turn into "no definition exists" — that would
    silently convert an adoptable metric into an ask, or worse, let a later
    rung's guess win."""
    class _Broken:
        label = "broken"

        def candidates(self, company_id, goal_text):
            raise RuntimeError("registry down")

    out = resolve(company_id="co-1", raw_goal_text="improve net revenue retention",
                  currency="arr_dollars",
                  sources=[_Broken(), KpiTreeSource(tree())])
    assert out.status == "candidate"
    assert out.definition.definition_text == VERBATIM


def test_an_empty_ladder_asks():
    out = resolve(company_id="co-1", raw_goal_text="anything",
                  currency="arr_dollars", sources=[])
    assert out.status == "needs_input"


# ── Matching: normalisation, not inference ───────────────────────────────────

@pytest.mark.parametrize("goal", [
    "improve net revenue retention",
    "Net Revenue Retention",
    "increase our net revenue retention rate",
])
def test_a_parenthetical_abbreviation_does_not_hide_the_metric(goal):
    """"Net Revenue Retention (NRR)" is how half a KPI tree is written, and a
    literal substring match sees NO overlap between that and "improve net
    revenue retention". The first version silently fell through to the ask for
    every metric named this way — a wrong answer that looks like caution."""
    out = resolve_with(KpiTreeSource(tree()), goal=goal)
    assert out.status == "candidate", goal
    assert out.definition.metric_name == "Net Revenue Retention (NRR)"


@pytest.mark.parametrize("goal", [
    "reduce moderation escalations",
    "improve app store rating",
    "cut time to first value",
])
def test_an_unrelated_goal_still_falls_through_to_the_ask(goal):
    """The matcher is looser than a substring, and this is the guard on that:
    loosening it must not turn into matching anything. A metric the goal does
    not name has to stay unfound."""
    out = resolve_with(KpiTreeSource(tree()), goal=goal)
    assert out.status == "needs_input", goal


def test_matching_is_not_fuzzy():
    """No stemming, no edit distance. "retention" must not match "retentions"
    by similarity — if a tree spells it differently, that is an ask, not a
    guess."""
    out = resolve_with(
        KpiTreeSource(tree(north=("Gross Retention", "d"))),
        goal="improve net revenue retention",
    )
    assert out.status == "needs_input"


def test_a_metric_named_only_with_stopwords_cannot_match_everything():
    out = resolve_with(
        KpiTreeSource(tree(north=("The Rate", "d"))),
        goal="improve net revenue retention",
    )
    assert out.status == "needs_input"


# ─── The four the review caught. Each one adopted something nobody chose. ────

def _tree(*metrics):
    """A KPI tree with the given (name, definition) primary metrics."""
    from types import SimpleNamespace

    return SimpleNamespace(
        north_star=SimpleNamespace(metric=metrics[0][0], description=metrics[0][1]),
        primary_metrics=[
            SimpleNamespace(metric=m, description=d) for m, d in metrics[1:]
        ],
    )


def _resolve(goal, tree):
    return resolve(company_id="co", raw_goal_text=goal, currency="accounts",
                   sources=[KpiTreeSource(tree)])


def test_two_metrics_matching_one_goal_is_a_question_not_a_ranking():
    """MAU and WAU both name "increase active users". Adopting whichever the
    tree lists first is a coherent, confident answer to a question the user
    did not ask — I9's exact failure mode, and invisible afterwards."""
    tree = _tree(("Monthly Active Users", "distinct users in 30 days"),
                 ("Weekly Active Users", "distinct users in 7 days"))
    out = _resolve("increase active users", tree)
    assert out.status == "needs_input"
    assert "Monthly Active Users" in out.ask and "Weekly Active Users" in out.ask


def test_the_tie_is_order_independent():
    """The bug was positional, so the proof has to be too: swapping the tree
    must not change the outcome."""
    a = _resolve("increase active users",
                 _tree(("Monthly Active Users", "30 days"),
                       ("Weekly Active Users", "7 days")))
    b = _resolve("increase active users",
                 _tree(("Weekly Active Users", "7 days"),
                       ("Monthly Active Users", "30 days")))
    assert a.status == b.status == "needs_input"


def test_a_goal_of_only_stopwords_adopts_nothing():
    """`set() <= anything` is True, so a goal that normalised to zero tokens
    matched EVERY metric and silently adopted the north star."""
    tree = _tree(("Net Revenue Retention", "expansion minus churn"))
    for goal in ("improve our total rate", "   ", "make it better"):
        out = _resolve(goal, tree)
        assert out.status != "candidate", f"{goal!r} adopted something"


def test_a_real_metric_name_still_matches():
    """The control. A guard that also blocked real matches would trade one
    failure for another."""
    tree = _tree(("Net Revenue Retention (NRR)", "expansion minus churn"))
    out = _resolve("improve net revenue retention", tree)
    assert out.status == "candidate"


def test_reduce_is_recorded_as_a_decrease():
    """The table never mutates a locked row, so "reduce churn" stored as
    `increase` has every later run reading the goal backwards."""
    tree = _tree(("Churn Rate", "accounts lost over accounts held"))
    out = _resolve("reduce churn rate", tree)
    assert out.status == "candidate"
    assert out.definition.direction == "decrease"


def test_confirm_refuses_an_empty_definition():
    """`resolve` refuses to adopt a named-but-undefined metric. Clearing the
    textarea reached the same state through the other door."""
    from app.crucible.types import GoalDefinition

    bare = GoalDefinition(id="", raw_goal_text="g", metric_name="", definition_text="",
                          currency="accounts", direction="increase")
    for blank in ("", "   ", "\t\n"):
        with pytest.raises(ValueError, match="empty"):
            confirm(bare, user_id="u1", at=NOW, definition_text=blank)


def test_a_definition_with_no_known_source_is_elicited_not_adopted():
    """`origin or "adopted"` defaulted unknown provenance to the STRONGER
    claim — a row asserting the company's own system defined a metric that no
    source ever named."""
    from app.crucible.types import GoalDefinition

    bare = GoalDefinition(id="", raw_goal_text="g", metric_name="", definition_text="",
                          currency="accounts", direction="increase")
    locked = confirm(bare, user_id="u1", at=NOW,
                     definition_text="renewal-cohort revenue net of churn")
    assert locked.origin == "elicited"
    assert locked.status == "locked"


def test_a_broken_source_does_not_end_stage_0():
    """The docstring promises one broken rung cannot end Stage 0."""
    class Exploding:
        label = "exploding"

        def candidates(self, company_id, goal_text):
            raise RuntimeError("boom")

    tree = _tree(("Net Revenue Retention", "expansion minus churn"))
    out = resolve(company_id="co", raw_goal_text="improve net revenue retention",
                  currency="accounts", sources=[Exploding(), KpiTreeSource(tree)])
    assert out.status == "candidate"
