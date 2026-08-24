"""Auto-attach: a report GENERATED inside a project chat lands on that
project's own artifact list — the same standing rule the other five
artifact types already follow (prd/evidence/ticket_set/prototype/
custom_artifact). Wiring tests for the `ask_job_runner._on_committed` hook;
`capture_report` and `add_artifact` each have their own direct unit coverage
elsewhere (`test_report_capture.py`, `test_db_projects.py`-shaped suites) —
this file proves the HOOK calls them with the right arguments, under the
right gate, and never at the wrong time.

Mechanism note. This hook used to mint its own row via
`save_chat_output_as_report`, because `capture_report` only persisted a
self-contained HTML-DOCUMENT answer and every report pipeline answers in
MARKDOWN since the pinned templates were removed — so capture returned None
for a real report and wrote nothing. That is fixed at the source:
`report_capture` now also captures a payload the engines marked
`_report: True`, which is what makes ONE row serve both the project's
artifact list and (in main chat, where nothing attached at all) the library
and the thread it was generated in. The hook therefore attaches the row
capture just wrote instead of minting a second one under the `saved-chat`
skill.
"""
from __future__ import annotations

from app import ask_job_runner as ajr


def _payload(answer: str, skill: str | None, *, report: bool = False) -> dict:
    """An Ask-shaped payload, as `qa_agent._tag` (or a report module's own
    tagging) leaves it. `_report` is the engines' marker for "this return IS
    the document" — never stamped on their degraded apologies, which carry
    `_skill` all the same."""
    return {
        "answer": answer, "key_points": [], "citations": [],
        "confidence": 0.7, "unanswered": "", "_skill": skill,
        **({"_report": True} if report else {}),
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
    """Mock the two writers this path runs through — `db.save_report` (the
    ONE row, written by `capture_report`) and `add_artifact` (the attach) —
    returning capture dicts of every call each received. `add_artifact` is
    patched at its own module because the hook imports it fresh, by name,
    inside its try block."""
    calls = {"save": [], "attach": []}

    def _fake_save(company_id, **kw):
        calls["save"].append({"company_id": company_id, **kw})
        return report_id

    def _fake_add_artifact(project_id, artifact_type, artifact_id):
        calls["attach"].append((project_id, artifact_type, artifact_id))
        return {
            "project_id": project_id, "artifact_type": artifact_type,
            "artifact_id": artifact_id,
        }

    import app.db as db

    monkeypatch.setattr(db, "save_report", _fake_save)
    monkeypatch.setattr("app.db.projects.add_artifact", _fake_add_artifact)
    return calls


# ── AC1: a project report turn captures + attaches ──────────────────────────


async def test_project_report_turn_captures_and_attaches(monkeypatch):
    _wire_common(monkeypatch, answer_payload=_payload(
        _REPORT_BODY, "voice-of-customer-report", report=True,
    ))
    calls = _patch_capture(monkeypatch)

    await ajr.run_ask_job(
        ask_id=1, enterprise_id="c1", question="give me a voice-of-customer report",
        dataset="d", conversation_id=5, user_id="u1",
        context_source=_PROJECT_CONTEXT_SOURCE,
    )

    assert len(calls["save"]) == 1, "one row, not one per consumer"
    saved = calls["save"][0]
    assert saved["html"] == _REPORT_BODY
    assert saved["company_id"] == "c1"
    assert saved["workspace_id"] is None
    assert saved["conversation_id"] == 5, "attached to the chat it ran in"
    # The row is badged with the REPORT's own skill, not the generic
    # `saved-chat` the retired workaround used — that is what gives the
    # artifacts row its kind label and its per-kind filter.
    assert saved["skill"] == "voice-of-customer-report"
    assert calls["attach"] == [(9, "report", 501)]


async def test_project_report_turn_covers_every_report_pipeline_id(monkeypatch):
    """Every member of the `_REPORT_PIPELINE_IDS` set the sixth-branch
    report-deferral gate reads (five `PIPELINE_SKILLS` report ids + the
    `call-digest` machinery id) captures and attaches when its payload is
    marked as the document."""
    for report_skill in sorted(ajr.qa_agent._REPORT_PIPELINE_IDS):
        _wire_common(monkeypatch, answer_payload=_payload(
            _REPORT_BODY, report_skill, report=True,
        ))
        calls = _patch_capture(monkeypatch, report_id=777)

        await ajr.run_ask_job(
            ask_id=2, enterprise_id="c1", question="q", dataset="d",
            conversation_id=5, user_id="u1",
            context_source=_PROJECT_CONTEXT_SOURCE,
        )
        assert calls["attach"] == [(9, "report", 777)], report_skill


# ── AC2: a non-report project answer captures/attaches nothing ──────────────


async def test_non_report_project_answer_no_capture_no_attach(monkeypatch):
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


async def test_non_report_pipeline_skill_no_capture_no_attach(monkeypatch):
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


async def test_a_report_pipelines_apology_is_not_an_artifact(monkeypatch):
    """The engines stamp `_skill` on their degraded returns too ("I'm not
    connected to a transcript source"). Only `_report` says a document was
    produced, so an apology leaves no artifact behind."""
    _wire_common(monkeypatch, answer_payload=_payload(
        "I couldn't find any calls in that window to summarize.",
        "voice-of-customer-report",
    ))
    calls = _patch_capture(monkeypatch)

    await ajr.run_ask_job(
        ask_id=11, enterprise_id="c1", question="voc for last week",
        dataset="d", conversation_id=5, user_id="u1",
        context_source=_PROJECT_CONTEXT_SOURCE,
    )

    assert calls["save"] == []
    assert calls["attach"] == []


# ── AC3: a main-chat report is captured, but never attached ─────────────────


async def test_main_chat_report_is_captured_and_never_attached(monkeypatch):
    """No `context_source` at all (the plain main-chat call shape). The
    report IS captured — that row is the library entry and the thread's own
    Reports panel — but it belongs to no project, so nothing attaches."""
    _wire_common(monkeypatch, answer_payload=_payload(
        _REPORT_BODY, "voice-of-customer-report", report=True,
    ))
    calls = _patch_capture(monkeypatch)

    await ajr.run_ask_job(
        ask_id=5, enterprise_id="c1", question="give me a voice-of-customer report",
        dataset="d", conversation_id=5, user_id="u1",
    )

    assert len(calls["save"]) == 1
    assert calls["save"][0]["conversation_id"] == 5
    assert calls["attach"] == []


async def test_main_chat_report_no_attach_non_project_context_source_kind(monkeypatch):
    """A `context_source` present but of a DIFFERENT kind (never project) —
    the gate reads `kind == "project"` literally, nothing looser."""
    _wire_common(monkeypatch, answer_payload=_payload(
        _REPORT_BODY, "market-intelligence-report", report=True,
    ))
    calls = _patch_capture(monkeypatch)

    await ajr.run_ask_job(
        ask_id=6, enterprise_id="c1", question="give me a market intel report",
        dataset="d", conversation_id=5, user_id="u1",
        context_source={"kind": "prd", "params": {"prd_id": 3}},
    )

    assert len(calls["save"]) == 1, "still a library artifact"
    assert calls["attach"] == []


async def test_project_report_never_falls_back_to_a_top_level_project_id(monkeypatch):
    """The gate is STRICT on `context_source["params"]["project_id"]` — a
    top-level `project_id` kwarg (which project chat never actually sends)
    must never substitute for it."""
    _wire_common(monkeypatch, answer_payload=_payload(
        _REPORT_BODY, "voice-of-customer-report", report=True,
    ))
    calls = _patch_capture(monkeypatch)

    await ajr.run_ask_job(
        ask_id=7, enterprise_id="c1", question="give me a voice-of-customer report",
        dataset="d", conversation_id=5, user_id="u1",
        project_id=42,  # top-level kwarg only — no context_source at all
    )

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
        _REPORT_BODY, "voice-of-customer-report", report=True,
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


async def test_a_capture_that_wrote_nothing_attaches_nothing(monkeypatch):
    """No row means no id to attach — the hook must not invent one (and must
    not fall back to minting a second, differently-badged row, which is what
    it used to do)."""
    _wire_common(monkeypatch, answer_payload=_payload(
        _REPORT_BODY, "voice-of-customer-report", report=True,
    ))
    calls = _patch_capture(monkeypatch, report_id=None)

    await ajr.run_ask_job(
        ask_id=12, enterprise_id="c1", question="give me a voice-of-customer report",
        dataset="d", conversation_id=5, user_id="u1",
        context_source=_PROJECT_CONTEXT_SOURCE,
    )

    assert calls["attach"] == []


# ── AC5: best-effort — a capture/attach failure never breaks the answer ─────


async def test_attach_failure_does_not_break_the_answer(monkeypatch):
    _wire_common(monkeypatch, answer_payload=_payload(
        _REPORT_BODY, "voice-of-customer-report", report=True,
    ))
    _patch_capture(monkeypatch)

    def _boom(*a, **kw):  # noqa: ARG001
        raise RuntimeError("forced attach failure")

    monkeypatch.setattr("app.db.projects.add_artifact", _boom)

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
