"""Seed a freshly auto-created project's memory with its ORIGIN context.

When a PRD generated in the main chat auto-forks into a NEW project
(`app/project_from_prd.py`'s new-project branch), the reasoning that led to
that PRD lives only in the originating chat thread — invisible to a teammate
who opens the project cold. This seeds the new project's memory so a
project born from a PRD opens with POPULATED memory, never a blank slate:
  - a brief "what this PRD is / the problem it addresses" summary, and
  - the concrete decisions/trade-offs the conversation actually settled on.
Grounded strictly in what was said + the PRD's own content (never invented).

Non-empty guarantee: memory is ALWAYS seeded with at least a grounded brief
on this path. When the conversation is thin/empty, the brief is drawn from
the PRD content itself; when the LLM summarizer fails, a deterministic
PRD-derived brief (title + the PRD's opening prose — the PRD's own words, so
still grounded) is written instead. Only a total absence of PRD content
leaves memory unseeded.

Best-effort (AD-P7): never raises. By the time this runs the project, its
PRD artifact, and the conversation binding are already committed, so any
failure just means memory is seeded with less (never blocks creation).
Reuses the project's established primitives rather than inventing new ones
(AD-P3 — no second writer, no second summary table):
  - memory write  → `project_memory_entries.add_agent_promoted_entry`
    (the same agent-promoted, `source_conversation_id`-tagged shape the
    group-chat promoter writes), then `project_memory.schedule_regen`.
  - LLM call      → `app.llm.call_json` + `DEFAULT_MODEL`, one bounded call,
    same client/model as `app/project_memory.py::maybe_promote_turn`.
  - cost telemetry → `app.llm_telemetry.log_llm_run`, mirroring
    `project_memory.py::_log_promotion_run`'s one-line-per-run shape.

Seeded ONLY from the new-project branch — a PRD added to an already-bound
project is not re-seeded (that branch returns before ever calling here).
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


def _log_seed_run(
    *, project_id: int, prd_id: int, conversation_id: int, meta: dict,
    start: float, entries: int, used_fallback: bool,
) -> None:
    """The one structured cost-summary line for a successful seed run —
    identifiers + counts + a bool only, mirroring
    `project_memory.py::_log_promotion_run`'s shape (never raises; a
    logging hiccup must never be the reason the seed fails)."""
    try:
        log_llm_run(
            operation="projects.memory.origin_seed",
            identifier={
                "project_id": project_id,
                "prd_id": prd_id,
                "conversation_id": conversation_id,
            },
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


def seed_project_origin_memory(
    *, project_id: int, prd_id: int, prd_title: str, conversation_id: int
) -> None:
    """Best-effort: summarize the originating chat + PRD into a concise
    "project origin" memory for the freshly created project. Never raises —
    any failure is logged and swallowed so project creation is untouched.

    Writes a brief summary as one agent-promoted memory entry and each key
    decision as its own entry (the memory model is a list of discrete
    entries), all tagged with `source_conversation_id=conversation_id`, then
    schedules ONE summary regen for the whole seed.

    Non-empty guarantee: at least the brief is always seeded on this path —
    the LLM brief when available, otherwise a deterministic PRD-derived
    brief. Memory is left unseeded only when there is no PRD content to
    ground even a fallback brief (no title).
    """
    start = time.monotonic()
    try:
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
        # HTTP handler — same contract as the group-chat promoter).
        schedule_regen(project_id)
        _log_seed_run(
            project_id=project_id, prd_id=prd_id, conversation_id=conversation_id,
            meta=meta, start=start, entries=written, used_fallback=used_fallback,
        )
    except Exception:  # noqa: BLE001 — best-effort, AD-P7: never break project creation
        logger.warning(
            "project_origin_seed_failed project_id=%s prd_id=%s conversation_id=%s",
            project_id, prd_id, conversation_id, exc_info=True,
        )
