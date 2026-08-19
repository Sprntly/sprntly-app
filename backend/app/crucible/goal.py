"""Stage 0 — goal resolution. Adopted or elicited, never inferred.

`CRUCIBLE-GOAL-RESOLUTION.md`, and invariant I9, which sits above the other
nine: they protect the quality of the answer, this one protects the identity of
the question. A wrong definition does not produce a slightly wrong answer. It
produces a fully coherent, well-sized, well-argued answer to a DIFFERENT
question, and nothing downstream can detect it — the causal lint passes, the
scoring is sound, every claim traces to a real document, and the customer acts
on it.

So the rule is narrow:

    already defined in the company's systems  -> adopt it VERBATIM
    not defined, or defined more than once    -> ASK
    either way                                -> confirm before analysis runs
    once confirmed                            -> lock, and never ask again

## What this module will not do

**It will not paraphrase.** An adopted definition is returned byte-identical to
what the company wrote. "Revenue" tidied into "recognised revenue" is a
different metric, asserted by us, wearing their authority.

**It will not break a tie.** Two systems defining the same metric differently is
a CONFLICT, surfaced to the user. Picking the more recently updated one is the
exact failure I9 exists to prevent, and it is invisible afterwards.

**It will not lock anything.** `resolve()` only ever returns `candidate` or
`needs_input`. Locking requires `confirm()`, which requires a user id and a
timestamp, and `GoalDefinition.__post_init__` refuses the state without them.
No code path here — and no LLM output anywhere — can produce a locked
definition.

## The ladder

1. the company's own KPI tree (`companies.kpi_tree`)
2. a connected metric registry            — PR-later, interface only
3. a definitional statement in the corpus — PR-later, LLM call site 0b
4. ask

Steps 2 and 3 are declared as a `MetricSource` protocol and left unimplemented
rather than stubbed with something that guesses: an empty ladder rung that
falls through to "ask" is honest, and a rung that returns a plausible-looking
definition it invented is the failure this module exists to prevent.
"""
from __future__ import annotations

import hashlib
import logging
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Iterable, Literal, Optional, Protocol, Sequence

from app.crucible.types import (
    DefinitionConflict,
    GoalCurrency,
    GoalDefinition,
)

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class MetricCandidate:
    """A definition found somewhere, carried with where it came from.

    `definition_text` is VERBATIM. Nothing in this module rewrites it, and the
    confirmation screen shows it as the company wrote it — that is what makes
    "adopted" a true description rather than a claim about our own paraphrase.
    """
    metric_name: str
    definition_text: str
    source_ref: str
    source_label: str


class MetricSource(Protocol):
    """A rung on the ladder. Returns every candidate it can see, never a pick.

    Returning a LIST is the point: a source that resolved its own ambiguity
    would hide the conflict, and the conflict is the thing worth surfacing.
    """

    label: str

    def candidates(self, company_id: str, goal_text: str) -> Sequence[MetricCandidate]:
        ...


#: Words that carry no metric identity, so their presence or absence must not
#: decide a match.
_STOPWORDS = frozenset({
    "the", "a", "an", "of", "and", "or", "to", "in", "on", "for", "by", "per",
    "improve", "increase", "decrease", "reduce", "grow", "raise", "lower",
    "drive", "boost", "lift", "our", "my", "we", "total", "overall", "rate",
})


def _tokens(text: str) -> tuple[str, ...]:
    """Significant words, parentheticals dropped.

    "Net Revenue Retention (NRR)" and "improve net revenue retention" have to
    reach the same tokens, because that naming style — full name plus a
    bracketed abbreviation — is how half of a KPI tree is written, and a
    literal substring match sees no overlap at all between them.

    This is TOKENISATION, not inference. It normalises spelling; it does not
    decide what the user meant. A goal naming a different metric still finds
    nothing and falls through to the ask, which is what I9 requires.
    """
    import re as _re

    without_parens = _re.sub(r"\([^)]*\)", " ", text.lower())
    words = _re.findall(r"[a-z0-9]+", without_parens)
    return tuple(w for w in words if len(w) > 2 and w not in _STOPWORDS)


def _names_the_same_metric(
    metric: tuple[str, ...], goal: tuple[str, ...]
) -> bool:
    """Does the goal name this metric?

    Containment in EITHER direction, on significant tokens: "improve net
    revenue retention" names "Net Revenue Retention (NRR)", and "NRR" alone
    would too if the tree spelled it that way. Requires at least one
    significant token on the metric side, so a metric named only with
    stopwords cannot match everything.

    Deliberately NOT fuzzy. No edit distance, no stemming, no synonyms — each
    of those is a guess about intent, and a wrong guess here produces a
    coherent answer to the wrong question.
    """
    if not metric:
        return False
    return set(metric) <= set(goal) or set(goal) <= set(metric)


@dataclass(frozen=True)
class KpiTreeSource:
    """The company's own KPI tree — the rung that actually fires today.

    Matches on significant tokens, in either direction — see `_tokens` and
    `_names_the_same_metric`. Normalisation only: parentheticals dropped,
    stopwords ignored, no stemming, no edit distance, no synonyms. Each of those
    would be a guess about what the user meant, and I9 forbids exactly that.
    When nothing matches, the ladder falls through to the ask, which is the
    correct outcome rather than a failure.
    """

    tree: Any
    label: str = "your KPI tree"

    def candidates(self, company_id: str, goal_text: str) -> Sequence[MetricCandidate]:
        if not self.tree:
            return ()
        goal_tokens = _tokens(goal_text)
        found: list[MetricCandidate] = []

        entries: list[tuple[str, str, str]] = []
        north = getattr(self.tree, "north_star", None)
        if north is not None and getattr(north, "metric", ""):
            entries.append((north.metric, getattr(north, "description", ""), "north_star"))
        for i, m in enumerate(getattr(self.tree, "primary_metrics", []) or []):
            if getattr(m, "metric", ""):
                entries.append((m.metric, getattr(m, "description", ""), f"primary_metrics[{i}]"))

        for name, description, ref in entries:
            if _names_the_same_metric(_tokens(name), goal_tokens):
                found.append(MetricCandidate(
                    metric_name=name,
                    # VERBATIM, and empty when the company left it empty — an
                    # absent description is a real finding (the metric is named
                    # but never defined), not something to fill in.
                    definition_text=description or "",
                    source_ref=f"companies.kpi_tree.{ref}",
                    source_label=self.label,
                ))
        return tuple(found)


ResolutionStatus = Literal["candidate", "conflict", "needs_input"]


@dataclass(frozen=True)
class GoalResolution:
    """What Stage 0 found. Never a locked definition — see `confirm`."""
    status: ResolutionStatus
    definition: Optional[GoalDefinition] = None
    conflicts: tuple[DefinitionConflict, ...] = ()
    candidates_seen: tuple[MetricCandidate, ...] = ()
    #: What to put in front of the user. Built here so the wording lives beside
    #: the reasoning that produced it.
    ask: str = ""


def definition_hash(definition_text: str, source_ref: Optional[str]) -> str:
    """Covers the text AND where it came from.

    Both matter for drift: the same words arriving from a different source is a
    different definition, and the same source changing its wording is too.
    """
    payload = f"{definition_text.strip()}\x00{source_ref or ''}"
    return hashlib.sha256(payload.encode()).hexdigest()


def _conflicts_between(candidates: Sequence[MetricCandidate]) -> tuple[DefinitionConflict, ...]:
    """Distinct definitions of the same metric name, pairwise.

    Compared on the NORMALISED text so whitespace does not manufacture a
    conflict, but reported with the originals so the user sees what each system
    actually says.
    """
    conflicts: list[DefinitionConflict] = []
    for i, a in enumerate(candidates):
        for b in candidates[i + 1:]:
            if a.metric_name.lower() != b.metric_name.lower():
                continue
            if " ".join(a.definition_text.split()) == " ".join(b.definition_text.split()):
                continue
            conflicts.append(DefinitionConflict(
                metric_name=a.metric_name,
                source_a=a.source_ref, definition_a=a.definition_text,
                source_b=b.source_ref, definition_b=b.definition_text,
            ))
    return tuple(conflicts)


def resolve(
    *,
    company_id: str,
    raw_goal_text: str,
    currency: GoalCurrency,
    sources: Iterable[MetricSource],
    definition_id: str = "",
) -> GoalResolution:
    """Walk the ladder. Returns a CANDIDATE or a question — never a lock."""
    seen: list[MetricCandidate] = []
    for source in sources:
        try:
            seen.extend(source.candidates(company_id, raw_goal_text))
        except Exception:  # noqa: BLE001 — one broken rung must not end Stage 0
            logger.exception(
                "crucible goal: source %r failed; continuing down the ladder",
                getattr(source, "label", source),
            )

    conflicts = _conflicts_between(seen)
    if conflicts:
        # NEVER resolved here. Two authoritative systems disagreeing about what
        # a metric means is worth more than either answer — it says the model of
        # the business is wrong somewhere.
        return GoalResolution(
            status="conflict",
            conflicts=conflicts,
            candidates_seen=tuple(seen),
            ask=_conflict_ask(conflicts),
        )

    if not seen:
        return GoalResolution(
            status="needs_input",
            candidates_seen=(),
            ask=_no_definition_ask(raw_goal_text),
        )

    best = seen[0]
    if not best.definition_text.strip():
        # The metric is NAMED but never DEFINED. Adopting an empty definition
        # would mean sizing everything against a word, so this is an ask — and
        # a more useful one, because it can quote the metric back.
        return GoalResolution(
            status="needs_input",
            candidates_seen=tuple(seen),
            ask=_named_but_undefined_ask(best),
        )

    return GoalResolution(
        status="candidate",
        definition=GoalDefinition(
            id=definition_id or f"goal-{company_id}",
            raw_goal_text=raw_goal_text,
            metric_name=best.metric_name,
            definition_text=best.definition_text,
            definition_source_ref=best.source_ref,
            currency=currency,
            direction="increase",
            status="candidate",
            origin="adopted",
            definition_hash=definition_hash(best.definition_text, best.source_ref),
        ),
        candidates_seen=tuple(seen),
        ask=_confirm_ask(best),
    )


def confirm(
    definition: GoalDefinition,
    *,
    user_id: str,
    at: datetime,
    definition_text: Optional[str] = None,
) -> GoalDefinition:
    """Lock it. THE ONLY WAY a definition reaches `locked`.

    `definition_text` lets the user correct what was proposed — an adopted
    definition they edit becomes `elicited`, because it is now their words
    rather than their system's, and calling it adopted would misdescribe where
    it came from on every later run.
    """
    if not user_id:
        raise ValueError("I9: locking requires the id of the user who confirmed it")

    text = definition.definition_text if definition_text is None else definition_text
    edited = text.strip() != definition.definition_text.strip()

    return GoalDefinition(
        id=definition.id,
        raw_goal_text=definition.raw_goal_text,
        metric_name=definition.metric_name,
        definition_text=text,
        definition_source_ref=definition.definition_source_ref,
        source_ref=definition.source_ref,
        currency=definition.currency,
        direction=definition.direction,
        target_value=definition.target_value,
        horizon_weeks=definition.horizon_weeks,
        population=definition.population,
        status="locked",
        origin="elicited" if edited else (definition.origin or "adopted"),
        confirmed_by_user_at=at,
        confirmed_by_user_id=user_id,
        definition_hash=definition_hash(text, definition.definition_source_ref),
        supersedes=definition.supersedes,
        conflicts_found=definition.conflicts_found,
    )


def has_drifted(locked: GoalDefinition, current_text: str) -> bool:
    """Has the source definition changed since we locked it?

    Compared on the HASH, not the prose: re-reading the words on every run and
    diffing them is what makes a whitespace edit look like a redefinition.
    """
    return definition_hash(current_text, locked.definition_source_ref) != locked.definition_hash


# ── What the user actually sees ──────────────────────────────────────────────
# Three properties make an ask work (GOAL-RESOLUTION §5): it demonstrates a
# SEARCH rather than admitting ignorance, it proposes a default so the PM can
# approve rather than compose, and it names the CONSEQUENCE of the choice.

def _confirm_ask(c: MetricCandidate) -> str:
    return (
        f"I'll read \"{c.metric_name}\" as {c.source_label} defines it:\n\n"
        f"    {c.definition_text}\n\n"
        f"That's your definition, not mine — I haven't reworded it. "
        f"Everything I find gets sized against it, so if it's not the one you "
        f"meant, change it here and I'll use yours instead."
    )


def _named_but_undefined_ask(c: MetricCandidate) -> str:
    return (
        f"\"{c.metric_name}\" is in {c.source_label}, but with no definition "
        f"written down — so I know what you call it and not how it's "
        f"calculated.\n\n"
        f"Those are different questions and the second one decides every number "
        f"I produce: two teams can both point at this metric and mean gross "
        f"versus net, or booked versus recognised. Tell me how it's calculated "
        f"and I'll use that."
    )


def _no_definition_ask(goal_text: str) -> str:
    return (
        f"I can't find \"{goal_text}\" defined anywhere in your systems, so I "
        f"want to check what I'm optimising before I start.\n\n"
        f"Describe what you'd want to see move and I'll find the closest thing "
        f"you actually measure. I'd rather ask than guess: a definition I "
        f"invented would give you a confident answer to a question you didn't "
        f"ask, and you wouldn't be able to tell from the output."
    )


def _conflict_ask(conflicts: Sequence[DefinitionConflict]) -> str:
    first = conflicts[0]
    return (
        f"Your systems define \"{first.metric_name}\" two different ways, and I "
        f"won't pick for you — the choice changes every number in the "
        f"result.\n\n"
        f"  {first.source_a}:\n    {first.definition_a}\n\n"
        f"  {first.source_b}:\n    {first.definition_b}\n\n"
        f"Which one is the goal?"
    )
