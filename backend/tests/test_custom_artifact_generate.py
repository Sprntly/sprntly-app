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
    error_code      TEXT,
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


def _llm_result_with_output(output):
    """The same real dataclass, with an arbitrary `output` — for the case where
    the field is not the string this call path expects."""
    from app.graph.gateway import LLMResult

    return LLMResult(
        output=output, model=_MODEL_FOR_TESTS, prompt_version="test",
        input_tokens=0, output_tokens=0, cache_read_input_tokens=0,
        cache_creation_input_tokens=0, cost_usd=0.0, latency_ms=0,
        stop_reason="end_turn",
    )


def _llm_result(text: str):
    """A REAL `LLMResult`, not a look-alike.

    THIS IS THE WHOLE REASON THE FEATURE SHIPPED BROKEN. The stub used to be
    `SimpleNamespace(text=...)`, and the generator read `result.text` — an
    attribute `LLMResult` has never had. The fake defined the interface the code
    was wrong about, so all 20 tests in this file passed against a generator
    that raised AttributeError on its first real call, every time, for three
    days.

    Constructing the actual dataclass is what makes that impossible: rename or
    remove a field and this file stops importing. A hand-rolled double can only
    ever assert that the code agrees with the double.
    """
    return _llm_result_with_output(text)


_MODEL_FOR_TESTS = "claude-sonnet-4-6"


def _stub_llm(monkeypatch, text: str):
    monkeypatch.setattr(gen, "llm_call", lambda **kw: _llm_result(text))


# ─── The result contract (the bug that made this feature never work) ─────────

def test_the_generator_reads_the_field_LLMResult_actually_has(gen_env, monkeypatch):
    """THE REGRESSION, stated as a property rather than a mock expectation.

    The generator read `result.text`. `LLMResult` carries `output`, and has
    never carried `text` — so the first real call raised AttributeError AFTER
    the model had answered and been paid for, every single time. Nothing in the
    product said so, because the row went to `failed` with the reason in a
    column the API did not return.

    Passing the REAL dataclass is what makes this test meaningful: it fails
    against a generator reading any attribute the gateway does not actually
    return.
    """
    _stub_llm(monkeypatch, "<h1>Real</h1><p>from the output field</p>")
    doc_id = _pending(gen_env)

    gen.generate_into(company_id=gen_env, artifact_id=doc_id, kind="memo", task="t")

    row = get_artifact(gen_env, doc_id)
    assert row["status"] == "ready", f"generation failed: {row.get('error')}"
    assert "from the output field" in row["body_html"]


def test_a_non_text_result_fails_rather_than_stringifying_into_the_document(
    gen_env, monkeypatch
):
    """`str(output)` would be the obvious defensive coercion and it is the wrong
    one: a dict stringifies to a non-empty repr, passes the empty-output gate,
    and lands as a READY document whose body is `{'html': ...}` — titled from
    an <h1> that does not exist, and forwardable to someone's leadership. A
    garbage body is worse than none, because none is visible."""
    monkeypatch.setattr(
        gen, "llm_call",
        lambda **kw: _llm_result_with_output({"html": "<h1>T</h1><p>x</p>"}),
    )
    doc_id = _pending(gen_env)

    gen.generate_into(company_id=gen_env, artifact_id=doc_id, kind="memo", task="t")

    row = get_artifact(gen_env, doc_id)
    assert row["status"] == "failed"
    assert row["body_html"] == ""


def test_the_test_double_is_the_real_gateway_type():
    """Belt and braces on the lesson: if a future edit swaps this file's stub
    back to a hand-rolled object, this fails. A double that defines its own
    interface can only prove the code agrees with the double."""
    from app.graph.gateway import LLMResult

    assert isinstance(_llm_result("x"), LLMResult)


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


# ─── A failure says WHY, in a form the product can show ──────────────────────
#
# Every one of these asserts the CODE rather than the message: the code is the
# contract the API returns and the web maps to copy, and it is the half a person
# who asked for the document can actually be shown. `error` stays the operator's
# raw text and is never returned — the tests below check both halves land,
# because writing only one is how a failed generation stayed invisible.

def test_an_empty_generation_records_the_empty_code(gen_env, monkeypatch):
    _stub_llm(monkeypatch, "   ")
    doc_id = _pending(gen_env)
    gen.generate_into(company_id=gen_env, artifact_id=doc_id, kind="memo", task="t")
    assert get_artifact(gen_env, doc_id)["error_code"] == gen.FAILURE_EMPTY


def test_an_llm_failure_records_the_llm_code_and_keeps_the_raw_error(
    gen_env, monkeypatch
):
    monkeypatch.setattr(
        gen, "llm_call", lambda **kw: (_ for _ in ()).throw(RuntimeError("gateway down"))
    )
    doc_id = _pending(gen_env)
    gen.generate_into(company_id=gen_env, artifact_id=doc_id, kind="memo", task="t")
    row = get_artifact(gen_env, doc_id)
    assert row["error_code"] == gen.FAILURE_LLM
    # The operator's half is still there — the code replaces what the API
    # RETURNS, not what the row records.
    assert "gateway down" in (row["error"] or "")


def test_a_document_too_large_to_store_is_classified_as_such(gen_env, monkeypatch):
    """Distinct from a generic model failure, and the distinction is the whole
    point: asking again gets the same 400KB document, so the copy has to say
    "ask for a shorter one" rather than "try again"."""
    _stub_llm(monkeypatch, "<h1>T</h1><p>x</p>")

    def _too_large(*a, **kw):
        from app.db.custom_artifacts import BodyTooLarge

        raise BodyTooLarge("body is 2000000 chars (max 400000)")

    monkeypatch.setattr(gen, "finish_artifact", _too_large)
    doc_id = _pending(gen_env)
    gen.generate_into(company_id=gen_env, artifact_id=doc_id, kind="memo", task="t")
    row = get_artifact(gen_env, doc_id)
    assert row["status"] == "failed"
    assert row["error_code"] == gen.FAILURE_TOO_LARGE


def test_a_failure_AFTER_the_model_answered_is_not_blamed_on_the_model(
    gen_env, monkeypatch
):
    """THE DISTINCTION THAT MATTERS. The model answers, and then the write
    fails — a Supabase disconnect, an unparseable fragment. Reporting that as
    "the generator could not be reached" is a confident false statement about a
    generation that plainly succeeded, which is the exact failure mode this
    whole change exists to remove."""
    _stub_llm(monkeypatch, "<h1>T</h1><p>real content</p>")
    monkeypatch.setattr(
        gen, "finish_artifact",
        lambda *a, **kw: (_ for _ in ()).throw(RuntimeError("connection reset")),
    )
    doc_id = _pending(gen_env)

    gen.generate_into(company_id=gen_env, artifact_id=doc_id, kind="memo", task="t")

    assert get_artifact(gen_env, doc_id)["error_code"] == gen.FAILURE_STORAGE


def test_the_same_exception_classifies_differently_by_phase(gen_env, monkeypatch):
    """Same exception TYPE, opposite meaning, decided by which side of the model
    call it was raised on — so the phase bit is doing real work rather than
    being a second name for the exception type."""
    boom = lambda *a, **kw: (_ for _ in ()).throw(RuntimeError("connection reset"))

    monkeypatch.setattr(gen, "llm_call", boom)
    before = _pending(gen_env)
    gen.generate_into(company_id=gen_env, artifact_id=before, kind="memo", task="t")

    _stub_llm(monkeypatch, "<h1>T</h1><p>x</p>")
    monkeypatch.setattr(gen, "finish_artifact", boom)
    after = _pending(gen_env)
    gen.generate_into(company_id=gen_env, artifact_id=after, kind="memo", task="t")

    assert get_artifact(gen_env, before)["error_code"] == gen.FAILURE_LLM
    assert get_artifact(gen_env, after)["error_code"] == gen.FAILURE_STORAGE


def test_classification_is_by_type_not_by_message_text(gen_env, monkeypatch):
    """A provider that rewords its errors must not silently reclassify every
    failure. Nothing here reads the message, so an exception whose text SAYS
    "too large" is still a generic model failure unless its type says so."""
    monkeypatch.setattr(
        gen, "llm_call",
        lambda **kw: (_ for _ in ()).throw(RuntimeError("body is too large")),
    )
    doc_id = _pending(gen_env)
    gen.generate_into(company_id=gen_env, artifact_id=doc_id, kind="memo", task="t")
    assert get_artifact(gen_env, doc_id)["error_code"] == gen.FAILURE_LLM


def test_a_successful_generation_clears_a_previous_failures_code(
    gen_env, monkeypatch
):
    """A stale code on a row that has since succeeded is worse than a stale
    error: the API returns it, so the panel would say "could not be written"
    over a document that plainly was."""
    from app.db.custom_artifacts import fail_artifact

    doc_id = _pending(gen_env)
    fail_artifact(gen_env, doc_id, "gateway down", code=gen.FAILURE_LLM)
    _stub_llm(monkeypatch, "<h1>T</h1><p>second time lucky</p>")

    gen.generate_into(company_id=gen_env, artifact_id=doc_id, kind="memo", task="t")

    row = get_artifact(gen_env, doc_id)
    assert row["status"] == "ready"
    assert row["error_code"] is None
    assert row["error"] is None


# ─── The prompt carries the grounding rule ───────────────────────────────────

def test_context_is_passed_to_the_model_and_named_as_the_only_facts(
    gen_env, monkeypatch
):
    captured = {}

    def _capture(**kw):
        captured.update(kw)
        return _llm_result("<h1>T</h1><p>x</p>")

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
        lambda **kw: (captured.update(kw), _llm_result("<h1>T</h1><p>x</p>"))[1],
    )
    gen.generate_into(company_id=gen_env, artifact_id=_pending(gen_env),
                      kind="memo", task="t")
    assert "CONTEXT: none was supplied" in captured["input"]


# ─── Thin-context grounding fallback ─────────────────────────────────────────
#
# THE BUG. "Generate a report for the biggest issues we have, explain it" as a
# thread's first message reaches `generate_into` with only the seeded ack turn
# as context (~100 chars — the user's own question plus "Writing your issues
# report..."), so the document's own honesty rule correctly wrote "no factual
# grounding was available" for every section. The IDENTICAL question without
# the word "report" reached `qa_agent.answer` instead and answered correctly,
# because THAT path retrieves (call digest / voice-of-customer / the KG). These
# tests pin `_ground_thin_context`'s three behaviors and `generate_into`'s use
# of it: fires only when context is thin AND a dataset is present, folds a real
# answer in when one comes back, and changes nothing when it doesn't.

def test_grounding_is_skipped_with_no_dataset(gen_env, monkeypatch):
    """Backward compatibility: an older frontend build (or any caller) that
    omits `dataset` gets exactly today's behavior — no retrieval attempted,
    the original honest-empty-context path runs unchanged."""
    from app import qa_agent

    calls = []
    monkeypatch.setattr(qa_agent, "answer", lambda **kw: calls.append(kw) or {"answer": "should never be reached"})
    captured = {}
    monkeypatch.setattr(
        gen, "llm_call",
        lambda **kw: (captured.update(kw), _llm_result("<h1>T</h1><p>x</p>"))[1],
    )

    gen.generate_into(
        company_id=gen_env, artifact_id=_pending(gen_env),
        kind="issues report", task="our biggest issues",
    )

    assert calls == []
    assert "CONTEXT: none was supplied" in captured["input"]


def test_grounding_fires_on_thin_context_and_folds_the_real_answer_in(
    gen_env, monkeypatch
):
    """The fix, corrected once already — see
    `test_grounding_must_plan_before_answering_not_answer_unplanned` for the
    regression this pins. A cold-thread create with a dataset gets a real
    PLAN built first, then ONE retrieval pass through `qa_agent.answer`, and
    the result becomes the document's grounding — instead of the document
    honestly reporting a gap a plain question wouldn't have had."""
    from app import ask_planner, qa_agent

    plan_calls = []
    answer_calls = []
    _SENTINEL_PLAN = object()

    def _fake_plan(**kw):
        plan_calls.append(kw)
        return _SENTINEL_PLAN

    def _fake_answer(**kw):
        answer_calls.append(kw)
        return {"answer": "Export failures are 28.7% of the support queue this month."}

    monkeypatch.setattr(ask_planner, "plan_for_answer", _fake_plan)
    monkeypatch.setattr(qa_agent, "answer", _fake_answer)
    captured = {}
    monkeypatch.setattr(
        gen, "llm_call",
        lambda **kw: (captured.update(kw), _llm_result("<h1>T</h1><p>x</p>"))[1],
    )

    gen.generate_into(
        company_id=gen_env, artifact_id=_pending(gen_env),
        kind="issues report", task="our biggest issues, explain it",
        context="Q: our biggest issues, explain it\n\nA: Writing your issues report — it will open in the panel on the right.",
        dataset="acme",
    )

    assert len(plan_calls) == 1
    assert plan_calls[0]["enterprise_id"] == gen_env
    assert plan_calls[0]["question"] == "our biggest issues, explain it"

    assert len(answer_calls) == 1
    assert answer_calls[0]["dataset"] == "acme"
    assert answer_calls[0]["enterprise_id"] == gen_env
    assert answer_calls[0]["question"] == "our biggest issues, explain it"
    # THE ACTUAL BUG: `plan_for_answer`'s result must reach `qa_agent.answer`,
    # not get built and thrown away. `plan is object()` (identity, not
    # equality) so this fails if the plan is dropped OR silently swapped
    # for something ELSE that happens to compare equal.
    assert answer_calls[0]["plan"] is _SENTINEL_PLAN
    assert "Export failures are 28.7%" in captured["input"]
    # Never silently overwritten: the caller's own (thin) context survives
    # alongside the grounded answer, not replaced by it.
    assert "Writing your issues report" in captured["input"]


def test_grounding_is_skipped_when_context_is_already_substantive(
    gen_env, monkeypatch
):
    """A document drafted from a real conversation is unaffected — retrieval
    would answer a different question from the one already discussed, exactly
    as the module's grounding-source rule says. No extra LLM call, no extra
    latency, for the case that was never broken."""
    from app import qa_agent

    calls = []
    monkeypatch.setattr(qa_agent, "answer", lambda **kw: calls.append(kw) or {"answer": "unused"})
    monkeypatch.setattr(gen, "llm_call", lambda **kw: _llm_result("<h1>T</h1><p>x</p>"))

    rich_context = "Prior turn established the export bug in detail. " * 10
    assert len(rich_context) >= gen._THIN_CONTEXT_CHARS

    gen.generate_into(
        company_id=gen_env, artifact_id=_pending(gen_env),
        kind="leadership update", task="write it up",
        context=rich_context, dataset="acme",
    )

    assert calls == []


def test_grounding_failure_falls_back_to_the_original_honest_path(
    gen_env, monkeypatch
):
    """Best-effort by contract: a raising retrieval must not fail the
    generation or leak an exception past `generate_into`'s total-by-contract
    boundary — it can only ADD grounding, never remove the fallback that
    already existed."""
    from app import qa_agent

    def _boom(**kw):
        raise RuntimeError("KG unreachable")

    monkeypatch.setattr(qa_agent, "answer", _boom)
    captured = {}
    monkeypatch.setattr(
        gen, "llm_call",
        lambda **kw: (captured.update(kw), _llm_result("<h1>T</h1><p>x</p>"))[1],
    )

    doc_id = _pending(gen_env)
    gen.generate_into(
        company_id=gen_env, artifact_id=doc_id,
        kind="issues report", task="our biggest issues", dataset="acme",
    )

    row = get_artifact(gen_env, doc_id)
    assert row["status"] == "ready"
    assert "CONTEXT: none was supplied" in captured["input"]


def test_grounding_with_no_task_is_skipped(gen_env, monkeypatch):
    """`_ground_thin_context` needs a real question to ask — an empty task has
    nothing for `qa_agent.answer` to route, and calling it with "" would ask a
    different, meaningless question."""
    from app import qa_agent

    calls = []
    monkeypatch.setattr(qa_agent, "answer", lambda **kw: calls.append(kw) or {"answer": "x"})

    assert gen._ground_thin_context(company_id=gen_env, dataset="acme", task="   ") == ""
    assert calls == []


def test_grounding_with_an_empty_qa_answer_changes_nothing(gen_env, monkeypatch):
    """A retrieval that ran but genuinely had nothing to say ("" or whitespace)
    is not different from one that never ran — the document falls back to its
    own honest-empty-context copy either way."""
    from app import qa_agent

    monkeypatch.setattr(qa_agent, "answer", lambda **kw: {"answer": "   "})
    captured = {}
    monkeypatch.setattr(
        gen, "llm_call",
        lambda **kw: (captured.update(kw), _llm_result("<h1>T</h1><p>x</p>"))[1],
    )

    gen.generate_into(
        company_id=gen_env, artifact_id=_pending(gen_env),
        kind="issues report", task="our biggest issues", dataset="acme",
    )
    assert "CONTEXT: none was supplied" in captured["input"]


def test_grounding_must_plan_before_answering_not_answer_unplanned(
    gen_env, monkeypatch
):
    """THE ACTUAL BUG, caught live on staging AFTER this whole feature had
    already merged. `_ground_thin_context`'s first cut called
    `qa_agent.answer(plan=None, ...)` on the theory that `plan=None` is "the
    same router every unplanned chat question already goes through" — a
    misreading of `qa_agent.answer`'s own docstring. There IS no live
    unplanned caller: `ask_job_runner._single_shot` — the ONLY path a real
    chat question ever takes — calls `ask_planner.plan_for_answer` FIRST and
    passes its result. `plan=None` is what a PLANNER OUTAGE degrades to, not
    a path a healthy question takes.

    Live proof, reproduced via staging Chrome tools: the identical imperative
    task text, asked plainly through chat (which plans it), returned a
    10,044-character grounded answer citing the workspace's real support and
    revenue data. The SAME text, sent through the un-fixed
    `_ground_thin_context` (`plan=None`), returned nothing — the document
    still wrote "no factual grounding was available". Not a data problem;
    the missing plan was the entire gap.

    This test pins the fix by asserting the CALL ORDER a mock can prove:
    `ask_planner.plan_for_answer` must be called, and BEFORE `qa_agent.answer`
    — proving the plan could not have been built from something the answer
    call itself produced, and could not have been skipped."""
    from app import ask_planner, qa_agent

    order = []
    monkeypatch.setattr(
        ask_planner, "plan_for_answer",
        lambda **kw: order.append("plan") or object(),
    )
    monkeypatch.setattr(
        qa_agent, "answer",
        lambda **kw: order.append("answer") or {"answer": "grounded"},
    )
    monkeypatch.setattr(gen, "llm_call", lambda **kw: _llm_result("<h1>T</h1><p>x</p>"))

    gen.generate_into(
        company_id=gen_env, artifact_id=_pending(gen_env),
        kind="issues report", task="our biggest issues", dataset="acme",
    )

    assert order == ["plan", "answer"]


def test_a_raising_planner_still_falls_back_safely(gen_env, monkeypatch):
    """`plan_for_answer` documents itself as never raising (a planner outage
    returns None, its own fail-open contract) — this asserts the belt as well
    as the braces: even if that contract were ever violated, grounding is
    still best-effort and the generation still succeeds honestly, exactly
    like a raising `qa_agent.answer` already must (see
    test_grounding_failure_falls_back_to_the_original_honest_path)."""
    from app import ask_planner, qa_agent

    def _boom(**kw):
        raise RuntimeError("planner outage")

    monkeypatch.setattr(ask_planner, "plan_for_answer", _boom)
    called_answer = []
    monkeypatch.setattr(
        qa_agent, "answer", lambda **kw: called_answer.append(kw) or {"answer": "x"},
    )
    captured = {}
    monkeypatch.setattr(
        gen, "llm_call",
        lambda **kw: (captured.update(kw), _llm_result("<h1>T</h1><p>x</p>"))[1],
    )

    doc_id = _pending(gen_env)
    gen.generate_into(
        company_id=gen_env, artifact_id=doc_id,
        kind="issues report", task="our biggest issues", dataset="acme",
    )

    row = get_artifact(gen_env, doc_id)
    assert row["status"] == "ready"
    # The whole plan+answer pair is one try/except: a raising planner never
    # reaches the answer call, and the document falls back to its original
    # honest-empty-context copy.
    assert called_answer == []
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
    # The one failure the product can speak about with certainty — nothing is
    # writing this row, so "ask again" is genuinely the right advice.
    assert row["error_code"] == gen.FAILURE_INTERRUPTED


def test_the_age_gate_outlasts_the_longest_possible_healthy_generation(gen_env):
    """THE GATE IS NOT A PREFERENCE, it is arithmetic.

    These rows carry no heartbeat, so age is the only signal the sweep has — and
    now that the sweep RECURS every 5 minutes it is pointed at live generations
    owned by this very process, not just at rows left by a dead one. A gate
    shorter than the longest healthy run marks a document failed WHILE IT IS
    STILL WRITING; the user is shown a failure, and then `finish_artifact` lands
    the document afterwards and flips the row to ready. Telling someone their
    document died and then silently producing it is worse than either outcome.

    One call can take MAX_ATTEMPTS × LONG_REQUEST_TIMEOUT_S before backoff, and
    it queues behind every other generation on the shared LLM gate.
    """
    from app.llm import LONG_REQUEST_TIMEOUT_S, MAX_ATTEMPTS

    worst_case_minutes = MAX_ATTEMPTS * LONG_REQUEST_TIMEOUT_S / 60
    assert gen.ORPHAN_AFTER_MINUTES > worst_case_minutes


def test_a_generation_at_the_old_thirty_minute_mark_is_left_alone(gen_env):
    """The regression in concrete terms: a document 45 minutes into a retrying
    generation is HEALTHY, and the old 30-minute gate would have failed it."""
    doc_id = _pending(gen_env)
    _age(gen_env, doc_id, 45)
    assert gen.sweep_orphan_generating() == 0
    assert get_artifact(gen_env, doc_id)["status"] == "generating"


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
