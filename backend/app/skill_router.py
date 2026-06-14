"""Intent detection + skill routing for the Ask endpoint.

Given a user question, determines which skill (if any) should handle it.
Falls back to general Ask (corpus + KG) when no skill matches.

The router uses keyword matching first (fast, no LLM call), then an optional
LLM classifier for ambiguous queries. The keyword rules are deliberately
broad — false positives are cheap (the skill produces a structured answer),
false negatives are expensive (the user gets a generic response when a
specialized one was available).
"""
from __future__ import annotations

import logging
import re
from dataclasses import dataclass

logger = logging.getLogger(__name__)


@dataclass
class SkillMatch:
    skill_id: str
    confidence: float  # 0..1
    action: str        # human-readable label for the frontend


# Keyword patterns → skill mapping. Order matters: first match wins.
# Each entry: (compiled regex, skill_id, action_label, base_confidence)
_RULES: list[tuple[re.Pattern, str, str, float]] = [
    # PRD generation
    (re.compile(r"\b(generate|create|write|draft)\b.{0,20}\bprd\b", re.I),
     "prd-author", "Generate PRD", 0.95),
    (re.compile(r"\bprd\b.{0,20}\b(for|about|from)\b", re.I),
     "prd-author", "Generate PRD", 0.90),

    # Prioritization
    (re.compile(r"\b(prioriti[sz]e|rank|rice|wsjf|ice\s+score|moscow)\b", re.I),
     "prioritize", "Prioritize ideas", 0.90),
    (re.compile(r"\b(re-?prioriti[sz]e|re-?rank|re-?sequence)\b", re.I),
     "prioritize", "Re-prioritize backlog", 0.90),

    # User stories / tickets
    (re.compile(r"\b(create|generate|write)\b.{0,20}\b(ticket|story|stories|task)\b", re.I),
     "user-stories", "Generate user stories", 0.90),
    (re.compile(r"\b(user\s+stor|acceptance\s+criteria|ac\s+for)\b", re.I),
     "user-stories", "Generate user stories", 0.85),

    # Backlog triage
    (re.compile(r"\b(triage|clean\s*up|dedupe|duplicate).{0,20}\bbacklog\b", re.I),
     "backlog-triage", "Triage backlog", 0.85),

    # Decision memo
    (re.compile(r"\b(decision|build\s+vs?\s+buy|pivot|persevere|trade-?off)\b", re.I),
     "decision-memo", "Draft decision memo", 0.80),

    # Feedback synthesis
    (re.compile(r"\b(feedback|nps|csat|survey|sentiment).{0,20}\b(synthe|analyz|review|summary)\b", re.I),
     "feedback-synthesis", "Synthesize feedback", 0.85),
    (re.compile(r"\b(synthe|analyz|review).{0,20}\b(feedback|nps|csat|survey)\b", re.I),
     "feedback-synthesis", "Synthesize feedback", 0.85),

    # Third-party feedback (customer reviews, tickets)
    (re.compile(r"\b(customer|support).{0,20}\b(ticket|review|complaint|issue)\b", re.I),
     "third-party-feedback", "Analyze customer feedback", 0.80),

    # Competitive intelligence
    (re.compile(r"\b(competit|competitor|competitive\s+analysis|market\s+position)\b", re.I),
     "competitive-intelligence-review", "Competitive analysis", 0.85),

    # Incident runbook
    (re.compile(r"\b(incident|runbook|post-?mortem|sev-?\d|on-?call|outage)\b", re.I),
     "incident-runbook", "Generate incident runbook", 0.80),

    # Fact check
    (re.compile(r"\b(fact.?check|verify|is\s+it\s+true|source.?check)\b", re.I),
     "fact-check", "Fact-check claims", 0.85),

    # Prototype
    (re.compile(r"\b(prototype|generate\s+prototype|design\s+prototype)\b", re.I),
     "prd-author", "Generate prototype", 0.80),

    # Evidence / deep dive
    (re.compile(r"\b(evidence|deep\s*dive|root\s*cause|investigate)\b", re.I),
     "feedback-synthesis", "Deep dive analysis", 0.70),
]


def detect_intent(question: str) -> SkillMatch | None:
    """Match a user question to a skill via keyword rules.

    Returns the best SkillMatch, or None if no skill matches (→ general Ask).
    """
    for pattern, skill_id, action, confidence in _RULES:
        if pattern.search(question):
            return SkillMatch(skill_id=skill_id, confidence=confidence, action=action)
    return None


def list_available_skills() -> list[dict]:
    """Return the routable skills for the chat composer UI, grouped-ready.

    Computed from the vendored catalog (`backend/skills/`) — not a hand-list —
    so installing a skill folder surfaces it automatically. Non-routable skills
    (business-context, fact-check) are excluded; the UI shape stays
    {id, label, trigger, description, category}.
    """
    from app.skills.catalog import routable_manifest

    return [
        {
            "id": s["id"],
            "label": s["label"],
            "trigger": s["trigger"],
            "description": s["description"],
            "category": s["category"],
        }
        for s in routable_manifest()
    ]
