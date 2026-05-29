"""DB helpers for `prototypes` + `prototype_checkpoints` (P1-06).

Mirrors the canonical triad shape in `backend/app/db/prds.py` /
`backend/app/db/evidences.py`:

- start_prototype           — insert a generating row, return its id
- complete_prototype        — set status='ready', bundle_url, completed_at
- fail_prototype            — set status='failed', error (truncated 500 chars)
- get_prototype             — single-row fetch, workspace-filtered
- find_existing_prototype   — most recent ready/generating row for
                              (prd_id, workspace_id, template_version, variant)
- create_checkpoint         — insert a prototype_checkpoints row, return its id
- invalidate_orphan_generating_prototypes — startup hook: flip stuck
                              'generating' rows (orphaned across restart) to 'failed'
- invalidate_stale_prototypes — startup hook: flip 'ready' rows whose
                              template_version < current to 'invalidated'

These helpers are *synchronous* and use `require_client()` + `utc_now()`,
mirroring `db/prds.py` exactly — supabase-py is a synchronous client, and the
existing async-task routes (e.g. `routes/prd.py`) call these sync helpers
directly from their async handlers. (The P1-06 ticket's pseudo-code sketched
`async def`, but its own "mirror db/prds.py exactly / match that pattern
verbatim" directive and AC #14 conformance sweep resolve the conflict in favour
of the codebase pattern. An `async def` wrapper over the sync supabase-py client
would block the event loop while pretending to be concurrent.)

Workspace isolation (Architecture Rules #20-#23):
- INSERTs populate `workspace_id` from the caller (the route passes
  `require_session().aud` through; it is NEVER hardcoded here).
- User-driven SELECT / UPDATE / DELETE filter by `workspace_id`.
- Background invalidation helpers (orphan + stale) operate across ALL
  workspaces — the comment above each says so explicitly.

Scenario model (spec §3): the A/B/C/0 input scenarios are DERIVED from the
input columns via `infer_scenario_from_inputs` / `infer_scenario`, never stored
as a column. See the migration header for the rationale.
"""
from __future__ import annotations

import logging
from typing import Any

from app.db.client import require_client, utc_now

logger = logging.getLogger(__name__)

_TABLE = "prototypes"
_CHECKPOINT_TABLE = "prototype_checkpoints"


# ─── Scenario inference (derived, never stored) ──────────────────────────


def infer_scenario_from_inputs(
    *,
    figma_file_key: str | None,
    website_url: str | None,
    github_installation_id: int | None,
    prd_references_codebase: bool,
) -> frozenset[str]:
    """Pure helper: derive the scenario set from raw inputs.

    Decouples the inference from the prototype row shape so the route, the
    runner, and the cost-summary log can all call it without a DB hit.

    Per spec §3:
      A: Figma file key present
      B: website URL present AND no Figma
      C: GitHub installation present AND PRD :::design block references a
         codebase target
      0: none of the above
    Scenarios are additive — A + C is valid (frozenset({'A', 'C'})).
    """
    scenarios: set[str] = set()
    if figma_file_key:
        scenarios.add("A")
    if website_url and not figma_file_key:
        scenarios.add("B")
    if github_installation_id and prd_references_codebase:
        scenarios.add("C")
    if not scenarios:
        scenarios.add("0")
    return frozenset(scenarios)


def infer_scenario(prototype: dict[str, Any], prd: dict[str, Any] | None) -> frozenset[str]:
    """Derive the scenario set from a prototype row + its PRD.

    `prd` may be None when the caller only has the prototype (e.g. a log line
    that does not want a PRD fetch) — in that case Scenario C is never inferred
    (acceptable: better to under-report than over-report).

    Codebase-target detection (the PRD `:::design`-block parse) lands in P4-05;
    until then `prd_references_codebase` is the safe `False` default, so
    Scenario C does not activate. The `prd` argument is accepted now so the
    signature is stable when P4-05 wires the real detector.
    """
    return infer_scenario_from_inputs(
        figma_file_key=prototype.get("figma_file_key"),
        website_url=prototype.get("website_url"),
        github_installation_id=prototype.get("github_installation_id"),
        prd_references_codebase=False,  # P4-05 replaces with the real detector
    )


# ─── Async-task triad (mirrors db/prds.py) ───────────────────────────────


def start_prototype(
    *,
    prd_id: int,
    workspace_id: str,                # from require_session().aud
    template_version: int,
    variant: str = "v1",
    instructions: str = "",
    target_platform: str = "both",
    figma_file_key: str | None = None,
    website_url: str | None = None,
    github_installation_id: int | None = None,
) -> int:
    """Insert a generating row, return its id. State transition: prototype_created.

    Scenario inputs (figma_file_key, website_url, github_installation_id) are
    stored as snapshots of what was available at generate time. Scenario LABELS
    (A/B/C/0) are computed at read time via infer_scenario(...); never persisted.

    Keyword-only args (the `*`) prevent positional confusion between `prd_id`,
    `workspace_id`, and `template_version` — cheap discipline given that a
    workspace_id mix-up is a cross-tenant-leak class of bug.
    """
    c = require_client()
    resp = c.table(_TABLE).insert({
        "prd_id": prd_id,
        "workspace_id": workspace_id,
        "status": "generating",
        "variant": variant,
        "template_version": template_version,
        "instructions": instructions,
        "target_platform": target_platform,
        "figma_file_key": figma_file_key,
        "website_url": website_url,
        "github_installation_id": github_installation_id,
    }).execute()
    row_id = resp.data[0]["id"]
    # Inferred-scenario logged for observability; never written to the row.
    # Only the derived label (A/B/C/0) is logged — never the input *values*
    # (figma_file_key, website_url, instructions) — per Rule #24 (no PII / no
    # secrets in logs).
    scenario_label = ",".join(sorted(infer_scenario_from_inputs(
        figma_file_key=figma_file_key,
        website_url=website_url,
        github_installation_id=github_installation_id,
        prd_references_codebase=False,  # PRD body not available here; route enriches if needed
    )))
    logger.info(
        "prototype_created prototype_id=%s prd_id=%s scenario=%s",
        row_id, prd_id, scenario_label,
    )
    return row_id


def complete_prototype(
    *,
    prototype_id: int,
    workspace_id: str,                # explicit filter (Rule #22)
    bundle_url: str,
    current_checkpoint_id: int | None = None,
) -> None:
    """Mark ready + populate bundle_url. State transition: prototype_completed."""
    c = require_client()
    patch: dict[str, Any] = {
        "status": "ready",
        "bundle_url": bundle_url,
        "completed_at": utc_now(),
        "error": None,
    }
    if current_checkpoint_id is not None:
        patch["current_checkpoint_id"] = current_checkpoint_id
    (
        c.table(_TABLE)
        .update(patch)
        .eq("id", prototype_id)
        .eq("workspace_id", workspace_id)  # explicit workspace filter
        .execute()
    )
    logger.info("prototype_completed prototype_id=%s", prototype_id)


def fail_prototype(
    *,
    prototype_id: int,
    workspace_id: str,
    error: str,
) -> None:
    """Mark failed. Matches the existing fail_* error format (truncated 500 chars)."""
    c = require_client()
    (
        c.table(_TABLE)
        .update({
            "status": "failed",
            "error": (error or "")[:500],
            "completed_at": utc_now(),
        })
        .eq("id", prototype_id)
        .eq("workspace_id", workspace_id)
        .execute()
    )
    logger.info(
        "prototype_failed prototype_id=%s error_class=%s",
        prototype_id, (error or "").split(":", 1)[0][:80],
    )


def get_prototype(
    *,
    prototype_id: int,
    workspace_id: str,
) -> dict[str, Any] | None:
    """Single-row fetch for the GET /v1/design-agent/{id} route. Workspace-filtered."""
    c = require_client()
    resp = (
        c.table(_TABLE)
        .select("*")
        .eq("id", prototype_id)
        .eq("workspace_id", workspace_id)
        .limit(1)
        .execute()
    )
    return resp.data[0] if resp.data else None


def find_existing_prototype(
    *,
    prd_id: int,
    workspace_id: str,
    template_version: int,
    variant: str = "v1",
) -> dict[str, Any] | None:
    """Return the most recent ready/generating row for the key, else None.

    Filters by `workspace_id` — user-driven query per Rule #22. Ordered by `id`
    descending (mirrors find_existing_prd: monotonic identity is a more
    deterministic "most recent" than same-second created_at timestamps).
    """
    c = require_client()
    resp = (
        c.table(_TABLE)
        .select("*")
        .eq("prd_id", prd_id)
        .eq("workspace_id", workspace_id)
        .eq("template_version", template_version)
        .eq("variant", variant)
        .in_("status", ["ready", "generating"])
        .order("id", desc=True)
        .limit(1)
        .execute()
    )
    return resp.data[0] if resp.data else None


def create_checkpoint(
    *,
    prototype_id: int,
    workspace_id: str,
    bundle_url: str | None,
    prd_revision_hash: str | None,
    figma_frame_hash: str | None,
    prompt_history: list[dict[str, Any]],
    comment_state: list[dict[str, Any]] | None = None,
) -> int:
    """Insert a checkpoint row; return its id. P3 wires comment_state."""
    c = require_client()
    resp = c.table(_CHECKPOINT_TABLE).insert({
        "prototype_id": prototype_id,
        "workspace_id": workspace_id,
        "bundle_url": bundle_url,
        "prd_revision_hash": prd_revision_hash,
        "figma_frame_hash": figma_frame_hash,
        "prompt_history": prompt_history,
        "comment_state": comment_state or [],
    }).execute()
    return resp.data[0]["id"]


# ─── Background / lifespan helpers (operate across ALL workspaces) ────────


def invalidate_orphan_generating_prototypes() -> int:
    """Startup hook: flip stuck 'generating' rows (process died mid-run) to 'failed'.

    Operates ACROSS ALL WORKSPACES — this is a system-wide cleanup, not a
    user-driven query, so it deliberately does NOT filter by workspace_id
    (Rule #23). Mirrors the select-then-update-by-id shape of
    invalidate_orphan_generating_prds. Returns the count of rows updated.
    """
    c = require_client()
    rows = c.table(_TABLE).select("id").eq("status", "generating").execute().data
    ids = [r["id"] for r in rows]
    if ids:
        (
            c.table(_TABLE)
            .update({
                "status": "failed",
                "error": "orphaned: process restarted mid-generation",
                "completed_at": utc_now(),
            })
            .in_("id", ids)
            .execute()
        )
        logger.info("prototype_orphan_cleared count=%s", len(ids))
    return len(ids)


def invalidate_stale_prototypes(current_version: int, variant: str = "v1") -> int:
    """Startup hook: flip 'ready' rows whose template_version < current to 'invalidated'.

    Operates ACROSS ALL WORKSPACES — a system-wide cache demote on prompt-version
    bump, not user-driven, so it deliberately does NOT filter by workspace_id
    (Rule #23). Variant-scoped, mirroring invalidate_stale_prds: distinct
    prototype templates do not invalidate one another. Only strictly-older
    versions are demoted (a row stamped with a newer version is left alone).
    Returns the count of rows updated.
    """
    c = require_client()
    rows = (
        c.table(_TABLE)
        .select("id, template_version")
        .eq("status", "ready")
        .eq("variant", variant)
        .execute()
        .data
    )
    stale_ids = [
        r["id"] for r in rows
        if r.get("template_version") is not None and r["template_version"] < current_version
    ]
    if stale_ids:
        c.table(_TABLE).update({"status": "invalidated"}).in_("id", stale_ids).execute()
        logger.info(
            "prototype_stale_invalidated count=%s current_version=%s variant=%s",
            len(stale_ids), current_version, variant,
        )
    return len(stale_ids)
