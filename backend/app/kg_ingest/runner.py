"""Ingestion runner — RawRecords → extraction batches → KG (§1b pipeline).

Generic across providers: a puller yields RawRecords; the runner batches them
(by char budget) and routes each batch through the generic extractor. Signal
idempotency is content-keyed (uuid5), so re-syncs and shifting batches can't
duplicate. Error-isolated per batch — one bad batch never kills the sync.

COST GATE: pullers re-fetch everything on every sync, and the uuid5 dedup
only fires at the signal WRITE — after the LLM call was paid for. The runner
therefore keeps a per-record content-hash ledger (db.kg_ingest_ledger) and
extracts ONLY records not seen before; hashes are recorded per batch that
extracted successfully, so a failed batch is retried on the next sync. The
ledger is advisory and fails open — any ledger error degrades to extracting
everything, never to skipping unextracted data.
"""
from __future__ import annotations

import hashlib
import json
import logging
from datetime import datetime, timedelta, timezone
from typing import Callable, Iterable, Optional

from app.db.kg_ingest_ledger import record_hashes, seen_hashes
from app.graph.extractor import extract_document
from app.graph.facade import GraphFacade
from app.kg_ingest.pullers import (
    clickup,
    fireflies,
    github,
    gong,
    hubspot,
    jira,
    sprinklr,
    superset,
    uploads,
)
from app.kg_ingest.types import RawRecord

logger = logging.getLogger(__name__)

_BATCH_CHAR_BUDGET = 6000

# provider → (puller fn, token_json key, source-type hint for the extractor)
PULLERS: dict[str, tuple[Callable[[str], Iterable[RawRecord]], str, str]] = {
    "clickup":   (clickup.pull,   "access_token", "project_mgmt (work items; classify bug/feature/fix)"),
    "jira":      (jira.pull,      "access_token", "project_mgmt (issues: bugs/stories/tasks/epics with native type + status + priority)"),
    "hubspot":   (hubspot.pull,   "access_token", "revenue + support + customer_voice (deals: blockers/feature gaps; tickets: support pain/churn risk; notes/emails: voice-of-customer; owners: attribution; line items: revenue detail)"),
    "fireflies": (fireflies.pull, "api_key",      "customer_voice / communication (meeting transcripts)"),
    "gong":      (gong.pull,      "gong_credential", "customer_voice (sales & customer-success call recordings — Gong's distilled call briefs, key points, highlights: first-party voice-of-customer evidence from real customer conversations)"),
    "github":    (github.pull,    "access_token", "engineering activity (PRs + commit messages; distilled ship signals — classify feature/fix/refactor, surface what's being built)"),
    "sprinklr":  (sprinklr.pull,  "access_token", "customer_voice (CX cases: support pain/churn risk; inbound social messages/mentions: public voice-of-customer + market sentiment)"),
    "superset":  (superset.pull,  "superset_credential", "analytics (BI metadata: dashboards/charts/datasets/saved queries — the company's metrics vocabulary, what is measured and how it's organized)"),
    # No third party behind this one: the "credential" is the owning company id
    # and the puller reads the documents the user uploaded themselves. Each
    # record carries the user's own source name + description (see the puller).
    "uploads":   (uploads.pull,  "company_id",          "user-uploaded business documents (research, strategy, support exports, decks, spreadsheets — the user named and described this corpus; treat the source_name/source_description properties as authoritative context for what the text is)"),
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


# ── Incremental sync ─────────────────────────────────────────────────────────
#
# Some pullers cap how many records they fetch per sync (an API-cost bound).
# With a FIXED lookback window that cap becomes a coverage ceiling: a company
# with more calls in the window than the cap gets the same head re-fetched
# every cycle and never reaches the tail. For providers listed here the sync
# instead asks for "what landed since the last successful sync", so each cycle
# handles a small delta and the cap stops bounding coverage.
#
# Only providers whose pull() accepts a `since` kwarg belong here.
INCREMENTAL_PULLERS: frozenset[str] = frozenset({"gong"})

#: Incremental pulls re-ask for a little BEFORE the last sync. Providers
#: process recordings asynchronously (a Gong call brief can land hours after
#: the call itself), so a strict `since = last_sync_at` would permanently skip
#: anything that finished processing after its window had closed. The overlap
#: is free: re-fetched records hit the ledger and are dropped before any model
#: call.
_INCREMENTAL_OVERLAP = timedelta(days=2)


def incremental_since(last_sync_at: str | datetime | None) -> Optional[datetime]:
    """The `since` bound for an incremental pull, or None for a full window.

    None/unparseable last_sync_at ⇒ None ("never synced, or we can't tell" →
    the puller's own default lookback), so a bad timestamp degrades to MORE
    data, never less.
    """
    if not last_sync_at:
        return None
    if isinstance(last_sync_at, datetime):
        stamp = last_sync_at
    else:
        try:
            stamp = datetime.fromisoformat(str(last_sync_at).replace("Z", "+00:00"))
        except (TypeError, ValueError):
            logger.warning("ingest: unparseable last_sync_at %r", last_sync_at)
            return None
    if stamp.tzinfo is None:
        stamp = stamp.replace(tzinfo=timezone.utc)
    return stamp - _INCREMENTAL_OVERLAP


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
    last_sync_at: str | datetime | None = None,
) -> dict:
    """Pull + extract one provider into the KG. Returns counts + errors.

    `last_sync_at` (the connection's stamp) turns the pull INCREMENTAL for
    providers in INCREMENTAL_PULLERS — they fetch only what landed since
    then, minus a safety overlap. Everyone else pulls their default window
    exactly as before.
    """
    if provider not in PULLERS:
        raise ValueError(f"No puller for provider {provider!r}")
    puller, _, hint = PULLERS[provider]

    if records is None:
        since = (
            incremental_since(last_sync_at)
            if provider in INCREMENTAL_PULLERS
            else None
        )
        if since is not None:
            logger.info(
                "ingest: %s incremental pull since %s", provider, since.isoformat()
            )
            records = list(puller(token, since=since))
        else:
            records = list(puller(token))

    # Ledger gate: drop records whose exact rendering was already extracted
    # for this enterprise, BEFORE any model call. A changed record renders
    # differently → new hash → extracted again. Fail-open by construction:
    # seen_hashes returns {} on any error, so the sync degrades to extracting
    # everything (pre-ledger behavior) rather than skipping data.
    hashes = {id(r): _content_hash(r.render()) for r in records}
    seen = seen_hashes(enterprise_id, list(set(hashes.values())))
    fresh = [r for r in records if hashes[id(r)] not in seen]

    totals = {"records": len(records), "deduped": len(records) - len(fresh),
              "batches": 0, "signals": 0, "themes": 0, "skipped": 0}
    errors: list[str] = []
    for i, batch in enumerate(_batches(fresh)):
        text = "\n\n".join(r.render() for r in batch)
        try:
            r = extract_document(
                facade, enterprise_id,
                doc_name=f"{provider}-sync-batch-{i}",
                text=text,
                agent=f"ingest:{provider}",
                source_hint=hint,
                origin="connector",
                # The uploads "connector" is the user's own documents — the
                # same evidentiary class as manual uploads, so it keeps the
                # brief gate's upload-only relaxation (convergence counts
                # channel="upload" as upload evidence, mirroring #868's Drive
                # rationale).
                provenance_extra=(
                    {"channel": "upload"} if provider == "uploads" else None
                ),
            )
            totals["batches"] += 1
            for k in ("signals", "themes", "skipped"):
                totals[k] += r[k]
            # Only a batch that made it through extraction is recorded — a
            # failed batch keeps its hashes out of the ledger and is simply
            # re-extracted on the next sync.
            record_hashes(
                enterprise_id, provider, [hashes[id(rec)] for rec in batch]
            )
        except Exception as e:  # noqa: BLE001 — error-isolation per batch
            logger.exception("extraction failed: %s batch %d", provider, i)
            errors.append(f"batch {i}: {e}")
    return {**totals, "errors": errors}


def _content_hash(rendered: str) -> str:
    """Stable ledger key for one RawRecord rendering."""
    return hashlib.sha256(rendered.encode("utf-8", "replace")).hexdigest()
