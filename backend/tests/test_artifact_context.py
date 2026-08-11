"""Tests for app.artifact_context — the standalone-artifact chat grounding.

`prd_context` gives a PRD-tab chat its document; these builders do the same
for the tabs that hold an artifact WITHOUT a PRD — an evidence report and a
standalone ticket set. Contract under test, mirroring test_prd_context:

- happy path renders the block, with script/style noise stripped from v3
  HTML evidence bodies and every ticket's body + acceptance criteria present
- ownership is enforced INSIDE the builder: a foreign company gets ''
- missing rows / no tenant → '' (best-effort, never raises)
- the ask route 404s a foreign or unknown id before any job starts, and a
  granted id reaches qa_agent.answer, whose grounding block carries the
  document (end-to-end through POST /v1/ask)
"""
from __future__ import annotations

from app.artifact_context import build_evidence_context, build_ticket_set_context


def _seed_evidence(db, *, slug: str, evidence_id: int, payload_md: str) -> None:
    brief = (
        db.table("briefs")
        .insert(
            {
                "dataset": slug,
                "week_label": "W",
                "payload": {"insights": []},
                "is_current": True,
            }
        )
        .execute()
        .data[0]
    )
    db.table("evidences").insert(
        {
            "id": evidence_id,
            "brief_id": brief["id"],
            "insight_index": 0,
            "title": "Churn signal evidence",
            "payload_md": payload_md,
            "status": "ready",
            "variant": "v3",
        }
    ).execute()


def _seed_ticket_set(db, *, company_id: str, set_id: int) -> None:
    db.table("ticket_sets").insert(
        {
            "id": set_id,
            "company_id": company_id,
            "status": "ready",
            "title": "Webhook retries",
            "source_text": "break this into tickets",
            "stories": [
                {
                    "id": "s1",
                    "title": "Retry the webhook with backoff",
                    "body": "Exponential backoff, max 5 attempts",
                    "acceptance_criteria": ["retries stop after success"],
                    "priority": "high",
                },
                {
                    "id": "s2",
                    "title": "Surface the failure in the audit log",
                    "body": "",
                    "acceptance_criteria": [],
                },
            ],
        }
    ).execute()


# ── evidence builder ─────────────────────────────────────────────────────────


def test_evidence_block_renders_body_with_noise_stripped(tenant_client, isolated_settings):
    t = tenant_client.make(slug="acme")
    db = isolated_settings["supabase"]
    _seed_evidence(
        db,
        slug="acme",
        evidence_id=71,
        payload_md=(
            "<html><style>.x{color:red}</style><script>alert('no')</script>"
            "<body><h2>Finding 2</h2><p>Zendesk: 14 tickets mention export.</p></body></html>"
        ),
    )

    block = build_evidence_context(t.company_id, 71)

    assert "CURRENT EVIDENCE CONTEXT" in block
    assert "Churn signal evidence" in block
    assert "Zendesk: 14 tickets mention export." in block
    # Noise is stripped, not merely tolerated — it would eat the section cap.
    assert "alert(" not in block and "color:red" not in block


def test_evidence_block_is_empty_for_a_foreign_company(tenant_client, isolated_settings):
    tenant_client.make(slug="acme")
    other = tenant_client.make(slug="globex", user_id="u-globex")
    db = isolated_settings["supabase"]
    _seed_evidence(db, slug="acme", evidence_id=72, payload_md="<p>Private.</p>")

    # The builder re-checks ownership itself — the worker path has no route
    # gate in front of it, so '' here is the difference between a degrade and
    # a cross-tenant prompt leak.
    assert build_evidence_context(other.company_id, 72) == ""


def test_evidence_block_degrades_on_missing_row_or_tenant(tenant_client):
    t = tenant_client.make(slug="acme")
    assert build_evidence_context(t.company_id, 999) == ""
    assert build_evidence_context(None, 1) == ""
    assert build_evidence_context(t.company_id, None) == ""


# ── ticket-set builder ───────────────────────────────────────────────────────


def test_ticket_set_block_renders_every_ticket(tenant_client, isolated_settings):
    t = tenant_client.make(slug="acme")
    db = isolated_settings["supabase"]
    _seed_ticket_set(db, company_id=t.company_id, set_id=7)

    block = build_ticket_set_context(t.company_id, 7)

    assert "CURRENT TICKET SET CONTEXT" in block
    assert "Webhook retries" in block
    assert "Tickets: 2" in block
    assert "Ticket 1: Retry the webhook with backoff" in block
    assert "Exponential backoff, max 5 attempts" in block
    assert "- retries stop after success" in block
    assert "Priority: high" in block
    # A ticket with no body/criteria still appears — every ticket is listed.
    assert "Ticket 2: Surface the failure in the audit log" in block


def test_ticket_set_block_is_empty_for_a_foreign_company(tenant_client, isolated_settings):
    t = tenant_client.make(slug="acme")
    other = tenant_client.make(slug="globex", user_id="u-globex")
    db = isolated_settings["supabase"]
    _seed_ticket_set(db, company_id=t.company_id, set_id=8)

    # get_set filters company_id in the query — foreign and missing are the
    # same '' here, exactly as they are the same 404 at the route.
    assert build_ticket_set_context(other.company_id, 8) == ""


# ── the ask route, end to end ────────────────────────────────────────────────


def test_ask_404s_a_foreign_or_unknown_artifact_id(tenant_client, isolated_settings):
    t = tenant_client.make(slug="acme")
    other = tenant_client.make(slug="globex", user_id="u-globex")
    db = isolated_settings["supabase"]
    _seed_evidence(db, slug="acme", evidence_id=73, payload_md="<p>Ours.</p>")
    _seed_ticket_set(db, company_id=t.company_id, set_id=9)

    # A crafted id must die at the route — 404, never 403, and never a job.
    r = other.client.post(
        "/v1/ask", json={"question": "what does this say?", "dataset": "globex", "evidence_id": 73}
    )
    assert r.status_code == 404
    r = other.client.post(
        "/v1/ask", json={"question": "what does this say?", "dataset": "globex", "ticket_set_id": 9}
    )
    assert r.status_code == 404
    r = t.client.post(
        "/v1/ask", json={"question": "what does this say?", "dataset": "acme", "evidence_id": 999}
    )
    assert r.status_code == 404


def test_ask_grounds_on_the_open_evidence(tenant_client, isolated_settings, monkeypatch):
    """The whole wire: evidence_id on POST /v1/ask → run_ask_job → qa_agent —
    whose answer call receives the evidence block as its grounding context."""
    import time

    import app.ask_runner as ask_runner

    t = tenant_client.make(slug="acme")
    db = isolated_settings["supabase"]
    _seed_evidence(
        db, slug="acme", evidence_id=74,
        payload_md="<p>Finding: enterprise churn doubled after the pricing change.</p>",
    )

    captured: dict = {}
    real_compose = ask_runner.compose_ask_answer

    def _spy(*args, **kwargs):
        captured["prd_context"] = kwargs.get("prd_context", "")
        return real_compose(*args, **kwargs)

    monkeypatch.setattr(ask_runner, "compose_ask_answer", _spy)
    # qa_agent binds its own reference at import time — patch both names.
    import app.qa_agent as qa

    monkeypatch.setattr(qa, "compose_ask_answer", _spy)

    r = t.client.post(
        "/v1/ask",
        json={"question": "what is the strongest signal here?", "dataset": "acme", "evidence_id": 74},
    )
    assert r.status_code == 200, r.text
    ask_id = r.json()["ask_id"]
    deadline = time.monotonic() + 5
    while time.monotonic() < deadline:
        body = t.client.get(f"/v1/ask/{ask_id}").json()
        if body["status"] != "generating":
            break
        time.sleep(0.02)
    assert body["status"] == "ready", body

    assert "CURRENT EVIDENCE CONTEXT" in captured.get("prd_context", "")
    assert "enterprise churn doubled" in captured["prd_context"]
