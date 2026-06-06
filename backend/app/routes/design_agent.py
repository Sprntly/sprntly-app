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
import json
import logging
import os
from typing import Any, Literal
from urllib.parse import urlsplit

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import Response, StreamingResponse
from pydantic import BaseModel, Field

from app.auth import CompanyContext, require_company, require_company_from_query  # company-scoped auth dep
from app.design_agent.csrf import require_same_origin  # P5-06 server-side CSRF/Origin gate
from app.design_agent.rate_limit import (  # P5-07 public-surface rate limits
    PUBLIC_COMMENT_LIMITER,
    PUBLIC_TOKEN_LIMITER,
)
from app.db.prds import get_prd_rendered, reset_prd_to_draft
from app.db.products import get_company_website  # onboarding-website fallback source
from app.db.prototype_exports import find_prototype_export
from app.db.prototypes import (
    advance_current_checkpoint,
    complete_prototype,
    create_checkpoint,
    delete_prototype,
    fail_prototype,
    find_existing_prototype,
    find_prototype_by_share_token,
    find_ready_prototype_by_prd,
    flag_stale_handoff,
    get_prototype,
    infer_scenario_from_inputs,
    mark_awaiting_clarification,
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
from app.design_agent.client import get_design_agent_client
from app.design_agent.prompts import (
    DESIGN_AGENT_SCAFFOLD_SYSTEM,
    DESIGN_AGENT_TEMPLATE_VERSION,
    render_scaffold_user,
)
from app.design_agent.event_stream import subscribe as _sse_subscribe
from app.design_agent.runner import generate_prototype, reconcile_comments_on_checkpoint
from app.design_agent.screenshot import capture_bundle_screenshot  # best-effort preview capture
from app.design_agent.storage import (
    TypeCheckError,
    ViteBuildError,
    stage_bundle,
    stage_preview_image,
    vite_build,
    vite_build_with_repair,
)

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


class ManualDesignInput(BaseModel):
    """P5-02: the absolute Scenario-B floor — a user-supplied brand color + font
    that styles the prototype even when there is no Figma and no extractable
    website design system (the noon-Day-11 manual cut). Both fields required when
    the object is present; the whole object is optional on the request."""

    primary_color: str = Field(..., min_length=1)   # e.g. "#3b82f6"
    font_family: str = Field(..., min_length=1)      # e.g. "Inter"


class GenerateRequest(BaseModel):
    prd_id: int = Field(..., gt=0)
    target_platform: str = Field("both")  # "desktop" | "mobile" | "both"
    instructions: str = Field("")
    figma_file_key: str | None = None     # explicit; auto-detection via the
    #                                       connector lookup lands in a later phase.
    website_url: str | None = None        # P5-02: Scenario B fallback source
    manual_design: ManualDesignInput | None = None  # P5-02: absolute floor
    github_repo: str | None = None        # connected-repo full_name ("org/repo");
    #                                       prompt context only — no fetch, no clone,
    #                                       no agent tool. The repo identifier travels
    #                                       into the scaffold prompt so generation can
    #                                       be told which existing codebase to match.

    def normalised_platform(self) -> str:
        return self.target_platform.strip().lower() or "both"

    def normalised_github_repo(self) -> str | None:
        """Treat an explicit empty / whitespace-only repo the same as absent."""
        v = (self.github_repo or "").strip()
        return v or None


class GenerateResponse(BaseModel):
    prototype_id: int
    status: str  # "generating" | "ready"


# ─── Routes ───────────────────────────────────────────────────────────────


@router.post(
    "/generate",
    response_model=GenerateResponse,
    dependencies=[Depends(require_same_origin)],  # P5-06 CSRF/Origin gate (authed mutating)
)
async def generate(
    body: GenerateRequest,
    company: CompanyContext = Depends(require_company),
) -> GenerateResponse:
    """Kick off prototype generation in the background; return the id in <200ms.

    Short-circuits when a ready/generating row already exists for this PRD under
    this workspace + template_version (mirrors routes/prd.py's find_existing
    dedupe) so a double-click on Generate does not fan out duplicate runs.
    """
    _require_feature_enabled()
    workspace_id = company.company_id

    # Sync DB helpers, called directly (no await) — see CALL-STYLE NOTE.
    existing = find_existing_prototype(
        prd_id=body.prd_id,
        workspace_id=workspace_id,
        template_version=DESIGN_AGENT_TEMPLATE_VERSION,
        variant=_VARIANT,
    )
    if existing:
        return GenerateResponse(prototype_id=existing["id"], status=existing["status"])

    # Onboarding website as the automatic design source fallback.
    # Design-source precedence: Figma → website → manual → none. When the user
    # connected NO Figma file AND typed NO website URL AND supplied no manual
    # design hints, fall back to the company's onboarding website
    # (products.website) so it becomes the automatic design source. We never
    # override an explicit Figma file or a user-typed website_url, and we only
    # consult the helper for the genuinely-empty case so Figma runs skip the DB
    # read entirely. The resolved value is threaded into BOTH the prototype
    # snapshot (below) and the background generation task, so it's observable on
    # the prototype row's website_url column.
    effective_website_url = body.website_url
    typed = (body.website_url or "").strip()
    if not body.figma_file_key and not typed and body.manual_design is None:
        fallback_url = get_company_website(workspace_id)
        if fallback_url:
            effective_website_url = fallback_url
            logger.info(
                "design_agent_website_fallback company_id=%s prd_id=%s host=%s",
                workspace_id,
                body.prd_id,
                urlsplit(fallback_url).hostname or "",
            )

    # Connected-repo identifier the user chose as the existing codebase to match.
    # Prompt context only (no fetch, no clone, no agent tool). NO-PERSIST decision:
    # `start_prototype` exposes no repo/codebase text column and this ticket adds
    # no migration (NO `ALTER` on prds/briefs/evidences), so the repo is NOT
    # snapshotted on the prototype row — it travels as a request-only field into
    # the background generation task (prompt context + cost-summary identifier).
    repo = body.normalised_github_repo()

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
        website_url=effective_website_url,  # snapshot; resolved value incl. onboarding fallback
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
            website_url=effective_website_url,  # resolved value incl. onboarding fallback
            manual_design=body.manual_design,
            github_repo=repo,  # normalised connected-repo full_name; prompt context only
        )
    )
    _inflight_tasks.add(task)
    task.add_done_callback(_inflight_tasks.discard)

    return GenerateResponse(prototype_id=prototype_id, status="generating")


# ─── PRD patches: list pending (P3-10, F11) ─────────────────────────────────────
#
# F11's user-facing half. P3-09 persists agent-proposed PRD edits as `pending` rows
# in `prd_patches` (NEVER touching `prds.payload_md`); the `PrdPatchBanner` surfaces
# them and the POST accept/reject routes (at EOF) resolve them. Accept flips a row
# to `applied`; the RENDERED PRD reflects it on the next load via
# `apply_patches_to_prd_md` — the banner never mutates the PrdScreen contentEditable.
#
# ROUTE ORDER IS LOAD-BEARING — do NOT "tidy" this block down to EOF with its POST
# siblings. The list path `/prd-patches` is a SINGLE segment, so Starlette would
# match it against the earlier `GET /{prototype_id}` catch-all below (the int
# validation on `prototype_id` happens in the handler, not the path regex) and
# return 422 before this handler is ever reached. Declaring it ABOVE the catch-all
# is the FastAPI static-before-dynamic fix. `PrdPatchOut` + `_patch_to_out` + the
# `prd_patches` import live here (not at EOF) because the list decorator's
# `response_model` needs `PrdPatchOut` defined at module-load time; the EOF POSTs
# reference these same module-level symbols. Same gate posture as the authed routes:
# feature-flag 404 when off + require_app_session 401 + workspace filter.
from app.db.prd_patches import (
    list_pending_patches,
    mark_patch_applied,
    mark_patch_rejected,
)


class PrdPatchOut(BaseModel):
    id: int
    prd_id: int
    prototype_id: int
    rationale: str
    patch_md: str
    status: str           # 'pending' | 'applied' | 'rejected'
    created_at: str


def _patch_to_out(row: dict[str, Any]) -> dict[str, Any]:
    """Project a `prd_patches` row to the PrdPatchOut shape (ISO-string timestamp).

    `created_at` is stringified defensively (same reasoning as `_comment_to_out`):
    Postgres returns a timestamptz object via supabase, the SQLite fake returns
    TEXT — `str()` normalises both to the ISO string PrdPatchOut expects. The
    internal `workspace_id` / `resolved_at` columns are deliberately NOT projected
    (the banner needs neither)."""
    return {
        "id": row["id"],
        "prd_id": row["prd_id"],
        "prototype_id": row["prototype_id"],
        "rationale": row["rationale"],
        "patch_md": row["patch_md"],
        "status": row["status"],
        "created_at": str(row["created_at"]),
    }


@router.get("/prd-patches", response_model=list[PrdPatchOut])
def get_pending_patches(
    prd_id: int,
    company: CompanyContext = Depends(require_company),
) -> list[PrdPatchOut]:
    """List the PENDING patches for a PRD (created_at-ascending), workspace-filtered.

    Only `pending` rows surface — `applied`/`rejected` are resolved and excluded by
    `list_pending_patches`. A PRD with no pending patches returns `[]` (the banner
    renders nothing). 401 without a session; 404-invisibility is moot here (a
    foreign-workspace PRD simply yields no rows under this workspace filter)."""
    _require_feature_enabled()
    workspace_id = company.company_id
    return [
        PrdPatchOut(**_patch_to_out(p))
        for p in list_pending_patches(prd_id=prd_id, workspace_id=workspace_id)
    ]


# ─── Figma file listing (Generate modal design-source selector) ──────────────


class FigmaFileItem(BaseModel):
    """One listable Figma file for the Generate modal's design selector."""

    key: str
    name: str


class FigmaFilesResponse(BaseModel):
    files: list[FigmaFileItem]


@router.get("/figma-files", response_model=FigmaFilesResponse)
def list_figma_files(
    company: CompanyContext = Depends(require_company),
) -> FigmaFilesResponse:
    """List the caller's Figma files for the Generate modal's design selector.

    A read-only proxy over the Figma REST API using the company's stored Figma
    OAuth token. Single-segment static path declared ABOVE the
    `GET /{prototype_id}` catch-all so a request here is never shadowed into it.

    Gating order matters: the feature flag is checked FIRST, so a probe with the
    flag off returns 404 (never 401/403) and cannot tell this route from a
    missing one -- matching every other Design Agent route's posture. Then the
    Figma token is resolved scoped to the CALLER's company only
    (`company.company_id`): a company never resolves another company's files, and
    an unconnected company gets the same 404 the by-key route returns.

    Honest degradation: the current Figma OAuth grant has no project/team-listing
    scope and no stored team id, and the Figma REST API has no flat "list my
    files" endpoint, so `fetch_files` returns an empty list until that upstream
    provisioning lands (a connectors-lane dependency). Any upstream listing
    failure is mapped to an empty list here -- never a 500 leaking the upstream
    body -- so the modal renders an honest empty state rather than fake files.
    """
    _require_feature_enabled()
    # Lazy imports mirror runner.py's `_resolve_figma_access_token`: they keep
    # this module importable without the connector/db stack at import time (which
    # the route-test module-reload env depends on) and let tests patch the token
    # resolver + the REST helper. routes/connectors.py owns the token decryption
    # (reused, not reimplemented); connectors/figma_oauth.py owns the REST call.
    from app.connectors import figma_oauth
    from app.routes.connectors import _figma_access_token

    token = _figma_access_token(company.company_id)  # 404 when Figma not connected
    try:
        files = figma_oauth.fetch_files(token)
    except Exception:  # upstream listing failure -> honest empty state, never 500
        logger.warning(
            "design_agent.figma_files_list_failed company_id=%s", company.company_id
        )
        files = []
    logger.info(
        "design_agent.figma_files_listed company_id=%s count=%d",
        company.company_id,
        len(files),
    )
    return FigmaFilesResponse(
        files=[FigmaFileItem(key=f["key"], name=f["name"]) for f in files]
    )


@router.get("/{prototype_id}")
def get_one(
    prototype_id: int,
    company: CompanyContext = Depends(require_company),
) -> dict[str, Any]:
    """Return the full prototype row for the frontend poller (P1-09).

    Sync handler (mirrors routes/prd.py's GET) — FastAPI runs it in the
    threadpool, so the blocking supabase read does not stall the event loop.
    Workspace-filtered: a row in a different workspace returns 404, not 403,
    so cross-tenant existence is not even disclosed (Rule #22).
    """
    _require_feature_enabled()
    workspace_id = company.company_id
    row = get_prototype(prototype_id=prototype_id, workspace_id=workspace_id)
    if not row:
        raise HTTPException(status_code=404, detail="Prototype not found")
    return row


@router.delete("/{prototype_id}", status_code=204)
def delete_prototype_route(
    prototype_id: int,
    company: CompanyContext = Depends(require_company),
) -> Response:
    _require_feature_enabled()
    workspace_id = company.company_id
    existing = get_prototype(prototype_id=prototype_id, workspace_id=workspace_id)
    if existing is None:
        raise HTTPException(status_code=404, detail="Prototype not found")
    delete_prototype(prototype_id=prototype_id, workspace_id=workspace_id)
    reset_prd_to_draft(existing["prd_id"])
    return Response(status_code=204)


@router.get("/by-prd/{prd_id}")
def get_by_prd(
    prd_id: int,
    company: CompanyContext = Depends(require_company),
) -> dict[str, Any]:
    """Return the most-recent READY prototype for a PRD (read-only lookup).

    A pure read with NO generate side-effect — unlike the dedup inside
    POST /generate, which kicks off a generation when none exists. That makes
    it safe to call on PRD-screen load so the frontend can render a preview
    card / flip Approve to "View Prototype". Returns 404 when no ready
    prototype exists (the frontend swallows 404→null). Workspace-filtered: a
    prototype in another workspace returns 404, not 403, so cross-tenant
    existence is not disclosed. The path is two-segment (`/by-prd/{prd_id}`),
    so it can never be shadowed by the single-segment `GET /{prototype_id}`
    catch-all above regardless of declaration order — a one-segment route
    pattern only ever matches one-segment paths.
    """
    _require_feature_enabled()
    row = find_ready_prototype_by_prd(
        prd_id=prd_id, workspace_id=company.company_id
    )
    if not row:
        raise HTTPException(status_code=404, detail="No ready prototype for this PRD")
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
    website_url: str | None = None,
    manual_design: ManualDesignInput | None = None,
    github_repo: str | None = None,
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
        # Single "design source" slot. Figma always wins when present (AC3) — the
        # website block is not even built in that case. When there is no Figma,
        # Scenario B (extracted/manual website design system) takes the slot;
        # `_website_context_block` returns None when there is neither a website
        # URL nor manual hints, so we fall back to the generic Figma string
        # ("(no Figma source detected)").
        website_block = (
            None if figma_file_key
            else await _website_context_block(website_url, manual_design)
        )
        source_block = website_block or _figma_context_block(figma_file_key)

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
            figma_frames=source_block,
            codebase_repo=github_repo,  # one-line "match this codebase" context; None -> "(no codebase source)"
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
            website_url=website_url,        # P5-02: derives 'B' (url, no figma) / '0'
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
            github_repo=github_repo,  # cost-summary identifier only; does NOT alter the scenario label
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
    # Step 1 — Vite build (anchor-id plugin runs here). P6-07: the bounded
    # unresolved-relative-import repair wrapper stubs/strips an orphan `./screens/*`
    # import (the degrade-converged 2/2-repro) and rebuilds instead of shipping
    # status=failed; on exhaustion it raises UnresolvedImportRepairExhausted (a
    # ViteBuildError subclass — caught by the tuple below, distinct error_class).
    # vite_build_with_repair returns the (possibly) REPAIRED virtual_fs as its
    # second element; we REBIND `virtual_fs` here, BEFORE the `_source/` staging
    # step below, so the staged source matches the built dist.
    try:
        dist_files, repaired_virtual_fs = await vite_build_with_repair(virtual_fs)
    except (ViteBuildError, FileNotFoundError, TypeCheckError) as exc:
        # P3-15 (B3): TypeCheckError joins the precise build-failure tuple so a
        # runtime-break diagnostic routes to fail_prototype with the diagnostic in
        # `prototypes.error` (NOT the generic outer except — which would lose this
        # precise handling). error_class only in the log (Rule #24 — no
        # stderr/secrets); the full message (incl. fatal codes) goes to the row's
        # error column.
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
    # P6-07: rebind to the repaired source BEFORE the `_source/` staging step so
    # the staged source matches the built dist. On a clean build this is the same
    # map. When a repair was applied (the map changed), emit build_repair_applied
    # with an action count only (Rule #24 — no source / no import paths): stubs add
    # keys, strips change file bodies.
    if repaired_virtual_fs != virtual_fs:
        repair_actions = len(set(repaired_virtual_fs) - set(virtual_fs)) + sum(
            1 for k in virtual_fs
            if k in repaired_virtual_fs and repaired_virtual_fs[k] != virtual_fs[k]
        )
        logger.info(
            "build_repair_applied prototype_id=%s actions=%d",
            prototype_id, repair_actions,
        )
    virtual_fs = repaired_virtual_fs
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

    # Step 3.7 — best-effort preview screenshot of the staged bundle. HONEST-DEGRADE:
    # a capture that returns no image (no browser runtime / nav error / timeout), or
    # raises, leaves preview_image_url None and the prototype STILL completes ready.
    # No fake/placeholder image is ever stored. Runs on the success path only — a
    # build failure returned above before reaching here. When a Chromium runtime is
    # provisioned on the host the thumbnail just works; until then it degrades to null.
    preview_image_url = None
    try:
        png = await capture_bundle_screenshot(bundle_url)
        if png is not None:
            preview_image_url = await stage_preview_image(
                prototype_id=prototype_id,
                checkpoint_id=checkpoint_id,
                png_bytes=png,
            )
            logger.info(
                "preview_captured prototype_id=%s checkpoint_id=%s",
                prototype_id, checkpoint_id,
            )
        else:
            # Capture degraded internally (no browser / nav / timeout) — the
            # specific class was handled inside the capture helper.
            logger.warning(
                "preview_capture_failed prototype_id=%s checkpoint_id=%s error_class=%s",
                prototype_id, checkpoint_id, "unavailable",
            )
    except Exception as exc:  # noqa: BLE001 — capture is best-effort; never fail completion.
        logger.warning(
            "preview_capture_failed prototype_id=%s checkpoint_id=%s error_class=%s",
            prototype_id, checkpoint_id, type(exc).__name__,
        )

    # Step 4 — mark ready + thread current_checkpoint_id back to the prototype.
    complete_prototype(
        prototype_id=prototype_id,
        workspace_id=workspace_id,
        bundle_url=bundle_url,
        current_checkpoint_id=checkpoint_id,
        preview_image_url=preview_image_url,
    )


def _load_prd_body(prd_id: int) -> str:
    """Fetch the PRD's `payload_md` for the agent's user message.

    Uses `get_prd_rendered` (db/prds.py, P3-17) so the body the agent sees in its
    iterate user-message reflects accepted (status='applied') prd_patches folded
    in at read time (F11 render-on-read). Like the underlying `get_prd`, this is
    NOT workspace-scoped — PRDs predate the workspace_id primitive, and
    `routes/prd.py` reads them the same way under its own auth dependency. Per AC
    #10 this is the documented fallback: the route's `require_app_session` gate is
    the access boundary; a workspace filter is added if/when `get_prd` grows a
    `workspace_id` param. Raises 404 (surfaced into the row's error via the
    caller's except) when the PRD does not exist.
    """
    prd = get_prd_rendered(prd_id)
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


def _is_usable_color(value: str | None) -> bool:
    """True iff `value` is a real, non-transparent color worth feeding the scaffold.

    Rejects ``None``, empty/whitespace, the literal ``transparent``, and any
    zero-alpha ``rgba(...)`` / ``hsla(...)`` (alpha component == 0). Everything
    else (hex, ``rgb()``, named colors, non-zero-alpha ``rgba()``) is usable.

    Why this is load-bearing (P5-01 verifier finding, 2026-06-02): the extractor's
    own ``_below_confidence`` guard only checks for an EMPTY color string, so a
    NON-empty transparent value like ``rgba(0,0,0,0)`` — which P5-01's live runs
    returned as the "primary color" on Stripe/Linear (modern CSS-layered sites) —
    passes that guard and would otherwise flow straight into the scaffold prose.
    This is the second gate that catches it. Do NOT call this on font/logo fields.
    """
    if value is None:
        return False
    v = value.strip().lower()
    if not v or v == "transparent":
        return False
    if v.startswith(("rgba(", "hsla(")) and ")" in v:
        inner = v[v.index("(") + 1 : v.rindex(")")]
        parts = [p.strip() for p in inner.split(",")]
        if len(parts) == 4:
            try:
                if float(parts[3]) == 0.0:
                    return False
            except ValueError:
                pass  # non-numeric alpha → treat as usable (don't over-reject)
    return True


def _manual_design_block(primary_color: str, font_family: str, *, host: str | None) -> str:
    """Scaffold prose for user-supplied brand hints (the manual floor)."""
    prefix = (
        f"No design system could be extracted from {host}; "
        if host else ""
    )
    return (
        f"{prefix}Use these user-supplied brand hints as the design system. "
        f"Primary color: {primary_color}. Heading and body font: {font_family}. "
        "Build a clean, modern interface around this color and typography."
    )


def _url_only_neutral_block(host: str) -> str:
    """Scaffold prose when a URL was given but nothing usable was extracted and
    no manual hints are available — instruct a clean neutral palette."""
    return (
        f"The brand site at {host} was provided but no usable design system could "
        "be extracted — produce a clean, neutral, modern design (no specific brand "
        "color or font available)."
    )


def _extracted_design_block(
    ds: dict[str, Any],
    *,
    host: str,
    manual_design: "ManualDesignInput | None",
) -> str:
    """Render an extracted website design system as scaffold prose.

    The COLOR fields are gated through `_is_usable_color`: a transparent /
    zero-alpha extracted color is treated as below-confidence FOR THAT FIELD
    ONLY — the color is then sourced from `manual_design` (if present) or a
    neutral-palette instruction, while the good extracted font/logo/spacing
    signal is KEPT. A transparent value NEVER reaches the prose.
    """
    parts: list[str] = [
        f"Design system extracted from the brand website ({host}). "
        "Match this visual identity in the prototype."
    ]

    primary = ds.get("primary_color")
    if _is_usable_color(primary):
        parts.append(f"Primary color: {primary}.")
    elif manual_design is not None:
        parts.append(
            f"Primary color: {manual_design.primary_color} (user-supplied; no "
            "usable brand color could be extracted from the site)."
        )
    else:
        parts.append(
            "No usable brand color extracted from the site — produce a clean "
            "neutral palette."
        )

    background = ds.get("background_color")
    if _is_usable_color(background):
        parts.append(f"Background color: {background}.")

    if ds.get("heading_font_family"):
        parts.append(f"Heading font: {ds['heading_font_family']}.")
    if ds.get("heading_size_scale"):
        parts.append(f"Heading size: {ds['heading_size_scale']}.")
    if ds.get("body_font_family"):
        parts.append(f"Body font: {ds['body_font_family']}.")
    if ds.get("border_radius_convention"):
        parts.append(f"Border radius: {ds['border_radius_convention']}.")
    spacing = ds.get("spacing_scale_samples") or []
    if spacing:
        parts.append(f"Spacing samples: {', '.join(spacing)}.")
    if ds.get("logo_url"):
        parts.append(f"Logo: {ds['logo_url']}.")

    return " ".join(parts)


async def _website_context_block(
    website_url: str | None,
    manual_design: "ManualDesignInput | None",
) -> str | None:
    """Scaffold context for Scenario B (analog of `_figma_context_block`).

    Returns a prose design-system block, or ``None`` when there is no website
    source at all (the caller then uses the Figma block / the generic
    "(no source)" string). Precedence: extracted > manual > url-only.

      1. ``website_url`` set → ``extract_website_design_system`` (P5-01). On a
         non-``None`` dict, render it (with the transparent-color gate).
      2. extractor ``None`` + ``manual_design`` present → manual prose.
      3. extractor ``None`` + no manual + a URL was given → url-only neutral block.
      4. no ``website_url`` but ``manual_design`` present → manual prose
         (Scenario-0-with-manual-hints; the run is labelled '0' because
         `infer_scenario_from_inputs` keys 'B' off `website_url`, but the hints
         MUST still reach the scaffold — decision 2026-06-02, AC10).
      5. neither → ``None``.

    The P5-01 import is lazy + ImportError-guarded so the manual-floor half ships
    independently of the extractor (the noon-Day-11 cut, AC5).
    """
    if website_url:
        host = urlsplit(website_url).hostname or website_url
        ds: dict[str, Any] | None = None
        try:
            from app.design_agent.scenarios.website import (
                extract_website_design_system,
            )
            ds = await extract_website_design_system(website_url)
        except ImportError:
            ds = None  # P5-01 not merged → fall through to manual / url-only.
        if ds is not None:
            return _extracted_design_block(ds, host=host, manual_design=manual_design)
        if manual_design is not None:
            return _manual_design_block(
                manual_design.primary_color, manual_design.font_family, host=host
            )
        return _url_only_neutral_block(host)

    # No website_url: only the manual hints can supply a design system.
    if manual_design is not None:
        return _manual_design_block(
            manual_design.primary_color, manual_design.font_family, host=None
        )
    return None


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
def get_by_token(token: str, request: Request) -> PublicPrototypeView:
    """Resolve a share_token to its viewable prototype. NO auth — the token IS
    the access. Returns 404 (never 401/403) on a bad token, a `private` row, or a
    row whose `status` is not `ready` (a generating/failed prototype is not
    viewable, and we do not disclose that it exists). `bundle_url` is withheld
    (null) for passcode mode until POST /passcode succeeds.

    `request: Request` is injected by FastAPI only to source nothing user-visible —
    the path/response_model are unchanged, so the web client contract is intact.
    """
    _require_feature_enabled()
    th = _share_token_hash(token)
    # P5-07: per-token view rate limit (60/min/token). Mounted AFTER the feature
    # gate (feature-off still 404s first) and BEFORE token resolution: the 429 fires
    # on scan velocity for a VALID-FORMAT token regardless of whether it resolves, so
    # it reveals only "you are scanning fast," never whether a token exists. A
    # non-existent token under the limit still returns 404.
    if not PUBLIC_TOKEN_LIMITER.check(token):
        retry_after = PUBLIC_TOKEN_LIMITER.retry_after(token)
        logger.info(
            "public_token_rate_limited token_hash=%s retry_after_seconds=%s",
            th, retry_after,
        )
        raise HTTPException(
            status_code=429,
            detail={"error": "rate_limit", "retry_after_seconds": retry_after},
        )
    PUBLIC_TOKEN_LIMITER.register(token)
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


@router.post(
    "/{prototype_id}/complete",
    response_model=CompleteResponse,
    dependencies=[Depends(require_same_origin)],  # P5-06 CSRF/Origin gate (authed mutating)
)
async def post_complete(
    prototype_id: int,
    company: CompanyContext = Depends(require_company),
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
    workspace_id = company.company_id
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


@router.post(
    "/{prototype_id}/resume",
    response_model=ResumeResponse,
    dependencies=[Depends(require_same_origin)],  # P5-06 CSRF/Origin gate (authed mutating)
)
def post_resume(
    prototype_id: int,
    company: CompanyContext = Depends(require_company),
) -> ResumeResponse:
    """F15: unlock the prototype + flag any open handoff record as stale.

    Sets is_complete=false. Calls flag_stale_handoff(prototype_id) which marks the
    most-recent `prototype_exports` row stale (that row IS the handoff record per
    the 2026-05-29 decision). Returns 0 when no export exists yet, so resume on a
    never-completed prototype is a clean no-op on the handoff surface.
    Idempotent: resume-on-WIP is a 200 no-op.
    """
    _require_feature_enabled()
    workspace_id = company.company_id
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


@router.post(
    "/{prototype_id}/share",
    response_model=ShareResponse,
    dependencies=[Depends(require_same_origin)],  # P5-06 CSRF/Origin gate (authed mutating)
)
def post_share(
    prototype_id: int,
    body: ShareRequest,
    company: CompanyContext = Depends(require_company),
) -> ShareResponse:
    """F6: set / update share configuration. Wraps set_share_config (P2-06).

    On passcode mode without a passcode, returns 400. On unknown mode → 422
    (caught by pydantic). On row-not-found in this workspace → 404.
    """
    _require_feature_enabled()
    workspace_id = company.company_id
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
    company: CompanyContext = Depends(require_company),
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
    workspace_id = company.company_id
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
    pin_x_pct: float | None = Field(default=None, ge=0, le=100)
    pin_y_pct: float | None = Field(default=None, ge=0, le=100)
    resolved_anchor_id: str | None = Field(default=None, max_length=64)


class CommentOut(BaseModel):
    id: int
    anchor_id: str
    body: str
    author: str
    status: str           # 'open' | 'resolved' | 'orphaned'
    created_at: str
    resolved_at: str | None = None
    pin_x_pct: float | None = None
    pin_y_pct: float | None = None
    resolved_anchor_id: str | None = None


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
        "pin_x_pct": row.get("pin_x_pct"),
        "pin_y_pct": row.get("pin_y_pct"),
        "resolved_anchor_id": row.get("resolved_anchor_id"),
    }


# ─── Internal (authed) comment routes ─────────────────────────────────────


@router.post(
    "/{prototype_id}/comments",
    response_model=CommentOut,
    dependencies=[Depends(require_same_origin)],  # P5-06 CSRF/Origin gate (authed mutating)
)
def post_comment(
    prototype_id: int,
    body: CommentCreate,
    company: CompanyContext = Depends(require_company),
) -> CommentOut:
    """Create a comment as an internal user. Workspace-filtered: 404 if the
    prototype is not in the caller's workspace (cross-tenant existence is not
    disclosed — Rule #22). Attributed to the internal author label."""
    _require_feature_enabled()
    workspace_id = company.company_id
    proto = get_prototype(prototype_id=prototype_id, workspace_id=workspace_id)
    if not proto:
        raise HTTPException(status_code=404, detail="Prototype not found")
    row = insert_comment(
        prototype_id=prototype_id,
        workspace_id=workspace_id,
        anchor_id=body.anchor_id,
        body=body.body,
        author=company.user_name or company.user_email or company.user_id,
        user_id=company.user_id,
        pin_x_pct=body.pin_x_pct,
        pin_y_pct=body.pin_y_pct,
        resolved_anchor_id=body.resolved_anchor_id,
    )
    return CommentOut(**_comment_to_out(row))


@router.get("/{prototype_id}/comments", response_model=list[CommentOut])
def get_comments(
    prototype_id: int,
    company: CompanyContext = Depends(require_company),
) -> list[CommentOut]:
    """List every comment for a prototype (all statuses, created_at-ascending).
    Workspace-filtered: 404 if the prototype is not in the caller's workspace."""
    _require_feature_enabled()
    workspace_id = company.company_id
    proto = get_prototype(prototype_id=prototype_id, workspace_id=workspace_id)
    if not proto:
        raise HTTPException(status_code=404, detail="Prototype not found")
    return [
        CommentOut(**_comment_to_out(r))
        for r in list_comments(prototype_id=prototype_id, workspace_id=workspace_id)
    ]


@router.patch(
    "/{prototype_id}/comments/{cid}/resolve",
    response_model=CommentOut,
    dependencies=[Depends(require_same_origin)],  # P5-06 CSRF/Origin gate (authed mutating)
)
def patch_resolve_comment(
    prototype_id: int,
    cid: int,
    company: CompanyContext = Depends(require_company),
) -> CommentOut:
    """Resolve a comment (internal only — external viewers cannot resolve, per
    spec §4 Stage 2 'only internal users with credentials can act'). Returns 404
    when the comment is not in the caller's workspace OR belongs to a different
    prototype than the one in the path (no cross-prototype resolve)."""
    _require_feature_enabled()
    workspace_id = company.company_id
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
    raise HTTPException(status_code=404, detail="Not found")
    _require_feature_enabled()
    proto = find_prototype_by_share_token(token)
    if not proto or proto.get("share_mode") == "private" or proto.get("status") != "ready":
        raise HTTPException(status_code=404, detail="Not found")
    # P5-07: per-IP public-comment rate limit (10/hour/IP). Mounted AFTER the 404
    # resolution (a private/missing/not-ready prototype 404s first, so the limiter
    # never discloses a hidden prototype's existence) and BEFORE insert_comment (the
    # spend-meaningful write). Keyed by client IP — the same machine can spam across
    # many tokens, so per-IP, not per-token, is the spam boundary. Null-guard mirrors
    # the passcode route's `request.client.host if request.client else "0.0.0.0"`.
    client_ip = request.client.host if request.client else "0.0.0.0"
    if not PUBLIC_COMMENT_LIMITER.check(client_ip):
        retry_after = PUBLIC_COMMENT_LIMITER.retry_after(client_ip)
        logger.info(
            "public_comment_rate_limited ip_present=%s retry_after_seconds=%s",
            request.client is not None, retry_after,
        )
        raise HTTPException(
            status_code=429,
            detail={"error": "rate_limit", "retry_after_seconds": retry_after},
        )
    PUBLIC_COMMENT_LIMITER.register(client_ip)
    row = insert_comment(
        prototype_id=proto["id"],
        workspace_id=proto["workspace_id"],   # from the resolved row, not a session
        anchor_id=body.anchor_id,
        body=body.body,
        author="external",
        pin_x_pct=body.pin_x_pct,
        pin_y_pct=body.pin_y_pct,
        resolved_anchor_id=body.resolved_anchor_id,
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


# ─── Comment clarify (P7 — comment-clarify dialog) ──────────────────────────────
#
# POST /{prototype_id}/clarify-comment
#
# Lightweight LLM call (claude-haiku-4-5-20251001, max_tokens=200) that
# generates a single clarifying question for a comment body before the Apply
# flow commits an iterate. Backed by the shared `get_design_agent_client()`
# factory (AD16 — spend attributed to DESIGN_AGENT_ANTHROPIC_API_KEY).
# Not in the iterate queue — this is a synchronous pre-flight, fast enough
# (<1s on Haiku) to sit in the request path without a background task.


class ClarifyCommentRequest(BaseModel):
    comment_body: str = Field(..., min_length=1, max_length=4000)


class ClarifyCommentResponse(BaseModel):
    question: str


@router.post("/{prototype_id}/clarify-comment", response_model=ClarifyCommentResponse)
def clarify_comment_route(
    prototype_id: int,
    body: ClarifyCommentRequest,
    company: CompanyContext = Depends(require_company),
) -> ClarifyCommentResponse:
    """Return a single clarifying question for a comment before Apply is confirmed.

    Workspace-isolated (require_company) and feature-flag-gated. Uses the shared
    Design Agent Anthropic client (AD16) with a lightweight Haiku call so the
    dialog loads in <1s without touching the iterate queue.
    """
    _require_feature_enabled()
    workspace_id = company.company_id
    proto = get_prototype(prototype_id=prototype_id, workspace_id=workspace_id)
    if proto is None:
        raise HTTPException(status_code=404, detail="Prototype not found")
    client = get_design_agent_client()
    msg = client.messages.create(
        model="claude-haiku-4-5-20251001",
        max_tokens=200,
        messages=[{
            "role": "user",
            "content": (
                f'You are reviewing a design feedback comment about to be applied to a UI prototype.\n'
                f'Comment: "{body.comment_body}"\n'
                f'Ask exactly ONE brief, specific clarifying question to understand the designer\'s intent before applying this change. '
                f'Be concise (one sentence max). Do not explain yourself, just ask the question.'
            ),
        }],
    )
    question = msg.content[0].text.strip()
    return ClarifyCommentResponse(question=question)


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
# `advance_current_checkpoint` helper (F7: stable URL, no share_token rotation),
# called at the tail of `_stage_iterate_run`.
#
# Mode (P3-05 → P3-07): EXECUTE is the default; `mode='plan'` is now FULLY wired
# (P3-07) — the plan/execute tool split + the distinct plan system prompt + the
# Plan→Execute transition (`POST /{id}/iterate/confirm-plan`) all land here.
# Concurrency / queueing is P3-06 (the queue serialises plan + execute runs alike).
from app.design_agent.prompts import (
    DESIGN_AGENT_ITERATE_SYSTEM,
    DESIGN_AGENT_PLAN_SYSTEM,
    render_iterate_user,
)
from app.design_agent.runner import (
    drain_iteration_queue,
    estimate_iterate_cost,
    iterate_prototype,
)
from app.design_agent.rate_limit import ITERATE_LIMITER
from app.design_agent.storage import read_source_files_for_checkpoint
from app.db.prototype_pending_iterations import (
    QueueFullError,
    enqueue_iteration,
    queue_position,
    set_iteration_plan,
)


class IterateRequest(BaseModel):
    prompt: str = Field(..., min_length=1, max_length=8000)
    applied_comment_id: int | None = None        # F10: set when Apply pre-filled the prompt
    mode: Literal["plan", "execute"] = "execute"  # P3-07 implements 'plan'; P3-05 runs 'execute'


class IterateResponse(BaseModel):
    prototype_id: int
    status: str                                   # 'generating' (kicked off in the bg)
    queue_position: int                           # P3-06: derived slot in the iterate queue


@router.post(
    "/{prototype_id}/iterate",
    response_model=IterateResponse,
    dependencies=[Depends(require_same_origin)],  # P5-06 CSRF/Origin gate (authed mutating)
)
async def post_iterate(
    prototype_id: int,
    body: IterateRequest,
    company: CompanyContext = Depends(require_company),
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
    workspace_id = company.company_id
    proto = get_prototype(prototype_id=prototype_id, workspace_id=workspace_id)
    if not proto:
        raise HTTPException(status_code=404, detail="Prototype not found")
    if proto.get("is_complete"):
        raise HTTPException(status_code=409, detail="Prototype is locked; Resume Iteration first")
    if proto.get("status") != "ready":
        raise HTTPException(status_code=409, detail="Prototype not ready to iterate")

    # P5-04 (AD15 spend control): per-prototype iterate rate limit — 6 calls/hr.
    # Mounted AFTER the feature/session/workspace/lock/ready gates (so a feature-off
    # or cross-tenant request gets its 404/401 first and the limiter never leaks
    # existence) and BEFORE enqueue_iteration (enqueue is the spend-meaningful action;
    # a rate-limited call must not consume a queue slot). Distinct from the queue_full
    # 429 below — that one fires when a slot can't be granted; this one fires before a
    # slot is even requested. Estimate/confirm-plan are intentionally NOT limited here.
    key = str(prototype_id)
    if not ITERATE_LIMITER.check(key):
        retry_after = ITERATE_LIMITER.retry_after(key)
        logger.info(
            "iterate_rate_limited prototype_id=%s retry_after_seconds=%s",
            prototype_id, retry_after,
        )
        raise HTTPException(
            status_code=429,
            detail={"error": "rate_limit", "retry_after_seconds": retry_after},
        )
    ITERATE_LIMITER.register(key)

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


class EstimateRequest(BaseModel):
    prompt: str = Field(..., min_length=1, max_length=8000)
    applied_comment_id: int | None = None


@router.post(
    "/{prototype_id}/iterate/estimate",
    dependencies=[Depends(require_same_origin)],  # P5-06 CSRF/Origin gate (authed POST)
)
async def post_iterate_estimate(
    prototype_id: int,
    body: EstimateRequest,
    company: CompanyContext = Depends(require_company),
) -> dict:
    """Pre-flight cost estimate (AD14): return the token + dollar estimate + soft-cap
    warning the CostEstimateModal renders BEFORE an iterate run. Deterministic, no
    Anthropic call in the request path — cancelling the modal costs nothing.

    Gates mirror POST /iterate exactly: feature-flag (404 when off) + require_app_session
    (401) + workspace filter (404 cross-tenant). Empty prompt → 422 (min_length=1).

    Route placement: this is a POST on a 3-segment path (`/{id}/iterate/estimate`); the
    `GET /{prototype_id}` catch-all cannot shadow it (different method AND more segments),
    matching the existing `POST /{id}/iterate` + `/iterate/confirm-plan` siblings.

    Observability (Rule #24): logs identifiers + token counts only — never the prompt or
    bundle content.
    """
    _require_feature_enabled()
    workspace_id = company.company_id
    proto = get_prototype(prototype_id=prototype_id, workspace_id=workspace_id)
    if not proto:
        raise HTTPException(status_code=404, detail="Prototype not found")
    # estimate_iterate_cost is async (it awaits read_source_files_for_checkpoint, S2).
    estimate = await estimate_iterate_cost(
        prototype_id=prototype_id,
        workspace_id=workspace_id,
        prompt=body.prompt,
        applied_comment_id=body.applied_comment_id,
    )
    logger.info(
        "prototype_iterate_estimate prototype_id=%s cached_input_tokens=%s "
        "new_input_tokens=%s est_cost_usd=%s exceeds_soft_cap=%s",
        prototype_id,
        estimate["cached_input_tokens"],
        estimate["new_input_tokens"],
        estimate["est_cost_usd"],
        estimate["exceeds_soft_cap"],
    )
    return estimate


class ConfirmPlanRequest(BaseModel):
    """Plan->Execute transition body (P3-07, AD10). The team reviewed the plan a
    `mode='plan'` run emitted and approved (or refined) it; `plan` carries the
    approved text back, `prompt` is the iterate request the plan was for."""
    prompt: str = Field(..., min_length=1, max_length=8000)
    plan: str = Field(..., min_length=1, max_length=8000)
    applied_comment_id: int | None = None


@router.post(
    "/{prototype_id}/iterate/confirm-plan",
    response_model=IterateResponse,
    dependencies=[Depends(require_same_origin)],  # P5-06 CSRF/Origin gate (authed mutating)
)
async def post_confirm_plan(
    prototype_id: int,
    body: ConfirmPlanRequest,
    company: CompanyContext = Depends(require_company),
) -> IterateResponse:
    """Plan->Execute transition (P3-07, AD10): run the approved plan in EXECUTE mode.

    Same gates/posture as POST /iterate (feature-flag 404, require_app_session 401,
    workspace 404, locked/not-ready 409, full-queue 429). Enqueues an EXECUTE
    iteration carrying the approved plan on the queue row's `plan` column; the drain
    prepends it to the run's system blocks as an addendum (`approved_plan`). Returns
    in <200ms — no Anthropic call in the request path.
    """
    _require_feature_enabled()
    workspace_id = company.company_id
    proto = get_prototype(prototype_id=prototype_id, workspace_id=workspace_id)
    if not proto:
        raise HTTPException(status_code=404, detail="Prototype not found")
    if proto.get("is_complete"):
        raise HTTPException(status_code=409, detail="Prototype is locked; Resume Iteration first")
    if proto.get("status") != "ready":
        raise HTTPException(status_code=409, detail="Prototype not ready to iterate")

    try:
        row = enqueue_iteration(
            prototype_id=prototype_id,
            workspace_id=workspace_id,
            prompt=body.prompt,
            applied_comment_id=body.applied_comment_id,
            mode="execute",          # the confirm always runs in EXECUTE mode
            plan=body.plan,          # approved plan -> prepended as a system addendum
        )
    except QueueFullError:
        raise HTTPException(status_code=429, detail={"error": "queue_full", "max": 5})

    logger.info("prototype_plan_confirmed prototype_id=%s iteration_id=%s", prototype_id, row["id"])
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
    approved_plan: str | None = None,
    iteration_id: int | None = None,
) -> None:
    """Background iterate run: load the current bundle + open comments, render the
    iterate prompts (cache-disciplined), run the agent loop, then stage the result
    via the iterate path (NOT the first-completion path).

    Source load (S2): `get_prototype` FIRST to obtain `current_checkpoint_id`, then
    `read_source_files_for_checkpoint(prototype_id, current_checkpoint_id)`
    (P2-04 — positional args, async, storage-path read, NOT workspace-filtered) to
    pre-fill the agent's virtual_fs. On any exception the row is marked failed in
    the existing Sprntly error format; the prior bundle_url is preserved.

    PLAN vs EXECUTE (P3-07, AD10), keyed on `body.mode`:
      - 'plan'    : uses DESIGN_AGENT_PLAN_SYSTEM + the plan tool registry (no
                    write/line_replace). On completion the emitted textual plan is
                    persisted to the queue row (`set_iteration_plan`, needs
                    `iteration_id`) and the run stages NOTHING — a plan builds no
                    bundle and advances no checkpoint (AC6). A plan run NEVER fails
                    the prototype row: the bundle is untouched.
      - 'execute' : the existing path. `approved_plan` (set by the confirm-plan
                    transition) is prepended to the system blocks as an addendum.
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
        # PLAN mode swaps in the distinct plan/discuss system prompt (AD10); the
        # explore-only tool registry is selected downstream by mode in agent_loop.
        system_text = (
            DESIGN_AGENT_PLAN_SYSTEM if body.mode == "plan" else DESIGN_AGENT_ITERATE_SYSTEM
        )
        system_blocks = [{
            "type": "text",
            "text": system_text,
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
            # Tool-partition mode (AD17 / P3-07): canonical 'plan' or 'execute',
            # never 'iterate'. agent_loop -> tools_for_mode selects the registry.
            mode=body.mode,
            # Plan->Execute transition: the approved plan (if any) is prepended to
            # the system blocks as an addendum inside iterate_prototype.
            approved_plan=approved_plan,
        )

        # PLAN mode (AD10): persist the emitted plan, stage NOTHING (no checkpoint,
        # no bundle — a plan builds nothing, AC6). A plan run never fails the
        # prototype row; the bundle is untouched regardless of run status.
        if body.mode == "plan":
            if result.status == "complete":
                plan_text = _extract_plan_text(result.final_content)
                if iteration_id is not None and plan_text:
                    set_iteration_plan(
                        iteration_id=iteration_id,
                        workspace_id=workspace_id,
                        plan=plan_text,
                    )
                logger.info(
                    "prototype_plan_run_complete prototype_id=%s iteration_id=%s plan_chars=%s",
                    prototype_id, iteration_id, len(plan_text),
                )
            else:
                logger.warning(
                    "prototype_plan_run_incomplete prototype_id=%s iteration_id=%s status=%s",
                    prototype_id, iteration_id, result.status,
                )
            return

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
        elif result.status == "awaiting_clarification":
            # F12 (P4-08): a clarifying_question terminal-PAUSE is NOT a failure.
            # The runner already persisted the question on `pending_question`
            # (P3-08); leave the row in a clean PAUSED state (status='ready',
            # pending_question set, no completed_at, no error) so the P3-16
            # answer-resume iterate is NOT 409-blocked by `post_iterate`'s
            # `status != 'ready'` guard. Do NOT fail_prototype — that flip is
            # exactly the bug this ticket fixes. (Iterate path only; the
            # generate-time pause is scoped out — see P4-08 Open question.)
            mark_awaiting_clarification(
                prototype_id=prototype_id,
                workspace_id=workspace_id,
            )
            logger.info(
                "prototype_iterate_paused_awaiting_clarification prototype_id=%s",
                prototype_id,
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
        # P3-07: the queue row's `plan` column is the APPROVED plan for a confirm
        # row (prepended as a system addendum in execute mode) AND the write target
        # for a plan-mode run's emitted plan. `iteration_id` lets the plan branch
        # persist back to this row. `.get` tolerates pre-migration schemas (None).
        approved_plan=row.get("plan"),
        iteration_id=row.get("id"),
    )


def _extract_plan_text(final_content: list[dict[str, Any]]) -> str:
    """Concatenate the text blocks of a plan run's final assistant turn (P3-07).

    The plan IS the final assistant message's text (plan mode ends its turn with
    the plan and no tool calls). Non-text blocks (there should be none on a clean
    plan turn) are ignored. Returns a stripped string; empty when the turn had no
    text (an abnormal plan run — the caller logs and skips persistence)."""
    parts = [
        b.get("text", "")
        for b in (final_content or [])
        if isinstance(b, dict) and b.get("type") == "text"
    ]
    return "\n".join(p for p in parts if p).strip()


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
    except (ViteBuildError, FileNotFoundError, TypeCheckError) as exc:
        # P3-15 cross-ticket seam: the type-check runs inside the shared
        # _vite_build_sync, so it fires on the ITERATE build too. A runtime-broken
        # iterate must fail the iterate (route to fail_prototype), not silently
        # stage — mirror _stage_complete_run's widened tuple.
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

    # Step 4 — P3-12. Advance current_checkpoint_id + bundle_url WITHOUT a
    # completed_at re-stamp (NOT complete_prototype — AC6a). F7: this does not
    # rotate share_token / share_mode, so the public /p/<token> URL is unchanged
    # and now resolves to the new checkpoint's bundle_url.
    advance_current_checkpoint(
        prototype_id=prototype_id,
        workspace_id=workspace_id,
        checkpoint_id=checkpoint_id,
        bundle_url=bundle_url,
    )


# ─── PRD patches: accept / reject (P3-10, F11) ──────────────────────────────────
#
# Accept/reject resolve a PENDING `prd_patches` proposal (P3-09). The companion
# LIST route (`GET /prd-patches`) + the `PrdPatchOut` model + `_patch_to_out` + the
# `prd_patches` import are declared ABOVE the `GET /{prototype_id}` catch-all (see
# the "PRD patches: list pending" block there) — the list path is a SINGLE segment
# and would otherwise be swallowed by the catch-all (FastAPI static-before-dynamic).
# These two POSTs are 3-segment (`/prd-patches/{id}/accept|reject`), unambiguous
# against the single-segment catch-all, so they stay at EOF and reuse the
# module-level `PrdPatchOut` / `_patch_to_out` / `mark_patch_*` symbols defined in
# that block. Same gate posture as the authed routes above: feature-flag 404 when
# off + require_app_session 401 + workspace 404 (cross-tenant invisibility, Rule
# #22). Sync handlers (mirrors get_one): FastAPI runs them in the threadpool.


@router.post(
    "/prd-patches/{patch_id}/accept",
    response_model=PrdPatchOut,
    dependencies=[Depends(require_same_origin)],  # P5-06 CSRF/Origin gate (authed mutating)
)
def post_accept_patch(
    patch_id: int,
    company: CompanyContext = Depends(require_company),
) -> PrdPatchOut:
    """Accept a proposed PRD patch: flip its status to `applied` (P3-09
    `mark_patch_applied`) and return the updated row. The rendered PRD reflects the
    applied patch on its NEXT load (read path folds it in via
    `apply_patches_to_prd_md`); this route does NOT mutate `prds.payload_md` or the
    PrdScreen `contentEditable`. 404 when the patch is not in the caller's
    workspace (cross-tenant invisibility, Rule #22). Idempotent: re-accepting an
    already-applied patch is a no-op flip that returns the row."""
    _require_feature_enabled()
    workspace_id = company.company_id
    row = mark_patch_applied(patch_id=patch_id, workspace_id=workspace_id)
    if not row:
        raise HTTPException(status_code=404, detail="Patch not found")
    # Route-level state-transition log (Rule #24 / AC12): identifiers only — never
    # patch_md / rationale (they can embed PRD body). Logged on the route's own
    # logger so the observability AC is satisfied at this surface.
    logger.info("prd_patch_applied patch_id=%s", patch_id)
    return PrdPatchOut(**_patch_to_out(row))


@router.post(
    "/prd-patches/{patch_id}/reject",
    response_model=PrdPatchOut,
    dependencies=[Depends(require_same_origin)],  # P5-06 CSRF/Origin gate (authed mutating)
)
def post_reject_patch(
    patch_id: int,
    company: CompanyContext = Depends(require_company),
) -> PrdPatchOut:
    """Reject a proposed PRD patch: flip its status to `rejected` (P3-09
    `mark_patch_rejected`) and return the updated row. The PRD is unaffected
    (rejected patches are excluded by `apply_patches_to_prd_md`). 404 when not in
    the caller's workspace. Idempotent (mirrors accept)."""
    _require_feature_enabled()
    workspace_id = company.company_id
    row = mark_patch_rejected(patch_id=patch_id, workspace_id=workspace_id)
    if not row:
        raise HTTPException(status_code=404, detail="Patch not found")
    # Identifiers only — never patch_md / rationale (Rule #24 / AC12).
    logger.info("prd_patch_rejected patch_id=%s", patch_id)
    return PrdPatchOut(**_patch_to_out(row))


# ─── F13 manual edit: commit-back (P4-02, AD23) ─────────────────────────────────
#
# The commit-back half of F13: when the user clicks "Save edits" in the
# ManualEditOverlay (P4-01), the accumulated `{anchor_id, property, old_value,
# new_value}` triples are POSTed here. Per AD23 the visual change was ALREADY
# applied client-side (no LLM); the LLM is invoked ONLY to COMMIT the change into
# the prototype's SOURCE (not to compute it) — exactly once per Save.
#
# ROUTE PLACEMENT (route-ordering catch-all): `POST /{id}/manual-edit` is a POST on
# a 2-segment path. The `GET /{prototype_id}` catch-all (line ~268) is a GET on a
# 1-segment path — it CANNOT shadow this (different METHOD and more segments),
# exactly like the existing `POST /{id}/complete`, `/{id}/iterate`, `/{id}/share`
# siblings declared after the catch-all. So this stays at EOF with the other
# non-shadowable POSTs (mirrors the `/prd-patches/{id}/accept|reject` rationale
# above) rather than being hoisted above the catch-all (which is reserved for NEW
# single-segment routes like `GET /prd-patches`). Reachability is VERIFIED by a
# real request in test_design_agent_manual_edit.py (asserts the route is hit, not
# 422-shadowed by the int-coerced catch-all).
#
# QUEUE DECISION (AD23): manual edit does NOT go through the P3-06 iterate queue —
# it is a distinct, small, 2-iter operation. It runs as a single fire-and-forget bg
# task (held in `_inflight_tasks`, strong-ref discipline mirroring post_iterate).
# `queue_position` is always 0 in the response (kept in the shape for client parity
# with iterate). A manual edit that collides with an in-flight iterate is
# last-write-wins on `current_checkpoint_id` via advance_current_checkpoint
# (acceptable for MVP).
#
# Localized imports (mirror the P3-05 block near _run_iterate_bg): the manual-edit
# runner entrypoint + prompt symbols. _stage_iterate_run / read_source_files_for_checkpoint
# / fail_prototype / get_prototype / infer_scenario_from_inputs are already in module scope.
from app.design_agent.prompts import (
    DESIGN_AGENT_MANUAL_EDIT_SYSTEM,
    render_manual_edit_user,
)
from app.design_agent.runner import manual_edit_prototype


class ManualEditTriple(BaseModel):
    """One fixed-property visual edit (P4-01 ManualEditTriple wire-shape). `old_value`
    is the pristine value at first selection; `new_value` is the value at Save. The
    closed `property` set matches P4-01's EditableProperty exactly."""
    anchor_id: str = Field(..., min_length=1)
    property: Literal["text", "font-size", "padding", "color", "background"]
    old_value: str
    new_value: str


class ManualEditRequest(BaseModel):
    # min_length=1 → 422 on empty edits; max_length=50 → a manual session is small.
    edits: list[ManualEditTriple] = Field(..., min_length=1, max_length=50)


class ManualEditResponse(BaseModel):
    prototype_id: int
    status: str            # 'generating' (kicked off in the bg)
    queue_position: int    # always 0 — manual edit does not use the iterate queue


@router.post(
    "/{prototype_id}/manual-edit",
    response_model=ManualEditResponse,
    dependencies=[Depends(require_same_origin)],  # P5-06 CSRF/Origin gate (authed mutating)
)
async def post_manual_edit(
    prototype_id: int,
    body: ManualEditRequest,
    company: CompanyContext = Depends(require_company),
) -> ManualEditResponse:
    """F13/AD23: commit a batch of manual visual edits into the prototype source.

    Gates (identical posture to POST /iterate): feature-flag (404 when off) +
    require_app_session (401) + workspace filter (404 cross-tenant). Two 409s:
      - `is_complete` (locked, F14): cannot edit until Resume Iteration.
      - `status != 'ready'`: cannot edit a generating/failed/invalidated row.
    On success fires `_run_manual_edit_bg` as a single bg task (NOT the iterate
    queue) and returns status='generating', queue_position=0 in <200ms. No
    Anthropic call in the request path (AC1/AC4 — the LLM runs once, in the bg).
    """
    _require_feature_enabled()
    workspace_id = company.company_id
    proto = get_prototype(prototype_id=prototype_id, workspace_id=workspace_id)
    if not proto:
        raise HTTPException(status_code=404, detail="Prototype not found")
    if proto.get("is_complete"):
        raise HTTPException(status_code=409, detail="Prototype is locked; Resume Iteration first")
    if proto.get("status") != "ready":
        raise HTTPException(status_code=409, detail="Prototype not ready to edit")

    # Observability (Rule #24 / AC14): identifiers only — never the edit triples
    # (old/new values can embed user-facing copy).
    logger.info("prototype_manual_edit_started prototype_id=%s", prototype_id)
    task = asyncio.create_task(
        _run_manual_edit_bg(
            prototype_id=prototype_id,
            workspace_id=workspace_id,
            body=body,
        )
    )
    _inflight_tasks.add(task)
    task.add_done_callback(_inflight_tasks.discard)
    return ManualEditResponse(
        prototype_id=prototype_id,
        status="generating",
        queue_position=0,
    )


async def _run_manual_edit_bg(
    *,
    prototype_id: int,
    workspace_id: str,
    body: ManualEditRequest,
) -> None:
    """Background manual-edit run (AD23): load the current bundle source, render the
    commit-only prompts (cache-disciplined), run the thin 2-iter agent loop, then
    stage the result via the iterate path (a manual edit is a checkpoint ADVANCE,
    not a first completion — reuses `_stage_iterate_run` verbatim).

    STALE-ANCHOR (fail-closed, AC10): the run is NOT pre-validated for anchor
    presence (the anchors live in the BUILT dist, not the staged `_source/` TSX).
    The DESIGN_AGENT_MANUAL_EDIT_SYSTEM prompt instructs the agent to `search` the
    source for each triple's element and, when it cannot resolve one, to end its
    turn WITHOUT editing. We detect that no-target outcome as "the run ended but the
    source is byte-identical to the seed" → `fail_prototype` with a loud
    `manual_edit: anchor … not found` error and NO checkpoint advance. P4-01
    surfaces the error toast from `status='failed'` + the error on the next poll.
    Do NOT silently succeed on a missing anchor.
    """
    try:
        proto = get_prototype(prototype_id=prototype_id, workspace_id=workspace_id)
        if not proto:
            return  # row vanished under us; nothing to edit.

        checkpoint_id = proto.get("current_checkpoint_id")
        current_source = (
            await read_source_files_for_checkpoint(prototype_id, checkpoint_id)
            if checkpoint_id else {}
        )

        cacheable_blocks, volatile_block = render_manual_edit_user(
            current_source=current_source,
            edits=[e.model_dump() for e in body.edits],
        )
        # System block cached at the END of the stable prefix (AD2), mirroring
        # _run_iterate_bg. The source user prefix is cached too (its last block
        # carries cache_control); the volatile edit-triple block does not.
        system_blocks = [{
            "type": "text",
            "text": DESIGN_AGENT_MANUAL_EDIT_SYSTEM,
            "cache_control": {"type": "ephemeral", "ttl": "1h"},
        }]
        user_message = {"role": "user", "content": [*cacheable_blocks, volatile_block]}

        figma_file_key = proto.get("figma_file_key")
        scenario_set = infer_scenario_from_inputs(
            figma_file_key=figma_file_key,
            website_url=proto.get("website_url"),
            github_installation_id=proto.get("github_installation_id"),
            prd_references_codebase=False,
        )
        scenario_label = ",".join(sorted(scenario_set))

        result, virtual_fs = await manual_edit_prototype(
            prototype_id=prototype_id,
            workspace_id=workspace_id,
            system_blocks=system_blocks,
            user_message=user_message,
            current_source=current_source,
            figma_file_key=figma_file_key,
            scenario=scenario_label,
        )

        source_changed = virtual_fs != current_source
        if result.status == "complete" and virtual_fs and source_changed:
            # Reuse the iterate staging path verbatim — a manual edit is a checkpoint
            # ADVANCE (no complete_prototype, no completed_at re-stamp; F7 stable URL).
            await _stage_iterate_run(
                prototype_id=prototype_id,
                workspace_id=workspace_id,
                virtual_fs=virtual_fs,
                iterate_prompt="<manual edit>",
            )
        elif result.status == "complete":
            # AD23 stale-anchor fail-closed: the run ended but committed NO source
            # change → the agent could not resolve a triple's target element. Record
            # a loud error and do NOT advance the checkpoint.
            anchors = ", ".join(e.anchor_id for e in body.edits)
            fail_prototype(
                prototype_id=prototype_id,
                workspace_id=workspace_id,
                error=(
                    f"manual_edit: anchor(s) {anchors} not found in current bundle; "
                    f"no source change committed"
                ),
            )
        else:
            # Mirror _run_iterate_bg's structured failure: surface the RunResult
            # error_message / error_class so an Anthropic failure (or the 2-iter
            # max_iters cap with no committed change) is triageable.
            error_parts = [
                f"manual_edit agent_loop ended with status={result.status} iters={result.iters}"
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
        # error_class only in the structured log (Rule #24 — no source / edit
        # content); the full message goes to the row's error column.
        logger.warning(
            "design_agent.manual_edit_failed prototype_id=%s error_class=%s",
            prototype_id, type(exc).__name__,
        )
        fail_prototype(
            prototype_id=prototype_id,
            workspace_id=workspace_id,
            error=f"{type(exc).__name__}: {exc}",
        )


# ── SSE event stream (two-segment GET, order-independent vs the catch-all) ───
# Bearer auth via query param because EventSource cannot set headers. The token
# is validated through require_company_from_query — same decode + company-
# resolution path as require_company, identical trust. Never logged.
# Nginx buffering disabled via X-Accel-Buffering so events reach the client
# immediately rather than accumulating until the connection closes.

@router.get("/{prototype_id}/events")
async def stream_prototype_events(
    prototype_id: int,
    company: CompanyContext = Depends(require_company_from_query),
    _flag: None = Depends(_require_feature_enabled),
) -> StreamingResponse:
    workspace_id = company.company_id
    # Workspace-scoped existence check before opening the stream. Returns 404
    # (not 401, not 403) on cross-tenant or missing prototype — the same
    # invisibility posture as GET /{prototype_id}.
    if get_prototype(prototype_id=prototype_id, workspace_id=workspace_id) is None:
        raise HTTPException(404, "Prototype not found")

    logger.info(
        "design_agent.events_connect prototype_id=%s workspace_id=%s",
        prototype_id,
        workspace_id,
    )

    async def _gen():
        try:
            async for event in _sse_subscribe(prototype_id):
                yield f"data: {json.dumps(event)}\n\n"
        finally:
            logger.info(
                "design_agent.events_disconnect prototype_id=%s workspace_id=%s",
                prototype_id,
                workspace_id,
            )

    return StreamingResponse(
        _gen(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
        },
    )
