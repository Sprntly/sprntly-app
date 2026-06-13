"""Unit tests for the codebase-map live repo reader.

All GitHub API calls are stubbed with MagicMock — no real network, no
installation token required.  The stubbing pattern mirrors the one used in
test_design_system_github_ui_primitives.py (patch github_app.requests,
headers_for_installation, fetch_repo_tree, fetch_repo_meta, and
get_installation_token so the token-plumbing short-circuits cleanly).

Plain-engineering note: source files for this module must contain no internal
engagement coordinates.  The test_no_prohibited_tokens_in_source test verifies
this by constructing the pattern at runtime so the literals it checks for are
not themselves present in this file as continuous strings.
"""
from __future__ import annotations

import base64
import logging
import subprocess
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch, call

import pytest

from app.design_agent.codebase_map.repo_reader import (
    RepoSnapshot,
    RepoReader,
    _MAX_FILES,
    _MAX_TREE_ENTRIES,
    _MAX_FILE_BYTES,
    _MAX_EXTRA_FILE_BYTES,
    _detect_frontend_root,
    read_repo,
)

# ── helpers ─────────────────────────────────────────────────────────────────────

_STUB_TOKEN = "ghs_stubtoken"
_STUB_HEADERS = {"Authorization": f"Bearer {_STUB_TOKEN}", "Accept": "application/vnd.github+json"}
_STUB_SHA = "deadbeef1234567890abcdef"
_STUB_REPO = "org/repo"
_STUB_BRANCH = "dev"


def _b64(text: str) -> str:
    return base64.b64encode(text.encode()).decode()


def _ok_response(json_body):
    """Return a fake requests.Response with .ok=True and .json()=json_body."""
    resp = MagicMock()
    resp.ok = True
    resp.status_code = 200
    resp.json.return_value = json_body
    return resp


def _err_response(status: int):
    resp = MagicMock()
    resp.ok = False
    resp.status_code = status
    return resp


def _contents_payload(path: str, text: str, size: int | None = None) -> dict:
    encoded = _b64(text)
    return {
        "path": path,
        "encoding": "base64",
        "content": encoded,
        "size": size if size is not None else len(text.encode()),
    }


def _make_commits_resp(sha: str = _STUB_SHA) -> MagicMock:
    return _ok_response({"sha": sha})


def _base_patches(
    extra_tree: list[str] | None = None,
    meta_branch: str = "main",
    commits_resp=None,
):
    """Return a dict of patch kwargs commonly shared across happy-path tests."""
    return {
        "get_installation_token": patch(
            "app.connectors.github_app.get_installation_token",
            return_value=_STUB_TOKEN,
        ),
        "headers_for_installation": patch(
            "app.connectors.github_app.headers_for_installation",
            return_value=_STUB_HEADERS,
        ),
        "fetch_repo_tree": patch(
            "app.connectors.github_app.fetch_repo_tree",
            return_value=extra_tree or ["src/App.tsx", "src/index.tsx"],
        ),
        "fetch_repo_meta": patch(
            "app.connectors.github_app.fetch_repo_meta",
            return_value={"default_branch": meta_branch},
        ),
    }


# ── happy-path tests ─────────────────────────────────────────────────────────────

def test_read_repo_happy_returns_snapshot():
    """read_repo with a well-stubbed happy path returns a complete RepoSnapshot."""
    file_text = "export const App = () => <div>Hello</div>"
    commits_body = {"sha": _STUB_SHA}
    contents_body = _contents_payload("src/App.tsx", file_text)

    mock_requests = MagicMock()
    # First request: GET /commits/dev  → SHA
    # Subsequent requests: GET /contents/src/App.tsx, /contents/src/index.tsx
    index_contents = _contents_payload("src/index.tsx", "import React from 'react'")
    mock_requests.get.side_effect = [
        _ok_response(commits_body),
        _ok_response(contents_body),
        _ok_response(index_contents),
    ]

    with patch("app.connectors.github_app.get_installation_token", return_value=_STUB_TOKEN), \
         patch("app.connectors.github_app.headers_for_installation", return_value=_STUB_HEADERS), \
         patch("app.connectors.github_app.fetch_repo_tree", return_value=["src/App.tsx", "src/index.tsx"]), \
         patch("app.connectors.github_app.fetch_repo_meta", return_value={"default_branch": "main"}), \
         patch("app.connectors.github_app.requests", mock_requests):

        snapshot = read_repo(123, _STUB_REPO, f"{_STUB_REPO}@{_STUB_BRANCH}")

    assert snapshot is not None
    assert isinstance(snapshot, RepoSnapshot)
    assert snapshot.commit_sha == _STUB_SHA
    assert snapshot.branch == _STUB_BRANCH
    assert snapshot.repo == _STUB_REPO
    assert "src/App.tsx" in snapshot.files
    assert snapshot.files["src/App.tsx"] == file_text


def test_ref_none_uses_default_branch():
    """When ref is None, SHA resolution uses fetch_repo_meta's default_branch."""
    commits_body = {"sha": _STUB_SHA}
    mock_requests = MagicMock()
    mock_requests.get.side_effect = [
        _ok_response(commits_body),
        _ok_response(_contents_payload("src/App.tsx", "hello")),
    ]

    mock_fetch_repo_meta = MagicMock(return_value={"default_branch": "main"})

    with patch("app.connectors.github_app.get_installation_token", return_value=_STUB_TOKEN), \
         patch("app.connectors.github_app.headers_for_installation", return_value=_STUB_HEADERS), \
         patch("app.connectors.github_app.fetch_repo_tree", return_value=["src/App.tsx"]), \
         patch("app.connectors.github_app.fetch_repo_meta", mock_fetch_repo_meta), \
         patch("app.connectors.github_app.requests", mock_requests):

        snapshot = read_repo(123, _STUB_REPO, None)

    assert snapshot is not None
    # fetch_repo_meta was called — confirms the default-branch fallback path ran
    mock_fetch_repo_meta.assert_called_once_with(_STUB_TOKEN, _STUB_REPO)
    # The commits request was made against "main"
    commits_call_url = mock_requests.get.call_args_list[0][0][0]
    assert "/commits/main" in commits_call_url
    assert snapshot.branch == "main"


def test_base64_file_decodes():
    """A base64-encoded payload decodes correctly into files."""
    expected_text = "hello world"
    commits_body = {"sha": _STUB_SHA}
    mock_requests = MagicMock()
    mock_requests.get.side_effect = [
        _ok_response(commits_body),
        _ok_response(_contents_payload("src/hello.ts", expected_text)),
    ]

    with patch("app.connectors.github_app.get_installation_token", return_value=_STUB_TOKEN), \
         patch("app.connectors.github_app.headers_for_installation", return_value=_STUB_HEADERS), \
         patch("app.connectors.github_app.fetch_repo_tree", return_value=["src/hello.ts"]), \
         patch("app.connectors.github_app.fetch_repo_meta", return_value={"default_branch": "main"}), \
         patch("app.connectors.github_app.requests", mock_requests):

        snapshot = read_repo(123, _STUB_REPO, f"{_STUB_REPO}@{_STUB_BRANCH}")

    assert snapshot is not None
    assert snapshot.files.get("src/hello.ts") == expected_text


# ── bounded I/O tests ────────────────────────────────────────────────────────────

def test_tree_cap_truncates():
    """A tree with 1000 entries is capped at _MAX_TREE_ENTRIES and snapshot.truncated is True."""
    large_tree = [f"src/file{i}.ts" for i in range(1000)]
    commits_body = {"sha": _STUB_SHA}
    mock_requests = MagicMock()
    # commits call + contents for up to _MAX_FILES files
    mock_requests.get.side_effect = (
        [_ok_response(commits_body)]
        + [_ok_response(_contents_payload(p, "x")) for p in large_tree[:_MAX_FILES]]
    )

    with patch("app.connectors.github_app.get_installation_token", return_value=_STUB_TOKEN), \
         patch("app.connectors.github_app.headers_for_installation", return_value=_STUB_HEADERS), \
         patch("app.connectors.github_app.fetch_repo_tree", return_value=large_tree[:_MAX_TREE_ENTRIES]), \
         patch("app.connectors.github_app.fetch_repo_meta", return_value={"default_branch": "main"}), \
         patch("app.connectors.github_app.requests", mock_requests):

        snapshot = read_repo(123, _STUB_REPO, f"{_STUB_REPO}@{_STUB_BRANCH}")

    assert snapshot is not None
    assert len(snapshot.tree_paths) <= _MAX_TREE_ENTRIES
    assert snapshot.truncated is True


def test_file_cap_truncates_and_stops_fetching():
    """Requesting 100 paths caps at _MAX_FILES and sets truncated; no extra contents calls."""
    many_paths = [f"src/file{i}.ts" for i in range(100)]
    commits_body = {"sha": _STUB_SHA}
    mock_requests = MagicMock()
    mock_requests.get.side_effect = (
        [_ok_response(commits_body)]
        + [_ok_response(_contents_payload(p, f"body{i}")) for i, p in enumerate(many_paths)]
    )

    with patch("app.connectors.github_app.get_installation_token", return_value=_STUB_TOKEN), \
         patch("app.connectors.github_app.headers_for_installation", return_value=_STUB_HEADERS), \
         patch("app.connectors.github_app.fetch_repo_tree", return_value=many_paths), \
         patch("app.connectors.github_app.fetch_repo_meta", return_value={"default_branch": "main"}), \
         patch("app.connectors.github_app.requests", mock_requests):

        snapshot = read_repo(123, _STUB_REPO, f"{_STUB_REPO}@{_STUB_BRANCH}")

    assert snapshot is not None
    assert len(snapshot.files) <= _MAX_FILES
    assert snapshot.truncated is True
    # commits call + at most _MAX_FILES contents calls
    contents_calls = mock_requests.get.call_count - 1  # subtract the commits call
    assert contents_calls <= _MAX_FILES


def test_oversize_file_skipped():
    """A file whose size exceeds _MAX_FILE_BYTES is omitted from files silently."""
    oversize_payload = {
        "path": "src/big.ts",
        "encoding": "base64",
        "content": _b64("x" * 10),
        "size": _MAX_FILE_BYTES + 1,  # too big
    }
    small_payload = _contents_payload("src/small.ts", "small content")
    commits_body = {"sha": _STUB_SHA}
    mock_requests = MagicMock()
    mock_requests.get.side_effect = [
        _ok_response(commits_body),
        _ok_response(oversize_payload),
        _ok_response(small_payload),
    ]

    with patch("app.connectors.github_app.get_installation_token", return_value=_STUB_TOKEN), \
         patch("app.connectors.github_app.headers_for_installation", return_value=_STUB_HEADERS), \
         patch("app.connectors.github_app.fetch_repo_tree", return_value=["src/big.ts", "src/small.ts"]), \
         patch("app.connectors.github_app.fetch_repo_meta", return_value={"default_branch": "main"}), \
         patch("app.connectors.github_app.requests", mock_requests):

        snapshot = read_repo(123, _STUB_REPO, f"{_STUB_REPO}@{_STUB_BRANCH}")

    assert snapshot is not None
    assert "src/big.ts" not in snapshot.files
    assert "src/small.ts" in snapshot.files


# ── error handling / edge tests ──────────────────────────────────────────────────

def test_falsy_installation_returns_none():
    """read_repo(0, ...) and read_repo(None, ...) return None without raising."""
    assert read_repo(0, _STUB_REPO, None) is None
    assert read_repo(None, _STUB_REPO, None) is None


def test_repo_without_slash_returns_none():
    """A repo string without a '/' returns None without raising."""
    assert read_repo(123, "norepo", None) is None


def test_transport_error_on_sha_returns_none():
    """When the commits API raises a transport exception, read_repo returns None."""
    mock_requests = MagicMock()
    mock_requests.get.side_effect = ConnectionError("network down")

    with patch("app.connectors.github_app.get_installation_token", return_value=_STUB_TOKEN), \
         patch("app.connectors.github_app.headers_for_installation", return_value=_STUB_HEADERS), \
         patch("app.connectors.github_app.fetch_repo_tree", return_value=["src/App.tsx"]), \
         patch("app.connectors.github_app.fetch_repo_meta", return_value={"default_branch": "main"}), \
         patch("app.connectors.github_app.requests", mock_requests):

        result = read_repo(123, _STUB_REPO, f"{_STUB_REPO}@{_STUB_BRANCH}")

    assert result is None


def test_single_file_fetch_error_skips_and_marks_truncated():
    """When one file's contents request raises, that file is skipped, truncated is True,
    and other files are still returned."""
    good_payload = _contents_payload("src/good.ts", "good content")
    commits_body = {"sha": _STUB_SHA}

    mock_requests = MagicMock()
    # commits succeeds, first file raises, second file succeeds
    mock_requests.get.side_effect = [
        _ok_response(commits_body),
        ConnectionError("timeout on bad file"),
        _ok_response(good_payload),
    ]

    with patch("app.connectors.github_app.get_installation_token", return_value=_STUB_TOKEN), \
         patch("app.connectors.github_app.headers_for_installation", return_value=_STUB_HEADERS), \
         patch("app.connectors.github_app.fetch_repo_tree", return_value=["src/bad.ts", "src/good.ts"]), \
         patch("app.connectors.github_app.fetch_repo_meta", return_value={"default_branch": "main"}), \
         patch("app.connectors.github_app.requests", mock_requests):

        snapshot = read_repo(123, _STUB_REPO, f"{_STUB_REPO}@{_STUB_BRANCH}")

    assert snapshot is not None
    assert "src/bad.ts" not in snapshot.files
    assert "src/good.ts" in snapshot.files
    assert snapshot.truncated is True


def test_non_base64_payload_skipped():
    """A payload with encoding != 'base64' is skipped without raising."""
    bad_payload = {"path": "src/weird.ts", "encoding": "utf-8", "content": "raw text", "size": 8}
    good_payload = _contents_payload("src/normal.ts", "normal")
    commits_body = {"sha": _STUB_SHA}
    mock_requests = MagicMock()
    mock_requests.get.side_effect = [
        _ok_response(commits_body),
        _ok_response(bad_payload),
        _ok_response(good_payload),
    ]

    with patch("app.connectors.github_app.get_installation_token", return_value=_STUB_TOKEN), \
         patch("app.connectors.github_app.headers_for_installation", return_value=_STUB_HEADERS), \
         patch("app.connectors.github_app.fetch_repo_tree", return_value=["src/weird.ts", "src/normal.ts"]), \
         patch("app.connectors.github_app.fetch_repo_meta", return_value={"default_branch": "main"}), \
         patch("app.connectors.github_app.requests", mock_requests):

        snapshot = read_repo(123, _STUB_REPO, f"{_STUB_REPO}@{_STUB_BRANCH}")

    assert snapshot is not None
    assert "src/weird.ts" not in snapshot.files
    assert "src/normal.ts" in snapshot.files


# ── auth / observability tests ───────────────────────────────────────────────────

def test_uses_installation_headers():
    """Every raw requests.get call uses the headers returned by headers_for_installation(123)."""
    custom_headers = {"Authorization": "Bearer verified_stub", "X-Custom": "yes"}
    commits_body = {"sha": _STUB_SHA}
    file_payload = _contents_payload("src/App.tsx", "content")
    mock_requests = MagicMock()
    mock_requests.get.side_effect = [
        _ok_response(commits_body),
        _ok_response(file_payload),
    ]

    mock_h4i = MagicMock(return_value=custom_headers)

    with patch("app.connectors.github_app.get_installation_token", return_value=_STUB_TOKEN), \
         patch("app.connectors.github_app.headers_for_installation", mock_h4i), \
         patch("app.connectors.github_app.fetch_repo_tree", return_value=["src/App.tsx"]), \
         patch("app.connectors.github_app.fetch_repo_meta", return_value={"default_branch": "main"}), \
         patch("app.connectors.github_app.requests", mock_requests):

        snapshot = read_repo(123, _STUB_REPO, f"{_STUB_REPO}@{_STUB_BRANCH}")

    assert snapshot is not None
    # headers_for_installation was invoked with the correct installation_id
    for actual_call in mock_h4i.call_args_list:
        assert actual_call == call(123)
    # Every raw requests.get call carried the custom_headers
    for raw_call in mock_requests.get.call_args_list:
        assert raw_call.kwargs.get("headers") == custom_headers


def test_read_emits_identifier_only_log(caplog):
    """A successful read emits exactly one codebase_map.repo_read INFO line that
    contains the repo and sha but does NOT contain any file body or token value."""
    file_text = "secret content that must not appear in logs"
    commits_body = {"sha": _STUB_SHA}
    mock_requests = MagicMock()
    mock_requests.get.side_effect = [
        _ok_response(commits_body),
        _ok_response(_contents_payload("src/App.tsx", file_text)),
    ]

    with caplog.at_level(logging.INFO, logger="app.design_agent.codebase_map.repo_reader"):
        with patch("app.connectors.github_app.get_installation_token", return_value=_STUB_TOKEN), \
             patch("app.connectors.github_app.headers_for_installation", return_value=_STUB_HEADERS), \
             patch("app.connectors.github_app.fetch_repo_tree", return_value=["src/App.tsx"]), \
             patch("app.connectors.github_app.fetch_repo_meta", return_value={"default_branch": "main"}), \
             patch("app.connectors.github_app.requests", mock_requests):

            snapshot = read_repo(123, _STUB_REPO, f"{_STUB_REPO}@{_STUB_BRANCH}")

    assert snapshot is not None
    repo_read_lines = [r for r in caplog.records if "repo_read" in r.getMessage()]
    assert len(repo_read_lines) == 1, f"Expected exactly 1 repo_read log line, got {len(repo_read_lines)}"
    log_msg = repo_read_lines[0].getMessage()
    assert _STUB_REPO in log_msg
    assert _STUB_SHA in log_msg
    # File body must NOT appear in the log line
    assert file_text not in log_msg
    # Token value must NOT appear in the log line
    assert _STUB_TOKEN not in log_msg


# ── module integrity tests ────────────────────────────────────────────────────────

def test_repo_reader_imports_without_anthropic():
    """The module loads successfully and does not pull in the anthropic package."""
    result = subprocess.run(
        [sys.executable, "-c",
         "import app.design_agent.codebase_map.repo_reader; "
         "import sys; assert 'anthropic' not in sys.modules"],
        capture_output=True, text=True,
    )
    assert result.returncode == 0, result.stderr


def test_no_prohibited_tokens_in_source():
    """Neither deliverable file contains internal engagement coordinates.

    The pattern is assembled at runtime from split parts so that the literals
    being checked do not appear verbatim in this test file itself.
    """
    import re
    repo_root = Path(__file__).parent.parent
    targets = [
        repo_root / "app" / "design_agent" / "codebase_map" / "repo_reader.py",
        Path(__file__),
    ]
    # Build the pattern by joining split fragments — the literals are never
    # contiguous in this source, so the test does not trip its own check.
    parts = [
        r"C[0-9]-[0-9]",
        "C" + "-series",
        r"H[0-9]-[0-9]",
        r"P[0-9]-[0-9]",
        r"\bAD[0-9]",
        r"\bF[0-9]{1,2}\b",
        "DB" + "D",
        "Babaji" + "de",
    ]
    pattern = "|".join(parts)
    for target in targets:
        text = target.read_text()
        matches = re.findall(pattern, text)
        assert not matches, f"Prohibited token(s) {matches} found in {target.name}"


# ── byte-cap extras (Change 1) ─────────────────────────────────────────────────


def _resp_any(url, **kw):
    """side_effect: SHA for the commits call, a 1-byte body for any contents call."""
    if "/commits/" in url:
        return _ok_response({"sha": _STUB_SHA})
    return _ok_response({"encoding": "base64", "content": _b64("x"), "size": 1})


def test_extra_path_over_tree_cap_under_extra_cap_is_fetched():
    """AC1: with always_fetch=n, a ~300 KB extra at idx < n is decoded (1 MiB
    ceiling) while a same-size path at idx >= n is dropped (128 KB tree cap)."""
    reader = RepoReader(123)
    payload = _contents_payload("f", "x" * 10, size=300_000)
    with patch(
        "app.design_agent.codebase_map.repo_reader._get_contents_raw",
        return_value=payload,
    ), patch.object(RepoReader, "_headers", return_value=_STUB_HEADERS):
        bodies, had_error = reader.fetch_files(
            _STUB_REPO, _STUB_BRANCH, ["extra.css", "tree.css"], always_fetch=1,
        )
    assert 300_000 < _MAX_EXTRA_FILE_BYTES
    assert 300_000 > _MAX_FILE_BYTES
    assert "extra.css" in bodies      # idx 0 < always_fetch → 1 MiB ceiling
    assert "tree.css" not in bodies   # idx 1 >= always_fetch → 128 KB cap drops it
    assert had_error is False


def test_tree_sampled_file_over_128k_still_dropped():
    """AC2: always_fetch=0 → every path keeps the 128 KB tree cap; a 200 KB
    file is dropped, not truncated, not fetched."""
    reader = RepoReader(123)
    payload = _contents_payload("big", "x" * 10, size=200_000)
    with patch(
        "app.design_agent.codebase_map.repo_reader._get_contents_raw",
        return_value=payload,
    ), patch.object(RepoReader, "_headers", return_value=_STUB_HEADERS):
        bodies, _ = reader.fetch_files(
            _STUB_REPO, _STUB_BRANCH, ["big.ts"], always_fetch=0,
        )
    assert "big.ts" not in bodies
    assert _MAX_FILE_BYTES == 128_000


def test_extras_fetched_first_beyond_max_files():
    """AC3: read_repo with more extras than _MAX_FILES still attempts every
    extra (extras occupy the front of fetch_list, always_fetch raises budget)."""
    extras = [f"dep/file{i}.ts" for i in range(_MAX_FILES + 5)]  # 45 > 40
    tree = ["src/App.tsx", "src/index.tsx"]
    mock_requests = MagicMock()
    mock_requests.get.side_effect = _resp_any

    with patch("app.connectors.github_app.get_installation_token", return_value=_STUB_TOKEN), \
         patch("app.connectors.github_app.headers_for_installation", return_value=_STUB_HEADERS), \
         patch("app.connectors.github_app.fetch_repo_tree", return_value=tree), \
         patch("app.connectors.github_app.fetch_repo_meta", return_value={"default_branch": "main"}), \
         patch("app.connectors.github_app.requests", mock_requests):
        snapshot = read_repo(
            123, _STUB_REPO, f"{_STUB_REPO}@{_STUB_BRANCH}", extra_paths=extras,
        )

    assert snapshot is not None
    for e in extras:
        assert e in snapshot.files  # every extra attempted + decoded, none clipped


def test_no_extras_behaviour_unchanged():
    """AC4: read_repo(extra_paths=None) fetches only tree paths at the 128 KB
    cap — a 200 KB tree file is dropped, behaviour byte-for-byte as today."""
    big = _contents_payload("src/big.ts", "x" * 10, size=200_000)
    small = _contents_payload("src/small.ts", "ok")
    mock_requests = MagicMock()
    mock_requests.get.side_effect = [
        _ok_response({"sha": _STUB_SHA}),
        _ok_response(big),
        _ok_response(small),
    ]
    with patch("app.connectors.github_app.get_installation_token", return_value=_STUB_TOKEN), \
         patch("app.connectors.github_app.headers_for_installation", return_value=_STUB_HEADERS), \
         patch("app.connectors.github_app.fetch_repo_tree", return_value=["src/big.ts", "src/small.ts"]), \
         patch("app.connectors.github_app.fetch_repo_meta", return_value={"default_branch": "main"}), \
         patch("app.connectors.github_app.requests", mock_requests):
        snapshot = read_repo(
            123, _STUB_REPO, f"{_STUB_REPO}@{_STUB_BRANCH}", extra_paths=None,
        )
    assert snapshot is not None
    assert "src/big.ts" not in snapshot.files
    assert "src/small.ts" in snapshot.files


# ── frontend-aware read budget (Change 5) ──────────────────────────────────────


def test_detect_frontend_root_monorepo_root_and_none():
    """AC13: web/ via a package.json declaring next/react; '' for a repo-root
    app whose manifest is at the root; '' when no manifest + no App-Router dir."""
    import json as _json

    monorepo_tree = [
        "backend/app/main.py",
        "web/package.json",
        "web/app/(app)/page.tsx",
        "web/app/globals.css",
    ]
    monorepo_files = {
        "web/package.json": _json.dumps({"dependencies": {"next": "15", "react": "19"}}),
    }
    assert _detect_frontend_root(monorepo_tree, monorepo_files) == "web/"

    root_tree = ["package.json", "src/app/page.tsx"]
    root_files = {"package.json": _json.dumps({"dependencies": {"react": "19"}})}
    assert _detect_frontend_root(root_tree, root_files) == ""

    bare_tree = ["backend/app/main.py", "README.md", "Makefile"]
    assert _detect_frontend_root(bare_tree, {}) == ""


def test_detect_frontend_root_app_router_fallback():
    """AC13: with no package.json bodies, the App-Router page dir signal still
    finds the frontend prefix (web/app/**/page.tsx → 'web/')."""
    tree = ["backend/app/main.py", "web/app/(app)/sources/page.tsx"]
    assert _detect_frontend_root(tree, {}) == "web/"


def test_tree_cap_measures_frontend_after_filter():
    """AC14: a backend-first tree (600 backend paths BEFORE 50 web/app pages)
    keeps the web pages when frontend_root='web/' (cap applied AFTER the prefix
    filter); with no detected root the flat 600-cap clips them (today's bug)."""
    backend = [f"backend/app/file{i}.py" for i in range(600)]
    web = [f"web/app/route{i}/page.tsx" for i in range(50)]
    tree = backend + web

    mock_requests = MagicMock()
    mock_requests.get.side_effect = _resp_any
    base_patches = lambda: (  # noqa: E731 - test-local convenience
        patch("app.connectors.github_app.get_installation_token", return_value=_STUB_TOKEN),
        patch("app.connectors.github_app.headers_for_installation", return_value=_STUB_HEADERS),
        patch("app.connectors.github_app.fetch_repo_tree", return_value=tree),
        patch("app.connectors.github_app.fetch_repo_meta", return_value={"default_branch": "main"}),
        patch("app.connectors.github_app.requests", mock_requests),
    )

    p = base_patches()
    with p[0], p[1], p[2], p[3], p[4]:
        snap_fe = read_repo(123, _STUB_REPO, f"{_STUB_REPO}@{_STUB_BRANCH}", frontend_root="web/")
    assert snap_fe is not None
    assert any(path.startswith("web/app/") for path in snap_fe.tree_paths)
    assert all(path.startswith("web/") for path in snap_fe.tree_paths)

    # Flat behaviour: when detection finds no frontend root, the 600-cap clips
    # the backend-first listing and the web pages never survive.
    mock_requests2 = MagicMock()
    mock_requests2.get.side_effect = _resp_any
    with patch("app.connectors.github_app.get_installation_token", return_value=_STUB_TOKEN), \
         patch("app.connectors.github_app.headers_for_installation", return_value=_STUB_HEADERS), \
         patch("app.connectors.github_app.fetch_repo_tree", return_value=tree), \
         patch("app.connectors.github_app.fetch_repo_meta", return_value={"default_branch": "main"}), \
         patch("app.connectors.github_app.requests", mock_requests2), \
         patch("app.design_agent.codebase_map.repo_reader._detect_frontend_root", return_value=""):
        snap_flat = read_repo(123, _STUB_REPO, f"{_STUB_REPO}@{_STUB_BRANCH}")
    assert snap_flat is not None
    assert not any(path.startswith("web/") for path in snap_flat.tree_paths)


def test_read_repo_threads_frontend_root_into_tree_filter():
    """AC14: the prefix filter is applied BEFORE the _MAX_TREE_ENTRIES cap — a
    passed frontend_root yields a tree budget measured on frontend files only."""
    backend = [f"backend/file{i}.py" for i in range(100)]
    web = [f"web/app/route{i}/page.tsx" for i in range(700)]  # > _MAX_TREE_ENTRIES
    tree = backend + web
    mock_requests = MagicMock()
    mock_requests.get.side_effect = _resp_any

    with patch("app.connectors.github_app.get_installation_token", return_value=_STUB_TOKEN), \
         patch("app.connectors.github_app.headers_for_installation", return_value=_STUB_HEADERS), \
         patch("app.connectors.github_app.fetch_repo_tree", return_value=tree), \
         patch("app.connectors.github_app.fetch_repo_meta", return_value={"default_branch": "main"}), \
         patch("app.connectors.github_app.requests", mock_requests):
        snap = read_repo(123, _STUB_REPO, f"{_STUB_REPO}@{_STUB_BRANCH}", frontend_root="web/")

    assert snap is not None
    # 700 web paths filtered first, THEN capped at _MAX_TREE_ENTRIES — every
    # surviving path is a frontend path, none of the 100 backend paths leak in.
    assert len(snap.tree_paths) == _MAX_TREE_ENTRIES
    assert all(path.startswith("web/") for path in snap.tree_paths)
