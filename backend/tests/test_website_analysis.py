"""Onboarding website-analysis tests.

Covers analyze_website (the structured-inference flow) and the
POST /v1/onboarding/analyze-website route. ALL network (fetch_page) and the
gateway llm_call are mocked — no real HTTP, no Anthropic.

Asserts: structured shape from a mocked fetch + mocked gateway; graceful
degrade (no raise) on SSRF-blocked / no-URL / unreachable / LLM-error inputs;
persistence to companies.business_context; suggested_metrics {metric,description}
shape; never-fabricate pass-through (null model fields → unknown, not invented);
route require_company gating + tenant scoping; and (2026-09-03) the five fields
scraped for the onboarding steps that were cut down to name + website —
mission / portfolio / competitors / monetization / users_description — landing
on the raw companies/products columns Settings itself renders, gap-only.
"""
from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient

import app.onboarding.website_analysis as wa
from app.business_context import load_business_context
from tests.conftest import (
    _enable_supabase_bearer,
    _mint_supabase_token,
    _seed_company_membership,
)

_COMPANY_ID = "co-test"  # the id _seed_company_membership seeds


# --------------------------------------------------------------------------- #
# Helpers / fixtures
# --------------------------------------------------------------------------- #
def _llm_result(output):
    """A gateway.LLMResult carrying `output` (the structured dict)."""
    from app.graph.gateway import LLMResult

    return LLMResult(
        output=output, model="claude-sonnet-4-6", prompt_version="t",
        input_tokens=1, output_tokens=1, cache_read_input_tokens=0,
        cache_creation_input_tokens=0, cost_usd=0.0, latency_ms=1,
        stop_reason="end_turn",
    )


_FULL_OUTPUT = {
    "industry": "B2B SaaS",
    "sub_vertical": "field-service management",
    "business_type": "SaaS",
    "stage": "growth",
    "business_context": "Acme sells field-service management software to HVAC "
                        "contractors on a per-seat subscription.",
    "suggested_metrics": [
        {"metric": "Activation rate", "description": "Share of new accounts completing a first job."},
        {"metric": "Net revenue retention", "description": "Expansion minus churn across the base."},
        {"metric": "Seats per account", "description": "Average paid seats — the monetization unit."},
        {"metric": "Weekly active dispatchers", "description": "Core power-user engagement."},
    ],
    "provenance": "name + url given; industry/business_type/metrics inferred from site.",
}

# The same fixture, plus the five fields cut from the onboarding steps
# (2026-09-03) that are now scraped instead of typed.
_FULL_OUTPUT_WITH_ONBOARDING_FIELDS = {
    **_FULL_OUTPUT,
    "mission": "Keep every field technician's day running on time.",
    "portfolio": "Also ships a lightweight dispatch app for solo operators.",
    "competitors": ["ServiceTitan", "Housecall Pro", "ServiceTitan"],  # dup on purpose
    "monetization": "seat",
    "users_description": "Dispatchers and field technicians at HVAC contractors.",
}


def _seed_product(db, company_id: str, **columns):
    """A primary product row for `_fill_onboarding_gaps` to patch."""
    db.table("products").insert(
        {
            "id": f"prod-{company_id}",
            "company_id": company_id,
            "name": "Acme",
            "website": "https://acme.com",
            "is_primary": True,
            **columns,
        }
    ).execute()


@pytest.fixture
def seeded_company(isolated_settings):
    """A companies row so save/load_business_context can update + read it."""
    db = isolated_settings["supabase"]
    existing = db.table("companies").select("id").eq("id", _COMPANY_ID).execute().data
    if not existing:
        db.table("companies").insert({
            "id": _COMPANY_ID, "slug": "acme", "display_name": "Acme",
            "industry": "B2B SaaS", "product_description": "Field ops",
        }).execute()
    return db


@pytest.fixture
def company_client(isolated_settings, monkeypatch) -> TestClient:
    """Bearer-authed TestClient resolving company_id == 'co-test'."""
    import app.main as main_mod
    _enable_supabase_bearer(monkeypatch)
    _seed_company_membership(isolated_settings["supabase"])
    c = TestClient(main_mod.app)
    c.headers["Authorization"] = f"Bearer {_mint_supabase_token()}"
    return c


def _patch_fetch(monkeypatch, mapping):
    """Patch the module's fetch_page with an async stub honoring `mapping`
    (url-substring → text); '' for anything unmatched (a real miss). Also stubs
    the up-front SSRF guard to a no-op so the test never touches real DNS — the
    blocked-URL path has its own dedicated test."""
    async def fake_fetch(url, max_chars=50_000):
        for key, text in mapping.items():
            if key in url:
                return text[:max_chars]
        return ""
    monkeypatch.setattr(wa, "fetch_page", fake_fetch)
    monkeypatch.setattr(wa, "assert_public_url", lambda _u: None)


# --------------------------------------------------------------------------- #
# 1. Happy path — structured object from mocked fetch + mocked gateway
# --------------------------------------------------------------------------- #
def test_returns_structured_analysis(seeded_company, monkeypatch):
    _patch_fetch(monkeypatch, {"acme.com": "Acme — field service software. Pricing: $49/seat."})
    with patch.object(wa, "llm_call", return_value=_llm_result(_FULL_OUTPUT)) as m:
        out = wa.analyze_website(_COMPANY_ID, "https://acme.com")

    assert out["ok"] is True
    assert out["industry"] == "B2B SaaS"
    assert out["business_type"] == "SaaS"
    assert out["stage"] == "growth"
    assert out["sub_vertical"] == "field-service management"
    assert out["business_context"].startswith("Acme sells")
    assert len(out["suggested_metrics"]) == 4
    # The skill was bound on the single gateway call.
    assert m.call_args.kwargs["skill"] == "business-context"
    assert m.call_args.kwargs["json_schema"] is wa.SCHEMA
    assert m.call_count == 1


def test_business_context_uses_deep_model(seeded_company, monkeypatch):
    """Onboarding business-context inference is a DEEP, once-per-company,
    background pass that seeds everything downstream → DEEP_MODEL (opus)."""
    from app.llm import DEEP_MODEL

    _patch_fetch(monkeypatch, {"acme.com": "Acme field service software."})
    with patch.object(wa, "llm_call", return_value=_llm_result(_FULL_OUTPUT)) as m:
        wa.analyze_website(_COMPANY_ID, "https://acme.com")

    assert m.call_args.kwargs["model"] == DEEP_MODEL


def test_suggested_metrics_shape(seeded_company, monkeypatch):
    _patch_fetch(monkeypatch, {"acme.com": "Acme field service software."})
    with patch.object(wa, "llm_call", return_value=_llm_result(_FULL_OUTPUT)):
        out = wa.analyze_website(_COMPANY_ID, "https://acme.com")
    assert 4 <= len(out["suggested_metrics"]) <= 6
    for met in out["suggested_metrics"]:
        assert set(met) == {"metric", "description"}
        assert met["metric"] and isinstance(met["metric"], str)
        assert isinstance(met["description"], str)


def test_malformed_metrics_filtered(seeded_company, monkeypatch):
    """Junk metric entries (non-dict, empty name) are dropped, not fabricated."""
    out_obj = dict(_FULL_OUTPUT)
    out_obj["suggested_metrics"] = [
        {"metric": "Good", "description": "ok"},
        {"metric": "", "description": "blank name dropped"},
        "not-a-dict",
        {"description": "no metric key dropped"},
    ]
    _patch_fetch(monkeypatch, {"acme.com": "Acme."})
    with patch.object(wa, "llm_call", return_value=_llm_result(out_obj)):
        out = wa.analyze_website(_COMPANY_ID, "https://acme.com")
    assert out["suggested_metrics"] == [{"metric": "Good", "description": "ok"}]


# --------------------------------------------------------------------------- #
# 2. Persistence — structured context written to companies.business_context
# --------------------------------------------------------------------------- #
def test_persists_business_context(seeded_company, monkeypatch):
    _patch_fetch(monkeypatch, {"acme.com": "Acme field service software."})
    with patch.object(wa, "llm_call", return_value=_llm_result(_FULL_OUTPUT)):
        out = wa.analyze_website(_COMPANY_ID, "https://acme.com")

    assert out["business_context_version"] is not None
    doc = load_business_context(_COMPANY_ID)
    assert doc is not None
    assert doc.identity.industry.value == "B2B SaaS"
    assert doc.identity.industry.src == "inferred"  # web-derived provenance
    assert doc.business_model.model_type.value == "SaaS"
    assert doc.identity.website.value == "https://acme.com"
    assert any(s.url == "https://acme.com" for s in doc.meta.sources)


def test_persist_does_not_overwrite_user_fields(seeded_company, monkeypatch):
    """A user-authoritative leaf is preserved; the inference only fills gaps."""
    from app.business_context import BusinessContext, Meta, save_business_context

    doc = BusinessContext()
    doc.identity.industry = Meta(value="Healthcare", src="user", conf="high")
    save_business_context(_COMPANY_ID, doc)

    _patch_fetch(monkeypatch, {"acme.com": "Acme."})
    with patch.object(wa, "llm_call", return_value=_llm_result(_FULL_OUTPUT)):
        wa.analyze_website(_COMPANY_ID, "https://acme.com")

    after = load_business_context(_COMPANY_ID)
    assert after.identity.industry.value == "Healthcare"  # user value untouched
    assert after.identity.industry.src == "user"
    assert after.business_model.model_type.value == "SaaS"  # gap filled


# --------------------------------------------------------------------------- #
# 2b. The onboarding-column scrape (2026-09-03) — mission / portfolio /
#     competitors / monetization / users_description, written straight onto
#     companies/products, gap-only. Separate from `_persist_business_context`
#     above: that call only reaches the chat-facing BusinessContext doc, which
#     Settings never reads.
# --------------------------------------------------------------------------- #
def test_scraped_fields_returned_in_the_response(seeded_company, monkeypatch):
    _patch_fetch(monkeypatch, {"acme.com": "Acme — field service software."})
    with patch.object(wa, "llm_call", return_value=_llm_result(_FULL_OUTPUT_WITH_ONBOARDING_FIELDS)):
        out = wa.analyze_website(_COMPANY_ID, "https://acme.com")

    assert out["mission"] == "Keep every field technician's day running on time."
    assert out["portfolio"] == "Also ships a lightweight dispatch app for solo operators."
    # Deduped, order preserved, case-insensitive — the fixture repeats "ServiceTitan".
    assert out["competitors"] == ["ServiceTitan", "Housecall Pro"]
    assert out["monetization"] == "seat"
    assert out["users_description"] == "Dispatchers and field technicians at HVAC contractors."


def test_scraped_fields_fill_empty_company_and_product_columns(seeded_company, monkeypatch):
    """The whole point: a company that never opens Settings still has these
    filled in when it eventually does."""
    _seed_product(seeded_company, _COMPANY_ID)
    _patch_fetch(monkeypatch, {"acme.com": "Acme — field service software."})
    with patch.object(wa, "llm_call", return_value=_llm_result(_FULL_OUTPUT_WITH_ONBOARDING_FIELDS)):
        wa.analyze_website(_COMPANY_ID, "https://acme.com")

    company = seeded_company.table("companies").select("*").eq("id", _COMPANY_ID).execute().data[0]
    assert company["mission"] == "Keep every field technician's day running on time."
    assert company["portfolio"] == "Also ships a lightweight dispatch app for solo operators."
    assert company["competitors"] == ["ServiceTitan", "Housecall Pro"]

    product = seeded_company.table("products").select("*").eq("id", f"prod-{_COMPANY_ID}").execute().data[0]
    assert product["monetization"] == ["seat"]  # single-element array — matches the manual-entry shape
    assert product["users_description"] == "Dispatchers and field technicians at HVAC contractors."


def test_scrape_does_not_overwrite_typed_onboarding_fields(seeded_company, monkeypatch):
    """A person who already typed their mission (or anything else here) before
    the scrape lands — or before it ever ran — keeps exactly what they typed."""
    seeded_company.table("companies").update(
        {"mission": "Written by a human.", "competitors": ["HandTyped Inc"]}
    ).eq("id", _COMPANY_ID).execute()
    _seed_product(seeded_company, _COMPANY_ID, users_description="Also written by a human.")

    _patch_fetch(monkeypatch, {"acme.com": "Acme — field service software."})
    with patch.object(wa, "llm_call", return_value=_llm_result(_FULL_OUTPUT_WITH_ONBOARDING_FIELDS)):
        wa.analyze_website(_COMPANY_ID, "https://acme.com")

    company = seeded_company.table("companies").select("*").eq("id", _COMPANY_ID).execute().data[0]
    assert company["mission"] == "Written by a human."
    assert company["competitors"] == ["HandTyped Inc"]
    # portfolio was empty → still filled, proving the guard is per-field, not
    # "skip the whole row because something on it is user-authored".
    assert company["portfolio"] == "Also ships a lightweight dispatch app for solo operators."

    product = seeded_company.table("products").select("*").eq("id", f"prod-{_COMPANY_ID}").execute().data[0]
    assert product["users_description"] == "Also written by a human."
    assert product["monetization"] == ["seat"]  # empty → still filled


def test_scrape_names_no_competitors_by_default(seeded_company, monkeypatch):
    """The never-fabricate rule biting hardest on the new fields: a site with no
    comparison page names nothing, and nothing is invented from category."""
    sparse = {**_FULL_OUTPUT, "mission": None, "portfolio": None, "competitors": [],
              "monetization": None, "users_description": None}
    _seed_product(seeded_company, _COMPANY_ID)
    _patch_fetch(monkeypatch, {"acme.com": "Acme field service software."})
    with patch.object(wa, "llm_call", return_value=_llm_result(sparse)):
        out = wa.analyze_website(_COMPANY_ID, "https://acme.com")

    assert out["mission"] is None
    assert out["competitors"] == []
    assert out["monetization"] is None
    company = seeded_company.table("companies").select("*").eq("id", _COMPANY_ID).execute().data[0]
    # Untouched — nothing to fill, so no write at all.
    assert not company.get("mission")
    assert not (company.get("competitors") or [])


def test_scrape_drops_a_monetization_value_the_frontend_does_not_render():
    """Guards the schema's own enum: a value outside MONETIZATION_VALUES (a
    forced-JSON slip, or a caller bypassing the schema entirely) must not reach
    the column — it would fill `products.monetization` with a value Settings'
    chip picker can never show as selected."""
    assert wa._normalize_monetization("seat") == "seat"
    assert wa._normalize_monetization("enterprise-license") is None
    assert wa._normalize_monetization(None) is None


def test_scrape_has_nothing_to_patch_when_the_company_has_no_product_yet(seeded_company, monkeypatch):
    """No primary product row (a company mid-signup, before its product was
    created) → the company-level fields still fill; the product-level ones are
    skipped rather than raising."""
    _patch_fetch(monkeypatch, {"acme.com": "Acme — field service software."})
    with patch.object(wa, "llm_call", return_value=_llm_result(_FULL_OUTPUT_WITH_ONBOARDING_FIELDS)):
        wa.analyze_website(_COMPANY_ID, "https://acme.com")  # must not raise

    company = seeded_company.table("companies").select("*").eq("id", _COMPANY_ID).execute().data[0]
    assert company["mission"] == "Keep every field technician's day running on time."


def test_scrape_write_failure_does_not_break_the_analysis_result(seeded_company, monkeypatch):
    """A DB error while filling the onboarding columns is swallowed — the
    analysis the caller is waiting on must still come back."""
    _patch_fetch(monkeypatch, {"acme.com": "Acme — field service software."})
    monkeypatch.setattr(
        wa, "_primary_product_gaps",
        lambda cid: (_ for _ in ()).throw(RuntimeError("db down")),
    )
    with patch.object(wa, "llm_call", return_value=_llm_result(_FULL_OUTPUT_WITH_ONBOARDING_FIELDS)):
        out = wa.analyze_website(_COMPANY_ID, "https://acme.com")
    assert out["ok"] is True
    assert out["mission"] == "Keep every field technician's day running on time."


# --------------------------------------------------------------------------- #
# 3. Never-fabricate — null model fields pass through as unknown
# --------------------------------------------------------------------------- #
def test_never_fabricate_null_fields_pass_through(seeded_company, monkeypatch):
    sparse = {
        "industry": None, "sub_vertical": None, "business_type": None,
        "stage": None, "business_context": "", "suggested_metrics": [],
        "provenance": "site too thin to infer anything",
    }
    _patch_fetch(monkeypatch, {"acme.com": "Coming soon."})
    with patch.object(wa, "llm_call", return_value=_llm_result(sparse)):
        out = wa.analyze_website(_COMPANY_ID, "https://acme.com")

    assert out["ok"] is True  # the pass succeeded; it just found little
    assert out["industry"] is None
    assert out["business_type"] is None
    assert out["stage"] is None
    assert out["suggested_metrics"] == []
    # Nothing fabricated → nothing inferred persisted for those leaves.
    doc = load_business_context(_COMPANY_ID)
    if doc is not None:
        assert not doc.identity.industry.is_known
        assert not doc.business_model.model_type.is_known


# --------------------------------------------------------------------------- #
# 4. Graceful degrade — never raises
# --------------------------------------------------------------------------- #
def test_no_url_graceful(seeded_company, monkeypatch):
    # No fetch, no LLM should be reached for an empty URL.
    with patch.object(wa, "llm_call", side_effect=AssertionError("must not call LLM")):
        out = wa.analyze_website(_COMPANY_ID, "")
    assert out["ok"] is False
    assert out["reason"] == "no_url"
    assert out["suggested_metrics"] == []
    assert out["business_context"] == ""


def test_ssrf_blocked_url_graceful(seeded_company, monkeypatch):
    from app.net_guard import UnsafeURLError

    def blocked(_url):
        raise UnsafeURLError("non-public")

    monkeypatch.setattr(wa, "assert_public_url", blocked)
    with patch.object(wa, "llm_call", side_effect=AssertionError("must not call LLM")):
        out = wa.analyze_website(_COMPANY_ID, "http://169.254.169.254/latest/meta-data/")
    assert out["ok"] is False
    assert out["reason"] == "blocked_url"
    assert out["suggested_metrics"] == []


def test_unreachable_or_empty_graceful(seeded_company, monkeypatch):
    # URL passes the up-front guard, but every page fetch returns empty (host up,
    # no readable content) → no corpus → graceful, no LLM call.
    _patch_fetch(monkeypatch, {})
    with patch.object(wa, "llm_call", side_effect=AssertionError("must not call LLM")):
        out = wa.analyze_website(_COMPANY_ID, "https://nope.example.com")
    assert out["ok"] is False
    assert out["reason"] == "unreachable_or_empty"


def test_llm_failure_graceful(seeded_company, monkeypatch):
    _patch_fetch(monkeypatch, {"acme.com": "Acme field service software."})
    with patch.object(wa, "llm_call", side_effect=RuntimeError("model down")):
        out = wa.analyze_website(_COMPANY_ID, "https://acme.com")
    assert out["ok"] is False
    assert out["reason"] == "analysis_failed"
    assert out["suggested_metrics"] == []


def test_subpage_failure_non_fatal(seeded_company, monkeypatch):
    """Homepage succeeds, pricing/about fail → still a full result (homepage is
    enough)."""
    # Only the homepage matches; /pricing and /about return ''.
    async def fake_fetch(url, max_chars=50_000):
        if url.rstrip("/").endswith("acme.com"):
            return "Acme homepage — field service software."
        return ""  # sub-pages unreachable
    monkeypatch.setattr(wa, "fetch_page", fake_fetch)
    monkeypatch.setattr(wa, "assert_public_url", lambda _u: None)
    with patch.object(wa, "llm_call", return_value=_llm_result(_FULL_OUTPUT)):
        out = wa.analyze_website(_COMPANY_ID, "https://acme.com")
    assert out["ok"] is True
    assert out["industry"] == "B2B SaaS"


# --------------------------------------------------------------------------- #
# 5. Bounded fetch — corpus capped at MAX_TOTAL_CHARS
# --------------------------------------------------------------------------- #
def test_corpus_capped(monkeypatch):
    big = "x" * 100_000
    captured = {}

    def fake_build(url, facts, corpus):
        captured["len"] = len(corpus)
        return corpus
    _patch_fetch(monkeypatch, {"acme.com": big})
    monkeypatch.setattr(wa, "_build_user_prompt", fake_build)
    monkeypatch.setattr(wa, "_company_facts", lambda cid: {})
    monkeypatch.setattr(wa, "_persist_business_context", lambda *a, **k: 1)
    with patch.object(wa, "llm_call", return_value=_llm_result(_FULL_OUTPUT)):
        wa.analyze_website(_COMPANY_ID, "https://acme.com")
    assert captured["len"] <= wa.MAX_TOTAL_CHARS


# --------------------------------------------------------------------------- #
# 6. Route — require_company gating + tenant scoping
# --------------------------------------------------------------------------- #
def test_route_requires_auth(company_client):
    # Strip the bearer → require_company → 401.
    company_client.headers.pop("Authorization", None)
    r = company_client.post("/v1/onboarding/analyze-website", json={"url": "https://acme.com"})
    assert r.status_code == 401


# The route is now fire-and-forget: POST persists a `generating` job in
# website_analysis_jobs and (under pytest) runs the worker INLINE, returning
# {job_id, status}. The full analysis dict is read from
# GET /v1/onboarding/analyze-website/{job_id} as `result`. The worker calls the
# same analyze_website pipeline, patched at its source module (the runner imports
# it from app.onboarding.website_analysis) so we exercise the real job lifecycle.
def test_route_returns_job_id_and_persists_generating(company_client, monkeypatch):
    """POST returns {job_id, status} and persists a per-tenant job row."""
    captured = {}

    def fake_analyze(company_id, url):
        captured["company_id"] = company_id
        captured["url"] = url
        return {"ok": True, "reason": None, "url": url, "industry": "B2B SaaS",
                "business_type": "SaaS", "stage": None, "sub_vertical": None,
                "business_context": "brief", "suggested_metrics": [],
                "provenance": "p", "business_context_version": 2}

    monkeypatch.setattr(
        "app.website_analysis_job_runner.analyze_website", fake_analyze
    )
    r = company_client.post(
        "/v1/onboarding/analyze-website", json={"url": "https://acme.com"}
    )
    assert r.status_code == 200
    body = r.json()
    assert isinstance(body["job_id"], int)
    # Under pytest the worker runs inline, so the row is already ready.
    assert body["status"] in ("generating", "ready")
    # Tenant scoping: analyze_website is called with the CALLER's company_id.
    assert captured["company_id"] == _COMPANY_ID
    assert captured["url"] == "https://acme.com"

    from app.db import get_analysis_job

    row = get_analysis_job(body["job_id"])
    assert row is not None
    assert row["company_id"] == _COMPANY_ID


def test_route_get_walks_generating_to_ready_with_same_shape(
    company_client, monkeypatch
):
    """The worker fills `result`; GET returns the SAME analyze_website dict the
    old synchronous POST body carried (so setWebsiteAnalysis(result) is
    unchanged)."""
    analysis = {
        "ok": True, "reason": None, "url": "https://acme.com",
        "industry": "B2B SaaS", "business_type": "SaaS", "stage": "growth",
        "sub_vertical": "field-service", "business_context": "brief",
        "suggested_metrics": [{"metric": "Activation", "description": "first job"}],
        "provenance": "p", "business_context_version": 3,
    }
    monkeypatch.setattr(
        "app.website_analysis_job_runner.analyze_website",
        lambda cid, url: analysis,
    )
    start = company_client.post(
        "/v1/onboarding/analyze-website", json={"url": "https://acme.com"}
    ).json()
    r = company_client.get(f"/v1/onboarding/analyze-website/{start['job_id']}")
    assert r.status_code == 200
    body = r.json()
    assert body["status"] == "ready"
    assert body["error"] is None
    # The GET's `result` is the exact analyze_website dict.
    assert body["result"] == analysis


def test_route_degrades_gracefully_with_ok_false(company_client, monkeypatch):
    """A blocked URL still resolves to a ready job carrying ok:false (UI falls
    back to manual entry rather than handling a request failure)."""
    degraded = {"ok": False, "reason": "blocked_url", "url": "http://169.254.169.254/",
                "industry": None, "business_type": None, "stage": None,
                "sub_vertical": None, "business_context": "",
                "suggested_metrics": [], "provenance": "blocked_url"}
    monkeypatch.setattr(
        "app.website_analysis_job_runner.analyze_website",
        lambda cid, url: degraded,
    )
    start = company_client.post(
        "/v1/onboarding/analyze-website",
        json={"url": "http://169.254.169.254/"},
    ).json()
    r = company_client.get(f"/v1/onboarding/analyze-website/{start['job_id']}")
    assert r.status_code == 200
    body = r.json()
    assert body["status"] == "ready"
    assert body["result"]["ok"] is False
    assert body["result"]["reason"] == "blocked_url"


def test_route_worker_failure_marks_error_and_does_not_crash(
    company_client, monkeypatch
):
    """An unexpected failure inside the analysis marks the job `error` (best-
    effort) and the worker never propagates the exception."""
    def _boom(company_id, url):  # noqa: ARG001
        raise RuntimeError("kaboom")

    monkeypatch.setattr("app.website_analysis_job_runner.analyze_website", _boom)
    start = company_client.post(
        "/v1/onboarding/analyze-website", json={"url": "https://acme.com"}
    ).json()
    r = company_client.get(f"/v1/onboarding/analyze-website/{start['job_id']}")
    assert r.status_code == 200
    body = r.json()
    assert body["status"] == "error"
    assert "kaboom" in (body["error"] or "")


def test_route_get_nonexistent_returns_404(company_client):
    r = company_client.get("/v1/onboarding/analyze-website/999999")
    assert r.status_code == 404


def test_route_get_foreign_job_returns_404(company_client, monkeypatch, isolated_settings):
    """A job belonging to another company is not readable (404, no disclosure)."""
    monkeypatch.setattr(
        "app.website_analysis_job_runner.analyze_website",
        lambda cid, url: {"ok": True, "reason": None, "url": url,
                          "industry": None, "business_type": None, "stage": None,
                          "sub_vertical": None, "business_context": "",
                          "suggested_metrics": [], "provenance": "p"},
    )
    start = company_client.post(
        "/v1/onboarding/analyze-website", json={"url": "https://acme.com"}
    ).json()
    # Re-point the job row at a different company → the caller can't read it.
    isolated_settings["supabase"].table("companies").insert({
        "id": "other-co", "slug": "other", "display_name": "Other",
    }).execute()
    isolated_settings["supabase"].table("website_analysis_jobs").update(
        {"company_id": "other-co"}
    ).eq("id", start["job_id"]).execute()
    r = company_client.get(f"/v1/onboarding/analyze-website/{start['job_id']}")
    assert r.status_code == 404
