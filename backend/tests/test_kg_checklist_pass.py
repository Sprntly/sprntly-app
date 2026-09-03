"""Tests for the directed-checklist second pass (app.graph.extractor.
run_checklist_pass) and its wiring into app.kg_ingest.runner.sync_provider
for call-shaped providers (fireflies/zoom/google_meet).
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


def test_valid_at_stamps_a_checklist_minted_signal(facade):
    """A caller-supplied `valid_at` reaches a checklist-minted Signal exactly
    like the main pass's — same `_write_items` path, same contract. The
    runner threads the SAME call date to both passes for one call, so this
    keeps the two passes' signals dating (and staling) identically."""
    from datetime import datetime, timezone

    entries = [_entry("commercial", content="priced at $50k/yr",
                      quote="we're paying fifty thousand a year")]
    text = "Buyer: we're paying fifty thousand a year for this."
    call_date = datetime(2025, 6, 1, tzinfo=timezone.utc)
    _run_checklist(facade, entries, text, valid_at=call_date)
    sig = _csig(facade, "priced at $50k/yr")
    assert sig.valid_at == call_date


def test_empty_checklist_output_writes_nothing(facade):
    result = _run_checklist(facade, [], "anything")
    assert result == {"signals": 0, "themes": 0, "skipped": 0, "signal_ids": []}


# ── malformed batch-result shape: skip gracefully, never a bare AttributeError ─
#
# Live-verify (2026-08-27): a batched result's structured output can have a
# field come back the wrong TYPE (e.g. `checklist` as a bare string rather
# than a list of entry dicts) even though the tool call itself is a dict
# envelope. `_finish_checklist` guards this explicitly now — see
# `MalformedLLMResultError`.


def test_malformed_checklist_value_is_a_string_raises_named_error_with_a_log(facade, caplog):
    with caplog.at_level("WARNING"):
        with pytest.raises(ex.MalformedLLMResultError):
            ex._finish_checklist(
                facade, "ent-c", {"checklist": "oops a bare string"},
                doc_name="call.md", text="anything", origin=None,
                provenance_extra=None, source_ref=None,
            )
    assert any("non-list" in r.message for r in caplog.records)


def test_malformed_checklist_output_not_a_dict_raises_named_error(facade):
    with pytest.raises(ex.MalformedLLMResultError):
        ex._finish_checklist(
            facade, "ent-c", "not-a-dict-output", doc_name="call.md",
            text="anything", origin=None, provenance_extra=None,
            source_ref=None,
        )


def test_one_malformed_checklist_entry_is_dropped_other_entries_still_process(facade, caplog):
    """A `checklist` list that is itself well-formed but mixes a real entry
    with a stray non-dict element must not lose the good entry."""
    entries = [
        "a stray malformed string, not an entry dict",
        _entry("sentiment", content="real sentiment fact",
               quote="customers seem happy"),
    ]
    text = "Rep: customers seem happy with the rollout."
    with caplog.at_level("WARNING"):
        result = _run_checklist(facade, entries, text)
    assert result["signals"] == 1
    assert _csig(facade, "real sentiment fact") is not None
    assert any("malformed" in r.message for r in caplog.records)


# ── build_checklist_request / parse_checklist_response: batch-authoring seam ─
#
# Same proof as the main pass (see test_kg_extractor.py): the standalone
# build/parse pair — used by a caller assembling a BULK batch rather than
# calling run_checklist_pass live — composes to the EXACT SAME facade outcome
# as the live/sync inline path for identical model output.


def test_build_and_parse_checklist_compose_to_the_same_result_as_the_live_path(facade):
    quote = "we're paying fifty thousand a year for this platform"
    text = f"Buyer: {quote}."
    entries = [_entry("commercial", content="priced at $50k/yr", quote=quote)]

    with patch.object(ex, "llm_call", return_value=_checklist_llm_result(entries)), \
         patch.object(ex, "embed_texts",
                      side_effect=lambda texts, **k: [[0.0] * 4 for _ in texts]):
        live_result = ex.run_checklist_pass(facade, "ent-c-live", doc_name="call.md",
                                            text=text)

    kwargs = ex.build_checklist_request(doc_name="call.md", text=text)
    assert kwargs["model"] == ex.DEFAULT_MODEL
    assert kwargs["tools"][0]["input_schema"] == ex._CHECKLIST_SCHEMA

    from types import SimpleNamespace

    fake_message = SimpleNamespace(content=[
        SimpleNamespace(type="tool_use", name="submit_response",
                        input={"checklist": entries}),
    ])
    with patch.object(ex, "embed_texts",
                      side_effect=lambda texts, **k: [[0.0] * 4 for _ in texts]):
        batch_result = ex.parse_checklist_response(
            facade, "ent-c-batch", fake_message, doc_name="call.md", text=text,
        )

    # signal_ids are content-keyed by enterprise_id (uuid5), so the two runs'
    # ids legitimately differ (different enterprise_id) — compare everything
    # else, then verify each run's own signal independently below.
    assert {k: v for k, v in batch_result.items() if k != "signal_ids"} == \
           {k: v for k, v in live_result.items() if k != "signal_ids"}

    live_sig = facade.get_signal(
        "ent-c-live", str(uuid.uuid5(ex._NS, "ent-c-live|priced at $50k/yr")))
    batch_sig = facade.get_signal(
        "ent-c-batch", str(uuid.uuid5(ex._NS, "ent-c-batch|priced at $50k/yr")))
    assert live_sig is not None and batch_sig is not None
    assert live_sig.kind == batch_sig.kind == "commercial_term"


def test_build_checklist_request_renders_the_full_text_given(facade):
    """The batch build path is handed the FULL transcript text (the checklist
    pass's caller-supplied `text`, same contract as the live call) — never a
    condensed one."""
    kwargs = ex.build_checklist_request(doc_name="call.md", text="the full transcript body")
    user_content = kwargs["messages"][0]["content"]
    assert "<document name='call.md'>" in user_content
    assert "the full transcript body" in user_content


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


# ── a stated commercial figure survives extraction as structure ──────────────
# (not as a paraphrase, and never as the sentence itself).


def test_commercial_category_carries_grounded_amount_properties(facade):
    """A figure a speaker actually states on the call — David's own example,
    "[Sprntly] is $100,000" — is captured as structure, not just prose."""
    entries = [_entry(
        "commercial", content="Sprntly is worth $100,000 to this account",
        quote="if we had this feature, we can unblock 100,000 dollars",
        properties={"amount": 100000, "currency": "USD", "basis": "total-contract",
                    "certainty": "quoted"},
    )]
    text = "Buyer: if we had this feature, we can unblock 100,000 dollars in revenue."
    _run_checklist(facade, entries, text)
    sig = _csig(facade, "Sprntly is worth $100,000 to this account")
    assert sig is not None
    assert sig.kind == "commercial_term"
    assert sig.properties["amount"] == 100000.0
    assert sig.properties["currency"] == "USD"
    assert sig.properties["basis"] == "total-contract"
    assert sig.properties["certainty"] == "quoted"


def test_partnership_commercial_also_carries_grounded_amount(facade):
    """The partnership/ecosystem sibling category gets the same shape —
    both 'commercial' and 'partnership_commercial' carry it."""
    entries = [_entry(
        "partnership_commercial", content="Meridian referral worth $20k/yr",
        quote="the Meridian partnership brings in about twenty thousand a year",
        properties={"amount": 20000, "currency": "USD", "basis": "per-year",
                    "certainty": "estimated-by-speaker"},
    )]
    text = "Rep: the Meridian partnership brings in about twenty thousand a year."
    _run_checklist(facade, entries, text)
    sig = _csig(facade, "Meridian referral worth $20k/yr")
    assert sig is not None
    assert sig.properties["amount"] == 20000.0
    assert sig.properties["basis"] == "per-year"


def test_commercial_amount_omitted_when_no_figure_was_stated(facade):
    """I2/I3: no figure named -> no `amount` key at all — never a defaulted
    0, and no `currency`/`basis`/`certainty` written with no number behind
    them."""
    entries = [_entry(
        "commercial", content="pricing came up but no number was named",
        quote="pricing came up but we didn't get into specifics",
        properties={"currency": "USD", "basis": "one-off"},
    )]
    text = "Rep: pricing came up but we didn't get into specifics."
    _run_checklist(facade, entries, text)
    sig = _csig(facade, "pricing came up but no number was named")
    assert sig is not None
    assert "amount" not in sig.properties
    assert "currency" not in sig.properties
    assert "basis" not in sig.properties


def test_commercial_amount_never_defaults_to_zero_when_absent(facade):
    """Same contract, stated as its own assertion: I3 says unmeasured is
    `None` and never `0` — an omitted `amount` key must never read as a
    written `0`."""
    entries = [_entry(
        "commercial", content="budget was discussed",
        quote="we did talk about budget a little",
    )]
    text = "Rep: we did talk about budget a little."
    _run_checklist(facade, entries, text)
    sig = _csig(facade, "budget was discussed")
    assert sig is not None
    assert sig.properties.get("amount", "not zero") != 0
    assert "amount" not in sig.properties


def test_commercial_amount_ignored_when_not_a_real_number(facade):
    """A model that writes a string, bool or NaN into `amount` gets the
    property dropped rather than a garbage value persisted."""
    for bad_amount in ("a lot", True, float("nan"), float("inf")):
        entries = [_entry(
            "commercial", content=f"bad amount case {bad_amount!r}",
            quote="we discussed pricing on the call",
            properties={"amount": bad_amount, "currency": "USD"},
        )]
        text = "Rep: we discussed pricing on the call today."
        _run_checklist(facade, entries, text)
        sig = _csig(facade, f"bad amount case {bad_amount!r}")
        assert sig is not None
        assert "amount" not in sig.properties, f"should reject amount={bad_amount!r}"


def test_commercial_basis_and_certainty_outside_the_closed_vocabulary_are_dropped(facade):
    """`basis`/`certainty` are closed vocabularies — a value the model
    invents outside them is dropped, not persisted verbatim; `amount` (a
    real number) is kept regardless, since it is independently validated."""
    entries = [_entry(
        "commercial", content="an odd basis and certainty",
        quote="they said it would be about fifty thousand a year, roughly",
        properties={"amount": 50000, "basis": "roughly-guessed",
                    "certainty": "pretty-sure"},
    )]
    text = "Rep: they said it would be about fifty thousand a year, roughly."
    _run_checklist(facade, entries, text)
    sig = _csig(facade, "an odd basis and certainty")
    assert sig is not None
    assert sig.properties["amount"] == 50000.0
    assert "basis" not in sig.properties
    assert "certainty" not in sig.properties


def test_commercial_figure_still_requires_the_grounding_gate(facade):
    """A structured figure does not loosen the precision contract: a
    `commercial` entry with a fabricated (ungrounded) quote is dropped
    ENTIRELY — including its `amount` — exactly like every other category."""
    entries = [_entry(
        "commercial", content="invented a $2M deal that was never discussed",
        quote="this sentence about two million dollars never appears anywhere",
        properties={"amount": 2000000, "currency": "USD"},
    )]
    result = _run_checklist(facade, entries, "Rep: nothing commercial came up on this call.")
    assert result["signals"] == 0
    assert _csig(facade, "invented a $2M deal that was never discussed") is None


# ── buying-intent band: same mechanism, same pass, never the phrasing ────────


def test_objection_category_carries_a_high_intent_band(facade):
    """David's own example: 'if I have this, then I'll buy tomorrow' reads
    as high buying intent — captured as a band + a short basis, not the
    sentence itself."""
    entries = [_entry(
        "objection", content="pricing was the last blocker before signing",
        quote="if I have this feature, I'll buy tomorrow",
        properties={"intent_band": "high",
                    "intent_basis": "explicit readiness to buy immediately"},
    )]
    text = "Buyer: if I have this feature, I'll buy tomorrow."
    _run_checklist(facade, entries, text)
    sig = _csig(facade, "pricing was the last blocker before signing")
    assert sig is not None
    assert sig.properties["intent_band"] == "high"
    assert sig.properties["intent_basis"] == "explicit readiness to buy immediately"


def test_sentiment_and_commitment_categories_also_carry_intent_band(facade):
    """Three categories carry an intent band — 'objection' is covered above;
    'sentiment' and 'commitment' get the identical shape."""
    entries = [
        _entry("sentiment", content="lukewarm interest in the new tier",
               quote="this would be nice to have I suppose",
               properties={"intent_band": "low",
                           "intent_basis": "hedged, non-committal interest"}),
        _entry("commitment", content="Jane will review pricing by Friday",
               quote="Jane will look at pricing again by Friday",
               properties={"owner": "Jane Doe", "due": "Friday",
                           "intent_band": "medium",
                           "intent_basis": "engaged but no buy signal yet"}),
    ]
    text = (
        "Buyer: this would be nice to have I suppose.\n"
        "PM: Jane will look at pricing again by Friday."
    )
    _run_checklist(facade, entries, text)
    sentiment_sig = _csig(facade, "lukewarm interest in the new tier")
    assert sentiment_sig.properties["intent_band"] == "low"
    commitment_sig = _csig(facade, "Jane will review pricing by Friday")
    # commitment keeps ITS existing owner/due shape AND gains intent —
    # additive, not a replacement of the pre-existing contract.
    assert commitment_sig.properties["owner"] == "Jane Doe"
    assert commitment_sig.properties["due"] == "Friday"
    assert commitment_sig.properties["intent_band"] == "medium"


def test_intent_band_outside_high_medium_low_is_dropped(facade):
    entries = [_entry(
        "objection", content="ambiguous intent case",
        quote="we might consider it at some point maybe",
        properties={"intent_band": "very high indeed", "intent_basis": "unsure"},
    )]
    text = "Buyer: we might consider it at some point maybe."
    _run_checklist(facade, entries, text)
    sig = _csig(facade, "ambiguous intent case")
    assert sig is not None
    assert "intent_band" not in sig.properties
    assert "intent_basis" not in sig.properties


def test_intent_basis_that_is_the_verbatim_quote_is_dropped_not_persisted(facade):
    """The never-persist-the-speech discipline applied a second time: if a
    model tries to smuggle the transcript sentence into `intent_basis`
    instead of `verbatim_quote`, the sanitizer must still catch it.
    `intent_band` survives (it is a closed-vocabulary classification, not
    text); `intent_basis` does not."""
    quote = "if I have this feature working end to end I will buy tomorrow morning"
    entries = [_entry(
        "objection", content="strong buy signal on the call",
        quote=quote,
        properties={"intent_band": "high", "intent_basis": quote},
    )]
    text = f"Buyer: {quote}."
    _run_checklist(facade, entries, text)
    sig = _csig(facade, "strong buy signal on the call")
    assert sig is not None
    assert sig.properties["intent_band"] == "high"
    assert "intent_basis" not in sig.properties


def test_intent_band_is_not_carried_on_categories_outside_the_named_three(facade):
    """The intent-band shape is scoped to 'objection'/'sentiment'/
    'commitment' — a model writing `intent_band` on an unrelated category
    (e.g. 'timeline') must not have it persisted; that category keeps only
    its own documented shape."""
    entries = [_entry(
        "timeline", content="renewal is tied to fiscal year",
        quote="we need this before our fiscal year renewal",
        properties={"urgency": "high", "intent_band": "high"},
    )]
    text = "Buyer: we need this before our fiscal year renewal."
    _run_checklist(facade, entries, text)
    sig = _csig(facade, "renewal is tied to fiscal year")
    assert sig is not None
    assert sig.properties["urgency"] == "high"
    assert "intent_band" not in sig.properties


# ── Proven directly: no transcript text reaches a written Signal ────────────


def test_no_property_value_reproduces_the_verbatim_quote(facade):
    """The precision contract stated as a property test, not just an
    assertion in prose: for every category shape this ticket adds, sweep
    every STRING value written into `properties` and confirm none of them
    is the verbatim quote (or a near-verbatim copy of it) — the exact leak
    the never-persist-the-speech rule forbids taking a second path through
    `properties` instead of the already-guarded `verbatim_quote` field."""
    quote = "if we had this feature we could unblock one hundred thousand dollars"
    entries = [
        _entry("commercial", content="value framed at $100k",
               quote=quote,
               properties={"amount": 100000, "currency": "USD",
                           "basis": "total-contract", "certainty": "quoted"}),
        _entry("objection", content="strong buying signal",
               quote="if I have this then I will buy tomorrow for sure",
               properties={"intent_band": "high", "intent_basis": quote}),
    ]
    text = (
        f"Buyer: {quote}.\n"
        "Buyer: if I have this then I will buy tomorrow for sure."
    )
    _run_checklist(facade, entries, text)
    for content in ("value framed at $100k", "strong buying signal"):
        sig = _csig(facade, content)
        assert sig is not None
        for key, value in sig.properties.items():
            if isinstance(value, str):
                assert value.strip().lower() != quote.strip().lower(), (
                    f"property {key!r} on signal {content!r} reproduced the "
                    f"verbatim quote"
                )
                assert quote.lower() not in value.lower(), (
                    f"property {key!r} on signal {content!r} contains the "
                    f"verbatim quote as a substring"
                )


def test_checklist_properties_description_documents_grounded_boundaries():
    """Content property test on the LLM-facing schema description (the
    prompt surface a model actually reads): the commercial and buying-intent
    shapes are documented, and the never-invent / never-extrapolate / never-
    verbatim boundaries are stated in words, not just enforced in code."""
    desc = ex._CHECKLIST_SCHEMA["properties"]["checklist"]["items"]["properties"][
        "properties"]["description"]
    for token in ("amount", "currency", "basis", "certainty",
                  "intent_band", "intent_basis",
                  "one-off", "per-year", "per-seat", "total-contract",
                  "quoted", "asked", "estimated-by-speaker",
                  "high", "medium", "low"):
        assert token in desc, f"properties description should mention {token!r}"
    assert "never" in desc.lower()
    assert "extrapolat" in desc.lower()
    assert "verbatim" in desc.lower() or "own words" in desc.lower()
    assert len(desc) > 400


# ── runner wiring: call providers always process (no per-tenant gate) ─────────


def test_sync_provider_processes_call_provider_for_any_tenant(monkeypatch):
    """No allowlist gates `_CALL_PROVIDERS` — every tenant's call-provider
    sync proceeds through the full pipeline (puller + main pass + checklist
    pass) unconditionally."""
    monkeypatch.setattr(runner, "seen_hashes", lambda *a, **k: set())
    monkeypatch.setattr(runner, "record_hashes", lambda *a, **k: None)
    monkeypatch.setattr(
        runner, "extract_document",
        lambda *a, **k: {"signals": 1, "themes": 0, "skipped": 0},
    )
    monkeypatch.setattr(
        runner, "run_checklist_pass",
        lambda *a, **k: {"signals": 1, "themes": 0, "skipped": 0, "signal_ids": []},
    )
    rec = RawRecord(provider="fireflies", kind="meeting", external_id="FF1",
                    title="t", text="body")
    out = runner.sync_provider(None, "ent-any-tenant-at-all", "fireflies",
                               token="t", records=[rec])
    assert out["records"] == 1
    assert out["signals"] == 2


# ── runner wiring: checklist pass invocation ──────────────────────────────────


def test_fireflies_checklist_pass_reads_full_transcript_not_the_digest(monkeypatch):
    """Config B: for Fireflies, `extract_document` (main pass) gets the
    cheap digest (`RawRecord.text`) while `run_checklist_pass` gets the FULL
    transcript (`RawRecord.checklist_text`) — same call (doc_name/source_ref
    match), DIFFERENT text. The checklist is now the sole full-transcript
    reader; known-fact recall flows through it, not the main pass."""
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
