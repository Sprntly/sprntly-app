"""Tests for the Market Research agent."""
from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import patch

import pytest


@pytest.fixture
def facade(isolated_settings):
    from app.graph import GraphFacade
    return GraphFacade()


def _fake_client(company_row=None, product_row=None):
    class FakeQ:
        def __init__(self, rows): self._rows = rows
        def select(self, *_): return self
        def eq(self, *_a, **_k): return self
        def execute(self): return SimpleNamespace(data=self._rows)
    class FakeC:
        def table(self, name):
            if name == "companies":
                return FakeQ([company_row] if company_row else [])
            if name == "products":
                return FakeQ([product_row] if product_row else [])
            raise AssertionError(name)
    return FakeC()


_COMPANY = {"display_name": "Swayat AI", "industry": "B2B SaaS",
            "product_description": "Field service management for technicians",
            "business_type": "SaaS"}
_PRODUCT = {"name": "Swayat", "website": "https://swayat.com",
            "description": "Field ops"}


def test_company_profile_includes_primary_product(monkeypatch):
    from app.research import market

    monkeypatch.setattr(market, "require_client",
                        lambda: _fake_client(_COMPANY, _PRODUCT))
    p = market.company_profile("ent-A")
    assert p["display_name"] == "Swayat AI"
    assert p["product"]["website"] == "https://swayat.com"


def test_run_extracts_and_logs(facade, isolated_settings, monkeypatch):
    from app.research import market

    monkeypatch.setattr(market, "require_client",
                        lambda: _fake_client(_COMPANY, _PRODUCT))
    captured = {}

    def fake_search(*, system, user, meta_out=None, max_searches=None, **kw):
        captured["user"] = user
        if meta_out is not None:
            meta_out["input_tokens"] = 1234
        return "Users on g2.com praise routing; complain about offline sync."

    def fake_extract(f, eid, *, doc_name, text, agent, source_hint=None):
        captured["hint"] = source_hint
        assert agent == "market_research"
        return {"signals": 3, "themes": 2, "skipped": 0}

    with patch.object(market, "call_with_web_search", side_effect=fake_search), \
         patch.object(market, "extract_document", side_effect=fake_extract):
        out = market.run_market_research(facade, "ent-A")

    assert out == {"signals": 3, "themes": 2, "skipped": 0, "found": True}
    # research prompt grounded in onboarding profile
    assert "Swayat AI" in captured["user"] and "swayat.com" in captured["user"]
    assert "customer_voice" in captured["hint"]
    # configurable channel sweeps (§1c) — reddit/HN/linkedin targeted, subject substituted
    assert "site:reddit.com Swayat" in captured["user"]
    assert "site:news.ycombinator.com Swayat" in captured["user"]
    assert "site:linkedin.com Swayat" in captured["user"]

    logs = isolated_settings["supabase"].table("agent_decision_log").select("*") \
        .eq("enterprise_id", "ent-A").execute().data
    runs = [r for r in logs if r["decision_type"] == "research_run"]
    assert len(runs) == 1 and runs[0]["agent"] == "market_research"
    assert runs[0]["factors"]["search_tokens"] == 1234


def test_no_findings_skips_extraction(facade, monkeypatch):
    from app.research import market

    monkeypatch.setattr(market, "require_client",
                        lambda: _fake_client(_COMPANY, None))
    with patch.object(market, "call_with_web_search", return_value="NO_FINDINGS"), \
         patch.object(market, "extract_document") as fake_extract:
        out = market.run_market_research(facade, "ent-A")
    fake_extract.assert_not_called()
    assert out["found"] is False and out["signals"] == 0


def test_missing_company_raises(facade, monkeypatch):
    from app.research import market

    monkeypatch.setattr(market, "require_client", lambda: _fake_client(None))
    with pytest.raises(ValueError, match="Company not found"):
        market.run_market_research(facade, "ent-gone")
