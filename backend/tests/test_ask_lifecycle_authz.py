"""Ask-lifecycle authorization at the `/v1/ask` route — Parts 1 + 3.

These two gates are the project-chat feature's OWN authorization logic, hoisted
to run SYNCHRONOUSLY at the route BEFORE any job is spawned or tokens spent, and
every check FAILS CLOSED.

Part 3 — synchronous project-membership gate:
    A project-scoped ask (`context_source.kind == "project"`) is membership-
    gated AT THE ROUTE for BOTH surfaces: a cross-tenant project 404s, a
    same-tenant NON-member 403s — BEFORE the job spawns, so a non-member burns
    no ask-planner tokens and never sees a raw `403` string from a failed
    background job.

Part 1 — group 2-mode gate, pre-generation + fail-closed:
    In a project GROUP chat: solo (1 human) → Sprntly always replies; multi-
    human (≥2) → Sprntly is silent UNLESS the turn `@Sprntly`-mentions it. The
    gate runs BEFORE generation: a suppressed ask does NO LLM work, stores NO
    readable answer (terminal `cancelled`, empty response), and spawns no
    worker. Member-count failure FAILS CLOSED (treated as multi-human →
    suppress).

The route-gate decision is observed two ways, both deterministic (no real LLM):
  * `fake_llm["calls"]` — proves NO model call happened on a denial/suppression
    (the timing proof: the gate ran BEFORE generation).
  * a stubbed `run_ask_job` recorder — proves whether the worker was spawned at
    all (suppressed/denied → not spawned; admitted → spawned).
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


def _group_source(project_id: int, surface: str = "group") -> dict:
    return {"kind": "project", "params": {"project_id": project_id, "surface": surface}}


@pytest.fixture
def worker_spy(monkeypatch):
    """Stub `run_ask_job` (awaited inline under pytest) with an async recorder,
    so a test can prove whether the route ADMITTED an ask to generation without
    running the heavy group generation path. Records each call's kwargs."""
    calls: list[dict] = []

    async def _spy(*args, **kwargs):  # noqa: ANN002, ANN003
        calls.append(kwargs or {"_args": args})

    monkeypatch.setattr(ask_route, "run_ask_job", _spy)
    return calls


# ─────────────────────── Part 3 — synchronous membership gate ───────────────


def test_group_ask_non_member_403_before_job(
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
            "context_source": _group_source(project_id),
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
    """Part 3 applies to the private surface too — a non-member is 403'd at the
    route before any generation, same as the group surface."""
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
            "context_source": _group_source(project_id, surface="private"),
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
            "context_source": _group_source(a_project["id"]),
        },
    )
    assert resp.status_code == 404, resp.text
    assert worker_spy == []
    assert fake_llm["calls"] == []


def test_member_group_ask_admitted(
    tenant_client, isolated_settings, fake_llm, worker_spy
):
    """A member's solo group ask is ADMITTED past both gates — the worker is
    spawned (gate did NOT suppress). (Solo project: creator is the only human.)"""
    t = tenant_client.make(slug="acme-mem")
    _seed_corpus(isolated_settings["data_dir"], dataset="acme-mem")
    project = t.client.post("/v1/projects", json={"name": "Solo project"}).json()
    resp = t.client.post(
        "/v1/ask",
        json={
            "question": "what's next?",
            "dataset": "acme-mem",
            "context_source": _group_source(project["id"]),
        },
    )
    assert resp.status_code == 200, resp.text
    assert len(worker_spy) == 1                   # admitted → worker spawned
    assert resp.json()["status"] != "cancelled"   # not suppressed


# ─────────────────────── Part 1 — group 2-mode gate ─────────────────────────


def _make_group_project(tenant_client, isolated_settings, slug, *, multi_human):
    t = tenant_client.make(slug=slug)
    _seed_corpus(isolated_settings["data_dir"], dataset=slug)
    project = t.client.post("/v1/projects", json={"name": slug}).json()
    project_id = project["id"]
    if multi_human:
        from app.db import projects as projects_db

        projects_db.add_member(project_id, "second-human")
    return t, project_id


def test_multi_human_untagged_suppressed_before_generation(
    tenant_client, isolated_settings, fake_llm, worker_spy
):
    """MULTI-human (≥2) + NO @Sprntly mention → SUPPRESSED before generation:
    terminal `cancelled`, NO worker spawned, NO LLM call, empty response (the
    sender reads nothing)."""
    from app.db.client import require_client

    t, project_id = _make_group_project(
        tenant_client, isolated_settings, "grp-multi-untagged", multi_human=True
    )
    resp = t.client.post(
        "/v1/ask",
        json={
            "question": "just chatting with the team here",
            "dataset": "grp-multi-untagged",
            "context_source": _group_source(project_id),
        },
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["status"] == "cancelled"          # terminal, no-answer state
    assert worker_spy == []                        # generation never spawned
    assert fake_llm["calls"] == []                 # no token spend (pre-gen gate)

    # The suppressed job row is terminal with an EMPTY response — nothing the
    # sender's GET can read as a Sprntly reply.
    rows = [r for r in _ask_jobs(require_client) if r["id"] == body["ask_id"]]
    assert len(rows) == 1
    assert rows[0]["status"] == "cancelled"
    assert (rows[0].get("response") or {}) in ({}, "{}")


def test_multi_human_tagged_admitted(
    tenant_client, isolated_settings, fake_llm, worker_spy
):
    """MULTI-human + an @Sprntly mention → ADMITTED (worker spawned)."""
    t, project_id = _make_group_project(
        tenant_client, isolated_settings, "grp-multi-tagged", multi_human=True
    )
    resp = t.client.post(
        "/v1/ask",
        json={
            "question": "@Sprntly can you summarise where we are?",
            "dataset": "grp-multi-tagged",
            "context_source": _group_source(project_id),
        },
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["status"] != "cancelled"
    assert len(worker_spy) == 1                     # admitted → generation ran


def test_solo_untagged_admitted(
    tenant_client, isolated_settings, fake_llm, worker_spy
):
    """SOLO project (1 human) + NO mention → ALWAYS replies (admitted)."""
    t, project_id = _make_group_project(
        tenant_client, isolated_settings, "grp-solo", multi_human=False
    )
    resp = t.client.post(
        "/v1/ask",
        json={
            "question": "no mention here at all",
            "dataset": "grp-solo",
            "context_source": _group_source(project_id),
        },
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["status"] != "cancelled"
    assert len(worker_spy) == 1


def test_member_count_failure_fails_closed(
    tenant_client, isolated_settings, fake_llm, worker_spy, monkeypatch
):
    """FAIL CLOSED: if the member count can't be established, the untagged group
    ask is treated as multi-human and SUPPRESSED — even for a solo project — so
    a count hiccup never lets the agent interject into a shared thread."""
    t, project_id = _make_group_project(
        tenant_client, isolated_settings, "grp-count-fail", multi_human=False
    )

    from app.db import projects as projects_db

    def _boom(_project_id):
        raise RuntimeError("count read failed (simulated DNS blip)")

    monkeypatch.setattr(projects_db, "count_project_members", _boom)

    resp = t.client.post(
        "/v1/ask",
        json={
            "question": "anyone around to help",
            "dataset": "grp-count-fail",
            "context_source": _group_source(project_id),
        },
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["status"] == "cancelled"    # fail-closed → suppressed
    assert worker_spy == []
    assert fake_llm["calls"] == []


# ─────────────────────── Unit-level gate helpers (no DB) ────────────────────


def test_mentions_agent_word_boundary():
    assert ask_route._mentions_agent("@Sprntly help me") is True
    assert ask_route._mentions_agent("hey @sprntly") is True
    assert ask_route._mentions_agent("@sprntlybot is a different handle") is False
    assert ask_route._mentions_agent("no mention here") is False
    assert ask_route._mentions_agent(None) is False


def test_project_source_extraction():
    from app.routes.ask import AskIn, _project_source

    group = AskIn(
        question="hi there",
        dataset="d",
        context_source={"kind": "project", "params": {"project_id": 7, "surface": "group"}},
    )
    assert _project_source(group) == (7, {"project_id": 7, "surface": "group"})

    # Non-project context_source → None (main path untouched).
    non_project = AskIn(question="hi there", dataset="d", context_source=None)
    assert _project_source(non_project) is None

    # kind=project but no project_id in params → None (behaves as main).
    no_id = AskIn(
        question="hi there", dataset="d",
        context_source={"kind": "project", "params": {"surface": "group"}},
    )
    assert _project_source(no_id) is None


def test_group_is_multi_human_fails_closed(monkeypatch):
    from app.db import projects as projects_db
    from app.routes.ask import _group_is_multi_human

    monkeypatch.setattr(projects_db, "count_project_members", lambda _pid: 2)
    assert _group_is_multi_human(1) is True

    monkeypatch.setattr(projects_db, "count_project_members", lambda _pid: 1)
    assert _group_is_multi_human(1) is False

    def _raise(_pid):
        raise RuntimeError("simulated read failure")

    monkeypatch.setattr(projects_db, "count_project_members", _raise)
    assert _group_is_multi_human(1) is True  # fail CLOSED → multi-human
