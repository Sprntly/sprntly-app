"""Skill resolution across BOTH libraries — vendored built-ins and the
company's uploaded custom skills (PRD 1854).

Built-ins stay exactly as they are: disk directories loaded (and lru-cached)
by loader.get_skill. Custom skills resolve from the `custom_skills` table by
(company_id, slug) and are rebuilt into the same frozen SkillSpec via
custom.build_spec — so the gateway's method-prefix injection cannot tell the
two apart. Deliberately NO cache on the DB path: a read per invocation is one
PostgREST select, it keeps every replica consistent, and a deleted skill
disappears immediately (the invocation-error ticket relies on that).

The upload route rejects slugs that shadow a vendored id, so lookup order
(built-in first) is a formality, never a conflict-resolution rule.
"""
from __future__ import annotations

from typing import Optional

from app.db.custom_skills import get_custom_skill
from app.skills.custom import build_spec
from app.skills.loader import SkillSpec, UnknownSkillError, get_skill, list_skills


def custom_skill_spec(company_id: str, slug: str) -> Optional[SkillSpec]:
    """The company's uploaded skill as a gateway-ready SkillSpec, or None."""
    if not company_id or not slug:
        return None
    row = get_custom_skill(company_id, slug)
    return build_spec(row) if row else None


def has_custom_skill(company_id: str, slug: str) -> bool:
    """True when the company has an uploaded skill under this slug."""
    return custom_skill_spec(company_id, slug) is not None


def resolve_skill(skill_id: str, company_id: Optional[str] = None) -> SkillSpec:
    """A SkillSpec from either library: vendored disk skill first, then the
    company's custom skills. Raises UnknownSkillError when neither has it."""
    try:
        return get_skill(skill_id)
    except UnknownSkillError:
        if company_id:
            spec = custom_skill_spec(company_id, skill_id)
            if spec is not None:
                return spec
        raise


def is_builtin(skill_id: str) -> bool:
    """True when the id names a vendored disk skill (fresh check, uncached)."""
    return skill_id in set(list_skills())
