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
import json
import logging
import re
from dataclasses import dataclass, field
from urllib.parse import quote

logger = logging.getLogger(__name__)

# ── per-read budget constants (scoped to this module) ──────────────────────────
# Intentionally separate from the styling-fetch _GITHUB_* constants in adapters.py:
# the map reader needs more tree entries and more file bodies than the 12-file
# UI-primitive fetch, but remains bounded well below a full-repo download.
_MAX_TREE_ENTRIES = 600    # per-build blob-path budget — route-tree + component discovery
# Raw recursive listing ceiling. We list the WHOLE repo tree up to this many
# paths (cheap — path strings, never bytes) so read_repo can filter to the
# detected frontend subtree BEFORE applying the _MAX_TREE_ENTRIES budget. A
# flat-from-root budget would clip a non-root frontend (e.g. web/) that sorts
# after a larger backend tree, leaving the map empty. Well above any realistic
# single-app file count, still bounded against a pathological repo.
_MAX_TREE_SCAN = 5_000
_MAX_FILES = 40            # total file bodies per map build (screen + deps + shell + config)
_MAX_FILE_BYTES = 128_000  # per-file byte cap for tree-sampled files; larger files are skipped, not truncated
# Caller-supplied extras (the explicit must-reads read_repo prepends) get a
# larger ceiling than tree-sampled files: a monorepo's real globals.css / theme
# bridge is often 200-400KB and would be silently dropped under the tree cap,
# killing the theme bridge. Still bounded — never an unlimited fetch.
_MAX_EXTRA_FILE_BYTES = 1_048_576  # 1 MiB ceiling for explicit extras


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


# ── frontend-root detection (multi-root monorepo support) ──────────────────────
# In a monorepo the frontend app lives under a package dir (e.g. web/) while the
# backend sorts ahead of it in the recursive blob listing. Detecting that subtree
# lets read_repo measure its per-build budget against frontend files instead of a
# backend-first prefix.

# A Next.js / App-Router page file under an optional package prefix and optional
# src/ dir: captures the prefix in front of the conventional app/ root.
_APP_ROUTER_PAGE_RE = re.compile(r"^(.*?)(?:src/)?app/.*page\.[jt]sx?$")


def _candidate_package_json_paths(tree_paths: list[str]) -> list[str]:
    """package.json paths at the repo root or one/two levels deep.

    Bounded by depth so detection reads only a handful of small manifests rather
    than every nested package.json in a large monorepo.
    """
    out: list[str] = []
    for p in tree_paths:
        if p == "package.json" or p.endswith("/package.json"):
            if p.count("/") <= 2:
                out.append(p)
    return out


def _pkg_deps(body: str) -> dict:
    """Merged dependency map from a package.json body ({} on any parse error)."""
    try:
        data = json.loads(body)
    except Exception:
        return {}
    if not isinstance(data, dict):
        return {}
    merged: dict = {}
    for key in ("dependencies", "devDependencies", "peerDependencies"):
        deps = data.get(key)
        if isinstance(deps, dict):
            merged.update(deps)
    return merged


def _pkg_declares_frontend(body: str) -> bool:
    """True when a package.json body declares a next or react dependency."""
    deps = _pkg_deps(body)
    return "next" in deps or "react" in deps


def _app_router_prefixes(tree_paths: list[str]) -> set[str]:
    """All repo-relative prefixes hosting an app/**/page.* (or src/app/**) file."""
    return {
        m.group(1)
        for m in (_APP_ROUTER_PAGE_RE.match(p) for p in tree_paths)
        if m is not None
    }


def _app_router_prefix(tree_paths: list[str]) -> str:
    """Shallowest repo-relative prefix hosting an app/**/page.* (or src/app/**)."""
    prefixes = _app_router_prefixes(tree_paths)
    return min(prefixes, key=lambda s: (s.count("/"), s)) if prefixes else ""


def _detect_frontend_root(tree_paths: list[str], files: dict[str, str]) -> str:
    """Return the repo-relative prefix of the frontend app subtree ('web/'), or
    '' for a repo-root app.

    Detection order: (a) a package.json declaring next/react — among candidates,
    PREFER one that hosts an App-Router page (real screens) or declares `next`
    over a bare-react-only sibling (e.g. a generated prototype-runtime package
    that declares react but ships no app/**/page.*), then the shallowest, then
    alphabetical as a stable last resort; a root-only frontend manifest means a
    repo-root app and returns ''. (b) Else the prefix of the dir hosting
    app/**/page.* or src/app/**/page.*. (c) Else '' (honest fallback; never
    raises). When '', callers apply today's flat behaviour with no filtering.
    """
    app_router = _app_router_prefixes(tree_paths)
    # (prefix, has_app_router, declares_next) for each non-root frontend package.
    candidates: list[tuple[str, bool, bool]] = []
    root_is_frontend = False
    for p in tree_paths:
        if p != "package.json" and not p.endswith("/package.json"):
            continue
        deps = _pkg_deps(files.get(p, ""))
        if not ("next" in deps or "react" in deps):
            continue
        prefix = p[: len(p) - len("package.json")]  # '' or 'web/' or 'apps/web/'
        if prefix:
            candidates.append((prefix, prefix in app_router, "next" in deps))
        else:
            root_is_frontend = True
    if candidates:
        # A real frontend app hosts App-Router pages and/or declares next; a
        # bare-react sibling (generated output) wins only if nothing better
        # exists. Ties fall to the shallowest, then alphabetical (stable).
        candidates.sort(
            key=lambda c: (
                0 if c[1] else 1,        # hosts app/**/page.* first
                0 if c[2] else 1,        # declares next next
                c[0].count("/"),         # shallower
                c[0],                    # alphabetical, stable
            )
        )
        return candidates[0][0]
    if root_is_frontend:
        return ""
    return _app_router_prefix(tree_paths)


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
        """Return (paths, truncated) from the recursive blob-path listing.

        Lists the WHOLE repo tree up to _MAX_TREE_SCAN paths so read_repo can
        filter to the detected frontend subtree BEFORE applying the per-build
        _MAX_TREE_ENTRIES budget. truncated is True when the raw listing hit the
        scan ceiling exactly.
        """
        from app.connectors import github_app

        token = github_app.get_installation_token(self.installation_id)
        paths = github_app.fetch_repo_tree(token, repo, branch, max_entries=_MAX_TREE_SCAN)
        truncated = len(paths) == _MAX_TREE_SCAN
        return paths, truncated

    def fetch_files(
        self,
        repo: str,
        branch: str,
        paths: list[str],
        always_fetch: int = 0,
    ) -> tuple[dict[str, str], bool]:
        """Fetch up to max(_MAX_FILES, always_fetch) file bodies.

        Returns (bodies_dict, had_error).  had_error is True if any path was
        skipped due to a non-404 HTTP error or a transport exception, signalling
        that the snapshot is partial.  Oversize or non-base64 skips are silent
        and do not set had_error.

        always_fetch raises the slice budget so the first `always_fetch` paths
        (the caller's explicit extras, which read_repo prepends) are always
        attempted even when they alone exceed _MAX_FILES, and earn the larger
        _MAX_EXTRA_FILE_BYTES ceiling. The default of 0 preserves the original
        _MAX_FILES-bounded, 128 KB-capped behaviour for callers with no extras.
        """
        headers = self._headers()
        bodies: dict[str, str] = {}
        had_error = False

        budget = max(_MAX_FILES, always_fetch)
        for idx, path in enumerate(paths[:budget]):
            # The first `always_fetch` paths are the caller's explicit extras
            # (read_repo prepends them), so they earn the larger byte ceiling.
            # Tree-sampled paths keep the tighter _MAX_FILE_BYTES cap.
            max_bytes = _MAX_EXTRA_FILE_BYTES if idx < always_fetch else _MAX_FILE_BYTES
            try:
                payload = _get_contents_raw(headers, repo, path, branch)
                text = _decode_file_payload(payload, max_bytes=max_bytes)
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
    frontend_root: str = "",
) -> RepoSnapshot | None:
    """Resolve SHA → list tree → fetch bounded file set → return a RepoSnapshot.

    Returns None when installation_id is falsy, repo has no '/', SHA resolution
    fails, or the tree is empty.  All transport errors are absorbed; callers
    degrade gracefully to "no codebase map" via a None return.

    extra_paths lets the caller prepend a screen's dependency closure; the
    extras are fetched FIRST (within a raised budget) so they always land even
    when they alone exceed _MAX_FILES.

    frontend_root, when known to the caller (the recreate path knows the located
    file's prefix), anchors the tree budget directly; otherwise the frontend
    subtree is auto-detected so the per-build budget measures frontend files,
    not a backend-first listing. Empty string ('') means today's flat behaviour.
    """
    if not installation_id or not repo or "/" not in repo:
        return None

    reader = RepoReader(installation_id)

    sha, branch = reader.resolve_commit_sha(repo, ref)
    if not sha or not branch:
        return None

    raw_tree, raw_truncated = reader.list_tree(repo, branch)
    if not raw_tree:
        return None

    # Anchor the per-build tree budget to the frontend subtree. Callers that
    # already know the prefix pass it; the map-build path auto-detects, reading
    # only the handful of candidate package.json bodies detection needs.
    root = frontend_root
    if not root:
        pkg_paths = _candidate_package_json_paths(raw_tree)
        pkg_files: dict[str, str] = {}
        if pkg_paths:
            pkg_files, _ = reader.fetch_files(repo, branch, pkg_paths)
        root = _detect_frontend_root(raw_tree, pkg_files)

    filtered = [p for p in raw_tree if p.startswith(root)] if root else raw_tree
    tree_paths = filtered[:_MAX_TREE_ENTRIES]
    tree_truncated = raw_truncated or len(tree_paths) == _MAX_TREE_ENTRIES

    # Merge caller-supplied extras FIRST (deduped, order-preserving) so the
    # screen's explicit dependency closure is always within the fetch budget,
    # then append the tree sample. Prepending + the always_fetch budget below
    # guarantees every explicit extra is attempted even when the extras alone
    # exceed _MAX_FILES (true for monorepos with deep dependency closures).
    fetch_list: list[str] = []
    seen: set[str] = set()
    n_extras = 0
    for p in (extra_paths or []):
        if p not in seen:
            fetch_list.append(p)
            seen.add(p)
            n_extras += 1
    for p in tree_paths:
        if p not in seen:
            fetch_list.append(p)
            seen.add(p)

    truncated = tree_truncated or (len(fetch_list) > max(_MAX_FILES, n_extras))

    bodies, had_fetch_error = reader.fetch_files(
        repo, branch, fetch_list, always_fetch=n_extras
    )
    if had_fetch_error:
        truncated = True

    logger.info(
        "codebase_map.repo_read repo=%s sha=%s frontend_root=%s n_tree=%d n_files=%d truncated=%s",
        repo, sha, root or "", len(tree_paths), len(bodies), truncated,
    )

    return RepoSnapshot(
        repo=repo,
        commit_sha=sha,
        branch=branch,
        tree_paths=tree_paths,
        files=bodies,
        truncated=truncated,
    )
