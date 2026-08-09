"""Chat summaries for freshly generated artifacts (PRD / evidence / prototype).

When an artifact finishes generating in the chat's right panel, the chat posts
a short assistant message summarizing what was actually built — who it targets,
the anchor decisions, what success is measured on — so the thread records the
outcome instead of going quiet after the acknowledgment. This module writes
that message: one small LLM call over the artifact's own content.

Contract mirrors app/prd_clarify.py (the small single-call template):
  - `summarize_artifact` never raises — any failure returns None and the chat
    simply gets no summary (the feature is an enhancement, never a blocker).
  - Model tier is the default (not the haiku routing tier): the summary is the
    one line of record the team reads instead of the document, so quality
    matters more than the pennies.

One deliberate prompt constraint: the summary must not END with an offer or a
question. The chat-intent resolver treats a bare affirmative ("yes", "go
ahead") as adopting whatever the assistant just offered in its most recent
turn (app/chat_intent.py) — and this summary becomes that most recent turn the
moment it is persisted. A trailing "Want me to break this into tickets?" would
silently rewire what the user's next "yes" means.
"""
from __future__ import annotations

import logging

from app.graph.gateway import llm_call

logger = logging.getLogger(__name__)

SUMMARY_PROMPT_VERSION = "artifact-chat-summary-v1"

# Content beyond this is noise for a 4-sentence summary; the cap also bounds
# spend on pathological documents (evidence HTML briefs can be large).
_CONTENT_MAX_CHARS = 24_000

_SCHEMA: dict = {
    "type": "object",
    "properties": {"summary": {"type": "string"}},
    "required": ["summary"],
    "additionalProperties": False,
}

_SYSTEM = """You summarize a freshly generated product artifact for the chat \
thread of the person who asked for it. They will open the document itself in a \
side panel; your summary is the one-glance record of what got built.

Write 3-5 sentences of plain prose. Be interpretive, not structural: say who \
the artifact targets, the anchor decisions or findings, the riskiest \
assumption, and what success is measured on — not a table of contents. Never \
enumerate section names.

Hard rules:
- No markdown headings, no bullet lists. Plain sentences (bold is fine).
- Do NOT end with a question or an offer of next steps ("Want me to…", \
"Shall I…"). State what exists; stop.
- Do not address the reader with pleasantries; open directly with substance.
- The content may be raw HTML; read through the markup.
"""

_USER = """ARTIFACT KIND: {kind}
TITLE: {title}

CONTENT:
{content}"""


def _clip(text: str) -> str:
    return text if len(text) <= _CONTENT_MAX_CHARS else text[:_CONTENT_MAX_CHARS]


def summarize_artifact(
    enterprise_id: str,
    *,
    kind: str,
    title: str,
    content: str,
    agent: str = "prd",
) -> str | None:
    """One-call chat summary of an artifact's content. Never raises.

    `agent` labels the usage bucket ("prd" | "evidence" | "design" — the
    caller passes the artifact's own family so the spend lands on the right
    dashboard feature). Returns None when the content is empty or the call
    fails — callers skip posting in that case.
    """
    if not content.strip():
        return None
    try:
        result = llm_call(
            enterprise_id=enterprise_id,
            agent=agent,
            purpose="artifact_chat_summary",
            system=_SYSTEM,
            input=_USER.format(kind=kind, title=title or "(untitled)", content=_clip(content)),
            prompt_version=SUMMARY_PROMPT_VERSION,
            json_schema=_SCHEMA,
            max_tokens=600,
        )
        out = result.output if isinstance(result.output, dict) else {}
        summary = out.get("summary")
        if not isinstance(summary, str) or not summary.strip():
            return None
        return summary.strip()
    except Exception:  # noqa: BLE001 — best-effort: no summary beats no artifact flow
        logger.exception("artifact chat summary failed (kind=%s)", kind)
        return None
