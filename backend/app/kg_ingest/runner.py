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
import inspect
import json
import logging
from datetime import datetime, timezone
from typing import Callable, Iterable

from app.db.kg_ingest_ledger import record_hashes, seen_hashes
from app.graph.extractor import extract_document
from app.graph.facade import GraphFacade
from app.kg_ingest.pullers import (
    clickup,
    confluence,
    fireflies,
    github,
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
    "github":    (github.pull,    "access_token", "engineering activity (PRs + commit messages; distilled ship signals — classify feature/fix/refactor, surface what's being built)"),
    "sprinklr":  (sprinklr.pull,  "access_token", "customer_voice (CX cases: support pain/churn risk; inbound social messages/mentions: public voice-of-customer + market sentiment)"),
    "superset":  (superset.pull,  "superset_credential", "analytics (BI metadata: dashboards/charts/datasets/saved queries — the company's metrics vocabulary, what is measured and how it's organized)"),
    # No third party behind this one: the "credential" is the owning company id
    # and the puller reads the documents the user uploaded themselves. Each
    # record carries the user's own source name + description (see the puller).
    "uploads":   (uploads.pull,  "company_id",          "user-uploaded business documents (research, strategy, support exports, decks, spreadsheets — the user named and described this corpus; treat the source_name/source_description properties as authoritative context for what the text is)"),
    # Like uploads, the "credential" is the company id — a Confluence pull
    # needs the site id and the selected spaces off the connection row, and
    # token_for can only hand a puller one field.
    "confluence": (confluence.pull, "company_id",       "internal_documentation (Confluence wiki pages + blog posts from the spaces this workspace selected: product specs, PRDs, architecture and decision records, runbooks, meeting and retro notes, team handbooks — the company's WRITTEN CONTEXT. Treat these as statements of intent, plans and internal process, NOT as customer evidence: a page asserting a problem is its author's claim about it, not measured proof. The space_key and title properties carry which team/area owns the page)"),
}

#: Providers whose records are DOCUMENTS rather than connector telemetry.
#:
#: Their signals still carry origin="connector" (they did arrive over a
#: connector) but also channel="upload", which is what the brief's evidence
#: gate reads to keep an upload-only tenant on the relaxed single-source path
#: (synthesis.convergence: `origin == "connector" and channel == "upload"`
#: counts as upload evidence). Google Drive reaches the same conclusion by a
#: different route — kg_ingest.drive_extract stamps origin="upload" outright,
#: for the reason spelled out in its module docstring.
#:
#: Getting this wrong is a SILENT regression in the wrong direction: a plain
#: connector origin would make briefs STRICTER for a tenant the moment they
#: connect their wiki.
_DOCUMENT_PROVIDERS: frozenset[str] = frozenset({"uploads", "confluence"})


#: Providers whose records are DISCRETE DATED EVENTS — a meeting happened at a
#: point in time, and the facts extracted from it were true THEN.
#:
#: These are extracted one document per record instead of batched, which is what
#: makes per-signal attribution possible at all: a batch has many source records
#: and one provenance, so the transcript id and the meeting date are lost the
#: moment two calls share a document. Every Fireflies signal in prod today reads
#: `{"doc": "fireflies-sync-batch-3"}` with valid_at = the sync date — 2,229 of
#: them for one workspace, all stamped inside a 9-day window while the calls
#: they came from span two and a half years.
#:
#: The cost is more extraction calls (a batch held ~2 meetings at the 6000-char
#: budget, so roughly 2×). The ledger absorbs it: only records whose rendering
#: CHANGED reach the model, so steady-state is one call per genuinely new
#: meeting, which is the floor for any design that dates signals correctly.
_EVENT_TIME_PROVIDERS: frozenset[str] = frozenset({"fireflies"})


def _batches(
    records: list[RawRecord], *, per_record: bool = False
) -> Iterable[list[RawRecord]]:
    if per_record:
        for r in records:
            yield [r]
        return
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


def _event_time(record: RawRecord) -> datetime | None:
    """RawRecord.timestamp (ISO, per the puller contract) → datetime, or None.

    Tolerant on purpose: a provider that hands back an unparseable timestamp
    should degrade to "no event time" (valid_at falls back to now, i.e. today's
    behaviour) rather than fail the whole sync."""
    raw = (record.timestamp or "").strip()
    if not raw:
        return None
    try:
        dt = datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except ValueError:
        logger.warning("unparseable timestamp %r on %s/%s",
                       raw, record.provider, record.external_id)
        return None
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


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
    since: datetime | None = None,
    until: datetime | None = None,
) -> dict:
    """Pull + extract one provider into the KG. Returns counts + errors.

    `since`/`until` scope the pull for pullers that accept a window; they are
    IGNORED by pullers that don't, so a caller can pass a window generically.

    The SCHEDULED sync deliberately passes no window and re-sweeps the provider
    every run. That looks wasteful and isn't: the ledger gate below drops every
    record whose rendering is unchanged before any model call, so a re-sweep
    costs API pages, not tokens — and unlike a `last_sync_at` cursor it also
    picks up a record EDITED after we last saw it (a transcript re-summarised
    yesterday has a new rendering and a new hash, but an old date, so a cursor
    would skip it forever). The window exists for bounded historical backfills,
    where re-sweeping all of history on every run would be the wasteful choice.
    """
    if provider not in PULLERS:
        raise ValueError(f"No puller for provider {provider!r}")
    puller, _, hint = PULLERS[provider]

    if records is None:
        kwargs: dict[str, object] = {}
        if since is not None or until is not None:
            accepted = inspect.signature(puller).parameters
            if since is not None and "since" in accepted:
                kwargs["since"] = since
            if until is not None and "until" in accepted:
                kwargs["until"] = until
            if (since is not None and "since" not in accepted) or (
                until is not None and "until" not in accepted
            ):
                logger.warning(
                    "puller %s takes no date window — pulling unscoped", provider
                )
        records = list(puller(token, **kwargs))

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
    per_record = provider in _EVENT_TIME_PROVIDERS
    for i, batch in enumerate(_batches(fresh, per_record=per_record)):
        text = "\n\n".join(r.render() for r in batch)
        # Document-class providers (the user's own uploads, and the company
        # wiki) are the same evidentiary class as manual uploads, so they keep
        # the brief gate's upload-only relaxation — convergence counts
        # channel="upload" as upload evidence, mirroring #868's Drive
        # rationale. See _DOCUMENT_PROVIDERS.
        extra: dict[str, object] = (
            {"channel": "upload"} if provider in _DOCUMENT_PROVIDERS else {}
        )
        doc_name = f"{provider}-sync-batch-{i}"
        valid_at = None
        if per_record:
            # One record per batch, so the id and the date are unambiguous and
            # can be stamped on every signal the record produces.
            rec = batch[0]
            doc_name = f"{provider}:{rec.external_id}"
            extra["external_id"] = rec.external_id
            valid_at = _event_time(rec)
            if valid_at:
                extra["occurred_at"] = valid_at.isoformat()
        try:
            r = extract_document(
                facade, enterprise_id,
                doc_name=doc_name,
                text=text,
                agent=f"ingest:{provider}",
                source_hint=hint,
                origin="connector",
                provenance_extra=extra or None,
                valid_at=valid_at,
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
