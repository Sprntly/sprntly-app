"""Sharing an artifact into Slack from the chat.

Two halves, tested separately because they fail differently:

  * `app.slack_share` — pure matching and composition. Fast, no fixtures, and
    it is where the rules that decide WHAT gets posted WHERE actually live.
  * `POST /v1/share/slack/{preview,send}` — the tenancy gate, the two-step
    contract (preview never posts), and the send's refusal to take a message
    body from the caller.

The invariant this file exists to protect, above all others: NOTHING REACHES
SLACK WITHOUT A SECOND, EXPLICIT CALL. A preview that posted — or a send that
posted something other than what the preview showed — is the failure mode the
whole feature is shaped around, so both are asserted directly.
"""
from __future__ import annotations

import importlib
import json
import sys
import uuid
from unittest.mock import patch

import pytest
from cryptography.fernet import Fernet
from fastapi.testclient import TestClient

from app import slack_share
from tests._company_helpers import (
    seed_company,
    seed_connection,
    setup_supabase_auth,
    supabase_bearer,
)

BASE = "https://app.sprntly.test"


# ─────────────────────────── pure: kinds + links ───────────────────────────


@pytest.mark.parametrize(
    "named,expected",
    [
        ("prd", "prd"),
        ("PRD", "prd"),
        ("tickets", "ticket_set"),
        ("ticket_set", "ticket_set"),
        ("report", "report"),
        ("doc", "custom_artifact"),
        (None, None),
    ],
)
def test_canonical_type_maps_user_words_to_library_kinds(named, expected):
    assert slack_share.canonical_type(named) == expected


def test_unshareable_kinds_are_not_coerced_into_something_else():
    """A prototype is not a PRD. The whole point of reporting the kind back is
    that substituting a document nobody asked for is worse than saying no."""
    assert slack_share.canonical_type("prototype") is None
    assert slack_share.canonical_type("evidence") is None


def test_prd_link_reuses_the_apps_existing_deep_link():
    # The same `/brief?prd=` shape the "your PRD is ready" ping has always
    # sent, so a share and a notification land a reader in the same place.
    assert slack_share.share_link(BASE, artifact_type="prd", artifact_id=42) == (
        f"{BASE}/brief?prd=42"
    )


def test_prd_link_prefers_the_open_ids_prd_id():
    """A library row's own id is not always the PRD id — the `open` block is
    what the app's PRD routes take."""
    link = slack_share.share_link(
        BASE, artifact_type="prd", artifact_id=999, open_ids={"prd_id": 7}
    )
    assert link == f"{BASE}/brief?prd=7"


@pytest.mark.parametrize("kind", ["report", "ticket_set", "custom_artifact"])
def test_other_kinds_link_to_the_library_focused_on_the_row(kind):
    # `${type}-${id}` is exactly ArtifactsScreen's own activeArtifactKey shape,
    # so the link opens the row through the same per-kind logic a click runs.
    assert slack_share.share_link(BASE, artifact_type=kind, artifact_id=5) == (
        f"{BASE}/artifacts?focus={kind}-5"
    )


# ─────────────────────────── pure: which document ───────────────────────────


def _row(kind: str, rid: int, title: str, created: str = "2026-08-01") -> dict:
    return {
        "type": kind,
        "id": rid,
        "title": title,
        "status": "ready",
        "created_at": created,
        "open": {"prd_id": rid} if kind == "prd" else {},
    }


def test_resolves_one_clear_title_match():
    items = [_row("prd", 1, "Checkout Abandonment"), _row("prd", 2, "Billing Export")]
    out = slack_share.resolve_share_target(
        items, artifact_type="prd", artifact_query="checkout abandonment", base_url=BASE
    )
    assert out["status"] == "resolved"
    assert out["artifact"]["id"] == 1
    assert out["artifact"]["url"] == f"{BASE}/brief?prd=1"


def test_equally_good_matches_ask_rather_than_pick():
    items = [
        _row("prd", 1, "Export Scheduling", "2026-08-02"),
        _row("prd", 2, "Export Scheduling", "2026-08-01"),
    ]
    out = slack_share.resolve_share_target(
        items, artifact_type="prd", artifact_query="export scheduling", base_url=BASE
    )
    assert out["status"] == "ambiguous"
    assert {c["id"] for c in out["candidates"]} == {1, 2}


def test_an_unnamed_kind_searches_every_shareable_kind():
    """"Share the onboarding one" names no kind — a report must be able to win
    on its title rather than losing to PRDs by being checked second."""
    items = [
        _row("prd", 1, "Billing Export"),
        _row("report", 9, "Onboarding Friction"),
    ]
    out = slack_share.resolve_share_target(
        items, artifact_type=None, artifact_query="onboarding friction", base_url=BASE
    )
    assert out["status"] == "resolved"
    assert out["artifact"]["type"] == "report"
    assert out["artifact"]["id"] == 9


def test_a_kind_that_cannot_be_shared_is_reported_as_itself():
    out = slack_share.resolve_share_target(
        [], artifact_type="prototype", artifact_query="dark mode", base_url=BASE
    )
    assert out["status"] == "unsupported_type"
    assert out["named_type"] == "prototype"


def test_no_query_is_not_an_error_it_is_the_callers_context_to_supply():
    # "share this PRD" — the ROUTE resolves it from the tab. Reaching here with
    # nothing is simply not_found, which the chat turns into "which one?".
    out = slack_share.resolve_share_target(
        [_row("prd", 1, "Anything")], artifact_type="prd", artifact_query=None,
        base_url=BASE,
    )
    assert out["status"] == "not_found"


def test_evidence_is_never_offered_even_when_its_title_matches_perfectly():
    items = [_row("evidence", 3, "Checkout Abandonment")]
    out = slack_share.resolve_share_target(
        items, artifact_type=None, artifact_query="checkout abandonment", base_url=BASE
    )
    assert out["status"] == "not_found"


# ─────────────────────────── pure: which channel ────────────────────────────


CHANNELS = [
    {"id": "C1", "name": "product", "is_private": False, "is_member": True},
    {"id": "C2", "name": "product-leads", "is_private": False, "is_member": False},
    {"id": "C3", "name": "founders", "is_private": True, "is_member": False},
]


def test_an_exact_channel_name_resolves():
    out = slack_share.match_channel(CHANNELS, "product")
    assert out["status"] == "resolved"
    assert out["channel"]["id"] == "C1"


def test_the_leading_hash_is_optional():
    assert slack_share.match_channel(CHANNELS, "#product")["channel"]["id"] == "C1"


def test_a_partial_channel_match_is_offered_never_taken():
    """The audience is the thing being decided. "#product" vs "#product-leads"
    is one substring apart, so a near miss asks instead of picking."""
    out = slack_share.match_channel(CHANNELS, "prod")
    assert out["status"] == "ambiguous"
    assert {c["id"] for c in out["candidates"]} == {"C1", "C2"}


def test_an_unknown_channel_hands_back_the_whole_list_to_pick_from():
    out = slack_share.match_channel(CHANNELS, "nope")
    assert out["status"] == "not_found"
    assert len(out["candidates"]) == 3


def test_no_channel_named_asks_which_one():
    out = slack_share.match_channel(CHANNELS, None)
    assert out["status"] == "needs_channel"
    assert out["channel"] is None


def test_a_public_channel_the_bot_is_missing_from_warns_but_is_allowed():
    warning = slack_share.channel_warning(
        {"name": "product-leads", "is_private": False, "is_member": False}
    )
    assert warning and "join" in warning
    assert not slack_share.channel_is_blocked(
        {"name": "product-leads", "is_private": False, "is_member": False}
    )


def test_a_private_channel_the_bot_is_missing_from_is_blocked():
    ch = {"name": "founders", "is_private": True, "is_member": False}
    assert slack_share.channel_is_blocked(ch)
    assert "invite" in slack_share.channel_warning(ch).lower()


def test_a_channel_the_bot_is_already_in_says_nothing():
    assert slack_share.channel_warning(
        {"name": "product", "is_private": False, "is_member": True}
    ) is None


# ─────────────────────────── pure: the message ──────────────────────────────


def test_summary_strips_markdown_furniture_and_keeps_the_prose():
    body = "# Overview\n\n:::block\nSome **real** prose about [checkout](http://x).\n"
    out = slack_share.summarize(body)
    assert "Overview" in out
    assert "**" not in out and ":::" not in out and "http://x" not in out
    assert "checkout" in out


def test_summary_truncates_on_a_word_boundary():
    out = slack_share.summarize("alpha bravo charlie delta echo foxtrot", limit=20)
    assert out.endswith("…")
    assert "chali" not in out  # never a mid-word cut


def test_summary_of_nothing_is_empty_not_an_ellipsis():
    assert slack_share.summarize("") == ""
    assert slack_share.summarize(None) == ""


# ── the stylesheet that shipped to a customer's Slack ───────────────────────
#
# A real PRD is stored as a full HTML document with a `<style>` block. Removing
# TAGS left the stylesheet behind as the document's first "prose", so the
# teaser posted in front of a whole team read:
#
#   Build the Public External-Brief Route @import url('https://fonts.google…
#   :root{--green:#1A6B47;--ink:#1F241F;--sub:#5B615B; …
#
# Reported from a live share, 2026-08-16.

_REPORTED_BODY = """<h1>Build the Public External-Brief Route</h1>
<style>
@import url('https://fonts.googleapis.com/css2?family=Spectral:wght@500;600');
:root{--green:#1A6B47;--ink:#1F241F;--sub:#5B615B;--page:#FFFFFF;}
</style>
<p>The route is specified in the brief but no page exists at that path yet.</p>"""


def test_a_style_block_never_reaches_the_teaser():
    out = slack_share.summarize(_REPORTED_BODY)
    assert "@import" not in out
    assert "#1A6B47" not in out
    assert ":root" not in out
    assert "googleapis" not in out


def test_the_real_prose_survives_the_stylesheet():
    """Not just "the CSS is gone" — the sentence a reader actually needs must
    still be there, or the fix would be a blank teaser."""
    out = slack_share.summarize(_REPORTED_BODY)
    assert "Build the Public External-Brief Route" in out
    assert "no page exists at that path yet" in out


@pytest.mark.parametrize("body,gone,kept", [
    ("<p>Real prose.</p><script>var x={a:1};</script>", "var x", "Real prose."),
    ("<head><title>T</title></head><p>Body prose.</p>", "<title", "Body prose."),
    ("<!-- internal note --><p>Visible prose.</p>", "internal note", "Visible prose."),
    # An UNCLOSED style block (a truncated document) must not leak its tail.
    ("<h1>Title</h1><style>:root{--a:#fff;}", "#fff", "Title"),
])
def test_non_prose_elements_are_removed_whole(body, gone, kept):
    out = slack_share.summarize(body)
    assert gone not in out
    assert kept in out


def test_ordinary_markdown_is_untouched_by_the_css_strip():
    """The CSS rules must not eat a normal document. A PRD that merely MENTIONS
    a colour or a brace is still prose."""
    out = slack_share.summarize(
        "# Overview\n\nWe will use the brand green (#1A6B47) for the primary "
        "button, per the design system."
    )
    assert "brand green" in out
    assert "primary button" in out


def _artifact() -> dict:
    return {
        "type": "prd",
        "id": 1,
        "title": "Checkout Abandonment",
        "kind_label": "PRD",
        "url": f"{BASE}/brief?prd=1",
    }


def test_compose_leads_with_the_users_own_words():
    text, blocks = slack_share.compose_share(
        note="Would love the team's feedback on this.",
        artifact=_artifact(), summary="A teaser.", sharer_name="Ada",
    )
    assert blocks[0]["text"]["text"] == "Would love the team's feedback on this."
    assert text.startswith("Would love the team's feedback")


def test_compose_links_the_document_by_title():
    _text, blocks = slack_share.compose_share(
        note=None, artifact=_artifact(), summary="", sharer_name=None,
    )
    assert f"<{BASE}/brief?prd=1|Checkout Abandonment>" in blocks[0]["text"]["text"]


def test_compose_without_a_note_starts_at_the_document():
    """A note-less share must not post an empty section — the client also reads
    block 0 to pre-fill its note box, and a document line there would be
    nonsense the user has to delete."""
    _text, blocks = slack_share.compose_share(
        note=None, artifact=_artifact(), summary="", sharer_name=None,
    )
    assert blocks[0]["text"]["text"].startswith("*PRD:*")


def test_compose_escapes_a_title_that_would_break_the_link_span():
    art = {**_artifact(), "title": "A <script> & B"}
    _text, blocks = slack_share.compose_share(
        note=None, artifact=art, summary="", sharer_name=None,
    )
    rendered = blocks[0]["text"]["text"]
    assert "&lt;script&gt;" in rendered and "&amp;" in rendered


def test_compose_names_the_sharer_when_known():
    _text, blocks = slack_share.compose_share(
        note=None, artifact=_artifact(), summary="", sharer_name="Ada",
    )
    assert any("Shared from Sprntly by Ada" in json.dumps(b) for b in blocks)


def test_the_plain_text_fallback_carries_the_link():
    """Slack requires it for notifications and accessibility — a reader who
    only ever sees the push notification still gets somewhere to click."""
    text, _blocks = slack_share.compose_share(
        note="Look at this", artifact=_artifact(), summary="", sharer_name=None,
    )
    assert f"{BASE}/brief?prd=1" in text


# ─────────────────────────── routes ─────────────────────────────────────────


def _reload_app_modules():
    for name in (
        "app.config",
        "app.connectors.tokens",
        "app.connectors.slack_oauth",
        "app.slack_share",
        "app.routes.slack_share",
        "app.main",
    ):
        if name in sys.modules:
            importlib.reload(sys.modules[name])


@pytest.fixture
def share_env(isolated_settings, monkeypatch):
    monkeypatch.setenv("TOKEN_ENCRYPTION_KEY", Fernet.generate_key().decode())
    monkeypatch.setenv("FRONTEND_URL", BASE)
    _reload_app_modules()
    yield


def _client_with_slack(monkeypatch, *, slug: str = "acme"):
    """A signed-in company that has Slack connected.

    The dataset slug MUST equal the company slug for the PRD ownership chain
    (prd → brief → dataset → company) to resolve to this caller — the same
    requirement `tenant_client` documents.
    """
    setup_supabase_auth(monkeypatch)
    import app.main as main_mod

    importlib.reload(sys.modules["app.main"])

    user_id = "test-user-" + uuid.uuid4().hex[:8]
    company_id = seed_company(user_id=user_id, slug=slug)
    seed_connection(
        company_id=company_id,
        provider="slack",
        token_blob={"access_token": "xoxb-test", "authed_user_id": "U1"},
        user_id=user_id,
    )
    client = TestClient(main_mod.app, headers=supabase_bearer(user_id))
    return client, company_id, user_id


_PRD_BODY = "Carts are abandoned at the payment step."


def _seed_prd(db_mod, dataset: str = "acme", title: str = "Checkout Abandonment") -> int:
    """A ready PRD on `dataset`, through the same writers the product uses."""
    brief_id = db_mod.save_brief(
        dataset=dataset, week_label="Week of stub",
        payload={"summary_headline": "s", "insights": [{"title": "I0"}],
                 "_schema_version": 1},
        schema_version=1,
    )
    prd_id = db_mod.start_prd(
        brief_id=brief_id, insight_index=0, title=title,
        template_version=1, variant="v3", source="chat",
        theme_id=f"share:{uuid.uuid4().hex[:8]}",
    )
    db_mod.complete_prd(prd_id, title=title, md=f"# {title}\n\n{_PRD_BODY}")
    return prd_id


def test_preview_posts_nothing(share_env, isolated_settings, monkeypatch):
    """THE contract. A preview that posted would make the confirmation step
    decorative, which is the entire reason this feature has two routes."""
    client, company_id, _u = _client_with_slack(monkeypatch)
    prd_id = _seed_prd(isolated_settings["db"])
    with patch("app.connectors.slack_oauth.list_channels", return_value=CHANNELS), \
         patch("app.connectors.slack_oauth.post_message") as post:
        r = client.post("/v1/share/slack/preview",
                        json={"prd_id": prd_id, "channel": "product"})
    assert r.status_code == 200, r.text
    assert r.json()["status"] == "ready"
    post.assert_not_called()


def test_preview_composes_the_message_it_will_send(share_env, isolated_settings, monkeypatch):
    client, company_id, _u = _client_with_slack(monkeypatch)
    prd_id = _seed_prd(isolated_settings["db"])
    with patch("app.connectors.slack_oauth.list_channels", return_value=CHANNELS):
        r = client.post("/v1/share/slack/preview", json={
            "prd_id": prd_id, "channel": "product",
            "note": "Feedback welcome.",
        })
    body = r.json()
    assert body["target"]["title"] == "Checkout Abandonment"
    assert body["target"]["url"] == f"{BASE}/brief?prd={prd_id}"
    assert body["channel"]["id"] == "C1"
    assert "Feedback welcome." in body["message"]["text"]
    # The teaser comes off the PRD's own body, not the title.
    assert "abandoned at the payment step" in body["message"]["summary"]


def test_preview_with_no_channel_asks_which_one(share_env, isolated_settings, monkeypatch):
    client, company_id, _u = _client_with_slack(monkeypatch)
    prd_id = _seed_prd(isolated_settings["db"])
    with patch("app.connectors.slack_oauth.list_channels", return_value=CHANNELS):
        r = client.post("/v1/share/slack/preview", json={"prd_id": prd_id})
    body = r.json()
    assert body["status"] == "needs_channel"
    assert len(body["channels"]) == 3
    # The message is composed anyway — picking a destination for something you
    # cannot see is not a choice.
    assert body["message"]["text"]


def test_preview_blocks_a_private_channel_sprntly_cannot_join(share_env, isolated_settings, monkeypatch):
    client, company_id, _u = _client_with_slack(monkeypatch)
    prd_id = _seed_prd(isolated_settings["db"])
    with patch("app.connectors.slack_oauth.list_channels", return_value=CHANNELS):
        r = client.post("/v1/share/slack/preview",
                        json={"prd_id": prd_id, "channel": "founders"})
    body = r.json()
    assert body["status"] == "blocked"
    assert "invite" in body["warning"].lower()


def test_preview_warns_before_a_self_join_rather_than_after(share_env, isolated_settings, monkeypatch):
    client, company_id, _u = _client_with_slack(monkeypatch)
    prd_id = _seed_prd(isolated_settings["db"])
    with patch("app.connectors.slack_oauth.list_channels", return_value=CHANNELS):
        r = client.post("/v1/share/slack/preview",
                        json={"prd_id": prd_id, "channel": "product-leads"})
    body = r.json()
    assert body["status"] == "ready"
    assert "join" in body["warning"]


def test_a_foreign_prd_is_a_404_not_a_403(share_env, isolated_settings, monkeypatch):
    """No cross-tenant existence disclosure — the same posture every other
    id-keyed surface takes."""
    client, _company_id, _u = _client_with_slack(monkeypatch)
    # A second company, and a PRD on ITS dataset — ownership runs
    # prd → brief → dataset slug → company, so a different SLUG is what makes
    # this genuinely another tenant's document.
    seed_company(user_id="someone-else", slug="other-co")
    foreign_prd = _seed_prd(isolated_settings["db"], dataset="other-co")
    with patch("app.connectors.slack_oauth.list_channels", return_value=CHANNELS):
        r = client.post("/v1/share/slack/preview", json={"prd_id": foreign_prd})
    assert r.status_code == 404


def test_send_posts_once_with_the_server_composed_body(share_env, isolated_settings, monkeypatch):
    client, company_id, _u = _client_with_slack(monkeypatch)
    prd_id = _seed_prd(isolated_settings["db"])
    with patch("app.connectors.slack_oauth.post_message",
               return_value={"ok": True, "ts": "1.2", "channel": "C1"}) as post:
        r = client.post("/v1/share/slack/send", json={
            "prd_id": prd_id, "channel_id": "C1", "note": "Feedback welcome.",
        })
    assert r.status_code == 200, r.text
    assert r.json()["ok"] is True
    post.assert_called_once()
    kwargs = post.call_args.kwargs
    assert kwargs["channel"] == "C1"
    assert "Feedback welcome." in kwargs["text"]
    # The document line is rebuilt server-side from the row, never taken from
    # the request.
    assert "Checkout Abandonment" in json.dumps(kwargs["blocks"])
    # A public channel the bot may not be in must still be recoverable.
    assert kwargs["auto_join"] is True


def test_send_ignores_a_caller_supplied_message_body(share_env, isolated_settings, monkeypatch):
    """The browser cannot hand our bot token arbitrary text to post. Extra keys
    on the request are simply not part of the contract, and the body is
    rebuilt from the database regardless."""
    client, company_id, _u = _client_with_slack(monkeypatch)
    prd_id = _seed_prd(isolated_settings["db"])
    with patch("app.connectors.slack_oauth.post_message",
               return_value={"ok": True, "ts": "1.2", "channel": "C1"}) as post:
        client.post("/v1/share/slack/send", json={
            "prd_id": prd_id, "channel_id": "C1",
            "text": "PAYROLL DATA", "blocks": [{"type": "section"}],
        })
    posted = json.dumps(post.call_args.kwargs)
    assert "PAYROLL DATA" not in posted
    assert "Checkout Abandonment" in posted


def test_send_refuses_when_the_document_cannot_be_found(share_env, monkeypatch):
    """A 404, never a silent no-op: the client asked to send a specific thing,
    and "couldn't find it" must never render in the thread as "sent"."""
    client, _company_id, _u = _client_with_slack(monkeypatch)
    with patch("app.connectors.slack_oauth.post_message") as post:
        r = client.post("/v1/share/slack/send", json={
            "artifact_type": "prd", "artifact_query": "nothing by this name",
            "channel_id": "C1",
        })
    assert r.status_code == 404
    post.assert_not_called()


def test_share_needs_a_slack_connection(share_env, isolated_settings, monkeypatch):
    setup_supabase_auth(monkeypatch)
    import app.main as main_mod

    importlib.reload(sys.modules["app.main"])
    user_id = "test-user-" + uuid.uuid4().hex[:8]
    company_id = seed_company(user_id=user_id)
    prd_id = _seed_prd(isolated_settings["db"])
    client = TestClient(main_mod.app, headers=supabase_bearer(user_id))
    r = client.post("/v1/share/slack/preview", json={"prd_id": prd_id})
    assert r.status_code == 404
    assert "not connected" in r.json()["detail"].lower()


def test_share_requires_authentication(share_env, monkeypatch):
    setup_supabase_auth(monkeypatch)
    import app.main as main_mod

    importlib.reload(sys.modules["app.main"])
    anon = TestClient(main_mod.app)
    r = anon.post("/v1/share/slack/preview", json={"prd_id": 1})
    assert r.status_code in (401, 403)
