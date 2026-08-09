"""Tests for GET /v1/reports/{id} — the report document the viewer opens.

The artifact listing omits `html` (it would carry N full documents), so this is
where the body comes from. Covers the tenant gate (a foreign report reads as
missing, so it 404s exactly like a nonexistent id) and the attachment fields the
viewer's header renders.
"""
from __future__ import annotations

from tests._company_helpers import company_client, seed_company, supabase_bearer

VOC_HTML = (
    "<!DOCTYPE html><html><head><title>Voice of Customer Report</title></head>"
    "<body><h1>Voice of Customer Report</h1></body></html>"
)


def _seed_report(*, company_id: str, skill: str = "voice-of-customer-report",
                 title: str = "VoC", html: str = VOC_HTML, question: str = "",
                 conversation_id: int | None = None,
                 prd_id: int | None = None) -> int:
    from app.db.client import require_client
    resp = require_client().table("reports").insert({
        "company_id": company_id,
        "skill": skill,
        "title": title,
        "html": html,
        "question": question,
        "conversation_id": conversation_id,
        "prd_id": prd_id,
    }).execute()
    return resp.data[0]["id"]


def test_requires_auth(unauth_client, isolated_settings):
    assert unauth_client.get("/v1/reports/1").status_code == 401


def test_returns_the_document_and_its_attachment(isolated_settings, monkeypatch):
    ctx = company_client(monkeypatch)
    rid = _seed_report(
        company_id=ctx.company_id,
        question="what are customers saying?",
        conversation_id=42,
        prd_id=7,
    )

    r = ctx.client.get(f"/v1/reports/{rid}")
    assert r.status_code == 200
    body = r.json()
    assert body["id"] == rid
    assert body["skill"] == "voice-of-customer-report"
    assert body["title"] == "VoC"
    assert body["question"] == "what are customers saying?"
    assert body["html"] == VOC_HTML, "the viewer renders this verbatim"
    assert body["conversation_id"] == 42
    assert body["prd_id"] == 7


def test_unattached_report_returns_null_attachment(isolated_settings, monkeypatch):
    ctx = company_client(monkeypatch)
    rid = _seed_report(company_id=ctx.company_id)

    body = ctx.client.get(f"/v1/reports/{rid}").json()
    assert body["conversation_id"] is None
    assert body["prd_id"] is None


def test_404_on_missing_report(isolated_settings, monkeypatch):
    ctx = company_client(monkeypatch)
    assert ctx.client.get("/v1/reports/999999").status_code == 404


def test_foreign_report_404s_rather_than_403s(isolated_settings, monkeypatch):
    """No cross-tenant existence disclosure: another company's report must be
    indistinguishable from an id that never existed."""
    ctx = company_client(monkeypatch)
    other_company_id = seed_company(user_id="intruder", slug="rival")
    rival_rid = _seed_report(company_id=other_company_id, title="Rival VoC")

    r = ctx.client.get(f"/v1/reports/{rival_rid}")
    assert r.status_code == 404

    # And the rival can read their own — proving the row exists and the 404
    # above is the tenant gate, not a missing seed.
    own = ctx.client.get(
        f"/v1/reports/{rival_rid}", headers=supabase_bearer("intruder")
    )
    assert own.status_code == 200
    assert own.json()["title"] == "Rival VoC"


# ─── Per-thread listing (the chat panel's Reports tab) ───────────────────────


def test_lists_the_reports_captured_in_one_thread_newest_first(isolated_settings, monkeypatch):
    ctx = company_client(monkeypatch)
    first = _seed_report(company_id=ctx.company_id, title="VoC v1", conversation_id=42)
    second = _seed_report(company_id=ctx.company_id, title="VoC v2", conversation_id=42)
    _seed_report(company_id=ctx.company_id, title="Other thread", conversation_id=43)
    _seed_report(company_id=ctx.company_id, title="Unattached")

    r = ctx.client.get("/v1/reports?conversation_id=42")
    assert r.status_code == 200
    reports = r.json()["reports"]
    assert [x["id"] for x in reports] == [second, first], "newest first"
    assert [x["title"] for x in reports] == ["VoC v2", "VoC v1"]
    assert reports[0]["skill"] == "voice-of-customer-report"
    assert reports[0]["conversation_id"] == 42
    assert reports[0]["share_mode"] == "private"


def test_listing_omits_the_bodies(isolated_settings, monkeypatch):
    """A list of N reports must not carry N full documents — the reader opens
    one, and GET /v1/reports/{id} serves that one."""
    ctx = company_client(monkeypatch)
    _seed_report(company_id=ctx.company_id, conversation_id=42)

    reports = ctx.client.get("/v1/reports?conversation_id=42").json()["reports"]
    assert reports
    assert "html" not in reports[0]


def test_thread_with_no_reports_lists_empty(isolated_settings, monkeypatch):
    ctx = company_client(monkeypatch)
    _seed_report(company_id=ctx.company_id, conversation_id=42)

    assert ctx.client.get("/v1/reports?conversation_id=999").json()["reports"] == []


def test_listing_is_company_scoped(isolated_settings, monkeypatch):
    """A conversation id guessed from another tenant reads as an empty thread,
    never as their reports."""
    ctx = company_client(monkeypatch)
    other_company_id = seed_company(user_id="intruder", slug="rival")
    _seed_report(company_id=other_company_id, title="Rival VoC", conversation_id=42)

    assert ctx.client.get("/v1/reports?conversation_id=42").json()["reports"] == []

    # The rival sees their own — proving the row exists and the empty list above
    # is the tenant gate, not a missing seed.
    theirs = ctx.client.get(
        "/v1/reports?conversation_id=42", headers=supabase_bearer("intruder")
    ).json()["reports"]
    assert [x["title"] for x in theirs] == ["Rival VoC"]


def test_listing_requires_a_conversation_id(isolated_settings, monkeypatch):
    """Without the filter this would be an unbounded company-wide report dump —
    that listing is GET /v1/artifacts, not this route."""
    ctx = company_client(monkeypatch)
    assert ctx.client.get("/v1/reports").status_code == 422


def test_listing_requires_auth(unauth_client, isolated_settings):
    assert unauth_client.get("/v1/reports?conversation_id=42").status_code == 401


# ─── Report kinds (the "New report" picker) — REMOVED ────────────────────────
# `GET /v1/reports/kinds`, `app/report_kinds.py` and the web picker are gone
# with the pinned report formats. The picker offered exactly three of them
# (Voice of Customer / Competitor Analysis / Public Feedback) as fixed skills to
# pin; those formats no longer exist, so there is nothing left to pick between.
# The button was already dark behind `SHOW_NEW_REPORT_BUTTON = false`, so nothing
# user-facing changed. Reports are asked for in chat and captured from there —
# every test below this line covers that surviving path unchanged.


def test_kinds_route_is_gone(isolated_settings, monkeypatch):
    """The route must not still be serving.

    `/kinds` was a literal path declared BEFORE `/{report_id}` precisely so the
    int-typed dynamic segment could not shadow it. With the literal removed the
    dynamic route sees "kinds", fails int coercion, and FastAPI answers 422 —
    a fine answer for a path that no longer exists. What must not happen is a
    200, i.e. the picker quietly still being served."""
    ctx = company_client(monkeypatch)
    assert ctx.client.get("/v1/reports/kinds").status_code in (404, 422)


