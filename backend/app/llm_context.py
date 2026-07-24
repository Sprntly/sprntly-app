"""The "bring your own LLM context" import path.

Client feedback (July 22): most PMs have already explained their company,
product, users and strategy to an assistant — Claude, ChatGPT, Gemini — many
times over. Retyping all of it into onboarding is the single biggest reason
setup stalls. So we let them hand that context over instead of typing it: the
user copies CONTEXT_PROMPT into whichever assistant they already use, gets a
Markdown file back, and uploads it. Skipping means typing it by hand as before.

Why a prompt rather than an integration: an OAuth "connect your Claude
account" flow was built and then removed, because an Anthropic token
authorises Messages API calls and cannot read a user's claude.ai conversation
history — there is no endpoint for it, so the connected account could not
actually produce the context the button promised. One copy-paste prompt works
in every assistant today and needs no registered app, on our side or theirs.

This module owns the data contract: the prompt we hand out, the parser that
turns the returned Markdown back into onboarding fields, and the LLM extraction
pass that reads the files our prompt did NOT produce.

TWO READERS, IN THAT ORDER, and the order is the point:

  1. `parse_context_markdown` — a deterministic heading walk. The prompt
     dictates the exact headings, so for a file our own prompt produced this is
     enough, and it is free, instant, offline-testable, and incapable of
     inventing a fact the export didn't contain. It runs inline in the request.

  2. `extract_context_fields` — one LLM pass over the raw file. Users paste our
     prompt into assistants we don't control, edit the result, or upload a
     strategy doc they already had; all of those miss the heading contract and
     the walk reads nothing out of them. The LLM pass has no such requirement.
     It costs a round-trip, so it runs as a BACKGROUND job (llm_context_jobs)
     while the user works through the connectors step.

The merge rule is `_merge`: the deterministic value WINS wherever it exists.
It came from our exact contract; the LLM's is a reading. The LLM only ever
fills fields the walk left blank, so adding it can widen coverage and can never
corrupt a file that already parsed cleanly.

Neither reader may guess. The extraction prompt below says so explicitly and
the schema uses empty string / empty array for "not stated" — a blank field is
always the correct answer to a document that doesn't answer it. Anything we
cannot map is preserved verbatim in `unmapped` rather than dropped, and every
value lands in the onboarding form as an EDITABLE prefill the user reviews,
never as a silently-committed answer.
"""
from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field

logger = logging.getLogger(__name__)

#: Bumped whenever the heading contract below changes in a way that would make
#: an older export parse wrong. Emitted in the prompt and echoed in the parse
#: result so a stale export is recognisable rather than silently mis-mapped.
CONTEXT_FORMAT_VERSION = "1"

#: The prompt we hand the user to run in their own assistant.
#:
#: Authored by the product team (July 2026) — Stages 0 through 4d are their
#: text, kept VERBATIM. Do not "tidy" them: the retrieval budget, the
#: volatility classes, and the entity-lock rules are deliberate, and the
#: no-guessing discipline is the whole reason an import is safe to trust.
#:
#: ⚠️ TWO SECTIONS ARE OURS, NOT THEIRS, and are marked in-place below:
#:   * the closing sentence of Stage 4d (their copy was truncated mid-sentence)
#:   * Stage 5 in full (referenced twice in their text but never supplied)
#: Stage 5 is where the OUTPUT CONTRACT lives — the exact `##` headings
#: `parse_context_markdown` reads back. If you replace it, keep the heading
#: block byte-identical or update _SECTION_FIELDS / _BULLET_FIELDS to match;
#: `test_prompt_and_parser_agree_on_every_heading` fails loudly if they drift.
CONTEXT_PROMPT = """\
MODE: document
DELIVERY: inline
# ^ Caller sets these two lines. See Stage 0b. If absent, assume MODE: document.

You are an Executive Product Operations Architect. Search the user's memory and past
conversations and produce a Sprntly Onboarding Context File.

A wrong value is worse than a blank one. A document that silently misrepresents the
company is the worst outcome available to you. When in doubt, leave the field blank
and say so. The user will review this before it is used, so an honest gap is cheap
and a confident error is expensive.

Do not ask questions before starting. Do not narrate your process. Search, verify, emit.

═══════════════════════════════════════
STAGE 0 — PREFLIGHT: TWO SOURCES, NOT ONE
═══════════════════════════════════════

You have two distinct sources. Use both. The current chat window is not one of them —
it contains only this conversation and will not tell you about the company.

SOURCE 1 — LONG-TERM MEMORY.
Persistent facts the platform has saved about this user, separate from any single chat.
Platform names for it: "memory" / "saved memories" / "personal context" / "Memory"
(ChatGPT), "Saved info" / "personal context" (Gemini), "memories" (Claude), or an
equivalent settings-level store. Read this FIRST. It is short, dense, already
deduplicated, and tells you which entity you are dealing with before you spend any
search calls finding out.

SOURCE 2 — CONVERSATION HISTORY.
Past chats, retrievable by topic and, on some platforms, by recency. This is where
detail, dates, and reversals live.

Confirm you can reach at least one. If you can reach NEITHER, stop and emit only this:

  status: NO_SEARCH_ACCESS

  I can't reach your saved memory or your past conversations in this environment, so
  there's nothing for me to mine. Two options: run this in an assistant with memory
  and chat history enabled, or attach your strategy docs, roadmaps, OKR sheets, recent
  specs, and positioning material and I'll extract from those instead.

  A form full of blanks is worse than no form: it looks like a finding about your
  company when it is a finding about the tooling.

If you can reach memory but not conversation history, continue. Say so in the status
block and expect thin coverage on anything dated.

═══════════════════════════════════════
STAGE 0b — OUTPUT MODE
═══════════════════════════════════════

Read the MODE line at the top of this prompt.

MODE: document  (default — assume this if the line is missing or unreadable)
  A person pasted this in and will read the result.
  Emit a human-readable document. If the platform can create files, write a .md file
  and offer a .docx on request. If it cannot, emit the document inline in the chat.
  Include the review checklist required in Stage 5. Prose is fine. JSON is not.

MODE: json
  A program called this and will parse the result.
  Emit ONE JSON object and nothing else. No preamble, no markdown fences, no closing
  commentary, no explanation of what you did. If you cannot complete the task, still
  emit valid JSON with the appropriate status. A parser cannot recover from an apology.

DELIVERY: inline | file | webhook
  inline   — return in the response body.
  file     — write to a file and return the path.
  webhook  — the caller has supplied a target; POST the JSON payload to it and return
             only the status code and the payload you sent.
  If DELIVERY is absent, use inline for json and file for document (falling back to
  inline if file creation is unavailable).

Never guess the mode from tone or context. The MODE line is the only signal. Absent
line means document, always — a human handed unexpected JSON has lost some polish,
but a pipeline handed unexpected prose has broken.

═══════════════════════════════════════
STAGE 1 — ENTITY LOCK
═══════════════════════════════════════

Check long-term memory first — it usually names the company outright. Then run these
four searches to confirm and to find aliases:

  1. our company we are building
  2. my company our product our team
  3. our customers our pilot our client
  4. company name founded launched renamed

THE SUBJECT COMPANY is the one the user speaks about as "we / our / us" AND makes
decisions for. It is the company whose roadmap they set, whose pricing they choose,
whose hiring they do.

EXCLUDED, no matter how much material exists about them:
  · Customers, prospects, pilots, design partners, accounts — anything discussed as
    "they / their / the client"
  · Former employers discussed in the past tense
  · Companies analysed as competitors, case studies, or teardowns
  · Fictional or illustrative companies invented for a demo or template
  · Companies the user advises but does not run

ONE NAME, SEVERAL SPELLINGS is not ambiguity. A rename, a legal entity that differs
from the trading name, a product name that differs from the company name, and a
dictation typo are all the same entity. Lock it once and record the aliases.

EXACTLY ONE candidate passes → lock it. Every field from here refers only to this entity.

TWO OR MORE genuinely distinct companies pass → stop. Emit only:

  status: ENTITY_AMBIGUOUS
  entity_candidates: [Name A, Name B]

  Your history contains more than one company you appear to run or build. Tell me which
  one this context file is for and I'll run again.

ZERO pass → continue, set entity_confidence to low, and treat every entity-dependent
field with extra scepticism.

═══════════════════════════════════════
STAGE 2 — EXTRACTION RULES
═══════════════════════════════════════

PROVENANCE. Only statements the user authored or explicitly approved become facts.
Content you or another assistant generated — suggestions, drafted options, brainstormed
lists, recommended frameworks — is NOT a fact unless the user adopted it in their own
words. "Here are three positioning options" followed by "interesting" is not a decision.
"We're going with option two" is. This applies to prior context files too: an earlier
run of this prompt is a pointer, not a source.

DO NOT INFER. If the material does not say it, the field is blank. Do not reason your
way to a plausible value, do not fill a gap from what similar companies usually do, and
do not average two sources into a third number that appears nowhere.

SYNTHETIC DATA. Discard anything traceable to a demo dataset, sample data, dummy data,
test fixture, mock workspace, worked example, or illustrative scenario. These read
exactly like real metrics and are the most common source of false product data. If a
number's origin is unclear, leave it blank.

THIRD-PARTY CONTAINMENT. Never write another organisation's confidential internals into
this file. A customer may appear as a relationship — name, segment, status. Their
metrics, roadmaps, internal tooling, org structure, and strategy must not appear
anywhere in the document.

ABSOLUTE TIME. Convert relative language to absolute. "Next quarter" becomes the named
quarter; "last week" becomes a date or is dropped. Relative time in a persistent context
file decays into error.

PERSONAL DATA. Names and roles of team members and stakeholders are in scope.
Compensation, health, performance reviews, personal circumstances, and contact details
are not. Omit silently.

NO PLACEHOLDERS. Never emit a literal bracket token such as [Company] or [Date].
Substitute a real value or leave the field blank.

CONFIDENCE. Mark each populated field High, Medium, or Blank. These are provenance
labels, not accuracy estimates:
  High   — the user stated it directly, recently, and nothing contradicts it.
  Medium — the user stated it, but it is old, partial, or another value also exists.
  Blank  — not found, or found only in excluded material.
Do not report accuracy percentages, coverage percentages, or field counts. You cannot
measure your own accuracy and a number implies you can.

═══════════════════════════════════════
STAGE 3 — DATING AND RECONCILIATION
═══════════════════════════════════════

Date every extracted item by the conversation it came from. Dating drives the rules below.

CASE 1 — the same field has both older and newer sources.
The newer value wins. Mark Medium and note in one clause that other values exist. Never
average, never pick the better-written one. The superseded value is not waste: route it
to "notable past decisions" or "what the company decided not to pursue" with its date
and, where stated, the reason.

CASE 2 — a field has only OLD sources and nothing newer.
Silence is ambiguous. It may mean settled and never revisited, or quietly abandoned.
Resolve by volatility class:

STABLE — old sources acceptable at High. Decided once, remain binding:
  mission and vision · anti-positioning · the not-doing list · internal glossary ·
  banned words and conventions · notable past decisions · gold-standard templates ·
  jobs to be done · primary buyer pains · data sensitivity boundaries · decision gates ·
  brand and design fields · category

VOLATILE — old-only sources cap at Medium, with the source period stated, e.g.
"(last confirmed Q1 2026)":
  strategic bets · company goals · portfolio · competitive set · differentiation ·
  ICP and anti-ICP · surfaces · product description · personas · team roadmap ·
  decision process · prioritisation framework · decision rights · members ·
  stakeholders · operating cadence · workspace scope · stage · business type ·
  revenue model · constraints

HIGHLY VOLATILE — old-only sources are left Blank. Never import a stale number as a
current fact:
  monetisation and pricing · all current metric values · all metric targets ·
  north star current value and target · current-cycle OKRs · company size ·
  metrics tracked

CASE 3 — a newer source explicitly reverses an older one.
Exclude the old value entirely from its field. Record the reversal in decision history
or the not-pursuing list.

CASE 4 — two sources conflict and neither is clearly newer.
Do not choose. Populate the field with both, marked Medium, and add the conflict to the
review checklist in Stage 5. An unresolved conflict surfaced is useful; an unresolved
conflict silently resolved is a lie with a date on it.

═══════════════════════════════════════
STAGE 4 — RETRIEVAL BUDGET
═══════════════════════════════════════

CALL BUDGET: 32 retrieval calls maximum.
  · 1–2 calls  — long-term memory read
  · 3 calls    — recency sweep
  · 10 calls   — seed sweep
  · 2 calls    — archaeology sweep
  · remainder  — coverage batches

STOP at the first of:
  1. 32 calls
  2. Roughly 120 distinct conversations seen
  3. Three consecutive calls add no new populated field
  4. You judge the remaining context insufficient to write the full document

Conditions 3 and 4 matter most. Reserve room to write. A document truncated because
retrieval ate the context window is worse than one built from less material, because
truncation is invisible in the output.

MUST-ATTEMPT BLOCKS. Attempt all six regardless of how full the document already looks:
  · Notable past decisions and their outcomes
  · User personas and the economic buyer
  · North star metric and its definition
  · Current strategic bets
  · The not-doing list and anti-positioning
  · Known constraints

─── 4a. LONG-TERM MEMORY READ (first) ───

Retrieve everything the platform has saved about this user. Read it before searching
anything. It costs almost nothing, it names the entity, and it surfaces the vocabulary —
product names, colleague names, internal shorthand — that makes every later search
better. Treat its contents as user-authored unless it is visibly a summary of your own
prior output.

─── 4b. RECENCY SWEEP (3 calls) ───

If the platform can retrieve conversations by recency, pull the most recent 60 in
batches of 20. This covers what topic search structurally misses: recent changes whose
vocabulary matches none of the seed queries. Weight anything that contradicts or
supersedes older material.

If the platform cannot retrieve by recency, skip and go to 4c. Do not simulate it with
date-word queries — those match text about dates, not conversations from those dates.

─── 4c. SEED SWEEP (10 calls, run all ten) ───

Two to five content words. Real subject nouns. Never meta-words like "discussed" or
"conversation." Substitute the locked company and product names where they help.

  1. company strategy goals OKRs
  2. mission vision positioning
  3. product roadmap priorities quarter
  4. competitors competitive landscape
  5. customer persona ICP target user
  6. north star metric definition baseline
  7. pricing tiers packaging
  8. decision rejected alternative why
  9. constraints blockers technical debt risks
 10. team process prioritisation decision

CHECKPOINT A. If the must-attempt blocks are still entirely empty and the seed sweep
returned little that was usable, the topic vocabulary is not matching this history. Do
not rephrase the same queries. Go to Attempt 2.

─── 4d. ARCHAEOLOGY SWEEP (2 calls) ───

Retrieve the OLDEST conversations available, oldest-first, roughly 20 to 30. Run this
after the seed sweep so you already know which fields are empty.

Founding-era conversations hold material that is decided once, never re-discussed, and
still binding — precisely what topic search misses because nobody has mentioned it in a
year. Look for: the founding thesis and why the company exists · original naming,
glossary terms, internal shorthand · architectural or product invariants set early ·
rejected alternatives and the reasoning · the original ICP and who was deliberately
excluded · constraints accepted at the outset.

Apply Stage 3 to everything found here. Old material that survives — nothing newer
contradicts it, and the field is STABLE — is legitimate and marked High. Old material
in a VOLATILE or HIGHLY VOLATILE field is downgraded or blanked, not imported.

If oldest-first retrieval is unavailable, spend these two calls on the founding
vocabulary instead — "why we started", "original idea", "first version", "we decided
not to" — and treat whatever returns under the same Stage 3 rules. Do not fabricate an
origin story from the recent material; an empty founding section is a fact about the
history you can reach, not a gap to fill.

─── 4e. COVERAGE BATCHES (remaining calls) ───

Spend what is left of the budget on the fields still Blank, one targeted query per
field, using the vocabulary you learned in 4a. Stop the moment three consecutive calls
add nothing — the remaining budget is worth more as room to write than as more search.

═══════════════════════════════════════
STAGE 5 — EMIT
═══════════════════════════════════════

Emit the document below and nothing else. No preamble, no commentary, no code fence
around the whole thing.

Reproduce every `##` heading EXACTLY as written, in this order, including the `##`.
The headings are a machine contract — they are read back by a parser, so a renamed,
reordered, merged, or dropped heading silently loses that field. Keep a heading even
when its value is UNKNOWN.

One fact per line. Keep each answer to a phrase or a couple of sentences. Where you
know a value, write it plainly — no confidence label inside the field itself. Where you
do not, write exactly: UNKNOWN

<!-- sprntly-context v{version} -->

## Company
- Name:
- Website:

## Mission and vision

## Strategy and OKRs

## Portfolio

## Planning cycle

## Product
- Name:
- Website:
- Surfaces:
- Monetization:

## Users

## Competitors

## Metrics

## Prioritization

## Team

## Anything else

Guidance per section:
- Surfaces: which of web, mobile app, API, hardware the product ships on.
- Monetization: how the product makes money (subscription, usage-based, seat-based,
  transaction fee, freemium, enterprise contracts, partner rev-share, ads, or free).
- Users: who the users or customers are, in prose, including the economic buyer.
- Competitors: comma-separated names only.
- Metrics: the metrics the team is judged on, comma-separated. Name the north star
  first if one is defined. Do NOT carry over a stale value or target — Stage 3 makes
  those HIGHLY VOLATILE, so a number with no recent source is omitted entirely.
- Prioritization: how the team decides what to build next.
- Team: the team or workspace name, then what it owns end to end.
- Anything else: everything the sections above have no room for and that Stage 4's
  must-attempt blocks turned up — notable past decisions and their outcomes, the
  not-doing list and anti-positioning, known constraints, strategic bets, internal
  glossary and shorthand, operating cadence, decision rights. Keep it structured with
  `###` sub-headings so it stays readable; it is preserved whole.

Then, after the document, add these two blocks:

## Review checklist

List every item the user must personally verify before this file is used:
  · every field marked Medium, with the reason it is not High
  · every Stage 3 CASE 4 conflict, showing BOTH values and their dates
  · every must-attempt block that came back empty, so an absence reads as an absence
    rather than as a finding
  · anything you excluded on an entity-lock or third-party-containment judgement call

If the checklist is empty, say so in one line. Do not pad it.

## Status

  entity: <the locked company name, or UNKNOWN>
  entity_confidence: <high | medium | low>
  sources_reached: <memory and history | memory only | history only>
  period_covered: <oldest and newest conversation dates you actually saw>

State plainly whether coverage was thin and which sections suffered. Do not report
accuracy percentages, coverage percentages, or field counts — Stage 2 forbids them.
""".replace("{version}", CONTEXT_FORMAT_VERSION)

#: Heading (normalised) -> the onboarding field it prefills. Headings that a
#: user's assistant renders slightly differently ("Mission & vision" vs
#: "Mission and vision") normalise to the same key — see `_normalise`.
_SECTION_FIELDS: dict[str, str] = {
    "mission and vision": "mission",
    "strategy and okrs": "strategy",
    "portfolio": "portfolio",
    "planning cycle": "planning_cycle",
    "users": "users_description",
    "competitors": "competitors",
    "metrics": "metrics",
    "prioritization": "prioritization_framework",
    "team": "team_scope",
    "anything else": "notes",
}

#: (section, bullet label) -> onboarding field, for the two sections that carry
#: `- Label: value` bullets rather than a prose body.
_BULLET_FIELDS: dict[tuple[str, str], str] = {
    ("company", "name"): "company_name",
    ("company", "website"): "company_website",
    ("product", "name"): "product_name",
    ("product", "website"): "product_website",
    ("product", "surfaces"): "surfaces",
    ("product", "monetization"): "monetization",
}

#: Sections the prompt asks for that are for the HUMAN, not for onboarding: the
#: reviewer's to-verify list and the run's provenance report. They deliberately
#: map to no field — they land in `unmapped`, which the route preserves and
#: files with the export, so the reviewer keeps them without any of it being
#: mistaken for a company fact. Declared explicitly (rather than just falling
#: through) so the prompt/parser drift guard can still catch a heading that is
#: unmapped by ACCIDENT.
_NON_FIELD_SECTIONS = frozenset({"review checklist", "status"})

#: Fields the user's assistant returns as a comma-separated list.
_LIST_FIELDS = frozenset({"competitors", "metrics", "surfaces"})

#: What the prompt tells the assistant to write when it doesn't know. Compared
#: case-insensitively; treated as "the user never told them this", i.e. leave
#: the onboarding field blank rather than writing the literal word in.
_UNKNOWN_TOKENS = frozenset({"unknown", "n/a", "na", "none", "-", "—", "tbd"})

#: Surface values the product step accepts, keyed by what an assistant is
#: likely to write. Anything unrecognised is dropped from `surfaces` (and kept
#: in `unmapped`) rather than pushed into the form as an invalid chip.
_SURFACE_ALIASES: dict[str, str] = {
    "web": "web",
    "web app": "web",
    "website": "web",
    "browser": "web",
    "mobile": "mobile",
    "mobile app": "mobile",
    "ios": "mobile",
    "android": "mobile",
    "api": "api",
    "hardware": "hardware",
    "device": "hardware",
    "wearable": "hardware",
}

#: The onboarding form's three OTHER closed-vocabulary fields. Unlike a free
#: prose field, `companies.planning_cycle` and `companies.prioritization_framework`
#: carry a DB CHECK constraint, so a raw phrase the assistant wrote ("Six-week
#: build cycles…", "RICE scoring for anything above two engineer-weeks…") is not
#: just an odd chip — it makes the whole workspace write FAIL. So the parser
#: maps these to the canonical value the form and DB accept, and drops anything
#: it can't confidently place into `unmapped` (blank is safe; a constraint
#: violation loses the entire import). Keys are `_normalise`d, trailing period
#: stripped — see `_map_vocab`.
_PLANNING_CYCLE_ALIASES: dict[str, str] = {
    "half": "half",
    "every half": "half",
    "half year": "half",
    "half yearly": "half",
    "half-yearly": "half",
    "semi annual": "half",
    "semiannual": "half",
    "semi-annual": "half",
    "biannual": "half",
    "bi annual": "half",
    "twice a year": "half",
    "h1 and h2": "half",  # "H1/H2" normalises the slash to " and "
    "quarterly": "quarterly",
    "quarter": "quarterly",
    "quarters": "quarterly",
    "per quarter": "quarterly",
    "every quarter": "quarterly",
    "annual": "annual",
    "annually": "annual",
    "yearly": "annual",
    "year": "annual",
    "per year": "annual",
    "once a year": "annual",
    "monthly": "monthly",
    "month": "monthly",
    "per month": "monthly",
    "every month": "monthly",
}

_FRAMEWORK_ALIASES: dict[str, str] = {
    "goal based": "goal-based",
    "goal-based": "goal-based",
    "based on goal": "goal-based",
    "based on goals": "goal-based",
    "goal": "goal-based",
    "goals": "goal-based",
    "objective based": "goal-based",
    "rice": "rice",
    "wsjf": "wsjf",
    "moscow": "moscow",
    "kano": "kano",
    "volume and severity": "volume-severity",  # "volume/severity" → " and "
    "volume severity": "volume-severity",
    "volume-severity": "volume-severity",
}

#: Distinctive framework tokens safe to match as whole words inside a longer
#: phrase — "RICE scoring for anything above two engineer-weeks" is
#: unambiguously RICE. Deliberately NOT applied to planning cycle, where a
#: sentence like "six-week cycles, quarterly OKR reviews" would wrongly match
#: "quarterly" when the real cadence is six-weekly.
_FRAMEWORK_KEYWORDS: tuple[str, ...] = ("rice", "wsjf", "moscow", "kano")

_MONETIZATION_ALIASES: dict[str, str] = {
    "subscription": "subscription",
    "subscriptions": "subscription",
    "saas subscription": "subscription",
    "recurring subscription": "subscription",
    "recurring": "subscription",
    "seat": "seat",
    "seats": "seat",
    "seat based": "seat",
    "seat-based": "seat",
    "per seat": "seat",
    "per-seat": "seat",
    "usage": "usage",
    "usage based": "usage",
    "usage-based": "usage",
    "consumption": "usage",
    "metered": "usage",
    "pay as you go": "usage",
    "transaction fee": "transaction-fee",
    "transaction fees": "transaction-fee",
    "transaction-fee": "transaction-fee",
    "transaction": "transaction-fee",
    "take rate": "transaction-fee",
    "commission": "transaction-fee",
    "advertising": "advertising",
    "ads": "advertising",
    "ad supported": "advertising",
    "ad-supported": "advertising",
    "partner rev share": "partner-rev-share",
    "partner rev-share": "partner-rev-share",
    "partner revenue share": "partner-rev-share",
    "rev share": "partner-rev-share",
    "revenue share": "partner-rev-share",
    "one time": "one-time",
    "one-time": "one-time",
    "one time purchase": "one-time",
    "one-time purchase": "one-time",
    "one off": "one-time",
    "perpetual": "one-time",
    "perpetual license": "one-time",
    "free": "free",
    "freemium": "free",
    "free tier": "free",
    "no charge": "free",
}

#: field name -> (alias map, whole-word keyword fallbacks). Drives `_map_vocab`
#: from both readers so the deterministic parse and the LLM extraction land the
#: SAME canonical value for a closed-vocabulary field.
_VOCAB_FIELDS: dict[str, tuple[dict[str, str], tuple[str, ...]]] = {
    "planning_cycle": (_PLANNING_CYCLE_ALIASES, ()),
    "prioritization_framework": (_FRAMEWORK_ALIASES, _FRAMEWORK_KEYWORDS),
    "monetization": (_MONETIZATION_ALIASES, ()),
}


@dataclass
class ParsedContext:
    """The outcome of reading one exported Markdown file.

    `fields` holds only what we could confidently map; `unmapped` keeps every
    section we recognised but had nowhere to put, so nothing the user's export
    contained is silently discarded. `format_version` is None when the export
    carried no version marker (an older or hand-written file) — the caller
    surfaces that as "we read this best-effort", not as a failure.
    """

    fields: dict[str, object] = field(default_factory=dict)
    unmapped: dict[str, str] = field(default_factory=dict)
    format_version: str | None = None

    @property
    def is_empty(self) -> bool:
        return not self.fields


def _normalise(heading: str) -> str:
    """Lower-case a heading and flatten the punctuation an assistant is free to
    vary — `&`/`and`, `/`, trailing colons — so "Mission & Vision:" and
    "Mission and vision" are the same key."""
    text = heading.strip().strip(":").lower()
    text = text.replace("&", " and ").replace("/", " and ")
    return re.sub(r"\s+", " ", text).strip()


def _is_unknown(value: str) -> bool:
    """True for the placeholders the prompt tells the assistant to use when it
    doesn't know something.

    Deliberately NOT `_normalise` — that flattens `/` into " and " for heading
    matching, which would turn the very common "N/A" into "n and a" and let it
    through as real content. Placeholder detection needs a plain fold.
    """
    return value.strip().strip(".").strip().casefold() in _UNKNOWN_TOKENS


def _split_list(value: str) -> list[str]:
    """Comma-separated (or newline-bulleted) values -> a clean list."""
    parts = re.split(r"[,\n]", value)
    return [p.strip().lstrip("-•*").strip() for p in parts if p.strip().lstrip("-•*").strip()]


def _map_surfaces(raw: list[str]) -> tuple[list[str], list[str]]:
    """Return (recognised surfaces, unrecognised inputs). Order-preserving and
    de-duplicated — an export saying "web app, website" must not produce two
    `web` chips."""
    mapped: list[str] = []
    rejected: list[str] = []
    for item in raw:
        key = _normalise(item)
        surface = _SURFACE_ALIASES.get(key)
        if surface is None:
            rejected.append(item)
        elif surface not in mapped:
            mapped.append(surface)
    return mapped, rejected


def _map_vocab(value: str, field_name: str) -> str | None:
    """Map a free-text value to the canonical option a closed-vocabulary field
    accepts, or None when it can't be placed.

    Returning None is the safe outcome: the field is left blank for the user to
    pick rather than written raw, which for `planning_cycle` /
    `prioritization_framework` would violate a DB CHECK and sink the whole
    import. Exact (normalised) alias match first; then, only for fields that
    opt in, a whole-word keyword match so "RICE scoring for anything above two
    engineer-weeks" still resolves to `rice`.
    """
    spec = _VOCAB_FIELDS.get(field_name)
    if spec is None:
        return value
    aliases, keywords = spec
    key = _normalise(value).strip(" .")
    if key in aliases:
        return aliases[key]
    for token in keywords:
        if re.search(rf"\b{re.escape(token)}\b", key):
            return aliases[token]
    return None


def _is_known_section(heading: str) -> bool:
    """True when a heading names a section the contract defines."""
    key = _normalise(heading)
    return (
        key in _SECTION_FIELDS
        or key in _NON_FIELD_SECTIONS
        or key in {section for section, _ in _BULLET_FIELDS}
    )


def _sections(markdown: str) -> list[tuple[str, str]]:
    """Split the document into (heading, body) pairs.

    A `##` always starts a new section. A `###` starts one only if it NAMES a
    known section, which resolves the two ways assistants deviate, in opposite
    directions:

      * The prompt asks for `###` sub-headings inside "Anything else"
        (`### Not doing`, `### Glossary`). Those are content — breaking on them
        would shear that section apart and scatter it across bogus fields.
      * Some assistants demote the whole document a level and emit `###
        Mission and vision` for a real section. Those must still be read.

    Deeper levels (`####`+) are always body. Content before the first heading —
    a preamble the assistant added despite being told not to — is ignored
    rather than treated as a section body.
    """
    heading_re = re.compile(r"^\s{0,3}(#{2,3})\s+(.+?)\s*$")
    out: list[tuple[str, str]] = []
    current: str | None = None
    body: list[str] = []
    for line in markdown.splitlines():
        match = heading_re.match(line)
        if match and (len(match.group(1)) == 2 or _is_known_section(match.group(2))):
            if current is not None:
                out.append((current, "\n".join(body).strip()))
            current = match.group(2)
            body = []
        elif current is not None:
            body.append(line)
    if current is not None:
        out.append((current, "\n".join(body).strip()))
    return out


def _bullets(body: str) -> tuple[dict[str, str], str]:
    """Pull `- Label: value` lines out of a section body.

    Returns (bullets, leftover prose). A bullet whose value is empty is kept
    with an empty string so the caller can tell "the assistant left the field
    blank" from "the assistant never emitted that bullet".
    """
    bullets: dict[str, str] = {}
    prose: list[str] = []
    for line in body.splitlines():
        match = re.match(r"^\s*[-•*]\s*([^:]{1,60}):\s*(.*)$", line)
        if match:
            bullets[_normalise(match.group(1))] = match.group(2).strip()
        elif line.strip():
            prose.append(line)
    return bullets, "\n".join(prose).strip()


def parse_context_markdown(markdown: str) -> ParsedContext:
    """Read an exported context document into onboarding prefill fields.

    Tolerant by design: the file comes back from a third-party assistant we do
    not control, so unknown headings, missing sections, `##` vs `###`, and
    "UNKNOWN" placeholders all degrade to "we got less" rather than an error.
    A file we recognise nothing in yields an empty ParsedContext, which the
    route reports honestly instead of pretending the import worked.
    """
    result = ParsedContext()
    if not markdown or not markdown.strip():
        return result

    version = re.search(r"<!--\s*sprntly-context\s+v(\S+)\s*-->", markdown)
    if version:
        result.format_version = version.group(1)

    for heading, body in _sections(markdown):
        key = _normalise(heading)
        bullets, prose = _bullets(body)

        # Sections that carry `- Label: value` bullets (company, product).
        for label, value in bullets.items():
            target = _BULLET_FIELDS.get((key, label))
            if target is None:
                result.unmapped[f"{heading} / {label}"] = value
                continue
            if not value or _is_unknown(value):
                continue
            if target == "surfaces":
                mapped, rejected = _map_surfaces(_split_list(value))
                if mapped:
                    result.fields[target] = mapped
                if rejected:
                    result.unmapped[f"{heading} / {label} (unrecognised)"] = ", ".join(rejected)
            elif target in _VOCAB_FIELDS:
                # A closed-vocabulary field (monetization): canonicalise it, and
                # keep an unmappable value in `unmapped` rather than writing a
                # raw phrase the form can't select / the DB can reject.
                canonical = _map_vocab(value, target)
                if canonical is not None:
                    result.fields[target] = canonical
                else:
                    result.unmapped[f"{heading} / {label}"] = value
            else:
                result.fields[target] = value

        if not prose:
            continue

        target = _SECTION_FIELDS.get(key)
        if target is None:
            result.unmapped[heading] = prose
            continue
        if _is_unknown(prose):
            continue
        if target in _VOCAB_FIELDS:
            # planning_cycle / prioritization_framework carry a DB CHECK, so a
            # raw phrase here would fail the workspace write, not just render an
            # odd chip — map to the canonical value or drop it to `unmapped`.
            canonical = _map_vocab(prose, target)
            if canonical is not None:
                result.fields[target] = canonical
            else:
                result.unmapped[heading] = prose
            continue
        result.fields[target] = _split_list(prose) if target in _LIST_FIELDS else prose

    return result


# ───────────────────────── LLM extraction pass ─────────────────────────
#
# Reads the files the heading walk above cannot: an export from an assistant
# that reworded our headings, a document the user edited by hand, or a strategy
# doc they already had and uploaded instead. Runs as a background job because
# it costs an LLM round-trip; the deterministic parse has already returned by
# the time this starts.

#: The surfaces the product step accepts. The model is given these verbatim
#: and anything outside them is dropped on the way back — a chip the form
#: cannot render is worse than a blank field. The other three closed-vocabulary
#: fields (monetization, planning_cycle, prioritization_framework) are mapped
#: through the SAME `_VOCAB_FIELDS` alias tables the deterministic parser uses,
#: so both readers land the identical canonical value.
_ALLOWED_SURFACES = ("web", "mobile", "api", "hardware")

#: Free-text fields, and the cap we truncate them to. The caps match the
#: onboarding inputs' own maxLength so an imported value can be edited in the
#: form it lands in rather than arriving already over the limit.
_TEXT_LIMITS: dict[str, int] = {
    "company_name": 100,
    "company_website": 500,
    "product_name": 100,
    "product_website": 500,
    "mission": 500,
    "strategy": 2000,
    "portfolio": 500,
    "users_description": 2000,
    "team_scope": 2000,
    "notes": 20000,
}

#: List-valued fields and the maximum number of entries we keep.
_LIST_LIMITS: dict[str, int] = {
    "surfaces": len(_ALLOWED_SURFACES),
    "competitors": 20,
    "metrics": 20,
}

#: How much of the uploaded file we send. Context exports are prose; a document
#: past this is either padded or not a context export, and the tail of a long
#: one is worth less than a bounded, predictable prompt.
_EXTRACT_CHAR_LIMIT = 120_000

_EXTRACT_SYSTEM = """\
You extract onboarding fields from a product-context document.

The document was written by (or with) an AI assistant to describe ONE company
and its product. Your only job is to read it and report what it says.

THE RULES, IN PRIORITY ORDER:

1. NEVER GUESS. If the document does not state something, return "" (or [] for
   a list). A blank field is the correct answer to a document that does not
   answer it. Do not infer a plausible value from the industry, from the
   company name, or from what similar companies usually do. A wrong value is
   worse than a blank one — the user reviews these in a form, and a blank is
   obvious to fix while a confident error looks like their own answer.

2. REPORT, DO NOT SUMMARISE THE ASSISTANT. Extract what the USER's company is
   and does. If the document contains an assistant's suggestions, options it
   drafted, or alternatives it floated, those are not facts about the company
   unless the document says they were adopted.

3. TREAT "UNKNOWN" AS BLANK. Documents produced by our own prompt write the
   literal word UNKNOWN for fields the user never covered. Return "" for those,
   never the word itself. The same goes for N/A, TBD, and bracketed
   placeholders like [Company Name].

4. NO STALE NUMBERS. Extract metric NAMES, not their values or targets. "MAU"
   is a metric; "77M MAU by Q4" is a metric plus a number whose freshness you
   cannot check. Names only.

5. ONE COMPANY. If the document discusses customers, competitors, or former
   employers alongside the subject company, extract only the subject — the one
   it speaks about as "we"/"our". Competitors go in the competitors list and
   nowhere else.

Closed vocabularies — return ONLY these exact values, or "" / []:
  surfaces:                 web, mobile, api, hardware
  monetization:             subscription, seat, usage, transaction-fee,
                            advertising, partner-rev-share, one-time, free
  planning_cycle:           half, quarterly, annual, monthly
  prioritization_framework: goal-based, rice, wsjf, moscow, kano,
                            volume-severity
If the document describes something outside a vocabulary, return "" for that
field and leave the description in `notes`. Do not bend it to the nearest
option.
"""

_EXTRACT_SCHEMA: dict = {
    "type": "object",
    "properties": {
        "company_name": {
            "type": "string",
            "description": 'The subject company\'s name. "" if not stated.',
        },
        "company_website": {
            "type": "string",
            "description": 'Company website URL. "" if not stated.',
        },
        "mission": {
            "type": "string",
            "description": 'Mission and vision, in the document\'s own words. "" if not stated.',
        },
        "strategy": {
            "type": "string",
            "description": 'Strategy, OKRs, or current goals. "" if not stated.',
        },
        "portfolio": {
            "type": "string",
            "description": 'Other products in the company\'s portfolio. "" if not stated.',
        },
        "planning_cycle": {
            "type": "string",
            "description": 'One of: half, quarterly, annual, monthly. "" if not stated.',
        },
        "product_name": {
            "type": "string",
            "description": 'The primary product\'s name. "" if not stated.',
        },
        "product_website": {
            "type": "string",
            "description": 'Product website URL. "" if not stated.',
        },
        "surfaces": {
            "type": "array",
            "items": {"type": "string"},
            "description": "Which of web, mobile, api, hardware the product ships on. [] if not stated.",
        },
        "monetization": {
            "type": "string",
            "description": (
                "One of: subscription, seat, usage, transaction-fee, advertising, "
                'partner-rev-share, one-time, free. "" if not stated.'
            ),
        },
        "users_description": {
            "type": "string",
            "description": 'Who the users and the economic buyer are, in prose. "" if not stated.',
        },
        "competitors": {
            "type": "array",
            "items": {"type": "string"},
            "description": "Competitor company names only. [] if not stated.",
        },
        "metrics": {
            "type": "array",
            "items": {"type": "string"},
            "description": (
                "Metric NAMES the team is judged on, north star first. "
                "No values or targets. [] if not stated."
            ),
        },
        "prioritization_framework": {
            "type": "string",
            "description": (
                "One of: goal-based, rice, wsjf, moscow, kano, volume-severity. "
                '"" if not stated.'
            ),
        },
        "team_scope": {
            "type": "string",
            "description": 'The team or workspace name and what it owns end to end. "" if not stated.',
        },
        "notes": {
            "type": "string",
            "description": (
                "Everything else worth keeping: past decisions, the not-doing list, "
                'constraints, glossary, cadence. "" if there is none.'
            ),
        },
    },
    "required": [
        "company_name", "company_website", "mission", "strategy", "portfolio",
        "planning_cycle", "product_name", "product_website", "surfaces",
        "monetization", "users_description", "competitors", "metrics",
        "prioritization_framework", "team_scope", "notes",
    ],
}


def _clean_text(value: object, limit: int) -> str | None:
    """A model-returned string -> a usable value, or None to leave blank.

    Rejects the placeholder tokens the extraction prompt tells the model to
    avoid but which it can still echo from the source document, so an
    "UNKNOWN" in the user's export never reaches the form as literal text.
    """
    if not isinstance(value, str):
        return None
    text = value.strip()
    if not text or _is_unknown(text):
        return None
    # A bracketed placeholder ("[Company Name]") is a template artefact, not an
    # answer — the prompt forbids emitting one, and the source may contain one.
    if re.fullmatch(r"\[.*\]", text):
        return None
    return text[:limit]


def _clean_list(value: object, limit: int) -> list[str] | None:
    """A model-returned array -> a de-duplicated clean list, or None if empty."""
    if not isinstance(value, list):
        return None
    out: list[str] = []
    for item in value:
        text = _clean_text(item, 200)
        if text and text not in out:
            out.append(text)
    return out[:limit] or None


def _coerce_extracted(raw: dict) -> dict[str, object]:
    """Validate one LLM extraction into onboarding fields.

    Everything the model may have got wrong is caught here rather than in the
    form: unknown enum values are DROPPED (not snapped to the nearest option —
    a wrong pick reads as the user's own answer), unrecognised surfaces are
    dropped, blanks and placeholders never make it through, and free text is
    capped to what the matching input accepts.
    """
    fields: dict[str, object] = {}

    for key, limit in _TEXT_LIMITS.items():
        text = _clean_text(raw.get(key), limit)
        if text is not None:
            fields[key] = text

    for key in _VOCAB_FIELDS:
        text = _clean_text(raw.get(key), 200)
        if text is None:
            continue
        # Same alias tables as the deterministic parser: an out-of-vocabulary
        # value is dropped (never snapped to the nearest option — a wrong pick
        # reads as the user's own answer), and a raw phrase never reaches a
        # CHECK-constrained column.
        canonical = _map_vocab(text, key)
        if canonical is not None:
            fields[key] = canonical

    for key, limit in _LIST_LIMITS.items():
        items = _clean_list(raw.get(key), limit)
        if items is None:
            continue
        if key == "surfaces":
            # Reuse the same alias table the heading parser uses, so "web app"
            # and "iOS" land on the form's chips from either reader.
            mapped, _rejected = _map_surfaces(items)
            items = mapped
        if items:
            fields[key] = items

    return fields


def _merge(base: ParsedContext, extracted: dict[str, object]) -> ParsedContext:
    """Fold an LLM extraction into a deterministic parse.

    The deterministic value always wins: it came from the exact heading
    contract, while the extraction is a reading of prose. The LLM therefore only
    fills fields the walk left blank — widening coverage on files our parser
    doesn't understand, without ever being able to overwrite one it does.
    """
    merged = ParsedContext(
        fields=dict(base.fields),
        unmapped=dict(base.unmapped),
        format_version=base.format_version,
    )
    for key, value in extracted.items():
        if key not in merged.fields:
            merged.fields[key] = value
    return merged


def extract_context_fields(
    markdown: str, base: ParsedContext | None = None
) -> ParsedContext:
    """Read a context document with the LLM and merge over the heading parse.

    `base` is the deterministic parse the caller already ran (re-run here when
    omitted). Returns a ParsedContext holding the union of both readings — see
    `_merge` for who wins.

    NEVER raises. Every failure mode — no API key, a timeout, a malformed
    response — degrades to returning `base` unchanged, because the caller is a
    background job whose only purpose is to widen a prefill the user has
    already been given. A failed extraction costs them the fields the heading
    walk missed and nothing else; one that raised would strand the job row.
    """
    parsed = base if base is not None else parse_context_markdown(markdown)
    if not markdown or not markdown.strip():
        return parsed

    from app.llm import call_json

    try:
        raw = call_json(
            system=_EXTRACT_SYSTEM,
            user=(
                "Extract the onboarding fields from the context document below.\n"
                'Return "" or [] for anything it does not state.\n\n'
                "<document>\n"
                f"{markdown[:_EXTRACT_CHAR_LIMIT]}\n"
                "</document>"
            ),
            schema=_EXTRACT_SCHEMA,
            max_tokens=4000,
            # Extraction, not authoring — there is nothing to sample for.
            temperature=0,
        )
    except Exception:  # noqa: BLE001 — degrade to the deterministic parse
        logger.exception("llm-context: extraction failed; keeping the heading parse")
        return parsed

    if not isinstance(raw, dict):
        logger.warning(
            "llm-context: extraction returned %s, not a dict", type(raw).__name__
        )
        return parsed

    return _merge(parsed, _coerce_extracted(raw))
