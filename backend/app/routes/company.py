"""Company config routes — the KPI tree (North Star + supporting metrics).

GET /v1/company/kpi-tree  — current tree (404 if unset)
PUT /v1/company/kpi-tree  — validate + persist (version auto-bumps)

Backs design-v4 onboarding page 05 + dashboard 09; Synthesis reads the tree
for strategic-alignment scoring (§4c).
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException

from app.auth import CompanyContext, require_company
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
