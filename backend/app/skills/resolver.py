"""Skill resolution across BOTH libraries — vendored built-ins and the
company's uploaded custom skills (PRD 1854).

Built-ins stay exactly as they are: disk directories loaded (and lru-cached)
by loader.get_skill. Custom skills resolve from the `custom_skills` table by
(company_id, slug) and are rebuilt into the same frozen SkillSpec via
custom.build_spec — so the gateway's method-prefix injection cannot tell the
two apart. Deliberately NO cache on the DB path: a read per invocation is one
PostgREST select, it keeps every replica consistent, and a deleted skill
disappears immediately (the invocation-error ticket relies on that).

Lookup order is BUILT-IN FIRST: a custom skill NEVER overrides a vendored one.
The upload route hands a name-colliding upload its own trigger instead
(custom.available_slug), so the two ids don't overlap in the first place and
both skills stay invocable — this order is what guarantees it even for a row
that predates that rule. Custom skills resolve on the fallback leg, where the
id names no vendored dir.
"""
from __future__ import annotations

import logging
from typing import Optional

from app.db.custom_skills import get_custom_skill
from app.skills.custom import build_spec
from app.skills.loader import SkillSpec, UnknownSkillError, get_skill, list_skills

logger = logging.getLogger(__name__)


def custom_skill_spec(company_id: str, slug: str) -> Optional[SkillSpec]:
    """The company's uploaded skill as a gateway-ready SkillSpec, or None.

    Fails OPEN to None on a DB error: this lookup rides every invocation of a
    non-vendored id, so a PostgREST hiccup must degrade to "no such custom
    skill" rather than take skill answers down with it."""
    if not company_id or not slug:
        return None
    try:
        row = get_custom_skill(company_id, slug)
    except Exception:  # noqa: BLE001 — any DB failure degrades to built-ins
        logger.warning(
            "custom-skill lookup failed for slug=%s; falling back to built-ins",
            slug, exc_info=True,
        )
        return None
    return build_spec(row) if row else None


def has_custom_skill(company_id: str, slug: str) -> bool:
    """True when the company has an uploaded skill under this slug."""
    return custom_skill_spec(company_id, slug) is not None


def resolve_skill(skill_id: str, company_id: Optional[str] = None) -> SkillSpec:
    """A SkillSpec from either library — the VENDORED skill first, so a custom
    upload can never take over a built-in's trigger; the company's uploads
    answer for every id no built-in claims. Raises UnknownSkillError when
    neither library has it.

    Built-in-first also skips the DB read entirely for vendored ids (the common
    case), and get_skill is lru_cached."""
    try:
        return get_skill(skill_id)
    except UnknownSkillError:
        pass
    if company_id:
        spec = custom_skill_spec(company_id, skill_id)
        if spec is not None:
            return spec
    raise UnknownSkillError(f"unknown skill {skill_id!r} in either library")


def is_builtin(skill_id: str) -> bool:
    """True when the id names a vendored disk skill (fresh check, uncached)."""
    return skill_id in set(list_skills())
