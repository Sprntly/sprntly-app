"""The chat's artifact listing + thread stamps — the server half of
"what are my PRDs?" answered as clickable rows.

Two helpers on routes/chat.py, both tenant-scoped and both best-effort:

  * `_chat_artifact_list` — the SAME aggregation the Artifacts screen reads,
    narrowed to a kind, capped, and enriched: PRD rows gain the conversation
    that produced them (`conversations_for_prds`) so a click can resume the
    PRD's own thread.
  * `_attach_open_conversations` — stamps open-artifact PRD candidates with
    the same binding, which is what turns "open the checkout PRD" into the
    document WITH the chat the user had about it.

The gate rules live in the planner (`apply_gates` — junk kind coerces to
"all") and are covered here too, because a wrong kind silently lists the
wrong things.
"""
from __future__ import annotations

import pytest

from app.stories.generate import Story

# `prototypes` is deliberately NOT in conftest's shared base schema (the
# Design Agent suites each create their own richer copy — see the note at
# conftest._FAKE_SCHEMA and test_artifact_chat_summary._PROTOTYPE_DDL). The
# unified listing this suite exercises fans out over that table, so the
# columns IT reads are added suite-locally, same pattern.
_PROTOTYPE_DDL = """
CREATE TABLE IF NOT EXISTS prototypes (
    id                INTEGER PRIMARY KEY AUTOINCREMENT,
    prd_id            INTEGER,
    workspace_id      TEXT NOT NULL,
    status            TEXT NOT NULL DEFAULT 'generating',
    preview_image_url TEXT,
    is_complete       INTEGER NOT NULL DEFAULT 0,
    created_at        TEXT NOT NULL DEFAULT (datetime('now'))
);
"""


@pytest.fixture(autouse=True)
def _with_prototypes_table(isolated_settings):
    from tests import _fake_supabase

    _fake_supabase.get_fake_db().executescript(_PROTOTYPE_DDL)
    return isolated_settings


def _seed_prd(db_mod, dataset: str, *, title: str = "Checkout PRD") -> int:
    brief_id = db_mod.save_brief(
        dataset=dataset, week_label="Week of stub",
        payload={"summary_headline": "s", "insights": [{"title": "I"}],
                 "_schema_version": 1},
        schema_version=1,
    )
    prd_id = db_mod.start_prd(
        brief_id=brief_id, insight_index=0, title=title,
        template_version=1, variant="v2",
        # `source="chat"` — a PRD THE USER ASKED FOR. This file is about the
        # chat listing's own behaviour (kind filter, count trim, thread
        # binding), and the default (`brief`) is now held out of the library
        # until someone reads it (db.prds.is_hidden_from_library), which would
        # empty every assertion here for an unrelated reason.
        source="chat",
    )
    db_mod.complete_prd(prd_id, title=title, md="<h1>Doc</h1>")
    return prd_id


def _seed_conversation(company_id: str, *, title: str, prd_id: int | None = None,
                       user_id: str = "u-1") -> int:
    from app.db.client import require_client

    return require_client().table("conversations").insert({
        "company_id": company_id,
        "user_id": user_id,
        "title": title,
        "prd_id": prd_id,
    }).execute().data[0]["id"]


def _seed_set(company_id: str, *, conversation_id: int | None = None) -> int:
    from app.db.client import require_client

    return require_client().table("ticket_sets").insert({
        "company_id": company_id,
        "title": "Webhook tickets",
        "stories": [Story(title="A", body="b").to_dict()],
        "status": "ready",
        "conversation_id": conversation_id,
    }).execute().data[0]["id"]


class _Ctx:
    """The tiny slice of CompanyContext the helpers read."""

    def __init__(self, company_id: str, workspace_id=None):
        self.company_id = company_id
        self.workspace_id = workspace_id
        self.workspace_is_default = True


# ─── conversations_for_prds ──────────────────────────────────────────────────


def test_newest_conversation_wins_and_scope_is_the_company(
    tenant_client, isolated_settings
):
    from app.db.conversations import conversations_for_prds

    a = tenant_client.make(slug="acme")
    b = tenant_client.make(slug="beta")
    db_mod = isolated_settings["db"]
    prd_id = _seed_prd(db_mod, "acme")
    _seed_conversation(a.company_id, title="First chat", prd_id=prd_id)
    newest = _seed_conversation(a.company_id, title="Second chat", prd_id=prd_id)
    # A FOREIGN company's binding to the same integer id must never surface.
    _seed_conversation(b.company_id, title="Rival chat", prd_id=prd_id)

    out = conversations_for_prds([prd_id], a.company_id)
    assert out[prd_id]["id"] == newest
    assert out[prd_id]["title"] == "Second chat"

    assert conversations_for_prds([], a.company_id) == {}


# ─── _chat_artifact_list ─────────────────────────────────────────────────────


def test_the_listing_carries_prd_threads_and_honours_the_kind(
    tenant_client, isolated_settings
):
    from app.routes.chat import _chat_artifact_list

    t = tenant_client.make(slug="acme")
    db_mod = isolated_settings["db"]
    prd_id = _seed_prd(db_mod, "acme")
    conv = _seed_conversation(t.company_id, title="Checkout chat", prd_id=prd_id)
    set_conv = _seed_conversation(t.company_id, title="Tickets chat")
    set_id = _seed_set(t.company_id, conversation_id=set_conv)

    everything = _chat_artifact_list(_Ctx(t.company_id), "all")
    kinds = {i["type"] for i in everything}
    assert {"prd", "evidence", "ticket_set"} >= kinds  # no foreign kinds invented
    prd_row = next(i for i in everything if i["type"] == "prd")
    assert prd_row["id"] == prd_id
    # THE stamp: the thread a click resumes.
    assert prd_row["source"]["conversation_id"] == conv
    assert prd_row["source"]["conversation_title"] == "Checkout chat"
    assert prd_row["open"]["prd_id"] == prd_id

    set_row = next(i for i in everything if i["type"] == "ticket_set")
    assert set_row["id"] == set_id
    assert set_row["source"]["conversation_id"] == set_conv
    assert set_row["source"]["conversation_title"] == "Tickets chat"

    only_prds = _chat_artifact_list(_Ctx(t.company_id), "prd")
    assert {i["type"] for i in only_prds} == {"prd"}
    # Junk kind coerces upstream (planner) — the route treats unknown as-is
    # and simply matches nothing, never errors.
    assert _chat_artifact_list(_Ctx(t.company_id), "nonsense") == []


def test_the_asked_for_count_trims_the_listing_and_junk_never_widens_it(
    tenant_client, isolated_settings
):
    """"the latest PRD" / "my last N" must return exactly that many — the
    reported bug was 12 rows for both asks. The limit only ever TIGHTENS the
    cap: zero, negative, bool and giant values all fall back to it."""
    from app.routes.chat import _MAX_CHAT_ARTIFACTS, _chat_artifact_list

    t = tenant_client.make(slug="acme")
    db_mod = isolated_settings["db"]
    for n in range(3):
        _seed_prd(db_mod, "acme", title=f"PRD {n}")

    assert len(_chat_artifact_list(_Ctx(t.company_id), "prd", 1)) == 1
    assert len(_chat_artifact_list(_Ctx(t.company_id), "prd", 2)) == 2
    # (Which row survives a size-1 trim is the listing's own newest-first
    # order — same-second seeds tie on created_at, so it is not asserted.)
    # Junk limits fall back to the cap (here: all three rows).
    for junk in (0, -5, True, 10_000, None):
        rows = _chat_artifact_list(_Ctx(t.company_id), "prd", junk)
        assert len(rows) == 3, junk
    assert _MAX_CHAT_ARTIFACTS >= 3  # the fallback above is the cap, not luck


def test_a_prd_with_no_chat_lists_with_a_null_thread(
    tenant_client, isolated_settings
):
    """No surviving chat → both halves null — the client's fallback signal,
    never a fake history."""
    from app.routes.chat import _chat_artifact_list

    t = tenant_client.make(slug="acme")
    _seed_prd(isolated_settings["db"], "acme")

    rows = _chat_artifact_list(_Ctx(t.company_id), "prd")
    assert rows[0]["source"]["conversation_id"] is None
    assert rows[0]["source"]["conversation_title"] is None


# ─── _attach_open_conversations ──────────────────────────────────────────────


def test_open_candidates_gain_their_thread_stamp(tenant_client, isolated_settings):
    from app.routes.chat import _attach_open_conversations

    t = tenant_client.make(slug="acme")
    db_mod = isolated_settings["db"]
    prd_id = _seed_prd(db_mod, "acme")
    conv = _seed_conversation(t.company_id, title="Checkout chat", prd_id=prd_id)

    open_result = {
        "status": "resolved",
        "artifact_type": "prd",
        "query": "checkout",
        "artifact": {"type": "prd", "id": prd_id, "prd_id": prd_id, "title": "Checkout PRD"},
        "candidates": [
            {"type": "prd", "id": prd_id, "prd_id": prd_id, "title": "Checkout PRD"},
            {"type": "evidence", "id": 999, "title": "Some evidence"},
        ],
    }
    _attach_open_conversations(open_result, t.company_id)

    assert open_result["artifact"]["conversation_id"] == conv
    assert open_result["artifact"]["conversation_title"] == "Checkout chat"
    assert open_result["candidates"][0]["conversation_id"] == conv
    # Non-PRD candidates are left exactly as they were.
    assert "conversation_id" not in open_result["candidates"][1]


# ─── the planner gate ────────────────────────────────────────────────────────


@pytest.mark.parametrize("raw,expected", [
    ("prd", "prd"), ("ticket_set", "ticket_set"), ("all", "all"),
    (None, "all"), ("junk-kind", "all"),
])
def test_apply_gates_coerces_the_kind(raw, expected):
    from app import ask_planner as ap

    plan = ap.apply_gates(
        {"action": "list_artifacts", "action_confidence": 0.9,
         "list_kind": raw, "reason": "listing"},
        enterprise_id="ent-1", connected=[],
    )
    assert plan.action == "list_artifacts"
    assert plan.list_kind == expected


@pytest.mark.parametrize("raw,expected", [
    ("count", "count"), ("items", "items"), (None, "items"), ("junk", "items"),
])
def test_apply_gates_coerces_the_mode(raw, expected):
    from app import ask_planner as ap

    plan = ap.apply_gates(
        {"action": "list_artifacts", "action_confidence": 0.9,
         "list_kind": "prd", "list_mode": raw, "reason": "how many"},
        enterprise_id="ent-1", connected=[],
    )
    assert plan.list_mode == expected


def test_the_counts_come_from_the_full_library_not_the_card_cap(
    tenant_client, isolated_settings
):
    """"How many PRDs today vs yesterday?" answered with 12 cards was the
    reported bug — the tally must come from the WHOLE library, split by
    calendar date, with today/yesterday resolved server-side."""
    from datetime import date, timedelta

    from app.db.client import require_client
    from app.routes.chat import _chat_artifact_counts

    t = tenant_client.make(slug="acme")
    today = date.today().isoformat()
    yesterday = (date.today() - timedelta(days=1)).isoformat()
    c = require_client()
    for created, n in ((today, 2), (yesterday, 3)):
        for i in range(n):
            c.table("ticket_sets").insert({
                "company_id": t.company_id,
                "title": f"S-{created}-{i}",
                "stories": [Story(title="A", body="b").to_dict()],
                "status": "ready",
                "created_at": f"{created}T0{i}:00:00Z",
            }).execute()

    counts = _chat_artifact_counts(_Ctx(t.company_id), "ticket_set")
    assert counts is not None
    assert counts["kind"] == "ticket_set"
    assert counts["total"] == 5
    assert counts["today"] == 2
    assert counts["yesterday"] == 3
    by_day = {d["date"]: d["count"] for d in counts["by_day"]}
    assert by_day[today] == 2
    assert by_day[yesterday] == 3
    # Newest-first ordering of the day buckets.
    assert counts["by_day"][0]["date"] == today


def test_other_actions_carry_no_kind():
    from app import ask_planner as ap

    plan = ap.apply_gates(
        {"action": "answer", "list_kind": "prd", "reason": "q"},
        enterprise_id="ent-1", connected=[],
    )
    assert plan.list_kind is None
