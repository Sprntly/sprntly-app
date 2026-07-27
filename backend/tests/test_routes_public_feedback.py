"""Stored public-feedback reports: the artifacts listing entry + the re-serve
route (GET /v1/public-feedback/reports/{id}).

Mirrors test_routes_artifacts.py's fixture style: `company_client` gives a
JWT-authed TestClient with a seeded company + membership; the
public_feedback_runs table is added on top of conftest's base fake schema and
rows are seeded through the fake Supabase client.
"""
from __future__ import annotations

import pytest


from tests._company_helpers import company_client, seed_company, supabase_bearer

# public_feedback_runs lives in conftest's base fake schema (the artifacts
# aggregator reads it unconditionally). The aggregator also reads prototypes,
# which is NOT in the base schema — reuse test_routes_artifacts's DDL for it.
from tests import _fake_supabase
from tests.test_routes_artifacts import _PROTOTYPE_DDL


@pytest.fixture
def runs_env(isolated_settings):
    _fake_supabase.get_fake_db().executescript(_PROTOTYPE_DDL)
    yield


def _seed_run(company_id: str, *, question="what are people saying?",
              window_label="Public feedback · July 2026",
              html="<!DOCTYPE html><html><body>report</body></html>") -> int:
    from app.db.client import require_client
    resp = require_client().table("public_feedback_runs").insert({
        "company_id": company_id,
        "question": question,
        "window_label": window_label,
        "records": [{"verbatim": "x", "category": "product"}],
        "metadata": {"totals": {"collected": 1}},
        "html": html,
    }).execute()
    return resp.data[0]["id"]


def test_report_served_by_id(runs_env, monkeypatch):
    ctx = company_client(monkeypatch)
    run_id = _seed_run(ctx.company_id)
    r = ctx.client.get(f"/v1/public-feedback/reports/{run_id}", headers=ctx.headers)
    assert r.status_code == 200
    body = r.json()
    assert body["id"] == run_id
    assert body["window_label"] == "Public feedback · July 2026"
    assert body["html"].startswith("<!DOCTYPE html>")


def test_foreign_report_404s_without_existence_leak(runs_env, monkeypatch):
    ctx = company_client(monkeypatch)
    other_cid = seed_company(user_id="other-user", slug="rival-co")
    foreign_id = _seed_run(other_cid)
    r = ctx.client.get(f"/v1/public-feedback/reports/{foreign_id}", headers=ctx.headers)
    assert r.status_code == 404
    # unknown id: identical response
    r2 = ctx.client.get("/v1/public-feedback/reports/999999", headers=ctx.headers)
    assert r2.status_code == 404
    assert r.json() == r2.json()


def test_artifacts_listing_includes_reports(runs_env, monkeypatch):
    ctx = company_client(monkeypatch)
    run_id = _seed_run(ctx.company_id)
    r = ctx.client.get("/v1/artifacts?dataset=acme", headers=ctx.headers)
    assert r.status_code == 200
    reports = [a for a in r.json()["artifacts"] if a["type"] == "report"]
    assert len(reports) == 1
    item = reports[0]
    assert item["id"] == run_id
    assert item["title"] == "Public feedback · July 2026"
    assert item["open"] == {"report_id": run_id}
    assert item["source"]["question"] == "what are people saying?"
    # the document-sized html stays OUT of the listing payload
    assert "html" not in item


def test_artifacts_listing_excludes_foreign_reports(runs_env, monkeypatch):
    ctx = company_client(monkeypatch)
    other_cid = seed_company(user_id="other-user", slug="rival-co")
    _seed_run(other_cid)
    r = ctx.client.get("/v1/artifacts?dataset=acme", headers=ctx.headers)
    assert r.status_code == 200
    assert [a for a in r.json()["artifacts"] if a["type"] == "report"] == []
