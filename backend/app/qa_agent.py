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
from app.prompt_history import clamp_turn_text
from app.prompts import (
    ASK_SYSTEM,
    ASK_SYSTEM_CUSTOM_SKILL_ADDENDUM,
    ASK_SYSTEM_KG_ADDENDUM,
    ASK_SYSTEM_PRD_ADDENDUM,
    ASK_SYSTEM_REPORT_ADDENDUM,
    OUT_OF_SCOPE_MESSAGE,
)
from app.report_context import build_report_context
from app.skill_router import (
    detect_intent,
    is_call_digest,
    is_connector_lookup,
    is_context_dependent_followup,
    is_data_analysis_request,
    is_jira_lookup,
    is_ticket_update,
    is_voc_report_request,
)
from app.skills.catalog import NON_ROUTABLE, routable_manifest
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
# build + Top Insights brief (both on DEEP_MODEL); the PRD composes off that already-
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
    "knowledge). When in doubt, prefer in_scope=true.\n\n"
    "The question is often a FOLLOW-UP whose subject lives in the conversation "
    "above, not in its own words (\"can you get me all the details about it?\", "
    "\"who owns that one?\"). Resolve pronouns and ellipsis against the earlier "
    "turns BEFORE judging scope and skill: if the thread is about the user's "
    "product work, a bare follow-up to it is in_scope=true — never out of scope "
    "merely for being short or topic-less on its own."
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
    """Render the last few turns as plain text for prompt context.

    Each turn is clamped (`app.prompt_history`) before it is folded in: an HTML
    report answer — VoC, public-feedback, DS analysis — is persisted verbatim as
    a conversation turn, and one carrying base64 charts is megabytes of `data:`
    URI. Replaying that into the router and answer calls would 400 every later
    ask in the thread, non-retryably. The turn count cap alone doesn't bound
    bytes, so the per-turn clamp is what makes this safe."""
    if not history:
        return ""
    recent = history[-_HISTORY_TURNS:]
    rows = [
        f"{t.get('role', 'user').capitalize()}: {clamp_turn_text(t.get('content', ''))}"
        for t in recent
    ]
    return "Conversation so far:\n" + "\n".join(rows) + "\n\n"


def _routable(skill_id: str, enterprise_id: Optional[str] = None) -> bool:
    """Can this id be invoked? Built-ins: vendored and not NON_ROUTABLE.
    With an enterprise_id, the company's CUSTOM skills (PRD 1854) also count —
    a fresh DB check, so a just-uploaded skill works and a just-deleted one
    stops immediately. A vendored id answers for the BUILT-IN and nothing else
    (custom skills never override one, and take their own trigger at upload),
    so a NON_ROUTABLE built-in id stays non-routable. Custom skills reach here
    only via the slash fast-path and pinned_skill; the regex rules and the LLM
    router menu stay built-in only (the memoized menu is process-global —
    per-company entries there would leak names across tenants)."""
    if skill_id in set(list_skills()):
        return skill_id not in NON_ROUTABLE
    if enterprise_id:
        from app.skills.resolver import has_custom_skill

        return has_custom_skill(enterprise_id, skill_id)
    return False


def route(
    question: str,
    *,
    enterprise_id: str,
    history: Optional[list[dict]] = None,
) -> RouteDecision:
    """Decide whether a skill applies, and which. Slash + regex fast-paths skip
    the LLM router; otherwise classify with haiku over the routable menu."""
    q = question.strip()

    # 1) Explicit slash command: "/prioritize rank these" — the one routing
    # stage that also matches the company's custom skills.
    if q.startswith("/"):
        token = q[1:].split(None, 1)[0].lower()
        if _routable(token, enterprise_id):
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
    decision: RouteDecision, enterprise_id, question, history, prd_context: str = "",
    on_delta=None, skill_spec=None, report_context: str = "",
) -> dict:
    """Skill answer via one gateway call (SKILL.md injected by the gateway),
    grounded on the KG when the tenant's graph has relevant signal — or, for a
    PRD-tab chat, on the open PRD alone (`prd_context` rides the cacheable
    prefix and the KG retrieval is skipped).

    `report_context` is ADDITIVE where the other two are exclusive: a report
    generated in this thread is one more thing the answer may need to read, not
    a replacement for the tenant's own signal — "how does recommendation 2
    compare to what support has seen since?" wants both."""
    model = HEAVY_MODEL if decision.skill_id in HEAVY_SKILLS else ANSWER_MODEL
    # Custom skill (PRD 1854): resolve the DB-backed spec and hand it over.
    # resolve_skill is BUILT-IN FIRST, so a vendored id keeps sending the
    # gateway to disk (spec stays None) no matter what the company uploaded;
    # only an id no built-in claims resolves to an upload. The dispatch may
    # pass the spec in to save the repeat lookup.
    if skill_spec is None and decision.skill_id and enterprise_id:
        from app.skills.resolver import custom_skill_spec, is_builtin

        if not is_builtin(decision.skill_id):
            skill_spec = custom_skill_spec(enterprise_id, decision.skill_id)
    if prd_context:
        # PRD-grounded ask: the PRD context block (~26K tokens) IS the
        # grounding — skip the KG retrieval (embeddings HTTP call + pgvector
        # queries, ~0.5-1s serial) entirely. The block rides the CACHEABLE
        # user prefix instead of plain input: it is byte-stable across turns
        # of the same PRD conversation, so turns 2+ cache-read it instead of
        # re-prefilling. The gateway PREPENDS the skill's METHOD block to this
        # prefix — also byte-stable per (skill, PRD content) — so the whole
        # prefix stays cache-friendly; history + the question stay uncached.
        kg_block, kg_used = "", False
    else:
        kg_block, kg_used = _kg_grounding(enterprise_id, question)
    system = (
        ASK_SYSTEM
        + (ASK_SYSTEM_PRD_ADDENDUM if prd_context else "")
        + (ASK_SYSTEM_REPORT_ADDENDUM if report_context else "")
        + (ASK_SYSTEM_KG_ADDENDUM if kg_used else "")
        # skill_spec is not None ⇔ the method text is a company upload, not a
        # vendored skill — tell the model it's user content, never authority.
        + (ASK_SYSTEM_CUSTOM_SKILL_ADDENDUM if skill_spec is not None else "")
        + f"\n\nThe user's question maps to the '{decision.skill_id}' skill. "
        "Follow that skill's method to produce a structured, actionable answer."
    )
    report_block = f"{report_context}\n\n---\n\n" if report_context else ""
    result = llm_call(
        enterprise_id=enterprise_id,
        agent="qa",
        purpose="skill_answer",
        model=model,
        system=system,
        input=(
            _render_history(history) + report_block + kg_block
            + f"Question: {question}"
        ),
        user_cacheable_prefix=prd_context or None,
        prompt_version="qa-skill-v1",
        json_schema=_ASK_RESPONSE_SCHEMA,
        skill=decision.skill_id,
        skill_spec=skill_spec,
        max_tokens=12000,
        # Structured-call streaming: on_delta receives partial-JSON fragments
        # of the tool input; the Ask worker's extractor turns them into text.
        on_delta=on_delta,
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
    decision: RouteDecision, enterprise_id, question, history, prd_context: str = "",
    report_context: str = "",
) -> dict:
    """Skill answer via a tool-use loop so the skill's deterministic script runs
    ON OUR INFRA (app.skills.scripts) instead of the model estimating the math."""
    skill_id = decision.skill_id
    tool = SCRIPT_TOOLS[skill_id]
    spec = get_skill(skill_id)
    prd_block = f"{prd_context}\n\n---\n\n" if prd_context else ""
    report_block = f"{report_context}\n\n---\n\n" if report_context else ""
    system = (
        ASK_SYSTEM
        + (ASK_SYSTEM_PRD_ADDENDUM if prd_context else "")
        + (ASK_SYSTEM_REPORT_ADDENDUM if report_context else "")
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
        user=(
            _render_history(history) + prd_block + report_block
            + f"Question: {question}"
        ),
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


def _ds_claude_enabled(enterprise_id: Optional[str]) -> bool:
    """Is the Claude data-analysis engine enabled for this company?

    `ds_claude_analysis` in companies.feature_flags. DEFAULT ON since 2026-07-30
    (Apurva): a missing key means ON, so every existing company gets the engine
    without a backfill and named opt-outs are explicit `false` rows — the same
    grandfather pattern chat_intent_envelope shipped under.

    THREE states, and the third is why this isn't a one-liner:
      * explicit `false`            → OFF (the deterministic engine)
      * key absent                  → ON  (grandfathered / newly onboarded)
      * flags UNKNOWN (read failed) → OFF, deliberately

    That last case diverges from #893, which fails OPEN. The difference is what
    the flag gates: chat_intent_envelope picks a routing strategy, while this one
    decides whether a tenant's raw uploaded CSVs leave the box for the Anthropic
    Files API. "I couldn't read your flags" must not resolve to "so I shipped
    your data" — and the fallback costs the user nothing, since the v5.8 engine
    answers instead. `feature_flags_for_company` can't express this (it collapses
    a failed read into `{}`), hence `read_feature_flags`, which returns None.
    """
    if not enterprise_id:
        return False
    try:
        from app.entitlements import ds_claude_analysis_enabled, read_feature_flags

        return ds_claude_analysis_enabled(read_feature_flags(enterprise_id))
    except Exception:  # noqa: BLE001 — flag read must never break the ask
        logger.exception("ds_claude_analysis flag read failed for %s", enterprise_id)
        return False


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
    conversation_id: Optional[int] = None,
    on_delta: Optional[Callable[[str], None]] = None,
) -> dict:
    """Answer a question via the best skill, or directly. `pinned_skill` skips
    routing (used when a confirm-gate follow-up has already chosen the skill).
    `prd_id` marks a PRD-tab ask: the open PRD (+ its insight/evidence/tickets/
    prototype) is assembled into a grounding block so "this PRD" questions
    actually see the document.

    `conversation_id` is the chat room this ask runs in. When a report was
    generated in it, the report is loaded into its own grounding block for the
    same reason as the PRD — a follow-up like "explain recommendation 1" needs
    the whole document, and the history fold only carries its first few
    thousand characters (see app/report_context.py).

    `on_delta`, when supplied, token-streams the answer as it generates: it
    receives the PARTIAL-JSON fragments of the structured answer call (the Ask
    worker wraps a token_stream sink in app.ask_stream.AnswerFieldExtractor,
    which decodes just the `answer` field's text). Only the two schema-shaped
    paths stream — the direct compose_ask_answer path and the single-shot skill
    answer. The special paths (call digest, VoC/public-feedback HTML reports,
    script tool-loops, DS analysis, Jira lookup) return non-streamable payloads
    and are delivered whole via the job poll, exactly as before.

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

    # "Analyze my data" is a COMMAND to run a DS engine over the company's
    # uploaded CSV/Excel exports — not a question for the corpus/KG.
    # Intercept before generic routing for the same reason as the call digest:
    # the keyword rules would send it to a synthesis skill, which answers from
    # the KG instead of computing over the actual data.
    if not pinned_skill and is_data_analysis_request(question):
        # Opt-in per company: the Claude code-execution engine actually reads the
        # question (the v5.8 battery never does) but sends the raw CSVs to the
        # Files API, so it stays behind a flag until that's signed off. Any
        # failure falls through to the deterministic engine — the permanent
        # fail-open floor — so this can never 500 the chat.
        if _ds_claude_enabled(enterprise_id):
            try:
                from app.ds import claude_analysis

                return claude_analysis.answer(
                    enterprise_id=enterprise_id,
                    question=question,
                    history=history,
                    is_cancelled=is_cancelled,
                )
            except AskCancelled:
                raise  # a user Stop must not spend a second (legacy) run
            except Exception:  # noqa: BLE001 — fall back, never fail
                logger.exception(
                    "DS Claude analysis failed for %s; falling back to the "
                    "deterministic engine", enterprise_id,
                )
        from app.ds import chat_analysis

        return chat_analysis.answer(
            enterprise_id=enterprise_id, question=question, history=history
        )

    # Rewrite a ticket FROM a PRD ("update the ticket details with the PRD").
    # Checked BEFORE the tracker lookup below, which would otherwise claim it —
    # a write verb on a PM noun is exactly its trigger — and hand it to a skill
    # whose tools are tracker-only and which cannot read a PRD at all. That
    # mismatch is the reported failure this path exists to fix. It serves BOTH
    # kinds of ticket (Sprntly and Jira), so unlike the lookup it must not be
    # gated on a tracker connection.
    if (
        not pinned_skill
        and not question.lstrip().startswith("/")
        and is_ticket_update(question, history)
    ):
        from app import ticket_update

        return ticket_update.answer(
            enterprise_id=enterprise_id,
            question=question,
            history=history,
            prd_id=prd_id,
        )

    # Live TRACKER read: a question referencing a ticket/epic wants the CURRENT
    # state — status, comments, epic children — not the periodic, comment-less
    # KG snapshot the generic router would answer from. Intercept before routing
    # and let the model fetch the real issues live (read-only tool loop, plus
    # Jira's propose→confirm card). The tracker picker resolves WHICH tracker the
    # company has connected (Jira, else ClickUp) — routing here has always been
    # tracker-agnostic while execution was Jira-only, which is why a ClickUp-only
    # company used to be told to connect Jira. When no tracker is connected it
    # returns a connect message rather than falling through. A slash command
    # (handled by route()) is exempt so an explicit skill invocation that merely
    # names Jira isn't hijacked.
    if not pinned_skill and not question.lstrip().startswith("/") and is_jira_lookup(question, history):
        from app.connector_lookup import tracker

        return tracker.answer(
            enterprise_id=enterprise_id, question=question, history=history
        )

    # Live read of any OTHER connected tool the question names — "check slack for
    # what was said about the pricing change", "what changed in the repo this
    # week", "which deals in hubspot mention onboarding". Same reason as the
    # tracker path: the answer lives in the tool, not in the KG snapshot. Placed
    # LAST of the interceptions on purpose — call-digest, VoC and DS own their
    # phrasings, and the tracker owns tickets; this one only fires when the
    # question NAMES a source none of them claimed. A source we cannot read live
    # is answered honestly here too (registry.not_supported_message), which is
    # better than the generic path guessing from the KG.
    if not pinned_skill and not question.lstrip().startswith("/"):
        connector_hints = is_connector_lookup(question, history)
        if connector_hints:
            from app.connector_lookup import registry

            return registry.answer_for_hints(
                enterprise_id=enterprise_id, question=question,
                history=history, hints=connector_hints,
            )

    if pinned_skill and _routable(pinned_skill, enterprise_id):
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
    #
    # Exception: a follow-up that points back at the thread ("...the details
    # about it?") carries no topic of its own, so the gate can read it as
    # out-of-domain when the thing it refers to is squarely in domain. Those
    # answer on the direct path WITH the history folded in — whose grounding
    # rules still apply, and whose ASK_SYSTEM scope clause still refuses if the
    # resolved question really is off-topic.
    if decision.source == "out_of_scope" and not is_context_dependent_followup(question, history):
        return _out_of_scope_payload()

    # PRD-tab grounding, shared by the direct and skill paths. Best-effort:
    # build_prd_context returns '' on any failure, degrading to a plain ask.
    prd_context = ""
    if prd_id:
        from app.prd_context import build_prd_context

        prd_context = build_prd_context(enterprise_id, prd_id)

    # Report grounding: a report generated in this thread is what "the report",
    # "your recommendations" and "point 1" refer to. Same best-effort contract
    # as the PRD block — '' when the thread has produced none, or on any read
    # failure, which is every ask that isn't a report follow-up.
    #
    # An explicit skill invocation is exempt (a slash command, or a pinned skill
    # — which is how the Artifacts "New report" button generates one). Those say
    # "run this skill", not "tell me about what you produced", and handing the
    # previous report to a run that is about to write the next one invites it to
    # be rewritten rather than regenerated.
    report_context = ""
    if not pinned_skill and not question.lstrip().startswith("/"):
        report_context = build_report_context(enterprise_id, conversation_id)

    if not decision.skill_id:
        # Direct path — corpus + KG, unchanged. Fold history into the question.
        q = _render_history(history) + question if history else question
        return compose_ask_answer(
            dataset, q, enterprise_id=enterprise_id, prd_context=prd_context,
            report_context=report_context, on_delta=on_delta,
        )

    # Custom skill (PRD 1854): an uploaded skill runs through the generic
    # single-shot path — never through a built-in's special-cased pipeline
    # below (public-feedback web search, VoC call digest, script tools), which
    # belongs to the vendored method, not to whatever the company uploaded.
    # Vendored ids skip the lookup outright: they always answer for the
    # built-in, so no upload can divert one here. One fresh DB read otherwise;
    # the resolved spec is handed to the single-shot call so it isn't looked
    # up twice.
    from app.skills.resolver import custom_skill_spec, is_builtin

    if not is_builtin(decision.skill_id):
        custom_spec = custom_skill_spec(enterprise_id, decision.skill_id)
        if custom_spec is not None:
            payload = _answer_single_shot(
                decision, enterprise_id, question, history, prd_context=prd_context,
                on_delta=on_delta, skill_spec=custom_spec,
                report_context=report_context,
            )
            return _maybe_verify(payload, enterprise_id)

    # Public-feedback routed: the report needs the public WEB (app stores,
    # Reddit, review sites), which the generic skill answer can't reach — it
    # would answer from the KG's first-party signal. Run the dedicated
    # web-search pipeline instead; it returns None only when the company
    # profile can't be read, falling through to the generic answer.
    if decision.skill_id == "public-feedback-report":
        from app import public_feedback

        pf = public_feedback.answer(
            enterprise_id=enterprise_id, question=question, history=history
        )
        if pf is not None:
            return _maybe_verify(pf, enterprise_id)

    # Company-research routed: "do some deep research on our company/pricing"
    # needs the public WEB, which the generic skill answer can't reach — it
    # would answer from whatever the KG already holds, which for a fresh
    # company is nothing. Run the dedicated staged sweep instead; it also seeds
    # the KG (origin="web_research", never "upload"/"connector"). Returns None
    # when the feature flag is off or the company profile can't be read, falling
    # through to the generic answer.
    if decision.skill_id == "company-research":
        from app import company_research

        cr = company_research.answer(
            enterprise_id=enterprise_id, question=question, history=history,
            # A sweep is several minutes of paid web search; each stage boundary
            # is a cancellation checkpoint, so a Stop actually stops it.
            is_cancelled=is_cancelled,
        )
        if cr is not None:
            return _maybe_verify(cr, enterprise_id)

    # Competitive-intelligence routed: the review needs the public WEB (what a
    # rival shipped, their pricing page, their app-store rating), which the
    # generic skill answer can't reach — it would answer from the KG's
    # first-party signal, and the skill's own integrity rule then forbids the
    # numbers it would need. Run the dedicated staged web-research pipeline
    # instead (Scan when prior state exists, Review otherwise). It returns None
    # only when the company profile can't be read, falling through to the
    # generic answer.
    if decision.skill_id == "competitive-intelligence-review":
        from app import competitive_intel

        cir = competitive_intel.answer(
            enterprise_id=enterprise_id, question=question, history=history,
            # A staged sweep is minutes of paid web search; each competitor and
            # each module boundary is a cancellation checkpoint, so a Stop
            # actually stops the spending (company_research parity).
            is_cancelled=is_cancelled,
        )
        if cir is not None:
            return _maybe_verify(cir, enterprise_id)

    # VoC routed by ANY stage — including the haiku intent router. Prefer the
    # SAME live call digest the phrase fast-paths use when a call source is
    # connected, so a phrasing only the LLM router understands ("what is the
    # number 1 user complaint from today's conversations?") gets the identical
    # answer path as a regex-matched one. Intent decides; phrases are only a
    # latency shortcut (decision 2026-07-27). Without a call source, render the
    # pinned HTML report from KG signal when there is any; else fall through to
    # the generic answer (which explains what to connect).
    if decision.skill_id == "voice-of-customer-report":
        from app import call_digest

        if not pinned_skill and call_digest.has_call_source(enterprise_id):
            return call_digest.answer(
                enterprise_id=enterprise_id, question=question, history=history
            )
        voc = _answer_voc_report(decision, enterprise_id, question, history)
        if voc is not None:
            return _maybe_verify(voc, enterprise_id)

    if decision.skill_id in SCRIPT_TOOLS:
        payload = _answer_with_script(
            decision, enterprise_id, question, history, prd_context=prd_context,
            report_context=report_context,
        )
    else:
        payload = _answer_single_shot(
            decision, enterprise_id, question, history, prd_context=prd_context,
            on_delta=on_delta, report_context=report_context,
        )
    return _maybe_verify(payload, enterprise_id)
