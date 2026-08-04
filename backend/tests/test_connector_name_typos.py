"""A mistyped source name is still a named source.

Reported 2026-08-03: "conflunece just added something new check and tell me what
the content is" was answered "I cannot perform a live, real-time pull of your
Confluence space on demand". The user named the source, said what they wanted,
and got told the product lacks a capability it has — because the transposed
`ne` missed \\bconfluence\\b and the question fell through to the generic path.

The bar is Damerau-Levenshtein ≤ 1 on names of seven letters or more, and the
negative cases below are the point: a similarity RATIO would put "confidence"
and "conflunece" within ~0.1 of each other, and eventually route a question
about confidence intervals into a wiki lookup.
"""
from __future__ import annotations

from app.skill_router import _within_one_edit, is_connector_lookup


# ── the edit-distance bar ────────────────────────────────────────────────────

def test_one_edit_of_every_kind_is_a_typo():
    assert _within_one_edit("conflunece", "confluence")   # transposition
    assert _within_one_edit("confluance", "confluence")   # substitution
    assert _within_one_edit("conflence", "confluence")    # deletion
    assert _within_one_edit("confluencee", "confluence")  # insertion
    assert _within_one_edit("confluence", "confluence")   # exact


def test_two_edits_is_a_different_word():
    # The case that makes this distance-based rather than ratio-based.
    assert not _within_one_edit("confidence", "confluence")
    assert not _within_one_edit("conference", "confluence")
    assert not _within_one_edit("influence", "confluence")


# ── through the router ───────────────────────────────────────────────────────

def test_the_reported_message_now_names_its_source():
    assert is_connector_lookup(
        "conflunece just added something new check and tell me what the content is"
    ) == {"confluence"}


def test_common_slips_on_long_names_are_caught():
    cases = {
        "check confluance for the onboarding spec": {"confluence"},
        "what's in hubspt about the renewal": {"hubspot"},
        "any tasks in clickpu about checkout": {"clickup"},
        "pull the firelfies call about pricing": {"fireflies"},
    }
    for question, expected in cases.items():
        assert is_connector_lookup(question) == expected, question


def test_ordinary_words_are_not_dragged_in():
    """The whole risk of fuzzy matching, pinned. None of these names a source."""
    for question in [
        "what's our confidence interval on that estimate?",
        "how did the conference go?",
        "what influence does pricing have on churn?",
        "we need a superset of those requirements",   # exact-name risk, unchanged
    ]:
        hints = is_connector_lookup(question) or set()
        assert "confluence" not in hints, question


def test_short_names_are_left_alone():
    """One edit from `slack` is `black` and from `jira` is `jars`. At five
    letters a typo and a different word are indistinguishable, so short names
    stay exact-match only."""
    assert is_connector_lookup("the black pipe leaked") is None
    assert is_connector_lookup("we filled the jars") is None


def test_a_correctly_spelled_name_is_unchanged():
    assert is_connector_lookup("check confluence for the spec") == {"confluence"}
    assert is_connector_lookup("check slack and jira for the decision") == {
        "slack", "jira",
    }
