"""Which background call sites take the half-price Batches path.

Batching trades latency for 50% off, so the only safe places to switch it on
are calls with nobody waiting. These tests pin BOTH directions per site: the
warm path batches, and the user-facing path through the same code does not.
"""
import inspect

import pytest


def _llm_call_kwargs(monkeypatch, module_path: str, attr: str = "llm_call"):
    """Capture the kwargs the module's next llm_call receives."""
    import importlib

    mod = importlib.import_module(module_path)
    seen: dict = {}

    def fake(**kw):
        seen.update(kw)
        raise RuntimeError("stop after capture")

    monkeypatch.setattr(mod, attr, fake)
    return mod, seen


# ─── evidence warm ──────────────────────────────────────────────────────────


def test_evidence_batches_only_on_the_background_path():
    """`_run_sync` passes batch=background verbatim — warm batches, the
    user-facing corpus fallback from evidence_kg (which passes no `background`)
    does not."""
    from app import evidence_runner

    src = inspect.getsource(evidence_runner._run_sync)
    assert "batch=background" in src
    # The default must be False so the evidence_kg fallback stays live.
    sig = inspect.signature(evidence_runner._run_sync)
    assert sig.parameters["background"].default is False


def test_evidence_kg_fallback_does_not_pass_background():
    """The guard behind the test above: if evidence_kg ever started passing
    background=True, a user's 'View evidence' click would silently start
    waiting on a batch."""
    from app import evidence_kg

    src = inspect.getsource(evidence_kg)
    call = src[src.index("_legacy_run_sync("):]
    call = call[:call.index(")") + 1]
    assert "background" not in call, f"user-facing fallback now passes: {call}"


# ─── PRD warm ───────────────────────────────────────────────────────────────


def test_prd_part_a_batches_only_on_the_background_path():
    from app import prd_runner

    src = inspect.getsource(prd_runner._call_part_a)
    assert "batch=background" in src
    assert inspect.signature(prd_runner._call_part_a).parameters["background"].default is False


@pytest.mark.parametrize("route_file", [
    "app/routes/prd.py", "app/routes/brief.py", "app/multi_agent_orchestrator.py",
])
def test_no_user_facing_entry_point_asks_for_the_background_lane(route_file):
    """Every interactive PRD entry point must leave `background` alone.

    This is the whole safety argument for `batch=background`: one of these
    passing background=True would put a person behind a Message Batch.
    """
    text = open(route_file).read()
    for line in text.splitlines():
        if "generate_prd" in line and "background=True" in line:
            pytest.fail(f"{route_file}: interactive PRD call asks for the background lane: {line.strip()}")


# ─── Ask warm ───────────────────────────────────────────────────────────────


def test_ask_warm_always_batches():
    """`_generate_one_sync` has exactly one caller (`_warm_one`), so it is
    unconditionally warm and batches unconditionally."""
    from app import ask_runner

    src = inspect.getsource(ask_runner._generate_one_sync)
    assert "batch=True" in src


def test_ask_warm_has_no_other_caller():
    """The premise of the test above, pinned so it cannot rot: if a second,
    interactive caller appears, the unconditional batch=True becomes wrong."""
    import re

    src = open("app/ask_runner.py").read()
    uses = [m for m in re.findall(r"_generate_one_sync", src)]
    # one definition + one to_thread reference
    assert len(uses) == 2, f"_generate_one_sync now has {len(uses) - 1} call sites"


# ─── ideation ───────────────────────────────────────────────────────────────


def test_sequence_ideation_threads_batch_from_the_caller():
    """Not unconditional: run_synthesis passes batch=True only for the
    SCHEDULED callers, and the user-facing brief routes pass False."""
    from app.synthesis import ideation

    sig = inspect.signature(ideation.sequence_ideation)
    assert sig.parameters["batch"].default is False
    assert sig.parameters["batch_deadline_s"].default is None
    assert "batch=batch" in inspect.getsource(ideation.sequence_ideation)


def test_run_synthesis_actually_passes_batch_down_to_ideation():
    """Binds the two halves — a rename on either side breaks this, not prod."""
    from app.synthesis import agent

    src = inspect.getsource(agent.run_synthesis)
    call = src[src.index("sequence_ideation("):]
    call = call[:call.index(")") + 1]
    assert "batch=batch" in call, call
    assert "batch_deadline_s=batch_deadline_s" in call, call
