"""Tenant-isolation guards for the two surfaces the 2026-06-20 audit found
unscoped: the multi-agent doc/run reads and the pipeline routes.

Both relied on app-layer scoping that wasn't there (the backend uses the
service-role Supabase key, so RLS is bypassed — the app layer is the only
tenant boundary):

* `GET /v1/multi-agent/doc/{doc_id}` looked up a row by a SEQUENTIAL integer id
  with no ownership check — a trivially enumerable cross-tenant IDOR. The run
  reads (`/{run_id}`, `/{run_id}/docs`) were unscoped too.
* The pipeline routes were gated only by the non-tenant `require_session` and
  passed a client-supplied `dataset` slug straight through.

`multi_agent_docs` has no `company_id` column, so the fix binds via the doc's
`brief_id` → brief → dataset → company chain (`require_owned_brief`); pipeline
switches to `require_company` + `require_owned_dataset`.
"""
from __future__ import annotations

import pathlib
import uuid

import pytest

from app.db import multi_agent_docs
from app.routes import multi_agent as ma


def _seed_company(sb, slug: str) -> str:
    cid = uuid.uuid4().hex
    sb.table("companies").insert(
        {"id": cid, "slug": slug, "display_name": slug.title()}
    ).execute()
    return cid


def _save_brief(db, dataset: str) -> int:
    return db.save_brief(dataset, "W", {"insights": []}, schema_version=1)


def test_assert_run_owned_rejects_foreign_tenant(isolated_settings):
    sb = isolated_settings["supabase"]
    db = isolated_settings["db"]
    a = _seed_company(sb, "company-a")
    b = _seed_company(sb, "company-b")
    brief_id = _save_brief(db, "company-a")
    run_id = "run-" + uuid.uuid4().hex
    multi_agent_docs.start_doc(
        brief_id=brief_id, insight_index=0, prd_id=None,
        doc_type="technical_design", title="t", run_id=run_id,
    )

    # Owner resolves to the run's docs.
    docs = ma._assert_run_owned(run_id, a)
    assert docs and docs[0]["brief_id"] == brief_id

    # Foreign tenant → 404 (no existence disclosure), not the docs.
    with pytest.raises(Exception) as ei:
        ma._assert_run_owned(run_id, b)
    assert getattr(ei.value, "status_code", None) == 404

    # Unknown run → empty (still initializing / standard mode); nothing to leak.
    assert ma._assert_run_owned("no-such-run", b) == []


def test_single_doc_guard_binds_via_brief(isolated_settings):
    """The /doc/{doc_id} guard 404s a foreign tenant even though doc_id is a
    guessable sequential integer with no company_id on the row."""
    from app.deps.ownership import require_owned_brief

    sb = isolated_settings["supabase"]
    db = isolated_settings["db"]
    a = _seed_company(sb, "company-a")
    b = _seed_company(sb, "company-b")
    brief_id = _save_brief(db, "company-a")
    doc_id = multi_agent_docs.start_doc(
        brief_id=brief_id, insight_index=0, prd_id=None,
        doc_type="qa_test_cases", title="t",
    )
    doc = multi_agent_docs.get_doc(doc_id)
    assert doc["brief_id"] == brief_id

    # The exact guard the route runs after fetching the doc.
    assert require_owned_brief(doc["brief_id"], a)["id"] == brief_id
    with pytest.raises(Exception) as ei:
        require_owned_brief(doc["brief_id"], b)
    assert getattr(ei.value, "status_code", None) == 404


def test_pipeline_routes_are_company_scoped():
    """Source guard: pipeline must not use the non-tenant require_session, and
    every route must gate on dataset ownership."""
    src = (
        pathlib.Path(__file__).resolve().parent.parent / "app" / "routes" / "pipeline.py"
    ).read_text()
    assert "require_session" not in src, "pipeline must not use non-tenant require_session"
    assert "require_company" in src
    # Three routes (run / status / runs) — each must call require_owned_dataset.
    assert src.count("require_owned_dataset(") >= 3, (
        "each pipeline route must gate on dataset ownership"
    )
