"""HTTP client for the Sprntly backend internal API.

The DS Agent uses this to:
  - pull corpus data for a dataset (knowledge base context)
  - query which input sources are enabled per enterprise
  - push analysis results back so they enter the corpus
"""
from __future__ import annotations

import logging
import os
from typing import Any

import httpx

logger = logging.getLogger(__name__)

_BACKEND_URL = os.environ.get("BACKEND_URL", "http://127.0.0.1:8000")
_INTERNAL_KEY = os.environ.get("BACKEND_INTERNAL_KEY", "")
_TIMEOUT = 30  # seconds


class BackendError(Exception):
    """Raised when a backend call fails."""

    def __init__(self, status: int, detail: str) -> None:
        self.status = status
        self.detail = detail
        super().__init__(f"Backend {status}: {detail}")


def _headers() -> dict[str, str]:
    return {"X-Internal-Key": _INTERNAL_KEY}


def fetch_corpus(dataset_slug: str) -> dict[str, Any]:
    """GET /internal/corpus/{slug} — returns corpus docs + joined text."""
    url = f"{_BACKEND_URL}/internal/corpus/{dataset_slug}"
    resp = httpx.get(url, headers=_headers(), timeout=_TIMEOUT)
    if resp.status_code != 200:
        raise BackendError(resp.status_code, resp.text)
    return resp.json()


def fetch_input_sources(dataset_slug: str) -> list[dict[str, Any]]:
    """GET /internal/datasets/{slug}/input-sources."""
    url = f"{_BACKEND_URL}/internal/datasets/{dataset_slug}/input-sources"
    resp = httpx.get(url, headers=_headers(), timeout=_TIMEOUT)
    if resp.status_code != 200:
        raise BackendError(resp.status_code, resp.text)
    return resp.json().get("input_sources", [])


def push_analysis(
    dataset_slug: str,
    filename: str,
    markdown: str,
    source: str = "ds-agent",
) -> dict[str, Any]:
    """POST /internal/datasets/{slug}/ingest-analysis — write analysis to corpus."""
    url = f"{_BACKEND_URL}/internal/datasets/{dataset_slug}/ingest-analysis"
    body = {"source": source, "filename": filename, "markdown": markdown}
    resp = httpx.post(url, json=body, headers=_headers(), timeout=_TIMEOUT)
    if resp.status_code != 200:
        raise BackendError(resp.status_code, resp.text)
    return resp.json()
