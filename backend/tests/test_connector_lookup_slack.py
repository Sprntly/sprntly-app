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

import pytest
import requests
from fastapi import HTTPException

from app.connector_lookup import answer as ca
from app.connector_lookup import slack as sl


def _handle(user_token=None, workspace=None):
    """A warm handle: users and BOTH channel views preloaded.

    `channels` is the bot's MEMBERSHIP (what slack_sync.fetch_channels returns);
    `workspace_channels` is every channel conversations.list can see, member or
    not. They are deliberately different sets here — the membership list is what
    gates search privacy, the workspace list is what turns a name into an id,
    and conflating them is the bug the resolver tests below pin.

    Both are marked loaded so no test can reach the network by accident.
    """
    handle = sl.SlackHandle(bot_token="xoxb-1", user_token=user_token)
    handle.users = {"U1": "ada", "U2": "grace"}
    handle._users_loaded = True
    handle.channels = [
        {"id": "C1", "name": "general", "is_private": False},
        {"id": "C2", "name": "product-eng", "is_private": True},
    ]
    handle._channels_loaded = True
    handle.workspace_channels = workspace if workspace is not None else [
        {"id": "C1", "name": "general", "is_private": False, "is_member": True},
        {"id": "C2", "name": "product-eng", "is_private": True, "is_member": True},
        # The bot has NEVER been invited to these two — the whole point.
        {"id": "C3", "name": "launch-room", "is_private": False, "is_member": False},
        {"id": "G4", "name": "founders", "is_private": True, "is_member": False},
    ]
    handle._workspace_loaded = True
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
    assert "keyword search is available" in session.notes[0]

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


# ── search privacy gate (user-token search must not leak DMs) ────────────────

def _match(channel_id="C1", name="general", text="pricing v2", **channel_flags):
    channel = {"id": channel_id, "name": name}
    channel.update(channel_flags)
    return {"channel": channel, "ts": "1750000000.1", "user": "U1", "text": text}


def test_shareable_match_gate():
    """Unit-level truth table. search.messages reads as the authorizing USER, so
    the gate is "could the bot have read this too" — anything else would quote one
    employee's private messages into another's answer."""
    bot_channels = {"C1", "G-bot-is-in"}
    # Public channel → shareable.
    assert sl.is_shareable_match(_match("C1"), bot_channels)
    assert sl.is_shareable_match(_match("C-not-joined"), bot_channels)
    # DM by id, and by flag.
    assert not sl.is_shareable_match(_match("D123", name="ada"), bot_channels)
    assert not sl.is_shareable_match(_match("C9", is_im=True), bot_channels)
    # Group DM.
    assert not sl.is_shareable_match(_match("C9", is_mpim=True), bot_channels)
    # Private: only when the bot is a member.
    assert not sl.is_shareable_match(_match("G-secret", is_private=True), bot_channels)
    assert sl.is_shareable_match(_match("G-bot-is-in", is_private=True), bot_channels)
    assert not sl.is_shareable_match(_match("C9", is_private=True), bot_channels)


def test_search_drops_dm_and_private_matches_before_the_model_sees_them(monkeypatch):
    """THE privacy test: a user-token search returns the authorizing user's DMs and
    private channels; none of it may reach the model, and the result must disclose
    the scope it actually covered."""
    monkeypatch.setattr(sl.slack_oauth, "search_messages", lambda *a, **k: {
        "matches": [
            _match("D555", name="ada", text="SECRET dm about salary"),
            _match("G777", name="founders", text="SECRET private channel plan",
                   is_private=True),
            _match("C9", name="mpdm-ada--bo", text="SECRET group dm", is_mpim=True),
            _match("C1", name="general", text="public pricing chatter"),
        ],
        "total": 4,
    })
    out = sl.PROVIDER.dispatch(_session("xoxp-1"), "slack_search_messages",
                               {"query": "pricing"})
    assert "SECRET" not in out
    assert "public pricing chatter" in out
    assert "#general" in out and "#founders" not in out
    # …and it says what it covered.
    assert "searched PUBLIC / bot-readable Slack channels only" in out
    assert "3 further match(es) were in DMs or private channels" in out


def test_search_keeps_a_private_channel_the_bot_is_in(monkeypatch):
    """A private channel the Sprntly bot was added to is already readable by any
    teammate's lookup, so a search hit there is not a leak."""
    monkeypatch.setattr(sl.slack_oauth, "search_messages", lambda *a, **k: {
        "matches": [_match("C2", name="product-eng", text="ship friday",
                           is_private=True)],
        "total": 1,
    })
    out = sl.PROVIDER.dispatch(_session("xoxp-1"), "slack_search_messages",
                               {"query": "ship"})
    assert "ship friday" in out and "#product-eng" in out


def test_search_with_only_private_matches_reports_nothing_shareable(monkeypatch):
    monkeypatch.setattr(sl.slack_oauth, "search_messages", lambda *a, **k: {
        "matches": [_match("D1", text="SECRET"), _match("D2", text="SECRET")],
        "total": 2,
    })
    out = sl.PROVIDER.dispatch(_session("xoxp-1"), "slack_search_messages",
                               {"query": "x"})
    assert "SECRET" not in out
    assert "2 match(es) were in DMs or private channels and were excluded" in out
    assert "never imply you read anyone's DMs" in out


def test_search_mode_note_discloses_the_filtering(monkeypatch):
    """The system block must carry the mode, not just the tool result."""
    _tokens(monkeypatch, [_row(user="xoxp-1")])
    monkeypatch.setattr(sl, "decrypt_token_json", lambda enc: enc)
    note = sl.PROVIDER.open_session("co-a").notes[0]
    assert "FILTERED to public / bot-readable channels" in note
    assert "searched public channels" in note


def test_system_block_forbids_claiming_a_private_search():
    block = sl.PROVIDER.system_block()
    assert "DMs, group DMs and private channels the bot isn't in are NEVER readable" in block
    assert "never that you searched someone's private messages" in block


def test_tokens_are_not_in_the_handle_repr():
    handle = sl.SlackHandle(bot_token="xoxb-secret", user_token="xoxp-secret")
    assert "secret" not in repr(handle)


def test_search_with_a_user_token_renders_matches(monkeypatch):
    captured = {}

    def fake_search(token, *, query, count=20, page=1, sort="score", sort_dir="desc"):
        captured.update({"token": token, "query": query, "count": count,
                         "sort": sort, "sort_dir": sort_dir})
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


# ── search ORDERING (relevance vs newest) ────────────────────────────────────
#
# Slack's search.messages defaults to sort=score — relevance — so an unsorted
# search returns the top-scoring matches of ALL TIME. The reported failure was
# a "what's the latest in Slack" answer built on exactly that list, which read
# as current and wasn't. These pin both halves of the fix: the param actually
# reaches Slack, and the rendered result says which order it used.
# Params verified against https://docs.slack.dev/reference/methods/search.messages
# (sort: score|timestamp, default score; sort_dir: asc|desc, default desc).

def _capture_search(monkeypatch, matches=None, total=1):
    captured = {}

    def fake_search(token, *, query, count=20, page=1, sort="score", sort_dir="desc"):
        captured.update({"query": query, "sort": sort, "sort_dir": sort_dir})
        return {
            "matches": matches if matches is not None else [
                {"channel": {"name": "general"}, "ts": "1750000000.1",
                 "user": "U1", "text": "pricing v2 shipped"},
            ],
            "total": total,
        }

    monkeypatch.setattr(sl.slack_oauth, "search_messages", fake_search)
    return captured


def test_search_defaults_to_relevance_and_says_so(monkeypatch):
    """The default is unchanged — a keyword question wants the best match, not
    last Tuesday's passing mention — but the result now DECLARES that, so an
    answer cannot quietly present relevance hits as the newest news."""
    captured = _capture_search(monkeypatch)
    out = sl.PROVIDER.dispatch(_session("xoxp-1"), "slack_search_messages",
                               {"query": "pricing"})
    assert captured["sort"] == "score"
    assert captured["sort_dir"] == "desc"
    assert "ordered by RELEVANCE" in out
    assert "not the newest" in out
    assert "sort=\"newest\"" in out


def test_search_can_sort_by_time(monkeypatch):
    """sort='newest' → Slack's `timestamp`, descending. This is what makes a
    "what's the latest" question answerable at all."""
    captured = _capture_search(monkeypatch)
    out = sl.PROVIDER.dispatch(_session("xoxp-1"), "slack_search_messages",
                               {"query": "pricing", "sort": "newest"})
    assert captured["sort"] == "timestamp"
    assert captured["sort_dir"] == "desc"
    assert "ordered NEWEST FIRST" in out
    assert "RELEVANCE" not in out


def test_an_unknown_sort_falls_back_to_relevance(monkeypatch):
    """`sort` is model input, so it is validated rather than trusted — Slack
    400s an unrecognised value, and a typo must not break the search."""
    captured = _capture_search(monkeypatch)
    out = sl.PROVIDER.dispatch(_session("xoxp-1"), "slack_search_messages",
                               {"query": "pricing", "sort": "chronological"})
    assert captured["sort"] == "score"
    assert "ordered by RELEVANCE" in out


def test_a_queryless_search_windows_and_sorts_newest(monkeypatch):
    """No query means "the latest, whatever it is". search.messages requires a
    query string, but accepts a modifier-only one (verified live 2026-08-03),
    so the adapter sends `after:<date>` and forces newest — a keyword-free
    relevance ranking would be ranking nothing."""
    captured = _capture_search(monkeypatch)
    out = sl.PROVIDER.dispatch(_session("xoxp-1"), "slack_search_messages", {})
    assert captured["query"].startswith("after:")
    assert captured["sort"] == "timestamp"
    assert captured["sort_dir"] == "desc"
    assert "ordered NEWEST FIRST" in out
    assert "no keyword given" in out


def test_a_queryless_search_overrides_a_relevance_sort(monkeypatch):
    """`sort` is model input; pairing it with no query is a contradiction the
    adapter resolves in favour of time, never silently in favour of Slack's
    score default."""
    captured = _capture_search(monkeypatch)
    sl.PROVIDER.dispatch(_session("xoxp-1"), "slack_search_messages",
                         {"sort": "relevance"})
    assert captured["sort"] == "timestamp"


def test_an_empty_queryless_search_names_its_window(monkeypatch):
    """Zero rows from a windowed read means "quiet week", not "no such
    messages" — and the copy must not leak the synthetic after: query the
    model never wrote."""
    _capture_search(monkeypatch, matches=[])
    out = sl.PROVIDER.dispatch(_session("xoxp-1"), "slack_search_messages", {})
    assert "last 7 days" in out
    assert "after:" not in out


def test_a_generic_newest_keyword_is_dropped_and_widened(monkeypatch):
    """The observed failure: "latest feedback in slack" became query='feedback'
    — and Slack only matches messages CONTAINING that word, so the fresh
    message (which never says "feedback") was structurally invisible. A generic
    word + newest means "show me what's new": the keyword is dropped, the read
    widens to the window, and the result says so."""
    captured = _capture_search(monkeypatch)
    out = sl.PROVIDER.dispatch(_session("xoxp-1"), "slack_search_messages",
                               {"query": "feedback", "sort": "newest"})
    assert captured["query"].startswith("after:")
    assert captured["sort"] == "timestamp"
    assert "'feedback' was dropped" in out
    assert "ordered NEWEST FIRST" in out


def test_a_generic_word_on_relevance_stays_a_real_search(monkeypatch):
    """Without sort=newest there is no recency intent to honour — someone
    hunting for where the word "feedback" was literally used gets exactly
    that."""
    captured = _capture_search(monkeypatch)
    sl.PROVIDER.dispatch(_session("xoxp-1"), "slack_search_messages",
                         {"query": "feedback"})
    assert captured["query"] == "feedback"
    assert captured["sort"] == "score"


def test_a_specific_newest_keyword_keeps_its_query(monkeypatch):
    """The widening is for generic words only — "newest about pricing" is a
    real, answerable question and must stay one."""
    captured = _capture_search(monkeypatch)
    sl.PROVIDER.dispatch(_session("xoxp-1"), "slack_search_messages",
                         {"query": "pricing", "sort": "newest"})
    assert captured["query"] == "pricing"
    assert captured["sort"] == "timestamp"


def test_a_multi_word_generic_query_is_not_widened(monkeypatch):
    """"customer feedback" or "pricing feedback" carries a real subject; only
    a bare single generic word triggers the widening."""
    captured = _capture_search(monkeypatch)
    sl.PROVIDER.dispatch(_session("xoxp-1"), "slack_search_messages",
                         {"query": "customer feedback", "sort": "newest"})
    assert captured["query"] == "customer feedback"


def test_the_search_tool_tells_the_model_when_to_sort_by_time():
    """The disclosure only helps after the fact; the tool schema is what makes
    the model pick the right order in the first place."""
    schema = sl.SEARCH_TOOL["input_schema"]["properties"]["sort"]
    assert schema["enum"] == ["relevance", "newest"]
    assert "newest" in sl.SEARCH_TOOL["description"]
    assert "most recent" in sl.SEARCH_TOOL["description"]
    assert "sort=\"newest\"" in sl.PROVIDER.system_block()
    # `query` is optional now — a schema that still required it would turn the
    # queryless mode into a validation error at the API layer.
    assert "query" not in sl.SEARCH_TOOL["input_schema"].get("required", [])
    assert "OMIT" in sl.SEARCH_TOOL["input_schema"]["properties"]["query"]["description"]


def test_search_messages_clamps_sort_before_it_reaches_slack(monkeypatch):
    """Same guard one layer down, where the HTTP call is actually built."""
    from app.connectors import slack_oauth

    seen = {}

    class _Resp:
        ok = True
        status_code = 200

        @staticmethod
        def json():
            return {"ok": True, "messages": {"matches": [], "total": 0}}

    monkeypatch.setattr(slack_oauth.requests, "get",
                        lambda url, **k: seen.update(k.get("params") or {}) or _Resp())
    slack_oauth.search_messages("xoxp-1", query="x", sort="newest", sort_dir="sideways")
    assert seen["sort"] == "score"        # "newest" is OUR word, not Slack's
    assert seen["sort_dir"] == "desc"
    slack_oauth.search_messages("xoxp-1", query="x",
                                sort=slack_oauth.SEARCH_SORT_NEWEST)
    assert seen["sort"] == "timestamp"


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
    # 20 rows + the ordering note + the privacy disclosure + the cap marker.
    assert out.count("\n") <= sl._MAX_SEARCH_HITS + 2
    assert "showing 20 of 500 matches" in out


def test_empty_search_is_honest(monkeypatch):
    monkeypatch.setattr(sl.slack_oauth, "search_messages",
                        lambda *a, **k: {"matches": [], "total": 0})
    out = sl.PROVIDER.dispatch(_session("xoxp-1"), "slack_search_messages", {"query": "zzz"})
    assert out == "(no Slack messages match 'zzz')"


# ── dispatch_records (AC1/AC2/AC3/AC4) ──────────────────────────────────────


def test_dispatch_records_returns_none_for_other_tools():
    for name in ("slack_list_channels", "slack_channel_history", "slack_get_thread"):
        assert sl.PROVIDER.dispatch_records(_session(), name, {}) is None


def test_dispatch_records_no_records_when_search_is_unavailable():
    """No user token → SEARCH_UNAVAILABLE, same as dispatch — and no records,
    since there is nothing to build them from."""
    text, records = sl.PROVIDER.dispatch_records(
        _session(), "slack_search_messages", {"query": "pricing"}
    )
    assert records is None
    assert text == sl.SEARCH_UNAVAILABLE


def test_dispatch_records_text_matches_dispatch_exactly(monkeypatch):
    """AC5, mutation-proof: dispatch_records's text must be byte-identical to
    dispatch's own output for the identical search call — both now run
    `_search_and_hits` (the refactor), so this pins that the split changed
    nothing observable."""
    def fake_search(token, *, query, count=20, page=1, sort="score", sort_dir="desc"):
        return {"matches": [
            {"channel": {"id": "C1", "name": "general"}, "ts": "1750000000.1",
             "user": "U1", "text": "we ship pricing v2 <@U2>"},
        ], "total": 1}

    monkeypatch.setattr(sl.slack_oauth, "search_messages", fake_search)
    expected = sl.PROVIDER.dispatch(
        _session(user_token="xoxp-1"), "slack_search_messages", {"query": "pricing"}
    )
    text, records = sl.PROVIDER.dispatch_records(
        _session(user_token="xoxp-1"), "slack_search_messages", {"query": "pricing"}
    )
    assert text == expected
    assert records is not None and len(records) == 1


def test_dispatch_records_ac3_external_id_is_channel_and_ts(monkeypatch):
    """AC3 — Slack's compound identity: channel + ts, the only stable key one
    Slack message has."""
    monkeypatch.setattr(sl.slack_oauth, "search_messages", lambda *a, **k: {
        "matches": [
            {"channel": {"id": "C1", "name": "general"}, "ts": "1750000000.1",
             "user": "U1", "text": "we ship pricing v2"},
        ], "total": 1,
    })
    _text, records = sl.PROVIDER.dispatch_records(
        _session(user_token="xoxp-1"), "slack_search_messages", {"query": "pricing"}
    )
    assert records[0].external_id == "C1:1750000000.1"
    assert records[0].provider == "slack"
    assert records[0].kind == "message"


def test_dispatch_records_privacy_gate_matches_dispatch(monkeypatch):
    """The privacy filter (is_shareable_match) must apply identically to
    records as it does to the rendered text — a DM must never become a
    RawRecord any more than it becomes a rendered line."""
    monkeypatch.setattr(sl.slack_oauth, "search_messages", lambda *a, **k: {
        "matches": [
            {"channel": {"id": "D1", "is_im": True}, "ts": "1.0",
             "user": "U1", "text": "private message"},
            {"channel": {"id": "C1", "name": "general"}, "ts": "2.0",
             "user": "U1", "text": "public message"},
        ], "total": 2,
    })
    _text, records = sl.PROVIDER.dispatch_records(
        _session(user_token="xoxp-1"), "slack_search_messages", {"query": "message"}
    )
    assert len(records) == 1
    assert records[0].external_id == "C1:2.0"


def test_dispatch_records_ac4_no_puller_to_be_identical_with():
    """AC4 — Slack's answer, and it is a DIFFERENT one from the other four
    providers: Slack has NO RawRecord-producing puller at all.
    `kg_ingest.runner.PULLERS` has no "slack" entry, and Slack's own KG path
    (`kg_ingest.slack_extract`) hashes whole chunks of synced channel markdown
    keyed on `(channel_id, chunk)`, never one message and never
    `RawRecord.render()`. There is structurally nothing for a Slack sweep
    record to collide with in the ledger."""
    from app.kg_ingest import runner
    from app.kg_ingest import slack_extract

    assert "slack" not in runner.PULLERS
    # The ledger key Slack's OWN ingestion path uses is scoped to
    # (channel_id, chunk) — not `RawRecord.render()` at all, confirming there
    # is no unit for a sweep record to collide with.
    h1 = slack_extract._chunk_hash("C1", "some channel markdown")
    h2 = slack_extract._chunk_hash("C2", "some channel markdown")
    assert h1 != h2, "the hash is scoped by channel, not just content"


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

    def fake_history(token, *, channel, limit=100, oldest=None, latest=None,
                     cursor=None, auto_join=False):
        captured.update({"channel": channel, "limit": limit, "oldest": oldest,
                         "auto_join": auto_join})
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
    assert captured["auto_join"] is True   # never fail on "the bot wasn't invited"
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


# ── name → id resolution against the WHOLE workspace ─────────────────────────
#
# THE bug. conversations.history takes an ID only (the reference is explicit),
# and the resolver only knew the channels the bot had been INVITED to. So
# "#launch-room" resolved to the literal string "launch-room", Slack answered
# `channel_not_found`, the model read that as "no such channel" and fell back
# to search — which is how a channel-history question became a relevance-ranked
# keyword search over all time.

def test_a_channel_the_bot_is_not_in_still_resolves_to_its_id():
    """The membership list has never heard of #launch-room; the workspace list
    has. A name must become an id either way."""
    handle = _handle()
    assert "launch-room" not in {c["name"] for c in handle.channels}
    assert handle.resolve_channel("#launch-room") == "C3"
    assert handle.resolve_channel("launch-room") == "C3"


def test_a_raw_id_never_touches_the_channel_directory(monkeypatch):
    """Ids pass through unchanged, and cost nothing to pass through — no
    conversations.list page is fetched to hand back what we were given."""
    handle = sl.SlackHandle(bot_token="xoxb-1")   # nothing preloaded
    monkeypatch.setattr(sl.slack_sync, "fetch_channels",
                        lambda *a, **k: pytest.fail("membership list fetched for an id"))
    monkeypatch.setattr(sl.slack_oauth, "list_channels",
                        lambda *a, **k: pytest.fail("workspace list fetched for an id"))
    assert handle.resolve_channel("C0123ABCD") == "C0123ABCD"
    assert handle.resolve_channel("G0123ABCD") == "G0123ABCD"


def test_resolution_falls_back_to_the_reference_when_nothing_matches():
    assert _handle().resolve_channel("#no-such-place") == "no-such-place"


def test_the_workspace_list_is_fetched_once_and_survives_failure(monkeypatch):
    """Best-effort, like every other directory read on this handle: a failed
    conversations.list degrades resolution, it never breaks the answer."""
    calls = []
    monkeypatch.setattr(sl.slack_oauth, "list_channels",
                        lambda token: calls.append(token) or (_ for _ in ()).throw(
                            RuntimeError("boom")))
    handle = sl.SlackHandle(bot_token="xoxb-1")
    handle._channels_loaded = True     # membership list empty, already "loaded"
    assert handle.workspace_channel_list() == []
    assert handle.workspace_channel_list() == []
    assert calls == ["xoxb-1"]         # fetched once, not once per lookup


def test_the_privacy_gate_still_reads_MEMBERSHIP_not_the_workspace():
    """Load-bearing separation: `bot_channel_ids` decides whether a private
    search hit may be quoted to a colleague. It must keep meaning "the bot is
    in this channel" — resolving names against the wider workspace list must
    not widen the set of private conversations we are willing to report."""
    handle = _handle()
    assert handle.bot_channel_ids() == {"C1", "C2"}
    assert "G4" not in handle.bot_channel_ids()      # visible, but not a member
    assert not sl.is_shareable_match(
        {"channel": {"id": "G4", "name": "founders", "is_private": True}},
        handle.bot_channel_ids(),
    )


# ── self-join, mirroring the delivery path ───────────────────────────────────

def _history_raising(monkeypatch, error, then=None):
    """Patch fetch_conversation_history to reject with a Slack error string."""
    def fake(token, *, channel, limit=100, oldest=None, latest=None, cursor=None,
             auto_join=False):
        if then is not None and auto_join:
            return then
        raise HTTPException(400, f"Slack rejected the history read: {error}")

    monkeypatch.setattr(sl.slack_oauth, "fetch_conversation_history", fake)


def test_history_asks_slack_oauth_to_self_join_and_retry(monkeypatch):
    """The adapter delegates the join to slack_oauth, exactly as brief delivery
    does (post_message(..., auto_join=True)) — so a public channel the bot was
    never invited to reads successfully on the retry."""
    _history_raising(monkeypatch, "not_in_channel", then={
        "messages": [{"ts": "1750000100.0", "user": "U1", "text": "launch is friday"}],
        "has_more": False,
    })
    out = sl.PROVIDER.dispatch(_session(), "slack_channel_history",
                               {"channel": "#launch-room"})
    assert "launch is friday" in out


def test_self_join_retry_happens_inside_the_connector(monkeypatch):
    """One level down: not_in_channel → conversations.join → read again, once."""
    from app.connectors import slack_oauth

    attempts = []
    joined = []

    class _Resp:
        def __init__(self, payload):
            self._payload = payload
            self.ok = True
            self.status_code = 200

        def json(self):
            return self._payload

    def fake_get(url, **kwargs):
        attempts.append(kwargs["params"]["channel"])
        if len(attempts) == 1:
            return _Resp({"ok": False, "error": "not_in_channel"})
        return _Resp({"ok": True, "messages": [{"ts": "1.0", "text": "hi"}]})

    monkeypatch.setattr(slack_oauth.requests, "get", fake_get)
    monkeypatch.setattr(slack_oauth, "join_channel",
                        lambda token, channel: joined.append(channel) or True)
    out = slack_oauth.fetch_conversation_history("xoxb-1", channel="C3", auto_join=True)
    assert joined == ["C3"]
    assert attempts == ["C3", "C3"]        # read, join, read again — once
    assert out["messages"][0]["text"] == "hi"


def test_without_auto_join_the_rejection_is_unchanged(monkeypatch):
    """Every other caller (the Configure drawer preview, the sync path) keeps
    today's behaviour — the join is opt-in, not a new default."""
    from app.connectors import slack_oauth

    joined = []

    class _Resp:
        ok = True
        status_code = 200

        @staticmethod
        def json():
            return {"ok": False, "error": "not_in_channel"}

    monkeypatch.setattr(slack_oauth.requests, "get", lambda url, **k: _Resp())
    monkeypatch.setattr(slack_oauth, "join_channel",
                        lambda token, channel: joined.append(channel) or True)
    with pytest.raises(HTTPException):
        slack_oauth.fetch_conversation_history("xoxb-1", channel="C3")
    assert joined == []


def test_a_private_channel_says_invite_the_bot(monkeypatch):
    """A bot cannot add itself to a private channel, so the retry cannot help
    and the honest instruction is the answer — not "the name is wrong"."""
    _history_raising(monkeypatch, "not_in_channel")
    out = sl.PROVIDER.dispatch(_session(), "slack_channel_history",
                               {"channel": "#founders"})
    assert "PRIVATE channel" in out
    assert "/invite @Sprntly in #founders" in out
    assert "nothing was said there" in out
    assert "the name is wrong" not in out


def test_a_public_channel_that_could_not_be_joined_says_invite_the_bot(monkeypatch):
    _history_raising(monkeypatch, "not_in_channel")
    out = sl.PROVIDER.dispatch(_session(), "slack_channel_history",
                               {"channel": "#launch-room"})
    assert "#launch-room exists" in out
    assert "/invite @Sprntly in #launch-room" in out


def test_only_an_unknown_name_gets_the_name_is_wrong_copy(monkeypatch):
    """The narrowing that matters: telling a model the channel name might be
    wrong is what made it stop reading channels and start searching. It is now
    said only when the workspace really has no such channel."""
    _history_raising(monkeypatch, "channel_not_found")
    out = sl.PROVIDER.dispatch(_session(), "slack_channel_history",
                               {"channel": "#nowhere"})
    assert "no channel called 'nowhere' is visible" in out
    assert "slack_list_channels" in out
    # …and a channel that DOES exist never gets that copy.
    other = sl.PROVIDER.dispatch(_session(), "slack_channel_history",
                                 {"channel": "#launch-room"})
    assert "no channel called" not in other


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
    assert "no channel called 'nope' is visible" in out
    assert "slack_list_channels" in out


def test_a_non_channel_rejection_still_uses_the_generic_copy(monkeypatch):
    """`_channel_access_text` claims only the two channel-access errors; a rate
    limit or an auth failure on the history read keeps its own copy."""
    def boom(*a, **k):
        raise HTTPException(400, "Slack rejected the history read: ratelimited")

    monkeypatch.setattr(sl.slack_oauth, "fetch_conversation_history", boom)
    out = sl.PROVIDER.dispatch(_session(), "slack_channel_history",
                               {"channel": "#launch-room"})
    assert "rate-limited" in out and "may be incomplete" in out


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
