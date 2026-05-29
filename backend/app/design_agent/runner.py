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
Iteration cap: max_iters (8 for scaffold per BUILD-PHASES.md §Phase 1 #4).
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
import time
from dataclasses import dataclass
from typing import Any

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
DEFAULT_MAX_ITERS = 8
DEFAULT_MAX_TOKENS = 4096
TOOL_RESULT_MAX_CHARS = 25000  # per agent-build-research.md §5.1

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
    return (
        f"You have {iters_remaining} iterations remaining. Wrap up: finish any "
        f"in-progress files, do not start new ones."
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

    try:
        while iters < max_iters:
            iters += 1

            # Wrap-up nudge at N-1 per agent-build-research.md §4.2. The trailing
            # message here is always a user turn (the prior iteration's
            # tool_results, or the initial user message when max_iters == 1), so
            # we append the nudge as a text block to that turn rather than a
            # second consecutive user message — the Messages API treats turns as
            # alternating, and a standalone consecutive user turn is unsafe.
            if iters == max_iters - 1:
                _append_text_block(messages[-1], _wrap_up_nudge(2))

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

            if stop == "end_turn":
                return _finish(usage, "complete", iters, start, content)

            if stop == "max_tokens":
                if max_tokens_retried:
                    return _finish(usage, "max_tokens", iters, start, content)
                max_tokens *= 2
                max_tokens_retried = True
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

        # Exited because iters == max_iters.
        return _finish(usage, "max_iters", iters, start, [])

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
