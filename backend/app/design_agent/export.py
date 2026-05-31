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
    return _assemble(
        prototype=prototype,
        checkpoint=checkpoint,
        prd=prd,
        source_files=source_files,
        generated_at=datetime.now(timezone.utc).isoformat(),
    )


def _assemble(
    *,
    prototype: dict[str, Any],
    checkpoint: dict[str, Any],
    prd: dict[str, Any],
    source_files: dict[str, str],
    generated_at: str,
) -> str:
    """Pure assembly of the markdown body. Separated for testability —
    pass synthesised dicts (including a pre-loaded `source_files`) to lock
    down the output shape. Stays SYNC: the source-file read is awaited by
    `render_export_markdown` and the result handed in here.
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
    return "\n".join(parts).rstrip() + "\n"


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
