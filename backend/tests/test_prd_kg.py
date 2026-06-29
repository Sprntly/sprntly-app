"""Tests for PRD grounding regrounded on the Knowledge Graph.

The PRD agent keeps the `prd-author` skill binding + 2-part split, but now
grounds the prd-author input on the insight's KG evidence trail (the SUPPORTS
signals behind the synthesis-written hypothesis + theme convergence signals)
instead of dumping the markdown corpus — consistent with brief/evidence/ask,
which all answer from the brain.

Two layers under test:

1. `app.graph.retrieval.insight_evidence_trail` — pure, tenant-scoped trail
   resolution over the REAL seeded `query_entities` / `edges_to` / `get_signal`
   reads (insight → theme_id → hypothesis → SUPPORTS signals + theme signals).

2. `app.prd_runner` — the wiring: KG-trail grounding reaches the prd-author
   llm_call input + is cited, decision-log carries kg_refs, empty-trail falls
   back to the corpus, and the 2-part output + storage contract is unchanged.

Mocked at the gateway/facade seam (no Anthropic, no pgvector).
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from app import prd_runner
from app.graph.gateway import LLMResult


# ─────────────────────────── seeding helpers ───────────────────────────


def _seed_company(db, *, company_id: str, slug: str) -> None:
    existing = db.table("companies").select("id").eq("id", company_id).execute().data
    if not existing:
        db.table("companies").insert(
            {"id": company_id, "slug": slug, "display_name": slug.title()}
        ).execute()


def _seed_corpus(data_dir, dataset, body="CORPUS_FALLBACK_MARK"):
    ds = data_dir / dataset
    ds.mkdir(exist_ok=True)
    (ds / "a.md").write_text(body)


def _seed_brief(db_mod, dataset, insights):
    payload = {"summary_headline": "stub", "insights": insights, "_schema_version": 1}
    return db_mod.save_brief(
        dataset=dataset, week_label="Week of stub", payload=payload, schema_version=1
    )


def _start_prd(db_mod, brief_id, insight_index=0, title="t"):
    return db_mod.start_prd(
        brief_id=brief_id, insight_index=insight_index, title=title,
        template_version=1, variant="v2",
    )


def _seed_trail(facade, ent, *, theme_label, insight_title, theme_id_prop=None,
                signal_specs=None, with_hypothesis=True, support_n=None):
    """Seed a theme + signals + (optionally) a synthesis-style hypothesis with
    ADDRESSES edge to the theme and SUPPORTS edges from the first `support_n`
    signals. Returns (theme, hypothesis_or_None, signals).

    signal_specs: list of (source_type, kind, content, prov). All wired to the
    theme via a REQUESTS edge (mirrors synthesis evidence wiring); `prov` is the
    signal's provenance dict (e.g. {"source": "zendesk"})."""
    from app.graph.types import Entity, Relationship, Signal

    signal_specs = signal_specs or []
    theme = Entity(enterprise_id=ent, type="theme", canonical_label=theme_label)
    facade.create_entity(ent, theme)
    theme_id = theme_id_prop or theme.id

    now = datetime.now(timezone.utc)
    sigs = []
    for st, kind, content, prov in signal_specs:
        sig = Signal(enterprise_id=ent, source_type=st, kind=kind, content=content,
                     provenance=prov, valid_at=now - timedelta(days=1))
        facade.write_signal(ent, sig)
        facade.write_relationship(ent, Relationship(
            enterprise_id=ent, type="REQUESTS", source_kind="signal",
            source_id=sig.id, target_kind="entity", target_id=theme.id))
        sigs.append(sig)

    hyp = None
    if with_hypothesis:
        hyp = Entity(
            enterprise_id=ent, type="hypothesis", canonical_label=insight_title[:200],
            properties={"claim": "ship the fix", "tag": "something_broken",
                        "theme_id": theme_id},
        )
        facade.create_entity(ent, hyp)
        facade.write_relationship(ent, Relationship(
            enterprise_id=ent, type="ADDRESSES", source_kind="entity",
            source_id=hyp.id, target_kind="entity", target_id=theme.id))
        n = support_n if support_n is not None else len(sigs)
        for sig in sigs[:n]:
            facade.write_relationship(ent, Relationship(
                enterprise_id=ent, type="SUPPORTS", source_kind="signal",
                source_id=sig.id, target_kind="entity", target_id=hyp.id))
    return theme, hyp, sigs


def _llm_result(output, model="claude-sonnet-4-6", prompt_version="prd-author-v1"):
    return LLMResult(
        output=output, model=model, prompt_version=prompt_version,
        input_tokens=10, output_tokens=5, cache_read_input_tokens=0,
        cache_creation_input_tokens=0, cost_usd=0.001, latency_ms=5,
        stop_reason="end_turn",
    )


# Generation is now TWO concurrent prd-author calls. _TWO_PART is kept as a
# generic output for tests that only assert on the call INPUT / decision-log
# (both part-calls receive the same input shape, so either's output is fine).
_TWO_PART = (
    "# Surface — Ship the thing\n\n"
    "# Part A — Product Requirements Document (human-readable)\n"
    "## 1. Problem & evidence\nUsers can't X.\n"
    "\n---\n"
    "# Part B — Implementation Spec (LLM-readable / agent-executable)\n"
    "## B0. Available artifacts\nWHEN x THE SYSTEM SHALL y.\n"
)

_PART_A = (
    "# Surface — Ship the thing\n\n"
    "# Part A — Product Requirements Document (human-readable)\n"
    "## 1. Problem & evidence\nUsers can't X.\n"
)
_PART_B = (
    "# Part B — Implementation Spec (LLM-readable / agent-executable)\n"
    "## B0. Available artifacts\nWHEN x THE SYSTEM SHALL y.\n"
)


def _two_call(**kwargs):
    """Part-aware llm_call stub: returns only the half this call asks for."""
    if kwargs.get("purpose") == "generate_prd_part_b":
        return _llm_result(_PART_B)
    return _llm_result(_PART_A)

COMPANY_ID = "co-prd-kg"
SLUG = "asurion"


@pytest.fixture
def facade(isolated_settings):
    from app.graph import GraphFacade

    return GraphFacade()


# ─────────────────── layer 1: insight_evidence_trail ───────────────────


def test_trail_resolves_supports_and_theme_signals(facade):
    """insight → theme_id → hypothesis → SUPPORTS signals, plus theme
    convergence signals; each carries content/source_type/provenance/confidence."""
    from app.graph.retrieval import insight_evidence_trail

    theme, hyp, sigs = _seed_trail(
        facade, COMPANY_ID, theme_label="Checkout broken", insight_title="Fix checkout",
        signal_specs=[
            ("customer_voice", "complaint", "users abandon at pay step",
             {"source": "zendesk"}),
            ("revenue", "metric", "$120k at risk", {}),
            ("analytics", "funnel", "30% drop", {}),
        ],
        support_n=2,  # only first 2 signals are SUPPORTS; 3rd is theme-only
    )
    brief = {"insights": [{"title": "Fix checkout", "theme_id": theme.id}]}

    trail = insight_evidence_trail(facade, COMPANY_ID, brief, 0)

    assert trail["empty"] is False
    assert trail["theme_id"] == theme.id
    assert trail["hypothesis"]["entity_id"] == hyp.id
    contents = {s["content"] for s in trail["signals"]}
    assert "users abandon at pay step" in contents
    assert "$120k at risk" in contents
    assert "30% drop" in contents  # folded in via the theme edge
    # SUPPORTS-tagged signals sort ahead of theme-only ones.
    edges = [s["edge"] for s in trail["signals"]]
    assert edges[0] == "SUPPORTS"
    assert "theme" in edges
    # Provenance + source_type travel with each signal for citation.
    cv = next(s for s in trail["signals"] if s["content"].startswith("users abandon"))
    assert cv["source_type"] == "customer_voice"
    assert cv["provenance"]["source"] == "zendesk"


def test_trail_kg_refs_cover_signals_hypothesis_theme(facade):
    from app.graph.retrieval import insight_evidence_trail

    theme, hyp, sigs = _seed_trail(
        facade, COMPANY_ID, theme_label="T", insight_title="I",
        signal_specs=[("revenue", "metric", "x", {})])
    brief = {"insights": [{"title": "I", "theme_id": theme.id}]}

    trail = insight_evidence_trail(facade, COMPANY_ID, brief, 0)

    assert sigs[0].id in trail["kg_refs"]
    assert hyp.id in trail["kg_refs"]
    assert theme.id in trail["kg_refs"]


def test_trail_empty_when_no_theme_id(facade):
    """An insight with no theme_id AND no title-matching hypothesis has no KG
    linkage → empty trail. (With a title that matches a hypothesis, the shared
    resolver title-falls-back; covered by the shared-resolver tests below.)"""
    from app.graph.retrieval import insight_evidence_trail

    brief = {"insights": [{"title": "no linkage"}]}
    trail = insight_evidence_trail(facade, COMPANY_ID, brief, 0)
    assert trail["empty"] is True
    assert trail["signals"] == []
    assert trail["hypothesis"] is None


def test_trail_empty_when_theme_has_no_signals_and_no_hypothesis(facade):
    """A theme_id that resolves to nothing in the KG → empty trail (fallback)."""
    from app.graph.retrieval import insight_evidence_trail

    brief = {"insights": [{"title": "ghost", "theme_id": "theme-does-not-exist"}]}
    trail = insight_evidence_trail(facade, COMPANY_ID, brief, 0)
    assert trail["empty"] is True


def test_trail_theme_only_when_hypothesis_missing(facade):
    """No synthesis hypothesis yet, but the theme has signals: the trail still
    surfaces the theme convergence signals (insight→hypothesis fuzziness)."""
    from app.graph.retrieval import insight_evidence_trail

    theme, hyp, sigs = _seed_trail(
        facade, COMPANY_ID, theme_label="T", insight_title="I",
        signal_specs=[("revenue", "metric", "theme-evidence", {})],
        with_hypothesis=False)
    brief = {"insights": [{"title": "I", "theme_id": theme.id}]}

    trail = insight_evidence_trail(facade, COMPANY_ID, brief, 0)
    assert trail["empty"] is False
    assert trail["hypothesis"] is None
    assert any(s["content"] == "theme-evidence" for s in trail["signals"])
    assert all(s["edge"] == "theme" for s in trail["signals"])


def test_trail_skips_superseded_signals(facade):
    from app.graph.retrieval import insight_evidence_trail
    from app.graph.types import Entity, Relationship, Signal

    theme, hyp, sigs = _seed_trail(
        facade, COMPANY_ID, theme_label="T", insight_title="I",
        signal_specs=[("revenue", "metric", "live", {})])
    # A superseded signal wired to the hypothesis must not surface.
    dead = Signal(enterprise_id=COMPANY_ID, source_type="revenue", kind="metric",
                  content="stale", properties={"superseded_by": "newer"})
    facade.write_signal(COMPANY_ID, dead)
    facade.write_relationship(COMPANY_ID, Relationship(
        enterprise_id=COMPANY_ID, type="SUPPORTS", source_kind="signal",
        source_id=dead.id, target_kind="entity", target_id=hyp.id))
    brief = {"insights": [{"title": "I", "theme_id": theme.id}]}

    trail = insight_evidence_trail(facade, COMPANY_ID, brief, 0)
    contents = {s["content"] for s in trail["signals"]}
    assert "live" in contents
    assert "stale" not in contents


def test_render_evidence_trail_section_cites_sources(facade):
    from app.graph.retrieval import insight_evidence_trail, render_evidence_trail_section

    theme, hyp, sigs = _seed_trail(
        facade, COMPANY_ID, theme_label="T", insight_title="I",
        signal_specs=[("customer_voice", "complaint", "the pain", {"source": "gong"})])
    brief = {"insights": [{"title": "I", "theme_id": theme.id}]}
    trail = insight_evidence_trail(facade, COMPANY_ID, brief, 0)

    md = render_evidence_trail_section(trail)
    assert "KNOWLEDGE GRAPH EVIDENCE" in md
    assert "customer_voice" in md
    assert "the pain" in md
    assert "gong" in md


def test_render_empty_trail_is_blank(facade):
    from app.graph.retrieval import render_evidence_trail_section

    assert render_evidence_trail_section({"empty": True}) == ""
    assert render_evidence_trail_section({}) == ""


# ─────────────────── layer 2: prd_runner grounding ───────────────────


def _setup_kg_prd(isolated_settings, facade, *, support_n=2):
    db_mod = isolated_settings["db"]
    _seed_company(isolated_settings["supabase"], company_id=COMPANY_ID, slug=SLUG)
    _seed_corpus(isolated_settings["data_dir"], SLUG)
    theme, hyp, sigs = _seed_trail(
        facade, COMPANY_ID, theme_label="Checkout broken", insight_title="Fix checkout",
        signal_specs=[
            ("customer_voice", "complaint", "KG_SIGNAL_MARK abandon at pay",
             {"source": "zendesk"}),
            ("revenue", "metric", "$120k at risk", {}),
        ],
        support_n=support_n)
    brief_id = _seed_brief(
        db_mod, SLUG, insights=[{"title": "Fix checkout", "theme_id": theme.id}])
    prd_id = _start_prd(db_mod, brief_id)
    return db_mod, brief_id, prd_id, theme, hyp, sigs


def test_prd_grounds_on_kg_trail_not_corpus(isolated_settings, facade, monkeypatch):
    """The human PRD's input carries the KG trail's signals (cited) and NOT the
    corpus dump (KG-first grounding). It binds prd-author — and the human-PRD
    flow makes exactly one call (no implementation-spec)."""
    db_mod, brief_id, prd_id, theme, hyp, sigs = _setup_kg_prd(isolated_settings, facade)

    calls: list[dict] = []
    monkeypatch.setattr(prd_runner, "llm_call",
                        lambda **kw: (calls.append(kw), _llm_result(_PART_A))[1])
    prd_runner._run_sync(prd_id, brief_id, 0)

    assert len(calls) == 1
    inp = calls[0]["input"]
    assert "KNOWLEDGE GRAPH EVIDENCE" in inp
    assert "KG_SIGNAL_MARK abandon at pay" in inp
    assert "customer_voice" in inp           # source_type cited
    assert "zendesk" in inp                   # provenance cited
    assert "CORPUS_FALLBACK_MARK" not in inp  # corpus is NOT dumped
    # The human PRD binds prd-author, on the prd agent; no machine spec.
    assert calls[0]["purpose"] == "generate_prd_part_a"
    assert calls[0]["skill"] == "prd-author"
    assert calls[0]["agent"] == "prd"


def test_prd_decision_log_carries_kg_refs(isolated_settings, facade, monkeypatch):
    """The generate_prd decision-log row pins the signal/hypothesis/theme ids."""
    db_mod, brief_id, prd_id, theme, hyp, sigs = _setup_kg_prd(isolated_settings, facade)

    monkeypatch.setattr(prd_runner, "llm_call", lambda **kw: _llm_result(_TWO_PART))
    prd_runner._run_sync(prd_id, brief_id, 0)

    sup = isolated_settings["supabase"]
    rows = sup.table("agent_decision_log").select("*").execute().data
    gen = [r for r in rows if r["decision_type"] == "generate_prd"]
    assert len(gen) == 1
    refs = gen[0]["kg_refs"]
    assert theme.id in refs
    assert hyp.id in refs
    assert any(s.id in refs for s in sigs)
    assert gen[0]["factors"]["grounding"] == "kg"
    assert gen[0]["factors"]["kg_signals"] >= 1


def test_prd_decision_log_uses_company_uuid_not_slug(isolated_settings, facade, monkeypatch):
    """Regression: the decision log is keyed by the resolved company UUID, not the
    dataset slug — agent_decision_log.enterprise_id is a uuid column, so logging the
    slug ('asurion') raised 22P02 and failed the PRD after generation succeeded."""
    db_mod, brief_id, prd_id, theme, hyp, sigs = _setup_kg_prd(isolated_settings, facade)
    monkeypatch.setattr(prd_runner, "llm_call", lambda **kw: _llm_result(_TWO_PART))
    prd_runner._run_sync(prd_id, brief_id, 0)

    sup = isolated_settings["supabase"]
    gen = [r for r in sup.table("agent_decision_log").select("*").execute().data
           if r["decision_type"] == "generate_prd"]
    assert len(gen) == 1
    assert gen[0]["enterprise_id"] == COMPANY_ID   # the uuid …
    assert gen[0]["enterprise_id"] != SLUG          # … not the slug


def test_prd_no_company_for_slug_skips_decision_log(isolated_settings, facade, monkeypatch):
    """A dataset that owns no company resolves to None → the decision log is skipped
    (no uuid to key on) rather than crashing the PRD."""
    db_mod, brief_id, prd_id, theme, hyp, sigs = _setup_kg_prd(isolated_settings, facade)
    monkeypatch.setattr(prd_runner, "company_id_for_slug", lambda _slug: None)
    monkeypatch.setattr(prd_runner, "llm_call", lambda **kw: _llm_result(_TWO_PART))
    prd_runner._run_sync(prd_id, brief_id, 0)  # must not raise

    sup = isolated_settings["supabase"]
    gen = [r for r in sup.table("agent_decision_log").select("*").execute().data
           if r["decision_type"] == "generate_prd"]
    assert gen == []


def test_prd_empty_trail_falls_back_to_corpus(isolated_settings, facade, monkeypatch):
    """An insight with no KG backing → corpus grounding (PRD never hard-fails),
    and the decision log records the corpus path with empty kg_refs."""
    db_mod = isolated_settings["db"]
    _seed_company(isolated_settings["supabase"], company_id=COMPANY_ID, slug=SLUG)
    _seed_corpus(isolated_settings["data_dir"], SLUG)
    # Insight with a theme_id that resolves to nothing → empty trail.
    brief_id = _seed_brief(
        db_mod, SLUG, insights=[{"title": "orphan", "theme_id": "no-such-theme"}])
    prd_id = _start_prd(db_mod, brief_id)

    captured: dict = {}
    monkeypatch.setattr(prd_runner, "llm_call",
                        lambda **kw: (captured.update(kw), _llm_result(_TWO_PART))[1])
    prd_runner._run_sync(prd_id, brief_id, 0)

    assert "CORPUS_FALLBACK_MARK" in captured["input"]
    assert "KNOWLEDGE GRAPH EVIDENCE" not in captured["input"]
    sup = isolated_settings["supabase"]
    gen = [r for r in sup.table("agent_decision_log").select("*").execute().data
           if r["decision_type"] == "generate_prd"]
    assert gen[0]["factors"]["grounding"] == "corpus"
    assert gen[0]["kg_refs"] == []


def test_prd_no_company_falls_back_to_corpus(isolated_settings, monkeypatch):
    """No companies row for the slug → no tenant → corpus fallback."""
    db_mod = isolated_settings["db"]
    _seed_corpus(isolated_settings["data_dir"], SLUG)
    brief_id = _seed_brief(
        db_mod, SLUG, insights=[{"title": "x", "theme_id": "t1"}])
    prd_id = _start_prd(db_mod, brief_id)

    captured: dict = {}
    monkeypatch.setattr(prd_runner, "llm_call",
                        lambda **kw: (captured.update(kw), _llm_result(_TWO_PART))[1])
    prd_runner._run_sync(prd_id, brief_id, 0)
    assert "CORPUS_FALLBACK_MARK" in captured["input"]


def test_prd_human_storage_unchanged_on_kg(isolated_settings, facade, monkeypatch):
    """Regrounding doesn't touch the human-PRD storage contract: the human PRD →
    payload_md, llm_part stays empty (the spec is on demand), status ready."""
    db_mod, brief_id, prd_id, theme, hyp, sigs = _setup_kg_prd(isolated_settings, facade)

    monkeypatch.setattr(prd_runner, "llm_call", lambda **kw: _llm_result(_PART_A))
    prd_runner._run_sync(prd_id, brief_id, 0)

    row = db_mod.get_prd(prd_id)
    assert row["status"] == "ready"
    assert "Part A — Product Requirements Document" in row["payload_md"]
    assert (row["llm_part"] or "") == ""


def test_prd_kg_read_failure_falls_back_to_corpus(isolated_settings, facade, monkeypatch):
    """A KG read that explodes must not break the PRD — it degrades to corpus."""
    db_mod, brief_id, prd_id, theme, hyp, sigs = _setup_kg_prd(isolated_settings, facade)

    def _boom(*a, **k):
        raise RuntimeError("KG down")

    monkeypatch.setattr(prd_runner, "insight_evidence_trail", _boom)
    captured: dict = {}
    monkeypatch.setattr(prd_runner, "llm_call",
                        lambda **kw: (captured.update(kw), _llm_result(_TWO_PART))[1])
    prd_runner._run_sync(prd_id, brief_id, 0)

    assert "CORPUS_FALLBACK_MARK" in captured["input"]
    assert db_mod.get_prd(prd_id)["status"] == "ready"
