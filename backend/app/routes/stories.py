"""User-story endpoints — generate from a PRD, then push into ClickUp.

  POST /v1/stories/generate  {prd_id}            -> generated stories (no write)
  POST /v1/stories/lists                          -> ClickUp lists to pick a target
  POST /v1/stories/push      {list_id, stories}  -> create the stories in ClickUp

Generation and push are kept SEPARATE on purpose: generation never touches the
user's tracker, so the user reviews the stories before any are written. Push is
the explicit, outward-facing write. All routes require_workspace (tenant scoped).
"""
from __future__ import annotations

import asyncio
import itertools
import logging

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from app.auth import WorkspaceContext, require_company, require_workspace  # noqa: F401 — re-exported for tests' dependency_overrides
from app.connectors import asana_oauth, clickup_oauth, jira_oauth
from app.stories.generate import (
    PRDNotFoundError,
    Story,
    generate_user_stories,
)
from app.prd_runner import warm_impl_spec
from app.stories.push import (
    AsanaNotConnectedError,
    ClickUpNotConnectedError,
    JiraNotConnectedError,
    push_stories_to_asana,
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


# ── Standalone ticket sets: terminal state ───────────────────────────────────
#
# A `ticket_sets` row is created 'generating' at kick-off and MUST reach a
# terminal state, because unlike the PRD path there is nothing that re-kicks it:
# a set is a one-shot artifact, so a row left 'generating' is a panel that spins
# forever over a run that finished. These two helpers are the guarantee.


def _settle_ticket_set(company_id: str, set_id: int, stories: list) -> None:
    """Ensure a set that finished generating is no longer 'generating'.

    Normally a no-op: app.stories.generate already wrote the row (with its
    LLM-derived title) inside the worker. This only fires when that write was
    skipped or swallowed — including the ZERO-TICKET run, which is a real
    terminal outcome for a set and lands as `ready` with an empty array so the
    panel can offer "no tickets came back — try again" instead of spinning.

    That is a DELIBERATE divergence from the PRD path's never-cache-empty rule.
    There, `prd_tickets` is a CACHE keyed by content hash and an empty row wedges
    the tab on "0 tickets" forever, so the write is skipped and the next open
    retries. Here the row IS the artifact, there is no next open that retries,
    and "the run produced nothing" is information the user needs.
    """
    from app.db.ticket_sets import finish_set, get_set

    try:
        row = get_set(company_id, set_id)
        if row is None or row.get("status") != "generating":
            return
        payload = [s.to_dict() for s in stories]
        finish_set(
            set_id,
            title=(row.get("title") or "").strip() or _fallback_set_title(row, payload),
            stories=payload,
        )
    except Exception:  # noqa: BLE001 — never turn a completed run into a 500
        logger.exception("settling ticket set %s failed (continuing)", set_id)


def _fallback_set_title(row: dict, stories: list[dict]) -> str:
    """A title for a set the naming leg never got to name.

    Prefers the first ticket's title over a generic string: a library where
    every standalone set reads "Tickets" is a library you cannot navigate.
    """
    for s in stories:
        t = str((s or {}).get("title") or "").strip()
        if t:
            return t
    src = str(row.get("source_text") or "").strip()
    return (src[:80] + "…") if len(src) > 80 else (src or "Tickets from this conversation")


def _mark_ticket_set_failed(set_id: int | None, error: str) -> None:
    """Flip a set to `failed` after a generation error. Best-effort: the job
    store already carries the error for the poll, so a failure here costs the
    reopen path its accuracy but never the response."""
    if set_id is None:
        return
    try:
        from app.db.ticket_sets import fail_set

        fail_set(set_id, error)
    except Exception:  # noqa: BLE001
        logger.exception("marking ticket set %s failed did not land", set_id)


class GenerateIn(BaseModel):
    prd_id: int | None = Field(default=None, ge=1)
    insight: str | None = None
    # The chat thread an INSIGHT run was started from, so the resulting ticket
    # set can be reopened from that thread and shown as "from <chat title>" in
    # the artifacts library. Optional and ignored on the prd_id path: a set can
    # legitimately be born outside any conversation.
    conversation_id: int | None = Field(default=None, ge=1)


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
    company: WorkspaceContext = Depends(require_workspace),
):
    """Kick off user-story generation from a PRD (or a free-form insight).

    Fire-and-forget: schedules the multi-minute generation in the background and
    returns a `job_id` immediately so the Tickets tab never blocks on a hung
    request. Poll GET /v1/stories/jobs/{job_id} until status is "ready" (carries
    `stories`) or "failed" (carries `error`). Generation never writes to ClickUp
    — call /v1/stories/push separately once the user has reviewed.

    The INSIGHT path additionally creates a `ticket_sets` row up front and
    returns its `ticket_set_id`, so tickets generated from a chat with no PRD
    behind them are a durable artifact rather than a wall of markdown in a chat
    bubble. The row is created here, at kick-off, and flipped to ready/failed by
    the background job — the client never posts stories back, which is what
    makes a double-poll or a StrictMode double-effect unable to produce two sets.
    """
    if (body.prd_id is None) == (body.insight is None):
        raise HTTPException(400, "provide exactly one of prd_id or insight")

    # A conversation_id is client-supplied and ids are sequential, so prove the
    # thread is this company's before stamping a set with it. 404, never 403 —
    # a foreign tenant must not learn whether the id exists.
    if body.conversation_id is not None:
        from app.db.conversations import conversation_belongs_to_company

        if not conversation_belongs_to_company(
            body.conversation_id, company.company_id
        ):
            raise HTTPException(404, "Conversation not found")

    # Idempotent while in-flight: breaking a PRD into tickets is a multi-minute
    # call, and the Tickets tab re-kicks generation whenever it remounts (a tab
    # switch) before the first run has persisted — the cache read still 404s/sees
    # no fresh row, so the client falls through to /generate again. Re-attach
    # that rapid second call to the running job instead of starting a parallel,
    # wasteful one. Keyed by (company, prd_id|insight) since that's what the run
    # is over. Once a job is ready/failed it's persisted (PRD) or terminal, so we
    # only dedupe against still-"generating" jobs.
    #
    # NOTE for the insight path: `insight` is an LLM-composed task string, so two
    # phrasings of the same request do NOT dedupe here and would produce two
    # sets. That is deliberate at this layer (a second ask genuinely IS a second
    # set — see the product decision) and the accidental-double-send case is held
    # by the chat's own in-flight latch, not by this check.
    existing = next(
        (
            j
            for j in _jobs.values()
            if j["status"] == "generating"
            and j["company_id"] == company.company_id
            and j.get("prd_id") == body.prd_id
            and j.get("insight") == body.insight
        ),
        None,
    )
    if existing is not None:
        out = {"job_id": existing["id"], "status": "generating"}
        # Hand back the SAME set the running job will fill, so a re-attaching
        # client opens the panel on it instead of waiting for a set id that
        # will never arrive.
        if existing.get("ticket_set_id") is not None:
            out["ticket_set_id"] = existing["ticket_set_id"]
        return out

    # Create the set row BEFORE scheduling (and after the dedupe check, so a
    # re-attach never leaves an orphan `generating` row nothing will finish).
    ticket_set_id: int | None = None
    if body.insight is not None:
        from app.db.ticket_sets import create_set

        ticket_set_id = create_set(
            company.company_id,
            workspace_id=getattr(company, "workspace_id", None),
            conversation_id=body.conversation_id,
            source_text=body.insight,
        )

    job_id = next(_job_ids)
    _jobs[job_id] = {
        "id": job_id,
        "company_id": company.company_id,
        "prd_id": body.prd_id,
        "insight": body.insight,
        "ticket_set_id": ticket_set_id,
        "status": "generating",
        "stories": None,
        "stubs": None,  # planned ticket roster (fan-out), for skeleton rows
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

    def _on_plan(stubs: list[dict], total: int) -> None:
        # The plan completes ~20-35s in — long before the first enrich batch
        # (which on a single-wave run is the very END). Publish the roster so
        # the poll can render skeleton tickets immediately. Keep only display
        # fields; a stub is untrusted model output.
        snapshot = [
            {
                "title": str(s.get("title") or "").strip(),
                "summary": str(s.get("summary") or "").strip(),
                "prd_section": str(s.get("prd_section") or "").strip(),
            }
            for s in stubs
            if isinstance(s, dict) and str(s.get("title") or "").strip()
        ]

        def _apply() -> None:
            job = _jobs.get(job_id)
            if job is not None and job["status"] == "generating":
                job["stubs"] = snapshot
                job["progress"] = {"done": 0, "total": total}

        loop.call_soon_threadsafe(_apply)

    async def _run() -> None:
        try:
            stories = await asyncio.to_thread(
                generate_user_stories,
                company.company_id, prd_id=body.prd_id, insight=body.insight,
                ticket_set_id=ticket_set_id,
                strategy=strategy,
                batch_size=settings.ticket_gen_batch_size,
                max_parallel=settings.ticket_gen_max_parallel,
                on_batch=_on_batch,
                on_plan=_on_plan,
            )
            job = _jobs.get(job_id)
            if job is not None:
                job["status"] = "ready"
                job["stories"] = [s.to_dict() for s in stories]
                job["stubs"] = None
                job["progress"] = None
            # Backstop the set's terminal state. generate_user_stories owns the
            # normal write (it has the stories and the title leg), but that
            # write is deliberately exception-swallowing so a persistence
            # hiccup never loses a completed generation. Without this, such a
            # hiccup would leave the row 'generating' forever and the panel
            # spinning on a run that finished minutes ago — the one outcome the
            # reopen path must never produce.
            if ticket_set_id is not None:
                await asyncio.to_thread(
                    _settle_ticket_set, company.company_id, ticket_set_id, stories
                )
            # Pre-warm the Implementation Spec (Part B) AFTER the run so the
            # NEXT regenerate can INHERIT acceptance criteria from it. This used
            # to fire alongside the kick, but the warm (a ~3-4 min, 10K+ token
            # generation) competed with this very run's enrich shards for the
            # process-wide LLM gate — observed on prod as a 93.6s wait for a
            # concurrency slot. THIS run never needed it (it generates over the
            # already-rendered PRD), so deferring costs nothing and frees the
            # gate while the user is actually watching.
            if body.prd_id is not None:
                warm = asyncio.create_task(warm_impl_spec(body.prd_id))
                _inflight_tasks.add(warm)
                warm.add_done_callback(_inflight_tasks.discard)
        except PRDNotFoundError as exc:
            job = _jobs.get(job_id)
            if job is not None:
                job["status"], job["error"] = "failed", str(exc)
            _mark_ticket_set_failed(ticket_set_id, str(exc))
        except Exception as exc:  # noqa: BLE001 — surface, never hang the poll
            logger.exception("story generation failed job_id=%s", job_id)
            job = _jobs.get(job_id)
            if job is not None:
                job["status"], job["error"] = "failed", f"{type(exc).__name__}: {exc}"
            _mark_ticket_set_failed(ticket_set_id, f"{type(exc).__name__}: {exc}")

    task = asyncio.create_task(_run())
    _inflight_tasks.add(task)
    task.add_done_callback(_inflight_tasks.discard)

    out: dict = {"job_id": job_id, "status": "generating"}
    if ticket_set_id is not None:
        out["ticket_set_id"] = ticket_set_id
    return out


@router.get("/jobs/{job_id}")
def get_job(
    job_id: int,
    company: WorkspaceContext = Depends(require_workspace),
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
        # The planned stub roster comes first (~20-35s in) so the client can put
        # up skeleton rows for the whole set before any full ticket exists.
        if job.get("stories"):
            out["stories"] = job["stories"]
        if job.get("stubs"):
            out["stubs"] = job["stubs"]
        if job.get("progress"):
            out["progress"] = job["progress"]
    return out


@router.get("/for-prd/{prd_id}")
def tickets_for_prd(
    prd_id: int,
    company: WorkspaceContext = Depends(require_workspace),
):
    """Return the persisted tickets for a PRD and whether they're still fresh.

    The Tickets tab reads this first: if `fresh` is true (the stored stories were
    generated from the PRD's current rendered content), it renders them with no
    LLM call. If the row is missing/stale/failed it kicks off /generate, which
    re-persists. `fresh` compares the stored content_hash to the live PRD hash.
    """
    from app.db.prd_tickets import get_tickets, prd_hash_matches

    row = get_tickets(company.company_id, prd_id)
    if row is None:
        return {"status": "none", "fresh": False, "stories": []}
    fresh = (
        row.get("status") == "ready"
        and bool(row.get("stories"))  # an empty cached set is a failed run → retry
        # Accepts the pre-impl-spec-warm hash too — the background Part B warm
        # racing save_tickets used to flip this false with no real PRD change.
        and prd_hash_matches(prd_id, row.get("content_hash"))
    )
    return {
        "status": row.get("status") or "ready",
        "fresh": fresh,
        "stories": _apply_lifecycle(company.company_id, prd_id,
                                    row.get("stories") or []),
        "generated_at": row.get("generated_at"),
    }


def _apply_lifecycle(
    company_id: str, prd_id: int, stories: list[dict]
) -> list[dict]:
    """Drop DELETED tickets from a rendered set and tag EXCLUDED ones.

    Deleted tickets stay in `prd_tickets.stories` — that array is regenerated
    wholesale from the PRD, so the deletion is recorded on ticket_edits and
    applied on the way out instead. Excluded ones are still shown (the user
    chose to keep them in Sprntly), carrying `lifecycle` so the UI can mark
    them as not going to the tracker.
    """
    from app.db.ticket_lifecycle import DELETED, lifecycle_by_key
    from app.stories.sync import ticket_key_for

    try:
        states = lifecycle_by_key(company_id, prd_id)
    except Exception:  # noqa: BLE001 — a lookup failure must not blank the tab
        logger.warning("lifecycle lookup failed for prd %s", prd_id)
        return stories
    if not states:
        return stories
    out: list[dict] = []
    for s in stories:
        if not isinstance(s, dict):
            continue
        state = states.get(ticket_key_for(prd_id, s))
        if state == DELETED:
            continue
        out.append({**s, "lifecycle": state} if state else s)
    return out


@router.post("/lists")
def clickup_lists(company: WorkspaceContext = Depends(require_workspace)):
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
    company: WorkspaceContext = Depends(require_workspace),
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
def jira_projects(company: WorkspaceContext = Depends(require_workspace)):
    """List the Jira projects this company can push stories into (target picker).

    404 if Jira isn't connected.
    """
    from app.stories.push import _jira_creds

    try:
        access_token, cloud_id = _jira_creds(company.company_id)
    except JiraNotConnectedError as e:
        raise HTTPException(404, str(e)) from e
    return {"projects": jira_oauth.list_projects(access_token, cloud_id)}


@router.post("/asana/projects")
def asana_projects(company: WorkspaceContext = Depends(require_workspace)):
    """List the Asana projects this company can push stories into (target
    picker). Returns `{lists: [{id, name}]}` — shaped like the ClickUp list
    picker (project gid as `id`) so the web's compact destination picker is
    reused unchanged. 404 if Asana isn't connected.
    """
    from app.stories.push import _asana_creds

    try:
        token = _asana_creds(company.company_id)
    except AsanaNotConnectedError as e:
        raise HTTPException(404, str(e)) from e
    return {
        "lists": [
            {"id": p["gid"], "name": p.get("name") or p["gid"]}
            for p in asana_oauth.list_projects(token) if p.get("gid")
        ]
    }


class JiraMembersIn(BaseModel):
    project_key: str = Field(..., min_length=1)
    query: str | None = None


@router.post("/jira/members")
def jira_members(
    body: JiraMembersIn,
    company: WorkspaceContext = Depends(require_workspace),
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
    company: WorkspaceContext = Depends(require_workspace),
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
    """The sync row as the web/MCP read it. Shared with the ticket-set sync
    route (app/stories/sync_control.py) so both surfaces return one shape."""
    from app.stories.sync_control import public_sync_state

    return public_sync_state(cfg)


@router.get("/sync/{prd_id}")
def sync_state(
    prd_id: int,
    company: WorkspaceContext = Depends(require_workspace),
):
    """This PRD's tracker-sync state: destination, whether a sync is running,
    when it last completed, and the pulled per-ticket tracker statuses.
    `configured: false` means the tickets were never pushed anywhere."""
    from app.db.ticket_sync import get_sync_config
    from app.stories.scope import prd_scope

    return _public_sync_state(get_sync_config(company.company_id, prd_scope(prd_id)))


@router.get("/sync/{prd_id}/tracker-meta")
def tracker_meta(
    prd_id: int,
    refresh: bool = False,
    company: WorkspaceContext = Depends(require_workspace),
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
    from app.stories.scope import prd_scope
    from app.stories.sync import ticket_sync_providers

    cfg = get_sync_config(company.company_id, prd_scope(prd_id))
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
    company: WorkspaceContext = Depends(require_workspace),
):
    """Run a two-way sync pass for this PRD's tickets, in the background.

    First push: pass `provider` + `destination_id` (a ClickUp list / Jira
    project) to register the destination — the same call also switches an
    existing PRD to a different tool/destination. With no body fields the
    already-configured destination re-syncs. 404 when nothing is configured
    and no destination was given. Poll GET /sync/{prd_id} for completion.

    The body of this is shared with the standalone-ticket-set sync route — see
    app/stories/sync_control.trigger_scope_sync. Both surfaces must bind,
    warm and single-flight identically; two copies would drift, and what they
    drive writes to customers' live trackers.
    """
    from app.stories.scope import prd_scope
    from app.stories.sync_control import trigger_scope_sync

    return await trigger_scope_sync(
        company.company_id, prd_scope(prd_id),
        provider=body.provider,
        destination_id=body.destination_id,
        destination_name=body.destination_name,
        unconfigured_message=(
            "This PRD's tickets were never pushed — pick a destination first"
        ),
    )


class PullStatusIn(BaseModel):
    list_id: str = Field(..., min_length=1)
    ticket_ids: list[str] = Field(..., min_length=1)


@router.post("/pull-status")
def pull_status(
    body: PullStatusIn,
    company: WorkspaceContext = Depends(require_workspace),
):
    """Bidirectional read: return the current ClickUp state (status, assignee,
    url) for the given tickets already synced to `list_id`, keyed by ticket id.
    Tickets never pushed are simply absent. 404 if ClickUp isn't connected."""
    from app.stories.push import pull_clickup_status

    try:
        return {"statuses": pull_clickup_status(company.company_id, body.list_id, body.ticket_ids)}
    except ClickUpNotConnectedError as e:
        raise HTTPException(404, str(e)) from e
