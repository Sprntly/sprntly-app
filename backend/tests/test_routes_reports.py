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


# ─── Report kinds (the "New report" picker) ──────────────────────────────────


def test_kinds_lists_the_offered_reports_with_their_prompts(isolated_settings, monkeypatch):
    ctx = company_client(monkeypatch)

    r = ctx.client.get("/v1/reports/kinds")
    assert r.status_code == 200
    kinds = r.json()["kinds"]
    assert kinds, "the picker must offer something"
    by_skill = {k["skill"]: k for k in kinds}
    assert "voice-of-customer-report" in by_skill
    assert "competitive-intelligence-review" in by_skill
    voc = by_skill["voice-of-customer-report"]
    assert voc["label"] == "Voice of Customer"
    assert voc["blurb"]
    # The prompt rides along so the client can start the run through the ordinary
    # ask pipeline with the skill pinned.
    assert "voice of customer" in voc["prompt"].lower()


def test_kinds_only_offers_skills_that_exist(isolated_settings, monkeypatch):
    """A vendored skill can be renamed or removed; a picker entry whose skill is
    gone would be a button that only fails once clicked."""
    ctx = company_client(monkeypatch)
    import app.report_kinds as rk

    monkeypatch.setattr(rk, "list_skills", lambda: ["competitive-intelligence-review"], raising=False)
    monkeypatch.setattr(
        "app.skills.loader.list_skills",
        lambda: ["competitive-intelligence-review"],
    )

    skills = [k["skill"] for k in ctx.client.get("/v1/reports/kinds").json()["kinds"]]
    assert skills == ["competitive-intelligence-review"]


def test_kinds_route_is_not_shadowed_by_the_id_route(isolated_settings, monkeypatch):
    """`/kinds` is a literal path declared before `/{report_id}` — if the dynamic
    route won, this would 404 (or 422) instead of listing."""
    ctx = company_client(monkeypatch)
    assert ctx.client.get("/v1/reports/kinds").status_code == 200


def test_kinds_requires_auth(unauth_client, isolated_settings):
    assert unauth_client.get("/v1/reports/kinds").status_code == 401
