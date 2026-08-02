"""Tests for report PDF download + the share-link surfaces.

Three things under test:
  - GET /v1/reports/{id}/pdf — server-rendered download, 503 (not a blank file)
    when the renderer is unavailable, per-user rate limit.
  - POST /v1/reports/{id}/share — opt-in sharing, default private, and the
    STATIC-URL invariant (a token survives public→private→public).
  - The no-auth /v1/public/reports/{token} surface — minimum disclosure, 404 for
    unknown/revoked, and the passcode gate.

Chromium never launches here: conftest's autouse `_no_real_browser_in_preview_capture`
stubs report_pdf's Playwright seam, so the render degrades unless a test patches
`render_report_pdf` itself.
"""
from __future__ import annotations

import pytest

from tests._company_helpers import company_client, seed_company, supabase_bearer

VOC_HTML = "<!DOCTYPE html><html><body><h1>Voice of Customer</h1></body></html>"


@pytest.fixture(autouse=True)
def _reset_report_limiters():
    """The share/pdf limiters are process-local module singletons, so counts leak
    between tests unless cleared."""
    from app.routes import reports, reports_public

    for limiter in (
        reports.PDF_LIMITER,
        reports_public.PUBLIC_READ_LIMITER,
        reports_public.PUBLIC_PASSCODE_LIMITER,
        reports_public.PUBLIC_PDF_LIMITER,
    ):
        limiter._events.clear()
    yield


def _seed_report(*, company_id: str, title: str = "VoC Q2", html: str = VOC_HTML) -> int:
    from app.db.client import require_client
    resp = require_client().table("reports").insert({
        "company_id": company_id,
        "skill": "voice-of-customer-report",
        "title": title,
        "html": html,
        "question": "what are customers saying?",
    }).execute()
    return resp.data[0]["id"]


def _patch_pdf(monkeypatch, payload: bytes | None = b"%PDF-1.4 fake"):
    """Stub the renderer (Chromium is unavailable in tests by design)."""
    async def fake_render(html: str):
        fake_render.calls.append(html)
        return payload

    fake_render.calls = []
    from app.routes import reports, reports_public

    monkeypatch.setattr(reports, "render_report_pdf", fake_render)
    monkeypatch.setattr(reports_public, "render_report_pdf", fake_render)
    return fake_render


# ─── PDF download (authenticated) ────────────────────────────────────────────


def test_pdf_download_returns_a_named_attachment(isolated_settings, monkeypatch):
    ctx = company_client(monkeypatch)
    render = _patch_pdf(monkeypatch)
    rid = _seed_report(company_id=ctx.company_id, title="Voice of Customer · Q2")

    r = ctx.client.get(f"/v1/reports/{rid}/pdf")
    assert r.status_code == 200
    assert r.headers["content-type"] == "application/pdf"
    # Filename is slugified from the title so the download is recognisable.
    assert r.headers["content-disposition"] == 'attachment; filename="voice-of-customer-q2.pdf"'
    assert r.headers["cache-control"] == "private, no-store"
    assert r.content == b"%PDF-1.4 fake"
    # The stored document is what gets rendered.
    assert render.calls == [VOC_HTML]


def test_pdf_download_503s_rather_than_returning_a_blank_file(isolated_settings, monkeypatch):
    ctx = company_client(monkeypatch)
    _patch_pdf(monkeypatch, payload=None)  # renderer unavailable / failed
    rid = _seed_report(company_id=ctx.company_id)

    r = ctx.client.get(f"/v1/reports/{rid}/pdf")
    assert r.status_code == 503
    assert "try again" in r.json()["detail"].lower()


def test_pdf_download_without_a_browser_degrades_to_503(isolated_settings, monkeypatch):
    """End-to-end through the real renderer with Playwright stubbed out by
    conftest: no Chromium, so it returns None and the route answers 503."""
    ctx = company_client(monkeypatch)
    rid = _seed_report(company_id=ctx.company_id)

    assert ctx.client.get(f"/v1/reports/{rid}/pdf").status_code == 503


def test_pdf_download_is_rate_limited_per_user(isolated_settings, monkeypatch):
    ctx = company_client(monkeypatch)
    _patch_pdf(monkeypatch)
    rid = _seed_report(company_id=ctx.company_id)

    from app.routes import reports
    for _ in range(reports.PDF_LIMITER._max):
        assert ctx.client.get(f"/v1/reports/{rid}/pdf").status_code == 200

    limited = ctx.client.get(f"/v1/reports/{rid}/pdf")
    assert limited.status_code == 429
    assert int(limited.headers["retry-after"]) > 0


def test_pdf_download_404s_for_a_foreign_report(isolated_settings, monkeypatch):
    ctx = company_client(monkeypatch)
    _patch_pdf(monkeypatch)
    other = seed_company(user_id="intruder", slug="rival")
    rid = _seed_report(company_id=other)

    assert ctx.client.get(f"/v1/reports/{rid}/pdf").status_code == 404


# ─── Share configuration (authenticated) ─────────────────────────────────────


def test_report_starts_private(isolated_settings, monkeypatch):
    ctx = company_client(monkeypatch)
    rid = _seed_report(company_id=ctx.company_id)

    body = ctx.client.get(f"/v1/reports/{rid}").json()
    assert body["share_mode"] == "private"
    assert body["share_token"] is None, "nothing is reachable by link until asked for"


def test_going_public_mints_a_token(isolated_settings, monkeypatch):
    ctx = company_client(monkeypatch)
    rid = _seed_report(company_id=ctx.company_id)

    r = ctx.client.post(f"/v1/reports/{rid}/share", json={"share_mode": "public"})
    assert r.status_code == 200
    assert r.json()["share_mode"] == "public"
    token = r.json()["share_token"]
    assert token

    # And the read surface now reports it.
    assert ctx.client.get(f"/v1/reports/{rid}").json()["share_token"] == token


def test_token_survives_public_private_public(isolated_settings, monkeypatch):
    """The static-URL invariant: pausing sharing must not invalidate a link that
    has already been handed to someone."""
    ctx = company_client(monkeypatch)
    rid = _seed_report(company_id=ctx.company_id)

    first = ctx.client.post(
        f"/v1/reports/{rid}/share", json={"share_mode": "public"}
    ).json()["share_token"]
    ctx.client.post(f"/v1/reports/{rid}/share", json={"share_mode": "private"})
    again = ctx.client.post(
        f"/v1/reports/{rid}/share", json={"share_mode": "public"}
    ).json()["share_token"]

    assert again == first


def test_private_hides_the_token_and_revokes_access(isolated_settings, monkeypatch):
    ctx = company_client(monkeypatch)
    rid = _seed_report(company_id=ctx.company_id)
    token = ctx.client.post(
        f"/v1/reports/{rid}/share", json={"share_mode": "public"}
    ).json()["share_token"]

    r = ctx.client.post(f"/v1/reports/{rid}/share", json={"share_mode": "private"})
    assert r.json() == {"share_mode": "private", "share_token": None}
    # The link stops resolving immediately, as a 404 (not a 403) — revoked and
    # never-existed look the same.
    assert ctx.client.get(f"/v1/public/reports/{token}").status_code == 404


def test_passcode_mode_requires_a_passcode(isolated_settings, monkeypatch):
    ctx = company_client(monkeypatch)
    rid = _seed_report(company_id=ctx.company_id)

    r = ctx.client.post(f"/v1/reports/{rid}/share", json={"share_mode": "passcode"})
    assert r.status_code == 400


def test_share_404s_for_a_foreign_report(isolated_settings, monkeypatch):
    ctx = company_client(monkeypatch)
    other = seed_company(user_id="intruder", slug="rival")
    rid = _seed_report(company_id=other)

    r = ctx.client.post(f"/v1/reports/{rid}/share", json={"share_mode": "public"})
    assert r.status_code == 404
    # The rival's report is still private — a failed cross-tenant share must not
    # have written anything.
    own = ctx.client.get(f"/v1/reports/{rid}", headers=supabase_bearer("intruder"))
    assert own.json()["share_mode"] == "private"


# ─── The public surface (no auth) ────────────────────────────────────────────


def _share(ctx, rid: int, mode: str, passcode: str | None = None) -> str:
    body: dict = {"share_mode": mode}
    if passcode:
        body["passcode"] = passcode
    return ctx.client.post(f"/v1/reports/{rid}/share", json=body).json()["share_token"]


def test_public_link_serves_the_document_without_auth(isolated_settings, monkeypatch):
    ctx = company_client(monkeypatch)
    rid = _seed_report(company_id=ctx.company_id, title="Voice of Customer · Q2")
    token = _share(ctx, rid, "public")

    # No Authorization header — this is the anonymous visitor.
    r = ctx.client.get(f"/v1/public/reports/{token}", headers={})
    assert r.status_code == 200
    body = r.json()
    assert body["title"] == "Voice of Customer · Q2"
    assert body["html"] == VOC_HTML
    assert body["kind"] == "Voice of customer report"


def test_public_projection_leaks_nothing_internal(isolated_settings, monkeypatch):
    ctx = company_client(monkeypatch)
    rid = _seed_report(company_id=ctx.company_id)
    token = _share(ctx, rid, "public")

    body = ctx.client.get(f"/v1/public/reports/{token}").json()
    # Exactly four fields — widening this set widens what a stranger can see.
    assert set(body) == {"title", "kind", "html", "created_at"}
    for leaked in ("id", "company_id", "workspace_id", "question",
                   "conversation_id", "prd_id", "share_token", "share_passcode_hash"):
        assert leaked not in body


def test_unknown_token_404s(isolated_settings, monkeypatch):
    company_client(monkeypatch)
    from app.main import app
    from fastapi.testclient import TestClient

    with TestClient(app) as anon:
        assert anon.get("/v1/public/reports/not-a-real-token").status_code == 404


def test_passcode_link_withholds_the_document_until_unlocked(isolated_settings, monkeypatch):
    ctx = company_client(monkeypatch)
    rid = _seed_report(company_id=ctx.company_id)
    token = _share(ctx, rid, "passcode", passcode="letmein")

    # The visitor holds a valid token but needs to be told what to do next — the
    # one place this surface answers something other than 404.
    gated = ctx.client.get(f"/v1/public/reports/{token}")
    assert gated.status_code == 401
    assert gated.json()["detail"] == "passcode_required"

    wrong = ctx.client.post(f"/v1/public/reports/{token}/unlock", json={"passcode": "nope"})
    assert wrong.status_code == 401
    assert "html" not in wrong.json()

    ok = ctx.client.post(f"/v1/public/reports/{token}/unlock", json={"passcode": "letmein"})
    assert ok.status_code == 200
    assert ok.json()["html"] == VOC_HTML


def test_unlock_attempts_are_rate_limited(isolated_settings, monkeypatch):
    ctx = company_client(monkeypatch)
    rid = _seed_report(company_id=ctx.company_id)
    token = _share(ctx, rid, "passcode", passcode="letmein")

    from app.routes import reports_public
    for _ in range(reports_public.PUBLIC_PASSCODE_LIMITER._max):
        ctx.client.post(f"/v1/public/reports/{token}/unlock", json={"passcode": "x"})

    blocked = ctx.client.post(f"/v1/public/reports/{token}/unlock", json={"passcode": "letmein"})
    assert blocked.status_code == 429, "the brute-force gate holds even for the right passcode"


def test_public_pdf_download(isolated_settings, monkeypatch):
    ctx = company_client(monkeypatch)
    _patch_pdf(monkeypatch)
    rid = _seed_report(company_id=ctx.company_id, title="VoC Q2")
    token = _share(ctx, rid, "public")

    r = ctx.client.post(f"/v1/public/reports/{token}/pdf", json={})
    assert r.status_code == 200
    assert r.headers["content-type"] == "application/pdf"
    assert r.headers["content-disposition"] == 'attachment; filename="voc-q2.pdf"'


def test_public_pdf_honours_the_passcode_gate(isolated_settings, monkeypatch):
    ctx = company_client(monkeypatch)
    _patch_pdf(monkeypatch)
    rid = _seed_report(company_id=ctx.company_id)
    token = _share(ctx, rid, "passcode", passcode="letmein")

    assert ctx.client.post(f"/v1/public/reports/{token}/pdf", json={}).status_code == 401
    assert ctx.client.post(
        f"/v1/public/reports/{token}/pdf", json={"passcode": "wrong"}
    ).status_code == 401
    ok = ctx.client.post(f"/v1/public/reports/{token}/pdf", json={"passcode": "letmein"})
    assert ok.status_code == 200


def test_private_report_pdf_is_not_publicly_reachable(isolated_settings, monkeypatch):
    ctx = company_client(monkeypatch)
    _patch_pdf(monkeypatch)
    rid = _seed_report(company_id=ctx.company_id)
    token = _share(ctx, rid, "public")
    ctx.client.post(f"/v1/reports/{rid}/share", json={"share_mode": "private"})

    assert ctx.client.post(f"/v1/public/reports/{token}/pdf", json={}).status_code == 404
