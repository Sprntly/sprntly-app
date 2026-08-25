"""Thin wrapper over the Anthropic SDK — and, for companies that choose it, over
an OpenAI client wearing the same interface.

All `messages.create` calls go through `_create_with_retries`, which adds
exponential-backoff retries on transient failures (429 / 5xx / overloaded /
timeouts / connection drops) and a per-request timeout. Existing callers
(`call_json` / `call_md`) get this for free; the agent-facing gateway
(`app.graph.gateway.llm_call`) layers tenant context + telemetry on top.

`get_client()` returns whichever client the acting company's provider calls for
— a real `anthropic.Anthropic`, or `app.openai_client.OpenAIMessagesClient`,
which implements the same `messages.create` / `messages.stream` surface (see
that module for the translation). Everything below this line is written against
that shared surface and is provider-agnostic: the model id a caller passes is
translated to the equivalent OpenAI tier inside the client, so no call site,
prompt, or runner needs a provider conditional.
"""
import json
import logging
import random
import re
import threading
import time as _time
from functools import lru_cache
from typing import Any

import anthropic
from anthropic import Anthropic
from fastapi import HTTPException

from app.config import settings
from app.llm_metering import install_metering
from app.llm_providers import PROVIDER_ANTHROPIC, PROVIDER_OPENAI
from app.openai_client import OpenAIAPIError, OpenAIMessagesClient

logger = logging.getLogger(__name__)

# Either client this module may hand back. Both expose `messages.create` /
# `messages.stream` with the same kwargs and the same response shape.
LLMClient = Anthropic | OpenAIMessagesClient

DEFAULT_MODEL = "claude-sonnet-4-6"
# Deep-reasoning tier. Reserved for the handful of calls that are genuinely
# open-ended AND infrequent AND high-stakes — the top-insights composition and
# the onboarding business-context inference (each runs ~once per brief / per
# company and seeds everything downstream). Everything else — structured
# extraction, ranking, PRD templating, the per-message/loop paths — stays on
# DEFAULT_MODEL where opus would compound cost for marginal quality. Keep in
# sync with the pricing row in app/llm_telemetry.py (est_cost_usd fails closed).
# Opus tier is standardised on 4.7 (same value as the design-agent escalation
# model) so there is a single opus version across the codebase.
DEEP_MODEL = "claude-opus-4-7"

# Classifier tier. The mirror of DEEP_MODEL at the other end: calls that are
# HIGH-VOLUME, closed-set, and short-output — pick one of N labels, route one
# message. `app.qa_agent.ROUTER_MODEL` has always been this value; this is the
# same tier named where the other two live so non-router call sites can reach it
# without importing qa_agent (which pulls in the whole skill registry).
#
# Sized from production: classify_goal_fit ran 5,292 times in 30 days for 99
# output tokens a call and, on DEFAULT_MODEL, burned 15,444 model-seconds —
# 3.3% of ALL model time in the system — to choose between "high", "med" and
# "low". Keep in sync with the pricing row in app/llm_telemetry.py.
FAST_MODEL = "claude-haiku-4-5"

# --- Process-wide concurrency cap on in-flight Anthropic calls ---------------
# Concurrent streaming model calls compete for RAM/CPU; past some point on a
# given box, streaming slows to a crawl, requests stall, and the gateway's retry
# layer fires — making the contention WORSE. This semaphore bounds how many
# calls are in flight at the single chokepoint (`_create_with_retries`) at once;
# the Nth+1 call BLOCKS (queues) until a slot frees, rather than piling on or
# failing. Tune to the box: the default is conservative; the prod box has since
# grown to ~3.8 GB, where 6 concurrent streams measured ~80 MB extra — see
# LLM_MAX_CONCURRENCY / LLM_BG_CAP.
#
# Why a threading (not asyncio) semaphore: every heavy caller runs the blocking
# Anthropic call inside a WORKER THREAD (the gateway's `llm_call` is sync and
# dispatched via `asyncio.to_thread` / background threads). Acquiring a
# threading semaphore blocks that worker thread, NOT the asyncio event loop, so
# the loop stays responsive and queued `to_thread` calls simply wait their turn
# on the thread-pool side. Any caller that reaches the chokepoint MUST be on a
# worker thread (see callers rerouted through `asyncio.to_thread`) so the loop
# is never blocked here.
#
# Default 6: raised from the original conservative 3 once a real caller started
# dispatching several of its own calls concurrently (competitive_intel's
# per-competitor capture fan-out) and would otherwise have queued behind this
# gate, giving back most of the win concurrency was meant to buy. This is not a
# new, untested number — it is exactly the "6 concurrent streams ~80 MB extra"
# figure the comment above already measured and documented as safe on the prod
# box. This gate is process-wide and shared by EVERY interactive LLM call in
# the app, not just competitive_intel, so raising it affects every caller that
# reaches this chokepoint. Hosts with RAM headroom can raise it further via
# LLM_MAX_CONCURRENCY (and LLM_BG_CAP, to let warming use the extra slots).
# Values <= 0 / unset fall back to the default (never 0, which would deadlock).
_DEFAULT_MAX_CONCURRENCY = 6
# How long a call may wait for a slot before we emit a (single) saturation log,
# so sustained contention is observable without spamming every queued call.
_SLOT_WAIT_LOG_THRESHOLD_S = 5.0


def _resolve_max_concurrency() -> int:
    raw = getattr(settings, "llm_max_concurrency", _DEFAULT_MAX_CONCURRENCY)
    try:
        n = int(raw)
    except (TypeError, ValueError):
        n = _DEFAULT_MAX_CONCURRENCY
    return n if n > 0 else _DEFAULT_MAX_CONCURRENCY


_DEFAULT_BG_CAP = 1


def _resolve_bg_cap() -> int:
    """How many of the `capacity` slots background (warm) calls may hold at once.

    Default 1 serializes warming; raising it (env LLM_BG_CAP) parallelizes the
    per-insight PRD/evidence warm. The _PriorityGate clamps it to capacity-1 so
    background can never occupy every slot (interactive callers stay reachable).
    """
    raw = getattr(settings, "llm_bg_cap", _DEFAULT_BG_CAP)
    try:
        n = int(raw)
    except (TypeError, ValueError):
        n = _DEFAULT_BG_CAP
    return n if n > 0 else _DEFAULT_BG_CAP


class _PriorityGate:
    """Two-lane concurrency gate over the process-wide call cap.

    Interactive callers (the default — anything a user is actively waiting on)
    compete for `capacity` slots exactly like the old BoundedSemaphore.
    Background callers (pre-warming) are second-class twice over:

      - at most `bg_cap` background calls hold slots at once, so warming can
        never occupy the whole cap; and
      - a background caller never acquires while ANY interactive caller is
        waiting — a user's click always jumps the warm queue.

    A threading (not asyncio) primitive for the same reason the old semaphore
    was one: callers hold the slot from worker threads (see module note), so
    waiting blocks that thread, never the event loop.
    """

    def __init__(self, capacity: int, bg_cap: int = 1) -> None:
        self._capacity = capacity
        # Background may never consume the full cap (that would starve clicks
        # until a warm call finishes); with capacity 1 there is no spare slot,
        # so background degrades to polite-FIFO behind interactive waiters.
        self._bg_cap = max(1, min(bg_cap, capacity - 1)) if capacity > 1 else 1
        self._cond = threading.Condition()
        self._active = 0
        self._bg_active = 0
        self._interactive_waiting = 0

    def acquire(self, *, background: bool = False) -> None:
        with self._cond:
            if background:
                while (
                    self._active >= self._capacity
                    or self._bg_active >= self._bg_cap
                    or self._interactive_waiting > 0
                ):
                    self._cond.wait()
                self._bg_active += 1
            else:
                self._interactive_waiting += 1
                try:
                    while self._active >= self._capacity:
                        self._cond.wait()
                finally:
                    self._interactive_waiting -= 1
            self._active += 1

    def release(self, *, background: bool = False) -> None:
        with self._cond:
            self._active -= 1
            if background:
                self._bg_active -= 1
            self._cond.notify_all()


_llm_gate = _PriorityGate(_resolve_max_concurrency(), bg_cap=_resolve_bg_cap())

# Retry policy for transient API failures. 4 attempts ≈ 0.5s + 2s + 8s of
# backoff (+ jitter) worst-case before surfacing the error.
MAX_ATTEMPTS = 4
_BACKOFF_BASE_S = 0.5
# Default per-request read timeout. Generous enough for the ranking-class
# calls (~100s observed) but well below the SDK's own non-streaming ceiling.
_REQUEST_TIMEOUT_S = 120.0
# Long-generation read timeout (public — the gateway reads it for long-output
# skills). Big non-streamed responses (e.g. the 2-part PRD) exceed the default;
# long-output skills run with this floor AND stream the response, which is the
# SDK's required pattern for slow/large requests and sidesteps the read timeout.
LONG_REQUEST_TIMEOUT_S = 600.0

# A single wrapping markdown code fence (```lang … ```). Models sometimes wrap an
# HTML/markdown document in one despite being told not to; we strip it so the
# stored payload is the raw document.
_CODE_FENCE_RE = re.compile(r"^\s*```[a-zA-Z0-9_-]*\r?\n([\s\S]*?)\r?\n?```\s*$")


def strip_code_fence(text: str) -> str:
    """Strip a single wrapping markdown code fence (```html … ```) from a model
    response, returning the inner document. Returns `text` unchanged when it
    isn't fenced. Use on outputs that must be stored/rendered as a raw document
    (e.g. the evidence-brief HTML), where a stray fence would otherwise leak into
    the artifact."""
    m = _CODE_FENCE_RE.match(text)
    return m.group(1).strip() if m else text


@lru_cache(maxsize=16)
def _client_for_key(api_key: str, key_mode: str = "platform") -> Anthropic:
    """Cached Anthropic client keyed by the API key. max_retries=0: the SDK's own
    retry layer is disabled so ours is the single source of truth.

    The client is instrumented for usage metering before being cached, so every
    call through it lands in `llm_usage_events` without any call site opting in
    (see app.llm_metering). `key_mode` records whose key is billed; it is part
    of the cache key only for correctness-by-construction — it is a function of
    `api_key`, so it never actually splits the cache.
    """
    client = Anthropic(api_key=api_key, timeout=_REQUEST_TIMEOUT_S, max_retries=0)
    return install_metering(client, key_mode, provider=PROVIDER_ANTHROPIC)


@lru_cache(maxsize=16)
def _openai_client_for_key(
    api_key: str, key_mode: str = "platform"
) -> OpenAIMessagesClient:
    """The OpenAI counterpart of `_client_for_key`, cached the same way.

    Same contract: no client-side retry (ours is the only layer), the same
    default read timeout, and metered before caching so an OpenAI workspace's
    spend lands in `llm_usage_events` on exactly the same footing — tagged
    `provider='openai'` so the usage dashboard can split the two.
    """
    client = OpenAIMessagesClient(api_key=api_key, timeout=_REQUEST_TIMEOUT_S)
    return install_metering(client, key_mode, provider=PROVIDER_OPENAI)


def get_client() -> LLMClient:
    """The client for the acting company's provider and key (see app.llm_keys).

    Provider and key are resolved in one step: the company's own key when it has
    one for the provider it chose, that provider's platform key otherwise.
    Unbound stacks (CLI, startup, unauthenticated) get Anthropic + the platform
    key, exactly as before. Embeddings go through OpenAI directly and never call
    this factory.
    """
    from app.llm_keys import resolve_llm_client_config

    provider, key, key_mode = resolve_llm_client_config(
        anthropic_platform_key=settings.anthropic_api_key or None,
        openai_platform_key=settings.openai_api_key or None,
    )
    if not key:
        raise HTTPException(
            500,
            "OPENAI_API_KEY not configured"
            if provider == PROVIDER_OPENAI
            else "ANTHROPIC_API_KEY not configured",
        )
    if provider == PROVIDER_OPENAI:
        return _openai_client_for_key(key, key_mode)
    return _client_for_key(key, key_mode)


def _is_retryable(exc: Exception) -> bool:
    if isinstance(exc, (anthropic.APIConnectionError, anthropic.APITimeoutError)):
        return True
    if isinstance(exc, anthropic.APIStatusError):
        # 429 rate limit, 5xx server errors, 529 overloaded.
        return exc.status_code == 429 or exc.status_code >= 500
    if isinstance(exc, OpenAIAPIError):
        # Same rule on the OpenAI side. `status_code is None` means the request
        # never got a response (connection/timeout) — the transport-failure case
        # the two anthropic exceptions above cover.
        return exc.status_code is None or exc.status_code == 429 or exc.status_code >= 500
    return False


def _attempt_delay(attempt: int) -> float:
    return _BACKOFF_BASE_S * (4 ** attempt) * (1 + random.random() * 0.25)


def _create_with_retries(
    client: LLMClient, *, stream: bool = False, background: bool = False,
    on_delta=None, on_json_delta=None, **kwargs
):
    """`messages.create` with exponential backoff on transient failures.

    When `stream=True`, the request is issued through `client.messages.stream`
    and the streamed deltas are accumulated into the final message — the SDK's
    required pattern for long/large outputs, which avoids the read timeout a
    big non-streamed response would hit. The return value is the same final
    Message object either way, so callers (`_capture_meta`, content extraction)
    are unchanged.

    `on_delta(text)` — optional. When given AND streaming, each TEXT delta is
    passed to it as it arrives (for token-streaming a doc to the client). It
    never fires for tool-use/JSON responses (their deltas are partial JSON, not
    text) or on the non-streamed path. A transient failure mid-stream restarts
    the stream, so on_delta may re-emit from the beginning; the caller treats
    the persisted final result as authoritative and uses on_delta only for
    progressive display. Callback exceptions are swallowed. Between attempts a
    callback exposing `reset()` is rewound (app.graph.token_stream.delta_sink's
    does), which is how downstream accumulators are told to drop attempt 1
    rather than glue the two together.

    `on_json_delta(partial_json)` — the tool-use counterpart of `on_delta`:
    when given AND streaming, each `input_json` PARTIAL-JSON fragment of the
    forced tool's input is forwarded as it arrives (a caller-side extractor —
    e.g. app.ask_stream — turns those into display text). Same restart caveat
    and same `reset()` rewind as on_delta, so its incremental parse restarts
    with the re-emitted stream.

    The whole call (including its retries) holds ONE process-wide concurrency
    slot (`_llm_gate`) for its full duration, so the box never runs more
    than LLM_MAX_CONCURRENCY model calls at once. Acquiring blocks the calling
    WORKER THREAD (not the asyncio loop — see module note); the slot is always
    released in `finally`, so an Anthropic error never leaks a slot.

    `background=True` marks the call as pre-warming: it waits in the gate's
    low-priority lane (capped, and always behind interactive waiters) so a
    user-facing call is never queued behind warm work.
    """
    _wait_start = _time.monotonic()
    _llm_gate.acquire(background=background)
    waited = _time.monotonic() - _wait_start
    if waited >= _SLOT_WAIT_LOG_THRESHOLD_S:
        # Saturation is observable but not spammy: only calls that actually had
        # to queue for a while log, and only once each (after the slot frees).
        logger.warning(
            "LLM call waited %.1fs for a concurrency slot "
            "(cap=%d) — model calls are saturated",
            waited, _resolve_max_concurrency(),
        )
    try:
        last: Exception | None = None
        for attempt in range(MAX_ATTEMPTS):
            try:
                # A retry restarts the stream from zero — rewind a stateful
                # incremental extractor so it re-parses the fresh emission
                # instead of gluing two attempts together. BOTH callback shapes
                # are rewound: the tool-use extractor (on_json_delta) and the
                # raw-text sink (on_delta) accumulate downstream just the same,
                # and a `reset()` that publishes a restart frame is what stops
                # the client rendering attempt 1 + attempt 2 as one document.
                # Display-only path: a callback that fails to rewind must not
                # take the retry down with it.
                if attempt:
                    for _cb in (on_json_delta, on_delta):
                        reset = getattr(_cb, "reset", None)
                        if callable(reset):
                            try:
                                reset()
                            except Exception:  # noqa: BLE001 — display only
                                logger.exception(
                                    "stream restart reset failed (continuing)"
                                )
                if stream:
                    with client.messages.stream(**kwargs) as s:
                        # Drain the stream so deltas are consumed, then return
                        # the assembled final message (same shape as create).
                        # With on_delta, forward each text delta as it lands
                        # (progressive display) before assembling the final.
                        if on_json_delta is not None:
                            # Tool-use streaming: the payload arrives as
                            # `input_json` partial fragments, not text events.
                            for _event in s:
                                if getattr(_event, "type", None) != "input_json":
                                    continue
                                try:
                                    on_json_delta(getattr(_event, "partial_json", "") or "")
                                except Exception:  # noqa: BLE001 — display only
                                    logger.exception("on_json_delta callback failed (continuing)")
                        elif on_delta is not None:
                            for _text in s.text_stream:
                                try:
                                    on_delta(_text)
                                except Exception:  # noqa: BLE001 — display only
                                    logger.exception("on_delta callback failed (continuing)")
                        return s.get_final_message()
                return client.messages.create(**kwargs)
            except Exception as exc:  # noqa: BLE001 — classified below
                if not _is_retryable(exc) or attempt == MAX_ATTEMPTS - 1:
                    raise
                delay = _attempt_delay(attempt)
                logger.warning(
                    "LLM call transient failure (attempt %d/%d, retrying in %.1fs): %s",
                    attempt + 1, MAX_ATTEMPTS, delay, exc,
                )
                last = exc
                _time.sleep(delay)
        raise last  # pragma: no cover — loop always returns or raises
    finally:
        _llm_gate.release(background=background)


# --- Prompt-cache TTL tiers -------------------------------------------------
# Anthropic prices a cache WRITE by the TTL the request asks for: the bare
# `{"type": "ephemeral"}` block is the 5-minute tier at 1.25x the input rate,
# and an explicit `ttl: "1h"` is the 1-hour tier at 2x. A read is 0.1x either
# way. So the tiers are not strictly ordered — 1h is cheaper per call only when
# entries actually survive to be read.
#
# The rule of thumb this codebase uses: pick 1h when the measured share of
# calls landing within an hour of the previous one is comfortably above the
# ~55% break-even, and 5m otherwise. `compose_top_insights` is the clear case
# (measured over 21 days of production: 14.8% of calls fall within 5 minutes of
# the previous one, 86.3% within an hour) — see app/graph/gateway.py.
CACHE_TTL_5M = "5m"
CACHE_TTL_1H = "1h"

# Minimum cacheable prefix, in TOKENS, per model family. A prefix shorter than
# this is NOT cached and the API says nothing about it — the response simply
# comes back with `cache_creation_input_tokens: 0`. Attaching `cache_control`
# below the floor is therefore not a cheap no-op to be safe about: it burns one
# of the four breakpoints a request is allowed and makes the telemetry read as
# "we cache here" when we do not. Sized from Anthropic's published per-model
# minimums; keep in sync when a model tier is added.
_MIN_CACHEABLE_TOKENS = {
    "claude-haiku-4-5": 4096,
    "claude-opus-4-7": 2048,
    "claude-sonnet-4-6": 1024,
}
# Fail SAFE, not open: an unknown model gets the strictest floor, so a new tier
# can only ever under-cache (a missed saving) rather than emit dead breakpoints.
_DEFAULT_MIN_CACHEABLE_TOKENS = 4096
# Prose runs ~4 chars/token. Only ever used to compare against the floors above,
# where erring high costs a cache we could have had and erring low costs a dead
# breakpoint — so this stays deliberately conservative.
_CHARS_PER_TOKEN = 4


def _create_maybe_batched(
    client, *, batch: bool, label: str = "", batch_deadline_s: float | None = None,
    stream: bool = False, background: bool = False, on_delta=None,
    on_json_delta=None, **kwargs,
):
    """`_create_with_retries`, except a caller with no one waiting can pay half.

    Anthropic's Message Batches API runs the SAME request for 50% of the price;
    the trade is latency (minutes, not seconds). `batch=True` means "this work
    is not on anyone's request path" — background ingest, a catalog backfill —
    and it is opt-in per call site rather than a global default because only the
    call site knows whether anything is waiting.

    Falls back to the ordinary synchronous path whenever batching does not work
    out (`run_batch` returns None: switch off, non-Anthropic key, API error,
    deadline). ONE shape can never batch, and it is refused here rather than in
    `app.llm_batch` because the reason is local to this module:

      * PER-DELTA CALLBACKS. A batch returns one finished message; there are no
        deltas to forward, so a caller that passed `on_delta`/`on_json_delta`
        must keep the live path or it would silently stop streaming.

    `stream` and a per-request `timeout` do NOT refuse a batch, which they used
    to. Both exist for one reason — a large generation on the synchronous path
    trips the HTTP read timeout — and a batch performs no synchronous read, so
    neither applies to it. Refusing on them meant every long-output skill
    (`prd-author`, `evidence-brief`, `implementation-spec`,
    `ideation-prioritize` — see `graph.gateway._LONG_OUTPUT_SKILLS`) had
    `batch=True` silently downgraded to a full-price live call, which is most of
    the background generation in the product.

    They are instead dropped for the BATCH attempt and kept for the FALLBACK.
    That distinction is load-bearing: `run_batch` returns None on a switched-off
    flag, a non-Anthropic key, an API error or a deadline, and the fallback then
    runs the very generation the long-output transport exists for — Part B
    averages ~185s against a 120s default read timeout, so falling back without
    the streaming transport would trade a batch miss for a guaranteed
    `httpx.ReadTimeout`.

    `timeout` is also not a Messages-API body parameter — it is an SDK request
    option — so it could not go into a batch request's `params` even if the
    semantics fit.

    The batched request is otherwise byte-identical to the one the sync path
    would send, which is the point: `_build_base_kwargs` produced it, both paths
    consume it, and neither can drift from the other.
    """
    can_batch = batch and on_delta is None and on_json_delta is None
    if can_batch:
        from app.llm_batch import BatchRequest, run_batch

        # Strip the SDK request option; the body is what a batch carries.
        batch_kwargs = {k: v for k, v in kwargs.items() if k != "timeout"}
        results = run_batch(
            [BatchRequest("r0", batch_kwargs)],
            label=label,
            **({"deadline_s": batch_deadline_s}
               if batch_deadline_s is not None else {}),
        )
        if results and "r0" in results:
            return results["r0"]
    # Fallback keeps the caller's original transport, timeout included.
    return _create_with_retries(
        client, stream=stream, background=background, on_delta=on_delta,
        on_json_delta=on_json_delta, **kwargs,
    )


def _cache_control(ttl: str | None) -> dict:
    """The `cache_control` block for a TTL tier.

    `None` / `"5m"` emits the bare ephemeral block — the API's 5-minute default,
    billed at 1.25x input. `"1h"` emits the explicit 1-hour block, billed at 2x.
    """
    if ttl == CACHE_TTL_1H:
        return {"type": "ephemeral", "ttl": CACHE_TTL_1H}
    return {"type": "ephemeral"}


def _is_cacheable(text: str, model: str) -> bool:
    """Whether `text` is long enough to actually produce a cache entry on `model`.

    Guards the silent-no-op case described on `_MIN_CACHEABLE_TOKENS`: the
    triage path, for instance, sends ~2.5k tokens to haiku, whose floor is 4096,
    so marking it cacheable would look like a fix and change nothing.
    """
    floor = _MIN_CACHEABLE_TOKENS.get(model, _DEFAULT_MIN_CACHEABLE_TOKENS)
    return len(text) >= floor * _CHARS_PER_TOKEN


def _build_base_kwargs(
    *,
    model: str,
    max_tokens: int,
    system: str,
    user: str,
    user_cacheable_prefix: str | None,
    temperature: float | None = None,
    cache_ttl: str | None = None,
) -> dict:
    """Build the kwargs dict passed to `messages.create`.

    Prompt caching is applied wherever it can actually pay off:

      * `system` is cached whenever it clears the acting model's minimum
        cacheable prefix — on BOTH the plain and the prefixed branch. It used
        to be cached only on the prefixed branch, so every skill-less caller
        with a large static system prompt (`app.ask_planner` most visibly,
        whose ~7k-char block is documented as "STATIC and TENANT-INVARIANT so
        one cache entry serves every company") silently paid full input price
        on every call. The comment described the intent; the code only
        implemented half of it.
      * `user_cacheable_prefix`, when given, is cached under the same TTL.

    A `system` short enough to be uncacheable keeps the plain-string form, so
    the request shape for small callers is byte-identical to before.

    `cache_ttl` selects the pricing tier for both blocks — see `_cache_control`.
    One tier per request, deliberately: the two blocks are always written and
    read together, and a single tier keeps `app.llm_metering` able to price the
    response's single `cache_creation_input_tokens` total unambiguously.

    `temperature` (when not None) is threaded straight through to
    `messages.create` — omitted entirely when None so the API default (1.0) is
    used, keeping every existing caller byte-identical.
    """
    cc = _cache_control(cache_ttl)
    cache_system = _is_cacheable(system, model)

    if user_cacheable_prefix is None:
        base = {
            "model": model,
            "max_tokens": max_tokens,
            "system": (
                [{"type": "text", "text": system, "cache_control": cc}]
                if cache_system
                else system
            ),
            "messages": [{"role": "user", "content": user}],
        }
        if temperature is not None:
            base["temperature"] = temperature
        return base

    system_param: list[dict] = [
        {"type": "text", "text": system, "cache_control": cc}
        if cache_system
        else {"type": "text", "text": system}
    ]
    content = [
        {"type": "text", "text": user_cacheable_prefix, "cache_control": cc},
        {"type": "text", "text": user},
    ]
    base = {
        "model": model,
        "max_tokens": max_tokens,
        "system": system_param,
        "messages": [{"role": "user", "content": content}],
    }
    if temperature is not None:
        base["temperature"] = temperature
    return base


def _capture_meta(meta_out: dict | None, msg, model: str) -> None:
    """Populate caller-supplied meta_out with usage/stop info (gateway telemetry).

    The RESPONSE's own model wins over the one the caller asked for, matching
    what `app.llm_metering` records. It matters on two counts: an Anthropic
    alias resolves to a concrete id, and on OpenAI the call site's Claude id was
    translated to an OpenAI model — so trusting the request would have the
    gateway price real OpenAI tokens against Claude's rate card in
    `agent_decision_log`.
    """
    if meta_out is None:
        return
    u = getattr(msg, "usage", None)
    meta_out.update({
        "model": getattr(msg, "model", None) or model,
        "input_tokens": getattr(u, "input_tokens", 0) or 0,
        "output_tokens": getattr(u, "output_tokens", 0) or 0,
        "cache_creation_input_tokens": getattr(u, "cache_creation_input_tokens", 0) or 0,
        "cache_read_input_tokens": getattr(u, "cache_read_input_tokens", 0) or 0,
        "stop_reason": getattr(msg, "stop_reason", None),
    })


def _unwrap_response_envelope(out, schema):
    """Unwrap a lone ``{"response": {...}}`` envelope from a structured result.

    Some models (observed on the non-streamed Opus path) nest the ENTIRE
    structured object under a single ``response`` key — cued by the tool name
    ``submit_response`` — even though the tool's ``input_schema`` is flat. Callers
    then read their real fields (e.g. ``insights``) off the top level and get
    nothing. This was silently emptying every regenerated Top Insights brief.

    Safe + narrow: only unwraps when the result is EXACTLY ``{"response": <dict>}``
    AND the requested schema does not itself declare a top-level ``response``
    property (so a schema that legitimately has a ``response`` field is untouched).
    """
    if not isinstance(out, dict) or list(out.keys()) != ["response"]:
        return out
    inner = out["response"]
    if not isinstance(inner, dict):
        return out
    if "response" in ((schema or {}).get("properties") or {}):
        return out
    return inner


def call_json(
    *,
    system: str,
    user: str,
    model: str = DEFAULT_MODEL,
    max_tokens: int = 16000,
    schema: dict | None = None,
    user_cacheable_prefix: str | None = None,
    cache_ttl: str | None = None,
    meta_out: dict | None = None,
    stream: bool = False,
    timeout: float | None = None,
    background: bool = False,
    temperature: float | None = None,
    on_json_delta=None,
    batch: bool = False,
    batch_label: str = "",
    batch_deadline_s: float | None = None,
) -> dict:
    """Call Claude expecting a strict JSON object response.

    `batch=True` routes the call through the Message Batches API at half price
    when nothing is waiting on it — see `_create_maybe_batched`. It is a pure
    cost/latency trade: the request, the response handling, and the returned
    value are identical either way, and any problem falls back to the live path.

    If `schema` is provided, uses Anthropic tool-use with a forced tool_choice
    — the SDK validates the structured input and returns a real dict, which
    eliminates the JSON-string-escaping failures that happen when an LLM
    hand-writes JSON containing markdown tables, quoted text, etc.

    `on_json_delta(partial_json)` — optional; with `stream=True` and a schema,
    forwards each partial-JSON fragment of the tool input as it streams (see
    _create_with_retries). Ignored on the schema-less text-parse path.

    If `schema` is None, falls back to parsing the model's text response as
    JSON (used by endpoints whose payload is simple enough to round-trip
    safely).

    If `user_cacheable_prefix` is provided, it is sent as a separate text
    block before `user` with `cache_control: ephemeral` set, so subsequent
    calls within the cache TTL reuse the prefix tokens. When the system
    prompt is also substantial (>1000 chars), it gets the same treatment.
    """
    client = get_client()
    base_kwargs: dict = _build_base_kwargs(
        model=model,
        max_tokens=max_tokens,
        system=system,
        user=user,
        user_cacheable_prefix=user_cacheable_prefix,
        cache_ttl=cache_ttl,
        temperature=temperature,
    )
    if timeout is not None:
        # Per-request read-timeout override (an SDK request option) — used for
        # long generations that exceed the client default.
        base_kwargs["timeout"] = timeout
    if schema is not None:
        tool = {
            "name": "submit_response",
            "description": "Submit the structured response. All fields required.",
            "input_schema": schema,
        }
        msg = _create_maybe_batched(
            client,
            batch=batch,
            label=batch_label,
            batch_deadline_s=batch_deadline_s,
            stream=stream,
            background=background,
            on_json_delta=on_json_delta,
            **base_kwargs,
            tools=[tool],
            tool_choice={"type": "tool", "name": "submit_response"},
        )
        _capture_meta(meta_out, msg, model)
        for block in msg.content:
            if block.type == "tool_use" and block.name == "submit_response":
                out = dict(block.input) if not isinstance(block.input, dict) else block.input
                return _unwrap_response_envelope(out, schema)
        raise HTTPException(
            502, "LLM did not invoke the structured response tool"
        )

    msg = _create_maybe_batched(
        client, batch=batch, label=batch_label, batch_deadline_s=batch_deadline_s,
        stream=stream, background=background, **base_kwargs,
    )
    _capture_meta(meta_out, msg, model)
    text = "".join(b.text for b in msg.content if b.type == "text").strip()
    # Tolerate accidental fences
    if text.startswith("```"):
        text = text.split("\n", 1)[1] if "\n" in text else text
        if text.endswith("```"):
            text = text.rsplit("```", 1)[0]
        text = text.lstrip("json").lstrip("\n").strip()
    try:
        return json.loads(text)
    except json.JSONDecodeError as exc:
        raise HTTPException(
            502, f"LLM returned invalid JSON: {exc}; first 400 chars: {text[:400]!r}"
        ) from exc


def call_md(
    *,
    system: str,
    user: str,
    model: str = DEFAULT_MODEL,
    max_tokens: int = 16000,
    user_cacheable_prefix: str | None = None,
    cache_ttl: str | None = None,
    meta_out: dict | None = None,
    stream: bool = False,
    timeout: float | None = None,
    background: bool = False,
    temperature: float | None = None,
    on_delta=None,
    batch: bool = False,
    batch_label: str = "",
    batch_deadline_s: float | None = None,
) -> str:
    """Call Claude expecting plain markdown output.

    `batch=True` is the same half-price opt-in `call_json` takes — see
    `_create_maybe_batched`. It matters most on THIS branch: markdown callers
    are where the long-output skills live (prd-author and friends), and those
    are exactly the background generations worth batching.

    `stream=True` streams the response (required for long/large outputs; avoids
    the read timeout) and `timeout` overrides the per-request read timeout for
    a single slow call. Both default off, so existing callers are unchanged.

    `on_delta(text)` — optional; forwards each text delta as it streams (for
    token-streaming the doc to the client). Requires stream=True to fire.

    `user_cacheable_prefix` mirrors `call_json`: when supplied it is sent as a
    separate `cache_control: ephemeral` text block before `user` (and the system
    prompt is cached too when substantial), so a large STABLE prefix — e.g. a
    bound skill's METHOD block or a static HTML template — is reused across calls
    within the cache TTL instead of being re-processed on every call and retry.
    When None, the kwargs shape is byte-identical to before (plain string system
    + content), so every existing caller is unchanged.
    """
    kwargs: dict = _build_base_kwargs(
        model=model,
        max_tokens=max_tokens,
        system=system,
        user=user,
        user_cacheable_prefix=user_cacheable_prefix,
        cache_ttl=cache_ttl,
        temperature=temperature,
    )
    if timeout is not None:
        kwargs["timeout"] = timeout
    msg = _create_maybe_batched(
        get_client(), batch=batch, label=batch_label,
        batch_deadline_s=batch_deadline_s,
        stream=stream, background=background, on_delta=on_delta, **kwargs
    )
    _capture_meta(meta_out, msg, model)
    return "".join(b.text for b in msg.content if b.type == "text").strip()


def run_tool_loop(
    *,
    system: str,
    user: str,
    tools: list[dict],
    dispatch,                       # (name: str, input: dict) -> str
    model: str = DEFAULT_MODEL,
    max_tokens: int = 8000,
    max_iters: int = 5,
    user_cacheable_prefix: str | None = None,
    cache_ttl: str | None = None,
    meta_out: dict | None = None,
    force_tool: str | None = None,
) -> str:
    """Run a manual tool-use loop until the model stops calling tools.

    The model may call any of `tools`; each `tool_use` is executed by
    `dispatch(name, input) -> str` and fed back as a `tool_result`. Returns the
    model's final text. `meta_out` (if given) captures usage from the LAST turn.
    Bounded by `max_iters` so a misbehaving model can't loop forever.

    `force_tool` (optional): the name of a tool the model MUST call on the
    FIRST turn (`tool_choice={"type":"tool"}`). Used by the project group
    tool-loop's forcing pass — when a turn clearly asks to delegate/complete a
    task but the model narrated a bare "I'll do it" promise without calling the
    tool, a second pass with `force_tool` set guarantees the tool actually
    fires (so the ledger/handoff really happens) instead of ending on the
    promise. Only the FIRST turn is forced; subsequent turns revert to auto so
    the model can produce its closing text.

    This is the shared, single-chokepoint tool loop (same retry/concurrency gate
    as every other call). Used by the paths that need the model to REACH a live
    system mid-answer — the tracker lookup, ticket updates, connector reads.
    (It also backed the deleted `app.skills.scripts`, whose deterministic PM
    math ran as a local tool; that path is gone, the live-read ones are not.)
    """
    client = get_client()
    base = _build_base_kwargs(
        model=model,
        max_tokens=max_tokens,
        system=system,
        user=user,
        user_cacheable_prefix=user_cacheable_prefix,
        cache_ttl=cache_ttl,
    )
    system_param = base["system"]
    messages = base["messages"]
    final_text = ""
    for _i in range(max_iters):
        # Force the named tool on the FIRST turn only; auto thereafter.
        extra = (
            {"tool_choice": {"type": "tool", "name": force_tool}}
            if force_tool and _i == 0
            else {}
        )
        msg = _create_with_retries(
            client,
            model=model,
            max_tokens=max_tokens,
            system=system_param,
            messages=messages,
            tools=tools,
            **extra,
        )
        _capture_meta(meta_out, msg, model)
        text = "".join(
            b.text for b in msg.content if getattr(b, "type", None) == "text"
        ).strip()
        if text:
            final_text = text
        if getattr(msg, "stop_reason", None) != "tool_use":
            return final_text
        messages.append({"role": "assistant", "content": msg.content})
        results = []
        for b in msg.content:
            if getattr(b, "type", None) == "tool_use":
                try:
                    out = dispatch(b.name, b.input)
                except Exception as exc:  # noqa: BLE001 — surface to the model
                    out = f"(tool {b.name} error: {exc})"
                results.append(
                    {"type": "tool_result", "tool_use_id": b.id, "content": str(out)}
                )
        messages.append({"role": "user", "content": results})
    # Exhaustion guard: the `for` completed without an early `return` at
    # `stop_reason != "tool_use"`, i.e. the model called a tool on every one of
    # the `max_iters` turns and never composed a closing text turn. If it also
    # never set `final_text` on any turn, the loop returns "" — the invisible
    # empty-answer failure mode (this used to be a bare `return` with no log).
    # Warn so the failure stops being silent in the logs; the caller
    # (`_try_scoped_tool_answer`) now degrades an empty return to a real answer.
    if not final_text.strip():
        logger.warning(
            "run_tool_loop exhausted max_iters=%s with no closing text turn — "
            "returning empty text (model called tools every turn)",
            max_iters,
        )
    return final_text


def call_with_web_search(
    *,
    system: str,
    user: str,
    model: str = DEFAULT_MODEL,
    max_tokens: int = 8000,
    max_searches: int = 5,
    meta_out: dict | None = None,
    skill: str | None = None,
    skill_module: str | None = None,
) -> str:
    """Call Claude with the server-side web_search tool enabled.

    Returns the final text answer (the model searches autonomously up to
    `max_searches` times). Used by the outward research agents
    (competitor / market). Web content is untrusted input — callers'
    system prompts must treat it as data, never instructions.

    When `skill` is set, the bound skill's method text (and the named
    `skill_module`, if any) is PREPENDED to the system prompt under a
    "## METHOD (skill: <id> @<hash>)" delimiter — the caller's own system
    prompt stays as the agent-specific layer after it. On this path the method
    rides the SYSTEM prompt rather than the user-message prefix
    `_build_base_kwargs` uses, and it carries its own cache breakpoint --
    the method block is sent as its own cache-controlled system block, with the
    module and the caller's system layer in a second, uncached block after it
    (see the comment at the split for why the boundary is where it is).

    TOLERANT of a `skill` that names no vendored directory, for the same reason
    `graph.gateway._build_method_prefix` is: every research pass on this path
    (public-feedback capture, company-research stages, the competitive sweep and
    its weekly deep-dive) passes `skill=` for ATTRIBUTION as much as for method
    text, and those ids no longer name a vendored skill. Raising here would take
    the entire web-research capability down over a missing prompt fragment.
    Each of those callers carries its own capture contract in `system`, which is
    what the records are actually parsed against, so a missing method is a
    quality tradeoff. A missing `skill_module` inside a skill that DOES exist
    still raises — that is a caller bug, not a vendoring decision.

    The request STREAMS on the long read timeout: a search-heavy call (the
    server runs up to `max_searches` web searches before composing the answer)
    routinely outlives the default non-streaming read timeout — the
    public-feedback capture pass hit exactly that httpx.ReadTimeout on staging.
    Streaming is the SDK's required pattern for slow/large requests; the
    accumulated final Message keeps `_capture_meta` and content extraction
    unchanged.
    """
    # `system_param` is what actually goes on the wire: a plain string for the
    # method-less callers (byte-identical to before), or a TWO-BLOCK list that
    # puts a cache breakpoint after the stable method text. See below.
    system_param: Any = system
    if skill is not None:
        # Imported lazily to avoid a module-load cycle (loader -> config -> ...).
        from app.skills.loader import UnknownSkillError, get_skill

        try:
            spec = get_skill(skill)
        except UnknownSkillError:
            spec = None  # not vendored -> run method-less; see the docstring
        if spec is not None:
            method = f"## METHOD (skill: {spec.id} @{spec.content_hash})\n{spec.method}"
            # Everything AFTER the stable method: the optional module, then the
            # caller's own system layer. Kept in exactly the order the single
            # concatenated string used to have, so the model reads the same
            # prompt it always did.
            tail = ""
            if skill_module:
                module_text = spec.modules[skill_module]
                tail += f"\n\n### MODULE: {skill_module}\n{module_text}"
            tail += f"\n{system}"
            # WHY THE SPLIT. This path used to send one concatenated string with
            # no `cache_control` at all -- the docstring's "no cacheable-prefix
            # mechanism". The whole string is unusable as a cache key because two
            # of its three parts move on EVERY call: `skill_module` and the
            # caller's `system` (competitive_intel appends a per-pass
            # "### THIS PASS" focus). The skill's own SKILL.md does not move --
            # it is byte-stable across every pass of a run, across runs, and
            # across tenants -- so the breakpoint goes right after it.
            #
            # Anthropic renders the prefix as tools -> system -> messages, so a
            # breakpoint here also caches the tool definition ahead of it (the
            # web_search block is ~2.2k tokens on its own, and `max_searches` is
            # read once per run, so it is stable for the run too).
            #
            # Guarded on `_is_cacheable` for the same reason `_build_base_kwargs`
            # is: a block under the model's minimum cacheable prefix silently
            # produces no cache entry while still burning one of the four
            # breakpoints a request is allowed.
            if _is_cacheable(method, model):
                system_param = [
                    {"type": "text", "text": method,
                     "cache_control": _cache_control(None)},
                    {"type": "text", "text": tail},
                ]
            else:
                system_param = f"{method}{tail}"
    msg = _create_with_retries(
        get_client(),
        stream=True,
        model=model,
        max_tokens=max_tokens,
        system=system_param,
        messages=[{"role": "user", "content": user}],
        tools=[{
            # DELIBERATELY the 2025 tool. The 20260209 variant adds dynamic
            # filtering that runs code execution under the hood; measured on a
            # 4-search competitive-intel-shaped query against sonnet-4-6 it cost
            # 291,691 input tokens in 319s versus 38,691 in 46s here -- 7.5x the
            # tokens and 7x the latency. Do not "upgrade" this without re-running
            # that comparison.
            "type": "web_search_20250305",
            "name": "web_search",
            "max_uses": max_searches,
        }],
        timeout=LONG_REQUEST_TIMEOUT_S,
    )
    _capture_meta(meta_out, msg, model)
    return "".join(b.text for b in msg.content if b.type == "text").strip()
