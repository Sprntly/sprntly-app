"""Bounded tool-loop responder for the PRIVATE project chat ("My chat with
Sprntly") — the `answer` executor only.

The private individual thread historically answers single-shot
(`qa_agent._answer_single_shot`) with no tools, so it can neither reach the
project's full memory/artifacts/ledger the @Sprntly group agent can. This
module runs the SAME project read tools the group agent uses (imported from
`app.project_group_context` — never forked, so the tenancy gate stays
single-sourced) on a bounded `run_tool_loop`, PLUS the same `delegate_task`
tool the group agent carries (`app.project_delegation` — reused verbatim,
never forked) so a member can hand a task off from their own private chat,
not only the group chat, PLUS the same `execute_task` tool the group agent
carries (`app.project_task_execution` — reused verbatim, never forked) so a
member can ask Sprntly to draft the one v1 agent-doable task (a PRD) from
their own private chat too. `handle_delegate_task`/`handle_execute_task` own
their own gates and generation; this module only threads the caller's
identity through, injects the project roster so the model can resolve a
free-text assignee, and (for `execute_task`) supplies a `post_turn` callback
so the drafted outcome lands back in this same conversation.

PRD edits no longer flow through this responder: the client-side intent
classifier (`dispatchChatIntent`, `web/app/lib/chat/dispatchChatIntent.ts`)
peels an `edit_prd`-classified message off BEFORE it ever reaches `/v1/ask`,
routing it to the in-place, versioned `POST /v1/projects/{id}/prd/chat-edit`
instead (the shared `apply_chat_edit_scoped` + the ★ cross-project IDOR gate).
This responder therefore no longer wires the propose-PRD-patch tool
(`project_prd_patch_tool.py`, the retired propose/review flow) — an edit-phrased
`answer` turn (one that reached here anyway, e.g. the classifier abstained)
creates no `prd_patches` row; it answers in text only, same as any other
read-tool question.

Contract (mirrors `_respond_as_group_agent`): bounded (`max_iters=5`), exactly
one structured cost line per reply (identifiers only — never body/question),
and best-effort (AD-P7) — on ANY failure it degrades to the caller's single-shot
answer rather than raising, so a project ask never 500s. It returns a payload
DICT shaped like the single-shot answer (`{"answer", "citations"}`) so the
existing `ask_job_runner` downstream (strip/complete/capture/log) runs
unchanged.

Cancellation (v1 scope): `run_tool_loop` has no `is_cancelled` hook, so a user
Stop cannot abort an in-flight project tool loop server-side — it is bounded by
`max_iters` instead (best-effort). Non-project asks keep their existing
`is_cancelled` threading unchanged (they never enter this module).
"""
from __future__ import annotations

import logging
import time
from typing import Callable

from app import project_delegation
from app import project_task_execution
from app.db import projects as projects_db
from app.db.conversations import post_individual_turn
from app.llm import DEFAULT_MODEL, run_tool_loop
from app.llm_telemetry import RunUsage, log_llm_run
from app.project_group_context import dispatch_read_tool, read_tools

logger = logging.getLogger(__name__)

_SYSTEM = (
    "You are Sprntly, the user's private project assistant in their one-on-one "
    "chat. Answer the user's question about THIS project directly and concisely. "
    "You have tools to read the project's shared memory, its artifact list, a "
    "specific artifact's content, and its task ledger — call them when the answer "
    "depends on project data rather than guessing. When the user asks for the "
    "whole picture — e.g. 'give me the entire context on this project', 'catch me "
    "up', or 'what's the why and goal here' — first read the project's shared "
    "memory (and its artifacts/ledger as needed), then synthesize the why, the "
    "goal, the current state, who's assigned to what, and prior work — grounded in "
    "what you read, never generic. When the user asks you to change the PRD, the "
    "edit is applied to the document in place and a new version is saved "
    "automatically so the change is undoable — it is NOT queued for approval and "
    "does not need a teammate to manually accept it before it takes effect. Never "
    "describe your role as merely advisory, or claim you cannot edit the PRD, or "
    "say edits must be accepted before they apply. Everything you can read is "
    "scoped to this one project; never assume data from another project or "
    "company."
)


def _roster_prompt_block(roster: list[dict]) -> str:
    """"PROJECT ROSTER:\n- {first} — {job_role}" — mirrors
    `routes.projects._group_system_with_roster`'s per-line rendering
    exactly, so the private surface resolves a free-text assignee ("the
    designer") to the same names/roles the group agent's roster block
    uses. Built from an ALREADY-fetched `roster` list (fetch-once, AD-P7)
    rather than a second `list_members` read."""
    lines = []
    for m in roster:
        name = m.get("name") or "(unnamed)"
        first = name.split()[0] if name != "(unnamed)" else name
        role = m.get("job_role") or "no role set"
        lines.append(f"- {first} — {role}")
    return "PROJECT ROSTER:\n" + ("\n".join(lines) if lines else "(no other members yet)")


def _render_transcript(history: list[dict], question: str) -> str:
    """Render prior turns + the new question into the loop's user message. Each
    history turn is `{role, content}`-shaped (assistant turns are the agent's);
    an unknown/absent role renders as the user."""
    lines: list[str] = []
    for turn in history or []:
        role = (turn.get("role") or "user").strip().lower()
        content = (turn.get("content") or "").strip()
        if not content:
            continue
        speaker = "Sprntly" if role == "assistant" else "User"
        lines.append(f"{speaker}: {content}")
    lines.append(f"User: {question}")
    return "\n".join(lines)


def respond_individual(
    *,
    project_id: int,
    dataset: str,
    company_id: str,
    question: str,
    history: list[dict],
    single_shot: Callable[[], dict],
    assigner_user_id: str | None = None,
    source_conversation_id: int | None = None,
) -> dict:
    """Produce the private-chat reply for a project ask via a bounded tool loop.

    Returns a payload dict `{"answer": <text>, "citations": []}` (single-shot
    shape). On ANY failure, degrades to `single_shot()` (the caller's own
    `qa_agent.answer(...)`), so the request still returns an answer body — never
    a 500 (AD-P7). Registers the project read tools AND `delegate_task` (reused
    verbatim from `app.project_delegation` — never forked); no PRD-write tool
    is registered — an edit-phrased turn that reaches here answers in text, it
    never creates a `prd_patches` row (edits are classified client-side, see
    the module docstring). `assigner_user_id`/`source_conversation_id` are the
    caller's own identity, threaded from `ask_job_runner` — a missing
    `assigner_user_id` degrades `delegate_task` to a safe decline string
    (`handle_delegate_task`'s own guard), never a 500."""
    start = time.monotonic()
    try:
        roster = projects_db.list_members(project_id)
    except Exception:  # noqa: BLE001 — best-effort, AD-P7
        roster = []
    tools = [
        project_delegation.DELEGATE_TASK_TOOL,
        project_task_execution.EXECUTE_TASK_TOOL,
        *read_tools(),
    ]
    system = f"{_SYSTEM}\n\n{_roster_prompt_block(roster)}"
    meta: dict = {}

    def _dispatch(name: str, tool_input: dict) -> str:
        # Project-scoped read tools first; returns None when `name` isn't
        # one of them, so delegate_task and the unknown-tool fallback below
        # still apply.
        read = dispatch_read_tool(
            name, tool_input,
            project_id=project_id, dataset=dataset, company_id=company_id,
        )
        if read is not None:
            return read
        if name == "delegate_task":
            return project_delegation.handle_delegate_task(
                project_id=project_id,
                assigner_user_id=assigner_user_id,
                source_conversation_id=source_conversation_id,
                source_turn_id=None,
                roster=roster,
                dataset=dataset,
                company_id=company_id,
                tool_input=tool_input,
            )
        if name == "execute_task":
            post_turn = (
                (lambda content: post_individual_turn(source_conversation_id, "assistant", content))
                if source_conversation_id is not None else None
            )
            return project_task_execution.handle_execute_task(
                project_id=project_id,
                requester_user_id=assigner_user_id,
                dataset=dataset,
                company_id=company_id,
                tool_input=tool_input,
                roster=roster,
                post_turn=post_turn,
            )
        return f"(unknown tool: {name})"

    try:
        text = run_tool_loop(
            system=system,
            user=_render_transcript(history, question),
            tools=tools,
            dispatch=_dispatch,
            model=DEFAULT_MODEL,
            max_iters=5,
            meta_out=meta,
        )
    except Exception:  # noqa: BLE001 — AD-P7: degrade to the single-shot answer
        logger.warning(
            "individual_project_reply_degraded project_id=%s", project_id
        )
        return single_shot()

    # Exactly one structured cost line per project reply — identifiers only,
    # never the body/question (Rule #24). Emitted only on the tool-loop path;
    # the degraded path's telemetry is single_shot's own.
    usage = RunUsage(
        cache_creation_input_tokens=meta.get("cache_creation_input_tokens", 0),
        cache_read_input_tokens=meta.get("cache_read_input_tokens", 0),
        input_tokens=meta.get("input_tokens", 0),
        output_tokens=meta.get("output_tokens", 0),
    )
    log_llm_run(
        operation="projects.individual_chat.reply",
        identifier={"project_id": project_id},
        usage=usage,
        duration_ms=int((time.monotonic() - start) * 1000),
        status="complete",
        model=meta.get("model") or DEFAULT_MODEL,
        mode="individual",
    )
    return {"answer": text, "citations": []}
