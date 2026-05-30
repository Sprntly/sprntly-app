"""Design Agent tool-use loop runner.

Per AD1: direct Anthropic Messages API, no SDK orchestration.
Per AD2: claude-sonnet-4-6 + cache_control ephemeral ttl 1h at end of stable
prefix (system + tool defs); never on per-call user content.
Per AD21: one Claude call per iteration; no manager/editor/verifier sub-agents.

The loop is `while stop_reason == "tool_use"`. Stop reasons handled:
  - "tool_use": dispatch tools, append tool_results, continue
  - "end_turn": loop exit, surface final assistant content
  - "max_tokens": double max_tokens once + retry the same turn; second hit = exit
  - "refusal": exit with status='refused'
Loop-pathology detection (per agent-build-research.md §4.3):
  - same (tool_name, input_hash) 3x in sliding window of 5 -> warn via tool_result
  - tool returns is_error: true 3x in a row -> wrap-up nudge
Iteration cap: max_iters (40; raised from 24 after the convergence fix —
real non-trivial PRDs were running to the old cap without converging). The
loop-pathology circuit-breakers above plus the graduated wrap-up nudges
(_wrap_up_nudge, fired at ~half / ~quarter / last turn) are the real
convergence drivers; the cap is a hard safety rail. On a max_iters exit the
last assistant turn is salvaged as final_content so a near-complete build is
not discarded.
Per-run cost accounting: aggregate usage.{cache_creation,cache_read,input,
output}_input_tokens per turn; emit one structured cost-summary log line
on completion via the shared app.llm_telemetry primitive.

PATTERN NOTE: First structured LLM cost log in the codebase. Format here
becomes the template for retrofitting PRD/Evidence/Ask runners later.
Scenario label is a pass-through string from the route (P1-07); the runner
does NOT re-derive (single inference site, lives in db/prototypes.py).
"""
from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import re
import time
from dataclasses import dataclass
from typing import Any

from app.db.prototype_comments import mark_comments_orphaned
from app.db.prototype_pending_iterations import (
    dequeue_next,
    mark_iteration_done,
    mark_iteration_failed,
)
from app.design_agent.autofixer import format_errors_for_agent
from app.design_agent.autofixer import run as autofixer_run
from app.design_agent.client import get_design_agent_client
from app.design_agent.tools import (
    ToolContext,
    dispatch,
    tool_definitions_for_api,
)
from app.llm_telemetry import RunUsage, log_llm_run

logger = logging.getLogger(__name__)

MODEL = "claude-sonnet-4-6"  # AD2; NEVER claude-sonnet-4-7
DEFAULT_MAX_ITERS = 40
DEFAULT_MAX_TOKENS = 4096
TOOL_RESULT_MAX_CHARS = 25000  # per agent-build-research.md §5.1

# ── AD12 orphan / re-attach: anchor-id extraction from the BUILT bundle ──────
#
# N2 — cross-language width coupling. This MUST equal `HASH_HEX_LENGTH` in
# `prototype-runtime/vite-plugin-anchor-id.ts` (P0-02), which emits
# `data-anchor-id` via `.slice(0, HASH_HEX_LENGTH)` at BUILD time. The agent
# NEVER emits `data-anchor-id` itself (AD4) — only the Vite plugin does, so the
# raw virtual_fs has no anchors and extraction MUST run over `vite_build`'s
# output. If the plugin's width ever changes, update this constant in lockstep:
# a stale width makes `_ANCHOR_ID_RE` silently match nothing, which would orphan
# EVERY open comment on the next build. A single named site (here) makes that a
# loud one-line change instead of a silent regex break.
_ANCHOR_HEX_WIDTH = 8

# Built from the width constant (N2) rather than a bare `{8}` literal. Matches
# both the plain attribute form (`data-anchor-id="abc12345"`) and the
# JS-string-escaped form (`data-anchor-id=\"abc12345\"`) Vite may emit when the
# attribute lands inside a bundled JS string literal.
_ANCHOR_ID_RE = re.compile(
    rf'data-anchor-id=(?:"|\\")([0-9a-f]{{{_ANCHOR_HEX_WIDTH}}})(?:"|\\")'
)


def extract_anchor_ids(dist_files: dict[str, str]) -> set[str]:
    """Return the distinct set of `data-anchor-id` values present across all
    built dist files. Pure; deterministic; no LLM, no network.

    The regex matches both the plain (`data-anchor-id="abc12345"`) and the
    escaped-in-JS-string (`data-anchor-id=\\"abc12345\\"`) forms, since Vite may
    emit the attribute inside a bundled JS string literal. Width is the
    `_ANCHOR_HEX_WIDTH` constant (coupled to P0-02's `HASH_HEX_LENGTH`).

    AD4-collision ([[ad4-collision-by-design]]): when the same anchor id appears
    on multiple elements (structurally-identical subtrees hash-collide), it is
    returned ONCE — set membership, not per-element. A comment on a collided id
    survives iff that id appears anywhere in the new bundle.
    """
    found: set[str] = set()
    for content in dist_files.values():
        found.update(_ANCHOR_ID_RE.findall(content))
    return found


def reconcile_comments_on_checkpoint(
    *,
    prototype_id: int,
    workspace_id: str,
    dist_files: dict[str, str],
) -> int:
    """AD12: after a new checkpoint's bundle is built, orphan every OPEN comment
    whose anchor_id is absent from the new bundle's surviving anchor IDs. Returns
    the count orphaned. Workspace-filtered (the prototype being regenerated is
    known — NOT a cross-workspace sweep).

    A comment whose anchor SURVIVES is left 'open' (re-attached implicitly — the
    anchor_id is unchanged, so P3-03's pin re-renders against the same id). AD4
    guarantees an unmodified element's anchor id is byte-identical across builds,
    so survival is exact-string membership, not fuzzy matching. There is no
    explicit un-orphan step: orphaning is one-way in P3 (a later build that
    re-introduces a deleted element does NOT auto-revive its comment).

    Called on EVERY new checkpoint build — the GENERATE staging path
    (`_stage_complete_run`) and the ITERATE staging path (`_stage_iterate_run`).
    It keys on `prototype_id` (not `checkpoint_id`), so it is build-path-agnostic.
    Callers wrap this best-effort: a reconcile failure must NOT fail the build.
    """
    surviving = extract_anchor_ids(dist_files)
    orphaned = mark_comments_orphaned(
        prototype_id=prototype_id,
        workspace_id=workspace_id,
        surviving_anchor_ids=surviving,
    )
    # Identifiers + counts only (Rule #24) — never anchor values or comment body.
    logger.info(
        "comments_reconciled prototype_id=%s surviving_anchors=%s orphaned=%s",
        prototype_id, len(surviving), orphaned,
    )
    return orphaned

# Pricing constants + RunUsage live in app.llm_telemetry — shared across
# every LLM call site in the repo. design_agent/runner.py only consumes
# the primitive; it does not own LLM telemetry shape.


@dataclass
class RunResult:
    status: str  # "complete" | "max_iters" | "refused" | "max_tokens" | "error"
    iters: int
    usage: RunUsage
    duration_ms: int
    final_content: list[dict[str, Any]]  # raw assistant content blocks
    error_class: str | None = None
    error_message: str | None = None


def _hash_tool_call(name: str, input: dict[str, Any]) -> str:
    payload = json.dumps({"n": name, "i": input}, sort_keys=True, default=str)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:16]


def _wrap_up_nudge(iters_remaining: int) -> str:
    if iters_remaining <= 2:
        return (
            f"You have {iters_remaining} tool-call turn(s) left. STOP now: finish "
            f"the current file, do NOT start new ones, then end your turn with a "
            f"1-2 sentence summary. A cut-off build is lost."
        )
    return (
        f"You have ~{iters_remaining} tool-call turns left. Start converging: make "
        f"the core flow navigable, batch any remaining writes, prefer finishing the "
        f"primary flow over adding screens. End your turn (no tool calls) as soon as "
        f"the core flow works."
    )


def _resolve_figma_access_token(figma_file_key: str | None) -> str | None:
    """Best-effort Figma access-token resolution for the `fetch_figma` tool.

    The tool executor never decrypts tokens itself (keeps tools.py importable
    without the connector stack); the runner injects the token onto the
    ToolContext before dispatch. Mirrors routes/connectors.py `_figma_access_token`
    but is NON-fatal: a prototype may have no Figma connection, or the connector
    may be unauthorised/unreadable. In any failure case we return None and let
    `fetch_figma` degrade to its own `is_error` path rather than aborting the
    whole generation. Returns None immediately when there's no file to fetch.
    """
    if not figma_file_key:
        return None
    try:
        # Lazy import: keeps runner.py importable in unit tests without the
        # FastAPI connector/db stack, and lets tests monkeypatch this resolver.
        from app.routes.connectors import _figma_access_token

        return _figma_access_token()
    except Exception as exc:  # not-connected (HTTPException 404), decrypt errors, etc.
        logger.info(
            "design_agent.figma_token_unresolved figma_file_key=%s error_class=%s",
            figma_file_key,
            type(exc).__name__,
        )
        return None


async def agent_loop(
    system_blocks: list[dict[str, Any]],
    user_message: dict[str, Any],
    ctx: ToolContext,
    max_iters: int = DEFAULT_MAX_ITERS,
    max_tokens: int = DEFAULT_MAX_TOKENS,
    scenario: str = "A",
    mode: str = "scaffold",
) -> RunResult:
    """Run the agent's tool-use loop until end_turn / max_iters.

    `system_blocks` is the system prompt as a list of `{"type": "text", "text": ...}`
    blocks. The LAST block must carry `cache_control: {type: "ephemeral", ttl: "1h"}`
    per AD2. `user_message` is the initial user-turn payload (also a list of
    content blocks; PRD + Figma context). Callers (P1-05/P1-07) assemble these.

    `scenario` and `mode` are pass-through labels surfaced in the cost-summary
    log by `generate_prototype`; the loop itself is scenario-agnostic per the
    single-inference-site decision (routing lives in the route layer).
    """
    client = get_design_agent_client()
    tools_payload = tool_definitions_for_api()
    messages: list[dict[str, Any]] = [user_message]

    usage = RunUsage()
    tool_call_window: list[str] = []
    consec_errors = 0
    start = time.perf_counter()
    iters = 0
    max_tokens_retried = False
    # Last assistant turn's content, salvaged on a max_iters exit so a near-
    # complete build (the agent ran out of turns mid-flow) is not discarded.
    last_assistant_content: list[dict[str, Any]] = []

    try:
        while iters < max_iters:
            iters += 1

            # Graduated wrap-up pressure (per agent-build-research.md §4.2) with
            # the REAL remaining count — was a single hardcoded "2 remaining"
            # nudge at N-1, too late to change a build's trajectory. Gentle
            # heads-up at ~half budget, firmer at ~quarter, hard stop in the last
            # turn. The trailing message here is always a user turn (the prior
            # iteration's tool_results, or the initial user message on iter 1), so
            # we append the nudge as a text block to that turn rather than a
            # second consecutive user message — the Messages API treats turns as
            # alternating, and a standalone consecutive user turn is unsafe.
            remaining = max_iters - iters
            if remaining in {max_iters // 2, max(2, max_iters // 4), 1}:
                _append_text_block(messages[-1], _wrap_up_nudge(remaining))

            resp = await asyncio.to_thread(
                client.messages.create,
                model=MODEL,
                max_tokens=max_tokens,
                system=system_blocks,
                tools=tools_payload,
                messages=messages,
            )
            usage.add(resp.usage)

            stop = resp.stop_reason
            content = [b.model_dump() for b in resp.content]
            messages.append({"role": "assistant", "content": content})
            last_assistant_content = content

            if stop == "end_turn":
                return _finish(usage, "complete", iters, start, content)

            if stop == "max_tokens":
                if max_tokens_retried:
                    return _finish(usage, "max_tokens", iters, start, content)
                max_tokens *= 2
                max_tokens_retried = True
                # The truncated assistant turn was appended above. When the cap
                # is hit MID-tool_use (the `write` content arg never finishes
                # serialising, leaving a tool_use block with partial/missing
                # input) re-sending it 400s the Messages API: "tool_use ids were
                # found without tool_result blocks immediately after" — the
                # dangling tool_use has no answering tool_result, and the loop's
                # retry never produces one. (A pure-text truncation would instead
                # 400 as two consecutive assistant turns.) Discard the truncated
                # turn and retry the SAME turn with the doubled budget, exactly as
                # this function's docstring intends ("retry the same turn"). The
                # usage from the truncated call is already counted above. (P2-03)
                messages.pop()
                continue

            if stop == "refusal":
                return _finish(usage, "refused", iters, start, content)

            if stop != "tool_use":
                return _finish(usage, "complete", iters, start, content)

            # Collect tool_use blocks; dispatch concurrently per parallel-tool-use rule.
            tool_uses = [b for b in content if b.get("type") == "tool_use"]
            results = await asyncio.gather(*[
                dispatch(tu["name"], tu.get("input") or {}, ctx) for tu in tool_uses
            ])

            # Static AST autofixer (P1-10): after every successful write/
            # line_replace on a .tsx/.ts file, validate the emitted content.
            # On failure, mutate the result to is_error so the agent receives
            # the analysis feedback as a normal tool_result and self-corrects
            # (per agent-build-research.md §2.4 + §4.1). Runs BEFORE the next
            # user message is built. Not an LLM call — does not touch `usage`.
            for i, (tu, result) in enumerate(zip(tool_uses, results)):
                if tu["name"] not in {"write", "line_replace"} or result.get("is_error"):
                    continue
                fpath = (tu.get("input") or {}).get("path", "")
                if not fpath.endswith((".tsx", ".ts")):
                    continue
                af = await autofixer_run(fpath, ctx.virtual_fs.get(fpath, ""), ctx.virtual_fs)
                if not af.get("ok"):
                    results[i] = {
                        "is_error": True,
                        "content": format_errors_for_agent(af),
                        "tool_name": tu["name"],
                    }

            # Pathology detection (per §4.3): same (name, input) 3x in window of 5.
            new_warnings: list[str] = []
            for tu in tool_uses:
                h = _hash_tool_call(tu["name"], tu.get("input") or {})
                tool_call_window.append(h)
                tool_call_window = tool_call_window[-5:]
                if tool_call_window.count(h) >= 3:
                    new_warnings.append(
                        f"You have called {tu['name']} with identical input "
                        f"3 times in the last 5 calls. Either change parameters "
                        f"or proceed without re-querying."
                    )

            # Consecutive error tracking.
            had_error = any(r.get("is_error") for r in results)
            consec_errors = (consec_errors + 1) if had_error else 0
            if consec_errors >= 3:
                new_warnings.append(
                    "Tool errors have repeated 3 times consecutively. Step back, "
                    "reassess the approach before retrying the same tool."
                )

            # Build the next user message: tool_result blocks FIRST per
            # agent-build-research.md §1.3, then any warnings as text blocks.
            next_content: list[dict[str, Any]] = []
            for tu, result in zip(tool_uses, results):
                block: dict[str, Any] = {
                    "type": "tool_result",
                    "tool_use_id": tu["id"],
                    "content": _serialise_tool_result(result),
                }
                if result.get("is_error"):
                    block["is_error"] = True
                next_content.append(block)
            for warn in new_warnings:
                next_content.append({"type": "text", "text": warn})

            messages.append({"role": "user", "content": next_content})

        # Exited because iters == max_iters. Salvage the last assistant turn as
        # final_content (was discarded as []) — a build that ran out of turns
        # mid-flow is usually near-complete and worth staging, not throwing away.
        return _finish(usage, "max_iters", iters, start, last_assistant_content)

    except Exception as exc:
        result = _finish(usage, "error", iters, start, [])
        result.error_class = type(exc).__name__
        result.error_message = str(exc)
        return result


def _append_text_block(message: dict[str, Any], text: str) -> None:
    """Append a text block to an existing message's content, keeping the turn
    single (alternation-safe). Promotes a bare-string content to a block list
    if a caller passed the older `content: str` shape."""
    block = {"type": "text", "text": text}
    content = message.get("content")
    if isinstance(content, list):
        content.append(block)
    elif isinstance(content, str):
        message["content"] = [{"type": "text", "text": content}, block]
    else:
        message["content"] = [block]


def _serialise_tool_result(result: dict[str, Any]) -> str:
    """Compress a tool result dict to a JSON string for the Anthropic API."""
    safe = {k: v for k, v in result.items() if k != "is_error"}
    return json.dumps(safe, default=str)[:TOOL_RESULT_MAX_CHARS]  # truncate per §5.1


def _finish(usage: RunUsage, status: str, iters: int, start: float, final_content: list) -> RunResult:
    duration_ms = int((time.perf_counter() - start) * 1000)
    return RunResult(
        status=status,
        iters=iters,
        usage=usage,
        duration_ms=duration_ms,
        final_content=final_content,
    )


async def generate_prototype(
    prototype_id: int,
    workspace_id: str,
    system_blocks: list[dict[str, Any]],
    user_message: dict[str, Any],
    figma_file_key: str | None,
    scenario: str = "A",
) -> tuple[RunResult, dict[str, str]]:
    """Public entrypoint: run agent_loop with a fresh ToolContext, emit the
    cost-summary log line, and return `(result, virtual_fs)` for P1-07 + P1-08
    to persist + stage.

    P1-08 extends the return type: the `virtual_fs` map (the raw TSX/TS files the
    agent built up via `write`/`line_replace`) is returned alongside the
    `RunResult` so the route hook can run `vite_build` over it and stage the
    bundle. The loop itself never persisted `virtual_fs`; it lives on the
    `ToolContext`, which is local to this function — hence the threading.

    The Figma access token is resolved here (runner-injected onto the
    ToolContext, before any tool dispatch) so `fetch_figma` can reach the
    Figma data API. Resolution is best-effort: a prototype without a Figma
    connection runs fine, with fetch_figma reporting its own is_error.
    """
    ctx = ToolContext(
        prototype_id=prototype_id,
        workspace_id=workspace_id,
        virtual_fs={},
        figma_file_key=figma_file_key,
        figma_access_token=_resolve_figma_access_token(figma_file_key),
    )
    result = await agent_loop(
        system_blocks=system_blocks,
        user_message=user_message,
        ctx=ctx,
        scenario=scenario,
        mode="scaffold",
    )
    # Cost-summary log line per TICKET_STANDARD §2 LLM-calling AC —
    # emitted via the shared llm_telemetry.log_llm_run primitive so the
    # log shape stays identical across every LLM call site in the repo
    # (and future PRD/Evidence/Ask/Brief runners can adopt with one call).
    log_llm_run(
        operation="design_agent.run.complete",
        identifier={
            "prototype_id": prototype_id,
            "scenario": scenario,
            "mode": "scaffold",
        },
        usage=result.usage,
        duration_ms=result.duration_ms,
        status=result.status,
        model=MODEL,
        error_class=result.error_class,
        iters=result.iters,
    )
    return result, ctx.virtual_fs


async def iterate_prototype(
    *,
    prototype_id: int,
    workspace_id: str,
    system_blocks: list[dict[str, Any]],
    user_message: dict[str, Any],
    current_source: dict[str, str],
    figma_file_key: str | None,
    scenario: str = "A",
    mode: str = "execute",
) -> tuple[RunResult, dict[str, str]]:
    """Iterate entrypoint (P3-05): mirror of `generate_prototype` for the EDIT
    path (AD8). The difference from scaffold is the seed: the ToolContext's
    `virtual_fs` is PRE-POPULATED with the current checkpoint's source files
    (loaded by the caller via `read_source_files_for_checkpoint`, P2-04) so a
    `view` of an existing file returns its content instead of a not-found error.
    The loop, cache discipline, and Figma-token injection are identical.

    `mode` is the tool-partition value threaded into `agent_loop` (and, once
    P3-07 lands, into `tools_for_mode`). The canonical iterate value is
    `'execute'`, NEVER `'iterate'` — P3-07 partitions on `scaffold`/`plan`/
    `execute`. The `mode="iterate"` string below is a DIFFERENT thing: the
    cost-log identifier (telemetry), distinguishing iterate runs from scaffold
    runs in the structured log, independent of the tool-partition mode.

    Returns `(result, virtual_fs)` — the post-run virtual_fs (seed + the agent's
    edits) for the caller's iterate-staging path (`_stage_iterate_run`).
    """
    ctx = ToolContext(
        prototype_id=prototype_id,
        workspace_id=workspace_id,
        # Copy so the agent's in-loop mutations never write back into the caller's
        # source dict; `view` returns real content because the seed is present.
        virtual_fs=dict(current_source),
        figma_file_key=figma_file_key,
        figma_access_token=_resolve_figma_access_token(figma_file_key),
    )
    result = await agent_loop(
        system_blocks=system_blocks,
        user_message=user_message,
        ctx=ctx,
        scenario=scenario,
        mode=mode,
    )
    # Cost-summary log line — same shared primitive as generate_prototype. The
    # operation + mode identifier mark this as an ITERATE run for telemetry; the
    # log carries identifiers + token counts only (Rule #24), never PRD/comment/
    # Figma content.
    log_llm_run(
        operation="design_agent.run.iterate",
        identifier={
            "prototype_id": prototype_id,
            "scenario": scenario,
            "mode": "iterate",
        },
        usage=result.usage,
        duration_ms=result.duration_ms,
        status=result.status,
        model=MODEL,
        error_class=result.error_class,
        iters=result.iters,
    )
    return result, ctx.virtual_fs


async def drain_iteration_queue(*, prototype_id: int, workspace_id: str) -> None:
    """Serially drain the pending-iteration queue for a prototype (AD11, P3-06).

    Pops the OLDEST pending row, marks it 'running' (`dequeue_next`), runs it
    through the P3-05 iterate body, marks it 'done' (or 'failed'), then chains the
    next pending row via `asyncio.create_task` until the queue is empty. At most
    ONE iteration runs at a time per prototype — each `_run_one_iteration` is
    awaited to completion BEFORE the next is dequeued, so there is never more than
    one 'running' row. A failed iteration marks its row 'failed' and the drain
    CONTINUES to the next pending row (one bad prompt does not stall the queue).

    Idempotent kick: if there is no pending row (e.g. the queue is already being
    drained, or it is empty), this no-ops — so the route can fire it on every
    enqueue without spawning a second concurrent drain.

    Deferred import (`_run_one_iteration`, `_inflight_tasks`): the routes module
    imports this function at load time, so a top-level `import app.routes...` here
    would be a cycle. The function-local import is the established break in this
    codebase (mirrors `_resolve_figma_access_token` and
    `db.prototypes.record_export_at_complete`). `_run_one_iteration` owns the real
    iterate body; `_inflight_tasks` is the route's strong-ref set (AC9).
    """
    row = dequeue_next(prototype_id=prototype_id, workspace_id=workspace_id)
    if not row:
        return
    from app.routes.design_agent import _inflight_tasks, _run_one_iteration
    try:
        await _run_one_iteration(row)
        mark_iteration_done(iteration_id=row["id"], workspace_id=workspace_id)
    except Exception as exc:  # noqa: BLE001 — one bad iteration must not stall the queue.
        mark_iteration_failed(
            iteration_id=row["id"],
            workspace_id=workspace_id,
            error=f"{type(exc).__name__}: {exc}",
        )
        logger.warning(
            "iteration_failed prototype_id=%s iteration_id=%s error_class=%s",
            prototype_id, row["id"], type(exc).__name__,
        )
    # Chain the next pending iteration. Strong-ref discipline (AC9): hold the task
    # in the route's _inflight_tasks set + discard on done, so it is never GC'd
    # mid-run. The chained drain no-ops if the queue is now empty (the `if not row`
    # guard above), so chaining terminates.
    nxt = asyncio.create_task(
        drain_iteration_queue(prototype_id=prototype_id, workspace_id=workspace_id)
    )
    _inflight_tasks.add(nxt)
    nxt.add_done_callback(_inflight_tasks.discard)
