"""Design Agent tool registry + dispatch.

Per AD17: 6 ACTION_TOOLS (inviolable cap, fixed list) + up to 4 SENTINEL_TOOLS
(currently empty in P1; sentinels — clarifying_question, propose_prd_patch —
land in P3-08 and P3-09). The split exists in code from day 1 so P3 can append
to SENTINEL_TOOLS without restructuring the module.

Tool descriptions follow Anthropic's guidance (3-4 sentences min + negative-
space — "when NOT to call this tool"). They are the load-bearing artefact
per agent-build-research.md §1.4; implementations are intentionally short.
"""
from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from typing import Any, Awaitable, Callable

# ─── Tool definition shape ────────────────────────────────────────────────


@dataclass(frozen=True)
class ToolDef:
    name: str
    description: str
    input_schema: dict[str, Any]
    execute: Callable[[dict[str, Any], "ToolContext"], Awaitable[dict[str, Any]]]
    category: str  # "action" | "sentinel"


@dataclass
class ToolContext:
    """Runtime context handed to every tool execute fn.

    Assembled by the P1-04 runner per agent run. `figma_access_token` is
    resolved by the runner from the stored Figma connector token (see
    routes/connectors.py `_figma_access_token`) — the tool executor never
    decrypts tokens itself, which keeps this module importable without the
    backend connector/db stack (only the no-key / no-token branches are
    exercised in unit tests).

    `figma_node_id`: optional frame-level node-id extracted from a pasted
    Figma URL's `node-id` query parameter (hyphen→colon converted by the
    frontend). When set, `_exec_fetch_figma` uses it as the default
    `frame_ids` so the agent reads that specific frame instead of the
    file's top-5 frames, enabling frame-targeted generation.
    """

    prototype_id: int
    workspace_id: str            # from require_session().aud
    virtual_fs: dict[str, str] = field(default_factory=dict)  # populated by write/line_replace
    figma_file_key: str | None = None
    figma_node_id: str | None = None   # frame-level targeting; None → top-5 default
    figma_access_token: str | None = None  # runner-injected; None until the connector is authorised


# ─── 6 ACTION TOOLS (cap=6, inviolable per AD17) ──────────────────────────

VIEW = ToolDef(
    name="view",
    description=(
        "Read the contents of a file in the prototype's virtual filesystem. "
        "Use this BEFORE editing any file you have not already written this "
        "session — agents that write blind frequently overwrite their own "
        "earlier work. Returns the file content with line numbers prepended "
        "so subsequent line_replace calls can reference exact line ranges. "
        "Large files (>5K lines) return the first 500 + last 100 lines with "
        "a 'X total lines' footer and a hint to pass `lines: [start, end]` for "
        "a specific range. Do NOT call this on files you have already viewed "
        "this turn (the prior result is still in your context); do NOT call "
        "this to discover what files exist (use `search` for that)."
    ),
    input_schema={
        "type": "object",
        "properties": {
            "path": {"type": "string", "description": "Path relative to the prototype root, e.g. 'src/App.tsx'."},
            "lines": {
                "type": "array",
                "items": {"type": "integer"},
                "minItems": 2, "maxItems": 2,
                "description": "Optional [start, end] inclusive line range for partial reads on large files.",
            },
        },
        "required": ["path"],
    },
    execute=lambda inp, ctx: _exec_view(inp, ctx),
    category="action",
)

WRITE = ToolDef(
    name="write",
    description=(
        "Create a NEW file or completely rewrite an existing file in the "
        "prototype's virtual filesystem. Prefer `line_replace` for any change "
        "to an existing file larger than ~10 lines — full rewrites are 5× "
        "more expensive in output tokens AND lose the stable JSX anchor IDs "
        "the Vite plugin emits at compile time (anchor IDs are content-hashed "
        "by parent + nesting + type + index; structural drift moves them). "
        "Use this for: brand-new component files, fresh CSS modules, an "
        "intentional ground-up rewrite of a small component. Do NOT use this "
        "to make small text or style changes (use `line_replace`); do NOT "
        "emit `data-anchor-id` attributes manually — the build pipeline "
        "applies them automatically and any you write will be ignored or "
        "stripped."
    ),
    input_schema={
        "type": "object",
        "properties": {
            "path": {"type": "string", "description": "Path relative to the prototype root."},
            "content": {"type": "string", "description": "Full file content. NO `data-anchor-id` attributes — these are auto-applied."},
        },
        "required": ["path", "content"],
    },
    execute=lambda inp, ctx: _exec_write(inp, ctx),
    category="action",
)

LINE_REPLACE = ToolDef(
    name="line_replace",
    description=(
        "Replace a contiguous line range in an existing file. The `search` "
        "parameter MUST contain the exact current content of the named lines "
        "(verbatim, including whitespace) — this is the pre-image check that "
        "prevents you from overwriting code you have not actually read. "
        "Provide 3-5 lines of search context (not the whole file) and the "
        "exact line numbers — short context keeps the cost down and reduces "
        "false matches. On mismatch the tool returns `is_error: true` with "
        "the file's actual lines in that range so you can correct and retry. "
        "Use this for ALL edits to existing files larger than ~10 lines. Do "
        "NOT use this to insert content beyond the file's existing length "
        "(use `write` for append-style additions); do NOT use this if you "
        "have not first `view`ed the file this session."
    ),
    input_schema={
        "type": "object",
        "properties": {
            "path": {"type": "string"},
            "first_replaced_line": {"type": "integer", "description": "Inclusive 1-indexed line number where the replacement starts."},
            "last_replaced_line": {"type": "integer", "description": "Inclusive 1-indexed line number where the replacement ends."},
            "search": {"type": "string", "description": "Exact current content of the named lines (pre-image check)."},
            "replace": {"type": "string", "description": "New content for the named lines."},
        },
        "required": ["path", "first_replaced_line", "last_replaced_line", "search", "replace"],
    },
    execute=lambda inp, ctx: _exec_line_replace(inp, ctx),
    category="action",
)

SEARCH = ToolDef(
    name="search",
    description=(
        "Grep the prototype's virtual filesystem for a regex pattern, with "
        "optional file-glob filtering. Use this to locate where a component, "
        "import, Tailwind class, or string literal lives BEFORE deciding "
        "which file to view or edit. Returns up to 25 matches, each with "
        "file path + line number + matched line + two lines of surrounding "
        "context; if there are more matches the response includes a 'X more "
        "matches not shown' hint with a suggestion to narrow the pattern. "
        "Do NOT use this in place of `view` once you know which file to "
        "inspect (search is for discovery; view is for reading); do NOT use "
        "this to read whole files (it truncates each match to 5 lines of "
        "context)."
    ),
    input_schema={
        "type": "object",
        "properties": {
            "pattern": {"type": "string", "description": "Regex pattern (Python `re` syntax)."},
            "path_glob": {"type": "string", "description": "Optional glob like 'src/**/*.tsx'. Defaults to all files."},
        },
        "required": ["pattern"],
    },
    execute=lambda inp, ctx: _exec_search(inp, ctx),
    category="action",
)

FETCH_FIGMA = ToolDef(
    name="fetch_figma",
    description=(
        "Fetch structural data (frame names, bounds, color/typography tokens, "
        "child component references) for up to 5 Figma frames per call. "
        "Returns names + bounds + token references — NOT pixel data, NOT "
        "rendered images. Use this once at the start of a generation to scope "
        "the design surface, then again ONLY when you need a specific frame "
        "you have not already seen (each call adds ≤15K tokens to context "
        "and the cache breakpoint is set above your call, so repeated calls "
        "are not free). Pass `frame_ids: [...]` to fetch specific frames "
        "you already know the IDs of; otherwise the default returns the top "
        "5 frames in the file. Do NOT call this for files outside the "
        "prototype's configured `figma_file_key` (the tool will return "
        "is_error: true); do NOT try to fetch raster/image data — this tool "
        "returns structural metadata only."
    ),
    input_schema={
        "type": "object",
        "properties": {
            "frame_ids": {
                "type": "array",
                "items": {"type": "string"},
                "description": "Optional explicit frame IDs to fetch. Capped at 5 per call.",
            },
        },
    },
    execute=lambda inp, ctx: _exec_fetch_figma(inp, ctx),
    category="action",
)

READ_CONSOLE = ToolDef(
    name="read_console",
    description=(
        "Read the browser console output for the prototype's current preview. "
        "In P1 this tool is a stub that returns an empty array — there is no "
        "running prototype runtime to instrument per AD20 (no per-prototype "
        "container/sandbox). Future versions may wire real telemetry; for now "
        "treat an empty response as 'no console output available' and proceed "
        "without relying on runtime feedback. Do NOT call this in a loop "
        "expecting data (you will get [] every time); do NOT use it as a "
        "verification step (use static analysis on the file content you "
        "wrote, not runtime traces that do not exist)."
    ),
    input_schema={
        "type": "object",
        "properties": {
            "level": {"type": "string", "enum": ["error", "warn", "info", "log"], "description": "Optional log-level filter."},
        },
    },
    execute=lambda inp, ctx: _exec_read_console(inp, ctx),
    category="action",
)

ACTION_TOOLS: list[ToolDef] = [VIEW, WRITE, LINE_REPLACE, SEARCH, FETCH_FIGMA, READ_CONSOLE]

# ─── EXIT-SENTINEL TOOLS (cap=4) ──────────────────────────────────────────
# Sentinels land in P3:
#   - clarifying_question (P3-08): pauses the loop awaiting user reply (THIS file)
#   - propose_prd_patch  (P3-09): persists a PRD patch proposal, ends the loop
# Each sentinel must satisfy "ends or pauses the loop with a structured
# payload" per AD17. New sentinels are appended below; do not change the
# list shape. The RUNNER (agent_loop) keys the loop-break + resulting state on
# the tool NAME, not on `category == "sentinel"` uniformly — clarifying_question
# is a terminal-PAUSE; propose_prd_patch (P3-09) is a terminal-COMPLETE.

CLARIFYING_QUESTION = ToolDef(
    name="clarifying_question",
    description=(
        "Pause and ask the user ONE specific question when the request is "
        "genuinely ambiguous and proceeding would require guessing about "
        "product intent. Calling this ENDS your turn and returns control to "
        "the user — it is a terminal action, not a mid-work query. Use it only "
        "for genuine PRODUCT ambiguity (e.g. 'should this CTA submit the form "
        "or open a confirmation modal?'). Do NOT call it for choices the design "
        "system, the PRD, or the Figma frames already answer (colour, font, "
        "spacing, which shadcn component) — pick the reasonable default and "
        "proceed. Do NOT call it as a courtesy or to confirm you understood; "
        "the user trusts you to execute. At most ONE call per run."
    ),
    input_schema={
        "type": "object",
        "properties": {
            "question": {"type": "string", "description": "The single specific question. One sentence."},
            "choices": {
                "type": "array",
                "items": {"type": "string"},
                "description": "Optional 2-4 options rendered as buttons; omit for free-text.",
            },
            "context": {"type": "string", "description": "Optional 1-2 sentence reason it's ambiguous."},
        },
        "required": ["question"],
    },
    execute=lambda inp, ctx: _exec_clarifying_question(inp, ctx),
    category="sentinel",
)

PROPOSE_PRD_PATCH = ToolDef(
    name="propose_prd_patch",
    description=(
        "Propose an edit to the PRD text when — and ONLY when — the change you "
        "just made (or are about to make) introduces or removes a USER-FACING "
        "CAPABILITY that the PRD should reflect (e.g. you added a confirmation "
        "step the PRD doesn't mention, or removed a field the PRD requires). "
        "Calling this records a PROPOSED patch for the user to accept or reject "
        "— it does NOT edit the PRD directly and DOES end your turn (it is a "
        "terminal action: call it LAST, after your write/line_replace edits are "
        "done). Pass a 1-sentence `rationale` and the `patch_md` (the markdown "
        "delta to append). Do NOT propose a patch for purely visual tweaks "
        "(colour, spacing, copy polish) — those have no PRD implication. Do NOT "
        "propose more than one patch per run; batch all PRD implications into a "
        "single patch. At most ONE call per run."
    ),
    input_schema={
        "type": "object",
        "properties": {
            "rationale": {"type": "string", "description": "One sentence: why this change affects the PRD."},
            "patch_md": {"type": "string", "description": "The proposed markdown delta to the PRD."},
        },
        "required": ["rationale", "patch_md"],
    },
    execute=lambda inp, ctx: _exec_propose_prd_patch(inp, ctx),
    category="sentinel",
)

# propose_prd_patch is EXECUTE-ONLY: `tools_for_mode` includes the full
# SENTINEL_TOOLS list only in execute mode; plan/scaffold keep just
# clarifying_question (filtered by name there). No PRD-edit step exists at
# plan/scaffold, so the sentinel is absent from those registries by construction.
SENTINEL_TOOLS: list[ToolDef] = [CLARIFYING_QUESTION, PROPOSE_PRD_PATCH]

# ─── Registry-level invariants ────────────────────────────────────────────

assert len(ACTION_TOOLS) == 6, "AD17: action tool count is fixed at 6"
assert len(SENTINEL_TOOLS) <= 4, "AD17: sentinel tool count capped at 4"
assert all(t.category == "action" for t in ACTION_TOOLS)
assert all(t.category == "sentinel" for t in SENTINEL_TOOLS)


def all_tools() -> list[ToolDef]:
    """Concatenated registry, action tools first. Order is stable across runs."""
    return [*ACTION_TOOLS, *SENTINEL_TOOLS]


def tool_definitions_for_api() -> list[dict[str, Any]]:
    """Serialised shape for the Anthropic Messages API `tools=` field.

    Back-compat alias (P3-07): equivalent to the EXECUTE-mode registry. P1's
    callers imported this unconditional serialiser before mode partitioning
    existed; it stays as `tool_definitions_for_mode("execute")` so they keep
    working while they migrate to the explicit mode-aware call. New call sites
    MUST use `tool_definitions_for_mode(mode)` — never this alias."""
    return tool_definitions_for_mode("execute")


# ─── AD17 mode-partitioned registry assembly (P3-07) ──────────────────────────
#
# AD17's rule is "6 action tools (fixed) + ≤4 exit-sentinel tools" — a split,
# NOT a flat ≤7. The cap is enforced PER MODE: action-count ≤6 AND sentinel-count
# ≤4. PLAN mode runs an explore-only subset of the action tools (no write /
# line_replace — Plan mode CANNOT mutate by construction, AD10 "mode is state,
# not a request"); EXECUTE / SCAFFOLD run all 6. Sentinels (clarifying_question
# from P3-08, propose_prd_patch from P3-09) are appended to SENTINEL_TOOLS by
# those tickets and filtered per mode here.

PLAN_ACTION_TOOLS: list[ToolDef] = [VIEW, SEARCH, FETCH_FIGMA, READ_CONSOLE]  # no write/line_replace
EXECUTE_ACTION_TOOLS: list[ToolDef] = ACTION_TOOLS                            # all 6

# The canonical tool-partition mode strings. NOTE: 'iterate' is NOT one of them —
# it is P3-05's cost-log telemetry label, not a tool-partition mode. A caller that
# passes any non-canonical value (incl. the legacy 'iterate' label) is treated as
# 'execute' by `tools_for_mode`'s else branch, but callers MUST pass one of these.
_PARTITION_MODES = ("scaffold", "plan", "execute", "manual")


def tools_for_mode(mode: str) -> list[ToolDef]:
    """Return the tool registry for a run mode. AD17's split is enforced PER MODE:
      - 'plan'    : explore-only action tools (view/search/fetch_figma/read_console)
                    + plan-safe sentinels (clarifying_question). No write/line_replace.
      - 'execute' : all 6 action tools + all sentinels.
      - 'scaffold': all 6 action tools + scaffold-safe sentinels (clarifying_question;
                    NOT propose_prd_patch — there is no PRD-edit step at scaffold).
      - 'manual'  : all 6 action tools + NO sentinels (P4-02, AD23 — a commit-only
                    run never pauses or proposes a PRD patch).
    Any other mode value (incl. the legacy 'iterate' telemetry label) is treated
    as 'execute'.

    The returned registry is FROZEN for the duration of a run (the runner computes
    it ONCE before the tool-use loop and never mutates it mid-loop): a mid-run tool
    change would invalidate the prompt cache (agent-build-research.md §3.4). The
    per-mode invariant (action ≤6, sentinel ≤4) is asserted on every call so a bad
    future append fails loud rather than silently shipping an over-budget registry."""
    if mode == "plan":
        action = PLAN_ACTION_TOOLS
        sentinels = [t for t in SENTINEL_TOOLS if t.name == "clarifying_question"]
    elif mode == "scaffold":
        action = EXECUTE_ACTION_TOOLS
        sentinels = [t for t in SENTINEL_TOOLS if t.name == "clarifying_question"]
    elif mode == "manual":
        # P4-02 (AD23): manual edit is a COMMIT-ONLY run — all 6 action tools
        # (line_replace is the workhorse) but NO sentinels. A manual commit never
        # pauses to ask (clarifying_question) and never proposes a PRD patch
        # (propose_prd_patch); it mechanically commits already-decided changes and
        # stops. This is a tool-PARTITION addition, NOT a new tool — AD17 budget
        # unchanged (6 action, 0 sentinel here). Do NOT fall through to 'execute',
        # which would register BOTH sentinels and break the manual-edit AC.
        action = EXECUTE_ACTION_TOOLS
        sentinels = []
    else:  # 'execute' (and any non-canonical value, defensively)
        action = EXECUTE_ACTION_TOOLS
        sentinels = list(SENTINEL_TOOLS)
    registry = [*action, *sentinels]
    # AD17 per-mode invariant — action ≤6 AND sentinel ≤4 (NOT a flat ≤7).
    assert sum(1 for t in registry if t.category == "action") <= 6, "AD17: ≤6 action tools per mode"
    assert sum(1 for t in registry if t.category == "sentinel") <= 4, "AD17: ≤4 sentinel tools per mode"
    return registry


def tool_definitions_for_mode(mode: str) -> list[dict[str, Any]]:
    """Serialised (Anthropic `tools=` shape) registry for a run mode. The
    mode-aware counterpart to `tool_definitions_for_api`; the runner calls this
    once at run start with the run's mode."""
    return [
        {"name": t.name, "description": t.description, "input_schema": t.input_schema}
        for t in tools_for_mode(mode)
    ]


# ─── Dispatch ─────────────────────────────────────────────────────────────


def _unknown_tool_error(name: str, registered: list[str]) -> dict[str, Any]:
    return {
        "is_error": True,
        "content": f"Unknown tool: {name!r}. Registered tools: {registered}",
        "tool_name": name,
    }


async def dispatch(
    name: str,
    input: dict[str, Any],
    ctx: ToolContext,
    allowed_names: set[str] | None = None,
) -> dict[str, Any]:
    """Look up a tool by name and execute it. Returns the tool_result content.

    `allowed_names` (P3-07, AD10): when provided — the runner passes the
    mode-partitioned registry's tool names, FROZEN at run start — a tool_use whose
    name is NOT in that set is rejected with an "Unknown tool" `is_error` WITHOUT
    executing. This is the "plan mode is state, not a request" guarantee
    (agent-build-research.md §4.5): even if the model hallucinates a `write` in
    PLAN mode (where the registry omits it), dispatch never runs the executor, so
    the virtual_fs is untouched. When `allowed_names` is None (the default — direct
    unit-test calls), no mode gate is applied and the global `all_tools()` registry
    governs.

    On any execute exception, returns an `is_error: true` payload with the
    failure class + message (NOT the traceback) so the agent can recover
    per agent-build-research.md §4.1. The runner is responsible for
    formatting this into the Anthropic `tool_result` block shape.
    """
    if allowed_names is not None and name not in allowed_names:
        return _unknown_tool_error(name, sorted(allowed_names))
    for t in all_tools():
        if t.name == name:
            try:
                return await t.execute(input, ctx)
            except Exception as exc:
                return {
                    "is_error": True,
                    "content": f"{type(exc).__name__}: {exc}",
                    "tool_name": name,
                }
    return _unknown_tool_error(name, [t.name for t in all_tools()])


# ─── Execute implementations (short by design; descriptions carry the weight) ─


async def _exec_view(inp: dict, ctx: ToolContext) -> dict:
    path = inp["path"]
    if path not in ctx.virtual_fs:
        siblings = sorted({p.rsplit("/", 1)[0] for p in ctx.virtual_fs.keys() if "/" in p})
        return {
            "is_error": True,
            "content": f"File not found: {path}. Directories with files: {siblings[:10]}",
            "tool_name": "view",
        }
    content = ctx.virtual_fs[path]
    lines = content.splitlines()
    total = len(lines)
    if "lines" in inp:
        start, end = inp["lines"]
        slice_ = lines[max(0, start - 1):end]
        numbered = "\n".join(f"{i + start}: {ln}" for i, ln in enumerate(slice_))
        return {"content": numbered, "total_lines": total}
    if total > 600:
        head = "\n".join(f"{i + 1}: {ln}" for i, ln in enumerate(lines[:500]))
        tail = "\n".join(f"{i + total - 99}: {ln}" for i, ln in enumerate(lines[-100:]))
        hint = f"\n... (truncated; {total} total lines; pass lines: [start, end] for a specific range)\n"
        return {"content": head + hint + tail, "total_lines": total}
    numbered = "\n".join(f"{i + 1}: {ln}" for i, ln in enumerate(lines))
    return {"content": numbered, "total_lines": total}


async def _exec_write(inp: dict, ctx: ToolContext) -> dict:
    ctx.virtual_fs[inp["path"]] = inp["content"]
    return {"content": f"Wrote {len(inp['content'])} bytes to {inp['path']}.", "path": inp["path"]}


async def _exec_line_replace(inp: dict, ctx: ToolContext) -> dict:
    path = inp["path"]
    if path not in ctx.virtual_fs:
        return {"is_error": True, "content": f"File not found: {path}", "tool_name": "line_replace"}
    lines = ctx.virtual_fs[path].splitlines()
    first, last = inp["first_replaced_line"], inp["last_replaced_line"]
    if first < 1 or last > len(lines) or first > last:
        return {
            "is_error": True,
            "content": (
                f"Invalid line range [{first}, {last}] in {path} "
                f"(file has {len(lines)} lines)."
            ),
            "tool_name": "line_replace",
        }
    actual = "\n".join(lines[first - 1:last])
    if actual != inp["search"]:
        return {
            "is_error": True,
            "content": (
                f"search/replace mismatch in {path} lines {first}-{last}. "
                f"Actual current content:\n{actual}\n"
                f"Re-issue with the corrected `search` string."
            ),
            "tool_name": "line_replace",
        }
    new_lines = lines[: first - 1] + inp["replace"].splitlines() + lines[last:]
    ctx.virtual_fs[path] = "\n".join(new_lines)
    return {"content": f"Replaced lines {first}-{last} in {path}.", "path": path}


async def _exec_search(inp: dict, ctx: ToolContext) -> dict:
    import re
    import fnmatch
    pattern = re.compile(inp["pattern"])
    glob = inp.get("path_glob", "*")
    matches: list[dict] = []
    total = 0
    for path, content in ctx.virtual_fs.items():
        if not fnmatch.fnmatch(path, glob):
            continue
        lines = content.splitlines()
        for i, ln in enumerate(lines, start=1):
            if pattern.search(ln):
                total += 1
                if len(matches) < 25:
                    ctx_lines = lines[max(0, i - 3):i + 2]
                    matches.append({"path": path, "line": i, "match": ln, "context": ctx_lines})
    out: dict[str, Any] = {"matches": matches, "total": total}
    if total > 25:
        out["hint"] = f"{total - 25} more matches not shown; narrow the pattern."
    return out


def _extract_top_level_frames(file_doc: dict, frame_ids: list[str]) -> list[dict]:
    """Pull top-level frames {id, name, type, bounds} from a Figma file document.

    Figma's GET /v1/files/{key} returns {document: {children: [pages]}} where
    each page's children are the top-level frames. If `frame_ids` is given,
    keep only matching frames; otherwise keep all. Caller caps the result at 5.
    Defensive against partial/missing tree nodes — returns [] rather than raising.
    """
    document = file_doc.get("document", {}) if isinstance(file_doc, dict) else {}
    wanted = set(frame_ids)
    out: list[dict] = []
    for page in document.get("children", []) or []:
        for node in (page.get("children", []) or []) if isinstance(page, dict) else []:
            if not isinstance(node, dict):
                continue
            if wanted and node.get("id") not in wanted:
                continue
            out.append({
                "id": node.get("id"),
                "name": node.get("name"),
                "type": node.get("type"),
                "bounds": node.get("absoluteBoundingBox"),
            })
    return out


def _extract_palette_summary(file_doc: dict) -> dict:
    """Walk the Figma document tree and extract the dominant fill palette.

    NOTE: This function's sole remaining consumer is the in-loop Figma
    fetch-tool payload (_exec_fetch_figma), which returns its output as the
    "palette" key for the agent's fetch_figma tool. It is NOT used by
    design-system extraction — FigmaExtractor.normalize now reads the rich
    gather keys from gather_figma_signals through the shared kernel.

    Returns a dict with:
      - background: hex of the most common large-area dark/light fill
      - accent: hex of the least-common (likely interactive/highlight) fill
      - is_dark: True when the dominant background is dark
      - swatches: list of all unique hex colors found (up to 12, deduplicated)
    Falls back gracefully to empty on any structure error.
    """
    from collections import Counter

    fills: list[str] = []

    def _walk(node: object) -> None:
        if not isinstance(node, dict):
            return
        for fill in (node.get("fills") or []):
            if not isinstance(fill, dict):
                continue
            if fill.get("type") != "SOLID":
                continue
            if fill.get("visible") is False:
                continue
            c = fill.get("color", {})
            r = int((c.get("r", 0) or 0) * 255)
            g = int((c.get("g", 0) or 0) * 255)
            b = int((c.get("b", 0) or 0) * 255)
            fills.append(f"#{r:02x}{g:02x}{b:02x}")
        for child in (node.get("children") or []):
            _walk(child)

    try:
        _walk(file_doc.get("document", {}))
    except Exception:
        return {}

    if not fills:
        return {}

    counter = Counter(fills)
    ordered = [c for c, _ in counter.most_common()]

    def _luminance(hex_color: str) -> float:
        r = int(hex_color[1:3], 16)
        g = int(hex_color[3:5], 16)
        b = int(hex_color[5:7], 16)
        return 0.299 * r + 0.587 * g + 0.114 * b

    # Background: most common fill among the dark ones (luminance < 80).
    # Falls back to most common fill if none are dark.
    dark_fills = [c for c in ordered if _luminance(c) < 80]
    background = dark_fills[0] if dark_fills else ordered[0] if ordered else None

    is_dark = bool(background and _luminance(background) < 128)

    # Accent: most SATURATED fill in the mid-luminance range (60–220).
    # Saturation = (max_channel - min_channel) / max_channel.
    # This picks the "pop" color (e.g. gold, coral) over neutral grays that
    # happen to land in the same luminance band but have low saturation.
    def _saturation(hex_color: str) -> float:
        r = int(hex_color[1:3], 16)
        g = int(hex_color[3:5], 16)
        b = int(hex_color[5:7], 16)
        mx = max(r, g, b)
        return (mx - min(r, g, b)) / mx if mx else 0.0

    mid_fills = [c for c in ordered if 60 <= _luminance(c) <= 220]
    saturated = sorted(mid_fills, key=_saturation, reverse=True)
    accent = saturated[0] if saturated else (ordered[-1] if len(ordered) > 1 else None)

    # After collecting fills, also collect typography from TEXT nodes
    fonts: dict[str, set[int]] = {}  # fontFamily → set of fontWeights

    def _walk_text(node: object) -> None:
        if not isinstance(node, dict):
            return
        if node.get("type") == "TEXT":
            style = node.get("style", {})
            family = style.get("fontFamily")
            weight = style.get("fontWeight")
            if family:
                fonts.setdefault(family, set())
                if isinstance(weight, (int, float)):
                    fonts[family].add(int(weight))
        for child in (node.get("children") or []):
            _walk_text(child)

    try:
        _walk_text(file_doc.get("document", {}))
    except Exception:
        pass

    # Dominant font = most-used family; collect its weights sorted
    dominant_font = max(fonts, key=lambda f: len(fonts[f])) if fonts else None
    dominant_weights = sorted(fonts.get(dominant_font, set())) if dominant_font else []

    return {
        "background": background,
        "accent": accent,
        "is_dark": is_dark,
        "swatches": ordered[:12],
        "font_family": dominant_font,        # e.g. "Inter"
        "font_weights": dominant_weights,    # e.g. [400, 700]
    }


async def _exec_fetch_figma(inp: dict, ctx: ToolContext) -> dict:
    if not ctx.figma_file_key:
        return {"is_error": True, "content": "No Figma file key configured for this prototype.", "tool_name": "fetch_figma"}
    if not ctx.figma_access_token:
        return {
            "is_error": True,
            "content": (
                "No Figma access token available for this prototype "
                "(connector not authorised)."
            ),
            "tool_name": "fetch_figma",
        }
    # Lazy import keeps tools.py importable without the backend connector stack;
    # the happy path only touches the network when actually called (untested in
    # unit tests, which exercise only the no-key / no-token branches).
    # NOTE: the connector helpers take (access_token, file_key) — the runner
    # (P1-04) resolves the token onto ctx; this executor never decrypts tokens.
    from app.connectors.figma_oauth import fetch_file, fetch_file_styles
    # Frame targeting: the agent may pass explicit frame_ids; when it doesn't,
    # fall back to ctx.figma_node_id (set when the user pasted a frame-level
    # Figma URL) so the first fetch targets that specific frame rather than the
    # file's default top-5. The [:5] cap applies in both cases.
    frame_ids = (inp.get("frame_ids") or ([ctx.figma_node_id] if ctx.figma_node_id else []))[:5]
    file_doc = await asyncio.to_thread(fetch_file, ctx.figma_access_token, ctx.figma_file_key)
    styles = await asyncio.to_thread(fetch_file_styles, ctx.figma_access_token, ctx.figma_file_key)
    frames = _extract_top_level_frames(file_doc, frame_ids)[:5]
    return {"frames": frames, "styles": styles, "palette": _extract_palette_summary(file_doc)}


async def _exec_read_console(inp: dict, ctx: ToolContext) -> dict:
    # AD20: no per-prototype runtime exists. Stub returns empty array.
    # Real implementation requires runtime instrumentation (deferred to v2).
    return {"entries": [], "note": "No prototype runtime configured (AD20 stub)."}


async def _exec_clarifying_question(inp: dict, ctx: ToolContext) -> dict:
    """Sentinel executor: returns the question payload as the tool_result. The
    RUNNER detects the sentinel by tool NAME (`clarifying_question`) and breaks
    the loop — the executor itself does NOT pause; the loop does. In practice the
    runner's sentinel-distinction branch fires BEFORE dispatch, so this executor
    is rarely reached on the loop path; it exists so a direct `dispatch(...)` call
    (and the AD17 dispatch-routes-to-executor non-breakage AC) still resolves to a
    structured payload. The `_sentinel` marker is for traceability only — the
    loop-break decision keys on the name, not on this return value."""
    return {
        "_sentinel": "clarifying_question",
        "question": inp.get("question"),
        "choices": inp.get("choices"),
        "context": inp.get("context"),
    }


async def _exec_propose_prd_patch(inp: dict, ctx: ToolContext) -> dict:
    """Sentinel #2 executor (P3-09, F11): persist a PROPOSED PRD patch as a sibling
    `prd_patches` row, then return a `_sentinel` payload. The RUNNER detects the
    sentinel by tool NAME (`propose_prd_patch`) and ends the loop as a terminal-
    COMPLETE (distinct from clarifying_question's terminal-PAUSE). The patch is
    persisted HERE (the side-effecting executor), so the runner's break arm calls
    `dispatch` for this tool precisely to run this body.

    F11: this NEVER touches `prds` — the proposal lives only in `prd_patches`; the
    PRD is rendered by applying applied patches on read (db.prd_patches
    `apply_patches_to_prd_md`). `prd_id` is read via a LAZY `get_prototype`
    (mirrors `_exec_fetch_figma`'s lazy connector import) so this module stays
    importable without the DB stack on the import path and no `prd_id` field has to
    be threaded onto `ToolContext`."""
    from app.db.prd_patches import insert_patch
    from app.db.prototypes import get_prototype
    proto = get_prototype(prototype_id=ctx.prototype_id, workspace_id=ctx.workspace_id)
    if not proto:
        return {"is_error": True, "content": "Prototype not found for PRD patch.", "tool_name": "propose_prd_patch"}
    row = insert_patch(
        prd_id=proto["prd_id"],
        prototype_id=ctx.prototype_id,
        workspace_id=ctx.workspace_id,
        rationale=inp["rationale"],
        patch_md=inp["patch_md"],
    )
    return {
        "_sentinel": "propose_prd_patch",
        "patch_id": row["id"],
        "content": "PRD patch proposed (pending user review). This ends your turn.",
    }
