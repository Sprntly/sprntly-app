"""Thin helper for uploading a session's CSV to the Anthropic Files API.

There are no application-defined chat tools anymore — the analysis is
driven by Claude's server-side `code_execution` tool. This module just
exists so the upload happens once per file (instead of on every chat
turn) and so the file_id lives on `SessionState`.
"""

from __future__ import annotations

import os
from pathlib import Path

from anthropic import Anthropic


_client: Anthropic | None = None


def _get_client() -> Anthropic:
    global _client
    if _client is None:
        key = os.environ.get("ANTHROPIC_API_KEY")
        if not key:
            raise RuntimeError("ANTHROPIC_API_KEY is not set.")
        _client = Anthropic(api_key=key)
    return _client


def upload_csv(path: str | Path, filename: str | None = None) -> str:
    """Push the CSV at `path` to Anthropic's Files API. Returns the file_id.

    The same file_id can be referenced via a `container_upload` content
    block on subsequent `messages.create` calls; the file is loaded into
    the code-execution sandbox at a path Claude discovers on read.
    """
    p = Path(path)
    if not p.exists():
        raise FileNotFoundError(p)
    client = _get_client()
    label = filename or p.name
    with p.open("rb") as fh:
        uploaded = client.beta.files.upload(
            file=(label, fh, "text/csv"),
        )
    return uploaded.id


def delete_file(file_id: str) -> None:
    """Best-effort cleanup. Swallows errors — a failed delete is not user-visible."""
    try:
        _get_client().beta.files.delete(file_id)
    except Exception:
        pass
