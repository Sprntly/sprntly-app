"""Tests for the Business Context entity, agent, KG projection, and routes."""
from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import patch

import pytest

from fastapi.testclient import TestClient

from app.business_context import (
    BusinessContext,
    BusinessModel,
    Identity,
    Meta,
    Segment,
    UsersSegments,
    Vocabulary,
    VocabTerm,
    load_business_context,
    save_business_context,
)
from tests.conftest import (
    _enable_supabase_bearer,
    _mint_supabase_token,
    _seed_company_membership,
)


@pytest.fixture
def company_client(isolated_settings, monkeypatch) -> TestClient:
    """Bearer-authed TestClient resolving company_id == 'co-test' via the real
    require_company path (mirrors conftest.company_client without its DA-suite
    `env` dependency)."""
    import app.main as main_mod
    _enable_supabase_bearer(monkeypatch)
    _seed_company_membership(isolated_settings["supabase"])
    c = TestClient(main_mod.app)
    c.headers["Authorization"] = f"Bearer {_mint_supabase_token()}"
    return c


# --------------------------------------------------------------------------- #
# Builders
# --------------------------------------------------------------------------- #
def _doc(**kw) -> BusinessContext:
    d = BusinessContext()
    d.identity = Identity(
        legal_name=Meta(value="Frazil", src="user", conf="high", as_of="2026-06-07"),
        one_liner=Meta(value="frozen beverage program for c-stores", src="web",
                       conf="high", as_of="2026-06-07",
                       evidence="Frazil powers frozen drink programs for c-stores"),
        website=Meta(value="https://frazil.com", src="user", conf="high"),
    )
    d.business_model = BusinessModel(
        who_pays=Meta(value="store operators", src="web", conf="med",
                      evidence="operators buy the program"),
        who_uses=Meta(value="end consumers", src="web", conf="high",
                      evidence="consumers at the dispenser"),
        good_outcome=Meta(value="operator reorder rate", src="user", conf="high"),
    )
    d.users_segments = UsersSegments(segments=[
        Segment(name=Meta(value="C-store operators", src="user", conf="high"),
                jtbd=Meta(value="grow margin per square foot", src="user", conf="med"),
                is_buyer=Meta(value=True, src="user", conf="high")),
    ])
    d.goals_strategy.stated_goal = Meta(
        value="grow repeat orders from operators", src="given", conf="high")
    d.goals_strategy.known_constraints = Meta(
        value=["small field sales team"], src="user", conf="high")
    d.vocabulary = Vocabulary(terms=[
        VocabTerm(term=Meta(value="operator", src="user", conf="high"),
                  their_meaning=Meta(value="the paying store", src="user", conf="high")),
    ])
    for k, v in kw.items():
        setattr(d, k, v)
    return d


# --------------------------------------------------------------------------- #
# 1–3. Model: schema-faithful round-trip (incl. meta provenance), partials, version
# --------------------------------------------------------------------------- #
def test_model_roundtrips_with_meta_provenance():
    d = _doc()
    rt = BusinessContext.model_validate(d.model_dump())
    # leaf value + every provenance field survives the round-trip
    one = rt.identity.one_liner
    assert one.value == "frozen beverage program for c-stores"
    assert one.src == "web" and one.conf == "high"
    assert one.evidence == "Frazil powers frozen drink programs for c-stores"
    assert rt.business_model.who_pays.src == "web"
    assert rt.users_segments.segments[0].name.value == "C-store operators"
    assert rt.vocabulary.terms[0].their_meaning.value == "the paying store"


def test_partial_doc_tolerated():
    # Only identity basics present; everything else defaults to unknown leaves.
    d = BusinessContext(identity=Identity(
        legal_name=Meta(value="Acme", src="user", conf="high")))
    rt = BusinessContext.model_validate(d.model_dump())
    assert rt.identity.legal_name.value == "Acme"
    assert rt.business_model.who_pays.src == "unknown"
    assert rt.users_segments.segments == []
    assert rt.business_model.who_pays.is_known is False


def test_empty_raw_roundtrips():
    # A bare {} is a valid (all-unknown) doc — tolerate hand-edited/legacy shapes.
    rt = BusinessContext.model_validate({})
    assert rt.version == 1 and rt.identity.legal_name.is_known is False


# --------------------------------------------------------------------------- #
# 4. render_for_prompt: shows known, omits unknown, caps length
# --------------------------------------------------------------------------- #
def test_render_shows_known_omits_unknown():
    out = _doc().render_for_prompt()
    assert "Frazil" in out and "frozen beverage program" in out
    assert "pays: store operators" in out and "uses: end consumers" in out
    assert "Good outcome for them: operator reorder rate" in out
    assert "C-store operators" in out
    assert "operator = the paying store" in out
    assert "Goal: grow repeat orders" in out
    # an unknown leaf (sub_vertical) must not appear
    assert "unknown" not in out.lower()


def test_render_caps_length():
    out = _doc().render_for_prompt(max_chars=40)
    assert len(out) <= 40 and out.endswith("…")


# --------------------------------------------------------------------------- #
# 5–6. Versioned storage: load/save + version bump
# --------------------------------------------------------------------------- #
def _client_with(raw):
    class FakeQ:
        def __init__(self): self.updated = None
        def select(self, *_): return self
        def eq(self, *_): return self
        def update(self, patch): self.updated = patch; return self
        def execute(self): return SimpleNamespace(data=[{"business_context": raw}])
    q = FakeQ()
    return type("C", (), {"table": lambda s, n: q})(), q


def test_load_returns_none_for_empty(monkeypatch):
    import app.business_context as bc
    client, _ = _client_with({})
    monkeypatch.setattr(bc, "require_client", lambda: client)
    assert bc.load_business_context("e") is None


def test_load_tolerates_invalid_shape(monkeypatch):
    import app.business_context as bc
    client, _ = _client_with({"identity": {"legal_name": {"src": 12345}}})
    monkeypatch.setattr(bc, "require_client", lambda: client)
    assert bc.load_business_context("e") is None  # bad src enum → None, no raise


def test_save_bumps_version_past_stored(monkeypatch):
    import app.business_context as bc
    stored = _doc().model_dump(); stored["version"] = 7
    client, q = _client_with(stored)
    monkeypatch.setattr(bc, "require_client", lambda: client)
    saved = bc.save_business_context("e", _doc())
    assert saved.version == 8
    assert q.updated["business_context"]["version"] == 8
    # last_refreshed stamped on save
    assert q.updated["business_context"]["meta"]["last_refreshed"]["src"] == "given"


# --------------------------------------------------------------------------- #
# 7–9. Agent: seeding, user-field preservation, web-fill with evidence
# --------------------------------------------------------------------------- #
_COMPANY = {
    "display_name": "Frazil", "industry": "Foodservice", "sub_vertical": None,
    "stage": "growth", "product_description": "frozen beverage dispensers",
    "business_type": "services", "team_size": 40, "okrs": "grow repeat orders",
    "biggest_risk": "operator churn", "dead_ends": ["DTC retail"],
    "competitors": ["Slush Puppie"],
}
_PRODUCT = {"name": "Frazil", "website": "https://frazil.com",
            "description": "frozen drink program"}


def _seed_company(db, cid: str) -> None:
    """A bare companies row so save/load_business_context can update + read it."""
    db.table("companies").insert(
        {"id": cid, "slug": f"slug-{cid}", "display_name": cid}
    ).execute()


def _patch_agent_io(monkeypatch, agent, company=_COMPANY, product=_PRODUCT):
    monkeypatch.setattr(agent, "_company_row", lambda eid: dict(company))
    monkeypatch.setattr(agent, "_primary_product", lambda eid: dict(product))
    monkeypatch.setattr(agent, "load_kpi_tree", lambda eid: None)


def test_seed_from_onboarding_columns(isolated_settings, monkeypatch):
    from app.research import business_context_agent as agent
    _patch_agent_io(monkeypatch, agent)
    doc, name, row, product = agent._seed_from_known("ent-A", "2026-06-07")
    assert name == "Frazil"
    assert doc.identity.legal_name.value == "Frazil"
    assert doc.identity.legal_name.src == "user"          # first-party
    assert doc.identity.website.value == "https://frazil.com"
    assert doc.business_model.model_type.value == "services"
    assert doc.goals_strategy.stated_goal.value == "grow repeat orders"
    assert "operator churn" in doc.goals_strategy.known_constraints.value
    assert "DTC retail" in doc.goals_strategy.known_constraints.value
    assert doc.market_competition.main_alternatives.value == ["Slush Puppie"]


def test_web_fill_marks_src_web_with_evidence(isolated_settings, monkeypatch):
    from app.research import business_context_agent as agent
    _patch_agent_io(monkeypatch, agent)
    _seed_company(isolated_settings["supabase"], "ent-A")

    web_json = (
        '{"one_liner": {"value": "frozen drink program for c-stores", '
        '"conf": "high", "evidence": "Frazil powers frozen drink programs"}, '
        '"category": {"value": "frozen beverage dispensing", "conf": "med", '
        '"evidence": "category page text"}, '
        '"positioning_angle": {"value": "turnkey operator program", "conf": "low"}}'  # no evidence → dropped
    )

    def fake_search(*, system, user, meta_out=None, **kw):
        assert "Frazil" in user and "frazil.com" in user
        if meta_out is not None:
            meta_out["input_tokens"] = 99
        return "Here is the result:\n" + web_json

    with patch.object(agent, "call_with_web_search", side_effect=fake_search), \
         patch("app.research.business_context_projection.project_business_context",
               return_value={"segments": 0, "competitors": 1, "signals": 2}):
        out = agent.run_business_context(object(), "ent-A")

    saved = load_business_context("ent-A")
    one = saved.identity.one_liner
    assert one.src == "web" and one.evidence and one.conf == "high"
    assert saved.market_competition.category.src == "web"
    # positioning_angle had NO evidence → never filled (a guess is dropped)
    assert saved.market_competition.positioning_angle.is_known is False
    assert "identity.one_liner" in out["fields_filled"]
    assert "market_competition.positioning_angle" not in out["fields_filled"]


def test_agent_never_overwrites_user_field(isolated_settings, monkeypatch):
    from app.research import business_context_agent as agent
    _patch_agent_io(monkeypatch, agent)
    _seed_company(isolated_settings["supabase"], "ent-B")

    # Seed a stored doc whose one_liner is USER-authored.
    pre = BusinessContext()
    pre.identity.one_liner = Meta(value="THE HUMAN ONE-LINER", src="user", conf="high")
    save_business_context("ent-B", pre)

    web_json = ('{"one_liner": {"value": "web override", "conf": "high", '
                '"evidence": "site text"}}')

    with patch.object(agent, "call_with_web_search",
                      side_effect=lambda **kw: web_json), \
         patch("app.research.business_context_projection.project_business_context",
               return_value={}):
        agent.run_business_context(object(), "ent-B")

    saved = load_business_context("ent-B")
    # user-authored leaf preserved; agent fills gaps only
    assert saved.identity.one_liner.value == "THE HUMAN ONE-LINER"
    assert saved.identity.one_liner.src == "user"


def test_missing_display_name_raises(isolated_settings, monkeypatch):
    from app.research import business_context_agent as agent
    _patch_agent_io(monkeypatch, agent, company={**_COMPANY, "display_name": ""})
    with pytest.raises(ValueError, match="display_name"):
        agent.run_business_context(object(), "ent-X")


# --------------------------------------------------------------------------- #
# 10–11. KG projection: segment entities + signals, idempotent
# --------------------------------------------------------------------------- #
@pytest.fixture
def facade(isolated_settings):
    from app.graph import GraphFacade
    return GraphFacade()


def test_projection_creates_segments_and_signals(facade, isolated_settings, monkeypatch):
    from app.research import business_context_projection as proj
    monkeypatch.setattr(proj, "embed_texts", lambda t, **k: [[0.1] * 4 for _ in t])

    created = proj.project_business_context(facade, "ent-A", _doc())
    assert created["segments"] == 1
    assert created["competitors"] == 0  # _doc has no main_alternatives
    # constraint + good_outcome → 2 signals
    assert created["signals"] == 2

    segs = facade.query_entities("ent-A", type="segment")
    assert [s.canonical_label for s in segs] == ["C-store operators"]
    sigs = facade.active_signals("ent-A")
    kinds = {s.kind for s in sigs}
    assert kinds == {"constraint", "good_outcome"}
    # user-sourced leaves → pm_manual source_type
    assert all(s.source_type == "pm_manual" for s in sigs)


def test_projection_alternatives_and_inferred_source(facade, isolated_settings, monkeypatch):
    from app.research import business_context_projection as proj
    monkeypatch.setattr(proj, "embed_texts", lambda t, **k: [[0.1] * 4 for _ in t])

    d = _doc()
    d.market_competition.main_alternatives = Meta(
        value=["Slush Puppie", "DIY/do nothing"], src="web", conf="med",
        evidence="alternatives listed on review sites")
    # an inferred constraint → agent_inferred signal
    d.goals_strategy.known_constraints = Meta(
        value=["thin margins"], src="web", conf="med", evidence="x")

    created = proj.project_business_context(facade, "ent-C", d)
    comps = facade.query_entities("ent-C", type="competitor")
    # "DIY/do nothing" is filtered out
    assert [c.canonical_label for c in comps] == ["Slush Puppie"]
    assert created["competitors"] == 1
    constraint_sig = next(s for s in facade.active_signals("ent-C")
                          if s.kind == "constraint")
    assert constraint_sig.source_type == "agent_inferred"


def test_projection_idempotent(facade, isolated_settings, monkeypatch):
    from app.research import business_context_projection as proj
    monkeypatch.setattr(proj, "embed_texts", lambda t, **k: [[0.1] * 4 for _ in t])

    d = _doc()
    first = proj.project_business_context(facade, "ent-A", d)
    assert first["signals"] == 2 and first["segments"] == 1

    # Simulate the embedding-dedupe hit so the re-run finds existing entities.
    existing = facade.query_entities("ent-A", type="segment")
    orig_find = facade.find_candidates
    monkeypatch.setattr(
        facade, "find_candidates",
        lambda eid, typ, vec, k=10: [(existing[0], 0.99)] if typ == "segment" else [])

    second = proj.project_business_context(facade, "ent-A", d)
    assert second == {"segments": 0, "competitors": 0, "signals": 0}
    # no duplicate signals piled up
    assert len(facade.active_signals("ent-A")) == 2


# --------------------------------------------------------------------------- #
# 12–14. Routes: GET 404 when empty, PUT stamps user, refresh via dep override
# --------------------------------------------------------------------------- #
def test_get_404_when_empty(company_client):
    r = company_client.get("/v1/company/business-context")
    assert r.status_code == 404


def test_put_then_get_stamps_user_and_persists(company_client):
    body = BusinessContext()
    body.identity.legal_name = Meta(value="Acme", src="inferred", conf="med")
    body.business_model.who_pays = Meta(value="IT admins", src="web", conf="low")
    r = company_client.put("/v1/company/business-context",
                           json=body.model_dump())
    assert r.status_code == 200 and r.json()["ok"] is True
    assert r.json()["version"] == 1

    got = company_client.get("/v1/company/business-context").json()
    # every KNOWN leaf the human submitted is re-stamped src="user"
    assert got["identity"]["legal_name"]["src"] == "user"
    assert got["business_model"]["who_pays"]["src"] == "user"
    # unknown leaves stay gap-fillable
    assert got["product_value"]["what_it_does"]["src"] == "unknown"


def test_put_bumps_version(company_client):
    body = BusinessContext()
    body.identity.legal_name = Meta(value="Acme", src="user", conf="high")
    company_client.put("/v1/company/business-context", json=body.model_dump())
    r2 = company_client.put("/v1/company/business-context", json=body.model_dump())
    assert r2.json()["version"] == 2


def test_refresh_route_runs_agent(company_client, monkeypatch):
    import app.routes.business_context as routes

    def fake_run(facade, company_id):
        assert company_id == "co-test"
        return {"version": 3, "fields_filled": ["identity.one_liner"],
                "overall_confidence": "med", "confidence": {}, "projection": {}}

    monkeypatch.setattr(routes, "run_business_context", fake_run)
    r = company_client.post("/v1/company/business-context/refresh")
    assert r.status_code == 200
    body = r.json()
    assert body["ok"] is True and body["version"] == 3
    assert body["overall_confidence"] == "med"


def test_routes_require_company():
    # The router gates every route on require_company (Depends).
    from app.routes.business_context import router
    paths = {r.path for r in router.routes}
    assert "/v1/company/business-context" in paths
    assert "/v1/company/business-context/refresh" in paths
