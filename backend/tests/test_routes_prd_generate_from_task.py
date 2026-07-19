"""Tests for POST /v1/prd/generate-from-task — the chat "generate a PRD for
<specific need>" command.

The task text is the source: the route synthesizes an insight from the user's
own words (title derived from the text, summary carrying the full ask) and
feeds it to the standard prd-author pipeline via insight_override — the same
shape the ideation/import paths use. Rows are marked source='chat' with a
synthetic theme_id ('chat:<hash-of-normalized-task>') so re-issuing the same
ask finds the existing PRD instead of regenerating (find-or-create), and
version history groups per task. Anchors to the company's current brief when
one exists, else to the per-company uploads brief (new accounts).
"""
from __future__ import annotations

from app.db.client import require_client
from app.routes.prd import _chat_task_theme_id, _chat_task_title


def _seed_corpus(data_dir, dataset, body="some corpus body"):
    ds = data_dir / dataset
    ds.mkdir(exist_ok=True)
    (ds / "a.md").write_text(body)


def _save_current_brief(db_mod, dataset):
    payload = {
        "summary_headline": "stub",
        "insights": [{"title": "Brief insight 0", "theme_id": "brief-theme"}],
        "_schema_version": 1,
    }
    return db_mod.save_brief(
        dataset=dataset, week_label="Week of stub", payload=payload, schema_version=1
    )


def _prd_row(prd_id):
    return require_client().table("prds").select("*").eq("id", prd_id).execute().data[0]


# ── title / theme-id derivation (pure helpers) ───────────────────────────────

def test_chat_task_title_cleans_and_capitalizes():
    assert _chat_task_title("dark mode  on mobile.") == "Dark mode on mobile"
    assert _chat_task_title("  improve onboarding!?  ") == "Improve onboarding"


def test_chat_task_title_truncates_on_word_boundary():
    long = "a really long task description " * 10
    title = _chat_task_title(long)
    assert len(title) <= 91  # 90 + ellipsis
    assert title.endswith("…")
    assert " " not in title[-2:]  # no dangling space before the ellipsis


def test_chat_task_theme_id_normalizes_case_and_whitespace():
    a = _chat_task_theme_id("Dark   Mode on MOBILE")
    b = _chat_task_theme_id("dark mode on mobile")
    assert a == b
    assert a.startswith("chat:")
    assert _chat_task_theme_id("something else") != a


# ── POST /v1/prd/generate-from-task ──────────────────────────────────────────

def test_generate_from_task_happy_path(tenant_client, isolated_settings):
    t = tenant_client.make(slug="acme")
    _seed_corpus(isolated_settings["data_dir"], dataset="acme")
    _save_current_brief(isolated_settings["db"], dataset="acme")

    resp = t.client.post(
        "/v1/prd/generate-from-task", json={"task": "dark mode on mobile"}
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] in ("generating", "ready")
    assert body["title"] == "Dark mode on mobile"
    assert body["variant"] == "v3"

    # The row is discriminated as a chat-task PRD, keyed on the task hash.
    row = _prd_row(body["prd_id"])
    assert row["source"] == "chat"
    assert row["theme_id"] == _chat_task_theme_id("dark mode on mobile")


def test_generate_from_task_dedups_same_task(tenant_client, isolated_settings):
    t = tenant_client.make(slug="acme")
    db_mod = isolated_settings["db"]
    brief_id = _save_current_brief(db_mod, dataset="acme")

    existing = db_mod.start_prd(
        brief_id=brief_id, insight_index=0, title="Dark mode on mobile",
        template_version=1, variant="v3", source="chat",
        theme_id=_chat_task_theme_id("Dark Mode on Mobile"),
    )
    db_mod.complete_prd(existing, title="Dark mode on mobile", md="# Already here")

    # Same task, different casing/spacing → the existing PRD is returned.
    resp = t.client.post(
        "/v1/prd/generate-from-task", json={"task": "dark   mode on MOBILE"}
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["prd_id"] == existing
    assert body["status"] == "ready"


def test_generate_from_task_force_makes_new_row(tenant_client, isolated_settings):
    t = tenant_client.make(slug="acme")
    db_mod = isolated_settings["db"]
    brief_id = _save_current_brief(db_mod, dataset="acme")

    existing = db_mod.start_prd(
        brief_id=brief_id, insight_index=0, title="Dark mode on mobile",
        template_version=1, variant="v3", source="chat",
        theme_id=_chat_task_theme_id("dark mode on mobile"),
    )
    db_mod.complete_prd(existing, title="Dark mode on mobile", md="# Already here")

    resp = t.client.post(
        "/v1/prd/generate-from-task",
        json={"task": "dark mode on mobile", "force": True},
    )
    assert resp.status_code == 200
    assert resp.json()["prd_id"] != existing


def test_generate_from_task_without_brief_anchors_to_uploads_brief(
    tenant_client, isolated_settings
):
    """A company with NO current brief still gets a PRD — anchored to the
    per-company uploads brief, same as the PRD-import path."""
    t = tenant_client.make(slug="acme")
    _seed_corpus(isolated_settings["data_dir"], dataset="acme")

    resp = t.client.post(
        "/v1/prd/generate-from-task", json={"task": "improve onboarding"}
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] in ("generating", "ready")
    row = _prd_row(body["prd_id"])
    assert row["source"] == "chat"


def test_generate_from_task_rejects_short_task(tenant_client, isolated_settings):
    t = tenant_client.make(slug="acme")
    _save_current_brief(isolated_settings["db"], dataset="acme")
    resp = t.client.post("/v1/prd/generate-from-task", json={"task": "ab"})
    assert resp.status_code == 422


def test_generate_from_task_without_auth_returns_401(unauth_client, isolated_settings):
    resp = unauth_client.post(
        "/v1/prd/generate-from-task", json={"task": "dark mode"}
    )
    assert resp.status_code == 401


def test_generate_from_task_feeds_task_as_insight_override(
    tenant_client, isolated_settings, monkeypatch
):
    """The route hands the runner a synthetic insight built from the task text
    (title + 'Requested by the user in chat' summary) — the user's own words
    ground the generation, not a brief insight."""
    from app.routes import prd as prd_routes

    t = tenant_client.make(slug="acme")
    _save_current_brief(isolated_settings["db"], dataset="acme")

    captured: dict = {}

    # Capture SYNCHRONOUSLY at call time (the returned no-op coroutine is what
    # asyncio.create_task schedules) — the background task itself may not get a
    # loop slice before the response assertion runs.
    def _capture(prd_id, brief_id, insight_index, insight_override=None, **kw):
        captured.update(
            prd_id=prd_id, brief_id=brief_id, insight_index=insight_index,
            insight_override=insight_override,
        )

        async def _noop():
            return None

        return _noop()

    monkeypatch.setattr(prd_routes, "generate_prd_and_warm", _capture)

    resp = t.client.post(
        "/v1/prd/generate-from-task", json={"task": "dark mode on mobile"}
    )
    assert resp.status_code == 200

    override = captured["insight_override"]
    assert override["title"] == "Dark mode on mobile"
    assert "dark mode on mobile" in override["summary"]
    # No synthetic theme_id inside the insight — it has no KG backing, so
    # grounding must take its corpus fallback instead of resolving 'chat:…'.
    assert "theme_id" not in override
