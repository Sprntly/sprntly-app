"""Tests for the individual project chat: `/v1/ask` accepts an optional
`project_id`, folds project memory (+ the caller's job_role) into the
turn's context, and binds the conversation to the project — all additive
on top of the existing per-user `/v1/ask` path (AD-P2/AD-P8/AD-P11).

Omitted `project_id` MUST behave exactly as today (no project block,
`_load_history` untouched — that's the regression this file pins first).
Set, it must:
  - 404 on a cross-tenant project id (`project_belongs_to_company`)
  - 403 on a same-tenant NON-member (AD-P11 — the same IDOR class the
    other project membership gates close)
  - fold the project's memory summary + top-N entries + the caller's
    job_role into the prompt, bounded to a token budget
  - never touch the enterprise KG tables directly (`assemble_project_context`
    reads only `project_memory_*` + `profiles.role`)
  - leave the enterprise KG retrieval (`graph.retrieval.retrieve_context`)
    running exactly as it does today
  - degrade to no project block on any assembly failure (best-effort,
    AD-P7) — the answer must still be produced
  - best-effort bind the conversation to the project, first-write-wins
"""
from __future__ import annotations

from tests._company_helpers import company_client
from tests._project_helpers import seed_same_tenant_non_member


def _seed_corpus(data_dir, dataset, body="some corpus body"):
    ds = data_dir / dataset
    ds.mkdir(exist_ok=True)
    (ds / "a.md").write_text(body)


def _poll_ask(client, ask_id, *, timeout=5.0):
    import time

    deadline = time.monotonic() + timeout
    body = None
    while time.monotonic() < deadline:
        resp = client.get(f"/v1/ask/{ask_id}")
        assert resp.status_code == 200, resp.text
        body = resp.json()
        if body["status"] != "generating":
            return body
        time.sleep(0.02)
    return body


def _default_workspace_id(company_id: str) -> str:
    from app.db.workspaces import ensure_default_workspace

    return ensure_default_workspace(company_id)["id"]


def _create_project(t, *, name: str = "Test project") -> dict:
    from app.db import projects as projects_db

    ws_id = _default_workspace_id(t.company_id)
    return projects_db.create_project(
        company_id=t.company_id, workspace_id=ws_id, name=name, created_by=t.user_id,
    )


_STANDARD_PAYLOAD = {
    "answer": "ok", "key_points": [], "citations": [],
    "confidence": 0.9, "unanswered": "",
}


def _project_ctx(project_id: int, surface: str = "private") -> dict:
    """The current wire shape for a project-scoped ask. Project chats carry
    their project on `context_source`, NOT the removed top-level `project_id`
    field — see `app.routes.ask._project_source` (`{"kind": "project",
    "params": {"project_id", "surface"}}`). The individual project chat is the
    `"private"` surface."""
    return {"kind": "project", "params": {"project_id": project_id, "surface": surface}}


# ---- A1/A9 — no-project_id regression ---------------------------------------


def test_ask_without_project_id_unchanged(tenant_client, isolated_settings, fake_llm):
    """Omitted project_id: no project block folded in — the assembly is
    identical to today's."""
    t = tenant_client.make(slug="acme-no-project")
    _seed_corpus(isolated_settings["data_dir"], dataset="acme-no-project")
    fake_llm["payload"] = _STANDARD_PAYLOAD
    start = t.client.post(
        "/v1/ask",
        json={"question": "What is the biggest churn driver?", "dataset": "acme-no-project"},
    ).json()
    body = _poll_ask(t.client, start["ask_id"])
    assert body["status"] == "ready"
    assert len(fake_llm["calls"]) == 1
    prompt = fake_llm["calls"][0]["user"]
    assert "[Project context]" not in prompt


def test_load_history_unmodified():
    """`_load_history`'s signature and its per-user ownership scoping are
    untouched by this ticket — pinned directly against the live source
    rather than a historical SHA (unit tests never shell to git)."""
    import inspect

    from app.routes import ask as ask_route

    sig = inspect.signature(ask_route._load_history)
    assert list(sig.parameters.keys()) == ["conversation_id", "company_id", "user_id"]

    source = inspect.getsource(ask_route._load_history)
    assert '.eq("company_id", company_id)' in source
    assert '.eq("user_id", user_id)' in source


# ---- A2/A3/A4/A5 — context fold-in ------------------------------------------


def test_ask_with_project_folds_memory_and_role(tenant_client, isolated_settings, fake_llm):
    t = tenant_client.make(slug="acme-project-fold")
    _seed_corpus(isolated_settings["data_dir"], dataset="acme-project-fold")
    project = _create_project(t)

    from app.db.client import require_client
    from app.db.project_memory_entries import add_entry

    require_client().table("profiles").update({"role": "Product Manager"}).eq(
        "id", t.user_id
    ).execute()
    require_client().table("project_memory_summary").insert(
        {
            "project_id": project["id"],
            "summary_md": "This project tracks the Q3 onboarding launch.",
            "entry_count": 1,
            "stale": False,
        }
    ).execute()
    add_entry(project["id"], body="Ship by Friday — no exceptions.", author_user_id=t.user_id)

    fake_llm["payload"] = _STANDARD_PAYLOAD
    # A plain-context question (NOT project-content/tool-shaped): the unified
    # engine's gate declines it, so it folds project context via the composer
    # fall-through (`_fold_project_context`) — the path that carries the
    # AUTHORITATIVE preamble. A content-shaped question ("what should I know
    # about this project?") instead takes the scoped tool path, where the same
    # facts ride the SYSTEM prompt without the preamble (covered by the
    # `context_source` gates below).
    start = t.client.post(
        "/v1/ask",
        json={
            "question": "Give me your honest take on where we are.",
            "dataset": "acme-project-fold",
            "context_source": _project_ctx(project["id"]),
        },
    ).json()
    body = _poll_ask(t.client, start["ask_id"])
    assert body["status"] == "ready"
    assert len(fake_llm["calls"]) == 1
    prompt = fake_llm["calls"][0]["user"]
    # The private project chat folds an AUTHORITATIVE project-facts block
    # (the same breadth the @Sprntly group agent gets) rather than the older
    # passive "[Project context]" header — the framing tells the model these
    # lines are the source of truth and NOT to deflect to "connect a connector".
    assert "AUTHORITATIVE for THIS project" in prompt
    assert "This project tracks the Q3 onboarding launch." in prompt
    assert "Ship by Friday — no exceptions." in prompt
    assert "Product Manager" in prompt


# ---- AC10 — single-sourced preamble, byte-identical for the private surface -


def test_shared_preamble_equals_prior_ask_literal(tenant_client, isolated_settings, fake_llm):
    """`PROJECT_FACTS_AUTHORITATIVE_PREAMBLE` (`app.surface_scope`) is the
    EXACT string that used to be inlined at `routes/ask.py`, and the folded
    `{"role":"context", ...}` row `routes/ask.py` builds is byte-identical
    to before (`PREAMBLE\\nblock`, single newline) — the extraction changes
    where the string lives, never what reaches the model."""
    from app.surface_scope import PROJECT_FACTS_AUTHORITATIVE_PREAMBLE

    assert PROJECT_FACTS_AUTHORITATIVE_PREAMBLE == (
        "[Project workspace facts — AUTHORITATIVE for THIS project, and "
        "the source of truth for anything about the project itself. The "
        "lines below are the real members (and their roles), the real "
        "task/delegation ledger, and the real artifacts (PRDs, "
        "prototypes, evidence, reports, ticket sets) of the project this "
        "chat belongs to. When asked who is on this project, what tasks "
        "are open / who is doing what, or how many / which PRDs or "
        "artifacts exist, answer directly and specifically from these "
        "facts. Do NOT say you cannot see them and do NOT tell the user "
        "to connect a data source for them — this block IS that source.]"
    )

    t = tenant_client.make(slug="acme-preamble-literal")
    _seed_corpus(isolated_settings["data_dir"], dataset="acme-preamble-literal")
    project = _create_project(t)

    fake_llm["payload"] = _STANDARD_PAYLOAD
    # Plain-context question -> composer fall-through, the path that folds the
    # AUTHORITATIVE preamble into the turn (see `test_ask_with_project_folds`).
    start = t.client.post(
        "/v1/ask",
        json={
            "question": "Give me your honest take on where we are.",
            "dataset": "acme-preamble-literal",
            "context_source": _project_ctx(project["id"]),
        },
    ).json()
    body = _poll_ask(t.client, start["ask_id"])
    assert body["status"] == "ready"
    prompt = fake_llm["calls"][0]["user"]
    # The preamble is bound to the project block with a SINGLE newline, not
    # a blank line — exactly `routes/ask.py`'s pre-extraction literal join.
    assert f"{PROJECT_FACTS_AUTHORITATIVE_PREAMBLE}\n" in prompt


def test_assemble_context_no_kg_tables(tenant_client, isolated_settings):
    """AC3 — `assemble_project_context` issues no query against
    kg_entity/kg_signal/kg_relationship (query spy on the real fake-DB
    client's `.table()`)."""
    t = tenant_client.make(slug="acme-context-spy")
    project = _create_project(t)

    from app.db.client import require_client
    from app.db.project_memory_entries import add_entry

    add_entry(project["id"], body="A durable guardrail.", author_user_id=t.user_id)

    client = require_client()
    seen_tables: list[str] = []
    original_table = client.table

    def _spy_table(name, *a, **kw):
        seen_tables.append(name)
        return original_table(name, *a, **kw)

    client.table = _spy_table
    try:
        from app.project_context import assemble_project_context

        block = assemble_project_context(project["id"], t.user_id)
    finally:
        client.table = original_table

    assert block  # the assembly actually read something
    assert seen_tables, "expected at least one table read"
    assert not any(
        name in {"kg_entity", "kg_signal", "kg_relationship"} for name in seen_tables
    ), f"assemble_project_context queried a KG table: {seen_tables}"


def test_enterprise_kg_still_runs(tenant_client, isolated_settings, fake_llm, monkeypatch):
    """AC4 — the enterprise KG retrieval (`graph.retrieval.retrieve_context`)
    still runs for the company scope on a project-scoped ask; project
    context is ADDITIONAL, never a replacement."""
    t = tenant_client.make(slug="acme-kg-still-runs")
    _seed_corpus(isolated_settings["data_dir"], dataset="acme-kg-still-runs")
    project = _create_project(t)

    from app.graph import retrieval as retrieval_mod

    calls: list = []
    original = retrieval_mod.retrieve_context

    def _spy(*a, **k):
        calls.append(1)
        return original(*a, **k)

    monkeypatch.setattr(retrieval_mod, "retrieve_context", _spy)

    fake_llm["payload"] = _STANDARD_PAYLOAD
    start = t.client.post(
        "/v1/ask",
        json={
            "question": "What is the biggest churn driver?",
            "dataset": "acme-kg-still-runs",
            "context_source": _project_ctx(project["id"]),
        },
    ).json()
    body = _poll_ask(t.client, start["ask_id"])
    assert body["status"] == "ready"
    assert calls, "retrieve_context must still run for a project-scoped ask"


def test_context_token_budget_capped(tenant_client, isolated_settings):
    """AC5 — an over-budget entry set is truncated, not dropped whole-cloth
    into the prompt."""
    t = tenant_client.make(slug="acme-budget-cap")
    project = _create_project(t)

    from app.db.project_memory_entries import add_entry

    for i in range(30):
        add_entry(
            project["id"],
            body=f"Guardrail number {i}: " + ("detail " * 5),
            author_user_id=t.user_id,
        )

    from app.project_context import assemble_project_context

    small_budget = 100  # tokens — enough for a handful of entries, not all 30
    block = assemble_project_context(project["id"], t.user_id, token_budget=small_budget)

    assert block
    assert block.count("Guardrail number") < 30
    assert len(block) < 3000  # nowhere near the full unbounded entry set


# ---- A6 — cross-tenant / A11 membership isolation ---------------------------


def test_ask_foreign_project_id_returns_404(tenant_client, isolated_settings):
    a = tenant_client.make(slug="company-a-proj-ask")
    project = _create_project(a)
    b = tenant_client.make(slug="company-b-proj-ask")
    _seed_corpus(isolated_settings["data_dir"], dataset="company-b-proj-ask")

    resp = b.client.post(
        "/v1/ask",
        json={
            "question": "What does this project know?",
            "dataset": "company-b-proj-ask",
            "context_source": _project_ctx(project["id"]),
        },
    )
    assert resp.status_code == 404


def test_ask_same_tenant_non_member_returns_403(isolated_settings, monkeypatch):
    """AD-P11 — a real second account in the SAME company/workspace, never
    added to the project, must be blocked even though it resolves
    `require_workspace` successfully (it is NOT a foreign tenant — that
    case is `test_ask_foreign_project_id_returns_404` above). Uses
    `company_client` (not `tenant_client`) because `seed_same_tenant_non_member`
    mints its bearer against `company_client`'s JWT secret."""
    ctx = company_client(monkeypatch)
    project = ctx.client.post("/v1/projects", json={"name": "Non-member gate"}).json()
    _, non_member_headers = seed_same_tenant_non_member(ctx)

    resp = ctx.client.post(
        "/v1/ask",
        json={
            "question": "What does this project know?",
            "dataset": "acme",
            "context_source": _project_ctx(project["id"]),
        },
        headers=non_member_headers,
    )
    assert resp.status_code == 403


# ---- A7/A8 — best-effort failure + empty project -----------------------------


def test_ask_project_context_failure_is_best_effort(
    tenant_client, isolated_settings, fake_llm, monkeypatch
):
    """AC7 — `assemble_project_context` raising must not block the answer;
    the ask still completes, without a project block."""
    t = tenant_client.make(slug="acme-context-fail")
    _seed_corpus(isolated_settings["data_dir"], dataset="acme-context-fail")
    project = _create_project(t)

    import app.project_context as project_context_mod

    def _boom(*a, **k):
        raise RuntimeError("assembly blew up")

    monkeypatch.setattr(project_context_mod, "assemble_project_context", _boom)

    fake_llm["payload"] = {**_STANDARD_PAYLOAD, "answer": "still answered"}
    start = t.client.post(
        "/v1/ask",
        json={
            "question": "What should I know?",
            "dataset": "acme-context-fail",
            "context_source": _project_ctx(project["id"]),
        },
    ).json()
    body = _poll_ask(t.client, start["ask_id"])
    assert body["status"] == "ready"
    assert body["answer"] == "still answered"
    prompt = fake_llm["calls"][0]["user"]
    assert "[Project context]" not in prompt


def test_ask_empty_project_yields_no_block(tenant_client, isolated_settings, fake_llm):
    """AC7 — a project with no memory yet (and no job_role set) folds no
    block; the answer is still produced."""
    t = tenant_client.make(slug="acme-empty-project")
    _seed_corpus(isolated_settings["data_dir"], dataset="acme-empty-project")
    project = _create_project(t)

    fake_llm["payload"] = _STANDARD_PAYLOAD
    start = t.client.post(
        "/v1/ask",
        json={
            "question": "What should I know?",
            "dataset": "acme-empty-project",
            "context_source": _project_ctx(project["id"]),
        },
    ).json()
    body = _poll_ask(t.client, start["ask_id"])
    assert body["status"] == "ready"
    prompt = fake_llm["calls"][0]["user"]
    assert "[Project context]" not in prompt


# ---- A9 — conversation binding (A10 cost-log test removed, see note below) ----


def test_conversation_bound_to_project_first_write_wins(
    tenant_client, isolated_settings, fake_llm
):
    t = tenant_client.make(slug="acme-bind-project")
    _seed_corpus(isolated_settings["data_dir"], dataset="acme-bind-project")
    project = _create_project(t, name="First project")
    other_project = _create_project(t, name="Second project")

    conv = t.client.post("/v1/conversations", json={"title": "c"}).json()
    conv_id = conv["id"]

    fake_llm["payload"] = _STANDARD_PAYLOAD
    start = t.client.post(
        "/v1/ask",
        json={
            "question": "What should I know?",
            "dataset": "acme-bind-project",
            "context_source": _project_ctx(project["id"]),
            "conversation_id": conv_id,
        },
    ).json()
    _poll_ask(t.client, start["ask_id"])

    from app.db.client import require_client

    row = (
        require_client()
        .table("conversations")
        .select("project_id")
        .eq("id", conv_id)
        .execute()
        .data[0]
    )
    assert row["project_id"] == project["id"]

    # A re-request against the SAME conversation but a DIFFERENT project
    # must not error, and must not repoint the binding — first-write-wins.
    start2 = t.client.post(
        "/v1/ask",
        json={
            "question": "A follow-up question.",
            "dataset": "acme-bind-project",
            "context_source": _project_ctx(other_project["id"]),
            "conversation_id": conv_id,
        },
    ).json()
    body2 = _poll_ask(t.client, start2["ask_id"])
    assert body2["status"] == "ready"

    row2 = (
        require_client()
        .table("conversations")
        .select("project_id")
        .eq("id", conv_id)
        .execute()
        .data[0]
    )
    assert row2["project_id"] == project["id"]


# NOTE (removed): `test_ask_cost_log_includes_project_id` (AC A10) pinned a
# `app.routes.ask` INFO cost-log line that carried `project_id=<id>`. The
# async ask-job rewrite removed that route-level log: cost/analytics now go
# through `app.db.asks.log_ask(question, answer, citations)` — which carries NO
# project_id — and a project-scoped ask persists `project_id` on the `ask_jobs`
# row instead (via the legacy top-level `project_id` channel, not
# `context_source`). There is no current route-level log to retarget the
# assertion to, and it is not a safety invariant, so the test was deleted
# rather than rewritten to assert a mechanism that no longer exists.
