"""GitHub agent tools — five capabilities the agent picks at chat-time.

Each tool is a thin wrapper around a GitHub REST endpoint using the
existing installation-token plumbing in `app.connectors.github_app`.
The route layer resolves `installation_id` from the company's
connection row before calling dispatch().

Tool surface:
    github_search_code(query, repo, limit)
    github_get_file(repo, path, ref)
    github_list_files(repo, path, ref)
    github_get_pr_diff(repo, pr_number)
    github_list_commits(repo, branch, since, limit)

All tools return plain dicts (the LLM will see them serialised as JSON
in the tool_result block). On failure, they return a single-key
`{"error": "<reason>"}` dict so the model can react gracefully.
"""
from __future__ import annotations

import base64
import logging
from typing import Any

import requests

from app.agent_tools import registry
from app.connectors import github_app

logger = logging.getLogger(__name__)

GITHUB_API = "https://api.github.com"
_TIMEOUT = 15


def _headers_for(installation_id: int) -> dict[str, str]:
    """Indirection layer so tests can patch without touching github_app."""
    return github_app.headers_for_installation(installation_id)


# ─────────────────────── github_get_file ───────────────────────


def github_get_file(
    *,
    installation_id: int,
    repo: str,
    path: str,
    ref: str | None = None,
) -> dict[str, Any]:
    """Fetch a single file's contents from a repo at an optional ref."""
    params = {"ref": ref} if ref else {}
    r = requests.get(
        f"{GITHUB_API}/repos/{repo}/contents/{path}",
        headers=_headers_for(installation_id),
        params=params,
        timeout=_TIMEOUT,
    )
    if r.status_code == 404:
        return {"error": "not_found", "repo": repo, "path": path}
    if not r.ok:
        logger.warning(
            "github_get_file %s/%s: %s %s",
            repo, path, r.status_code, r.text[:200],
        )
        return {"error": f"http_{r.status_code}"}

    body = r.json() or {}
    if body.get("type") != "file":
        return {"error": "not_a_file", "type": body.get("type")}
    encoded = body.get("content") or ""
    try:
        decoded = base64.b64decode(encoded).decode("utf-8", errors="replace")
    except Exception:
        decoded = ""
    return {
        "repo": repo,
        "path": body.get("path"),
        "ref": ref,
        "size": body.get("size"),
        "sha": body.get("sha"),
        "content": decoded,
    }


# ─────────────────────── github_list_files ───────────────────────


def github_list_files(
    *,
    installation_id: int,
    repo: str,
    path: str = "",
    ref: str | None = None,
) -> dict[str, Any]:
    """List the contents of a directory (or describe a single file).

    GitHub's contents endpoint returns an array for directories and a
    single object for files. We normalise to a single `entries` array.
    """
    params = {"ref": ref} if ref else {}
    url_path = path.lstrip("/")
    r = requests.get(
        f"{GITHUB_API}/repos/{repo}/contents/{url_path}",
        headers=_headers_for(installation_id),
        params=params,
        timeout=_TIMEOUT,
    )
    if r.status_code == 404:
        return {"error": "not_found", "repo": repo, "path": path}
    if not r.ok:
        return {"error": f"http_{r.status_code}"}

    body = r.json()
    if isinstance(body, dict):
        body = [body]
    entries = [
        {
            "name": e.get("name"),
            "path": e.get("path"),
            "type": e.get("type"),
            "size": e.get("size"),
            "sha": e.get("sha"),
        }
        for e in body
    ]
    return {"repo": repo, "path": path, "entries": entries}


# ─────────────────────── github_search_code ───────────────────────


def github_search_code(
    *,
    installation_id: int,
    query: str,
    repo: str,
    limit: int = 10,
) -> dict[str, Any]:
    """Search code in a specific repo. GitHub's code search requires
    a `repo:` qualifier in the query for App auth."""
    limit = max(1, min(int(limit), 30))
    q = f"{query} repo:{repo}"
    r = requests.get(
        f"{GITHUB_API}/search/code",
        headers=_headers_for(installation_id),
        params={"q": q, "per_page": limit},
        timeout=_TIMEOUT,
    )
    if not r.ok:
        return {"error": f"http_{r.status_code}", "query": query}
    body = r.json() or {}
    hits = [
        {
            "name": item.get("name"),
            "path": item.get("path"),
            "repo": (item.get("repository") or {}).get("full_name"),
            "url": item.get("html_url"),
            "score": item.get("score"),
        }
        for item in (body.get("items") or [])
    ]
    return {
        "query": query,
        "repo": repo,
        "total": body.get("total_count", 0),
        "hits": hits,
    }


# ─────────────────────── github_get_pr_diff ───────────────────────


def github_get_pr_diff(
    *,
    installation_id: int,
    repo: str,
    pr_number: int,
) -> dict[str, Any]:
    """Fetch the unified diff text for a pull request."""
    headers = _headers_for(installation_id).copy()
    headers["Accept"] = "application/vnd.github.diff"
    r = requests.get(
        f"{GITHUB_API}/repos/{repo}/pulls/{pr_number}",
        headers=headers,
        timeout=_TIMEOUT,
    )
    if r.status_code == 404:
        return {"error": "not_found", "repo": repo, "pr_number": pr_number}
    if not r.ok:
        return {"error": f"http_{r.status_code}"}
    return {
        "repo": repo,
        "pr_number": pr_number,
        "diff": r.text,
    }


# ─────────────────────── github_list_commits ───────────────────────


def github_list_commits(
    *,
    installation_id: int,
    repo: str,
    branch: str | None = None,
    since: str | None = None,
    limit: int = 20,
) -> dict[str, Any]:
    """Recent commits on a branch."""
    limit = max(1, min(int(limit), 100))
    params: dict[str, Any] = {"per_page": limit}
    if branch:
        params["sha"] = branch
    if since:
        params["since"] = since
    r = requests.get(
        f"{GITHUB_API}/repos/{repo}/commits",
        headers=_headers_for(installation_id),
        params=params,
        timeout=_TIMEOUT,
    )
    if not r.ok:
        return {"error": f"http_{r.status_code}"}
    body = r.json() or []
    commits = []
    for c in body:
        commit = c.get("commit") or {}
        author = commit.get("author") or {}
        commits.append(
            {
                "sha": c.get("sha"),
                "message": commit.get("message"),
                "author": author.get("name"),
                "author_email": author.get("email"),
                "date": author.get("date"),
                "url": c.get("html_url"),
            }
        )
    return {"repo": repo, "branch": branch, "commits": commits}


# ─────────────────────── Register all tools ───────────────────────


def _register_all() -> None:
    registry.register(
        {
            "name": "github_get_file",
            "description": (
                "Read a single file from a GitHub repository the user has "
                "connected to Sprntly. Returns the file's content as text."
            ),
            "input_schema": {
                "type": "object",
                "properties": {
                    "repo": {
                        "type": "string",
                        "description": "Repository in 'owner/name' form (e.g. 'acme/widgets').",
                    },
                    "path": {
                        "type": "string",
                        "description": "Path to the file relative to repo root.",
                    },
                    "ref": {
                        "type": "string",
                        "description": "Branch, tag, or commit SHA. Defaults to the repo's default branch.",
                    },
                },
                "required": ["repo", "path"],
            },
        },
        github_get_file,
    )

    registry.register(
        {
            "name": "github_list_files",
            "description": (
                "List the contents of a directory in a connected GitHub "
                "repository. Use this to discover what files exist before "
                "calling github_get_file. Pass an empty path for repo root."
            ),
            "input_schema": {
                "type": "object",
                "properties": {
                    "repo": {
                        "type": "string",
                        "description": "Repository in 'owner/name' form.",
                    },
                    "path": {
                        "type": "string",
                        "description": "Directory path relative to repo root. Empty string = repo root.",
                    },
                    "ref": {
                        "type": "string",
                        "description": "Branch, tag, or commit SHA. Defaults to the repo's default branch.",
                    },
                },
                "required": ["repo"],
            },
        },
        github_list_files,
    )

    registry.register(
        {
            "name": "github_search_code",
            "description": (
                "Search source code within a specific connected GitHub "
                "repository. Returns up to `limit` matching files with paths "
                "and URLs. Use this when you need to find code by keyword "
                "(function name, string literal, comment) before fetching it."
            ),
            "input_schema": {
                "type": "object",
                "properties": {
                    "query": {
                        "type": "string",
                        "description": "Keyword(s) to search for. Supports GitHub's code-search syntax (e.g. 'login filename:auth.py').",
                    },
                    "repo": {
                        "type": "string",
                        "description": "Repository to scope the search to ('owner/name'). Required by GitHub App auth.",
                    },
                    "limit": {
                        "type": "integer",
                        "description": "Max hits to return (1-30). Defaults to 10.",
                    },
                },
                "required": ["query", "repo"],
            },
        },
        github_search_code,
    )

    registry.register(
        {
            "name": "github_get_pr_diff",
            "description": (
                "Fetch the unified diff for a pull request — the actual "
                "code changes. Useful for reasoning about what a PR does."
            ),
            "input_schema": {
                "type": "object",
                "properties": {
                    "repo": {
                        "type": "string",
                        "description": "Repository in 'owner/name' form.",
                    },
                    "pr_number": {
                        "type": "integer",
                        "description": "The PR number (the integer in github.com/<repo>/pull/<N>).",
                    },
                },
                "required": ["repo", "pr_number"],
            },
        },
        github_get_pr_diff,
    )

    registry.register(
        {
            "name": "github_list_commits",
            "description": (
                "List recent commits on a branch of a connected GitHub "
                "repository. Returns sha, message, author, date, url."
            ),
            "input_schema": {
                "type": "object",
                "properties": {
                    "repo": {
                        "type": "string",
                        "description": "Repository in 'owner/name' form.",
                    },
                    "branch": {
                        "type": "string",
                        "description": "Branch name. Defaults to the repo's default branch.",
                    },
                    "since": {
                        "type": "string",
                        "description": "ISO 8601 timestamp; only return commits at or after this time.",
                    },
                    "limit": {
                        "type": "integer",
                        "description": "Max commits to return (1-100). Defaults to 20.",
                    },
                },
                "required": ["repo"],
            },
        },
        github_list_commits,
    )


_register_all()
