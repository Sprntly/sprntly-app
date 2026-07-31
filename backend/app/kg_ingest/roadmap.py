"""Roadmap → KG ingest — the workspace's uploaded roadmap as a priorities anchor.

A PM uploads their roadmap (onboarding strategy step, or Settings → Process &
Planning) and it lands in `roadmap_doc` (see app.roadmap_doc). Until now that
text only reached the Top Insights brief PROMPT — it was invisible to the
knowledge graph, so convergence, Ask, PRD evidence and ideation never knew what
the company had actually committed to shipping. This module closes that gap: one
extraction pass per roadmap VERSION, into the same KG every other source writes
to.

Four properties worth stating explicitly, because they're what makes a roadmap
different from every other ingest source:

1. NOT EVIDENCE — enforced on BOTH axes the brief sufficiency gate reads.
   A roadmap is the company's OWN plan, not evidence that a problem exists; a
   tenant whose only data is a roadmap must still be refused a brief rather than
   handed one built out of its own stated intentions.

   * ``origin=None`` — keeps roadmap signals out of the upload-only relaxation
     (convergence.is_upload_only → has_sufficient_evidence). They count as
     neither upload nor connector evidence, exactly like onboarding metadata.
   * ``force_source_type="pm_manual"`` — the gate's PRIMARY path counts signals
     by source_type (CONNECTED_SOURCE_TYPES → connected_breadth >= 2, or >= 3
     connected signals), and origin does not affect it at all. Roadmaps quote
     their own metrics ("ARR $2M", "churn 9%", "clear the Zendesk backlog"), and
     the extractor otherwise keeps model-picked evidence types on merit — so a
     mere default would let those bullets register as revenue/analytics evidence
     and open the gate. Pinning makes that structurally impossible.

   What downstream code filters and attributes on is
   ``provenance["channel"] == "roadmap"``.

2. REPLACE semantics, not append. There is exactly ONE roadmap per workspace and
   the latest upload wins (`roadmap_doc` is upserted on workspace_id). So when
   version N is extracted, every still-live roadmap signal for that workspace
   that version N no longer asserts is EXPIRED immediately — a bet you dropped
   should stop steering the product the moment you drop it. Bets carried over
   from the previous version keep their existing (live) signals untouched,
   because extraction is content-keyed: the same sentence re-derives the same
   signal id, which comes back in the extractor's keep-set as a duplicate.

   Expiry is deliberately conservative — it is skipped entirely when the
   extraction produced ZERO signals (we failed to read v2; that is not evidence
   v1's bets were dropped), when the upload was an unparseable binary stub, and
   when the stored roadmap changed while we were extracting. Retiring a live bet
   by mistake is far worse than carrying a stale one for one more cycle.

3. A per-version content-hash ledger (kg_source rows, source_type
   ``roadmap_doc``) makes re-ingest free. The same roadmap reaching us twice —
   the endpoint kickoff AND the next brief's incremental seed — extracts once.
   The ledger row is written only AFTER a successful extraction, so a failed run
   (bad key, provider outage) simply retries on the next touch.

4. Company-root wiring. Every signal a version asserts (freshly written, or
   carried over unchanged from the previous version) gets an INFORMS edge to
   the tenant's single ``company`` root entity (``GraphFacade.
   ensure_company_entity`` — the same find-or-create primitive and edge
   semantics ``research.business_context_projection`` uses), so roadmap
   content doesn't float disconnected from its tenant. No separate edge-expiry
   step is needed: an INFORMS edge from a signal ``expire_signals`` later
   retires isn't deleted, but every reader that walks INFORMS/edges-from-a-
   signal already resolves the source signal and calls ``signal_is_retired``
   before trusting it (evidence_kg, retrieval, convergence) — the same pattern
   every other signal-sourced edge in this KG already relies on, so retirement
   is covered transitively, not by a new mechanism. Theme entities the shared
   extractor creates as a side effect are NOT wired here — they are a
   cross-source concept every ingestion path shares, not something owned by
   roadmap ingestion.

Called from two places, both fire-and-forget / error-isolated:
  * `kg_ingest.auto_sync.kickoff_roadmap_ingest` — right after POST
    /v1/company/roadmap-doc, so a fresh upload reaches the KG in seconds.
  * `synthesis_brief.seed_incremental` — the grandfather + retry leg, so every
    roadmap uploaded BEFORE this shipped backfills on the next brief generation.
"""
from __future__ import annotations

import hashlib
import logging
import threading
import uuid
from typing import Optional

from app.graph.extractor import _NS, extract_document
from app.graph.facade import GraphFacade
from app.graph.types import Relationship, Source
from app.ingest import is_unparsed_stub
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

#: Source type FORCED onto every roadmap signal — not a default, a pin.
#:
#: The brief sufficiency gate counts signals by source_type
#: (convergence.CONNECTED_SOURCE_TYPES → connected_breadth >= 2 or >= 3 connected
#: signals). A roadmap routinely quotes its own metrics ("ARR $2M", "churn 9%",
#: "clear the Zendesk backlog"), and the extractor keeps model-picked evidence
#: types on merit — so DEFAULTING would let a roadmap contribute
#: revenue/analytics/customer_voice "evidence" and open the gate on the company's
#: own stated plans. Pinning to pm_manual (a non-connected, seeded type, 60-day
#: window per graph/types.py) makes that structurally impossible, and is the
#: source_type half of the same decision as origin=None.
_FORCE_SOURCE_TYPE = "pm_manual"

#: Per-company lock. Roadmap ingest is READ-MODIFY-WRITE across several
#: statements (snapshot live signals → extract → expire the difference → record
#: the ledger), and it runs from two places that can overlap: the upload kickoff
#: and a brief's incremental seed. Without serialization the worst interleaving
#: expires the CURRENT roadmap's signals — the seed snapshots v3's ids as
#: "previous" while its keep-set is v2's. Reentrant so the auto_sync wrapper can
#: hold it and still call in.
#:
#: LIMIT: this is an in-process lock, so it serializes within ONE uvicorn worker.
#: Two workers (or the scheduler process) can still interleave. The version/sha
#: re-check immediately before the expiry pass is what makes that case safe —
#: the lock is the cheap fast path, the re-check is the actual guarantee.
_ingest_locks: dict[str, threading.RLock] = {}
_ingest_locks_guard = threading.Lock()


def ingest_lock(company_id: str) -> threading.RLock:
    """The per-company roadmap-ingest lock (reentrant)."""
    with _ingest_locks_guard:
        lock = _ingest_locks.get(company_id)
        if lock is None:
            lock = threading.RLock()
            _ingest_locks[company_id] = lock
        return lock


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


def _company_informed_signal_ids(
    facade: GraphFacade, company_id: str, company_entity_id: str
) -> set[str]:
    """Ids of signals that already carry an INFORMS edge to the tenant's
    `company` root. The per-ingest dedup check: a bet CARRIED OVER from the
    previous roadmap version re-derives the same content-keyed signal id (see
    module docstring (2)), so without this check re-extracting it every
    version would pile up a duplicate edge to the root on every re-upload."""
    return {
        e.source_id
        for e in facade.edges_to(company_id, company_entity_id, type="INFORMS")
        if e.source_kind == "signal"
    }


def _roadmap_unchanged_since(
    company_id: str, workspace_id: Optional[str], version: int, sha: str
) -> bool:
    """Is the STORED roadmap still the one we just extracted?

    Re-read immediately before the expiry pass. The per-company lock only covers
    one process, so a second uvicorn worker (or the scheduler) can replace the
    roadmap mid-extraction; expiring a keep-set computed from a superseded
    version could retire the CURRENT roadmap's signals. Fails CLOSED — any read
    error returns False, which skips expiry (keeping stale bets is recoverable on
    the next ingest; wiping live ones is not)."""
    try:
        current = load_roadmap_doc(company_id, workspace_id=workspace_id)
    except Exception:  # noqa: BLE001 — fail closed
        logger.exception("roadmap-ingest: re-read failed for %s (ws=%s)",
                         company_id, workspace_id)
        return False
    if current is None:
        return False
    if current.version != version:
        return False
    return content_sha(
        company_id, workspace_id, (current.extracted_text or "").strip()
    ) == sha


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
      * ``{"status": "no_text"}``   — no roadmap stored, or nothing usable in it:
        empty extracted text (scanned-image PDF) OR the unparsed-binary
        placeholder stub (legacy .doc/.ppt/.xls). No ledger row either way, so a
        readable re-upload is tried again.
      * ``{"status": "unchanged", "content_sha": …}`` — this version is already
        in the KG; zero model calls.
      * ``{"status": "ingested", signals, themes, duplicates, chunks, expired,
        version, content_sha}`` — extracted; ``expired`` counts signals from the
        PREVIOUS roadmap version that this version no longer asserts.

    Raises on extraction failure (bad API key, provider outage) WITHOUT writing
    the ledger row, so the next touch retries. Both call sites isolate.

    Serialized per company (``ingest_lock``) and re-validated against the stored
    roadmap immediately before the expiry pass, so a concurrent upload can never
    make this expire the CURRENT roadmap's signals.
    """
    facade = facade or GraphFacade()
    with ingest_lock(company_id):
        return _ingest_roadmap_locked(company_id, workspace_id, facade)


def _ingest_roadmap_locked(
    company_id: str, workspace_id: Optional[str], facade: GraphFacade
) -> dict:
    doc = load_roadmap_doc(company_id, workspace_id=workspace_id)
    if doc is None:
        return {"status": "no_text"}
    text = (doc.extracted_text or "").strip()
    # The unparsed-binary stub is NON-EMPTY (app.ingest.fallback_to_md), so a
    # plain `if not text` misses it. Extracting it would yield ~0 signals and an
    # empty keep-set, which — before this guard — expired every signal from the
    # PREVIOUS roadmap and then recorded a ledger row for the useless stub. Treat
    # it exactly like no text: no model call, no ledger row, no expiry.
    if is_unparsed_stub(text):
        logger.info(
            "roadmap-ingest: %r is an unparsed binary stub for company=%s ws=%s "
            "— skipping (previous roadmap signals left intact)",
            doc.filename, company_id, workspace_id,
        )
        return {"status": "no_text", "reason": "unparsed_binary"}
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
            # FORCED, not defaulted: a roadmap's own quoted metrics must never
            # count as connected evidence in the brief gate. See _FORCE_SOURCE_TYPE.
            force_source_type=_FORCE_SOURCE_TYPE,
            provenance_extra=provenance_extra,
        )
        totals["signals"] += r["signals"]
        totals["themes"] += r["themes"]
        totals["duplicates"] += r["skipped"]
        keep.update(r.get("signal_ids") or [])

    # ── Company-root wiring ───────────────────────────────────────────────────
    # Every signal this version asserts (freshly written, or carried over from
    # the previous version and re-derived to the same content-keyed id) gets an
    # INFORMS edge to the tenant's single `company` root — the same
    # find-or-create primitive and edge semantics `business_context_projection`
    # uses, so roadmap-derived content stops floating disconnected from its
    # tenant. Only THEMES (created generically inside `extract_document`,
    # shared by every extraction caller — connectors, uploads, roadmap alike)
    # are left unwired here: they are not a roadmap-owned node, and wiring them
    # would mean changing the shared extractor's behavior for every ingestion
    # path, not just this one.
    #
    # Dedup via `_company_informed_signal_ids` (not a blind write) because
    # `keep` includes ids `extract_document` reported as duplicate-skipped —
    # bets carried over unchanged from a version that already wired them.
    company_wired = 0
    if keep:
        company_entity_id = facade.ensure_company_entity(company_id)
        already_wired = _company_informed_signal_ids(
            facade, company_id, company_entity_id)
        for sig_id in sorted(keep - already_wired):
            facade.write_relationship(company_id, Relationship(
                enterprise_id=company_id, type="INFORMS",
                source_kind="signal", source_id=sig_id,
                target_kind="entity", target_id=company_entity_id,
                provenance={"agent": "ingest:roadmap"},
            ))
            company_wired += 1

    # ── Replace semantics, with two safety gates ─────────────────────────────
    # Retire whatever the old roadmap asserted and this one doesn't. Only reached
    # when EVERY chunk extracted cleanly — a partial run would have a partial
    # keep-set and would wrongly expire live bets.
    dropped = sorted(previous_ids - keep)
    expired = 0
    if not dropped:
        pass
    elif not keep:
        # Zero-signal extraction. Whatever the cause (a roadmap the model found
        # nothing extractable in, an empty template, a format that converted to
        # noise), a v2 that asserts NOTHING is not evidence that v1's bets were
        # dropped — it's evidence we failed to read v2. Never wipe on empty.
        logger.warning(
            "roadmap-ingest: extraction produced ZERO signals for company=%s "
            "ws=%s v%s — skipping expiry, keeping %d previous signal(s)",
            company_id, workspace_id, doc.version, len(dropped),
        )
    elif not _roadmap_unchanged_since(company_id, workspace_id, doc.version, sha):
        # Someone replaced the roadmap while we were extracting (another worker /
        # the scheduler — the in-process lock can't see them). Our keep-set
        # describes a roadmap that is no longer current, so expiring against it
        # could retire the CURRENT roadmap's signals. Skip; the newer version's
        # own ingest run owns the expiry.
        logger.warning(
            "roadmap-ingest: roadmap for company=%s ws=%s changed during "
            "extraction (was v%s) — skipping expiry, newer ingest will handle it",
            company_id, workspace_id, doc.version,
        )
    else:
        expired = facade.expire_signals(company_id, dropped)

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
        "duplicates=%s expired=%s company_wired=%s",
        company_id, workspace_id, doc.version, len(parts),
        totals["signals"], totals["duplicates"], expired, company_wired,
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
        "company_wired": company_wired,
    }
