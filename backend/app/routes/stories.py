"""User-story endpoints — generate from a PRD, then push into ClickUp.

  POST /v1/stories/generate  {prd_id}            -> generated stories (no write)
  POST /v1/stories/lists                          -> ClickUp lists to pick a target
  POST /v1/stories/push      {list_id, stories}  -> create the stories in ClickUp

Generation and push are kept SEPARATE on purpose: generation never touches the
user's tracker, so the user reviews the stories before any are written. Push is
the explicit, outward-facing write. All routes require_company (tenant scoped).
"""
from __future__ import annotations

import asyncio
import itertools
import logging

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from app.auth import CompanyContext, require_company
from app.connectors import clickup_oauth, jira_oauth
from app.stories.generate import (
    PRDNotFoundError,
    Story,
    generate_user_stories,
)
from app.prd_runner import warm_impl_spec
from app.stories.push import (
    ClickUpNotConnectedError,
    JiraNotConnectedError,
    push_stories_to_clickup,
    push_stories_to_jira,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/v1/stories", tags=["stories"])

# ── Async story generation jobs ──────────────────────────────────────────────
# Breaking a PRD into tickets is a multi-minute LLM call. Running it inside the
# request (the old synchronous POST) made the Tickets tab hang/spin for minutes
# and risked proxy/browser timeouts. So generation is now fire-and-forget: POST
# schedules a background task and returns a job id immediately; the client polls
# GET /jobs/{id} until status is "ready" or "failed".
#
# The job store tracks only the in-flight generation; the produced stories are
# persisted to the `prd_tickets` table on completion (app.db.prd_tickets), so the
# tab serves them via GET /for-prd without re-running generation until the PRD
# changes. The in-memory store matches the single-uvicorn-worker pattern
# elsewhere (e.g. app/brief_runner.py); a restart only drops an in-flight job's
# poll (the tab re-kicks generation), never a completed set (that's in the DB).
# All dict mutations happen on the event loop (the to_thread result is applied
# back in the coroutine), so no lock is needed under the single-worker topology.
_jobs: dict[int, dict] = {}
_job_ids = itertools.count(1)
_JOBS_CAP = 200  # keep the store bounded over a long-running process
_inflight_tasks: set[asyncio.Task] = set()


def _prune_jobs() -> None:
    """Bound the in-memory store: drop the oldest finished jobs past the cap."""
    if len(_jobs) <= _JOBS_CAP:
        return
    finished = [jid for jid, j in _jobs.items() if j["status"] in ("ready", "failed")]
    for jid in sorted(finished)[: len(_jobs) - _JOBS_CAP]:
        _jobs.pop(jid, None)


class GenerateIn(BaseModel):
    prd_id: int | None = Field(default=None, ge=1)
    insight: str | None = None


class StoryIn(BaseModel):
    # Legacy core (always present).
    title: str
    body: str = ""
    acceptance_criteria: list[str] = Field(default_factory=list)
    priority: str | None = None
    route: str | None = None
    # Structured ticket fields (additive; the edited ticket carries these so the
    # push renders the full five-section description). Unknown/absent → defaults.
    ticket_type: str = "build"
    what: str = ""
    why_now: str = ""
    user_story: str = ""
    scope: list[str] = Field(default_factory=list)
    out_of_scope: str = ""
    prd_section: str = ""
    ears_ids: list[str] = Field(default_factory=list)
    signals: list[str] = Field(default_factory=list)
    ac_inherited: bool = False
    subtasks: list[str] = Field(default_factory=list)
    blocked_by: list[str] = Field(default_factory=list)
    blocks: list[str] = Field(default_factory=list)
    story_points: int | None = None
    labels: list[str] = Field(default_factory=list)
    data_gaps: list[str] = Field(default_factory=list)
    decision: str | None = None
    owner: str | None = None
    decide_by: str | None = None
    timebox: str | None = None
    exit_condition: str | None = None
    # Push-time only: Atlassian accountId to assign this ticket to on a Jira push
    # (per-ticket assignee picker). None = unassigned. Ignored for ClickUp.
    assignee_account_id: str | None = None


class PushIn(BaseModel):
    list_id: str = Field(..., min_length=1)
    stories: list[StoryIn] = Field(..., min_length=1)


class PushJiraIn(BaseModel):
    project_key: str = Field(..., min_length=1)
    stories: list[StoryIn] = Field(..., min_length=1)
    issue_type: str = Field(default="Task", min_length=1)


@router.post("/generate")
async def generate(
    body: GenerateIn,
    company: CompanyContext = Depends(require_company),
):
    """Kick off user-story generation from a PRD (or a free-form insight).

    Fire-and-forget: schedules the multi-minute generation in the background and
    returns a `job_id` immediately so the Tickets tab never blocks on a hung
    request. Poll GET /v1/stories/jobs/{job_id} until status is "ready" (carries
    `stories`) or "failed" (carries `error`). Generation never writes to ClickUp
    — call /v1/stories/push separately once the user has reviewed.
    """
    if (body.prd_id is None) == (body.insight is None):
        raise HTTPException(400, "provide exactly one of prd_id or insight")

    # Idempotent while in-flight: breaking a PRD into tickets is a multi-minute
    # call, and the Tickets tab re-kicks generation whenever it remounts (a tab
    # switch) before the first run has persisted — the cache read still 404s/sees
    # no fresh row, so the client falls through to /generate again. Re-attach
    # that rapid second call to the running job instead of starting a parallel,
    # wasteful one. Keyed by (company, prd_id|insight) since that's what the run
    # is over. Once a job is ready/failed it's persisted (PRD) or terminal, so we
    # only dedupe against still-"generating" jobs.
    existing = next(
        (
            j["id"]
            for j in _jobs.values()
            if j["status"] == "generating"
            and j["company_id"] == company.company_id
            and j.get("prd_id") == body.prd_id
            and j.get("insight") == body.insight
        ),
        None,
    )
    if existing is not None:
        return {"job_id": existing, "status": "generating"}

    # Pre-warm the Implementation Spec (Part B) in the background so tickets can
    # INHERIT acceptance criteria from it. New PRDs are already warmed at
    # creation (a cache hit here); this covers PRDs that predate that — their
    # spec generates in the background now so the NEXT regenerate inherits, while
    # THIS ticket run stays a single fast call over the already-rendered PRD
    # (never blocked on Part B, never regenerating the PRD).
    if body.prd_id is not None:
        warm = asyncio.create_task(warm_impl_spec(body.prd_id))
        _inflight_tasks.add(warm)
        warm.add_done_callback(_inflight_tasks.discard)

    job_id = next(_job_ids)
    _jobs[job_id] = {
        "id": job_id,
        "company_id": company.company_id,
        "prd_id": body.prd_id,
        "insight": body.insight,
        "status": "generating",
        "stories": None,
        "progress": None,  # {"done": n, "total": m} once fan-out batches land
        "error": None,
    }
    _prune_jobs()

    from app.config import settings

    strategy = "fanout" if settings.ticket_gen_fanout else "single"

    # Bridge fan-out batch completions (which fire on a worker thread) back onto
    # the event loop so the job dict is only ever mutated there (the single-worker
    # invariant this store relies on). Each batch publishes the partial ticket set
    # + progress so the poll can stream them in before the whole run finishes.
    loop = asyncio.get_running_loop()

    def _on_batch(stories, done: int, total: int) -> None:
        snapshot = [s.to_dict() for s in stories]  # off-loop: no shared state read

        def _apply() -> None:
            job = _jobs.get(job_id)
            if job is not None and job["status"] == "generating":
                job["stories"] = snapshot
                job["progress"] = {"done": done, "total": total}

        loop.call_soon_threadsafe(_apply)

    async def _run() -> None:
        try:
            stories = await asyncio.to_thread(
                generate_user_stories,
                company.company_id, prd_id=body.prd_id, insight=body.insight,
                strategy=strategy,
                batch_size=settings.ticket_gen_batch_size,
                max_parallel=settings.ticket_gen_max_parallel,
                on_batch=_on_batch,
            )
            job = _jobs.get(job_id)
            if job is not None:
                job["status"] = "ready"
                job["stories"] = [s.to_dict() for s in stories]
                job["progress"] = None
        except PRDNotFoundError as exc:
            job = _jobs.get(job_id)
            if job is not None:
                job["status"], job["error"] = "failed", str(exc)
        except Exception as exc:  # noqa: BLE001 — surface, never hang the poll
            logger.exception("story generation failed job_id=%s", job_id)
            job = _jobs.get(job_id)
            if job is not None:
                job["status"], job["error"] = "failed", f"{type(exc).__name__}: {exc}"

    task = asyncio.create_task(_run())
    _inflight_tasks.add(task)
    task.add_done_callback(_inflight_tasks.discard)

    return {"job_id": job_id, "status": "generating"}


@router.get("/jobs/{job_id}")
def get_job(
    job_id: int,
    company: CompanyContext = Depends(require_company),
):
    """Poll a story-generation job. 404 for an unknown job or a foreign tenant
    (job ids are sequential integers, so bind to the caller's company)."""
    job = _jobs.get(job_id)
    if job is None or job["company_id"] != company.company_id:
        raise HTTPException(404, "Job not found")
    out: dict = {"job_id": job_id, "status": job["status"]}
    if job["status"] == "ready":
        out["stories"] = job["stories"] or []
    elif job["status"] == "failed":
        out["error"] = job["error"]
    elif job["status"] == "generating":
        # Stream partial results: fan-out publishes tickets batch-by-batch, so a
        # poll mid-run can render what's landed already instead of an empty spin.
        if job.get("stories"):
            out["stories"] = job["stories"]
        if job.get("progress"):
            out["progress"] = job["progress"]
    return out


@router.get("/for-prd/{prd_id}")
def tickets_for_prd(
    prd_id: int,
    company: CompanyContext = Depends(require_company),
):
    """Return the persisted tickets for a PRD and whether they're still fresh.

    The Tickets tab reads this first: if `fresh` is true (the stored stories were
    generated from the PRD's current rendered content), it renders them with no
    LLM call. If the row is missing/stale/failed it kicks off /generate, which
    re-persists. `fresh` compares the stored content_hash to the live PRD hash.
    """
    from app.db.prd_tickets import get_tickets, prd_content_hash

    row = get_tickets(company.company_id, prd_id)
    if row is None:
        return {"status": "none", "fresh": False, "stories": []}
    current = prd_content_hash(prd_id)
    fresh = (
        row.get("status") == "ready"
        and bool(row.get("stories"))  # an empty cached set is a failed run → retry
        and current is not None
        and current == row.get("content_hash")
    )
    return {
        "status": row.get("status") or "ready",
        "fresh": fresh,
        "stories": row.get("stories") or [],
        "generated_at": row.get("generated_at"),
    }


@router.post("/lists")
def clickup_lists(company: CompanyContext = Depends(require_company)):
    """List the ClickUp lists this company can push into (target picker).

    404 if ClickUp isn't connected.
    """
    from app.stories.push import _clickup_access_token

    try:
        token = _clickup_access_token(company.company_id)
    except ClickUpNotConnectedError as e:
        raise HTTPException(404, str(e)) from e
    return {"lists": clickup_oauth.list_lists(token)}


@router.post("/push")
def push(
    body: PushIn,
    company: CompanyContext = Depends(require_company),
):
    """Create the given stories as tasks in a ClickUp list (explicit write).

    404 if ClickUp isn't connected. Per-story failures are isolated and
    reported in `errors` rather than failing the whole batch.
    """
    stories = [Story.from_dict(s.model_dump()) for s in body.stories]
    try:
        result = push_stories_to_clickup(company.company_id, body.list_id, stories)
    except ClickUpNotConnectedError as e:
        raise HTTPException(404, str(e)) from e
    return result


@router.post("/jira/projects")
def jira_projects(company: CompanyContext = Depends(require_company)):
    """List the Jira projects this company can push stories into (target picker).

    404 if Jira isn't connected.
    """
    from app.stories.push import _jira_creds

    try:
        access_token, cloud_id = _jira_creds(company.company_id)
    except JiraNotConnectedError as e:
        raise HTTPException(404, str(e)) from e
    return {"projects": jira_oauth.list_projects(access_token, cloud_id)}


class JiraMembersIn(BaseModel):
    project_key: str = Field(..., min_length=1)
    query: str | None = None


@router.post("/jira/members")
def jira_members(
    body: JiraMembersIn,
    company: CompanyContext = Depends(require_company),
):
    """List users assignable to issues in a Jira project (assignee picker).

    Returns `{members: [{accountId, displayName, email, active, avatarUrl}]}`.
    404 if Jira isn't connected.
    """
    from app.stories.push import _jira_creds

    try:
        access_token, cloud_id = _jira_creds(company.company_id)
    except JiraNotConnectedError as e:
        raise HTTPException(404, str(e)) from e
    members = jira_oauth.list_assignable_users(
        access_token, cloud_id, body.project_key, query=body.query
    )
    return {"members": members}


@router.post("/jira/push")
def push_jira(
    body: PushJiraIn,
    company: CompanyContext = Depends(require_company),
):
    """Create the given stories as issues in a Jira project (explicit write).

    404 if Jira isn't connected. Per-story failures are isolated and reported
    in `errors` rather than failing the whole batch.
    """
    stories = [Story.from_dict(s.model_dump()) for s in body.stories]
    try:
        result = push_stories_to_jira(
            company.company_id, body.project_key, stories, issue_type=body.issue_type
        )
    except JiraNotConnectedError as e:
        raise HTTPException(404, str(e)) from e
    return result


# ── Two-way tracker sync (per-PRD) ───────────────────────────────────────────
#
# The first manual push registers the PRD's destination (a prd_ticket_sync
# row); from then on the scheduler auto-syncs it on an interval and the web's
# sync button triggers the same pass ad-hoc. A pass reconciles BOTH directions
# per ticket with last-writer-wins: local edits (web + MCP, from ticket_edits)
# push out; tracker-side edits and status moves import back as overrides (see
# app.stories.sync). Sync runs in the background: POST returns immediately
# with {status: "syncing"} and the client polls GET until sync_status is back
# to "idle".


class SyncTriggerIn(BaseModel):
    """Optional destination for the FIRST push (or a tool/destination switch).
    Omit all fields to re-sync the already-configured destination."""

    provider: str | None = Field(default=None)
    destination_id: str | None = Field(default=None, min_length=1)
    destination_name: str | None = None


def _public_sync_state(cfg: dict | None) -> dict:
    """The sync row as the web/MCP read it. A 'syncing' older than the stale
    window reports as idle so a crashed run never wedges the button."""
    from app.stories.sync import sync_in_flight

    if cfg is None:
        return {"configured": False}
    return {
        "configured": True,
        "provider": cfg.get("provider"),
        "destination_id": cfg.get("destination_id"),
        "destination_name": cfg.get("destination_name"),
        "auto_sync": bool(cfg.get("auto_sync")),
        "sync_status": "syncing" if sync_in_flight(cfg) else "idle",
        "last_synced_at": cfg.get("last_synced_at"),
        "last_error": cfg.get("last_error"),
        "statuses": cfg.get("statuses") or {},
    }


@router.get("/sync/{prd_id}")
def sync_state(
    prd_id: int,
    company: CompanyContext = Depends(require_company),
):
    """This PRD's tracker-sync state: destination, whether a sync is running,
    when it last completed, and the pulled per-ticket tracker statuses.
    `configured: false` means the tickets were never pushed anywhere."""
    from app.db.ticket_sync import get_sync_config

    return _public_sync_state(get_sync_config(company.company_id, prd_id))


@router.get("/sync/{prd_id}/tracker-meta")
def tracker_meta(
    prd_id: int,
    refresh: bool = False,
    company: CompanyContext = Depends(require_company),
):
    """The tracker vocabulary (statuses / priorities / issue types / custom
    fields) the ticket detail renders instead of Sprntly's canned lists.

    Bound PRD → the bound destination's meta (cache; `?refresh=1` forces a
    live re-fetch). UNBOUND PRD with a connected tracker → the freshest meta
    the connect-time warm cached for that tracker (`configured: false` but
    `provider`/`meta` set) — the detail speaks the customer's vocabulary from
    the moment they connect, before any push. No tracker at all → all-null,
    the web keeps its defaults."""
    from app import db
    from app.db.ticket_sync import get_sync_config
    from app.db.tracker_meta import get_newest_cached_meta, get_or_fetch_meta
    from app.stories.sync import ticket_sync_providers

    cfg = get_sync_config(company.company_id, prd_id)
    if cfg is not None:
        meta = get_or_fetch_meta(
            company.company_id, cfg["provider"], cfg["destination_id"],
            refresh=refresh,
        )
        return {
            "configured": True,
            "provider": cfg.get("provider"),
            "destination_id": cfg.get("destination_id"),
            "meta": meta,
        }

    for provider in ticket_sync_providers():
        try:
            if not db.get_connection(company.company_id, provider):
                continue
            meta = get_newest_cached_meta(company.company_id, provider)
        except Exception:  # noqa: BLE001 — fallback vocabulary is best-effort
            continue
        if meta:
            return {
                "configured": False,
                "provider": provider,
                "destination_id": meta.get("destination_id"),
                "meta": meta,
            }
        # Connected but never warmed (connection predates the connect-time
        # pull) — self-heal: warm in the background; the next open serves it.
        from app.connectors.tracker_meta import kick_company_meta_warm

        kick_company_meta_warm(company.company_id, provider)
    return {"configured": False, "provider": None,
            "destination_id": None, "meta": None}


@router.post("/sync/{prd_id}")
async def trigger_sync(
    prd_id: int,
    body: SyncTriggerIn,
    company: CompanyContext = Depends(require_company),
):
    """Run a two-way sync pass for this PRD's tickets, in the background.

    First push: pass `provider` + `destination_id` (a ClickUp list / Jira
    project) to register the destination — the same call also switches an
    existing PRD to a different tool/destination. With no body fields the
    already-configured destination re-syncs. 404 when nothing is configured
    and no destination was given. Poll GET /sync/{prd_id} for completion.
    """
    from app.db.ticket_sync import get_sync_config, mark_syncing, upsert_sync_config
    from app.stories.sync import (
        run_prd_sync,
        sync_in_flight,
        ticket_sync_providers,
    )

    if (body.provider is None) != (body.destination_id is None):
        raise HTTPException(400, "provider and destination_id go together")
    if body.provider is not None:
        # Eligibility is type-driven: the provider must be a task-management
        # connector (app/connectors/catalog.py) the sync engine implements.
        if body.provider not in ticket_sync_providers():
            raise HTTPException(
                400,
                f"{body.provider!r} is not a task-management connector tickets can sync with",
            )
        upsert_sync_config(
            company.company_id, prd_id,
            provider=body.provider,
            destination_id=body.destination_id,
            destination_name=body.destination_name,
        )

    cfg = get_sync_config(company.company_id, prd_id)
    if cfg is None:
        raise HTTPException(404, "This PRD's tickets were never pushed — pick a destination first")

    # EVERY sync trigger (first bind AND the ad-hoc Sync button) refreshes
    # the destination's vocabulary cache — statuses / priorities / custom
    # fields / built-ins re-pulled from the tracker so tracker_meta reflects
    # workspace changes now, not at the 6h TTL. Best-effort in the
    # background — a metadata failure must never block the sync itself.
    async def _warm_meta(provider: str = cfg["provider"],
                         destination_id: str = cfg["destination_id"]) -> None:
        from app.db.tracker_meta import get_or_fetch_meta
        try:
            await asyncio.to_thread(
                get_or_fetch_meta, company.company_id, provider,
                destination_id, refresh=True,
            )
        except Exception:  # noqa: BLE001 — cache warming is best-effort
            logger.warning("tracker-meta warm failed for prd %s", prd_id)

    meta_task = asyncio.create_task(_warm_meta())
    _inflight_tasks.add(meta_task)
    meta_task.add_done_callback(_inflight_tasks.discard)

    if sync_in_flight(cfg):
        return {"status": "syncing"}

    # Mark before spawning so a GET between this response and the thread
    # starting already reads "syncing" (no idle flash in the UI).
    mark_syncing(company.company_id, prd_id)

    async def _run() -> None:
        try:
            await asyncio.to_thread(run_prd_sync, company.company_id, prd_id)
        except Exception:  # noqa: BLE001 — recorded on the row by run_prd_sync
            logger.exception("ad-hoc ticket sync failed for prd %s", prd_id)

    task = asyncio.create_task(_run())
    _inflight_tasks.add(task)
    task.add_done_callback(_inflight_tasks.discard)
    return {"status": "syncing"}


class PullStatusIn(BaseModel):
    list_id: str = Field(..., min_length=1)
    ticket_ids: list[str] = Field(..., min_length=1)


@router.post("/pull-status")
def pull_status(
    body: PullStatusIn,
    company: CompanyContext = Depends(require_company),
):
    """Bidirectional read: return the current ClickUp state (status, assignee,
    url) for the given tickets already synced to `list_id`, keyed by ticket id.
    Tickets never pushed are simply absent. 404 if ClickUp isn't connected."""
    from app.stories.push import pull_clickup_status

    try:
        return {"statuses": pull_clickup_status(company.company_id, body.list_id, body.ticket_ids)}
    except ClickUpNotConnectedError as e:
        raise HTTPException(404, str(e)) from e
