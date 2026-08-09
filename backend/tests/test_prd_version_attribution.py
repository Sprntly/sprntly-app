"""PRD version snapshots record WHO saved, not just that something did.

`prd_versions.saved_by` was the literal string "auto" on every automatic
snapshot, so history told you a save happened and never who made it. When two
users edit the same PRD — the 2026-08-03 report where one user's edits stayed
invisible to the other — the version table could not attribute a single row,
so the data could not confirm or refute the report on its own.

`saved_by` is free text rendered straight into the history list
("Edit · {saved_by} · {date}"), so the acting user's address goes in it with no
schema or frontend change, and pre-existing "auto" rows still render.
"""
from __future__ import annotations

import uuid

import app.auth  # noqa: F401 — load app.config + app.auth into sys.modules

from tests._company_helpers import company_client, supabase_bearer


def _bearer_with_email(user_id: str, email: str) -> dict[str, str]:
    """A Supabase bearer carrying an `email` claim — that claim, not the
    profiles row, is what populates CompanyContext.user_email."""
    import time

    import jwt

    from tests._company_helpers import SUPABASE_JWT_SECRET

    token = jwt.encode(
        {"sub": user_id, "aud": "authenticated", "email": email, "exp": int(time.time()) + 3600},
        SUPABASE_JWT_SECRET,
        algorithm="HS256",
    )
    return {"Authorization": f"Bearer {token}"}


def _rows(table: str, **eq) -> list[dict]:
    from app.db.client import require_client

    q = require_client().table(table).select("*")
    for k, v in eq.items():
        q = q.eq(k, v)
    return q.execute().data or []


def _seed_profile(user_id: str, email: str) -> None:
    from app.db.client import require_client

    require_client().table("profiles").insert(
        {"id": user_id, "email": email, "first_name": "T", "last_name": "U"}
    ).execute()


def _seed_prd(company_id: str, *, dataset: str = "acme", title: str = "Doc", payload: str = "<p>v0</p>") -> int:
    """A minimal ready PRD owned by `company_id`, via the same dataset binding
    require_owned_prd checks."""
    from app.db.client import require_client

    c = require_client()
    # Ownership resolves prd -> brief -> dataset -> workspace/company, so the
    # PRD needs a brief on the company's own dataset (the default workspace's
    # dataset is the bare company slug).
    brief_id = int(uuid.uuid4().int % 1_000_000)
    c.table("briefs").insert(
        {"id": brief_id, "dataset": dataset, "week_label": "W1", "payload": {}}
    ).execute()
    prd_id = int(uuid.uuid4().int % 1_000_000)
    c.table("prds").insert(
        {
            "id": prd_id,
            "brief_id": brief_id,
            "insight_index": 0,
            "title": title,
            "payload_md": payload,
            "status": "ready",
            "source": dataset,
        }
    ).execute()
    return prd_id


def test_put_records_the_editing_user_not_the_literal_auto(isolated_settings, monkeypatch):
    ctx = company_client(monkeypatch)
    prd_id = _seed_prd(ctx.company_id)

    r = ctx.client.put(
        f"/v1/prd/{prd_id}",
        json={"title": "Doc", "payload_md": "<p>v1 — my edit</p>"},
        headers=_bearer_with_email(ctx.user_id, "david+test1@sprntly.ai"),
    )
    assert r.status_code == 200, r.text

    versions = _rows("prd_versions", prd_id=prd_id)
    assert len(versions) == 1, versions
    saved_by = versions[0]["saved_by"]
    # The whole point: the row names a person, so two-user edit history is
    # attributable instead of a wall of identical "auto".
    assert saved_by != "auto"
    assert saved_by == "david+test1@sprntly.ai"


def test_two_users_editing_one_prd_are_told_apart_in_history(isolated_settings, monkeypatch):
    """The case that motivated this: reconstructing who changed what."""
    david = company_client(monkeypatch)
    prd_id = _seed_prd(david.company_id)

    assert david.client.put(
        f"/v1/prd/{prd_id}",
        json={"title": "Doc", "payload_md": "<p>davids edit</p>"},
        headers=_bearer_with_email(david.user_id, "david+test1@sprntly.ai"),
    ).status_code == 200

    # A second member of the SAME company edits the same PRD.
    from app.db.client import require_client

    jide_id = "test-user-" + uuid.uuid4().hex[:8]
    require_client().table("company_members").insert(
        {
            "id": uuid.uuid4().hex,
            "company_id": david.company_id,
            "user_id": jide_id,
            # org admin implicitly administers every workspace, so no
            # workspace_members row is needed for this test's purpose
            "role": "admin",
        }
    ).execute()

    from fastapi.testclient import TestClient
    import app.main as main_mod

    jide = TestClient(main_mod.app, headers=_bearer_with_email(jide_id, "jide@sprntly.ai"))
    assert jide.put(
        f"/v1/prd/{prd_id}", json={"title": "Doc", "payload_md": "<p>jides edit</p>"}
    ).status_code == 200

    history = sorted(_rows("prd_versions", prd_id=prd_id), key=lambda v: v["version_number"])
    assert [v["saved_by"] for v in history] == [
        "david+test1@sprntly.ai",
        "jide@sprntly.ai",
    ], history


def test_falls_back_to_user_id_when_the_profile_carries_no_email(isolated_settings, monkeypatch):
    ctx = company_client(monkeypatch)  # no profile row → no email on the context
    prd_id = _seed_prd(ctx.company_id)

    assert ctx.client.put(
        f"/v1/prd/{prd_id}", json={"title": "Doc", "payload_md": "<p>v1</p>"}
    ).status_code == 200

    saved_by = _rows("prd_versions", prd_id=prd_id)[0]["saved_by"]
    # Still attributable — an id beats the anonymous "auto" it used to write.
    assert saved_by == ctx.user_id
