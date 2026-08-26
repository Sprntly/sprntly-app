"""Tests for app.graph.extractor.extract_document — the source_type_default
coercion and provenance_extra stamping used by connector-category uploads."""
from __future__ import annotations

import os
from unittest.mock import patch

import pytest

import app.graph.extractor as ex
from app.graph.gateway import LLMResult


@pytest.fixture
def facade(isolated_settings):
    from app.graph import GraphFacade
    return GraphFacade()


def _llm_result(items: list[dict]) -> LLMResult:
    return LLMResult(
        output={"signals": items}, model="m", prompt_version=ex.PROMPT_VERSION,
        input_tokens=0, output_tokens=0, cache_read_input_tokens=0,
        cache_creation_input_tokens=0, cost_usd=0.0, latency_ms=0,
        stop_reason="end_turn",
    )


def _item(content: str, source_type: str) -> dict:
    return {"kind": "feature_request", "content": content,
            "source_type": source_type, "theme": "Search",
            "relationship": "REQUESTS", "confidence": 0.9}


def _extract(facade, items, **kwargs) -> None:
    with patch.object(ex, "llm_call", return_value=_llm_result(items)), \
         patch.object(ex, "embed_texts",
                      side_effect=lambda texts, **k: [[0.0] * 4 for _ in texts]):
        ex.extract_document(facade, "ent-x", doc_name="calls.md",
                            text="doc body", **kwargs)


def _signals(facade):
    # query via edges is overkill here; read the signal store directly through
    # the facade's public per-id fetch by re-deriving the content-keyed ids.
    import uuid
    ids = [str(uuid.uuid5(ex._NS, f"ent-x|{c}"))
           for c in ("seeded fact", "revenue fact", "weird fact")]
    return facade.get_signals("ent-x", ids)


def test_source_type_default_coerces_seeded_and_unknown_types(facade):
    """With source_type_default set, seeded types (pm_manual/verbal_claim/
    agent_inferred) and out-of-vocabulary types are re-stamped; an evidence
    type the LLM picked on merit is kept."""
    _extract(facade, [
        _item("seeded fact", "pm_manual"),
        _item("revenue fact", "revenue"),
        _item("weird fact", "meeting_notes"),   # not in SIGNAL_SOURCE_TYPES
    ], source_type_default="customer_voice")

    by_content = {s.content: s for s in _signals(facade).values()}
    assert by_content["seeded fact"].source_type == "customer_voice"
    assert by_content["weird fact"].source_type == "customer_voice"
    assert by_content["revenue fact"].source_type == "revenue"


def test_no_default_keeps_llm_source_types(facade):
    """Without source_type_default (every pre-existing caller), the LLM's
    choice lands verbatim — no behavior change."""
    _extract(facade, [_item("seeded fact", "pm_manual")])
    by_content = {s.content: s for s in _signals(facade).values()}
    assert by_content["seeded fact"].source_type == "pm_manual"


def test_force_source_type_overrides_even_merited_evidence_types(facade):
    """force_source_type PINS every signal, unlike source_type_default which
    keeps an evidence type the LLM picked on merit.

    Used where the DOCUMENT CLASS decides evidentiary weight and a model-picked
    connected type would be an integrity problem — the roadmap path, whose
    quoted metrics must never count as connected evidence in the brief gate."""
    _extract(facade, [
        _item("seeded fact", "pm_manual"),
        _item("revenue fact", "revenue"),      # merited — still overridden
        _item("weird fact", "meeting_notes"),  # out of vocabulary
    ], force_source_type="pm_manual")

    by_content = {s.content: s for s in _signals(facade).values()}
    assert {s.source_type for s in by_content.values()} == {"pm_manual"}


def test_force_source_type_takes_precedence_over_default(facade):
    """When both are passed, the pin wins."""
    _extract(facade, [_item("revenue fact", "revenue")],
             source_type_default="customer_voice", force_source_type="pm_manual")
    by_content = {s.content: s for s in _signals(facade).values()}
    assert by_content["revenue fact"].source_type == "pm_manual"


def test_provenance_extra_merged_with_origin(facade):
    """provenance_extra rides into every signal's provenance next to origin."""
    _extract(facade, [_item("seeded fact", "pm_manual")],
             origin="connector",
             provenance_extra={"channel": "upload", "category": "voice"})
    sig = next(iter(_signals(facade).values()))
    assert sig.provenance["origin"] == "connector"
    assert sig.provenance["channel"] == "upload"
    assert sig.provenance["category"] == "voice"


# ── skill_id routing (connector extraction skills) ──────────────────────────


def test_skill_id_none_by_default_leaves_llm_call_unchanged(facade):
    """Every pre-existing caller passes no skill_id — llm_call must receive
    skill=None, and no skill_id key lands in provenance."""
    with patch.object(ex, "llm_call", return_value=_llm_result(
            [_item("seeded fact", "pm_manual")])) as mock_call, \
         patch.object(ex, "embed_texts",
                      side_effect=lambda texts, **k: [[0.0] * 4 for _ in texts]):
        ex.extract_document(facade, "ent-x", doc_name="calls.md", text="doc body")
    assert mock_call.call_args.kwargs["skill"] is None
    sig = next(iter(_signals(facade).values()))
    assert "skill_id" not in sig.provenance


def test_skill_id_passed_through_to_llm_call(facade):
    """A connector-bound skill_id rides straight into gateway.llm_call(skill=...),
    which is what folds the skill's SKILL.md into the cacheable prompt prefix
    and suffixes prompt_version with +<id>@<hash> (see gateway._build_method_prefix)."""
    with patch.object(ex, "llm_call", return_value=_llm_result(
            [_item("seeded fact", "pm_manual")])) as mock_call, \
         patch.object(ex, "embed_texts",
                      side_effect=lambda texts, **k: [[0.0] * 4 for _ in texts]):
        ex.extract_document(facade, "ent-x", doc_name="calls.md", text="doc body",
                            skill_id="hubspot-extraction")
    assert mock_call.call_args.kwargs["skill"] == "hubspot-extraction"


def test_skill_id_stamped_on_every_written_signal(facade):
    """Every Signal a skill-routed batch produces can point back to the exact
    skill that produced it — the field a later formal skill_id column reads
    from."""
    _extract(facade, [
        _item("seeded fact", "pm_manual"),
        _item("revenue fact", "revenue"),
    ], skill_id="jira-extraction")
    by_content = {s.content: s for s in _signals(facade).values()}
    assert by_content["seeded fact"].provenance["skill_id"] == "jira-extraction"
    assert by_content["revenue fact"].provenance["skill_id"] == "jira-extraction"


def test_skill_id_coexists_with_origin_and_provenance_extra(facade):
    """skill_id, origin, and provenance_extra all land in the same dict without
    clobbering each other — the shape a real connector call site uses."""
    _extract(facade, [_item("seeded fact", "pm_manual")],
             origin="connector", skill_id="clickup-extraction",
             provenance_extra={"channel": "upload"})
    sig = next(iter(_signals(facade).values()))
    assert sig.provenance["origin"] == "connector"
    assert sig.provenance["skill_id"] == "clickup-extraction"
    assert sig.provenance["channel"] == "upload"


# ── typed field promotion (skill_id/origin/channel/evidence_eligible) ──────────


def _sig(facade, content: str):
    import uuid
    sig_id = str(uuid.uuid5(ex._NS, f"ent-x|{content}"))
    return facade.get_signal("ent-x", sig_id)


def test_typed_skill_id_defaults_to_generic_tag(facade):
    """Every signal extract_document writes gets a typed skill_id — 'generic'
    when no real skill was used, even though the informal provenance key is
    (unchanged) omitted in that case."""
    _extract(facade, [_item("generic-tagged fact", "pm_manual")])
    sig = _sig(facade, "generic-tagged fact")
    assert sig.skill_id == "generic"
    assert "skill_id" not in sig.provenance  # informal dict behavior unchanged


def test_typed_skill_id_carries_real_skill(facade):
    _extract(facade, [_item("typed skill fact", "pm_manual")],
             skill_id="hubspot-extraction")
    sig = _sig(facade, "typed skill fact")
    assert sig.skill_id == "hubspot-extraction"


def test_typed_origin_and_channel_fields_populated(facade):
    _extract(facade, [_item("origin channel fact", "pm_manual")],
             origin="connector", provenance_extra={"channel": "upload"})
    sig = _sig(facade, "origin channel fact")
    assert sig.origin == "connector"
    assert sig.channel == "upload"


def test_evidence_eligible_true_for_connected_source_type_and_origin(facade):
    _extract(facade, [_item("connected evidence fact", "revenue")],
             origin="connector")
    sig = _sig(facade, "connected evidence fact")
    assert sig.evidence_eligible is True


def test_evidence_eligible_false_for_seeded_source_type(facade):
    _extract(facade, [_item("seeded non-evidence fact", "pm_manual")])
    sig = _sig(facade, "seeded non-evidence fact")
    assert sig.evidence_eligible is False


def test_evidence_eligible_false_for_non_evidence_origin_even_if_connected_type(facade):
    """The origin exclusion (web_research) holds even when source_type alone
    would look like connected evidence — mirrors convergence.NON_EVIDENCE_ORIGINS."""
    _extract(facade, [_item("scraped revenue fact", "revenue")],
             origin="web_research")
    sig = _sig(facade, "scraped revenue fact")
    assert sig.evidence_eligible is False


# ── RELATES_TO review-queue logging ─────────────────────────────────────────


def test_relates_to_fallback_is_logged(facade, caplog):
    """A relationship type outside the 5-value extractor allow-list is
    bucketed into RELATES_TO AND logged with the model's original proposal —
    not silently discarded."""
    item = _item("novel relationship fact", "pm_manual")
    item["relationship"] = "VALIDATES"  # in RELATIONSHIP_VOCAB, not the 5-value allow-list
    with caplog.at_level("WARNING", logger="app.graph.extractor"):
        _extract(facade, [item])
    assert any("RELATES_TO fallback" in r.message and "VALIDATES" in r.message
               for r in caplog.records)


def test_allowed_relationship_type_is_not_logged(facade, caplog):
    with caplog.at_level("WARNING", logger="app.graph.extractor"):
        _extract(facade, [_item("normal relationship fact", "pm_manual")])  # REQUESTS
    assert not any("RELATES_TO fallback" in r.message for r in caplog.records)


# ── triage pass ──────────────────────────────────────────────────────────────


def _triage_result(relevant: bool, category: str = "customer_feedback", reason: str = "test"):
    from app.graph.triage import TriageResult
    return TriageResult(relevant=relevant, category=category, reason=reason,
                        confidence=0.9, source="llm")


def test_triage_off_by_default_no_extra_call(facade):
    """Every pre-existing caller (triage=False, the default) never touches
    app.graph.triage — no behavior/cost change for them."""
    import app.graph.triage as triage_mod
    with patch.object(triage_mod, "triage_batch") as mock_triage:
        _extract(facade, [_item("untouched fact", "pm_manual")])
    mock_triage.assert_not_called()


def test_triage_relevant_batch_proceeds_to_extraction(facade):
    import app.graph.triage as triage_mod
    with patch.object(triage_mod, "triage_batch",
                      return_value=_triage_result(True, category="support_ticket")):
        _extract(facade, [_item("relevant fact", "pm_manual")], triage=True)
    sig = _sig(facade, "relevant fact")
    assert sig is not None
    assert sig.provenance["triage_category"] == "support_ticket"


def test_triage_routes_to_category_skill_when_caller_passed_none(facade):
    """When the caller passes no explicit skill_id, a category with a
    CATEGORY_SKILLS entry is routed to that skill — the scope-2 'route to a
    matching skill' wiring. CATEGORY_SKILLS is empty by default (see
    app.graph.triage docstring); this proves the mechanism works once an
    entry exists, without asserting anything about today's (empty) map."""
    import app.graph.triage as triage_mod
    with patch.object(triage_mod, "triage_batch",
                      return_value=_triage_result(True, category="sales_deal")), \
         patch.object(triage_mod, "CATEGORY_SKILLS", {"sales_deal": "hubspot-extraction"}), \
         patch.object(ex, "llm_call", return_value=_llm_result(
             [_item("routed fact", "pm_manual")])) as mock_call, \
         patch.object(ex, "embed_texts",
                      side_effect=lambda texts, **k: [[0.0] * 4 for _ in texts]):
        ex.extract_document(facade, "ent-x", doc_name="deal.md", text="doc body",
                            triage=True)
    assert mock_call.call_args.kwargs["skill"] == "hubspot-extraction"
    sig = _sig(facade, "routed fact")
    assert sig.skill_id == "hubspot-extraction"


def test_triage_category_skill_does_not_override_explicit_skill_id(facade):
    """An explicit caller-passed skill_id (e.g. connector-based routing) wins
    over triage's category-based routing."""
    import app.graph.triage as triage_mod
    with patch.object(triage_mod, "triage_batch",
                      return_value=_triage_result(True, category="sales_deal")), \
         patch.object(triage_mod, "CATEGORY_SKILLS", {"sales_deal": "hubspot-extraction"}), \
         patch.object(ex, "llm_call", return_value=_llm_result(
             [_item("explicit skill fact", "pm_manual")])) as mock_call, \
         patch.object(ex, "embed_texts",
                      side_effect=lambda texts, **k: [[0.0] * 4 for _ in texts]):
        ex.extract_document(facade, "ent-x", doc_name="deal.md", text="doc body",
                            triage=True, skill_id="jira-extraction")
    assert mock_call.call_args.kwargs["skill"] == "jira-extraction"


def test_triage_filtered_batch_skips_extraction_and_is_logged(facade, caplog):
    """A not-relevant verdict skips extraction entirely (no signal written)
    AND is logged — the AC's 'logged, not silently dropped' requirement."""
    import app.graph.triage as triage_mod
    with caplog.at_level("WARNING", logger="app.graph.triage"), \
         patch.object(triage_mod, "triage_batch",
                      return_value=_triage_result(False, category="internal_admin",
                                                   reason="HR paperwork")), \
         patch.object(ex, "llm_call") as mock_llm_call:
        result = ex.extract_document(facade, "ent-x", doc_name="hr-doc.md",
                                     text="doc body", triage=True)
    mock_llm_call.assert_not_called()  # extraction itself never ran
    assert result["filtered"] is True
    assert result["triage_category"] == "internal_admin"
    assert result["signals"] == 0
    assert any("ingest triage filtered batch" in r.message for r in caplog.records)
    assert any("internal_admin" in r.message for r in caplog.records)


def test_triage_batch_fails_open_on_gateway_error():
    """A triage_batch exception must never lose data — it fails OPEN
    (relevant=True, category='uncategorized') rather than raising or
    filtering, so a triage outage degrades to 'extract everything'."""
    import app.graph.triage as triage_mod
    with patch.object(triage_mod, "llm_call", side_effect=RuntimeError("gateway down")):
        verdict = triage_mod.triage_batch(
            enterprise_id="ent-x", agent="a", doc_name="d", text="some text"
        )
    assert verdict.relevant is True
    assert verdict.category == "uncategorized"
    assert verdict.source == "fail_open"


def test_triage_fail_open_verdict_still_extracts_via_extract_document(facade):
    """extract_document(triage=True) against a broken triage gateway still
    writes the signal (fail-open), instead of losing the document."""
    import app.graph.triage as triage_mod
    with patch.object(triage_mod, "llm_call", side_effect=RuntimeError("gateway down")):
        _extract(facade, [_item("fail-open fact", "pm_manual")], triage=True)
    sig = _sig(facade, "fail-open fact")
    assert sig is not None
    assert sig.provenance["triage_category"] == "uncategorized"


def test_triage_unknown_category_coerced_to_other(facade):
    import app.graph.triage as triage_mod

    def fake_llm_call(**kw):
        return _llm_result_for_triage({"relevant": True, "category": "not-a-real-category",
                                       "reason": "x", "confidence": 0.5})

    with patch.object(triage_mod, "llm_call", side_effect=fake_llm_call):
        verdict = triage_mod.triage_batch(
            enterprise_id="ent-x", agent="a", doc_name="d", text="some text"
        )
    assert verdict.category == "other"


def _llm_result_for_triage(output: dict) -> LLMResult:
    return LLMResult(
        output=output, model="m", prompt_version="p",
        input_tokens=0, output_tokens=0, cache_read_input_tokens=0,
        cache_creation_input_tokens=0, cost_usd=0.0, latency_ms=0,
        stop_reason="end_turn",
    )


# ── vendor-side taxonomy + owner/timing preservation ─────────────────────────
#
# The extractor prompt used to name only customer-voice exemplars, so vendor-side
# facts (our own pricing, the status of our own capabilities, engagement
# logistics) were dropped even when present, and an owner-attributed action item
# lost its owner/due/status. These cover the broadened prompt + the fact that the
# new kinds and the owner/due/status properties survive into the KG unchanged.


def _kind_item(content: str, kind: str, *, source_type: str = "revenue",
               theme: str = "Pricing", properties: dict | None = None) -> dict:
    item = {"kind": kind, "content": content, "source_type": source_type,
            "theme": theme, "relationship": "SUPPORTS", "confidence": 0.9}
    if properties is not None:
        item["properties"] = properties
    return item


def test_prompt_v2_names_vendor_side_scope_and_owner_timing():
    """Content property test on the LLM-facing strings (the actual change):
    the system prompt names vendor-side facts and owner/due/status, the kind
    vocabulary carries the new kinds, and the properties description shows the
    owner/due/status shape. PROMPT_VERSION is bumped so re-extraction cache-busts.
    """
    assert ex.PROMPT_VERSION == "extract-doc-v2"

    system = ex._SYSTEM.lower()
    for term in ("pricing", "commercial", "capabilit", "logistic",
                 "owner", "due", "status"):
        assert term in system, f"_SYSTEM should mention {term!r}"

    props = ex._EXTRACT_SCHEMA["properties"]["signals"]["items"]["properties"]
    kind_desc = props["kind"]["description"].lower()
    for term in ("pricing", "commercial_term", "capability", "finding"):
        assert term in kind_desc, f"kind description should list {term!r}"

    props_desc = props["properties"]["description"].lower()
    for term in ("owner", "due", "status"):
        assert term in props_desc, f"properties description should show {term!r}"


def test_vendor_side_kinds_and_owner_properties_persist(facade):
    """A pricing kind, a capability kind, and an owner-attributed action item
    (properties.owner/due/status) all survive extraction into the signal store
    unchanged — the extractor neither validates the kind away nor drops the
    attribution properties."""
    items = [
        _kind_item("We charge $30,000 a year for 50 users", "pricing",
                   properties={"amount_usd": 30000, "seats": 50}),
        _kind_item("The platform supports remote runbook versioning", "capability",
                   source_type="verbal_claim", theme="Runbook versioning"),
        _kind_item("Jane Doe to send the SOW by Friday", "finding",
                   source_type="communication", theme="SOW",
                   properties={"owner": "Jane Doe", "due": "Friday", "status": "open"}),
    ]
    with patch.object(ex, "llm_call", return_value=_llm_result(items)), \
         patch.object(ex, "embed_texts",
                      side_effect=lambda texts, **k: [[0.0] * 4 for _ in texts]):
        ex.extract_document(facade, "ent-x", doc_name="calls.md", text="doc body")

    import uuid
    ids = [str(uuid.uuid5(ex._NS, f"ent-x|{it['content']}")) for it in items]
    by_content = {s.content: s for s in facade.get_signals("ent-x", ids).values()}

    assert by_content["We charge $30,000 a year for 50 users"].kind == "pricing"
    assert by_content["The platform supports remote runbook versioning"].kind == "capability"
    owner_sig = by_content["Jane Doe to send the SOW by Friday"]
    assert owner_sig.properties.get("owner") == "Jane Doe"
    assert owner_sig.properties.get("due") == "Friday"
    assert owner_sig.properties.get("status") == "open"


# A REAL-LLM eval: it exercises the actual broadened prompt + schema against
# Anthropic and asserts the vendor-side + owner-attributed signals are minted.
# Skipped by default because it spends a real API call; run it with a live key:
#     RUN_KG_EXTRACTOR_LLM=1 ANTHROPIC_API_KEY=... pytest \
#         tests/test_kg_extractor.py -k real_llm
# It drives the genuine gateway loop (no stubbed extraction) and inspects the
# raw model output, so it never touches the DB (log=False, no facade write).
@pytest.mark.integration
@pytest.mark.skipif(
    os.getenv("RUN_KG_EXTRACTOR_LLM") != "1",
    reason="real-LLM eval; set RUN_KG_EXTRACTOR_LLM=1 with a live ANTHROPIC key",
)
def test_vendor_side_and_owner_extraction_real_llm():
    from app.graph.gateway import llm_call

    summary = (
        "summary: We walked the customer through pricing. We charge $30,000 a "
        "year for 50 users, and offer $250 per hour facilitation on top for "
        "onboarding workshops. They asked whether the platform can version "
        "runbooks; the platform supports remote runbook versioning today. "
        "action items: **Jane Doe** to send the SOW by Friday."
    )
    result = llm_call(
        enterprise_id="ent-eval", agent="test:extractor-eval",
        purpose="extract_document", prompt_version=ex.PROMPT_VERSION,
        system=ex._SYSTEM,
        input=f"<document name='calls.md'>\n{summary}\n</document>",
        json_schema=ex._EXTRACT_SCHEMA, log=False,
    )
    signals = result.output.get("signals", [])
    kinds = {s.get("kind") for s in signals}

    assert kinds & {"pricing", "commercial_term"}, (
        f"expected a pricing/commercial_term signal, got kinds={kinds}")
    assert kinds & {"capability", "product_capability_status"}, (
        f"expected a capability signal, got kinds={kinds}")
    assert any((s.get("properties") or {}).get("owner") for s in signals), (
        f"expected an action item with properties.owner, got {signals}")
