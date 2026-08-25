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
        rubric_system="RUBRIC",
        verdict_schema={"type": "object"},
        batch_size=10,
    )
    kw.update(overrides)
    return cmr.CorpusMapReduceSpec(**kw)


def _run(spec, items, monkeypatch, fake_llm, on_phase=None):
    monkeypatch.setattr(gateway_mod, "llm_call", fake_llm)
    return cmr.run(
        spec, enterprise_id="ent-A", question="how many calls raised X",
        window=SimpleNamespace(label="last 7 days"), constraints=None,
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
    assert kw["system"] == spec.rubric_system
    assert kw["enterprise_id"] == "ent-A"
    assert kw["agent"] == "qa"
    # Every item's id is tagged in the rendered input the model sees.
    for it in items:
        assert f'<item id="{it.id}">' in kw["input"]


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
