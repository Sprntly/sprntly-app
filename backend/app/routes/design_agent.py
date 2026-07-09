"""Design Agent HTTP routes.

Wires the `generate_prototype` agent loop, the scaffold prompts, and the
`prototypes` DB helpers into the FastAPI surface:

    POST /v1/design-agent/generate  {prd_id, target_platform, instructions, figma_file_key}
    GET  /v1/design-agent/{id}

POST /generate returns within 200ms — it inserts a `generating` row, fires the
agent loop in a background task, and returns the prototype_id immediately (no
Anthropic call in the request path). Routes are isolated under
`APIRouter(prefix="/v1/design-agent")`.
Feature-flag-gated — both endpoints return 404 when `DESIGN_AGENT_ENABLED` is
unset / "0" / "false", so the feature is invisible until it is flipped on.
Workspace-isolated — `workspace_id` is read from the session `aud` claim at
insert time and every user-driven query filters by it.

CALL-STYLE NOTE: the `db.prototypes` helpers are *synchronous* (supabase-py is
sync; this mirrors `db/prds.py` + `routes/prd.py` exactly). They are called
WITHOUT `await`, directly from the async handler — the same pattern
`routes/prd.py` uses for `start_prd` / `find_existing_prd`. The only awaited
call in this module is `generate_prototype` (genuinely async), which runs off
the request path inside the background task.

SCOPE (what this initial slice does NOT do):
- Bundle staging to storage + `complete_prototype(bundle_url=...)` — wired later
  (`_run_generation_bg` → `_stage_complete_run`: vite_build → checkpoint →
  stage_bundle → complete_prototype on the success path).
- CSRF / Origin check — added later (matches Sprntly's existing routes, which
  have no CSRF defense; design-agent inherits the gap until it is hardened).
- Per-session rate limiter — added later.
- `POST /complete | /resume | /share | /export | /iterate | /manual-edit` —
  appended to this file in later phases.
"""
from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import os
import time
import uuid
from typing import Any, Literal
from urllib.parse import urlsplit

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import Response, StreamingResponse
from pydantic import BaseModel, Field, field_validator

from app.auth import CompanyContext, require_company, require_company_from_query  # company-scoped auth dep
from app.config import settings
from app.design_agent.csrf import require_same_origin  # server-side CSRF/Origin gate
from app.design_agent.rate_limit import (  # public-surface rate limits
    PUBLIC_COMMENT_LIMITER,  # consumed by the public comment write route (design_agent_comments)
    PUBLIC_TOKEN_LIMITER,
)
from app.db.companies import display_name_for_company_id, slug_for_company_id
from app.db.design_agent_jobs import (  # Tier 2 opt-in worker queue
    enqueue_job,
    worker_heartbeat_fresh,
)
from app.db.prds import get_prd_rendered, list_prds_by_brief, reset_prd_to_draft
from app.db.github import find_github_installation_for_repo
from app.db.products import get_company_website  # onboarding-website fallback source
from app.db.prototype_exports import find_prototype_export
from app.db.prototypes import (
    advance_current_checkpoint,
    complete_prototype,
    create_checkpoint,
    delete_prototype,
    fail_prototype,
    find_existing_prototype,
    find_prototype_by_prd,
    find_prototype_by_share_token,
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
    set_grounding_note,
    set_share_config,
    start_prototype,
    verify_share_passcode,
    clear_pending_question,
)
from app.db.prototype_comments import list_comments  # iterate grounding reads open threads
from app.db.usage_events import finalize_usage_event, start_usage_event
from app.llm_telemetry import RunUsage
from app.design_agent.client import get_design_agent_client
from app.design_agent.prompts import (
    DESIGN_AGENT_SCAFFOLD_SYSTEM,
    DESIGN_AGENT_TEMPLATE_VERSION,
    render_scaffold_user,
)
from app.design_agent.event_stream import publish_step, subscribe as _sse_subscribe
from app.design_agent.progress import FINISHING_STEP, VITE_PHASE_STEP
from app.design_agent.runner import MODEL, generate_prototype, reconcile_comments_on_checkpoint, repair_build_run
from app.design_agent.screenshot import capture_bundle_screenshot  # best-effort preview capture
from app.design_agent.url_slug import url_slugify  # cosmetic /p/<company>/<feature>/<token> segments
from app.design_agent.codebase_map.recreate import (
    ThemeExpectations,
    _assert_structural_parity,
    assert_containment,
    assert_theme_landed,
    derive_interactive_scope,
)
from app.design_agent.storage import (
    PlaceholderShippedError,
    ThemeBridgeError,
    TypeCheckError,
    TypeCheckRepairExhausted,
    ViteBuildError,
    assert_mounts_generated_content,
    authed_bundle_url,
    fresh_bundle_url,
    public_bundle_proxy_url,
    repair_unresolved_relative_imports,
    stage_bundle,
    stage_preview_image,
    vite_build,
    vite_build_with_repair,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/v1/design-agent", tags=["design-agent"])

_VARIANT = "v1"

# PRD_VARIANT is the canonical storage variant for the prd-author agent (v2,
# as set in prd_runner.PRD_VARIANT). Imported here so GET /brief-prototype-map
# filters prds by the same variant the PRD generation pipeline writes. Lazy
# import avoids circular-import risk at module parse time; the value is stable
# across requests so the import cost is negligible.
def _prd_variant() -> str:
    from app.prd_runner import PRD_VARIANT as _PV
    return _PV

# Strong refs to in-flight background generation tasks. asyncio only holds a
# weak reference to a bare `create_task` result, so without this the task can be
# garbage-collected mid-run. The done-callback discards each task on completion.
# The fuller in-flight discipline (cancellation, draining on shutdown) lands
# later; this is the minimum to keep the task alive.
_inflight_tasks: set[asyncio.Task] = set()

# Separate, generation-only registry keyed by prototype_id, used ONLY by the
# cancel endpoint to look up the in-flight generation task for a given prototype
# and best-effort cancel it. Deliberately NOT the shared `_inflight_tasks` set:
# that set is shared across five task types (generate/iterate/drain/manual-edit/
# locate) and the SIGTERM drain iterates it as a set of Tasks, so re-keying it to
# a dict would break draining and collide prototype_ids across task types. The
# generate task registers in BOTH (the set keeps the drain strong-ref; this dict
# powers cancel lookup); the done-callback removes from both. When the task isn't
# in this process (multi-worker), the lookup misses and cancel is a no-op — the
# endpoint's unconditional DB cleanup still leaves a correct user-facing state.
_inflight_generation_tasks: dict[int, asyncio.Task] = {}


# ── Tier 0: graceful drain ───────────────────────────────────────────────────
# Set True by the lifespan teardown (via request_shutdown()) when the process is
# draining on SIGTERM. While True, POST /generate rejects NEW in-process work
# with 503 rather than starting a task that a pending SIGKILL would abandon
# mid-run (the deploy-time 502 class). The frontend retry treats a 503 here
# as the self-heal signal: the next boot accepts the retried request cleanly.
_shutting_down = False


def request_shutdown() -> None:
    """Mark the process as draining so /generate stops admitting new work.

    Called from the lifespan teardown in app/main.py (after `yield`). Idempotent;
    safe to call more than once.
    """
    global _shutting_down
    _shutting_down = True


async def drain_inflight(deadline_seconds: float) -> None:
    """Wait up to ``deadline_seconds`` for in-flight generation tasks to finish.

    Called from the lifespan teardown after request_shutdown(). Does NOT cancel
    on timeout: the heavy section runs a vite subprocess on an uncancellable
    worker thread (asyncio.to_thread), so a cancel would not stop it and would
    only corrupt the partial bundle. Instead, any still-running prototype is
    logged by id and left in its 'generating' DB state; the startup
    invalidate_orphan_generating_prototypes() sweep demotes it to 'failed' on the
    next boot (system-wide, status-only — it handles a drain-timeout leftover
    with no extra checkpoint code). Never raises: a drain error must not block
    process shutdown.
    """
    global _shutting_down
    _shutting_down = True
    pending = {t for t in _inflight_tasks if not t.done()}
    if not pending:
        # asyncio.wait raises ValueError on an empty set — skip cleanly.
        return
    try:
        logger.info(
            "design_agent.drain_start pending=%d deadline_s=%s",
            len(pending), deadline_seconds,
        )
        _done, still_running = await asyncio.wait(
            pending, timeout=deadline_seconds
        )
        if still_running:
            # Deadline elapsed with work outstanding. Do NOT cancel (uncancellable
            # vite thread). Name the still-running tasks; the startup orphan sweep
            # recovers their 'generating' rows on next boot.
            logger.warning(
                "design_agent.drain_timeout still_running=%d tasks=%s — "
                "leaving 'generating' rows for the next-boot orphan sweep",
                len(still_running),
                [getattr(t, "get_name", lambda: "?")() for t in still_running],
            )
        else:
            logger.info("design_agent.drain_complete drained=%d", len(_done))
    except Exception:  # noqa: BLE001 — drain must never block shutdown
        logger.warning("design_agent.drain_error", exc_info=True)


# ── Tier 1: generation concurrency guard ─────────────────────────────────────
# Lazy-initialised so a test can monkeypatch the setting before first use and so
# the limit is read at CALL-TIME, not frozen at import (the import-bound-settings
# gotcha). One semaphore per process; the limit comes from
# settings.design_agent_generation_concurrency (default 1).
_generation_semaphore: asyncio.Semaphore | None = None


def _get_generation_semaphore() -> asyncio.Semaphore:
    """Return the process-wide generation semaphore, creating it on first use.

    The limit is read from settings AT CALL-TIME (first acquire), so a test that
    sets DESIGN_AGENT_GENERATION_CONCURRENCY (and reloads config) before the
    first generation gets the configured limit. Values <= 0 fall back to 1 (a 0
    permit would deadlock every generation).
    """
    global _generation_semaphore
    if _generation_semaphore is None:
        # Defensive getattr: a stale/reloaded Settings singleton from another
        # test's importlib.reload can lack this field (in-suite reload pollution);
        # fall back to the default 1 rather than AttributeError. In prod the field
        # always exists, so this reads the real configured value. Mirrors the
        # getattr-defensive pattern used for theme_expectations below.
        limit = getattr(settings, "design_agent_generation_concurrency", 1)
        if limit <= 0:
            limit = 1
        _generation_semaphore = asyncio.Semaphore(limit)
    return _generation_semaphore


def _feature_enabled() -> bool:
    """Read DESIGN_AGENT_ENABLED at REQUEST TIME (never import time).

    Default-off; never default-1 in any commit.
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


# ── Tier 2: opt-in worker queue ──────────────────────────────────────────────
# When ON *and* a worker heartbeat is fresh, /generate enqueues the generation
# onto `design_agent_jobs` for `python -m app.worker` to run off the API request
# process. OTHERWISE — flag off, no fresh heartbeat, or any enqueue/table error —
# /generate falls back to today's in-process create_task path verbatim. The
# fallback is the load-bearing safety: a box without the worker systemd unit
# behaves exactly as today. Read at REQUEST TIME (os.environ, mirroring
# _feature_enabled) so a flip needs no deploy and survives module reload in tests.
_WORKER_HEARTBEAT_FRESH_SECONDS = 30


def _worker_enabled() -> bool:
    """Read DESIGN_AGENT_WORKER_ENABLED at REQUEST TIME (never import time)."""
    val = (os.environ.get("DESIGN_AGENT_WORKER_ENABLED") or "").strip().lower()
    return val in {"1", "true", "yes"}


def _serialize_generation_payload(kwargs: dict) -> dict:
    """Make the `_run_generation_bg` kwargs JSON-serializable for the job queue.

    Every value is already a scalar / str / int / None EXCEPT `manual_design`
    (a `ManualDesignInput` Pydantic model). We `model_dump()` it to a plain dict
    so the whole payload round-trips through jsonb losslessly. The worker calls
    `_deserialize_generation_payload` to reconstruct the model, so the inline
    path and the worker path invoke the IDENTICAL `_run_generation_bg` body with
    byte-for-byte identical inputs.
    """
    out = dict(kwargs)
    md = out.get("manual_design")
    if md is not None and hasattr(md, "model_dump"):
        out["manual_design"] = md.model_dump()
    return out


def _deserialize_generation_payload(payload: dict) -> dict:
    """Inverse of `_serialize_generation_payload`: reconstruct the
    `ManualDesignInput` model from its dict so the worker calls
    `_run_generation_bg(**kwargs)` with the same types the inline path uses."""
    out = dict(payload)
    md = out.get("manual_design")
    if isinstance(md, dict):
        out["manual_design"] = ManualDesignInput.model_validate(md)
    return out


def _resolve_github_installation_id_for_repo(
    company_id: str, repo_full_name: str | None
) -> int | None:
    """Best-effort company-scoped repo full_name -> GitHub App installation id.

    Resolves through the COMPANY's GitHub App installations, not the connecting
    user's personal OAuth token: GitHub is a company-shared connector, so any
    member's grounding must resolve as long as the company has an installation
    covering the repo. The installation token (minted from the App JWT, no 8h
    OAuth clock) is what every downstream repo-byte read already uses, so this
    keeps grounding alive for non-connecting members and after the connector's
    personal OAuth token expires. No covering installation -> None, and
    generation proceeds with no codebase grounding exactly as before.
    """
    if not repo_full_name:
        return None
    try:
        install = find_github_installation_for_repo(repo_full_name, company_id)
    except Exception:
        logger.info("design_agent.github_installation_resolve_failed")
        return None
    if not install:
        return None
    try:
        return int(install["installation_id"])
    except (KeyError, TypeError, ValueError):
        return None


# ─── Schemas ────────────────────────────────────────────────────────────────


class ManualDesignInput(BaseModel):
    """The absolute Scenario-B floor — a user-supplied brand color + font
    that styles the prototype even when there is no Figma and no extractable
    website design system. Both fields required when the object is present; the
    whole object is optional on the request."""

    primary_color: str = Field(..., min_length=1)   # e.g. "#3b82f6"
    font_family: str = Field(..., min_length=1)      # e.g. "Inter"


class GenerateRequest(BaseModel):
    prd_id: int = Field(..., gt=0)
    target_platform: str = Field("both")  # "desktop" | "mobile" | "both"
    instructions: str = Field("")
    figma_file_key: str | None = None     # explicit; auto-detection via the
    #                                       connector lookup lands in a later phase.
    figma_node_id: str | None = None      # optional frame-level node-id extracted
    #                                       from a pasted Figma URL (node-id query
    #                                       param, hyphen→colon converted client-side).
    #                                       When set, the fetch_figma tool targets this
    #                                       specific frame instead of the file's top-5.
    website_url: str | None = None        # Scenario B fallback source
    manual_design: ManualDesignInput | None = None  # absolute floor
    github_repo: str | None = None        # connected-repo full_name ("org/repo");
    #                                       no fetch, no clone, no agent tool. The repo
    #                                       identifier travels into the scaffold prompt
    #                                       and, when a matching GitHub App installation
    #                                       is known, into the design-system source
    #                                       resolver for future codebase extraction.
    design_source: Literal["figma", "github", "website"] | None = None
    #   Explicit single-source selector. figma → use figma_file_key; github →
    #   use github_repo; website → use the onboarding website (or a typed
    #   website_url). None = old client / no explicit choice → preserve the
    #   prior implicit precedence + always-on onboarding fallback unchanged.
    chosen_screen_route: str | None = None
    #   The route the PM confirmed in the locate UX (codebase generation only).
    #   When present alongside a resolved installation, the background task
    #   resolves it to a node on the snapshot map and feeds the recreate
    #   pre-seed branch of generate_prototype. None = blank-canvas path.
    chosen_screen_id: str | None = None
    #   The stable node id the PM confirmed in the locate UX (codebase generation
    #   only). This is the resolution key: a non-route host (the app shell, an
    #   in-page section) carries a non-route id and a possibly-empty/shared route,
    #   so id is what lets it survive to the recreate pre-seed. chosen_screen_route
    #   stays the human label + recreate pin / cache key. None = old client; the
    #   background task then falls back to resolving by route exactly as before.
    map_commit_sha: str | None = None
    #   The map snapshot the route was confirmed against. Pins build_map at
    #   read time so the recreate reads the same bytes the PM confirmed
    #   against, and lands a cache hit on the (installation_id, repo, sha) key.

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
    dependencies=[Depends(require_same_origin)],  # CSRF/Origin gate (authed mutating)
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
    # Tier 0: while the process is draining on SIGTERM, reject new work
    # cleanly rather than start a task a pending SIGKILL would abandon mid-run.
    # 503 is the retry signal the frontend retry self-heals on (the next
    # boot accepts it). Checked after the feature gate so the feature stays
    # invisible (404) when off.
    if _shutting_down:
        raise HTTPException(
            status_code=503,
            detail="service is draining, retry shortly",
        )
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

    # Connected-repo identifier the user chose as the existing codebase to match.
    # No fetch, no clone, no agent tool. The repo full_name remains request-only
    # because the prototypes table has no codebase text column, but we do persist
    # the matching GitHub App installation id when available: that is the existing
    # production-shaped scenario/source column and gives the future codebase
    # extractor enough installation context to read the selected repo.
    # Resolved BEFORE the website fallback so we can detect an unsatisfiable
    # github selection and gracefully degrade rather than hard-failing.
    repo = body.normalised_github_repo()
    github_installation_id = _resolve_github_installation_id_for_repo(
        workspace_id, repo
    )

    # Onboarding website as the automatic design source fallback.
    # When design_source is explicitly set to "figma" or "github", the website
    # auto-fill is suppressed so it cannot clobber the chosen source.
    # An explicit figma/github selection whose inputs are not actually available
    # degrades to the website default rather than hard-failing the generation,
    # so a user who picks GitHub before a repo resolves still gets a prototype.
    # When design_source is None (old client), the prior implicit precedence +
    # always-on onboarding auto-fill both run exactly as before (back-compat).
    selection = body.design_source
    figma_unsatisfiable = selection == "figma" and not body.figma_file_key
    github_unsatisfiable = selection == "github" and github_installation_id is None
    use_website_fallback = selection in (None, "website") or figma_unsatisfiable or github_unsatisfiable

    effective_website_url = body.website_url
    typed = (body.website_url or "").strip()
    if use_website_fallback and not body.figma_file_key and not typed and body.manual_design is None:
        fallback_url = get_company_website(workspace_id)
        if fallback_url:
            effective_website_url = fallback_url
            logger.info(
                "design_agent_website_fallback company_id=%s prd_id=%s host=%s",
                workspace_id,
                body.prd_id,
                urlsplit(fallback_url).hostname or "",
            )

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
        github_installation_id=github_installation_id,
    )

    # Open a usage-ledger row for this generation (billing/observability). The id
    # rides bg_kwargs so the SAME row is finalized at the bg-runner terminal on
    # BOTH the in-process and Tier-2 worker paths. Fail-open: a ledger failure
    # must never block a generation, so this never changes control flow.
    # NOTE: prd ownership isn't workspace-verified on this path yet; usage
    # attribution assumes caller == prd owner.
    event_id: int | None = None
    try:
        event_id = start_usage_event(
            workspace_id=workspace_id,
            kind="full_generation",
            prd_id=body.prd_id,
            prototype_id=prototype_id,
        )
    except Exception:  # noqa: BLE001 — ledger is fail-open; identifiers only.
        logger.warning(
            "usage_event_start_failed kind=full_generation prototype_id=%s",
            prototype_id,
        )

    # The exact `_run_generation_bg` kwargs — computed ONCE here so the inline
    # create_task path and the Tier 2 worker path run the IDENTICAL
    # generation body with byte-for-byte identical inputs. The enqueue boundary
    # is these already-resolved request-level inputs (resolved website_url,
    # resolved github_installation_id, etc.); the worker replays
    # `_run_generation_bg(**payload)` rather than re-deriving them, so the two
    # paths cannot diverge.
    bg_kwargs = dict(
        prototype_id=prototype_id,
        workspace_id=workspace_id,
        prd_id=body.prd_id,
        target_platform=body.normalised_platform(),
        instructions=body.instructions,
        figma_file_key=body.figma_file_key,
        figma_node_id=body.figma_node_id,  # frame-level targeting; None when absent
        website_url=effective_website_url,  # resolved value incl. onboarding fallback
        manual_design=body.manual_design,
        github_repo=repo,  # normalised connected-repo full_name; prompt context only
        github_installation_id=github_installation_id,
        design_source=body.design_source,
        chosen_screen_route=body.chosen_screen_route,
        chosen_screen_id=body.chosen_screen_id,
        map_commit_sha=body.map_commit_sha,
        # Usage-ledger row id, threaded so the bg-runner can finalize the SAME
        # row at the terminal. A plain int → round-trips losslessly through the
        # Tier-2 job payload (serialize/deserialize copy scalars verbatim).
        event_id=event_id,
    )

    # Tier 2: 3-way enqueue decision. ARM the worker queue ONLY when the
    # flag is on AND a worker heartbeat is fresh. enqueue_job is fail-soft
    # (returns None on a missing table / DB error), so even an armed attempt that
    # fails to land a row degrades to the in-process path — never a 500. A box
    # without the worker unit therefore behaves exactly as today.
    if _worker_enabled() and worker_heartbeat_fresh(
        within_seconds=_WORKER_HEARTBEAT_FRESH_SECONDS
    ):
        job = enqueue_job(
            prototype_id=prototype_id,
            workspace_id=workspace_id,
            payload=_serialize_generation_payload(bg_kwargs),
        )
        if job is not None:
            # The prototype row is already 'generating'; the worker picks the job
            # up and runs the identical body. Transparent to the frontend, which
            # polls prototype status as today.
            logger.info(
                "design_agent_generation_enqueued prototype_id=%s job_id=%s",
                prototype_id, job.get("id"),
            )
            return GenerateResponse(prototype_id=prototype_id, status="generating")
        # enqueue failed (table missing / DB error) — fall through to in-process.
        logger.warning(
            "design_agent_enqueue_failed_inprocess_fallback prototype_id=%s",
            prototype_id,
        )
    elif _worker_enabled():
        # Flag on but no live worker (stale/absent heartbeat). Falling back keeps
        # a box where the worker unit isn't running from stranding a queued job.
        logger.warning(
            "design_agent_worker_no_heartbeat_inprocess_fallback prototype_id=%s",
            prototype_id,
        )

    # In-process path (verbatim pre-Tier-2 behaviour): flag off, no fresh
    # heartbeat, or a failed enqueue all land here.
    task = asyncio.create_task(_run_generation_bg(**bg_kwargs))
    _inflight_tasks.add(task)
    task.add_done_callback(_inflight_tasks.discard)
    # Also register in the generation-only, prototype_id-keyed registry so the
    # cancel endpoint can find and abort this task. Cleared via a SEPARATE
    # done-callback so the shared-set strong-ref idiom above is left exactly as
    # every other create_task site does it (add + add_done_callback(discard)).
    _inflight_generation_tasks[prototype_id] = task

    def _clear_generation_entry(t: asyncio.Task, _pid: int = prototype_id) -> None:
        # Clobber-guard: only remove the entry if it still points at THIS task —
        # a subsequent regeneration for the same prototype_id could have replaced
        # it, and that newer task must keep its registration.
        if _inflight_generation_tasks.get(_pid) is t:
            _inflight_generation_tasks.pop(_pid, None)

    task.add_done_callback(_clear_generation_entry)

    return GenerateResponse(prototype_id=prototype_id, status="generating")


# ─── PRD patches: list pending ──────────────────────────────────────────────────
#
# The user-facing half of the PRD-patch flow. Agent-proposed PRD edits are
# persisted as `pending` rows in `prd_patches` (NEVER touching `prds.payload_md`);
# the `PrdPatchBanner` surfaces
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
    row = find_prototype_by_prd(
        prd_id=prd_id, workspace_id=company.company_id, statuses=["ready"]
    )
    if not row:
        raise HTTPException(status_code=404, detail="No ready prototype for this PRD")
    return row


@router.get("/by-prd/{prd_id}/active")
def get_active_by_prd(
    prd_id: int,
    company: CompanyContext = Depends(require_company),
) -> dict[str, Any]:
    """Return the most-recent READY-or-GENERATING prototype for a PRD.

    Resume lookup: unlike `/by-prd/{prd_id}` (ready only), this also returns an
    in-flight 'generating' row so the prototype route can RE-ATTACH on a (re)load
    that happens mid-generation — show the loader and poll to ready — instead of
    dropping to the generate panel and stranding the finished bundle during the
    readiness lag (SSE 'done' at codegen-complete vs complete_prototype() at the
    end of the build/stage tail). Pure read, no generate side-effect. Returns 404
    when no active prototype exists (frontend swallows 404→null). Workspace-
    filtered: a prototype in another workspace returns 404, not 403. Three-segment
    path, so it can never be shadowed by the single-segment `GET /{prototype_id}`.
    """
    _require_feature_enabled()
    row = find_prototype_by_prd(
        prd_id=prd_id, workspace_id=company.company_id, statuses=["ready", "generating"]
    )
    if not row:
        raise HTTPException(status_code=404, detail="No active prototype for this PRD")
    return row


@router.get("/by-prd/{prd_id}/latest")
def get_latest_by_prd(
    prd_id: int,
    company: CompanyContext = Depends(require_company),
) -> dict[str, Any]:
    """Return the most-recent prototype for a PRD of ANY status (incl 'failed').

    Failed-state lookup: unlike `/by-prd/{prd_id}/active` (ready-or-generating
    only), this does NOT status-filter, so a FAILED latest row resolves here
    instead of returning null and dropping the prototype route to the bare
    generate CTA. The route calls this only on the none-branch (no ready/
    generating row) to decide between an error+retry surface and the empty
    state. Pure read, no generate side-effect. Returns 404 when NO prototype
    exists for the PRD (frontend swallows 404→null → empty state). Workspace-
    filtered: a prototype in another workspace returns 404, not 403. Three-
    segment path, so it can never be shadowed by the single-segment
    `GET /{prototype_id}`.
    """
    _require_feature_enabled()
    row = find_prototype_by_prd(
        prd_id=prd_id, workspace_id=company.company_id
    )
    if not row:
        raise HTTPException(status_code=404, detail="No prototype for this PRD")
    return row


# ─── Brief prototype map (batch read for card rendering) ─────────────────────


class PrototypeReadiness(BaseModel):
    """Prototype presence + preview for one PRD entry.

    `ready` is always True when this object appears — absent prototype is
    represented by `prototype: null` on the parent entry, not by a
    `{ready: false}` object, so the frontend never needs to branch on the field.
    """

    ready: bool = True
    preview_image_url: str | None = None


class BriefPrototypeMapEntry(BaseModel):
    """One insight that HAS a PRD (status=ready, PRD_VARIANT).

    Insights without a ready PRD are absent from the entries list so the
    frontend can treat `absent == no PRD` without a null-check branch.

    `prd_title` mirrors the PRD's title field so the brief card tile can
    display the same title as the editor's da-titlebar-title without an
    independent source — the two can never diverge.
    """

    insight_index: int
    prd_id: int
    prd_title: str
    prototype: PrototypeReadiness | None = None


class BriefPrototypeMapResponse(BaseModel):
    """Response for GET /v1/design-agent/brief-prototype-map.

    One `entries` item per insight that has a ready PRD.  Empty list when no
    PRDs exist for the brief.  The prototype field carries readiness + preview
    URL when a ready prototype exists for that PRD; null otherwise.
    """

    brief_id: int
    entries: list[BriefPrototypeMapEntry]


@router.get("/brief-prototype-map", response_model=BriefPrototypeMapResponse)
def get_brief_prototype_map(
    brief_id: int,
    company: CompanyContext = Depends(require_company),
) -> BriefPrototypeMapResponse:
    """Return which insights have a PRD and whether each PRD has a ready prototype.

    Designed for the brief overview screen: the frontend calls this ONCE per
    brief on load and uses the result to render context-aware cards (no PRD,
    PRD without prototype, PRD with prototype + optional preview image).

    Pure read — NO side effects. Never creates a PRD, never creates a prototype.
    Feature-flag-gated and workspace-isolated identically to GET /by-prd/{prd_id}:
      - 404 when DESIGN_AGENT_ENABLED is off (feature invisible).
      - workspace_id resolved from the caller's company membership (require_company).
      - prototype lookup is workspace-scoped via find_prototype_by_prd.

    Brief ownership check: this endpoint does NOT explicitly verify that brief_id
    belongs to the caller's workspace. Sibling read routes (e.g. GET /by-prd/{prd_id})
    follow the same approach — cross-workspace containment is enforced at the
    prototype layer (find_prototype_by_prd filters by workspace_id), and a
    foreign-workspace brief simply yields no PRD rows. The caller therefore learns
    nothing about a brief they don't own — the entries list is empty. FLAG: if an
    explicit brief→company ownership check is added to sibling routes, add the same
    check here (see prds table brief_id → briefs table → company/dataset chain).
    """
    _require_feature_enabled()
    workspace_id = company.company_id
    prd_variant = _prd_variant()

    prds = list_prds_by_brief(brief_id=brief_id, variant=prd_variant)

    entries: list[BriefPrototypeMapEntry] = []
    for prd in prds:
        proto_row = find_prototype_by_prd(
            prd_id=prd["id"], workspace_id=workspace_id, statuses=["ready"]
        )
        prototype = (
            PrototypeReadiness(preview_image_url=proto_row.get("preview_image_url"))
            if proto_row
            else None
        )
        entries.append(
            BriefPrototypeMapEntry(
                insight_index=prd["insight_index"],
                prd_id=prd["id"],
                prd_title=prd["title"] or "",
                prototype=prototype,
            )
        )

    return BriefPrototypeMapResponse(brief_id=brief_id, entries=entries)


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


# ─── Locate: map + locate + gate pipeline ─────────────────────────────────────
#
# POST /locate is a static POST and cannot be shadowed by GET /{prototype_id}.
# Declared here (above the catch-all) as a defensive measure per the standing
# route-ordering lesson; FastAPI matches POSTs independently of GETs.
#
# Flow: workspace-check PRD → resolve installation → build_map → locate_screen
#       → decide_gate → serialize.  Both blocking calls are wrapped in
#       asyncio.to_thread so the FastAPI worker is never blocked.
#
# Degradation:
#   No installation / build_map None     → unmapped fail-open (200, unmapped=True)
#   build_map raises                     → unmapped fail-open (200, unmapped=True)
#   locate_screen raises (patched tests) → 502 (PM retries; never fabricates)


class LocateRequest(BaseModel):
    prd_id: int = Field(..., gt=0)
    github_repo: str = Field(..., min_length=1)   # connected-repo full_name "org/repo"
    ref: str | None = None                         # branch/sha; None = default branch
    # Optional user steer for a "search again" re-run: a free-text direction
    # ("the settings page") that re-ranks locate toward the surface the PM means.
    # Capped here (the prompt layer caps again defensively); blank/None = today's
    # unsteered locate, byte-for-byte.
    hint: str | None = Field(default=None, max_length=300)
    # Optional screenshot of the target screen for the same "search again" re-run.
    # A base64 image data URL ("data:image/<png|jpeg|webp>;base64,…"),
    # client-downscaled. It rides the volatile locate user turn as an image block
    # so the model reads its on-screen text/route cues and re-ranks. Stateless:
    # never persisted, never logged as bytes. Omitted/None = no image. Server
    # decode + size validation lives in locate_screen, which falls open to a
    # text-only locate (never 500) on an oversized/undecodable image.
    image: str | None = Field(default=None)


class LocateCandidateOut(BaseModel):
    id: str = ""  # stable node id; "" for an old map node that only had a route.
    #               The resolution key the picker forwards as chosen_screen_id so a
    #               non-route host (app shell / in-page section) survives to generate.
    route: str
    entry_component: str
    confidence: int
    rationale: str
    ambiguous: bool
    component_count: int = 0  # composed_components length from the matching ScreenNode


class LocateResponse(BaseModel):
    decision: Literal["auto_proceed", "proceed_with_note", "ranked_confirm"]
    chosen: list[LocateCandidateOut]   # screen(s) generation would run on
    ranked: list[LocateCandidateOut]   # full top-3 for the picker
    top_confidence: int
    threshold: int
    repo: str
    posture: Literal["CLEAN", "PARTIAL"]   # from MapResult — surfaced for the chip
    unmapped: bool = False                  # True when no installation / empty map
    commit_sha: str = ""                    # snapshot SHA the route was confirmed
    #                                         against; the subsequent generate
    #                                         pins build_map to this SHA so the
    #                                         recreate reads the same bytes.
    #                                         "" on the unmapped path.
    # Image-as-steer. Cues the model read off an attached screenshot,
    # for the recovery chip; empty unless an image was applied. image_status tells
    # the UI whether the screenshot was used so it never claims a steer that did
    # not happen ("absent" | "applied" | "ignored_oversize" | "ignored_decode").
    read_cues: list[str] = Field(default_factory=list)
    image_status: str = "absent"


def _unmapped_locate_response(repo: str) -> LocateResponse:
    """Fail-open response for the no-installation and map-failure paths."""
    from app.design_agent.codebase_map.gate import GateResult, threshold_for_repo
    from app.design_agent.codebase_map.locate import emit_locate_telemetry
    t = threshold_for_repo(repo)
    emit_locate_telemetry(
        repo=repo,
        sha="",
        gate_result=GateResult(decision="ranked_confirm", chosen=[], ranked=[], threshold=t, top_confidence=0),
        n_candidates=0,
    )
    return LocateResponse(
        decision="ranked_confirm",
        chosen=[],
        ranked=[],
        top_confidence=0,
        threshold=t,
        repo=repo,
        posture="PARTIAL",
        unmapped=True,
        commit_sha="",
    )


def _candidate_to_out(candidate, map_result) -> LocateCandidateOut:
    """Serialize one LocateCandidate, enriching component_count from the map.

    Prefer matching the map node by stable id so a non-route host (the app
    shell with an empty route, an in-page section with a shared route) gets the
    right count; fall back to the route match when the candidate carries no id
    (an old map node that only had a route).
    """
    candidate_id = getattr(candidate, "id", "") or ""
    component_count = 0
    if map_result is not None:
        for node in map_result.nodes:
            matches = (
                node.id == candidate_id if candidate_id else node.route == candidate.route
            )
            if matches:
                component_count = len(node.composed_components)
                break
    return LocateCandidateOut(
        id=candidate_id,
        route=candidate.route,
        entry_component=candidate.entry_component,
        confidence=candidate.confidence,
        rationale=candidate.rationale,
        ambiguous=candidate.ambiguous,
        component_count=component_count,
    )


# ── Async locate: job store + accept/poll contract ───────────────────────────
# Under generation load the single API process is CPU-saturated, so the
# previously synchronous /locate (map build → locate LLM → gate) hung past
# nginx's read timeout and returned 504 — the frontend then silently collapsed
# to the PRD. The fix decouples the request from the work: POST /locate kicks the
# pipeline into a background task (registered for graceful drain) and returns a
# job id immediately, and the client polls GET /locate/jobs/{id} for the result.
# No 504 on the request itself; the frontend shows progress + retries.
#
# The store is intentionally PROCESS-LOCAL (a module-level dict), not a DB table:
# locate jobs are seconds-long and the frontend retry re-submits if a deploy
# restart loses one in flight, so durable persistence is not warranted here.
# (Flagged for the reviewer: if cross-process durability is ever needed this is
# the seam to revisit — but it is deliberately out of scope.)

# job_id -> {status, workspace_id, created_at, result?, error?}
_locate_jobs: dict[str, dict[str, Any]] = {}
_LOCATE_JOB_TTL_SECONDS = 600  # drop entries older than ~10 min (opportunistic sweep)
_MAX_LOCATE_IMAGE_CHARS = 10 * 1024 * 1024  # ~10 MB of base64 chars; a legit <=5 MB image is ~6.7 MB base64, so this never rejects a valid request


def _sweep_locate_jobs(now: float | None = None) -> None:
    """Opportunistic TTL sweep: drop job entries older than the TTL so the
    process-local dict cannot grow unbounded. Called on each store access; no
    background timer. Cheap (a single pass over a small, short-lived dict)."""
    cutoff = (now if now is not None else time.monotonic()) - _LOCATE_JOB_TTL_SECONDS
    stale = [jid for jid, rec in _locate_jobs.items() if rec.get("created_at", 0) < cutoff]
    for jid in stale:
        _locate_jobs.pop(jid, None)


class LocateJobAccepted(BaseModel):
    """Returned by POST /locate the moment the background task is kicked off."""
    job_id: str
    status: Literal["running"]


class LocateJobStatus(BaseModel):
    """Returned by GET /locate/jobs/{job_id} on each poll."""
    status: Literal["running", "done", "error"]
    result: LocateResponse | None = None
    error: str | None = None


async def _run_locate_bg(
    *,
    job_id: str,
    workspace_id: str,
    github_repo: str,
    ref: str | None,
    prd_text: str,
    installation_id: int | None,
    hint: str | None = None,
    image: str | None = None,
) -> None:
    """Run the locate pipeline off the request path and record the terminal
    state in the process-local job store.

    Resolves to the same LocateResponse the endpoint used to return synchronously:
    installation resolve → build_map → locate_screen → decide_gate → telemetry →
    serialize. Installation/map failures degrade to the unmapped fail-open
    response (status "done"); a locate-LLM failure records status "error".

    Never raises: a background task whose exception escaped would only log a
    noisy "Task exception was never retrieved" and the poller would hang on
    "running" forever. Every path writes a terminal record instead.
    """
    from app.design_agent.codebase_map.service import build_map
    from app.design_agent.codebase_map.locate import emit_locate_telemetry, locate_screen
    from app.design_agent.codebase_map.gate import decide_gate, threshold_for_repo
    from app.design_agent.codebase_map.shell import APP_SHELL_NODE_ID

    def _store(status: str, *, result: LocateResponse | None = None, error: str | None = None) -> None:
        rec = _locate_jobs.get(job_id)
        if rec is None:  # swept away (TTL) — nothing to update
            return
        rec["status"] = status
        if result is not None:
            rec["result"] = result
        if error is not None:
            rec["error"] = error

    try:
        if installation_id is None:
            _store("done", result=_unmapped_locate_response(github_repo))
            return

        try:
            map_result = await asyncio.to_thread(build_map, installation_id, github_repo, ref)
        except Exception:
            logger.info("design_agent.locate.map_failed repo=%s", github_repo)
            _store("done", result=_unmapped_locate_response(github_repo))
            return

        if map_result is None:
            _store("done", result=_unmapped_locate_response(github_repo))
            return

        locate_result = await asyncio.to_thread(
            locate_screen, prd_text, map_result, hint=hint, image=image
        )

        threshold = threshold_for_repo(github_repo)
        # The gate's spans-routing rescue (attach a cross-cutting would-be-decline
        # to the app shell) only fires when it knows the map promoted an app-shell
        # surface. Derive that minimal signal from the already-built map — the
        # single kind="shell" node and its stable id — instead of handing the gate
        # the whole map. No shell node => has_app_shell=False, leaving routed-node
        # decisions byte-for-byte unchanged.
        shell_node = next((n for n in map_result.nodes if n.kind == "shell"), None)
        has_app_shell = shell_node is not None
        app_shell_node_id = shell_node.id if shell_node is not None else APP_SHELL_NODE_ID
        gate = decide_gate(
            locate_result,
            threshold=threshold,
            has_app_shell=has_app_shell,
            app_shell_node_id=app_shell_node_id,
        )
        emit_locate_telemetry(
            repo=github_repo,
            sha=map_result.commit_sha,
            gate_result=gate,
            n_candidates=len(gate.ranked),
        )

        _store("done", result=LocateResponse(
            decision=gate.decision,
            chosen=[_candidate_to_out(c, map_result) for c in gate.chosen],
            ranked=[_candidate_to_out(c, map_result) for c in gate.ranked],
            top_confidence=gate.top_confidence,
            threshold=gate.threshold,
            repo=github_repo,
            posture=map_result.posture,
            unmapped=False,
            commit_sha=map_result.commit_sha,
            read_cues=locate_result.read_cues,
            image_status=locate_result.image_status,
        ))
    except Exception as exc:  # noqa: BLE001 — terminal record, never let the task die unhandled
        from app.design_agent.provider_errors import (
            classify_provider_error,
            is_alertable,
        )

        # Store ONLY the safe class in the job record — a raw exception string can
        # carry provider/account state and must stay out of any client-visible
        # field. The raw text goes to the log ONLY.
        cls = classify_provider_error(exc)
        logger.warning(
            "design_agent.locate.failed repo=%s error_class=%s classified=%s raw=%s",
            github_repo, type(exc).__name__, cls.value, str(exc),
        )
        if is_alertable(cls):
            from app.design_agent.provider_alert import maybe_alert_provider_outage

            maybe_alert_provider_outage(cls, context={"prototype_id": "locate"})
        _store("error", error=cls.value)


@router.post(
    "/locate",
    response_model=LocateJobAccepted,
    status_code=202,
    dependencies=[Depends(require_same_origin)],
)
async def locate(
    body: LocateRequest,
    company: CompanyContext = Depends(require_company),
) -> LocateJobAccepted:
    """Accept a locate request and run the pipeline in the background.

    Validates + authorizes inline (feature gate, PRD ownership, installation
    resolve), creates a process-local job record, kicks the heavy work
    (build_map → locate LLM → gate) into a background task registered in
    _inflight_tasks (so graceful drain awaits an in-flight locate), and returns a
    job id immediately. The client polls GET /locate/jobs/{job_id}.

    Unlike /generate, /locate is NOT rejected while draining: it is the
    lightweight path we keep serving. An already-running locate task is still
    awaited by drain because it is registered in _inflight_tasks.
    """
    _require_feature_enabled()
    workspace_id = company.company_id

    logger.info(
        "design_agent.locate.request prd_id=%s repo=%s workspace_id=%s",
        body.prd_id, body.github_repo, workspace_id,
    )

    # Workspace isolation: PRD must belong to this company's workspace. Resolved
    # inline (not in the background task) so an unauthorized request fails fast
    # with the right status instead of leaking a pollable job id.
    from app.deps.ownership import require_owned_prd
    require_owned_prd(body.prd_id, workspace_id)
    prd_row = get_prd_rendered(body.prd_id)
    prd_text = (prd_row.get("payload_md") or "") if prd_row else ""

    installation_id = _resolve_github_installation_id_for_repo(
        workspace_id, body.github_repo
    )

    # Trim the optional steer to a clean None when blank so the background task
    # and the locate prompt take the unsteered path; the model length cap on
    # LocateRequest.hint already bounds it.
    hint = (body.hint or "").strip() or None
    # The image is forwarded as-is (or None). locate_screen owns decode + size
    # validation and falls open to text-only on a bad/oversized image; we do not
    # log or persist the bytes here.
    image = body.image or None

    if image is not None and len(image) > _MAX_LOCATE_IMAGE_CHARS:
        # Accept-step guard: a client that bypasses the client-side downscale must not
        # get a giant upload queued and billed to the vision model. This is a 4xx, not a
        # 500 — within-cap-but-undecodable images still fall open to a text-only locate
        # inside the job. Reject synchronously before the job is minted.
        raise HTTPException(status_code=413, detail="Screenshot too large.")

    _sweep_locate_jobs()
    job_id = uuid.uuid4().hex
    _locate_jobs[job_id] = {
        "status": "running",
        "workspace_id": workspace_id,
        "created_at": time.monotonic(),
    }

    task = asyncio.create_task(
        _run_locate_bg(
            job_id=job_id,
            workspace_id=workspace_id,
            github_repo=body.github_repo,
            ref=body.ref,
            prd_text=prd_text,
            installation_id=installation_id,
            hint=hint,
            image=image,
        )
    )
    _inflight_tasks.add(task)
    task.add_done_callback(_inflight_tasks.discard)

    return LocateJobAccepted(job_id=job_id, status="running")


@router.get("/locate/jobs/{job_id}", response_model=LocateJobStatus)
async def locate_job(
    job_id: str,
    company: CompanyContext = Depends(require_company),
) -> LocateJobStatus:
    """Poll the status/result of a locate job.

    Workspace-scoped: a job is only pollable by the workspace that created it. A
    job_id belonging to another workspace (or one already swept / never minted)
    returns 404 — not 403 — so cross-tenant existence is not even disclosed.
    """
    _require_feature_enabled()
    workspace_id = company.company_id

    _sweep_locate_jobs()
    rec = _locate_jobs.get(job_id)
    if rec is None or rec.get("workspace_id") != workspace_id:
        raise HTTPException(status_code=404, detail="Locate job not found")

    return LocateJobStatus(
        status=rec["status"],
        result=rec.get("result"),
        error=rec.get("error"),
    )


@router.get("/{prototype_id}")
def get_one(
    prototype_id: int,
    company: CompanyContext = Depends(require_company),
) -> dict[str, Any]:
    """Return the full prototype row for the frontend poller.

    Sync handler (mirrors routes/prd.py's GET) — FastAPI runs it in the
    threadpool, so the blocking supabase read does not stall the event loop.
    Workspace-filtered: a row in a different workspace returns 404, not 403,
    so cross-tenant existence is not even disclosed.
    """
    _require_feature_enabled()
    workspace_id = company.company_id
    row = get_prototype(prototype_id=prototype_id, workspace_id=workspace_id)
    if not row:
        raise HTTPException(status_code=404, detail="Prototype not found")
    return row


@router.delete("/{prototype_id}", status_code=204, dependencies=[Depends(require_same_origin)])
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


@router.post("/{prototype_id}/cancel", status_code=204, dependencies=[Depends(require_same_origin)])
def cancel_prototype_route(
    prototype_id: int,
    company: CompanyContext = Depends(require_company),
) -> Response:
    """Cancel an in-flight generation and return the user to a clean slate.

    Two-part abort:
      1. Unconditional DB cleanup — delete the prototype row + reset its PRD to
         draft (mirrors DELETE /{prototype_id}). This is what makes the
         user-facing outcome correct regardless of which worker holds the task,
         and there is no `cancelled` status: clean-slate matches the "I picked
         the wrong thing, undo it" intent.
      2. Best-effort task abort — if the in-flight generation task is running in
         THIS process, cancel it so it stops spending on further LLM turns. When
         the task lives in another worker (or has already finished), this is a
         no-op and step 1 still leaves the correct state.

    Workspace-scoped exactly like the DELETE route: a prototype in another
    workspace returns 404, never 403, so cross-tenant existence is not disclosed.
    Idempotent/safe: an already-gone prototype returns 404, not 500.
    """
    _require_feature_enabled()
    workspace_id = company.company_id
    existing = get_prototype(prototype_id=prototype_id, workspace_id=workspace_id)
    if existing is None:
        raise HTTPException(status_code=404, detail="Prototype not found")

    # 1. Unconditional DB cleanup (idempotent; correct under multi-worker prod).
    delete_prototype(prototype_id=prototype_id, workspace_id=workspace_id)
    reset_prd_to_draft(existing["prd_id"])

    # 2. Best-effort abort of the local in-flight generation task. A `cancel()`
    # raises CancelledError at the next await boundary — it stops the NEXT LLM
    # turn but cannot kill the turn currently running on a worker thread, so this
    # is a best-effort stop of future turns, not an instant mid-stream kill.
    task = _inflight_generation_tasks.get(prototype_id)
    if task is not None and not task.done():
        task.cancel()
        logger.info(
            "design_agent.generation_cancelled prototype_id=%s", prototype_id,
        )

    return Response(status_code=204)


# NOTE: GET /by-prd/{prd_id} is defined ABOVE (near the other PRD routes), not
# here — a second identical definition used to live at this spot, which produced
# a duplicate operation id and a redundant route registration. Removed; the
# single definition above is canonical.


# ─── Background generation ────────────────────────────────────────────────


def _finalize_usage_event_failed(
    *,
    event_id: int | None,
    workspace_id: str,
    prototype_id: int,
    error_class: str,
    kind: Literal["full_generation", "iteration"],
) -> None:
    """Fail-open finalize of a usage-ledger row to 'failed'.

    Shared by both the generation and iteration failure terminals — a
    ledger-write failure must never change control flow at either terminal, so
    every finalize call here is wrapped: identifiers-only WARNING on error,
    never propagated. No tokens are required on a failure (the run did not
    bill a complete generation/iteration), so only status + error_class are
    recorded. `kind` distinguishes the two call sites in the log line only —
    it carries no other behavior difference (both terminals are otherwise
    identical: same DB call, same fail-open guard, same no-op on a None
    event_id, which covers PLAN mode or a failure before the ledger row
    opened).
    """
    if event_id is None:
        return
    try:
        finalize_usage_event(
            event_id=event_id,
            workspace_id=workspace_id,
            status="failed",
            error_class=error_class,
        )
    except Exception:  # noqa: BLE001 — ledger is fail-open; identifiers only.
        logger.warning(
            "usage_event_finalize_failed event_id=%s prototype_id=%s kind=%s",
            event_id, prototype_id, kind,
        )


async def _run_generation_bg(
    *,
    prototype_id: int,
    workspace_id: str,
    prd_id: int,
    target_platform: str,
    instructions: str,
    figma_file_key: str | None,
    figma_node_id: str | None = None,
    website_url: str | None = None,
    manual_design: ManualDesignInput | None = None,
    github_repo: str | None = None,
    github_installation_id: int | None = None,
    design_source: str | None = None,
    chosen_screen_route: str | None = None,
    chosen_screen_id: str | None = None,
    map_commit_sha: str | None = None,
    event_id: int | None = None,
) -> None:
    """Fired from POST /generate; assembles the first call + runs the agent loop.

    On any exception, sets prototype.status='failed' with the error message in
    the existing Sprntly format (`f"{type(exc).__name__}: {exc}"`, prd_runner.py
    style). The structured cost-summary log line is emitted by
    `generate_prototype` itself. On a complete run with emitted files,
    `_stage_complete_run` builds + stages the bundle and marks the row
    ready; every other terminal state fails the row.
    """
    try:
        prd_md = _load_prd_body(prd_id)
        # Single "design source" slot. Figma always wins when present — the
        # website block is not even built in that case. When there is no Figma,
        # Scenario B (extracted/manual website design system) takes the slot;
        # `_website_context_block` returns None when there is neither a website
        # URL nor manual hints, so we fall back to the generic Figma string
        # ("(no Figma source detected)").
        # Run the website extractor at most once per generation. The same sample
        # feeds both the scaffold prose block (below) and the design-system
        # pre-seed (threaded into `generate_prototype`), so Scenario B's tokens
        # reach the prototype the same way Scenario A's do — via a pre-seeded
        # `src/index.css` — without a second browser run. Skipped entirely when a
        # Figma file is present (Figma wins the single design-source slot).
        website_sample: dict | None = None
        if not figma_file_key:
            website_sample = await _extract_website_sample(website_url)
        website_block = (
            None if figma_file_key
            else await _website_context_block(
                website_url,
                manual_design,
                extracted_ds=website_sample,
                extracted_ds_resolved=True,
            )
        )
        source_block = website_block or _figma_context_block(figma_file_key)

        # Exactly one system block; cache_control at the END of the stable
        # prefix. The agent loop reads the LAST block's cache_control to cache
        # the stable system prefix.
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
        # (Scenario C) lands later — for now, prd_references_codebase is
        # always False, so C never fires here regardless of inputs.
        scenario_set = infer_scenario_from_inputs(
            figma_file_key=figma_file_key,
            website_url=website_url,        # derives 'B' (url, no figma) / '0'
            github_installation_id=github_installation_id,
            prd_references_codebase=False,  # the detector lands later
        )
        scenario_label = ",".join(sorted(scenario_set))  # "A" | "A,C" | "0" ...

        # Resolve the PM-confirmed screen route into a LocatedScreen for the
        # recreate pre-seed branch of generate_prototype. Gated to codebase
        # generation only; every other source path is byte-for-byte today's
        # blank-canvas flow (located_screen stays None). Reuses the
        # installation id already resolved in the handler so we never make a
        # second live GitHub OAuth call here. map_commit_sha pins build_map
        # to the snapshot the PM confirmed the route against, so the recreate
        # reads the same bytes (and lands a cache hit on the existing key).
        located = None
        # The repo map for this run, when one was built. Carried into
        # generate_prototype as shell_map so the shell-grounded fallback (Tier-2)
        # can seat the PRD inside the real app shell when NO screen was located.
        # Built once and reused for both the located resolve and the fallback (a
        # cache hit by repo+commit, so no second crawl).
        shell_map = None
        # Build the map whenever a codebase run pins a snapshot — a chosen screen
        # gives us Tier-1, no chosen screen still gives Tier-2 the shell. Gated on
        # map_commit_sha so we only read against the snapshot the PM confirmed
        # against (cache hit), never a fresh HEAD crawl.
        if (
            design_source == "github"
            and github_installation_id is not None
            and map_commit_sha
        ):
            try:
                from app.design_agent.codebase_map.recreate import LocatedScreen
                from app.design_agent.codebase_map.service import build_map
                map_result = await asyncio.to_thread(
                    build_map, github_installation_id, github_repo, map_commit_sha
                )
                if map_result is not None:
                    shell_map = map_result
                    # Resolve by stable id first — the app shell (route="") and an
                    # in-page section (empty or shared route) only survive this hop
                    # by id, and id is unique where a route is not. Fall back to the
                    # route match when no id was sent (old client) or it did not
                    # resolve, preserving today's routed-node behaviour exactly.
                    node = None
                    if chosen_screen_id or chosen_screen_route:
                        node = next(
                            (n for n in map_result.nodes if chosen_screen_id and n.id == chosen_screen_id),
                            None,
                        )
                        if node is None and chosen_screen_route:
                            node = next(
                                (n for n in map_result.nodes if n.route == chosen_screen_route),
                                None,
                            )
                    if node is not None:
                        located = LocatedScreen(map_result=map_result, node=node)
                        logger.info(
                            "design_agent.recreate_wired prototype_id=%s repo=%s route=%s sha=%s node_id=%s node_kind=%s",
                            prototype_id, github_repo, chosen_screen_route, map_commit_sha, node.id, node.kind,
                        )
                elif chosen_screen_id or chosen_screen_route:
                    logger.warning(
                        "design_agent.recreate_wire_failed prototype_id=%s repo=%s",
                        prototype_id, github_repo,
                    )
            except Exception:
                logger.warning(
                    "design_agent.recreate_wire_failed prototype_id=%s repo=%s",
                    prototype_id, github_repo,
                )
                located = None
                shell_map = None

        # Signal (not silent) when a codebase-grounded request degrades below full
        # grounding. design_source == "github" is the user's explicit intent signal.
        # Two degrade shapes below both mean the recreate pre-seed did not get what
        # it needed; today neither is surfaced anywhere the user can see it.
        if design_source == "github":
            grounding_note: str | None = None
            if shell_map is None:
                # No repository map was even attempted/available for this run —
                # covers: map_commit_sha absent, github_installation_id unresolved,
                # AND build_map itself returning None (the existing
                # recreate_wire_failed warning path) — all three collapse to
                # shell_map staying None.
                grounding_note = (
                    "Generated without codebase grounding: no repository map was "
                    "available for this run, so the prototype was built from the "
                    "design system only."
                )
            elif located is None and (chosen_screen_id or chosen_screen_route):
                # A map built fine, but the specific screen the user selected could
                # not be matched (stale id/route after a repo change — the map's own
                # "id-vs-route drift" seam). A shell-grounded prototype was still
                # produced; only the screen-specific parity contract is missing.
                grounding_note = (
                    "The selected screen could not be matched in the repository "
                    "map, so the prototype was generated against the general app "
                    "shell instead of that specific screen."
                )
            if grounding_note is not None:
                set_grounding_note(
                    prototype_id=prototype_id,
                    workspace_id=workspace_id,
                    note=grounding_note,
                )
                logger.warning(
                    "design_agent.grounding_degraded prototype_id=%s tier=%s",
                    prototype_id, "blank" if shell_map is None else "shell_only",
                )

        # Tier 1: serialise the HEAVY section (LLM recreate loop + vite
        # build + screenshot — the part that pins both cores on the 2-vCPU prod
        # box) under a process-wide semaphore. Default concurrency 1 keeps CPU
        # headroom for a concurrent /locate, softening the 504-under-load class.
        # The cheap setup above (PRD load, block assembly, route resolution) runs
        # OUTSIDE the guard. The prototype row stays 'generating' while a queued
        # run waits here (start_prototype set it; nothing flips it before this),
        # so a queued-but-not-yet-running prototype is correctly still generating.
        # Accumulator for build/typecheck repair tokens. The repair runs as a
        # SEPARATE agent loop inside _build_repair_loop, whose usage is NOT part
        # of the primary RunResult.usage. _stage_complete_run threads this in and
        # _build_repair_loop sums each repair pass into it, so the succeeded
        # ledger row reflects primary + repair tokens (under-counting = under-
        # billing). Empty when no repair ran.
        repair_usage = RunUsage()
        async with _get_generation_semaphore():
            result, virtual_fs = await generate_prototype(
                prototype_id=prototype_id,
                workspace_id=workspace_id,
                system_blocks=system_blocks,
                user_message=user_message,
                figma_file_key=figma_file_key,
                figma_node_id=figma_node_id,  # frame-level targeting; None when absent
                scenario=scenario_label,
                github_repo=github_repo,  # cost-summary identifier only; does NOT alter the scenario label
                github_installation_id=github_installation_id,
                website_url=None if figma_file_key else website_url,  # Scenario B pre-seed source
                website_sample=website_sample,  # reuse the single extractor run for the pre-seed
                design_source=design_source,
                located_screen=located,
                # Carried for the shell-grounded fallback: when no screen was
                # located, the runner reads this map's shell + theme and seats the
                # PRD inside the real app shell. None on every non-codebase run.
                shell_map=shell_map,
            )
            # Success path: a complete run that emitted files gets built +
            # staged + marked ready. A complete run with no files, or any non-complete
            # terminal state, fails the row. Build + screenshot stay inside the guard
            # because the vite build is the CPU pin we are protecting against.
            if result.status == "complete" and virtual_fs:
                # Derive the interactivity-containment scope on the recreate path
                # only (located is not None). The PRD's named interactions, derived
                # deterministically from the PRD text + the located screen — no LLM.
                # On the blank-canvas path (located is None) the scope is None, so
                # _stage_complete_run skips the containment check (byte-identical to
                # today).
                interactive_scope = (
                    derive_interactive_scope(prd_md, located)
                    if located is not None
                    else None
                )
                staged_ok = await _stage_complete_run(
                    prototype_id=prototype_id,
                    workspace_id=workspace_id,
                    virtual_fs=virtual_fs,
                    system_blocks=system_blocks,
                    figma_file_key=figma_file_key,
                    figma_node_id=figma_node_id,
                    scenario=scenario_label,
                    # Carries the theme-bridge expectations set on the recreate path
                    # (None on every blank-canvas run). Getattr defensively so older
                    # test stubs that return a bare SimpleNamespace keep working.
                    theme_expectations=getattr(result, "theme_expectations", None),
                    interactive_scope=interactive_scope,
                    # Recreate path only (located is not None) — the structural-parity
                    # self-check's ground truth (real shell + located node). None on
                    # every blank-canvas run, so the check is skipped there.
                    parity_located=located,
                    # Repair-token accumulator: _build_repair_loop sums each repair
                    # pass's usage into this so the ledger captures primary + repair.
                    repair_usage=repair_usage,
                )
                # Finalize the usage ledger. _stage_complete_run owns the prototype
                # status write (ready on success, failed on build-exhaustion), so we
                # mirror it: succeeded only when it staged. Tokens = primary run +
                # any repair-loop passes (under-counting = under-billing). Fail-open.
                if event_id is not None:
                    total_usage = RunUsage()
                    total_usage.add(getattr(result, "usage", None))
                    total_usage.add(repair_usage)
                    try:
                        finalize_usage_event(
                            event_id=event_id,
                            workspace_id=workspace_id,
                            status="succeeded" if staged_ok else "failed",
                            usage=total_usage,
                            model=MODEL,
                            prototype_id=prototype_id,
                            error_class=None if staged_ok else "build_stage_failed",
                        )
                    except Exception:  # noqa: BLE001 — ledger is fail-open.
                        logger.warning(
                            "usage_event_finalize_failed event_id=%s prototype_id=%s kind=full_generation",
                            event_id, prototype_id,
                        )
            elif result.status == "complete" and not virtual_fs:
                fail_prototype(
                    prototype_id=prototype_id,
                    workspace_id=workspace_id,
                    error="agent_loop completed but emitted no files",
                )
                _finalize_usage_event_failed(
                    event_id=event_id,
                    workspace_id=workspace_id,
                    prototype_id=prototype_id,
                    error_class="no_files",
                    kind="full_generation",
                )
            else:
                # Include the structured error_message / error_class from
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
                _finalize_usage_event_failed(
                    event_id=event_id,
                    workspace_id=workspace_id,
                    prototype_id=prototype_id,
                    error_class=getattr(result, "error_class", None)
                    or f"status_{result.status}",
                    kind="full_generation",
                )
    except asyncio.CancelledError:
        # The cancel endpoint called task.cancel() (the user aborted from the
        # loading screen). CancelledError is a BaseException, so it already
        # escapes the `except Exception` below WITHOUT reaching fail_prototype —
        # this narrow handler is here for explicitness and future-proofing: it
        # must NEVER write a terminal status (complete_prototype/fail_prototype)
        # for the now-deleted id, so a cancelled run cannot resurrect the row the
        # user just discarded. The cancel endpoint has already deleted the
        # prototype row, so there is no DB cleanup to do here; the storage/bundle
        # dir (if any) is staged deep inside _stage_complete_run from a virtual
        # FS and its path is not available at this scope, so there is no partial
        # on-disk dir to remove on this path. Re-raise so the cancellation
        # propagates normally.
        logger.info(
            "design_agent.generation_cancelled_bg prototype_id=%s", prototype_id,
        )
        raise
    except Exception as exc:  # noqa: BLE001 — bg task must never leak; row is failed.
        from app.design_agent.provider_errors import (
            classify_provider_error,
            is_alertable,
            safe_error_message,
        )

        # A provider exception can land here directly (raised outside the runner's
        # own terminal catch). Classify it and store ONLY the safe class + a fixed
        # generic message in the client-visible `error` column — never the raw
        # exception text. The raw text goes to the log ONLY.
        cls = classify_provider_error(exc)
        logger.warning(
            "design_agent.generation_failed prototype_id=%s error_class=%s classified=%s raw=%s",
            prototype_id, type(exc).__name__, cls.value, str(exc),
        )
        if is_alertable(cls):
            from app.design_agent.provider_alert import maybe_alert_provider_outage

            maybe_alert_provider_outage(cls, context={"prototype_id": prototype_id})
        _finalize_usage_event_failed(
            event_id=event_id,
            workspace_id=workspace_id,
            prototype_id=prototype_id,
            error_class=cls.value,
            kind="full_generation",
        )
        fail_prototype(
            prototype_id=prototype_id,
            workspace_id=workspace_id,
            error=f"error_class={cls.value} | error_message={safe_error_message(cls)}",
        )


# The post-build build-repair loop: at most a few agent re-entries, each held
# under its own small spend budget so repair can never reignite the very cost
# pressure that caused the dangling-import failure in the first place.
_BUILD_REPAIR_MAX_ITERS = 3
_BUILD_REPAIR_CAP_USD = 0.10

# Repair directive for the fail-closed acceptance gate: the build was green but the
# bundle still renders the scaffold placeholder (the agent wrote components but no
# entry point composes them). Fed to `_build_repair_loop` as the re-entry
# diagnostics so the agent wires an entry point instead of re-shipping the
# placeholder. Phrased as a build diagnostic (the loop leads each re-entry with it).
_ENTRY_POINT_REPAIR_DIRECTIVE = (
    "The build succeeded but the rendered app is still the empty scaffold "
    "placeholder: you created components but no entry point renders them. Write "
    "src/App.tsx (a default-exported component) and src/main.tsx that import and "
    "render your top-level component, so the app mounts your generated UI instead "
    "of the placeholder."
)



async def _build_repair_loop(
    *,
    prototype_id: int,
    workspace_id: str,
    virtual_fs: dict[str, str],
    system_blocks: list[dict],
    figma_file_key: str | None,
    figma_node_id: str | None,
    scenario: str,
    first_diagnostics: str,
    repair_usage: RunUsage | None = None,
) -> tuple[dict[str, str], dict[str, str]]:
    """Recover a build that failed — a runtime-breaking type check OR a generic
    `vite build` error (bad CSS `@apply`, an unresolved import the build's own
    deterministic repair could not fix, etc.).

    Re-enter the agent up to a few times to fix the offending source the build
    error names, rebuilding after each pass. Returns `(dist_files, repaired_fs)` on
    a green build. The build error headline leads each diagnostics string, so the
    agent sees the offending class/import on every re-entry.

    On exhaustion — re-tries used or the repair spend budget reached — the recovery
    is CLASS-AWARE. When the residual is a runtime-breaking type error (a dangling
    import the agent could not resolve), deterministically drop those imports (the
    same strip the build's own repair uses) and rebuild once, so the prototype still
    renders (incomplete but visible); if even that fails, raise
    TypeCheckRepairExhausted so the caller fails the row with a precise reason. When
    the residual is a generic ViteBuildError (e.g. an `@apply <unknown>` CSS error),
    stripping dangling imports does NOT help, so it is HONEST-FAILED: the residual
    propagates to the caller's fail path, which the frontend renders as error+Retry.

    Repair spend is tracked on its own budget, independent of the generation soft
    and hard caps, so a repair turn can run even when the original generation was
    stopped for cost. The agent sees the compiler diagnostics, but they are pinned
    behind a generic progress label, so they never reach a user-facing step event.
    """
    # Show a single calm step the moment repair begins, so the user never sees the
    # build stall in the gap between the failed build and the first repair re-entry.
    publish_step(prototype_id, FINISHING_STEP)
    diagnostics = first_diagnostics
    repair_cost_usd = 0.0
    # The most recent build error across the bounded re-tries. The exhaustion
    # branch reads its TYPE to pick the right recovery (strip-to-green for the
    # import/typecheck class, honest-fail for any other ViteBuildError).
    last_exc: TypeCheckError | ViteBuildError | PlaceholderShippedError = TypeCheckError(
        first_diagnostics
    )
    for _ in range(_BUILD_REPAIR_MAX_ITERS):
        if repair_cost_usd >= _BUILD_REPAIR_CAP_USD:
            break
        result, virtual_fs = await repair_build_run(
            prototype_id=prototype_id,
            workspace_id=workspace_id,
            system_blocks=system_blocks,
            virtual_fs=virtual_fs,
            diagnostics=diagnostics,
            figma_file_key=figma_file_key,
            figma_node_id=figma_node_id,
            scenario=scenario,
        )
        repair_cost_usd += result.usage.est_cost_usd(MODEL)
        # Surface repair tokens to the caller's usage ledger. This is a separate
        # agent loop, so its tokens are NOT in the generation's primary
        # RunResult.usage; accumulating here lets the succeeded ledger row reflect
        # primary + repair. Independent of the cap math above (which is unchanged).
        if repair_usage is not None:
            repair_usage.add(result.usage)
        try:
            dist_files, virtual_fs = await vite_build_with_repair(virtual_fs)
            # Re-assert the acceptance gate each pass: a green rebuild that still
            # renders the scaffold placeholder (entry never wired) must NOT be
            # returned as a recovered build — feed the entry-point directive back in.
            assert_mounts_generated_content(dist_files)
            logger.info(
                "build_repair_succeeded prototype_id=%s repair_cost_usd=%.4f",
                prototype_id, repair_cost_usd,
            )
            return dist_files, virtual_fs
        except (TypeCheckError, ViteBuildError) as exc:
            # Feed the residual back into the next pass. The headline leads, so the
            # agent sees the offending class/import (typecheck symbol OR CSS class).
            last_exc = exc
            diagnostics = str(exc)
            continue
        except PlaceholderShippedError as exc:
            # Green build, but still the placeholder — re-enter with the entry-point
            # directive (not the generic build headline) so the agent wires an entry.
            last_exc = exc
            diagnostics = _ENTRY_POINT_REPAIR_DIRECTIVE
            continue
        # A non-build failure (e.g. a missing runtime/scaffold file) propagates to
        # the caller and fails the row, exactly as a first-build failure of that
        # kind does — it is not model-fixable here.
    # Exhausted. Recover by residual class: only the import/typecheck class benefits
    # from stripping dangling imports. A generic build error (e.g. CSS `@apply` of an
    # undefined utility) does not — stripping imports cannot fix it — so honest-fail.
    if not isinstance(last_exc, TypeCheckError):
        logger.info(
            "build_repair_exhausted prototype_id=%s repair_cost_usd=%.4f "
            "error_class=%s action=honest_fail",
            prototype_id, repair_cost_usd, type(last_exc).__name__,
        )
        raise last_exc
    logger.info(
        "build_repair_exhausted prototype_id=%s repair_cost_usd=%.4f action=strip_to_green",
        prototype_id, repair_cost_usd,
    )
    stripped_fs, _actions = repair_unresolved_relative_imports(virtual_fs)
    try:
        return await vite_build_with_repair(stripped_fs)
    except (ViteBuildError, FileNotFoundError, TypeCheckError) as exc:
        raise TypeCheckRepairExhausted(
            f"build still failing after repair and strip: {exc}"
        ) from exc


async def _stage_checkpoint_and_bundle(
    *,
    prototype_id: int,
    workspace_id: str,
    dist_files: dict[str, str],
    virtual_fs: dict[str, str],
    prompt_history: list[dict],
    log_prefix: str,
) -> tuple[int, str]:
    """Shared checkpoint + dual-stage + comment-reconcile sequence used by BOTH
    the first-completion staging path and the iterate staging path.

    Does NOT touch `prototypes.status` / `completed_at` / `current_checkpoint_id`
    — the caller owns the terminal write (`complete_prototype` vs
    `advance_current_checkpoint`), because that is the one genuine difference
    between a first completion and a checkpoint advance (an iterate must never
    re-stamp `completed_at`).

    Steps, each keeping its pre-existing fail-open / fail-closed posture:

    1. `create_checkpoint` — the returned id seeds the bundle prefix.
    2. `stage_bundle(dist_files)` — the BUILT dist/. Raises on failure; the
       caller's own `except` wraps this call and routes to `fail_prototype`, so
       the caller-side error contract is unchanged. This function does NOT call
       `fail_prototype` itself.
    3. `stage_bundle(virtual_fs, sub_prefix="_source")` — best-effort raw source
       for the next iterate / manual edit. A failure logs
       ``{log_prefix}source_stage_failed`` and proceeds.
    4. `reconcile_comments_on_checkpoint` — best-effort orphan/re-attach of
       comments whose anchor vanished from this build. A failure logs
       ``comments_reconcile_failed`` (same key on both paths) and proceeds.

    Returns ``(checkpoint_id, bundle_url)`` where `bundle_url` is the STABLE
    app-origin proxy base from `authed_bundle_url(prototype_id)` — never the
    server-side signed object URL `stage_bundle` returns.
    """
    # Step: checkpoint row (id seeds the bundle prefix). prd/figma hashes +
    # comment_state land later; for now the checkpoint records the bundle only.
    checkpoint_id = create_checkpoint(
        prototype_id=prototype_id,
        workspace_id=workspace_id,
        bundle_url=None,            # populated on the prototype row after staging
        prd_revision_hash=None,     # PRD-hash + figma-hash wired later
        figma_frame_hash=None,
        prompt_history=prompt_history,
        comment_state=[],
    )

    # Step: stage the BUILT dist/ (never raw virtual_fs). Raises on failure —
    # the caller's except wraps this exactly as it did pre-extraction. The signed
    # URL returned here is server-side only and never browser-facing (NO-BYPASS).
    await stage_bundle(
        prototype_id=prototype_id,
        checkpoint_id=checkpoint_id,
        files=dist_files,
    )

    # Step: stage the RAW virtual_fs under _source/ so the export serialiser and
    # the NEXT iterate can read real TSX, not minified bundles. Best-effort: a
    # source-stage failure logs and proceeds — the load-bearing artefact (dist/)
    # is already staged.
    try:
        await stage_bundle(
            prototype_id=prototype_id,
            checkpoint_id=checkpoint_id,
            files=virtual_fs,
            sub_prefix="_source",
        )
    except Exception as exc:  # noqa: BLE001 — source-stage is best-effort.
        logger.warning(
            "%ssource_stage_failed prototype_id=%s checkpoint_id=%s error_class=%s",
            log_prefix, prototype_id, checkpoint_id, type(exc).__name__,
        )

    # Step: orphan/re-attach every OPEN comment whose anchor vanished from THIS
    # build's bundle. Path-agnostic (keys on prototype_id, not checkpoint_id).
    # Best-effort: the bundle is already staged, so a reconcile failure must NOT
    # fail the build — it logs and the run still proceeds.
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

    # NO-BYPASS migration: the persisted bundle_url is the STABLE app-origin PROXY
    # base, not the 24h signed object URL. Computed here so neither caller repeats it.
    return checkpoint_id, authed_bundle_url(prototype_id)


async def _stage_complete_run(
    *,
    prototype_id: int,
    workspace_id: str,
    virtual_fs: dict[str, str],
    system_blocks: list[dict] | None = None,
    figma_file_key: str | None = None,
    figma_node_id: str | None = None,
    scenario: str = "A",
    theme_expectations: ThemeExpectations | None = None,
    interactive_scope: list[str] | None = None,
    parity_located: "LocatedScreen | None" = None,
    repair_usage: RunUsage | None = None,
) -> bool:
    """Post-run hook: vite_build → checkpoint → stage_bundle → complete.

    Returns True when the prototype reached 'ready' (staged), False when a build/
    stage failure routed the row to `fail_prototype`. The caller uses this to set
    the matching usage-ledger terminal status. `repair_usage`, when passed, is the
    accumulator the build-repair loop sums its (separate-agent-loop) token usage
    into so the caller's ledger row reflects primary + repair tokens.

    Four steps, each gating the next:

    1. **Vite build** runs the anchor-id plugin over the agent's raw TSX
       (load-bearing for comments, manual edits, and share). A build failure (bad JSX, missing
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
    # Step 1 — Vite build (anchor-id plugin runs here). The bounded
    # unresolved-relative-import repair wrapper stubs/strips an orphan `./screens/*`
    # import (the degrade-converged 2/2-repro) and rebuilds instead of shipping
    # status=failed; on exhaustion it raises UnresolvedImportRepairExhausted (a
    # ViteBuildError subclass — caught by the tuple below, distinct error_class).
    # vite_build_with_repair returns the (possibly) REPAIRED virtual_fs as its
    # second element; we REBIND `virtual_fs` here, BEFORE the `_source/` staging
    # step below, so the staged source matches the built dist.
    publish_step(prototype_id, VITE_PHASE_STEP)
    try:
        dist_files, repaired_virtual_fs = await vite_build_with_repair(virtual_fs)
        # Fail-closed acceptance gate: a green build that still renders the scaffold
        # placeholder (the agent wrote components but never wired an entry point)
        # must be repaired, never shipped ready. Runs BEFORE the theme/checkpoint
        # steps so a placeholder bundle never reaches staging. Raises
        # PlaceholderShippedError → handled below (repair-then-fail).
        assert_mounts_generated_content(dist_files)
        # Theme-bridge assertion gate (codebase-recreate path only). Runs BEFORE
        # checkpoint creation so a theme-miss fails the row without staging an
        # unstyled bundle. No-op for Scenario A/B blank-canvas runs (None).
        if theme_expectations is not None:
            assert_theme_landed(dist_files, theme_expectations)
            logger.info(
                "design_agent.theme_landed prototype_id=%s n_token_signals=%d n_fonts=%d asset_present=%s",
                prototype_id,
                len(theme_expectations.token_signals),
                len(theme_expectations.font_families),
                str(bool(theme_expectations.asset_basename)).lower(),
            )
        # Interactivity-containment self-check (recreate path only). A SIBLING of
        # the theme gate above — gated on its own scope, independent of
        # theme_expectations, so it runs on ANY scoped recreate run. Greps the
        # agent's GENERATED SOURCE (the .tsx/.jsx bodies), where the event
        # handlers live BEFORE the build strips them — NOT the built dist that
        # assert_theme_landed inspects. `virtual_fs` here is still the raw emitted
        # source (the rebind to the repaired map happens further below), which is
        # the correct containment target.
        #
        # Policy: LOG + FLAG on a containment miss, never block. The inert-
        # affordance UX rule (a silent no-op control reading as broken) is still an
        # open product decision, so a miss is surfaced as telemetry, not used to
        # fail a prototype that otherwise built and themed cleanly — every
        # prototype that completes today still completes. The warning log line is
        # the observable flag (no DB column). A future ticket can flip this to a
        # hard block once that product decision lands. Wrapped defensively so an
        # unexpected error in the pure-regex check can never fail the row.
        if interactive_scope:
            try:
                generated_source = "\n".join(
                    body for path, body in virtual_fs.items()
                    if path.endswith((".tsx", ".jsx"))
                )
                report = assert_containment(generated_source, interactive_scope)
                if report.ok:
                    logger.info(
                        "design_agent.containment prototype_id=%s ok=%s n_extra_handlers=%d n_inert=%d href_count=%d",
                        prototype_id,
                        str(report.ok).lower(),
                        len(report.extra_handlers),
                        len(report.inert_without_affordance),
                        report.href_count,
                    )
                else:
                    logger.warning(
                        "design_agent.containment prototype_id=%s ok=%s n_extra_handlers=%d n_inert=%d href_count=%d",
                        prototype_id,
                        str(report.ok).lower(),
                        len(report.extra_handlers),
                        len(report.inert_without_affordance),
                        report.href_count,
                    )
            except Exception:  # noqa: BLE001 — non-fatal: never fail a row on a self-check bug
                logger.warning(
                    "design_agent.containment_check_errored prototype_id=%s",
                    prototype_id,
                )
        # Structural-parity self-check (recreate path only). A SIBLING of the
        # containment + theme gates: it greps the agent's GENERATED SOURCE for the
        # real shell brand, nav labels, and composed-component names the recreate
        # was handed (from the located screen's extracted shell + node). Source-
        # grounded — NO DOM, NO live-URL. Same policy as containment: LOG + FLAG a
        # parity gap, NEVER block — recognizability drift is a quality signal, not
        # a safety gate, so a prototype that built + themed cleanly still
        # completes. Wrapped defensively so a self-check bug can never fail the row.
        if parity_located is not None:
            try:
                parity_source = "\n".join(
                    body for path, body in virtual_fs.items()
                    if path.endswith((".tsx", ".jsx"))
                )
                parity = _assert_structural_parity(
                    parity_source,
                    None,
                    parity_located.map_result.shell,
                    parity_located,
                )
                log_parity = logger.info if parity.ok else logger.warning
                log_parity(
                    "design_agent.structural_parity prototype_id=%s matched=%d missing=%d extra=%d ok=%s "
                    "missing_refs=%s extra_refs=%s",
                    prototype_id,
                    len(parity.matched),
                    len(parity.missing),
                    len(parity.extra),
                    str(parity.ok).lower(),
                    parity.missing,
                    parity.extra,
                )
            except Exception:  # noqa: BLE001 — non-fatal: never fail a row on a self-check bug
                logger.warning(
                    "design_agent.structural_parity_check_errored prototype_id=%s",
                    prototype_id,
                )
    except PlaceholderShippedError as exc:
        # Green build, but the bundle still renders the scaffold placeholder — the
        # agent's UI was never wired into an entry point. With agent context
        # (system_blocks present) re-enter the repair loop with the entry-point
        # directive: the loop re-asserts this gate each pass and, on exhaustion,
        # honest-fails (a placeholder is not strip-to-green recoverable). Without
        # agent context (a direct staging call), fail precisely. NEVER complete a
        # prototype while the sentinel is present.
        if system_blocks is None:
            logger.warning(
                "placeholder_shipped prototype_id=%s scenario=%s error_class=placeholder_shipped",
                prototype_id, scenario,
            )
            fail_prototype(
                prototype_id=prototype_id,
                workspace_id=workspace_id,
                error=f"placeholder_shipped: {exc}",
            )
            return False
        try:
            dist_files, repaired_virtual_fs = await _build_repair_loop(
                prototype_id=prototype_id,
                workspace_id=workspace_id,
                virtual_fs=virtual_fs,
                system_blocks=system_blocks,
                figma_file_key=figma_file_key,
                figma_node_id=figma_node_id,
                scenario=scenario,
                first_diagnostics=_ENTRY_POINT_REPAIR_DIRECTIVE,
                repair_usage=repair_usage,
            )
        except (PlaceholderShippedError, ViteBuildError, FileNotFoundError, TypeCheckError) as repair_exc:
            logger.warning(
                "placeholder_repair_failed prototype_id=%s scenario=%s error_class=placeholder_shipped",
                prototype_id, scenario,
            )
            fail_prototype(
                prototype_id=prototype_id,
                workspace_id=workspace_id,
                error=f"placeholder_shipped: {repair_exc}",
            )
            return False
    except (TypeCheckError, ViteBuildError) as exc:
        # A model-fixable build failure does NOT fail outright. This covers a
        # runtime-breaking type error (most often a screen the agent imported but
        # was cut off before writing) AND a generic `vite build` error whose
        # headline names a fixable cause (e.g. an `@apply` of an undefined utility
        # class, or an unresolved import the build's own repair could not stub).
        # Re-enter the agent a few times to fix the offending source; on exhaustion
        # the recovery is class-aware (strip-to-green for the import/typecheck class,
        # honest-fail for any other build error). Only if we have the agent context
        # to re-enter with — a direct staging call without it falls back to failing
        # precisely, as before.
        if system_blocks is None:
            logger.warning(
                "vite_build_failed prototype_id=%s error_class=%s",
                prototype_id, type(exc).__name__,
            )
            fail_prototype(
                prototype_id=prototype_id,
                workspace_id=workspace_id,
                error=f"{type(exc).__name__}: {exc}",
            )
            return False
        try:
            dist_files, repaired_virtual_fs = await _build_repair_loop(
                prototype_id=prototype_id,
                workspace_id=workspace_id,
                virtual_fs=virtual_fs,
                system_blocks=system_blocks,
                figma_file_key=figma_file_key,
                figma_node_id=figma_node_id,
                scenario=scenario,
                first_diagnostics=str(exc),
                repair_usage=repair_usage,
            )
        except (ViteBuildError, FileNotFoundError, TypeCheckError) as repair_exc:
            logger.warning(
                "typecheck_repair_failed prototype_id=%s error_class=%s",
                prototype_id, type(repair_exc).__name__,
            )
            fail_prototype(
                prototype_id=prototype_id,
                workspace_id=workspace_id,
                error=f"{type(repair_exc).__name__}: {repair_exc}",
            )
            return False
    except (FileNotFoundError, ThemeBridgeError) as exc:
        # The fail-fast path: a missing runtime/scaffold file (not model-fixable) or
        # a theme-bridge miss (not a build error) fails the row. A ViteBuildError is
        # NOT handled here — it routes into the agent-driven repair loop above,
        # alongside TypeCheckError. For ThemeBridgeError the log names the missing
        # signals; for the missing-file case it names the error class.
        if isinstance(exc, ThemeBridgeError):
            logger.warning(
                "theme_bridge_failed prototype_id=%s missing=%s",
                prototype_id, str(exc),
            )
        else:
            logger.warning(
                "vite_build_failed prototype_id=%s error_class=%s",
                prototype_id, type(exc).__name__,
            )
        fail_prototype(
            prototype_id=prototype_id,
            workspace_id=workspace_id,
            error=f"{type(exc).__name__}: {exc}",
        )
        return False
    # Rebind to the repaired source BEFORE the `_source/` staging step so
    # the staged source matches the built dist. On a clean build this is the same
    # map. When a repair was applied (the map changed), emit build_repair_applied
    # with an action count only (no source / no import paths): stubs add
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

    # Step 2 — checkpoint the build, stage the dist/ bundle, stage the raw source,
    # and reconcile comments (shared with the iterate path). Fail-closed on the
    # dist stage (routed to THIS function's fail_prototype below); best-effort on
    # source-stage + reconcile. log_prefix="" reproduces the exact
    # "source_stage_failed" key (no leading underscore, no prefix).
    try:
        checkpoint_id, bundle_url = await _stage_checkpoint_and_bundle(
            prototype_id=prototype_id,
            workspace_id=workspace_id,
            dist_files=dist_files,
            virtual_fs=virtual_fs,
            prompt_history=[],
            log_prefix="",
        )
    except Exception as exc:  # noqa: BLE001 — surface staging failure on the row.
        fail_prototype(
            prototype_id=prototype_id,
            workspace_id=workspace_id,
            error=f"{type(exc).__name__}: {exc}",
        )
        return False

    # Step 3.7 — best-effort preview screenshot of the staged bundle. HONEST-DEGRADE:
    # a capture that returns no image (no browser runtime / nav error / timeout), or
    # raises, leaves preview_image_url None and the prototype STILL completes ready.
    # No fake/placeholder image is ever stored. Runs on the success path only — a
    # build failure returned above before reaching here. When a Chromium runtime is
    # provisioned on the host the thumbnail just works; until then it degrades to null.
    preview_image_url = None
    try:
        # Render LOCALLY from the built dist/ files, NOT the signed Supabase URL:
        # the SPA's relative ./assets/* module scripts cannot resolve under a
        # per-object signature, so a signed-URL capture only ever paints the
        # un-hydrated #root shell. capture_bundle_screenshot serves dist_files
        # over a loopback static server so the SPA hydrates and the screenshot
        # captures the rendered app.
        png = await capture_bundle_screenshot(dist_files)
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
    # NO-BYPASS migration (plan §8-A/B): the persisted bundle_url is the STABLE
    # app-origin PROXY base returned by `_stage_checkpoint_and_bundle`, NOT the
    # 24h Supabase signed object URL (that signed URL was used only for the
    # server-side screenshot capture and is never browser-facing). The authed
    # surface serves this proxy base via the da_view_grant cookie; the public
    # surface re-derives a by-token proxy URL on read (_public_bundle_url). Signing
    # happens inside the proxy handler, sign-on-read, so a browser never receives a
    # direct/signed object URL.
    complete_prototype(
        prototype_id=prototype_id,
        workspace_id=workspace_id,
        bundle_url=bundle_url,
        current_checkpoint_id=checkpoint_id,
        preview_image_url=preview_image_url,
    )
    return True


def _load_prd_body(prd_id: int) -> str:
    """Fetch the PRD's `payload_md` for the agent's user message.

    Uses `get_prd_rendered` (db/prds.py) so the body the agent sees in its
    iterate user-message reflects accepted (status='applied') prd_patches folded
    in at read time (render-on-read). Like the underlying `get_prd`, this is
    NOT workspace-scoped — PRDs predate the workspace_id primitive, and
    `routes/prd.py` reads them the same way under its own auth dependency. This
    is the documented fallback: the route's `require_app_session` gate is
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

    Why this is load-bearing (verifier finding, 2026-06-02): the extractor's
    own ``_below_confidence`` guard only checks for an EMPTY color string, so a
    NON-empty transparent value like ``rgba(0,0,0,0)`` — which live runs
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


def _website_design_system_block(
    sample: dict[str, Any],
    *,
    host: str,
    manual_design: "ManualDesignInput | None",
) -> str:
    """Render extracted website signals via the unified DesignSystem path.

    This mirrors the pre-seed source: the sampled website dict is wrapped by the
    website adapter, normalized to DesignSystem tokens, and summarized for the
    scaffold. Transparent / zero-alpha colors keep the legacy field-level floor:
    extracted typography/radius/spacing survive, while the primary color comes
    from manual hints or the neutral default.
    """
    from app.design_agent.design_system.adapters import WebExtractor

    extractor = WebExtractor()
    raw = extractor.extract_raw_signals(f"https://{host}", sample=sample)
    design_system = extractor.normalize(raw)
    tokens = design_system.tokens

    parts: list[str] = [
        f"Design system extracted from the brand website ({host}). "
        "Match this visual identity in the prototype."
    ]

    has_usable_accent = any(
        _is_usable_color(c.get("color"))
        for c in (sample.get("color_candidates") or [])
    )
    if has_usable_accent:
        parts.append(f"Primary color: {tokens.colors.primary}.")
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

    background = sample.get("background_color")
    if _is_usable_color(background):
        parts.append(f"Background color: {tokens.colors.background}.")

    if sample.get("heading_font_family"):
        parts.append(f"Heading font: {tokens.fonts.heading_family}.")
    if sample.get("heading_size_scale"):
        parts.append(f"Heading size: {sample['heading_size_scale']}.")
    if sample.get("body_font_family"):
        parts.append(f"Body font: {tokens.fonts.body_family}.")
    if sample.get("border_radius_convention"):
        parts.append(f"Border radius: {tokens.radius_convention}.")
    spacing = sample.get("spacing_scale_samples") or []
    if spacing:
        parts.append(
            "Spacing samples: "
            + ", ".join(f"{value}px" for value in tokens.spacing_scale)
            + "."
        )
    if sample.get("logo_url"):
        parts.append(f"Logo: {sample['logo_url']}.")

    return " ".join(parts)


async def _extract_website_sample(website_url: str | None) -> dict | None:
    """Run the headless-browser website extractor once for `website_url`.

    Returns the sampled design-system dict, or None on a low-confidence sample,
    an extractor failure, or no URL. Isolated as its own helper so the same
    sample feeds BOTH the scaffold prose block and the design-system pre-seed
    without paying for two browser runs. The import is lazy + ImportError-guarded
    so the rest of the generate path ships even when the extractor is absent.
    """
    if not website_url:
        return None
    try:
        from app.design_agent.scenarios.website import (
            extract_website_design_system,
        )
        return await extract_website_design_system(website_url)
    except ImportError:
        return None


async def _website_context_block(
    website_url: str | None,
    manual_design: "ManualDesignInput | None",
    *,
    extracted_ds: dict | None = None,
    extracted_ds_resolved: bool = False,
) -> str | None:
    """Scaffold context for Scenario B (analog of `_figma_context_block`).

    Returns a prose design-system block, or ``None`` when there is no website
    source at all (the caller then uses the Figma block / the generic
    "(no source)" string). Precedence: extracted > manual > url-only.

      1. ``website_url`` set → ``extract_website_design_system``. On a
         non-``None`` dict, render it (with the transparent-color gate).
      2. extractor ``None`` + ``manual_design`` present → manual prose.
      3. extractor ``None`` + no manual + a URL was given → url-only neutral block.
      4. no ``website_url`` but ``manual_design`` present → manual prose
         (Scenario-0-with-manual-hints; the run is labelled '0' because
         `infer_scenario_from_inputs` keys 'B' off `website_url`, but the hints
         MUST still reach the scaffold — decision 2026-06-02).
      5. neither → ``None``.

    The extractor import is lazy + ImportError-guarded so the manual-floor half
    ships independently of the extractor.
    """
    if website_url:
        host = urlsplit(website_url).hostname or website_url
        ds: dict[str, Any] | None = None
        if extracted_ds_resolved:
            # The caller already ran extraction once (and is reusing the sample
            # for the design-system pre-seed). Reuse it here rather than paying
            # for a second browser run.
            ds = extracted_ds
        else:
            try:
                from app.design_agent.scenarios.website import (
                    extract_website_design_system,
                )
                ds = await extract_website_design_system(website_url)
            except ImportError:
                ds = None  # extractor not merged → fall through to manual / url-only.
        if ds is not None:
            return _website_design_system_block(ds, host=host, manual_design=manual_design)
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


# ─── Public share viewer ───────────────────────────────────────────────────
#
# These two routes back the unauthenticated `/p/<token>` viewer (web/app/p/[token]).
# They are NO-AUTH BY DESIGN: the share_token IS the access primitive, so
# they carry NO `require_app_session` dependency and NO workspace filter —
# `find_prototype_by_share_token` is the one legitimate cross-workspace read
# (see db/prototypes.py). Both are feature-flag-gated via the shared
# `_require_feature_enabled()` so a brute-force scan returns 404, matching the
# auth'd routes' invisibility posture (404-not-401).
#
# The response is MINIMUM-DISCLOSURE: exactly four fields. No prototype_id,
# prd_id, workspace_id, instructions, figma_file_key, created_at, or error ever
# leaves this surface — that reduces the cross-tenant fingerprinting surface to
# what the viewer strictly needs to render. response_model enforces the exact
# key set even if the row carries more columns.


def _share_token_hash(token: str) -> str:
    """sha256 prefix of a share token, for log correlation only.

    The full token must NEVER reach log aggregation: it is the access primitive,
    so logging it verbatim is equivalent to logging the share URL. An 8-char
    sha256 prefix correlates a resolve/deny pair for one token without being
    reversible to the token (or to anything PII).
    """
    return hashlib.sha256(token.encode("utf-8")).hexdigest()[:8]


def _public_bundle_url(row: dict[str, Any]) -> str | None:
    """Bundle URL for a public/passcode view — the app-origin PROXY base.

    NO-BYPASS migration (plan §8-D): a public/passcode share resolves to the
    same-origin bundle proxy keyed by the share token in the path
    (`…/by-token/<token>/bundle/index.html`), NOT a direct/signed Supabase URL.
    The proxy authorizes (re-resolves share_mode per asset GET) and signs-on-read
    server-side, so the browser never receives a signed object URL and the share
    never expires (no 24h TTL on the public URL). Returns None when the row has
    no share_token (a token is minted at start_prototype, so this is defensive)."""
    token = row.get("share_token")
    if not token:
        return None
    return public_bundle_proxy_url(token)


def _public_target_platform(row: dict[str, Any]) -> str:
    """Normalise a row's target_platform to the public display enum. Only the two
    single-device values pass through; everything else — "both", the legacy "web"
    value, null, or any unexpected string — collapses to "both" so the public
    viewer degrades to showing the toggle. Keeps the response contract to exactly
    {"desktop", "mobile", "both"}."""
    tp = (row.get("target_platform") or "").strip().lower()
    return tp if tp in ("desktop", "mobile") else "both"


def _public_cosmetic_slugs(row: dict[str, Any]) -> tuple[str, str]:
    """Compute the two cosmetic path segments for /p/<company>/<feature>/<token>.
    Derived at SERVE TIME, never persisted. Fail-soft: a missing/empty
    display_name or PRD title degrades to a fixed fallback rather than raising —
    a public visitor must never see a 500 because a cosmetic lookup failed. Any
    exception is caught, logged (identifiers only — no display_name/title
    content), and degrades the same way.
    """
    try:
        display_name = display_name_for_company_id(row["workspace_id"]) or ""
        prd = get_prd_rendered(row["prd_id"]) if row.get("prd_id") else None
        title = (prd or {}).get("title") or ""
    except Exception:
        logger.warning(
            "design_agent.public_cosmetic_slugs_failed workspace_id=%s prd_id=%s",
            row.get("workspace_id"), row.get("prd_id"), exc_info=True,
        )
        display_name, title = "", ""
    return url_slugify(display_name, fallback="company"), url_slugify(title, fallback="prototype")


class PublicPrototypeView(BaseModel):
    share_mode: Literal["public", "passcode"]  # "private" is never returned
    requires_passcode: bool                    # true iff share_mode == "passcode"
    bundle_url: str | None                     # null until a passcode is verified
    is_complete: bool
    company_slug: str                          # cosmetic segment of /p/<slug>/<token>
    # Human-readable cosmetic segments for the 3-segment canonical URL
    # /p/<company_display_slug>/<feature_slug>/<token>. Computed at serve time
    # from companies.display_name / prds.title — no new column, never validated
    # on read (same trust model as company_slug).
    company_display_slug: str = Field("")
    feature_slug: str = Field("")
    # Benign display enum ("desktop" | "mobile" | "both") — lets the public viewer
    # hide the Desktop/Mobile toggle for a single-device prototype (there is
    # nothing to toggle to) and show a static device badge instead. Reveals nothing
    # about content or ownership, so it is safe on the minimum-disclosure public
    # payload (analogous to the already-exposed is_complete / company_slug).
    target_platform: str = Field("both")


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
    # Per-token view rate limit (60/min/token). Mounted AFTER the feature
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
    company_display_slug, feature_slug = _public_cosmetic_slugs(row)
    return PublicPrototypeView(
        share_mode=mode,
        requires_passcode=(mode == "passcode"),
        # bundle_url is released for public mode immediately; for passcode mode it
        # stays null here and is only returned by the verify route on success.
        # A public/passcode share is permanent but the stored bundle_url is a 24h
        # signed URL — re-sign on read so the iframe never 403s once the TTL lapses.
        bundle_url=_public_bundle_url(row) if mode == "public" else None,
        is_complete=bool(row.get("is_complete")),
        # INTENTIONAL slug exposure (intentional, reviewed): companies.slug is the cosmetic segment of the public /p/<slug>/<token> URL — the ONE surface overriding the "slug is internal, never render" convention (api.ts:163, brief.py:34).
        company_slug=slug_for_company_id(row["workspace_id"]) or "",
        # Human-readable cosmetic segments for /p/<company>/<feature>/<token>.
        company_display_slug=company_display_slug,
        feature_slug=feature_slug,
        # Null/legacy ("web") rows collapse to "both", so an older row degrades to
        # the always-toggle behaviour.
        target_platform=_public_target_platform(row),
    )


@router.post("/by-token/{token}/passcode", response_model=PublicPrototypeView)
def verify_passcode(
    token: str, body: PasscodeAttempt, request: Request, response: Response
) -> PublicPrototypeView:
    """Verify a passcode against a passcode-mode share; on success return the
    bundle_url. Rate-limited 5/min/token. The rate-limit check
    runs FIRST so counter exhaustion is observable as 429 BEFORE any hash
    comparison; under the limit, a wrong passcode returns 401 `invalid_passcode`.
    404 (not 401) for a bad/non-passcode/not-ready token preserves invisibility.
    """
    _require_feature_enabled()
    th = _share_token_hash(token)
    # Rate-limit FIRST: a token over the limit gets 429 before we touch the
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
    # Bundle-proxy (plan §5): set the scoped da_share_grant cookie so the iframe's
    # subsequent asset GETs to the proxy authenticate. The cookie is HMAC-bound to
    # this token + checkpoint; fails closed (503) on an unset token secret. Set
    # only when there is a checkpoint to bind (a ready share always has one).
    _cp = row.get("current_checkpoint_id")
    if _cp is not None:
        from app.routes.design_agent_bundle import set_share_grant_cookie

        set_share_grant_cookie(response, token=token, checkpoint_id=_cp)
    logger.info("prototype_public_view_resolved token_hash=%s share_mode=passcode", th)
    company_display_slug, feature_slug = _public_cosmetic_slugs(row)
    return PublicPrototypeView(
        share_mode="passcode",
        requires_passcode=True,
        # Permanent share, 24h stored signed URL — re-sign on read (see get_by_token).
        bundle_url=_public_bundle_url(row),
        is_complete=bool(row.get("is_complete")),
        company_slug=slug_for_company_id(row["workspace_id"]) or "",
        # Human-readable cosmetic segments for /p/<company>/<feature>/<token>.
        company_display_slug=company_display_slug,
        feature_slug=feature_slug,
        # Same normalisation as get_by_token so a passcode-unlocked single-device
        # prototype also gates its toggle.
        target_platform=_public_target_platform(row),
    )


# ─── Lifecycle: Mark Complete / Resume Iteration / Set Share ──────────────────
#
# Complete locks a prototype; resume unlocks it AND flags any open
# downstream handoff (the most-recent export row) as stale per spec §8;
# share sets share_mode/token/passcode. All three reuse `require_app_session`
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
    dependencies=[Depends(require_same_origin)],  # CSRF/Origin gate (authed mutating)
)
async def post_complete(
    prototype_id: int,
    company: CompanyContext = Depends(require_company),
) -> CompleteResponse:
    """Lock the prototype. Sets is_complete=true and promotes
    current_checkpoint_id → complete_checkpoint_id. Idempotent: a second
    /complete on an already-complete prototype is a no-op (200; no row change).
    Returns 404 if the prototype is not in the caller's workspace.
    Returns 409 if `status != 'ready'` (cannot mark a generating/failed/invalidated
    prototype complete).

    `async def` because the export-write hook (`record_export_at_complete`)
    is async — it awaits the markdown serialiser. The
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
    # The export-write hook (async) generates the markdown
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
    dependencies=[Depends(require_same_origin)],  # CSRF/Origin gate (authed mutating)
)
def post_resume(
    prototype_id: int,
    company: CompanyContext = Depends(require_company),
) -> ResumeResponse:
    """Unlock the prototype + flag any open handoff record as stale.

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


@router.post(
    "/{prototype_id}/dismiss-question",
    dependencies=[Depends(require_same_origin)],  # CSRF/Origin gate (authed mutating)
)
def post_dismiss_question(
    prototype_id: int,
    company: CompanyContext = Depends(require_company),
) -> dict[str, bool]:
    """Clear the prototype's pending clarifying question ("Skip this change").

    The user chose not to answer the open question, so it must be cleared
    server-side — the sidecar is the awaiting-answer signal, and leaving it set
    would keep prompting on every poll. Workspace-filtered: a row in a different
    workspace returns 404, not 403, so cross-tenant existence is not disclosed.
    Idempotent: dismissing a prototype with no open question is a 200 no-op.
    """
    _require_feature_enabled()
    workspace_id = company.company_id
    row = get_prototype(prototype_id=prototype_id, workspace_id=workspace_id)
    if not row:
        raise HTTPException(status_code=404, detail="Prototype not found")
    clear_pending_question(prototype_id=prototype_id, workspace_id=workspace_id)
    logger.info(
        "prototype_question_dismissed prototype_id=%s", prototype_id
    )
    return {"ok": True}


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
    dependencies=[Depends(require_same_origin)],  # CSRF/Origin gate (authed mutating)
)
def post_share(
    prototype_id: int,
    body: ShareRequest,
    company: CompanyContext = Depends(require_company),
) -> ShareResponse:
    """Set / update share configuration. Wraps set_share_config.

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


# ─── Export read ────────────────────────────────────────────────────────────
#
# Return the markdown brief of the locked checkpoint. The /complete
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
    """Return the markdown export of the locked checkpoint.

    Returns 409 when the prototype is not complete (is_complete=false).
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
        # WIP prototypes viewable but not exportable.
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


# ─── Iterate: re-prompt + Apply-driven edits ───────────────────────────────────
#
# A SEPARATE iterate prompt distinct from scaffold; this block lands the iterate
# spine. Re-prompt and Apply-on-comment (pre-fills the prompt) both route through
# `POST /{id}/iterate`:
#
#   POST /v1/design-agent/{prototype_id}/iterate  {prompt, applied_comment_id?, mode?}
#
# Cache discipline: the iterate system blocks + the current bundle source +
# the open comment threads form the STABLE cacheable prefix; the user's iterate
# prompt is the per-call volatile suffix (render_iterate_user owns the breakpoint).
#
# Staging: a complete iterate run stages via `_stage_iterate_run`, NOT
# `_stage_complete_run`. An iterate is a checkpoint ADVANCE, not a first
# completion, so it MUST NOT call `complete_prototype` (which re-stamps
# completed_at + emits prototype_completed). Advancing `current_checkpoint_id` +
# threading the new bundle_url onto the prototype row is the
# `advance_current_checkpoint` helper (stable URL, no share_token rotation),
# called at the tail of `_stage_iterate_run`.
#
# Mode: EXECUTE is the default; `mode='plan'` is now FULLY wired — the
# plan/execute tool split + the distinct plan system prompt + the
# Plan→Execute transition (`POST /{id}/iterate/confirm-plan`) all land here.
# Concurrency / queueing serialises plan + execute runs alike.
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
    applied_comment_id: int | None = None        # set when Apply pre-filled the prompt
    mode: Literal["plan", "execute"] = "execute"  # 'plan' and 'execute' modes


class IterateResponse(BaseModel):
    prototype_id: int
    status: str                                   # 'generating' (kicked off in the bg)
    queue_position: int                           # derived slot in the iterate queue


@router.post(
    "/{prototype_id}/iterate",
    response_model=IterateResponse,
    dependencies=[Depends(require_same_origin)],  # CSRF/Origin gate (authed mutating)
)
async def post_iterate(
    prototype_id: int,
    body: IterateRequest,
    company: CompanyContext = Depends(require_company),
) -> IterateResponse:
    """Kick off an iterate of an existing prototype; return in <200ms.

    Gates (identical posture to /generate): feature-flag (404 when off) +
    require_app_session (401) + workspace filter (404 cross-tenant). Two iterate-
    specific 409s:
      - `is_complete` (locked): cannot iterate until Resume Iteration.
      - `status != 'ready'`: cannot iterate a generating/failed/invalidated row.
    On success enqueues the iterate (5-slot queue), kicks the serial drain in
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

    # Spend control: per-prototype iterate rate limit — 6 calls/hr.
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

    # Enqueue instead of firing a raw bg task. The queue caps at 5
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
    dependencies=[Depends(require_same_origin)],  # CSRF/Origin gate (authed POST)
)
async def post_iterate_estimate(
    prototype_id: int,
    body: EstimateRequest,
    company: CompanyContext = Depends(require_company),
) -> dict:
    """Pre-flight cost estimate: return the token + dollar estimate + soft-cap
    warning the CostEstimateModal renders BEFORE an iterate run. Deterministic, no
    Anthropic call in the request path — cancelling the modal costs nothing.

    Gates mirror POST /iterate exactly: feature-flag (404 when off) + require_app_session
    (401) + workspace filter (404 cross-tenant). Empty prompt → 422 (min_length=1).

    Route placement: this is a POST on a 3-segment path (`/{id}/iterate/estimate`); the
    `GET /{prototype_id}` catch-all cannot shadow it (different method AND more segments),
    matching the existing `POST /{id}/iterate` + `/iterate/confirm-plan` siblings.

    Observability: logs identifiers + token counts only — never the prompt or
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
    """Plan->Execute transition body. The team reviewed the plan a
    `mode='plan'` run emitted and approved (or refined) it; `plan` carries the
    approved text back, `prompt` is the iterate request the plan was for."""
    prompt: str = Field(..., min_length=1, max_length=8000)
    plan: str = Field(..., min_length=1, max_length=8000)
    applied_comment_id: int | None = None


@router.post(
    "/{prototype_id}/iterate/confirm-plan",
    response_model=IterateResponse,
    dependencies=[Depends(require_same_origin)],  # CSRF/Origin gate (authed mutating)
)
async def post_confirm_plan(
    prototype_id: int,
    body: ConfirmPlanRequest,
    company: CompanyContext = Depends(require_company),
) -> IterateResponse:
    """Plan->Execute transition: run the approved plan in EXECUTE mode.

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


def _project_open_comments_for_grounding(
    all_comments: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Project OPEN, ANCHORED comments to the {anchor_id, body, author} shape the
    iterate prompt renderer expects ("apply this comment to element X").

    General (unpinned) comments have no anchor_id -- they carry prototype-level
    feedback, not a "go change this element" instruction -- so they are
    excluded entirely rather than surfacing a bogus `data-anchor-id="None"`
    reference in the prompt. Extracted to a standalone function (rather than
    inlined in `_run_iterate_bg`) so the null-exclusion is independently
    testable without invoking the full background iterate run.
    """
    return [
        {"anchor_id": c["anchor_id"], "body": c["body"], "author": c["author"]}
        for c in all_comments if c.get("status") == "open" and c.get("anchor_id")
    ]


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
    (positional args, async, storage-path read, NOT workspace-filtered) to
    pre-fill the agent's virtual_fs. On any exception the row is marked failed in
    the existing Sprntly error format; the prior bundle_url is preserved.

    PLAN vs EXECUTE, keyed on `body.mode`:
      - 'plan'    : uses DESIGN_AGENT_PLAN_SYSTEM + the plan tool registry (no
                    write/line_replace). On completion the emitted textual plan is
                    persisted to the queue row (`set_iteration_plan`, needs
                    `iteration_id`) and the run stages NOTHING — a plan builds no
                    bundle and advances no checkpoint. A plan run NEVER fails
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

        # Open comment threads — the stable cacheable signal (list_comments,
        # filtered to open, anchored-only). See _project_open_comments_for_grounding.
        all_comments = list_comments(prototype_id=prototype_id, workspace_id=workspace_id)
        open_comments = _project_open_comments_for_grounding(all_comments)

        # Applied-comment: workspace-filtered (it came from the same
        # list_comments read, which filters by workspace) lookup by id, projected
        # to {anchor_id, body}. None when no applied_comment_id or no match.
        # `.get` (not `[...]`) — a general comment's anchor_id key can be null.
        applied_comment = None
        if body.applied_comment_id is not None:
            applied_comment = next(
                (
                    {"anchor_id": c.get("anchor_id"), "body": c["body"]}
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
        # System block(s) cached at the END of the stable prefix, mirroring
        # _run_generation_bg. The bundle+comments user prefix is cached too (its
        # last block carries cache_control); the volatile prompt block does not.
        # PLAN mode swaps in the distinct plan/discuss system prompt; the
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
            prd_references_codebase=False,  # the codebase detector lands later
        )
        scenario_label = ",".join(sorted(scenario_set))

        # Open a usage-ledger row for an EXECUTE iteration (billing/observability).
        # PLAN mode bills nothing and returns before staging, so it gets NO event.
        # Emitted here (not in post_iterate) because the iteration queue row stores
        # inputs only — there is no clean column to thread an id through; the bg
        # runner has prototype_id/prd_id/applied_comment_id in scope, so this is
        # the single-scope point that covers the queue+drain path. Fail-open: a
        # ledger failure must never block the iteration.
        iter_event_id: int | None = None
        if body.mode != "plan":
            try:
                iter_event_id = start_usage_event(
                    workspace_id=workspace_id,
                    kind="iteration",
                    prd_id=proto.get("prd_id"),
                    prototype_id=prototype_id,
                    trigger_comment_id=body.applied_comment_id,
                )
            except Exception:  # noqa: BLE001 — ledger is fail-open; identifiers only.
                logger.warning(
                    "usage_event_start_failed kind=iteration prototype_id=%s",
                    prototype_id,
                )

        try:
            await asyncio.to_thread(
                clear_pending_question,
                prototype_id=prototype_id,
                workspace_id=workspace_id,
            )
        except Exception:
            pass

        result, virtual_fs = await iterate_prototype(
            prototype_id=prototype_id,
            workspace_id=workspace_id,
            system_blocks=system_blocks,
            user_message=user_message,
            current_source=current_source,
            figma_file_key=figma_file_key,
            scenario=scenario_label,
            # Tool-partition mode: canonical 'plan' or 'execute',
            # never 'iterate'. agent_loop -> tools_for_mode selects the registry.
            mode=body.mode,
            # Plan->Execute transition: the approved plan (if any) is prepended to
            # the system blocks as an addendum inside iterate_prototype.
            approved_plan=approved_plan,
        )

        # PLAN mode: persist the emitted plan, stage NOTHING (no checkpoint,
        # no bundle — a plan builds nothing). A plan run never fails the
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
            staged_ok = await _stage_iterate_run(
                prototype_id=prototype_id,
                workspace_id=workspace_id,
                virtual_fs=virtual_fs,
                iterate_prompt=body.prompt,
            )
            # Finalize the iteration ledger row. _stage_iterate_run owns the
            # prototype write (advance on success, fail on build error), so we
            # mirror it. Iterations do NOT run a separate build-repair loop, so
            # result.usage already covers every Anthropic call in the run — no
            # rollup needed (asserted in scope-confirm). Fail-open.
            if iter_event_id is not None:
                try:
                    finalize_usage_event(
                        event_id=iter_event_id,
                        workspace_id=workspace_id,
                        status="succeeded" if staged_ok else "failed",
                        usage=getattr(result, "usage", None),
                        model=MODEL,
                        error_class=None if staged_ok else "build_stage_failed",
                    )
                except Exception:  # noqa: BLE001 — ledger is fail-open.
                    logger.warning(
                        "usage_event_finalize_failed event_id=%s prototype_id=%s kind=iteration",
                        iter_event_id, prototype_id,
                    )
        elif result.status == "complete" and not virtual_fs:
            fail_prototype(
                prototype_id=prototype_id,
                workspace_id=workspace_id,
                error="iterate agent_loop completed but emitted no files",
            )
            _finalize_usage_event_failed(
                event_id=iter_event_id,
                workspace_id=workspace_id,
                prototype_id=prototype_id,
                error_class="no_files",
                kind="iteration",
            )
        elif result.status == "awaiting_clarification":
            # A clarifying_question terminal-PAUSE is NOT a failure.
            # The runner already persisted the question on `pending_question`;
            # leave the row in a clean PAUSED state (status='ready',
            # pending_question set, no completed_at, no error) so the
            # answer-resume iterate is NOT 409-blocked by `post_iterate`'s
            # `status != 'ready'` guard. Do NOT fail_prototype — that flip is
            # exactly the bug this fixes. (Iterate path only; the
            # generate-time pause is scoped out.)
            mark_awaiting_clarification(
                prototype_id=prototype_id,
                workspace_id=workspace_id,
            )
            # PAUSE, not completion: leave the 'started' ledger row OPEN. We only
            # ever bill 'succeeded'; the row is finalized when the answer-resume
            # iteration actually completes (which opens its own event).
            logger.info(
                "prototype_iterate_paused_awaiting_clarification prototype_id=%s",
                prototype_id,
            )
        else:
            # Mirror _run_generation_bg's structured failure: surface the
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
            _finalize_usage_event_failed(
                event_id=iter_event_id,
                workspace_id=workspace_id,
                prototype_id=prototype_id,
                error_class=getattr(result, "error_class", None)
                or f"status_{result.status}",
                kind="iteration",
            )
    except Exception as exc:  # noqa: BLE001 — bg task must never leak; row is failed.
        from app.design_agent.provider_errors import (
            classify_provider_error,
            is_alertable,
            safe_error_message,
        )

        # A provider exception can land here directly (raised outside the runner's
        # own terminal catch). Store ONLY the safe class + a fixed generic message
        # on the client-visible row — never the raw exception text. Raw text ⇒ log.
        cls = classify_provider_error(exc)
        logger.warning(
            "design_agent.iterate_failed prototype_id=%s error_class=%s classified=%s raw=%s",
            prototype_id, type(exc).__name__, cls.value, str(exc),
        )
        if is_alertable(cls):
            from app.design_agent.provider_alert import maybe_alert_provider_outage

            maybe_alert_provider_outage(cls, context={"prototype_id": prototype_id})
        # iter_event_id may be unbound if the failure preceded its assignment
        # (e.g. get_prototype raised); guard with locals() so the fail-open
        # finalize never itself raises a NameError.
        _finalize_usage_event_failed(
            event_id=locals().get("iter_event_id"),
            workspace_id=workspace_id,
            prototype_id=prototype_id,
            error_class=cls.value,
            kind="iteration",
        )
        fail_prototype(
            prototype_id=prototype_id,
            workspace_id=workspace_id,
            error=f"error_class={cls.value} | error_message={safe_error_message(cls)}",
        )


async def _run_one_iteration(row: dict[str, Any]) -> None:
    """Run a single dequeued queue row through the iterate body.

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
        # The queue row's `plan` column is the APPROVED plan for a confirm
        # row (prepended as a system addendum in execute mode) AND the write target
        # for a plan-mode run's emitted plan. `iteration_id` lets the plan branch
        # persist back to this row. `.get` tolerates pre-migration schemas (None).
        approved_plan=row.get("plan"),
        iteration_id=row.get("id"),
    )


def _extract_plan_text(final_content: list[dict[str, Any]]) -> str:
    """Concatenate the text blocks of a plan run's final assistant turn.

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
) -> bool:
    """Iterate-completion staging path. DELIBERATELY SEPARATE from
    `_stage_complete_run`: it does NOT call `complete_prototype`. An iterate
    is a checkpoint ADVANCE on an already-completed prototype, so re-stamping
    `completed_at` and emitting `prototype_completed` would be wrong — that whole
    separation is the point of this helper. Do NOT fold it back into the scaffold
    staging path.

    Returns True when the iterate staged (checkpoint advanced), False when a
    build/stage failure routed the row to `fail_prototype`. The caller uses this
    to set the matching usage-ledger terminal status.

    Steps: vite_build (anchor-id plugin runs here) → create_checkpoint (threading
    the iterate prompt into prompt_history) → stage_bundle (dist + raw _source so
    the NEXT iterate can read it back). Then the seam advances
    `current_checkpoint_id` + bundle_url WITHOUT a completed_at re-stamp.
    """
    # Step 1 — Vite build (anchor-id plugin runs here).
    try:
        dist_files = await vite_build(virtual_fs)
        # Fail-closed acceptance gate. Unlike generate, iterate has NO agent
        # re-entry context at this seam, so a placeholder build cannot be repaired
        # here — fail-closed rather than silently advance the checkpoint. (Iterate
        # edits existing source so rarely drops the entry, but must never ship the
        # placeholder.)
        assert_mounts_generated_content(dist_files)
    except PlaceholderShippedError as exc:
        logger.warning(
            "iterate_placeholder_shipped prototype_id=%s error_class=placeholder_shipped",
            prototype_id,
        )
        fail_prototype(
            prototype_id=prototype_id,
            workspace_id=workspace_id,
            error=f"placeholder_shipped: {exc}",
        )
        return False
    except (ViteBuildError, FileNotFoundError, TypeCheckError) as exc:
        # Cross-cutting seam: the type-check runs inside the shared
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
        return False
    logger.info(
        "iterate_vite_build_succeeded prototype_id=%s dist_file_count=%s",
        prototype_id, len(dist_files),
    )

    # Step 2 — checkpoint the build (threading the iterate prompt into
    # prompt_history), stage the dist/ bundle + raw source, and reconcile comments
    # (shared with the first-completion path). Fail-closed on the dist stage
    # (routed to THIS function's fail_prototype below); best-effort on source-stage
    # + reconcile. log_prefix="iterate_" reproduces the exact
    # "iterate_source_stage_failed" key.
    try:
        checkpoint_id, bundle_url = await _stage_checkpoint_and_bundle(
            prototype_id=prototype_id,
            workspace_id=workspace_id,
            dist_files=dist_files,
            virtual_fs=virtual_fs,
            prompt_history=[{"kind": "iterate", "prompt": iterate_prompt}],
            log_prefix="iterate_",
        )
    except Exception as exc:  # noqa: BLE001 — surface staging failure on the row.
        fail_prototype(
            prototype_id=prototype_id,
            workspace_id=workspace_id,
            error=f"{type(exc).__name__}: {exc}",
        )
        return False

    # Step 4 — Advance current_checkpoint_id + bundle_url WITHOUT a
    # completed_at re-stamp (NOT complete_prototype). This does not
    # rotate share_token / share_mode, so the public /p/<token> URL is unchanged
    # and now resolves to the new checkpoint's bundle. NO-BYPASS (plan §8): the
    # bundle_url returned by `_stage_checkpoint_and_bundle` is the STABLE app-origin
    # proxy base (not the staged signed object URL, which is server-side only); the
    # proxy signs-on-read per checkpoint.
    advance_current_checkpoint(
        prototype_id=prototype_id,
        workspace_id=workspace_id,
        checkpoint_id=checkpoint_id,
        bundle_url=bundle_url,
    )
    return True


# ─── PRD patches: accept / reject ───────────────────────────────────────────────
#
# Accept/reject resolve a PENDING `prd_patches` proposal. The companion
# LIST route (`GET /prd-patches`) + the `PrdPatchOut` model + `_patch_to_out` + the
# `prd_patches` import are declared ABOVE the `GET /{prototype_id}` catch-all (see
# the "PRD patches: list pending" block there) — the list path is a SINGLE segment
# and would otherwise be swallowed by the catch-all (FastAPI static-before-dynamic).
# These two POSTs are 3-segment (`/prd-patches/{id}/accept|reject`), unambiguous
# against the single-segment catch-all, so they stay at EOF and reuse the
# module-level `PrdPatchOut` / `_patch_to_out` / `mark_patch_*` symbols defined in
# that block. Same gate posture as the authed routes above: feature-flag 404 when
# off + require_app_session 401 + workspace 404 (cross-tenant invisibility).
# Sync handlers (mirrors get_one): FastAPI runs them in the threadpool.


@router.post(
    "/prd-patches/{patch_id}/accept",
    response_model=PrdPatchOut,
    dependencies=[Depends(require_same_origin)],  # CSRF/Origin gate (authed mutating)
)
def post_accept_patch(
    patch_id: int,
    company: CompanyContext = Depends(require_company),
) -> PrdPatchOut:
    """Accept a proposed PRD patch: flip its status to `applied`
    (`mark_patch_applied`) and return the updated row. The rendered PRD reflects the
    applied patch on its NEXT load (read path folds it in via
    `apply_patches_to_prd_md`); this route does NOT mutate `prds.payload_md` or the
    PrdScreen `contentEditable`. 404 when the patch is not in the caller's
    workspace (cross-tenant invisibility). Idempotent: re-accepting an
    already-applied patch is a no-op flip that returns the row."""
    _require_feature_enabled()
    workspace_id = company.company_id
    row = mark_patch_applied(patch_id=patch_id, workspace_id=workspace_id)
    if not row:
        raise HTTPException(status_code=404, detail="Patch not found")
    # Route-level state-transition log: identifiers only — never
    # patch_md / rationale (they can embed PRD body). Logged on the route's own
    # logger so the observability AC is satisfied at this surface.
    logger.info("prd_patch_applied patch_id=%s", patch_id)
    return PrdPatchOut(**_patch_to_out(row))


@router.post(
    "/prd-patches/{patch_id}/reject",
    response_model=PrdPatchOut,
    dependencies=[Depends(require_same_origin)],  # CSRF/Origin gate (authed mutating)
)
def post_reject_patch(
    patch_id: int,
    company: CompanyContext = Depends(require_company),
) -> PrdPatchOut:
    """Reject a proposed PRD patch: flip its status to `rejected`
    (`mark_patch_rejected`) and return the updated row. The PRD is unaffected
    (rejected patches are excluded by `apply_patches_to_prd_md`). 404 when not in
    the caller's workspace. Idempotent (mirrors accept)."""
    _require_feature_enabled()
    workspace_id = company.company_id
    row = mark_patch_rejected(patch_id=patch_id, workspace_id=workspace_id)
    if not row:
        raise HTTPException(status_code=404, detail="Patch not found")
    # Identifiers only — never patch_md / rationale.
    logger.info("prd_patch_rejected patch_id=%s", patch_id)
    return PrdPatchOut(**_patch_to_out(row))


# ─── Manual edit: commit-back ───────────────────────────────────────────────────
#
# The commit-back half of manual edit: when the user clicks "Save edits" in the
# ManualEditOverlay, the accumulated `{anchor_id, property, old_value,
# new_value}` triples are POSTed here. The visual change was ALREADY
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
# QUEUE DECISION: manual edit does NOT go through the iterate queue —
# it is a distinct, small, 2-iter operation. It runs as a single fire-and-forget bg
# task (held in `_inflight_tasks`, strong-ref discipline mirroring post_iterate).
# `queue_position` is always 0 in the response (kept in the shape for client parity
# with iterate). A manual edit that collides with an in-flight iterate is
# last-write-wins on `current_checkpoint_id` via advance_current_checkpoint
# (acceptable for MVP).
#
# Localized imports (mirror the iterate block near _run_iterate_bg): the manual-edit
# runner entrypoint + prompt symbols. _stage_iterate_run / read_source_files_for_checkpoint
# / fail_prototype / get_prototype / infer_scenario_from_inputs are already in module scope.
from app.design_agent.prompts import (
    DESIGN_AGENT_MANUAL_EDIT_SYSTEM,
    render_manual_edit_user,
)
from app.design_agent.runner import manual_edit_prototype


class ManualEditTriple(BaseModel):
    """One fixed-property visual edit (ManualEditTriple wire-shape). `old_value`
    is the pristine value at first selection; `new_value` is the value at Save. The
    closed `property` set matches the frontend's EditableProperty exactly."""
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
    dependencies=[Depends(require_same_origin)],  # CSRF/Origin gate (authed mutating)
)
async def post_manual_edit(
    prototype_id: int,
    body: ManualEditRequest,
    company: CompanyContext = Depends(require_company),
) -> ManualEditResponse:
    """Commit a batch of manual visual edits into the prototype source.

    Gates (identical posture to POST /iterate): feature-flag (404 when off) +
    require_app_session (401) + workspace filter (404 cross-tenant). Two 409s:
      - `is_complete` (locked): cannot edit until Resume Iteration.
      - `status != 'ready'`: cannot edit a generating/failed/invalidated row.
    On success fires `_run_manual_edit_bg` as a single bg task (NOT the iterate
    queue) and returns status='generating', queue_position=0 in <200ms. No
    Anthropic call in the request path (the LLM runs once, in the bg).
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

    # Observability: identifiers only — never the edit triples
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
    """Background manual-edit run: load the current bundle source, render the
    commit-only prompts (cache-disciplined), run the thin 2-iter agent loop, then
    stage the result via the iterate path (a manual edit is a checkpoint ADVANCE,
    not a first completion — reuses `_stage_iterate_run` verbatim).

    STALE-ANCHOR (fail-closed): the run is NOT pre-validated for anchor
    presence (the anchors live in the BUILT dist, not the staged `_source/` TSX).
    The DESIGN_AGENT_MANUAL_EDIT_SYSTEM prompt instructs the agent to `search` the
    source for each triple's element and, when it cannot resolve one, to end its
    turn WITHOUT editing. We detect that no-target outcome as "the run ended but the
    source is byte-identical to the seed" → `fail_prototype` with a loud
    `manual_edit: anchor … not found` error and NO checkpoint advance. The frontend
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
        # System block cached at the END of the stable prefix, mirroring
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
            # ADVANCE (no complete_prototype, no completed_at re-stamp; stable URL).
            await _stage_iterate_run(
                prototype_id=prototype_id,
                workspace_id=workspace_id,
                virtual_fs=virtual_fs,
                iterate_prompt="<manual edit>",
            )
        elif result.status == "complete":
            # Stale-anchor fail-closed: the run ended but committed NO source
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
        # error_class only in the structured log (no source / edit
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
