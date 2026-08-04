"""Marvin live-lookup adapter.

Mirrors tests/test_connector_lookup_confluence.py — no network, no LLM, no DB.
The MCP session is patched in `marvin_fetch`'s own namespace, so nothing here
reaches heymarvin.com.

The load-bearing assertions:
  * Marvin is a REAL lookup provider, not DEFERRED. Naming it in chat used to
    produce "syncs into your knowledge graph, but I can't query it live" —
    accurate, and useless when the study is sitting right there.
  * A capability this connection's server does NOT expose is never reported as
    a search that found nothing. Marvin ships tool subsets per plan, so this is
    a normal state, and conflating it with "no results" is what would let chat
    state that a research team has studied nothing on a topic they have studied
    at length.
  * Every list states the order it came back in. Marvin's tool schema is
    discovered at runtime, so the ordering is not merely undocumented — it is
    unknowable — and #1042 is what happens when a relevance-ordered list is
    handed to a model with nothing saying so.
  * Interview transcripts never reach the model, on this path as on the sync
    path. The live reader is not a loophole around the no-raw-dump contract.
  * A successful live read queues the normal background ingest, so what the
    user just pulled reaches the knowledge graph — and a turn that never opened
    a session queues nothing.
"""
from __future__ import annotations

import json
from unittest.mock import patch

import pytest

from app.connector_lookup import answer as ca
from app.connector_lookup import registry
from app.connector_lookup.marvin import PROVIDER as MARVIN
from app.connectors import marvin_fetch
from app.connectors.mcp_client import McpError

MCP_URL = "https://mcp.heymarvin.com"


# ── Fakes ────────────────────────────────────────────────────────────────────


def _tool(name: str, description: str = "", properties: dict | None = None,
          required: list[str] | None = None) -> dict:
    return {
        "name": name,
        "description": description,
        "inputSchema": {
            "type": "object",
            "properties": properties or {},
            "required": required or [],
        },
    }


DEFAULT_TOOLS = [
    _tool("search_customer_knowledge", "Search interviews, surveys, studies",
          {"query": {"type": "string"}, "limit": {"type": "integer"}}),
    _tool("list_projects", "List all projects", {"limit": {"type": "integer"}}),
    _tool("list_files", "List files in a project",
          {"project_id": {"type": "string"}, "limit": {"type": "integer"}}),
    _tool("get_file_content", "Show file content and summary",
          {"file_id": {"type": "string"}}, required=["file_id"]),
]


class _FakeMcp:
    """Stand-in McpSession driven by a {tool_name: result} script."""

    instances: list["_FakeMcp"] = []

    def __init__(self, url, access_token, *, timeout=None, **_kw):
        self.url = url
        self.access_token = access_token
        self.timeout = timeout
        self.closed = False
        self.calls: list[tuple[str, dict]] = []
        _FakeMcp.instances.append(self)

    # Class-level script, set per test by `_patch_mcp`.
    tools: list[dict] = DEFAULT_TOOLS
    results: dict = {}

    def __enter__(self):
        return self

    def __exit__(self, *_exc):
        self.closed = True
        return None

    def list_tools(self):
        return type(self).tools

    def call_tool(self, name, arguments):
        self.calls.append((name, arguments))
        result = type(self).results.get(name)
        if callable(result):
            return result(arguments)
        if result is None:
            raise McpError(f"no script for {name}")
        return result


@pytest.fixture(autouse=True)
def _reset_fake():
    _FakeMcp.instances = []
    _FakeMcp.tools = DEFAULT_TOOLS
    _FakeMcp.results = {}
    yield
    _FakeMcp.instances = []


@pytest.fixture
def mcp(monkeypatch):
    """Patch the MCP session in marvin_fetch's namespace. Returns the fake."""
    monkeypatch.setattr(marvin_fetch, "McpSession", _FakeMcp)
    return _FakeMcp


def _structured(rows: list[dict]) -> dict:
    return {"structuredContent": rows}


def _handle() -> marvin_fetch.MarvinSession:
    return marvin_fetch.MarvinSession(
        enterprise_id="co-1", access_token="at-1", mcp_url=MCP_URL,
    )


def _session(handle=None) -> ca.LookupSession:
    return ca.LookupSession(provider="marvin", handle=handle or _handle())


def _no_sync(monkeypatch):
    """Record kickoff_sync calls instead of spawning a thread."""
    import app.kg_ingest.auto_sync as auto_sync

    fired: list[tuple[str, str]] = []
    monkeypatch.setattr(
        auto_sync, "kickoff_sync",
        lambda cid, provider: fired.append((cid, provider)) or True,
    )
    return fired


# ── Registry wiring ──────────────────────────────────────────────────────────


def test_marvin_is_a_live_lookup_provider_not_deferred():
    """The regression this adapter exists to prevent: a connected research
    repository chat refuses to read."""
    assert "marvin" in registry.LOOKUP_PROVIDERS
    assert "marvin" not in registry.DEFERRED
    assert "marvin" not in registry.NO_CONNECTOR
    assert registry.provider_for("marvin") is MARVIN
    assert registry.display_name("marvin") == "Marvin"


def test_adapter_satisfies_the_protocol():
    from app.connector_lookup.base import LookupProvider

    assert isinstance(MARVIN, LookupProvider)
    assert MARVIN.provider == "marvin"
    assert {t["name"] for t in MARVIN.tools()} == {
        "marvin_search", "marvin_list_projects",
        "marvin_list_files", "marvin_get_file",
    }


def test_ask_ai_is_not_offered_as_a_tool():
    """Marvin's Ask AI is nondeterministic and bills the customer's own Marvin
    account. Sprntly reads the repository and synthesizes itself."""
    names = " ".join(t["name"] for t in MARVIN.tools())
    assert "ask" not in names
    assert "ask_ai" not in MARVIN.system_block().lower()


# ── open_session ─────────────────────────────────────────────────────────────


def test_open_session_none_when_not_connected(monkeypatch):
    from app import db

    monkeypatch.setattr(db, "get_connection", lambda cid, provider: None)
    assert MARVIN.open_session("co-1") is None


def test_open_session_never_raises_on_a_broken_credential(monkeypatch):
    """A chat answer must degrade to not-connected copy, not a 500 — the
    LookupProvider.open_session contract."""
    from app import db

    monkeypatch.setattr(db, "get_connection",
                        lambda cid, provider: {"token_json_encrypted": "not-fernet"})
    assert MARVIN.open_session("co-1") is None

    def _boom(cid, provider):
        raise RuntimeError("supabase exploded")

    monkeypatch.setattr(db, "get_connection", _boom)
    assert MARVIN.open_session("co-1") is None


def _connected(monkeypatch, token_json: dict):
    """A stored, decryptable Marvin connection for co-1."""
    from app import db
    from app.connectors import tokens

    monkeypatch.setattr(tokens, "decrypt_token_json", lambda blob: blob)
    monkeypatch.setattr(marvin_fetch, "decrypt_token_json", lambda blob: blob)
    monkeypatch.setattr(marvin_fetch, "encrypt_token_json", lambda blob: blob)
    monkeypatch.setattr(
        db, "get_connection",
        lambda cid, provider: {"token_json_encrypted": json.dumps(token_json)},
    )


def test_open_session_carries_the_stored_endpoint_and_token(monkeypatch):
    _connected(monkeypatch, {
        "access_token": "at-live", "mcp_url": MCP_URL, "region": "us",
        "obtained_at": 10**10, "expires_in": 3600,
    })
    session = MARVIN.open_session("co-1")
    assert session is not None
    assert session.handle.access_token == "at-live"
    assert session.handle.mcp_url == MCP_URL
    assert session.handle.enterprise_id == "co-1"


def test_an_expiring_token_is_refreshed_and_persisted(monkeypatch):
    """Marvin access tokens live about an hour, so most turns land on a stale
    one. Persisting matters because Marvin may rotate the refresh token: a
    refresh we do not store strands the connection at the NEXT expiry while
    looking healthy today."""
    from app import db

    _connected(monkeypatch, {
        "access_token": "at-stale", "mcp_url": MCP_URL, "region": "eu",
        "refresh_token": "rt-1", "obtained_at": 0, "expires_in": 3600,
    })
    monkeypatch.setattr(
        marvin_fetch.marvin_oauth, "refresh_access_token",
        lambda rt, *, region: {"access_token": "at-fresh", "expires_in": 3600},
    )
    written: list[tuple] = []
    monkeypatch.setattr(
        db, "update_connection_tokens",
        lambda cid, provider, blob: written.append((cid, provider, blob)),
    )

    session = MARVIN.open_session("co-1")
    assert session.handle.access_token == "at-fresh"
    assert len(written) == 1
    company_id, provider, blob = written[0]
    assert (company_id, provider) == ("co-1", "marvin")
    stored = json.loads(blob)
    assert stored["access_token"] == "at-fresh"
    # The refresh token is carried forward: some servers only issue one once.
    assert stored["refresh_token"] == "rt-1"
    # And the EU deployment survives the refresh, not the US default.
    assert stored["mcp_url"] == "https://mcp-eu.heymarvin.com"


def test_a_fresh_token_is_not_refreshed(monkeypatch):
    import time as _time

    _connected(monkeypatch, {
        "access_token": "at-live", "mcp_url": MCP_URL, "region": "us",
        "refresh_token": "rt-1",
        "obtained_at": int(_time.time()), "expires_in": 3600,
    })

    def _never(*a, **kw):
        raise AssertionError("refreshed a token that had not expired")

    monkeypatch.setattr(marvin_fetch.marvin_oauth, "refresh_access_token", _never)
    assert MARVIN.open_session("co-1").handle.access_token == "at-live"


def test_a_rejected_refresh_becomes_not_connected(monkeypatch):
    """Reconnect copy, not a 500 and not a half-open session."""
    _connected(monkeypatch, {
        "access_token": "at-stale", "mcp_url": MCP_URL, "region": "us",
        "refresh_token": "rt-dead", "obtained_at": 0, "expires_in": 3600,
    })

    def _rejected(rt, *, region):
        raise marvin_fetch.marvin_oauth.MarvinAuthExpiredError("revoked")

    monkeypatch.setattr(
        marvin_fetch.marvin_oauth, "refresh_access_token", _rejected
    )
    assert MARVIN.open_session("co-1") is None


def test_a_failed_persist_does_not_lose_the_lookup(monkeypatch):
    """The token in hand still works for this turn; the write can be retried."""
    from app import db

    _connected(monkeypatch, {
        "access_token": "at-stale", "mcp_url": MCP_URL, "region": "us",
        "refresh_token": "rt-1", "obtained_at": 0, "expires_in": 3600,
    })
    monkeypatch.setattr(
        marvin_fetch.marvin_oauth, "refresh_access_token",
        lambda rt, *, region: {"access_token": "at-fresh", "expires_in": 3600},
    )

    def _boom(*a, **kw):
        raise RuntimeError("supabase write failed")

    monkeypatch.setattr(db, "update_connection_tokens", _boom)
    assert MARVIN.open_session("co-1").handle.access_token == "at-fresh"


def test_the_handle_never_appears_in_a_session_repr():
    """A LookupSession repr reaches log lines, exception context and test
    failure dumps — the bearer token must not ride along."""
    handle = _handle()
    assert "at-1" not in repr(handle)
    assert "at-1" not in repr(_session(handle))


# ── Reads ────────────────────────────────────────────────────────────────────


def test_search_renders_hits_with_their_analysis_excerpt(mcp):
    mcp.results = {"search_customer_knowledge": _structured([{
        "id": "f1", "name": "Interview — Dana",
        "summary": "Dana could not find where to invite teammates.",
        "transcript": "DANA: uhh, so…" * 400,
        "updated_at": "2026-07-30", "project": "Onboarding research",
        "url": "https://app.heymarvin.com/f/1",
    }])}
    out = MARVIN.dispatch(_session(), "marvin_search", {"query": "onboarding"})

    assert "Interview — Dana" in out
    assert "invite teammates" in out
    assert "2026-07-30" in out
    assert "project Onboarding research" in out
    assert "https://app.heymarvin.com/f/1" in out
    # THE contract: the transcript is not read, here either.
    assert "DANA:" not in out


def test_search_resolves_the_query_parameter_from_the_tools_own_schema(mcp):
    """Marvin publishes no schema, so the argument NAME is discovered. A tool
    calling it `q` must still be searchable."""
    mcp.tools = [_tool("search_knowledge", "Search", {"q": {"type": "string"}})]
    mcp.results = {"search_knowledge": _structured([])}
    MARVIN.dispatch(_session(), "marvin_search", {"query": "pricing"})
    assert _FakeMcp.instances[-1].calls[-1][1]["q"] == "pricing"


def test_search_requires_a_query():
    assert "'query' is required" in MARVIN.dispatch(_session(), "marvin_search", {})


def test_an_empty_search_says_it_searched_this_accounts_visible_research(mcp):
    mcp.results = {"search_customer_knowledge": _structured([])}
    out = MARVIN.dispatch(_session(), "marvin_search", {"query": "nothing"})
    assert "no Marvin research matched" in out
    assert "THIS account's visible research" in out


def test_list_projects_renders_each_studys_description(mcp):
    mcp.results = {"list_projects": _structured([
        {"id": "p1", "name": "Onboarding research",
         "description": "Why do new teams stall?"},
    ])}
    out = MARVIN.dispatch(_session(), "marvin_list_projects", {})
    assert "p1: Onboarding research" in out
    assert "Why do new teams stall?" in out


def test_list_files_scopes_to_a_project(mcp):
    mcp.results = {"list_files": _structured(
        [{"id": "f1", "name": "Study", "summary": "a finding"}]
    )}
    out = MARVIN.dispatch(
        _session(), "marvin_list_files", {"project_id": "p1"},
    )
    assert _FakeMcp.instances[-1].calls[-1][1]["project_id"] == "p1"
    assert "in project p1" in out


def test_a_listing_tool_with_no_project_scope_says_the_read_was_wider(mcp):
    """An optional argument the tool cannot take must not silently narrow
    nothing — the model has to know the answer covers more than was asked."""
    mcp.tools = [_tool("list_files", "List files", {"limit": {"type": "integer"}})]
    mcp.results = {"list_files": _structured(
        [{"id": "f1", "name": "Study", "summary": "a finding"}]
    )}
    out = MARVIN.dispatch(_session(), "marvin_list_files", {"project_id": "p1"})
    assert "accepts no project_id argument" in out
    assert "wider than asked" in out


def test_get_file_returns_the_analysis_layer_not_the_transcript(mcp):
    mcp.results = {"get_file_content": _structured([{
        "id": "f1", "title": "Interview — Dana",
        "summary": "Dana could not find where to invite teammates.",
        "key_points": ["Setup felt long"],
        "transcript": "DANA: so I clicked…" * 400,
        "url": "https://app.heymarvin.com/f/1",
    }])}
    out = MARVIN.dispatch(_session(), "marvin_get_file", {"file_id": "f1"})

    assert "Interview — Dana" in out
    assert "invite teammates" in out
    assert "- Setup felt long" in out
    assert "url: https://app.heymarvin.com/f/1" in out
    assert "DANA:" not in out


def test_get_file_falls_back_to_prose_when_the_server_returns_text(mcp):
    mcp.results = {"get_file_content": {
        "content": [{"type": "text", "text": "Summary: pricing confusion."}],
    }}
    out = MARVIN.dispatch(_session(), "marvin_get_file", {"file_id": "f1"})
    assert "Summary: pricing confusion." in out


def test_get_file_with_no_analysis_says_so_rather_than_implying_emptiness(mcp):
    mcp.results = {"get_file_content": _structured(
        [{"id": "f1", "title": "Raw interview"}]
    )}
    out = MARVIN.dispatch(_session(), "marvin_get_file", {"file_id": "f1"})
    assert "no summary, key points or other analysis layer" in out
    assert "transcript itself is deliberately not read" in out


def test_get_file_requires_a_file_id():
    assert "'file_id' is required" in MARVIN.dispatch(
        _session(), "marvin_get_file", {},
    )


def test_unknown_tool_is_reported():
    assert "unknown tool" in MARVIN.dispatch(_session(), "marvin_nope", {})


# ── Caps ─────────────────────────────────────────────────────────────────────


def test_results_are_capped_with_an_honest_marker(mcp):
    mcp.results = {"search_customer_knowledge": _structured([
        {"id": f"f{i}", "name": f"Study {i}", "summary": "finding"}
        for i in range(40)
    ])}
    out = MARVIN.dispatch(_session(), "marvin_search", {"query": "x"})
    assert f"showing {marvin_fetch.RESULT_LIMIT} of 40 matches" in out
    assert "Study 39" not in out


def test_one_files_analysis_is_capped(mcp):
    mcp.results = {"get_file_content": _structured(
        [{"id": "f1", "title": "Long study", "summary": "x" * 99_999}]
    )}
    out = MARVIN.dispatch(_session(), "marvin_get_file", {"file_id": "f1"})
    assert "truncated" in out
    assert len(out) < 99_999


# ── Ordering (#1042) ─────────────────────────────────────────────────────────


def test_a_newest_first_option_is_requested_and_rendered(mcp):
    """When the tool declares a sort enum, ask for recency AND say we did."""
    mcp.tools = [_tool("search_knowledge", "Search", {
        "query": {"type": "string"},
        "sort": {"type": "string", "enum": ["relevance", "most_recent"]},
    })]
    mcp.results = {"search_knowledge": _structured(
        [{"id": "f1", "name": "S", "summary": "finding"}]
    )}
    out = MARVIN.dispatch(_session(), "marvin_search", {"query": "x"})
    assert _FakeMcp.instances[-1].calls[-1][1]["sort"] == "most_recent"
    assert "ordered NEWEST FIRST" in out
    assert "most_recent" in out


def test_an_unknown_ordering_is_stated_rather_than_assumed(mcp):
    """The #1042 bug in its Marvin form. The schema is discovered at runtime, so
    a tool with no sort argument has an ordering we cannot know — and a model
    handed an unlabelled list will call it "the latest"."""
    mcp.results = {"search_customer_knowledge": _structured(
        [{"id": "f1", "name": "S", "summary": "finding"}]
    )}
    out = MARVIN.dispatch(_session(), "marvin_search", {"query": "x"})
    assert "ordering NOT SPECIFIED" in out
    assert 'Do NOT describe them as "the latest"' in out


def test_a_sort_value_is_never_invented(mcp):
    """A sort parameter with no declared enum gets nothing: Marvin publishes no
    schema, so a guessed value is a 400 at best and a silently different
    ordering at worst."""
    mcp.tools = [_tool("search_knowledge", "Search", {
        "query": {"type": "string"}, "sort": {"type": "string"},
    })]
    mcp.results = {"search_knowledge": _structured([])}
    out = MARVIN.dispatch(_session(), "marvin_search", {"query": "x"})
    assert "sort" not in _FakeMcp.instances[-1].calls[-1][1]
    assert "ordering NOT SPECIFIED" in out


# ── Missing capabilities ─────────────────────────────────────────────────────


def test_a_capability_this_server_lacks_is_not_a_no_results_answer(mcp):
    """THE assertion. Marvin ships tool subsets per plan, so a workspace whose
    server exposes no search is normal — and reporting that as "found nothing"
    would have chat state that a research team has studied nothing on a topic
    they have studied at length."""
    mcp.tools = [_tool("list_projects", "List all projects")]
    mcp.results = {"list_projects": _structured([])}
    out = MARVIN.dispatch(_session(), "marvin_search", {"query": "pricing"})

    assert "UNAVAILABLE" in out
    assert "NOT a no-results answer" in out
    assert "could not look" in out
    assert "no Marvin research matched" not in out


def test_a_tool_whose_required_argument_we_cannot_fill_is_skipped(mcp):
    """Calling blind buys a guaranteed 400 and tells the model nothing."""
    mcp.tools = [_tool("get_file_content", "Show file content",
                       {"file_id": {"type": "string"},
                        "workspace": {"type": "string"}},
                       required=["file_id", "workspace"])]
    out = MARVIN.dispatch(_session(), "marvin_get_file", {"file_id": "f1"})
    assert "UNAVAILABLE" in out
    assert "requires workspace" in out
    assert not _FakeMcp.instances[-1].calls, "called a tool that would 400"


def test_a_tool_with_nowhere_to_put_a_required_argument_is_skipped(mcp):
    """The other half: the id has no parameter to go in at all, so the request
    cannot even be expressed."""
    mcp.tools = [_tool("get_file_content", "Show file content",
                       {"workspace": {"type": "string"}})]
    out = MARVIN.dispatch(_session(), "marvin_get_file", {"file_id": "f1"})
    assert "UNAVAILABLE" in out
    assert "accepts no file_id argument" in out


# ── MCP session hygiene ──────────────────────────────────────────────────────


def test_every_read_closes_its_mcp_session(mcp):
    """LookupSession has no teardown hook, so an MCP conversation held open for
    the turn would leak a server-side session per question."""
    mcp.results = {"list_projects": _structured([{"id": "p1", "name": "A"}])}
    MARVIN.dispatch(_session(), "marvin_list_projects", {})
    assert _FakeMcp.instances and all(i.closed for i in _FakeMcp.instances)


def test_capabilities_are_resolved_once_per_turn(mcp):
    """`tools/list` is paid once, not once per tool call."""
    mcp.results = {
        "list_projects": _structured([{"id": "p1", "name": "A"}]),
        "list_files": _structured([{"id": "f1", "name": "S", "summary": "x"}]),
    }
    session = _session()
    MARVIN.dispatch(session, "marvin_list_projects", {})
    caps = session.handle.caps
    MARVIN.dispatch(session, "marvin_list_files", {})
    assert session.handle.caps is caps
    assert set(caps) >= {"list_projects", "list_files", "search", "get_file"}


def test_the_framework_timeout_bounds_every_call(mcp):
    from app.connector_lookup.base import HTTP_TIMEOUT

    mcp.results = {"list_projects": _structured([])}
    MARVIN.dispatch(_session(), "marvin_list_projects", {})
    assert _FakeMcp.instances[-1].timeout == HTTP_TIMEOUT


# ── Knowledge-graph write-back ───────────────────────────────────────────────


def test_a_successful_lookup_queues_the_normal_background_ingest(mcp, monkeypatch):
    """What the user just pulled out of Marvin is exactly what the KG should
    hold. It goes through `kickoff_sync` — the same ledger-deduped path a fresh
    connection uses — rather than being extracted inline, which would add
    minutes to a chat turn."""
    fired = _no_sync(monkeypatch)
    mcp.results = {"list_projects": _structured([{"id": "p1", "name": "A"}])}
    MARVIN.dispatch(_session(), "marvin_list_projects", {})
    assert fired == [("co-1", "marvin")]


def test_the_ingest_is_queued_once_per_turn_not_once_per_tool_call(mcp, monkeypatch):
    fired = _no_sync(monkeypatch)
    mcp.results = {
        "list_projects": _structured([{"id": "p1", "name": "A"}]),
        "list_files": _structured([{"id": "f1", "name": "S", "summary": "x"}]),
        "search_customer_knowledge": _structured([]),
    }
    session = _session()
    MARVIN.dispatch(session, "marvin_list_projects", {})
    MARVIN.dispatch(session, "marvin_list_files", {})
    MARVIN.dispatch(session, "marvin_search", {"query": "x"})
    assert fired == [("co-1", "marvin")]


def test_nothing_is_queued_when_the_session_never_opened(monkeypatch):
    """No session, no read, nothing new to ingest — and no background work
    kicked off for a company whose connection is dead."""
    from app import db

    fired = _no_sync(monkeypatch)
    monkeypatch.setattr(db, "get_connection", lambda cid, provider: None)
    assert MARVIN.open_session("co-1") is None
    assert fired == []


def test_nothing_is_queued_when_the_read_failed(mcp, monkeypatch):
    fired = _no_sync(monkeypatch)
    mcp.results = {}          # every call raises McpError
    with pytest.raises(McpError):
        MARVIN.dispatch(_session(), "marvin_list_projects", {})
    assert fired == []


def test_nothing_is_queued_for_an_unknown_tool(mcp, monkeypatch):
    fired = _no_sync(monkeypatch)
    MARVIN.dispatch(_session(), "marvin_nope", {})
    assert fired == []


def test_a_broken_sync_kickoff_never_breaks_the_answer(mcp, monkeypatch):
    import app.kg_ingest.auto_sync as auto_sync

    def _boom(*a, **kw):
        raise RuntimeError("thread pool exhausted")

    monkeypatch.setattr(auto_sync, "kickoff_sync", _boom)
    mcp.results = {"list_projects": _structured([{"id": "p1", "name": "A"}])}
    out = MARVIN.dispatch(_session(), "marvin_list_projects", {})
    assert "p1: A" in out


# ── Honest-limits copy ───────────────────────────────────────────────────────


def test_system_block_states_the_permission_boundary():
    """MCP reads as the connecting user, so research that account cannot see is
    invisible, not absent — and those are different answers."""
    block = MARVIN.system_block()
    assert "AS THE PERSON WHO CONNECTED IT" in block
    assert "Permission-invisible research is not missing research" in block


def test_system_block_states_that_transcripts_are_not_read():
    block = MARVIN.system_block()
    assert "INTERVIEW TRANSCRIPTS ARE NOT READ" in block
    assert "analysis layer" in block
    assert "verbatim" in block


def test_system_block_states_the_connection_is_read_only():
    block = MARVIN.system_block()
    assert "READ-ONLY" in block
    assert "mcp:read" in block


def test_system_block_pre_briefs_the_model_on_ordering_and_unavailability():
    block = MARVIN.system_block()
    assert "ordering is not specified" in block
    assert "UNAVAILABLE" in block
    assert "could not look" in block


def test_not_connected_copy_names_the_settings_path():
    from app.connector_lookup.marvin import NOT_CONNECTED

    assert "Marvin" in NOT_CONNECTED
    assert "Settings" in NOT_CONNECTED
