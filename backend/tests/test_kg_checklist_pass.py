"""Tests for the directed-checklist second pass (app.graph.extractor.
run_checklist_pass) and its wiring into app.kg_ingest.runner.sync_provider
for call-shaped providers (fireflies/zoom/google_meet), plus the gated-rollout
allowlist that scopes that pipeline's rollout.
"""
from __future__ import annotations

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


def test_checklist_system_prompt_names_all_11_categories_and_precision_contract():
    """Content property test on the LLM-facing checklist system prompt: every
    category is named (so the model can't drift the vocabulary) and the
    precision contract (honest not-discussed over invention, verbatim
    grounding) is present."""
    system = ex._CHECKLIST_SYSTEM.lower()
    for key, *_rest in ex._CHECKLIST_CATEGORIES:
        assert key in system, f"checklist system prompt should name category {key!r}"
    assert "not discussed" in system or "not-discussed" in system
    assert "verbatim" in system
    assert "invent" in system
    assert len(ex._CHECKLIST_SYSTEM) > 500


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


def test_call_provider_sync_invokes_checklist_pass_with_matching_args(monkeypatch):
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
    rec = RawRecord(provider="fireflies", kind="meeting", external_id="FF1",
                    title="t", text="call body")
    out = runner.sync_provider(None, "ent-A", "fireflies", token="t", records=[rec])

    assert len(checklist_calls) == 1
    assert checklist_calls[0] == extract_calls[0], (
        "the checklist pass must run over the SAME doc_name/text/source_ref "
        "as the main extraction pass, on the same call")
    # totals combine both passes.
    assert out["signals"] == 3
    assert out["themes"] == 1


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
