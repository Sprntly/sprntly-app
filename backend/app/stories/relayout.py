"""Re-lay an EXISTING ticket set into a different ticket format, in place.

The tickets counterpart of `prd_runner.regenerate_prd_into_template`, but a
much smaller operation — deliberately. A ticket format governs only the
DESCRIPTION LAYOUT (`stories/layout.py`): which sections render, in what order,
under what labels. The ticket's CONTENT — title, the five canonical sections,
acceptance criteria, priorities, subtasks — is untouched, and so is its
identity: every story is rehydrated through `Story.from_dict`, which pins
`stable_id()` to the stored id, so tracker mappings (`jira_issue_map` and
friends), per-ticket edits and comments all stay attached across a switch.
That identity preservation is WHY this is a re-layout and not a regeneration:
a fresh generation would produce a whole new set of ids and orphan every synced
issue in the customer's live tracker.

Two legs:

  * THE SWAP (pure, instant): each story's `description_layout` is replaced
    with the target layout. Custom sections already in the story's
    `custom_sections` dict are KEPT even when the new layout doesn't reference
    them — they simply stop rendering, and switching back re-renders them,
    which is what makes the whole operation reversible without a versions
    table.
  * THE FILL (one gateway call, fail-open): a target layout may ask for custom
    sections the stories have no content for. Those are filled from what each
    ticket already says — never invented — in ONE batched call for the whole
    set. A failed or junk fill leaves the sections empty, and
    `Story.to_description` skips empty sections, so the switch still lands;
    the section appears once the company regenerates.
"""
from __future__ import annotations

import json
import logging
from typing import Any, Optional

from app.graph.gateway import llm_call
from app.stories.generate import Story
from app.stories.layout import TicketLayoutError, normalize_layout

logger = logging.getLogger(__name__)

FILL_PROMPT_VERSION = "ticket-relayout-fill-v1"

_CUSTOM_PREFIX = "custom:"

#: Grounding budget per ticket in the fill call — the fill reads a ticket, it
#: does not need the whole of a long one to write a two-line section.
_FILL_TICKET_CHARS = 2_000
#: One filled section's ceiling. Anything longer is the model writing an essay
#: into a description slot.
_FILL_SECTION_CHARS = 1_500
_FILL_MAX_TOKENS = 8_000


class TicketRelayoutError(ValueError):
    """A target format whose stored layout cannot be used. User-facing —
    the route turns it into a 409 the caller can act on."""


def layout_for_template(
    company_id: str, template_id: Optional[str]
) -> Optional[list[dict]]:
    """The target layout for an EXPLICIT switch, or None for the built-in.

    Deliberately NOT `resolve_ticket_layout`: that resolver treats None as "no
    preference" and falls back to the company's ACTIVE format — the exact wrong
    answer here, where None is the user choosing Sprntly's built-in layout (the
    same sentinel problem `prd_runner.BUILTIN_FORMAT` exists to solve; tickets
    spell the built-in as a bare None layout, so no sentinel is needed).

    The route has already validated the id (`_requested_template_id`: owned,
    right type, usable), so a row that still cannot yield a layout is a stored
    artifact gone bad — raised as `TicketRelayoutError` rather than silently
    swapped for a different layout than the one that was asked for.
    """
    if not template_id:
        return None

    from app.db.artifact_templates import get_template_by_id

    row = get_template_by_id(company_id, template_id)
    if row is None or row.get("artifact_type") != "tickets":
        raise TicketRelayoutError("That ticket format can't be used.")
    compiled = (row.get("compiled") or "").strip()
    if not compiled:
        raise TicketRelayoutError(
            "That ticket format hasn't finished compiling yet — try again in a "
            "moment."
        )
    try:
        layout = normalize_layout(json.loads(compiled))
    except (TypeError, ValueError, TicketLayoutError):
        logger.warning(
            "ticket format %s has an unusable stored layout company=%s",
            template_id, company_id,
        )
        raise TicketRelayoutError(
            "That ticket format's sections couldn't be read — re-upload it and "
            "try again."
        )
    return layout


def _custom_keys(layout: Optional[list[dict]]) -> list[tuple[str, str]]:
    """The (key, label) pairs of a layout's custom sections, in layout order."""
    out: list[tuple[str, str]] = []
    for entry in layout or []:
        source = str(entry.get("source") or "")
        if source.startswith(_CUSTOM_PREFIX):
            out.append((source[len(_CUSTOM_PREFIX):], str(entry.get("label") or "")))
    return out


def _fill_input(stories: list[Story], missing: dict[str, list[str]],
                labels: dict[str, str]) -> str:
    """The grounded fill request: each ticket's own content plus exactly the
    section keys it is missing. Clamped per ticket — the fill writes a short
    section, it does not need a long ticket in full."""
    lines: list[str] = []
    for s in stories:
        keys = missing.get(s.stable_id())
        if not keys:
            continue
        content = "\n".join(
            part for part in (
                f"Title: {s.title}",
                f"What: {s.what}" if s.what else "",
                f"Why now: {s.why_now}" if s.why_now else "",
                f"User story: {s.user_story or s.body}",
                "Scope:\n" + "\n".join(f"- {x}" for x in s.scope) if s.scope else "",
                f"Out of scope: {s.out_of_scope}" if s.out_of_scope else "",
                "Acceptance criteria:\n"
                + "\n".join(f"- {x}" for x in s.acceptance_criteria)
                if s.acceptance_criteria else "",
            ) if part
        )[:_FILL_TICKET_CHARS]
        wanted = ", ".join(f"{k} ({labels.get(k) or k})" for k in keys)
        lines.append(
            f"### Ticket {s.stable_id()}\n{content}\nSections to fill: {wanted}"
        )
    return "\n\n".join(lines)


_FILL_SYSTEM = (
    "A company switched its engineering-ticket format, and the new format asks "
    "for sections these existing tickets don't have yet. For each ticket, "
    "write the requested sections USING ONLY what that ticket already says — "
    "condense and restate its own content under the new heading. Never invent "
    "requirements, estimates, owners or facts the ticket does not contain. "
    "When a ticket genuinely has nothing to say for a section, OMIT that key "
    "entirely rather than padding it. Keep each section to a few sentences or "
    "a short list."
)

_FILL_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "tickets": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "id": {"type": "string", "description": "The ticket's id, exactly as given."},
                    "sections": {
                        "type": "object",
                        "description": (
                            "Filled sections keyed by the section KEY (not the "
                            "label). Omit keys the ticket has nothing for."
                        ),
                        "additionalProperties": {"type": "string"},
                    },
                },
                "required": ["id", "sections"],
            },
        }
    },
    "required": ["tickets"],
}


def _fill_custom_sections(
    enterprise_id: str, stories: list[Story], layout: list[dict]
) -> None:
    """Fill target-layout custom sections the stories lack, in place.

    ONE batched gateway call for the whole set, and fail-open at every step: a
    fill that errors, times out or returns junk leaves the sections empty —
    `to_description` skips empty sections, so the switch still lands and the
    ticket simply renders without them. Existing custom content is never
    overwritten: only keys with no content are requested, so a section carried
    over from a previous format (or hand-edited) survives the switch.
    """
    pairs = _custom_keys(layout)
    if not pairs:
        return
    labels = dict(pairs)
    missing: dict[str, list[str]] = {}
    for s in stories:
        keys = [k for k, _ in pairs if not (s.custom_sections or {}).get(k)]
        if keys:
            missing[s.stable_id()] = keys
    if not missing:
        return

    try:
        result = llm_call(
            enterprise_id=enterprise_id,
            agent="user_stories",
            purpose="ticket_relayout_fill",
            prompt_version=FILL_PROMPT_VERSION,
            system=_FILL_SYSTEM,
            input=_fill_input(stories, missing, labels),
            json_schema=_FILL_SCHEMA,
            temperature=0,
            max_tokens=_FILL_MAX_TOKENS,
        )
        rows = ((result.output or {}) if result else {}).get("tickets") or []
    except Exception:  # noqa: BLE001 — an empty section beats a failed switch
        logger.exception("ticket relayout fill failed (sections left empty)")
        return

    by_id = {s.stable_id(): s for s in stories}
    for row in rows:
        if not isinstance(row, dict):
            continue
        story = by_id.get(str(row.get("id") or ""))
        sections = row.get("sections")
        if story is None or not isinstance(sections, dict):
            continue
        wanted = set(missing.get(story.stable_id()) or [])
        for key, value in sections.items():
            text = str(value or "").strip()[:_FILL_SECTION_CHARS]
            # Only keys this ticket was actually missing — the model must not
            # overwrite content that already exists, and an invented key would
            # never render anyway (the layout is what drives rendering).
            if text and key in wanted:
                story.custom_sections[key] = text


def relayout_stories(
    enterprise_id: str, stories: list[dict], layout: Optional[list[dict]]
) -> list[dict]:
    """Every story re-stamped with `layout` (None = the built-in five sections),
    identities and content preserved, missing custom sections filled.

    Returns NEW dicts; the caller owns persistence. Non-dict entries in a
    stored array (defensive — the array is jsonb) are dropped, matching how
    every reader of these arrays already filters them.
    """
    hydrated = [Story.from_dict(s) for s in stories if isinstance(s, dict)]
    for s in hydrated:
        s.description_layout = list(layout) if layout else None
    if layout:
        _fill_custom_sections(enterprise_id, hydrated, layout)
    return [s.to_dict() for s in hydrated]


def run_switch(
    company_id: str,
    *,
    prd_id: Optional[int] = None,
    ticket_set_id: Optional[int] = None,
    stories: list[dict],
    layout: Optional[list[dict]],
    artifact_template_id: Optional[str],
) -> None:
    """The background half of POST /v1/stories/change-template: re-lay, persist,
    clear the marker. Blocking — the route hands it to a worker thread.

    The route has already run every gate (ownership, the set is `ready` with
    tickets in it, the target format resolves to `layout`) and written the
    in-flight marker, so this does the work and owns exactly one obligation:
    the marker must not outlive it. The happy path clears it inside the same
    update that lands the stories (`set_tickets_template` / `set_set_template`);
    this function's `except` covers everything else, because a marker left
    `running` by a raised fill is a Tickets tab that waits forever.

    Never raises. A failed switch leaves the tickets exactly as they were —
    nothing partial is written, since `relayout_stories` builds the whole new
    array before anything is persisted — and the client learns of it by the
    marker clearing with the format unchanged, the same way the PRD switch
    reports its own failures.
    """
    label = f"prd_id={prd_id}" if prd_id is not None else f"set_id={ticket_set_id}"
    try:
        relaid = relayout_stories(company_id, stories, layout)
        if prd_id is not None:
            from app.db.prd_tickets import set_tickets_template

            set_tickets_template(company_id, prd_id, relaid, artifact_template_id)
        else:
            from app.db.ticket_sets import set_set_template

            set_set_template(
                company_id, int(ticket_set_id), relaid, artifact_template_id
            )
        logger.info(
            "ticket format switch landed %s company=%s template=%s tickets=%d",
            label, company_id, artifact_template_id, len(relaid),
        )
    except Exception:  # noqa: BLE001 — a background job must not die loudly
        logger.warning(
            "ticket format switch failed %s company=%s template=%s — tickets "
            "left unchanged", label, company_id, artifact_template_id,
            exc_info=True,
        )
        try:
            if prd_id is not None:
                from app.db.prd_tickets import clear_tickets_relaying

                clear_tickets_relaying(company_id, prd_id)
            else:
                from app.db.ticket_sets import clear_set_relaying

                clear_set_relaying(company_id, int(ticket_set_id))
        except Exception:  # noqa: BLE001
            # The marker ages out on its own (RELAYOUT_STALE_AFTER_S), so a
            # failed cleanup costs a stale label for a few minutes, not a
            # permanently stuck tab.
            logger.warning(
                "couldn't clear the relayout marker %s company=%s", label,
                company_id, exc_info=True,
            )
