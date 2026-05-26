"""Delta classifier stub for inline prototype comments.

Spec source: Design_Agent_Spec.docx §5 (inline comments) + §8 (delta
classification — three classes: context_gap, preference, style).

POC implementation is a keyword heuristic — sufficient to wire the
classification chip in the UI + the prototype_comment_applied KG event.
The real Claude-driven classifier lands when the KG delta classifier
service is wired (P2). The signature already takes an `llm_call`
callable so the swap is a one-line change.

Mapping (heuristic):
  context_gap  — "missing", "add", "need", "where is", "should include"
  preference   — "prefer", "like better", "too", "rather"
  style        — "color", "font", "size", "padding", "spacing", "margin"

If multiple categories hit, the priority order is style > context_gap
> preference. This biases the chip towards the most actionable bucket
for the generator (style edits are cheap; context_gaps need a regen;
preferences are workspace-level memory).
"""
from __future__ import annotations

import logging
from typing import Any, Callable, Optional

from app.design.models import CommentClassification, Prototype

logger = logging.getLogger(__name__)


_STYLE_KEYWORDS = (
    "color",
    "colour",
    "font",
    "size",
    "padding",
    "spacing",
    "margin",
    "rounded",
    "shadow",
    "border",
)

_CONTEXT_GAP_KEYWORDS = (
    "missing",
    "add ",
    "need ",
    "needs ",
    "where is",
    "should include",
    "include ",
    "lacks",
    "doesn't have",
    "does not have",
)

_PREFERENCE_KEYWORDS = (
    "prefer",
    "like better",
    "rather",
    "too ",
    "would like",
    "would prefer",
)


def classify_comment(
    prototype: Prototype,
    comment_text: str,
    llm_call: Optional[Callable[[str, str], dict[str, Any]]] = None,
) -> CommentClassification:
    """Return one of three classes for a comment.

    Args:
        prototype: The owning prototype — gives the real classifier
            context (which is unused by the stub but kept for signature
            stability when Claude lands).
        comment_text: The comment text.
        llm_call: Optional LLM call hook (system, user) → dict. Stub
            ignores it; the real classifier will route here.

    Returns:
        One of "context_gap" | "preference" | "style". Default is
        "context_gap" when no keyword hits — generators treat that as
        "needs another pass" which is the safest fallback.
    """
    if llm_call is not None:
        # Hook reserved for P2. Today we still run the heuristic so the
        # stub remains deterministic. A future PR replaces the heuristic
        # entirely.
        logger.debug("classify_comment: llm_call provided but ignored in POC stub")

    lowered = (comment_text or "").lower()

    # Priority: style > context_gap > preference (see module docstring).
    if any(kw in lowered for kw in _STYLE_KEYWORDS):
        return "style"
    if any(kw in lowered for kw in _CONTEXT_GAP_KEYWORDS):
        return "context_gap"
    if any(kw in lowered for kw in _PREFERENCE_KEYWORDS):
        return "preference"
    # Default: treat unclassified as a context_gap — the safe assumption
    # is the comment is asking for a regen rather than a workspace-level
    # preference update.
    return "context_gap"


__all__ = ["classify_comment"]
