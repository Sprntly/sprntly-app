"""Friendly progress copy for the Design Agent activity stream.

Pure module — no imports from runner.py, no side effects on import.
Maps tool activity to plain-English user-facing copy that is safe to
display in the left-panel activity stream (SSE `kind:"step"` events).

Rules:
  - No file paths, extensions, token counts, iteration numbers, or
    tool names ever reach the output string.
  - `tool_input` may be None or {} at streaming time (input not yet
    accumulated). When it is None/empty, the tool-name-only fallback
    is used. When `path` is available, path-based rules apply.
"""
from __future__ import annotations

import os
from typing import Any


def friendly_step(tool_name: str, tool_input: dict[str, Any] | None = None) -> str:
    """Map a tool call to a plain-English user-facing progress label.

    `tool_name` is the tool name the model called.
    `tool_input` is the tool's input dict as received at the time this
    function is called; it may be None or {} during streaming (before
    the input has been fully accumulated).

    Returns a short sentence (no period) safe for display in the UI.
    No path, extension, token count, iteration number, or tool name
    ever appears in the returned string.
    """
    # Sentinel tools — never surface their names or imply agent state
    if tool_name in ("clarifying_question", "propose_prd_patch"):
        return "Working on your prototype…"

    if tool_name == "fetch_figma":
        return "Reading your Figma design…"

    if tool_name == "search":
        return "Exploring your codebase…"

    if tool_name == "read":
        return "Reading the codebase…"

    if tool_name == "read_console":
        return "Checking browser output…"

    if tool_name == "line_replace":
        return "Refining the design…"

    if tool_name == "write":
        path = (tool_input or {}).get("path") if tool_input else None
        return _write_label(path)

    # Fallback for any unmapped tool or unknown call
    return "Working on your prototype…"


def _write_label(path: str | None) -> str:
    """Derive a friendly label for a `write` tool call given its path.

    When `path` is None or empty, returns the generic fallback.
    No path string, extension, or directory name ever reaches the output.
    """
    if not path:
        return "Building your prototype…"

    if path.startswith("src/screens/") or path.startswith("src/pages/"):
        # Extract just the filename stem, capitalise the first letter
        basename = os.path.basename(path)
        stem = os.path.splitext(basename)[0]
        if stem:
            name = stem[0].upper() + stem[1:]
            return f"Building the {name} screen…"
        return "Building your prototype…"

    if path.startswith("src/components/ui/"):
        return "Designing UI components…"

    if path.startswith("src/components/"):
        return "Building components…"

    if path == "src/App.tsx":
        return "Wiring the app together…"

    if path.startswith("src/index."):
        return "Applying the design system…"

    return "Building your prototype…"


# Sentinel step event emitted just before the Vite build phase.
# Exported so routes/design_agent.py can fire it without duplicating the dict.
VITE_PHASE_STEP: dict[str, str] = {
    "kind": "step",
    "text": "Putting it together…",
    "state": "active",
}
