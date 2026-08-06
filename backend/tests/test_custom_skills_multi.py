"""Uploading ONE zip that holds SEVERAL skills (POST /v1/skills).

A zipped `skills/` directory — one folder per skill, the layout Claude Code
and the Agent Skills format use — used to import as a single mangled row: the
first SKILL.md won the method slot, the second became a "module" of it, and
the third was dropped outright by the basename `setdefault`. It now imports as
one row, one trigger and one stored file per skill.

Covered here:
- N folders → N rows, N staged originals, and a `{skills, skipped}` body
- each skill named/described from its own SKILL.md frontmatter, not the form
- the collision rules applied PER SKILL: a name the company already used
  replaces that row in place, a built-in's name takes the `-2` trigger
- per-skill failures (the 50k character cap) are skipped with a reason while
  the rest import; importing nothing at all is the only 400
- each row owns a standalone original, so per-skill download still works
- the SINGLE-skill body is untouched — same object, no `skills` key
- company isolation: another tenant's identically named skills are untouched

The single-skill path's own ~60 tests live in test_custom_skills_routes.py and
are deliberately unedited — that they still pass is the proof this feature
changed nothing underneath them.
"""
from __future__ import annotations

import io
import zipfile
from pathlib import Path

import pytest


@pytest.fixture(autouse=True)
def _isolated_storage_dir(tmp_path, monkeypatch, isolated_settings):
    """Per-test filesystem storage fallback (same fixture the sibling route
    tests use) so staged skill files never leak between tests."""
    import app.skills_storage as ss

    monkeypatch.setattr(ss.settings, "storage_dir", str(tmp_path / "proto"), raising=False)


def _zip_bytes(entries: dict[str, bytes]) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        for name, data in entries.items():
            zf.writestr(name, data)
    return buf.getvalue()


def _skill_md(name: str, description: str, body: str = "Do the thing.") -> bytes:
    return f"---\nname: {name}\ndescription: {description}\n---\n\n{body}\n".encode()


def _upload(client, data, *, filename="skills.zip", name="Ignored Name",
            description="Ignored description"):
    """The form still sends a name/description — the modal always does — and a
    multi-skill archive ignores both, which several tests below assert."""
    return client.post(
        "/v1/skills",
        files={"file": (filename, data, "application/zip")},
        data={"name": name, "description": description},
    )


def _staged_files() -> list[Path]:
    import app.skills_storage as ss

    root = Path(ss.settings.storage_dir).resolve() / "custom-skills"
    return [p for p in root.rglob("*") if p.is_file()] if root.is_dir() else []


_TWO_SKILLS = {
    "skills/sprint-planner/SKILL.md": _skill_md("sprint-planner", "Plans a sprint."),
    "skills/sprint-planner/references/source.md": b"cited",
    "skills/pricing-review/SKILL.md": _skill_md("pricing-review", "Reviews pricing."),
}


# ─── the happy path ──────────────────────────────────────────────────────────


def test_multi_skill_zip_creates_one_row_per_skill(tenant_client):
    from app import db

    t = tenant_client.make(slug="acme")
    resp = _upload(t.client, _zip_bytes(_TWO_SKILLS))
    assert resp.status_code == 201, resp.text
    body = resp.json()
    assert body["skipped"] == []
    created = {s["name"]: s for s in body["skills"]}
    assert sorted(created) == ["Pricing Review", "Sprint Planner"]
    # Each skill names ITSELF from its frontmatter — the form's name and
    # description cannot describe two skills, so they are ignored here.
    assert created["Sprint Planner"]["description"] == "Plans a sprint."
    assert created["Sprint Planner"]["trigger"] == "/sprint-planner"
    assert created["Pricing Review"]["trigger"] == "/pricing-review"
    assert all(s["replaced"] is False for s in body["skills"])
    assert "Ignored Name" not in [s["name"] for s in body["skills"]]

    # Two library rows, each carrying only its own content.
    listed = t.client.get("/v1/skills").json()["skills"]
    assert sorted(s["slug"] for s in listed) == ["pricing-review", "sprint-planner"]
    planner = db.get_custom_skill(t.company_id, "sprint-planner")
    assert planner["references"] == {"source.md": "cited"}
    assert "Reviews pricing" not in planner["method"]
    assert db.get_custom_skill(t.company_id, "pricing-review")["references"] == {}


def test_each_skill_gets_its_own_stored_original(tenant_client):
    t = tenant_client.make(slug="acme")
    body = _upload(t.client, _zip_bytes(_TWO_SKILLS)).json()
    # One file per row, not one shared bundle: the first delete would otherwise
    # strip the object out from under the other row.
    assert len(_staged_files()) == 2

    by_name = {s["name"]: s for s in body["skills"]}
    # The skill with a supporting file round-trips as its own .zip; the one
    # that is just a method comes back as the .md it effectively was.
    planner = t.client.get(f"/v1/skills/{by_name['Sprint Planner']['id']}/file")
    assert planner.status_code == 200, planner.text
    assert planner.json()["name"] == "sprint-planner.zip"
    pricing = t.client.get(f"/v1/skills/{by_name['Pricing Review']['id']}/file")
    assert pricing.json()["name"] == "pricing-review.md"

    # Deleting one leaves the other's original in place.
    assert t.client.delete(f"/v1/skills/{by_name['Sprint Planner']['id']}").status_code == 200
    assert len(_staged_files()) == 1


def test_multi_skill_zip_without_frontmatter_names_skills_from_their_folders(tenant_client):
    t = tenant_client.make(slug="acme")
    data = _zip_bytes({
        "raci-builder/SKILL.md": b"# RACI\n\nBuilds a RACI grid for a launch.\n",
        "journey-mapper/SKILL.md": b"# Journey\n\nMaps an actor's journey.\n",
    })
    body = _upload(t.client, data).json()
    created = {s["name"]: s for s in body["skills"]}
    assert sorted(created) == ["Journey Mapper", "Raci Builder"]
    assert created["Journey Mapper"]["description"] == "Maps an actor's journey."


# ─── collisions, per skill ───────────────────────────────────────────────────


def test_each_skill_replaces_the_companys_own_skill_of_that_name(tenant_client):
    from app import db

    t = tenant_client.make(slug="acme")
    first = _upload(t.client, _zip_bytes(_TWO_SKILLS)).json()
    ids = {s["name"]: s["id"] for s in first["skills"]}

    # Re-uploading the export after editing one method is a new VERSION of the
    # skills it holds, exactly as a single re-upload is: same rows, same
    # triggers, new content — and one new skill for the folder that was added.
    second = _upload(t.client, _zip_bytes({
        **_TWO_SKILLS,
        "skills/sprint-planner/SKILL.md": _skill_md(
            "sprint-planner", "Plans a sprint.", "Version two of the method."
        ),
        "skills/raci-builder/SKILL.md": _skill_md("raci-builder", "Builds a RACI."),
    })).json()
    by_name = {s["name"]: s for s in second["skills"]}
    assert by_name["Sprint Planner"]["replaced"] is True
    assert by_name["Sprint Planner"]["id"] == ids["Sprint Planner"]
    assert by_name["Raci Builder"]["replaced"] is False

    listed = t.client.get("/v1/skills").json()["skills"]
    assert len(listed) == 3  # not six
    assert "Version two" in db.get_custom_skill(t.company_id, "sprint-planner")["method"]
    # One original per row still — the superseded ones were cleaned up.
    assert len(_staged_files()) == 3


def test_a_skill_sharing_a_builtins_name_takes_the_next_trigger(tenant_client, monkeypatch):
    import app.routes.custom_skills as mod

    t = tenant_client.make(slug="acme")
    monkeypatch.setattr(mod, "list_skills", lambda: ["prd-author"])
    data = _zip_bytes({
        "prd-author/SKILL.md": _skill_md("prd-author", "Ours, not theirs."),
        "pricing-review/SKILL.md": _skill_md("pricing-review", "Reviews pricing."),
    })
    body = _upload(t.client, data).json()
    ours = next(s for s in body["skills"] if s["name"] == "Prd Author")
    # The built-in keeps /prd-author and is never replaced; ours takes the next
    # free trigger and says so.
    assert ours["slug"] == "prd-author-2"
    assert ours["name_conflict"] is True


def test_two_folders_with_one_name_end_as_one_skill(tenant_client):
    from app import db

    t = tenant_client.make(slug="acme")
    data = _zip_bytes({
        "first/SKILL.md": _skill_md("Duplicate", "The first copy.", "First body."),
        "second/SKILL.md": _skill_md("Duplicate", "The second copy.", "Second body."),
    })
    body = _upload(t.client, data).json()
    # Same name inside one archive is the same skill twice, so the second
    # folder replaces the first rather than racing it for the trigger — the
    # library read happens per skill for exactly this reason.
    assert [s["replaced"] for s in body["skills"]] == [False, True]
    assert len({s["id"] for s in body["skills"]}) == 1
    assert len(t.client.get("/v1/skills").json()["skills"]) == 1
    assert "Second body" in db.get_custom_skill(t.company_id, "duplicate")["method"]
    assert len(_staged_files()) == 1


def test_multi_skill_import_never_reaches_another_company(tenant_client):
    from app import db

    a = tenant_client.make(slug="acme")
    b = tenant_client.make(slug="globex")
    assert _upload(a.client, _zip_bytes(_TWO_SKILLS)).status_code == 201
    theirs = db.get_custom_skill(a.company_id, "sprint-planner")

    resp = _upload(b.client, _zip_bytes({
        "skills/sprint-planner/SKILL.md": _skill_md(
            "sprint-planner", "Globex's own.", "Globex body."
        ),
        "skills/pricing-review/SKILL.md": _skill_md("pricing-review", "Globex pricing."),
    }))
    assert resp.status_code == 201
    assert all(s["replaced"] is False for s in resp.json()["skills"])
    # Acme's rows are byte-for-byte what they were; the slugs coexist across
    # tenants because uniqueness is per company.
    after = db.get_custom_skill(a.company_id, "sprint-planner")
    assert after["id"] == theirs["id"] and after["method"] == theirs["method"]
    assert "Globex body" in db.get_custom_skill(b.company_id, "sprint-planner")["method"]


# ─── partial failure ─────────────────────────────────────────────────────────


def test_one_oversized_skill_is_skipped_and_the_rest_import(tenant_client):
    from app.skills.custom import MAX_SKILL_CONTENT_CHARS

    t = tenant_client.make(slug="acme")
    huge = b"a" * (MAX_SKILL_CONTENT_CHARS + 1)
    data = _zip_bytes({
        "sprint-planner/SKILL.md": _skill_md("sprint-planner", "Plans a sprint."),
        "bloated/SKILL.md": _skill_md("bloated", "Too much.").replace(b"Do the thing.", huge),
        "pricing-review/SKILL.md": _skill_md("pricing-review", "Reviews pricing."),
    })
    resp = _upload(t.client, data)
    # A 12-skill export with one over-cap method should import eleven, not
    # fail: 201 with the reason attached to the folder that caused it.
    assert resp.status_code == 201, resp.text
    body = resp.json()
    assert sorted(s["name"] for s in body["skills"]) == ["Pricing Review", "Sprint Planner"]
    assert [s["path"] for s in body["skipped"]] == ["bloated"]
    assert f"{MAX_SKILL_CONTENT_CHARS:,} character" in body["skipped"][0]["reason"]
    # The rejected skill left nothing behind — two rows, two originals.
    assert len(t.client.get("/v1/skills").json()["skills"]) == 2
    assert len(_staged_files()) == 2


def test_unusable_folders_are_reported_without_blocking_the_import(tenant_client):
    t = tenant_client.make(slug="acme")
    data = _zip_bytes({
        "good/SKILL.md": _skill_md("good", "A good one."),
        "empty/SKILL.md": b"  \n",
        "undescribed/SKILL.md": b"# Undescribed\n",
        "another/SKILL.md": _skill_md("another", "Another good one."),
    })
    body = _upload(t.client, data).json()
    assert sorted(s["name"] for s in body["skills"]) == ["Another", "Good"]
    assert sorted(s["path"] for s in body["skipped"]) == ["empty", "undescribed"]
    assert all(s["reason"] for s in body["skipped"])


def test_an_archive_that_yields_nothing_is_a_400_with_the_reasons(tenant_client):
    t = tenant_client.make(slug="acme")
    data = _zip_bytes({
        "empty/SKILL.md": b"  \n",
        "undescribed/SKILL.md": b"# Undescribed\n",
    })
    resp = _upload(t.client, data)
    # Nothing was created, so there is no 201 to give — and the message names
    # every folder and why, because that is what the user has to fix.
    assert resp.status_code == 400
    detail = resp.json()["detail"]
    assert "Empty" in detail and "Undescribed" in detail
    assert t.client.get("/v1/skills").json()["skills"] == []
    assert _staged_files() == []


# ─── the single-skill body is untouched ──────────────────────────────────────


def test_a_single_skill_zip_still_answers_the_single_object_body(tenant_client):
    t = tenant_client.make(slug="acme")
    # One SKILL.md plus supporting files is the layout that has always worked;
    # it must not sprout a `skills` list now.
    data = _zip_bytes({
        "my-skill/SKILL.md": b"# Method\n",
        "my-skill/modules/extra.md": b"more",
    })
    resp = _upload(t.client, data, filename="my-skill.zip", name="Estimation Helper",
                   description="Scores features")
    assert resp.status_code == 201, resp.text
    body = resp.json()
    assert "skills" not in body and "skipped" not in body
    # The FORM's name and description are still what a single skill is called.
    assert body["name"] == "Estimation Helper"
    assert body["description"] == "Scores features"
    assert body["slug"] == "estimation-helper"
    assert body["replaced"] is False
    assert len(_staged_files()) == 1


def test_a_zip_with_one_skill_folder_is_still_a_single_upload(tenant_client):
    t = tenant_client.make(slug="acme")
    data = _zip_bytes({"skills/only-one/SKILL.md": _skill_md("only-one", "Just one.")})
    body = _upload(t.client, data, name="Named By The Form",
                   description="Described by the form").json()
    # One SKILL.md is one skill however deep it sits, so the form still names
    # it and the body is still the single object.
    assert "skills" not in body
    assert body["name"] == "Named By The Form"
