"""Background PRD generation. Triggered when a user clicks 'Generate PRD';
the HTTP request returns immediately with a prd_id and status='generating',
the actual Claude call runs in a worker thread, and the prds row gets
updated to status='ready' (or 'failed') when done.

Re-platformed onto the `prd-author` skill (the canonical 2-part method): the
skill METHOD — Part A a human-readable PRD, a horizontal rule, then Part B an
LLM-readable Implementation Spec — is bound via the gateway (`skill=`), so the
agent-specific context (the brief insight being turned into a PRD, plus the
evidence it was derived from) is the only thing the runner supplies as input.

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

Output is split on the Part A/Part B horizontal rule:
  - Part A (human) → `payload_md`  — what the frontend renders, unchanged.
  - Part B (LLM)   → `llm_part`    — for downstream coding-agent consumption.

The generation is decision-logged (§4d) with the prd-author skill id + hash
(which the gateway pins into `prompt_version`, `+prd-author@<hash>`) plus
`kg_refs` = the signal/hypothesis/theme ids the trail surfaced.
"""
import asyncio
import json
import logging

from app.config import settings
from app.corpus import load_corpus
from app.db import complete_prd_2part, get_brief_by_id
from app.db.companies import company_id_for_slug
from app.db.prds import fail_prd
from app.graph.decision_log import log_agent_decision
from app.graph.facade import GraphFacade
from app.graph.gateway import llm_call
from app.graph.retrieval import insight_evidence_trail, render_evidence_trail_section
from app.skills.loader import get_skill

logger = logging.getLogger(__name__)

PROMPT_VERSION = "prd-author-v1"
_SKILL = "prd-author"
_AGENT = "prd"

# Agent-specific framing. The prd-author METHOD (the 2-part Part A/Part B
# structure + anti-hallucination discipline) is supplied by the bound skill;
# this system prompt only states the agent's job and grounding rules.
_SYSTEM = """\
You are Sprntly's PRD agent. Turn the supplied brief insight into ONE \
two-part document exactly as the METHOD above specifies: Part A a \
human-readable PRD, then a horizontal rule (`---`), then Part B an \
LLM-readable Implementation Spec a coding agent builds and tests against.

Ground every numeric claim, mechanism, metric, and acceptance criterion in \
the supplied insight and the evidence it was derived from — falsifiable by a \
reader who can pull the same data. The evidence is the knowledge graph's \
data-source signals (the same trail that backs the brief insight) when \
present, else the markdown corpus. Cite signals by source_type (and \
provenance where present). Never invent numbers, users, sources, business \
rules, or contracts; label unknowns per the METHOD (`[ASSUMPTION]` / \
`[ASSUMPTION → T0]` / `[ESCALATE]`) rather than guessing.

Emit Markdown only — no commentary outside the document. Part A and Part B \
MUST be separated by a single `---` horizontal rule so the two halves can be \
stored and rendered independently."""

_USER_TEMPLATE = """\
Write the two-part PRD for the following brief insight.

BRIEF INSIGHT (the problem to turn into a PRD):
{insight_json}

{evidence}

TEMPLATE (follow this structure for both parts):
{template}
"""

# Header for the corpus-grounded fallback block (KG trail unavailable / empty).
_CORPUS_BLOCK = (
    "CORPUS (the evidence the insight was derived from — ground claims here):\n"
    "{corpus}"
)

# The horizontal rule the prd-author skill emits between Part A (human PRD)
# and Part B (Implementation Spec). Splitting on the FIRST such rule keeps any
# `---` that appears later inside Part B intact.
_PART_SEPARATOR = "\n---\n"


def _split_2part(md: str) -> tuple[str, str]:
    """Split the prd-author output into (Part A human, Part B LLM).

    The skill separates the two halves with a `---` horizontal rule on its own
    line. If no rule is present (degenerate single-part output), Part A is the
    whole document and Part B is empty — `payload_md` still renders.
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


def _run_sync(prd_id: int, brief_id: int, insight_index: int) -> None:
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
    # Reground on the KG evidence trail (synthesis engine) — the same signals
    # that back the brief insight — falling back to the corpus when there's no
    # KG backing or under the legacy engine. `trail` (None on the corpus path)
    # carries the kg_refs for the decision log.
    evidence, trail = _resolve_grounding(dataset, brief, insight_index)
    # The PRD structure ships with the skill (templates/prd-template.md) so the
    # human PRD + Implementation Spec stay version-locked to the method.
    template = get_skill(_SKILL).templates["prd-template.md"]
    user = _USER_TEMPLATE.format(
        insight_json=json.dumps(insight, indent=2),
        evidence=evidence,
        template=template,
    )
    result = llm_call(
        enterprise_id=dataset,
        agent=_AGENT,
        purpose="generate_prd",
        prompt_version=PROMPT_VERSION,
        system=_SYSTEM,
        input=user,
        skill=_SKILL,
    )
    human_part, llm_part = _split_2part(str(result.output))
    title = insight.get("title") or f"Insight #{insight_index + 1}"
    complete_prd_2part(
        prd_id=prd_id, title=title, human_md=human_part, llm_part=llm_part
    )

    # Decision-log the generation (§4d). `result.prompt_version` carries the
    # `+prd-author@<hash>` suffix the gateway appended, so the audit row pins
    # the exact method version behind this PRD.
    # kg_refs: the signal/hypothesis/theme ids the evidence trail surfaced, so
    # the §4d audit row pins the exact KG nodes this PRD was grounded on (empty
    # on the corpus-fallback path, where `trail` is None).
    kg_refs = (trail or {}).get("kg_refs") or []
    log_agent_decision(
        enterprise_id=dataset,
        agent=_AGENT,
        decision_type="generate_prd",
        factors={
            "prd_id": prd_id,
            "brief_id": brief_id,
            "insight_index": insight_index,
            "skill": _SKILL,
            "has_llm_part": bool(llm_part),
            "grounding": "kg" if trail is not None else "corpus",
            "kg_signals": len((trail or {}).get("signals") or []),
            "brief_engine": settings.brief_engine,
        },
        output={"title": title, "prd_id": prd_id},
        model=result.model,
        prompt_version=result.prompt_version,
        kg_refs=kg_refs,
    )


async def generate_prd(prd_id: int, brief_id: int, insight_index: int) -> None:
    """Run PRD generation in a worker thread; update DB with result."""
    logger.info(
        "PRD generation starting prd_id=%s brief_id=%s insight_index=%s",
        prd_id,
        brief_id,
        insight_index,
    )
    try:
        await asyncio.to_thread(_run_sync, prd_id, brief_id, insight_index)
        logger.info("PRD generation succeeded prd_id=%s", prd_id)
    except Exception as exc:
        msg = f"{type(exc).__name__}: {exc}"
        logger.exception("PRD generation failed prd_id=%s", prd_id)
        fail_prd(prd_id, msg)
