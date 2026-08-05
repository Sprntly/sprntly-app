"""Tests for Slack channels -> KG extraction (kg_ingest.slack_extract), and
the two seams it touches: `connectors.slack_sync.sync_slack` (which collects
`SlackChannelDoc`s and kicks off extraction) and
`synthesis_brief._seed_from_corpus` (which stops double-ingesting the
wholesale `slack_channels.md` corpus doc now that per-channel extraction
exists).

Slack already fed the KG before this change, but as one undifferentiated
document per workspace with a model-guessed `source_type` and no per-channel
identity. These tests pin the new shape: one extraction document per
channel, a deterministic `communication` type floor, channel-id/name
provenance (and NEVER the typed-column-promoting `"channel"` key), a
content-hash ledger (not a watermark), and per-chunk error isolation.
"""
from __future__ import annotations

from unittest.mock import patch

import pytest

from app.kg_ingest import slack_extract
from app.kg_ingest.slack_extract import SlackChannelDoc


# ─────────────────────────── shared helpers ───────────────────────────


def _doc(**kw) -> SlackChannelDoc:
    base = dict(
        channel_id="C0001AAA",
        channel_name="product-feedback",
        text=(
            "## #product-feedback\n\n"
            "**Alice** (2026-08-01 10:00):\n"
            "Customers keep asking for SSO.\n"
        ),
        latest_ts="1712345678.000100",
        message_count=1,
        is_private=False,
    )
    base.update(kw)
    return SlackChannelDoc(**base)


def _fake_extract(calls: list[dict], *, result: dict | None = None):
    """Fake `extract_document` — records every call's kwargs plus doc_name/
    text, and returns a fixed result dict."""
    fixed = result or {"signals": 1, "themes": 0, "skipped": 0}

    def _fn(facade, company_id, *, doc_name, text, **kw):
        calls.append({"company_id": company_id, "doc_name": doc_name,
                      "text": text, **kw})
        return dict(fixed)

    return _fn


class _Ledger:
    """In-memory stand-in for db.kg_ingest_ledger, mirroring the pattern in
    tests/test_kg_ingest_ledger.py."""

    def __init__(self):
        self.seen_set: set[str] = set()
        self.recorded: list[tuple[str, str, list[str]]] = []

    def seen(self, company_id, hashes):
        return {h for h in hashes if h in self.seen_set}

    def record(self, company_id, provider, hashes):
        self.recorded.append((company_id, provider, list(hashes)))
        self.seen_set.update(hashes)


@pytest.fixture
def ledger(monkeypatch):
    led = _Ledger()
    monkeypatch.setattr(slack_extract, "seen_hashes", led.seen)
    monkeypatch.setattr(slack_extract, "record_hashes", led.record)
    return led


# ═══════════════════════ Creation (AC1, AC2) ═══════════════════════


def test_slack_extract_emits_one_document_per_channel(ledger, monkeypatch):
    calls: list[dict] = []
    monkeypatch.setattr(slack_extract, "extract_document", _fake_extract(calls))
    docs = [
        _doc(channel_id="C1", channel_name="general"),
        _doc(channel_id="C2", channel_name="support"),
        _doc(channel_id="C3", channel_name="random"),
    ]
    out = slack_extract.extract_slack_channels(object(), "co-1", docs)
    assert len(calls) == 3
    assert len({c["doc_name"] for c in calls}) == 3
    assert out["errors"] == []


def test_slack_extract_doc_name_is_channel_scoped(ledger, monkeypatch):
    calls: list[dict] = []
    monkeypatch.setattr(slack_extract, "extract_document", _fake_extract(calls))
    slack_extract.extract_slack_channels(
        object(), "co-1", [_doc(channel_name="product-feedback")]
    )
    assert calls[0]["doc_name"] == "slack/#product-feedback"
    assert calls[0]["doc_name"] != "slack_channels"


def test_slack_extract_stamps_channel_id_and_name_in_provenance(ledger, monkeypatch):
    calls: list[dict] = []
    monkeypatch.setattr(slack_extract, "extract_document", _fake_extract(calls))
    slack_extract.extract_slack_channels(
        object(), "co-1",
        [_doc(channel_id="C99", channel_name="eng", latest_ts="1700000000.000001")],
    )
    pe = calls[0]["provenance_extra"]
    assert pe["slack_channel_id"] == "C99"
    assert pe["slack_channel_name"] == "eng"
    assert pe["slack_latest_ts"] == "1700000000.000001"


# ═══════════════════════ Serialization / contract (AC3, AC4) ═══════════════════════


def test_slack_extract_never_passes_provenance_channel_key(ledger, monkeypatch):
    """PRIVACY/INTEGRITY TRAP GUARD (AC3) — mutation-proofable.

    `provenance_extra["channel"]` is promoted into the typed
    `Signal.channel` column, whose only meaning in this codebase is
    "upload" (read by the brief sufficiency gate — see
    graph.extractor._DOCUMENT_PROVIDERS / runner.py:84-97). Writing a Slack
    channel name under that key would silently corrupt the gate's read.

    RED-first: add `"channel": doc.channel_name` to slack_extract.py's
    `provenance_extra` dict and this test fails.
    """
    calls: list[dict] = []
    monkeypatch.setattr(slack_extract, "extract_document", _fake_extract(calls))
    slack_extract.extract_slack_channels(
        object(), "co-1",
        [_doc(channel_id="C1", channel_name="general"),
         _doc(channel_id="C2", channel_name="support")],
    )
    assert calls, "extract_document was never called"
    for c in calls:
        assert "channel" not in (c["provenance_extra"] or {})


def test_slack_extract_passes_source_type_default_communication(ledger, monkeypatch):
    calls: list[dict] = []
    monkeypatch.setattr(slack_extract, "extract_document", _fake_extract(calls))
    slack_extract.extract_slack_channels(object(), "co-1", [_doc()])
    assert calls[0]["source_type_default"] == "communication"


def test_slack_extract_origin_is_upload(ledger, monkeypatch):
    """Pins the deliberate deferral: origin stays "upload", not "connector"
    — flipping it would make the brief gate stricter for Slack-only
    tenants (see the module docstring / ticket LOCKED note)."""
    calls: list[dict] = []
    monkeypatch.setattr(slack_extract, "extract_document", _fake_extract(calls))
    slack_extract.extract_slack_channels(object(), "co-1", [_doc()])
    assert calls[0]["origin"] == "upload"


# The two tests below prove the RESULT of source_type_default (not just
# that it's passed) by exercising the real extract_document against a real
# GraphFacade — extract_document's own re-stamp logic is generically
# covered in test_kg_extractor.py; these pin that Slack's specific call
# shape (triage=True, source_type_default="communication") flows through
# it correctly.


def _slack_llm_result(items: list[dict]):
    from app.graph.gateway import LLMResult
    import app.graph.extractor as ex

    return LLMResult(
        output={"signals": items}, model="m", prompt_version=ex.PROMPT_VERSION,
        input_tokens=0, output_tokens=0, cache_read_input_tokens=0,
        cache_creation_input_tokens=0, cost_usd=0.0, latency_ms=0,
        stop_reason="end_turn",
    )


def _item(content: str, source_type: str) -> dict:
    return {"kind": "feature_request", "content": content,
            "source_type": source_type, "theme": "Auth",
            "relationship": "REQUESTS", "confidence": 0.9}


def test_slack_extract_seeded_source_type_is_restamped_communication(
    ledger, isolated_settings
):
    import uuid

    import app.graph.extractor as ex
    import app.graph.triage as triage_mod
    from app.graph.facade import GraphFacade
    from app.graph.triage import TriageResult

    facade = GraphFacade()
    content = "customers keep asking for SSO in slack"
    with patch.object(ex, "llm_call",
                      return_value=_slack_llm_result([_item(content, "pm_manual")])), \
         patch.object(ex, "embed_texts",
                      side_effect=lambda texts, **k: [[0.0] * 4 for _ in texts]), \
         patch.object(triage_mod, "triage_batch",
                      return_value=TriageResult(True, "customer_feedback", "t")):
        slack_extract.extract_slack_channels(
            facade, "ent-x", [_doc(text=f"## #eng\n\n{content}\n")]
        )
    sig_id = str(uuid.uuid5(ex._NS, f"ent-x|{content}"))
    sig = facade.get_signal("ent-x", sig_id)
    assert sig is not None
    assert sig.source_type == "communication"


def test_slack_extract_keeps_model_chosen_customer_voice(ledger, isolated_settings):
    import uuid

    import app.graph.extractor as ex
    import app.graph.triage as triage_mod
    from app.graph.facade import GraphFacade
    from app.graph.triage import TriageResult

    facade = GraphFacade()
    content = "a customer verbatim about churn risk"
    with patch.object(ex, "llm_call",
                      return_value=_slack_llm_result([_item(content, "customer_voice")])), \
         patch.object(ex, "embed_texts",
                      side_effect=lambda texts, **k: [[0.0] * 4 for _ in texts]), \
         patch.object(triage_mod, "triage_batch",
                      return_value=TriageResult(True, "customer_feedback", "t")):
        slack_extract.extract_slack_channels(
            facade, "ent-x", [_doc(text=f"## #support\n\n{content}\n")]
        )
    sig_id = str(uuid.uuid5(ex._NS, f"ent-x|{content}"))
    sig = facade.get_signal("ent-x", sig_id)
    assert sig is not None
    assert sig.source_type == "customer_voice"


# ═══════════════════════ Retrieval / dedupe (AC6) ═══════════════════════


def test_slack_extract_skips_chunks_already_in_ledger(ledger, monkeypatch):
    doc = _doc()
    h = slack_extract._chunk_hash(doc.channel_id, doc.text)
    ledger.seen_set.add(h)
    calls: list[dict] = []
    monkeypatch.setattr(slack_extract, "extract_document", _fake_extract(calls))
    out = slack_extract.extract_slack_channels(object(), "co-1", [doc])
    assert calls == []
    assert out["deduped"] == 1
    assert out["chunks"] == 0


def test_slack_extract_records_hashes_only_for_successful_chunks(ledger, monkeypatch):
    line = "z" * 999 + "\n"
    text = line * 8  # -> 2 chunks (6 lines then 2 lines)
    doc = _doc(channel_name="eng", text=text, message_count=5)

    def _fn(facade, company_id, *, doc_name, text, **kw):
        if "part 2" in doc_name:
            raise RuntimeError("llm down")
        return {"signals": 1, "themes": 0, "skipped": 0}

    monkeypatch.setattr(slack_extract, "extract_document", _fn)
    out = slack_extract.extract_slack_channels(object(), "co-1", [doc])

    # Only the successful chunk's hash was recorded.
    assert len(out["errors"]) == 1
    assert len(ledger.recorded) == 1
    assert len(ledger.recorded[0][2]) == 1


def test_slack_extract_rerun_with_no_new_messages_costs_no_llm_calls(ledger, monkeypatch):
    calls: list[dict] = []
    monkeypatch.setattr(slack_extract, "extract_document", _fake_extract(calls))
    docs = [_doc(channel_id="C1", channel_name="general"),
            _doc(channel_id="C2", channel_name="support")]

    out1 = slack_extract.extract_slack_channels(object(), "co-1", docs)
    assert len(calls) == 2 and out1["deduped"] == 0

    calls.clear()
    out2 = slack_extract.extract_slack_channels(object(), "co-1", docs)
    assert calls == []
    assert out2["deduped"] == 2
    assert out2["chunks"] == 0


def test_slack_extract_edited_message_rehashes_and_reextracts(ledger, monkeypatch):
    calls: list[dict] = []
    monkeypatch.setattr(slack_extract, "extract_document", _fake_extract(calls))
    doc_a = _doc(channel_id="C1", channel_name="general", text="## #general\n\nhello\n")
    doc_b = _doc(channel_id="C2", channel_name="support", text="## #support\n\nhelp\n")
    slack_extract.extract_slack_channels(object(), "co-1", [doc_a, doc_b])
    calls.clear()

    edited_a = _doc(channel_id="C1", channel_name="general",
                    text="## #general\n\nhello there!\n")
    out = slack_extract.extract_slack_channels(object(), "co-1", [edited_a, doc_b])
    names = [c["doc_name"] for c in calls]
    assert names == ["slack/#general"]  # only the edited channel re-extracts
    assert out["deduped"] == 1


# ═══════════════════════ Isolation (AC9) ═══════════════════════


def test_slack_extract_isolates_one_failing_channel(ledger, monkeypatch):
    calls: list[str] = []

    def _fn(facade, company_id, *, doc_name, text, **kw):
        if "support" in doc_name:
            raise RuntimeError("llm down")
        calls.append(doc_name)
        return {"signals": 1, "themes": 0, "skipped": 0}

    monkeypatch.setattr(slack_extract, "extract_document", _fn)
    docs = [
        _doc(channel_id="C1", channel_name="general"),
        _doc(channel_id="C2", channel_name="support"),
        _doc(channel_id="C3", channel_name="random"),
    ]
    out = slack_extract.extract_slack_channels(object(), "co-1", docs)
    assert calls == ["slack/#general", "slack/#random"]
    assert len(out["errors"]) == 1 and "support" in out["errors"][0]


def test_slack_extract_with_zero_channels_is_a_noop(ledger, monkeypatch):
    def _boom(*a, **k):
        raise AssertionError("extract_document should never be called")

    monkeypatch.setattr(slack_extract, "extract_document", _boom)
    out = slack_extract.extract_slack_channels(object(), "co-1", [])
    assert out == {
        "channels": 0, "chunks": 0, "signals": 0, "themes": 0,
        "skipped": 0, "deduped": 0, "errors": [],
    }


# ═══════════════════════ Error handling / observability (AC9, AC12) ═══════════════════════


def test_slack_extract_logs_identifiers_not_message_text(ledger, monkeypatch, caplog):
    secret_text = "customer said the pipeline was cancelled over pricing"

    def _raise(facade, company_id, *, doc_name, text, **kw):
        raise RuntimeError("boom")

    monkeypatch.setattr(slack_extract, "extract_document", _raise)
    doc = _doc(channel_id="C7", channel_name="eng",
              text=f"## #eng\n\n**Bob**: {secret_text}\n", message_count=1)

    with caplog.at_level("ERROR", logger="app.kg_ingest.slack_extract"):
        out = slack_extract.extract_slack_channels(object(), "co-1", [doc])

    assert out["errors"] == ["eng: RuntimeError"]
    log_text = "\n".join(r.getMessage() for r in caplog.records)
    assert "eng" in log_text
    assert "RuntimeError" in log_text
    assert "co-1" in log_text
    assert secret_text not in log_text


# ═══════════════════════ Edge cases (AC5, AC10) ═══════════════════════


def test_slack_extract_chunks_a_long_channel(ledger, monkeypatch):
    calls: list[dict] = []
    monkeypatch.setattr(slack_extract, "extract_document", _fake_extract(calls))
    line = "y" * 999 + "\n"
    text = line * 15  # 15,000 chars -> 3 chunks (6000, 6000, 3000)
    doc = _doc(channel_name="eng", text=text, message_count=5)
    slack_extract.extract_slack_channels(object(), "co-1", [doc])
    names = [c["doc_name"] for c in calls]
    assert names == [
        "slack/#eng (part 1/3)", "slack/#eng (part 2/3)", "slack/#eng (part 3/3)",
    ]
    assert "".join(c["text"] for c in calls) == text


def test_slack_extract_truncates_beyond_max_kg_chars(ledger, monkeypatch, caplog):
    calls: list[dict] = []
    monkeypatch.setattr(slack_extract, "extract_document", _fake_extract(calls))
    line = "w" * 999 + "\n"
    text = line * 80  # 80,000 chars
    doc = _doc(channel_name="eng", text=text, message_count=100)
    with caplog.at_level("INFO", logger="app.kg_ingest.slack_extract"):
        slack_extract.extract_slack_channels(object(), "co-1", [doc])
    total = sum(len(c["text"]) for c in calls)
    assert total <= slack_extract._MAX_KG_CHARS
    log_text = "\n".join(r.getMessage() for r in caplog.records)
    assert "eng" in log_text
    assert str(len(text)) in log_text
    assert str(slack_extract._MAX_KG_CHARS) in log_text


def test_slack_extract_skips_empty_channel_section(ledger, monkeypatch):
    calls: list[dict] = []
    monkeypatch.setattr(slack_extract, "extract_document", _fake_extract(calls))
    doc = _doc(channel_name="eng", text="## #eng\n\n_No recent messages._\n",
              message_count=0)
    out = slack_extract.extract_slack_channels(object(), "co-1", [doc])
    assert calls == []
    assert out["channels"] == 0


def test_slack_extract_triage_window_sees_message_text_not_the_summary_table(
    ledger, monkeypatch
):
    """The concrete fix for the triage defect: the first 4,000 chars handed
    to triage for a channel document begin with that channel's own heading,
    not the workspace-wide "## Channels Overview" table (AC1)."""
    calls: list[dict] = []
    monkeypatch.setattr(slack_extract, "extract_document", _fake_extract(calls))
    doc = _doc(
        channel_name="support",
        text="## #support\n\n**Bob** (2026-08-01):\nTicket 42 reopened.\n",
        message_count=1,
    )
    slack_extract.extract_slack_channels(object(), "co-1", [doc])
    window = calls[0]["text"][:4000]
    assert window.startswith("## #support")
    assert "Channels Overview" not in window


# ═══════════════════════ Corpus suppression (AC8) ═══════════════════════
#
# _seed_from_corpus stops extracting the wholesale slack_channels.md corpus
# doc now that per-channel extraction (above) covers Slack. The corpus FILE
# itself stays — it still feeds briefs/Ask/DS Agent — only its own direct
# KG extraction is suppressed.


def _seed_company(db, *, company_id: str, slug: str) -> None:
    existing = db.table("companies").select("id").eq("id", company_id).execute().data
    if not existing:
        db.table("companies").insert(
            {"id": company_id, "slug": slug, "display_name": slug.title()}
        ).execute()


class _CorpusDoc:
    def __init__(self, name: str, text: str):
        self.name, self.text = name, text


def test_seed_from_corpus_skips_the_slack_doc(isolated_settings):
    import app.synthesis_brief as sb
    from app.graph.facade import GraphFacade

    _seed_company(isolated_settings["supabase"], company_id="co-1", slug="acme")
    facade = GraphFacade()

    class _Corpus:
        docs = [_CorpusDoc("slack_channels", "# Slack Workspace Messages\n...")]

    extracted: list[str] = []
    with patch.object(sb, "load_corpus", return_value=_Corpus()), \
         patch.object(sb, "extract_document",
                      side_effect=lambda *a, **k: extracted.append(k["doc_name"]) or
                      {"signals": 1, "themes": 0, "skipped": 0}):
        out = sb._seed_from_corpus(facade, "co-1", "acme")

    assert extracted == []
    assert out["kg_excluded"] == 1
    assert out["docs"] == 0


def test_seed_from_corpus_records_no_corpus_doc_row_for_slack(isolated_settings):
    import app.synthesis_brief as sb
    from app.graph.facade import GraphFacade

    _seed_company(isolated_settings["supabase"], company_id="co-1", slug="acme")
    facade = GraphFacade()

    class _Corpus:
        docs = [_CorpusDoc("slack_channels", "# Slack Workspace Messages\n...")]

    with patch.object(sb, "load_corpus", return_value=_Corpus()), \
         patch.object(sb, "extract_document",
                      side_effect=lambda *a, **k: {"signals": 1, "themes": 0, "skipped": 0}):
        sb._seed_from_corpus(facade, "co-1", "acme")

    srcs = facade.list_sources("co-1", source_type="corpus_doc")
    assert not any(s.config.get("doc") == "slack_channels" for s in srcs)


def test_seed_from_corpus_still_extracts_other_docs(isolated_settings):
    import app.synthesis_brief as sb
    from app.graph.facade import GraphFacade

    _seed_company(isolated_settings["supabase"], company_id="co-1", slug="acme")
    facade = GraphFacade()

    class _Corpus:
        docs = [_CorpusDoc("slack_channels", "# Slack Workspace Messages\n..."),
                _CorpusDoc("roadmap", "plan text")]

    extracted: list[str] = []
    with patch.object(sb, "load_corpus", return_value=_Corpus()), \
         patch.object(sb, "extract_document",
                      side_effect=lambda *a, **k: extracted.append(k["doc_name"]) or
                      {"signals": 1, "themes": 0, "skipped": 0}):
        out = sb._seed_from_corpus(facade, "co-1", "acme")

    assert extracted == ["roadmap"]
    assert out["docs"] == 1
    assert out["kg_excluded"] == 1


# ═══════════════════════ Sync wiring (AC1, AC7, AC8, AC9, AC11) ═══════════════════════
#
# The tests above exercise extract_slack_channels directly. These exercise
# connectors.slack_sync.sync_slack end-to-end (Slack HTTP mocked at
# `_slack_get`) to prove the wiring: SlackChannelDocs are correctly
# collected inside the existing per-channel loop and handed to
# kickoff_slack_extract, the channel selection already applied to
# `channels` is what determines which docs get built, no new Slack
# endpoint is requested, and a kickoff failure never reaches sync_slack's
# own return value.


def _channel(cid: str, name: str, *, is_member: bool = True,
            is_private: bool = False) -> dict:
    return {"id": cid, "name": name, "is_member": is_member,
            "is_private": is_private, "topic": {}, "purpose": {}}


def _slack_get_fake(channels, messages_by_channel, *, users=None, url_log=None):
    users = users or {}

    def _get(url, token, params=None, timeout=30):
        params = params or {}
        if url_log is not None:
            url_log.append(url)
        if url == "https://slack.com/api/users.list":
            return {"ok": True, "members": [
                {"id": uid, "profile": {"display_name": name}}
                for uid, name in users.items()
            ]}
        if url == "https://slack.com/api/conversations.list":
            return {"ok": True, "channels": channels}
        if url == "https://slack.com/api/conversations.history":
            ch_id = params.get("channel")
            return {"ok": True, "messages": messages_by_channel.get(ch_id, [])}
        if url == "https://slack.com/api/conversations.replies":
            return {"ok": True, "messages": []}
        return {"ok": False, "error": "unknown_url"}

    return _get


@pytest.fixture
def slack_sync_env(isolated_settings, tmp_data_dir, monkeypatch):
    import app.config as config_mod
    from app.connectors import slack_sync

    # slack_sync does `from app.config import settings` at import time and
    # is not in conftest's reload order, so it can hold a Settings object
    # from an earlier test's DATA_DIR — rebind it to this test's fresh one.
    monkeypatch.setattr(slack_sync, "settings", config_mod.settings)
    monkeypatch.setattr(
        slack_sync, "_get_company_token_and_config",
        lambda company_id: ("xoxb-test", {}, {"user_id": "u1"}),
    )
    monkeypatch.setattr(slack_sync.db, "update_slack_connection_sync",
                        lambda *a, **k: None)
    monkeypatch.setattr(slack_sync.db, "upsert_input_source",
                        lambda *a, **k: None)
    return slack_sync


def test_sync_slack_builds_one_channel_doc_per_channel_and_kicks_off_extraction(
    slack_sync_env, monkeypatch
):
    from app.kg_ingest import slack_extract as se

    channels = [_channel("C1", "general"), _channel("C2", "support"),
                _channel("C3", "random")]
    messages = {
        "C1": [{"user": "U1", "text": "hi", "ts": "1700000000.000001"}],
        "C2": [{"user": "U1", "text": "help please", "ts": "1700000001.000001"}],
        "C3": [{"user": "U1", "text": "coffee?", "ts": "1700000002.000001"}],
    }
    monkeypatch.setattr(
        slack_sync_env, "_slack_get",
        _slack_get_fake(channels, messages, users={"U1": "Alice"}),
    )
    captured = {}
    monkeypatch.setattr(
        se, "kickoff_slack_extract",
        lambda company_id, docs: captured.update(company_id=company_id, docs=docs)
        or True,
    )

    result = slack_sync_env.sync_slack("acme", company_id="co-1")

    assert result.channels_count == 3
    assert captured["company_id"] == "co-1"
    docs = captured["docs"]
    assert {d.channel_id for d in docs} == {"C1", "C2", "C3"}
    assert {d.channel_name for d in docs} == {"general", "support", "random"}
    for d in docs:
        assert d.message_count == 1
        assert d.latest_ts
        assert f"## #{d.channel_name}" in d.text


def test_slack_extract_honours_existing_channel_selection(slack_sync_env, monkeypatch):
    from app.kg_ingest import slack_extract as se

    channels = [_channel("C1", "general"), _channel("C2", "support"),
                _channel("C3", "random")]
    messages = {cid: [{"user": "U1", "text": "hi", "ts": "1700000000.000001"}]
               for cid in ("C1", "C2", "C3")}
    monkeypatch.setattr(slack_sync_env, "_slack_get",
                        _slack_get_fake(channels, messages))
    monkeypatch.setattr(
        slack_sync_env, "_get_company_token_and_config",
        lambda company_id: (
            "xoxb-test",
            {slack_sync_env.CONFIG_SYNC_CHANNEL_IDS: ["C2"]},
            {"user_id": "u1"},
        ),
    )
    captured = {}
    monkeypatch.setattr(
        se, "kickoff_slack_extract",
        lambda company_id, docs: captured.update(docs=docs) or True,
    )

    slack_sync_env.sync_slack("acme", company_id="co-1")

    # Selection is applied to `channels` via select_sync_channels BEFORE the
    # per-channel loop runs — the collected docs must reflect it exactly,
    # not a reimplementation of the selection logic.
    assert [d.channel_id for d in captured["docs"]] == ["C2"]


def test_slack_extract_makes_no_new_slack_api_calls(slack_sync_env, monkeypatch):
    from app.kg_ingest import slack_extract as se

    channels = [_channel("C1", "general")]
    messages = {"C1": [{"user": "U1", "text": "hi", "ts": "1700000000.000001",
                        "reply_count": 0}]}
    url_log: list[str] = []
    monkeypatch.setattr(
        slack_sync_env, "_slack_get",
        _slack_get_fake(channels, messages, url_log=url_log),
    )
    monkeypatch.setattr(se, "kickoff_slack_extract", lambda *a, **k: True)

    slack_sync_env.sync_slack("acme", company_id="co-1")

    allowed = {
        slack_sync_env.SLACK_USERS_URL,
        slack_sync_env.SLACK_CONVERSATIONS_LIST_URL,
        slack_sync_env.SLACK_CONVERSATIONS_HISTORY_URL,
        slack_sync_env.SLACK_CONVERSATIONS_REPLIES_URL,
    }
    assert url_log, "no Slack API call was made at all"
    assert set(url_log) <= allowed


def test_slack_extract_failure_never_raises_into_sync_slack(slack_sync_env, monkeypatch):
    from app.kg_ingest import slack_extract as se

    channels = [_channel("C1", "general")]
    messages = {"C1": [{"user": "U1", "text": "hi", "ts": "1700000000.000001"}]}
    monkeypatch.setattr(slack_sync_env, "_slack_get",
                        _slack_get_fake(channels, messages))

    def _boom(company_id, docs):
        raise RuntimeError("thread spawn exploded")

    monkeypatch.setattr(se, "kickoff_slack_extract", _boom)

    result = slack_sync_env.sync_slack("acme", company_id="co-1")  # must not raise
    assert result.channels_count == 1
    assert result.errors == []  # the kickoff failure is swallowed, not surfaced as a sync error


def test_slack_corpus_file_still_written_after_sync(slack_sync_env, monkeypatch, tmp_data_dir):
    from pathlib import Path

    from app.kg_ingest import slack_extract as se

    channels = [_channel("C1", "general")]
    messages = {"C1": [{"user": "U1", "text": "hi", "ts": "1700000000.000001"}]}
    monkeypatch.setattr(slack_sync_env, "_slack_get",
                        _slack_get_fake(channels, messages))
    monkeypatch.setattr(se, "kickoff_slack_extract", lambda *a, **k: True)

    slack_sync_env.sync_slack("acme", company_id="co-1")

    assert (Path(tmp_data_dir) / "acme" / "slack_channels.md").exists()
