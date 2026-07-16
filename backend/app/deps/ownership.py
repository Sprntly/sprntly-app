"""Tenant ownership binding for the legacy dataset/id-keyed surfaces.

The legacy PRD / brief / evidence / Ask / datasets routes were gated only by
``require_session`` (a non-tenant "is anyone signed in?" check) and looked rows
up by a client-supplied ``prd_id`` / ``brief_id`` / ``evidence_id`` / ``dataset``
slug with no ownership check. Because the backend talks to Supabase with the
service-role key (RLS bypassed), the application layer is the ONLY tenant
boundary — so one signed-in tenant could read/edit/delete another tenant's rows
and leak another tenant's corpus through Ask.

These helpers re-establish that boundary. They mirror the pattern the newer
surfaces use (``require_company`` resolves the caller's company from the JWT;
the route then asserts the requested row belongs to that company). The ownership
chain is:

    dataset (slug)  --company_id_for_slug-->  company_id
    brief           --brief.dataset (slug)-->  company_id
    prd             --prd.brief_id-->  brief  -->  company_id
    evidence        --evidence.brief_id-->  brief  -->  company_id

On any mismatch (or a missing row, or an unresolvable slug) we raise 404 rather
than 403 — a foreign tenant must not be able to tell an existing-but-not-yours
row apart from a non-existent one (no existence disclosure).

Multi-workspace (2026-07): datasets are per-WORKSPACE (datasets.workspace_id;
the default workspace keeps the bare company slug, additional workspaces use
'{company}--{workspace}'). Every helper takes an optional `workspace_id`;
when provided (routes on require_workspace pass ctx.workspace_id) the dataset
must be bound to THAT workspace — with a rollout fallback: a dataset whose
binding is NULL but whose slug resolves to the caller's company stays
accepted (pre-backfill rows must not 404).
"""
from __future__ import annotations

from fastapi import HTTPException

from app.db.companies import company_id_for_slug


def _not_found(detail: str) -> HTTPException:
    # 404 (never 403) so cross-tenant existence is never disclosed.
    return HTTPException(status_code=404, detail=detail)


def company_id_for_dataset(slug: str) -> str | None:
    """The owning company id for a dataset slug, or None when no company owns it.

    Resolution order: the dataset's bound workspace (datasets.workspace_id →
    workspaces.company_id — covers per-workspace '{company}--{workspace}'
    slugs), then the legacy slug==company-slug mapping. Returns None when the
    slug maps to no company so callers can 404 without disclosing existence.
    """
    if not slug:
        return None
    from app.db.workspaces import workspace_for_dataset_slug

    bound = workspace_for_dataset_slug(slug)
    if bound:
        return bound["company_id"]
    return company_id_for_slug(slug)


def _dataset_in_workspace(slug: str, company_id: str, workspace_id: str) -> bool:
    """True iff dataset `slug` is usable from `workspace_id`. Bound datasets
    must match exactly; an unbound dataset (NULL workspace_id) owned by the
    company is accepted during rollout (legacy rows pre-backfill)."""
    from app.db.workspaces import workspace_for_dataset_slug

    bound = workspace_for_dataset_slug(slug)
    if bound:
        return bound["workspace_id"] == workspace_id
    return company_id_for_slug(slug) == company_id


def require_owned_dataset(
    slug: str, company_id: str, workspace_id: str | None = None
) -> str:
    """Assert dataset `slug` belongs to `company_id` (and, when given, to the
    active `workspace_id`); return the slug.

    Raises 404 when the slug maps to a different company/workspace (or to no
    company), so a caller can never act on, list files of, or seed an LLM
    answer from a dataset that isn't theirs.
    """
    owner = company_id_for_dataset(slug)
    if owner is None or owner != company_id:
        raise _not_found(f"Dataset {slug!r} not found")
    if workspace_id and not _dataset_in_workspace(slug, company_id, workspace_id):
        raise _not_found(f"Dataset {slug!r} not found")
    return slug


def require_owned_brief(
    brief_id: int, company_id: str, workspace_id: str | None = None
) -> dict:
    """Resolve `brief_id` → brief and assert it belongs to `company_id` (and
    the active workspace when given).

    Returns the brief row on success; raises 404 when the brief is missing or
    its dataset resolves to a different company/workspace.
    """
    from app.db import get_brief_by_id

    brief = get_brief_by_id(brief_id)
    if not brief:
        raise _not_found("Brief not found")
    slug = brief.get("dataset") or ""
    owner = company_id_for_dataset(slug)
    if owner is None or owner != company_id:
        raise _not_found("Brief not found")
    if workspace_id and not _dataset_in_workspace(slug, company_id, workspace_id):
        raise _not_found("Brief not found")
    return brief


def require_owned_prd(
    prd_id: int, company_id: str, workspace_id: str | None = None
) -> dict:
    """Resolve `prd_id` → prd → brief and assert ownership by `company_id`
    (and the active workspace when given).

    Returns the raw prd row on success (callers that need the rendered body
    re-read via get_prd_rendered after the gate passes). Raises 404 when the
    prd is missing, its brief is missing, or the brief's dataset resolves to a
    different company/workspace.
    """
    from app.db.prds import get_prd

    prd = get_prd(prd_id)
    if not prd:
        raise _not_found("PRD not found")
    # Bind the prd to its company via brief.dataset. A prd whose brief vanished
    # is unattributable to any tenant → treat as not found (fail closed).
    require_owned_brief(prd["brief_id"], company_id, workspace_id)
    return prd


def require_owned_evidence(
    evidence_id: int, company_id: str, workspace_id: str | None = None
) -> dict:
    """Resolve `evidence_id` → evidence → brief and assert ownership (company
    + active workspace when given).

    Returns the evidence row on success; raises 404 when the evidence is
    missing or its brief's dataset resolves to a different company/workspace.
    """
    from app.db import get_evidence

    evidence = get_evidence(evidence_id)
    if not evidence:
        raise _not_found("Evidence not found")
    require_owned_brief(evidence["brief_id"], company_id, workspace_id)
    return evidence
