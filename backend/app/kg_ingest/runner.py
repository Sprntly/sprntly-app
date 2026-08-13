"""Ingestion runner — RawRecords → extraction batches → KG (§1b pipeline).

Generic across providers: a puller yields RawRecords; the runner batches them
(by char budget) and routes each batch through the extractor. A provider with
a dedicated method in ``PROVIDER_SKILLS`` (currently HubSpot, Jira, ClickUp —
the connectors whose record shapes carry classification signal the generic
prompt can't see, e.g. Jira's native issue type) is routed to its skill;
every other provider falls back to the fully generic extraction path
unchanged. Signal idempotency is content-keyed (uuid5), so re-syncs and
shifting batches can't duplicate. Error-isolated per batch — one bad batch
never kills the sync.

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
from typing import Callable, Iterable

from app.db.kg_ingest_ledger import record_hashes, seen_hashes
from app.graph.extractor import extract_document
from app.graph.facade import GraphFacade
from app.kg_ingest.pullers import (
    asana,
    clickup,
    confluence,
    fireflies,
    github,
    google_meet,
    hubspot,
    jira,
    sprinklr,
    superset,
    uploads,
    zoom,
)
from app.kg_ingest.types import RawRecord

logger = logging.getLogger(__name__)

_BATCH_CHAR_BUDGET = 6000

# provider → (puller fn, token_json key, source-type hint for the extractor)
PULLERS: dict[str, tuple[Callable[[str], Iterable[RawRecord]], str, str]] = {
    "clickup":   (clickup.pull,   "access_token", "project_mgmt (work items; classify bug/feature/fix)"),
    "asana":     (asana.pull,     "access_token", "project_mgmt (tasks: no native type or priority — classify bug/feature/fix from title+notes; status is the SECTION the task sits in within its project, completed is the done boolean; custom fields carry the team's own taxonomy where set)"),
    "jira":      (jira.pull,      "access_token", "project_mgmt (issues: bugs/stories/tasks/epics with native type + status + priority)"),
    "hubspot":   (hubspot.pull,   "access_token", "revenue + support + customer_voice (deals: blockers/feature gaps; tickets: support pain/churn risk; notes/emails: voice-of-customer; owners: attribution; line items: revenue detail)"),
    "fireflies": (fireflies.pull, "api_key",      "customer_voice / communication (meeting transcripts)"),
    # Like uploads and confluence, the "credential" is the company id — a Zoom
    # pull needs the picked hosts and the incremental cursor off the connection
    # row, and token_for can only hand a puller one field.
    "zoom":      (zoom.pull,      "company_id",   "customer_voice / communication (Zoom cloud-recorded meeting transcripts, speaker-attributed: what customers, prospects and the team actually SAID on a call — treat a quoted line as first-party evidence of that person's view, not as a decision. A record whose text states no transcript was available is a recording we could not read, NOT an empty meeting)"),
    # Like zoom/uploads/confluence, the "credential" is the company id — a Meet
    # pull needs the connected account's identity off the connection row, and
    # token_for can only hand a puller one field.
    "google_meet": (google_meet.pull, "company_id", "customer_voice / communication (Google Meet transcripts, speaker-attributed: what customers, prospects and the team actually SAID on a call — treat a quoted line as first-party evidence of that person's view, not as a decision. COVERAGE IS ONE PERSON'S CALENDAR: Google only exposes meetings the connected account ORGANIZED, and only for 30 days, so absence of a meeting is never evidence it did not happen. A record whose text states no transcript was available is a call we could not read, NOT an empty meeting)"),
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

# provider → vendored extraction skill id (backend/skills/<id>/), for the
# highest-value connectors that have a purpose-built extraction method beyond
# the generic prompt above. A provider with no entry here (fireflies, github,
# sprinklr, superset, uploads, and every non-PULLERS connector) falls back to
# the fully generic extractor unchanged — see extract_document's skill_id
# docstring. Extend this mapping as more connectors get a dedicated skill.
PROVIDER_SKILLS: dict[str, str] = {
    "hubspot": "hubspot-extraction",
    "jira": "jira-extraction",
    "clickup": "clickup-extraction",
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
        # Name every pull as it lands: which connector, for whom, how much.
        # This plus the fresh/dedup line below make "did provider X's data
        # reach the KG, and if not where did it stop" answerable from logs.
        logger.info(
            "kg-ingest: PULLED %s for %s — %d raw records",
            provider, enterprise_id, len(records),
        )

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
    # 0 fresh means the extraction below is a no-op by design (everything
    # already in the ledger), not a connector failure.
    logger.info(
        "kg-ingest: %s for %s — %d fresh of %d (dedup skipped %d)",
        provider, enterprise_id, len(fresh), len(records),
        totals["deduped"],
    )
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
                # Document-class providers (the user's own uploads, and the
                # company wiki) are the same evidentiary class as manual
                # uploads, so they keep the brief gate's upload-only
                # relaxation — convergence counts channel="upload" as upload
                # evidence, mirroring #868's Drive rationale. See
                # _DOCUMENT_PROVIDERS.
                provenance_extra=(
                    {"channel": "upload"} if provider in _DOCUMENT_PROVIDERS else None
                ),
                skill_id=PROVIDER_SKILLS.get(provider),
                # Haiku relevance + category triage ahead of every batch
                # — the core connector-sync ingestion path.
                triage=True,
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
