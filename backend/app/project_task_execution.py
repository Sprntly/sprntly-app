"""`execute_task` tool + best-effort inline agent-execution handler.

The engine's other promise (spec, alongside `delegate_task`): the agent
doesn't only chase people, it does the tasks it actually can, then hands
off the rest. This module wires the LOCKED v1 agent-doable set — PRD
draft ONLY — so a member can ask Sprntly, in either project chat surface,
to draft the PRD itself: the agent runs the EXISTING PRD generate pipeline
(`app.prd_runner._generate_human_prd` + `extract_input_questions_task`)
and posts the draft plus its finalize/input questions, instead of routing
the ask to a human.

Structured like `project_delegation.py`'s `delegate_task` sibling:
`handle_execute_task` is the best-effort, never-raising `dispatch` target
for BOTH the group agent's `run_tool_loop` (`routes/projects.py::
_respond_as_group_agent`) and the private-chat loop (`project_individual_
agent.py::respond_individual`).

Agent-execution here is INLINE and STATELESS: it produces the artifact and
posts it — it creates NO `project_delegations` row and NO agent pseudo-
user (the human-only delegation schema is untouched). Anything outside the
doable set — including an analysis ask, which has no wired project-surface
generator — declines and routes the member to `delegate_task` instead,
so the promise stays honest rather than over-broad.
"""
from __future__ import annotations

import asyncio
import logging
import time
from typing import Callable

from app.llm import DEFAULT_MODEL
from app.llm_telemetry import RunUsage, log_llm_run

logger = logging.getLogger(__name__)

# The ONLY project-surface task type with a wired generate path in v1.
# Analysis is explicitly deferred — no project `analysis` artifact type
# exists and the only analysis generator (`agents/risk_analysis.py::
# generate_risk_analysis`) has no `project_id` entry point, so wiring it
# here would be new generation surface, which this ticket does not add.
AGENT_DOABLE_TYPES = ("prd",)

EXECUTE_TASK_TOOL = {
    "name": "execute_task",
    "description": (
        "Call this ONLY when a member asks YOU (Sprntly) to DRAFT A PRD yourself for "
        "THIS project (\"draft the PRD for this\", \"put together the PRD\"). Drafting "
        "a PRD is the one task you can do directly right now. Do NOT call this to hand "
        "work to a teammate (use delegate_task for that), to answer a question, or for "
        "any other kind of task — including running an ANALYSIS, which is NOT yet "
        "supported and must be delegated to a person. For anything other than a PRD "
        "draft, decline and suggest delegating it."
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "task_type": {
                "type": "string",
                "enum": ["prd"],
                "description": "the generate path to run (v1: prd only)",
            },
            "task_summary": {
                "type": "string",
                "description": "the concrete thing to produce, from the conversation",
            },
        },
        "required": ["task_type", "task_summary"],
        "additionalProperties": False,
    },
}

_DECLINE_NOT_DOABLE = (
    "Drafting a PRD is the only task I can do myself right now — for anything "
    "else, including an analysis, I'd need to hand it to a person. Want me to "
    "delegate this instead?"
)
_DECLINE_MISSING_SUMMARY = (
    "I need a concrete description of what to draft — ask again with the details."
)
_DECLINE_GENERIC_FAILURE = (
    "I couldn't draft that just now — want me to hand it to someone, or try again?"
)

# Storage sentinel — insight_index is not a real brief-insight index for a
# chat-sourced PRD (mirrors `routes/prd.py::_CHAT_TASK_INSIGHT_INDEX`); it
# only anchors the (brief_id, insight_index) pair the rest of the PRD
# machinery expects.
_INSIGHT_INDEX = 0


def _author_name(user_id: str | None, roster: list[dict]) -> str | None:
    """The requester's display name, from an already-fetched roster (fetch-
    once, mirrors `project_delegation._build_brief`'s assigner lookup) — no
    second `list_members` read."""
    if not user_id:
        return None
    match = next((m for m in roster if m.get("user_id") == user_id), None)
    return (match or {}).get("name")


def _post(post_turn: "Callable[[str], None] | None", message: str) -> None:
    """Best-effort delivery of the outcome into the originating chat. A
    missing/failing `post_turn` must never turn a successful draft into a
    reported failure (AD-P22 posture) — the handler's returned string still
    lets the model relay the outcome to the member even if this explicit
    post failed."""
    if post_turn is None:
        return
    try:
        post_turn(message)
    except Exception:  # noqa: BLE001 — best-effort, never masks a successful generate
        logger.warning("execute_task_post_turn_failed")


def _log_execute_run(
    *,
    project_id: int,
    meta: dict,
    start: float,
    status: str,
    error_class: str | None = None,
) -> None:
    """The one structured cost-summary line for an LLM call made DIRECTLY by
    this handler (AD-P15) — NOT the reused PRD pipeline's own call, which is
    already logged through `prd_runner`'s own `llm_call` telemetry. On the
    v1 PRD-only path this handler makes no such direct call (generation is
    reused verbatim), so this helper is not exercised by `_execute_prd`
    today; it exists so any future handler-level LLM call has a ready,
    tested, identifier-only (never task_summary/body) logging path rather
    than one added ad hoc. Never raises."""
    try:
        log_llm_run(
            operation="projects.task.execute",
            identifier={"project_id": project_id},
            usage=RunUsage(
                cache_creation_input_tokens=meta.get("cache_creation_input_tokens", 0),
                cache_read_input_tokens=meta.get("cache_read_input_tokens", 0),
                input_tokens=meta.get("input_tokens", 0),
                output_tokens=meta.get("output_tokens", 0),
            ),
            duration_ms=int((time.monotonic() - start) * 1000),
            status=status,
            model=meta.get("model") or DEFAULT_MODEL,
            error_class=error_class,
        )
    except Exception:  # noqa: BLE001 — observability must never break execution
        logger.warning("execute_task_cost_log_failed project_id=%s", project_id)


async def _run_prd_generation(
    prd_id: int, brief_id: int, insight_index: int, insight: dict, author: str | None,
) -> None:
    """The reused-pipeline call, isolated in its own coroutine so the sync
    dispatch call site (`run_tool_loop`'s `dispatch`, which runs on a worker
    thread with no event loop of its own — see `ask_job_runner._run_sync`
    and `routes/projects.py::post_group_turn_route`) can drive it via
    `asyncio.run`. Calls the EXISTING pipeline verbatim — no new generation
    function is defined here."""
    from app.prd_runner import _generate_human_prd, extract_input_questions_task

    await _generate_human_prd(
        prd_id, brief_id, insight_index, insight_override=insight, author=author,
    )
    await extract_input_questions_task(prd_id)


def _execute_prd(
    *,
    project_id: int,
    requester_user_id: str | None,
    dataset: str,
    task_summary: str,
    roster: list[dict],
    post_turn: "Callable[[str], None] | None",
) -> str:
    """`task_type == "prd"`: create/resolve the project's PRD artifact the
    same way `routes/prd.py::generate_from_task` does (theme-keyed find-or-
    create against the dataset's current/uploads brief), reuse the EXISTING
    generate pipeline to write it, then attach it to THIS project
    (`add_artifact`) and post the outcome. Fail-closed (AD-P19 delivery-
    then-record ordering): the artifact is only attached to the project and
    the outcome only posted AFTER generation succeeds — a raise from the
    reused pipeline is caught below and returns a decline with no artifact
    attached and nothing posted."""
    try:
        from app.db.briefs import ensure_uploads_brief, get_current_brief
        from app.db.prds import find_existing_prd_for_theme, start_prd
        from app.db.projects import add_artifact
        from app.prd_runner import PRD_VARIANT
        from app.prompts import PRD_TEMPLATE_VERSION
        from app.routes.prd import _chat_task_theme_id, _chat_task_title

        theme_id = _chat_task_theme_id(task_summary)
        title = _chat_task_title(task_summary)

        brief = get_current_brief(dataset)
        brief_id = brief["id"] if brief else ensure_uploads_brief(dataset)

        existing = find_existing_prd_for_theme(brief_id, theme_id, variant=PRD_VARIANT)
        if existing is not None:
            prd_id = existing["id"]
            add_artifact(project_id, "prd", prd_id)
            message = (
                f"I'd already drafted “{existing.get('title') or title}” for "
                "this — I've attached it to this project. Check the PRD tab and "
                "answer the finalize questions there."
            )
            _post(post_turn, message)
            return message

        author = _author_name(requester_user_id, roster)
        prd_id = start_prd(
            brief_id=brief_id,
            insight_index=_INSIGHT_INDEX,
            title=title,
            template_version=PRD_TEMPLATE_VERSION,
            variant=PRD_VARIANT,
            source="chat",
            theme_id=theme_id,
            question=task_summary,
        )
        insight = {
            "title": title,
            "summary": f"Requested by the user in chat: {task_summary}",
            "query": task_summary,
        }

        asyncio.run(_run_prd_generation(prd_id, brief_id, _INSIGHT_INDEX, insight, author))

        # Attach + post ONLY after a successful generate (AD-P19 ordering) —
        # a raise above is caught below with neither of these having run.
        add_artifact(project_id, "prd", prd_id)
        message = (
            f"I've drafted the PRD “{title}” — check the PRD tab and answer "
            "the finalize questions there. I'll treat this as done unless you "
            "say otherwise."
        )
        _post(post_turn, message)
        return message
    except Exception as exc:  # noqa: BLE001 — AD-P7/AD-P19: no partial artifact, no raise
        logger.warning(
            "execute_task_prd_failed project_id=%s error_class=%s",
            project_id, type(exc).__name__,
        )
        return _DECLINE_GENERIC_FAILURE


def handle_execute_task(
    *,
    project_id: int,
    requester_user_id: str | None,
    dataset: str,
    company_id: str,
    tool_input: dict,
    roster: list[dict] | None = None,
    post_turn: "Callable[[str], None] | None" = None,
) -> str:
    """Best-effort dispatch handler for the `execute_task` tool — never
    raises (AD-P7); on ANY failure (bad input, a non-doable type, or a
    downstream generate failure) it returns a safe decline string and
    performs no write beyond what already succeeded before the failure.

    Signature mirrors `project_delegation.handle_delegate_task`'s shape
    (`project_id`, a requester identity, `dataset`/`company_id`,
    `tool_input`), plus `roster` (author-name resolution, reusing an
    already-fetched roster like `handle_delegate_task` does) and
    `post_turn` (a one-arg callback each call site supplies for its own
    conversation writer — `conversations_db.post_group_turn` for the group
    agent, `post_individual_turn` for the private chat — so this module
    stays decoupled from which chat surface called it).

    `company_id` is accepted for signature parity with `handle_delegate_task`
    and future doable types; the v1 PRD path does not need it directly (PRD
    creation is dataset/brief-scoped, not company-UUID-scoped)."""
    try:
        tool_input = tool_input or {}
        task_type = (tool_input.get("task_type") or "").strip()
        task_summary = (tool_input.get("task_summary") or "").strip()

        if task_type not in AGENT_DOABLE_TYPES:
            return _DECLINE_NOT_DOABLE

        if not task_summary:
            return _DECLINE_MISSING_SUMMARY

        if task_type == "prd":
            return _execute_prd(
                project_id=project_id,
                requester_user_id=requester_user_id,
                dataset=dataset,
                task_summary=task_summary,
                roster=roster or [],
                post_turn=post_turn,
            )

        # Unreachable while AGENT_DOABLE_TYPES == ("prd",) — fail-closed
        # rather than fall through silently if the set ever grows without a
        # matching branch here.
        return _DECLINE_NOT_DOABLE
    except Exception as exc:  # noqa: BLE001 — AD-P7: never raise into the tool loop
        logger.warning(
            "execute_task_failed project_id=%s error_class=%s",
            project_id, type(exc).__name__,
        )
        return _DECLINE_GENERIC_FAILURE
