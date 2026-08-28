"""Query-time map-reduce-over-corpus engine (app.corpus_mapreduce) — partition
correctness, the deterministic Python reduce, the cross-batch id guard, honest
unclassified accounting, gateway telemetry routing, and the local fan-out cap.

No network/LLM/DB: `app.graph.gateway.llm_call` is patched in every test.
"""
from __future__ import annotations

import threading
import time
from dataclasses import dataclass
from types import SimpleNamespace

import pytest

import app.corpus_mapreduce as cmr
import app.graph.gateway as gateway_mod
from app import llm as llm_module


@dataclass
class _Item:
    id: str
    text: str = "content"


def _items(n: int) -> list[_Item]:
    return [_Item(id=f"c{i}") for i in range(n)]


def _spec(**overrides) -> cmr.CorpusMapReduceSpec:
    kw = dict(
        domain="test_domain",
        fetch=lambda *a, **k: [],
        render_item=lambda it: it.text,
        item_id=lambda it: it.id,
        render_label=lambda it: it.id,
        phase_label="Analyzing the findings…",
        base_discipline="BASE",
        criterion="CRIT",
        verdict_schema={"type": "object"},
        batch_size=10,
    )
    kw.update(overrides)
    return cmr.CorpusMapReduceSpec(**kw)


def _run(spec, items, monkeypatch, fake_llm, on_phase=None, constraints=None):
    monkeypatch.setattr(gateway_mod, "llm_call", fake_llm)
    return cmr.run(
        spec, enterprise_id="ent-A", question="how many calls raised X",
        window=SimpleNamespace(label="last 7 days"), constraints=constraints,
        on_phase=on_phase, items=items,
    )


# ── partition correctness ────────────────────────────────────────────────────

def test_partition_splits_into_consecutive_batches_of_batch_size():
    items = list(range(23))
    batches = cmr._partition(items, 10)
    assert [len(b) for b in batches] == [10, 10, 3]
    assert [x for b in batches for x in b] == items


def test_partition_exact_multiple_has_no_short_final_batch():
    items = list(range(20))
    batches = cmr._partition(items, 10)
    assert [len(b) for b in batches] == [10, 10]


def test_partition_fewer_items_than_batch_size_is_one_batch():
    items = list(range(3))
    batches = cmr._partition(items, 10)
    assert batches == [[0, 1, 2]]


def test_partition_empty_items_yields_no_batches():
    assert cmr._partition([], 10) == []


# ── deterministic reduce ─────────────────────────────────────────────────────

def _all_hit_llm(**kw):
    purpose = kw["purpose"]
    idx = int(purpose.rsplit("_s", 1)[1])
    # Whatever ids this batch's own prompt was built for — recovered from the
    # rendered input the engine tags every item with, so the fake never has
    # to see the real item objects.
    import re
    ids = re.findall(r'<item id="([^"]+)">', kw["input"])
    verdicts = {i: {"hit": True, "reason": f"hit-{i}"} for i in ids}
    return SimpleNamespace(output={"verdicts": verdicts}, stop_reason="end_turn")


def test_deterministic_reduce_same_verdicts_same_count(monkeypatch):
    items = _items(7)
    spec = _spec(batch_size=3)
    eng1 = _run(spec, items, monkeypatch, _all_hit_llm)
    eng2 = _run(spec, items, monkeypatch, _all_hit_llm)
    assert eng1.count == eng2.count == 7 == len(eng1.hit_ids)
    assert eng1.hit_ids == eng2.hit_ids


def test_count_is_exactly_len_of_hit_ids_not_narrated(monkeypatch):
    def _partial_hit_llm(**kw):
        import re
        ids = re.findall(r'<item id="([^"]+)">', kw["input"])
        # Alternate hit/no-hit within each batch, deterministically.
        verdicts = {
            i: {"hit": (n % 2 == 0), "reason": "r" if n % 2 == 0 else ""}
            for n, i in enumerate(ids)
        }
        return SimpleNamespace(output={"verdicts": verdicts}, stop_reason="end_turn")

    items = _items(9)
    spec = _spec(batch_size=4)
    eng = _run(spec, items, monkeypatch, _partial_hit_llm)
    assert eng.count == len(eng.hit_ids)
    assert eng.count < eng.total_items  # a real partial result, not a trivial all-hit


# ── cross-batch id guard ─────────────────────────────────────────────────────

def test_cross_batch_id_guard_rejects_out_of_partition_ids(monkeypatch, caplog):
    # Batch 0's call claims a hit for "c5", which belongs to batch 1.
    def _bogus_llm(**kw):
        idx = int(kw["purpose"].rsplit("_s", 1)[1])
        if idx == 0:
            return SimpleNamespace(
                output={"verdicts": {
                    "c0": {"hit": True, "reason": "real"},
                    "c5": {"hit": True, "reason": "bogus, not batch 0's id"},
                }},
                stop_reason="end_turn",
            )
        return SimpleNamespace(
            output={"verdicts": {
                "c3": {"hit": False, "reason": ""},
                "c4": {"hit": False, "reason": ""},
                "c5": {"hit": True, "reason": "batch 1's own real verdict"},
            }},
            stop_reason="end_turn",
        )

    items = _items(6)  # c0..c5, batch_size=3 -> batch0=[c0,c1,c2], batch1=[c3,c4,c5]
    spec = _spec(batch_size=3)
    import logging
    with caplog.at_level(logging.WARNING, logger="app.corpus_mapreduce"):
        eng = _run(spec, items, monkeypatch, _bogus_llm)
    # c5's REAL verdict (from its own batch, batch 1) counts; the bogus
    # cross-batch claim from batch 0 does not duplicate it.
    assert eng.hit_ids.count("c5") == 1
    assert eng.reasons["c5"] == "batch 1's own real verdict"
    assert eng.count == 2  # c0 (batch 0, real) + c5 (batch 1, real)
    assert any("outside its own partition" in r.message for r in caplog.records)


def test_cross_batch_guard_does_not_fabricate_a_classification(monkeypatch):
    # A batch claims an out-of-partition id, and that id's OWN batch never
    # mentions it at all — it must surface as unclassified, not as counted.
    def _bogus_only_llm(**kw):
        idx = int(kw["purpose"].rsplit("_s", 1)[1])
        if idx == 0:
            return SimpleNamespace(
                output={"verdicts": {
                    "c0": {"hit": True, "reason": "real"},
                    "c3": {"hit": True, "reason": "bogus — not batch 0's id"},
                }},
                stop_reason="end_turn",
            )
        return SimpleNamespace(
            output={"verdicts": {"c2": {"hit": False, "reason": ""}}},
            stop_reason="end_turn",
        )  # batch 1 never returns a verdict for c3

    items = _items(4)  # batch_size=2 -> batch0=[c0,c1], batch1=[c2,c3]
    spec = _spec(batch_size=2)
    eng = _run(spec, items, monkeypatch, _bogus_only_llm)
    assert "c3" not in eng.hit_ids
    assert "c3" in eng.unclassified_ids


# ── unclassified accounting ──────────────────────────────────────────────────

def test_unclassified_surfaced_not_counted(monkeypatch):
    def _partial_llm(**kw):
        idx = int(kw["purpose"].rsplit("_s", 1)[1])
        if idx == 0:
            # Batch 0 omits c1 entirely.
            return SimpleNamespace(
                output={"verdicts": {"c0": {"hit": True, "reason": "yes"}}},
                stop_reason="end_turn",
            )
        return SimpleNamespace(
            output={"verdicts": {"c2": {"hit": False, "reason": ""}}},
            stop_reason="end_turn",
        )

    items = _items(3)
    spec = _spec(batch_size=2)  # batch0=[c0,c1], batch1=[c2]
    eng = _run(spec, items, monkeypatch, _partial_llm)
    assert eng.unclassified_ids == ["c1"]
    assert eng.count == 1
    assert "c1" not in eng.hit_ids


def test_a_batch_that_returns_no_usable_verdicts_leaves_its_whole_batch_unclassified(monkeypatch):
    def _broken_llm(**kw):
        return SimpleNamespace(output={"not_verdicts_at_all": True}, stop_reason="end_turn")

    items = _items(3)
    spec = _spec(batch_size=10)
    eng = _run(spec, items, monkeypatch, _broken_llm)
    assert eng.count == 0
    assert sorted(eng.unclassified_ids) == ["c0", "c1", "c2"]


def test_empty_corpus_short_circuits_with_no_llm_call(monkeypatch):
    calls = []
    monkeypatch.setattr(gateway_mod, "llm_call", lambda **kw: calls.append(kw))
    spec = _spec()
    eng = cmr.run(spec, enterprise_id="ent-A", question="how many",
                  window=SimpleNamespace(label="last 7 days"), items=[])
    assert eng == cmr.EngineResult(count=0, hit_ids=[], reasons={}, total_items=0,
                                   unclassified_ids=[])
    assert calls == []


# ── gateway telemetry routing ────────────────────────────────────────────────

def test_map_routes_through_gateway_llm_call_with_expected_kwargs(monkeypatch):
    captured: list[dict] = []

    def _capture(**kw):
        captured.append(kw)
        return SimpleNamespace(output={"verdicts": {}}, stop_reason="end_turn")

    items = _items(3)
    spec = _spec(domain="voc_calls", batch_size=10,
                 verdict_schema={"type": "object", "properties": {}})
    _run(spec, items, monkeypatch, _capture)

    assert len(captured) == 1
    kw = captured[0]
    assert kw["purpose"] == "voc_calls_map_s0"
    assert kw["model"] == llm_module.FAST_MODEL
    assert kw["json_schema"] == spec.verdict_schema
    assert kw["system"] == cmr._composed_system(spec, None)
    assert kw["system"] == f"{spec.base_discipline}\n\n{spec.criterion}"
    assert kw["enterprise_id"] == "ent-A"
    assert kw["agent"] == "qa"
    # Every item's id is tagged in the rendered input the model sees.
    for it in items:
        assert f'<item id="{it.id}">' in kw["input"]


def test_map_call_pins_temperature_zero_for_deterministic_verdicts(monkeypatch):
    """A per-item classification bar should not vary run-to-run on identical
    input — pin `temperature=0` on every map call (see corpus_mapreduce.py's
    `_map_batch`), never left to the model's default sampling."""
    captured: list[dict] = []

    def _capture(**kw):
        captured.append(kw)
        return SimpleNamespace(output={"verdicts": {}}, stop_reason="end_turn")

    items = _items(4)
    spec = _spec(batch_size=2)  # two batches -> two map calls
    _run(spec, items, monkeypatch, _capture)

    assert len(captured) == 2
    assert all(kw["temperature"] == 0 for kw in captured)


def test_map_never_calls_the_bare_call_json(monkeypatch):
    """Telemetry lands via `gateway.llm_call`, never the raw `app.llm.call_json`
    the spike used (which bypassed `llm_usage_events`)."""
    import app.llm as llm_mod

    def _boom(*a, **k):
        raise AssertionError("must not call app.llm.call_json directly")

    monkeypatch.setattr(llm_mod, "call_json", _boom)
    monkeypatch.setattr(gateway_mod, "llm_call",
                        lambda **kw: SimpleNamespace(output={"verdicts": {}},
                                                     stop_reason="end_turn"))
    items = _items(3)
    spec = _spec()
    _run(spec, items, monkeypatch, gateway_mod.llm_call)


# ── ANALYZING phase ──────────────────────────────────────────────────────────

def test_emits_analyzing_phase_before_dispatching_the_map(monkeypatch):
    phases: list[str] = []
    items = _items(2)
    spec = _spec()
    _run(spec, items, monkeypatch, _all_hit_llm, on_phase=phases.append)
    assert phases == ["Analyzing the findings…"]


def test_no_phase_sink_is_a_silent_no_op(monkeypatch):
    items = _items(2)
    spec = _spec()
    eng = _run(spec, items, monkeypatch, _all_hit_llm, on_phase=None)
    assert eng.count == 2  # ran to completion without a sink


def test_phase_label_is_spec_supplied_never_a_reportphase_value(monkeypatch):
    """A domain names its own progress phrase — the engine never falls back
    to `app.report_phases.ReportPhase`. This is the mechanism
    `app.chat_intent._is_report_pipeline`'s carve-out depends on: a count
    engine's progress must never carry the raw label the frontend's
    classify-time envelope keys on to open the Reports drawer."""
    phases: list[str] = []
    items = _items(2)
    spec = _spec(phase_label="Analyzing your widgets…")
    _run(spec, items, monkeypatch, _all_hit_llm, on_phase=phases.append)
    assert phases == ["Analyzing your widgets…"]
    # Never the shared report vocabulary this run's spec did not opt into.
    assert "Analyzing the findings…" not in phases


def test_run_never_imports_reportphase():
    """Structural guard, not just a phase-string assertion: the engine module
    itself must carry no dependency on the report vocabulary at all — a
    domain-agnostic count/classification engine has no business knowing what
    a report is."""
    import app.corpus_mapreduce as mod

    assert not hasattr(mod, "ReportPhase")
    assert not hasattr(mod, "emit_report_phase")


# ── render_label / EngineResult.labels ───────────────────────────────────────

def test_render_label_is_honored_on_hit_labels(monkeypatch):
    """The shared engine renders each hit via `spec.render_label(item)` — a
    synthetic spec's custom label appears verbatim on `EngineResult.labels`,
    never the raw `item_id`."""
    items = _items(2)
    spec = _spec(render_label=lambda it: f"friendly-{it.id}")
    eng = _run(spec, items, monkeypatch, _all_hit_llm)
    assert eng.count == 2
    assert eng.labels == {"c0": "friendly-c0", "c1": "friendly-c1"}
    # Never the raw id standing in for the label.
    assert "c0" not in eng.labels.values()


def test_render_label_is_only_resolved_for_hits(monkeypatch):
    """`labels` is populated for HITS only — the domain-agnostic assembly
    never needs a label for an item that did not match, and a spec whose
    `render_label` would raise on a non-hit item (e.g. it assumes a field
    only present on a qualifying item) must never see one."""
    def _first_hit_only(**kw):
        import re
        ids = re.findall(r'<item id="([^"]+)">', kw["input"])
        verdicts = {i: {"hit": i == "c0", "reason": "r" if i == "c0" else ""}
                    for i in ids}
        return SimpleNamespace(output={"verdicts": verdicts}, stop_reason="end_turn")

    def _label_or_raise(it):
        if it.id != "c0":
            raise AssertionError("render_label must not run on a non-hit item")
        return "friendly-c0"

    items = _items(2)
    spec = _spec(render_label=_label_or_raise)
    eng = _run(spec, items, monkeypatch, _first_hit_only)
    assert eng.hit_ids == ["c0"]
    assert eng.labels == {"c0": "friendly-c0"}


# ── local fan-out cap ────────────────────────────────────────────────────────

def test_local_fanout_cap_is_gate_capacity_minus_one(monkeypatch):
    monkeypatch.setattr(llm_module._llm_gate, "_capacity", 6)
    assert cmr._local_fanout_cap() == 5
    monkeypatch.setattr(llm_module._llm_gate, "_capacity", 1)
    assert cmr._local_fanout_cap() == 1  # never below 1


def test_local_fanout_cap_bounds_concurrent_map_calls(monkeypatch):
    monkeypatch.setattr(llm_module._llm_gate, "_capacity", 6)  # local cap = 5
    active = 0
    max_active = 0
    lock = threading.Lock()

    def _slow_llm(**kw):
        nonlocal active, max_active
        with lock:
            active += 1
            max_active = max(max_active, active)
        time.sleep(0.05)
        with lock:
            active -= 1
        return SimpleNamespace(output={"verdicts": {}}, stop_reason="end_turn")

    items = _items(70)  # batch_size=10 -> 7 batches, more than the cap of 5
    spec = _spec(batch_size=10)
    _run(spec, items, monkeypatch, _slow_llm)
    assert max_active <= 5
    assert max_active == 5  # real concurrency happened, bounded exactly at the cap


# ── map_model ─────────────────────────────────────────────────────────────

def test_map_model_default_is_fast_model_when_spec_carries_no_override(monkeypatch):
    captured: list[dict] = []

    def _capture(**kw):
        captured.append(kw)
        return SimpleNamespace(output={"verdicts": {}}, stop_reason="end_turn")

    items = _items(2)
    spec = _spec()  # no map_model override
    assert spec.map_model is None
    _run(spec, items, monkeypatch, _capture)
    assert captured[0]["model"] == llm_module.FAST_MODEL


def test_map_model_override_is_honoured_over_the_default(monkeypatch):
    captured: list[dict] = []

    def _capture(**kw):
        captured.append(kw)
        return SimpleNamespace(output={"verdicts": {}}, stop_reason="end_turn")

    items = _items(2)
    spec = _spec(map_model="claude-sonnet-4-6")
    _run(spec, items, monkeypatch, _capture)
    assert captured[0]["model"] == "claude-sonnet-4-6"
    assert captured[0]["model"] != llm_module.FAST_MODEL


# ── criterion composition (base_discipline + criterion) ─────────────────────

def test_composed_system_is_base_discipline_then_default_criterion_when_absent():
    spec = _spec(base_discipline="BASE", criterion="DEFAULT-CRIT")
    assert cmr._composed_system(spec, None) == "BASE\n\nDEFAULT-CRIT"
    assert cmr._composed_system(spec, {}) == "BASE\n\nDEFAULT-CRIT"


def test_supplied_criterion_replaces_the_default_in_the_composed_system():
    spec = _spec(base_discipline="BASE", criterion="DEFAULT-CRIT")
    composed = cmr._composed_system(spec, {"criterion": "CUSTOM BAR"})
    assert composed == "BASE\n\nCUSTOM BAR"
    assert "DEFAULT-CRIT" not in composed


def test_base_discipline_guards_present_in_both_default_and_overridden_cases():
    spec = _spec(base_discipline="BASE-GUARD", criterion="DEFAULT-CRIT")
    default_composed = cmr._composed_system(spec, None)
    overridden_composed = cmr._composed_system(spec, {"criterion": "CUSTOM BAR"})
    assert "BASE-GUARD" in default_composed
    assert "BASE-GUARD" in overridden_composed


def test_blank_or_non_string_criterion_falls_back_to_the_default():
    spec = _spec(base_discipline="BASE", criterion="DEFAULT-CRIT")
    for bad in ("", "   ", None, 5, [], {}):
        assert cmr._composed_system(spec, {"criterion": bad}) == "BASE\n\nDEFAULT-CRIT"


def test_map_call_uses_the_composed_system_including_a_supplied_criterion(monkeypatch):
    captured: list[dict] = []

    def _capture(**kw):
        captured.append(kw)
        return SimpleNamespace(output={"verdicts": {}}, stop_reason="end_turn")

    items = _items(2)
    spec = _spec(base_discipline="BASE-GUARD", criterion="DEFAULT-CRIT")
    _run(spec, items, monkeypatch, _capture, constraints={"criterion": "CUSTOM BAR"})
    assert captured[0]["system"] == "BASE-GUARD\n\nCUSTOM BAR"
    assert "DEFAULT-CRIT" not in captured[0]["system"]


# ── generic prefilter hook — domain-agnostic on the engine's side ───────────
# Proves the engine's `CorpusMapReduceSpec.prefilter` is honoured with NO
# domain knowledge baked into `corpus_mapreduce.py` itself — a synthetic spec
# supplies its own filter/wrap logic and the engine applies it uniformly.

def test_prefilter_defaults_to_none_and_is_a_strict_no_op(monkeypatch):
    items = _items(4)
    spec = _spec(batch_size=2)
    assert spec.prefilter is None
    eng = _run(spec, items, monkeypatch, _all_hit_llm)
    assert eng.count == 4
    assert eng.total_items == 4


def test_prefilter_narrows_the_classification_pool_but_total_items_stays_the_full_fetch(monkeypatch):
    items = _items(4)  # c0..c3
    seen_ids: set[str] = set()

    def _capture_llm(**kw):
        import re
        ids = re.findall(r'<item id="([^"]+)">', kw["input"])
        seen_ids.update(ids)
        return SimpleNamespace(
            output={"verdicts": {i: {"hit": True, "reason": "r"} for i in ids}},
            stop_reason="end_turn",
        )

    spec = _spec(batch_size=10, prefilter=lambda its, ent: [it for it in its if it.id != "c1"])
    eng = _run(spec, items, monkeypatch, _capture_llm)
    assert "c1" not in seen_ids            # never sent to the model
    assert "c1" not in eng.hit_ids
    assert eng.total_items == 4            # the FULL fetched count, not the narrowed pool of 3
    assert eng.count == 3


def test_prefilter_receives_the_full_fetched_items_and_the_run_enterprise_id(monkeypatch):
    captured: dict = {}

    def _prefilter(its, enterprise_id):
        captured["items"] = its
        captured["enterprise_id"] = enterprise_id
        return its

    items = _items(2)
    spec = _spec(prefilter=_prefilter)
    _run(spec, items, monkeypatch, _all_hit_llm)
    assert captured["enterprise_id"] == "ent-A"
    assert captured["items"] == items


def test_prefilter_dropped_items_are_excluded_not_surfaced_as_unclassified(monkeypatch):
    """An item the prefilter drops was never owed a verdict — it must not
    land in `unclassified_ids` (that field means the model was shown an id
    and never returned a verdict for it), unlike a genuinely unclassified
    surviving item."""
    def _broken_llm(**kw):
        return SimpleNamespace(output={"not_verdicts_at_all": True}, stop_reason="end_turn")

    items = _items(3)  # c0, c1, c2
    spec = _spec(batch_size=10, prefilter=lambda its, ent: [it for it in its if it.id != "c1"])
    eng = _run(spec, items, monkeypatch, _broken_llm)
    assert "c1" not in eng.unclassified_ids
    assert sorted(eng.unclassified_ids) == ["c0", "c2"]
    assert eng.total_items == 3


def test_prefilter_returning_an_empty_pool_short_circuits_with_no_llm_call(monkeypatch):
    calls_made: list[dict] = []
    monkeypatch.setattr(gateway_mod, "llm_call", lambda **kw: calls_made.append(kw))
    items = _items(3)
    spec = _spec(prefilter=lambda its, ent: [])
    eng = cmr.run(spec, enterprise_id="ent-A", question="how many",
                  window=SimpleNamespace(label="last 7 days"), items=items)
    assert eng.count == 0
    assert eng.total_items == 3
    assert eng.unclassified_ids == []
    assert calls_made == []


def test_prefilter_may_return_a_domain_defined_wrapper_type(monkeypatch):
    """The engine does not care WHAT a prefilter returns, only that
    `item_id`/`render_item`/`render_label` still work on it — proves the
    hook supports annotation/wrapping, not just filtering, with zero
    engine-side knowledge of the wrapper's shape."""
    @dataclass
    class _Wrapped:
        inner: _Item
        tag: str

    items = _items(2)

    def _wrap(its, ent):
        return [_Wrapped(inner=it, tag=f"tag-{it.id}") for it in its]

    rendered_tags: list[str] = []

    def _render(w):
        rendered_tags.append(w.tag)
        return w.inner.text

    spec = _spec(
        prefilter=_wrap,
        item_id=lambda w: w.inner.id,
        render_item=_render,
        render_label=lambda w: f"label-{w.inner.id}",
    )
    eng = _run(spec, items, monkeypatch, _all_hit_llm)
    assert eng.count == 2
    assert set(rendered_tags) == {"tag-c0", "tag-c1"}
    assert eng.labels == {"c0": "label-c0", "c1": "label-c1"}


def test_corpus_mapreduce_module_carries_no_voc_or_email_domain_logic():
    """Reusability guard: the shared engine must stay domain-agnostic — ALL
    email-domain / call_index / voc LOGIC lives in call_digest.py's
    VOC_CALLS_SPEC (`_voc_count_prefilter`), never here. Checks actual code
    dependencies (imports, calls to the reused primitives), not prose —
    the module docstring legitimately NAMES "voc_calls" once as an
    illustrative example domain (`domain="voc_calls"` -> purpose
    "voc_calls_map_s0"), which is documentation, not a dependency."""
    import inspect

    source = inspect.getsource(cmr).lower()
    for banned in ("import app.call_index", "from app.call_index",
                  "_own_domains(", "derive_account(", "@gmail", "email domain"):
        assert banned not in source, f"corpus_mapreduce.py must not contain {banned!r}"
