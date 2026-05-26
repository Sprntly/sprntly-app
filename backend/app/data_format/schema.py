"""Pydantic schemas for the canonical user table and DATA_SUMMARY.

Mirrors the Data_Format_Spec.docx column list:

  user_id        string, required, unique
  signup_date    date YYYY-MM-DD, required
  goal_metric    float, required — never imputed (drop row if null)
  tenure_bucket  derived: "0-30d" | "31-90d" | "90d+"
  region         optional ISO country code
  device         optional: mobile / web / desktop / unknown
  tier           optional: free / pro / enterprise
  [feature_*]    optional float; binary 0/1 or count

DATA_SUMMARY is the pre-computed aggregate the Express tier reads; it
never sees raw user data.
"""
from __future__ import annotations

from datetime import date
from enum import Enum
from typing import Optional

from pydantic import BaseModel, ConfigDict, Field, field_validator


# ----- enums ----------------------------------------------------------------


class QualityTier(str, Enum):
    HIGH = "HIGH"
    MEDIUM = "MEDIUM"
    LOW = "LOW"
    INSUFFICIENT = "INSUFFICIENT"


class TenureBucket(str, Enum):
    NEW = "0-30d"
    MID = "31-90d"
    OLD = "90d+"


class Device(str, Enum):
    MOBILE = "mobile"
    WEB = "web"
    DESKTOP = "desktop"
    UNKNOWN = "unknown"


class Tier(str, Enum):
    FREE = "free"
    PRO = "pro"
    ENTERPRISE = "enterprise"


# ----- canonical row --------------------------------------------------------


class CanonicalUserRow(BaseModel):
    """One row per user — the DS Agent's universal input shape.

    ``features`` is the open-ended bag of per-user feature columns
    (binary 0/1 indicators or counts).  Keeping it as a dict on the row
    lets normalizers add arbitrary connector-specific columns without
    schema migrations.
    """

    model_config = ConfigDict(extra="forbid")

    user_id: str = Field(..., min_length=1)
    signup_date: date
    goal_metric: float

    tenure_bucket: Optional[TenureBucket] = None
    region: Optional[str] = None
    device: Optional[Device] = None
    tier: Optional[Tier] = None

    features: dict[str, Optional[float]] = Field(default_factory=dict)

    @field_validator("region")
    @classmethod
    def _validate_region(cls, v: Optional[str]) -> Optional[str]:
        if v is None:
            return None
        v = v.strip().upper()
        if len(v) != 2 or not v.isalpha():
            raise ValueError("region must be a 2-letter ISO country code")
        return v

    @field_validator("features", mode="before")
    @classmethod
    def _validate_features(
        cls, v: dict[str, Optional[float]]
    ) -> dict[str, Optional[float]]:
        if not isinstance(v, dict):
            raise ValueError("features must be a dict")
        for k, val in v.items():
            if not isinstance(k, str) or not k:
                raise ValueError("feature keys must be non-empty strings")
            if val is None:
                continue
            if isinstance(val, bool):
                raise ValueError(
                    f"feature {k!r} must be float/int (binary 0/1 or count); pass 0/1, not bool"
                )
            if not isinstance(val, (int, float)):
                raise ValueError(
                    f"feature {k!r} must be float/int (binary or count), got {type(val).__name__}"
                )
        return v


# ----- DATA_SUMMARY ---------------------------------------------------------


class FeatureSummary(BaseModel):
    """Aggregate stats for one feature column."""

    model_config = ConfigDict(extra="forbid")

    avg_in_goal_1: float
    avg_in_goal_0: float
    lift: float
    null_pct: float = Field(..., ge=0.0, le=1.0)
    n_users_with_data: int = Field(..., ge=0)


class DataQuality(BaseModel):
    """Top-level quality summary, surfaced to Express + Synthesis."""

    model_config = ConfigDict(extra="forbid")

    completeness_pct: float = Field(..., ge=0.0, le=1.0)
    quality_tier: QualityTier
    goal_completeness: float = Field(..., ge=0.0, le=1.0)


class DataSummary(BaseModel):
    """Pre-computed aggregate that the Express tier reads.

    The Express tier MUST NOT see raw user-level data — only this
    object.  Schema matches the example in Data_Format_Spec.docx.
    """

    model_config = ConfigDict(extra="forbid")

    goal_metric: str
    n_users: int = Field(..., ge=0)
    goal_metric_rate: float = Field(..., ge=0.0, le=1.0)
    features: dict[str, FeatureSummary]
    data_quality: DataQuality
    connector: str
    company_name: str
    product_type: str


# ----- validation -----------------------------------------------------------


class ValidationResult(BaseModel):
    """Outcome of ``validate_user_table``.

    ``passed`` is True iff there are no FAIL-level failures.  WARN-level
    issues populate ``warnings`` but don't block.
    """

    model_config = ConfigDict(extra="forbid")

    passed: bool
    failures: list[str] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)
