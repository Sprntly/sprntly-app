"""Slack lookup — token resolution, search-mode honesty, reads, failure copy.

Mirrors tests/test_jira_lookup.py: no network/LLM/DB. The Slack fetchers and the
token store are patched in the slack adapter's namespace.

The load-bearing behaviour here is HONESTY, not coverage: without a user token
there is no message search, and a bot token only sees channels the bot is in. A
lookup that quietly narrowed either would produce a confident "nobody said that"
about a workspace it could barely read.
"""
from __future__ import annotations

import json

import requests
from fastapi import HTTPException

from app.connector_lookup import answer as ca
from app.connector_lookup import slack as sl


def _handle(user_token=None):
    handle = sl.SlackHandle(bot_token="xoxb-1", user_token=user_token)
    handle.users = {"U1": "ada", "U2": "grace"}
    handle._users_loaded = True
    handle.channels = [
        {"id": "C1", "name": "general", "is_private": False},
        {"id": "C2", "name": "product-eng", "is_private": True},
    ]
    handle._channels_loaded = True
    return handle


def _session(user_token=None):
    return ca.LookupSession(provider="slack", handle=_handle(user_token))


def _tokens(monkeypatch, rows):
    """Patch the two company-scoped connection reads the adapter uses."""
    from app import db

    seen: list[tuple] = []

    def list_slack(company_id):
        seen.append(("list_slack_connections", company_id))
        return rows

    def get_connection(company_id, provider):
        seen.append(("get_connection", company_id, provider))
        return None

    monkeypatch.setattr(db, "list_slack_connections", list_slack)
    monkeypatch.setattr(db, "get_connection", get_connection)
    return seen


def _row(bot="xoxb-1", user=None):
    payload = {"access_token": bot}
    if user:
        payload["user_access_token"] = user
    return {"token_json_encrypted": json.dumps(payload)}


# ── session + tenancy ────────────────────────────────────────────────────────

def test_tokens_are_read_for_the_authenticated_company_only(monkeypatch):
    """Cross-tenant isolation: every credential read is keyed by the company the
    request authenticated as."""
    seen = _tokens(monkeypatch, [_row()])
    monkeypatch.setattr(sl, "decrypt_token_json", lambda enc: enc)
    bot, user = sl._load_tokens("co-a")
    assert bot == "xoxb-1" and user is None
    assert seen == [("list_slack_connections", "co-a")]


def test_falls_back_to_the_company_scoped_row_for_legacy_installs(monkeypatch):
    from app import db

    monkeypatch.setattr(db, "list_slack_connections", lambda cid: [])
    monkeypatch.setattr(db, "get_connection", lambda cid, prov: _row("xoxb-legacy"))
    monkeypatch.setattr(sl, "decrypt_token_json", lambda enc: enc)
    assert sl._load_tokens("co-a") == ("xoxb-legacy", None)


def test_prefers_the_row_that_can_search(monkeypatch):
    """A company can hold several per-user Slack rows; the one carrying a user
    token is the only one that can search, so it wins."""
    _tokens(monkeypatch, [_row("xoxb-a"), _row("xoxb-b", user="xoxp-b")])
    monkeypatch.setattr(sl, "decrypt_token_json", lambda enc: enc)
    assert sl._load_tokens("co-a") == ("xoxb-b", "xoxp-b")


def test_undecryptable_row_is_skipped_not_fatal(monkeypatch):
    from app.connectors.tokens import TokenEncryptionError

    _tokens(monkeypatch, [{"token_json_encrypted": "bad"}, _row("xoxb-ok")])

    def decrypt(enc):
        if enc == "bad":
            raise TokenEncryptionError("bad key")
        return enc

    monkeypatch.setattr(sl, "decrypt_token_json", decrypt)
    assert sl._load_tokens("co-a") == ("xoxb-ok", None)


def test_open_session_none_when_not_connected(monkeypatch):
    _tokens(monkeypatch, [])
    assert sl.PROVIDER.open_session("co-a") is None


def test_open_session_records_the_search_mode(monkeypatch):
    _tokens(monkeypatch, [_row(user="xoxp-1")])
    monkeypatch.setattr(sl, "decrypt_token_json", lambda enc: enc)
    session = sl.PROVIDER.open_session("co-a")
    assert "search IS available" in session.notes[0]

    _tokens(monkeypatch, [_row()])
    session = sl.PROVIDER.open_session("co-a")
    assert "NO user token" in session.notes[0]


def test_not_connected_never_calls_the_tool_loop(monkeypatch):
    """Case 1, on the Slack path: connect copy, and the model is never asked."""
    _tokens(monkeypatch, [])
    from app import db

    monkeypatch.setattr(db, "list_connections", lambda cid: [{"provider": "jira"}])
    called = []
    out = ca.answer(enterprise_id="co-a", question="check slack for the pricing thread",
                    providers=[sl.PROVIDER], run_loop=lambda **k: called.append(k),
                    log=lambda *a: None)
    assert called == []
    assert "Slack isn't connected yet" in out["answer"]
    assert "Connected right now: Jira." in out["answer"]


# ── search-mode honesty ──────────────────────────────────────────────────────

def test_search_without_a_user_token_says_so_and_points_at_channel_reads():
    """The single most important behaviour on this adapter: it does NOT downgrade
    silently. Slack search needs a user token; without one the tool says what it
    can't do and what to do instead."""
    out = sl.PROVIDER.dispatch(_session(), "slack_search_messages", {"query": "pricing"})
    assert "unavailable for this workspace" in out
    assert "Do NOT report this as 'nothing found'" in out
    assert "slack_channel_history" in out


def test_search_with_a_user_token_renders_matches(monkeypatch):
    captured = {}

    def fake_search(token, *, query, count=20, page=1):
        captured.update({"token": token, "query": query, "count": count})
        return {"matches": [
            {"channel": {"name": "general"}, "ts": "1750000000.1",
             "user": "U1", "text": "we ship pricing v2 <@U2>"},
        ], "total": 1}

    monkeypatch.setattr(sl.slack_oauth, "search_messages", fake_search)
    out = sl.PROVIDER.dispatch(
        _session(user_token="xoxp-1"), "slack_search_messages", {"query": "pricing"}
    )
    assert captured["token"] == "xoxp-1"
    assert "#general" in out and "ada:" in out
    assert "@grace" in out          # user ids resolved to names
    assert "ts=1750000000.1" in out  # so a thread can be followed


def test_search_reports_when_slack_has_more_matches_than_shown(monkeypatch):
    monkeypatch.setattr(sl.slack_oauth, "search_messages", lambda *a, **k: {
        "matches": [{"channel": {"name": "g"}, "ts": "1.0", "user": "U1", "text": "x"}],
        "total": 240,
    })
    out = sl.PROVIDER.dispatch(_session("xoxp-1"), "slack_search_messages", {"query": "x"})
    assert "showing 1 of 240 matches" in out


def test_huge_search_result_is_capped_with_a_marker(monkeypatch):
    """Case 4 on the Slack path: 500 matches → capped, and the cap is stated."""
    monkeypatch.setattr(sl.slack_oauth, "search_messages", lambda *a, **k: {
        "matches": [
            {"channel": {"name": "g"}, "ts": f"{i}.0", "user": "U1", "text": f"m{i}"}
            for i in range(500)
        ],
        "total": 500,
    })
    out = sl.PROVIDER.dispatch(_session("xoxp-1"), "slack_search_messages", {"query": "x"})
    assert out.count("\n") <= sl._MAX_SEARCH_HITS + 1
    assert "showing 20 of 500 matches" in out


def test_empty_search_is_honest(monkeypatch):
    monkeypatch.setattr(sl.slack_oauth, "search_messages",
                        lambda *a, **k: {"matches": [], "total": 0})
    out = sl.PROVIDER.dispatch(_session("xoxp-1"), "slack_search_messages", {"query": "zzz"})
    assert out == "(no Slack messages match 'zzz')"


# ── channel reads ────────────────────────────────────────────────────────────

def test_list_channels_renders_ids_and_privacy():
    out = sl.PROVIDER.dispatch(_session(), "slack_list_channels", {})
    assert "#general (id=C1)" in out
    assert "#product-eng (id=C2, private)" in out


def test_list_channels_when_the_bot_is_in_none(monkeypatch):
    handle = sl.SlackHandle(bot_token="xoxb-1")
    handle._channels_loaded = True
    out = sl.PROVIDER.dispatch(
        ca.LookupSession(provider="slack", handle=handle), "slack_list_channels", {}
    )
    assert "no Slack channels are readable" in out
    assert "rather than concluding nothing was discussed" in out


def test_channel_history_resolves_a_name_to_an_id_and_windows_the_read(monkeypatch):
    captured = {}

    def fake_history(token, *, channel, limit=100, oldest=None, latest=None, cursor=None):
        captured.update({"channel": channel, "limit": limit, "oldest": oldest})
        return {"messages": [
            {"ts": "1750000200.0", "user": "U2", "text": "and shipped"},
            {"ts": "1750000100.0", "user": "U1", "text": "we agreed on v2",
             "reply_count": 3},
        ], "has_more": False}

    monkeypatch.setattr(sl.slack_oauth, "fetch_conversation_history", fake_history)
    out = sl.PROVIDER.dispatch(
        _session(), "slack_channel_history", {"channel": "#general", "days": 3}
    )
    assert captured["channel"] == "C1"
    assert captured["limit"] == sl._MAX_MESSAGES
    assert captured["oldest"] is not None
    # Oldest-first, with authors, thread pointers and the window stated.
    assert out.index("we agreed on v2") < out.index("and shipped")
    assert "thread: 3 replies, thread_ts=1750000100.0" in out
    assert "#general — last 3 days (2 messages)" in out


def test_channel_history_requires_a_channel():
    out = sl.PROVIDER.dispatch(_session(), "slack_channel_history", {})
    assert out == "(slack_channel_history: 'channel' is required)"


def test_channel_history_empty_names_the_visibility_caveat(monkeypatch):
    monkeypatch.setattr(sl.slack_oauth, "fetch_conversation_history",
                        lambda *a, **k: {"messages": [], "has_more": False})
    out = sl.PROVIDER.dispatch(_session(), "slack_channel_history", {"channel": "general"})
    assert "or the bot isn't in that channel" in out


def test_channel_history_days_are_clamped(monkeypatch):
    captured = {}
    monkeypatch.setattr(sl.slack_oauth, "fetch_conversation_history",
                        lambda t, **k: captured.update(k) or {"messages": []})
    sl.PROVIDER.dispatch(_session(), "slack_channel_history",
                         {"channel": "general", "days": 9999})
    first = float(captured["oldest"].split(".")[0])
    sl.PROVIDER.dispatch(_session(), "slack_channel_history",
                         {"channel": "general", "days": "junk"})
    second = float(captured["oldest"].split(".")[0])
    assert second > first  # 9999 days clamped to 90; junk fell back to the default


def test_get_thread_reads_replies(monkeypatch):
    captured = {}

    def fake_replies(token, channel_id, thread_ts, limit=50, timeout=30):
        captured.update({"channel": channel_id, "ts": thread_ts, "timeout": timeout})
        return [{"ts": "1750000101.0", "user": "U2", "text": "agreed"}]

    monkeypatch.setattr(sl.slack_sync, "fetch_thread_replies", fake_replies)
    out = sl.PROVIDER.dispatch(
        _session(), "slack_get_thread", {"channel": "general", "thread_ts": "1750000100.0"}
    )
    assert captured == {"channel": "C1", "ts": "1750000100.0", "timeout": 15}
    assert "grace: agreed" in out


def test_get_thread_requires_both_args():
    out = sl.PROVIDER.dispatch(_session(), "slack_get_thread", {"channel": "general"})
    assert "'channel' and 'thread_ts' are required" in out


def test_unknown_channel_reference_is_passed_through():
    """A raw id, or a private channel the list call missed: let Slack decide
    rather than refusing locally."""
    assert _handle().resolve_channel("C999") == "C999"
    assert _handle().resolve_channel("#general") == "C1"
    assert _handle().resolve_channel("") is None


# ── failure copy (cases 2, 5, 7) ─────────────────────────────────────────────

def test_timeout_becomes_an_honest_tool_result(monkeypatch):
    def boom(*a, **k):
        raise requests.Timeout("read timed out")

    monkeypatch.setattr(sl.slack_oauth, "fetch_conversation_history", boom)
    out = sl.PROVIDER.dispatch(_session(), "slack_channel_history", {"channel": "general"})
    assert out == "(Slack timed out on slack_channel_history — no results from this call)"


def test_rate_limit_says_results_may_be_partial(monkeypatch):
    def boom(*a, **k):
        raise HTTPException(400, "Slack rejected the history read: ratelimited")

    monkeypatch.setattr(sl.slack_oauth, "fetch_conversation_history", boom)
    out = sl.PROVIDER.dispatch(_session(), "slack_channel_history", {"channel": "general"})
    assert "rate-limited" in out and "may be incomplete" in out
    assert "not retrying in a loop" in out or "rather than retrying in a loop" in out


def test_revoked_token_says_reconnect_and_leaves_the_row_alone(monkeypatch):
    """Case 2: a mid-loop auth failure is readable, tells the user to reconnect,
    and never touches the stored connection (no write path exists here)."""
    from app import db

    def boom(*a, **k):
        raise HTTPException(400, "Slack rejected the search: invalid_auth")

    monkeypatch.setattr(sl.slack_oauth, "search_messages", boom)
    writes = []
    monkeypatch.setattr(db, "update_connection_tokens",
                        lambda *a, **k: writes.append(a), raising=False)
    monkeypatch.setattr(db, "delete_connection", lambda *a, **k: writes.append(a), raising=False)
    out = sl.PROVIDER.dispatch(_session("xoxp-1"), "slack_search_messages", {"query": "x"})
    assert "needs reconnecting" in out and "do not retry" in out.lower()
    assert writes == []


def test_missing_channel_points_at_the_channel_list(monkeypatch):
    def boom(*a, **k):
        raise HTTPException(400, "Slack rejected the history read: channel_not_found")

    monkeypatch.setattr(sl.slack_oauth, "fetch_conversation_history", boom)
    out = sl.PROVIDER.dispatch(_session(), "slack_channel_history", {"channel": "nope"})
    assert "isn't readable" in out and "slack_list_channels" in out


def test_connection_error_is_reported_without_a_traceback(monkeypatch):
    def boom(*a, **k):
        raise requests.ConnectionError("dns")

    monkeypatch.setattr(sl.slack_oauth, "fetch_conversation_history", boom)
    out = sl.PROVIDER.dispatch(_session(), "slack_channel_history", {"channel": "general"})
    assert out.startswith("(Slack slack_channel_history failed to reach Slack:")


def test_unknown_tool_is_rejected():
    assert "unknown tool" in sl.PROVIDER.dispatch(_session(), "slack_delete_all", {})


def test_directory_fetches_use_the_framework_timeout(monkeypatch):
    """The corpus sync's fetchers are reused, with the tighter chat bound passed
    in — a chat answer must not wait 30s per Slack call."""
    seen = {}
    monkeypatch.setattr(sl.slack_sync, "fetch_users",
                        lambda token, timeout=30: seen.setdefault("users", timeout) or {})
    monkeypatch.setattr(sl.slack_sync, "fetch_channels",
                        lambda token, limit=50, timeout=30: seen.setdefault("channels", timeout) or [])
    handle = sl.SlackHandle(bot_token="xoxb-1")
    handle.user_map()
    handle.channel_list()
    assert seen == {"users": 15, "channels": 15}


def test_directory_fetch_failure_is_non_fatal(monkeypatch):
    monkeypatch.setattr(sl.slack_sync, "fetch_users",
                        lambda *a, **k: (_ for _ in ()).throw(RuntimeError("boom")))
    monkeypatch.setattr(sl.slack_sync, "fetch_channels",
                        lambda *a, **k: (_ for _ in ()).throw(RuntimeError("boom")))
    handle = sl.SlackHandle(bot_token="xoxb-1")
    assert handle.user_map() == {}
    assert handle.channel_list() == []


def test_system_block_states_the_visibility_and_write_limits():
    block = sl.PROVIDER.system_block()
    assert "channels the Sprntly bot was added to" in block
    assert "NEVER \"it was never said\"" in block
    assert "READ-ONLY" in block
