"""Project-surface context assembler — registers under `context_source`
kind `"project"`.

Produces a BREADTH-ONLY `SurfaceScope` for the private ("My chat with
Sprntly") project-chat surface: the project's roster + task-ledger digest +
artifact manifest + memory, framed authoritatively, reusing the EXISTING
single-sourced assembler in `app.project_group_context`
(`assemble_private_project_context`).

Membership gate FIRST (IDOR-critical), ported verbatim in shape from the
pre-collapse caller (`routes/ask.py` at commit `b09801dd^`): a project not in
the caller's `(company, workspace)` 404s, a same-tenant NON-member 403s — and
BOTH checks run BEFORE any project data is read, so knowing a project id can
never leak its memory into an answer. The gate is NOT best-effort: it raises.

Breadth AND depth: `extra_tools` carries the 7 project tools (4 shared read
tools + `delegate_task` + `execute_task` + `complete_task`), so the EXISTING
sixth tool-loop branch in `qa_agent.answer` (`_try_scoped_tool_answer`, which
reads `scope.extra_tools`) claims a project-content / delegate / execute /
complete turn. A
plain-Q&A project ask is NOT claimed by that branch — its own intent gate
decides — and still reaches the composer via `qa_agent._fold_project_context`,
which prepends the authoritative preamble itself, so this assembler must NOT
prepend it to `context_payload` (doing so would double it on the fold path).

Block assembly IS best-effort (AD-P7): a read failure degrades to an empty
block and never blocks the answer. Returns a `SurfaceScope` (the type
`answer(scope=...)` already consumes), populating the breadth fields only.
"""
from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone

from fastapi import HTTPException

from app.context_assembler import AssembleRequest
from app.surface_scope import Surface, SurfaceScope

logger = logging.getLogger(__name__)

# ── In-session "are you done?" check (A5, request-time flag-gated) ─────────
#
# Feature: while the ASSIGNEE is chatting in their OWN private project chat,
# proactively surface a "have you finished this?" nudge for their OWN open
# delegated task(s) — woven into the model's normal reply via an injected
# instruction, never a mechanical append and never a new interjection
# channel. The instruction only ever asks; it explicitly forbids marking a
# task done or assuming it is done. Completion capture is unchanged and
# unrelated to this feature — a "yes" reply is still caught exclusively by
# the EXISTING `delegation_status_ingest.maybe_ingest_status` classifier
# (wired at `ask_job_runner.py`'s post-answer `_on_committed`), which this
# module never imports and never calls.
#
# Throttled per delegation via `delegation_followups.last_insession_ask_at`
# (see `db/delegation_followups.py` / the
# `20260902120000_delegation_followups_insession_ask.sql` migration): a task
# is only (re-)injected once its last ask is NULL or older than
# `_INSESSION_ASK_WINDOW_HOURS`. The ask path has no first-class
# session/thread boundary of its own, so this window is a deliberate proxy —
# "not asked about within N hours" reads as "a new session" — rather than a
# literal session id.
_INSESSION_ASK_WINDOW_HOURS = 6

#: Cap on how many of the assignee's own open tasks get named in one
#: instruction — mirrors `project_group_context._LEDGER_DIGEST_ROWS`'s
#: soft-cap posture; a heavily-delegated assignee can't blow up the prompt.
_INSESSION_TASK_CHECK_CAP = 3

#: Per-task summary length cap in the injected instruction (bounded-length
#: LLM-facing text) — mirrors `project_group_context._MANIFEST_TITLE_CHARS`'s
#: truncate-with-ellipsis posture.
_INSESSION_TASK_SUMMARY_CHARS = 160


def _parse_iso(value) -> datetime | None:
    """Best-effort ISO-8601 -> aware UTC datetime; any missing/unparseable
    value degrades to `None` (mirrors `delegation_status_ingest._parse_iso`
    — duplicated rather than imported, matching this codebase's existing
    per-module `_parse_iso` precedent)."""
    if not value:
        return None
    if isinstance(value, datetime):
        dt = value
    else:
        text = str(value).strip()
        if text.endswith("Z"):
            text = text[:-1] + "+00:00"
        try:
            dt = datetime.fromisoformat(text)
        except ValueError:
            return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def _insession_task_check_block(project_id: int, user_id: str | None) -> str:
    """The A5 injected instruction naming the CALLER's own open delegated
    task(s) in this project — or `""` when: the flag is off, there is no
    caller identity, the caller has no open delegation, or every open
    delegation was already asked about within
    `_INSESSION_ASK_WINDOW_HOURS`. Ask-only: the returned text always
    forbids marking a task done or assuming it is done — never a
    completion side-effect. Best-effort (AD-P7): every DB read/write below
    is individually swallowed so a failure degrades to omitting the
    instruction, never to blocking the reply. Records the throttle write
    (`last_insession_ask_at = now`) for every task actually surfaced, so a
    read failure that never reaches the write can't leave a stale marker
    behind."""
    from app.config import settings

    if not settings.insession_task_check_enabled or not user_id:
        return ""

    from app.db import delegation_events as delegation_events_db

    try:
        open_rows = [
            row
            for row in delegation_events_db.list_status_for_assignee(project_id, user_id)
            if row.get("status") in delegation_events_db.OPEN_STATES
        ]
    except Exception:  # noqa: BLE001 — best-effort, AD-P7
        logger.warning(
            "insession_task_check_prefilter_failed project_id=%s",
            project_id, exc_info=True,
        )
        return ""
    if not open_rows:
        return ""

    from app.db import delegation_followups as delegation_followups_db

    now = datetime.now(timezone.utc)
    cutoff = now - timedelta(hours=_INSESSION_ASK_WINDOW_HOURS)

    due: list[dict] = []
    for row in open_rows:
        delegation_id = row.get("delegation_id")
        if delegation_id is None:
            continue
        try:
            followup = delegation_followups_db.get_followup(delegation_id)
        except Exception:  # noqa: BLE001 — best-effort, AD-P7
            followup = None
        last_ask = _parse_iso((followup or {}).get("last_insession_ask_at"))
        if last_ask is not None and last_ask > cutoff:
            continue  # asked within the window already — throttled
        due.append(row)
        if len(due) >= _INSESSION_TASK_CHECK_CAP:
            break
    if not due:
        return ""

    for row in due:
        try:
            delegation_followups_db.upsert_followup(
                row["delegation_id"], last_insession_ask_at=now
            )
        except Exception:  # noqa: BLE001 — best-effort, AD-P7: a write
            # failure only risks a possibly-early re-ask next turn; it must
            # never suppress THIS turn's instruction.
            logger.warning(
                "insession_task_check_upsert_failed delegation_id=%s",
                row.get("delegation_id"), exc_info=True,
            )

    summaries = []
    for row in due:
        summary = (row.get("task_summary") or "").strip() or "(no summary)"
        if len(summary) > _INSESSION_TASK_SUMMARY_CHARS:
            summary = summary[:_INSESSION_TASK_SUMMARY_CHARS].rstrip() + "…"
        summaries.append(summary)
    tasks_text = "; ".join(f"'{s}'" for s in summaries)
    plural = "s" if len(summaries) > 1 else ""

    return (
        f"The user has an open task{plural} assigned to them: {tasks_text}. If "
        "their current message relates to it, naturally ask whether they've "
        "finished it so it can be reported back. Do NOT mark it done, and do "
        "NOT assume it's done — only ask. If you have already asked about "
        "this task earlier in this conversation, do not ask again."
    )


class ProjectContextAssembler:
    """`ContextAssembler` for `kind == "project"`. See module docstring for the
    membership-gate and breadth-only contract."""

    def assemble(self, req: AssembleRequest) -> SurfaceScope:
        params = req.params or {}
        project_id_raw = params.get("project_id")

        # No project to scope to → behave as the no-source main path (a
        # main-surface scope is a no-op ALIAS for `scope is None`).
        if project_id_raw is None:
            return SurfaceScope(surface=Surface.main)
        project_id = int(project_id_raw)

        # ── Membership gate FIRST (IDOR-critical) ────────────────────────────
        # Ported from `b09801dd^:backend/app/routes/ask.py`: 404 when the
        # project isn't in the caller's `(company, workspace)` (same
        # non-disclosure posture as the dataset/prd gates — "exists but not
        # yours" and "doesn't exist" are indistinguishable), 403 when the caller
        # is a same-tenant NON-member. Both run BEFORE any project data is read.
        from app.db.projects import is_project_member, project_belongs_to_company

        if not project_belongs_to_company(
            project_id, req.company_id, req.workspace_id
        ):
            raise HTTPException(404, "Project not found")
        if not is_project_member(project_id, req.user_id):
            raise HTTPException(403, "Not a member of this project")

        surface = Surface.project_private

        # ── Breadth block ────────────────────────────────────────────────────
        # The single-sourced private assembler — the caller's own memory +
        # roster/ledger/manifest. Best-effort (AD-P7): a read failure degrades
        # to an empty block. The authoritative preamble is added by
        # `qa_agent._fold_project_context`, NOT here (folding it in would
        # double it).
        block = ""
        try:
            from app import project_group_context

            block = project_group_context.assemble_private_project_context(
                project_id, req.user_id, req.dataset, req.company_id
            )
        except Exception:  # noqa: BLE001 — best-effort, never blocks the answer
            logger.warning(
                "project context assembly failed project_id=%s surface=%s",
                project_id, surface.value, exc_info=True,
            )
            block = ""

        # ── Project instructions block ───────────────────────────────────────
        # Single-sourced format both surfaces use (`_instructions_block`); folded
        # into `system_addendum` below (never `context_payload`). Best-effort.
        instr_block = ""
        try:
            from app.db import projects as projects_db
            from app.project_group_context import _instructions_block

            instr_block = _instructions_block(
                projects_db.get_instructions(project_id)
            )
        except Exception:  # noqa: BLE001 — best-effort
            instr_block = ""

        # ── In-session "are you done?" check (A5) ────────────────────────────
        # See the module-level `_insession_task_check_block` docstring. Flag
        # off (the default) or any read/write failure both degrade to "" —
        # byte-identical to pre-A5 behavior either way.
        insession_check_block = ""
        try:
            insession_check_block = _insession_task_check_block(
                project_id, req.user_id
            )
        except Exception:  # noqa: BLE001 — best-effort, AD-P7
            logger.warning(
                "insession_task_check_block_failed project_id=%s",
                project_id, exc_info=True,
            )
            insession_check_block = ""

        # ── Depth tools (the breadth → depth flip) ───────────────────────────
        # Populate `extra_tools` with the 7 project tools so the EXISTING sixth
        # ladder branch (`qa_agent._try_scoped_tool_answer`, which reads
        # `scope.extra_tools`) claims a project-content / delegate / execute /
        # complete turn. Ported in shape — NOT reimplemented — from `b09801dd^:
        # app/ask_job_runner._build_private_scope`: the 4 shared read tools +
        # `delegate_task` + `execute_task` + `complete_task`, with the three sidecar fields that
        # branch's dispatch consumes: `roster` (free-text assignee → member
        # resolution), `assigner_identity` (delegation attribution) and
        # `post_turn` (execute-task progress posts). All best-effort (AD-P7).
        #
        # This does NOT route EVERY project ask through the tool loop: the sixth
        # branch's own intent gate (`is_project_tool_request` /
        # `is_project_content_request` / bare-send / edit) decides which turns it
        # claims. A plain-Q&A project ask still falls through to the breadth/
        # composer path below the branch, exactly as it did with empty tools.
        from app import project_delegation, project_task_execution
        from app.db import projects as projects_db
        from app.project_group_context import read_tools

        try:
            roster = projects_db.list_members(project_id)
        except Exception:  # noqa: BLE001 — best-effort, AD-P7
            roster = []

        # ── system_addendum composition ──────────────────────────────────────
        # Ported verbatim in COMPOSITION from `b09801dd^:app/ask_job_runner.
        # _build_private_scope`: the private surface folds the relocated
        # tool-usage system guidance (`_PRIVATE_SCOPE_SYSTEM`, which itself already
        # appends `PROJECT_TOOL_NUDGE`) + the roster block + the project
        # instructions, so the model gets WHEN/HOW guidance for delegate_task /
        # execute_task / edit-in-place alongside the 6 depth tools — not just the
        # facts. Reuses the `roster` fetched just above for the sidecars (no
        # re-fetch). The constants/helper are imported (not reimplemented) from
        # `app.ask_job_runner`, where they still live on this commit.
        from app.ask_job_runner import (
            _PRIVATE_SCOPE_COMPOSER_FOLD,
            _PRIVATE_SCOPE_SYSTEM,
            _private_roster_block,
        )

        system_addendum = (
            f"{_PRIVATE_SCOPE_SYSTEM}\n\n{_private_roster_block(roster)}"
        )
        if instr_block:
            system_addendum = f"{system_addendum}\n\n{instr_block}"
        if insession_check_block:
            system_addendum = f"{system_addendum}\n\n{insession_check_block}"

        # The gate-decline / composer fall-through's OWN addendum — same
        # roster/instructions composition, but built from the delegate-tool-
        # guidance-free `_PRIVATE_SCOPE_COMPOSER_FOLD` instead of the full
        # `_PRIVATE_SCOPE_SYSTEM` (see `SurfaceScope.composer_fold_addendum`).
        #
        # `insession_check_block` is folded into BOTH addenda, same as
        # `instr_block` just above it: a plain "how's it going" message from
        # the assignee is exactly the kind of turn the gate declines (no
        # delegate/execute intent) and falls through to the composer, so the
        # nudge must reach that path too — not just the tool-loop branch.
        composer_fold_addendum = (
            f"{_PRIVATE_SCOPE_COMPOSER_FOLD}\n\n{_private_roster_block(roster)}"
        )
        if instr_block:
            composer_fold_addendum = f"{composer_fold_addendum}\n\n{instr_block}"
        if insession_check_block:
            composer_fold_addendum = (
                f"{composer_fold_addendum}\n\n{insession_check_block}"
            )

        # `post_turn` — the execute-task progress writer. RELOCATED in shape from
        # `_build_private_scope`: the private surface's turn writer, bound to the
        # ask's own conversation. `None` when the ask carries no conversation
        # (nothing to post into), which degrades `execute_task` to no progress
        # posts rather than erroring.
        #
        # Realtime fan-out (best-effort, AD-P22): `post_individual_turn` is
        # CROSS-USER-capable (a delegate/execute-task reply can land in a
        # teammate's own individual chat, not necessarily `req.user_id`'s), so
        # the per-user topic's uid is resolved from the WRITTEN conversation
        # itself (`get_individual_conversation_owner`), never `req.user_id` —
        # mirrors `project_delegation._publish_brief_delivered`'s same-reasoning
        # owner resolution. A publish-prep failure never masks the already-
        # written turn.
        post_turn = None
        if req.conversation_id is not None:
            from app.db.conversations import (
                get_individual_conversation_owner,
                post_individual_turn,
            )
            from app.realtime import publish_broadcast

            def _post_turn(
                content: str,
                _conversation_id: int = req.conversation_id,
                _project_id: int = project_id,
            ) -> dict:
                turn = post_individual_turn(_conversation_id, "assistant", content)
                # Defense-in-depth: never publish a blank-content row (the
                # row above is still written either way) — mirrors the
                # client's own `parseRealtimeTurnPayload` blank-content
                # guard, so a blank write here can never render a phantom
                # bubble on the receiving thread.
                if not (turn.get("content") or "").strip():
                    return turn
                try:
                    owner_uid = get_individual_conversation_owner(_conversation_id)
                    if owner_uid is not None:
                        publish_broadcast(
                            f"project:{_project_id}:user:{owner_uid}",
                            "turn.created",
                            {
                                k: turn[k]
                                for k in ("id", "role", "content", "created_at")
                                if k in turn
                            },
                        )
                except Exception:  # noqa: BLE001 — best-effort, AD-P22
                    logger.warning(
                        "realtime_publish_prep_failed topic=project:%s:user:? "
                        "event=turn.created conversation_id=%s",
                        _project_id, _conversation_id, exc_info=True,
                    )
                return turn

            post_turn = _post_turn

        return SurfaceScope(
            surface=surface,
            project_id=project_id,
            context_payload=block,
            system_addendum=system_addendum,
            composer_fold_addendum=composer_fold_addendum,
            # The project tools, stable order: delegate + execute + complete +
            # the 4 shared read tools. Non-empty `extra_tools` is the on-switch
            # the sixth branch gates on (along with its intent gate).
            extra_tools=(
                project_delegation.DELEGATE_TASK_TOOL,
                project_task_execution.EXECUTE_TASK_TOOL,
                project_delegation.COMPLETE_TASK_TOOL,
                *read_tools(),
            ),
            roster=tuple(roster),
            assigner_identity={
                "assigner_user_id": req.user_id,
                "source_conversation_id": req.conversation_id,
            },
            post_turn=post_turn,
        )
