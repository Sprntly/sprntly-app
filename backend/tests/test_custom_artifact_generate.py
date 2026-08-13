"""Tests for custom-artifact generation — the writer, its gates, and the sweep.

The properties under test are the ones whose failure a user would feel:

  * a generation that produces nothing must FAIL the row, not leave an empty
    document that looks like a blank page the user opened themselves;
  * generated HTML is sanitized on the way in, exactly like a typed edit;
  * the row's title comes from the document's own <h1>, so the library row and
    the first line of the document cannot disagree;
  * `generate_into` never raises, because the panel polls the row and an escaped
    exception leaves it spinning on a generation that is not running;
  * the orphan sweep is AGE-GATED — staging and prod share one Supabase project,
    so a blanket sweep would fail the other environment's live generations.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest

from app import custom_artifact_generate as gen
from app.db.custom_artifacts import create_artifact, get_artifact
from tests import _fake_supabase
from tests._company_helpers import seed_company

_DDL = """
CREATE TABLE IF NOT EXISTS custom_artifacts (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    company_id      TEXT NOT NULL,
    workspace_id    TEXT,
    conversation_id INTEGER,
    kind            TEXT NOT NULL DEFAULT '',
    title           TEXT NOT NULL DEFAULT '',
    body_html       TEXT NOT NULL DEFAULT '',
    status          TEXT NOT NULL DEFAULT 'ready',
    error           TEXT,
    version         INTEGER NOT NULL DEFAULT 1,
    created_by      TEXT,
    updated_by      TEXT,
    created_at      TEXT NOT NULL DEFAULT (datetime('now')),
    updated_at      TEXT NOT NULL DEFAULT (datetime('now'))
);
"""


@pytest.fixture
def gen_env(isolated_settings):
    _fake_supabase.get_fake_db().executescript(_DDL)
    return seed_company(user_id="gen-user", slug="acme")


def _pending(company_id: str, kind: str = "leadership update") -> int:
    return create_artifact(company_id, kind=kind, status="generating")["id"]


def _stub_llm(monkeypatch, text: str):
    monkeypatch.setattr(
        gen, "llm_call", lambda **kw: SimpleNamespace(text=text)
    )


# ─── The happy path ──────────────────────────────────────────────────────────

def test_generation_lands_ready_with_the_document(gen_env, monkeypatch):
    _stub_llm(monkeypatch, "<h1>Q3 reliability update</h1><p>Latency is down.</p>")
    doc_id = _pending(gen_env)

    gen.generate_into(
        company_id=gen_env, artifact_id=doc_id,
        kind="leadership update", task="write up Q3 reliability",
    )

    row = get_artifact(gen_env, doc_id)
    assert row["status"] == "ready"
    assert "Latency is down" in row["body_html"]
    assert row["error"] is None


def test_title_comes_from_the_documents_own_h1(gen_env, monkeypatch):
    """The library row and the document's first line must not disagree — a row
    called "leadership update" over a document titled "Q3 reliability update"
    reads as two different documents."""
    _stub_llm(monkeypatch, "<h1>Q3 reliability update</h1><p>x</p>")
    doc_id = _pending(gen_env)
    gen.generate_into(company_id=gen_env, artifact_id=doc_id,
                      kind="leadership update", task="t")
    assert get_artifact(gen_env, doc_id)["title"] == "Q3 reliability update"


def test_title_falls_back_to_the_kind_when_there_is_no_h1(gen_env, monkeypatch):
    _stub_llm(monkeypatch, "<p>no heading here</p>")
    doc_id = _pending(gen_env)
    gen.generate_into(company_id=gen_env, artifact_id=doc_id,
                      kind="launch plan", task="t")
    assert get_artifact(gen_env, doc_id)["title"] == "launch plan"


def test_generated_html_is_sanitized(gen_env, monkeypatch):
    """A generation is not more trusted than a paste. The model is instructed to
    emit a small tag vocabulary, but instruction is not enforcement."""
    _stub_llm(monkeypatch, "<h1>T</h1><p>ok</p><script>alert(1)</script>")
    doc_id = _pending(gen_env)
    gen.generate_into(company_id=gen_env, artifact_id=doc_id, kind="memo", task="t")
    body = get_artifact(gen_env, doc_id)["body_html"]
    assert "<script" not in body and "ok" in body


def test_code_fence_is_stripped(gen_env, monkeypatch):
    """Models wrap HTML in a fence often enough that the raw text would
    otherwise be stored as literal backticks."""
    _stub_llm(monkeypatch, "```html\n<h1>T</h1><p>body</p>\n```")
    doc_id = _pending(gen_env)
    gen.generate_into(company_id=gen_env, artifact_id=doc_id, kind="memo", task="t")
    row = get_artifact(gen_env, doc_id)
    assert "```" not in row["body_html"]
    assert row["title"] == "T"


# ─── Failure is recorded, never swallowed and never left spinning ────────────

def test_empty_generation_fails_the_row_rather_than_storing_a_blank(
    gen_env, monkeypatch
):
    """An empty document is indistinguishable from the user's own blank page,
    so it would hide the fact that a call ran and came back with nothing."""
    _stub_llm(monkeypatch, "   ")
    doc_id = _pending(gen_env)
    gen.generate_into(company_id=gen_env, artifact_id=doc_id, kind="memo", task="t")
    row = get_artifact(gen_env, doc_id)
    assert row["status"] == "failed"
    assert row["body_html"] == ""


def test_content_that_is_entirely_stripped_also_fails(gen_env, monkeypatch):
    """A model that returns ONLY disallowed markup leaves nothing behind. The
    sanitizer succeeds and the document is still empty — so the emptiness check
    has to run AFTER sanitizing, not before."""
    _stub_llm(monkeypatch, "<script>alert(1)</script>")
    doc_id = _pending(gen_env)
    gen.generate_into(company_id=gen_env, artifact_id=doc_id, kind="memo", task="t")
    assert get_artifact(gen_env, doc_id)["status"] == "failed"


def test_an_llm_failure_is_recorded_and_never_raises(gen_env, monkeypatch):
    """Total by contract: the panel polls this row, so an escaped exception
    would leave a document spinning on a generation that is not running."""
    def _boom(**kw):
        raise RuntimeError("gateway down")

    monkeypatch.setattr(gen, "llm_call", _boom)
    doc_id = _pending(gen_env)

    gen.generate_into(company_id=gen_env, artifact_id=doc_id, kind="memo", task="t")

    row = get_artifact(gen_env, doc_id)
    assert row["status"] == "failed"
    assert "gateway down" in (row["error"] or "")


# ─── The prompt carries the grounding rule ───────────────────────────────────

def test_context_is_passed_to_the_model_and_named_as_the_only_facts(
    gen_env, monkeypatch
):
    captured = {}

    def _capture(**kw):
        captured.update(kw)
        return SimpleNamespace(text="<h1>T</h1><p>x</p>")

    monkeypatch.setattr(gen, "llm_call", _capture)
    gen.generate_into(
        company_id=gen_env, artifact_id=_pending(gen_env),
        kind="leadership update", task="Q3 reliability",
        context="p99 latency fell from 900ms to 210ms",
    )
    assert "p99 latency fell" in captured["input"]
    assert "leadership update" in captured["input"]
    # The rule that stops a forwarded document inventing a number.
    assert "Never invent a number" in captured["system"]


def test_missing_context_is_stated_rather_than_left_blank(gen_env, monkeypatch):
    """"No context" has to be a stated condition to write honestly under, not
    an empty section the model papers over."""
    captured = {}
    monkeypatch.setattr(
        gen, "llm_call",
        lambda **kw: (captured.update(kw), SimpleNamespace(text="<h1>T</h1><p>x</p>"))[1],
    )
    gen.generate_into(company_id=gen_env, artifact_id=_pending(gen_env),
                      kind="memo", task="t")
    assert "CONTEXT: none was supplied" in captured["input"]


# ─── The orphan sweep ────────────────────────────────────────────────────────

def _age(company_id: str, doc_id: int, minutes: int) -> None:
    """Backdate a row's updated_at, the only signal the sweep has."""
    from app.db.client import require_client

    stamp = (datetime.now(timezone.utc) - timedelta(minutes=minutes)).isoformat()
    require_client().table("custom_artifacts").update(
        {"updated_at": stamp}
    ).eq("id", doc_id).execute()


def test_sweep_fails_an_old_abandoned_generation(gen_env):
    doc_id = _pending(gen_env)
    _age(gen_env, doc_id, 120)
    assert gen.sweep_orphan_generating() == 1
    row = get_artifact(gen_env, doc_id)
    assert row["status"] == "failed"
    assert row["error"] == gen.ORPHAN_ERROR


def test_sweep_leaves_a_generation_that_is_still_running(gen_env):
    """THE ONE THAT MATTERS: staging and prod share one Supabase project, so
    both environments' rows live in this table. A blanket "fail everything
    generating" sweep at staging startup would kill documents prod is writing
    right now. Age is the only signal that separates a dead owner from a live
    one, so a fresh row must survive."""
    doc_id = _pending(gen_env)  # written just now
    assert gen.sweep_orphan_generating() == 0
    assert get_artifact(gen_env, doc_id)["status"] == "generating"


def test_sweep_ignores_finished_documents(gen_env):
    from app.db.custom_artifacts import finish_artifact

    doc_id = _pending(gen_env)
    finish_artifact(gen_env, doc_id, title="T", body_html="<p>done</p>")
    _age(gen_env, doc_id, 500)
    assert gen.sweep_orphan_generating() == 0
    assert get_artifact(gen_env, doc_id)["status"] == "ready"
