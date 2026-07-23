"""Canonical user-facing insight types — the categories a PM picks to say which
findings they want as their Top Insights.

Single source of truth on the backend for:
  * the weekly-brief compose prompt (each composed finding is classified into
    one or more of these, so filtering matches the SAME vocabulary the user
    picked from — see synthesis/agent.py),
  * the per-user preference store and its validation,
  * the DB CHECK constraints on the stored preference (kept byte-identical in
    the migration that introduces them).

Mirrors the frontend list in web/app/lib/insight-types.ts. Adding, removing, or
renaming a type means changing BOTH sides AND the DB constraint(s).

History: merged from the original 6 onboarding chips + 3 client-requested
report types (2026-07-23). All three requested types turned out to be
duplicates of an existing chip, so the merged set is still six. Two slugs were
renamed because their meaning broadened in the merge:
  drive_metric        -> build_priorities
  emerging_complaints -> user_feedback
See the accompanying migration for the data remap.
"""
from __future__ import annotations

#: slug -> (label, one-line description). The description is fed to the compose
#: prompt verbatim so the model classifies each finding into the exact same
#: categories the user selects from, making the per-user filter precise rather
#: than a fuzzy mapping off the internal 7-way skill taxonomy.
INSIGHT_TYPES: "dict[str, tuple[str, str]]" = {
    "top_problems": (
        "Top user problems & opportunities",
        "The most pressing user/product problems and the biggest opportunities "
        "surfaced across all signals.",
    ),
    "build_priorities": (
        "Most important to build",
        "The highest-priority things to build next, synthesizing every signal "
        "(metric movement, user demand, revenue, strategy).",
    ),
    "user_feedback": (
        "User feedback & complaints",
        "What users are actually saying: emerging complaints, recurring feedback "
        "themes, and frequently-requested changes.",
    ),
    "competitor_moves": (
        "Competitor & market moves",
        "Competitive and market developments the team should react to "
        "(launches, pricing, positioning, category shifts).",
    ),
    "reliability_signals": (
        "Reliability & incident signals",
        "Reliability problems, incidents, errors, latency, and stability risks.",
    ),
    "wins": (
        "Wins to celebrate",
        "Positive movements, milestones, and wins worth recognizing.",
    ),
}

#: The slugs, in canonical display order. Use this everywhere a fixed set is
#: needed (schema enum, constraint list, validation).
INSIGHT_TYPE_SLUGS: "tuple[str, ...]" = tuple(INSIGHT_TYPES.keys())


def is_valid_insight_type(slug: str) -> bool:
    return slug in INSIGHT_TYPES


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


def prompt_block() -> str:
    """The TYPES reference block injected into the compose prompt, so the model
    classifies each finding into these exact categories."""
    lines = ["INSIGHT TYPES — classify every finding into one or two of these:"]
    for slug, (label, desc) in INSIGHT_TYPES.items():
        lines.append(f"  - {slug} ({label}): {desc}")
    return "\n".join(lines)
