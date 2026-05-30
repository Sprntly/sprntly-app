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
import hashlib
import logging
import os
from typing import Any, Literal

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import Response
from pydantic import BaseModel, Field

from app.auth import require_app_session  # app-audience auth dep (BUILD.md §6)
from app.db.prds import get_prd
from app.db.prototype_exports import find_prototype_export
from app.db.prototypes import (
    complete_prototype,
    create_checkpoint,
    fail_prototype,
    find_existing_prototype,
    find_prototype_by_share_token,
    flag_stale_handoff,
    get_prototype,
    infer_scenario_from_inputs,
    mark_complete,
    passcode_rate_limit_check,
    passcode_rate_limit_clear,
    passcode_rate_limit_register_failure,
    record_export_at_complete,
    resume_iteration,
    set_share_config,
    start_prototype,
    verify_share_passcode,
)
from app.design_agent.prompts import (
    DESIGN_AGENT_SCAFFOLD_SYSTEM,
    DESIGN_AGENT_TEMPLATE_VERSION,
    render_scaffold_user,
)
from app.design_agent.runner import generate_prototype, reconcile_comments_on_checkpoint
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
            # P2-02: include the structured error_message / error_class from
            # RunResult so the underlying failure (e.g. an Anthropic
            # BadRequestError) is preserved for triage rather than dropped on
            # the floor. The 500-char cap is applied downstream in
            # fail_prototype, so no caller-side truncation is needed here.
            error_parts = [
                f"agent_loop ended with status={result.status} iters={result.iters}"
            ]
            error_message = getattr(result, "error_message", None)
            error_class = getattr(result, "error_class", None)
            if error_message:
                error_parts.append(f"error_message={error_message}")
            if error_class:
                error_parts.append(f"error_class={error_class}")
            fail_prototype(
                prototype_id=prototype_id,
                workspace_id=workspace_id,
                error=" | ".join(error_parts),
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

    # Step 3.5 — Stage the RAW virtual_fs alongside dist/ under _source/ so the
    # export serialiser (P2-08) can read raw TSX, not minified bundles.
    # Best-effort: a source-stage failure logs and proceeds — the prototype is
    # still ready (the load-bearing artefact is the dist/ bundle). The serialiser
    # gracefully falls back to its "no source staged" message if this step failed.
    try:
        await stage_bundle(
            prototype_id=prototype_id,
            checkpoint_id=checkpoint_id,
            files=virtual_fs,
            sub_prefix="_source",
        )
    except Exception as exc:  # noqa: BLE001 — source-stage is best-effort.
        logger.warning(
            "source_stage_failed prototype_id=%s checkpoint_id=%s error_class=%s",
            prototype_id, checkpoint_id, type(exc).__name__,
        )

    # Step 3.6 — AD12 orphan/re-attach. Orphan every OPEN comment whose anchor
    # vanished from THIS build's bundle. Best-effort: the bundle is already
    # staged, so a reconcile failure must NOT fail the build — it logs and the
    # prototype still completes ready (orphaning is housekeeping, not a gate).
    try:
        reconcile_comments_on_checkpoint(
            prototype_id=prototype_id,
            workspace_id=workspace_id,
            dist_files=dist_files,
        )
    except Exception as exc:  # noqa: BLE001 — reconcile is best-effort housekeeping.
        logger.warning(
            "comments_reconcile_failed prototype_id=%s error_class=%s",
            prototype_id, type(exc).__name__,
        )

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


# ─── Public share viewer (P2-05) ──────────────────────────────────────────
#
# These two routes back the unauthenticated `/p/<token>` viewer (web/app/p/[token]).
# They are NO-AUTH BY DESIGN: the share_token IS the access primitive (F6), so
# they carry NO `require_app_session` dependency and NO workspace filter —
# `find_prototype_by_share_token` is the one legitimate cross-workspace read
# (see db/prototypes.py). Both are feature-flag-gated via the shared
# `_require_feature_enabled()` so a brute-force scan returns 404, matching the
# auth'd routes' invisibility posture (Rule #15 / F6: 404-not-401).
#
# The response is MINIMUM-DISCLOSURE: exactly four fields. No prototype_id,
# prd_id, workspace_id, instructions, figma_file_key, created_at, or error ever
# leaves this surface — that reduces the cross-tenant fingerprinting surface to
# what the viewer strictly needs to render. response_model enforces the exact
# key set even if the row carries more columns.


def _share_token_hash(token: str) -> str:
    """sha256 prefix of a share token, for log correlation only (Rule #24).

    The full token must NEVER reach log aggregation: it is the access primitive,
    so logging it verbatim is equivalent to logging the share URL. An 8-char
    sha256 prefix correlates a resolve/deny pair for one token without being
    reversible to the token (or to anything PII).
    """
    return hashlib.sha256(token.encode("utf-8")).hexdigest()[:8]


class PublicPrototypeView(BaseModel):
    share_mode: Literal["public", "passcode"]  # "private" is never returned
    requires_passcode: bool                    # true iff share_mode == "passcode"
    bundle_url: str | None                     # null until a passcode is verified
    is_complete: bool


class PasscodeAttempt(BaseModel):
    passcode: str = Field(..., min_length=1, max_length=128)


@router.get("/by-token/{token}", response_model=PublicPrototypeView)
def get_by_token(token: str) -> PublicPrototypeView:
    """Resolve a share_token to its viewable prototype. NO auth — the token IS
    the access. Returns 404 (never 401/403) on a bad token, a `private` row, or a
    row whose `status` is not `ready` (a generating/failed prototype is not
    viewable, and we do not disclose that it exists). `bundle_url` is withheld
    (null) for passcode mode until POST /passcode succeeds.
    """
    _require_feature_enabled()
    th = _share_token_hash(token)
    row = find_prototype_by_share_token(token)
    if not row or row.get("share_mode") == "private" or row.get("status") != "ready":
        # `private` is its own reason; a missing row OR a not-ready row both map
        # to `not_found` so we never disclose that a hidden prototype exists.
        reason = "private" if (row and row.get("share_mode") == "private") else "not_found"
        logger.info("prototype_public_view_denied token_hash=%s reason=%s", th, reason)
        raise HTTPException(status_code=404, detail="Not found")
    mode = row["share_mode"]
    logger.info("prototype_public_view_resolved token_hash=%s share_mode=%s", th, mode)
    return PublicPrototypeView(
        share_mode=mode,
        requires_passcode=(mode == "passcode"),
        # bundle_url is released for public mode immediately; for passcode mode it
        # stays null here and is only returned by the verify route on success.
        bundle_url=row.get("bundle_url") if mode == "public" else None,
        is_complete=bool(row.get("is_complete")),
    )


@router.post("/by-token/{token}/passcode", response_model=PublicPrototypeView)
def verify_passcode(
    token: str, body: PasscodeAttempt, request: Request
) -> PublicPrototypeView:
    """Verify a passcode against a passcode-mode share; on success return the
    bundle_url. Rate-limited 5/min/token (P2-06 primitive). The rate-limit check
    runs FIRST so counter exhaustion is observable as 429 BEFORE any hash
    comparison; under the limit, a wrong passcode returns 401 `invalid_passcode`.
    404 (not 401) for a bad/non-passcode/not-ready token preserves invisibility.
    """
    _require_feature_enabled()
    th = _share_token_hash(token)
    # Rate-limit FIRST (AC6): a token over the limit gets 429 before we touch the
    # row or the hash, so a brute-forcer cannot distinguish rate-limited from
    # wrong-passcode by timing the hash compare.
    client_ip = request.client.host if request.client else "0.0.0.0"
    if not passcode_rate_limit_check(token=token, ip=client_ip):
        logger.info("prototype_public_view_denied token_hash=%s reason=rate_limited", th)
        raise HTTPException(status_code=429, detail="Too many attempts")
    row = find_prototype_by_share_token(token)
    if not row or row.get("share_mode") != "passcode" or row.get("status") != "ready":
        logger.info("prototype_public_view_denied token_hash=%s reason=not_found", th)
        raise HTTPException(status_code=404, detail="Not found")
    if not verify_share_passcode(body.passcode, row.get("share_passcode_hash")):
        passcode_rate_limit_register_failure(token=token)
        logger.info("prototype_public_view_denied token_hash=%s reason=passcode_required", th)
        raise HTTPException(status_code=401, detail="invalid_passcode")
    # Success: clear the failure history so a later legitimate visitor is not
    # throttled by this token's earlier wrong attempts.
    passcode_rate_limit_clear(token=token)
    logger.info("prototype_public_view_resolved token_hash=%s share_mode=passcode", th)
    return PublicPrototypeView(
        share_mode="passcode",
        requires_passcode=True,
        bundle_url=row.get("bundle_url"),
        is_complete=bool(row.get("is_complete")),
    )


# ─── Lifecycle: Mark Complete / Resume Iteration / Set Share (P2-07) ──────────
#
# F14 (complete) locks a prototype; F15 (resume) unlocks it AND flags any open
# downstream handoff (the most-recent export row) as stale per spec §8; F6
# (share) sets share_mode/token/passcode. All three reuse `require_app_session`
# (app-audience auth) and `_require_feature_enabled` so they are invisible (404)
# while the flag is off and 401 without an app session — identical gates to
# /generate above. The handlers are sync (mirrors get_one): FastAPI runs them in
# the threadpool, so the blocking supabase calls don't stall the event loop.


class CompleteRequest(BaseModel):
    """Empty body — POST /complete takes no payload. The current checkpoint
    (from `prototypes.current_checkpoint_id`) is what's locked."""
    pass


class CompleteResponse(BaseModel):
    prototype_id: int
    is_complete: bool
    complete_checkpoint_id: int | None


@router.post("/{prototype_id}/complete", response_model=CompleteResponse)
async def post_complete(
    prototype_id: int,
    session: dict = Depends(require_app_session),
) -> CompleteResponse:
    """F14: lock the prototype. Sets is_complete=true and promotes
    current_checkpoint_id → complete_checkpoint_id. Idempotent: a second
    /complete on an already-complete prototype is a no-op (200; no row change).
    Returns 404 if the prototype is not in the caller's workspace.
    Returns 409 if `status != 'ready'` (cannot mark a generating/failed/invalidated
    prototype complete).

    `async def` because the export-write hook (`record_export_at_complete`,
    filled in by P2-09) is now async — it awaits the markdown serialiser. The
    sync DB helpers (`get_prototype`, `mark_complete`) are still called WITHOUT
    `await` per the CALL-STYLE NOTE; only the export hook is awaited.
    """
    _require_feature_enabled()
    workspace_id = (session.get("aud") or "").strip()
    if not workspace_id:
        raise HTTPException(status_code=401, detail="No workspace claim")
    row = get_prototype(prototype_id=prototype_id, workspace_id=workspace_id)
    if not row:
        raise HTTPException(status_code=404, detail="Prototype not found")
    if row["status"] != "ready":
        raise HTTPException(status_code=409, detail=f"Cannot complete: status={row['status']}")
    updated = mark_complete(prototype_id=prototype_id, workspace_id=workspace_id)
    # P2-09 fills in the export-write hook (async): it generates the markdown
    # brief and persists it to prototype_exports. Awaited inline so the export
    # row is committed before the /complete response returns (no fire-and-forget
    # coroutine — an un-awaited call would silently never insert the row).
    await record_export_at_complete(prototype_id=prototype_id, workspace_id=workspace_id)
    return CompleteResponse(
        prototype_id=updated["id"],
        is_complete=updated["is_complete"],
        complete_checkpoint_id=updated["complete_checkpoint_id"],
    )


class ResumeResponse(BaseModel):
    prototype_id: int
    is_complete: bool
    handoffs_flagged_stale: int   # count for log/UX; the export row IS the handoff


@router.post("/{prototype_id}/resume", response_model=ResumeResponse)
def post_resume(
    prototype_id: int,
    session: dict = Depends(require_app_session),
) -> ResumeResponse:
    """F15: unlock the prototype + flag any open handoff record as stale.

    Sets is_complete=false. Calls flag_stale_handoff(prototype_id) which marks the
    most-recent `prototype_exports` row stale (that row IS the handoff record per
    the 2026-05-29 decision). Returns 0 when no export exists yet, so resume on a
    never-completed prototype is a clean no-op on the handoff surface.
    Idempotent: resume-on-WIP is a 200 no-op.
    """
    _require_feature_enabled()
    workspace_id = (session.get("aud") or "").strip()
    if not workspace_id:
        raise HTTPException(status_code=401, detail="No workspace claim")
    row = get_prototype(prototype_id=prototype_id, workspace_id=workspace_id)
    if not row:
        raise HTTPException(status_code=404, detail="Prototype not found")
    updated = resume_iteration(prototype_id=prototype_id, workspace_id=workspace_id)
    stale_count = flag_stale_handoff(prototype_id=prototype_id, workspace_id=workspace_id)
    return ResumeResponse(
        prototype_id=updated["id"],
        is_complete=updated["is_complete"],
        handoffs_flagged_stale=stale_count,
    )


class ShareRequest(BaseModel):
    mode: Literal["private", "public", "passcode"]
    passcode: str | None = Field(default=None, max_length=128)


class ShareResponse(BaseModel):
    prototype_id: int
    share_mode: str
    share_token: str | None     # null for private, populated for public/passcode


@router.post("/{prototype_id}/share", response_model=ShareResponse)
def post_share(
    prototype_id: int,
    body: ShareRequest,
    session: dict = Depends(require_app_session),
) -> ShareResponse:
    """F6: set / update share configuration. Wraps set_share_config (P2-06).

    On passcode mode without a passcode, returns 400. On unknown mode → 422
    (caught by pydantic). On row-not-found in this workspace → 404.
    """
    _require_feature_enabled()
    workspace_id = (session.get("aud") or "").strip()
    if not workspace_id:
        raise HTTPException(status_code=401, detail="No workspace claim")
    if body.mode == "passcode" and not body.passcode:
        raise HTTPException(status_code=400, detail="passcode-mode requires a passcode")
    row = get_prototype(prototype_id=prototype_id, workspace_id=workspace_id)
    if not row:
        raise HTTPException(status_code=404, detail="Prototype not found")
    try:
        updated = set_share_config(
            prototype_id=prototype_id,
            workspace_id=workspace_id,
            share_mode=body.mode,
            passcode=body.passcode,
        )
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
    return ShareResponse(
        prototype_id=updated["id"],
        share_mode=updated["share_mode"],
        share_token=updated.get("share_token"),
    )


# ─── Export read (P2-09) ──────────────────────────────────────────────────────
#
# F16/F17: return the markdown brief of the locked checkpoint. The /complete
# handler snapshots the markdown into prototype_exports (record_export_at_complete);
# this route reads that snapshot, falling back to a fresh serialiser render if the
# snapshot row is missing (defensive against a partial-failure during /complete).
# Same gates as the authed routes above: feature-flag (404 when off) +
# require_app_session (401 without a session) + workspace filter (404 cross-tenant).


@router.get("/{prototype_id}/export")
async def get_export(
    prototype_id: int,
    session: dict = Depends(require_app_session),
) -> Response:
    """F16/F17: return the markdown export of the locked checkpoint.

    Returns 409 when the prototype is not complete (is_complete=false) per F17.
    Returns 404 when not in the caller's workspace.
    Returns 200 with Content-Type: text/markdown; charset=utf-8 and
    Content-Disposition: attachment; filename="<slug>-design-brief.md".
    The frontend uses this for both Download .md and Copy to clipboard (it
    reads the text body and copies via navigator.clipboard.writeText).
    """
    _require_feature_enabled()
    workspace_id = (session.get("aud") or "").strip()
    if not workspace_id:
        raise HTTPException(status_code=401, detail="No workspace claim")
    proto = get_prototype(prototype_id=prototype_id, workspace_id=workspace_id)
    if not proto:
        raise HTTPException(status_code=404, detail="Prototype not found")
    if not proto.get("is_complete"):
        # F17: WIP prototypes viewable but not exportable.
        raise HTTPException(status_code=409, detail="Mark prototype complete first")
    export_row = find_prototype_export(
        prototype_id=prototype_id, workspace_id=workspace_id,
    )
    if not export_row:
        # Fallback: regenerate on the fly if the snapshot row is missing
        # (shouldn't happen under normal flow — /complete writes it — but
        # defensive against partial-failure during the /complete handler).
        from app.design_agent.export import render_export_markdown
        markdown = await render_export_markdown(
            prototype_id, proto["complete_checkpoint_id"],
            workspace_id=workspace_id,
        )
    else:
        markdown = export_row["markdown_content"]
    filename = _export_filename(proto)
    return Response(
        content=markdown,
        media_type="text/markdown; charset=utf-8",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


def _export_filename(proto: dict[str, Any]) -> str:
    """Build a safe download filename. Strips non-ascii + replaces spaces.
    Falls back to prototype-<id> when no slug source available."""
    import re
    base = f"prototype-{proto['id']}"
    return re.sub(r"[^A-Za-z0-9_-]", "-", base) + "-design-brief.md"


# ─── Anchored comments (P3-02) ─────────────────────────────────────────────
#
# F8: anyone with the share URL can comment; spec §4 Stage 2 splits write access
# by surface, not by capability gate — internal users act through the authed app
# routes, external viewers through the public `/p/<token>` variant. This block
# mounts the HTTP surface over P3-01's `db.prototype_comments` helpers:
#
#   POST  /{prototype_id}/comments              (authed — create)
#   GET   /{prototype_id}/comments              (authed — list, all statuses)
#   PATCH /{prototype_id}/comments/{cid}/resolve (authed — resolve)
#   POST  /by-token/{token}/comments            (public, NO auth — create)
#   GET   /by-token/{token}/comments            (public, NO auth — list)
#
# The internal routes reuse the authed-route gates verbatim (feature flag +
# require_app_session + workspace filter via get_prototype). The public routes
# mirror get_by_token's posture exactly: the token IS the access primitive (F6),
# so NO auth dependency and NO session workspace claim — workspace_id is taken
# from the RESOLVED prototype row. Per spec §4 Stage 2 ("only internal users with
# credentials can act"), external viewers create + read only; there is NO public
# resolve route. Public-write rate limiting is OUT of scope here — it lands in
# P5-07 (per TICKET_LIST shared-resources).
from app.db.prototype_comments import insert_comment, list_comments, resolve_comment


class CommentCreate(BaseModel):
    anchor_id: str = Field(..., min_length=1, max_length=64)
    body: str = Field(..., min_length=1, max_length=4000)


class CommentOut(BaseModel):
    id: int
    anchor_id: str
    body: str
    author: str
    status: str           # 'open' | 'resolved' | 'orphaned'
    created_at: str
    resolved_at: str | None = None


def _comment_to_out(row: dict[str, Any]) -> dict[str, Any]:
    """Project a DB row to the CommentOut shape (ISO-string timestamps).

    Timestamps are stringified defensively: Postgres returns timestamptz objects
    via supabase, the SQLite fake returns TEXT — `str()` normalises both to the
    ISO string CommentOut expects without leaking driver-specific types."""
    return {
        "id": row["id"],
        "anchor_id": row["anchor_id"],
        "body": row["body"],
        "author": row["author"],
        "status": row["status"],
        "created_at": str(row["created_at"]),
        "resolved_at": str(row["resolved_at"]) if row.get("resolved_at") else None,
    }


# ─── Internal (authed) comment routes ─────────────────────────────────────


@router.post("/{prototype_id}/comments", response_model=CommentOut)
def post_comment(
    prototype_id: int,
    body: CommentCreate,
    session: dict = Depends(require_app_session),
) -> CommentOut:
    """Create a comment as an internal user. Workspace-filtered: 404 if the
    prototype is not in the caller's workspace (cross-tenant existence is not
    disclosed — Rule #22). Attributed to the internal author label."""
    _require_feature_enabled()
    workspace_id = (session.get("aud") or "").strip()
    if not workspace_id:
        raise HTTPException(status_code=401, detail="No workspace claim")
    proto = get_prototype(prototype_id=prototype_id, workspace_id=workspace_id)
    if not proto:
        raise HTTPException(status_code=404, detail="Prototype not found")
    row = insert_comment(
        prototype_id=prototype_id,
        workspace_id=workspace_id,
        anchor_id=body.anchor_id,
        body=body.body,
        author="demo",
    )
    return CommentOut(**_comment_to_out(row))


@router.get("/{prototype_id}/comments", response_model=list[CommentOut])
def get_comments(
    prototype_id: int,
    session: dict = Depends(require_app_session),
) -> list[CommentOut]:
    """List every comment for a prototype (all statuses, created_at-ascending).
    Workspace-filtered: 404 if the prototype is not in the caller's workspace."""
    _require_feature_enabled()
    workspace_id = (session.get("aud") or "").strip()
    if not workspace_id:
        raise HTTPException(status_code=401, detail="No workspace claim")
    proto = get_prototype(prototype_id=prototype_id, workspace_id=workspace_id)
    if not proto:
        raise HTTPException(status_code=404, detail="Prototype not found")
    return [
        CommentOut(**_comment_to_out(r))
        for r in list_comments(prototype_id=prototype_id, workspace_id=workspace_id)
    ]


@router.patch("/{prototype_id}/comments/{cid}/resolve", response_model=CommentOut)
def patch_resolve_comment(
    prototype_id: int,
    cid: int,
    session: dict = Depends(require_app_session),
) -> CommentOut:
    """Resolve a comment (internal only — external viewers cannot resolve, per
    spec §4 Stage 2 'only internal users with credentials can act'). Returns 404
    when the comment is not in the caller's workspace OR belongs to a different
    prototype than the one in the path (no cross-prototype resolve)."""
    _require_feature_enabled()
    workspace_id = (session.get("aud") or "").strip()
    if not workspace_id:
        raise HTTPException(status_code=401, detail="No workspace claim")
    row = resolve_comment(comment_id=cid, workspace_id=workspace_id)
    if not row or row["prototype_id"] != prototype_id:
        raise HTTPException(status_code=404, detail="Comment not found")
    return CommentOut(**_comment_to_out(row))


# ─── Public (token-resolved, NO auth) comment routes ──────────────────────
#
# F8: "anyone with the URL can comment." The token IS the access primitive.
# Workspace is taken from the RESOLVED prototype row (not a session claim).
# External viewers may CREATE + READ comments but NOT resolve them. The
# resolution posture matches get_by_token exactly (404 for missing / private /
# not-ready) so brute-force scanning discloses nothing (Rule #15 / F6).


@router.post("/by-token/{token}/comments", response_model=CommentOut)
def post_comment_public(token: str, body: CommentCreate, request: Request) -> CommentOut:
    """Public comment write. Resolves token → prototype; rejects when the
    prototype is private or not ready (404, matching get_by_token's posture).
    The comment is attributed to the anonymous external author label, and the
    workspace_id is taken from the resolved row — never a session claim."""
    _require_feature_enabled()
    proto = find_prototype_by_share_token(token)
    if not proto or proto.get("share_mode") == "private" or proto.get("status") != "ready":
        raise HTTPException(status_code=404, detail="Not found")
    row = insert_comment(
        prototype_id=proto["id"],
        workspace_id=proto["workspace_id"],   # from the resolved row, not a session
        anchor_id=body.anchor_id,
        body=body.body,
        author="external",
    )
    # Token hashed, never raw (Rule #24 — the token is the access primitive); no
    # comment body in the log line (PII). insert_comment emits its own
    # `comment_created` line; this adds the public-surface correlation marker.
    logger.info(
        "comment_created_public token_hash=%s prototype_id=%s comment_id=%s",
        _share_token_hash(token), proto["id"], row["id"],
    )
    return CommentOut(**_comment_to_out(row))


@router.get("/by-token/{token}/comments", response_model=list[CommentOut])
def get_comments_public(token: str) -> list[CommentOut]:
    """Public comment read. All viewers can read existing comments (spec §4).
    Same 404 posture as the public write for missing / private / not-ready."""
    _require_feature_enabled()
    proto = find_prototype_by_share_token(token)
    if not proto or proto.get("share_mode") == "private" or proto.get("status") != "ready":
        raise HTTPException(status_code=404, detail="Not found")
    return [
        CommentOut(**_comment_to_out(r))
        for r in list_comments(prototype_id=proto["id"], workspace_id=proto["workspace_id"])
    ]


# ─── Iterate: re-prompt + Apply-driven edits (P3-05) ───────────────────────────
#
# AD8 mandates a SEPARATE iterate prompt distinct from scaffold; this block lands
# the iterate spine the rest of P3 hangs on. F9 (re-prompt) and F10 (Apply-on-
# comment pre-fills the prompt) both route through `POST /{id}/iterate`:
#
#   POST /v1/design-agent/{prototype_id}/iterate  {prompt, applied_comment_id?, mode?}
#
# Cache discipline (AD2): the iterate system blocks + the current bundle source +
# the open comment threads form the STABLE cacheable prefix; the user's iterate
# prompt is the per-call volatile suffix (render_iterate_user owns the breakpoint).
#
# Staging (B2 — AC6a): a complete iterate run stages via `_stage_iterate_run`, NOT
# `_stage_complete_run`. An iterate is a checkpoint ADVANCE, not a first
# completion, so it MUST NOT call `complete_prototype` (which re-stamps
# completed_at + emits prototype_completed). Advancing `current_checkpoint_id` +
# threading the new bundle_url onto the prototype row is P3-12's
# `advance_current_checkpoint` helper — not merged yet, so this leaves that
# advance as the documented seam (`_advance_current_checkpoint_seam`).
#
# Scope (P3-05): EXECUTE mode only. `mode='plan'` is ACCEPTED but treated as
# execute here — the real plan/execute tool split is P3-07. Concurrency / queueing
# is P3-06 (this fires a single bg task). Tools are append-only: this block is at
# the EOF of the route file; no existing handler is modified.
from app.design_agent.prompts import DESIGN_AGENT_ITERATE_SYSTEM, render_iterate_user
from app.design_agent.runner import drain_iteration_queue, iterate_prototype
from app.design_agent.storage import read_source_files_for_checkpoint
from app.db.prototype_pending_iterations import (
    QueueFullError,
    enqueue_iteration,
    queue_position,
)


class IterateRequest(BaseModel):
    prompt: str = Field(..., min_length=1, max_length=8000)
    applied_comment_id: int | None = None        # F10: set when Apply pre-filled the prompt
    mode: Literal["plan", "execute"] = "execute"  # P3-07 implements 'plan'; P3-05 runs 'execute'


class IterateResponse(BaseModel):
    prototype_id: int
    status: str                                   # 'generating' (kicked off in the bg)
    queue_position: int                           # P3-06: derived slot in the iterate queue


@router.post("/{prototype_id}/iterate", response_model=IterateResponse)
async def post_iterate(
    prototype_id: int,
    body: IterateRequest,
    session: dict = Depends(require_app_session),
) -> IterateResponse:
    """F9/F10: kick off an iterate of an existing prototype; return in <200ms.

    Gates (identical posture to /generate): feature-flag (404 when off) +
    require_app_session (401) + workspace filter (404 cross-tenant). Two iterate-
    specific 409s:
      - `is_complete` (locked, F14): cannot iterate until Resume Iteration (P2-07).
      - `status != 'ready'`: cannot iterate a generating/failed/invalidated row.
    On success enqueues the iterate (AD11 5-slot queue), kicks the serial drain in
    the background, and returns status='generating' + the derived queue_position.
    A full queue returns 429. No Anthropic call in the request path.
    """
    _require_feature_enabled()
    workspace_id = (session.get("aud") or "").strip()
    if not workspace_id:
        raise HTTPException(status_code=401, detail="No workspace claim")
    proto = get_prototype(prototype_id=prototype_id, workspace_id=workspace_id)
    if not proto:
        raise HTTPException(status_code=404, detail="Prototype not found")
    if proto.get("is_complete"):
        raise HTTPException(status_code=409, detail="Prototype is locked; Resume Iteration first")
    if proto.get("status") != "ready":
        raise HTTPException(status_code=409, detail="Prototype not ready to iterate")

    # P3-06 (AD11): enqueue instead of firing a raw bg task. The queue caps at 5
    # active (pending + running) iterations per prototype; a 6th enqueue raises
    # QueueFullError → 429. The drain kick is idempotent (it no-ops if a row is
    # already running), so firing it on every enqueue never spawns a second
    # concurrent drain.
    try:
        row = enqueue_iteration(
            prototype_id=prototype_id,
            workspace_id=workspace_id,
            prompt=body.prompt,
            applied_comment_id=body.applied_comment_id,
            mode=body.mode,
        )
    except QueueFullError:
        raise HTTPException(status_code=429, detail={"error": "queue_full", "max": 5})

    logger.info("prototype_iterate_started prototype_id=%s", prototype_id)
    task = asyncio.create_task(
        drain_iteration_queue(prototype_id=prototype_id, workspace_id=workspace_id)
    )
    _inflight_tasks.add(task)
    task.add_done_callback(_inflight_tasks.discard)
    return IterateResponse(
        prototype_id=prototype_id,
        status="generating",
        queue_position=row["queue_position"],
    )


async def _run_iterate_bg(
    *,
    prototype_id: int,
    workspace_id: str,
    body: IterateRequest,
) -> None:
    """Background iterate run: load the current bundle + open comments, render the
    iterate prompts (cache-disciplined), run the agent loop, then stage the result
    via the iterate path (NOT the first-completion path).

    Source load (S2): `get_prototype` FIRST to obtain `current_checkpoint_id`, then
    `read_source_files_for_checkpoint(prototype_id, current_checkpoint_id)`
    (P2-04 — positional args, async, storage-path read, NOT workspace-filtered) to
    pre-fill the agent's virtual_fs. On any exception the row is marked failed in
    the existing Sprntly error format; the prior bundle_url is preserved.
    """
    try:
        proto = get_prototype(prototype_id=prototype_id, workspace_id=workspace_id)
        if not proto:
            return  # row vanished under us; nothing to iterate.

        checkpoint_id = proto.get("current_checkpoint_id")
        current_source = (
            await read_source_files_for_checkpoint(prototype_id, checkpoint_id)
            if checkpoint_id else {}
        )

        # Open comment threads — the stable cacheable signal (P3-01 list_comments,
        # filtered to open). Project to the {anchor_id, body, author} shape the
        # prompt renderer expects.
        all_comments = list_comments(prototype_id=prototype_id, workspace_id=workspace_id)
        open_comments = [
            {"anchor_id": c["anchor_id"], "body": c["body"], "author": c["author"]}
            for c in all_comments if c.get("status") == "open"
        ]

        # F10 applied-comment: workspace-filtered (it came from the same
        # list_comments read, which filters by workspace) lookup by id, projected
        # to {anchor_id, body}. None when no applied_comment_id or no match.
        applied_comment = None
        if body.applied_comment_id is not None:
            applied_comment = next(
                (
                    {"anchor_id": c["anchor_id"], "body": c["body"]}
                    for c in all_comments if c["id"] == body.applied_comment_id
                ),
                None,
            )

        cacheable_blocks, volatile_block = render_iterate_user(
            current_source=current_source,
            open_comments=open_comments,
            iterate_prompt=body.prompt,
            applied_comment=applied_comment,
        )
        # System block(s) cached at the END of the stable prefix (AD2), mirroring
        # _run_generation_bg. The bundle+comments user prefix is cached too (its
        # last block carries cache_control); the volatile prompt block does not.
        system_blocks = [{
            "type": "text",
            "text": DESIGN_AGENT_ITERATE_SYSTEM,
            "cache_control": {"type": "ephemeral", "ttl": "1h"},
        }]
        user_message = {"role": "user", "content": [*cacheable_blocks, volatile_block]}

        figma_file_key = proto.get("figma_file_key")
        scenario_set = infer_scenario_from_inputs(
            figma_file_key=figma_file_key,
            website_url=proto.get("website_url"),
            github_installation_id=proto.get("github_installation_id"),
            prd_references_codebase=False,  # P4-05 implements the codebase detector
        )
        scenario_label = ",".join(sorted(scenario_set))

        result, virtual_fs = await iterate_prototype(
            prototype_id=prototype_id,
            workspace_id=workspace_id,
            system_blocks=system_blocks,
            user_message=user_message,
            current_source=current_source,
            figma_file_key=figma_file_key,
            scenario=scenario_label,
            # EXECUTE mode for tool partitioning (P3-05 is execute-only; 'plan' is
            # accepted on the request but runs as execute until P3-07 wires the
            # real split). Canonical 'execute', never 'iterate' (AD17 / P3-07).
            mode="execute",
        )

        if result.status == "complete" and virtual_fs:
            await _stage_iterate_run(
                prototype_id=prototype_id,
                workspace_id=workspace_id,
                virtual_fs=virtual_fs,
                iterate_prompt=body.prompt,
            )
        elif result.status == "complete" and not virtual_fs:
            fail_prototype(
                prototype_id=prototype_id,
                workspace_id=workspace_id,
                error="iterate agent_loop completed but emitted no files",
            )
        else:
            # Mirror _run_generation_bg's structured failure (P2-02): surface the
            # RunResult error_message / error_class so an Anthropic failure is
            # triageable rather than dropped. fail_prototype caps at 500 chars.
            error_parts = [
                f"iterate agent_loop ended with status={result.status} iters={result.iters}"
            ]
            error_message = getattr(result, "error_message", None)
            error_class = getattr(result, "error_class", None)
            if error_message:
                error_parts.append(f"error_message={error_message}")
            if error_class:
                error_parts.append(f"error_class={error_class}")
            fail_prototype(
                prototype_id=prototype_id,
                workspace_id=workspace_id,
                error=" | ".join(error_parts),
            )
    except Exception as exc:  # noqa: BLE001 — bg task must never leak; row is failed.
        # error_class only in the structured log (Rule #24 — no PRD / comment /
        # Figma content); the full message goes to the row's error column.
        logger.warning(
            "design_agent.iterate_failed prototype_id=%s error_class=%s",
            prototype_id, type(exc).__name__,
        )
        fail_prototype(
            prototype_id=prototype_id,
            workspace_id=workspace_id,
            error=f"{type(exc).__name__}: {exc}",
        )


async def _run_one_iteration(row: dict[str, Any]) -> None:
    """Run a single dequeued queue row through the P3-05 iterate body (P3-06).

    Adapter for `runner.drain_iteration_queue`: reconstructs the `IterateRequest`
    from the persisted queue row and delegates to `_run_iterate_bg` (UNCHANGED) so
    the drain reuses the exact source-load → prompt-render → agent-loop → stage
    path. `_run_iterate_bg` handles prototype-level failure internally (it marks
    the PROTOTYPE failed and never raises on a runner error), so on the normal
    path the drain marks the queue ROW 'done'. This adapter only re-raises on an
    UNEXPECTED exception (a row missing required keys, etc.), which the drain
    catches to mark the queue row 'failed' without stalling the rest of the queue.
    """
    body = IterateRequest(
        prompt=row["prompt"],
        applied_comment_id=row.get("applied_comment_id"),
        mode=row.get("mode") or "execute",
    )
    await _run_iterate_bg(
        prototype_id=row["prototype_id"],
        workspace_id=row["workspace_id"],
        body=body,
    )


async def _stage_iterate_run(
    *,
    prototype_id: int,
    workspace_id: str,
    virtual_fs: dict[str, str],
    iterate_prompt: str,
) -> None:
    """Iterate-completion staging path (B2). DELIBERATELY SEPARATE from
    `_stage_complete_run`: it does NOT call `complete_prototype` (AC6a). An iterate
    is a checkpoint ADVANCE on an already-completed prototype, so re-stamping
    `completed_at` and emitting `prototype_completed` would be wrong — that whole
    separation is the point of this helper. Do NOT fold it back into the scaffold
    staging path.

    Steps: vite_build (anchor-id plugin runs here) → create_checkpoint (threading
    the iterate prompt into prompt_history) → stage_bundle (dist + raw _source so
    the NEXT iterate can read it back). Then the P3-12 seam advances
    `current_checkpoint_id` + bundle_url WITHOUT a completed_at re-stamp.
    """
    # Step 1 — Vite build (anchor-id plugin runs here, AD4).
    try:
        dist_files = await vite_build(virtual_fs)
    except (ViteBuildError, FileNotFoundError) as exc:
        logger.warning(
            "iterate_vite_build_failed prototype_id=%s error_class=%s",
            prototype_id, type(exc).__name__,
        )
        fail_prototype(
            prototype_id=prototype_id,
            workspace_id=workspace_id,
            error=f"{type(exc).__name__}: {exc}",
        )
        return
    logger.info(
        "iterate_vite_build_succeeded prototype_id=%s dist_file_count=%s",
        prototype_id, len(dist_files),
    )

    # Step 2 — new checkpoint; thread the iterate prompt into prompt_history (AC6a).
    checkpoint_id = create_checkpoint(
        prototype_id=prototype_id,
        workspace_id=workspace_id,
        bundle_url=None,
        prd_revision_hash=None,    # P3-12 wires PRD-hash + figma-hash on this path
        figma_frame_hash=None,
        prompt_history=[{"kind": "iterate", "prompt": iterate_prompt}],
        comment_state=[],
    )

    # Step 3 — stage the BUILT dist/ (never raw virtual_fs).
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

    # Step 3.5 — stage the RAW virtual_fs under _source/ so the NEXT iterate's
    # read_source_files_for_checkpoint returns real TSX. Best-effort (mirrors
    # _stage_complete_run): a source-stage failure logs and proceeds.
    try:
        await stage_bundle(
            prototype_id=prototype_id,
            checkpoint_id=checkpoint_id,
            files=virtual_fs,
            sub_prefix="_source",
        )
    except Exception as exc:  # noqa: BLE001 — source-stage is best-effort.
        logger.warning(
            "iterate_source_stage_failed prototype_id=%s checkpoint_id=%s error_class=%s",
            prototype_id, checkpoint_id, type(exc).__name__,
        )

    # Step 3.6 — AD12 orphan/re-attach on the ITERATE path. An iterate is a new
    # checkpoint build, so per AD12 it MUST reconcile comments too (P3-05 shipped
    # before this helper existed; wired here so generate AND iterate both orphan
    # vanished anchors). Same path-agnostic helper as _stage_complete_run — it
    # keys on prototype_id, not checkpoint_id. Best-effort: a reconcile failure
    # must NOT fail the iterate (the bundle is already staged).
    try:
        reconcile_comments_on_checkpoint(
            prototype_id=prototype_id,
            workspace_id=workspace_id,
            dist_files=dist_files,
        )
    except Exception as exc:  # noqa: BLE001 — reconcile is best-effort housekeeping.
        logger.warning(
            "comments_reconcile_failed prototype_id=%s error_class=%s",
            prototype_id, type(exc).__name__,
        )

    # Step 4 — P3-12 SEAM. Advance current_checkpoint_id + bundle_url WITHOUT a
    # completed_at re-stamp (NOT complete_prototype — AC6a).
    _advance_current_checkpoint_seam(
        prototype_id=prototype_id,
        workspace_id=workspace_id,
        checkpoint_id=checkpoint_id,
        bundle_url=bundle_url,
    )


def _advance_current_checkpoint_seam(
    *,
    prototype_id: int,
    workspace_id: str,
    checkpoint_id: int,
    bundle_url: str | None,
) -> None:
    """P3-12 SEAM (documented, intentional — see the ticket's Iterate staging path
    + the P3-05/P3-12 ordering decision).

    P3-12 lands `db.prototypes.advance_current_checkpoint`, which advances
    `prototypes.current_checkpoint_id` → `checkpoint_id` and threads `bundle_url`
    onto the prototype row WITHOUT re-stamping `completed_at` (the iterate-correct
    counterpart to `complete_prototype` — AC6a). Until it merges, the new
    checkpoint is fully built + staged and this records the pending advance as an
    INFO marker (identifiers only, Rule #24). When P3-12 lands, it fills the call
    here — that edit lives in this builder-owned block, not a hot file.
    """
    logger.info(
        "prototype_iterate_checkpoint_staged prototype_id=%s workspace_id=%s "
        "checkpoint_id=%s bundle_staged=%s advance_pending=P3-12",
        prototype_id, workspace_id, checkpoint_id, bool(bundle_url),
    )
