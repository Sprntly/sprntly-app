"""Reading skills out of a GitHub repo — the discovery rules (app/skills/github_source.py).

Discovery walks a repo's recursive tree and decides, BEFORE fetching anything,
which blobs may be read at all. That filter is the security boundary of the
feature: a tree comes from a repo Sprntly does not control, and every path in
it ends up in a Contents API URL and in a synthesized archive's member names.

Covered here, all against synthetic tree entries (no network):
- one skill per directory holding a SKILL.md; a nested skill is its own and is
  excluded from its ancestor; `references/` is per skill, not per repo
- symlinks (mode 120000 — the blob is the link target) and submodules
  (type "commit") are never fetched
- absolute, traversing, backslashed and empty-segment paths are refused
- node_modules/ .git/ vendor/ are ignored wherever they sit
- a blob over the Contents API's 1 MB base64 ceiling is reported on its skill
  instead of being fetched or silently dropped
- GitHub's `truncated` flag is surfaced, and the per-call skill cap sets it
- skills are named from their own SKILL.md frontmatter, folder name as fallback
"""
from __future__ import annotations

from unittest.mock import patch

import pytest

from app.skills import github_source
from app.skills.github_source import is_safe_path


def _blob(path: str, *, size: int = 100, mode: str = "100644", type_: str = "blob") -> dict:
    return {"path": path, "type": type_, "mode": mode, "size": size, "sha": "deadbeef"}


def _skill_md(name: str, description: str, body: str = "Do the thing.") -> str:
    return f"---\nname: {name}\ndescription: {description}\n---\n\n{body}\n"


def _discover(entries, bodies, *, truncated=False, subpath="", repo="octocat/methods"):
    """Run discovery with the GitHub layer stubbed at its two seams."""
    with patch.object(
        github_source, "resolve_commit", return_value=("abc123", "main")
    ), patch(
        "app.connectors.github_app.get_installation_token", return_value="ghs_x"
    ), patch(
        "app.connectors.github_app.fetch_repo_tree_entries",
        return_value=(entries, truncated),
    ), patch.object(
        github_source, "fetch_skill_files",
        side_effect=lambda _i, _r, _sha, paths: {p: bodies[p] for p in paths if p in bodies},
    ):
        return github_source.discover_skills(42, repo, "main", subpath=subpath)


# ─── path safety ─────────────────────────────────────────────────────────────


@pytest.mark.parametrize("path", [
    "/etc/passwd",            # absolute
    "../../secrets/SKILL.md",  # traversal
    "skills/..\\..\\SKILL.md",  # backslash — a Windows separator in disguise
    "skills//SKILL.md",        # empty segment
    "",
])
def test_hostile_paths_are_refused(path):
    assert is_safe_path(path) is False


def test_ordinary_repo_paths_are_accepted():
    assert is_safe_path("skills/sprint-planner/SKILL.md") is True
    assert is_safe_path("SKILL.md") is True


# ─── skill detection ─────────────────────────────────────────────────────────


def test_every_md_is_a_skill_and_folders_bundle_their_support_files():
    # The listing rule the picker sells: every .md the user can see in the
    # repo is a row they can tick. A SKILL.md folder keeps ONLY its
    # references/ and modules/ bundled; every other .md — a loose sibling, a
    # README — stands on its own and the user simply leaves it unticked.
    entries = [
        _blob("skills/sprint-planner/SKILL.md"),
        _blob("skills/sprint-planner/references/source.md"),
        _blob("skills/sprint-planner/deep-dive.md"),
        _blob("skills/pricing-review/SKILL.md"),
        _blob("README.md"),
    ]
    bodies = {
        "skills/sprint-planner/SKILL.md": _skill_md("sprint-planner", "Plans a sprint."),
        "skills/sprint-planner/references/source.md": "cited",
        "skills/sprint-planner/deep-dive.md": "module text",
        "skills/pricing-review/SKILL.md": _skill_md("pricing-review", "Reviews pricing."),
        "README.md": "# repo",
    }
    result = _discover(entries, bodies)
    by_path = {s.path: s for s in result.skills}
    assert sorted(by_path) == [
        "README.md",
        "skills/pricing-review",
        "skills/sprint-planner",
        "skills/sprint-planner/deep-dive.md",
    ]

    planner = by_path["skills/sprint-planner"]
    assert planner.name == "Sprint Planner"
    assert planner.description == "Plans a sprint."
    # references/ is resolved RELATIVE to the skill, which is the bug the zip
    # parser had when it only looked at the archive root.
    assert planner.parsed.references == {"source.md": "cited"}
    assert planner.parsed.modules == {}
    assert planner.importable and planner.file_count == 2

    # The loose sibling is a whole skill of its own, named from its filename
    # when it carries no frontmatter.
    dive = by_path["skills/sprint-planner/deep-dive.md"]
    assert dive.name == "Deep Dive"
    assert dive.parsed.method == "module text"
    assert dive.parsed.modules == {} and dive.parsed.references == {}

    # A README with no prose body can't derive a description — it is listed
    # (nothing is hidden) but marked unimportable with the reason.
    readme = by_path["README.md"]
    assert readme.name == "README"
    assert not readme.importable and "description" in readme.reason


def test_a_nested_skill_is_its_own_and_leaves_its_ancestor():
    entries = [
        _blob("sales/SKILL.md"),
        _blob("sales/pitch.md"),
        _blob("sales/discovery/SKILL.md"),
        _blob("sales/discovery/questions.md"),
    ]
    bodies = {
        "sales/SKILL.md": _skill_md("sales", "Sells."),
        "sales/pitch.md": "pitch",
        "sales/discovery/SKILL.md": _skill_md("discovery", "Discovers."),
        "sales/discovery/questions.md": "questions",
    }
    by_path = {s.path: s for s in _discover(entries, bodies).skills}
    assert sorted(by_path) == [
        "sales", "sales/discovery", "sales/discovery/questions.md", "sales/pitch.md",
    ]
    assert by_path["sales"].parsed.modules == {}
    assert by_path["sales/discovery"].parsed.modules == {}
    assert by_path["sales/pitch.md"].parsed.method == "pitch"
    assert by_path["sales/discovery/questions.md"].parsed.method == "questions"


def test_subpath_scopes_the_walk_and_the_naming():
    entries = [
        _blob("docs/notes.md"),
        _blob("skills/raci/SKILL.md"),
        _blob("skills/raci/extra.md"),
    ]
    bodies = {
        "docs/notes.md": "not a skill",
        "skills/raci/SKILL.md": "# RACI\n\nBuilds a RACI grid.\n",
        "skills/raci/extra.md": "extra",
    }
    result = _discover(entries, bodies, subpath="skills")
    by_path = {s.path: s for s in result.skills}
    # docs/notes.md is outside the walked folder — never listed. extra.md is
    # inside it, so it stands as its own skill like any other .md.
    assert sorted(by_path) == ["raci", "raci/extra.md"]
    # Named for its own folder, not for the folder the import was pointed at.
    assert by_path["raci"].name == "Raci"
    assert by_path["raci"].description == "Builds a RACI grid."


def test_a_root_skill_is_named_from_the_repo_or_the_folder():
    entries = [_blob("SKILL.md"), _blob("nested/SKILL.md")]
    bodies = {
        "SKILL.md": "# Root\n\nThe root method.\n",
        "nested/SKILL.md": "# Nested\n\nThe nested method.\n",
    }
    result = _discover(entries, bodies, repo="octocat/team-methods")
    names = {s.path: s.name for s in result.skills}
    assert names == {"": "Team Methods", "nested": "Nested"}


# ─── what is never fetched ───────────────────────────────────────────────────


def test_symlinks_submodules_and_ignored_dirs_are_never_read():
    entries = [
        _blob("skills/real/SKILL.md"),
        # A symlink's blob content is the TARGET PATH — importing it would store
        # "../../etc/passwd" as somebody's skill method.
        _blob("skills/link/SKILL.md", mode="120000"),
        # A submodule entry points into another repo entirely.
        _blob("skills/sub", type_="commit", mode="160000"),
        _blob("node_modules/pkg/SKILL.md"),
        _blob("vendor/other/SKILL.md"),
        _blob(".git/hooks/SKILL.md"),
    ]
    bodies = {p["path"]: _skill_md("x", "Ex.") for p in entries}
    fetched: list[str] = []

    def _capture(_i, _r, _sha, paths):
        fetched.extend(paths)
        return {p: bodies[p] for p in paths if p in bodies}

    with patch.object(
        github_source, "resolve_commit", return_value=("abc123", "main")
    ), patch(
        "app.connectors.github_app.get_installation_token", return_value="ghs_x"
    ), patch(
        "app.connectors.github_app.fetch_repo_tree_entries", return_value=(entries, False)
    ), patch.object(github_source, "fetch_skill_files", side_effect=_capture):
        result = github_source.discover_skills(42, "octocat/methods", "main")

    assert [s.path for s in result.skills] == ["skills/real"]
    assert fetched == ["skills/real/SKILL.md"]


def test_non_markdown_files_are_ignored():
    entries = [
        _blob("skills/a/SKILL.md"),
        _blob("skills/a/script.py"),
        _blob("skills/a/logo.png"),
    ]
    bodies = {"skills/a/SKILL.md": _skill_md("a", "Ay.")}
    skill = _discover(entries, bodies).skills[0]
    # Uploaded/imported code is never executed and never stored — same rule the
    # zip parser applies to non-.md members.
    assert skill.parsed.modules == {} and skill.file_count == 1


def test_an_oversize_markdown_file_is_reported_not_fetched():
    entries = [
        _blob("skills/huge/SKILL.md", size=2 * 1024 * 1024),
        _blob("skills/fine/SKILL.md"),
    ]
    bodies = {"skills/fine/SKILL.md": _skill_md("fine", "Fine.")}
    result = _discover(entries, bodies)
    by_path = {s.path: s for s in result.skills}
    huge = by_path["skills/huge"]
    # Over the Contents API's 1 MB base64 ceiling — reported against its skill
    # with a reason, never re-fetched through the raw media type.
    assert huge.importable is False
    assert "1 MB" in huge.reason
    assert by_path["skills/fine"].importable is True


# ─── budgets ─────────────────────────────────────────────────────────────────


def test_the_trees_truncated_flag_is_surfaced_with_a_note():
    entries = [_blob("skills/a/SKILL.md")]
    bodies = {"skills/a/SKILL.md": _skill_md("a", "Ay.")}
    result = _discover(entries, bodies, truncated=True)
    # A partial listing that looked complete would quietly import a subset of
    # the repo and tell the user nothing.
    assert result.truncated is True
    assert result.notes and "too large" in result.notes[0]


def test_the_skill_cap_clips_and_says_so(monkeypatch):
    monkeypatch.setattr(github_source, "MAX_SKILLS", 2)
    entries = [_blob(f"skills/s{i}/SKILL.md") for i in range(5)]
    bodies = {e["path"]: _skill_md(f"s{i}", "Desc.") for i, e in enumerate(entries)}
    result = _discover(entries, bodies)
    assert len(result.skills) == 2
    assert result.truncated is True
    assert any("more than 2 skills" in n for n in result.notes)


# ─── naming and invalid skills ───────────────────────────────────────────────


def test_a_skill_with_no_description_is_listed_as_invalid():
    entries = [_blob("skills/bare/SKILL.md"), _blob("skills/good/SKILL.md")]
    bodies = {
        "skills/bare/SKILL.md": "# Bare\n\n## Only headings\n",
        "skills/good/SKILL.md": _skill_md("good", "Good."),
    }
    by_path = {s.path: s for s in _discover(entries, bodies).skills}
    # Listed, not hidden: the picker shows why, so the fix is obvious.
    assert by_path["skills/bare"].importable is False
    assert "description:" in by_path["skills/bare"].reason


def test_a_skill_over_the_character_cap_is_listed_as_invalid():
    from app.skills.custom import MAX_SKILL_CONTENT_CHARS

    entries = [_blob("skills/big/SKILL.md")]
    bodies = {
        "skills/big/SKILL.md": _skill_md("big", "Big.", "x" * (MAX_SKILL_CONTENT_CHARS + 1))
    }
    skill = _discover(entries, bodies).skills[0]
    assert skill.importable is False
    assert f"{MAX_SKILL_CONTENT_CHARS:,} character" in skill.reason
    assert skill.char_count > MAX_SKILL_CONTENT_CHARS
