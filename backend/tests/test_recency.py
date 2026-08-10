"""Unit tests for the connector-agnostic KG-extraction recency window.

`within_extraction_window` is the single shared decision that separates catalog
coverage (every document, forever) from KG-extraction coverage (recent
documents only). These pin its boundary, its fail-open contract, and the
disabled-window escape hatch — no connector, no HTTP, pure function.
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone

from app.kg_ingest.recency import within_extraction_window

UTC = timezone.utc
# A fixed reference so the window boundary is deterministic. cutoff for an
# 18-month window is 2026-08-10 minus 18 months = 2025-02-10T00:00:00Z.
_NOW = datetime(2026, 8, 10, tzinfo=UTC)


# ── Creation / boundary ──────────────────────────────────────────────────────


def test_within_window_recent_true():
    """A timestamp inside the window is extractable (AC1)."""
    assert within_extraction_window("2026-07-20T10:00:00Z", 18, now=_NOW) is True


def test_within_window_old_false():
    """A timestamp older than the window is catalog-only, not extracted (AC1)."""
    assert within_extraction_window("2024-01-01T00:00:00Z", 18, now=_NOW) is False


def test_within_window_edge_is_inclusive():
    """A document modified exactly at the cutoff instant is IN-window (AC2).

    The cutoff for `_NOW` (2026-08-10) minus 18 months is 2025-02-10T00:00:00Z
    (month arithmetic with day-clamping, not a fixed-days approximation)."""
    assert within_extraction_window("2025-02-10T00:00:00Z", 18, now=_NOW) is True
    # One second before the cutoff falls out — proves the boundary is real, not
    # a coincidence of a coarse comparison.
    assert within_extraction_window("2025-02-09T23:59:59Z", 18, now=_NOW) is False


def test_within_window_day_clamp_across_short_month():
    """Month math must clamp the day, not overflow. 2026-03-31 minus 1 month is
    Feb 28, so a page dated 2026-02-28 is exactly at the (clamped) cutoff."""
    now = datetime(2026, 3, 31, tzinfo=UTC)
    assert within_extraction_window("2026-02-28T00:00:00Z", 1, now=now) is True
    assert within_extraction_window("2026-02-27T00:00:00Z", 1, now=now) is False


# ── Error handling / edge ────────────────────────────────────────────────────


def test_within_window_none_fails_open():
    """A missing modified date yields the document for extraction (fail-open,
    AC3) and never raises — the catalog still holds it regardless."""
    assert within_extraction_window(None, 18, now=_NOW) is True


def test_within_window_malformed_fails_open_and_logs(caplog):
    """An unparseable modified date fails open (AC3) and logs exactly one INFO
    line so the anomaly is observable, but does not raise."""
    with caplog.at_level(logging.INFO, logger="app.kg_ingest.recency"):
        result = within_extraction_window("not-a-date", 18, now=_NOW)
    assert result is True
    info = [r for r in caplog.records if r.levelno == logging.INFO
            and "unparseable" in r.getMessage()]
    assert len(info) == 1


def test_within_window_zero_months_disables():
    """A window of 0 (or negative) months disables the gate — extract
    everything, even a decade-old page (AC4)."""
    assert within_extraction_window("2010-01-01T00:00:00Z", 0, now=_NOW) is True
    assert within_extraction_window("2010-01-01T00:00:00Z", -1, now=_NOW) is True


def test_within_window_tz_naive_stamp_is_coerced_to_utc():
    """A tz-naive ISO stamp (no Z / offset) is treated as UTC rather than
    crashing the aware-vs-naive comparison."""
    assert within_extraction_window("2026-07-20T10:00:00", 18, now=_NOW) is True
