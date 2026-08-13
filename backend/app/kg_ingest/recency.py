"""Connector-agnostic recency window for KG extraction.

Sweep connectors (Confluence first; Jira / Drive folders / Slack are the
intended future adopters) split two jobs that used to be welded together:

  * CATALOG coverage — EVERY walked document is registered so it stays
    findable forever, regardless of age.
  * KG-EXTRACTION coverage — only documents modified *recently* are also fed
    to the graph, which is a current-state store, not an archive.

`within_extraction_window` is the single shared decision that separates the
two. A connector parses its own provider's modified timestamp and asks this
helper whether that document is recent enough to extract. The window width
comes from `settings.kg_extraction_window_months`; the connector passes it in
so this module stays free of config imports and trivially unit-testable.

Deliberately stdlib-only — no `dateutil`. Month arithmetic borrows across year
boundaries and clamps the day with `calendar.monthrange`, so a cutoff never
approximates a month as a fixed number of days.

FAIL-OPEN is the contract: a missing or unparseable timestamp yields the
document for extraction (returns True). The product goal is "don't lose recent
facts"; over-extracting a handful of undated pages is bounded by the runner's
content-hash ledger (re-syncs are free) and the per-space extraction budget,
whereas fail-closed risks dropping a genuinely recent doc from the graph. The
document is catalogued regardless, so findability is never at stake either way.
"""
from __future__ import annotations

import calendar
import logging
from datetime import datetime, timezone

logger = logging.getLogger(__name__)


def _subtract_months(dt: datetime, months: int) -> datetime:
    """`dt` shifted back `months` calendar months, clamping the day.

    Borrows across year boundaries (month arithmetic on a base-12 index) and
    clamps the day to the target month's length so e.g. Mar 31 − 1 month is
    Feb 28/29, never an invalid date."""
    total = dt.year * 12 + (dt.month - 1) - months
    year, month0 = divmod(total, 12)
    month = month0 + 1
    day = min(dt.day, calendar.monthrange(year, month)[1])
    return dt.replace(year=year, month=month, day=day)


def _parse_iso_utc(modified_at: str) -> datetime:
    """Parse a Confluence-style ISO-8601 stamp to a tz-aware UTC datetime.

    Confluence stamps like `2026-07-20T10:00:00Z` and `...T...000Z`; the
    `Z`→`+00:00` swap makes `datetime.fromisoformat` accept them on the
    deployed Python. A tz-naive parse is coerced to UTC so the comparison is
    always aware-vs-aware."""
    dt = datetime.fromisoformat(modified_at.replace("Z", "+00:00"))
    if dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def within_extraction_window(
    modified_at: str | None,
    window_months: int,
    *,
    now: datetime | None = None,
) -> bool:
    """True iff a document modified at `modified_at` is recent enough to extract.

    * `window_months <= 0` → True (window disabled; extract everything).
    * `modified_at` missing or unparseable → True (FAIL-OPEN, see module docs).
    * otherwise: True iff the parsed instant is at or after the cutoff
      (`now` minus `window_months`, day-clamped). The boundary is INCLUSIVE —
      a document modified exactly at the cutoff instant is in-window.
    """
    if window_months <= 0:
        return True
    if not modified_at:
        return True
    try:
        modified = _parse_iso_utc(modified_at)
    except (TypeError, ValueError):
        logger.info(
            "recency: unparseable modified date %r — extracting (fail-open)",
            modified_at,
        )
        return True

    reference = now or datetime.now(timezone.utc)
    if reference.tzinfo is None:
        reference = reference.replace(tzinfo=timezone.utc)
    cutoff = _subtract_months(reference, window_months)
    return modified >= cutoff
