"""Company-scoped Fireflies KG re-extraction, on the Anthropic Message
Batches API — the "fire when needed" replacement for a hand-written box
script whenever the extraction prompt, taxonomy, or model changes, a new
tenant's history needs backfilling, or a fix needs to be re-applied to
already-ingested calls.

    # Dry run is the DEFAULT — pulls the window and reports a count + an
    # estimated cost, writes NOTHING:
    python scripts/backfill_fireflies_kg.py --company <enterprise_id>

    # Then, only with explicit owner approval for the target environment:
    python scripts/backfill_fireflies_kg.py --company <enterprise_id> --run

Fireflies-only (YAGNI) — Zoom/Meet need a different condensed main-pass input
(a `claude-haiku-4-5` summary, not a free digest) and are not wired up here;
``--provider`` other than ``fireflies`` errors clearly rather than silently
doing the wrong thing.

Company-scoped, MANDATORY (``--company``) — this tool refuses to run without
one. Never all-tenants.

CURSOR-SAFE: pulls via the puller's EXPLICIT WINDOW path
(``fireflies.pull(..., since=, until=)``), which never reads or stamps the
shared prod+staging incremental cursor (``kg_last_synced_until`` on the
connection row) — see ``app.kg_ingest.pullers.fireflies.pull``. Running this
can never desynchronize the regular scheduled sync.

BATCH-FIRST, SYNC-FALLBACK, CHUNKED: every call's main pass + checklist pass
(the same per-call split ``app.kg_ingest.runner.sync_provider`` uses for
Fireflies) is submitted as a bulk `app.llm_batch.run_batch` call — half the
live price, and off the shared interactive concurrency gate. Calls are
processed in chunks of ``CHUNK_SIZE`` (one `run_batch` submission per chunk,
each waited on for up to ``--batch-deadline-hours``) rather than one giant
batch, so a stalled or slow chunk only falls back to synchronous extraction
for ITS calls, not the whole run. If batching is off, the company key isn't
Anthropic, the batch API errors, or a chunk's deadline passes, `run_batch`
returns `None` for that chunk and this script falls back to the existing
SYNCHRONOUS extraction path for that chunk's calls (correctness over cost) —
never a partial/silent loss.

IDEMPOTENT: the content-hash ledger (``app.db.kg_ingest_ledger``) dedups
already-extracted calls exactly like a normal sync, so re-running this is
safe and free for calls it already processed.

Running this against a real tenant needs Babajide's explicit permission —
this script is safe to ship, but FIRING it (``--run``) against staging or
production is an owner decision, per-environment, not implied by shipping it.
"""
from __future__ import annotations

import argparse
import json
import logging
import re
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app import db  # noqa: E402
from app.connectors.tokens import decrypt_token_json  # noqa: E402
from app.graph.extractor import (  # noqa: E402
    build_checklist_request,
    build_extract_request,
    extract_document,
    parse_checklist_response,
    parse_extract_response,
    run_checklist_pass,
)
from app.graph.facade import GraphFacade  # noqa: E402
from app.db.kg_ingest_ledger import record_hashes, seen_hashes  # noqa: E402
from app.kg_ingest.runner import _condensed_and_full_text, _content_hash, token_for  # noqa: E402
from app.kg_ingest.pullers import fireflies  # noqa: E402
from app.kg_ingest.types import RawRecord  # noqa: E402
from app.llm_batch import BatchRequest, BATCH_COST_MULTIPLIER, run_batch  # noqa: E402
from app.llm_keys import company_llm_key  # noqa: E402
from app.usage_context import feature_for_agent, usage_scope  # noqa: E402

#: How many CALLS (each = 2 requests, main + checklist) go into one bulk
#: `run_batch` submission. Live-verify (2026-08-27) submitted 798 requests
#: (399 calls) as ONE batch; chunking bounds the blast radius of a stalled or
#: slow submission — if one chunk misses its deadline, only THAT chunk falls
#: back to sync, not the whole run — and keeps each individual `messages.batches.create`
#: call comfortably small. Not a hard API ceiling (Anthropic's own limit is
#: far higher); this is a robustness choice, not a workaround for a real cap.
CHUNK_SIZE = 250

#: Generous default wait for one chunk's batch to finish before falling back
#: to sync for that chunk. `app.llm_batch.DEFAULT_DEADLINE_S` (900s / 15 min)
#: is sized for the tiny, single-request callers `gateway.llm_call`'s
#: `batch=True` opt-in makes — NOT for a bulk backfill chunk of up to
#: `CHUNK_SIZE` calls. Live-verify's first real run submitted 798 requests as
#: one batch, missed the 15-minute default, and silently fell back to
#: full-price SEQUENTIAL sync extraction for all 399 calls — exactly the slow/
#: expensive path this tool exists to avoid. The Batches API's own SLA is 24h;
#: bulk batches this size routinely take tens of minutes to a couple of hours,
#: so 4h is generous headroom without being unbounded. Overridable per run via
#: `--batch-deadline-hours` for an operator backfilling a very large history.
DEFAULT_BATCH_DEADLINE_HOURS = 4.0

logger = logging.getLogger("backfill_fireflies_kg")

#: Providers this tool actually knows how to backfill. See the module
#: docstring — Zoom/Meet need a Haiku-summary condensed input this script
#: does not build; every other value errors clearly rather than silently
#: doing the wrong thing.
SUPPORTED_PROVIDERS: frozenset[str] = frozenset({"fireflies"})

#: The `agent` label every extraction call this tool makes is attributed
#: under — the SAME string on both transports (batch's usage_scope below AND
#: the sync-fallback's extract_document/run_checklist_pass calls) so the two
#: paths land in the same feature bucket rather than splitting cost
#: attribution by which transport happened to run.
_AGENT = "kg-backfill:fireflies"

#: Live (non-batch), measured Config-B cost for ONE Fireflies call's two
#: passes (main + checklist) — see `app.kg_ingest.runner`'s
#: `_condensed_and_full_text` docstring ("measured ~$0.13/call for
#: Fireflies"). The dry-run estimate below applies `BATCH_COST_MULTIPLIER`
#: on top. This is a ROUGH estimate grounded in a historical production
#: measurement, not a live token count — the actual cost is only known once
#: `--run` is fired and metering records the real usage.
_MEASURED_LIVE_COST_PER_CALL_USD = 0.13

#: The real Anthropic Batches API rejects any `custom_id` outside
#: `^[a-zA-Z0-9_-]{1,64}$` — discovered live (2026-08-27): the original
#: `"<external_id>:main"` / `":checklist"` separator 400s the WHOLE batch
#: submission ("String should match pattern"), which `run_batch` treats as a
#: hard failure and silently falls back to full-price sync for every call,
#: not just the offending one. `-` replaces `:` as the separator (verified
#: live), and `_CUSTOM_ID_UNSAFE_RE` sanitizes the external_id defensively —
#: Fireflies ids are alphanumeric in practice, but a batch is all-or-nothing,
#: so a single stray character must never be able to take the whole
#: submission down silently.
_CUSTOM_ID_UNSAFE_RE = re.compile(r"[^a-zA-Z0-9_-]")
_MAIN_SUFFIX = "-main"
_CHECKLIST_SUFFIX = "-checklist"
_MAX_CUSTOM_ID_LEN = 64


def _parse_since_days(raw: str) -> int:
    """Accepts a bare integer or an integer with a trailing 'd' (days) —
    e.g. ``"365"`` or ``"365d"``. Always days; there is no other unit."""
    s = raw.strip().lower()
    if s.endswith("d"):
        s = s[:-1]
    return int(s)


def _estimate_dry_run_cost(n_calls: int) -> float:
    """Rough estimated USD for a batch run of `n_calls` Fireflies calls (main
    + checklist pass each), at the batch (0.5x live) rate. See
    `_MEASURED_LIVE_COST_PER_CALL_USD`."""
    return n_calls * _MEASURED_LIVE_COST_PER_CALL_USD * BATCH_COST_MULTIPLIER


def _custom_id_prefix(external_id: str) -> str:
    """A `custom_id`-safe prefix for one call's two batch requests. See
    `_CUSTOM_ID_UNSAFE_RE`'s module-level comment for why this exists.

    Strips any character outside `[a-zA-Z0-9_-]` (replaced with `_`) and caps
    the length so `<prefix><_CHECKLIST_SUFFIX>` — the longer of the two
    suffixes — never exceeds `_MAX_CUSTOM_ID_LEN`. Logs loudly when it had to
    change anything: Fireflies ids are alphanumeric in practice, so this
    should be a no-op every real time it runs, and a silent change here is
    exactly the kind of thing that quietly starts colliding two different
    calls onto the same custom_id.
    """
    safe = _CUSTOM_ID_UNSAFE_RE.sub("_", external_id)
    max_prefix_len = _MAX_CUSTOM_ID_LEN - len(_CHECKLIST_SUFFIX)
    truncated = safe[:max_prefix_len]
    if truncated != external_id:
        logger.warning(
            "fireflies external_id %r is not custom_id-safe as-is — "
            "sanitized to %r for this batch. In the unlikely event two "
            "external_ids sanitize to the same prefix their custom_ids "
            "would collide; investigate if that ever shows up.",
            external_id, truncated,
        )
    return truncated


def _fetch_api_key(enterprise_id: str) -> str:
    """The decrypted Fireflies API key for `enterprise_id`'s connection.
    Raises ValueError with a clear message if there is no connection."""
    row = db.get_connection(enterprise_id, "fireflies")
    if not row:
        raise ValueError(f"no fireflies connection for company {enterprise_id!r}")
    token_json = json.loads(decrypt_token_json(row["token_json_encrypted"]))
    return token_for("fireflies", token_json)


def _build_batch_requests(
    fresh: list[RawRecord], enterprise_id: str,
) -> tuple[list[BatchRequest], dict[str, tuple[str, RawRecord, str, str]]]:
    """Build ONE `BatchRequest` per pass (main + checklist) across EVERY
    fresh call — the bulk batch this script hands to `run_batch` in one shot.

    Returns `(requests, unit_by_id)`: `unit_by_id[custom_id]` is
    `(pass_name, record, doc_name, pass_text)` — everything
    `parse_extract_response` / `parse_checklist_response` need once
    `run_batch` returns results keyed the same way. `custom_id` is
    `<custom_id_prefix><_MAIN_SUFFIX|_CHECKLIST_SUFFIX>` — see
    `_custom_id_prefix` — unique per call since Fireflies external_ids are
    themselves unique. The "round trip" (batch result -> which call/pass it
    belongs to) is a plain dict lookup on the FULL `custom_id` string
    (`unit_by_id[custom_id]`, in `_run_batched` below) — nothing anywhere
    parses or splits the id back apart, so it holds regardless of what
    separator/characters the id itself is made of.
    """
    requests: list[BatchRequest] = []
    unit_by_id: dict[str, tuple[str, RawRecord, str, str]] = {}
    for rec in fresh:
        main_text, checklist_text = _condensed_and_full_text(
            "fireflies", rec, enterprise_id
        )
        doc_name = f"fireflies-backfill-{rec.external_id}"
        prefix = _custom_id_prefix(rec.external_id)
        main_id = f"{prefix}{_MAIN_SUFFIX}"
        checklist_id = f"{prefix}{_CHECKLIST_SUFFIX}"
        requests.append(BatchRequest(
            main_id, build_extract_request(doc_name=doc_name, text=main_text),
        ))
        requests.append(BatchRequest(
            checklist_id,
            build_checklist_request(doc_name=doc_name, text=checklist_text),
        ))
        unit_by_id[main_id] = ("main", rec, doc_name, main_text)
        unit_by_id[checklist_id] = ("checklist", rec, doc_name, checklist_text)
    return requests, unit_by_id


def _accumulate(tally: dict, result: dict) -> None:
    for k in ("signals", "themes", "skipped"):
        tally[k] += result.get(k, 0)


def _run_sync_fallback(
    facade: GraphFacade, enterprise_id: str, fresh: list[RawRecord],
    hashes: dict[int, str],
) -> tuple[dict, set[str]]:
    """The existing SYNC extraction path, per call — used when `run_batch`
    returns `None` for the whole submission (batching off / non-Anthropic key
    / API error / deadline). Correctness over cost: every call still gets
    extracted, just at the live (non-batch) price. Mirrors
    `app.kg_ingest.runner.sync_provider`'s per-call error isolation — one bad
    call is logged and skipped, not fatal to the run."""
    tally = {"signals": 0, "themes": 0, "skipped": 0}
    extracted: set[str] = set()
    for rec in fresh:
        main_text, checklist_text = _condensed_and_full_text(
            "fireflies", rec, enterprise_id
        )
        doc_name = f"fireflies-backfill-{rec.external_id}"
        source_ref = ("fireflies", rec.external_id)
        try:
            r = extract_document(
                facade, enterprise_id, doc_name=doc_name, text=main_text,
                agent=_AGENT, origin="connector",
                source_ref=source_ref,
            )
            _accumulate(tally, r)
            c = run_checklist_pass(
                facade, enterprise_id, doc_name=doc_name, text=checklist_text,
                agent=_AGENT, origin="connector",
                source_ref=source_ref,
            )
            _accumulate(tally, c)
            record_hashes(enterprise_id, "fireflies", [hashes[id(rec)]])
            extracted.add(rec.external_id)
        except Exception:  # noqa: BLE001 — per-call isolation, matches sync_provider
            logger.exception("sync-fallback extraction failed for call %s",
                             rec.external_id)
    return tally, extracted


def _submit_batch(
    enterprise_id: str, requests: list[BatchRequest], *,
    deadline_s: float = DEFAULT_BATCH_DEADLINE_HOURS * 3600,
) -> dict | None:
    """`run_batch(requests, deadline_s=deadline_s)`, ATTRIBUTED — live-verify
    (2026-08-27) found the CLI calling `run_batch` bare left every batched
    usage row unattributed: `app.llm_metering.record_external_usage` reads
    the acting company off `app.llm_keys.current_company_id()` and the
    feature/operation label off `app.usage_context`'s scope, both ContextVars
    that are normally bound by `gateway.llm_call`'s own
    `with company_llm_key(enterprise_id), usage_scope(...)` block — which this
    CLI, calling `run_batch` directly rather than through the gateway, never
    enters. Unwrapped, `run_batch` still records usage ROWS (0.5x
    cost_multiplier is correct either way) but with company/feature both
    `None`/unattributed, invisible on any per-company or per-feature cost
    view. This binds the SAME two context managers `llm_call` uses, with the
    SAME `feature_for_agent(_AGENT)` mapping every other extraction call this
    tool makes resolves to (see `_AGENT`), so batched spend lands in the
    identical company + feature bucket the sync-fallback path's calls would.

    `deadline_s` defaults to the generous `DEFAULT_BATCH_DEADLINE_HOURS`, NOT
    `app.llm_batch.DEFAULT_DEADLINE_S` (900s/15min, sized for a single-request
    caller) — see that constant's module-level comment. Always passed
    explicitly by `_process_chunk` below; the default here only covers a
    caller that invokes this directly (e.g. a test).
    """
    with company_llm_key(enterprise_id), usage_scope(
        feature=feature_for_agent(_AGENT), operation="kg_backfill"
    ):
        return run_batch(requests, label="kg-backfill:fireflies", deadline_s=deadline_s)


def _chunks(items: list, size: int):
    """Yield successive `size`-length slices of `items` (the last one short).
    Plain index slicing — no `RawRecord` identity is copied or mutated, so
    `id(rec)`-keyed structures (the ledger `hashes` dict) stay valid across
    chunks."""
    for i in range(0, len(items), size):
        yield items[i:i + size]


def _process_chunk(
    facade: GraphFacade, enterprise_id: str, chunk: list[RawRecord],
    hashes: dict[int, str], deadline_s: float,
) -> tuple[dict, set[str], bool]:
    """Submit ONE chunk's calls as one bulk batch and write its results —
    the per-chunk unit of work `main`'s loop drives. Returns
    `(tally, extracted, used_batch)`; `used_batch` is False when this chunk
    fell back to sync (batching off / API error / THIS CHUNK's deadline),
    which only affects this chunk, never the others — the whole point of
    chunking (see `CHUNK_SIZE`'s module-level comment)."""
    requests, unit_by_id = _build_batch_requests(chunk, enterprise_id)
    logger.info(
        "submitting %d request(s) for a %d-call chunk (deadline=%.0fs)",
        len(requests), len(chunk), deadline_s,
    )
    results = _submit_batch(enterprise_id, requests, deadline_s=deadline_s)
    if results is None:
        logger.warning(
            "run_batch returned None for this chunk (batching disabled / "
            "non-Anthropic key / API error / deadline) — falling back to "
            "the existing SYNC path for just these %d call(s), at the live "
            "not batch rate", len(chunk),
        )
        tally, extracted = _run_sync_fallback(facade, enterprise_id, chunk, hashes)
        return tally, extracted, False
    tally, extracted = _run_batched(
        facade, enterprise_id, chunk, hashes, unit_by_id, results,
    )
    return tally, extracted, True


def _run_batched(
    facade: GraphFacade, enterprise_id: str, fresh: list[RawRecord],
    hashes: dict[int, str], unit_by_id: dict[str, tuple[str, RawRecord, str, str]],
    results: dict,
) -> tuple[dict, set[str]]:
    """Parse `run_batch`'s results into signals, per call, and advance the
    ledger for every call whose passes came back — a call missing BOTH
    results (both passes errored inside the batch) is skipped and logged; its
    hash is never recorded, so the next run retries it."""
    tally = {"signals": 0, "themes": 0, "skipped": 0}
    extracted: set[str] = set()
    by_call: dict[str, dict[str, tuple]] = {}
    for custom_id, message in results.items():
        pass_name, rec, doc_name, text = unit_by_id[custom_id]
        by_call.setdefault(rec.external_id, {})[pass_name] = (doc_name, text, message)

    for rec in fresh:
        entry = by_call.get(rec.external_id, {})
        if not entry:
            logger.warning(
                "no batch result for call %s (both passes missing/errored) — "
                "skipping; ledger not advanced, retried on the next run",
                rec.external_id,
            )
            continue
        source_ref = ("fireflies", rec.external_id)
        try:
            if "main" in entry:
                doc_name, _text, message = entry["main"]
                r = parse_extract_response(
                    facade, enterprise_id, message, doc_name=doc_name,
                    origin="connector", source_ref=source_ref,
                )
                _accumulate(tally, r)
            else:
                logger.warning("main pass missing for call %s", rec.external_id)
            if "checklist" in entry:
                doc_name, text, message = entry["checklist"]
                c = parse_checklist_response(
                    facade, enterprise_id, message, doc_name=doc_name, text=text,
                    origin="connector", source_ref=source_ref,
                )
                _accumulate(tally, c)
            else:
                logger.warning("checklist pass missing for call %s", rec.external_id)
            record_hashes(enterprise_id, "fireflies", [hashes[id(rec)]])
            extracted.add(rec.external_id)
        except Exception:  # noqa: BLE001 — per-call isolation, matches sync_provider
            logger.exception("batch-result write failed for call %s", rec.external_id)
    return tally, extracted


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--company", required=True,
                    help="enterprise_id to backfill — required, never all-tenants")
    ap.add_argument("--provider", default="fireflies",
                    help="only 'fireflies' is implemented today")
    ap.add_argument("--since", default="365d",
                    help="how far back to pull, in days (e.g. 30 or 30d); default 365d")
    ap.add_argument("--limit", type=int, default=500,
                    help="max calls to pull; default 500")
    ap.add_argument("--run", action="store_true",
                    help="actually execute (default is a read-only dry run)")
    ap.add_argument("--batch-deadline-hours", type=float,
                    default=DEFAULT_BATCH_DEADLINE_HOURS,
                    help="how long to wait for one chunk's batch to finish "
                         "before falling back to sync for that chunk, in "
                         f"hours; default {DEFAULT_BATCH_DEADLINE_HOURS}")
    args = ap.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(message)s")

    if args.provider not in SUPPORTED_PROVIDERS:
        print(
            f"error: --provider={args.provider!r} is not implemented — only "
            f"{sorted(SUPPORTED_PROVIDERS)} today. Zoom/Meet need a different "
            f"condensed main-pass input (a claude-haiku-4-5 summary, not a "
            f"free digest) and are not wired up here.",
            file=sys.stderr,
        )
        return 2

    enterprise_id = args.company
    dry_run = not args.run
    since_days = _parse_since_days(args.since)
    until = datetime.now(timezone.utc)
    since = until - timedelta(days=since_days)

    try:
        api_key = _fetch_api_key(enterprise_id)
    except ValueError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1

    logger.info("pulling fireflies calls for company=%s since=%s until=%s limit=%d "
               "(explicit window — the shared incremental sync cursor is untouched)",
               enterprise_id, since.date().isoformat(), until.date().isoformat(),
               args.limit)
    records = list(fireflies.pull(
        api_key, enterprise_id=enterprise_id, since=since, until=until,
        limit=args.limit,
    ))
    logger.info("pulled %d call(s)", len(records))

    # Ledger dedupe — identical semantics to app.kg_ingest.runner.sync_provider:
    # a record whose content hash was already extracted is skipped before any
    # model call, so a re-run of this tool is free for calls it already did.
    hashes = {id(r): _content_hash(r.render()) for r in records}
    seen = seen_hashes(enterprise_id, list(set(hashes.values())))
    fresh = [r for r in records if hashes[id(r)] not in seen]
    logger.info("%d fresh of %d (already-extracted, ledger-skipped: %d)",
               len(fresh), len(records), len(records) - len(fresh))

    if not fresh:
        logger.info("nothing to do")
        return 0

    if dry_run:
        est_cost = _estimate_dry_run_cost(len(fresh))
        logger.info(
            "DRY RUN — would extract %d call(s), %d LLM pass(es) (main + "
            "checklist per call), estimated cost ~$%.2f (batch rate, %.1fx "
            "live) — writing NOTHING. Re-run with --run to execute.",
            len(fresh), len(fresh) * 2, est_cost, BATCH_COST_MULTIPLIER,
        )
        return 0

    facade = GraphFacade()
    deadline_s = args.batch_deadline_hours * 3600
    chunks = list(_chunks(fresh, CHUNK_SIZE))
    logger.info(
        "processing %d call(s) as %d chunk(s) of up to %d call(s) each "
        "(batch deadline %.1fh per chunk)",
        len(fresh), len(chunks), CHUNK_SIZE, args.batch_deadline_hours,
    )

    tally = {"signals": 0, "themes": 0, "skipped": 0}
    extracted: set[str] = set()
    any_batch = any_fallback = False
    for i, chunk in enumerate(chunks):
        logger.info("chunk %d/%d — %d call(s)", i + 1, len(chunks), len(chunk))
        chunk_tally, chunk_extracted, used_batch = _process_chunk(
            facade, enterprise_id, chunk, hashes, deadline_s,
        )
        _accumulate(tally, chunk_tally)
        extracted |= chunk_extracted
        if used_batch:
            any_batch = True
        else:
            any_fallback = True

    if any_batch and any_fallback:
        transport = "mixed (some chunks batched, some fell back to sync)"
    elif any_batch:
        transport = "batch"
    else:
        transport = "sync-fallback"

    logger.info(
        "done — calls=%d extracted=%d signals=%d themes=%d skipped=%d "
        "transport=%s",
        len(fresh), len(extracted), tally["signals"], tally["themes"],
        tally["skipped"], transport,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
