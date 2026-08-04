"""Read skills out of a connected GitHub repo.

Teams keep their methods where their code is — `skills/<id>/SKILL.md`, one
folder per skill, the layout Claude Code and the Agent Skills format use
(https://code.claude.com/docs/en/skills). Downloading that as a zip to
re-upload it is a chore that goes stale the moment someone edits a method, so
this module walks the repo directly and hands the same ParsedSkill shape the
upload path produces.

Detection is NOT reimplemented here. `skills.custom.skill_roots_for` /
`owning_skill_root` / `skill_file_role` / `derive_identity` are the same rules
the zip parser runs, so importing a repo and uploading a zip OF that repo
produce identical skills — a second implementation would drift the day one of
them was fixed.

Auth is the GitHub App INSTALLATION token (`github_app.headers_for_installation`),
never the member's stored OAuth token: that token's scopes are `read:user
user:email` and cannot read repository contents at all, while the App already
holds Contents: Read (backend/docs/CONNECTORS.md). No new OAuth app, no new
scope, no new dependency — requests + stdlib, the way every other GitHub
reader here works.

Everything is bounded, because this runs inside a request:

  - the ref is resolved to a COMMIT SHA once, and the tree and every file are
    read at that SHA, so a push mid-import cannot mix old and new content
  - the tree listing is capped and GitHub's own `truncated` flag is surfaced
    rather than swallowed (recursive trees cap at 100k entries / 7 MB —
    https://docs.github.com/en/rest/git/trees)
  - discovery caps the number of skills and the number of file reads per call
  - a blob over the Contents API's 1 MB base64 ceiling is skipped with a
    reason, never re-fetched through the raw media type
    (https://docs.github.com/en/rest/repos/contents)
  - symlinks (mode 120000, whose blob content is the link target) and
    submodules (type "commit") are rejected before anything is fetched, as are
    absolute, traversing, backslashed or empty-segment paths

Fetches fan out through a small ThreadPoolExecutor, the same shape and the
same reasoning as codebase_map/repo_reader.py: independent I/O-bound round
trips, a bounded number of concurrent installation rate-limit slots (the
installation budget is 5,000 requests/hour).
"""
from __future__ import annotations

import base64
import logging
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from urllib.parse import quote

from app.skills.custom import (
    MAX_SKILL_CONTENT_CHARS,
    ParsedSkill,
    SkillRoot,
    content_chars,
    derive_identity,
    owning_skill_root,
    skill_file_role,
    skill_roots_for,
    slugify,
)

logger = logging.getLogger(__name__)

# Per-call budgets. A repo of methods is small; these exist so a pathological
# monorepo can't turn one HTTP request into a thousand GitHub round trips.
MAX_TREE_ENTRIES = 20_000   # raw path listing (cheap — strings, never bytes)
MAX_SKILLS = 50             # discovered skill folders per call
MAX_FILE_FETCHES = 400      # file bodies per call, across every skill
#: Contents API returns base64 JSON only up to 1 MB; above that the content
#: field comes back empty and only the raw media type works. A skill method is
#: text, so an over-size blob is a mistake to report, not a fetch to retry.
MAX_FILE_BYTES = 1_048_576
_FETCH_CONCURRENCY = 8

#: Directories that are never a skill source, however they are laid out.
_IGNORED_DIRS = ("node_modules", ".git", "vendor")

_SYMLINK_MODE = "120000"


class GithubSourceError(RuntimeError):
    """GitHub transport/HTTP failure — the route answers 502."""


class GithubRefNotFound(GithubSourceError):
    """The repo or ref could not be resolved — the route answers 404.

    Split from the transport error deliberately: a typo'd branch is the user's
    to fix and a 502 would tell them the wrong thing about it."""


@dataclass(frozen=True)
class GithubSkill:
    """One skill found in a repo, already named for itself.

    `reason` non-empty means it CANNOT be imported (over the content cap, no
    usable name, an oversize file) — discovery still returns it so the picker
    can show why rather than silently listing fewer skills than the repo has.
    """

    path: str                  # repo-relative directory ("" = the walked root)
    name: str
    description: str
    parsed: ParsedSkill
    file_count: int
    char_count: int
    reason: str = ""

    @property
    def importable(self) -> bool:
        return not self.reason


@dataclass(frozen=True)
class DiscoveryResult:
    commit_sha: str
    branch: str
    skills: list[GithubSkill] = field(default_factory=list)
    #: True when the tree listing or a per-call budget clipped what we saw.
    truncated: bool = False
    #: Operator/user-readable notes about what was clipped or skipped.
    notes: list[str] = field(default_factory=list)


# ─── path safety ─────────────────────────────────────────────────────────────


def is_safe_path(path: str) -> bool:
    """Reject anything that isn't a plain repo-relative forward-slash path.

    A tree path comes from a repo we do not control, and it ends up in a
    Contents API URL and in a synthesized archive's member names. Absolute
    paths, `..` segments, backslashes (a Windows-style path that a later
    os.path.join would read as a separator) and empty segments are all refused
    outright rather than normalised — normalising an attacker-shaped path is
    how traversal bugs get written."""
    if not path or path.startswith("/") or "\\" in path:
        return False
    parts = path.split("/")
    return all(p and p != ".." and p != "." for p in parts)


def _is_ignored(path: str) -> bool:
    return any(seg in _IGNORED_DIRS for seg in path.split("/"))


def _usable_blob(entry: dict) -> bool:
    """A tree entry we may fetch: a real .md blob, not a symlink, not a
    submodule, not inside an ignored directory."""
    if entry.get("type") != "blob":
        return False  # "tree" is a directory, "commit" is a submodule pointer
    if str(entry.get("mode") or "") == _SYMLINK_MODE:
        # A symlink's blob content is the TARGET PATH, not the file — importing
        # it would store a path string as somebody's skill method.
        return False
    path = str(entry.get("path") or "")
    if not is_safe_path(path) or _is_ignored(path):
        return False
    return path.lower().endswith(".md")


# ─── the reader ──────────────────────────────────────────────────────────────


def _headers(installation_id: int) -> dict:
    from app.connectors import github_app

    return github_app.headers_for_installation(installation_id)


def resolve_commit(installation_id: int, repo: str, ref: str | None) -> tuple[str, str]:
    """(commit_sha, branch) for a repo + optional branch/tag/SHA.

    Mirrors RepoReader.resolve_commit_sha, with one difference that matters
    here: it RAISES instead of returning None, because an importer that cannot
    say which commit it read has nothing honest to show the user."""
    from app.connectors import github_app

    branch = (ref or "").strip()
    try:
        if not branch:
            token = github_app.get_installation_token(installation_id)
            meta = github_app.fetch_repo_meta(token, repo)
            branch = meta.get("default_branch") or "main"
        resp = github_app.requests.get(
            f"{github_app.GITHUB_API_BASE}/repos/{quote(repo, safe='/')}"
            f"/commits/{quote(branch, safe='')}",
            headers=_headers(installation_id),
            timeout=15,
        )
    except Exception as exc:  # noqa: BLE001 — transport failure
        logger.warning("skills.github resolve repo=%s error=%s", repo, type(exc).__name__)
        raise GithubSourceError("Couldn't reach GitHub. Please try again.") from exc
    if resp.status_code in (404, 422):
        raise GithubRefNotFound(
            f"We couldn't find “{branch}” in {repo}. Check the branch name and "
            "that the Sprntly GitHub App can see this repository."
        )
    if not resp.ok:
        logger.warning(
            "skills.github resolve repo=%s status=%s %s",
            repo, resp.status_code, resp.text[:200],
        )
        raise GithubSourceError(f"GitHub returned {resp.status_code} for {repo}.")
    sha = (resp.json() or {}).get("sha")
    if not isinstance(sha, str) or not sha:
        raise GithubSourceError(f"GitHub returned no commit for {repo}@{branch}.")
    return sha, branch


def fetch_skill_files(
    installation_id: int, repo: str, commit_sha: str, paths: list[str]
) -> dict[str, str]:
    """Decoded text for each path, read AT `commit_sha`.

    Missing files (404) and undecodable ones are silently absent from the
    result — the caller sees a skill with fewer files, which its own emptiness
    checks then catch. A non-404 HTTP error is a transport failure and raises,
    because importing a skill with half its content silently is worse than
    failing the request."""
    from app.connectors import github_app

    headers = _headers(installation_id)
    targets = [p for p in paths if is_safe_path(p)][:MAX_FILE_FETCHES]
    if not targets:
        return {}

    def _one(path: str) -> tuple[str, str | None]:
        resp = github_app.requests.get(
            f"{github_app.GITHUB_API_BASE}/repos/{quote(repo, safe='/')}"
            f"/contents/{quote(path, safe='/')}",
            headers=headers,
            # The SHA, never the branch: a push between the tree read and this
            # fetch must not swap the content underneath the import.
            params={"ref": commit_sha},
            timeout=15,
        )
        if resp.status_code == 404:
            return path, None
        if not resp.ok:
            raise GithubSourceError(
                f"GitHub returned {resp.status_code} while reading {repo}."
            )
        return path, _decode_contents(resp.json())

    try:
        with ThreadPoolExecutor(max_workers=min(_FETCH_CONCURRENCY, len(targets))) as pool:
            results = list(pool.map(_one, targets))
    except GithubSourceError:
        raise
    except Exception as exc:  # noqa: BLE001 — transport failure
        logger.warning("skills.github fetch repo=%s error=%s", repo, type(exc).__name__)
        raise GithubSourceError("Couldn't read files from GitHub. Please try again.") from exc
    return {path: text for path, text in results if text is not None}


def _decode_contents(payload) -> str | None:
    """Contents-API file payload → text. None for a directory listing, a
    non-base64 encoding, or an over-ceiling file (whose `content` comes back
    empty). Mirrors repo_reader._decode_file_payload."""
    if not payload or isinstance(payload, list):
        return None
    try:
        if int(payload.get("size") or 0) > MAX_FILE_BYTES:
            return None
    except (TypeError, ValueError):
        return None
    content = payload.get("content")
    if payload.get("encoding") != "base64" or not isinstance(content, str):
        return None
    try:
        return base64.b64decode(content).decode("utf-8", errors="ignore")
    except Exception:  # noqa: BLE001 — malformed base64 is a skip, not a 500
        return None


def discover_skills(
    installation_id: int, repo: str, ref: str | None = None, subpath: str = ""
) -> DiscoveryResult:
    """Every skill in `repo` at `ref`, with its content, ready to import.

    Content comes back with the listing rather than in a second pass, because
    the picker has to show what each skill IS (its own name and description,
    its size against the 50k cap) before anyone selects it — and because the
    import re-runs this and imports from ITS result, so a client can never
    hand us a path to fetch.
    """
    from app.connectors import github_app

    commit_sha, branch = resolve_commit(installation_id, repo, ref)
    prefix = "/".join(p for p in (subpath or "").strip("/").split("/") if p)
    if prefix and not is_safe_path(prefix):
        raise GithubRefNotFound(f"“{subpath}” isn't a valid folder path.")

    try:
        token = github_app.get_installation_token(installation_id)
        entries, truncated = github_app.fetch_repo_tree_entries(
            token, repo, commit_sha, max_entries=MAX_TREE_ENTRIES
        )
    except GithubSourceError:
        raise
    except Exception as exc:  # noqa: BLE001 — transport failure
        logger.warning("skills.github tree repo=%s error=%s", repo, type(exc).__name__)
        raise GithubSourceError("Couldn't read the repository from GitHub.") from exc

    notes: list[str] = []
    if truncated:
        notes.append(
            "This repository is too large to list in full, so some skills may "
            "be missing. Point the import at the folder your skills live in."
        )

    # Oversize .md blobs are reported per skill, not silently dropped: a skill
    # whose method is 2 MB is a thing the user has to know about.
    oversize: set[str] = set()
    usable: list[str] = []
    for entry in entries:
        path = str(entry.get("path") or "")
        if prefix and not (path == prefix or path.startswith(prefix + "/")):
            continue
        if not _usable_blob(entry):
            continue
        try:
            size = int(entry.get("size") or 0)
        except (TypeError, ValueError):
            size = 0
        if size > MAX_FILE_BYTES:
            oversize.add(path)
            continue
        usable.append(path)

    # Paths are walked relative to the requested folder, so a `path=skills`
    # import names skills after their own folders rather than after "skills".
    # Oversize files are in `rel` but never in `grouped`: their folder still
    # counts as a skill (so the picker can say why it can't be imported) while
    # the file itself is never fetched.
    rel = {
        p: p[len(prefix) + 1:] if prefix else p
        for p in [*usable, *sorted(oversize)]
    }
    roots = skill_roots_for(rel.values())
    if len(roots) > MAX_SKILLS:
        truncated = True
        notes.append(
            f"This repository has more than {MAX_SKILLS} skills — only the "
            f"first {MAX_SKILLS} are listed. Import a subfolder to reach the rest."
        )
        roots = roots[:MAX_SKILLS]

    grouped: dict[SkillRoot, list[str]] = {root: [] for root in roots}
    for path in usable:
        root = owning_skill_root(rel[path], roots)
        if root is not None:
            grouped[root].append(path)

    wanted = [p for paths in grouped.values() for p in paths]
    if len(wanted) > MAX_FILE_FETCHES:
        truncated = True
        notes.append("Too many files to read in one go — some skills are listed without their supporting files.")
    bodies = fetch_skill_files(installation_id, repo, commit_sha, wanted)

    skills: list[GithubSkill] = []
    for root in roots:
        skills.append(
            _skill_for_root(
                root=root,
                roots=roots,
                paths=grouped[root],
                rel=rel,
                bodies=bodies,
                oversize=oversize,
                repo=repo,
                prefix=prefix,
            )
        )
    return DiscoveryResult(
        commit_sha=commit_sha, branch=branch, skills=skills,
        truncated=truncated, notes=notes,
    )


def _skill_for_root(
    *,
    root: SkillRoot,
    roots: list[SkillRoot],
    paths: list[str],
    rel: dict[str, str],
    bodies: dict[str, str],
    oversize: set[str],
    repo: str,
    prefix: str,
) -> GithubSkill:
    """Assemble one skill from its files, with the same layout rules and the
    same naming fallbacks the zip parser uses."""
    method = ""
    modules: dict[str, str] = {}
    references: dict[str, str] = {}
    for path in sorted(paths, key=str.lower):
        text = bodies.get(path)
        if text is None:
            continue
        base = path.rsplit("/", 1)[-1]
        role = skill_file_role(rel[path], root)
        if role == "method" and not method:
            method = text
            continue
        (references if role == "references" else modules).setdefault(base, text)

    display_path = "/".join(root)
    # A skill at the walked root has no folder of its own, so it borrows the
    # folder the import was pointed at, else the repo's own name.
    label = root[-1] if root else (
        prefix.rsplit("/", 1)[-1] if prefix else repo.rsplit("/", 1)[-1]
    )
    name, description = derive_identity(method, label)
    parsed = ParsedSkill(method=method, modules=modules, references=references)
    chars = content_chars(parsed)
    # An oversize file is attributed to the skill that OWNS it — the deepest
    # root above it — so a 2 MB file in a nested skill never invalidates the
    # ancestor that merely contains that folder.
    skipped_files = sorted(
        p for p in oversize if owning_skill_root(rel.get(p, p), roots) == root
    )

    reason = ""
    if skipped_files:
        reason = (
            f"{skipped_files[0].rsplit('/', 1)[-1]} is larger than 1 MB, which "
            "GitHub won't serve as text."
        )
    elif not method.strip():
        reason = "its SKILL.md is empty"
    elif not name or not slugify(name):
        reason = "we couldn't work out a name for it — add `name:` to its SKILL.md"
    elif not description:
        reason = (
            "we couldn't work out a description for it — add `description:` to "
            "its SKILL.md"
        )
    elif chars > MAX_SKILL_CONTENT_CHARS:
        reason = (
            f"it is over the {MAX_SKILL_CONTENT_CHARS:,} character limit "
            f"({chars:,} characters)"
        )
    return GithubSkill(
        path=display_path,
        name=name,
        description=description,
        parsed=parsed,
        file_count=len([p for p in paths if p in bodies]),
        char_count=chars,
        reason=reason,
    )
