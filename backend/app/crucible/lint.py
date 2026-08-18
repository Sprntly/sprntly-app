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
CAUSAL_EXEMPTIONS: tuple[str, ...] = (
    r"\b(?:hard|disk|shared|google|storage|ssd|usb|network)\s+drives\b",
    r"\bdrives\s+(?:failed|sync|are|were|mounted|attached)\b",
    r"\bsales\s+leads\s+to\b",
    r"\bleads\s+to\s+(?:follow|chase|call|contact|qualify)\b",
    r"\bresults\s+in\s+the\s+(?:toolbar|sidebar|panel|menu|tab|header)\b",
    r"\bdue\s+to\s+(?:expire|renew|ship|start|begin|land|close|complete)\b",
    # "The cause of the outage is still under investigation" asserts the exact
    # OPPOSITE of a cause — it says we do not know one. The head-noun ban is
    # about "the cause of X IS Y"; a sentence that names no Y is honest
    # reporting and must stay legal, or the lint punishes the phrasing it wants.
    (r"\b(?:the|a|root)\s+(?:root\s+)?caus(?:e|es)\s+of\b[^.]{0,80}?\bis\s+"
     r"(?:still\s+)?(?:under\s+investigation|unknown|unclear|not\s+yet\s+known|"
     r"being\s+investigated|to\s+be\s+determined|tbd)\b"),
    (r"\b(?:the|a)\s+(?:primary\s+|main\s+|biggest\s+|largest\s+)?drivers?\s+of\b"
     r"[^.]{0,80}?\bis\s+(?:still\s+)?"
     r"(?:under\s+investigation|unknown|unclear|not\s+yet\s+known)\b"),
)

#: Extension (see module docstring). Same violation, different tense.
#:
#: The bare nouns `cause` and `drive` are DELIBERATELY ABSENT. "Root-cause
#: analysis", "the cause of the outage" and "a drive to reduce churn" are not
#: causal assertions, and "Google Drive" is a connector this product syncs — so
#: banning the bare forms would fire constantly on legitimate text. A lint with
#: false positives is a lint somebody switches off, and a switched-off lint
#: protects nothing. Only the forms that can only be a causal verb are listed.
#: Head-noun causal assertions. These matter as much as the verbs and were
#: missing: the bare nouns `cause` and `drive` are excluded above, which left
#: "the cause of X is Y" and "the primary driver of X is Y" permanently legal
#: while banning the weaker "X causes Y". An LLM writing findings reaches for
#: these constructions at least as often.
BANNED_CAUSAL_INFLECTIONS: tuple[str, ...] = (
    "caused", "causing", "causal factor",
    "drove", "driving", "driven by",
    "lead to", "led to", "leading to",
    "result in", "resulted in", "resulting in",
    "responsible for", "attributable to", "owing to",
    "the reason for", "the reason why",
    # Head-noun forms.
    "the cause of", "a cause of", "root cause of",
    "the driver of", "the primary driver of", "a driver of", "drivers of",
    "a consequence of", "the consequence of", "as a consequence",
    "is why", "explains why", "explain why",
    "contributes to",
)

# DELIBERATELY NOT BANNED, having been considered and rejected: `makes`,
# `produces`, `triggers`, `reduces`, `increases`. Each appears in a genuine
# causal assertion ("slow export makes accounts churn") and in far more
# ordinary prose that asserts nothing ("the query produces 40 rows", "the
# webhook triggers a sync", "retention increases with tenure", "the discount
# reduces the total"). Banning them would fire constantly, and since I5 is a
# hard error every one of those is a failed run. The lint catches the
# constructions that can only be causal; it is not a general NLP filter and
# pretending otherwise would make it useless.

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
