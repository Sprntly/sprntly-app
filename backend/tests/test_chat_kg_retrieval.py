"""Tests for the Ask × Knowledge Graph bridge (#18).

Two layers:

1. `app.graph.retrieval.retrieve_context` — pure, tenant-scoped retrieval.
   The fake Supabase has no pgvector, so `find_candidates` returns [] there;
   we mock embeddings + find_candidates and assert the bundle is composed from
   the REAL seeded `active_signals` / `edges_to` / `load_session_context`
   reads. What's genuinely exercised: edge-walking, dedup, ranking, the token
   budget cap, session-context folding, and tenant isolation. The pgvector
   kNN ordering itself is mocked (integration-tested against real Supabase).

2. `app.ask_runner.compose_ask_answer` + POST /v1/ask — the wiring: combined
   corpus+KG prompt, the KG context section, corpus-only fallback, and the
   decision-log row carrying kg_refs.
"""
from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from unittest.mock import patch

import pytest


# ─────────────────────────── seeding helpers ───────────────────────────


@pytest.fixture
def facade(isolated_settings):
    from app.graph import GraphFacade

    return GraphFacade()


def _seed_theme_with_signals(facade, ent, label, specs):
    """specs: list of (source_type, kind, content, props, age_days).
    Wires each signal to the theme via a REQUESTS edge (signal → theme)."""
    from app.graph.types import Entity, Relationship, Signal

    theme = Entity(enterprise_id=ent, type="theme", canonical_label=label)
    facade.create_entity(ent, theme)
    now = datetime.now(timezone.utc)
    sigs = []
    for st, kind, content, props, age in specs:
        sig = Signal(
            enterprise_id=ent,
            source_type=st,
            kind=kind,
            content=content,
            properties=props,
            valid_at=now - timedelta(days=age),
        )
        facade.write_signal(ent, sig)
        facade.write_relationship(
            ent,
            Relationship(
                enterprise_id=ent,
                type="REQUESTS",
                source_kind="signal",
                source_id=sig.id,
                target_kind="entity",
                target_id=theme.id,
            ),
        )
        sigs.append(sig)
    return theme, sigs


def _patch_candidates(theme_scores):
    """Patch GraphFacade.find_candidates to return (Entity, score) tuples —
    stands in for the pgvector kNN the fake backend can't run. `theme_scores`
    is a list of (theme_entity, score)."""
    from app.graph.facade import GraphFacade

    return patch.object(
        GraphFacade, "find_candidates", lambda self, ent, typ, vec, k=10: list(theme_scores)
    )


def _patch_embed():
    """Patch the embeddings call retrieval imports lazily."""
    return patch(
        "app.graph.embeddings.embed_texts",
        side_effect=lambda texts, **k: [[0.1] * 4 for _ in texts],
    )


# ─────────────────────────── retrieval: composition ───────────────────────────


def test_retrieve_context_returns_ranked_signals_and_themes(facade):
    from app.graph.retrieval import retrieve_context

    theme, _ = _seed_theme_with_signals(
        facade,
        "ent-A",
        "Pipeline health",
        [
            ("revenue", "deal_blocker", "Acme $1.4M stuck on SSO", {}, 1),
            ("customer_voice", "feature_request", "Buyers want SSO", {}, 2),
        ],
    )
    with _patch_embed(), _patch_candidates([(theme, 0.92)]):
        bundle = retrieve_context(facade, "ent-A", "How is my pipeline?")

    assert bundle["empty"] is False
    assert [t["label"] for t in bundle["themes"]] == ["Pipeline health"]
    assert {s["content"] for s in bundle["signals"]} == {
        "Acme $1.4M stuck on SSO",
        "Buyers want SSO",
    }
    # Every signal carries content + source_type + provenance for grounding.
    for s in bundle["signals"]:
        assert set(s) >= {"signal_id", "content", "source_type", "provenance", "rank"}
    # Ranked descending.
    ranks = [s["rank"] for s in bundle["signals"]]
    assert ranks == sorted(ranks, reverse=True)


def test_retrieve_context_theme_match_boosts_above_recent(facade):
    """A signal wired to a matched theme outranks an equally-fresh recent
    signal with no theme boost."""
    from app.graph.retrieval import retrieve_context

    theme, _ = _seed_theme_with_signals(
        facade,
        "ent-A",
        "Onboarding",
        [("customer_voice", "feature_request", "matched-theme signal", {}, 0)],
    )
    # An unrelated, equally fresh signal with no edge to any matched theme.
    from app.graph.types import Signal

    facade.write_signal(
        "ent-A",
        Signal(
            enterprise_id="ent-A",
            source_type="customer_voice",
            kind="feature_request",
            content="loose recent signal",
            valid_at=datetime.now(timezone.utc),
        ),
    )
    with _patch_embed(), _patch_candidates([(theme, 0.9)]):
        bundle = retrieve_context(facade, "ent-A", "q")

    contents = [s["content"] for s in bundle["signals"]]
    assert contents[0] == "matched-theme signal"
    assert "loose recent signal" in contents


def test_retrieve_context_dedupes_signal_across_paths(facade):
    """A signal reachable via BOTH a matched theme AND the recent-signals pull
    appears once."""
    from app.graph.retrieval import retrieve_context

    theme, sigs = _seed_theme_with_signals(
        facade,
        "ent-A",
        "Churn",
        [("revenue", "deal_blocker", "the one signal", {}, 0)],
    )
    with _patch_embed(), _patch_candidates([(theme, 0.8)]):
        bundle = retrieve_context(facade, "ent-A", "q")

    ids = [s["signal_id"] for s in bundle["signals"]]
    assert ids.count(sigs[0].id) == 1


def test_retrieve_context_skips_superseded_signals(facade):
    from app.graph.retrieval import retrieve_context

    theme, sigs = _seed_theme_with_signals(
        facade,
        "ent-A",
        "Theme",
        [
            ("revenue", "deal_blocker", "stale claim", {}, 0),
            ("revenue", "deal_reopened", "fresh claim", {}, 0),
        ],
    )
    facade.supersede_signal("ent-A", sigs[0].id, sigs[1].id)
    with _patch_embed(), _patch_candidates([(theme, 0.8)]):
        bundle = retrieve_context(facade, "ent-A", "q")

    contents = [s["content"] for s in bundle["signals"]]
    assert "stale claim" not in contents
    assert "fresh claim" in contents


def test_retrieve_context_folds_in_session_context(facade):
    from app.graph.retrieval import retrieve_context
    from app.graph.types import Entity

    facade.create_entity(
        "ent-A",
        Entity(enterprise_id="ent-A", type="hypothesis", canonical_label="SSO unblocks enterprise"),
    )
    facade.create_entity(
        "ent-A",
        Entity(enterprise_id="ent-A", type="decision", canonical_label="Prioritize SSO this quarter"),
    )
    facade.create_entity(
        "ent-A",
        Entity(enterprise_id="ent-A", type="outcome", canonical_label="Churn down 4pts"),
    )
    with _patch_embed(), _patch_candidates([]):
        bundle = retrieve_context(facade, "ent-A", "q")

    assert [h["label"] for h in bundle["hypotheses"]] == ["SSO unblocks enterprise"]
    assert [d["label"] for d in bundle["decisions"]] == ["Prioritize SSO this quarter"]
    assert [o["label"] for o in bundle["outcomes"]] == ["Churn down 4pts"]
    assert bundle["empty"] is False


def test_retrieve_context_recent_signals_without_theme_match(facade):
    """No theme match (find_candidates → []) still surfaces recent non-stale
    signals — covers fresh connector data not yet wired to a theme."""
    from app.graph.retrieval import retrieve_context
    from app.graph.types import Signal

    facade.write_signal(
        "ent-A",
        Signal(
            enterprise_id="ent-A",
            source_type="analytics",
            kind="metric_shift",
            content="DAU dropped 12%",
            valid_at=datetime.now(timezone.utc),
        ),
    )
    with _patch_embed(), _patch_candidates([]):
        bundle = retrieve_context(facade, "ent-A", "what changed?")

    assert [s["content"] for s in bundle["signals"]] == ["DAU dropped 12%"]
    assert bundle["themes"] == []


# ─────────────────────────── retrieval: budget + empty ───────────────────────────


def test_retrieve_context_token_budget_caps_signals(facade):
    from app.graph.retrieval import retrieve_context

    big = "x" * 4000  # ~1000 tokens each at 4 chars/token
    theme, _ = _seed_theme_with_signals(
        facade,
        "ent-A",
        "Theme",
        [("revenue", "deal_blocker", f"{big}-{i}", {}, i) for i in range(10)],
    )
    with _patch_embed(), _patch_candidates([(theme, 0.9)]):
        bundle = retrieve_context(facade, "ent-A", "q", token_budget=2500)

    # 2500-token budget / ~1000 tokens per signal → at most ~3 signals.
    assert 1 <= len(bundle["signals"]) <= 3
    assert bundle["token_estimate"] <= 2500 + 1000  # last one may straddle the cap


def test_retrieve_context_empty_kg_returns_empty_bundle(facade):
    from app.graph.retrieval import retrieve_context

    with _patch_embed(), _patch_candidates([]):
        bundle = retrieve_context(facade, "ent-empty", "anything?")

    assert bundle["empty"] is True
    assert bundle["signals"] == []
    assert bundle["themes"] == []
    assert bundle["kg_refs"] == []


def test_retrieve_context_resilient_when_embeddings_unavailable(facade):
    """If embed_texts raises (e.g. no OPENAI key), retrieval degrades to
    recent-signals-only instead of failing."""
    from app.graph.retrieval import retrieve_context
    from app.graph.types import Signal

    facade.write_signal(
        "ent-A",
        Signal(
            enterprise_id="ent-A",
            source_type="analytics",
            kind="metric_shift",
            content="recent only",
            valid_at=datetime.now(timezone.utc),
        ),
    )
    with patch(
        "app.graph.embeddings.embed_texts",
        side_effect=RuntimeError("OPENAI_API_KEY not configured"),
    ):
        bundle = retrieve_context(facade, "ent-A", "q")

    assert [s["content"] for s in bundle["signals"]] == ["recent only"]
    assert bundle["themes"] == []


def test_kg_refs_collects_signal_theme_and_entity_ids(facade):
    from app.graph.retrieval import retrieve_context
    from app.graph.types import Entity

    theme, sigs = _seed_theme_with_signals(
        facade,
        "ent-A",
        "Theme",
        [("revenue", "deal_blocker", "sig", {}, 0)],
    )
    dec = Entity(enterprise_id="ent-A", type="decision", canonical_label="ship it")
    facade.create_entity("ent-A", dec)
    with _patch_embed(), _patch_candidates([(theme, 0.9)]):
        bundle = retrieve_context(facade, "ent-A", "q")

    assert sigs[0].id in bundle["kg_refs"]
    assert theme.id in bundle["kg_refs"]
    assert dec.id in bundle["kg_refs"]


# ─────────────────────────── tenant isolation ───────────────────────────


def test_retrieve_context_tenant_isolation(facade):
    """ent-B's signals never leak into ent-A's bundle, even with an identical
    theme match shape."""
    from app.graph.retrieval import retrieve_context

    theme_a, _ = _seed_theme_with_signals(
        facade, "ent-A", "Shared", [("revenue", "deal_blocker", "A-only signal", {}, 0)]
    )
    _seed_theme_with_signals(
        facade, "ent-B", "Shared", [("revenue", "deal_blocker", "B-only signal", {}, 0)]
    )
    # Even if the kNN mock (wrongly) returned ent-A's theme, edges_to is tenant-
    # scoped, so a cross-tenant query can't read the other tenant's signals.
    with _patch_embed(), _patch_candidates([(theme_a, 0.9)]):
        bundle_a = retrieve_context(facade, "ent-A", "q")
    contents_a = [s["content"] for s in bundle_a["signals"]]
    assert "A-only signal" in contents_a
    assert "B-only signal" not in contents_a

    with _patch_embed(), _patch_candidates([]):
        bundle_b = retrieve_context(facade, "ent-B", "q")
    contents_b = [s["content"] for s in bundle_b["signals"]]
    assert "B-only signal" in contents_b
    assert "A-only signal" not in contents_b


# ─────────────────────────── render section ───────────────────────────


def test_render_context_section_includes_signals_and_provenance(facade):
    from app.graph.retrieval import render_context_section, retrieve_context

    theme, _ = _seed_theme_with_signals(
        facade,
        "ent-A",
        "Pipeline",
        [("revenue", "deal_blocker", "Acme blocked on SSO", {}, 0)],
    )
    with _patch_embed(), _patch_candidates([(theme, 0.9)]):
        bundle = retrieve_context(facade, "ent-A", "q")
    text = render_context_section(bundle)
    assert "KNOWLEDGE GRAPH CONTEXT" in text
    assert "Acme blocked on SSO" in text
    assert "revenue" in text  # source_type surfaced for citation


def test_render_context_section_empty_bundle_is_blank():
    from app.graph.retrieval import render_context_section

    assert render_context_section({"empty": True}) == ""
    assert render_context_section({}) == ""


# ─────────────────────────── compose_ask_answer wiring ───────────────────────────


def test_compose_ask_answer_corpus_only_when_no_enterprise(
    isolated_settings, fake_llm
):
    """No tenant → corpus-only path, identical to pre-#18. No KG section in the
    prompt, no decision-log row."""
    from app import ask_runner

    ds = isolated_settings["data_dir"] / "asurion"
    ds.mkdir(exist_ok=True)
    (ds / "a.md").write_text("legacy corpus body")
    fake_llm["payload"] = {
        "answer": "x", "key_points": [], "citations": [], "confidence": 0.5, "unanswered": "",
    }

    ask_runner.compose_ask_answer("asurion", "What changed?", enterprise_id=None)

    assert len(fake_llm["calls"]) == 1
    user = fake_llm["calls"][0]["user"]
    assert "KNOWLEDGE GRAPH CONTEXT" not in user
    rows = (
        isolated_settings["supabase"].table("agent_decision_log").select("*").execute().data
    )
    assert rows == []


def test_compose_ask_answer_injects_kg_section_and_logs_refs(
    isolated_settings, fake_llm, facade
):
    """With a tenant + seeded KG: the prompt gets a KG section AND a decision-log
    row lands with agent='ask', decision_type='answer', kg_refs populated."""
    from app import ask_runner

    ds = isolated_settings["data_dir"] / "asurion"
    ds.mkdir(exist_ok=True)
    (ds / "a.md").write_text("legacy corpus body")
    fake_llm["payload"] = {
        "answer": "grounded", "key_points": ["k"], "citations": [], "confidence": 0.7,
        "unanswered": "",
    }
    theme, sigs = _seed_theme_with_signals(
        facade,
        "co-1",
        "Pipeline",
        [("revenue", "deal_blocker", "Acme blocked on SSO", {}, 0)],
    )

    with _patch_embed(), _patch_candidates([(theme, 0.9)]):
        ask_runner.compose_ask_answer("asurion", "How is pipeline?", enterprise_id="co-1")

    user = fake_llm["calls"][0]["user"]
    assert "KNOWLEDGE GRAPH CONTEXT" in user
    assert "Acme blocked on SSO" in user

    rows = (
        isolated_settings["supabase"].table("agent_decision_log").select("*").execute().data
    )
    assert len(rows) == 1
    row = rows[0]
    assert row["agent"] == "ask"
    assert row["decision_type"] == "answer"
    assert row["enterprise_id"] == "co-1"
    kg_refs = row["kg_refs"]
    if isinstance(kg_refs, str):
        kg_refs = json.loads(kg_refs)
    assert sigs[0].id in kg_refs
    assert theme.id in kg_refs


def test_compose_ask_answer_empty_kg_falls_back_to_corpus_only(
    isolated_settings, fake_llm
):
    """Tenant resolves but its KG is empty → corpus-only prompt; the decision
    log still records the ask with kg_used=False and empty kg_refs."""
    from app import ask_runner

    ds = isolated_settings["data_dir"] / "asurion"
    ds.mkdir(exist_ok=True)
    (ds / "a.md").write_text("legacy corpus body")
    fake_llm["payload"] = {
        "answer": "x", "key_points": [], "citations": [], "confidence": 0.5, "unanswered": "",
    }

    with _patch_embed(), _patch_candidates([]):
        ask_runner.compose_ask_answer("asurion", "q?", enterprise_id="co-empty")

    user = fake_llm["calls"][0]["user"]
    assert "KNOWLEDGE GRAPH CONTEXT" not in user
    rows = (
        isolated_settings["supabase"].table("agent_decision_log").select("*").execute().data
    )
    assert len(rows) == 1
    factors = rows[0]["factors"]
    if isinstance(factors, str):
        factors = json.loads(factors)
    assert factors["kg_used"] is False
    kg_refs = rows[0]["kg_refs"]
    if isinstance(kg_refs, str):
        kg_refs = json.loads(kg_refs)
    assert kg_refs == []


# ─────────────────────────── route: POST /v1/ask ───────────────────────────


def _seed_corpus(data_dir, dataset="asurion", body="some corpus body"):
    ds = data_dir / dataset
    ds.mkdir(exist_ok=True)
    (ds / "a.md").write_text(body)


def test_ask_route_uses_kg_context_when_signals_exist(
    tenant_client, isolated_settings, fake_llm
):
    # The Ask route now requires a company (require_company) AND the dataset slug
    # must resolve to that company (require_owned_dataset). Seed a company whose
    # slug == the dataset, then seed KG signals under that company's id so the
    # resolved tenant's graph carries them into the answer.
    t = tenant_client.make(slug="asurion")
    _seed_corpus(isolated_settings["data_dir"])
    fake_llm["payload"] = {
        "answer": "grounded", "key_points": [], "citations": [], "confidence": 0.8,
        "unanswered": "",
    }
    from app.graph import GraphFacade

    facade = GraphFacade()
    theme, _ = _seed_theme_with_signals(
        facade,
        t.company_id,
        "Pipeline",
        [("revenue", "deal_blocker", "Acme blocked on SSO", {}, 0)],
    )

    with _patch_embed(), _patch_candidates([(theme, 0.9)]):
        resp = t.client.post(
            "/v1/ask", json={"question": "How is my pipeline?", "dataset": "asurion"}
        )
    assert resp.status_code == 200
    assert resp.json()["citations"] == []  # still stripped
    user = fake_llm["calls"][-1]["user"]
    assert "KNOWLEDGE GRAPH CONTEXT" in user
    assert "Acme blocked on SSO" in user


def test_ask_route_corpus_only_for_legacy_session(
    tenant_client, isolated_settings, fake_llm
):
    """When the resolved company's KG is EMPTY (no signals seeded), the Ask route
    falls back to corpus-only — response shape unchanged. This is the pre-#18
    corpus-only behaviour, now reached via a resolved-but-empty tenant rather than
    an unresolved legacy session (the route requires a company after the
    tenant-isolation fix)."""
    t = tenant_client.make(slug="asurion")
    _seed_corpus(isolated_settings["data_dir"])
    fake_llm["payload"] = {
        "answer": "x", "key_points": [], "citations": [], "confidence": 0.5, "unanswered": "",
    }
    resp = t.client.post(
        "/v1/ask", json={"question": "What is churn?", "dataset": "asurion"}
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["answer"] == "x"
    assert "KNOWLEDGE GRAPH CONTEXT" not in fake_llm["calls"][-1]["user"]
