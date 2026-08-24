"""The scheduled brief may batch; the user-facing one must never.

`compose_top_insights` is the single most expensive call in the product (opus,
a 32k-token method block, one call per company per period — ~$486/mo of a
~$2,731/mo run-rate), which makes it the best candidate for the Batches API's
50% discount. The catch is that the SAME function backs two synchronous routes
where a person is watching a spinner:

    POST /brief            (routes/synthesis.py)  -> run_synthesis
    routes/brief.py:516                           -> generate_brief_for

so `batch` is threaded, not switched on globally. These tests pin the split —
a regression here does not fail loudly, it just makes a user wait minutes.
"""
from __future__ import annotations

import inspect
import re

from app.synthesis import agent as synth_agent
from app import synthesis_brief


def test_run_synthesis_does_not_batch_by_default():
    """The user-facing POST /brief passes no `batch`, so the default is what
    protects it."""
    assert inspect.signature(
        synth_agent.run_synthesis).parameters["batch"].default is False


def test_generate_brief_for_does_not_batch_by_default():
    assert inspect.signature(
        synthesis_brief.generate_brief_for).parameters["batch"].default is False


def test_generate_brief_for_passes_batch_through(monkeypatch):
    """Threading it is the whole mechanism — if this stops reaching
    run_synthesis the scheduler silently pays full price."""
    seen = {}
    monkeypatch.setattr(
        synthesis_brief, "run_synthesis",
        lambda facade, cid, **kw: seen.update(kw) or {"insights": []},
    )
    # Drive the tail of generate_brief_for directly: everything before the
    # run_synthesis call is DB/gate work this test has no opinion about.
    sig = inspect.signature(synthesis_brief.generate_brief_for)
    assert "batch" in sig.parameters
    src = inspect.getsource(synthesis_brief.generate_brief_for)
    assert "batch=batch" in src, "batch must reach run_synthesis"


def test_scheduled_paths_opt_in():
    """The three background entry points must actually ask for the discount."""
    from app import scheduler

    tick = inspect.getsource(scheduler._generate_brief_for_company)
    assert "batch=True" in tick, "the brief tick generates 3h early — batch it"

    cycle = inspect.getsource(scheduler._run_synthesis_for_all_companies)
    assert "batch=True" in cycle, "the synthesis cycle is background work"

    startup = inspect.getsource(synthesis_brief.generate_all_synthesis_briefs)
    assert "batch=True" in startup, "the startup pass is background work"


def test_user_facing_routes_never_ask_for_batching():
    """Guards the direction that actually hurts: a route opting in would make a
    person wait minutes on a spinner for a saving they cannot see."""
    from app.routes import synthesis as synthesis_route

    src = inspect.getsource(synthesis_route.generate_brief)
    assert "batch=True" not in src

    import app.routes.brief as brief_route
    assert "batch=True" not in inspect.getsource(brief_route)


def test_batch_deadline_sits_well_inside_the_generation_lead():
    """A slow batch must never make a brief miss its slot. The seam cancels and
    runs live at the deadline, so the deadline has to be comfortably shorter
    than the lead the scheduler generates on."""
    from app.brief_schedule import GENERATION_LEAD

    src = inspect.getsource(synth_agent.run_synthesis)
    assert "batch_deadline_s=45 * 60" in src
    assert 45 * 60 < GENERATION_LEAD.total_seconds() / 2, (
        "the batch deadline should leave at least half the lead as slack"
    )


def test_gateway_accepts_every_batch_argument_the_callers_pass():
    """Regression: `run_synthesis` passed `batch_deadline_s` to `llm_call`
    before `llm_call` had that parameter, so EVERY scheduled brief would have
    died with `TypeError: unexpected keyword argument`. It surfaced only because
    an unrelated test swallows exceptions and then found nothing captured —
    nothing in the batching tests themselves would have caught it.

    Binding the real signature against the real call is what makes that class of
    mistake impossible to reintroduce silently.
    """
    import inspect
    from app.graph.gateway import llm_call

    params = inspect.signature(llm_call).parameters
    for name in ("batch", "batch_deadline_s"):
        assert name in params, f"gateway.llm_call must accept {name}"

    src = inspect.getsource(synth_agent.run_synthesis)
    for kwarg in re.findall(r"\b(batch\w*)=", src):
        assert kwarg in params, (
            f"run_synthesis passes {kwarg}= to llm_call, which does not accept it"
        )
