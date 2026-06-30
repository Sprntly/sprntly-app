"""Fireflies puller — window-scoped pull() + on-demand fetch_calls() with quotes.

Patches `requests.post` in the puller namespace so no network call is made.
"""
from __future__ import annotations

from datetime import datetime, timezone
from unittest.mock import MagicMock, patch

from app.kg_ingest.pullers import fireflies


def _resp(transcripts):
    m = MagicMock()
    m.raise_for_status.return_value = None
    m.json.return_value = {"data": {"transcripts": transcripts}}
    return m


# date is epoch-ms (Fireflies returns Float); 1_750_000_000_000 ≈ 2025-06-15.
_T = {
    "id": "ff-1",
    "title": "QBR — Acme",
    "date": 1_750_000_000_000,
    "participants": ["pm@us.com", "cto@acme.com"],
    "summary": {"overview": "Acme wants SSO.", "action_items": "Send SSO docs",
                "keywords": ["sso", "security"]},
    "sentences": [
        {"speaker_name": "CTO", "text": "We can't roll out without SAML SSO."},
        {"speaker_name": "PM", "text": "Got it, I'll scope it."},
        {"speaker_name": "CTO", "text": ""},  # empty → dropped
    ],
}


def test_fetch_calls_returns_transcripts_with_quotes():
    with patch("app.kg_ingest.pullers.fireflies.requests.post", return_value=_resp([_T])):
        calls = fireflies.fetch_calls("key")
    assert len(calls) == 1
    c = calls[0]
    assert c.external_id == "ff-1"
    assert c.overview == "Acme wants SSO."
    assert c.keywords == ["sso", "security"]
    # Empty-text sentence dropped; quotes are speaker-attributed.
    assert c.quotes == [
        {"speaker": "CTO", "text": "We can't roll out without SAML SSO."},
        {"speaker": "PM", "text": "Got it, I'll scope it."},
    ]
    # Epoch-ms date normalized to ISO.
    assert c.date.startswith("2025-06-15")


def test_fetch_calls_passes_window_and_requests_sentences():
    since = datetime(2026, 6, 22, tzinfo=timezone.utc)
    until = datetime(2026, 6, 29, tzinfo=timezone.utc)
    with patch("app.kg_ingest.pullers.fireflies.requests.post", return_value=_resp([])) as post:
        out = fireflies.fetch_calls("key", since=since, until=until)
    assert out == []
    body = post.call_args.kwargs["json"]
    assert body["variables"]["fromDate"] == "2026-06-22T00:00:00+00:00"
    assert body["variables"]["toDate"] == "2026-06-29T00:00:00+00:00"
    # Digest query asks for verbatim sentences; KG-ingest query must not.
    assert "sentences" in body["query"]


def test_render_includes_quotes_and_source_line():
    with patch("app.kg_ingest.pullers.fireflies.requests.post", return_value=_resp([_T])):
        c = fireflies.fetch_calls("key")[0]
    rendered = c.render()
    assert "## Call: QBR — Acme" in rendered
    assert "participants: pm@us.com, cto@acme.com" in rendered
    assert 'CTO: "We can\'t roll out without SAML SSO."' in rendered


def test_pull_is_distilled_only_and_window_scoped():
    """The KG-ingest pull() must NOT request sentences (no raw-dump §6) and must
    forward the window to the API."""
    since = datetime(2026, 6, 1, tzinfo=timezone.utc)
    with patch("app.kg_ingest.pullers.fireflies.requests.post", return_value=_resp([_T])) as post:
        records = list(fireflies.pull("key", since=since, limit=10))
    body = post.call_args.kwargs["json"]
    assert "sentences" not in body["query"]
    assert body["variables"] == {"limit": 10, "fromDate": "2026-06-01T00:00:00+00:00", "toDate": None}
    assert records[0].provider == "fireflies" and records[0].kind == "meeting"
    # No verbatim quotes leak into the persisted record.
    assert "SAML SSO" not in records[0].text
