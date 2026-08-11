"""The company's own library — its uploaded SKILLS and FORMATS — rendered for
an answer.

Asked for by the planner (`ask_planner.Plan.include_library`) and executed on
the answer path, this is what makes "what skills do I have", "which PRD format
is active" and "why isn't my format being used" answerable at all. Before it,
those questions reached a model that had the company's knowledge graph in front
of it and no idea what was in the company's library, so the honest outcomes were
silence and the dishonest one was a plausible list of things nobody uploaded.

TWO LIBRARIES, ONE BLOCK, and the distinction is the thing users actually get
wrong: a SKILL is a METHOD (how the work is done — the team's own way of writing
a churn autopsy) and a FORMAT is a FORM (what the finished document looks like).
They are uploaded on different screens, they are scoped the same way — by
COMPANY, never by workspace — and a question about "my templates" is about the
second while a question about "my methods" is about the first.

WHAT THIS DELIBERATELY DOES NOT LIST: Sprntly's own built-in capabilities. The
owner's call (2026-08-10) is that "what skills do I have" means the customer's
own uploads, not a catalogue of the product — the system prompt already
describes what the assistant can do, and duplicating it here would put a second,
drifting copy of that list inside every library answer.

Never raises, and returns "" for a company with nothing to show — an empty
library is a real state with real copy ("you haven't uploaded any yet"), but
that sentence belongs to the answer model reading a block that says so, not to a
block that fabricates one for a company whose read simply failed.
"""
from __future__ import annotations

import logging
from typing import Optional

logger = logging.getLogger(__name__)

# Bounds on what reaches the prompt. Larger than the planner's own caps
# (`ask_planner._MAX_PLANNER_TEMPLATES`) because this block IS the answer rather
# than a menu to choose from: a company with forty skills asking "what do I
# have" should get forty, and the description clamp is what keeps that bounded.
_MAX_SKILLS = 60
_MAX_TEMPLATES = 60
_NAME_CHARS = 120
_DESCRIPTION_CHARS = 240

# Stored artifact_type → the words a person uses. Same mapping (and same reason)
# as `artifact_templates.store.ARTIFACT_TYPE_LABELS`: nobody calls it
# `impl_spec` out loud.
_KIND_LABELS: dict[str, str] = {
    "prd": "PRD",
    "tickets": "Tickets",
    "impl_spec": "Engineering spec",
}

# The order the kinds are always listed in — PRD first because it is the one
# most companies upload, and FIXED because a block whose sections reshuffle
# between two answers makes the assistant look like it is reading different data
# each time.
_KIND_ORDER: tuple[str, ...] = ("prd", "tickets", "impl_spec")

# Kinds this ANSWER never mentions. Mirrors `HIDDEN_ARTIFACT_TYPE_IDS` in
# web/app/lib/compileNotes.ts, which withheld engineering-spec formats from
# every screen (owner, 2026-08-06) so a user is asked to understand two document
# types instead of three while that settles.
#
# The chat is a surface too, and it was the one that forgot: this block listed
# "Engineering spec templates: (None uploaded yet.)" to a user whose Templates
# screen has no such group and whose upload modal offers no such chip —
# advertising a type they cannot act on and then explaining they have none of it.
#
# HIDES, NEVER CHANGES BEHAVIOUR, exactly as the web set does: an engineering
# spec is still generated for every PRD, tickets still inherit their acceptance
# criteria from its EARS requirements, and a company that already activated an
# engineering-spec format keeps generating into it. Only the mention is
# withheld. Emptying this set and the web one together restores the type.
_HIDDEN_KINDS: frozenset[str] = frozenset({"impl_spec"})

#: The kinds any answer may name. Derived, so this block and the screens can
#: never disagree about what exists.
_VISIBLE_KINDS: tuple[str, ...] = tuple(
    k for k in _KIND_ORDER if k not in _HIDDEN_KINDS
)

# Where a person goes to change any of this. Named in the block rather than left
# to the answer model, which would otherwise invent a plausible settings path.
_SKILLS_SCREEN = "Skills"
_TEMPLATES_SCREEN = "Templates"


def _one_line(text: Optional[str], limit: int) -> str:
    """Customer free text, flattened to one clamped line.

    Collapse-then-clamp, in that order, for the reason `qa_agent
    ._custom_skill_line` states: a newline inside an uploaded name or
    description would otherwise let it forge a list line or a section header
    inside this block, and clamping first can leave a newline inside the kept
    slice."""
    flat = " ".join((text or "").split())
    if len(flat) > limit:
        flat = flat[:limit].rstrip() + "…"
    return flat


def _skill_lines(enterprise_id: str) -> tuple[list[str], bool]:
    """`(lines, read_ok)` for the company's uploaded skills.

    `read_ok` is what separates "this company has no skills" from "we could not
    find out", and the caller needs both: the first is a fact worth telling the
    user, the second must never be reported as one.

    A skill whose slug collides with a vendored built-in is listed and MARKED,
    not hidden. `resolve_skill` is built-in-first, so such a skill genuinely
    cannot be invoked by its trigger — but it is sitting on the company's Skills
    screen, so answering "you have no skill called that" to someone looking
    straight at it is the worse of the two wrongs. Collisions are legacy only:
    `skills.custom.available_slug` gives every new upload a free trigger.
    """
    try:
        from app.db.custom_skills import list_custom_skills
        from app.skills.loader import list_skills
    except Exception:  # noqa: BLE001 — an import failure is a failed read
        logger.warning("library: skills modules unavailable", exc_info=True)
        return [], False

    try:
        rows = list_custom_skills(enterprise_id)
    except Exception:  # noqa: BLE001 — never break an answer over a library read
        logger.warning("library: custom-skill read failed", exc_info=True)
        return [], False

    try:
        builtin = set(list_skills())
    except Exception:  # noqa: BLE001 — only costs the collision marker
        builtin = set()

    lines: list[str] = []
    for row in (rows or [])[:_MAX_SKILLS]:
        slug = _one_line(row.get("slug"), _NAME_CHARS)
        if not slug:
            continue
        name = _one_line(row.get("name"), _NAME_CHARS) or slug
        description = _one_line(row.get("description"), _DESCRIPTION_CHARS)
        line = f"- {name} (trigger: /{slug})"
        if description:
            line += f" — {description}"
        if slug in builtin:
            line += (
                " [NOTE: this name is also one of Sprntly's built-in methods, "
                "which takes precedence, so /"
                f"{slug} runs the built-in rather than this upload]"
            )
        lines.append(line)
    return lines, True


def _template_lines(enterprise_id: str) -> tuple[dict[str, list[str]], bool]:
    """`({artifact_type: lines}, read_ok)` for the company's uploaded formats.

    Grouped by kind because that is how the question is asked — "do we have a
    ticket format" is about one group — and every kind gets a group even when it
    is empty, so the answer can say "no ticket format" as a fact rather than by
    noticing an absence.

    Each line carries the two states that decide whether a format does anything:
    whether it is ACTIVE (the one applied automatically) and whether it compiled
    cleanly (an uploaded format that never compiled governs nothing, which is
    the single most confusing thing about this feature and the thing users write
    in about).
    """
    try:
        from app.db.artifact_templates import list_templates
    except Exception:  # noqa: BLE001
        logger.warning("library: template module unavailable", exc_info=True)
        return {}, False

    try:
        rows = list_templates(enterprise_id)
    except Exception:  # noqa: BLE001 — never break an answer over a library read
        logger.warning("library: format read failed", exc_info=True)
        return {}, False

    # Grouped by VISIBLE kind: a row of a hidden kind falls through the
    # membership check below and is skipped, so an engineering-spec format a
    # company uploaded before the type was withheld is not named here either.
    grouped: dict[str, list[str]] = {kind: [] for kind in _VISIBLE_KINDS}
    for row in (rows or [])[:_MAX_TEMPLATES]:
        kind = row.get("artifact_type") or ""
        if kind not in grouped:
            # Either a WITHHELD kind (`_HIDDEN_KINDS` — deliberate, and the
            # common case) or one this build does not know about: a row written
            # by a newer deploy, or by hand. Listing an unknown one under a
            # guessed heading would be a confident mislabel, so both are skipped.
            logger.info("library: not naming a format of kind %r", kind[:40])
            continue
        name = _one_line(row.get("name"), _NAME_CHARS) or "(untitled)"
        status = row.get("compile_status")
        if row.get("is_active") and status == "ready":
            state = "ACTIVE — every new one is written in this format"
        elif row.get("is_active"):
            state = (
                f"ACTIVE, currently being re-checked ({status or 'pending'}) — "
                "documents keep using its last good version meanwhile"
            )
        elif status == "ready":
            state = "ready, but not active — activate it to start using it"
        else:
            state = (
                f"not usable yet ({status or 'pending'}) — it hasn't passed the "
                "format check, so it governs nothing"
            )
        uploader = _one_line(row.get("uploader_name"), _NAME_CHARS)
        line = f"- {name} — {state}"
        if uploader:
            line += f" (uploaded by {uploader})"
        grouped[kind].append(line)
    return grouped, True


def library_block(enterprise_id: Optional[str]) -> str:
    """The company's skills and formats, as a context section for the answer.

    Returns "" when there is no tenant or when BOTH reads failed — a block that
    said "you have nothing" because a query timed out would be a confident lie
    about the user's own data, and no block at all degrades to the answer the
    user got before this existed.

    A half-failed read still renders: the half that succeeded is true, and the
    section for the half that did not says it could not be read rather than
    saying it is empty."""
    if not enterprise_id:
        return ""

    skills, skills_ok = _skill_lines(enterprise_id)
    templates, templates_ok = _template_lines(enterprise_id)
    if not skills_ok and not templates_ok:
        return ""

    parts: list[str] = [
        "=== THIS WORKSPACE'S SKILLS AND TEMPLATES ===",
        "This is the complete, current list of what this company has uploaded. "
        "It is authoritative: if something is not here, they have not uploaded "
        "it. Never name a skill or a template that does not appear below.",
        # The section is titled with the customer's own word, and says so, for
        # the reason the incident showed: the document index is full of
        # Confluence pages called "Template - …", and an answer model with both
        # in front of it counted the wiki pages as templates. Naming the
        # collision here — in the block that IS the answer — is what stops it.
        "TEMPLATES here means these uploaded formats and nothing else. A "
        "Confluence or Drive page titled \"Template - …\" is a wiki page their "
        "team writes in; it governs no Sprntly document and is never one of "
        "their templates.",
        "",
        "SKILLS (methods — how a piece of work is done). Uploaded and managed on "
        f"the {_SKILLS_SCREEN} screen, and shared by everyone in the company.",
    ]
    if not skills_ok:
        parts.append("(This list could not be read just now — say so rather than "
                     "reporting it as empty.)")
    elif not skills:
        parts.append("(None uploaded yet.)")
    else:
        parts.extend(skills)

    parts += [
        "",
        "TEMPLATES (also called formats — what the finished document looks "
        f"like). Uploaded and managed on the {_TEMPLATES_SCREEN} screen. One "
        "per kind can be ACTIVE, and the active one is applied automatically to "
        "every new document of that kind — nobody has to ask for it.",
    ]
    if not templates_ok:
        parts.append("(This list could not be read just now — say so rather than "
                     "reporting it as empty.)")
    else:
        for kind in _VISIBLE_KINDS:
            lines = templates.get(kind) or []
            parts.append(f"{_KIND_LABELS[kind]} templates:")
            parts.extend(lines or ["- (None uploaded yet.)"])

    return "\n".join(parts)
