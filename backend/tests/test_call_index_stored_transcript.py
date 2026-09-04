"""Single-call summaries (`app.call_index._summarize_calls`) reading a stored
transcript before the live provider fetch.

The single-call read path (`answer_single_call` -> `_summarize_calls`) used to
call the LIVE `fetch_transcript` on every ask, even for a call the digest's
own `call_transcripts` store already held — measured at ~12s of a ~23s total
response on prod. `call_digest.build_corpus` already answers a covered window
from that same store instead of re-fetching live (owner decision
2026-08-12); these tests pin the same pattern for the single-call path:

  * a stored transcript carrying full `sentences` answers without the live
    fetch (the latency win),
  * a store row that predates this cache — `call_digest`'s own writes, which
    hold only a bounded quote SAMPLE, never the full sentence-level
    transcript — is treated as a miss, not read as if it were complete (the
    fidelity bar: never trade a thinner answer for speed),
  * a miss still live-fetches and WARMS the store, so the next ask for the
    same call is stored-fast too, and
  * the warm write never leaves the row thinner than `call_digest` would
    have written for the same call (no cross-path regression to the digest's
    own quote sample).
"""
from __future__ import annotations

import time

import app.call_index as ci
from app.db.call_transcripts import load_call_transcript, store_call_transcripts
from app.kg_ingest.pullers.fireflies import CallTranscript


def _call(external_id: str = "c1", title: str = "Acme QBR") -> ci.IndexedCall:
    return ci.IndexedCall(
        external_id=external_id, title=title,
        call_date="2026-08-20T10:00:00+00:00", duration_min=30.0,
        participants=["ana@acme.com"], account="Acme", summary="",
    )


def _live_raw() -> dict:
    """What the live Fireflies fetch returns — full sentence-level content,
    the shape `render_transcript` and the summarizer both need."""
    return {
        "title": "Acme QBR",
        "summary": {"overview": "Quarterly review with Acme."},
        "sentences": [
            {"speaker_name": "Ana", "text": "We need SSO by end of Q3."},
            {"speaker_name": "Rep", "text": "Noted, I'll confirm the date."},
            {"speaker_name": "Ana", "text": "Also the dark mode toggle is broken."},
        ],
    }


def _stub_llm(monkeypatch, capture: dict | None = None):
    import app.graph.gateway as gw

    def _fake(**kwargs):
        if capture is not None:
            capture.update(kwargs)
        return type("R", (), {"output": "Acme wants SSO by Q3 and reported a dark mode bug."})()

    monkeypatch.setattr(gw, "llm_call", _fake)


def _fail_if_called(*a, **k):
    raise AssertionError("a covered call must not fetch the transcript live")


# ── AC1: a stored transcript with full sentences skips the live fetch ───────

def test_stored_transcript_with_sentences_skips_live_fetch(tenant_client, monkeypatch):
    t = tenant_client.make(slug="acme")
    call = _call()
    raw = _live_raw()
    store_call_transcripts(t.company_id, [CallTranscript(
        external_id=call.external_id, title=raw["title"], date=call.call_date,
        participants=call.participants, overview=raw["summary"]["overview"],
        quotes=[{"speaker": "Ana", "text": "We need SSO by end of Q3."}],
        sentences=raw["sentences"],
    )])
    monkeypatch.setattr(ci, "fetch_transcript", _fail_if_called)
    _stub_llm(monkeypatch)

    out = ci._summarize_calls(t.company_id, "summarize the Acme call", [call])

    assert out is not None
    assert out["_skill_source"] == "call-index-single"


def test_latency_before_after_on_a_covered_call(tenant_client, monkeypatch):
    """Before/after timing proof, on a local rig: stand in for the measured
    ~12s live Fireflies leg with a deterministic sleep (a real network call in
    a unit test would be both slow and flaky) and show the stored path pays
    none of it."""
    t = tenant_client.make(slug="acme")
    call = _call()
    raw = _live_raw()
    _stub_llm(monkeypatch)

    def _slow_fetch(company_id, external_id, **k):
        time.sleep(0.15)  # stands in for the measured ~12s live leg
        return raw

    # BEFORE: nothing stored — pays the "live" cost every time.
    monkeypatch.setattr(ci, "fetch_transcript", _slow_fetch)
    start = time.perf_counter()
    out_before = ci._summarize_calls(t.company_id, "summarize the Acme call", [call])
    before_elapsed = time.perf_counter() - start
    assert out_before is not None
    assert before_elapsed >= 0.15

    # AFTER: the call above warmed the store — the live leg is skipped now.
    monkeypatch.setattr(ci, "fetch_transcript", _fail_if_called)
    start = time.perf_counter()
    out_after = ci._summarize_calls(t.company_id, "summarize the Acme call", [call])
    after_elapsed = time.perf_counter() - start
    assert out_after is not None
    assert after_elapsed < before_elapsed / 2, (
        f"stored path ({after_elapsed:.4f}s) was not materially faster than "
        f"the live path ({before_elapsed:.4f}s)"
    )


# ── fidelity: the stored path must feed the model the SAME content ──────────

def test_stored_path_prompt_is_identical_to_the_live_path_prompt(tenant_client, monkeypatch):
    """Deterministic backstop for the fidelity bar: the exact text handed to
    the model is byte-identical whether it came from the live fetch or from a
    stored replay of the same content — so a real model given either sees the
    same call. (The real-LLM round trip on this same content lives in
    test_call_index_stored_transcript_live.py, gated on ANTHROPIC_API_KEY.)"""
    t = tenant_client.make(slug="acme")
    call = _call()
    raw = _live_raw()

    live_capture: dict = {}
    monkeypatch.setattr(ci, "fetch_transcript", lambda *a, **k: raw)
    _stub_llm(monkeypatch, live_capture)
    ci._summarize_calls(t.company_id, "summarize the Acme call", [call])
    live_input = live_capture["input"]

    # Second call for the SAME external_id now reads the store the first call
    # warmed — same content, different code path.
    stored_capture: dict = {}
    monkeypatch.setattr(ci, "fetch_transcript", _fail_if_called)
    _stub_llm(monkeypatch, stored_capture)
    ci._summarize_calls(t.company_id, "summarize the Acme call", [call])
    stored_input = stored_capture["input"]

    assert stored_input == live_input


# ── a digest-only row (no `sentences`) is a miss, not a thin hit ────────────

def test_digest_only_row_without_sentences_falls_through_to_live_fetch(
    tenant_client, monkeypatch
):
    """A row `call_digest` wrote before this cache existed carries a bounded
    quote SAMPLE, never the full transcript — reading it as usable would
    silently downgrade the summary. It must be treated exactly like no row at
    all."""
    t = tenant_client.make(slug="acme")
    call = _call()
    store_call_transcripts(t.company_id, [CallTranscript(
        external_id=call.external_id, title="Acme QBR", date=call.call_date,
        participants=call.participants, overview="Quarterly review.",
        quotes=[{"speaker": "Ana", "text": "We need SSO."}],
        # no `sentences` — exactly what call_digest.fetch_calls produces today
    )])
    fetched = []
    monkeypatch.setattr(
        ci, "fetch_transcript",
        lambda cid, ext, **k: fetched.append(ext) or _live_raw(),
    )
    _stub_llm(monkeypatch)

    out = ci._summarize_calls(t.company_id, "summarize the Acme call", [call])

    assert fetched == [call.external_id]
    assert out is not None


# ── AC3: a genuine miss live-fetches AND warms the store ────────────────────

def test_miss_live_fetches_and_warms_the_store(tenant_client, monkeypatch):
    t = tenant_client.make(slug="acme")
    call = _call()
    raw = _live_raw()
    fetched = []
    monkeypatch.setattr(
        ci, "fetch_transcript",
        lambda cid, ext, **k: fetched.append(ext) or raw,
    )
    _stub_llm(monkeypatch)

    out = ci._summarize_calls(t.company_id, "summarize the Acme call", [call])
    assert out is not None
    assert fetched == [call.external_id]  # live-fetched once

    stored = load_call_transcript(t.company_id, ci.PROVIDER_FIREFLIES, call.external_id)
    assert stored is not None
    assert stored["sentences"] == raw["sentences"]

    # The SECOND ask for the same call must not fetch live again.
    monkeypatch.setattr(ci, "fetch_transcript", _fail_if_called)
    out2 = ci._summarize_calls(t.company_id, "summarize the Acme call", [call])
    assert out2 is not None
    assert fetched == [call.external_id]  # unchanged — no second live fetch


def test_warm_write_preserves_quote_sample_a_digest_run_would_have_written(
    tenant_client, monkeypatch
):
    """The single-call path's write-through must never leave a row THINNER
    than `call_digest`'s own writes for the same call — it derives `quotes`
    the same bounded way `fireflies.fetch_calls` does, so a digest reading
    this warmed row later loses nothing."""
    t = tenant_client.make(slug="acme")
    call = _call()
    raw = _live_raw()
    monkeypatch.setattr(ci, "fetch_transcript", lambda *a, **k: raw)
    _stub_llm(monkeypatch)

    ci._summarize_calls(t.company_id, "summarize the Acme call", [call])

    stored = load_call_transcript(t.company_id, ci.PROVIDER_FIREFLIES, call.external_id)
    assert stored is not None
    assert stored["quotes"], "warm write dropped the verbatim quote sample"
    assert any("SSO" in q["text"] for q in stored["quotes"])
    assert stored["overview"] == "Quarterly review with Acme."
