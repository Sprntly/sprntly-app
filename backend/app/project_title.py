"""Generate a concise, human-readable PROJECT title from a PRD's content.

When a project is auto-created from a PRD — BOTH the generation-time hook
(`app/project_from_prd.py::maybe_auto_create_project_for_prd`) AND the
create-modal's "Auto · from PRD" tab (`POST /v1/projects`, `origin='prd_auto'`,
`routes/projects.py::create_project`) — the project should be named for what
the PRD is ABOUT, not simply handed the PRD's own title verbatim. A PRD title
is often long, document-shaped, or feature-framed ("PRD: Redesign of the
onboarding flow for enterprise trials"); a project wants a short, recognizable
name ("Enterprise Onboarding Redesign").

This is the single shared name-derivation point both create paths route
through, so the generated title lands the same way regardless of entry point.

Design (mirrors `app.chat_suggestions`):
  - A dedicated, FAST title call — haiku tier (`claude-haiku-4-5`), NOT the
    answer/summary tier. Naming a document is well within haiku, and it keeps
    the added latency to project creation minimal.
  - SYNCHRONOUS at creation, so the project shows its generated name
    immediately — no create-with-PRD-title-then-async-rename flicker.

Best-effort and fail-safe (mirrors `bind_conversation_to_prd` /
`seed_project_origin_memory`): never raises. If the call fails, times out,
returns an empty/unusable title, or errors for ANY reason, the PRD title is
returned unchanged — a project is NEVER left nameless or blank. This is a
naming refinement on a path that must always succeed, not a step in it.
"""
from __future__ import annotations

import logging
import time

from app.db import prds as prds_db
from app.llm import call_json
from app.llm_telemetry import RunUsage, log_llm_run

logger = logging.getLogger(__name__)

# Haiku tier — see module docstring. A short naming call; correctness of the
# RESULT is guaranteed by the deterministic fallback below, not by the model.
_MODEL = "claude-haiku-4-5"

# Bound the raw material fed to the model — a large PRD body must not blow the
# prompt window or the spend on a one-shot title. Matches
# `project_origin_seed._PRD_MAX_CHARS`'s posture.
_PRD_MAX_CHARS = 6_000

# A project name is a label, not a sentence — clip anything the model returns
# past this so a runaway response can never become the stored name.
_MAX_TITLE_CHARS = 80

# A small ceiling: a title is a handful of tokens, and this bounds the output
# half of the per-creation cost.
_MAX_TOKENS = 60

_SYSTEM = """You name a product PROJECT, given the PRD it was created from.

Produce ONE short, human-readable project title that reflects what the PRD is \
ABOUT — its subject and goal — the way a person would name the workspace they \
open to work on it.

Rules:
- 2 to 6 words. Title Case. A label, not a sentence — no trailing period.
- Name the SUBJECT, not the document. Do NOT restate the PRD's title verbatim, \
and never include words like "PRD", "Project", "Spec", "Document", "Proposal", \
or "Requirements".
- Ground it strictly in the PRD's actual content. Do not invent a scope, \
product name, or audience the PRD does not mention.
- Plain text only: no quotes, markdown, or punctuation decoration."""

_SCHEMA = {
    "type": "object",
    "properties": {
        "title": {
            "type": "string",
            "description": (
                "A concise 2-6 word Title Case project name reflecting what "
                "the PRD is about. No surrounding quotes or punctuation."
            ),
        },
    },
    "required": ["title"],
    "additionalProperties": False,
}


def _clip(text: str, cap: int) -> str:
    return text if len(text) <= cap else text[:cap]


def _read_prd_body(prd_id: int) -> str:
    """The PRD's human body (`payload_md`), or "" when unavailable — the raw
    material the title is grounded in. Best-effort: the PRD title alone is
    enough to prompt with when there is no body."""
    try:
        prd = prds_db.get_prd(prd_id)
        if prd:
            return (prd.get("payload_md") or "").strip()
    except Exception:  # noqa: BLE001 — best-effort, title-only is a valid prompt
        pass
    return ""


def _clean_title(raw: object) -> str:
    """Normalize the model's title into a storable name, or "" if unusable.

    Strips surrounding quotes/whitespace and clips to the length cap. Returns
    "" for anything that is not a non-empty string, so the caller falls back to
    the PRD title."""
    if not isinstance(raw, str):
        return ""
    title = raw.strip().strip('"').strip("'").strip()
    if not title:
        return ""
    return _clip(title, _MAX_TITLE_CHARS).strip()


def generate_project_title(*, prd_id: int, fallback_title: str) -> str:
    """A concise project name generated from the PRD's content, or
    `fallback_title` when generation is unavailable or fails.

    NEVER raises: any error, timeout, or empty/unusable result falls back to
    `fallback_title` unchanged, so a project is never left nameless. Callers
    can use the return value directly as the project `name`.
    """
    start = time.monotonic()
    meta: dict = {}
    try:
        prd_body = _read_prd_body(prd_id)
        user = f"PRD title: {fallback_title}"
        if prd_body:
            user += f"\n\nPRD body:\n{_clip(prd_body, _PRD_MAX_CHARS)}"
        user += "\n\nName this project."

        out = call_json(
            system=_SYSTEM,
            user=user,
            model=_MODEL,
            schema=_SCHEMA,
            max_tokens=_MAX_TOKENS,
            meta_out=meta,
        )
        title = _clean_title(out.get("title"))
        if not title:
            return fallback_title

        _log_title_run(prd_id=prd_id, meta=meta, start=start)
        return title
    except Exception:  # noqa: BLE001 — best-effort: a naming refinement never
        # breaks (or blocks) project creation. Fall back to the PRD title.
        logger.warning(
            "project_title_generation_failed prd_id=%s; using PRD title",
            prd_id,
            exc_info=True,
        )
        return fallback_title


def _log_title_run(*, prd_id: int, meta: dict, start: float) -> None:
    """One structured cost-summary line per generated title — mirrors
    `project_origin_seed._log_seed_run`'s shape. Never raises: a telemetry
    hiccup must not turn a successful title into a fallback."""
    try:
        log_llm_run(
            operation="projects.title.generate",
            identifier={"prd_id": prd_id},
            usage=RunUsage(
                cache_creation_input_tokens=meta.get("cache_creation_input_tokens", 0),
                cache_read_input_tokens=meta.get("cache_read_input_tokens", 0),
                input_tokens=meta.get("input_tokens", 0),
                output_tokens=meta.get("output_tokens", 0),
            ),
            duration_ms=int((time.monotonic() - start) * 1000),
            status="complete",
            model=meta.get("model") or _MODEL,
        )
    except Exception:  # noqa: BLE001 — observability must never break the caller
        logger.warning("project_title_cost_log_failed prd_id=%s", prd_id)
