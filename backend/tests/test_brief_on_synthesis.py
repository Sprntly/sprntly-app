"""Tests for the weekly brief on KG synthesis — the only engine.

Covers the synthesis write path (/generate, /regenerate), the seed-if-empty
trigger, the scheduler synthesis cycle with per-company error isolation, and
the unchanged UI read path (/current, /status, /{id}). The legacy brief/KG
engine has been retired, so these assert synthesis runs UNCONDITIONALLY (no
engine flag). LLM/gateway/run_synthesis are all mocked — no test hits Anthropic
or Supabase.
"""
from __future__ import annotations

import asyncio
from unittest.mock import patch

import pytest

import app.main as main_mod
import app.routes.brief as brief_routes
import app.scheduler as scheduler_mod
import app.synthesis_brief as sb


# ── helpers ───────────────────────────────────────────────────────────────


@pytest.fixture
def app_client(tenant_client):
    """Tenant-authed client for the route-driven tests here. After the
    tenant-isolation fix the brief/datasets routes require `require_company`,
    so a legacy-cookie client would 403. The route tests all operate on the
    "acme" dataset slug, so we seed a company whose slug == "acme" (company id
    "co-1", matching the direct `_seed_company(... company_id="co-1", slug="acme")`
    calls those tests already make) and authenticate as a member of it."""
    return tenant_client.make(slug="acme", user_id="user-acme", company_id="co-1").client

def _seed_company(db, *, company_id: str, slug: str) -> None:
    """Insert a companies row so slug↔company_id resolution works."""
    existing = db.table("companies").select("id").eq("id", company_id).execute().data
    if not existing:
        db.table("companies").insert(
            {"id": company_id, "slug": slug, "display_name": slug.title()}
        ).execute()


def _fake_synthesis_payload(dataset_slug: str) -> dict:
    return {
        "week_label": "Week of June 8, 2026",
        "summary_headline": "synthesis headline",
        "company": dataset_slug,
        "insights": [{"title": "X", "tag": "something_broken", "theme_id": "t1"}],
        "_generated_by": "synthesis_agent",
        "_schema_version": 1,
    }


# ── /generate — synthesis engine path ───────────────────────────────────────

def test_generate_synthesis_path_runs_run_synthesis(app_client, isolated_settings, monkeypatch):
    db = isolated_settings["supabase"]
    _seed_company(db, company_id="co-1", slug="acme")

    def _fake_gen(slug, **_kw):
        # mirror run_synthesis: persist into briefs, return payload
        payload = _fake_synthesis_payload("acme")
        isolated_settings["db"].save_brief("acme", payload["week_label"], payload,
                                           schema_version=1)
        return payload

    with patch.object(brief_routes, "generate_brief_for", side_effect=_fake_gen) as gen, \
         patch.object(brief_routes, "_notify_brief_ready") as ping:
        r = app_client.post("/v1/brief/generate?dataset=acme")

    assert r.status_code == 200, r.text
    # User-triggered: delivery suppressed in synthesis, short ping sent instead.
    gen.assert_called_once_with("acme", deliver=False)
    ping.assert_called_once()
    body = r.json()
    assert body["summary_headline"] == "synthesis headline"
    # response preserves the {brief_id, **payload} contract
    assert isinstance(body["brief_id"], int)


def test_generate_synthesis_then_current_reads_back(app_client, isolated_settings, monkeypatch):
    db = isolated_settings["supabase"]
    _seed_company(db, company_id="co-1", slug="acme")

    def _fake_gen(slug, **_kw):
        payload = _fake_synthesis_payload("acme")
        isolated_settings["db"].save_brief("acme", payload["week_label"], payload, 1)
        return payload

    with patch.object(brief_routes, "generate_brief_for", side_effect=_fake_gen), \
         patch.object(brief_routes, "_notify_brief_ready"):
        app_client.post("/v1/brief/generate?dataset=acme")

    # UI read path unchanged: /current returns the saved synthesis brief
    r = app_client.get("/v1/brief/current?dataset=acme")
    assert r.status_code == 200
    assert r.json()["_generated_by"] == "synthesis_agent"


def test_generate_unowned_dataset_returns_404(app_client, monkeypatch):
    # After the tenant-isolation fix, a dataset slug that isn't the caller's
    # company is rejected by the ownership gate (404) before generate_brief_for
    # is ever reached — the gate supersedes the old unknown-company 409.
    with patch.object(brief_routes, "generate_brief_for") as gen:
        r = app_client.post("/v1/brief/generate?dataset=ghost")
    assert r.status_code == 404
    gen.assert_not_called()


# ── /regenerate — synthesis engine path ─────────────────────────────────────

def test_regenerate_synthesis_path_starts_synthesis_bg(app_client, isolated_settings, monkeypatch):
    _seed_company(isolated_settings["supabase"], company_id="co-1", slug="acme")
    seen: list[str] = []

    async def _fake_bg(dataset):
        seen.append(dataset)

    # /regenerate always routes to the synthesis bg runner.
    with patch.object(brief_routes, "_synthesis_generate_bg", side_effect=_fake_bg):
        r = app_client.post("/v1/brief/regenerate?dataset=acme")

    assert r.status_code == 200
    assert r.json() == {"started": True, "dataset": "acme"}


def test_synthesis_bg_runner_invokes_generate_brief_for(isolated_settings, monkeypatch):
    with patch.object(brief_routes, "generate_brief_for") as gen, \
         patch.object(brief_routes, "_notify_brief_ready"):
        asyncio.run(brief_routes._synthesis_generate_bg("acme"))
    gen.assert_called_once_with("acme", deliver=False)


def test_synthesis_bg_runner_swallows_errors(isolated_settings, monkeypatch):
    # fire-and-forget: a failing synthesis must not raise out of the bg task
    with patch.object(brief_routes, "generate_brief_for", side_effect=RuntimeError("boom")):
        asyncio.run(brief_routes._synthesis_generate_bg("acme"))  # no raise


# ── seed-if-empty logic ─────────────────────────────────────────────────────

def test_seed_incremental_on_empty_kg_seeds_corpus_and_connectors(isolated_settings):
    from app.graph.facade import GraphFacade

    facade = GraphFacade()
    # KG empty → corpus seed runs AND connectors are pulled (was_empty path).
    with patch.object(facade, "active_signals", return_value=[]), \
         patch.object(sb, "_seed_from_corpus", return_value={"docs": 1}) as corpus, \
         patch.object(sb, "_seed_from_connectors", return_value={"providers": 0}) as conn:
        out = sb.seed_incremental(facade, "co-1", "acme")
    assert out["was_empty"] is True
    assert out["connectors"] is not None
    corpus.assert_called_once()
    conn.assert_called_once()


def test_seed_incremental_on_populated_kg_seeds_corpus_skips_connectors(isolated_settings):
    """Regression: on a POPULATED KG, the corpus seed still runs (so a newly
    added doc is extracted) but connectors are NOT re-pulled."""
    from app.graph.facade import GraphFacade
    from app.graph.types import Signal

    facade = GraphFacade()
    sig = Signal(enterprise_id="co-1", source_type="revenue", kind="x", content="y")
    with patch.object(facade, "active_signals", return_value=[sig]), \
         patch.object(sb, "_seed_from_corpus", return_value={"docs": 1}) as corpus, \
         patch.object(sb, "_seed_from_connectors") as conn:
        out = sb.seed_incremental(facade, "co-1", "acme")
    assert out["was_empty"] is False
    assert out["connectors"] is None
    corpus.assert_called_once()      # corpus ALWAYS seeded (incremental)
    conn.assert_not_called()         # connectors NOT re-pulled on populated KG


def test_seed_incremental_extracts_only_new_doc_on_populated_kg(isolated_settings):
    """The bug this PR fixes: a newly-added corpus doc on a populated KG is
    extracted, while a doc already recorded as a corpus_doc source is not."""
    from app.graph.facade import GraphFacade
    from app.graph.types import Signal, Source

    _seed_company(isolated_settings["supabase"], company_id="co-1", slug="acme")
    facade = GraphFacade()

    class _Doc:
        def __init__(self, name, text):
            self.name, self.text = name, text

    old_doc = _Doc("kept.md", "already ingested text")
    new_doc = _Doc("fresh.md", "brand new text")

    class _Corpus:
        docs = [old_doc, new_doc]

    # Record old_doc as already-ingested (matching the hash the seed computes).
    import hashlib
    old_sha = hashlib.sha256(f"co-1|{old_doc.text}".encode()).hexdigest()
    facade.create_source("co-1", Source(
        enterprise_id="co-1", source_type="corpus_doc", label=old_doc.name,
        config={"content_sha": old_sha, "doc": old_doc.name},
    ))

    # Populated KG so we're on the incremental (not first-time) path.
    sig = Signal(enterprise_id="co-1", source_type="revenue", kind="x", content="y")
    extracted: list[str] = []
    with patch.object(facade, "active_signals", return_value=[sig]), \
         patch.object(sb, "load_corpus", return_value=_Corpus()), \
         patch.object(sb, "extract_document",
                      side_effect=lambda *a, **k: extracted.append(k["doc_name"]) or
                      {"signals": 1, "themes": 0, "skipped": 0}):
        out = sb.seed_incremental(facade, "co-1", "acme")

    assert extracted == ["fresh.md"]          # only the NEW doc extracted
    assert out["corpus"]["docs"] == 1
    assert out["corpus"]["unchanged"] == 1    # old doc skipped via content_sha
    # The new doc is now recorded as a corpus_doc source for next time.
    labels = {s.label for s in facade.list_sources("co-1", source_type="corpus_doc")}
    assert labels == {"kept.md", "fresh.md"}


def test_seed_corpus_reextracts_when_content_changes(isolated_settings):
    """Same doc NAME, edited text → different hash → IS re-extracted; an
    unchanged doc (hash already in kg_source) is skipped."""
    from app.graph.facade import GraphFacade
    from app.graph.types import Source

    _seed_company(isolated_settings["supabase"], company_id="co-1", slug="acme")
    facade = GraphFacade()

    class _Doc:
        def __init__(self, name, text):
            self.name, self.text = name, text

    class _Corpus:
        docs = [_Doc("notes.md", "v2 edited text"),  # name reused, text changed
                _Doc("stable.md", "unchanged text")]

    import hashlib
    # Pre-record the OLD content of notes.md and the current content of stable.md.
    old_notes_sha = hashlib.sha256("co-1|v1 original text".encode()).hexdigest()
    stable_sha = hashlib.sha256("co-1|unchanged text".encode()).hexdigest()
    for sha, name in [(old_notes_sha, "notes.md"), (stable_sha, "stable.md")]:
        facade.create_source("co-1", Source(
            enterprise_id="co-1", source_type="corpus_doc", label=name,
            config={"content_sha": sha, "doc": name},
        ))

    extracted: list[str] = []
    with patch.object(sb, "load_corpus", return_value=_Corpus()), \
         patch.object(sb, "extract_document",
                      side_effect=lambda *a, **k: extracted.append(k["doc_name"]) or
                      {"signals": 1, "themes": 0, "skipped": 0}):
        out = sb._seed_from_corpus(facade, "co-1", "acme")

    assert extracted == ["notes.md"]       # edited doc re-extracted
    assert out["unchanged"] == 1           # stable.md skipped
    assert out["docs"] == 1


def test_generate_brief_for_seeds_then_runs_synthesis(isolated_settings):
    _seed_company(isolated_settings["supabase"], company_id="co-1", slug="acme")
    with patch.object(sb, "seed_incremental", return_value={"corpus": {}}) as seed, \
         patch.object(sb, "run_synthesis", return_value={"summary_headline": "ok"}) as run:
        out = sb.generate_brief_for("acme")
    assert out["summary_headline"] == "ok"
    seed.assert_called_once()
    # run_synthesis called with the resolved company_id + slug
    _, kwargs = run.call_args[0], run.call_args[1]
    assert run.call_args[0][1] == "co-1"
    assert kwargs["dataset_slug"] == "acme"


# ── refresh-gating: skip synthesis when KG unchanged ────────────────────────

def test_generate_brief_for_no_prior_brief_always_synthesizes(isolated_settings):
    """First generation (no current brief) ALWAYS runs synthesis, regardless of
    whether the KG has new signals."""
    _seed_company(isolated_settings["supabase"], company_id="co-1", slug="acme")
    with patch.object(sb, "get_current_brief", return_value=None), \
         patch.object(sb, "seed_incremental", return_value={"corpus": {}}), \
         patch.object(sb, "run_synthesis",
                      return_value={"summary_headline": "fresh"}) as run, \
         patch("app.graph.facade.GraphFacade.has_signals_since") as has:
        out = sb.generate_brief_for("acme")
    assert out["summary_headline"] == "fresh"
    run.assert_called_once()
    has.assert_not_called()  # no prior brief → no gating check needed


def test_generate_brief_for_unchanged_kg_skips_synthesis(isolated_settings):
    """Prior brief exists + NO new signals since its generated_at → synthesis is
    NOT called; the existing brief is returned unchanged."""
    _seed_company(isolated_settings["supabase"], company_id="co-1", slug="acme")
    prior = {"id": 42, "generated_at": "2026-06-10T00:00:00+00:00",
             "summary_headline": "existing"}
    with patch.object(sb, "get_current_brief", return_value=prior), \
         patch.object(sb, "seed_incremental", return_value={"corpus": {}}), \
         patch.object(sb, "run_synthesis") as run, \
         patch("app.graph.facade.GraphFacade.has_signals_since",
               return_value=False) as has:
        out = sb.generate_brief_for("acme")
    run.assert_not_called()                 # synthesis skipped
    assert out is prior                     # existing brief returned unchanged
    # gating checked against the prior brief's generated_at
    has.assert_called_once_with("co-1", "2026-06-10T00:00:00+00:00")


def test_generate_brief_for_new_signals_runs_synthesis(isolated_settings):
    """Prior brief exists + NEW signals since its generated_at → synthesis IS
    called (seed added data, or another path wrote signals)."""
    _seed_company(isolated_settings["supabase"], company_id="co-1", slug="acme")
    prior = {"id": 42, "generated_at": "2026-06-10T00:00:00+00:00",
             "summary_headline": "stale"}
    with patch.object(sb, "get_current_brief", return_value=prior), \
         patch.object(sb, "seed_incremental", return_value={"corpus": {"docs": 1}}), \
         patch.object(sb, "run_synthesis",
                      return_value={"summary_headline": "regenerated"}) as run, \
         patch("app.graph.facade.GraphFacade.has_signals_since",
               return_value=True) as has:
        out = sb.generate_brief_for("acme")
    run.assert_called_once()
    assert out["summary_headline"] == "regenerated"
    has.assert_called_once_with("co-1", "2026-06-10T00:00:00+00:00")


def test_has_signals_since_is_tenant_scoped(isolated_settings):
    """Facade has_signals_since: True iff a signal's created_at is strictly
    after the ts, and only for the queried enterprise."""
    from app.graph.facade import GraphFacade

    _seed_company(isolated_settings["supabase"], company_id="co-1", slug="acme")
    _seed_company(isolated_settings["supabase"], company_id="co-2", slug="globex")
    db = isolated_settings["supabase"]

    # Insert kg_signal rows directly with explicit created_at values so the
    # comparison is deterministic (bypasses the DB default).
    def _sig(sid, ent, created_at):
        db.table("kg_signal").insert({
            "id": sid, "enterprise_id": ent, "source_type": "revenue",
            "kind": "x", "content": "y",
            "valid_at": "2026-06-01T00:00:00+00:00",
            "transaction_at": "2026-06-01T00:00:00+00:00",
            "created_at": created_at,
        }).execute()

    cutoff = "2026-06-10T00:00:00+00:00"
    facade = GraphFacade()

    # Only a pre-cutoff signal → False.
    _sig("s-old", "co-1", "2026-06-09T00:00:00+00:00")
    assert facade.has_signals_since("co-1", cutoff) is False

    # A post-cutoff signal → True.
    _sig("s-new", "co-1", "2026-06-11T00:00:00+00:00")
    assert facade.has_signals_since("co-1", cutoff) is True

    # Tenant-scoped: co-2 has ONLY a post-cutoff signal of its own, but co-1's
    # result above never depended on it; and co-2 with only a pre-cutoff signal
    # of its own stays False even though co-1 has a newer one.
    _sig("s-other", "co-2", "2026-06-09T00:00:00+00:00")  # co-2: pre-cutoff only
    assert facade.has_signals_since("co-2", cutoff) is False  # co-1's s-new invisible


def test_has_signals_since_survives_server_page_caps(isolated_settings, monkeypatch):
    """Regression (prod bug, Lab X 12k+ signals): PostgREST caps an unlimited
    select at ~1000 rows, and the old full-table scan could get a page holding
    only OLD signals — the gate then reported "unchanged" forever and the brief
    never regenerated. The facade must fetch newest-first with a limit, so the
    answer is correct no matter how many rows the tenant has."""
    from tests import _fake_supabase as fake
    from app.graph.facade import GraphFacade

    _seed_company(isolated_settings["supabase"], company_id="co-1", slug="acme")
    db = isolated_settings["supabase"]
    # 1200 pre-cutoff signals inserted FIRST (so an unordered capped page is
    # all-old), then one post-cutoff signal.
    rows = [{
        "id": f"s-{i}", "enterprise_id": "co-1", "source_type": "revenue",
        "kind": "x", "content": "y",
        "valid_at": "2026-06-01T00:00:00+00:00",
        "transaction_at": "2026-06-01T00:00:00+00:00",
        "created_at": "2026-06-09T00:00:00+00:00",
    } for i in range(1200)]
    rows.append({**rows[0], "id": "s-new",
                 "created_at": "2026-06-11T00:00:00+00:00"})
    db.table("kg_signal").insert(rows).execute()

    # Mimic the server-side page cap: an execute() with no explicit limit
    # returns at most 1000 rows (exactly PostgREST's behavior).
    orig_execute = fake._Query.execute

    def capped_execute(self):
        res = orig_execute(self)
        if getattr(self, "_limit", None) is None and isinstance(res.data, list):
            res.data = res.data[:1000]
        return res

    monkeypatch.setattr(fake._Query, "execute", capped_execute)

    assert GraphFacade().has_signals_since(
        "co-1", "2026-06-10T00:00:00+00:00") is True


def test_generate_brief_for_resolves_slug_to_company_id(isolated_settings):
    _seed_company(isolated_settings["supabase"], company_id="co-xyz", slug="globex")
    cid, slug = sb.resolve_company("globex")
    assert cid == "co-xyz" and slug == "globex"


def test_generate_brief_for_unknown_slug_raises(isolated_settings):
    with pytest.raises(ValueError):
        sb.resolve_company("nope-not-here")


def test_seed_from_corpus_is_bounded(isolated_settings, monkeypatch):
    """Seeding caps NEW corpus extractions at MAX_SEED_DOCS so it can't hang
    the request."""
    from app.graph.facade import GraphFacade

    _seed_company(isolated_settings["supabase"], company_id="co-1", slug="acme")

    class _Doc:
        def __init__(self, n):
            # distinct text per doc → distinct content hash → all "new"
            self.name, self.text = f"d{n}", f"text-{n}"

    class _Corpus:
        docs = [_Doc(i) for i in range(sb.MAX_SEED_DOCS + 10)]

    facade = GraphFacade()
    calls: list[str] = []
    with patch.object(sb, "load_corpus", return_value=_Corpus()), \
         patch.object(sb, "extract_document",
                      side_effect=lambda *a, **k: calls.append(k["doc_name"]) or
                      {"signals": 1, "themes": 0, "skipped": 0}):
        sb._seed_from_corpus(facade, "co-1", "acme")
    assert len(calls) == sb.MAX_SEED_DOCS


def test_seed_from_corpus_isolates_bad_doc(isolated_settings):
    """One doc that raises during extraction must not abort the whole seed,
    and a failed doc is NOT recorded as ingested (so it retries next run)."""
    from app.graph.facade import GraphFacade

    _seed_company(isolated_settings["supabase"], company_id="co-1", slug="acme")

    class _Doc:
        def __init__(self, n):
            self.name, self.text = f"d{n}", f"text-{n}"

    class _Corpus:
        docs = [_Doc(0), _Doc(1)]

    def _extract(*a, **k):
        if k["doc_name"] == "d0":
            raise RuntimeError("bad doc")
        return {"signals": 2, "themes": 1, "skipped": 0}

    facade = GraphFacade()
    with patch.object(sb, "load_corpus", return_value=_Corpus()), \
         patch.object(sb, "extract_document", side_effect=_extract):
        out = sb._seed_from_corpus(facade, "co-1", "acme")
    assert out["docs"] == 1 and out["signals"] == 2  # only the good doc counted
    # Only the GOOD doc was recorded as ingested; the bad one retries next run.
    labels = {s.label for s in facade.list_sources("co-1", source_type="corpus_doc")}
    assert labels == {"d1"}


def test_list_sources_is_tenant_scoped_and_filters_by_type(isolated_settings):
    """Facade list_sources returns only the enterprise's sources and filters
    by source_type."""
    from app.graph.facade import GraphFacade
    from app.graph.types import Source

    _seed_company(isolated_settings["supabase"], company_id="co-1", slug="acme")
    _seed_company(isolated_settings["supabase"], company_id="co-2", slug="globex")
    facade = GraphFacade()

    facade.create_source("co-1", Source(
        enterprise_id="co-1", source_type="corpus_doc", label="a",
        config={"content_sha": "h1"}))
    facade.create_source("co-1", Source(
        enterprise_id="co-1", source_type="connector", label="slack"))
    facade.create_source("co-2", Source(
        enterprise_id="co-2", source_type="corpus_doc", label="other-tenant",
        config={"content_sha": "h2"}))

    # tenant-scoped: co-1 never sees co-2's row
    all_co1 = facade.list_sources("co-1")
    assert {s.label for s in all_co1} == {"a", "slack"}
    # filtered by source_type
    corpus_co1 = facade.list_sources("co-1", source_type="corpus_doc")
    assert {s.label for s in corpus_co1} == {"a"}
    assert corpus_co1[0].config == {"content_sha": "h1"}


# ── scheduler synthesis cycle ────────────────────────────────────────────────

def test_scheduler_cycle_iterates_companies(isolated_settings, monkeypatch):
    db = isolated_settings["supabase"]
    _seed_company(db, company_id="co-1", slug="acme")
    _seed_company(db, company_id="co-2", slug="globex")

    seen: list[str] = []
    with patch("app.synthesis_brief.generate_brief_for",
               side_effect=lambda slug: seen.append(slug)):
        asyncio.run(scheduler_mod._run_scheduled_cycle())
    assert sorted(seen) == ["acme", "globex"]


def test_scheduler_synthesis_isolates_per_company_failure(isolated_settings, monkeypatch):
    db = isolated_settings["supabase"]
    _seed_company(db, company_id="co-1", slug="acme")
    _seed_company(db, company_id="co-2", slug="globex")
    _seed_company(db, company_id="co-3", slug="initech")

    seen: list[str] = []

    def _gen(slug):
        if slug == "globex":
            raise RuntimeError("synthesis blew up for globex")
        seen.append(slug)

    with patch("app.synthesis_brief.generate_brief_for", side_effect=_gen):
        asyncio.run(scheduler_mod._run_synthesis_for_all_companies())  # no raise
    # the failing company is skipped; the others still ran
    assert sorted(seen) == ["acme", "initech"]


def test_scheduler_synthesis_no_companies_is_noop(isolated_settings, monkeypatch):
    with patch("app.synthesis_brief.generate_brief_for") as gen:
        asyncio.run(scheduler_mod._run_synthesis_for_all_companies())
    gen.assert_not_called()


# ── UI read path unchanged ───────────────────────────────────────────────────

def test_read_endpoints_unchanged_under_synthesis(app_client, isolated_settings, monkeypatch):
    db = isolated_settings["db"]
    brief_id = db.save_brief("acme", "Week 1", {"insights": [], "_generated_by": "synthesis_agent"},
                             schema_version=1)

    # /current
    r = app_client.get("/v1/brief/current?dataset=acme")
    assert r.status_code == 200 and r.json()["id"] == brief_id
    # /{id}
    r = app_client.get(f"/v1/brief/{brief_id}")
    assert r.status_code == 200 and r.json()["id"] == brief_id
    # /status — ready once a brief exists
    r = app_client.get("/v1/brief/status?dataset=acme")
    assert r.status_code == 200 and r.json()["status"] == "ready"


# ── startup brief generation (main.py) ──────────────────────────────────────

def test_startup_runs_synthesis_briefs(isolated_settings, monkeypatch):
    with patch.object(sb, "generate_all_synthesis_briefs") as synth:
        asyncio.run(main_mod._startup_generate_briefs())
    synth.assert_called_once()


def test_startup_brief_generation_swallows_errors(isolated_settings, monkeypatch):
    # startup must never break on brief generation
    with patch.object(sb, "generate_all_synthesis_briefs",
                      side_effect=RuntimeError("boom")):
        asyncio.run(main_mod._startup_generate_briefs())  # no raise


def test_generate_all_synthesis_briefs_iterates_and_warms(isolated_settings, monkeypatch):
    db = isolated_settings["supabase"]
    _seed_company(db, company_id="co-1", slug="acme")
    _seed_company(db, company_id="co-2", slug="globex")

    seen: list[str] = []
    warmed: list[str] = []
    with patch.object(sb, "generate_brief_for",
                      side_effect=lambda slug: seen.append(slug)), \
         patch("app.brief_runner.warm_synthesis_drilldowns",
               side_effect=lambda slug: warmed.append(slug)):
        sb.generate_all_synthesis_briefs()
    assert sorted(seen) == ["acme", "globex"]
    assert sorted(warmed) == ["acme", "globex"]


def test_generate_all_synthesis_briefs_isolates_failure(isolated_settings, monkeypatch):
    db = isolated_settings["supabase"]
    _seed_company(db, company_id="co-1", slug="acme")
    _seed_company(db, company_id="co-2", slug="globex")

    seen: list[str] = []

    def _gen(slug):
        if slug == "acme":
            raise RuntimeError("boom")
        seen.append(slug)

    with patch.object(sb, "generate_brief_for", side_effect=_gen), \
         patch("app.brief_runner.warm_synthesis_drilldowns"):
        sb.generate_all_synthesis_briefs()  # no raise
    assert seen == ["globex"]  # failing company skipped, rest still run


# ── dataset-create brief generation (datasets route) ─────────────────────────

def test_dataset_generate_routes_to_synthesis_bg(app_client, isolated_settings, monkeypatch):
    db = isolated_settings["db"]
    db.insert_dataset(slug="acme", display_name="Acme")

    seen: list[str] = []

    async def _fake_bg(dataset):
        seen.append(dataset)

    with patch.object(brief_routes, "_synthesis_generate_bg", side_effect=_fake_bg):
        r = app_client.post("/v1/datasets/acme/generate")

    assert r.status_code == 200, r.text
    assert r.json() == {"started": True, "dataset": "acme"}


# ── warm-drilldowns parity (BUG 3) ───────────────────────────────────────────

def test_synthesis_bg_warms_drilldowns_after_generate(isolated_settings, monkeypatch):
    """/regenerate's synthesis bg body warms drill-downs after the brief."""
    order: list[str] = []
    with patch.object(brief_routes, "generate_brief_for",
                      side_effect=lambda slug, **_kw: order.append("gen")), \
         patch.object(brief_routes, "warm_synthesis_drilldowns",
                      side_effect=lambda slug: order.append("warm")) as warm:
        asyncio.run(brief_routes._synthesis_generate_bg("acme"))
    warm.assert_called_once_with("acme")
    assert order == ["gen", "warm"]  # warm runs AFTER generation


def test_synthesis_bg_skips_warm_when_generate_fails(isolated_settings):
    """A failed brief generation must not warm (and must not raise)."""
    with patch.object(brief_routes, "generate_brief_for",
                      side_effect=RuntimeError("boom")), \
         patch.object(brief_routes, "warm_synthesis_drilldowns") as warm:
        asyncio.run(brief_routes._synthesis_generate_bg("acme"))  # no raise
    warm.assert_not_called()


def test_scheduler_synthesis_warms_drilldowns(isolated_settings, monkeypatch):
    db = isolated_settings["supabase"]
    _seed_company(db, company_id="co-1", slug="acme")

    warmed: list[str] = []
    with patch("app.synthesis_brief.generate_brief_for"), \
         patch("app.brief_runner.warm_synthesis_drilldowns",
               side_effect=lambda slug: warmed.append(slug)):
        asyncio.run(scheduler_mod._run_synthesis_for_all_companies())
    assert warmed == ["acme"]


def test_warm_synthesis_drilldowns_swallows_errors(isolated_settings):
    """A raise inside warming must not propagate (brief is already saved)."""
    from app import brief_runner

    with patch.object(brief_runner, "get_current_brief",
                      side_effect=RuntimeError("db down")):
        brief_runner.warm_synthesis_drilldowns("acme")  # no raise


def test_warm_synthesis_drilldowns_fans_out_when_brief_exists(isolated_settings):
    """When a brief exists, warming reads it back and fans out _warm_drilldowns."""
    from app import brief_runner

    fake_brief = {"id": 7, "insights": [{"title": "X"}]}
    with patch.object(brief_runner, "get_current_brief", return_value=fake_brief), \
         patch.object(brief_runner, "_warm_drilldowns") as warm:
        brief_runner.warm_synthesis_drilldowns("acme")
    warm.assert_called_once_with(fake_brief, dataset="acme")


def test_warm_synthesis_drilldowns_noop_without_brief(isolated_settings):
    from app import brief_runner

    with patch.object(brief_runner, "get_current_brief", return_value=None), \
         patch.object(brief_runner, "_warm_drilldowns") as warm:
        brief_runner.warm_synthesis_drilldowns("acme")
    warm.assert_not_called()
