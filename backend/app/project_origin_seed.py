"""Seed a freshly created project's memory with its ORIGIN context — the
grounded "why" a newcomer's join-greeting (`app/project_join_greeting.py`)
and the project's memory summary both read.

Generalized across ALL project origins (private-first memory wave): a
`prd_auto` project seeds from the originating chat + PRD; a `manual` or
`artifact` project seeds from its name plus whatever grounding text is
available at creation (the creator's first message/instructions, or the
seeding artifact's own title/excerpt) — never fabricated when that text
isn't available, in which case a name-only deterministic brief is written
instead. Before this wave, only `prd_auto` ever got a "why" — manual/
artifact projects opened with memory permanently empty.

`prd_auto` (unchanged from before this wave — when a PRD generated in the
main chat auto-forks into a NEW project via
`app/project_from_prd.py`'s new-project branch, the reasoning that led to
that PRD lives only in the originating chat thread, invisible to a teammate
who opens the project cold):
  - a brief "what this PRD is / the problem it addresses" summary, and
  - the concrete decisions/trade-offs the conversation actually settled on.
Grounded strictly in what was said + the PRD's own content (never invented).

Non-empty guarantee: memory is ALWAYS seeded with at least a grounded brief,
for every origin. For `prd_auto`, when the conversation is thin/empty the
brief is drawn from the PRD content itself; when the LLM summarizer fails, a
deterministic PRD-derived brief (title + the PRD's opening prose — the PRD's
own words, so still grounded) is written instead — only a total absence of
PRD content leaves memory unseeded. For `manual`/`artifact`, the equivalent
floor is a name-only deterministic brief when there's no grounding text or
the summarizer fails — only a missing project name leaves memory unseeded.

Best-effort (AD-P7): never raises. By the time this runs the project (and,
for `prd_auto`, its PRD artifact and conversation binding) is already
committed, so any failure just means memory is seeded with less (never
blocks creation). Reuses the project's established primitives rather than
inventing new ones (AD-P3 — no second writer, no second summary table):
  - memory write  → `project_memory_entries.add_agent_promoted_entry`
    (the same agent-promoted, `source_conversation_id`-tagged shape the
    project-chat promoter writes), then `project_memory.schedule_regen`.
  - LLM call      → `app.llm.call_json` + `DEFAULT_MODEL`, one bounded call,
    same client/model as `app/project_memory.py::maybe_promote_turn`.
  - cost telemetry → `app.llm_telemetry.log_llm_run`, mirroring
    `project_memory.py::_log_promotion_run`'s one-line-per-run shape.

`prd_auto` is seeded ONLY from the new-project branch of
`maybe_auto_create_project_for_prd` — a PRD added to an already-bound
project is not re-seeded (that branch returns before ever calling here).
`manual`/`artifact` are seeded once, best-effort, from the project-create
route (`routes/projects.py::create_project`).
"""
from __future__ import annotations

import logging
import time

from app.db import prds as prds_db
from app.db import project_memory_entries as memory_db
from app.db.client import require_client
from app.llm import DEFAULT_MODEL, call_json
from app.llm_telemetry import RunUsage, log_llm_run
from app.project_memory import schedule_regen

logger = logging.getLogger(__name__)

# Bound the raw material fed to the model — same posture as
# `app.project_memory._CONTENT_MAX_CHARS`: a long thread or a large PRD must
# not blow the prompt window or the spend on a one-shot origin summary.
_TURNS_MAX_CHARS = 18_000
_PRD_MAX_CHARS = 6_000
# Never flood a fresh project's memory: cap the discrete decision entries.
_MAX_DECISIONS = 6
# The deterministic fallback brief pulls the PRD's opening prose — kept short.
_FALLBACK_PRD_CHARS = 320

_SYSTEM = """You seed the shared "project memory" for a NEW product project \
that was just auto-created from a PRD generated in a chat. You are given the \
originating chat conversation (the human's messages and the assistant's \
replies that led to the PRD) and the PRD itself (title + body).

Produce two things, GROUNDED STRICTLY in the material provided — never \
invent a decision, constraint, or rationale that is not actually present:

1. brief_summary: 1-3 plain sentences stating what this PRD/project is and \
the problem or goal it addresses. Draw it from the PRD and the conversation. \
ALWAYS produce this — even if the conversation is empty, summarize the PRD \
itself. Never leave it blank.

2. decisions: the KEY decisions, trade-offs, and reasoning the conversation \
actually settled on that led to this PRD — each as its own short, \
self-contained statement (what was decided and, briefly, why). Include a \
point ONLY if the conversation genuinely contains it. If the conversation is \
thin or contains no real decisions/reasoning, return an EMPTY list — do not \
manufacture points to fill it (the brief_summary alone is enough).

Hard rules:
- No markdown headings or bullet characters inside a string. Plain prose.
- Do not address the reader or offer next steps ("Want me to…", "Shall I…"). \
State the fact; stop.
- Every decision must be traceable to something actually said in the \
conversation. When in doubt, leave it out.
"""

_SCHEMA = {
    "type": "object",
    "properties": {
        "brief_summary": {"type": "string"},
        "decisions": {"type": "array", "items": {"type": "string"}},
    },
    "required": ["brief_summary", "decisions"],
    "additionalProperties": False,
}

# The `manual`/`artifact` branch has no chat conversation and no PRD to
# summarize — just the project's own name plus whatever grounding text the
# create route was given (a first message/instructions, or an excerpt of
# the seeding artifact). One field only: there is no separate "decisions"
# to extract from a bare name + short excerpt the way there is from a real
# conversation.
_SEED_TEXT_MAX_CHARS = 4_000

_SYSTEM_GENERIC = """You seed the shared "project memory" for a NEW product \
project that was just created — manually, or from an existing artifact — \
rather than auto-forked from a PRD chat. You are given the project's name \
and, when available, grounding text in the creator's own words: either \
their first message/instructions, or an excerpt of the artifact the \
project was seeded from.

Produce ONE thing, GROUNDED STRICTLY in the material provided — never \
invent a goal, constraint, or rationale that is not actually present:

brief_summary: 1-3 plain sentences stating what this project is for and the \
problem or goal it addresses, drawn from the project name and the supplied \
text. If the supplied text says little, say little — do not pad it out \
with invented detail.

Hard rules:
- No markdown headings or bullet characters inside the string. Plain prose.
- Do not address the reader or offer next steps ("Want me to…", "Shall I…"). \
State the fact; stop.
"""

_SCHEMA_GENERIC = {
    "type": "object",
    "properties": {
        "brief_summary": {"type": "string"},
    },
    "required": ["brief_summary"],
    "additionalProperties": False,
}


def _clip(text: str, cap: int) -> str:
    return text if len(text) <= cap else text[:cap]


def _read_turns(conversation_id: int) -> str:
    """The originating conversation's turns as plain `role: content` lines,
    oldest first — the raw reasoning material. Reads `conversation_turns`
    directly (the conversation was already company-ownership-validated
    before this module is reached — the seed only runs from
    `maybe_auto_create_project_for_prd`'s new-project branch, right after
    the caller's own binding). Returns "" on any failure or an empty
    thread."""
    rows = (
        require_client()
        .table("conversation_turns")
        .select("role, content")
        .eq("conversation_id", conversation_id)
        .order("id")
        .execute()
        .data
        or []
    )
    lines = []
    for row in rows:
        content = (row.get("content") or "").strip()
        if not content:
            continue
        role = "Assistant" if row.get("role") == "assistant" else "User"
        lines.append(f"{role}: {content}")
    return _clip("\n\n".join(lines), _TURNS_MAX_CHARS)


def _read_prd_body(prd_id: int) -> str:
    """The PRD's human body (`payload_md`), or "" when unavailable — read
    once and reused for both the LLM gist and the deterministic fallback."""
    try:
        prd = prds_db.get_prd(prd_id)
        if prd:
            return (prd.get("payload_md") or "").strip()
    except Exception:  # noqa: BLE001 — best-effort, title alone is enough
        pass
    return ""


def _prd_gist(prd_title: str, prd_body: str) -> str:
    """`title` + a clip of the PRD body — the source for the "what the PRD
    is" half of the summary. Title alone when there's no body."""
    gist = f"PRD title: {prd_title}"
    if prd_body:
        gist += f"\n\nPRD body:\n{_clip(prd_body, _PRD_MAX_CHARS)}"
    return gist


def _first_prose(prd_body: str) -> str:
    """The PRD's opening prose paragraph, markdown decoration stripped —
    used verbatim (the PRD's own words) in the deterministic fallback brief,
    so it is grounded, never fabricated. "" when the body has no prose."""
    para: list[str] = []
    for raw in prd_body.splitlines():
        line = raw.strip().lstrip("#>-*").strip()
        if not line:
            if para:  # end of the first non-empty paragraph
                break
            continue
        para.append(line)
    text = " ".join(para).strip()
    if len(text) > _FALLBACK_PRD_CHARS:
        text = text[:_FALLBACK_PRD_CHARS].rstrip() + "…"
    return text


def _fallback_brief(prd_title: str, prd_body: str) -> str:
    """A deterministic, grounded brief drawn from the PRD ITSELF — the
    non-empty guarantee's floor when the LLM summarizer fails or returns a
    blank brief. Combines the PRD title with its opening prose (the PRD's own
    words); title-only when the body has no usable prose. "" only when there
    is no title at all (nothing grounded to say)."""
    title = (prd_title or "").strip()
    if not title:
        return ""
    prose = _first_prose(prd_body)
    if prose:
        return f'This project was created from the PRD "{title}". {prose}'
    return f'This project was created from the PRD "{title}".'


def _fallback_brief_generic(project_name: str | None) -> str:
    """A deterministic, name-only brief — the `manual`/`artifact` floor when
    there is no grounding text to summarize (or the LLM summarizer fails).
    Grounded only in the project's own name, exactly like `_fallback_brief`
    is grounded only in the PRD's own words; never fabricates a "why".
    "" only when there is no name at all (nothing grounded to say)."""
    name = (project_name or "").strip()
    if not name:
        return ""
    return f'This project, "{name}", was just created.'


def _log_seed_run(
    *, project_id: int, meta: dict, start: float, entries: int, used_fallback: bool,
    prd_id: int | None = None, conversation_id: int | None = None,
) -> None:
    """The one structured cost-summary line for a successful seed run —
    identifiers + counts + a bool only, mirroring
    `project_memory.py::_log_promotion_run`'s shape (never raises; a
    logging hiccup must never be the reason the seed fails). `prd_id`/
    `conversation_id` are omitted from `identifier` for the `manual`/
    `artifact` branches, which have neither."""
    identifier: dict = {"project_id": project_id}
    if prd_id is not None:
        identifier["prd_id"] = prd_id
    if conversation_id is not None:
        identifier["conversation_id"] = conversation_id
    try:
        log_llm_run(
            operation="projects.memory.origin_seed",
            identifier=identifier,
            usage=RunUsage(
                cache_creation_input_tokens=meta.get("cache_creation_input_tokens", 0),
                cache_read_input_tokens=meta.get("cache_read_input_tokens", 0),
                input_tokens=meta.get("input_tokens", 0),
                output_tokens=meta.get("output_tokens", 0),
            ),
            duration_ms=int((time.monotonic() - start) * 1000),
            status="complete",
            model=meta.get("model") or DEFAULT_MODEL,
            entries=entries,
            used_fallback=used_fallback,
        )
    except Exception:  # noqa: BLE001 — observability must never break the seed
        logger.warning("project_origin_seed_cost_log_failed project_id=%s", project_id)


def _seed_prd_auto(*, project_id: int, prd_id: int, prd_title: str, conversation_id: int) -> None:
    """The ORIGINAL `prd_auto` seed body — byte-identical behaviour to
    before this wave's generalization, just relocated under the new
    per-origin dispatch in `seed_project_origin_memory`."""
    start = time.monotonic()
    turns = _read_turns(conversation_id)
    prd_body = _read_prd_body(prd_id)
    fallback = _fallback_brief(prd_title, prd_body)

    brief = ""
    decisions: list[str] = []
    meta: dict = {}
    # The summarizer is the ONLY failure-prone step; isolate it so a
    # summarizer error still falls through to the deterministic fallback
    # rather than skipping the seed entirely.
    try:
        out = call_json(
            system=_SYSTEM,
            user=(
                f"Originating conversation:\n"
                f"{turns or '(the conversation has no recorded messages)'}\n\n"
                f"{_prd_gist(prd_title, prd_body)}"
            ),
            model=DEFAULT_MODEL,
            schema=_SCHEMA,
            meta_out=meta,
        )
        brief = str(out.get("brief_summary") or "").strip()
        decisions = [
            str(d).strip()
            for d in (out.get("decisions") or [])
            if isinstance(d, str) and str(d).strip()
        ][:_MAX_DECISIONS]
    except Exception as exc:  # noqa: BLE001 — fall through to the fallback brief
        logger.warning(
            "project_origin_seed_summarize_failed project_id=%s prd_id=%s error=%s",
            project_id, prd_id, type(exc).__name__,
        )

    # Non-empty floor: never let a blank/failed summary leave memory empty.
    used_fallback = not brief
    if not brief:
        brief = fallback

    bodies = ([brief] if brief else []) + decisions
    if not bodies:
        # Only reachable when there is no PRD content to ground even a
        # fallback brief — nothing to say, so nothing is written.
        logger.warning(
            "project_origin_seed_empty project_id=%s prd_id=%s conversation_id=%s",
            project_id, prd_id, conversation_id,
        )
        return

    written = 0
    for body in bodies:
        memory_db.add_agent_promoted_entry(
            project_id, body=body, source_conversation_id=conversation_id
        )
        written += 1

    # `add_agent_promoted_entry` only flips summary `stale`; regen the
    # cached summary once for the whole seed (write happens outside an
    # HTTP handler — same contract as the project-chat promoter).
    schedule_regen(project_id)
    _log_seed_run(
        project_id=project_id, prd_id=prd_id, conversation_id=conversation_id,
        meta=meta, start=start, entries=written, used_fallback=used_fallback,
    )


def _seed_generic(*, project_id: int, origin: str, project_name: str | None, seed_text: str | None) -> None:
    """The `manual`/`artifact` seed body: a single `brief_summary` entry
    grounded in the project's name plus whatever grounding text the
    create route was given — no chat, no PRD, so no "decisions" list.
    When `seed_text` is empty there is nothing to summarize, so this skips
    the LLM call entirely and writes the name-only fallback directly
    (cost-conscious — a call with nothing to say would just reproduce the
    fallback anyway)."""
    start = time.monotonic()
    name = (project_name or "").strip()
    text = (seed_text or "").strip()
    fallback = _fallback_brief_generic(name)

    brief = ""
    meta: dict = {}
    if text:
        kind = "the creator's first message/instructions" if origin == "manual" else "the seeding artifact"
        try:
            out = call_json(
                system=_SYSTEM_GENERIC,
                user=(
                    f"Project name: {name or '(untitled)'}\n\n"
                    f"Grounding text ({kind}):\n{_clip(text, _SEED_TEXT_MAX_CHARS)}"
                ),
                model=DEFAULT_MODEL,
                schema=_SCHEMA_GENERIC,
                meta_out=meta,
            )
            brief = str(out.get("brief_summary") or "").strip()
        except Exception as exc:  # noqa: BLE001 — fall through to the fallback brief
            logger.warning(
                "project_origin_seed_summarize_failed project_id=%s origin=%s error=%s",
                project_id, origin, type(exc).__name__,
            )

    used_fallback = not brief
    if not brief:
        brief = fallback

    if not brief:
        # Only reachable when there is no project name at all — nothing
        # grounded to say, so nothing is written.
        logger.warning("project_origin_seed_empty project_id=%s origin=%s", project_id, origin)
        return

    memory_db.add_agent_promoted_entry(project_id, body=brief, source_conversation_id=None)
    schedule_regen(project_id)
    _log_seed_run(
        project_id=project_id, meta=meta, start=start, entries=1, used_fallback=used_fallback,
    )


def seed_project_origin_memory(
    *,
    project_id: int,
    origin: str,
    prd_id: int | None = None,
    prd_title: str | None = None,
    conversation_id: int | None = None,
    project_name: str | None = None,
    seed_text: str | None = None,
) -> None:
    """Best-effort: seed the freshly created project's memory with a
    grounded "why", branching on `origin`. Never raises — any failure is
    logged and swallowed so project creation is untouched.

    `origin="prd_auto"` (unchanged from before this wave): summarizes the
    originating chat + PRD (`prd_id`, `prd_title`, `conversation_id` — all
    required for this branch) into a brief summary PLUS each key decision
    as its own entry, all tagged with `source_conversation_id=conversation_id`.

    `origin="manual"` / `origin="artifact"`: summarizes `project_name` +
    `seed_text` (the creator's first message/instructions for `manual`; the
    seeding artifact's title/excerpt for `artifact`) into a single brief
    summary entry, `source_conversation_id=None` (no conversation exists for
    either origin). Falls back to a name-only deterministic brief when
    `seed_text` is empty or the summarizer fails.

    Every branch schedules exactly ONE summary regen and never raises
    (AD-P7) — a seed failure never blocks project creation.
    """
    try:
        if origin == "prd_auto":
            _seed_prd_auto(
                project_id=project_id, prd_id=prd_id, prd_title=prd_title or "",
                conversation_id=conversation_id,
            )
        else:
            _seed_generic(
                project_id=project_id, origin=origin,
                project_name=project_name, seed_text=seed_text,
            )
    except Exception:  # noqa: BLE001 — best-effort, AD-P7: never break project creation
        logger.warning(
            "project_origin_seed_failed project_id=%s origin=%s prd_id=%s conversation_id=%s",
            project_id, origin, prd_id, conversation_id, exc_info=True,
        )
