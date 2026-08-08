"""Call index: listing intent, account derivation, and the cheap answer path.

The index exists because the full-corpus digest costs ~168s and ~$0.23 per
question (measured on Northwind, 22 calls) while ~98% of that is the model
reading transcripts. These tests pin the two properties that make the saving
real and safe:

  * a LISTING question is answered from Postgres, never from the digest, and
  * a SYNTHESIS question is left alone, so the index can never silently
    downgrade "summarize last week" into a list of titles.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

import app.call_index as ci


def _fresh(**kw) -> ci.Freshness:
    """A synced, current index — the precondition every answer path requires.

    Passed explicitly so these tests exercise the ANSWER logic rather than the
    freshness gate, which has its own tests at the bottom of this file. Omitting
    it is not a shortcut: the answer paths would then call `ensure_fresh` for
    real and reach the database.
    """
    return ci.Freshness(
        connected=True, as_of=datetime.now(timezone.utc), **kw
    )


# ── listing vs synthesis intent ──────────────────────────────────────────────

def test_listing_requests_are_recognised():
    """These all want the LIST. The first is the phrasing that fell through to
    the KG in production and answered 'no transcripts available' while the
    index held exactly what was asked for."""
    for question in (
        "Give me the 5 latest transcripts for customer conversations",
        "list the calls from last week",
        "which meetings did we have with customers",
        "show me recent customer calls",
        "how many calls did we have last week",
        "who did we talk to this week",
    ):
        assert ci.is_listing_request(question), question


def test_synthesis_requests_are_left_to_the_digest():
    """A summarize/recap/theme verb means the caller wants the ANALYSIS. The
    index must not intercept these — answering with a list of titles would be a
    silent downgrade of the answer, which is worse than being slow."""
    for question in (
        "summarize the customer calls from last week",
        "recap this week's meetings",
        "what did we hear on our sales calls",
        "give me the themes from last week's calls",
        "what were the takeaways from the customer conversations",
        "summarize the last 5 calls",           # a count does not make it a listing
    ):
        assert not ci.is_listing_request(question), question


def test_unrelated_questions_are_not_listings():
    for question in (
        "what should we build next quarter",
        "write a PRD for billing",
        "how is our churn trending",
    ):
        assert not ci.is_listing_request(question), question


# ── account derivation ───────────────────────────────────────────────────────

def test_account_derives_from_the_external_domain():
    account = ci.derive_account(
        ["host@northwind.example", "buyer@initech.example", "rep@initech.example"],
        own_domains={"northwind.example"},
    )
    assert account == "Initech"


def test_account_is_none_for_an_internal_call():
    """An internal standup or interview genuinely has no customer account. A
    wrong label is worse than a blank one when the model reads this as
    evidence, so guessing is not acceptable here."""
    assert ci.derive_account(
        ["host@northwind.example", "eng@northwind.example"],
        own_domains={"northwind.example"},
    ) is None


def test_generic_mail_domains_never_become_an_account():
    assert ci.derive_account(
        ["host@northwind.example", "someone@gmail.com"],
        own_domains={"northwind.example"},
    ) is None


def test_most_common_external_domain_wins():
    """A call with several customer attendees and one vendor rep is labelled by
    the customer, not by whoever happens to be listed first."""
    assert ci.derive_account(
        ["a@aperture.example", "b@aperture.example", "solo@othercorp.com"],
        own_domains=set(),
    ) == "Aperture"


# ── the cheap answer path ────────────────────────────────────────────────────

def _call(idx: int, account: str | None = "Initech") -> ci.IndexedCall:
    return ci.IndexedCall(
        external_id=f"id-{idx}",
        title=f"Call {idx}",
        call_date=f"2026-07-2{idx}T10:00:00+00:00",
        duration_min=30.0,
        participants=["a@initech.example"],
        account=account,
        summary="",
    )


def test_answer_listing_reads_the_index_and_makes_no_model_call(monkeypatch):
    monkeypatch.setattr(ci, "list_calls", lambda *a, **k: [_call(i) for i in range(1, 4)])
    out = ci.answer_listing("ent-A", "list the calls from last week", fresh=_fresh())

    assert out is not None
    assert out["_skill_source"] == "call-index"
    assert "3 calls" in out["answer"]
    assert "Initech" in out["answer"]


def test_answer_listing_honours_an_explicit_count(monkeypatch):
    """'the 5 latest' must return 5, not the whole window — the answer should
    match the question that was asked."""
    monkeypatch.setattr(ci, "list_calls", lambda *a, **k: [_call(i) for i in range(1, 10)])
    out = ci.answer_listing("ent-A", "give me the 5 latest transcripts", fresh=_fresh())

    assert out["answer"].count("\n- ") == 5


# ── a listing must answer for the window it was asked about ──────────────────
#
# Observed in Chrome on staging, company with 485 indexed calls:
#
#   "list the calls from last week"      -> "50 calls"  spanning 07-16…07-31
#   "how many calls did we have last week" -> "50 calls"  same two-week span
#   "who did we talk to this week"       -> "50 calls"  same two-week span
#   "Give me the 5 latest transcripts"   -> "5 calls"    CORRECT
#
# `qa_agent` never passed `window=`, so `since`/`until` were always None and
# `list_calls` returned its default 50 newest rows whatever period was asked
# for. The only thing that ever narrowed a listing was `_COUNT_RULE`, which is
# why the "5 latest" case looked right and masked every other one.
#
# The stated number is the dangerous part: "50 calls" is not a slow answer or a
# vague one, it is a WRONG FACT delivered with no hedge.

def _capturing_list_calls(returns):
    """A `list_calls` stand-in that records the window it was scoped with."""
    seen: dict = {}

    def _list(company_id, *, since=None, until=None, limit=50):
        seen["since"], seen["until"], seen["limit"] = since, until, limit
        return returns

    return _list, seen


def test_a_windowed_listing_is_scoped_to_that_window(monkeypatch):
    """"last week" must reach `list_calls` as a real since/until pair."""
    _list, seen = _capturing_list_calls([_call(i) for i in range(1, 4)])
    monkeypatch.setattr(ci, "list_calls", _list)

    out = ci.answer_listing("ent-A", "list the calls from last week", fresh=_fresh())

    assert seen["since"] is not None and seen["until"] is not None
    assert (seen["until"] - seen["since"]).days == 7      # one week, not two
    assert "last week" in out["answer"]                    # and it says so


def test_a_windowed_count_states_the_window_total_not_the_row_cap(monkeypatch):
    """The wrong-number case. Three calls in the window must answer "3", never
    the number of rows the cap happened to return."""
    monkeypatch.setattr(
        ci, "list_calls", lambda *a, **k: [_call(i) for i in range(1, 4)]
    )
    monkeypatch.setattr(
        ci, "count_calls",
        lambda *a, **k: pytest.fail("a window under the cap needs no count query"),
    )
    out = ci.answer_listing(
        "ent-A", "how many calls did we have last week", fresh=_fresh()
    )
    assert "3 calls" in out["answer"]
    assert "50" not in out["answer"]


def test_an_unwindowed_listing_is_not_scoped(monkeypatch):
    """The 7-day fallback is a DEFAULT, not a request. Applying it to "list the
    calls" would hide everything older than a week from a question that named no
    cutoff — `.explicit` is the same flag `windowed_call_question` honours."""
    _list, seen = _capturing_list_calls([_call(1)])
    monkeypatch.setattr(ci, "list_calls", _list)

    ci.answer_listing("ent-A", "list the calls", fresh=_fresh())

    assert seen["since"] is None and seen["until"] is None


def test_an_overflowing_window_states_the_true_total_and_says_it_truncated(monkeypatch):
    """A window may hold more than the render cap. Then the count and the list
    must disagree HONESTLY — state the real total, show the newest N, say so."""
    over = [_call(1) for _ in range(ci._LISTING_RENDER_CAP + 1)]
    monkeypatch.setattr(ci, "list_calls", lambda *a, **k: over)
    monkeypatch.setattr(ci, "count_calls", lambda *a, **k: 485)

    out = ci.answer_listing("ent-A", "list the calls from last week", fresh=_fresh())

    assert "485 calls" in out["answer"]
    assert f"Showing the {ci._LISTING_RENDER_CAP} most recent" in out["answer"]
    assert out["answer"].count("\n- ") == ci._LISTING_RENDER_CAP


def test_an_uncountable_overflow_does_not_invent_a_total(monkeypatch):
    """If the count cannot be read we say "more than N" — never a number we
    did not get."""
    over = [_call(1) for _ in range(ci._LISTING_RENDER_CAP + 1)]
    monkeypatch.setattr(ci, "list_calls", lambda *a, **k: over)
    monkeypatch.setattr(ci, "count_calls", lambda *a, **k: None)

    out = ci.answer_listing("ent-A", "list the calls from last week", fresh=_fresh())

    assert f"More than {ci._LISTING_RENDER_CAP} calls" in out["answer"]


def test_answer_listing_states_no_calls_when_a_sync_proved_there_are_none(monkeypatch):
    """An empty index BEHIND a successful sync is a fact, not a gap.

    Before the freshness layer this returned None and fell through — which was
    right at the time, because an empty table could equally have meant "we never
    synced". With `Freshness.usable` proving a sync completed, falling through
    would instead spend ~168s rediscovering that there is nothing there.
    """
    monkeypatch.setattr(ci, "list_calls", lambda *a, **k: [])
    out = ci.answer_listing("ent-A", "list the calls", fresh=_fresh())
    assert out is not None
    assert "No calls" in out["answer"]
    assert out["_skill_source"] == "call-index"


def test_rendered_line_omits_a_missing_account():
    line = _call(1, account=None).render()
    assert "None" not in line
    assert "Call 1" in line


# ── own-domain detection ─────────────────────────────────────────────────────

def test_ubiquitous_domain_is_treated_as_our_own(monkeypatch):
    """The host attends every call; a customer does not. Without this, a
    workspace with no membership rows labels every call with the VENDOR's
    domain — observed on the first Northwind sync, where all 485 calls came
    back as account="Northwind"."""
    monkeypatch.setattr(
        "app.db.drip.list_members_with_email", lambda cid: [], raising=False
    )
    calls = [
        {"participants": ["host@northwind.example", "buyer@initech.example"]},
        {"participants": ["host@northwind.example", "cto@aperture.example"]},
        {"participants": ["host@northwind.example", "pm@soylent.example"]},
        {"participants": ["host@northwind.example"]},
    ]
    own = ci._own_domains("ent-A", calls)

    assert "northwind.example" in own            # on 4/4 calls -> ours
    assert "initech.example" not in own          # on 1/4 -> a customer
    # ...and the derived account is therefore the customer, not us.
    assert ci.derive_account(calls[0]["participants"], own) == "Initech"


def test_ubiquity_needs_more_than_one_call(monkeypatch):
    """A single call must not brand its lone attendee domain as 'ours' — every
    domain is trivially on 100% of a one-call corpus."""
    monkeypatch.setattr(
        "app.db.drip.list_members_with_email", lambda cid: [], raising=False
    )
    own = ci._own_domains("ent-A", [{"participants": ["someone@acme.com"]}])
    assert "acme.com" not in own


# ── single named call ────────────────────────────────────────────────────────

def test_single_call_intent_recognised():
    for question in (
        "Summarize Vandelayindustries",
        "summarize the Vandelay Industries call",
        "tell me about the Initech conversation",
        "what did we discuss with NCC",
        "walk me through the NEFCO check-in",
    ):
        assert ci.is_single_call_request(question), question


def test_window_questions_are_not_single_call():
    """A window word means the digest, not one call — otherwise the cheap path
    would hijack 'summarize last week' and answer from a single arbitrary
    transcript."""
    for question in (
        "summarize the customer calls from last week",
        "recap this week's meetings",
        "summarize all the calls",
    ):
        assert not ci.is_single_call_request(question), question


def test_listing_questions_are_not_single_call():
    assert not ci.is_single_call_request("give me the 5 latest transcripts")


# ── asking for the transcript IS asking for the content ──────────────────────
#
# Found on staging 2026-08-16, on one Zoom meeting, both ways:
#
#   "find me the transcript of David Mumuni's Zoom meeting"
#       -> listing leg. Five meetings with times and attendees, and the claim
#          that their "transcripts could not be loaded for this question".
#   "summarize David Mumuni's Zoom meeting from Aug 5 at 14:45"
#       -> the transcript, read and answered in full.
#
# Same call, same data, different verb. Worse than a routing miss: the listing
# leg tells the model the index holds titles and dates and NOT transcripts, so
# the answer stated a limitation the product does not have.

def test_asking_for_a_named_transcript_is_a_single_call_request():
    for question in (
        "find me the transcript of David Mumuni's Zoom meeting",
        "get me the Genworth transcript",
        # The bare noun phrase, which is how people actually ask.
        "transcript of the Mayer Brown call",
        "what was said on the BBVA call",
        "read me the NEFCO check-in",
    ):
        assert ci.is_single_call_request(question), question


def test_a_plural_transcript_ask_that_NAMES_an_account_is_still_not_one_call():
    """Caught by review. "send me the last 3 transcripts from Acme" has no
    listing verb, matches the bare `transcripts` noun, survives the window gate
    ("last 3" is not "last week") and NAMES an account — so it would have been
    answered from exactly one call. A plural noun means a set, and a set belongs
    to the listing or the digest."""
    for question in (
        "send me the last 3 transcripts from Acme",
        "transcripts for the Genworth account",
    ):
        assert not ci.is_single_call_request(question), question


def test_intent_stopwords_do_not_strip_words_that_are_part_of_a_NAME():
    """Also caught by review, and the more dangerous of the two. The first cut
    put these words in `_ASK_WORDS`, which `resolve_calls` shares — so "the Open
    AI call" resolved to NO terms and the answer said "none of their titles or
    accounts match this" about a call sitting in the index under that exact
    name. Intent stripping is local to the intent question."""
    assert ci._query_terms("summarize the Open AI call") == ["Open"]
    assert "Read" in ci._query_terms("what did Read AI say")


def test_the_single_call_prompt_answers_the_question_that_was_asked():
    """Found live, after the routing fix landed: "find me the transcript of
    David Mumuni's Zoom meeting" reached the right call and fetched the right
    transcript — then answered with a paragraph about the transcript carrying no
    timestamp. Fetching the right call and then declining to show it is, from
    the user's side, the same failure as not finding it.

    Asserted on the PROMPT because that is where the behaviour lives: the model
    is handed the transcript either way, and what it does with it is what this
    string decides."""
    system = ci._SINGLE_CALL_SYSTEM
    assert "ANSWER THE QUESTION THAT WAS ASKED" in system
    # The transcript branch, and the explicit refusal to substitute a summary.
    assert "reproduce the conversation itself" in system
    assert "Do not replace it with a summary" in system
    # Summarizing stays a first-class branch, not a casualty of the fix.
    assert "what a PM would act on" in system
    # And the caveat that consumed the live answer is bounded.
    assert "never let it displace the answer" in system


def test_transcript_asks_still_obey_every_other_gate():
    """The new nouns buy no exemption. A window still means the digest, and a
    plural ask that names no call still belongs to the listing — otherwise this
    fix would trade a missed transcript for the far worse failure the
    single-call guard exists to prevent: answering about ONE arbitrary call as
    though it were the set that was asked about."""
    for question in (
        "give me all the transcripts from last week",
        "find me the transcripts",
        "which calls have transcripts",
    ):
        assert not ci.is_single_call_request(question), question


# ── the single-call path must not claim a general ask ────────────────────────
#
# Reproduced live on staging (485 indexed calls): "can you summarize our recent
# customer calls" — plural, general, naming no call — was claimed by the
# single-call path, resolved to exactly ONE call, and answered about an internal
# SE-candidate interview. The model happened to notice the mismatch and said so;
# that is luck, not a safety net, and the next wrong pick may be plausible enough
# to pass for an answer.
#
# Two independent defects, so two independent guards and two sets of tests:
#   1. INTENT — a summary verb alone claimed the question. It must also NAME
#      something (an account, a distinctive title term, or a date).
#   2. RESOLUTION — "can" matched mid-word inside "Candidate". A short term may
#      no longer match as a substring.

def test_the_staging_overreach_is_not_a_single_call_request():
    """The exact reported question. Plural and general: it names no call, so the
    single-call path must stand down and leave it to the digest."""
    assert not ci.is_single_call_request("can you summarize our recent customer calls")


def test_general_plural_asks_are_never_claimed():
    """These all describe a SET of calls with generic qualifiers only. Every one
    of them belongs to the listing or digest path, which answer over the whole
    window rather than picking one member of it."""
    for question in (
        "can you summarize our recent customer calls",
        "summarize our recent customer calls",
        "summarize the last few calls",
        "recap the last few calls",
        "summarize all the calls",
        "tell me about the customer calls",
        "what happened on our calls",
        "summarize recent conversations",
        "recap our discovery calls",
        "summarize the sales calls",
        "summarize the most recent call",
        "give me a summary of our meetings",
        "walk me through this week's customer conversations",
    ):
        assert not ci.is_single_call_request(question), question


def test_a_date_names_a_call():
    """A date is a NAMED reference just as an account is — it identifies which
    call without needing its title."""
    assert ci.is_single_call_request("summarize the call on 2026-07-29")


def _indexed(account, title):
    return ci.IndexedCall(
        external_id=f"id-{account}", title=title,
        call_date="2026-07-31T10:00:00+00:00", duration_min=41.0,
        participants=[], account=account, summary="",
    )


def test_resolution_matches_a_squashed_account_name(monkeypatch):
    """The index stores 'Vandelayindustries' while the title says 'Vandelay Industries'. A user
    types either. Normalizing to alphanumerics makes all three compare equal."""
    calls = [
        _indexed("Vandelayindustries", "Vandelay Industries + Northwind Briefing"),
        _indexed("Globex", "Northwind | Globex Q+A"),
        _indexed("Ncc", "Rosa Delgado and Tom Mercer"),
    ]
    monkeypatch.setattr(ci, "list_calls", lambda *a, **k: calls)

    for question in ("Summarize Vandelayindustries", "summarize the Vandelay Industries call"):
        best = ci.resolve_calls("ent-A", question)
        assert best and best[0].account == "Vandelayindustries", question


def test_a_general_ask_resolves_to_nothing_rather_than_to_one_call(monkeypatch):
    """The resolution half of the staging bug, in one assertion.

    "can" survived the ask-word strip and matched INSIDE "Candidate", so a
    question naming no call scored an internal hiring interview as a named match
    and summarized it. Nothing here may match — an empty list is what makes the
    caller fall through to the digest.
    """
    calls = [
        _indexed(None, "SE Candidate Interview"),
        _indexed("Vandelayindustries", "Vandelay Industries + Northwind Briefing"),
        _indexed("Initech", "Initech Discovery"),
    ]
    monkeypatch.setattr(ci, "list_calls", lambda *a, **k: calls)
    assert ci.resolve_calls("ent-A", "can you summarize our recent customer calls") == []


def test_a_general_ask_produces_no_single_call_answer(monkeypatch):
    """The live symptom, end to end: an answer WAS produced, headed with an
    internal hiring interview. No transcript may be fetched and no answer
    returned — the caller falls through to the digest, which answers over the
    whole window."""
    calls = [
        _indexed(None, "SE Candidate Interview"),
        _indexed("Vandelayindustries", "Vandelay Industries + Northwind Briefing"),
    ]
    monkeypatch.setattr(ci, "list_calls", lambda *a, **k: calls)
    monkeypatch.setattr(
        ci, "fetch_transcript",
        lambda *a, **k: pytest.fail("a general ask must not fetch a transcript"),
    )
    assert ci.answer_single_call(
        "ent-A", "can you summarize our recent customer calls", fresh=_fresh()
    ) is None


def test_a_short_term_never_matches_mid_word(monkeypatch):
    """A whole-word hit is trusted at any length ("NCC"); a SUBSTRING hit needs
    a distinctive term. Without the floor, any 3-letter fragment of a real word
    silently resolves a call."""
    calls = [_indexed(None, "SE Candidate Interview")]
    monkeypatch.setattr(ci, "list_calls", lambda *a, **k: calls)
    # "can" is inside "Candidate" — must NOT resolve.
    assert ci.resolve_calls("ent-A", "summarize the can call") == []
    # ...while a short term that IS a whole word still resolves.
    ncc = [_indexed("Ncc", "Rosa Delgado and Tom Mercer")]
    monkeypatch.setattr(ci, "list_calls", lambda *a, **k: ncc)
    assert ci.resolve_calls("ent-A", "what did we discuss with NCC") == ncc


def test_resolution_selects_by_date(monkeypatch):
    """A date the user typed picks that day's call, the same reference form
    `select_from_candidates` already accepts in a disambiguation reply."""
    older = ci.IndexedCall(
        "id-old", "Initech Discovery", "2026-07-22T10:00:00+00:00", 30.0, [],
        "Initech", "",
    )
    calls = [_indexed("Vandelayindustries", "Vandelay Industries + Northwind Briefing"), older]
    monkeypatch.setattr(ci, "list_calls", lambda *a, **k: calls)
    got = ci.resolve_calls("ent-A", "summarize the call on 2026-07-22")
    assert [c.external_id for c in got] == ["id-old"]


def test_resolution_returns_nothing_when_no_call_matches(monkeypatch):
    """No match must yield [] so the caller falls through — summarizing an
    arbitrary call would be worse than not answering."""
    monkeypatch.setattr(ci, "list_calls", lambda *a, **k: [_indexed("Globex", "Globex Q+A")])
    assert ci.resolve_calls("ent-A", "summarize the Initech call") == []


def test_single_call_falls_through_when_transcript_is_empty(monkeypatch):
    """A resolved call with no transcript must fall through rather than
    summarizing nothing."""
    monkeypatch.setattr(
        ci, "list_calls", lambda *a, **k: [_indexed("Globex", "Globex Q+A")]
    )
    monkeypatch.setattr(ci, "fetch_transcript", lambda *a, **k: {"sentences": []})
    assert ci.answer_single_call(
        "ent-A", "summarize the Globex call", fresh=_fresh()
    ) is None


def test_single_call_summarizes_one_transcript(monkeypatch):
    """The load-bearing assertion: exactly ONE transcript is fetched, not the
    whole window."""
    fetched = []
    monkeypatch.setattr(
        ci, "list_calls", lambda *a, **k: [_indexed("Globex", "Northwind | Globex Q+A")]
    )
    monkeypatch.setattr(
        ci, "fetch_transcript",
        lambda cid, ext: fetched.append(ext) or {
            "title": "Northwind | Globex Q+A",
            "summary": {"overview": "ov"},
            "sentences": [{"speaker_name": "Buyer", "text": "we need SSO"}],
        },
    )
    import app.graph.gateway as gw
    monkeypatch.setattr(
        gw, "llm_call",
        lambda **k: type("R", (), {"output": "They asked for SSO."})(),
    )

    out = ci.answer_single_call("ent-A", "summarize the Globex call", fresh=_fresh())

    assert fetched == ["id-Globex"]          # exactly one transcript
    assert out["_skill_source"] == "call-index-single"
    assert "Globex" in out["answer"] and "SSO" in out["answer"]


def test_a_real_length_call_renders_complete():
    """The corpus's longest call is 880 sentences / ~58k chars. Nothing at that
    scale may be truncated — the previous 600-SENTENCE cap silently cut 3 of 10
    recent calls, including the Vandelay Industries call (609) whose summary was
    therefore produced without its closing 9 sentences."""
    raw = {
        "title": "Long real call",
        "sentences": [{"speaker_name": "A", "text": "x" * 60} for _ in range(880)],
    }
    rendered = ci.render_transcript(raw)
    assert "omitted" not in rendered
    assert rendered.count("\n  ") == 880


def test_truncation_keeps_the_close_and_announces_itself():
    """If the budget genuinely is exceeded, the END must survive — that is where
    next steps and commitments are — and the text must SAY it is partial so the
    model can disclose rather than implying completeness."""
    sentences = (
        [{"speaker_name": "A", "text": f"open {i}"} for i in range(100)]
        + [{"speaker_name": "B", "text": "m" * 400} for i in range(500)]
        + [{"speaker_name": "C", "text": f"close {i}"} for i in range(100)]
    )
    rendered = ci.render_transcript({"title": "t", "sentences": sentences},
                                    max_chars=20_000)

    assert "open 0" in rendered            # opening kept
    assert "close 99" in rendered          # CLOSING kept — the old cap lost this
    assert "omitted" in rendered           # elision is visible to the model
    assert "PARTIAL" in rendered           # and stated explicitly
    assert "Do not claim completeness" in rendered


# ── replying to a disambiguation ─────────────────────────────────────────────
#
# Reported from a live thread: "give me a summary of the call with blue cross"
# correctly asked which of two Umbrella calls, the user answered "both", and the
# answer fell through to the KG reporting the transcripts were unavailable —
# while they were one fetch away. The disambiguation was posing a question it
# could not read the answer to.

def _two_umbrella():
    return [
        ci.IndexedCall("id-a", "Umbrella Health Group + Northwind Sync",
                       "2026-07-28T10:00:00+00:00", 47.0, [], "Umbrella", ""),
        ci.IndexedCall("id-b", "Umbrella Health Group + Briefing",
                       "2026-07-22T10:00:00+00:00", 61.0, [], "Umbrella", ""),
    ]


def _disambiguated_history(original="give me a summary of the call with blue cross"):
    return [
        {"role": "user", "content": original},
        {"role": "assistant",
         "content": f"I found 2 {ci._DISAMBIGUATION_MARKER}\n\n- a\n- b"},
    ]


def test_bare_reply_to_a_disambiguation_is_recognised():
    """'both' names no call and carries no summary verb — only the pending
    question in history makes it meaningful."""
    history = _disambiguated_history()
    for reply in ("both", "the first one", "2026-07-28", "1"):
        assert ci.is_single_call_request(reply, history), reply
    # ...and without that history it is correctly NOT a call request.
    assert not ci.is_single_call_request("both", None)


def test_both_selects_every_candidate():
    assert len(ci.select_from_candidates("both", _two_umbrella())) == 2


def test_ordinal_and_date_select_one():
    calls = _two_umbrella()
    assert ci.select_from_candidates("the first one", calls) == [calls[0]]
    assert ci.select_from_candidates("2026-07-22", calls) == [calls[1]]


def test_long_messages_do_not_select_by_ordinal():
    """'what did the first customer say about pricing' contains 'first' but is a
    new question, not a selection. Only a short reply may select by ordinal."""
    calls = _two_umbrella()
    assert ci.select_from_candidates("first", calls) == [calls[0]]
    picked = ci.select_from_candidates(
        "what did the first customer say about pricing", calls
    )
    assert picked != [calls[0]]


def test_the_word_one_does_not_mean_first():
    """"the Initech one" means an ITEM, not the first item. Mapping "one" to
    index 0 made an unmatched narrowing term silently return the wrong call."""
    calls = _two_umbrella()
    assert ci.select_from_candidates("the Initech one", calls) == []
    assert ci.select_from_candidates("the sync one", calls) == [calls[0]]


def test_both_fetches_every_selected_transcript(monkeypatch):
    """The bug in one assertion: replying 'both' must fetch BOTH transcripts and
    summarize them, not fall through."""
    fetched = []
    monkeypatch.setattr(ci, "resolve_calls", lambda cid, q, **k: _two_umbrella())
    monkeypatch.setattr(
        ci, "fetch_transcript",
        lambda cid, ext: fetched.append(ext) or {
            "title": ext, "summary": {"overview": ""},
            "sentences": [{"speaker_name": "Buyer", "text": "pricing please"}],
        },
    )
    import app.graph.gateway as gw
    monkeypatch.setattr(gw, "llm_call",
                        lambda **k: type("R", (), {"output": "Both summarized."})())

    out = ci.answer_single_call(
        "ent-A", "both", history=_disambiguated_history(), fresh=_fresh()
    )

    assert sorted(fetched) == ["id-a", "id-b"]
    assert out["_skill_source"] == "call-index-multi"
    assert "2 calls" in out["answer"]


def test_selection_that_matches_nothing_falls_through(monkeypatch):
    monkeypatch.setattr(ci, "resolve_calls", lambda cid, q, **k: _two_umbrella())
    out = ci.select_from_candidates("the Initech one", _two_umbrella())
    assert out == []


# ── index-driven window routing ──────────────────────────────────────────────

def test_not_calls_matches_plurals():
    """Every noun needs an optional plural. Without it "tickets" slipped past
    "ticket\\b" and a ticket question routed to the calls."""
    for question in (
        "what tickets did we close last week",
        "which issues shipped last week",
        "what PRs merged last week",
        "analyze the CSVs from last week",
        "what deploys went out last week",
    ):
        assert ci._NOT_CALLS.search(question), question


def test_release_questions_are_not_call_questions():
    """Reproduced on staging: "did the prototype ship last week?" took 188s and
    returned a multi-section voice-of-customer report — a yes/no question about
    a ship date, answered from the week's customer calls.

    It named a window, the company had calls in it, and `_NOT_CALLS` vetoed
    nothing: the list carried the release NOUNS ("releases", "deploys") but not
    "ship", not "prototype", and not the verb forms of the nouns it did carry.
    """
    for question in (
        "did the prototype ship last week?",
        "did we ship the prototype last week",
        "what shipped last week",
        "is the prototype shipping this week",
        "was it released last week",
        "did anything get deployed last week",
    ):
        assert ci._NOT_CALLS.search(question), question


def test_product_vocabulary_customers_use_is_not_vetoed():
    """The membership rule is "another source OWNS the answer", not "sounds like
    engineering". `launch` and `rollout` were tried in `_NOT_CALLS` and removed:
    customers say "when does this launch" and "how did the rollout go" on calls,
    so the calls are the RIGHT source and vetoing them sends the question to the
    KG's distilled summaries while we hold the transcripts."""
    for question in (
        "what did customers say about the launch last week",
        "how did customers react to the rollout last week",
        "did we launch last week",
        "how did the rollout go last week",
    ):
        assert not ci._NOT_CALLS.search(question), question


def test_windowed_routing_stands_down_for_a_release_question(monkeypatch):
    """The veto fires BEFORE any freshness or DB work, so a question that was
    never about calls does not even pay for a sync — the ordering `_NOT_CALLS`
    is placed first to guarantee."""
    monkeypatch.setattr(
        ci, "ensure_fresh",
        lambda *a, **k: pytest.fail("a release question must not trigger a sync"),
    )
    monkeypatch.setattr(
        ci, "list_calls",
        lambda *a, **k: pytest.fail("a release question must not read the index"),
    )
    assert ci.windowed_call_question("ent-A", "did the prototype ship last week?") is None


def test_call_phrasings_are_not_excluded():
    """The control set. Widening `_NOT_CALLS` is only safe while these still
    reach the calls — a veto list that swallows genuine customer-voice questions
    has traded one silent misroute for another."""
    for question in (
        "give me top 3 product requests from last week",
        "what did customers want last week",
        "biggest complaints last week",
        "what did customers say about pricing last week",
        "top feature requests from last week",
        "what frustrated customers most last week",
    ):
        assert not ci._NOT_CALLS.search(question), question


# ── freshness: the three ways a populated-looking index lies ─────────────────
#
# The index is only trustworthy if a reader can tell these apart, and the rows
# alone cannot tell any of them from any other:
#
#   (a) this company has no calls          -> zero rows
#   (b) we have never synced this company  -> zero rows      <- same as (a)
#   (c) synced 6h ago, a call has happened -> rows, newest one missing
#
# (b) read as (a) is silent: every interception returns None and the question
# degrades to the old expensive path with nothing reporting a problem. (c) is
# worse — `answer_listing` states a COUNT, so a stale index answers WRONG.


def test_never_synced_is_not_usable_even_with_a_connected_source():
    """State (b). No successful sync → the index proves nothing, so no answer
    path may read it. This is the whole point of tracking sync state
    separately: row count alone cannot distinguish this from state (a)."""
    assert not ci.Freshness(connected=True, as_of=None).usable


def test_no_source_is_not_usable():
    """Nothing connected → the interceptions must not fire at all. Not a
    failure; there is simply nothing to index."""
    assert not ci.Freshness(connected=False).usable


def test_a_completed_sync_makes_the_index_usable():
    """State (a) proper: we looked, and there are none. That is an answer."""
    assert ci.Freshness(connected=True, as_of=datetime.now(timezone.utc)).usable


def test_listing_refuses_to_answer_from_an_unsynced_index(monkeypatch):
    """The silent bug, pinned. Rows exist but no sync ever completed — the
    answer path must fall through rather than present them as the call list."""
    monkeypatch.setattr(ci, "list_calls", lambda *a, **k: [_call(1)])
    out = ci.answer_listing(
        "ent-A", "list the calls", fresh=ci.Freshness(connected=True, as_of=None)
    )
    assert out is None


def test_single_call_refuses_to_answer_from_an_unsynced_index(monkeypatch):
    """Same gate on the single-call path: resolving a name against a half-built
    index is how you confidently summarize the WRONG call."""
    monkeypatch.setattr(ci, "resolve_calls", lambda *a, **k: [_call(1)])
    out = ci.answer_single_call(
        "ent-A", "summarize the Initech call",
        fresh=ci.Freshness(connected=False),
    )
    assert out is None


def test_stale_listing_discloses_its_age_instead_of_asserting_a_count(monkeypatch):
    """State (c). A count is a completeness claim; we may only make it when the
    index is current. Stale → still answer, but say how old it is and why."""
    monkeypatch.setattr(ci, "list_calls", lambda *a, **k: [_call(i) for i in range(1, 4)])
    stale = ci.Freshness(
        connected=True,
        as_of=datetime.now(timezone.utc) - timedelta(hours=6),
        stale=True,
        error="the transcript source did not respond in time",
    )
    out = ci.answer_listing("ent-A", "list the calls", fresh=stale)

    assert "3 calls" in out["answer"]          # still useful
    assert "6h ago" in out["answer"]           # but dated
    assert "did not respond" in out["answer"]  # and says why


def test_a_fresh_listing_carries_no_caveat(monkeypatch):
    """A caveat on every answer trains the reader to ignore it. Only stale
    answers get one."""
    monkeypatch.setattr(ci, "list_calls", lambda *a, **k: [_call(1)])
    out = ci.answer_listing("ent-A", "list the calls", fresh=_fresh())
    assert "_Note:" not in out["answer"]


def test_ensure_fresh_within_ttl_does_no_work(monkeypatch):
    """The hot path is ONE row read. A company synced recently must not touch
    the connection table or the network — this runs on every call question."""
    recent = datetime.now(timezone.utc) - timedelta(minutes=2)
    monkeypatch.setattr(
        ci, "_sync_state", lambda cid: {"last_success_at": recent.isoformat()}
    )

    def _boom(*_a, **_k):  # pragma: no cover — asserts it is never reached
        raise AssertionError("ensure_fresh touched the source inside the TTL")

    monkeypatch.setattr(ci, "_has_source", _boom)
    monkeypatch.setattr(ci, "sync_company", _boom)

    fresh = ci.ensure_fresh("ent-A")
    assert fresh.usable and not fresh.stale


def test_ensure_fresh_reports_no_source_rather_than_syncing(monkeypatch):
    monkeypatch.setattr(ci, "_sync_state", lambda cid: None)
    monkeypatch.setattr(ci, "_has_source", lambda cid: False)
    fresh = ci.ensure_fresh("ent-A")
    assert not fresh.connected and not fresh.usable


def test_ensure_fresh_tops_up_incrementally_from_the_last_success(monkeypatch):
    """A refresh must not re-read the whole history: it asks only for calls
    since the last success, minus a deliberate overlap for late-arriving ones."""
    last = datetime.now(timezone.utc) - timedelta(hours=6)
    monkeypatch.setattr(
        ci, "_sync_state", lambda cid: {"last_success_at": last.isoformat()}
    )
    monkeypatch.setattr(ci, "_has_source", lambda cid: True)
    seen = {}

    def _sync(company_id, *, since=None, **_k):
        seen["since"] = since
        return 1

    monkeypatch.setattr(ci, "sync_company", _sync)

    fresh = ci.ensure_fresh("ent-A")
    assert fresh.usable and not fresh.stale
    assert seen["since"] == last - ci._INCREMENTAL_OVERLAP


def test_incremental_since_mirrors_the_read_path_anchor(monkeypatch):
    """The scheduler's cycle must top up, not re-read history: passing no
    `since` made every 20-minute refresh a full ten-page re-sync, which
    exhausted a tenant's Fireflies daily quota (2026-08-15) and 429-blocked
    every other Fireflies read for that account. Same anchor as
    `ensure_fresh`: last success minus the late-arrival overlap."""
    last = datetime.now(timezone.utc) - timedelta(hours=6)
    monkeypatch.setattr(
        ci, "_sync_state", lambda cid: {"last_success_at": last.isoformat()}
    )
    assert ci.incremental_since("ent-A") == last - ci._INCREMENTAL_OVERLAP


def test_incremental_since_is_none_before_the_first_success(monkeypatch):
    """A fresh connection still needs its one full history pull."""
    monkeypatch.setattr(ci, "_sync_state", lambda cid: None)
    assert ci.incremental_since("ent-A") is None


def test_ensure_fresh_degrades_to_stale_when_the_source_fails(monkeypatch):
    """A failed refresh must NOT look fresh. We keep the old `as_of` and mark
    it stale, so the answer discloses its age instead of asserting a count."""
    last = datetime.now(timezone.utc) - timedelta(hours=6)
    monkeypatch.setattr(
        ci, "_sync_state", lambda cid: {"last_success_at": last.isoformat()}
    )
    monkeypatch.setattr(ci, "_has_source", lambda cid: True)

    def _sync(*_a, **_k):
        raise RuntimeError("Fireflies GraphQL error")

    monkeypatch.setattr(ci, "sync_company", _sync)

    fresh = ci.ensure_fresh("ent-A")
    assert fresh.stale
    assert fresh.as_of == last          # not advanced by a failure
    assert "Fireflies" in (fresh.error or "")
    assert fresh.usable                 # we still hold real rows worth showing


def test_a_failed_first_sync_leaves_the_index_unusable(monkeypatch):
    """Never synced AND the refresh failed → we know nothing. Falling through is
    right; claiming "no calls" would be the confidently-wrong answer."""
    monkeypatch.setattr(ci, "_sync_state", lambda cid: None)
    monkeypatch.setattr(ci, "_has_source", lambda cid: True)

    def _sync(*_a, **_k):
        raise RuntimeError("401 unauthorized")

    monkeypatch.setattr(ci, "sync_company", _sync)

    fresh = ci.ensure_fresh("ent-A")
    assert fresh.connected and fresh.stale
    assert not fresh.usable


def test_a_slow_source_times_out_instead_of_hanging_the_turn(monkeypatch):
    """An inline refresh runs inside a chat turn, so it is bounded. On timeout
    we answer from what we hold, disclosed as stale."""
    import time

    last = datetime.now(timezone.utc) - timedelta(hours=2)
    monkeypatch.setattr(
        ci, "_sync_state", lambda cid: {"last_success_at": last.isoformat()}
    )
    monkeypatch.setattr(ci, "_has_source", lambda cid: True)
    monkeypatch.setattr(ci, "sync_company", lambda *a, **k: time.sleep(5))

    started = time.monotonic()
    fresh = ci.ensure_fresh("ent-A", timeout_s=0.2)
    elapsed = time.monotonic() - started

    assert elapsed < 2.0
    assert fresh.stale and fresh.as_of == last
    assert "did not respond" in (fresh.error or "")


def test_windowed_routing_stands_down_when_the_index_is_unusable(monkeypatch):
    """Without this gate an unsynced index reads as "no calls in that window"
    and the question routes to the generic path — the exact silent failure this
    routing exists to fix, reintroduced one level up."""
    monkeypatch.setattr(
        ci, "ensure_fresh", lambda *a, **k: ci.Freshness(connected=True, as_of=None)
    )

    def _boom(*_a, **_k):  # pragma: no cover
        raise AssertionError("read the index without a usable freshness result")

    monkeypatch.setattr(ci, "list_calls", _boom)
    assert ci.windowed_call_question("ent-A", "what came up last week") is None


def test_windowed_routing_does_not_sync_for_a_question_with_no_window(monkeypatch):
    """`ensure_fresh` may reach the network, so it sits AFTER the cheap gates.
    A question that was never going to route here must not pay for a sync."""
    def _boom(*_a, **_k):  # pragma: no cover
        raise AssertionError("ensure_fresh ran before the window gate")

    monkeypatch.setattr(ci, "ensure_fresh", _boom)
    assert ci.windowed_call_question("ent-A", "what should we build next") is None


# ── sync bookkeeping ─────────────────────────────────────────────────────────
#
# The stamp IS the fix. Without it a zero-row read cannot tell "no calls" from
# "never synced", and every downstream guard above is guessing.


class _FakeTable:
    def __init__(self, sink): self.sink = sink
    def upsert(self, row, **_k): self.sink.append(row); return self
    def delete(self): self.sink.append(("delete",)); return self
    def eq(self, *_a, **_k): return self
    def execute(self): return type("R", (), {"data": []})()


def _capture_writes(monkeypatch):
    """Collect every row written to call_index_sync."""
    written: list = []
    monkeypatch.setattr(
        ci, "_write_sync_state",
        lambda cid, patch: written.append(patch),
    )
    return written


def test_a_full_sync_that_finds_nothing_still_stamps_success(monkeypatch):
    """The obvious-looking `if not rows: return 0` is exactly the bug.

    A company whose source genuinely has no calls must end up DISTINGUISHABLE
    from one that was never synced — otherwise chat answers "no calls" to a
    question it never actually checked.
    """
    written = _capture_writes(monkeypatch)
    monkeypatch.setattr(ci, "_own_domains", lambda *a, **k: set())

    count = ci._sync_from_source(
        "ent-A", "key", limit=500, since=None,
        post=lambda *a, **k: [], client=_FakeClient(),
    )

    assert count == 0
    assert written and written[0]["last_success_at"]
    assert written[0]["last_error"] is None
    assert written[0]["call_count"] == 0     # a full sync may state the count


def test_an_incremental_sync_does_not_overwrite_the_call_count(monkeypatch):
    """A top-up that writes 0 rows means "nothing new", not "no calls". Letting
    it set call_count=0 would turn a healthy refresh into evidence of an empty
    account."""
    written = _capture_writes(monkeypatch)
    monkeypatch.setattr(ci, "_own_domains", lambda *a, **k: set())

    ci._sync_from_source(
        "ent-A", "key", limit=500, since=datetime.now(timezone.utc),
        post=lambda *a, **k: [], client=_FakeClient(),
    )

    assert "call_count" not in written[0]
    assert written[0]["last_success_at"]


def test_an_incremental_sync_asks_the_source_for_only_the_new_window(monkeypatch):
    """One page, one HTTP call, however deep the history — that is what makes
    an inline read-path refresh affordable."""
    monkeypatch.setattr(ci, "_write_sync_state", lambda *a, **k: None)
    monkeypatch.setattr(ci, "_own_domains", lambda *a, **k: set())
    since = datetime(2026, 8, 1, tzinfo=timezone.utc)
    seen: list = []

    def _post(_key, _query, variables):
        seen.append(variables)
        return []

    ci._sync_from_source("ent-A", "key", limit=500, since=since,
                         post=_post, client=_FakeClient())

    assert seen[0]["fromDate"].startswith("2026-08-01")


def test_a_full_sync_sends_no_date_bound(monkeypatch):
    monkeypatch.setattr(ci, "_write_sync_state", lambda *a, **k: None)
    monkeypatch.setattr(ci, "_own_domains", lambda *a, **k: set())
    seen: list = []

    def _post(_key, _query, variables):
        seen.append(variables)
        return []

    ci._sync_from_source("ent-A", "key", limit=500, since=None,
                         post=_post, client=_FakeClient())

    assert "fromDate" not in seen[0]


def test_a_failed_sync_does_not_stamp_success(monkeypatch):
    """A sync that stamps freshness on failure is worse than one that never
    stamps at all: the index would read as current while the data rots."""
    written = _capture_writes(monkeypatch)
    monkeypatch.setattr(ci, "_record_sync_failure",
                        lambda cid, exc: written.append({"last_error": str(exc)}))
    monkeypatch.setattr(ci, "_own_domains", lambda *a, **k: set())

    def _explode(*_a, **_k):
        raise RuntimeError("Fireflies GraphQL error")

    monkeypatch.setattr(ci, "_sync_from_source", _explode)
    monkeypatch.setattr(ci, "_load_api_key_for_test", lambda *_a: "key", raising=False)

    import app.call_digest as cd
    monkeypatch.setattr(cd, "_load_api_key", lambda *_a: "key")
    monkeypatch.setattr("app.db.client.require_client", lambda: _FakeClient())

    try:
        ci.sync_company("ent-A")
    except RuntimeError:
        pass

    assert written and "last_success_at" not in written[0]
    assert "Fireflies" in written[0]["last_error"]


def test_no_connected_source_is_not_stamped_as_a_sync_outcome(monkeypatch):
    """"Never connected" must not look like "synced and found nothing" — that
    would make `usable` true and let chat answer "no calls" for a company with
    no transcript source at all."""
    written = _capture_writes(monkeypatch)
    import app.call_digest as cd
    monkeypatch.setattr(cd, "_load_api_key", lambda *_a: None)

    assert ci.sync_company("ent-A") is None   # None, not 0 — see the contract
    assert written == []


class _FakeClient:
    def __init__(self): self.rows: list = []
    def table(self, _name): return _FakeTable(self.rows)


def test_a_vanished_source_reads_as_disconnected_not_as_an_empty_index(monkeypatch):
    """The hole this closes: `sync_company` used to return 0 both for "synced,
    found nothing" and "there is no source", so a company whose connection
    lookup failed open would sync nothing, be stamped usable, and be told
    "No calls. Your transcript source is connected and I checked it" — with no
    transcript source at all.

    `sync_company` now returns None for that case and `ensure_fresh` reports it
    as disconnected.
    """
    monkeypatch.setattr(ci, "_sync_state", lambda cid: None)
    monkeypatch.setattr(ci, "_has_source", lambda cid: True)   # fails open
    monkeypatch.setattr(ci, "sync_company", lambda *a, **k: None)

    fresh = ci.ensure_fresh("ent-A")
    assert not fresh.connected
    assert not fresh.usable


def test_sync_company_returns_none_when_no_source_is_connected(monkeypatch):
    """The return contract the guard above depends on: None, never 0."""
    import app.call_digest as cd
    monkeypatch.setattr(cd, "_load_api_key", lambda *_a: None)
    assert ci.sync_company("ent-A") is None


def test_concurrent_call_questions_produce_one_sync(monkeypatch):
    """A burst of call questions in one session must not fire a sync each, all
    fetching the same page."""
    import threading as th

    monkeypatch.setattr(ci, "_sync_state", lambda cid: None)
    monkeypatch.setattr(ci, "_has_source", lambda cid: True)
    syncs = []
    gate = th.Event()

    def _slow_sync(company_id, **_k):
        syncs.append(company_id)
        gate.wait(0.5)          # hold the lock while the others pile up
        return 1

    monkeypatch.setattr(ci, "sync_company", _slow_sync)

    threads = [
        th.Thread(target=lambda: ci.ensure_fresh("ent-A", timeout_s=0.05))
        for _ in range(4)
    ]
    for t in threads:
        t.start()
    for t in threads:
        t.join(2)
    gate.set()

    assert len(syncs) == 1, f"expected one sync, got {len(syncs)}"


def test_listing_with_no_window_reads_naturally(monkeypatch):
    """"No calls recorded at all" — not "no calls for that period" when the
    question named no period."""
    monkeypatch.setattr(ci, "list_calls", lambda *a, **k: [])
    out = ci.answer_listing("ent-A", "which calls have we had", fresh=_fresh())
    assert "recorded at all" in out["answer"]
    assert "that period" not in out["answer"]


def test_a_ds_question_with_a_window_is_not_hijacked_to_the_calls(monkeypatch):
    """Ordering regression.

    `is_data_analysis_request` is a lexical gate that never checks whether
    tabular data exists, and `_NOT_CALLS` covers csv/spreadsheet/dashboard but
    NOT "numbers" or "metrics" — so "what do the numbers say about last week"
    matches the DS rules AND the windowed-calls route. The DS interception must
    win; the index route sits below it.
    """
    import app.qa_agent as qa
    from app.skill_router import is_data_analysis_request

    question = "what do the numbers say about last week"
    assert is_data_analysis_request(question), "premise: this IS a DS question"
    assert not ci._NOT_CALLS.search(question), "premise: _NOT_CALLS misses it"

    # `qa.answer` judges every interceptor off `routing_text` (the question
    # up to the `[Attached files]` marker, see qa_agent._routing_text) rather
    # than the raw `question` — an argument rename, not a reordering. The
    # literal this ordering check searches for moved with it.
    src = __import__("inspect").getsource(qa.answer)
    ds_at = src.index("is_data_analysis_request(routing_text)")
    window_at = src.index("call_index.windowed_call_question")
    assert ds_at < window_at, (
        "the windowed-calls route must sit BELOW the DS interception, or a DS "
        "question with a named window routes to the call digest"
    )


def test_the_index_routing_still_precedes_the_call_digest(monkeypatch):
    """The other half of the ordering: the cheap index paths must come before
    the ~168s digest, which is the saving this whole module is for."""
    import app.qa_agent as qa

    src = __import__("inspect").getsource(qa.answer)
    assert src.index("call_index.is_listing_request") < src.index("is_call_digest(routing_text)")
    assert src.index("call_index.is_single_call_request") < src.index("is_call_digest(routing_text)")


# ── Zoom as a second source ──────────────────────────────────────────────────
#
# `call_index` was provider-agnostic in its SCHEMA and hardcoded to Fireflies in
# its CODE. These pin the generalization, and above all the ISOLATION: two
# sources share one table and one company, and neither may clobber the other's
# rows or its watermark.


class _ProviderTable:
    """Records upserts/deletes with the filters applied, so a test can assert
    WHICH provider a write or a wipe touched."""

    def __init__(self, sink, name):
        self.sink = sink
        self.name = name
        self.filters: dict = {}
        self._deleting = False

    def upsert(self, rows, **kw):
        self.sink.append({"table": self.name, "rows": rows,
                          "on_conflict": kw.get("on_conflict")})
        return self

    def delete(self):
        self._deleting = True
        return self

    def select(self, *_a, **_k):
        return self

    def eq(self, col, val):
        self.filters[col] = val
        return self

    def limit(self, *_a):
        return self

    def execute(self):
        if self._deleting:
            self.sink.append({"table": self.name, "deleted": dict(self.filters)})
        return type("R", (), {"data": [], "count": 0})()


class _ProviderClient:
    def __init__(self):
        self.ops: list = []

    def table(self, name):
        return _ProviderTable(self.ops, name)


class _Ctx:
    """Stand-in for zoom_oauth.ZoomContext."""

    def __init__(self, user_ids=(), user_names=None):
        self.company_id = "ent-A"
        self.access_token = "tok"
        self.user_ids = list(user_ids)
        self.user_names = dict(user_names or {})
        self.last_synced_until = None


def _zoom_meeting(uuid="zm-1", topic="Acme QBR", host="sam@acme.co"):
    return {
        "uuid": uuid,
        "id": 99,
        "topic": topic,
        "start_time": "2026-07-20T10:00:00Z",
        "duration": 42,
        "host_email": host,
    }


def test_zoom_rows_carry_the_zoom_provider_and_the_shared_conflict_key(monkeypatch):
    """Idempotency is the whole contract: rows upsert on
    (company_id, provider, external_id), so a re-sync refreshes rather than
    duplicating a company's entire call history."""
    monkeypatch.setattr(ci, "_stamp_state", lambda *a, **k: None)
    monkeypatch.setattr(ci, "_own_domains", lambda *a, **k: set())
    client = _ProviderClient()

    count = ci._sync_zoom_from_source(
        "ent-A", _Ctx(user_ids=["u1"]), limit=500, since=None,
        list_recordings=lambda *a, **k: [_zoom_meeting()],
        client=client,
    )

    assert count >= 1
    write = next(op for op in client.ops if op.get("rows"))
    assert write["on_conflict"] == "company_id,provider,external_id"
    row = write["rows"][0]
    assert row["provider"] == "zoom"
    assert row["external_id"] == "zm-1"
    assert row["title"] == "Acme QBR"
    assert row["duration_min"] == 42.0
    assert (row["call_date"] or "").startswith("2026-07-20")


def test_a_recurring_zoom_meeting_seen_in_two_windows_is_indexed_once(monkeypatch):
    """Upsert would dedupe the ROW, but the same uuid twice in one batch is a
    wasted write and a misleading count."""
    monkeypatch.setattr(ci, "_stamp_state", lambda *a, **k: None)
    monkeypatch.setattr(ci, "_own_domains", lambda *a, **k: set())

    count = ci._sync_zoom_from_source(
        "ent-A", _Ctx(user_ids=["u1"]), limit=500, since=None,
        list_recordings=lambda *a, **k: [_zoom_meeting()],
        client=_ProviderClient(),
    )
    assert count == 1


def test_zoom_indexing_honours_the_host_selection(monkeypatch):
    """The picker is not decoration — an unselected host is never fetched."""
    monkeypatch.setattr(ci, "_stamp_state", lambda *a, **k: None)
    monkeypatch.setattr(ci, "_own_domains", lambda *a, **k: set())
    asked: list = []

    def _list(_tok, user_id, **_k):
        asked.append(str(user_id))
        return [_zoom_meeting(uuid="m-" + str(user_id))]

    ci._sync_zoom_from_source(
        "ent-A", _Ctx(user_ids=["u2"], user_names={"u2": "kim@acme.co"}),
        limit=500, since=None, list_recordings=_list, client=_ProviderClient(),
    )
    assert set(asked) == {"u2"}


def test_zoom_indexing_never_asks_for_more_than_a_month(monkeypatch):
    """Zoom SILENTLY CLAMPS a wider from/to, so an over-wide request returns a
    month and looks like a quiet quarter."""
    from datetime import date as _date

    from app.connectors.zoom_oauth import MAX_WINDOW_DAYS

    monkeypatch.setattr(ci, "_stamp_state", lambda *a, **k: None)
    monkeypatch.setattr(ci, "_own_domains", lambda *a, **k: set())
    windows: list = []

    def _list(_tok, _uid, *, frm=None, to=None, **_k):
        windows.append((frm, to))
        return []

    ci._sync_zoom_from_source(
        "ent-A", _Ctx(user_ids=["u1"]), limit=500, since=None,
        list_recordings=_list, client=_ProviderClient(),
    )

    assert windows
    for frm, to in windows:
        span = (_date.fromisoformat(to) - _date.fromisoformat(frm)).days
        assert 0 <= span <= MAX_WINDOW_DAYS, (frm, to)


def test_a_zoom_sync_stamps_only_the_zoom_watermark(monkeypatch):
    """`call_index_sync` is keyed (company_id, provider). A Zoom sync writing
    Fireflies' row would mark Fireflies fresh when it had not run — and a
    listing would then state a count from stale data with no hedge."""
    stamped: list = []
    monkeypatch.setattr(
        ci, "_write_sync_state",
        lambda cid, patch, provider=ci.PROVIDER_FIREFLIES:
            stamped.append((cid, provider, patch)),
    )
    monkeypatch.setattr(ci, "_own_domains", lambda *a, **k: set())

    ci._sync_zoom_from_source(
        "ent-A", _Ctx(user_ids=["u1"]), limit=500, since=None,
        list_recordings=lambda *a, **k: [_zoom_meeting()],
        client=_ProviderClient(),
    )

    assert stamped
    assert all(provider == "zoom" for _cid, provider, _p in stamped)
    assert stamped[0][2]["last_success_at"]
    assert stamped[0][2]["call_count"] == 1


def test_a_fireflies_sync_still_stamps_only_fireflies(monkeypatch):
    """The other direction of the same isolation."""
    stamped: list = []
    monkeypatch.setattr(
        ci, "_write_sync_state",
        lambda cid, patch, provider=ci.PROVIDER_FIREFLIES:
            stamped.append((cid, provider, patch)),
    )
    monkeypatch.setattr(ci, "_own_domains", lambda *a, **k: set())

    ci._sync_from_source(
        "ent-A", "key", limit=500, since=None,
        post=lambda *a, **k: [], client=_FakeClient(),
    )

    assert stamped and all(p == "fireflies" for _c, p, _patch in stamped)


def test_sync_all_sources_indexes_both_providers(monkeypatch):
    """A company with Fireflies AND Zoom must get BOTH indexed in one pass."""
    calls: list = []

    def _sync(company_id, *, limit=None, since=None, provider=ci.PROVIDER_FIREFLIES):
        calls.append(provider)
        return 2

    monkeypatch.setattr(ci, "sync_company", _sync)
    total = ci.sync_all_sources("ent-A")

    assert calls == list(ci.CALL_PROVIDERS)
    assert total == 4


def test_sync_all_sources_returns_none_when_nothing_is_connected(monkeypatch):
    """None, not 0 — the distinction the entire freshness layer rests on."""
    monkeypatch.setattr(ci, "sync_company", lambda *a, **k: None)
    assert ci.sync_all_sources("ent-A") is None


def test_one_source_failing_does_not_stop_the_other(monkeypatch):
    """A partial index is strictly better than none, and the per-provider sync
    state records exactly which half is stale."""
    def _sync(company_id, *, limit=None, since=None, provider=ci.PROVIDER_FIREFLIES):
        if provider == ci.PROVIDER_FIREFLIES:
            raise RuntimeError("Fireflies GraphQL error")
        return 3

    monkeypatch.setattr(ci, "sync_company", _sync)
    assert ci.sync_all_sources("ent-A") == 3


def test_every_source_failing_re_raises(monkeypatch):
    """A total failure must still reach the caller — a silent zero would look
    like a company with no calls."""
    def _sync(*_a, **_k):
        raise RuntimeError("everything is down")

    monkeypatch.setattr(ci, "sync_company", _sync)
    with pytest.raises(RuntimeError):
        ci.sync_all_sources("ent-A")


def test_freshness_takes_the_STALEST_contributing_source(monkeypatch):
    """`list_calls` reads across providers, so an answer is only as fresh as its
    stalest contributor. Taking the newest would let a Zoom sync from a minute
    ago vouch for day-old Fireflies rows — and answer_listing STATES A COUNT."""
    old = datetime.now(timezone.utc) - timedelta(hours=20)
    new = datetime.now(timezone.utc)
    monkeypatch.setattr(
        ci, "_state_for",
        lambda cid, provider: {"last_success_at": new.isoformat()},
    )
    assert ci._oldest_success("ent-A", old) == old


def test_a_zoom_only_company_is_fresh_on_zooms_own_watermark(monkeypatch):
    """Fireflies never synced and never will here — gating on it would take a
    full Zoom index permanently offline."""
    zoom_at = datetime.now(timezone.utc)
    monkeypatch.setattr(
        ci, "_state_for",
        lambda cid, provider: {"last_success_at": zoom_at.isoformat()},
    )
    assert ci._oldest_success("ent-A", None) == zoom_at


def test_a_company_with_no_zoom_state_is_unaffected(monkeypatch):
    """The Fireflies-only path must be exactly what it was — including its
    cost, which is why this short-circuits before any connectivity lookup."""
    ff = datetime.now(timezone.utc)
    monkeypatch.setattr(ci, "_state_for", lambda cid, provider: None)
    assert ci._oldest_success("ent-A", ff) == ff


def test_a_connected_but_never_synced_source_does_not_blank_the_index(monkeypatch):
    """A source that never succeeded has no rows in the index, so it has
    nothing to be stale about. Its failure is reported on the connection row."""
    ff = datetime.now(timezone.utc)
    monkeypatch.setattr(
        ci, "_state_for",
        lambda cid, provider: {"last_success_at": None, "last_error": "boom"},
    )
    assert ci._oldest_success("ent-A", ff) == ff


def test_has_source_accepts_zoom_alone(monkeypatch):
    """Gating on Fireflies alone would report connected=False for a company
    whose Zoom index is full, and every answer path would decline."""
    monkeypatch.setattr(ci, "connected_call_providers", lambda cid: ["zoom"])
    assert ci._has_source("ent-A") is True
    monkeypatch.setattr(ci, "connected_call_providers", lambda cid: [])
    assert ci._has_source("ent-A") is False


def test_clearing_one_provider_leaves_the_others_index_alone(monkeypatch):
    """Disconnecting Fireflies must not destroy a working Zoom index — the
    customer disconnects one tool and loses another tool's call history, with
    nothing anywhere to explain it."""
    client = _ProviderClient()
    monkeypatch.setattr("app.db.client.require_client", lambda: client)

    ci.clear_company("ent-A", ci.PROVIDER_FIREFLIES)

    deletes = [op for op in client.ops if "deleted" in op]
    assert deletes, "nothing was deleted"
    for op in deletes:
        assert op["deleted"]["company_id"] == "ent-A"
        assert op["deleted"]["provider"] == "fireflies"


def test_an_unscoped_clear_still_wipes_everything(monkeypatch):
    """Account-level teardown keeps its old behaviour."""
    client = _ProviderClient()
    monkeypatch.setattr("app.db.client.require_client", lambda: client)

    ci.clear_company("ent-A")

    deletes = [op for op in client.ops if "deleted" in op]
    assert deletes
    for op in deletes:
        assert "provider" not in op["deleted"]


def test_a_transcript_fetch_goes_to_the_source_that_minted_the_id(monkeypatch):
    """An external_id only means something to its own provider — asking
    Fireflies for a Zoom meeting uuid gets a confident nothing, and the answer
    path falls through for a call we could have summarized."""
    seen: dict = {}

    def _zoom(cid, eid):
        seen["zoom"] = eid
        return {"sentences": []}

    monkeypatch.setattr(ci, "_fetch_zoom_transcript", _zoom)
    ci.fetch_transcript("ent-A", "zm-1", provider=ci.PROVIDER_ZOOM)
    assert seen["zoom"] == "zm-1"


def test_summarizing_dispatches_on_the_rows_provider(monkeypatch):
    """The IndexedCall carries its provider precisely so this dispatch is
    possible."""
    asked: list = []

    def _fetch(cid, eid, *, provider=ci.PROVIDER_FIREFLIES):
        asked.append((eid, provider))
        return None

    monkeypatch.setattr(ci, "fetch_transcript", _fetch)
    call = ci.IndexedCall(
        external_id="zm-9", title="Acme QBR", call_date="2026-07-20T10:00:00Z",
        duration_min=30.0, participants=[], account=None, summary="",
        provider=ci.PROVIDER_ZOOM,
    )
    assert ci._summarize_calls("ent-A", "summarize the acme qbr", [call]) is None
    assert asked == [("zm-9", "zoom")]


def test_an_indexed_call_defaults_to_fireflies():
    """Every pre-existing construction site is untouched."""
    call = ci.IndexedCall(
        external_id="ff-1", title="t", call_date=None, duration_min=None,
        participants=[], account=None, summary="",
    )
    assert call.provider == "fireflies"
