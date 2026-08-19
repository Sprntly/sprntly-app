"""Custom skill upload/list/file routes (routes/custom_skills.py, PRD 1854).

Custom skills are COMPANY-scoped for now — all workspaces in a company share
one library; the uploading workspace is stamped on the row but never queried.

Covered:
- happy path: .md and .zip uploads create a company-scoped skill row and
  stage the original bytes (filesystem fallback in tests)
- the server-side validation ladder: missing/over-limit metadata (422), bad
  extension (422), empty file (400), oversize (413), unparseable content
  (400), over-limit parsed content in characters (413)
- name conflicts: a repeat of the company's OWN skill name REPLACES that skill
  in place (same id, same trigger, new content/hash, old original file cleaned
  up) and reports `replaced`; sharing a BUILT-IN skill's name is allowed and
  overrides nothing — the upload takes the next free trigger and reports it
  via `trigger` + `name_conflict`. Replacement matches within one company only
- list: newest-first metadata, company-isolated
- detail: one skill WITH its method text (the edit form's source); foreign ids
  404 indistinguishably
- edit (PATCH): name/description/method updated in place on the same row; a
  rename re-derives the trigger through the same built-in deconfliction; a
  rename onto another of the company's skills replaces (deletes) that one; the
  validation ladder and the 50k content cap mirror upload's; a .zip skill keeps
  its modules and gets a fresh content_hash; foreign ids 404 and leave the
  other tenant's identically named skill untouched
- file links: signed/fallback URLs for owned skills; foreign ids 404
- delete: removes the row and the staged original, frees the slug for
  re-upload; foreign/missing ids 404 indistinguishably
"""
from __future__ import annotations

import io
import zipfile
from pathlib import Path

import pytest

_MD = b"# Estimation method\nScore by reach x confidence.\n"


@pytest.fixture(autouse=True)
def _isolated_storage_dir(tmp_path, monkeypatch, isolated_settings):
    """Point the filesystem storage fallback at a per-test tmp dir so staged
    skill files never accumulate across tests (or pollute the repo data dir).

    Depends on isolated_settings so it runs AFTER _reload_app_modules() — then
    patches the settings instance skills_storage actually holds, which is the
    object its storage functions read at call time."""
    import app.skills_storage as ss

    monkeypatch.setattr(ss.settings, "storage_dir", str(tmp_path / "proto"), raising=False)


def _zip_bytes(entries: dict[str, bytes]) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        for name, data in entries.items():
            zf.writestr(name, data)
    return buf.getvalue()


def _upload(client, *, name="Estimation Helper", description="Scores features",
            filename="skill.md", data=_MD):
    return client.post(
        "/v1/skills",
        files={"file": (filename, data, "text/markdown")},
        data={"name": name, "description": description},
    )


def _edit(client, skill_id, *, name="Estimation Helper", description="Scores features",
          method="# Estimation method\nEdited in place.\n"):
    return client.patch(
        f"/v1/skills/{skill_id}",
        json={"name": name, "description": description, "method": method},
    )


def _staged_files() -> list[Path]:
    import app.skills_storage as ss

    root = Path(ss.settings.storage_dir).resolve() / "custom-skills"
    return [p for p in root.rglob("*") if p.is_file()] if root.is_dir() else []


# ─── happy path ──────────────────────────────────────────────────────────────


def test_upload_md_creates_skill(tenant_client):
    t = tenant_client.make(slug="acme")
    resp = _upload(t.client)
    assert resp.status_code == 201, resp.text
    body = resp.json()
    assert body["slug"] == "estimation-helper"
    assert body["trigger"] == "/estimation-helper"
    assert body["name"] == "Estimation Helper"
    assert body["description"] == "Scores features"
    # uploader_name is captured from the session (JWT name/email claims — the
    # minted test token carries neither, so it may be empty, never client-set).
    assert "uploader_name" in body
    assert body["has_file"] is True
    assert body["name_conflict"] is False
    # Nothing was replaced — this name was free.
    assert body["replaced"] is False
    # The original bytes are staged under the workspace prefix.
    assert len(_staged_files()) == 1


def test_upload_zip_creates_skill_with_modules(tenant_client):
    t = tenant_client.make(slug="acme")
    data = _zip_bytes({"SKILL.md": _MD, "modules/extra.md": b"more"})
    resp = _upload(t.client, name="Zip Skill", filename="zip-skill.zip", data=data)
    assert resp.status_code == 201, resp.text

    from app import db

    # Fetch through the real read API: slug lookup returns the parsed content.
    rows = db.list_custom_skills(t.company_id)
    assert [r["slug"] for r in rows] and rows[0]["slug"] == "zip-skill"
    full = db.get_custom_skill(t.company_id, "zip-skill")
    assert full["method"].startswith("# Estimation")
    assert full["modules"] == {"extra.md": "more"}


def test_upload_zip_without_form_fields_names_itself(tenant_client):
    # The modal hides name/description for archives — a zip names its skill
    # the way the multi path and the GitHub import do: frontmatter first.
    t = tenant_client.make(slug="acme")
    md = b"---\nname: ticket-breakdown\ndescription: Breaks work into tickets.\n---\n\nBody.\n"
    data = _zip_bytes({"SKILL.md": md})
    resp = _upload(t.client, name="", description="", filename="anything.zip", data=data)
    assert resp.status_code == 201, resp.text
    body = resp.json()
    assert body["name"] == "Ticket Breakdown"
    assert body["description"] == "Breaks work into tickets."
    assert body["trigger"] == "/ticket-breakdown"


def test_upload_zip_without_frontmatter_falls_back_to_the_zip_name(tenant_client):
    t = tenant_client.make(slug="acme")
    data = _zip_bytes({"SKILL.md": b"# Method\n\nScores features by reach.\n"})
    resp = _upload(t.client, name="", description="", filename="estimation-helper.zip", data=data)
    assert resp.status_code == 201, resp.text
    body = resp.json()
    assert body["name"] == "Estimation Helper"
    assert body["description"] == "Scores features by reach."


def test_upload_zip_with_form_fields_still_honours_them(tenant_client):
    # Backwards compatibility: a caller that DOES send the fields (an older
    # client, a script) keeps naming the skill itself.
    t = tenant_client.make(slug="acme")
    md = b"---\nname: from-frontmatter\ndescription: Content name.\n---\n\nBody.\n"
    data = _zip_bytes({"SKILL.md": md})
    resp = _upload(t.client, name="Typed Name", description="Typed description.",
                   filename="s.zip", data=data)
    assert resp.status_code == 201, resp.text
    assert resp.json()["name"] == "Typed Name"
    assert resp.json()["description"] == "Typed description."


def test_uploader_identity_comes_from_session(tenant_client):
    t = tenant_client.make(slug="acme", user_id="user-dana")
    resp = _upload(t.client)
    assert resp.status_code == 201

    from app import db

    row = db.get_custom_skill(t.company_id, "estimation-helper")
    assert row["uploader_id"] == "user-dana"
    # The uploading workspace is stamped even though scoping is company-level.
    assert row["workspace_id"]


# ─── validation ladder ───────────────────────────────────────────────────────


def test_missing_name_422(tenant_client):
    t = tenant_client.make(slug="acme")
    resp = _upload(t.client, name="   ")
    assert resp.status_code == 422
    assert "name" in resp.json()["detail"].lower()


def test_missing_description_422(tenant_client):
    t = tenant_client.make(slug="acme")
    resp = _upload(t.client, description="")
    assert resp.status_code == 422
    assert "description" in resp.json()["detail"].lower()


def test_over_limit_metadata_422(tenant_client):
    t = tenant_client.make(slug="acme")
    assert _upload(t.client, name="x" * 65).status_code == 422
    assert _upload(t.client, description="x" * 1025).status_code == 422


def test_unsupported_extension_422(tenant_client):
    t = tenant_client.make(slug="acme")
    resp = _upload(t.client, filename="skill.pdf", data=b"%PDF-1.4")
    assert resp.status_code == 422
    assert ".md" in resp.json()["detail"]


def test_uppercase_extension_accepted(tenant_client):
    t = tenant_client.make(slug="acme")
    assert _upload(t.client, filename="SKILL.MD").status_code == 201


def test_empty_file_400(tenant_client):
    t = tenant_client.make(slug="acme")
    assert _upload(t.client, data=b"").status_code == 400


def test_oversize_413_and_boundary_accepted(tenant_client, monkeypatch):
    import app.routes.custom_skills as mod
    from app.skills_storage import MAX_SKILL_UPLOAD_BYTES

    t = tenant_client.make(slug="acme")
    # Lift the CONTENT (character) cap so this test isolates the BYTE
    # boundary — 20 MB of markdown is far over the character cap, which has
    # its own boundary test below.
    monkeypatch.setattr(mod, "MAX_SKILL_CONTENT_CHARS", 30 * 1024 * 1024)
    # Exactly 20 MB is accepted (inclusive boundary, per the PRD edge case)…
    at_limit = b"a" * MAX_SKILL_UPLOAD_BYTES
    assert _upload(t.client, name="At Limit", data=at_limit).status_code == 201
    # …one byte over is rejected before processing.
    over = b"a" * (MAX_SKILL_UPLOAD_BYTES + 1)
    resp = _upload(t.client, name="Over Limit", data=over)
    assert resp.status_code == 413
    assert "20 MB" in resp.json()["detail"]


def test_content_cap_boundary_md(tenant_client):
    from app.skills.custom import MAX_SKILL_CONTENT_CHARS

    t = tenant_client.make(slug="acme")
    # Exactly at the cap is accepted (inclusive, like the byte boundary)…
    at_cap = b"a" * MAX_SKILL_CONTENT_CHARS
    assert _upload(t.client, name="At Cap", data=at_cap).status_code == 201
    assert len(_staged_files()) == 1
    # …one character over is rejected, before anything is staged.
    over = b"a" * (MAX_SKILL_CONTENT_CHARS + 1)
    resp = _upload(t.client, name="Over Cap", data=over)
    assert resp.status_code == 413
    assert f"{MAX_SKILL_CONTENT_CHARS:,} character" in resp.json()["detail"]
    assert len(_staged_files()) == 1


def test_content_cap_counts_every_zip_member(tenant_client):
    from app.skills.custom import MAX_SKILL_CONTENT_CHARS

    t = tenant_client.make(slug="acme")
    # Each member is under the cap; together they exceed it — the cap is on
    # the TOTAL parsed text, not the method file alone.
    half = b"a" * (MAX_SKILL_CONTENT_CHARS // 2 + 1)
    data = _zip_bytes({"SKILL.md": half, "modules/big.md": half})
    resp = _upload(t.client, name="Big Zip", filename="big.zip", data=data)
    assert resp.status_code == 413
    assert "character" in resp.json()["detail"]
    assert _staged_files() == []


def test_zip_without_md_400(tenant_client):
    t = tenant_client.make(slug="acme")
    resp = _upload(t.client, filename="s.zip", data=_zip_bytes({"a.txt": b"no md"}))
    assert resp.status_code == 400
    assert ".md" in resp.json()["detail"]


def test_empty_markdown_400(tenant_client):
    t = tenant_client.make(slug="acme")
    assert _upload(t.client, data=b"   \n").status_code == 400


def test_symbol_only_name_422(tenant_client):
    t = tenant_client.make(slug="acme")
    assert _upload(t.client, name="!!!").status_code == 422


# ─── name conflicts + rollback ───────────────────────────────────────────────


def test_builtin_name_taken_gets_its_own_trigger(tenant_client, monkeypatch):
    import app.routes.custom_skills as mod

    t = tenant_client.make(slug="acme")
    monkeypatch.setattr(mod, "list_skills", lambda: ["prd-author"])
    # Sharing a built-in's name is a normal upload and overrides NOTHING: the
    # built-in keeps /prd-author, so this one takes the next free trigger.
    resp = _upload(t.client, name="PRD Author")
    assert resp.status_code == 201, resp.text
    body = resp.json()
    assert body["name"] == "PRD Author"  # the typed name is never rewritten
    assert body["slug"] == "prd-author-2"
    assert body["trigger"] == "/prd-author-2"
    assert body["name_conflict"] is True
    assert len(_staged_files()) == 1

    # The list carries the flag too; skills with a free name stay False.
    assert _upload(t.client, name="Own Thing").status_code == 201
    skills = {s["slug"]: s for s in t.client.get("/v1/skills").json()["skills"]}
    assert skills["prd-author-2"]["name_conflict"] is True
    assert skills["own-thing"]["name_conflict"] is False


def test_trigger_series_skips_slugs_already_handed_out(tenant_client, monkeypatch):
    import app.routes.custom_skills as mod

    t = tenant_client.make(slug="acme")
    monkeypatch.setattr(mod, "list_skills", lambda: ["prd-author"])
    # A skill legitimately named "PRD Author 2" occupies /prd-author-2 …
    assert _upload(t.client, name="PRD Author 2").json()["slug"] == "prd-author-2"
    # … so the one colliding with the built-in has to skip past it.
    assert _upload(t.client, name="PRD Author").json()["slug"] == "prd-author-3"


def test_reupload_replaces_the_companys_own_skill_in_place(tenant_client):
    from app import db

    t = tenant_client.make(slug="acme")
    first = _upload(t.client).json()
    assert len(_staged_files()) == 1

    # A repeat of the company's OWN skill name is a NEW VERSION of it: the row
    # is updated in place, so the id and the trigger the team already knows
    # both survive.
    resp = _upload(
        t.client, description="Scores features, v2", data=b"# Estimation v2\nNew method.\n"
    )
    assert resp.status_code == 201, resp.text
    body = resp.json()
    assert body["replaced"] is True
    assert body["id"] == first["id"]
    assert body["slug"] == first["slug"] == "estimation-helper"
    assert body["trigger"] == "/estimation-helper"
    assert body["description"] == "Scores features, v2"

    # One library entry, not two — and it serves the NEW content under the
    # same slug, which is what /estimation-helper now resolves to.
    listed = t.client.get("/v1/skills").json()["skills"]
    assert [s["id"] for s in listed] == [first["id"]]
    full = db.get_custom_skill(t.company_id, "estimation-helper")
    assert full["method"] == "# Estimation v2\nNew method.\n"

    # The superseded original file is gone; only the new version's is staged.
    assert len(_staged_files()) == 1


def test_reupload_refreshes_the_content_hash(tenant_client):
    from app import db

    t = tenant_client.make(slug="acme")
    _upload(t.client)
    before = db.get_custom_skill(t.company_id, "estimation-helper")["content_hash"]

    _upload(t.client, data=b"# Estimation\nCompletely different method.\n")
    after = db.get_custom_skill(t.company_id, "estimation-helper")["content_hash"]
    # prompt_version carries this hash, so a stale one would misreport which
    # method text actually answered.
    assert after and after != before


def test_reupload_replaces_the_content_it_does_not_merge_it(tenant_client):
    from app import db

    t = tenant_client.make(slug="acme")
    zipped = _zip_bytes({"SKILL.md": _MD, "modules/extra.md": b"old module"})
    assert _upload(t.client, filename="s.zip", data=zipped).status_code == 201
    assert db.get_custom_skill(t.company_id, "estimation-helper")["modules"] == {
        "extra.md": "old module"
    }

    # Re-uploading a bare .md is a full replacement: the module the old bundle
    # carried is gone, not merged forward into the new version's prompt.
    assert _upload(t.client, data=b"# just the method\n").json()["replaced"] is True
    row = db.get_custom_skill(t.company_id, "estimation-helper")
    assert row["method"] == "# just the method\n"
    assert row["modules"] == {}


def test_reupload_keeps_the_skills_place_in_the_library(tenant_client):
    t = tenant_client.make(slug="acme")
    assert _upload(t.client, name="First Skill").status_code == 201
    assert _upload(t.client, name="Second Skill").status_code == 201

    # Replacing the older skill refreshes its content, not its created_at —
    # the library must not reshuffle because someone updated a skill's text.
    assert _upload(t.client, name="First Skill", data=b"# v2\n").json()["replaced"] is True
    listed = t.client.get("/v1/skills").json()["skills"]
    assert [s["slug"] for s in listed] == ["second-skill", "first-skill"]


def test_reupload_matches_on_name_not_stored_slug(tenant_client, monkeypatch):
    import app.routes.custom_skills as mod

    t = tenant_client.make(slug="acme")
    monkeypatch.setattr(mod, "list_skills", lambda: ["prd-author"])
    # This one is stored under /prd-author-2 (the built-in owns /prd-author),
    # so the match has to compare NAMES — matching on the stored slug would
    # miss it and create a second entry.
    first = _upload(t.client, name="PRD Author").json()
    assert first["slug"] == "prd-author-2"

    resp = _upload(t.client, name="PRD  author!")  # same name once slugified
    assert resp.status_code == 201, resp.text
    body = resp.json()
    assert body["replaced"] is True
    assert body["id"] == first["id"]
    assert body["slug"] == "prd-author-2"  # the disambiguated trigger survives
    assert body["name"] == "PRD  author!"  # the newly typed display name wins
    assert body["name_conflict"] is True
    assert len(_staged_files()) == 1


def test_reupload_of_a_builtins_name_still_deconflicts_the_trigger(tenant_client, monkeypatch):
    import app.routes.custom_skills as mod

    t = tenant_client.make(slug="acme")
    monkeypatch.setattr(mod, "list_skills", lambda: ["prd-author"])
    # Replacement is CUSTOM-vs-CUSTOM only. A built-in is never replaced: the
    # first upload of its name takes /prd-author-2, and after that name is in
    # the company's library a second upload replaces THAT row — never the
    # vendored skill, whose trigger stays free of both.
    assert _upload(t.client, name="PRD Author").json()["slug"] == "prd-author-2"
    again = _upload(t.client, name="PRD Author", data=b"# ours v2\n").json()
    assert again["replaced"] is True
    assert again["slug"] == "prd-author-2"
    assert [s["slug"] for s in t.client.get("/v1/skills").json()["skills"]] == ["prd-author-2"]


def test_reupload_never_reaches_another_companys_skill(tenant_client):
    from app import db

    a = tenant_client.make(slug="acme")
    b = tenant_client.make(slug="globex")
    theirs = _upload(a.client).json()
    # Globex uploading the SAME name creates its own skill — it must not find,
    # replace, or even observe Acme's row (custom skills are company-scoped and
    # the library read that drives the match is company-filtered).
    ours = _upload(b.client, data=b"# Globex method\n").json()
    assert ours["replaced"] is False
    assert ours["id"] != theirs["id"]
    assert ours["slug"] == theirs["slug"] == "estimation-helper"

    # Acme's content and metadata are untouched.
    a_row = db.get_custom_skill(a.company_id, "estimation-helper")
    assert a_row["id"] == theirs["id"]
    assert a_row["method"] == _MD.decode()
    assert db.get_custom_skill(b.company_id, "estimation-helper")["method"] == "# Globex method\n"
    # Both originals are still staged — neither delete crossed the boundary.
    assert len(_staged_files()) == 2


def test_same_slug_allowed_across_companies(tenant_client):
    a = tenant_client.make(slug="acme")
    b = tenant_client.make(slug="globex")
    assert _upload(a.client).status_code == 201
    assert _upload(b.client).status_code == 201  # unique is per-company


# ─── list + isolation ────────────────────────────────────────────────────────


def test_list_returns_company_skills_newest_first(tenant_client):
    t = tenant_client.make(slug="acme")
    assert _upload(t.client, name="First Skill").status_code == 201
    assert _upload(t.client, name="Second Skill").status_code == 201

    resp = t.client.get("/v1/skills")
    assert resp.status_code == 200
    skills = resp.json()["skills"]
    assert [s["slug"] for s in skills] == ["second-skill", "first-skill"]
    # Metadata only — the method text is not shipped to the library list.
    assert "method" not in skills[0]


def test_list_is_company_isolated(tenant_client):
    a = tenant_client.make(slug="acme")
    b = tenant_client.make(slug="globex")
    assert _upload(a.client).status_code == 201

    assert b.client.get("/v1/skills").json()["skills"] == []


# ─── detail (the edit form's source) ─────────────────────────────────────────


def test_detail_returns_the_method_text(tenant_client):
    t = tenant_client.make(slug="acme")
    skill_id = _upload(t.client).json()["id"]

    resp = t.client.get(f"/v1/skills/{skill_id}")
    assert resp.status_code == 200, resp.text
    body = resp.json()
    # Everything the list carries, plus the text the edit form pre-fills with.
    assert body["id"] == skill_id
    assert body["slug"] == "estimation-helper"
    assert body["name"] == "Estimation Helper"
    assert body["method"] == _MD.decode()
    assert body["modules"] == []
    assert body["references"] == []
    assert body["attached_chars"] == 0


def test_detail_names_attached_files_without_shipping_their_text(tenant_client):
    t = tenant_client.make(slug="acme")
    data = _zip_bytes({
        "SKILL.md": _MD,
        "modules/extra.md": b"more",
        "references/src.md": b"cited",
    })
    skill_id = _upload(t.client, filename="s.zip", data=data).json()["id"]

    body = t.client.get(f"/v1/skills/{skill_id}").json()
    # Filenames only — the form edits the method, so the supporting files are
    # something to REPORT ("these stay attached"), not something to ship.
    assert body["modules"] == ["extra.md"]
    assert body["references"] == ["src.md"]
    assert "more" not in str(body["modules"])
    # …but their size is reported, so the client can mirror the content cap
    # (which is on the TOTAL parsed text, not the method alone).
    assert body["attached_chars"] == len("more") + len("cited")


def test_detail_foreign_id_404(tenant_client):
    a = tenant_client.make(slug="acme")
    b = tenant_client.make(slug="globex")
    skill_id = _upload(a.client).json()["id"]

    assert b.client.get(f"/v1/skills/{skill_id}").status_code == 404


def test_detail_unknown_id_404(tenant_client):
    t = tenant_client.make(slug="acme")
    assert t.client.get("/v1/skills/not-a-real-id").status_code == 404


# ─── edit in place ───────────────────────────────────────────────────────────


def test_edit_updates_all_three_fields_on_the_same_row(tenant_client):
    from app import db

    t = tenant_client.make(slug="acme")
    first = _upload(t.client).json()

    resp = _edit(
        t.client,
        first["id"],
        name="Sizing Guide",
        description="Sizes work against our template",
        method="# Sizing\nEdited by hand.\n",
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    # Same row — an edit is not a delete-and-recreate, so links, history and
    # the card's place in the library all survive.
    assert body["id"] == first["id"]
    assert body["name"] == "Sizing Guide"
    assert body["description"] == "Sizes work against our template"
    assert body["method"] == "# Sizing\nEdited by hand.\n"
    assert body["replaced_skill_id"] is None

    listed = t.client.get("/v1/skills").json()["skills"]
    assert [s["id"] for s in listed] == [first["id"]]
    assert db.get_custom_skill(t.company_id, "sizing-guide")["method"] == (
        "# Sizing\nEdited by hand.\n"
    )


def test_edit_without_a_rename_leaves_the_trigger_alone(tenant_client, monkeypatch):
    import app.routes.custom_skills as mod

    t = tenant_client.make(slug="acme")
    monkeypatch.setattr(mod, "list_skills", lambda: ["prd-author"])
    # This skill was handed /prd-author-2 because the built-in owns the plain
    # slug. Fixing its description must not move it — the trigger a team has
    # learned only changes when the NAME changes.
    first = _upload(t.client, name="PRD Author").json()
    assert first["slug"] == "prd-author-2"

    body = _edit(t.client, first["id"], name="PRD Author", description="Ours, v2").json()
    assert body["slug"] == "prd-author-2"
    assert body["trigger"] == "/prd-author-2"
    assert body["description"] == "Ours, v2"
    assert body["name_conflict"] is True


def test_edit_renaming_rederives_the_trigger(tenant_client):
    from app import db

    t = tenant_client.make(slug="acme")
    first = _upload(t.client).json()
    assert first["slug"] == "estimation-helper"

    body = _edit(t.client, first["id"], name="Sizing Guide").json()
    assert body["slug"] == "sizing-guide"
    assert body["trigger"] == "/sizing-guide"
    # The old trigger stops resolving — accepted consequence of a rename.
    assert db.get_custom_skill(t.company_id, "estimation-helper") is None
    assert db.get_custom_skill(t.company_id, "sizing-guide")["id"] == first["id"]


def test_edit_renaming_onto_a_builtin_takes_the_next_free_trigger(tenant_client, monkeypatch):
    import app.routes.custom_skills as mod
    from app import db

    t = tenant_client.make(slug="acme")
    monkeypatch.setattr(mod, "list_skills", lambda: ["prd-author"])
    first = _upload(t.client).json()

    # Renaming a skill to a BUILT-IN's name goes through exactly the same
    # deconfliction an upload does (2026-07-30: a custom skill never overrides
    # a built-in), so it lands on the `-2` series.
    body = _edit(t.client, first["id"], name="PRD Author").json()
    assert body["slug"] == "prd-author-2"
    assert body["name"] == "PRD Author"
    assert body["name_conflict"] is True
    # The vendored trigger is untouched: nothing custom now answers to it.
    assert db.get_custom_skill(t.company_id, "prd-author") is None


def test_edit_renaming_skips_a_trigger_already_handed_out(tenant_client, monkeypatch):
    import app.routes.custom_skills as mod

    t = tenant_client.make(slug="acme")
    monkeypatch.setattr(mod, "list_skills", lambda: ["prd-author"])
    assert _upload(t.client, name="PRD Author 2").json()["slug"] == "prd-author-2"
    mine = _upload(t.client, name="Own Thing").json()

    # /prd-author is the built-in's and /prd-author-2 is already a sibling's,
    # so the rename has to skip both. (Different NAMES — "PRD Author 2" does
    # not slugify to "prd-author" — so this is a trigger clash, not a replace.)
    assert _edit(t.client, mine["id"], name="PRD Author").json()["slug"] == "prd-author-3"


def test_edit_renaming_onto_our_own_skill_replaces_it(tenant_client):
    from app import db

    t = tenant_client.make(slug="acme")
    victim = _upload(t.client, name="Journey Mapper", description="Maps journeys").json()
    mine = _upload(t.client, name="Estimation Helper").json()
    assert len(_staged_files()) == 2

    # Renaming onto a name the company already uses REPLACES that skill: the
    # edited row survives (same id) under the new name and takes over its
    # trigger; the other row is gone. The UI confirms this before sending.
    resp = _edit(t.client, mine["id"], name="Journey Mapper")
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["id"] == mine["id"]
    assert body["name"] == "Journey Mapper"
    assert body["slug"] == "journey-mapper"
    assert body["replaced_skill_id"] == victim["id"]

    listed = t.client.get("/v1/skills").json()["skills"]
    assert [s["id"] for s in listed] == [mine["id"]]
    assert db.get_custom_skill(t.company_id, "estimation-helper") is None
    assert db.get_custom_skill(t.company_id, "journey-mapper")["id"] == mine["id"]
    # Both originals are cleaned up: the replaced skill's (it has no row now)
    # and the edited skill's (its text is no longer what the file holds).
    assert _staged_files() == []


def test_edit_replacement_matches_the_name_not_the_stored_slug(tenant_client, monkeypatch):
    import app.routes.custom_skills as mod

    t = tenant_client.make(slug="acme")
    monkeypatch.setattr(mod, "list_skills", lambda: ["prd-author"])
    # Stored at /prd-author-2 because the built-in owns the plain slug…
    victim = _upload(t.client, name="PRD Author").json()
    assert victim["slug"] == "prd-author-2"
    mine = _upload(t.client, name="Own Thing").json()

    # …so matching on the stored slug would miss it and leave two skills named
    # "PRD Author". Matching is on slugify(name), like the re-upload replace.
    body = _edit(t.client, mine["id"], name="PRD  author!").json()
    assert body["replaced_skill_id"] == victim["id"]
    assert body["id"] == mine["id"]
    assert body["name"] == "PRD  author!"
    # It takes over the freed trigger rather than inventing /prd-author-3.
    assert body["slug"] == "prd-author-2"
    assert [s["id"] for s in t.client.get("/v1/skills").json()["skills"]] == [mine["id"]]


def test_edit_never_reaches_another_companys_skill(tenant_client):
    from app import db

    a = tenant_client.make(slug="acme")
    b = tenant_client.make(slug="globex")
    theirs = _upload(a.client).json()
    ours = _upload(b.client, name="Own Thing", data=b"# Globex method\n").json()

    # Globex renaming its skill to a name ACME uses is not a replacement of
    # anything: the library read behind the match is company-filtered, so
    # Acme's row is never a candidate.
    body = _edit(b.client, ours["id"], name="Estimation Helper").json()
    assert body["replaced_skill_id"] is None
    assert body["slug"] == "estimation-helper"  # the same slug, in another company

    a_row = db.get_custom_skill(a.company_id, "estimation-helper")
    assert a_row["id"] == theirs["id"]
    assert a_row["method"] == _MD.decode()
    assert [s["id"] for s in a.client.get("/v1/skills").json()["skills"]] == [theirs["id"]]


def test_edit_foreign_id_404_and_leaves_the_skill_untouched(tenant_client):
    from app import db

    a = tenant_client.make(slug="acme")
    b = tenant_client.make(slug="globex")
    skill_id = _upload(a.client).json()["id"]

    # 404, never 403 — a foreign tenant must not learn the id exists.
    assert _edit(b.client, skill_id, name="Hijacked").status_code == 404
    row = db.get_custom_skill(a.company_id, "estimation-helper")
    assert row["id"] == skill_id
    assert row["name"] == "Estimation Helper"
    assert row["method"] == _MD.decode()
    assert len(_staged_files()) == 1


def test_edit_unknown_id_404(tenant_client):
    t = tenant_client.make(slug="acme")
    assert _edit(t.client, "not-a-real-id").status_code == 404


def test_edit_keeps_zip_modules_and_refreshes_the_content_hash(tenant_client):
    from app import db

    t = tenant_client.make(slug="acme")
    data = _zip_bytes({"SKILL.md": _MD, "modules/extra.md": b"more"})
    first = _upload(t.client, filename="s.zip", data=data).json()
    before = db.get_custom_skill(t.company_id, "estimation-helper")["content_hash"]

    # Editing the method swaps the MAIN text only — the archive's supporting
    # files are not something the form shows, so it must not silently drop them
    # (the re-upload path replaces wholesale; this one does not).
    assert _edit(t.client, first["id"], method="# Estimation\nBy hand.\n").status_code == 200
    row = db.get_custom_skill(t.company_id, "estimation-helper")
    assert row["method"] == "# Estimation\nBy hand.\n"
    assert row["modules"] == {"extra.md": "more"}
    # content_hash is content-derived, so it moves — prompt_version carries it,
    # and a stale hash would misreport which method text actually answered.
    assert row["content_hash"] and row["content_hash"] != before


def test_edit_drops_the_stored_original(tenant_client):
    t = tenant_client.make(slug="acme")
    first = _upload(t.client).json()
    assert first["has_file"] is True
    assert len(_staged_files()) == 1

    body = _edit(t.client, first["id"], method="# Nothing like the file\n").json()
    # The uploaded bytes no longer describe this skill, so the row stops
    # pointing at them and the object is dropped rather than served as a
    # download that contradicts the method.
    assert body["has_file"] is False
    assert _staged_files() == []
    assert t.client.get(f"/v1/skills/{first['id']}/file").status_code == 404
    # The skill itself is fine — only its downloadable original is gone.
    assert t.client.get(f"/v1/skills/{first['id']}").json()["method"] == (
        "# Nothing like the file\n"
    )


def test_edit_keeps_the_skills_place_in_the_library(tenant_client):
    t = tenant_client.make(slug="acme")
    first = _upload(t.client, name="First Skill").json()
    assert _upload(t.client, name="Second Skill").status_code == 201

    # Editing refreshes content, not created_at: the library must not reshuffle
    # because someone fixed a typo (same rule the re-upload path follows).
    assert _edit(t.client, first["id"], name="First Skill", method="# v2\n").status_code == 200
    listed = t.client.get("/v1/skills").json()["skills"]
    assert [s["slug"] for s in listed] == ["second-skill", "first-skill"]


# ─── edit: validation ladder ─────────────────────────────────────────────────


def test_edit_missing_name_422(tenant_client):
    t = tenant_client.make(slug="acme")
    skill_id = _upload(t.client).json()["id"]
    resp = _edit(t.client, skill_id, name="   ")
    assert resp.status_code == 422
    assert "name" in resp.json()["detail"].lower()


def test_edit_missing_description_422(tenant_client):
    t = tenant_client.make(slug="acme")
    skill_id = _upload(t.client).json()["id"]
    resp = _edit(t.client, skill_id, description="")
    assert resp.status_code == 422
    assert "description" in resp.json()["detail"].lower()


def test_edit_over_limit_metadata_422(tenant_client):
    t = tenant_client.make(slug="acme")
    skill_id = _upload(t.client).json()["id"]
    assert _edit(t.client, skill_id, name="x" * 65).status_code == 422
    assert _edit(t.client, skill_id, description="x" * 1025).status_code == 422


def test_edit_symbol_only_name_422(tenant_client):
    t = tenant_client.make(slug="acme")
    skill_id = _upload(t.client).json()["id"]
    assert _edit(t.client, skill_id, name="!!!").status_code == 422


def test_edit_empty_method_400(tenant_client):
    from app import db

    t = tenant_client.make(slug="acme")
    skill_id = _upload(t.client).json()["id"]
    resp = _edit(t.client, skill_id, method="   \n")
    assert resp.status_code == 400
    assert "method" in resp.json()["detail"].lower()
    # Nothing was written — a rejected edit leaves the skill exactly as it was.
    assert db.get_custom_skill(t.company_id, "estimation-helper")["method"] == _MD.decode()


def test_edit_over_the_content_cap_413(tenant_client):
    from app import db
    from app.skills.custom import MAX_SKILL_CONTENT_CHARS

    t = tenant_client.make(slug="acme")
    skill_id = _upload(t.client).json()["id"]

    # Exactly at the cap is accepted (inclusive, like upload's boundary)…
    assert _edit(t.client, skill_id, method="a" * MAX_SKILL_CONTENT_CHARS).status_code == 200
    # …one character over is rejected, with upload's message verbatim.
    resp = _edit(t.client, skill_id, method="a" * (MAX_SKILL_CONTENT_CHARS + 1))
    assert resp.status_code == 413
    assert f"{MAX_SKILL_CONTENT_CHARS:,} character" in resp.json()["detail"]
    # The over-cap text never landed.
    assert len(db.get_custom_skill(t.company_id, "estimation-helper")["method"]) == (
        MAX_SKILL_CONTENT_CHARS
    )


def test_edit_content_cap_counts_the_attached_files_too(tenant_client):
    from app.skills.custom import MAX_SKILL_CONTENT_CHARS

    t = tenant_client.make(slug="acme")
    half = b"a" * (MAX_SKILL_CONTENT_CHARS // 2)
    data = _zip_bytes({"SKILL.md": b"# small\n", "modules/big.md": half})
    skill_id = _upload(t.client, filename="s.zip", data=data).json()["id"]

    # The module the archive carried still counts against the cap, because it
    # is still part of the prompt this skill injects.
    resp = _edit(t.client, skill_id, method="a" * (MAX_SKILL_CONTENT_CHARS // 2 + 1))
    assert resp.status_code == 413
    assert "character" in resp.json()["detail"]


# ─── original-file links ─────────────────────────────────────────────────────


def test_file_links_for_owned_skill(tenant_client):
    t = tenant_client.make(slug="acme")
    skill_id = _upload(t.client).json()["id"]

    resp = t.client.get(f"/v1/skills/{skill_id}/file")
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["name"] == "estimation-helper.md"
    # Filesystem fallback in tests → stable file:// URLs.
    assert body["view_url"].startswith("file://")
    assert body["download_url"]


def test_file_links_foreign_id_404(tenant_client):
    a = tenant_client.make(slug="acme")
    b = tenant_client.make(slug="globex")
    skill_id = _upload(a.client).json()["id"]

    assert b.client.get(f"/v1/skills/{skill_id}/file").status_code == 404


def test_file_links_unknown_id_404(tenant_client):
    t = tenant_client.make(slug="acme")
    assert t.client.get("/v1/skills/not-a-real-id/file").status_code == 404


# ─── delete ──────────────────────────────────────────────────────────────────


def test_delete_removes_row_and_staged_file(tenant_client):
    t = tenant_client.make(slug="acme")
    skill_id = _upload(t.client).json()["id"]
    assert len(_staged_files()) == 1

    resp = t.client.delete(f"/v1/skills/{skill_id}")
    assert resp.status_code == 200, resp.text
    assert resp.json() == {"deleted": True, "id": skill_id}
    assert t.client.get("/v1/skills").json()["skills"] == []
    # The staged original is cleaned up with the row.
    assert _staged_files() == []


def test_delete_frees_slug_for_reupload(tenant_client):
    t = tenant_client.make(slug="acme")
    skill_id = _upload(t.client).json()["id"]

    assert t.client.delete(f"/v1/skills/{skill_id}").status_code == 200
    # Same name again → no duplicate-slug 409 once the row is gone.
    assert _upload(t.client).status_code == 201


def test_delete_foreign_id_404_and_keeps_skill(tenant_client):
    a = tenant_client.make(slug="acme")
    b = tenant_client.make(slug="globex")
    skill_id = _upload(a.client).json()["id"]

    assert b.client.delete(f"/v1/skills/{skill_id}").status_code == 404
    # The owner's skill and its staged file are untouched.
    assert [s["id"] for s in a.client.get("/v1/skills").json()["skills"]] == [skill_id]
    assert len(_staged_files()) == 1


def test_delete_unknown_id_404(tenant_client):
    t = tenant_client.make(slug="acme")
    assert t.client.delete("/v1/skills/not-a-real-id").status_code == 404
