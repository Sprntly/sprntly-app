"""Tests for app.prd_context — the "CURRENT PRD CONTEXT" chat grounding block.

build_prd_context assembles the PRD open next to the chat (+ its source
insight, evidence, tickets, prototype) into one prompt block. Contract under
test:

- full chain renders every section, with script/style noise stripped from
  v3 HTML bodies
- ownership is re-checked inside the builder: a foreign company gets ''
- missing prd / no tenant → '' (best-effort, never raises)
- missing artifacts (no evidence/tickets/prototype, sentinel insight_index)
  skip their sections without dropping the PRD itself
"""
from __future__ import annotations

import uuid

from app.prd_context import build_prd_context


def _seed_chain(
    db,
    *,
    slug: str,
    prd_id: int,
    insight_index: int = 0,
    payload_md: str = "# The PRD body",
    insights: list | None = None,
):
    brief = (
        db.table("briefs")
        .insert(
            {
                "dataset": slug,
                "week_label": "W",
                "payload": {"insights": insights if insights is not None else []},
                "is_current": True,
            }
        )
        .execute()
        .data[0]
    )
    db.table("prds").insert(
        {
            "id": prd_id,
            "brief_id": brief["id"],
            "insight_index": insight_index,
            "title": "Export flow revamp",
            "status": "ready",
            "payload_md": payload_md,
        }
    ).execute()
    return brief


def test_full_chain_renders_all_sections(tenant_client, isolated_settings):
    t = tenant_client.make(slug="acme")
    db = isolated_settings["supabase"]
    brief = _seed_chain(
        db,
        slug="acme",
        prd_id=301,
        payload_md=(
            "<html><style>.x{color:red}</style>"
            "<script>alert('no')</script>"
            "<body><h1>Export flow revamp</h1><p>Users need CSV export.</p></body></html>"
        ),
        insights=[
            {"title": "Exports are the #1 ask", "body": "14 tickets mention CSV export."}
        ],
    )
    db.table("evidences").insert(
        {
            "brief_id": brief["id"],
            "insight_index": 0,
            "title": "Export demand evidence",
            "payload_md": "<html><style>b{}</style><p>Zendesk: 14 tickets.</p></html>",
            "status": "ready",
            "variant": "v3",
        }
    ).execute()
    db.table("prd_tickets").insert(
        {
            "company_id": t.company_id,
            "prd_id": 301,
            "status": "ready",
            "stories": [
                {
                    "id": "s1",
                    "title": "Add CSV export button",
                    "body": "Button on the reports page",
                    "acceptance_criteria": ["downloads csv", "respects filters"],
                }
            ],
        }
    ).execute()

    block = build_prd_context(t.company_id, 301)

    assert "CURRENT PRD CONTEXT" in block
    assert "Export flow revamp" in block
    assert "Users need CSV export." in block
    # v3 HTML noise stripped, content kept.
    assert "alert('no')" not in block
    assert ".x{color:red}" not in block
    # Insight, evidence, tickets sections.
    assert "Exports are the #1 ask" in block
    assert "14 tickets mention CSV export." in block
    assert "Zendesk: 14 tickets." in block
    assert "Add CSV export button" in block
    assert "acceptance criteria: 2" in block


def test_foreign_company_gets_empty_block(tenant_client, isolated_settings):
    """Defense-in-depth: the builder re-gates ownership, so a foreign tenant id
    never yields another company's PRD text."""
    tenant_client.make(slug="acme")
    db = isolated_settings["supabase"]
    _seed_chain(db, slug="acme", prd_id=302)
    assert build_prd_context(uuid.uuid4().hex, 302) == ""


def test_missing_prd_or_tenant_is_empty_not_raising(tenant_client):
    t = tenant_client.make(slug="acme")
    assert build_prd_context(t.company_id, 999_999) == ""
    assert build_prd_context(None, 1) == ""
    assert build_prd_context(t.company_id, None) == ""


def test_missing_artifacts_keep_prd_section(tenant_client, isolated_settings):
    """A PRD with no evidence/tickets/prototype and a sentinel insight_index
    (backlog/ideation PRDs) still grounds on the PRD body alone."""
    t = tenant_client.make(slug="acme")
    db = isolated_settings["supabase"]
    _seed_chain(db, slug="acme", prd_id=303, insight_index=9_999)

    block = build_prd_context(t.company_id, 303)
    assert "CURRENT PRD CONTEXT" in block
    assert "# The PRD body" in block
    assert "Source insight" not in block
    assert "Evidence" not in block
    assert "Tickets" not in block
    assert "Prototype" not in block
