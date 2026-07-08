"""Structured "User input needed" questions for a PRD — extraction + answer.

The `prd-author` skill writes a "User input needed" section into the PRD as
decorative HTML (`<ul class="inputs"><li>…[ESCALATE]/[NEED]…owner…</li></ul>`).
Nothing structured reaches the product, so those decisions sat inert inside the
document. This module gives them a life:

  1. `extract_input_questions(prd_id)` — a LIGHTWEIGHT pass (run once after the PRD
     is generated) reads the finished PRD and lifts each "User input needed" item
     into a structured question. For an [ESCALATE] product decision it also
     proposes a small set of plausible answer options (rendered as buttons in the
     PRD's chat); a [NEED] (missing-data) item carries no options and is answered
     as free text. Persisted via db.prd_input_questions. Best-effort — a failure
     here NEVER fails PRD generation.

  2. `apply_answer(prd_html, question, answer)` — the SCOPED EDITOR. Given the
     current PRD HTML and ONE resolved decision (question + chosen answer), it
     makes the MINIMAL change to the affected sections and removes that item from
     the "User input needed" list — WITHOUT re-running the heavy prd-author skill
     (no template, no evidence grounding, no exemplars). Returns the updated HTML
     plus which sections changed, so the chat can confirm "Updated Requirements,
     Goal". The route persists the result via the existing version-snapshot path.

Both calls go through the LLM gateway so tenant isolation, prompt-cache, cost/
usage, and the decision-log audit spine keep working.
"""
from __future__ import annotations

import logging

from app.db.prd_input_questions import replace_questions
from app.db.prds import get_prd
from app.graph.gateway import llm_call
from app.llm import strip_code_fence
from app.prompts import VOICE_GUARD

logger = logging.getLogger(__name__)

_AGENT = "prd"

# ── Extraction ───────────────────────────────────────────────────────────────

EXTRACT_PROMPT_VERSION = "prd-input-questions-extract-v1"

_EXTRACT_SCHEMA: dict = {
    "type": "object",
    "properties": {
        "questions": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "tag": {"type": "string", "enum": ["escalate", "need"]},
                    "prompt": {"type": "string"},
                    "owner": {"type": "string"},
                    "options": {
                        "type": "array",
                        "items": {
                            "type": "object",
                            "properties": {
                                "label": {"type": "string"},
                                "description": {"type": "string"},
                            },
                            "required": ["label"],
                        },
                    },
                },
                "required": ["tag", "prompt", "options"],
            },
        },
    },
    "required": ["questions"],
}

_EXTRACT_SYSTEM = """\
You extract the "User input needed" items from a Product Requirements Document \
and turn each into a structured, answerable question. The PRD is an HTML \
document; read its content and ignore the markup/CSS.

Find the "User input needed" section (items tagged [ESCALATE] or [NEED], each \
with an owner). For EACH item emit one question:
- `tag`: "escalate" for a product DECISION the team must make; "need" for MISSING \
DATA / a fact the team must supply.
- `prompt`: the decision or the missing fact, phrased as a clear, self-contained \
question a PM can answer without re-reading the PRD.
- `owner`: the owner named on the item (e.g. "PM", "Data"), or omit if none.
- `options`: for an "escalate" decision, 2–4 SHORT, MUTUALLY-EXCLUSIVE candidate \
answers grounded in the PRD's own context (each a `label`, plus an optional \
one-line `description` of the tradeoff). For a "need" data item emit an EMPTY \
`options` array — it is answered with a value, not a choice.

Rules: invent NOTHING beyond what the PRD supports; options must be plausible \
given the document. If the PRD has no "User input needed" items, return an empty \
`questions` array. Return ONLY the structured object.""" + VOICE_GUARD

_EXTRACT_USER = """\
Extract the "User input needed" questions from this PRD.

PRD (HTML — read the content, ignore the markup):
{prd_html}
"""


def _run_extract(prd_html: str, enterprise_id: str) -> list[dict]:
    """One structured extraction call → the list of question dicts (possibly []).
    Kept separate from persistence so it is trivially unit-testable with a mocked
    gateway."""
    result = llm_call(
        enterprise_id=enterprise_id,
        agent=_AGENT,
        purpose="extract_prd_input_questions",
        prompt_version=EXTRACT_PROMPT_VERSION,
        system=_EXTRACT_SYSTEM,
        input=_EXTRACT_USER.format(prd_html=prd_html),
        json_schema=_EXTRACT_SCHEMA,
        max_tokens=4000,
        # Deterministic lift of an existing PRD section into structured
        # questions — no creativity wanted; temperature 0 keeps it stable.
        temperature=0,
    )
    out = result.output if isinstance(result.output, dict) else {}
    questions = out.get("questions") or []
    return questions if isinstance(questions, list) else []


def extract_input_questions(prd_id: int) -> list[dict]:
    """Extract + persist the PRD's "User input needed" questions. Best-effort.

    Reads the PRD, runs the lightweight extraction pass, and replaces the stored
    questions for this prd_id. Returns the persisted rows (empty on no items or on
    any failure). NEVER raises — extraction is a convenience layered on top of a
    PRD that is already generated and stored, so a hiccup here must not fail (or
    appear to fail) PRD generation.
    """
    try:
        row = get_prd(prd_id)
        if not row:
            logger.info("prd_input_questions: prd_id=%s not found — skipping", prd_id)
            return []
        prd_html = (row.get("payload_md") or "").strip()
        if not prd_html:
            return []
        # Same enterprise attribution the PRD generation used: the brief's company
        # isn't threaded here, so attribute to the brief_id-scoped dataset via the
        # gateway's enterprise_id (a string tag). Using the PRD's brief keeps the
        # telemetry grouped with the rest of that PRD's calls.
        enterprise_id = str(row.get("brief_id") or prd_id)
        questions = _run_extract(prd_html, enterprise_id)
        return replace_questions(prd_id, questions)
    except Exception:  # noqa: BLE001 — extraction must never break generation
        logger.exception("prd_input_questions extraction failed prd_id=%s", prd_id)
        return []


# ── Scoped answer / editor ───────────────────────────────────────────────────

EDIT_PROMPT_VERSION = "prd-input-answer-edit-v1"

_EDIT_SCHEMA: dict = {
    "type": "object",
    "properties": {
        "html": {"type": "string"},
        "sections_changed": {"type": "array", "items": {"type": "string"}},
        "summary": {"type": "string"},
    },
    "required": ["html", "sections_changed", "summary"],
}

_EDIT_SYSTEM = """\
You are Sprntly's PRD editor. You are given a complete PRD as a self-contained \
HTML document and ONE resolved "User input needed" decision (a question and the \
answer the team chose). Apply the answer to the PRD with the MINIMAL change \
necessary.

Rules:
- Change ONLY the sections the answer actually affects (e.g. Requirements, Goal, \
Hypothesis, Users, Appendix). Leave every unaffected section — and the document's \
`<style>`, byline, structure, and section order — BYTE-FOR-BYTE unchanged.
- Remove the answered item from the "User input needed" list. If that list \
becomes empty, remove the whole "User input needed" section (its `<div \
class="eyebrow">` and the `<ul class="inputs">`) — the section is self-clearing.
- Do NOT restyle, reorder, rename, or re-author anything else. Do NOT touch \
unrelated `[NEED]`/`[ESCALATE]` items. Invent no new numbers — fold in exactly \
what the answer states.
- Keep the output a single valid, self-contained HTML document that still renders \
in the same visual system.

Return the FULL updated HTML document in `html`, the list of human-readable \
section names you changed in `sections_changed` (e.g. ["Requirements", "Goal"]), \
and a one-line `summary` of the edit.""" + VOICE_GUARD

_EDIT_USER = """\
Apply this resolved decision to the PRD below.

QUESTION: {question}
ANSWER: {answer}

PRD (HTML — edit and return the full document):
{prd_html}
"""


def apply_answer(prd_html: str, question: str, answer: str, enterprise_id: str) -> dict:
    """Run the scoped editor: fold ONE answered decision into the PRD HTML.

    Returns `{"html": <updated document>, "sections_changed": [...],
    "summary": ...}`. Raises RuntimeError if the model returns no usable HTML (the
    caller then leaves the PRD untouched). This is deliberately NOT the prd-author
    skill — no template, no grounding, no exemplars — so it stays a cheap,
    targeted edit rather than a full regeneration.
    """
    result = llm_call(
        enterprise_id=enterprise_id,
        agent=_AGENT,
        purpose="apply_prd_input_answer",
        prompt_version=EDIT_PROMPT_VERSION,
        system=_EDIT_SYSTEM,
        input=_EDIT_USER.format(question=question, answer=answer, prd_html=prd_html),
        json_schema=_EDIT_SCHEMA,
        max_tokens=32000,
        long_output=True,
    )
    out = result.output if isinstance(result.output, dict) else {}
    html = strip_code_fence((out.get("html") or "").strip())
    if not html:
        raise RuntimeError("scoped PRD edit returned no HTML")
    sections = out.get("sections_changed") or []
    return {
        "html": html,
        "sections_changed": [s for s in sections if isinstance(s, str)],
        "summary": (out.get("summary") or "").strip(),
    }
