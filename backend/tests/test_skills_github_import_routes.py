"""Importing skills straight from a connected GitHub repo.

  GET  /v1/skills/github/discover?repo=owner/name&ref=&path=
  POST /v1/skills/github/import   {repo, ref, path, paths[]}

Discover is read-only and previews each skill the way the picker needs it:
the trigger it would get, and whether it would REPLACE one of the company's
existing skills. Import re-runs discovery server-side and stores only skills
present in THAT result, through the same `store_skill` an upload uses — so the
collision rules, the per-row stored original, and the character cap are
identical whether a skill arrived in a zip or from a repo.

Tenancy is the sharp edge here. The installation is resolved from the caller's
company and the repo name (`find_github_installation_for_repo`, company-
filtered) and never from client input, so a repo another company connected is
a 404 — never a 403, which would confirm it exists.

GitHub is mocked at `github_source`'s two seams (commit resolution + tree/file
reads); nothing here touches the network.
"""
from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

import pytest


@pytest.fixture(autouse=True)
def _isolated_storage_dir(tmp_path, monkeypatch, isolated_settings):
    import app.skills_storage as ss

    monkeypatch.setattr(ss.settings, "storage_dir", str(tmp_path / "proto"), raising=False)


def _staged_files() -> list[Path]:
    import app.skills_storage as ss

    root = Path(ss.settings.storage_dir).resolve() / "custom-skills"
    return [p for p in root.rglob("*") if p.is_file()] if root.is_dir() else []


def _skill_md(name: str, description: str, body: str = "Do the thing.") -> str:
    return f"---\nname: {name}\ndescription: {description}\n---\n\n{body}\n"


def _blob(path: str, *, size: int = 100, mode: str = "100644", type_: str = "blob") -> dict:
    return {"path": path, "type": type_, "mode": mode, "size": size, "sha": "sha1"}


_TREE = [
    _blob("skills/sprint-planner/SKILL.md"),
    _blob("skills/sprint-planner/references/source.md"),
    _blob("skills/pricing-review/SKILL.md"),
]
_BODIES = {
    "skills/sprint-planner/SKILL.md": _skill_md("sprint-planner", "Plans a sprint."),
    "skills/sprint-planner/references/source.md": "cited",
    "skills/pricing-review/SKILL.md": _skill_md("pricing-review", "Reviews pricing."),
}


def _connect_repo(company_id: str, *, installation_id: int = 4242, owner: str = "octocat"):
    """Bind a GitHub App installation to this company — what 'the repo is
    connected' means to the resolver (it matches on owner + company)."""
    from app import db

    db.upsert_github_installation(
        installation_id=installation_id,
        account_id=1,
        account_login=owner,
        account_type="Organization",
        company_id=company_id,
    )


class _Github:
    """Context manager stubbing github_source's GitHub calls."""

    def __init__(self, tree=None, bodies=None, truncated=False):
        self.tree = _TREE if tree is None else tree
        self.bodies = _BODIES if bodies is None else bodies
        self.truncated = truncated
        self._patches = []

    def __enter__(self):
        from app.skills import github_source

        self._patches = [
            patch.object(github_source, "resolve_commit", return_value=("c0ffee", "main")),
            patch("app.connectors.github_app.get_installation_token", return_value="ghs_x"),
            patch(
                "app.connectors.github_app.fetch_repo_tree_entries",
                return_value=(self.tree, self.truncated),
            ),
            patch.object(
                github_source, "fetch_skill_files",
                side_effect=lambda _i, _r, _sha, paths: {
                    p: self.bodies[p] for p in paths if p in self.bodies
                },
            ),
        ]
        for p in self._patches:
            p.start()
        return self

    def __exit__(self, *exc):
        for p in self._patches:
            p.stop()
        return False


def _discover(client, repo="octocat/methods", **params):
    return client.get("/v1/skills/github/discover", params={"repo": repo, **params})


def _import(client, paths, repo="octocat/methods", **body):
    return client.post(
        "/v1/skills/github/import", json={"repo": repo, "paths": paths, **body}
    )


# ─── discover ────────────────────────────────────────────────────────────────


def test_discover_lists_the_repos_skills_without_writing_anything(tenant_client):
    t = tenant_client.make(slug="acme")
    _connect_repo(t.company_id)
    with _Github():
        resp = _discover(t.client)
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["commit_sha"] == "c0ffee" and body["ref"] == "main"
    by_path = {s["path"]: s for s in body["skills"]}
    assert sorted(by_path) == ["skills/pricing-review", "skills/sprint-planner"]
    planner = by_path["skills/sprint-planner"]
    assert planner["name"] == "Sprint Planner"
    assert planner["trigger_preview"] == "/sprint-planner"
    assert planner["status"] == "new"
    assert planner["file_count"] == 2 and planner["char_count"] > 0
    # Read-only: nothing was created by looking.
    assert t.client.get("/v1/skills").json()["skills"] == []
    assert _staged_files() == []


def test_discover_marks_a_skill_that_would_replace_one_of_ours(tenant_client):
    t = tenant_client.make(slug="acme")
    _connect_repo(t.company_id)
    # The company already has a skill under this name, so importing it is a new
    # VERSION of that skill and keeps its trigger — the picker must say so
    # before anyone commits to the import.
    t.client.post(
        "/v1/skills",
        files={"file": ("s.md", b"# ours\n", "text/markdown")},
        data={"name": "Sprint Planner", "description": "Ours."},
    )
    with _Github():
        body = _discover(t.client).json()
    planner = next(s for s in body["skills"] if s["path"] == "skills/sprint-planner")
    assert planner["status"] == "replaces"
    assert planner["trigger_preview"] == "/sprint-planner"


def test_discover_previews_the_trigger_a_builtin_name_would_get(tenant_client, monkeypatch):
    import app.routes.custom_skills as mod

    t = tenant_client.make(slug="acme")
    _connect_repo(t.company_id)
    monkeypatch.setattr(mod, "list_skills", lambda: ["sprint-planner"])
    with _Github():
        body = _discover(t.client).json()
    planner = next(s for s in body["skills"] if s["path"] == "skills/sprint-planner")
    # A built-in is never overridden: the preview shows the trigger the import
    # will actually hand out, computed the same way the write path does.
    assert planner["status"] == "new"
    assert planner["trigger_preview"] == "/sprint-planner-2"


def test_discover_reports_an_unimportable_skill_with_its_reason(tenant_client):
    t = tenant_client.make(slug="acme")
    _connect_repo(t.company_id)
    tree = [*_TREE, _blob("skills/bare/SKILL.md")]
    bodies = {**_BODIES, "skills/bare/SKILL.md": "# Bare\n"}
    with _Github(tree=tree, bodies=bodies):
        body = _discover(t.client).json()
    bare = next(s for s in body["skills"] if s["path"] == "skills/bare")
    assert bare["status"] == "invalid" and "description:" in bare["reason"]


# ─── tenancy ─────────────────────────────────────────────────────────────────


def test_a_repo_this_company_hasnt_connected_is_404(tenant_client):
    t = tenant_client.make(slug="acme")  # no installation bound
    with _Github():
        resp = _discover(t.client)
    assert resp.status_code == 404
    assert "connected" in resp.json()["detail"].lower()


def test_another_companys_repo_is_404_not_403(tenant_client):
    a = tenant_client.make(slug="acme")
    b = tenant_client.make(slug="globex")
    _connect_repo(a.company_id)  # only Acme connected octocat
    with _Github():
        resp = _discover(b.client)
        imported = _import(b.client, ["skills/sprint-planner"])
    # 404 both times: a 403 would confirm the repo exists and is connected to
    # Sprntly by somebody, which is exactly what a foreign tenant must not learn.
    assert resp.status_code == 404
    assert imported.status_code == 404
    assert b.client.get("/v1/skills").json()["skills"] == []


def test_import_without_an_origin_header_is_403(tenant_client):
    t = tenant_client.make(slug="acme")
    _connect_repo(t.company_id)
    # The test client defaults a same-origin header; a forged cross-site POST
    # would carry someone else's, so the mutating route fails closed without one.
    with _Github():
        resp = t.client.post(
            "/v1/skills/github/import",
            json={"repo": "octocat/methods", "paths": ["skills/sprint-planner"]},
            headers={"Origin": ""},
        )
    assert resp.status_code == 403
    assert t.client.get("/v1/skills").json()["skills"] == []


# ─── import ──────────────────────────────────────────────────────────────────


def test_import_creates_one_row_and_one_file_per_selected_skill(tenant_client):
    from app import db

    t = tenant_client.make(slug="acme")
    _connect_repo(t.company_id)
    with _Github():
        resp = _import(t.client, ["skills/sprint-planner", "skills/pricing-review"])
    assert resp.status_code == 201, resp.text
    body = resp.json()
    assert body["skipped"] == [] and body["commit_sha"] == "c0ffee"
    names = sorted(s["name"] for s in body["imported"])
    assert names == ["Pricing Review", "Sprint Planner"]
    assert all(s["replaced"] is False for s in body["imported"])

    # Each skill carries its own content and its own original file.
    planner = db.get_custom_skill(t.company_id, "sprint-planner")
    assert planner["references"] == {"source.md": "cited"}
    assert "Reviews pricing" not in planner["method"]
    assert len(_staged_files()) == 2
    assert len(t.client.get("/v1/skills").json()["skills"]) == 2


def test_import_takes_only_the_skills_that_were_selected(tenant_client):
    t = tenant_client.make(slug="acme")
    _connect_repo(t.company_id)
    with _Github():
        body = _import(t.client, ["skills/pricing-review"]).json()
    assert [s["name"] for s in body["imported"]] == ["Pricing Review"]
    assert [s["slug"] for s in t.client.get("/v1/skills").json()["skills"]] == [
        "pricing-review"
    ]


def test_a_path_that_is_not_in_the_repo_is_never_fetched(tenant_client):
    t = tenant_client.make(slug="acme")
    _connect_repo(t.company_id)
    # Client paths FILTER the server's own discovery; they are never fetch
    # targets, so a made-up path imports nothing rather than reading a file.
    with _Github():
        resp = _import(t.client, ["../../etc/passwd", "secrets/prod"])
    assert resp.status_code == 404
    assert t.client.get("/v1/skills").json()["skills"] == []


def test_reimporting_replaces_the_same_rows_in_place(tenant_client):
    from app import db

    t = tenant_client.make(slug="acme")
    _connect_repo(t.company_id)
    with _Github():
        first = _import(t.client, ["skills/sprint-planner"]).json()
    original_id = first["imported"][0]["id"]

    updated_bodies = {
        **_BODIES,
        "skills/sprint-planner/SKILL.md": _skill_md(
            "sprint-planner", "Plans a sprint.", "Version two of the method."
        ),
    }
    with _Github(bodies=updated_bodies):
        again = _import(t.client, ["skills/sprint-planner"]).json()
    # Re-importing after the repo moved on is a new VERSION: same row, same id,
    # same trigger the team has learned — not a second card.
    assert again["imported"][0]["replaced"] is True
    assert again["imported"][0]["id"] == original_id
    assert again["imported"][0]["slug"] == "sprint-planner"
    assert len(t.client.get("/v1/skills").json()["skills"]) == 1
    assert "Version two" in db.get_custom_skill(t.company_id, "sprint-planner")["method"]
    assert len(_staged_files()) == 1  # the superseded original was cleaned up


def test_a_builtin_name_takes_the_next_trigger_and_overrides_nothing(
    tenant_client, monkeypatch
):
    import app.routes.custom_skills as mod

    t = tenant_client.make(slug="acme")
    _connect_repo(t.company_id)
    monkeypatch.setattr(mod, "list_skills", lambda: ["sprint-planner"])
    with _Github():
        body = _import(t.client, ["skills/sprint-planner"]).json()
    ours = body["imported"][0]
    assert ours["slug"] == "sprint-planner-2"
    assert ours["name_conflict"] is True


def test_one_bad_skill_is_skipped_and_the_batch_survives(tenant_client):
    from app.skills.custom import MAX_SKILL_CONTENT_CHARS

    t = tenant_client.make(slug="acme")
    _connect_repo(t.company_id)
    tree = [*_TREE, _blob("skills/bloated/SKILL.md")]
    bodies = {
        **_BODIES,
        "skills/bloated/SKILL.md": _skill_md(
            "bloated", "Too much.", "x" * (MAX_SKILL_CONTENT_CHARS + 1)
        ),
    }
    with _Github(tree=tree, bodies=bodies):
        resp = _import(
            t.client,
            ["skills/sprint-planner", "skills/bloated", "skills/pricing-review"],
        )
    assert resp.status_code == 201, resp.text
    body = resp.json()
    # Eleven of twelve skills importing beats a request that fails outright.
    assert sorted(s["name"] for s in body["imported"]) == [
        "Pricing Review", "Sprint Planner"
    ]
    assert [s["path"] for s in body["skipped"]] == ["skills/bloated"]
    assert f"{MAX_SKILL_CONTENT_CHARS:,} character" in body["skipped"][0]["reason"]
    assert len(_staged_files()) == 2


def test_an_import_that_creates_nothing_is_a_400(tenant_client):
    t = tenant_client.make(slug="acme")
    _connect_repo(t.company_id)
    tree = [_blob("skills/bare/SKILL.md")]
    bodies = {"skills/bare/SKILL.md": "# Bare\n"}
    with _Github(tree=tree, bodies=bodies):
        resp = _import(t.client, ["skills/bare"])
    assert resp.status_code == 400
    assert "description:" in resp.json()["detail"]
    assert _staged_files() == []


def test_importing_nothing_is_a_422(tenant_client):
    t = tenant_client.make(slug="acme")
    _connect_repo(t.company_id)
    with _Github():
        assert _import(t.client, []).status_code == 422


# ─── GitHub failures ─────────────────────────────────────────────────────────


def test_a_missing_branch_is_a_404_not_a_502(tenant_client):
    from app.skills import github_source

    t = tenant_client.make(slug="acme")
    _connect_repo(t.company_id)
    with patch.object(
        github_source, "resolve_commit",
        side_effect=github_source.GithubRefNotFound("We couldn't find “nope” in octocat/methods."),
    ):
        resp = _discover(t.client, ref="nope")
    # A typo'd branch is the user's to fix; a 502 would tell them the wrong
    # thing about whose problem it is.
    assert resp.status_code == 404
    assert "nope" in resp.json()["detail"]


def test_a_github_transport_failure_is_a_502(tenant_client):
    from app.skills import github_source

    t = tenant_client.make(slug="acme")
    _connect_repo(t.company_id)
    with patch.object(
        github_source, "resolve_commit",
        side_effect=github_source.GithubSourceError("Couldn't reach GitHub. Please try again."),
    ):
        resp = _discover(t.client)
    assert resp.status_code == 502
    assert "GitHub" in resp.json()["detail"]
