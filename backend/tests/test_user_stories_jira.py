"""User stories v2 (full skill spec) + Jira push.

Covers the v2 generation contract — decision tickets from [ESCALATE],
traceability chain, dependency/[P]/walking-skeleton metadata, the
criteria_generated flag — and the Jira half of the push step: ADF rendering,
labels, error isolation, and the tracker-aware /v1/stories/push route.

All Jira HTTP and the LLM gateway are mocked.
"""
from __future__ import annotations

import time
from types import SimpleNamespace

import pytest

from app.stories.generate import Story
from app.stories.push import _story_labels, _story_to_adf, push_stories_to_jira

from tests._company_helpers import company_client, seed_connection


# ───────────────────────── v2 Story contract ──────────────────────────────────


def test_decision_ticket_is_forced_needs_human():
    s = Story.from_dict({
        "title": "Resolve metric-source connector contract",
        "body": "Decision needed: which connector owns the metric source.",
        "acceptance_criteria": [],
        "kind": "decision",
        "route": "agent-ready",  # model slip — must be overridden
        "owner": "eng lead",
        "blocks": ["Build the connector"],
    })
    assert s.kind == "decision"
    assert s.route == "needs-human"
    assert s.owner == "eng lead"
    assert s.blocks == ["Build the connector"]


def test_unknown_kind_falls_back_to_build():
    s = Story.from_dict({"title": "T", "body": "b", "kind": "epic"})
    assert s.kind == "build"


def test_v2_fields_round_trip_through_dict():
    s = Story.from_dict({
        "title": "T", "body": "b",
        "acceptance_criteria": ["Given a, When b, Then c."],
        "trace": "T4 -> R5,R7 -> acceptance tests -> PRD goal",
        "dependencies": ["Other story"],
        "parallel": True,
        "walking_skeleton": True,
        "criteria_generated": False,
    })
    d = s.to_dict()
    assert d["trace"].startswith("T4 ->")
    assert d["dependencies"] == ["Other story"]
    assert d["parallel"] is True
    assert d["walking_skeleton"] is True
    assert Story.from_dict(d).to_dict() == d


def test_description_carries_trace_and_skeleton():
    s = Story(
        title="T", body="As a PM, I want X, so that Y.",
        acceptance_criteria=["Given a, When b, Then c."],
        route="agent-ready",
        trace="T4 -> R5 -> tests -> PRD goal",
        dependencies=["Earlier story"],
        parallel=True,
        walking_skeleton=True,
    )
    desc = s.to_description()
    assert "Trace: T4 -> R5" in desc
    assert "Depends on: Earlier story" in desc
    assert "[P] parallel-safe" in desc
    assert "Walking skeleton" in desc


def test_prose_mode_flags_generated_criteria():
    s = Story(title="T", body="b", acceptance_criteria=["Given x."],
              criteria_generated=True)
    assert "generated from prose" in s.to_description()


def test_decision_ticket_description_names_owner_and_blocks():
    s = Story.from_dict({
        "title": "Decide auth provider", "body": "Decision needed.",
        "kind": "decision", "owner": "founder",
        "blocks": ["Build login", "Build signup"],
    })
    desc = s.to_description()
    assert "Decision owner: founder" in desc
    assert "Blocks: Build login, Build signup" in desc


# ───────────────────────── ADF rendering ──────────────────────────────────────


def _adf_text(node) -> str:
    if isinstance(node, dict):
        if node.get("type") == "text":
            return node.get("text", "")
        return "".join(_adf_text(c) for c in node.get("content", []))
    return ""


def test_story_to_adf_is_dual_layer():
    s = Story(
        title="T", body="As a PM, I want X, so that Y.",
        acceptance_criteria=["Given a, When b, Then c."],
        priority="high", route="agent-ready",
        trace="T1 -> R1 -> tests -> goal",
    )
    doc = _story_to_adf(s)
    assert doc["type"] == "doc" and doc["version"] == 1
    text = _adf_text(doc)
    assert "As a PM" in text
    assert "Acceptance criteria" in text
    assert "Given a, When b, Then c." in text
    assert "Trace: T1 -> R1" in text
    assert "Suggested priority: high" in text
    assert "Route: agent-ready" in text


def test_story_labels_carry_routing():
    s = Story.from_dict({
        "title": "T", "body": "b", "kind": "decision",
        "walking_skeleton": True,
    })
    labels = _story_labels(s)
    assert "sprntly" in labels
    assert "needs-human" in labels
    assert "decision-ticket" in labels
    assert "walking-skeleton" in labels
    # Jira rejects labels containing spaces.
    assert all(" " not in label for label in labels)


# ───────────────────────── Jira push ──────────────────────────────────────────


def _seed_jira(company_id):
    seed_connection(
        company_id=company_id, provider="jira",
        token_blob={
            "access_token": "jira-at", "refresh_token": "jira-rt",
            "expires_in": 3600, "obtained_at": int(time.time()),
            "cloud_id": "cloud-1", "site_url": "https://acme.atlassian.net",
        },
    )


def test_push_to_jira_error_isolation(isolated_settings, monkeypatch):
    ctx = company_client(monkeypatch)
    _seed_jira(ctx.company_id)

    from app.connectors import jira_oauth

    def _create_issue(token, cloud_id, *, project_id, issue_type_id, summary,
                      description_adf=None, labels=None, site_url=None):
        if summary == "boom":
            raise RuntimeError("jira 500")
        return {"id": "1", "key": f"ENG-{summary}",
                "url": f"{site_url}/browse/ENG-{summary}"}

    monkeypatch.setattr(jira_oauth, "create_issue", _create_issue)

    stories = [
        Story(title="ok1", body="b"),
        Story(title="boom", body="b"),
        Story(title="ok2", body="b"),
    ]
    result = push_stories_to_jira(ctx.company_id, "1", "10001", stories)
    assert [c["story"] for c in result["created"]] == ["ok1", "ok2"]
    assert result["created"][0]["issue_key"] == "ENG-ok1"
    assert result["created"][0]["url"].startswith("https://acme.atlassian.net")
    assert len(result["errors"]) == 1
    assert result["errors"][0]["story"] == "boom"


def test_push_to_jira_404_when_not_connected(isolated_settings, monkeypatch):
    from fastapi import HTTPException

    ctx = company_client(monkeypatch)
    with pytest.raises(HTTPException) as exc:
        push_stories_to_jira(ctx.company_id, "1", "10001",
                             [Story(title="x", body="b")])
    assert exc.value.status_code == 404


# ───────────────────────── /v1/stories routes ─────────────────────────────────


def test_push_route_jira_requires_target(isolated_settings, monkeypatch):
    ctx = company_client(monkeypatch)
    r = ctx.client.post("/v1/stories/push", json={
        "tracker": "jira",
        "stories": [{"title": "T", "body": "b"}],
    })
    assert r.status_code == 400
    assert "project_id" in r.json()["detail"]


def test_push_route_clickup_requires_list_id(isolated_settings, monkeypatch):
    ctx = company_client(monkeypatch)
    r = ctx.client.post("/v1/stories/push", json={
        "stories": [{"title": "T", "body": "b"}],
    })
    assert r.status_code == 400
    assert "list_id" in r.json()["detail"]


def test_push_route_rejects_unknown_tracker(isolated_settings, monkeypatch):
    ctx = company_client(monkeypatch)
    r = ctx.client.post("/v1/stories/push", json={
        "tracker": "linear",
        "stories": [{"title": "T", "body": "b"}],
    })
    assert r.status_code == 400


def test_push_route_jira_creates_issues(isolated_settings, monkeypatch):
    ctx = company_client(monkeypatch)
    _seed_jira(ctx.company_id)

    from app.connectors import jira_oauth

    seen: list[dict] = []

    def _create_issue(token, cloud_id, *, project_id, issue_type_id, summary,
                      description_adf=None, labels=None, site_url=None):
        seen.append({"summary": summary, "labels": labels,
                     "project_id": project_id})
        return {"id": "1", "key": "ENG-1",
                "url": "https://acme.atlassian.net/browse/ENG-1"}

    monkeypatch.setattr(jira_oauth, "create_issue", _create_issue)

    r = ctx.client.post("/v1/stories/push", json={
        "tracker": "jira",
        "project_id": "1",
        "issue_type_id": "10001",
        "stories": [{
            "title": "Decide auth provider", "body": "Decision needed.",
            "kind": "decision", "route": "agent-ready",  # must be overridden
        }],
    })
    assert r.status_code == 200, r.text
    assert r.json()["created"][0]["issue_key"] == "ENG-1"
    # Route invariant survived the HTTP round-trip: decision => needs-human.
    assert "needs-human" in seen[0]["labels"]


def test_jira_projects_route_returns_picker(isolated_settings, monkeypatch):
    ctx = company_client(monkeypatch)
    _seed_jira(ctx.company_id)

    from app.connectors import jira_oauth
    monkeypatch.setattr(
        jira_oauth, "list_projects",
        lambda token, cloud_id: [{"id": "1", "key": "ENG",
                                  "name": "Engineering"}],
    )
    r = ctx.client.post("/v1/stories/jira/projects")
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["projects"][0]["key"] == "ENG"
    assert body["site_url"] == "https://acme.atlassian.net"


def test_jira_issue_types_route(isolated_settings, monkeypatch):
    ctx = company_client(monkeypatch)
    _seed_jira(ctx.company_id)

    from app.connectors import jira_oauth
    monkeypatch.setattr(
        jira_oauth, "list_issue_types",
        lambda token, cloud_id, project_id: [{"id": "10001", "name": "Story",
                                              "subtask": False}],
    )
    r = ctx.client.post("/v1/stories/jira/issue-types",
                        json={"project_id": "1"})
    assert r.status_code == 200, r.text
    assert r.json()["issue_types"][0]["name"] == "Story"
