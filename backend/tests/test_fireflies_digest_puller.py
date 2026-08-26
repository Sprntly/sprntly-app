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


def test_fetch_calls_paginates_past_the_api_page_cap():
    # Fireflies caps a transcripts query at 50 — a 30-day window with 70 calls
    # must be fetched across pages, not truncated to the first page.
    def page(i):
        return {**_T, "id": f"ff-{i}"}
    pages = [
        _resp([page(i) for i in range(50)]),          # full page → keep going
        _resp([page(i) for i in range(50, 70)]),      # short page → stop
    ]
    with patch("app.kg_ingest.pullers.fireflies.requests.post", side_effect=pages) as post:
        calls = fireflies.fetch_calls("key")
    assert len(calls) == 70
    assert calls[0].external_id == "ff-0" and calls[-1].external_id == "ff-69"
    skips = [c.kwargs["json"]["variables"]["skip"] for c in post.call_args_list]
    assert skips == [0, 50]


def test_fetch_calls_stops_at_the_digest_limit():
    # A runaway window can't loop forever: the overall cap bounds total calls.
    full = _resp([{**_T, "id": f"ff-{i}"} for i in range(50)])
    with patch("app.kg_ingest.pullers.fireflies.requests.post", return_value=full) as post:
        calls = fireflies.fetch_calls("key", limit=120)
    assert len(calls) == 120
    # Last page asked only for the remainder (120 - 100 = 20).
    assert post.call_args_list[-1].kwargs["json"]["variables"]["limit"] == 20


def test_render_max_quotes_trims_and_zero_drops_block():
    with patch("app.kg_ingest.pullers.fireflies.requests.post", return_value=_resp([_T])):
        c = fireflies.fetch_calls("key")[0]
    trimmed = c.render(max_quotes=1)
    assert 'CTO: "We can\'t roll out without SAML SSO."' in trimmed
    assert "Got it" not in trimmed
    summary_only = c.render(max_quotes=0)
    assert "verbatim quotes:" not in summary_only
    assert "summary: Acme wants SSO." in summary_only


def test_pull_requests_transcript_and_is_window_scoped():
    """The KG-ingest pull() now requests `sentences` so a transcript-only fact
    reaches extraction (parity with Zoom/Meet), and still forwards the window.

    This REVERSES the previous distilled-only assertion: §6 (no raw dump) is a
    PERSISTENCE contract — the transcript rides the transient `RawRecord.text`
    into one extraction call and is never written to a table (see runner /
    `_record_from`), so it is correct for the in-memory text to carry it."""
    since = datetime(2026, 6, 1, tzinfo=timezone.utc)
    with patch("app.kg_ingest.pullers.fireflies.requests.post", return_value=_resp([_T])) as post:
        records = list(fireflies.pull("key", since=since, limit=10))
    body = post.call_args.kwargs["json"]
    assert "sentences" in body["query"]
    # `skip` joined the variables on 2026-08-16. Without it this path could
    # never see past the API's 50-per-query ceiling, which is why the KG held
    # roughly three days of meetings however high the record cap was set. An
    # explicit `since` keeps the pull cursor-free, so page one starts at 0.
    assert body["variables"] == {
        "limit": 10, "skip": 0,
        "fromDate": "2026-06-01T00:00:00+00:00", "toDate": None,
    }
    assert records[0].provider == "fireflies" and records[0].kind == "meeting"
    # The transcript-only fact now reaches the extraction text.
    assert "SAML SSO" in records[0].text


def test_record_text_is_summary_first_then_full_transcript():
    """`_record_from` emits the distilled summary FIRST, then the FULL
    speaker-attributed transcript."""
    text = fireflies._record_from(_T).text
    # Summary leads; transcript follows.
    assert text.index("summary: Acme wants SSO.") < text.index("transcript:")
    assert "action items: Send SSO docs" in text
    # Speaker-attributed, same "{speaker}: {text}" shape Zoom/Meet render;
    # empty-text sentence dropped.
    assert "CTO: We can't roll out without SAML SSO." in text
    assert "PM: Got it, I'll scope it." in text


def test_deep_transcript_fact_beyond_4000_chars_is_retained():
    """The failure mode from live verify: a fact stated deep in a long call —
    well past the old 4000-char head window, near the tail — must now appear in
    `RawRecord.text`. Feed-full replaces head-truncation, so a USANA-scale call
    (the real one is ~74k chars) keeps its deep asks. This is a DELIBERATE
    contract change from the prior 4000-char bound."""
    # ~1000 filler sentences (~26k chars) push the real fact far past 4000.
    filler = [{"speaker_name": "Rep", "text": "Thanks, that all makes sense to me."}
              for _ in range(1000)]
    deep_fact = {"speaker_name": "Buyer",
                 "text": "One more thing — we really need a download button on the report page."}
    tail = [{"speaker_name": "Rep", "text": "Understood, noting that."}
            for _ in range(20)]
    call = {**_T, "sentences": filler + [deep_fact] + tail}
    text = fireflies._record_from(call).text
    offset = text.find("download button")
    assert offset > 4000, f"deep fact should sit well past 4000 chars, at {offset}"
    assert "download button" in text
    # Well under the ceiling → nothing truncated.
    assert len(text) < fireflies._TRANSCRIPT_CHAR_CEILING


def test_ceiling_trims_transcript_tail_but_keeps_summary():
    """The ONLY cap is the defensive `_TRANSCRIPT_CHAR_CEILING`: a pathological
    call that exceeds it is trimmed at the TAIL — the summary (emitted first)
    always survives, and the text is bounded to the ceiling."""
    ceiling = fireflies._TRANSCRIPT_CHAR_CEILING
    # Each sentence ~2k chars; enough of them to blow past the ceiling.
    huge = {**_T, "sentences": [{"speaker_name": "X", "text": "word " * 400}
                                for _ in range(ceiling // 2000 + 50)]}
    huge_text = fireflies._record_from(huge).text
    assert len(huge_text) == ceiling
    assert huge_text.startswith("summary: Acme wants SSO.")
    assert "action items: Send SSO docs" in huge_text


def test_record_without_sentences_is_summary_only():
    """A transcript with no `sentences` still builds a clean summary record —
    no dangling 'transcript:' header."""
    no_sent = {k: v for k, v in _T.items() if k != "sentences"}
    text = fireflies._record_from(no_sent).text
    assert "summary: Acme wants SSO." in text
    assert "transcript:" not in text
