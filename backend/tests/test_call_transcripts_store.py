"""Persisted call transcripts — the digest's stored-first read.

Owner decision 2026-08-12 (reversing call_index's "transcripts are
deliberately not stored"): the VoC digest answers from `call_transcripts`
when the window is covered there, live-fetches only when it is not, and
writes through what a live fetch returns so the first ask warms the rest.
"""
from __future__ import annotations

from unittest.mock import patch

from app.call_digest import Window, build_corpus
from app.db.call_transcripts import load_call_transcripts, store_call_transcripts
from app.kg_ingest.pullers.fireflies import CallTranscript
from datetime import datetime, timezone


def _window() -> Window:
    return Window(
        since=datetime(2026, 8, 1, tzinfo=timezone.utc),
        until=datetime(2026, 8, 8, tzinfo=timezone.utc),
        label="Aug 1–8",
    )


def _call(eid: str, date: str = "2026-08-03T10:00:00+00:00") -> CallTranscript:
    return CallTranscript(
        external_id=eid, title=f"Call {eid}", date=date,
        participants=["ana@acme.com"], overview="Pricing pushback.",
        quotes=[{"speaker": "Ana", "text": "the new tier is confusing"}],
    )


def _connect_fireflies(company_id: str):
    from app import db

    db.upsert_connection(
        company_id=company_id, provider="fireflies",
        token_encrypted="enc", scopes="", status="active",
    )


def test_store_and_load_round_trip(tenant_client):
    t = tenant_client.make(slug="acme")
    assert store_call_transcripts(t.company_id, [_call("c1"), _call("c2")]) == 2
    grouped = load_call_transcripts(
        t.company_id, "2026-08-01T00:00:00+00:00", "2026-08-08T00:00:00+00:00"
    )
    assert {p["external_id"] for p in grouped["fireflies"]} == {"c1", "c2"}
    # Outside the window → nothing.
    assert load_call_transcripts(
        t.company_id, "2026-09-01T00:00:00+00:00", "2026-09-08T00:00:00+00:00"
    ) == {}


def test_digest_answers_from_the_store_without_touching_the_provider(
    tenant_client, monkeypatch
):
    """The point of the feature: a covered window costs zero provider calls."""
    import app.call_digest as cd

    t = tenant_client.make(slug="acme")
    _connect_fireflies(t.company_id)
    monkeypatch.setattr(cd, "_load_api_key", lambda cid: "ff-key")
    store_call_transcripts(t.company_id, [_call("c1")])

    with patch.object(
        cd, "fetch_calls",
        side_effect=AssertionError("a covered window must not fetch live"),
    ):
        corpus = build_corpus(t.company_id, _window())

    assert corpus.status == "ok"
    assert "Pricing pushback." in corpus.text
    assert "the new tier is confusing" in corpus.text


def test_digest_live_fallback_writes_through(tenant_client, monkeypatch):
    """An uncovered window still fetches live — old behaviour — and persists
    what it fetched, so the next ask over the window reads the store."""
    import app.call_digest as cd

    t = tenant_client.make(slug="acme")
    _connect_fireflies(t.company_id)
    monkeypatch.setattr(cd, "_load_api_key", lambda cid: "ff-key")

    with patch.object(cd, "fetch_calls", return_value=[_call("c9")]) as fetched:
        corpus = build_corpus(t.company_id, _window())
    assert corpus.status == "ok"
    assert fetched.call_count == 1

    stored = load_call_transcripts(
        t.company_id, "2026-08-01T00:00:00+00:00", "2026-08-08T00:00:00+00:00"
    )
    assert [p["external_id"] for p in stored["fireflies"]] == ["c9"]
