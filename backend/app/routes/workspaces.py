"""Workspace management routes — the workspace switcher's API.

Endpoints:
  GET    /v1/workspaces        — the caller's workspaces (each with their
                                 effective role + dataset slug); powers the
                                 switcher and the Settings → Workspaces pane.
  POST   /v1/workspaces        — create an additional workspace (org admin).
  PATCH  /v1/workspaces/{id}   — rename (workspace admin; slug/dataset
                                 unchanged so a rename never churns data).
  DELETE /v1/workspaces/{id}   — delete a non-default workspace (org admin).
                                 FKs cascade the scoped rows; the bound
                                 dataset row cascades too, and its dataset-
                                 text world (briefs etc.) is cleaned by slug.

Tenancy: require_company (the switcher must list workspaces BEFORE an active
workspace is chosen, so these routes take no X-Workspace-Id).
"""
from __future__ import annotations

import logging

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field

from app.auth import CompanyContext, require_company
from app.db.companies import slug_for_company_id
from app.db.workspaces import (
    create_workspace,
    dataset_slug_for_workspace,
    ensure_default_workspace,
    get_workspace,
    get_workspace_member,
    list_workspaces_for_user,
    register_workspace_dataset,
    update_workspace,
    upsert_workspace_member,
    delete_workspace,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/v1/workspaces", tags=["workspaces"])


class WorkspaceIn(BaseModel):
    name: str = Field(..., min_length=1, max_length=100)


def _require_org_admin(company: CompanyContext) -> None:
    if company.role not in ("owner", "admin"):
        raise HTTPException(403, "Workspace management is restricted to admins")


def _require_ws_admin(workspace_id: str, company: CompanyContext) -> None:
    if company.role in ("owner", "admin"):
        return
    member = get_workspace_member(workspace_id, company.user_id)
    if not member or member.get("role") != "admin":
        raise HTTPException(403, "Workspace management is restricted to admins")


def _public(ws: dict, *, role: str | None = None) -> dict:
    return {
        "id": ws["id"],
        "name": ws.get("name"),
        "slug": ws.get("slug"),
        "is_default": bool(ws.get("is_default")),
        "product_id": ws.get("product_id"),
        "dataset": ws.get("dataset") or dataset_slug_for_workspace(ws["id"]),
        **({"role": role} if role is not None else {}),
    }


@router.get("")
def get_workspaces(company: CompanyContext = Depends(require_company)):
    """The caller's accessible workspaces, default first, each carrying the
    caller's effective role and the workspace's dataset slug (what the
    frontend feeds into every dataset-keyed call)."""
    # Self-heal so every company always lists at least its default workspace.
    ensure_default_workspace(company.company_id)
    rows = list_workspaces_for_user(
        company.company_id, company.user_id, company.role
    )
    company_slug = slug_for_company_id(company.company_id)
    out = []
    for w in rows:
        ds = dataset_slug_for_workspace(w["id"])
        if not ds and w.get("is_default"):
            # The default workspace's dataset is the bare company slug even
            # before the binding row exists (pre-migration data).
            ds = company_slug
        out.append({**_public(w, role=w.get("role")), "dataset": ds})
    # org_role: the caller's COMPANY-level role. Distinct from the per-
    # workspace effective roles above — workspace creation is org-admin
    # gated, and the frontend needs this to show/hide create affordances
    # (a workspace-level admin who is a plain org member must not see them).
    return {"workspaces": out, "org_role": company.role}


@router.post("", status_code=status.HTTP_201_CREATED)
def post_workspace(
    body: WorkspaceIn,
    company: CompanyContext = Depends(require_company),
):
    """Create an additional workspace: the row, the creator's workspace-admin
    membership, and its dataset (slug '{company}--{workspace}') + corpus dir."""
    _require_org_admin(company)
    name = body.name.strip()
    if not name:
        raise HTTPException(422, "Workspace name cannot be empty")
    ws = create_workspace(company.company_id, name)
    upsert_workspace_member(ws["id"], company.user_id, "admin")
    company_slug = slug_for_company_id(company.company_id)
    dataset = None
    if company_slug:
        dataset = register_workspace_dataset(ws, company_slug=company_slug)
    return {**_public(ws, role="admin"), "dataset": dataset}


@router.patch("/{workspace_id}")
def patch_workspace(
    workspace_id: str,
    body: WorkspaceIn,
    company: CompanyContext = Depends(require_company),
):
    ws = get_workspace(workspace_id)
    if not ws or ws.get("company_id") != company.company_id:
        raise HTTPException(404, "Workspace not found")
    _require_ws_admin(workspace_id, company)
    name = body.name.strip()
    if not name:
        raise HTTPException(422, "Workspace name cannot be empty")
    updated = update_workspace(workspace_id, name=name) or {**ws, "name": name}
    return _public(updated)


@router.delete("/{workspace_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_workspace_route(
    workspace_id: str,
    company: CompanyContext = Depends(require_company),
):
    """Delete a non-default workspace. The default is refused (409): it owns
    the company's bare-slug dataset world. Scoped rows + workspace_members +
    the bound datasets row all cascade via FKs; dataset-TEXT rows (briefs,
    cached_asks, knowledge_*) are cleaned by slug here since they have no FK.
    """
    ws = get_workspace(workspace_id)
    if not ws or ws.get("company_id") != company.company_id:
        raise HTTPException(404, "Workspace not found")
    _require_org_admin(company)
    if ws.get("is_default"):
        raise HTTPException(409, "The default workspace cannot be deleted")

    slug = dataset_slug_for_workspace(workspace_id)
    delete_workspace(workspace_id)
    if slug:
        # Best-effort cleanup of the dataset-text world. A failure leaves
        # orphan rows keyed by a slug nothing references — harmless, and an
        # admin op can sweep them later.
        try:
            from app.db.client import require_client

            client = require_client()
            for table, col in (
                ("briefs", "dataset"),
                ("cached_asks", "dataset"),
                ("knowledge_entities", "dataset"),
                ("knowledge_relationships", "dataset"),
                ("pipeline_runs", "dataset"),
                ("enterprise_input_sources", "dataset"),
            ):
                client.table(table).delete().eq(col, slug).execute()
        except Exception:  # noqa: BLE001 — cleanup is best-effort
            logger.warning("workspace delete: dataset cleanup failed for %s", slug)
    return None
