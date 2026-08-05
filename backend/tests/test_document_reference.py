"""Which document does a chat message REFER to — and when must we refuse to say?

Two layers, deliberately:

  * `app.document_reference` on its own — pure, no DB, no model. This is where
    the guards live, so this is where they are pinned.
  * `ask_runner.document_grounding` Stage R end to end — that the resolved
    document actually gets its BODY loaded and marked, that an abstention
    reaches the prompt as an abstention, and that Confluence and Google Drive
    behave identically to an upload.

THE FAILURE THIS SUITE EXISTS TO PREVENT is not "we didn't find the document".
It is "we found the WRONG document and said so confidently". Every abstention
test here is therefore a real assertion, not a soft one: a resolver that
guesses passes none of them.

The precedent bug, pinned in `test_substring_overreach_*`: `call_index`
matched query terms as bare substrings, so "can" (three chars, survived the
stopword strip) matched inside "Candidate" and hijacked a plural question into
a single-document answer about something unrelated. The same shape is
reachable here — "can you summarize our docs" against a page titled
"Candidate Scorecard" — and must not resolve.
"""
from __future__ import annotations

import inspect

import pytest

_CID = "co-docref"


# ─────────────────────────── test doubles ───────────────────────────


class _Doc:
    """A catalog row, only the fields the resolver reads.

    A stand-in rather than a real `CatalogDocument` so a title/topics case can
    be written in one line — the resolver takes `Sequence[Any]` and touches
    only these attributes, which this pins.
    """

    def __init__(self, title, *, provider="confluence", external_id=None,
                 source_name="", topics=()):
        self.title = title
        self.provider = provider
        self.external_id = external_id or title.lower().replace(" ", "-")
        self.source_name = source_name
        self.topics = list(topics)
        self.summary = ""

    def __repr__(self):  # pragma: no cover — assertion output only
        return f"<Doc {self.title!r}>"


def _turn(role, content):
    return {"role": role, "content": content}


# ═══════════════════ Layer 1 — the resolver, in isolation ═══════════════════


# ── Resolution from the message alone ──────────────────────────────────────


def test_resolves_a_confluence_page_the_message_names():
    from app.document_reference import resolve_documents

    docs = [
        _Doc("Q3 Pricing Teardown"),
        _Doc("Onboarding Runbook"),
        _Doc("SOC 2 Evidence Log"),
    ]
    ref = resolve_documents("what does the Q3 pricing teardown conclude?", docs)

    assert ref.referenced is True
    assert ref.abstained is False
    assert [d.title for d in ref.documents] == ["Q3 Pricing Teardown"]
    assert ref.basis == "named"


def test_resolves_without_the_exact_title_string():
    """The whole point of Stage R over Stage N: Stage N needs the normalized
    title to be a literal substring of the message. "the pricing teardown" is
    not — and it is obviously that document."""
    from app.document_reference import resolve_documents

    docs = [_Doc("Q3 Pricing Teardown"), _Doc("Onboarding Runbook")]
    ref = resolve_documents("summarize the pricing teardown for me", docs)

    assert [d.title for d in ref.documents] == ["Q3 Pricing Teardown"]


def test_resolves_on_topics_not_only_title():
    from app.document_reference import resolve_documents

    docs = [
        _Doc("Runbook 14", topics=["incident escalation", "pagerduty rotation"]),
        _Doc("Runbook 15", topics=["release checklist"]),
    ]
    ref = resolve_documents("what's in the pagerduty rotation runbook?", docs)

    assert [d.title for d in ref.documents] == ["Runbook 14"]


def test_short_whole_word_still_resolves():
    """`_MIN_SUBSTRING_TERM` floors SUBSTRINGS only. "SSO" and "Q3" are real
    document names and a whole-word hit is trusted at any length — flooring
    those too would make short-titled documents unreachable."""
    from app.document_reference import resolve_documents

    docs = [_Doc("SSO Rollout Plan"), _Doc("Billing Migration Notes")]
    ref = resolve_documents("what does the SSO rollout say about Okta?", docs)

    assert [d.title for d in ref.documents] == ["SSO Rollout Plan"]


# ── The substring-overreach guard (the call_index bug, not repeated) ───────


def test_substring_overreach_can_does_not_match_candidate():
    """THE precedent bug. "can" is three characters and sits inside
    "Candidate". It must not resolve, by two independent guards: it is an
    ask-word (stripped before matching at all) and it is under
    `_MIN_SUBSTRING_TERM` (so even unstripped it could not match mid-word)."""
    from app.document_reference import resolve_documents

    docs = [_Doc("Candidate Scorecard — Staff Engineer")]
    ref = resolve_documents("can you summarize our recent documents?", docs)

    assert ref.referenced is False, (
        "a plural, general question naming no document resolved to one — this "
        "is the call_index 'can' inside 'Candidate' overreach, repeated"
    )
    assert ref.documents == []


def test_substring_overreach_short_term_never_matches_mid_word():
    """Directly on the scorer, so the guard is pinned even if the stopword
    list changes. "art" is 3 chars and sits inside "Quarterly"."""
    from app.document_reference import _MIN_SUBSTRING_TERM, score_document

    assert _MIN_SUBSTRING_TERM == 4
    assert score_document(["art"], _Doc("Quarterly Roadmap")) == 0


def test_substring_hit_alone_cannot_pin():
    """A >=4-char mid-word hit scores, but scores BELOW the pin floor. It may
    help rank; it may never be the resolved referent on its own."""
    from app.document_reference import resolve_documents, score_document

    doc = _Doc("Roadmapping Guidelines")
    # "roadmap" is a 7-char substring of "roadmapping" — a real hit...
    assert score_document(["roadmap"], doc) > 0
    # ...but not a whole word, so it cannot pin.
    ref = resolve_documents("anything on roadmap?", [doc])
    assert ref.documents == []


# ── The real-workspace bug: a common word shared with a title ──────────────
#
# These titles are REAL, taken from a production workspace's catalog. The
# invented titles elsewhere in this file all happened to avoid question words,
# which is exactly why the fixtures missed this and running the resolver
# against real metadata caught it in one pass.


def test_a_question_word_inside_a_title_does_not_pin_it():
    """"how" is a whole word in "Template - How-to guide". A question about
    the BUSINESS, naming no document, must not resolve to it.

    Two independent guards now: "how" is an ask-word (stripped before
    matching), and a lone short whole-word hit cannot pin regardless."""
    from app.document_reference import resolve_documents

    docs = [
        _Doc("Template - How-to guide"),
        _Doc("Template - Meeting notes"),
        _Doc("Product requirements"),
    ]
    ref = resolve_documents("how many customers do we have?", docs)

    assert ref.documents == [], (
        "an ordinary business question pinned a document on the single shared "
        "word 'how' — the confidently-wrong-document failure this module "
        "exists to prevent"
    )
    assert ref.referenced is False


def test_a_bare_pronoun_question_does_not_pin_on_a_shared_word():
    from app.document_reference import resolve_documents

    docs = [_Doc("Template - How-to guide")]
    ref = resolve_documents(
        "how many seats is it?", docs,
        history=[_turn("user", "we bought more licences")],
    )
    assert ref.documents == []
    assert ref.abstained is False


def test_one_common_word_is_never_enough_to_pin():
    """The CLASS, not just the "how" instance. No stopword list is ever
    complete, so a lone SHORT whole-word match must not resolve even when the
    word is not a stopword at all."""
    from app.document_reference import resolve_documents

    docs = [_Doc("Widget Plan")]
    ref = resolve_documents("is the plan approved?", docs)

    assert ref.documents == []


def test_one_title_word_pins_ONLY_with_a_document_cue():
    """Single-word titles must stay reachable without reopening the class.

    A length-based escape hatch used to allow this — any whole-word hit of
    >= 6 chars pinned. That let "how is onboarding going for new hires?" pin a
    page titled "Onboarding": a question about the BUSINESS deflected into a
    wiki page. Length is the wrong signal, because "onboarding" is both long
    and completely ordinary.

    The signal that separates them is whether the message is talking about a
    DOCUMENT at all."""
    from app.document_reference import resolve_documents

    docs = [_Doc("Onboarding"), _Doc("Billing Migration Notes")]

    # No document cue — this is a question about the business.
    assert resolve_documents(
        "how is onboarding going for new hires?", docs
    ).documents == []
    # A document cue — now it is a reference.
    assert [d.title for d in resolve_documents(
        "what does the onboarding doc say about SSO?", docs
    ).documents] == ["Onboarding"]


def test_a_topic_word_never_establishes_a_reference():
    """Topics feed RANKING, never the reference gate.

    "what's our pricing strategy for enterprise?" shares a topic word with a
    teardown and names nothing. Counting topic hits toward the gate pinned
    that page and, via rule 10, told the model to answer FROM it — a general
    strategy question answered out of one wiki page."""
    from app.document_reference import resolve_documents

    docs = [
        _Doc("Q3 Pricing Teardown", topics=["pricing", "discounts"]),
        _Doc("Onboarding"),
    ]
    assert resolve_documents(
        "what's our pricing strategy for enterprise?", docs
    ).documents == []
    # The same catalog, a message that does name it, still resolves.
    assert [d.title for d in resolve_documents(
        "summarize the Q3 pricing teardown", docs
    ).documents] == ["Q3 Pricing Teardown"]


def test_repeating_one_word_cannot_satisfy_the_two_word_gate():
    """Hits were counted per occurrence, so a message repeating a single word
    could clear a gate that asks for two DISTINCT ones — the gate bypassed by
    repetition rather than by evidence."""
    from app.document_reference import query_terms, resolve_documents

    assert query_terms("pricing pricing pricing") == ["pricing"]
    docs = [_Doc("Pricing"), _Doc("Roadmap 2026")]
    assert resolve_documents("pricing pricing pricing", docs).documents == []


def test_generic_words_alone_name_nothing():
    from app.document_reference import resolve_documents

    docs = [_Doc("Internal Customer Research 2026")]
    ref = resolve_documents("show me our recent internal customer docs", docs)

    assert ref.referenced is False
    assert ref.documents == []


# ── Abstention: two similar titles ─────────────────────────────────────────


def test_abstains_between_two_similarly_named_documents():
    """The disambiguation case. Both pages match every surviving term equally,
    so there is no basis to prefer one — and picking either would be a coin
    flip presented to the user as an answer."""
    from app.document_reference import resolve_documents

    docs = [
        _Doc("Q3 Pricing Teardown", external_id="page-q3"),
        _Doc("Q4 Pricing Teardown", external_id="page-q4"),
    ]
    ref = resolve_documents("what does the pricing teardown say about discounts?", docs)

    assert ref.referenced is True
    assert ref.abstained is True
    assert ref.documents == [], "picked one of two equally-matching documents"
    assert {d.title for d in ref.candidates} == {
        "Q3 Pricing Teardown", "Q4 Pricing Teardown"
    }
    assert "more than one document" in ref.reason


def test_a_distinguishing_term_breaks_the_tie():
    """The complement of the test above — abstention must not be so eager that
    naming the distinguishing word stops working."""
    from app.document_reference import resolve_documents

    docs = [
        _Doc("Q3 Pricing Teardown", external_id="page-q3"),
        _Doc("Q4 Pricing Teardown", external_id="page-q4"),
    ]
    ref = resolve_documents("what does the Q4 pricing teardown say?", docs)

    assert ref.abstained is False
    assert [d.title for d in ref.documents] == ["Q4 Pricing Teardown"]


def test_narrow_candidates_applies_a_disambiguation_reply():
    from app.document_reference import narrow_candidates

    candidates = [_Doc("Q3 Pricing Teardown"), _Doc("Q4 Pricing Teardown")]
    assert [d.title for d in narrow_candidates("the Q4 one", candidates)] == [
        "Q4 Pricing Teardown"
    ]


# ── Answering our own clarifying question ──────────────────────────────────
#
# An abstention the user cannot answer is WORSE than not abstaining: we ask
# "did you mean Q3 or Q4?", they say "the Q4 one", and every guard that made
# the first abstention correct fires again on the reply. This is the dead end
# `call_index` records — a disambiguation that "poses a question it cannot read
# the answer to". These pin the fix.


def _asked_which(original: str) -> list[dict]:
    """History whose last assistant turn is a REALISTIC clarifying question.

    This used to fabricate the literal `UNRESOLVED_REFERENCE_HEADING` block as
    assistant content, and that made every test below worthless: the heading
    is rendered into the SYSTEM PROMPT, while an assistant turn holds the
    model's reply. Production never puts one in the other, so the tests passed
    against a state that could not occur and step 0 was dead in the product
    while green here.

    What prompt rule 11 actually produces is an answer that names the
    candidate titles and asks. That is what this fixture is now — the shape
    the detector keys on, and the shape a real model writes.
    """
    return _asked_which_of(
        original, "Q3 Pricing Teardown", "Q4 Pricing Teardown"
    )


def _asked_which_of(original: str, *titles: str) -> list[dict]:
    """Same, for a specific pair of candidate titles."""
    listed = ", ".join(titles[:-1]) + f" and {titles[-1]}"
    return [
        _turn("user", original),
        _turn("assistant",
              f"I can see a few documents that could match: {listed} — "
              "which did you mean?"),
    ]


@pytest.mark.parametrize("reply,expected", [
    ("the Q4 one", "Q4 Pricing Teardown"),
    ("Q4", "Q4 Pricing Teardown"),
    ("Q3", "Q3 Pricing Teardown"),
    ("the second one", "Q4 Pricing Teardown"),
    ("the first one", "Q3 Pricing Teardown"),
    ("2", "Q4 Pricing Teardown"),
])
def test_a_reply_to_our_question_resolves(reply, expected):
    """Each of these fails every cold-start guard — "Q4" is two characters and
    "the second one" names nothing — and each is a perfectly good answer to a
    question we just asked about a list we just showed."""
    from app.document_reference import resolve_documents

    docs = [_Doc("Q3 Pricing Teardown"), _Doc("Q4 Pricing Teardown")]
    ref = resolve_documents(
        reply, docs,
        history=_asked_which("what does the pricing teardown say about discounts?"),
    )

    assert ref.abstained is False, "the user answered our question and we asked again"
    assert [d.title for d in ref.documents] == [expected]
    assert ref.basis == "clarified"


def test_a_reply_narrows_on_the_TITLE_the_user_was_shown_not_on_topics():
    """Real-workspace shape. Both "Product requirements" pages carry "template"
    in their TOPICS — "software development template" and "product
    requirements template" — while only one has it in the title.

    The options we render are `- {title} ({provider})`, so "the template one"
    is an unambiguous choice between what was on screen. Narrowing against
    topics made it match both and re-ask, for a reason invisible to the user.
    Topics are the right signal for FINDING a document and the wrong one for
    picking between documents already named."""
    from app.document_reference import resolve_documents

    docs = [
        _Doc("Product requirements", external_id="plain",
             topics=["product requirements", "software development template"]),
        _Doc("Template - Product requirements", external_id="tpl",
             topics=["product requirements template", "user stories"]),
    ]
    ref = resolve_documents(
        "the template one", docs,
        history=_asked_which_of(
            "what does the product requirements page say?",
            "Product requirements", "Template - Product requirements",
        ),
    )

    assert [d.title for d in ref.documents] == ["Template - Product requirements"]


def test_a_reply_that_still_does_not_choose_re_abstains():
    """A reply that matches BOTH offered documents keeps asking, over the two
    that were actually offered — never over the catalog at large."""
    from app.document_reference import resolve_documents

    docs = [_Doc("Q3 Pricing Teardown"), _Doc("Q4 Pricing Teardown")]
    ref = resolve_documents(
        "the pricing teardown one", docs,
        history=_asked_which("what does the pricing teardown say about discounts?"),
    )

    assert ref.documents == []
    assert ref.abstained is True
    assert {d.title for d in ref.candidates} == {
        "Q3 Pricing Teardown", "Q4 Pricing Teardown"
    }


def test_a_reply_naming_a_DIFFERENT_document_wins_over_the_old_candidates():
    """The user changed their mind. Narrowing the reply against the previous
    options returned one of them — the user named another document outright
    and got a stale candidate back."""
    from app.document_reference import resolve_documents

    docs = [
        _Doc("Q3 Pricing Teardown"), _Doc("Q4 Pricing Teardown"),
        _Doc("Q4 Board Deck"),
    ]
    ref = resolve_documents(
        "forget it - summarize the Q4 board deck", docs,
        history=_asked_which("what does the pricing teardown say?"),
    )

    assert [d.title for d in ref.documents] == ["Q4 Board Deck"]


def test_a_clarification_reply_never_reaches_outside_what_was_offered():
    """`candidates or list(documents)` fell back to the WHOLE CATALOG, so an
    ordinal reply returned an arbitrary row reported as the user's choice."""
    from app.document_reference import resolve_documents

    docs = [
        _Doc("Pricing"), _Doc("Roadmap 2026"), _Doc("Security Review"),
        _Doc("Q3 Board Deck"), _Doc("Runbook"), _Doc("SSO Rollout"),
    ]
    # An assistant turn that names NOTHING — so nothing was offered.
    history = [
        _turn("user", "what does it say about pricing?"),
        _turn("assistant", "Could you say a bit more about what you need?"),
    ]
    ref = resolve_documents("the second one", docs, history=history)

    assert ref.documents == [], (
        "an ordinal reply pinned a catalog row that was never offered"
    )


def test_only_the_MOST_RECENT_assistant_turn_counts_as_our_question():
    """A clarification the user already moved past must not capture an
    unrelated message later in the thread."""
    from app.document_reference import resolve_documents

    docs = [_Doc("Q3 Pricing Teardown"), _Doc("Q4 Pricing Teardown")]
    history = _asked_which("what does the pricing teardown say?") + [
        _turn("user", "never mind"),
        _turn("assistant", "No problem."),
    ]
    ref = resolve_documents("Q4", docs, history=history)

    # Falls through to cold resolution, where "Q4" is correctly too weak.
    assert ref.documents == []
    assert ref.basis != "clarified"


# ── Resolution from prior-turn context ─────────────────────────────────────


def test_resolves_a_followup_against_the_document_named_earlier():
    """The headline case: "what does it say about pricing?" carries no naming
    word at all. Only the thread makes it meaningful."""
    from app.document_reference import resolve_documents

    docs = [
        _Doc("Q3 Pricing Teardown", external_id="page-q3"),
        _Doc("Competitor Pricing Sweep", external_id="page-sweep"),
    ]
    history = [
        _turn("user", "summarize the Q3 pricing teardown"),
        _turn("assistant", "Here is the summary you asked for."),
    ]
    ref = resolve_documents("what does it say about discounts?", docs, history=history)

    assert ref.referenced is True
    assert ref.abstained is False
    assert ref.basis == "anaphoric"
    assert [d.title for d in ref.documents] == ["Q3 Pricing Teardown"], (
        "the follow-up resolved to a different document than the thread "
        "established — 'Competitor Pricing Sweep' is the wrong answer even "
        "though it also mentions pricing"
    )


def test_followup_resolves_from_an_assistant_turn_that_names_one_document():
    from app.document_reference import resolve_documents

    docs = [_Doc("Incident Postmortem 41"), _Doc("Incident Postmortem 42")]
    history = [
        _turn("user", "what happened last Tuesday?"),
        _turn("assistant", "The Incident Postmortem 42 covers that outage."),
    ]
    ref = resolve_documents("what does it say about the root cause?", docs,
                            history=history)

    assert [d.title for d in ref.documents] == ["Incident Postmortem 42"]


def test_followup_abstains_when_the_previous_answer_covered_several_documents():
    """"Here are your five documents" establishes NOTHING. A follow-up "what
    does it say" after a list has no referent, and inventing one is the bug."""
    from app.document_reference import resolve_documents

    docs = [_Doc("Incident Postmortem 41"), _Doc("Incident Postmortem 42")]
    history = [
        _turn("user", "what postmortems do we have?"),
        _turn("assistant",
              "Two: Incident Postmortem 41 and Incident Postmortem 42."),
    ]
    ref = resolve_documents("what does it say about the root cause?", docs,
                            history=history)

    assert ref.abstained is True
    assert ref.documents == []
    assert len(ref.candidates) == 2


def test_followup_makes_NO_REFERENCE_when_no_turn_established_anything():
    """An anaphor with nothing behind it is not a document reference.

    This used to ABSTAIN, which rendered "This message refers to a specific
    document… ask which one the user means" — with no candidates to offer. The
    user's follow-up about seat counts got a clarifying question about
    Confluence pages. Falling through silently to Stage T is the honest
    outcome: we have no evidence a document was meant."""
    from app.document_reference import resolve_documents

    docs = [_Doc("Q3 Pricing Teardown")]
    history = [
        _turn("user", "how many seats are we licensed for?"),
        _turn("assistant", "Forty."),
    ]
    ref = resolve_documents("what does it say about that?", docs, history=history)

    assert ref.documents == []
    assert ref.abstained is False, "a bare anaphor produced a clarifying question"
    assert ref.referenced is False


def test_followup_with_no_history_at_all_makes_no_reference():
    """Turn one, no thread. Abstaining here asked "which document?" and listed
    nothing, which is the worst possible first message."""
    from app.document_reference import resolve_documents

    ref = resolve_documents("what does it say about pricing?",
                            [_Doc("Q3 Pricing Teardown")])
    assert ref.documents == []
    assert ref.abstained is False
    assert ref.candidates == []


def test_the_commonest_followup_in_the_product_stays_quiet():
    """"can you summarize it?" after an ordinary answer.

    Weakest possible evidence — one short substring against a topic — used to
    be enough to declare a reference, so this asked which Confluence page the
    user meant when they had asked to summarize the previous answer."""
    from app.document_reference import resolve_documents

    docs = [
        _Doc("Q3 Pricing Teardown"),
        _Doc("Product requirements"),
        _Doc("Template - Product requirements"),
    ]
    history = [
        _turn("user", "what are our top 3 product requests from last week?"),
        _turn("assistant",
              "The top three were SSO, bulk export and a dark mode toggle."),
    ]
    for message in ("can you summarize it?", "is there more detail on it?"):
        ref = resolve_documents(message, docs, history=history)
        assert ref.documents == [], message
        assert ref.abstained is False, (
            f"{message!r} produced a clarifying question about documents"
        )


def test_a_first_message_naming_its_subject_resolves_rather_than_asking():
    """Empty history, a document cue AND a named subject. This abstained with
    zero candidates because the anaphoric branch claimed it and had no
    fallback to the message's own words."""
    from app.document_reference import resolve_documents

    docs = [_Doc("Q3 Pricing Teardown"), _Doc("Onboarding")]
    ref = resolve_documents("what does the doc say about Q3 pricing?", docs)

    assert [d.title for d in ref.documents] == ["Q3 Pricing Teardown"]


def test_history_containing_the_CURRENT_message_does_not_change_the_answer():
    """The frontend persists the user turn fire-and-forget and then fires the
    ask, so `_load_history` can return a thread that already contains the
    message being asked about. When it does, the backwards walk hits the
    current message first and re-resolves it on its own words — reproducing
    verbatim the failure the anaphora-first ordering exists to prevent.

    Traced: with the duplicate turn present, "what does it say about
    discounts?" pinned a "Discount Policy" page (its TOPICS carry "discounts")
    instead of the teardown under discussion. Intermittently, because it is a
    race.

    Fixed in the resolver rather than the frontend so the guarantee does not
    depend on one caller behaving; Slack and MCP assemble history their own
    way."""
    from app.document_reference import resolve_documents

    docs = [
        _Doc("Q3 Pricing Teardown", external_id="teardown"),
        _Doc("Discount Policy", external_id="policy",
             topics=["discounts", "rebates"]),
    ]
    current = "what does it say about discounts?"
    clean = [
        _turn("user", "summarize the Q3 Pricing Teardown"),
        _turn("assistant", "Here is the teardown summary."),
    ]
    raced = clean + [_turn("user", current)]

    assert [d.title for d in resolve_documents(current, docs, history=clean).documents] \
        == ["Q3 Pricing Teardown"]
    assert [d.title for d in resolve_documents(current, docs, history=raced).documents] \
        == ["Q3 Pricing Teardown"], (
            "the duplicated current turn changed the referent — the race is live"
        )


def test_reference_resolution_is_bounded_on_a_huge_attachment_turn(monkeypatch):
    """`_load_history` folds attachment text into a turn UNCLAMPED, so an
    imported PRD can put tens of thousands of characters into one turn.
    Measured before the clamp: 56 KB against a 200-row catalog cost 1.09 s of
    synchronous CPU inside the request, before any model call.

    Asserts the clamp is doing the work, not the wall clock — a timing
    assertion would be flaky on shared CI."""
    from app.document_reference import _MAX_TURN_CHARS, query_terms, resolve_documents

    docs = [_Doc(f"Runbook {i} Operations Guide") for i in range(200)]
    huge = "lorem ipsum pricing discount rollout " * 1600  # ~56 KB
    assert len(huge) > 50_000
    history = [_turn("user", huge), _turn("assistant", "ok")]

    resolve_documents("what does it say about pricing?", docs, history=history)

    # The clamp bounds what any single turn contributes...
    assert _MAX_TURN_CHARS <= 4000
    # ...and dedupe bounds the term count regardless of repetition.
    assert len(query_terms(huge)) < 20


def test_naming_a_document_overrides_the_established_one():
    """A thread about document A, then an explicit mention of B, must switch.
    Anaphora resolution running first would pin A forever."""
    from app.document_reference import resolve_documents

    docs = [
        _Doc("Q3 Pricing Teardown", external_id="page-q3"),
        _Doc("Security Review 2026", external_id="page-sec"),
    ]
    history = [_turn("user", "summarize the Q3 pricing teardown")]
    ref = resolve_documents("actually, what about the security review?", docs,
                            history=history)

    assert [d.title for d in ref.documents] == ["Security Review 2026"]
    assert ref.basis == "named"


def test_most_recent_establishment_wins():
    from app.document_reference import resolve_documents

    docs = [
        _Doc("Q3 Pricing Teardown", external_id="page-q3"),
        _Doc("Security Review 2026", external_id="page-sec"),
    ]
    history = [
        _turn("user", "summarize the Q3 pricing teardown"),
        _turn("assistant", "Done."),
        _turn("user", "now open the security review"),
        _turn("assistant", "Done."),
    ]
    ref = resolve_documents("what does it recommend?", docs, history=history)

    assert [d.title for d in ref.documents] == ["Security Review 2026"]


# ── No documents / no connector ────────────────────────────────────────────


def test_empty_catalog_reports_no_reference_not_an_abstention():
    """A workspace with no catalogued documents has not FAILED to identify one.
    Reporting an abstention would put "I couldn't tell which document you
    meant" in front of a user who has connected nothing."""
    from app.document_reference import resolve_documents

    ref = resolve_documents("what does the pricing teardown say?", [])

    assert ref.referenced is False
    assert ref.abstained is False
    assert ref.reason == ""


def test_anaphora_with_empty_catalog_does_not_abstain():
    from app.document_reference import resolve_documents

    ref = resolve_documents("what does it say?", [], history=[
        _turn("user", "summarize the pricing teardown"),
    ])
    assert ref.referenced is False
    assert ref.abstained is False


# ── Anaphora detection ─────────────────────────────────────────────────────


@pytest.mark.parametrize("message", [
    "what does it say about pricing?",
    "summarize that page",
    "can you pull more detail from that document?",
    "what's in the doc?",
    "tell me more about this",
])
def test_detects_document_anaphora(message):
    from app.document_reference import has_document_anaphora

    assert has_document_anaphora(message) is True


@pytest.mark.parametrize("message", [
    "I think that we should ship the pricing change",
    "how many customers churned last quarter?",
    "generate a PRD for usage-based billing",
    # A bare pronoun with NO reading cue. The most common false positive, and
    # the reason a bare anaphor needs corroboration: without the pairing this
    # printed "which document do you mean?" onto an ordinary question.
    "how many seats is it?",
    "when is it shipping?",
    "who owns it now?",
    "is it worth doing this quarter?",
])
def test_does_not_see_anaphora_where_there_is_none(message):
    from app.document_reference import has_document_anaphora

    assert has_document_anaphora(message) is False


def test_a_bare_pronoun_without_a_reading_cue_makes_no_reference(
    isolated_settings, catalog_candidates, confluence_bodies
):
    """The false positive, end to end: an ordinary question containing "it"
    must not produce an UNRESOLVED section."""
    from app.ask_runner import document_grounding

    db = isolated_settings["supabase"]
    _seed_catalog_row(
        db, provider="confluence", external_id="page-q3",
        title="Q3 Pricing Teardown", source_name="Product wiki",
        summary="Pricing analysis.",
    )
    catalog_candidates([])

    block, _ = document_grounding(
        _CID, "how many seats is it?",
        history=[_turn("user", "we bought more licences")],
    )

    assert "UNRESOLVED" not in block


# ═════════════ Layer 2 — Stage R inside document_grounding ═════════════

_CANDIDATES_FN = "document_find_candidates"


@pytest.fixture
def catalog_candidates():
    """Stub `document_find_candidates` — same shape as the fixture in
    test_ask_document_retrieval.py, kept local so the two suites cannot leak
    RPC stubs into each other."""
    from tests._fake_supabase import FakeSupabaseClient

    FakeSupabaseClient.rpc_returns.pop(_CANDIDATES_FN, None)
    FakeSupabaseClient.rpc_calls.clear()

    def _set(rows):
        FakeSupabaseClient.rpc_returns[_CANDIDATES_FN] = rows

    yield _set
    FakeSupabaseClient.rpc_returns.pop(_CANDIDATES_FN, None)
    FakeSupabaseClient.rpc_calls.clear()


def _seed_company(db, company_id=_CID):
    existing = db.table("companies").select("id").eq("id", company_id).execute().data
    if not existing:
        db.table("companies").insert(
            {"id": company_id, "slug": f"slug-{company_id}",
             "display_name": company_id}
        ).execute()


def _seed_catalog_row(
    db, *, provider, external_id, title, company_id=_CID, source_name="",
    summary="", topics=(), doc_date="2026-08-02T10:00:00+00:00",
):
    _seed_company(db, company_id)
    db.table("document_catalog").insert({
        "company_id": company_id,
        "provider": provider,
        "external_id": external_id,
        "title": title,
        "source_name": source_name,
        "content_hash": f"hash-{external_id}",
        "summary": summary,
        "topics": list(topics),
        "doc_date": doc_date,
    }).execute()


@pytest.fixture
def confluence_bodies(monkeypatch):
    """Serve Confluence page bodies from a dict, patched at the SAME seam
    `test_ask_document_retrieval.py` uses — `confluence_fetch.open_session` /
    `get_page`.

    Deliberately not `BodyResolver.resolve_confluence`. `isolated_settings`
    calls `_reload_app_modules()`, so patching a class attribute on
    `app.document_bodies` binds to whichever module object happened to exist
    at fixture time — which is stable when this file runs alone and NOT
    stable when it runs after a file that triggered a different reload
    sequence. `BodyResolver` imports `confluence_fetch` lazily inside the
    method, so patching the connector module is looked up at call time and
    survives any reload.
    """
    from app.connectors import confluence_fetch

    pages: dict[str, str] = {}

    def _open_session(enterprise_id):
        return object()

    def _get_page(session, page_id):
        if page_id in pages:
            return {"id": page_id, "text": pages[page_id]}
        return None

    monkeypatch.setattr(confluence_fetch, "open_session", _open_session)
    monkeypatch.setattr(confluence_fetch, "get_page", _get_page)
    return pages


@pytest.fixture
def confluence_outage(monkeypatch):
    """A wiki that is connected but unreachable — the fetch raises."""
    from app.connectors import confluence_fetch

    def _boom(session, page_id):
        raise RuntimeError("auth expired")

    monkeypatch.setattr(confluence_fetch, "open_session", lambda eid: object())
    monkeypatch.setattr(confluence_fetch, "get_page", _boom)


def _seed_drive_corpus_file(db, *, file_id, label, slug, name, text,
                            company_id=_CID):
    """A synced Drive file: its converted corpus markdown on disk, plus the
    `kg_source` provenance row recording where that markdown landed.

    The real thing rather than a patched `resolve_drive_body`, so this
    exercises the actual Drive body path — provenance lookup, path
    reconstruction and file read — the way the Drive tests in
    `test_ask_document_retrieval.py` do.
    """
    from app import document_bodies
    from app.datasets import dataset_path

    target = dataset_path(slug) / name
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(text)
    db.table("kg_source").insert({
        "id": document_bodies.drive_source_id(company_id, file_id),
        "enterprise_id": company_id,
        "source_type": "google_drive",
        "label": label,
        "config": {"file_id": file_id, "md_dataset": slug, "md_file": name},
        "status": "active",
    }).execute()


def test_stage_r_loads_a_confluence_body_the_message_implies(
    isolated_settings, catalog_candidates, confluence_bodies
):
    """End to end: the message never spells the title, and the page's live
    body reaches the prompt marked as the referent."""
    from app.ask_runner import document_grounding

    db = isolated_settings["supabase"]
    _seed_catalog_row(
        db, provider="confluence", external_id="page-q3",
        title="Q3 Pricing Teardown", source_name="Product wiki",
        summary="Usage-based billing replaces seat-based for enterprise.",
        topics=["usage-based billing", "enterprise pricing"],
    )
    confluence_bodies["page-q3"] = "GRANDFATHERED ACCOUNTS: three named logos."
    catalog_candidates([])

    block, _ = document_grounding(
        _CID, "what does the pricing teardown say about grandfathering?"
    )

    assert "GRANDFATHERED ACCOUNTS" in block, "the implied page's body did not load"
    assert "[THIS is the document the user's message refers to]" in block


def test_stage_r_resolves_a_followup_against_the_established_page(
    isolated_settings, catalog_candidates, confluence_bodies
):
    """(b) in the verification script — the follow-up loads the page from the
    PREVIOUS turn, not the one whose summary also mentions the topic."""
    from app.ask_runner import document_grounding

    db = isolated_settings["supabase"]
    _seed_catalog_row(
        db, provider="confluence", external_id="page-q3",
        title="Q3 Pricing Teardown", source_name="Product wiki",
        summary="Usage-based billing replaces seat-based.",
    )
    _seed_catalog_row(
        db, provider="confluence", external_id="page-sweep",
        title="Competitor Pricing Sweep", source_name="Product wiki",
        summary="Discount benchmarks across six competitors.",
    )
    confluence_bodies["page-q3"] = "TEARDOWN BODY — discount floor is 12%."
    confluence_bodies["page-sweep"] = "SWEEP BODY — competitor discounts."
    catalog_candidates([])

    block, _ = document_grounding(
        _CID,
        "what does it say about discounts?",
        history=[
            _turn("user", "summarize the Q3 pricing teardown"),
            _turn("assistant", "Here you go."),
        ],
    )

    assert "TEARDOWN BODY" in block
    assert "SWEEP BODY" not in block, (
        "the follow-up loaded the other pricing document — resolving against "
        "the message's own words instead of the thread"
    )


def test_stage_r_abstains_and_says_so_in_the_prompt(
    isolated_settings, catalog_candidates, confluence_bodies
):
    """(c) in the verification script. Two equally-matching pages: no referent
    is marked, and the block carries an explicit UNRESOLVED section so the
    model asks instead of guessing."""
    from app.ask_runner import document_grounding

    db = isolated_settings["supabase"]
    for external_id, title in (
        ("page-q3", "Q3 Pricing Teardown"), ("page-q4", "Q4 Pricing Teardown")
    ):
        _seed_catalog_row(
            db, provider="confluence", external_id=external_id, title=title,
            source_name="Product wiki", summary="Pricing analysis.",
        )
        confluence_bodies[external_id] = f"BODY OF {title}"
    catalog_candidates([])

    block, _ = document_grounding(
        _CID, "what does the pricing teardown say about discounts?"
    )

    assert "The document this message refers to is UNRESOLVED" in block
    assert "[THIS is the document the user's message refers to]" not in block, (
        "abstained and then marked one anyway"
    )
    assert "Q3 Pricing Teardown" in block and "Q4 Pricing Teardown" in block


def test_stage_r_is_inert_when_no_connector_is_present(
    isolated_settings, catalog_candidates
):
    """A company with neither Confluence nor Drive connected — no catalog rows
    at all. Grounding must behave exactly as before: no crash, no UNRESOLVED
    section, and no referent marker."""
    from app.ask_runner import document_grounding

    _seed_company(isolated_settings["supabase"])
    catalog_candidates([])

    block, manifest = document_grounding(
        _CID, "what does the pricing teardown say?",
        history=[_turn("user", "and what does it say?")],
    )

    assert block == ""
    assert manifest == []


def test_stage_r_covers_google_drive_documents(
    isolated_settings, catalog_candidates
):
    """Drive resolves through the same Stage R as Confluence, and its body
    comes off the real provenance path — the seam is the provider-dispatching
    `BodyResolver`, not the resolver."""
    from app.ask_runner import document_grounding

    db = isolated_settings["supabase"]
    _seed_catalog_row(
        db, provider="google_drive", external_id="drive-file-9",
        title="Enterprise Onboarding Runbook", source_name="Google Drive",
        summary="Steps for provisioning a new enterprise tenant.",
    )
    _seed_drive_corpus_file(
        db, file_id="drive-file-9", label="Enterprise Onboarding Runbook",
        slug="asurion", name="enterprise-onboarding-runbook.md",
        text="RUNBOOK BODY — step 1 is SSO.",
    )
    catalog_candidates([])

    block, _ = document_grounding(
        _CID, "what's in the enterprise onboarding runbook?"
    )

    assert "RUNBOOK BODY" in block
    assert "[THIS is the document the user's message refers to]" in block


def test_unfetchable_referent_states_the_reason_not_absence(
    isolated_settings, catalog_candidates, confluence_outage
):
    """A resolved page whose live fetch fails must read as "exists, could not
    load, here's why" — never as absence. The reference resolved correctly;
    only the fetch failed, and the two must not collapse."""
    from app.ask_runner import document_grounding

    db = isolated_settings["supabase"]
    _seed_catalog_row(
        db, provider="confluence", external_id="page-q3",
        title="Q3 Pricing Teardown", source_name="Product wiki",
        summary="Pricing analysis.",
    )
    catalog_candidates([])

    block, _ = document_grounding(
        _CID, "what does the Q3 pricing teardown say?"
    )

    assert "Q3 Pricing Teardown" in block
    assert "this document exists" in block
    assert "could not be loaded" in block
    lowered = block.lower()
    for forbidden in ("does not exist", "no such document",
                      "has not been uploaded"):
        assert forbidden not in lowered


def test_a_document_named_in_an_EARLIER_turn_is_not_named_by_this_one(
    isolated_settings, catalog_candidates, confluence_bodies
):
    """Stage R must resolve against the CURRENT message, not the thread's
    accumulated vocabulary.

    `document_grounding` requires `question` to be the user's bare
    current-turn message — a contract #1046 established by removing the
    folding at every call site, and one Stage R now depends on: resolving a
    reference against a folded thread would let a document named five turns
    ago count as named by this turn.

    An earlier revision of this branch carried a `reference_question`
    parameter to work around callers that folded. #1046 fixed the callers
    instead, so the parameter is gone and this pins the BEHAVIOUR that
    replaced it: prior turns reach Stage R only through `history`, where
    anaphora looks for them deliberately, and a topic-less current message
    pins nothing.
    """
    from app.ask_runner import document_grounding

    db = isolated_settings["supabase"]
    _seed_catalog_row(
        db, provider="confluence", external_id="page-sec",
        title="Security Review 2026", source_name="Product wiki",
        summary="Pen test findings.",
    )
    confluence_bodies["page-sec"] = "SECURITY BODY"
    catalog_candidates([])

    block, _ = document_grounding(
        _CID,
        "how many seats are we licensed for?",
        history=[
            _turn("user", "summarize the Security Review 2026"),
            _turn("assistant", "Done."),
        ],
    )

    assert "[THIS is the document the user's message refers to]" not in block, (
        "a document named in an earlier turn was treated as named by this "
        "one — the current message names nothing and carries no anaphor"
    )
    assert "UNRESOLVED" not in block


def test_topical_fill_still_runs_after_an_abstention(
    isolated_settings, catalog_candidates, confluence_bodies
):
    """Abstention withholds the REFERENT CLAIM, not the prompt. Stage T keeps
    its existing no-floor behaviour, so an ambiguous message degrades to what
    shipped before Stage R rather than to nothing."""
    from app.ask_runner import document_grounding

    db = isolated_settings["supabase"]
    for external_id, title in (
        ("page-q3", "Q3 Pricing Teardown"), ("page-q4", "Q4 Pricing Teardown")
    ):
        _seed_catalog_row(
            db, provider="confluence", external_id=external_id, title=title,
            source_name="Product wiki", summary="Pricing analysis.",
        )
        confluence_bodies[external_id] = f"BODY OF {title}"
    catalog_candidates([{
        "id": "cat-page-q3", "provider": "confluence", "external_id": "page-q3",
        "title": "Q3 Pricing Teardown", "source_name": "Product wiki",
        "summary": "Pricing analysis.", "topics": [], "url": None,
        "doc_date": "2026-08-02T10:00:00+00:00", "conversation_id": None,
        "score": 0.04,
    }])

    block, manifest = document_grounding(
        _CID, "what does the pricing teardown say about discounts?"
    )

    assert "The document this message refers to is UNRESOLVED" in block
    entry = next(m for m in manifest if m["file_id"] == "confluence:page-q3")
    assert entry["match"] == "topic", (
        "Stage T stopped running after an abstention — the fallback must be "
        "the pre-Stage-R behaviour, not an empty prompt"
    )


def test_a_document_body_full_of_format_tokens_survives_verbatim(
    isolated_settings, catalog_candidates, confluence_bodies
):
    """A resolved document's body must reach the prompt UNINTERPRETED.

    Document bodies are user-authored — a Confluence page carrying a JSON
    snippet, a code block, or a literal `{question}`. Today they are safe
    because the block is assembled by concatenation and reaches the gateway as
    a VALUE (`user_cacheable_prefix=`), and `.format()` does not rescan the
    values it substitutes. This test exists so that stays true: any future
    refactor that starts formatting the assembled user string fails here
    rather than in production.

    Both failure directions are bad and neither is visible in review. A stray
    `{}` raises IndexError and kills the answer outright; a `{question}` would
    interpolate other prompt state into a block the model has been explicitly
    told to treat as fetched document content.

    Not hypothetical for this path in particular: the prompt's own rule 5
    documents `(source: {name}, uploaded {date})` as literal text, and real
    catalogs contain pages titled "Template - ..." — template documents are
    exactly the kind that carry placeholder braces.
    """
    from app.ask_runner import document_grounding

    hostile = (
        'CONFIG: {"id": 1, "state": "{queued}"} — see {question} and {0} and {} '
        'plus a bare } and a stray { for good measure. 100%% done.'
    )
    db = isolated_settings["supabase"]
    _seed_catalog_row(
        db, provider="confluence", external_id="page-tpl",
        title="Deploy Runbook Reference", source_name="Product wiki",
        summary="Deployment configuration and rollback steps.",
    )
    confluence_bodies["page-tpl"] = hostile
    catalog_candidates([])

    block, _ = document_grounding(_CID, "what's in the deploy runbook reference?")

    assert hostile in block, (
        "the document body was altered on its way into the prompt — something "
        "on this path is now interpreting user-authored text as a format string"
    )


def test_format_tokens_survive_all_the_way_into_the_gateway_call(
    isolated_settings, catalog_candidates, confluence_bodies, fake_llm
):
    """The same guard, at the layer where it would actually break.

    The test above stops at `document_grounding`, which only CONCATENATES —
    the hazard is one layer up, in `compose_ask_answer`, where the block is
    joined with the corpus/facts and handed over as `user_cacheable_prefix`.
    A refactor that started formatting that assembled string would leave the
    block-level test green and break production, so the assertion has to be
    on what the gateway actually receives.
    """
    from app.ask_runner import compose_ask_answer

    hostile = 'CFG {"a": "{b}"} {question} {0} {} stray { and } 100%% done'
    db = isolated_settings["supabase"]
    _seed_catalog_row(
        db, provider="confluence", external_id="page-tpl2",
        title="Deploy Runbook Reference", source_name="Product wiki",
        summary="Deployment configuration and rollback steps.",
    )
    confluence_bodies["page-tpl2"] = hostile
    catalog_candidates([])
    fake_llm["payload"] = {
        "answer": "x", "key_points": [], "citations": [], "confidence": 0.5,
        "unanswered": "",
    }

    compose_ask_answer(
        "asurion", "what's in the deploy runbook reference?", enterprise_id=_CID,
    )

    prefix = fake_llm["calls"][0]["kwargs"]["user_cacheable_prefix"]
    assert prefix is not None
    assert hostile in prefix, (
        "user-authored document text was interpreted on its way to the "
        "gateway — a brace in a wiki page now corrupts the prompt"
    )


def test_unresolved_heading_is_the_same_literal_everywhere():
    """The heading is a CROSS-PR CONTRACT, so pin every copy of it together.

    Three consumers bind to this string verbatim: `document_grounding` renders
    it, `ASK_SYSTEM_DOCUMENTS_ADDENDUM` rule 11 quotes it, and #1060's live
    sweep addendum carries a precedence clause naming it — the clause that
    stops a sweep with plausible-looking material from answering instead of
    asking which document was meant.

    If someone rewords one copy, the others silently stop binding and the only
    symptom is a confidently wrong answer in production. This fails first."""
    from app.ask_runner import UNRESOLVED_REFERENCE_HEADING
    from app.prompts import ASK_SYSTEM_DOCUMENTS_ADDENDUM

    assert UNRESOLVED_REFERENCE_HEADING == (
        "The document this message refers to is UNRESOLVED"
    )
    assert UNRESOLVED_REFERENCE_HEADING in ASK_SYSTEM_DOCUMENTS_ADDENDUM, (
        "prompt rule 11 no longer quotes the heading it is written about"
    )


def test_abstention_block_renders_the_contracted_heading(
    isolated_settings, catalog_candidates, confluence_bodies
):
    """…and that the rendered block actually uses it, so the constant cannot
    drift away from what reaches the model."""
    from app.ask_runner import UNRESOLVED_REFERENCE_HEADING, document_grounding

    db = isolated_settings["supabase"]
    for external_id, title in (
        ("page-q3", "Q3 Pricing Teardown"), ("page-q4", "Q4 Pricing Teardown")
    ):
        _seed_catalog_row(
            db, provider="confluence", external_id=external_id, title=title,
            source_name="Product wiki", summary="Pricing analysis.",
        )
    catalog_candidates([])

    block, _ = document_grounding(
        _CID, "what does the pricing teardown say about discounts?"
    )

    assert f"## {UNRESOLVED_REFERENCE_HEADING}" in block


def test_an_abstention_and_a_live_sweep_compose_into_ONE_coherent_prompt(
    isolated_settings, catalog_candidates, confluence_bodies, fake_llm
):
    """The cross-PR interaction, exercised in a single build for the first time.

    #1060's connector sweep and this PR's abstention were written apart and
    pull in opposite directions. The sweep's addendum tells the model to answer
    from live cross-source material and attribute it; the UNRESOLVED section
    tells it to ask which document was meant. If the sweep happens to surface
    something plausible for an ambiguous document reference, a model that
    satisfies the sweep and skips the abstention produces the worst available
    answer: confident, about the wrong document, and assembled from genuinely
    real data — which is what makes it hard for anyone to catch.

    #1060 anticipated this and shipped a PRECEDENCE clause naming this PR's
    heading verbatim, phrased conditionally so it was inert while this branch
    was unmerged. It goes live the moment this lands, and until now the two
    have never been in one prompt. This asserts the wiring actually holds:
    both sections present, the precedence clause present, and the heading it
    names identical to the one that was rendered.

    What a model DOES with the combined prompt is live-verification's
    question. What is mechanically checkable — and is the part that would
    silently rot — is that the clause and the section it defers to are both
    there and still agree on the string.
    """
    from app.ask_runner import UNRESOLVED_REFERENCE_HEADING, compose_ask_answer
    from app.prompts import ASK_SYSTEM_DOCUMENTS_ADDENDUM

    # #1060-only surface. Skipped rather than imported at module scope so THIS
    # file stays green if #1060 is ever reverted — the runtime does not depend
    # on it, and a test file that goes red on main over another PR's revert is
    # a coupling nobody asked for.
    sweep_addendum = getattr(
        __import__("app.prompts", fromlist=["x"]),
        "ASK_SYSTEM_LIVE_SWEEP_ADDENDUM", None,
    )
    if sweep_addendum is None or "live_context" not in inspect.signature(
        compose_ask_answer
    ).parameters:
        pytest.skip("#1060 (cross-connector sweep) is not present")
    ASK_SYSTEM_LIVE_SWEEP_ADDENDUM = sweep_addendum

    db = isolated_settings["supabase"]
    for external_id, title in (
        ("page-q3", "Q3 Pricing Teardown"), ("page-q4", "Q4 Pricing Teardown")
    ):
        _seed_catalog_row(
            db, provider="confluence", external_id=external_id, title=title,
            source_name="Product wiki", summary="Pricing analysis.",
        )
        confluence_bodies[external_id] = f"BODY OF {title}"
    catalog_candidates([])
    fake_llm["payload"] = {
        "answer": "x", "key_points": [], "citations": [], "confidence": 0.5,
        "unanswered": "",
    }

    compose_ask_answer(
        "asurion",
        "what does the pricing teardown say about discounts?",
        enterprise_id=_CID,
        live_context="LIVE SWEEP: a Slack thread about discounting.",
    )

    call = fake_llm["calls"][0]
    system = call["kwargs"].get("system") or call["system"]
    prefix = call["kwargs"]["user_cacheable_prefix"] or ""

    # The abstention reached the prompt...
    assert f"## {UNRESOLVED_REFERENCE_HEADING}" in prefix
    # ...both addenda are in the system prompt together...
    assert ASK_SYSTEM_LIVE_SWEEP_ADDENDUM in system, "the sweep addendum is absent"
    assert ASK_SYSTEM_DOCUMENTS_ADDENDUM in system, "the documents addendum is absent"
    # ...and the precedence clause names the heading that was actually rendered.
    assert "PRECEDENCE" in system
    assert UNRESOLVED_REFERENCE_HEADING in ASK_SYSTEM_LIVE_SWEEP_ADDENDUM, (
        "the sweep's precedence clause no longer quotes the heading this "
        "module renders — the sweep would stop deferring to the abstention"
    )
    # And no document was pinned, so nothing invites answering from one.
    assert "[THIS is the document the user's message refers to]" not in prefix


def test_manifest_labels_a_named_resolution_named(
    isolated_settings, catalog_candidates, confluence_bodies
):
    """Stage R reports the EVIDENCE, not itself. A document the message names
    stays "named" whether Stage N's substring rule or Stage R's ranking found
    it — the manifest contract is about how we knew, and that did not change."""
    from app.ask_runner import document_grounding

    db = isolated_settings["supabase"]
    _seed_catalog_row(
        db, provider="confluence", external_id="page-q3",
        title="Q3 Pricing Teardown", source_name="Product wiki",
        summary="Pricing analysis.",
    )
    confluence_bodies["page-q3"] = "BODY"
    catalog_candidates([])

    _, manifest = document_grounding(_CID, "summarize the Q3 pricing teardown")

    entry = next(m for m in manifest if m["file_id"] == "confluence:page-q3")
    assert entry["match"] == "named"
    assert entry["loaded"] is True


def test_manifest_labels_a_thread_resolution_anaphoric(
    isolated_settings, catalog_candidates, confluence_bodies
):
    """The one genuinely new label. Resolving against the THREAD is a different
    claim from resolving against the message, and an auditor reviewing a wrong
    answer needs to be able to find every document chosen that way."""
    from app.ask_runner import document_grounding

    db = isolated_settings["supabase"]
    _seed_catalog_row(
        db, provider="confluence", external_id="page-q3",
        title="Q3 Pricing Teardown", source_name="Product wiki",
        summary="Pricing analysis.",
    )
    confluence_bodies["page-q3"] = "BODY"
    catalog_candidates([])

    _, manifest = document_grounding(
        _CID, "what does it say about discounts?",
        history=[
            _turn("user", "summarize the Q3 pricing teardown"),
            _turn("assistant", "Here you go."),
        ],
    )

    entry = next(m for m in manifest if m["file_id"] == "confluence:page-q3")
    assert entry["match"] == "anaphoric"
    assert entry["loaded"] is True
