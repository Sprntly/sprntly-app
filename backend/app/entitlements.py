"""Per-company module entitlements (companies.feature_flags JSONB).

The staff admin panel (routes/staff_admin.py) stores per-company module
flags in `companies.feature_flags`. This module is the ONE place that
resolves a raw flags dict into module on/off decisions, and exposes the
FastAPI dependencies that enforce them server-side:

  * ``agents``       — ALL chat-surface capability (the Ask/Q&A agent,
                       skill routing, chat commands like PRD/ticket/VoC
                       generation from chat). Enforced on the ask + agent
                       chat routes via ``require_agents_module``.
  * ``weekly_brief`` — the weekly-brief PROCESS (scheduled generation and
                       Slack/email delivery, plus the on-demand brief
                       generation/regeneration endpoints). Enforced on
                       routes via ``require_weekly_brief_module`` and in
                       the scheduler's company loops via
                       ``weekly_brief_enabled``.

Resolution is FAIL-OPEN for grandfathering: existing companies carry
feature_flags = {} or only legacy keys (on_demand_analysis,
auto_prd_generation, engineer_agent, research_agent, …). A missing modern
key defaults ON unless the legacy keys it superseded are present and all
false; an explicit modern key always wins. This mirrors the staff-panel
frontend mapping (web/.../staff/StaffAdminScreen.tsx `agentsEnabled`),
plus the backend-only "empty dict / no relevant keys → ON" default.

Deliberately NOT gated by these flags (owner decisions):
  * the staff admin panel itself — /v1/staff/* authenticates via
    require_staff (a staff JWT, not a tenant), so module flags never apply;
  * KG ingestion (connector sync, corpus seeding, kg_ingest) — the KG also
    grounds PRDs and chat, so it keeps running even when weekly_brief is
    off;
  * prototype generation — companies.prototype_enabled is a dedicated
    column with its own gate in routes/design_agent.py.
"""
from __future__ import annotations

import logging

from fastapi import Depends, HTTPException

from app.auth import WorkspaceContext, require_workspace

logger = logging.getLogger(__name__)

# 403 details — the frontend surfaces these verbatim, keep them user-readable.
AGENTS_DISABLED_DETAIL = (
    "The Agents module is not enabled for your organization."
)
WEEKLY_BRIEF_DISABLED_DETAIL = (
    "The Weekly Brief module is not enabled for your organization."
)

# Legacy per-capability flags the single `agents` module superseded. When
# `agents` is absent but any of these are present, they decide (OR): a company
# that had any chat capability on keeps the whole chat surface.
_LEGACY_AGENTS_KEYS = ("on_demand_analysis", "auto_prd_generation")


def agents_enabled(flags: dict | None) -> bool:
    """Resolve the `agents` module from a raw feature_flags dict.

    Precedence (mirrors the staff panel's agentsEnabled, plus fail-open
    defaults for grandfathered rows):
      1. explicit `agents` key → its boolean value (only an explicit
         `agents: false` can turn the chat surface off);
      2. else, legacy keys present → OR of on_demand_analysis /
         auto_prd_generation;
      3. else (empty dict, None, non-dict junk, or only irrelevant keys)
         → ON.
    """
    if not isinstance(flags, dict) or not flags:
        return True
    if "agents" in flags:
        return bool(flags["agents"])
    if any(key in flags for key in _LEGACY_AGENTS_KEYS):
        return any(bool(flags.get(key)) for key in _LEGACY_AGENTS_KEYS)
    return True


def weekly_brief_enabled(flags: dict | None) -> bool:
    """Resolve the `weekly_brief` module from a raw feature_flags dict.

    Same shape as `agents` but with no legacy aliases: a missing key is ON
    (grandfathering); only an explicit `weekly_brief: false` turns the
    weekly-brief process off.
    """
    if not isinstance(flags, dict):
        return True
    if "weekly_brief" in flags:
        return bool(flags["weekly_brief"])
    return True


def feature_flags_for_company(company_id: str) -> dict:
    """A company's raw feature_flags dict, `{}` on any failure.

    Lenient on READ FAILURE only (legacy schema without the column, fake
    test client, transient DB error ⇒ {} ⇒ every module resolves ON,
    matching the grandfather semantics) — an explicit false stored in the
    row is always respected. Mirrors prototype_enabled_for_company.
    """
    from app.db.client import require_client

    try:
        rows = (
            require_client()
            .table("companies")
            .select("feature_flags")
            .eq("id", company_id)
            .limit(1)
            .execute()
            .data
            or []
        )
    except Exception:  # noqa: BLE001 — fail open, see docstring
        return {}
    if not rows:
        return {}
    flags = rows[0].get("feature_flags")
    return flags if isinstance(flags, dict) else {}


def require_agents_module(
    company: WorkspaceContext = Depends(require_workspace),
) -> WorkspaceContext:
    """FastAPI dependency: require_workspace + the `agents` module gate.

    Drop-in replacement for `Depends(require_workspace)` on chat-surface
    routes — returns the same WorkspaceContext (a CompanyContext subclass,
    so company-scoped callers keep working), or 403s when the caller's
    company has the Agents module explicitly disabled. The module flag
    itself stays COMPANY-level; only the returned context carries the
    active workspace.
    """
    if not agents_enabled(feature_flags_for_company(company.company_id)):
        logger.info(
            "Agents module disabled for company %s — rejecting", company.company_id
        )
        raise HTTPException(status_code=403, detail=AGENTS_DISABLED_DETAIL)
    return company


def require_weekly_brief_module(
    company: WorkspaceContext = Depends(require_workspace),
) -> WorkspaceContext:
    """FastAPI dependency: require_workspace + the `weekly_brief` module gate.

    For the on-demand brief generation/regeneration endpoints. Read-only
    brief endpoints (current/status/by-id/…) stay ungated — existing briefs
    remain visible when the module is toggled off; only new generation and
    delivery stop.
    """
    if not weekly_brief_enabled(feature_flags_for_company(company.company_id)):
        logger.info(
            "Weekly Brief module disabled for company %s — rejecting",
            company.company_id,
        )
        raise HTTPException(status_code=403, detail=WEEKLY_BRIEF_DISABLED_DETAIL)
    return company
