"""Audio → text transcription via OpenAI Whisper (task #25).

Anthropic has no audio-transcription API; OpenAI is already the project's
embeddings provider (same OPENAI_API_KEY, see app/graph/embeddings.py), so we
reuse it here. This module is provider-agnostic from the caller's view: it takes
audio bytes and returns {text, duration?, language?}.

We POST multipart/form-data to the OpenAI transcriptions endpoint with
response_format=verbose_json so the response carries duration + language (and
segments, which we don't persist — see audio_ingest for the data-minimization
note). Retries on 429/5xx mirror the embeddings backoff.
"""
from __future__ import annotations

import logging
import time

import requests

from app.config import settings

logger = logging.getLogger(__name__)

WHISPER_MODEL = "whisper-1"
_URL = "https://api.openai.com/v1/audio/transcriptions"
_MAX_ATTEMPTS = 3
_TIMEOUT = 300  # transcription is slower than embeddings; allow a few minutes
# OpenAI's hard limit on the transcriptions endpoint is 25 MB per request.
MAX_AUDIO_BYTES = 25 * 1024 * 1024

# Best-effort content types so the multipart part is well-formed; OpenAI keys
# off the filename extension, so an exact match isn't required.
_CONTENT_TYPES = {
    "mp3": "audio/mpeg",
    "m4a": "audio/mp4",
    "mp4": "audio/mp4",
    "wav": "audio/wav",
    "webm": "audio/webm",
    "ogg": "audio/ogg",
    "flac": "audio/flac",
}


def _content_type(filename: str) -> str:
    ext = filename.rsplit(".", 1)[-1].lower() if "." in filename else ""
    return _CONTENT_TYPES.get(ext, "application/octet-stream")


def transcribe_audio(
    audio_bytes: bytes,
    filename: str,
    *,
    model: str = WHISPER_MODEL,
) -> dict:
    """Transcribe audio to text via OpenAI Whisper.

    Returns {"text": str, "duration": float | None, "language": str | None}.
    Raises RuntimeError if OPENAI_API_KEY is missing, ValueError if the audio is
    empty or exceeds Whisper's 25 MB request limit.
    """
    if not audio_bytes:
        raise ValueError("audio_bytes is empty")
    if len(audio_bytes) > MAX_AUDIO_BYTES:
        raise ValueError(
            f"audio is {len(audio_bytes)} bytes; exceeds Whisper's "
            f"{MAX_AUDIO_BYTES}-byte per-request limit (chunk longer files)"
        )
    key = getattr(settings, "openai_api_key", "")
    if not key:
        raise RuntimeError("OPENAI_API_KEY not configured — transcription unavailable")

    files = {"file": (filename, audio_bytes, _content_type(filename))}
    data = {"model": model, "response_format": "verbose_json"}
    headers = {"Authorization": f"Bearer {key}"}

    last: Exception | None = None
    for attempt in range(_MAX_ATTEMPTS):
        try:
            resp = requests.post(
                _URL, headers=headers, files=files, data=data, timeout=_TIMEOUT
            )
        except requests.RequestException as e:
            if attempt < _MAX_ATTEMPTS - 1:
                time.sleep(0.5 * (4 ** attempt))
                last = e
                continue
            raise
        if resp.status_code in (429, 500, 502, 503) and attempt < _MAX_ATTEMPTS - 1:
            delay = 0.5 * (4 ** attempt)
            logger.warning("OpenAI transcription %s; retrying in %.1fs",
                           resp.status_code, delay)
            time.sleep(delay)
            last = RuntimeError(f"transcription HTTP {resp.status_code}")
            continue
        resp.raise_for_status()
        body = resp.json()
        return {
            "text": (body.get("text") or "").strip(),
            "duration": body.get("duration"),
            "language": body.get("language"),
        }
    raise last  # pragma: no cover
