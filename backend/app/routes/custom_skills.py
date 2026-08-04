"""Custom skill endpoints — upload, list, edit, and original-file links (PRD 1854).

  POST   /v1/skills                 -> upload a .md/.zip skill with name+description
  GET    /v1/skills                 -> list the company's custom skills (metadata)
  GET    /v1/skills/github/discover -> list the skills a connected repo holds (read-only)
  POST   /v1/skills/github/import   -> import selected skills from that repo
  GET    /v1/skills/{id}            -> one skill WITH its method text (the edit form's source)
  PATCH  /v1/skills/{id}            -> edit name/description/method in place
  GET    /v1/skills/{id}/file       -> signed view/download URLs for the original upload
  DELETE /v1/skills/{id}            -> delete a skill (row + original file)

The two `/github/*` routes are declared ABOVE the `/{skill_id}` family, since
FastAPI matches in declaration order and the catch-all would otherwise answer
"Skill not found." for them. They read a repo through the GitHub App
INSTALLATION token, resolved from the caller's company and the repo name —
never from a client-supplied installation id — and a repo this company hasn't
connected is a 404, never a 403 (a 403 would confirm that someone else has).

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
from app.skills import github_source
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


# ─── GitHub import ───────────────────────────────────────────────────────────
#
# Declared ABOVE the `/{skill_id}` routes below: FastAPI matches in declaration
# order, so `/github/discover` under them would be swallowed by the catch-all
# and answer "Skill not found." for a path that has nothing to do with an id.


def _installation_for(repo: str, company: WorkspaceContext) -> int:
    """The caller company's GitHub installation for `repo`, or 404.

    The installation is resolved from the CALLER'S COMPANY and the repo name —
    it is never accepted from the client, because an installation id is all it
    would take to read a repo belonging to somebody else entirely.
    `find_github_installation_for_repo` is company-filtered, so a repo another
    company connected simply does not resolve here.

    404 and NOT 403 on a miss, like every other ownership check in this repo: a
    403 would confirm that the repo exists and is connected to Sprntly by
    someone, which is exactly the fact a foreign tenant must not learn."""
    if not repo or "/" not in repo:
        raise HTTPException(422, "Repository must be in owner/name form.")
    install = db.find_github_installation_for_repo(repo, company.company_id)
    if not install or not install.get("installation_id"):
        raise HTTPException(
            404,
            "That repository isn't connected to Sprntly. Connect it from "
            "Settings → Connectors and try again.",
        )
    return int(install["installation_id"])


def _discover(repo: str, ref: str, path: str, company: WorkspaceContext):
    """Shared discovery + its error ladder (both routes run it — the import
    re-runs it rather than trusting the client's list)."""
    installation_id = _installation_for(repo, company)
    try:
        return github_source.discover_skills(
            installation_id, repo, ref or None, subpath=path or ""
        )
    except github_source.GithubRefNotFound as e:
        raise HTTPException(404, str(e)) from e
    except github_source.GithubSourceError as e:
        # A GitHub-side failure is not the caller's fault and not ours — 502,
        # the same posture ingest.github_deep_read takes.
        logger.warning("skills_github_discover_failed repo_present=%s", bool(repo))
        raise HTTPException(502, str(e)) from e


def _preview(skill: github_source.GithubSkill, *, taken: set[str], by_name: dict[str, dict]) -> dict:
    """One discovered skill as the picker needs it.

    `status` is computed with the SAME slugify/available_slug the write path
    uses, against the same company library, so the preview and the outcome
    cannot disagree: "replaces" means this name is already one of the company's
    skills (the import updates that row in place, keeping its trigger), "new"
    means it lands on the trigger shown here."""
    base = slugify(skill.name)
    replacing = by_name.get(base)
    if not skill.importable:
        status, slug = "invalid", base
    elif replacing is not None:
        status, slug = "replaces", replacing["slug"]
    else:
        status, slug = "new", available_slug(base, taken) if base else ""
    return {
        "path": skill.path,
        "name": skill.name,
        "description": skill.description,
        "slug_preview": slug,
        "trigger_preview": f"/{slug}" if slug else "",
        "file_count": skill.file_count,
        "char_count": skill.char_count,
        "status": status,
        "reason": skill.reason,
    }


@router.get("/github/discover")
def discover_github_skills(
    repo: str,
    ref: str = "",
    path: str = "",
    company: WorkspaceContext = Depends(require_workspace),
):
    """List the skills a connected repo holds — read-only, writes nothing.

    Two questions the picker has to answer before anyone commits to an import,
    and both need the company's own library: which trigger each skill would get,
    and which of them would REPLACE a skill the team already has. Both are
    computed here rather than in the browser, because the client's copy of the
    library can be stale and the built-in catalog is not fully public.
    """
    result = _discover(repo, ref, path, company)
    existing = db.list_custom_skills(company.company_id)
    taken = set(list_skills()) | {r["slug"] for r in existing}
    by_name = {slugify(r.get("name") or ""): r for r in existing}
    return {
        "repo": repo,
        "ref": result.branch,
        "commit_sha": result.commit_sha,
        "truncated": result.truncated,
        "notes": result.notes,
        "skills": [_preview(s, taken=taken, by_name=by_name) for s in result.skills],
    }


class GithubImportIn(BaseModel):
    """POST body — which skills, out of which repo at which ref."""

    repo: str = ""
    ref: str = ""
    path: str = ""
    #: Repo-relative skill folders, exactly as `discover` returned them.
    paths: list[str] = []


@router.post(
    "/github/import",
    status_code=201,
    dependencies=[Depends(require_same_origin)],  # CSRF/Origin gate (authed mutating)
)
async def import_github_skills(
    body: GithubImportIn,
    company: WorkspaceContext = Depends(require_workspace),
):
    """Import the selected skills from a connected repo into the library.

    Discovery is re-run SERVER-SIDE and only skills present in that result are
    imported. The client's `paths` are a filter over what we found, never fetch
    targets — accepting a path to read would turn this route into "fetch any
    file from any repo this company has connected", which is not what it is.

    Everything after that is the multi-skill upload path: each skill is stored
    through the same `store_skill`, so the collision rules (replace the
    company's own same-named skill in place, take the next free trigger past a
    built-in's) and the per-row stored original are identical whether a skill
    arrived in a zip or from a repo. Per-skill failures land in `skipped` and
    the rest still import.
    """
    repo = (body.repo or "").strip()
    wanted = {p.strip() for p in (body.paths or [])}
    if not wanted:
        raise HTTPException(422, "Select at least one skill to import.")

    result = _discover(repo, (body.ref or "").strip(), (body.path or "").strip(), company)
    selected = [s for s in result.skills if s.path in wanted]
    if not selected:
        raise HTTPException(
            404,
            "We couldn't find those skills in the repository any more — "
            "search again and retry.",
        )

    imported: list[dict] = []
    skipped: list[dict] = []
    for skill in selected:
        if not skill.importable:
            skipped.append(
                {"path": skill.path, "name": skill.name, "reason": skill.reason}
            )
            continue
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
        imported.append(_skill_payload(stored.row, replaced=stored.replaced))

    if not imported:
        raise HTTPException(
            400,
            "No skills could be imported. "
            + " ".join(f"{s['name'] or s['path'] or 'A skill'}: {s['reason']}." for s in skipped),
        )
    logger.info(
        "custom_skills_github_imported company_present=%s imported=%s skipped=%s",
        bool(company.company_id), len(imported), len(skipped),
    )
    return {
        "imported": imported,
        "skipped": skipped,
        "commit_sha": result.commit_sha,
        "ref": result.branch,
    }


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
