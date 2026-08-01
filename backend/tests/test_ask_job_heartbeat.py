"""The ask worker's liveness heartbeat (app.ask_job_runner._heartbeat).

Why this exists — the staging blocker, reproduced 4/4 times:

  t≈12min  the chat's poll budget (runAskGeneration MAX_MS) expires →
           "Timed out waiting for the answer". The pending marker is
           deliberately LEFT so a reload can re-attach.
  t≈15min  the orphan sweep fails the row, because `updated_at` had not moved
           since creation (report paths publish no deltas, so nothing touched
           it) and age was the only "is the worker dead?" signal available.
           The worker was very much alive.
  t≈20min+ the worker finishes; complete_ask_job is guarded on
           `status == 'generating'`, so the write NO-OPS and the answer is
           discarded. A reload finds `error`, which is why it never resumed.

The heartbeat turns that age gate back into a liveness check. This module tests
the loop; test_ask_job_orphan_reaper covers what the beat does to the sweep.
"""
from __future__ import annotations

import asyncio

import pytest

import app.ask_job_runner as runner


async def test_heartbeat_beats_until_the_job_leaves_generating(monkeypatch):
    beats = []

    def _touch(ask_id: int) -> bool:
        beats.append(ask_id)
        return len(beats) < 3        # third beat finds a terminal row

    monkeypatch.setattr(runner, "touch_ask_job", _touch)
    monkeypatch.setattr(runner, "ORPHAN_ASK_JOB_HEARTBEAT_SECONDS", 0)
    await asyncio.wait_for(runner._heartbeat(42), timeout=5)
    assert beats == [42, 42, 42], "the loop must stop when the row is terminal"


async def test_heartbeat_survives_a_transient_db_error(monkeypatch):
    """A blip must not stop the beat — losing the beat means losing the run."""
    calls = {"n": 0}

    def _touch(_ask_id: int) -> bool:
        calls["n"] += 1
        if calls["n"] == 1:
            raise RuntimeError("db blip")
        return False

    monkeypatch.setattr(runner, "touch_ask_job", _touch)
    monkeypatch.setattr(runner, "ORPHAN_ASK_JOB_HEARTBEAT_SECONDS", 0)
    # The loop swallows the error and exits without raising.
    await asyncio.wait_for(runner._heartbeat(7), timeout=5)
    assert calls["n"] >= 1


async def test_heartbeat_is_cancellable(monkeypatch):
    monkeypatch.setattr(runner, "touch_ask_job", lambda _id: True)
    monkeypatch.setattr(runner, "ORPHAN_ASK_JOB_HEARTBEAT_SECONDS", 0.01)
    task = asyncio.create_task(runner._heartbeat(1))
    await asyncio.sleep(0.05)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task


async def test_a_long_answer_is_beaten_for_its_whole_duration(monkeypatch):
    """The shape of the real failure: an answer that outlives the sweep window.
    With a fake clock, a 'slow' answer must be beaten while it runs and the beat
    must stop as soon as the run returns."""
    beats = []
    monkeypatch.setattr(runner, "touch_ask_job",
                        lambda ask_id: beats.append(ask_id) or True)
    monkeypatch.setattr(runner, "ORPHAN_ASK_JOB_HEARTBEAT_SECONDS", 0.01)
    monkeypatch.setattr(runner, "complete_ask_job", lambda *a, **k: None)
    monkeypatch.setattr(runner, "capture_report", lambda *a, **k: None)
    monkeypatch.setattr(runner, "is_ask_cancelled", lambda _id: False)
    monkeypatch.setattr(runner.token_stream, "close", lambda *a, **k: None)
    monkeypatch.setattr(
        runner.token_stream, "delta_sink", lambda *a, **k: (lambda _t: None)
    )

    import time as _time

    def _slow_answer(**kwargs):
        _time.sleep(0.25)            # "runs long", like a CIR sweep
        return {"answer": "done", "citations": []}

    monkeypatch.setattr(runner.qa_agent, "answer", _slow_answer)
    monkeypatch.setattr(runner, "log_ask", lambda **k: None, raising=False)

    await runner.run_ask_job(
        ask_id=99, enterprise_id="e1", question="run a competitive review",
        dataset="ds",
    )
    assert len(beats) >= 2, f"a long answer was beaten only {len(beats)} time(s)"
    # The beat is cancelled with the job: no beats after it finished.
    before = len(beats)
    await asyncio.sleep(0.05)
    assert len(beats) == before, "the heartbeat outlived the job"
