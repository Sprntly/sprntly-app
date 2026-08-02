"""Usage metering wrapper around the Anthropic client.

Every Claude call in this codebase is issued through one of three client
factories (`app.llm`, `app.design_agent.client`, `app.routes.agent_chat`). This
module wraps the client those factories return so that **one row is recorded per
model call, everywhere, without touching a single call site**.

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

from app.llm_telemetry import RunUsage, UnknownModelError

logger = logging.getLogger(__name__)


def _record(
    *,
    key_mode: str,
    model: str | None,
    message: Any | None,
    started_at: float,
    status: str = "succeeded",
    error_class: str | None = None,
) -> None:
    """Stitch one usage row together and hand it to the buffered writer."""
    try:
        from app.db.llm_usage import record_usage
        from app.llm_keys import current_company_id
        from app.usage_context import current_scope

        company_id = current_company_id()
        if not company_id:
            # Unbound stack: CLI, startup probe, the admin key-test call. There
            # is no tenant to attribute the spend to, so there is no useful row
            # to write — the platform-key total is already ours by definition.
            return

        scope = current_scope()
        usage = getattr(message, "usage", None)
        run = RunUsage(
            input_tokens=getattr(usage, "input_tokens", 0) or 0,
            output_tokens=getattr(usage, "output_tokens", 0) or 0,
            cache_creation_input_tokens=getattr(usage, "cache_creation_input_tokens", 0) or 0,
            cache_read_input_tokens=getattr(usage, "cache_read_input_tokens", 0) or 0,
        )
        # The response's own `model` is authoritative (an alias in the request
        # resolves to a concrete id); fall back to what we asked for.
        actual_model = getattr(message, "model", None) or model

        cost: float | None
        try:
            cost = run.est_cost_usd(actual_model) if actual_model else None
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
            provider="anthropic",
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

    def __init__(self, inner: Any, key_mode: str, model: str | None, started_at: float):
        self._inner = inner
        self._key_mode = key_mode
        self._model = model
        self._started_at = started_at
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
                model=self._model,
                message=msg,
                started_at=self._started_at,
            )
        return msg


class _MeteredStreamManager:
    """Proxies the SDK's `MessageStreamManager` context manager."""

    def __init__(self, inner: Any, key_mode: str, model: str | None):
        self._inner = inner
        self._key_mode = key_mode
        self._model = model
        self._started_at = time.monotonic()

    def __enter__(self) -> _MeteredStream:
        return _MeteredStream(
            self._inner.__enter__(), self._key_mode, self._model, self._started_at
        )

    def __exit__(self, exc_type, exc, tb) -> Any:
        if exc_type is not None:
            _record(
                key_mode=self._key_mode,
                model=self._model,
                message=None,
                started_at=self._started_at,
                status="failed",
                error_class=exc_type.__name__,
            )
        return self._inner.__exit__(exc_type, exc, tb)

    def __getattr__(self, name: str) -> Any:
        return getattr(self._inner, name)


class _MeteredMessages:
    """Proxies `client.messages`, metering `create` and `stream`."""

    def __init__(self, inner: Any, key_mode: str):
        self._inner = inner
        self._key_mode = key_mode

    def __getattr__(self, name: str) -> Any:
        return getattr(self._inner, name)

    def create(self, **kwargs: Any) -> Any:
        started_at = time.monotonic()
        model = kwargs.get("model")
        try:
            msg = self._inner.create(**kwargs)
        except Exception as exc:
            # A failed attempt still consumed a request (and may have been
            # partially billed). Record it so the dashboard's failure rate is
            # real, then re-raise untouched.
            _record(
                key_mode=self._key_mode,
                model=model,
                message=None,
                started_at=started_at,
                status="failed",
                error_class=type(exc).__name__,
            )
            raise
        _record(
            key_mode=self._key_mode, model=model, message=msg, started_at=started_at
        )
        return msg

    def stream(self, **kwargs: Any) -> _MeteredStreamManager:
        return _MeteredStreamManager(
            self._inner.stream(**kwargs), self._key_mode, kwargs.get("model")
        )


def install_metering(client: Any, key_mode: str) -> Any:
    """Instrument an Anthropic client in place; returns the same client.

    Swaps `client.messages` for a metering proxy. The client object ITSELF is
    left alone — deliberately, rather than returning a wrapper object: callers
    and tests rely on the factories returning a real `anthropic.Anthropic`
    (`isinstance` checks, `.api_key`, `.max_retries`) and on the per-key client
    cache returning the identical instance across calls. `messages` is a
    `cached_property` on the SDK class, so assigning to the instance shadows it
    cleanly (verified against anthropic 0.117.0); every other attribute of the
    client is untouched.

    `key_mode` is fixed per client because the clients are cached per API key,
    and whose key it is follows from the key itself — so the label can never
    drift from the credential that was actually billed.

    Idempotent: re-installing on an already-metered client is a no-op, so a
    double-wrap can never double-count.
    """
    if isinstance(getattr(client, "messages", None), _MeteredMessages):
        return client
    client.messages = _MeteredMessages(client.messages, key_mode)
    return client
