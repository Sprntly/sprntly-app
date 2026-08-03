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
    ParsedSkill,
    SkillParseError,
    available_slug,
    content_chars,
    content_hash_for,
    parse_upload,
    slugify,
)
from app.skills.loader import list_skills

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
    to changes underneath them."""
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

    try:
        parsed = parse_upload(file.filename or f"skill.{ext}", data)
    except SkillParseError as e:
        raise HTTPException(400, str(e))
    # Character cap on the PARSED text, after the byte cap on the raw file: a
    # zip can pass 20 MB compressed yet expand into far more prompt text than
    # any invocation should carry.
    if content_chars(parsed) > MAX_SKILL_CONTENT_CHARS:
        raise HTTPException(
            413,
            f"Skill content exceeds the {MAX_SKILL_CONTENT_CHARS:,} character "
            "limit. Please trim the skill text and try again.",
        )

    base = slugify(name)
    if not base:
        raise HTTPException(422, "Skill name must contain at least one letter or number.")

    # One read of the company library serves both the replace match and the
    # trigger series below. It is company-scoped by construction
    # (list_custom_skills filters on company_id), and that filter is the only
    # thing keeping a replace inside one tenant — another company's skill of
    # the same name is never in this list, so it can never be the row we write.
    existing = db.list_custom_skills(company.company_id)
    # Re-using one of the company's OWN skill names is a NEW VERSION of that
    # skill, not a second entry: update the row in place. Matched on the
    # slugified name — the same equivalence the old 409 used — because the
    # stored slug may have been disambiguated away from it by a built-in
    # collision ("PRD Author" living at /prd-author-2 still gets replaced).
    # The list is newest-first, so a legacy library that somehow holds two
    # rows under one name updates the most recent of them.
    replacing = next(
        (r for r in existing if slugify(r.get("name") or "") == base), None
    )

    key = await skills_storage.stage_skill_file(
        company_id=company.company_id, data=data, ext=ext
    )

    if replacing is not None:
        try:
            row = db.update_custom_skill(
                company_id=company.company_id,
                skill_id=replacing["id"],
                workspace_id=company.workspace_id,
                name=name,
                description=description,
                method=parsed.method,
                modules=parsed.modules,
                references=parsed.references,
                content_hash=content_hash_for(parsed),
                storage_key=key,
                uploader_id=company.user_id,
                uploader_name=company.user_name or company.user_email or "",
            )
        except Exception:
            await skills_storage.delete_skill_file(company_id=company.company_id, key=key)
            raise
        if row is not None:
            # The row points at the new file now, so the previous original is
            # unreferenced — drop it (best-effort; delete_skill_file never
            # raises) rather than leaving every version's bytes behind.
            old_key = replacing.get("storage_key")
            if old_key and old_key != key:
                await skills_storage.delete_skill_file(
                    company_id=company.company_id, key=old_key
                )
            logger.info(
                "custom_skill_replaced company_present=%s slug=%s size_bytes=%s ext=%s",
                bool(company.company_id), row["slug"], len(data), ext,
            )
            return _skill_payload(row, replaced=True)
        # The row vanished between the list read and the update (a concurrent
        # delete). Fall through and create the skill fresh, without counting
        # the gone row's slug as taken.
        existing = [r for r in existing if r["id"] != replacing["id"]]

    # Sharing a vendored built-in's name is deliberately NOT rejected and does
    # not override it: the built-in keeps its own trigger and this upload takes
    # the next free one, so chat can offer BOTH (their descriptions are what
    # tells them apart).
    slug = available_slug(base, set(list_skills()) | {r["slug"] for r in existing})

    try:
        row = db.insert_custom_skill(
            company_id=company.company_id,
            workspace_id=company.workspace_id,
            slug=slug,
            name=name,
            description=description,
            method=parsed.method,
            modules=parsed.modules,
            references=parsed.references,
            content_hash=content_hash_for(parsed),
            storage_key=key,
            uploader_id=company.user_id,
            uploader_name=company.user_name or company.user_email or "",
        )
    except db.DuplicateSkillSlug:
        # Backstop for the race the library read above can't close: two
        # concurrent uploads of the same new name both find nothing to replace
        # and both compute the same free slug. Only a genuine collision reaches
        # here now (a repeated name is a replace), so the message says to retry
        # rather than to rename. Roll back the staged object so the rejected
        # upload leaves nothing behind (no orphaned file without a row).
        await skills_storage.delete_skill_file(company_id=company.company_id, key=key)
        raise HTTPException(
            409,
            "Another upload just took this skill's trigger. Please try again.",
        )
    except Exception:
        await skills_storage.delete_skill_file(company_id=company.company_id, key=key)
        raise

    logger.info(
        "custom_skill_created company_present=%s slug=%s size_bytes=%s ext=%s name_conflict=%s",
        bool(company.company_id), slug, len(data), ext, slug != base,
    )
    return _skill_payload(row, replaced=False)


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
