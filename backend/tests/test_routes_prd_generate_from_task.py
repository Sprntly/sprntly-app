"""Tests for POST /v1/prd/generate-from-task — the chat "generate a PRD for
<specific need>" command.

The task text is the source: the route synthesizes an insight from the user's
own words (title derived from the text, summary carrying the full ask) and
feeds it to the standard prd-author pipeline via insight_override — the same
shape the ideation/import paths use. Rows are marked source='chat' with a
synthetic theme_id ('chat:<hash-of-normalized-task>') so re-issuing the same
ask finds the existing PRD instead of regenerating (find-or-create), and
version history groups per task. Anchors to the company's current brief when
one exists, else to the per-company uploads brief (new accounts).
"""
from __future__ import annotations

from app.db.client import require_client
from app.routes.prd import _chat_task_theme_id, _chat_task_title


def _seed_corpus(data_dir, dataset, body="some corpus body"):
    ds = data_dir / dataset
    ds.mkdir(exist_ok=True)
    (ds / "a.md").write_text(body)


def _save_current_brief(db_mod, dataset):
    payload = {
        "summary_headline": "stub",
        "insights": [{"title": "Brief insight 0", "theme_id": "brief-theme"}],
        "_schema_version": 1,
    }
    return db_mod.save_brief(
        dataset=dataset, week_label="Week of stub", payload=payload, schema_version=1
    )


def _prd_row(prd_id):
    return require_client().table("prds").select("*").eq("id", prd_id).execute().data[0]


# ── title / theme-id derivation (pure helpers) ───────────────────────────────

def test_chat_task_title_cleans_and_capitalizes():
    assert _chat_task_title("dark mode  on mobile.") == "Dark mode on mobile"
    assert _chat_task_title("  improve onboarding!?  ") == "Improve onboarding"


def test_chat_task_title_truncates_on_word_boundary():
    long = "a really long task description " * 10
    title = _chat_task_title(long)
    assert len(title) <= 91  # 90 + ellipsis
    assert title.endswith("…")
    assert " " not in title[-2:]  # no dangling space before the ellipsis


def test_chat_task_theme_id_normalizes_case_and_whitespace():
    a = _chat_task_theme_id("Dark   Mode on MOBILE")
    b = _chat_task_theme_id("dark mode on mobile")
    assert a == b
    assert a.startswith("chat:")
    assert _chat_task_theme_id("something else") != a


# ── POST /v1/prd/generate-from-task ──────────────────────────────────────────

def test_generate_from_task_happy_path(tenant_client, isolated_settings):
    t = tenant_client.make(slug="acme")
    _seed_corpus(isolated_settings["data_dir"], dataset="acme")
    _save_current_brief(isolated_settings["db"], dataset="acme")

    resp = t.client.post(
        "/v1/prd/generate-from-task", json={"task": "dark mode on mobile"}
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] in ("generating", "ready")
    assert body["title"] == "Dark mode on mobile"
    assert body["variant"] == "v3"

    # The row is discriminated as a chat-task PRD, keyed on the task hash.
    row = _prd_row(body["prd_id"])
    assert row["source"] == "chat"
    assert row["theme_id"] == _chat_task_theme_id("dark mode on mobile")


def test_generate_from_task_dedups_same_task(tenant_client, isolated_settings):
    t = tenant_client.make(slug="acme")
    db_mod = isolated_settings["db"]
    brief_id = _save_current_brief(db_mod, dataset="acme")

    existing = db_mod.start_prd(
        brief_id=brief_id, insight_index=0, title="Dark mode on mobile",
        template_version=1, variant="v3", source="chat",
        theme_id=_chat_task_theme_id("Dark Mode on Mobile"),
    )
    db_mod.complete_prd(existing, title="Dark mode on mobile", md="# Already here")

    # Same task, different casing/spacing → the existing PRD is returned.
    resp = t.client.post(
        "/v1/prd/generate-from-task", json={"task": "dark   mode on MOBILE"}
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["prd_id"] == existing
    assert body["status"] == "ready"


def test_generate_from_task_force_makes_new_row(tenant_client, isolated_settings):
    t = tenant_client.make(slug="acme")
    db_mod = isolated_settings["db"]
    brief_id = _save_current_brief(db_mod, dataset="acme")

    existing = db_mod.start_prd(
        brief_id=brief_id, insight_index=0, title="Dark mode on mobile",
        template_version=1, variant="v3", source="chat",
        theme_id=_chat_task_theme_id("dark mode on mobile"),
    )
    db_mod.complete_prd(existing, title="Dark mode on mobile", md="# Already here")

    resp = t.client.post(
        "/v1/prd/generate-from-task",
        json={"task": "dark mode on mobile", "force": True},
    )
    assert resp.status_code == 200
    assert resp.json()["prd_id"] != existing


def test_generate_from_task_without_brief_anchors_to_uploads_brief(
    tenant_client, isolated_settings
):
    """A company with NO current brief still gets a PRD — anchored to the
    per-company uploads brief, same as the PRD-import path."""
    t = tenant_client.make(slug="acme")
    _seed_corpus(isolated_settings["data_dir"], dataset="acme")

    resp = t.client.post(
        "/v1/prd/generate-from-task", json={"task": "improve onboarding"}
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] in ("generating", "ready")
    row = _prd_row(body["prd_id"])
    assert row["source"] == "chat"


def test_generate_from_task_rejects_short_task(tenant_client, isolated_settings):
    t = tenant_client.make(slug="acme")
    _save_current_brief(isolated_settings["db"], dataset="acme")
    resp = t.client.post("/v1/prd/generate-from-task", json={"task": "ab"})
    assert resp.status_code == 422


def test_generate_from_task_without_auth_returns_401(unauth_client, isolated_settings):
    resp = unauth_client.post(
        "/v1/prd/generate-from-task", json={"task": "dark mode"}
    )
    assert resp.status_code == 401


def test_generate_from_task_feeds_task_as_insight_override(
    tenant_client, isolated_settings, monkeypatch
):
    """The route hands the runner a synthetic insight built from the task text
    (title + 'Requested by the user in chat' summary) — the user's own words
    ground the generation, not a brief insight."""
    from app.routes import prd as prd_routes

    t = tenant_client.make(slug="acme")
    _save_current_brief(isolated_settings["db"], dataset="acme")

    captured: dict = {}

    # Capture SYNCHRONOUSLY at call time (the returned no-op coroutine is what
    # asyncio.create_task schedules) — the background task itself may not get a
    # loop slice before the response assertion runs.
    def _capture(prd_id, brief_id, insight_index, insight_override=None, **kw):
        captured.update(
            prd_id=prd_id, brief_id=brief_id, insight_index=insight_index,
            insight_override=insight_override,
        )

        async def _noop():
            return None

        return _noop()

    monkeypatch.setattr(prd_routes, "generate_prd_and_warm", _capture)

    resp = t.client.post(
        "/v1/prd/generate-from-task", json={"task": "dark mode on mobile"}
    )
    assert resp.status_code == 200

    override = captured["insight_override"]
    assert override["title"] == "Dark mode on mobile"
    assert "dark mode on mobile" in override["summary"]
    # `query` carries the raw ask — the parallel Evidence-artifact retrieval
    # keys on it (grounding itself uses the insight text via _kg_topic_bundle).
    assert override["query"] == "dark mode on mobile"
    # No synthetic theme_id inside the insight — 'chat:…' must never be walked
    # as a KG theme.
    assert "theme_id" not in override


# ── The parallel Evidence artifact (semantic KG retrieval over the task) ─────
# (PRD grounding itself — trail → topic retrieval → corpus — is covered by
# test_prd_kg.py's kg_topic tests.)

def _run(coro):
    import asyncio

    loop = asyncio.new_event_loop()
    try:
        return loop.run_until_complete(coro)
    finally:
        loop.close()


def test_generate_task_evidence_skips_without_kg_backing(
    tenant_client, isolated_settings, monkeypatch
):
    """No retrieval hits → NO evidences row at all (the Evidence tab stays
    hidden) — evidence is never corpus-invented for a chat task."""
    import app.graph.retrieval as retrieval
    from app import evidence_kg

    tenant_client.make(slug="acme")
    brief_id = _save_current_brief(isolated_settings["db"], dataset="acme")
    monkeypatch.setattr(retrieval, "task_evidence_trail", lambda f, e, t: None)

    insight = {"title": "Dark mode", "summary": "s", "query": "dark mode"}
    _run(evidence_kg.generate_task_evidence(brief_id, insight, "chat:abc123"))

    rows = (
        require_client().table("evidences").select("*")
        .eq("brief_id", brief_id).execute().data
    )
    assert rows == []


def test_generate_task_evidence_creates_doc_from_retrieved_signals(
    tenant_client, isolated_settings, monkeypatch
):
    """Retrieval hits → an evidences row keyed (brief_id, 'chat:…') is created
    and completed from the evidence-brief skill call, fed the retrieved trail."""
    import app.graph.retrieval as retrieval
    from app import evidence_kg
    from app.graph.gateway import LLMResult

    tenant_client.make(slug="acme")
    brief_id = _save_current_brief(isolated_settings["db"], dataset="acme")

    trail = {
        "insight": {"title": "dark mode"},
        "theme_id": None,
        "hypothesis": None,
        "signals": [{"signal_id": "s1", "content": "users ask for dark mode",
                     "kind": "feedback", "source_type": "zendesk",
                     "provenance": {"source": "ticket-1"}, "confidence": 0.9,
                     "rank": 1.0}],
        "kg_refs": ["s1"],
        "empty": False,
    }
    monkeypatch.setattr(retrieval, "task_evidence_trail", lambda f, e, t: trail)

    seen_inputs: list[str] = []

    def _fake_llm(**kwargs):
        seen_inputs.append(kwargs.get("input", ""))
        return LLMResult(
            output="<html><style></style><body>evidence</body></html>",
            model="claude-sonnet-4-6",
            prompt_version="x+evidence-brief@abc123",
            input_tokens=1, output_tokens=1, cache_read_input_tokens=0,
            cache_creation_input_tokens=0, cost_usd=0.0, latency_ms=1,
            stop_reason="end_turn",
        )

    monkeypatch.setattr(evidence_kg, "llm_call", _fake_llm)

    insight = {"title": "Dark mode", "summary": "s", "query": "dark mode"}
    _run(evidence_kg.generate_task_evidence(brief_id, insight, "chat:abc123"))

    rows = (
        require_client().table("evidences").select("*")
        .eq("brief_id", brief_id).execute().data
    )
    assert len(rows) == 1
    row = rows[0]
    assert row["theme_id"] == "chat:abc123"
    assert row["status"] == "ready"
    assert row["title"] == "Dark mode"
    # The retrieved signal grounded the skill call.
    assert seen_inputs and "users ask for dark mode" in seen_inputs[0]

    # Find-or-create: a second run reuses the doc instead of generating again.
    _run(evidence_kg.generate_task_evidence(brief_id, insight, "chat:abc123"))
    rows = (
        require_client().table("evidences").select("*")
        .eq("brief_id", brief_id).execute().data
    )
    assert len(rows) == 1


def test_get_prd_evidence_route(tenant_client, isolated_settings):
    """GET /v1/prd/{id}/evidence: 404 for non-chat PRDs and for chat PRDs whose
    evidence was skipped; the doc for chat PRDs that have one."""
    t = tenant_client.make(slug="acme")
    db_mod = isolated_settings["db"]
    brief_id = _save_current_brief(db_mod, dataset="acme")

    # Non-chat PRD → 404.
    brief_prd = db_mod.start_prd(
        brief_id=brief_id, insight_index=0, title="t", template_version=1,
        variant="v3",
    )
    assert t.client.get(f"/v1/prd/{brief_prd}/evidence").status_code == 404

    # Chat PRD without an evidence doc (retrieval skipped) → 404.
    theme = _chat_task_theme_id("dark mode")
    chat_prd = db_mod.start_prd(
        brief_id=brief_id, insight_index=0, title="Dark mode",
        template_version=1, variant="v3", source="chat", theme_id=theme,
    )
    assert t.client.get(f"/v1/prd/{chat_prd}/evidence").status_code == 404

    # With the doc → 200 + the row.
    from app.db.evidences import start_evidence, complete_evidence

    ev_id = start_evidence(
        brief_id=brief_id, insight_index=0, title="Dark mode",
        template_version=1, variant="v3", theme_id=theme,
    )
    complete_evidence(ev_id, title="Dark mode", md="<html>e</html>")
    resp = t.client.get(f"/v1/prd/{chat_prd}/evidence")
    assert resp.status_code == 200
    body = resp.json()
    assert body["id"] == ev_id
    assert body["status"] == "ready"
