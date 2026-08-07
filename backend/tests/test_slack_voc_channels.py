"""Slack as a voice-of-customer source — channel resolution, aggregation,
honesty and privacy.

No network/LLM/DB: `db.list_slack_connections`, the token decrypt, and the three
Slack fetchers (`fetch_channels`, `fetch_users`, `fetch_conversation_history`)
are patched in the modules the adapter imports them from.

WHAT THESE PIN, and why each one is a real defect and not a style preference:

  AGGREGATION. Every configured channel reaches the assembled context. The
  reported bug was an answer built from ONE channel's search hit that read like
  a report on the whole feedback surface.

  NO-KEYWORD ROUTING. A feedback question with no "slack" token reaches the
  channels. Before, Slack's only chat routes both required the word.

  HONESTY. A channel that could not be read is NAMED with its reason. "Nothing
  in the feedback channels" must never be what a timeout looks like.

  PRIVACY. A DM never becomes text a teammate reads, on this path or the search
  path — both go through one predicate now, so this is also a test that the
  extraction did not loosen it.
"""
from __future__ import annotations

import json

import pytest
from fastapi import HTTPException

from app.connector_lookup import slack as sl
from app.connector_lookup import slack_voc as voc
from app.connectors import slack_oauth, slack_sync

COMPANY = "co-voc"

#: Bot-member channels, as `slack_sync.fetch_channels` returns them (it filters
#: on is_member, so everything here is a channel the bot is in).
_MEMBER_CHANNELS = [
    {"id": "C1", "name": "product-feedback", "is_private": False},
    {"id": "C2", "name": "support-escalations", "is_private": False},
    {"id": "C3", "name": "demos", "is_private": False},
    {"id": "G9", "name": "founders", "is_private": True},
]


def _row(config: dict | None = None, *, status: str = "active", bot: str = "xoxb-1"):
    return {
        "status": status,
        "config_json": json.dumps(config or {}),
        "token_json_encrypted": json.dumps({"access_token": bot}),
    }


def _messages(channel_id: str) -> list[dict]:
    return [
        {"ts": "1780000000.1", "user": "U1", "text": f"first in {channel_id}"},
        {"ts": "1780000100.1", "user": "U2", "text": f"second in {channel_id}"},
    ]


@pytest.fixture
def slack_env(monkeypatch):
    """A working Slack workspace. Returns a mutable dict the tests tune:

    `rows`      — what db.list_slack_connections yields
    `history`   — {channel_id: messages | Exception} ; a value that is an
                  exception is RAISED, which is how a per-channel failure is
                  simulated without any threading trickery
    `members`   — the bot-membership channel list
    `calls`     — every channel id conversations.history was asked for
    """
    env: dict = {
        "rows": [_row()],
        "history": {c["id"]: _messages(c["id"]) for c in _MEMBER_CHANNELS},
        "members": list(_MEMBER_CHANNELS),
        "calls": [],
    }

    from app import db

    monkeypatch.setattr(db, "list_slack_connections", lambda cid: env["rows"])
    monkeypatch.setattr(db, "get_connection", lambda cid, provider: None)
    monkeypatch.setattr(sl, "decrypt_token_json", lambda enc: enc)
    monkeypatch.setattr(slack_sync, "fetch_channels", lambda *a, **k: env["members"])
    monkeypatch.setattr(
        slack_sync, "fetch_users", lambda *a, **k: {"U1": "ada", "U2": "grace"}
    )
    monkeypatch.setattr(slack_oauth, "list_channels", lambda token: env["members"])

    def _history(token, *, channel, limit=None, oldest=None, auto_join=False, **kw):
        env["calls"].append(channel)
        found = env["history"].get(channel, [])
        if isinstance(found, Exception):
            raise found
        return {"messages": list(reversed(found)), "has_more": False}

    monkeypatch.setattr(slack_oauth, "fetch_conversation_history", _history)
    return env


# ── channel resolution: both config shapes, and the one that is NOT a VoC shape ──


def test_selection_is_read_from_sync_channel_ids(slack_env):
    slack_env["rows"] = [_row({
        "sync_channel_ids": ["C1", "C3"],
        "sync_channel_names": {"C1": "product-feedback", "C3": "demos"},
    })]
    channels, explicit = voc.configured_channels(COMPANY)
    assert explicit is True
    assert [(c.id, c.name) for c in channels] == [
        ("C1", "product-feedback"), ("C3", "demos")
    ]


def test_selection_merges_and_dedupes_across_duplicate_connection_rows(slack_env):
    """Slack is per-USER, so a company holds several rows — and in the live data
    one company carries TWO rows for the same channel. The VoC set is the union
    of every active row's selection, each channel once, insertion order kept."""
    slack_env["rows"] = [
        _row({"sync_channel_ids": ["C1"], "sync_channel_names": {"C1": "product-feedback"}}),
        _row({"sync_channel_ids": ["C1"], "sync_channel_names": {"C1": "product-feedback"}}),
        _row({"sync_channel_ids": ["C2", "C1"],
              "sync_channel_names": {"C2": "support-escalations"}}),
        # Inactive rows contribute nothing.
        _row({"sync_channel_ids": ["C3"]}, status="revoked"),
    ]
    channels, explicit = voc.configured_channels(COMPANY)
    assert explicit is True
    assert [c.id for c in channels] == ["C1", "C2"]


def test_delivery_target_config_is_not_a_voc_selection(slack_env):
    """`channel_id` / `channel_name` is the user's brief-DELIVERY target
    (POST /connectors/slack/config — "Save the user's notification target"),
    NOT a customer-feedback source.

    Reading it as one would mine a company's own announcement channel for
    customer sentiment, and would silently pin a single channel as "the" VoC
    channel — the exact single-channel answer this whole module exists to stop.
    A row carrying only a delivery target therefore yields NO explicit
    selection, and the read falls back to bot membership (the Settings picker's
    own stated behaviour when nothing is ticked)."""
    slack_env["rows"] = [_row({
        "target_type": "channel", "channel_id": "C3", "channel_name": "demos",
    })]
    channels, explicit = voc.configured_channels(COMPANY)
    assert (channels, explicit) == ([], False)

    result = voc.read(COMPANY)
    assert result.selection == voc.SELECTION_MEMBERSHIP
    # Every bot-member channel, not just the delivery target.
    assert {r.channel.name for r in result.reads} >= {
        "product-feedback", "support-escalations", "demos"
    }


def test_no_selection_falls_back_to_every_bot_member_channel(slack_env):
    result = voc.read(COMPANY)
    assert result.selection == voc.SELECTION_MEMBERSHIP
    assert set(slack_env["calls"]) == {"C1", "C2", "C3", "G9"}


# ── aggregation ──────────────────────────────────────────────────────────────


def test_three_configured_channels_are_all_represented(slack_env):
    """AC1. Three configured channels → three sections, three channels' worth of
    messages, one merged block. Not the first one that answered."""
    slack_env["rows"] = [_row({
        "sync_channel_ids": ["C1", "C2", "C3"],
        "sync_channel_names": {
            "C1": "product-feedback", "C2": "support-escalations", "C3": "demos",
        },
    })]
    result = voc.read(COMPANY)

    assert result.selection == voc.SELECTION_CONFIGURED
    assert len(result.read_channels) == 3
    assert sorted(slack_env["calls"]) == ["C1", "C2", "C3"]

    block = result.render()
    for name in ("#product-feedback", "#support-escalations", "#demos"):
        assert f"### {name}" in block, name
    for cid in ("C1", "C2", "C3"):
        assert f"first in {cid}" in block and f"second in {cid}" in block, cid
    # The aggregate says it is one, so a theme in one channel is not reported
    # as a company-wide theme.
    assert "Attribute every quote to ITS OWN channel" in block


def test_a_quiet_channel_is_rendered_as_read_not_omitted(slack_env):
    """An empty channel is not the same as an unread one, and the block has to
    be able to say "we looked and it was quiet" — otherwise the only way to
    express it is silence, which reads as "not checked"."""
    slack_env["rows"] = [_row({
        "sync_channel_ids": ["C1", "C3"],
        "sync_channel_names": {"C1": "product-feedback", "C3": "demos"},
    })]
    slack_env["history"]["C3"] = []
    block = voc.read(COMPANY).render()
    assert "### #demos" in block
    assert "no messages posted in the last" in block


# ── honesty ──────────────────────────────────────────────────────────────────


def test_an_unreadable_channel_is_named_with_its_reason(slack_env):
    """AC4. A channel the bot cannot read is NAMED, with copy that says what to
    do about it, and the block explicitly forbids reading its absence as
    silence. This is the "nothing in Slack about it" ≠ "Slack timed out"
    contract, at channel granularity."""
    slack_env["rows"] = [_row({
        "sync_channel_ids": ["C1", "G9"],
        "sync_channel_names": {"C1": "product-feedback", "G9": "founders"},
    })]
    slack_env["history"]["G9"] = HTTPException(400, "not_in_channel")

    result = voc.read(COMPANY)
    statuses = {r.channel.name: r.status for r in result.reads}
    assert statuses["G9" if "G9" in statuses else "founders"] == voc.STATUS_UNREADABLE

    block = result.render()
    assert "Feedback channels NOT read" in block
    assert "#founders" in block
    # The reason is actionable, not a shrug.
    assert "/invite @Sprntly" in block
    assert "Do not describe them as quiet" in block


def test_a_channel_that_raises_is_reported_not_dropped(slack_env):
    slack_env["rows"] = [_row({
        "sync_channel_ids": ["C1", "C2"],
        "sync_channel_names": {"C1": "product-feedback", "C2": "support-escalations"},
    })]
    slack_env["history"]["C2"] = RuntimeError("boom")

    result = voc.read(COMPANY)
    assert len(result.reads) == 2               # nothing vanished
    assert len(result.read_channels) == 1
    block = result.render()
    assert "#product-feedback" in block
    assert "#support-escalations" in block and "could not be read" in block


def test_no_connection_is_not_an_empty_result(slack_env):
    """"Slack is not connected" and "the feedback channels were quiet" are
    different answers, and printing one for the other is how a permissions gap
    becomes a fact about customers."""
    slack_env["rows"] = []
    result = voc.read(COMPANY)
    assert result.present is False
    assert result.connected is False
    assert result.render() == ""            # nothing to assert an absence from
    assert "not connected" in result.unavailable


def test_over_the_channel_cap_the_dropped_channels_are_named(slack_env):
    slack_env["members"] = [
        {"id": f"C{i}", "name": f"chan-{i}", "is_private": False} for i in range(6)
    ]
    slack_env["history"] = {f"C{i}": _messages(f"C{i}") for i in range(6)}
    result = voc.read(COMPANY, max_channels=2)
    assert len(slack_env["calls"]) == 2
    dropped = [r for r in result.reads if r.status == voc.STATUS_DROPPED]
    assert len(dropped) == 4
    assert all("not read" in r.reason() for r in dropped)


# ── privacy ──────────────────────────────────────────────────────────────────


def test_a_dm_in_the_selection_is_excluded_and_never_read(slack_env):
    """A `D…` id cannot legitimately be a VoC channel. It is refused before any
    read, named as excluded, and — the part that matters — no history call is
    made for it."""
    slack_env["rows"] = [_row({
        "sync_channel_ids": ["C1", "D7"],
        "sync_channel_names": {"C1": "product-feedback", "D7": "ada"},
    })]
    result = voc.read(COMPANY)
    assert "D7" not in slack_env["calls"]
    excluded = [r for r in result.reads if r.status == voc.STATUS_EXCLUDED]
    assert [r.channel.id for r in excluded] == ["D7"]
    assert "DM" in excluded[0].reason()


def test_a_private_channel_the_bot_is_not_in_is_excluded(slack_env):
    """The gate is bot membership, exactly as it is for search hits: a private
    conversation the bot cannot see is one no teammate's lookup could reach."""
    slack_env["members"] = [{"id": "C1", "name": "product-feedback", "is_private": False}]
    slack_env["rows"] = [_row({
        "sync_channel_ids": ["C1", "G9"],
        "sync_channel_names": {"C1": "product-feedback", "G9": "founders"},
    })]
    result = voc.read(COMPANY)
    assert "G9" not in slack_env["calls"]
    assert [r.channel.id for r in result.reads if r.status == voc.STATUS_EXCLUDED] == ["G9"]


def test_search_privacy_gate_still_excludes_dms_after_the_extraction():
    """`is_shareable_match` now delegates to `is_shareable_channel`. Pinned here
    as well as in the search suite because the extraction is exactly the kind of
    refactor that loosens a privacy rule without any test noticing."""
    bot_ids = {"C1", "G4"}
    assert sl.is_shareable_match({"channel": {"id": "C1", "name": "general"}}, bot_ids)
    assert not sl.is_shareable_match({"channel": {"id": "D1", "is_im": True}}, bot_ids)
    assert not sl.is_shareable_match({"channel": {"id": "G7", "is_private": True}}, bot_ids)
    assert sl.is_shareable_match({"channel": {"id": "G4", "is_private": True}}, bot_ids)
    # The compound shape the search API also uses (channel_id, no nested id).
    assert not sl.is_shareable_match({"channel_id": "D2", "channel": {"is_im": True}}, bot_ids)


# ── the adapter tool ─────────────────────────────────────────────────────────


def test_the_voc_tool_is_offered_and_aggregates(slack_env):
    """A question that DOES name Slack still gets the aggregate, through the
    same module — so the two routes can never disagree about "all the
    channels"."""
    assert any(t["name"] == "slack_voc_channels" for t in sl.PROVIDER.tools())
    slack_env["rows"] = [_row({
        "sync_channel_ids": ["C1", "C2"],
        "sync_channel_names": {"C1": "product-feedback", "C2": "support-escalations"},
    })]
    session = sl.PROVIDER.open_session(COMPANY)
    assert session.handle.company_id == COMPANY   # tenancy, never model input
    out = sl.PROVIDER.dispatch(session, "slack_voc_channels", {})
    assert "### #product-feedback" in out and "### #support-escalations" in out


def test_the_voc_tool_refuses_rather_than_guessing_a_tenant(slack_env):
    handle = sl.SlackHandle(bot_token="xoxb-1")     # no company_id
    session = sl.LookupSession(provider="slack", handle=handle)
    out = sl.PROVIDER.dispatch(session, "slack_voc_channels", {})
    assert "no company is in scope" in out
    assert slack_env["calls"] == []


# ── routing: reaching the channels WITHOUT the word "slack" ──────────────────


def test_feedback_questions_route_to_the_voc_path_without_naming_slack():
    """AC2, first half. None of these say "slack" or name a #channel, so
    `is_connector_lookup` declines them by design — this predicate is what sends
    them to the voice-of-customer pass, which is what reads the channels."""
    from app.skill_router import is_connector_lookup, is_voc_report_request

    for q in [
        "what are our customers saying?",
        "what have users been complaining about lately",
        "what's been happening in our feedback channels this week",
        "anything new in the support channels?",
        "what are customers asking for",
        "what came through the channels we connected for voice of customer",
    ]:
        assert is_voc_report_request(q), q
        assert "slack" not in q.lower()
        assert not is_connector_lookup(q), q


def test_the_widened_rules_do_not_divert_ordinary_questions():
    """The precision half. These carry a customer-noun or a channel-noun and
    must still reach normal routing — a VoC rule that claims them turns every
    product question into a feedback report."""
    from app.skill_router import is_voc_report_request

    for q in [
        "which channels do we sell through?",
        "what are our customers' plan tiers",
        "how many users signed up last week",
        "generate a PRD for the onboarding channel picker",
        "what is our sales channel strategy",
        "summarize this document",
    ]:
        assert not is_voc_report_request(q), q


def test_slack_only_company_reaches_the_voc_pass(slack_env):
    """AC2, second half. A company whose ONLY voice source is Slack used to be
    told to connect Fireflies. `has_call_source` is the gate qa_agent checks
    before handing the turn to the digest."""
    import app.call_digest as cd

    assert cd.has_call_source(COMPANY) is True
    slack_env["rows"] = []
    assert cd.has_call_source(COMPANY) is False


def test_the_digest_prompt_carries_every_configured_channel(slack_env, monkeypatch):
    """The end-to-end shape of the bug: a feedback question naming no source,
    for a company with three configured channels and no calls at all. All three
    must be in the prompt, and the coverage banner must name them — the banner
    is what a user reads to check that the answer really covered them."""
    import app.call_digest as cd
    import app.graph.gateway as gateway_mod

    slack_env["rows"] = [_row({
        "sync_channel_ids": ["C1", "C2", "C3"],
        "sync_channel_names": {
            "C1": "product-feedback", "C2": "support-escalations", "C3": "demos",
        },
    })]
    monkeypatch.setattr(cd, "_load_api_key", lambda cid: None)
    monkeypatch.setattr(cd, "_voice_docs", lambda cid, w: [])
    monkeypatch.setattr(cd, "_zoom_context", lambda cid: None)
    monkeypatch.setattr(cd, "build_kg_context", lambda *a, **k: cd.KgContext())

    captured: dict = {}

    class _R:
        output = {"answer": "themes", "key_points": [], "citations": [],
                  "confidence": 0.6, "unanswered": ""}

    monkeypatch.setattr(
        gateway_mod, "llm_call",
        lambda **kw: (captured.update(kw), _R())[1],
    )

    payload = cd.answer(
        enterprise_id=COMPANY, question="what are our customers saying?"
    )

    # It did NOT dead-end on "no call source is connected".
    assert "Fireflies" not in payload["answer"]
    prompt = captured["input"]
    for name in ("#product-feedback", "#support-escalations", "#demos"):
        assert f"### {name}" in prompt, name
    for cid in ("C1", "C2", "C3"):
        assert f"first in {cid}" in prompt, cid
    # The coverage banner names the channels, not just a count.
    assert "customer-feedback channels" in prompt
    assert "#demos" in prompt.split("===")[1]


def test_the_digest_banner_names_a_channel_it_could_not_read(slack_env, monkeypatch):
    """AC4 at the answer layer. A failed channel is a coverage caveat in the
    banner, never an absence the model can read as silence."""
    import app.call_digest as cd
    import app.graph.gateway as gateway_mod

    slack_env["rows"] = [_row({
        "sync_channel_ids": ["C1", "G9"],
        "sync_channel_names": {"C1": "product-feedback", "G9": "founders"},
    })]
    slack_env["history"]["G9"] = HTTPException(400, "not_in_channel")
    monkeypatch.setattr(cd, "_load_api_key", lambda cid: None)
    monkeypatch.setattr(cd, "_voice_docs", lambda cid, w: [])
    monkeypatch.setattr(cd, "_zoom_context", lambda cid: None)
    monkeypatch.setattr(cd, "build_kg_context", lambda *a, **k: cd.KgContext())

    captured: dict = {}

    class _R:
        output = {"answer": "themes", "key_points": [], "citations": [],
                  "confidence": 0.6, "unanswered": ""}

    monkeypatch.setattr(
        gateway_mod, "llm_call",
        lambda **kw: (captured.update(kw), _R())[1],
    )
    cd.answer(enterprise_id=COMPANY, question="summarize customer feedback")

    banner = captured["input"].split("===")[1]
    assert "NOT read: #founders" in banner
    assert "coverage caveat" in banner


def test_config_keys_match_the_sync_paths_keys():
    """slack_voc holds its own copies of the two config keys so it stays
    importable without the sync path. Copies drift; this is the assertion that
    makes them fail loudly instead."""
    assert voc.CONFIG_SYNC_CHANNEL_IDS == slack_sync.CONFIG_SYNC_CHANNEL_IDS
    assert voc.CONFIG_SYNC_CHANNEL_NAMES == slack_sync.CONFIG_SYNC_CHANNEL_NAMES
