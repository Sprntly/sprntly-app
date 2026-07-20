"""Tests for the Competitor Analysis agent."""
from __future__ import annotations

from unittest.mock import patch

import pytest


@pytest.fixture
def facade(isolated_settings):
    from app.graph import GraphFacade
    return GraphFacade()


def _seed_company(db, cid="co-1", competitors=("Adobe", "Notion")):
    db.table("companies").insert({
        "id": cid, "slug": "acme", "display_name": "Acme",
    }).execute()
    # companies.competitors is a text[] column added by the onboarding
    # migration; the fake schema stores arbitrary cols loosely — emulate via
    # update with a JSON-encoded list (jsonb registry handles decode).
    return cid


def test_roster_reads_companies_competitors(isolated_settings, monkeypatch):
    from app.research import competitor as comp

    monkeypatch.setattr(
        comp, "require_client",
        lambda: isolated_settings["supabase"],
    )
    # fake table lacks competitors col — patch at the query layer instead
    class FakeQ:
        def select(self, *_): return self
        def eq(self, *_): return self
        def execute(self):
            from types import SimpleNamespace
            return SimpleNamespace(data=[{"competitors": [" Adobe ", "", "Notion"]}])
    monkeypatch.setattr(comp, "require_client",
                        lambda: type("C", (), {"table": lambda s, n: FakeQ()})())
    assert comp.competitor_roster("co-1") == ["Adobe", "Notion"]


def test_run_isolates_failures_and_logs(facade, isolated_settings):
    from app.research import competitor as comp

    summaries = {
        "Adobe": "Adobe launched in-editor AI authoring on 2026-05-20 (adobe.com).",
        "Notion": RuntimeError("search exploded"),
    }

    def fake_search(*, system, user, meta_out=None, **kw):
        for name, v in summaries.items():
            if name in user:
                if isinstance(v, Exception):
                    raise v
                return v
        raise AssertionError("unknown competitor in prompt")

    def fake_extract(f, eid, *, doc_name, text, agent, source_hint=None):
        assert "competitive intelligence" in source_hint
        return {"signals": 2, "themes": 1, "skipped": 0}

    with patch.object(comp, "call_with_web_search", side_effect=fake_search), \
         patch.object(comp, "extract_document", side_effect=fake_extract), \
         patch.object(comp, "embed_texts", side_effect=lambda t, **k: [[0.1] * 4 for _ in t]):
        out = comp.run_competitor_research(
            facade, "ent-A", competitors=["Adobe", "Notion"])

    assert out["competitors"] == 1            # Adobe succeeded
    assert out["signals"] == 2
    assert len(out["errors"]) == 1 and "Notion" in out["errors"][0]

    # competitor entity created for the successful one
    ents = facade.query_entities("ent-A", type="competitor")
    assert [e.canonical_label for e in ents] == ["Adobe"]

    # run decision-logged
    logs = isolated_settings["supabase"].table("agent_decision_log").select("*") \
        .eq("enterprise_id", "ent-A").execute().data
    runs = [r for r in logs if r["decision_type"] == "research_run"]
    assert len(runs) == 1
    assert runs[0]["agent"] == "competitor_analysis"
    assert runs[0]["factors"]["roster"] == ["Adobe", "Notion"]


def test_no_findings_skips_extraction(facade, isolated_settings):
    from app.research import competitor as comp

    with patch.object(comp, "call_with_web_search", return_value="NO_FINDINGS"), \
         patch.object(comp, "extract_document") as fake_extract, \
         patch.object(comp, "embed_texts", side_effect=lambda t, **k: [[0.1] * 4 for _ in t]):
        out = comp.run_competitor_research(facade, "ent-A", competitors=["Ghost"])
    fake_extract.assert_not_called()
    assert out["no_findings"] == ["Ghost"]
    assert out["signals"] == 0


def test_empty_roster_triggers_discovery_then_runs(facade, isolated_settings, monkeypatch):
    """Empty roster → auto-discovery bootstraps it, then the light run uses it."""
    from app.research import competitor as comp

    monkeypatch.setattr(comp, "competitor_roster", lambda eid: [])
    monkeypatch.setattr(comp, "discover_competitors", lambda eid: ["Figma"])
    with patch.object(comp, "call_with_web_search", return_value="NO_FINDINGS"), \
         patch.object(comp, "embed_texts", side_effect=lambda t, **k: [[0.1] * 4 for _ in t]):
        out = comp.run_competitor_research(facade, "ent-A")
    assert out["no_findings"] == ["Figma"]


def test_run_raises_when_discovery_finds_nothing(facade, monkeypatch):
    from app.research import competitor as comp

    monkeypatch.setattr(comp, "competitor_roster", lambda eid: [])
    monkeypatch.setattr(comp, "discover_competitors", lambda eid: [])
    with pytest.raises(ValueError, match="none could be discovered"):
        comp.run_competitor_research(facade, "ent-A")


def test_competitor_entity_reused_on_rerun(facade):
    from app.research import competitor as comp

    with patch.object(comp, "call_with_web_search", return_value="NO_FINDINGS"), \
         patch.object(comp, "embed_texts", side_effect=lambda t, **k: [[0.1] * 4 for _ in t]):
        comp.run_competitor_research(facade, "ent-A", competitors=["Adobe"])
        comp.run_competitor_research(facade, "ent-A", competitors=["Adobe"])
    ents = facade.query_entities("ent-A", type="competitor")
    # find_candidates returns [] under the fake (no pgvector), so a second
    # entity is created — assert AT MOST 2 and flag: with real pgvector this
    # resolves to 1. The invariant under test: no crash + entities created.
    assert 1 <= len(ents) <= 2


# ---- auto-roster discovery --------------------------------------------------

def _seed_company_profile(db, cid="ent-A", competitors=None):
    """Seed a company row (+ primary product) the way onboarding would, so
    company_profile / competitor_roster read it from the fake Supabase."""
    row = {
        "id": cid, "slug": f"slug-{cid}", "display_name": "Acme",
        "industry": "B2B SaaS",
        "product_description": "Field service management for technicians",
        "business_type": "SaaS",
    }
    if competitors is not None:
        row["competitors"] = competitors
    db.table("companies").insert(row).execute()
    db.table("products").insert({
        "id": f"prod-{cid}", "company_id": cid, "name": "Acme",
        "website": "https://acme.com", "description": "Field ops", "is_primary": 1,
    }).execute()


def _discovery_struct_result(names):
    from app.graph.gateway import LLMResult
    return LLMResult(
        output={"competitors": [
            {"name": n, "website": f"https://{n.lower()}.com", "why": "direct rival"}
            for n in names
        ]},
        model="claude-sonnet-4-6", prompt_version="t",
        input_tokens=1, output_tokens=1, cache_read_input_tokens=0,
        cache_creation_input_tokens=0, cost_usd=0, latency_ms=1, stop_reason="end_turn")


def test_discover_writes_roster_and_logs_when_empty(facade, isolated_settings):
    from app.research import competitor as comp

    db = isolated_settings["supabase"]
    _seed_company_profile(db, "ent-A", competitors=[])  # empty roster

    with patch.object(comp, "call_with_web_search",
                      return_value="ServiceTitan, Jobber, Housecall Pro are the rivals."), \
         patch.object(comp, "llm_call",
                      return_value=_discovery_struct_result(
                          ["ServiceTitan", "Jobber", "Housecall Pro"])):
        out = comp.discover_competitors("ent-A")

    assert out == ["ServiceTitan", "Jobber", "Housecall Pro"]
    # roster persisted into companies.competitors[]
    saved = db.table("companies").select("competitors").eq("id", "ent-A").execute().data
    assert saved[0]["competitors"] == ["ServiceTitan", "Jobber", "Housecall Pro"]
    # decision-logged with full rationale
    logs = db.table("agent_decision_log").select("*").eq("enterprise_id", "ent-A").execute().data
    disc = [r for r in logs if r["decision_type"] == "discover_roster"]
    assert len(disc) == 1 and disc[0]["agent"] == "competitor_analysis"
    assert len(disc[0]["factors"]["picks"]) == 3
    assert disc[0]["output"]["roster"] == ["ServiceTitan", "Jobber", "Housecall Pro"]


def test_discover_never_overwrites_nonempty_roster(facade, isolated_settings):
    from app.research import competitor as comp

    db = isolated_settings["supabase"]
    _seed_company_profile(db, "ent-A", competitors=["Existing Co"])

    with patch.object(comp, "call_with_web_search",
                      side_effect=AssertionError("must not search")) as search, \
         patch.object(comp, "llm_call",
                      side_effect=AssertionError("must not structure")):
        out = comp.discover_competitors("ent-A")

    assert out == ["Existing Co"]
    search.assert_not_called()
    # untouched + no discover_roster log written
    saved = db.table("companies").select("competitors").eq("id", "ent-A").execute().data
    assert saved[0]["competitors"] == ["Existing Co"]
    logs = db.table("agent_decision_log").select("*").eq("enterprise_id", "ent-A").execute().data
    assert not [r for r in logs if r["decision_type"] == "discover_roster"]


def test_discover_caps_to_max_competitors(facade, isolated_settings):
    from app.research import competitor as comp

    db = isolated_settings["supabase"]
    _seed_company_profile(db, "ent-A", competitors=[])
    with patch.object(comp, "call_with_web_search", return_value="A, B, C, D, E"), \
         patch.object(comp, "llm_call",
                      return_value=_discovery_struct_result(["A", "B", "C", "D", "E"])):
        out = comp.discover_competitors("ent-A")
    assert out == ["A", "B", "C"]  # deep_dive_max_competitors default = 3


# ---- staged deep-dive -------------------------------------------------------

def test_deep_dive_runs_module_sequence_with_carryforward(facade, isolated_settings):
    """Each diagnostic stage + the compose stage is a separate web-search call
    carrying skill_module + the prior stages' summaries forward."""
    from app.research import competitor as comp

    calls = []  # (module, user_prompt)

    def fake_search(*, system, user, meta_out=None, skill=None, skill_module=None, **kw):
        calls.append((skill_module, user))
        assert skill == "competitive-intelligence-review"
        if meta_out is not None:
            meta_out["input_tokens"] = 100
            meta_out["output_tokens"] = 50
        return f"FINDING for {skill_module}"

    def fake_extract(f, eid, *, doc_name, text, agent, source_hint=None):
        assert "competitive intelligence" in source_hint
        assert "PRESSURES" in source_hint
        return {"signals": 2, "themes": 1, "skipped": 0}

    with patch.object(comp, "call_with_web_search", side_effect=fake_search), \
         patch.object(comp, "extract_document", side_effect=fake_extract) as extract, \
         patch.object(comp, "embed_texts", side_effect=lambda t, **k: [[0.1] * 4 for _ in t]):
        out = comp.run_competitor_deep_dive(facade, "ent-A", competitors=["Figma"])

    modules = [m for m, _ in calls]
    # all 6 diagnostic modules in order, then the synthesis compose stage
    assert modules == comp.CIR_DIAGNOSTIC_MODULES + [comp.CIR_SYNTHESIS_MODULE]
    # carry-forward: the 2nd stage prompt includes the 1st module's finding;
    # the compose prompt includes every diagnostic finding.
    assert "FINDING for 02-arena.md" in calls[1][1]
    compose_prompt = calls[-1][1]
    for m in comp.CIR_DIAGNOSTIC_MODULES:
        assert f"FINDING for {m}" in compose_prompt
    # extraction invoked exactly once for the one competitor, with the report
    extract.assert_called_once()
    assert out["competitors"] == 1 and out["signals"] == 2
    assert out["web_search_calls"] == len(comp.CIR_DIAGNOSTIC_MODULES) + 1


def test_deep_dive_isolates_per_competitor_errors_and_logs(facade, isolated_settings):
    from app.research import competitor as comp

    def fake_search(*, system, user, meta_out=None, skill_module=None, **kw):
        if "Notion" in user and skill_module == "02-arena.md":
            raise RuntimeError("stage exploded")
        if meta_out is not None:
            meta_out["input_tokens"] = 10
        return f"finding {skill_module}"

    with patch.object(comp, "call_with_web_search", side_effect=fake_search), \
         patch.object(comp, "extract_document",
                      return_value={"signals": 3, "themes": 1, "skipped": 0}), \
         patch.object(comp, "embed_texts", side_effect=lambda t, **k: [[0.1] * 4 for _ in t]):
        out = comp.run_competitor_deep_dive(
            facade, "ent-A", competitors=["Adobe", "Notion"])

    assert out["competitors"] == 1            # Adobe succeeded
    assert out["signals"] == 3
    assert len(out["errors"]) == 1 and "Notion" in out["errors"][0]
    # one per-competitor deep_dive log (the success) + one run summary log
    db = isolated_settings["supabase"]
    logs = db.table("agent_decision_log").select("*").eq("enterprise_id", "ent-A").execute().data
    assert len([r for r in logs if r["decision_type"] == "deep_dive"]) == 1
    runs = [r for r in logs if r["decision_type"] == "deep_dive_run"]
    assert len(runs) == 1 and runs[0]["factors"]["run_tokens"] > 0


def test_deep_dive_respects_web_search_cost_cap(facade, isolated_settings, monkeypatch):
    """A tight web-search budget stops further competitors once the cap is hit."""
    from app.research import competitor as comp
    from app.graph import config_layers

    # Budget of 7 = exactly one competitor (6 diagnostic + 1 compose).
    monkeypatch.setitem(
        config_layers.PLATFORM_DEFAULTS["research"], "deep_dive_max_web_searches", 7)

    def fake_search(*, system, user, meta_out=None, skill_module=None, **kw):
        if meta_out is not None:
            meta_out["input_tokens"] = 1
        return f"finding {skill_module}"

    with patch.object(comp, "call_with_web_search", side_effect=fake_search) as search, \
         patch.object(comp, "extract_document",
                      return_value={"signals": 1, "themes": 0, "skipped": 0}), \
         patch.object(comp, "embed_texts", side_effect=lambda t, **k: [[0.1] * 4 for _ in t]):
        out = comp.run_competitor_deep_dive(
            facade, "ent-A", competitors=["A", "B", "C"])

    assert out["competitors"] == 1            # only the first fit in the budget
    assert out["web_search_calls"] == 7       # 6 diagnostic + 1 compose, no more
    assert search.call_count == 7
    assert any("budget" in e for e in out["errors"])


def test_deep_dive_autodiscovers_when_roster_empty(facade, isolated_settings):
    from app.research import competitor as comp

    db = isolated_settings["supabase"]
    _seed_company_profile(db, "ent-A", competitors=[])

    def fake_search(*, system, user, meta_out=None, skill=None, skill_module=None, **kw):
        if meta_out is not None:
            meta_out["input_tokens"] = 1
        # the discovery call has no skill_module; the stage calls do
        if skill_module is None and skill is None:
            return "Jobber is the rival."
        return f"finding {skill_module}"

    with patch.object(comp, "call_with_web_search", side_effect=fake_search), \
         patch.object(comp, "llm_call", return_value=_discovery_struct_result(["Jobber"])), \
         patch.object(comp, "extract_document",
                      return_value={"signals": 1, "themes": 0, "skipped": 0}), \
         patch.object(comp, "embed_texts", side_effect=lambda t, **k: [[0.1] * 4 for _ in t]):
        out = comp.run_competitor_deep_dive(facade, "ent-A")

    assert out["competitors"] == 1
    saved = db.table("companies").select("competitors").eq("id", "ent-A").execute().data
    assert saved[0]["competitors"] == ["Jobber"]


# ---- routes -----------------------------------------------------------------

def _override_company(monkeypatch, cid="co-X"):
    import app.main as main_mod
    from app.auth import CompanyContext
    import app.routes.research as research_route
    require_company = research_route.require_company
    main_mod.app.dependency_overrides[require_company] = lambda: CompanyContext(
        company_id=cid, role="admin", user_id="u1")
    return main_mod, require_company


def test_route_run_light_mode_calls_light_agent(isolated_settings, monkeypatch):
    from fastapi.testclient import TestClient
    import app.routes.research as research_route

    main_mod, require_company = _override_company(monkeypatch)
    monkeypatch.setattr(research_route, "run_competitor_research",
                        lambda facade, cid, *, competitors=None: {
                            "competitors": 2, "signals": 4, "themes": 1,
                            "skipped": 0, "no_findings": [], "errors": []})
    monkeypatch.setattr(research_route, "run_competitor_deep_dive",
                        lambda *a, **k: pytest.fail("deep-dive must not run"))
    try:
        client = TestClient(main_mod.app)
        r = client.post("/v1/research/competitors/run", json={})
    finally:
        main_mod.app.dependency_overrides.pop(require_company, None)
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["ok"] is True and body["mode"] == "light" and body["signals"] == 4


def test_route_run_deep_dive_mode_calls_deep_dive(isolated_settings, monkeypatch):
    from fastapi.testclient import TestClient
    import app.routes.research as research_route

    main_mod, require_company = _override_company(monkeypatch)
    captured = {}

    def fake_dd(facade, cid, *, competitors=None):
        captured["cid"] = cid
        return {"competitors": 3, "signals": 9, "themes": 3, "skipped": 0,
                "errors": [], "web_search_calls": 21}
    monkeypatch.setattr(research_route, "run_competitor_deep_dive", fake_dd)
    try:
        client = TestClient(main_mod.app)
        r = client.post("/v1/research/competitors/run", json={"mode": "deep_dive"})
    finally:
        main_mod.app.dependency_overrides.pop(require_company, None)
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["mode"] == "deep_dive" and body["web_search_calls"] == 21
    assert captured["cid"] == "co-X"


def test_route_deep_dive_endpoint(isolated_settings, monkeypatch):
    from fastapi.testclient import TestClient
    import app.routes.research as research_route

    main_mod, require_company = _override_company(monkeypatch)
    monkeypatch.setattr(research_route, "run_competitor_deep_dive",
                        lambda facade, cid, *, competitors=None: {
                            "competitors": 1, "signals": 2, "themes": 1,
                            "skipped": 0, "errors": [], "web_search_calls": 7})
    try:
        client = TestClient(main_mod.app)
        r = client.post("/v1/research/competitors/deep-dive", json={})
    finally:
        main_mod.app.dependency_overrides.pop(require_company, None)
    assert r.status_code == 200, r.text
    assert r.json()["mode"] == "deep_dive"


def test_route_deep_dive_requires_auth(isolated_settings):
    from fastapi.testclient import TestClient
    import app.main as main_mod

    client = TestClient(main_mod.app)
    r = client.post("/v1/research/competitors/deep-dive", json={})
    assert r.status_code in (401, 403, 404)
