"""Implicit document resolution — which document a question is about when it
never names one (`app.document_referent` + Stage R in
`app.ask_runner.document_grounding`).

THE ACCEPTANCE BAR FOR THIS FEATURE IS THE FALSE-POSITIVE SUITE BELOW, not the
happy paths. The previous attempt at this resolved correctly often enough and
was rejected anyway, because it also pinned a Confluence page onto "what's our
pricing strategy?" — an ordinary business question with no document in it. A
document asserted as the subject of a question that was not about a document
makes the model answer as that document, which is strictly worse than the
pre-fix behaviour of resolving nothing.

So the suites are, in the order they matter:

  1. `TestNoFalseReferents` — ordinary business questions resolve to NOTHING.
  2. `TestCarryForward` — turn 1 names a document, turn 2 says "what does it
     say about X", and the referent carries.
  3. `TestAmbiguity` — two plausible documents produce a question back to the
     user, never a silent pick.
  4. `TestBothProviders` — Confluence and Google Drive, end to end.
  5. `TestFailurePathsSurvive` — a body fetch that fails still reports its
     reason, and a resolved-but-unfetchable document is never claimed as read.
  6. `TestPromptContract` — the marker the block emits and the marker the
     system prompt keys off are THE SAME STRING. The previous attempt's
     abstention guard was dead in production for exactly this reason.

WHAT "RESOLVES TO NO DOCUMENT" MEANS HERE, precisely, because it is the line
every assertion in suite 1 is drawn on: no manifest entry may carry
`match` of `carried` or `resolved`, and the rendered block may not carry the
referent heading. It does NOT mean no document is loaded. Stage T's rankless
topical fill is unchanged by this work and still puts up to
MAX_SELECTED_DOCUMENTS bodies in the prompt for any question against a
non-empty catalog — deliberately, since a body the model is told to ignore if
irrelevant costs budget, whereas withholding one costs an answer. Stage R adds
the only thing here that is an assertion, and the assertion is what must never
be wrong.
"""
from __future__ import annotations

import pytest

_CID = "co-referent"
_CONFLUENCE_PAGE_ID = "page-sd-8801"
_DRIVE_FILE_ID = "drive-file-4410"
_CANDIDATES_FN = "document_find_candidates"


# ─────────────────────────── seeding helpers ───────────────────────────


def _seed_company(db, company_id=_CID):
    existing = db.table("companies").select("id").eq("id", company_id).execute().data
    if not existing:
        db.table("companies").insert(
            {"id": company_id, "slug": f"slug-{company_id}", "display_name": company_id}
        ).execute()


def _seed_catalog_row(
    db, *, provider, external_id, title, company_id=_CID, source_name="Uploads",
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
        "conversation_id": None,
        "user_id": None,
    }).execute()


def _candidate(
    *, provider, external_id, title, score=0.04, source_name="Uploads",
    summary="", topics=(), doc_date="2026-08-02T10:00:00+00:00",
):
    """One row shaped exactly like `document_find_candidates` returns."""
    return {
        "id": f"cat-{external_id}",
        "provider": provider,
        "external_id": external_id,
        "title": title,
        "source_name": source_name,
        "summary": summary,
        "topics": list(topics),
        "url": None,
        "doc_date": doc_date,
        "conversation_id": None,
        "score": score,
    }


def _seed_drive_corpus_file(db, *, file_id, label, slug, name, text):
    """A synced Drive file: its corpus markdown, plus the provenance row that
    records where that markdown landed."""
    from app import document_bodies
    from app.datasets import dataset_path

    target = dataset_path(slug) / name
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(text)
    db.table("kg_source").insert({
        "id": document_bodies.drive_source_id(_CID, file_id),
        "enterprise_id": _CID,
        "source_type": "google_drive",
        "label": label,
        "config": {"file_id": file_id, "md_dataset": slug, "md_file": name},
        "status": "active",
    }).execute()


class _FakeConfluenceFetch:
    def __init__(self, page=None, *, session=object(), raises=False):
        self.page = page
        self.session = session
        self.raises = raises
        self.pages_fetched = []

    def open_session(self, enterprise_id):
        return self.session

    def get_page(self, session, page_id):
        self.pages_fetched.append(page_id)
        if self.raises:
            raise RuntimeError("auth expired")
        return self.page


@pytest.fixture
def confluence_pages(monkeypatch):
    from app.connectors import confluence_fetch

    def _install(**kwargs):
        fake = _FakeConfluenceFetch(**kwargs)
        monkeypatch.setattr(confluence_fetch, "open_session", fake.open_session)
        monkeypatch.setattr(confluence_fetch, "get_page", fake.get_page)
        return fake

    return _install


@pytest.fixture
def catalog_candidates():
    """Fix `document_find_candidates`' RESULT. The ranking itself lives in the
    Postgres function and is exercised against real Postgres elsewhere; what
    these tests own is what RESOLUTION does with a given shortlist."""
    from tests._fake_supabase import FakeSupabaseClient

    FakeSupabaseClient.rpc_returns.pop(_CANDIDATES_FN, None)

    def _set(rows):
        FakeSupabaseClient.rpc_returns[_CANDIDATES_FN] = rows

    yield _set
    FakeSupabaseClient.rpc_returns.pop(_CANDIDATES_FN, None)


@pytest.fixture
def adjudicator(monkeypatch):
    """Stub the adjudication LLM call and record what it was asked.

    Returns a setter taking the model's verdict as a 1-based shortlist index
    or None. `calls` on the returned object is how the false-positive suite
    proves the adjudicator was never even REACHED for an ordinary business
    question — a guard that only works because a model chose to decline is a
    guard one prompt change away from failing, so the deterministic cue gate
    is asserted separately from the model's judgement."""
    from app import document_referent

    state = {"choice": None, "calls": []}

    def _fake(*, enterprise_id, question, candidates, history=None):
        state["calls"].append({
            "question": question,
            "candidates": [c.get("external_id") for c in candidates],
            "history": history,
        })
        choice = state["choice"]
        if choice is None:
            return None
        return candidates[choice - 1].get("external_id")

    monkeypatch.setattr(document_referent, "adjudicate", _fake)

    class _Handle:
        calls = state["calls"]

        @staticmethod
        def picks(index):
            state["choice"] = index

        @staticmethod
        def declines():
            state["choice"] = None

    return _Handle


def _referent_matches(manifest):
    """Every manifest entry Stage R claimed as the subject of the question."""
    from app.document_referent import MATCH_CARRIED, MATCH_RESOLVED

    return [
        m for m in manifest if m.get("match") in (MATCH_CARRIED, MATCH_RESOLVED)
    ]


def _seed_two_wiki_pages(db):
    _seed_catalog_row(
        db, provider="confluence", external_id="page-pricing",
        title="Enterprise Pricing Model 2026", source_name="Strategy",
        summary="How enterprise contracts are priced, by seat band and usage.",
        topics=["pricing", "enterprise contracts", "seat bands"],
    )
    _seed_catalog_row(
        db, provider="confluence", external_id="page-team",
        title="Engineering Team Structure", source_name="People",
        summary="Squad layout, headcount by squad, and reporting lines.",
        topics=["team structure", "headcount", "squads"],
    )


# ══════════════════ 1. FALSE POSITIVES — the acceptance bar ════════════════


ORDINARY_BUSINESS_QUESTIONS = [
    "what's our pricing strategy?",
    "how big is the team?",
    "what should we build next?",
    "why are customers churning?",
    "how much revenue did we book last quarter?",
    "who are our biggest enterprise accounts?",
    "what are the top customer complaints",
    "should we raise prices for enterprise?",
    "how many engineers do we have on billing",
    "what drives activation for new signups",
]


@pytest.mark.parametrize("question", ORDINARY_BUSINESS_QUESTIONS)
def test_ordinary_business_question_resolves_to_no_document(
    isolated_settings, catalog_candidates, adjudicator, confluence_pages, question
):
    """THE BAR. A question about the business is not a reference to a document,
    even when a document on the shortlist is about the same subject.

    The catalog here is stacked against the test on purpose: the pricing page
    is a strong topical match for half these questions, and the fake
    adjudicator is set to pick the FIRST candidate if it is ever consulted. So
    the only thing that can keep the referent empty is the deterministic cue
    gate refusing to treat these as document questions at all.
    """
    from app.ask_runner import document_grounding

    db = isolated_settings["supabase"]
    _seed_two_wiki_pages(db)
    catalog_candidates([
        _candidate(
            provider="confluence", external_id="page-pricing",
            title="Enterprise Pricing Model 2026", source_name="Strategy",
            summary="How enterprise contracts are priced, by seat band and usage.",
            topics=["pricing", "enterprise contracts"],
        ),
        _candidate(
            provider="confluence", external_id="page-team",
            title="Engineering Team Structure", source_name="People",
            summary="Squad layout, headcount by squad, and reporting lines.",
            topics=["team structure", "headcount"],
        ),
    ])
    confluence_pages(page={"id": "page-pricing", "text": "Seat bands start at 50."})
    adjudicator.picks(1)  # would say yes to anything it is asked

    block, manifest = document_grounding(_CID, question)

    from app.prompts import DOCUMENT_REFERENT_HEADING

    assert _referent_matches(manifest) == [], (
        f"{question!r} was given a document as its subject — this is the "
        f"failure mode the whole feature is gated on"
    )
    assert DOCUMENT_REFERENT_HEADING not in block
    assert adjudicator.calls == [], (
        "an ordinary business question reached the adjudicator; the cue gate "
        "must stop it deterministically, before any model gets a vote"
    )


@pytest.mark.parametrize("question", ORDINARY_BUSINESS_QUESTIONS)
def test_ordinary_business_question_has_no_cue(question):
    """The gate itself, as a pure unit — no DB, no catalog, no model. Keeping
    this separate from the integration test above means a regression points
    straight at the regex rather than at eight layers of wiring."""
    from app.document_referent import has_document_cue

    assert has_document_cue(question) is False


def test_a_prior_turn_about_a_document_does_not_pin_the_next_question(
    isolated_settings, catalog_candidates, adjudicator, confluence_pages
):
    """The nastiest false positive available to a carry-forward design: the
    conversation genuinely WAS about a document last turn, and then the user
    changes the subject to an ordinary business question.

    Carrying there would mean every question after any document discussion
    gets answered as that document, which is the pin-forever failure. The
    referent carries on a REFERRING EXPRESSION, not on proximity."""
    from app.ask_runner import document_grounding

    db = isolated_settings["supabase"]
    _seed_two_wiki_pages(db)
    catalog_candidates([_candidate(
        provider="confluence", external_id="page-pricing",
        title="Enterprise Pricing Model 2026", source_name="Strategy",
        summary="How enterprise contracts are priced.",
    )])
    confluence_pages(page={"id": "page-pricing", "text": "Seat bands start at 50."})
    adjudicator.picks(1)
    history = [
        {"role": "user", "content": "what's in Enterprise Pricing Model 2026?"},
        {"role": "assistant", "content": "It covers seat bands. [Source: Enterprise Pricing Model 2026]"},
    ]

    _, manifest = document_grounding(
        _CID, "how big is the team?", history=history
    )

    assert _referent_matches(manifest) == []


def test_a_topical_load_is_not_a_referent(
    isolated_settings, catalog_candidates, confluence_pages
):
    """The distinction this feature rests on, asserted directly: Stage T still
    loads for a question with no document cue, and that load is labelled
    `topic` — evidence offered to the model, not an assertion about what the
    question means."""
    from app.ask_runner import document_grounding

    db = isolated_settings["supabase"]
    _seed_two_wiki_pages(db)
    catalog_candidates([_candidate(
        provider="confluence", external_id="page-pricing",
        title="Enterprise Pricing Model 2026", source_name="Strategy",
        summary="How enterprise contracts are priced.",
    )])
    confluence_pages(page={"id": "page-pricing", "text": "Seat bands start at 50."})

    _, manifest = document_grounding(_CID, "what's our pricing strategy?")

    entry = next(m for m in manifest if m["file_id"] == "confluence:page-pricing")
    assert entry["match"] == "topic"
    assert entry["loaded"] is True, (
        "Stage T's behaviour must be unchanged by this work — withholding a "
        "body is the failure mode the no-floor design exists to prevent"
    )


def test_adjudicator_declining_leaves_no_referent(
    isolated_settings, catalog_candidates, adjudicator, confluence_pages
):
    """Guard #3 standing alone. The message HAS a document cue and a plausible
    candidate clears the floor, so the adjudicator is genuinely consulted —
    and its "none of these" must be honoured as a refusal, not rounded to the
    top-ranked candidate."""
    from app.ask_runner import document_grounding
    from app.prompts import DOCUMENT_REFERENT_HEADING

    db = isolated_settings["supabase"]
    _seed_two_wiki_pages(db)
    catalog_candidates([_candidate(
        provider="confluence", external_id="page-pricing",
        title="Enterprise Pricing Model 2026", source_name="Strategy",
        summary="How enterprise contracts are priced.",
        topics=["pricing"],
    )])
    confluence_pages(page={"id": "page-pricing", "text": "Seat bands start at 50."})
    adjudicator.declines()

    block, manifest = document_grounding(
        _CID, "what does the onboarding spec say about pricing?"
    )

    assert adjudicator.calls, "the adjudicator should have been consulted here"
    assert _referent_matches(manifest) == []
    assert DOCUMENT_REFERENT_HEADING not in block


def test_out_of_range_adjudication_is_a_refusal_not_a_clamp(isolated_settings):
    """The real `adjudicate`, against a model that answers with an index off
    the end of the shortlist. Clamping into range would convert the one
    signal a confused model has into a pick."""
    from app import document_referent

    class _Result:
        output = {"document": 9, "reason": "the ninth one"}

    document_referent.llm_call = lambda **kw: _Result()  # noqa: E731
    try:
        assert document_referent.adjudicate(
            enterprise_id=_CID,
            question="what does the spec say?",
            candidates=[{"external_id": "a", "title": "A"}],
        ) is None
    finally:
        from app.graph.gateway import llm_call

        document_referent.llm_call = llm_call


def test_adjudication_failure_degrades_to_no_referent(isolated_settings):
    """A gateway that raises must mean "no referent", never an exception that
    reaches the answer. Resolution is an enhancement on top of grounding that
    already worked without it."""
    from app import document_referent

    def _boom(**kw):
        raise RuntimeError("gateway down")

    document_referent.llm_call = _boom
    try:
        assert document_referent.adjudicate(
            enterprise_id=_CID,
            question="what does the spec say?",
            candidates=[{"external_id": "a", "title": "A"}],
        ) is None
    finally:
        from app.graph.gateway import llm_call

        document_referent.llm_call = llm_call


def test_content_term_floor_skips_a_shortlist_with_nothing_in_common(
    isolated_settings, catalog_candidates, adjudicator, confluence_pages
):
    """Guard #2. A cue is present, but the only candidate shares no vocabulary
    with the question at all — so the model is never asked. Cheap, and it
    keeps a shortlist of one irrelevant row from being the thing a model is
    invited to pick from."""
    from app.ask_runner import document_grounding

    db = isolated_settings["supabase"]
    _seed_catalog_row(
        db, provider="confluence", external_id="page-holiday",
        title="Holiday Calendar", source_name="People",
        summary="Public holidays observed by each office.",
        topics=["holidays", "offices"],
    )
    catalog_candidates([_candidate(
        provider="confluence", external_id="page-holiday",
        title="Holiday Calendar", source_name="People",
        summary="Public holidays observed by each office.",
        topics=["holidays", "offices"],
    )])
    confluence_pages(page={"id": "page-holiday", "text": "Offices close Dec 25."})
    adjudicator.picks(1)

    _, manifest = document_grounding(
        _CID, "what does the deployment runbook say about rollbacks?"
    )

    assert adjudicator.calls == []
    assert _referent_matches(manifest) == []


# ══════════════════════════ 2. CARRY-FORWARD ═══════════════════════════════


class TestCarryForward:
    def test_turn_two_resolves_to_the_document_turn_one_named(
        self, isolated_settings, catalog_candidates, confluence_pages
    ):
        """The headline case. Turn 1 names a wiki page; turn 2 says "what does
        it say about X" and names nothing at all.

        On the pre-fix code this could not resolve: Stage N has no name to
        match, and Stage T ranks the word "pricing" against the whole catalog
        with no idea a document was under discussion."""
        from app.ask_runner import document_grounding
        from app.document_referent import MATCH_CARRIED
        from app.prompts import DOCUMENT_REFERENT_HEADING

        db = isolated_settings["supabase"]
        _seed_two_wiki_pages(db)
        # The topical rank puts the WRONG page first, so a pass here cannot be
        # Stage T getting lucky.
        catalog_candidates([_candidate(
            provider="confluence", external_id="page-team",
            title="Engineering Team Structure", source_name="People",
            summary="Squad layout and headcount.",
        )])
        fake = confluence_pages(page={
            "id": "page-pricing",
            "text": "Enterprise pricing uses seat bands starting at 50 seats.",
        })
        history = [
            {"role": "user", "content": "open Enterprise Pricing Model 2026"},
            {"role": "assistant", "content": "That page covers contract pricing."},
        ]

        block, manifest = document_grounding(
            _CID, "what does it say about seat bands?", history=history
        )

        entry = next(m for m in manifest if m["file_id"] == "confluence:page-pricing")
        assert entry["match"] == MATCH_CARRIED
        assert entry["loaded"] is True
        assert "seat bands starting at 50 seats" in block
        assert DOCUMENT_REFERENT_HEADING in block
        assert "page-pricing" in fake.pages_fetched

    def test_an_assistant_citation_establishes_the_referent(
        self, isolated_settings, catalog_candidates, confluence_pages
    ):
        """In practice the document is named by the ASSISTANT's citation more
        often than by the user — the user asks a topic question, the answer
        cites a page, and the follow-up refers back to it."""
        from app.ask_runner import document_grounding
        from app.document_referent import MATCH_CARRIED

        db = isolated_settings["supabase"]
        _seed_two_wiki_pages(db)
        catalog_candidates([])
        confluence_pages(page={"id": "page-pricing", "text": "Seat bands start at 50."})
        history = [
            {"role": "user", "content": "how do enterprise contracts work?"},
            {
                "role": "assistant",
                "content": "Priced by seat band. [Source: Enterprise Pricing Model 2026]",
            },
        ]

        _, manifest = document_grounding(
            _CID, "does that page mention discounts?", history=history
        )

        entry = next(m for m in manifest if m["file_id"] == "confluence:page-pricing")
        assert entry["match"] == MATCH_CARRIED

    def test_the_most_recent_naming_turn_wins(
        self, isolated_settings, catalog_candidates, confluence_pages
    ):
        """A thread that discussed one document and then moved to another is
        about the SECOND one. Unioning the whole window would report a false
        ambiguity for a conversation that is perfectly clear."""
        from app.ask_runner import document_grounding
        from app.document_referent import MATCH_CARRIED

        db = isolated_settings["supabase"]
        _seed_two_wiki_pages(db)
        catalog_candidates([])
        confluence_pages(page={"id": "page-team", "text": "Four squads, 21 engineers."})
        history = [
            {"role": "user", "content": "summarise Enterprise Pricing Model 2026"},
            {"role": "assistant", "content": "Seat bands."},
            {"role": "user", "content": "now show me Engineering Team Structure"},
            {"role": "assistant", "content": "Four squads."},
        ]

        _, manifest = document_grounding(
            _CID, "what does it say about headcount?", history=history
        )

        assert next(
            m for m in manifest if m["file_id"] == "confluence:page-team"
        )["match"] == MATCH_CARRIED
        assert next(
            m for m in manifest if m["file_id"] == "confluence:page-pricing"
        )["match"] != MATCH_CARRIED

    def test_no_anaphor_means_no_carry(
        self, isolated_settings, catalog_candidates, confluence_pages
    ):
        """Same history, a question that does not refer back. Carry needs a
        referring expression; standing next to a document discussion is not
        one."""
        from app.ask_runner import document_grounding

        db = isolated_settings["supabase"]
        _seed_two_wiki_pages(db)
        catalog_candidates([])
        confluence_pages(page={"id": "page-pricing", "text": "Seat bands."})
        history = [
            {"role": "user", "content": "summarise Enterprise Pricing Model 2026"},
            {"role": "assistant", "content": "Seat bands."},
        ]

        _, manifest = document_grounding(
            _CID, "what were revenues last quarter?", history=history
        )

        assert _referent_matches(manifest) == []

    def test_an_anaphor_with_no_prior_document_resolves_to_nothing(
        self, isolated_settings, catalog_candidates, adjudicator, confluence_pages
    ):
        """"What does it say?" as the first message of a conversation. The
        user refers to something, but nothing visible established what — and
        supplying a document anyway is a fabrication dressed as a
        resolution."""
        from app.ask_runner import document_grounding

        db = isolated_settings["supabase"]
        _seed_two_wiki_pages(db)
        catalog_candidates([])
        confluence_pages(page={"id": "page-pricing", "text": "Seat bands."})
        adjudicator.picks(1)

        _, manifest = document_grounding(_CID, "what does it say?", history=[])

        assert _referent_matches(manifest) == []

    def test_a_named_document_beats_resolution(
        self, isolated_settings, catalog_candidates, confluence_pages
    ):
        """Stage N wins outright: a question that spells a document's title
        out is an explicit request, and resolution must not get to argue with
        it even when the conversation was about something else."""
        from app.ask_runner import document_grounding

        db = isolated_settings["supabase"]
        _seed_two_wiki_pages(db)
        catalog_candidates([])
        confluence_pages(page={"id": "page-team", "text": "Four squads."})
        history = [
            {"role": "user", "content": "open Enterprise Pricing Model 2026"},
            {"role": "assistant", "content": "Seat bands."},
        ]

        _, manifest = document_grounding(
            _CID,
            "what does Engineering Team Structure say about squads?",
            history=history,
        )

        entry = next(m for m in manifest if m["file_id"] == "confluence:page-team")
        assert entry["match"] == "named"
        assert _referent_matches(manifest) == []

    def test_a_short_generic_title_does_not_carry(self, isolated_settings):
        """A title short enough to substring-match ordinary prose ("Notes")
        cannot carry — an accidental collision inside a turn is not evidence
        that the user meant that document."""
        from app.document_referent import KnownDocument, carried_referent

        known = [KnownDocument(key="confluence:n", title="Notes", provider="confluence")]
        result = carried_referent(
            "what does it say about pricing?",
            [{"role": "assistant", "content": "I took some notes on that."}],
            known,
        )

        assert result.referent is None
        assert result.ambiguous_titles == []


# ═══════════════════════════ 3. AMBIGUITY ══════════════════════════════════


class TestAmbiguity:
    def test_two_documents_named_in_one_turn_do_not_silently_pick_one(
        self, isolated_settings, catalog_candidates, confluence_pages
    ):
        """The turn that established the referent established two of them.
        The block must say so and instruct the model to ask — the silent pick
        is the behaviour under test."""
        from app.ask_runner import document_grounding
        from app.prompts import (
            DOCUMENT_AMBIGUOUS_HEADING,
            DOCUMENT_REFERENT_HEADING,
        )

        db = isolated_settings["supabase"]
        _seed_two_wiki_pages(db)
        catalog_candidates([])
        confluence_pages(page={"id": "page-pricing", "text": "Seat bands."})
        history = [{
            "role": "user",
            "content": (
                "compare Enterprise Pricing Model 2026 with "
                "Engineering Team Structure"
            ),
        }]

        block, manifest = document_grounding(
            _CID, "what does it say about headcount?", history=history
        )

        assert _referent_matches(manifest) == [], "a tie must not resolve"
        assert DOCUMENT_AMBIGUOUS_HEADING in block
        assert DOCUMENT_REFERENT_HEADING not in block
        assert "Enterprise Pricing Model 2026" in block
        assert "Engineering Team Structure" in block

    def test_the_ambiguity_block_tells_the_model_to_ask(
        self, isolated_settings, catalog_candidates, confluence_pages
    ):
        """The instruction, not just the heading. A heading the model is free
        to interpret is how an ambiguity turns back into a pick."""
        from app.ask_runner import document_grounding

        db = isolated_settings["supabase"]
        _seed_two_wiki_pages(db)
        catalog_candidates([])
        confluence_pages(page={"id": "page-pricing", "text": "Seat bands."})
        history = [{
            "role": "user",
            "content": (
                "compare Enterprise Pricing Model 2026 with "
                "Engineering Team Structure"
            ),
        }]

        block, _ = document_grounding(
            _CID, "what does it say about headcount?", history=history
        )

        assert "Ask the user which one" in block
        assert "Do not pick one yourself" in block


# ══════════════════════ 4. BOTH PROVIDERS, END TO END ══════════════════════


class TestBothProviders:
    def test_confluence_page_resolves_by_description(
        self, isolated_settings, catalog_candidates, adjudicator, confluence_pages
    ):
        """The descriptive path on Confluence: the user describes the page
        ("our integration options write-up") without naming it, adjudication
        identifies it, and its live-fetched body reaches the prompt."""
        from app.ask_runner import document_grounding
        from app.document_referent import MATCH_RESOLVED

        db = isolated_settings["supabase"]
        _seed_catalog_row(
            db, provider="confluence", external_id=_CONFLUENCE_PAGE_ID,
            title="Moolre integration options", source_name="Software Development",
            summary="Three options for moving Moolre transaction data into Sprntly.",
            topics=["integration options", "transaction data", "API"],
        )
        catalog_candidates([_candidate(
            provider="confluence", external_id=_CONFLUENCE_PAGE_ID,
            title="Moolre integration options", source_name="Software Development",
            summary="Three options for moving Moolre transaction data into Sprntly.",
            topics=["integration options", "transaction data"],
        )])
        fake = confluence_pages(page={
            "id": _CONFLUENCE_PAGE_ID,
            "text": "Option 2 has Sprntly expose an endpoint Moolre calls.",
        })
        adjudicator.picks(1)

        block, manifest = document_grounding(
            _CID, "what does our integration options write-up say about the API?"
        )

        entry = next(
            m for m in manifest if m["file_id"] == f"confluence:{_CONFLUENCE_PAGE_ID}"
        )
        assert entry["match"] == MATCH_RESOLVED
        assert entry["loaded"] is True
        assert "Option 2 has Sprntly expose an endpoint" in block
        assert fake.pages_fetched == [_CONFLUENCE_PAGE_ID]

    def test_drive_file_resolves_by_description(
        self, isolated_settings, catalog_candidates, adjudicator
    ):
        """Drive's half of the same path. Its body comes from the corpus
        markdown its own sync wrote, read back through the provenance row."""
        from app.ask_runner import document_grounding
        from app.document_referent import MATCH_RESOLVED

        db = isolated_settings["supabase"]
        _seed_drive_corpus_file(
            db, file_id=_DRIVE_FILE_ID, label="Billing model 2026",
            slug="acme", name="billing_model_2026.md",
            text="Enterprise billing moves to usage-based in Q1.",
        )
        _seed_catalog_row(
            db, provider="google_drive", external_id=_DRIVE_FILE_ID,
            title="Billing model 2026", source_name="Google Drive",
            summary="Enterprise billing moves from seats to usage in Q1.",
            topics=["billing", "usage-based pricing"],
        )
        catalog_candidates([_candidate(
            provider="google_drive", external_id=_DRIVE_FILE_ID,
            title="Billing model 2026", source_name="Google Drive",
            summary="Enterprise billing moves from seats to usage in Q1.",
            topics=["billing", "usage-based pricing"],
        )])
        adjudicator.picks(1)

        block, manifest = document_grounding(
            _CID, "what does the billing doc say about usage pricing?"
        )

        entry = next(
            m for m in manifest if m["file_id"] == f"google_drive:{_DRIVE_FILE_ID}"
        )
        assert entry["match"] == MATCH_RESOLVED
        assert "Enterprise billing moves to usage-based in Q1." in block

    def test_drive_file_carries_across_turns(
        self, isolated_settings, catalog_candidates, adjudicator
    ):
        """Carry-forward is provider-blind by construction — it matches
        titles, not sources — and that is worth pinning down rather than
        assuming, since Drive is the provider the catalog has barely any rows
        for and so the one least likely to be exercised by accident."""
        from app.ask_runner import document_grounding
        from app.document_referent import MATCH_CARRIED

        db = isolated_settings["supabase"]
        _seed_drive_corpus_file(
            db, file_id=_DRIVE_FILE_ID, label="Billing model 2026",
            slug="acme", name="billing_model_2026.md",
            text="Enterprise billing moves to usage-based in Q1.",
        )
        _seed_catalog_row(
            db, provider="google_drive", external_id=_DRIVE_FILE_ID,
            title="Billing model 2026", source_name="Google Drive",
            summary="Enterprise billing moves from seats to usage in Q1.",
        )
        catalog_candidates([])
        adjudicator.declines()
        history = [
            {"role": "user", "content": "open Billing model 2026"},
            {"role": "assistant", "content": "It moves billing to usage."},
        ]

        _, manifest = document_grounding(
            _CID, "what does it say about Q1?", history=history
        )

        entry = next(
            m for m in manifest if m["file_id"] == f"google_drive:{_DRIVE_FILE_ID}"
        )
        assert entry["match"] == MATCH_CARRIED


# ═════════════════ 5. THE HONESTY CONTRACTS STILL HOLD ═════════════════════


class TestFailurePathsSurvive:
    def test_a_resolved_document_that_cannot_be_fetched_says_why(
        self, isolated_settings, catalog_candidates, confluence_pages
    ):
        """The failure-reason contract (prompts rule 9) applied to the NEW
        path. A resolved referent whose body will not load is the most
        dangerous combination in this design: the prompt asserts the question
        is about that document, so if the fetch failure were silent the model
        would answer about a document it never read.

        Both statements must survive together — "this is the document you are
        being asked about" AND "its contents could not be loaded, here is
        why" — and nothing may read as the document being absent."""
        from app.ask_runner import document_grounding
        from app.document_referent import MATCH_CARRIED
        from app.prompts import DOCUMENT_REFERENT_HEADING

        db = isolated_settings["supabase"]
        _seed_two_wiki_pages(db)
        catalog_candidates([])
        confluence_pages(raises=True)
        history = [
            {"role": "user", "content": "open Enterprise Pricing Model 2026"},
            {"role": "assistant", "content": "Seat bands."},
        ]

        block, manifest = document_grounding(
            _CID, "what does it say about seat bands?", history=history
        )

        entry = next(m for m in manifest if m["file_id"] == "confluence:page-pricing")
        assert entry["match"] == MATCH_CARRIED
        assert entry["loaded"] is False, (
            "`loaded` is about the BODY reaching the prompt — resolution "
            "must not record an intention as a fact"
        )
        assert DOCUMENT_REFERENT_HEADING in block
        assert "could not be loaded for this question" in block
        assert "this document exists" in block
        lowered = block.lower()
        for forbidden in (
            "does not exist", "no such document", "not in any connected source",
        ):
            assert forbidden not in lowered

    def test_the_referent_block_does_not_claim_the_document_was_read(
        self, isolated_settings, catalog_candidates, confluence_pages
    ):
        """Naming the subject and asserting a successful read are separate
        claims, and this block makes only the first. Said in the block itself
        rather than left to inference, because the model has no other way to
        tell a referent from a citation."""
        from app.ask_runner import document_grounding

        db = isolated_settings["supabase"]
        _seed_two_wiki_pages(db)
        catalog_candidates([])
        confluence_pages(raises=True)
        history = [{"role": "user", "content": "open Enterprise Pricing Model 2026"}]

        block, _ = document_grounding(
            _CID, "what does it say about seat bands?", history=history
        )

        assert "does not say its contents were loaded" in block

    def test_truncation_marker_survives_on_a_resolved_document(
        self, isolated_settings, catalog_candidates, confluence_pages
    ):
        """A resolved body over budget is still truncated in band, with the
        marker that says so. Resolution changes which document loads, never
        whether an overflow is admitted."""
        from app.ask_runner import document_grounding

        db = isolated_settings["supabase"]
        _seed_two_wiki_pages(db)
        catalog_candidates([])
        confluence_pages(page={"id": "page-pricing", "text": "seat bands. " * 5000})
        history = [{"role": "user", "content": "open Enterprise Pricing Model 2026"}]

        block, _ = document_grounding(
            _CID, "what does it say about seat bands?", history=history
        )

        assert "[Truncated — showing the first" in block

    def test_resolution_blowing_up_still_answers(
        self, isolated_settings, catalog_candidates, confluence_pages, monkeypatch
    ):
        """Stage R is an enhancement on grounding that worked without it. A
        crash inside resolution must cost the referent and nothing else."""
        from app import ask_runner, document_referent

        db = isolated_settings["supabase"]
        _seed_two_wiki_pages(db)
        catalog_candidates([_candidate(
            provider="confluence", external_id="page-pricing",
            title="Enterprise Pricing Model 2026", source_name="Strategy",
            summary="How enterprise contracts are priced.",
        )])
        confluence_pages(page={"id": "page-pricing", "text": "Seat bands start at 50."})

        def _boom(**kw):
            raise RuntimeError("resolution exploded")

        monkeypatch.setattr(document_referent, "resolve", _boom)

        block, manifest = ask_runner.document_grounding(
            _CID, "what does that page say about seat bands?"
        )

        assert "Enterprise Pricing Model 2026" in block
        assert _referent_matches(manifest) == []
        assert next(
            m for m in manifest if m["file_id"] == "confluence:page-pricing"
        )["loaded"] is True


# ═══════════════════ 6. THE PROMPT CONTRACT IS NOT DEAD ════════════════════


class TestPromptContract:
    def test_the_emitted_marker_and_the_prompt_rule_are_the_same_string(self):
        """The previous attempt's abstention guard never fired in production
        because the heading it searched for was emitted into the system prompt
        and matched against assistant content — two strings that could never
        meet. Both ends now reference ONE constant, and this asserts the
        system prompt really does quote it."""
        from app.prompts import (
            ASK_SYSTEM_DOCUMENTS_ADDENDUM,
            DOCUMENT_AMBIGUOUS_HEADING,
            DOCUMENT_REFERENT_HEADING,
        )

        assert DOCUMENT_REFERENT_HEADING in ASK_SYSTEM_DOCUMENTS_ADDENDUM
        assert DOCUMENT_AMBIGUOUS_HEADING in ASK_SYSTEM_DOCUMENTS_ADDENDUM

    def test_the_rendered_block_uses_the_same_constants(self):
        """The other end of the same contract, at the renderer rather than in
        the prompt — so a heading edited in one place fails here instead of
        going quietly dead in production."""
        from app.document_referent import (
            MATCH_CARRIED,
            KnownDocument,
            Resolution,
            render_referent_block,
        )
        from app.prompts import (
            DOCUMENT_AMBIGUOUS_HEADING,
            DOCUMENT_REFERENT_HEADING,
        )

        resolved = "\n".join(render_referent_block(Resolution(
            referent=KnownDocument(key="k", title="A Page", provider="confluence"),
            how=MATCH_CARRIED,
        )))
        ambiguous = "\n".join(render_referent_block(
            Resolution(ambiguous_titles=["A Page", "B Page"])
        ))

        assert DOCUMENT_REFERENT_HEADING in resolved
        assert DOCUMENT_AMBIGUOUS_HEADING in ambiguous
        assert render_referent_block(Resolution()) == []


# ════════════════════ 7. WIRING: history actually arrives ══════════════════


def test_history_threads_from_the_worker_contextvar(
    isolated_settings, catalog_candidates, confluence_pages
):
    """The skill-routed path reaches grounding through
    `qa_agent._answer_single_shot`, which calls `document_grounding` with two
    positional arguments and no history. Without the ContextVar the whole
    feature is dead on the path most traffic takes — and it would look exactly
    like a resolution miss, not like a wiring bug."""
    from app import ask_runner
    from app.document_referent import MATCH_CARRIED

    db = isolated_settings["supabase"]
    _seed_two_wiki_pages(db)
    catalog_candidates([])
    confluence_pages(page={"id": "page-pricing", "text": "Seat bands start at 50."})
    token = ask_runner.set_active_history([
        {"role": "user", "content": "open Enterprise Pricing Model 2026"},
        {"role": "assistant", "content": "Seat bands."},
    ])
    try:
        # Two positional arguments — exactly what qa_agent passes.
        _, manifest = ask_runner.document_grounding(
            _CID, "what does it say about seat bands?"
        )
    finally:
        ask_runner.reset_active_history(token)

    assert next(
        m for m in manifest if m["file_id"] == "confluence:page-pricing"
    )["match"] == MATCH_CARRIED


def test_history_is_never_folded_into_the_question(
    isolated_settings, catalog_candidates, confluence_pages
):
    """History reaches RESOLUTION only. Folding it into `question` would let
    Stage N's substring rule match every filename mentioned anywhere in the
    thread — turning a name match, the one stage that is right by
    construction, into the loosest stage of the three."""
    from app.ask_runner import document_grounding

    db = isolated_settings["supabase"]
    _seed_two_wiki_pages(db)
    catalog_candidates([])
    confluence_pages(page={"id": "page-pricing", "text": "Seat bands."})
    history = [
        {"role": "user", "content": "summarise Enterprise Pricing Model 2026"},
        {"role": "assistant", "content": "Seat bands."},
    ]

    _, manifest = document_grounding(
        _CID, "how many squads do we run?", history=history
    )

    # No anaphor, so no carry; and nothing may be `named` either, which is
    # what a history-polluted question would produce.
    assert [m for m in manifest if m.get("match") == "named"] == []
    assert _referent_matches(manifest) == []


def test_the_worker_publishes_and_clears_the_history_contextvar(isolated_settings):
    """A ContextVar left set outlives the request into whatever ask reuses
    that pooled thread, and a stale history would resolve one user's pronoun
    against another user's conversation. Asserted on the worker itself
    because the `finally` is the whole safety property."""
    import inspect

    from app import ask_job_runner

    source = inspect.getsource(ask_job_runner._run_sync)
    assert "set_active_history" in source
    finally_block = source.split("finally:")[-1]
    assert "reset_active_history" in finally_block
