"""Report capture — the chat→artifact hop for skill-generated HTML reports.

Covers the three decisions the capture makes: IS this answer a report document,
IS it a report that belongs in the library (vs one that already has an artifact
home), and WHAT is it attached to. Plus the best-effort contract: nothing here
may ever raise into the ask worker.
"""
from __future__ import annotations

from app import report_capture as rc

VOC_HTML = (
    "<!DOCTYPE html>\n"
    "<html><head><title>Voice of Customer Report · 1 Mar – 30 Jun 2026</title></head>"
    "<body><h1>Voice of Customer Report</h1><p>TL;DR …</p></body></html>"
)


def _payload(answer: str, skill: str | None = "voice-of-customer-report") -> dict:
    """An Ask-shaped payload, as qa_agent._tag leaves it."""
    return {
        "answer": answer, "key_points": [], "citations": [],
        "confidence": 0.6, "unanswered": "", "_skill": skill,
    }


# ─── the document sniff (must agree with web/app/lib/htmlBrief.ts) ───────────


def test_looks_like_html_report_accepts_document_openers():
    for opener in (
        "<!DOCTYPE html><html></html>",
        "<html><body>x</body></html>",
        "<meta charset='utf-8'><div>x</div>",
        "<div class='report'>x</div>",
        "<style>.a{}</style><div>x</div>",
        "  \n<!doctype html><html></html>",  # leading whitespace tolerated
    ):
        assert rc.looks_like_html_report(opener), opener


def test_looks_like_html_report_unwraps_a_stray_code_fence():
    fenced = "```html\n<!DOCTYPE html><html><body>x</body></html>\n```"
    assert rc.looks_like_html_report(fenced)


def test_looks_like_html_report_rejects_markdown_and_empty():
    for text in (
        "## Themes\n\n- Onboarding friction",
        "Here is your report: <div>x</div>",  # prose first — not a document
        "",
        None,
    ):
        assert not rc.looks_like_html_report(text), text


# ─── the title (denormalised so listing never parses HTML) ───────────────────


def test_report_title_prefers_the_title_tag():
    assert rc.report_title(VOC_HTML, "voice-of-customer-report") == (
        "Voice of Customer Report · 1 Mar – 30 Jun 2026"
    )


def test_report_title_falls_back_to_h1_then_skill_label():
    h1_only = "<div><h1>Competitive Review — Q3</h1></div>"
    assert rc.report_title(h1_only, "competitive-intelligence-review") == (
        "Competitive Review — Q3"
    )
    # Neither <title> nor <h1>: the skill's humanised label.
    assert rc.report_title("<div>no headings</div>", "voice-of-customer-report") == (
        "Voice of customer report"
    )


def test_report_title_strips_inner_tags_and_decodes_entities():
    messy = "<h1>Voice of <span>Customer</span>\n  &amp; Support &#8212; 2026</h1>"
    assert rc.report_title(messy, "voice-of-customer-report") == (
        "Voice of Customer & Support — 2026"
    )


def test_report_title_skips_an_empty_title_tag_and_caps_length():
    assert rc.report_title("<title>  </title><h1>Real One</h1>", "x-report") == "Real One"
    long_title = rc.report_title(f"<h1>{'A' * 500}</h1>", "x-report")
    assert len(long_title) == 200


# ─── capture: what gets written, and what is attached to it ──────────────────


def _patch_save(monkeypatch, result=42):
    """Capture the save_report kwargs instead of hitting the DB."""
    import app.db as db

    seen: dict = {}

    def fake_save(company_id, **kw):
        seen.update({"company_id": company_id, **kw})
        return result

    monkeypatch.setattr(db, "save_report", fake_save)
    return seen


def test_capture_writes_the_report_with_its_chat_and_prd_attachment(monkeypatch):
    seen = _patch_save(monkeypatch)

    report_id = rc.capture_report(
        _payload(VOC_HTML),
        company_id="c1",
        question="VoC for last quarter, enterprise only",
        workspace_id="w1",
        ask_id=99,
        conversation_id=42,
        prd_id=7,
    )

    assert report_id == 42
    assert seen["company_id"] == "c1"
    assert seen["workspace_id"] == "w1"
    assert seen["skill"] == "voice-of-customer-report"
    assert seen["title"] == "Voice of Customer Report · 1 Mar – 30 Jun 2026"
    assert seen["html"] == VOC_HTML
    assert seen["question"] == "VoC for last quarter, enterprise only"
    assert seen["ask_id"] == 99
    # The attachment: this report hangs off both the chat room and the PRD.
    assert seen["conversation_id"] == 42
    assert seen["prd_id"] == 7


def test_capture_stores_the_document_unfenced(monkeypatch):
    seen = _patch_save(monkeypatch)
    rc.capture_report(_payload(f"```html\n{VOC_HTML}\n```"), company_id="c1")
    assert seen["html"] == VOC_HTML, "a stray fence must not reach the viewer/PDF/share"


def test_capture_leaves_attachments_unset_when_the_ask_had_none(monkeypatch):
    seen = _patch_save(monkeypatch)
    rc.capture_report(_payload(VOC_HTML), company_id="c1")
    assert seen["conversation_id"] is None
    assert seen["prd_id"] is None
    assert seen["workspace_id"] is None


def test_capture_skips_answers_that_are_not_report_documents(monkeypatch):
    seen = _patch_save(monkeypatch)
    assert rc.capture_report(_payload("## Themes\n- friction"), company_id="c1") is None
    assert seen == {}, "a markdown answer must not become an artifact"


def test_capture_skips_skills_that_own_another_artifact_type(monkeypatch):
    seen = _patch_save(monkeypatch)
    # A chat-generated PRD is already a `prd` artifact; capturing it here would
    # list the same document twice under two types.
    assert rc.capture_report(
        _payload(VOC_HTML, skill="prd-author"), company_id="c1"
    ) is None
    assert seen == {}


def test_capture_skips_an_unrouted_answer(monkeypatch):
    seen = _patch_save(monkeypatch)
    assert rc.capture_report(_payload(VOC_HTML, skill=None), company_id="c1") is None
    assert seen == {}, "no skill = nothing to badge the artifact with"


def test_capture_skips_a_cancelled_ask(monkeypatch):
    seen = _patch_save(monkeypatch)
    out = rc.capture_report(
        _payload(VOC_HTML), company_id="c1", ask_id=5, is_cancelled=lambda: True
    )
    assert out is None
    assert seen == {}, "the user stopped this answer; it was never shown"


def test_cancellation_is_only_checked_for_report_answers(monkeypatch):
    """The check costs a DB read, so an ordinary markdown ask must not pay it."""
    _patch_save(monkeypatch)
    calls: list[int] = []

    rc.capture_report(
        _payload("## just markdown"),
        company_id="c1",
        is_cancelled=lambda: calls.append(1) or False,
    )
    assert calls == []


def test_capture_never_raises_when_the_save_fails(monkeypatch):
    import app.db as db

    def boom(*a, **k):
        raise RuntimeError("db down")

    monkeypatch.setattr(db, "save_report", boom)
    # Best-effort contract: the chat answer already rendered, so a failed save
    # degrades the library and nothing else.
    assert rc.capture_report(_payload(VOC_HTML), company_id="c1", ask_id=3) is None


# ─── worker wiring ───────────────────────────────────────────────────────────


async def test_run_ask_job_captures_with_the_asks_attachment_context(monkeypatch):
    from app import ask_job_runner as ajr

    monkeypatch.setattr(ajr.qa_agent, "answer", lambda **kw: _payload(VOC_HTML))
    monkeypatch.setattr(ajr, "complete_ask_job", lambda i, p: None)
    monkeypatch.setattr(ajr, "is_ask_cancelled", lambda i: False)

    seen: dict = {}

    def fake_capture(payload, **kw):
        seen.update({"answer": payload["answer"], **kw})
        return 1

    monkeypatch.setattr(ajr, "capture_report", fake_capture)

    await ajr.run_ask_job(
        ask_id=11,
        enterprise_id="c1",
        question="voc for q2",
        dataset="d",
        conversation_id=42,
        prd_id=7,
        workspace_id="w1",
    )

    assert seen["answer"] == VOC_HTML
    assert seen["company_id"] == "c1"
    assert seen["question"] == "voc for q2"
    assert seen["ask_id"] == 11
    assert seen["conversation_id"] == 42
    assert seen["prd_id"] == 7
    assert seen["workspace_id"] == "w1"


async def test_run_ask_job_persists_the_report_end_to_end(
    isolated_settings, monkeypatch
):
    """The whole hop with nothing mocked but the model: a report answer leaves a
    readable row, attached to the chat room it was generated in."""
    from app import ask_job_runner as ajr
    from app import db

    completed: dict = {}
    monkeypatch.setattr(ajr.qa_agent, "answer", lambda **kw: _payload(VOC_HTML))
    monkeypatch.setattr(ajr, "complete_ask_job", lambda i, p: completed.setdefault(i, p))
    monkeypatch.setattr(ajr, "is_ask_cancelled", lambda i: False)
    monkeypatch.setattr(ajr, "fail_ask_job", lambda i, m: completed.setdefault("failed", m))

    await ajr.run_ask_job(
        ask_id=12,
        enterprise_id="c1",
        question="what are customers saying?",
        dataset="d",
        conversation_id=42,
        workspace_id="w1",
    )

    assert completed[12]["answer"] == VOC_HTML, "the answer is stored regardless"
    assert "failed" not in completed, "capture must never mark the ask errored"

    from app.db.client import require_client

    rows = require_client().table("reports").select("id").eq("company_id", "c1").execute().data
    assert len(rows) == 1, "exactly one report captured"
    row = db.get_report(rows[0]["id"], "c1")
    assert row["skill"] == "voice-of-customer-report"
    assert row["title"] == "Voice of Customer Report · 1 Mar – 30 Jun 2026"
    assert row["html"] == VOC_HTML
    assert row["question"] == "what are customers saying?"
    assert row["ask_id"] == 12
    assert row["conversation_id"] == 42, "attached to the chat room it ran in"
    assert row["prd_id"] is None, "no PRD context on this ask"
    assert row["workspace_id"] == "w1"


async def test_run_ask_job_captures_nothing_for_a_markdown_answer(
    isolated_settings, monkeypatch
):
    from app import ask_job_runner as ajr
    from app.db.client import require_client

    monkeypatch.setattr(
        ajr.qa_agent, "answer", lambda **kw: _payload("## Themes\n- friction")
    )
    monkeypatch.setattr(ajr, "complete_ask_job", lambda i, p: None)
    monkeypatch.setattr(ajr, "is_ask_cancelled", lambda i: False)

    await ajr.run_ask_job(ask_id=13, enterprise_id="c1", question="q", dataset="d")

    rows = require_client().table("reports").select("id").execute().data
    assert rows == [], "an ordinary chat answer leaves no artifact behind"


# ─── the store ───────────────────────────────────────────────────────────────


def test_get_report_is_scoped_to_its_company(isolated_settings):
    """A foreign id reads as missing, so the route 404s rather than disclosing
    that another tenant's report exists."""
    from app import db

    rid = db.save_report(
        "c1", skill="voice-of-customer-report", title="VoC", html=VOC_HTML
    )
    assert db.get_report(rid, "c1")["title"] == "VoC"
    assert db.get_report(rid, "c2") is None
