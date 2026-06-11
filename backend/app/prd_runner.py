"""Background PRD generation. Triggered when a user clicks 'Generate PRD';
the HTTP request returns immediately with a prd_id and status='generating',
the actual Claude call runs in a worker thread, and the prds row gets
updated to status='ready' (or 'failed') when done.

Re-platformed onto the `prd-author` skill (the canonical 2-part method): the
skill METHOD — Part A a human-readable PRD, a horizontal rule, then Part B an
LLM-readable Implementation Spec — is bound via the gateway (`skill=`), so the
agent-specific context (the brief insight being turned into a PRD, plus the
evidence it was derived from) is the only thing the runner supplies as input.

Performance (2-part concurrent generation): rather than ONE big sequential
`llm_call` that emits both halves and then splitting on the `---` rule, the
runner now issues TWO concurrent `prd-author` calls that share the SAME
brief-insight context + KG grounding:
  - Call A → produces ONLY Part A (the human-readable PRD).
  - Call B → produces ONLY Part B (the LLM-readable Implementation Spec).
Both stay coherent (same brief, same insight, same KG facts, same template);
each is steered to emit just its half via a per-part directive layered on the
shared `prd-author` skill binding (so the METHOD + version pin are preserved).
The two calls run concurrently (`asyncio.gather` over `asyncio.to_thread`,
since the gateway's `llm_call` is synchronous), so wall-clock is ~max(A, B)
instead of A+B — roughly a 2× speedup.

Grounding is regrounded on the KNOWLEDGE GRAPH (consistent with brief/evidence/
ask, which all answer from the brain): instead of dumping the per-dataset
markdown corpus, the runner resolves the insight's KG evidence trail
(insight → theme → synthesis-written hypothesis → SUPPORTS signals + theme
convergence signals, each with content/source_type/provenance/confidence) via
`graph.retrieval.insight_evidence_trail` and feeds THAT as the grounding. The
PRD's problem/evidence section then cites the actual data-source signals that
also back the brief insight.

Resilient: gated on `settings.brief_engine` (synthesis → KG grounding, legacy →
corpus), and KG-first-with-fallback even under synthesis — if the insight has
no KG backing (empty trail), the runner falls back to the corpus grounding so a
PRD never hard-fails.

Assembly + resilience:
  - Part A output → `payload_md`  — what the frontend renders, unchanged.
  - Part B output → `llm_part`    — for downstream coding-agent consumption.
  Part A is the required half: if Part B comes back empty (degenerate output),
  the PRD still completes with Part A + an empty `llm_part` (mirroring the old
  `_split_2part` resilience). If Part B's CALL fails outright, the PRD still
  completes with Part A + empty `llm_part`, and the Part-B failure is logged
  (never silent). A Part-A failure fails the whole PRD.

The generation is decision-logged (§4d) with the prd-author skill id + hash
(which the gateway pins into `prompt_version`, `+prd-author@<hash>`, taken from
the Part-A call so the audit row pins one consistent method version) plus
`kg_refs` = the signal/hypothesis/theme ids the trail surfaced and
`has_llm_part` reflecting whether Part B was produced.
"""
import asyncio
import json
import logging

from app.config import settings
from app.corpus import load_corpus
from app.db import complete_prd_2part, get_brief_by_id
from app.db.companies import company_id_for_slug
from app.db.prds import clone_prd, fail_prd, find_existing_prd, get_prd_rendered, start_prd
from app.graph.decision_log import log_agent_decision
from app.graph.facade import GraphFacade
from app.graph.gateway import llm_call
from app.graph.retrieval import insight_evidence_trail, render_evidence_trail_section
from app.skills.loader import get_skill

logger = logging.getLogger(__name__)

PROMPT_VERSION = "prd-author-v1"
_SKILL = "prd-author"
_AGENT = "prd"
# Storage variant for new PRD rows — shared by the on-demand route
# (routes/prd.py) and pre-warming below, so dedupe matches across both paths.
PRD_VARIANT = "v2"

# Agent-specific framing. The prd-author METHOD (the 2-part Part A/Part B
# structure + anti-hallucination discipline) is supplied by the bound skill;
# this system prompt only states the agent's job and grounding rules.
#
# Both halves are now generated as two concurrent calls, so the system prompt
# no longer asks for a single combined document; it states the shared job +
# grounding rules, and each call adds a per-part directive (see _PART_A_DIRECTIVE
# / _PART_B_DIRECTIVE) telling the model which half to emit. Both calls keep the
# prd-author skill binding so the METHOD and its version pin still apply.
_SYSTEM = """\
You are Sprntly's PRD agent. Following the METHOD above, turn the supplied \
brief insight into the prd-author two-part deliverable: Part A a \
human-readable PRD, and Part B an LLM-readable Implementation Spec a coding \
agent builds and tests against. You will be asked to produce exactly ONE of \
the two parts on this call — honor the PART DIRECTIVE in the request.

Ground every numeric claim, mechanism, metric, and acceptance criterion in \
the supplied insight and the evidence it was derived from — falsifiable by a \
reader who can pull the same data. The evidence is the knowledge graph's \
data-source signals (the same trail that backs the brief insight) when \
present, else the markdown corpus. Cite signals by source_type (and \
provenance where present). Never invent numbers, users, sources, business \
rules, or contracts; label unknowns per the METHOD (`[ASSUMPTION]` / \
`[ASSUMPTION → T0]` / `[ESCALATE]`) rather than guessing.

Emit Markdown only — no commentary outside the document. Produce ONLY the \
part named in the PART DIRECTIVE; do NOT emit the other part and do NOT emit \
the `---` horizontal rule that would separate them."""

# Per-part directives. Each call carries the SAME shared context (insight +
# evidence + template) plus exactly one of these so the model emits one half.
# Part A and Part B derive from the same brief, so they stay coherent.
_PART_A_DIRECTIVE = """\
PART DIRECTIVE: Produce ONLY Part A — the human-readable Product Requirements \
Document (the METHOD's Part A: problem & evidence, goals & success metrics, \
non-goals, users & scenarios, requirements/flows, risks, open questions, \
rollout, "done when"). Do NOT include Part B (the Implementation Spec) and do \
NOT emit the `---` separator. Start at the document title / Part A heading."""

_PART_B_DIRECTIVE = """\
PART DIRECTIVE: Produce ONLY Part B — the LLM-readable Implementation Spec \
(the METHOD's Part B: available artifacts & grounding, stakes & autonomy gate, \
constitution, EARS requirements traced to Part A, design & contracts, \
out-of-scope/not-constrained/unresolved, cross-cutting checklist, \
dependency-ordered tasks, acceptance tests & Definition of Done, independent \
verification report). Derive it from the SAME brief insight and evidence as \
Part A so the two halves stay coherent; its requirements trace to the Part A \
goals implied by that same insight. Do NOT include Part A and do NOT emit the \
`---` separator. Start at the Part B heading."""

_USER_TEMPLATE = """\
{part_directive}

Write your assigned part of the two-part PRD for the following brief insight.

BRIEF INSIGHT (the problem to turn into a PRD):
{insight_json}

{evidence}

TEMPLATE (the full two-part structure — produce ONLY your assigned part of it):
{template}
"""

# Header for the corpus-grounded fallback block (KG trail unavailable / empty).
_CORPUS_BLOCK = (
    "CORPUS (the evidence the insight was derived from — ground claims here):\n"
    "{corpus}"
)

# The horizontal rule the prd-author skill historically emitted between Part A
# (human PRD) and Part B (Implementation Spec). The concurrent path produces the
# two parts directly (no rule to split on), but `_split_2part` is retained for
# back-compat with any caller that still consumes a single combined document.
_PART_SEPARATOR = "\n---\n"


def _split_2part(md: str) -> tuple[str, str]:
    """Split a single combined prd-author document into (Part A, Part B).

    Retained for back-compat: the concurrent generation path produces the two
    halves directly and does NOT call this. Any caller still holding a single
    combined document (Part A + `---` + Part B) can split it here. If no rule is
    present (degenerate single-part output), Part A is the whole document and
    Part B is empty.
    """
    idx = md.find(_PART_SEPARATOR)
    if idx == -1:
        return md.strip(), ""
    part_a = md[:idx].strip()
    part_b = md[idx + len(_PART_SEPARATOR):].strip()
    return part_a, part_b


def _corpus_grounding(dataset: str) -> str:
    """The legacy grounding: the per-dataset markdown corpus, as a labelled
    block. Used when brief_engine='legacy' OR the KG trail is empty."""
    corpus = load_corpus(dataset)
    return _CORPUS_BLOCK.format(corpus=corpus.joined())


def _kg_trail(dataset: str, brief: dict, insight_index: int) -> dict | None:
    """Best-effort KG evidence trail for the insight. Returns the trail dict
    (when it has KG backing) or None when there's no tenant context, the trail
    is empty, or any read fails — the caller then grounds on the corpus.

    Resilient by construction: a slug that owns no company, an empty KG, a fake
    backend with no pgvector, or any read error all collapse to None so the PRD
    falls back to the corpus grounding (never hard-fails)."""
    company_id = company_id_for_slug(dataset)
    if not company_id:
        logger.info("PRD KG grounding: no company for slug=%s — corpus fallback", dataset)
        return None
    try:
        facade = GraphFacade()
        trail = insight_evidence_trail(facade, company_id, brief, insight_index)
    except Exception:  # noqa: BLE001 — KG read must never break PRD generation
        logger.exception("PRD KG grounding failed for slug=%s — corpus fallback", dataset)
        return None
    if not trail or trail.get("empty"):
        return None
    return trail


def _resolve_grounding(
    dataset: str, brief: dict, insight_index: int
) -> tuple[str, dict | None]:
    """Resolve the evidence block + (the KG trail it came from, or None).

    Gated on `settings.brief_engine`, consistent with brief/evidence/ask:
      - synthesis (default) → KG-first: the insight's evidence trail when it has
        backing, else corpus fallback.
      - legacy              → corpus grounding, no KG read.
    The returned trail (None on the corpus path) drives kg_refs in the
    decision log.
    """
    if settings.brief_engine == "synthesis":
        trail = _kg_trail(dataset, brief, insight_index)
        if trail is not None:
            return render_evidence_trail_section(trail), trail
    return _corpus_grounding(dataset), None


def _build_context(brief_id: int, insight_index: int) -> dict:
    """Resolve everything BOTH part-calls share, exactly once.

    Returns the shared inputs for the two concurrent prd-author calls: the
    resolved company id, the evidence block + KG trail (so Part A and Part B are
    grounded on the SAME facts), the rendered template, the insight, and the
    title. Building this once guarantees the two halves are coherent.
    """
    brief = get_brief_by_id(brief_id)
    if not brief:
        raise RuntimeError(f"brief_id={brief_id} not found")
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
    # carries the kg_refs for the decision log. Resolved ONCE and shared by both
    # part-calls so Part A and Part B cite the same evidence.
    evidence, trail = _resolve_grounding(dataset, brief, insight_index)
    # The PRD structure ships with the skill (templates/prd-template.md) so the
    # human PRD + Implementation Spec stay version-locked to the method. Both
    # calls receive the full template and emit only their assigned half.
    template = get_skill(_SKILL).templates["prd-template.md"]
    title = insight.get("title") or f"Insight #{insight_index + 1}"
    return {
        "company_id": company_id,
        "dataset": dataset,
        "evidence": evidence,
        "trail": trail,
        "template": template,
        "insight": insight,
        "title": title,
    }


def _call_part(ctx: dict, *, purpose: str, directive: str, background: bool = False):
    """One prd-author call that emits a single part.

    Shares `ctx`'s insight + evidence + template across both parts and steers
    the model to one half via `directive`. Keeps `skill=_SKILL` so the METHOD
    and its `+prd-author@<hash>` version pin are preserved on every call.
    """
    user = _USER_TEMPLATE.format(
        part_directive=directive,
        insight_json=json.dumps(ctx["insight"], indent=2),
        evidence=ctx["evidence"],
        template=ctx["template"],
    )
    return llm_call(
        enterprise_id=ctx["company_id"] or ctx["dataset"],
        agent=_AGENT,
        purpose=purpose,
        prompt_version=PROMPT_VERSION,
        system=_SYSTEM,
        input=user,
        skill=_SKILL,
        background=background,
    )


def _call_part_a(ctx: dict, background: bool = False):
    """Concurrent call A — ONLY the human-readable PRD (Part A)."""
    return _call_part(
        ctx, purpose="generate_prd_part_a", directive=_PART_A_DIRECTIVE,
        background=background,
    )


def _call_part_b(ctx: dict, background: bool = False):
    """Concurrent call B — ONLY the LLM Implementation Spec (Part B)."""
    return _call_part(
        ctx, purpose="generate_prd_part_b", directive=_PART_B_DIRECTIVE,
        background=background,
    )


def _finalize(prd_id: int, brief_id: int, insight_index: int, ctx: dict,
              result_a, result_b, part_b_error: str | None) -> None:
    """Assemble the two part-outputs, persist, and decision-log.

    Part A is required (its result is always present here). Part B is best-
    effort: an empty Part-B output OR a Part-B call failure (`part_b_error`)
    both complete the PRD with Part A + an empty `llm_part` — mirroring the old
    degenerate-output resilience — and the failure (if any) is logged, never
    silent.
    """
    human_part = str(result_a.output).strip()
    if result_b is not None:
        llm_part = str(result_b.output).strip()
    else:
        llm_part = ""
    if part_b_error:
        # Part A succeeded but Part B did not — complete with Part A alone
        # (the human PRD is valid on its own) and surface the failure.
        logger.error(
            "PRD Part B generation failed prd_id=%s — completing with Part A only: %s",
            prd_id, part_b_error,
        )

    title = ctx["title"]
    complete_prd_2part(
        prd_id=prd_id, title=title, human_md=human_part, llm_part=llm_part
    )

    # Decision-log the generation (§4d). `result_a.prompt_version` carries the
    # `+prd-author@<hash>` suffix the gateway appended; both part-calls bind the
    # same skill so they pin the same hash — the audit row records the Part-A
    # one consistently. `has_llm_part` reflects whether Part B was produced.
    # kg_refs: the signal/hypothesis/theme ids the evidence trail surfaced, so
    # the §4d audit row pins the exact KG nodes this PRD was grounded on (empty
    # on the corpus-fallback path, where `trail` is None).
    trail = ctx["trail"]
    company_id = ctx["company_id"]
    kg_refs = (trail or {}).get("kg_refs") or []
    if company_id:
        factors = {
            "prd_id": prd_id,
            "brief_id": brief_id,
            "insight_index": insight_index,
            "skill": _SKILL,
            "has_llm_part": bool(llm_part),
            "grounding": "kg" if trail is not None else "corpus",
            "kg_signals": len((trail or {}).get("signals") or []),
            "brief_engine": settings.brief_engine,
            # Two-call generation: record whether Part B failed (None on success)
            # so the audit row never hides a half-complete generation.
            "part_b_error": part_b_error,
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


async def _generate_2part(
    prd_id: int, brief_id: int, insight_index: int, background: bool = False
) -> None:
    """Build shared context, run the two part-calls CONCURRENTLY, finalize.

    The gateway's `llm_call` is synchronous, so each part runs in a worker
    thread and the two are awaited together via `asyncio.gather` — wall-clock is
    ~max(A, B), not A+B. Part A is required (gather propagates its failure and
    fails the whole PRD); Part B is best-effort (its failure is captured and the
    PRD still completes with Part A only).
    """
    ctx = await asyncio.to_thread(_build_context, brief_id, insight_index)

    # Run both halves concurrently. return_exceptions=True so a Part-B failure
    # doesn't cancel an in-flight Part A; we decide per-part below.
    result_a, result_b = await asyncio.gather(
        asyncio.to_thread(_call_part_a, ctx, background),
        asyncio.to_thread(_call_part_b, ctx, background),
        return_exceptions=True,
    )

    # Part A is required: re-raise so generate_prd marks the PRD failed.
    if isinstance(result_a, BaseException):
        raise result_a

    # Part B is best-effort: capture its failure, complete with Part A only.
    part_b_error: str | None = None
    if isinstance(result_b, BaseException):
        part_b_error = f"{type(result_b).__name__}: {result_b}"
        result_b = None

    await asyncio.to_thread(
        _finalize, prd_id, brief_id, insight_index, ctx,
        result_a, result_b, part_b_error,
    )


def _run_sync(prd_id: int, brief_id: int, insight_index: int) -> None:
    """Synchronous entry point (used by tests and any sync caller).

    Drives the concurrent two-part generation to completion. `generate_prd`
    calls `_generate_2part` directly on the running loop; this wrapper exists
    for sync callers and runs it on a fresh loop.
    """
    asyncio.run(_generate_2part(prd_id, brief_id, insight_index))


async def generate_prd(
    prd_id: int, brief_id: int, insight_index: int, background: bool = False
) -> None:
    """Run the concurrent two-part PRD generation; update DB with result.

    `background=True` (pre-warming) routes both part-calls through the LLM
    gate's low-priority lane: capped concurrency, and always behind any
    interactive caller — a user's "Generate PRD" click is never queued
    behind warm work.
    """
    logger.info(
        "PRD generation starting prd_id=%s brief_id=%s insight_index=%s priority=%s",
        prd_id,
        brief_id,
        insight_index,
        "background" if background else "interactive",
    )
    try:
        await _generate_2part(prd_id, brief_id, insight_index, background)
        logger.info("PRD generation succeeded prd_id=%s", prd_id)
    except Exception as exc:
        msg = f"{type(exc).__name__}: {exc}"
        logger.exception("PRD generation failed prd_id=%s", prd_id)
        fail_prd(prd_id, msg)


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


def _normalize_title(title) -> str:
    """Whitespace/case-insensitive form for insight-sameness comparison."""
    return " ".join(str(title or "").split()).lower()


def _matching_insight_index(prev_insights: list, insight: dict) -> int | None:
    """Index of the insight in `prev_insights` that is "the same" as `insight`:
    normalized title AND tag match. Conservative on purpose — a fuzzy match
    risks serving a PRD written for a different finding."""
    want_title = _normalize_title(insight.get("title"))
    want_tag = insight.get("tag")
    if not want_title:
        return None
    for i, prev in enumerate(prev_insights):
        prev = prev or {}
        if (
            _normalize_title(prev.get("title")) == want_title
            and prev.get("tag") == want_tag
        ):
            return i
    return None


def try_reuse_prd(brief: dict, insight_index: int) -> int | None:
    """Clone a previous brief's ready PRD when this brief's insight is the same.

    Brief regenerations mint a new brief_id, which orphans every PRD keyed to
    the old one — even when the insight itself is unchanged. When a recent
    prior brief of the same dataset carries the same insight (normalized title
    + tag) with a ready PRD, copy its RENDERED content (user edits + applied
    patches included) into a fresh ready row for (this brief, insight_index)
    and return the new id — instant, no LLM call. Returns None whenever reuse
    doesn't apply; callers then generate normally. Best-effort: any failure
    logs and returns None, never blocks generation.
    """
    if not settings.prd_reuse_enabled:
        return None
    brief_id = brief.get("id")
    dataset = brief.get("dataset")
    insights = brief.get("insights") or []
    if not brief_id or not dataset or not (0 <= insight_index < len(insights)):
        return None
    insight = insights[insight_index] or {}
    try:
        from app.db.briefs import recent_briefs_for_dataset

        for prev in recent_briefs_for_dataset(dataset, exclude_id=brief_id):
            prev_idx = _matching_insight_index(prev.get("insights") or [], insight)
            if prev_idx is None:
                continue
            src = find_existing_prd(prev["id"], prev_idx, variant=PRD_VARIANT)
            if not src or src.get("status") != "ready":
                continue
            rendered = get_prd_rendered(src["id"]) or src
            new_id = clone_prd(
                rendered, brief_id=brief_id, insight_index=insight_index
            )
            logger.info(
                "PRD reused src_prd_id=%s new_prd_id=%s brief_id=%s "
                "insight_index=%s (matched brief_id=%s idx=%s)",
                src["id"], new_id, brief_id, insight_index, prev["id"], prev_idx,
            )
            _log_reuse_decision(
                dataset=dataset, brief_id=brief_id, insight_index=insight_index,
                src=src, new_id=new_id, matched_brief_id=prev["id"],
            )
            return new_id
    except Exception:  # noqa: BLE001 — reuse is an optimization, never a gate
        logger.exception(
            "PRD reuse check failed brief_id=%s insight_index=%s — generating fresh",
            brief_id, insight_index,
        )
    return None


def _log_reuse_decision(
    *, dataset: str, brief_id: int, insight_index: int,
    src: dict, new_id: int, matched_brief_id: int,
) -> None:
    """§4d audit row for a reuse (error-isolated — never undoes the clone)."""
    try:
        company_id = company_id_for_slug(dataset)
        if not company_id:
            return
        log_agent_decision(
            enterprise_id=company_id,
            agent=_AGENT,
            decision_type="reuse_prd",
            factors={
                "prd_id": new_id,
                "src_prd_id": src.get("id"),
                "brief_id": brief_id,
                "insight_index": insight_index,
                "matched_brief_id": matched_brief_id,
                "skill": _SKILL,
            },
            output={"title": src.get("title"), "prd_id": new_id},
            model="none (cloned)",
            prompt_version=PROMPT_VERSION,
            kg_refs=[],
        )
    except Exception:  # noqa: BLE001
        logger.exception("PRD reuse decision-log failed prd_id=%s", new_id)


async def warm_prds_for_brief(brief: dict) -> None:
    """Pre-generate PRDs for the top insights of a freshly-saved brief.

    Runs strictly in the LLM gate's BACKGROUND lane (see app.llm._PriorityGate):
    at most one warm model-call holds a slot at a time and any interactive
    caller jumps ahead of it, so a user's "Generate PRD" click is never queued
    behind warming. PRDs warm sequentially (cheapest way to keep the background
    footprint at one call) and dedupe against existing rows, so a brief that
    already warmed — or an insight the user already generated — is skipped.

    Error-isolated per insight: warming is a perf optimization, never a
    correctness requirement.
    """
    count = settings.prd_warm_count
    if count <= 0:
        return
    brief_id = brief.get("id")
    insights = brief.get("insights") or []
    if not brief_id or not insights:
        return
    from app.prompts import PRD_TEMPLATE_VERSION

    for i in _top_insight_indices(insights, count):
        try:
            if find_existing_prd(brief_id, i, variant=PRD_VARIANT):
                continue
            # Reuse beats regeneration: an unchanged insight's PRD clones over
            # from the previous brief for free — no warm LLM spend at all.
            if await asyncio.to_thread(try_reuse_prd, brief, i):
                continue
            title = (insights[i] or {}).get("title") or f"Insight #{i + 1}"
            prd_id = start_prd(
                brief_id=brief_id,
                insight_index=i,
                title=title,
                template_version=PRD_TEMPLATE_VERSION,
                variant=PRD_VARIANT,
            )
            logger.info(
                "Warming PRD prd_id=%s brief_id=%s insight_index=%s",
                prd_id, brief_id, i,
            )
            await generate_prd(prd_id, brief_id, i, background=True)
        except Exception:  # noqa: BLE001 — warming is best-effort
            logger.exception(
                "PRD warming failed brief_id=%s insight_index=%s", brief_id, i
            )
