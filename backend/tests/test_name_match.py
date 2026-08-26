"""Tests for app.name_match — the general name-resolution primitive.

Table-driven and scenario-agnostic: the tables assert whole CLASSES of naming
convention resolve (all orderings, initials, nicknames, name↔name), and a
negative table asserts genuinely different people DECLINE. The real-data misses
that motivated this (reversed-order `trohad`, nickname `Jay`↔`Jason`) appear
only as INSTANCES of those general classes, never special-cased.
"""
from __future__ import annotations

import pytest

from app import name_match
from app.name_match import match_name


DOMAIN = "@example.com"


# ── tier 1: local-part conventions, ALL orderings ────────────────────────────
#
# One name (first=jane, last=doe) exercised across every convention a real org
# might use — including reversed / last-led forms and initials.

_CONVENTION_LOCALS = [
    "jane",            # first alone
    "jane.doe",        # first.last
    "jane_doe",        # first_last (separator-agnostic)
    "janedoe",         # firstlast
    "doe.jane",        # last.first  (reversed)
    "doejane",         # lastfirst   (reversed)
    "jdoe",            # finitial+last
    "doej",            # last+finitial (last-led)
    "janed",           # first+linitial
    "djane",           # linitial+first
    "jd",              # initials
    "dj",              # initials reversed
]


@pytest.mark.parametrize("local", _CONVENTION_LOCALS)
def test_local_part_conventions_all_orderings_resolve(local):
    assert match_name("Jane Doe", [local + DOMAIN]) == local + DOMAIN


def test_reversed_last_led_convention_instance_troha():
    """Real-data instance of the last-led class: David Troha → trohad@…
    (last + first-initial), which a first-name-only matcher missed."""
    assert match_name("David Troha", ["trohad" + DOMAIN]) == "trohad" + DOMAIN
    # and the other last-led orderings for the same name
    assert match_name("David Troha", ["troha.david" + DOMAIN])
    assert match_name("David Troha", ["dtroha" + DOMAIN])


# ── tier 2: nickname / diminutive normalization, BOTH directions ─────────────
#
# (spoken/nickname form, formal local part) and the reverse — the table is the
# spec, the Jay/Jason miss is one row of it.

_NICKNAME_CASES = [
    ("Jay Watson", "jason.watson"),      # the motivating miss
    ("Bob Smith", "robert.smith"),
    ("Bill Gates", "william.gates"),
    ("Mike Ross", "michael.ross"),
    ("Liz Lemon", "elizabeth.lemon"),
    ("Jim Halpert", "james.halpert"),
    ("Chris Traeger", "christopher.traeger"),
    ("Beth Harmon", "elizabeth.harmon"),
    ("Kate Austen", "katherine.austen"),
    # reverse direction: formal spoken name, nickname local part
    ("Robert Smith", "bob.smith"),
    ("William Gates", "bill.gates"),
    ("Michael Ross", "mike.ross"),
    ("Elizabeth Lemon", "liz.lemon"),
]


@pytest.mark.parametrize("name,local", _NICKNAME_CASES)
def test_nickname_normalization_resolves_both_directions(name, local):
    assert match_name(name, [local + DOMAIN]) == local + DOMAIN


def test_nickname_expansion_is_pure_data_and_symmetric():
    """name_variants is a symmetric equivalence lookup — a group member expands
    to the whole group, both directions, with no code path per name."""
    assert "jason" in name_match.name_variants("jay")
    assert "jay" in name_match.name_variants("jason")
    assert "robert" in name_match.name_variants("bob")
    assert "bob" in name_match.name_variants("robert")
    # unknown tokens map to just themselves
    assert name_match.name_variants("watson") == frozenset({"watson"})


# ── tier 1/2 for name↔name (Google Meet display-name participants) ───────────

_NAME_TO_NAME_CASES = [
    ("Jay Watson", "Jason Watson"),        # nickname, name-only
    ("Jason Watson", "Jay Watson"),        # reverse
    ("David Troha", "Troha, David"),       # reversed order, comma form
    ("Jane Doe", "Jane Doe"),              # identity
    ("Bob Smith", "Robert Smith"),         # nickname, name-only
]


@pytest.mark.parametrize("owner,participant", _NAME_TO_NAME_CASES)
def test_name_to_name_matching(owner, participant):
    assert match_name(owner, [participant]) == participant


# ── tier 3: calibrated fuzzy fallback picks a near-miss over the threshold ────

def test_fuzzy_fallback_tolerates_a_typo():
    """A one-character typo in the local part still resolves via fuzzy, above
    the 0.82 floor — the deterministic tiers having missed it."""
    assert match_name("Jane Doe", ["janedooe" + DOMAIN])  # doubled 'o'


# ── safe failure mode: genuinely different people DECLINE ─────────────────────

_NEGATIVE_CASES = [
    ("David Troha", ["alice.smith@x.com", "bob.jones@x.com"]),
    ("Jane Doe", ["john.smith@x.com"]),
    ("Jay Watson", ["james.bond@x.com"]),      # nickname first, WRONG surname
    ("Bob Smith", ["robert.jones@x.com"]),     # nickname first, WRONG surname
    ("David Troha", ["Alice Smith", "Bob Jones"]),  # name-only, different people
    ("Jane Doe", [""]),                        # empty candidate
    ("", ["jane.doe@x.com"]),                  # empty target
]


@pytest.mark.parametrize("target,candidates", _NEGATIVE_CASES)
def test_different_people_decline(target, candidates):
    assert match_name(target, candidates) is None


def test_first_exact_convention_hit_wins_over_a_fuzzy_other():
    """Given a mix, the exact-convention participant is returned, not a fuzzy
    lookalike."""
    got = match_name("Jane Doe", ["someone.else@x.com", "jane.doe@x.com"])
    assert got == "jane.doe@x.com"


def test_surname_alone_does_not_auto_match():
    """The bare last name is deliberately NOT a convention — it collides between
    different people who share a surname."""
    assert match_name("David Troha", ["troha@x.com"]) is None
