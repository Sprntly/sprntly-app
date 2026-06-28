"""Tests for event-driven corpus→KG ingestion (corpus-seed-on-arrival).

Connector pullers (kickoff_sync) already cover GitHub/ClickUp/HubSpot/Fireflies.
This suite covers the *corpus* path — manual file uploads and the Drive/Slack/
Figma sync-to-corpus routes — which now eagerly extract into the KG via
`kickoff_corpus_seed` instead of waiting for the next brief's seed.

Covered:
  * kickoff fires a fire-and-forget daemon thread with (company_id, slug)
  * the background body runs the incremental _seed_from_corpus, error-isolated
  * overlapping kickoffs for a company share one lock (serialize, don't pile up)
  * the connectors helper resolves the corpus slug (dataset → company fallback)
  * the file-upload route triggers a seed after a successful upload
"""
from __future__ import annotations

import io

from app.kg_ingest import auto_sync


# ───────────────────────── kickoff_corpus_seed unit ─────────────────────────

def test_kickoff_corpus_seed_starts_daemon_thread(monkeypatch):
    started = {}

    class FakeThread:
        def __init__(self, target=None, args=(), name=None, daemon=None):
            started["args"] = args
            started["name"] = name
            started["daemon"] = daemon

        def start(self):
            started["started"] = True

    monkeypatch.setattr(auto_sync.threading, "Thread", FakeThread)
    assert auto_sync.kickoff_corpus_seed("co-7", "acme") is True
    assert started["started"] is True
    assert started["args"] == ("co-7", "acme")
    assert started["daemon"] is True


def test_kickoff_corpus_seed_never_raises_on_thread_failure(monkeypatch):
    def boom(*a, **k):
        raise RuntimeError("no threads today")

    monkeypatch.setattr(auto_sync.threading, "Thread", boom)
    # A thread-spawn failure must never bubble into the request flow.
    assert auto_sync.kickoff_corpus_seed("co-7", "acme") is False


def test_run_corpus_seed_calls_seed_from_corpus(monkeypatch):
    calls = {}
    monkeypatch.setattr(auto_sync, "GraphFacade", lambda *a, **k: "FACADE")

    import app.synthesis_brief as sb

    def fake_seed(facade, company_id, slug):
        calls["args"] = (facade, company_id, slug)
        return {"docs": 2, "signals": 5, "unchanged": 1}

    monkeypatch.setattr(sb, "_seed_from_corpus", fake_seed)
    auto_sync._run_corpus_seed("co-1", "acme")
    assert calls["args"] == ("FACADE", "co-1", "acme")


def test_run_corpus_seed_is_error_isolated(monkeypatch):
    monkeypatch.setattr(auto_sync, "GraphFacade", lambda *a, **k: object())

    import app.synthesis_brief as sb

    def boom(*a, **k):
        raise RuntimeError("extraction blew up")

    monkeypatch.setattr(sb, "_seed_from_corpus", boom)
    # Must swallow the failure — a bad seed can't crash the daemon thread.
    auto_sync._run_corpus_seed("co-1", "acme")


def test_corpus_seed_lock_is_per_company():
    a1 = auto_sync._corpus_seed_lock("company-a")
    a2 = auto_sync._corpus_seed_lock("company-a")
    b = auto_sync._corpus_seed_lock("company-b")
    assert a1 is a2          # same company → same lock (serializes its seeds)
    assert a1 is not b       # different company → independent lock


# ───────────────────── connectors corpus-seed helper ─────────────────────

def test_seed_corpus_after_sync_uses_dataset(monkeypatch):
    import app.routes.connectors as conn_route

    calls = []
    monkeypatch.setattr(conn_route, "kickoff_corpus_seed",
                        lambda cid, slug: calls.append((cid, slug)))
    conn_route._seed_corpus_after_sync("co-X", "explicit-dataset")
    assert calls == [("co-X", "explicit-dataset")]


def test_seed_corpus_after_sync_falls_back_to_company_slug(monkeypatch):
    import app.routes.connectors as conn_route
    from app.db import companies

    monkeypatch.setattr(companies, "slug_for_company_id", lambda cid: "company-slug")
    calls = []
    monkeypatch.setattr(conn_route, "kickoff_corpus_seed",
                        lambda cid, slug: calls.append((cid, slug)))
    conn_route._seed_corpus_after_sync("co-X", None)
    assert calls == [("co-X", "company-slug")]


def test_seed_corpus_after_sync_skips_when_no_slug(monkeypatch):
    import app.routes.connectors as conn_route
    from app.db import companies

    monkeypatch.setattr(companies, "slug_for_company_id", lambda cid: None)
    calls = []
    monkeypatch.setattr(conn_route, "kickoff_corpus_seed",
                        lambda cid, slug: calls.append((cid, slug)))
    conn_route._seed_corpus_after_sync("co-orphan", None)
    assert calls == []       # no slug → nothing to seed, no crash


# ───────────────────── file-upload route trigger ─────────────────────

def test_upload_files_triggers_corpus_seed(tenant_client, monkeypatch):
    """A successful upload must kick a background corpus seed for the dataset."""
    import app.routes.datasets as ds_route

    calls = []
    monkeypatch.setattr(ds_route, "kickoff_corpus_seed",
                        lambda cid, slug: calls.append((cid, slug)))

    t = tenant_client.make(slug="acme")
    t.client.post("/v1/datasets", json={"slug": "acme", "display_name": "Acme"})
    files = [("files", ("a.txt", io.BytesIO(b"hello world"), "text/plain"))]
    r = t.client.post("/v1/datasets/acme/files", files=files)
    assert r.status_code == 200, r.text
    assert calls == [(t.company_id, "acme")]


def test_upload_all_failed_does_not_trigger_corpus_seed(tenant_client, monkeypatch):
    """If no file ingested (all errored/too large), don't kick a pointless seed."""
    import app.routes.datasets as ds_route

    calls = []
    monkeypatch.setattr(ds_route, "kickoff_corpus_seed",
                        lambda cid, slug: calls.append((cid, slug)))
    monkeypatch.setattr(ds_route, "MAX_UPLOAD_BYTES", 1)  # force size rejection

    t = tenant_client.make(slug="acme")
    t.client.post("/v1/datasets", json={"slug": "acme", "display_name": "Acme"})
    files = [("files", ("big.txt", io.BytesIO(b"too large for the cap"), "text/plain"))]
    r = t.client.post("/v1/datasets/acme/files", files=files)
    assert r.status_code == 200, r.text
    assert r.json()["ingested"] == []
    assert calls == []
