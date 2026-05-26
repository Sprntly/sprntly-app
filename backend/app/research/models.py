"""Pydantic models for the Research Agent's competitive digest.

These are intentionally lean and JSON-serialisable so the Brief
generator can drop them straight into the `competitive_pulse` slot of
the brief payload without further translation.

Validation guarantees (enforced by validators below):
- summary stays under 280 chars (tweet-length; Brief uses one-liners)
- review body stays under 500 chars (the raw review can be much
  longer; we don't want to balloon brief payloads)
- store is exactly "ios" or "android" (no surprise sources)
- rating is in [1, 5] (App Store / Google Play standard range)
"""
from __future__ import annotations

from typing import Literal, Optional

from pydantic import BaseModel, Field, field_validator


_SUMMARY_MAX = 280
_BODY_MAX = 500


def _truncate(text: str, limit: int) -> str:
    """Truncate to `limit` chars on a word boundary when possible.

    The trailing ellipsis (U+2026) counts toward `limit` so the
    returned length is always <= `limit`.
    """
    if text is None:
        return ""
    text = text.strip()
    if len(text) <= limit:
        return text
    # Reserve one char for the ellipsis.
    sliced = text[: limit - 1]
    # Prefer a word boundary if there's one in the last 30 chars to
    # avoid cutting mid-word, but only if it leaves a reasonable amount
    # of content (>50% of the limit). Otherwise hard-cut.
    space = sliced.rfind(" ")
    if space > limit // 2:
        sliced = sliced[:space]
    return sliced + "…"


class ChangelogSignal(BaseModel):
    """A single item scraped from a competitor's changelog/blog/press page."""

    competitor: str
    # "blog" / "changelog" / "press" / "releases" — the kind of page the
    # item came from. Used by the Brief to weight signals ("changelog"
    # > "blog" for actual feature ships).
    source: str
    title: str
    url: str
    # ISO-8601 string when we can parse one off the page; None if the
    # page didn't include a date we recognised. The Brief treats None
    # as "this week" (since the digest is weekly) but flags it lower
    # confidence in the highlight ranker.
    published_at: Optional[str] = None
    summary: str = ""

    @field_validator("summary", mode="before")
    @classmethod
    def _cap_summary(cls, v: str | None) -> str:
        if v is None:
            return ""
        return _truncate(str(v), _SUMMARY_MAX)

    @field_validator("title", "competitor", "source", "url", mode="before")
    @classmethod
    def _require_nonempty(cls, v: str | None) -> str:
        if v is None or not str(v).strip():
            raise ValueError("field must be a non-empty string")
        return str(v).strip()


class ReviewSignal(BaseModel):
    """A single app-store review (iOS for Phase 1; Android is stubbed)."""

    competitor: str
    store: Literal["ios", "android"]
    rating: int = Field(ge=1, le=5)
    title: str
    body: str = ""
    published_at: str

    @field_validator("body", mode="before")
    @classmethod
    def _cap_body(cls, v: str | None) -> str:
        if v is None:
            return ""
        return _truncate(str(v), _BODY_MAX)

    @field_validator("competitor", "title", "published_at", mode="before")
    @classmethod
    def _require_nonempty(cls, v: str | None) -> str:
        if v is None or not str(v).strip():
            raise ValueError("field must be a non-empty string")
        return str(v).strip()


class CompetitorPulse(BaseModel):
    """All signals collected for one competitor in a single weekly window."""

    competitor_name: str
    app_store_signals: list[ReviewSignal] = Field(default_factory=list)
    changelog_signals: list[ChangelogSignal] = Field(default_factory=list)
    # G2/Capterra/etc. live here. Phase-1 always empty; reserving the
    # field shape so callers can light up review-site UI before the
    # scraper is wired in.
    review_signals: list[ReviewSignal] = Field(default_factory=list)
    # Surfaced by the Brief's "Competitive Pulse" section when true.
    # The digest aggregator sets this rule-based (no LLM) — see
    # `digest._compute_notable`.
    notable: bool = False

    @field_validator("competitor_name", mode="before")
    @classmethod
    def _require_nonempty(cls, v: str | None) -> str:
        if v is None or not str(v).strip():
            raise ValueError("competitor_name must be a non-empty string")
        return str(v).strip()


class CompetitiveDigest(BaseModel):
    """Top-level object the Synthesis Agent / Brief consumes."""

    workspace_id: str
    generated_at: str  # ISO-8601 UTC
    pulses: list[CompetitorPulse] = Field(default_factory=list)
    # Pre-rendered bullet points, 3-5 items, ready to drop into the
    # Brief's "Competitive Pulse" section. Capped server-side so the
    # Brief renderer doesn't have to truncate.
    top_highlights: list[str] = Field(default_factory=list, max_length=5)

    @field_validator("workspace_id", "generated_at", mode="before")
    @classmethod
    def _require_nonempty(cls, v: str | None) -> str:
        if v is None or not str(v).strip():
            raise ValueError("field must be a non-empty string")
        return str(v).strip()
