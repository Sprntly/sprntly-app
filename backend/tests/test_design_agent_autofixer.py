"""Unit tests for the static AST autofixer (P1-10).

The four fixers live in the Node companion (autofixer.js) and are exercised
through the real Python wrapper -> Node subprocess path. Those tests are
guarded by `requires_babel`: they run when @babel/parser is resolvable from
prototype-runtime/node_modules (a dev env with the P0 Vite install) and skip
cleanly otherwise (e.g. the backend CI image installs Python deps only). The
wrapper-robustness tests need at most a bare `node` and patch the JS to force
each failure mode; the format/observability/runner-integration tests need
neither Node nor babel.

Async wrapper/loop coroutines are driven via `asyncio.run` (a fresh loop per
test, which manages the subprocess child watcher cleanly) — matching the
test_design_agent_runner.py convention.
"""
from __future__ import annotations

import asyncio
import copy
import logging
import shutil
import types

import pytest

from app.design_agent import autofixer, runner
from tests._fake_anthropic import _FakeStream

AUTOFIXER_LOGGER = "app.design_agent.autofixer"


# ─── Environment guards ──────────────────────────────────────────────────────

_NODE_PRESENT = shutil.which(autofixer._NODE_BIN) is not None
# Detection-based: resolve the same node_modules the wrapper uses (honours
# DESIGN_AGENT_NODE_PATH, which backend CI sets to an isolated @babel/parser
# install). When babel is resolvable the fixer tests RUN; they only skip on a
# bare local checkout with neither prototype-runtime deps nor the CI override.
_BABEL_PRESENT = (autofixer._node_modules_path() / "@babel" / "parser").exists()

requires_node = pytest.mark.skipif(
    not _NODE_PRESENT, reason="node binary not on PATH"
)
requires_babel = pytest.mark.skipif(
    not (_NODE_PRESENT and _BABEL_PRESENT),
    reason="@babel/parser not resolvable (no DESIGN_AGENT_NODE_PATH override "
    "and no prototype-runtime/node_modules install)",
)


def _run(coro):
    return asyncio.run(coro)


def _vfs(*paths: str) -> dict[str, str]:
    return {p: "" for p in paths}


# ─── Fixer (a): clean + hallucinated imports ─────────────────────────────────


@pytest.mark.integration
@requires_babel
def test_clean_tsx_returns_ok():
    content = (
        "import React from 'react';\n"
        "import { Button } from '@/components/ui/button';\n"
        "export default function App() {\n"
        "  return <div className=\"flex items-center gap-4 p-2 bg-slate-50\"><Button /></div>;\n"
        "}\n"
    )
    result = _run(autofixer.run("src/App.tsx", content, _vfs("src/App.tsx")))
    assert result == {"ok": True}


@pytest.mark.integration
@requires_babel
def test_hallucinated_relative_import_flagged():
    content = "import x from './missing';\nexport const a = 1;\n"
    result = _run(autofixer.run("src/App.tsx", content, _vfs("src/App.tsx")))
    assert result["ok"] is False
    assert result["errors"][0]["fixer"] == "hallucinated-import"


@pytest.mark.integration
@requires_babel
def test_hallucinated_at_alias_flagged():
    content = "import x from '@/lib/nonexistent';\nexport const a = 1;\n"
    result = _run(autofixer.run("src/App.tsx", content, _vfs("src/App.tsx")))
    assert result["ok"] is False
    assert result["errors"][0]["fixer"] == "hallucinated-import"


@pytest.mark.integration
@requires_babel
def test_hallucinated_package_flagged():
    content = "import x from 'react-magic';\nexport const a = 1;\n"
    result = _run(autofixer.run("src/App.tsx", content, _vfs("src/App.tsx")))
    assert result["ok"] is False
    assert result["errors"][0]["fixer"] == "hallucinated-import"
    assert "react-magic" in result["errors"][0]["message"]


@pytest.mark.integration
@requires_babel
def test_resolved_relative_import_passes():
    content = "import { Button } from './Button';\nexport const a = 1;\n"
    result = _run(autofixer.run(
        "src/App.tsx", content, _vfs("src/App.tsx", "src/Button.tsx"),
    ))
    assert result == {"ok": True}


@pytest.mark.integration
@requires_babel
def test_radix_ui_subpackages_pass():
    content = "import * as Slot from '@radix-ui/react-slot';\nexport const a = 1;\n"
    result = _run(autofixer.run("src/App.tsx", content, _vfs("src/App.tsx")))
    assert result == {"ok": True}


# ─── Fixer (c): shadcn component validation ──────────────────────────────────


@pytest.mark.integration
@requires_babel
def test_unknown_shadcn_component_flagged():
    content = "import { Foo } from '@/components/ui/foo';\nexport const a = 1;\n"
    result = _run(autofixer.run("src/App.tsx", content, _vfs("src/App.tsx")))
    assert result["ok"] is False
    err = result["errors"][0]
    assert err["fixer"] == "shadcn-component"
    # AC4: suggestion lists 8 example available components.
    names = [p for p in err["suggestion"].replace("Available:", "").split(",")]
    assert len([n for n in names if n.strip() and "…" not in n]) == 8


@pytest.mark.integration
@requires_babel
def test_known_shadcn_component_passes():
    content = "import { Button } from '@/components/ui/button';\nexport const a = 1;\n"
    result = _run(autofixer.run("src/App.tsx", content, _vfs("src/App.tsx")))
    assert result == {"ok": True}


# ─── Fixer (b): Tailwind class validation ────────────────────────────────────


@pytest.mark.integration
@requires_babel
def test_unknown_semantic_token_flagged():
    content = "export default function A() { return <div className=\"bg-foreground\" />; }\n"
    result = _run(autofixer.run("src/App.tsx", content, _vfs("src/App.tsx")))
    assert result["ok"] is False
    assert any(e["fixer"] == "tailwind-class" for e in result["errors"])


@pytest.mark.integration
@requires_babel
def test_arbitrary_value_passes():
    content = "export default function A() { return <div className=\"bg-[#abc] p-[14px]\" />; }\n"
    result = _run(autofixer.run("src/App.tsx", content, _vfs("src/App.tsx")))
    assert result == {"ok": True}


@pytest.mark.integration
@requires_babel
def test_valid_classes_pass():
    content = "export default function A() { return <div className=\"flex items-center gap-4 p-2 bg-slate-50\" />; }\n"
    result = _run(autofixer.run("src/App.tsx", content, _vfs("src/App.tsx")))
    assert result == {"ok": True}


@pytest.mark.integration
@requires_babel
def test_variant_prefix_passes():
    content = "export default function A() { return <div className=\"md:flex hover:bg-slate-100\" />; }\n"
    result = _run(autofixer.run("src/App.tsx", content, _vfs("src/App.tsx")))
    assert result == {"ok": True}


# ─── Fixer (d): JSX / TS syntax soundness ────────────────────────────────────


@pytest.mark.integration
@requires_babel
def test_unbalanced_tag_flagged():
    content = "export default function A() { return <div><span></div>; }\n"
    result = _run(autofixer.run("src/App.tsx", content, _vfs("src/App.tsx")))
    assert result["ok"] is False
    err = result["errors"][0]
    assert err["fixer"] == "jsx-syntax"
    assert err["line"] is not None


@pytest.mark.integration
@requires_babel
def test_invalid_ts_syntax_flagged():
    content = "const x: = 1;\n"
    result = _run(autofixer.run("src/App.tsx", content, _vfs("src/App.tsx")))
    assert result["ok"] is False
    assert result["errors"][0]["fixer"] == "jsx-syntax"


# ─── Non-validated file types ────────────────────────────────────────────────


def test_non_tsx_file_returns_ok_without_subprocess(monkeypatch):
    spawned = []

    async def _boom(*args, **kwargs):  # pragma: no cover - must not be called
        spawned.append(args)
        raise AssertionError("subprocess must not spawn for non-.tsx/.ts files")

    monkeypatch.setattr(autofixer.asyncio, "create_subprocess_exec", _boom)
    result = _run(autofixer.run("src/styles.css", "body { color: red; }", {}))
    assert result == {"ok": True}
    assert spawned == []


# ─── Wrapper robustness (best-effort: failures => ok:True + ONE warning) ─────


def test_node_missing_returns_ok_with_warning(monkeypatch, caplog):
    monkeypatch.setattr(autofixer, "_NODE_BIN", "/nonexistent/bin/node")
    with caplog.at_level(logging.WARNING, logger=AUTOFIXER_LOGGER):
        result = _run(autofixer.run("src/App.tsx", "export const a = 1;", _vfs("src/App.tsx")))
    assert result == {"ok": True}
    assert any("autofixer_node_missing" in r.getMessage() for r in caplog.records)


@requires_node
def test_subprocess_timeout_returns_ok_with_warning(monkeypatch, tmp_path, caplog):
    slow_js = tmp_path / "slow.js"
    slow_js.write_text("process.stdin.resume(); setTimeout(() => process.exit(0), 10000);\n")
    monkeypatch.setattr(autofixer, "_AUTOFIXER_JS", slow_js)
    monkeypatch.setattr(autofixer, "_SUBPROCESS_TIMEOUT_S", 0.5)
    with caplog.at_level(logging.WARNING, logger=AUTOFIXER_LOGGER):
        result = _run(autofixer.run("src/App.tsx", "export const a = 1;", _vfs("src/App.tsx")))
    assert result == {"ok": True}
    assert any("autofixer_timeout" in r.getMessage() for r in caplog.records)


@requires_node
def test_subprocess_returncode_nonzero_returns_ok_with_warning(monkeypatch, tmp_path, caplog):
    fail_js = tmp_path / "fail.js"
    fail_js.write_text("process.stdin.resume(); process.stdin.on('end', () => process.exit(2));\n")
    monkeypatch.setattr(autofixer, "_AUTOFIXER_JS", fail_js)
    # This test is about the EXIT-CODE path, not the timeout path. On a starved
    # 2-vCPU CI runner node's cold start can blow the default 8s budget and the
    # run logs autofixer_timeout instead — pin a generous budget so the
    # subprocess always reaches its exit(2).
    monkeypatch.setattr(autofixer, "_SUBPROCESS_TIMEOUT_S", 60.0)
    with caplog.at_level(logging.WARNING, logger=AUTOFIXER_LOGGER):
        result = _run(autofixer.run("src/App.tsx", "export const a = 1;", _vfs("src/App.tsx")))
    assert result == {"ok": True}
    assert any("autofixer_subprocess_failed" in r.getMessage() for r in caplog.records)


@requires_node
def test_subprocess_invalid_json_returns_ok_with_warning(monkeypatch, tmp_path, caplog):
    bad_js = tmp_path / "bad.js"
    bad_js.write_text(
        "process.stdin.resume();"
        " process.stdin.on('end', () => { process.stdout.write('this is not json'); process.exit(0); });\n"
    )
    monkeypatch.setattr(autofixer, "_AUTOFIXER_JS", bad_js)
    # Same starvation guard as the exit-code test above: this exercises the
    # invalid-JSON path, so the subprocess must never be cut off by the timeout.
    monkeypatch.setattr(autofixer, "_SUBPROCESS_TIMEOUT_S", 60.0)
    with caplog.at_level(logging.WARNING, logger=AUTOFIXER_LOGGER):
        result = _run(autofixer.run("src/App.tsx", "export const a = 1;", _vfs("src/App.tsx")))
    assert result == {"ok": True}
    assert any("autofixer_invalid_json" in r.getMessage() for r in caplog.records)


# ─── format_errors_for_agent ─────────────────────────────────────────────────


def test_format_errors_returns_human_readable_string():
    result = {
        "ok": False,
        "errors": [
            {"fixer": "hallucinated-import", "line": 3, "col": None,
             "message": "Package 'react-magic' is not allowed.", "suggestion": "Use react."},
            {"fixer": "jsx-syntax", "line": None, "col": None,
             "message": "parse error", "suggestion": None},
        ],
    }
    rendered = autofixer.format_errors_for_agent(result)
    assert rendered.startswith("Static analysis failed")
    assert rendered.count("\n  - ") == 2
    assert "[hallucinated-import]" in rendered
    assert "line 3" in rendered
    assert "Suggestion: Use react." in rendered
    assert "(no location)" in rendered


def test_format_errors_for_ok_result_returns_pass_message():
    assert autofixer.format_errors_for_agent({"ok": True}) == "Static analysis passed."


# ─── Observability (AC12) ────────────────────────────────────────────────────


def test_no_file_content_in_log_records(monkeypatch, caplog):
    monkeypatch.setattr(autofixer, "_NODE_BIN", "/nonexistent/bin/node")
    secret = "SENSITIVE_PROTOTYPE_BODY_do_not_log_123"
    with caplog.at_level(logging.WARNING, logger=AUTOFIXER_LOGGER):
        _run(autofixer.run("src/App.tsx", secret, _vfs("src/App.tsx")))
    assert caplog.records, "expected at least one warning record"
    assert all(secret not in r.getMessage() for r in caplog.records)


# ─── Runner integration (P1-04 agent_loop hook) ──────────────────────────────


class _FakeBlock:
    def __init__(self, data):
        self._data = data

    def model_dump(self):
        return copy.deepcopy(self._data)


class _FakeMessage:
    def __init__(self, stop_reason, blocks):
        self.stop_reason = stop_reason
        self.content = [_FakeBlock(b) for b in blocks]
        self.usage = types.SimpleNamespace(
            cache_creation_input_tokens=0, cache_read_input_tokens=0,
            input_tokens=0, output_tokens=0,
        )


class _RecordingClient:
    def __init__(self, responses):
        self._responses = list(responses)
        self.calls = []
        self.messages = types.SimpleNamespace(create=self._create, stream=self._stream)

    def _create(self, **kwargs):
        self.calls.append({"messages": copy.deepcopy(kwargs.get("messages"))})
        i = len(self.calls) - 1
        return self._responses[i] if i < len(self._responses) else self._responses[-1]

    def _stream(self, **kwargs):
        return _FakeStream(self._create(**kwargs))


def _tool_use(tid, name, inp):
    return {"type": "tool_use", "id": tid, "name": name, "input": inp}


def _system():
    return [
        {"type": "text", "text": "You are the Design Agent."},
        {"type": "text", "text": "tools", "cache_control": {"type": "ephemeral", "ttl": "1h"}},
    ]


def _user():
    return {"role": "user", "content": [{"type": "text", "text": "Build it."}]}


def _install_client(monkeypatch, responses):
    client = _RecordingClient(responses)
    monkeypatch.setattr(runner, "get_design_agent_client", lambda: client)
    return client


def test_runner_invokes_autofixer_after_write_tsx(monkeypatch):
    async def fake_dispatch(name, inp, ctx, allowed_names=None):
        ctx.virtual_fs[inp["path"]] = inp.get("content", "")
        return {"content": "wrote", "path": inp["path"]}

    async def fake_autofixer(file_path, content, vfs):
        return {"ok": False, "errors": [
            {"fixer": "hallucinated-import", "line": 1, "col": None,
             "message": "bad import here", "suggestion": None},
        ]}

    monkeypatch.setattr(runner, "dispatch", fake_dispatch)
    monkeypatch.setattr(runner, "autofixer_run", fake_autofixer)
    client = _install_client(monkeypatch, [
        _FakeMessage("tool_use", [_tool_use("t1", "write", {"path": "src/App.tsx", "content": "x"})]),
        _FakeMessage("end_turn", [{"type": "text", "text": "done"}]),
    ])
    ctx = runner.ToolContext(prototype_id=1, workspace_id="app", virtual_fs={})
    _run(runner.agent_loop(_system(), _user(), ctx))

    tr = client.calls[1]["messages"][-1]["content"][0]
    assert tr["type"] == "tool_result"
    assert tr["is_error"] is True
    assert "bad import here" in tr["content"]  # JSON-serialised formatted feedback


def test_runner_skips_autofixer_for_write_ts_failure(monkeypatch):
    calls = []

    async def fail_dispatch(name, inp, ctx, allowed_names=None):
        return {"is_error": True, "content": "write failed"}

    async def spy_autofixer(file_path, content, vfs):
        calls.append(file_path)
        return {"ok": True}

    monkeypatch.setattr(runner, "dispatch", fail_dispatch)
    monkeypatch.setattr(runner, "autofixer_run", spy_autofixer)
    _install_client(monkeypatch, [
        _FakeMessage("tool_use", [_tool_use("t1", "write", {"path": "src/App.tsx", "content": "x"})]),
        _FakeMessage("end_turn", [{"type": "text", "text": "done"}]),
    ])
    ctx = runner.ToolContext(prototype_id=1, workspace_id="app", virtual_fs={})
    _run(runner.agent_loop(_system(), _user(), ctx))
    assert calls == []  # autofixer not invoked when the write itself errored


def test_runner_skips_autofixer_for_non_tsx_files(monkeypatch):
    calls = []

    async def fake_dispatch(name, inp, ctx, allowed_names=None):
        ctx.virtual_fs[inp["path"]] = inp.get("content", "")
        return {"content": "wrote", "path": inp["path"]}

    async def spy_autofixer(file_path, content, vfs):
        calls.append(file_path)
        return {"ok": True}

    monkeypatch.setattr(runner, "dispatch", fake_dispatch)
    monkeypatch.setattr(runner, "autofixer_run", spy_autofixer)
    _install_client(monkeypatch, [
        _FakeMessage("tool_use", [_tool_use("t1", "write", {"path": "package.json", "content": "{}"})]),
        _FakeMessage("end_turn", [{"type": "text", "text": "done"}]),
    ])
    ctx = runner.ToolContext(prototype_id=1, workspace_id="app", virtual_fs={})
    _run(runner.agent_loop(_system(), _user(), ctx))
    assert calls == []  # package.json is not .tsx/.ts → autofixer not invoked
