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

PER-HIT ENRICHMENT, PERSIST-THREAD ONLY (added after the first build hit
byte-identity for Jira alone). AC4 byte-identity needs a sweep's record and
the scheduled pull's record for the same item to render EQUAL — and for
three of five live legs (clickup, confluence, hubspot) the sweep's own lean
search-hit shape genuinely lacks fields only the puller's full fetch carries
(see each adapter's `_row_to_record`/`_deal_row_to_record` docstring for the
exact gaps). Closing that gap needs one extra fetch per hit, which
`sweep.py`'s own module docstring forbids — correctly, for THAT module: its
wall-clock budget is shared across every live leg in flight for one chat
answer, and a fetch-per-hit there would blow it. That constraint does not
hold HERE: this thread starts AFTER the prompt block sweep.py rendered is
already in the model's hands, and `open_session(enterprise_id)` resolves
credentials from the DB the same way on this thread as on the request
thread — nothing about running in the background makes it unsafe. So
per-hit enrichment is allowed in this module, and ONLY this module:
`_enrich_source` below turns each eligible source's lean records into
puller-shaped ones (`clickup.enrich_record`, `confluence.enrich_record`,
`hubspot.enrich_record`) before hashing, bounded to
`_ENRICH_MAX_PER_SOURCE` hits and isolated per hit — a 404/timeout/other
failure drops just that one record back to its lean form rather than
aborting the source. Jira needs none of this (its own sweep-time single-hit
branch already fetches the full issue). Slack has no puller-shaped record to
enrich TOWARD at all — see connector_lookup/slack.py's `_match_to_record`
for why, and for what bounds its cost instead of hashing.

PER-PROVIDER COOLDOWN (`sweep_persist_cooldown`, `app/db/`). Enrichment adds
real HTTP cost against a customer's own API, paid on every question that
happens to sweep a source — and it happens BEFORE the ledger hash check
below, so the content-hash ledger alone cannot prevent paying for a fetch
about to be discarded as a duplicate. `_run` now checks the cooldown before
doing ANYTHING for a source — enrichment, hashing, extraction — and skips it
entirely when it was processed within `settings.pipeline_interval_hours`
(6h). This applies to all five providers, not just the three enrichable
ones: it is the ONLY mechanism that bounds Slack's cost (Slack cannot dedupe
against the pull at all), and it protects the other four on top of the
ledger. Persisted, not in-process (`app.db.sweep_persist_cooldown` — a
Supabase table, not a module-level dict), so it survives a restart; see that
module's docstring for why it is a separate table from `kg_ingest_ledger`
rather than a reuse of one.
"""
from __future__ import annotations

import logging
import threading
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from app.connector_lookup.sweep import SourceResult, SweepResult
    from app.kg_ingest.types import RawRecord

logger = logging.getLogger(__name__)

#: Top-K hits enriched per source per persistence run (AC-A3). Bounds the
#: persist thread's own fan-out against a customer's API — enrichment costs
#: one extra HTTP call per hit (the whole point of the amendment above), and
#: a broad sweep can surface more hits than that is worth paying for on a
#: background thread every time someone asks a fuzzy question. Five, chosen
#: to comfortably cover a focused sweep result without turning "what's the
#: status of the pricing project" into fifteen ClickUp/Confluence/HubSpot
#: fetches behind the scenes. A hit past this cap is NOT dropped — it is
#: hashed/extracted in its lean, un-enriched form, same as before this
#: amendment (AC6's fallback still applies per-record, not just per-source).
_ENRICH_MAX_PER_SOURCE = 5

# Per-company locks so two asks that both trigger a sweep around the same time
# serialize their persistence instead of extracting the same fresh content in
# parallel — mirrors auto_sync's `_corpus_seed_lock`. Serializing (rather than
# dropping the second one) means the second run still lands anything the first
# missed; the content-hash ledger makes the overlap a cheap no-op, not a
# repeated LLM call.
_locks: dict[str, threading.Lock] = {}
_locks_guard = threading.Lock()


def is_persistable(source: "SourceResult") -> bool:
    """May this source's content be written into the tenant's graph?

    THE STRUCTURAL FAIL-CLOSED RULE. `SweepResult.read` filters on `usable` —
    STATUS_OK plus non-empty text — and that turned out to be a property of the
    ADAPTER'S HONESTY rather than of the data. An adapter that returned a
    carefully-worded failure sentence instead of raising was, to
    `sweep._run_live`, a source that had been read; its error string flowed
    straight through here into `extract_document` and became a signal in a
    customer's graph on the shared prod Supabase. That happened (the Meet
    listing paths, fixed in the adapters), and fixing the adapters alone leaves
    the next adapter free to reintroduce it.

    So the gate is now a property of the DATA: **only a source carrying
    structured `RawRecord`s is persistable.** No adapter builds a RawRecord out
    of a timeout; records exist only where a real fetch returned real rows. A
    future adapter that returns prose on failure now produces a leg that is
    rendered to the model (where an honest failure sentence belongs) and
    written nowhere.

    THE PROMPT TAKES PROSE, THE GRAPH TAKES ONLY RECORDS. That split is the
    whole idea, and it closes a second hole nobody had noticed: an honest EMPTY
    result — "(no Asana task TITLE matches these terms)", "(no Slack message
    contains 'x')" — was also `usable`, also records-free, and was therefore
    also being extracted into the graph. An absence statement is the last thing
    that should become evidence.

    WHAT THIS GIVES UP, stated rather than glossed: the two local legs (`calls`,
    `github`) carry no records, so they stop being persisted. That costs
    nothing real — both read from tables our own pullers already ingested
    (`call_index` rows come from the Fireflies/Zoom pullers, `github` from the
    synced `github_pull_requests`), so persisting them re-extracted content the
    KG already had by another route. `_hashable_units`' own docstring already
    called the text-hashing path "the narrower, degraded case": it could never
    collide with the scheduled pull's hash, so it only ever deduped a sweep
    against a previous identical sweep.
    """
    from app.connector_lookup.sweep import STATUS_OK

    return (
        source.status == STATUS_OK
        and bool((source.text or "").strip())
        and bool(source.records)
    )


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
    records: "list[RawRecord] | None", text: str
) -> "list[tuple[str, RawRecord | None]]":
    """`[(rendered, record_or_None), ...]` — one entry per thing `_run` will
    hash/extract for one source. `records` (when present) yields one entry
    PER RECORD (AC6: records are used when present); their absence yields one
    entry for the whole `text` (AC6: falls back to today's behaviour). Never
    both — records, when present, REPLACE text-hashing rather than adding to
    it, so a source is never double-counted.

    Takes `records`/`text` directly rather than a `SourceResult` so the
    caller can pass the ENRICHED record list (`_enrich_source`) without
    mutating the `SourceResult` itself — see that function's docstring.
    """
    if records:
        return [(record.render(), record) for record in records]
    return [(text, None)]


def _enrichers() -> "dict[str, object]":
    """Provider key -> `enrich_record(session, record) -> RawRecord`, for the
    three providers whose sweep-time record is genuinely leaner than the
    puller's (AC-A1). Lazily imported — same reason `_run` imports its own
    dependencies lazily: this module stays cheap to import for callers that
    never trigger persistence, and it sidesteps any import-order coupling
    with the adapter modules.
    """
    from app.connector_lookup import clickup, confluence, hubspot

    return {
        "clickup": clickup.enrich_record,
        "confluence": confluence.enrich_record,
        "hubspot": hubspot.enrich_record,
    }


def _enrich_source(
    enterprise_id: str, source: "SourceResult"
) -> "list[RawRecord] | None":
    """`source.records`, with persist-thread-only per-hit enrichment applied
    where this provider has one (AC-A1). Never mutates `source.records`
    itself — sweep_persist runs concurrently with nothing else touching this
    SweepResult, but the SourceResult objects are shared with whatever
    already rendered the prompt block, and mutating them in place is
    unnecessary risk for no benefit.

    Bounded to `_ENRICH_MAX_PER_SOURCE` (AC-A3) and per-hit isolated
    (AC-A4): a hit past the cap, or one whose fetch raises, is returned
    UNENRICHED (the original lean record) rather than dropped or aborting
    the source — the run keeps going either way.
    """
    records = source.records
    if not records:
        return records
    enrich = _enrichers().get(source.key)
    if enrich is None:
        return records
    from app.connector_lookup import registry

    adapter = registry.provider_for(source.key)
    if adapter is None:
        return records
    try:
        session = adapter.open_session(enterprise_id)
    except Exception:  # noqa: BLE001 — a session that can't open enriches nothing
        logger.warning(
            "sweep-persist: enrichment session failed to open for %s/%s",
            enterprise_id, source.key, exc_info=True,
        )
        return records
    if session is None:
        return records
    out: list = []
    for i, record in enumerate(records):
        if i >= _ENRICH_MAX_PER_SOURCE:
            out.append(record)  # past the cap — unenriched, not dropped (AC-A3)
            continue
        try:
            out.append(enrich(session, record))
        except Exception:  # noqa: BLE001 — one hit's failure must not drop the rest (AC-A4)
            logger.info(
                "sweep-persist: enrichment failed for %s/%s/%s (keeping the "
                "lean record)",
                enterprise_id, source.key, record.external_id, exc_info=True,
            )
            out.append(record)
    return out


def _run(enterprise_id: str, sources: "list[SourceResult]") -> None:
    """Blocking persistence — runs inside the daemon thread only.

    Fully isolated: any failure — including `GraphFacade()` construction, a
    ledger outage, or an extraction blow-up — is logged, never raised. Same
    one-try-around-everything shape as `auto_sync._run_corpus_seed`: the ask
    this sweep served has already been answered by the time this runs, so
    nothing here can reach the user regardless of where it fails. Serialized
    per company via `_lock_for` (see above).
    """
    from app.config import settings
    from app.db import sweep_persist_cooldown as cooldown
    from app.db.kg_ingest_ledger import record_hashes, seen_hashes
    from app.graph.extractor import extract_document
    from app.graph.facade import GraphFacade

    lock = _lock_for(enterprise_id)
    with lock:
        try:
            # Per-(company, provider) cooldown, ALL FIVE providers (AC-A2):
            # a source processed within the last `pipeline_interval_hours`
            # is skipped ENTIRELY — no enrichment fetch, no ledger read, no
            # extraction (AC-A5) — before anything else in this run touches
            # it. Checked (and fail-open) per source, not once for the whole
            # run, so one company sweeping jira+clickup in the same minute
            # doesn't have clickup's cooldown gate jira's turn too.
            interval_hours = getattr(settings, "pipeline_interval_hours", 6)
            # THE INVARIANT, re-checked HERE and not only at the caller. `_run`
            # is the only thing in this codebase that writes a sweep's content
            # to a graph, so it is the choke point, and a guarantee that lives
            # only in `kickoff_sweep_persist` is one a future direct caller can
            # walk around. See `is_persistable`.
            refused = [s.key for s in sources if not is_persistable(s)]
            sources = [s for s in sources if is_persistable(s)]
            if refused:
                logger.info(
                    "sweep-persist: %s refused %s — no structured records, so "
                    "nothing about them is written to the graph",
                    enterprise_id, ",".join(refused),
                )
            if not sources:
                return

            active_sources: list = []
            cooled_providers: list[str] = []
            for source in sources:
                if cooldown.in_cooldown(
                    enterprise_id, source.key, hours=interval_hours
                ):
                    cooled_providers.append(source.key)
                    continue
                active_sources.append(source)
            if cooled_providers:
                logger.info(
                    "sweep-persist: %s provider(s) %s in cooldown "
                    "(< %sh since their last run) — zero enrichment, zero "
                    "extraction this run",
                    enterprise_id, ",".join(cooled_providers), interval_hours,
                )

            # One item per record when a source has records (AC6), else one
            # item for the whole source text — see `_hashable_units`. Records
            # are the PERSIST-THREAD-ENRICHED list (AC-A1), not
            # `source.records` directly — see `_enrich_source`.
            items = [
                (source, record, _content_hash(rendered))
                for source in active_sources
                for rendered, record in _hashable_units(
                    _enrich_source(enterprise_id, source), source.text
                )
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
            # Mark cooldown for every source actually processed this run —
            # regardless of whether it ended up written, skipped, or a mix.
            # Best-effort per source, so one write failure doesn't stop the
            # rest from being marked.
            for source in active_sources:
                cooldown.mark_run(enterprise_id, source.key)
            logger.info(
                "sweep-persist: %s written=%d skipped=%d of %d item(s) "
                "across %d/%d source(s) (%d in cooldown)",
                enterprise_id, written, skipped, len(items),
                len(active_sources), len(sources), len(cooled_providers),
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
        # `.read` filters to sources with status ok and non-empty text. That is
        # necessary and NOT sufficient — it is a claim the adapter makes about
        # itself, and one adapter's honest-sounding failure sentence satisfied
        # it all the way into a tenant's graph. `is_persistable` adds the
        # structural half (real records, not prose) and `_run` re-checks it, so
        # this filter is the cheap early-out rather than the guarantee.
        sources = [s for s in result.read if is_persistable(s)]
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
