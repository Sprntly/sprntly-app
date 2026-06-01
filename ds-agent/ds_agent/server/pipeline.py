"""Multi-agent pipeline validation.

Chains DS Agent → Design Agent → Engineer Agent for a given dataset,
capturing each stage's output.  This is a debug/validation endpoint,
not user-facing — it proves the pipeline works end-to-end with the
stub agents.
"""
from __future__ import annotations

import logging
import time
from typing import Any

from . import agents as _agents
from . import backend_client as _backend
from .chat import ChatRunner
from .state import SessionState

logger = logging.getLogger(__name__)


def _run_stage(
    agent_id: str,
    prompt: str,
    corpus_context: str | None = None,
    dataset_slug: str | None = None,
) -> dict[str, Any]:
    """Run a single agent turn and return status + output summary."""
    agent = _agents.get(agent_id)
    if agent is None:
        return {"status": "skipped", "reason": f"agent {agent_id!r} not registered"}

    session = SessionState(
        sid=f"pipeline-{int(time.time())}",
        agent_id=agent_id,
        dataset_slug=dataset_slug,
        corpus_context=corpus_context,
    )

    try:
        runner = ChatRunner(agent)
        result = runner.turn(session, prompt)
        return {
            "status": "ok",
            "agent": agent.name,
            "output_length": len(result.assistant_text),
            "summary": result.assistant_text[:500],
            "code_executions": len(result.code_executions),
        }
    except Exception as exc:
        logger.warning("Pipeline stage %s failed: %s", agent_id, exc, exc_info=True)
        return {"status": "error", "agent": agent.name, "error": str(exc)}


def run_pipeline(dataset_slug: str) -> dict[str, Any]:
    """Run the DS → Design → Engineer pipeline for a dataset.

    Returns a dict with each stage's output for inspection.
    """
    # Fetch corpus from backend
    corpus_context: str | None = None
    try:
        corpus_data = _backend.fetch_corpus(dataset_slug)
        corpus_context = corpus_data.get("joined", "")
    except _backend.BackendError as exc:
        return {
            "dataset": dataset_slug,
            "error": f"Failed to fetch corpus: {exc}",
            "stages": {},
        }

    stages: dict[str, Any] = {}

    # Stage 1: DS Agent — run a quick analysis prompt
    ds_prompt = (
        "Based on the company knowledge base provided, identify the top 3 "
        "data-driven insights that should inform this week's product brief. "
        "For each insight, note the data source and confidence level."
    )
    stages["ds"] = _run_stage(
        "ds", ds_prompt, corpus_context=corpus_context, dataset_slug=dataset_slug
    )

    # Stage 2: Design Agent — feed DS output
    ds_output = stages["ds"].get("summary", "No DS output available.")
    design_prompt = (
        f"The data science agent identified these insights:\n\n{ds_output}\n\n"
        "Suggest UI patterns and design directions for the top insight."
    )
    stages["design"] = _run_stage(
        "design", design_prompt, corpus_context=corpus_context, dataset_slug=dataset_slug
    )

    # Stage 3: Engineer Agent — feed Design output
    design_output = stages["design"].get("summary", "No design output available.")
    engineer_prompt = (
        f"Based on this design direction:\n\n{design_output}\n\n"
        "Break this down into implementation tasks and flag technical risks."
    )
    stages["engineer"] = _run_stage(
        "engineer", engineer_prompt, corpus_context=corpus_context, dataset_slug=dataset_slug
    )

    return {
        "dataset": dataset_slug,
        "stages": stages,
    }
