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


# ---------- zoom puller ----------
#
# Like Confluence, the credential is a COMPANY ID, so every test stubs
# sync_context (the seam that reads the connection row) plus the Zoom read
# helpers — all in the puller's own namespace, per house style.


_VTT = """WEBVTT

1
00:00:01.000 --> 00:00:04.000
Sam Lee: Right, the renewal.

2
00:00:04.000 --> 00:00:08.000
Sam Lee: They will not sign without SSO.

3
00:00:08.000 --> 00:00:11.000
Kim Patel: That is the third account this quarter.
"""


def _zctx(user_ids=(), user_names=None, cursor=None):
    from app.connectors.zoom_oauth import ZoomContext

    return ZoomContext(
        company_id="co-1",
        access_token="tok",
        user_ids=list(user_ids),
        user_names=dict(user_names or {}),
        last_synced_until=cursor,
    )


def _meeting(uuid="m1", topic="Acme renewal", *, files=None, start="2026-07-20T10:00:00Z"):
    return {
        "uuid": uuid,
        "id": 99,
        "topic": topic,
        "start_time": start,
        "duration": 42,
        "host_email": "sam@acme.co",
        "recording_files": (
            [{"file_type": "TRANSCRIPT", "file_extension": "VTT",
              "download_url": "https://zoom.test/dl/secret-token-in-url"}]
            if files is None else files
        ),
    }


def _zstub(monkeypatch, ctx, *, meetings_by_host, vtt=_VTT, users=None):
    """Route the Zoom read helpers, recording which host/window was asked for
    so a test can assert WHAT was fetched, not just what came back."""
    from app.kg_ingest.pullers import zoom

    asked: list[tuple[str, str, str]] = []

    def fake_list_recordings(_tok, user_id, *, frm=None, to=None, **_kw):
        asked.append((str(user_id), frm, to))
        return list((meetings_by_host or {}).get(str(user_id), []))

    monkeypatch.setattr(zoom, "sync_context", lambda cid: ctx)
    monkeypatch.setattr(zoom, "list_user_recordings", fake_list_recordings)
    monkeypatch.setattr(
        zoom, "list_users",
        lambda _tok, **kw: (
            users if users is not None else [
                {"id": "u1", "email": "sam@acme.co", "display_name": "Sam",
                 "licensed": True},
                {"id": "u2", "email": "kim@acme.co", "display_name": "Kim",
                 "licensed": True},
            ],
            False,
        ),
    )
    monkeypatch.setattr(zoom, "fetch_transcript_text", lambda _tok, _url: vtt)
    monkeypatch.setattr(zoom, "get_meeting_recordings", lambda _tok, _uuid: {})
    monkeypatch.setattr(zoom, "_stamp_counters", lambda *a, **k: None)
    return zoom, asked


# ── VTT parsing ──────────────────────────────────────────────────────────────


def test_vtt_parses_to_speaker_attributed_text_and_merges_runs():
    """Zoom emits a cue every few seconds, so an unmerged transcript is
    hundreds of two-line fragments — which reads to an extractor as hundreds of
    disconnected utterances rather than one person making one argument."""
    from app.kg_ingest.pullers.zoom import parse_vtt

    text, speakers = parse_vtt(_VTT)
    assert text == (
        "Sam Lee: Right, the renewal. They will not sign without SSO.\n"
        "Kim Patel: That is the third account this quarter."
    )
    assert speakers == ["Sam Lee", "Kim Patel"]
    # Timecodes, cue indices and the WEBVTT header are all gone.
    assert "-->" not in text and "WEBVTT" not in text
    assert "00:00" not in text


def test_vtt_tolerates_cues_with_no_speaker_prefix():
    """Some accounts record without speaker attribution. Dropping those cues
    would turn a perfectly good transcript into an empty one."""
    from app.kg_ingest.pullers.zoom import parse_vtt

    raw = (
        "WEBVTT\n\n1\n00:00:01.000 --> 00:00:03.000\nWe should ship it.\n\n"
        "2\n00:00:03.000 --> 00:00:05.000\nAgreed.\n\n"
        "3\n00:00:05.000 --> 00:00:08.000\nKim: I'll write it up.\n"
    )
    text, speakers = parse_vtt(raw)
    assert "We should ship it. Agreed." in text
    assert "Kim: I'll write it up." in text
    assert speakers == ["Kim"]


def test_vtt_does_not_mistake_a_colon_in_a_sentence_for_a_speaker():
    """A length bound alone is not enough — "the problem is this" is only
    nineteen characters, so a purely length-based rule attributes a customer's
    actual complaint to a speaker of that name and then merges every later cue
    into it."""
    from app.kg_ingest.pullers.zoom import parse_vtt

    raw = (
        "WEBVTT\n\n1\n00:00:01.000 --> 00:00:03.000\n"
        "the problem is this: nobody in the enterprise tier can log in at all\n"
    )
    text, speakers = parse_vtt(raw)
    assert speakers == []
    # The words are all still there — a missing attribution is less detail, a
    # WRONG one is asserted misinformation.
    assert text.startswith("the problem is this:")
    assert "nobody in the enterprise tier can log in at all" in text


def test_vtt_keeps_a_real_speaker_whose_line_also_contains_a_colon():
    from app.kg_ingest.pullers.zoom import parse_vtt

    raw = (
        "WEBVTT\n\n1\n00:00:01.000 --> 00:00:03.000\n"
        "Sam Lee: the problem is this: nobody can log in\n"
    )
    text, speakers = parse_vtt(raw)
    assert speakers == ["Sam Lee"]
    assert text == "Sam Lee: the problem is this: nobody can log in"


def test_vtt_accepts_an_email_style_speaker_label():
    from app.kg_ingest.pullers.zoom import parse_vtt

    raw = (
        "WEBVTT\n\n1\n00:00:01.000 --> 00:00:03.000\n"
        "sam@acme.co: we need SSO\n"
    )
    _text, speakers = parse_vtt(raw)
    assert speakers == ["sam@acme.co"]


def test_vtt_empty_input_is_empty_output():
    from app.kg_ingest.pullers.zoom import parse_vtt

    assert parse_vtt("") == ("", [])
    assert parse_vtt("WEBVTT\n\n") == ("", [])


# ── Records ──────────────────────────────────────────────────────────────────


def test_zoom_puller_yields_transcript_records(monkeypatch):
    ctx = _zctx(user_ids=["u1"], user_names={"u1": "sam@acme.co"})
    zoom, _ = _zstub(monkeypatch, ctx, meetings_by_host={"u1": [_meeting()]})
    recs = list(zoom.pull("co-1"))
    assert len(recs) == 1
    r = recs[0]
    assert (r.provider, r.kind, r.external_id) == ("zoom", "meeting", "m1")
    assert r.title == "Acme renewal"
    assert "Sam Lee: Right, the renewal." in r.text
    assert r.properties["host_email"] == "sam@acme.co"
    assert r.properties["duration_min"] == 42
    assert r.properties["speakers"] == ["Sam Lee", "Kim Patel"]
    assert r.properties["has_transcript"] is True
    assert r.timestamp == "2026-07-20T10:00:00Z"


def test_zoom_record_never_carries_a_download_url_or_token(monkeypatch):
    """A Zoom download_url is a credential-bearing link to customer
    conversation. It must not reach properties, the rendered record, or the
    content hash."""
    ctx = _zctx(user_ids=["u1"])
    zoom, _ = _zstub(monkeypatch, ctx, meetings_by_host={"u1": [_meeting()]})
    r = list(zoom.pull("co-1"))[0]
    blob = r.render() + repr(r.properties)
    assert "secret-token-in-url" not in blob
    assert "download_url" not in blob
    assert "zoom.test" not in blob


def test_zoom_puller_never_logs_the_download_url(monkeypatch, caplog):
    import logging as _logging

    ctx = _zctx(user_ids=["u1"])
    zoom, _ = _zstub(monkeypatch, ctx, meetings_by_host={"u1": [_meeting()]})
    with caplog.at_level(_logging.DEBUG):
        list(zoom.pull("co-1"))
    logged = " ".join(rec.getMessage() for rec in caplog.records)
    assert "secret-token-in-url" not in logged
    assert "zoom.test" not in logged


def test_a_meeting_with_no_transcript_still_yields_a_record(monkeypatch):
    """"We found nothing" and "we could not look" are different answers. The
    commonest cause is audio transcription switched off in the customer's Zoom
    account — silently skipping those meetings would present a half-empty
    corpus as a complete one, with nothing anywhere to explain the gap."""
    ctx = _zctx(user_ids=["u1"])
    zoom, _ = _zstub(
        monkeypatch, ctx,
        meetings_by_host={"u1": [_meeting(files=[
            {"file_type": "MP4", "file_extension": "MP4", "download_url": "x"},
        ])]},
    )
    recs = list(zoom.pull("co-1"))
    assert len(recs) == 1
    assert "no transcript available" in recs[0].text.lower()
    assert recs[0].properties["has_transcript"] is False
    # The meeting is still identifiable — this is a record, not a placeholder.
    assert recs[0].external_id == "m1"
    assert recs[0].title == "Acme renewal"


def test_a_transcript_shape_we_cannot_parse_is_skipped_not_crashed(monkeypatch):
    """Zoom's docs contradict themselves on whether TRANSCRIPT is .vtt or
    .json. A JSON transcript must degrade to the no-transcript record rather
    than be fed to a WebVTT parser."""
    ctx = _zctx(user_ids=["u1"])
    zoom, _ = _zstub(
        monkeypatch, ctx,
        meetings_by_host={"u1": [_meeting(files=[
            {"file_type": "TRANSCRIPT", "file_extension": "JSON",
             "download_url": "x"},
        ])]},
    )
    recs = list(zoom.pull("co-1"))
    assert len(recs) == 1
    assert "no transcript available" in recs[0].text.lower()


def test_cc_captions_are_ignored_as_a_duplicate(monkeypatch):
    """CC duplicates TRANSCRIPT's content, so falling back to it buys a second
    copy of the same conversation at the cost of a second extraction."""
    from app.connectors.zoom_oauth import transcript_file_from

    assert transcript_file_from({"recording_files": [
        {"file_type": "CC", "file_extension": "VTT", "download_url": "cc"},
    ]}) is None
    picked = transcript_file_from({"recording_files": [
        {"file_type": "CC", "file_extension": "VTT", "download_url": "cc"},
        {"file_type": "TRANSCRIPT", "file_extension": "VTT", "download_url": "t"},
    ]})
    assert picked["download_url"] == "t"


# ── Windowing ────────────────────────────────────────────────────────────────


def test_puller_never_requests_a_window_wider_than_one_month(monkeypatch):
    """Zoom SILENTLY CLAMPS a wider from/to instead of erroring, so a naive
    "last 90 days" request returns a month and looks like a quiet quarter."""
    from datetime import date

    from app.connectors.zoom_oauth import MAX_WINDOW_DAYS

    ctx = _zctx(user_ids=["u1"])
    zoom, asked = _zstub(monkeypatch, ctx, meetings_by_host={"u1": []})
    list(zoom.pull("co-1"))
    assert asked, "no window was requested at all"
    for _uid, frm, to in asked:
        span = (date.fromisoformat(to) - date.fromisoformat(frm)).days
        assert 0 <= span <= MAX_WINDOW_DAYS, (frm, to)


def test_first_sync_backfills_three_months_newest_first(monkeypatch):
    """A new connection should land a quarter of calls in the graph, not
    whatever happened this week — and if the valve trips, what gets dropped
    should be the OLDEST calls."""
    from datetime import date

    ctx = _zctx(user_ids=["u1"], cursor=None)
    zoom, asked = _zstub(monkeypatch, ctx, meetings_by_host={"u1": []})
    list(zoom.pull("co-1"))

    windows = [(f, t) for _u, f, t in asked]
    assert len(windows) == 3
    tos = [date.fromisoformat(t) for _f, t in windows]
    assert tos == sorted(tos, reverse=True)      # newest first
    span = date.fromisoformat(windows[0][1]) - date.fromisoformat(windows[-1][0])
    assert 89 <= span.days <= 91                 # ~3 months end to end


def test_a_later_run_walks_only_the_gap_since_the_last_sync(monkeypatch):
    """Without the cursor a 6-hourly schedule would re-walk the entire backfill
    forever: three windows per host, four times a day."""
    from datetime import date, timedelta

    yesterday = (date.today() - timedelta(days=1)).isoformat()
    ctx = _zctx(user_ids=["u1"], cursor=yesterday)
    zoom, asked = _zstub(monkeypatch, ctx, meetings_by_host={"u1": []})
    list(zoom.pull("co-1"))

    assert len(asked) == 1, asked
    assert asked[0][1] == yesterday


def test_a_long_outage_resumes_with_the_recent_past_not_a_year(monkeypatch):
    from datetime import date, timedelta

    ctx = _zctx(user_ids=["u1"],
                cursor=(date.today() - timedelta(days=400)).isoformat())
    zoom, asked = _zstub(monkeypatch, ctx, meetings_by_host={"u1": []})
    list(zoom.pull("co-1"))
    assert len(asked) == 3          # bounded by _MAX_WINDOWS, not 14 windows


def test_an_unreadable_cursor_falls_back_to_a_backfill(monkeypatch):
    ctx = _zctx(user_ids=["u1"], cursor="not-a-date")
    zoom, asked = _zstub(monkeypatch, ctx, meetings_by_host={"u1": []})
    list(zoom.pull("co-1"))
    assert len(asked) == 3


# ── Host selection ───────────────────────────────────────────────────────────


def test_zoom_puller_honours_the_host_selection(monkeypatch):
    """The whole point of the picker: an unselected host is never fetched, not
    merely filtered out after the request."""
    ctx = _zctx(user_ids=["u2"], user_names={"u2": "kim@acme.co"})
    zoom, asked = _zstub(
        monkeypatch, ctx,
        meetings_by_host={"u1": [_meeting()], "u2": [_meeting(uuid="m9")]},
    )
    recs = list(zoom.pull("co-1"))
    assert {uid for uid, _f, _t in asked} == {"u2"}
    assert [r.external_id for r in recs] == ["m9"]


def test_no_selection_pulls_every_licensed_host(monkeypatch):
    """Empty selection = every host — the backwards-compatible default that
    keeps a pre-picker connection working. Basic (unlicensed) accounts cannot
    record to the cloud at all, so listing them spends a request to learn
    nothing."""
    ctx = _zctx(user_ids=[])
    zoom, asked = _zstub(
        monkeypatch, ctx,
        meetings_by_host={"u1": [_meeting()], "u2": [_meeting(uuid="m9")],
                          "u3": [_meeting(uuid="m3")]},
        users=[
            {"id": "u1", "email": "sam@acme.co", "licensed": True},
            {"id": "u2", "email": "kim@acme.co", "licensed": True},
            {"id": "u3", "email": "basic@acme.co", "licensed": False},
        ],
    )
    recs = list(zoom.pull("co-1"))
    assert {uid for uid, _f, _t in asked} == {"u1", "u2"}
    assert "m3" not in {r.external_id for r in recs}


def test_a_selected_host_that_no_longer_lists_is_still_pulled(monkeypatch):
    """A deactivated host still OWNS their old recordings. Dropping them
    because they fell out of the active listing would silently shrink the
    corpus."""
    ctx = _zctx(user_ids=["gone-1"], user_names={"gone-1": "left@acme.co"})
    zoom, asked = _zstub(
        monkeypatch, ctx, meetings_by_host={"gone-1": [_meeting(uuid="m-old")]},
        users=[],
    )
    recs = list(zoom.pull("co-1"))
    assert [r.external_id for r in recs] == ["m-old"]
    assert asked[0][0] == "gone-1"


# ── Caps and failure isolation ───────────────────────────────────────────────


def test_recordings_per_host_cap_holds(monkeypatch):
    ctx = _zctx(user_ids=["u1"])
    zoom, _ = _zstub(
        monkeypatch, ctx,
        meetings_by_host={"u1": [_meeting(uuid=f"m{i}") for i in range(80)]},
    )
    monkeypatch.setattr(zoom, "_MAX_RECORDINGS_PER_HOST", 5)
    assert len(list(zoom.pull("co-1"))) == 5


def test_global_record_valve_holds(monkeypatch):
    """The content-hash ledger makes RE-syncs free, but the FIRST sync pays the
    LLM for everything."""
    ctx = _zctx(user_ids=["u1", "u2"])
    zoom, _ = _zstub(
        monkeypatch, ctx,
        meetings_by_host={
            "u1": [_meeting(uuid=f"a{i}") for i in range(10)],
            "u2": [_meeting(uuid=f"b{i}") for i in range(10)],
        },
    )
    monkeypatch.setattr(zoom, "_MAX_RECORDS", 4)
    assert len(list(zoom.pull("co-1"))) == 4


def test_text_is_capped_under_the_runner_batch_budget(monkeypatch):
    """A record that blew the 6000-char batch budget would be split across
    batches mid-sentence and both halves would lose their context."""
    from app.kg_ingest.runner import _BATCH_CHAR_BUDGET

    ctx = _zctx(user_ids=["u1"])
    long_vtt = "WEBVTT\n\n" + "\n\n".join(
        f"{i}\n00:00:0{i % 10}.000 --> 00:00:0{(i + 1) % 10}.000\n"
        f"Sam Lee: {'word ' * 40}"
        for i in range(200)
    )
    zoom, _ = _zstub(
        monkeypatch, ctx, meetings_by_host={"u1": [_meeting()]}, vtt=long_vtt,
    )
    r = list(zoom.pull("co-1"))[0]
    assert len(r.text) <= zoom._TEXT_CHARS
    assert len(r.render()) < _BATCH_CHAR_BUDGET


def test_a_deleted_recording_mid_sync_skips_without_failing(monkeypatch):
    """A recording deleted or trashed between the listing and the read is a
    normal race on a live account, not a broken credential."""
    ctx = _zctx(user_ids=["u1", "u2"])
    zoom, _ = _zstub(
        monkeypatch, ctx,
        meetings_by_host={"u1": [_meeting()], "u2": [_meeting(uuid="m9")]},
    )

    def flaky(_tok, user_id, **kw):
        if str(user_id) == "u1":
            raise RuntimeError("404 recording no longer exists")
        return [_meeting(uuid="m9")]

    monkeypatch.setattr(zoom, "list_user_recordings", flaky)
    recs = list(zoom.pull("co-1"))
    assert [r.external_id for r in recs] == ["m9"]


def test_an_expired_token_is_never_swallowed_by_host_isolation(monkeypatch):
    """Per-host isolation must not hide a reconnect signal — that would report
    a cheerful zero-record sync on a dead connection."""
    from app.connectors.zoom_oauth import ZoomAuthExpiredError

    ctx = _zctx(user_ids=["u1"])
    zoom, _ = _zstub(monkeypatch, ctx, meetings_by_host={"u1": []})

    def dead(*_a, **_k):
        raise ZoomAuthExpiredError("reconnect")

    monkeypatch.setattr(zoom, "list_user_recordings", dead)
    with pytest.raises(ZoomAuthExpiredError):
        list(zoom.pull("co-1"))


def test_zoom_puller_quiet_when_not_connected(monkeypatch):
    """A disconnected company is a no-op, not an error — the scheduler sweeps
    every row and a race with a disconnect must not stamp a failure."""
    from app.kg_ingest.pullers import zoom

    def gone(cid):
        raise zoom.ZoomNotConnectedError("no row")

    monkeypatch.setattr(zoom, "sync_context", gone)
    assert list(zoom.pull("co-1")) == []


def test_a_recurring_meeting_seen_in_two_windows_counts_once(monkeypatch):
    ctx = _zctx(user_ids=["u1"])
    zoom, _ = _zstub(monkeypatch, ctx, meetings_by_host={"u1": [_meeting()]})
    # Every window returns the same meeting.
    recs = list(zoom.pull("co-1"))
    assert [r.external_id for r in recs] == ["m1"]


# ── Counters + cursor ────────────────────────────────────────────────────────


def _capture_counters(monkeypatch, zoom):
    stamped: dict = {}
    monkeypatch.setattr(
        zoom, "_stamp_counters",
        lambda cid, *, meetings, transcripts, until: stamped.update(
            company_id=cid, meetings=meetings, transcripts=transcripts, until=until
        ),
    )
    return stamped


def test_counters_split_meetings_from_transcripts(monkeypatch):
    """The GAP between these two numbers is how the web layer says "recordings
    are syncing but transcription is off in Zoom" — a settings problem in the
    customer's account that is invisible without both."""
    ctx = _zctx(user_ids=["u1"])
    zoom, _ = _zstub(
        monkeypatch, ctx,
        meetings_by_host={"u1": [
            _meeting(uuid="m1"),
            _meeting(uuid="m2", files=[{"file_type": "MP4",
                                        "file_extension": "MP4",
                                        "download_url": "x"}]),
        ]},
    )
    stamped = _capture_counters(monkeypatch, zoom)
    list(zoom.pull("co-1"))
    assert stamped["meetings"] == 2
    assert stamped["transcripts"] == 1


def test_cursor_advances_on_a_clean_run(monkeypatch):
    from datetime import date

    ctx = _zctx(user_ids=["u1"])
    zoom, _ = _zstub(monkeypatch, ctx, meetings_by_host={"u1": [_meeting()]})
    stamped = _capture_counters(monkeypatch, zoom)
    list(zoom.pull("co-1"))
    assert stamped["until"] == date.today().isoformat()


def test_cursor_does_not_advance_when_a_host_failed(monkeypatch):
    """A partial run that moved the watermark would skip the failed host's
    window PERMANENTLY — those calls would never be picked up by any later
    sync, and nothing would ever say so."""
    ctx = _zctx(user_ids=["u1", "u2"])
    zoom, _ = _zstub(monkeypatch, ctx, meetings_by_host={"u2": [_meeting(uuid="m9")]})

    def flaky(_tok, user_id, **kw):
        if str(user_id) == "u1":
            raise RuntimeError("host unavailable")
        return [_meeting(uuid="m9")]

    monkeypatch.setattr(zoom, "list_user_recordings", flaky)
    stamped = _capture_counters(monkeypatch, zoom)
    list(zoom.pull("co-1"))
    assert stamped["until"] is None
    assert stamped["meetings"] == 1     # counters still report what DID happen


def test_cursor_does_not_advance_when_the_valve_tripped(monkeypatch):
    ctx = _zctx(user_ids=["u1"])
    zoom, _ = _zstub(
        monkeypatch, ctx,
        meetings_by_host={"u1": [_meeting(uuid=f"m{i}") for i in range(10)]},
    )
    monkeypatch.setattr(zoom, "_MAX_RECORDS", 2)
    stamped = _capture_counters(monkeypatch, zoom)
    list(zoom.pull("co-1"))
    assert stamped["until"] is None


def test_counters_are_written_through_the_config_MERGE(monkeypatch):
    """Not a wholesale upsert. The connection config also holds sync_user_ids,
    and replacing it here would silently widen a narrowed host selection back
    to every host — the same regression the OAuth callback had to be fixed
    for."""
    from app import db
    from app.kg_ingest.pullers import zoom

    calls: dict = {}
    monkeypatch.setattr(
        db, "patch_connection_config",
        lambda cid, provider, patch: calls.update(
            cid=cid, provider=provider, patch=patch
        ),
    )
    zoom._stamp_counters("co-1", meetings=4, transcripts=3, until="2026-08-04")

    assert calls["provider"] == "zoom"
    assert calls["patch"] == {
        "last_sync_meetings": 4,
        "last_sync_transcripts": 3,
        "last_synced_until": "2026-08-04",
    }
    # The merge writer is the one that preserves everything else on the config.
    assert "sync_user_ids" not in calls["patch"]


def test_stamping_counters_never_fails_a_good_sync(monkeypatch):
    from app import db
    from app.kg_ingest.pullers import zoom

    def boom(*_a, **_k):
        raise RuntimeError("supabase down")

    monkeypatch.setattr(db, "patch_connection_config", boom)
    zoom._stamp_counters("co-1", meetings=1, transcripts=1, until="2026-08-04")


# ── Registration ─────────────────────────────────────────────────────────────


def test_zoom_is_registered_as_a_company_id_puller():
    """The credential must be the COMPANY ID: a Zoom pull needs the picked
    hosts and the cursor off the connection row, which a lone access token
    cannot reach."""
    from app.kg_ingest.runner import PULLERS, _DOCUMENT_PROVIDERS

    puller, key, hint = PULLERS["zoom"]
    assert key == "company_id"
    assert "customer_voice" in hint
    # A recorded call is EVIDENCE, not an upload-class document — putting zoom
    # here would hand it the brief gate's upload-only relaxation it has not
    # earned.
    assert "zoom" not in _DOCUMENT_PROVIDERS


def test_the_scheduler_picks_zoom_up_with_no_scheduler_edit():
    """refresh_connectors fires kickoff_sync for any provider in PULLERS, so
    registration alone is what puts zoom on the 6-hourly sweep."""
    from app.kg_ingest.runner import PULLERS

    assert "zoom" in PULLERS


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
