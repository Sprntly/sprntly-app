"""Tests for the Phase-1 ingestion pipeline: pullers → RawRecord → runner → KG."""
from __future__ import annotations

from unittest.mock import MagicMock, patch

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


def test_jira_puller_yields_issues(monkeypatch):
    from app.connectors import jira_oauth
    from app.kg_ingest.pullers import jira

    # cloud_id resolution is a separate call — stub it so the test targets pull().
    monkeypatch.setattr(jira, "first_cloud_id", lambda tok: "cloud-1")

    search_body = {
        "issues": [{
            "id": "10001", "key": "PROJ-1",
            "fields": {
                "summary": "Fix login bug",
                "description": {
                    "type": "doc", "version": 1,
                    "content": [{"type": "paragraph", "content": [
                        {"type": "text", "text": "Users report 500 on login"},
                    ]}],
                },
                "status": {"name": "In Progress"},
                "priority": {"name": "High"},
                "issuetype": {"name": "Bug"},
                "project": {"name": "Platform"},
                "labels": ["auth"],
                "assignee": {"displayName": "Jide"},
                "updated": "2026-07-01T00:00:00.000+0000",
            },
        }],
        "isLast": True,
    }
    resp = MagicMock()
    resp.json.return_value = search_body
    resp.raise_for_status.return_value = None
    captured = {}
    def fake_get(url, params=None, headers=None, timeout=None):
        captured["params"] = params
        return resp
    monkeypatch.setattr(jira.requests, "get", fake_get)

    recs = list(jira.pull("tok"))
    assert len(recs) == 1
    r = recs[0]
    assert (r.provider, r.kind, r.external_id) == ("jira", "issue", "PROJ-1")
    assert r.title == "Fix login bug"
    assert "500 on login" in r.text
    assert r.properties["status"] == "In Progress"
    assert r.properties["type"] == "Bug"
    assert r.properties["labels"] == ["auth"]
    # Regression guard: the enhanced /search/jql endpoint 400s on unbounded JQL,
    # so the query MUST carry a search restriction (a bare ORDER BY is rejected).
    jql = captured["params"]["jql"].lower()
    assert "order by" in jql and ("created" in jql or "updated" in jql or ">=" in jql)
    assert jira_oauth  # imported for symmetry / ensures module loads


def test_jira_puller_no_site_yields_nothing(monkeypatch):
    from app.kg_ingest.pullers import jira
    monkeypatch.setattr(jira, "first_cloud_id", lambda tok: None)
    assert list(jira.pull("tok")) == []


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
    # _pull_deals is the deals sub-puller; the top-level pull() now fans out
    # across every CRM sub-resource (tickets/notes/owners/line items), tested
    # separately in test_hubspot_puller_expansion.
    recs = list(hubspot._pull_deals("tok"))
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


# ---------- confluence puller ----------
#
# The credential is a COMPANY ID, so every test stubs sync_context (the seam
# that reads the connection row) plus api_get / list_spaces — all in the
# puller's own namespace, per house style.


def _ctx(space_ids=(), space_keys=None, site_url="https://acme.atlassian.net/wiki"):
    from app.connectors.confluence_oauth import ConfluenceContext

    return ConfluenceContext(
        company_id="co-1",
        access_token="tok",
        cloud_id="cloud-1",
        base="https://api.atlassian.com/ex/confluence/cloud-1/wiki",
        site_url=site_url,
        space_ids=list(space_ids),
        space_keys=dict(space_keys or {}),
    )


_ENG = {"id": "s1", "key": "ENG", "name": "Engineering", "type": "global"}
_PROD = {"id": "s2", "key": "PROD", "name": "Product", "type": "global"}


def _page(pid="p1", title="Auth rewrite spec", body_html="<p>SSO is required.</p>",
          version=3):
    return {
        "id": pid,
        "title": title,
        "status": "current",
        "parentId": "p0",
        "authorId": "acct-9",
        "version": {"number": version, "createdAt": "2026-07-20T10:00:00Z"},
        "body": {"storage": {"representation": "storage", "value": body_html}},
        "_links": {"webui": f"/spaces/ENG/pages/{pid}"},
    }


def _stub(monkeypatch, ctx, *, pages_by_space, blogposts_by_space=None):
    """Route api_get by URL + space-id, so a test can assert WHICH spaces were
    fetched, not just what came back."""
    from app.kg_ingest.pullers import confluence

    requested: list[str] = []

    def fake_api_get(token, url, params=None, *, what="read"):
        params = params or {}
        sid = str(params.get("space-id") or "")
        if url.endswith("/api/v2/pages"):
            requested.append(sid)
            return {"results": (pages_by_space or {}).get(sid, [])}
        if url.endswith("/api/v2/blogposts"):
            return {"results": (blogposts_by_space or {}).get(sid, [])}
        return {}

    monkeypatch.setattr(confluence, "sync_context", lambda cid: ctx)
    monkeypatch.setattr(confluence, "api_get", fake_api_get)
    monkeypatch.setattr(
        confluence, "list_spaces", lambda tok, cloud, **kw: [_ENG, _PROD]
    )
    return confluence, requested


def test_confluence_puller_yields_pages(monkeypatch):
    ctx = _ctx()
    confluence, _ = _stub(monkeypatch, ctx, pages_by_space={"s1": [_page()], "s2": []})
    recs = [r for r in confluence.pull("co-1") if r.properties["space_key"] == "ENG"]
    assert len(recs) == 1
    r = recs[0]
    assert (r.provider, r.kind, r.external_id) == ("confluence", "page", "p1")
    assert r.title == "Auth rewrite spec"
    assert "SSO is required." in r.text
    assert r.properties["space_key"] == "ENG"
    assert r.properties["space_name"] == "Engineering"
    assert r.properties["version"] == 3
    assert r.properties["parent_id"] == "p0"
    assert r.properties["author_id"] == "acct-9"
    assert r.properties["url"] == \
        "https://acme.atlassian.net/wiki/spaces/ENG/pages/p1"
    # The page's last-modified, not its creation date.
    assert r.timestamp == "2026-07-20T10:00:00Z"


def test_confluence_puller_emits_blogposts_too(monkeypatch):
    ctx = _ctx()
    confluence, _ = _stub(
        monkeypatch, ctx,
        pages_by_space={"s1": [], "s2": []},
        blogposts_by_space={"s1": [_page(pid="b1", title="Launch retro")]},
    )
    kinds = {(r.kind, r.external_id) for r in confluence.pull("co-1")}
    assert ("blogpost", "b1") in kinds


def test_confluence_puller_honours_picked_spaces(monkeypatch):
    """The whole point of the picker: an unselected space is never fetched,
    not merely filtered out after the request."""
    ctx = _ctx(space_ids=["s2"], space_keys={"s2": "PROD"})
    confluence, requested = _stub(
        monkeypatch, ctx, pages_by_space={"s1": [_page()], "s2": [_page(pid="p9")]},
    )
    recs = list(confluence.pull("co-1"))
    assert requested == ["s2"]
    assert [r.external_id for r in recs] == ["p9"]


def test_confluence_puller_no_selection_pulls_every_space(monkeypatch):
    """Empty selection = everything readable — the backwards-compatible
    default that keeps a pre-picker connection working (mirrors slack_sync)."""
    ctx = _ctx(space_ids=[])
    confluence, requested = _stub(
        monkeypatch, ctx,
        pages_by_space={"s1": [_page()], "s2": [_page(pid="p9")]},
    )
    list(confluence.pull("co-1"))
    assert sorted(requested) == ["s1", "s2"]


def test_confluence_puller_skips_a_selection_that_no_longer_resolves(monkeypatch):
    """A deleted space, or one the connecting account lost access to, must not
    abort the rest of the sync."""
    ctx = _ctx(space_ids=["s1", "s404"], space_keys={"s404": "GONE"})
    confluence, requested = _stub(monkeypatch, ctx, pages_by_space={"s1": [_page()]})
    recs = list(confluence.pull("co-1"))
    assert requested == ["s1"]
    assert len(recs) == 1


def test_confluence_puller_flattens_storage_body(monkeypatch):
    """Storage format is XHTML with <ac:*>/<ri:*> macro tags — the text must
    survive, the markup and any script must not."""
    html = (
        "<p>Latency is <strong>2.4s</strong> at p95.</p>"
        '<ac:structured-macro ac:name="info"><ac:rich-text-body>'
        "<p>Owner is the platform team.</p></ac:rich-text-body>"
        "</ac:structured-macro><script>alert('x')</script>"
    )
    ctx = _ctx()
    confluence, _ = _stub(
        monkeypatch, ctx, pages_by_space={"s1": [_page(body_html=html)]},
    )
    text = next(iter(confluence.pull("co-1"))).text
    assert "Latency is 2.4s at p95." in text.replace("\n", " ")
    assert "Owner is the platform team." in text
    assert "ac:structured-macro" not in text
    assert "alert(" not in text


def test_confluence_puller_flattens_adf_body(monkeypatch):
    """ADF arrives as a JSON *string* inside `value` — it needs a json.loads
    before the walker, which is easy to miss."""
    import json as _json

    adf = _json.dumps({
        "type": "doc", "version": 1,
        "content": [{"type": "paragraph", "content": [
            {"type": "text", "text": "Churn rose to 9% in June."},
        ]}],
    })
    page = _page()
    page["body"] = {"atlas_doc_format": {
        "representation": "atlas_doc_format", "value": adf,
    }}
    ctx = _ctx()
    confluence, _ = _stub(monkeypatch, ctx, pages_by_space={"s1": [page]})
    assert "Churn rose to 9% in June." in next(iter(confluence.pull("co-1"))).text


def test_confluence_puller_survives_unparseable_adf(monkeypatch):
    page = _page()
    page["body"] = {"atlas_doc_format": {
        "representation": "atlas_doc_format", "value": "not json{{{",
    }}
    ctx = _ctx()
    confluence, _ = _stub(monkeypatch, ctx, pages_by_space={"s1": [page]})
    recs = list(confluence.pull("co-1"))
    # The title alone still carries signal, so the record survives with no body.
    assert len(recs) == 1 and recs[0].text == ""


def test_confluence_puller_paginates_by_cursor(monkeypatch):
    from app.kg_ingest.pullers import confluence

    seen_cursors: list[str | None] = []

    def fake_api_get(token, url, params=None, *, what="read"):
        params = params or {}
        if not url.endswith("/api/v2/pages"):
            return {}
        seen_cursors.append(params.get("cursor"))
        if params.get("cursor") is None:
            return {
                "results": [_page(pid="p1")],
                "_links": {"next": "/wiki/api/v2/pages?cursor=CUR2&limit=50"},
            }
        return {"results": [_page(pid="p2")]}

    monkeypatch.setattr(confluence, "sync_context", lambda cid: _ctx())
    monkeypatch.setattr(confluence, "api_get", fake_api_get)
    monkeypatch.setattr(confluence, "list_spaces", lambda tok, cloud, **kw: [_ENG])
    ids = [r.external_id for r in confluence.pull("co-1")]
    assert ids == ["p1", "p2"]
    assert seen_cursors == [None, "CUR2"]


def test_confluence_puller_requests_freshest_first(monkeypatch):
    """`sort=-modified-date` is what makes the per-space cap keep the pages
    that changed most recently — and why no watermark is needed."""
    from app.kg_ingest.pullers import confluence

    captured: dict = {}

    def fake_api_get(token, url, params=None, *, what="read"):
        if url.endswith("/api/v2/pages"):
            captured.update(params or {})
        return {}

    monkeypatch.setattr(confluence, "sync_context", lambda cid: _ctx())
    monkeypatch.setattr(confluence, "api_get", fake_api_get)
    monkeypatch.setattr(confluence, "list_spaces", lambda tok, cloud, **kw: [_ENG])
    list(confluence.pull("co-1"))
    assert captured["sort"] == "-modified-date"
    assert captured["body-format"] == "storage"


def test_confluence_puller_isolates_a_bad_space(monkeypatch):
    from app.kg_ingest.pullers import confluence

    def fake_api_get(token, url, params=None, *, what="read"):
        if (params or {}).get("space-id") == "s1":
            raise RuntimeError("space exploded")
        if url.endswith("/api/v2/pages"):
            return {"results": [_page(pid="p9")]}
        return {}

    monkeypatch.setattr(confluence, "sync_context", lambda cid: _ctx())
    monkeypatch.setattr(confluence, "api_get", fake_api_get)
    monkeypatch.setattr(confluence, "list_spaces", lambda tok, cloud, **kw: [_ENG, _PROD])
    recs = list(confluence.pull("co-1"))
    assert [r.external_id for r in recs] == ["p9"]


def test_confluence_puller_raises_when_every_space_failed(monkeypatch):
    """A revoked grant must not report a cheerful zero-record sync — on the
    connection row that is indistinguishable from "this wiki is empty"."""
    from app.kg_ingest.pullers import confluence

    def boom(token, url, params=None, *, what="read"):
        raise RuntimeError("all dead")

    monkeypatch.setattr(confluence, "sync_context", lambda cid: _ctx())
    monkeypatch.setattr(confluence, "api_get", boom)
    monkeypatch.setattr(confluence, "list_spaces", lambda tok, cloud, **kw: [_ENG])
    with pytest.raises(RuntimeError, match="all dead"):
        list(confluence.pull("co-1"))


def test_confluence_puller_never_swallows_a_reconnect_signal(monkeypatch):
    """Per-space isolation must not eat an auth failure — that is the one
    error the user can actually act on.

    The exception class is taken from the PULLER's namespace, not by importing
    it fresh: test_routes_connectors_confluence importlib.reloads
    confluence_oauth, so a fresh import in the same session is a different
    class object and `except` would silently miss it — the test would then
    pass via the generic per-space isolation path instead of the branch it
    means to pin."""
    from app.kg_ingest.pullers import confluence

    def dead(token, url, params=None, *, what="read"):
        raise confluence.ConfluenceAuthExpiredError("revoked")

    monkeypatch.setattr(confluence, "sync_context", lambda cid: _ctx())
    monkeypatch.setattr(confluence, "api_get", dead)
    monkeypatch.setattr(confluence, "list_spaces", lambda tok, cloud, **kw: [_ENG, _PROD])
    with pytest.raises(confluence.ConfluenceAuthExpiredError):
        list(confluence.pull("co-1"))


def test_confluence_puller_caps_total_records(monkeypatch):
    from app.kg_ingest.pullers import confluence

    monkeypatch.setattr(confluence, "_MAX_RECORDS", 3)
    ctx = _ctx()
    _stub(monkeypatch, ctx,
          pages_by_space={"s1": [_page(pid=f"p{i}") for i in range(10)]})
    assert len(list(confluence.pull("co-1"))) == 3


def test_confluence_puller_quiet_when_not_connected(monkeypatch):
    """A disconnected company is a no-op, not an error — the scheduler sweeps
    every row and a race with a disconnect must not stamp a failure."""
    from app.kg_ingest.pullers import confluence

    def gone(cid):
        # From the puller's namespace — see the reload note above.
        raise confluence.ConfluenceNotConnectedError("no row")

    monkeypatch.setattr(confluence, "sync_context", gone)
    assert list(confluence.pull("co-1")) == []


# ---------- runner ----------

def _recs(n, provider="clickup"):
    return [RawRecord(provider=provider, kind="task", external_id=f"t{i}",
                      title=f"Task {i}", text="x" * 500) for i in range(n)]


def test_runner_batches_and_aggregates(isolated_settings):
    from app.graph import GraphFacade
    from app.kg_ingest import runner

    facade = GraphFacade()
    seen_docs = []
    def fake_extract(f, eid, *, doc_name, text, agent, source_hint=None,
                     origin=None, provenance_extra=None, skill_id=None,
                     triage=None):
        seen_docs.append((doc_name, len(text), source_hint, origin,
                          provenance_extra, skill_id))
        return {"signals": 2, "themes": 1, "skipped": 0}

    with patch.object(runner, "extract_document", side_effect=fake_extract):
        out = runner.sync_provider(facade, "ent-A", "clickup",
                                   token="t", records=_recs(20))
    assert out["records"] == 20
    assert out["batches"] >= 2                       # char budget forces split
    assert out["signals"] == out["batches"] * 2
    assert not out["errors"]
    assert all("clickup-sync-batch-" in d for d, *_ in seen_docs)
    assert all(l <= 7000 for _, l, *_ in seen_docs)
    assert all(h and "project_mgmt" in h for _, _, h, *_ in seen_docs)
    # Connector syncs stamp origin="connector" so the brief gate never treats
    # a tenant with live connectors as upload-only.
    assert all(o == "connector" for _, _, _, o, _, _ in seen_docs)
    # Third-party syncs carry no channel stamp (only `uploads` does).
    assert all(pe is None for *_, pe, _sid in seen_docs)
    # ClickUp is skill-routed (PROVIDER_SKILLS) — every batch carries its
    # dedicated extraction skill id.
    assert all(sid == "clickup-extraction" for *_, sid in seen_docs)


def test_runner_stamps_upload_channel_for_uploads_provider(isolated_settings):
    """The uploads "connector" is the user's own documents — its signals carry
    channel="upload" so convergence keeps the upload-only brief relaxation
    (same evidentiary class as manual uploads; mirrors #868's Drive rationale)."""
    from app.graph import GraphFacade
    from app.kg_ingest import runner

    seen = []
    def fake_extract(f, eid, **kw):
        seen.append(kw)
        return {"signals": 1, "themes": 0, "skipped": 0}

    with patch.object(runner, "extract_document", side_effect=fake_extract):
        out = runner.sync_provider(GraphFacade(), "ent-A", "uploads",
                                   token="co-1", records=_recs(3, provider="uploads"))
    assert not out["errors"]
    assert seen and all(k["origin"] == "connector" for k in seen)
    assert all(k["provenance_extra"] == {"channel": "upload"} for k in seen)


def test_runner_stamps_upload_channel_for_confluence(isolated_settings):
    """A wiki is the same evidentiary class as an upload: internal
    documentation, not measured customer signal. Without channel="upload" a
    tenant connecting Confluence would silently LOSE the brief gate's
    upload-only relaxation — briefs would get stricter, not richer."""
    from app.graph import GraphFacade
    from app.kg_ingest import runner

    seen = []
    def fake_extract(f, eid, **kw):
        seen.append(kw)
        return {"signals": 1, "themes": 0, "skipped": 0}

    with patch.object(runner, "extract_document", side_effect=fake_extract):
        out = runner.sync_provider(GraphFacade(), "ent-A", "confluence",
                                   token="co-1",
                                   records=_recs(3, provider="confluence"))
    assert not out["errors"]
    assert seen and all(k["origin"] == "connector" for k in seen)
    assert all(k["provenance_extra"] == {"channel": "upload"} for k in seen)
    assert all("internal_documentation" in k["source_hint"] for k in seen)


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
        runner.sync_provider(GraphFacade(), "ent-A", "nonexistent_provider", token="t")


def test_token_for_picks_right_field():
    from app.kg_ingest.runner import token_for

    assert token_for("clickup", {"access_token": "a"}) == "a"
    assert token_for("fireflies", {"api_key": "k"}) == "k"
    # Confluence's credential is the COMPANY ID, not a token — the puller
    # needs the connection's config (picked spaces, cloud_id) and token_for
    # can only hand it one field. Same shape as uploads.
    assert token_for("confluence", {"company_id": "co-1"}) == "co-1"
    with pytest.raises(ValueError, match="company_id"):
        token_for("confluence", {"access_token": "wrong-shape"})
    with pytest.raises(ValueError, match="api_key"):
        token_for("fireflies", {"access_token": "wrong-shape"})


def test_sync_route_uses_company_scoped_connection(isolated_settings, monkeypatch):
    """Route-level: get_connection/update_connection_sync must be called with
    company_id first (Martin's #136 multitenancy) — guards the seam that broke
    when #114 and #136 landed in parallel."""
    from fastapi.testclient import TestClient
    import app.main as main_mod
    from app.auth import CompanyContext
    import app.routes.ingest as ingest_route
    # Override via the route module's OWN captured reference — app.auth may
    # have been reloaded by fixtures, making a fresh import a different object.
    require_company = ingest_route.require_company

    calls = {}
    monkeypatch.setattr(ingest_route.db, "get_connection",
                        lambda cid, prov: calls.setdefault("get", (cid, prov)) and None)
    main_mod.app.dependency_overrides[require_company] = lambda: CompanyContext(
        company_id="co-X", role="admin", user_id="u1")
    try:
        client = TestClient(main_mod.app)
        r = client.post("/v1/ingest/clickup/sync")
    finally:
        main_mod.app.dependency_overrides.pop(require_company, None)
    assert r.status_code == 404                  # no connection row → 404
    assert calls["get"] == ("co-X", "clickup")   # company-scoped call shape
