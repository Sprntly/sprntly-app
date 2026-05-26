"""Brief package — composition layer over Synthesis + DS Agent.

The Monday Brief is Sprntly's flagship output (Master PRD §4.2):
ALWAYS runs the Comprehensive tier regardless of team size, plan, or
trust level. This package wires the DS Agent's Comprehensive tier
(P1.5) into the Synthesis Agent's 11-step Brief Assembly pipeline
(P0-3) and persists/caches the result.

Submodule layout:
  comprehensive.py  — `run_brief_comprehensive`: DS → Synthesis →
                      persist + cache for one (workspace, dataset).
  cache.py          — `cached_briefs` table accessors (Mon-aligned).
  persist.py        — Brief persister; reuses the existing `briefs`
                      table so the legacy /v1/brief/current route
                      continues to read what we write.
  scheduler.py      — `run_monday_brief_for_all_workspaces`: stub
                      entry point a cron / APScheduler harness fires
                      every Monday 9am workspace TZ. Cron wiring is
                      a deploy-time concern (deferred to P3).

The Synthesis-only path on `/v1/brief/regenerate` is unchanged. The
new `/v1/brief/comprehensive/regenerate` runs the full DS + Synthesis
composition with caching.
"""
from app.brief.comprehensive import run_brief_comprehensive
from app.brief.cache import (
    get_cached_brief,
    save_cached_brief,
    week_start_iso,
)
from app.brief.scheduler import run_monday_brief_for_all_workspaces

__all__ = [
    "run_brief_comprehensive",
    "get_cached_brief",
    "save_cached_brief",
    "week_start_iso",
    "run_monday_brief_for_all_workspaces",
]
