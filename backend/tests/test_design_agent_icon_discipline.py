"""Tests for the icon-discipline prompt rules (Layer 1).

Guards the generated-UI glyph discipline added to the Design Agent prompts:
- the scaffold system prompt forbids emoji as UI glyphs (lucide-react for
  icons; emoji only when the grounding source itself uses that exact emoji;
  reuse the source's icon set / inline SVGs);
- the recreate discipline reuses the source's exact icons and never
  substitutes an emoji for an icon;
- the template version is bumped (7) so cached prototypes regenerate;
- the pre-existing prose-scoped no-emoji rules (chat replies) are untouched.

Assertions are substring-based by design (no full-string equality, which would
brittle-break on word edits). Pure constants; no network, no DB, no env reads.
"""
from __future__ import annotations

from app.design_agent import prompts as p

SYS = p.DESIGN_AGENT_SCAFFOLD_SYSTEM


def test_scaffold_prompt_forbids_ui_emoji():
    """The assembled scaffold system prompt carries the UI-glyph rule:
    lucide-react for icons, no emoji in the UI unless the source uses it,
    reuse the source's icon set / inline SVGs."""
    # normalise whitespace — rule text wraps across hard line breaks.
    flat = " ".join(SYS.lower().split())
    # lucide-react named as the icon mechanism for UI glyphs
    assert "lucide-react" in flat
    assert "ui glyphs" in flat
    # forbids rendering emoji in the UI
    assert "do not render emoji" in flat
    # the source-exception ("only use an emoji if the grounding source ...")
    assert "grounding source" in flat
    # reuse the source's icon set / inline svgs rather than substituting
    assert "reuse" in flat
    assert "inline svgs" in flat or "icon set" in flat


def test_recreate_discipline_mentions_icon_reuse_no_emoji():
    """DESIGN_AGENT_RECREATE_DISCIPLINE gains the exact-icon-reuse /
    no-emoji-substitution clause."""
    disc = p.DESIGN_AGENT_RECREATE_DISCIPLINE
    # normalise whitespace — the clause wraps across hard line breaks in the
    # source string, so collapse runs of whitespace before substring-matching.
    flat = " ".join(disc.lower().split())
    assert "never substitute an emoji for an icon" in flat
    # reuse the source's exact icon imports / inline svgs
    assert "icon imports" in flat or "inline svgs" in flat


def test_template_version_bumped():
    """Template-invalidating changes move the version (now 8 — the screenshot
    design-reference directive)."""
    assert p.DESIGN_AGENT_TEMPLATE_VERSION == 8
    assert isinstance(p.DESIGN_AGENT_TEMPLATE_VERSION, int)


def test_prose_no_emoji_rules_unchanged():
    """The pre-existing prose-scoped (chat-reply) no-emoji guidance is correct
    as-is and must survive this change — guard against accidentally moving or
    breaking it while adding the UI-glyph rule. These are PROSE-scoped (chat
    replies), distinct from the new generated-UI glyph rule."""
    # scaffold prose: "No emoji unless the PRD asks for them."
    assert "No emoji unless the PRD asks for them" in p.DESIGN_AGENT_SCAFFOLD_SYSTEM
    # iterate + manual-edit prose: "No emoji unless asked."
    assert "No emoji unless asked" in p.DESIGN_AGENT_ITERATE_SYSTEM
    assert "No emoji unless asked" in p.DESIGN_AGENT_MANUAL_EDIT_SYSTEM
