"""POST /v1/connectors/slack/commands — the slash-command sink, plus the report
delivery helpers it shares with the Events API loop.

Slack gives a slash command THREE SECONDS before the user sees an operation
timeout, and a competitive scan takes minutes. So the contract under test is:
signature-verified, ephemeral ack returned with no model work in the request
path, and the run delivered afterwards via `response_url` + an in-channel post
with the HTML document attached as a file.

NOTE on activation: registering `/competitive-scan` is a Slack app-MANIFEST
change, made per app (prod and dev are separate apps) and deliberately not part
of this code. These tests therefore drive the endpoint directly and never assume
the command is registered.
"""
from __future__ import annotations

import hashlib
import hmac
import importlib
import json
import sys
import time
from unittest.mock import patch
from urllib.parse import urlencode

import pytest
from cryptography.fernet import Fernet

from tests._company_helpers import company_client

TEAM_ID = "T_CMD123"
BOT_USER_ID = "UBOT"
INSTALLER_SLACK_USER = "UALICE"
RESPONSE_URL = "https://hooks.slack.com/commands/T_CMD123/999/abc"

REPORT_HTML = (
    "<!DOCTYPE html>\n<html lang=\"en\"><head><style>body{color:red}</style>"
    "</head><body><div class=\"frame\"><div class=\"page\">"
    "<h1>Where Acme stands</h1>"
    "<div class=\"opener\"><b>The automation race is over and everyone "
    "finished.</b> Every platform we compete with now ships automated buying "
    "with AI creative attached.</div>"
    "<div class=\"opener\">Product discovery is moving upstream into AI "
    "assistants, and we own no surface in that journey at all today.</div>"
    "</div></div>"
    '<script type="application/json" id="report-metadata">'
    '{"window": "Jan \\u2013 26 Jul 2026", "mode": "review"}</script>'
    "</body></html>"
)


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


def _seed_slack_connection(company_id: str, user_id: str, scopes: str = "") -> None:
    from app import db
    from app.connectors.tokens import encrypt_token_json

    blob = {
        "access_token": "xoxb-test-bot-token",
        "token_type": "bot",
        "bot_user_id": BOT_USER_ID,
        "authed_user_id": INSTALLER_SLACK_USER,
        "team_id": TEAM_ID,
        "team_name": "Acme",
        "scope": scopes,
    }
    db.upsert_slack_connection(
        company_id=company_id,
        user_id=user_id,
        token_encrypted=encrypt_token_json(json.dumps(blob)),
        scopes=scopes,
        account_label="alice@co.com",
        config_json=json.dumps({"team": {"id": TEAM_ID}}),
    )


def _sign(body: bytes) -> dict[str, str]:
    ts = str(int(time.time()))
    base = b"v0:" + ts.encode() + b":" + body
    digest = hmac.new(b"test-signing-secret", base, hashlib.sha256).hexdigest()
    return {
        "X-Slack-Request-Timestamp": ts,
        "X-Slack-Signature": f"v0={digest}",
        "Content-Type": "application/x-www-form-urlencoded",
    }


def _command_body(**overrides) -> bytes:
    form = {
        "command": "/competitive-scan",
        "text": "",
        "team_id": TEAM_ID,
        "channel_id": "C777",
        "user_id": INSTALLER_SLACK_USER,
        "response_url": RESPONSE_URL,
        "trigger_id": "123.456.abc",
    }
    form.update(overrides)
    return urlencode(form).encode()


def _post_command(client, *, body: bytes | None = None, sign: bool = True):
    body = body if body is not None else _command_body()
    headers = _sign(body) if sign else {
        "X-Slack-Request-Timestamp": str(int(time.time())),
        "X-Slack-Signature": "v0=deadbeef",
        "Content-Type": "application/x-www-form-urlencoded",
    }
    return client.post("/v1/connectors/slack/commands", content=body, headers=headers)


@pytest.fixture
def connected(slack_env, monkeypatch):
    ctx = company_client(monkeypatch)
    _seed_slack_connection(ctx.company_id, ctx.user_id, scopes="chat:write files:write")
    return ctx


@pytest.fixture
def connected_no_upload(slack_env, monkeypatch):
    ctx = company_client(monkeypatch)
    _seed_slack_connection(ctx.company_id, ctx.user_id, scopes="chat:write")
    return ctx


# ─────────────────────────── the request path ───────────────────────────

def test_bad_signature_is_rejected(connected):
    with patch("app.routes.connectors._run_slack_report_command") as bg:
        r = _post_command(connected.client, sign=False)
    assert r.status_code == 401
    bg.assert_not_called()


def test_returns_an_ephemeral_ack_naming_the_duration(connected):
    with patch("app.routes.connectors._run_slack_report_command"):
        r = _post_command(connected.client)
    assert r.status_code == 200
    body = r.json()
    assert body["response_type"] == "ephemeral"
    # The ack is the whole point: Slack shows no typing indicator for a bot, so a
    # silent multi-minute run reads as a broken command.
    assert "5-10 minutes" in body["text"]
    assert "competitive scan" in body["text"]


def test_request_path_does_no_model_work(connected):
    """Signature verify + form parse only. The 3s Slack deadline is the reason,
    and the budget is deliberately generous (>=1s) so CI noise can't flake it."""
    with patch("app.routes.connectors._run_slack_report_command"):
        started = time.monotonic()
        r = _post_command(connected.client)
        elapsed = time.monotonic() - started
    assert r.status_code == 200
    assert elapsed < 1.0, f"command path took {elapsed:.2f}s"


def test_unreadable_form_still_returns_200(connected):
    """Slack shows the user an error for any non-2xx, so an unusable payload gets
    a 200 with a human message rather than a 4xx."""
    with patch("app.routes.connectors._run_slack_report_command") as bg:
        r = _post_command(connected.client, body=_command_body(team_id="", channel_id=""))
    assert r.status_code == 200
    assert "couldn't read that command" in r.json()["text"]
    bg.assert_not_called()


def test_any_command_name_is_accepted(connected):
    """The endpoint never assumes WHICH command is registered — activation is a
    manifest change owned outside this code."""
    calls = []
    with patch("app.routes.connectors._run_slack_report_command",
               side_effect=lambda **kw: calls.append(kw) or None):
        r = _post_command(connected.client, body=_command_body(command="/sprntly-scan"))
    assert r.status_code == 200
    assert calls and calls[0]["command"] == "/sprntly-scan"


def test_command_args_reach_the_background_run(connected):
    calls = []
    with patch("app.routes.connectors._run_slack_report_command",
               side_effect=lambda **kw: calls.append(kw) or None):
        _post_command(connected.client, body=_command_body(text="Acme, Globex"))
    assert calls[0]["text"] == "Acme, Globex"
    assert calls[0]["response_url"] == RESPONSE_URL


# ─────────────────────────── the background run ───────────────────────────

def _question_of(mock_answer) -> str:
    return mock_answer.call_args.kwargs["question"]


async def test_background_run_pins_cir_and_delivers_summary_plus_file(connected):
    from app.routes import connectors as conn

    with patch("app.qa_agent.answer",
               return_value={"answer": REPORT_HTML, "citations": [],
                             "_skill": "competitive-intelligence-review"}) as mock_answer, \
         patch("app.connectors.slack_oauth.post_message") as mock_post, \
         patch("app.connectors.slack_oauth.upload_file", return_value=True) as mock_up, \
         patch("app.routes.connectors.requests.post") as mock_http:
        await conn._run_slack_report_command(
            team_id=TEAM_ID, channel="C777", slack_user=INSTALLER_SLACK_USER,
            text="", command="/competitive-scan", response_url=RESPONSE_URL,
        )

    # Pinned to the CIR skill so the command can't be re-routed by phrasing.
    assert mock_answer.call_args.kwargs["pinned_skill"] == \
        "competitive-intelligence-review"
    assert mock_answer.call_args.kwargs["enterprise_id"] == connected.company_id

    # The HTML document is NEVER posted as message text.
    posted = " ".join(c.kwargs.get("text", "") for c in mock_post.call_args_list)
    assert "<!DOCTYPE html>" not in posted
    assert "body{color:red}" not in posted
    # ...instead: a summary read from the report's own opening, plus the window.
    assert "Where Acme stands" in posted
    assert "The automation race is over" in posted
    assert "Jan – 26 Jul 2026" in posted

    # ...and the document itself arrives as a file.
    assert mock_up.call_count == 1
    up = mock_up.call_args.kwargs
    assert up["channel"] == "C777"
    assert up["filename"] == "competitive-intelligence-report.html"
    assert up["content"] == REPORT_HTML
    assert up["title"] == "Competitive Intelligence report"

    # The command is closed out on its response_url.
    assert mock_http.call_args.kwargs["json"]["response_type"] == "ephemeral"
    assert "posted above" in mock_http.call_args.kwargs["json"]["text"]


async def test_command_args_override_the_stored_roster(connected):
    """Names in the command text ride the QUESTION, which is where the pipeline
    reads an ad-hoc set from — so they override the roster without writing to it."""
    from app.routes import connectors as conn

    with patch("app.qa_agent.answer",
               return_value={"answer": "prose", "citations": []}) as mock_answer, \
         patch("app.connectors.slack_oauth.post_message"), \
         patch("app.routes.connectors.requests.post"):
        await conn._run_slack_report_command(
            team_id=TEAM_ID, channel="C777", slack_user=INSTALLER_SLACK_USER,
            text="Acme, Globex", command="/competitive-scan",
            response_url=RESPONSE_URL,
        )
    from app.competitive_intel import named_competitors

    question = _question_of(mock_answer)
    assert named_competitors(question) == ["Acme", "Globex"]


def test_command_question_shapes():
    from app.routes import connectors as conn
    from app.competitive_intel import named_competitors
    from app.skill_router import is_competitive_report_request

    bare = conn._command_question("")
    assert is_competitive_report_request(bare)
    assert named_competitors(bare) == []
    named = conn._command_question("Acme and Globex")
    assert is_competitive_report_request(named)
    assert named_competitors(named) == ["Acme", "Globex"]


async def test_missing_files_write_scope_falls_back_to_a_pointer(connected_no_upload):
    from app.routes import connectors as conn

    with patch("app.qa_agent.answer",
               return_value={"answer": REPORT_HTML, "citations": [],
                             "_skill": "competitive-intelligence-review"}), \
         patch("app.connectors.slack_oauth.post_message") as mock_post, \
         patch("app.connectors.slack_oauth.upload_file") as mock_up, \
         patch("app.routes.connectors.requests.post"):
        await conn._run_slack_report_command(
            team_id=TEAM_ID, channel="C777", slack_user=INSTALLER_SLACK_USER,
            text="", command="/competitive-scan", response_url=RESPONSE_URL,
        )
    mock_up.assert_not_called()
    posted = " ".join(c.kwargs.get("text", "") for c in mock_post.call_args_list)
    assert "open Sprntly chat" in posted
    assert "Where Acme stands" in posted      # the summary still landed
    assert "<!DOCTYPE html>" not in posted


async def test_failed_upload_also_falls_back(connected):
    from app.routes import connectors as conn

    with patch("app.qa_agent.answer",
               return_value={"answer": REPORT_HTML, "citations": [],
                             "_skill": "competitive-intelligence-review"}), \
         patch("app.connectors.slack_oauth.post_message") as mock_post, \
         patch("app.connectors.slack_oauth.upload_file", return_value=False), \
         patch("app.routes.connectors.requests.post"):
        await conn._run_slack_report_command(
            team_id=TEAM_ID, channel="C777", slack_user=INSTALLER_SLACK_USER,
            text="", command="/competitive-scan", response_url=RESPONSE_URL,
        )
    posted = " ".join(c.kwargs.get("text", "") for c in mock_post.call_args_list)
    assert "open Sprntly chat" in posted


async def test_unconnected_workspace_is_told_on_the_response_url(slack_env, monkeypatch):
    from app.routes import connectors as conn

    company_client(monkeypatch)      # a company, but no Slack connection
    with patch("app.qa_agent.answer") as mock_answer, \
         patch("app.routes.connectors.requests.post") as mock_http:
        await conn._run_slack_report_command(
            team_id="T_NOPE", channel="C1", slack_user="U1", text="",
            command="/competitive-scan", response_url=RESPONSE_URL,
        )
    mock_answer.assert_not_called()
    assert "isn't connected" in mock_http.call_args.kwargs["json"]["text"]


async def test_run_failure_tells_the_user_and_never_raises(connected):
    from app.routes import connectors as conn

    with patch("app.qa_agent.answer", side_effect=RuntimeError("boom")), \
         patch("app.connectors.slack_oauth.post_message"), \
         patch("app.routes.connectors.requests.post") as mock_http:
        await conn._run_slack_report_command(
            team_id=TEAM_ID, channel="C777", slack_user=INSTALLER_SLACK_USER,
            text="", command="/competitive-scan", response_url=RESPONSE_URL,
        )
    assert "Something went wrong" in mock_http.call_args.kwargs["json"]["text"]


async def test_empty_answer_is_reported_rather_than_posted(connected):
    from app.routes import connectors as conn

    with patch("app.qa_agent.answer", return_value={"answer": "  "}), \
         patch("app.connectors.slack_oauth.post_message") as mock_post, \
         patch("app.routes.connectors.requests.post") as mock_http:
        await conn._run_slack_report_command(
            team_id=TEAM_ID, channel="C777", slack_user=INSTALLER_SLACK_USER,
            text="", command="/competitive-scan", response_url=RESPONSE_URL,
        )
    mock_post.assert_not_called()
    assert "couldn't complete" in mock_http.call_args.kwargs["json"]["text"]


async def test_response_url_post_failure_never_breaks_the_run(connected):
    from app.routes import connectors as conn

    with patch("app.qa_agent.answer",
               return_value={"answer": "prose answer", "citations": []}), \
         patch("app.connectors.slack_oauth.post_message") as mock_post, \
         patch("app.routes.connectors.requests.post",
               side_effect=RuntimeError("hook down")):
        await conn._run_slack_report_command(
            team_id=TEAM_ID, channel="C777", slack_user=INSTALLER_SLACK_USER,
            text="", command="/competitive-scan", response_url=RESPONSE_URL,
        )
    # The in-channel post IS the real delivery and still happened.
    assert mock_post.call_count == 1


async def test_no_response_url_is_a_no_op(connected):
    from app.routes import connectors as conn

    with patch("app.qa_agent.answer",
               return_value={"answer": "prose answer", "citations": []}), \
         patch("app.connectors.slack_oauth.post_message"), \
         patch("app.routes.connectors.requests.post") as mock_http:
        await conn._run_slack_report_command(
            team_id=TEAM_ID, channel="C777", slack_user=INSTALLER_SLACK_USER,
            text="", command="/competitive-scan", response_url="",
        )
    mock_http.assert_not_called()


# ─────────────────────────── interrupted-run sweep ───────────────────────────

def test_sweep_posts_a_retry_notice_for_an_orphaned_report_marker(connected):
    from app.routes import connectors as conn

    marker = conn._register_slack_report(
        team_id=TEAM_ID, channel="C777", thread_ts="1700000000.000100",
        question="competitive intelligence report",
    )
    assert marker in conn._slack_report_markers
    with patch("app.connectors.slack_oauth.post_message") as mock_post:
        swept = conn.sweep_interrupted_slack_reports()
    assert len(swept) == 1
    assert conn._slack_report_markers == {}          # markers cleared
    kw = mock_post.call_args.kwargs
    assert "interrupted" in kw["text"]
    assert kw["channel"] == "C777"
    assert kw["thread_ts"] == "1700000000.000100"


def test_sweep_is_a_no_op_with_no_markers(connected):
    from app.routes import connectors as conn

    with patch("app.connectors.slack_oauth.post_message") as mock_post:
        assert conn.sweep_interrupted_slack_reports() == []
    mock_post.assert_not_called()


def test_completed_run_leaves_no_marker_behind(connected):
    from app.routes import connectors as conn

    marker = conn._register_slack_report(
        team_id=TEAM_ID, channel="C777", thread_ts=None, question="q")
    conn._clear_slack_report(marker)
    assert conn._slack_report_markers == {}
    conn._clear_slack_report(None)      # tolerated


def test_sweep_survives_a_dead_connection(slack_env, monkeypatch):
    from app.routes import connectors as conn

    company_client(monkeypatch)
    conn._register_slack_report(team_id="T_GONE", channel="C1", thread_ts=None,
                                question="q")
    with patch("app.connectors.slack_oauth.post_message") as mock_post:
        assert len(conn.sweep_interrupted_slack_reports()) == 1
    mock_post.assert_not_called()
