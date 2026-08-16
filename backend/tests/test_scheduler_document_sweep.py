"""The recurring sweep for team documents abandoned mid-generation.

WHY THIS FILE EXISTS. `sweep_orphan_generating` was written, tested, and called
from exactly one place: application startup. A comment in main.py said "the
scheduler and the next restart both retry it" — and the scheduler never did,
because the call was never added beside its four siblings in
`_run_orphan_ask_job_sweep`.

That gap is not cosmetic, and the age gate is what makes it bite. A document is
only swept once it has sat in `generating` for 30 minutes, so the restart that
orphaned it is BY DEFINITION too early to clean it up. With no recurring sweep,
the panel spins on a document nothing is writing until the next restart — days,
on prod. A periodic sweep is what makes an age-gated sweep work at all.

So these tests pin the WIRING, which is the part that was missing, and the
isolation, which is what lets it live next to four other sweeps safely.
"""
from __future__ import annotations

import pytest


def test_the_recurring_sweep_actually_sweeps_documents(monkeypatch):
    """THE REGRESSION. Without this call, a 30-minute age gate can only ever be
    satisfied by a process that has been up for 30 minutes and then restarts —
    i.e. the healing path is a coincidence rather than a mechanism."""
    from app import custom_artifact_generate as gen
    from app import scheduler as sched_mod

    called: list[bool] = []
    monkeypatch.setattr(
        gen, "sweep_orphan_generating", lambda *a, **kw: (called.append(True), 3)[1]
    )
    # The four siblings are not under test here; stub them so this asserts one
    # thing. Each is imported INSIDE the function, so patching the source module
    # is what takes effect.
    _silence_siblings(monkeypatch)

    sched_mod._run_orphan_ask_job_sweep()

    assert called == [True], "the scheduler must sweep abandoned documents"


def test_a_failing_document_sweep_cannot_take_the_scheduler_down(monkeypatch):
    """Same contract as every sibling sweep: fully isolated. A sweep that
    raised would otherwise kill the whole 5-minute job, silently stopping the
    ask-job, pipeline-run, research-run and business-context sweeps too."""
    from app import custom_artifact_generate as gen
    from app import scheduler as sched_mod

    def _boom(*a, **kw):
        raise RuntimeError("table does not exist")

    monkeypatch.setattr(gen, "sweep_orphan_generating", _boom)
    _silence_siblings(monkeypatch)

    sched_mod._run_orphan_ask_job_sweep()  # must not raise


def test_a_failing_sibling_cannot_stop_the_document_sweep(monkeypatch):
    """The isolation has to run BOTH ways. A sweep placed after an unguarded
    neighbour is a sweep that stops running the day the neighbour breaks."""
    from app import custom_artifact_generate as gen
    from app import scheduler as sched_mod
    from app.db import asks as asks_mod

    called: list[bool] = []
    monkeypatch.setattr(
        gen, "sweep_orphan_generating", lambda *a, **kw: (called.append(True), 0)[1]
    )
    _silence_siblings(monkeypatch)
    monkeypatch.setattr(
        asks_mod, "fail_orphan_generating_ask_jobs",
        lambda *a, **kw: (_ for _ in ()).throw(RuntimeError("supabase down")),
    )

    sched_mod._run_orphan_ask_job_sweep()

    assert called == [True]


def _silence_siblings(monkeypatch) -> None:
    from app.db import asks, business_context_refresh, company_research_runs, pipeline_runs

    monkeypatch.setattr(asks, "fail_orphan_generating_ask_jobs", lambda *a, **kw: 0)
    monkeypatch.setattr(pipeline_runs, "fail_orphan_running_runs", lambda *a, **kw: 0)
    monkeypatch.setattr(
        company_research_runs, "fail_orphan_company_research_runs", lambda *a, **kw: 0
    )
    monkeypatch.setattr(
        business_context_refresh, "fail_orphan_business_context_refreshes",
        lambda *a, **kw: 0,
    )
