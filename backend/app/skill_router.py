"""Intent detection + PIPELINE routing for the Ask endpoint.

Given a user question, determines which dedicated PIPELINE (if any) should
handle it. Everything else falls back to the general Ask answer (corpus + KG) —
which is now the default, not the fallback.

WHAT THIS MODULE STOPPED DOING. It used to pick one of ~78 vendored `SKILL.md`
methods for a chat turn. That layer is gone: chat answers on the default model
with no method injected. What survives here is the small set of rules whose
match selects a piece of REAL MACHINERY the answer model cannot substitute for —
a live call fetch, a web-search sweep, a tracker read, the DS engine. Those are
capabilities, not prose styles, so keyword routing still earns its keep for
them.

The rules that only chose a METHOD were deleted, and two of them were live bugs:
a bare `\\bprototype\\b` and `\\bprd\\b …\\b(for|about|from)\\b` sent "did the
prototype ship last week?" and "what's in the PRD for onboarding?" to
`prd-author` — a long-output document generator — so an ordinary question came
back as a full PRD. Those rules were terminal (no LLM tier could override them
for a tenant with no uploaded skills), which is exactly why removing them is the
fix rather than a re-tune.

The rules are still deliberately broad within their narrow subject: a false
positive costs a pipeline run, a false negative costs the user an answer only
that pipeline can give.
"""
from __future__ import annotations

import logging
import re
from dataclasses import dataclass

# The same "this turn is a document, not prose" sniff the prompt-fold uses, so
# thread detection and history clamping agree on what a report turn is.
from app.prompt_history import looks_like_html

logger = logging.getLogger(__name__)


@dataclass
class SkillMatch:
    skill_id: str
    confidence: float  # 0..1
    action: str        # human-readable label for the frontend


# ── Competitive-intelligence report intent (narrowed) ────────────────────────
# The CIR skill is the heaviest thing we ship: a multi-minute staged web-research
# sweep. It must fast-path only when the user is asking for the REPORT, not
# merely mentioning competitors. Two ingredients, and a shape needs both:
#   * a competitor SUBJECT ("competitors", "competition", "competitive",
#     "rivals", "market"), and
#   * a report/comparison INTENT (report, analysis, review, scan, landscape,
#     benchmark, intelligence, "where do we stand", "how do we compare",
#     "what are they shipping").
_CIR_SUBJECT = r"(?:competitors?|competition|competitive|rivals?|market)"
# "market" is the loose one: it names our category as often as our rivals, so it
# is admitted only where the surrounding shape is unambiguous ("market
# landscape", "benchmark us against the market", "how do we compare to the
# market"). Shapes where a bare "the market" reads as a general question —
# "what is the market doing today?", "run a study on the market" — use the
# STRICT subject instead and defer to the haiku router.
_CIR_SUBJECT_STRICT = r"(?:competitors?|competition|competitive|rivals?)"
_CIR_REPORT_NOUN = (
    r"(?:report|analys[ie]s|review|scan|landscape|benchmark(?:ing|s)?|"
    r"intelligence|study|teardown|round-?up|deep[\s-]?dive|pulse|briefing)"
)


def _near(a: str, b: str, gap: int = 45) -> str:
    """Either order, within `gap` characters."""
    return rf"(?:{a}.{{0,{gap}}}{b}|{b}.{{0,{gap}}}{a})"


# Named sub-patterns, bound before the f-strings below: an f-string EXPRESSION
# cannot contain a backslash before Python 3.12, and CI runs 3.11.
_CIR_SUBJECT_B = rf"\b{_CIR_SUBJECT}\b"
_CIR_STANDING = r"\bwhere\s+do\s+we\s+stand\b"
_CIR_COMPARE = r"\bhow\s+do\s+we\s+(?:compare|stack\s+up|measure\s+up)\b"
_CIR_BENCHMARK = r"\bbenchmark\b"

_CIR_REPORT_RULE_SRC = (
    # "competitive intelligence", "competitor report", "competitive analysis",
    # "market landscape", "monthly competitor scan", "quarterly competitive
    # review", "competitor deep dive". Up to two filler words between the two.
    rf"\b{_CIR_SUBJECT}\s+(?:\w+\s+){{0,2}}{_CIR_REPORT_NOUN}\b"
    # "review of the competition", "teardown of our rivals". STRICT subject:
    # "run a study on the market" is a general research ask, not a report ask.
    rf"|\b{_CIR_REPORT_NOUN}\s+(?:of|on|for|across|against|vs\.?|versus)\s+"
    rf"(?:the\s+|our\s+)?(?:\w+\s+){{0,2}}{_CIR_SUBJECT_STRICT}\b"
    # "where do we stand vs competitors" / "vs the competition, where do we stand"
    rf"|{_near(_CIR_STANDING, _CIR_SUBJECT_B)}"
    # "how do we compare to the market" / "how do we stack up against rivals"
    rf"|{_near(_CIR_COMPARE, _CIR_SUBJECT_B)}"
    # "what are our competitors shipping/launching/doing/been up to". STRICT
    # subject: "what is the market doing today?" is a general question.
    rf"|\bwhat\s+(?:are|is|has|have)\b.{{0,30}}\b{_CIR_SUBJECT_STRICT}\b.{{0,30}}"
    r"\b(?:ship(?:ping|ped)?|launch(?:ing|ed)?|releas\w+|doing|building|"
    r"been\s+up\s+to|up\s+to)\b"
    # "benchmark us against the market"
    rf"|{_near(_CIR_BENCHMARK, _CIR_SUBJECT_B)}"
)

# Sibling strategy skills whose asks read competitor-ish but belong elsewhere,
# plus asks about the user's OWN uploaded data. Vetoing them keeps the fast-path
# honest instead of widening the regex further and then apologising for it.
#
# The uploaded-data veto exists because the DS engine's own trigger
# (`is_data_analysis_request`) requires an analyze-VERB, and "deep dive" is not
# one — so "do a deep dive on the market data I uploaded" fell past DS and was
# claimed by CIR's `market … deep dive` shape, buying a web sweep for a question
# about a spreadsheet. Anything naming the user's own data defers.
#
# TAM/SAM/SOM is matched CASE-SENSITIVELY via `(?-i:…)`: under re.I the `sam`
# alternative also matched the NAME Sam, so "competitive analysis of Sam's Club
# vs Costco" lost its fast path to a market-sizing veto.
_CIR_VETO = re.compile(
    r"\bmarket\s+structure\b|\bfive\s+forces\b|\bporter'?s\b"
    r"|\bmarket\s+siz\w+\b|(?-i:\b(?:TAM|SAM|SOM)\b)"
    r"|\bpositioning\s+statement\b|\bbattle\s?cards?\b"
    r"|\btraffic\s+lights?\b|\bbeachhead\b"
    # …the user's own data, not the public web.
    r"|\buploaded?\b|\b(?:my|our)\s+data\b|\bthe\s+data\b"
    r"|\b(?:csvs?|spreadsheets?|excel|xlsx?)\b",
    re.I,
)

# skill_id → veto pattern. When a rule matches but its veto also matches, the
# fast-path DEFERS (falls through to the remaining rules, then the haiku router)
# rather than claiming a question that belongs to a sibling skill.
_RULE_VETOES: dict[str, re.Pattern] = {
    "competitive-intelligence-review": _CIR_VETO,
}


def is_competitive_report_request(question: str) -> bool:
    """True when the question asks for the competitive-intelligence REPORT.

    The predicate behind the CIR fast-path rule, exported because the Slack
    surface needs the same judgement BEFORE running anything: a report ask has
    to be acknowledged with its duration up front, since the run itself takes
    minutes.
    """
    q = question or ""
    if _CIR_VETO.search(q):
        return False
    return bool(re.search(_CIR_REPORT_RULE_SRC, q, re.I))


# Keyword patterns → PIPELINE mapping. Order matters: first match wins.
# Each entry: (compiled regex, pipeline_id, action_label, base_confidence)
#
# Every id below names a dedicated module in `app/` that does something the
# answer model cannot do for itself — a paid web-search sweep, a live call
# fetch. That is the ONLY admission test now. Rules that merely picked a
# `SKILL.md` method (prd-author, prioritize, user-stories, ideation-prioritize,
# decision-memo, interview-synthesis, feedback-synthesis, incident-runbook,
# fact-check, sales-battlecard, "prototype", "deep dive") were deleted with the
# built-in skill layer: those questions now reach the ordinary answer, which is
# the point of the change, not a casualty of it.
#
# `sales-battlecard`'s rule is gone but the behaviour it protected is NOT: it
# sat above the CIR rule to stop "sell against Acme" / "build a battlecard"
# buying a multi-minute web sweep. That veto lives in `_CIR_VETO` (which matches
# `battle ?cards?`), which is where it always actually was — the rule ordering
# was belt-and-braces.
_RULES: list[tuple[re.Pattern, str, str, float]] = [
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

    # Deep company research — research OUR OWN company on the public web
    # (products, positioning, pricing, market, recent news) → KG signals.
    # Deliberately ABOVE the competitive-intelligence rule so "research our
    # market" is an inward ask.
    #
    # NB (routing narrowing, this stack): a bare "market position(ing)" no
    # longer falls through to CIR — the CIR rule below is report-intent only, so
    # a positioning question with no report noun goes to the haiku router (which
    # reads it as `positioning`). Nothing here depends on that fall-through; the
    # first-person guard below is what keeps these rules from hijacking outward
    # asks.
    #
    # Both patterns require a FIRST-PERSON possessive (our|my) — never "the".
    # "the" would hijack outward asks: "research the pricing of Salesforce",
    # "can you research the company Datadog", "research the positioning of our
    # top competitor" (which would have beaten CIR from up here), and "run deep
    # research on the market leaders". The noun list also omits "competitors",
    # so "research our competitors" falls through as well.
    (re.compile(r"\b(?:deep\s+)?research\b.{0,30}\b(our|my)\s+"
                r"(compan(?:y|ies)|product|market|pricing|positioning)\b", re.I),
     "company-research", "Deep company research", 0.85),
    (re.compile(r"\bwhat\s+do(?:es)?\s+(we|our\s+(?:company|product))\b.{0,20}"
                r"\b(offer|sell|charge)\b", re.I),
     "company-research", "Deep company research", 0.85),

    # Competitive intelligence — REPORT-INTENT shapes only.
    #
    # This rule used to be `\b(competit|competitor|competitive analysis|market
    # position)\b`, which meant ANY sentence containing the word "competitor"
    # fast-pathed into the heaviest skill we ship: "the PRD should mention our
    # competitors", "customers keep comparing us to a competitor", "who are our
    # competitors?" all bought a multi-minute web-research review nobody asked
    # for. Intent-first convention (#925): the regex tier is a zero-latency
    # fast-path for UNAMBIGUOUS report asks, and everything else is the haiku
    # router's job — phrasings live as test data in
    # tests/test_cir_routing_phrases.py, not as ever-growing regexes.
    (re.compile(_CIR_REPORT_RULE_SRC, re.I),
     "competitive-intelligence-review", "Competitive intelligence report", 0.85),
]


# The ids `_RULES` may emit, and the complete set of ids a CHAT TURN can be
# routed to that is not one of the company's own uploads.
#
# This is the contract that replaced "is it a vendored skill and not
# NON_ROUTABLE". Each id keys a dispatch branch in `qa_agent.answer` that hands
# the turn to a dedicated module, and each of those modules is reachable ONLY
# through one of these ids — so a name that drops out of here silently
# un-ships a capability rather than degrading it. `test_skill_router.py` pins
# the set against the rules and against qa_agent's dispatch.
#
# All four have rules above; the set is stated separately because `pinned_skill`
# (Slack's `/competitive` command) and the classifier can also name one without
# a rule firing.
PIPELINE_SKILLS: frozenset[str] = frozenset({
    "voice-of-customer-report",
    "public-feedback-report",
    "company-research",
    "competitive-intelligence-review",
})


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
      question the user asked, not just the one they just sent.

    A generated REPORT turn is neither. It is a document the assistant produced,
    persisted verbatim as its conversation turn, and it quotes whatever record
    ids its sources use — a VoC report cites "TKT-48766" (Zendesk) and
    "CALL-9875" (Talkdesk), both shaped exactly like a Jira key, and its Sources
    line may name Jira as one connector among several. Reading that as tracker
    evidence made every report thread a tracker thread, so the next follow-up
    that happened to say "more" / "details" / "expand" was answered by the
    tracker path with "no tracker is connected — connect Jira or ClickUp"
    instead of by the report. Reported live against a Voice of Customer report.
    A tracker thread is established by what was SAID, never by a document."""
    if not history:
        return False
    for turn in history[-_TRACKER_THREAD_WINDOW:]:
        content = turn.get("content") or ""
        if not content:
            continue
        if looks_like_html(content):
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


def _last_answer_was_report(history: list[dict] | None) -> bool:
    """True when the most recent assistant turn is a generated REPORT document.

    The sticky branches of the tracker and connector routers exist to carry an
    anaphoric follow-up ("more detail on that one") back to the source the
    thread was already reading. When the thing sitting directly above the
    follow-up is a REPORT, that inference is wrong: "explain more on
    recommendations point 1" is about the document, not a reason to re-read a
    tracker or a connector. Those paths answer such a question with "no tracker
    is connected" or "nothing found in Slack" — the reported bug — while the
    normal answer path can read the report itself.

    Only the LATEST answer counts. A report earlier in the thread must not stop
    a later, genuine tracker/connector thread from going sticky.
    """
    for turn in reversed(history or []):
        if (turn.get("role") or "user") != "assistant":
            continue
        return looks_like_html(turn.get("content") or "")
    return False


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
    # The turn above this one is a report — the follow-up is about that
    # document, not a reason to go looking in a tracker. Stateless matches are
    # already through (a follow-up that really does name an issue key still
    # routes here), so only the ambiguous, thread-carried ones stand down.
    if _last_answer_was_report(history):
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


# ── Ticket-update intent (rewrite an existing ticket FROM a PRD) ─────────────
# "update the ticket details with the PRD", "rewrite PROJ-142 from the spec",
# "sync the ticket description to the PRD". Checked BEFORE is_jira_lookup, which
# claims these sentences today (a write verb on a PM noun) and hands them to a
# skill with no way to read a PRD — the reported failure. Routes to
# app/ticket_update.py, which serves both Sprntly tickets and Jira issues.
_TICKET_UPDATE_VERB = re.compile(
    r"\b(updat(?:e|ing)|rewrit(?:e|ing)|revis(?:e|ing)|refresh(?:ing)?|"
    r"sync(?:ing)?|synchroni[sz](?:e|ing)|align(?:ing)?|populat(?:e|ing)|"
    r"fill\s+in|flesh(?:ing)?\s+out|expand(?:ing)?)\b",
    re.I,
)
# The SOURCE document the ticket is rewritten from.
_TICKET_UPDATE_SOURCE = re.compile(
    r"\b(prd|product\s+requirements?(?:\s+doc\w*)?|spec(?:ification)?s?|"
    r"product\s+brief|requirements?\s+doc\w*)\b",
    re.I,
)
# The link between the two — "update the ticket WITH the PRD". Requiring one
# keeps "update the ticket and the PRD" (two objects, not a rewrite) out.
_TICKET_UPDATE_LINK = re.compile(
    r"\b(with|from|using|based\s+on|per|against|to\s+match|to\s+reflect|"
    r"to\s+align\s+with|in\s+line\s+with)\b",
    re.I,
)
# Creation phrasings belong to the user-stories skill ("create tickets from the
# PRD" makes new tickets; it does not rewrite one that exists). Deliberately
# WITHOUT `sync` — which _vetoed_as_creation treats as creation because there it
# means the push flow, while "sync the ticket with the PRD" is exactly this path.
_TICKET_UPDATE_CREATE_VETO = re.compile(
    r"\b(create|generate|draft|make|build|author|produce|compose|push|delete)\b",
    re.I,
)


def is_ticket_update(question: str, history: list[dict] | None = None) -> bool:
    """True when the message asks to rewrite an EXISTING ticket from a PRD.

    Direction is what this test is really about, and it is the one thing a bag
    of keywords gets wrong. "update the ticket with the PRD" and "update the PRD
    with the ticket" carry the same words and mean opposite things — one
    rewrites a ticket, the other edits a document. So the ticket must appear
    BEFORE the source document: whichever noun the verb reaches first is the
    thing being changed.

    A follow-up inside a tracker thread may name no ticket at all ("update it
    with the PRD") — there the thread supplies the target, so anaphora plus an
    active tracker thread stands in for naming it.
    """
    q = question or ""
    if not (
        _TICKET_UPDATE_VERB.search(q)
        and _TICKET_UPDATE_LINK.search(q)
    ):
        return False
    m_source = _TICKET_UPDATE_SOURCE.search(q)
    if m_source is None:
        return False

    m_key = _JIRA_ISSUE_KEY.search(q)
    m_noun = _JIRA_PM_NOUN.search(q)
    starts = [m.start() for m in (m_key, m_noun) if m is not None]
    if not starts:
        # No ticket named here. Only a live tracker thread can supply one.
        return bool(history) and _in_tracker_thread(history) and bool(
            _TRACKER_ANAPHORA.search(q)
        )
    target_at = min(starts)
    # The document comes first → the DOCUMENT is what's being changed. Not ours.
    if target_at > m_source.start():
        return False
    m_veto = _TICKET_UPDATE_CREATE_VETO.search(q)
    # A creation verb governing the ticket ("create tickets from the PRD"), as
    # opposed to one sitting inside what's being described.
    if m_veto is not None and m_veto.start() < target_at:
        return False
    return True


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
    # Unambiguous product name, so a bare mention is enough. Naming it
    # alongside "atlassian" simply puts BOTH providers in the returned set —
    # this function answers with a set, not a first match.
    "confluence": re.compile(r"\bconfluence\b", re.I),
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


# The OTHER way a tool name is a subject: as a possessed artifact — "the Stripe
# integration", "our Figma plugin", "the Google Drive connector". The tool named
# there is the thing being BUILT or evaluated, not a source to open, so these are
# ordinary product questions and belong to the skill router.
#
# The veto above only covers competitive/comparison framing (dff83902), which
# leaves this whole family exposed: "should we prioritise the stripe integration
# or the notion one" matched `stripe`, and because only 8 of the 22 recognised
# providers have a live adapter, the user's PRIORITISATION question was answered
# "Stripe isn't a Sprntly connector yet" — a dead end, with the skill that does
# answer it never reached. Reported 2026-08-02. Customers who BUILD integrations
# ask this shape constantly, so it is their core subject matter that was losing
# skill routing.
#
# Whole-message, matching the veto above rather than dropping only the offending
# provider, and that is deliberate: the elliptical second half ("...or the notion
# ONE") carries no artifact noun of its own, so a per-provider rule would veto
# `stripe`, keep `notion`, and dead-end anyway. The cost is that a genuine read
# sharing a sentence with an artifact mention ("check slack for what was said
# about the stripe integration") falls through to the generic path — a softer
# failure than today's confident "not connectable", and the same trade the
# comparison veto already makes.
#
# Nouns kept deliberately narrow: `integration|connector|plugin|extension|add-on`
# are unambiguously "a thing we built or might build". `app`, `api`, `bot` and
# `sdk` are NOT included — "the slack app" and "the stripe api" are read as often
# as they are built, and vetoing those would cost real lookups.
_CONNECTOR_ARTIFACT_VETO = re.compile(
    r"\b(?:our|the|a|an|this|that|their|your)\s+[\w-]+(?:\s+[\w-]+)?\s+"
    r"(?:integrations?|connectors?|plugins?|extensions?|add-?ons?)\b",
    re.I,
)


def _named_connector_providers(text: str) -> set[str]:
    """Provider keys explicitly named in one message."""
    if _CONNECTOR_MENTION_VETO.search(text) or _CONNECTOR_ARTIFACT_VETO.search(text):
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
    #
    # …unless the turn above is a REPORT, in which case the follow-up is about
    # the document. That case reaches here loaded: the report ITSELF lists its
    # sources ("Support tickets (Zendesk), Sales/CS field notes (Salesforce
    # CRM)"), and the request that produced it usually named one too ("what are
    # customers saying on slack?"). Every one of those looks like a source this
    # thread was reading, so "explain more on recommendations point 1" would be
    # answered by re-reading Slack instead of by the report it names.
    if not history or _TRACKER_PIVOT.search(q) or _last_answer_was_report(history):
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
        # A report DOCUMENT names its sources as provenance, not as a source
        # this thread is reading live — see _in_tracker_thread for the same
        # exclusion on the tracker side.
        if content and not looks_like_html(content):
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
    """Match a user question to a PIPELINE via keyword rules.

    Returns the best SkillMatch, or None if no pipeline matches (→ general Ask,
    which is now the default answer rather than a fallback).

    A rule with a `_RULE_VETOES` entry defers when its veto also matches, so a
    question that belongs elsewhere keeps falling through instead of being
    claimed by a broader rule.
    """
    for pattern, skill_id, action, confidence in _RULES:
        if not pattern.search(question):
            continue
        veto = _RULE_VETOES.get(skill_id)
        if veto is not None and veto.search(question):
            continue
        return SkillMatch(skill_id=skill_id, confidence=confidence, action=action)
    return None


def list_available_skills(enterprise_id: str | None = None) -> list[dict]:
    """The company's OWN uploaded skills, for the chat composer's palette.

    This used to be the vendored built-in catalog, filtered to what the router
    could pick. Both halves of that are gone: the library is down to nine
    method docs bound by name from their own pipelines, and of those exactly
    three were ever routable — so the endpoint would have advertised three
    triggers the router can no longer select. A palette that offers a skill
    nothing will honour is worse than an empty one, so it now offers only the
    library the user themselves built, which IS still selectable (the LLM
    router's per-company block, and the composer's own trigger chip).

    Shape is unchanged — {id, label, trigger, description, category} — so the
    composer, the Skills screen and the command palette need no new contract;
    `category` is always "Custom" now, which is the honest grouping.

    Fails OPEN to [] on any error and on a missing tenant: this backs a picker,
    and a picker that 500s is worse than a picker that is empty.
    """
    if not enterprise_id:
        return []
    try:
        from app.db.custom_skills import list_custom_skills

        rows = list_custom_skills(enterprise_id) or []
    except Exception:  # noqa: BLE001 — a palette must never break the app
        logger.warning("custom-skill palette lookup failed", exc_info=True)
        return []
    out: list[dict] = []
    for row in rows:
        slug = (row.get("slug") or "").strip()
        if not slug:
            continue
        out.append({
            "id": slug,
            "label": (row.get("name") or slug).strip(),
            "trigger": f"/{slug}",
            "description": (row.get("description") or "").strip(),
            "category": "Custom",
        })
    return out
