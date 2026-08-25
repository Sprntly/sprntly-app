"""Query-time map-reduce-over-corpus — the parallelizable count/classification
subclass of corpus questions ("how many / which <items> that <content-filter>").

Domain-agnostic from day one: a caller supplies a `CorpusMapReduceSpec`
(fetch/render/id + a rubric + a verdict schema) and `run()` does the rest —
partition into batches, classify each batch CONCURRENTLY against the rubric
with a fast model, then reduce deterministically IN PYTHON. The reduce is the
structural point of this module: `count = len(hit_ids)`, never a number an LLM
narrated in prose, so it cannot disagree with its own enumerated evidence the
way a single big synthesis call can ("said 30, listed 27").

Every map call is telemetered (routed through `app.graph.gateway.llm_call`,
never the bare `app.llm.call_json`) and respects the process-wide `_llm_gate`
via `app.graph.gateway.llm_call` -> `app.llm.call_json` -> the gate's
`acquire`/`release` around the actual Anthropic call. On top of that shared
gate this module adds its OWN local fan-out cap (a threading semaphore sized
to the live gate capacity minus one) so a single big count question can never
occupy every shared slot in the process — every other in-flight interactive
call always keeps at least one slot to run on.

`fetch`/`render_item`/`item_id`/`base_discipline`/`criterion`/`verdict_schema`
are the only domain-specific pieces of a `CorpusMapReduceSpec`. Everything
else — batching, concurrency, the id-tagging each item gets in its rendered
block, the cross-batch guard, the reduce, and the unclassified accounting —
lives here once. A `verdict_schema` MUST shape its structured response as
`{"verdicts": {<item_id>: {"hit": bool, "reason": str, ...}}}` — the engine
reads exactly `hit` and `reason` off each entry; a domain schema may carry
additional fields for its own use (e.g. a theme tag) without the engine caring.

The per-item map system is `base_discipline + criterion`, composed fresh for
every `run()`: `base_discipline` is the domain's structural guards that ALWAYS
apply and are NEVER caller-overridable (e.g. "only a real external
customer/prospect counts"); `criterion` is the classification bar — the
definitional, per-query part — and a caller's `constraints["criterion"]`
REPLACES the spec's own default criterion for that one call when supplied
(see `_resolve_criterion`). This keeps "what always guards the count" and
"what the count is actually counting" as two separately-owned pieces, so a
caller can redefine the second without ever being able to relax the first.
"""
from __future__ import annotations

import asyncio
import logging
import threading
from dataclasses import dataclass, field
from typing import Any, Callable, Optional

from app.report_phases import ReportPhase, emit_report_phase

logger = logging.getLogger(__name__)

#: Default items per map call. Tuned from the concurrency spike (voc-baseline
#: rig, 83-call corpus): batch_size=10 was negligibly slower than 5 but
#: meaningfully cheaper (fewer repeated rubric copies) and half the concurrent
#: request fan-out to manage. A domain may override per `CorpusMapReduceSpec`.
_DEFAULT_BATCH_SIZE = 10

#: Output ceiling for one map call. A batch's classification response is a
#: short per-item verdict list, never prose — this is generous headroom for
#: `_DEFAULT_BATCH_SIZE` items each carrying a one-line reason.
_MAP_MAX_TOKENS = 2000


@dataclass
class CorpusMapReduceSpec:
    """One domain's plug-in into the shared engine. See module docstring for
    the `verdict_schema` response-shape contract every domain must honour."""

    #: Short, log-safe domain label (used in the map call's `purpose`, e.g.
    #: "voc_calls" -> purpose "voc_calls_map_s0").
    domain: str
    #: `(enterprise_id, window, constraints) -> list[Item]`. Only consulted
    #: when the caller does not already have the items in hand (see `items`
    #: on `run()`, which most callers use to reuse an already-assembled
    #: corpus for free).
    fetch: Callable[[str, Any, Optional[dict]], list]
    #: `(item) -> str` — renders ONE item's content for the model. Never
    #: include the item's id in this text; the engine tags every item with
    #: its id itself (see `_render_batch`), so every domain gets identical,
    #: unambiguous id-tagging without having to implement it.
    render_item: Callable[[Any], str]
    #: `(item) -> str` — the item's stable identifier (must be unique within
    #: the fetched corpus).
    item_id: Callable[[Any], str]
    #: The structural guards that ALWAYS apply for this domain, regardless of
    #: any caller-supplied criterion — e.g. scope/external-participant/
    #: actively-raised discipline. Composed as the FIRST half of every map
    #: call's system prompt (see module docstring); never replaced by
    #: `constraints["criterion"]`, only `criterion` below is.
    base_discipline: str
    #: The domain's SENSIBLE DEFAULT classification bar — what counts as a
    #: "hit" once `base_discipline` already holds. A caller's
    #: `constraints["criterion"]` (see `run()`) REPLACES this text for one
    #: query; when absent, this default is used and `base_discipline` is
    #: unaffected either way.
    criterion: str
    #: The structured-output schema for one map call. See the module
    #: docstring's response-shape contract.
    verdict_schema: dict
    batch_size: int = _DEFAULT_BATCH_SIZE
    #: The map call's model. `None` (default) resolves to `app.llm.FAST_MODEL`
    #: at call time — cheap domains keep the default; a domain needing
    #: stronger comprehension (see `call_digest.VOC_CALLS_SPEC`, which sets
    #: this to the same Sonnet constant the answer/report synthesis calls
    #: use) overrides explicitly.
    map_model: Optional[str] = None


@dataclass
class EngineResult:
    count: int
    hit_ids: list[str]
    reasons: dict[str, str]
    total_items: int
    unclassified_ids: list[str] = field(default_factory=list)


def _partition(items: list, batch_size: int) -> list[list]:
    """Split `items` into consecutive batches of at most `batch_size`."""
    if batch_size <= 0:
        batch_size = _DEFAULT_BATCH_SIZE
    return [items[i : i + batch_size] for i in range(0, len(items), batch_size)]


def _local_fanout_cap() -> int:
    """The engine's own fan-out ceiling: the LIVE process-wide `_llm_gate`
    capacity minus one, so one big count question can never occupy every
    shared slot — at least one slot always stays free for every other
    in-flight interactive call. Read fresh off the module (not cached at
    import) so a test's monkeypatch of the gate's capacity is honoured, and
    so a runtime capacity change (env-driven, resolved once at process start)
    is reflected without this module needing its own copy of the value.

    Never raises the gate itself (AD: do not raise the global concurrency
    lever from a single caller) — this only bounds how many of the EXISTING
    shared slots this one engine run may hold at once.
    """
    from app import llm as llm_module

    capacity = getattr(llm_module._llm_gate, "_capacity", 6)
    return max(1, capacity - 1)


def _render_batch(spec: CorpusMapReduceSpec, batch: list) -> str:
    """Render one batch's items, each wrapped in an explicit id tag — the
    ENGINE's job, not the domain's `render_item`, so every domain's items are
    unambiguously addressable by the exact id the reduce step expects back,
    with zero extra work required of the domain spec."""
    return "\n\n".join(
        f'<item id="{spec.item_id(it)}">\n{spec.render_item(it)}\n</item>'
        for it in batch
    )


def _resolve_criterion(spec: CorpusMapReduceSpec, constraints: Optional[dict]) -> str:
    """A caller-supplied `constraints["criterion"]` REPLACES the spec's own
    default `criterion` for this one query; absent (or not a non-empty
    string) falls back to the spec's default. Only this half is ever
    caller-overridable — `spec.base_discipline`'s structural guards are
    composed in unconditionally by `_composed_system`, in BOTH cases."""
    if isinstance(constraints, dict):
        raw = constraints.get("criterion")
        if isinstance(raw, str) and raw.strip():
            return raw.strip()
    return spec.criterion


def _composed_system(spec: CorpusMapReduceSpec, constraints: Optional[dict]) -> str:
    """The per-item map system: `spec.base_discipline` (always applied) +
    the resolved criterion (the spec's own default, or a caller-supplied
    `constraints["criterion"]` when one is present). See module docstring."""
    return f"{spec.base_discipline}\n\n{_resolve_criterion(spec, constraints)}"


def _map_batch(
    spec: CorpusMapReduceSpec,
    *,
    idx: int,
    batch: list,
    enterprise_id: str,
    sem: threading.Semaphore,
    system: str,
    model: str,
) -> dict:
    """One batch's classification call. Runs on a worker thread (dispatched
    via `asyncio.to_thread` by `run()`) — `sem.acquire()` blocks that thread,
    never an event loop, the same pattern `app.llm._llm_gate` itself relies
    on. Telemetered via `app.graph.gateway.llm_call` (NOT the bare
    `app.llm.call_json`) so usage lands in `llm_usage_events` like every other
    interactive call.

    `system` and `model` are resolved ONCE by `run()` (the composed
    base_discipline+criterion, and the spec's `map_model` or the default fast
    model) and passed down identically to every batch of the same run — never
    recomputed per batch."""
    from app.graph.gateway import llm_call

    ids_in_batch = ", ".join(spec.item_id(it) for it in batch)
    user = (
        f"Classify EVERY item below by its id. Return one verdict per id "
        f"shown — never omit one, never invent an id that is not shown. "
        f"Ids in this batch: {ids_in_batch}\n\n{_render_batch(spec, batch)}"
    )
    sem.acquire()
    try:
        result = llm_call(
            enterprise_id=enterprise_id,
            agent="qa",
            purpose=f"{spec.domain}_map_s{idx}",
            system=system,
            input=user,
            prompt_version=f"corpus-mapreduce-{spec.domain}-v1",
            model=model,
            json_schema=spec.verdict_schema,
            max_tokens=_MAP_MAX_TOKENS,
            # Pinned deterministic: an unpinned map call showed run-to-run
            # verdict churn on an identical corpus (same items, same rubric,
            # different hit rosters) — a per-item classification bar should
            # not vary by sampling noise. See existing precedent for the same
            # pin at other per-item classification call sites (e.g.
            # app.design_agent.codebase_map.locate, app.stories.generate).
            temperature=0,
        )
    finally:
        sem.release()
    return result.output if isinstance(result.output, dict) else {}


def run(
    spec: CorpusMapReduceSpec,
    *,
    enterprise_id: str,
    question: str,
    window: Any,
    constraints: Optional[dict] = None,
    on_phase: Optional[Callable[[str], None]] = None,
    items: Optional[list] = None,
) -> EngineResult:
    """Fetch (or reuse `items`) -> partition -> concurrent map -> deterministic
    Python reduce. See module docstring for the four-stage shape and the
    telemetry/concurrency-control contract.

    `question` is accepted (not currently read) so a future domain's `fetch`
    or rubric selection can be question-aware without changing this
    signature — kept symmetric with the spec's documented entry point.
    """
    del question  # not read yet; kept for signature symmetry, see docstring

    fetched = list(items) if items is not None else spec.fetch(
        enterprise_id, window, constraints
    )
    total_items = len(fetched)
    if total_items == 0:
        return EngineResult(count=0, hit_ids=[], reasons={}, total_items=0,
                            unclassified_ids=[])

    batches = _partition(fetched, spec.batch_size)
    ids_by_batch = [
        {spec.item_id(it) for it in batch} for batch in batches
    ]

    emit_report_phase(on_phase, ReportPhase.ANALYZING)

    # Resolved ONCE per run, identical across every batch: the composed
    # system (base_discipline + the resolved criterion — the spec's default,
    # or a caller-supplied constraints["criterion"]) and the map model (the
    # spec's own override, or app.llm.FAST_MODEL).
    from app import llm as llm_module

    system = _composed_system(spec, constraints)
    map_model = spec.map_model or llm_module.FAST_MODEL

    cap = min(_local_fanout_cap(), len(batches)) or 1
    sem = threading.Semaphore(cap)

    async def gather_batches() -> list[dict]:
        return await asyncio.gather(*[
            asyncio.to_thread(
                _map_batch, spec, idx=idx, batch=batch,
                enterprise_id=enterprise_id, sem=sem,
                system=system, model=map_model,
            )
            for idx, batch in enumerate(batches)
        ])

    outputs = asyncio.run(gather_batches())

    hit_ids: list[str] = []
    reasons: dict[str, str] = {}
    classified_ids: set[str] = set()
    for idx, output in enumerate(outputs):
        allowed = ids_by_batch[idx]
        verdicts = output.get("verdicts") if isinstance(output, dict) else None
        if not isinstance(verdicts, dict):
            logger.warning(
                "corpus_mapreduce: batch %d (domain=%s) returned no usable "
                "verdicts — its %d item(s) are unclassified",
                idx, spec.domain, len(allowed),
            )
            continue
        for item_id, verdict in verdicts.items():
            if item_id not in allowed:
                # Cross-batch guard: a batch may only speak for its OWN
                # partition. An id claimed outside it is dropped — never
                # counted as a hit, and it does NOT clear that id's real
                # unclassified status (its own batch still owes it a verdict).
                logger.warning(
                    "corpus_mapreduce: batch %d (domain=%s) returned an id "
                    "outside its own partition (%s) — dropped, not counted",
                    idx, spec.domain, item_id,
                )
                continue
            if not isinstance(verdict, dict):
                continue
            classified_ids.add(item_id)
            if verdict.get("hit"):
                hit_ids.append(item_id)
                reasons[item_id] = str(verdict.get("reason") or "")

    all_ids = [spec.item_id(it) for it in fetched]
    unclassified_ids = [i for i in all_ids if i not in classified_ids]

    return EngineResult(
        count=len(hit_ids),
        hit_ids=hit_ids,
        reasons=reasons,
        total_items=total_items,
        unclassified_ids=unclassified_ids,
    )
