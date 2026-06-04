"""Tests for the Phase-1 ingestion pipeline: pullers → RawRecord → runner → KG."""
from __future__ import annotations

from unittest.mock import patch

import pytest

from app.kg_ingest.types import RawRecord


# ---------- RawRecord rendering ----------

def test_rawrecord_render_includes_structured_props():
    r = RawRecord(provider="hubspot", kind="deal", external_id="123",
                  title="Acme renewal", text="Blocked on SSO",
                  properties={"amount_usd": "140000", "stage": "negotiation",
                              "empty": ""},
                  timestamp="2026-06-01")
    out = r.render()
    assert "[hubspot/deal id=123 at=2026-06-01]" in out
    assert "amount_usd=140000" in out and "stage=negotiation" in out
    assert "empty=" not in out
    assert "Blocked on SSO" in out


# ---------- pullers (mocked HTTP) ----------

def test_clickup_puller_yields_tasks(monkeypatch):
    from app.kg_ingest.pullers import clickup

    pages = {
        "/team": {"teams": [{"id": "T1"}]},
        "/team/T1/task": {"tasks": [{
            "id": "task-1", "name": "Fix login bug",
            "text_content": "Users report 500 on login",
            "status": {"status": "open"}, "priority": {"priority": "high"},
            "list": {"name": "Sprint 12"}, "tags": [{"name": "auth"}],
            "assignees": [{"username": "jide"}],
            "date_updated": "1780000000000",
        }], "last_page": True},
    }
    monkeypatch.setattr(clickup, "_get", lambda tok, path, params=None: pages[path])
    recs = list(clickup.pull("tok"))
    assert len(recs) == 1
    r = recs[0]
    assert (r.provider, r.kind, r.external_id) == ("clickup", "task", "task-1")
    assert r.properties["status"] == "open"
    assert r.properties["tags"] == ["auth"]


def test_hubspot_puller_yields_deals_with_paging(monkeypatch):
    from app.kg_ingest.pullers import hubspot

    calls = []
    def fake_get(tok, path, params=None):
        calls.append(params)
        if len(calls) == 1:
            return {"results": [{
                "id": "d1",
                "properties": {"dealname": "Acme", "amount": "140000",
                               "dealstage": "closedlost",
                               "description": "lost on missing SSO",
                               "hs_lastmodifieddate": "2026-06-01"},
                "associations": {"companies": {"results": [{"id": "c9"}]}},
            }], "paging": {"next": {"after": "pg2"}}}
        return {"results": [{
            "id": "d2", "properties": {"dealname": "Globex", "amount": "50000"},
        }]}
    monkeypatch.setattr(hubspot, "_get", fake_get)
    recs = list(hubspot.pull("tok"))
    assert [r.external_id for r in recs] == ["d1", "d2"]
    assert recs[0].properties["company_ids"] == ["c9"]
    assert calls[1]["after"] == "pg2"


def test_fireflies_puller_yields_meetings(monkeypatch):
    from app.kg_ingest.pullers import fireflies

    class FakeResp:
        status_code = 200
        def raise_for_status(self): ...
        def json(self):
            return {"data": {"transcripts": [{
                "id": "m1", "title": "Acme QBR", "date": 1780000000,
                "participants": ["a@acme.com", "pm@sprntly.ai"],
                "summary": {"overview": "Asked for SSO twice",
                            "action_items": "Follow up on SSO timeline",
                            "keywords": ["sso"]},
            }]}}
    with patch.object(fireflies.requests, "post", return_value=FakeResp()):
        recs = list(fireflies.pull("key"))
    assert len(recs) == 1
    assert recs[0].kind == "meeting"
    assert "Asked for SSO twice" in recs[0].text
    assert recs[0].properties["participants"] == ["a@acme.com", "pm@sprntly.ai"]


def test_fireflies_graphql_error_raises(monkeypatch):
    from app.kg_ingest.pullers import fireflies

    class FakeResp:
        def raise_for_status(self): ...
        def json(self): return {"errors": [{"message": "bad key"}]}
    with patch.object(fireflies.requests, "post", return_value=FakeResp()):
        with pytest.raises(RuntimeError, match="GraphQL error"):
            list(fireflies.pull("key"))


# ---------- runner ----------

def _recs(n, provider="clickup"):
    return [RawRecord(provider=provider, kind="task", external_id=f"t{i}",
                      title=f"Task {i}", text="x" * 500) for i in range(n)]


def test_runner_batches_and_aggregates(isolated_settings):
    from app.graph import GraphFacade
    from app.kg_ingest import runner

    facade = GraphFacade()
    seen_docs = []
    def fake_extract(f, eid, *, doc_name, text, agent, source_hint=None):
        seen_docs.append((doc_name, len(text), source_hint))
        return {"signals": 2, "themes": 1, "skipped": 0}

    with patch.object(runner, "extract_document", side_effect=fake_extract):
        out = runner.sync_provider(facade, "ent-A", "clickup",
                                   token="t", records=_recs(20))
    assert out["records"] == 20
    assert out["batches"] >= 2                       # char budget forces split
    assert out["signals"] == out["batches"] * 2
    assert not out["errors"]
    assert all("clickup-sync-batch-" in d for d, _, _ in seen_docs)
    assert all(l <= 7000 for _, l, _ in seen_docs)
    assert all(h and "project_mgmt" in h for _, _, h in seen_docs)


def test_runner_isolates_batch_errors(isolated_settings):
    from app.graph import GraphFacade
    from app.kg_ingest import runner

    calls = {"n": 0}
    def flaky(f, eid, **kw):
        calls["n"] += 1
        if calls["n"] == 1:
            raise RuntimeError("LLM exploded")
        return {"signals": 1, "themes": 0, "skipped": 0}

    with patch.object(runner, "extract_document", side_effect=flaky):
        out = runner.sync_provider(GraphFacade(), "ent-A", "clickup",
                                   token="t", records=_recs(20))
    assert len(out["errors"]) == 1
    assert out["signals"] >= 1                       # later batches still ran


def test_runner_unknown_provider_raises(isolated_settings):
    from app.graph import GraphFacade
    from app.kg_ingest import runner

    with pytest.raises(ValueError, match="No puller"):
        runner.sync_provider(GraphFacade(), "ent-A", "jira", token="t")


def test_token_for_picks_right_field():
    from app.kg_ingest.runner import token_for

    assert token_for("clickup", {"access_token": "a"}) == "a"
    assert token_for("fireflies", {"api_key": "k"}) == "k"
    with pytest.raises(ValueError, match="api_key"):
        token_for("fireflies", {"access_token": "wrong-shape"})
