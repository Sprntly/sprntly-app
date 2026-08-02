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

This module owns the data contract: the prompt we hand out, and the LLM pass
that reads the returned Markdown back into onboarding fields.

ONE READER, from v3 (2026-07-27). Until then there were two, in order: a
deterministic walk over the `## Section` headings our own prompt dictated, then
an LLM pass for the files that walk could not read. The v3 prompt is the
product team's own context-file spec, shipped as they wrote it — a YAML status
block and flat `field_name: value  [marker]` lines under five numbered
sections, with field names that describe the COMPANY rather than our onboarding
form. Keeping the heading walk would have meant editing their document to suit
our parser, so the walk was deleted instead and `extract_context_fields` is now
the only reader.

The costs of that, accepted deliberately: every import waits on an LLM
round-trip, so there is no instant inline prefill, and an API failure means the
import reads nothing. What it does not cost is the context itself — the raw .md
is filed as a company document and handed to the knowledge-graph ingest on
upload regardless of what the extraction makes of it, so a failed read still
leaves the user's material in the product.

The reader may not guess. The extraction prompt below says so explicitly and
the schema uses empty string / empty array for "not stated" — a blank field is
always the correct answer to a document that doesn't answer it. Every value
lands in the onboarding form as an EDITABLE prefill the user reviews, never as
a silently-committed answer.

The coercion layer between the model and the form is load-bearing and stays.
`_strip_marker` takes v3's provenance tags off a value, `_map_vocab` maps a
free-text answer onto the closed vocabulary a field accepts, and anything it
cannot place is DROPPED rather than snapped to the nearest option. That last
part is not tidiness: `companies.planning_cycle` and
`companies.prioritization_framework` carry DB CHECK constraints, so a raw
phrase written through would fail the entire workspace write, not merely render
an odd chip.
"""
from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field

logger = logging.getLogger(__name__)

#: Bumped whenever the document the prompt asks for changes shape. Echoed back
#: in the import result so a file produced by an older prompt is recognisable
#: rather than silently mis-read.
#:
#: v3 (2026-07-27): the prompt is the product team's own context-file spec — a
#: YAML status block plus flat `field_name: value  [marker]` lines under five
#: numbered sections — replacing the `## Section` heading contract of v1/v2 and
#: the deterministic parser that read it. v1 and v2 files still import fine:
#: they are prose documents like any other, and the extraction pass has never
#: required a heading contract.
CONTEXT_FORMAT_VERSION = "3"

#: The prompt we hand the user to run in their own assistant.
#:
#: Authored by the product team and kept VERBATIM — do not "tidy" it. The
#: retrieval order, the staleness line, the marker vocabulary and the
#: no-guessing discipline are all deliberate, and the last of those is the
#: whole reason an import is safe to trust.
#:
#: THE CONFIRMED-VALUES BLOCK SHIPS WITH ALL ITS LABELS AND (mostly) NO VALUES.
#: `build_context_prompt` fills the two lines onboarding has actually collected
#: by the time this prompt is served — company name and company website, from
#: the step that now runs BEFORE the import one. The rest stay empty, because
#: a visible label with nothing after it is itself a fact ("this was asked for
#: and not confirmed"), the sentence beneath the block refers to what the user
#: left blank, and the step lets them fill any of it in before copying. Do not
#: delete a line here for having no value to put in it.
CONTEXT_PROMPT = """\
I'm setting up an AI workspace for my company and I need a context file for it, so
the agents working in that workspace reason from real information instead of guesses.

Please write the document yourself rather than sending me a form to fill in. Some of
what's needed is in your saved memory of our past work, some in this conversation,
some on the company website. Use what you can get at.

What I want at the end is one complete .md file. Not an outline, not Section 1 with
an offer to continue, not three fragments I stitch together. Do all the searching
first, then write the whole thing.

The file goes straight into a form. Every line in it is a field value, so it needs to
be field values and nothing else. No introduction, no explanation of what you did, no
commentary between fields, no summary at the end. If a sentence isn't the answer to a
field, it doesn't belong in the file. Anything you want to tell me about the run goes
in the coverage line of the status block, which is the one place set aside for it.

Three more things to settle up front, so you don't have to ask.

I read all of this before any of it gets used. Nothing goes into a system unreviewed.
So you don't need to protect me from uncertain material by leaving it out. Put it in,
mark it, and let me decide. What I can't fix is something confidently wrong with no
marker on it, or something you found and dropped because you weren't sure. Capture
generously, label honestly.

Partial is the answer. I'm not asking for exhaustive coverage and I know you can't
search everything. Whatever you can reach is enough. A file built from part of the
picture is much more useful to me than no file.

And if you're about to send me a message explaining which sources you can and can't
reach, and offering to produce a partial file with an honest audit trail instead:
yes. That's what I want. You don't need to ask. Skip that message and write the file.

Spend as many messages as you need on searching. Just don't start writing until the
searching is done, so the file comes out in one piece.

---

## THINGS I'VE ALREADY CONFIRMED

Use these exact values. I've verified them myself, so if you find something different
in an older source, mine are the current ones. Note the discrepancy in the review
section at the end rather than changing the value.

    company name:
    also operates as:
    legal entity:
    positioning line:
    primary buyer:
    category:
    company website:

Anything I've left blank you'll have to work out, and you'll probably get some of it
wrong, so flag whatever you derived.

If other companies turn up in the sources, the one this file is about is the one I
build. Not one I sell to, analyse, advise, or compete with. Those appear often and
they read exactly the same in a search result.

---

## WHERE THIS STUFF USUALLY LIVES

Not a checklist and not a set of requirements. Just where this material tends to sit.
Use what you have, ignore what you don't, and don't treat an unreachable source as a
reason to stop.

    Saved memory or long-term context. Read what there is of it in full before you
    start searching. It's a primary source, not a footnote.

    Past conversations, if you can search them.

    This conversation, and anything attached to it.

    The company website. Home, about, product, pricing, careers, blog. Mission and
    vision are best taken from here, that's where we've actually written them.

    The wider web. Press, funding databases, review sites, competitor pages.

If you're not sure whether saved memory exists here, it goes by different names:
Memory or reference chat history in ChatGPT, Saved info or personal context in Gemini,
Memory or past chats in Claude. Meta AI keeps memory but usually can't search
conversations.

---

## HOW FAR TO GO

Roughly, in this order, dropping anything you can't do:

    Memory in full, first.
    The website.
    Topic searches on: positioning, ICP, pricing, roadmap, competitors, north star
    metric, decisions, team.
    The 30 or so most recent conversations, for anything that's changed since.
    Then one targeted search for each field still empty. This is the pass people skip
    and it's where a good share of the file comes from.
    If you can sort oldest-first, two searches on founding-era material. That's where
    past decisions and rejected directions live. If oldest-first just returns recent
    conversations instead, skip it and say so, rather than substituting more topic
    searches.
    The wider web last.

The first four are worth doing properly. The rest are worth doing if there's room.

A dozen to eighteen searches is about right. Past twenty-five you're over-searching and
will run out of room before you finish writing. Broad thematic queries work better than
narrow keyword ones. Where a detail matters, prefer an original conversation over a
summary of it, since summaries tend to collapse a suggestion and a decision into one
sentence.

Finish all of it before you write anything. If you're weighing one more search against
having room to finish the file, stop searching. The complete file matters more than the
last two fields.

---

## HOW MUCH TO CARRY

Don't trim to what a person would have had the patience to type. One value per field at
the length someone would type, then everything else on an "also mined" line directly
beneath that field. If you found twenty past decisions, give me the eight that matter
as rows and an also mined line saying there are twelve more. Cutting the surplus throws
away the main reason to do this by machine rather than by hand.

Same for anything that turned up in more than one form. Hold both, mark which is which,
let me pick.

---

## HOW IT SHOULD READ

Every line is either a field name and its value, or a row in a repeating field. Nothing
else.

    field name: value  [marker]

Field names in lowercase with underscores, exactly as listed in the section below, since
they're matched against form fields on import. Don't rename them, don't reorder them,
don't add fields that aren't listed, don't drop ones you couldn't fill. An empty field
with a marker is what tells me it was looked for.

    company_size:  [not found]
    pricing:  [stale, last seen Mar 2026]
    current_okrs:  [not available, no history access]
    positioning_line: Execution Intelligence Platform for AI-native software teams  [confirmed]

The marker sits after the value in square brackets, never inside it. A value reading
"execution intelligence platform (medium confidence, from an April 2026 conversation)"
can't be imported. Markers: confirmed, derived, stale, not found, not available, plus
high, medium or low where it helps.

Don't put a percentage on anything. You can't measure your own accuracy and I'm reading
it all anyway.

Shapes by field type:

    Short text: one line, no sentence unless the field is genuinely prose.
    Lists (competitors, banned_words, strategic_bets, constraints, roadmap_themes):
    plain comma-separated values on one line, no bullets, no commentary.
    Labelled pairs (users): sub-lines indented under the field name.
    Repeating (past_decisions, roster, integrations_and_data_sources, personas):
    a table, one row each, no paragraph above or below it.
    Prose (business_context, mission, vision, one_line_description, product_description):
    two or three sentences, the length someone types into a textarea. Business context
    reads like someone inside the company explaining it to a new hire, not like
    marketing copy and not like a report.

    users:
      who_uses_it: <role, seniority, team type>  [marker]
      who_buys_it: <title, and who signs>  [marker]
      company_type: <size, stage, sector>  [marker]

    competitors: <name, name, name>  [marker]
    competitors_also_mined: <the rest>

---

## HOW TO HANDLE GAPS

A blank field is fine. A wrong one is expensive, because everything downstream
inherits it. So:

    Couldn't find it: leave the value empty, mark it not found, list it in section 5.
    Whole source unavailable: mark not available rather than not found, so I can tell
    a tooling gap from a real gap.
    We genuinely don't have one: leave it empty and put the reason in section 5.

Please don't fill a field from inference. Counting five names in a roster and writing
"1 to 10 employees" is the kind of thing I'm trying to avoid.

Don't pad lists. Two real competitors beat six with four invented.

If two sources disagree, don't pick a winner quietly. Put your best value in the field
and both versions in section 5.

On age: four months is my staleness line, applied field by field rather than section
by section.

    Metrics, OKRs, headcount, pricing, roadmap, pilot status, runway. Older than four
    months with nothing recent confirming it, leave it blank. Stale numbers are worse
    than none.
    ICP, buyer, competitors, positioning, product surfaces, roster. Carry forward,
    mark stale, list in section 5 for me to confirm.
    Mission, founding story, past decisions, rejected directions, glossary, banned
    words, anti-positioning. Age doesn't matter. Full confidence.

One boundary: if you come across a customer's or partner's internal metrics, org
chart, roadmap or tool stack from pilot work, leave it out. That belongs to them.
What we learned from the work is ours and belongs in the file.

---

## BEFORE YOU WRITE

Three quick passes over what you've gathered:

    Anything appearing twice under two names, merge it. Companies and people both.
    Anything that can't be true at the same time across fields. Two different revenue
    numbers, a category that doesn't match the competitor set, a buyer persona that
    doesn't match the ICP, a strategy that contradicts the not-doing list. Those go in
    section 5, not reconciled quietly.
    Anything whose only support is that it seemed likely. Take it out.

---

## FIELDS

**Status block.** YAML, at the very top:

    status: complete | partial | awaiting input
    generated: <date>
    company_name: <name>
    company_name_source: confirmed | derived
    company_website: <url or blank>
    company_website_source: confirmed | derived | none
    entity_confidence: high | medium | low
    coverage: >
      What you actually looked at, what you didn't, anything that constrained the run.
      The only place in the file for anything that isn't a field value.
    fields_populated: <n of total>
    conflicts_open: <n>
    output_file: <filename>

**1. company**
company_name, also_operates_as, legal_entity, company_website, one_line_description,
business_context, mission, vision, category, stage, company_size, users, icp,
buyer_persona, competitors, differentiators, anti_positioning, north_star_metric,
north_star_baseline, north_star_target, current_okrs, pricing, strategic_bets,
constraints, not_doing_list, glossary, tone_and_voice, banned_words, brand_tokens

**2. product**
product_description, surfaces, personas, jobs_to_be_done, product_metrics,
roadmap_themes, integrations_and_data_sources, past_decisions

**3. team_and_workspace**
roster, roles, external_stakeholders, workspace_name, workspace_purpose,
workspace_scope, operating_cadence, rituals

**4. governance**
decision_rights, approval_thresholds, prioritisation_framework, data_sensitivity,
compliance_constraints

**5. review**
unresolved_conflicts, needs_confirming, deliberately_blank, also_mined_overflow

Section 5 is rows, not prose. One line per item: the field, the issue, the competing
values or the reason. It's the first thing I read, so it's the last thing to drop.

Field-specific:

    workspace_name and workspace_purpose from conversations first, website as fallback.
    company_size stays blank unless someone actually stated it.
    competitors: consolidate across sources with judgement rather than stacking two
    lists together.
    Conflicts never go inside a field value.

---

## FORMAT

One .md file. That matters more than anything else here, because it goes straight into
an import pipeline.

Filename: SPRNTLY-CONTEXT_<company>_<YYYY-MM-DD>.md

Whichever of these you can do, in this order:

    Write an actual file and give me the download link. If you can do this, write the
    whole document to the file in one go. Output limits are about chat messages and
    don't apply to a file, so there's no reason to break it up.
    Put it in a canvas or document titled with that filename.
    Failing both, put the whole thing in one fenced code block marked markdown, with
    the filename as a comment on the first line, and I'll save it myself.

What doesn't work is field values written straight into the chat with nothing around
them. I can't save that as a file without hand-cleaning it.

Write all five sections. Don't stop after section 1 to check in, don't ask whether to
proceed, and don't offer a summary in place of the rest.

If you genuinely run out of room mid-document, don't restart and don't re-emit what
you've already written. Say "continuing" outside the file and pick up at the exact
field you stopped at, adding to the same file or canvas so it ends up as one document.
If you're in a code block because you have no file or canvas, continue in a fresh code
block from the stopping point and I'll join them.

---

## IF THERE'S GENUINELY NOTHING

Only if you can't reach any source at all and I haven't attached anything. Don't send
an empty skeleton, just say this:

    I couldn't reach saved memory, past conversations or the web here, so there's
    nothing to work from yet. Either of these gets us moving:

    Quickest: attach a strategy doc, positioning one-pager, deck, roadmap or OKR
    sheet. Any two or three and I'll build the whole thing from those.

    Or fill this in and I'll build from it plus whatever the web has:

        Company name:
        Website:
        What you sell, one line:
        Who buys it (title, company type):
        Top 3 competitors:
        North star metric:
        One thing you've decided NOT to build, and why:

If you can reach the web but not memory or history, that's not nothing. Build the file
and put this after it, outside the file:

    These are the fields no website could tell me. Paste any of them and I'll fold
    them in and reissue:

        Past decisions you wouldn't want re-proposed:
        Things you've deliberately decided not to build:
        Words your team refuses to use:
        North star metric, current value and target:
        Current cycle OKRs:
        Team roster and roles:

If two different companies match and you can't tell which is mine, ask which one
rather than guessing.

When I reply to either of those, take what I give you as confirmed and go straight to
the document. No need to thank me or repeat back what I said.
"""

#: The confirmed-block lines `build_context_prompt` can fill, and the onboarding
#: value each takes. Matched as a WHOLE LINE (four-space indent, empty value) so
#: a same-named label elsewhere in the prompt — the "IF THERE'S GENUINELY
#: NOTHING" fallback form has its own `Company name:` — is never touched.
_PROMPT_CONFIRMED_LABELS = ("company name", "company website")

#: Cap on a value written into the block. The prompt asks for one line per
#: field; a pasted essay would push the block out of shape, and the assistant
#: reads the overflow as instructions rather than as a company name.
_PROMPT_VALUE_LIMIT = 200


def build_context_prompt(*, company_name: str = "", company_website: str = "") -> str:
    """CONTEXT_PROMPT with the confirmed-values block filled from onboarding.

    The company step runs BEFORE the import one (2026-07-27), so by the time
    the user copies this prompt we already know the two things it opens by
    asking for. Filling them here rather than leaving the user to retype them
    is the point of that reorder: the assistant gets the entity locked from the
    first line instead of inferring it, and a wrong company is the one error
    this whole document is built to avoid.

    Anything we don't have stays an empty labelled line — see CONTEXT_PROMPT.
    Values are flattened to one line and capped, because the block's shape is
    part of the contract; a multi-line value would read as extra instructions.
    """
    prompt = CONTEXT_PROMPT
    for label, value in zip(
        _PROMPT_CONFIRMED_LABELS, (company_name, company_website)
    ):
        text = " ".join((value or "").split())[:_PROMPT_VALUE_LIMIT]
        if not text:
            continue
        prompt = re.sub(
            rf"^(    {re.escape(label)}:)[ \t]*$",
            # A function replacement, so a backslash or a `\1` in the user's own
            # company name is written literally instead of being expanded.
            lambda m, t=text: f"{m.group(1)} {t}",
            prompt,
            count=1,
            flags=re.MULTILINE,
        )
    return prompt


#: What the v3 document writes in a marker when it didn't find something, plus
#: the placeholders every assistant reaches for. Compared case-insensitively;
#: treated as "the user never told them this", i.e. leave the onboarding field
#: blank rather than writing the literal word into the form.
_UNKNOWN_TOKENS = frozenset(
    {
        "unknown",
        "n/a",
        "na",
        "none",
        "-",
        "—",
        "tbd",
        "not found",
        "not available",
        "not stated",
        "blank",
    }
)

#: The v3 provenance vocabulary. A marker sits AFTER the value in square
#: brackets — "execution intelligence  [confirmed]", "pricing:  [stale, last
#: seen Mar 2026]" — so it is metadata about the answer, never part of it. The
#: prompt is explicit that a marker never goes inside a value, but the document
#: comes back from an assistant we don't control, so `_strip_marker` enforces
#: it rather than trusting it.
_MARKER_RE = re.compile(
    r"\s*\[\s*(?:confirmed|derived|stale|not\s+found|not\s+available"
    r"|high|medium|low)\b[^\]]*\]\s*$",
    re.IGNORECASE,
)

#: Keys that appear ONLY in a v3 status block, so a file carrying one was
#: produced by the current prompt. Used when there is no legacy
#: `<!-- sprntly-context vN -->` marker to read, which v3 documents have no
#: room for — every line in them is a field value by contract.
_V3_SIGNATURE = re.compile(
    r"^\s*(?:fields_populated|conflicts_open|output_file)\s*:", re.MULTILINE
)

#: Surface values the product step accepts, keyed by what an assistant is
#: likely to write. Anything unrecognised is dropped rather than pushed into
#: the form as an invalid chip.
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
#: just an odd chip — it makes the whole workspace write FAIL. So the reader
#: maps these to the canonical value the form and DB accept, and drops anything
#: it can't confidently place (blank is safe; a constraint violation loses the
#: entire import). Keys are `_normalise`d, trailing period stripped — see
#: `_map_vocab`.
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

#: Deliberately WIDE (2026-07-25). Prioritization is the field an export is
#: least likely to phrase canonically — almost nobody writes "goal-based", they
#: write "whatever moves the north star this half" — and the six options are
#: broad enough that the common phrasings genuinely belong to one of them. A
#: blank here also costs more than elsewhere: the metrics step makes the
#: framework MANDATORY, so an unmapped value is a form the import promised to
#: fill and didn't.
_FRAMEWORK_ALIASES: dict[str, str] = {
    # goal-based — the honest home for "we build what moves the goal", which is
    # what most teams describe when they name no framework at all.
    "goal based": "goal-based",
    "goal-based": "goal-based",
    "based on goal": "goal-based",
    "based on goals": "goal-based",
    "goal": "goal-based",
    "goals": "goal-based",
    "objective based": "goal-based",
    "objectives": "goal-based",
    "okr": "goal-based",
    "okrs": "goal-based",
    "okr based": "goal-based",
    "okr driven": "goal-based",
    "goal driven": "goal-based",
    "outcome based": "goal-based",
    "outcome driven": "goal-based",
    "outcomes": "goal-based",
    "strategic alignment": "goal-based",
    "strategy": "goal-based",
    "north star": "goal-based",
    "metric impact": "goal-based",
    "impact on metrics": "goal-based",
    # rice, and the neighbouring impact-vs-effort scoring models. ICE is RICE
    # without reach; an unweighted impact/effort grid is the same instinct.
    "rice": "rice",
    "rice scoring": "rice",
    "reach impact confidence effort": "rice",
    "ice": "rice",
    "ice scoring": "rice",
    "impact confidence ease": "rice",
    "impact effort": "rice",
    "impact and effort": "rice",  # "impact/effort" → " and "
    "impact vs effort": "rice",
    "impact versus effort": "rice",
    "effort and impact": "rice",
    "value and effort": "rice",
    "value vs effort": "rice",
    "value versus effort": "rice",
    "value effort": "rice",
    "weighted scoring": "rice",
    "scoring model": "rice",
    # wsjf
    "wsjf": "wsjf",
    "weighted shortest job first": "wsjf",
    "cost of delay": "wsjf",
    "cost of delay and job size": "wsjf",  # "cost of delay / job size"
    # moscow
    "moscow": "moscow",
    "must should could": "moscow",
    "must and should and could": "moscow",  # "must/should/could"
    "must have should have": "moscow",
    "must have should have could have": "moscow",
    # kano
    "kano": "kano",
    "kano model": "kano",
    # volume-severity
    "volume and severity": "volume-severity",  # "volume/severity" → " and "
    "volume severity": "volume-severity",
    "volume-severity": "volume-severity",
    "severity and volume": "volume-severity",
    "severity": "volume-severity",
    "ticket volume": "volume-severity",
    "issue volume": "volume-severity",
}

#: Framework tokens safe to match as whole words inside a longer phrase —
#: "RICE scoring for anything above two engineer-weeks" is unambiguously RICE.
#: ORDER IS THE PRECEDENCE: the first token found wins, so the named
#: frameworks are checked before the softer goal/impact phrasings, and a
#: sentence naming both ("RICE, in service of the quarter's OKRs") resolves to
#: the framework rather than the goal.
#:
#: Deliberately NOT applied to planning cycle, where a sentence like "six-week
#: cycles, quarterly OKR reviews" would wrongly match "quarterly" when the real
#: cadence is six-weekly.
_FRAMEWORK_KEYWORDS: tuple[str, ...] = (
    "rice",
    "wsjf",
    "weighted shortest job first",
    "moscow",
    "must and should and could",  # "must/should/could"
    "must should could",
    "must have should have",
    "kano",
    "ice",
    "cost of delay",
    "impact and effort",
    "impact vs effort",
    "impact versus effort",
    "value and effort",
    "value vs effort",
    "value versus effort",
    "reach impact confidence effort",
    "ticket volume",
    "severity",
    "okrs",
    "okr",
    "north star",
    "strategic alignment",
    "goals",
    "goal",
)

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
    # The canonical token itself: the extraction prompt asks for exactly this,
    # so without the identity entry the one answer we requested is the one
    # answer `_map_vocab` drops. See the round-trip test.
    "partner-rev-share": "partner-rev-share",
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

#: field name -> (alias map, whole-word keyword fallbacks). Drives `_map_vocab`,
#: which every closed-vocabulary value passes through on its way to the form.
_VOCAB_FIELDS: dict[str, tuple[dict[str, str], tuple[str, ...]]] = {
    "planning_cycle": (_PLANNING_CYCLE_ALIASES, ()),
    "prioritization_framework": (_FRAMEWORK_ALIASES, _FRAMEWORK_KEYWORDS),
    "monetization": (_MONETIZATION_ALIASES, ()),
}


@dataclass
class ParsedContext:
    """The outcome of reading one uploaded context document.

    `fields` holds only what the reader could confidently place. `unmapped` is
    kept for the response shape the onboarding client already consumes, and is
    empty from v3 on: with the heading walk gone there are no leftover sections
    to file, and nothing from the user's document is lost by leaving it so —
    the raw .md is filed as a document source in full and fed to the knowledge
    graph, whatever the extraction made of it.

    `format_version` is None when the file matches neither the v3 shape nor an
    older export's version marker (a hand-written or third-party document) —
    the caller surfaces that as "we read this best-effort", not as a failure.
    """

    fields: dict[str, object] = field(default_factory=dict)
    unmapped: dict[str, str] = field(default_factory=dict)
    format_version: str | None = None

    @property
    def is_empty(self) -> bool:
        return not self.fields


def _normalise(value: str) -> str:
    """Lower-case a value and flatten the punctuation an assistant is free to
    vary — `&`/`and`, `/`, a trailing colon — so "Impact / effort" and
    "impact and effort" reach the alias tables as the same key."""
    text = value.strip().strip(":").lower()
    text = text.replace("&", " and ").replace("/", " and ")
    return re.sub(r"\s+", " ", text).strip()


def _strip_marker(value: str) -> str:
    """Take the v3 provenance markers off the end of a value.

    "Execution intelligence  [derived] [medium]" is the value "Execution
    intelligence"; "[not found]" on its own is no value at all and comes back
    as "". Only trailing brackets whose contents are marker vocabulary are
    removed, so a value that genuinely ends in brackets survives.
    """
    text = value.strip()
    while True:
        stripped = _MARKER_RE.sub("", text).strip()
        if stripped == text:
            return text
        text = stripped


def _is_unknown(value: str) -> bool:
    """True for the placeholders a document uses when it doesn't know something.

    Deliberately NOT `_normalise` — that flattens `/` into " and " for alias
    matching, which would turn the very common "N/A" into "n and a" and let it
    through as real content. Placeholder detection needs a plain fold.
    """
    return value.strip().strip(".").strip().casefold() in _UNKNOWN_TOKENS


def _split_list(value: str) -> list[str]:
    """Comma-separated (or newline-bulleted) values -> a clean list. The schema
    asks for arrays, but a model handed a document whose lists are written
    "Linear, Jira, Productboard" on one line will sometimes echo that shape
    back, and splitting it is better than dropping it."""
    parts = re.split(r"[,\n]", value)
    return [p.strip().lstrip("-•*").strip() for p in parts if p.strip().lstrip("-•*").strip()]


def _map_surfaces(raw: list[str]) -> tuple[list[str], list[str]]:
    """Return (recognised surfaces, unrecognised inputs). Order-preserving and
    de-duplicated — a document saying "web app, website" must not produce two
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


def detect_format_version(markdown: str) -> str | None:
    """Which contract the uploaded file follows, or None if we can't tell.

    v3 documents carry no version marker on purpose — every line in them is a
    field value by contract, so there is nowhere to put one — and are
    recognised by the status-block keys only they emit. v1/v2 exports carry an
    `<!-- sprntly-context vN -->` comment, which still reads. Reported to the
    client, never acted on: an unrecognised file is read exactly the same way.
    """
    if not markdown:
        return None
    marker = re.search(r"<!--\s*sprntly-context\s+v(\S+)\s*-->", markdown)
    if marker:
        return marker.group(1)
    if _V3_SIGNATURE.search(markdown):
        return "3"
    return None


# ───────────────────────── LLM extraction pass ─────────────────────────
#
# The only reader since v3. It has to cope with the document our prompt asks
# for, an older `## Section` export, one the user reworded or hand-edited, and
# a strategy doc they already had and uploaded instead — so it reads meaning
# rather than structure, and everything it returns is validated below before it
# can reach the form.

#: The surfaces the product step accepts. The model is given these verbatim
#: and anything outside them is dropped on the way back — a chip the form
#: cannot render is worse than a blank field. The other three closed-vocabulary
#: fields (monetization, planning_cycle, prioritization_framework) are mapped
#: through the `_VOCAB_FIELDS` alias tables.
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
    "team_name": 100,
    "team_scope": 2000,
    "sizing_methodology": 1000,
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

3. TREAT A MARKED-EMPTY FIELD AS BLANK. Documents produced by our own prompt
   write a field with no value when the user never covered it. Return "" for
   those. The same goes for UNKNOWN, N/A, TBD, "not found", "not available",
   and bracketed placeholders like [Company Name].

4. NO STALE NUMBERS. Extract metric NAMES, not their values or targets. "MAU"
   is a metric; "77M MAU by Q4" is a metric plus a number whose freshness you
   cannot check. Names only.

5. ONE COMPANY. If the document discusses customers, competitors, or former
   employers alongside the subject company, extract only the subject — the one
   it speaks about as "we"/"our". Competitors go in the competitors list and
   nowhere else.

THE SHAPE OUR OWN PROMPT PRODUCES. Many of these documents are a YAML `status:`
block followed by flat `field_name: value  [marker]` lines under five numbered
sections (company, product, team_and_workspace, governance, review). When you
see that shape:

  · The square-bracket marker is PROVENANCE, not part of the value.
    "execution intelligence  [confirmed]" is the value "execution
    intelligence". The vocabulary is confirmed, derived, stale, not found, not
    available, high, medium, low. Never write a marker into a field.
  · A field with a marker and no value — "company_size:  [not found]" — was
    looked for and not found. Return "" for it.
  · The `status:` block describes the RUN, not the company: coverage, field
    counts, open conflicts. Take company_name and company_website from it if
    they are there and nowhere else; ignore the rest of it.
  · Section 5 (review) lists unresolved conflicts, values still to be
    confirmed, and fields left deliberately blank. Those are NOT facts and
    must never be promoted into a field. Keep them in `notes` so the user
    still sees them.
  · A line ending `_also_mined` is overflow for the field above it. Treat it
    the same way you treat that field.

WHERE THOSE FIELDS GO. Their names describe the company; ours name the
onboarding form. Map like this, and return "" for anything the document
doesn't carry:

  company_name, company_website        → the same
  mission, vision                      → mission
  current_okrs, strategic_bets         → strategy
  surfaces                             → surfaces
  pricing                              → monetization
  users, icp, buyer_persona, personas  → users_description
  competitors                          → competitors
  north_star_metric, then product_metrics → metrics, north star first, NAMES
      only (north_star_baseline and north_star_target are values — leave them
      out entirely)
  prioritisation_framework             → prioritization_framework (note the
      British spelling in the source)
  operating_cadence, rituals           → planning_cycle when they name a
      planning cadence, and sizing_methodology when they describe how work is
      estimated or sized
  workspace_name                       → team_name
  workspace_scope, workspace_purpose   → team_scope
  everything else worth keeping        → notes: business_context,
      one_line_description, product_description, category, stage,
      differentiators, anti_positioning, not_doing_list, constraints,
      glossary, tone_and_voice, banned_words, brand_tokens, past_decisions,
      roadmap_themes, jobs_to_be_done, integrations_and_data_sources, roster,
      roles, external_stakeholders, decision_rights, approval_thresholds,
      data_sensitivity, compliance_constraints, and the section 5 rows.

product_name and product_website are filled only when the document names the
product SEPARATELY from the company, and portfolio only when it names other
products the company also ships. Neither is a place to repeat the company name.

Documents that follow none of this — a strategy deck's notes, a reworded
export, a file the user edited by hand — are read the same way: report what
they state, blank what they don't.

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

ONE EXCEPTION — prioritization_framework is a CLASSIFICATION, not a quote.
Teams rarely name a framework; they describe how they choose. Map the
description onto the listed value that expresses it:
  · scored on reach / impact / confidence / effort, ICE, or any impact-vs-effort
    or value-vs-effort weighing  → rice
  · cost of delay, job size, weighted shortest job first  → wsjf
  · must / should / could / won't  → moscow
  · basic needs vs delighters, satisfaction curves  → kano
  · ranked by how many users hit it and how badly — ticket volume, severity,
    support load  → volume-severity
  · chosen by which goal, OKR, metric, north star or strategic bet it moves —
    including a plain "whatever moves the number this quarter"  → goal-based
This is still not a licence to guess: return "" when the document says nothing
about how work is chosen, or when it describes something none of the six can
express ("the CEO decides", "loudest customer wins"). The user reviews and can
change it on the metrics step, but a blank there is a required field they must
fill by hand — so classify when the description supports it.
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
            "description": 'Strategy, OKRs, strategic bets, or current goals. "" if not stated.',
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
            "description": (
                "The primary product's name, only when the document names it "
                'separately from the company. "" if not stated.'
            ),
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
            "description": (
                "Who the users, the ICP and the economic buyer are, in prose. "
                '"" if not stated.'
            ),
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
                "No values, baselines or targets. [] if not stated."
            ),
        },
        "prioritization_framework": {
            "type": "string",
            "description": (
                "One of: goal-based, rice, wsjf, moscow, kano, volume-severity. "
                '"" if not stated.'
            ),
        },
        "team_name": {
            "type": "string",
            "description": (
                "The name of the team or workspace the user works in (the "
                'document\'s workspace_name), NOT the company name unless the '
                'whole company is one team. "" if not stated.'
            ),
        },
        "team_scope": {
            "type": "string",
            "description": 'What that team or workspace owns end to end. "" if not stated.',
        },
        "sizing_methodology": {
            "type": "string",
            "description": (
                "How the team sizes work — story points, t-shirt sizes, who "
                'sizes, how estimates are calibrated. "" if not stated.'
            ),
        },
        "notes": {
            "type": "string",
            "description": (
                "Everything else worth keeping: business context, past decisions, "
                "the not-doing list, anti-positioning, constraints, glossary, "
                'banned words, cadence, roster, governance, review rows. "" if none.'
            ),
        },
    },
    "required": [
        "company_name", "company_website", "mission", "strategy", "portfolio",
        "planning_cycle", "product_name", "product_website", "surfaces",
        "monetization", "users_description", "competitors", "metrics",
        "prioritization_framework", "team_name", "team_scope",
        "sizing_methodology", "notes",
    ],
}


def _clean_text(value: object, limit: int) -> str | None:
    """A model-returned string -> a usable value, or None to leave blank.

    Strips the v3 provenance marker, then rejects the placeholder tokens the
    extraction prompt tells the model to avoid but which it can still echo from
    the source document — so neither a "[not found]" nor an "UNKNOWN" in the
    user's file ever reaches the form as literal text.
    """
    if not isinstance(value, str):
        return None
    text = _strip_marker(value)
    if not text or _is_unknown(text):
        return None
    # A bracketed placeholder ("[Company Name]") is a template artefact, not an
    # answer — the prompt forbids emitting one, and the source may contain one.
    if re.fullmatch(r"\[.*\]", text):
        return None
    return text[:limit]


def _clean_list(value: object, limit: int) -> list[str] | None:
    """A model-returned array -> a de-duplicated clean list, or None if empty.

    A comma-separated string is accepted too: the source document writes its
    lists that way, and a model echoing the source's shape should widen the
    import rather than lose the field.
    """
    if isinstance(value, str):
        value = _split_list(_strip_marker(value))
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
    dropped, markers, blanks and placeholders never make it through, and free
    text is capped to what the matching input accepts.
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
        # An out-of-vocabulary value is dropped, never snapped to the nearest
        # option — a wrong pick reads as the user's own answer — and a raw
        # phrase never reaches a CHECK-constrained column.
        canonical = _map_vocab(text, key)
        if canonical is not None:
            fields[key] = canonical

    for key, limit in _LIST_LIMITS.items():
        items = _clean_list(raw.get(key), limit)
        if items is None:
            continue
        if key == "surfaces":
            mapped, _rejected = _map_surfaces(items)
            items = mapped
        if items:
            fields[key] = items

    return fields


def extract_context_fields(markdown: str) -> ParsedContext:
    """Read an uploaded context document into onboarding prefill fields.

    The only reader since v3, and a background job because it costs an LLM
    round-trip.

    NEVER raises. Every failure mode — no API key, a timeout, a malformed
    response — degrades to an empty ParsedContext, which the caller reports
    honestly as "we couldn't read anything out of that file". The user's
    material is not lost either way: the raw .md was filed as a document source
    before this ran. A raise would strand the job row.
    """
    parsed = ParsedContext(format_version=detect_format_version(markdown))
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
    except Exception:  # noqa: BLE001 — an unread import beats a stranded job
        logger.exception("llm-context: extraction failed; the import reads nothing")
        return parsed

    if not isinstance(raw, dict):
        logger.warning(
            "llm-context: extraction returned %s, not a dict", type(raw).__name__
        )
        return parsed

    parsed.fields = _coerce_extracted(raw)
    return parsed
