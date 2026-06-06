"""Coworker names — the user-given names for their four AI coworkers.

Backs design-v4 onboarding page 07: the user names each specialist that
joins the workspace (Product / Design / Data Science / Admin). The name
is how the coworker signs its work in chats, briefs, and comments.

Storage: `companies.coworker_names jsonb` (column from the coworker_names
migration). The map is keyed by a fixed slot id so adding a future slot
never needs a schema change; unknown keys are dropped on save.
"""
from __future__ import annotations

from pydantic import BaseModel, field_validator

from app.db.client import require_client

# Fixed slots, in onboarding display order. Defaults double as the
# placeholder handles shown in the page-07 form (name_pm, name_pd, …).
COWORKER_SLOTS: tuple[str, ...] = ("pm", "pd", "ds", "admin")

_MAX_NAME_LEN = 40


class CoworkerNames(BaseModel):
    """Names for the four coworker slots; each optional until set."""

    pm: str = ""
    pd: str = ""
    ds: str = ""
    admin: str = ""

    @field_validator("pm", "pd", "ds", "admin", mode="before")
    @classmethod
    def _clean(cls, v: object) -> str:
        s = ("" if v is None else str(v)).strip()
        return s[:_MAX_NAME_LEN]


def load_coworker_names(enterprise_id: str) -> CoworkerNames:
    """Read the company's coworker names; empty model if unset/invalid."""
    r = (
        require_client().table("companies")
        .select("coworker_names")
        .eq("id", enterprise_id)
        .execute()
    )
    if not r.data:
        return CoworkerNames()
    raw = r.data[0].get("coworker_names") or {}
    if not isinstance(raw, dict):
        return CoworkerNames()
    # Keep only known slots; ignore anything legacy/hand-edited.
    known = {k: raw[k] for k in COWORKER_SLOTS if k in raw}
    return CoworkerNames.model_validate(known)


def save_coworker_names(enterprise_id: str, names: CoworkerNames) -> CoworkerNames:
    """Persist the four coworker names (only known slots are written)."""
    (
        require_client().table("companies")
        .update({"coworker_names": names.model_dump()})
        .eq("id", enterprise_id)
        .execute()
    )
    return names
