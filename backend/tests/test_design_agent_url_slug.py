"""Unit tests for the public-share-URL slugifier (app.design_agent.url_slug).

The SHARED_PARITY_CASES table below is the source of truth for the mirror-parity
check: the frontend test (web/app/lib/__tests__/urlSlug.test.ts) asserts the same
(input, fallback, expected) tuples against the TS port, so the two slugifiers
provably produce identical output for the same inputs.
"""
from __future__ import annotations

from app.design_agent.url_slug import url_slugify

# (input, fallback, expected) — MIRRORED verbatim in urlSlug.test.ts (AC3).
SHARED_PARITY_CASES = [
    ("Lab X", "item", "lab-x"),
    ("  Acme!! Corp  ", "item", "acme-corp"),
    ("Customer Onboarding Revamp", "prototype", "customer-onboarding-revamp"),
    ("", "company", "company"),
    ("Foo & Bar / Baz", "item", "foo-bar-baz"),
    (
        "aaaaaaa-bbbbbbb-ccccccc-ddddddd-eeeeeee-fffffff",
        "item",
        "aaaaaaa-bbbbbbb-ccccccc-ddddddd-eeeeeee",
    ),
]


def test_url_slugify_lowercases_and_dashes():
    assert url_slugify("Lab X") == "lab-x"


def test_url_slugify_collapses_and_strips():
    assert url_slugify("  Acme!! Corp  ") == "acme-corp"


def test_url_slugify_empty_and_none_return_fallback():
    assert url_slugify("", fallback="company") == "company"
    assert url_slugify(None, fallback="prototype") == "prototype"  # type: ignore[arg-type]
    assert url_slugify("   ", fallback="company") == "company"
    # A string that is ALL punctuation slugifies to empty → fallback.
    assert url_slugify("!!! ???", fallback="item") == "item"


def test_url_slugify_caps_length_no_trailing_dash():
    # 60-char input, cap 40 → ≤40 chars, and the cut must not leave a trailing '-'.
    long = "word-" * 12  # 60 chars, ends "...word-"
    out = url_slugify(long, max_length=40)
    assert len(out) <= 40
    assert not out.endswith("-")
    # The 40-char slice ("word-" x8) ends on a dash, which the post-cut strip
    # removes → 8 dash-joined "word"s (39 chars), no trailing dash.
    assert out == "word-word-word-word-word-word-word-word"


def test_url_slugify_default_fallback_is_item():
    assert url_slugify("") == "item"


def test_url_slugify_matches_shared_parity_cases():
    # These exact expectations are also asserted by the TS mirror (AC3).
    for name, fallback, expected in SHARED_PARITY_CASES:
        assert url_slugify(name, fallback=fallback) == expected
