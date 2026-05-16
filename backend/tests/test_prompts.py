"""Sanity tests for prompt-version constants and template placeholders.

These constants are stamped into every cached brief / evidence / PRD / ask
row so the startup invalidation loop can detect a stale cache. They must
be positive ints — `None` or 0 would break the version-bump invalidation
logic in app.main.lifespan.

The BRIEF_USER_TEMPLATE is `.format()`-ed at request time with `dataset`
and `corpus`; if either placeholder goes missing, every brief generation
fails silently with a KeyError swallowed by the warmer.
"""
from app import prompts


def test_brief_schema_version_is_positive_int():
    assert isinstance(prompts.BRIEF_SCHEMA_VERSION, int)
    assert prompts.BRIEF_SCHEMA_VERSION > 0


def test_evidence_template_version_is_positive_int():
    assert isinstance(prompts.EVIDENCE_TEMPLATE_VERSION, int)
    assert prompts.EVIDENCE_TEMPLATE_VERSION > 0


def test_prd_template_version_is_positive_int():
    assert isinstance(prompts.PRD_TEMPLATE_VERSION, int)
    assert prompts.PRD_TEMPLATE_VERSION > 0


def test_ask_cache_version_is_positive_int():
    assert isinstance(prompts.ASK_CACHE_VERSION, int)
    assert prompts.ASK_CACHE_VERSION > 0


def test_brief_user_template_has_dataset_placeholder():
    assert "{dataset}" in prompts.BRIEF_USER_TEMPLATE


def test_brief_user_template_has_corpus_placeholder():
    assert "{corpus}" in prompts.BRIEF_USER_TEMPLATE


def test_brief_user_template_formats_without_keyerror():
    """The two placeholders are the only `.format()` keys; smoke-test it."""
    out = prompts.BRIEF_USER_TEMPLATE.format(dataset="asurion", corpus="STUB")
    assert "asurion" in out
    assert "STUB" in out


def test_predefined_ask_prompts_are_strings():
    assert len(prompts.PREDEFINED_ASK_PROMPTS) > 0
    for p in prompts.PREDEFINED_ASK_PROMPTS:
        assert isinstance(p, str)
        assert p.strip() == p
