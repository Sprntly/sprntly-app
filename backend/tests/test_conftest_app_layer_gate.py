"""Contract tests for the HTTP-layer reload gate in `tests/conftest.py`.

`isolated_settings` no longer reloads `app.main` + every route module for all
10,850 fast-lane tests. It asks `file_needs_app_layer` whether the test FILE
mentions the app at all, and only then pays the ~450 ms. That gate is the whole
performance win, and it is also the whole risk: if it ever answers "no" for a
file that really does drive the app, the test runs against whatever `app.main`
the previous test in the xdist worker left behind.

These tests pin both directions of the gate, and pin the guard that turns a
wrong "no" into a loud, named failure instead of an ordering-dependent mystery.
"""

from __future__ import annotations

import ast
import re
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from tests import conftest as ct

TESTS_DIR = Path(__file__).parent


def _write(tmp_path: Path, name: str, body: str) -> str:
    p = tmp_path / name
    p.write_text(body)
    return str(p)


# ── the gate itself ──────────────────────────────────────────────────────────


def test_unmarked_file_does_not_need_the_app_layer(tmp_path):
    """A file with no app marker must answer False.

    This is the half that a neutered `file_needs_app_layer` (one hardcoded to
    True, or one whose marker scan stopped working) fails.
    """
    path = _write(
        tmp_path,
        "test_pure_logic.py",
        "def test_adds():\n    assert 1 + 1 == 2\n",
    )
    assert ct.file_needs_app_layer(path) is False


@pytest.mark.parametrize("marker", ct._APP_MARKERS)
def test_every_marker_triggers_the_app_layer(tmp_path, marker):
    """Each marker, on its own, must flip the gate to True.

    Parametrised over the real tuple so a marker that gets silently dropped
    from `_APP_MARKERS` cannot go unnoticed — and so a gate hardcoded to False
    fails here.
    """
    path = _write(
        tmp_path,
        f"test_marked_{re.sub(r'[^a-z]', '_', marker.lower())}.py",
        f"def test_uses({marker.split('.')[0]}=None):\n"
        f"    # exercises {marker}\n"
        f"    assert True\n",
    )
    assert ct.file_needs_app_layer(path) is True


def test_gate_distinguishes_the_two_files(tmp_path):
    """The gate must return DIFFERENT answers for the two shapes.

    A constant-valued gate (always True or always False) passes neither of the
    two tests above at once; this one states the invariant directly so the
    mutation is impossible to miss.
    """
    plain = _write(tmp_path, "test_plain.py", "def test_x():\n    assert True\n")
    appish = _write(
        tmp_path, "test_appish.py", "def test_x(app_client):\n    assert app_client\n"
    )
    assert ct.file_needs_app_layer(plain) != ct.file_needs_app_layer(appish)
    assert ct.file_needs_app_layer(appish) is True


def test_gate_answer_is_cached_per_path(tmp_path):
    """Answers are memoised per path — the scan must not re-read on every test."""
    path = _write(tmp_path, "test_cached.py", "def test_x():\n    assert True\n")
    assert ct.file_needs_app_layer(path) is False
    # Rewrite the file with a marker; the cached answer must win.
    Path(path).write_text("def test_x(app_client):\n    assert app_client\n")
    assert ct.file_needs_app_layer(path) is False
    ct._needs_app_layer.pop(path, None)
    assert ct.file_needs_app_layer(path) is True


def test_unreadable_file_is_assumed_to_need_the_app(tmp_path):
    """Failing to read the source must fall back to the SAFE answer.

    Being wrong this way is merely slow. Being wrong the other way is a
    mystery failure in an unrelated test.
    """
    assert ct.file_needs_app_layer(str(tmp_path / "does_not_exist.py")) is True


# ── only the marked half pays the reload ─────────────────────────────────────


def test_only_marked_files_trigger_the_reload(tmp_path, monkeypatch):
    """End-to-end on the decision `isolated_settings` makes.

    Spies on `_reload_modules` — the single function both halves funnel
    through — and drives the same gate the fixture drives.
    """
    calls: list[list[str]] = []
    monkeypatch.setattr(ct, "_reload_modules", lambda names: calls.append(list(names)))

    plain = _write(tmp_path, "test_plain2.py", "def test_x():\n    assert True\n")
    appish = _write(tmp_path, "test_appish2.py", "def test_x(tenant_client):\n    pass\n")

    for path, expected_reloads in ((plain, 0), (appish, 1)):
        calls.clear()
        if ct.file_needs_app_layer(path):
            ct.reload_app_layer()
        assert len(calls) == expected_reloads, path

    # And when it does fire, it reloads the HTTP layer — app.main included.
    calls.clear()
    ct.reload_app_layer()
    assert calls == [list(ct._APP_RELOAD_ORDER)]
    assert "app.main" in calls[0]


def test_app_main_is_not_in_the_per_test_half():
    """The expensive module must NOT be back in the per-test list.

    `app.main` was 275 ms of the 469 ms. If a future edit moves it back into
    `_RELOAD_ORDER`, the entire saving is gone and nothing else would notice.
    """
    assert "app.main" not in ct._RELOAD_ORDER
    assert "app.main" in ct._APP_RELOAD_ORDER
    assert not set(ct._RELOAD_ORDER) & set(ct._APP_RELOAD_ORDER)
    # Every route module belongs to the app half.
    assert not [m for m in ct._RELOAD_ORDER if m.startswith("app.routes.")]


# ── a wrong "no" fails loudly, not mysteriously ──────────────────────────────


def test_building_a_client_under_an_unmarked_file_raises(tmp_path, monkeypatch):
    """The guard: a misclassified file gets a named error, not a stale app.

    Without this, a fixture that builds a TestClient for a file lacking a
    marker would silently reuse the previous test's `app.main`.
    """
    plain = _write(tmp_path, "test_unmarked_client.py", "def test_x():\n    assert True\n")
    monkeypatch.setattr(ct, "_current_test_file", plain)

    with pytest.raises(RuntimeError) as exc:
        TestClient(FastAPI())

    msg = str(exc.value)
    assert "test_unmarked_client.py" in msg, "the error must name the offending file"
    assert "_APP_MARKERS" in msg, "the error must say how to fix it"


def test_building_a_client_under_a_marked_file_is_allowed(tmp_path, monkeypatch):
    """The guard must not fire for a properly marked file."""
    appish = _write(
        tmp_path, "test_marked_client.py", "def test_x(unauth_client):\n    pass\n"
    )
    monkeypatch.setattr(ct, "_current_test_file", appish)
    assert TestClient(FastAPI()) is not None


# ── the drift guard that caught the real gap ─────────────────────────────────


def _calls_testclient(node: ast.AST) -> bool:
    """True if this function actually CALLS TestClient(...).

    Deliberately a call check, not a substring one: `_reset_iterate_limiter`
    merely mentions TestClient in its docstring, and counting that would make
    this drift guard cry wolf.
    """
    for sub in ast.walk(node):
        if not isinstance(sub, ast.Call):
            continue
        func = sub.func
        name = (
            func.id
            if isinstance(func, ast.Name)
            else func.attr
            if isinstance(func, ast.Attribute)
            else ""
        )
        if name.endswith("TestClient"):
            return True
    return False


def _client_building_fixtures() -> set[str]:
    """Names of conftest fixtures whose body constructs a TestClient."""
    tree = ast.parse((TESTS_DIR / "conftest.py").read_text())
    found = set()
    for node in tree.body:
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        is_fixture = any("fixture" in ast.dump(dec) for dec in node.decorator_list)
        if is_fixture and _calls_testclient(node):
            found.add(node.name)
    return found


def test_every_client_building_fixture_is_a_marker():
    """Any conftest fixture that builds a client MUST be in `_APP_MARKERS`.

    This is the invariant the original cut of this change violated: the test
    file only ever names such a fixture in its signature, so the source scan
    cannot see the app any other way. `tenant_client` and `unauth_client` were
    both missing, which silently left 69 files running against a stale app.
    """
    missing = sorted(
        name
        for name in _client_building_fixtures()
        if not any(m in name for m in ct._APP_MARKERS)
    )
    assert not missing, (
        f"conftest fixtures build a TestClient but are not app markers: {missing}. "
        f"Add them to _APP_MARKERS in tests/conftest.py, or every test file that "
        f"uses them will skip the HTTP-layer reload and run against a stale app."
    )


def test_known_client_fixtures_are_all_covered():
    """Belt and braces: the four fixtures we know build clients, by name."""
    for name in ("app_client", "unauth_client", "tenant_client", "company_client"):
        assert name in ct._APP_MARKERS, f"{name} must be an app marker"


# ── the scan is deliberately generous ────────────────────────────────────────


@pytest.mark.parametrize(
    "body",
    [
        pytest.param("# we deliberately avoid TestClient here\ndef test_x():\n    assert True\n", id="comment"),
        pytest.param('"""Nothing to do with app.main."""\ndef test_x():\n    assert True\n', id="docstring"),
        pytest.param('MSG = "see app_client for the authed variant"\ndef test_x():\n    assert True\n', id="string-literal"),
    ],
)
def test_a_marker_in_a_comment_or_string_still_counts(tmp_path, body):
    """A mention in a comment/string returns True, and that is CORRECT.

    The scan is a plain substring match, so prose counts. This is the safe
    direction: the file pays ~450 ms it may not need, and nothing breaks. The
    tempting "fix" is to parse the AST and only count real references — do not.
    An AST scan cannot see a file that names an app-building FIXTURE in a test
    signature, which is precisely how `tenant_client` and `unauth_client` went
    missing and left 69 files on a stale app. Over-matching is the design.
    """
    path = _write(tmp_path, "test_prose_only.py", body)
    assert ct.file_needs_app_layer(path) is True


def test_cross_module_fixture_import_needs_the_app_layer(tmp_path):
    """Importing fixtures from a sibling test module counts as touching the app.

    Found by the guard, not by inspection: `test_design_agent_cancel_route.py`
    does `from tests.test_design_agent_routes import env, client` and never
    names `TestClient` or any conftest fixture, so the first cut of the marker
    list classified it `no` — and its 8 tests then built a client against a
    stale app. Generic names like `client`/`env` are unusable as markers (they
    appear all over the suite), so any cross-test-module import is treated as
    "assume the app comes with it".
    """
    path = _write(
        tmp_path,
        "test_reuses_fixtures.py",
        "from tests.test_something_else import client, env\n"
        "def test_x(env, client):\n    assert client\n",
    )
    assert ct.file_needs_app_layer(path) is True


def test_real_cross_importing_files_are_classified_yes():
    """Every file in the tree that imports from a sibling test module.

    Guards the marker against being narrowed later: these are exactly the files
    whose app usage is invisible in their own source.
    """
    offenders = [
        f
        for f in sorted(TESTS_DIR.glob("test_*.py"))
        if "from tests.test_" in f.read_text(errors="ignore")
        and not ct.file_needs_app_layer(str(f))
    ]
    assert not offenders, (
        f"these files import fixtures from a sibling test module but are "
        f"classified as not needing the app layer: {[f.name for f in offenders]}"
    )
