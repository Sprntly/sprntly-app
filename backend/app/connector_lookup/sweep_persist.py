"""Persist a cross-connector sweep's reads into the knowledge graph.

STATUS: LIVE, unconditionally — no flag. Persistence used to be parked behind
`sweep_kg_persist_enabled` because the ledger dedupe was structurally broken:
it hashed `SourceResult.text` — one whole source's answer to whatever question
triggered the sweep — which could never collide with the scheduled 6-hourly
pull's hash of `RawRecord.render()` (one record, fixed structural format). The
adapter-records work fixed the unit mismatch (see `_content_hash` below); with
dedupe now real, the flag had nothing left to protect against, so it was
removed rather than flipped. Two things this means, worth stating plainly:

  - persistence is live the moment this merges — every sweep with a
    RECORDS-capable source now writes to the KG, on every company, with no
    staged rollout;
  - the flag's sibling, `settings.chat_cross_connector_sweep`, is UNCHANGED
    and still gates whether the sweep runs at all. Turning the sweep off
    still turns this off with it — there is nothing here to persist from a
    sweep that never ran.

Sibling to `sweep.py`, deliberately kept OUT of it. `sweep.py` carries the
sweep's latency contract as a load-bearing module comment (BUDGET_S, the
fan-out itself); folding a write path into that file would put the
persistence discussion inside the file whose whole point is "we read fast and
throw it away".

THE SAFETY LINE (do not cross): this module's only input is a `SweepResult` —
the raw text (and, now, the structured records) a sweep read FROM connectors.
It has no path that could accept the model's answer, and that is deliberate,
not incidental. The sweep result feeds the very answer call that reads the KG
back on the next ask; persisting the answer would make the model's own output
become its own evidence, and a wrong answer written once would look identical
to ground truth on every future read — an error that compounds silently and
does not self-correct. Every source ever written here comes from
`SweepResult.read`, which is itself already filtered to `usable` (actually
read from a connector, non-empty) — never the rendered prompt block, never
anything the model produced.

OFF THE ASK PATH: `kickoff_sweep_persist` starts a daemon thread and returns
immediately, same shape as `app.kg_ingest.auto_sync.kickoff_corpus_seed`. By
the time it is called, `qa_agent._sweep_context` has already rendered the
prompt block the answer call will use — this function cannot add latency to
that render because nothing here participates in producing it. The thread's
own work (ledger read, triage, extraction, KG write) happens strictly after
the caller returns.
"""
from __future__ import annotations

import logging
import threading
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from app.connector_lookup.sweep import SourceResult, SweepResult
    from app.kg_ingest.types import RawRecord

logger = logging.getLogger(__name__)

# Per-company locks so two asks that both trigger a sweep around the same time
# serialize their persistence instead of extracting the same fresh content in
# parallel — mirrors auto_sync's `_corpus_seed_lock`. Serializing (rather than
# dropping the second one) means the second run still lands anything the first
# missed; the content-hash ledger makes the overlap a cheap no-op, not a
# repeated LLM call.
_locks: dict[str, threading.Lock] = {}
_locks_guard = threading.Lock()


def _lock_for(enterprise_id: str) -> threading.Lock:
    with _locks_guard:
        lock = _locks.get(enterprise_id)
        if lock is None:
            lock = threading.Lock()
            _locks[enterprise_id] = lock
        return lock


def _content_hash(rendered: str) -> str:
    """Stable ledger key for one rendered unit.

    This is now LITERALLY `kg_ingest.runner._content_hash` — not merely the
    same algorithm kept in sync by hand, but the same function object,
    imported rather than re-implemented — because that is what makes the two
    hashes collide, and a second sha256-over-utf-8 that happened to agree
    today could silently drift tomorrow.

    Why sharing the unit matters: this function is unit-agnostic — it hashes
    whatever string its caller passes — and `_run` below now passes it TWO
    different kinds of string depending on what a source carries:

      - when a source has `RawRecord`s (the adapter-records capability,
        `connector_lookup/base.py`'s `RecordsCapable`), each record's
        `render()` is hashed individually — the SAME unit and the SAME
        function `kg_ingest.runner.sync_provider` hashes for the scheduled
        6-hourly pull. A record the pull already ingested and a record this
        sweep reads for the identical item now hash to the identical value,
        so the ledger recognises it and the extractor is not paid for it
        twice. That collision is the entire point of this ticket.
      - when a source has none (an adapter that hasn't implemented the
        records capability, or a call whose response was too lean to build
        one from — see each adapter's `dispatch_records` docstring for which
        case applies), `source.text` — one whole source's free-text answer to
        whatever question triggered the sweep — is hashed instead, exactly as
        before. That still only dedupes a sweep against a PREVIOUS IDENTICAL
        SWEEP, never against the scheduled pull, because two different
        questions about the same item produce two different strings. This is
        the fallback this docstring used to describe as the feature's whole
        behaviour; it is now the narrower, degraded case.
    """
    from app.kg_ingest.runner import _content_hash as _pull_content_hash

    return _pull_content_hash(rendered)


def _hashable_units(
    source: "SourceResult",
) -> "list[tuple[str, RawRecord | None]]":
    """`[(rendered, record_or_None), ...]` — one entry per thing `_run` will
    hash/extract for this source. A source with records yields one entry PER
    RECORD (AC6: records are used when present); a source without yields one
    entry for the whole `source.text` (AC6: falls back to today's behaviour).
    Never both — records, when present, REPLACE text-hashing for that source
    rather than adding to it, so a source is never double-counted.
    """
    if source.records:
        return [(record.render(), record) for record in source.records]
    return [(source.text, None)]


def _run(enterprise_id: str, sources: "list[SourceResult]") -> None:
    """Blocking persistence — runs inside the daemon thread only.

    Fully isolated: any failure — including `GraphFacade()` construction, a
    ledger outage, or an extraction blow-up — is logged, never raised. Same
    one-try-around-everything shape as `auto_sync._run_corpus_seed`: the ask
    this sweep served has already been answered by the time this runs, so
    nothing here can reach the user regardless of where it fails. Serialized
    per company via `_lock_for` (see above).
    """
    from app.db.kg_ingest_ledger import record_hashes, seen_hashes
    from app.graph.extractor import extract_document
    from app.graph.facade import GraphFacade

    lock = _lock_for(enterprise_id)
    with lock:
        try:
            # One item per record when a source has records (AC6), else one
            # item for the whole source text — see `_hashable_units`.
            items = [
                (source, record, _content_hash(rendered))
                for source in sources
                for rendered, record in _hashable_units(source)
            ]
            try:
                # Fail-open, matching the ledger's own contract: a read error
                # means "extract everything this run" rather than "skip it".
                seen = seen_hashes(
                    enterprise_id, list({h for *_r, h in items})
                )
            except Exception:  # noqa: BLE001 — advisory only, never break the run
                logger.exception(
                    "sweep-persist: ledger read failed for %s (extracting all)",
                    enterprise_id,
                )
                seen = set()

            facade = GraphFacade()
            written = skipped = 0
            for source, record, content_hash in items:
                if content_hash in seen:
                    skipped += 1
                    continue
                try:
                    if record is not None:
                        text = record.render()
                        doc_name = (
                            f"{source.key}-sweep-{record.external_id}-"
                            f"{content_hash[:12]}"
                        )
                    else:
                        text = source.text
                        doc_name = f"{source.key}-sweep-{content_hash[:12]}"
                    extract_document(
                        facade, enterprise_id,
                        doc_name=doc_name,
                        text=text,
                        agent=f"ingest:{source.key}",
                        origin="connector",
                        # Names the ROUTE, on top of the provider key already
                        # in `agent`/`doc_name` — a scheduled-pull signal and
                        # a sweep-origin signal from the same provider must be
                        # distinguishable from provenance alone (AC5).
                        provenance_extra={"route": "sweep"},
                        # Same Haiku relevance gate every other ingestion path
                        # runs through (kg_ingest/runner.py) — no second layer.
                        triage=True,
                    )
                    # Only an item that made it through extraction is
                    # recorded — a failed write keeps its hash out of the
                    # ledger so the next sweep or scheduled pull to read this
                    # content retries it.
                    record_hashes(enterprise_id, source.key, [content_hash])
                    written += 1
                except Exception:  # noqa: BLE001 — one item failing must not block the rest
                    logger.exception(
                        "sweep-persist: extraction failed for %s/%s",
                        enterprise_id, source.key,
                    )
            logger.info(
                "sweep-persist: %s written=%d skipped=%d of %d item(s) "
                "across %d source(s)",
                enterprise_id, written, skipped, len(items), len(sources),
            )
        except Exception:  # noqa: BLE001 — fully isolated, see docstring
            logger.exception("sweep-persist: run failed for %s", enterprise_id)


def kickoff_sweep_persist(enterprise_id: str, result: "SweepResult | None") -> bool:
    """Fire-and-forget: persist a completed sweep's reads.

    Called from `qa_agent._sweep_context` right after a sweep's block has
    already been rendered for the prompt — this is strictly additional work,
    never a precondition for the answer. Returns False (nothing started) when
    there is no tenant, or the sweep read nothing usable. Unconditional
    otherwise — there is no feature flag here; see the module docstring for
    why. Never blocks; never raises into the caller's ask flow.
    """
    if not enterprise_id or result is None:
        return False
    try:
        # `.read` is already filtered to sources actually read from a
        # connector (status ok, non-empty text) — never an unread/timed-out/
        # dropped source, and never an `unread_reason` (AC6).
        sources = list(result.read)
        if not sources:
            return False
        t = threading.Thread(
            target=_run, args=(enterprise_id, sources),
            name="sweep-persist", daemon=True,
        )
        t.start()
        return True
    except Exception:  # noqa: BLE001 — never let persistence touch the caller
        logger.exception("sweep-persist: kickoff failed for %s", enterprise_id)
        return False
