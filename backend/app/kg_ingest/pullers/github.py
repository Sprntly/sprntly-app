"""GitHub puller — distilled code-insight activity → RawRecords.

The weekly sync surfaces *activity signals* — what the team is shipping —
not source code. Per repo (capped at pilot scale) we pull recent PRs (titles
+ bodies + state) and recent commit messages, distill each into a compact
RawRecord, and let the generic extractor turn them into KG signals/themes.

REPO SCOPE — installation-selected only. The repos come from the tenant's
GitHub App installation(s): exactly the set granted during install, the same
list the Connectors → Configure panel shows. They deliberately do NOT come
from /user/repos: the user-OAuth token sees every repo its owner can touch
across ALL orgs (affiliation=owner,collaborator,organization_member), which
ingested private repos from users' employers and communities into tenant
graphs the moment they connected (2026-08-13 incident — a tenant that
selected two repos received sprntly-app + devcongress activity). No
installation → nothing selected → nothing pulled.

DATA-MINIMIZATION (§6): transient pulls of metadata only. PR/commit text is
human-authored prose about the work; we never persist file contents or bulk
code. The on-demand deep-read (separate module, NOT in this sync) handles the
heavier "read the repo" path with its own injection-defended LLM pass.

Auth: repo *listing* uses App installation tokens (github_app
.fetch_installation_repos); PR/commit *reads* still use the stored user-OAuth
`access_token` (token_for("github") → access_token). A granted repo the
connecting user can't read (403/404) is logged and skipped — one repo never
fails the sync.
"""
from __future__ import annotations

import logging
from typing import Iterator

import requests

from app.connectors import github_app
from app.db.github import list_github_installations
from app.kg_ingest.types import RawRecord

logger = logging.getLogger(__name__)

# Pilot-scale caps — keep the weekly sync cheap and the pull transient.
_MAX_REPOS = 10
_PRS_PER_REPO = 20
_COMMITS_PER_REPO = 30
_BODY_CHARS = 1500
_COMMIT_MSG_CHARS = 600


def _pr_records(token: str, repo: str) -> Iterator[RawRecord]:
    for pr in github_app.fetch_recent_pull_requests(token, repo, per_page=_PRS_PER_REPO):
        number = pr.get("number")
        yield RawRecord(
            provider="github",
            kind="pull_request",
            external_id=f"{repo}#pr-{number}",
            title=pr.get("title", ""),
            text=(pr.get("body") or "")[:_BODY_CHARS],
            properties={
                "repo": repo,
                "state": pr.get("state"),
                "author": pr.get("author"),
            },
            timestamp=pr.get("updated_at"),
        )


def _commit_records(token: str, repo: str) -> Iterator[RawRecord]:
    for c in github_app.fetch_recent_commits(token, repo, per_page=_COMMITS_PER_REPO):
        sha = c.get("sha") or ""
        msg = (c.get("message") or "").strip()
        if not msg:
            continue
        # Title is the first line; the rest of the message is the body.
        first, _, rest = msg.partition("\n")
        yield RawRecord(
            provider="github",
            kind="commit",
            external_id=f"{repo}@{sha}",
            title=first[:200],
            text=rest.strip()[:_COMMIT_MSG_CHARS],
            properties={"repo": repo, "author": c.get("author")},
            timestamp=c.get("date"),
        )


def _is_skippable(exc: requests.HTTPError) -> bool:
    """403/404 on a single repo → skip it; anything else is a real failure."""
    resp = getattr(exc, "response", None)
    return resp is not None and resp.status_code in (403, 404)


def _selected_repos(enterprise_id: str | None) -> list[str]:
    """full_names granted to the tenant's App installation(s), capped.

    The union across installations (a company may have installed the App on
    more than one org/user account), de-duplicated in order. Empty when the
    tenant has no installation — that is "nothing selected", not an error,
    and the sync must pull nothing rather than fall back to the user token's
    much wider visibility.
    """
    if not enterprise_id:
        # Defensive: a puller invoked without tenant context has no way to
        # know the selection, so it must not guess.
        logger.warning("github: no enterprise_id — repo selection unknown, pulling nothing")
        return []
    installs = list_github_installations(enterprise_id)
    if not installs:
        logger.info(
            "github: no App installation for %s — nothing selected, nothing pulled",
            enterprise_id,
        )
        return []
    repos: list[str] = []
    for inst in installs:
        for r in github_app.fetch_installation_repos(inst["installation_id"]):
            full = r.get("full_name")
            if full and full not in repos:
                repos.append(full)
    if not repos:
        # Granted-but-unlistable (revoked install, GitHub hiccup) degrades to
        # an empty sync this pass — honest nothing over the wrong repos.
        logger.warning(
            "github: installation(s) for %s returned no repos — pulling nothing",
            enterprise_id,
        )
    return repos[:_MAX_REPOS]


def pull(token: str, *, enterprise_id: str | None = None) -> Iterator[RawRecord]:
    """Yield distilled activity RawRecords for the tenant-selected repos.

    Scope comes from the App installation's grant list (see module docstring);
    a repo we can't read is logged and skipped so the sync stays alive."""
    for full in _selected_repos(enterprise_id):
        try:
            yield from _pr_records(token, full)
            yield from _commit_records(token, full)
        except requests.HTTPError as e:
            if _is_skippable(e):
                logger.info("github: skipping repo %s — not readable (%s)",
                            full, getattr(e.response, "status_code", "?"))
                continue
            raise
