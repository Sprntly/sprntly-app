"""Catalog policy over the vendored PM skills: category, routability, labels.

`loader.py` is the *mechanics* (read a skill off disk, hash it, parse its
frontmatter). This module is the *policy*:
  - which category each skill belongs to (for UI grouping),
  - which skills the Q&A router may pick (`routable`),
  - a human label + slash trigger for the composer UI,
  - `build_manifest()` — the single source the LLM router menu and the
    `/v1/ask/skills` endpoint both read, computed from what's on disk.

Non-routable skills stay installed (and callable by name from agent code) but
are never offered to the router:
  - `business-context` is an ingestion/onboarding process, not a chat answer.
  - `fact-check` is an internal verification pass over other outputs.
"""
from __future__ import annotations

from functools import lru_cache

from app.skills.loader import get_skill, list_skills

# Skill id → display category. Sourced from the PM-Agent-Skills package layout
# (the skills install flat under backend/skills/, so category lives here).
SKILL_CATEGORY: dict[str, str] = {
    "prd-author": "Documentation & Specification",
    "prd-critique": "Documentation & Specification",
    "implementation-spec": "Documentation & Specification",
    "product-one-pager": "Documentation & Specification",
    "tech-spec": "Documentation & Specification",
    "test-scenario-builder": "Documentation & Specification",
    "user-stories": "Documentation & Specification",
    "assumption-risk-map": "Discovery & Research",
    "business-context": "Discovery & Research",
    "continuous-discovery": "Discovery & Research",
    "evidence-brief": "Discovery & Research",
    "interview-guide": "Discovery & Research",
    "interview-synthesis": "Discovery & Research",
    "jobs-to-be-done": "Discovery & Research",
    "persona-segment": "Discovery & Research",
    "problem-framing": "Discovery & Research",
    "survey-design": "Discovery & Research",
    "backlog-triage": "Prioritization & Decision",
    "decision-by-traffic-lights": "Prioritization & Decision",
    "decision-memo": "Prioritization & Decision",
    "pre-mortem": "Prioritization & Decision",
    "prioritize": "Prioritization & Decision",
    "red-team-review": "Prioritization & Decision",
    "competitive-intelligence-review": "Strategy & Vision",
    "growth-vectors": "Strategy & Vision",
    "lean-canvas": "Strategy & Vision",
    "market-structure": "Strategy & Vision",
    "okr-nct": "Strategy & Vision",
    "positioning": "Strategy & Vision",
    "product-strategy-stack": "Strategy & Vision",
    "product-vision": "Strategy & Vision",
    "roadmap": "Strategy & Vision",
    "analytics-instrumentation": "Metrics, Experimentation & Growth",
    "experiment-design": "Metrics, Experimentation & Growth",
    "experiment-readout": "Metrics, Experimentation & Growth",
    "funnel-activation": "Metrics, Experimentation & Growth",
    "growth-loop": "Metrics, Experimentation & Growth",
    "pricing-packaging": "Metrics, Experimentation & Growth",
    "product-market-fit": "Metrics, Experimentation & Growth",
    "retention-churn": "Metrics, Experimentation & Growth",
    "saas-metrics-diagnosis": "Metrics, Experimentation & Growth",
    "dependency-risk-track": "Delivery & Operations",
    "incident-runbook": "Delivery & Operations",
    "launch-gtm": "Delivery & Operations",
    "release-notes": "Delivery & Operations",
    "retrospective": "Delivery & Operations",
    "scope-slicing": "Delivery & Operations",
    "status-report": "Delivery & Operations",
    "story-mapping": "Delivery & Operations",
    "tech-discovery-docs": "Delivery & Operations",
    "working-backwards": "Delivery & Operations",
    "customer-comms": "Stakeholder & Communication",
    "exec-narrative": "Stakeholder & Communication",
    "feedback-synthesis": "Stakeholder & Communication",
    "negotiation-prep": "Stakeholder & Communication",
    "stakeholder-map": "Stakeholder & Communication",
    "stakeholder-update": "Stakeholder & Communication",
    "third-party-feedback": "Stakeholder & Communication",
    "fact-check": "Verification",
    "weekly-brief": "Stakeholder & Communication",
}

# Skills the Q&A router must never select (still installed + callable by name).
#   - business-context is an ingestion/onboarding process, not a chat answer.
#   - fact-check is an internal verification pass over other outputs.
#   - weekly-brief is the synthesis-agent's brief composer (bound by name from
#     app/synthesis/agent.py); it composes the weekly brief from already-computed
#     signals, it is not something the Q&A router should pick for a chat turn.
#   - evidence-brief is the Evidence Page method, bound by name from
#     app/evidence_kg.py; it synthesizes a single brief insight's KG signal
#     trail into the provenance doc, not a chat answer.
NON_ROUTABLE: frozenset[str] = frozenset(
    {"business-context", "fact-check", "weekly-brief", "evidence-brief"}
)

# Expensive skills that trip the confirm gate on large scope (see qa_agent).
COST_GATED: frozenset[str] = frozenset({"competitive-intelligence-review"})

# Acronyms to upper-case when humanising an id into a display label.
_ACRONYMS = {
    "prd", "okr", "nct", "gtm", "rice", "saas", "cir", "jtbd", "ice",
    "wsjf", "pmf", "nps", "rag", "ab", "kpi", "ui", "ux",
}


def humanize_label(skill_id: str) -> str:
    """`prd-author` → `PRD author`, `okr-nct` → `OKR NCT`, `roadmap` → `Roadmap`."""
    words = skill_id.split("-")
    out = []
    for i, w in enumerate(words):
        if w in _ACRONYMS:
            out.append(w.upper())
        elif i == 0:
            out.append(w.capitalize())
        else:
            out.append(w)
    return " ".join(out)


@lru_cache(maxsize=1)
def build_manifest() -> list[dict]:
    """The full installed-skill catalog, computed from disk. One entry per
    skill: id, label, category, description (frontmatter), has_scripts,
    routable, trigger. Sorted by id for stable output.

    Cached in-process; skills are vendored and don't change at runtime.
    """
    out: list[dict] = []
    for skill_id in list_skills():
        spec = get_skill(skill_id)
        out.append(
            {
                "id": skill_id,
                "label": humanize_label(skill_id),
                "category": SKILL_CATEGORY.get(skill_id, "Uncategorized"),
                "description": spec.description,
                "has_scripts": spec.has_scripts,
                "routable": skill_id not in NON_ROUTABLE,
                "trigger": f"/{skill_id}",
            }
        )
    return out


def routable_manifest() -> list[dict]:
    """Manifest filtered to the skills the router may pick."""
    return [s for s in build_manifest() if s["routable"]]
