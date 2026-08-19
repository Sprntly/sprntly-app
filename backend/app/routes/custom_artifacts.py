"""Custom artifacts — team documents of any kind (the "Others" library).

  POST   /v1/custom-artifacts                  -> create a document
  GET    /v1/custom-artifacts                  -> this company's documents
  GET    /v1/custom-artifacts/by-conversation/{cid} -> the ones born in a chat
  GET    /v1/custom-artifacts/{id}             -> one document, with its body
  PATCH  /v1/custom-artifacts/{id}             -> save a title / kind / body
  DELETE /v1/custom-artifacts/{id}             -> remove it
  POST   /v1/custom-artifacts/generate         -> write one with the LLM

EXPLICIT ASKS ONLY. Nothing reaches `/generate` on a user's behalf. The chat
gets here through the planner's `create_artifact` action, which fires on a
request to CREATE a document and never on a question about one, and the
suggestion strip only ever proposes a prompt the USER then chooses to send.
That ordering is the requirement, not a nicety: this library is shared with the
whole team, so a document created from a misread question appears in every
colleague's library.

TENANT GATE on every route: `require_company` resolves the caller's company
from the JWT and `db.custom_artifacts` filters `company_id` IN THE QUERY, so a
document belonging to another company reads as absent. Both cases raise 404,
never 403 — a foreign tenant must not be able to tell "exists but not yours"
from "doesn't exist". RLS is bypassed (service-role key), so this is the ONLY
tenant boundary these routes have.

NO PER-USER GATE, deliberately. Any member of the company can read and write
any document in it: that is what "shared within the team" means, and it is the
same posture reports and ticket sets already have. `created_by`/`updated_by`
are attribution only. Explicit per-person sharing is a later slice; when it
arrives it adds a check here, and until then a route that filtered on
`created_by` would silently make the library private — the exact bug #1061
fixed on the share-link path.

THE BODY IS SANITIZED ON EVERY WRITE (app/custom_artifact_html.py), not on
read. Sanitizing on write means the stored document is the safe one, so every
consumer — this API, the PDF renderer, a future export, a future share link —
is covered without each having to remember. Read paths return what is stored.
"""
from __future__ import annotations

import asyncio
import logging
import sys
from concurrent.futures import ThreadPoolExecutor
from functools import partial

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from app.auth import CompanyContext, require_company
from app.custom_artifact_generate import generate_into
from app.custom_artifact_html import sanitize_artifact_html
from app.db.conversations import conversation_belongs_to_company
from app.db.custom_artifacts import (
    MAX_BODY_CHARS,
    BodyTooLarge,
    VersionConflict,
    create_artifact,
    delete_artifact,
    get_artifact,
    list_artifacts_for_company,
    list_artifacts_for_conversation,
    update_artifact,
)
from app.project_from_prd import maybe_pin_custom_artifact_to_project

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/v1/custom-artifacts", tags=["custom-artifacts"])

# Strong references to in-flight generations. asyncio holds only a WEAK
# reference to a running task, so a bare `create_task(...)` result can be
# garbage-collected mid-generation — the same set-plus-discard pattern every
# other create_task site in this codebase uses (routes/ask.py, evidence.py,
# brief.py, design_agent.py).
_inflight_tasks: set[asyncio.Task] = set()

# Document generations run HERE and nowhere else. A small dedicated pool, so a
# burst of documents can only ever make other documents wait: each one holds its
# thread for minutes (`long_output=True` = a 600s read timeout, plus queueing on
# the process-wide `_llm_gate`), and both pools it might otherwise borrow are
# shared with work that must stay responsive — the anyio pool serves every sync
# route, and asyncio's default executor backs ~120 `to_thread` calls across this
# codebase.
#
# FOUR because generations are bounded by the LLM gate long before they are
# bounded by threads; more threads here would only queue in a different place,
# and the queue that matters is already durable — an unstarted generation is a
# `generating` row, which is precisely what that row exists to survive.
_GENERATION_POOL = ThreadPoolExecutor(
    max_workers=4, thread_name_prefix="custom-artifact-gen"
)

# The ceiling is the db module's (imported, not redeclared, so the two cannot
# drift). It is enforced there — this route only turns the resulting error into
# a 413 with a reason.
#
# THE CHECK CANNOT HAPPEN ON THE RAW INPUT, which is what it used to do.
# Sanitizing ESCAPES `&`, `<` and `>`, so a body can grow ~5x on the way
# through: 399,007 bytes of `&` passed a raw 400,000 check and sanitized to
# 1,995,035, which the storage layer then sliced back to 400,000 — 80% of the
# document silently discarded behind a 200 OK. The size that matters is the
# size that gets STORED, so both writers below measure the sanitized string.
#
# A GENEROUS RAW GUARD STILL RUNS FIRST, though, because "measure after
# sanitizing" means the parser sees the input before anything bounds it: a
# 100MB body would be buffered by FastAPI, built into a full BeautifulSoup
# tree and re-serialised before the real ceiling could refuse it. The multiple
# is deliberately loose (escaping expands by at most ~5x) so it can only ever
# catch input that could not have fit anyway.
_RAW_BODY_LIMIT = MAX_BODY_CHARS * 8


def _guard_raw_size(body_html: str | None) -> None:
    if body_html is not None and len(body_html) > _RAW_BODY_LIMIT:
        raise HTTPException(413, "Document is too large")

def _public(row: dict, *, with_body: bool = True) -> dict:
    """One document as the web reads it.

    Empty strings are returned rather than omitted: the editor renders every
    field and decides its own placeholder copy, so the API never decides that a
    blank title should disappear.

    `error` — the raw `str(exc)` an operator needs — is NEVER included. That is
    not an oversight to be corrected later: it is exception text, so it carries
    URLs, provider wording and whatever else ended up in the message, into a
    library every colleague can read. `error_code` is the half that is safe,
    and it is what the failure copy is built from.

    THE CODE RIDES WITH THE BODY, on the detail read only. Listings select a
    fixed column set that does not include it, so returning the key there would
    emit `error_code: null` for a document that genuinely failed — a field that
    lies is worse than a field that is absent, and the listing already carries
    `status` for the one thing it needs to show.
    """
    out = {
        "id": row["id"],
        "kind": row.get("kind") or "",
        "title": row.get("title") or "",
        "status": row.get("status") or "ready",
        "version": int(row.get("version") or 1),
        "created_at": row.get("created_at"),
        "updated_at": row.get("updated_at"),
        "conversation_id": row.get("conversation_id"),
        "created_by": row.get("created_by"),
        "updated_by": row.get("updated_by"),
    }
    if with_body:
        out["body_html"] = row.get("body_html") or ""
        out["error_code"] = row.get("error_code") or None
    return out


def _require_owned(artifact_id: int, company_id: str) -> dict:
    """The document, or 404. The company filter lives in the query."""
    row = get_artifact(company_id, artifact_id)
    if row is None:
        raise HTTPException(404, "Artifact not found")
    return row


class CreateIn(BaseModel):
    # Every field is optional: "New document" from the library creates an empty
    # one and the user names it by typing, exactly as a new Google Doc behaves.
    kind: str = ""
    title: str = ""
    body_html: str = ""
    conversation_id: int | None = None


@router.post("")
def create(
    body: CreateIn,
    company: CompanyContext = Depends(require_company),
):
    """Create a document. Returns the full row, including its id and version."""
    # `conversation_id` is the ONE id on this surface the CLIENT chooses, which
    # makes it the one that has to be checked. Conversation ids are sequential
    # integers, and the artifacts listing resolves a document's conversation
    # into a TITLE — so an unchecked id lets a caller attach their own document
    # to another tenant's chat and read that chat's title back out of their own
    # library. Storing only ids the caller owns closes it at the source, which
    # also covers every future reader of the column.
    _guard_raw_size(body.body_html)
    if body.conversation_id is not None and not conversation_belongs_to_company(
        body.conversation_id, company.company_id
    ):
        raise HTTPException(404, "Conversation not found")
    try:
        row = create_artifact(
            company.company_id,
            kind=body.kind,
            title=body.title,
            body_html=sanitize_artifact_html(body.body_html),
            conversation_id=body.conversation_id,
            created_by=company.user_id,
        )
    except BodyTooLarge:
        raise HTTPException(413, "Document is too large")
    return _public(row)


@router.get("")
def list_all(company: CompanyContext = Depends(require_company)):
    """This company's documents, newest first, WITHOUT their bodies."""
    rows = list_artifacts_for_company(company.company_id)
    return {"artifacts": [_public(r, with_body=False) for r in rows]}


@router.get("/by-conversation/{conversation_id}")
def list_for_conversation(
    conversation_id: int,
    company: CompanyContext = Depends(require_company),
):
    """The documents born in one chat, newest first (the thread-resume read)."""
    rows = list_artifacts_for_conversation(company.company_id, conversation_id)
    return {"artifacts": [_public(r, with_body=False) for r in rows]}


@router.get("/{artifact_id}")
def get_one(
    artifact_id: int,
    company: CompanyContext = Depends(require_company),
):
    """One document with its body."""
    return _public(_require_owned(artifact_id, company.company_id))


class UpdateIn(BaseModel):
    # None means "don't touch this field", so a body autosave never clobbers a
    # title someone renamed in another tab, and vice versa.
    title: str | None = None
    kind: str | None = None
    body_html: str | None = None
    # The version the editor started from. Optional: omitting it accepts
    # last-write-wins, which is what a rename from the library row does.
    base_version: int | None = Field(default=None, ge=1)


@router.patch("/{artifact_id}")
def update(
    artifact_id: int,
    body: UpdateIn,
    company: CompanyContext = Depends(require_company),
):
    """Save an edit.

    409 when `base_version` no longer matches — someone else saved first. The
    response carries THEIR version of the document so the editor can say who
    moved it and offer the current text, rather than dropping the user's work
    on the floor with a bare error.
    """
    _guard_raw_size(body.body_html)
    # No ownership pre-read here: `update_artifact` resolves the row
    # company-filtered and returns None for one that is absent OR foreign,
    # which becomes the same 404 below. A pre-read would be a second round trip
    # buying a boundary the writer already enforces.
    try:
        row = update_artifact(
            company.company_id,
            artifact_id,
            title=body.title,
            kind=body.kind,
            body_html=(
                sanitize_artifact_html(body.body_html)
                if body.body_html is not None
                else None
            ),
            base_version=body.base_version,
            updated_by=company.user_id,
        )
    except BodyTooLarge:
        raise HTTPException(413, "Document is too large")
    except VersionConflict as exc:
        current = exc.current
        raise HTTPException(
            status_code=409,
            detail={
                "error": "version_conflict",
                "current": _public(current) if current else None,
            },
        )
    if row is None:
        # Deleted between the ownership read and the write.
        raise HTTPException(404, "Artifact not found")
    return _public(row)


class GenerateIn(BaseModel):
    """BOUNDED, all three text fields.

    Everything here is forwarded into an LLM prompt, and none of it was
    bounded: the client sends a 12k thread transcript as `context` and the
    server accepted whatever arrived. An unbounded field on a prompt path is
    not a validation nicety — it is a caller (or a bug in one) able to spend
    this company's tokens by the megabyte on a single request, and the cost
    lands on the tenant, not on the sender.

    The ceilings are deliberately far above any real request — the client's own
    transcript cap is 12k — so a legitimate document can never hit one. They
    exist to make the pathological case a 422 instead of a bill.
    """

    # WHAT KIND of document. Free text, straight from the user's own words
    # ("leadership update"), never coerced onto a list. Stored truncated to 120;
    # the bound here is about what gets PARSED, not what gets kept.
    kind: str = Field(default="", max_length=500)
    # The self-contained brief for what to write. On the chat path this is the
    # planner's synthesized `task`, not the raw last message.
    task: str = Field(default="", max_length=8_000)
    # Facts the document may assert. Supplied by the caller (the chat turn that
    # asked), because the thread already established what this is about — see
    # custom_artifact_generate's note on why there is no retrieval pass here.
    # 60k matches the attachment-content ceiling in routes/conversations.py,
    # which is the same kind of caller-supplied context.
    context: str = Field(default="", max_length=60_000)
    conversation_id: int | None = None


@router.post("/generate")
async def generate(
    body: GenerateIn,
    company: CompanyContext = Depends(require_company),
):
    """Start writing a document. Returns the row immediately, `generating`.

    The row is created BEFORE the multi-minute call so the panel has an id to
    open and poll against, rather than waiting on an id that only exists once
    generation finishes — the ticket-sets lifecycle. Creating it up front is
    also what makes double-generation structurally impossible: the client never
    posts content back, so a double-click or a StrictMode double-effect has
    nothing to write with.

    EXPLICIT ASKS ONLY. Nothing calls this on the user's behalf: the chat
    reaches it through the planner's `create_artifact` action, which fires on a
    request to CREATE a document and never on a question about one, and the
    suggestion strip only ever proposes a prompt the user then chooses to send.
    A document appearing in someone's library because they asked a question is
    the failure mode this rule exists to prevent.
    """
    # BOTH SUPABASE CALLS GO TO A THREAD, because this handler is now `async`
    # and they are blocking sync HTTP. Left inline they would run ON THE EVENT
    # LOOP: two round trips of the whole process stalled per request, and a
    # wedged Supabase client (a documented event here — see the h2 hang) would
    # take the entire API down instead of one worker thread. Making a handler
    # async without moving its blocking calls trades a threadpool problem for a
    # strictly worse one.
    if body.conversation_id is not None and not await asyncio.to_thread(
        conversation_belongs_to_company, body.conversation_id, company.company_id
    ):
        raise HTTPException(404, "Conversation not found")

    row = await asyncio.to_thread(
        create_artifact,
        company.company_id,
        kind=body.kind,
        # Provisional: the generator replaces it with the document's own <h1>.
        # A name now means the library row is never blank while it writes.
        title=(body.kind or "Document").strip()[:300],
        status="generating",
        conversation_id=body.conversation_id,
        created_by=company.user_id,
    )

    # Pin the document to its project the instant the row exists — SERVER-SIDE,
    # so it lands whether or not the client is still connected, and BEFORE the
    # multi-minute generation runs (the `generating` row surfaces in the rail
    # immediately, same as a building prototype). Only pins when the
    # conversation is already bound to a project; a doc drafted in a bare chat
    # stays project-less. Total/best-effort — a missed pin never fails the
    # request or the generation; the project's own refetch reconciles it.
    if body.conversation_id is not None:
        await asyncio.to_thread(
            maybe_pin_custom_artifact_to_project,
            company_id=company.company_id,
            conversation_id=body.conversation_id,
            artifact_id=row["id"],
        )

    # OFF THE REQUEST THREADPOOL, deliberately — this is the shape every other
    # long generation in this codebase uses (ask, evidence, brief, design).
    #
    # The handler used to be a sync `def` scheduling `BackgroundTasks`. The
    # argument for it was true as far as it went (a sync handler has no running
    # loop, so `create_task` would raise) but it answered the wrong question:
    # the fix for "a sync handler cannot create_task" is not "keep the sync
    # handler", it is "do not be a sync handler". BackgroundTasks runs the
    # callable on the SAME anyio threadpool FastAPI uses to serve every sync
    # route — 40 tokens, single process — and `long_output=True` lets one
    # document generation hold a token for up to 600 seconds. Enough concurrent
    # documents and sync routes queue behind them for no reason a user could
    # ever see.
    #
    # A DEDICATED, BOUNDED EXECUTOR runs the generation — not `to_thread`'s
    # default one. `asyncio.to_thread` would hand this to the loop's shared
    # default executor (min(32, cpu+4) threads), which ~120 other `to_thread`
    # sites in this codebase also use for short blocking work: ask-job
    # execution, attachment staging, the ask cache. A document generation holds
    # its thread for MINUTES (`long_output=True`, plus queueing on the
    # process-wide `_llm_gate`), so a burst of documents would occupy that pool
    # and stall everything sharing it. Moving the problem from the anyio pool
    # to the default pool would have been a smaller version of the bug, not a
    # fix. Documents get their own small pool and can only ever starve each
    # other; the queue beyond it is the durable `generating` row, which is
    # exactly what that row is for.
    #
    # The handler is async, so the loop is running and `create_task` is legal.
    #
    # The task is held in a module-level set, the pattern every create_task site
    # here follows: asyncio keeps only a weak reference, so a bare task can be
    # garbage-collected mid-generation.
    #
    # The DURABLE ROW is the job state — there is no in-memory job store to lose
    # — so a process death mid-write is recoverable by `sweep_orphan_generating`
    # rather than invisible.
    kwargs = dict(
        company_id=company.company_id,
        artifact_id=row["id"],
        kind=body.kind,
        task=body.task,
        context=body.context,
    )
    if "pytest" in sys.modules:
        # The TestClient does not keep the app's event loop alive between
        # requests, so a fire-and-forget task would never run and a test that
        # polls the row would spin forever. Inline under pytest, exactly as
        # routes/ask.py does for the same reason. `generate_into` is total, so
        # this cannot fail the request.
        await asyncio.to_thread(generate_into, **kwargs)
    else:
        loop = asyncio.get_running_loop()
        task = asyncio.ensure_future(
            loop.run_in_executor(_GENERATION_POOL, partial(generate_into, **kwargs))
        )
        _inflight_tasks.add(task)
        task.add_done_callback(_inflight_tasks.discard)

    # THE ROW AS CREATED, on both paths. The response is the same contract in
    # tests as in production — `generating`, with the id to poll — rather than
    # a re-read that would let a test assert a shape production never returns.
    return _public(row)


@router.delete("/{artifact_id}")
def remove(
    artifact_id: int,
    company: CompanyContext = Depends(require_company),
):
    """Delete a document. 404 when absent or foreign."""
    if not delete_artifact(company.company_id, artifact_id):
        raise HTTPException(404, "Artifact not found")
    return {"deleted": True}
