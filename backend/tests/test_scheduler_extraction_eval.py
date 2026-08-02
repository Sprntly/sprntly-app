"""Tests for the extraction-eval scheduler job: registration (opt-in via
EXTRACTION_EVAL_ENABLED, mirroring the drip-email job's wiring test) and the
job function itself (`_run_extraction_eval_cycle`) — fully isolated, never
crashes the scheduler."""
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


def _run_start_scheduler(monkeypatch, *, eval_enabled):
    from app import scheduler as sched_mod
    monkeypatch.setattr(sched_mod.settings, "scheduler_enabled", True)
    monkeypatch.setattr(sched_mod.settings, "pipeline_interval_hours", 6)
    monkeypatch.setattr(sched_mod.settings, "extraction_eval_enabled", eval_enabled)
    monkeypatch.setattr(sched_mod.settings, "extraction_eval_interval_hours", 24)
    fake = _FakeScheduler()
    monkeypatch.setattr(sched_mod, "AsyncIOScheduler", lambda: fake)
    sched_mod.start_scheduler()
    sched_mod.shutdown_scheduler()
    return fake


def test_start_scheduler_registers_extraction_eval_job_when_enabled(monkeypatch):
    fake = _run_start_scheduler(monkeypatch, eval_enabled=True)
    ids = sorted(j["id"] for j in fake.jobs)
    assert "extraction_eval" in ids
    assert "brief_tick" in ids
    assert fake.started is True


def test_start_scheduler_omits_extraction_eval_job_when_disabled(monkeypatch):
    fake = _run_start_scheduler(monkeypatch, eval_enabled=False)
    ids = sorted(j["id"] for j in fake.jobs)
    assert "extraction_eval" not in ids
    assert "brief_tick" in ids


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
