"""The weekly brief reports the finding; it does not pitch the fix.

WHAT WAS WRONG. The `top-insights` skill was rewritten to be insight-first —
titles size the problem, the card body's third beat is the evidence basis, and
the CTA pair leads with "View the evidence" — but two layers upstream and
downstream of it still framed the brief as an opportunity to capture:

  * The FRONTEND never rendered the skill's `_card.body` at all. Both brief
    adapters rebuilt the body as `subtitle + recommendation`, so every card the
    PM read ended on an imperative ("Ship one MCP integration in beta this
    quarter…") while the evidence-first body the skill had just composed was
    thrown away.
  * The GREETING method still said "Frame it as money to go capture, not fires
    to put out" and rolled the cards up as "$60M within reach" — a projected
    return on a fix, asserted at the top of the page before a single card had
    made its case.

These tests pin the two halves that live in this repo's backend: the card body
really is threaded through to the render, and no instructional line tells the
model to frame the greeting as a payoff.
"""
from __future__ import annotations

import re
from pathlib import Path

import pytest

SKILL_DIR = Path(__file__).resolve().parents[1] / "skills" / "top-insights"

# Phrasings that turn the greeting's roll-up into a promised return on acting.
PAYOFF_PHRASES = (
    "upside on the table",
    "money to go capture",
    "within reach",
    "upside to capture",
)


def _instructional_lines(text: str) -> list[str]:
    """Lines that instruct the model, excluding counter-examples.

    `references/examples.md` quotes bad greetings on purpose. A line is a
    counter-example when it is a ✗ specimen or the "Why it fails:" gloss that
    names the phrasing it is banning — those must be allowed to contain the very
    words the rest of the skill forbids.
    """
    out = []
    for line in text.splitlines():
        stripped = line.strip()
        if stripped.startswith("✗") or stripped.startswith("Why it fails:"):
            continue
        out.append(line)
    return out


@pytest.mark.parametrize(
    "rel", ["SKILL.md", "references/rubric.md", "references/examples.md"]
)
def test_no_instruction_frames_the_greeting_as_a_payoff(rel: str) -> None:
    text = (SKILL_DIR / rel).read_text(encoding="utf-8")
    for line in _instructional_lines(text):
        low = line.lower()
        for phrase in PAYOFF_PHRASES:
            if phrase not in low:
                continue
            # A prohibition is allowed to quote the phrase it prohibits.
            # Only "never"/"fails" count: a bare "not" also matches the ORIGINAL
            # bad instruction ("money to go capture, NOT fires to put out"), so
            # allowing it would make this guard fail open on the exact line it
            # exists to catch.
            assert re.search(r"\bnever\b|\bfails\b", low), (
                f"{rel} instructs the payoff framing: {line.strip()!r}"
            )


def test_skill_states_the_greeting_reports_rather_than_pitches() -> None:
    text = (SKILL_DIR / "SKILL.md").read_text(encoding="utf-8")
    step7 = text.split("### 7. Write the greeting", 1)[1].split("### 8.", 1)[0]
    low = step7.lower()
    assert "does not pitch a payoff" in low
    assert "at stake" in low
    # The banned phrasings are named explicitly so the model can recognize them.
    for phrase in ("money to go capture", "upside on the table", "within reach"):
        assert phrase in low, f"step 7 no longer names {phrase!r} as banned"


def test_canonical_template_greeting_is_not_a_pitch() -> None:
    """`assets/brief-template.html` is the design's source of truth for the
    frontend. It is not sent to the model, but a sample greeting that pitches a
    payoff is exactly what the next reader copies."""
    html = (SKILL_DIR / "assets" / "brief-template.html").read_text(encoding="utf-8")
    greeting = html.split('class="greeting"', 1)[1].split("</div>", 1)[0].lower()
    for phrase in PAYOFF_PHRASES:
        assert phrase not in greeting, f"template greeting still pitches: {phrase!r}"


def test_agent_prompt_says_the_card_body_is_what_renders() -> None:
    """The model must know `body` is the prose the PM reads and that
    `recommendation` never reaches the brief — otherwise it keeps writing the
    next step into `subtitle`, where the legacy fallback would surface it."""
    from app.synthesis import agent

    system = agent._SYSTEM
    assert "RENDERS THE CARD'S OWN `body`" in system
    assert "`recommendation` is a PRD seed the brief never shows" in system

    schema = agent._BRIEF_SCHEMA if hasattr(agent, "_BRIEF_SCHEMA") else None
    if schema is None:  # schema constant renamed — find it by shape
        schema = next(
            v for v in vars(agent).values()
            if isinstance(v, dict) and "insights" in (v.get("properties") or {})
        )
    rec = schema["properties"]["insights"]["items"]["properties"]["recommendation"]
    assert "NOT RENDERED IN THE BRIEF" in rec["description"]

    greeting = schema["properties"]["greeting"]["description"].lower()
    assert "never frame the total as a payoff" in greeting


def test_card_body_is_threaded_onto_the_insight_for_the_render() -> None:
    """`_card.body` is the ONLY source the brief render has for card prose. If
    the mapper drops it, every card silently falls back to `subtitle`."""
    from app.synthesis.top_insights_skill import cards_to_insights

    body = (
        "A checkout failure has been live three weeks. It is costing about "
        "$2.2M a year. Drawn from 340 support tickets and three interviews."
    )
    insights = [{"theme_id": "t1", "title": "old", "subtitle": "s",
                 "recommendation": "Ship the fix this sprint."}]
    cards = [{"type": "reliability", "title": "Checkout is failing — $2.2M rides on it",
              "body": body, "sources": ["support"], "finding_id": "t1"}]

    out = cards_to_insights(cards, insights)
    assert out[0]["_card"]["body"] == body
    # The recommendation survives in the payload (it seeds the PRD goal) but is
    # not what the card body carries.
    assert out[0]["recommendation"] == "Ship the fix this sprint."
    assert "Ship the fix" not in out[0]["_card"]["body"]
