"""Tests for the Jira Personal Data Reporting (GDPR) compliance module.

All Atlassian HTTP + DB access is mocked; these exercise the collect → report →
act (erase/refresh) logic and the batching/status handling.
"""
from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from app.connectors import jira_personal_data as pd


def _row(account_id, *, company_id="co-1", status="active", created_at="2026-07-01T00:00:00+00:00",
         user_fetched_at=None, cloud_id="cloud-1"):
    user = {"accountId": account_id, "emailAddress": f"{account_id}@x.co", "displayName": account_id}
    cfg = {"cloud_id": cloud_id, "user": user}
    if user_fetched_at:
        cfg["user_fetched_at"] = user_fetched_at
    return {
        "company_id": company_id, "provider": "jira", "status": status,
        "created_at": created_at, "config": cfg, "token_json_encrypted": "enc",
    }


# ── collect_reportable_accounts ──────────────────────────────────────────────


def test_collect_returns_accounts_with_updatedAt(monkeypatch):
    monkeypatch.setattr(pd, "_jira_rows", lambda: [
        _row("acc-a", created_at="2026-07-01T00:00:00+00:00"),
        _row("acc-b", user_fetched_at="2026-07-05T12:00:00+00:00"),
    ])
    out = pd.collect_reportable_accounts()
    by_id = {a["accountId"]: a for a in out}
    assert set(by_id) == {"acc-a", "acc-b"}
    assert by_id["acc-a"]["updatedAt"] == "2026-07-01T00:00:00+00:00"
    # user_fetched_at wins over created_at when present.
    assert by_id["acc-b"]["updatedAt"] == "2026-07-05T12:00:00+00:00"


def test_collect_dedupes_same_account_across_companies(monkeypatch):
    monkeypatch.setattr(pd, "_jira_rows", lambda: [
        _row("acc-a", company_id="co-1"),
        _row("acc-a", company_id="co-2"),
    ])
    out = pd.collect_reportable_accounts()
    assert [a["accountId"] for a in out] == ["acc-a"]


def test_collect_skips_rows_without_accountId(monkeypatch):
    bad = {"company_id": "c", "provider": "jira", "config": {"cloud_id": "x"},
           "created_at": "2026-07-01T00:00:00+00:00"}
    monkeypatch.setattr(pd, "_jira_rows", lambda: [bad, _row("acc-a")])
    assert [a["accountId"] for a in pd.collect_reportable_accounts()] == ["acc-a"]


# ── report_batch ─────────────────────────────────────────────────────────────


def _resp(status, json_body=None, headers=None):
    m = MagicMock()
    m.status_code = status
    m.json.return_value = json_body or {}
    m.headers = headers or {}
    m.text = ""
    return m


def test_report_batch_204_no_action(monkeypatch):
    with patch.object(pd.requests, "post", return_value=_resp(204)) as mp:
        actions = pd.report_batch("tok", [{"accountId": "a", "updatedAt": "2026-07-01T00:00:00+00:00"}])
    assert actions == []
    args = mp.call_args
    assert args.args[0] == pd.REPORT_URL
    assert args.kwargs["headers"]["Authorization"] == "Bearer tok"
    assert args.kwargs["json"]["accounts"] == [{"accountId": "a", "updatedAt": "2026-07-01T00:00:00+00:00"}]


def test_report_batch_200_returns_actions(monkeypatch):
    body = {"accounts": [{"accountId": "a", "status": "closed"}]}
    with patch.object(pd.requests, "post", return_value=_resp(200, body)):
        actions = pd.report_batch("tok", [{"accountId": "a", "updatedAt": "t"}])
    assert actions == [{"accountId": "a", "status": "closed"}]


def test_report_batch_caps_at_90(monkeypatch):
    accts = [{"accountId": f"a{i}", "updatedAt": "t"} for i in range(200)]
    with patch.object(pd.requests, "post", return_value=_resp(204)) as mp:
        pd.report_batch("tok", accts)
    assert len(mp.call_args.kwargs["json"]["accounts"]) == 90


def test_report_batch_429_returns_empty(monkeypatch):
    with patch.object(pd.requests, "post", return_value=_resp(429, headers={"Retry-After": "30"})):
        assert pd.report_batch("tok", [{"accountId": "a", "updatedAt": "t"}]) == []


# ── erase_account ────────────────────────────────────────────────────────────


def test_erase_deletes_all_matching_connections(monkeypatch):
    monkeypatch.setattr(pd, "_jira_rows", lambda: [
        _row("acc-a", company_id="co-1"),
        _row("acc-a", company_id="co-2"),
        _row("acc-b", company_id="co-3"),
    ])
    deleted = []
    monkeypatch.setattr(pd.db, "delete_connection",
                        lambda cid, prov: deleted.append((cid, prov)) or True)
    n = pd.erase_account("acc-a")
    assert n == 2
    assert deleted == [("co-1", "jira"), ("co-2", "jira")]


# ── run_report_cycle orchestration ───────────────────────────────────────────


def test_cycle_skips_when_no_accounts(monkeypatch):
    monkeypatch.setattr(pd, "collect_reportable_accounts", lambda: [])
    out = pd.run_report_cycle()
    assert out["reported"] == 0 and "no accounts" in out["skipped"]


def test_cycle_skips_when_no_token(monkeypatch):
    monkeypatch.setattr(pd, "collect_reportable_accounts",
                        lambda: [{"accountId": "a", "updatedAt": "t"}])
    monkeypatch.setattr(pd, "_app_bearer_token", lambda: None)
    out = pd.run_report_cycle()
    assert "no active jira" in out["skipped"]


def test_cycle_erases_closed_and_refreshes_updated(monkeypatch):
    monkeypatch.setattr(pd, "collect_reportable_accounts", lambda: [
        {"accountId": "closed-1", "updatedAt": "t"},
        {"accountId": "updated-1", "updatedAt": "t"},
        {"accountId": "ok-1", "updatedAt": "t"},
    ])
    monkeypatch.setattr(pd, "_app_bearer_token", lambda: "tok")
    monkeypatch.setattr(pd, "report_batch", lambda tok, batch: [
        {"accountId": "closed-1", "status": "closed"},
        {"accountId": "updated-1", "status": "updated"},
    ])
    erased, refreshed = [], []
    monkeypatch.setattr(pd, "erase_account", lambda a: erased.append(a) or 1)
    monkeypatch.setattr(pd, "refresh_account", lambda a: refreshed.append(a) or 1)
    out = pd.run_report_cycle()
    assert out["reported"] == 3 and out["batches"] == 1
    assert out["closed"] == 1 and out["erased"] == 1 and out["updated"] == 1
    assert erased == ["closed-1"] and refreshed == ["updated-1"]
