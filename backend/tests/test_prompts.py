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
    # Upper bound raised from 1600 when the addendum stopped describing three
    # identity fields and started describing the whole onboarding capture plus
    # the captured-versus-connected routing rule. Still a bloat guard.
    assert 400 <= len(prompts.ASK_SYSTEM_COMPANY_FACTS_ADDENDUM) <= 3200


def test_company_facts_addendum_routes_stated_answers_to_configuration():
    """The reported failure: asked for the north star, chat said no connected
    source states one and sent the team to Connectors — for a value they had
    already chosen in onboarding. The addendum has to say which questions this
    block answers, and that a connector is not how they get answered."""
    a = prompts.ASK_SYSTEM_COMPANY_FACTS_ADDENDUM
    for needle in ("north star", "Connected sources record what HAPPENED",
                   "never suggest connecting a data source"):
        assert needle in a, f"addendum missing {needle!r}"


def test_company_facts_addendum_names_where_a_missing_field_is_set():
    """A genuinely unset field sends the user to the Settings section that owns
    it — the alternative the model reached for otherwise was Connectors, which
    fills none of them."""
    a = prompts.ASK_SYSTEM_COMPANY_FACTS_ADDENDUM
    for section in ("Settings → Metrics", "Settings → Business Context",
                    "Settings → Company Profile",
                    "Settings → Process & Planning"):
        assert section in a, f"addendum missing {section!r}"


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

    The third raise, 4300 -> 5400, pays for the two document-RESOLUTION
    clauses, and they are the only clauses here that describe something the
    model must do rather than something it must not conclude. (g) says a
    "{DOCUMENT_REFERENT_HEADING}" section names the one document the message
    is about, worked out from the message and the earlier turns — without it
    the section is just another heading and "what does it say about pricing?"
    has no subject. (h) says an ambiguity section means ASK, and says it at
    length on purpose: the cheap failure is a model that reads two candidate
    titles and picks the first, which is the exact behaviour the section
    exists to prevent and the one a terse clause would not stop.

    (g) also carries a sentence with no imperative in it at all — that most
    questions have no such section and that this is normal. It is there
    because the opposite reading is the expensive one: a model that treats the
    section's ABSENCE as a prompt to go find a document to be about would
    reintroduce, from the prompt side, the false-referent failure the resolver
    is built to make unreachable.

    5400 is ~7% above the current 5026 — room for wording repairs, not room
    for another feature's worth of instructions. This string is prepended to
    the system prompt of every ask that renders a document block, so its size
    is a per-request cost and the ceiling is the thing that keeps it honest.
    """
    assert 600 <= len(prompts.ASK_SYSTEM_DOCUMENTS_ADDENDUM) <= 5400


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
