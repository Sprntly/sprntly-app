"""Every keyword passed to `gateway.llm_call` must exist on `gateway.llm_call`.

This is a whole-codebase guard, not a test of one feature. The gateway is the
chokepoint every model call crosses, its signature grows a parameter at a time,
and a call site that passes one the gateway does not have raises TypeError at
RUN time — on a background path that is error-isolated, so it surfaces as
"warming quietly stopped working", not as a red test.

It has now happened twice:

  * `run_synthesis` passed `batch_deadline_s` to `llm_call` before that
    parameter existed; every scheduled brief would have died. Caught only
    because an unrelated skills-loader test happened to import the module.
  * `evidence_runner` and `prd_runner` passed `batch_label` to `llm_call`,
    which derives its own label as f"{agent}.{purpose}" and accepts no such
    argument. `call_json`/`call_md` DO accept it, which is exactly why the
    mistake looks right.

Both were signature mismatches invisible to any test that asserts on source
text. This binds the real signature instead.
"""
from __future__ import annotations

import ast
import inspect
from pathlib import Path

import pytest

from app.graph.gateway import llm_call

_APP = Path(__file__).resolve().parent.parent / "app"


def _accepted() -> set[str]:
    params = inspect.signature(llm_call).parameters
    if any(p.kind is inspect.Parameter.VAR_KEYWORD for p in params.values()):
        pytest.skip("llm_call takes **kwargs; this guard cannot narrow it")
    return set(params)


def _call_sites() -> list[tuple[str, int, list[str]]]:
    """Every `llm_call(...)` in app/, as (file, line, keyword names)."""
    out: list[tuple[str, int, list[str]]] = []
    for path in sorted(_APP.rglob("*.py")):
        try:
            tree = ast.parse(path.read_text())
        except SyntaxError:  # pragma: no cover - would fail elsewhere first
            continue
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            fn = node.func
            name = fn.id if isinstance(fn, ast.Name) else getattr(fn, "attr", None)
            if name != "llm_call":
                continue
            kws = [k.arg for k in node.keywords if k.arg is not None]
            out.append((str(path.relative_to(_APP.parent)), node.lineno, kws))
    return out


def test_the_scan_finds_the_known_call_sites():
    """Guard the guard: a broken scan would make every assertion below vacuous."""
    sites = _call_sites()
    assert len(sites) >= 10, f"only found {len(sites)} llm_call sites — scan is broken"
    files = {f for f, _, _ in sites}
    assert any("prd_runner" in f for f in files)
    assert any("evidence_runner" in f for f in files)


@pytest.mark.parametrize("site", _call_sites(), ids=lambda s: f"{s[0]}:{s[1]}")
def test_call_site_passes_only_parameters_llm_call_has(site):
    file, line, kwargs = site
    unknown = sorted(set(kwargs) - _accepted())
    assert not unknown, (
        f"{file}:{line} passes {unknown} to llm_call, which does not accept "
        f"{'them' if len(unknown) > 1 else 'it'}. "
        f"llm_call accepts: {sorted(_accepted())}"
    )
