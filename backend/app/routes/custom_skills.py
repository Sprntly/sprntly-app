"""Custom skill endpoints — upload, list, and original-file links (PRD 1854).

  POST   /v1/skills               -> upload a .md/.zip skill with name+description
  GET    /v1/skills               -> list the company's custom skills (metadata)
  GET    /v1/skills/{id}/file     -> signed view/download URLs for the original upload

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
400, oversize → 413, unparseable content → 400, slug conflict (company
duplicate OR shadowing a built-in skill id) → 409.
"""
from __future__ import annotations

import logging

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile

from app import db, skills_storage
from app.auth import WorkspaceContext, require_workspace
from app.design_agent.csrf import require_same_origin  # server-side CSRF/Origin gate
from app.skills.custom import (
    MAX_DESCRIPTION_CHARS,
    MAX_NAME_CHARS,
    SkillParseError,
    content_hash_for,
    parse_upload,
    slugify,
)
from app.skills.loader import list_skills

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/v1/skills", tags=["skills"])


def _skill_payload(row: dict) -> dict:
    """Row → API shape shared by POST (201 body) and GET (list items)."""
    return {
        "id": row["id"],
        "slug": row["slug"],
        "trigger": f"/{row['slug']}",
        "name": row["name"],
        "description": row["description"],
        "uploader_name": row.get("uploader_name") or "",
        "created_at": row.get("created_at"),
        "has_file": bool(row.get("storage_key")),
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
    """Create a custom skill from an uploaded .md or .zip (≤ 20 MB)."""
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

    slug = slugify(name)
    if not slug:
        raise HTTPException(422, "Skill name must contain at least one letter or number.")
    if slug in set(list_skills()):
        # Shadowing a vendored built-in id would make the /trigger ambiguous.
        raise HTTPException(
            409, f"'{slug}' is the id of a built-in Sprntly skill — choose a different name."
        )

    key = await skills_storage.stage_skill_file(
        company_id=company.company_id, data=data, ext=ext
    )
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
        # Roll back the staged object so the rejected upload leaves nothing
        # behind (no orphaned file without a metadata row).
        await skills_storage.delete_skill_file(company_id=company.company_id, key=key)
        raise HTTPException(
            409, "A skill with this name already exists in your company."
        )
    except Exception:
        await skills_storage.delete_skill_file(company_id=company.company_id, key=key)
        raise

    logger.info(
        "custom_skill_created company_present=%s slug=%s size_bytes=%s ext=%s",
        bool(company.company_id), slug, len(data), ext,
    )
    return _skill_payload(row)


@router.get("")
def list_skills_route(company: WorkspaceContext = Depends(require_workspace)):
    """The COMPANY's custom skills, newest first (metadata only) — shared
    across all of the company's workspaces."""
    rows = db.list_custom_skills(company.company_id)
    return {"skills": [_skill_payload(r) for r in rows]}


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
