"""Unit tests for the shared execution-lifecycle primitive
(`app.ask_job_runner.run_execution_job` + `ExecutionOutcome` +
`_classify_error`).

The primitive is the spine main / private / group all run through, so these
tests pin its contract directly against fakes (no DB, no LLM, no network):

  * exactly ONE guarded terminal transition per run (AC3);
  * the SUCCESS write + payload are byte-identical to the pre-extraction path
    (AC1) and the post-terminal `on_committed` side effects run in order
    (AC2);
  * `AskCancelled` leaves the row `cancelled`, never `error` (AC1);
  * `error_class` is classified from the raised exception and the raw message
    never leaks into the class (AC4);
  * terminal-once is keyed to the guarded write — a reaper that finalized
    first makes the worker's terminal write no-op; the mutation proof shows
    that dropping the guard would double-write (AC3, PI13).
"""
from __future__ import annotations

import asyncio

import httpx
import pytest

import app.ask_job_runner as runner
from app.ask_job_runner import ExecutionOutcome, run_execution_job
from app.qa_agent import AskCancelled


class _Row:
    """A minimal stand-in for the one `ask_jobs` row a run owns, modelling
    the `.eq('status','generating')` guard the real `complete_ask_job` /
    `fail_ask_job` writes carry."""

    def __init__(self) -> None:
        self.status = "generating"
        self.response: dict = {}
        self.error: str | None = None
        self.error_class: str | None = None
        self.terminal_writes = 0

    def guarded_complete(self, payload: dict) -> None:
        if self.status == "generating":
            self.status = "ready"
            self.response = payload
            self.error = None
            self.terminal_writes += 1

    def guarded_fail(self, msg: str, error_class: str | None = None) -> None:
        if self.status == "generating":
            self.status = "error"
            self.error = msg
            self.error_class = error_class
            self.terminal_writes += 1


def _bind_guarded(monkeypatch, row: _Row) -> None:
    monkeypatch.setattr(runner, "complete_ask_job", lambda job_id, payload: row.guarded_complete(payload))
    monkeypatch.setattr(runner, "fail_ask_job", lambda job_id, msg, error_class=None: row.guarded_fail(msg, error_class))


# ─────────────────────────── success / byte-identity ───────────────────────

async def test_run_execution_job_success_writes_ready_once(monkeypatch):
    row = _Row()
    _bind_guarded(monkeypatch, row)
    payload = {"answer": "hi", "citations": []}

    outcome = await run_execution_job(
        job_id=1, run_id="r1",
        is_cancelled=lambda: False,
        heartbeat=lambda: row.status == "generating",
        body=lambda: ExecutionOutcome(status="ready", response=payload),
    )
    assert outcome.status == "ready"
    assert row.status == "ready"
    assert row.terminal_writes == 1, "exactly one terminal transition"


async def test_run_execution_job_main_terminal_writes_byte_identical(monkeypatch):
    """The SUCCESS write stores EXACTLY the payload the body returned, and no
    fail write is issued — byte-identical to the pre-extraction
    `complete_ask_job(ask_id, _strip_citations(payload))` success write."""
    row = _Row()
    completed: dict = {}
    monkeypatch.setattr(runner, "complete_ask_job", lambda job_id, payload: completed.update({"id": job_id, "payload": payload}) or row.guarded_complete(payload))
    monkeypatch.setattr(runner, "fail_ask_job", lambda *a, **k: pytest.fail("fail_ask_job must not fire on success"))
    payload = {"answer": "the answer", "citations": [], "_skill": None}

    await run_execution_job(
        job_id=42, run_id="r",
        is_cancelled=lambda: False,
        heartbeat=lambda: row.status == "generating",
        body=lambda: ExecutionOutcome(status="ready", response=payload),
    )
    assert completed == {"id": 42, "payload": payload}
    assert completed["payload"] is payload, "the stored payload is the body's, unchanged"


async def test_run_execution_job_post_terminal_ordering_preserved(monkeypatch):
    """`on_committed` runs AFTER the terminal success write — the seam
    main/private hangs `capture_report → promote → ingest` off, in order."""
    row = _Row()
    order: list[str] = []
    monkeypatch.setattr(runner, "complete_ask_job", lambda job_id, payload: order.append("complete") or row.guarded_complete(payload))
    monkeypatch.setattr(runner, "fail_ask_job", lambda *a, **k: None)

    def _on_committed(outcome: ExecutionOutcome) -> None:
        order.append("capture_report")
        order.append("promote")
        order.append("ingest")

    await run_execution_job(
        job_id=1, run_id="r",
        is_cancelled=lambda: False,
        heartbeat=lambda: row.status == "generating",
        body=lambda: ExecutionOutcome(status="ready", response={"answer": "x"}),
        on_committed=_on_committed,
    )
    assert order == ["complete", "capture_report", "promote", "ingest"]


async def test_run_execution_job_on_committed_not_run_on_failure(monkeypatch):
    row = _Row()
    _bind_guarded(monkeypatch, row)
    ran: list[str] = []

    def _boom() -> ExecutionOutcome:
        raise RuntimeError("nope")

    await run_execution_job(
        job_id=1, run_id="r",
        is_cancelled=lambda: False,
        heartbeat=lambda: row.status == "generating",
        body=_boom,
        on_committed=lambda outcome: ran.append("committed"),
    )
    assert ran == [], "post-terminal side effects must not run on a failed body"
    assert row.status == "error"


# ─────────────────────────────── cancel ────────────────────────────────────

async def test_run_execution_job_cancel_leaves_cancelled(monkeypatch):
    row = _Row()
    row.status = "cancelled"  # the /cancel endpoint already wrote it
    _bind_guarded(monkeypatch, row)

    def _cancelled_body() -> ExecutionOutcome:
        raise AskCancelled()

    outcome = await run_execution_job(
        job_id=1, run_id="r",
        is_cancelled=lambda: True,
        heartbeat=lambda: row.status == "generating",
        body=_cancelled_body,
    )
    assert outcome.status == "cancelled"
    assert row.status == "cancelled"
    assert row.error is None, "a cancel must never write an error"
    assert row.terminal_writes == 0, "no complete/fail on the cancel path"


# ─────────────────────────── error classification ──────────────────────────

def _api_status_error() -> Exception:
    import anthropic

    resp = httpx.Response(status_code=402, request=httpx.Request("POST", "http://x"))
    return anthropic.APIStatusError("insufficient credit", response=resp, body=None)


def test_classify_error_billing():
    assert runner._classify_error(_api_status_error()) == "billing"


def test_classify_error_timeout():
    import anthropic

    assert runner._classify_error(TimeoutError("slow")) == "timeout"
    assert runner._classify_error(
        anthropic.APITimeoutError(request=httpx.Request("POST", "http://x"))
    ) == "timeout"
    assert runner._classify_error(
        httpx.ReadTimeout("read", request=httpx.Request("POST", "http://x"))
    ) == "timeout"


def test_classify_error_local_gate():
    from fastapi import HTTPException

    assert runner._classify_error(HTTPException(status_code=403, detail="denied")) == "local_gate"


def test_classify_error_app_and_no_message_leak():
    exc = RuntimeError("secret internal detail")
    ec = runner._classify_error(exc)
    assert ec == "app"
    assert "secret" not in ec


async def test_run_execution_job_error_class_classification(monkeypatch):
    """End-to-end: a raised exception is classified and the class is written
    onto the terminal-fail; the raw message rides `ask_jobs.error`, never the
    class."""
    import anthropic

    cases = [
        (_api_status_error(), "billing"),
        (anthropic.APITimeoutError(request=httpx.Request("POST", "http://x")), "timeout"),
        (RuntimeError("kaboom"), "app"),
    ]
    for exc, expected in cases:
        row = _Row()
        _bind_guarded(monkeypatch, row)

        def _raise(_exc=exc) -> ExecutionOutcome:
            raise _exc

        outcome = await run_execution_job(
            job_id=1, run_id="r",
            is_cancelled=lambda: False,
            heartbeat=lambda: row.status == "generating",
            body=_raise,
        )
        assert outcome.status == "error"
        assert outcome.error_class == expected, (exc, expected)
        assert row.error_class == expected
        # The raw message is preserved on the internal `error` column (never
        # exposed on a read/broadcast), and the class is a clean fixed-vocab
        # category — never the message itself.
        assert row.error, "the raw debug message is preserved internally"
        assert row.error_class in {"billing", "timeout", "local_gate", "app"}
        assert row.error_class != row.error


# ──────────────────── terminal-once + reaper mutation proof ─────────────────

async def test_run_execution_job_terminal_once_guard(monkeypatch):
    """A reaper that finalized the row mid-run wins; the worker's guarded
    terminal write then no-ops — exactly ONE terminal transition (AC3)."""
    row = _Row()
    _bind_guarded(monkeypatch, row)

    def _body() -> ExecutionOutcome:
        # Simulate the orphan reaper firing WHILE the body runs: it flips the
        # row out of `generating` via the SAME guarded fail write.
        row.guarded_fail("Generation was interrupted by a server restart.", "app")
        return ExecutionOutcome(status="ready", response={"answer": "late"})

    await run_execution_job(
        job_id=1, run_id="r",
        is_cancelled=lambda: False,
        heartbeat=lambda: row.status == "generating",
        body=_body,
    )
    assert row.status == "error", "the reaper's terminal write stands"
    assert row.terminal_writes == 1, "the worker's complete no-op'd on the guard"


async def test_terminal_guard_removed_double_write_is_red(monkeypatch):
    """MUTATION (PI13): drop the `.eq('status','generating')` guard from the
    success write and the reaper + worker BOTH finalize — the single-terminal
    invariant breaks (two writes, the reaper's `error` clobbered to `ready`).
    This proves the guard is the terminal-once mechanism the primitive relies
    on; the guarded variant above is GREEN, this unguarded variant is RED."""
    row = _Row()

    def _unguarded_complete(job_id, payload):
        # The mutation: no status guard — writes unconditionally.
        row.status = "ready"
        row.response = payload
        row.terminal_writes += 1

    monkeypatch.setattr(runner, "complete_ask_job", _unguarded_complete)
    monkeypatch.setattr(runner, "fail_ask_job", lambda job_id, msg, error_class=None: row.guarded_fail(msg, error_class))

    def _body() -> ExecutionOutcome:
        row.guarded_fail("reaped", "app")  # reaper finalizes first
        return ExecutionOutcome(status="ready", response={"answer": "late"})

    await run_execution_job(
        job_id=1, run_id="r",
        is_cancelled=lambda: False,
        heartbeat=lambda: row.status == "generating",
        body=_body,
    )
    # Without the guard the worker clobbers the reaper: two terminal writes,
    # final state wrongly `ready`. This is the RED the guard prevents.
    assert row.terminal_writes == 2
    assert row.status == "ready"


# ─────────────────────────────── heartbeat ─────────────────────────────────

async def test_run_execution_job_heartbeat_beats_and_stops(monkeypatch):
    monkeypatch.setattr(runner, "ORPHAN_ASK_JOB_HEARTBEAT_SECONDS", 0.01)
    row = _Row()
    _bind_guarded(monkeypatch, row)
    beats: list[int] = []

    def _heartbeat() -> bool:
        beats.append(1)
        return row.status == "generating"

    import time as _time

    def _slow_body() -> ExecutionOutcome:
        _time.sleep(0.08)
        return ExecutionOutcome(status="ready", response={"answer": "done"})

    await run_execution_job(
        job_id=1, run_id="r",
        is_cancelled=lambda: False,
        heartbeat=_heartbeat,
        body=_slow_body,
    )
    assert len(beats) >= 2, f"a long run must be beaten (got {len(beats)})"
    # The beat is cancelled with the job — no beats after it finishes.
    before = len(beats)
    await asyncio.sleep(0.03)
    assert len(beats) == before, "the heartbeat outlived the run"
