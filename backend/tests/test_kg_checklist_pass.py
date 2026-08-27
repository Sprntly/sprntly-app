"""Tests for the directed-checklist second pass (app.graph.extractor.
run_checklist_pass) and its wiring into app.kg_ingest.runner.sync_provider
for call-shaped providers (fireflies/zoom/google_meet), plus the gated-rollout
allowlist that scopes that pipeline's rollout.
"""
from __future__ import annotations

import os
import uuid
from unittest.mock import patch

import pytest

import app.graph.extractor as ex
from app.graph.gateway import LLMResult
from app.kg_ingest import runner
from app.kg_ingest.types import RawRecord


@pytest.fixture
def facade(isolated_settings):
    from app.graph import GraphFacade
    return GraphFacade()


# ── extractor.run_checklist_pass ──────────────────────────────────────────────


def _entry(category: str, *, discussed: bool = True, content: str = "",
           quote: str = "", properties: dict | None = None) -> dict:
    return {"category": category, "discussed": discussed, "content": content,
            "verbatim_quote": quote, "properties": properties or {}}


def _checklist_llm_result(entries: list[dict]) -> LLMResult:
    return LLMResult(
        output={"checklist": entries}, model="m",
        prompt_version=ex.CHECKLIST_PROMPT_VERSION,
        input_tokens=0, output_tokens=0, cache_read_input_tokens=0,
        cache_creation_input_tokens=0, cost_usd=0.0, latency_ms=0,
        stop_reason="end_turn",
    )


def _run_checklist(facade, entries: list[dict], text: str, **kwargs) -> dict:
    with patch.object(ex, "llm_call", return_value=_checklist_llm_result(entries)), \
         patch.object(ex, "embed_texts",
                      side_effect=lambda texts, **k: [[0.0] * 4 for _ in texts]):
        return ex.run_checklist_pass(facade, "ent-c", doc_name="call.md",
                                     text=text, **kwargs)


def _csig(facade, content: str):
    sig_id = str(uuid.uuid5(ex._NS, f"ent-c|{content}"))
    return facade.get_signal("ent-c", sig_id)


def test_all_categories_map_to_expected_kind_and_stakeholders_never_mints(facade):
    """Every one of the 11 categories is checked (attention coverage), but only
    the 10 that have a clean signal-kind home actually mint a Signal —
    'stakeholders' is a person-graph recall target only, never a Signal, even
    though this entry is discussed=True with a grounded quote like every
    other one."""
    entries = []
    quotes: dict[str, str] = {}
    for key, _desc, _kind, _theme, _rel, _source_type, _mint in ex._CHECKLIST_CATEGORIES:
        quote = f"the team said quote-for-{key} happened on the call"
        quotes[key] = quote
        entries.append(_entry(key, content=f"{key} fact", quote=quote))
    text = "\n".join(quotes.values())

    result = _run_checklist(facade, entries, text)

    expected_minted = {c[0]: c[2] for c in ex._CHECKLIST_CATEGORIES if c[6]}
    assert result["signals"] == len(expected_minted)
    assert "stakeholders" not in expected_minted, "sanity: table still marks it non-minting"

    for key, expected_kind in expected_minted.items():
        sig = _csig(facade, f"{key} fact")
        assert sig is not None, f"expected a signal for category {key!r}"
        assert sig.kind == expected_kind
        assert sig.provenance["checklist_category"] == key

    # Never minted, despite being discussed=True with a grounded quote.
    assert _csig(facade, "stakeholders fact") is None


def test_legal_category_mints_the_new_legal_term_kind(facade):
    """Row 9 (legal/security/compliance) is the new `legal_term` kind, not
    folded into the `finding` catch-all."""
    entries = [_entry("legal", content="no NDA in place yet",
                       quote="honestly we don't even have an NDA in place")]
    text = "Facilitator: honestly we don't even have an NDA in place with them yet."
    _run_checklist(facade, entries, text)
    sig = _csig(facade, "no NDA in place yet")
    assert sig is not None
    assert sig.kind == "legal_term"


def test_commitment_category_carries_owner_due_status_properties(facade):
    """Row 6 (commitments) maps to `finding` + properties{owner,due,status} —
    the v2 action-item attribution shape, so commitments flow through
    owner-resolution for free."""
    entries = [_entry(
        "commitment", content="Jane owns the SSO doc by Friday",
        quote="Jane will send the SSO doc by Friday",
        properties={"owner": "Jane Doe", "due": "Friday", "status": "open"},
    )]
    text = "PM: Jane will send the SSO doc by Friday."
    _run_checklist(facade, entries, text)
    sig = _csig(facade, "Jane owns the SSO doc by Friday")
    assert sig is not None
    assert sig.kind == "finding"
    assert sig.properties["owner"] == "Jane Doe"
    assert sig.properties["due"] == "Friday"
    assert sig.properties["status"] == "open"


def test_timeline_category_carries_urgency_and_trigger_date_properties(facade):
    """Row 10 (timeline/urgency) maps to `finding` + properties{urgency,
    trigger_date}."""
    entries = [_entry(
        "timeline", content="go-live is tied to fiscal year end",
        quote="we need this live before our fiscal year closes",
        properties={"urgency": "high", "trigger_date": "2026-09-30"},
    )]
    text = "Buyer: we need this live before our fiscal year closes."
    _run_checklist(facade, entries, text)
    sig = _csig(facade, "go-live is tied to fiscal year end")
    assert sig is not None
    assert sig.properties["urgency"] == "high"
    assert sig.properties["trigger_date"] == "2026-09-30"


def test_not_discussed_category_mints_nothing(facade):
    """discussed=false must not invent a fact to fill the slot."""
    entries = [_entry("commercial", discussed=False, content="", quote="")]
    result = _run_checklist(facade, entries, "nothing commercial came up")
    assert result == {"signals": 0, "themes": 0, "skipped": 0, "signal_ids": []}


def test_discussed_true_with_ungrounded_quote_is_dropped(facade):
    """Precision contract: a quote that cannot be found verbatim in the
    transcript must be DROPPED, never written — the hallucination gate. This
    is what kept the spec's spike at 0/N hallucination."""
    entries = [_entry("objection", content="fabricated blocker",
                       quote="this exact sentence never appears anywhere")]
    result = _run_checklist(facade, entries, "Buyer: pricing seems fine to us.")
    assert result["signals"] == 0
    assert _csig(facade, "fabricated blocker") is None


def test_discussed_true_with_empty_quote_is_dropped(facade):
    """A claimed fact with no grounding quote at all is dropped, same gate as
    an ungrounded one — never written on trust alone."""
    entries = [_entry("sentiment", discussed=True, content="vague sentiment",
                       quote="")]
    result = _run_checklist(facade, entries, "some transcript text")
    assert result["signals"] == 0
    assert _csig(facade, "vague sentiment") is None


def test_unknown_category_is_skipped_without_crashing_other_entries(facade):
    """A category the model invents outside the fixed 11 is dropped safely —
    it must not crash the pass or block the OTHER, valid entries in the same
    checklist response."""
    entries = [
        _entry("not_a_real_category", content="should be ignored",
               quote="whatever"),
        _entry("sentiment", content="real sentiment fact",
               quote="customers seem happy"),
    ]
    text = "Rep: customers seem happy with the rollout."
    result = _run_checklist(facade, entries, text)
    assert _csig(facade, "should be ignored") is None
    assert _csig(facade, "real sentiment fact") is not None
    assert result["signals"] == 1


def test_source_call_id_and_provenance_stamped_via_source_ref(facade):
    """A checklist-minted signal inherits source_call_id/provenance exactly
    like the main extraction pass — same _write_items path, same source_ref
    contract."""
    import app.call_index as ci

    entries = [_entry("commercial", content="priced at $50k/yr",
                       quote="we're paying fifty thousand a year")]
    text = "Buyer: we're paying fifty thousand a year for this."
    with patch.object(ci, "resolve_call_id", return_value=77) as resolve:
        _run_checklist(facade, entries, text, source_ref=("fireflies", "FF-9"))
    resolve.assert_called_once_with("ent-c", "fireflies", "FF-9")
    sig = _csig(facade, "priced at $50k/yr")
    assert sig.source_call_id == 77
    assert sig.provenance["provider"] == "fireflies"
    assert sig.provenance["external_id"] == "FF-9"


def test_empty_checklist_output_writes_nothing(facade):
    result = _run_checklist(facade, [], "anything")
    assert result == {"signals": 0, "themes": 0, "skipped": 0, "signal_ids": []}


def test_quote_grounded_helper_normalizes_whitespace_and_case():
    assert ex._quote_is_grounded("Hello   World",
                                  "text before hello world after") is True
    assert ex._quote_is_grounded("", "anything") is False
    assert ex._quote_is_grounded("missing", "present only") is False


# ── grounding-gate fix (live-verify 2026-08-26): real quotes drop the ────────
# repeated per-line "{speaker}: " prefix and join sentences with a space,
# so a strict literal-substring check against the raw newline-joined,
# speaker-prefixed source rejected most REAL quotes on formatting grounds.


def test_quote_grounded_accepts_a_real_speaker_prefix_dropped_multi_sentence_quote():
    """The exact failure mode live-verify found: a real, in-order,
    non-fabricated quote spanning two source lines, with the repeated
    speaker prefix dropped and the sentences joined by a space instead of a
    newline — the natural way a model quotes a contiguous remark."""
    source = (
        "Jordan Lee: The base subscription comes with 75 seats and that's "
        "forty-five thousand dollars a year for ninety users.\n"
        "Jordan Lee: As you go up in user count the price per seat goes "
        "down significantly."
    )
    quote = (
        "The base subscription comes with 75 seats and that's thirty "
        "thousand dollars a year for ninety users. As you go up in user "
        "count the price per seat goes down significantly."
    )
    assert ex._quote_is_grounded(quote, source) is True


def test_quote_grounded_accepts_reformatting_via_the_word_run_fallback():
    """A quote that drops a filler word/interjection from the START of a
    source line (so it is no longer a literal substring even after
    flattening) is still grounded IF it contains a genuine run of >= 6
    consecutive verbatim source words — the word-run fallback, not the
    flattened-substring check, is what saves this one."""
    source = (
        "Jordan Lee: Well, the base plan is ninety seats.\n"
        "Jordan Lee: And moving up from there the price drops."
    )
    quote = "the base plan is ninety seats and moving up from there the price drops"
    # Not a literal substring of the flattened source (still carries "Well,"
    # and mid-sentence periods) — proves this is the word-run path, not (1)/(2).
    assert quote not in ex._flatten_transcript_lines(source).lower()
    assert ex._quote_is_grounded(quote, source) is True


def test_quote_grounded_still_rejects_a_fabricated_quote_with_unrelated_content():
    """The fabrication guard holds: a quote whose content was never said at
    all shares no consecutive word run with the source and is rejected by
    all three checks."""
    source = (
        "Jordan Lee: The base subscription comes with 75 seats and that's "
        "forty-five thousand dollars a year for ninety users."
    )
    quote = "the customer explicitly agreed to sign a three year exclusive contract"
    assert ex._quote_is_grounded(quote, source) is False


def test_quote_grounded_still_rejects_source_words_reordered_or_scattered():
    """Sharing individual WORDS with the source is not enough — the guard is
    order-sensitive, not bag-of-words. Scrambling real source words into a
    new sentence must still be rejected."""
    source = (
        "Jordan Lee: The base subscription comes with 75 seats and that's "
        "forty-five thousand dollars a year for ninety users."
    )
    # Same vocabulary as the source, shuffled into a different claim/order.
    quote = "ninety seats a year forty-five thousand dollars base users subscription"
    assert ex._quote_is_grounded(quote, source) is False


def test_quote_grounded_short_quote_still_requires_a_full_match():
    """A quote shorter than the word-run minimum (< 6 words) must still
    match in FULL — the fallback never gets MORE lenient for a short claim.
    Dropping even one word ("already") from the middle of a short quote is
    enough to fail every check, same as a wholly unrelated claim."""
    source = "Jordan Lee: We already have SOC2 in place."
    assert ex._quote_is_grounded("we already have SOC2 in place", source) is True
    assert ex._quote_is_grounded("we have SOC2", source) is False


def test_checklist_pass_mints_a_signal_for_a_real_reformatted_multi_line_quote(facade):
    """End-to-end reproduction of the live-verify symptom (a production
    tenant minting ~0/11 checklist categories): a real, correctly-quoted
    multi-sentence checklist answer must actually mint a signal now, not
    just pass the isolated grounding helper."""
    source = (
        "Jordan Lee: The base subscription comes with 75 seats and that's "
        "forty-five thousand dollars a year for ninety users.\n"
        "Jordan Lee: As you go up in user count the price per seat goes "
        "down significantly."
    )
    quote = (
        "The base subscription comes with 75 seats and that's thirty "
        "thousand dollars a year for ninety users. As you go up in user "
        "count the price per seat goes down significantly."
    )
    entries = [_entry("commercial", content="base plan is $45k/yr for 75 seats",
                       quote=quote)]
    result = _run_checklist(facade, entries, source)
    assert result["signals"] == 1
    sig = _csig(facade, "base plan is $45k/yr for 75 seats")
    assert sig is not None
    assert sig.kind == "commercial_term"


def test_checklist_pass_still_drops_a_fabricated_quote_end_to_end(facade):
    """Same end-to-end path, but a fabricated quote must still be dropped —
    the fix must not have widened the gate for content that was never said."""
    source = (
        "Jordan Lee: The base subscription comes with 75 seats and that's "
        "forty-five thousand dollars a year for ninety users."
    )
    entries = [_entry(
        "commercial", content="customer agreed to a 3-year exclusive deal",
        quote="the customer explicitly agreed to sign a three year exclusive contract",
    )]
    result = _run_checklist(facade, entries, source)
    assert result["signals"] == 0
    assert _csig(facade, "customer agreed to a 3-year exclusive deal") is None


def test_checklist_has_13_categories_including_the_two_new_ones():
    """Config B widens the checklist 11 -> 13: `customer_environment` (->
    `finding`) and `partnership_commercial` (-> `commercial_term`) join with
    the SAME contract as the original 11 — verbatim-quote-required, minted
    via the shared `_write_items` path."""
    assert len(ex._CHECKLIST_CATEGORIES) == 13
    by_key = {c[0]: c for c in ex._CHECKLIST_CATEGORIES}
    assert by_key["customer_environment"][2] == "finding"
    assert by_key["customer_environment"][6] is True  # mints a signal
    assert by_key["partnership_commercial"][2] == "commercial_term"
    assert by_key["partnership_commercial"][6] is True


def test_new_categories_mint_signals_with_the_grounding_contract(facade):
    """The two new categories go through the exact same grounded-write path
    as the original 11 — no special-casing."""
    entries = [
        _entry("customer_environment", content="EU manufacturing site runs on AWS Frankfurt",
               quote="our EU manufacturing site actually runs on AWS Frankfurt"),
        _entry("partnership_commercial", content="Meridian Partners referral drives leads",
               quote="the Meridian Partners referral has been driving leads"),
    ]
    text = (
        "Rep: our EU manufacturing site actually runs on AWS Frankfurt.\n"
        "Rep: the Meridian Partners referral has been driving leads."
    )
    result = _run_checklist(facade, entries, text)
    assert result["signals"] == 2
    env_sig = _csig(facade, "EU manufacturing site runs on AWS Frankfurt")
    assert env_sig is not None and env_sig.kind == "finding"
    partner_sig = _csig(facade, "Meridian Partners referral drives leads")
    assert partner_sig is not None and partner_sig.kind == "commercial_term"


def test_new_categories_still_require_a_grounded_quote(facade):
    """Same precision contract as the original 11: an ungrounded claim in a
    new category is dropped, not invented."""
    entries = [_entry("customer_environment", content="fabricated environment claim",
                       quote="this sentence never appears anywhere in the call")]
    result = _run_checklist(facade, entries, "Rep: everything is running smoothly.")
    assert result["signals"] == 0
    assert _csig(facade, "fabricated environment claim") is None


def test_checklist_system_prompt_names_every_category_and_precision_contract():
    """Content property test on the LLM-facing checklist system prompt: every
    category is named (so the model can't drift the vocabulary) and the
    precision contract (honest not-discussed over invention, verbatim
    grounding) is present. Dynamic against `_CHECKLIST_CATEGORIES` so it
    holds regardless of how many categories the checklist carries."""
    system = ex._CHECKLIST_SYSTEM.lower()
    for key, *_rest in ex._CHECKLIST_CATEGORIES:
        assert key in system, f"checklist system prompt should name category {key!r}"
    assert "not discussed" in system or "not-discussed" in system
    assert "verbatim" in system
    assert "invent" in system
    assert len(ex._CHECKLIST_SYSTEM) > 500


def test_checklist_system_prompt_carries_the_scenario_noise_guardrail():
    """Config B makes the checklist pass the SOLE full-transcript reader, so
    the scenario-noise guardrail (simulated/hypothetical content must not be
    minted as real) must live HERE too, not just in the shared `_SYSTEM` —
    the exact gap this ticket closes."""
    system = ex._CHECKLIST_SYSTEM.lower()
    for cue in ("simulated", "hypothetical", "tabletop", "let's say", "imagine"):
        assert cue in system, f"checklist guardrail should mention {cue!r}"
    assert "ransomware" in system
    assert "discussed=false" in system or "discussed = false" in system


def test_checklist_schema_requires_category_discussed_content_quote():
    props = ex._CHECKLIST_SCHEMA["properties"]["checklist"]["items"]["properties"]
    for field in ("category", "discussed", "content", "verbatim_quote", "properties"):
        assert field in props
    required = ex._CHECKLIST_SCHEMA["properties"]["checklist"]["items"]["required"]
    for field in ("category", "discussed", "content", "verbatim_quote"):
        assert field in required
    assert "properties" not in required, "properties (owner/due/etc) is optional"


# ── runner wiring: gated rollout (KG_CALL_REEXTRACT_ALLOWLIST) ──────────────────


def test_gated_rollout_env_absent_allows_every_tenant(monkeypatch):
    monkeypatch.delenv(runner.REEXTRACT_ALLOWLIST_ENV, raising=False)
    assert runner._call_provider_reextraction_allowed("ent-A") is True
    assert runner._call_provider_reextraction_allowed("ent-anything-at-all") is True


def test_gated_rollout_empty_env_string_allows_every_tenant(monkeypatch):
    monkeypatch.setenv(runner.REEXTRACT_ALLOWLIST_ENV, "")
    assert runner._call_provider_reextraction_allowed("ent-A") is True


def test_gated_rollout_env_set_restricts_to_listed_tenants(monkeypatch):
    monkeypatch.setenv(runner.REEXTRACT_ALLOWLIST_ENV, "ent-A, ent-B")
    assert runner._call_provider_reextraction_allowed("ent-A") is True
    assert runner._call_provider_reextraction_allowed("ent-B") is True
    assert runner._call_provider_reextraction_allowed("ent-C") is False


def test_sync_provider_skips_non_allowlisted_call_provider(monkeypatch):
    """A gated tenant's fireflies sync is a NO-OP for this tick — the puller
    is never even called — not an error, and the scheduler simply retries it
    on its next pass once the allowlist widens."""
    monkeypatch.setenv(runner.REEXTRACT_ALLOWLIST_ENV, "ent-allowed")
    pulled: list[str] = []

    def fake_pull(token, **kw):
        pulled.append(token)
        return iter([])

    monkeypatch.setitem(runner.PULLERS, "fireflies",
                        (fake_pull, "api_key", "hint"))
    out = runner.sync_provider(None, "ent-blocked", "fireflies", token="t")
    assert out["gated"] is True
    assert out["records"] == 0
    assert pulled == [], "the puller must never be called for a gated tenant"


def test_sync_provider_proceeds_for_allowlisted_call_provider(monkeypatch):
    monkeypatch.setenv(runner.REEXTRACT_ALLOWLIST_ENV, "ent-allowed")
    monkeypatch.setattr(runner, "seen_hashes", lambda *a, **k: set())
    monkeypatch.setattr(runner, "record_hashes", lambda *a, **k: None)
    monkeypatch.setattr(
        runner, "extract_document",
        lambda *a, **k: {"signals": 0, "themes": 0, "skipped": 0},
    )
    monkeypatch.setattr(
        runner, "run_checklist_pass",
        lambda *a, **k: {"signals": 0, "themes": 0, "skipped": 0, "signal_ids": []},
    )
    rec = RawRecord(provider="fireflies", kind="meeting", external_id="FF1",
                    title="t", text="body")
    out = runner.sync_provider(None, "ent-allowed", "fireflies", token="t",
                               records=[rec])
    assert out.get("gated") is None
    assert out["records"] == 1


def test_gating_does_not_restrict_non_call_providers(monkeypatch):
    """The allowlist only scopes `_CALL_PROVIDERS` — every other connector
    keeps syncing for every tenant regardless of the allowlist."""
    monkeypatch.setenv(runner.REEXTRACT_ALLOWLIST_ENV, "ent-allowed")
    monkeypatch.setattr(runner, "seen_hashes", lambda *a, **k: set())
    monkeypatch.setattr(runner, "record_hashes", lambda *a, **k: None)
    monkeypatch.setattr(
        runner, "extract_document",
        lambda *a, **k: {"signals": 0, "themes": 0, "skipped": 0},
    )
    rec = RawRecord(provider="clickup", kind="task", external_id="C1",
                    title="t", text="body")
    out = runner.sync_provider(None, "ent-blocked", "clickup", token="t",
                               records=[rec])
    assert out.get("gated") is None
    assert out["records"] == 1


# ── runner wiring: checklist pass invocation ──────────────────────────────────


def test_fireflies_checklist_pass_reads_full_transcript_not_the_digest(monkeypatch):
    """Config B: for Fireflies, `extract_document` (main pass) gets the
    cheap digest (`RawRecord.text`) while `run_checklist_pass` gets the FULL
    transcript (`RawRecord.checklist_text`) — same call (doc_name/source_ref
    match), DIFFERENT text. The checklist is now the sole full-transcript
    reader; known-fact recall flows through it, not the main pass."""
    monkeypatch.delenv(runner.REEXTRACT_ALLOWLIST_ENV, raising=False)
    monkeypatch.setattr(runner, "seen_hashes", lambda *a, **k: set())
    monkeypatch.setattr(runner, "record_hashes", lambda *a, **k: None)
    extract_calls: list[tuple] = []
    checklist_calls: list[tuple] = []

    def fake_extract(facade, enterprise_id, *, doc_name, text, source_ref=None, **kw):
        extract_calls.append((doc_name, text, source_ref))
        return {"signals": 1, "themes": 1, "skipped": 0}

    def fake_checklist(facade, enterprise_id, *, doc_name, text, source_ref=None, **kw):
        checklist_calls.append((doc_name, text, source_ref))
        return {"signals": 2, "themes": 0, "skipped": 0, "signal_ids": []}

    monkeypatch.setattr(runner, "extract_document", fake_extract)
    monkeypatch.setattr(runner, "run_checklist_pass", fake_checklist)
    rec = RawRecord(
        provider="fireflies", kind="meeting", external_id="FF1", title="t",
        text="summary: cheap digest only",
        checklist_text=("summary: cheap digest only\ntranscript:\n"
                        "CTO: the deep fact lives only in the full transcript"),
    )
    out = runner.sync_provider(None, "ent-A", "fireflies", token="t", records=[rec])

    assert len(extract_calls) == 1 and len(checklist_calls) == 1
    # Same call — doc_name and source_ref must still match.
    assert extract_calls[0][0] == checklist_calls[0][0]
    assert extract_calls[0][2] == checklist_calls[0][2]
    # DIFFERENT text: the main pass never sees the transcript block; the
    # checklist pass does.
    assert "transcript:" not in extract_calls[0][1]
    assert "deep fact" not in extract_calls[0][1]
    assert "transcript:" in checklist_calls[0][1]
    assert "deep fact" in checklist_calls[0][1]
    # totals combine both passes.
    assert out["signals"] == 3
    assert out["themes"] == 1


def test_zoom_main_pass_gets_haiku_summary_checklist_gets_full_transcript(monkeypatch):
    """Zoom/Meet have no native digest, so Config B derives the main pass's
    condensed input via a `claude-haiku-4-5` call
    (`extractor.summarize_call_transcript`) HERE in the runner, while the
    checklist pass still reads the untouched full transcript (Zoom/Meet have
    no separate `checklist_text` — `RawRecord.text` already IS the full
    transcript)."""
    monkeypatch.delenv(runner.REEXTRACT_ALLOWLIST_ENV, raising=False)
    monkeypatch.setattr(runner, "seen_hashes", lambda *a, **k: set())
    monkeypatch.setattr(runner, "record_hashes", lambda *a, **k: None)
    extract_calls: list[tuple] = []
    checklist_calls: list[tuple] = []
    summarize_calls: list[tuple] = []

    def fake_extract(facade, enterprise_id, *, doc_name, text, source_ref=None, **kw):
        extract_calls.append((doc_name, text, source_ref))
        return {"signals": 1, "themes": 1, "skipped": 0}

    def fake_checklist(facade, enterprise_id, *, doc_name, text, source_ref=None, **kw):
        checklist_calls.append((doc_name, text, source_ref))
        return {"signals": 2, "themes": 0, "skipped": 0, "signal_ids": []}

    def fake_summarize(enterprise_id, text):
        summarize_calls.append((enterprise_id, text))
        return "condensed haiku summary"

    monkeypatch.setattr(runner, "extract_document", fake_extract)
    monkeypatch.setattr(runner, "run_checklist_pass", fake_checklist)
    monkeypatch.setattr(runner, "summarize_call_transcript", fake_summarize)
    full_transcript = "Rep: this is the full zoom transcript with lots of detail."
    rec = RawRecord(provider="zoom", kind="meeting", external_id="Z1", title="t",
                    text=full_transcript)
    out = runner.sync_provider(None, "ent-A", "zoom", token="t", records=[rec])

    assert summarize_calls == [("ent-A", full_transcript)]
    # Main pass got the (fake) Haiku summary, not the full transcript.
    assert "condensed haiku summary" in extract_calls[0][1]
    assert full_transcript not in extract_calls[0][1]
    # Checklist pass got the ORIGINAL full transcript, untouched.
    assert full_transcript in checklist_calls[0][1]
    assert out["signals"] == 3


def test_zoom_summarization_failure_falls_back_to_full_transcript_for_main_pass(monkeypatch):
    """A Haiku summarization failure must degrade to feeding the main pass
    the full transcript — never fail the sync or leave the main pass with
    nothing. Uses google_meet to also prove Meet shares this path with Zoom."""
    monkeypatch.delenv(runner.REEXTRACT_ALLOWLIST_ENV, raising=False)
    monkeypatch.setattr(runner, "seen_hashes", lambda *a, **k: set())
    monkeypatch.setattr(runner, "record_hashes", lambda *a, **k: None)
    extract_calls: list[tuple] = []

    def fake_extract(facade, enterprise_id, *, doc_name, text, source_ref=None, **kw):
        extract_calls.append((doc_name, text, source_ref))
        return {"signals": 1, "themes": 0, "skipped": 0}

    def boom(enterprise_id, text):
        raise RuntimeError("haiku call failed")

    monkeypatch.setattr(runner, "extract_document", fake_extract)
    monkeypatch.setattr(
        runner, "run_checklist_pass",
        lambda *a, **k: {"signals": 0, "themes": 0, "skipped": 0, "signal_ids": []},
    )
    monkeypatch.setattr(runner, "summarize_call_transcript", boom)
    full_transcript = "Rep: this is the full meet transcript."
    rec = RawRecord(provider="google_meet", kind="meeting", external_id="M1",
                    title="t", text=full_transcript)
    out = runner.sync_provider(None, "ent-A", "google_meet", token="t", records=[rec])

    assert out["errors"] == []
    assert full_transcript in extract_calls[0][1]
    assert out["signals"] == 1


def test_non_call_provider_sync_never_invokes_checklist_pass(monkeypatch):
    monkeypatch.setattr(runner, "seen_hashes", lambda *a, **k: set())
    monkeypatch.setattr(runner, "record_hashes", lambda *a, **k: None)
    monkeypatch.setattr(
        runner, "extract_document",
        lambda *a, **k: {"signals": 1, "themes": 0, "skipped": 0},
    )

    def poison(*a, **k):
        raise AssertionError("checklist pass must not run for a non-call provider")

    monkeypatch.setattr(runner, "run_checklist_pass", poison)
    rec = RawRecord(provider="clickup", kind="task", external_id="C1",
                    title="t", text="body")
    runner.sync_provider(None, "ent-A", "clickup", token="t", records=[rec])


def test_checklist_pass_failure_is_isolated_and_ledger_still_advances(monkeypatch):
    """A checklist-pass exception must not fail the sync or block the main
    extraction's ledger progress — it only costs this cycle's recall boost
    for this one call."""
    monkeypatch.setattr(runner, "seen_hashes", lambda *a, **k: set())
    recorded: list[tuple] = []
    monkeypatch.setattr(runner, "record_hashes",
                        lambda *a, **k: recorded.append(a))
    monkeypatch.setattr(
        runner, "extract_document",
        lambda *a, **k: {"signals": 1, "themes": 0, "skipped": 0},
    )

    def boom(*a, **k):
        raise RuntimeError("checklist LLM call failed")

    monkeypatch.setattr(runner, "run_checklist_pass", boom)
    rec = RawRecord(provider="fireflies", kind="meeting", external_id="FF1",
                    title="t", text="call body")
    out = runner.sync_provider(None, "ent-A", "fireflies", token="t", records=[rec])

    assert out["errors"] == [], "a checklist failure is not a sync error"
    assert out["signals"] == 1, "only the main extraction's count survives"
    assert len(recorded) == 1, "the ledger still advanced for the successful unit"


# ── call-transcript condensation tuning (Zoom/Meet main-pass input) ─────────
#
# Live-verify (2026-08-26) found the first version of `summarize_call_transcript`
# only cut Zoom/Meet cost ~7% ($0.2086/call): a "dense, factual digest" prompt
# produced a summary long/detailed enough that the main pass's OWN extraction
# call still cost ~$0.0704, most of the way back to the pre-Config-B baseline.
# These tests guard the fix: a short, theme-only gist with a bounded token cap.


def _text_llm_result(text: str) -> LLMResult:
    return LLMResult(
        output=text, model="m", prompt_version=ex.CALL_SUMMARY_PROMPT_VERSION,
        input_tokens=0, output_tokens=0, cache_read_input_tokens=0,
        cache_creation_input_tokens=0, cost_usd=0.0, latency_ms=0,
        stop_reason="end_turn",
    )


def test_summarize_call_transcript_caps_output_and_asks_for_a_short_gist():
    """The Haiku call-summary must be SHORT — a bounded `max_tokens` (so a
    verbose model can't quietly regress the savings) and a prompt that
    explicitly asks for a brief, theme-only gist rather than a dense,
    fact-preserving digest. This is the exact regression the tuning fix
    closes: the too-dense first version re-inflated the main pass's own
    extraction cost."""
    with patch.object(ex, "llm_call",
                      return_value=_text_llm_result("a short gist")) as mock_call:
        result = ex.summarize_call_transcript("ent-x", "full transcript text")
    assert result == "a short gist"

    kwargs = mock_call.call_args.kwargs
    assert kwargs["max_tokens"] <= 400, (
        "max_tokens must bound the summary to a short gist, not a digest")
    assert kwargs["model"] == "claude-haiku-4-5"

    system = ex._CALL_SUMMARY_SYSTEM.lower()
    for phrase in ("short", "150-250 words", "general theme", "brief"):
        assert phrase in system, f"call-summary prompt should ask for {phrase!r}"
    # Explicitly tells the model the specific facts are handled elsewhere, so
    # leaving them out of THIS summary is correct, not a loss — the root
    # cause fix, not just a shorter word count.
    assert "not try to preserve" in system
    assert "leaving them out is correct" in system


def test_summarize_call_transcript_prompt_version_is_bumped_for_the_tuning_fix():
    """The prompt content materially changed (dense digest -> short gist) —
    the prompt_version must be bumped so any downstream cache/log keyed on it
    reflects the new behavior."""
    assert ex.CALL_SUMMARY_PROMPT_VERSION == "kg-call-summary-v2"


# ── real-LLM eval: checklist guardrail (Config B — checklist is now the ─────
# sole full-transcript reader, so ITS guardrail must independently hold)


@pytest.mark.integration
@pytest.mark.skipif(
    os.getenv("RUN_KG_EXTRACTOR_LLM") != "1",
    reason="real-LLM eval; set RUN_KG_EXTRACTOR_LLM=1 with a live ANTHROPIC key",
)
def test_checklist_guardrail_filters_simulated_incident_keeps_real_fact_real_llm():
    """Config B makes the checklist pass the SOLE full-transcript reader, so
    its OWN guardrail (not just the shared `_SYSTEM`'s, which no longer sees
    this transcript) must independently suppress a simulated tabletop
    scenario while still extracting a real fact stated plainly in the same
    transcript — the over-filter guard."""
    transcript = (
        "Facilitator: Let's run a quick security tabletop. Let's say we get "
        "hit by ransomware overnight and the primary database is encrypted "
        "— walk me through what you'd each do first.\n"
        "Ops: I'd isolate the affected hosts and page the on-call.\n"
        "Security: I'd start the comms tree and check our backups.\n"
        "Facilitator: Good. One real thing before we wrap: honestly we "
        "don't even have an NDA in place with Acme yet, and they keep "
        "asking for one.\n"
        "Legal: Right, I'll get that moving this week."
    )
    result = ex.llm_call(
        enterprise_id="ent-eval", agent="test:checklist-eval",
        purpose="extract_checklist", prompt_version=ex.CHECKLIST_PROMPT_VERSION,
        system=ex._CHECKLIST_SYSTEM,
        input=f"<document name='call.md'>\n{transcript}\n</document>",
        json_schema=ex._CHECKLIST_SCHEMA,
    )
    checklist = result.output.get("checklist", [])
    by_cat = {c.get("category"): c for c in checklist}

    objection = by_cat.get("objection") or {}
    assert "ransomware" not in (objection.get("content") or "").lower(), (
        f"simulated ransomware must not be reported as a real objection/"
        f"incident; got {objection}")

    legal = by_cat.get("legal") or {}
    assert legal.get("discussed") is True and "nda" in (legal.get("content") or "").lower(), (
        f"the plainly-real NDA gap must still be reported discussed; got {legal}")
