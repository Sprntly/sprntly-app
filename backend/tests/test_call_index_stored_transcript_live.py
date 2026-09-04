"""Real local-Supabase + real-Anthropic round trip for the single-call
stored-transcript summary path (`app.call_index._summarize_calls`) — the
fidelity half the deterministic suite
(`test_call_index_stored_transcript.py`) cannot close: a stubbed LLM proves
the stored path skips the live fetch and feeds the model byte-identical
input, never that a REAL model's summary produced from a stored transcript
actually surfaces the same key facts a live-fetch summary would.

Deliberately does NOT use the `tenant_client`/`isolated_settings` fixtures —
those force `ANTHROPIC_API_KEY` to a dummy value and swap in the in-memory
fake Supabase client precisely so no ordinary test can hit either real
service. This suite wants both, real, so it drives `app.db.client.
require_client()` and the model straight through whatever `app.config.
settings` resolves to from the process environment, against an EXISTING real
company on the local rig (never a fabricated id — a fresh `call_transcripts`
row needs a genuine `companies.id` to satisfy its FK) and cleans up exactly
the row it wrote.

One live-model smoke test. The rest of the fidelity/latency/miss-path
coverage is deterministic and runs in the fast lane
(`test_call_index_stored_transcript.py`). Registered in
`test_ci_lane_coverage.py::_KNOWN_UNRUNNABLE` under `ANTHROPIC_API_KEY`.

    ANTHROPIC_API_KEY=... SUPABASE_URL=http://127.0.0.1:54321 \\
    SUPABASE_SERVICE_ROLE_KEY=... \\
        pytest tests/test_call_index_stored_transcript_live.py -m integration
"""
from __future__ import annotations

import os

import pytest

import app.call_index as ci
from app.db.call_transcripts import store_call_transcripts
from app.kg_ingest.pullers.fireflies import CallTranscript

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(not os.getenv("ANTHROPIC_API_KEY"), reason="needs live model"),
]


def _sb():
    from app.config import settings
    from supabase import create_client

    url = settings.supabase_url
    assert "127.0.0.1" in url or "localhost" in url, (
        f"refusing to run the live single-call round-trip against a "
        f"non-loopback SUPABASE_URL ({url!r}) — this test writes real rows"
    )
    assert settings.supabase_service_role_key, "SUPABASE_SERVICE_ROLE_KEY is not set"
    return create_client(url, settings.supabase_service_role_key)


@pytest.fixture
def real_company_id():
    sb = _sb()
    rows = sb.table("companies").select("id").limit(1).execute().data
    assert rows, "no company row on the local rig — seed one before running this suite"
    return rows[0]["id"]


_EXTERNAL_ID = "live-single-call-stored-transcript-probe"

# Two distinct, checkable facts — one a commitment/blocker, one a bug report —
# standing in for the kind of content a real Fireflies transcript carries.
_SENTENCES = [
    {
        "speaker_name": "Ana",
        "text": "We absolutely need SAML-based SSO live before our Q3 "
                "renewal, or we walk.",
    },
    {
        "speaker_name": "Rep",
        "text": "Understood — I'll get engineering to commit to a date "
                "this week.",
    },
    {
        "speaker_name": "Ana",
        "text": "Separately, the dark mode toggle in Settings has been "
                "broken since the last release; it does nothing when "
                "clicked.",
    },
]


def _call() -> ci.IndexedCall:
    return ci.IndexedCall(
        external_id=_EXTERNAL_ID, title="Acme QBR",
        call_date="2026-08-20T10:00:00+00:00", duration_min=30.0,
        participants=["ana@acme.com"], account="Acme", summary="",
    )


def test_stored_transcript_summary_surfaces_the_same_facts_a_live_fetch_would(
    real_company_id, monkeypatch
):
    """Seeds the REAL store with the SAME content a live fetch would have
    returned, forces the stored-first path (a live fetch here fails the
    test), and asserts the REAL model's summary carries the two substantive
    facts the transcript actually contains. Equivalent-quality bar: a summary
    answered from the store must read like one from a live fetch, not a
    degraded stand-in."""
    company_id = real_company_id
    call = _call()
    sb = _sb()
    try:
        store_call_transcripts(company_id, [CallTranscript(
            external_id=call.external_id, title=call.title, date=call.call_date,
            participants=call.participants, overview="Quarterly review with Acme.",
            sentences=_SENTENCES,
        )])

        def _fail_if_called(*a, **k):
            raise AssertionError(
                "a covered call must not fetch the transcript live"
            )

        monkeypatch.setattr(ci, "fetch_transcript", _fail_if_called)

        out = ci._summarize_calls(company_id, "summarize the Acme call", [call])

        assert out is not None
        answer = out["answer"].lower()
        assert "sso" in answer or "saml" in answer, out["answer"]
        assert "dark mode" in answer or "dark-mode" in answer, out["answer"]
    finally:
        sb.table("call_transcripts").delete().eq("company_id", company_id).eq(
            "provider", "fireflies"
        ).eq("external_id", _EXTERNAL_ID).execute()
