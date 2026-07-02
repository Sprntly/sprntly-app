"""Background PRD generation + on-demand Implementation Spec generation.

Two SEPARATE flows, deliberately decoupled:

1. **Human PRD (Part A) — eager.** Triggered when a user clicks "Generate PRD";
   the HTTP request returns immediately with a prd_id and status='generating',
   the actual Claude call runs in a worker thread, and the prds row gets updated
   to status='ready' (or 'failed') when done. This flow produces ONLY the
   human-readable PRD via the `prd-author` skill — it no longer also generates
   the machine spec.

2. **Implementation Spec (Part B) — on demand + cached.** Generated the FIRST
   time a user sends the PRD to Claude Code, via the dedicated
   `implementation-spec` skill fed the FINISHED human PRD. The result is cached
   in `prds.llm_part`, keyed to the human PRD's content hash
   (`llm_part_source_hash`). A re-send whose human PRD is unchanged reuses the
   cache; editing/restoring the human PRD clears it (db.prds.update_prd_content),
   so the next send regenerates against the new text. See `ensure_impl_spec`.

Why split: the machine spec is needless work (and latency) for the many PRDs
that are never handed to a coding agent, and the user-facing machine-PRD view
was removed. Generating it lazily keeps the human PRD fast and the spec fresh.

Grounding is regrounded on the KNOWLEDGE GRAPH (consistent with brief/evidence/
ask, which all answer from the brain): instead of dumping the per-dataset
markdown corpus, the runner resolves the insight's KG evidence trail
(insight → theme → synthesis-written hypothesis → SUPPORTS signals + theme
convergence signals, each with content/source_type/provenance/confidence) via
`graph.retrieval.insight_evidence_trail` and feeds THAT as the grounding. Both
Part A and the (later) Part B share the SAME grounding so they stay coherent.

Resilient: KG-first-with-fallback — if the insight has no KG backing (empty
trail), the runner falls back to the corpus grounding so a PRD never
hard-fails.
"""
import asyncio
import json
import logging
import time
import uuid

from app.company_template import render_templates_for_prompt
from app.config import settings
from app.corpus import load_corpus, load_prd_template
from app.db import complete_prd, get_brief_by_id
from app.db.companies import company_id_for_slug
from app.db.prds import (
    fail_prd,
    find_existing_prd,
    get_prd_rendered,
    prd_source_hash,
    set_prd_impl_spec,
    start_prd,
)
from app.graph.decision_log import log_agent_decision
from app.graph.facade import GraphFacade
from app.graph.gateway import llm_call
from app.graph.retrieval import insight_evidence_trail, render_evidence_trail_section
from app.prompts import VOICE_GUARD

logger = logging.getLogger(__name__)

PROMPT_VERSION = "prd-author-v3"
_SKILL = "prd-author"
# The machine-readable Implementation Spec (Part B) is generated on demand by the
# dedicated `implementation-spec` skill, fed the FINISHED human PRD (Part A) — its
# method (EARS requirements, design/contracts, dependency-ordered tasks,
# acceptance tests, DoD, independent verification) consumes the whole human PRD.
_SKILL_B = "implementation-spec"
PROMPT_VERSION_B = "prd-impl-spec-v1"
_AGENT = "prd"
# Storage variant for new PRD rows — shared by the on-demand route
# (routes/prd.py) and pre-warming below, so dedupe matches across both paths.
PRD_VARIANT = "v2"

# Agent-specific framing for the human PRD. The prd-author METHOD is supplied by
# the bound skill; this system prompt states the agent's job + grounding rules,
# and the _PART_A_DIRECTIVE steers the output to lean Markdown per the template.
# The Implementation Spec is a SEPARATE, on-demand call bound to the
# `implementation-spec` skill with its own _SYSTEM_B (below).
_SYSTEM = """\
You are Sprntly's PRD agent. Following the METHOD above, turn the supplied \
brief insight into a lean, human-readable Product Requirements Document for \
stakeholder alignment and decisions: problem & evidence, goals & guardrail \
metrics, non-goals, users & scenarios, signal-linked requirements (with \
inline `[edge case]` / `[failure]` tags on load-bearing branches), risks + the \
single riskiest assumption, open questions, rollout & measurement, and a \
testable done-when.

Ground every numeric claim, mechanism, metric, and acceptance criterion in \
the supplied insight and the evidence it was derived from — falsifiable by a \
reader who can pull the same data. The evidence is the company's \
connected-source signals (the same trail that backs the brief insight) when \
present, else the company's source data. Cite signals by source_type (and \
provenance where present). Never invent numbers, users, sources, business \
rules, or contracts; label unknowns per the METHOD (`[ASSUMPTION]` / \
`[ASSUMPTION → T0]` / `[ESCALATE]`) rather than guessing.

Emit Markdown only — no commentary outside the document. Produce ONLY the \
human-readable PRD; do NOT emit an Implementation Spec and do NOT emit a `---` \
horizontal rule.""" + VOICE_GUARD

# The human-PRD directive. Carries the insight + evidence + template and steers
# the model to emit lean Markdown (headings, prose, bullets, and ONE requirements
# table) per the template — no typed `:::` blocks. The frontend `prd-adapter`
# renders this markdown directly (h2/p/ul/table + inline emphasis).
_PART_A_DIRECTIVE = """\
PART DIRECTIVE: Produce ONLY the human-readable Product Requirements Document, \
as clean Markdown following the TEMPLATE below — its section order and \
headings, kept lean. The METHOD above governs your REASONING and quality bar \
(problem-first, no fabrication, signal-linked requirements, a primary metric \
split from guardrails, exactly one riskiest assumption with a three-line \
pre-mortem, a testable done-when); the TEMPLATE governs the OUTPUT STRUCTURE. \
Render Requirements as the Markdown table the template shows \
(ID | Requirement | Priority | Signal/Source | Acceptance), and tag \
load-bearing branches inline with `[edge case]` / `[failure]` so the downstream \
Implementation Spec inherits them. Fill every [bracketed] placeholder with \
concrete, grounded content; never keep a bracketed example; flag a missing \
number `[NEED: …]` rather than inventing it. Keep the body lean — push \
operational detail (A/B mechanics, tech notes, competitor scans, detailed \
rollout) to an Appendix. Do NOT include an Implementation Spec and do NOT emit \
a `---` separator. Start at the document title."""

_USER_TEMPLATE = """\
{part_directive}

Write the human-readable PRD for the following brief insight.

BRIEF INSIGHT (the problem to turn into a PRD):
{insight_json}

{evidence}
{exemplars}TEMPLATE (the PRD structure — produce it as your output):
{template}
"""

# The Implementation Spec is generated by the `implementation-spec` skill (its
# SKILL.md is the METHOD layer). It is fed the FINISHED human PRD — the skill
# consumes the PRD's tagged requirements (untagged = happy path; `[edge case]`
# / `[failure]` inline) and turns them into the LLM-readable spec.
_SYSTEM_B = """\
You are Sprntly's Implementation Spec agent. Following the METHOD above \
(the implementation-spec skill), turn the supplied human PRD into the \
LLM-readable Implementation Spec a coding agent can build and test against \
without ambiguity: EARS requirements traced to the PRD, design & contracts, \
dependency-ordered tasks, acceptance tests, a Definition of Done, and the \
independent verification report.

Consume ONLY the supplied PRD and evidence. Every requirement traces to a PRD \
goal; every contract binds verbatim to the PRD or evidence. Never invent a \
requirement, rule, or contract — split unknowns into research-resolvable \
(`[ASSUMPTION → T0]`) vs must-escalate (`[ESCALATE]`) per the METHOD. Inherit \
the PRD's load-bearing tags: untagged requirements are the happy path, \
`[edge case]` and `[failure]` requirements get their mandatory branches.

Emit Markdown only — no commentary outside the document, and do NOT restate \
the human PRD or emit a `---` separator. Start at the Implementation Spec \
heading.""" + VOICE_GUARD

_USER_TEMPLATE_B = """\
Produce the LLM-readable Implementation Spec for the human PRD below. \
Derive every requirement, contract, task, and acceptance test from this PRD \
and its evidence — trace each back to the PRD it implements.

HUMAN PRD (the spec to implement):
{human_prd}

{evidence}
{exemplars}"""

# Header for the source-data fallback block (KG trail unavailable / empty).
# Reader-facing wording deliberately avoids "corpus" — the model sees this
# header and must never echo internal vocabulary (see VOICE_GUARD).
_CORPUS_BLOCK = (
    "SOURCE DATA (the evidence the insight was derived from — ground claims here):\n"
    "{corpus}"
)


def _corpus_grounding(dataset: str) -> str:
    """Corpus fallback grounding: the per-dataset markdown corpus, as a
    labelled block. Used when the KG trail is empty (no KG backing for the
    insight, a legacy corpus dataset, or any KG read error)."""
    corpus = load_corpus(dataset)
    return _CORPUS_BLOCK.format(corpus=corpus.joined())


def _kg_trail(
    dataset: str, brief: dict, insight_index: int, insight: dict | None = None
) -> dict | None:
    """Best-effort KG evidence trail for the insight. Returns the trail dict
    (when it has KG backing) or None when there's no tenant context, the trail
    is empty, or any read fails — the caller then grounds on the corpus.

    `insight` overrides brief.insights[insight_index] (the backlog PRD path);
    when None the insight is read from the brief at insight_index.

    Resilient by construction: a slug that owns no company, an empty KG, a fake
    backend with no pgvector, or any read error all collapse to None so the PRD
    falls back to the corpus grounding (never hard-fails)."""
    company_id = company_id_for_slug(dataset)
    if not company_id:
        logger.info("PRD KG grounding: no company for slug=%s — corpus fallback", dataset)
        return None
    try:
        facade = GraphFacade()
        trail = insight_evidence_trail(
            facade, company_id, brief, insight_index, insight=insight
        )
    except Exception:  # noqa: BLE001 — KG read must never break PRD generation
        logger.exception("PRD KG grounding failed for slug=%s — corpus fallback", dataset)
        return None
    if not trail or trail.get("empty"):
        return None
    return trail


def _resolve_grounding(
    dataset: str, brief: dict, insight_index: int, insight: dict | None = None
) -> tuple[str, dict | None]:
    """Resolve the evidence block + (the KG trail it came from, or None).

    KG-first, consistent with brief/evidence/ask: the insight's evidence trail
    when it has backing, else corpus fallback (an empty KG, a legacy corpus
    dataset, or any KG read error). The returned trail (None on the corpus
    fallback) drives kg_refs in the decision log. `insight` overrides
    brief.insights[insight_index] (backlog PRD path).
    """
    trail = _kg_trail(dataset, brief, insight_index, insight)
    if trail is not None:
        return render_evidence_trail_section(trail), trail
    return _corpus_grounding(dataset), None


def _build_context(
    brief_id: int, insight_index: int, insight_override: dict | None = None
) -> dict:
    """Resolve everything a generation call needs, exactly once.

    Returns the shared inputs: the resolved company id, the evidence block + KG
    trail, the rendered PRD template, the insight, and the title. Reused by the
    human-PRD generation and (later) by the on-demand Implementation Spec, so
    both halves are grounded on the SAME facts and stay coherent.

    `insight_override` supplies the insight directly (the backlog PRD path: the
    theme is NOT in brief.insights, so there is no valid insight_index to read).
    When given, insight_index is only a storage sentinel and is NOT used to index
    the brief. When None, the insight is read from brief.insights[insight_index].
    """
    brief = get_brief_by_id(brief_id)
    if not brief:
        raise RuntimeError(f"brief_id={brief_id} not found")
    if insight_override is not None:
        insight = insight_override
    else:
        insights = brief.get("insights") or []
        if not (0 <= insight_index < len(insights)):
            raise RuntimeError(
                f"insight_index={insight_index} out of range (0..{len(insights) - 1})"
            )
        insight = insights[insight_index]
    dataset = brief.get("dataset", "asurion")
    # The decision log is tenant-scoped by company UUID, not the dataset slug.
    # Resolve it once; a dataset that owns no company (legacy corpus datasets)
    # yields None and the §4d decision log is skipped below.
    company_id = company_id_for_slug(dataset)
    # Reground on the KG evidence trail (synthesis engine) — the same signals
    # that back the brief insight — falling back to the corpus when there's no
    # KG backing or under the legacy engine. `trail` (None on the corpus path)
    # carries the kg_refs for the decision log.
    # Brief path keeps the original 3-arg call (the insight is read from the
    # brief at insight_index); the backlog path passes the synthesized insight so
    # the trail resolves the right theme. Splitting the call keeps existing
    # monkeypatches of _resolve_grounding (3-arg) working.
    if insight_override is not None:
        evidence, trail = _resolve_grounding(dataset, brief, insight_index, insight)
    else:
        evidence, trail = _resolve_grounding(dataset, brief, insight_index)
    # The human PRD is generated against the typed-`:::`-block contract
    # (data/sprntly_prd_template.md) that the frontend `prd-adapter` parses into
    # first-class components. Without these blocks the adapter degrades to plain
    # markdown — the "PRD looks like a raw .md" failure. (The Implementation Spec
    # does NOT use this template.)
    template = load_prd_template()
    title = insight.get("title") or f"Insight #{insight_index + 1}"
    # FORMAT/STYLE EXEMPLARS — the company's uploaded gold-standard PRD examples
    # ("what good looks like"). Additive context ONLY: folded into the prompt so
    # the model MATCHES the house structure & voice. No templates (or no company
    # for the slug) ⇒ empty string ⇒ a clean no-op. Best-effort.
    exemplars = ""
    if company_id:
        try:
            exemplars = render_templates_for_prompt(company_id)
        except Exception:  # noqa: BLE001 — exemplars are best-effort context
            logger.exception(
                "PRD format exemplars lookup failed for company=%s — skipping",
                company_id,
            )
            exemplars = ""
    return {
        "company_id": company_id,
        "dataset": dataset,
        "evidence": evidence,
        "trail": trail,
        "template": template,
        "exemplars": exemplars,
        "insight": insight,
        "title": title,
    }


def _exemplars_block(ctx: dict) -> str:
    """The FORMAT/STYLE EXEMPLARS block for a prompt, or '' when no templates."""
    exemplars = ctx.get("exemplars") or ""
    return f"\n{exemplars}\n" if exemplars else ""


def _call_part_a(ctx: dict, background: bool = False):
    """Generate the human-readable PRD via the `prd-author` skill.

    Steers the model to the typed-`:::`-block human PRD via _PART_A_DIRECTIVE and
    keeps `skill=_SKILL` so the METHOD + its `+prd-author@<hash>` version pin are
    preserved.
    """
    user = _USER_TEMPLATE.format(
        part_directive=_PART_A_DIRECTIVE,
        insight_json=json.dumps(ctx["insight"], indent=2),
        evidence=ctx["evidence"],
        exemplars=_exemplars_block(ctx),
        template=ctx["template"],
    )
    return llm_call(
        enterprise_id=ctx["company_id"] or ctx["dataset"],
        agent=_AGENT,
        purpose="generate_prd_part_a",
        prompt_version=PROMPT_VERSION,
        system=_SYSTEM,
        input=user,
        skill=_SKILL,
        background=background,
    )


def _call_impl_spec(ctx: dict, human_prd: str, background: bool = False):
    """Generate the Implementation Spec via the `implementation-spec` skill, fed
    the FINISHED human PRD. Binds `skill=_SKILL_B` so its METHOD + the
    `+implementation-spec@<hash>` version pin apply."""
    user = _USER_TEMPLATE_B.format(
        human_prd=human_prd,
        evidence=ctx["evidence"],
        exemplars=_exemplars_block(ctx),
    )
    return llm_call(
        enterprise_id=ctx["company_id"] or ctx["dataset"],
        agent=_AGENT,
        purpose="generate_prd_part_b",
        prompt_version=PROMPT_VERSION_B,
        system=_SYSTEM_B,
        input=user,
        skill=_SKILL_B,
        background=background,
    )


def _finalize_part_a(
    prd_id: int, brief_id: int, insight_index: int, ctx: dict, result_a
) -> None:
    """Persist the human PRD and decision-log the generation (§4d).

    Stores ONLY the human PRD in `payload_md` (the machine spec is generated
    separately, on demand). `result_a.prompt_version` carries the
    `+prd-author@<hash>` suffix the gateway appended; kg_refs pins the exact KG
    nodes this PRD was grounded on (empty on the corpus-fallback path).
    """
    human_part = str(result_a.output).strip()
    title = ctx["title"]
    complete_prd(prd_id=prd_id, title=title, md=human_part)

    trail = ctx["trail"]
    company_id = ctx["company_id"]
    kg_refs = (trail or {}).get("kg_refs") or []
    if company_id:
        factors = {
            "prd_id": prd_id,
            "brief_id": brief_id,
            "insight_index": insight_index,
            "skill": _SKILL,
            "grounding": "kg" if trail is not None else "corpus",
            "kg_signals": len((trail or {}).get("signals") or []),
        }
        log_agent_decision(
            enterprise_id=company_id,
            agent=_AGENT,
            decision_type="generate_prd",
            factors=factors,
            output={"title": title, "prd_id": prd_id},
            model=result_a.model,
            prompt_version=result_a.prompt_version,
            kg_refs=kg_refs,
        )


async def _generate_human_prd(
    prd_id: int, brief_id: int, insight_index: int, background: bool = False,
    insight_override: dict | None = None,
) -> None:
    """Build context, generate the human PRD (Part A only), persist + log.

    Runs as clean async (the event loop is never blocked — the synchronous
    `llm_call` runs in a worker thread). The Implementation Spec is NOT produced
    here; it is generated on demand by `ensure_impl_spec`. `insight_override`
    routes the backlog PRD path (the theme is not in brief.insights).
    """
    ctx = await asyncio.to_thread(
        _build_context, brief_id, insight_index, insight_override
    )
    result_a = await asyncio.to_thread(_call_part_a, ctx, background)
    await asyncio.to_thread(
        _finalize_part_a, prd_id, brief_id, insight_index, ctx, result_a
    )


def _run_sync(prd_id: int, brief_id: int, insight_index: int) -> None:
    """Synchronous entry point (used by tests and any sync caller).

    Drives the human-PRD generation to completion on a fresh event loop.
    """
    asyncio.run(_generate_human_prd(prd_id, brief_id, insight_index))


async def generate_prd(
    prd_id: int, brief_id: int, insight_index: int, background: bool = False,
    insight_override: dict | None = None,
) -> None:
    """Run the human-PRD generation; update DB with result.

    `background=True` (pre-warming) routes the call through the LLM gate's
    low-priority lane: capped concurrency, and always behind any interactive
    caller — a user's "Generate PRD" click is never queued behind warm work.
    The Implementation Spec is never produced here — it is on demand
    (`ensure_impl_spec`), so every generation path is human-PRD-only.

    `insight_override` supplies the insight directly (the backlog PRD path):
    insight_index is then a storage sentinel, not a brief index.
    """
    logger.info(
        "PRD generation starting prd_id=%s brief_id=%s insight_index=%s "
        "priority=%s",
        prd_id,
        brief_id,
        insight_index,
        "background" if background else "interactive",
    )
    try:
        await _generate_human_prd(
            prd_id, brief_id, insight_index, background, insight_override
        )
        logger.info("PRD generation succeeded prd_id=%s", prd_id)
    except Exception as exc:
        msg = f"{type(exc).__name__}: {exc}"
        logger.exception("PRD generation failed prd_id=%s", prd_id)
        fail_prd(prd_id, msg)


# ── on-demand Implementation Spec (Part B) ───────────────────────────────────

def ensure_impl_spec(prd_id: int) -> dict:
    """Return the machine-readable Implementation Spec for a human PRD, generating
    it on demand and caching the result.

    Called when a user sends the PRD to Claude Code. Idempotent + cached:
      - If a spec is already cached AND the human PRD is unchanged (its content
        hash matches `llm_part_source_hash`), the cached spec is returned —
        re-sends are free and deterministic.
      - Otherwise the spec is generated by the `implementation-spec` skill (fed
        the finished human PRD + the SAME evidence the PRD was grounded on),
        persisted to `llm_part` keyed to the current PRD hash, and returned.

    Cache invalidation is automatic: editing/restoring the human PRD clears
    `llm_part`/`llm_part_source_hash` (db.prds.update_prd_content) AND changes the
    PRD text, so the hash check alone would already force a regenerate.

    Returns {"llm_part": <markdown>, "cached": <bool>}.
    """
    row = get_prd_rendered(prd_id)  # human PRD as the user sees it (patches folded)
    if row is None:
        raise RuntimeError(f"prd_id={prd_id} not found")
    human_prd = (row.get("payload_md") or "").strip()
    if not human_prd:
        raise RuntimeError(f"prd_id={prd_id} has no human PRD to build a spec from")

    source_hash = prd_source_hash(human_prd)
    cached = (row.get("llm_part") or "").strip()
    if cached and row.get("llm_part_source_hash") == source_hash:
        logger.info("impl-spec cache HIT prd_id=%s", prd_id)
        return {"llm_part": cached, "cached": True}

    logger.info("impl-spec cache MISS prd_id=%s — generating", prd_id)
    ctx = _build_context(row["brief_id"], row["insight_index"])
    result_b = _call_impl_spec(ctx, human_prd)
    llm_part = str(result_b.output).strip()
    set_prd_impl_spec(prd_id, llm_part=llm_part, source_hash=source_hash)

    company_id = ctx.get("company_id")
    if company_id:
        try:
            log_agent_decision(
                enterprise_id=company_id,
                agent=_AGENT,
                decision_type="generate_impl_spec",
                factors={
                    "prd_id": prd_id,
                    "brief_id": row["brief_id"],
                    "insight_index": row["insight_index"],
                    "skill": _SKILL_B,
                    "has_llm_part": bool(llm_part),
                },
                output={"prd_id": prd_id},
                model=result_b.model,
                prompt_version=result_b.prompt_version,
            )
        except Exception:  # noqa: BLE001 — audit logging must never fail the send
            logger.exception("impl-spec decision log failed prd_id=%s", prd_id)

    return {"llm_part": llm_part, "cached": False}


def _top_insight_indices(insights: list, count: int) -> list[int]:
    """Original indices of the `count` insights a user is likeliest to open:
    the LLM-flagged headline insight first, then by confidence descending —
    the same hero-selection order the brief UI renders."""
    ranked = sorted(
        range(len(insights)),
        key=lambda i: (
            not bool((insights[i] or {}).get("is_headline")),
            -float((insights[i] or {}).get("confidence") or 0.0),
        ),
    )
    return ranked[:count]


async def _warm_one_prd(brief_id: int, insight_index: int, title: str) -> None:
    """Warm a single insight's human PRD as a multi-agent run.

    Mints a run_id and stamps it on the PRD row so the multi-agent "Generate
    PRD" path dedupes against this warm run instead of restarting it (see
    routes/multi_agent.find_existing_prd guard). Generates the human PRD only
    (Part A) — the prefetch's job is to have the human-readable PRD ready, not
    the implementation spec. Dedup-guarded and error-isolated: warming is a
    perf optimization, never a correctness requirement.
    """
    from app.prompts import PRD_TEMPLATE_VERSION

    try:
        if find_existing_prd(brief_id, insight_index, variant=PRD_VARIANT):
            return
        run_id = str(uuid.uuid4())
        prd_id = start_prd(
            brief_id=brief_id,
            insight_index=insight_index,
            title=title,
            template_version=PRD_TEMPLATE_VERSION,
            variant=PRD_VARIANT,
            run_id=run_id,
        )
        logger.info(
            "Warming PRD prd_id=%s brief_id=%s insight_index=%s run_id=%s",
            prd_id, brief_id, insight_index, run_id,
        )
        await generate_prd(
            prd_id, brief_id, insight_index, background=True
        )
    except Exception:  # noqa: BLE001 — warming is best-effort
        logger.exception(
            "PRD warming failed brief_id=%s insight_index=%s",
            brief_id, insight_index,
        )


async def warm_prds_for_brief(brief: dict) -> None:
    """Pre-generate human PRDs for the top insights of a freshly-saved brief.

    Fans out one warm task per insight (concurrently, rather than sequentially)
    so insight N's PRD never waits on insight N-1's to finish at the task level.
    Each warm runs in the LLM gate's BACKGROUND lane (see app.llm._PriorityGate),
    which still bounds in-flight warm model-calls (bg_cap, default 1) and always
    yields to interactive callers — so a user's "Generate PRD" click is never
    queued behind warming, and the small prod box isn't flooded. Each warm
    dedupes against existing rows, so a brief that already warmed — or an insight
    the user already generated — is skipped. Only the human PRD is warmed; the
    Implementation Spec stays on demand (`ensure_impl_spec`).

    Per-insight work (human PRD only + run_id stamping) lives in `_warm_one_prd`.
    """
    count = settings.prd_warm_count
    if count <= 0:
        return
    brief_id = brief.get("id")
    insights = brief.get("insights") or []
    if not brief_id or not insights:
        return

    indices = _top_insight_indices(insights, count)
    started = time.perf_counter()
    await asyncio.gather(
        *(
            _warm_one_prd(
                brief_id, i, (insights[i] or {}).get("title") or f"Insight #{i + 1}"
            )
            for i in indices
        )
    )
    # Single grep-able summary for onboarding-latency measurement: how long the
    # full human-PRD warm took for this brief (background-lane, bg_cap-gated).
    logger.info(
        "warm_prds_for_brief completed: %d insight(s) in %.1fs brief_id=%s",
        len(indices), time.perf_counter() - started, brief_id,
    )
