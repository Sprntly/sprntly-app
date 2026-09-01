"""The scoped PRD editor — a targeted rewrite of the sections an instruction
actually touches, never a full prd-author regeneration.

WAS `prd_questions.py`, AND HELD TWO FEATURES. The other one — extracting a
PRD's `[ESCALATE]`/`[NEED]` items into answerable question cards — was removed
on 2026-09-01 (owner decision). What is left is the editor those answers used
to drive, which the PRD chat has always driven too: "make the goal more
specific", "tighten Risks". That is the whole module now, hence the rename.

The contract every caller consumes is `{"html", "sections_changed", "summary"}`,
with RuntimeError when the model returns no usable HTML — the caller then
leaves the PRD untouched. Deliberately NOT the prd-author skill: no template,
no evidence grounding, no exemplars, so it stays a cheap targeted edit.

`targeted_edit.enabled()` picks the strategy: a section-scoped rewrite when on,
a full-document re-emit when off. Both go through the LLM gateway, so tenant
isolation, prompt caching, cost/usage and the decision-log audit spine keep
working.
"""
from __future__ import annotations

import logging

from app.graph.gateway import llm_call
from app.llm import strip_code_fence
from app.prompts import VOICE_GUARD
from app import targeted_edit

logger = logging.getLogger(__name__)

_AGENT = "prd"


_EDIT_SCHEMA: dict = {
    "type": "object",
    "properties": {
        "html": {"type": "string"},
        "sections_changed": {"type": "array", "items": {"type": "string"}},
        "summary": {"type": "string"},
    },
    "required": ["html", "sections_changed", "summary"],
}

CHAT_EDIT_PROMPT_VERSION = "prd-chat-edit-v1"

_CHAT_EDIT_SYSTEM = """\
You are Sprntly's PRD editor. You are given a complete PRD as a self-contained \
HTML document and ONE edit instruction the user typed in chat ("make this PRD \
shorter", "add a rollout section", "rename the metric to activation rate"). \
Apply the instruction with the MINIMAL change necessary.

Rules:
- Change ONLY the sections the instruction actually affects. Leave every \
unaffected section — and the document's `<style>`, byline, structure, and \
section order — BYTE-FOR-BYTE unchanged. A broad instruction ("make it \
shorter") may touch several sections, but each change must serve the \
instruction; never re-author content the instruction doesn't reach.
- Do NOT restyle, reorder, rename, or rewrite anything the instruction doesn't \
ask for. Do NOT touch "User input needed" `[NEED]`/`[ESCALATE]` items unless \
the instruction resolves one. Invent no new facts, numbers, or requirements — \
fold in exactly what the instruction states; where it implies content you don't \
have, mark it `[NEED: …]` in the house style rather than fabricating.
- If the instruction does not actually request a change to the document (it's \
a question or a comment), return the document UNCHANGED with an empty \
`sections_changed` and a `summary` saying no edit was needed.
- Keep the output a single valid, self-contained HTML document that still \
renders in the same visual system.

Return the FULL updated HTML document in `html`, the list of human-readable \
section names you changed in `sections_changed` (e.g. ["Requirements", \
"Goal"]), and a one-line `summary` of the edit.""" + VOICE_GUARD

_CHAT_EDIT_USER = """\
Apply this edit instruction to the PRD below.

INSTRUCTION: {instruction}

PRD (HTML — edit and return the full document):
{prd_html}
"""


# ── Shared full-emit + targeted-edit dispatch ────────────────────────────────
#
# Both scoped editors below run the SAME LLM call shape (`_EDIT_SCHEMA`,
# max_tokens=32000, long_output). `_full_emit` is today's behavior verbatim,
# factored out so it is BOTH the flag-off path AND the fallback when a targeted
# splice is rejected. `_targeted_or_fallback` runs the changed-sections-only
# contract behind `TARGETED_EDIT_ENABLED` and falls back to `_full_emit` on any
# validation gate failure — fail-to-slow, never fail-to-corrupt.


def _full_emit(
    *, system: str, user: str, enterprise_id: str, purpose: str, prompt_version: str
) -> dict:
    """Today's full-document re-emit editor call. The return shape
    (`{html, sections_changed, summary}`) is the contract every caller consumes."""
    result = llm_call(
        enterprise_id=enterprise_id,
        agent=_AGENT,
        purpose=purpose,
        prompt_version=prompt_version,
        system=system,
        input=user,
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


def _targeted_or_fallback(
    *,
    prd_html: str,
    system: str,
    user: str,
    enterprise_id: str,
    purpose: str,
    prompt_version: str,
) -> dict:
    """Run the targeted-ops editor; splice+validate; fall back to `_full_emit`
    (the proven path) on ANY gate failure. Only reached when the flag is ON."""
    t_result = llm_call(
        enterprise_id=enterprise_id,
        agent=_AGENT,
        purpose=purpose,
        prompt_version=f"{prompt_version}-targeted",
        system=targeted_edit.targeted_system(system, targeted_edit.PRD_SECTION_MODEL),
        input=user,
        json_schema=targeted_edit.TARGETED_EDIT_SCHEMA,
        max_tokens=32000,
        long_output=True,
    )
    out = t_result.output if isinstance(t_result.output, dict) else {}
    summary = (out.get("summary") or "").strip()
    try:
        html, sections = targeted_edit.interpret(
            out,
            stored_doc=prd_html,
            model=targeted_edit.PRD_SECTION_MODEL,
            strip_fence=strip_code_fence,
        )
    except targeted_edit.FallbackNeeded as exc:
        logger.warning(
            "targeted PRD edit (%s) falling back to full-emit: %s", purpose, exc
        )
        return _full_emit(
            system=system,
            user=user,
            enterprise_id=enterprise_id,
            purpose=purpose,
            prompt_version=prompt_version,
        )
    return {
        "html": html,
        "sections_changed": [s for s in sections if isinstance(s, str)],
        "summary": summary,
    }


def apply_chat_edit(prd_html: str, instruction: str, enterprise_id: str) -> dict:
    """Run the scoped editor for a free-form chat instruction.

    Returns `{"html", "sections_changed", "summary"}`, and raises RuntimeError
    when the model returns no usable HTML — the caller then leaves the PRD
    untouched. Driven by the user's own edit instruction. This is
    the chat "make changes to the PRD" path: a targeted rewrite of the affected
    sections, never a full prd-author regeneration.
    """
    user = _CHAT_EDIT_USER.format(instruction=instruction, prd_html=prd_html)
    if not targeted_edit.enabled():
        return _full_emit(
            system=_CHAT_EDIT_SYSTEM,
            user=user,
            enterprise_id=enterprise_id,
            purpose="apply_prd_chat_edit",
            prompt_version=CHAT_EDIT_PROMPT_VERSION,
        )
    return _targeted_or_fallback(
        prd_html=prd_html,
        system=_CHAT_EDIT_SYSTEM,
        user=user,
        enterprise_id=enterprise_id,
        purpose="apply_prd_chat_edit",
        prompt_version=CHAT_EDIT_PROMPT_VERSION,
    )
