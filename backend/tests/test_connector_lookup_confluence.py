"""Confluence live-lookup adapter.

Mirrors tests/test_connector_lookup_adapters.py — no network/LLM/DB, every
fetcher patched in its own namespace.

The load-bearing assertions:
  * Confluence is a REAL lookup provider, not DEFERRED. Before this adapter
    existed, naming Confluence in chat produced "syncs into your knowledge
    graph, but I can't query it live" — accurate, and useless when the pages
    are right there.
  * A search the token cannot RUN is never reported as a search that found
    nothing. That is the one failure mode capable of making chat state, with
    confidence, that a wiki has nothing on a topic it documents thoroughly.
  * The copy never claims wiki-wide coverage: reads are bounded by the
    connecting user's permissions and by the workspace's space selection.
"""
from __future__ import annotations

import inspect

import pytest

from app.connector_lookup import answer as ca
from app.connector_lookup import registry
from app.connector_lookup.base import DEFAULT_RESULT_CHARS
from app.connector_lookup.confluence import PROVIDER as CONFLUENCE
from app.connectors import confluence_fetch
from app.connectors.confluence_oauth import (
    ConfluenceAuthExpiredError,
    ConfluenceContext,
    ConfluenceNotConnectedError,
)


def _ctx(space_ids=()):
    return ConfluenceContext(
        company_id="co-1",
        access_token="tok",
        cloud_id="cloud-1",
        base="https://api.atlassian.com/ex/confluence/cloud-1/wiki",
        site_url="https://acme.atlassian.net/wiki",
        space_ids=list(space_ids),
        space_keys={},
    )


def _handle(space_ids=()):
    return confluence_fetch.ConfluenceSession(ctx=_ctx(space_ids))


def _session(handle=None):
    return ca.LookupSession(provider="confluence", handle=handle or _handle())


_ENG = {"id": "s1", "key": "ENG", "name": "Engineering", "type": "global"}
_PROD = {"id": "s2", "key": "PROD", "name": "Product", "type": "global"}


# ── Registry wiring ──────────────────────────────────────────────────────────


def test_confluence_is_a_live_lookup_provider_not_deferred():
    """The regression this whole adapter exists to prevent: a connected wiki
    that chat refuses to read."""
    assert "confluence" in registry.LOOKUP_PROVIDERS
    assert "confluence" not in registry.DEFERRED
    assert "confluence" not in registry.NO_CONNECTOR
    assert registry.provider_for("confluence") is CONFLUENCE
    assert registry.display_name("confluence") == "Confluence"


def test_adapter_satisfies_the_protocol():
    from app.connector_lookup.base import LookupProvider

    assert isinstance(CONFLUENCE, LookupProvider)
    assert CONFLUENCE.provider == "confluence"
    names = {t["name"] for t in CONFLUENCE.tools()}
    assert names == {
        "confluence_search", "confluence_list_pages",
        "confluence_list_spaces", "confluence_get_page",
    }


def test_open_session_none_when_not_connected(monkeypatch):
    def _gone(_cid):
        raise ConfluenceNotConnectedError("no row")

    monkeypatch.setattr(confluence_fetch, "sync_context", _gone)
    assert CONFLUENCE.open_session("co-1") is None


def test_open_session_never_raises_on_a_broken_credential(monkeypatch):
    """A chat answer must degrade to not-connected copy, not a 500."""
    def _boom(_cid):
        raise RuntimeError("decrypt exploded")

    monkeypatch.setattr(confluence_fetch, "sync_context", _boom)
    assert CONFLUENCE.open_session("co-1") is None


# ── Search ───────────────────────────────────────────────────────────────────


def test_search_renders_hits_with_space_and_link(monkeypatch):
    def _api_get(tok, url, params=None, *, what="read"):
        assert "/rest/api/search" in url
        assert 'text ~ "onboarding"' in params["cql"]
        return {"results": [{
            "content": {"id": "111", "title": "Onboarding spec", "type": "page",
                        "space": {"key": "ENG"}},
            "excerpt": "<p>New users land on the <b>welcome</b> screen</p>",
            "url": "/spaces/ENG/pages/111",
            "lastModified": "2026-07-30T10:00:00Z",
        }]}

    monkeypatch.setattr(confluence_fetch, "api_get", _api_get)
    out = CONFLUENCE.dispatch(_session(), "confluence_search", {"text": "onboarding"})
    assert "Onboarding spec" in out
    assert "space ENG" in out
    assert "https://acme.atlassian.net/wiki/spaces/ENG/pages/111" in out
    assert "welcome" in out            # excerpt HTML flattened
    assert "<b>" not in out


def test_search_narrows_to_a_space_key(monkeypatch):
    seen = {}

    def _api_get(tok, url, params=None, *, what="read"):
        seen["cql"] = params["cql"]
        return {"results": []}

    monkeypatch.setattr(confluence_fetch, "api_get", _api_get)
    CONFLUENCE.dispatch(
        _session(), "confluence_search", {"text": "sso", "space_key": "ENG"},
    )
    assert 'space.key = "ENG"' in seen["cql"]


def test_search_escapes_quotes_in_the_query(monkeypatch):
    """A quote in user text would otherwise break out of the CQL string
    literal and make the whole query a syntax error."""
    seen = {}

    def _api_get(tok, url, params=None, *, what="read"):
        seen["cql"] = params["cql"]
        return {"results": []}

    monkeypatch.setattr(confluence_fetch, "api_get", _api_get)
    CONFLUENCE.dispatch(_session(), "confluence_search", {"text": 'the "big" rewrite'})
    assert '\\"big\\"' in seen["cql"]


def test_search_unavailable_is_not_reported_as_no_results(monkeypatch):
    """THE assertion. CQL search is v1 and needs the classic search:confluence
    scope, which a connection authorized earlier does not carry. Treating that
    401 as an empty result set would have chat state that a wiki says nothing
    about a topic it documents thoroughly."""
    def _api_get(tok, url, params=None, *, what="read"):
        raise ConfluenceAuthExpiredError("Unauthorized; scope does not match")

    monkeypatch.setattr(confluence_fetch, "api_get", _api_get)
    out = CONFLUENCE.dispatch(_session(), "confluence_search", {"text": "sso"})
    assert "UNAVAILABLE" in out
    assert "NOT a no-results answer" in out
    assert "confluence_list_pages" in out     # steered to the fallback
    assert "reconnect" in out.lower()
    assert "no matching pages" not in out


def test_search_requires_text():
    assert "'text' is required" in CONFLUENCE.dispatch(
        _session(), "confluence_search", {},
    )


# ── dispatch_records (AC1/AC2/AC3/AC4) ──────────────────────────────────────


def test_dispatch_records_returns_none_for_other_tools():
    for name in ("confluence_list_pages", "confluence_list_spaces", "confluence_get_page"):
        assert CONFLUENCE.dispatch_records(_session(), name, {}) is None


def test_dispatch_records_returns_none_when_search_is_unavailable(monkeypatch):
    def _api_get(tok, url, params=None, *, what="read"):
        raise ConfluenceAuthExpiredError("scope gap")

    monkeypatch.setattr(confluence_fetch, "api_get", _api_get)
    text, records = CONFLUENCE.dispatch_records(
        _session(), "confluence_search", {"text": "sso"}
    )
    assert records is None
    assert "UNAVAILABLE" in text


def test_dispatch_records_text_matches_dispatch_exactly(monkeypatch):
    """AC5, mutation-proof."""
    def _api_get(tok, url, params=None, *, what="read"):
        return {"results": [{
            "content": {"id": "111", "title": "Onboarding spec", "type": "page",
                        "space": {"key": "ENG"}},
            "excerpt": "<p>New users land on the <b>welcome</b> screen</p>",
            "url": "/spaces/ENG/pages/111",
            "lastModified": "2026-07-30T10:00:00Z",
        }]}

    monkeypatch.setattr(confluence_fetch, "api_get", _api_get)
    expected = CONFLUENCE.dispatch(_session(), "confluence_search", {"text": "onboarding"})

    monkeypatch.setattr(confluence_fetch, "api_get", _api_get)
    text, records = CONFLUENCE.dispatch_records(
        _session(), "confluence_search", {"text": "onboarding"}
    )
    assert text == expected
    assert records is not None and len(records) == 1


def test_dispatch_records_ac4_not_byte_identical_to_the_puller(monkeypatch):
    """AC4 — Confluence's answer: NOT byte-identical, proven against the REAL
    scheduled-pull puller (`kg_ingest.pullers.confluence.pull`) for the SAME
    page, with the exact gaps named:

      - `space_name`, `status`, `version`, `parent_id`, `author_id`: none of
        these ride the CQL `/rest/api/search` response the sweep tool calls —
        only the v2 `/pages` GET the puller uses carries them.
      - `text`: a ~240-char CQL excerpt, not the puller's up-to-4,000-char
        converted page body.
      - `url`/`timestamp`: built from different response fields on the two
        API versions, not guaranteed to agree even when both are present.
    """
    from app.kg_ingest.pullers import confluence as confluence_puller

    v2_item = {
        "id": "111", "title": "Onboarding spec", "status": "current",
        "body": {"storage": {
            "value": "<p>New users land on the <b>welcome</b> screen</p>",
            "representation": "storage",
        }},
        "_links": {"webui": "/spaces/ENG/pages/111"},
        "version": {"number": 3, "createdAt": "2026-07-30T09:00:00Z"},
        "parentId": "100", "authorId": "acc-1",
    }

    def _puller_api_get(tok, url, params=None, *, what="read"):
        return {"results": [v2_item]}

    monkeypatch.setattr(confluence_puller, "sync_context", lambda cid: _ctx())
    monkeypatch.setattr(confluence_puller, "list_spaces", lambda tok, cloud: [_ENG])
    monkeypatch.setattr(confluence_puller, "api_get", _puller_api_get)
    pull_record = next(confluence_puller.pull("co-1"))

    def _search_api_get(tok, url, params=None, *, what="read"):
        return {"results": [{
            "content": {"id": "111", "title": "Onboarding spec", "type": "page",
                        "space": {"key": "ENG"}},
            "excerpt": "New users land on the welcome screen",
            "url": "/spaces/ENG/pages/111",
            "lastModified": "2026-07-30T10:00:00Z",   # search API's OWN notion of "when"
        }]}

    monkeypatch.setattr(confluence_fetch, "api_get", _search_api_get)
    _text, records = CONFLUENCE.dispatch_records(
        _session(), "confluence_search", {"text": "onboarding"}
    )
    sweep_record = records[0]

    assert sweep_record.render() != pull_record.render(), (
        "AC4: Confluence's search-based record must NOT claim byte-identity"
    )
    # AC3 — external_id still matches (the page id both sides agree on).
    assert sweep_record.external_id == pull_record.external_id == "111"
    assert sweep_record.properties["space_key"] == pull_record.properties["space_key"] == "ENG"
    # The gaps, named rather than merely observed.
    for missing in ("space_name", "status", "version", "parent_id", "author_id"):
        assert sweep_record.properties[missing] is None
        assert pull_record.properties[missing] is not None
    assert sweep_record.text != pull_record.text, (
        "the CQL excerpt (Confluence's own search ranking) and the puller's "
        "html_to_md-converted storage body are two different extractions, "
        "not a truncation of one another — this fixture's short body just "
        "happens to match the excerpt's CONTENT length; a real page's body "
        "would additionally be far longer than a ~240-char excerpt"
    )


# ── Listing + page fetch ─────────────────────────────────────────────────────


def test_list_pages_covers_only_the_selected_spaces(monkeypatch):
    """A workspace that picked spaces must not have chat read the others."""
    requested: list[str] = []

    def _api_get(tok, url, params=None, *, what="read"):
        if url.endswith("/api/v2/pages"):
            requested.append(params["space-id"])
            return {"results": [{
                "id": "p1", "title": "Runbook",
                "version": {"createdAt": "2026-07-30T09:00:00Z"},
                "_links": {"webui": "/spaces/PROD/pages/p1"},
            }]}
        return {}

    monkeypatch.setattr(confluence_fetch, "api_get", _api_get)
    monkeypatch.setattr(confluence_fetch, "list_spaces",
                        lambda tok, cloud, **kw: [_ENG, _PROD])
    out = CONFLUENCE.dispatch(
        _session(_handle(space_ids=["s2"])), "confluence_list_pages", {},
    )
    assert requested == ["s2"]
    assert "Runbook" in out


def test_list_spaces_says_whether_the_set_is_a_selection(monkeypatch):
    monkeypatch.setattr(confluence_fetch, "list_spaces",
                        lambda tok, cloud, **kw: [_ENG, _PROD])
    selected = CONFLUENCE.dispatch(
        _session(_handle(space_ids=["s1"])), "confluence_list_spaces", {},
    )
    assert "synced" in selected and "Engineering" in selected
    assert "Product" not in selected

    everything = CONFLUENCE.dispatch(_session(), "confluence_list_spaces", {})
    assert "readable" in everything
    assert "Engineering" in everything and "Product" in everything


def test_get_page_returns_the_body_as_text(monkeypatch):
    def _api_get(tok, url, params=None, *, what="read"):
        assert url.endswith("/api/v2/pages/131189")
        assert params["body-format"] == "storage"
        return {
            "id": "131189", "title": "Auth rewrite", "status": "current",
            "version": {"number": 4, "createdAt": "2026-07-30T10:00:00Z"},
            "_links": {"webui": "/spaces/ENG/pages/131189"},
            "body": {"storage": {"representation": "storage", "value":
                                 "<p>SSO ships in Q4.</p><script>x()</script>"}},
        }

    monkeypatch.setattr(confluence_fetch, "api_get", _api_get)
    out = CONFLUENCE.dispatch(
        _session(), "confluence_get_page", {"page_id": "131189"},
    )
    assert "Auth rewrite" in out
    assert "SSO ships in Q4." in out
    assert "version 4" in out
    assert "x()" not in out


def _page_body(text: str, *, page_id: str = "131189") -> dict:
    """Fake /pages (or /blogposts) v2 response with a given rendered body.
    Wrapped in a single <p> so html_to_md carries `text` through byte-for-byte
    — the tests can then reason about exact lengths and cap boundaries."""
    return {
        "id": page_id, "title": "Runbook", "status": "current",
        "version": {"number": 4, "createdAt": "2026-07-30T10:00:00Z"},
        "_links": {"webui": f"/spaces/ENG/pages/{page_id}"},
        "body": {"storage": {"representation": "storage", "value": f"<p>{text}</p>"}},
    }


def _dispatch_get_page(monkeypatch, page: dict, *, page_id: str = "131189") -> str:
    monkeypatch.setattr(confluence_fetch, "api_get", lambda *a, **kw: page)
    return CONFLUENCE.dispatch(_session(), "confluence_get_page", {"page_id": page_id})


def test_get_page_over_cap_says_it_was_truncated(monkeypatch):
    """AC1 — regression, RED on unfixed code. A page body over PAGE_BODY_CHARS
    was a bare slice with nothing telling the model it was cut, so a topic
    sitting past the cut read as absent. The marker must carry the real total
    length of the untruncated body, not the capped length."""
    body_text = "x" * (confluence_fetch.PAGE_BODY_CHARS + 6000)
    out = _dispatch_get_page(monkeypatch, _page_body(body_text))
    assert "characters — truncated" in out
    assert f"of {len(body_text):,} characters" in out
    assert f"of {confluence_fetch.PAGE_BODY_CHARS:,} characters" not in out


def test_get_page_truncation_does_not_hide_content_silently(monkeypatch):
    """AC1 — the user-facing symptom. The tail of the result must be the
    honest marker, not a mid-sentence cut the model would mistake for the
    whole page."""
    body_text = "Step one. " * 700  # 7000 rendered chars, over the cap
    out = _dispatch_get_page(monkeypatch, _page_body(body_text))
    assert out.rstrip().endswith("or narrow the query.)")
    assert not out.rstrip().endswith("Step one.")


def test_get_page_under_cap_is_unchanged(monkeypatch):
    """AC2 — a body at/under the cap renders exactly as before this fix: no
    marker, no trailing-whitespace change."""
    body_text = "y" * 500
    out = _dispatch_get_page(monkeypatch, _page_body(body_text))
    assert "truncated" not in out
    assert out.endswith(body_text)


def test_get_page_at_exactly_the_cap_has_no_marker(monkeypatch):
    """AC7 — boundary: a body of exactly PAGE_BODY_CHARS is not over the cap
    and must not carry a marker."""
    body_text = "x" * confluence_fetch.PAGE_BODY_CHARS
    out = _dispatch_get_page(monkeypatch, _page_body(body_text))
    assert "truncated" not in out
    assert out.endswith(body_text)


def test_get_page_one_char_over_the_cap_has_a_marker(monkeypatch):
    """AC7 — boundary: one char past PAGE_BODY_CHARS is over the cap."""
    body_text = "x" * (confluence_fetch.PAGE_BODY_CHARS + 1)
    out = _dispatch_get_page(monkeypatch, _page_body(body_text))
    assert "characters — truncated" in out


def test_get_page_empty_body_still_says_no_readable_text(monkeypatch):
    """AC8 — an empty storage value falls through to the existing 'no
    readable body text' copy, and carries no marker."""
    page = _page_body("x")
    page["body"]["storage"]["value"] = ""
    out = _dispatch_get_page(monkeypatch, page)
    assert "(this page has no readable body text)" in out
    assert "truncated" not in out


def test_confluence_uses_the_shared_truncation_marker(monkeypatch):
    """AC4 — exactly one marker format in the lane. Reads the connector's own
    module source: no hand-rolled truncation literal, only `cap_text`. Pins
    reuse over reinvention — a hand-rolled marker added later goes red here."""
    source = inspect.getsource(confluence_fetch)
    assert "truncated" not in source.lower()
    assert "showing the first" not in source.lower()

    body_text = "x" * (confluence_fetch.PAGE_BODY_CHARS + 500)
    out = _dispatch_get_page(monkeypatch, _page_body(body_text))
    assert "characters — truncated" in out


def test_truncated_page_survives_the_outer_result_cap(monkeypatch):
    """AC6 — render a maximal page, then pass it through the SAME outer cap
    connector_lookup.answer applies to every tool result (answer.py:283). The
    inner marker must not itself be clipped away by the outer one."""
    body_text = "x" * 100_000  # far over the cap, a realistic long runbook
    out = _dispatch_get_page(monkeypatch, _page_body(body_text))
    assert "characters — truncated" in out
    outer = confluence_fetch.cap_text(out, limit=DEFAULT_RESULT_CHARS)
    assert outer == out  # comfortably under DEFAULT_RESULT_CHARS; not re-cut
    assert outer.rstrip().endswith("or narrow the query.)")


def test_capped_body_length_is_still_bounded(monkeypatch):
    """AC3 — the fix adds honesty, not payload. The returned `text` field's
    body content stays within PAGE_BODY_CHARS; only the marker sits beyond
    it."""
    monkeypatch.setattr(confluence_fetch, "api_get",
                        lambda *a, **kw: _page_body("x" * (confluence_fetch.PAGE_BODY_CHARS + 4000)))
    page = confluence_fetch.get_page(_handle(), "131189")
    text = page["text"]
    marker_index = text.index("\n\n(showing the first")
    assert marker_index == confluence_fetch.PAGE_BODY_CHARS


def test_get_page_missing_is_stated_not_faked(monkeypatch):
    monkeypatch.setattr(confluence_fetch, "api_get",
                        lambda *a, **kw: {})   # api_get maps 404 → {}
    out = CONFLUENCE.dispatch(_session(), "confluence_get_page", {"page_id": "999"})
    assert "no Confluence page found with id 999" in out


def test_get_page_requires_page_id():
    assert "'page_id' is required" in CONFLUENCE.dispatch(
        _session(), "confluence_get_page", {},
    )


def test_unknown_tool_is_reported():
    assert "unknown tool" in CONFLUENCE.dispatch(_session(), "confluence_nope", {})


# ── Honest-limits copy ───────────────────────────────────────────────────────


def test_system_block_states_the_permission_boundary():
    """Chat must never imply it searched the whole wiki. 3LO reads as the
    connecting user, so an invisible page is not an absent one."""
    block = CONFLUENCE.system_block()
    assert "AS THE PERSON WHO CONNECTED IT" in block
    assert "page restrictions" in block
    assert "READ-ONLY" in block
    # And it must pre-brief the model on the search-unavailable path.
    assert "could not look" in block


def test_not_connected_copy_names_the_settings_path():
    from app.connector_lookup.confluence import NOT_CONNECTED

    assert "Confluence" in NOT_CONNECTED
    assert "Settings" in NOT_CONNECTED
