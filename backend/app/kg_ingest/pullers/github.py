"""GitHub puller — distilled code-insight activity → RawRecords.

The weekly sync surfaces *activity signals* — what the team is shipping —
not source code. Per repo (capped at pilot scale) we pull recent PRs (titles
+ bodies + state) and recent commit messages, distill each into a compact
RawRecord, and let the generic extractor turn them into KG signals/themes.

DATA-MINIMIZATION (§6): transient pulls of metadata only. PR/commit text is
human-authored prose about the work; we never persist file contents or bulk
code. The on-demand deep-read (separate module, NOT in this sync) handles the
heavier "read the repo" path with its own injection-defended LLM pass.

Auth: the stored user-OAuth `access_token` (token_for("github") → access_token)
scopes repo listing + PR/commit reads to whatever the user can see. A repo we
can't read (403/404) is logged and skipped — one repo never fails the sync.
"""
from __future__ import annotations

import logging
from typing import Iterator

import requests

from app.connectors import github_app
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


def pull(token: str) -> Iterator[RawRecord]:
    """Yield distilled activity RawRecords across the user's recent repos.

    Repos are taken from the most-recently-updated slice (pilot cap). A repo
    we can't read is logged and skipped so the sync stays alive."""
    repos = github_app.fetch_user_repos(token, per_page=_MAX_REPOS)
    for r in repos[:_MAX_REPOS]:
        full = r.get("full_name")
        if not full:
            continue
        try:
            yield from _pr_records(token, full)
            yield from _commit_records(token, full)
        except requests.HTTPError as e:
            if _is_skippable(e):
                logger.info("github: skipping repo %s — not readable (%s)",
                            full, getattr(e.response, "status_code", "?"))
                continue
            raise
