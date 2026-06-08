"""Tests for audio→text transcription + raw-audio KG ingestion (task #25).

All external calls are mocked: no real OpenAI Whisper request is ever made.
"""
from __future__ import annotations

from unittest.mock import patch

import pytest


# ---------- transcribe_audio (mocked OpenAI HTTP) ----------

class _FakeResp:
    def __init__(self, status=200, body=None):
        self.status_code = status
        self._body = body or {}

    def raise_for_status(self):
        if self.status_code >= 400:
            raise RuntimeError(f"HTTP {self.status_code}")

    def json(self):
        return self._body


def test_transcribe_audio_posts_multipart_and_returns_text(monkeypatch):
    from app.kg_ingest import transcription

    monkeypatch.setattr(transcription.settings, "openai_api_key", "sk-test")
    captured = {}

    def fake_post(url, headers=None, files=None, data=None, timeout=None):
        captured["url"] = url
        captured["headers"] = headers
        captured["files"] = files
        captured["data"] = data
        return _FakeResp(body={"text": "Hello world.", "duration": 12.5,
                               "language": "english"})

    with patch.object(transcription.requests, "post", side_effect=fake_post):
        out = transcription.transcribe_audio(b"RIFFsomeaudio", "meeting.mp3")

    assert out["text"] == "Hello world."
    assert out["duration"] == 12.5
    assert out["language"] == "english"
    # multipart: file part present with filename + bytes; model + verbose_json set
    assert captured["url"].endswith("/v1/audio/transcriptions")
    assert captured["files"]["file"][0] == "meeting.mp3"
    assert captured["files"]["file"][1] == b"RIFFsomeaudio"
    assert captured["files"]["file"][2] == "audio/mpeg"      # mp3 content type
    assert captured["data"]["model"] == "whisper-1"
    assert captured["data"]["response_format"] == "verbose_json"
    assert captured["headers"]["Authorization"] == "Bearer sk-test"


def test_transcribe_audio_honors_model_override(monkeypatch):
    from app.kg_ingest import transcription

    monkeypatch.setattr(transcription.settings, "openai_api_key", "sk-test")
    captured = {}

    def fake_post(url, headers=None, files=None, data=None, timeout=None):
        captured["model"] = data["model"]
        return _FakeResp(body={"text": "x"})

    with patch.object(transcription.requests, "post", side_effect=fake_post):
        transcription.transcribe_audio(b"a", "a.wav", model="whisper-2")
    assert captured["model"] == "whisper-2"


def test_transcribe_audio_content_type_by_extension(monkeypatch):
    from app.kg_ingest import transcription

    monkeypatch.setattr(transcription.settings, "openai_api_key", "sk-test")
    seen = {}

    def fake_post(url, headers=None, files=None, data=None, timeout=None):
        seen["ct"] = files["file"][2]
        return _FakeResp(body={"text": "x"})

    with patch.object(transcription.requests, "post", side_effect=fake_post):
        transcription.transcribe_audio(b"a", "rec.m4a")
    assert seen["ct"] == "audio/mp4"


def test_transcribe_audio_missing_key_raises(monkeypatch):
    from app.kg_ingest import transcription

    monkeypatch.setattr(transcription.settings, "openai_api_key", "")
    with pytest.raises(RuntimeError, match="OPENAI_API_KEY"):
        transcription.transcribe_audio(b"audio", "x.mp3")


def test_transcribe_audio_empty_bytes_raises(monkeypatch):
    from app.kg_ingest import transcription

    monkeypatch.setattr(transcription.settings, "openai_api_key", "sk-test")
    with pytest.raises(ValueError, match="empty"):
        transcription.transcribe_audio(b"", "x.mp3")


def test_transcribe_audio_oversized_raises(monkeypatch):
    from app.kg_ingest import transcription

    monkeypatch.setattr(transcription.settings, "openai_api_key", "sk-test")
    big = b"x" * (transcription.MAX_AUDIO_BYTES + 1)
    with pytest.raises(ValueError, match="limit"):
        transcription.transcribe_audio(big, "x.mp3")


def test_transcribe_audio_retries_on_5xx(monkeypatch):
    from app.kg_ingest import transcription

    monkeypatch.setattr(transcription.settings, "openai_api_key", "sk-test")
    monkeypatch.setattr(transcription.time, "sleep", lambda *_: None)
    calls = {"n": 0}

    def flaky(url, headers=None, files=None, data=None, timeout=None):
        calls["n"] += 1
        if calls["n"] == 1:
            return _FakeResp(status=503)
        return _FakeResp(body={"text": "recovered", "duration": 1.0})

    with patch.object(transcription.requests, "post", side_effect=flaky):
        out = transcription.transcribe_audio(b"a", "x.mp3")
    assert calls["n"] == 2
    assert out["text"] == "recovered"


# ---------- ingest_audio (mocked transcribe + extract) ----------

def test_ingest_audio_wires_transcript_into_extractor(isolated_settings):
    from app.graph import GraphFacade
    from app.kg_ingest import audio_ingest

    captured = {}

    def fake_extract(f, eid, *, doc_name, text, agent, source_hint=None):
        captured["doc_name"] = doc_name
        captured["text"] = text
        captured["agent"] = agent
        captured["source_hint"] = source_hint
        captured["eid"] = eid
        return {"signals": 3, "themes": 1, "skipped": 0}

    fake_tx = {"text": "We need SSO before renewal.", "duration": 90.0,
               "language": "english"}
    with patch.object(audio_ingest, "transcribe_audio", return_value=fake_tx), \
         patch.object(audio_ingest, "extract_document", side_effect=fake_extract):
        out = audio_ingest.ingest_audio(
            GraphFacade(), "ent-A",
            audio_bytes=b"audio", filename="qbr.mp3", source="fireflies")

    assert out == {"signals": 3, "themes": 1, "skipped": 0, "duration": 90.0}
    assert captured["eid"] == "ent-A"
    assert captured["agent"] == "ingest:fireflies:audio"
    assert "customer_voice" in captured["source_hint"]
    # RawRecord shape rendered into extraction text: kind, transcript, metadata
    assert "meeting_transcript" in captured["text"]
    assert "We need SSO before renewal." in captured["text"]
    assert "duration=90.0" in captured["text"]
    assert "filename=qbr.mp3" in captured["text"]
    # idempotent doc name keyed on transcript content hash
    assert captured["doc_name"].startswith("fireflies-audio-")


def test_ingest_audio_idempotent_doc_name(isolated_settings):
    from app.graph import GraphFacade
    from app.kg_ingest import audio_ingest

    names = []

    def fake_extract(f, eid, *, doc_name, text, agent, source_hint=None):
        names.append(doc_name)
        return {"signals": 1, "themes": 0, "skipped": 0}

    fake_tx = {"text": "same content", "duration": 1.0, "language": "english"}
    with patch.object(audio_ingest, "transcribe_audio", return_value=fake_tx), \
         patch.object(audio_ingest, "extract_document", side_effect=fake_extract):
        audio_ingest.ingest_audio(GraphFacade(), "ent-A",
                                  audio_bytes=b"a", filename="a.mp3")
        audio_ingest.ingest_audio(GraphFacade(), "ent-A",
                                  audio_bytes=b"different-bytes", filename="b.wav")
    # same transcript content → same doc name regardless of file bytes/name
    assert names[0] == names[1]


def test_ingest_audio_empty_transcript_skips_extraction(isolated_settings):
    from app.graph import GraphFacade
    from app.kg_ingest import audio_ingest

    fake_tx = {"text": "   ", "duration": 5.0, "language": "english"}
    with patch.object(audio_ingest, "transcribe_audio", return_value=fake_tx), \
         patch.object(audio_ingest, "extract_document") as mock_extract:
        out = audio_ingest.ingest_audio(GraphFacade(), "ent-A",
                                        audio_bytes=b"a", filename="silent.mp3")
    mock_extract.assert_not_called()
    assert out == {"signals": 0, "themes": 0, "skipped": 0, "duration": 5.0}


# ---------- route (multipart upload, dep-override) ----------

def _override_company(main_mod, ingest_route):
    from app.auth import CompanyContext
    require_company = ingest_route.require_company
    main_mod.app.dependency_overrides[require_company] = lambda: CompanyContext(
        company_id="co-X", role="member", user_id="u1")
    return require_company


def test_audio_route_happy_path(isolated_settings):
    from fastapi.testclient import TestClient
    import app.main as main_mod
    import app.routes.ingest as ingest_route
    import app.kg_ingest.audio_ingest as audio_ingest

    require_company = _override_company(main_mod, ingest_route)
    try:
        with patch.object(audio_ingest, "ingest_audio",
                          return_value={"signals": 4, "themes": 2,
                                        "skipped": 1, "duration": 120.0}) as mock_ing:
            client = TestClient(main_mod.app)
            r = client.post(
                "/v1/ingest/audio",
                files={"file": ("meeting.mp3", b"RIFFaudiobytes", "audio/mpeg")},
                data={"source": "fireflies"},
            )
    finally:
        main_mod.app.dependency_overrides.pop(require_company, None)

    assert r.status_code == 200, r.text
    body = r.json()
    assert body["ok"] is True
    assert body["filename"] == "meeting.mp3"
    assert body["signals"] == 4 and body["duration"] == 120.0
    # the route passed the uploaded bytes + filename through
    _, kwargs = mock_ing.call_args
    assert kwargs["audio_bytes"] == b"RIFFaudiobytes"
    assert kwargs["filename"] == "meeting.mp3"
    assert kwargs["source"] == "fireflies"


def test_audio_route_rejects_empty_file(isolated_settings):
    from fastapi.testclient import TestClient
    import app.main as main_mod
    import app.routes.ingest as ingest_route

    require_company = _override_company(main_mod, ingest_route)
    try:
        client = TestClient(main_mod.app)
        r = client.post("/v1/ingest/audio",
                        files={"file": ("empty.mp3", b"", "audio/mpeg")})
    finally:
        main_mod.app.dependency_overrides.pop(require_company, None)
    assert r.status_code == 400


def test_audio_route_rejects_oversized_file(isolated_settings):
    from fastapi.testclient import TestClient
    import app.main as main_mod
    import app.routes.ingest as ingest_route

    require_company = _override_company(main_mod, ingest_route)
    big = b"x" * (ingest_route._MAX_AUDIO_BYTES + 1)
    try:
        client = TestClient(main_mod.app)
        r = client.post("/v1/ingest/audio",
                        files={"file": ("huge.mp3", big, "audio/mpeg")})
    finally:
        main_mod.app.dependency_overrides.pop(require_company, None)
    assert r.status_code == 413


def test_audio_route_missing_key_returns_503(isolated_settings):
    from fastapi.testclient import TestClient
    import app.main as main_mod
    import app.routes.ingest as ingest_route
    import app.kg_ingest.audio_ingest as audio_ingest

    require_company = _override_company(main_mod, ingest_route)
    try:
        with patch.object(audio_ingest, "ingest_audio",
                          side_effect=RuntimeError("OPENAI_API_KEY not configured")):
            client = TestClient(main_mod.app)
            r = client.post("/v1/ingest/audio",
                            files={"file": ("m.mp3", b"a", "audio/mpeg")})
    finally:
        main_mod.app.dependency_overrides.pop(require_company, None)
    assert r.status_code == 503
    assert "OPENAI_API_KEY" in r.json()["detail"]
