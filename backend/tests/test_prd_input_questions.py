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


# ── routes ───────────────────────────────────────────────────────────────────

def test_get_input_questions_route(tenant_client, isolated_settings):
    t = tenant_client.make(slug="acme")
    db_mod = isolated_settings["db"]
    _, prd_id = _seed_prd(db_mod, dataset="acme")
    import app.db.prd_input_questions as q
    q.replace_questions(prd_id, [{"tag": "need", "prompt": "Baseline?"}])

    resp = t.client.get(f"/v1/prd/{prd_id}/input-questions")
    assert resp.status_code == 200
    questions = resp.json()["questions"]
    assert len(questions) == 1 and questions[0]["prompt"] == "Baseline?"


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
