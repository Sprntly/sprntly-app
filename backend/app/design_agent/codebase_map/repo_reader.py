"""Bounded live-repo reader for the codebase map pipeline.

Fetches a repo's commit SHA, recursive blob-path listing, and a bounded set of
file bodies via the GitHub App installation API.  Returns a RepoSnapshot for use
by the downstream node/edge/shell extractors.

Design notes
------------
- Zero modifications to any pre-existing connector or adapter file: all GitHub
  App plumbing is consumed via the public surface of app.connectors.github_app.
- Per-file fetch bodies borrow the logic of GithubExtractor._github_get_contents
  and _fetch_text_file mirrored inline, since those are bound instance methods
  that cannot be imported without touching the original class.
- Sequential file fetching is deliberate: the live GitHub App rate-limit
  behaviour on customer repos is untested at scale, so a simple loop matches
  the existing adapter pattern and avoids compounding concurrent API pressure.
  A bounded thread-pool would reduce wall-clock time at the cost of more
  simultaneous rate-limit slots — that trade-off belongs in a later ticket once
  actual rate-limit headroom is measured on real customer repos.
"""
from __future__ import annotations

import base64
import logging
from dataclasses import dataclass, field
from urllib.parse import quote

logger = logging.getLogger(__name__)

# ── per-read budget constants (scoped to this module) ──────────────────────────
# Intentionally separate from the styling-fetch _GITHUB_* constants in adapters.py:
# the map reader needs more tree entries and more file bodies than the 12-file
# UI-primitive fetch, but remains bounded well below a full-repo download.
_MAX_TREE_ENTRIES = 600    # recursive blob paths — route-tree + component discovery
_MAX_FILES = 40            # total file bodies per map build (screen + deps + shell + config)
_MAX_FILE_BYTES = 128_000  # per-file byte cap; files larger than this are skipped, not truncated


# ── data model ──────────────────────────────────────────────────────────────────

@dataclass
class RepoSnapshot:
    """Bounded point-in-time view of a repo, keyed to a concrete commit SHA."""

    repo: str               # "org/repo"
    commit_sha: str         # resolved HEAD-of-ref SHA the map is built from
    branch: str             # branch the SHA came from
    tree_paths: list[str]   # capped recursive blob-path listing
    files: dict[str, str] = field(default_factory=dict)  # {repo-relative path: decoded text}
    truncated: bool = False  # True when any cap or transport skip clipped the result


# ── low-level fetch helpers (logic mirrored from GithubExtractor) ───────────────

def _get_contents_raw(headers: dict, repo_full_name: str, path: str, branch: str | None):
    """Fetch the GitHub contents API for a single path.

    Returns the parsed JSON payload on success.
    Returns None when the path does not exist (404).
    Raises on non-OK non-404 HTTP responses (rate-limit, server error) so the
    caller can detect partial-fetch and set truncated=True.
    Propagates transport-level exceptions for the same reason.
    """
    from app.connectors import github_app

    params = {"ref": branch} if branch else None
    quoted_path = quote(path, safe="/")
    quoted_repo = quote(repo_full_name, safe="/")
    resp = github_app.requests.get(
        f"{github_app.GITHUB_API_BASE}/repos/{quoted_repo}/contents/{quoted_path}",
        headers=headers,
        params=params,
        timeout=15,
    )
    if resp.status_code == 404:
        return None  # file simply does not exist — silent skip
    if not resp.ok:
        raise OSError(f"GitHub contents API returned {resp.status_code} for {path}")
    return resp.json()


def _decode_file_payload(payload, max_bytes: int = _MAX_FILE_BYTES) -> str | None:
    """Decode a GitHub contents-API file payload to a UTF-8 string.

    Returns None when: the payload is a directory listing, the file exceeds
    max_bytes, the encoding is not base64, or the content field is absent.
    Mirrors the logic of GithubExtractor._fetch_text_file.
    """
    if not payload or isinstance(payload, list):
        return None
    try:
        size = int(payload.get("size") or 0)
    except (TypeError, ValueError):
        return None
    if size > max_bytes:
        return None
    content = payload.get("content")
    if payload.get("encoding") != "base64" or not isinstance(content, str):
        return None
    try:
        return base64.b64decode(content).decode("utf-8", errors="ignore")
    except Exception:
        return None


# ── main reader class ────────────────────────────────────────────────────────────

class RepoReader:
    """Reads a connected customer repo via the GitHub App installation API."""

    def __init__(self, installation_id: int) -> None:
        self.installation_id = installation_id

    def _headers(self) -> dict:
        from app.connectors import github_app
        return github_app.headers_for_installation(self.installation_id)

    def resolve_commit_sha(self, repo: str, ref: str | None) -> tuple[str | None, str | None]:
        """Return (commit_sha, branch).

        ref may be 'owner/repo@branch' style, a bare branch name, or None.
        When branch is absent the repo's default_branch is used.
        Returns (None, None) on any failure without raising.
        """
        from app.connectors import github_app

        # Parse the optional branch out of ref — mirrors _repo_ref_parts in adapters.py.
        cleaned = (ref or "").strip()
        if "@" in cleaned:
            _, branch_part = cleaned.split("@", 1)
            branch: str | None = branch_part.strip() or None
        elif cleaned:
            branch = cleaned  # bare branch name passed directly
        else:
            branch = None

        try:
            if not branch:
                token = github_app.get_installation_token(self.installation_id)
                meta = github_app.fetch_repo_meta(token, repo)
                branch = meta.get("default_branch") or "main"

            quoted_repo = quote(repo, safe="/")
            quoted_branch = quote(branch, safe="")
            resp = github_app.requests.get(
                f"{github_app.GITHUB_API_BASE}/repos/{quoted_repo}/commits/{quoted_branch}",
                headers=self._headers(),
                timeout=15,
            )
            if not resp.ok:
                logger.warning(
                    "codebase_map.sha_resolve repo=%s stage=sha_resolve status=%s",
                    repo, resp.status_code,
                )
                return None, None
            sha = (resp.json() or {}).get("sha")
            if not isinstance(sha, str) or not sha:
                return None, None
            return sha, branch
        except Exception as exc:
            logger.warning(
                "codebase_map.sha_resolve repo=%s stage=sha_resolve error=%s",
                repo, type(exc).__name__,
            )
            return None, None

    def list_tree(self, repo: str, branch: str) -> tuple[list[str], bool]:
        """Return (paths, truncated) from a bounded recursive blob-path listing.

        Delegates to fetch_repo_tree with an explicit max_entries cap.
        truncated is True when the returned list hit the cap exactly.
        """
        from app.connectors import github_app

        token = github_app.get_installation_token(self.installation_id)
        paths = github_app.fetch_repo_tree(token, repo, branch, max_entries=_MAX_TREE_ENTRIES)
        truncated = len(paths) == _MAX_TREE_ENTRIES
        return paths, truncated

    def fetch_files(self, repo: str, branch: str, paths: list[str]) -> tuple[dict[str, str], bool]:
        """Fetch up to _MAX_FILES file bodies.

        Returns (bodies_dict, had_error).  had_error is True if any path was
        skipped due to a non-404 HTTP error or a transport exception, signalling
        that the snapshot is partial.  Oversize or non-base64 skips are silent
        and do not set had_error.  The call count never exceeds _MAX_FILES.
        """
        headers = self._headers()
        bodies: dict[str, str] = {}
        had_error = False

        for path in paths[:_MAX_FILES]:
            try:
                payload = _get_contents_raw(headers, repo, path, branch)
                text = _decode_file_payload(payload)
                if text is not None:
                    bodies[path] = text
            except Exception:
                had_error = True

        return bodies, had_error


# ── top-level entry ──────────────────────────────────────────────────────────────

def read_repo(
    installation_id: int,
    repo: str,
    ref: str | None,
    extra_paths: list[str] | None = None,
) -> RepoSnapshot | None:
    """Resolve SHA → list tree → fetch bounded file set → return a RepoSnapshot.

    Returns None when installation_id is falsy, repo has no '/', SHA resolution
    fails, or the tree is empty.  All transport errors are absorbed; callers
    degrade gracefully to "no codebase map" via a None return.

    extra_paths lets the caller append a screen's dependency closure on a second
    pass without re-listing the full tree (deduplication is applied).
    """
    if not installation_id or not repo or "/" not in repo:
        return None

    reader = RepoReader(installation_id)

    sha, branch = reader.resolve_commit_sha(repo, ref)
    if not sha or not branch:
        return None

    tree_paths, tree_truncated = reader.list_tree(repo, branch)
    if not tree_paths:
        return None

    # Merge any caller-supplied extra paths (deduplication preserves order).
    fetch_list: list[str] = list(tree_paths)
    if extra_paths:
        seen = set(fetch_list)
        for p in extra_paths:
            if p not in seen:
                fetch_list.append(p)
                seen.add(p)

    truncated = tree_truncated or (len(fetch_list) > _MAX_FILES)

    bodies, had_fetch_error = reader.fetch_files(repo, branch, fetch_list)
    if had_fetch_error:
        truncated = True

    logger.info(
        "codebase_map.repo_read repo=%s sha=%s n_tree=%d n_files=%d truncated=%s",
        repo, sha, len(tree_paths), len(bodies), truncated,
    )

    return RepoSnapshot(
        repo=repo,
        commit_sha=sha,
        branch=branch,
        tree_paths=tree_paths,
        files=bodies,
        truncated=truncated,
    )
