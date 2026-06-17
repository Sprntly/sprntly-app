"""Tests for the backlog sequencer: the "sequence the rest into a backlog" half
of prioritization (synthesis hook + store + routes)."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from unittest.mock import patch

import pytest

from app.graph.gateway import LLMResult


def _llm_result(output, model="claude-sonnet-4-6"):
    return LLMResult(
        output=output, model=model, prompt_version="test",
        input_tokens=10, output_tokens=5, cache_read_input_tokens=0,
        cache_creation_input_tokens=0, cost_usd=0.001, latency_ms=5,
        stop_reason="end_turn",
    )


@pytest.fixture
def facade(isolated_settings):
    from app.graph import GraphFacade
    return GraphFacade()


def _seed_theme_with_signals(facade, ent, label, specs):
    """specs: list of (source_type, kind, props, age_days). Returns the theme."""
    from app.graph.types import Entity, Relationship, Signal
    theme = Entity(enterprise_id=ent, type="theme", canonical_label=label)
    facade.create_entity(ent, theme)
    now = datetime.now(timezone.utc)
    for st, kind, props, age in specs:
        sig = Signal(enterprise_id=ent, source_type=st, kind=kind,
                     content=f"{label} {kind} {age}", properties=props,
                     valid_at=now - timedelta(days=age))
        facade.write_signal(ent, sig)
        facade.write_relationship(ent, Relationship(
            enterprise_id=ent, type="REQUESTS", source_kind="signal",
            source_id=sig.id, target_kind="entity", target_id=theme.id))
    return theme


def _triage_for(*theme_ids):
    """A triage payload echoing the given theme_ids (the LLM copies them back)."""
    return {"items": [
        {"theme_id": tid, "tag": "something_new", "reasoning": f"rationale {i}"}
        for i, tid in enumerate(theme_ids)
    ]}


# ── seed companies for the FK + tenant tests ──

def _seed_company(db, cid):
    existing = db.table("companies").select("id").eq("id", cid).execute().data
    if not existing:
        db.table("companies").insert(
            {"id": cid, "slug": f"slug-{cid}", "display_name": cid.title()}
        ).execute()
    return cid


# ─────────────────────── sequencing (pure-ish, mocked llm) ───────────────────────

def test_sequence_excludes_brief_top_themes(facade, isolated_settings):
    from app.synthesis import backlog as bl

    _seed_company(isolated_settings["supabase"], "ent-A")
    brief_theme = _seed_theme_with_signals(facade, "ent-A", "brief-one", [
        ("revenue", "deal_blocker", {"revenue_at_risk_usd": 900000}, 0),
        ("customer_voice", "feature_request", {}, 1),
    ])
    rest_a = _seed_theme_with_signals(facade, "ent-A", "rest-a", [
        ("revenue", "deal_blocker", {"revenue_at_risk_usd": 400000}, 0),
    ])
    rest_b = _seed_theme_with_signals(facade, "ent-A", "rest-b", [
        ("customer_voice", "feature_request", {}, 0),
    ])

    with patch.object(bl, "llm_call",
                      return_value=_llm_result(_triage_for(rest_a.id, rest_b.id))):
        rows = bl.sequence_backlog(facade, "ent-A", exclude_theme_ids=[brief_theme.id])

    theme_ids = {r["theme_id"] for r in rows}
    assert brief_theme.id not in theme_ids        # brief theme excluded
    assert theme_ids == {rest_a.id, rest_b.id}


def test_sequence_ranks_remaining_by_score(facade, isolated_settings):
    from app.synthesis import backlog as bl

    _seed_company(isolated_settings["supabase"], "ent-A")
    # "broad" converges across 3 source types (higher base score); "thin" has one.
    broad = _seed_theme_with_signals(facade, "ent-A", "broad", [
        ("revenue", "deal_blocker", {}, 0),
        ("customer_voice", "feature_request", {}, 0),
        ("project_mgmt", "bug", {}, 0),
    ])
    thin = _seed_theme_with_signals(facade, "ent-A", "thin", [
        ("communication", "feature_request", {}, 30),
    ])

    with patch.object(bl, "llm_call",
                      return_value=_llm_result(_triage_for(broad.id, thin.id))):
        rows = bl.sequence_backlog(facade, "ent-A", exclude_theme_ids=[])

    assert [r["title"] for r in rows] == ["broad", "thin"]
    assert rows[0]["rank"] == 1 and rows[1]["rank"] == 2
    assert rows[0]["score"] >= rows[1]["score"]


def test_sequence_persists_all_themes_but_triages_only_top_cap(
        facade, isolated_settings, monkeypatch):
    """EVERY non-brief theme is persisted to the backlog; only the top
    TRIAGE_CAP get an LLM tag/rationale (cost bound). Tail items land without."""
    from app.synthesis import backlog as bl

    _seed_company(isolated_settings["supabase"], "ent-A")
    monkeypatch.setattr(bl, "TRIAGE_CAP", 2)
    # Four themes with descending breadth → deterministic rank four>three>two>one.
    t4 = _seed_theme_with_signals(facade, "ent-A", "four", [
        (s, "feature_request", {}, 0)
        for s in ("revenue", "customer_voice", "project_mgmt", "communication")])
    t3 = _seed_theme_with_signals(facade, "ent-A", "three", [
        (s, "feature_request", {}, 0)
        for s in ("revenue", "customer_voice", "project_mgmt")])
    _seed_theme_with_signals(facade, "ent-A", "two", [
        (s, "feature_request", {}, 0) for s in ("revenue", "customer_voice")])
    _seed_theme_with_signals(facade, "ent-A", "one", [
        ("revenue", "feature_request", {}, 0)])

    captured = {}

    def _cap(**kw):
        captured["input"] = kw.get("input", "")
        return _llm_result(_triage_for(t4.id, t3.id))

    with patch.object(bl, "llm_call", side_effect=_cap):
        rows = bl.sequence_backlog(facade, "ent-A", exclude_theme_ids=[])

    # All four persisted — nothing dropped by a cap.
    assert len(rows) == 4
    # The triage LLM only saw the top-2 themes.
    assert "four" in captured["input"] and "three" in captured["input"]
    assert "two" not in captured["input"] and "one" not in captured["input"]
    # Tail items are persisted but carry no LLM tag/reasoning.
    by_title = {r["title"]: r for r in rows}
    assert by_title["four"]["tag"] == "something_new"
    assert by_title["two"]["tag"] is None
    assert by_title["one"]["reasoning"] is None


def test_sequence_persists_items_with_rank_and_reasoning(facade, isolated_settings):
    from app.synthesis import backlog as bl

    db = isolated_settings["supabase"]
    _seed_company(db, "ent-A")
    t = _seed_theme_with_signals(facade, "ent-A", "only", [("revenue", "deal_blocker", {}, 0)])

    payload = {"items": [{"theme_id": t.id, "tag": "something_broken",
                          "reasoning": "below the brief but worth tracking"}]}
    with patch.object(bl, "llm_call", return_value=_llm_result(payload)):
        bl.sequence_backlog(facade, "ent-A", exclude_theme_ids=[])

    rows = db.table("backlog_items").select("*").eq("enterprise_id", "ent-A").execute().data
    assert len(rows) == 1
    r = rows[0]
    assert r["theme_id"] == t.id
    assert r["rank"] == 1
    assert r["tag"] == "something_broken"
    assert r["reasoning"] == "below the brief but worth tracking"
    assert r["status"] == "backlog"
    assert r["score"] is not None


def test_sequence_upsert_is_idempotent(facade, isolated_settings):
    from app.synthesis import backlog as bl

    db = isolated_settings["supabase"]
    _seed_company(db, "ent-A")
    t = _seed_theme_with_signals(facade, "ent-A", "only", [("revenue", "deal_blocker", {}, 0)])

    with patch.object(bl, "llm_call", return_value=_llm_result(_triage_for(t.id))):
        bl.sequence_backlog(facade, "ent-A", exclude_theme_ids=[])
        bl.sequence_backlog(facade, "ent-A", exclude_theme_ids=[])  # re-run

    rows = db.table("backlog_items").select("*").eq("enterprise_id", "ent-A").execute().data
    assert len(rows) == 1   # upserted in place, not duplicated


def test_sequence_rerun_refreshes_rank_preserves_status(facade, isolated_settings):
    from app.synthesis import backlog as bl
    from app.db.backlog import update_backlog_status

    db = isolated_settings["supabase"]
    _seed_company(db, "ent-A")
    t = _seed_theme_with_signals(facade, "ent-A", "only", [("revenue", "deal_blocker", {}, 0)])
    with patch.object(bl, "llm_call", return_value=_llm_result(_triage_for(t.id))):
        bl.sequence_backlog(facade, "ent-A", exclude_theme_ids=[])

    item = db.table("backlog_items").select("*").eq("enterprise_id", "ent-A").execute().data[0]
    update_backlog_status("ent-A", item["id"], "in_progress")

    # Re-run the sequencer — the user-owned status must survive.
    with patch.object(bl, "llm_call", return_value=_llm_result(_triage_for(t.id))):
        bl.sequence_backlog(facade, "ent-A", exclude_theme_ids=[])

    again = db.table("backlog_items").select("*").eq("enterprise_id", "ent-A").execute().data
    assert len(again) == 1
    assert again[0]["status"] == "in_progress"   # not reset to 'backlog'


def test_sequence_decision_logged(facade, isolated_settings):
    from app.synthesis import backlog as bl

    db = isolated_settings["supabase"]
    _seed_company(db, "ent-A")
    a = _seed_theme_with_signals(facade, "ent-A", "a", [("revenue", "deal_blocker", {}, 0)])
    brief = _seed_theme_with_signals(facade, "ent-A", "brief", [("revenue", "deal_blocker", {}, 0)])

    with patch.object(bl, "llm_call", return_value=_llm_result(_triage_for(a.id))):
        bl.sequence_backlog(facade, "ent-A", exclude_theme_ids=[brief.id])

    logs = db.table("agent_decision_log").select("*").eq("enterprise_id", "ent-A").execute().data
    seq = [r for r in logs if r["decision_type"] == "sequence"]
    assert len(seq) == 1
    assert seq[0]["agent"] == "backlog"
    assert seq[0]["output"]["count"] == 1
    assert seq[0]["factors"]["excluded_theme_ids"] == [brief.id]
    assert a.id in seq[0]["output"]["backlog_theme_ids"]


def test_sequence_empty_when_all_excluded(facade, isolated_settings):
    from app.synthesis import backlog as bl

    _seed_company(isolated_settings["supabase"], "ent-A")
    t = _seed_theme_with_signals(facade, "ent-A", "only", [("revenue", "deal_blocker", {}, 0)])
    # No LLM call should happen when there's nothing to sequence.
    with patch.object(bl, "llm_call", side_effect=AssertionError("should not call LLM")):
        rows = bl.sequence_backlog(facade, "ent-A", exclude_theme_ids=[t.id])
    assert rows == []


def test_sequence_binds_backlog_triage_skill(facade, isolated_settings):
    from app.synthesis import backlog as bl

    _seed_company(isolated_settings["supabase"], "ent-A")
    t = _seed_theme_with_signals(facade, "ent-A", "only", [("revenue", "deal_blocker", {}, 0)])

    captured = {}
    def fake_llm(**kw):
        captured.update(kw)
        return _llm_result(_triage_for(t.id))

    with patch.object(bl, "llm_call", fake_llm):
        bl.sequence_backlog(facade, "ent-A", exclude_theme_ids=[])

    assert captured["skill"] == "backlog-triage"
    assert captured["purpose"] == "sequence_backlog"


def test_backlog_triage_skill_is_vendored():
    from app.skills.loader import get_skill
    spec = get_skill("backlog-triage")
    assert spec.id == "backlog-triage"
    assert "Backlog Triage" in spec.method
    assert spec.content_hash   # fingerprinted


# ─────────────────────── synthesis hook (resilience) ───────────────────────

_RANKED = {
    "summary_headline": "headline",
    "insights": [{
        "theme_id": "FILLED_IN_TEST",
        "tag": "something_broken",
        "title": "Brief insight",
        "subtitle": "s",
        "recommendation": "do it",
        "metrics": [{"label": "ARR", "value": "$1M"}],
        "convergence": [{"source": "revenue", "signal": "x", "strength": "Strong"}],
        "confidence": 0.8,
        "is_headline": True,
        "reasoning": "top.",
    }],
}


def test_synthesis_hook_runs_backlog_excluding_brief_theme(facade, isolated_settings):
    from app.synthesis import agent as synth
    from app.synthesis import backlog as bl

    db = isolated_settings["supabase"]
    _seed_company(db, "ent-A")
    brief_theme = _seed_theme_with_signals(facade, "ent-A", "BRIEF", [
        ("revenue", "deal_blocker", {"revenue_at_risk_usd": 1400000}, 0),
        ("customer_voice", "feature_request", {}, 0),
        ("project_mgmt", "bug", {}, 0),
    ])
    rest = _seed_theme_with_signals(facade, "ent-A", "REST", [
        ("communication", "feature_request", {}, 0),
    ])
    ranked = {**_RANKED, "insights": [{**_RANKED["insights"][0], "theme_id": brief_theme.id}]}

    with patch.object(synth, "llm_call", return_value=_llm_result(ranked)), \
         patch.object(bl, "llm_call", return_value=_llm_result(_triage_for(rest.id))):
        brief = synth.run_synthesis(facade, "ent-A", dataset_slug="acme")

    assert brief["_backlog_count"] == 1
    rows = db.table("backlog_items").select("*").eq("enterprise_id", "ent-A").execute().data
    assert {r["theme_id"] for r in rows} == {rest.id}   # only the non-brief theme


def test_synthesis_survives_backlog_failure(facade, isolated_settings):
    from app.synthesis import agent as synth

    db = isolated_settings["supabase"]
    _seed_company(db, "ent-A")
    brief_theme = _seed_theme_with_signals(facade, "ent-A", "BRIEF", [
        ("revenue", "deal_blocker", {"revenue_at_risk_usd": 1400000}, 0),
    ])
    ranked = {**_RANKED, "insights": [{**_RANKED["insights"][0], "theme_id": brief_theme.id}]}

    with patch.object(synth, "llm_call", return_value=_llm_result(ranked)), \
         patch.object(synth, "sequence_backlog",
                      side_effect=RuntimeError("backlog boom")):
        brief = synth.run_synthesis(facade, "ent-A", dataset_slug="acme")

    # Brief still generated + saved despite the backlog failure.
    assert brief["insights"]
    assert brief["_backlog_count"] is None
    saved = db.table("briefs").select("*").eq("dataset", "acme").execute().data
    assert len(saved) == 1


# ─────────────────────── routes (dep-override + tenant isolation) ───────────────────────

@pytest.fixture
def _override_company(isolated_settings, monkeypatch):
    """Override require_company on the backlog route, return the company id."""
    import app.main as main_mod
    import app.routes.backlog as backlog_route
    from app.auth import CompanyContext

    db = isolated_settings["supabase"]
    cid = _seed_company(db, "co-X")
    require_company = backlog_route.require_company
    main_mod.app.dependency_overrides[require_company] = lambda: CompanyContext(
        company_id=cid, role="member", user_id="u1")
    yield cid
    main_mod.app.dependency_overrides.pop(require_company, None)


def _seed_item(db, cid, theme_id, rank, score, *, status="backlog"):
    import uuid
    iid = str(uuid.uuid4())
    db.table("backlog_items").insert({
        "id": iid, "enterprise_id": cid, "theme_id": theme_id,
        "title": f"item {rank}", "tag": "something_new", "rank": rank,
        "score": score, "status": status, "reasoning": "r",
    }).execute()
    return iid


def _seed_brief(db, slug):
    """Seed a current weekly brief for a company slug (briefs.dataset == slug).

    The backlog GET route gates on a brief existing, so the no-brief →
    empty-backlog invariant holds; this helper lets the populated-backlog tests
    satisfy that gate (a backlog is the by-product of a real analysis).
    """
    from app.db.briefs import save_brief
    save_brief(slug, "Week of test", {"summary_headline": "h", "insights": []},
               schema_version=1)


def test_get_backlog_returns_rank_ordered(isolated_settings, _override_company):
    from fastapi.testclient import TestClient
    import app.main as main_mod

    cid = _override_company
    db = isolated_settings["supabase"]
    _seed_brief(db, f"slug-{cid}")   # a brief exists → backlog (the rest) shows
    _seed_item(db, cid, "t2", rank=2, score=0.3)
    _seed_item(db, cid, "t1", rank=1, score=0.9)

    client = TestClient(main_mod.app)
    r = client.get("/v1/backlog")
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["count"] == 2
    assert [i["rank"] for i in body["items"]] == [1, 2]   # rank-ascending
    assert body["items"][0]["theme_id"] == "t1"


def test_get_backlog_empty_when_no_brief(isolated_settings, _override_company):
    """No weekly brief has ever been generated → the backlog is empty even if
    stale/orphaned backlog_items rows exist for the tenant."""
    from fastapi.testclient import TestClient
    import app.main as main_mod

    cid = _override_company
    db = isolated_settings["supabase"]
    # Rows exist in the table, but NO brief for this company.
    _seed_item(db, cid, "t1", rank=1, score=0.9)
    _seed_item(db, cid, "t2", rank=2, score=0.3)

    client = TestClient(main_mod.app)
    r = client.get("/v1/backlog")
    assert r.status_code == 200, r.text
    body = r.json()
    assert body == {"items": [], "count": 0}   # gated on a brief existing


def test_get_backlog_returns_rank_4plus_when_brief_exists(
    isolated_settings, _override_company,
):
    """End-to-end split: a synthesis run puts the top 3 in the brief and the
    rest (rank ≥ 4) in the backlog; the route then surfaces exactly those."""
    from fastapi.testclient import TestClient
    import app.main as main_mod
    from app.synthesis import agent as synth
    from app.synthesis import backlog as bl

    cid = _override_company
    db = isolated_settings["supabase"]
    from app.graph import GraphFacade
    facade = GraphFacade()

    # Five themes: the LLM judge picks the top 3 for the brief, the sequencer
    # puts the remaining two into the backlog.
    themes = [
        _seed_theme_with_signals(facade, cid, f"theme-{i}", [
            ("revenue", "deal_blocker", {"revenue_at_risk_usd": 900000 - i}, 0),
        ])
        for i in range(5)
    ]
    top3 = themes[:3]
    rest = themes[3:]
    ranked = {
        "summary_headline": "h",
        "insights": [
            {**_RANKED["insights"][0], "theme_id": t.id, "is_headline": i == 0,
             "title": f"brief-{i}"}
            for i, t in enumerate(top3)
        ],
    }

    with patch.object(synth, "llm_call", return_value=_llm_result(ranked)), \
         patch.object(bl, "llm_call",
                      return_value=_llm_result(_triage_for(*[t.id for t in rest]))):
        synth.run_synthesis(facade, cid, dataset_slug=f"slug-{cid}")

    client = TestClient(main_mod.app)
    body = client.get("/v1/backlog").json()
    backlog_ids = {i["theme_id"] for i in body["items"]}
    # The top-3 brief themes are EXCLUDED; only ranks ≥ 4 remain in the backlog.
    assert backlog_ids == {t.id for t in rest}
    for t in top3:
        assert t.id not in backlog_ids


def test_patch_backlog_updates_status(isolated_settings, _override_company):
    from fastapi.testclient import TestClient
    import app.main as main_mod

    cid = _override_company
    db = isolated_settings["supabase"]
    iid = _seed_item(db, cid, "t1", rank=1, score=0.9)

    client = TestClient(main_mod.app)
    r = client.patch(f"/v1/backlog/{iid}", json={"status": "done"})
    assert r.status_code == 200, r.text
    assert r.json()["status"] == "done"
    row = db.table("backlog_items").select("*").eq("id", iid).execute().data[0]
    assert row["status"] == "done"


def test_patch_backlog_rejects_bad_status(isolated_settings, _override_company):
    from fastapi.testclient import TestClient
    import app.main as main_mod

    cid = _override_company
    db = isolated_settings["supabase"]
    iid = _seed_item(db, cid, "t1", rank=1, score=0.9)

    client = TestClient(main_mod.app)
    r = client.patch(f"/v1/backlog/{iid}", json={"status": "nonsense"})
    assert r.status_code == 400


def test_patch_backlog_tenant_isolation(isolated_settings, _override_company):
    from fastapi.testclient import TestClient
    import app.main as main_mod

    cid = _override_company   # "co-X" — the authed tenant
    db = isolated_settings["supabase"]
    other = _seed_company(db, "co-OTHER")
    other_item = _seed_item(db, other, "t1", rank=1, score=0.9)

    client = TestClient(main_mod.app)
    # GET only returns the authed tenant's items (none for co-X).
    assert client.get("/v1/backlog").json()["count"] == 0
    # PATCH on another tenant's item → 404 (not found for this tenant).
    r = client.patch(f"/v1/backlog/{other_item}", json={"status": "done"})
    assert r.status_code == 404
    # The other tenant's item is untouched.
    row = db.table("backlog_items").select("*").eq("id", other_item).execute().data[0]
    assert row["status"] == "backlog"
