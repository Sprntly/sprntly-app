"""Design Agent HTTP routes (P1-07).

Wires P1-04 (`generate_prototype` agent loop), P1-05 (scaffold prompts), and
P1-06 (`prototypes` DB helpers) into the FastAPI surface:

    POST /v1/design-agent/generate  {prd_id, target_platform, instructions, figma_file_key}
    GET  /v1/design-agent/{id}

Per BUILD-PHASES.md §Phase 1 AC #1: POST /generate returns within 200ms — it
inserts a `generating` row, fires the agent loop in a background task, and
returns the prototype_id immediately (no Anthropic call in the request path).
Per BUILD.md §6 isolation: `APIRouter(prefix="/v1/design-agent")`.
Per skill-config §Architecture Rule #27: feature-flag-gated — both endpoints
return 404 when `DESIGN_AGENT_ENABLED` is unset / "0" / "false", so the feature
is invisible until Apurva flips it.
Per skill-config §Architecture Rules #21-#22: workspace-isolated — `workspace_id`
is read from the session `aud` claim at insert time and every user-driven query
filters by it.

CALL-STYLE NOTE (P1-06 carry-forward): the `db.prototypes` helpers are
*synchronous* (supabase-py is sync; this mirrors `db/prds.py` + `routes/prd.py`
exactly). They are called WITHOUT `await`, directly from the async handler —
the same pattern `routes/prd.py` uses for `start_prd` / `find_existing_prd`.
The only awaited call in this module is `generate_prototype` (P1-04, genuinely
async), which runs off the request path inside the background task.

SCOPE (what this ticket does NOT do, per the ticket's scope boundaries):
- Bundle staging to storage + `complete_prototype(bundle_url=...)` — wired by
  P1-08 (`_run_generation_bg` → `_stage_complete_run`: vite_build → checkpoint →
  stage_bundle → complete_prototype on the success path).
- CSRF / Origin check — P5-06 (matches Sprntly's existing routes, which have no
  CSRF defense; design-agent inherits the gap until P5 hardens it).
- Per-session rate limiter — P5-04.
- `POST /complete | /resume | /share | /export | /iterate | /manual-edit` —
  appended to this file in later phases (P2/P3/P4).
"""
from __future__ import annotations

import asyncio
import logging
import os
from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from app.auth import require_app_session  # app-audience auth dep (BUILD.md §6)
from app.db.prds import get_prd
from app.db.prototypes import (
    complete_prototype,
    create_checkpoint,
    fail_prototype,
    find_existing_prototype,
    get_prototype,
    infer_scenario_from_inputs,
    start_prototype,
)
from app.design_agent.prompts import (
    DESIGN_AGENT_SCAFFOLD_SYSTEM,
    DESIGN_AGENT_TEMPLATE_VERSION,
    render_scaffold_user,
)
from app.design_agent.runner import generate_prototype
from app.design_agent.storage import ViteBuildError, stage_bundle, vite_build

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/v1/design-agent", tags=["design-agent"])

_VARIANT = "v1"

# Strong refs to in-flight background generation tasks. asyncio only holds a
# weak reference to a bare `create_task` result, so without this the task can be
# garbage-collected mid-run. The done-callback discards each task on completion.
# The fuller in-flight discipline (cancellation, draining on shutdown) lands in
# P5-05; this is the P1 minimum to keep the task alive (AC #6).
_inflight_tasks: set[asyncio.Task] = set()


def _feature_enabled() -> bool:
    """Read DESIGN_AGENT_ENABLED at REQUEST TIME (never import time).

    Per skill-config Rule #27: default-off; never default-1 in any commit.
    Request-time read means flipping the env var takes effect without a code
    deploy or process restart, and keeps the gate honest under module reload in
    tests. The frontend uses a *separate* var, `NEXT_PUBLIC_DESIGN_AGENT_ENABLED`
    (the `NEXT_PUBLIC_` prefix is mandatory for Next.js client-bundle exposure);
    the two gate independently — this one is the security boundary.
    """
    val = (os.environ.get("DESIGN_AGENT_ENABLED") or "").strip().lower()
    return val in {"1", "true", "yes"}


def _require_feature_enabled() -> None:
    if not _feature_enabled():
        # 404 (not 401, not a JSON error) so the feature is invisible when off.
        raise HTTPException(status_code=404, detail="Not found")


# ─── Schemas ────────────────────────────────────────────────────────────────


class GenerateRequest(BaseModel):
    prd_id: int = Field(..., gt=0)
    target_platform: str = Field("both")  # "desktop" | "mobile" | "both"
    instructions: str = Field("")
    figma_file_key: str | None = None     # explicit; auto-detection via the
    #                                       connector lookup lands in a later phase.

    def normalised_platform(self) -> str:
        return self.target_platform.strip().lower() or "both"


class GenerateResponse(BaseModel):
    prototype_id: int
    status: str  # "generating" | "ready"


# ─── Routes ───────────────────────────────────────────────────────────────


@router.post("/generate", response_model=GenerateResponse)
async def generate(
    body: GenerateRequest,
    session: dict = Depends(require_app_session),
) -> GenerateResponse:
    """Kick off prototype generation in the background; return the id in <200ms.

    Short-circuits when a ready/generating row already exists for this PRD under
    this workspace + template_version (mirrors routes/prd.py's find_existing
    dedupe) so a double-click on Generate does not fan out duplicate runs.
    """
    _require_feature_enabled()
    workspace_id = (session.get("aud") or "").strip()
    if not workspace_id:
        raise HTTPException(status_code=401, detail="No workspace claim")

    # Sync DB helpers, called directly (no await) — see CALL-STYLE NOTE.
    existing = find_existing_prototype(
        prd_id=body.prd_id,
        workspace_id=workspace_id,
        template_version=DESIGN_AGENT_TEMPLATE_VERSION,
        variant=_VARIANT,
    )
    if existing:
        return GenerateResponse(prototype_id=existing["id"], status=existing["status"])

    # Insert the generating row. Scenario inputs (figma_file_key, etc.) are
    # stored as snapshots; the A/B/C/0 label is DERIVED at read time
    # (infer_scenario), never persisted — see db/prototypes.py.
    prototype_id = start_prototype(
        prd_id=body.prd_id,
        workspace_id=workspace_id,
        template_version=DESIGN_AGENT_TEMPLATE_VERSION,
        variant=_VARIANT,
        instructions=body.instructions,
        target_platform=body.normalised_platform(),
        figma_file_key=body.figma_file_key,
        website_url=None,             # populated in P5-02 (Scenario B)
        github_installation_id=None,  # populated in P4-05 (Scenario C)
    )

    task = asyncio.create_task(
        _run_generation_bg(
            prototype_id=prototype_id,
            workspace_id=workspace_id,
            prd_id=body.prd_id,
            target_platform=body.normalised_platform(),
            instructions=body.instructions,
            figma_file_key=body.figma_file_key,
        )
    )
    _inflight_tasks.add(task)
    task.add_done_callback(_inflight_tasks.discard)

    return GenerateResponse(prototype_id=prototype_id, status="generating")


@router.get("/{prototype_id}")
def get_one(
    prototype_id: int,
    session: dict = Depends(require_app_session),
) -> dict[str, Any]:
    """Return the full prototype row for the frontend poller (P1-09).

    Sync handler (mirrors routes/prd.py's GET) — FastAPI runs it in the
    threadpool, so the blocking supabase read does not stall the event loop.
    Workspace-filtered: a row in a different workspace returns 404, not 403,
    so cross-tenant existence is not even disclosed (Rule #22).
    """
    _require_feature_enabled()
    workspace_id = (session.get("aud") or "").strip()
    if not workspace_id:
        raise HTTPException(status_code=401, detail="No workspace claim")
    row = get_prototype(prototype_id=prototype_id, workspace_id=workspace_id)
    if not row:
        raise HTTPException(status_code=404, detail="Prototype not found")
    return row


# ─── Background generation ────────────────────────────────────────────────


async def _run_generation_bg(
    *,
    prototype_id: int,
    workspace_id: str,
    prd_id: int,
    target_platform: str,
    instructions: str,
    figma_file_key: str | None,
) -> None:
    """Fired from POST /generate; assembles the first call + runs the agent loop.

    On any exception, sets prototype.status='failed' with the error message in
    the existing Sprntly format (`f"{type(exc).__name__}: {exc}"`, prd_runner.py
    style). The structured cost-summary log line is emitted by
    `generate_prototype` itself (P1-04). On a complete run with emitted files,
    `_stage_complete_run` (P1-08) builds + stages the bundle and marks the row
    ready; every other terminal state fails the row.
    """
    try:
        prd_md = _load_prd_body(prd_id)
        figma_block = _figma_context_block(figma_file_key)

        # Exactly one system block; cache_control at the END of the stable
        # prefix (AD2 / TICKET_STANDARD §2 LLM-calling AC). The agent loop reads
        # the LAST block's cache_control to cache the stable system prefix.
        system_blocks = [{
            "type": "text",
            "text": DESIGN_AGENT_SCAFFOLD_SYSTEM,
            "cache_control": {"type": "ephemeral", "ttl": "1h"},
        }]
        user_text = render_scaffold_user(
            prd_md=prd_md,
            target_platform=target_platform,
            instructions=instructions,
            figma_frames=figma_block,
        )
        user_message = {
            "role": "user",
            "content": [{"type": "text", "text": user_text}],
        }

        # Derive the scenario label once at the call boundary so the runner can
        # surface it in the cost-summary log without re-deriving (single
        # inference site, db/prototypes.py). Codebase-target detection
        # (Scenario C) lands in P4-05 — for P1, prd_references_codebase is
        # always False, so C never fires here regardless of inputs.
        scenario_set = infer_scenario_from_inputs(
            figma_file_key=figma_file_key,
            website_url=None,               # P5-02 populates
            github_installation_id=None,    # P4-05 populates
            prd_references_codebase=False,  # P4-05 implements the detector
        )
        scenario_label = ",".join(sorted(scenario_set))  # "A" | "A,C" | "0" ...

        result, virtual_fs = await generate_prototype(
            prototype_id=prototype_id,
            workspace_id=workspace_id,
            system_blocks=system_blocks,
            user_message=user_message,
            figma_file_key=figma_file_key,
            scenario=scenario_label,
        )
        # Success path (P1-08): a complete run that emitted files gets built +
        # staged + marked ready. A complete run with no files, or any non-complete
        # terminal state, fails the row.
        if result.status == "complete" and virtual_fs:
            await _stage_complete_run(
                prototype_id=prototype_id,
                workspace_id=workspace_id,
                virtual_fs=virtual_fs,
            )
        elif result.status == "complete" and not virtual_fs:
            fail_prototype(
                prototype_id=prototype_id,
                workspace_id=workspace_id,
                error="agent_loop completed but emitted no files",
            )
        else:
            fail_prototype(
                prototype_id=prototype_id,
                workspace_id=workspace_id,
                error=f"agent_loop ended with status={result.status} iters={result.iters}",
            )
    except Exception as exc:  # noqa: BLE001 — bg task must never leak; row is failed.
        # error_class only in the structured log (Rule #24 — no PII / no PRD /
        # no instructions / no figma contents); the full message is stored in
        # the row's `error` column (truncated to 500 chars by fail_prototype).
        logger.warning(
            "design_agent.generation_failed prototype_id=%s error_class=%s",
            prototype_id, type(exc).__name__,
        )
        fail_prototype(
            prototype_id=prototype_id,
            workspace_id=workspace_id,
            error=f"{type(exc).__name__}: {exc}",
        )


async def _stage_complete_run(
    *,
    prototype_id: int,
    workspace_id: str,
    virtual_fs: dict[str, str],
) -> None:
    """Post-run hook (P1-08): vite_build → checkpoint → stage_bundle → complete.

    Four steps, each gating the next:

    1. **Vite build** runs the P0-02 anchor-id plugin over the agent's raw TSX
       (per AD4 — load-bearing for F8/F13/F5). A build failure (bad JSX, missing
       runtime, timeout) marks the row failed and creates NO checkpoint.
    2. **Checkpoint** row is inserted first so its id seeds the bundle prefix.
    3. **Stage** the BUILT dist/ (never the raw virtual_fs) to Supabase Storage
       (primary) / filesystem (fallback). A staging failure leaves the checkpoint
       row present but `bundle_url` NULL.
    4. **Complete** marks the prototype ready and threads `current_checkpoint_id`.

    The DB helpers (`create_checkpoint` / `complete_prototype` / `fail_prototype`)
    are synchronous and called WITHOUT await (supabase-py is sync; mirrors
    db/prds.py + routes/prd.py). `vite_build` / `stage_bundle` are async and
    awaited. vite_build / stage_bundle failures are handled here (with their own
    log lines) rather than propagated, so the error strings match the ticket ACs
    exactly; a DB-helper failure propagates to the caller's outer except.
    """
    # Step 1 — Vite build (anchor-id plugin runs here).
    try:
        dist_files = await vite_build(virtual_fs)
    except (ViteBuildError, FileNotFoundError) as exc:
        # error_class only in the log (Rule #24 — no stderr/secrets); the full
        # message (incl. stderr tail) goes to the row's error column.
        logger.warning(
            "vite_build_failed prototype_id=%s error_class=%s",
            prototype_id, type(exc).__name__,
        )
        fail_prototype(
            prototype_id=prototype_id,
            workspace_id=workspace_id,
            error=f"{type(exc).__name__}: {exc}",
        )
        return
    logger.info(
        "vite_build_succeeded prototype_id=%s checkpoint_id=N/A dist_file_count=%s",
        prototype_id, len(dist_files),
    )

    # Step 2 — checkpoint row (id seeds the bundle prefix). prd/figma hashes +
    # comment_state land in P3; for P1 the checkpoint records the bundle only.
    checkpoint_id = create_checkpoint(
        prototype_id=prototype_id,
        workspace_id=workspace_id,
        bundle_url=None,            # populated on the prototype row after staging
        prd_revision_hash=None,     # P3-12 wires PRD-hash + figma-hash
        figma_frame_hash=None,
        prompt_history=[],
        comment_state=[],
    )

    # Step 3 — stage the BUILT dist/ (not raw virtual_fs).
    try:
        bundle_url = await stage_bundle(
            prototype_id=prototype_id,
            checkpoint_id=checkpoint_id,
            files=dist_files,
        )
    except Exception as exc:  # noqa: BLE001 — surface staging failure on the row.
        fail_prototype(
            prototype_id=prototype_id,
            workspace_id=workspace_id,
            error=f"{type(exc).__name__}: {exc}",
        )
        return

    # Step 4 — mark ready + thread current_checkpoint_id back to the prototype.
    complete_prototype(
        prototype_id=prototype_id,
        workspace_id=workspace_id,
        bundle_url=bundle_url,
        current_checkpoint_id=checkpoint_id,
    )


def _load_prd_body(prd_id: int) -> str:
    """Fetch the PRD's `payload_md` for the agent's user message.

    `get_prd` is the existing helper (db/prds.py) and is NOT workspace-scoped —
    PRDs predate the workspace_id primitive, and `routes/prd.py` reads them the
    same way under its own auth dependency. Per AC #10 this is the documented
    fallback: the route's `require_app_session` gate is the access boundary; a
    workspace filter is added if/when `get_prd` grows a `workspace_id` param.
    Raises 404 (surfaced into the row's error via the caller's except) when the
    PRD does not exist.
    """
    prd = get_prd(prd_id)
    if not prd:
        raise HTTPException(status_code=404, detail="PRD not found")
    return prd.get("payload_md") or ""


def _figma_context_block(figma_file_key: str | None) -> str:
    """Build the Figma context block for the scaffold user message (Scenario A).

    We do NOT pre-fetch frames here: the agent pulls frame structure itself via
    the `fetch_figma` tool (the runner injects the decrypted Figma token onto
    the ToolContext before any tool dispatch — see design_agent/runner.py), and
    the scaffold system prompt instructs it to call `fetch_figma` once to see
    the top-level frames. So this block just tells the agent a Figma file is
    available; a blocking pre-fetch in the request-spawned task would add a
    failure mode for marginal benefit.
    """
    if not figma_file_key:
        return "(no Figma source detected)"
    return (
        f"A Figma file is connected to this prototype (file key: {figma_file_key}). "
        "Call the fetch_figma tool (no frame_ids) to list its top-level frames, "
        "then fetch the specific frames you need."
    )
