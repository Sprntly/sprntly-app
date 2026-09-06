"""Defense-in-depth blank-content guard on the two `turn.created` publish
sites that are NOT `routes/conversations.py::add_turn` (covered by
`test_routes_conversation_turn_realtime.py::test_add_turn_never_publishes_whitespace_only_content`):

- `routes/projects.py::_publish_turn_created` — the PRD-chat-edit path's own
  publish-on-write.
- `context_assembler_project.py`'s `_post_turn` closure (`SurfaceScope.post_turn`)
  — the `execute_task` tool's progress-post callback, exercised the same way
  `test_surface_scope.py`'s own `post_turn` coverage does.

Both mirror the client's own `parseRealtimeTurnPayload` blank-content guard
(`web/.../projects/useProjectConversation.ts`): a blank-content row is still
persisted by the caller (unchanged), but never published, so a stray blank
write can never render a phantom bubble on the receiving thread.
"""
from __future__ import annotations

import app.routes.projects as projects_route


def test_publish_turn_created_skips_blank_content(monkeypatch):
    published: list[tuple[str, str, dict]] = []
    monkeypatch.setattr(
        projects_route, "publish_broadcast",
        lambda topic, event, payload: published.append((topic, event, payload)),
    )

    projects_route._publish_turn_created(
        1, "user-1", {"id": 1, "role": "assistant", "content": "   ", "created_at": "now"},
    )

    assert published == []


def test_publish_turn_created_still_publishes_real_content(monkeypatch):
    published: list[tuple[str, str, dict]] = []
    monkeypatch.setattr(
        projects_route, "publish_broadcast",
        lambda topic, event, payload: published.append((topic, event, payload)),
    )

    projects_route._publish_turn_created(
        1, "user-1", {"id": 1, "role": "assistant", "content": "Done.", "created_at": "now"},
    )

    assert len(published) == 1
    topic, event, payload = published[0]
    assert topic == "project:1:user:user-1"
    assert event == "turn.created"
    assert payload["content"] == "Done."


def test_context_assembler_project_post_turn_skips_blank_content(monkeypatch):
    """Same closure `test_surface_scope.py`'s `post_turn` tests exercise —
    a blank-content generate (e.g. an interrupted/empty PRD-draft outcome)
    must still WRITE the row (unchanged) but never publish it."""
    from app.context_assembler import AssembleRequest
    from app.context_assembler_project import ProjectContextAssembler
    from app.db import projects as projects_db
    import app.db.conversations as conversations_db
    import app.realtime as realtime_mod

    monkeypatch.setattr(projects_db, "project_belongs_to_company", lambda *a, **k: True)
    monkeypatch.setattr(projects_db, "is_project_member", lambda *a, **k: True)
    monkeypatch.setattr(
        conversations_db, "post_individual_turn",
        lambda conversation_id, role, content: {
            "id": 503, "role": role, "content": content,
            "created_at": "2026-09-02T00:00:00Z",
        },
    )
    monkeypatch.setattr(
        conversations_db, "get_individual_conversation_owner", lambda conversation_id: "owner-uid-1",
    )
    published: list[tuple[str, str, dict]] = []
    monkeypatch.setattr(
        realtime_mod, "publish_broadcast",
        lambda topic, event, payload: published.append((topic, event, payload)),
    )

    req = AssembleRequest(
        user_id="u1", company_id="c1", dataset="", conversation_id=777,
        question="q", workspace_id="w1",
        params={"project_id": 42, "surface": "private"},
    )
    scope = ProjectContextAssembler().assemble(req)
    assert scope.post_turn is not None

    turn = scope.post_turn("   ")
    assert turn["content"] == "   ", "the row is still written, unchanged"
    assert published == [], "a blank-content turn must never publish"
