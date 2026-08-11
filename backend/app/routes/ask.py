import asyncio
import json
import logging
import random
import sys
import time

from fastapi import Depends, APIRouter, File, HTTPException, UploadFile
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field, field_validator

from app.ask_job_runner import ask_channel, run_ask_job
from app.auth import (  # noqa: F401 — require_company re-exported for tests' dependency_overrides
    CompanyContext,
    WorkspaceContext,
    require_company,
    require_workspace,
    require_workspace_from_query,
)
from app.graph import token_stream
from app.ingest import convert
from app.db import (
    cancel_ask_job,
    complete_ask_job,
    find_cached_ask,
    get_ask_job,
    start_ask_job,
)
from app.deps.ownership import require_owned_dataset, require_owned_prd
from app.entitlements import require_agents_module
from app.skill_router import list_available_skills

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/v1/ask", tags=["ask"])


# Strong refs to in-flight background Ask tasks. asyncio holds only a weak
# reference to a bare create_task result, so without this the task can be
# garbage-collected mid-run and the row would be stuck 'generating'. The
# done-callback discards each task on completion (mirrors routes/prd.py).
_inflight_tasks: set[asyncio.Task] = set()


# Pre-warmed cache hits return in <100ms — instantaneous responses break the
# demo illusion that the LLM is generating the answer in real time. A short
# random synthetic delay keeps the cached responses feeling generated. The
# frontend's "Thinking…" loader bridges this gap.
CACHE_HIT_DELAY_MIN_SECONDS = 5.0
CACHE_HIT_DELAY_MAX_SECONDS = 7.0

# Briefly wait on a still-warming cache row before falling through to a
# parallel LLM call. After a backend restart the warming semaphore can be
# draining for ~30-60s; an early click would otherwise pay full generation
# cost and race the warm task.
GENERATING_POLL_TIMEOUT_SECONDS = 25.0
GENERATING_POLL_INTERVAL_SECONDS = 0.5


# Tool-use schema for the Ask endpoint. Defined here (not in prompts.py)
# because it's how we extract the response, not part of the prompt text.
# Letting the Anthropic SDK validate structured input avoids the
# JSON-string-escaping failures that happen when the LLM hand-writes JSON
# with markdown tables, quoted text, and pipes inside the answer field.
ASK_RESPONSE_SCHEMA: dict = {
    "type": "object",
    "properties": {
        "answer": {
            "type": "string",
            "description": "Markdown-formatted answer. Follow the formatting rules in the system prompt.",
        },
        "key_points": {
            "type": "array",
            "items": {"type": "string"},
            "description": "Short bullet summary of the answer.",
        },
        "citations": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "source": {"type": "string"},
                    "evidence": {"type": "string"},
                },
                "required": ["source", "evidence"],
            },
        },
        "confidence": {"type": "number", "description": "0..1"},
        "unanswered": {
            "type": "string",
            "description": "Empty string if fully answered, else what data is missing.",
        },
    },
    "required": ["answer", "key_points", "citations", "confidence", "unanswered"],
}


class AskIn(BaseModel):
    # The cap must fit a question PLUS an inlined `[Attached files]` block —
    # the composer appends extracted document markdown (clamped to 100k there,
    # see ChatScreen.submitAsk) to the question. 2000 was the pre-attachment
    # sanity cap; keep a generous abuse ceiling, not a content limit.
    question: str = Field(..., min_length=3, max_length=120_000)
    dataset: str
    # Optional multi-turn: when set, prior turns of this conversation are
    # loaded (ownership-checked) and fed to the router + answer for follow-ups.
    conversation_id: int | None = None
    # Optional: skip routing and force this skill — used when a confirm-gate
    # follow-up has already chosen the skill.
    pinned_skill: str | None = None
    # Optional PRD-tab grounding: when the chat runs beside an open PRD, the
    # tab sends its prd_id so the answer sees the PRD (+ its insight, evidence,
    # tickets, prototype). Ownership-gated in the route.
    prd_id: int | None = Field(default=None, ge=1)

    # Belt to `ingest.strip_nul`'s braces. That fix stops extraction PRODUCING a
    # NUL; this one stops one ARRIVING — the client inlines attachment text it
    # extracted (or replays text from a turn stored before that fix) into the
    # question, and a single NUL anywhere in it makes `start_ask_job`'s insert
    # fail with SQLSTATE 22P05 and 500 the whole send. Observed live, twelve
    # times in one session.
    #
    # STRIPS rather than refuses: the character is never meaningful in a
    # question, and rejecting the message would turn a stray byte into a
    # user-visible failure — which is the thing being fixed.
    @field_validator("question")
    @classmethod
    def _drop_nul(cls, v: str) -> str:
        from app.ingest import strip_nul

        return strip_nul(v)


def _strip_citations(payload: dict) -> dict:
    """Citations stay in the LLM's grounding (so answers remain evidence-bound)
    but are not surfaced to the UI — the citation cards clutter the Ask reply.
    Always pass the response through this before returning to the client.
    """
    payload["citations"] = []
    return payload


def _load_history(
    conversation_id: int | None, company_id: str, user_id: str
) -> list[dict]:
    """Fetch prior turns [{role, content}] for an owned conversation, oldest
    first. Chats are per-user: the conversation must belong to the CALLER, not
    just their company — otherwise a teammate's conversation_id would replay
    that teammate's private turns into the model context. Best-effort: no id,
    foreign/unowned conversation, or any read error → [].

    Each turn's OWN attachments (`conversation_turns.attachments` — extracted
    text persisted at upload time, see `routes/conversations.py:TurnAttachment`)
    are folded onto that SAME turn's `content` before it is returned. Without
    this, an attachment's text rides the FIRST turn only via the question
    string the composer built client-side — by the third turn, the per-turn
    clamp (`app.prompt_history.clamp_turn_text`, applied unmodified at
    `qa_agent._render_history`) has erased every trace of it, and the model
    denies the document exists. Folding happens HERE, not in
    `_render_history`: the point of this split is that a turn with no
    attachments still returns exactly `{role, content}` — byte-identical to
    today — with nothing downstream needing to know attachments exist at
    all."""
    if not conversation_id:
        return []
    try:
        from app.db.client import require_client

        c = require_client()
        owned = (
            c.table("conversations")
            .select("id")
            .eq("id", conversation_id)
            .eq("company_id", company_id)
            .eq("user_id", user_id)
            .limit(1)
            .execute()
        )
        if not owned.data:
            return []
        turns = (
            c.table("conversation_turns")
            .select("role,content,attachments")
            .eq("conversation_id", conversation_id)
            .order("created_at")
            .execute()
        )
        out: list[dict] = []
        for row in turns.data or []:
            content = row.get("content") or ""
            for attachment in row.get("attachments") or []:
                body = attachment.get("content") or ""
                if not body:
                    # A document imported straight to a PRD persists a
                    # name-only chip (content == "") — the file BECAME the
                    # PRD, not this turn's context; nothing to fold in.
                    continue
                name = attachment.get("name") or "attachment"
                content += f"\n\n[Attached: {name}]\n{body}"
            out.append({"role": row.get("role", "user"), "content": content})
        return out
    except Exception:  # noqa: BLE001 — history must never break the answer
        return []


def _resolve_cache_hit(dataset: str, question: str) -> dict | None:
    """Resolve the pre-warm cache for this question, applying the same waiting +
    synthetic-delay behavior the old synchronous endpoint did. Returns the
    decoded (un-stripped) cached payload on a ready hit, else None. Blocking —
    called via `asyncio.to_thread` so the synthetic delay / generating-poll
    never blocks the event loop."""
    cached = find_cached_ask(dataset, question)
    # If a warm task is still in flight (typical post-restart while the warming
    # semaphore drains), wait for it instead of firing a parallel LLM call — the
    # user perceives real generation time, so we skip the synthetic delay.
    waited_on_generation = False
    if cached and cached.get("status") == "generating":
        deadline = time.monotonic() + GENERATING_POLL_TIMEOUT_SECONDS
        while time.monotonic() < deadline:
            time.sleep(GENERATING_POLL_INTERVAL_SECONDS)
            cached = find_cached_ask(dataset, question)
            if not cached or cached.get("status") != "generating":
                waited_on_generation = True
                break
    if cached and cached.get("status") == "ready":
        try:
            payload = json.loads(cached["response_json"])
        except (TypeError, ValueError):
            # Corrupt cache row — caller falls through and regenerates.
            return None
        if not waited_on_generation:
            time.sleep(
                random.uniform(
                    CACHE_HIT_DELAY_MIN_SECONDS, CACHE_HIT_DELAY_MAX_SECONDS
                )
            )
        return payload
    return None


@router.post("")
async def ask(
    body: AskIn,
    # Chat is the Agents module: 403 when the staff panel disabled it.
    company: CompanyContext = Depends(require_agents_module),
):
    """Kick off (or short-circuit) an Ask, returning `{ask_id, status}`.

    Fire-and-forget — mirrors PRD/evidence so a backgrounded or remounted tab
    keeps the answer generating server-side and re-attaches by polling
    `GET /v1/ask/{ask_id}`. The actual answer body is fetched from the status
    endpoint, which returns the SAME citation-stripped shape the old
    synchronous POST returned (so downstream rendering/citation handling is
    unchanged).
    """
    # 0) Tenant gate: the dataset slug must resolve to the caller's company.
    # Without this, an arbitrary client slug would seed a FOREIGN company's
    # corpus into the LLM answer (cross-tenant corpus leak). The company gate
    # (via require_agents_module) scopes the KG half; this scopes the
    # corpus/dataset half. 404 on mismatch.
    require_owned_dataset(body.dataset, company.company_id, company.workspace_id)
    enterprise_id = company.company_id
    # PRD-tab ask: the prd must belong to the caller's company/workspace, or a
    # crafted prd_id would seed a FOREIGN tenant's PRD (+ evidence/tickets)
    # into the answer context. 404 on mismatch, same as the dataset gate.
    if body.prd_id is not None:
        require_owned_prd(body.prd_id, company.company_id, company.workspace_id)

    # History loads BEFORE the cache resolution (not after, as it did before
    # this fix) so eligibility can be derived from it: a thread that already
    # holds an assistant turn must not be served a cache hit that never read
    # that thread. Moving it up costs one extra DB read on a cache hit; it
    # already ran on every miss.
    history = _load_history(body.conversation_id, enterprise_id, company.user_id)

    # 1) Cache hit short-circuit — the home + Ask Sprntly starter chips send
    # deterministic prompts pre-warmed at brief-generation time. We persist the
    # cached answer onto an immediately-`ready` ask job (rather than returning it
    # inline) so the POST contract is uniform — the client always gets an ask_id
    # and reads the body from the status endpoint, cached or generated. The
    # user-visible result is identical (same payload, same synthetic delay).
    # SKIPPED for PRD-tab asks: the cache is keyed on (dataset, question) only,
    # so it would serve a context-free answer for a question about the open PRD.
    # SKIPPED for a mid-thread ask, for the same reason: a thread that already
    # holds an assistant turn has context a cache hit never read. A FIRST-TURN
    # ask deliberately stays eligible even though `conversation_id` is already
    # set on that request — the client awaits conversation creation before
    # asking, so `conversation_id` is non-null on turn one too, and a first-turn
    # ask has no thread yet to be blind to (that's what the starter chips send).
    mid_thread = any(turn.get("role") == "assistant" for turn in history)
    cached_payload = (
        await asyncio.to_thread(_resolve_cache_hit, body.dataset, body.question)
        if (body.prd_id is None and not mid_thread)
        else None
    )
    if cached_payload is not None:
        ask_id = start_ask_job(
            company_id=enterprise_id,
            dataset=body.dataset,
            question=body.question,
            conversation_id=body.conversation_id,
            pinned_skill=body.pinned_skill,
        )
        complete_ask_job(ask_id, _strip_citations(cached_payload))
        return {"ask_id": ask_id, "status": "ready"}

    # 2) Cache miss → persist a generating job and kick the SAME qa_agent
    # pipeline in the background. The worker writes the result/citations onto
    # the job row; the client polls GET /v1/ask/{ask_id} until ready.
    # `history` was already loaded above (before cache resolution).
    ask_id = start_ask_job(
        company_id=enterprise_id,
        dataset=body.dataset,
        question=body.question,
        conversation_id=body.conversation_id,
        pinned_skill=body.pinned_skill,
        prd_id=body.prd_id,
    )
    if "pytest" in sys.modules:
        # The TestClient does not keep the app's event loop alive between
        # requests, so a fire-and-forget create_task would never run and the
        # client's status-poll would spin forever. Run the worker inline under
        # pytest for deterministic results (mirrors decision_log's test-mode
        # handling). Production keeps the non-blocking create_task path below.
        await run_ask_job(
            ask_id=ask_id,
            enterprise_id=enterprise_id,
            question=body.question,
            dataset=body.dataset,
            history=history,
            pinned_skill=body.pinned_skill,
            prd_id=body.prd_id,
            # Attachment context for a captured HTML report: the chat room and
            # PRD this ask ran in (see app/report_capture.py).
            conversation_id=body.conversation_id,
            # The caller's own identity, for the SAME ownership check
            # `_load_history` above already ran — document_grounding
            # (app.ask_runner) re-derives it independently rather than
            # trusting body.conversation_id, since that raw value is
            # otherwise passed through unconditionally regardless of
            # ownership (see app.ask_runner.set_active_conversation).
            user_id=company.user_id,
            workspace_id=company.workspace_id,
        )
        row = get_ask_job(ask_id)
        return {"ask_id": ask_id, "status": (row or {}).get("status", "ready")}

    task = asyncio.create_task(
        run_ask_job(
            ask_id=ask_id,
            enterprise_id=enterprise_id,
            question=body.question,
            dataset=body.dataset,
            history=history,
            pinned_skill=body.pinned_skill,
            prd_id=body.prd_id,
            # Attachment context for a captured HTML report — see above.
            conversation_id=body.conversation_id,
            # The caller's own identity — see above.
            user_id=company.user_id,
            workspace_id=company.workspace_id,
        )
    )
    _inflight_tasks.add(task)
    task.add_done_callback(_inflight_tasks.discard)
    return {"ask_id": ask_id, "status": "generating"}


@router.get("/skills")
def get_skills(company: CompanyContext = Depends(require_company)):
    """The skills the chat composer may offer — the company's OWN uploads.

    COMPANY-SCOPED as of the bare-chat change, which is why this gained an auth
    dependency it never had. It used to serve a process-global list of vendored
    built-ins, so no tenant was involved; it now serves one customer's uploaded
    library, so serving it unauthenticated would hand any caller another
    company's skill names and descriptions.

    See `skill_router.list_available_skills` for why built-ins are no longer
    offered here at all.
    """
    return {"skills": list_available_skills(company.company_id)}


# Same ceiling as PRD import — a slide deck or spec is comfortably under this.
_MAX_EXTRACT_BYTES = 25 * 1024 * 1024  # 25 MB


@router.post("/extract-file")
async def extract_file(
    file: UploadFile = File(...),
    # Attachment extraction only feeds the chat composer → Agents module.
    company: CompanyContext = Depends(require_agents_module),  # noqa: ARG001 — auth gate only
):
    """Parse a chat attachment (pptx/pdf/docx/…) to markdown for ask context.

    The composer can inline plain-text attachments itself, but binary document
    formats need server-side parsing (`app.ingest.convert` — no LLM). Returns
    `{name, markdown}`; the composer appends it to the question as an
    `[Attached files]` block, so a deck attached to a plain question actually
    reaches the agent instead of being silently dropped.
    """
    data = await file.read()
    if not data:
        raise HTTPException(400, "Uploaded file is empty.")
    if len(data) > _MAX_EXTRACT_BYTES:
        raise HTTPException(413, "File too large (max 25 MB).")
    markdown = await asyncio.to_thread(convert, file.filename or "upload", data)
    if not markdown.strip():
        raise HTTPException(
            422,
            "Could not extract any text from the file. Scanned/image-only PDFs "
            "and legacy .ppt are not supported — export to PDF or .pptx.",
        )
    return {"name": file.filename or "upload", "markdown": markdown}


@router.post("/{ask_id}/cancel")
def cancel_ask(
    ask_id: int,
    company: WorkspaceContext = Depends(require_workspace),
):
    """Stop an in-flight Ask (the user realized it was the wrong question).

    Flips the job `generating` → `cancelled`; the background worker polls that
    status between LLM steps and aborts before the next (expensive) call, and a
    late-finishing answer is discarded rather than shown. Idempotent and
    race-safe: if the worker already finished, the update no-ops and this
    returns the real terminal status (`ready`/`error`) instead. 404 if the job
    doesn't belong to the caller's company (no cross-tenant existence
    disclosure — mirrors GET /v1/ask/{id})."""
    row = get_ask_job(ask_id)
    if not row or row.get("company_id") != company.company_id:
        raise HTTPException(404, "Ask not found")
    status = cancel_ask_job(ask_id)
    return {"ask_id": ask_id, "status": status or "cancelled"}


@router.get("/usage")
def get_usage(company: WorkspaceContext = Depends(require_workspace)):
    """Per-enterprise Q&A usage: calls, cost, tokens (total + by agent)."""
    from app.qa_usage import fetch_qa_usage

    return fetch_qa_usage(company.company_id)


# Declared AFTER the static /skills + /usage routes so they aren't shadowed by
# this dynamic int param (FastAPI matches in declaration order).
@router.get("/{ask_id}")
def get_ask(
    ask_id: int,
    company: WorkspaceContext = Depends(require_workspace),
):
    """Status + result for an Ask job.

    Returns `{status, answer, key_points, citations, confidence, unanswered,
    error, routed_skill, routed_skill_action}`. Once `status == 'ready'` the
    answer/key_points/citations/etc. fields carry the SAME citation-stripped
    shape the old synchronous POST returned, so downstream rendering is
    unchanged. 404 if the job doesn't belong to the caller's company (no
    cross-tenant existence disclosure)."""
    row = get_ask_job(ask_id)
    if not row or row.get("company_id") != company.company_id:
        raise HTTPException(404, "Ask not found")
    status = row.get("status") or "generating"
    payload = row.get("response") or {}
    return {
        "status": status,
        "error": row.get("error"),
        # The skill the router picked, readable from `generating` onwards — the
        # rest of this body is empty until the job is ready, so this is the only
        # thing a waiting client can learn about what is actually running.
        # Both stay null when the router selected nothing (a direct answer, an
        # out-of-scope refusal, or one of qa_agent's pre-routing interceptors):
        # null means "no skill", never "unknown skill", and the client is
        # expected to show nothing rather than fall back to a guess.
        "routed_skill": row.get("routed_skill"),
        "routed_skill_action": row.get("routed_skill_action"),
        "answer": payload.get("answer", ""),
        "key_points": payload.get("key_points", []),
        "citations": payload.get("citations", []),
        "confidence": payload.get("confidence", 0),
        "unanswered": payload.get("unanswered", ""),
        # Server-derived document manifest (existence-vs-retrieval contract) —
        # defaulted explicitly so a warm/cached row from before this ticket
        # (no "documents" key) still returns [] rather than dropping the key.
        "documents": payload.get("documents", []),
        # Pass through any extra fields the qa_agent attaches (e.g. confirm-gate
        # metadata, the payload's own `_skill`) so the contract stays a superset
        # of the old body. The two routed_skill* keys are excluded so the job
        # row's columns stay authoritative for them at every status — a stored
        # payload can never shadow the value the client already saw mid-run.
        **{
            k: v
            for k, v in payload.items()
            if k
            not in {
                "answer",
                "key_points",
                "citations",
                "confidence",
                "unanswered",
                "routed_skill",
                "routed_skill_action",
                "documents",
            }
        },
    }


@router.get("/{ask_id}/stream")
async def stream_ask_generation(
    ask_id: int,
    company: WorkspaceContext = Depends(require_workspace_from_query),
) -> StreamingResponse:
    """SSE token stream of a chat answer as it generates, so the reply renders
    word-by-word instead of appearing whole (mirrors GET /v1/prd/{id}/stream).

    EventSource can't send headers, so the bearer rides as `?token=`
    (require_workspace_from_query). Frames: an optional `{"kind":"replay",…}`
    catch-up (everything emitted before this client connected — a remounted tab
    re-attaching mid-answer), `{"kind":"delta","text":…}` carrying decoded
    answer markdown (the `answer` field only — key_points/citations/confidence
    arrive with the poll), then a terminal `{"kind":"done"|"error"}`.
    `{"kind":"restart"}` may appear between deltas: a transient gateway failure
    made the generation re-emit from zero, so everything streamed before it is
    superseded and the client drops its accumulated text (see
    app.graph.token_stream). A client that ignores the kind degrades to
    rendering both attempts until the poll replaces the preview.

    PROGRESSIVE DISPLAY ONLY — the client keeps polling GET /v1/ask/{id}, which
    stays the authoritative source of the finished answer. Cache-hit and
    non-streamable answers (HTML reports, script tool-loops) publish no deltas:
    this stream stays silent and the poll delivers the whole reply, unchanged.
    Single-worker transport (see app.graph.token_stream); on multi-worker this
    yields nothing and the poll still carries the result. 404 on a foreign or
    missing job (no cross-tenant existence disclosure — mirrors GET /{ask_id}).
    """
    row = get_ask_job(ask_id)
    if not row or row.get("company_id") != company.company_id:
        raise HTTPException(404, "Ask not found")
    channel = ask_channel(ask_id)

    async def _gen():
        async for event in token_stream.subscribe(channel):
            yield f"data: {json.dumps(event)}\n\n"

    return StreamingResponse(
        _gen(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )
