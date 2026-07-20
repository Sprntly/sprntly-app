"""Tests for the ideation sequencer: the "sequence the rest into the ideation
pool + shortlist the 25-30 worth showing" half of prioritization (synthesis
hook + store + routes)."""
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


def _triage_with_dupes(theme_ids, duplicate_of):
    """Triage payload where `duplicate_of` maps a theme_id → the earlier theme_id
    it duplicates (same project, different wording)."""
    return {"items": [
        {"theme_id": tid, "tag": "something_new", "reasoning": f"rationale {i}",
         "duplicate_of": duplicate_of.get(tid, "")}
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
    from app.synthesis import ideation as bl

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
        rows = bl.sequence_ideation(facade, "ent-A", exclude_theme_ids=[brief_theme.id])

    theme_ids = {r["theme_id"] for r in rows}
    assert brief_theme.id not in theme_ids        # brief theme excluded
    assert theme_ids == {rest_a.id, rest_b.id}


def test_sequence_classifies_goal_fit_on_background_lane(
        facade, isolated_settings, monkeypatch):
    """The sequencer's goal-fit sweep classifies EVERY non-brief theme — on a
    first-run company that is hundreds of serial LLM calls, so they must ride
    the gate's background lane (never starving the interactive PRD/evidence/
    ticket generations a user is waiting on right after their brief)."""
    from app.synthesis import ideation as bl
    from app.synthesis import scoring
    from app.kpi_tree import KpiTree, NorthStar

    _seed_company(isolated_settings["supabase"], "ent-A")
    t = _seed_theme_with_signals(facade, "ent-A", "rest-a", [
        ("revenue", "deal_blocker", {"revenue_at_risk_usd": 400000}, 0),
    ])
    tree = KpiTree(north_star=NorthStar(metric="Weekly Active Technicians",
                                        description="7-day active techs."),
                   version=1)
    monkeypatch.setattr(bl, "load_kpi_tree", lambda eid: tree)

    classify_calls = []

    def fake_scoring_llm(**kw):
        classify_calls.append(kw)
        return _llm_result({"fit": "high", "reasoning": "moves the north star"})

    with patch.object(scoring, "llm_call", fake_scoring_llm), \
            patch.object(bl, "llm_call",
                         return_value=_llm_result(_triage_for(t.id))):
        bl.sequence_ideation(facade, "ent-A", exclude_theme_ids=[])

    assert classify_calls, "expected the sequencer to classify goal fit"
    assert all(kw["purpose"] == "classify_goal_fit" for kw in classify_calls)
    assert all(kw["background"] is True for kw in classify_calls)


def test_sequence_ranks_remaining_by_score(facade, isolated_settings):
    from app.synthesis import ideation as bl

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
        rows = bl.sequence_ideation(facade, "ent-A", exclude_theme_ids=[])

    assert [r["title"] for r in rows] == ["Broad", "Thin"]   # titles are title-cased
    assert rows[0]["rank"] == 1 and rows[1]["rank"] == 2
    assert rows[0]["score"] >= rows[1]["score"]


# ─────────────────────────── dedup (_drop_duplicates unit) ───────────────────────

class _Cand:
    """Minimal stand-in for a ThemeConvergence — _drop_duplicates only reads
    .theme_id."""
    def __init__(self, theme_id):
        self.theme_id = theme_id


def _dupes(mapping):
    return {tid: {"duplicate_of": tgt} for tid, tgt in mapping.items()}


def test_drop_duplicates_keeps_highest_ranked_of_cluster():
    from app.synthesis.ideation import _drop_duplicates
    cands = [_Cand("a"), _Cand("b"), _Cand("c")]
    # b is the same project as a (earlier); c is distinct.
    survivors = _drop_duplicates(cands, _dupes({"b": "a"}))
    assert [c.theme_id for c in survivors] == ["a", "c"]


def test_drop_duplicates_ignores_forward_self_and_unknown_pointers():
    from app.synthesis.ideation import _drop_duplicates
    cands = [_Cand("a"), _Cand("b"), _Cand("c")]
    # a→b points FORWARD (later), b→b is self, c→zzz is unknown: all ignored.
    survivors = _drop_duplicates(
        cands, _dupes({"a": "b", "b": "b", "c": "zzz"}))
    assert [c.theme_id for c in survivors] == ["a", "b", "c"]


def test_drop_duplicates_resolves_chain_to_surviving_root():
    from app.synthesis.ideation import _drop_duplicates
    cands = [_Cand("a"), _Cand("b"), _Cand("c")]
    # b duplicates a (dropped); c points at b (already dropped) → c is KEPT,
    # because its named canonical no longer stands. Prevents a dropped item from
    # silently taking others down with it.
    survivors = _drop_duplicates(cands, _dupes({"b": "a", "c": "b"}))
    assert [c.theme_id for c in survivors] == ["a", "c"]


def test_drop_duplicates_no_flags_keeps_everything():
    from app.synthesis.ideation import _drop_duplicates
    cands = [_Cand("a"), _Cand("b")]
    assert [c.theme_id for c in _drop_duplicates(cands, {})] == ["a", "b"]


# ─────────────────────────────── title casing ───────────────────────────────

def test_title_case_capitalizes_and_preserves_acronyms():
    from app.synthesis.ideation import _title_case
    cases = {
        "brief delivery": "Brief Delivery",
        "onboarding": "Onboarding",
        "PRD generation": "PRD Generation",             # acronym untouched
        "Voice of Customer (VoC) digest": "Voice of Customer (VoC) Digest",
        "enterprise security & SSO": "Enterprise Security & SSO",
        "seat limit / over-provisioning": "Seat Limit / Over-Provisioning",
        "brief / report sharing": "Brief / Report Sharing",
        "HubSpot OAuth integration": "HubSpot OAuth Integration",
        "the brief": "The Brief",                        # minor word capped when first
    }
    for src, want in cases.items():
        assert _title_case(src) == want, f"{src!r} → {_title_case(src)!r}"


# ───────────────────────── dedup (sequence_backlog end-to-end) ───────────────────

def test_sequence_drops_reworded_duplicate(facade, isolated_settings):
    """Two themes describing the same project in different wording collapse to a
    single backlog row, ranked contiguously."""
    from app.synthesis import ideation as bl

    db = isolated_settings["supabase"]
    _seed_company(db, "ent-A")
    # "sso" converges broader → ranks first; "single sign-on" is the reworded twin.
    sso = _seed_theme_with_signals(facade, "ent-A", "Add SSO login", [
        ("revenue", "deal_blocker", {}, 0),
        ("customer_voice", "feature_request", {}, 0),
        ("project_mgmt", "bug", {}, 0),
    ])
    twin = _seed_theme_with_signals(facade, "ent-A", "Support single sign-on", [
        ("customer_voice", "feature_request", {}, 5),
    ])
    other = _seed_theme_with_signals(facade, "ent-A", "Dark mode", [
        ("revenue", "deal_blocker", {}, 0),
        ("project_mgmt", "bug", {}, 0),
    ])

    triage = _triage_with_dupes(
        [sso.id, other.id, twin.id], duplicate_of={twin.id: sso.id})
    with patch.object(bl, "llm_call", return_value=_llm_result(triage)):
        rows = bl.sequence_ideation(facade, "ent-A", exclude_theme_ids=[])

    theme_ids = [r["theme_id"] for r in rows]
    assert twin.id not in theme_ids                 # reworded duplicate dropped
    assert sso.id in theme_ids and other.id in theme_ids
    assert [r["rank"] for r in rows] == [1, 2]       # contiguous after the drop

    # Nothing lingers in the store for the dropped twin.
    stored = db.table("ideation_items").select("theme_id").eq(
        "enterprise_id", "ent-A").execute().data
    assert twin.id not in {r["theme_id"] for r in stored}


def test_sequence_dedup_prunes_prior_duplicate_row(facade, isolated_settings):
    """A twin that was persisted on an earlier run (before it was recognised as a
    duplicate) is pruned on the next sequence, not left orphaned."""
    from app.synthesis import ideation as bl

    db = isolated_settings["supabase"]
    _seed_company(db, "ent-A")
    sso = _seed_theme_with_signals(facade, "ent-A", "Add SSO login", [
        ("revenue", "deal_blocker", {}, 0),
        ("customer_voice", "feature_request", {}, 0),
        ("project_mgmt", "bug", {}, 0),
    ])
    twin = _seed_theme_with_signals(facade, "ent-A", "Support single sign-on", [
        ("customer_voice", "feature_request", {}, 5),
    ])

    # Run 1: triage does NOT yet flag the twin → both persist.
    with patch.object(bl, "llm_call",
                      return_value=_llm_result(_triage_for(sso.id, twin.id))):
        bl.sequence_ideation(facade, "ent-A", exclude_theme_ids=[])
    assert db.table("ideation_items").select("id").eq(
        "enterprise_id", "ent-A").execute().data.__len__() == 2

    # Run 2: triage now flags the twin as a duplicate → the stale row is pruned.
    triage = _triage_with_dupes(
        [sso.id, twin.id], duplicate_of={twin.id: sso.id})
    with patch.object(bl, "llm_call", return_value=_llm_result(triage)):
        bl.sequence_ideation(facade, "ent-A", exclude_theme_ids=[])

    stored = db.table("ideation_items").select("theme_id").eq(
        "enterprise_id", "ent-A").execute().data
    assert {r["theme_id"] for r in stored} == {sso.id}


def test_sequence_persists_all_themes_but_triages_only_top_cap(
        facade, isolated_settings, monkeypatch):
    """EVERY non-brief theme is persisted to the ideation pool; only the top
    PRIORITIZE_POOL get an LLM tag/rationale (cost bound). Tail items land
    without."""
    from app.synthesis import ideation as bl

    _seed_company(isolated_settings["supabase"], "ent-A")
    monkeypatch.setattr(bl, "PRIORITIZE_POOL", 2)
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
        rows = bl.sequence_ideation(facade, "ent-A", exclude_theme_ids=[])

    # All four persisted — nothing dropped by a cap.
    assert len(rows) == 4
    # The triage LLM only saw the top-2 themes.
    assert "four" in captured["input"] and "three" in captured["input"]
    assert "two" not in captured["input"] and "one" not in captured["input"]
    # Tail items are persisted but carry no LLM tag/reasoning.
    by_title = {r["title"]: r for r in rows}
    assert by_title["Four"]["tag"] == "something_new"
    assert by_title["Two"]["tag"] is None
    assert by_title["One"]["reasoning"] is None


def test_sequence_persists_items_with_rank_and_reasoning(facade, isolated_settings):
    from app.synthesis import ideation as bl

    db = isolated_settings["supabase"]
    _seed_company(db, "ent-A")
    t = _seed_theme_with_signals(facade, "ent-A", "only", [("revenue", "deal_blocker", {}, 0)])

    payload = {"items": [{"theme_id": t.id, "tag": "something_broken",
                          "reasoning": "below the brief but worth tracking"}]}
    with patch.object(bl, "llm_call", return_value=_llm_result(payload)):
        bl.sequence_ideation(facade, "ent-A", exclude_theme_ids=[])

    rows = db.table("ideation_items").select("*").eq("enterprise_id", "ent-A").execute().data
    assert len(rows) == 1
    r = rows[0]
    assert r["theme_id"] == t.id
    assert r["rank"] == 1
    assert r["tag"] == "something_broken"
    assert r["reasoning"] == "below the brief but worth tracking"
    assert r["status"] == "proposed"
    assert r["shortlisted"] is True   # a lone candidate is always shortlisted
    assert r["score"] is not None


def test_sequence_upsert_is_idempotent(facade, isolated_settings):
    from app.synthesis import ideation as bl

    db = isolated_settings["supabase"]
    _seed_company(db, "ent-A")
    t = _seed_theme_with_signals(facade, "ent-A", "only", [("revenue", "deal_blocker", {}, 0)])

    with patch.object(bl, "llm_call", return_value=_llm_result(_triage_for(t.id))):
        bl.sequence_ideation(facade, "ent-A", exclude_theme_ids=[])
        bl.sequence_ideation(facade, "ent-A", exclude_theme_ids=[])  # re-run

    rows = db.table("ideation_items").select("*").eq("enterprise_id", "ent-A").execute().data
    assert len(rows) == 1   # upserted in place, not duplicated


def test_sequence_rerun_refreshes_rank_preserves_status(facade, isolated_settings):
    from app.synthesis import ideation as bl
    from app.db.ideation import update_ideation_status

    db = isolated_settings["supabase"]
    _seed_company(db, "ent-A")
    t = _seed_theme_with_signals(facade, "ent-A", "only", [("revenue", "deal_blocker", {}, 0)])
    with patch.object(bl, "llm_call", return_value=_llm_result(_triage_for(t.id))):
        bl.sequence_ideation(facade, "ent-A", exclude_theme_ids=[])

    item = db.table("ideation_items").select("*").eq("enterprise_id", "ent-A").execute().data[0]
    update_ideation_status("ent-A", item["id"], "in_progress")

    # Re-run the sequencer — the user-owned status must survive.
    with patch.object(bl, "llm_call", return_value=_llm_result(_triage_for(t.id))):
        bl.sequence_ideation(facade, "ent-A", exclude_theme_ids=[])

    again = db.table("ideation_items").select("*").eq("enterprise_id", "ent-A").execute().data
    assert len(again) == 1
    assert again[0]["status"] == "in_progress"   # not reset to 'proposed'


def test_sequence_decision_logged(facade, isolated_settings):
    from app.synthesis import ideation as bl

    db = isolated_settings["supabase"]
    _seed_company(db, "ent-A")
    a = _seed_theme_with_signals(facade, "ent-A", "a", [("revenue", "deal_blocker", {}, 0)])
    brief = _seed_theme_with_signals(facade, "ent-A", "brief", [("revenue", "deal_blocker", {}, 0)])

    with patch.object(bl, "llm_call", return_value=_llm_result(_triage_for(a.id))):
        bl.sequence_ideation(facade, "ent-A", exclude_theme_ids=[brief.id])

    logs = db.table("agent_decision_log").select("*").eq("enterprise_id", "ent-A").execute().data
    seq = [r for r in logs if r["decision_type"] == "sequence"]
    assert len(seq) == 1
    assert seq[0]["agent"] == "ideation"
    assert seq[0]["output"]["count"] == 1
    assert seq[0]["factors"]["excluded_theme_ids"] == [brief.id]
    assert a.id in seq[0]["output"]["ideation_theme_ids"]


def test_sequence_empty_when_all_excluded(facade, isolated_settings):
    from app.synthesis import ideation as bl

    _seed_company(isolated_settings["supabase"], "ent-A")
    t = _seed_theme_with_signals(facade, "ent-A", "only", [("revenue", "deal_blocker", {}, 0)])
    # No LLM call should happen when there's nothing to sequence.
    with patch.object(bl, "llm_call", side_effect=AssertionError("should not call LLM")):
        rows = bl.sequence_ideation(facade, "ent-A", exclude_theme_ids=[t.id])
    assert rows == []


def test_sequence_binds_backlog_triage_skill(facade, isolated_settings):
    from app.synthesis import ideation as bl

    _seed_company(isolated_settings["supabase"], "ent-A")
    t = _seed_theme_with_signals(facade, "ent-A", "only", [("revenue", "deal_blocker", {}, 0)])

    captured = {}
    def fake_llm(**kw):
        captured.update(kw)
        return _llm_result(_triage_for(t.id))

    with patch.object(bl, "llm_call", fake_llm):
        bl.sequence_ideation(facade, "ent-A", exclude_theme_ids=[])

    assert captured["skill"] == "ideation-prioritize"
    assert captured["purpose"] == "sequence_ideation"


def test_ideation_prioritize_skill_is_vendored():
    from app.skills.loader import get_skill
    spec = get_skill("ideation-prioritize")
    assert spec.id == "ideation-prioritize"
    assert "Ideation Prioritize" in spec.method
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
    from app.synthesis import ideation as bl

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

    assert brief["_ideation_count"] == 1
    rows = db.table("ideation_items").select("*").eq("enterprise_id", "ent-A").execute().data
    assert {r["theme_id"] for r in rows} == {rest.id}   # only the non-brief theme


def test_synthesis_survives_backlog_failure(facade, isolated_settings):
    from app.synthesis import agent as synth

    db = isolated_settings["supabase"]
    _seed_company(db, "ent-A")
    # Multi-source so it clears the brief evidence gate (this test is about
    # backlog-failure resilience, not the thin-evidence path).
    brief_theme = _seed_theme_with_signals(facade, "ent-A", "BRIEF", [
        ("revenue", "deal_blocker", {"revenue_at_risk_usd": 1400000}, 0),
        ("customer_voice", "feature_request", {}, 0),
    ])
    ranked = {**_RANKED, "insights": [{**_RANKED["insights"][0], "theme_id": brief_theme.id}]}

    with patch.object(synth, "llm_call", return_value=_llm_result(ranked)), \
         patch.object(synth, "sequence_ideation",
                      side_effect=RuntimeError("ideation boom")):
        brief = synth.run_synthesis(facade, "ent-A", dataset_slug="acme")

    # Brief still generated + saved despite the backlog failure.
    assert brief["insights"]
    assert brief["_ideation_count"] is None
    saved = db.table("briefs").select("*").eq("dataset", "acme").execute().data
    assert len(saved) == 1


# ─────────────────────── routes (dep-override + tenant isolation) ───────────────────────

@pytest.fixture
def _override_company(isolated_settings, monkeypatch):
    """Override require_company on the ideation route, return the company id."""
    import app.main as main_mod
    import app.routes.ideation as ideation_route
    from app.auth import CompanyContext

    db = isolated_settings["supabase"]
    cid = _seed_company(db, "co-X")
    require_company = ideation_route.require_company
    main_mod.app.dependency_overrides[require_company] = lambda: CompanyContext(
        company_id=cid, role="admin", user_id="u1")
    yield cid
    main_mod.app.dependency_overrides.pop(require_company, None)


def _seed_item(db, cid, theme_id, rank, score, *, status="proposed",
               shortlisted=True):
    import uuid
    iid = str(uuid.uuid4())
    db.table("ideation_items").insert({
        "id": iid, "enterprise_id": cid, "theme_id": theme_id,
        "title": f"item {rank}", "tag": "something_new", "rank": rank,
        "score": score, "status": status, "shortlisted": shortlisted,
        "reasoning": "r",
    }).execute()
    return iid


def _seed_brief(db, slug):
    """Seed a current weekly brief for a company slug (briefs.dataset == slug).

    The ideation GET route gates on a brief existing, so the no-brief →
    empty-page invariant holds; this helper lets the populated tests satisfy
    that gate (the ideation pool is the by-product of a real analysis).
    """
    from app.db.briefs import save_brief
    save_brief(slug, "Week of test", {"summary_headline": "h", "insights": []},
               schema_version=1)


def test_get_ideation_returns_rank_ordered(isolated_settings, _override_company):
    from fastapi.testclient import TestClient
    import app.main as main_mod

    cid = _override_company
    db = isolated_settings["supabase"]
    _seed_brief(db, f"slug-{cid}")   # a brief exists → ideas (the rest) show
    _seed_item(db, cid, "t2", rank=2, score=0.3)
    _seed_item(db, cid, "t1", rank=1, score=0.9)

    client = TestClient(main_mod.app)
    r = client.get("/v1/ideation")
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["count"] == 2
    assert [i["rank"] for i in body["items"]] == [1, 2]   # rank-ascending
    assert body["items"][0]["theme_id"] == "t1"


def test_get_ideation_empty_when_no_brief(isolated_settings, _override_company):
    """No weekly brief has ever been generated → the page is empty even if
    stale/orphaned ideation_items rows exist for the tenant."""
    from fastapi.testclient import TestClient
    import app.main as main_mod

    cid = _override_company
    db = isolated_settings["supabase"]
    # Rows exist in the table, but NO brief for this company.
    _seed_item(db, cid, "t1", rank=1, score=0.9)
    _seed_item(db, cid, "t2", rank=2, score=0.3)

    client = TestClient(main_mod.app)
    r = client.get("/v1/ideation")
    assert r.status_code == 200, r.text
    body = r.json()
    assert body == {"items": [], "count": 0}   # gated on a brief existing


def test_get_ideation_returns_rank_4plus_when_brief_exists(
    isolated_settings, _override_company,
):
    """End-to-end split: a synthesis run puts the top 3 in the brief and the
    rest (rank ≥ 4) in the ideation pool; the route then surfaces exactly
    those (all shortlisted here — small pool)."""
    from fastapi.testclient import TestClient
    import app.main as main_mod
    from app.synthesis import agent as synth
    from app.synthesis import ideation as bl

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
    body = client.get("/v1/ideation").json()
    ideation_ids = {i["theme_id"] for i in body["items"]}
    # The top-3 brief themes are EXCLUDED; only ranks ≥ 4 remain in ideation.
    assert ideation_ids == {t.id for t in rest}
    for t in top3:
        assert t.id not in ideation_ids


def test_patch_ideation_updates_status(isolated_settings, _override_company):
    from fastapi.testclient import TestClient
    import app.main as main_mod

    cid = _override_company
    db = isolated_settings["supabase"]
    iid = _seed_item(db, cid, "t1", rank=1, score=0.9)

    client = TestClient(main_mod.app)
    r = client.patch(f"/v1/ideation/{iid}", json={"status": "done"})
    assert r.status_code == 200, r.text
    assert r.json()["status"] == "done"
    row = db.table("ideation_items").select("*").eq("id", iid).execute().data[0]
    assert row["status"] == "done"


def test_patch_ideation_rejects_bad_status(isolated_settings, _override_company):
    from fastapi.testclient import TestClient
    import app.main as main_mod

    cid = _override_company
    db = isolated_settings["supabase"]
    iid = _seed_item(db, cid, "t1", rank=1, score=0.9)

    client = TestClient(main_mod.app)
    r = client.patch(f"/v1/ideation/{iid}", json={"status": "nonsense"})
    assert r.status_code == 400


def test_patch_ideation_tenant_isolation(isolated_settings, _override_company):
    from fastapi.testclient import TestClient
    import app.main as main_mod

    cid = _override_company   # "co-X" — the authed tenant
    db = isolated_settings["supabase"]
    other = _seed_company(db, "co-OTHER")
    other_item = _seed_item(db, other, "t1", rank=1, score=0.9)

    client = TestClient(main_mod.app)
    # GET only returns the authed tenant's items (none for co-X).
    assert client.get("/v1/ideation").json()["count"] == 0
    # PATCH on another tenant's item → 404 (not found for this tenant).
    r = client.patch(f"/v1/ideation/{other_item}", json={"status": "done"})
    assert r.status_code == 404
    # The other tenant's item is untouched.
    row = db.table("ideation_items").select("*").eq("id", other_item).execute().data[0]
    assert row["status"] == "proposed"


# ─────────────────── replace-not-append (prune stale) ───────────────────

def test_prune_stale_ideation_removes_only_stale_proposed_status(isolated_settings):
    """prune_stale_ideation deletes proposed-state rows (including the legacy
    'backlog' spelling) whose theme isn't in the keep-set, and preserves kept
    themes, user-managed (non-proposed) rows, manual ideas, and other
    tenants."""
    from app.db.ideation import prune_stale_ideation, list_ideation_items
    db = isolated_settings["supabase"]
    _seed_company(db, "ent-A")   # backlog_items.enterprise_id → companies(id)
    _seed_company(db, "ent-B")

    def seed(ent, theme_id, status="proposed"):
        db.table("ideation_items").insert({
            "id": f"{ent}-{theme_id}", "enterprise_id": ent, "theme_id": theme_id,
            "title": theme_id, "rank": 1, "score": 0.5, "status": status,
        }).execute()

    seed("ent-A", "keep-me")
    seed("ent-A", "stale-1")
    seed("ent-A", "stale-2", status="backlog")       # legacy spelling → still pruned
    seed("ent-A", "done-item", status="done")        # user-managed → preserved
    seed("ent-A", "manual:user-idea")                # user-added → preserved
    seed("ent-B", "other-tenant")                    # different tenant → untouched

    removed = prune_stale_ideation("ent-A", {"keep-me"})
    assert removed == 2                              # stale-1, stale-2

    remaining = {r["theme_id"] for r in list_ideation_items("ent-A")}
    # kept + user-managed + manual survive
    assert remaining == {"keep-me", "done-item", "manual:user-idea"}
    # other tenant is never touched
    assert {r["theme_id"] for r in list_ideation_items("ent-B")} == {"other-tenant"}


def test_sequence_ideation_replaces_instead_of_appending(facade, isolated_settings):
    """A re-sequence REPLACES the auto-generated pool: themes that dropped out
    (here, moved into the brief) are pruned instead of accumulating, new themes
    appear, and a user-marked item survives. Regression for the 153-item
    backlog bloat."""
    from app.synthesis import ideation as bl
    from app.db.ideation import list_ideation_items, update_ideation_status

    _seed_company(isolated_settings["supabase"], "ent-A")
    alpha = _seed_theme_with_signals(facade, "ent-A", "alpha", [
        ("revenue", "deal_blocker", {"revenue_at_risk_usd": 500000}, 0)])
    beta = _seed_theme_with_signals(facade, "ent-A", "beta", [
        ("customer_voice", "feature_request", {}, 0)])

    # Run 1: both alpha + beta land in the pool.
    with patch.object(bl, "llm_call",
                      return_value=_llm_result(_triage_for(alpha.id, beta.id))):
        bl.sequence_ideation(facade, "ent-A", exclude_theme_ids=[])
    rows1 = list_ideation_items("ent-A")
    assert {r["theme_id"] for r in rows1} == {alpha.id, beta.id}

    # User marks beta's item done (a lifecycle change we must never clobber).
    beta_row = next(r for r in rows1 if r["theme_id"] == beta.id)
    update_ideation_status("ent-A", beta_row["id"], "done")

    # Run 2: alpha + beta now made the brief (excluded); a NEW theme gamma converges.
    gamma = _seed_theme_with_signals(facade, "ent-A", "gamma", [
        ("revenue", "deal_blocker", {"revenue_at_risk_usd": 300000}, 0)])
    with patch.object(bl, "llm_call",
                      return_value=_llm_result(_triage_for(gamma.id))):
        bl.sequence_ideation(facade, "ent-A", exclude_theme_ids=[alpha.id, beta.id])

    rows2 = list_ideation_items("ent-A")
    tids = {r["theme_id"] for r in rows2}
    assert alpha.id not in tids          # pruned (was 'backlog', dropped out) — no append
    assert gamma.id in tids              # fresh theme sequenced
    assert beta.id in tids               # user-managed 'done' item preserved
    assert len(rows2) == 2               # NOT 3 — the backlog did not accumulate


# ─────────────────────── prioritization shortlist ───────────────────────

def _prioritize_for(theme_ids, shortlist):
    """A full prioritize payload: items for every theme + an ordered shortlist
    [{theme_id, why_now}] (the LLM's pick of what deserves a visible slot)."""
    return {
        "items": [
            {"theme_id": tid, "tag": "something_new", "reasoning": f"rationale {i}"}
            for i, tid in enumerate(theme_ids)
        ],
        "shortlist": [
            {"theme_id": tid, "why_now": f"why {tid}"} for tid in shortlist
        ],
    }


def _seed_n_themes(facade, ent, n):
    """n themes with strictly descending breadth → deterministic score order."""
    sources = ("revenue", "customer_voice", "project_mgmt", "communication")
    themes = []
    for i in range(n):
        breadth = max(1, len(sources) - i)
        themes.append(_seed_theme_with_signals(
            facade, ent, f"theme-{i}",
            [(s, "feature_request", {}, 0) for s in sources[:breadth]]))
    return themes


def test_shortlist_orders_picked_ideas_first(facade, isolated_settings, monkeypatch):
    """The LLM's shortlist is honored: picked ideas persist shortlisted=True with
    rank equal to the SHORTLIST order (even against the deterministic score
    order); the unpicked tail follows, hidden, with contiguous ranks."""
    from app.synthesis import ideation as idn

    db = isolated_settings["supabase"]
    _seed_company(db, "ent-A")
    monkeypatch.setattr(idn, "SHORTLIST_MIN", 2)
    t = _seed_n_themes(facade, "ent-A", 4)  # score order: t[0] > t[1] > t[2] > t[3]

    # LLM picks t[2] then t[0] — deliberately NOT score order.
    payload = _prioritize_for([th.id for th in t], shortlist=[t[2].id, t[0].id])
    with patch.object(idn, "llm_call", return_value=_llm_result(payload)):
        rows = idn.sequence_ideation(facade, "ent-A", exclude_theme_ids=[])

    by_theme = {r["theme_id"]: r for r in rows}
    assert by_theme[t[2].id]["shortlisted"] and by_theme[t[2].id]["rank"] == 1
    assert by_theme[t[0].id]["shortlisted"] and by_theme[t[0].id]["rank"] == 2
    # Unpicked tail: persisted, hidden, contiguous ranks in score order.
    assert not by_theme[t[1].id]["shortlisted"] and by_theme[t[1].id]["rank"] == 3
    assert not by_theme[t[3].id]["shortlisted"] and by_theme[t[3].id]["rank"] == 4
    # why_now becomes the visible rationale for shortlisted rows.
    assert by_theme[t[2].id]["reasoning"] == f"why {t[2].id}"
    # Persistence matches.
    stored = {r["theme_id"]: r for r in db.table("ideation_items").select("*")
              .eq("enterprise_id", "ent-A").execute().data}
    assert stored[t[2].id]["shortlisted"] is True
    assert stored[t[1].id]["shortlisted"] is False


def test_shortlist_capped_at_max(facade, isolated_settings, monkeypatch):
    """Even if the LLM over-picks, only SHORTLIST_MAX ideas become visible."""
    from app.synthesis import ideation as idn

    _seed_company(isolated_settings["supabase"], "ent-A")
    monkeypatch.setattr(idn, "SHORTLIST_MIN", 1)
    monkeypatch.setattr(idn, "SHORTLIST_MAX", 2)
    t = _seed_n_themes(facade, "ent-A", 4)

    payload = _prioritize_for([th.id for th in t],
                              shortlist=[th.id for th in t])  # picks all 4
    with patch.object(idn, "llm_call", return_value=_llm_result(payload)):
        rows = idn.sequence_ideation(facade, "ent-A", exclude_theme_ids=[])

    assert sum(1 for r in rows if r["shortlisted"]) == 2
    assert [r["theme_id"] for r in rows if r["shortlisted"]] == [t[0].id, t[1].id]


def test_llm_failure_falls_back_to_deterministic_shortlist(
        facade, isolated_settings, monkeypatch):
    """llm_call raising is caught IN the sequencer (fail-open): the pool still
    persists, the top FALLBACK_SHORTLIST by score are shortlisted, and the
    decision log records the fallback."""
    from app.synthesis import ideation as idn

    db = isolated_settings["supabase"]
    _seed_company(db, "ent-A")
    monkeypatch.setattr(idn, "FALLBACK_SHORTLIST", 1)
    t = _seed_n_themes(facade, "ent-A", 2)

    with patch.object(idn, "llm_call", side_effect=RuntimeError("LLM down")):
        rows = idn.sequence_ideation(facade, "ent-A", exclude_theme_ids=[])

    assert len(rows) == 2                               # run persisted anyway
    by_theme = {r["theme_id"]: r for r in rows}
    assert by_theme[t[0].id]["shortlisted"] is True      # top by score
    assert by_theme[t[1].id]["shortlisted"] is False
    assert by_theme[t[0].id]["tag"] is None              # no LLM annotations

    logs = db.table("agent_decision_log").select("*").eq(
        "enterprise_id", "ent-A").execute().data
    seq = [r for r in logs if r["decision_type"] == "sequence"]
    assert seq[0]["factors"]["shortlist_source"] == "deterministic_fallback"
    assert seq[0]["factors"]["shortlist_count"] == 1


def test_small_pool_full_shortlist_is_llm_sourced(facade, isolated_settings):
    """Fewer candidates than SHORTLIST_MIN: the LLM shortlisting them ALL is
    valid (no fallback) — 'fewer only when fewer distinct candidates exist'."""
    from app.synthesis import ideation as idn

    db = isolated_settings["supabase"]
    _seed_company(db, "ent-A")
    t = _seed_n_themes(facade, "ent-A", 2)

    payload = _prioritize_for([th.id for th in t], shortlist=[th.id for th in t])
    with patch.object(idn, "llm_call", return_value=_llm_result(payload)):
        rows = idn.sequence_ideation(facade, "ent-A", exclude_theme_ids=[])

    assert all(r["shortlisted"] for r in rows)
    logs = db.table("agent_decision_log").select("*").eq(
        "enterprise_id", "ent-A").execute().data
    seq = [r for r in logs if r["decision_type"] == "sequence"]
    assert seq[0]["factors"]["shortlist_source"] == "llm"


def test_shortlist_never_includes_dropped_duplicates(facade, isolated_settings):
    """A theme the triage marked duplicate can't be shortlisted even if the LLM
    (inconsistently) also put it in the shortlist."""
    from app.synthesis import ideation as idn

    _seed_company(isolated_settings["supabase"], "ent-A")
    t = _seed_n_themes(facade, "ent-A", 2)

    payload = _prioritize_for([th.id for th in t], shortlist=[t[0].id, t[1].id])
    payload["items"][1]["duplicate_of"] = t[0].id       # t1 duplicates t0
    with patch.object(idn, "llm_call", return_value=_llm_result(payload)):
        rows = idn.sequence_ideation(facade, "ent-A", exclude_theme_ids=[])

    assert [r["theme_id"] for r in rows] == [t[0].id]   # dup dropped entirely


# ─────────────────────── manual ideas + visibility ───────────────────────

def test_manual_item_is_shortlisted_and_survives_prune(isolated_settings):
    """A user-added idea is born proposed + shortlisted, and prune (which runs on
    every re-sequence with a keep-set that can never contain a manual synthetic
    theme_id) must NOT delete it. Regression: the old prune wiped manual rows on
    every weekly run."""
    from app.db.ideation import (
        create_manual_ideation_item, list_visible_ideation_items,
        prune_stale_ideation,
    )

    db = isolated_settings["supabase"]
    _seed_company(db, "ent-A")
    row = create_manual_ideation_item("ent-A", title="My idea", tag=None)
    assert row["status"] == "proposed"
    assert row["shortlisted"] is True
    assert row["theme_id"].startswith("manual:")

    removed = prune_stale_ideation("ent-A", {"some-other-theme"})
    assert removed == 0
    visible = list_visible_ideation_items("ent-A")
    assert [r["id"] for r in visible] == [row["id"]]


def test_get_ideation_returns_only_visible_rows(isolated_settings, _override_company):
    """GET /v1/ideation hides the non-shortlisted tail and done/dismissed rows;
    it shows shortlisted, manual, and in_progress rows."""
    from fastapi.testclient import TestClient
    import app.main as main_mod

    cid = _override_company
    db = isolated_settings["supabase"]
    _seed_brief(db, f"slug-{cid}")
    _seed_item(db, cid, "t-short", rank=1, score=0.9)                      # shortlisted
    _seed_item(db, cid, "t-tail", rank=2, score=0.5, shortlisted=False)    # hidden tail
    _seed_item(db, cid, "t-wip", rank=3, score=0.4, shortlisted=False,
               status="in_progress")                                       # visible
    _seed_item(db, cid, "manual:u1", rank=4, score=0.0, shortlisted=False) # visible (manual)
    _seed_item(db, cid, "t-done", rank=5, score=0.3, status="done")        # hidden
    _seed_item(db, cid, "t-gone", rank=6, score=0.2, status="dismissed")   # hidden

    client = TestClient(main_mod.app)
    body = client.get("/v1/ideation").json()
    assert {i["theme_id"] for i in body["items"]} == {"t-short", "t-wip", "manual:u1"}
    assert body["count"] == 3
