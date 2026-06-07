"""Meta-test: enforce that any test function invoking real subprocesses or
_typecheck_runtime_break (real tsc --noEmit, without a subprocess mock) carries
@pytest.mark.integration.

This prevents the fast lane (pytest -m "not integration") from silently
re-bloating as new tests are added. The check is static — it parses test
source files with ast, not by running the tests.
"""
from __future__ import annotations

import ast
from pathlib import Path

_TESTS_DIR = Path(__file__).parent
# subprocess.* calls are always heavy (real I/O) unless the caller patches them
# via monkeypatch — but we flag at the callee level, not the patcher level.
# _typecheck_runtime_break is heavy only when called WITHOUT a monkeypatch
# fixture (i.e. the real tsc binary runs). Tests that mock subprocess.run before
# calling _typecheck_runtime_break accept `monkeypatch` as a parameter; the
# real-tsc tests do not. See _calls_heavy_io for the distinction.
_HEAVY_PATTERNS = frozenset({
    "subprocess",
    "create_subprocess_exec",
    "create_subprocess_shell",
})

# Patterns that are heavy only when the test does NOT accept `monkeypatch`
# (i.e. cannot be mocking the subprocess that the callee invokes).
_HEAVY_IF_NO_MONKEYPATCH = frozenset({
    "_typecheck_runtime_break",  # real tsc --noEmit; fast-lane tests patch storage.subprocess first
})


def _has_monkeypatch_param(func_node: ast.FunctionDef | ast.AsyncFunctionDef) -> bool:
    """Return True if the function signature includes a `monkeypatch` parameter."""
    args = func_node.args
    all_args = (
        args.posonlyargs + args.args + args.kwonlyargs
        + ([args.vararg] if args.vararg else [])
        + ([args.kwarg] if args.kwarg else [])
    )
    return any(a.arg == "monkeypatch" for a in all_args)


def _calls_heavy_io(func_node: ast.FunctionDef | ast.AsyncFunctionDef) -> bool:
    """Return True if the test function performs real heavy I/O.

    Rules:
    - Direct subprocess.* attribute calls are always heavy (the caller must have
      mocked them externally, but we detect at the direct-call level).
    - _typecheck_runtime_break(...) is heavy only when the function has NO
      `monkeypatch` parameter — real-tsc integration tests don't patch subprocess,
      while fast-lane tests accept `monkeypatch` and patch storage.subprocess.run
      before calling _typecheck_runtime_break.

    Excludes references that appear only as arguments to other calls
    (e.g. monkeypatch.setattr, inspect.signature) — those are mocked,
    not real subprocess invocations.
    """
    has_monkeypatch = _has_monkeypatch_param(func_node)

    for node in ast.walk(func_node):
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        # Direct attribute call: subprocess.run(...), asyncio.create_subprocess_exec(...)
        if isinstance(func, ast.Attribute) and func.attr in _HEAVY_PATTERNS:
            return True
        # Direct name call: subprocess(...) [uncommon but defensive]
        if isinstance(func, ast.Name) and func.id in _HEAVY_PATTERNS:
            return True
        # _typecheck_runtime_break as attribute: storage._typecheck_runtime_break(...)
        if isinstance(func, ast.Attribute) and func.attr in _HEAVY_IF_NO_MONKEYPATCH:
            if not has_monkeypatch:
                return True
        # _typecheck_runtime_break as bare name (unlikely but defensive)
        if isinstance(func, ast.Name) and func.id in _HEAVY_IF_NO_MONKEYPATCH:
            if not has_monkeypatch:
                return True
    return False


def _has_integration_marker(func_node: ast.FunctionDef) -> bool:
    """Return True if the function has @pytest.mark.integration."""
    for decorator in func_node.decorator_list:
        # @pytest.mark.integration
        if (
            isinstance(decorator, ast.Attribute)
            and decorator.attr == "integration"
            and isinstance(decorator.value, ast.Attribute)
            and decorator.value.attr == "mark"
        ):
            return True
        # @mark.integration (unlikely but defensive)
        if isinstance(decorator, ast.Attribute) and decorator.attr == "integration":
            return True
    return False


def test_all_subprocess_tests_are_marked_integration():
    """Every test function that calls subprocess/_typecheck_runtime_break must carry @pytest.mark.integration."""
    violations: list[str] = []

    for path in sorted(_TESTS_DIR.glob("test_*.py")):
        source = path.read_text(encoding="utf-8")
        tree = ast.parse(source, filename=str(path))

        for node in ast.walk(tree):
            if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            if not node.name.startswith("test_"):
                continue
            if _calls_heavy_io(node) and not _has_integration_marker(node):
                violations.append(f"{path.name}::{node.name}")

    assert not violations, (
        "The following test functions use real subprocess/_typecheck_runtime_break "
        "but are missing @pytest.mark.integration. "
        "Add the marker or mock the heavy I/O.\n"
        + "\n".join(f"  - {v}" for v in violations)
    )
