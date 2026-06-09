"""Embeddings via OpenAI (text-embedding-3-small, 1536d) — contract S2 `embed`.

Anthropic has no embeddings API; OpenAI was chosen 2026-05-28 (see
shared-contracts doc). Uses stdlib urllib — no new dependency. Tests patch
`embed_texts`.
"""
from __future__ import annotations

import json
import logging
import time
import urllib.error
import urllib.request

from app.config import settings

logger = logging.getLogger(__name__)

EMBEDDING_MODEL = "text-embedding-3-small"
EMBEDDING_DIM = 1536
_URL = "https://api.openai.com/v1/embeddings"
_MAX_ATTEMPTS = 3


def embed_texts(texts: list[str], model: str = EMBEDDING_MODEL) -> list[list[float]]:
    """Embed a batch of texts. Raises RuntimeError if OPENAI_API_KEY is missing."""
    if not texts:
        return []
    key = getattr(settings, "openai_api_key", "")
    if not key:
        logger.warning("OPENAI_API_KEY not configured — returning zero vectors "
                       "(KG search will be degraded until a key is set)")
        return [[0.0] * EMBEDDING_DIM for _ in texts]
    body = json.dumps({"model": model, "input": texts}).encode()
    last: Exception | None = None
    for attempt in range(_MAX_ATTEMPTS):
        req = urllib.request.Request(
            _URL, data=body, method="POST",
            headers={"Authorization": f"Bearer {key}",
                     "Content-Type": "application/json"},
        )
        try:
            with urllib.request.urlopen(req, timeout=60) as resp:
                data = json.loads(resp.read())
            return [d["embedding"] for d in data["data"]]
        except urllib.error.HTTPError as e:
            if e.code in (429, 500, 502, 503) and attempt < _MAX_ATTEMPTS - 1:
                delay = 0.5 * (4 ** attempt)
                logger.warning("OpenAI embeddings %s; retrying in %.1fs", e.code, delay)
                time.sleep(delay)
                last = e
                continue
            raise
        except urllib.error.URLError as e:
            if attempt < _MAX_ATTEMPTS - 1:
                time.sleep(0.5 * (4 ** attempt))
                last = e
                continue
            raise
    raise last  # pragma: no cover
