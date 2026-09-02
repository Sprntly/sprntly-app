"""The gate selects; it does not rank, rescore, or delete.

Apurva ruled for a goal-relevance gate after a real run for "grow revenue by
5%" put three descriptions of the company's OWN product in its top five — the
list was ordered by how many accounts mentioned a theme, and what gets mentioned
most on a sales call is the vendor's own demo.

I2's letter says no LLM returns a score, a rank or a decision. This IS a
decision, taken deliberately. Everything I2 was protecting is asserted below.
"""
from __future__ import annotations

from app.crucible.relevance import Verdict, partition
from datetime import datetime, timezone

from app.crucible.types import ConfidenceInputs, Finding, ImpactInputs

NOW = datetime(2026, 3, 1, tzinfo=timezone.utc)


def _f(fid: str, *, adjudication: str = "corroborated") -> Finding:
    return Finding(
        id=fid, statement=f"statement {fid}", claim_ids=(f"c-{fid}",),
        impact_inputs=ImpactInputs(
            currency="accounts", affected_population=None,
            movable_gap=None, value_per_unit=None,
        ),
        confidence_inputs=ConfidenceInputs(
            strengths=("reported",), claim_types=("mechanism",),
            observed_ats=(NOW,), authoritative_count=1, claim_count=1,
            independent_authoritative_source_types=1,
        ),
        adjudication=adjudication, label=f"theme {fid}",
    )


def test_the_gate_never_reorders_what_it_keeps():
    """THE INVARIANT. The order is the frozen rank; a gate that reordered would
    be making the decision the scorers own."""
    findings = [_f("a"), _f("b"), _f("c"), _f("d")]
    verdicts = {"b": Verdict(False, "product capability, not a problem")}
    kept, aside = partition(findings, verdicts)

    assert [f.id for f in kept] == ["a", "c", "d"]
    assert [f.id for f, _ in aside] == ["b"]


def test_nothing_is_deleted_only_moved():
    """A set-aside finding leaves with its reason attached, for the appendix."""
    findings = [_f("a"), _f("b")]
    verdicts = {"a": Verdict(False, "describes our own product")}
    kept, aside = partition(findings, verdicts)

    assert len(kept) + len(aside) == len(findings)
    assert aside[0][1] == "describes our own product"


def test_an_unjudged_finding_is_kept():
    """No verdict is not a negative verdict. A model that skipped a row, or a
    chunk whose call failed, must not cost the reader a finding."""
    kept, aside = partition([_f("a"), _f("b")], {})
    assert [f.id for f in kept] == ["a", "b"]
    assert aside == []


def test_a_failed_gate_keeps_everything():
    """FAILING OPEN IS THE SAFETY PROPERTY. A relevance pass that died and
    quietly hid three hundred findings would make a thin report look decisive —
    the worst outcome available here."""
    findings = [_f(str(i)) for i in range(50)]
    kept, aside = partition(findings, {})   # {} is what a total failure returns
    assert len(kept) == 50
    assert aside == []


def test_an_authoritative_conflict_is_never_set_aside():
    """Two sources that may both speak disagreeing is the most
    decision-relevant thing a run can find — `_rank` puts it first regardless of
    size, and no relevance judgement outranks that."""
    findings = [_f("a", adjudication="conflict"), _f("b")]
    verdicts = {
        "a": Verdict(False, "the model thought this was off-topic"),
        "b": Verdict(False, "genuinely off-topic"),
    }
    kept, aside = partition(findings, verdicts)

    assert [f.id for f in kept] == ["a"]
    assert [f.id for f, _ in aside] == ["b"]


def test_a_false_verdict_with_no_reason_is_not_a_verdict():
    """The reason is what the reader sees in the appendix. A `false` with
    nothing beside it sets a finding aside and cannot say why, which is the
    silent filtering this whole design exists to avoid. Verdicts are keyed by
    `idx` — the theme's 1-based position in the numbered prompt — not by the
    real finding id (the id never has to leave `_input`/`_judge_chunk`,
    which is half the output-token saving)."""
    from app.crucible.relevance import _judge_chunk
    import app.graph.gateway as gw

    class _R:
        output = {"verdicts": [
            {"idx": 1, "bears_on_goal": False, "reason": ""},
            {"idx": 2, "bears_on_goal": False, "reason": "off-topic"},
        ]}

    real = gw.llm_call
    try:
        gw.llm_call = lambda **kw: _R()
        out = _judge_chunk(enterprise_id="e", goal_text="g",
                           definition_text="d", findings=[_f("a"), _f("b")])
    finally:
        gw.llm_call = real
    assert "a" not in out
    assert out["b"].bears_on_goal is False


def test_a_true_verdict_with_no_reason_is_still_a_verdict():
    """The biggest real lever on the gate's wall clock: a `true` verdict is never rendered
    anywhere, so it needs no reason at all — only a `false` one does. A gate
    that dropped empty-reason `true` verdicts would defeat the whole point of
    telling the model not to bother writing one."""
    from app.crucible.relevance import _judge_chunk
    import app.graph.gateway as gw

    class _R:
        output = {"verdicts": [
            {"idx": 1, "bears_on_goal": True, "reason": ""},
        ]}

    real = gw.llm_call
    try:
        gw.llm_call = lambda **kw: _R()
        out = _judge_chunk(enterprise_id="e", goal_text="g",
                           definition_text="d", findings=[_f("a")])
    finally:
        gw.llm_call = real
    assert out["a"] == Verdict(True, "")


def test_a_verdict_for_an_unknown_index_is_discarded():
    """An out-of-range or hallucinated `idx` must not set aside a finding
    that was never judged."""
    from app.crucible.relevance import _judge_chunk
    import app.graph.gateway as gw

    class _R:
        output = {"verdicts": [
            {"idx": 99, "bears_on_goal": False, "reason": "x"},
        ]}

    real = gw.llm_call
    try:
        gw.llm_call = lambda **kw: _R()
        out = _judge_chunk(enterprise_id="e", goal_text="g",
                           definition_text="d", findings=[_f("a")])
    finally:
        gw.llm_call = real
    assert out == {}


def test_the_prompt_numbers_themes_and_never_leaks_the_real_id():
    """The id never leaves this function — the input-side half of the ordinal-index saving —
    sent nowhere in the prompt, so the model cannot echo it back and the
    numbered list is all it has to work from."""
    from app.crucible.relevance import _input

    findings = [_f("a-long-opaque-id-1"), _f("a-long-opaque-id-2")]
    text = _input("grow revenue", "definition", findings)
    assert "1. theme a-long-opaque-id-1" in text
    assert "2. theme a-long-opaque-id-2" in text
    assert "a-long-opaque-id-1" not in text.replace(
        "1. theme a-long-opaque-id-1", ""
    )
    assert "finding_id" not in text


def test_the_chunk_calls_the_fast_model():
    """`judge_goal_relevance` is exactly the shape `FAST_MODEL`'s own
    charter names — high-volume, closed-set, short-output — not the
    reasoning-depth default."""
    from app.crucible.relevance import _judge_chunk
    from app.llm import FAST_MODEL
    import app.graph.gateway as gw

    seen = {}

    def spy(**kw):
        seen.update(kw)

        class _R:
            output = {"verdicts": []}
        return _R()

    real = gw.llm_call
    try:
        gw.llm_call = spy
        _judge_chunk(enterprise_id="e", goal_text="g", definition_text="d",
                     findings=[_f("a")])
    finally:
        gw.llm_call = real
    assert seen.get("model") == FAST_MODEL


def test_one_bad_chunk_does_not_lose_the_others():
    """A 300-finding run is several calls. One failing must cost only its own
    chunk's verdicts, and those findings are then kept.

    Forced to `MAX_PARALLEL = 1` so the two chunks run in a known order —
    with real concurrency, which of the two chunks happens to make the FIRST
    call (and so hits the injected failure) is a race this test does not need
    to resolve; it only needs to prove one bad chunk cannot cost the other
    one's verdicts, which is exactly as true one-at-a-time.
    """
    import app.crucible.relevance as mod
    import app.graph.gateway as gw

    calls = {"n": 0}

    def flaky(**kw):
        calls["n"] += 1
        if calls["n"] == 1:
            raise RuntimeError("gateway hiccup")

        class _R:
            # `idx=1` — "later" is the sole finding in its own chunk, so it is
            # always the first (and only) numbered theme in THAT call.
            output = {"verdicts": [
                {"idx": 1, "bears_on_goal": False, "reason": "off-topic"},
            ]}
        return _R()

    findings = [_f(f"f{i}") for i in range(mod.CHUNK)] + [_f("later")]
    real, off, parallel = gw.llm_call, mod._offline, mod.MAX_PARALLEL
    try:
        gw.llm_call = flaky
        mod._offline = lambda: False
        mod.MAX_PARALLEL = 1
        out = mod.judge_relevance(enterprise_id="e", goal_text="g",
                                  definition_text="d", findings=findings)
    finally:
        gw.llm_call, mod._offline, mod.MAX_PARALLEL = real, off, parallel

    assert calls["n"] == 2
    # The failed chunk contributed nothing, so all of its findings are kept.
    kept, aside = partition(findings, out)
    assert [f.id for f, _ in aside] == ["later"]
    assert len(kept) == mod.CHUNK


def test_the_gate_stops_asking_at_its_deadline():
    """LATENCY IS A FAILURE MODE, AND ONLY ERRORS WERE GUARDED.

    Every call was wrapped in a try, which was called failing open. It is not:
    a call that never returns raises nothing. On staging a 149-finding run sat
    thirteen minutes past its last narration line with its findings computed,
    unsaved and invisible — no error, because there was no error.

    Past the deadline the gate stops asking and everything unjudged is kept,
    the same direction every other failure here resolves in. The deadline is
    checked before each WAVE of up to `MAX_PARALLEL` chunks, not before each
    individual chunk — a wave already in flight always finishes.
    """
    import app.crucible.relevance as mod
    import app.graph.gateway as gw

    calls = {"n": 0}
    clock = {"t": 0.0}

    def slow(**kw):
        calls["n"] += 1
        clock["t"] += mod.DEADLINE_SECONDS  # each call burns the whole budget

        class _R:
            output = {"verdicts": []}
        return _R()

    findings = [_f(f"f{i}") for i in range(mod.CHUNK * 3)]
    real, off, mono = gw.llm_call, mod._offline, mod.time.monotonic
    try:
        gw.llm_call = slow
        mod._offline = lambda: False
        mod.time.monotonic = lambda: clock["t"]
        out = mod.judge_relevance(enterprise_id="e", goal_text="g",
                                  definition_text="d", findings=findings)
    finally:
        gw.llm_call, mod._offline, mod.time.monotonic = real, off, mono

    # The first WAVE (up to MAX_PARALLEL chunks) always fires before the
    # deadline can stop it; the second wave never starts.
    assert calls["n"] == min(mod.MAX_PARALLEL, 3)
    # And nothing it never judged was set aside.
    kept, aside = partition(findings, out)
    assert len(kept) == len(findings)
    assert aside == []


def test_the_deadline_is_checked_before_a_call_not_after():
    """A budget tested only on the way out still pays for the call that broke
    it. With the budget already spent, the gate must make NO call at all."""
    import app.crucible.relevance as mod
    import app.graph.gateway as gw

    calls = {"n": 0}

    def counted(**kw):
        calls["n"] += 1

        class _R:
            output = {"verdicts": []}
        return _R()

    real, off, mono = gw.llm_call, mod._offline, mod.time.monotonic
    t = {"v": 0.0}
    try:
        gw.llm_call = counted
        mod._offline = lambda: False
        # Time jumps past the deadline between the budget being set and the
        # first chunk being asked for.
        seq = iter([0.0] + [mod.DEADLINE_SECONDS + 1.0] * 20)
        mod.time.monotonic = lambda: next(seq, mod.DEADLINE_SECONDS + 1.0)
        mod.judge_relevance(enterprise_id="e", goal_text="g",
                            definition_text="d", findings=[_f("a")])
    finally:
        gw.llm_call, mod._offline, mod.time.monotonic = real, off, mono

    assert calls["n"] == 0
