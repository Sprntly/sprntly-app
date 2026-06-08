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
"""
from __future__ import annotations

from fastapi import HTTPException

from app.db.companies import company_id_for_slug


def _not_found(detail: str) -> HTTPException:
    # 404 (never 403) so cross-tenant existence is never disclosed.
    return HTTPException(status_code=404, detail=detail)


def company_id_for_dataset(slug: str) -> str | None:
    """The owning company id for a dataset slug, or None when no company owns it.

    A dataset slug IS a company slug (briefs/prds/asks all key off the slug as
    TEXT; the company that owns the slug owns those rows). Returns None when the
    slug maps to no company so callers can 404 without disclosing existence.
    """
    if not slug:
        return None
    return company_id_for_slug(slug)


def require_owned_dataset(slug: str, company_id: str) -> str:
    """Assert dataset `slug` belongs to `company_id`; return the slug.

    Raises 404 when the slug maps to a different company (or to no company),
    so a caller can never act on, list files of, or seed an LLM answer from a
    dataset that isn't theirs.
    """
    owner = company_id_for_dataset(slug)
    if owner is None or owner != company_id:
        raise _not_found(f"Dataset {slug!r} not found")
    return slug


def require_owned_brief(brief_id: int, company_id: str) -> dict:
    """Resolve `brief_id` → brief and assert it belongs to `company_id`.

    Returns the brief row on success; raises 404 when the brief is missing or
    its dataset resolves to a different company.
    """
    from app.db import get_brief_by_id

    brief = get_brief_by_id(brief_id)
    if not brief:
        raise _not_found("Brief not found")
    owner = company_id_for_dataset(brief.get("dataset") or "")
    if owner is None or owner != company_id:
        raise _not_found("Brief not found")
    return brief


def require_owned_prd(prd_id: int, company_id: str) -> dict:
    """Resolve `prd_id` → prd → brief and assert ownership by `company_id`.

    Returns the raw prd row on success (callers that need the rendered body
    re-read via get_prd_rendered after the gate passes). Raises 404 when the
    prd is missing, its brief is missing, or the brief's dataset resolves to a
    different company.
    """
    from app.db.prds import get_prd

    prd = get_prd(prd_id)
    if not prd:
        raise _not_found("PRD not found")
    # Bind the prd to its company via brief.dataset. A prd whose brief vanished
    # is unattributable to any tenant → treat as not found (fail closed).
    require_owned_brief(prd["brief_id"], company_id)
    return prd


def require_owned_evidence(evidence_id: int, company_id: str) -> dict:
    """Resolve `evidence_id` → evidence → brief and assert ownership.

    Returns the evidence row on success; raises 404 when the evidence is
    missing or its brief's dataset resolves to a different company.
    """
    from app.db import get_evidence

    evidence = get_evidence(evidence_id)
    if not evidence:
        raise _not_found("Evidence not found")
    require_owned_brief(evidence["brief_id"], company_id)
    return evidence
