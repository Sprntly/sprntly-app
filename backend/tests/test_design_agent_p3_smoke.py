# test_design_agent_p3_smoke.py
#
# Phase-3 end-to-end smoke test (P3-13).
#
# A single test walks the full Scenario A iteration loop against the real
# FastAPI app (in-process ASGI), the fake-Supabase harness, and a mocked
# Anthropic client:
#
#     generate -> ready -> comment -> iterate -> re-ready
#               -> propose-patch -> accept-patch -> closed F11 render loop
#
# The per-ticket unit tests (P3-08 .. P3-17) each cover one route/helper in
# isolation; this is the integration backstop that proves they compose. When
# this breaks, a P3 surface has regressed at a seam.
#
# Mock infrastructure (the scripted Anthropic client + response/block
# primitives + the poll-until-ready helper) is reused from the generate-route
# test, which is the single verified source of the tool-call block shape the
# runner expects (see runner.py: it reads block.type/.name/.id/.input). The
# scripted tool calls themselves are built inline below with explicit,
# readable content so the Scenario A walk is self-documenting and fully
# deterministic.

import pytest
import httpx
from httpx import ASGITransport

from app.main import app
from app.design_agent import runner as runner_mod
from tests.fake_supabase import make_fake_supabase, install_fake_supabase
from tests.test_design_agent_generate import (
    _Block,
    _Msg,
    _FakeClient,
    _end_turn_msg,
    _poll_until,
)


# ---------- deterministic prototype content ----------
#
# The generate run writes INITIAL_TSX; the iterate run line-replaces
# OLD_LABEL -> NEW_LABEL inside it. Asserting NEW_LABEL (and the absence of
# OLD_LABEL) in current_files proves the edit was applied (AC4).

PRD_ID = "prd_p3_smoke"
PRD_BODY = "# Smoke PRD\n\nBuild a hero with a call-to-action button."

APP_PATH = "src/App.tsx"
OLD_LABEL = "Get Started"
NEW_LABEL = "Start Free Trial"
INITIAL_TSX = (
    "export default function App() {\n"
    f"  return <button data-cta>{OLD_LABEL}</button>;\n"
    "}\n"
)

ANCHOR_ID = "anchor-hero-cta"
COMMENT_BODY = "Rename the CTA button."

PATCH_TEXT = "Rename the primary CTA to 'Start Free Trial'."
PATCH_SECTION = "Features"


# ---------- scripted Anthropic tool-call messages (explicit, inline) ----------

def _write_msg(path: str, content: str) -> "_Msg":
    return _Msg(
        stop_reason="tool_use",
        content=[_Block("tool_use", id="tu_write", name="write",
                        input={"path": path, "content": content})],
    )


def _line_replace_msg(path: str, old_str: str, new_str: str) -> "_Msg":
    return _Msg(
        stop_reason="tool_use",
        content=[_Block("tool_use", id="tu_line_replace", name="line_replace",
                        input={"path": path, "old_str": old_str, "new_str": new_str})],
    )


def _propose_prd_patch_msg(patch_md: str, section: str) -> "_Msg":
    return _Msg(
        stop_reason="tool_use",
        content=[_Block("tool_use", id="tu_patch", name="propose_prd_patch",
                        input={"patch_md": patch_md, "section": section})],
    )


# ---------- fixtures ----------

@pytest.fixture
def fake_db():
    # Fresh in-memory Supabase per test; install_fake_supabase rebinds the
    # storage seam. Each test installs its own fake, so the next test's install
    # overwrites this one — no cross-test residue (AC7). Mirrors the existing
    # design-agent route-test fixtures, which carry no explicit teardown.
    fake = make_fake_supabase()
    install_fake_supabase(fake)
    yield fake


@pytest.fixture
def seeded_prd(fake_db):
    fake_db.table("prds").insert({
        "id": PRD_ID,
        "payload_md": PRD_BODY,
        "title": "Smoke PRD",
    }).execute()
    return PRD_ID


# ---------- the smoke test ----------

@pytest.mark.asyncio
async def test_scenario_a_full_iteration_loop(fake_db, seeded_prd, monkeypatch):
    # One shared scripted client drives BOTH agent runs in order:
    #   generate run: write -> end_turn                     (-> RunResult complete)
    #   iterate  run: line_replace -> propose_prd_patch     (-> RunResult proposed_patch)
    # The runner returns immediately on the propose_prd_patch sentinel, so the
    # trailing end_turn documents intent but is never consumed.
    script = [
        _write_msg(APP_PATH, INITIAL_TSX),
        _end_turn_msg(),
        _line_replace_msg(APP_PATH, OLD_LABEL, NEW_LABEL),
        _propose_prd_patch_msg(PATCH_TEXT, PATCH_SECTION),
        _end_turn_msg(),
    ]
    fake_client = _FakeClient(script)
    monkeypatch.setattr(runner_mod, "get_design_agent_client", lambda: fake_client)

    transport = ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:

        # --- AC2: GENERATE -> READY -----------------------------------------
        r = await client.post("/v1/design-agent/prototypes",
                              json={"prd_id": seeded_prd, "name": "SmokeProto"})
        assert r.status_code == 201, r.text
        proto_id = r.json()["id"]

        proto = await _poll_until(client, proto_id, "ready")
        assert proto["status"] == "ready"
        assert proto["current_files"], "generate produced no files"
        assert proto["current_files"][APP_PATH] == INITIAL_TSX
        assert OLD_LABEL in proto["current_files"][APP_PATH]

        # --- AC3: COMMENT (via the comment-ingestion route, P3-10) ----------
        r = await client.post(
            f"/v1/design-agent/prototypes/{proto_id}/comments",
            json={"anchor_id": ANCHOR_ID, "body": COMMENT_BODY},
        )
        assert r.status_code == 201, r.text
        comment = r.json()
        comment_id = comment["id"]
        assert comment["anchor_id"] == ANCHOR_ID
        assert comment["status"] == "open"

        # --- AC4 + AC5: ITERATE -> RE-READY + PROPOSE PATCH -----------------
        r = await client.post(
            f"/v1/design-agent/prototypes/{proto_id}/iterate",
            json={"comment_ids": [comment_id]},
        )
        assert r.status_code == 202, r.text

        proto = await _poll_until(client, proto_id, "ready")
        # AC4: the edit is reflected in current_files.
        assert proto["current_files"][APP_PATH].count(NEW_LABEL) == 1
        assert OLD_LABEL not in proto["current_files"][APP_PATH]
        # AC4: the consumed comment is resolved by the P3-12 state machine,
        # and the in-flight pending-iteration marker is cleared.
        resolved = (fake_db.table("prototype_comments")
                    .select("*").eq("id", comment_id).execute())
        assert resolved.data[0]["status"] == "resolved"
        assert proto.get("pending_iteration") in (None, {}), \
            "pending-iteration marker not cleared after iterate"

        # AC5: a single proposed patch row lands in the prd_patches sibling
        # table — the F11 'pending' change awaiting human accept/reject.
        patches = (fake_db.table("prd_patches")
                   .select("*").eq("prototype_id", proto_id).execute())
        assert len(patches.data) == 1
        patch = patches.data[0]
        assert patch["status"] == "proposed"
        assert patch["prd_id"] == seeded_prd
        assert patch["patch_md"] == PATCH_TEXT
        patch_id = patch["id"]

        # --- AC6b (pre-accept render): the closed F11 loop, OPEN end --------
        # Before accept, get_prd_rendered must NOT surface the proposed patch.
        raw_before = (fake_db.table("prds")
                      .select("*").eq("id", seeded_prd).execute()).data[0]["payload_md"]
        r = await client.get(f"/v1/prd/{seeded_prd}")
        assert r.status_code == 200, r.text
        pre_accept_md = r.json()["payload_md"]
        assert PATCH_TEXT not in pre_accept_md, \
            "proposed (un-accepted) patch leaked into rendered PRD"
        assert "Design Agent updates" not in pre_accept_md

        # --- AC6: ACCEPT PATCH ----------------------------------------------
        r = await client.post(f"/v1/design-agent/prd-patches/{patch_id}/accept")
        assert r.status_code == 200, r.text
        assert r.json()["status"] == "accepted"

        # --- AC6b (post-accept render): the closed F11 loop, CLOSED end -----
        r = await client.get(f"/v1/prd/{seeded_prd}")
        assert r.status_code == 200, r.text
        post_accept_md = r.json()["payload_md"]
        assert PATCH_TEXT in post_accept_md, \
            "accepted patch did not render into the PRD"
        assert "Design Agent updates" in post_accept_md

        # F11: the raw prds.payload_md is byte-identical before vs after — the
        # patch is appended at render time only, never ALTERed into the row.
        raw_after = (fake_db.table("prds")
                     .select("*").eq("id", seeded_prd).execute()).data[0]["payload_md"]
        assert raw_after == raw_before == PRD_BODY
