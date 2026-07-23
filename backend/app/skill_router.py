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
    # PRD generation. Verb + noun lists mirror the web command rule
    # (BriefChat.isPrdCommand) — "give me a prd for X" was a real user miss
    # under the old generate/create/write/draft-only list, and users also say
    # "product brief" / "product spec(ification)" / "product requirements
    # document" for the same artifact. Gap widened to 40 chars for multi-word
    # fillers ("put together a quick one-page prd").
    (re.compile(
        r"\b(generate|create|write|draft|make|build|prepare|produce|compose"
        r"|develop|author|give|need|want|put\s+together)\b.{0,40}"
        r"\b(prd|product\s+requirements?\s+doc(?:ument)?|product\s+brief"
        r"|product\s+spec(?:ification)?)s?\b", re.I),
     "prd-author", "Generate PRD", 0.95),
    # "spec this/it out (for X)" — same command, no artifact noun.
    (re.compile(r"\bspec\s+(this|that|it)\s+out\b", re.I),
     "prd-author", "Generate PRD", 0.90),
    (re.compile(r"\bprd\b.{0,20}\b(for|about|from)\b", re.I),
     "prd-author", "Generate PRD", 0.90),

    # Prioritization
    (re.compile(r"\b(prioriti[sz]e|rank|rice|wsjf|ice\s+score|moscow)\b", re.I),
     "prioritize", "Prioritize ideas", 0.90),
    (re.compile(r"\b(re-?prioriti[sz]e|re-?rank|re-?sequence)\b", re.I),
     "prioritize", "Re-prioritize ideas", 0.90),

    # User stories / tickets
    (re.compile(r"\b(create|generate|write)\b.{0,20}\b(ticket|story|stories|task)\b", re.I),
     "user-stories", "Generate user stories", 0.90),
    (re.compile(r"\b(user\s+stor|acceptance\s+criteria|ac\s+for)\b", re.I),
     "user-stories", "Generate user stories", 0.85),

    # Ideation prioritize ("backlog" kept as a chat alias for the old name)
    (re.compile(r"\b(triage|clean\s*up|dedupe|duplicate|prioriti[sz]e).{0,20}\b(ideation|ideas|backlog)\b", re.I),
     "ideation-prioritize", "Prioritize ideation", 0.85),

    # Decision memo
    (re.compile(r"\b(decision|build\s+vs?\s+buy|pivot|persevere|trade-?off)\b", re.I),
     "decision-memo", "Draft decision memo", 0.80),

    # Interview synthesis (qualitative research: 1:1s, focus groups, usability,
    # win/loss & churn-exit interviews → themes). Before feedback-synthesis; the
    # two share no keywords.
    (re.compile(r"\b(synthes\w*|analyz\w*|theme).{0,25}\b(interviews?|user\s+research|usability\s+(?:test|session)s?|focus\s+groups?|roundtables?)\b", re.I),
     "interview-synthesis", "Synthesize interviews", 0.85),
    (re.compile(r"\b(interviews?|usability\s+(?:test|session)s?|focus\s+groups?|user\s+research|win[\s/-]?loss|churn[\s-]?exit).{0,30}\b(synthes|analyz|theme|learn|insight|takeaway)\b", re.I),
     "interview-synthesis", "Synthesize interviews", 0.85),
    (re.compile(r"\binterview\s+(notes?|transcripts?)\b", re.I),
     "interview-synthesis", "Synthesize interviews", 0.80),
    (re.compile(r"\bwhat.{0,20}\b(learn|hear|find).{0,25}\b(call|interview|session|conversation)s?\b", re.I),
     "interview-synthesis", "Synthesize interviews", 0.80),

    # Feedback synthesis (quick thematic pass over a pile of feedback)
    (re.compile(r"\b(feedback|nps|csat|survey|sentiment).{0,20}\b(synthe|analyz|review|summary)\b", re.I),
     "feedback-synthesis", "Synthesize feedback", 0.85),
    (re.compile(r"\b(synthe|analyz|review).{0,20}\b(feedback|nps|csat|survey)\b", re.I),
     "feedback-synthesis", "Synthesize feedback", 0.85),

    # Public feedback report (external reviews & social: App Store, Google Play,
    # Reddit, G2, X; "what are people saying about us online")
    (re.compile(r"\b(review\s+mining|online\s+reputation|public\s+sentiment|app[\s-]?store|google\s+play|trustpilot|capterra|\bg2\b|reddit)\b", re.I),
     "public-feedback-report", "Public feedback report", 0.85),
    (re.compile(r"\bwhat.{0,25}\b(saying|people\s+say).{0,15}\babout\s+us\b", re.I),
     "public-feedback-report", "Public feedback report", 0.85),

    # Voice of customer — call recordings & transcripts (Fireflies/Gong/Zoom).
    # Summarizing what customers said on CSM/sales/discovery calls and the
    # feedback in those recordings. Distinct from interview-synthesis's
    # "what did we learn from calls" (thematic research) — this is the
    # first-party VoC corpus pass, so it wins recording/summary phrasings.
    (re.compile(r"\b(fireflies|gong|otter|zoom\s+recordings?|call\s+recordings?|recorded\s+calls?|meeting\s+recordings?|call\s+transcripts?)\b", re.I),
     "voice-of-customer-report", "Voice of customer report", 0.85),
    (re.compile(r"\b(summari[sz]e|summary\s+of|feedback\s+(?:from|on|in|across)|takeaways?\s+from)\b.{0,30}\bcalls?\b", re.I),
     "voice-of-customer-report", "Voice of customer report", 0.80),
    (re.compile(r"\b(voice\s+of\s+customer|voc\s+report)\b", re.I),
     "voice-of-customer-report", "Voice of customer report", 0.85),

    # Voice of customer (curated/first-party: support tickets, complaints, reviews)
    (re.compile(r"\b(customer|support).{0,20}\b(ticket|review|complaint|issue)s?\b", re.I),
     "voice-of-customer-report", "Analyze customer feedback", 0.80),

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


# ── Call-digest intent ──────────────────────────────────────────────────────
# "summarize the customer calls from last week", "recap this week's meetings",
# "what did we hear on our sales calls". This is NOT a plain skill route: it
# triggers an on-demand fetch of every call in a time window from a connected
# transcript source (Fireflies), then runs voice-of-customer-report over the
# complete corpus. qa_agent checks this BEFORE the generic skill router, because
# the generic rules would otherwise misroute it (e.g. "what did we hear on calls"
# → interview-synthesis) and answer from the lossy KG instead of live calls.
_CALL_NOUN = r"(?:customer|sales|user|client|cx|csm|discovery|success|support)?\s*(?:calls?|meetings?|stand-?ups?|qbrs?|check-?ins?|syncs?)"
_DIGEST_VERB = r"(?:summari[sz]e|recap|digest|rundown|round-?up|catch me up|brief me|overview|go over|run through|what (?:happened|did we (?:hear|learn|discuss|talk about))|themes?|feedback|voice of(?: the)? customer|takeaways?)"
_CALL_DIGEST_RULES: list[re.Pattern] = [
    # verb ... noun  ("summarize all the customer calls", "recap this week's meetings")
    re.compile(_DIGEST_VERB + r".{0,40}\b" + _CALL_NOUN, re.I),
    # noun ... verb  ("customer calls — what did we hear", "this week's meetings recap")
    re.compile(_CALL_NOUN + r".{0,40}\b" + _DIGEST_VERB, re.I),
]


def is_call_digest(question: str) -> bool:
    """True if the question asks to summarize/recap customer calls or meetings —
    the trigger for the on-demand call-digest path (see app/call_digest.py)."""
    return any(p.search(question) for p in _CALL_DIGEST_RULES)


# Bare "voice of customer" / "VoC report" asks — no call-noun, so is_call_digest
# misses them. qa_agent diverts these to the live call digest too, but only when
# a call source is connected (else they fall to the skill's what-to-connect
# guidance). Mirrors the router's VoC rule at line ~91.
_VOC_REPORT_RULE = re.compile(r"\b(voice\s+of\s+customer|voc\s+report)\b", re.I)

# "Summary of feedback from recent customer conversations"-style asks are VoC
# reports by intent, but carry neither the literal "voice of customer" nor a
# call-noun — without this rule they fall to the haiku router, which sends them
# to a synthesis/DS answer instead of the pinned VoC report (user-reported
# misroute). Requires BOTH a feedback word and a customer-conversation noun, in
# either order, so plain "customer conversations" questions still route freely.
_VOC_FEEDBACK_CONVO_RULE = re.compile(
    r"\bfeedback\b.{0,80}\b(?:customer|user|client)\s+(?:conversations?|discussions?)\b"
    r"|\b(?:customer|user|client)\s+(?:conversations?|discussions?)\b.{0,80}\bfeedback\b",
    re.I | re.S,
)


def is_voc_report_request(question: str) -> bool:
    """True for a bare 'voice of customer' / 'VoC report' request, or a
    feedback-from-customer-conversations phrasing. Distinct from is_call_digest
    (which needs a call-noun); used by qa_agent to route these to the live call
    digest when a call source is connected."""
    return bool(
        _VOC_REPORT_RULE.search(question) or _VOC_FEEDBACK_CONVO_RULE.search(question)
    )


# ── Data-analysis intent (DS agent) ─────────────────────────────────────────
# "analyze my data", "what does our usage data show", "run a data analysis" →
# the deterministic DS engine over the company's uploaded CSV/Excel exports
# (app/ds/chat_analysis.py). Like call-digest, qa_agent checks this BEFORE the
# generic skill router: the generic rules would misroute these to a synthesis
# skill and answer from the KG instead of actually computing over the data.
_DATA_NOUN = r"(?:data(?:set)?s?|csvs?|spreadsheets?|analytics|product\s+usage|usage\s+data|metrics\s+data|export(?:ed)?\s+(?:data|files?)|exports?)"
_ANALYZE_VERB = r"(?:analy[sz]e|analysis|dig\s+into|crunch|mine|explore|profile|run\s+the\s+numbers\s+on)"
_DATA_ANALYSIS_RULES: list[re.Pattern] = [
    # verb ... noun ("analyze my product data", "crunch the exported CSVs")
    re.compile(r"\b" + _ANALYZE_VERB + r"\b.{0,40}\b" + _DATA_NOUN + r"\b", re.I),
    # noun ... verb/insight ("my usage data — any insights?")
    re.compile(r"\b" + _DATA_NOUN + r"\b.{0,40}\b(?:analy[sz]e|analysis|insights?|patterns?|findings?|anomal(?:y|ies))\b", re.I),
    # insight ... noun ("any patterns in our dataset?", "insights from the CSVs")
    re.compile(r"\b(?:insights?|patterns?|findings?|anomal(?:y|ies))\b.{0,40}\b" + _DATA_NOUN + r"\b", re.I),
    # "what does the data say/show/tell us"
    re.compile(r"\bwhat\b.{0,30}\b(?:data|numbers|metrics)\b.{0,25}\b(?:say|show|tell|reveal)", re.I),
    # explicit ask for the DS agent / data-science pass
    re.compile(r"\b(?:data[\s-]science|ds)\s+(?:agent|analysis|report)\b", re.I),
]
# These asks belong to the synthesis/VoC/interview skills even when they contain
# a data-noun ("analyze the survey data") — qualitative corpora, not tabular
# exports. Presence of any of them vetoes the DS route.
_DATA_ANALYSIS_VETO = re.compile(
    r"\b(?:interviews?|feedback|nps|csat|surveys?|reviews?|calls?|meetings?|transcripts?|tickets?|complaints?)\b",
    re.I,
)


def is_data_analysis_request(question: str) -> bool:
    """True if the question asks to analyze the company's uploaded tabular data —
    the trigger for the DS-engine path (see app/ds/chat_analysis.py)."""
    if _DATA_ANALYSIS_VETO.search(question):
        return False
    return any(p.search(question) for p in _DATA_ANALYSIS_RULES)


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
