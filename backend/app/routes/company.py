"""Company config routes — KPI tree + coworker names.

GET  /v1/company/kpi-tree   — current tree (404 if unset)
PUT  /v1/company/kpi-tree   — validate + persist (version auto-bumps)
GET  /v1/company/coworkers  — current coworker names (empty map if unset)
PUT  /v1/company/coworkers  — persist coworker names

Backs design-v4 onboarding page 05 (KPI tree) + page 07 (coworker names) +
dashboard 09; Synthesis reads the KPI tree for strategic-alignment scoring.
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException

from app.auth import CompanyContext, require_company
from app.coworkers import CoworkerNames, load_coworker_names, save_coworker_names
from app.kpi_tree import KpiTree, load_kpi_tree, save_kpi_tree

router = APIRouter(prefix="/v1/company", tags=["company"])


@router.get("/kpi-tree")
def get_kpi_tree(company: CompanyContext = Depends(require_company)):
    tree = load_kpi_tree(company.company_id)
    if tree is None:
        raise HTTPException(404, "KPI tree not set — complete onboarding step 5")
    return tree.model_dump()


@router.put("/kpi-tree")
def put_kpi_tree(tree: KpiTree, company: CompanyContext = Depends(require_company)):
    saved = save_kpi_tree(company.company_id, tree)
    return {"ok": True, "version": saved.version}


@router.get("/coworkers")
def get_coworkers(company: CompanyContext = Depends(require_company)):
    return load_coworker_names(company.company_id).model_dump()


@router.put("/coworkers")
def put_coworkers(
    names: CoworkerNames, company: CompanyContext = Depends(require_company)
):
    saved = save_coworker_names(company.company_id, names)
    return {"ok": True, "coworker_names": saved.model_dump()}
