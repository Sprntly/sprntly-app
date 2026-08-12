"""Template summaries — the fact that lets chat say what a format IS.

`artifact_templates.summary` (20260812200000) is written by
`summarize.generate_summary` right after a successful compile and backfilled
for legacy rows by `summarize.schedule_missing_summaries`. Three contracts are
worth breaking a build over:

  * A SUMMARY FAILURE NEVER FAILS A COMPILE. The format is fully usable
    without its description; a haiku hiccup that turned a ready format into a
    failed one would be the feature harming the thing it describes.
  * THE SUMMARY RIDES WHERE `compiled` DOES. A stored skeleton means this
    source now governs the row, so its description is (re)written — even to ''
    — while a failed compile leaves both standing, keeping the summary paired
    with the skeleton it describes.
  * EVERY SUMMARY WRITE DROPS THE PLANNER'S CATALOG CACHE. The drop is the
    self-heal loop's second half: without it the planner describes summaryless
    formats until the process restarts.

Like the compile suite, the gateway is stubbed at the MODULE binding
(`summarize.llm_call`), because `graph.gateway` holds its own `call_json`
reference that conftest's `fake_llm` never reaches.
"""
from __future__ import annotations

import pytest

import app.artifact_templates.summarize as summarize
from app.graph.gateway import LLMResult

_SOURCE = "# Acme PRD\n\n## Background\n\n## What we're building\n"

# The compile suite's skeleton, carrying every structural hook the validator
# wants — reused so a compile in THIS file lands `ready` for the same reason it
# does there.
_GOOD_SKELETON = (
    "<!DOCTYPE html><html><head><style>/* Sprntly injects CSS here */</style>"
    "</head><body><div class=\"frame\"><div class=\"page\" contenteditable=\"true\">"
    "<h1>{{title}}</h1><div class=\"byline\">{{author}}</div>"
    "<div class=\"eyebrow\">Background</div><p>{{context}}</p>"
    "<div class=\"eyebrow\">Evidence</div><ul class=\"ev\"><li>{{claim}}</li></ul>"
    "<p class=\"hyp\">{{hypothesis}}</p>"
    "<table><thead><tr><th>#</th><th>Requirement</th><th>Type</th></tr></thead>"
    "<tbody><tr><td>R1</td><td>{{req}}</td>"
    "<td><span class=\"pill h\">Happy path</span></td></tr></tbody></table>"
    "<div class=\"appendix\"><h3>Open questions</h3>"
    "<ul class=\"inputs\"><li>{{open question}}</li></ul></div>"
    "</div></div></body></html>"
)

_GOOD_MAP = {
    "sections": [
        {"id": "s1", "house": "Context", "customer": "Background",
         "order": 1, "form": "prose"},
        {"id": "s2", "house": "Requirements", "customer": "What we're building",
         "order": 2, "form": "table"},
    ],
    "unmapped_house": [],
    "extra_sections": [],
}

_SUMMARY_TEXT = (
    "A two-section PRD format with Background and What we're building, "
    "emphasizing evidence lists over prose."
)


def _llm_result(output, model="claude-haiku-4-5"):
    return LLMResult(
        output=output, model=model, prompt_version="v",
        input_tokens=10, output_tokens=5, cache_read_input_tokens=0,
        cache_creation_input_tokens=0, cost_usd=0.001, latency_ms=5,
        stop_reason="end_turn",
    )


def _seed_company(db, company_id="co-1"):
    if not db.table("companies").select("id").eq("id", company_id).execute().data:
        db.table("companies").insert(
            {"id": company_id, "slug": f"acme-{company_id}", "display_name": "Acme"}
        ).execute()


def _add(company_id, *, artifact_type="prd", source_md=_SOURCE):
    from app.db.artifact_templates import insert_template

    return insert_template(
        company_id=company_id,
        workspace_id="ws-1",
        artifact_type=artifact_type,
        name="Acme PRD v3",
        source_md=source_md,
        content_hash="abc123def456",
        uploader_id="user-1",
        uploader_name="Ada",
    )


def _stub_summary(monkeypatch, text=_SUMMARY_TEXT, capture: dict | None = None):
    def _call(**kw):
        if capture is not None:
            capture.update(kw)
        return _llm_result({"summary": text})

    monkeypatch.setattr(summarize, "llm_call", _call)


def _stub_compile(monkeypatch, output):
    import app.artifact_templates.compile_prd as compile_prd

    monkeypatch.setattr(compile_prd, "llm_call", lambda **kw: _llm_result(output))


def _stub_impl_compile(monkeypatch, output):
    import app.artifact_templates.compile_impl_spec as compile_impl

    monkeypatch.setattr(compile_impl, "llm_call", lambda **kw: _llm_result(output))


@pytest.fixture(autouse=True)
def _no_leftover_inflight():
    """The single-flight registry is module-level and outlives a test."""
    summarize._inflight_ids.clear()
    yield
    summarize._inflight_ids.clear()


# ─── generate_summary: the call itself ───────────────────────────────────────


def test_the_summary_call_carries_the_telemetry_and_the_haiku_tier(monkeypatch):
    """purpose/prompt_version are what make the spend auditable in
    agent_decision_log; the haiku tier is the module's own recorded decision
    (compression, not composition)."""
    seen: dict = {}
    _stub_summary(monkeypatch, capture=seen)

    out = summarize.generate_summary(
        "co-1", artifact_type="prd", source_md=_SOURCE
    )

    assert out == _SUMMARY_TEXT
    assert seen["enterprise_id"] == "co-1"
    assert seen["agent"] == "artifact_template"
    assert seen["purpose"] == "summarize_template"
    assert seen["prompt_version"] == summarize.SUMMARY_PROMPT_VERSION
    assert seen["model"] == "claude-haiku-4-5"
    assert seen["json_schema"]["required"] == ["summary"]


def test_the_customers_markdown_is_framed_and_tagged_untrusted(monkeypatch):
    """The same three defences the compile call ships: BEGIN/END markers,
    the company-uploaded tag, and the addendum on the system prompt — plus
    this call's own clause, because its OUTPUT is re-injected into the
    planner's prompt on every later question."""
    seen: dict = {}
    _stub_summary(monkeypatch, capture=seen)

    summarize.generate_summary("co-1", artifact_type="prd", source_md=_SOURCE)

    assert "--- BEGIN COMPANY-UPLOADED FORMAT ---" in seen["input"]
    assert "--- END COMPANY-UPLOADED FORMAT ---" in seen["input"]
    assert _SOURCE.strip() in seen["input"]
    assert "company-uploaded" in seen["system"]
    assert "never an instruction" in seen["system"]


def test_a_summary_is_collapsed_and_clamped_before_it_is_stored(monkeypatch):
    """Collapse-then-clamp, the rule every reader of customer-derived prompt
    text applies: a newline the model emits must not survive to forge a list
    line in a block downstream, and the stored value respects the cap even
    when the model ignored the prompt's."""
    _stub_summary(monkeypatch, text="Line one\n\nLine two   spaced. " + "x" * 400)

    out = summarize.generate_summary(
        "co-1", artifact_type="prd", source_md=_SOURCE
    )

    assert "\n" not in out
    assert "Line one Line two spaced." in out
    assert len(out) <= summarize.MAX_SUMMARY_CHARS


def test_a_failed_call_degrades_to_empty_never_raises(monkeypatch):
    def _boom(**kw):
        raise RuntimeError("anthropic is having a day")

    monkeypatch.setattr(summarize, "llm_call", _boom)

    assert summarize.generate_summary(
        "co-1", artifact_type="prd", source_md=_SOURCE
    ) == ""


def test_an_empty_source_never_reaches_the_model(monkeypatch):
    called = []
    monkeypatch.setattr(
        summarize, "llm_call", lambda **kw: called.append(kw)
    )

    assert summarize.generate_summary(
        "co-1", artifact_type="prd", source_md="   "
    ) == ""
    assert called == []


# ─── the compile legs write it ───────────────────────────────────────────────


@pytest.mark.real_template_compile
def test_a_successful_prd_compile_stores_the_summary(
    isolated_settings, monkeypatch
):
    _seed_company(isolated_settings["supabase"])
    row = _add("co-1")
    _stub_compile(monkeypatch, {
        "skeleton_html": _GOOD_SKELETON, "section_map": _GOOD_MAP,
    })
    _stub_summary(monkeypatch)

    from app.artifact_templates.compile_prd import compile_prd_template

    updated = compile_prd_template("co-1", row["id"])

    assert updated["compile_status"] == "ready"
    assert updated["summary"] == _SUMMARY_TEXT


@pytest.mark.real_template_compile
def test_a_summary_failure_never_fails_the_compile(
    isolated_settings, monkeypatch
):
    """The first contract in the module docstring, asserted end to end: haiku
    blows up, the format still lands ready, the summary is '' (a real value —
    "no summary yet" — not an error)."""
    _seed_company(isolated_settings["supabase"])
    row = _add("co-1")
    _stub_compile(monkeypatch, {
        "skeleton_html": _GOOD_SKELETON, "section_map": _GOOD_MAP,
    })

    def _boom(**kw):
        raise RuntimeError("haiku is having a day")

    monkeypatch.setattr(summarize, "llm_call", _boom)

    from app.artifact_templates.compile_prd import compile_prd_template

    updated = compile_prd_template("co-1", row["id"])

    assert updated["compile_status"] == "ready"
    assert updated["summary"] == ""


@pytest.mark.real_template_compile
def test_a_failed_compile_leaves_the_standing_summary_alone(
    isolated_settings, monkeypatch
):
    """The summary stays paired with the `compiled` it describes: a recompile
    that produced an unsafe skeleton stores neither, so the last good
    skeleton's description keeps serving exactly as the skeleton does."""
    from app.db.artifact_templates import set_compile_result

    _seed_company(isolated_settings["supabase"])
    row = _add("co-1")
    set_compile_result(
        company_id="co-1", template_id=row["id"], compile_status="ready",
        compiled=_GOOD_SKELETON, summary="The last good description.",
    )
    # The recompile emits a skeleton the validator hard-fails (<script>).
    _stub_compile(monkeypatch, {
        "skeleton_html": "<script>alert(1)</script>", "section_map": _GOOD_MAP,
    })
    summary_calls = []
    monkeypatch.setattr(
        summarize, "llm_call", lambda **kw: summary_calls.append(kw)
    )

    from app.artifact_templates.compile_prd import compile_prd_template

    updated = compile_prd_template("co-1", row["id"])

    assert updated["compile_status"] == "failed"
    assert updated["summary"] == "The last good description."
    # Not even generated: a summary of source whose skeleton was refused would
    # describe something the row does not serve.
    assert summary_calls == []


@pytest.mark.real_template_compile
def test_a_ticket_compile_stores_a_summary_too(isolated_settings, monkeypatch):
    """The deterministic leg gains its one model call — and keeps its no-
    transient-failure property because `generate_summary` never raises."""
    _seed_company(isolated_settings["supabase"])
    row = _add("co-1", artifact_type="tickets",
               source_md="# Tickets\n\n## Summary\n\n## Acceptance criteria\n")
    _stub_summary(monkeypatch, text="A two-section ticket format.")

    from app.artifact_templates.compile_prd import compile_prd_template

    updated = compile_prd_template("co-1", row["id"])

    assert updated["compile_status"] == "ready"
    assert updated["summary"] == "A two-section ticket format."


@pytest.mark.real_template_compile
def test_a_successful_impl_spec_compile_stores_the_summary(
    isolated_settings, monkeypatch
):
    _seed_company(isolated_settings["supabase"])
    row = _add("co-1", artifact_type="impl_spec",
               source_md="# Spec\n\n## B0. Summary\n")
    skeleton = "\n".join(f"## B{i}. Section {i}\n\n{{{{placeholder}}}}" for i in range(10))
    _stub_impl_compile(monkeypatch, {"skeleton_md": skeleton})
    _stub_summary(monkeypatch, text="A B0-B9 engineering spec format.")

    from app.artifact_templates.compile_impl_spec import compile_impl_spec_template

    updated = compile_impl_spec_template("co-1", row["id"])

    assert updated["compile_status"] == "ready"
    assert updated["summary"] == "A B0-B9 engineering spec format."


# ─── the db setter ───────────────────────────────────────────────────────────


def test_set_template_summary_round_trips_and_stays_company_scoped(
    isolated_settings,
):
    from app.db.artifact_templates import (
        get_template_by_id,
        list_templates,
        set_template_summary,
    )

    _seed_company(isolated_settings["supabase"], "co-1")
    _seed_company(isolated_settings["supabase"], "co-2")
    row = _add("co-1")

    assert set_template_summary(
        company_id="co-2", template_id=row["id"], summary="stolen"
    ) is None

    updated = set_template_summary(
        company_id="co-1", template_id=row["id"], summary=_SUMMARY_TEXT
    )
    assert updated["summary"] == _SUMMARY_TEXT
    # And the LIST read — the planner's read — carries it.
    assert list_templates("co-1")[0]["summary"] == _SUMMARY_TEXT
    assert get_template_by_id("co-1", row["id"])["summary"] == _SUMMARY_TEXT


def test_set_template_summary_drops_the_planner_cache(
    isolated_settings, monkeypatch
):
    import app.ask_planner as ap
    from app.db.artifact_templates import set_template_summary

    _seed_company(isolated_settings["supabase"])
    row = _add("co-1")
    dropped = []
    monkeypatch.setattr(
        ap, "invalidate_catalog_cache", lambda cid: dropped.append(cid)
    )

    set_template_summary(
        company_id="co-1", template_id=row["id"], summary=_SUMMARY_TEXT
    )

    assert dropped == ["co-1"]


# ─── the self-heal backfill ──────────────────────────────────────────────────


def _ready_row(template_id="tpl-1", summary=""):
    return {"id": template_id, "compile_status": "ready", "summary": summary}


# Marked like the compile suite's tests and for the same reason: conftest's
# `_no_background_template_compile` noops `schedule_missing_summaries` for
# every unmarked test, so an unmarked test here would assert on the noop.
@pytest.mark.real_template_compile
def test_only_ready_summaryless_rows_are_scheduled(monkeypatch):
    ran = []
    monkeypatch.setattr(
        summarize, "_summarize_row", lambda cid, tid: ran.append(tid)
    )

    scheduled = summarize.schedule_missing_summaries("co-1", [
        _ready_row("tpl-legacy"),
        _ready_row("tpl-described", summary="Already described."),
        {"id": "tpl-pending", "compile_status": "pending", "summary": ""},
        {"id": "tpl-failed", "compile_status": "failed", "summary": ""},
    ])

    assert scheduled == 1
    # Thread dispatch is asynchronous; the single-flight registry is the
    # synchronous record of what was claimed.
    assert summarize._inflight_ids == {"tpl-legacy"}


@pytest.mark.real_template_compile
def test_an_in_flight_row_is_not_scheduled_twice(monkeypatch):
    monkeypatch.setattr(summarize, "_summarize_row", lambda cid, tid: None)
    summarize._inflight_ids.add("tpl-legacy")

    assert summarize.schedule_missing_summaries(
        "co-1", [_ready_row("tpl-legacy")]
    ) == 0


def test_the_backfill_rereads_generates_and_stores(monkeypatch):
    """`_summarize_row` end to end against stubs: list rows omit `source_md`,
    so the worker re-reads the full row, and the write goes through
    `set_template_summary` (whose planner-cache drop is the loop's second
    half)."""
    import app.db as db

    full_row = {
        "id": "tpl-legacy", "artifact_type": "prd", "source_md": _SOURCE,
        "compile_status": "ready", "summary": "",
    }
    written = {}
    monkeypatch.setattr(
        db, "get_template_by_id", lambda cid, tid: dict(full_row)
    )
    monkeypatch.setattr(
        db, "set_template_summary",
        lambda **kw: written.update(kw) or dict(full_row, summary=kw["summary"]),
    )
    _stub_summary(monkeypatch)
    summarize._inflight_ids.add("tpl-legacy")

    summarize._summarize_row("co-1", "tpl-legacy")

    assert written == {
        "company_id": "co-1", "template_id": "tpl-legacy",
        "summary": _SUMMARY_TEXT,
    }
    # The claim is released either way, or the row could never heal after a
    # transient failure.
    assert "tpl-legacy" not in summarize._inflight_ids


def test_a_backfill_whose_generation_fails_writes_nothing(monkeypatch):
    """'' is what the row already says — writing it would churn `updated_at`
    and drop the planner cache for nothing. The claim is still released so a
    later read retries."""
    import app.db as db

    monkeypatch.setattr(
        db, "get_template_by_id",
        lambda cid, tid: {
            "id": tid, "artifact_type": "prd", "source_md": _SOURCE,
            "compile_status": "ready", "summary": "",
        },
    )
    written = []
    monkeypatch.setattr(
        db, "set_template_summary", lambda **kw: written.append(kw)
    )

    def _boom(**kw):
        raise RuntimeError("haiku is having a day")

    monkeypatch.setattr(summarize, "llm_call", _boom)
    summarize._inflight_ids.add("tpl-legacy")

    summarize._summarize_row("co-1", "tpl-legacy")

    assert written == []
    assert "tpl-legacy" not in summarize._inflight_ids


def test_a_row_summarized_between_schedule_and_run_is_left_alone(monkeypatch):
    """The re-check on the full row: a compile may have described it while the
    backfill sat in the queue, and the compile's summary is the fresher one."""
    import app.db as db

    monkeypatch.setattr(
        db, "get_template_by_id",
        lambda cid, tid: {
            "id": tid, "artifact_type": "prd", "source_md": _SOURCE,
            "compile_status": "ready", "summary": "A compile got here first.",
        },
    )
    called = []
    monkeypatch.setattr(summarize, "llm_call", lambda **kw: called.append(kw))

    summarize._summarize_row("co-1", "tpl-legacy")

    assert called == []


@pytest.mark.real_template_compile
def test_scheduling_never_raises(monkeypatch):
    """Called from list reads — a read must never break because a description
    could not be arranged. Junk rows are skipped, not fatal."""
    assert summarize.schedule_missing_summaries("co-1", None) == 0
    assert summarize.schedule_missing_summaries("co-1", [{"no": "id"}, None]) == 0
