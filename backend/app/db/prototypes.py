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
import time
import uuid
from threading import Lock
from typing import Any

from argon2 import PasswordHasher

from app.db.client import require_client, retry_on_disconnect, utc_now

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


@retry_on_disconnect
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
    screenshot_key: str | None = None,
) -> int:
    """Insert a generating row, return its id. State transition: prototype_created.

    Scenario inputs (figma_file_key, website_url, github_installation_id,
    screenshot_key) are stored as snapshots of what was available at generate
    time. Scenario LABELS (A/B/C/0) are computed at read time via
    infer_scenario(...); never persisted.

    Keyword-only args (the `*`) prevent positional confusion between `prd_id`,
    `workspace_id`, and `template_version` — cheap discipline given that a
    workspace_id mix-up is a cross-tenant-leak class of bug.
    """
    c = require_client()
    payload: dict[str, Any] = {
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
        # Every prototype is born with a stable share_token. share_mode stays its
        # 'private' default so the token is present but never exposed (the public
        # resolver 404s private rows); set_share_config flips the mode without
        # rotating this token, giving one permanent /p/<slug>/<token> URL.
        "share_token": str(uuid.uuid4()),
    }
    # Write screenshot_key only when supplied — the optional-column convention
    # (mirrors db/prototype_comments.insert_comment): a keyless insert's payload
    # carries exactly the prior column set, so environments whose schema predates
    # the column keep working and the null stays an honest "no screenshot" signal.
    if screenshot_key is not None:
        payload["screenshot_key"] = screenshot_key
    resp = c.table(_TABLE).insert(payload).execute()
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
    preview_image_url: str | None = None,
) -> None:
    """Mark ready + populate bundle_url. State transition: prototype_completed.

    `preview_image_url` is the optional thumbnail URL captured on completion; it is
    included in the patch ONLY when non-None, so existing callers that omit it
    produce a byte-for-byte identical UPDATE (the column simply stays null).
    """
    c = require_client()
    patch: dict[str, Any] = {
        "status": "ready",
        "bundle_url": bundle_url,
        "completed_at": utc_now(),
        "error": None,
    }
    if current_checkpoint_id is not None:
        patch["current_checkpoint_id"] = current_checkpoint_id
    if preview_image_url is not None:
        patch["preview_image_url"] = preview_image_url
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


@retry_on_disconnect
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


@retry_on_disconnect
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


def find_prototype_by_prd(
    *,
    prd_id: int,
    workspace_id: str,
    statuses: list[str] | None = None,
) -> dict[str, Any] | None:
    """Return the most-recent prototype for a PRD matching `statuses`, or None.

    Read-only, no generate side-effect (unlike find_existing_prototype, which
    also gates POST /generate's dedupe and filters on template_version/variant
    — a different helper for a different purpose, kept separate). Filtered to
    the caller's workspace, newest by id. `statuses=None` means no status
    filter at all (matches ANY status, including 'failed'/'invalidated') —
    this backs the three read-only /by-prd lookups:

      statuses=["ready"]              -> GET /by-prd/{prd_id}        (ready only)
      statuses=["ready", "generating"] -> GET /by-prd/{prd_id}/active (resume lookup)
      statuses=None                    -> GET /by-prd/{prd_id}/latest (any status,
                                           incl. 'failed' — backs the error+retry surface)
    """
    c = require_client()
    q = (
        c.table(_TABLE)
        .select("*")
        .eq("prd_id", prd_id)
        .eq("workspace_id", workspace_id)
    )
    if statuses is not None:
        q = q.in_("status", statuses)
    resp = q.order("id", desc=True).limit(1).execute()
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


def restore_template_demoted_prototypes() -> int:
    """One-time: un-hide template-demoted prototypes. Flip status
    'invalidated' -> 'ready' for rows that still have a bundle_url.
    Operates across ALL workspaces (mirrors the system-wide demote it
    reverses) — workspace_id is read-only and preserved per row.
    Idempotent: a second run matches no rows and is a no-op.
    """
    c = require_client()
    rows = (c.table(_TABLE).select("id")
            .eq("status", "invalidated")
            .not_.is_("bundle_url", "null")
            .execute().data)
    ids = [r["id"] for r in rows]
    if ids:
        c.table(_TABLE).update({"status": "ready"}).in_("id", ids).execute()
    return len(ids)


# ─── Preview-image backfill helpers ───────────────────────────────────────
#
# Used by the `python -m app.backfill_previews` one-off to repair prototypes
# whose preview captured the un-hydrated SPA shell (or never captured at all).
# The backfill re-renders the staged bundle locally and writes the result with
# `set_preview_image_url`; it iterates candidate rows with
# `list_ready_prototypes_for_backfill`. Status/completed_at are left untouched —
# only the thumbnail is corrected.


def set_preview_image_url(
    *,
    prototype_id: int,
    workspace_id: str,
    preview_image_url: str,
) -> None:
    """Update ONLY `preview_image_url` on a single row. Workspace-filtered.

    A targeted update for the preview backfill — it does not touch status,
    bundle_url, or completed_at, so re-running it on an already-correct row only
    rewrites the same column (idempotent). Workspace-filtered.
    """
    c = require_client()
    (
        c.table(_TABLE)
        .update({"preview_image_url": preview_image_url})
        .eq("id", prototype_id)
        .eq("workspace_id", workspace_id)
        .execute()
    )
    logger.info("prototype_preview_backfilled prototype_id=%s", prototype_id)


@retry_on_disconnect
def list_ready_prototypes_for_backfill() -> list[dict[str, Any]]:
    """Return ready rows that have a current_checkpoint, for the preview backfill.

    Operates ACROSS ALL WORKSPACES — an operator-run repair, not user-driven, so
    it does NOT filter by workspace_id (a system-side sweep, like the invalidation hooks).
    Projects only the columns the backfill needs (id, workspace_id,
    current_checkpoint_id, preview_image_url) so the caller can repair BOTH
    wrong-capture rows AND null-preview rows. A row with no current_checkpoint_id
    is skipped (nothing staged to re-render).
    """
    c = require_client()
    rows = (
        c.table(_TABLE)
        .select("id, workspace_id, current_checkpoint_id, preview_image_url")
        .eq("status", "ready")
        .execute()
        .data
        or []
    )
    return [r for r in rows if r.get("current_checkpoint_id") is not None]


# ─── Sharing config + passcode + public-route lookup (P2-06) ──────────────
#
# F6: share_token is an OPAQUE uuid4 — NOT a JWT, NOT derived from prototype_id,
# NOT signed. A brute-force scan of /p/<random-uuid> has no DB row, so the
# public resolver returns 404 (not 401). DESIGN_AGENT_TOKEN_SECRET (config.py)
# is bound for FUTURE HMAC-based token rotation and is intentionally NOT
# consumed here. The app session secret is NEVER reused for any Design Agent
# surface (skill-config Architecture Rule #14) — this module deliberately holds
# no reference to that secret.

_SHARE_MODES = {"private", "public", "passcode"}

# OWASP-default params (time_cost=3, memory_cost=64 MB, parallelism=4) — argon2id.
_PW_HASHER = PasswordHasher()


def hash_share_passcode(passcode: str) -> str:
    """Return the argon2id hash of a passcode for storage.

    Raises ValueError on empty input — a passcode-mode share with no passcode is
    a programming bug, not a runtime condition to swallow. The plaintext passcode
    is NEVER logged or persisted; only the hash is stored.
    """
    if not passcode:
        raise ValueError("hash_share_passcode: passcode is empty")
    return _PW_HASHER.hash(passcode)


def verify_share_passcode(plaintext: str, hashed: str | None) -> bool:
    """Return True iff `plaintext` matches the stored argon2id `hashed` value.

    Never raises: returns False on a missing hash, a malformed/garbage hash, or a
    wrong passcode. The caller (P2-05 passcode route) treats every False the same
    way, so collapsing all failure modes to False keeps the call-site simple and
    avoids leaking which failure occurred.
    """
    if not hashed or not plaintext:
        return False
    try:
        return _PW_HASHER.verify(hashed, plaintext)
    except Exception:
        # VerifyMismatchError / InvalidHashError / VerificationError all mean the
        # same thing to the caller: this passcode did not verify. Defensive
        # catch-all keeps the "never raises" contract.
        return False


def set_share_config(
    *,
    prototype_id: int,
    workspace_id: str,                          # explicit workspace filter (Rule #22)
    share_mode: str,                            # 'private' | 'public' | 'passcode'
    passcode: str | None = None,                # required iff share_mode == 'passcode'
) -> dict[str, Any]:
    """Update share_mode + share_token + share_passcode_hash for a prototype.

    Returns the updated row (including the generated share_token when the mode is
    not 'private'). Workspace-filtered: a prototype that is not in `workspace_id`
    raises ValueError (the standard isolation guard; this is NOT the public-route
    path — see find_prototype_by_share_token for that).

    Behaviour by mode:
      - 'private'  → share_mode=private; share_token PRESERVED (was: nulled); share_passcode_hash=NULL
      - 'public'   → share_mode=public;  share_token=uuid4() if NULL else preserved; hash=NULL
      - 'passcode' → share_mode=passcode; share_token=uuid4() if NULL else preserved; hash=argon2(passcode)
    Re-setting public→public (or passcode→passcode) does NOT rotate share_token
    (F7: the public URL is reused across regenerations).
    """
    if share_mode not in _SHARE_MODES:
        raise ValueError(f"set_share_config: unknown share_mode={share_mode!r}")
    if share_mode == "passcode" and not passcode:
        raise ValueError("set_share_config: passcode-mode requires a passcode")

    c = require_client()
    row = get_prototype(prototype_id=prototype_id, workspace_id=workspace_id)
    if not row:
        raise ValueError(f"set_share_config: prototype {prototype_id} not found in workspace")

    patch: dict[str, Any] = {"share_mode": share_mode}
    if share_mode == "private":
        # share_token is PRESERVED on private (static-URL invariant): the public
        # URL is stable across public→private→public toggles. Only the passcode
        # hash is cleared so a re-public does not silently retain a stale gate.
        patch["share_passcode_hash"] = None
    else:
        # Preserve an existing token (F7) — only mint one when none exists yet.
        patch["share_token"] = row.get("share_token") or str(uuid.uuid4())
        patch["share_passcode_hash"] = (
            hash_share_passcode(passcode) if share_mode == "passcode" else None
        )

    (
        c.table(_TABLE)
        .update(patch)
        .eq("id", prototype_id)
        .eq("workspace_id", workspace_id)  # explicit workspace filter (Rule #22)
        .execute()
    )
    # Rule #24 / #26: state-transition INFO line. Log the mode + id ONLY — never
    # the passcode plaintext and never the share_token (the token is the access
    # primitive, so it must not leak into log aggregation).
    logger.info("prototype_share_configured prototype_id=%s mode=%s", prototype_id, share_mode)
    return get_prototype(prototype_id=prototype_id, workspace_id=workspace_id)


@retry_on_disconnect
def find_prototype_by_share_token(token: str) -> dict[str, Any] | None:
    """Public-route lookup — deliberately does NOT filter by workspace_id.

    This is the ONE legitimate cross-workspace user-facing query in the codebase,
    justified by F6's design: the share_token IS the access primitive, so anyone
    holding the URL holds the access regardless of which workspace owns the row.
    Do NOT "fix" this to add a workspace filter — that would break public sharing.
    (Contrast get_prototype, which is workspace-filtered for the authenticated
    app surface.) Returns the full row, or None when the token has no row — which
    is what makes a /p/<random-uuid> scan return 404, not 401.
    """
    c = require_client()
    resp = c.table(_TABLE).select("*").eq("share_token", token).limit(1).execute()
    return resp.data[0] if resp.data else None


# ─── Passcode rate-limit primitive (in-memory token bucket) ──────────────
#
# 5 failures per minute per token. In-memory: a list of failure timestamps per
# token, pruned on each check. Process-local — matches Sprntly's single-uvicorn-
# worker pattern (backend/app/main.py). If/when Sprntly horizontal-scales this
# moves to Redis; for the 2-week build, in-memory is sufficient. Guarded by a
# Lock because the FastAPI TestClient (and uvicorn under threads) can call these
# from multiple threads.

_RATE_LIMIT_WINDOW_SEC = 60
_RATE_LIMIT_MAX_FAILURES = 5
_passcode_failures: dict[str, list[float]] = {}
_passcode_failures_lock = Lock()


def passcode_rate_limit_check(*, token: str, ip: str) -> bool:
    """Return True iff `token` has < 5 failures in the last 60s.

    The `ip` arg is accepted for FUTURE per-IP throttling (P5 hardening) and for
    log enrichment, but is intentionally NOT used in the limit decision here —
    the spec is "5/min/token" (BUILD-PHASES §Phase 2 deliverable #3). Prunes
    expired failure timestamps as a side effect of each check.
    """
    now = time.monotonic()
    with _passcode_failures_lock:
        history = _passcode_failures.get(token, [])
        fresh = [t for t in history if now - t < _RATE_LIMIT_WINDOW_SEC]
        _passcode_failures[token] = fresh
        return len(fresh) < _RATE_LIMIT_MAX_FAILURES


def passcode_rate_limit_register_failure(*, token: str) -> None:
    """Record one failed passcode attempt for `token` (monotonic timestamp)."""
    now = time.monotonic()
    with _passcode_failures_lock:
        _passcode_failures.setdefault(token, []).append(now)


def passcode_rate_limit_clear(*, token: str) -> None:
    """Clear the failure history for `token` — called on a SUCCESSFUL verify so a
    legitimate viewer is never rate-limited by their own earlier typos."""
    with _passcode_failures_lock:
        _passcode_failures.pop(token, None)


# ─── Lifecycle: Mark Complete / Resume / stale-handoff flag (P2-07) ───────────
#
# Structural cousins of `complete_prototype` (different patch semantics): all
# user-driven, all workspace-filtered (Rule #22). `flag_stale_handoff` operates
# on `prototype_exports` — the most-recent export row IS the handoff record (per
# the 2026-05-29 decision: no separate handoff_records table). That table is
# created by P2-09's migration; this helper is exercised against the in-memory
# fake until the trio merges (see the ticket's "THE KNOT" note).


def mark_complete(*, prototype_id: int, workspace_id: str) -> dict[str, Any]:
    """F14: set is_complete=true, promote current_checkpoint_id → complete_checkpoint_id.
    Idempotent: a re-call when already complete is a no-op (returns the row unchanged).
    """
    c = require_client()
    row = get_prototype(prototype_id=prototype_id, workspace_id=workspace_id)
    if not row:
        raise ValueError(f"mark_complete: prototype {prototype_id} not found")
    patch: dict[str, Any] = {"is_complete": True}
    # Promote current_checkpoint_id → complete_checkpoint_id only on the first
    # complete. A second complete (idempotent path) preserves the original
    # complete_checkpoint_id (the canonical lock point).
    if not row.get("is_complete"):
        patch["complete_checkpoint_id"] = row.get("current_checkpoint_id")
    (
        c.table(_TABLE)
        .update(patch)
        .eq("id", prototype_id)
        .eq("workspace_id", workspace_id)  # explicit workspace filter (Rule #22)
        .execute()
    )
    logger.info(
        "prototype_completed prototype_id=%s complete_checkpoint_id=%s",
        prototype_id,
        patch.get("complete_checkpoint_id", row.get("complete_checkpoint_id")),
    )
    return get_prototype(prototype_id=prototype_id, workspace_id=workspace_id)


def resume_iteration(*, prototype_id: int, workspace_id: str) -> dict[str, Any]:
    """F15: set is_complete=false. Does NOT clear complete_checkpoint_id —
    that's the historical lock point and stays. Idempotent.
    """
    c = require_client()
    (
        c.table(_TABLE)
        .update({"is_complete": False})
        .eq("id", prototype_id)
        .eq("workspace_id", workspace_id)  # explicit workspace filter (Rule #22)
        .execute()
    )
    logger.info("prototype_resumed prototype_id=%s", prototype_id)
    return get_prototype(prototype_id=prototype_id, workspace_id=workspace_id)


def flag_stale_handoff(*, prototype_id: int, workspace_id: str) -> int:
    """F15: mark the most recent export row for this prototype as stale.

    The most-recent `prototype_exports` row IS the handoff record (per the
    2026-05-29 decision: no separate handoff_records table). Sets `is_stale=true`
    on the most recent export row for this prototype + workspace. Returns the
    count of rows updated (0 if no non-stale export exists yet; 1 otherwise).

    Idempotent: re-calling when the most-recent export is already stale returns 0
    because the `is_stale = false` filter excludes it from the candidate set.
    """
    c = require_client()
    rows = (
        c.table("prototype_exports")
        .select("id")
        .eq("prototype_id", prototype_id)
        .eq("workspace_id", workspace_id)  # explicit workspace filter (Rule #22)
        .eq("is_stale", False)
        .order("id", desc=True)
        .limit(1)
        .execute()
        .data
    )
    if not rows:
        return 0
    export_id = rows[0]["id"]
    (
        c.table("prototype_exports")
        .update({"is_stale": True})
        .eq("id", export_id)
        .execute()
    )
    logger.info(
        "prototype_export_marked_stale prototype_id=%s export_id=%s",
        prototype_id, export_id,
    )
    return 1


async def record_export_at_complete(*, prototype_id: int, workspace_id: str) -> None:
    """P2-09: fills in P2-07's stub. Generates the markdown via the serialiser
    and persists to prototype_exports. Idempotent on (prototype_id, checkpoint_id):
    a re-call after the first Mark Complete on the same checkpoint no-ops.

    Async (P2-07's stub was sync): `render_export_markdown` is async, so the
    POST /complete handler awaits this. The local imports avoid an import cycle
    at module load (export.py imports get_prototype from this module).
    """
    from app.design_agent.export import render_export_markdown
    from app.db.prototype_exports import insert_prototype_export
    proto = get_prototype(prototype_id=prototype_id, workspace_id=workspace_id)
    if not proto:
        # Race / orphan — log and continue. The /complete handler succeeded;
        # absence of the row here means the row was deleted between the
        # handler's get_prototype check and now. Don't raise; the /complete
        # response is already committed.
        logger.warning(
            "record_export_at_complete_skipped prototype_id=%s reason=missing_row",
            prototype_id,
        )
        return
    checkpoint_id = proto.get("complete_checkpoint_id")
    if not checkpoint_id:
        # Should never happen — mark_complete sets complete_checkpoint_id —
        # but defensive against a future change to mark_complete semantics.
        logger.warning(
            "record_export_at_complete_skipped prototype_id=%s reason=no_checkpoint",
            prototype_id,
        )
        return
    try:
        markdown = await render_export_markdown(
            prototype_id=prototype_id,
            checkpoint_id=checkpoint_id,
            workspace_id=workspace_id,
        )
    except ValueError as exc:
        # Serialiser failure (missing PRD, mismatched checkpoint, etc.) —
        # log and return; the /complete response is already committed, so
        # the prototype is locked but the export will need regeneration via
        # the GET-export fallback path.
        logger.warning(
            "record_export_at_complete_failed prototype_id=%s checkpoint_id=%s error_class=%s",
            prototype_id, checkpoint_id, type(exc).__name__,
        )
        return
    insert_prototype_export(
        prototype_id=prototype_id,
        checkpoint_id=checkpoint_id,
        workspace_id=workspace_id,
        markdown_content=markdown,
    )


# ─── F12 clarifying-question pause (P3-08) ────────────────────────────────────
#
# The clarifying_question exit-sentinel persists its question as a SIDECAR on the
# prototype row (`pending_question` jsonb). It is NOT a new status value — a
# paused prototype stays 'ready' and `pending_question IS NOT NULL` is the
# "awaiting answer" signal. Both helpers are workspace-filtered (Rule #22) and
# additive (they touch only the new column; existing helpers are unchanged).


def set_pending_question(
    *,
    prototype_id: int,
    workspace_id: str,                 # explicit workspace filter (Rule #22)
    question: dict[str, Any] | None,
) -> None:
    """F12: write the clarifying-question payload to `prototypes.pending_question`.

    `question` is the {question, choices?, context?} dict from the sentinel (or
    None to clear). Workspace-filtered: a 'demo' call never touches an 'app' row.
    Does NOT change `status` — the sidecar IS the awaiting-answer signal.

    Logs identifiers ONLY (Rule #24) — the question TEXT is never logged: it can
    embed PRD / product detail. `set` vs `cleared` is derivable from whether
    `question` is None, so the log line records only the id + the action.
    """
    c = require_client()
    (
        c.table(_TABLE)
        .update({"pending_question": question})
        .eq("id", prototype_id)
        .eq("workspace_id", workspace_id)  # explicit workspace filter (Rule #22)
        .execute()
    )
    logger.info(
        "prototype_question_%s prototype_id=%s",
        "set" if question is not None else "cleared",
        prototype_id,
    )


def clear_pending_question(*, prototype_id: int, workspace_id: str) -> None:
    """F12: null out `prototypes.pending_question` (the answer arrived, P3-16).

    Thin wrapper over `set_pending_question(question=None)` so the call site that
    resumes a paused run reads as an explicit clear. Workspace-filtered."""
    set_pending_question(
        prototype_id=prototype_id,
        workspace_id=workspace_id,
        question=None,
    )


def set_grounding_note(
    *,
    prototype_id: int,
    workspace_id: str,
    note: str,
) -> None:
    """Persist a plain-English note that this run's codebase grounding
    degraded below what the request asked for. A sidecar, like
    pending_question — no new status value. Set once, at generation time,
    when design_source == "github" but the recreate pre-seed ended up with no
    map (blank-canvas) or no matched screen (shell-only). Never cleared: the
    note describes how THIS run resolved grounding, not a live mutable state.
    Workspace-filtered (cross-tenant safety). Logs identifiers only — the
    note text itself is fixed boilerplate (never PII/secrets), but the log
    stays identifier-only per this file's existing convention.
    """
    c = require_client()
    (
        c.table(_TABLE)
        .update({"grounding_note": note})
        .eq("id", prototype_id)
        .eq("workspace_id", workspace_id)
        .execute()
    )
    logger.info("prototype_grounding_degraded prototype_id=%s", prototype_id)


# ─── Checkpoint chain: advance current_checkpoint_id on iterate (P3-12, F7) ───
#
# The iterate staging path (`_stage_iterate_run`, P3-05) deliberately does NOT
# call `complete_prototype` — re-stamping `completed_at` + emitting
# `prototype_completed` is wrong semantics for an iterate (B2 decision,
# 2026-05-30). So the checkpoint advance that `complete_prototype` performs "for
# free" on the GENERATE path does NOT happen for free on the iterate path; this
# helper is the iterate-correct counterpart that advances `current_checkpoint_id`
# + `bundle_url` WITHOUT touching `completed_at` / `status`.
#
# F7 (stable URL, no version history in MVP): the chain is forward-only —
# `current_checkpoint_id` always points at the newest checkpoint, older
# `prototype_checkpoints` rows are retained (AD6 atomic snapshots) but never
# served. This helper MUST NOT rotate `share_token` or change `share_mode`: an
# external viewer on `/p/<token>` keeps the same URL and now sees the new
# checkpoint's bundle because the public resolver reads `bundle_url`.


def advance_current_checkpoint(
    *,
    prototype_id: int,
    workspace_id: str,                 # explicit workspace filter (Rule #22)
    checkpoint_id: int,
    bundle_url: str | None,
) -> dict[str, Any] | None:
    """Point the prototype at the LATEST checkpoint (F7: stable URL, latest
    content). Updates `current_checkpoint_id` + `bundle_url`, workspace-filtered.

    Does NOT touch `share_token` / `share_mode` (F7: the URL is reused across
    regenerations — the token is unchanged) and does NOT re-stamp `completed_at`
    / `status` (the iterate-correct counterpart to `complete_prototype` —
    B2/AC6a). Returns the updated row, or None when no row matched the
    (prototype_id, workspace_id) pair (a cross-workspace call is a no-op).
    """
    c = require_client()
    (
        c.table(_TABLE)
        .update({"current_checkpoint_id": checkpoint_id, "bundle_url": bundle_url})
        .eq("id", prototype_id)
        .eq("workspace_id", workspace_id)  # explicit workspace filter (Rule #22)
        .execute()
    )
    # State-transition INFO line, identifiers only (Rule #24): never the
    # bundle_url (it is the storage path) and never the share_token.
    logger.info(
        "prototype_checkpoint_advanced prototype_id=%s checkpoint_id=%s",
        prototype_id, checkpoint_id,
    )
    return get_prototype(prototype_id=prototype_id, workspace_id=workspace_id)


# ─── F12 clarifying-question PAUSE-correction for the route bg layer (P4-08) ───
#
# When the agent calls `clarifying_question` mid-iterate, the runner returns
# `status='awaiting_clarification'` and persists the question on `pending_question`
# (P3-08). A pause is NOT a failure: by P3-08's design a paused prototype stays
# `status='ready'` and `pending_question IS NOT NULL` is the awaiting-answer
# signal. But the route bg layer (`_run_iterate_bg`) would otherwise route every
# non-complete status — including a pause — to `fail_prototype`, flipping the row
# to `status='failed'`. That flip then trips `post_iterate`'s `status != 'ready'`
# 409 guard, so the P3-16 answer-as-new-iterate is rejected and the
# clarify→answer→resume loop is dead. This helper is the status correction the bg
# layer must apply on a pause.
#
# Why a new helper rather than reusing one: the only helper that sets
# `status='ready'` is `complete_prototype`, but it also requires a `bundle_url`,
# re-stamps `completed_at`, and emits `prototype_completed` — wrong semantics for a
# pause. `advance_current_checkpoint` mutates `current_checkpoint_id` + `bundle_url`
# — also wrong. So no existing helper expresses "return the row to PAUSED-ready
# without touching the completion fields." This is the named, fail-closed
# counterpart.


def delete_prototype(*, prototype_id: int, workspace_id: str) -> None:
    """Hard-delete a prototype row. Workspace-filtered (Rule #22)."""
    c = require_client()
    (
        c.table(_TABLE)
        .delete()
        .eq("id", prototype_id)
        .eq("workspace_id", workspace_id)
        .execute()
    )
    logger.info("prototype_deleted prototype_id=%s", prototype_id)


def mark_awaiting_clarification(*, prototype_id: int, workspace_id: str) -> None:
    """F12 (P4-08): return a paused prototype to the clean PAUSED state after a
    clarifying_question terminal-PAUSE in the route bg layer.

    A pause is NOT a failure and NOT a completion. The runner already persisted
    the question on `pending_question` (P3-08); this helper only corrects the
    status the bg layer would otherwise have set to 'failed'. Sets status='ready'
    and clears `error`. Does NOT touch `bundle_url` / `completed_at` /
    `current_checkpoint_id` / `share_token` — on the ITERATE path the prior
    'ready' bundle and checkpoint are preserved untouched, so the existing share
    URL keeps serving the last good prototype while the question is open. Does NOT
    write `pending_question` — the runner already did (P3-08). Workspace-filtered
    (Rule #22)."""
    c = require_client()
    (
        c.table(_TABLE)
        .update({"status": "ready", "error": None})
        .eq("id", prototype_id)
        .eq("workspace_id", workspace_id)  # explicit workspace filter (Rule #22)
        .execute()
    )
    logger.info("prototype_awaiting_clarification prototype_id=%s", prototype_id)
