"""Async story-generation route: POST is fire-and-forget (returns a job id),
GET /jobs/{id} polls until ready/failed. Replaces the old synchronous POST that
blocked the Tickets tab on a multi-minute LLM call.
"""
from __future__ import annotations

import asyncio
import threading

import pytest

from app.auth import CompanyContext
from app.routes import stories
from app.stories.generate import PRDNotFoundError, Story


def _ctx(cid: str = "ent-A") -> CompanyContext:
    return CompanyContext(company_id=cid, role="owner", user_id="u")


async def _drain(job_id: int, tries: int = 100) -> None:
    """Wait for the background task to leave the 'generating' state."""
    for _ in range(tries):
        if stories._jobs[job_id]["status"] != "generating":
            return
        await asyncio.sleep(0.02)


def test_poll_streams_partial_batches_before_ready(isolated_settings, monkeypatch):
    """Fan-out publishes tickets batch-by-batch; a poll mid-run sees the partial
    set + progress while status is still 'generating', then the full set on ready."""
    gate = threading.Event()

    def _staged(cid, prd_id=None, insight=None, on_batch=None, **kw):
        # Batch 1 lands…
        on_batch([Story(title="A", body="b")], 1, 2)
        gate.wait(2)  # …hold in 'generating' so the test can observe the partial
        both = [Story(title="A", body="b"), Story(title="B", body="b")]
        on_batch(both, 2, 2)
        return both

    monkeypatch.setattr(stories, "generate_user_stories", _staged)

    async def _flow():
        resp = await stories.generate(stories.GenerateIn(insight="x"), _ctx())
        jid = resp["job_id"]
        mid = None
        for _ in range(100):
            await asyncio.sleep(0.02)
            j = stories.get_job(jid, _ctx())
            if j["status"] == "generating" and j.get("stories"):
                mid = j
                break
        assert mid is not None, "partial batch never surfaced in the poll"
        assert [s["title"] for s in mid["stories"]] == ["A"]
        assert mid["progress"] == {"done": 1, "total": 2}
        gate.set()
        await _drain(jid)
        return stories.get_job(jid, _ctx())

    final = asyncio.run(_flow())
    assert final["status"] == "ready"
    assert [s["title"] for s in final["stories"]] == ["A", "B"]
    assert "progress" not in final, "progress cleared once complete"


def test_generate_returns_job_id_then_polls_ready(isolated_settings, monkeypatch):
    monkeypatch.setattr(
        stories, "generate_user_stories",
        lambda cid, prd_id=None, insight=None, **kw: [
            Story(title="Wire SSO", body="As a PM…"),
            Story(title="Add audit log", body="As an admin…"),
        ],
    )

    async def _flow():
        resp = await stories.generate(stories.GenerateIn(insight="Users want SSO"), _ctx())
        assert resp["status"] == "generating" and isinstance(resp["job_id"], int)
        await _drain(resp["job_id"])
        return resp["job_id"]

    job_id = asyncio.run(_flow())
    got = stories.get_job(job_id, _ctx())
    assert got["status"] == "ready"
    assert [s["title"] for s in got["stories"]] == ["Wire SSO", "Add audit log"]


def test_poll_reports_failure_instead_of_hanging(isolated_settings, monkeypatch):
    def _boom(cid, prd_id=None, insight=None, **kw):
        raise PRDNotFoundError("prd 999 not found")
    monkeypatch.setattr(stories, "generate_user_stories", _boom)

    async def _flow():
        resp = await stories.generate(stories.GenerateIn(prd_id=999), _ctx())
        await _drain(resp["job_id"])
        return resp["job_id"]

    job_id = asyncio.run(_flow())
    got = stories.get_job(job_id, _ctx())
    assert got["status"] == "failed"
    assert "not found" in got["error"]


def test_get_job_is_tenant_scoped(isolated_settings, monkeypatch):
    monkeypatch.setattr(
        stories, "generate_user_stories",
        lambda cid, prd_id=None, insight=None, **kw: [Story(title="X", body="b")],
    )

    async def _flow():
        resp = await stories.generate(stories.GenerateIn(insight="x"), _ctx("ent-A"))
        await _drain(resp["job_id"])
        return resp["job_id"]

    job_id = asyncio.run(_flow())
    # Owner sees it…
    assert stories.get_job(job_id, _ctx("ent-A"))["status"] == "ready"
    # …a foreign tenant gets 404 (job ids are sequential — no existence leak).
    with pytest.raises(Exception) as ei:
        stories.get_job(job_id, _ctx("ent-B"))
    assert getattr(ei.value, "status_code", None) == 404
    # Unknown id → 404.
    with pytest.raises(Exception) as ei2:
        stories.get_job(999999, _ctx("ent-A"))
    assert getattr(ei2.value, "status_code", None) == 404


def test_inflight_generate_dedupes_by_prd(isolated_settings, monkeypatch):
    """A rapid second /generate for the same PRD while the first is still
    running re-attaches to that job (same id, one LLM run) — this is the fix for
    the Tickets tab re-kicking generation on every remount/tab-switch."""
    calls = {"n": 0}
    release = threading.Event()

    def _slow(cid, prd_id=None, insight=None, **kw):
        calls["n"] += 1
        release.wait(2)  # hold the job in "generating" across the second call
        return [Story(title="T1", body="b")]

    monkeypatch.setattr(stories, "generate_user_stories", _slow)

    async def _flow():
        first = await stories.generate(stories.GenerateIn(prd_id=42), _ctx())
        # Second call lands while the first run is still blocked on `release`.
        second = await stories.generate(stories.GenerateIn(prd_id=42), _ctx())
        assert second["job_id"] == first["job_id"]  # re-attached, not a new job
        # A different PRD is its own job (not deduped).
        other = await stories.generate(stories.GenerateIn(prd_id=43), _ctx())
        assert other["job_id"] != first["job_id"]
        release.set()
        await _drain(first["job_id"])
        await _drain(other["job_id"])
        return first["job_id"]

    job_id = asyncio.run(_flow())
    assert stories.get_job(job_id, _ctx())["status"] == "ready"
    assert calls["n"] == 2  # PRD 42 ran once (deduped), PRD 43 once — never thrice


def test_generate_after_completion_starts_fresh_job(isolated_settings, monkeypatch):
    """Dedup is in-flight only: once a run finishes, a later /generate for the
    same PRD (e.g. the PRD changed → stale cache) starts a brand-new job."""
    monkeypatch.setattr(
        stories, "generate_user_stories",
        lambda cid, prd_id=None, insight=None, **kw: [Story(title="T", body="b")],
    )

    async def _flow():
        first = await stories.generate(stories.GenerateIn(prd_id=7), _ctx())
        await _drain(first["job_id"])  # finishes → no longer "generating"
        second = await stories.generate(stories.GenerateIn(prd_id=7), _ctx())
        await _drain(second["job_id"])
        return first["job_id"], second["job_id"]

    a, b = asyncio.run(_flow())
    assert a != b


def test_inflight_dedupe_is_tenant_scoped(isolated_settings, monkeypatch):
    """Two companies generating the same prd_id concurrently get distinct jobs —
    dedup keys on (company, prd_id), never collapses across tenants."""
    release = threading.Event()
    monkeypatch.setattr(
        stories, "generate_user_stories",
        lambda cid, prd_id=None, insight=None, **kw: (release.wait(2), [Story(title="T", body="b")])[1],
    )

    async def _flow():
        a = await stories.generate(stories.GenerateIn(prd_id=9), _ctx("ent-A"))
        b = await stories.generate(stories.GenerateIn(prd_id=9), _ctx("ent-B"))
        assert a["job_id"] != b["job_id"]
        release.set()
        await _drain(a["job_id"])
        await _drain(b["job_id"])

    asyncio.run(_flow())


def test_generate_requires_exactly_one_source(isolated_settings):
    async def _flow():
        with pytest.raises(Exception) as ei:
            await stories.generate(stories.GenerateIn(), _ctx())
        assert getattr(ei.value, "status_code", None) == 400
        with pytest.raises(Exception) as ei2:
            await stories.generate(stories.GenerateIn(prd_id=1, insight="x"), _ctx())
        assert getattr(ei2.value, "status_code", None) == 400
    asyncio.run(_flow())
