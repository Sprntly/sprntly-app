"""Embeddings via OpenAI (text-embedding-3-small, 1536d) — contract S2 `embed`.

Anthropic has no embeddings API; OpenAI was chosen 2026-05-28 (see
shared-contracts doc). Uses stdlib urllib — no new dependency. Tests patch
`embed_texts`.

Cost tracking: OpenAI returns a `usage.prompt_tokens` count per call. When the
caller passes its tenant (`enterprise_id`) and the call site's `purpose`, the
token count + estimated USD cost are (a) emitted on the canonical
`log_llm_run` grep line and (b) appended to `agent_decision_log`
(decision_type="embedding") — the SAME per-tenant audit spine every Anthropic
call already writes to, so embedding spend is queryable per-company and, via
`factors.purpose`, per-feature. Pricing lives in `MODEL_PRICING`. Telemetry is
best-effort: any logging failure is swallowed so it can never break embedding,
which sits on the KG ingest + Ask retrieval hot paths.
"""
from __future__ import annotations

import json
import logging
import time
import urllib.error
import urllib.request

from app.config import settings
from app.llm_telemetry import MODEL_PRICING, RunUsage, log_llm_run

logger = logging.getLogger(__name__)

EMBEDDING_MODEL = "text-embedding-3-small"
EMBEDDING_DIM = 1536
_URL = "https://api.openai.com/v1/embeddings"
_MAX_ATTEMPTS = 3


def _log_embedding_usage(
    usage_obj: dict | None,
    model: str,
    enterprise_id: str | None,
    purpose: str,
    duration_ms: int,
) -> None:
    """Record token usage + estimated cost for one embeddings call.

    Best-effort and fully guarded: telemetry must NEVER break embedding (KG
    ingest + Ask retrieval hot paths), so every failure is swallowed + logged.
    Always emits the canonical grep line; additionally writes a per-tenant,
    per-feature row to `agent_decision_log` when `enterprise_id` is supplied.
    """
    try:
        usage_obj = usage_obj or {}
        prompt_tokens = int(usage_obj.get("prompt_tokens")
                            or usage_obj.get("total_tokens") or 0)
        run = RunUsage(input_tokens=prompt_tokens)
        cost = run.est_cost_usd(model) if model in MODEL_PRICING else 0.0
        # Canonical grep line — one shape across every LLM call site.
        log_llm_run(
            operation="embeddings.embed",
            identifier={"enterprise_id": enterprise_id or ""},
            usage=run, duration_ms=duration_ms, status="complete",
            model=model, purpose=purpose,
        )
        # Per-tenant audit/billing row (same table as every Anthropic call).
        # purpose lands in factors so spend is attributable per feature.
        if enterprise_id:
            from app.graph.decision_log import log_agent_decision

            log_agent_decision(
                enterprise_id=enterprise_id,
                agent="embeddings",
                decision_type="embedding",
                factors={
                    "purpose": purpose,
                    "input_tokens": prompt_tokens,
                    "cost_usd": round(cost, 6),
                },
                model=model,
            )
    except Exception:  # noqa: BLE001 — telemetry must never break embedding
        logger.exception("embedding usage logging failed (continuing)")


def embed_texts(
    texts: list[str],
    model: str = EMBEDDING_MODEL,
    *,
    enterprise_id: str | None = None,
    purpose: str = "embed",
) -> list[list[float]]:
    """Embed a batch of texts. Raises RuntimeError if OPENAI_API_KEY is missing.

    Pass `enterprise_id` (the tenant/company id) and `purpose` (the calling
    feature, e.g. "kg_extract", "kg_retrieval") to attribute token usage + cost
    per-company and per-feature in `agent_decision_log`. Omitting them keeps the
    old behaviour plus a grep-only cost line (no per-tenant row). The
    no-API-key zero-vector fallback records nothing — no real spend occurred.
    """
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
            t0 = time.monotonic()
            with urllib.request.urlopen(req, timeout=60) as resp:
                data = json.loads(resp.read())
            _log_embedding_usage(
                data.get("usage"), model, enterprise_id, purpose,
                int((time.monotonic() - t0) * 1000),
            )
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
