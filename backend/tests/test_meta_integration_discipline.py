"""Meta-test: enforce that any test function invoking real subprocesses or real
vite builds carries @pytest.mark.integration.

This prevents the fast lane (pytest -m "not integration") from silently
re-bloating as new tests are added. The check is static — it parses test
source files with ast, not by running the tests.
"""
from __future__ import annotations

import ast
from pathlib import Path

_TESTS_DIR = Path(__file__).parent
_HEAVY_PATTERNS = frozenset({
    "subprocess",
    "create_subprocess_exec",
    "create_subprocess_shell",
    "vite_build",
})


def _calls_heavy_io(node: ast.AST) -> bool:
    """Return True if the AST node contains a Name or Attribute matching a heavy pattern."""
    for child in ast.walk(node):
        if isinstance(child, ast.Name) and child.id in _HEAVY_PATTERNS:
            return True
        if isinstance(child, ast.Attribute) and child.attr in _HEAVY_PATTERNS:
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
    """Every test function that calls subprocess/vite_build must carry @pytest.mark.integration."""
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
        "The following test functions use real subprocess/vite_build "
        "but are missing @pytest.mark.integration. "
        "Add the marker or mock the heavy I/O.\n"
        + "\n".join(f"  - {v}" for v in violations)
    )
