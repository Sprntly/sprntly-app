"""Shared LLM cost-summary log primitive (P1-04, first cost log in repo).

Design goal: ONE log shape across every LLM call site in the codebase so
observability dashboards have a single format to query and post-handoff
adoption by PRD/Evidence/Ask/Brief runners is mechanical (one import,
one call per runner).

The canonical log line shape:

    <operation> prototype_id=<id> scenario=<label> mode=<mode> iters=<N>
        cached_input_tokens=<N> input_tokens=<N> output_tokens=<N>
        duration_ms=<N> est_cost_usd=<float> status=<enum> error_class=<str|>

`operation` namespaces the call site (e.g. design_agent.run.complete,
prd.generate.complete, evidence.refresh.complete). `identifier` carries
the call-site's primary keys (prototype_id, prd_id, etc.) — variable
between call sites; the dict is rendered as ordered k=v pairs.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any

logger = logging.getLogger(__name__)


# Per-model pricing ($/token; derived from /MTok in agent-build-research.md §3.1).
# When a new model is approved (e.g. an Anthropic refresh), append a row here;
# call sites already passing `model=` Just Work after the addition.
MODEL_PRICING: dict[str, dict[str, float]] = {
    "claude-sonnet-4-6": {
        "input":          3.0 / 1_000_000,
        "cache_write_1h": 6.0 / 1_000_000,
        "cache_read":     0.3 / 1_000_000,
        "output":         15.0 / 1_000_000,
    },
    "claude-opus-4-7": {  # opus tier — DEEP_MODEL, HEAVY_MODEL, design escalation
        "input":          5.0 / 1_000_000,
        "cache_write_1h": 10.0 / 1_000_000,
        "cache_read":     0.5 / 1_000_000,
        "output":         25.0 / 1_000_000,
    },
    # OpenAI embeddings (KG signal/theme vectors — app.graph.embeddings). Anthropic
    # has no embeddings API, so this is the one non-Anthropic priced model. Billed
    # on prompt tokens only — no output, no prompt caching — so the other three
    # rates are 0.0 (kept so est_cost_usd's fixed key access never KeyErrors).
    # $0.02 /MTok (OpenAI pricing, text-embedding-3-small).
    "text-embedding-3-small": {
        "input":          0.02 / 1_000_000,
        "cache_write_1h": 0.0,
        "cache_read":     0.0,
        "output":         0.0,
    },
}


class UnknownModelError(KeyError):
    """Raised when est_cost_usd is called with a model not in MODEL_PRICING.

    Fails closed by design — silent zero-cost would mask spend during
    a model migration.
    """
    pass


@dataclass
class RunUsage:
    cache_creation_input_tokens: int = 0
    cache_read_input_tokens: int = 0
    input_tokens: int = 0
    output_tokens: int = 0

    def add(self, usage: Any) -> None:
        """Accumulate a single Anthropic response's usage object."""
        self.cache_creation_input_tokens += getattr(usage, "cache_creation_input_tokens", 0) or 0
        self.cache_read_input_tokens += getattr(usage, "cache_read_input_tokens", 0) or 0
        self.input_tokens += getattr(usage, "input_tokens", 0) or 0
        self.output_tokens += getattr(usage, "output_tokens", 0) or 0

    def est_cost_usd(self, model: str) -> float:
        """Compute spend in USD for a given model. Raises UnknownModelError
        if `model` isn't in MODEL_PRICING — fails closed."""
        if model not in MODEL_PRICING:
            raise UnknownModelError(
                f"No pricing for model '{model}'; add to MODEL_PRICING. "
                f"Known models: {sorted(MODEL_PRICING.keys())}"
            )
        p = MODEL_PRICING[model]
        return (
            self.cache_creation_input_tokens * p["cache_write_1h"]
            + self.cache_read_input_tokens * p["cache_read"]
            + self.input_tokens * p["input"]
            + self.output_tokens * p["output"]
        )


def project_next_iter_cost(usage: RunUsage, model: str, iters: int) -> float:
    """Projected cumulative cost (USD) IF one more average iteration runs.

    current spend + one more iteration's worth at the run's OWN observed
    average rate so far: current × (1 + 1/iters). Requires the caller's
    actual iteration count — a prior version approximated this as a flat
    "current × 2", which is only correct at iters == 1 and silently
    overshoots at every iteration count above that (the more iterations
    already run, the smaller one more SHOULD look relative to the total,
    not larger — see the incident this ticket fixes). Returns 0.0 when
    nothing has billed yet or iters <= 0 (nothing to average against).

    Pure and deterministic — no network, no SDK token-counter. Reuses
    ``MODEL_PRICING`` via ``RunUsage.est_cost_usd``; raises ``UnknownModelError``
    on an unpriced model (fails closed). The soft/hard cap is the caller's to
    pass — this helper is cap-agnostic so any future agent can supply its own.
    """
    current = usage.est_cost_usd(model)  # raises UnknownModelError — fails closed
    if current <= 0 or iters <= 0:
        return 0.0
    return current * (1 + 1 / iters)


def should_wrap_up(usage: RunUsage, model: str, soft_cap: float, iters: int) -> bool:
    """True iff the projected next-iteration cost would reach/exceed the soft cap.

    Pure decision primitive — the CALLER (e.g. ``agent_loop``) decides what to do
    when it returns True (inject a wrap-up nudge, degrade gracefully). Boundary is
    inclusive: a projection exactly equal to ``soft_cap`` returns True. Opt-in by
    import for any future Sprntly agent (PRD/Evidence runner) — one import, one
    call. Raises ``UnknownModelError`` on an unpriced model (via
    ``project_next_iter_cost``). ``iters`` is the caller's current 1-based
    iteration count — required, not defaulted, so no future caller can silently
    reproduce the flat-doubling bug by omission (see project_next_iter_cost).
    """
    return project_next_iter_cost(usage, model, iters) >= soft_cap


def should_abort(usage: RunUsage, model: str, hard_cap: float, iters: int) -> bool:
    """True iff the projected next-iteration spend would reach/exceed the HARD cap.

    The fail-closed BACKSTOP above AD15's soft cap: when the soft-cap nudge
    (``should_wrap_up``) failed to converge a pathological run, this signals the
    caller (``agent_loop``) to ABORT — terminate the run with a clean terminal
    status rather than keep burning budget. Reuses ``project_next_iter_cost``
    (same realized+projected model as ``should_wrap_up``, so the two thresholds
    are measured consistently); boundary inclusive (projection == hard_cap →
    True). Pure / deterministic; raises ``UnknownModelError`` on an unpriced
    model. The hard cap is the caller's to pass — cap-agnostic for cross-agent
    reuse. ``iters`` is required the same way as ``should_wrap_up`` — see
    project_next_iter_cost.
    """
    return project_next_iter_cost(usage, model, iters) >= hard_cap


def log_llm_run(
    *,
    operation: str,
    identifier: dict[str, Any],
    usage: RunUsage,
    duration_ms: int,
    status: str,
    model: str,
    error_class: str | None = None,
    **extra: Any,
) -> None:
    """Emit the canonical LLM cost-summary log line.

    Required fields:
        operation    — e.g. "design_agent.run.complete"
        identifier   — call-site primary keys, e.g. {"prototype_id": 42, "scenario": "A"}
        usage        — RunUsage
        duration_ms  — wall-clock for the operation
        status       — call-site enum, e.g. "complete" | "max_iters" | "refused" | "error"
        model        — model identifier, must be a key in MODEL_PRICING

    Optional:
        error_class  — exception class name when status indicates failure
        **extra      — any additional k=v pairs (rendered after the required ones)

    Discipline: identifiers ONLY (no PII, no prompt body, no API key, no tool
    result content). Cost is computed via MODEL_PRICING[model] — fails closed
    on unknown model (raises UnknownModelError; the caller is the bug, not the
    log line). Designed for grep-friendly observability + future log-aggregation.
    """
    cost = usage.est_cost_usd(model)  # raises UnknownModelError — don't swallow

    parts: list[str | None] = [operation]
    for key in sorted(identifier.keys()):
        parts.append(f"{key}={identifier[key]}")
    parts.append(f"iters={extra.pop('iters')}" if "iters" in extra else None)
    # The four token fields go in a fixed order for grep predictability.
    parts.extend([
        f"cached_input_tokens={usage.cache_read_input_tokens}",
        f"input_tokens={usage.input_tokens}",
        f"output_tokens={usage.output_tokens}",
        f"duration_ms={duration_ms}",
        f"est_cost_usd={cost:.4f}",
        f"model={model}",
        f"status={status}",
        f"error_class={error_class or ''}",
    ])
    for key in sorted(extra.keys()):
        parts.append(f"{key}={extra[key]}")
    logger.info(" ".join(p for p in parts if p))
