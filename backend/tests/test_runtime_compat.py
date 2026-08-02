"""Guard against dev/CI Python drifting ahead of the deploy runtime.

Incident (2026-07-23, PR #864): a template f-string used a backslash inside a
replacement field — legal on the dev machine's Python 3.12 (PEP 701), a hard
SyntaxError on the 3.11 that staging and prod actually run. Local tests and
CI (then on 3.12) were green; the service would have failed to import the
module on deploy. Caught only by compiling on the box.

Two defenses, both here:

1. CI must run the SAME minor version as the EC2 runtime, so newer-only
   syntax fails in CI the way it would fail on deploy. `test_ci_python_
   matches_runtime` pins the workflow to RUNTIME_PYTHON and fails if someone
   bumps CI without also (deliberately) updating this constant alongside a
   real runtime upgrade.

2. `test_all_app_files_compile` compiles every backend/app file under the
   interpreter the suite runs on. On CI (pinned to the runtime version) this
   is a true deploy-syntax gate for every file — including ones no other
   test imports.

Why not ast.parse(feature_version=(3, 11)) on newer interpreters: verified
2026-07-23 that it does NOT reject PEP 701 backslash-in-f-string on a 3.12
interpreter (feature_version approximates the old grammar but not the old
f-string tokenizer), so it silently misses exactly the incident class.
"""
from __future__ import annotations

import py_compile
import re
import sys
from pathlib import Path

# The EC2 deploy runtime (prod `sprintly` + staging `sprintly-staging` venvs).
# Update ONLY as part of an actual runtime upgrade on the box.
RUNTIME_PYTHON = "3.11"

REPO_ROOT = Path(__file__).resolve().parents[2]
APP_DIR = REPO_ROOT / "backend" / "app"
WORKFLOWS = REPO_ROOT / ".github" / "workflows"


def test_ci_python_matches_runtime():
    """Every setup-python pin in the test workflow must equal the deploy
    runtime — a newer CI Python accepts syntax the deploy will reject."""
    workflow = WORKFLOWS / "test-backend.yml"
    text = workflow.read_text(encoding="utf-8")
    pins = re.findall(r"python-version:\s*['\"]([^'\"]+)['\"]", text)
    assert pins, f"no python-version pins found in {workflow}"
    for pin in pins:
        assert pin == RUNTIME_PYTHON, (
            f"{workflow.name} pins python-version '{pin}' but the EC2 runtime "
            f"is {RUNTIME_PYTHON}. CI must match the runtime, or a "
            f"newer-Python-only SyntaxError ships to deploy unseen. If the "
            f"box's runtime was genuinely upgraded, update RUNTIME_PYTHON "
            f"in this test in the same PR."
        )


def test_all_app_files_compile():
    """Compile every app file under the current interpreter. On CI (pinned to
    the runtime version) this catches deploy-breaking syntax in files no other
    test happens to import."""
    failures = []
    for f in sorted(APP_DIR.rglob("*.py")):
        try:
            py_compile.compile(str(f), doraise=True, cfile=None)
        except py_compile.PyCompileError as e:
            failures.append(f"{f.relative_to(REPO_ROOT)}: {e.msg}")
    assert not failures, (
        f"files that do not compile on Python "
        f"{sys.version_info.major}.{sys.version_info.minor}:\n"
        + "\n".join(failures)
    )
