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

import pytest

from app.connector_lookup import answer as ca
from app.connector_lookup import registry
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
