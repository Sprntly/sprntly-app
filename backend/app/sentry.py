"""Sentry error-tracking setup for the backend API.

Init is GATED on ``settings.sentry_dsn``: when the DSN is empty (local dev,
CI, tests) this is a complete no-op — nothing is imported-with-side-effects,
nothing is sent. Set SENTRY_DSN per-environment (systemd EnvironmentFile /
.env) to turn it on.

The FastAPI + Starlette integrations auto-attach when sentry_sdk detects the
frameworks, so we don't wire any middleware by hand — unhandled exceptions in
request handlers and background tasks are captured automatically.
"""

from __future__ import annotations

import logging

from app.config import settings

logger = logging.getLogger(__name__)

_initialized = False


def init_sentry() -> bool:
    """Initialise Sentry if a DSN is configured. Returns True if enabled.

    Safe to call more than once (idempotent) and safe to call when the
    ``sentry_sdk`` package is not installed — both just return False.
    """
    global _initialized
    if _initialized:
        return True

    dsn = (settings.sentry_dsn or "").strip()
    if not dsn:
        return False

    try:
        import sentry_sdk
    except ImportError:  # pragma: no cover - dependency optional at runtime
        logger.warning("SENTRY_DSN set but sentry-sdk is not installed; skipping.")
        return False

    sentry_sdk.init(
        dsn=dsn,
        environment=settings.sentry_environment or "development",
        release=(settings.sentry_release or None),
        traces_sample_rate=settings.sentry_traces_sample_rate,
        # PII (headers, cookies, request bodies) is NOT sent by default — this
        # backend handles customer data. Flip deliberately if ever needed.
        send_default_pii=False,
    )
    _initialized = True
    logger.info(
        "Sentry initialised (environment=%s, traces_sample_rate=%s)",
        settings.sentry_environment,
        settings.sentry_traces_sample_rate,
    )
    return True
