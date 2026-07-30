"""Tests for the Slack sync pull-channel selection (app/connectors/slack_sync).

The user picks which channels the corpus sync reads (saved by
POST /v1/connectors/slack/sync-channels as sync_channel_ids /
sync_channel_names on the connection config); select_sync_channels applies
that selection to the bot-visible channel list. No selection = legacy
behavior (every channel the bot is a member of).
"""
from __future__ import annotations

from app.connectors.slack_sync import (
    CONFIG_SYNC_CHANNEL_IDS,
    CONFIG_SYNC_CHANNEL_NAMES,
    select_sync_channels,
)


def _ch(cid: str, name: str) -> dict:
    return {"id": cid, "name": name}


CHANNELS = [_ch("C1", "general"), _ch("C2", "support"), _ch("C3", "random")]


def test_no_selection_keeps_all_channels():
    kept, errors = select_sync_channels(CHANNELS, {})
    assert kept == CHANNELS
    assert errors == []


def test_empty_selection_keeps_all_channels():
    """An empty stored list means 'no selection', not 'sync nothing'."""
    kept, errors = select_sync_channels(
        CHANNELS, {CONFIG_SYNC_CHANNEL_IDS: []}
    )
    assert kept == CHANNELS
    assert errors == []


def test_selection_filters_and_orders():
    """Only selected channels are kept, in selection order."""
    kept, errors = select_sync_channels(
        CHANNELS, {CONFIG_SYNC_CHANNEL_IDS: ["C3", "C1"]}
    )
    assert [c["id"] for c in kept] == ["C3", "C1"]
    assert errors == []


def test_unavailable_selected_channel_reported_by_name():
    """A selected channel the bot can't see is skipped with an error naming
    it (via the stored names map) — the sync proceeds with the rest."""
    config = {
        CONFIG_SYNC_CHANNEL_IDS: ["C2", "C9"],
        CONFIG_SYNC_CHANNEL_NAMES: {"C9": "customer-vips"},
    }
    kept, errors = select_sync_channels(CHANNELS, config)
    assert [c["id"] for c in kept] == ["C2"]
    assert len(errors) == 1
    assert "customer-vips" in errors[0]
    assert "not in this channel" in errors[0]


def test_unavailable_channel_without_name_falls_back_to_id():
    kept, errors = select_sync_channels(
        CHANNELS, {CONFIG_SYNC_CHANNEL_IDS: ["C9"]}
    )
    assert kept == []
    assert len(errors) == 1
    assert "C9" in errors[0]
