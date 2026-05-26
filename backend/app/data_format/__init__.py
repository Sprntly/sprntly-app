"""Canonical data-format layer for the DS Agent.

This package owns the *input contract* between connectors and any
data-science / synthesis pipeline.  It does two things:

1.  Defines the **canonical user table** — one row per user, columns =
    features + ``goal_metric`` (see ``schema.CanonicalUserRow``).
2.  Defines the pre-computed **DATA_SUMMARY** aggregate (
    ``schema.DataSummary``) that the Express tier consumes.  The
    Express tier MUST NOT see raw user-level data.

Per-connector normalizers (``normalizers/``) convert raw connector
payloads into the canonical shape.  ``null_rules`` and ``quality`` apply
the spec's null / missing-value rules and quality-tier classification.
``summarize`` builds the final DATA_SUMMARY object.

Today there is no live caller; this lands the schemas + mappers so
P0-5 (Brief Comprehensive) and P1.5 (DS Stages 2-5) have a stable
input contract.
"""
from __future__ import annotations

from app.data_format.schema import (
    CanonicalUserRow,
    DataQuality,
    DataSummary,
    FeatureSummary,
    QualityTier,
    ValidationResult,
)
from app.data_format.null_rules import apply_null_rules
from app.data_format.quality import assess_quality, validate_user_table
from app.data_format.summarize import build_data_summary

__all__ = [
    "CanonicalUserRow",
    "DataQuality",
    "DataSummary",
    "FeatureSummary",
    "QualityTier",
    "ValidationResult",
    "apply_null_rules",
    "assess_quality",
    "validate_user_table",
    "build_data_summary",
]
