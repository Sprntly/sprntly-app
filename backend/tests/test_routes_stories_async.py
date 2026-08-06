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


def test_poll_surfaces_planned_stubs_before_any_batch(isolated_settings, monkeypatch):
    """The plan leg publishes the stub roster long before the first enrich batch
    — a poll in that window sees `stubs` + a zero-progress counter (skeleton
    rows), and the roster disappears once the run is ready."""
    gate = threading.Event()

    def _staged(cid, prd_id=None, insight=None, on_batch=None, on_plan=None, **kw):
        on_plan(
            [
                {"title": "A", "summary": "do A", "prd_section": "Part A §5 R1",
                 "ears_ids": ["E1"]},  # extra fields must not leak to the client
                {"title": "B"},
                {"title": ""},  # untitled model junk is dropped
            ],
            2,
        )
        gate.wait(2)  # hold in 'generating' so the test can observe the stubs
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
            if j["status"] == "generating" and j.get("stubs"):
                mid = j
                break
        assert mid is not None, "planned stubs never surfaced in the poll"
        assert mid["stubs"] == [
            {"title": "A", "summary": "do A", "prd_section": "Part A §5 R1"},
            {"title": "B", "summary": "", "prd_section": ""},
        ]
        assert mid["progress"] == {"done": 0, "total": 2}
        assert "stories" not in mid, "no full ticket exists yet"
        gate.set()
        await _drain(jid)
        return stories.get_job(jid, _ctx())

    final = asyncio.run(_flow())
    assert final["status"] == "ready"
    assert [s["title"] for s in final["stories"]] == ["A", "B"]
    assert "stubs" not in final, "the roster is gone once the real set exists"


def test_impl_spec_warm_deferred_until_generation_completes(
    isolated_settings, monkeypatch
):
    """The Part B pre-warm must NOT run alongside generation — it used to fire
    at kick time and compete with this very run's enrich shards for the LLM
    gate (93.6s slot waits observed on prod). It now runs after the job is
    ready, purely for the NEXT regenerate to inherit AC from."""
    warm_calls: list[int] = []

    async def _fake_warm(prd_id, ctx=None):
        warm_calls.append(prd_id)

    monkeypatch.setattr(stories, "warm_impl_spec", _fake_warm)
    gate = threading.Event()

    def _gen(cid, prd_id=None, insight=None, **kw):
        gate.wait(2)
        return [Story(title="A", body="b")]

    monkeypatch.setattr(stories, "generate_user_stories", _gen)

    async def _flow():
        resp = await stories.generate(stories.GenerateIn(prd_id=7), _ctx())
        jid = resp["job_id"]
        await asyncio.sleep(0.1)  # generation is parked on the gate
        assert warm_calls == [], "warm ran while generation was still in flight"
        gate.set()
        await _drain(jid)
        for _ in range(100):  # the warm task is scheduled after ready — let it run
            if warm_calls:
                break
            await asyncio.sleep(0.02)
        assert warm_calls == [7]
        return stories.get_job(jid, _ctx())

    final = asyncio.run(_flow())
    assert final["status"] == "ready"


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


async def _fake_warm(prd_id, ctx=None):
    """`stories.generate` schedules `warm_impl_spec` as its own fire-and-forget
    `asyncio.create_task` once a run with a `prd_id` completes — real
    `warm_impl_spec` runs `asyncio.to_thread(ensure_impl_spec, ...)`, a REAL OS
    thread that `asyncio.run()`'s task-cancellation cleanup cannot actually
    interrupt once it's mid-flight (cancelling the asyncio-level future does
    not stop an already-running executor thread). Left unstubbed, that thread
    can outlive `asyncio.run(_flow())` entirely and later touch the shared
    fake DB via `require_client()` on uncontrolled timing — the exact hazard
    `tests/_fake_supabase.py`'s module docstring names. Tests here that only
    care about the dedup/job-lifecycle behavior stub it out; the one test that
    exists to verify the warm's OWN deferred-timing contract
    (`test_impl_spec_warm_deferred_until_generation_completes`) uses its own
    local variant to assert on."""
    return None


def test_inflight_generate_dedupes_by_prd(isolated_settings, monkeypatch):
    """A rapid second /generate for the same PRD while the first is still
    running re-attaches to that job (same id, one LLM run) — this is the fix for
    the Tickets tab re-kicking generation on every remount/tab-switch."""
    calls = {"n": 0}
    release = threading.Event()
    monkeypatch.setattr(stories, "warm_impl_spec", _fake_warm)

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
    monkeypatch.setattr(stories, "warm_impl_spec", _fake_warm)
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
    monkeypatch.setattr(stories, "warm_impl_spec", _fake_warm)
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


# ── Standalone ticket sets (the insight path) ────────────────────────────────
#
# Tickets asked for in a chat with no PRD used to exist only as markdown in the
# reply bubble. The insight path now creates a `ticket_sets` row at KICK-OFF and
# returns its id, so the panel has something durable to open and poll.


def test_insight_run_creates_a_generating_set_and_returns_its_id(
    isolated_settings, monkeypatch
):
    from app.db.ticket_sets import get_set

    monkeypatch.setattr(
        stories, "generate_user_stories",
        lambda cid, prd_id=None, insight=None, **kw: [Story(title="T", body="b")],
    )

    async def _flow():
        resp = await stories.generate(
            stories.GenerateIn(insight="break this into tickets"), _ctx()
        )
        # The id is available IMMEDIATELY — before the multi-minute run lands.
        assert resp["ticket_set_id"]
        row = get_set("ent-A", resp["ticket_set_id"])
        assert row["status"] == "generating"
        assert row["source_text"] == "break this into tickets"
        await _drain(resp["job_id"])
        return resp["ticket_set_id"]

    sid = asyncio.run(_flow())
    assert get_set("ent-A", sid)["status"] == "ready"


def test_prd_run_creates_no_ticket_set(isolated_settings, monkeypatch):
    """The PRD path is untouched: no set row, and no `ticket_set_id` key that
    would make a client think one exists."""
    from app.db.ticket_sets import list_sets_for_company

    monkeypatch.setattr(stories, "warm_impl_spec", _fake_warm)
    monkeypatch.setattr(
        stories, "generate_user_stories",
        lambda cid, prd_id=None, insight=None, **kw: [Story(title="T", body="b")],
    )

    async def _flow():
        resp = await stories.generate(stories.GenerateIn(prd_id=7), _ctx())
        await _drain(resp["job_id"])
        return resp

    resp = asyncio.run(_flow())
    assert "ticket_set_id" not in resp
    assert list_sets_for_company("ent-A") == []


def test_a_failed_insight_run_marks_the_set_failed(isolated_settings, monkeypatch):
    """A set left 'generating' forever is a panel that spins on a run that
    already died — the one reopen outcome that must never happen."""
    from app.db.ticket_sets import get_set

    def _boom(cid, prd_id=None, insight=None, **kw):
        raise RuntimeError("provider exploded")

    monkeypatch.setattr(stories, "generate_user_stories", _boom)

    async def _flow():
        resp = await stories.generate(stories.GenerateIn(insight="x"), _ctx())
        await _drain(resp["job_id"])
        return resp["ticket_set_id"]

    sid = asyncio.run(_flow())
    row = get_set("ent-A", sid)
    assert row["status"] == "failed"
    assert "provider exploded" in (row["error"] or "")


def test_a_zero_ticket_run_settles_ready_with_no_tickets(
    isolated_settings, monkeypatch
):
    """Distinct from a failure: the run completed and produced nothing, which
    the panel renders as "no tickets came back — try again". Left generating,
    it would spin forever; marked failed, it would claim an error that did not
    happen."""
    from app.db.ticket_sets import get_set

    monkeypatch.setattr(
        stories, "generate_user_stories",
        lambda cid, prd_id=None, insight=None, **kw: [],
    )

    async def _flow():
        resp = await stories.generate(stories.GenerateIn(insight="x"), _ctx())
        await _drain(resp["job_id"])
        return resp["ticket_set_id"]

    sid = asyncio.run(_flow())
    row = get_set("ent-A", sid)
    assert row["status"] == "ready"
    assert list(row["stories"] or []) == []


def test_a_swallowed_persist_still_settles_the_set(isolated_settings, monkeypatch):
    """generate_user_stories owns the normal write and swallows its own
    exceptions so a persistence hiccup never loses a finished generation. The
    route's backstop is what stops that hiccup stranding the row."""
    from app.db.ticket_sets import get_set

    monkeypatch.setattr(
        stories, "generate_user_stories",
        # Returns stories but never persists them (what a swallowed write
        # looks like from the route's side).
        lambda cid, prd_id=None, insight=None, **kw: [Story(title="Recovered", body="b")],
    )

    async def _flow():
        resp = await stories.generate(stories.GenerateIn(insight="x"), _ctx())
        await _drain(resp["job_id"])
        return resp["ticket_set_id"]

    sid = asyncio.run(_flow())
    row = get_set("ent-A", sid)
    assert row["status"] == "ready"
    assert [s["title"] for s in row["stories"]] == ["Recovered"]
    # The fallback names the set after its first ticket rather than a generic
    # label every set in the library would share.
    assert row["title"] == "Recovered"


def test_a_foreign_conversation_id_404s_and_creates_nothing(
    isolated_settings, monkeypatch
):
    """conversation_id is client-supplied and ids are sequential, so ownership
    is proven before it is stamped onto an artifact — otherwise a caller could
    read a foreign chat's title back out of the artifacts listing."""
    from app.db.client import require_client
    from app.db.ticket_sets import list_sets_for_company

    conv = require_client().table("conversations").insert(
        {"company_id": "someone-else", "title": "Their private thread"}
    ).execute().data[0]["id"]

    async def _flow():
        with pytest.raises(Exception) as ei:
            await stories.generate(
                stories.GenerateIn(insight="x", conversation_id=conv), _ctx()
            )
        assert getattr(ei.value, "status_code", None) == 404

    asyncio.run(_flow())
    assert list_sets_for_company("ent-A") == []


def test_an_owned_conversation_id_is_stamped_on_the_set(isolated_settings, monkeypatch):
    from app.db.client import require_client
    from app.db.ticket_sets import get_set

    conv = require_client().table("conversations").insert(
        {"company_id": "ent-A", "title": "Checkout drop-off"}
    ).execute().data[0]["id"]
    monkeypatch.setattr(
        stories, "generate_user_stories",
        lambda cid, prd_id=None, insight=None, **kw: [Story(title="T", body="b")],
    )

    async def _flow():
        resp = await stories.generate(
            stories.GenerateIn(insight="x", conversation_id=conv), _ctx()
        )
        await _drain(resp["job_id"])
        return resp["ticket_set_id"]

    sid = asyncio.run(_flow())
    assert get_set("ent-A", sid)["conversation_id"] == conv


def test_reattaching_to_an_inflight_insight_run_reuses_the_same_set(
    isolated_settings, monkeypatch
):
    """A re-attach must hand back the SAME set the running job will fill — not
    a second row, and not a response with no set id at all."""
    from app.db.ticket_sets import list_sets_for_company

    release = threading.Event()
    monkeypatch.setattr(
        stories, "generate_user_stories",
        lambda cid, prd_id=None, insight=None, **kw: (
            release.wait(2), [Story(title="T", body="b")]
        )[1],
    )

    async def _flow():
        first = await stories.generate(stories.GenerateIn(insight="same"), _ctx())
        second = await stories.generate(stories.GenerateIn(insight="same"), _ctx())
        assert second["job_id"] == first["job_id"]
        assert second["ticket_set_id"] == first["ticket_set_id"]
        release.set()
        await _drain(first["job_id"])

    asyncio.run(_flow())
    assert len(list_sets_for_company("ent-A")) == 1
