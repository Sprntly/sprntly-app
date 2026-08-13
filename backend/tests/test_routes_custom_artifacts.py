"""Tests for /v1/custom-artifacts — team documents of any kind ("Others").

Covers the three things this surface can get wrong in ways a user would feel:

  1. TENANCY — a document belongs to a company, and a member of another company
     must not be able to read, edit, delete or even PROVE THE EXISTENCE of it.
     Every cross-tenant case asserts 404 specifically (never 403), because a 403
     is itself a disclosure.
  2. SHARING — a member who did not create a document must be able to open and
     edit it. This is the actual product requirement ("shared within the team"),
     and it is the exact defect class #1061 fixed on the share-link path, where
     a resolver handed every non-creator a read-only view.
  3. CONCURRENCY — two people editing one document must not silently overwrite
     each other. A save carrying a stale `base_version` is refused with 409 and
     the current document, not merged and not dropped.
"""
from __future__ import annotations

import uuid

import pytest

from tests import _fake_supabase
from tests._company_helpers import (
    company_client,
    seed_company,
    supabase_bearer,
)

# SQLite translation of supabase/migrations/20260813120000_custom_artifacts.sql.
# `version` defaults to 1 and `status` to 'ready', exactly as the real DDL does —
# the route's create path relies on both defaults rather than sending them.
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
    version         INTEGER NOT NULL DEFAULT 1,
    created_by      TEXT,
    updated_by      TEXT,
    created_at      TEXT NOT NULL DEFAULT (datetime('now')),
    updated_at      TEXT NOT NULL DEFAULT (datetime('now'))
);
"""


@pytest.fixture
def docs_env(isolated_settings):
    _fake_supabase.get_fake_db().executescript(_DDL)
    yield


def _create(ctx, **body):
    return ctx.client.post("/v1/custom-artifacts", json=body)


def _seed_conversation(*, company_id: str, title: str = "chat") -> int:
    """A real conversation row. Needed because `conversation_id` is the one id
    the CLIENT supplies on this surface, so the route proves ownership of it
    before storing — see the create route's note."""
    from app.db.client import require_client

    return (
        require_client().table("conversations")
        .insert({"company_id": company_id, "title": title})
        .execute()
        .data[0]["id"]
    )


# ─── Create / read / list ────────────────────────────────────────────────────

def test_create_returns_the_row_with_id_and_version(docs_env, monkeypatch):
    ctx = company_client(monkeypatch)
    r = _create(ctx, kind="leadership update", title="Q3", body_html="<p>hi</p>")
    assert r.status_code == 200
    doc = r.json()
    assert doc["id"] >= 1
    assert doc["kind"] == "leadership update"
    assert doc["title"] == "Q3"
    assert doc["body_html"] == "<p>hi</p>"
    # A fresh document is immediately usable — nothing to wait for.
    assert doc["status"] == "ready"
    assert doc["version"] == 1


def test_create_accepts_a_completely_empty_document(docs_env, monkeypatch):
    """"New document" from the library names itself by being typed in, the way
    a new Google Doc does. An API that required a title would break that."""
    ctx = company_client(monkeypatch)
    r = _create(ctx)
    assert r.status_code == 200
    assert r.json()["title"] == "" and r.json()["body_html"] == ""


def test_create_sanitizes_the_body_before_storing(docs_env, monkeypatch):
    """Sanitizing on WRITE is what makes every reader safe without each one
    remembering to sanitize. Proven by reading the stored row back."""
    ctx = company_client(monkeypatch)
    doc_id = _create(ctx, body_html="<p>ok</p><script>alert(1)</script>").json()["id"]
    stored = ctx.client.get(f"/v1/custom-artifacts/{doc_id}").json()
    assert "<script" not in stored["body_html"]
    assert "ok" in stored["body_html"]


def test_listing_omits_bodies(docs_env, monkeypatch):
    """A library of N documents must not ship N full documents."""
    ctx = company_client(monkeypatch)
    _create(ctx, title="A", body_html="<p>" + "x" * 5000 + "</p>")
    rows = ctx.client.get("/v1/custom-artifacts").json()["artifacts"]
    assert len(rows) == 1
    assert "body_html" not in rows[0]
    assert rows[0]["title"] == "A"


def test_listing_is_newest_first(docs_env, monkeypatch):
    ctx = company_client(monkeypatch)
    _create(ctx, title="first")
    _create(ctx, title="second")
    titles = [r["title"] for r in ctx.client.get("/v1/custom-artifacts").json()["artifacts"]]
    assert titles == ["second", "first"]


def test_by_conversation_returns_only_that_chats_documents(docs_env, monkeypatch):
    ctx = company_client(monkeypatch)
    a = _seed_conversation(company_id=ctx.company_id, title="chat A")
    b = _seed_conversation(company_id=ctx.company_id, title="chat B")
    _create(ctx, title="in chat A", conversation_id=a)
    _create(ctx, title="in chat B", conversation_id=b)
    _create(ctx, title="standalone")
    rows = ctx.client.get(f"/v1/custom-artifacts/by-conversation/{a}").json()["artifacts"]
    assert [r["title"] for r in rows] == ["in chat A"]


def test_cannot_attach_a_document_to_another_tenants_chat(docs_env, monkeypatch):
    """`conversation_id` is the one id the CLIENT picks, so it is the one that
    can be forged. Ids are sequential integers and the artifacts listing turns
    a document's conversation into a TITLE — so an unchecked id would let a
    caller attach their own document to a foreign chat and read that chat's
    name back out of their own library. 404, never 403."""
    outsider = "outsider-" + uuid.uuid4().hex[:8]
    other_cid = seed_company(user_id=outsider, slug="rival")
    ctx = company_client(monkeypatch)
    foreign_chat = _seed_conversation(company_id=other_cid, title="Their secret deal")

    r = _create(ctx, title="mine", conversation_id=foreign_chat)
    assert r.status_code == 404
    # Nothing was stored, so the title cannot leak through the listing later.
    assert ctx.client.get("/v1/custom-artifacts").json()["artifacts"] == []


def test_a_nonexistent_conversation_is_refused_too(docs_env, monkeypatch):
    ctx = company_client(monkeypatch)
    assert _create(ctx, title="x", conversation_id=987654).status_code == 404


# ─── Update ──────────────────────────────────────────────────────────────────

def test_patch_body_bumps_version_and_leaves_title_alone(docs_env, monkeypatch):
    """A body autosave must not clobber a title someone renamed elsewhere."""
    ctx = company_client(monkeypatch)
    doc_id = _create(ctx, title="Keep me", body_html="<p>a</p>").json()["id"]
    r = ctx.client.patch(f"/v1/custom-artifacts/{doc_id}", json={"body_html": "<p>b</p>"})
    assert r.status_code == 200
    assert r.json()["title"] == "Keep me"
    assert r.json()["body_html"] == "<p>b</p>"
    assert r.json()["version"] == 2


def test_patch_title_leaves_body_alone(docs_env, monkeypatch):
    ctx = company_client(monkeypatch)
    doc_id = _create(ctx, title="old", body_html="<p>body</p>").json()["id"]
    r = ctx.client.patch(f"/v1/custom-artifacts/{doc_id}", json={"title": "new"})
    assert r.status_code == 200
    assert r.json()["title"] == "new" and r.json()["body_html"] == "<p>body</p>"


def test_patch_sanitizes_too(docs_env, monkeypatch):
    """The create path is not the only way HTML gets in."""
    ctx = company_client(monkeypatch)
    doc_id = _create(ctx).json()["id"]
    r = ctx.client.patch(
        f"/v1/custom-artifacts/{doc_id}", json={"body_html": '<p onclick="x()">t</p>'}
    )
    assert "onclick" not in r.json()["body_html"]


def test_matching_base_version_is_accepted(docs_env, monkeypatch):
    ctx = company_client(monkeypatch)
    doc_id = _create(ctx, body_html="<p>a</p>").json()["id"]
    r = ctx.client.patch(
        f"/v1/custom-artifacts/{doc_id}",
        json={"body_html": "<p>b</p>", "base_version": 1},
    )
    assert r.status_code == 200 and r.json()["version"] == 2


def test_stale_base_version_is_refused_with_the_current_document(docs_env, monkeypatch):
    """THE lost-update case: two members edit the same paragraph.

    The second save started from version 1, but version 1 is gone. It must be
    refused — and refused with the winner's text, so the editor can say who
    moved it instead of throwing the user's work away with a bare error.
    """
    ctx = company_client(monkeypatch)
    doc_id = _create(ctx, body_html="<p>original</p>").json()["id"]
    # Colleague saves first.
    ctx.client.patch(
        f"/v1/custom-artifacts/{doc_id}",
        json={"body_html": "<p>theirs</p>", "base_version": 1},
    )
    # We were still holding version 1.
    r = ctx.client.patch(
        f"/v1/custom-artifacts/{doc_id}",
        json={"body_html": "<p>mine</p>", "base_version": 1},
    )
    assert r.status_code == 409
    detail = r.json()["detail"]
    assert detail["error"] == "version_conflict"
    assert detail["current"]["body_html"] == "<p>theirs</p>"
    assert detail["current"]["version"] == 2
    # And the loser's text was NOT written.
    assert ctx.client.get(f"/v1/custom-artifacts/{doc_id}").json()["body_html"] == "<p>theirs</p>"


def test_omitting_base_version_accepts_last_write_wins(docs_env, monkeypatch):
    """Renaming from the library row has nothing to lose, so it need not carry
    a version. This is the opt-out, and it must keep working."""
    ctx = company_client(monkeypatch)
    doc_id = _create(ctx, title="a").json()["id"]
    ctx.client.patch(f"/v1/custom-artifacts/{doc_id}", json={"title": "b", "base_version": 1})
    r = ctx.client.patch(f"/v1/custom-artifacts/{doc_id}", json={"title": "c"})
    assert r.status_code == 200 and r.json()["title"] == "c"


def test_oversized_body_is_refused_not_truncated(docs_env, monkeypatch):
    """Silent truncation is the worst outcome: the user finds out later, by
    noticing the end of their document is gone."""
    ctx = company_client(monkeypatch)
    doc_id = _create(ctx).json()["id"]
    r = ctx.client.patch(
        f"/v1/custom-artifacts/{doc_id}", json={"body_html": "x" * 400_001}
    )
    assert r.status_code == 413


def test_a_body_that_grows_past_the_ceiling_WHILE_SANITIZING_is_refused(
    docs_env, monkeypatch
):
    """The size that matters is the size that gets STORED.

    Sanitizing ESCAPES `&`, `<` and `>`, so a body can grow ~5x on the way
    through. A ceiling checked on the RAW input therefore passes a document
    that the storage layer then has to cut: 399,007 chars of `&` sanitize to
    1,995,035, and the user gets a 200 OK with ~80% of their document gone.
    The `"x" * 400_001` case above cannot catch this, because `x` does not
    expand — which is exactly why it passed while the bug was live.
    """
    ctx = company_client(monkeypatch)
    doc_id = _create(ctx).json()["id"]
    r = ctx.client.patch(
        f"/v1/custom-artifacts/{doc_id}", json={"body_html": "&" * 399_007}
    )
    assert r.status_code == 413
    # And nothing was written — a refused save must not half-land.
    assert ctx.client.get(f"/v1/custom-artifacts/{doc_id}").json()["body_html"] == ""


def test_create_with_an_expanding_oversized_body_is_refused_too(docs_env, monkeypatch):
    ctx = company_client(monkeypatch)
    assert _create(ctx, body_html="&" * 399_007).status_code == 413


# ─── Delete ──────────────────────────────────────────────────────────────────

def test_delete_removes_it(docs_env, monkeypatch):
    ctx = company_client(monkeypatch)
    doc_id = _create(ctx, title="bye").json()["id"]
    assert ctx.client.delete(f"/v1/custom-artifacts/{doc_id}").status_code == 200
    assert ctx.client.get(f"/v1/custom-artifacts/{doc_id}").status_code == 404
    assert ctx.client.get("/v1/custom-artifacts").json()["artifacts"] == []


def test_delete_of_a_missing_document_is_404(docs_env, monkeypatch):
    ctx = company_client(monkeypatch)
    assert ctx.client.delete("/v1/custom-artifacts/999").status_code == 404


# ─── Sharing: a colleague can read AND write ─────────────────────────────────

def test_a_colleague_can_read_and_edit_a_document_they_did_not_create(
    docs_env, monkeypatch
):
    """The product requirement, asserted directly.

    A second member of the SAME company gets 200 on read, on save and on
    delete. If a future change scopes any of these by `created_by`, this test
    is what fails — which is the point, because the resulting bug ("I can't
    edit my teammate's doc") is invisible to every test that only ever uses
    one user.
    """
    ctx = company_client(monkeypatch)
    doc_id = _create(ctx, title="team doc", body_html="<p>a</p>").json()["id"]

    # A second member of the same company, sharing the seeded company row.
    colleague_id = "colleague-" + uuid.uuid4().hex[:8]
    from app.db.client import require_client

    require_client().table("company_members").insert(
        {
            "id": uuid.uuid4().hex,
            "company_id": ctx.company_id,
            "user_id": colleague_id,
            "role": "member",
        }
    ).execute()
    headers = supabase_bearer(colleague_id)

    assert ctx.client.get(f"/v1/custom-artifacts/{doc_id}", headers=headers).status_code == 200
    saved = ctx.client.patch(
        f"/v1/custom-artifacts/{doc_id}",
        json={"body_html": "<p>edited by colleague</p>"},
        headers=headers,
    )
    assert saved.status_code == 200
    assert saved.json()["body_html"] == "<p>edited by colleague</p>"
    # Attribution follows the editor, while ownership does not move.
    assert saved.json()["updated_by"] == colleague_id
    assert ctx.client.delete(f"/v1/custom-artifacts/{doc_id}", headers=headers).status_code == 200


# ─── Tenancy: another company sees nothing, and cannot tell ──────────────────

@pytest.fixture
def other_company(monkeypatch):
    """A SECOND company with its own member, sharing the same fake DB."""
    user_id = "outsider-" + uuid.uuid4().hex[:8]
    seed_company(user_id=user_id, slug="rival")
    return SimpleNamespaceCompat(user_id=user_id, headers=supabase_bearer(user_id))


class SimpleNamespaceCompat:
    def __init__(self, **kw):
        self.__dict__.update(kw)


def test_foreign_company_cannot_read_and_gets_404_not_403(
    docs_env, monkeypatch, other_company
):
    ctx = company_client(monkeypatch)
    doc_id = _create(ctx, title="secret").json()["id"]
    r = ctx.client.get(f"/v1/custom-artifacts/{doc_id}", headers=other_company.headers)
    # 404, never 403: a 403 confirms the id exists.
    assert r.status_code == 404


def test_foreign_company_cannot_edit(docs_env, monkeypatch, other_company):
    ctx = company_client(monkeypatch)
    doc_id = _create(ctx, body_html="<p>ours</p>").json()["id"]
    r = ctx.client.patch(
        f"/v1/custom-artifacts/{doc_id}",
        json={"body_html": "<p>theirs</p>"},
        headers=other_company.headers,
    )
    assert r.status_code == 404
    # And nothing was written.
    assert ctx.client.get(f"/v1/custom-artifacts/{doc_id}").json()["body_html"] == "<p>ours</p>"


def test_foreign_company_cannot_delete(docs_env, monkeypatch, other_company):
    ctx = company_client(monkeypatch)
    doc_id = _create(ctx, title="ours").json()["id"]
    assert ctx.client.delete(
        f"/v1/custom-artifacts/{doc_id}", headers=other_company.headers
    ).status_code == 404
    assert ctx.client.get(f"/v1/custom-artifacts/{doc_id}").status_code == 200


def test_foreign_company_listing_is_empty(docs_env, monkeypatch, other_company):
    ctx = company_client(monkeypatch)
    _create(ctx, title="ours")
    rows = ctx.client.get("/v1/custom-artifacts", headers=other_company.headers)
    assert rows.json()["artifacts"] == []


def test_by_conversation_is_company_scoped_too(docs_env, monkeypatch, other_company):
    """Conversation ids are sequential integers, so a chat-scoped read that
    forgot the company filter would hand a foreign document to anyone who
    guessed one."""
    ctx = company_client(monkeypatch)
    chat = _seed_conversation(company_id=ctx.company_id)
    _create(ctx, title="ours", conversation_id=chat)
    rows = ctx.client.get(
        f"/v1/custom-artifacts/by-conversation/{chat}", headers=other_company.headers
    )
    assert rows.json()["artifacts"] == []


def test_unauthenticated_requests_are_rejected(docs_env, monkeypatch):
    ctx = company_client(monkeypatch)
    doc_id = _create(ctx).json()["id"]
    r = ctx.client.get(f"/v1/custom-artifacts/{doc_id}", headers={"Authorization": ""})
    assert r.status_code in (401, 403)


def test_finishing_a_generation_bumps_the_version(docs_env, monkeypatch):
    """A completed generation must invalidate an editor that opened the row
    while it was still writing.

    PATCH is not gated on `status`, so that editor holds version 1 and an EMPTY
    buffer. If `finish_artifact` left the version alone, its next autosave would
    pass the compare-and-set and replace the freshly generated document with
    nothing — the exact lost update the counter exists to catch.
    """
    from app.db.custom_artifacts import create_artifact, finish_artifact

    ctx = company_client(monkeypatch)
    row = create_artifact(ctx.company_id, kind="memo", status="generating")
    assert row["version"] == 1

    finish_artifact(ctx.company_id, row["id"], title="Q3", body_html="<p>generated</p>")

    # The stale editor's save is now refused instead of silently winning.
    stale = ctx.client.patch(
        f"/v1/custom-artifacts/{row['id']}",
        json={"body_html": "", "base_version": 1},
    )
    assert stale.status_code == 409
    assert ctx.client.get(
        f"/v1/custom-artifacts/{row['id']}"
    ).json()["body_html"] == "<p>generated</p>"
