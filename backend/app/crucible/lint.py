"""Causal lint — I5. Causal verbs require causal evidence.

Every statement leaving the engine passes through here. A sentence saying one
thing *caused* another, written over evidence that only shows the two move
together, is the most expensive kind of wrong this system can produce: it reads
as confident, it survives review because the number behind it is right, and the
customer acts on a mechanism that was never established.

Lint failure is a HARD ERROR, not a warning (SPEC §1 I5, §11). A warning gets
filtered out of logs by the second week.

## Two deliberate departures from the spec's four-line version

The spec sketches this as a substring scan over six verbs. Both changes below
make it stricter, and both are called out because deviating from the spec
quietly is how an invariant erodes.

**Word boundaries, not substrings.** `"causes" in text` fires on "root-cause
analysis" and misses nothing in exchange. A lint with false positives gets
disabled, so precision here is what keeps it enabled.

**Inflections are banned too.** The spec lists `causes` but not `caused`,
`drives` but not `driving`. "Slow export caused churn" is the identical
violation as "causes", and a lint that passes it is theatre. `INFLECTIONS`
extends the spec's list and is documented as an extension rather than folded in
silently; `spec_literal=True` runs the spec's six alone.

## What is NOT banned, and why

Hedged and comparative language is untouched: "correlates with", "is associated
with", "coincides with", "precedes". These describe what was actually observed
at correlational strength, and pushing authors away from them is what produces
the causal overclaiming in the first place. The lint's job is to make the honest
phrasing the easy one.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Optional

from app.crucible.types import EVIDENCE_STRENGTHS, EvidenceStrength

#: SPEC §11, verbatim. Changing this list is a spec change, not a refactor.
BANNED_CAUSAL_VERBS: tuple[str, ...] = (
    "causes", "drives", "leads to", "results in", "because of", "due to",
)

#: Phrases that LOOK like a banned verb and are not one. Checked first; a match
#: here suppresses the ban at that position.
#:
#: The spec's six are bare word sequences, and four of them are ordinary English
#: in this product's domain:
#:
#:   * `drives`  — "Two drives failed in the storage tier", "Shared drives sync
#:     nightly". The singular `drive` was already excluded for Google Drive; the
#:     exclusion did not survive its own pluralisation.
#:   * `leads to` — "Sales leads to follow up on are in the tracker". Sprntly
#:     ships a HubSpot connector; "leads" is unavoidable vocabulary.
#:   * `results in` — "click Results in the toolbar", matched across a
#:     capitalised UI label because the scan is case-insensitive.
#:   * `due to`   — "the contract is due to expire". `due to` + infinitive is
#:     temporal, not causal.
#:
#: These matter more than they would in a warning-only lint: I5 is a HARD ERROR,
#: so each false positive is a 500 on a real run, and "a lint with false
#: positives is one somebody switches off" is how the invariant dies.

#: Inflections of the spec's verbs. Same violation, different tense.
BANNED_CAUSAL_INFLECTIONS: tuple[str, ...] = (
    "caused", "causing", "causal factor",
    "drove", "driving",
    "lead to", "led to", "leading to",
    "result in", "resulted in", "resulting in",
    "owing to",
    # Unambiguously causal in every use I can construct; unlike `a cause of`,
    # there is no innocent reading of "X is a consequence of Y".
    "a consequence of", "as a consequence of", "in consequence of",
)

# CONSIDERED AND REJECTED, because each fires on ordinary prose and I5 is a HARD
# ERROR, so every false positive is a failed run rather than a warning:
#
#   contributes to   "Each account contributes to the total" — and worse, it is
#                    this engine's own vocabulary: signals contribute to a theme
#                    score, and GOAL_ANALYSIS.md uses that framing itself.
#   is why           "That is why the report shows two rows."
#   explains why     "The PRD explains why the retired sections are hidden."
#   responsible for  "The engineer responsible for the connector is on call."
#   attributable to  "The invoice is attributable to the July billing period."
#   driven by        "a job driven by cron"
#   a cause of       "A cause of concern for the team is the backlog size."
#   drivers of       "Drivers of the new hardware ship in August."
#   makes / produces / triggers / reduces / increases — each far more common in
#                    prose that asserts nothing than in a causal claim.
#
# The head-noun assertions those were reaching for are caught by
# CAUSAL_HEAD_NOUN below, which requires the copula AND its complement rather
# than banning a noun and then trying to exempt every innocent use of it.

#: What makes a complement a DISCLAIMER rather than an assertion.
#:
#: STEMS, not whole phrases, and that distinction is the fix. The first version
#: was a closed nine-phrase allowlist, so it hard-errored on "is not established
#: by the available evidence" — the exact honest phrasing this lint exists to
#: make easy — and on "is being tracked in ticket KAN-322", "is yet to be
#: identified", "is one of three candidates", "is disputed between the two
#: sources". Nine sentences that assert no cause at all, each a failed run.
#:
#: A negation or an uncertainty stem anywhere in the complement means the
#: sentence is reporting that we do NOT know, which is never what I5 exists to
#: catch. This costs some recall. A lint that 500s on honest reporting costs
#: the whole invariant, because somebody switches it off.
_NOT_A_DISCLAIMER = (
    r"(?![^.;]{0,60}?\b(?:not|never|unknown|unclear|uncertain|undetermined|"
    r"unresolved|pending|tbd|investigat\w*|identif\w*|determin\w*|establish\w*|"
    r"disput\w*|document\w*|track\w*|candidate\w*|hypothes\w*|suspect\w*|"
    r"what|which|whether|why|yet|open)\b)"
)

CAUSAL_EXEMPTIONS: tuple[str, ...] = (
    r"\b(?:hard|disk|shared|google|storage|ssd|usb|network)\s+drives\b",
    r"\bdrives\s+(?:failed|sync|are|were|mounted|attached)\b",
    # "Sales leads to follow up on" — a noun phrase. Requires the following
    # verb; the unconditional form used to swallow "Sales leads to churn".
    r"\b(?:sales|marketing|inbound|qualified)\s+leads\s+to\s+"
    r"(?:follow|chase|call|contact|qualify|review|action)\b",
    # "click Results in the toolbar" — the UI word must END the clause. Without
    # the lookahead this swallowed "results in the panel timing out", a causal
    # claim about a UI element, which is what this product writes about most.
    r"\bresults\s+in\s+the\s+(?:toolbar|sidebar|panel|menu|tab|header|view)\b"
    r"(?!\s+\w)",
    # "due to expire" — temporal, not causal. The infinitive list is the weak
    # part of this design and is why the head-noun rule below is a regex
    # instead: an allowlist of literal phrasings needs a new entry for every
    # sentence a model writes.
    r"\bdue\s+to\s+(?:be|go|run|arrive|expire|renew|ship|start|begin|land|"
    r"close|complete|finish|launch|report|end)\b",
)

#: The head-noun causal assertion, banned by SHAPE rather than by phrase.
#:
#: "The primary driver of churn is export latency" is a full causal claim, and
#: banning the bare nouns `cause`/`drive` is not an option — "root-cause
#: analysis" and "Google Drive" are everywhere in this product. So the ban
#: requires the whole construction: head noun, its object, the copula, and a
#: complement that is not a disclaimer.
#:
#: KNOWN LIMIT, stated rather than papered over: "The cause of X is unknown; it
#: is Y" passes, because `[^.;]` stops the match at the semicolon and the
#: second clause names no banned phrase. That is a contradictory sentence
#: nobody writes deliberately, and catching it costs more precision than it
#: buys.
#: The head noun with the copula in FRONT: "X is the cause of Y", "latency is
#: the primary driver of churn". Always an assertion — there is no reading of
#: "is the cause of" that leaves the cause unclaimed — so unlike the bare noun
#: this needs no complement check.
CAUSAL_COPULA_FIRST = (
    r"\bis\s+(?:the|a|one)\s+"
    r"(?:primary\s+|main\s+|root\s+|major\s+|biggest\s+|largest\s+|underlying\s+)?"
    r"(?:cause|driver)s?\s+of\b"
)

CAUSAL_HEAD_NOUN = (
    r"\b(?:the|a|its|one)\s+"
    r"(?:primary\s+|main\s+|root\s+|biggest\s+|largest\s+|underlying\s+)?"
    # "a cause of concern for the team is the backlog" is an idiom, not a
    # causal claim about concern. Excluded in the RULE rather than by another
    # span exemption, because an exemption shorter than the hit cannot contain
    # it and so would never fire.
    r"(?:cause|driver)s?\s+of\s+(?!concern\b|alarm\b|worry\b|complaint\b)"
    r"[^.;]{0,80}?\bis\s+" + _NOT_A_DISCLAIMER
)

#: THE PASSIVE FORM — and the one the head-noun rule structurally CANNOT see,
#: because that rule keys on `cause|driver` + `of` + copula and "X is driven by
#: Y" has none of the three.
#:
#: `driven by` / `responsible for` / `attributable to` were dropped from the
#: phrase list for good reasons ("a job driven by cron", "the engineer
#: responsible for the connector is on call", "attributable to the July billing
#: period") — but nothing replaced them, so four genuine assertions went legal
#: while a comment claimed the head-noun rule covered them. It did not. "X is
#: driven by Y" is the most common passive causal construction an LLM writes.
CAUSAL_PASSIVE = (
    r"\b(?:is|are|was|were)\s+"
    r"(?:driven\s+by|caused\s+by|responsible\s+for)\s+"
    + _NOT_A_DISCLAIMER
)

# `attributable to` was TRIED here and removed: it catches "Churn is
# attributable to export latency" and also "The invoice is attributable to the
# July billing period", which is accounting, not causation, and which no
# disclaimer stem can distinguish. A hard error on an invoice sentence costs
# more than the one assertion it buys. Documented rather than silently dropped
# so the next person does not re-add it and rediscover the invoice.

#: "The reason for X is Y", "the reason why X is Y", "the consequence of X is
#: Y". Also dropped for precision and also not replaced.
CAUSAL_REASON_NOUN = (
    r"\b(?:the\s+reason\s+(?:for|why)|(?:the|a)\s+consequence\s+of)\b"
    r"[^.;]{0,80}?\bis\s+" + _NOT_A_DISCLAIMER
)



#: The one strength that has earned causal language. An experiment, and nothing
#: else — `measured` means we counted it, not that we established why.
CAUSAL_STRENGTHS: frozenset[str] = frozenset({"causally_tested"})


class CausalLintError(ValueError):
    """A causal claim written over non-causal evidence."""

    def __init__(self, violation: str, strength: str, text: str) -> None:
        self.violation = violation
        self.strength = strength
        self.text = text
        super().__init__(
            f"I5: causal phrasing {violation!r} over {strength!r} evidence. "
            f"Only {sorted(CAUSAL_STRENGTHS)} may assert causation. "
            f"Rewrite to what was observed, or raise the evidence. "
            f"Statement: {text[:200]!r}"
        )


@dataclass(frozen=True)
class LintResult:
    ok: bool
    violation: Optional[str] = None
    #: Every hit, not just the first — an author fixing one phrase at a time
    #: through repeated failures is the worst version of this loop.
    violations: tuple[str, ...] = ()


def _pattern(phrases: tuple[str, ...]) -> re.Pattern[str]:
    """One alternation, longest-first so 'led to' wins over 'lead to' overlap,
    with `\\s+` between words so line-wrapped prose is caught too."""
    ordered = sorted(set(phrases), key=len, reverse=True)
    alts = [r"\s+".join(re.escape(w) for w in p.split()) for p in ordered]
    return re.compile(r"\b(" + "|".join(alts) + r")\b", re.IGNORECASE)


_SPEC_ONLY = _pattern(BANNED_CAUSAL_VERBS)
_WITH_INFLECTIONS = _pattern(BANNED_CAUSAL_VERBS + BANNED_CAUSAL_INFLECTIONS)
_EXEMPT = re.compile("|".join(CAUSAL_EXEMPTIONS), re.IGNORECASE)
#: The four shape rules, checked together. Each requires a whole construction
#: rather than a phrase, which is what lets the ban list stay small enough not
#: to fire on ordinary prose.
_HEAD_NOUN = re.compile(
    "|".join(
        f"(?:{rule})"
        for rule in (
            CAUSAL_HEAD_NOUN,      # "the cause of X is Y"
            CAUSAL_COPULA_FIRST,   # "X is the cause of Y"
            CAUSAL_PASSIVE,        # "X is driven by Y"
            CAUSAL_REASON_NOUN,    # "the reason for X is Y"
        )
    ),
    re.IGNORECASE,
)


def _exempt_spans(text: str) -> list[tuple[int, int]]:
    return [m.span() for m in _EXEMPT.finditer(text)]


def _is_exempt(span: tuple[int, int], exempt: list[tuple[int, int]]) -> bool:
    """Does this hit fall inside a phrase we have decided is not causal?"""
    return any(start <= span[0] and span[1] <= end for start, end in exempt)


def lint_claim(
    text: str,
    strength: EvidenceStrength | str,
    *,
    spec_literal: bool = False,
) -> LintResult:
    """Check one statement. Pure; raises nothing.

    `strength` is the strength of the evidence the statement rests on. Anything
    below `causally_tested` may describe what was observed and may not assert
    why it happened.
    """
    if strength not in EVIDENCE_STRENGTHS:
        raise ValueError(
            f"Unknown evidence strength {strength!r}; expected one of "
            f"{sorted(EVIDENCE_STRENGTHS)}"
        )
    if strength in CAUSAL_STRENGTHS:
        return LintResult(ok=True)

    pattern = _SPEC_ONLY if spec_literal else _WITH_INFLECTIONS
    exempt = _exempt_spans(text)
    hits = tuple(dict.fromkeys(
        m.group(0).lower()
        for m in pattern.finditer(text)
        if not _is_exempt(m.span(), exempt)
    ))
    if not spec_literal:
        hits = hits + tuple(
            " ".join(m.group(0).lower().split())
            for m in _HEAD_NOUN.finditer(text)
            if not _is_exempt(m.span(), exempt)
        )
        hits = tuple(dict.fromkeys(hits))
    if hits:
        return LintResult(ok=False, violation=hits[0], violations=hits)
    return LintResult(ok=True)


def assert_lint_clean(
    text: str,
    strength: EvidenceStrength | str,
    *,
    spec_literal: bool = False,
) -> None:
    """`lint_claim`, as the hard error I5 requires. Use this at every boundary
    where a statement leaves the engine."""
    result = lint_claim(text, strength, spec_literal=spec_literal)
    if not result.ok:
        assert result.violation is not None
        raise CausalLintError(result.violation, str(strength), text)
