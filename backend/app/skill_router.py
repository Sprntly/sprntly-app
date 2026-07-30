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
    (re.compile(r"\b(review\s+mining|online\s+reputation|public\s+sentiment|public\s+feedback|public\s+standings?|app[\s-]?store|google\s+play|trustpilot|capterra|\bg2\b|reddit)\b", re.I),
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


# "Top customer feedback" / "summarize customer feedback" asks are VoC by
# intent too — they carry "feedback" but neither a call-noun nor a
# conversation-noun, so both rules above miss them and they fell to the generic
# answer path (user-reported misroute: "What is the top customer feedback"
# answered as a generic finding instead of the pinned VoC report). Requires an
# intent word near "customer|user|client feedback" in either order, so
# incidental mentions ("built this from customer feedback") don't divert.
# Widening is safe: qa_agent only reroutes when has_call_source() — companies
# with no call/voice-upload data keep the generic path.
_VOC_CUSTOMER_FEEDBACK_RULE = re.compile(
    r"\b(?:top|summar[iy]\w*|recent|latest|key|main|biggest|common)\b"
    r".{0,40}\b(?:customer|user|client)\s+feedback\b"
    r"|\b(?:customer|user|client)\s+feedback\b.{0,40}"
    r"\b(?:summar[iy]\w*|themes?|report|top|takeaways?)\b",
    re.I | re.S,
)


# Superlative-complaint asks — "what is the number 1 user complaint from
# today's customer conversations?", "top customer complaint this week",
# "biggest pain point customers raised". They carry a ranking word + a
# customer-noun + a complaint-noun but often neither "feedback" nor a literal
# call-noun, so every rule above misses them (user-reported 2026-07-27). The
# three-way requirement keeps precision: a bare "top complaints?" or an
# incidental "customers raised an issue" doesn't divert.
_VOC_COMPLAINT_RULE = re.compile(
    r"(?:\b(?:top|number\s*(?:one|1)|no\.\s*1|biggest|main|most\s+common|"
    r"most\s+frequent|loudest|worst)\b|#\s*1\b)"
    r".{0,50}\b(?:customer|user|client)s?\b"
    r".{0,50}\b(?:complain\w*|pain\s*points?|issues?|problems?|frustrations?|gripes?)"
    r"|\b(?:customer|user|client)s?\b.{0,30}"
    r"\b(?:complain\w*|pain\s*points?|frustrations?|gripes?)\b.{0,50}"
    r"(?:\b(?:top|number\s*(?:one|1)|biggest|main|most\s+common|ranked)\b|#\s*1\b)"
    r"|(?:\b(?:top|number\s*(?:one|1)|biggest|main|most\s+common|loudest|worst)\b|#\s*1\b)"
    r".{0,30}\b(?:complain\w*|pain\s*points?|frustrations?|gripes?)\b"
    r".{0,40}\b(?:customer|user|client)s?\b",
    re.I | re.S,
)


def is_voc_report_request(question: str) -> bool:
    """True for a bare 'voice of customer' / 'VoC report' request, a
    feedback-from-customer-conversations phrasing, or a top/summarize-customer-
    feedback phrasing. Distinct from is_call_digest (which needs a call-noun);
    used by qa_agent to route these to the live call digest when a call source
    is connected."""
    return bool(
        _VOC_COMPLAINT_RULE.search(question)
        or _VOC_REPORT_RULE.search(question)
        or _VOC_FEEDBACK_CONVO_RULE.search(question)
        or _VOC_CUSTOMER_FEEDBACK_RULE.search(question)
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


# ── Jira-lookup intent (live project-management reads) ───────────────────────
# "what's the status of PROJ-142", "summarize the checkout epic in Jira", "which
# tickets are open on the billing board". Triggers an on-demand LIVE read from a
# connected Jira (app/jira_lookup.py) instead of answering from the periodic,
# comment-less KG snapshot. Like call-digest, qa_agent checks this BEFORE the
# generic router, which would otherwise answer from the stale KG.
#
# A Jira issue key ("PROJ-142"): 2–10 leading upper-alnum chars, a hyphen, then
# digits. Case-SENSITIVE so lowercase false friends ("covid-19", "utf-8") don't
# match; real keys are uppercase.
_JIRA_ISSUE_KEY = re.compile(r"\b[A-Z][A-Z0-9]{1,9}-\d+\b")
_JIRA_WORD = re.compile(r"\bjira\b", re.I)
_JIRA_PM_NOUN = re.compile(
    r"\b(ticket|issue|epic|story|stories|bug|task|sub-?task|sprint|board|backlog)s?\b",
    re.I,
)
_JIRA_LOOKUP_VERB = re.compile(
    r"\b(look\s*up|pull\s*up|show|find|get|fetch|open|status\s+of|"
    r"details?\s+(?:of|for|on)|what'?s?\s+(?:the\s+)?status)\b",
    re.I,
)
# This path is READ-ONLY. A create/generate/push phrasing belongs to the
# user-stories skill or the Jira push flow, not here — veto it so those still
# route normally even when they mention Jira. Deliberately excludes "update" /
# "edit": "status update on PROJ-1" / "latest update" are READS, and the genuine
# write commands ("update PROJ-1 to done") carry no PM-noun/lookup-verb, so they
# never trigger a positive match anyway.
_JIRA_LOOKUP_VETO = re.compile(
    r"\b(create|generate|write|draft|make|build|author|produce|compose|push|"
    r"sync|delete)\b",
    re.I,
)
# CHANGING an issue that already exists is now this path's job too — the skill
# can propose an edit, which the user confirms before anything is written (see
# jira_lookup._PROPOSE_TOOL). So the mutation verbs came OUT of the veto above,
# which used to send "move it to In Review" to a skill that could only talk
# about it.
#
# Creation stays vetoed. "create a ticket for the login bug" is the user-stories
# skill's job, and "push these stories to jira" is the push flow's — neither is a
# change to an issue that exists. `delete` stays vetoed because nothing here
# implements it, and a confident-sounding refusal beats a silent no-op.
_JIRA_WRITE_VERB = re.compile(
    r"\b(update|set|change|edit|rename|move|transition|assign|unassign|"
    r"re-?open|close|resolve|add\s+a\s+comment|comment\s+on)\b",
    re.I,
)


# A follow-up INSIDE an active tracker thread rarely repeats "jira", an issue
# key, or even a PM noun. It either refines the search ("get all in to-do
# status", "only the PROJ ones") or points back at what the thread already put
# on screen ("can you get me all the details about it?"). Judging that message
# on its own words alone is what used to dead-end a live tracker thread at the
# scope gate, so two signals gate the sticky route instead:
#   1. the thread IS a tracker thread — _in_tracker_thread, which reads the
#      user's OWN earlier questions as well as the assistant's answers, and
#   2. the current message CONTINUES it — _continues_tracker_thread.
# A genuine pivot ("prioritize these features", "what's our churn rate?") trips
# the pivot veto and falls through to normal routing.
_JIRA_THREAD_MARKER = re.compile(r"\bjira\b|view in jira|issue key|workflow status", re.I)
_JIRA_FILTER = re.compile(
    r"\b(to[\s-]?do|in[\s-]?progress|in\s+review|done|open|closed|blocked|"
    r"backlog|status|project|board|sprint|epic|tickets?|issues?|bugs?|"
    r"tasks?|stor(?:y|ies)|assigned|assignee|priority|label)\b",
    re.I,
)
# How far back a tracker thread stays "active" — 8 turns ≈ 4 exchanges, enough
# that a couple of follow-ups don't age the original "get me ticket…" out.
_TRACKER_THREAD_WINDOW = 8
# Pointing back at what the thread already produced, instead of naming it again.
_TRACKER_ANAPHORA = re.compile(
    r"\b(it|its|it'?s|them|they|those|these|"
    r"th(?:at|is)\s+(?:one|ticket|issue|epic|stor(?:y|ies)|bug|task)|"
    r"the\s+(?:one|first|second|last|same|other)|the\s+above)\b"
    # A BARE "this"/"that" standing in for the issue on screen. The alternatives
    # above all require a following noun ("that ticket"), which missed the most
    # natural follow-up of all — "give me full details on this" — and dropped it
    # to the generic agent, which answered that it had no details for a ticket
    # the previous turn had just fetched.
    # Restricted to the two positions where a bare this/that is genuinely
    # referential: as the object of a preposition, or ending the message. A bare
    # this/that anywhere would swallow "I think that we should ship".
    r"|\b(?:on|about|for|of|with|from|into)\s+th(?:is|at)\b"
    r"|\bth(?:is|at)\s*[?.!]*\s*$",
    re.I,
)
# What such a follow-up asks FOR — attributes of an issue already on screen.
_TRACKER_DETAIL = re.compile(
    r"\b(details?|description|summar(?:y|ise|ize)|acceptance\s+criteria|"
    r"comments?|more|everything|expand|elaborate|who|assignee|assigned|"
    r"status|priority|labels?|due|link|url)\b",
    re.I,
)
# ...and what means the user has LEFT the tracker thread. Checked FIRST, so a
# pivot that happens to carry a pronoun ("prioritize these features") or a
# tracker-ish word ("what's the status of our roadmap?") routes normally.
_TRACKER_PIVOT = re.compile(
    r"\b(prioriti[sz]e|prioriti[sz]ation|roadmap|prd|brief|persona|churn|nps|"
    r"csat|retention|revenue|arr|mrr|pricing|competitors?|competitive|"
    r"prototype|surveys?|interviews?|calls?|meetings?|transcripts?|"
    r"strateg(?:y|ic)|okrs?|kpis?|metrics?)\b",
    re.I,
)


def _vetoed_as_creation(question: str) -> bool:
    """True when a create/push verb GOVERNS this request, rather than merely
    appearing in it.

    The veto exists to keep "create a ticket for X" and "push these stories to
    jira" out of the read path. Matching the verb anywhere in the sentence broke
    the moment a ticket's own TITLE contained one: "get me ticket about car build
    thread" was vetoed by the word "build" inside the title being searched for,
    so a plain lookup fell through to the scope gate and answered "I can only
    help with your product work".

    Position decides it. A creation verb before the PM noun is a command about
    what to make; after it, it is part of what is being looked for.
    """
    m_veto = _JIRA_LOOKUP_VETO.search(question)
    if m_veto is None:
        return False
    m_noun = _JIRA_PM_NOUN.search(question)
    # No PM noun at all ("generate a PRD", "draft it up") → still a creation
    # phrasing, still vetoed.
    return m_noun is None or m_veto.start() < m_noun.start()


def _stateless_tracker_lookup(question: str) -> bool:
    """The history-free half of is_jira_lookup — true when the message names a
    tracker read on its own words. Split out so thread detection can ask it of
    the user's earlier turns without recursing through the sticky branch."""
    if _vetoed_as_creation(question):
        return False
    has_key = bool(_JIRA_ISSUE_KEY.search(question))
    has_jira = bool(_JIRA_WORD.search(question))
    has_pm_noun = bool(_JIRA_PM_NOUN.search(question))
    has_verb = bool(_JIRA_LOOKUP_VERB.search(question))
    has_context = has_pm_noun or has_verb
    # Primary, tracker-agnostic trigger: a read verb on a PM noun — "get me
    # tickets", "show the open bugs", "find the checkout epic". No "jira" word or
    # key needed, so future trackers (Asana / ClickUp) route here the same way.
    if has_pm_noun and has_verb:
        return True
    # A change aimed at an issue that already exists — "update PROJ-1's due date",
    # "assign the login bug to Ada", "move DEV-88 to In Review". Requires a key or
    # a PM noun so a bare "update the roadmap" is not dragged in here.
    m_write = _JIRA_WRITE_VERB.search(question)
    if m_write and (has_key or has_pm_noun):
        m_key = _JIRA_ISSUE_KEY.search(question)
        # …and the verb must come BEFORE the issue it acts on. "we shipped in the
        # UTF-8 encoding update" trips both halves otherwise: UTF-8 looks exactly
        # like an issue key, and there "update" is a noun at the end of a
        # sentence, not a command.
        if not (m_key and m_write.start() > m_key.start()):
            return True
    # An explicit issue key with any lookup context (verb, PM noun, or "jira").
    if has_key and (has_context or has_jira):
        return True
    # "in jira" / "jira board" / "look up ... in jira" — jira + a PM-read context,
    # but never "jira" on its own (product mention).
    if has_jira and has_context:
        return True
    return False


def _in_tracker_thread(history: list[dict] | None) -> bool:
    """True when the recent conversation is an active tracker thread. Two kinds
    of evidence count, and either is enough:

    - the ASSISTANT side named Jira or carried an issue key (its clarifying
      question, or the issue it just fetched), and
    - the USER's own earlier questions — one of them was itself a tracker lookup
      ("get me ticket about cars"). This is what carries a thread whose answer
      happened to name no key ("no matching issues", a plain prose reply) into
      the next turn, and it's why the follow-up is read against every recent
      question the user asked, not just the one they just sent."""
    if not history:
        return False
    for turn in history[-_TRACKER_THREAD_WINDOW:]:
        content = turn.get("content") or ""
        if not content:
            continue
        if _JIRA_THREAD_MARKER.search(content) or _JIRA_ISSUE_KEY.search(content):
            return True
        if (turn.get("role") or "user") == "user" and _stateless_tracker_lookup(content):
            return True
    return False


# A bare acceptance of the assistant's OWN offer. The lookup routinely ends with
# "Would you like me to fetch the full details of this ticket?", and the natural
# reply is one word. That word carries no ticket, no verb, no pronoun and no
# filter — every other signal this module looks for is absent, so the message
# fell through to the generic agent, which then reported it had no details for a
# ticket the previous turn had just listed. Reported from a live thread.
_TRACKER_AFFIRMATIVE = re.compile(
    r"^\s*(?:yes|yeah|yep|yup|ya|sure|ok|okay|please|"
    r"yes\s+please|go\s+ahead|do\s+it|sounds\s+good|"
    r"that'?s?\s+right)\b[\s.!,]*$",
    re.I,
)


def _answers_tracker_question(question: str, history: list[dict] | None) -> bool:
    """True when this message is a bare "yes" accepting what the assistant just
    offered to do.

    Gated on the assistant having ACTUALLY asked something — the most recent
    assistant turn must end in a question mark. Without that gate a stray "ok"
    anywhere in a tracker thread would be treated as a fetch request; with it,
    the word is only meaningful because a question is sitting directly above it.
    """
    if not _TRACKER_AFFIRMATIVE.match(question or ""):
        return False
    for turn in reversed(history or []):
        if (turn.get("role") or "user") != "assistant":
            continue
        return (turn.get("content") or "").strip().endswith("?")
    return False


def _continues_tracker_thread(question: str) -> bool:
    """True when this message reads as a continuation of a tracker thread rather
    than a new subject — a filter refinement, or a pull for more of an issue the
    thread already surfaced ("all the details about it", "who's on that one")."""
    if _TRACKER_PIVOT.search(question):
        return False
    # A bare issue KEY inside a tracker thread — "how about KAN-1038", "and
    # KAN-4?". Statelessly a key needs company (a verb or PM noun) so a passing
    # mention like "the deploy for ABC-12 landed" doesn't hijack the chat; but
    # once the conversation IS about tickets, naming a key is unambiguous.
    if _JIRA_ISSUE_KEY.search(question):
        return True
    if _JIRA_FILTER.search(question):
        return True
    # Asking for an ATTRIBUTE of the issue on screen — "i want to see full
    # detail", "what's the description", "any comments?", "who's it assigned
    # to". These name what they want but not what it belongs to, because the
    # thread already established that. Safe without a pronoun: we are inside a
    # confirmed tracker thread and past the pivot veto, so a genuine subject
    # change ("what's our churn rate?") has already been rejected above.
    if _TRACKER_DETAIL.search(question):
        return True
    # Anaphora alone is too weak ("what does that mean for launch?"), so pair it
    # with an actual read: a lookup verb or a request for issue attributes.
    if _TRACKER_ANAPHORA.search(question) and (
        _JIRA_LOOKUP_VERB.search(question) or _TRACKER_DETAIL.search(question)
    ):
        return True
    # "move it to In Review", "set its due date to August 31" — a change pointed
    # at whatever the thread is already showing. The issue is named nowhere in
    # the message, so only the thread makes it resolvable.
    if _JIRA_WRITE_VERB.search(question):
        return True
    return False


def is_jira_lookup(question: str, history: list[dict] | None = None) -> bool:
    """True when the question asks to READ live tracker issues — a tracker read
    verb on a PM noun ("get me tickets", "show my epics"), an issue key with a
    lookup verb / PM noun, the word "jira" alongside such context, or a follow-up
    continuing an active tracker thread (using `history`). The PM noun ("ticket")
    — not the word "jira" — is the primary trigger, so the same path serves
    whichever tracker is connected (Jira today, Asana / ClickUp later); naming
    "jira" is just one more way in. Merely NAMING Jira (e.g. as one competitor in
    a CIR request) is not a lookup, so "jira" alone doesn't trigger. Vetoed for
    create/generate/push phrasings. The trigger for the on-demand lookup path
    (app/jira_lookup.py)."""
    if _stateless_tracker_lookup(question):
        return True
    if _vetoed_as_creation(question):
        return False
    if not _in_tracker_thread(history):
        return False
    # Either the message continues the thread on its own words, or it is a bare
    # "yes" to the assistant's own offer — which says nothing by itself and
    # everything in context.
    return (
        _continues_tracker_thread(question)
        or _answers_tracker_question(question, history)
    )


# ── Connector-lookup intent (live reads beyond the tracker) ──────────────────
#
# "check slack for what was said about the pricing change", "what changed in the
# repo this week", "which deals in hubspot mention onboarding" — questions whose
# answer lives in a connected TOOL, read live, rather than in the periodic KG
# snapshot the generic router answers from (app/connector_lookup/).
#
# The trigger is deliberately narrow: the question must NAME a source (or be a
# follow-up inside a thread that named one). Two reasons.
#   1. Anti-hallucination cuts both ways. Naming a source we can't read must
#      reach the honest "that isn't connectable yet" answer instead of a
#      KG-flavoured guess — so unsupported names (Zendesk, Gong, …) are matched
#      here on purpose, and the registry answers them without fetching anything.
#   2. False-positive routing is the biggest UX risk on this path: stealing
#      "what are customers complaining about" from the VoC/DS paths would be a
#      visible regression, and those interceptions run BEFORE this one in
#      qa_agent.answer precisely so ordering — not regex cleverness — decides.
# The plan's second, wider trigger (provider-specific NOUNS plus that provider
# being connected) needs the tenant's connection list, which this module has no
# access to by design; it is deliberately left for a follow-up.
#
# Unambiguous product names. A mention is enough — "in slack", "from hubspot",
# "the github repo".
_CONNECTOR_STRONG_NAMES: dict[str, re.Pattern] = {
    "slack": re.compile(r"\bslack\b", re.I),
    "jira": re.compile(r"\b(jira|atlassian)\b", re.I),
    "clickup": re.compile(r"\bclick\s?up\b", re.I),
    "github": re.compile(r"\bgit\s?hub\b", re.I),
    "hubspot": re.compile(r"\bhub\s?spot\b", re.I),
    "fireflies": re.compile(r"\bfireflies\b", re.I),
    "google_drive": re.compile(
        r"\b(google\s+drive|g\s?drive|my\s+drive|drive\s+(?:files?|docs?|folder)|"
        r"google\s+docs?)\b", re.I,
    ),
    "asana": re.compile(r"\basana\b", re.I),
    "zendesk": re.compile(r"\bzen\s?desk\b", re.I),
    "gong": re.compile(r"\bgong\b", re.I),
    "amplitude": re.compile(r"\bamplitude\b", re.I),
    "mixpanel": re.compile(r"\bmixpanel\b", re.I),
    "sprinklr": re.compile(r"\bsprinklr\b", re.I),
    "superset": re.compile(r"\bsuperset\b", re.I),
    "figma": re.compile(r"\bfigma\b", re.I),
    "intercom": re.compile(r"\bintercom\b", re.I),
    "gitlab": re.compile(r"\bgit\s?lab\b", re.I),
    "sentry": re.compile(r"\bsentry\b", re.I),
    "stripe": re.compile(r"\bstripe\b", re.I),
}
# Names that are ordinary English too, so they need a data-read context in the
# same message ("in linear", "the notion doc") — never "linear growth", "I have
# no notion of it".
_CONNECTOR_AMBIGUOUS_NAMES: dict[str, re.Pattern] = {
    "linear": re.compile(r"\blinear\b", re.I),
    "notion": re.compile(r"\bnotion\b", re.I),
}
_CONNECTOR_AMBIGUOUS_VETO = re.compile(
    r"\blinear\s+(?:growth|regression|scale|relationship|model|algebra|time)\b|"
    r"\bno\s+notion\b|\bnotion\s+(?:that|of\s+(?:a|an|the)?\s*\w+ness)\b",
    re.I,
)
# A read context: the tracker read verbs, plus the verbs people use for tools
# ("check", "search", "look in", "what did … say").
_CONNECTOR_READ_CONTEXT = re.compile(
    r"\b(check|search|look\s*(?:in|up|at)|read|see|list|what'?s?|which|who|"
    r"any|find|get|fetch|show|pull|summari[sz]e|"
    r"tickets?|issues?|docs?|documents?|pages?|messages?|channels?|threads?|"
    r"deals?|contacts?|companies|records?|calls?|meetings?|commits?|prs?|"
    r"pull\s+requests?|repos?|files?)\b",
    re.I,
)
# A Slack channel reference — "#general", "in #product-eng". Two-plus chars and
# a leading letter so "#1" or a markdown heading never counts.
_SLACK_CHANNEL_REF = re.compile(r"(?<![\w/])#[a-z][a-z0-9._-]{1,60}\b", re.I)
# We can READ. A command to WRITE to a tool ("post this in slack", "send a
# message to #general", "create a hubspot deal") is not a lookup: it falls
# through to normal routing, which explains what Sprntly does, instead of a read
# path implying it posted something.
#
# Anchored at the START of the message (optionally behind "please"/"can you"),
# because that is what makes it a COMMAND. Matching these verbs anywhere would
# swallow plain reads — "what did they share in slack", "did anyone post in
# #general" are questions about the past, not instructions.
_CONNECTOR_WRITE_VETO = re.compile(
    r"^\s*(?:please\s+|can\s+you\s+|could\s+you\s+|i\s+want\s+you\s+to\s+|"
    r"let'?s\s+|go\s+ahead\s+and\s+)*"
    r"(post|send|dm|notify|announce|publish|schedule|invite|upload|create|add|"
    r"delete|remove|archive|push|sync|share|reply|respond)\b",
    re.I,
)


# What a follow-up inside a connector thread asks FOR. The tracker's equivalent
# (_TRACKER_DETAIL) is issue-shaped ("assignee", "priority"); these are the nouns
# of a conversation or a document — "what's the full thread", "any more context".
_CONNECTOR_FOLLOWUP_DETAIL = re.compile(
    r"\b(threads?|messages?|channels?|conversation|quotes?|verbatim|transcript|"
    r"full|rest|context|commits?|diff|files?)\b",
    re.I,
)


# NAMING a tool is not always asking to read it. A competitive-intelligence
# request lists products as SUBJECTS ("competitive analysis of Linear, Jira and
# Asana", "how does our roadmap compare to Jira?"), and the same veto guards
# "what are the alternatives to Zendesk". is_jira_lookup already refuses these
# for exactly this reason (see its own negative fixtures); the connector router
# has to refuse them too or it would steal every CIR that names a tool.
_CONNECTOR_MENTION_VETO = re.compile(
    r"\b(competitive|competitors?|competing|compare[ds]?|comparison|comparing|"
    r"versus|vs\.?|alternatives?|instead\s+of|migrat(?:e|ing|ion)|"
    r"market\s+(?:landscape|leaders?)|better\s+than|switch(?:ing)?\s+(?:to|from))\b",
    re.I,
)


def _named_connector_providers(text: str) -> set[str]:
    """Provider keys explicitly named in one message."""
    if _CONNECTOR_MENTION_VETO.search(text):
        return set()
    found: set[str] = set()
    for provider, pattern in _CONNECTOR_STRONG_NAMES.items():
        if pattern.search(text):
            found.add(provider)
    if _SLACK_CHANNEL_REF.search(text):
        found.add("slack")
    if not _CONNECTOR_AMBIGUOUS_VETO.search(text) and _CONNECTOR_READ_CONTEXT.search(text):
        for provider, pattern in _CONNECTOR_AMBIGUOUS_NAMES.items():
            if pattern.search(text):
                found.add(provider)
    return found


def is_connector_lookup(
    question: str, history: list[dict] | None = None
) -> set[str] | None:
    """The provider keys an ad-hoc connector lookup should cover, or None.

    A set (not a bool) because the answer needs to know WHICH sources to open —
    and because a question may name two ("check slack and jira for the pricing
    decision"). Includes providers we cannot read live: the registry turns those
    into an honest not-supported answer, which is the whole point of intercepting
    them here rather than letting the generic path guess.

    Returns None for write requests, creation phrasings, and anything that names
    no source at all.
    """
    q = question or ""
    if _CONNECTOR_WRITE_VETO.search(q) or _vetoed_as_creation(q):
        return None
    named = _named_connector_providers(q)
    if named:
        return named
    # Sticky thread: "who said that?" right after a Slack read names nothing at
    # all. Same two-signal shape as the tracker thread — the thread named a
    # source, and this message continues it rather than pivoting away.
    if not history or _TRACKER_PIVOT.search(q):
        return None
    if not (
        _TRACKER_ANAPHORA.search(q)
        or _TRACKER_DETAIL.search(q)
        or _CONNECTOR_FOLLOWUP_DETAIL.search(q)
    ):
        return None
    prior: set[str] = set()
    for turn in history[-_TRACKER_THREAD_WINDOW:]:
        content = turn.get("content") or ""
        if content:
            prior |= _named_connector_providers(content)
    return prior or None


def is_context_dependent_followup(question: str, history: list[dict] | None = None) -> bool:
    """True when the message only means something as a continuation of the
    thread — its subject lives in an earlier turn, not in its own words ("can you
    get me all the details about it?"). Judged in isolation, such a message looks
    topic-less, which is exactly what the scope gate mistakes for out-of-domain;
    qa_agent uses this to answer it in context instead of refusing."""
    if not history:
        return False
    return bool(_TRACKER_ANAPHORA.search(question))


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
