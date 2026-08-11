"""Projects — the shared container that gathers a topic's artifacts plus
the collaboration layer on top of them (group chat, per-user chats,
project-scoped memory).

Tenancy follows the workspace-scoped pattern (`WorkspaceContext` /
`require_workspace`, `backend/app/routes/ask.py`), NOT the dataset-slug
pattern (`require_owned_dataset`): `projects.company_id` +
`projects.workspace_id` are both `NOT NULL` UUID columns, and every read
here is filtered by both rather than by a `dataset` query param.

`project_belongs_to_company` mirrors
`app.db.conversations.conversation_belongs_to_company`: a project id whose
tenancy doesn't match the caller's context must read back as "doesn't
exist" (404), never "exists but is someone else's" (403) — the same
existence-non-disclosure rule every other cross-tenant id lookup in this
codebase follows.

The virtual "Sprntly" agent member (AD-P6 in the build spec) is rendered
from a constant at the route layer, not stored here — `list_members` only
ever returns real `project_members` rows joined to `profiles`.
"""
from __future__ import annotations

from app.db.client import require_client, retry_on_disconnect, utc_now


@retry_on_disconnect
def create_project(
    *,
    company_id: str,
    workspace_id: str,
    name: str,
    created_by: str,
    origin: str = "manual",
) -> dict:
    """Insert a new `projects` row scoped to `(company_id, workspace_id)`
    and add the creator as its first `project_members` row. Returns the
    created project."""
    client = require_client()
    row = (
        client.table("projects")
        .insert(
            {
                "company_id": company_id,
                "workspace_id": workspace_id,
                "name": name,
                "origin": origin or "manual",
                "created_by": created_by,
            }
        )
        .execute()
        .data[0]
    )
    client.table("project_members").insert(
        {"project_id": row["id"], "user_id": created_by}
    ).execute()
    return row


@retry_on_disconnect
def list_projects_for_workspace(company_id: str, workspace_id: str, user_id: str) -> list[dict]:
    """Projects in the caller's active workspace that the caller is a
    MEMBER of (membership = access, AD-P11) — a workspace project the
    caller hasn't been added to must not leak its name/existence into
    this list, same principle as the per-project 403 in the route layer.
    Recency-ordered (`updated_at desc`), each annotated with the counts
    the project card needs — derived at read time from the join tables,
    never stored (`[[feedback_prefer-inference-over-stored-derived-state]]`)."""
    client = require_client()
    caller_membership = (
        client.table("project_members")
        .select("project_id")
        .eq("user_id", user_id)
        .execute()
        .data
        or []
    )
    caller_project_ids = {row["project_id"] for row in caller_membership}
    if not caller_project_ids:
        return []

    projects = (
        client.table("projects")
        .select("*")
        .eq("company_id", company_id)
        .eq("workspace_id", workspace_id)
        .in_("id", list(caller_project_ids))
        .order("updated_at", desc=True)
        .execute()
        .data
        or []
    )
    if not projects:
        return []

    project_ids = [p["id"] for p in projects]

    artifact_rows = (
        client.table("project_artifacts")
        .select("project_id, artifact_type")
        .in_("project_id", project_ids)
        .execute()
        .data
        or []
    )
    artifact_counts: dict[int, dict[str, int]] = {}
    for row in artifact_rows:
        by_type = artifact_counts.setdefault(row["project_id"], {})
        by_type[row["artifact_type"]] = by_type.get(row["artifact_type"], 0) + 1

    member_rows = (
        client.table("project_members")
        .select("project_id, user_id")
        .in_("project_id", project_ids)
        .execute()
        .data
        or []
    )
    member_counts: dict[int, int] = {}
    for row in member_rows:
        member_counts[row["project_id"]] = member_counts.get(row["project_id"], 0) + 1

    group_chat_rows = (
        client.table("conversations")
        .select("id, project_id")
        .in_("project_id", project_ids)
        .eq("kind", "group")
        .execute()
        .data
        or []
    )
    has_group_chat = {row["project_id"] for row in group_chat_rows}

    memory_rows = (
        client.table("project_memory_entries")
        .select("id, project_id")
        .in_("project_id", project_ids)
        .execute()
        .data
        or []
    )
    memory_counts: dict[int, int] = {}
    for row in memory_rows:
        memory_counts[row["project_id"]] = memory_counts.get(row["project_id"], 0) + 1

    out = []
    for p in projects:
        pid = p["id"]
        out.append(
            {
                **p,
                "artifact_counts": artifact_counts.get(pid, {}),
                "member_count": member_counts.get(pid, 0),
                "has_group_chat": pid in has_group_chat,
                "memory_count": memory_counts.get(pid, 0),
            }
        )
    return out


@retry_on_disconnect
def get_project(project_id: int) -> dict | None:
    """Raw `projects` row by id, or None. Callers MUST additionally check
    `project_belongs_to_company` before trusting the tenancy of a
    client-supplied id — this alone does not scope by caller."""
    rows = (
        require_client()
        .table("projects")
        .select("*")
        .eq("id", project_id)
        .limit(1)
        .execute()
        .data
        or []
    )
    return rows[0] if rows else None


def project_belongs_to_company(project_id: int, company_id: str, workspace_id: str) -> bool:
    """Whether this project exists AND belongs to this
    `(company_id, workspace_id)`. Mirrors
    `conversations.conversation_belongs_to_company`: callers turn False
    into 404, never 403 — "exists but not yours" and "doesn't exist" must
    be indistinguishable to the caller."""
    rows = (
        require_client()
        .table("projects")
        .select("id")
        .eq("id", project_id)
        .eq("company_id", company_id)
        .eq("workspace_id", workspace_id)
        .limit(1)
        .execute()
        .data
        or []
    )
    return bool(rows)


@retry_on_disconnect
def is_project_member(project_id: int, user_id: str) -> bool:
    """Whether `user_id` is a (human) member of this project. Membership
    is access (AD-P11, v1 all-or-nothing) — used to gate
    `add_member` so only an existing member can grow the roster."""
    rows = (
        require_client()
        .table("project_members")
        .select("project_id")
        .eq("project_id", project_id)
        .eq("user_id", user_id)
        .limit(1)
        .execute()
        .data
        or []
    )
    return bool(rows)


@retry_on_disconnect
def add_member(project_id: int, user_id: str) -> dict:
    """Add `user_id` to `project_members` and touch the project's
    `updated_at` (every project mutation touches it, per the recency
    ordering `list_projects_for_workspace` relies on). Idempotent: adding
    an existing member is a no-op upsert, never a duplicate-key error."""
    client = require_client()
    row = (
        client.table("project_members")
        .upsert(
            {"project_id": project_id, "user_id": user_id},
            on_conflict="project_id,user_id",
        )
        .execute()
        .data
    )
    client.table("projects").update({"updated_at": utc_now()}).eq("id", project_id).execute()
    return row[0] if row else {"project_id": project_id, "user_id": user_id}


@retry_on_disconnect
def remove_member(project_id: int, target_user_id: str) -> bool:
    """Delete `target_user_id`'s `project_members` row for this project and
    touch the project's `updated_at` (mirrors `add_member`'s mutation
    contract — every project mutation touches it, per the recency ordering
    `list_projects_for_workspace` relies on). Returns whether a row was
    actually removed: False when the target wasn't a member, which the
    route turns into 404 (same not-found posture as `delete_memory`).

    Callers (the route) are responsible for the creator-can't-be-removed
    and no-self-removal guards BEFORE calling this — this helper only
    performs the delete once those checks have passed."""
    client = require_client()
    resp = (
        client.table("project_members")
        .delete()
        .eq("project_id", project_id)
        .eq("user_id", target_user_id)
        .execute()
    )
    # Mirrors `delete_entry`'s existence check (`project_memory_entries.py`):
    # real PostgREST reports `count`, the fake test client reports `data` —
    # prefer whichever is populated.
    removed = bool(resp.count) if resp.count is not None else bool(resp.data)
    if removed:
        client.table("projects").update({"updated_at": utc_now()}).eq("id", project_id).execute()
    return removed


@retry_on_disconnect
def list_members(project_id: int) -> list[dict]:
    """Human members of this project, enriched with profile display data
    (mirrors `app.db.team.list_company_members`'s profile-join shape).
    Each row carries `user_id, kind='human', name, avatar_url, job_role,
    added_at`. Does NOT include the virtual agent member — the route
    layer prepends that constant (AD-P6)."""
    client = require_client()
    members = (
        client.table("project_members")
        .select("project_id, user_id, added_at")
        .eq("project_id", project_id)
        .execute()
        .data
        or []
    )
    if not members:
        return []

    user_ids = [m["user_id"] for m in members]
    profiles_resp = (
        client.table("profiles")
        .select("id, email, full_name, first_name, last_name, avatar_url, role")
        .in_("id", user_ids)
        .execute()
    )
    by_id = {p["id"]: p for p in (profiles_resp.data or [])}

    out = []
    for m in members:
        prof = by_id.get(m["user_id"]) or {}
        full = (prof.get("full_name") or "").strip()
        first = (prof.get("first_name") or "").strip()
        last = (prof.get("last_name") or "").strip()
        name = full or (f"{first} {last}".strip() if (first or last) else None) or None
        out.append(
            {
                "user_id": m["user_id"],
                "kind": "human",
                "name": name,
                "email": prof.get("email"),
                "avatar_url": prof.get("avatar_url"),
                "job_role": prof.get("role"),
                "added_at": m.get("added_at"),
            }
        )
    return out


@retry_on_disconnect
def get_group_chat_id(project_id: int) -> int | None:
    """The project's single group-chat `conversations.id`, or None if it
    hasn't been created yet (a separate group-chat surface creates it —
    this module only reads). Exactly one `kind='group'` row per project
    is enforced in the schema by a partial unique index."""
    rows = (
        require_client()
        .table("conversations")
        .select("id")
        .eq("project_id", project_id)
        .eq("kind", "group")
        .limit(1)
        .execute()
        .data
        or []
    )
    return rows[0]["id"] if rows else None


@retry_on_disconnect
def add_artifact(project_id: int, artifact_type: str, artifact_id: int) -> dict:
    """Upsert a `(project_id, artifact_type, artifact_id)` ref into
    `project_artifacts` (the PK dedupes a repeat add into a no-op, same
    posture as `add_member`) and touch the project's `updated_at`.

    Write-time access validation — that the caller actually reaches this
    artifact (AD-P12, the IDOR guard) — is the ROUTE's job, before this is
    ever called; this helper only writes the ref once that gate has
    passed."""
    client = require_client()
    row = (
        client.table("project_artifacts")
        .upsert(
            {
                "project_id": project_id,
                "artifact_type": artifact_type,
                "artifact_id": artifact_id,
            },
            on_conflict="project_id,artifact_type,artifact_id",
        )
        .execute()
        .data
    )
    client.table("projects").update({"updated_at": utc_now()}).eq("id", project_id).execute()
    return (
        row[0]
        if row
        else {
            "project_id": project_id,
            "artifact_type": artifact_type,
            "artifact_id": artifact_id,
        }
    )


@retry_on_disconnect
def list_project_artifact_refs(project_id: int) -> list[dict]:
    """Raw `project_artifacts` refs for this project — unresolved
    `{artifact_type, artifact_id}` pairs. `list_artifacts_for_project`
    (`db/artifacts.py`) is what turns these into full artifact rows via the
    existing five-table fan-out; this helper only reads the join table."""
    return (
        require_client()
        .table("project_artifacts")
        .select("artifact_type, artifact_id")
        .eq("project_id", project_id)
        .execute()
        .data
        or []
    )


@retry_on_disconnect
def user_id_for_email(email: str) -> str | None:
    """Resolve an existing user's id from their profile email
    (case-insensitive, mirrors `app.db.team.member_exists_for_email`).
    None when no account exists for that email — inviting a non-user by
    email is `org_invites`-based and out of scope here (fast-follow)."""
    needle = (email or "").strip().lower()
    if not needle:
        return None
    rows = (
        require_client()
        .table("profiles")
        .select("id")
        .ilike("email", _escape_like(needle))
        .limit(1)
        .execute()
        .data
        or []
    )
    return rows[0]["id"] if rows else None


def _escape_like(value: str) -> str:
    """Escape LIKE/ILIKE metacharacters so a pattern matches the value
    literally (emails routinely contain `_`, a single-char wildcard)."""
    return value.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
