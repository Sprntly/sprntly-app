"""Roadmap → KG ingest — the workspace's uploaded roadmap as a priorities anchor.

A PM uploads their roadmap (onboarding strategy step, or Settings → Process &
Planning) and it lands in `roadmap_doc` (see app.roadmap_doc). Until now that
text only reached the Top Insights brief PROMPT — it was invisible to the
knowledge graph, so convergence, Ask, PRD evidence and ideation never knew what
the company had actually committed to shipping. This module closes that gap: one
extraction pass per roadmap VERSION, into the same KG every other source writes
to.

Three properties worth stating explicitly, because they're what makes a roadmap
different from every other ingest source:

1. ``origin=None`` — DELIBERATE. A roadmap is the company's OWN plan, not
   evidence that a problem exists. Stamping ``origin="upload"`` would let a
   tenant who uploaded nothing but a roadmap satisfy the brief evidence gate
   (convergence.is_upload_only → has_sufficient_evidence) and receive a brief
   built entirely out of its own stated intentions. So roadmap signals carry no
   origin at all — the gate counts them as neither upload nor connector
   evidence, exactly like onboarding metadata. What downstream code filters and
   attributes on is ``provenance["channel"] == "roadmap"``.

2. REPLACE semantics, not append. There is exactly ONE roadmap per workspace and
   the latest upload wins (`roadmap_doc` is upserted on workspace_id). So when
   version N is extracted, every still-live roadmap signal for that workspace
   that version N no longer asserts is EXPIRED immediately — a bet you dropped
   should stop steering the product the moment you drop it. Bets carried over
   from the previous version keep their existing (live) signals untouched,
   because extraction is content-keyed: the same sentence re-derives the same
   signal id, which comes back in the extractor's keep-set as a duplicate.

3. A per-version content-hash ledger (kg_source rows, source_type
   ``roadmap_doc``) makes re-ingest free. The same roadmap reaching us twice —
   the endpoint kickoff AND the next brief's incremental seed — extracts once.
   The ledger row is written only AFTER a successful extraction, so a failed run
   (bad key, provider outage) simply retries on the next touch.

Called from two places, both fire-and-forget / error-isolated:
  * `kg_ingest.auto_sync.kickoff_roadmap_ingest` — right after POST
    /v1/company/roadmap-doc, so a fresh upload reaches the KG in seconds.
  * `synthesis_brief.seed_incremental` — the grandfather + retry leg, so every
    roadmap uploaded BEFORE this shipped backfills on the next brief generation.
"""
from __future__ import annotations

import hashlib
import logging
import uuid
from typing import Optional

from app.graph.extractor import _NS, extract_document
from app.graph.facade import GraphFacade
from app.graph.types import Source
from app.roadmap_doc import load_roadmap_doc

logger = logging.getLogger(__name__)

#: kg_source.source_type used for the per-version ingest ledger.
LEDGER_SOURCE_TYPE = "roadmap_doc"

#: Provenance channel every roadmap-derived signal carries. This — NOT an
#: origin — is what downstream filters on.
ROADMAP_CHANNEL = "roadmap"

#: Chars per extraction call. Parity with the uploads puller
#: (kg_ingest/pullers/uploads.py) so a chunk always fits one extraction batch.
_CHUNK_CHARS = 4000

#: Pilot-scale ceiling, again mirroring the uploads puller. A 20 MB roadmap
#: (the route's upload cap) must not turn into thousands of model calls.
_MAX_CHUNKS = 25

#: What the extractor is looking at. Roadmaps read very differently from
#: customer evidence — this keeps the model from mistaking a planned bet for a
#: reported problem.
_SOURCE_HINT = (
    "stated product roadmap / planned priorities — the company's OWN plan "
    "(bets, timelines, goals), not customer evidence"
)

#: Seeded (non-evidence) source type for anything the model didn't pick an
#: evidence type for on merit. 60-day staleness window (graph/types.py) suits a
#: roadmap: it stays relevant for a quarter-ish, then ages out on its own.
_SOURCE_TYPE_DEFAULT = "pm_manual"


def _chunks(text: str) -> list[str]:
    """Split on the chunk budget. Returns [] for whitespace-only text."""
    body = (text or "").strip()
    if not body:
        return []
    return [
        body[i:i + _CHUNK_CHARS] for i in range(0, len(body), _CHUNK_CHARS)
    ][:_MAX_CHUNKS]


def content_sha(company_id: str, workspace_id: Optional[str], text: str) -> str:
    """Ledger key for one roadmap version. Scoped by company AND workspace so
    two workspaces that upload the SAME file each get their own extraction (their
    signals differ only by provenance, but each needs its own ledger row)."""
    return hashlib.sha256(
        f"{company_id}|{workspace_id or ''}|{text}".encode()
    ).hexdigest()


def _ledger_id(company_id: str, sha: str) -> str:
    """Deterministic kg_source id — an upsert-safe key, so a concurrent
    kickoff + seed racing on the same version can't duplicate the row."""
    return str(uuid.uuid5(_NS, f"roadmap-doc|{company_id}|{sha}"))


def _already_ingested(facade: GraphFacade, company_id: str, sha: str) -> bool:
    """True when this exact roadmap version already has a ledger row."""
    return any(
        (s.config or {}).get("content_sha") == sha
        for s in facade.list_sources(company_id, source_type=LEDGER_SOURCE_TYPE)
    )


def _live_roadmap_signal_ids(
    facade: GraphFacade, company_id: str, workspace_id: Optional[str]
) -> list[str]:
    """Ids of this WORKSPACE's still-active roadmap signals.

    Workspace-exact by design: workspace A replacing its roadmap must never
    expire workspace B's bets, and a company-wide sweep would do exactly that.
    Legacy signals stamped without a workspace_id match a None workspace_id only.
    """
    out: list[str] = []
    for sig in facade.active_signals(company_id):
        prov = sig.provenance or {}
        if prov.get("channel") != ROADMAP_CHANNEL:
            continue
        if (prov.get("workspace_id") or None) != (workspace_id or None):
            continue
        out.append(sig.id)
    return out


def ingest_roadmap(
    company_id: str,
    workspace_id: Optional[str] = None,
    *,
    facade: Optional[GraphFacade] = None,
) -> dict:
    """Extract the workspace's current roadmap into the KG (replace semantics).

    Idempotent per roadmap VERSION via the content-hash ledger, so calling this
    on every upload AND on every brief seed costs one extraction per version.

    Returns a status dict:
      * ``{"status": "no_text"}``   — no roadmap stored, or nothing extractable
        from it (a scanned-image PDF / binary upload converts to empty text).
      * ``{"status": "unchanged", "content_sha": …}`` — this version is already
        in the KG; zero model calls.
      * ``{"status": "ingested", signals, themes, duplicates, chunks, expired,
        version, content_sha}`` — extracted; ``expired`` counts signals from the
        PREVIOUS roadmap version that this version no longer asserts.

    Raises on extraction failure (bad API key, provider outage) WITHOUT writing
    the ledger row, so the next touch retries. Both call sites isolate.
    """
    facade = facade or GraphFacade()
    doc = load_roadmap_doc(company_id, workspace_id=workspace_id)
    if doc is None:
        return {"status": "no_text"}
    text = (doc.extracted_text or "").strip()
    parts = _chunks(text)
    if not parts:
        # Stored fine, nothing to extract (unparseable/binary upload). No ledger
        # row: if the PM re-uploads a readable version we want to try again.
        logger.info("roadmap-ingest: no extractable text for company=%s ws=%s",
                    company_id, workspace_id)
        return {"status": "no_text"}

    sha = content_sha(company_id, workspace_id, text)
    if _already_ingested(facade, company_id, sha):
        return {"status": "unchanged", "content_sha": sha}

    # Snapshot what the PREVIOUS version left live BEFORE extracting, so the
    # diff below is against the old roadmap, not against our own fresh writes.
    previous_ids = set(_live_roadmap_signal_ids(facade, company_id, workspace_id))

    provenance_extra: dict[str, object] = {
        "channel": ROADMAP_CHANNEL,
        "workspace_id": workspace_id or None,
        "roadmap_version": doc.version,
        "filename": doc.filename,
    }

    totals = {"signals": 0, "themes": 0, "duplicates": 0}
    keep: set[str] = set()
    for i, part in enumerate(parts):
        doc_name = (
            f"roadmap: {doc.filename}" if len(parts) == 1
            else f"roadmap: {doc.filename} (part {i + 1}/{len(parts)})"
        )
        r = extract_document(
            facade, company_id,
            doc_name=doc_name, text=part,
            agent="ingest:roadmap",
            source_hint=_SOURCE_HINT,
            # origin=None is the whole point — see module docstring (1).
            origin=None,
            source_type_default=_SOURCE_TYPE_DEFAULT,
            provenance_extra=provenance_extra,
        )
        totals["signals"] += r["signals"]
        totals["themes"] += r["themes"]
        totals["duplicates"] += r["skipped"]
        keep.update(r.get("signal_ids") or [])

    # Replace semantics: retire whatever the old roadmap asserted and this one
    # doesn't. Only reached when EVERY chunk extracted cleanly — a partial run
    # would have a partial keep-set and would wrongly expire live bets.
    dropped = sorted(previous_ids - keep)
    expired = facade.expire_signals(company_id, dropped) if dropped else 0

    # Ledger LAST: a version is only "done" once its signals are in the graph.
    facade.create_source(company_id, Source(
        id=_ledger_id(company_id, sha),
        enterprise_id=company_id,
        source_type=LEDGER_SOURCE_TYPE,
        label=doc.filename[:200],
        config={
            "content_sha": sha,
            "workspace_id": workspace_id,
            "filename": doc.filename,
            "version": doc.version,
            "chunks": len(parts),
        },
    ))
    logger.info(
        "roadmap-ingest done: company=%s ws=%s v%s chunks=%s signals=%s "
        "duplicates=%s expired=%s",
        company_id, workspace_id, doc.version, len(parts),
        totals["signals"], totals["duplicates"], expired,
    )
    return {
        "status": "ingested",
        "signals": totals["signals"],
        "themes": totals["themes"],
        "duplicates": totals["duplicates"],
        "chunks": len(parts),
        "expired": expired,
        "version": doc.version,
        "content_sha": sha,
    }
