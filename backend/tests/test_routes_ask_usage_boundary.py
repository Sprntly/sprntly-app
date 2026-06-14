"""Cross-tenant boundary tests for the Chat Agent usage route.

Checklist item "Chat Agent" + "Roles & Access" (cross-tenant leakage):
GET /v1/ask/usage must return ONLY the calling company's Q&A spend, never
another company's. The aggregation function itself is unit-tested in
test_qa_usage.py; this pins the ROUTE-level tenant scoping (require_company →
fetch_qa_usage(company.company_id)) end to end, including that a second
co-existing tenant's rows never leak in.
"""
from __future__ import annotations

import app.auth  # noqa: F401 — load app.config/app.auth into sys.modules

from tests._company_helpers import company_client, seed_company, supabase_bearer


def _log_qa_call(*, enterprise_id: str, agent: str = "qa", cost_usd: float,
                 input_tokens: int = 0, output_tokens: int = 0) -> None:
    """Seed an agent_decision_log row the way the gateway records a QA call."""
    import json

    from app.db.client import require_client

    require_client().table("agent_decision_log").insert(
        {
            "enterprise_id": enterprise_id,
            "agent": agent,
            "decision_type": "llm_call",
            "factors": json.dumps(
                {
                    "cost_usd": cost_usd,
                    "input_tokens": input_tokens,
                    "output_tokens": output_tokens,
                }
            ),
        }
    ).execute()


def test_usage_requires_auth(unauth_client, isolated_settings):
    assert unauth_client.get("/v1/ask/usage").status_code == 401


def test_usage_aggregates_only_own_company(isolated_settings, monkeypatch):
    ctx = company_client(monkeypatch)  # company A

    # Company A: two QA calls.
    _log_qa_call(enterprise_id=ctx.company_id, agent="qa", cost_usd=0.10,
                 input_tokens=100, output_tokens=20)
    _log_qa_call(enterprise_id=ctx.company_id, agent="qa-router", cost_usd=0.01,
                 input_tokens=10, output_tokens=2)

    # Company B (foreign tenant): a much larger spend that must NOT leak.
    other_cid = seed_company(user_id="intruder", slug="rival")
    _log_qa_call(enterprise_id=other_cid, agent="qa", cost_usd=99.0,
                 input_tokens=9999, output_tokens=9999)

    r = ctx.client.get("/v1/ask/usage")
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["calls"] == 2  # only A's two rows
    assert round(body["cost_usd"], 2) == 0.11  # 0.10 + 0.01, never 99.0
    assert body["input_tokens"] == 110
    assert set(body["by_agent"]) == {"qa", "qa-router"}


def test_usage_other_tenant_sees_only_its_own(isolated_settings, monkeypatch):
    """The mirror direction: the intruder reading usage gets ONLY its row."""
    ctx = company_client(monkeypatch)  # company A
    _log_qa_call(enterprise_id=ctx.company_id, agent="qa", cost_usd=5.0)

    other_cid = seed_company(user_id="intruder", slug="rival")
    _log_qa_call(enterprise_id=other_cid, agent="qa", cost_usd=0.25)

    rr = ctx.client.get("/v1/ask/usage", headers=supabase_bearer("intruder"))
    assert rr.status_code == 200
    body = rr.json()
    assert body["calls"] == 1
    assert round(body["cost_usd"], 2) == 0.25  # never sees A's 5.0


def test_usage_zero_for_company_with_no_calls(isolated_settings, monkeypatch):
    ctx = company_client(monkeypatch)
    r = ctx.client.get("/v1/ask/usage")
    assert r.status_code == 200
    body = r.json()
    assert body["calls"] == 0
    assert body["cost_usd"] == 0.0
    assert body["by_agent"] == {}
