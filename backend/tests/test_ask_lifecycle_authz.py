"""Ask-lifecycle authorization at the `/v1/ask` route.

The project-chat feature's OWN authorization logic, hoisted to run
SYNCHRONOUSLY at the route BEFORE any job is spawned or tokens spent, and
FAILS CLOSED: a project-scoped ask (`context_source.kind == "project"`) is
membership-gated AT THE ROUTE — a cross-tenant project 404s, a same-tenant
NON-member 403s — BEFORE the job spawns, so a non-member burns no
ask-planner tokens and never sees a raw `403` string from a failed
background job.

The route-gate decision is observed two ways, both deterministic (no real LLM):
  * `fake_llm["calls"]` — proves NO model call happened on a denial
    (the timing proof: the gate ran BEFORE generation).
  * a stubbed `run_ask_job` recorder — proves whether the worker was spawned at
    all (denied → not spawned; admitted → spawned).
"""
from __future__ import annotations

import pytest

from app.routes import ask as ask_route
from tests._project_helpers import seed_same_tenant_non_member


def _seed_corpus(data_dir, dataset, body="some corpus body"):
    ds = data_dir / dataset
    ds.mkdir(exist_ok=True)
    (ds / "a.md").write_text(body)


def _ask_jobs(require_client):
    return require_client().table("ask_jobs").select("*").execute().data or []


def _project_source(project_id: int, surface: str = "private") -> dict:
    return {"kind": "project", "params": {"project_id": project_id, "surface": surface}}


@pytest.fixture
def worker_spy(monkeypatch):
    """Stub `run_ask_job` (awaited inline under pytest) with an async recorder,
    so a test can prove whether the route ADMITTED an ask to generation
    without running the heavy generation path. Records each call's kwargs."""
    calls: list[dict] = []

    async def _spy(*args, **kwargs):  # noqa: ANN002, ANN003
        calls.append(kwargs or {"_args": args})

    monkeypatch.setattr(ask_route, "run_ask_job", _spy)
    return calls


# ─────────────────────── Synchronous membership gate ─────────────────────────


def test_project_ask_non_member_403_before_job(
    tenant_client, isolated_settings, fake_llm, worker_spy
):
    """A same-tenant NON-member's project ask 403s at the route — NO job row
    created, NO worker spawned, NO LLM call (no token spend)."""
    from app.db.client import require_client

    t = tenant_client.make(slug="acme-nm")
    _seed_corpus(isolated_settings["data_dir"], dataset="acme-nm")
    project = t.client.post("/v1/projects", json={"name": "Members only"}).json()
    project_id = project["id"]

    # Seed a real same-tenant, non-project-member user, then mint the bearer
    # with tenant_client's own token minter (the helper's bearer is signed with
    # a different secret than this harness validates).
    nm_user, _ = seed_same_tenant_non_member(t)
    headers = tenant_client.bearer(nm_user)
    before = len(_ask_jobs(require_client))
    resp = t.client.post(
        "/v1/ask",
        json={
            "question": "what's the plan?",
            "dataset": "acme-nm",
            "context_source": _project_source(project_id),
        },
        headers=headers,
    )
    assert resp.status_code == 403, resp.text
    assert worker_spy == []                      # worker never spawned
    assert fake_llm["calls"] == []               # no token spend
    assert len(_ask_jobs(require_client)) == before  # no job row created


def test_private_ask_non_member_403_before_job(
    tenant_client, isolated_settings, fake_llm, worker_spy
):
    """The membership gate applies to the private surface — a non-member is
    403'd at the route before any generation."""
    t = tenant_client.make(slug="acme-nm2")
    _seed_corpus(isolated_settings["data_dir"], dataset="acme-nm2")
    project = t.client.post("/v1/projects", json={"name": "Private members only"}).json()
    project_id = project["id"]

    nm_user, _ = seed_same_tenant_non_member(t)
    headers = tenant_client.bearer(nm_user)
    resp = t.client.post(
        "/v1/ask",
        json={
            "question": "catch me up",
            "dataset": "acme-nm2",
            "context_source": _project_source(project_id, surface="private"),
        },
        headers=headers,
    )
    assert resp.status_code == 403, resp.text
    assert worker_spy == []
    assert fake_llm["calls"] == []


def test_cross_tenant_project_ask_404(
    tenant_client, isolated_settings, fake_llm, worker_spy
):
    """A project id from ANOTHER tenant 404s (non-disclosure), never 403 — the
    caller can't tell 'exists but not yours' from 'doesn't exist'."""
    a = tenant_client.make(slug="tenant-a")
    _seed_corpus(isolated_settings["data_dir"], dataset="tenant-a")
    a_project = a.client.post("/v1/projects", json={"name": "A's project"}).json()

    b = tenant_client.make(slug="tenant-b")
    _seed_corpus(isolated_settings["data_dir"], dataset="tenant-b")
    resp = b.client.post(
        "/v1/ask",
        json={
            "question": "leak A?",
            "dataset": "tenant-b",
            "context_source": _project_source(a_project["id"]),
        },
    )
    assert resp.status_code == 404, resp.text
    assert worker_spy == []
    assert fake_llm["calls"] == []


def test_member_project_ask_admitted(
    tenant_client, isolated_settings, fake_llm, worker_spy
):
    """A member's project ask is ADMITTED past the membership gate — the
    worker is spawned."""
    t = tenant_client.make(slug="acme-mem")
    _seed_corpus(isolated_settings["data_dir"], dataset="acme-mem")
    project = t.client.post("/v1/projects", json={"name": "Solo project"}).json()
    resp = t.client.post(
        "/v1/ask",
        json={
            "question": "what's next?",
            "dataset": "acme-mem",
            "context_source": _project_source(project["id"]),
        },
    )
    assert resp.status_code == 200, resp.text
    assert len(worker_spy) == 1                   # admitted → worker spawned
    assert resp.json()["status"] != "cancelled"   # not suppressed


# ─────────────────────── Unit-level gate helpers (no DB) ────────────────────


def test_project_source_extraction():
    from app.routes.ask import AskIn, _project_source

    project = AskIn(
        question="hi there",
        dataset="d",
        context_source={"kind": "project", "params": {"project_id": 7, "surface": "private"}},
    )
    assert _project_source(project) == (7, {"project_id": 7, "surface": "private"})

    # Non-project context_source → None (main path untouched).
    non_project = AskIn(question="hi there", dataset="d", context_source=None)
    assert _project_source(non_project) is None

    # kind=project but no project_id in params → None (behaves as main).
    no_id = AskIn(
        question="hi there", dataset="d",
        context_source={"kind": "project", "params": {"surface": "private"}},
    )
    assert _project_source(no_id) is None


