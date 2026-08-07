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
        #: {channel_id: auto_join flag the read was made with} — the write
        #: side effect, recorded so a test can assert its ABSENCE.
        "joins": {},
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
        env["joins"][channel] = auto_join
        found = env["history"].get(channel, [])
        if isinstance(found, Exception):
            raise found
        return {"messages": list(reversed(found)), "has_more": False}

    monkeypatch.setattr(slack_oauth, "fetch_conversation_history", _history)

    # The catalog is empty unless a test fills `env["catalog"]`. Patched at the
    # accessor `slack_voc` actually calls, not at the table: `document_catalog`
    # is the single module that names `document_catalog`, and going around it
    # in a test would prove nothing about the code path that ships.
    env["catalog"] = []
    import app.document_catalog as dc

    monkeypatch.setattr(
        dc, "list_documents", lambda cid, **kw: list(env["catalog"])
    )
    return env


class _Doc:
    """A `CatalogDocument`-shaped row. `**kw`-tolerant by construction so a new
    field on the real model cannot silently narrow this double."""

    def __init__(self, external_id, title, summary="", topics=None, doc_date="",
                 updated_at="", url="", **kw):
        self.external_id = external_id
        self.title = title
        self.summary = summary
        self.topics = topics or []
        self.doc_date = doc_date
        self.updated_at = updated_at
        self.url = url
        for k, v in kw.items():
            setattr(self, k, v)


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


def test_the_fallback_includes_private_invited_channels(slack_env):
    """APURVA'S SCOPE DECISION, 2026-08-07 — pinned so it is not "fixed".

    With nothing ticked, every channel the bot was INVITED to is read, private
    channels included. That is the Settings copy verbatim and it is what
    `slack_sync.select_sync_channels` already does for the ingest sync, so chat
    and the sync cannot disagree about what a company's voice of customer is.
    An invitation IS the grant: the bot cannot see a private channel it was
    never added to, so a public-only restriction would protect nothing and
    would silently narrow the answer.

    `#founders` here is private AND the bot is in it."""
    private = [c for c in _MEMBER_CHANNELS if c["is_private"]]
    assert private, "fixture must contain a private channel for this to mean anything"

    voc.read(COMPANY)
    for channel in private:
        assert channel["id"] in slack_env["calls"], channel["name"]


def test_a_delivery_only_install_still_has_voc_channels(slack_env):
    """The same decision, at the routing gate. A company that connected Slack
    only to receive its brief — `target_type: "dm"`, no selection anywhere — is
    still answered from whatever channels the bot is in, rather than being
    gated behind an explicit selection it never made. Reviewed as a possible
    over-trigger and ACCEPTED; it is the majority shape in the live data."""
    slack_env["rows"] = [_row({
        "target_type": "dm", "channel_id": "", "channel_name": "demos",
    })]
    assert voc.has_voc_channels(COMPANY) is True

    result = voc.read(COMPANY)
    assert result.selection == voc.SELECTION_MEMBERSHIP
    assert result.present is True
    assert result.render() != ""


def test_the_fallback_still_excludes_dms_and_uninvited_private_channels(slack_env):
    """The decision above widens the SET, not the privacy rule. Membership is
    what both turn on: an invited private channel is in, a DM and a private
    channel the bot was never added to are out."""
    slack_env["members"] = [{"id": "C1", "name": "product-feedback", "is_private": False}]
    slack_env["rows"] = [_row({
        "sync_channel_ids": ["C1", "G9", "D7"],
        "sync_channel_names": {"C1": "product-feedback", "G9": "founders", "D7": "ada"},
    })]
    result = voc.read(COMPANY)
    excluded = {r.channel.id for r in result.reads if r.status == voc.STATUS_EXCLUDED}
    assert excluded == {"G9", "D7"}
    assert set(slack_env["calls"]) == {"C1"}


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


# ── no writes to the customer's workspace ────────────────────────────────────


def test_the_implicit_read_never_joins_a_channel(slack_env):
    """`conversations.join` adds the Sprntly bot to a channel and Slack posts a
    join notice into the customer's workspace — an outward-facing WRITE.

    This path runs on a question that named no channel and no source ("what are
    our customers saying?"), so it must not produce one. Asserting the FLAG the
    read was made with, not just that no join happened, because
    `fetch_conversation_history` only joins on a `not_in_channel` error: a test
    that watched for the join itself would pass on healthy fixtures and let the
    write ship anyway. Same shape as the 2026-08-05 sweep incident, where
    `open_session` looked read-only and rotated OAuth tokens."""
    slack_env["rows"] = [_row({
        "sync_channel_ids": ["C1", "C2"],
        "sync_channel_names": {"C1": "product-feedback", "C2": "support-escalations"},
    })]
    voc.read(COMPANY)
    assert slack_env["joins"] == {"C1": False, "C2": False}


def test_the_voc_tool_never_joins_a_channel_either(slack_env):
    """The named route reads the same channel SET, none of which the user
    picked individually — so it inherits the same rule."""
    slack_env["rows"] = [_row({"sync_channel_ids": ["C1"],
                               "sync_channel_names": {"C1": "product-feedback"}})]
    session = sl.PROVIDER.open_session(COMPANY)
    sl.PROVIDER.dispatch(session, "slack_voc_channels", {})
    assert set(slack_env["joins"].values()) == {False}


def test_the_named_channel_tool_still_joins(slack_env):
    """The default stays True where it belongs: the user named THAT channel and
    asked for it, and joining is the obvious repair for the commonest failure.
    Pinned so the fix above is scoped to the implicit path rather than quietly
    changing a shipped tool's behaviour."""
    session = sl.PROVIDER.open_session(COMPANY)
    sl.PROVIDER.dispatch(session, "slack_channel_history", {"channel": "#demos"})
    assert slack_env["joins"] == {"C3": True}


# ── one workspace's channels, one workspace's token ──────────────────────────


def test_the_selection_is_scoped_to_the_token_s_workspace(slack_env):
    """Channels are merged across rows; the TOKEN comes from one. Two members
    on different workspaces would otherwise have A's channel ids read with B's
    token — Slack says `channel_not_found`, and this module would faithfully
    report a healthy channel as unreadable and tell the user to invite a bot
    that is already there."""
    slack_env["rows"] = [
        _row({"team": {"id": "T-A"}, "sync_channel_ids": ["C1"],
              "sync_channel_names": {"C1": "product-feedback"}}),
        _row({"team": {"id": "T-B"}, "sync_channel_ids": ["C2"],
              "sync_channel_names": {"C2": "support-escalations"}}),
    ]
    channels, explicit = voc.configured_channels(COMPANY, "T-A")
    assert explicit is True
    assert [c.id for c in channels] == ["C1"]
    # And the live read picks the scope up from the session's own team.
    result = voc.read(COMPANY)
    assert slack_env["calls"] == ["C1"]


def test_rows_without_a_recorded_team_are_never_dropped(slack_env):
    """Excluding on a comparison that cannot be made would lose real channels —
    one live row carries no `team` at all. Filtering happens only when BOTH
    sides are known and differ."""
    slack_env["rows"] = [
        _row({"sync_channel_ids": ["C1"]}),                       # no team
        _row({"team": {"id": "T-A"}, "sync_channel_ids": ["C2"]}),
        _row({"team": {"id": "T-B"}, "sync_channel_ids": ["C3"]}),
    ]
    channels, _ = voc.configured_channels(COMPANY, "T-A")
    assert [c.id for c in channels] == ["C1", "C2"]
    # No team on the reading side either → no filtering at all.
    channels, _ = voc.configured_channels(COMPANY, "")
    assert [c.id for c in channels] == ["C1", "C2", "C3"]


# ── the stored catalog layer ─────────────────────────────────────────────────


def test_an_unreadable_channel_with_a_stored_summary_is_not_absent(slack_env):
    """THE REPORTED DEFECT, at its narrowest. The live answer said "I am not
    able to confirm whether a #demos channel exists" while a #demos
    document_catalog row with a summary and topics sat in the table, refreshed
    the day before. The channel must now appear, with its summary, its date,
    and an explicit "not read live"."""
    slack_env["rows"] = [_row({
        "sync_channel_ids": ["C1", "C3"],
        "sync_channel_names": {"C1": "product-feedback", "C3": "demos"},
    })]
    slack_env["history"]["C3"] = HTTPException(400, "not_in_channel")
    slack_env["catalog"] = [_Doc(
        "C3", "#demos",
        summary="Two demos booked this week; one pilot at risk.",
        topics=["demo scheduling", "customer pilot risk", "renewal churn"],
        doc_date="2026-08-05T20:51:11+00:00",
    )]

    result = voc.read(COMPANY)
    block = result.render()

    assert "### #demos" in block
    assert "NOT read live" in block
    assert "one pilot at risk" in block
    assert "renewal churn" in block
    assert "2026-08-05" in block                     # the summary is DATED
    # And it must not be passed off as a live read.
    assert "do NOT quote it as if you read it just now" in block
    assert result.read_channels and result.read_channels[0].channel.name == "product-feedback"
    assert [r.channel.name for r in result.stored_channels] == ["demos"]


def test_a_live_read_also_carries_its_stored_gist_labelled(slack_env):
    slack_env["rows"] = [_row({"sync_channel_ids": ["C1"],
                               "sync_channel_names": {"C1": "product-feedback"}})]
    slack_env["catalog"] = [_Doc(
        "C1", "#product-feedback", summary="VOC skill needs user input shaping.",
        topics=["VOC skill customization"], doc_date="2026-08-04T15:00:03+00:00",
    )]
    block = voc.read(COMPANY).render()
    assert "message(s) read live" in block
    assert "first in C1" in block                      # live messages
    assert "What this channel is about" in block       # and the gist, labelled
    assert "VOC skill needs user input shaping" in block


def test_catalog_channels_are_added_when_nothing_is_ticked(slack_env):
    """staging-test's shape: no explicit selection, so the product's contract is
    "read them all" — and the catalog holds channels the bot's CURRENT
    membership no longer lists. Covering only the intersection would make the
    answer narrower than either set the user can see."""
    slack_env["rows"] = [_row()]                       # no sync_channel_ids
    slack_env["catalog"] = [
        _Doc("C1", "#product-feedback", summary="a", topics=["x"]),
        _Doc("C9", "#working-group-for-june-9", summary="wg summary",
             topics=["github org"], doc_date="2026-08-04T20:39:41+00:00"),
    ]
    result = voc.read(COMPANY)
    labels = {r.channel.label for r in result.reads}
    assert "#working-group-for-june-9" in labels
    assert "C9" not in slack_env["calls"]     # added from storage, never read
    block = result.render()
    assert "wg summary" in block and "NOT read live" in block


def test_a_deselected_channel_does_not_resurface_from_the_catalog(slack_env):
    """`deregister_document` is never called for Slack, so a catalog row
    outlives the selection that created it — permanently. One live company has
    exactly this: #agent-escalations is in its catalog and NOT in its
    three-channel selection.

    An explicit selection is a deliberate narrowing. Putting a deselected
    channel's content back into a customer-feedback answer, from a row nothing
    ever collects, would quietly override that choice."""
    slack_env["rows"] = [_row({"sync_channel_ids": ["C1"],
                               "sync_channel_names": {"C1": "product-feedback"}})]
    slack_env["catalog"] = [
        _Doc("C1", "#product-feedback", summary="a", topics=["x"]),
        _Doc("C7", "#agent-escalations", summary="escalations",
             topics=["agent ship failures"]),
    ]
    result = voc.read(COMPANY)
    labels = {r.channel.label for r in result.reads}
    assert labels == {"#product-feedback"}
    assert "escalations" not in result.render()
    # The channel that IS selected still gets its stored gist.
    assert "a" in result.reads[0].stored.summary


def test_a_deselected_channel_does_not_resurface_when_slack_cannot_be_opened(
    slack_env, monkeypatch
):
    """THE GUARD'S WORST PATH, and the one the earlier version failed open on.

    The old guard was `selection == CONFIGURED and connected`. On the
    `open_session` failure path the selection had not been resolved yet, so
    `selection` was still its MEMBERSHIP default AND `connected` was False —
    both halves false, every catalog row appended, the deselected channel back
    in the answer. A guard whose inputs are computed after the early return it
    guards is not a guard.

    Fixture has a REAL selection AND a failing session — the combination the
    superseded test never actually built."""
    slack_env["rows"] = [_row({"sync_channel_ids": ["C1"],
                               "sync_channel_names": {"C1": "product-feedback"}})]
    monkeypatch.setattr(sl.PROVIDER, "open_session", lambda eid: None)
    slack_env["catalog"] = [
        _Doc("C1", "#product-feedback", summary="selected summary", topics=["x"]),
        _Doc("C7", "#agent-escalations", summary="deselected summary",
             topics=["t"]),
    ]
    result = voc.read(COMPANY)

    assert result.connected is False
    block = result.render()
    assert "selected summary" in block          # the ticked channel is served
    assert "deselected summary" not in block    # the unticked one is NOT
    assert "#agent-escalations" not in block


def test_a_dead_connection_with_no_selection_still_serves_stored_summaries(
    slack_env, monkeypatch
):
    """The narrowing is about not overriding a choice, not about hiding data
    when there is nothing else. With NOTHING ticked and Slack unopenable, the
    stored rows are all that is left and withholding them helps nobody.

    (The superseded version of this test set `rows = []`, so it had no
    connection and therefore no selection either — it passed without ever
    exercising the "even with a selection" case its name claimed.)"""
    slack_env["rows"] = [_row()]                # connected, nothing ticked
    monkeypatch.setattr(sl.PROVIDER, "open_session", lambda eid: None)
    slack_env["catalog"] = [_Doc("C7", "#agent-escalations",
                                 summary="escalations", topics=["t"])]
    result = voc.read(COMPANY)
    assert result.connected is False
    assert "escalations" in result.render()


def test_the_block_never_claims_the_wrong_provenance(slack_env, monkeypatch):
    """The header states where its own content came from. With a selection saved
    it must not say "no explicit channel selection is saved" — which is exactly
    what it said on the failure path, because `selection` was never resolved
    before the early return."""
    slack_env["rows"] = [_row({"sync_channel_ids": ["C1"],
                               "sync_channel_names": {"C1": "product-feedback"}})]
    monkeypatch.setattr(sl.PROVIDER, "open_session", lambda eid: None)
    slack_env["catalog"] = [_Doc("C1", "#product-feedback", summary="s",
                                 topics=["x"])]
    result = voc.read(COMPANY)
    assert result.selection == voc.SELECTION_CONFIGURED
    block = result.render()
    assert "no explicit channel selection is saved" not in block
    assert "Voice of Customer & Support" in block


def test_an_all_stored_block_does_not_claim_to_be_a_live_read(slack_env,
                                                              monkeypatch):
    """The header used to open "read live just now … 0 returned messages" over
    correctly-labelled stored sections — asserting a provenance its own content
    denies."""
    slack_env["rows"] = [_row()]
    monkeypatch.setattr(sl.PROVIDER, "open_session", lambda eid: None)
    slack_env["catalog"] = [_Doc("C3", "#demos", summary="stored gist",
                                 topics=["t"], doc_date="2026-08-05T00:00:00Z")]
    block = voc.read(COMPANY).render()
    assert "read live just now" not in block
    assert "NOTHING was read live this turn" in block
    assert "do not state message volumes" in block


def test_catalog_and_warmup_are_not_charged_to_the_fan_out_budget(slack_env,
                                                                  monkeypatch):
    """`budget_s` bounds the parallel fan-out — the part that can hang on a dead
    upstream. Charging the catalog round-trip and the directory warm-up to the
    same clock meant a slow `document_catalog` read consumed the whole budget
    and every channel came back TIMEOUT with zero reads attempted, reported as
    if Slack had been unresponsive."""
    import time as _t

    slack_env["rows"] = [_row({"sync_channel_ids": ["C1"],
                               "sync_channel_names": {"C1": "product-feedback"}})]

    def _slow_catalog(cid, **kw):
        _t.sleep(0.6)
        return []

    import app.document_catalog as dc
    monkeypatch.setattr(dc, "list_documents", _slow_catalog)

    result = voc.read(COMPANY, budget_s=0.5)
    assert slack_env["calls"] == ["C1"], "the channel must still be read"
    assert [r.status for r in result.reads] == [voc.STATUS_OK]
    assert result.budget_exceeded is False


def test_configured_but_never_ingested_says_so(slack_env):
    """Three live tenants have configured channels and ZERO catalog rows. Their
    answer must say "configured but nothing ingested and unreadable", never
    "no feedback found"."""
    slack_env["members"] = [{"id": "C1", "name": "product-feedback", "is_private": False}]
    slack_env["rows"] = [_row({
        "sync_channel_ids": ["C1", "C8"],
        "sync_channel_names": {"C1": "product-feedback", "C8": "all-sprntlyai"},
    })]
    slack_env["history"]["C8"] = HTTPException(400, "channel_not_found")
    slack_env["catalog"] = []

    block = voc.read(COMPANY).render()
    assert "NOT read, with NOTHING stored either" in block
    assert "#all-sprntlyai" in block or "C8" in block
    assert "never ingested this channel" in block
    assert "do not say they hold no feedback" in block


def test_a_dead_connection_still_answers_from_stored_summaries(slack_env):
    """Opening Slack failing is not the same as knowing nothing about the
    channels. The catalog is fetched BEFORE the session for exactly this."""
    slack_env["rows"] = []          # no usable Slack connection at all
    slack_env["catalog"] = [_Doc(
        "C3", "#demos", summary="demo scheduling and pilot risk",
        topics=["demo scheduling"], doc_date="2026-08-05T20:51:11+00:00",
    )]
    result = voc.read(COMPANY)
    assert result.connected is False
    assert result.present is True                   # stored content IS content
    block = result.render()
    assert "#demos" in block and "demo scheduling and pilot risk" in block
    assert "NOT read live" in block


def test_a_stored_summary_is_never_counted_as_a_live_read(slack_env):
    """`read_channels` means "read just now". If a stored summary ever leaked
    into it, the coverage banner would claim a live read that did not happen —
    the single worst failure available on this path."""
    slack_env["rows"] = [_row({"sync_channel_ids": ["C3"],
                               "sync_channel_names": {"C3": "demos"}})]
    slack_env["history"]["C3"] = HTTPException(400, "not_in_channel")
    slack_env["catalog"] = [_Doc("C3", "#demos", summary="s", topics=["t"])]
    result = voc.read(COMPANY)
    assert result.read_channels == []
    assert len(result.covered_channels) == 1
    assert result.present is True


def test_a_summaryless_catalog_row_contributes_nothing(slack_env):
    """One live row (#spryntly) has an empty summary. An empty stored summary
    must not mark a channel as covered.

    And with NOTHING readable at all the block stays empty — the sweep's rule,
    kept: a block saying only "I checked and found nothing" invites the model to
    assert the absence. The failure is not lost, it moves to the caller
    (`unread_channels` / the adapter tool / the digest's Slack-specific dead
    end), where it is stated as a read failure rather than as evidence."""
    slack_env["rows"] = [_row({"sync_channel_ids": ["C3"],
                               "sync_channel_names": {"C3": "demos"}})]
    slack_env["history"]["C3"] = HTTPException(400, "not_in_channel")
    slack_env["catalog"] = [_Doc("C3", "#demos", summary="", topics=[])]
    result = voc.read(COMPANY)
    assert result.present is False
    assert result.render() == ""
    assert [r.channel.label for r in result.unread_channels] == ["#demos"]
    assert "/invite @Sprntly" in result.unread_channels[0].reason()


def test_all_channels_unreadable_gives_slack_specific_guidance(slack_env, monkeypatch):
    """A Slack-only company whose channels are all unreadable must be told
    WHICH channel and what to do — not "connect Fireflies or Zoom", which is
    both wrong and unactionable when Slack is the voice source."""
    import app.call_digest as cd
    import app.graph.gateway as gateway_mod

    slack_env["rows"] = [_row({"sync_channel_ids": ["C3"],
                               "sync_channel_names": {"C3": "demos"}})]
    slack_env["history"]["C3"] = HTTPException(400, "not_in_channel")
    slack_env["catalog"] = []
    monkeypatch.setattr(cd, "_load_api_key", lambda cid: None)
    monkeypatch.setattr(cd, "_voice_docs", lambda cid, w: [])
    monkeypatch.setattr(cd, "_zoom_context", lambda cid: None)
    monkeypatch.setattr(cd, "build_kg_context", lambda *a, **k: cd.KgContext())

    spent: list = []
    monkeypatch.setattr(
        gateway_mod, "llm_call", lambda **kw: spent.append(kw) or None
    )
    payload = cd.answer(enterprise_id=COMPANY, question="what are customers saying?")

    assert spent == []                      # no spend with nothing to summarize
    assert "#demos" in payload["answer"]
    assert "not an absence of feedback" in payload["answer"]
    assert "Fireflies" not in payload["answer"]


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
        "what came through the channels we connected for voice of customer",
    ]:
        assert is_voc_report_request(q), q
        assert "slack" not in q.lower()
        assert not is_connector_lookup(q), q


def test_the_rules_reach_the_phrasings_people_actually_use():
    """RECALL IS THE FEATURE. The requirement is that a user reaches their Slack
    feedback channels without naming Slack; a rule set that only fires on "what
    are our customers saying" ships a capability almost nobody can reach. These
    eleven are natural phrasings measured against the pre-widening rules, where
    only three matched."""
    from app.skill_router import is_voc_report_request

    for q in [
        "any complaints about the new pricing?",
        "what feedback did we get on onboarding",
        "what's the sentiment on the redesign",
        "what pain points came up this week",
        "how did people react to the pricing change",
        "what's the biggest problem our customers have",
        "what did customers think of the new checkout",
        "anything customers are unhappy about",
        "what's been happening in our feedback channels",
        "anything new in the support channels?",
    ]:
        assert is_voc_report_request(q), q
        assert "slack" not in q.lower()


def test_ambiguous_nouns_need_a_customer_noun_to_count_as_voice():
    """A DELIBERATE RECALL LOSS, recorded so it is not silently reverted.

    `issues`, `problems`, `asks` and `requests` are equally at home in a tracker
    query and a release-status question, so a ranking word alone is not enough —
    they need a customer noun nearby. The cost is the first list: those no
    longer route here. The benefit is the second: a release-status question
    naming no source ("main issues blocking the release") used to return a
    customer-feedback report, and because it names no source nothing downstream
    could stand it back down.

    The asymmetry is the reason to accept the trade. A miss falls through to the
    behaviour that existed before this PR; a false positive REPLACES the answer
    the user asked for."""
    from app.skill_router import is_voc_report_request

    for q in [                                   # given up, knowingly
        "what are the top issues right now",
        "what are the main problems",
    ]:
        assert not is_voc_report_request(q), q
    for q in [                                   # what the narrowing protects
        "what are the top issues in Jira",
        "what are the main issues blocking the release",
        "what are the biggest blockers this sprint",
    ]:
        assert not is_voc_report_request(q), q
    for q in [                                   # still claimed, customer named
        "what's the biggest problem our customers have",
        "what are the top customer requests",
        "the most common issues users hit",
    ]:
        assert is_voc_report_request(q), q


def test_a_real_voc_question_containing_an_action_verb_still_routes():
    """THE AXIS BOTH EARLIER SETS WERE MISSING, and the reason 14/14 and then
    22/22 both looked clean while the veto was eating real questions.

    Every one of these is a genuine customer-voice question that HAPPENS to
    contain a word the action veto keys on — because the verb is the CUSTOMER's
    own action ("can't log into the app", "can't file a claim as a guest") or an
    ordinary noun sitting in the topic ("order management", "delivery estimate",
    "triage flow", "the schedule screen").

    A position-free, object-free veto stood down 8 of 15 of these. Two guards
    fix it and both are needed: the verb must come BEFORE the customer/voice
    noun (`_vetoed_as_action`, the same rule `_vetoed_as_creation` uses), and
    the bare reprioritise verbs need a determiner-led object so the noun senses
    are excluded."""
    from app.skill_router import is_voc_report_request

    for q in [
        "users complain they can't log into the app",
        "what did users say about the ticket triage flow?",
        "what complaints do we have about order management?",
        "customers say our delivery estimate is always wrong",
        "what do customers say about the update to the ticket flow?",
        "users complain they can't file a claim as a guest",
        "what do customers think of the new order flow",
        "users say the schedule screen is confusing",
        "what feedback did we get about the assign-to-me button",
        "customers complain the rank ordering is wrong",
        "what are users saying about triage times",
        "what do customers say about how we estimate delivery",
    ]:
        assert is_voc_report_request(q), q


def test_recall_given_up_to_keep_precision():
    """THE LIMITS, WRITTEN DOWN — these are misses ON PURPOSE, not bugs.

    The bar for these rules is honestly narrower recall with no precision leaks.
    The asymmetry: a miss falls through to pre-feature routing (yesterday's
    answer); a false positive REPLACES the answer the user asked for.

    1. Wanting verbs. `want`/`need`/`ask` were admitted to catch "customers
       asking us to add X" and immediately turned four ordinary product
       questions into VoC reports — they are how people ask about MECHANICS as
       much as sentiment. No veto can separate the two, because neither is an
       action request; the only fix is not admitting the verbs.
    2. Ambiguous nouns with no customer noun (see the ambiguous-noun test).
    3. A voice noun with no topic marker, ranking word or speech verb.

    If a future change makes any of these route again, check it has not
    reopened `test_ordinary_questions_with_wanting_verbs_are_not_voc`."""
    from app.skill_router import is_voc_report_request

    for q in [
        "what are customers asking for",
        "what features are customers asking us to add to the roadmap?",
        "what do customers want us to prioritize next quarter?",
        "clients keep asking us to add dark mode",
        "what are the top issues right now",
        "estimate accuracy is a common complaint",
    ]:
        assert not is_voc_report_request(q), q


def test_ordinary_questions_with_wanting_verbs_are_not_voc():
    """THE LEAK THE ABOVE BUYS BACK. Each of these carries a customer noun and a
    wanting verb while asking about mechanics, and each became a
    voice-of-customer report when `want`/`need`/`ask` were in the speech-verb
    list. They are not action requests, so no veto guards them."""
    from app.skill_router import is_voc_report_request

    for q in [
        "what do users need to do to reset a password?",
        "what permissions does a user need to publish?",
        "what plan do customers need for SSO?",
        "the client asked for a demo on Friday, add it to the calendar",
    ]:
        assert not is_voc_report_request(q), q


def test_a_subject_first_command_is_still_vetoed():
    """POSITION CANNOT CATCH THESE, structurally — the subject noun comes first,
    so `veto.start() < subject.start()` is false and the veto stands down.

    The fix is anchoring on the action's OBJECT: a work artifact
    (ticket/bug/backlog/roadmap) can only be the target of a command, never part
    of a customer question. That tier ignores position entirely.

    This is the same insight `_vetoed_as_creation` already embodies — it
    compares against `_JIRA_PM_NOUN`, the ARTIFACT, which lands after the verb,
    whereas `_vetoed_as_action` compared against the SUBJECT, which lands before
    it. Checked directly: `_vetoed_as_creation` does NOT have this hole."""
    from app.skill_router import _vetoed_as_creation, is_voc_report_request

    for q in [
        "take the customer complaints and turn them into tickets",
        "the top complaints - add them to the backlog",
        "customer requests: prioritize the top ones",
        "these user complaints should go onto the roadmap",
        "grab what customers are saying and file it as a bug",
        "prioritize what customers are asking for",
        "move the top complaints into the sprint",
    ]:
        assert not is_voc_report_request(q), q

    # The creation veto's equivalent shape, confirmed sound rather than assumed.
    for q in [
        "take the customer complaints and write a PRD for them",
        "the top complaints - draft tickets for them",
        "customer feedback: generate a spec from it",
    ]:
        assert _vetoed_as_creation(q), q


def test_the_object_constraint_is_load_bearing():
    """The reprioritise family requires a determiner-led object, and that is NOT
    redundant with the position check — a review pass removed it, kept position,
    saw zero false vetoes, and concluded it was dead weight.

    It is not. When a sentence STARTS with the noun sense, the bare verb sits
    before the customer noun, so position votes to veto and only the object
    constraint saves it. Each of these is a real customer-voice question that a
    bare-verb alternation would stand down."""
    from app.skill_router import is_voc_report_request

    for q in [
        "order management is what customers complain about",
        "estimate accuracy is what users complain about",
        "schedule changes are a common customer complaint",
    ]:
        assert is_voc_report_request(q), q


def test_an_action_request_is_never_diverted_into_a_voc_report():
    """The verb families `_vetoed_as_creation` does NOT cover — transform,
    append, reprioritise, amend. Every one carries a customer noun and a
    feedback noun on its way to asking for work to be done, so every recall rule
    matches them, and returning a report instead is a regression against pre-PR
    behaviour. "update the PRD with what customers are saying" is the sharpest:
    the user wants a PRD edited and got a report.

    These came from an INDEPENDENT phrasing set, not the one the rules were
    written against — the first set measured 14/14 and this one found six
    leaks."""
    from app.skill_router import is_voc_report_request

    for q in [
        "turn what customers are asking for into tickets",
        "add the top complaints to the backlog",
        "prioritize the top customer requests",
        "update the PRD with what customers are saying",
        "move the top complaints into the sprint",
        "convert customer feedback into epics",
        "file the user complaints as bugs",
        "rank the user requests for next sprint",
        "triage the user issues",
        "estimate the customer requests",
        "fold what customers are saying into the PRD",
        "incorporate customer feedback into the spec",
        "assign the top user complaints to me",
        "append the customer requests to the roadmap",
        "promote the top customer requests into epics",
        # "file a ticket FOR …" has no into/onto/as, so the transform rule
        # missed it and `_RANK_WORD + pain points` then claimed it.
        "file a ticket for the top pain points",
        "raise a bug for the top complaint",
        "open a ticket for the biggest complaint",
    ]:
        assert not is_voc_report_request(q), q


def test_a_generative_request_is_never_diverted_into_a_voc_report():
    """PRECISION, and the half that pulls AGAINST the recall above — which is
    why they are one change.

    Each of these carries a customer noun and a feedback noun on its way to
    asking for an ARTIFACT. `is_voc_report_request` is consulted before
    `route()`, so capturing one returns a voice-of-customer report instead of
    the PRD or tickets the user asked for. The first two are the reported
    regression; the rest are the same shape."""
    from app.skill_router import is_voc_report_request

    for q in [
        "write a PRD for the feature customers are asking for",
        "our users are frustrated with onboarding - draft tickets for it",
        "draft a PRD from what customers are saying",
        "create tickets for the top customer complaints",
        "generate a spec for the thing users keep requesting",
        "build a prototype for the top customer request",
    ]:
        assert not is_voc_report_request(q), q


def test_the_widened_rules_do_not_divert_ordinary_questions():
    """The precision half. These carry a customer-noun, a channel-noun or a
    feedback-noun and must still reach normal routing — a VoC rule that claims
    them turns every product question into a feedback report."""
    from app.skill_router import is_voc_report_request

    for q in [
        "which channels do we sell through?",
        "what are our customers' plan tiers",
        "how many users signed up last week",
        "generate a PRD for the onboarding channel picker",
        "what is our sales channel strategy",
        "summarize this document",
        # Feedback on OUR OWN work is not voice of customer. Rule 1 of the
        # recall set would claim the first of these without the veto.
        "give me feedback on my PRD draft",
        "summarize the feedback from the beta survey",
        "we built this from customer feedback",
        "draft a launch email for customers",
        "update the ticket description",
        "what is our churn rate",
        "show me the open PRs",
        "what did we ship last sprint",
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
    assert "NOT read and NOTHING stored: #founders" in banner
    assert "coverage caveat" in banner


def test_the_kill_switch_gates_the_choke_point_not_the_call_sites(slack_env, monkeypatch):
    """Off means off for EVERY caller, including the adapter tool — the sweep's
    2026-08-05 lesson, where a flag checked at one of two entry points left half
    the feature running. Asserted by going in through a DIFFERENT door than the
    digest does and confirming no Slack call is made."""
    from app.config import settings

    monkeypatch.setattr(settings, "slack_voc_channels", False, raising=False)
    slack_env["rows"] = [_row({"sync_channel_ids": ["C1", "C2"]})]

    result = voc.read(COMPANY)
    assert result.render() == ""
    assert "switched off" in result.unavailable

    session = sl.PROVIDER.open_session(COMPANY)
    out = sl.PROVIDER.dispatch(session, "slack_voc_channels", {})
    assert "switched off" in out
    assert slack_env["calls"] == []          # no channel was read by any route


def test_the_digest_banner_separates_live_reads_from_stored_summaries(
    slack_env, monkeypatch
):
    """The banner is what a user reads to check coverage. A stored summary must
    appear there NAMED and DATED, and must never be folded into the live-read
    count — that blur is what would license "customers said X this week" from a
    summary written a week ago."""
    import app.call_digest as cd
    import app.graph.gateway as gateway_mod

    slack_env["rows"] = [_row({
        "sync_channel_ids": ["C1", "C3"],
        "sync_channel_names": {"C1": "product-feedback", "C3": "demos"},
    })]
    slack_env["history"]["C3"] = HTTPException(400, "not_in_channel")
    slack_env["catalog"] = [_Doc(
        "C3", "#demos", summary="demo scheduling and renewal churn",
        topics=["renewal churn"], doc_date="2026-08-05T20:51:11+00:00",
    )]
    monkeypatch.setattr(cd, "_load_api_key", lambda cid: None)
    monkeypatch.setattr(cd, "_voice_docs", lambda cid, w: [])
    monkeypatch.setattr(cd, "_zoom_context", lambda cid: None)
    monkeypatch.setattr(cd, "build_kg_context", lambda *a, **k: cd.KgContext())

    captured: dict = {}

    class _R:
        output = {"answer": "themes", "key_points": [], "citations": [],
                  "confidence": 0.6, "unanswered": ""}

    monkeypatch.setattr(
        gateway_mod, "llm_call", lambda **kw: (captured.update(kw), _R())[1]
    )
    cd.answer(enterprise_id=COMPANY, question="what are our customers saying?")

    banner = captured["input"].split("===")[1]
    assert "1 customer-feedback channel" in banner          # live count is ONE
    assert "#product-feedback" in banner
    assert "covered ONLY by a stored, dated summary" in banner
    assert "#demos" in banner and "2026-08-05" in banner
    assert "never what was said in them during this window" in banner
    # And the summary itself reached the corpus, so the answer can use it.
    assert "demo scheduling and renewal churn" in captured["input"]


def test_a_connected_company_with_no_channels_is_not_told_to_connect_fireflies(
    slack_env, monkeypatch
):
    """The most likely shape in the live data: Slack connected, no explicit
    selection anywhere in the fleet, bot in no channel — `connected=True,
    reads=0, render=""`. Gating the dead end on `render()` (or on `voc.reads`)
    sent this company to "no call source is connected yet. Connect Fireflies or
    Zoom", while its Slack sat connected and working. `has_call_source` returns
    True for exactly these companies, so the digest CLAIMS the turn and has to
    be able to finish it."""
    import app.call_digest as cd
    import app.graph.gateway as gateway_mod

    slack_env["rows"] = [_row()]        # connected, no selection
    slack_env["members"] = []           # bot in no channel
    slack_env["catalog"] = []
    monkeypatch.setattr(cd, "_load_api_key", lambda cid: None)
    monkeypatch.setattr(cd, "_voice_docs", lambda cid, w: [])
    monkeypatch.setattr(cd, "_zoom_context", lambda cid: None)
    monkeypatch.setattr(cd, "build_kg_context", lambda *a, **k: cd.KgContext())

    spent: list = []
    monkeypatch.setattr(
        gateway_mod, "llm_call", lambda **kw: spent.append(kw) or None
    )

    assert cd.has_call_source(COMPANY) is True      # the turn IS claimed
    payload = cd.answer(enterprise_id=COMPANY, question="what are customers saying?")

    assert spent == []
    assert "Fireflies" not in payload["answer"]
    assert "Zoom" not in payload["answer"]
    assert "invite @Sprntly" in payload["answer"]
    assert "Voice of Customer & Support" in payload["answer"]
    assert "not a sign that customers have said nothing" in payload["answer"]


def test_the_total_char_ceiling_drops_a_whole_channel_and_says_so(slack_env,
                                                                  monkeypatch):
    """The overflow path DROPS a whole low-priority channel rather than cutting
    one mid-message — a half-rendered channel reads as a complete one. Honest
    today and previously unpinned, so a future edit could start silently
    truncating with every test still green."""
    monkeypatch.setattr(voc, "TOTAL_CHARS", 200)
    slack_env["rows"] = [_row({
        "sync_channel_ids": ["C1", "C2", "C3"],
        "sync_channel_names": {
            "C1": "product-feedback", "C2": "support-escalations", "C3": "demos",
        },
    })]
    for cid in ("C1", "C2", "C3"):
        slack_env["history"][cid] = [
            {"ts": f"178000000{i}.1", "user": "U1", "text": f"{cid} msg {i} " + "x" * 80}
            for i in range(4)
        ]

    result = voc.read(COMPANY)
    dropped = [r for r in result.reads if r.status == voc.STATUS_DROPPED]
    assert dropped, "the ceiling must drop a channel, not trim every one"
    # A dropped channel carries NO partial text and IS named with its reason.
    for r in dropped:
        assert r.text == ""
        assert "dropped from this prompt for length" in r.reason()
    block = result.render()
    for r in dropped:
        assert r.channel.label in block
    # THE AGGREGATE NEVER VANISHES. Without a first-channel floor, one channel
    # bigger than the ceiling drops every channel behind it, render() finds
    # nothing usable and returns "", and the whole block disappears with
    # nothing said about it — the silent-absence failure reached through the
    # length path instead of the read path.
    kept = [r for r in result.reads if r.usable]
    assert kept, "the ceiling must not drop everything"
    assert block, "the block must never silently vanish to the char ceiling"


def test_the_per_channel_cap_cannot_exceed_the_total_ceiling():
    """A constant that must dominate another is only correct on the day it is
    written unless the relationship itself is asserted. If `PER_CHANNEL_CHARS`
    ever grows past `TOTAL_CHARS`, the drop path stops being a rare overflow
    and becomes the common case — this fails loudly instead."""
    assert voc.PER_CHANNEL_CHARS <= voc.TOTAL_CHARS


def test_a_stored_only_answer_does_not_report_zero_slack_channels(slack_env,
                                                                  monkeypatch):
    """`VocRead.present` is true for a stored-only contribution, so a run line
    counting `read_channels` printed "+ 0 Slack feedback channels" on an answer
    partly built from Slack — a run line contradicting its own corpus."""
    import app.call_digest as cd
    import app.graph.gateway as gateway_mod

    slack_env["rows"] = [_row()]
    monkeypatch.setattr(sl.PROVIDER, "open_session", lambda eid: None)
    slack_env["catalog"] = [_Doc("C3", "#demos", summary="stored gist",
                                 topics=["t"], doc_date="2026-08-05T00:00:00Z")]
    monkeypatch.setattr(cd, "_load_api_key", lambda cid: None)
    monkeypatch.setattr(cd, "_voice_docs", lambda cid, w: [])
    monkeypatch.setattr(cd, "_zoom_context", lambda cid: None)
    monkeypatch.setattr(cd, "build_kg_context", lambda *a, **k: cd.KgContext())

    class _R:
        output = {"answer": "themes", "key_points": [], "citations": [],
                  "confidence": 0.6, "unanswered": ""}

    monkeypatch.setattr(gateway_mod, "llm_call", lambda **kw: _R())
    payload = cd.answer(enterprise_id=COMPANY, question="what are customers saying?")

    action = payload["_skill_action"]
    assert "0 Slack feedback channels" not in action
    assert "1 Slack feedback channel" in action


def test_the_report_path_run_line_counts_slack_channels(slack_env, monkeypatch):
    """The surviving mutation from the review: the REPORT path's run-line count
    was unpinned, because every existing test asks a query-shaped question and
    reaches `_answer_query` instead. A report-shaped ask is needed to exercise
    it at all.

    Also pins the pluralisation — the label read "1 Slack feedback channels"
    because the 's' was hardcoded.

    THE FIXTURE HAS TO MAKE `live != covered`, or the test is vacuous. An
    earlier version read one channel live with an empty catalog, so
    `live == covered == 1` and the `covered → read_channels` mutation survived
    — it pinned the plural and nothing else. Here the only channel is
    STORED-ONLY (live=0, covered=1), which is exactly the case the fix was
    about: a run line counting live reads printed "0 Slack feedback channels"
    on an answer partly built from Slack."""
    import app.call_digest as cd
    import app.graph.gateway as gateway_mod

    slack_env["rows"] = [_row()]
    monkeypatch.setattr(sl.PROVIDER, "open_session", lambda eid: None)
    slack_env["catalog"] = [_Doc("C3", "#demos", summary="stored gist",
                                 topics=["t"], doc_date="2026-08-05T00:00:00Z")]
    monkeypatch.setattr(cd, "_load_api_key", lambda cid: None)
    monkeypatch.setattr(cd, "_voice_docs", lambda cid, w: [])
    monkeypatch.setattr(cd, "_zoom_context", lambda cid: None)
    monkeypatch.setattr(cd, "build_kg_context", lambda *a, **k: cd.KgContext())
    # Report-shaped, not query-shaped — this is what reaches the report path.
    monkeypatch.setattr(cd, "is_voc_query", lambda q: False)

    class _R:
        output = {"answer": "## Voice of customer", "key_points": [],
                  "citations": [], "confidence": 0.6, "unanswered": ""}

    monkeypatch.setattr(gateway_mod, "llm_call", lambda **kw: _R())
    payload = cd.answer(
        enterprise_id=COMPANY, question="give me a voice of customer report"
    )

    action = payload["_skill_action"]
    assert "1 Slack feedback channel" in action
    assert "1 Slack feedback channels" not in action     # pluralisation
    assert "0 Slack feedback" not in action              # covered, not live


def test_config_keys_match_the_sync_paths_keys():
    """slack_voc holds its own copies of the two config keys so it stays
    importable without the sync path. Copies drift; this is the assertion that
    makes them fail loudly instead."""
    assert voc.CONFIG_SYNC_CHANNEL_IDS == slack_sync.CONFIG_SYNC_CHANNEL_IDS
    assert voc.CONFIG_SYNC_CHANNEL_NAMES == slack_sync.CONFIG_SYNC_CHANNEL_NAMES
