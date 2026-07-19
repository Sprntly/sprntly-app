"""Unified Q&A agent — the single front door behind every "ask" surface.

Pipeline (deterministic control flow; model only where judgement is needed):

  1. ROUTE   — decide skill-or-direct:
       slash fast-path  (`/prioritize …`)            → that skill, conf 1.0
       regex fast-path  (skill_router.detect_intent) → that skill, if routable
       else LLM router  (haiku over the routable manifest) → {skill_id|none}
       The LLM router also classifies scope: a question clearly outside
       product / PM / engineering / design short-circuits to the canned
       OUT_OF_SCOPE_MESSAGE — no answer model runs, so nothing is imagined.
  2. ANSWER  — skill → gateway.llm_call(skill=…) on sonnet, escalating heavy
               skills to opus; direct → compose_ask_answer (corpus + KG).
  3. (history) prior conversation turns are folded in for both the router and
     the answer so follow-ups ("now prioritise them") resolve.

Everything routes through the existing LLM gateway, so tenant isolation,
prompt-cache, cost/usage, and the decision-log audit spine keep working. The
generic path keeps using compose_ask_answer unchanged.

Models (decision 2026-06-13): router = haiku, answer = sonnet, heavy → opus.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from functools import lru_cache
from typing import Callable, Optional

from app.ask_runner import _ASK_RESPONSE_SCHEMA, _retrieve_kg_bundle, compose_ask_answer
from app.graph.gateway import llm_call
from app.llm import run_tool_loop
from app.prompts import (
    ASK_SYSTEM,
    ASK_SYSTEM_KG_ADDENDUM,
    ASK_SYSTEM_PRD_ADDENDUM,
    OUT_OF_SCOPE_MESSAGE,
)
from app.skill_router import (
    detect_intent,
    is_call_digest,
    is_data_analysis_request,
    is_voc_report_request,
)
from app.skills.catalog import COST_GATED, NON_ROUTABLE, routable_manifest
from app.skills.loader import get_skill, list_skills
from app.skills.scripts import SCRIPT_TOOLS

logger = logging.getLogger(__name__)


class AskCancelled(Exception):
    """Raised at a cooperative cancellation checkpoint when the caller's
    `is_cancelled()` reports the Ask has been stopped by the user. The worker
    (ask_job_runner) catches it and leaves the job row in its `cancelled` state
    WITHOUT marking it `error` — the answer is simply abandoned. Raising it
    between LLM steps is what lets a Stop that lands before the expensive answer
    call actually save that call, rather than only discarding the result."""


def _check_cancelled(is_cancelled: Optional[Callable[[], bool]]) -> None:
    """Abort the answer pipeline if the Ask was stopped. A no-op when no
    canceller is wired (e.g. direct/test callers) or it returns False."""
    if is_cancelled is not None and is_cancelled():
        raise AskCancelled()


ROUTER_MODEL = "claude-haiku-4-5"
ANSWER_MODEL = "claude-sonnet-4-6"
HEAVY_MODEL = "claude-opus-4-7"

# Skills heavy enough (deep analysis / long output) to answer on opus rather
# than sonnet. Tunable — keep small; most skills do fine on sonnet.
# NB: prd-author is intentionally NOT here — the deep reasoning lives in the KG
# build + weekly brief (both on DEEP_MODEL); the PRD composes off that already-
# analysed material and stays on sonnet, matching the product PRD pipeline
# (prd_runner.py, which never passed an opus model override).
HEAVY_SKILLS: frozenset[str] = frozenset(
    {"competitive-intelligence-review"}
)

# Optional fact-check verify pass over high-stakes answers (claims/numbers).
# OFF by default — flip via set_verify(True) / config. fact-check is otherwise
# non-routable; this is the internal verification use it was kept for.
VERIFY_ENABLED = False
HIGH_STAKES_SKILLS: frozenset[str] = frozenset(
    {"prd-author", "competitive-intelligence-review", "saas-metrics-diagnosis",
     "experiment-readout", "market-structure"}
)


def set_verify(enabled: bool) -> None:
    """Toggle the fact-check verify pass (config hook)."""
    global VERIFY_ENABLED
    VERIFY_ENABLED = enabled

# LLM router accepts a skill only at/above this confidence; below → direct.
_LLM_ROUTE_THRESHOLD = 0.6
# Regex fast-path threshold (matches the historical /v1/ask gate).
_REGEX_ROUTE_THRESHOLD = 0.75
# How many prior turns to feed the router / answer for follow-up context.
_HISTORY_TURNS = 6

_ROUTE_SCHEMA: dict = {
    "type": "object",
    "properties": {
        "skill_id": {
            "type": "string",
            "description": "Exact id of the single best-fit skill, or 'none' if the question is general and no skill clearly applies.",
        },
        "confidence": {"type": "number", "description": "0..1"},
        "reason": {"type": "string", "description": "One short clause."},
        "in_scope": {
            "type": "boolean",
            "description": (
                "false ONLY when the question is clearly outside product / PM / "
                "engineering / design work (see system prompt); when false, "
                "skill_id must be 'none'."
            ),
        },
    },
    "required": ["skill_id", "confidence", "reason", "in_scope"],
}

_ROUTER_SYSTEM = (
    "You are a router for a product-management assistant. Given the user's "
    "question (and recent conversation), pick the SINGLE best-fit PM skill from "
    "the menu, or 'none' if the question is general/conversational and no skill "
    "clearly applies. Prefer 'none' over a weak match. Return the skill's exact "
    "id.\n\n"
    "Also classify scope. in_scope=true when the question concerns the user's "
    "product or product work in any way: the product itself, problems, "
    "evidence, prioritization, tickets, PRDs, user feedback, prototypes, "
    "design, engineering, data about the business, or project management — or "
    "is a greeting / a question about this assistant. in_scope=false ONLY when "
    "the question is clearly outside those domains (general trivia, news, "
    "weather, sports, entertainment, personal advice, unrelated general "
    "knowledge). When in doubt, prefer in_scope=true."
)


@dataclass
class RouteDecision:
    skill_id: Optional[str]      # None → answer directly (no skill)
    confidence: float
    source: str                  # "slash" | "regex" | "llm" | "none"
    action: str = ""             # human label for the UI


@lru_cache(maxsize=1)
def _router_menu() -> str:
    """`- <id>: <description>` for every routable skill — the router's menu.
    Stable across calls, so it rides the gateway's cacheable prefix."""
    lines = [f"- {s['id']}: {s['description']}" for s in routable_manifest()]
    return "Available skills:\n" + "\n".join(lines)


def _render_history(history: Optional[list[dict]]) -> str:
    """Render the last few turns as plain text for prompt context."""
    if not history:
        return ""
    recent = history[-_HISTORY_TURNS:]
    rows = [f"{t.get('role', 'user').capitalize()}: {t.get('content', '')}" for t in recent]
    return "Conversation so far:\n" + "\n".join(rows) + "\n\n"


def _routable(skill_id: str) -> bool:
    return skill_id in set(list_skills()) and skill_id not in NON_ROUTABLE


def route(
    question: str,
    *,
    enterprise_id: str,
    history: Optional[list[dict]] = None,
) -> RouteDecision:
    """Decide whether a skill applies, and which. Slash + regex fast-paths skip
    the LLM router; otherwise classify with haiku over the routable menu."""
    q = question.strip()

    # 1) Explicit slash command: "/prioritize rank these"
    if q.startswith("/"):
        token = q[1:].split(None, 1)[0].lower()
        if _routable(token):
            return RouteDecision(token, 1.0, "slash", token)

    # 2) Regex fast-path (cheap, no LLM) — only for routable skills.
    intent = detect_intent(question)
    if intent and intent.confidence >= _REGEX_ROUTE_THRESHOLD and _routable(intent.skill_id):
        return RouteDecision(intent.skill_id, intent.confidence, "regex", intent.action)

    # 3) LLM router over the full routable menu.
    try:
        result = llm_call(
            enterprise_id=enterprise_id,
            agent="qa-router",
            purpose="route",
            model=ROUTER_MODEL,
            system=_ROUTER_SYSTEM,
            input=_render_history(history) + f"Question: {question}",
            prompt_version="qa-router-v2",
            json_schema=_ROUTE_SCHEMA,
            user_cacheable_prefix=_router_menu(),
            max_tokens=300,
        )
        out = result.output if isinstance(result.output, dict) else {}
        sid = (out.get("skill_id") or "none").strip()
        conf = float(out.get("confidence") or 0.0)
        if sid != "none" and _routable(sid) and conf >= _LLM_ROUTE_THRESHOLD:
            return RouteDecision(sid, conf, "llm", sid)
        # Scope gate: no skill matched AND the router says the question is
        # outside product/PM/engineering/design → canned refusal instead of a
        # direct answer the model would have to imagine. Strict `is False` so a
        # missing/odd field (old cached router rows, partial output) fails open
        # to the direct path, whose grounding rules still apply.
        if out.get("in_scope") is False:
            return RouteDecision(None, conf, "out_of_scope")
    except Exception:  # noqa: BLE001 — routing must never break the answer
        logger.exception("LLM router failed; answering directly")

    return RouteDecision(None, 0.0, "none")


# Confirm gate (decision 2026-06-13): cost-gated skills return this instead of
# running, so the UI can ask the PM how deep to go. v1 trips on every fresh
# route of a cost-gated skill (not when pinned via the follow-up); scope-aware
# auto-run of a tiny one-competitor teardown is a documented follow-up.
def _confirm_payload(skill_id: str, question: str) -> dict:
    return {
        "type": "needs_confirmation",
        "skill": skill_id,
        "scope": {"depth": "full"},
        "estimate": {"tier": "deep", "duration_s": 210},
        "options": [
            {"id": "quick", "label": "Quick teardown", "scope": {"depth": "quick"}},
            {"id": "full", "label": "Full review", "scope": {"depth": "full"}},
        ],
        # Keep the standard Ask shape so the route's _strip_citations + UI don't break.
        "answer": "",
        "key_points": [],
        "citations": [],
        "confidence": 0.0,
        "unanswered": "",
        "_skill": skill_id,
    }


# Ground truth over imagination: a question outside product/PM/engineering/
# design gets this fixed payload — no answer-model call, so there is nothing to
# hallucinate. Standard Ask shape (answer/key_points/citations/confidence/
# unanswered) so _strip_citations and the UI render it as a normal turn.
def _out_of_scope_payload() -> dict:
    return {
        "type": "out_of_scope",
        "answer": OUT_OF_SCOPE_MESSAGE,
        "key_points": [],
        "citations": [],
        "confidence": 1.0,
        "unanswered": "",
        "_skill": None,
        "_skill_source": "scope_gate",
    }


def _tag(payload: dict, decision: RouteDecision) -> dict:
    payload["_skill"] = decision.skill_id
    payload["_skill_action"] = decision.action
    payload["_skill_confidence"] = decision.confidence
    payload["_skill_source"] = decision.source
    return payload


def _kg_grounding(enterprise_id, question) -> tuple[str, bool]:
    """Live-context block from the KG for a generic skill answer.

    A skill call otherwise carries only its SKILL.md method + the raw question
    — no signal. A generative/analytical skill (prd-author, competitive-
    intelligence-review, …) handed an empty evidence context refuses per its
    own "every requirement traces to a real signal" rule, so the user sees a
    "no sources connected / not enough signal" answer even when their KG is
    full. Ground the skill on the SAME budget-capped KG bundle the direct Ask
    path uses. Best-effort: no tenant / empty KG / any read error → ('', False),
    and the skill runs corpus-less exactly as before (the call/VoC and
    deterministic SCRIPT_TOOLS skills keep their own dedicated grounding and
    never reach here)."""
    bundle = _retrieve_kg_bundle(enterprise_id, question)
    if not bundle:
        return "", False
    from app.graph.retrieval import render_context_section

    return f"{render_context_section(bundle)}\n\n---\n\n", True


def _answer_single_shot(
    decision: RouteDecision, enterprise_id, question, history, prd_context: str = ""
) -> dict:
    """Skill answer via one gateway call (SKILL.md injected by the gateway),
    grounded on the KG when the tenant's graph has relevant signal, and on the
    open PRD (`prd_context`) for PRD-tab chats."""
    model = HEAVY_MODEL if decision.skill_id in HEAVY_SKILLS else ANSWER_MODEL
    kg_block, kg_used = _kg_grounding(enterprise_id, question)
    prd_block = f"{prd_context}\n\n---\n\n" if prd_context else ""
    system = (
        ASK_SYSTEM
        + (ASK_SYSTEM_PRD_ADDENDUM if prd_context else "")
        + (ASK_SYSTEM_KG_ADDENDUM if kg_used else "")
        + f"\n\nThe user's question maps to the '{decision.skill_id}' skill. "
        "Follow that skill's method to produce a structured, actionable answer."
    )
    result = llm_call(
        enterprise_id=enterprise_id,
        agent="qa",
        purpose="skill_answer",
        model=model,
        system=system,
        input=_render_history(history) + prd_block + kg_block + f"Question: {question}",
        prompt_version="qa-skill-v1",
        json_schema=_ASK_RESPONSE_SCHEMA,
        skill=decision.skill_id,
        max_tokens=12000,
    )
    payload = (
        result.output
        if isinstance(result.output, dict)
        else {"answer": str(result.output), "key_points": [], "citations": [],
              "confidence": decision.confidence, "unanswered": ""}
    )
    return _tag(payload, decision)


def _answer_voc_report(decision: RouteDecision, enterprise_id, question, history) -> Optional[dict]:
    """VoC as the pinned HTML report when there's no live call source but the KG
    has signal. Grounds voice-of-customer-report on the SAME KG bundle the direct
    Ask path uses and renders it through the fixed template (frontend shows it in
    a sandboxed iframe). Returns None when the KG yields nothing, so the caller
    falls through to the generic single-shot answer (which explains what to
    connect). The live-calls path (call_digest) takes precedence and is handled
    upstream in `answer`."""
    from app import voc_report
    from app.graph.retrieval import render_context_section

    bundle = _retrieve_kg_bundle(enterprise_id, question)
    if not bundle:
        return None
    corpus_text = render_context_section(bundle)
    try:
        html = voc_report.build(
            enterprise_id=enterprise_id,
            question=(_render_history(history)) + question,
            corpus_text=corpus_text,
            source_line="=== KNOWLEDGE GRAPH — customer signal ===",
            model=HEAVY_MODEL if decision.skill_id in HEAVY_SKILLS else ANSWER_MODEL,
        )
    except Exception:  # noqa: BLE001 — fall back to the generic skill answer
        logger.exception("voc report from KG failed for %s", enterprise_id)
        return None
    payload = {"answer": html, "key_points": [], "citations": [],
               "confidence": decision.confidence, "unanswered": ""}
    return _tag(payload, decision)


def _answer_with_script(
    decision: RouteDecision, enterprise_id, question, history, prd_context: str = ""
) -> dict:
    """Skill answer via a tool-use loop so the skill's deterministic script runs
    ON OUR INFRA (app.skills.scripts) instead of the model estimating the math."""
    skill_id = decision.skill_id
    tool = SCRIPT_TOOLS[skill_id]
    spec = get_skill(skill_id)
    prd_block = f"{prd_context}\n\n---\n\n" if prd_context else ""
    system = (
        ASK_SYSTEM
        + (ASK_SYSTEM_PRD_ADDENDUM if prd_context else "")
        + f"\n\n## METHOD (skill: {skill_id})\n{spec.method}\n\n"
        f"You have a tool, `{tool.name}`, that runs the skill's deterministic "
        "script. Call it for the math instead of computing it yourself, then "
        "present the result clearly."
    )

    def dispatch(name: str, inp: dict) -> str:
        return tool.run(inp) if name == tool.name else f"(unknown tool {name})"

    meta: dict = {}
    text = run_tool_loop(
        system=system,
        user=_render_history(history) + prd_block + f"Question: {question}",
        tools=[tool.as_tool()],
        dispatch=dispatch,
        model=HEAVY_MODEL if skill_id in HEAVY_SKILLS else ANSWER_MODEL,
        max_tokens=8000,
        meta_out=meta,
    )
    _log_qa(enterprise_id, skill_id, "skill_answer_script", meta)
    payload = {
        "answer": text, "key_points": [], "citations": [],
        "confidence": decision.confidence, "unanswered": "",
    }
    return _tag(payload, decision)


def _maybe_verify(payload: dict, enterprise_id: str) -> dict:
    """When enabled, run a fact-check pass over a high-stakes answer and attach
    `_verification`. Best-effort and OFF by default, so the normal flow and
    every existing test are unaffected."""
    if not VERIFY_ENABLED:
        return payload
    skill = payload.get("_skill")
    answer_text = payload.get("answer") or ""
    if skill not in HIGH_STAKES_SKILLS or not answer_text:
        return payload
    try:
        result = llm_call(
            enterprise_id=enterprise_id,
            agent="qa-verify",
            purpose="fact_check",
            model=ANSWER_MODEL,
            system="Verify the claims in the answer are grounded; flag anything unsupported.",
            input=answer_text,
            prompt_version="qa-verify-v1",
            skill="fact-check",
            max_tokens=4000,
        )
        payload["_verification"] = result.output
    except Exception:  # noqa: BLE001 — verification must never break the answer
        logger.exception("qa verify pass failed")
    return payload


def _log_qa(enterprise_id: str, skill_id: str, purpose: str, meta: dict) -> None:
    """Best-effort decision-log row for the tool-loop path (the single-shot path
    is logged by the gateway itself)."""
    try:
        from app.graph.decision_log import log_agent_decision

        log_agent_decision(
            enterprise_id=enterprise_id,
            agent="qa",
            decision_type=purpose,
            factors={"skill": skill_id, **{k: meta.get(k) for k in
                     ("input_tokens", "output_tokens") if k in meta}},
            model=meta.get("model"),
            prompt_version=f"qa-skill-script-v1+{skill_id}",
        )
    except Exception:  # noqa: BLE001
        logger.exception("qa script decision-log write failed")


def answer(
    *,
    enterprise_id: str,
    question: str,
    dataset: str,
    history: Optional[list[dict]] = None,
    pinned_skill: Optional[str] = None,
    is_cancelled: Optional[Callable[[], bool]] = None,
    prd_id: Optional[int] = None,
) -> dict:
    """Answer a question via the best skill, or directly. `pinned_skill` skips
    routing (used when a confirm-gate follow-up has already chosen the skill).
    `prd_id` marks a PRD-tab ask: the open PRD (+ its insight/evidence/tickets/
    prototype) is assembled into a grounding block so "this PRD" questions
    actually see the document.

    `is_cancelled`, when supplied, is polled at cheap checkpoints between the
    routing and answer steps; if it returns True the pipeline raises
    `AskCancelled` and stops BEFORE the expensive answer LLM call, so a user
    Stop that lands early actually saves that cost. Callers that don't support
    cancellation (tests, the direct path) omit it and behave as before."""
    # Cancelled before we've spent anything → bail immediately.
    _check_cancelled(is_cancelled)
    # On-demand call digest: "summarize the customer calls from last week" needs a
    # LIVE fetch of every call in a window + a VoC pass over the complete corpus.
    # The generic router would misroute it (e.g. → interview-synthesis) and answer
    # from the lossy, token-capped KG, so intercept it first — unless the user has
    # pinned a specific skill via a follow-up.
    if not pinned_skill and is_call_digest(question):
        from app import call_digest

        return call_digest.answer(
            enterprise_id=enterprise_id, question=question, history=history
        )

    # Bare "voice of customer" / "VoC report" asks carry no call-noun, so
    # is_call_digest misses them — they'd fall to the corpus-less skill answer,
    # which reports "no sources connected" even when Fireflies has calls. When a
    # call source IS connected, run the same live digest so the natural phrasing
    # yields a real report; when it isn't, fall through to the skill route so it
    # can explain what to connect.
    if not pinned_skill and is_voc_report_request(question):
        from app import call_digest

        if call_digest.has_call_source(enterprise_id):
            return call_digest.answer(
                enterprise_id=enterprise_id, question=question, history=history
            )

    # "Analyze my data" is a COMMAND to run the deterministic DS engine over the
    # company's uploaded CSV/Excel exports — not a question for the corpus/KG.
    # Intercept before generic routing for the same reason as the call digest:
    # the keyword rules would send it to a synthesis skill, which answers from
    # the KG instead of computing over the actual data.
    if not pinned_skill and is_data_analysis_request(question):
        from app.ds import chat_analysis

        return chat_analysis.answer(
            enterprise_id=enterprise_id, question=question, history=history
        )

    if pinned_skill and _routable(pinned_skill):
        decision = RouteDecision(pinned_skill, 1.0, "pinned", pinned_skill)
    else:
        decision = route(question, enterprise_id=enterprise_id, history=history)

    # Routing (a cheap haiku call) is done; the answer/script call below is the
    # expensive one. This is the highest-value checkpoint: a Stop within the
    # first second or two lands here and skips the sonnet/opus generation.
    _check_cancelled(is_cancelled)

    # Out-of-domain question (router classified it, no skill matched) → the
    # canned refusal, deterministically. Never let the answer model improvise
    # on a topic we hold no ground truth for.
    if decision.source == "out_of_scope":
        return _out_of_scope_payload()

    # PRD-tab grounding, shared by the direct and skill paths. Best-effort:
    # build_prd_context returns '' on any failure, degrading to a plain ask.
    prd_context = ""
    if prd_id:
        from app.prd_context import build_prd_context

        prd_context = build_prd_context(enterprise_id, prd_id)

    if not decision.skill_id:
        # Direct path — corpus + KG, unchanged. Fold history into the question.
        q = _render_history(history) + question if history else question
        return compose_ask_answer(
            dataset, q, enterprise_id=enterprise_id, prd_context=prd_context
        )

    # Cost-gated skill freshly routed → ask before spending (CIR). A pinned
    # follow-up has already confirmed, so it runs.
    if decision.skill_id in COST_GATED and decision.source != "pinned":
        return _confirm_payload(decision.skill_id, question)

    # VoC routed with no live call source (call_digest is handled upstream): render
    # the pinned HTML report from KG signal when there is any; else fall through to
    # the generic answer (which explains what to connect).
    if decision.skill_id == "voice-of-customer-report":
        voc = _answer_voc_report(decision, enterprise_id, question, history)
        if voc is not None:
            return _maybe_verify(voc, enterprise_id)

    if decision.skill_id in SCRIPT_TOOLS:
        payload = _answer_with_script(
            decision, enterprise_id, question, history, prd_context=prd_context
        )
    else:
        payload = _answer_single_shot(
            decision, enterprise_id, question, history, prd_context=prd_context
        )
    return _maybe_verify(payload, enterprise_id)
