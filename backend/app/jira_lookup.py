"""On-demand Jira lookup — chat → live Jira (REST) → grounded answer.

THIN SHIM. The Jira lookup is now the first adapter of the generalized
connector-lookup framework (app/connector_lookup/): the tools, the dispatcher
and the system prompt live in app/connector_lookup/jira.py, and the answer loop
(session open, bounded tool loop, deterministic degradation, decision log) lives
in app/connector_lookup/answer.py, shared with every other connector.

This module stays because its surface is a contract, not an implementation
detail:

- `answer(...)` is what qa_agent's tracker fast-path calls, and its payload
  carries `_pending_jira_change` — the confirm card the web UI renders and
  routes/jira_write.py applies. Unchanged, key for key.
- `_SYSTEM` / `_SEARCH_TOOL` / `_GET_ISSUE_TOOL` / `_EDITMETA_TOOL` /
  `_PROPOSE_TOOL` / `_make_dispatch` / `jira_fetch` / `run_tool_loop` / `_log`
  are the module's long-standing patch + assertion surface. They are re-exported
  (same objects), and `answer` still resolves `run_tool_loop` and `_log` from
  THIS module's globals, so the Jira path keeps its own decision-log row
  (`decision_type="jira_lookup"`) and its own test seams.

Intent detection (is_jira_lookup) lives in skill_router; qa_agent routes the
tracker fast-path through app/connector_lookup/tracker.py, which picks the
tenant's connected tracker and delegates here for Jira.
"""
from __future__ import annotations

import logging

from app.connector_lookup import answer as connector_answer
from app.connector_lookup import jira as jira_adapter
from app.connectors import jira_fetch  # re-exported: patch surface
from app.llm import run_tool_loop  # re-exported: patch surface

logger = logging.getLogger(__name__)

ANSWER_MODEL = connector_answer.ANSWER_MODEL
_MAX_ITERS = connector_answer.MAX_ITERS
_MAX_TOKENS = connector_answer.MAX_TOKENS
_SKILL_ACTION = "Jira lookup"
_SKILL_SOURCE = "jira-lookup"

# ── Re-exports (same objects the adapter uses) ───────────────────────────────
_SYSTEM = jira_adapter.SYSTEM
_SEARCH_TOOL = jira_adapter.SEARCH_TOOL
_GET_ISSUE_TOOL = jira_adapter.GET_ISSUE_TOOL
_EDITMETA_TOOL = jira_adapter.EDITMETA_TOOL
_PROPOSE_TOOL = jira_adapter.PROPOSE_TOOL
_make_dispatch = jira_adapter.make_dispatch


def _plain_payload(answer: str, *, confidence: float = 0.0) -> dict:
    """Ask-shaped payload for the non-LLM branches (Jira not connected), tagged
    so the UI attributes it to the Jira-lookup path."""
    return connector_answer.plain_payload(
        answer, skill_source=_SKILL_SOURCE, skill_action=_SKILL_ACTION,
        confidence=confidence,
    )


def _render_history(history: list[dict] | None) -> str:
    """Recent turns as plain text. Wide enough (10 turns ≈ 5 exchanges) that the
    issue key the follow-up refers to is still in view — the model resolves
    "it"/"that one" against this block, so trimming it too hard is what turns a
    follow-up back into a blind re-search."""
    return connector_answer._render_history(history)


def answer(*, enterprise_id: str, question: str, history: list[dict] | None = None) -> dict:
    """Run the on-demand Jira lookup and return an Ask-shaped payload.

    Opens a live Jira session for the tenant and lets the model fetch the issues
    the question refers to via the tool loop. When Jira isn't connected, returns
    a helpful connect message instead. Never raises — the chat answer degrades
    gracefully on any failure.

    Everything Jira-specific (prompt, tools, connect copy, decision-log row) is
    passed explicitly, so the framework's generic behaviour never silently
    replaces the copy or telemetry this path has shipped with.
    """
    return connector_answer.answer(
        enterprise_id=enterprise_id,
        question=question,
        history=history,
        providers=[jira_adapter.PROVIDER],
        skill_source=_SKILL_SOURCE,
        skill_action=_SKILL_ACTION,
        not_connected_text=jira_adapter.NOT_CONNECTED,
        system_text=_SYSTEM,
        # Resolved from THIS module's globals at call time — the seams tests and
        # future callers already patch.
        run_loop=run_tool_loop,
        log=_log,
    )


def _log(enterprise_id: str, meta: dict) -> None:
    """Best-effort decision-log row (the tool-loop path bypasses the gateway's
    own logging, like _answer_with_script in qa_agent)."""
    try:
        from app.graph.decision_log import log_agent_decision

        log_agent_decision(
            enterprise_id=enterprise_id,
            agent="qa",
            decision_type="jira_lookup",
            factors={k: meta.get(k) for k in ("input_tokens", "output_tokens") if k in meta},
            model=meta.get("model"),
            prompt_version="qa-jira-lookup-v1",
        )
    except Exception:  # noqa: BLE001
        logger.exception("jira-lookup decision-log write failed")
