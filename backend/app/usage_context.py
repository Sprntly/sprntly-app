"""Ambient attribution for LLM usage metering — which FEATURE is spending.

The metering proxy (`app.llm_metering`) records a row for every model call, but
the Anthropic client has no idea whether it is generating a PRD, iterating a
prototype, or answering a chat message. This module carries that label through
the call stack the same way `app.llm_keys` carries the acting company id: a
`ContextVar` set by a `with usage_scope(...)` block at the feature's entry point
and read at the metering chokepoint.

    with usage_scope(feature=Feature.PRD, operation="generate", user_id=uid):
        ...                       # every LLM call inside is tagged 'prd/generate'

Two deliberate properties:

  * **Fail-soft.** An un-scoped call is recorded as `feature='unattributed'`
    rather than dropped. A forgotten scope loses the LABEL, never the SPEND —
    so the dashboard's total is always right even while coverage is incomplete,
    and an 'unattributed' slice is a visible prompt to go add the scope.
  * **Innermost wins, outer fields inherit.** Nested scopes are common (a route
    opens `feature=prd`, a helper deeper down narrows `operation=questions`).
    A nested scope inherits `feature`/`user_id` from its parent unless it
    overrides them, so callers only state what they actually know.

Thread/task propagation is inherited from `contextvars`: `asyncio.to_thread`,
`create_task`, and `BackgroundTasks` all snapshot the context at creation, so a
scope opened in a request handler survives into the worker thread that runs the
blocking Anthropic call. A scope opened INSIDE a worker thread does not
propagate back out, which is the desired direction.
"""
from __future__ import annotations

import contextlib
import contextvars
from dataclasses import dataclass, replace


class Feature:
    """The coarse surface a call belongs to — the dashboard's primary breakdown.

    String constants rather than an Enum so a row is a plain string end-to-end
    (DB column, API payload, chart label) with no serialisation step, and so an
    unrecognised value from an older deploy is still a usable label instead of a
    deserialisation error.
    """

    PRD = "prd"
    DESIGN_AGENT = "design_agent"       # prototype generation + iteration
    CHAT = "chat"
    ASK = "ask"
    IDEATION = "ideation"
    SYNTHESIS = "synthesis"
    WEEKLY_BRIEF = "weekly_brief"
    EVIDENCE = "evidence"
    RESEARCH = "research"               # competitor / market / business context
    DOCUMENTS = "documents"             # PRD companion docs (tech design, risk, ...)
    STORIES = "stories"                 # user stories + tickets
    ONBOARDING = "onboarding"
    KG_INGEST = "kg_ingest"
    EMBEDDINGS = "embeddings"
    QA = "qa"
    ONCALL = "oncall"
    DATA_SCIENCE = "data_science"
    UNATTRIBUTED = "unattributed"


# `app.graph.gateway.llm_call` already labels every call with an `agent` name —
# a taxonomy that predates this module and is finer-grained than the dashboard
# wants (four separate agents all produce PRD companion documents; three do
# research). Mapping it here means ~30 gateway call sites are attributed without
# being touched, and the two taxonomies cannot drift apart at the call sites.
_AGENT_FEATURE: dict[str, str] = {
    "prd": Feature.PRD,
    "evidence": Feature.EVIDENCE,
    "ask": Feature.ASK,
    "ideation": Feature.IDEATION,
    # Legacy alias for ideation, kept because ~1.9k historical rows carry it
    # (see the chat alias note in app/skill_router.py).
    "backlog": Feature.IDEATION,
    "synthesis": Feature.SYNTHESIS,
    # The KG document extractor (app/graph/extractor.py) passes its agent name
    # in as a parameter, so it never appears as a literal at a call site.
    "extractor": Feature.KG_INGEST,
    "brief_nudge": Feature.WEEKLY_BRIEF,
    "user_stories": Feature.STORIES,
    "oncall": Feature.ONCALL,
    "embeddings": Feature.EMBEDDINGS,
    "ds": Feature.DATA_SCIENCE,
    # QA family
    "qa": Feature.QA,
    "qa-router": Feature.QA,
    "qa-verify": Feature.QA,
    # PRD companion documents
    "qa_test_cases": Feature.DOCUMENTS,
    "risk_analysis": Feature.DOCUMENTS,
    "technical_design": Feature.DOCUMENTS,
    "traceability_matrix": Feature.DOCUMENTS,
    # Outward research
    "competitor_analysis": Feature.RESEARCH,
    "market_research": Feature.RESEARCH,
    "business_context": Feature.RESEARCH,
    # Onboarding
    "website_analysis": Feature.ONBOARDING,
    "onboarding_wizard_drafts": Feature.ONBOARDING,
    "multi_agent_orchestrator": Feature.SYNTHESIS,
}


def feature_for_agent(agent: str) -> str:
    """Map a gateway `agent` label onto a dashboard feature bucket.

    Unmapped agents fall back to the agent name itself rather than
    `unattributed` — a new agent then shows up under its own name (still a
    useful label) instead of being silently pooled with genuinely unlabelled
    calls. The `ingest:*` family is collapsed to one bucket because it is
    generated per source (`ingest:github`, `ingest:google_drive`, ...) and would
    otherwise sprawl across the chart legend.
    """
    if agent.startswith("ingest:"):
        return Feature.KG_INGEST
    return _AGENT_FEATURE.get(agent, agent)


@dataclass(frozen=True)
class UsageScope:
    feature: str
    operation: str | None = None
    user_id: str | None = None


_UNATTRIBUTED = UsageScope(feature=Feature.UNATTRIBUTED)

_current_scope: contextvars.ContextVar[UsageScope] = contextvars.ContextVar(
    "llm_usage_scope", default=_UNATTRIBUTED
)


@contextlib.contextmanager
def usage_scope(
    *,
    feature: str | None = None,
    operation: str | None = None,
    user_id: str | None = None,
):
    """Tag every LLM call in the enclosed block with a feature/operation label.

    Every field is optional and unset fields INHERIT from the enclosing scope,
    so an inner block can narrow just the operation:

        with usage_scope(feature=Feature.PRD, user_id=uid):   # route boundary
            with usage_scope(operation="clarify"):            # helper narrows
                ...                                            # → prd/clarify

    Always restores the previous scope on exit, including on exception.
    """
    parent = _current_scope.get()
    child = replace(
        parent,
        feature=feature if feature is not None else parent.feature,
        operation=operation if operation is not None else parent.operation,
        user_id=user_id if user_id is not None else parent.user_id,
    )
    token = _current_scope.set(child)
    try:
        yield child
    finally:
        _current_scope.reset(token)


def current_scope() -> UsageScope:
    """The scope in effect for this call stack; `unattributed` when unset."""
    return _current_scope.get()
