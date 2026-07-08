"""Unit tests for the Design Agent tool registry + dispatch (P1-03).

Tests are written as plain sync functions that drive the async executors via
`asyncio.run(...)`. This matches the existing `test_ask_runner.py` /
`test_evidence_runner.py` convention ("we test the sync path to avoid asyncio
plumbing") and means the suite runs identically with or without pytest-asyncio
installed — important because `tools.py` is pure-Python and unit-testable in
isolation, with no live backend required.
"""
from __future__ import annotations

import asyncio

from app.design_agent import tools
from app.design_agent.tools import (
    ACTION_TOOLS,
    SENTINEL_TOOLS,
    ToolContext,
    all_tools,
    dispatch,
    tool_definitions_for_mode,
)

AD17_ACTION_NAMES = {"view", "write", "line_replace", "search", "fetch_figma", "read_console"}


def _ctx(**overrides) -> ToolContext:
    base = dict(prototype_id=1, workspace_id="app", virtual_fs={})
    base.update(overrides)
    return ToolContext(**base)


def _run(coro):
    return asyncio.run(coro)


# ─── Creation / registry caps ─────────────────────────────────────────────


def test_action_tools_count_is_6():
    assert len(ACTION_TOOLS) == 6


def test_sentinel_tools_count_at_most_4():
    assert len(SENTINEL_TOOLS) <= 4


def test_sentinel_tools_holds_clarifying_question():
    # P3-08 landed sentinel #1 (clarifying_question); P3-09 landed sentinel #2
    # (propose_prd_patch). SENTINEL_TOOLS now holds exactly those two, in
    # declaration order, and the AD17 cap (≤4) still holds.
    assert [t.name for t in SENTINEL_TOOLS] == ["clarifying_question", "propose_prd_patch"]
    assert len(SENTINEL_TOOLS) <= 4
    assert all(t.category == "sentinel" for t in SENTINEL_TOOLS)


def test_all_action_tools_have_action_category():
    assert all(t.category == "action" for t in ACTION_TOOLS)


def test_all_sentinel_tools_have_sentinel_category():
    assert all(t.category == "sentinel" for t in SENTINEL_TOOLS)


def test_tool_names_match_ad17():
    assert {t.name for t in ACTION_TOOLS} == AD17_ACTION_NAMES


def test_no_seventh_action_tool():
    # AD17: the ACTION cap is inviolable — exactly 6 action tools, never a 7th,
    # regardless of how many sentinels are appended. all_tools() is the 6 actions
    # FIRST (stable order) then the sentinels (clarifying_question from P3-08).
    assert [t.name for t in ACTION_TOOLS] == [
        "view", "write", "line_replace", "search", "fetch_figma", "read_console",
    ]
    assert sum(1 for t in all_tools() if t.category == "action") == 6
    assert [t.name for t in all_tools()][:6] == [
        "view", "write", "line_replace", "search", "fetch_figma", "read_console",
    ]


# ─── Tool description quality (agent-build-research.md §1.4) ───────────────


def test_every_tool_description_at_least_200_chars():
    for t in all_tools():
        assert len(t.description) >= 200, f"{t.name} description too short ({len(t.description)})"


def test_every_tool_description_includes_negative_space():
    for t in all_tools():
        assert "Do NOT" in t.description, f"{t.name} missing negative-space 'Do NOT' clause"


def test_write_tool_description_forbids_data_anchor_id():
    # AC11 / AD4 — exact negative-space phrasing for the build-pipeline contract.
    desc = tools.WRITE.description
    assert "data-anchor-id" in desc
    # Negative-space phrasing per AC11 (verbatim from the ticket — the "do NOT
    # emit" clause is the build-pipeline contract for AD4 anchor IDs).
    assert "do NOT emit `data-anchor-id` attributes manually" in desc
    assert "the build pipeline applies them automatically" in desc
    assert "ignored or " in desc and "stripped" in desc


def test_view_tool_description_warns_against_blind_writes():
    desc = tools.VIEW.description
    assert "BEFORE editing" in desc
    assert "overwrite" in desc


# ─── Serialization for the Anthropic Messages API ─────────────────────────


def test_tool_definitions_for_mode_execute_shape():
    defs = tool_definitions_for_mode("execute")
    assert len(defs) == len(all_tools())
    for d in defs:
        assert set(d.keys()) == {"name", "description", "input_schema"}
        assert "category" not in d
        assert "execute" not in d


def test_tools_module_has_no_dead_alias():
    # `tool_definitions_for_api` was a back-compat alias for
    # `tool_definitions_for_mode("execute")` with zero production callers —
    # confirms the alias itself is gone, not just unused.
    import app.design_agent.tools as tools_mod

    assert not hasattr(tools_mod, "tool_definitions_for_api")


# ─── Dispatch: view ───────────────────────────────────────────────────────


def test_dispatch_view_existing_file_returns_numbered_content():
    ctx = _ctx(virtual_fs={"src/App.tsx": "line1\nline2"})
    res = _run(dispatch("view", {"path": "src/App.tsx"}, ctx))
    assert "is_error" not in res
    assert "1: line1" in res["content"]
    assert "2: line2" in res["content"]
    assert res["total_lines"] == 2


def test_dispatch_view_partial_range():
    ctx = _ctx(virtual_fs={"a.txt": "a\nb\nc\nd\ne"})
    res = _run(dispatch("view", {"path": "a.txt", "lines": [2, 4]}, ctx))
    assert "2: b" in res["content"]
    assert "4: d" in res["content"]
    assert "1: a" not in res["content"]


def test_dispatch_view_missing_file_returns_is_error_with_directory_hint():
    ctx = _ctx(virtual_fs={"src/components/App.tsx": "x"})
    res = _run(dispatch("view", {"path": "nope.tsx"}, ctx))
    assert res["is_error"] is True
    assert "File not found" in res["content"]
    assert "src/components" in res["content"]
    assert res["tool_name"] == "view"


def test_dispatch_view_truncates_large_file():
    body = "\n".join(f"row{i}" for i in range(700))  # 700 lines > 600 threshold
    ctx = _ctx(virtual_fs={"big.txt": body})
    res = _run(dispatch("view", {"path": "big.txt"}, ctx))
    assert res["total_lines"] == 700
    assert "truncated" in res["content"]
    assert "700 total lines" in res["content"]
    assert "1: row0" in res["content"]      # head present
    assert "700: row699" in res["content"]  # tail present


# ─── Dispatch: write ──────────────────────────────────────────────────────


def test_dispatch_write_populates_virtual_fs():
    ctx = _ctx()
    res = _run(dispatch("write", {"path": "src/New.tsx", "content": "hello"}, ctx))
    assert "is_error" not in res
    assert ctx.virtual_fs["src/New.tsx"] == "hello"
    assert res["path"] == "src/New.tsx"


# ─── Dispatch: line_replace ───────────────────────────────────────────────


def test_dispatch_line_replace_happy_path():
    ctx = _ctx(virtual_fs={"a.txt": "one\ntwo\nthree"})
    res = _run(dispatch("line_replace", {
        "path": "a.txt",
        "first_replaced_line": 2,
        "last_replaced_line": 2,
        "search": "two",
        "replace": "TWO",
    }, ctx))
    assert "is_error" not in res
    assert ctx.virtual_fs["a.txt"] == "one\nTWO\nthree"


def test_dispatch_line_replace_mismatch_returns_actual_content():
    ctx = _ctx(virtual_fs={"a.txt": "one\ntwo\nthree"})
    res = _run(dispatch("line_replace", {
        "path": "a.txt",
        "first_replaced_line": 2,
        "last_replaced_line": 2,
        "search": "WRONG",
        "replace": "X",
    }, ctx))
    assert res["is_error"] is True
    assert "search/replace mismatch" in res["content"]
    assert "two" in res["content"]  # the file's actual line is surfaced for recovery
    assert ctx.virtual_fs["a.txt"] == "one\ntwo\nthree"  # unchanged


def test_dispatch_line_replace_invalid_range_returns_is_error():
    ctx = _ctx(virtual_fs={"a.txt": "one\ntwo"})
    res = _run(dispatch("line_replace", {
        "path": "a.txt",
        "first_replaced_line": 1,
        "last_replaced_line": 99,
        "search": "x",
        "replace": "y",
    }, ctx))
    assert res["is_error"] is True
    assert "Invalid line range" in res["content"]


def test_dispatch_line_replace_missing_file_is_error():
    ctx = _ctx()
    res = _run(dispatch("line_replace", {
        "path": "ghost.txt",
        "first_replaced_line": 1,
        "last_replaced_line": 1,
        "search": "x",
        "replace": "y",
    }, ctx))
    assert res["is_error"] is True
    assert res["tool_name"] == "line_replace"


# ─── Dispatch: search ─────────────────────────────────────────────────────


def test_dispatch_search_returns_up_to_25_matches_with_hint():
    body = "\n".join("match here" for _ in range(30))  # 30 matching lines
    ctx = _ctx(virtual_fs={"f.txt": body})
    res = _run(dispatch("search", {"pattern": "match"}, ctx))
    assert res["total"] == 30
    assert len(res["matches"]) == 25
    assert "hint" in res
    assert "5 more matches not shown" in res["hint"]


def test_dispatch_search_with_path_glob():
    ctx = _ctx(virtual_fs={
        "src/components/App.tsx": "const X = 1",
        "src/util.ts": "const X = 2",
        "lib/Other.tsx": "const X = 3",
    })
    res = _run(dispatch("search", {"pattern": "const X", "path_glob": "src/**/*.tsx"}, ctx))
    assert res["total"] == 1
    assert res["matches"][0]["path"] == "src/components/App.tsx"


def test_dispatch_search_returns_context_lines():
    ctx = _ctx(virtual_fs={"f.txt": "a\nb\nTARGET\nd\ne"})
    res = _run(dispatch("search", {"pattern": "TARGET"}, ctx))
    assert res["total"] == 1
    m = res["matches"][0]
    assert m["line"] == 3
    assert "TARGET" in m["context"]


def test_search_empty_fs_returns_zero_matches():
    ctx = _ctx(virtual_fs={})
    res = _run(dispatch("search", {"pattern": "anything"}, ctx))
    assert res == {"matches": [], "total": 0}


# ─── Dispatch: fetch_figma (no-key / no-token branches only; happy path needs live OAuth) ─


def test_dispatch_fetch_figma_no_key_returns_is_error():
    ctx = _ctx(figma_file_key=None)
    res = _run(dispatch("fetch_figma", {}, ctx))
    assert res["is_error"] is True
    assert "No Figma file key configured" in res["content"]
    assert res["tool_name"] == "fetch_figma"


def test_dispatch_fetch_figma_key_but_no_token_returns_is_error():
    ctx = _ctx(figma_file_key="ABC123", figma_access_token=None)
    res = _run(dispatch("fetch_figma", {}, ctx))
    assert res["is_error"] is True
    assert "access token" in res["content"]
    assert res["tool_name"] == "fetch_figma"


def test_extract_top_level_frames_filters_and_shapes():
    doc = {
        "document": {
            "children": [
                {"children": [
                    {"id": "1:1", "name": "Home", "type": "FRAME", "absoluteBoundingBox": {"w": 1}},
                    {"id": "1:2", "name": "About", "type": "FRAME", "absoluteBoundingBox": {"w": 2}},
                ]},
            ],
        },
    }
    all_frames = tools._extract_top_level_frames(doc, [])
    assert {f["name"] for f in all_frames} == {"Home", "About"}
    assert all_frames[0]["bounds"] == {"w": 1}
    filtered = tools._extract_top_level_frames(doc, ["1:2"])
    assert [f["id"] for f in filtered] == ["1:2"]


def test_extract_top_level_frames_defensive_on_partial_tree():
    assert tools._extract_top_level_frames({}, []) == []
    assert tools._extract_top_level_frames({"document": {}}, []) == []
    assert tools._extract_top_level_frames({"document": {"children": [None, 5]}}, []) == []


# ─── Dispatch: read_console (AD20 stub) ───────────────────────────────────


def test_dispatch_read_console_returns_empty_stub():
    res = _run(dispatch("read_console", {}, _ctx()))
    assert res["entries"] == []
    assert "AD20" in res["note"]


# ─── Error handling ───────────────────────────────────────────────────────


def test_dispatch_unknown_tool_lists_registered_tools():
    res = _run(dispatch("unknown_tool", {}, _ctx()))
    assert res["is_error"] is True
    assert "Unknown tool" in res["content"]
    for name in AD17_ACTION_NAMES:
        assert name in res["content"]
    assert res["tool_name"] == "unknown_tool"


def test_dispatch_swallows_execute_exceptions(monkeypatch):
    def boom(inp, ctx):
        raise ValueError("x")

    # The VIEW lambda resolves _exec_view from module globals at call time,
    # so patching the module attribute routes through the real dispatch path.
    monkeypatch.setattr(tools, "_exec_read_console", boom)
    res = _run(dispatch("read_console", {}, _ctx()))
    assert res["is_error"] is True
    assert res["content"] == "ValueError: x"
    assert res["tool_name"] == "read_console"


def test_dispatch_exception_path_carries_no_traceback(monkeypatch):
    # AC10 / AC12: error content is "<Class>: <msg>" only — never a traceback,
    # never file body content.
    def boom(inp, ctx):
        raise RuntimeError("kaboom")

    monkeypatch.setattr(tools, "_exec_read_console", boom)
    res = _run(dispatch("read_console", {}, _ctx()))
    assert res["content"] == "RuntimeError: kaboom"
    assert "Traceback" not in res["content"]
    assert 'File "' not in res["content"]


def test_dispatch_real_exception_via_bad_regex():
    # Drives the real exception-swallowing path (no monkeypatch): an invalid
    # regex makes re.compile raise inside _exec_search.
    res = _run(dispatch("search", {"pattern": "["}, _ctx()))
    assert res["is_error"] is True
    assert res["tool_name"] == "search"
    assert "error" in res["content"].lower()


def test_error_returns_carry_tool_name():
    # Every error branch surfaces tool_name for the runner's routing/logging.
    ctx = _ctx(virtual_fs={})
    cases = [
        ("view", {"path": "missing"}),
        ("line_replace", {"path": "missing", "first_replaced_line": 1, "last_replaced_line": 1, "search": "", "replace": ""}),
        ("fetch_figma", {}),
        ("unknown", {}),
    ]
    for name, inp in cases:
        res = _run(dispatch(name, inp, ctx))
        assert res.get("is_error") is True
        assert res["tool_name"] == name


# ─── Edge cases ───────────────────────────────────────────────────────────


def test_dispatch_runs_under_asyncio():
    res = asyncio.run(dispatch("read_console", {}, _ctx()))
    assert res["entries"] == []


def test_parallel_dispatch_via_gather():
    ctx = _ctx(virtual_fs={"src/App.tsx": "alpha\nbeta"})

    async def go():
        return await asyncio.gather(
            dispatch("view", {"path": "src/App.tsx"}, ctx),
            dispatch("search", {"pattern": "beta"}, ctx),
        )

    view_res, search_res = asyncio.run(go())
    assert "alpha" in view_res["content"]
    assert search_res["total"] == 1
    # Shared ctx.virtual_fs not corrupted by concurrent reads.
    assert ctx.virtual_fs == {"src/App.tsx": "alpha\nbeta"}
