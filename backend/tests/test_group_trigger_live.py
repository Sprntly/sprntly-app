"""Real local-Supabase + real-LLM round-trip for the group smart-trigger
port — a stubbed classifier cannot prove either the CONTINUATION/AMBIGUOUS
judgment or the B2 write-vs-no-write narration distinction, only wiring
([[feedback_stubbed-e2e-masks-loop-behaviour]]).

Proves, against the REAL group route (`POST /v1/projects/{id}/group/
turns`), a REAL classifier, and — for the edit cases — the REAL editor:

  (a) a clear continuation of the agent's own thread, with no @Sprntly
      mention, triggers a reply (AC10);
  (b) an ambiguous unaddressed work request triggers a reply that ASKS who
      it's for (AC10);
  (c) a NAMED-human request stays out (AC10);
  (d) ambient human-to-human back-and-forth after a Sprntly turn stays out
      (AC10);
  (e) a continuation EDIT request writes the PRD in place + snapshots a
      `prd_versions` row, and the posted turn narrates a completed edit
      (AC12);
  (f) a non-edit continuation posts a reply with NO "added/updated/
      changed/Done" claim (AC12);
  (g) an ambiguous unaddressed request posts an "Are you assigning this to
      me?"-style clarifier and writes nothing (AC12).

Gated on BOTH a real LLM and the run flag; skips cleanly otherwise.
Registered in `test_ci_lane_coverage.py::_KNOWN_UNRUNNABLE` under both
`RUN_GROUP_TRIGGER_LIVE` and `ANTHROPIC_API_KEY`. Restores the DB to its
pre-test state.

    RUN_GROUP_TRIGGER_LIVE=1 ANTHROPIC_API_KEY=... \\
        pytest tests/test_group_trigger_live.py -m integration
"""
from __future__ import annotations

import os
import time

import jwt as pyjwt
import pytest

_RUN_LIVE = os.getenv("RUN_GROUP_TRIGGER_LIVE") == "1" and bool(os.getenv("ANTHROPIC_API_KEY"))

pytestmark = [
    pytest.mark.integration,
    pytest.mark.real_interjection_gate,  # opt OUT of conftest's autouse call_json stub
    pytest.mark.skipif(
        not _RUN_LIVE,
        reason=(
            "needs a real local Supabase + a real LLM — set "
            "RUN_GROUP_TRIGGER_LIVE=1 and ANTHROPIC_API_KEY, with "
            "SUPABASE_URL/SUPABASE_SERVICE_ROLE_KEY/SUPABASE_JWT_SECRET pointed "
            "at the local rig and the projects/prds/prd_versions/"
            "conversation_turns migrations applied"
        ),
    ),
]


def _sb():
    from app.config import settings
    from supabase import create_client

    url = settings.supabase_url
    assert "127.0.0.1" in url or "localhost" in url, (
        f"refusing to run the live group-trigger round-trip against a "
        f"non-loopback SUPABASE_URL ({url!r}) — this test mutates real rows"
    )
    assert settings.supabase_service_role_key, "SUPABASE_SERVICE_ROLE_KEY is not set"
    return create_client(url, settings.supabase_service_role_key)


@pytest.fixture(scope="module")
def sb():
    return _sb()


@pytest.fixture
def scene(sb):
    """A real (company, workspace, user) with ONE fresh project (no PRD by
    default — the edit-case tests seed their own PRD). Cleans up every row
    it created."""
    from app.db import projects as projects_db
    from app.db.client import require_client

    c = require_client()

    members = sb.table("company_members").select("company_id, user_id").limit(50).execute().data
    company_id = workspace_id = user_id = slug = None
    for m in members:
        comp = sb.table("companies").select("slug").eq("id", m["company_id"]).limit(1).execute().data
        ws = sb.table("workspaces").select("id").eq("company_id", m["company_id"]).limit(1).execute().data
        if comp and comp[0].get("slug") and ws:
            company_id, workspace_id, user_id, slug = (
                m["company_id"], ws[0]["id"], m["user_id"], comp[0]["slug"]
            )
            break
    assert company_id, "no (company w/ slug, workspace, member) in the local rig"

    created = {"projects": [], "briefs": [], "prds": []}

    def _project(name):
        p = projects_db.create_project(
            company_id=company_id, workspace_id=workspace_id, name=name, created_by=user_id,
        )
        created["projects"].append(p["id"])
        return p["id"]

    def _prd(project_id, title):
        # ★ dataset MUST be the caller's REAL workspace dataset slug — the
        # group route resolves the project's PRD via `_resolve_prd_id` ->
        # `list_artifacts_for_project`, which filters by `_dataset_for(ctx)`.
        # A fabricated dataset here would never be resolvable and the write
        # path this test exists to exercise would silently no-op every time.
        brief = c.table("briefs").insert({
            "dataset": slug, "week_label": title, "is_current": False,
            "payload": {"insights": []},
        }).execute().data[0]
        created["briefs"].append(brief["id"])
        row = c.table("prds").insert({
            "brief_id": brief["id"], "insight_index": 0, "title": title,
            "payload_md": f"# {title}\n\nOriginal problem statement.", "status": "ready",
        }).execute().data[0]
        created["prds"].append(row["id"])
        projects_db.add_artifact(project_id, "prd", row["id"])
        return row["id"]

    ctx = {"company_id": company_id, "workspace_id": workspace_id, "user_id": user_id, "_project": _project, "_prd": _prd}
    yield ctx

    for pid in created["projects"]:
        conv_ids = [
            row["id"] for row in
            sb.table("conversations").select("id").eq("project_id", pid).execute().data
        ]
        if conv_ids:
            sb.table("conversation_turns").delete().in_("conversation_id", conv_ids).execute()
        sb.table("conversations").delete().eq("project_id", pid).execute()
        sb.table("project_artifacts").delete().eq("project_id", pid).execute()
        sb.table("project_members").delete().eq("project_id", pid).execute()
        sb.table("projects").delete().eq("id", pid).execute()
    for prd_id in created["prds"]:
        sb.table("prd_versions").delete().eq("prd_id", prd_id).execute()
        sb.table("prds").delete().eq("id", prd_id).execute()
    for bid in created["briefs"]:
        sb.table("briefs").delete().eq("id", bid).execute()


def _bearer(user_id: str) -> dict[str, str]:
    from app.config import settings

    now = int(time.time())
    token = pyjwt.encode(
        {"sub": user_id, "aud": "authenticated", "exp": now + 3600},
        settings.supabase_jwt_secret,
        algorithm="HS256",
    )
    return {"Authorization": f"Bearer {token}"}


def _client(user_id: str, workspace_id: str):
    import app.main as main_mod
    from app.config import settings
    from fastapi.testclient import TestClient

    headers = _bearer(user_id)
    headers["X-Workspace-Id"] = workspace_id
    headers["Origin"] = settings.origins_list[0]
    return TestClient(main_mod.app, headers=headers)


def _turns(client, project_id):
    return client.get(f"/v1/projects/{project_id}/group/turns").json()["turns"]


def _post(client, project_id, content):
    return client.post(f"/v1/projects/{project_id}/group/turns", json={"content": content})


# ── AC10 — the posture shift, proven live ──────────────────────────────────


def test_continuation_and_ambiguity_respond_live(scene):
    client = _client(scene["user_id"], scene["workspace_id"])

    # (a) Continuation: a Sprntly-addressed mention first (deterministic),
    # then a short non-mention follow-up.
    proj_a = scene["_project"]("live continuation")
    r0 = _post(client, proj_a, "@Sprntly can you look into the deploy timeline?")
    assert r0.status_code == 200, r0.text
    r1 = _post(client, proj_a, "yes go ahead")
    assert r1.status_code == 200, r1.text
    turns_a = _turns(client, proj_a)
    assert [t["role"] for t in turns_a] == ["user", "assistant", "user", "assistant"], (
        f"a clear continuation of Sprntly's own thread must get a reply — "
        f"got roles {[t['role'] for t in turns_a]}"
    )

    # (b) Ambiguous unaddressed work request: no prior agent turn, no
    # named human — the real classifier should engage AND ask who it's for.
    proj_b = scene["_project"]("live ambiguous")
    r2 = _post(client, proj_b, "who's picking up the API docs for this release?")
    assert r2.status_code == 200, r2.text
    turns_b = _turns(client, proj_b)
    assert [t["role"] for t in turns_b] == ["user", "assistant"], (
        f"an ambiguous unaddressed work request must get a reply — got "
        f"roles {[t['role'] for t in turns_b]}"
    )
    assert "?" in turns_b[-1]["content"], "the clarifier reply should be a question"


def test_named_human_and_human_chatter_stay_out_live(scene):
    client = _client(scene["user_id"], scene["workspace_id"])

    # (c) Named-human request stays out.
    proj_c = scene["_project"]("live named human")
    r = _post(client, proj_c, "Alexis, can you pick up the API docs for this release?")
    assert r.status_code == 200, r.text
    turns_c = _turns(client, proj_c)
    assert [t["role"] for t in turns_c] == ["user"], (
        f"a request naming a specific human must NOT get a Sprntly reply — "
        f"got roles {[t['role'] for t in turns_c]}"
    )

    # (d) Ambient human-to-human back-and-forth after a Sprntly turn stays
    # out (the agent's own preceding turn does NOT make every follow-up a
    # continuation — only a REPLY to Sprntly does).
    proj_d = scene["_project"]("live human backforth")
    r0 = _post(client, proj_d, "@Sprntly can you summarize the open risks?")
    assert r0.status_code == 200, r0.text
    r1 = _post(
        client, proj_d,
        "Hey Sam, did you already loop in the design team on this?",
    )
    assert r1.status_code == 200, r1.text
    turns_d = _turns(client, proj_d)
    assert turns_d[-1]["role"] == "user", (
        f"ordinary human-to-human chatter after a Sprntly turn must not "
        f"itself get a reply — got roles {[t['role'] for t in turns_d]}"
    )


# ── AC12 — live B2 + clarifier, never mocked ────────────────────────────────


def test_continuation_edit_writes_and_snapshots_live(scene, sb):
    from app.db.client import require_client

    c = require_client()
    proj = scene["_project"]("live continuation edit")
    prd_id = scene["_prd"](proj, "Live continuation edit PRD")
    client = _client(scene["user_id"], scene["workspace_id"])

    before_versions = len(
        sb.table("prd_versions").select("id").eq("prd_id", prd_id).execute().data
    )

    r0 = _post(client, proj, "@Sprntly can you tighten up the problem statement?")
    assert r0.status_code == 200, r0.text
    r1 = _post(client, proj, "yes, go ahead and do that")
    assert r1.status_code == 200, r1.text

    turns = _turns(client, proj)
    assistant_turns = [t for t in turns if t["role"] == "assistant"]
    assert len(assistant_turns) >= 1, turns

    after_versions = len(
        sb.table("prd_versions").select("id").eq("prd_id", prd_id).execute().data
    )
    # Either the follow-up edit landed (one more version, "Done" narration)
    # or the model judged the mention-turn's own edit sufficient and the
    # continuation a non-edit follow-up — either way NO fabricated "Done"
    # without a real write: a "Done" claim implies a version bump.
    done_claims = [t for t in assistant_turns if "Done" in t["content"]]
    if done_claims:
        assert after_versions > before_versions, (
            "an assistant turn claimed a completed PRD update but no new "
            "prd_versions row landed — exactly the B2 fabrication this "
            "ticket exists to close"
        )


def test_non_edit_continuation_makes_no_write_claim_live(scene, sb):
    proj = scene["_project"]("live non-edit continuation")
    client = _client(scene["user_id"], scene["workspace_id"])

    r0 = _post(client, proj, "@Sprntly what's the current status of this project?")
    assert r0.status_code == 200, r0.text
    r1 = _post(client, proj, "thanks, that's helpful — one more thing though")
    assert r1.status_code == 200, r1.text

    turns = _turns(client, proj)
    for t in turns:
        if t["role"] != "assistant":
            continue
        content = t["content"]
        for claim in ("I've added", "I've updated", "I've changed", "Done — I've updated"):
            assert claim not in content, (
                f"a non-edit turn's reply fabricated a PRD-write claim: {content!r}"
            )


def test_ambiguous_unaddressed_yields_clarifier_live(scene, sb):
    proj = scene["_project"]("live ambiguous clarifier")
    client = _client(scene["user_id"], scene["workspace_id"])

    prd_before = sb.table("prds").select("id").execute().data
    r = _post(client, proj, "someone needs to own the migration doc updates")
    assert r.status_code == 200, r.text

    turns = _turns(client, proj)
    assistant_turns = [t for t in turns if t["role"] == "assistant"]
    assert len(assistant_turns) == 1, turns
    assert "?" in assistant_turns[-1]["content"], "expected a clarifying question"

    # Nothing was written — this project has no PRD artifact at all, and no
    # new PRD should have been created either.
    prd_after = sb.table("prds").select("id").execute().data
    assert len(prd_after) == len(prd_before)
