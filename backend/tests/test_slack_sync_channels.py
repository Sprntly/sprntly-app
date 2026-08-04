"""Tests for the Slack sync pull-channel selection (app/connectors/slack_sync).

The user picks which channels the corpus sync reads (saved by
POST /v1/connectors/slack/sync-channels as sync_channel_ids /
sync_channel_names on the connection config); select_sync_channels applies
that selection to the bot-visible channel list. No selection = legacy
behavior (every channel the bot is a member of).

Unticking is the reverse of ticking, so this file also covers the teardown
half: remove_channels_from_corpus takes an unticked channel's messages back
out of slack_channels.md, and purge_channels_from_synced_data sweeps every
dataset the company owns before re-seeding the KG the way a sync does.
"""
from __future__ import annotations

from pathlib import Path

import pytest

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


# ── resolve_company_slack_row — which install is "the company's" ────────────
#
# Voice-of-customer pulling is company-level: one Slack row serves the whole
# company. Preference: the active row carrying a pull-channel selection
# (latest-updated wins), else the oldest active install (the PM's).

def _row(user_id: str, *, status: str = "active", config: dict | None = None,
         created: str = "2026-07-01", updated: str = "2026-07-01") -> dict:
    import json as _json

    return {
        "user_id": user_id,
        "status": status,
        "config_json": _json.dumps(config or {}),
        "created_at": created,
        "updated_at": updated,
    }


def test_resolve_prefers_row_with_selection(monkeypatch):
    from app import db
    from app.connectors.slack_company import resolve_company_slack_row

    rows = [
        _row("u-pm", created="2026-07-01"),
        _row("u-eng", created="2026-07-10",
             config={CONFIG_SYNC_CHANNEL_IDS: ["C1"]}),
    ]
    monkeypatch.setattr(db, "list_slack_connections", lambda _cid: rows)
    assert resolve_company_slack_row("co-1")["user_id"] == "u-eng"


def test_resolve_falls_back_to_oldest_active_install(monkeypatch):
    from app import db
    from app.connectors.slack_company import resolve_company_slack_row

    rows = [
        _row("u-late", created="2026-07-20"),
        _row("u-pm", created="2026-07-01"),
        _row("u-gone", status="revoked", created="2026-06-01"),
    ]
    monkeypatch.setattr(db, "list_slack_connections", lambda _cid: rows)
    assert resolve_company_slack_row("co-1")["user_id"] == "u-pm"


def test_resolve_latest_updated_selection_wins(monkeypatch):
    from app import db
    from app.connectors.slack_company import resolve_company_slack_row

    rows = [
        _row("u-old-cfg", config={CONFIG_SYNC_CHANNEL_IDS: ["C1"]},
             updated="2026-07-05"),
        _row("u-new-cfg", config={CONFIG_SYNC_CHANNEL_IDS: ["C2"]},
             updated="2026-07-25"),
    ]
    monkeypatch.setattr(db, "list_slack_connections", lambda _cid: rows)
    assert resolve_company_slack_row("co-1")["user_id"] == "u-new-cfg"


def test_resolve_none_when_no_active_install(monkeypatch):
    from app import db
    from app.connectors.slack_company import resolve_company_slack_row

    monkeypatch.setattr(
        db, "list_slack_connections",
        lambda _cid: [_row("u-gone", status="revoked")],
    )
    assert resolve_company_slack_row("co-1") is None


# ── remove_channels_from_corpus — unticking takes the messages back out ────
#
# Unticking a channel used to leave everything it had already pulled in the
# corpus forever. slack_channels.md is one `## #<name>` section per channel
# plus one Channels Overview row per channel, so a channel IS addressable
# here — these pin that the right slice goes and the rest survives intact.

def _corpus_doc() -> str:
    """A slack_channels.md shaped exactly like sync_slack assembles one:
    header counts, Channels Overview table, `---`, then a body section per
    channel."""
    return (
        "# Slack Workspace Messages\n"
        "\n"
        "**Synced:** 2026-08-03 10:00 UTC\n"
        "**History window:** last 90 days\n"
        "**Channels:** 3 | **Messages:** 6 | **Thread replies:** 2\n"
        "\n"
        "## Channels Overview\n"
        "\n"
        "**Total channels synced:** 3\n"
        "\n"
        "| Channel | Members | Messages Synced | Topic |\n"
        "|---------|---------|-----------------|-------|\n"
        "| #customer-feedback | 12 | 3 | Voice of customer |\n"
        "| #support | 8 | 2 | Help desk |\n"
        "| #random | 4 | 1 |  |\n"
        "\n---\n\n"
        "## #customer-feedback\n"
        "\n"
        "**Alice** (2026-08-01):\n"
        "Checkout keeps timing out on the billing step.\n"
        "\n"
        "## #support\n"
        "\n"
        "**Bob** (2026-08-02):\n"
        "Ticket 42 reopened by the customer.\n"
        "\n"
        "## #random\n"
        "\n"
        "**Cara** (2026-08-02):\n"
        "Coffee run?\n"
    )


@pytest.fixture
def slack_corpus(isolated_settings, tmp_data_dir, monkeypatch) -> Path:
    """A tmp DATA_DIR that `slack_sync` will actually read.

    slack_sync does `from app.config import settings` at import time and is
    NOT in conftest's _RELOAD_ORDER, so it can still be holding a Settings
    object built from an EARLIER test's DATA_DIR — the module would then trim
    a corpus in a directory this test never wrote to and every assertion here
    would pass against an untouched file. Rebinding the module global to the
    freshly-reloaded settings pins it to this test's tmp dir.
    """
    import app.config as config_mod
    from app.connectors import slack_sync

    monkeypatch.setattr(slack_sync, "settings", config_mod.settings)
    return Path(tmp_data_dir)


def _write_corpus(base: Path, slug: str = "acme", text: str | None = None) -> Path:
    d = Path(base) / slug
    d.mkdir(parents=True, exist_ok=True)
    p = d / "slack_channels.md"
    p.write_text(text if text is not None else _corpus_doc(), encoding="utf-8")
    return p


def test_remove_channel_drops_its_section_and_keeps_the_others(slack_corpus):
    from app.connectors.slack_sync import remove_channels_from_corpus

    path = _write_corpus(slack_corpus)
    assert remove_channels_from_corpus("acme", ["support"]) == 1

    out = path.read_text(encoding="utf-8")
    # The unticked channel is gone — heading, table row and MESSAGES.
    assert "## #support" not in out
    assert "| #support |" not in out
    assert "Ticket 42 reopened" not in out
    # Everything else survives untouched.
    assert "## #customer-feedback" in out
    assert "Checkout keeps timing out" in out
    assert "## #random" in out
    assert "Coffee run?" in out


def test_remove_channel_rewrites_the_count_lines(slack_corpus):
    """A doc that still claims 3 channels above 2 sections is a doc an LLM
    will reason about a channel from. Channels and messages are both
    recomputed from the surviving table rows."""
    from app.connectors.slack_sync import remove_channels_from_corpus

    path = _write_corpus(slack_corpus)
    remove_channels_from_corpus("acme", ["support"])
    out = path.read_text(encoding="utf-8")

    assert "**Total channels synced:** 2" in out
    # 6 messages minus #support's 2; thread replies are never broken down per
    # channel in the doc, so that figure rides through to the next full sync.
    assert "**Channels:** 2 | **Messages:** 4 | **Thread replies:** 2" in out


def test_remove_several_channels_at_once(slack_corpus):
    from app.connectors.slack_sync import remove_channels_from_corpus

    path = _write_corpus(slack_corpus)
    assert remove_channels_from_corpus("acme", ["support", "random"]) == 2
    out = path.read_text(encoding="utf-8")
    assert "Ticket 42 reopened" not in out
    assert "Coffee run?" not in out
    assert "Checkout keeps timing out" in out
    assert "**Total channels synced:** 1" in out


def test_removing_every_channel_deletes_the_doc(slack_corpus):
    """An empty Slack doc still reads as a Slack source with nothing in it —
    delete the file instead of leaving a hollow one behind."""
    from app.connectors.slack_sync import remove_channels_from_corpus

    path = _write_corpus(slack_corpus)
    assert remove_channels_from_corpus(
        "acme", ["customer-feedback", "support", "random"]
    ) == 3
    assert not path.exists()


def test_remove_channel_is_case_and_hash_insensitive(slack_corpus):
    """Stored display names may be capitalised or already carry a '#'."""
    from app.connectors.slack_sync import remove_channels_from_corpus

    path = _write_corpus(slack_corpus)
    assert remove_channels_from_corpus("acme", ["#Support"]) == 1
    assert "Ticket 42 reopened" not in path.read_text(encoding="utf-8")


def test_remove_channel_keeps_a_message_that_looks_like_a_heading(slack_corpus):
    """Message text lands in the doc verbatim, so someone posting '## notes'
    must not terminate the removed section early and strand the rest of that
    channel's conversation in the corpus."""
    from app.connectors.slack_sync import remove_channels_from_corpus

    doc = (
        "# Slack Workspace Messages\n"
        "\n"
        "**Channels:** 2 | **Messages:** 2 | **Thread replies:** 0\n"
        "\n"
        "## Channels Overview\n"
        "\n"
        "**Total channels synced:** 2\n"
        "\n"
        "| Channel | Members | Messages Synced | Topic |\n"
        "|---------|---------|-----------------|-------|\n"
        "| #support | 8 | 1 | Help desk |\n"
        "| #random | 4 | 1 |  |\n"
        "\n---\n\n"
        "## #support\n"
        "\n"
        "**Bob** (2026-08-02):\n"
        "## incident notes\n"
        "Card declined for three enterprise accounts.\n"
        "\n"
        "## #random\n"
        "\n"
        "**Cara** (2026-08-02):\n"
        "Coffee run?\n"
    )
    path = _write_corpus(slack_corpus, text=doc)
    assert remove_channels_from_corpus("acme", ["support"]) == 1
    out = path.read_text(encoding="utf-8")
    assert "incident notes" not in out
    assert "Card declined" not in out
    assert "Coffee run?" in out


def test_remove_channel_not_in_the_doc_is_a_noop(slack_corpus):
    from app.connectors.slack_sync import remove_channels_from_corpus

    path = _write_corpus(slack_corpus)
    before = path.read_text(encoding="utf-8")
    assert remove_channels_from_corpus("acme", ["never-synced"]) == 0
    assert path.read_text(encoding="utf-8") == before


def test_remove_channel_without_a_corpus_file_is_a_noop(slack_corpus):
    """A company that never ran a Slack sync has nothing to trim — that is a
    0, not an error the save has to survive."""
    from app.connectors.slack_sync import remove_channels_from_corpus

    assert remove_channels_from_corpus("never-synced-slug", ["support"]) == 0


def test_remove_channel_with_empty_name_list_is_a_noop(slack_corpus):
    from app.connectors.slack_sync import remove_channels_from_corpus

    path = _write_corpus(slack_corpus)
    before = path.read_text(encoding="utf-8")
    assert remove_channels_from_corpus("acme", ["", "   "]) == 0
    assert path.read_text(encoding="utf-8") == before


# ── purge_channels_from_synced_data — every dataset the company owns ───────


def test_purge_sweeps_every_owned_dataset_and_reseeds(slack_corpus, monkeypatch):
    """The scheduled refresh writes to the company slug, but a manual sync can
    write into a workspace dataset — a channel has to leave both."""
    from app.connectors import slack_sync

    _write_corpus(slack_corpus, "acme")
    _write_corpus(slack_corpus, "acme--growth")
    monkeypatch.setattr(
        slack_sync, "company_dataset_slugs",
        lambda _cid: ["acme", "acme--growth"],
    )
    seeded: list[tuple[str, str]] = []
    import app.kg_ingest.auto_sync as auto_sync

    monkeypatch.setattr(
        auto_sync, "kickoff_corpus_seed",
        lambda cid, slug: seeded.append((cid, slug)) or True,
    )

    out = slack_sync.purge_channels_from_synced_data("co-1", ["support"])

    assert out["sections_removed"] == 2
    assert out["datasets"] == ["acme", "acme--growth"]
    assert out["reseeded"] == ["acme", "acme--growth"]
    assert seeded == [("co-1", "acme"), ("co-1", "acme--growth")]


def test_purge_skips_the_reseed_when_nothing_was_removed(slack_corpus, monkeypatch):
    """No trim, no re-extraction — a re-seed for an unchanged doc is pure cost."""
    from app.connectors import slack_sync

    _write_corpus(slack_corpus, "acme")
    monkeypatch.setattr(
        slack_sync, "company_dataset_slugs", lambda _cid: ["acme"])
    seeded: list = []
    import app.kg_ingest.auto_sync as auto_sync

    monkeypatch.setattr(
        auto_sync, "kickoff_corpus_seed",
        lambda cid, slug: seeded.append(slug) or True,
    )

    out = slack_sync.purge_channels_from_synced_data("co-1", ["never-synced"])
    assert out["sections_removed"] == 0
    assert seeded == []


def test_purge_with_no_names_does_nothing(slack_corpus, monkeypatch):
    from app.connectors import slack_sync

    called: list = []
    monkeypatch.setattr(
        slack_sync, "company_dataset_slugs",
        lambda _cid: called.append(_cid) or ["acme"],
    )
    out = slack_sync.purge_channels_from_synced_data("co-1", [])
    assert out == {"datasets": [], "sections_removed": 0, "reseeded": []}
    assert called == []


def test_purge_survives_one_bad_dataset(slack_corpus, monkeypatch):
    """Cleanup runs behind a save that already committed — one dataset
    blowing up must not stop the others or reach the caller."""
    from app.connectors import slack_sync

    _write_corpus(slack_corpus, "acme")
    monkeypatch.setattr(
        slack_sync, "company_dataset_slugs", lambda _cid: ["broken", "acme"])
    real = slack_sync.remove_channels_from_corpus

    def flaky(dataset, names):
        if dataset == "broken":
            raise RuntimeError("disk on fire")
        return real(dataset, names)

    monkeypatch.setattr(slack_sync, "remove_channels_from_corpus", flaky)
    import app.kg_ingest.auto_sync as auto_sync

    monkeypatch.setattr(auto_sync, "kickoff_corpus_seed", lambda *a: True)

    out = slack_sync.purge_channels_from_synced_data("co-1", ["support"])
    assert out["sections_removed"] == 1
    assert out["reseeded"] == ["acme"]
