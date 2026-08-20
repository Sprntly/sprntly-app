"""Bounds on `synthesis_brief._seed_from_corpus`'s retry-forever behaviour.

The loop records a doc as ingested only after a successful extract, so a failing
doc is retried on every run. That is correct for a transiently bad document and
catastrophic for a dead provider account: one company whose Anthropic key ran
out of credit drove ~200k rejected calls over nine days at roughly one per
second. The tokens were free — a rejected request bills nothing — but every one
of those calls took a slot in the process-wide LLM concurrency gate that all
interactive generation queues behind.

Two independent bounds, tested separately because either alone leaves a hole:
the per-run cap must count ATTEMPTS (it counted successes, so it was unreachable
in exactly the all-failing case it existed for), and a provider LIMIT error must
end the pass instead of being isolated per doc.
"""
from __future__ import annotations

import anthropic
import httpx
import pytest

from app import synthesis_brief


class _Doc:
    """A corpus doc. Text defaults to something UNIQUE per name: the seed loop
    dedups on sha256(company|text), so identical bodies would all collapse to
    'unchanged' after the first and the test would silently exercise nothing."""

    def __init__(self, name: str, text: str | None = None) -> None:
        self.name = name
        self.text = text if text is not None else f"real content for {name}"


class _Corpus:
    def __init__(self, docs) -> None:
        self.docs = docs


class _Facade:
    """Enough of GraphFacade for the seed loop: no docs previously ingested."""

    def __init__(self) -> None:
        self.created = []

    def list_sources(self, *a, **k):
        return []

    def create_source(self, company_id, source):
        self.created.append(source)


def _credit_exhausted() -> anthropic.BadRequestError:
    """The exact shape production returned, 200k times.

    Built through the SDK's own error type rather than a stand-in, so the test
    breaks if `llm_errors` stops recognising what the provider actually sends.
    """
    request = httpx.Request("POST", "https://api.anthropic.com/v1/messages")
    response = httpx.Response(400, request=request, json={
        "type": "error",
        "error": {
            "type": "invalid_request_error",
            "message": "Your credit balance is too low to access the Anthropic "
                       "API. Please go to Plans & Billing to upgrade or "
                       "purchase credits.",
        },
    })
    return anthropic.BadRequestError(
        "credit balance is too low", response=response, body=None
    )


@pytest.fixture
def seeded(monkeypatch):
    """`_seed_from_corpus` with the corpus and extractor under test control."""
    calls = {"n": 0}

    monkeypatch.setattr(synthesis_brief, "load_corpus", lambda slug: _Corpus(
        [_Doc(f"doc-{i}") for i in range(60)]
    ))
    monkeypatch.setattr(synthesis_brief.datasets, "md_file_categories",
                        lambda slug: {})
    monkeypatch.setattr(synthesis_brief, "is_unparsed_stub", lambda text: False)
    return calls


def test_all_failing_corpus_still_stops_at_the_per_run_cap(seeded, monkeypatch):
    """The cap counted successes, so nothing incremented when everything failed
    and the WHOLE corpus was re-attempted every run — the cap was unreachable in
    precisely the case it existed to bound."""
    def _boom(*a, **k):
        seeded["n"] += 1
        raise RuntimeError("this document is malformed")

    monkeypatch.setattr(synthesis_brief, "extract_document", _boom)

    totals = synthesis_brief._seed_from_corpus(_Facade(), "co-1", "northwind")

    assert seeded["n"] == synthesis_brief.MAX_SEED_DOCS
    assert totals["attempted"] == synthesis_brief.MAX_SEED_DOCS
    assert totals["docs"] == 0


def test_a_provider_limit_error_ends_the_pass_after_one_call(seeded, monkeypatch):
    """Per-doc isolation is right for a bad document and exactly wrong for a
    dead account: the next doc fails identically, so isolating it means failing
    N times instead of once."""
    def _no_credit(*a, **k):
        seeded["n"] += 1
        raise _credit_exhausted()

    monkeypatch.setattr(synthesis_brief, "extract_document", _no_credit)

    with pytest.raises(anthropic.BadRequestError):
        synthesis_brief._seed_from_corpus(_Facade(), "co-1", "northwind")

    assert seeded["n"] == 1


def test_a_bad_document_is_still_isolated(seeded, monkeypatch):
    """The circuit breaker must not swallow the existing behaviour: one
    unextractable doc is skipped and the rest of the pass proceeds."""
    def _one_bad(facade, company_id, *, doc_name, **k):
        seeded["n"] += 1
        if doc_name == "doc-0":
            raise ValueError("this one document is malformed")
        return {"signals": 1, "themes": 1, "skipped": 0, "signal_ids": []}

    monkeypatch.setattr(synthesis_brief, "extract_document", _one_bad)

    facade = _Facade()
    totals = synthesis_brief._seed_from_corpus(facade, "co-1", "northwind")

    assert totals["docs"] == synthesis_brief.MAX_SEED_DOCS - 1
    assert totals["attempted"] == synthesis_brief.MAX_SEED_DOCS
    # The failed doc is NOT recorded as ingested, so it retries next run — the
    # property that makes a transient failure recoverable, kept intact.
    assert len(facade.created) == synthesis_brief.MAX_SEED_DOCS - 1


def test_a_healthy_corpus_is_unaffected(seeded, monkeypatch):
    def _ok(*a, **k):
        seeded["n"] += 1
        return {"signals": 2, "themes": 1, "skipped": 0, "signal_ids": []}

    monkeypatch.setattr(synthesis_brief, "extract_document", _ok)

    totals = synthesis_brief._seed_from_corpus(_Facade(), "co-1", "northwind")

    assert totals["docs"] == synthesis_brief.MAX_SEED_DOCS
    assert totals["signals"] == 2 * synthesis_brief.MAX_SEED_DOCS
    assert totals["aborted"] == 0


def test_unreadable_stubs_do_not_consume_the_attempt_budget(monkeypatch):
    """Attempt-counting must not accidentally spend the budget on docs that
    never reach the model — a corpus of placeholders would otherwise starve the
    real ones behind it."""
    corpus = _Corpus(
        [_Doc(f"stub-{i}", text="STUB") for i in range(30)] + [_Doc("real")]
    )
    monkeypatch.setattr(synthesis_brief, "load_corpus", lambda slug: corpus)
    monkeypatch.setattr(synthesis_brief.datasets, "md_file_categories",
                        lambda slug: {})
    monkeypatch.setattr(synthesis_brief, "is_unparsed_stub",
                        lambda text: text == "STUB")

    seen = []

    def _ok(facade, company_id, *, doc_name, **k):
        seen.append(doc_name)
        return {"signals": 1, "themes": 0, "skipped": 0, "signal_ids": []}

    monkeypatch.setattr(synthesis_brief, "extract_document", _ok)

    totals = synthesis_brief._seed_from_corpus(_Facade(), "co-1", "northwind")

    assert totals["unreadable"] == 30
    assert seen == ["real"]
    assert totals["attempted"] == 1
