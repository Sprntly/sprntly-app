"""Canonical user-facing insight types — the categories a PM picks to say which
findings they want as their Top Insights.

Single source of truth on the backend for:
  * the top-insights compose prompt (each composed finding is classified into
    one or more of these, so filtering matches the SAME vocabulary the user
    picked from — see synthesis/agent.py),
  * the per-user preference store and its validation,
  * the DB CHECK constraints on the stored preference (kept byte-identical in
    the migration that introduces them).

Mirrors the frontend list in web/app/lib/insight-types.ts. Adding, removing, or
renaming a type means changing BOTH sides AND the DB constraint(s).

THE TWO LISTS MUST BE THE SAME LENGTH. A type the backend can classify a
finding into, but the frontend does not render, is worse than no type at all:
the compose prompt promotes a finding under it and the browser then filters
that finding out of view, with nothing on either surface saying why. Between
2026-07-27 and 2026-08-05 exactly that held — the backend counted six, the
picker offered three — and the extra three were invisible state. Apurva's
ruling on 2026-08-05: a backend insight type is either wired through to the
web or it does not exist.

History: the original 6 onboarding chips merged with 3 client-requested report
types (2026-07-23; all three were duplicates, so the merged set stayed six),
then narrowed to the THREE the picker actually offers (2026-08-05). The three
dropped — user_feedback, reliability_signals, wins — had no skill configured to
produce them and no chip to select them. Two slugs were renamed in the 07-23
merge as their meaning broadened:
  drive_metric        -> build_priorities
  emerging_complaints -> user_feedback (since dropped)
See the accompanying migration for the data remap.

The DB CHECK constraints deliberately still accept the wider vocabulary. A
constraint that is a strict superset of the code is safe — nothing can write a
slug this module does not know — whereas narrowing it needs a migration and a
data remap for rows that already hold a dropped slug. `clean_insight_types`
drops those on read, which degrades to "surface everything", the same default
as no preference at all.
"""
from __future__ import annotations

#: slug -> (label, one-line description). The description is fed to the compose
#: prompt verbatim so the model classifies each finding into the exact same
#: categories the user selects from, making the per-user filter precise rather
#: than a fuzzy mapping off the internal 7-way skill taxonomy.
#: Order is the picker's chip order, so the prompt, the chips and the settings
#: rows all read the same way round.
INSIGHT_TYPES: "dict[str, tuple[str, str]]" = {
    "top_problems": (
        "Top user problems & opportunities",
        "The most pressing user/product problems and the biggest opportunities "
        "surfaced across all signals.",
    ),
    "competitor_moves": (
        "Competitor & market moves",
        "Competitive and market developments the team should react to "
        "(launches, pricing, positioning, category shifts).",
    ),
    "build_priorities": (
        "Most important to build",
        "The highest-priority things to build next, synthesizing every signal "
        "(metric movement, user demand, revenue, strategy).",
    ),
}

#: The slugs, in canonical display order. Use this everywhere a fixed set is
#: needed (schema enum, constraint list, validation).
INSIGHT_TYPE_SLUGS: "tuple[str, ...]" = tuple(INSIGHT_TYPES.keys())

#: slug -> (badge text, accent hex) for the CARD PILL the reader sees.
#:
#: Until 2026-08-05 the pill showed the top-insights skill's own 8-way taxonomy
#: (Reliability, Growth, Demand, Retention, Competitive, Engagement, Compliance,
#: Momentum) — a vocabulary the preference picker does not contain, so a reader
#: who asked for "Reliability & incident signals" had no way to look at a card
#: and tell whether their selection had been honoured. GROWTH in particular is
#: not a preference slug at all. The pill now names the finding's OWN
#: `insight_types`, i.e. the exact vocabulary the picker offers.
#:
#: The badge text is a short form of the picker's chip label (the chip wording
#: in full is too long for an 11px uppercase pill); it lives here beside the
#: prompt label so the two can never drift. Accents are reused verbatim from the
#: skill taxonomy's existing palette — no new colours are introduced, each slug
#: simply claims the hex whose meaning already matched it.
INSIGHT_TYPE_BADGES: "dict[str, tuple[str, str]]" = {
    "top_problems":     ("Top problem",      "#b23b52"),  # rose
    "competitor_moves": ("Competitor moves", "#b07a2e"),  # ochre
    "build_priorities": ("What to build",    "#1a8a52"),  # green
}


def is_valid_insight_type(slug: str) -> bool:
    return slug in INSIGHT_TYPES


def display_insight_type(
    insight_types: object, selected: "list[str] | None" = None,
) -> "str | None":
    """Which of a finding's insight types to show on its card.

    A finding carries one or two, in the model's own order — the first is its
    PRIMARY classification. We walk the finding's types in that order and take
    the first one the reader selected, so a card whose primary type was asked
    for keeps it, and a card whose primary was NOT asked for surfaces the
    secondary type that was. Walking the SELECTION order instead would let a
    reader's first chip override every card's primary and collapse distinct
    findings to the same label. With no selection, the primary.

    Returns None when the finding carries no known type, which
    is the legacy case: briefs composed before the classifier existed have no
    `insight_types` at all, and the caller must keep its old skill-taxonomy label
    rather than invent one (the 8 skill types do not map cleanly onto the 3
    preference slugs — retention, demand, engagement and compliance each have no
    faithful counterpart).
    """
    types = clean_insight_types(insight_types)
    if not types:
        return None
    wanted = set(selected or ())
    for slug in types:
        if slug in wanted:
            return slug
    return types[0]


def clean_insight_types(values: object) -> "list[str]":
    """Filter an arbitrary input down to known slugs, order-preserving and
    de-duplicated. Returns [] for anything unusable — the readers treat an empty
    selection as "surface everything", so a junk value degrades to the default
    rather than raising."""
    if not isinstance(values, (list, tuple)):
        return []
    out: list[str] = []
    for v in values:
        if isinstance(v, str) and v in INSIGHT_TYPES and v not in out:
            out.append(v)
    return out


def order_pool_for_types(
    pool: "list[dict]", selected: "list[str]",
) -> "tuple[list[dict], int]":
    """Stable partition of a composed pool by the reader's insight types.

    Findings whose `insight_types` intersect `selected` lead, in their existing
    (best-first) order; everything else follows, also in its existing order.
    Returns the reordered pool plus how many findings matched.

    This is the DETERMINISTIC half of the preference contract. The compose
    prompt already carries the selection as a ranking nudge
    (synthesis/reader_prefs), but a nudge can't guarantee the LEAD finding
    matches — measured across the live briefs, a preferred finding existed but
    sat below rank 1 in most of them. Reordering here makes `insights[0]` — the
    canonical top insight the weekly email, the Slack post, PRD warming and the
    KG ledger all key off — a preferred finding whenever the pool holds one.

    Honours SKILL.md step 4b literally: preferences REORDER, they never
    exclude. Nothing is dropped, and no selection (or no match) returns the
    pool unchanged so the model's own ranking stands. Byte-for-byte the same
    semantics as the frontend's orderPoolForTypes in web/app/lib/
    brief-v2-adapter.ts, so the browser's partition of an already-partitioned
    pool is the identity and the two surfaces cannot disagree.
    """
    wanted = {s for s in selected if s in INSIGHT_TYPES}
    # Nothing selected, or EVERYTHING selected — both mean "no preference", so
    # the model's own ranking stands. The all-types case is not redundant: a
    # legacy finding carries no `insight_types`, so it intersects no selection
    # and would be demoted below every classified finding by a selection that
    # was meant to express no preference at all. The pickers now resolve a
    # cleared selection to the full set rather than to [], so this is the shape
    # that actually arrives. Mirrors coversEveryInsightType in the frontend's
    # lib/insight-types.
    if not wanted or wanted >= set(INSIGHT_TYPES):
        return pool, 0
    matching: list[dict] = []
    rest: list[dict] = []
    for ins in pool:
        types = ins.get("insight_types")
        if isinstance(types, (list, tuple)) and wanted.intersection(types):
            matching.append(ins)
        else:
            rest.append(ins)
    return matching + rest, len(matching)


def prompt_block() -> str:
    """The TYPES reference block injected into the compose prompt, so the model
    classifies each finding into these exact categories."""
    lines = ["INSIGHT TYPES — classify every finding into one or two of these:"]
    for slug, (label, desc) in INSIGHT_TYPES.items():
        lines.append(f"  - {slug} ({label}): {desc}")
    return "\n".join(lines)
