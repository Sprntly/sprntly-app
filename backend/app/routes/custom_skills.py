"""Custom skill endpoints — upload, list, edit, and original-file links (PRD 1854).

  POST   /v1/skills               -> upload a .md/.zip skill with name+description
  GET    /v1/skills               -> list the company's custom skills (metadata)
  GET    /v1/skills/{id}          -> one skill WITH its method text (the edit form's source)
  PATCH  /v1/skills/{id}          -> edit name/description/method in place
  GET    /v1/skills/{id}/file     -> signed view/download URLs for the original upload
  DELETE /v1/skills/{id}          -> delete a skill (row + original file)

Custom skills are COMPANY-SCOPED for now — all workspaces in a company share
one skill library, so reads filter by company_id. The uploading workspace is
still stamped on the row for a future move to workspace scoping.

Uploads store the PARSED markdown in the custom_skills table (the prompt
payload the invocation path reads) and the ORIGINAL bytes in Supabase Storage
under custom-skills/{company_id}/ (skills_storage.py). Validation here is
the server-side gate — the Skills screen mirrors it client-side, but every
check must hold against direct API calls.

A .zip carrying MORE THAN ONE SKILL.md is imported as more than one skill —
one row, one trigger and one stored file each — rather than being flattened
into a single row that silently loses everything after the first skill. That
archive is what a zipped `skills/` directory is, so it is the shape people
actually have. The form's name/description cannot name N skills, so a
multi-skill import names each one from its own SKILL.md frontmatter and the
201 answers with `{skills: [...], skipped: [...]}` instead of the single
object. A skill folder we can't name, can't describe, or can't fit under the
character cap lands in `skipped` with a reason and costs the others nothing;
importing NOTHING is the only case that fails the request (400).

Error ladder (mirrors the attachments/dataset upload guards): missing or
over-limit name/description → 422, unsupported extension → 422, empty file →
400, oversize → 413, unparseable content → 400, over-limit parsed content
(characters) → 413.

Sharing a BUILT-IN skill's name is allowed and does NOT override it: both
skills stay in the library and both stay invocable, so the upload takes the
next free trigger in the `<slug>`, `<slug>-2`, `<slug>-3` series
(skills.custom.available_slug). The display name is whatever the user typed;
only the trigger is disambiguated, and the 201/list payloads carry
`name_conflict` so the UI can say "the name was taken — here's your trigger".

Re-using one of the COMPANY'S OWN skill names REPLACES that skill (product
decision 2026-08-03, superseding the 409 this used to return): a re-upload is
how you ship a new version of your own method, and refusing it forced people
to delete-then-upload, which changed the trigger their team had learned and
dropped the skill's history. The matching row is updated in place — same id,
same `slug`/trigger, same position in the library — with the new content,
content_hash, description, and uploader; the previous original file is removed
from storage once the row points at the new one. "Same name" is the same
equivalence the 409 used: `slugify(name)`, so "PRD  author!" replaces
"PRD Author". Matching is company-scoped, so another tenant's identically
named skill is never touched. The 201 body carries `replaced` so the UI can
say "updated" rather than "uploaded".

EDITING a skill in place (PATCH) is the same product decision without the file:
a typo in a method doc should not need a re-export from whatever tool wrote it.
name, description and method are all editable; the modules and references a
.zip carried are preserved untouched, because the form only edits the main
method text. Two consequences the edit path owns and the upload path does not:

  - Renaming RE-DERIVES the trigger through the same `available_slug`
    deconfliction, so renaming a skill to a built-in's name lands on the `-2`
    series and still never overrides the built-in. The old `/slug` stops
    resolving — accepted, because a skill's trigger is derived from its name
    and a rename that kept the old one would be the more surprising outcome.
  - Renaming onto ANOTHER of the company's skills REPLACES that skill: the
    edited row survives (same id) with the new name and the other row is
    deleted, its original file cleaned up best-effort exactly as DELETE does.
    That is destructive, so the Skills screen makes the user confirm it before
    the PATCH is sent; the API itself is permissive, mirroring the re-upload
    replace it is the sibling of.
"""
from __future__ import annotations

import logging

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile
from pydantic import BaseModel

from app import db, skills_storage
from app.auth import WorkspaceContext, require_workspace
from app.design_agent.csrf import require_same_origin  # server-side CSRF/Origin gate
from app.skills.custom import (
    MAX_DESCRIPTION_CHARS,
    MAX_NAME_CHARS,
    MAX_SKILL_CONTENT_CHARS,
    MultiSkillArchive,
    ParsedSkill,
    SkillParseError,
    available_slug,
    build_skill_archive,
    content_chars,
    content_hash_for,
    parse_multi_upload,
    parse_upload,
    slugify,
)
from app.skills.loader import list_skills
from app.skills.store import (
    SkillContentTooLarge,
    SkillNameUnusable,
    SkillSlugTaken,
    SkillStoreError,
    store_skill,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/v1/skills", tags=["skills"])


class SkillEditIn(BaseModel):
    """PATCH body — the three fields the Skills screen's edit form owns.

    All three are required rather than an optional patch set: the form always
    submits the complete trio it rendered, and a partial write would let a
    stale form silently revert a field. The defaults are empty strings so a
    missing key lands on this route's own 422/400 ladder — the same messages
    upload returns — instead of a pydantic validation blob the modal can't
    render."""

    name: str = ""
    description: str = ""
    method: str = ""


def _skill_payload(row: dict, *, replaced: bool | None = None) -> dict:
    """Row → API shape shared by POST (201 body) and GET (list items).

    `replaced` is POST-only: True when this upload updated a skill the company
    already had under the same name rather than creating a new one. Omitted
    from the list payload, where it would mean nothing.

    `name_conflict`: this skill's name was already taken when it was uploaded,
    so its trigger was disambiguated away from the name's plain slug. Derived
    rather than stored — the stored slug differing from slugify(name) IS the
    record of the collision, and it stays correct however the built-in library
    changes afterwards."""
    payload = {
        "id": row["id"],
        "slug": row["slug"],
        "trigger": f"/{row['slug']}",
        "name": row["name"],
        "description": row["description"],
        "uploader_name": row.get("uploader_name") or "",
        "created_at": row.get("created_at"),
        "has_file": bool(row.get("storage_key")),
        "name_conflict": row["slug"] != slugify(row["name"]),
    }
    if replaced is not None:
        payload["replaced"] = replaced
    return payload


def _skill_detail(row: dict) -> dict:
    """The list payload PLUS the method text — what the edit form reads.

    `modules`/`references` are FILENAMES only, not their markdown: the form
    edits the main method and leaves the archive's supporting files alone, so
    the client needs to say "3 supporting files stay attached" and nothing
    more. `attached_chars` is how many characters those files contribute, so
    the modal can mirror MAX_SKILL_CONTENT_CHARS exactly (the cap is on the
    TOTAL parsed text, and a bare method-length check would be wrong for a
    skill uploaded as a .zip)."""
    modules = dict(row.get("modules") or {})
    references = dict(row.get("references") or {})
    return {
        **_skill_payload(row),
        "method": row.get("method") or "",
        "modules": sorted(modules),
        "references": sorted(references),
        "attached_chars": sum(len(t) for t in modules.values())
        + sum(len(t) for t in references.values()),
    }


@router.post(
    "",
    status_code=201,
    dependencies=[Depends(require_same_origin)],  # CSRF/Origin gate (authed mutating)
)
async def upload_skill(
    file: UploadFile = File(...),
    name: str = Form(""),
    description: str = Form(""),
    company: WorkspaceContext = Depends(require_workspace),
):
    """Create a custom skill from an uploaded .md or .zip (≤ 20 MB), or
    replace the company's existing skill of the same name with this version.

    Both outcomes answer 201 with the same body plus `replaced`; a replace
    keeps the skill's id and trigger, so nothing a team has learned or linked
    to changes underneath them.

    A .zip holding SEVERAL skills is imported as several skills — see
    `_upload_multi` for that body and why the form's name/description stop
    applying there."""
    name = (name or "").strip()
    description = (description or "").strip()
    if not name:
        raise HTTPException(422, "Skill name is required.")
    if not description:
        raise HTTPException(422, "Skill description is required.")
    if len(name) > MAX_NAME_CHARS:
        raise HTTPException(422, f"Skill name must be {MAX_NAME_CHARS} characters or fewer.")
    if len(description) > MAX_DESCRIPTION_CHARS:
        raise HTTPException(
            422, f"Skill description must be {MAX_DESCRIPTION_CHARS} characters or fewer."
        )

    ext = skills_storage.ext_of(file.filename or "")
    if not skills_storage.is_supported_ext(ext):
        raise HTTPException(
            422, "Only .md files and .zip archives are accepted. "
                 "Please try again with the correct format."
        )
    data = await file.read()
    if not data:
        raise HTTPException(400, "Uploaded file is empty.")
    if len(data) > skills_storage.MAX_SKILL_UPLOAD_BYTES:
        raise HTTPException(413, "File size exceeds the 20 MB limit. Please upload a smaller file.")

    filename = file.filename or f"skill.{ext}"
    # An archive holding more than one SKILL.md is N skills, not one — and it
    # is parsed and stored down its own path, because none of the single-skill
    # contract (the form's name, one row, one 201 object) survives contact with
    # it. Everything below this branch is the original one-skill path.
    try:
        multi = parse_multi_upload(filename, data)
    except SkillParseError as e:
        raise HTTPException(400, str(e))
    if multi is not None:
        return await _upload_multi(multi, company=company)

    try:
        parsed = parse_upload(filename, data)
    except SkillParseError as e:
        raise HTTPException(400, str(e))

    try:
        stored = await store_skill(
            company_id=company.company_id,
            workspace_id=company.workspace_id,
            uploader_id=company.user_id,
            uploader_name=company.user_name or company.user_email or "",
            name=name,
            description=description,
            parsed=parsed,
            data=data,
            ext=ext,
            # Resolved HERE, from this module's globals, so the whole ladder
            # (and the suite's monkeypatches of both) stays owned by the route.
            builtin_slugs=set(list_skills()),
            max_content_chars=MAX_SKILL_CONTENT_CHARS,
        )
    except SkillStoreError as e:
        raise HTTPException(_store_error_status(e), str(e))
    return _skill_payload(stored.row, replaced=stored.replaced)


def _store_error_status(exc: SkillStoreError) -> int:
    """The status each store refusal has always answered with, kept in one
    place now that two callers raise them."""
    if isinstance(exc, SkillContentTooLarge):
        return 413
    if isinstance(exc, SkillNameUnusable):
        return 422
    if isinstance(exc, SkillSlugTaken):
        return 409
    return 400


async def _upload_multi(
    multi: MultiSkillArchive, *, company: WorkspaceContext
) -> dict:
    """Import every skill an archive holds, one row and one stored file each.

    The form's name and description do not apply here — they can name one
    skill, and this archive holds several — so each skill was named from its
    own SKILL.md frontmatter during parsing. The body says so by shape: a list
    under `skills` instead of the single object, so a client can tell the two
    apart without a flag.

    Per-skill failures are RECORDED, not raised. A 12-skill export where one
    method blew past the character cap should import eleven skills and say
    which one it couldn't; failing the request would leave the user with
    nothing to show for a valid archive. `skipped` carries the folder, the
    name we managed to derive, and a reason written for a person. The one case
    that IS an error is importing nothing at all — there is no 201 to give.

    Each skill is stored with a standalone copy of itself (build_skill_archive)
    rather than the uploaded bundle: rows own their storage object one-to-one,
    so sharing the bundle would make the first DELETE strip the file out from
    under every other row it created.
    """
    created: list[dict] = []
    skipped: list[dict] = [
        {"path": s.path, "name": s.name, "reason": s.reason} for s in multi.skipped
    ]
    for skill in multi.skills:
        data, ext = build_skill_archive(skill.parsed)
        try:
            stored = await store_skill(
                company_id=company.company_id,
                workspace_id=company.workspace_id,
                uploader_id=company.user_id,
                uploader_name=company.user_name or company.user_email or "",
                name=skill.name,
                description=skill.description,
                parsed=skill.parsed,
                data=data,
                ext=ext,
                builtin_slugs=set(list_skills()),
                max_content_chars=MAX_SKILL_CONTENT_CHARS,
            )
        except SkillStoreError as e:
            skipped.append({"path": skill.path, "name": skill.name, "reason": str(e)})
            continue
        created.append(_skill_payload(stored.row, replaced=stored.replaced))

    if not created:
        # Nothing was created, so 201 would be a lie. One readable line rather
        # than a structured body: the modal renders `detail` verbatim, and the
        # reasons are what the user has to act on.
        raise HTTPException(
            400,
            "No skills could be imported from this archive. "
            + " ".join(f"{s['name'] or s['path'] or 'A skill'}: {s['reason']}." for s in skipped),
        )
    logger.info(
        "custom_skills_bulk_created company_present=%s created=%s skipped=%s",
        bool(company.company_id), len(created), len(skipped),
    )
    return {"skills": created, "skipped": skipped}


@router.get("")
def list_skills_route(company: WorkspaceContext = Depends(require_workspace)):
    """The COMPANY's custom skills, newest first (metadata only) — shared
    across all of the company's workspaces."""
    rows = db.list_custom_skills(company.company_id)
    return {"skills": [_skill_payload(r) for r in rows]}


@router.get("/{skill_id}")
def get_skill_route(
    skill_id: str,
    company: WorkspaceContext = Depends(require_workspace),
):
    """One custom skill WITH its method text — the edit form's source.

    Split from the list deliberately: the list is metadata-only because the
    method can run to 50k characters, and shipping every skill's full text to
    render a grid of cards would be absurd. 404 on a foreign or missing id,
    made indistinguishable by the company-filtered lookup."""
    row = db.get_custom_skill_by_id(company.company_id, skill_id)
    if row is None:
        raise HTTPException(404, "Skill not found.")
    return _skill_detail(row)


@router.patch(
    "/{skill_id}",
    dependencies=[Depends(require_same_origin)],  # CSRF/Origin gate (authed mutating)
)
async def edit_skill(
    skill_id: str,
    body: SkillEditIn,
    company: WorkspaceContext = Depends(require_workspace),
):
    """Edit a custom skill's name, description, and method text in place.

    The validation ladder mirrors upload's, because the same values end up in
    the same columns: empty/over-limit name or description → 422, a name with
    no letters or digits → 422, empty method → 400, and parsed content over
    MAX_SKILL_CONTENT_CHARS → 413 with upload's message verbatim. The cap is
    measured over the WHOLE parsed skill (this method plus the modules and
    references the row already carries), not the method alone.

    A rename re-derives the trigger; see the module docstring for why, and for
    what happens when the new name is another of the company's skills."""
    name = (body.name or "").strip()
    description = (body.description or "").strip()
    # The method keeps its interior whitespace verbatim — it is markdown, and
    # leading indentation can be load-bearing inside a fenced block. Only the
    # emptiness check strips.
    method = body.method or ""
    if not name:
        raise HTTPException(422, "Skill name is required.")
    if not description:
        raise HTTPException(422, "Skill description is required.")
    if len(name) > MAX_NAME_CHARS:
        raise HTTPException(422, f"Skill name must be {MAX_NAME_CHARS} characters or fewer.")
    if len(description) > MAX_DESCRIPTION_CHARS:
        raise HTTPException(
            422, f"Skill description must be {MAX_DESCRIPTION_CHARS} characters or fewer."
        )
    if not method.strip():
        raise HTTPException(
            400, "The skill method is empty — add the skill's method text and try again."
        )

    base = slugify(name)
    if not base:
        raise HTTPException(422, "Skill name must contain at least one letter or number.")

    # Company-filtered read: a foreign id is indistinguishable from a missing
    # one, and everything below only ever touches rows this read could reach.
    row = db.get_custom_skill_by_id(company.company_id, skill_id)
    if row is None:
        raise HTTPException(404, "Skill not found.")

    # Editing swaps the METHOD only — a .zip skill keeps every module and
    # reference the archive carried. content_hash_for is content-derived, so
    # recomputing it over the whole parsed set follows for free, and it has to:
    # prompt_version in agent_decision_log carries this hash, so a stale one
    # would misreport which method text actually answered.
    parsed = ParsedSkill(
        method=method,
        modules=dict(row.get("modules") or {}),
        references=dict(row.get("references") or {}),
    )
    if content_chars(parsed) > MAX_SKILL_CONTENT_CHARS:
        raise HTTPException(
            413,
            f"Skill content exceeds the {MAX_SKILL_CONTENT_CHARS:,} character "
            "limit. Please trim the skill text and try again.",
        )

    existing = db.list_custom_skills(company.company_id)
    others = [r for r in existing if r["id"] != skill_id]

    # A name that slugifies to what this skill already had is NOT a rename:
    # the trigger stays exactly as it is, including a `-2` it was handed at
    # upload time. Only a real rename re-derives it, so fixing a description
    # or a typo in the method can never move a trigger a team has learned.
    renaming = base != slugify(row.get("name") or "")
    replacing = None
    new_slug = None
    if renaming:
        # The same equivalence the re-upload replace uses: slugify(name), so
        # renaming to "PRD  author!" lands on the row named "PRD Author".
        # `others` comes from the company-filtered library read, so this can
        # only ever select a row inside the caller's own company.
        replacing = next(
            (r for r in others if slugify(r.get("name") or "") == base), None
        )
        # The replaced row is about to be deleted, so its trigger is free for
        # this skill to take over — that is the point of the replacement.
        contenders = [r for r in others if replacing is None or r["id"] != replacing["id"]]
        new_slug = available_slug(
            base, set(list_skills()) | {r["slug"] for r in contenders}
        )

    if replacing is not None:
        # Row first, then the write that takes its place: the (company_id,
        # slug) unique constraint means the loser has to be gone before this
        # skill can move onto its trigger. The delete is company-filtered too.
        # Its original file is cleaned up after the update lands, so a failed
        # update doesn't strand the bytes of a row that still exists.
        db.delete_custom_skill(company.company_id, replacing["id"])

    try:
        updated = db.update_custom_skill(
            company_id=company.company_id,
            skill_id=skill_id,
            workspace_id=company.workspace_id,
            name=name,
            description=description,
            method=method,
            modules=parsed.modules,
            references=parsed.references,
            content_hash=content_hash_for(parsed),
            # The stored original no longer describes this skill — its text is
            # whatever was uploaded, and the method above is what now answers.
            # Serving it would hand someone a file that contradicts the skill,
            # so the row stops pointing at it and the object is dropped below.
            # A later re-upload restores a downloadable original.
            storage_key=None,
            uploader_id=company.user_id,
            uploader_name=company.user_name or company.user_email or "",
            slug=new_slug,
        )
    except db.DuplicateSkillSlug:
        # Another upload or edit in this company took the trigger between the
        # library read above and this write.
        raise HTTPException(
            409,
            "Another skill just took this trigger. Please try again.",
        )
    if updated is None:
        # The row vanished between the read and the update (a concurrent
        # delete). Nothing to report but the 404 the caller would have got.
        raise HTTPException(404, "Skill not found.")

    old_key = row.get("storage_key")
    if old_key:
        await skills_storage.delete_skill_file(company_id=company.company_id, key=old_key)
    if replacing is not None and replacing.get("storage_key"):
        await skills_storage.delete_skill_file(
            company_id=company.company_id, key=replacing["storage_key"]
        )

    logger.info(
        "custom_skill_edited slug=%s renamed=%s replaced=%s",
        updated["slug"], renaming, replacing is not None,
    )
    return {
        **_skill_detail(updated),
        # The id of the company's OTHER skill this edit absorbed, or None. The
        # Skills screen drops that card; null means nothing else changed.
        "replaced_skill_id": replacing["id"] if replacing is not None else None,
    }


@router.get("/{skill_id}/file")
def skill_file_links(
    skill_id: str,
    company: WorkspaceContext = Depends(require_workspace),
):
    """Fresh signed view/download URLs for the ORIGINAL uploaded file.

    404 on anything that isn't this company's stored file — a foreign or
    missing id must be indistinguishable from a nonexistent one."""
    row = db.get_custom_skill_by_id(company.company_id, skill_id)
    if not row or not row.get("storage_key"):
        raise HTTPException(404, "Skill file not found.")
    ext = skills_storage.ext_of(row["storage_key"])
    try:
        urls = skills_storage.skill_file_urls(
            company_id=company.company_id,
            key=row["storage_key"],
            filename=f"{row['slug']}.{ext}",
        )
    except ValueError:
        raise HTTPException(404, "Skill file not found.")
    return {"name": f"{row['slug']}.{ext}", **urls}


@router.delete(
    "/{skill_id}",
    dependencies=[Depends(require_same_origin)],  # CSRF/Origin gate (authed mutating)
)
async def delete_skill(
    skill_id: str,
    company: WorkspaceContext = Depends(require_workspace),
):
    """Delete a custom skill for the WHOLE company (skills are company-scoped,
    so it disappears from every workspace's library and stops routing on the
    next invocation — the resolver reads the DB fresh each time).

    Row first, then the staged original: a failed storage delete leaves an
    orphaned file (best-effort, delete_skill_file never raises) rather than a
    ghost skill that still routes. 404 on a foreign or missing id, made
    indistinguishable by the company-filtered lookup."""
    row = db.delete_custom_skill(company.company_id, skill_id)
    if row is None:
        raise HTTPException(404, "Skill not found.")
    if row.get("storage_key"):
        await skills_storage.delete_skill_file(
            company_id=company.company_id, key=row["storage_key"]
        )
    logger.info("custom_skill_deleted slug=%s", row.get("slug"))
    return {"deleted": True, "id": skill_id}
