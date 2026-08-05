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


# ── ASK_SYSTEM_DOCUMENTS_ADDENDUM — the existence-vs-retrieval contract ─────
# The negative-space clauses (never deny an indexed document's existence,
# never blame a specific integration) are what the incident's answer
# violated, so they are asserted here rather than left to review.


def test_ask_system_documents_addendum_length_bounds():
    """Raised 2500 -> 3400 when topical selection landed, and 3400 -> 4300
    when connected-source documents became selectable. Both deliberate, and
    both decided by reading the clauses rather than by CI complaining.

    The first raise paid for four clauses: documents are chosen by TOPIC, so
    the prompt has to carry (a) that a one-line summary is a routing hint and
    not something to answer from, (b) that an automatically-selected document
    may be irrelevant and should be ignored rather than summarised, (c) what
    to do when two loaded documents disagree, and (d) that an Index marked
    PARTIAL no longer licenses an absence claim.

    The second pays for two more, and both are the incident itself rather
    than polish. The Index now also lists documents that live in a connected
    system, so (e) states that a Confluence page or a Drive file in the Index
    EXISTS and must not be answered with "go and check that integration" —
    the verbatim deflection a user got while the page was sitting in the
    wiki. And because those bodies are fetched at read time, a fetch can
    fail, so (f) gives that its own marker and its own instruction: say the
    contents could not be loaded and why, never that the document is absent.
    Without (f) the failure path collapses back into exactly the false denial
    this work exists to remove.

    The third raise, 4300 -> 5000, pays for the two clauses that make
    REFERENCE RESOLUTION mean anything. Selection can now do more than rank:
    it can resolve the specific document a message refers to — named, or
    established earlier in the thread and pointed at with "it" — and it can
    decline to. Both halves need saying, and neither is polish.

    (g) marks the resolved document and tells the model to answer FROM it,
    explicitly overriding rule 6 for that one entry. Without it a resolved
    referent is indistinguishable from the two topical documents beside it,
    and rule 6's ignore-if-irrelevant invites the model to skip the very
    document that was asked for.

    (h) is the abstention, and it is the clause the whole stage exists for.
    When a reference is ambiguous the block says so and lists the
    possibilities; this tells the model to ASK rather than pick the
    closest-looking one. Without it the section renders and the model is free
    to ignore it, which is worse than never abstaining — it looks careful and
    answers about the wrong document anyway. Deliberately loaded language
    ("a confidently wrong document is worse than one short question") because
    the failure it prevents is a confident answer, not a missing one.

    Both were cut hard before this raise was taken: 1039 characters of first
    drafts became 601 by deleting restatement, not contract. What is left is
    the marker, the instruction, the rule-6 override and the ask-instead-of-
    guess rule, with no sentence that only rephrases another.

    5000 is ~7% above the current 4666 — again room for wording repairs, not
    room for another feature's worth of instructions. This string is prepended
    to the system prompt of every ask that renders a document block, so its
    size is a per-request cost and the ceiling is the thing that keeps it
    honest.
    """
    assert 600 <= len(prompts.ASK_SYSTEM_DOCUMENTS_ADDENDUM) <= 5000


def test_ask_system_documents_addendum_required_content():
    a = prompts.ASK_SYSTEM_DOCUMENTS_ADDENDUM
    for needle in (
        "UPLOADED DOCUMENTS", "Index", "Contents loaded for this question",
        "EXISTS", "[Source:",
    ):
        assert needle in a, f"addendum missing {needle!r}"


def test_ask_system_documents_addendum_negative_space():
    a = prompts.ASK_SYSTEM_DOCUMENTS_ADDENDUM
    assert "never" in a
    assert "not in any connected source" in a


def test_ask_cache_version_unchanged():
    """Same invariant as test_ask_cache_version_is_unchanged, named per the
    ticket's planner-authored test list: a document appended to the answer
    prompt is additive (empty for a tenant with no uploads), so it does not
    require demoting every pre-warmed cached row."""
    assert prompts.ASK_CACHE_VERSION == 5
