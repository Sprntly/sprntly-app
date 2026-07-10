"""Tests for /internal/mcp-tokens/resolve + /internal/mcp/* (the mcp/
service's machine-to-machine API).

Covers: every route 401s without a valid X-Internal-Key, resolve turns a
raw token into {company_id, user_id, role}, and each data route is scoped
to the company_id it's given — a cross-tenant lookup (right ticket_key,
wrong company_id) returns nothing rather than another tenant's data.
"""
from __future__ import annotations

import uuid

import pytest

import app.auth  # noqa: F401 — ensure app.config/app.auth in sys.modules

from fastapi.testclient import TestClient

from app.db.mcp_tokens import create_mcp_token

_INTERNAL_KEY = "test-internal-key"

# The internal-MCP prototype route (`GET /internal/mcp/prd/{id}/prototype`) reads
# a `prototypes` table. It is intentionally NOT in the shared base schema — the
# ~40 Design Agent tests each create their own richer copy on the singleton fake
# DB and a base-schema copy collides with them (issue #697). So we create the
# trimmed variant this route needs locally, on the per-test reset DB.
_PROTOTYPES_DDL = """
CREATE TABLE prototypes (
    id                 INTEGER PRIMARY KEY AUTOINCREMENT,
    prd_id             INTEGER NOT NULL,
    workspace_id       TEXT NOT NULL,
    status             TEXT NOT NULL DEFAULT 'generating',
    is_complete        INTEGER NOT NULL DEFAULT 0,
    target_platform    TEXT NOT NULL DEFAULT 'both',
    preview_image_url  TEXT,
    share_mode         TEXT NOT NULL DEFAULT 'private',
    share_token        TEXT,
    error              TEXT,
    created_at         TEXT NOT NULL DEFAULT (datetime('now')),
    completed_at       TEXT
);
"""


@pytest.fixture(autouse=True)
def _prototypes_table(isolated_settings):
    """Create the route's `prototypes` table on the freshly-reset fake DB."""
    from tests import _fake_supabase

    _fake_supabase.get_fake_db().executescript(_PROTOTYPES_DDL)


def _seed_company_and_member(client, *, company_id: str, slug: str, user_id: str) -> None:
    client.table("companies").insert(
        {"id": company_id, "slug": slug, "display_name": slug.title()}
    ).execute()
    client.table("company_members").insert(
        {"id": uuid.uuid4().hex, "company_id": company_id, "user_id": user_id, "role": "owner"}
    ).execute()


def _client(isolated_settings, monkeypatch) -> TestClient:
    import app.config as config_mod
    import app.main as main_mod

    monkeypatch.setattr(config_mod.settings, "internal_api_key", _INTERNAL_KEY, raising=False)
    return TestClient(main_mod.app)


def _headers() -> dict[str, str]:
    return {"X-Internal-Key": _INTERNAL_KEY}


def test_routes_401_without_internal_key(isolated_settings, monkeypatch):
    client = _client(isolated_settings, monkeypatch)
    assert client.post("/internal/mcp-tokens/resolve", json={"token": "x"}).status_code == 401
    assert client.get("/internal/mcp/datasets", params={"company_id": "x"}).status_code == 401
    assert client.get("/internal/mcp/brief/current", params={"company_id": "x"}).status_code == 401
    assert client.get("/internal/mcp/backlog", params={"company_id": "x"}).status_code == 401
    assert client.get("/internal/mcp/prd/latest", params={"company_id": "x"}).status_code == 401
    assert client.get("/internal/mcp/prd/1/prototype", params={"company_id": "x"}).status_code == 401
    assert client.get("/internal/mcp/prd/1/evidence", params={"company_id": "x"}).status_code == 401
    assert (
        client.get("/internal/mcp/tickets/ABC-1/data", params={"company_id": "x"}).status_code
        == 401
    )


def test_resolve_returns_company_context_for_valid_token(isolated_settings, monkeypatch):
    client = _client(isolated_settings, monkeypatch)
    cid, uid = uuid.uuid4().hex, "user-1"
    _seed_company_and_member(isolated_settings["supabase"], company_id=cid, slug="acme", user_id=uid)
    created = create_mcp_token(company_id=cid, user_id=uid, name="t", token_role="developer")

    r = client.post(
        "/internal/mcp-tokens/resolve", json={"token": created["token"]}, headers=_headers()
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["company_id"] == cid
    assert body["user_id"] == uid
    assert body["role"] == "owner"
    # The minted token_role rides along so mcp/ can gate tools per token.
    assert body["token_role"] == "developer"


def test_resolve_401s_on_unknown_token(isolated_settings, monkeypatch):
    client = _client(isolated_settings, monkeypatch)
    r = client.post(
        "/internal/mcp-tokens/resolve", json={"token": "sprn_mcp_bogus"}, headers=_headers()
    )
    assert r.status_code == 401


def test_datasets_scoped_to_company(isolated_settings, monkeypatch):
    client = _client(isolated_settings, monkeypatch)
    db = isolated_settings["supabase"]
    cid_a = uuid.uuid4().hex
    cid_b = uuid.uuid4().hex
    _seed_company_and_member(db, company_id=cid_a, slug="acme", user_id="u-a")
    _seed_company_and_member(db, company_id=cid_b, slug="globex", user_id="u-b")
    db.table("datasets").insert({"slug": "acme", "display_name": "Acme"}).execute()
    db.table("datasets").insert({"slug": "globex", "display_name": "Globex"}).execute()

    r = client.get("/internal/mcp/datasets", params={"company_id": cid_a}, headers=_headers())
    assert r.status_code == 200, r.text
    slugs = [d["slug"] for d in r.json()["datasets"]]
    assert slugs == ["acme"]


def test_backlog_empty_when_no_brief(isolated_settings, monkeypatch):
    client = _client(isolated_settings, monkeypatch)
    cid = uuid.uuid4().hex
    _seed_company_and_member(isolated_settings["supabase"], company_id=cid, slug="acme", user_id="u-a")

    r = client.get("/internal/mcp/backlog", params={"company_id": cid}, headers=_headers())
    assert r.status_code == 200, r.text
    assert r.json() == {"items": [], "count": 0}


def test_prd_latest_404s_when_none(isolated_settings, monkeypatch):
    client = _client(isolated_settings, monkeypatch)
    cid = uuid.uuid4().hex
    _seed_company_and_member(isolated_settings["supabase"], company_id=cid, slug="acme", user_id="u-a")

    r = client.get("/internal/mcp/prd/latest", params={"company_id": cid}, headers=_headers())
    assert r.status_code == 404


def test_ticket_data_is_company_scoped(isolated_settings, monkeypatch):
    """Same ticket_key, two companies: each sees only its own override —
    no RLS safety net on the service-role client, so this is the explicit
    test that the company_id filter is actually applied."""
    client = _client(isolated_settings, monkeypatch)
    db = isolated_settings["supabase"]
    cid_a = uuid.uuid4().hex
    cid_b = uuid.uuid4().hex
    _seed_company_and_member(db, company_id=cid_a, slug="acme", user_id="u-a")
    _seed_company_and_member(db, company_id=cid_b, slug="globex", user_id="u-b")
    db.table("ticket_edits").insert(
        {"company_id": cid_a, "ticket_key": "ABC-1", "description": "Company A's ticket"}
    ).execute()

    r_a = client.get(
        "/internal/mcp/tickets/ABC-1/data", params={"company_id": cid_a}, headers=_headers()
    )
    assert r_a.status_code == 200, r_a.text
    assert r_a.json()["description"] == "Company A's ticket"

    # Company B has no trace of ABC-1 (no story, edit, comment, or attachment)
    # → 404, never a leak of company A's data.
    r_b = client.get(
        "/internal/mcp/tickets/ABC-1/data", params={"company_id": cid_b}, headers=_headers()
    )
    assert r_b.status_code == 404


def test_get_ticket_merges_base_story_content(isolated_settings, monkeypatch):
    """An UNEDITED ticket still returns its generated content (title, body →
    description, acceptance criteria, scope) from prd_tickets.stories — this is
    what a developer implements against."""
    client = _client(isolated_settings, monkeypatch)
    db = isolated_settings["supabase"]
    cid = uuid.uuid4().hex
    _seed_company_and_member(db, company_id=cid, slug="acme", user_id="u-a")
    _seed_prd_tickets(db, company_id=cid, prd_id=7, stories=[
        {
            "id": "story-1", "title": "Add SSO", "ticket_type": "feature",
            "body": "Implement SSO login", "acceptance_criteria": ["works", "tested"],
            "scope": ["login page"], "what": "SSO", "why_now": "enterprise deal",
        },
    ])

    r = client.get("/internal/mcp/tickets/story-1/data", params={"company_id": cid}, headers=_headers())
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["title"] == "Add SSO"
    assert body["description"] == "Implement SSO login"   # from story.body
    assert body["acceptance_criteria"] == ["works", "tested"]
    assert body["scope"] == ["login page"]
    assert body["what"] == "SSO"
    assert body["prd_id"] == 7


def test_get_ticket_edit_overrides_base_story(isolated_settings, monkeypatch):
    """An edit wins over the generated base content for the same field."""
    client = _client(isolated_settings, monkeypatch)
    db = isolated_settings["supabase"]
    cid = uuid.uuid4().hex
    _seed_company_and_member(db, company_id=cid, slug="acme", user_id="u-a")
    _seed_prd_tickets(db, company_id=cid, prd_id=8, stories=[
        {"id": "story-2", "title": "Base title", "body": "base desc", "ticket_type": "bug"},
    ])
    db.table("ticket_edits").insert(
        {"company_id": cid, "ticket_key": "story-2", "title": "Edited title", "status": "In progress"}
    ).execute()

    body = client.get("/internal/mcp/tickets/story-2/data", params={"company_id": cid}, headers=_headers()).json()
    assert body["title"] == "Edited title"      # edit wins
    assert body["description"] == "base desc"    # untouched → base story
    assert body["status"] == "In progress"       # status only comes from edits


def test_get_ticket_web_format_key_reads_web_rows(isolated_settings, monkeypatch):
    """The web-format key ("prd-{prd_id}-{story_id}") resolves the base story
    via the embedded story id AND reads the edit/comment rows the web app
    stored under the full composed key — the two surfaces share one silo."""
    client = _client(isolated_settings, monkeypatch)
    db = isolated_settings["supabase"]
    cid = uuid.uuid4().hex
    _seed_company_and_member(db, company_id=cid, slug="acme", user_id="u-a")
    _seed_prd_tickets(db, company_id=cid, prd_id=42, stories=[
        {"id": "a1b2c3d4e5f6", "title": "Base title", "body": "base desc"},
    ])
    # Rows as the WEB APP writes them: keyed by the composed key.
    db.table("ticket_edits").insert(
        {"company_id": cid, "ticket_key": "prd-42-a1b2c3d4e5f6", "status": "In review"}
    ).execute()
    db.table("ticket_comments").insert(
        {"company_id": cid, "ticket_key": "prd-42-a1b2c3d4e5f6", "author": "Ada", "body": "web comment"}
    ).execute()

    r = client.get(
        "/internal/mcp/tickets/prd-42-a1b2c3d4e5f6/data",
        params={"company_id": cid},
        headers=_headers(),
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["id"] == "prd-42-a1b2c3d4e5f6"
    assert body["prd_id"] == 42
    assert body["title"] == "Base title"          # base story located via embedded id
    assert body["status"] == "In review"           # web-written edit visible
    assert [c_["body"] for c_ in body["comments"]] == ["web comment"]


def test_get_ticket_slug_fallback_key_for_idless_story(isolated_settings, monkeypatch):
    """Legacy stories generated before ids existed: the web key embeds a title
    slug ("prd-{prd_id}-{slug}") — the route resolves the base story by
    re-deriving that slug."""
    client = _client(isolated_settings, monkeypatch)
    db = isolated_settings["supabase"]
    cid = uuid.uuid4().hex
    _seed_company_and_member(db, company_id=cid, slug="acme", user_id="u-a")
    _seed_prd_tickets(db, company_id=cid, prd_id=9, stories=[
        {"title": "Fix Login Flow!", "body": "legacy story"},  # no id
    ])

    r = client.get(
        "/internal/mcp/tickets/prd-9-fix-login-flow/data",
        params={"company_id": cid},
        headers=_headers(),
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["prd_id"] == 9
    assert body["title"] == "Fix Login Flow!"
    assert body["description"] == "legacy story"


def test_get_ticket_bare_legacy_key_still_resolves_story(isolated_settings, monkeypatch):
    """Keys written by older MCP clients (the bare story id) keep resolving the
    base story — no migration, graceful coexistence."""
    client = _client(isolated_settings, monkeypatch)
    db = isolated_settings["supabase"]
    cid = uuid.uuid4().hex
    _seed_company_and_member(db, company_id=cid, slug="acme", user_id="u-a")
    _seed_prd_tickets(db, company_id=cid, prd_id=3, stories=[
        {"id": "deadbeef0001", "title": "Old style", "body": "still readable"},
    ])

    r = client.get(
        "/internal/mcp/tickets/deadbeef0001/data",
        params={"company_id": cid},
        headers=_headers(),
    )
    assert r.status_code == 200, r.text
    assert r.json()["title"] == "Old style"
    assert r.json()["prd_id"] == 3


def test_get_ticket_404_when_no_trace(isolated_settings, monkeypatch):
    client = _client(isolated_settings, monkeypatch)
    cid = uuid.uuid4().hex
    _seed_company_and_member(isolated_settings["supabase"], company_id=cid, slug="acme", user_id="u-a")
    r = client.get("/internal/mcp/tickets/ghost/data", params={"company_id": cid}, headers=_headers())
    assert r.status_code == 404
    # Web-format key with no matching story/rows → same 404.
    r2 = client.get("/internal/mcp/tickets/prd-1-ghost/data", params={"company_id": cid}, headers=_headers())
    assert r2.status_code == 404


# ── ticket list + writes (developer read/edit surface) ──


def _seed_prd_tickets(db, *, company_id: str, prd_id: int, stories: list[dict]) -> None:
    db.table("prd_tickets").insert(
        {"company_id": company_id, "prd_id": prd_id, "content_hash": "h", "stories": stories}
    ).execute()


def test_list_tickets_flattens_stories_company_scoped(isolated_settings, monkeypatch):
    """list_tickets flattens every PRD's `stories` array into one list, and
    only returns the calling company's tickets."""
    client = _client(isolated_settings, monkeypatch)
    db = isolated_settings["supabase"]
    cid_a = uuid.uuid4().hex
    cid_b = uuid.uuid4().hex
    _seed_company_and_member(db, company_id=cid_a, slug="acme", user_id="u-a")
    _seed_company_and_member(db, company_id=cid_b, slug="globex", user_id="u-b")
    _seed_prd_tickets(db, company_id=cid_a, prd_id=1, stories=[
        {"id": "tkt-1", "title": "First", "ticket_type": "feature"},
        {"id": "tkt-2", "title": "Second", "ticket_type": "bug"},
    ])
    _seed_prd_tickets(db, company_id=cid_a, prd_id=2, stories=[
        {"id": "tkt-3", "title": "Third", "ticket_type": "chore"},
    ])
    _seed_prd_tickets(db, company_id=cid_b, prd_id=3, stories=[
        {"id": "other", "title": "Not yours", "ticket_type": "feature"},
    ])

    r = client.get("/internal/mcp/tickets", params={"company_id": cid_a}, headers=_headers())
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["count"] == 3
    # ids are the WEB-FORMAT keys ("prd-{prd_id}-{story_id}") — the same key
    # the web app composes, so edits/comments land in shared rows.
    ids = sorted(t["id"] for t in body["tickets"])
    assert ids == ["prd-1-tkt-1", "prd-1-tkt-2", "prd-2-tkt-3"]
    assert not any("other" in i for i in ids)
    # prd_id carried through for each flattened ticket.
    assert {t["prd_id"] for t in body["tickets"]} == {1, 2}


def test_list_tickets_empty_when_none(isolated_settings, monkeypatch):
    client = _client(isolated_settings, monkeypatch)
    cid = uuid.uuid4().hex
    _seed_company_and_member(isolated_settings["supabase"], company_id=cid, slug="acme", user_id="u-a")
    r = client.get("/internal/mcp/tickets", params={"company_id": cid}, headers=_headers())
    assert r.status_code == 200, r.text
    assert r.json() == {"tickets": [], "count": 0}


def test_list_tickets_includes_status_and_filters(isolated_settings, monkeypatch):
    """The list merges each ticket's current (edited) status, and the status /
    ticket_type filters narrow it."""
    client = _client(isolated_settings, monkeypatch)
    db = isolated_settings["supabase"]
    cid = uuid.uuid4().hex
    _seed_company_and_member(db, company_id=cid, slug="acme", user_id="u-a")
    _seed_prd_tickets(db, company_id=cid, prd_id=1, stories=[
        {"id": "a", "title": "A", "ticket_type": "feature"},
        {"id": "b", "title": "B", "ticket_type": "bug"},
    ])
    # Edits are stored under the web-format key (what the web app writes).
    db.table("ticket_edits").insert(
        {"company_id": cid, "ticket_key": "prd-1-a", "status": "In progress"}
    ).execute()

    all_ = client.get("/internal/mcp/tickets", params={"company_id": cid}, headers=_headers()).json()
    by_id = {t["id"]: t for t in all_["tickets"]}
    assert by_id["prd-1-a"]["status"] == "In progress"
    # Unedited ticket defaults to the canonical "Backlog", not null.
    assert by_id["prd-1-b"]["status"] == "Backlog"

    # Filter by status (case-insensitive).
    only_ip = client.get(
        "/internal/mcp/tickets", params={"company_id": cid, "status": "in progress"}, headers=_headers()
    ).json()
    assert [t["id"] for t in only_ip["tickets"]] == ["prd-1-a"]

    # The recommended `status=Backlog` filter finds the unedited backlog ticket.
    only_backlog = client.get(
        "/internal/mcp/tickets", params={"company_id": cid, "status": "Backlog"}, headers=_headers()
    ).json()
    assert [t["id"] for t in only_backlog["tickets"]] == ["prd-1-b"]

    # Filter by ticket_type.
    only_bug = client.get(
        "/internal/mcp/tickets", params={"company_id": cid, "ticket_type": "bug"}, headers=_headers()
    ).json()
    assert [t["id"] for t in only_bug["tickets"]] == ["prd-1-b"]


def test_list_tickets_prd_id_narrows_to_one_prd(isolated_settings, monkeypatch):
    """`prd_id` returns only that PRD's tickets (the MCP list_prd_tickets
    tool); a foreign company's prd_id matches nothing because the query is
    company-scoped before the filter."""
    client = _client(isolated_settings, monkeypatch)
    db = isolated_settings["supabase"]
    cid_a, cid_b = uuid.uuid4().hex, uuid.uuid4().hex
    _seed_company_and_member(db, company_id=cid_a, slug="acme", user_id="u-a")
    _seed_company_and_member(db, company_id=cid_b, slug="globex", user_id="u-b")
    _seed_prd_tickets(db, company_id=cid_a, prd_id=1, stories=[
        {"id": "a1", "title": "A1", "ticket_type": "feature"},
        {"id": "a2", "title": "A2", "ticket_type": "bug"},
    ])
    _seed_prd_tickets(db, company_id=cid_a, prd_id=2, stories=[
        {"id": "b1", "title": "B1", "ticket_type": "feature"},
    ])
    _seed_prd_tickets(db, company_id=cid_b, prd_id=9, stories=[
        {"id": "x1", "title": "Not yours", "ticket_type": "feature"},
    ])

    r = client.get(
        "/internal/mcp/tickets",
        params={"company_id": cid_a, "prd_id": 1},
        headers=_headers(),
    )
    assert r.status_code == 200, r.text
    assert sorted(t["id"] for t in r.json()["tickets"]) == ["prd-1-a1", "prd-1-a2"]

    # prd_id composes with the other filters.
    only_bug = client.get(
        "/internal/mcp/tickets",
        params={"company_id": cid_a, "prd_id": 1, "ticket_type": "bug"},
        headers=_headers(),
    ).json()
    assert [t["id"] for t in only_bug["tickets"]] == ["prd-1-a2"]

    # Another tenant's PRD id yields an empty list, never their tickets.
    foreign = client.get(
        "/internal/mcp/tickets",
        params={"company_id": cid_a, "prd_id": 9},
        headers=_headers(),
    ).json()
    assert foreign == {"tickets": [], "count": 0}


def test_list_tickets_assignee_user_id_scopes_to_owner(isolated_settings, monkeypatch):
    """`assignee_user_id` returns ONLY tickets whose edit assigns that user —
    a teammate's tickets and unassigned tickets are both excluded. This is the
    filter the MCP server drives with the token owner's user_id, so an AI
    client sees just the caller's own work queue."""
    client = _client(isolated_settings, monkeypatch)
    db = isolated_settings["supabase"]
    cid = uuid.uuid4().hex
    _seed_company_and_member(db, company_id=cid, slug="acme", user_id="u-a")
    _seed_prd_tickets(db, company_id=cid, prd_id=1, stories=[
        {"id": "mine", "title": "Mine", "ticket_type": "feature"},
        {"id": "theirs", "title": "Theirs", "ticket_type": "feature"},
        {"id": "nobody", "title": "Unassigned", "ticket_type": "feature"},
    ])
    db.table("ticket_edits").insert(
        {"company_id": cid, "ticket_key": "prd-1-mine",
         "assignee": {"user_id": "u-a", "display_name": "Ada"}}
    ).execute()
    db.table("ticket_edits").insert(
        {"company_id": cid, "ticket_key": "prd-1-theirs",
         "assignee": {"user_id": "u-b", "display_name": "Bob"}}
    ).execute()

    r = client.get(
        "/internal/mcp/tickets",
        params={"company_id": cid, "assignee_user_id": "u-a"},
        headers=_headers(),
    )
    assert r.status_code == 200, r.text
    assert [t["id"] for t in r.json()["tickets"]] == ["prd-1-mine"]

    # Without the filter, all three tickets are still there (unchanged shape).
    all_ = client.get(
        "/internal/mcp/tickets", params={"company_id": cid}, headers=_headers()
    ).json()
    assert all_["count"] == 3


def _seed_prd_chain(db, *, company_id: str, slug: str, prd_id: int) -> None:
    """Seed the briefs→prds chain so require_owned_prd resolves prd→company."""
    brief = db.table("briefs").insert(
        {"dataset": slug, "week_label": "W", "payload": {"insights": []}, "is_current": True}
    ).execute().data[0]
    db.table("prds").insert(
        {"id": prd_id, "brief_id": brief["id"], "insight_index": 0, "title": "P", "payload_md": "# body"}
    ).execute()


def test_get_prd_owned_and_foreign(isolated_settings, monkeypatch):
    client = _client(isolated_settings, monkeypatch)
    db = isolated_settings["supabase"]
    cid_a = uuid.uuid4().hex
    cid_b = uuid.uuid4().hex
    _seed_company_and_member(db, company_id=cid_a, slug="acme", user_id="u-a")
    _seed_company_and_member(db, company_id=cid_b, slug="globex", user_id="u-b")
    _seed_prd_chain(db, company_id=cid_a, slug="acme", prd_id=101)

    ok = client.get("/internal/mcp/prd/101", params={"company_id": cid_a}, headers=_headers())
    assert ok.status_code == 200, ok.text
    assert ok.json()["title"] == "P"

    # Foreign company → 404 (no existence disclosure), and missing id → 404.
    assert client.get("/internal/mcp/prd/101", params={"company_id": cid_b}, headers=_headers()).status_code == 404
    assert client.get("/internal/mcp/prd/9999", params={"company_id": cid_a}, headers=_headers()).status_code == 404


def test_get_ticket_subtasks_override_wins(isolated_settings, monkeypatch):
    """Edited child issues (ticket_edits.subtasks) replace the generated ones
    in the MCP ticket read — same override-wins merge as title/priority."""
    client = _client(isolated_settings, monkeypatch)
    db = isolated_settings["supabase"]
    cid = uuid.uuid4().hex
    _seed_company_and_member(db, company_id=cid, slug="acme", user_id="u-a")
    _seed_prd_tickets(db, company_id=cid, prd_id=1, stories=[
        {"id": "t1", "title": "T1", "subtasks": ["generated step"]},
    ])

    before = client.get(
        "/internal/mcp/tickets/prd-1-t1/data", params={"company_id": cid}, headers=_headers()
    ).json()
    assert before["subtasks"] == ["generated step"]

    db.table("ticket_edits").insert(
        {"company_id": cid, "ticket_key": "prd-1-t1", "subtasks": ["edited step"]}
    ).execute()
    after = client.get(
        "/internal/mcp/tickets/prd-1-t1/data", params={"company_id": cid}, headers=_headers()
    ).json()
    assert after["subtasks"] == ["edited step"]


def test_get_prd_prototype_shared_and_private(isolated_settings, monkeypatch):
    """Ready+shared prototype → both links; private → app_url only. Never the
    bundle_url or passcode hash."""
    client = _client(isolated_settings, monkeypatch)
    db = isolated_settings["supabase"]
    cid = uuid.uuid4().hex
    _seed_company_and_member(db, company_id=cid, slug="acme", user_id="u-a")
    _seed_prd_chain(db, company_id=cid, slug="acme", prd_id=101)
    db.table("prototypes").insert(
        {
            "prd_id": 101,
            "workspace_id": cid,
            "status": "ready",
            "is_complete": True,
            "share_mode": "public",
            "share_token": "tok-abc123",
            "preview_image_url": "https://cdn/x.png",
        }
    ).execute()

    r = client.get(
        "/internal/mcp/prd/101/prototype", params={"company_id": cid}, headers=_headers()
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["status"] == "ready"
    assert body["is_complete"] is True
    assert body["preview_image_url"] == "https://cdn/x.png"
    assert body["app_url"].endswith("/prototype?prd=101")
    assert body["public_url"].endswith("/p/acme/tok-abc123")
    assert "bundle_url" not in body
    assert "share_passcode_hash" not in body

    # Private prototype (newest row wins): no public link, app link intact.
    db.table("prototypes").insert(
        {"prd_id": 101, "workspace_id": cid, "status": "ready",
         "share_mode": "private", "share_token": "tok-hidden"}
    ).execute()
    private = client.get(
        "/internal/mcp/prd/101/prototype", params={"company_id": cid}, headers=_headers()
    ).json()
    assert private["public_url"] is None
    assert "tok-hidden" not in str(private)
    assert private["app_url"].endswith("/prototype?prd=101")


def test_get_prd_prototype_404s(isolated_settings, monkeypatch):
    """No prototype yet → 404; a foreign company's prd_id → 404 before the
    prototype lookup (require_owned_prd, no existence disclosure)."""
    client = _client(isolated_settings, monkeypatch)
    db = isolated_settings["supabase"]
    cid_a, cid_b = uuid.uuid4().hex, uuid.uuid4().hex
    _seed_company_and_member(db, company_id=cid_a, slug="acme", user_id="u-a")
    _seed_company_and_member(db, company_id=cid_b, slug="globex", user_id="u-b")
    _seed_prd_chain(db, company_id=cid_a, slug="acme", prd_id=101)
    db.table("prototypes").insert(
        {"prd_id": 101, "workspace_id": cid_a, "status": "ready",
         "share_mode": "public", "share_token": "tok-a"}
    ).execute()

    assert client.get(
        "/internal/mcp/prd/101/prototype", params={"company_id": cid_b}, headers=_headers()
    ).status_code == 404

    _seed_prd_chain(db, company_id=cid_a, slug="acme", prd_id=102)
    assert client.get(
        "/internal/mcp/prd/102/prototype", params={"company_id": cid_a}, headers=_headers()
    ).status_code == 404


def test_get_prd_evidence_resolves_via_prd_insight(isolated_settings, monkeypatch):
    """Evidence is keyed by (brief_id, insight_index) — the PRD row is the
    join. Latest ready/generating row of ANY variant wins (permissive read),
    and v3 rows are flagged as html content."""
    client = _client(isolated_settings, monkeypatch)
    db = isolated_settings["supabase"]
    cid = uuid.uuid4().hex
    _seed_company_and_member(db, company_id=cid, slug="acme", user_id="u-a")
    _seed_prd_chain(db, company_id=cid, slug="acme", prd_id=101)
    prd = db.table("prds").select("*").eq("id", 101).execute().data[0]
    db.table("evidences").insert(
        {"brief_id": prd["brief_id"], "insight_index": prd["insight_index"],
         "title": "Old evidence", "payload_md": "# old", "status": "ready", "variant": "v2"}
    ).execute()
    db.table("evidences").insert(
        {"brief_id": prd["brief_id"], "insight_index": prd["insight_index"],
         "title": "Why this matters", "payload_md": "<html>brief</html>",
         "status": "ready", "variant": "v3"}
    ).execute()

    r = client.get(
        "/internal/mcp/prd/101/evidence", params={"company_id": cid}, headers=_headers()
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["title"] == "Why this matters"  # newest row wins
    assert body["content"] == "<html>brief</html>"
    assert body["content_format"] == "html"
    assert body["content_truncated"] is False

    # Markdown-era rows are flagged as markdown.
    db.table("evidences").insert(
        {"brief_id": prd["brief_id"], "insight_index": prd["insight_index"],
         "title": "Newer md", "payload_md": "# md", "status": "ready", "variant": "v2"}
    ).execute()
    again = client.get(
        "/internal/mcp/prd/101/evidence", params={"company_id": cid}, headers=_headers()
    ).json()
    assert again["content_format"] == "markdown"


def test_get_prd_evidence_404s(isolated_settings, monkeypatch):
    """No evidence yet → 404; foreign company's prd_id → 404 via
    require_owned_prd (no existence disclosure)."""
    client = _client(isolated_settings, monkeypatch)
    db = isolated_settings["supabase"]
    cid_a, cid_b = uuid.uuid4().hex, uuid.uuid4().hex
    _seed_company_and_member(db, company_id=cid_a, slug="acme", user_id="u-a")
    _seed_company_and_member(db, company_id=cid_b, slug="globex", user_id="u-b")
    _seed_prd_chain(db, company_id=cid_a, slug="acme", prd_id=101)

    assert client.get(
        "/internal/mcp/prd/101/evidence", params={"company_id": cid_a}, headers=_headers()
    ).status_code == 404
    assert client.get(
        "/internal/mcp/prd/101/evidence", params={"company_id": cid_b}, headers=_headers()
    ).status_code == 404


def test_add_attachment_then_read_back(isolated_settings, monkeypatch):
    client = _client(isolated_settings, monkeypatch)
    db = isolated_settings["supabase"]
    cid = uuid.uuid4().hex
    _seed_company_and_member(db, company_id=cid, slug="acme", user_id="u-a")
    _seed_prd_tickets(db, company_id=cid, prd_id=1, stories=[{"id": "t", "title": "T"}])

    w = client.post(
        "/internal/mcp/tickets/t/attachments",
        params={"company_id": cid},
        json={"label": "PR #42", "sub": "https://github.com/x/y/pull/42"},
        headers=_headers(),
    )
    assert w.status_code == 200, w.text
    assert w.json()["label"] == "PR #42"

    data = client.get("/internal/mcp/tickets/t/data", params={"company_id": cid}, headers=_headers()).json()
    assert len(data["attachments"]) == 1
    assert data["attachments"][0]["sub"] == "https://github.com/x/y/pull/42"


def test_attachment_rejects_unsafe_url_scheme(isolated_settings, monkeypatch):
    """`sub` is rendered as an href in the app, so script-y schemes are rejected
    at the (AI-writable) write boundary."""
    client = _client(isolated_settings, monkeypatch)
    cid = uuid.uuid4().hex
    _seed_company_and_member(isolated_settings["supabase"], company_id=cid, slug="acme", user_id="u-a")

    bad = client.post(
        "/internal/mcp/tickets/t/attachments",
        params={"company_id": cid},
        json={"label": "click me", "sub": "javascript:alert(document.cookie)"},
        headers=_headers(),
    )
    assert bad.status_code == 400

    ok = client.post(
        "/internal/mcp/tickets/t/attachments",
        params={"company_id": cid},
        json={"label": "PR", "sub": "https://github.com/x/y/pull/1"},
        headers=_headers(),
    )
    assert ok.status_code == 200


def test_update_description_only_preserves_generated_criteria(isolated_settings, monkeypatch):
    """A description-only edit leaves the generated acceptance criteria intact;
    passing a list explicitly replaces them."""
    client = _client(isolated_settings, monkeypatch)
    db = isolated_settings["supabase"]
    cid = uuid.uuid4().hex
    _seed_company_and_member(db, company_id=cid, slug="acme", user_id="u-a")
    _seed_prd_tickets(db, company_id=cid, prd_id=1, stories=[
        {"id": "t", "title": "T", "body": "base", "acceptance_criteria": ["A", "B"]},
    ])

    client.put(
        "/internal/mcp/tickets/t/description",
        params={"company_id": cid},
        json={"description": "Refined"},  # no acceptance_criteria
        headers=_headers(),
    )
    data = client.get("/internal/mcp/tickets/t/data", params={"company_id": cid}, headers=_headers()).json()
    assert data["description"] == "Refined"
    assert data["acceptance_criteria"] == ["A", "B"]  # generated criteria preserved

    client.put(
        "/internal/mcp/tickets/t/description",
        params={"company_id": cid},
        json={"description": "Refined", "acceptance_criteria": ["C"]},
        headers=_headers(),
    )
    data2 = client.get("/internal/mcp/tickets/t/data", params={"company_id": cid}, headers=_headers()).json()
    assert data2["acceptance_criteria"] == ["C"]


def test_write_description_then_read_back(isolated_settings, monkeypatch):
    client = _client(isolated_settings, monkeypatch)
    cid = uuid.uuid4().hex
    _seed_company_and_member(isolated_settings["supabase"], company_id=cid, slug="acme", user_id="u-a")

    w = client.put(
        "/internal/mcp/tickets/TKT-1/description",
        params={"company_id": cid},
        json={"description": "Do the thing", "acceptance_criteria": ["a", "b"]},
        headers=_headers(),
    )
    assert w.status_code == 200, w.text

    r = client.get("/internal/mcp/tickets/TKT-1/data", params={"company_id": cid}, headers=_headers())
    assert r.json()["description"] == "Do the thing"
    assert r.json()["acceptance_criteria"] == ["a", "b"]


def test_write_fields_partial_preserves_description(isolated_settings, monkeypatch):
    """A fields update writes only the sent fields — the previously-saved
    description survives (exclude_unset semantics)."""
    client = _client(isolated_settings, monkeypatch)
    cid = uuid.uuid4().hex
    _seed_company_and_member(isolated_settings["supabase"], company_id=cid, slug="acme", user_id="u-a")

    client.put(
        "/internal/mcp/tickets/TKT-1/description",
        params={"company_id": cid},
        json={"description": "keep me", "acceptance_criteria": []},
        headers=_headers(),
    )
    w = client.put(
        "/internal/mcp/tickets/TKT-1/fields",
        params={"company_id": cid},
        json={"status": "in_progress"},
        headers=_headers(),
    )
    assert w.status_code == 200, w.text

    data = client.get("/internal/mcp/tickets/TKT-1/data", params={"company_id": cid}, headers=_headers()).json()
    assert data["status"] == "in_progress"
    assert data["description"] == "keep me"  # untouched


def test_add_comment_attributes_to_token_owner_name(isolated_settings, monkeypatch):
    """The comment author is resolved server-side from the token owner's
    profile name — not accepted from the caller, and not the generic 'mcp'."""
    client = _client(isolated_settings, monkeypatch)
    db = isolated_settings["supabase"]
    cid = uuid.uuid4().hex
    _seed_company_and_member(db, company_id=cid, slug="acme", user_id="u-a")
    db.table("profiles").insert({"id": "u-a", "full_name": "Ada Lovelace"}).execute()

    w = client.post(
        "/internal/mcp/tickets/TKT-1/comments",
        params={"company_id": cid, "user_id": "u-a"},
        json={"body": "looks good"},
        headers=_headers(),
    )
    assert w.status_code == 200, w.text
    assert w.json()["author"] == "Ada Lovelace"
    assert w.json()["body"] == "looks good"

    comments = client.get("/internal/mcp/tickets/TKT-1/data", params={"company_id": cid}, headers=_headers()).json()["comments"]
    assert len(comments) == 1
    assert comments[0]["author"] == "Ada Lovelace"
    assert comments[0]["body"] == "looks good"


def test_comment_author_falls_back_to_email_then_mcp(isolated_settings, monkeypatch):
    """Fallback chain when no name is on file: email, then 'mcp'."""
    client = _client(isolated_settings, monkeypatch)
    db = isolated_settings["supabase"]
    cid = uuid.uuid4().hex
    _seed_company_and_member(db, company_id=cid, slug="acme", user_id="u-e")
    db.table("profiles").insert({"id": "u-e", "email": "dev@acme.com"}).execute()  # no name

    w = client.post(
        "/internal/mcp/tickets/T/comments",
        params={"company_id": cid, "user_id": "u-e"},
        json={"body": "hi"},
        headers=_headers(),
    )
    assert w.json()["author"] == "dev@acme.com"

    # No profile row at all → last-resort "mcp".
    w2 = client.post(
        "/internal/mcp/tickets/T/comments",
        params={"company_id": cid, "user_id": "ghost"},
        json={"body": "hi"},
        headers=_headers(),
    )
    assert w2.json()["author"] == "mcp"


def test_ticket_writes_are_company_scoped(isolated_settings, monkeypatch):
    """A write under company A never surfaces for company B on the same
    ticket_key — the company_id filter is load-bearing (no RLS net)."""
    client = _client(isolated_settings, monkeypatch)
    db = isolated_settings["supabase"]
    cid_a = uuid.uuid4().hex
    cid_b = uuid.uuid4().hex
    _seed_company_and_member(db, company_id=cid_a, slug="acme", user_id="u-a")
    _seed_company_and_member(db, company_id=cid_b, slug="globex", user_id="u-b")

    client.put(
        "/internal/mcp/tickets/SHARED/fields",
        params={"company_id": cid_a},
        json={"status": "done"},
        headers=_headers(),
    )

    # Company A's edit is visible to A...
    a = client.get("/internal/mcp/tickets/SHARED/data", params={"company_id": cid_a}, headers=_headers())
    assert a.json()["status"] == "done"
    # ...but company B has no trace of SHARED → 404, never A's data.
    b = client.get("/internal/mcp/tickets/SHARED/data", params={"company_id": cid_b}, headers=_headers())
    assert b.status_code == 404


def test_ticket_write_routes_401_without_internal_key(isolated_settings, monkeypatch):
    client = _client(isolated_settings, monkeypatch)
    assert client.get("/internal/mcp/tickets", params={"company_id": "x"}).status_code == 401
    assert client.put(
        "/internal/mcp/tickets/T/fields", params={"company_id": "x"}, json={"status": "done"}
    ).status_code == 401
    assert client.put(
        "/internal/mcp/tickets/T/description",
        params={"company_id": "x"},
        json={"description": "x", "acceptance_criteria": []},
    ).status_code == 401
    assert client.post(
        "/internal/mcp/tickets/T/comments",
        params={"company_id": "x", "user_id": "y"},
        json={"body": "x"},
    ).status_code == 401
