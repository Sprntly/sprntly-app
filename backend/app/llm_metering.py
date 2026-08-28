"""Usage metering wrapper around the LLM client.

Every model call in this codebase is issued through one of three client
factories (`app.llm`, `app.design_agent.client`, `app.routes.agent_chat`). This
module wraps the client those factories return so that **one row is recorded per
model call, everywhere, without touching a single call site**.

Provider-agnostic by construction: it only touches `client.messages`, and
`app.openai_client.OpenAIMessagesClient` presents the same `create` / `stream`
surface and the same response shape as the Anthropic SDK. So a workspace running
on OpenAI is metered by exactly this code, tagged `provider='openai'`.

Instrumenting the client rather than the ~40 call sites is deliberate: call-site
metering means a permanent drift problem where every new feature is silently
unmetered until someone remembers to add a line. Here, a new feature is metered
the moment it makes its first call; the only thing it can omit is the human
LABEL (see `app.usage_context`), and an unlabelled call still lands in the
ledger as `feature='unattributed'`.

What a row is stitched together from:
    company_id    <- app.llm_keys.current_company_id()   (ambient, per request)
    feature/op    <- app.usage_context.current_scope()   (ambient, per feature)
    key_mode      <- fixed at client construction        (whose key is billed)
    tokens        <- the response's `usage` object       (provider ground truth)
    est_cost_usd  <- tokens x app.llm_telemetry.MODEL_PRICING

Both streamed and non-streamed calls are covered: `messages.create` reads usage
off the returned message, and `messages.stream` hooks `get_final_message()` —
the SDK's accumulate-the-stream call, which every streaming call site in this
repo already uses to obtain its result.

Fail-open, twice over: metering never changes the value a caller receives, and a
metering failure is swallowed and logged. A broken ledger degrades the usage
dashboard; it must never break generation.
"""
from __future__ import annotations

import logging
import time
from typing import Any

from app.llm_telemetry import CACHE_TTL_1H, CACHE_TTL_5M, RunUsage, UnknownModelError

logger = logging.getLogger(__name__)


def _requested_cache_ttl(kwargs: Any) -> str:
    """The cache-write tier this REQUEST asked for, read off the request itself.

    The response reports one `cache_creation_input_tokens` total and says
    nothing about which tier it was billed at, so the tier has to come from the
    request. Reading the outgoing kwargs rather than an ambient label makes this
    ground truth: a call site that changes its `cache_control` is priced
    correctly with no second place to update.

    `app.llm._build_base_kwargs` puts one tier on every block of a request, so a
    single answer is well defined. A hand-built request that mixes tiers is
    priced at the more expensive one — the total cannot be split, and
    over-reporting a mixed request is the safer direction than under-reporting.
    """
    blocks: list[Any] = []
    system = kwargs.get("system")
    if isinstance(system, list):
        blocks.extend(system)
    for message in kwargs.get("messages") or []:
        content = message.get("content") if isinstance(message, dict) else None
        if isinstance(content, list):
            blocks.extend(content)
    for block in blocks:
        if not isinstance(block, dict):
            continue
        control = block.get("cache_control")
        if isinstance(control, dict) and control.get("ttl") == CACHE_TTL_1H:
            return CACHE_TTL_1H
    return CACHE_TTL_5M


def actual_model_hint(message: Any, requested: str | None) -> str | None:
    """The model the response reports, falling back to the one we asked for.

    Same rule the ledger row uses, kept in one place so the warning above can
    never name a different model than the row it accompanies.
    """
    return getattr(message, "model", None) or requested


def _record(
    *,
    key_mode: str,
    provider: str,
    model: str | None,
    message: Any | None,
    started_at: float,
    status: str = "succeeded",
    error_class: str | None = None,
    cache_ttl: str | None = None,
    cost_multiplier: float = 1.0,
) -> None:
    """Stitch one usage row together and hand it to the buffered writer.

    `cost_multiplier` scales the ESTIMATED cost only, never the token counts.
    It exists for the Message Batches API, which charges half for an otherwise
    identical request (`app.llm_batch.BATCH_COST_MULTIPLIER`). Leaving it at 1.0
    for batched work would over-report that spend by exactly 2x and hide the
    saving; scaling the TOKENS instead would corrupt the one number that is
    ground truth, so the discount is applied at the price, not the quantity.
    """
    try:
        from app.db.llm_usage import record_usage
        from app.llm_keys import current_company_id
        from app.usage_context import Feature, current_scope

        company_id = current_company_id()
        if not company_id:
            # Unbound stack: CLI, startup probe, the admin key-test call. There
            # is no tenant to attribute the spend to, so there is no useful row
            # to write — the platform-key total is already ours by definition.
            return

        scope = current_scope()
        if scope.feature == Feature.UNATTRIBUTED:
            # The module docstring calls an unattributed slice "a visible prompt
            # to go add the scope" — but nothing made it visible, so ~900 calls
            # a week accumulated under that label with no way to tell which code
            # path produced them short of correlating timestamps against the
            # decision log. This line is that way: it names the model and the
            # shape of the call, so the path is greppable from one log window
            # instead of reconstructable from telemetry archaeology.
            #
            # WARNING, not exception: metering is fail-soft by contract and an
            # unlabelled call must still bill correctly. The label is what is
            # missing, never the spend.
            logger.warning(
                "llm_usage unattributed call company=%s model=%s in=%s cache_write=%s "
                "out=%s — no usage_scope on this path; see app.usage_context",
                company_id, actual_model_hint(message, model),
                getattr(getattr(message, "usage", None), "input_tokens", None),
                getattr(getattr(message, "usage", None),
                        "cache_creation_input_tokens", None),
                getattr(getattr(message, "usage", None), "output_tokens", None),
            )
        usage = getattr(message, "usage", None)
        run = RunUsage(
            input_tokens=getattr(usage, "input_tokens", 0) or 0,
            output_tokens=getattr(usage, "output_tokens", 0) or 0,
            cache_creation_input_tokens=getattr(usage, "cache_creation_input_tokens", 0) or 0,
            cache_read_input_tokens=getattr(usage, "cache_read_input_tokens", 0) or 0,
        )
        # The response's own `model` is authoritative (an alias in the request
        # resolves to a concrete id); fall back to what we asked for.
        actual_model = actual_model_hint(message, model)

        cost: float | None
        try:
            cost = (
                run.est_cost_usd(actual_model, cache_ttl or CACHE_TTL_5M)
                * cost_multiplier
                if actual_model else None
            )
        except UnknownModelError:
            # Fail SOFT here, unlike `log_llm_run` which fails closed. An
            # unpriced model must not take down chat; keep the tokens (the
            # ground truth) so spend can be re-derived once it is priced, and
            # let the null cost surface the gap on the dashboard.
            cost = None
            logger.warning("llm_usage unpriced model=%s (tokens still recorded)", actual_model)

        record_usage(
            company_id=company_id,
            feature=scope.feature,
            operation=scope.operation,
            user_id=scope.user_id,
            provider=provider,
            model=actual_model,
            key_mode=key_mode,
            input_tokens=run.input_tokens,
            output_tokens=run.output_tokens,
            cache_creation_input_tokens=run.cache_creation_input_tokens,
            cache_read_input_tokens=run.cache_read_input_tokens,
            est_cost_usd=cost,
            latency_ms=int((time.monotonic() - started_at) * 1000),
            status=status,
            error_class=error_class,
        )
    except Exception:  # noqa: BLE001 — metering is never allowed to surface
        logger.exception("llm usage metering failed (continuing)")


class _MeteredStream:
    """Proxies an SDK MessageStream, metering when the final message is built.

    Everything except `get_final_message` is forwarded untouched, so callers
    that iterate the stream (`for event in stream`) or read `text_stream` see
    the real object and behave identically.
    """

    def __init__(
        self, inner: Any, key_mode: str, provider: str, model: str | None, started_at: float,
        cache_ttl: str = CACHE_TTL_5M,
    ):
        self._inner = inner
        self._key_mode = key_mode
        self._provider = provider
        self._model = model
        self._started_at = started_at
        self._cache_ttl = cache_ttl
        self._recorded = False

    def __getattr__(self, name: str) -> Any:
        return getattr(self._inner, name)

    def __iter__(self):
        return iter(self._inner)

    def get_final_message(self) -> Any:
        msg = self._inner.get_final_message()
        # A retried stream re-enters this object at most once, but a caller is
        # free to call get_final_message() twice — record only the first.
        if not self._recorded:
            self._recorded = True
            _record(
                key_mode=self._key_mode,
                provider=self._provider,
                model=self._model,
                message=msg,
                started_at=self._started_at,
                cache_ttl=self._cache_ttl,
            )
        return msg


class _MeteredStreamManager:
    """Proxies the SDK's `MessageStreamManager` context manager."""

    def __init__(
        self, inner: Any, key_mode: str, provider: str, model: str | None,
        cache_ttl: str = CACHE_TTL_5M,
    ):
        self._inner = inner
        self._key_mode = key_mode
        self._provider = provider
        self._model = model
        self._cache_ttl = cache_ttl
        self._started_at = time.monotonic()

    def __enter__(self) -> _MeteredStream:
        return _MeteredStream(
            self._inner.__enter__(),
            self._key_mode,
            self._provider,
            self._model,
            self._started_at,
            self._cache_ttl,
        )

    def __exit__(self, exc_type, exc, tb) -> Any:
        if exc_type is not None:
            _record(
                key_mode=self._key_mode,
                provider=self._provider,
                model=self._model,
                message=None,
                started_at=self._started_at,
                status="failed",
                error_class=exc_type.__name__,
                cache_ttl=self._cache_ttl,
            )
        return self._inner.__exit__(exc_type, exc, tb)

    def __getattr__(self, name: str) -> Any:
        return getattr(self._inner, name)


class _MeteredMessages:
    """Proxies `client.messages`, metering `create` and `stream`."""

    def __init__(self, inner: Any, key_mode: str, provider: str):
        self._inner = inner
        self._key_mode = key_mode
        self._provider = provider

    def __getattr__(self, name: str) -> Any:
        return getattr(self._inner, name)

    def create(self, **kwargs: Any) -> Any:
        started_at = time.monotonic()
        model = kwargs.get("model")
        cache_ttl = _requested_cache_ttl(kwargs)
        try:
            msg = self._inner.create(**kwargs)
        except Exception as exc:
            # A failed attempt still consumed a request (and may have been
            # partially billed). Record it so the dashboard's failure rate is
            # real, then re-raise untouched.
            _record(
                key_mode=self._key_mode,
                provider=self._provider,
                model=model,
                message=None,
                started_at=started_at,
                status="failed",
                error_class=type(exc).__name__,
                cache_ttl=cache_ttl,
            )
            raise
        _record(
            key_mode=self._key_mode,
            provider=self._provider,
            model=model,
            message=msg,
            started_at=started_at,
            cache_ttl=cache_ttl,
        )
        return msg

    def stream(self, **kwargs: Any) -> _MeteredStreamManager:
        return _MeteredStreamManager(
            self._inner.stream(**kwargs), self._key_mode, self._provider, kwargs.get("model"),
            _requested_cache_ttl(kwargs),
        )


def install_metering(client: Any, key_mode: str, provider: str = "anthropic") -> Any:
    """Instrument an LLM client in place; returns the same client.

    Swaps `client.messages` for a metering proxy. The client object ITSELF is
    left alone — deliberately, rather than returning a wrapper object: callers
    and tests rely on the factories returning a real `anthropic.Anthropic`
    (`isinstance` checks, `.api_key`, `.max_retries`) and on the per-key client
    cache returning the identical instance across calls. `messages` is a
    `cached_property` on the SDK class, so assigning to the instance shadows it
    cleanly (verified against anthropic 0.117.0); every other attribute of the
    client is untouched. `OpenAIMessagesClient.messages` is a plain instance
    attribute for the same reason — so this assignment works on both.

    `key_mode` and `provider` are both fixed per client because the clients are
    cached per API key, and both facts follow from the key itself — so neither
    label can drift from the credential that was actually billed. `provider`
    defaults to Anthropic to keep the pre-OpenAI call shape working.

    Idempotent: re-installing on an already-metered client is a no-op, so a
    double-wrap can never double-count.
    """
    if isinstance(getattr(client, "messages", None), _MeteredMessages):
        return client
    client.messages = _MeteredMessages(client.messages, key_mode, provider)
    return client


def key_mode_of(client: Any, default: str = "platform") -> str:
    """The key mode a metered client was built with.

    `install_metering` fixes `key_mode` per client (the clients are cached per
    API key, and the mode follows from the key), so reading it back off the
    proxy is exact — and cheaper and less racy than re-resolving the company's
    key posture just to label a row. Falls back to the platform mode for a
    client that was never instrumented, e.g. in a test.
    """
    return getattr(getattr(client, "messages", None), "_key_mode", default)


def record_external_usage(
    *,
    key_mode: str,
    provider: str,
    model: str | None,
    message: Any,
    started_at: float,
    cost_multiplier: float = 1.0,
) -> None:
    """Meter a response that did NOT come through `client.messages.create`.

    The proxy in this module instruments `create`/`stream`; anything reaching
    the API another way is invisible to it. The Message Batches endpoint is the
    first such path (`app.llm_batch`), and without this its spend would be
    missing from `llm_usage_events` entirely — moving work onto batches would
    read on the dashboard as volume disappearing rather than as a saving.

    Batched responses carry no `cache_control` echo, so the write tier cannot be
    read back off the request the way `_requested_cache_ttl` does for a live
    call; the 5-minute default applies, which is what every batching call site
    sends today.
    """
    _record(
        key_mode=key_mode,
        provider=provider,
        model=model,
        message=message,
        started_at=started_at,
        cost_multiplier=cost_multiplier,
    )
