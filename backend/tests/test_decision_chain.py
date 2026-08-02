"""Tests for `app.graph.decision_chain` — the write path for the reserved
decision/outcome/artifact ledger spine (hypothesis was already written by
`synthesis.agent.run_synthesis`; this module writes what comes after).

Layers under test:

1. Unit tests per write function — right entity type, right properties, right
   edge source_kind/target_kind/type, using `GraphFacade` only.
2. An end-to-end chain test that seeds a hypothesis and drives the REAL
   product triggers in order (a real `prd_runner._run_sync` PRD generation,
   then a real `PATCH /v1/ideation/{id}` status='done' call) and asserts the
   full chain hypothesis -> decision -> artifact -> outcome -> hypothesis is
   traceable via the same facade primitives / resolver
   (`graph.retrieval.resolve_insight_hypothesis`, `GraphFacade.edges_from`)
   the existing evidence-trail code already uses.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from app.graph.decision_chain import (
    ARTIFACT_TO_OUTCOME_TYPE,
    artifacts_for_hypothesis,
    create_artifact_from_decision,
    create_outcome_from_artifact,
    promote_hypothesis_to_decision,
    validate_hypothesis_from_outcome,
)

COMPANY_ID = "co-decision-chain"


@pytest.fixture
def facade(isolated_settings):
    from app.graph import GraphFacade

    return GraphFacade()


def _seed_company(db, *, company_id=COMPANY_ID, slug="dc-co"):
    existing = db.table("companies").select("id").eq("id", company_id).execute().data
    if not existing:
        db.table("companies").insert(
            {"id": company_id, "slug": slug, "display_name": slug.title()}
        ).execute()


def _seed_hypothesis(facade, ent, *, theme_label="Checkout broken",
                      insight_title="Fix checkout"):
    from app.graph.types import Entity, Relationship

    theme = Entity(enterprise_id=ent, type="theme", canonical_label=theme_label)
    facade.create_entity(ent, theme)
    hyp = Entity(
        enterprise_id=ent, type="hypothesis", canonical_label=insight_title[:200],
        properties={"claim": "ship the fix", "tag": "something_broken",
                    "theme_id": theme.id},
    )
    facade.create_entity(ent, hyp)
    facade.write_relationship(ent, Relationship(
        enterprise_id=ent, type="ADDRESSES", source_kind="entity",
        source_id=hyp.id, target_kind="entity", target_id=theme.id))
    return theme, hyp


# ─────────────────────────── unit tests: per write path ───────────────────────────


def test_promote_hypothesis_to_decision_writes_entity_and_edge(facade):
    _seed_company(facade._client)
    theme, hyp = _seed_hypothesis(facade, COMPANY_ID)

    decision = promote_hypothesis_to_decision(
        facade, COMPANY_ID, hyp.id, label="Ship checkout fix",
        properties={"prd_id": 1, "brief_id": 2, "insight_index": 0},
        provenance={"agent": "prd", "trigger": "generate_prd"},
    )

    stored = facade.get_entity(COMPANY_ID, decision.id)
    assert stored is not None
    assert stored.type == "decision"
    assert stored.canonical_label == "Ship checkout fix"
    assert stored.properties["hypothesis_id"] == hyp.id
    assert stored.properties["prd_id"] == 1

    edges = facade.edges_from(COMPANY_ID, hyp.id, type="PROMOTED_TO")
    assert len(edges) == 1
    edge = edges[0]
    assert edge.source_kind == "entity" and edge.source_id == hyp.id
    assert edge.target_kind == "entity" and edge.target_id == decision.id


def test_create_artifact_from_decision_writes_entity_and_edge(facade):
    _seed_company(facade._client)
    theme, hyp = _seed_hypothesis(facade, COMPANY_ID)
    decision = promote_hypothesis_to_decision(
        facade, COMPANY_ID, hyp.id, label="Ship checkout fix")

    artifact = create_artifact_from_decision(
        facade, COMPANY_ID, decision.id, label="Checkout fix PRD",
        properties={"prd_id": 1},
    )

    stored = facade.get_entity(COMPANY_ID, artifact.id)
    assert stored is not None
    assert stored.type == "artifact"
    assert stored.properties["decision_id"] == decision.id
    assert stored.properties["prd_id"] == 1

    edges = facade.edges_from(COMPANY_ID, decision.id, type="RESULTED_IN")
    assert len(edges) == 1
    edge = edges[0]
    assert edge.source_kind == "entity" and edge.source_id == decision.id
    assert edge.target_kind == "entity" and edge.target_id == artifact.id


def test_artifacts_for_hypothesis_walks_promoted_to_resulted_in(facade):
    _seed_company(facade._client)
    theme, hyp = _seed_hypothesis(facade, COMPANY_ID)
    decision = promote_hypothesis_to_decision(
        facade, COMPANY_ID, hyp.id, label="Ship checkout fix")
    artifact = create_artifact_from_decision(
        facade, COMPANY_ID, decision.id, label="Checkout fix PRD")

    found = artifacts_for_hypothesis(facade, COMPANY_ID, hyp.id)
    assert [a.id for a in found] == [artifact.id]


def test_artifacts_for_hypothesis_empty_when_no_decision(facade):
    _seed_company(facade._client)
    theme, hyp = _seed_hypothesis(facade, COMPANY_ID)

    assert artifacts_for_hypothesis(facade, COMPANY_ID, hyp.id) == []


def test_artifacts_for_hypothesis_most_recent_first(facade):
    """Two decisions promoted from the same hypothesis (e.g. re-generated PRD)
    each result in an artifact — the most recently written artifact sorts
    first."""
    _seed_company(facade._client)
    theme, hyp = _seed_hypothesis(facade, COMPANY_ID)

    d1 = promote_hypothesis_to_decision(facade, COMPANY_ID, hyp.id, label="d1")
    a1 = create_artifact_from_decision(facade, COMPANY_ID, d1.id, label="a1")
    d2 = promote_hypothesis_to_decision(facade, COMPANY_ID, hyp.id, label="d2")
    a2 = create_artifact_from_decision(facade, COMPANY_ID, d2.id, label="a2")
    # Force a's transaction_at ordering deterministic even if writes land in
    # the same wall-clock tick on a fast machine.
    facade._tbl("kg_entity").update(
        {"transaction_at": (datetime.now(timezone.utc) - timedelta(minutes=1)).isoformat()}
    ).eq("id", a1.id).execute()

    found = artifacts_for_hypothesis(facade, COMPANY_ID, hyp.id)
    assert [a.id for a in found] == [a2.id, a1.id]


def test_create_outcome_from_artifact_writes_entity_and_edge(facade):
    _seed_company(facade._client)
    theme, hyp = _seed_hypothesis(facade, COMPANY_ID)
    decision = promote_hypothesis_to_decision(facade, COMPANY_ID, hyp.id, label="d")
    artifact = create_artifact_from_decision(facade, COMPANY_ID, decision.id, label="a")

    outcome = create_outcome_from_artifact(
        facade, COMPANY_ID, artifact.id, label="Checkout conversion recovered",
        properties={"theme_id": theme.id, "ideation_item_id": "item-1"},
    )

    stored = facade.get_entity(COMPANY_ID, outcome.id)
    assert stored is not None
    assert stored.type == "outcome"
    assert stored.properties["artifact_id"] == artifact.id
    assert stored.properties["theme_id"] == theme.id

    edges = facade.edges_from(COMPANY_ID, artifact.id, type=ARTIFACT_TO_OUTCOME_TYPE)
    assert len(edges) == 1
    edge = edges[0]
    assert edge.source_kind == "entity" and edge.source_id == artifact.id
    assert edge.target_kind == "entity" and edge.target_id == outcome.id


def test_validate_hypothesis_from_outcome_writes_null_edge_and_property(facade):
    """The manual/PM-annotatable edge: fires with actual_impact=None (no live
    analytics connector) — never silently skipped."""
    _seed_company(facade._client)
    theme, hyp = _seed_hypothesis(facade, COMPANY_ID)
    decision = promote_hypothesis_to_decision(facade, COMPANY_ID, hyp.id, label="d")
    artifact = create_artifact_from_decision(facade, COMPANY_ID, decision.id, label="a")
    outcome = create_outcome_from_artifact(facade, COMPANY_ID, artifact.id, label="o")

    rel = validate_hypothesis_from_outcome(
        facade, COMPANY_ID, outcome.id, hyp.id, actual_impact=None)

    assert rel.type == "VALIDATES"
    assert rel.source_kind == "entity" and rel.source_id == outcome.id
    assert rel.target_kind == "entity" and rel.target_id == hyp.id
    assert rel.properties["actual_impact"] is None

    stored = facade.get_entity(COMPANY_ID, outcome.id)
    assert stored.properties["actual_impact"] is None


def test_validate_hypothesis_from_outcome_can_be_annotated_later(facade):
    """A later manual annotation updates the outcome's actual_impact and
    writes a fresh VALIDATES edge recording the annotation (auditable
    history, not an in-place mutation of the first null edge)."""
    _seed_company(facade._client)
    theme, hyp = _seed_hypothesis(facade, COMPANY_ID)
    decision = promote_hypothesis_to_decision(facade, COMPANY_ID, hyp.id, label="d")
    artifact = create_artifact_from_decision(facade, COMPANY_ID, decision.id, label="a")
    outcome = create_outcome_from_artifact(facade, COMPANY_ID, artifact.id, label="o")
    validate_hypothesis_from_outcome(facade, COMPANY_ID, outcome.id, hyp.id,
                                     actual_impact=None)

    validate_hypothesis_from_outcome(
        facade, COMPANY_ID, outcome.id, hyp.id,
        actual_impact={"metric": "conversion", "delta_pct": 4.2},
        annotated_by="pm-1",
    )

    stored = facade.get_entity(COMPANY_ID, outcome.id)
    assert stored.properties["actual_impact"] == {"metric": "conversion", "delta_pct": 4.2}
    assert stored.properties["actual_impact_annotated_by"] == "pm-1"
    edges = facade.edges_from(COMPANY_ID, outcome.id, type="VALIDATES")
    assert len(edges) == 2  # the initial null edge + the annotated one — history kept


def test_relationship_vocab_accepts_all_new_edge_types():
    """`Relationship.__post_init__`'s closed-vocabulary check passes for every
    edge type this module writes — they're already declared in
    RELATIONSHIP_VOCAB, so no vocab change was needed."""
    from app.graph.types import Relationship

    for t in ("PROMOTED_TO", "RESULTED_IN", ARTIFACT_TO_OUTCOME_TYPE, "VALIDATES"):
        rel = Relationship(
            enterprise_id=COMPANY_ID, type=t, source_kind="entity",
            source_id="a", target_kind="entity", target_id="b",
        )
        assert rel.type == t  # constructs without raising


# ────────────────────────── end-to-end chain test ──────────────────────────


def test_full_chain_hypothesis_to_outcome_and_back_is_traceable(
    isolated_settings, facade, monkeypatch,
):
    """Seed a hypothesis from a real brief, then drive the REAL product
    triggers in order:

      1+2. Generate a PRD from the insight (prd_runner._run_sync) — the PRD
           reaches status='ready' in the same call, so Hypothesis->Decision
           and Decision->Artifact both fire.
      3+4. PATCH the corresponding ideation item to status='done' (the real
           HTTP route) — Artifact->Outcome and Outcome->Hypothesis both fire.

    Then asserts the full chain is traceable forward AND back, using the SAME
    facade primitives (`edges_from`) and resolver
    (`resolve_insight_hypothesis`) the existing evidence-trail code already
    uses — not a bespoke test-only traversal.
    """
    from app import prd_runner
    from app.db.ideation import upsert_ideation_item
    from app.graph.gateway import LLMResult
    from app.graph.retrieval import resolve_insight_hypothesis

    slug = "dc-slug"
    _seed_company(isolated_settings["supabase"], company_id=COMPANY_ID, slug=slug)
    theme, hyp = _seed_hypothesis(
        facade, COMPANY_ID, theme_label="Checkout broken", insight_title="Fix checkout")

    db_mod = isolated_settings["db"]
    payload = {
        "summary_headline": "h",
        "insights": [{"title": "Fix checkout", "theme_id": theme.id}],
        "_schema_version": 1,
    }
    brief_id = db_mod.save_brief(
        dataset=slug, week_label="Week of test", payload=payload, schema_version=1)
    prd_id = db_mod.start_prd(
        brief_id=brief_id, insight_index=0, title="Fix checkout",
        template_version=1, variant="v2")

    def _llm_result(output):
        return LLMResult(
            output=output, model="claude-sonnet-4-6", prompt_version="test",
            input_tokens=10, output_tokens=5, cache_read_input_tokens=0,
            cache_creation_input_tokens=0, cost_usd=0.001, latency_ms=5,
            stop_reason="end_turn",
        )

    monkeypatch.setattr(
        prd_runner, "llm_call",
        lambda **kw: _llm_result("# Part A\nShip the fix.\n"),
    )
    # 1 + 2: Generate PRD (real trigger).
    prd_runner._run_sync(prd_id, brief_id, 0)
    assert db_mod.get_prd(prd_id)["status"] == "ready"

    # 3 + 4: mark the corresponding ideation item done (real trigger, real route).
    iid = "item-fix-checkout"
    isolated_settings["supabase"].table("ideation_items").insert({
        "id": iid, "enterprise_id": COMPANY_ID, "theme_id": theme.id,
        "title": "Fix checkout", "tag": "something_broken", "rank": 1,
        "score": 0.9, "status": "in_progress", "shortlisted": True,
        "reasoning": "r",
    }).execute()

    import app.main as main_mod
    import app.routes.ideation as ideation_route
    from app.auth import CompanyContext
    from fastapi.testclient import TestClient

    require_company = ideation_route.require_company
    main_mod.app.dependency_overrides[require_company] = lambda: CompanyContext(
        company_id=COMPANY_ID, role="admin", user_id="u1")
    try:
        client = TestClient(main_mod.app)
        r = client.patch(f"/v1/ideation/{iid}", json={"status": "done"})
        assert r.status_code == 200, r.text
    finally:
        main_mod.app.dependency_overrides.pop(require_company, None)

    # ── Forward traceability: hypothesis -> decision -> artifact -> outcome ──
    promo = facade.edges_from(COMPANY_ID, hyp.id, type="PROMOTED_TO")
    assert len(promo) == 1
    decision = facade.get_entity(COMPANY_ID, promo[0].target_id)
    assert decision.type == "decision"

    resulted = facade.edges_from(COMPANY_ID, decision.id, type="RESULTED_IN")
    assert len(resulted) == 1
    artifact = facade.get_entity(COMPANY_ID, resulted[0].target_id)
    assert artifact.type == "artifact"

    realized = facade.edges_from(COMPANY_ID, artifact.id, type=ARTIFACT_TO_OUTCOME_TYPE)
    assert len(realized) == 1
    outcome = facade.get_entity(COMPANY_ID, realized[0].target_id)
    assert outcome.type == "outcome"

    # ── Round trip: outcome -> VALIDATES -> the SAME originating hypothesis ──
    validates = facade.edges_from(COMPANY_ID, outcome.id, type="VALIDATES")
    assert len(validates) == 1
    assert validates[0].target_id == hyp.id
    assert outcome.properties.get("actual_impact") is None  # manual/null-first

    # ── Same resolver the evidence-trail code uses re-finds the SAME
    #    hypothesis this whole chain hangs off of. ──
    resolved = resolve_insight_hypothesis(facade, COMPANY_ID, theme.id, "Fix checkout")
    assert resolved is not None and resolved.id == hyp.id


def test_patch_ideation_done_twice_does_not_duplicate_outcome(
    isolated_settings, facade, monkeypatch,
):
    """Idempotency guard: re-PATCHing an already-'done' item must not write a
    second outcome/edge pair."""
    from app import prd_runner
    from app.graph.gateway import LLMResult

    slug = "dc-slug2"
    _seed_company(isolated_settings["supabase"], company_id=COMPANY_ID, slug=slug)
    theme, hyp = _seed_hypothesis(
        facade, COMPANY_ID, theme_label="Onboarding slow", insight_title="Fix onboarding")

    db_mod = isolated_settings["db"]
    payload = {
        "summary_headline": "h",
        "insights": [{"title": "Fix onboarding", "theme_id": theme.id}],
        "_schema_version": 1,
    }
    brief_id = db_mod.save_brief(
        dataset=slug, week_label="Week of test", payload=payload, schema_version=1)
    prd_id = db_mod.start_prd(
        brief_id=brief_id, insight_index=0, title="Fix onboarding",
        template_version=1, variant="v2")

    def _llm_result(output):
        return LLMResult(
            output=output, model="claude-sonnet-4-6", prompt_version="test",
            input_tokens=10, output_tokens=5, cache_read_input_tokens=0,
            cache_creation_input_tokens=0, cost_usd=0.001, latency_ms=5,
            stop_reason="end_turn",
        )

    monkeypatch.setattr(prd_runner, "llm_call",
                        lambda **kw: _llm_result("# Part A\nShip.\n"))
    prd_runner._run_sync(prd_id, brief_id, 0)

    iid = "item-fix-onboarding"
    isolated_settings["supabase"].table("ideation_items").insert({
        "id": iid, "enterprise_id": COMPANY_ID, "theme_id": theme.id,
        "title": "Fix onboarding", "tag": "something_broken", "rank": 1,
        "score": 0.9, "status": "in_progress", "shortlisted": True,
        "reasoning": "r",
    }).execute()

    import app.main as main_mod
    import app.routes.ideation as ideation_route
    from app.auth import CompanyContext
    from fastapi.testclient import TestClient

    require_company = ideation_route.require_company
    main_mod.app.dependency_overrides[require_company] = lambda: CompanyContext(
        company_id=COMPANY_ID, role="admin", user_id="u1")
    try:
        client = TestClient(main_mod.app)
        assert client.patch(f"/v1/ideation/{iid}", json={"status": "done"}).status_code == 200
        assert client.patch(f"/v1/ideation/{iid}", json={"status": "done"}).status_code == 200
    finally:
        main_mod.app.dependency_overrides.pop(require_company, None)

    promo = facade.edges_from(COMPANY_ID, hyp.id, type="PROMOTED_TO")
    decision = facade.get_entity(COMPANY_ID, promo[0].target_id)
    resulted = facade.edges_from(COMPANY_ID, decision.id, type="RESULTED_IN")
    artifact = facade.get_entity(COMPANY_ID, resulted[0].target_id)
    realized = facade.edges_from(COMPANY_ID, artifact.id, type=ARTIFACT_TO_OUTCOME_TYPE)
    assert len(realized) == 1  # one outcome, not two
