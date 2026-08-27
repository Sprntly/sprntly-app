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
    """The KG-ingest pull() still requests `sentences` — but Config B
    (2026-08-26) means that transcript-only fact reaches the directed-
    checklist pass's `checklist_text`, NOT the main pass's `.text`, which
    is now digest-only. §6 (no raw dump) is a PERSISTENCE contract either
    way: the transcript rides a transient in-memory field into one LLM
    call and is never written to a table (see runner / `_record_from`)."""
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
    # The transcript-only fact reaches the CHECKLIST text, not the main-pass
    # text — Config B's whole point.
    assert "SAML SSO" not in records[0].text
    assert "SAML SSO" in records[0].checklist_text


def test_kg_query_requests_the_free_richer_digest_fields():
    """The richer Fireflies digest fields (gist/outline/topics_discussed/
    tasks/questions) cost nothing extra — same API call — and enrich the
    main pass's condensed input for better themes."""
    for field in ("gist", "outline", "topics_discussed", "tasks", "questions"):
        assert field in fireflies._QUERY_KG_WITH_TRANSCRIPT, (
            f"KG-ingest query should request the free {field!r} digest field")


def test_main_pass_text_is_digest_only_even_with_sentences():
    """Config B's central invariant: `.text` (the main open-extraction pass's
    input) is digest-only — summary + action items — and NEVER contains the
    transcript block, even when `sentences` are present. This is what makes
    the main pass cheap; the full transcript now reaches ONLY
    `checklist_text`."""
    rec = fireflies._record_from(_T)
    assert "summary: Acme wants SSO." in rec.text
    assert "action items: Send SSO docs" in rec.text
    assert "transcript:" not in rec.text
    assert "SAML SSO" not in rec.text


def test_richer_free_digest_fields_enrich_the_main_pass_text():
    """When Fireflies returns the richer free digest fields, they fold into
    the CONDENSED main-pass `.text` (for better themes) — cheap because
    it's the same API call, no LLM spend."""
    rich = {**_T, "summary": {**_T["summary"],
                              "gist": "Acme is blocked on SSO before rollout.",
                              "outline": "1. SSO gap 2. Timeline",
                              "topics_discussed": ["SSO", "timeline"],
                              "tasks": ["Send SSO docs to Acme"],
                              "questions": ["When can SSO ship?"]}}
    text = fireflies._record_from(rich).text
    assert "gist: Acme is blocked on SSO before rollout." in text
    assert "outline: 1. SSO gap 2. Timeline" in text
    assert "topics discussed: SSO; timeline" in text
    assert "tasks: Send SSO docs to Acme" in text
    assert "questions: When can SSO ship?" in text
    # Still digest-only — enrichment doesn't smuggle the transcript in.
    assert "transcript:" not in text


def test_record_checklist_text_is_summary_first_then_full_transcript():
    """`_record_from` builds `checklist_text` as the distilled summary
    FIRST, then the FULL speaker-attributed transcript — unchanged shape
    from before Config B, just relocated off the main-pass `.text`."""
    checklist_text = fireflies._record_from(_T).checklist_text
    # Summary leads; transcript follows.
    assert checklist_text.index("summary: Acme wants SSO.") < checklist_text.index("transcript:")
    assert "action items: Send SSO docs" in checklist_text
    # Speaker-attributed, same "{speaker}: {text}" shape Zoom/Meet render;
    # empty-text sentence dropped.
    assert "CTO: We can't roll out without SAML SSO." in checklist_text
    assert "PM: Got it, I'll scope it." in checklist_text


def test_deep_transcript_fact_beyond_4000_chars_is_retained_in_checklist_text():
    """The failure mode from live verify: a fact stated deep in a long call —
    well past the old 4000-char head window, near the tail — must now appear
    in `RawRecord.checklist_text` (the directed-checklist pass's input).
    Feed-full replaces head-truncation, so a real long-briefing-scale call
    (a real one is ~74k chars) keeps its deep asks."""
    # ~1000 filler sentences (~26k chars) push the real fact far past 4000.
    filler = [{"speaker_name": "Rep", "text": "Thanks, that all makes sense to me."}
              for _ in range(1000)]
    deep_fact = {"speaker_name": "Buyer",
                 "text": "One more thing — we really need a download button on the report page."}
    tail = [{"speaker_name": "Rep", "text": "Understood, noting that."}
            for _ in range(20)]
    call = {**_T, "sentences": filler + [deep_fact] + tail}
    checklist_text = fireflies._record_from(call).checklist_text
    offset = checklist_text.find("download button")
    assert offset > 4000, f"deep fact should sit well past 4000 chars, at {offset}"
    assert "download button" in checklist_text
    # Well under the ceiling → nothing truncated.
    assert len(checklist_text) < fireflies._TRANSCRIPT_CHAR_CEILING
    # The main pass never sees it — Config B's point.
    assert "download button" not in fireflies._record_from(call).text


def test_ceiling_trims_checklist_text_tail_but_keeps_summary():
    """The ONLY cap is the defensive `_TRANSCRIPT_CHAR_CEILING`: a
    pathological call's `checklist_text` that exceeds it is trimmed at the
    TAIL — the summary (emitted first) always survives, and the text is
    bounded to the ceiling. The main-pass `.text` is untouched by the
    ceiling — it's always the small digest."""
    ceiling = fireflies._TRANSCRIPT_CHAR_CEILING
    # Each sentence ~2k chars; enough of them to blow past the ceiling.
    huge = {**_T, "sentences": [{"speaker_name": "X", "text": "word " * 400}
                                for _ in range(ceiling // 2000 + 50)]}
    rec = fireflies._record_from(huge)
    assert len(rec.checklist_text) == ceiling
    assert rec.checklist_text.startswith("summary: Acme wants SSO.")
    assert "action items: Send SSO docs" in rec.checklist_text
    assert rec.text == "summary: Acme wants SSO.\naction items: Send SSO docs"


def test_record_without_sentences_falls_back_to_digest_only_checklist_text():
    """A transcript with no `sentences` still builds a clean digest-only
    record on BOTH fields — no dangling 'transcript:' header on either, and
    `checklist_text` degrades gracefully to the digest rather than being
    empty."""
    no_sent = {k: v for k, v in _T.items() if k != "sentences"}
    rec = fireflies._record_from(no_sent)
    assert "summary: Acme wants SSO." in rec.text
    assert "transcript:" not in rec.text
    assert rec.checklist_text == rec.text
    assert "transcript:" not in rec.checklist_text
