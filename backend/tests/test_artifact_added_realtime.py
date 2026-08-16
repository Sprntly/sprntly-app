"""`db/projects.py::add_artifact`'s best-effort `artifact.added` realtime
emit — the #9-count artifact-invalidation fix (AC10).

`add_artifact` is the SINGLE write chokepoint for every project artifact
attach (the client route, `execute_task`, a report capture) — this is the
one place a "the artifacts list changed" signal needs to fire so
`ProjectDetailScreen`'s group-channel subscription can refetch. Spies
`publish_broadcast` (patched on `app.realtime`, the module `add_artifact`
locally imports and calls through) so no real Realtime traffic is made.
"""
from __future__ import annotations

from app import realtime
from tests._company_helpers import company_client


def _create_project(ctx, *, name: str = "Artifact realtime project") -> dict:
    return ctx.client.post("/v1/projects", json={"name": name}).json()


def test_add_artifact_emits_artifact_added(isolated_settings, monkeypatch):
    ctx = company_client(monkeypatch)
    project = _create_project(ctx)

    calls = []
    monkeypatch.setattr(
        realtime, "publish_broadcast",
        lambda topic, event, payload: calls.append((topic, event, payload)),
    )

    from app.db import projects as projects_db

    projects_db.add_artifact(project["id"], "prd", 42)

    assert len(calls) == 1
    topic, event, payload = calls[0]
    assert topic == f"project:{project['id']}"
    assert event == "artifact.added"
    assert payload == {"project_id": project["id"], "artifact_type": "prd", "artifact_id": 42}


def test_add_artifact_survives_publish_failure(isolated_settings, monkeypatch):
    ctx = company_client(monkeypatch)
    project = _create_project(ctx)

    def _boom(topic, event, payload):
        raise RuntimeError("realtime endpoint unreachable")

    monkeypatch.setattr(realtime, "publish_broadcast", _boom)

    from app.db import projects as projects_db

    # Must NOT raise — the artifact write is the correctness requirement;
    # the broadcast is a best-effort nicety (AD-P7).
    ref = projects_db.add_artifact(project["id"], "prd", 43)
    assert ref["artifact_id"] == 43

    refs = projects_db.list_project_artifact_refs(project["id"])
    assert {"artifact_type": "prd", "artifact_id": 43} in [
        {"artifact_type": r["artifact_type"], "artifact_id": r["artifact_id"]} for r in refs
    ]
