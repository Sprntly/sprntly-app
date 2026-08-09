"""The write half of a custom-skill upload — parsed content in, a stored row
out (PRD 1854).

Lifted out of routes/custom_skills.py::upload_skill unchanged, because it now
has more than one caller: a single .md/.zip upload stores ONE skill, a
multi-skill archive stores N of them in a loop, and the GitHub skill import
lands on it next. Everything HTTP stays in the route — this module raises the
domain errors below and the route maps them to the status codes it always
returned, so a per-skill failure inside a loop is something the caller can
catch and record in a `skipped` list instead of a 4xx that costs the user the
other nine skills.

The collision rules are the route's, and they hold per skill:

  - re-using one of the COMPANY'S OWN skill names REPLACES that skill in place
    (same id, same trigger, new content), matched on `slugify(name)`
  - sharing a BUILT-IN's name overrides nothing — the upload takes the next
    free trigger in the `<slug>`, `<slug>-2` series

The library read that drives both is company-filtered, and it happens on EVERY
call rather than once per request: in a multi-skill loop the second skill has
to see the first one's row, or two skills named the same inside one archive
would race for one trigger and a built-in collision would hand out the same
`-2` twice.
"""
from __future__ import annotations

import logging
from collections.abc import Iterable
from dataclasses import dataclass

from app import db, skills_storage
from app.skills.custom import (
    ParsedSkill,
    available_slug,
    content_chars,
    content_hash_for,
    slugify,
)

logger = logging.getLogger(__name__)


class SkillStoreError(ValueError):
    """Base for the store's user-facing refusals — the message is verbatim."""


class SkillContentTooLarge(SkillStoreError):
    """Parsed text over the character cap (413 at the route)."""


class SkillNameUnusable(SkillStoreError):
    """A name that slugifies to nothing, so it can have no trigger (422)."""


class SkillSlugTaken(SkillStoreError):
    """Two concurrent writes raced for the same free trigger (409)."""


@dataclass(frozen=True)
class StoredSkill:
    row: dict
    #: True when this write updated the company's existing skill of the same
    #: name rather than creating a new one.
    replaced: bool


async def store_skill(
    *,
    company_id: str,
    workspace_id: str,
    uploader_id: str,
    uploader_name: str,
    name: str,
    description: str,
    parsed: ParsedSkill,
    data: bytes,
    ext: str,
    builtin_slugs: Iterable[str],
    max_content_chars: int,
    source_id: str | None = None,
) -> StoredSkill:
    """Stage `data` and create-or-replace the skill row it belongs to.

    `builtin_slugs` and `max_content_chars` are passed in rather than imported
    here so the caller stays the one place they are resolved — the route reads
    them from its own module globals, which is what the suite's monkeypatches
    of `list_skills` / `MAX_SKILL_CONTENT_CHARS` reach.

    `data`/`ext` are the bytes to keep as this skill's ORIGINAL: the uploaded
    file itself for a single upload, or the standalone archive synthesized for
    one skill out of a multi-skill zip. Either way one row owns one object, so
    the per-skill download and the delete cleanup keep working.

    `source_id` names the synced GitHub folder this skill came from, and it
    rides the SAME collision rules as everything else here — which is the point.
    A synced folder holding `estimation-helper.md` replaces the company's own
    "Estimation Helper" in place and ADOPTS it, so the team ends up with one
    skill on its original trigger rather than a duplicate on `-2`. That adoption
    also makes the skill read-only, which is a real consequence of importing a
    same-named skill from a synced folder and is called out in the import UI.
    """
    # Character cap on the PARSED text, after the byte cap on the raw file: a
    # zip can pass 20 MB compressed yet expand into far more prompt text than
    # any invocation should carry.
    if content_chars(parsed) > max_content_chars:
        raise SkillContentTooLarge(
            f"Skill content exceeds the {max_content_chars:,} character "
            "limit. Please trim the skill text and try again."
        )

    base = slugify(name)
    if not base:
        raise SkillNameUnusable("Skill name must contain at least one letter or number.")

    # One read of the company library serves both the replace match and the
    # trigger series below. It is company-scoped by construction
    # (list_custom_skills filters on company_id), and that filter is the only
    # thing keeping a replace inside one tenant — another company's skill of
    # the same name is never in this list, so it can never be the row we write.
    existing = db.list_custom_skills(company_id)
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
        company_id=company_id, data=data, ext=ext
    )

    if replacing is not None:
        try:
            row = db.update_custom_skill(
                company_id=company_id,
                skill_id=replacing["id"],
                workspace_id=workspace_id,
                name=name,
                description=description,
                method=parsed.method,
                modules=parsed.modules,
                references=parsed.references,
                content_hash=content_hash_for(parsed),
                storage_key=key,
                uploader_id=uploader_id,
                uploader_name=uploader_name,
                source_id=source_id,
            )
        except Exception:
            await skills_storage.delete_skill_file(company_id=company_id, key=key)
            raise
        if row is not None:
            # The row points at the new file now, so the previous original is
            # unreferenced — drop it (best-effort; delete_skill_file never
            # raises) rather than leaving every version's bytes behind.
            old_key = replacing.get("storage_key")
            if old_key and old_key != key:
                await skills_storage.delete_skill_file(
                    company_id=company_id, key=old_key
                )
            logger.info(
                "custom_skill_replaced company_present=%s slug=%s size_bytes=%s ext=%s",
                bool(company_id), row["slug"], len(data), ext,
            )
            return StoredSkill(row=row, replaced=True)
        # The row vanished between the list read and the update (a concurrent
        # delete). Fall through and create the skill fresh, without counting
        # the gone row's slug as taken.
        existing = [r for r in existing if r["id"] != replacing["id"]]

    # Sharing a vendored built-in's name is deliberately NOT rejected and does
    # not override it: the built-in keeps its own trigger and this upload takes
    # the next free one, so chat can offer BOTH (their descriptions are what
    # tells them apart).
    slug = available_slug(base, set(builtin_slugs) | {r["slug"] for r in existing})

    try:
        row = db.insert_custom_skill(
            company_id=company_id,
            workspace_id=workspace_id,
            slug=slug,
            name=name,
            description=description,
            method=parsed.method,
            modules=parsed.modules,
            references=parsed.references,
            content_hash=content_hash_for(parsed),
            storage_key=key,
            uploader_id=uploader_id,
            uploader_name=uploader_name,
            source_id=source_id,
        )
    except db.DuplicateSkillSlug as exc:
        # Backstop for the race the library read above can't close: two
        # concurrent uploads of the same new name both find nothing to replace
        # and both compute the same free slug. Only a genuine collision reaches
        # here now (a repeated name is a replace), so the message says to retry
        # rather than to rename. Roll back the staged object so the rejected
        # upload leaves nothing behind (no orphaned file without a row).
        await skills_storage.delete_skill_file(company_id=company_id, key=key)
        raise SkillSlugTaken(
            "Another upload just took this skill's trigger. Please try again."
        ) from exc
    except Exception:
        await skills_storage.delete_skill_file(company_id=company_id, key=key)
        raise

    logger.info(
        "custom_skill_created company_present=%s slug=%s size_bytes=%s ext=%s name_conflict=%s",
        bool(company_id), slug, len(data), ext, slug != base,
    )
    return StoredSkill(row=row, replaced=False)
