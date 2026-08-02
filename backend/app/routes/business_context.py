"""Business Context routes — the company's structured "lens".

GET  /v1/company/business-context               — current doc (404 if unset) [member]
PUT  /v1/company/business-context                — validate + save; every known leaf
                                                    the human sends is stamped src="user"
                                                    (so the agent never overwrites it) [admin]
POST /v1/company/business-context/refresh        — kick off an async refresh [admin]
GET  /v1/company/business-context/refresh-status — poll the refresh job [member]

Separate file from routes/company.py on purpose (avoids collisions with
in-flight branches editing that module). All routes require_company.

Access model (v0 access-boundary fix): the business-context doc is org-wide
company config. The GET stays open to any member, but the PUT (human edits)
and the refresh trigger (re-runs the agent + bumps the stored version for
everyone) mutate org-wide config and are gated to admin/owner via
`_require_admin` (the same helper app/routes/team.py uses for team writes).
refresh-status is read-only, so it stays open to any member like the doc GET.

The refresh itself is async (fire-and-forget `asyncio.create_task`, mirroring
app/ask_job_runner.py's pattern via app/business_context_refresh_runner.py):
a synchronous call here used to block the whole HTTP request until the real
research pass finished, which is exactly the shape of request most exposed to
browser/proxy timeout risk.
"""
from __future__ import annotations

import asyncio
import logging
import sys
from datetime import date

from fastapi import APIRouter, Depends, HTTPException

from app.auth import CompanyContext, require_company
from app.business_context import (
    BusinessContext,
    Meta,
    load_business_context,
    save_business_context,
)
from app.business_context_refresh_runner import run_business_context_refresh_job
from app.db import business_context_refresh_state, start_business_context_refresh
from app.routes.team import _require_admin

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/v1/company", tags=["company"])

# Strong refs to in-flight background refresh tasks. asyncio holds only a weak
# reference to a bare create_task result, so without this the task can be
# garbage-collected mid-run and the row would be stuck 'generating'. The
# done-callback discards each task on completion (mirrors routes/ask.py).
_inflight_tasks: set[asyncio.Task] = set()


def _run_inline_for_tests() -> bool:
    """The TestClient does not keep the app's event loop alive between
    requests, so a fire-and-forget create_task would never run and a
    status-poll would spin forever — mirrors routes/ask.py's identical
    test-mode handling. A function (not an inline `"pytest" in sys.modules`
    check) so a test can monkeypatch it to exercise the real fire-and-forget
    branch without touching the actual `sys.modules` registry."""
    return "pytest" in sys.modules


def _stamp_user_edits(doc: BusinessContext) -> BusinessContext:
    """A human is asserting these values via the editor → every KNOWN leaf is
    src='user' (the authoritative provenance the agent must never overwrite).
    Unknown leaves are left as-is so they stay gap-fillable by the agent."""
    today = date.today().isoformat()

    def stamp_layer(layer) -> None:
        for attr, m in vars(layer).items():
            if isinstance(m, Meta) and m.is_known and not m.is_user_authoritative:
                setattr(layer, attr, Meta(
                    value=m.value, src="user", conf=m.conf or "high",
                    as_of=today, evidence=m.evidence,
                ))

    for layer_name in ("identity", "business_model", "product_value",
                       "market_competition", "goals_strategy"):
        stamp_layer(getattr(doc, layer_name))
    for seg in doc.users_segments.segments:
        stamp_layer(seg)
    for term in doc.vocabulary.terms:
        stamp_layer(term)
    return doc


@router.get("/business-context")
def get_business_context(company: CompanyContext = Depends(require_company)):
    doc = load_business_context(company.company_id)
    if doc is None:
        raise HTTPException(
            404, "Business context not built yet — run refresh or complete onboarding"
        )
    return doc.model_dump()


@router.put("/business-context")
def put_business_context(
    doc: BusinessContext, company: CompanyContext = Depends(require_company)
):
    _require_admin(company)
    saved = save_business_context(company.company_id, _stamp_user_edits(doc))
    return {"ok": True, "version": saved.version}


@router.post("/business-context/refresh")
async def refresh_business_context(
    company: CompanyContext = Depends(require_company),
):
    """Kick off (or no-op on top of) an async business-context refresh,
    returning immediately regardless of how long the underlying research pass
    takes.

    Singleton per tenant: `start_business_context_refresh` is the atomic
    guard (see app/db/business_context_refresh.py) — a second trigger while
    one is already live for this company is a no-op, not a new run and not an
    error, mirroring company_research_runs' "already researching" branch.
    """
    _require_admin(company)
    if not start_business_context_refresh(company.company_id):
        return {"ok": True, "status": "generating", "already_running": True}

    if _run_inline_for_tests():
        # Run the worker inline under pytest for deterministic results.
        # Production keeps the non-blocking create_task path below.
        await run_business_context_refresh_job(company.company_id)
        return {"ok": True, **business_context_refresh_state(company.company_id)}

    task = asyncio.create_task(
        run_business_context_refresh_job(company.company_id)
    )
    _inflight_tasks.add(task)
    task.add_done_callback(_inflight_tasks.discard)
    return {"ok": True, "status": "generating"}


@router.get("/business-context/refresh-status")
def get_business_context_refresh_status(
    company: CompanyContext = Depends(require_company),
):
    """`{status, error}` for the client to poll after triggering a refresh.
    status is one of idle/generating/done/error — see the migration for the
    column this reads. Open to any member, like the doc GET above."""
    return business_context_refresh_state(company.company_id)
