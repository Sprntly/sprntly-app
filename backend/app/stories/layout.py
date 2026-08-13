"""Ticket description layout — the shape a company's own ticket format gives
the description body.

Tickets are JSON-schema output, not markdown (`stories/generate.py::_SCHEMA`),
so an uploaded ticket format cannot change the FIELD SET the model fills. What
it governs is the DESCRIPTION LAYOUT: which sections appear, in what order,
under what labels, plus any extra sections of the company's own.

  - The five canonical sources survive: `what`, `why_now`, `user_story`,
    `scope`, `out_of_scope`. A layout may rename and reorder them.
  - `user_story` may NOT be dropped — a ticket without the notion of a user
    story is not a ticket this product produces, and `Story.body` mirrors it.
  - Anything else the format asks for becomes `custom:<key>`, filled from
    `Story.custom_sections`.

## Why every entry carries TWO labels

The five sections have always had two different label vocabularies, and the
round-trip depends on both:

  - the PUSH label, rendered bold into the tracker description
    (`**Scope**`)
  - the EDIT label, the plain-text form the web edits and the sync compares
    against (`The ticket must cover`)

`sync._IMPORT_LABELS` is the map between them, and `Scope` → `The ticket must
cover` is a real, shipped asymmetry. If the two ever disagree,
`normalize_imported_description` stops recognising the labels the push just
wrote, `content_hash(title, description)` differs on every pass, and the
tracker sync sees a permanent phantom diff on every ticket forever.

So a layout entry carries both, the default layout encodes the legacy
asymmetry exactly, and every mirror is DERIVED from the same list rather than
written out again. A custom layout uses one label for both — there is no legacy
rename to preserve for a section the customer just named.
"""
from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass

logger = logging.getLogger(__name__)

#: The canonical description fields a layout may place. Closed set — anything
#: else is either `custom:<key>` or rejected at the boundary.
CANONICAL_SOURCES: tuple[str, ...] = (
    "what", "why_now", "user_story", "scope", "out_of_scope",
)

#: The one source a layout may not drop.
REQUIRED_SOURCE = "user_story"

_CUSTOM_PREFIX = "custom:"

#: How many sections one layout may hold. A format with fifty headings is a
#: mis-upload, and every section costs prompt tokens on every ticket.
MAX_LAYOUT_SECTIONS = 24


@dataclass(frozen=True)
class LayoutEntry:
    """One description section. `push_label` is what the tracker sees (bold);
    `edit_label` is what the web edits and the sync compares."""

    source: str
    push_label: str
    edit_label: str

    @property
    def is_custom(self) -> bool:
        return self.source.startswith(_CUSTOM_PREFIX)

    @property
    def custom_key(self) -> str:
        return self.source[len(_CUSTOM_PREFIX):] if self.is_custom else ""


#: The shipped layout, expressed in the same structure a custom one uses. The
#: `Scope` / `The ticket must cover` split is the legacy asymmetry described in
#: the module docstring — it is load-bearing, not a typo.
DEFAULT_LAYOUT: tuple[LayoutEntry, ...] = (
    LayoutEntry("what", "What", "What"),
    LayoutEntry("why_now", "Why now", "Why now"),
    LayoutEntry("user_story", "User story", "User story"),
    LayoutEntry("scope", "Scope", "The ticket must cover"),
    LayoutEntry("out_of_scope", "Out of scope", "Out of scope"),
)


class TicketLayoutError(ValueError):
    """A compiled layout we refuse to store — the message is user-facing."""


def _slug(text: str) -> str:
    """A stable key for a custom section, derived from its label."""
    out = re.sub(r"[^a-z0-9]+", "_", (text or "").strip().lower()).strip("_")
    return out[:48]


# Heading text → canonical source. Matched on the slugified heading, longest
# phrase first, so "out of scope" is never swallowed by "scope".
_SOURCE_SYNONYMS: tuple[tuple[str, str], ...] = (
    ("out_of_scope", "out_of_scope"),
    ("not_in_scope", "out_of_scope"),
    ("non_goals", "out_of_scope"),
    ("out_of_bounds", "out_of_scope"),
    ("user_story", "user_story"),
    ("story", "user_story"),
    ("as_a_user", "user_story"),
    ("why_now", "why_now"),
    ("why", "why_now"),
    ("rationale", "why_now"),
    ("background", "why_now"),
    ("context", "why_now"),
    ("scope", "scope"),
    ("in_scope", "scope"),
    ("requirements", "scope"),
    ("the_ticket_must_cover", "scope"),
    ("what", "what"),
    ("summary", "what"),
    ("description", "what"),
    ("overview", "what"),
)


def _source_for_heading(heading: str) -> str | None:
    """The canonical source a heading names, or None for a custom section."""
    key = _slug(heading)
    if not key:
        return None
    for phrase, source in _SOURCE_SYNONYMS:
        if key == phrase or key.startswith(phrase + "_") or key.endswith("_" + phrase):
            return source
    return None


_HEADING_RE = re.compile(r"^\s{0,3}(#{1,6})\s+(.+?)\s*#*\s*$")
_BOLD_LINE_RE = re.compile(r"^\s*\*\*(.+?)\*\*\s*:?\s*$")


def compile_ticket_layout(source_md: str) -> list[dict]:
    """An uploaded ticket format (markdown) → an ordered description layout.

    Deterministic and stdlib-only: a ticket format is a list of section
    headings, and reading them needs a parser, not a model call. Headings are
    taken from markdown `#` lines and from bold-only lines (`**Scope**`), which
    is how these formats are usually written in Confluence exports.

    A heading that names one of the canonical sections maps to it; anything
    else becomes `custom:<slug>` and is filled by the model from the template's
    own hint. Every layout is put through `normalize_layout`, so the closed set
    and the user-story rule hold whatever the file said."""
    entries: list[dict] = []
    seen_sources: set[str] = set()
    for raw_line in (source_md or "").splitlines():
        m = _HEADING_RE.match(raw_line) or _BOLD_LINE_RE.match(raw_line)
        if not m:
            continue
        label = (m.group(2) if m.re is _HEADING_RE else m.group(1)).strip()
        if not label:
            continue
        source = _source_for_heading(label)
        if source is None:
            key = _slug(label)
            if not key:
                continue
            source = f"{_CUSTOM_PREFIX}{key}"
        # First heading wins for a given section: a format that mentions
        # "Scope" twice means one Scope section, not two competing ones.
        if source in seen_sources:
            continue
        seen_sources.add(source)
        entries.append({"label": label, "source": source})
    return normalize_layout(entries)


def normalize_layout(raw) -> list[dict]:
    """Validate and coerce a layout into the stored `[{label, source}]` shape.

    THE BOUNDARY for the closed set, mirroring how the PRD compiler enforces
    `SECTION_FORMS`: an unknown `source` is dropped here rather than stored,
    because a stored one would reach `to_description` and render a section with
    no content behind it on every ticket the company pushes.

    Raises TicketLayoutError only for a layout that cannot be repaired — one
    with no usable sections at all. A missing `user_story` is APPENDED rather
    than rejected: the format is otherwise fine, and silently losing the user
    story is worse than adding it back at the end."""
    entries: list[dict] = []
    seen: set[str] = set()
    for item in raw if isinstance(raw, list) else []:
        if not isinstance(item, dict):
            continue
        source = str(item.get("source") or "").strip()
        label = str(item.get("label") or "").strip()
        if not label:
            continue
        if source.startswith(_CUSTOM_PREFIX):
            key = _slug(source[len(_CUSTOM_PREFIX):])
            if not key:
                logger.warning("ticket layout dropped an unusable custom source")
                continue
            source = f"{_CUSTOM_PREFIX}{key}"
        elif source not in CANONICAL_SOURCES:
            # Not canonical and not declared custom — the compiler drifted.
            logger.warning(
                "ticket layout dropped unknown source=%r", source[:40]
            )
            continue
        if source in seen:
            continue
        seen.add(source)
        entries.append({"label": label, "source": source})
        if len(entries) >= MAX_LAYOUT_SECTIONS:
            break

    if not entries:
        raise TicketLayoutError(
            "We couldn't find any sections in this ticket format. Give it "
            "headings for the parts a ticket should have and try again."
        )
    if REQUIRED_SOURCE not in seen:
        # Never silently lose it — Story.body mirrors the user story, and the
        # web's editor and the tracker round-trip both key on the section.
        entries.append({"label": "User story", "source": REQUIRED_SOURCE})
    return entries


def resolve_layout(stored) -> tuple[LayoutEntry, ...]:
    """The stored `[{label, source}]` (or None) → the entries every renderer
    uses. None / empty / unusable ⇒ DEFAULT_LAYOUT, so a ticket generated
    before this feature and a company that never uploads a format both take the
    byte-identical legacy path."""
    if not stored:
        return DEFAULT_LAYOUT
    out: list[LayoutEntry] = []
    for item in stored if isinstance(stored, list) else []:
        if not isinstance(item, dict):
            continue
        source = str(item.get("source") or "").strip()
        label = str(item.get("label") or "").strip()
        if not label or (
            source not in CANONICAL_SOURCES and not source.startswith(_CUSTOM_PREFIX)
        ):
            continue
        # A custom layout uses ONE label for both vocabularies: there is no
        # legacy rename to preserve for a section the customer just named.
        out.append(LayoutEntry(source=source, push_label=label, edit_label=label))
    return tuple(out) if out else DEFAULT_LAYOUT


def layout_prompt_hint(stored) -> str:
    """The per-key hint the generator carries for a custom layout, or "" for
    the default.

    Only the CUSTOM sections need describing — the canonical five are already
    specified in the schema, and repeating them would be prompt weight for no
    information."""
    entries = resolve_layout(stored)
    if entries is DEFAULT_LAYOUT:
        return ""
    customs = [e for e in entries if e.is_custom]
    if not customs:
        return ""
    lines = [
        "This company's ticket format also asks for these sections. Fill each "
        "one in `custom_sections` under the key given, grounded in the PRD — "
        "leave a key out entirely rather than inventing content for it:",
    ]
    lines += [f"- {e.custom_key}: {e.push_label}" for e in customs]
    return "\n".join(lines)


def resolve_ticket_layout(
    company_id: str | None, override_id: str | None = None
) -> tuple[list[dict] | None, str | None]:
    """The ticket description layout to write with, and the id of the format it
    came from — or (None, None) for Sprntly's default.

    `override_id` is a format this run was told to use — the one the user named
    in chat ("break this into tickets using our Acme ticket format"), resolved to
    an id by the Ask planner and validated at the route. It outranks whatever is
    active, because a company-wide activation cannot express "this one, this
    time". None — the normal case — means the active format, which is what every
    request got before the override existed.

    An override that cannot be used falls back to the ACTIVE format and is
    logged at WARNING, the same degradation and for the same reason as
    `prd_runner.resolve_prd_template`: the route has already refused the ids a
    user could do something about, so reaching here means the row moved under a
    run in flight, and failing the tickets would be worse than reshaping them.

    Same shape and same posture as `prd_runner.resolve_prd_template`: default
    first for the common case, a DB read only when there is a company, and
    **fail open** — any error returns the default and tickets still generate.

    The gate is `compiled != ""`, not `compile_status == "ready"`, for the same
    reason it is on the PRD path: gating on status would drop a company back to
    the default layout for the whole duration of a recheck, silently changing
    the shape of every ticket pushed in that window.

    A ticket format's compiled artifact IS the layout: the compiler stores the
    `[{label, source}]` JSON in `compiled`. It is re-normalised here rather than
    trusted, so a row written by an older build — or by hand — still cannot put
    an unknown `source` in front of `to_description`."""
    if not company_id:
        return None, None

    row = None
    if override_id:
        try:
            from app.db.artifact_templates import get_template_by_id

            row = get_template_by_id(company_id, override_id)
        except Exception:  # noqa: BLE001 — degrade to the active format below
            row = None
        # Company-filtered by the read, so a foreign id is already a miss. The
        # TYPE check is this layer's own: a PRD format's compiled artifact is an
        # HTML skeleton, not a `[{label, source}]` layout, so honouring one here
        # would hand `normalize_layout` something it cannot read.
        if row is not None and row.get("artifact_type") != "tickets":
            row = None
        if row is None:
            logger.warning(
                "ticket format %s was requested for company=%s and could not be "
                "used — these tickets are NOT in the format that was asked for",
                override_id, company_id,
            )

    if row is None:
        try:
            from app.db.artifact_templates import get_active_template

            row = get_active_template(company_id, "tickets")
        except Exception:  # noqa: BLE001 — any DB failure degrades to the default
            logger.warning(
                "active ticket format lookup failed for company=%s; using the default",
                company_id, exc_info=True,
            )
            return None, None
    if not row:
        return None, None
    compiled = (row.get("compiled") or "").strip()
    if not compiled:
        return None, None
    try:
        raw = json.loads(compiled)
    except (TypeError, ValueError):
        logger.warning(
            "active ticket format has an unreadable layout company=%s; using the default",
            company_id,
        )
        return None, None
    try:
        layout = normalize_layout(raw)
    except TicketLayoutError:
        logger.warning(
            "active ticket format compiled to an unusable layout company=%s",
            company_id,
        )
        return None, None
    return layout, row.get("id")
