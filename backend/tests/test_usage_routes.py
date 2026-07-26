"""Tests for GET /v1/admin/usage/{summary,export.csv}.

The rollup itself is a Postgres function, so these tests stub the RPC result
(via `FakeSupabaseClient.rpc_returns`) and assert on what the route does with
it: the re-slicing into totals / daily series / breakdowns, the gap-filling of
empty days, the admin gate, and the CSV shape.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from tests._fake_supabase import FakeSupabaseClient


def _today() -> str:
    return datetime.now(timezone.utc).date().isoformat()


def _yesterday() -> str:
    return (datetime.now(timezone.utc).date() - timedelta(days=1)).isoformat()


def _row(**over):
    base = {
        "day": _today(),
        "feature": "prd",
        "operation": "generate",
        "model": "claude-sonnet-4-6",
        "key_mode": "customer",
        "calls": 1,
        "failed_calls": 0,
        "input_tokens": 1000,
        "output_tokens": 500,
        "cache_creation_input_tokens": 0,
        "cache_read_input_tokens": 0,
        "est_cost_usd": 0.0105,
    }
    base.update(over)
    return base


@pytest.fixture(autouse=True)
def _clean_rpc():
    FakeSupabaseClient.rpc_returns = {}
    FakeSupabaseClient.rpc_calls = []
    yield
    FakeSupabaseClient.rpc_returns = {}
    FakeSupabaseClient.rpc_calls = []


def test_summary_aggregates_totals_and_breakdowns(tenant_client):
    env = tenant_client.make("usage-co")
    FakeSupabaseClient.rpc_returns["llm_usage_summary"] = [
        _row(),
        _row(feature="design_agent", operation="generate", est_cost_usd=0.25,
             input_tokens=4000, output_tokens=2000, calls=2),
        _row(feature="chat", model="claude-opus-4-7", key_mode="customer",
             est_cost_usd=0.05, calls=3, failed_calls=1),
    ]

    resp = env.client.get("/v1/admin/usage/summary?days=7")
    assert resp.status_code == 200
    body = resp.json()

    assert body["cost_basis"] == "estimated_from_tokens"
    assert body["totals"]["calls"] == 6
    assert body["totals"]["failed_calls"] == 1
    assert body["totals"]["input_tokens"] == 6000
    assert body["totals"]["est_cost_usd"] == pytest.approx(0.3105)

    # Breakdowns are ordered by spend, largest first.
    assert [b["feature"] for b in body["by_feature"]] == [
        "design_agent", "chat", "prd",
    ]
    assert {b["model"] for b in body["by_model"]} == {
        "claude-sonnet-4-6", "claude-opus-4-7",
    }
    assert body["scope"] == "customer_key"


def test_summary_gap_fills_days_with_no_usage(tenant_client):
    env = tenant_client.make("usage-gaps")
    FakeSupabaseClient.rpc_returns["llm_usage_summary"] = [_row(day=_today())]

    body = env.client.get("/v1/admin/usage/summary?days=7").json()
    daily = body["daily"]

    # Every calendar day in the window is present, in order, so a quiet week
    # cannot be compressed into a misleading continuous line.
    assert len(daily) == 7
    assert [d["day"] for d in daily] == sorted(d["day"] for d in daily)
    assert daily[-1]["day"] == _today()
    assert daily[-1]["est_cost_usd"] == pytest.approx(0.0105)
    assert daily[0]["est_cost_usd"] == 0
    assert daily[0]["calls"] == 0


def test_summary_scopes_rpc_to_the_calling_company(tenant_client):
    env = tenant_client.make("usage-scoped")
    FakeSupabaseClient.rpc_returns["llm_usage_summary"] = []

    env.client.get("/v1/admin/usage/summary?days=30&tz=America/New_York")

    fn, params = FakeSupabaseClient.rpc_calls[-1]
    assert fn == "llm_usage_summary"
    assert params["p_company_id"] == env.company_id
    assert params["p_tz"] == "America/New_York"


def test_summary_is_empty_not_an_error_when_there_is_no_usage(tenant_client):
    env = tenant_client.make("usage-empty")
    FakeSupabaseClient.rpc_returns["llm_usage_summary"] = []

    body = env.client.get("/v1/admin/usage/summary?days=30").json()
    assert body["totals"]["calls"] == 0
    assert body["by_feature"] == []
    assert len(body["daily"]) == 30


@pytest.mark.parametrize("days", [0, 366, -1])
def test_summary_rejects_out_of_range_windows(tenant_client, days):
    env = tenant_client.make(f"usage-range-{abs(days)}")
    assert env.client.get(f"/v1/admin/usage/summary?days={days}").status_code == 422


def test_summary_requires_authentication(unauth_client):
    assert unauth_client.get("/v1/admin/usage/summary").status_code in (401, 403)


def test_summary_forbidden_for_non_admin_member(tenant_client, monkeypatch):
    """Spend stays with the people who administer the company."""
    env = tenant_client.make("usage-role")
    from app.db.client import require_client

    require_client().table("company_members").update({"role": "member"}).eq(
        "company_id", env.company_id
    ).eq("user_id", env.user_id).execute()

    assert env.client.get("/v1/admin/usage/summary").status_code == 403


def test_csv_export_has_a_header_and_one_row_per_group(tenant_client):
    env = tenant_client.make("usage-csv")
    FakeSupabaseClient.rpc_returns["llm_usage_summary"] = [
        _row(),
        _row(day=_yesterday(), feature="chat"),
    ]

    resp = env.client.get("/v1/admin/usage/export.csv?days=7")
    assert resp.status_code == 200
    assert resp.headers["content-type"].startswith("text/csv")
    assert "attachment" in resp.headers["content-disposition"]

    lines = [ln for ln in resp.text.splitlines() if ln.strip()]
    assert lines[0].startswith("day,feature,operation,model,key_mode,")
    assert len(lines) == 3  # header + 2 rows


def test_summary_counts_only_the_customers_own_key(tenant_client):
    """Platform-key spend is Sprntly's cost, not the customer's bill.

    A workspace must never see a figure inflated by calls we paid for — so
    'platform' and 'unknown' rows are dropped before anything is totalled.
    """
    env = tenant_client.make("usage-keymode")
    FakeSupabaseClient.rpc_returns["llm_usage_summary"] = [
        _row(key_mode="customer", est_cost_usd=1.00, calls=1),
        _row(key_mode="platform", est_cost_usd=99.00, calls=50, feature="chat"),
        # Backfilled history: which key paid was never recorded.
        _row(key_mode="unknown", est_cost_usd=500.00, calls=900, feature="prd"),
    ]

    body = env.client.get("/v1/admin/usage/summary?days=30").json()

    assert body["scope"] == "customer_key"
    assert body["totals"]["est_cost_usd"] == pytest.approx(1.00)
    assert body["totals"]["calls"] == 1
    # The excluded spend must not leak through any breakdown either.
    assert [b["feature"] for b in body["by_feature"]] == ["prd"]
    assert sum(d["est_cost_usd"] for d in body["daily"]) == pytest.approx(1.00)


def test_summary_is_empty_when_all_usage_is_on_the_platform_key(tenant_client):
    """A workspace with no key of its own owes nothing, so it sees nothing."""
    env = tenant_client.make("usage-platform-only")
    FakeSupabaseClient.rpc_returns["llm_usage_summary"] = [
        _row(key_mode="platform", est_cost_usd=42.00, calls=100),
    ]

    body = env.client.get("/v1/admin/usage/summary?days=30").json()
    assert body["totals"]["calls"] == 0
    assert body["totals"]["est_cost_usd"] == 0
    assert body["by_feature"] == []


def test_csv_export_is_customer_key_only(tenant_client):
    env = tenant_client.make("usage-csv-keymode")
    FakeSupabaseClient.rpc_returns["llm_usage_summary"] = [
        _row(key_mode="customer"),
        _row(key_mode="platform", feature="chat"),
    ]
    resp = env.client.get("/v1/admin/usage/export.csv?days=7")
    lines = [ln for ln in resp.text.splitlines() if ln.strip()]
    assert len(lines) == 2  # header + the one customer row
    assert "platform" not in resp.text
