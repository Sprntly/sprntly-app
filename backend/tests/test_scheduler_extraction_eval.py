"""Tests for the extraction-eval scheduler job: unconditional registration
(mirroring the orphan-ask-job sweep and Jira GDPR job, which also run
unconditionally) and the job function itself (`_run_extraction_eval_cycle`)
— fully isolated, never crashes the scheduler."""
from __future__ import annotations

from unittest.mock import patch


class _FakeScheduler:
    def __init__(self):
        self.jobs: list[dict] = []
        self.started = False

    def add_job(self, func, *, trigger=None, id=None, name=None,
                replace_existing=False):
        self.jobs.append({"func": func, "id": id, "name": name})

    def start(self):
        self.started = True

    def shutdown(self, wait=False):
        pass


def _run_start_scheduler(monkeypatch):
    from app import scheduler as sched_mod
    monkeypatch.setattr(sched_mod.settings, "scheduler_enabled", True)
    monkeypatch.setattr(sched_mod.settings, "pipeline_interval_hours", 6)
    monkeypatch.setattr(sched_mod.settings, "extraction_eval_interval_hours", 24)
    fake = _FakeScheduler()
    # `**_` so this fake tolerates whatever construction kwargs start_scheduler
    # passes. It now passes `job_defaults` (misfire grace + coalesce — the fix
    # this branch carries); a zero-arg lambda made every such addition a
    # TypeError in a test that is not about scheduler construction at all.
    monkeypatch.setattr(sched_mod, "AsyncIOScheduler", lambda **_: fake)
    sched_mod.start_scheduler()
    sched_mod.shutdown_scheduler()
    return fake


def test_start_scheduler_always_registers_extraction_eval_job(monkeypatch):
    fake = _run_start_scheduler(monkeypatch)
    ids = sorted(j["id"] for j in fake.jobs)
    assert "extraction_eval" in ids
    assert "brief_tick" in ids
    assert fake.started is True


def test_run_extraction_eval_cycle_delegates_to_run_scheduled_eval_cycle():
    from app.scheduler import _run_extraction_eval_cycle

    with patch("app.graph.evals.run_scheduled_eval_cycle",
               return_value={"companies": 2, "skills": 3, "sampled": 10,
                             "findings": 1}) as mock_run:
        _run_extraction_eval_cycle()

    mock_run.assert_called_once()


def test_run_extraction_eval_cycle_never_raises():
    """A failure inside the eval sweep must never crash the scheduler tick."""
    from app.scheduler import _run_extraction_eval_cycle

    with patch("app.graph.evals.run_scheduled_eval_cycle",
               side_effect=RuntimeError("boom")):
        _run_extraction_eval_cycle()  # must not raise
