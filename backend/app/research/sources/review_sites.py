"""B2B review-site fetcher (G2, Capterra, TrustRadius, …) — STUB.

G2 and Capterra don't expose a free public API for individual product
review feeds. Real options:

1. G2's paid API ("Buyer Intent" / "Reviews" endpoints) — requires a
   signed contract, multiple-$k/month minimum.
2. A third-party scraper-as-a-service (SerpAPI, ScrapingBee, Bright
   Data, Apify actors). Cheap-ish but adds an external vendor key the
   ops team has to rotate.
3. Build our own headless-Chromium scraper. Both sites aggressively
   anti-bot; this is a non-trivial maintenance burden.

For Phase-1 we punt: this function logs a warning and returns an
empty list so the digest still produces a valid CompetitorPulse with
`review_signals = []`. Once we pick a vendor, swap the body for the
real implementation; the call site in `digest.py` won't need to
change.
"""
from __future__ import annotations

import logging

from app.research.models import ReviewSignal

logger = logging.getLogger(__name__)


def fetch_g2_signals(
    competitor: str,
    g2_slug: str | None = None,
) -> list[ReviewSignal]:
    """Stub. Always returns []. See module docstring for the deferral rationale.

    Args:
        competitor: Friendly name for log breadcrumbs.
        g2_slug:    The g2.com/products/<slug> identifier. Accepted but
                    unused; reserved so callers can wire it now and the
                    upgrade is a no-op for them.
    """
    logger.warning(
        "G2 integration deferred — needs paid API or scraper service "
        "(competitor=%s slug=%s)",
        competitor,
        g2_slug,
    )
    return []
