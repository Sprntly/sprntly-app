"""Fireflies / Zoom / GitHub / HubSpot / Google Drive adapters + not-supported
copy.

Mirrors tests/test_jira_lookup.py — no network/LLM/DB. Each adapter's fetchers,
token store and connection row are patched in its own namespace.

The load-bearing assertions per adapter:
  Fireflies  the digest still owns window questions; the window read is bounded
             and stated.
  Zoom       transcript-first: the system prompt and both tool descriptions
             mandate reading a matching recording's transcript before
             answering a content question — a search result (metadata only)
             must never be presented as a complete answer, or listed with a
             question asking whether to read it.
  GitHub     an installation id NEVER comes from model input; a repo outside the
             company's installation is refused before any HTTP call.
  HubSpot    a 403 is a SCOPE gap, said as one — never "there are no deals" —
             and other object types keep working in the same loop.
  Drive      only picked files are readable, and the copy never claims a
             Drive-wide search.
"""
from __future__ import annotations

import json

import pytest
import requests
from fastapi import HTTPException

from app.connector_lookup import answer as ca
from app.connector_lookup import registry
from app.connector_lookup.fireflies import PROVIDER as FIREFLIES
from app.connector_lookup.gdrive import PROVIDER as DRIVE
from app.connector_lookup.github import PROVIDER as GITHUB
from app.connector_lookup.hubspot import PROVIDER as HUBSPOT
from app.connector_lookup.zoom import PROVIDER as ZOOM
from app.kg_ingest.pullers.fireflies import CallTranscript


def _session(provider, handle, notes=None):
    return ca.LookupSession(provider=provider, handle=handle, notes=notes or [])


# ── Fireflies ────────────────────────────────────────────────────────────────

def _call(cid="c1", title="Acme onboarding", overview="they want SSO",
          keywords=None, quotes=None):
    return CallTranscript(
        external_id=cid, title=title, date="2026-07-20T10:00:00+00:00",
        participants=["ada@acme.com"], overview=overview,
        action_items="follow up", keywords=keywords or ["sso"],
        quotes=quotes or [{"speaker": "Ada", "text": "we need SSO by Q4"}],
    )


def _ff_handle():
    from app.connector_lookup.fireflies import FirefliesHandle

    return FirefliesHandle(api_key="key-a")


def test_fireflies_open_session_none_when_not_connected(monkeypatch):
    from app import db

    monkeypatch.setattr(db, "get_connection", lambda cid, prov: None)
    assert FIREFLIES.open_session("co") is None


def test_fireflies_api_key_is_read_for_the_authenticated_company_only(monkeypatch):
    from app import db
    from app.connector_lookup import fireflies as ff

    seen = []
    monkeypatch.setattr(db, "get_connection", lambda cid, prov: seen.append((cid, prov))
                        or {"token_json_encrypted": "enc"})
    monkeypatch.setattr(ff, "decrypt_token_json", lambda enc: json.dumps({"api_key": "k"}))
    session = FIREFLIES.open_session("co-a")
    assert seen == [("co-a", "fireflies")]
    assert session.handle.api_key == "k"


def test_fireflies_unreadable_token_is_not_connected(monkeypatch):
    from app import db
    from app.connector_lookup import fireflies as ff
    from app.connectors.tokens import TokenEncryptionError

    monkeypatch.setattr(db, "get_connection", lambda cid, prov: {"token_json_encrypted": "x"})

    def boom(enc):
        raise TokenEncryptionError("bad key")

    monkeypatch.setattr(ff, "decrypt_token_json", boom)
    assert FIREFLIES.open_session("co") is None


def test_fireflies_search_filters_and_states_the_window(monkeypatch):
    from app.connector_lookup import fireflies as ff

    captured = {}

    def fake_fetch(api_key, *, since=None, until=None, limit=50):
        captured.update({"key": api_key, "limit": limit,
                         "days": round((until - since).total_seconds() / 86400)})
        return [_call("c1"), _call("c2", title="Weekly standup",
                                   overview="sprint chatter", keywords=["sprint"],
                                   quotes=[{"speaker": "Bo", "text": "all green"}])]

    monkeypatch.setattr(ff, "fetch_calls", fake_fetch)
    out = FIREFLIES.dispatch(_session("fireflies", _ff_handle()),
                             "fireflies_search_calls", {"keywords": "SSO", "days": 14})
    assert captured["key"] == "key-a" and captured["days"] == 14
    assert "c1: Acme onboarding" in out
    assert "Weekly standup" not in out
    assert "in the last 14 days" in out and "of 2 read" in out


def test_fireflies_window_is_clamped(monkeypatch):
    from app.connector_lookup import fireflies as ff

    captured = {}
    monkeypatch.setattr(ff, "fetch_calls", lambda key, *, since, until, limit: captured.update(
        {"days": round((until - since).total_seconds() / 86400)}) or [])
    FIREFLIES.dispatch(_session("fireflies", _ff_handle()), "fireflies_search_calls",
                       {"days": 9999})
    assert captured["days"] == ff._MAX_DAYS


def test_fireflies_empty_search_says_which_window_it_read(monkeypatch):
    """Case 3: nothing found is a statement about the window, not about reality."""
    from app.connector_lookup import fireflies as ff

    monkeypatch.setattr(ff, "fetch_calls", lambda *a, **k: [])
    out = FIREFLIES.dispatch(_session("fireflies", _ff_handle()),
                             "fireflies_search_calls", {"keywords": "zzz"})
    assert "no Fireflies calls in the last 30 days" in out
    assert "Say which window you searched" in out


def test_fireflies_huge_result_is_capped(monkeypatch):
    """Case 4: 500 calls in the window → capped list with the honest marker."""
    from app.connector_lookup import fireflies as ff

    monkeypatch.setattr(ff, "fetch_calls",
                        lambda *a, **k: [_call(f"c{i}") for i in range(500)])
    out = FIREFLIES.dispatch(_session("fireflies", _ff_handle()),
                             "fireflies_search_calls", {})
    assert "showing 10 of 500 matches" in out


def test_fireflies_get_call_uses_the_search_cache(monkeypatch):
    """Search-then-read must not pay for the window twice."""
    from app.connector_lookup import fireflies as ff

    calls = {"n": 0}

    def fake_fetch(api_key, *, since=None, until=None, limit=50):
        calls["n"] += 1
        return [_call("c1")]

    monkeypatch.setattr(ff, "fetch_calls", fake_fetch)
    session = _session("fireflies", _ff_handle())
    FIREFLIES.dispatch(session, "fireflies_search_calls", {})
    out = FIREFLIES.dispatch(session, "fireflies_get_call", {"call_id": "c1"})
    assert calls["n"] == 1
    assert "we need SSO by Q4" in out           # speaker-attributed quotes
    assert "Call: Acme onboarding" in out


def test_fireflies_get_call_unknown_id_is_honest(monkeypatch):
    from app.connector_lookup import fireflies as ff

    monkeypatch.setattr(ff, "fetch_calls", lambda *a, **k: [_call("c1")])
    out = FIREFLIES.dispatch(_session("fireflies", _ff_handle()),
                             "fireflies_get_call", {"call_id": "nope"})
    assert "no Fireflies call with id nope" in out
    assert "search a wider window" in out


def test_fireflies_requires_a_call_id():
    out = FIREFLIES.dispatch(_session("fireflies", _ff_handle()),
                             "fireflies_get_call", {})
    assert out == "(fireflies_get_call: 'call_id' is required)"


def test_fireflies_api_failure_reaches_the_model_as_words(monkeypatch):
    from app.connector_lookup import fireflies as ff

    def boom(*a, **k):
        raise RuntimeError("Fireflies GraphQL error: unauthorized")

    monkeypatch.setattr(ff, "fetch_calls", boom)
    monkeypatch.setattr(FIREFLIES, "open_session",
                        lambda eid: _session("fireflies", _ff_handle()))
    out = ca.answer(enterprise_id="co", question="find the SSO call",
                    providers=[FIREFLIES],
                    run_loop=lambda **k: k["dispatch"]("fireflies_search_calls", {}),
                    log=lambda *a: None)
    assert "Fireflies fireflies_search_calls failed" in out["answer"]
    assert "unauthorized" in out["answer"]


def test_fireflies_system_block_defers_window_summaries_to_the_digest():
    block = FIREFLIES.system_block()
    assert "bounded window" in block
    assert "digest path handles that" in block


# ── Zoom ─────────────────────────────────────────────────────────────────────
#
# Load-bearing assertion: transcript-first. A search result is metadata only
# (topic, host, time) — never call content — so the system prompt and both
# tool descriptions must instruct the model to call zoom_get_recording for the
# matching meeting(s) BEFORE answering a content question, rather than listing
# search hits and asking the user whether it should read the transcript. This
# was a real product complaint: a live question ("check zoom for the most
# recent call") returned a metadata table and asked permission to read it,
# when the transcript is the entire point of asking. Unit tests can only
# verify the INSTRUCTION is present and correctly worded — whether the model
# actually obeys a system prompt is not something a unit test can guarantee,
# same as every other "MUST"-worded rule in these adapters' system blocks.

def _zoom_ctx(user_ids=None, user_names=None):
    from app.connectors.zoom_oauth import ZoomContext

    return ZoomContext(
        company_id="co", access_token="tok-a",
        user_ids=user_ids or [], user_names=user_names or {},
    )


def _zoom_handle(**ctx_kwargs):
    from app.connector_lookup.zoom import ZoomHandle

    return ZoomHandle(ctx=_zoom_ctx(**ctx_kwargs))


def _meeting(uuid="m1", topic="Acme onboarding", host_email="rep@sprntly.ai",
             start_time="2026-07-20T10:00:00Z", duration=30):
    return {
        "uuid": uuid, "topic": topic, "host_email": host_email,
        "start_time": start_time, "duration": duration,
    }


def _host(hid="h1", email="rep@sprntly.ai", display_name="Rep"):
    return {"id": hid, "email": email, "display_name": display_name}


def test_zoom_open_session_none_when_token_rejected(monkeypatch):
    from app.connector_lookup import zoom as z
    from app.connectors.zoom_oauth import ZoomAuthExpiredError

    def boom(company_id):
        raise ZoomAuthExpiredError("reconnect")

    monkeypatch.setattr(z, "sync_context", boom)
    assert ZOOM.open_session("co") is None


def test_zoom_open_session_none_on_any_failure_never_raises(monkeypatch):
    """_load_context's docstring promises it never raises — a live-lookup open
    that threw would take the whole chat answer down with it."""
    from app.connector_lookup import zoom as z

    def boom(company_id):
        raise RuntimeError("supabase is down")

    monkeypatch.setattr(z, "sync_context", boom)
    assert ZOOM.open_session("co") is None


def test_zoom_open_session_returns_a_working_handle(monkeypatch):
    from app.connector_lookup import zoom as z

    monkeypatch.setattr(z, "sync_context", lambda cid: _zoom_ctx())
    session = ZOOM.open_session("co-a")
    assert session.provider == "zoom"
    assert session.handle.ctx.company_id == "co"


def test_zoom_search_filters_and_states_the_window(monkeypatch):
    from app.connector_lookup import zoom as z

    captured = {}

    def fake_list(token, user_id, *, frm=None, to=None, page_size=30):
        captured.setdefault("calls", []).append((user_id, frm, to))
        return [
            _meeting("m1", "Acme onboarding"),
            _meeting("m2", "Weekly standup", host_email="lead@sprntly.ai"),
        ]

    monkeypatch.setattr(z, "_hosts", lambda ctx: [_host()])
    monkeypatch.setattr(z, "list_user_recordings", fake_list)
    out = ZOOM.dispatch(_session("zoom", _zoom_handle()),
                        "zoom_search_recordings", {"keywords": "acme", "days": 14})
    assert "m1: Acme onboarding" in out
    assert "Weekly standup" not in out
    assert "in the last 14 days" in out and "of 2 read" in out
    # Every host in the selection was actually asked, not just the first.
    assert captured["calls"][0][0] == "h1"


def test_zoom_window_is_clamped_to_the_adapter_max(monkeypatch):
    from app.connector_lookup import zoom as z

    monkeypatch.setattr(z, "_hosts", lambda ctx: [_host()])
    monkeypatch.setattr(z, "list_user_recordings", lambda *a, **k: [])
    days_seen = {}

    real_sync_windows = z.sync_windows

    def spying_sync_windows(cursor, *, today=None, max_windows=3):
        days_seen["max_windows"] = max_windows
        return real_sync_windows(cursor, today=today, max_windows=max_windows)

    monkeypatch.setattr(z, "sync_windows", spying_sync_windows)
    ZOOM.dispatch(_session("zoom", _zoom_handle()), "zoom_search_recordings",
                 {"days": 9999})
    assert days_seen["max_windows"] == -(-z._MAX_DAYS // 30)  # ceil(90/30) = 3


def test_zoom_a_wide_window_is_walked_in_month_sized_chunks_not_one_call(monkeypatch):
    """The exact bug class Zoom's silent clamping invites: a naive one-shot
    request for a wide range looks like a quiet quarter instead of an error.
    Every individual list_user_recordings call must span <=31 days."""
    from app.connector_lookup import zoom as z
    from datetime import date

    spans = []

    def fake_list(token, user_id, *, frm=None, to=None, page_size=30):
        span = (date.fromisoformat(to) - date.fromisoformat(frm)).days
        spans.append(span)
        return []

    monkeypatch.setattr(z, "_hosts", lambda ctx: [_host()])
    monkeypatch.setattr(z, "list_user_recordings", fake_list)
    ZOOM.dispatch(_session("zoom", _zoom_handle()), "zoom_search_recordings",
                 {"days": z._MAX_DAYS})
    assert spans, "list_user_recordings was never called"
    assert all(span <= 31 for span in spans), spans


def test_zoom_empty_search_says_which_window_it_read(monkeypatch):
    from app.connector_lookup import zoom as z

    monkeypatch.setattr(z, "_hosts", lambda ctx: [_host()])
    monkeypatch.setattr(z, "list_user_recordings", lambda *a, **k: [])
    out = ZOOM.dispatch(_session("zoom", _zoom_handle()),
                        "zoom_search_recordings", {"keywords": "zzz"})
    assert "no Zoom cloud recordings in the last 30 days" in out
    assert "Say which window you searched" in out


def test_zoom_huge_result_is_capped(monkeypatch):
    from app.connector_lookup import zoom as z

    monkeypatch.setattr(z, "_hosts", lambda ctx: [_host()])
    monkeypatch.setattr(
        z, "list_user_recordings",
        lambda *a, **k: [_meeting(f"m{i}") for i in range(50)],
    )
    out = ZOOM.dispatch(_session("zoom", _zoom_handle()),
                        "zoom_search_recordings", {})
    assert "showing 10 of 50 matches" in out


def test_zoom_get_recording_uses_the_search_cache(monkeypatch):
    """Search-then-read must not re-list the window."""
    from app.connector_lookup import zoom as z

    calls = {"n": 0}

    def fake_list(token, user_id, *, frm=None, to=None, page_size=30):
        calls["n"] += 1
        return [_meeting("m1")]

    monkeypatch.setattr(z, "_hosts", lambda ctx: [_host()])
    monkeypatch.setattr(z, "list_user_recordings", fake_list)
    monkeypatch.setattr(z, "_transcript_for",
                        lambda ctx, meeting: {"download_url": "https://zoom/x"})
    monkeypatch.setattr(z, "fetch_transcript_text", lambda tok, url: "raw-vtt")
    monkeypatch.setattr(z, "parse_vtt", lambda raw: ("Ada: we need SSO by Q4", ["Ada"]))

    session = _session("zoom", _zoom_handle())
    ZOOM.dispatch(session, "zoom_search_recordings", {})
    out = ZOOM.dispatch(session, "zoom_get_recording", {"meeting_id": "m1"})
    assert calls["n"] == 1
    assert "we need SSO by Q4" in out
    assert "Acme onboarding" in out
    assert "speakers: Ada" in out


def test_zoom_get_recording_unknown_id_is_honest(monkeypatch):
    from app.connector_lookup import zoom as z

    monkeypatch.setattr(z, "_hosts", lambda ctx: [_host()])
    monkeypatch.setattr(z, "list_user_recordings", lambda *a, **k: [_meeting("m1")])
    out = ZOOM.dispatch(_session("zoom", _zoom_handle()),
                        "zoom_get_recording", {"meeting_id": "nope"})
    assert "no Zoom recording with id nope" in out
    assert "search a wider window" in out


def test_zoom_requires_a_meeting_id():
    out = ZOOM.dispatch(_session("zoom", _zoom_handle()),
                        "zoom_get_recording", {})
    assert out == "(zoom_get_recording: 'meeting_id' is required)"


def test_zoom_get_recording_with_no_transcript_says_so_honestly(monkeypatch):
    """The commonest real case: audio transcription is off, or Zoom hasn't
    finished processing. Never invent content — say so, which IS a complete
    answer per the system prompt's own honest-limits rule."""
    from app.connector_lookup import zoom as z

    monkeypatch.setattr(z, "_hosts", lambda ctx: [_host()])
    monkeypatch.setattr(z, "list_user_recordings", lambda *a, **k: [_meeting("m1")])
    monkeypatch.setattr(z, "_transcript_for", lambda ctx, meeting: None)

    out = ZOOM.dispatch(_session("zoom", _zoom_handle()),
                        "zoom_get_recording", {"meeting_id": "m1"})
    assert "No transcript available" in out
    assert "audio transcription being turned off" in out


def test_zoom_system_block_and_tools_mandate_reading_the_transcript():
    """The regression this whole section exists to prevent: a search result
    alone must never be presented as a complete answer to a content question."""
    block = ZOOM.system_block()
    assert "ALWAYS READ THE TRANSCRIPT" in block
    assert "do not stop at the search result" in block.lower()
    assert "do not list search results and ask" in block.lower()

    tools = {t["name"]: t for t in ZOOM.tools()}
    assert "metadata only" in tools["zoom_search_recordings"]["description"].lower()
    assert "zoom_get_recording" in tools["zoom_search_recordings"]["description"]
    assert "before answering" in tools["zoom_get_recording"]["description"].lower()


# ── GitHub ───────────────────────────────────────────────────────────────────

def _gh_session():
    return _session("github", {"company_id": "co-a"})


def test_github_open_session_none_without_an_installation(monkeypatch):
    from app import db

    monkeypatch.setattr(db, "list_github_installations", lambda cid: [])
    assert GITHUB.open_session("co-a") is None


def test_github_open_session_ignores_suspended_installations(monkeypatch):
    from app import db

    monkeypatch.setattr(db, "list_github_installations",
                        lambda cid: [{"installation_id": 1, "suspended": True}])
    assert GITHUB.open_session("co-a") is None


def test_github_session_binds_the_authenticated_company(monkeypatch):
    from app import db

    seen = []
    monkeypatch.setattr(db, "list_github_installations", lambda cid: seen.append(cid) or [
        {"installation_id": 42, "suspended": False, "account_login": "acme"}])
    session = GITHUB.open_session("co-a")
    assert seen == ["co-a"]
    assert session.handle == {"company_id": "co-a"}
    assert "acme" in session.notes[0]


def test_github_installation_is_resolved_per_repo_under_the_company(monkeypatch):
    """Cross-tenant isolation: the id used to mint an installation token comes
    from a company-scoped DB lookup, never from the tool input."""
    from app import db

    seen = []
    monkeypatch.setattr(db, "find_github_installation_for_repo",
                        lambda repo, company_id: seen.append((repo, company_id))
                        or {"installation_id": 42})
    captured = {}
    monkeypatch.setattr(
        "app.agent_tools.github.github_list_commits",
        lambda **k: captured.update(k) or {"commits": [{"sha": "abc"}]},
    )
    out = GITHUB.dispatch(_gh_session(), "github_list_commits", {
        # A model trying to hand us another tenant's installation.
        "repo": "acme/widgets", "installation_id": 999, "company_id": "co-b",
    })
    assert seen == [("acme/widgets", "co-a")]
    assert captured["installation_id"] == 42
    assert "company_id" not in captured and "abc" in out


def test_github_refuses_a_repo_the_company_does_not_own(monkeypatch):
    """Case 6: a non-owned installation/repo is rejected BEFORE any HTTP call."""
    from app import db

    monkeypatch.setattr(db, "find_github_installation_for_repo",
                        lambda repo, company_id: None)
    called = []
    monkeypatch.setattr("app.agent_tools.github.github_get_file",
                        lambda **k: called.append(k))
    out = GITHUB.dispatch(_gh_session(), "github_get_file",
                          {"repo": "someone-else/private", "path": "README.md"})
    assert called == []
    assert "isn't covered by this company's GitHub installation" in out


def test_github_requires_a_full_repo_name():
    out = GITHUB.dispatch(_gh_session(), "github_list_commits", {"repo": "widgets"})
    assert "'repo' is required, in 'owner/name' form" in out


def _own_repo(monkeypatch):
    from app import db

    monkeypatch.setattr(db, "find_github_installation_for_repo",
                        lambda repo, company_id: {"installation_id": 7})


def test_github_pr_diff_is_truncated_with_a_marker(monkeypatch):
    _own_repo(monkeypatch)
    monkeypatch.setattr("app.agent_tools.github.github_get_pr_diff",
                        lambda **k: {"diff": "+" * 50_000})
    out = GITHUB.dispatch(_gh_session(), "github_get_pr_diff",
                          {"repo": "acme/widgets", "pr_number": 12})
    assert "truncated" in out
    assert len(out) < 20_000


def test_github_file_content_is_truncated(monkeypatch):
    _own_repo(monkeypatch)
    monkeypatch.setattr("app.agent_tools.github.github_get_file",
                        lambda **k: {"content": "x" * 50_000, "path": "a.py"})
    out = GITHUB.dispatch(_gh_session(), "github_get_file",
                          {"repo": "acme/widgets", "path": "a.py"})
    assert "truncated" in out


def test_github_limits_are_clamped(monkeypatch):
    _own_repo(monkeypatch)
    captured = {}
    monkeypatch.setattr("app.agent_tools.github.github_list_commits",
                        lambda **k: captured.update(k) or {"commits": [1]})
    GITHUB.dispatch(_gh_session(), "github_list_commits",
                    {"repo": "a/b", "limit": 9999})
    assert captured["limit"] == 30


def test_github_not_found_is_reported_as_a_visibility_limit(monkeypatch):
    _own_repo(monkeypatch)
    monkeypatch.setattr("app.agent_tools.github.github_get_file",
                        lambda **k: {"error": "not_found", "repo": "a/b", "path": "x"})
    out = GITHUB.dispatch(_gh_session(), "github_get_file", {"repo": "a/b", "path": "x"})
    assert "not found in the repos I can read" in out


def test_github_http_error_suggests_the_right_remedy(monkeypatch):
    _own_repo(monkeypatch)
    monkeypatch.setattr("app.agent_tools.github.github_search_code",
                        lambda **k: {"error": "http_403"})
    out = GITHUB.dispatch(_gh_session(), "github_search_code",
                          {"repo": "a/b", "query": "login"})
    assert "http_403" in out and "reconnecting" in out


def test_github_pr_number_must_be_numeric(monkeypatch):
    _own_repo(monkeypatch)
    out = GITHUB.dispatch(_gh_session(), "github_get_pr_diff",
                          {"repo": "a/b", "pr_number": "twelve"})
    assert "'pr_number' must be a number" in out


def test_github_unknown_tool_is_rejected(monkeypatch):
    _own_repo(monkeypatch)
    assert "unknown tool" in GITHUB.dispatch(_gh_session(), "github_push", {"repo": "a/b"})


def test_github_system_block_states_read_only_and_visibility():
    block = GITHUB.system_block()
    assert "read-only" in block
    assert "not in the repos I can read" in block
    assert "cannot push" in block


# ── HubSpot ──────────────────────────────────────────────────────────────────

class _Resp:
    def __init__(self, payload, status=200):
        self._payload = payload
        self.status_code = status

    def json(self):
        return self._payload


def _hs_session():
    return _session("hubspot", "hs-token")


def test_hubspot_open_session_none_when_not_connected(monkeypatch):
    from app.connectors import hubspot_sync

    def boom(company_id):
        raise HTTPException(404, "HubSpot is not connected")

    monkeypatch.setattr(hubspot_sync, "_get_valid_access_token", boom)
    assert HUBSPOT.open_session("co-a") is None


def test_hubspot_token_is_resolved_for_the_authenticated_company(monkeypatch):
    from app.connectors import hubspot_sync

    seen = []
    monkeypatch.setattr(hubspot_sync, "_get_valid_access_token",
                        lambda cid: seen.append(cid) or ("tok", {}))
    session = HUBSPOT.open_session("co-a")
    assert seen == ["co-a"] and session.handle == "tok"


def test_hubspot_search_renders_rows(monkeypatch):
    captured = {}

    def fake_post(url, headers=None, json=None, timeout=None):
        captured.update({"url": url, "body": json, "timeout": timeout,
                         "auth": headers["Authorization"]})
        return _Resp({"total": 1, "results": [
            {"id": "501", "properties": {"dealname": "Acme expansion",
                                         "amount": "12000", "dealstage": "contract"}},
        ]})

    monkeypatch.setattr(requests, "post", fake_post)
    out = HUBSPOT.dispatch(_hs_session(), "hubspot_search",
                           {"object_type": "deals", "query": "acme"})
    assert captured["url"].endswith("/crm/v3/objects/deals/search")
    assert captured["auth"] == "Bearer hs-token" and captured["timeout"] == 15
    assert "- 501: dealname: Acme expansion" in out


def test_hubspot_403_is_a_scope_gap_not_an_empty_result(monkeypatch):
    """Case 10: the scope wasn't granted. Saying "no deals" would be a lie."""
    monkeypatch.setattr(requests, "post", lambda *a, **k: _Resp({}, status=403))
    out = HUBSPOT.dispatch(_hs_session(), "hubspot_search",
                           {"object_type": "deals", "query": "acme"})
    assert "that scope was NOT granted" in out
    assert "do not say there are no deals" in out
    assert "permissions gap" in out


def test_hubspot_scope_gate_is_per_object_type_in_one_loop(monkeypatch):
    """Case 10, second half: deals 403 while contacts still work — same session,
    same loop, no reconnect in between."""
    def fake_post(url, headers=None, json=None, timeout=None):
        if "/deals/" in url:
            return _Resp({}, status=403)
        return _Resp({"total": 1, "results": [
            {"id": "1", "properties": {"firstname": "Ada", "email": "ada@acme.com"}}]})

    monkeypatch.setattr(requests, "post", fake_post)
    monkeypatch.setattr(HUBSPOT, "open_session", lambda eid: _hs_session())
    out = ca.answer(
        enterprise_id="co-a", question="which deals mention onboarding",
        providers=[HUBSPOT],
        run_loop=lambda **k: "|".join([
            k["dispatch"]("hubspot_search", {"object_type": "deals", "query": "x"}),
            k["dispatch"]("hubspot_search", {"object_type": "contacts", "query": "x"}),
        ]),
        log=lambda *a: None,
    )
    assert "scope was NOT granted" in out["answer"]
    assert "ada@acme.com" in out["answer"]


def test_hubspot_401_says_reconnect(monkeypatch):
    monkeypatch.setattr(requests, "post", lambda *a, **k: _Resp({}, status=401))
    out = HUBSPOT.dispatch(_hs_session(), "hubspot_search",
                           {"object_type": "contacts", "query": "x"})
    assert "needs reconnecting" in out


def test_hubspot_429_says_results_may_be_partial(monkeypatch):
    monkeypatch.setattr(requests, "post", lambda *a, **k: _Resp({}, status=429))
    out = HUBSPOT.dispatch(_hs_session(), "hubspot_search",
                           {"object_type": "contacts", "query": "x"})
    assert "rate-limited" in out and "may be incomplete" in out


def test_hubspot_timeout_is_honest(monkeypatch):
    def boom(*a, **k):
        raise requests.Timeout("slow")

    monkeypatch.setattr(requests, "post", boom)
    out = HUBSPOT.dispatch(_hs_session(), "hubspot_search",
                           {"object_type": "contacts", "query": "x"})
    assert out == "(HubSpot timed out on hubspot_search — no results from this call)"


def test_hubspot_empty_result_is_honest(monkeypatch):
    monkeypatch.setattr(requests, "post", lambda *a, **k: _Resp({"total": 0, "results": []}))
    out = HUBSPOT.dispatch(_hs_session(), "hubspot_search",
                           {"object_type": "contacts", "query": "zzz"})
    assert out == "(no HubSpot contacts match 'zzz')"


def test_hubspot_huge_result_is_capped(monkeypatch):
    monkeypatch.setattr(requests, "post", lambda *a, **k: _Resp({
        "total": 500,
        "results": [{"id": str(i), "properties": {"firstname": f"n{i}"}} for i in range(500)],
    }))
    out = HUBSPOT.dispatch(_hs_session(), "hubspot_search",
                           {"object_type": "contacts", "query": "a"})
    assert "showing 20 of 500 matches" in out


def test_hubspot_rejects_an_unknown_object_type():
    out = HUBSPOT.dispatch(_hs_session(), "hubspot_search",
                           {"object_type": "invoices", "query": "x"})
    assert "'object_type' must be one of" in out


def test_hubspot_get_renders_every_property(monkeypatch):
    monkeypatch.setattr(requests, "get", lambda *a, **k: _Resp({
        "id": "501", "properties": {"dealname": "Acme", "custom_field": "yes"}}))
    out = HUBSPOT.dispatch(_hs_session(), "hubspot_get",
                           {"object_type": "deals", "record_id": "501"})
    assert "deal 501" in out and "custom_field: yes" in out


def test_hubspot_singular_labels_are_english(monkeypatch):
    """"companies"[:-1] is "companie" — and this string is read by a user."""
    from app.connector_lookup import hubspot as hs

    assert hs.SINGULAR["companies"] == "company"
    monkeypatch.setattr(requests, "get", lambda *a, **k: _Resp({}, status=404))
    out = HUBSPOT.dispatch(_hs_session(), "hubspot_get",
                           {"object_type": "companies", "record_id": "7"})
    assert out == "(no HubSpot company with id 7)"
    monkeypatch.setattr(requests, "get", lambda *a, **k: _Resp(
        {"id": "7", "properties": {"name": "Acme"}}))
    out = HUBSPOT.dispatch(_hs_session(), "hubspot_get",
                           {"object_type": "companies", "record_id": "7"})
    assert out.startswith("company 7")
    assert "companie" not in out


def test_hubspot_get_missing_record(monkeypatch):
    monkeypatch.setattr(requests, "get", lambda *a, **k: _Resp({}, status=404))
    out = HUBSPOT.dispatch(_hs_session(), "hubspot_get",
                           {"object_type": "deals", "record_id": "9"})
    assert "no HubSpot deal with id 9" in out


# ── HubSpot — dispatch_records (AC1/AC2/AC3/AC4) ────────────────────────────


def _hs_search_response():
    return _Resp({"total": 1, "results": [
        {"id": "501", "properties": {
            "dealname": "Acme expansion", "amount": "12000",
            "dealstage": "contract", "pipeline": "default",
            "closedate": "2026-09-01", "hs_lastmodifieddate": "2026-08-01T00:00:00Z",
        }},
    ]})


def test_hubspot_dispatch_records_returns_none_for_non_deal_object_types(monkeypatch):
    """AC1/AC2 scope: the sweep leg only ever calls hubspot_search(deals) — the
    other three object types (contacts, companies, tickets) are unimplemented
    on purpose, proving the optional capability degrades cleanly for a tool
    call this adapter's dispatch_records does not cover."""
    monkeypatch.setattr(requests, "post", lambda *a, **k: _hs_search_response())
    for object_type in ("contacts", "companies", "tickets"):
        assert HUBSPOT.dispatch_records(
            _hs_session(), "hubspot_search", {"object_type": object_type, "query": "x"}
        ) is None
    assert HUBSPOT.dispatch_records(_hs_session(), "hubspot_get", {}) is None


def test_hubspot_dispatch_records_text_matches_dispatch_exactly(monkeypatch):
    """AC5/mutation-proof: dispatch_records's text must be byte-identical to
    dispatch's own output for the identical call — both are built from the
    same _render_row/footer logic over one fetch."""
    monkeypatch.setattr(requests, "post", lambda *a, **k: _hs_search_response())
    inp = {"object_type": "deals", "query": "acme"}
    expected = HUBSPOT.dispatch(_hs_session(), "hubspot_search", inp)

    monkeypatch.setattr(requests, "post", lambda *a, **k: _hs_search_response())
    text, records = HUBSPOT.dispatch_records(_hs_session(), "hubspot_search", inp)
    assert text == expected
    assert records is not None and len(records) == 1


def test_hubspot_dispatch_records_ac4_not_byte_identical_to_the_puller(monkeypatch):
    """AC4 — HubSpot's answer for this provider: NOT byte-identical, and the
    test proves exactly which properties diverge and why, rather than merely
    asserting inequality.

    `hubspot_search`'s OBJECT_PROPS["deals"] never requests `hubspot_owner_id`
    or `description`, and the search call carries no `associations` param, so
    `owner_id`/`company_ids`/`text` cannot be populated without a second HTTP
    call — out of scope per the ticket. The puller's record for the identical
    deal DOES carry them (from kg_ingest.pullers.hubspot._pull_deals, which
    requests the wider _DEAL_PROPS + associations="companies").
    """
    from app.kg_ingest.types import RawRecord

    monkeypatch.setattr(requests, "post", lambda *a, **k: _hs_search_response())
    _text, records = HUBSPOT.dispatch_records(
        _hs_session(), "hubspot_search", {"object_type": "deals", "query": "acme"}
    )
    sweep_record = records[0]

    # The puller's record for the SAME deal id, as kg_ingest.pullers.hubspot
    # ._pull_deals would build it from its own (wider) fetch — hand-built here
    # to pin the wire format, mirroring the deal fields _hs_search_response
    # supplies plus the two the search call never asks HubSpot for.
    pull_record = RawRecord(
        provider="hubspot", kind="deal", external_id="501",
        title="Acme expansion",
        text="Renewal conversation notes",  # from `description`, search-absent
        properties={
            "amount_usd": "12000", "stage": "contract", "pipeline": "default",
            "close_date": "2026-09-01", "owner_id": "42",  # search-absent
            "company_ids": ["9001"],                        # search-absent
        },
        timestamp="2026-08-01T00:00:00Z",
    )

    assert sweep_record.render() != pull_record.render(), (
        "AC4: HubSpot's search-based record must NOT claim byte-identity — "
        "owner_id/company_ids/text are structurally unavailable from the "
        "search response"
    )
    # The fields the search response DOES carry still agree with the puller.
    assert sweep_record.external_id == pull_record.external_id
    assert sweep_record.properties["amount_usd"] == pull_record.properties["amount_usd"]
    assert sweep_record.properties["stage"] == pull_record.properties["stage"]
    # The exact gap, named rather than merely observed.
    assert sweep_record.properties["owner_id"] is None
    assert sweep_record.properties["company_ids"] is None
    assert sweep_record.text == ""


# ── HubSpot — AC-A1 persist-thread enrichment achieves byte-identity ───────


def test_hubspot_enrich_record_achieves_byte_identity_with_the_real_puller(monkeypatch):
    """AC-A1: after `enrich_record` (persist-thread only), the sweep's record
    DOES render byte-identical to the real puller's
    (`kg_ingest.pullers.hubspot._pull_deals`) record for the same deal —
    proven against the real puller, not a hand-reconstruction."""
    from app.connector_lookup.hubspot import enrich_record
    from app.kg_ingest.pullers import hubspot as hubspot_puller

    deal_obj = {
        "id": "501",
        "properties": {
            "dealname": "Acme expansion", "amount": "12000",
            "dealstage": "contract", "pipeline": "default",
            "closedate": "2026-09-01", "hs_lastmodifieddate": "2026-08-01T00:00:00Z",
            "hubspot_owner_id": "42", "description": "Renewal conversation notes",
        },
        "associations": {"companies": {"results": [
            {"id": "9001", "type": "deal_to_company"},
        ]}},
    }

    def fake_puller_get(token, path, params=None):
        assert path == "/crm/v3/objects/deals"
        assert params["associations"] == "companies"
        return {"results": [deal_obj], "paging": {}}

    monkeypatch.setattr(hubspot_puller, "_get", fake_puller_get)
    pull_record = next(hubspot_puller._pull_deals("tok"))

    monkeypatch.setattr(requests, "post", lambda *a, **k: _hs_search_response())
    _text, records = HUBSPOT.dispatch_records(
        _hs_session(), "hubspot_search", {"object_type": "deals", "query": "acme"}
    )
    lean_record = records[0]

    # Persist-thread enrichment: one deal GET with associations=companies +
    # the SAME `_DEAL_PROPS` the puller requests.
    def fake_get(url, headers=None, params=None, timeout=None):
        assert url.endswith("/crm/v3/objects/deals/501")
        assert params["associations"] == "companies"
        assert params["properties"] == hubspot_puller._DEAL_PROPS
        return _Resp(deal_obj)

    monkeypatch.setattr(requests, "get", fake_get)
    enriched = enrich_record(_hs_session(), lean_record)

    assert enriched.render() == pull_record.render(), (
        "AC-A1: the ENRICHED record must render byte-identical to the real "
        "puller's record for the same deal"
    )
    assert enriched.external_id == pull_record.external_id == "501"


def test_hubspot_enrich_record_falls_back_to_the_lean_record_on_404(monkeypatch):
    """AC-A4: a deal that vanished between the search and the enrichment
    fetch (404) is not an error — enrichment falls back to the lean record."""
    from app.connector_lookup.hubspot import enrich_record

    monkeypatch.setattr(requests, "post", lambda *a, **k: _hs_search_response())
    _text, records = HUBSPOT.dispatch_records(
        _hs_session(), "hubspot_search", {"object_type": "deals", "query": "acme"}
    )
    lean_record = records[0]

    monkeypatch.setattr(requests, "get", lambda *a, **k: _Resp({}, status=404))
    out = enrich_record(_hs_session(), lean_record)
    assert out is lean_record


# ── Google Drive ─────────────────────────────────────────────────────────────

def _drive_session(picked=None):
    return _session("google_drive", {
        "row": {"company_id": "co-a", "config_json": "{}"},
        "picked": picked if picked is not None else [{"id": "fileid1234", "name": "Launch plan.docx"}],
        "service": object(),
    })


def test_drive_open_session_none_when_not_connected(monkeypatch):
    from app import db

    monkeypatch.setattr(db, "get_connection", lambda cid, prov: None)
    assert DRIVE.open_session("co-a") is None


def test_drive_session_reads_the_companys_picked_files(monkeypatch):
    from app import db

    seen = []
    monkeypatch.setattr(db, "get_connection", lambda cid, prov: seen.append((cid, prov)) or {
        "company_id": cid,
        "config_json": json.dumps({"files": [{"id": "fileid1234", "name": "Plan.docx"}]}),
    })
    session = DRIVE.open_session("co-a")
    assert seen == [("co-a", "google_drive")]
    assert session.handle["picked"] == [{"id": "fileid1234", "name": "Plan.docx"}]
    assert "1 Drive file(s) are connected" in session.notes[0]


def test_drive_session_notes_when_nothing_was_picked(monkeypatch):
    from app import db

    monkeypatch.setattr(db, "get_connection", lambda cid, prov: {
        "company_id": cid, "config_json": json.dumps({"files": []})})
    session = DRIVE.open_session("co-a")
    assert "NO Drive files are connected yet" in session.notes[0]


def test_drive_list_states_it_is_the_complete_set():
    out = DRIVE.dispatch(_drive_session(), "drive_list_connected_files", {})
    assert "fileid1234: Launch plan.docx" in out
    assert "this is the complete set" in out


def test_drive_list_with_nothing_connected_never_claims_a_search():
    out = DRIVE.dispatch(_drive_session(picked=[]), "drive_list_connected_files", {})
    assert "no Google Drive files are connected" in out
    assert "there is no Drive-wide search" in out
    assert "instead of reporting an empty search" in out


def test_drive_read_refuses_a_file_that_was_never_connected(monkeypatch):
    """Tenancy + scope in one: a model-invented file id never reaches Drive."""
    from app.connectors import google_drive_sync

    called = []
    monkeypatch.setattr(google_drive_sync, "get_file_metadata",
                        lambda service, fid: called.append(fid))
    out = DRIVE.dispatch(_drive_session(), "drive_read_file", {"file_id": "someone-elses"})
    assert called == []
    assert "not one of this company's connected Drive files" in out


def test_drive_read_converts_the_file_in_memory(monkeypatch):
    from app.connectors import google_drive_sync

    monkeypatch.setattr(google_drive_sync, "get_file_metadata", lambda service, fid: {
        "id": fid, "name": "Launch plan.docx", "mimeType": "application/vnd.openxml"})
    monkeypatch.setattr(google_drive_sync, "download_file_content",
                        lambda service, meta: ("Launch plan.txt", b"ship on friday"))
    out = DRIVE.dispatch(_drive_session(), "drive_read_file", {"file_id": "fileid1234"})
    assert "Launch plan.docx (Drive file fileid1234)" in out
    assert "ship on friday" in out


def test_drive_read_truncates_a_long_file(monkeypatch):
    from app.connectors import google_drive_sync

    monkeypatch.setattr(google_drive_sync, "get_file_metadata",
                        lambda service, fid: {"id": fid, "name": "Big.txt"})
    monkeypatch.setattr(google_drive_sync, "download_file_content",
                        lambda service, meta: ("Big.txt", b"x" * 60_000))
    out = DRIVE.dispatch(_drive_session(), "drive_read_file", {"file_id": "fileid1234"})
    assert "truncated" in out


def test_drive_read_unsupported_type_is_explained(monkeypatch):
    from app.connectors import google_drive_sync

    monkeypatch.setattr(google_drive_sync, "get_file_metadata", lambda service, fid: {
        "id": fid, "name": "clip.mov", "mimeType": "video/quicktime"})
    monkeypatch.setattr(google_drive_sync, "download_file_content",
                        lambda service, meta: None)
    out = DRIVE.dispatch(_drive_session([{"id": "fileid1234", "name": "clip.mov"}]),
                         "drive_read_file", {"file_id": "fileid1234"})
    assert "can't convert this file type" in out


def test_drive_api_failure_is_reported_without_a_traceback(monkeypatch):
    from app.connectors import google_drive_sync

    def boom(service, fid):
        raise RuntimeError("drive exploded")

    monkeypatch.setattr(google_drive_sync, "get_file_metadata", boom)
    out = DRIVE.dispatch(_drive_session(), "drive_read_file", {"file_id": "fileid1234"})
    assert out == "(Google Drive read failed: drive exploded)"


def test_drive_requires_a_file_id():
    out = DRIVE.dispatch(_drive_session(), "drive_read_file", {})
    assert out == "(drive_read_file: 'file_id' is required)"


def test_drive_system_block_never_promises_a_drive_search():
    block = DRIVE.system_block()
    assert "no Drive-wide search" in block
    assert "NEVER say you searched the user's Drive" in block


def test_drive_unknown_tool_is_rejected():
    assert "unknown tool" in DRIVE.dispatch(_drive_session(), "drive_delete", {})


# ── registry: the full inventory, and honest copy for the rest ────────────────

@pytest.fixture()
def _connected(monkeypatch):
    from app import db

    monkeypatch.setattr(db, "list_connections", lambda cid: [{"provider": "jira"}])
    monkeypatch.setattr(db, "list_slack_connections", lambda cid: [])


def test_every_shipped_adapter_resolves():
    assert set(registry.LOOKUP_PROVIDERS) == {
        "jira", "clickup", "slack", "fireflies", "github", "hubspot",
        "google_drive", "confluence", "zoom",
    }
    for name in registry.LOOKUP_PROVIDERS:
        provider = registry.provider_for(name)
        assert provider is not None and provider.provider == name
        assert provider.tools() and provider.system_block()


def test_tool_names_are_unique_across_adapters():
    """Two adapters sharing a tool name would make dispatch routing ambiguous."""
    seen: dict[str, str] = {}
    for name in registry.LOOKUP_PROVIDERS:
        for tool in registry.provider_for(name).tools():
            assert tool["name"] not in seen, (tool["name"], seen.get(tool["name"]))
            seen[tool["name"]] = name


def test_no_adapter_exposes_a_write_tool():
    """Read-only surface. Jira's propose tool is the single exception, and it
    writes nothing — the user confirms it through routes/jira_write.py."""
    forbidden = ("create", "update", "delete", "post", "send", "merge", "push")
    for name in registry.LOOKUP_PROVIDERS:
        for tool in registry.provider_for(name).tools():
            if tool["name"] == "jira_propose_change":
                continue
            assert not any(word in tool["name"] for word in forbidden), tool["name"]


@pytest.mark.parametrize("provider,expected", [
    ("zendesk", "Zendesk isn't a Sprntly connector yet"),
    ("gong", "Gong isn't a Sprntly connector yet"),
    ("linear", "Linear isn't a Sprntly connector yet"),
    ("amplitude", "Amplitude isn't a Sprntly connector yet"),
    ("stripe", "Stripe isn't a Sprntly connector yet"),
    ("notion", "Notion isn't a Sprntly connector yet"),
])
def test_sources_with_no_connector_are_answered_honestly(provider, expected, _connected):
    out = registry.answer_for_hints(
        enterprise_id="co-a", question=f"what's in {provider}", history=None,
        hints={provider},
    )
    assert expected in out["answer"]
    assert "Connected right now: Jira." in out["answer"]
    assert "read them live" in out["answer"]


def test_two_unsupported_sources_are_named_together(_connected):
    out = registry.answer_for_hints(
        enterprise_id="co-a", question="check gong and zendesk", history=None,
        hints={"gong", "zendesk"},
    )
    assert "Gong and Zendesk aren't a Sprntly connector yet" in out["answer"]


def test_a_supported_and_an_unsupported_source_together_still_read_what_it_can(monkeypatch, _connected):
    """"check slack and zendesk" — do the readable half rather than refusing both."""
    seen = {}
    monkeypatch.setattr(ca, "answer", lambda **k: seen.update(k) or {"answer": "x"})
    registry.answer_for_hints(enterprise_id="co-a", question="check slack and zendesk",
                              history=None, hints={"slack", "zendesk"})
    assert [p.provider for p in seen["providers"]] == ["slack"]


# ── credential hygiene ───────────────────────────────────────────────────────

def test_no_adapter_credential_survives_a_repr():
    """A LookupSession repr shows up in log lines, exception context and pytest
    failure dumps — none of them may contain a live token."""
    from app.connector_lookup.fireflies import FirefliesHandle

    handle = FirefliesHandle(api_key="ff-secret")
    assert "ff-secret" not in repr(handle)
    # The framework backstop covers adapters whose handle is a bare token string.
    assert "hs-secret" not in repr(_session("hubspot", "hs-secret"))
    assert "xoxp-secret" not in repr(
        _session("slack", {"user_token": "xoxp-secret"})
    )
    from app.connector_lookup.zoom import ZoomHandle
    from app.connectors.zoom_oauth import ZoomContext

    zoom_handle = ZoomHandle(
        ctx=ZoomContext(company_id="co", access_token="zoom-secret",
                        user_ids=[], user_names={}),
    )
    assert "zoom-secret" not in repr(zoom_handle)
