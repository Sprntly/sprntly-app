"""LLM gateway — the agent-facing entry point for every model call (contract S2).

Layers tenant context + telemetry on top of `app.llm`:
  - every call is attributed to (enterprise_id, agent, purpose, prompt_version)
  - usage/cost/latency are computed (via app.llm_telemetry pricing) and a
    telemetry row is appended to `agent_decision_log` (decision_type
    "llm_call") — the §4d audit spine. Semantic *decisions* (rank/flag/etc.)
    are logged separately by the agents themselves with reasoning attached.
  - retries/backoff/timeout come from app.llm._create_with_retries.

Usage:
    from app.graph.gateway import llm_call
    result = llm_call(
        enterprise_id=ctx.company_id, agent="synthesis", purpose="rank_themes",
        prompt_version="synth-rank-v1", system=SYS, input=user_text,
        json_schema=SCHEMA,
    )
    result.output  # dict (json_schema given) or str
"""
from __future__ import annotations

import logging
import time
from dataclasses import dataclass
from typing import Any, Optional

from app.llm import DEFAULT_MODEL, call_json, call_md
from app.llm_telemetry import MODEL_PRICING

logger = logging.getLogger(__name__)


@dataclass
class LLMResult:
    output: Any                # dict when json_schema given, else str
    model: str
    prompt_version: str
    input_tokens: int
    output_tokens: int
    cache_read_input_tokens: int
    cache_creation_input_tokens: int
    cost_usd: float
    latency_ms: int
    stop_reason: Optional[str]


def _est_cost(meta: dict) -> float:
    p = MODEL_PRICING.get(meta.get("model", ""))
    if not p:
        return 0.0
    return (
        meta.get("input_tokens", 0) * p["input"]
        + meta.get("output_tokens", 0) * p["output"]
        + meta.get("cache_read_input_tokens", 0) * p["cache_read"]
        + meta.get("cache_creation_input_tokens", 0) * p["cache_write_1h"]
    )


def llm_call(
    *,
    enterprise_id: str,
    agent: str,
    purpose: str,
    system: str,
    input: str,
    prompt_version: str,
    model: Optional[str] = None,
    json_schema: Optional[dict] = None,
    max_tokens: int = 16000,
    user_cacheable_prefix: Optional[str] = None,
    log: bool = True,
) -> LLMResult:
    """One attributed, telemetered LLM call. See module docstring."""
    chosen_model = model or DEFAULT_MODEL
    meta: dict = {}
    t0 = time.monotonic()
    if json_schema is not None:
        output: Any = call_json(
            system=system, user=input, model=chosen_model, max_tokens=max_tokens,
            schema=json_schema, user_cacheable_prefix=user_cacheable_prefix,
            meta_out=meta,
        )
    else:
        output = call_md(
            system=system, user=input, model=chosen_model, max_tokens=max_tokens,
            meta_out=meta,
        )
    latency_ms = int((time.monotonic() - t0) * 1000)

    result = LLMResult(
        output=output,
        model=meta.get("model", chosen_model),
        prompt_version=prompt_version,
        input_tokens=meta.get("input_tokens", 0),
        output_tokens=meta.get("output_tokens", 0),
        cache_read_input_tokens=meta.get("cache_read_input_tokens", 0),
        cache_creation_input_tokens=meta.get("cache_creation_input_tokens", 0),
        cost_usd=round(_est_cost(meta), 6),
        latency_ms=latency_ms,
        stop_reason=meta.get("stop_reason"),
    )

    if log:
        # Telemetry row (§4d). Never let an audit-write failure break the
        # primary flow — log and continue.
        try:
            from app.graph.decision_log import log_agent_decision

            log_agent_decision(
                enterprise_id=enterprise_id,
                agent=agent,
                decision_type="llm_call",
                factors={
                    "purpose": purpose,
                    "input_tokens": result.input_tokens,
                    "output_tokens": result.output_tokens,
                    "cache_read_input_tokens": result.cache_read_input_tokens,
                    "cost_usd": result.cost_usd,
                    "latency_ms": result.latency_ms,
                },
                model=result.model,
                prompt_version=prompt_version,
            )
        except Exception:  # noqa: BLE001
            logger.exception("agent_decision_log write failed (continuing)")

    return result
