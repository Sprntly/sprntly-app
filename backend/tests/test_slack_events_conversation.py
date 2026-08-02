"""Tests for the inbound Slack conversation loop on POST /slack/events.

Covers the message.im / app_mention branch added for two-way chat:
  - a user DM is resolved to the company, run through qa_agent.answer, and the
    answer is posted back to the same channel
  - an app_mention is answered in-thread (thread_ts set) with the <@BOT> token
    stripped from the question
  - the guards hold: Slack retries are ignored, bot/self messages never get a
    reply (no reply-loop), and an empty body is dropped

The Events API endpoint is unauthenticated — the signing-secret request
signature is the auth — so each request is signed exactly as Slack would.
"""
from __future__ import annotations

import hashlib
import hmac
import importlib
import json
import sys
import time
from unittest.mock import patch

import pytest
from cryptography.fernet import Fernet

from tests._company_helpers import company_client

TEAM_ID = "T_TEST123"
BOT_USER_ID = "UBOT"
INSTALLER_SLACK_USER = "UALICE"


def _reload_app_modules():
    for name in (
        "app.config",
        "app.connectors.tokens",
        "app.connectors.slack_oauth",
        "app.routes.connectors",
        "app.main",
    ):
        if name in sys.modules:
            importlib.reload(sys.modules[name])


@pytest.fixture
def slack_env(isolated_settings, monkeypatch):
    monkeypatch.setenv("TOKEN_ENCRYPTION_KEY", Fernet.generate_key().decode())
    monkeypatch.setenv("SLACK_CLIENT_ID", "test-slack-client-id")
    monkeypatch.setenv("SLACK_CLIENT_SECRET", "test-slack-client-secret")
    monkeypatch.setenv(
        "SLACK_OAUTH_REDIRECT_URI",
        "http://testserver/v1/connectors/slack/callback",
    )
    monkeypatch.setenv("FRONTEND_URL", "http://localhost:3000")
    monkeypatch.setenv("SLACK_SIGNING_SECRET", "test-signing-secret")
    _reload_app_modules()
    yield


def _seed_slack_connection(company_id: str, user_id: str) -> None:
    """Insert a Slack connection for the team with a token blob carrying the
    bot token + bot_user_id + authed_user_id, and config.team.id so the
    team→connection lookup resolves it."""
    from app import db
    from app.connectors.tokens import encrypt_token_json

    blob = {
        "access_token": "xoxb-test-bot-token",
        "token_type": "bot",
        "bot_user_id": BOT_USER_ID,
        "authed_user_id": INSTALLER_SLACK_USER,
        "team_id": TEAM_ID,
        "team_name": "Acme",
    }
    db.upsert_slack_connection(
        company_id=company_id,
        user_id=user_id,
        token_encrypted=encrypt_token_json(json.dumps(blob)),
        scopes="",
        account_label="alice@co.com",
        config_json=json.dumps({"team": {"id": TEAM_ID}}),
    )


def _sign(body: bytes) -> dict[str, str]:
    """Build the Slack signature headers for `body` using the test secret."""
    ts = str(int(time.time()))
    base = b"v0:" + ts.encode() + b":" + body
    digest = hmac.new(b"test-signing-secret", base, hashlib.sha256).hexdigest()
    return {
        "X-Slack-Request-Timestamp": ts,
        "X-Slack-Signature": f"v0={digest}",
        "Content-Type": "application/json",
    }


def _post_event(client, event: dict, extra_headers: dict | None = None):
    body = json.dumps(
        {"type": "event_callback", "team_id": TEAM_ID, "event": event}
    ).encode()
    headers = _sign(body)
    if extra_headers:
        headers.update(extra_headers)
    return client.post("/v1/connectors/slack/events", content=body, headers=headers)


@pytest.fixture
def connected(slack_env, monkeypatch):
    ctx = company_client(monkeypatch)
    _seed_slack_connection(ctx.company_id, ctx.user_id)
    return ctx


# ─────────────────────────── happy paths ───────────────────────────


def test_dm_message_runs_agent_and_replies(connected):
    """A direct message → qa_agent.answer → answer posted back to the DM."""
    with patch(
        "app.qa_agent.answer",
        return_value={"answer": "Onboarding is the top theme.", "citations": []},
    ) as mock_answer, patch(
        "app.connectors.slack_oauth.post_message"
    ) as mock_post, patch(
        "app.connectors.slack_oauth.fetch_conversation_history",
        return_value={"messages": [], "has_more": False, "next_cursor": ""},
    ):
        r = _post_event(
            connected.client,
            {
                "type": "message",
                "channel": "D123",
                "user": INSTALLER_SLACK_USER,
                "text": "what's the top theme?",
                "ts": "1700000000.000100",
            },
        )

    assert r.status_code == 200
    # Agent was called with the cleaned question + this company's dataset.
    assert mock_answer.call_count == 1
    kwargs = mock_answer.call_args.kwargs
    assert kwargs["question"] == "what's the top theme?"
    assert kwargs["enterprise_id"] == connected.company_id
    assert kwargs["dataset"]  # slug resolved
    # Answer posted back to the same DM channel, flat (no thread_ts).
    assert mock_post.call_count == 1
    pkw = mock_post.call_args.kwargs
    assert pkw["channel"] == "D123"
    assert pkw["text"] == "Onboarding is the top theme."
    assert pkw.get("thread_ts") is None


def test_app_mention_strips_token_and_replies_in_thread(connected):
    """An @mention in a channel → token stripped, reply threaded under it."""
    with patch(
        "app.qa_agent.answer",
        return_value={"answer": "Sure — here's the brief.", "citations": []},
    ) as mock_answer, patch(
        "app.connectors.slack_oauth.post_message"
    ) as mock_post:
        r = _post_event(
            connected.client,
            {
                "type": "app_mention",
                "channel": "C999",
                "user": INSTALLER_SLACK_USER,
                "text": "<@UBOT> summarize the brief",
                "ts": "1700000000.000200",
            },
        )

    assert r.status_code == 200
    assert mock_answer.call_args.kwargs["question"] == "summarize the brief"
    pkw = mock_post.call_args.kwargs
    assert pkw["channel"] == "C999"
    assert pkw["thread_ts"] == "1700000000.000200"


def test_dm_history_is_passed_to_agent(connected):
    """Prior DM turns are read and handed to the agent as multi-turn history,
    with the latest message (the current question) dropped and the bot's own
    posts mapped to the assistant role."""
    history_msgs = {
        # newest-first, as Slack returns it
        "messages": [
            {"user": INSTALLER_SLACK_USER, "text": "and pricing?"},  # current → dropped
            {"user": BOT_USER_ID, "bot_id": "B1", "text": "Onboarding leads."},
            {"user": INSTALLER_SLACK_USER, "text": "what's the top theme?"},
        ],
        "has_more": False,
        "next_cursor": "",
    }
    with patch(
        "app.qa_agent.answer",
        return_value={"answer": "Pricing is #2.", "citations": []},
    ) as mock_answer, patch(
        "app.connectors.slack_oauth.post_message"
    ), patch(
        "app.connectors.slack_oauth.fetch_conversation_history",
        return_value=history_msgs,
    ):
        _post_event(
            connected.client,
            {
                "type": "message",
                "channel": "D123",
                "user": INSTALLER_SLACK_USER,
                "text": "and pricing?",
                "ts": "1700000000.000300",
            },
        )

    hist = mock_answer.call_args.kwargs["history"]
    assert hist == [
        {"role": "user", "content": "what's the top theme?"},
        {"role": "assistant", "content": "Onboarding leads."},
    ]


# ─────────────────────────── guards ───────────────────────────


def test_retry_is_ignored(connected):
    """A Slack retry delivery must not re-run the agent (no double-answer)."""
    with patch("app.qa_agent.answer") as mock_answer, patch(
        "app.connectors.slack_oauth.post_message"
    ) as mock_post:
        r = _post_event(
            connected.client,
            {
                "type": "message",
                "channel": "D123",
                "user": INSTALLER_SLACK_USER,
                "text": "hi",
                "ts": "1700000000.000400",
            },
            extra_headers={"X-Slack-Retry-Num": "1"},
        )
    assert r.status_code == 200
    mock_answer.assert_not_called()
    mock_post.assert_not_called()


def test_bot_message_does_not_loop(connected):
    """A message carrying bot_id (e.g. our own reply) is never answered."""
    with patch("app.qa_agent.answer") as mock_answer, patch(
        "app.connectors.slack_oauth.post_message"
    ) as mock_post:
        r = _post_event(
            connected.client,
            {
                "type": "message",
                "channel": "D123",
                "user": BOT_USER_ID,
                "bot_id": "B1",
                "text": "Onboarding is the top theme.",
                "ts": "1700000000.000500",
            },
        )
    assert r.status_code == 200
    mock_answer.assert_not_called()
    mock_post.assert_not_called()


def test_empty_text_is_dropped(connected):
    """A message with no text (e.g. a file share) produces no agent call."""
    with patch("app.qa_agent.answer") as mock_answer, patch(
        "app.connectors.slack_oauth.post_message"
    ) as mock_post:
        r = _post_event(
            connected.client,
            {
                "type": "message",
                "channel": "D123",
                "user": INSTALLER_SLACK_USER,
                "text": "   ",
                "ts": "1700000000.000600",
            },
        )
    assert r.status_code == 200
    mock_answer.assert_not_called()
    mock_post.assert_not_called()


def test_bad_signature_is_rejected(connected):
    """An unsigned/forged request is rejected before any processing."""
    body = json.dumps(
        {"type": "event_callback", "team_id": TEAM_ID, "event": {"type": "message"}}
    ).encode()
    r = connected.client.post(
        "/v1/connectors/slack/events",
        content=body,
        headers={
            "X-Slack-Request-Timestamp": str(int(time.time())),
            "X-Slack-Signature": "v0=deadbeef",
            "Content-Type": "application/json",
        },
    )
    assert r.status_code == 401


# ──────────────── report asks: ack up front, file on the way out ────────────────
#
# A CIR ask from Slack used to be the worst experience we shipped: a multi-minute
# silent run, then a raw HTML document posted as message text (a wall of CSS,
# truncated by Slack). Both halves are fixed here.

REPORT_HTML = (
    '<!DOCTYPE html><html><head><style>body{color:red}</style></head><body>'
    "<h1>Where Acme stands</h1>"
    '<div class="opener">Two rivals are attacking the same seam, independently, '
    "and it is ours.</div>"
    '<script type="application/json" id="report-metadata">'
    '{"window": "Jan – Jul 2026"}</script></body></html>'
)


def _seed_with_scopes(ctx, scopes: str) -> None:
    """Re-seed this company's Slack connection with a given granted-scope set."""
    _seed_slack_connection(ctx.company_id, ctx.user_id)
    from app import db

    row = db.get_slack_connection(ctx.company_id, ctx.user_id)
    assert row is not None
    from app.connectors.tokens import encrypt_token_json

    db.upsert_slack_connection(
        company_id=ctx.company_id,
        user_id=ctx.user_id,
        token_encrypted=encrypt_token_json(json.dumps({
            "access_token": "xoxb-test-bot-token",
            "bot_user_id": BOT_USER_ID,
            "authed_user_id": INSTALLER_SLACK_USER,
            "team_id": TEAM_ID,
        })),
        scopes=scopes,
        account_label="alice@co.com",
        config_json=json.dumps({"team": {"id": TEAM_ID}}),
    )


def test_report_ask_acks_before_running_then_delivers_summary_and_file(connected):
    _seed_with_scopes(connected, "chat:write files:write")
    order = []

    def _answer(**kw):
        order.append("run")
        return {"answer": REPORT_HTML, "citations": [],
                "_skill": "competitive-intelligence-review"}

    def _post(_token, **kw):
        order.append(("post", kw.get("text", "")[:40]))
        return {}

    with patch("app.qa_agent.answer", side_effect=_answer), patch(
        "app.connectors.slack_oauth.post_message", side_effect=_post
    ), patch(
        "app.connectors.slack_oauth.upload_file", return_value=True
    ) as mock_up:
        r = _post_event(
            connected.client,
            {
                "type": "app_mention",
                "channel": "C999",
                "user": INSTALLER_SLACK_USER,
                "text": "<@UBOT> run a competitive intelligence report",
                "ts": "1700000000.000700",
            },
        )

    assert r.status_code == 200
    # The ack lands BEFORE the run, not after it.
    assert order[0][0] == "post" and "On it" in order[0][1]
    assert order[1] == "run"
    # The document is delivered as a file, threaded under the mention.
    assert mock_up.call_count == 1
    assert mock_up.call_args.kwargs["thread_ts"] == "1700000000.000700"
    assert mock_up.call_args.kwargs["content"] == REPORT_HTML


def test_html_report_is_never_posted_as_message_text(connected):
    _seed_with_scopes(connected, "chat:write files:write")
    with patch(
        "app.qa_agent.answer",
        return_value={"answer": REPORT_HTML, "citations": [],
                      "_skill": "competitive-intelligence-review"},
    ), patch("app.connectors.slack_oauth.post_message") as mock_post, patch(
        "app.connectors.slack_oauth.upload_file", return_value=True
    ):
        _post_event(
            connected.client,
            {
                "type": "app_mention",
                "channel": "C999",
                "user": INSTALLER_SLACK_USER,
                "text": "<@UBOT> where do we stand vs competitors?",
                "ts": "1700000000.000800",
            },
        )
    texts = " ".join(c.kwargs.get("text", "") for c in mock_post.call_args_list)
    assert "<!DOCTYPE html>" not in texts
    assert "body{color:red}" not in texts
    # ...replaced by the report's own opening.
    assert "Where Acme stands" in texts
    assert "Two rivals are attacking the same seam" in texts


def test_report_delivery_without_files_write_points_at_sprntly_chat(connected):
    _seed_with_scopes(connected, "chat:write")
    with patch(
        "app.qa_agent.answer",
        return_value={"answer": REPORT_HTML, "citations": [],
                      "_skill": "competitive-intelligence-review"},
    ), patch("app.connectors.slack_oauth.post_message") as mock_post, patch(
        "app.connectors.slack_oauth.upload_file"
    ) as mock_up:
        _post_event(
            connected.client,
            {
                "type": "app_mention",
                "channel": "C999",
                "user": INSTALLER_SLACK_USER,
                "text": "<@UBOT> monthly competitor scan",
                "ts": "1700000000.000900",
            },
        )
    mock_up.assert_not_called()
    texts = " ".join(c.kwargs.get("text", "") for c in mock_post.call_args_list)
    assert "open Sprntly chat" in texts
    assert "Where Acme stands" in texts


def test_ordinary_question_gets_no_report_ack_and_posts_prose_as_before(connected):
    with patch(
        "app.qa_agent.answer",
        return_value={"answer": "Onboarding is the top theme.", "citations": []},
    ), patch("app.connectors.slack_oauth.post_message") as mock_post, patch(
        "app.connectors.slack_oauth.upload_file"
    ) as mock_up, patch(
        "app.connectors.slack_oauth.fetch_conversation_history",
        return_value={"messages": [], "has_more": False, "next_cursor": ""},
    ):
        _post_event(
            connected.client,
            {
                "type": "message",
                "channel": "D123",
                "user": INSTALLER_SLACK_USER,
                "text": "what's the top theme?",
                "ts": "1700000001.000100",
            },
        )
    mock_up.assert_not_called()
    assert mock_post.call_count == 1
    assert mock_post.call_args.kwargs["text"] == "Onboarding is the top theme."


def test_incidental_competitor_mention_gets_no_report_ack(connected):
    """The narrowed CIR intent is what keeps Slack from acking a five-minute run
    for a sentence that merely says the word "competitor"."""
    with patch(
        "app.qa_agent.answer",
        return_value={"answer": "Noted.", "citations": []},
    ), patch("app.connectors.slack_oauth.post_message") as mock_post, patch(
        "app.connectors.slack_oauth.fetch_conversation_history",
        return_value={"messages": [], "has_more": False, "next_cursor": ""},
    ):
        _post_event(
            connected.client,
            {
                "type": "message",
                "channel": "D123",
                "user": INSTALLER_SLACK_USER,
                "text": "we lost that deal to a competitor last week",
                "ts": "1700000001.000200",
            },
        )
    assert mock_post.call_count == 1
    assert "On it" not in mock_post.call_args.kwargs["text"]


def test_retry_of_a_report_ask_neither_acks_nor_runs(connected):
    """Slack retries are the one thing that could start a SECOND multi-minute
    run — the retry guard is checked before the ack."""
    with patch("app.qa_agent.answer") as mock_answer, patch(
        "app.connectors.slack_oauth.post_message"
    ) as mock_post, patch("app.connectors.slack_oauth.upload_file") as mock_up:
        r = _post_event(
            connected.client,
            {
                "type": "app_mention",
                "channel": "C999",
                "user": INSTALLER_SLACK_USER,
                "text": "<@UBOT> competitive intelligence report",
                "ts": "1700000001.000300",
            },
            extra_headers={"X-Slack-Retry-Num": "1"},
        )
    assert r.status_code == 200
    mock_answer.assert_not_called()
    mock_post.assert_not_called()
    mock_up.assert_not_called()


def test_report_run_clears_its_in_flight_marker(connected):
    """A completed run leaves nothing for the startup sweep to report."""
    from app.routes import connectors as conn

    with patch(
        "app.qa_agent.answer",
        return_value={"answer": REPORT_HTML, "citations": [],
                      "_skill": "competitive-intelligence-review"},
    ), patch("app.connectors.slack_oauth.post_message"), patch(
        "app.connectors.slack_oauth.upload_file", return_value=True
    ):
        _post_event(
            connected.client,
            {
                "type": "app_mention",
                "channel": "C999",
                "user": INSTALLER_SLACK_USER,
                "text": "<@UBOT> competitive intelligence report",
                "ts": "1700000001.000400",
            },
        )
    assert conn._slack_report_markers == {}
