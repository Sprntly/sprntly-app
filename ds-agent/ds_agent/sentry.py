"""Sentry error-tracking setup for the ds-agent service.

Init is GATED on the ``SENTRY_DSN`` env var (loaded from .env via load_dotenv
in the entrypoints, or from the systemd EnvironmentFile in prod). When unset
this is a complete no-op. The FastAPI integration auto-attaches, so unhandled
request-handler exceptions are captured without manual middleware.
"""

from __future__ import annotations

import logging
import os

logger = logging.getLogger(__name__)

_initialized = False


def init_sentry() -> bool:
    """Initialise Sentry if SENTRY_DSN is set. Returns True if enabled.

    Idempotent, and safe to call when sentry-sdk is not installed.
    """
    global _initialized
    if _initialized:
        return True

    dsn = os.environ.get("SENTRY_DSN", "").strip()
    if not dsn:
        return False

    try:
        import sentry_sdk
    except ImportError:  # pragma: no cover - dependency optional at runtime
        logger.warning("SENTRY_DSN set but sentry-sdk is not installed; skipping.")
        return False

    def _sample_rate() -> float:
        try:
            return float(os.environ.get("SENTRY_TRACES_SAMPLE_RATE", "0") or "0")
        except ValueError:
            return 0.0

    sentry_sdk.init(
        dsn=dsn,
        environment=os.environ.get("SENTRY_ENVIRONMENT", "development"),
        release=(os.environ.get("SENTRY_RELEASE") or None),
        traces_sample_rate=_sample_rate(),
        send_default_pii=False,
    )
    _initialized = True
    logger.info("Sentry initialised for ds-agent.")
    return True
