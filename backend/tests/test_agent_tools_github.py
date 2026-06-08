"""Tests for the GitHub agent tools (C1 of the agent-tools-github slice).

Five tools the LLM picks at chat-time to fetch live data from GitHub:

  github_search_code(query, repo, limit)        — GitHub's code-search API
  github_get_file(repo, path, ref)              — read a single file
  github_list_files(repo, path, ref)            — list dir contents (or a file)
  github_get_pr_diff(repo, pr_number)           — diff text for a PR
  github_list_commits(repo, branch, since, limit) — commit history

Each tool uses an existing installation_id (looked up from the company's
connection row). All HTTP is mocked in tests.

The tool *registry* (separate file in the same module) describes each
tool's JSON Schema so the model knows what arguments to pass. The
dispatch helper picks the right Python function and calls it with the
LLM-provided arguments + the resolved installation_id.
"""
from __future__ import annotations

import importlib
import sys
from unittest.mock import patch, MagicMock

import pytest

import app.auth  # noqa: F401


# ─────────────────────── registry ───────────────────────


def test_registry_lists_github_tools(isolated_settings):
    """Sanity: importing the github tool module registers tools and they
    appear in the global registry."""
    from app.agent_tools import registry
    from app.agent_tools import github as gh  # noqa: F401 — registers via import

    names = {t["name"] for t in registry.list_tools()}
    # Five tools the slice ships.
    assert {
        "github_search_code",
        "github_get_file",
        "github_list_files",
        "github_get_pr_diff",
        "github_list_commits",
    }.issubset(names)


def test_each_tool_has_anthropic_compatible_schema(isolated_settings):
    """Anthropic tools need: name (str), description (str), input_schema
    (object). Smoke-check the shape so the loop won't 400."""
    from app.agent_tools import registry
    import app.agent_tools.github  # noqa: F401

    for t in registry.list_tools():
        assert isinstance(t.get("name"), str) and t["name"]
        assert isinstance(t.get("description"), str) and t["description"]
        schema = t.get("input_schema")
        assert isinstance(schema, dict) and schema.get("type") == "object"
        assert "properties" in schema


def test_registry_dispatch_unknown_tool_raises(isolated_settings):
    from app.agent_tools import registry

    with pytest.raises(KeyError):
        registry.dispatch("does_not_exist", {"x": 1}, installation_id=1)


# ─────────────────────── github_get_file ───────────────────────


def _patched_headers():
    """Avoid real GitHub App JWT signing — every tool calls
    `github_app.headers_for_installation(installation_id)` internally."""
    return patch(
        "app.agent_tools.github._headers_for",
        return_value={"Authorization": "Bearer fake", "Accept": "x"},
    )


def test_github_get_file_returns_decoded_text(isolated_settings, monkeypatch):
    from app.agent_tools import github as gh

    mock_resp = MagicMock(ok=True, status_code=200)
    # GitHub's contents API base64-encodes the file body.
    import base64

    contents_bytes = b"# Hello\nThis is the README.\n"
    mock_resp.json.return_value = {
        "type": "file",
        "name": "README.md",
        "path": "README.md",
        "size": len(contents_bytes),
        "content": base64.b64encode(contents_bytes).decode(),
        "encoding": "base64",
        "sha": "abc123",
    }
    monkeypatch.setattr(gh.requests, "get", lambda *a, **kw: mock_resp)
    with _patched_headers():
        out = gh.github_get_file(
            installation_id=1, repo="acme/widgets", path="README.md", ref="main"
        )

    assert out["path"] == "README.md"
    assert out["content"] == contents_bytes.decode()
    assert out["sha"] == "abc123"


def test_github_get_file_404_returns_error_shape(isolated_settings, monkeypatch):
    from app.agent_tools import github as gh

    mock_resp = MagicMock(ok=False, status_code=404, text="Not Found")
    mock_resp.json.return_value = {"message": "Not Found"}
    monkeypatch.setattr(gh.requests, "get", lambda *a, **kw: mock_resp)
    with _patched_headers():
        out = gh.github_get_file(
            installation_id=1, repo="acme/widgets", path="missing.txt"
        )
    assert out["error"] == "not_found"


def test_github_get_file_url_shape(isolated_settings, monkeypatch):
    """URL must be /repos/{repo}/contents/{path}?ref={ref}; ref optional."""
    from app.agent_tools import github as gh

    captured = {}

    def _fake(url, headers=None, params=None, timeout=None):
        captured["url"] = url
        captured["params"] = params
        m = MagicMock(ok=True, status_code=200)
        m.json.return_value = {
            "type": "file",
            "name": "x",
            "path": "x",
            "size": 0,
            "content": "",
            "encoding": "base64",
            "sha": "0",
        }
        return m

    monkeypatch.setattr(gh.requests, "get", _fake)
    with _patched_headers():
        gh.github_get_file(installation_id=1, repo="a/b", path="src/x.py", ref="dev")

    assert captured["url"] == "https://api.github.com/repos/a/b/contents/src/x.py"
    assert captured["params"] == {"ref": "dev"}


# ─────────────────────── github_list_files ───────────────────────


def test_github_list_files_returns_dir_entries(isolated_settings, monkeypatch):
    from app.agent_tools import github as gh

    mock_resp = MagicMock(ok=True, status_code=200)
    mock_resp.json.return_value = [
        {"type": "file", "name": "app.py", "path": "src/app.py", "size": 120, "sha": "f1"},
        {"type": "dir", "name": "utils", "path": "src/utils", "size": 0, "sha": "d1"},
    ]
    monkeypatch.setattr(gh.requests, "get", lambda *a, **kw: mock_resp)
    with _patched_headers():
        out = gh.github_list_files(
            installation_id=1, repo="acme/widgets", path="src"
        )

    assert "entries" in out
    by_name = {e["name"]: e for e in out["entries"]}
    assert by_name["app.py"]["type"] == "file"
    assert by_name["utils"]["type"] == "dir"


# ─────────────────────── github_search_code ───────────────────────


def test_github_search_code_returns_hits(isolated_settings, monkeypatch):
    from app.agent_tools import github as gh

    mock_resp = MagicMock(ok=True, status_code=200)
    mock_resp.json.return_value = {
        "total_count": 2,
        "items": [
            {
                "name": "auth.py",
                "path": "backend/auth.py",
                "repository": {"full_name": "acme/widgets"},
                "html_url": "https://github.com/acme/widgets/blob/main/backend/auth.py",
                "score": 1.0,
            },
            {
                "name": "auth_test.py",
                "path": "backend/auth_test.py",
                "repository": {"full_name": "acme/widgets"},
                "html_url": "https://github.com/acme/widgets/blob/main/backend/auth_test.py",
                "score": 0.5,
            },
        ],
    }
    monkeypatch.setattr(gh.requests, "get", lambda *a, **kw: mock_resp)
    with _patched_headers():
        out = gh.github_search_code(
            installation_id=1, query="login", repo="acme/widgets", limit=5
        )

    assert out["total"] == 2
    assert len(out["hits"]) == 2
    assert out["hits"][0]["path"] == "backend/auth.py"


def test_github_search_code_scopes_query_to_repo(
    isolated_settings, monkeypatch
):
    from app.agent_tools import github as gh

    captured = {}

    def _fake(url, headers=None, params=None, timeout=None):
        captured["params"] = params
        m = MagicMock(ok=True, status_code=200)
        m.json.return_value = {"total_count": 0, "items": []}
        return m

    monkeypatch.setattr(gh.requests, "get", _fake)
    with _patched_headers():
        gh.github_search_code(
            installation_id=1, query="useEffect", repo="acme/web", limit=10
        )

    assert "repo:acme/web" in captured["params"]["q"]
    assert "useEffect" in captured["params"]["q"]
    assert captured["params"]["per_page"] == 10


# ─────────────────────── github_get_pr_diff ───────────────────────


def test_github_get_pr_diff_returns_diff_text(isolated_settings, monkeypatch):
    from app.agent_tools import github as gh

    diff_text = (
        "diff --git a/src/app.py b/src/app.py\n"
        "@@ -1 +1,2 @@\n+ new line\n"
    )
    mock_resp = MagicMock(ok=True, status_code=200, text=diff_text)
    monkeypatch.setattr(gh.requests, "get", lambda *a, **kw: mock_resp)
    with _patched_headers():
        out = gh.github_get_pr_diff(
            installation_id=1, repo="acme/widgets", pr_number=42
        )
    assert out["diff"] == diff_text
    assert out["pr_number"] == 42


def test_github_get_pr_diff_uses_diff_accept_header(
    isolated_settings, monkeypatch
):
    from app.agent_tools import github as gh

    captured = {}

    def _fake(url, headers=None, timeout=None):
        captured["url"] = url
        captured["headers"] = headers
        return MagicMock(ok=True, status_code=200, text="diff")

    monkeypatch.setattr(gh.requests, "get", _fake)
    with _patched_headers():
        gh.github_get_pr_diff(installation_id=1, repo="a/b", pr_number=7)

    assert captured["url"] == "https://api.github.com/repos/a/b/pulls/7"
    assert captured["headers"]["Accept"] == "application/vnd.github.diff"


# ─────────────────────── github_list_commits ───────────────────────


def test_github_list_commits_returns_commit_summaries(
    isolated_settings, monkeypatch
):
    from app.agent_tools import github as gh

    mock_resp = MagicMock(ok=True, status_code=200)
    mock_resp.json.return_value = [
        {
            "sha": "abc1234",
            "commit": {
                "message": "feat: add login",
                "author": {"name": "Alice", "email": "a@x.com", "date": "2026-06-01T00:00:00Z"},
            },
            "html_url": "https://github.com/a/b/commit/abc1234",
        },
        {
            "sha": "def5678",
            "commit": {
                "message": "fix: handle null",
                "author": {"name": "Bob", "email": "b@y.com", "date": "2026-06-02T00:00:00Z"},
            },
            "html_url": "https://github.com/a/b/commit/def5678",
        },
    ]
    monkeypatch.setattr(gh.requests, "get", lambda *a, **kw: mock_resp)
    with _patched_headers():
        out = gh.github_list_commits(
            installation_id=1, repo="a/b", branch="main", limit=10
        )

    assert len(out["commits"]) == 2
    assert out["commits"][0]["sha"] == "abc1234"
    assert out["commits"][0]["message"] == "feat: add login"
    assert out["commits"][0]["author"] == "Alice"


# ─────────────────────── dispatch ───────────────────────


def test_dispatch_routes_to_correct_tool(isolated_settings, monkeypatch):
    """The dispatch helper should look up the tool by name and pass
    installation_id + tool args correctly."""
    from app.agent_tools import registry
    import app.agent_tools.github  # noqa: F401 — registers

    mock_resp = MagicMock(ok=True, status_code=200)
    mock_resp.json.return_value = {
        "type": "file",
        "name": "x",
        "path": "x",
        "size": 0,
        "content": "",
        "encoding": "base64",
        "sha": "s",
    }
    monkeypatch.setattr(
        sys.modules["app.agent_tools.github"].requests,
        "get",
        lambda *a, **kw: mock_resp,
    )
    with _patched_headers():
        result = registry.dispatch(
            "github_get_file",
            {"repo": "a/b", "path": "x"},
            installation_id=42,
        )
    assert result["path"] == "x"
