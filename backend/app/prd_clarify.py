"""Sufficiency check before chat-task PRD generation (clarify-first).

Issue d of the chat→PRD bug set: generation used to start immediately from
whatever the user typed, and the prd-author skill filled every gap with
assumptions — a thin prompt produced a confidently random PRD. This module is
the gate that runs BEFORE generation on every chat-PRD command (Apurva's
directive: ALWAYS check, even for detailed-looking prompts — length is not
sufficiency): does the task + conversation-attached documents actually carry
the ingredients a grounded PRD needs? If not, return targeted questions the
chat asks the user first; their answers are folded into the task and
generation proceeds.

Fail-open contract: ANY failure (gateway down, bad JSON) returns
sufficient=true with no questions, so the worst outcome is today's behavior
(generate immediately) — the gate must never block a PRD.
"""
from __future__ import annotations

import logging

from app.graph.gateway import llm_call

logger = logging.getLogger(__name__)

_AGENT = "prd"

CLARIFY_PROMPT_VERSION = "prd-clarify-v2"

_SCHEMA: dict = {
    "type": "object",
    "properties": {
        "sufficient": {"type": "boolean"},
        "missing": {"type": "array", "items": {"type": "string"}},
        "questions": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "prompt": {"type": "string"},
                    "options": {"type": "array", "items": {"type": "string"}},
                    "skip_default": {"type": "string"},
                },
                "required": ["prompt"],
                "additionalProperties": False,
            },
        },
    },
    "required": ["sufficient", "questions"],
    "additionalProperties": False,
}

_SYSTEM = """You gate PRD generation in a product-management app. The user \
asked for a PRD; you are given their task description and the text of any \
documents they attached in the conversation.

Decide whether this material is SUFFICIENT to write a grounded PRD without \
the author inventing key product decisions. Judge by CONTENT, not length. \
Check the core ingredients:

1. Problem / goal — what user or business problem is being solved, and why now
2. Target users — who this is for
3. Core requirements & scope — what it must do; what is explicitly out (v1 cuts)
4. Success criteria — WHAT the team will measure (never the numeric values)
5. Key constraints & context — systems it touches, non-negotiables, deadlines

Resolve each ingredient through this cascade, in order:
- STATED: the material answers it, explicitly or clearly implied → resolved. \
Never ask about anything the material already answers — that reads as not \
having listened.
- CONVENTION: an unambiguous industry default exists AND getting it wrong \
would not change the product's core mechanic → assume it; the PRD flags it \
inline. Never rest the CORE MECHANIC of the product on an assumed convention \
— when the gap sits at the heart of what is being built, ask.
- ASK: otherwise the ingredient is a candidate question, subject to the two \
tests below.

A question earns its slot only by passing BOTH tests:
- Consequence: different answers would produce materially different \
requirements. If every plausible answer yields the same PRD, don't ask.
- Openness: the answer genuinely cannot be inferred from the material.

Question rules:
- sufficient=true whenever no question survives the tests. ZERO questions is \
a valid and common outcome — never pad to reach a count. Five is a hard \
ceiling: keep the most consequential five; the PRD's own "User input needed" \
section catches the rest.
- Never ask for: metric values or baselines (the PRD marks those [NEED]), \
implementation or tech-stack choices, taste/wording preferences. Asking WHAT \
to measure is fine; asking for the number is not.
- Never ask openly what can be asked as a choice: when the answer space is \
enumerable, phrase it confirm-or-pick with 2–4 plausible `options`; \
otherwise leave options empty for free text.
- Every question carries `skip_default`: the one-line assumption the author \
proceeds with if the user skips it. Make it the most defensible default, not \
a guess.
- Make each question concrete to THIS task (name its systems and terms), \
never generic PM boilerplate.
- `missing` lists the ingredient names that are absent (from the 5 above).

Return ONLY the JSON object."""

_USER = """TASK (the user's request, plus any conversation context it was \
built from):
{task}

ATTACHED DOCUMENTS:
{docs}"""


def clarify_prd_task(enterprise_id: str, task: str, source_docs_md: str | None = None) -> dict:
    """Sufficiency verdict for a chat-PRD task. Never raises (fail-open to
    sufficient so generation is never blocked by the gate itself)."""
    try:
        # Default model tier (not the haiku routing tier): question quality
        # matters — a bad question poisons the PRD conversation — and the call
        # runs exactly once per command on the interactive path.
        result = llm_call(
            enterprise_id=enterprise_id,
            agent=_AGENT,
            purpose="clarify_task",
            system=_SYSTEM,
            input=_USER.format(task=task, docs=source_docs_md or "(none)"),
            prompt_version=CLARIFY_PROMPT_VERSION,
            json_schema=_SCHEMA,
            max_tokens=1500,
        )
        out = result.output if isinstance(result.output, dict) else {}
        questions = []
        for q in out.get("questions") or []:
            if isinstance(q, dict) and isinstance(q.get("prompt"), str) and q["prompt"].strip():
                opts = [o for o in (q.get("options") or []) if isinstance(o, str) and o.strip()]
                skip = q.get("skip_default")
                questions.append({
                    "prompt": q["prompt"].strip(),
                    "options": opts[:4],
                    "skip_default": skip.strip() if isinstance(skip, str) and skip.strip() else None,
                })
        sufficient = bool(out.get("sufficient")) or not questions
        return {
            "sufficient": sufficient,
            "questions": [] if sufficient else questions[:5],
            "missing": [m for m in (out.get("missing") or []) if isinstance(m, str)],
        }
    except Exception:  # noqa: BLE001 — the gate must never block generation
        logger.exception("PRD clarify check failed; failing open to sufficient")
        return {"sufficient": True, "questions": [], "missing": []}
