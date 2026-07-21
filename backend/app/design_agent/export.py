"""Markdown export serialiser (P2-08).

Pure deterministic function: (prototype_id, checkpoint_id) → markdown brief.
NO LLM call. Same inputs → byte-identical output across runs.

Consumed by:
- P2-09's record_export_at_complete hook (writes to prototype_exports table)
- P2-09's GET /v1/design-agent/{id}/export route (reads from the table, falls
  back to this function on a cache miss)

Per Apurva 2026-05-29: customers paste the output into their own coding AI
(Claude Code / Cursor / Codex). The markdown structure favours pastability over
machine-parseability — sections are H2 markers, source files are fenced code
blocks with language hints, deterministic ordering.
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any

from app.db.prds import get_prd_rendered
from app.db.prototype_comments import list_resolved_comments
from app.db.prototypes import get_prototype

logger = logging.getLogger(__name__)


async def render_export_markdown(
    prototype_id: int,
    checkpoint_id: int,
    *,
    workspace_id: str,
) -> str:
    """Render the markdown design brief for a (prototype, checkpoint) pair.

    Deterministic: same args + same DB state → byte-identical string. No LLM,
    no network, no time-of-day variation EXCEPT the explicit `generated_at`
    line (ISO 8601 UTC; the caller's clock is the only non-determinism, and
    P2-09 persists the result so the row is the single observation point).

    `async` because `_read_source_files` awaits the storage helper added in
    P2-04 (`read_source_files_for_checkpoint`). The source read happens here at
    the top level; `_assemble` stays sync and receives the loaded dict as a
    kwarg so its templating remains a pure function.

    Raises ValueError on missing prototype, missing checkpoint, or
    checkpoint not belonging to prototype (workspace-isolated through the
    underlying DB helpers).
    """
    prototype = get_prototype(prototype_id=prototype_id, workspace_id=workspace_id)
    if not prototype:
        raise ValueError(f"render_export_markdown: prototype {prototype_id} not found")
    checkpoint = _get_checkpoint(checkpoint_id=checkpoint_id, workspace_id=workspace_id)
    if not checkpoint or checkpoint["prototype_id"] != prototype_id:
        raise ValueError(
            f"render_export_markdown: checkpoint {checkpoint_id} does not belong to prototype {prototype_id}"
        )
    prd = get_prd_rendered(prototype["prd_id"])
    if not prd:
        raise ValueError(f"render_export_markdown: PRD {prototype['prd_id']} not found")

    source_files = await _read_source_files(prototype_id, checkpoint_id)
    # SYNC read (supabase-py is sync, matching the other prototype_comments helpers) —
    # call directly, no await, and hand the list to the pure sync `_assemble`. F16.
    resolved_comments = list_resolved_comments(
        prototype_id=prototype_id, workspace_id=workspace_id
    )
    return _assemble(
        prototype=prototype,
        checkpoint=checkpoint,
        prd=prd,
        source_files=source_files,
        resolved_comments=resolved_comments,
        generated_at=datetime.now(timezone.utc).isoformat(),
    )


def _assemble(
    *,
    prototype: dict[str, Any],
    checkpoint: dict[str, Any],
    prd: dict[str, Any],
    source_files: dict[str, str],
    generated_at: str,
    resolved_comments: list[dict[str, Any]] | None = None,
) -> str:
    """Pure assembly of the markdown body. Separated for testability —
    pass synthesised dicts (including a pre-loaded `source_files`) to lock
    down the output shape. Stays SYNC: the source-file read is awaited by
    `render_export_markdown` and the result handed in here.

    `resolved_comments` (F16, P4-07) is pre-loaded + pre-ordered by
    `list_resolved_comments` (by anchor_id, id); defaults to None → no Resolved
    Feedback section. Both enrichment sections (Design Source, Resolved Feedback)
    are conditional and append AFTER the five core sections, so the P2-08/P3-17
    contract output is byte-identical when their data is absent.
    """
    title = prd.get("title") or f"Prototype {prototype['id']}"
    prd_md = prd.get("payload_md") or ""
    design_block = _extract_design_block(prd_md)
    bundle_url = prototype.get("bundle_url") or "(no bundle staged)"
    short_hash = str(checkpoint["id"])  # bigint id is deterministic; sufficient

    parts: list[str] = []
    parts.append(f"# Design Brief: {title}")
    parts.append(f"> Generated {generated_at} from checkpoint {short_hash}.")
    parts.append("")
    parts.append("## PRD Reference")
    parts.append(_strip_design_block(prd_md))
    parts.append("")
    parts.append("## Design Spec")
    if design_block:
        parts.append(design_block)
    else:
        parts.append("_(no `:::design` block in the PRD)_")
    parts.append("")
    parts.append("## Live Prototype")
    parts.append(f"Interactive bundle: <{bundle_url}>")
    parts.append("")
    parts.append("## Generated Prototype Source")
    parts.append(
        "Below are the source files of the generated React + Vite + Tailwind "
        "prototype. Paste this entire brief into your coding agent (Claude "
        "Code, Cursor, Codex) to bootstrap the implementation in your repo."
    )
    parts.append("")
    if source_files:
        for rel_path in sorted(source_files.keys()):  # alphabetical for determinism
            lang = _language_for(rel_path)
            parts.append(f"### {rel_path}")
            parts.append(f"```{lang}")
            parts.append(source_files[rel_path].rstrip())
            parts.append("```")
            parts.append("")
    else:
        parts.append(
            f"_Source files not staged for this checkpoint. "
            f"The built bundle is available at {bundle_url} — "
            f"fetch it to inspect the compiled output._"
        )
        parts.append("")
    parts.append("## Iteration History")
    history = checkpoint.get("prompt_history") or []
    if history:
        for i, entry in enumerate(history, start=1):
            role = entry.get("role", "unknown")
            content = entry.get("content", "")
            if isinstance(content, list):
                # Anthropic block-list shape; flatten to text-only summary
                content = " ".join(b.get("text", "") for b in content if isinstance(b, dict))
            parts.append(f"### Turn {i} ({role})")
            parts.append(str(content).strip())
            parts.append("")
    else:
        parts.append("_No iteration history recorded for this checkpoint._")

    # ── Enrichment sections (F16, P4-07) — appended AFTER the five core sections,
    # in the order Design Source → Resolved Feedback. Each is conditional: when its
    # data is absent the helper returns "" and NOTHING is emitted (no header, no
    # placeholder), so the five-section contract output stays byte-identical for the
    # common no-Figma + no-resolved-comments case.
    for section in (
        _render_design_source(prototype),
        _render_resolved_feedback(resolved_comments or []),
    ):
        if section:
            parts.append("")
            parts.append(section)

    return "\n".join(parts).rstrip() + "\n"


def _render_design_source(prototype: dict[str, Any]) -> str:
    """Render the Design Source section (F16) — names the design inputs the
    prototype was generated from: the Figma file key and/or an uploaded
    screenshot reference. Returns "" (omit the section entirely) when the
    prototype carries neither (Scenario B website / Scenario 0 no-input).
    When both are present, one section carries both lines, Figma first.

    Emits the BARE Figma file key in a code span, NOT a constructed figma.com
    URL: the codebase builds no `figma.com/file/<key>` URL anywhere, so
    inventing a path / node-id format would be a guess. The reader pastes the
    key into Figma's opener.

    The screenshot line is a plain sentence — the `screenshot_key` storage key
    is NEVER printed: it embeds the workspace id and a storage-internal path,
    and this document gets pasted into third-party coding agents. Likewise no
    link to the stored image is constructed. Provenance is the sentence, not
    the key.
    """
    figma_file_key = prototype.get("figma_file_key")
    screenshot_key = prototype.get("screenshot_key")
    lines: list[str] = []
    if figma_file_key:
        lines.append(f"Generated from Figma file `{figma_file_key}`.")
    if screenshot_key:
        lines.append("Generated from an uploaded screenshot reference.")
    if not lines:
        return ""
    return "## Design Source\n\n" + "\n".join(lines)


def _render_resolved_feedback(resolved_comments: list[dict[str, Any]]) -> str:
    """Render the Resolved Feedback section (F16) — the prototype's resolved comment
    threads, grouped by anchor. Returns "" (omit the section) when there are no
    resolved comments.

    `resolved_comments` arrives pre-ordered by (anchor_id, id) from
    `list_resolved_comments`, so iteration here is deterministic — no sorting, no set
    iteration. One `### Anchor` sub-header per distinct anchor_id; multiple comments
    on the same anchor group under it in id order (consecutive same-anchor rows).

    Each line carries the author (bold), the `resolved_at` ISO timestamp in parens
    (falls back to "—" when null — defensive; P3-02 stamps it), then the body inline
    after a colon (bodies are short prose, not code — not fenced). The body IS the
    point of this section (the customer asked for "comments" in the handoff); this
    does NOT contradict Rule #24, which governs LOG lines, not the export artifact —
    so the body is never logged in this path.
    """
    if not resolved_comments:
        return ""
    lines: list[str] = [
        "## Resolved Feedback",
        "",
        "Feedback left on the prototype and resolved before this checkpoint was locked.",
    ]
    current_anchor: object = object()  # sentinel: never equals a real anchor_id
    for comment in resolved_comments:
        anchor_id = comment.get("anchor_id") or ""
        if anchor_id != current_anchor:
            current_anchor = anchor_id
            lines.append("")
            lines.append(f"### Anchor `{anchor_id}`")
        author = comment.get("author") or "unknown"
        resolved_at = comment.get("resolved_at") or "—"
        body = comment.get("body") or ""
        lines.append(f"- **{author}** (resolved {resolved_at}): {body}")
    return "\n".join(lines)


def _extract_design_block(prd_md: str) -> str:
    """Return the body of the `:::design …\n:::` block, or empty if not present.
    Per backend/app/prompts.py:363-372: the body is plain `key: value` lines, NOT JSON.
    """
    marker_open = ":::design"
    marker_close = ":::"
    start = prd_md.find(marker_open)
    if start == -1:
        return ""
    body_start = prd_md.find("\n", start) + 1
    if body_start == 0:
        return ""
    end = prd_md.find(f"\n{marker_close}", body_start)
    if end == -1:
        return ""
    return prd_md[body_start:end].strip()


def _strip_design_block(prd_md: str) -> str:
    """Return the PRD body with the `:::design` block REMOVED (it appears below
    in its own section; do not duplicate)."""
    marker_open = ":::design"
    marker_close = ":::"
    start = prd_md.find(marker_open)
    if start == -1:
        return prd_md.rstrip()
    end = prd_md.find(f"\n{marker_close}", start)
    if end == -1:
        return prd_md[:start].rstrip()
    end_of_close = end + len(f"\n{marker_close}")
    return (prd_md[:start] + prd_md[end_of_close:]).rstrip()


async def _read_source_files(
    prototype_id: int,
    checkpoint_id: int,
) -> dict[str, str]:
    """Return the staged `virtual_fs` for this checkpoint.

    Reads from `prototypes/<pid>/<cid>/_source/` via the storage helper added
    in P2-04. Returns the raw {relative_path: content} dict.

    Returns {} only for historical (pre-P2-04) checkpoints; any complete prototype
    generated after P2-04 ships MUST have source files (verified by P2-04's AC).
    """
    from app.design_agent.storage import read_source_files_for_checkpoint
    return await read_source_files_for_checkpoint(prototype_id, checkpoint_id)


def _language_for(rel_path: str) -> str:
    """Map a relative path to a markdown code-fence language hint.
    Defaults to empty string (no hint) for unknown extensions.
    """
    if rel_path.endswith((".tsx", ".jsx")): return "tsx"
    if rel_path.endswith(".ts"):            return "ts"
    if rel_path.endswith(".js"):            return "js"
    if rel_path.endswith(".css"):           return "css"
    if rel_path.endswith(".html"):          return "html"
    if rel_path.endswith(".json"):          return "json"
    if rel_path.endswith((".md", ".mdx")):  return "markdown"
    return ""


def _get_checkpoint(*, checkpoint_id: int, workspace_id: str) -> dict[str, Any] | None:
    """Local helper — db/prototypes.py does not yet export a checkpoint-by-id
    helper (verified at HEAD). Inline rather than adding to db/prototypes.py
    so this ticket's diff stays scoped to design_agent/."""
    from app.db.client import require_client
    c = require_client()
    resp = (c.table("prototype_checkpoints")
            .select("*")
            .eq("id", checkpoint_id)
            .eq("workspace_id", workspace_id)
            .limit(1).execute())
    return resp.data[0] if resp.data else None
