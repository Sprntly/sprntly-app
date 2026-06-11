"""Thin wrapper over the Anthropic SDK.

All `messages.create` calls go through `_create_with_retries`, which adds
exponential-backoff retries on transient failures (429 / 5xx / overloaded /
timeouts / connection drops) and a per-request timeout. Existing callers
(`call_json` / `call_md`) get this for free; the agent-facing gateway
(`app.graph.gateway.llm_call`) layers tenant context + telemetry on top.
"""
import json
import logging
import random
import threading
import time as _time

import anthropic
from anthropic import Anthropic
from fastapi import HTTPException

from app.config import settings

logger = logging.getLogger(__name__)

DEFAULT_MODEL = "claude-sonnet-4-6"

# --- Process-wide concurrency cap on in-flight Anthropic calls ---------------
# The prod box is small (~916 MB RAM, limited CPU). 4+ concurrent streaming
# model calls thrash it: streaming slows to a crawl, requests stall, and the
# gateway's retry layer fires — which makes the contention WORSE. This semaphore
# bounds how many calls are in flight at the single chokepoint
# (`_create_with_retries`) at once; the Nth+1 call BLOCKS (queues) until a slot
# frees, rather than piling on or failing.
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
# Default 3: lets one PRD's two parallel parts (Part A + Part B, each a stream)
# run together PLUS one other call (brief/evidence/ask) — the common steady
# state — while still capping total load well below the 4+ that stalls the box.
# Tunable via LLM_MAX_CONCURRENCY; values <= 0 / unset fall back to the default
# (never 0, which would deadlock every call).
_DEFAULT_MAX_CONCURRENCY = 3
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


_llm_gate = _PriorityGate(_resolve_max_concurrency())

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

_client: Anthropic | None = None


def get_client() -> Anthropic:
    global _client
    if _client is None:
        if not settings.anthropic_api_key:
            raise HTTPException(500, "ANTHROPIC_API_KEY not configured")
        # max_retries=0: the SDK's own retry layer is disabled so ours is the
        # single source of truth (uniform logging + backoff policy).
        _client = Anthropic(
            api_key=settings.anthropic_api_key,
            timeout=_REQUEST_TIMEOUT_S,
            max_retries=0,
        )
    return _client


def _is_retryable(exc: Exception) -> bool:
    if isinstance(exc, (anthropic.APIConnectionError, anthropic.APITimeoutError)):
        return True
    if isinstance(exc, anthropic.APIStatusError):
        # 429 rate limit, 5xx server errors, 529 overloaded.
        return exc.status_code == 429 or exc.status_code >= 500
    return False


def _attempt_delay(attempt: int) -> float:
    return _BACKOFF_BASE_S * (4 ** attempt) * (1 + random.random() * 0.25)


def _create_with_retries(
    client: Anthropic, *, stream: bool = False, background: bool = False, **kwargs
):
    """`messages.create` with exponential backoff on transient failures.

    When `stream=True`, the request is issued through `client.messages.stream`
    and the streamed deltas are accumulated into the final message — the SDK's
    required pattern for long/large outputs, which avoids the read timeout a
    big non-streamed response would hit. The return value is the same final
    Message object either way, so callers (`_capture_meta`, content extraction)
    are unchanged.

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
                if stream:
                    with client.messages.stream(**kwargs) as s:
                        # Drain the stream so deltas are consumed, then return
                        # the assembled final message (same shape as create).
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


def _build_base_kwargs(
    *,
    model: str,
    max_tokens: int,
    system: str,
    user: str,
    user_cacheable_prefix: str | None,
) -> dict:
    """Build the kwargs dict passed to `messages.create`.

    If `user_cacheable_prefix` is None, returns the simple `content=str` form
    used by every existing caller — behavior is unchanged. Otherwise builds
    content as a list of text blocks, with `cache_control: ephemeral` on the
    prefix (and on the system prompt when it's substantial enough to be
    worth caching).
    """
    if user_cacheable_prefix is None:
        return {
            "model": model,
            "max_tokens": max_tokens,
            "system": system,
            "messages": [{"role": "user", "content": user}],
        }
    system_param: list[dict] = [
        {"type": "text", "text": system, "cache_control": {"type": "ephemeral"}}
        if len(system) > 1000
        else {"type": "text", "text": system}
    ]
    content = [
        {
            "type": "text",
            "text": user_cacheable_prefix,
            "cache_control": {"type": "ephemeral"},
        },
        {"type": "text", "text": user},
    ]
    return {
        "model": model,
        "max_tokens": max_tokens,
        "system": system_param,
        "messages": [{"role": "user", "content": content}],
    }


def _capture_meta(meta_out: dict | None, msg, model: str) -> None:
    """Populate caller-supplied meta_out with usage/stop info (gateway telemetry)."""
    if meta_out is None:
        return
    u = getattr(msg, "usage", None)
    meta_out.update({
        "model": model,
        "input_tokens": getattr(u, "input_tokens", 0) or 0,
        "output_tokens": getattr(u, "output_tokens", 0) or 0,
        "cache_creation_input_tokens": getattr(u, "cache_creation_input_tokens", 0) or 0,
        "cache_read_input_tokens": getattr(u, "cache_read_input_tokens", 0) or 0,
        "stop_reason": getattr(msg, "stop_reason", None),
    })


def call_json(
    *,
    system: str,
    user: str,
    model: str = DEFAULT_MODEL,
    max_tokens: int = 16000,
    schema: dict | None = None,
    user_cacheable_prefix: str | None = None,
    meta_out: dict | None = None,
    stream: bool = False,
    timeout: float | None = None,
    background: bool = False,
) -> dict:
    """Call Claude expecting a strict JSON object response.

    If `schema` is provided, uses Anthropic tool-use with a forced tool_choice
    — the SDK validates the structured input and returns a real dict, which
    eliminates the JSON-string-escaping failures that happen when an LLM
    hand-writes JSON containing markdown tables, quoted text, etc.

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
        msg = _create_with_retries(
            client,
            stream=stream,
            background=background,
            **base_kwargs,
            tools=[tool],
            tool_choice={"type": "tool", "name": "submit_response"},
        )
        _capture_meta(meta_out, msg, model)
        for block in msg.content:
            if block.type == "tool_use" and block.name == "submit_response":
                return dict(block.input) if not isinstance(block.input, dict) else block.input
        raise HTTPException(
            502, "LLM did not invoke the structured response tool"
        )

    msg = _create_with_retries(client, stream=stream, background=background, **base_kwargs)
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
    meta_out: dict | None = None,
    stream: bool = False,
    timeout: float | None = None,
    background: bool = False,
) -> str:
    """Call Claude expecting plain markdown output.

    `stream=True` streams the response (required for long/large outputs; avoids
    the read timeout) and `timeout` overrides the per-request read timeout for
    a single slow call. Both default off, so existing callers are unchanged.
    """
    kwargs: dict = dict(
        model=model,
        max_tokens=max_tokens,
        system=system,
        messages=[{"role": "user", "content": user}],
    )
    if timeout is not None:
        kwargs["timeout"] = timeout
    msg = _create_with_retries(get_client(), stream=stream, background=background, **kwargs)
    _capture_meta(meta_out, msg, model)
    return "".join(b.text for b in msg.content if b.type == "text").strip()


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
    prompt stays as the agent-specific layer after it. The web-search path has
    no cacheable-prefix mechanism, so the method rides the system prompt here.
    """
    if skill is not None:
        # Imported lazily to avoid a module-load cycle (loader -> config -> ...).
        from app.skills.loader import get_skill

        spec = get_skill(skill)
        method = f"## METHOD (skill: {spec.id} @{spec.content_hash})\n{spec.method}"
        if skill_module:
            module_text = spec.modules[skill_module]
            method += f"\n\n### MODULE: {skill_module}\n{module_text}"
        system = f"{method}\n{system}"
    msg = _create_with_retries(
        get_client(),
        model=model,
        max_tokens=max_tokens,
        system=system,
        messages=[{"role": "user", "content": user}],
        tools=[{
            "type": "web_search_20250305",
            "name": "web_search",
            "max_uses": max_searches,
        }],
    )
    _capture_meta(meta_out, msg, model)
    return "".join(b.text for b in msg.content if b.type == "text").strip()
