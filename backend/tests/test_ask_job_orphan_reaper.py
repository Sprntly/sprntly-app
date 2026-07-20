"""Tests for app.db.asks.fail_orphan_generating_ask_jobs.

A process that dies mid-answer strands its `ask_jobs` row in `generating`. The
chat UI polls `GET /v1/ask/{id}` until the status leaves `generating`, so an
abandoned row makes the client spin forever with no error to explain it — which
is what a deploy restart did to staging job 189.

The reaper flips those rows to `error`. The age cutoff is the load-bearing part:
staging and prod share ONE Supabase project, so a blanket "fail everything
generating" sweep would kill answers the OTHER environment is generating right
now. Rows carry no owner/heartbeat column, so age is the only available signal.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

from app.db.asks import (
    ORPHAN_ASK_JOB_ERROR,
    fail_orphan_generating_ask_jobs,
)
from app.db.client import require_client


def _iso(minutes_ago: int) -> str:
    return (
        datetime.now(timezone.utc) - timedelta(minutes=minutes_ago)
    ).isoformat()


_COMPANY_ID = "co-reaper"


def _ensure_company() -> str:
    """ask_jobs.company_id has a FK to companies(id) in the fake Supabase."""
    c = require_client()
    existing = (
        c.table("companies").select("id").eq("id", _COMPANY_ID).execute().data
    )
    if not existing:
        c.table("companies").insert({
            "id": _COMPANY_ID,
            "slug": f"slug-{_COMPANY_ID}",
            "display_name": "Reaper Co",
        }).execute()
    return _COMPANY_ID


def _insert(status: str, minutes_ago: int, question: str = "q") -> int:
    c = require_client()
    row = c.table("ask_jobs").insert({
        "company_id": _ensure_company(),
        "dataset": "ds-1",
        "question": question,
        "status": status,
        "response": {},
        "updated_at": _iso(minutes_ago),
    }).execute()
    return row.data[0]["id"]


def _status(ask_id: int) -> tuple[str, str | None]:
    c = require_client()
    rows = c.table("ask_jobs").select("*").eq("id", ask_id).execute().data
    return rows[0]["status"], rows[0].get("error")


def test_stale_generating_job_is_failed(isolated_settings):
    ask_id = _insert("generating", minutes_ago=60)

    assert fail_orphan_generating_ask_jobs() == 1

    status, error = _status(ask_id)
    assert status == "error"
    assert error == ORPHAN_ASK_JOB_ERROR


def test_recent_generating_job_is_left_alone(isolated_settings):
    """The cross-environment guard: prod and staging share this table, so a job
    that started moments ago may be actively owned by the other process."""
    ask_id = _insert("generating", minutes_ago=1)

    assert fail_orphan_generating_ask_jobs() == 0

    status, _ = _status(ask_id)
    assert status == "generating"


def test_terminal_statuses_are_never_touched(isolated_settings):
    ready = _insert("ready", minutes_ago=60)
    cancelled = _insert("cancelled", minutes_ago=60)
    errored = _insert("error", minutes_ago=60)

    assert fail_orphan_generating_ask_jobs() == 0

    assert _status(ready)[0] == "ready"
    assert _status(cancelled)[0] == "cancelled"
    assert _status(errored)[0] == "error"


def test_cutoff_is_configurable(isolated_settings):
    ask_id = _insert("generating", minutes_ago=5)

    # Default (15m) leaves it; an explicit tighter window reaps it.
    assert fail_orphan_generating_ask_jobs() == 0
    assert fail_orphan_generating_ask_jobs(older_than_minutes=2) == 1
    assert _status(ask_id)[0] == "error"


def test_sweep_is_idempotent(isolated_settings):
    _insert("generating", minutes_ago=60)

    assert fail_orphan_generating_ask_jobs() == 1
    assert fail_orphan_generating_ask_jobs() == 0
