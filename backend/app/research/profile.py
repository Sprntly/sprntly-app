"""Pydantic models for competitor profiles + signals.

These are the wire shapes used at the service + HTTP boundary. The DB
shape is identical (one column per field, jsonb for raw_payload_json)
so service helpers can construct models directly from a Supabase row.

The `source` and `signal_type` literals are duplicated as DB CHECK
constraints in the migration. If you add a new variant here, also add
it to supabase/migrations/20260526000000_competitor_profiles.sql.
"""
from __future__ import annotations

from datetime import datetime
from typing import Literal, Optional

from pydantic import BaseModel, ConfigDict, Field


SignalSource = Literal[
    "app_store_ios",
    "app_store_android",
    "changelog",
    "blog",
    "press",
    "jobs",
    "g2",
    "social",
    "pricing",
    "seo",
]

SignalType = Literal[
    "review",
    "release",
    "blog_post",
    "press_release",
    "job_posting",
    "rating_change",
    "pricing_change",
    "feature_launch",
]

Sentiment = Literal["positive", "neutral", "negative"]


class CompetitorProfile(BaseModel):
    """Persistent record of a single competitor.

    `workspace_id` is the tenant boundary — every service-layer call
    enforces that the caller's workspace matches the row's workspace
    before returning anything.
    """
    model_config = ConfigDict(extra="forbid")

    id: str
    workspace_id: str
    name: str
    product_url: Optional[str] = None
    app_store_ios_url: Optional[str] = None
    app_store_android_url: Optional[str] = None
    g2_url: Optional[str] = None
    capterra_url: Optional[str] = None
    changelog_url: Optional[str] = None
    careers_url: Optional[str] = None
    twitter_handle: Optional[str] = None
    monitoring_enabled: bool = True
    created_at: datetime
    updated_at: datetime


class CompetitorProfileCreate(BaseModel):
    """Input shape for POST /v1/research/competitors. No id/timestamps —
    those are server-assigned. workspace_id is taken from the session.
    """
    model_config = ConfigDict(extra="forbid")

    name: str = Field(min_length=1, max_length=200)
    product_url: Optional[str] = None
    app_store_ios_url: Optional[str] = None
    app_store_android_url: Optional[str] = None
    g2_url: Optional[str] = None
    capterra_url: Optional[str] = None
    changelog_url: Optional[str] = None
    careers_url: Optional[str] = None
    twitter_handle: Optional[str] = None
    monitoring_enabled: bool = True


class CompetitorProfileUpdate(BaseModel):
    """Partial update — every field optional. Anything omitted is left
    as-is on the existing row.
    """
    model_config = ConfigDict(extra="forbid")

    name: Optional[str] = Field(default=None, min_length=1, max_length=200)
    product_url: Optional[str] = None
    app_store_ios_url: Optional[str] = None
    app_store_android_url: Optional[str] = None
    g2_url: Optional[str] = None
    capterra_url: Optional[str] = None
    changelog_url: Optional[str] = None
    careers_url: Optional[str] = None
    twitter_handle: Optional[str] = None
    monitoring_enabled: Optional[bool] = None


class CompetitorSignal(BaseModel):
    """A single observed event attributed to a competitor profile.

    `raw_payload_json` carries the source-specific blob (e.g. the full
    iTunes RSS entry, the parsed changelog item) so we can re-derive
    fields later if our extraction logic changes without re-fetching.
    """
    model_config = ConfigDict(extra="forbid")

    id: str
    competitor_profile_id: str
    source: SignalSource
    signal_type: SignalType
    title: str
    body: str = ""
    url: Optional[str] = None
    sentiment: Optional[Sentiment] = None
    published_at: datetime
    fetched_at: datetime
    raw_payload_json: dict = Field(default_factory=dict)


class CompetitorSignalCreate(BaseModel):
    """Input shape for `record_signal`. The service assigns id +
    fetched_at; everything else comes from the monitor that produced it.
    """
    model_config = ConfigDict(extra="forbid")

    source: SignalSource
    signal_type: SignalType
    title: str = Field(min_length=1)
    body: str = ""
    url: Optional[str] = None
    sentiment: Optional[Sentiment] = None
    published_at: datetime
    raw_payload_json: dict = Field(default_factory=dict)
