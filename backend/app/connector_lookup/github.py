"""GitHub adapter — wiring, not new plumbing.

The five read tools already exist and are tested (app/agent_tools/github.py,
built for the Agents-module chat surface). This adapter makes them reachable from
the main chat, which is a TENANCY job more than a feature job:

    the installation id is resolved from the authenticated company, per repo,
    via db.find_github_installation_for_repo — and NEVER read from model input.

That rule is the whole point. `github_get_file(installation_id=…)` mints a GitHub
App installation token for whatever id it is handed, so accepting one from a
tool-call argument would be a lateral-access path from a sentence (the same hole
routes/agent_chat.py:100-111 closes for its surface with an ownership check
before the LLM is consulted). Here the model supplies only a repo name; the
installation is looked up under the caller's company, and a repo that resolves to
no owned installation is refused without an HTTP call.
"""
from __future__ import annotations

import json
import logging

from app.agent_tools import github as gh_tools
from app.connector_lookup.base import LookupSession, cap_text

logger = logging.getLogger(__name__)

DISPLAY_NAME = "GitHub"

#: A PR diff is the one result that can be enormous; capped before the framework
#: cap so the marker names the diff rather than the whole tool result.
_DIFF_CHARS = 8_000
_MAX_SEARCH_HITS = 15
_MAX_COMMITS = 30
_MAX_ENTRIES = 100
_FILE_CHARS = 8_000

SYSTEM = (
    "Tools (all read-only, scoped to the repositories this company installed the "
    "Sprntly GitHub App on):\n"
    "- github_list_commits: recent commits on a branch (sha, message, author, "
    "date). Use `since` (ISO 8601) for \"this week\".\n"
    "- github_search_code: find code by keyword inside one repo.\n"
    "- github_list_files / github_get_file: browse and read files.\n"
    "- github_get_pr_diff: the unified diff of one pull request.\n\n"
    "Every tool needs `repo` in 'owner/name' form. If you don't know it, say so "
    "and ask — do not guess a repo name; a wrong guess is refused, not silently "
    "answered from another repository.\n"
    "Honest limits you MUST respect: you can only read repos included in this "
    "company's installation, so 'not found' means 'not in the repos I can read'. "
    "Cite commit SHAs, file paths and PR numbers. A diff may be truncated — say "
    "so instead of implying you read all of it. You cannot push, open a PR, "
    "comment or merge."
)


def _tool(name: str, description: str, properties: dict, required: list[str]) -> dict:
    return {
        "name": name,
        "description": description,
        "input_schema": {
            "type": "object", "properties": properties, "required": required,
        },
    }


_REPO_PROP = {"type": "string", "description": "Repository in 'owner/name' form."}

TOOLS = [
    _tool(
        "github_list_commits",
        "List recent commits on a branch of a connected repository. Returns sha, "
        "message, author and date. `since` is an ISO 8601 timestamp.",
        {"repo": _REPO_PROP,
         "branch": {"type": "string", "description": "Branch name (default: the repo's default branch)."},
         "since": {"type": "string", "description": "ISO 8601 timestamp; only commits at or after this."},
         "limit": {"type": "integer", "description": "Max commits (1-30)."}},
        ["repo"],
    ),
    _tool(
        "github_search_code",
        "Search source code inside one connected repository. Returns matching "
        "file paths and URLs.",
        {"repo": _REPO_PROP,
         "query": {"type": "string", "description": "Keyword(s); GitHub code-search syntax allowed."},
         "limit": {"type": "integer", "description": "Max hits (1-15)."}},
        ["repo", "query"],
    ),
    _tool(
        "github_list_files",
        "List the contents of a directory in a connected repository. Empty path "
        "means the repo root.",
        {"repo": _REPO_PROP,
         "path": {"type": "string", "description": "Directory path relative to the repo root."},
         "ref": {"type": "string", "description": "Branch, tag or commit SHA."}},
        ["repo"],
    ),
    _tool(
        "github_get_file",
        "Read one file from a connected repository.",
        {"repo": _REPO_PROP,
         "path": {"type": "string", "description": "File path relative to the repo root."},
         "ref": {"type": "string", "description": "Branch, tag or commit SHA."}},
        ["repo", "path"],
    ),
    _tool(
        "github_get_pr_diff",
        "Fetch the unified diff of one pull request — the actual code changes.",
        {"repo": _REPO_PROP,
         "pr_number": {"type": "integer", "description": "The PR number."}},
        ["repo", "pr_number"],
    ),
]

NOT_OWNED = (
    "(that repository isn't covered by this company's GitHub installation, so I "
    "can't read it. Only repos the Sprntly GitHub App was installed on are "
    "readable — say that rather than guessing at the contents.)"
)


class GitHubProvider:
    """LookupProvider over the existing agent_tools/github.py functions.

    `open_session` only proves the company HAS an installation; the id used for
    each call is resolved per repo at dispatch time, so a company with several
    installations reads each repo through the right one and none through the
    wrong one.
    """

    provider = "github"
    display_name = DISPLAY_NAME
    keywords = ("github", "repo", "pull request")

    def open_session(self, enterprise_id: str) -> LookupSession | None:
        from app import db

        try:
            installs = db.list_github_installations(enterprise_id) or []
        except Exception:  # noqa: BLE001
            logger.warning(
                "github-lookup: installation lookup failed for %s", enterprise_id,
                exc_info=True,
            )
            return None
        usable = [i for i in installs if not i.get("suspended")]
        if not usable:
            return None
        logins = [i.get("account_login") for i in usable if i.get("account_login")]
        notes = []
        if logins:
            notes.append(
                "readable GitHub account(s): " + ", ".join(logins)
                + " — repos outside these installations cannot be read."
            )
        # The company id is the ONLY tenancy input; it is captured here and used
        # for every per-repo installation lookup below.
        return LookupSession(
            provider=self.provider, handle={"company_id": enterprise_id}, notes=notes
        )

    def tools(self) -> list[dict]:
        return TOOLS

    def system_block(self) -> str:
        return SYSTEM

    def _installation_for(self, company_id: str, repo: str) -> int | None:
        """Resolve the company's installation for `repo`, or None.

        Tenant-scoped by construction (db.find_github_installation_for_repo
        filters on company_id and excludes legacy NULL-company rows), and the
        ONLY way an installation id enters this module.
        """
        from app import db

        try:
            row = db.find_github_installation_for_repo(repo, company_id)
        except Exception:  # noqa: BLE001
            logger.warning("github-lookup: repo→installation lookup failed",
                           exc_info=True)
            return None
        if not row:
            return None
        try:
            return int(row["installation_id"])
        except (KeyError, TypeError, ValueError):
            return None

    def dispatch(self, session: LookupSession, name: str, inp: dict) -> str:
        company_id = session.handle["company_id"]
        repo = (inp.get("repo") or "").strip()
        if not repo or "/" not in repo:
            return f"({name}: 'repo' is required, in 'owner/name' form)"
        # Model input can name a repo; it can never name an installation.
        installation_id = self._installation_for(company_id, repo)
        if installation_id is None:
            return NOT_OWNED

        if name == "github_list_commits":
            out = gh_tools.github_list_commits(
                installation_id=installation_id, repo=repo,
                branch=inp.get("branch") or None, since=inp.get("since") or None,
                limit=_clamp(inp.get("limit"), _MAX_COMMITS),
            )
            return _render(name, out, keys=("commits",))
        if name == "github_search_code":
            query = (inp.get("query") or "").strip()
            if not query:
                return "(github_search_code: 'query' is required)"
            out = gh_tools.github_search_code(
                installation_id=installation_id, repo=repo, query=query,
                limit=_clamp(inp.get("limit"), _MAX_SEARCH_HITS),
            )
            return _render(name, out, keys=("hits", "total"))
        if name == "github_list_files":
            out = gh_tools.github_list_files(
                installation_id=installation_id, repo=repo,
                path=inp.get("path") or "", ref=inp.get("ref") or None,
            )
            if isinstance(out.get("entries"), list):
                out["entries"] = out["entries"][:_MAX_ENTRIES]
            return _render(name, out, keys=("entries",))
        if name == "github_get_file":
            path = (inp.get("path") or "").strip()
            if not path:
                return "(github_get_file: 'path' is required)"
            out = gh_tools.github_get_file(
                installation_id=installation_id, repo=repo, path=path,
                ref=inp.get("ref") or None,
            )
            if isinstance(out.get("content"), str):
                out["content"] = cap_text(out["content"], limit=_FILE_CHARS)
            return _render(name, out, keys=("content",))
        if name == "github_get_pr_diff":
            try:
                pr_number = int(inp.get("pr_number"))
            except (TypeError, ValueError):
                return "(github_get_pr_diff: 'pr_number' must be a number)"
            out = gh_tools.github_get_pr_diff(
                installation_id=installation_id, repo=repo, pr_number=pr_number
            )
            if isinstance(out.get("diff"), str):
                out["diff"] = cap_text(out["diff"], limit=_DIFF_CHARS)
            return _render(name, out, keys=("diff",))
        return f"(unknown tool {name})"


def _clamp(value, ceiling: int) -> int:
    try:
        return max(1, min(int(value), ceiling))
    except (TypeError, ValueError):
        return ceiling


def _render(name: str, out: dict, *, keys: tuple[str, ...]) -> str:
    """The agent tools return dicts; the loop wants text. An `error` key becomes
    a sentence the model can act on (never a stack trace), and everything else is
    serialised compactly."""
    if not isinstance(out, dict):
        return str(out)
    error = out.get("error")
    if error:
        if error == "not_found":
            return f"({name}: not found in the repos I can read — {out})"
        if str(error).startswith("http_4") or str(error).startswith("http_5"):
            return (
                f"({name} failed: GitHub returned {error}. If it is http_401 or "
                "http_403 the installation may need reconnecting; if http_429, "
                "say results may be incomplete.)"
            )
        return f"({name} failed: {error})"
    if not any(out.get(k) for k in keys):
        return f"({name}: no results — {json.dumps({k: out.get(k) for k in ('repo', 'path', 'branch') if k in out})})"
    return json.dumps(out, default=str)


PROVIDER = GitHubProvider()
