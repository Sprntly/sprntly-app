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
from app.skills.loader import SkillSpec, UnknownSkillError, get_skill

logger = logging.getLogger(__name__)

# Skills whose output is large/slow enough that a non-streamed call risks the
# Anthropic read timeout (e.g. the 2-part PRD: a human PRD + an LLM impl-spec,
# ~4-6k output tokens). For these the gateway streams the response and runs on
# the long read timeout — the SDK's required pattern for big generations —
# accumulating the streamed text into the same return value. Behavior for all
# other skills/callers is unchanged.
_LONG_OUTPUT_SKILLS = frozenset(
    {"prd-author", "implementation-spec", "evidence-brief", "ideation-prioritize"}
)


def _is_long_output(skill: Optional[str]) -> bool:
    return skill is not None and skill in _LONG_OUTPUT_SKILLS


def _build_method_prefix(
    skill: str, skill_module: Optional[str], spec: Optional["SkillSpec"] = None
) -> tuple[str, str]:
    """Resolve a bound skill into (method_text_block, version_suffix).

    The method block is the skill's SKILL.md (plus the named module, if any)
    under a delimited header so the model reads it as the METHOD layer. The
    version suffix (`+<id>@<hash>`) is appended to prompt_version so the
    decision log records the exact method version behind the call.

    `spec` lets a caller inject a spec that is NOT a vendored disk skill — a
    company's uploaded custom skill (PRD 1854), resolved from the DB by
    qa_agent via app.skills.resolver. When None (every built-in call site),
    the id loads from disk exactly as before. The ONE deliberate difference
    for an injected spec: its header carries a `company-uploaded` tag, so the
    untrusted method text is labeled where the model reads it (the system
    prompt's custom-skill addendum points at that tag). The version suffix
    is built identically either way.

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

    TOLERANT BY DESIGN when the id names no vendored directory. The vendored
    library is now a small keep-list (nine skills), while a dozen-odd pipelines
    still pass `skill=<id>` at their call site — those bindings are how the
    decision log attributes a call, and several of them name a method we no
    longer ship. Raising here would turn "this pipeline has no method doc" into
    a 500 for a pipeline that is otherwise perfectly able to run on its own
    prompt, so a missing directory degrades to running METHOD-LESS instead:
    empty block, and `+bare` recorded in `prompt_version` so the audit spine can
    tell a method-less run apart from a method-backed one.

    NOT tolerant of an INJECTED spec (a company upload) — that path never
    touches disk, so there is nothing to be missing.
    """
    injected = spec is not None
    if not injected:
        try:
            spec = get_skill(skill)
        except UnknownSkillError:
            # Not vendored -> run method-less. See the docstring.
            return "", "+bare"
    origin = ", company-uploaded" if injected else ""
    header = f"## METHOD (skill: {spec.id} @{spec.content_hash}{origin})\n"
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
    skill_spec: Optional["SkillSpec"] = None,
    long_output: bool = False,
    log: bool = True,
    background: bool = False,
    temperature: Optional[float] = None,
    on_delta=None,
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
        # `skill_spec` carries a DB-backed custom skill (PRD 1854); None means
        # a vendored built-in, loaded from disk by id as always.
        method_block, version_suffix = _build_method_prefix(
            skill, skill_module, spec=skill_spec
        )
        prompt_version = f"{prompt_version}{version_suffix}"
        # The bound skill's method is a large, byte-stable block — route it into
        # the cacheable prefix (BEFORE any caller-supplied prefix) so it is a
        # cache read on subsequent calls rather than reprocessed every time. Both
        # the json and md paths now share this: call_md gained a cacheable-prefix
        # parameter, so markdown skills (prd-author, implementation-spec,
        # evidence-brief) stop folding the method uncached into `system`.
        #
        # Guarded on `method_block` being non-empty: a method-less run (the
        # skill id names no vendored dir — see _build_method_prefix) must leave
        # the caller's prefix exactly as it was, and must NOT turn a `None`
        # prefix into an empty string, which app.llm reads as "there is a
        # cacheable prefix" and would emit as an empty cache-controlled block.
        if method_block:
            user_cacheable_prefix = (
                method_block if user_cacheable_prefix is None
                else f"{method_block}\n{user_cacheable_prefix}"
            )
    # Long-output calls stream on the long read timeout so a large/slow
    # generation never trips the default per-request timeout. Triggered either by
    # a registered long-output skill (e.g. prd-author) OR an explicit
    # `long_output=True` from the caller — the latter for non-skill agents that
    # still produce big docs (technical design, risk analysis, traceability
    # matrix, QA test cases), which were tripping httpx.ReadTimeout on the
    # default 120s non-streamed path. Other callers keep the non-streamed path.
    # A caller asking for per-delta streaming implies the streaming transport.
    use_long_output = long_output or _is_long_output(skill) or (on_delta is not None)
    stream = use_long_output
    timeout = LONG_REQUEST_TIMEOUT_S if use_long_output else None
    meta: dict = {}
    t0 = time.monotonic()
    # [timing] — the gateway is the one chokepoint every model call crosses, so
    # a start/end pair HERE labels every LLM leg of a request (planner, answer,
    # suggestions, …) by its purpose with zero per-callsite edits.
    from app.timing import logger as _timing_logger

    _timing_logger.info(
        "[timing] block=llm:%s event=start agent=%s model=%s",
        purpose, agent, chosen_model,
    )
    # Bind the tenant's own Claude key (when configured) for this call. This is
    # the single chokepoint every KG-agent / brief / PRD / evidence / ticket call
    # flows through, and `enterprise_id` is the company id — so binding here
    # routes the whole gateway to the customer's key. Set-and-used in this
    # synchronous stack (the call below reaches app.llm.get_client), so it
    # propagates even when the primitive runs the Anthropic call on a worker
    # thread. See app.llm_keys.
    from app.llm_keys import company_llm_key
    from app.usage_context import feature_for_agent, usage_scope

    # Usage metering reads the acting company from `company_llm_key` and the
    # feature label from `usage_scope`, both at the `messages.create` inside.
    # Deriving the label from the `agent`/`purpose` this function already
    # receives means every gateway caller is attributed without being touched.
    # An explicit inner scope set by a caller still wins (usage_scope inherits
    # only what the inner block leaves unset).
    with company_llm_key(enterprise_id), usage_scope(
        feature=feature_for_agent(agent), operation=purpose
    ):
        if json_schema is not None:
            # The method (if any) is already merged into user_cacheable_prefix
            # above, so it stays cache-friendly across calls; the agent system
            # prompt is the layer after it.
            #
            # `on_delta` on a structured call receives the raw PARTIAL-JSON
            # fragments of the tool input (the deltas a forced-tool stream
            # actually emits) — the caller wraps it in an extractor (e.g.
            # app.ask_stream.AnswerFieldExtractor) to turn them into text.
            output: Any = call_json(
                system=system, user=input, model=chosen_model, max_tokens=max_tokens,
                schema=json_schema, user_cacheable_prefix=user_cacheable_prefix,
                meta_out=meta, stream=stream, timeout=timeout, background=background,
                temperature=temperature, on_json_delta=on_delta,
            )
        else:
            # call_md now supports the same cacheable prefix, so the method
            # (merged above) rides the prefix — a cache read on repeat calls —
            # instead of being concatenated uncached into `system`. The agent
            # `system` prompt is cached too when substantial (see
            # _build_base_kwargs).
            output = call_md(
                system=system, user=input, model=chosen_model, max_tokens=max_tokens,
                user_cacheable_prefix=user_cacheable_prefix,
                meta_out=meta, stream=stream, timeout=timeout, background=background,
                temperature=temperature, on_delta=on_delta,
            )
    latency_ms = int((time.monotonic() - t0) * 1000)
    _timing_logger.info(
        "[timing] block=llm:%s event=end dur_ms=%d agent=%s model=%s in_tok=%s out_tok=%s cache_read=%s",
        purpose, latency_ms, agent, meta.get("model", chosen_model),
        meta.get("input_tokens", 0), meta.get("output_tokens", 0),
        meta.get("cache_read_input_tokens", 0),
    )

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
                    # The write side of the cache, without which the read count
                    # alone can't yield a hit rate from the audit spine —
                    # `LLMResult` has carried it all along, it just never landed
                    # in `factors`. Needed to measure whether the router's
                    # cacheable menu prefix is actually being hit, and to see a
                    # cache RACE: a fleet of concurrent calls that should share
                    # one cached prefix but each shows cache_read=0 +
                    # cache_creation>0 is prefilling before any write lands.
                    "cache_creation_input_tokens": result.cache_creation_input_tokens,
                    "cost_usd": result.cost_usd,
                    "latency_ms": result.latency_ms,
                },
                model=result.model,
                prompt_version=prompt_version,
            )
        except Exception:  # noqa: BLE001
            logger.exception("agent_decision_log write failed (continuing)")

    return result
