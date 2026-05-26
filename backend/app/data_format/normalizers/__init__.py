"""Per-connector normalizers.

Phase 1 (this PR): Amplitude, Mixpanel, CSV / Google Sheets.

Phase 2 (deferred — stubs raise NotImplementedError):
  * GA4
  * PostHog
  * Pendo

Every normalizer takes the connector's native shape and returns
``list[CanonicalUserRow]``.  The ``goal_metric`` value is computed by
the normalizer (e.g. Day-30 retention as 0/1) — callers don't have to
know connector-specific semantics.
"""
from __future__ import annotations

from app.data_format.normalizers.amplitude import normalize_amplitude
from app.data_format.normalizers.csv import normalize_csv
from app.data_format.normalizers.mixpanel import normalize_mixpanel

__all__ = ["normalize_amplitude", "normalize_csv", "normalize_mixpanel"]
