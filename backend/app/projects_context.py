"""The workspace's PROJECTS — what they are, and which ones the caller has.

Asked for by the planner (`ask_planner.Plan.include_projects`) and executed on
the answer path. Before it, the chat did not know the feature existed: the word
"project" appears nowhere in `ASK_SYSTEM`, no block listed one, and the planner
had no action that could make one — so "what projects do I have" was answered
out of the knowledge graph, where the nearest thing to a project is a Jira
board, and "what is a project" got a generic definition of the English word.

TWO HALVES, and the concept half is the one that was missing: the block opens
by saying what a project IS in this product (a shared container for a topic —
its artifacts, its members, its group chat, its own memory), then lists the
caller's own. A model that can list them but cannot explain them answers the
second question badly, and "what even is this" is the first thing a new user
asks.

SCOPED THREE WAYS, and all three matter. Company, workspace (projects are
per-workspace, and one company can hold several), and MEMBERSHIP: a project
the caller has not been added to must not leak its name into this list, the
same rule `db.projects.list_projects_for_workspace` enforces for the Projects
screen and the route layer enforces with a 403. The workspace and the caller
ride request-scoped ContextVars (`ask_runner.active_workspace_id` /
`active_user_id`) rather than parameters, for the reason `set_active_project_id`
records: threading them through `answer()` is the qa_agent.py edit that
mechanism exists to avoid.

Never raises, and returns "" when it cannot scope itself or the read fails —
"you have no projects" said because a ContextVar was unset would be a confident
lie about the user's own workspace. A caller who genuinely belongs to none is a
real state and does render, because "you have none yet, here is what they are
for" is a true and useful answer.
"""
from __future__ import annotations

import logging
from typing import Optional

logger = logging.getLogger(__name__)

# The whole list reaches the prompt up to this bound. Projects are coarse — a
# workspace has a handful, not hundreds — so this is a runaway guard rather
# than a real ceiling, and a truncation is DECLARED (see below) rather than
# silently presented as the complete list.
_MAX_PROJECTS = 60

_PROJECTS_SCREEN = "Projects"

# `project_artifacts.artifact_type` → the words a person uses. Same mapping
# job as `library_context._KIND_LABELS`: nobody says "impl_spec" out loud.
_ARTIFACT_LABELS: dict[str, str] = {
    "prd": "PRD",
    "evidence": "evidence report",
    "prototype": "prototype",
    "report": "report",
    "ticket_set": "ticket set",
    "custom_artifact": "document",
}


def _artifacts_phrase(counts: dict) -> str:
    """"2 PRDs, 1 prototype" — or "no artifacts yet", which is a real state a
    project spends its first hour in."""
    if not counts:
        return "no artifacts yet"
    parts = []
    for kind, n in sorted(counts.items(), key=lambda kv: (-kv[1], kv[0])):
        label = _ARTIFACT_LABELS.get(kind, kind)
        parts.append(f"{n} {label}{'s' if n != 1 else ''}")
    return ", ".join(parts)


def _project_line(p: dict) -> str:
    name = (p.get("name") or "").strip() or "(untitled project)"
    members = p.get("member_count") or 0
    memory = p.get("memory_count") or 0
    chat = "group chat started" if p.get("has_group_chat") else "no group chat yet"
    return (
        f"- {name} — {members} member{'s' if members != 1 else ''} — "
        f"{_artifacts_phrase(p.get('artifact_counts') or {})} — {chat} — "
        f"{memory} memory note{'s' if memory != 1 else ''} — project id: {p.get('id')}"
    )


#: What a project IS. Kept as its own constant because it is the half of this
#: block that does not depend on a read succeeding — a workspace with no
#: projects still gets the explanation, which is exactly what someone asking
#: "what is a project" needs.
_WHAT_A_PROJECT_IS = (
    "A PROJECT in Sprntly is a shared container for one topic or piece of "
    "work. It gathers that topic's artifacts (PRDs, evidence, prototypes, "
    "reports, ticket sets) in one place, has its own members, its own group "
    "chat where the team and Sprntly talk together, a private chat per member, "
    "and its own memory — notes and decisions that persist across every "
    "conversation held inside it. It is NOT a Jira project, a Confluence "
    f"space, or anything in a connected tool. Projects live on the "
    f"{_PROJECTS_SCREEN} screen, and one can be created from this chat by "
    "asking for it."
)


def projects_block(company_id: Optional[str]) -> str:
    """This caller's projects in this workspace, as a context section."""
    if not company_id:
        return ""
    try:
        from app.ask_runner import active_user_id, active_workspace_id
        from app.db import projects as projects_db

        workspace_id, user_id = active_workspace_id(), active_user_id()
        if not workspace_id or not user_id:
            # Nothing set the request scope (a caller outside the ask worker,
            # or an older path). Rendering the explanation alone would be
            # honest but would strand the model with "here is what a project
            # is" and no way to say which exist — worse than the answer path
            # this block is absent from.
            logger.info(
                "projects block: no workspace/user in scope for %s", company_id
            )
            return ""
        projects = projects_db.list_projects_for_workspace(
            company_id, workspace_id, user_id
        ) or []
    except Exception:  # noqa: BLE001 — an unreadable list degrades, never lies
        logger.exception("projects block: read failed for %s", company_id)
        return ""

    shown, dropped = projects[:_MAX_PROJECTS], max(0, len(projects) - _MAX_PROJECTS)
    parts = [
        "=== THIS WORKSPACE'S PROJECTS ===",
        _WHAT_A_PROJECT_IS,
        "",
        "The list below is the complete set of projects THIS USER is a member "
        "of, in their active workspace, read just now. Membership is access: a "
        "project they have not been added to is deliberately not here, so "
        "never suggest the list may be incomplete for that reason — and never "
        "name a project that does not appear below.",
        "",
        f"PROJECTS ({len(projects)}), most recently updated first.",
    ]
    parts.extend([_project_line(p) for p in shown] or [
        "(None yet — this user is not a member of any project in this "
        "workspace. That is a normal state for a new workspace: say so, say "
        "what a project is for, and offer to create one.)"
    ])
    if dropped:
        parts.append(
            f"(+{dropped} more not shown — say the list was truncated at "
            f"{_MAX_PROJECTS} rather than presenting it as complete.)"
        )
    return "\n".join(parts)
