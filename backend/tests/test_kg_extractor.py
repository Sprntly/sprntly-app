"""Tests for app.graph.extractor.extract_document — the source_type_default
coercion and provenance_extra stamping used by connector-category uploads."""
from __future__ import annotations

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
