"""Auto-attach: a report GENERATED inside a project chat lands on that
project's own artifact list — the same standing rule the other five
artifact types already follow (prd/evidence/ticket_set/prototype/
custom_artifact). Wiring tests for the `ask_job_runner._on_committed` hook;
`save_chat_output_as_report` and `add_artifact` each have their own direct
unit coverage elsewhere (`test_project_artifact_capture.py`,
`test_db_projects.py`-shaped suites) — this file proves the HOOK calls them
with the right arguments, under the right gate, and never at the wrong time.

Root-cause note (verified directly against source, not assumed): the
obvious-looking `capture_report` call a few lines above this hook in
`_on_committed` is NOT the mechanism — `capture_report` only persists a
self-contained HTML-DOCUMENT answer (`report_capture._HTML_DOC_RE`), and
every report pipeline (VoC, competitive-intelligence, public-feedback,
company-research, market-intelligence, call-digest) now answers in plain
MARKDOWN — its own module docstring says the HTML sniff "self-disables" for
exactly those skills since the pinned HTML templates were removed. So
`capture_report` returns `None` for a real report and writes nothing; relying
on its return id would be a vacuous no-op fix. The new hook mints the row
itself via `save_chat_output_as_report` (the same writer the manual "Save to
project" button already calls), independent of `capture_report`.
"""
from __future__ import annotations

import asyncio

from app import ask_job_runner as ajr


def _payload(answer: str, skill: str | None) -> dict:
    """An Ask-shaped payload, as `qa_agent._tag` (or a report module's own
    tagging) leaves it — `_skill` carries the report-decision signal the
    hook reads."""
    return {
        "answer": answer, "key_points": [], "citations": [],
        "confidence": 0.7, "unanswered": "", "_skill": skill,
    }


_REPORT_BODY = (
    "## Voice of customer themes\n\n- Onboarding friction was the top "
    "complaint across the sampled window.\n- Pricing questions came up "
    "repeatedly on renewal calls."
)

_PROJECT_CONTEXT_SOURCE = {
    "kind": "project", "params": {"project_id": 9, "surface": "private"},
}


def _wire_common(monkeypatch, *, answer_payload: dict):
    """The scaffolding every test below shares: a stubbed answer, a no-op
    terminal write, an unstubbed-cancel worker, scope resolution stood down
    (orthogonal to this hook — it reads `context_source` directly, mirroring
    `test_ask_project_promotion.py`'s own precedent for the sibling
    `maybe_promote_turn` hook), and the sibling promotion/ingest hooks
    stubbed inert so this file only ever exercises the report-attach path."""
    monkeypatch.setattr(ajr.qa_agent, "answer", lambda **kw: dict(answer_payload))
    monkeypatch.setattr(ajr, "complete_ask_job", lambda i, p: None)
    monkeypatch.setattr(ajr, "is_ask_cancelled", lambda i: False)
    monkeypatch.setattr(ajr, "resolve_context_scope", lambda *a, **k: None)

    import app.project_memory as pm

    monkeypatch.setattr(pm, "maybe_promote_turn", lambda *a, **kw: None)

    import app.delegation_status_ingest as dsi

    monkeypatch.setattr(dsi, "maybe_ingest_status", lambda *a, **kw: None)


def _patch_capture(monkeypatch, *, report_id: int | None = 501):
    """Mock the two writers the hook calls, returning capture dicts of every
    call it made — `save_chat_output_as_report` (the mint) and `add_artifact`
    (the attach), each patched at their OWN module (the hook imports them
    fresh, by name, inside its try block, so patching the source module's
    attribute is what a late `from ... import ...` actually picks up)."""
    calls = {"save": [], "attach": []}

    def _fake_save(**kw):
        calls["save"].append(kw)
        return report_id

    def _fake_add_artifact(project_id, artifact_type, artifact_id):
        calls["attach"].append((project_id, artifact_type, artifact_id))
        return {
            "project_id": project_id, "artifact_type": artifact_type,
            "artifact_id": artifact_id,
        }

    monkeypatch.setattr(
        "app.project_artifact_capture.save_chat_output_as_report", _fake_save
    )
    monkeypatch.setattr("app.db.projects.add_artifact", _fake_add_artifact)
    return calls


# ── AC1: a project report turn mints + attaches ─────────────────────────────


async def test_project_report_turn_mints_and_attaches(monkeypatch):
    _wire_common(monkeypatch, answer_payload=_payload(
        _REPORT_BODY, "voice-of-customer-report",
    ))
    calls = _patch_capture(monkeypatch)

    await ajr.run_ask_job(
        ask_id=1, enterprise_id="c1", question="give me a voice-of-customer report",
        dataset="d", conversation_id=5, user_id="u1",
        context_source=_PROJECT_CONTEXT_SOURCE,
    )

    assert len(calls["save"]) == 1
    assert calls["save"][0]["content"] == _REPORT_BODY
    assert calls["save"][0]["company_id"] == "c1"
    assert calls["save"][0]["workspace_id"] is None
    assert calls["save"][0]["conversation_id"] == 5
    assert calls["attach"] == [(9, "report", 501)]


async def test_project_report_turn_covers_every_report_pipeline_id(monkeypatch):
    """Every member of the SAME `_REPORT_PIPELINE_IDS` set the sixth-branch
    report-deferral gate reads (five `PIPELINE_SKILLS` report ids + the
    `call-digest` machinery id) triggers the mint+attach — one signal, no
    second allow-list that could drift out of sync."""
    for report_id in sorted(ajr.qa_agent._REPORT_PIPELINE_IDS):
        _wire_common(monkeypatch, answer_payload=_payload(_REPORT_BODY, report_id))
        calls = _patch_capture(monkeypatch, report_id=777)

        await ajr.run_ask_job(
            ask_id=2, enterprise_id="c1", question="q", dataset="d",
            conversation_id=5, user_id="u1",
            context_source=_PROJECT_CONTEXT_SOURCE,
        )
        assert calls["attach"] == [(9, "report", 777)], report_id


# ── AC2: a non-report project answer mints/attaches nothing ─────────────────


async def test_non_report_project_answer_no_mint_no_attach(monkeypatch):
    _wire_common(monkeypatch, answer_payload=_payload(
        "There are 3 tasks open on this project right now.", None,
    ))
    calls = _patch_capture(monkeypatch)

    await ajr.run_ask_job(
        ask_id=3, enterprise_id="c1", question="what tasks are open?",
        dataset="d", conversation_id=5, user_id="u1",
        context_source=_PROJECT_CONTEXT_SOURCE,
    )

    assert calls["save"] == []
    assert calls["attach"] == []


async def test_non_report_pipeline_skill_no_mint_no_attach(monkeypatch):
    """A resolved skill that is NOT a report (a tracker lookup, a company
    skill, etc.) must not be misread as a report just because `_skill` is
    non-None."""
    _wire_common(monkeypatch, answer_payload=_payload(
        "Here are the open Jira tickets.", "tracker-lookup",
    ))
    calls = _patch_capture(monkeypatch)

    await ajr.run_ask_job(
        ask_id=4, enterprise_id="c1", question="what jira tickets are open?",
        dataset="d", conversation_id=5, user_id="u1",
        context_source=_PROJECT_CONTEXT_SOURCE,
    )

    assert calls["save"] == []
    assert calls["attach"] == []


# ── AC3: a main-chat report never attaches ───────────────────────────────────


async def test_main_chat_report_no_attach_no_context_source(monkeypatch):
    """No `context_source` at all (the plain main-chat call shape) — a
    report answer is stored as usual but never attached to any project."""
    _wire_common(monkeypatch, answer_payload=_payload(
        _REPORT_BODY, "voice-of-customer-report",
    ))
    calls = _patch_capture(monkeypatch)

    await ajr.run_ask_job(
        ask_id=5, enterprise_id="c1", question="give me a voice-of-customer report",
        dataset="d", conversation_id=5, user_id="u1",
    )

    assert calls["save"] == []
    assert calls["attach"] == []


async def test_main_chat_report_no_attach_non_project_context_source_kind(monkeypatch):
    """A `context_source` present but of a DIFFERENT kind (never project) —
    the gate reads `kind == "project"` literally, nothing looser."""
    _wire_common(monkeypatch, answer_payload=_payload(
        _REPORT_BODY, "market-intelligence-report",
    ))
    calls = _patch_capture(monkeypatch)

    await ajr.run_ask_job(
        ask_id=6, enterprise_id="c1", question="give me a market intel report",
        dataset="d", conversation_id=5, user_id="u1",
        context_source={"kind": "prd", "params": {"prd_id": 3}},
    )

    assert calls["save"] == []
    assert calls["attach"] == []


async def test_project_report_never_falls_back_to_a_top_level_project_id(monkeypatch):
    """The gate is STRICT on `context_source["params"]["project_id"]` — a
    top-level `project_id` kwarg (which project chat never actually sends)
    must never substitute for it."""
    _wire_common(monkeypatch, answer_payload=_payload(
        _REPORT_BODY, "voice-of-customer-report",
    ))
    calls = _patch_capture(monkeypatch)

    await ajr.run_ask_job(
        ask_id=7, enterprise_id="c1", question="give me a voice-of-customer report",
        dataset="d", conversation_id=5, user_id="u1",
        project_id=42,  # top-level kwarg only — no context_source at all
    )

    assert calls["save"] == []
    assert calls["attach"] == []


# ── AC4: idempotency ─────────────────────────────────────────────────────────


async def test_repeated_attach_is_shaped_as_a_no_op(monkeypatch):
    """Two report turns in the SAME project each call `add_artifact` with
    the identical `(project_id, "report", report_id)` triple that resolved —
    the hook itself never special-cases a repeat, and `add_artifact`'s own
    upsert (`on_conflict="project_id,artifact_type,artifact_id"`,
    `db/projects.py`) is what makes a repeat of that exact triple a no-op at
    the DB layer, not a second row. Pinned here as a source-level fact so a
    future edit to that conflict target is caught."""
    import inspect

    from app.db import projects as projects_db

    assert (
        'on_conflict="project_id,artifact_type,artifact_id"'
        in inspect.getsource(projects_db.add_artifact)
    )

    _wire_common(monkeypatch, answer_payload=_payload(
        _REPORT_BODY, "voice-of-customer-report",
    ))
    calls = _patch_capture(monkeypatch, report_id=501)

    for ask_id in (8, 9):
        await ajr.run_ask_job(
            ask_id=ask_id, enterprise_id="c1",
            question="give me a voice-of-customer report", dataset="d",
            conversation_id=5, user_id="u1",
            context_source=_PROJECT_CONTEXT_SOURCE,
        )

    assert calls["attach"] == [(9, "report", 501), (9, "report", 501)]


# ── AC5: best-effort — a mint/attach failure never breaks the answer ────────


async def test_attach_failure_does_not_break_the_answer(monkeypatch):
    _wire_common(monkeypatch, answer_payload=_payload(
        _REPORT_BODY, "voice-of-customer-report",
    ))

    def _boom(**kw):  # noqa: ARG001
        raise RuntimeError("forced mint failure")

    monkeypatch.setattr(
        "app.project_artifact_capture.save_chat_output_as_report", _boom
    )

    completed: dict = {}
    monkeypatch.setattr(ajr, "complete_ask_job", lambda i, p: completed.setdefault(i, p))

    # run_ask_job's own outer contract must swallow this; it must not raise
    # out of the call, and the already-committed answer must survive intact.
    await ajr.run_ask_job(
        ask_id=10, enterprise_id="c1", question="give me a voice-of-customer report",
        dataset="d", conversation_id=5, user_id="u1",
        context_source=_PROJECT_CONTEXT_SOURCE,
    )

    assert completed[10]["answer"] == _REPORT_BODY
