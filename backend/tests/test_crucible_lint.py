"""Crucible causal lint — I5. Causal verbs require causal evidence.

The lint is the cheapest invariant to build and the one that earns trust
fastest, because a reader can check it themselves in one sentence. What this
file holds:

  * every banned verb is blocked at every non-causal strength (the spec's own
    unit-test line), and the spec's six are checked by name so a future edit to
    the list cannot silently shrink it
  * `causally_tested` — and ONLY that — may assert causation
  * word boundaries: "root-cause analysis" and "overdue" are not violations,
    and a lint that flags them gets switched off
  * inflections ARE violations, because "caused" is not a lesser claim than
    "causes"
  * honest phrasing at correlational strength stays legal

No network, no DB, no LLM: `app.crucible.lint` is pure.
"""
from __future__ import annotations

import pytest

from app.crucible.lint import (
    BANNED_CAUSAL_INFLECTIONS,
    BANNED_CAUSAL_VERBS,
    CausalLintError,
    assert_lint_clean,
    lint_claim,
)

NON_CAUSAL = ("measured", "correlated", "inferred", "reported")


@pytest.mark.parametrize("verb", BANNED_CAUSAL_VERBS)
@pytest.mark.parametrize("strength", NON_CAUSAL)
def test_every_banned_verb_blocked_at_every_non_causal_strength(verb, strength):
    result = lint_claim(f"Slow export {verb} churn in the SMB segment.", strength)
    assert result.ok is False
    assert result.violation is not None


@pytest.mark.parametrize("verb", BANNED_CAUSAL_INFLECTIONS)
def test_inflections_are_blocked_too(verb):
    """'Slow export caused churn' is the same claim as 'causes'. A lint that
    passes the past tense is theatre."""
    assert lint_claim(f"Slow export {verb} churn.", "correlated").ok is False


def test_the_spec_six_are_still_the_spec_six():
    """Guards the list itself. Shrinking BANNED_CAUSAL_VERBS is a spec change,
    and it is the kind of change that looks like tidying."""
    assert set(BANNED_CAUSAL_VERBS) == {
        "causes", "drives", "leads to", "results in", "because of", "due to",
    }


@pytest.mark.parametrize("verb", BANNED_CAUSAL_VERBS)
def test_causally_tested_may_assert_causation(verb):
    assert lint_claim(f"Slow export {verb} churn.", "causally_tested").ok is True


def test_measured_is_not_enough_for_causation():
    """`measured` means we counted it, not that we established why. This is the
    boundary most likely to be argued away under deadline."""
    assert lint_claim("Onboarding length drives activation.", "measured").ok is False


@pytest.mark.parametrize("text", [
    "Root-cause analysis is scheduled for next sprint.",
    "The invoice is overdue tomorrow.",
    "Applications with a causeway integration are unaffected.",
    "Revenue rose; the driver tree is unchanged.",
    # Product-specific, and the reason the bare nouns are not banned: Google
    # Drive is a connector this codebase syncs, so `drive` as a banned verb
    # would fire on ordinary connector prose in every report.
    "Google Drive sync registered 27 documents.",
    "There is a drive to reduce onboarding time this quarter.",
    "The cause of the outage is still under investigation.",
])
def test_word_boundaries_prevent_false_positives(text):
    """A lint with false positives gets disabled, and a disabled lint protects
    nothing — so precision here is what keeps it enabled."""
    assert lint_claim(text, "reported").ok is True, text


@pytest.mark.parametrize("text", [
    "Export latency correlates with churn in the SMB segment.",
    "Accounts that hit the limit are associated with lower renewal rates.",
    "The drop coincides with the pricing change.",
    "Churn rose in the quarter following the migration.",
])
def test_honest_correlational_phrasing_stays_legal(text):
    """The lint's job is to make the honest phrasing the easy one, not to push
    authors toward vaguer prose."""
    assert lint_claim(text, "correlated").ok is True, text


def test_all_violations_are_reported_not_just_the_first():
    """An author fixing one phrase per failed run is the worst version of this
    loop."""
    result = lint_claim(
        "Latency causes churn, which leads to contraction due to budget review.",
        "correlated",
    )
    assert result.ok is False
    assert len(result.violations) >= 3


def test_line_wrapped_prose_is_still_caught():
    """Rendered narrative wraps; `leads\\nto` is the same violation."""
    assert lint_claim("Slow export leads\n   to churn.", "correlated").ok is False


def test_spec_literal_mode_runs_the_six_alone():
    """The inflection list is an extension and is documented as one, so there
    has to be a way to run exactly what the spec says."""
    assert lint_claim("Slow export caused churn.", "correlated",
                      spec_literal=True).ok is True
    assert lint_claim("Slow export causes churn.", "correlated",
                      spec_literal=True).ok is False


def test_assert_lint_clean_raises_and_names_the_violation():
    """I5 failure is a HARD ERROR, not a warning — a warning gets filtered out
    of the logs by the second week."""
    with pytest.raises(CausalLintError) as exc:
        assert_lint_clean("Pricing drives contraction.", "correlated")
    assert exc.value.violation == "drives"
    assert "I5" in str(exc.value)


def test_assert_lint_clean_passes_causally_tested():
    assert_lint_clean("The holdout shows the prompt causes activation.",
                      "causally_tested")


def test_unknown_strength_is_an_error_not_a_silent_pass():
    """A typo'd strength must never become a free pass through the lint."""
    with pytest.raises(ValueError):
        lint_claim("Anything at all.", "quite_sure")
