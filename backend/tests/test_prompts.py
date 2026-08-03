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
    """The three placeholders (dataset, signal_context, corpus) are the only
    `.format()` keys; smoke-test it. (signal_context was added by the pipeline /
    signal-fusion work and is supplied by the cli/brief_runner callers.)"""
    out = prompts.BRIEF_USER_TEMPLATE.format(
        dataset="asurion", signal_context="SIGNALS", corpus="STUB"
    )
    assert "asurion" in out
    assert "STUB" in out
    assert "SIGNALS" in out


def test_predefined_ask_prompts_are_strings():
    assert len(prompts.PREDEFINED_ASK_PROMPTS) > 0
    for p in prompts.PREDEFINED_ASK_PROMPTS:
        assert isinstance(p, str)
        assert p.strip() == p


# ── ASK_SYSTEM_COMPANY_FACTS_ADDENDUM — precedence wording is load-bearing ────
# A blanket "the company is always right" would be a worse bug than the wrong-
# domain incident it fixes (stale positioning beating measured churn), so
# these are property tests on the actual wording, not just presence.


def test_company_facts_addendum_scopes_authority_to_identity():
    a = prompts.ASK_SYSTEM_COMPANY_FACTS_ADDENDUM
    for needle in ("IDENTITY AND INTENT", "website or domain", "product names",
                   "what it sells", "METHOD"):
        assert needle in a, f"addendum missing {needle!r}"


def test_company_facts_addendum_carves_out_empirical_claims():
    a = prompts.ASK_SYSTEM_COMPANY_FACTS_ADDENDUM
    assert "EMPIRICAL" in a
    assert "NO special weight" in a
    assert "Measured evidence wins" in a
    assert "label which one is the company's stated view" in a


def test_company_facts_addendum_has_no_blanket_authority_language():
    low = prompts.ASK_SYSTEM_COMPANY_FACTS_ADDENDUM.lower()
    for phrase in ("always right", "always correct", "always wins",
                   "in all cases", "overrides everything"):
        assert phrase not in low, f"blanket-authority phrase found: {phrase!r}"


def test_company_facts_addendum_length_within_bounds():
    assert 400 <= len(prompts.ASK_SYSTEM_COMPANY_FACTS_ADDENDUM) <= 1600


def test_company_facts_addendum_frames_configuration_not_verified_fact():
    """This ticket's revised framing (planner decision): the block is
    configuration of record — what the workspace typed into its own fields,
    typos included — never independently verified truth."""
    a = prompts.ASK_SYSTEM_COMPANY_FACTS_ADDENDUM
    assert "WORKSPACE CONFIGURATION" in a
    assert "configuration of record" in a
    assert "not independently verified fact" in a


def test_ask_cache_version_is_unchanged():
    """Bumping this would demote/regenerate every pre-warmed cached row —
    unnecessary here since the warm path never carries workspace configuration
    (see test_generate_one_sync_prompt_has_no_company_facts in
    test_ask_runner.py) and no skill-sourced value can reach a cached row."""
    assert prompts.ASK_CACHE_VERSION == 5
