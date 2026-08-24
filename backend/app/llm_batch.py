"""Anthropic Message Batches — the same model calls at 50% of the price.

WHY THIS EXISTS. Measured over 7 days of production telemetry, 46% of LLM spend
(~$1,247/mo of a ~$2,731/mo run-rate) is work with NO HUMAN WAITING ON IT:
connector ingest running in a background thread, the document-catalog backfill,
and the scheduled brief that starts generating three hours before it is
delivered. Every one of those pays interactive prices for a latency guarantee
nobody consumes. The Batches API charges half for exactly the same request.

  measured, 2026-08-24, a 4-request batch on claude-sonnet-4-6:
    ended after 123s
    cache_creation=7,222  cache_read=7,222   <- prompt caching works INSIDE a
                                                batch, so the two discounts
                                                compose (0.5x batch on top of
                                                0.1x cache read)

WHAT THIS IS NOT. It is not a queue, a scheduler, or a retry system. It is one
blocking call that happens to cost half: hand it N independent requests, it
returns N results or it returns None. `None` means "batching did not work out"
and the caller runs its normal synchronous path — so every call site keeps
working exactly as before if the batch API is unavailable, slow, or the company
is on a non-Anthropic key.

THREE THINGS THAT ARE EASY TO GET WRONG, all handled here:

  * DOUBLE BILLING. A batch that misses its deadline has still been submitted
    and will still be processed and billed. Falling back to synchronous calls
    without cancelling would pay for both. `run_batch` cancels before it gives
    up.
  * SILENT MIS-METERING. `llm_metering.install_metering` instruments
    `client.messages`; the batches endpoint never goes through it, so batch
    spend would be invisible. Results are metered explicitly here, at the
    halved rate — otherwise the cost dashboard would over-report batched work
    by exactly 2x and the saving would look like it never happened.
  * HOLDING THE CONCURRENCY GATE. A batch is processed on Anthropic's side, not
    on this box, so it deliberately does NOT take an `app.llm._llm_gate` slot.
    Moving background work onto this path therefore frees interactive capacity
    as well as money — the gate is only 6 wide and every interactive request
    queues behind it.
"""
from __future__ import annotations

import logging
import time
from dataclasses import dataclass
from typing import Any, Optional

from app.config import settings
from app.llm_providers import PROVIDER_ANTHROPIC

logger = logging.getLogger(__name__)

#: Anthropic's own ceiling is 24h; most batches finish in under an hour and the
#: one measured above took two minutes. This default is sized for the CALLER we
#: expect (a background sync with no deadline at all), not for the API: waiting
#: 15 minutes for half-price is a good trade for work nobody is watching, and
#: waiting an hour is not, because the next scheduler tick would have come round
#: anyway. Call sites with a real deadline (the brief's three-hour generation
#: lead) must pass their own.
DEFAULT_DEADLINE_S = 15 * 60

#: Poll spacing. Cheap (a HEAD-shaped GET), so the floor is set by politeness
#: rather than cost; the first poll is deliberately sooner because a small batch
#: can be done in well under a minute and waiting a full interval on a finished
#: batch is pure added latency.
_FIRST_POLL_S = 5.0
_POLL_S = 20.0

#: What a batched request costs relative to the same request sent normally.
#: Applied to the ESTIMATED cost only — the token counts recorded are the real
#: ones the API reported, so a future re-rating can always be derived.
BATCH_COST_MULTIPLIER = 0.5


@dataclass(frozen=True)
class BatchRequest:
    """One request in a batch.

    `params` is exactly what would have been passed to `messages.create` —
    model, max_tokens, system, messages, tools. Building it is the caller's job
    precisely so that the synchronous fallback can pass the SAME dict to
    `messages.create` and get an identical result; if this module built the
    params itself the two paths could drift.
    """

    custom_id: str
    params: dict


def batching_enabled() -> bool:
    """Master switch. Off by default — see `app.config.llm_batch_enabled`."""
    return bool(getattr(settings, "llm_batch_enabled", False))


def run_batch(
    requests: list[BatchRequest],
    *,
    deadline_s: float = DEFAULT_DEADLINE_S,
    label: str = "",
) -> Optional[dict[str, Any]]:
    """Run `requests` as one Message Batch. `{custom_id: Message}` or None.

    None is the "just do it normally" signal and is returned for every
    non-success: batching disabled, a non-Anthropic company key, an API error,
    or the deadline passing. It is never an exception — a cost optimisation
    must not be able to break the work it is optimising.

    Only SUCCEEDED results appear in the returned dict. A batch where some
    requests errored still returns the ones that worked, and the caller handles
    the missing custom_ids the same way it would handle any other failure —
    which for the ingest path means leaving those records out of the ledger so
    the next sync retries them.
    """
    if not requests:
        return None
    if not batching_enabled():
        return None

    # Batches are an Anthropic endpoint. A company on an OpenAI key has no
    # equivalent, so it silently keeps the synchronous path.
    try:
        from app.llm_keys import current_provider

        if current_provider() != PROVIDER_ANTHROPIC:
            return None
    except Exception:  # noqa: BLE001 — provider unknown -> don't batch
        return None

    started = time.monotonic()
    batch_id: str | None = None
    client = None
    try:
        from app.llm import get_client

        client = get_client()
        # `get_client` returns a METERED client (app.llm_metering swaps
        # `.messages`). `.messages.batches` is reached through that proxy, so
        # the proxy must pass unknown attributes through — it does, but the
        # batches endpoint is not instrumented by it either way, which is why
        # `_meter_results` below exists.
        batch = client.messages.batches.create(
            requests=[
                {"custom_id": r.custom_id, "params": r.params} for r in requests
            ]
        )
        batch_id = batch.id
        logger.info(
            "llm-batch: submitted %d request(s) as %s%s",
            len(requests), batch_id, f" ({label})" if label else "",
        )

        poll = _FIRST_POLL_S
        while True:
            remaining = deadline_s - (time.monotonic() - started)
            if remaining <= 0:
                # Deadline. Cancel BEFORE returning None: the caller is about to
                # run these requests synchronously and an uncancelled batch
                # would be billed alongside them.
                logger.warning(
                    "llm-batch: %s did not finish within %.0fs — cancelling and "
                    "falling back to synchronous calls%s",
                    batch_id, deadline_s, f" ({label})" if label else "",
                )
                _cancel(client, batch_id)
                return None
            time.sleep(min(poll, remaining))
            poll = _POLL_S
            batch = client.messages.batches.retrieve(batch_id)
            if batch.processing_status == "ended":
                break

        out: dict[str, Any] = {}
        errored = 0
        for result in client.messages.batches.results(batch_id):
            if getattr(result.result, "type", None) == "succeeded":
                out[result.custom_id] = result.result.message
            else:
                errored += 1
        _meter_results(out.values(), started_at=started)
        logger.info(
            "llm-batch: %s ended in %.0fs — %d succeeded, %d errored",
            batch_id, time.monotonic() - started, len(out), errored,
        )
        return out
    except Exception:  # noqa: BLE001 — never surface; fall back to sync
        logger.exception(
            "llm-batch: failed%s — falling back to synchronous calls",
            f" ({label})" if label else "",
        )
        if batch_id and client is not None:
            _cancel(client, batch_id)
        return None


def _cancel(client: Any, batch_id: str) -> None:
    """Best-effort cancel. Already-processed requests are still billed — this
    only stops the ones that have not started, which is the whole point of
    cancelling before a synchronous retry."""
    try:
        client.messages.batches.cancel(batch_id)
        logger.info("llm-batch: cancelled %s", batch_id)
    except Exception:  # noqa: BLE001 — a failed cancel must not mask the reason
        logger.warning("llm-batch: could not cancel %s", batch_id, exc_info=True)


def _meter_results(messages, *, started_at: float) -> None:
    """Write one usage row per batched result, priced at the batch rate.

    `install_metering` never sees these — it wraps `messages.create`, and the
    batches endpoint bypasses it entirely. Without this, batched spend would be
    missing from `llm_usage_events` altogether and the switch to batching would
    read on the dashboard as a mysterious drop in volume rather than a saving.

    Fail-soft, matching `llm_metering._record`: metering never breaks the work.
    """
    try:
        from app.llm import get_client
        from app.llm_metering import key_mode_of, record_external_usage

        key_mode = key_mode_of(get_client())
        for msg in messages:
            record_external_usage(
                key_mode=key_mode,
                provider=PROVIDER_ANTHROPIC,
                model=getattr(msg, "model", None),
                message=msg,
                started_at=started_at,
                cost_multiplier=BATCH_COST_MULTIPLIER,
            )
    except Exception:  # noqa: BLE001 — metering is never allowed to surface
        logger.exception("llm-batch usage metering failed (continuing)")
