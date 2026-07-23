"""LLM fallback for the chat "make me a PRD" command decision.

ChatScreen decides client-side whether a message is a PRD COMMAND (open the
PRD tab, POST /v1/prd/generate-from-task) or a question for the ask agent.
Tier 1 is a regex (web BriefChat.isPrdCommand — fast, free, covers the common
verbs and "PRD for <topic>" shapes). This module is tier 2: a message that
NAMES a PRD but doesn't match the regex ("let's get a PRD going for the
checkout revamp") is classified here by haiku before the client lets it fall
through to the ask agent — the same slash→regex→LLM ladder the skill router
already uses (app.qa_agent.route).

Fail-open contract: ANY failure (gateway down, bad JSON, truncation) returns
not-a-command with confidence 0.0, so the worst outcome is today's behavior
(a grounded text answer), never a broken chat send.
"""
from __future__ import annotations

import logging

from app.graph.gateway import llm_call

logger = logging.getLogger(__name__)

_AGENT = "prd"
# Routing/classification tier — same model the qa router uses (see
# app/qa_agent.py ROUTER_MODEL and the model-tiering policy).
_MODEL = "claude-haiku-4-5"

_SCHEMA = {
    "type": "object",
    "properties": {
        "is_prd_command": {"type": "boolean"},
        "task": {"type": ["string", "null"]},
        "confidence": {"type": "number"},
    },
    "required": ["is_prd_command", "confidence"],
    "additionalProperties": False,
}

_SYSTEM = """You classify ONE chat message from a product-management app.

Decide whether the user is ASKING US TO PRODUCE a PRD (product requirements
document) — a command that should open the PRD generator — or anything else
(a question about PRDs, a question about an existing PRD, a discussion,
feedback on a document, a tickets request).

is_prd_command = true ONLY when the message requests that a PRD/requirements
document be created, written, or put together for some topic. Polite or
indirect phrasings still count ("could we get a PRD going for checkout?",
"let's have a requirements doc for the billing revamp").

is_prd_command = false for:
- questions about PRDs in general ("what makes a good PRD?")
- questions or comments about an existing PRD ("does the PRD cover mobile?",
  "the PRD for dark mode is missing metrics")
- requests to create TICKETS/stories (even from a PRD)
- everything else.

task: when is_prd_command is true, the message text minus the request
boilerplate — the topic PLUS every requirement detail the user gave, kept
VERBATIM (do not summarize, do not invent). null when the command names no
topic ("can we get a PRD going?") or is_prd_command is false.

confidence: 0.0-1.0 for the is_prd_command verdict.

Return ONLY the JSON object."""


def classify_prd_command(enterprise_id: str, text: str) -> dict:
    """Haiku verdict: is `text` a command to create a PRD, and for what task.

    Returns {is_prd_command, task, confidence}; never raises (fail-open to
    not-a-command so the chat send always completes).
    """
    try:
        result = llm_call(
            enterprise_id=enterprise_id,
            agent=_AGENT,
            purpose="classify_chat_command",
            model=_MODEL,
            system=_SYSTEM,
            input=f"Message:\n{text}",
            prompt_version="prd-command-classify-v1",
            json_schema=_SCHEMA,
            # The task echoes the user's requirement details verbatim, so the
            # budget must cover the full message (≤8000 chars ≈ ~2200 tokens).
            max_tokens=2500,
        )
        out = result.output if isinstance(result.output, dict) else {}
        task = out.get("task")
        task = task.strip() if isinstance(task, str) and task.strip() else None
        return {
            "is_prd_command": bool(out.get("is_prd_command")),
            "task": task,
            "confidence": float(out.get("confidence") or 0.0),
        }
    except Exception:  # noqa: BLE001 — classification must never break the send
        logger.exception("PRD command classify failed; treating as not-a-command")
        return {"is_prd_command": False, "task": None, "confidence": 0.0}
