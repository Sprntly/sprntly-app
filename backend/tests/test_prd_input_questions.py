"""Tests for the PRD "User input needed" → structured-question feature.

Covers three layers:
  - db.prd_input_questions helpers (replace / list / get / answer round-trips,
    delete-then-insert idempotency, need→no-options normalization, blank skip).
  - prd_questions.extract_input_questions (persists the mocked extraction; is
    best-effort — a gateway error yields [] and never raises) and apply_answer
    (returns the scoped edit; strips a stray code fence; RuntimeError on empty).
  - the routes GET /v1/prd/{id}/input-questions and
    POST /v1/prd/{id}/input-questions/{qid}/answer (scoped edit is applied,
    a version snapshot is taken, the question flips to answered, tenant-gated).

All LLM work is mocked at the gateway seam (app.prd_questions.llm_call /
apply_answer). The fake-Supabase schema (conftest _FAKE_SCHEMA) seeds
prd_input_questions so these run with no live Postgres.
"""
from __future__ import annotations

import app.prd_questions as prd_questions
from app.graph.gateway import LLMResult


def _llm_result(output):
    return LLMResult(
        output=output, model="claude-sonnet-4-6", prompt_version="v",
        input_tokens=10, output_tokens=5, cache_read_input_tokens=0,
        cache_creation_input_tokens=0, cost_usd=0.001, latency_ms=5,
        stop_reason="end_turn",
    )


def _seed_prd(db_mod, dataset="acme", html="<html><body>PRD</body></html>"):
    payload = {"summary_headline": "s", "insights": [{"title": "A"}], "_schema_version": 1}
    brief_id = db_mod.save_brief(
        dataset=dataset, week_label="w", payload=payload, schema_version=1
    )
    prd_id = db_mod.start_prd(
        brief_id=brief_id, insight_index=0, title="t", template_version=1, variant="v2",
    )
    db_mod.complete_prd(prd_id, title="t", md=html)
    return brief_id, prd_id


# ── db helpers ───────────────────────────────────────────────────────────────

def test_replace_and_list_round_trip(isolated_settings):
    import app.db.prd_input_questions as q
    _, prd_id = _seed_prd(isolated_settings["db"])
    q.replace_questions(prd_id, [
        {"tag": "escalate", "prompt": "Reminders on by default?", "owner": "PM",
         "options": [{"label": "On"}, {"label": "Off", "description": "less noise"}]},
        {"tag": "need", "prompt": "Manual follow-up rate today?", "owner": "Data",
         "options": [{"label": "0–20%"}, {"label": "20–50%"}, {"label": ">50%"}]},
    ])
    rows = q.list_questions(prd_id)
    assert [r["ordinal"] for r in rows] == [0, 1]
    assert rows[0]["tag"] == "escalate"
    assert rows[0]["prompt"] == "Reminders on by default?"
    assert [o["label"] for o in rows[0]["options"]] == ["On", "Off"]
    # A NEED item now also carries selectable options (candidate values/ranges);
    # they are preserved through persistence, not stripped.
    assert rows[1]["tag"] == "need"
    assert [o["label"] for o in rows[1]["options"]] == ["0–20%", "20–50%", ">50%"]
    assert all(r["status"] == "pending" for r in rows)


def test_need_without_options_stays_free_text(isolated_settings):
    # A NEED item whose answer is inherently free-form (no candidate set) keeps an
    # empty options list → the UI renders a plain text box, not buttons.
    import app.db.prd_input_questions as q
    _, prd_id = _seed_prd(isolated_settings["db"])
    q.replace_questions(prd_id, [
        {"tag": "need", "prompt": "What is the exact webhook URL?", "owner": "Eng"},
    ])
    rows = q.list_questions(prd_id)
    assert rows[0]["tag"] == "need"
    assert rows[0]["options"] == []


def test_replace_is_delete_then_insert(isolated_settings):
    import app.db.prd_input_questions as q
    _, prd_id = _seed_prd(isolated_settings["db"])
    q.replace_questions(prd_id, [{"tag": "need", "prompt": "First"}])
    q.replace_questions(prd_id, [{"tag": "need", "prompt": "Second"}])
    rows = q.list_questions(prd_id)
    assert len(rows) == 1 and rows[0]["prompt"] == "Second"


def test_replace_skips_blank_prompt(isolated_settings):
    import app.db.prd_input_questions as q
    _, prd_id = _seed_prd(isolated_settings["db"])
    q.replace_questions(prd_id, [
        {"tag": "need", "prompt": "   "},
        {"tag": "need", "prompt": "Real one"},
    ])
    rows = q.list_questions(prd_id)
    assert len(rows) == 1 and rows[0]["prompt"] == "Real one"


def test_answer_question_flips_status(isolated_settings):
    import app.db.prd_input_questions as q
    _, prd_id = _seed_prd(isolated_settings["db"])
    q.replace_questions(prd_id, [{"tag": "escalate", "prompt": "Q?",
                                  "options": [{"label": "Yes"}]}])
    qid = q.list_questions(prd_id)[0]["id"]
    updated = q.answer_question(qid, "Yes", answered_by="Ada")
    assert updated["status"] == "answered"
    assert updated["answer"] == "Yes"
    assert updated["answered_by"] == "Ada"
    assert updated["answered_at"] is not None
    assert q.get_question(qid)["status"] == "answered"


# ── extraction ───────────────────────────────────────────────────────────────

def test_extract_persists_questions(isolated_settings, monkeypatch):
    _, prd_id = _seed_prd(isolated_settings["db"])
    monkeypatch.setattr(prd_questions, "llm_call", lambda **kw: _llm_result({
        "questions": [
            {"tag": "escalate", "prompt": "Ship gated?", "owner": "PM",
             "options": [{"label": "Gated"}, {"label": "Open"}]},
            {"tag": "need", "prompt": "Baseline rate?", "owner": "Data", "options": []},
        ]
    }))
    rows = prd_questions.extract_input_questions(prd_id)
    assert len(rows) == 2

    import app.db.prd_input_questions as q
    stored = q.list_questions(prd_id)
    assert [r["tag"] for r in stored] == ["escalate", "need"]
    assert [o["label"] for o in stored[0]["options"]] == ["Gated", "Open"]


def test_extract_attributes_the_prds_company(isolated_settings, monkeypatch):
    # The gateway binds enterprise_id as the acting company for per-company key
    # routing (app.llm_keys), so extraction must pass the COMPANY id resolved
    # from the PRD's brief → dataset — not the brief id (a non-company id makes
    # the key resolver reject the call and silently kills extraction).
    company_id = "11111111-2222-4333-8444-555555555555"
    _, prd_id = _seed_prd(isolated_settings["db"], dataset="acme")
    seen: dict = {}

    def _capture(**kw):
        seen.update(kw)
        return _llm_result({"questions": []})

    monkeypatch.setattr(prd_questions, "llm_call", _capture)
    monkeypatch.setattr(
        prd_questions, "company_id_for_slug",
        lambda slug: company_id if slug == "acme" else None,
    )
    prd_questions.extract_input_questions(prd_id)
    assert seen["enterprise_id"] == company_id


def test_extract_falls_back_to_dataset_slug_without_company(isolated_settings, monkeypatch):
    # Legacy corpus datasets own no company row: keep the slug as a
    # telemetry-only tag (the key resolver treats non-company ids leniently).
    _, prd_id = _seed_prd(isolated_settings["db"], dataset="legacy-corpus")
    seen: dict = {}

    def _capture(**kw):
        seen.update(kw)
        return _llm_result({"questions": []})

    monkeypatch.setattr(prd_questions, "llm_call", _capture)
    monkeypatch.setattr(prd_questions, "company_id_for_slug", lambda slug: None)
    prd_questions.extract_input_questions(prd_id)
    assert seen["enterprise_id"] == "legacy-corpus"


def test_extract_is_best_effort_on_error(isolated_settings, monkeypatch):
    _, prd_id = _seed_prd(isolated_settings["db"])

    def _boom(**kw):
        raise RuntimeError("gateway down")

    monkeypatch.setattr(prd_questions, "llm_call", _boom)
    # Must NOT raise — extraction is layered on an already-stored PRD.
    assert prd_questions.extract_input_questions(prd_id) == []


def test_extract_missing_prd_returns_empty(isolated_settings, monkeypatch):
    monkeypatch.setattr(prd_questions, "llm_call", lambda **kw: _llm_result({"questions": []}))
    assert prd_questions.extract_input_questions(999999) == []


# ── scoped editor ────────────────────────────────────────────────────────────

def test_apply_answer_returns_edit(isolated_settings, monkeypatch):
    monkeypatch.setattr(prd_questions, "llm_call", lambda **kw: _llm_result({
        "html": "<html><body>EDITED</body></html>",
        "sections_changed": ["Requirements", "Goal"],
        "summary": "Gated the rollout.",
    }))
    out = prd_questions.apply_answer(
        "<html>orig</html>", "Ship gated?", "Gated", enterprise_id="co"
    )
    assert "EDITED" in out["html"]
    assert out["sections_changed"] == ["Requirements", "Goal"]
    assert out["summary"] == "Gated the rollout."


def test_apply_answer_strips_code_fence(isolated_settings, monkeypatch):
    monkeypatch.setattr(prd_questions, "llm_call", lambda **kw: _llm_result({
        "html": "```html\n<html><body>X</body></html>\n```",
        "sections_changed": [], "summary": "",
    }))
    out = prd_questions.apply_answer("<html>o</html>", "Q", "A", enterprise_id="co")
    assert out["html"].startswith("<html>")
    assert "```" not in out["html"]


def test_apply_answer_empty_html_raises(isolated_settings, monkeypatch):
    import pytest
    monkeypatch.setattr(prd_questions, "llm_call", lambda **kw: _llm_result({
        "html": "   ", "sections_changed": [], "summary": "",
    }))
    with pytest.raises(RuntimeError):
        prd_questions.apply_answer("<html>o</html>", "Q", "A", enterprise_id="co")


# ── single-flight registry ───────────────────────────────────────────────────

def test_mark_extracting_single_flight():
    try:
        assert not prd_questions.is_extracting(4242)
        assert prd_questions.mark_extracting(4242) is True
        assert prd_questions.is_extracting(4242)
        # The losing scheduler cannot double-reserve.
        assert prd_questions.mark_extracting(4242) is False
    finally:
        prd_questions.clear_extracting(4242)
    assert not prd_questions.is_extracting(4242)


def test_extract_task_releases_slot_on_error(isolated_settings, monkeypatch):
    # The background task must release its reservation even when extraction
    # blows up, or the PRD would report `extracting` forever and never retry.
    import asyncio

    import app.prd_runner as prd_runner

    def _boom(prd_id):
        raise RuntimeError("gateway down")

    monkeypatch.setattr(prd_questions, "extract_input_questions", _boom)
    assert prd_questions.mark_extracting(777) is True
    asyncio.run(prd_runner.extract_input_questions_task(777, reserved=True))
    assert not prd_questions.is_extracting(777)


def test_extract_task_skips_when_already_reserved(isolated_settings, monkeypatch):
    # An unreserved invocation (the generation pipeline) no-ops when another
    # run holds the slot — and must NOT release the other run's reservation.
    import asyncio

    import app.prd_runner as prd_runner

    calls: list[int] = []
    monkeypatch.setattr(
        prd_questions, "extract_input_questions", lambda prd_id: calls.append(prd_id) or []
    )
    assert prd_questions.mark_extracting(778) is True
    try:
        asyncio.run(prd_runner.extract_input_questions_task(778))
        assert calls == []
        assert prd_questions.is_extracting(778)
    finally:
        prd_questions.clear_extracting(778)


# ── routes ───────────────────────────────────────────────────────────────────

def test_get_input_questions_route(tenant_client, isolated_settings):
    t = tenant_client.make(slug="acme")
    db_mod = isolated_settings["db"]
    _, prd_id = _seed_prd(db_mod, dataset="acme")
    import app.db.prd_input_questions as q
    q.replace_questions(prd_id, [{"tag": "need", "prompt": "Baseline?"}])

    resp = t.client.get(f"/v1/prd/{prd_id}/input-questions")
    assert resp.status_code == 200
    body = resp.json()
    questions = body["questions"]
    assert len(questions) == 1 and questions[0]["prompt"] == "Baseline?"
    # Stored rows → no backfill needed.
    assert body["extracting"] is False


def _patch_extract_task(monkeypatch):
    """Replace the background extraction task with a recorder (patch the routes
    module's binding — that is the name the GET handler schedules)."""
    import app.routes.prd as prd_routes

    scheduled: list[int] = []

    async def _fake_task(prd_id: int, *, reserved: bool = False) -> None:
        scheduled.append(prd_id)
        prd_questions.clear_extracting(prd_id)

    monkeypatch.setattr(prd_routes, "extract_input_questions_task", _fake_task)
    return scheduled


def _wait_for(predicate, timeout=2.0):
    """The route fire-and-forgets its task onto the app loop; give it a beat to
    run before asserting (deterministic wait, not a fixed sleep)."""
    import time

    deadline = time.time() + timeout
    while not predicate() and time.time() < deadline:
        time.sleep(0.01)
    return predicate()


def test_get_input_questions_backfills_pre_feature_prd(
    tenant_client, isolated_settings, monkeypatch
):
    # A ready PRD with a "User input needed" section but NO stored questions
    # (generated before extraction existed, opened from Artifacts): the GET
    # schedules the backfill and reports extracting so the client polls.
    t = tenant_client.make(slug="acme")
    _, prd_id = _seed_prd(
        isolated_settings["db"], dataset="acme",
        html="<html><body><div>User input needed</div><ul class='inputs'>"
             "<li>[NEED] baseline</li></ul></body></html>",
    )
    scheduled = _patch_extract_task(monkeypatch)

    resp = t.client.get(f"/v1/prd/{prd_id}/input-questions")
    assert resp.status_code == 200
    assert resp.json() == {"questions": [], "extracting": True}
    assert _wait_for(lambda: scheduled == [prd_id])
    # The fake task released the slot on completion.
    assert _wait_for(lambda: not prd_questions.is_extracting(prd_id))


def test_get_input_questions_no_section_no_backfill(
    tenant_client, isolated_settings, monkeypatch
):
    # No "User input needed" section in the document → nothing to extract; the
    # GET answers empty WITHOUT burning an LLM call, and the client stops there.
    t = tenant_client.make(slug="acme")
    _, prd_id = _seed_prd(
        isolated_settings["db"], dataset="acme",
        html="<html><body>All resolved.</body></html>",
    )
    scheduled = _patch_extract_task(monkeypatch)

    resp = t.client.get(f"/v1/prd/{prd_id}/input-questions")
    assert resp.status_code == 200
    assert resp.json() == {"questions": [], "extracting": False}
    # Negative check: give a (wrongly) scheduled task a beat to surface.
    assert not _wait_for(lambda: scheduled, timeout=0.2)


def test_get_input_questions_inflight_not_rescheduled(
    tenant_client, isolated_settings, monkeypatch
):
    # An extraction already holds the slot (e.g. the generation pipeline's run
    # for a just-finished PRD): the GET reports extracting but schedules nothing.
    t = tenant_client.make(slug="acme")
    _, prd_id = _seed_prd(
        isolated_settings["db"], dataset="acme",
        html="<html><body>User input needed<ul><li>[NEED] x</li></ul></body></html>",
    )
    scheduled = _patch_extract_task(monkeypatch)
    assert prd_questions.mark_extracting(prd_id) is True
    try:
        resp = t.client.get(f"/v1/prd/{prd_id}/input-questions")
        assert resp.status_code == 200
        assert resp.json() == {"questions": [], "extracting": True}
        assert not _wait_for(lambda: scheduled, timeout=0.2)
    finally:
        prd_questions.clear_extracting(prd_id)


def test_get_input_questions_cross_tenant_404(tenant_client, isolated_settings):
    a = tenant_client.make(slug="acme")
    tenant_client.make(slug="other")
    db_mod = isolated_settings["db"]
    _, prd_id = _seed_prd(db_mod, dataset="other")
    # 'acme' cannot read 'other's PRD questions.
    resp = a.client.get(f"/v1/prd/{prd_id}/input-questions")
    assert resp.status_code == 404


def test_answer_route_applies_scoped_edit(tenant_client, isolated_settings, monkeypatch):
    t = tenant_client.make(slug="acme")
    db_mod = isolated_settings["db"]
    _, prd_id = _seed_prd(db_mod, dataset="acme", html="<html><body>ORIG</body></html>")
    import app.db.prd_input_questions as q
    q.replace_questions(prd_id, [{"tag": "escalate", "prompt": "Ship gated?",
                                  "options": [{"label": "Gated"}]}])
    qid = q.list_questions(prd_id)[0]["id"]

    # Mock the scoped editor (patch the source; the route lazy-imports it).
    monkeypatch.setattr(prd_questions, "apply_answer", lambda *a, **k: {
        "html": "<html><body>GATED ROLLOUT</body></html>",
        "sections_changed": ["Requirements"],
        "summary": "Gated.",
    })

    resp = t.client.post(
        f"/v1/prd/{prd_id}/input-questions/{qid}/answer", json={"answer": "Gated"}
    )
    assert resp.status_code == 200
    body = resp.json()
    assert "GATED ROLLOUT" in body["prd"]["payload_md"]
    assert body["sections_changed"] == ["Requirements"]
    assert body["question"]["status"] == "answered"
    assert body["question"]["answer"] == "Gated"

    # PRD content actually updated + a version snapshot was taken (undo point).
    from app.db.prds import list_prd_versions
    assert "GATED ROLLOUT" in db_mod.get_prd(prd_id)["payload_md"]
    assert len(list_prd_versions(prd_id)) >= 1


def test_answer_route_unknown_question_404(tenant_client, isolated_settings):
    t = tenant_client.make(slug="acme")
    db_mod = isolated_settings["db"]
    _, prd_id = _seed_prd(db_mod, dataset="acme")
    resp = t.client.post(
        f"/v1/prd/{prd_id}/input-questions/424242/answer", json={"answer": "x"}
    )
    assert resp.status_code == 404


# ── apply_chat_edit (free-form chat instruction editor) ──────────────────────

def test_apply_chat_edit_round_trip(isolated_settings, monkeypatch):
    seen = {}

    def _capture(**kw):
        seen.update(kw)
        return _llm_result({
            "html": "<html>v2</html>", "sections_changed": ["Goal"], "summary": "s",
        })

    monkeypatch.setattr(prd_questions, "llm_call", _capture)
    out = prd_questions.apply_chat_edit(
        "<html>v1</html>", "shorten the goal", enterprise_id="ent-1"
    )
    assert out == {"html": "<html>v2</html>", "sections_changed": ["Goal"], "summary": "s"}
    assert seen["enterprise_id"] == "ent-1"
    assert seen["purpose"] == "apply_prd_chat_edit"
    assert "shorten the goal" in seen["input"] and "<html>v1</html>" in seen["input"]


def test_apply_chat_edit_raises_on_empty_html(isolated_settings, monkeypatch):
    monkeypatch.setattr(prd_questions, "llm_call", lambda **kw: _llm_result({
        "html": "", "sections_changed": [], "summary": "",
    }))
    import pytest as _pytest
    with _pytest.raises(RuntimeError):
        prd_questions.apply_chat_edit("<html>v1</html>", "shorten", enterprise_id="e")
