"""Agent chat with live tool-use (C2 of the agent-tools-github slice).

  POST /v1/agent/chat-with-tools
    body: { message: <str>, installation_id: <int> }
    response: { response, iterations, tool_calls: [name...], truncated }

A parallel endpoint to the existing home-page chat (ask_runner). Runs
an Anthropic tool-use loop: the model can call any tool registered in
`app/agent_tools/registry.py` to fetch live data (currently 5 GitHub
tools — read file, list files, search code, get PR diff, list commits).

Why parallel and not a refactor of ask_runner: the existing one-shot
chat is the live home page surface. Running the tool-use loop there
multiplies token cost + latency for every chat. Until we've validated
the UX, this lives at /v1/agent/chat-with-tools and the frontend opts
in (via /lab/code-chat from C3).
"""
from __future__ import annotations

import json
import logging
from typing import Any

from anthropic import Anthropic
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from app import db
from app.agent_tools import registry
import app.agent_tools.github  # noqa: F401 — side-effect: registers GitHub tools
from app.auth import CompanyContext, require_company
from app.config import settings
from app.llm import DEFAULT_MODEL

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/v1/agent", tags=["agent"])

# Cap how many tool_use → tool_result round-trips we'll run before
# forcing the conversation to terminate. 8 is loose enough for the
# agent to do multi-step lookups (e.g. list files → search → read file →
# read another file) but tight enough to keep per-turn cost bounded.
MAX_ITERATIONS = 8

# Sonnet: this is an interactive tool-dispatch loop (5-10 turns), so MEDIUM
# routing reasoning on the cheaper/faster tier — opus is reserved for the rare
# deep single-shot calls (see app.llm.DEEP_MODEL), not per-turn loop work.
_MODEL = DEFAULT_MODEL
_MAX_TOKENS_PER_TURN = 4096

_SYSTEM_PROMPT = (
    "You are the Sprntly product agent. The user is a product manager. "
    "You have GitHub tools that can read code, files, commits, and PR diffs "
    "from repositories the user has connected to Sprntly. Use them when the "
    "user's question references the codebase. Cite specific files, lines, or "
    "commit SHAs when relevant. If a tool errors, acknowledge the error and "
    "either try a different approach or say what you couldn't find. Be concise."
)


# Lazy-init the Anthropic client so importing this module doesn't 500 in
# test environments without ANTHROPIC_API_KEY set.
_client: Anthropic | None = None


def get_llm_client() -> Anthropic:
    """Return the Anthropic client (lazy-initialised). Tests patch this."""
    global _client
    if _client is None:
        if not settings.anthropic_api_key:
            raise HTTPException(500, "ANTHROPIC_API_KEY not configured")
        _client = Anthropic(api_key=settings.anthropic_api_key, max_retries=0)
    return _client


class ChatWithToolsIn(BaseModel):
    message: str = Field(..., min_length=1)
    installation_id: int


@router.post("/chat-with-tools")
def chat_with_tools(
    body: ChatWithToolsIn,
    company: CompanyContext = Depends(require_company),
):
    """Run the tool-use loop until the model returns end_turn or we hit
    MAX_ITERATIONS.

    Tenancy: gated on require_company AND on installation ownership.
    The caller's installation_id must belong to their company; otherwise
    we 404 BEFORE consulting the LLM (no Anthropic tokens burned on
    rejected calls). Without this guard the dispatched GitHub tools
    would mint a GitHub App *installation token* for whatever id the
    body carried — Apurva's PR #230 closed the same lateral-access path
    on the connector routes but left this surface unguarded.
    """
    if not db.get_github_installation_for_company(
        body.installation_id, company.company_id
    ):
        raise HTTPException(404, "GitHub installation not found")
    client = get_llm_client()
    tools = registry.list_tools()

    messages: list[dict[str, Any]] = [
        {"role": "user", "content": body.message},
    ]
    tool_calls: list[str] = []
    iteration = 0
    truncated = False
    final_text = ""

    while iteration < MAX_ITERATIONS:
        iteration += 1
        resp = client.messages.create(
            model=_MODEL,
            max_tokens=_MAX_TOKENS_PER_TURN,
            system=_SYSTEM_PROMPT,
            tools=tools,
            messages=messages,
        )

        stop_reason = getattr(resp, "stop_reason", None)
        content_blocks = list(getattr(resp, "content", []) or [])

        if stop_reason == "tool_use":
            # Append the assistant's tool-use turn verbatim so the next
            # call sees the model's request, then run each tool and feed
            # results back as a user turn.
            messages.append({"role": "assistant", "content": content_blocks})
            tool_results: list[dict[str, Any]] = []
            for block in content_blocks:
                if getattr(block, "type", None) != "tool_use":
                    continue
                tool_calls.append(block.name)
                try:
                    result = registry.dispatch(
                        block.name,
                        dict(block.input or {}),
                        installation_id=body.installation_id,
                    )
                    payload = json.dumps(result, default=str)
                except Exception as exc:  # noqa: BLE001 — feed back to model
                    logger.warning(
                        "tool %s failed: %s", block.name, exc
                    )
                    payload = json.dumps({"error": str(exc)})
                tool_results.append(
                    {
                        "type": "tool_result",
                        "tool_use_id": block.id,
                        "content": payload,
                    }
                )
            messages.append({"role": "user", "content": tool_results})
            continue

        # stop_reason == "end_turn" (normal completion) OR something else
        # (max_tokens, stop_sequence, etc.) — extract whatever text we have.
        final_text = _extract_text(content_blocks)
        break
    else:
        # while-else: ran MAX_ITERATIONS without breaking via end_turn.
        truncated = True
        final_text = _extract_text(content_blocks) if content_blocks else ""

    # If we exit because the model kept calling tools past the cap, the
    # last response we have is a tool_use one — no text to surface.
    if truncated and not final_text:
        final_text = (
            "I reached my tool-use limit while answering. Try a more specific "
            "question or break it into smaller parts."
        )

    return {
        "response": final_text,
        "iterations": iteration,
        "tool_calls": tool_calls,
        "truncated": truncated,
    }


def _extract_text(blocks: list[Any]) -> str:
    parts = []
    for b in blocks:
        if getattr(b, "type", None) == "text":
            parts.append(getattr(b, "text", "") or "")
    return "".join(parts).strip()
