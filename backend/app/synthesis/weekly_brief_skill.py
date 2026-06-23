"""Adapters between the synthesis pipeline and the `weekly-brief` skill.

The synthesis pipeline (`app/synthesis/agent.py`) computes ranked
`ThemeConvergence` candidates UPSTREAM (convergence → evidence gate → dedup →
goal-aligned scoring). The brief COMPOSITION step then binds the LLM to the
vendored `weekly-brief` skill (see `skills/weekly-brief/SKILL.md`) to phrase
those candidates into a brief. This module is the two-way contract:

  to_signal_payload(...)   ThemeConvergence candidates → the skill's `signal`
                           schema (references/signal-schema.json) + light
                           context (recipient name, company scale), rendered as
                           the `brief_request` the skill reads. The skill PHRASES
                           these — it never recomputes the numbers, so every
                           figure it surfaces traces back to a candidate field.

  cards_to_insights(...)   the skill's emitted `brief.cards[]` → the persisted
                           `insights[]` shape the frontend brief UI reads
                           (web/app/lib/brief-v2-adapter.ts → BriefV2State). The
                           skill's card vocabulary (type/accent, pain-then-value
                           title, body, source chips, CTAs) is mapped onto the
                           existing insight fields (tag, title, subtitle,
                           recommendation, metrics, convergence, …) so the brief
                           render keeps working unchanged. Fields the skill does
                           not carry (e.g. inline chart_hints) keep being derived
                           by the agent as today.

Keeping this mapping in one module means the skill's schema and the frontend
contract are reconciled in exactly one place.
"""
from __future__ import annotations

from typing import TYPE_CHECKING, Optional

if TYPE_CHECKING:
    from app.synthesis.convergence import ThemeConvergence

# The skill's closed signal/card taxonomy (references/signal-schema.json) and
# its accent colors (SKILL.md step 3). Kept here so the mapping to the existing
# brief `tag` vocabulary lives next to the code that uses it.
SKILL_TYPE_ACCENTS: dict[str, str] = {
    "reliability": "#c0473c",
    "retention": "#b23b52",
    "competitive": "#b07a2e",
    "growth": "#1a8a52",
    "demand": "#5f57a6",
    "engagement": "#3f63a0",
    "compliance": "#4f5675",
}

# skill `type` → existing brief `tag` (the FIX/BUILD/OPTIMIZE buckets the
# frontend's TAG_META keys off). Loss/problem types map to FIX, opportunity
# types to BUILD, behavior/optimization types to OPTIMIZE.
_TYPE_TO_TAG: dict[str, str] = {
    "reliability": "something_broken",
    "retention": "something_broken",
    "compliance": "something_broken",
    "competitive": "something_new",
    "demand": "something_new",
    "growth": "something_better",
    "engagement": "something_better",
}

# existing brief `tag` → a sensible skill `type` (used when we hand the model a
# prior tag as a hint). Inverse-ish of _TYPE_TO_TAG, picking the most common.
_TAG_TO_TYPE: dict[str, str] = {
    "something_broken": "reliability",
    "something_new": "demand",
    "something_better": "growth",
}


def tag_for_skill_type(skill_type: Optional[str]) -> str:
    """Map a skill card `type` to the brief `tag` the frontend renders.

    Unknown / missing types fall back to `something_broken` (the frontend's own
    default in brief-v2-adapter.ts), so a card always has a renderable tag.
    """
    return _TYPE_TO_TAG.get((skill_type or "").lower().strip(), "something_broken")


def accent_for_skill_type(skill_type: Optional[str]) -> str:
    """The taxonomy accent hex for a skill `type` (empty string if unknown)."""
    return SKILL_TYPE_ACCENTS.get((skill_type or "").lower().strip(), "")


def company_scale_for(candidates: list["ThemeConvergence"]) -> Optional[str]:
    """A light company-scale hint for the skill's prioritize step (normalize
    impact to scale). We don't have ARR here, so summarize the revenue actually
    at stake across the ranked candidates — the only scale figure the pipeline
    already computed. None when no candidate carries a revenue figure (the skill
    then ranks within the brief, per SKILL.md step 2)."""
    total = sum(max(0.0, c.revenue_at_stake_usd) for c in candidates)
    if total <= 0:
        return None
    if total >= 1_000_000:
        return f"~${total / 1_000_000:.1f}M total revenue at stake across findings"
    return f"~${total / 1_000:.0f}k total revenue at stake across findings"


def to_signal_payload(
    candidates: list["ThemeConvergence"],
    *,
    recipient: str,
    company_scale: Optional[str],
) -> str:
    """Render the ranked candidates as the skill's `brief_request` (a list of
    `signal` objects + context), as the user input for the bound composition
    call.

    Each candidate becomes one `signal`. We supply the fields the skill reads
    and PHRASES — never a computed number the skill would have to invent:
      - type        : derived from the candidate's competitive/revenue profile
      - pain        : the sharpest computed stat (revenue at stake / breadth)
      - value.basis : how the figure was derived (auditable); amount stays the
                      candidate's own revenue figure, or null → qualitative value
      - story       : the candidate label + its evidence
      - sources     : the distinct source_types that converged (honest chips)
      - evidence    : the candidate's top evidence lines
      - confidence  : the candidate's effective weight, normalized
      - urgency/reach: derived from competitor pressure / signal count

    The rendered text is human-readable JSON-ish blocks (mirroring how the
    pipeline already renders candidate payloads) rather than strict JSON, since
    the model reads it as grounding, not as a parsed contract.
    """
    lines: list[str] = [
        "BRIEF_REQUEST — compose the weekly brief from these already-analyzed "
        "signals. Every number below is an INPUT; phrase it, never recompute or "
        "invent one.",
        f"recipient: {recipient or 'there'}",
        f"company_scale: {company_scale or '(unknown — rank within the brief)'}",
        "",
        "signals:",
    ]
    for c in candidates:
        skill_type = _candidate_skill_type(c)
        rev = c.revenue_at_stake_usd
        amount = _format_usd(rev) if rev > 0 else "null"
        # urgency: competitor pressure escalates it; otherwise breadth-led.
        if c.competitor_pressure:
            urgency = "high"
        elif c.breadth >= 2:
            urgency = "medium"
        else:
            urgency = "low"
        evidence_lines = "\n".join(
            f"      - [{e['source_type']}/{e['kind']}] {e['content']}"
            for e in c.evidence
        )
        lines.append(
            f"  - id: {c.theme_id}\n"
            f"    type: {skill_type}\n"
            f"    pain: {{ metric: \"revenue at stake / converging sources\", "
            f"value: \"{amount if rev > 0 else f'{c.breadth} sources converging'}\", "
            f"context: \"{c.theme_label}\" }}\n"
            f"    value: {{ verb: \"recover\", amount: {amount}, "
            f"basis: \"summed revenue_at_risk across {c.signal_count} converging "
            f"signals (recency-weighted)\", confidence: "
            f"{_norm_confidence(c):.2f} }}\n"
            f"    story: \"{c.theme_label}: {c.signal_count} signals across "
            f"{c.breadth} source types\"\n"
            f"    sources: {sorted(c.source_types)}\n"
            f"    confidence: {_norm_confidence(c):.2f}\n"
            f"    urgency: {urgency}\n"
            f"    reach: {{ unit: \"accounts\", count: {c.signal_count} }}\n"
            f"    evidence:\n{evidence_lines}"
        )
    return "\n".join(lines)


def _candidate_skill_type(c: "ThemeConvergence") -> str:
    """Pick a skill `type` for a candidate from its computed profile. Competitor
    pressure → competitive; a revenue figure → growth; otherwise demand. This is
    only a HINT — the model may reclassify per SKILL.md step 3, and the persisted
    tag is taken from the card the model returns."""
    if c.competitor_pressure:
        return "competitive"
    if c.revenue_at_stake_usd > 0:
        return "growth"
    return "demand"


def _format_usd(value: float) -> str:
    if value >= 1_000_000:
        return f"\"${value / 1_000_000:.1f}M\""
    if value >= 1_000:
        return f"\"${value / 1_000:.0f}k\""
    return f"\"${value:.0f}\""


def _norm_confidence(c: "ThemeConvergence") -> float:
    """Effective weight normalized into 0..1 for the skill's confidence floor."""
    if c.signal_count <= 0:
        return 0.0
    return max(0.0, min(1.0, c.effective_weight / c.signal_count))


def cards_to_insights(
    cards: list[dict],
    insights: list[dict],
) -> list[dict]:
    """Reconcile the skill's `brief.cards[]` onto the persisted `insights[]`.

    The agent ALSO emits the existing `insights[]` (the UI contract: tag, title,
    subtitle, recommendation, metrics, chart_hints, convergence, confidence,
    is_headline, prototypeable, theme_id) so the brief render keeps working
    untouched. This function layers the skill card's phrasing on top, by
    matching a card to its insight via `signal_id` == insight `theme_id`:

      - title : the card's pain-then-value title (the skill's signature line)
                replaces the insight title when present.
      - tag   : if the insight lacks a tag, derive it from the card `type`.
      - _card : the full skill card (type, accent, body, sources, ctas,
                signal_id) is threaded onto the insight as `_card` so downstream
                consumers / the HTML render can use the skill's native object.

    Insight fields the skill does not carry (chart_hints, metrics, convergence,
    prototypeable) are left exactly as the agent derived them. An insight with
    no matching card is returned unchanged. Returns a NEW list; inputs are not
    mutated.
    """
    cards_by_id: dict[str, dict] = {}
    for card in cards or []:
        sid = str(card.get("signal_id") or "").strip()
        if sid and sid not in cards_by_id:
            cards_by_id[sid] = card

    out: list[dict] = []
    for ins in insights:
        merged = dict(ins)
        card = cards_by_id.get(str(ins.get("theme_id") or "").strip())
        if card:
            title = (card.get("title") or "").strip()
            if title:
                merged["title"] = title
            if not merged.get("tag"):
                merged["tag"] = tag_for_skill_type(card.get("type"))
            merged["_card"] = {
                "type": card.get("type"),
                "accent": card.get("accent")
                or accent_for_skill_type(card.get("type")),
                "title": card.get("title"),
                "body": card.get("body"),
                "sources": card.get("sources") or [],
                "ctas": card.get("ctas") or [],
                "signal_id": card.get("signal_id"),
            }
        out.append(merged)
    return out
