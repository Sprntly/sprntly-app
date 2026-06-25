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

from app.llm import (
    DEFAULT_MODEL,
    LONG_REQUEST_TIMEOUT_S,
    call_json,
    call_md,
)
from app.llm_telemetry import MODEL_PRICING
from app.skills.loader import get_skill

logger = logging.getLogger(__name__)

# Skills whose output is large/slow enough that a non-streamed call risks the
# Anthropic read timeout (e.g. the 2-part PRD: a human PRD + an LLM impl-spec,
# ~4-6k output tokens). For these the gateway streams the response and runs on
# the long read timeout — the SDK's required pattern for big generations —
# accumulating the streamed text into the same return value. Behavior for all
# other skills/callers is unchanged.
_LONG_OUTPUT_SKILLS = frozenset({"prd-author", "implementation-spec"})


def _is_long_output(skill: Optional[str]) -> bool:
    return skill is not None and skill in _LONG_OUTPUT_SKILLS


def _build_method_prefix(skill: str, skill_module: Optional[str]) -> tuple[str, str]:
    """Resolve a bound skill into (method_text_block, version_suffix).

    The method block is the skill's SKILL.md (plus the named module, if any)
    under a delimited header so the model reads it as the METHOD layer. The
    version suffix (`+<id>@<hash>`) is appended to prompt_version so the
    decision log records the exact method version behind the call.

    The skill's `references/*` docs are appended to the block under
    `### REFERENCE: <name>` headers. SKILL.md instructs the model to *read*
    those files at runtime (e.g. "read references/signal-schema.json", "score
    against references/rubric.md", "compare to references/examples.md"); the app
    never made them available before, so the skill could not run its full
    documented workflow. Folding them into this method block — the cacheable
    prefix — makes the whole skill doc set in-prompt for ~one extra cache write,
    then a cache read on subsequent calls. `assets/*` (e.g. a render template)
    are deliberately NOT injected: the app renders from the structured payload,
    so the template is a downstream view, not a prompt input.
    """
    spec = get_skill(skill)
    header = f"## METHOD (skill: {spec.id} @{spec.content_hash})\n"
    block = header + spec.method
    if skill_module:
        try:
            module_text = spec.modules[skill_module]
        except KeyError as exc:
            raise KeyError(
                f"skill {skill!r} has no module {skill_module!r}; "
                f"available: {sorted(spec.modules)}"
            ) from exc
        block += f"\n\n### MODULE: {skill_module}\n{module_text}"
    # Reference docs SKILL.md tells the model to read at runtime. Sorted for a
    # deterministic prefix (cache-key stable). No-op for skills without a
    # references/ dir, so every other bound skill's prompt is byte-identical.
    references = getattr(spec, "references", {}) or {}
    for name in sorted(references):
        block += f"\n\n### REFERENCE: {name}\n{references[name]}"
    return block + "\n", f"+{spec.id}@{spec.content_hash}"


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
    skill: Optional[str] = None,
    skill_module: Optional[str] = None,
    long_output: bool = False,
    log: bool = True,
    background: bool = False,
) -> LLMResult:
    """One attributed, telemetered LLM call. See module docstring.

    When `skill` is set, the bound skill's method text (its SKILL.md, plus the
    named `skill_module` if given, plus the skill's `references/*` docs under
    "### REFERENCE:" headers) is PREPENDED to the cacheable prefix under a
    "## METHOD (skill: <id> @<hash>)" delimiter — the agent's own `system`
    prompt stays as the agent-specific layer AFTER the method. The method text
    (including references) rides the existing user_cacheable_prefix mechanism
    (see app.llm) so it is cache-friendly across calls. `prompt_version` is
    suffixed with `+<skill_id>@<hash>` so the decision log pins the exact
    method version.
    """
    chosen_model = model or DEFAULT_MODEL
    method_block = ""
    if skill is not None:
        method_block, version_suffix = _build_method_prefix(skill, skill_module)
        prompt_version = f"{prompt_version}{version_suffix}"
    # Long-output calls stream on the long read timeout so a large/slow
    # generation never trips the default per-request timeout. Triggered either by
    # a registered long-output skill (e.g. prd-author) OR an explicit
    # `long_output=True` from the caller — the latter for non-skill agents that
    # still produce big docs (technical design, risk analysis, traceability
    # matrix, QA test cases), which were tripping httpx.ReadTimeout on the
    # default 120s non-streamed path. Other callers keep the non-streamed path.
    use_long_output = long_output or _is_long_output(skill)
    stream = use_long_output
    timeout = LONG_REQUEST_TIMEOUT_S if use_long_output else None
    meta: dict = {}
    t0 = time.monotonic()
    if json_schema is not None:
        # call_json supports a cacheable user prefix — keep the method there so
        # it's cache-friendly across calls; the agent system prompt stays after.
        if method_block:
            user_cacheable_prefix = (
                method_block if user_cacheable_prefix is None
                else f"{method_block}\n{user_cacheable_prefix}"
            )
        output: Any = call_json(
            system=system, user=input, model=chosen_model, max_tokens=max_tokens,
            schema=json_schema, user_cacheable_prefix=user_cacheable_prefix,
            meta_out=meta, stream=stream, timeout=timeout, background=background,
        )
    else:
        # call_md has no cacheable-prefix path; fold the method into the system
        # prompt (method first, agent layer after) so the binding still applies.
        md_system = f"{method_block}\n{system}" if method_block else system
        output = call_md(
            system=md_system, user=input, model=chosen_model, max_tokens=max_tokens,
            meta_out=meta, stream=stream, timeout=timeout, background=background,
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
