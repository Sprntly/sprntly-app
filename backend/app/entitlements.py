"""Per-company module entitlements (companies.feature_flags JSONB).

The staff admin panel (routes/staff_admin.py) stores per-company module
flags in `companies.feature_flags`. This module is the ONE place that
resolves a raw flags dict into module on/off decisions, and exposes the
FastAPI dependencies that enforce them server-side:

  * ``agents``       — ALL chat-surface capability (the Ask/Q&A agent,
                       skill routing, chat commands like PRD/ticket/VoC
                       generation from chat). Enforced on the ask + agent
                       chat routes via ``require_agents_module``.
  * ``top_insights`` — the Top Insights brief PROCESS (scheduled generation
                       and Slack/email delivery, plus the on-demand brief
                       generation/regeneration endpoints). Enforced on
                       routes via ``require_top_insights_module`` and in
                       the scheduler's company loops via
                       ``top_insights_enabled``. Formerly ``weekly_brief``;
                       the legacy key is still honored as an alias for rows
                       written before the rename (a migration renames stored
                       keys, but a concurrently-running old backend or a
                       restored row must not flip a company ON by accident).
  * ``company_research`` — the deep company-research sweep (staged web
                       research about the company's OWN public footprint →
                       KG signals). Costs real money per run, so it carries
                       a kill switch; checked in routes/onboarding.py (the
                       post-website-submit kick) and in qa_agent's chat
                       dispatch, both via ``company_research_enabled``.

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
TOP_INSIGHTS_DISABLED_DETAIL = (
    "The Top Insights module is not enabled for your organization."
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


def top_insights_enabled(flags: dict | None) -> bool:
    """Resolve the `top_insights` module from a raw feature_flags dict.

    Same shape as `agents`: a missing key is ON (grandfathering); an explicit
    `top_insights: false` turns the Top Insights process off. `weekly_brief`
    is the pre-rename spelling of the same module and acts as its legacy
    alias — consulted only when the modern key is absent, so an explicit
    modern key always wins.
    """
    if not isinstance(flags, dict):
        return True
    if "top_insights" in flags:
        return bool(flags["top_insights"])
    if "weekly_brief" in flags:
        return bool(flags["weekly_brief"])
    return True


def company_research_enabled(flags: dict | None) -> bool:
    """Resolve the `company_research` flag from a raw feature_flags dict.

    Gates the deep company-research sweep (app/company_research.py) on BOTH of
    its surfaces — the onboarding kick and the chat ask — so an explicit
    `company_research: false` means "we never spend money researching this
    company on the public web", not "off in one place only".

    Fail-open like every other module flag: a missing key is ON, so existing
    companies (feature_flags = {} or legacy keys only) get the capability
    without a backfill, and only an explicit false in the staff panel turns it
    off. There is no legacy alias — the key is new with this feature.
    """
    if not isinstance(flags, dict):
        return True
    if "company_research" in flags:
        return bool(flags["company_research"])
    return True


def ds_claude_analysis_enabled(flags: dict | None) -> bool:
    """Resolve the `ds_claude_analysis` flag from a raw feature_flags dict.

    Gates the Claude code-execution data-analysis engine (app/ds/claude_analysis)
    against the deterministic v5.8 engine. DEFAULT ON since 2026-07-30 (Apurva):
    a missing key is ON, so existing companies get it without a backfill and only
    an explicit `ds_claude_analysis: false` — set per company in the staff panel —
    opts out. Same grandfather shape as `agents` / `top_insights` /
    `company_research`.

    ONE DELIBERATE DIVERGENCE from its siblings: an UNKNOWN flag state resolves
    OFF, not ON. `None` here means the read never reached the row (see
    `read_feature_flags`), and this flag does not gate a UI module — it gates
    whether a company's raw uploaded CSVs are shipped to the Anthropic Files API.
    Fail-open is the right default for "can this tenant see a feature"; it is the
    wrong default for "may we send this tenant's data off the box", where the
    safe answer to "I don't know" is no. The cost of failing closed is the
    deterministic engine answering instead — a working answer, not an error.
    """
    if not isinstance(flags, dict):
        return False
    if "ds_claude_analysis" in flags:
        return bool(flags["ds_claude_analysis"])
    return True


def cross_connector_sweep_enabled(flags: dict | None) -> bool:
    """Resolve the `chat_cross_connector_sweep` flag from a raw feature_flags dict.

    Gates the cross-connector sweep (app/connector_lookup/sweep.py): whether a
    source-agnostic chat question additionally reads the company's connected
    tools live before answering, instead of answering from the corpus + KG
    snapshot alone.

    Fail-open like `agents` / `top_insights` / `company_research`: a missing key
    is ON, so existing companies get the capability without a backfill and only
    an explicit `chat_cross_connector_sweep: false` in the staff panel opts out.

    Fail-open is right HERE, unlike `ds_claude_analysis`, and the difference is
    what the flag governs. That one decides whether a tenant's raw uploaded CSVs
    leave the box, so an unknown state must resolve to "no". This one only
    decides whether we ALSO read sources the tenant has already connected to
    Sprntly, through the same tenant-bound, read-only adapters chat uses when a
    question names them. Nothing leaves the tenant that was not already theirs,
    so the cost of being wrong is latency, not exposure — and `settings.
    chat_cross_connector_sweep` is the global lever for that.
    """
    if not isinstance(flags, dict):
        return True
    if "chat_cross_connector_sweep" in flags:
        return bool(flags["chat_cross_connector_sweep"])
    return True


def ask_planner_shadow_enabled(flags: dict | None) -> bool:
    """Resolve the `ask_planner_shadow` flag from a raw feature_flags dict.

    Gates the SHADOW-MODE Ask planner (app/ask_planner.py) — an extra LLM call
    that runs alongside `qa_agent.route`, logs what it would have decided, and
    acts on nothing. Slice 1 of backend/docs/ASK_PLANNER.md.

    DEFAULT OFF, which is the reverse of every sibling in this module, and the
    reverse on BOTH of the two "not an explicit true" cases:

      * explicit `true`             → ON
      * key absent                  → OFF
      * flags UNKNOWN (read failed) → OFF

    `agents` / `top_insights` / `company_research` grandfather a missing key ON
    so existing companies keep a capability they already had without a backfill.
    This flag gates the opposite kind of thing: not a capability anyone has, but
    an extra paid model call on EVERY chat message, collecting measurements for
    a feature that does not exist yet. Nobody has opted in, so a missing key must
    mean "not enrolled" rather than "enrolled by default", and an unreadable
    flags row must mean the same — "I couldn't read your flags" is not a reason
    to start spending a company's tokens. `ds_claude_analysis` already
    established the failed-read half of that reasoning; this flag extends it to
    the missing-key half because the spend is unconditional rather than gated on
    a rare question shape.

    The cost of failing closed is exactly one missing shadow row.
    """
    if not isinstance(flags, dict):
        return False
    return bool(flags.get("ask_planner_shadow", False))


def read_feature_flags(company_id: str) -> dict | None:
    """A company's raw feature_flags dict, or None when the READ itself failed.

    Distinguishes the two cases `feature_flags_for_company` deliberately
    collapses into `{}`: flags that genuinely contain no keys, and a read that
    never reached the row (legacy schema, fake test client, transient DB error).
    Callers that grandfather a missing key ON cannot tell those apart from `{}`
    alone — and a caller that must fail CLOSED on an unknown state needs to.
    """
    from app.db.authcache import feature_flags_cache
    from app.db.client import require_client

    # TTL-cached like every other per-request tenancy read (this was the only
    # one that wasn't, and a single ask read the row three times). Writes
    # invalidate via `authcache.invalidate_feature_flags`, so a toggled flag
    # applies on the next request rather than after the TTL.
    cached = feature_flags_cache.get(company_id)
    if cached is not None:
        return cached

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
    except Exception:  # noqa: BLE001 — the caller decides open vs closed
        logger.warning("feature_flags read failed for %s", company_id, exc_info=True)
        # Deliberately NOT cached. None means the read FAILED, and
        # `feature_flags_for_company` grandfathers a failed read to "on" —
        # caching it would grant a company whose flags say `agents: false` a
        # full TTL of access. Only a genuine dict is stored below. Same shape
        # of invariant as authcache's "never cache empty memberships".
        return None
    flags = rows[0].get("feature_flags") if rows else None
    resolved = flags if isinstance(flags, dict) else {}
    feature_flags_cache.set(company_id, resolved)
    return resolved


def feature_flags_for_company(company_id: str) -> dict:
    """A company's raw feature_flags dict, `{}` on any failure.

    Lenient on READ FAILURE only (legacy schema without the column, fake
    test client, transient DB error ⇒ {} ⇒ every module resolves ON,
    matching the grandfather semantics) — an explicit false stored in the
    row is always respected. Mirrors prototype_enabled_for_company.

    Unchanged contract, now expressed over `read_feature_flags`: a failed read
    (None) and empty flags ({}) both collapse to {} here, which is exactly the
    fail-open behaviour every module gate already depends on.
    """
    flags = read_feature_flags(company_id)
    return flags if flags is not None else {}


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


def require_top_insights_module(
    company: WorkspaceContext = Depends(require_workspace),
) -> WorkspaceContext:
    """FastAPI dependency: require_workspace + the `top_insights` module gate.

    For the on-demand brief generation/regeneration endpoints. Read-only
    brief endpoints (current/status/by-id/…) stay ungated — existing briefs
    remain visible when the module is toggled off; only new generation and
    delivery stop.
    """
    if not top_insights_enabled(feature_flags_for_company(company.company_id)):
        logger.info(
            "Top Insights module disabled for company %s — rejecting",
            company.company_id,
        )
        raise HTTPException(status_code=403, detail=TOP_INSIGHTS_DISABLED_DETAIL)
    return company
