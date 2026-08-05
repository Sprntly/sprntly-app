"""Persist a cross-connector sweep's reads into the knowledge graph.

STATUS: PARKED, flag off. Do not switch `sweep_kg_persist_enabled` on until
the adapter-record work below lands — see `_content_hash` for the full
reasoning. In short: the ledger here dedupes a sweep only against a PREVIOUS
IDENTICAL SWEEP, never against the scheduled 6-hourly pull, because the two
hash different units. Turning this on today re-extracts content the pull has
already ingested. That is wasted spend, not graph corruption — signal ids are
keyed on the fact, so duplicates collapse on write — but the waste is real and
the ledger's `skipped` count will read ~0, which looks like "the sweep finds
lots of new material" when it means "this ledger cannot see the pull."

Sibling to `sweep.py`, deliberately kept OUT of it. `sweep.py` carries the
sweep's latency contract as a load-bearing module comment (BUDGET_S, the
fan-out itself); folding a write path into that file would put the
persistence discussion inside the file whose whole point is "we read fast and
throw it away". Keeping this separate means the flag in `config.py` is a
one-line unwire — delete the import at the single call site in
`qa_agent._sweep_context` and this module is dead code again, with zero
change to `sweep.py`.

THE SAFETY LINE (do not cross): this module's only input is a `SweepResult` —
the raw text a sweep read FROM connectors. It has no path that could accept
the model's answer, and that is deliberate, not incidental. The sweep result
feeds the very answer call that reads the KG back on the next ask; persisting
the answer would make the model's own output become its own evidence, and a
wrong answer written once would look identical to ground truth on every
future read — an error that compounds silently and does not self-correct.
Every source ever written here comes from `SweepResult.read`, which is itself
already filtered to `usable` (actually read from a connector, non-empty) —
never the rendered prompt block, never anything the model produced.

OFF THE ASK PATH: `kickoff_sweep_persist` starts a daemon thread and returns
immediately, same shape as `app.kg_ingest.auto_sync.kickoff_corpus_seed`. By
the time it is called, `qa_agent._sweep_context` has already rendered the
prompt block the answer call will use — this function cannot add latency to
that render because nothing here participates in producing it. The thread's
own work (ledger read, triage, extraction, KG write) happens strictly after
the caller returns.
"""
from __future__ import annotations

import hashlib
import logging
import threading
from typing import TYPE_CHECKING

from app.config import settings

if TYPE_CHECKING:
    from app.connector_lookup.sweep import SourceResult, SweepResult

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


def _content_hash(text: str) -> str:
    """Stable ledger key for one source's rendered text.

    SCOPE, stated precisely because an earlier version of this docstring
    claimed more than the code delivers: this dedupes a sweep against a
    PREVIOUS IDENTICAL SWEEP, and nothing else. It does NOT dedupe against the
    scheduled pull.

    The hash FUNCTION matches `kg_ingest.runner._content_hash` (sha256 over
    utf-8), but the two hash different UNITS and therefore can never collide:

      - the scheduled pull hashes `RawRecord.render()` — ONE record, in a
        fixed structural format (`[jira/issue id=PROJ-123 …]\\ntitle: …`);
      - this hashes `SourceResult.text` — ONE WHOLE SOURCE's free-text answer
        to one question, whose shape depends on what was ASKED.

    Two sweeps about the same Jira issue, prompted by different questions,
    produce different strings; neither matches that issue's record rendering.
    So content the scheduled pull already ingested WILL be re-extracted here.

    That costs an extraction call; it does NOT corrupt the graph. Signal ids
    are `uuid5(enterprise_id, content)` (`graph.extractor`, ~:257), keyed on
    the FACT rather than the document, so a fact arriving by both routes
    collapses to one row (PK conflict → counted in the extractor's own
    `skipped`). The graph stays clean; the duplicate work is paid for.

    The real fix is upstream, not here: adapters already hold structured rows
    before they render prose (e.g. `connector_lookup.jira` has `hits` before
    `render_search(hits)`), so emitting `RawRecord`s from a sweep would make
    this hash collide with the pull's by construction. Until that lands, treat
    this ledger as a same-question cache, and read the extractor's returned
    `skipped` — not this ledger — as the measure of how much a sweep actually
    adds.
    """
    return hashlib.sha256(text.encode("utf-8", "replace")).hexdigest()


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
            hashes = {id(s): _content_hash(s.text) for s in sources}
            try:
                # Fail-open, matching the ledger's own contract: a read error
                # means "extract everything this run" rather than "skip it".
                seen = seen_hashes(enterprise_id, list(set(hashes.values())))
            except Exception:  # noqa: BLE001 — advisory only, never break the run
                logger.exception(
                    "sweep-persist: ledger read failed for %s (extracting all)",
                    enterprise_id,
                )
                seen = set()

            facade = GraphFacade()
            written = skipped = 0
            for source in sources:
                content_hash = hashes[id(source)]
                if content_hash in seen:
                    skipped += 1
                    continue
                try:
                    extract_document(
                        facade, enterprise_id,
                        doc_name=f"{source.key}-sweep-{content_hash[:12]}",
                        text=source.text,
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
                    # Only a source that made it through extraction is
                    # recorded — a failed write keeps its hash out of the
                    # ledger so the next sweep or scheduled pull to read this
                    # content retries it.
                    record_hashes(enterprise_id, source.key, [content_hash])
                    written += 1
                except Exception:  # noqa: BLE001 — one source failing must not block the rest
                    logger.exception(
                        "sweep-persist: extraction failed for %s/%s",
                        enterprise_id, source.key,
                    )
            logger.info(
                "sweep-persist: %s written=%d skipped=%d of %d source(s)",
                enterprise_id, written, skipped, len(sources),
            )
        except Exception:  # noqa: BLE001 — fully isolated, see docstring
            logger.exception("sweep-persist: run failed for %s", enterprise_id)


def kickoff_sweep_persist(enterprise_id: str, result: "SweepResult | None") -> bool:
    """Fire-and-forget: persist a completed sweep's reads.

    Called from `qa_agent._sweep_context` right after a sweep's block has
    already been rendered for the prompt — this is strictly additional work,
    never a precondition for the answer. Returns False (nothing started) when
    the flag is off, there is no tenant, or the sweep read nothing usable.
    Never blocks; never raises into the caller's ask flow.
    """
    if not enterprise_id or result is None:
        return False
    try:
        if not settings.sweep_kg_persist_enabled:
            return False
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
