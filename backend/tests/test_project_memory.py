"""Tests for `app/project_memory.py`: the bounded LLM writer that
regenerates `project_memory_summary` from a project's discrete
`project_memory_entries` (AD-P7), and the shared `schedule_regen` trigger
every memory-mutation path (route handlers today, agent-promotion and the
individual-chat hook later) fires it through.

LLM work is mocked at the module seam (`app.project_memory.call_md`) — the
same technique `test_group_chat_turns.py`/`test_artifact_chat_summary.py`
use for their own `call_md`/`llm_call` sites. A real-LLM round trip against
the local Supabase rig lives in `test_project_memory_live.py` (the ticket's
own real-LLM live tier); this suite proves the writer's CONTRACT — never
raises, upserts one row, triggers correctly, logs cleanly — not that Claude
itself will always honor the prompt's prose rules.
"""
from __future__ import annotations

import logging
import re
import time

from app import project_memory
from app.db import project_memory_entries as memory_db
from tests._company_helpers import company_client


def _create_project(ctx, *, name: str = "Memory synthesis project") -> dict:
    return ctx.client.post("/v1/projects", json={"name": name}).json()


def _make_fake_call_md(text: str):
    def _fake(*, system, user, model, meta_out=None, **kwargs):  # noqa: ARG001
        if meta_out is not None:
            meta_out.update(
                {
                    "model": model,
                    "input_tokens": 42,
                    "output_tokens": 17,
                    "cache_creation_input_tokens": 0,
                    "cache_read_input_tokens": 0,
                }
            )
        return text

    return _fake


_COMPLIANT_SUMMARY = (
    "The team is locked on shipping dark mode by Friday, with telemetry "
    "left explicitly opt-in rather than defaulted on. Ship date pressure "
    "is real but the privacy guardrail is not up for negotiation. "
    "Everything else about the rollout is still open."
)


# ── property-check helpers (test-local — NOT shipped in project_memory.py;
# they validate the WRITER'S round-trip + the PROMPT'S own stated rules,
# not that a real model call will always comply — that's the live tier) ──


def _no_headings_or_bullets(text: str) -> bool:
    for line in text.splitlines():
        s = line.strip()
        if s.startswith("#") or s.startswith("- ") or s.startswith("* "):
            return False
    return True


def _no_trailing_question_or_offer(text: str) -> bool:
    stripped = text.strip()
    if stripped.endswith("?"):
        return False
    sentences = [s for s in re.split(r"(?<=[.!?])\s+", stripped) if s]
    if not sentences:
        return True
    last = sentences[-1].lower()
    return not any(p in last for p in ("want me to", "shall i", "let me know"))


def _sentence_count(text: str) -> int:
    return len([s for s in re.split(r"(?<=[.!?])\s+", text.strip()) if s])


# ── Creation ─────────────────────────────────────────────────────────────


def test_regenerate_summary_writes_prose_row(isolated_settings, monkeypatch):
    ctx = company_client(monkeypatch)
    project = _create_project(ctx)
    memory_db.add_entry(project["id"], body="Ship dark mode by Friday.", author_user_id=ctx.user_id)
    memory_db.add_entry(
        project["id"], body="Never auto-enable telemetry.", author_user_id=ctx.user_id
    )

    seen = {}

    def fake_call_md(*, system, user, model, meta_out=None, **kwargs):  # noqa: ARG001
        seen.update({"system": system, "user": user, "model": model})
        if meta_out is not None:
            meta_out.update(
                {
                    "model": model, "input_tokens": 50, "output_tokens": 20,
                    "cache_creation_input_tokens": 0, "cache_read_input_tokens": 0,
                }
            )
        return _COMPLIANT_SUMMARY

    monkeypatch.setattr(project_memory, "call_md", fake_call_md)

    result = project_memory.regenerate_summary(project["id"])
    assert result == _COMPLIANT_SUMMARY

    from app.db.client import require_client

    rows = (
        require_client()
        .table("project_memory_summary")
        .select("*")
        .eq("project_id", project["id"])
        .execute()
        .data
    )
    assert len(rows) == 1
    row = rows[0]
    assert row["summary_md"] == result
    assert row["entry_count"] == 2
    assert row["stale"] is False
    assert row["generated_at"]
    assert seen["model"] == project_memory.DEFAULT_MODEL
    # The source material — not the model's own required OUTPUT shape — is
    # what's fed as the user turn.
    assert "Ship dark mode by Friday." in seen["user"]
    assert "Never auto-enable telemetry." in seen["user"]


def test_regenerate_summary_upserts_single_row(isolated_settings, monkeypatch):
    ctx = company_client(monkeypatch)
    project = _create_project(ctx)
    memory_db.add_entry(project["id"], body="First guardrail.", author_user_id=ctx.user_id)

    replies = iter(
        [
            "First synthesis line lands here. It reads as three sentences "
            "total. This closes the thought out cleanly.",
            "Updated synthesis reflects the new entry too. It stays "
            "interpretive throughout. Nothing here trails off unfinished.",
        ]
    )

    def fake_call_md(*, system, user, model, meta_out=None, **kwargs):  # noqa: ARG001
        if meta_out is not None:
            meta_out.update(
                {
                    "model": model, "input_tokens": 1, "output_tokens": 1,
                    "cache_creation_input_tokens": 0, "cache_read_input_tokens": 0,
                }
            )
        return next(replies)

    monkeypatch.setattr(project_memory, "call_md", fake_call_md)

    first = project_memory.regenerate_summary(project["id"])
    memory_db.add_entry(project["id"], body="Second guardrail.", author_user_id=ctx.user_id)
    second = project_memory.regenerate_summary(project["id"])
    assert first != second

    from app.db.client import require_client

    rows = (
        require_client()
        .table("project_memory_summary")
        .select("*")
        .eq("project_id", project["id"])
        .execute()
        .data
    )
    assert len(rows) == 1, "second regen must UPDATE the existing row, not insert a duplicate"
    assert rows[0]["summary_md"] == second
    assert rows[0]["entry_count"] == 2


# ── Prompt property (length + content + negative-space) ────────────────


def test_synthesis_no_headings_or_bullets(isolated_settings, monkeypatch):
    assert "No markdown headings, no bullet lists" in project_memory._SYSTEM

    ctx = company_client(monkeypatch)
    project = _create_project(ctx)
    memory_db.add_entry(project["id"], body="Guardrail one.", author_user_id=ctx.user_id)
    monkeypatch.setattr(project_memory, "call_md", _make_fake_call_md(_COMPLIANT_SUMMARY))

    result = project_memory.regenerate_summary(project["id"])
    assert _no_headings_or_bullets(result)

    # Negative-space: the checker itself must actually catch a violation —
    # proves this isn't a vacuously-true assertion.
    assert not _no_headings_or_bullets("# A heading\nSome text follows.")
    assert not _no_headings_or_bullets("- a bullet point\nmore text")
    assert not _no_headings_or_bullets("* another bullet style\nmore text")


def test_synthesis_no_trailing_question_or_offer(isolated_settings, monkeypatch):
    assert "Do NOT end with a question or an offer" in project_memory._SYSTEM

    ctx = company_client(monkeypatch)
    project = _create_project(ctx)
    memory_db.add_entry(project["id"], body="Guardrail one.", author_user_id=ctx.user_id)
    monkeypatch.setattr(project_memory, "call_md", _make_fake_call_md(_COMPLIANT_SUMMARY))

    result = project_memory.regenerate_summary(project["id"])
    assert _no_trailing_question_or_offer(result)

    assert not _no_trailing_question_or_offer("Everything is ready to ship?")
    assert not _no_trailing_question_or_offer("All set here. Want me to ship it now?")
    assert not _no_trailing_question_or_offer("Ready to go. Let me know if you want changes.")


def test_synthesis_sentence_count_bounded(isolated_settings, monkeypatch):
    assert "3-5 sentences" in project_memory._SYSTEM

    ctx = company_client(monkeypatch)
    project = _create_project(ctx)
    memory_db.add_entry(project["id"], body="Guardrail one.", author_user_id=ctx.user_id)
    monkeypatch.setattr(project_memory, "call_md", _make_fake_call_md(_COMPLIANT_SUMMARY))

    result = project_memory.regenerate_summary(project["id"])
    assert 3 <= _sentence_count(result) <= 5

    # Negative-space: the counter itself correctly rejects out-of-range prose.
    assert not (3 <= _sentence_count("One sentence only.") <= 5)
    assert not (
        3
        <= _sentence_count("One. Two. Three. Four. Five. Six. Seven sentences here.")
        <= 5
    )


# ── Error handling (mutation-proofed) ───────────────────────────────────


def test_regenerate_summary_swallows_llm_failure(isolated_settings, monkeypatch):
    ctx = company_client(monkeypatch)
    project = _create_project(ctx)
    memory_db.add_entry(project["id"], body="Existing guardrail.", author_user_id=ctx.user_id)

    from app.db.client import require_client

    require_client().table("project_memory_summary").insert(
        {
            "project_id": project["id"],
            "summary_md": "Last-good summary, untouched.",
            "entry_count": 1,
            "stale": False,
        }
    ).execute()
    before = (
        require_client()
        .table("project_memory_summary")
        .select("*")
        .eq("project_id", project["id"])
        .execute()
        .data[0]
    )

    def boom(**kwargs):  # noqa: ARG001
        raise RuntimeError("gateway down")

    monkeypatch.setattr(project_memory, "call_md", boom)

    result = project_memory.regenerate_summary(project["id"])  # must not raise
    assert result is None

    after = (
        require_client()
        .table("project_memory_summary")
        .select("*")
        .eq("project_id", project["id"])
        .execute()
        .data[0]
    )
    assert after == before, "the last-good row must be byte-identical after a failed regen"


def test_regenerate_summary_zero_entries_deletes_row(isolated_settings, monkeypatch):
    ctx = company_client(monkeypatch)
    project = _create_project(ctx)

    from app.db.client import require_client

    require_client().table("project_memory_summary").insert(
        {
            "project_id": project["id"],
            "summary_md": "Stale summary of entries that no longer exist.",
            "entry_count": 3,
            "stale": True,
        }
    ).execute()

    called: list[dict] = []
    monkeypatch.setattr(project_memory, "call_md", lambda **kw: called.append(kw))

    result = project_memory.regenerate_summary(project["id"])
    assert result is None
    assert called == [], "zero entries must make NO LLM call"

    rows = (
        require_client()
        .table("project_memory_summary")
        .select("*")
        .eq("project_id", project["id"])
        .execute()
        .data
    )
    assert rows == []

    fallback = memory_db.get_summary(project["id"])
    assert fallback == {"summary_md": None, "entry_count": 0, "stale": False}


# ── Trigger (`schedule_regen` primitive) ────────────────────────────────


def test_schedule_regen_inline_under_pytest(isolated_settings, monkeypatch):
    ctx = company_client(monkeypatch)
    project = _create_project(ctx)
    memory_db.add_entry(project["id"], body="Inline trigger guardrail.", author_user_id=ctx.user_id)

    from app.db.client import require_client

    require_client().table("project_memory_summary").insert(
        {"project_id": project["id"], "summary_md": "Old.", "entry_count": 0, "stale": True}
    ).execute()

    monkeypatch.setattr(project_memory, "call_md", _make_fake_call_md(_COMPLIANT_SUMMARY))

    project_memory.schedule_regen(project["id"])  # runs synchronously under pytest

    row = (
        require_client()
        .table("project_memory_summary")
        .select("*")
        .eq("project_id", project["id"])
        .execute()
        .data[0]
    )
    assert row["summary_md"] == _COMPLIANT_SUMMARY
    assert row["stale"] is False


async def test_schedule_regen_dedups_concurrent(monkeypatch):
    """Forces the PROD (loop-scheduled) branch via the `_run_inline_for_tests`
    seam — see its docstring — rather than touching `sys.modules` directly."""
    calls: list[int] = []

    def fake_regen(project_id: int) -> str:
        calls.append(project_id)
        time.sleep(0.05)  # runs in a worker thread (asyncio.to_thread) — safe to block
        return "ok"

    monkeypatch.setattr(project_memory, "regenerate_summary", fake_regen)
    monkeypatch.setattr(project_memory, "_run_inline_for_tests", lambda: False)

    project_memory.schedule_regen(7)
    project_memory.schedule_regen(7)  # a regen is already in flight for 7 — no-op
    assert len(project_memory._inflight) == 1, "concurrent schedule must not queue a second task"

    task = project_memory._inflight[7]
    await task

    assert calls == [7], "the in-flight regen must not be re-triggered while running"
    assert project_memory._inflight == {}, "the done-callback must clear the inflight entry"


def test_schedule_regen_never_raises_bare_sync(monkeypatch):
    """A plain sync test function (this one) has no running event loop, so
    with the pytest-inline shortcut also disabled, `schedule_regen` must
    fall through to the bare-sync inline branch and still never raise."""
    calls: list[int] = []

    def fake_regen(project_id: int) -> None:
        calls.append(project_id)

    monkeypatch.setattr(project_memory, "regenerate_summary", fake_regen)
    monkeypatch.setattr(project_memory, "_run_inline_for_tests", lambda: False)

    project_memory.schedule_regen(99)  # must not raise despite no running loop
    assert calls == [99]


def test_add_memory_triggers_regen(isolated_settings, monkeypatch):
    ctx = company_client(monkeypatch)
    project = _create_project(ctx)
    monkeypatch.setattr(project_memory, "call_md", _make_fake_call_md(_COMPLIANT_SUMMARY))

    r = ctx.client.post(f"/v1/projects/{project['id']}/memory", json={"body": "New insight via HTTP"})
    assert r.status_code == 200

    from app.db.client import require_client

    row = (
        require_client()
        .table("project_memory_summary")
        .select("*")
        .eq("project_id", project["id"])
        .execute()
        .data[0]
    )
    assert row["summary_md"] == _COMPLIANT_SUMMARY
    assert row["entry_count"] == 1
    assert row["stale"] is False


def test_edit_delete_memory_triggers_regen(isolated_settings, monkeypatch):
    ctx = company_client(monkeypatch)
    project = _create_project(ctx)
    monkeypatch.setattr(project_memory, "call_md", _make_fake_call_md(_COMPLIANT_SUMMARY))

    entry = ctx.client.post(
        f"/v1/projects/{project['id']}/memory", json={"body": "Original"}
    ).json()

    from app.db.client import require_client

    # Force stale so the edit path clearing it is the thing under test, not
    # a side effect of the add above.
    require_client().table("project_memory_summary").update({"stale": True}).eq(
        "project_id", project["id"]
    ).execute()

    r_edit = ctx.client.patch(
        f"/v1/projects/{project['id']}/memory/{entry['id']}", json={"body": "Edited"}
    )
    assert r_edit.status_code == 200
    row = (
        require_client()
        .table("project_memory_summary")
        .select("*")
        .eq("project_id", project["id"])
        .execute()
        .data[0]
    )
    assert row["stale"] is False
    assert row["entry_count"] == 1

    require_client().table("project_memory_summary").update({"stale": True}).eq(
        "project_id", project["id"]
    ).execute()

    r_delete = ctx.client.delete(f"/v1/projects/{project['id']}/memory/{entry['id']}")
    assert r_delete.status_code == 200

    # Zero entries left after the delete — the row is deleted entirely, not
    # just left with stale=False (matches the zero-entries writer contract).
    rows = (
        require_client()
        .table("project_memory_summary")
        .select("*")
        .eq("project_id", project["id"])
        .execute()
        .data
    )
    assert rows == []


# ── Observability ────────────────────────────────────────────────────────


def test_synthesis_emits_cost_log_no_body(isolated_settings, monkeypatch, caplog):
    ctx = company_client(monkeypatch)
    project = _create_project(ctx)
    memory_db.add_entry(
        project["id"], body="SECRET_ENTRY_BODY_DO_NOT_LOG", author_user_id=ctx.user_id
    )

    reply_text = (
        "SECRET_SUMMARY_TEXT_DO_NOT_LOG stays interpretive throughout. It "
        "never repeats verbatim entry text in a log line. This closes the "
        "thought cleanly."
    )
    monkeypatch.setattr(project_memory, "call_md", _make_fake_call_md(reply_text))

    with caplog.at_level(logging.INFO, logger="app.llm_telemetry"):
        result = project_memory.regenerate_summary(project["id"])
    assert result == reply_text

    cost_lines = [
        rec.getMessage() for rec in caplog.records if "projects.memory.synthesis" in rec.getMessage()
    ]
    assert len(cost_lines) == 1, "exactly one cost-summary line per regen"
    assert f"project_id={project['id']}" in cost_lines[0]
    assert "status=complete" in cost_lines[0]
    assert "est_cost_usd=" in cost_lines[0]

    joined = "\n".join(rec.getMessage() for rec in caplog.records)
    assert "SECRET_ENTRY_BODY_DO_NOT_LOG" not in joined
    assert "SECRET_SUMMARY_TEXT_DO_NOT_LOG" not in joined
