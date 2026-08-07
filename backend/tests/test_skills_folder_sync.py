"""Synced skill folders — the half of the GitHub import that runs unattended.

  POST   /v1/skills/github/import  {..., sync: true}  -> register the folder
  GET    /v1/skills/sources                           -> what's being synced
  POST   /v1/skills/sources/{id}/sync                 -> re-read it now
  DELETE /v1/skills/sources/{id}                      -> stop, keep the skills
  app.skills.github_sync.sync_source / sync_all_sources -> the 30-min sweep

The behaviour this file pins down, because every one of them is a decision
someone could reasonably have made the other way:

  - a synced folder imports EVERY .md in it, later ones included. That is the
    feature, and it is also why a README in the folder becomes a skill.
  - syncing a repo ROOT is refused (422) — a repository's markdown is not a
    skill library, and nobody is ticking boxes on the sweep.
  - a synced skill is read-only: PATCH and DELETE both 409, because the sweep
    would overwrite an edit and undo a delete within the half hour.
  - a file DELETED from the folder leaves its skill alone. Deletions are not
    mirrored; a background timer that removes things people use is worse than
    a stale skill.
  - stopping a folder RELEASES its skills — they stay, and become editable.

GitHub is mocked at github_source's seams, the same way the import-route suite
does it; nothing here touches the network.
"""
from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

import pytest


@pytest.fixture(autouse=True)
def _isolated_storage_dir(tmp_path, monkeypatch, isolated_settings):
    import app.skills_storage as ss

    monkeypatch.setattr(ss.settings, "storage_dir", str(tmp_path / "proto"), raising=False)


def _skill_md(name: str, description: str, body: str = "Do the thing.") -> str:
    return f"---\nname: {name}\ndescription: {description}\n---\n\n{body}\n"


def _blob(path: str, *, size: int = 100) -> dict:
    return {"path": path, "type": "blob", "mode": "100644", "size": size, "sha": "sha1"}


_TREE = [
    _blob("skills/sprint-planner/SKILL.md"),
    _blob("skills/pricing-review/SKILL.md"),
]
_BODIES = {
    "skills/sprint-planner/SKILL.md": _skill_md("sprint-planner", "Plans a sprint."),
    "skills/pricing-review/SKILL.md": _skill_md("pricing-review", "Reviews pricing."),
}


def _connect_repo(company_id: str, *, installation_id: int = 4242, owner: str = "octocat"):
    from app import db

    db.upsert_github_installation(
        installation_id=installation_id,
        account_id=1,
        account_login=owner,
        account_type="Organization",
        company_id=company_id,
    )


class _Github:
    """Stub github_source's GitHub calls. `sha` is the commit every resolve
    answers with, so a test can move the branch by constructing a new one."""

    def __init__(self, tree=None, bodies=None, sha="c0ffee", resolve_error=None):
        self.tree = _TREE if tree is None else tree
        self.bodies = _BODIES if bodies is None else bodies
        self.sha = sha
        self.resolve_error = resolve_error
        self._patches = []

    def __enter__(self):
        from app.skills import github_source

        resolve = (
            patch.object(github_source, "resolve_commit", side_effect=self.resolve_error)
            if self.resolve_error is not None
            else patch.object(
                github_source, "resolve_commit", return_value=(self.sha, "main")
            )
        )
        self._patches = [
            resolve,
            patch("app.connectors.github_app.get_installation_token", return_value="ghs_x"),
            patch(
                "app.connectors.github_app.fetch_repo_tree_entries",
                return_value=(self.tree, False),
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


def _import(client, paths, *, repo="octocat/methods", **body):
    return client.post(
        "/v1/skills/github/import", json={"repo": repo, "paths": paths, **body}
    )


def _import_synced(client, paths=("sprint-planner",), path="skills"):
    """The common setup: import a folder with syncing turned on.

    `paths` are relative to `path` — discovery walks the requested folder, so a
    `path=skills` import names its skills after their own folders rather than
    after "skills"."""
    return _import(client, list(paths), path=path, sync=True)


def _sources(client) -> list[dict]:
    return client.get("/v1/skills/sources").json()["sources"]


# ─── registering a folder ────────────────────────────────────────────────────


def test_importing_with_sync_registers_the_folder_and_marks_its_skills(tenant_client):
    t = tenant_client.make(slug="acme")
    _connect_repo(t.company_id)
    with _Github():
        resp = _import_synced(t.client)
    assert resp.status_code == 201, resp.text
    body = resp.json()
    assert body["synced"] is True
    assert body["imported"][0]["synced"] is True

    sources = _sources(t.client)
    assert len(sources) == 1
    assert sources[0]["repo"] == "octocat/methods"
    assert sources[0]["path"] == "skills"
    assert sources[0]["active"] is True
    # The sha the import read is stamped, so the next sweep can short-circuit.
    assert sources[0]["last_commit_sha"] == "c0ffee"[:7]


def test_importing_without_sync_registers_nothing(tenant_client):
    t = tenant_client.make(slug="acme")
    _connect_repo(t.company_id)
    with _Github():
        resp = _import(t.client, ["sprint-planner"], path="skills")
    assert resp.status_code == 201, resp.text
    assert resp.json()["synced"] is False
    assert resp.json()["imported"][0]["synced"] is False
    assert _sources(t.client) == []


def test_syncing_a_repo_root_is_refused(tenant_client):
    """Every .md in a whole repository is not a skill library. The one-shot
    import at the root still works — a human is ticking those boxes."""
    t = tenant_client.make(slug="acme")
    _connect_repo(t.company_id)
    with _Github():
        resp = _import(t.client, ["skills/sprint-planner"], sync=True)
    assert resp.status_code == 422
    assert "folder" in resp.json()["detail"].lower()
    assert _sources(t.client) == []


def test_reimporting_the_same_folder_updates_one_source(tenant_client):
    t = tenant_client.make(slug="acme")
    _connect_repo(t.company_id)
    with _Github():
        _import_synced(t.client)
        _import_synced(t.client, paths=("pricing-review",))
    # One folder, one row — not a second source syncing the same files.
    assert len(_sources(t.client)) == 1


# ─── synced skills are the repo's ────────────────────────────────────────────


def test_editing_a_synced_skill_is_refused(tenant_client):
    t = tenant_client.make(slug="acme")
    _connect_repo(t.company_id)
    with _Github():
        skill = _import_synced(t.client).json()["imported"][0]

    resp = t.client.patch(
        f"/v1/skills/{skill['id']}",
        json={"name": "Mine now", "description": "Changed.", "method": "# new"},
    )
    assert resp.status_code == 409
    assert "github" in resp.json()["detail"].lower()


def test_deleting_a_synced_skill_is_refused(tenant_client):
    """It would come back on the next sweep, so the refusal points at the two
    things that actually work: the repo, or stopping the folder."""
    t = tenant_client.make(slug="acme")
    _connect_repo(t.company_id)
    with _Github():
        skill = _import_synced(t.client).json()["imported"][0]

    resp = t.client.delete(f"/v1/skills/{skill['id']}")
    assert resp.status_code == 409
    assert t.client.get("/v1/skills").json()["skills"], "the skill must survive"


def test_a_hand_uploaded_skill_stays_editable(tenant_client):
    """The read-only rule keys on the SOURCE, not on skills in general."""
    t = tenant_client.make(slug="acme")
    created = t.client.post(
        "/v1/skills",
        files={"file": ("s.md", b"# ours\n", "text/markdown")},
        data={"name": "Ours", "description": "Hand made."},
    ).json()
    assert created["synced"] is False
    resp = t.client.patch(
        f"/v1/skills/{created['id']}",
        json={"name": "Ours", "description": "Edited.", "method": "# edited"},
    )
    assert resp.status_code == 200, resp.text


# ─── the sweep ───────────────────────────────────────────────────────────────


async def test_a_file_added_to_the_folder_becomes_a_skill_on_its_own(tenant_client):
    """The whole feature: nobody ticked this file, and it is a skill."""
    from app import db
    from app.skills.github_sync import sync_source

    t = tenant_client.make(slug="acme")
    _connect_repo(t.company_id)
    with _Github():
        # Only sprint-planner was ticked, but switching syncing on makes the
        # FOLDER the unit, so both of its skills land immediately.
        _import_synced(t.client)
    assert {s["slug"] for s in t.client.get("/v1/skills").json()["skills"]} == {
        "sprint-planner",
        "pricing-review",
    }

    # Someone commits a new method to the folder. New tree, new commit.
    tree = [*_TREE, _blob("skills/retro-runner/SKILL.md")]
    bodies = {
        **_BODIES,
        "skills/retro-runner/SKILL.md": _skill_md("retro-runner", "Runs a retro."),
    }
    source = db.list_active_skill_sources()[0]
    with _Github(tree=tree, bodies=bodies, sha="beefbeef"):
        result = await sync_source(source)

    assert result.error == ""
    slugs = {s["slug"] for s in t.client.get("/v1/skills").json()["skills"]}
    # Every .md in the folder, including the two nobody selected at import.
    assert slugs == {"sprint-planner", "pricing-review", "retro-runner"}


async def test_an_unchanged_folder_costs_one_call_and_imports_nothing(tenant_client):
    from app import db
    from app.skills.github_sync import sync_source

    t = tenant_client.make(slug="acme")
    _connect_repo(t.company_id)
    with _Github():
        _import_synced(t.client)

    source = db.list_active_skill_sources()[0]
    with _Github() as gh:  # same sha as the import
        with patch(
            "app.connectors.github_app.fetch_repo_tree_entries",
            side_effect=AssertionError("the tree must not be walked"),
        ):
            result = await sync_source(source)
    assert result.changed is False and result.imported == 0 and result.error == ""
    assert gh is not None


async def test_a_file_removed_from_the_folder_leaves_its_skill_alone(tenant_client):
    """Deletions are deliberately not mirrored."""
    from app import db
    from app.skills.github_sync import sync_source

    t = tenant_client.make(slug="acme")
    _connect_repo(t.company_id)
    with _Github():
        _import_synced(t.client, paths=("sprint-planner", "pricing-review"))

    source = db.list_active_skill_sources()[0]
    # pricing-review is gone from the repo.
    with _Github(tree=[_blob("skills/sprint-planner/SKILL.md")], sha="beefbeef"):
        await sync_source(source)

    slugs = {s["slug"] for s in t.client.get("/v1/skills").json()["skills"]}
    assert "pricing-review" in slugs, "a removed file must not delete its skill"


async def test_a_github_failure_records_the_error_and_keeps_the_last_good_sha(tenant_client):
    from app import db
    from app.skills import github_source
    from app.skills.github_sync import sync_source

    t = tenant_client.make(slug="acme")
    _connect_repo(t.company_id)
    with _Github():
        _import_synced(t.client)

    source = db.list_active_skill_sources()[0]
    with _Github(resolve_error=github_source.GithubSourceError("GitHub returned 500.")):
        result = await sync_source(source)

    assert result.error == "GitHub returned 500."
    row = db.list_active_skill_sources()[0]
    # The last KNOWN-GOOD sha survives, so the next sweep still sees the folder
    # as it was rather than treating the failure as "unchanged".
    assert row["last_commit_sha"] == "c0ffee"
    assert row["last_error"] == "GitHub returned 500."
    # And the panel shows it.
    assert _sources(t.client)[0]["last_error"] == "GitHub returned 500."


async def test_a_clean_sync_clears_a_previous_error(tenant_client):
    from app import db
    from app.skills import github_source
    from app.skills.github_sync import sync_source

    t = tenant_client.make(slug="acme")
    _connect_repo(t.company_id)
    with _Github():
        _import_synced(t.client)
    source = db.list_active_skill_sources()[0]
    with _Github(resolve_error=github_source.GithubSourceError("Down.")):
        await sync_source(source)

    with _Github(sha="beefbeef"):
        await sync_source(db.list_active_skill_sources()[0])
    assert _sources(t.client)[0]["last_error"] == ""


async def test_one_bad_source_does_not_stop_the_sweep(tenant_client):
    """Cross-tenant isolation: a broken folder costs its own company only."""
    from app import db
    from app.skills.github_sync import sync_all_sources

    a = tenant_client.make(slug="acme")
    _connect_repo(a.company_id, installation_id=1, owner="octocat")
    with _Github():
        _import_synced(a.client)

    # A second company whose source row is malformed (no repo).
    b = tenant_client.make(slug="globex", user_id="u-globex")
    db.upsert_skill_source(
        company_id=b.company_id,
        workspace_id=None,
        installation_id=0,
        repo="",
        ref="",
        path="skills",
        created_by=None,
    )

    with _Github(sha="beefbeef"):
        results = await sync_all_sources()
    assert len(results) == 2
    assert sum(1 for r in results if r.error) == 1
    # The healthy company still got its skills.
    assert a.client.get("/v1/skills").json()["skills"]


# ─── managing a folder ───────────────────────────────────────────────────────


async def test_sync_now_re_reads_even_when_the_commit_has_not_moved(tenant_client):
    """The forced path — a button that answered "nothing changed" about a
    folder the user can see is not fully imported would be useless."""
    t = tenant_client.make(slug="acme")
    _connect_repo(t.company_id)
    with _Github():
        _import_synced(t.client)
    source_id = _sources(t.client)[0]["id"]

    # A file appears in the folder at the SAME commit — the sweep's cheap path
    # would skip this, which is exactly the state the button exists for.
    tree = [*_TREE, _blob("skills/retro-runner/SKILL.md")]
    bodies = {
        **_BODIES,
        "skills/retro-runner/SKILL.md": _skill_md("retro-runner", "Runs a retro."),
    }
    with _Github(tree=tree, bodies=bodies):  # same sha as the import
        resp = t.client.post(f"/v1/skills/sources/{source_id}/sync")
    assert resp.status_code == 200, resp.text
    assert resp.json()["error"] == ""
    slugs = {s["slug"] for s in t.client.get("/v1/skills").json()["skills"]}
    assert slugs == {"sprint-planner", "pricing-review", "retro-runner"}


def test_stop_syncing_keeps_the_skills_and_makes_them_editable(tenant_client):
    t = tenant_client.make(slug="acme")
    _connect_repo(t.company_id)
    with _Github():
        skill = _import_synced(t.client).json()["imported"][0]
    source_id = _sources(t.client)[0]["id"]

    resp = t.client.delete(f"/v1/skills/sources/{source_id}")
    assert resp.status_code == 200, resp.text
    # Both of the folder's skills — a synced import takes the whole folder.
    assert resp.json()["released"] == 2

    listed = t.client.get("/v1/skills").json()["skills"]
    assert {s["slug"] for s in listed} == {"sprint-planner", "pricing-review"}, (
        "stopping a sync must never remove a skill"
    )
    assert all(s["synced"] is False for s in listed)
    # Released means editable — that is the point of stopping.
    edit = t.client.patch(
        f"/v1/skills/{skill['id']}",
        json={"name": "Sprint Planner", "description": "Ours now.", "method": "# mine"},
    )
    assert edit.status_code == 200, edit.text


async def test_a_stopped_folder_is_not_swept(tenant_client):
    from app.skills.github_sync import sync_all_sources

    t = tenant_client.make(slug="acme")
    _connect_repo(t.company_id)
    with _Github():
        _import_synced(t.client)
    t.client.delete(f"/v1/skills/sources/{_sources(t.client)[0]['id']}")

    # A new method lands in the folder after syncing was stopped.
    tree = [*_TREE, _blob("skills/retro-runner/SKILL.md")]
    bodies = {
        **_BODIES,
        "skills/retro-runner/SKILL.md": _skill_md("retro-runner", "Runs a retro."),
    }
    with _Github(tree=tree, bodies=bodies, sha="beefbeef"):
        assert await sync_all_sources() == []
    # It never arrived, because nothing swept the folder.
    slugs = {s["slug"] for s in t.client.get("/v1/skills").json()["skills"]}
    assert "retro-runner" not in slugs


# ─── tenancy ─────────────────────────────────────────────────────────────────


def test_another_companys_source_is_a_404(tenant_client):
    """404 and not 403, like every other ownership check here: a 403 would
    confirm the folder exists and belongs to someone."""
    a = tenant_client.make(slug="acme")
    _connect_repo(a.company_id)
    with _Github():
        _import_synced(a.client)
    source_id = _sources(a.client)[0]["id"]

    b = tenant_client.make(slug="globex", user_id="u-globex")
    assert _sources(b.client) == []
    assert b.client.post(f"/v1/skills/sources/{source_id}/sync").status_code == 404
    assert b.client.delete(f"/v1/skills/sources/{source_id}").status_code == 404
