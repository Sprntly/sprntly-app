"""Ingestion runner — RawRecords → extraction batches → KG (§1b pipeline).

Generic across providers: a puller yields RawRecords; the runner batches them
(by char budget) and routes each batch through the generic extractor. Signal
idempotency is content-keyed (uuid5), so re-syncs and shifting batches can't
duplicate. Error-isolated per batch — one bad batch never kills the sync.
"""
from __future__ import annotations

import json
import logging
from typing import Callable, Iterable

from app.graph.extractor import extract_document
from app.graph.facade import GraphFacade
from app.kg_ingest.pullers import clickup, fireflies, github, hubspot
from app.kg_ingest.types import RawRecord

logger = logging.getLogger(__name__)

_BATCH_CHAR_BUDGET = 6000

# provider → (puller fn, token_json key, source-type hint for the extractor)
PULLERS: dict[str, tuple[Callable[[str], Iterable[RawRecord]], str, str]] = {
    "clickup":   (clickup.pull,   "access_token", "project_mgmt (work items; classify bug/feature/fix)"),
    "hubspot":   (hubspot.pull,   "access_token", "revenue + support + customer_voice (deals: blockers/feature gaps; tickets: support pain/churn risk; notes/emails: voice-of-customer; owners: attribution; line items: revenue detail)"),
    "fireflies": (fireflies.pull, "api_key",      "customer_voice / communication (meeting transcripts)"),
    "github":    (github.pull,    "access_token", "engineering activity (PRs + commit messages; distilled ship signals — classify feature/fix/refactor, surface what's being built)"),
}


def _batches(records: list[RawRecord]) -> Iterable[list[RawRecord]]:
    batch: list[RawRecord] = []
    size = 0
    for r in records:
        rendered = len(r.render())
        if batch and size + rendered > _BATCH_CHAR_BUDGET:
            yield batch
            batch, size = [], 0
        batch.append(r)
        size += rendered
    if batch:
        yield batch


def token_for(provider: str, token_json: dict) -> str:
    """Pull the right credential field out of the decrypted token payload."""
    key = PULLERS[provider][1]
    value = token_json.get(key) or ""
    if not value:
        raise ValueError(f"connection for {provider!r} has no {key!r}")
    return value


def sync_provider(
    facade: GraphFacade,
    enterprise_id: str,
    provider: str,
    *,
    token: str,
    records: list[RawRecord] | None = None,
) -> dict:
    """Pull + extract one provider into the KG. Returns counts + errors."""
    if provider not in PULLERS:
        raise ValueError(f"No puller for provider {provider!r}")
    puller, _, hint = PULLERS[provider]

    if records is None:
        records = list(puller(token))

    totals = {"records": len(records), "batches": 0,
              "signals": 0, "themes": 0, "skipped": 0}
    errors: list[str] = []
    for i, batch in enumerate(_batches(records)):
        text = "\n\n".join(r.render() for r in batch)
        try:
            r = extract_document(
                facade, enterprise_id,
                doc_name=f"{provider}-sync-batch-{i}",
                text=text,
                agent=f"ingest:{provider}",
                source_hint=hint,
            )
            totals["batches"] += 1
            for k in ("signals", "themes", "skipped"):
                totals[k] += r[k]
        except Exception as e:  # noqa: BLE001 — error-isolation per batch
            logger.exception("extraction failed: %s batch %d", provider, i)
            errors.append(f"batch {i}: {e}")
    return {**totals, "errors": errors}
