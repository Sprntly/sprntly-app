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

import re

from app.db.client import require_client, retry_on_disconnect, utc_now
from app.db.team import email_belongs_to_other_company, get_member, list_company_members
from app.db.workspaces import get_workspace_member, list_workspace_members


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
def get_instructions(project_id: int) -> str | None:
    """The project's saved free-text instructions for the Sprntly agent, or
    None when nothing has been set. Single-column read — the hot-path caller
    is scope assembly on every project-private/group agent turn."""
    rows = (
        require_client()
        .table("projects")
        .select("instructions")
        .eq("id", project_id)
        .limit(1)
        .execute()
        .data
        or []
    )
    return rows[0].get("instructions") if rows else None


@retry_on_disconnect
def set_instructions(project_id: int, instructions: str | None) -> None:
    """Persist the project's instructions and touch `updated_at` (mirrors
    `add_member`'s update+touch pattern). Empty/whitespace-only normalizes
    to `None` (clearing) rather than storing an empty string, so `get_
    instructions` and the folded-block builder never have to distinguish
    "" from unset."""
    normalized = (instructions or "").strip() or None
    client = require_client()
    client.table("projects").update(
        {"instructions": normalized, "updated_at": utc_now()}
    ).eq("id", project_id).execute()


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
def count_project_members(project_id: int) -> int:
    """Human member count for the solo check — NO `profiles` join, so a
    profile-enrichment hiccup can never downgrade solo-ness. `list_members`'
    join is for display; this is for the decision. Raises on read failure;
    `_is_solo_project` owns the fail-open."""
    client = require_client()
    rows = (
        client.table("project_members")
        .select("user_id")
        .eq("project_id", project_id)
        .execute()
        .data
    ) or []
    return len(rows)


def _match_keys(member: dict) -> set[str]:
    """The casefolded set of strings this member matches on: full name,
    the first whitespace token of name (so "Fortune" matches "Fortune
    Adeyemi"), and job_role. A member with `name`/`job_role` NULL
    contributes only its non-null keys."""
    keys: set[str] = set()
    name = (member.get("name") or "").strip()
    if name:
        keys.add(name.casefold())
        first_token = name.split()[0]
        keys.add(first_token.casefold())
    job_role = (member.get("job_role") or "").strip()
    if job_role:
        keys.add(job_role.casefold())
    return keys


def resolve_member(project_id: int, needle: str) -> dict:
    """Resolve a free-text assignee reference to exactly one project member.
    Roster-constrained: candidates come ONLY from list_members(project_id),
    so a non-member / cross-project / cross-tenant user can never be
    returned. Deterministic — NO LLM call (AD-P18 fast-path). Fail-closed
    on no-match / ambiguity.

    Returns one of:
      {"status": "resolved",  "member": {<list_members row>}}
      {"status": "no_match",  "roster": [<members>]}      # caller asks who they mean
      {"status": "ambiguous", "candidates": [<members>]}  # caller asks which one
    """
    roster = list_members(project_id)

    raw = needle.strip()
    if raw.startswith("@"):
        raw = raw[1:]
    n = raw.casefold()

    if not n:
        return {"status": "no_match", "roster": roster}

    exact = [m for m in roster if n in _match_keys(m)]
    if len(exact) == 1:
        candidates = exact
    elif len(exact) > 1:
        return {"status": "ambiguous", "candidates": exact}
    else:
        # Prefix tier only fires when the exact tier found nothing, and
        # only for needles of length >= 2 (a single character is too
        # broad to prefix-match safely).
        if len(n) < 2:
            return {"status": "no_match", "roster": roster}
        prefix = [m for m in roster if any(k.startswith(n) for k in _match_keys(m))]
        if len(prefix) == 0:
            return {"status": "no_match", "roster": roster}
        if len(prefix) > 1:
            return {"status": "ambiguous", "candidates": prefix}
        candidates = prefix

    member = candidates[0]
    # Fail-closed membership re-check (AD-P18): even though the candidate
    # came from list_members(project_id), re-assert membership before
    # returning `resolved` — never trust a match set as a substitute for
    # the live authz check on the exact id about to be handed back.
    if not is_project_member(project_id, member["user_id"]):
        return {"status": "no_match", "roster": roster}
    return {"status": "resolved", "member": member}


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


# ─────────────────── resolve_candidate (tag-non-members) ───────────────────

TIER_MEMBER, TIER_WORKSPACE, TIER_COMPANY, TIER_NEWUSER, TIER_REFUSE = (
    "t_member",
    "t_workspace",
    "t_company",
    "t_newuser",
    "t_refuse",
)

# needle is an EMAIL -> the invite path may reach OUTSIDE the tenant
#                        (t_newuser/t_refuse are reachable).
# needle is a NAME   -> resolution is tenant-only (no cross-tenant directory
#                        read); a name with no in-tenant match refuses
#                        (`no_match`), NEVER an invite — you cannot invite an
#                        outsider you only know by name.
_EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


def _directory_match_keys(entry: dict) -> set[str]:
    """Casefolded match keys for an enriched workspace/company-directory row
    (`list_workspace_members`/`list_company_members` shape: `display_name` +
    optional `job_role`). Mirrors `_match_keys`'s discipline for the
    `list_members` row shape (`name`) above, widened to the directory
    enrichment's field names."""
    keys: set[str] = set()
    name = (entry.get("display_name") or "").strip()
    if name:
        keys.add(name.casefold())
        keys.add(name.split()[0].casefold())
    job_role = (entry.get("job_role") or "").strip()
    if job_role:
        keys.add(job_role.casefold())
    return keys


def _match_directory(entries: list[dict], n: str) -> tuple[dict | None, bool]:
    """Exact-then-prefix match of the already-casefolded needle `n` against
    `entries`, reusing `resolve_member`'s exact-then-prefix discipline.
    Returns `(matched_entry_or_None, ambiguous)` — never guesses: >1 hit at
    either tier is ambiguous, not a pick."""
    exact = [e for e in entries if n in _directory_match_keys(e)]
    if len(exact) == 1:
        return exact[0], False
    if len(exact) > 1:
        return None, True
    if len(n) < 2:
        return None, False
    prefix = [e for e in entries if any(k.startswith(n) for k in _directory_match_keys(e))]
    if len(prefix) == 1:
        return prefix[0], False
    if len(prefix) > 1:
        return None, True
    return None, False


def _contact_from(matched_entry: dict | None, email_needle: str | None) -> tuple[str | None, str | None]:
    """`(email, name)` for a resolved-but-non-member candidate, normalized
    lower-case email (AC-9). A NAME-needle match already carries both from
    the enriched directory row it was found in. An EMAIL-needle match
    carries only the (normalized) needle itself as the email — the needle
    IS the email that resolved the account, so no further profile read is
    needed to satisfy the nullable `name` field of the return shape."""
    if matched_entry is not None:
        email = matched_entry.get("email")
        return (email.strip().lower() if email else None), matched_entry.get("display_name")
    if email_needle:
        return email_needle.strip().lower(), None
    return None, None


def resolve_candidate(project_id: int, needle: str) -> dict:
    """Classify a mentioned name-or-email into exactly one tier relative to
    THIS project's tenancy — read-only sibling of `resolve_member` above,
    widening the search project -> workspace -> company -> email while
    re-asserting `project["company_id"]`/`project["workspace_id"]` on every
    branch, never a caller-supplied tenant (AD-TNM1). Deterministic — NO LLM
    call (AD-P18 fast-path), same posture as `resolve_member`.

    Fail-closed root: a falsy `get_project(project_id)` short-circuits to
    `t_refuse(no_project)` before any membership table is read.

    Returns exactly one of:
      {"tier": "t_member",    "member": {<list_members row>}}
      {"tier": "t_workspace", "user_id": str, "email": str|None, "name": str|None}
      {"tier": "t_company",   "user_id": str, "email": str|None, "name": str|None}
      {"tier": "t_newuser",   "email": str}                       # lower-cased
      {"tier": "t_refuse",    "reason": str}  # other_company|no_match|ambiguous|no_project
      # (policy match: the project-only same-domain "cross_company" refuse is
      # gone — matches the admin-invite policy, no email-domain gate)

    This is classification only — it performs no write. The action layer
    that consumes this tier re-runs the live membership assertion
    immediately before any mutation, mirroring `handle_delegate_task`'s
    `is_project_member` double-gate (`project_delegation.py`)."""
    project = get_project(project_id)
    if not project:
        return {"tier": TIER_REFUSE, "reason": "no_project"}

    company_id = project["company_id"]
    workspace_id = project["workspace_id"]

    raw = (needle or "").strip()
    is_email = bool(_EMAIL_RE.match(raw))
    # A leading "@" on a NAME needle is stripped exactly as `resolve_member`
    # does; an email never legitimately starts with "@" so this never fires
    # for the email shape.
    name_raw = raw[1:] if (not is_email and raw.startswith("@")) else raw
    n = name_raw.casefold()

    # Tier 1 — already a project member. `resolve_member` covers the
    # name/job_role match (its own casefold/@-strip/exact-then-prefix
    # discipline over `list_members`); an EMAIL needle additionally checks
    # the roster's `email` column directly, since `_match_keys` (above)
    # matches only on name/job_role and never sees email.
    if is_email:
        roster = list_members(project_id)
        email_matches = [
            m for m in roster if (m.get("email") or "").strip().lower() == raw.lower()
        ]
        if len(email_matches) == 1:
            return {"tier": TIER_MEMBER, "member": email_matches[0]}
    member_res = resolve_member(project_id, needle)
    if member_res["status"] == "resolved":
        return {"tier": TIER_MEMBER, "member": member_res["member"]}
    if member_res["status"] == "ambiguous":
        # The picker disambiguates upstream; the resolver never guesses.
        return {"tier": TIER_REFUSE, "reason": "ambiguous"}

    if not n:
        return {"tier": TIER_REFUSE, "reason": "no_match"}

    user_id: str | None = None
    matched_entry: dict | None = None

    if is_email:
        user_id = user_id_for_email(raw)
    else:
        ws_members = list_workspace_members(workspace_id)
        match, ambiguous = _match_directory(ws_members, n)
        if ambiguous:
            return {"tier": TIER_REFUSE, "reason": "ambiguous"}
        if match:
            user_id, matched_entry = match["user_id"], match
        else:
            company_members = list_company_members(company_id)
            match, ambiguous = _match_directory(company_members, n)
            if ambiguous:
                return {"tier": TIER_REFUSE, "reason": "ambiguous"}
            if match:
                user_id, matched_entry = match["user_id"], match

    if user_id:
        # Fail-closed re-assertion (AD-TNM1): t_workspace/t_company are
        # returned ONLY after the LIVE membership check against THIS
        # project's workspace_id/company_id — never the match set that
        # found the candidate. There is no path where a user_id from a
        # foreign tenant reaches either tier.
        if get_workspace_member(workspace_id, user_id):
            email, name = _contact_from(matched_entry, raw if is_email else None)
            return {"tier": TIER_WORKSPACE, "user_id": user_id, "email": email, "name": name}
        if get_member(company_id=company_id, user_id=user_id):
            email, name = _contact_from(matched_entry, raw if is_email else None)
            return {"tier": TIER_COMPANY, "user_id": user_id, "email": email, "name": name}
        # A real account that exists but is NOT in this project's company
        # (one-user-one-company) — cross-tenant, never disclosed as an
        # addable tier.
        return {"tier": TIER_REFUSE, "reason": "other_company"}

    if not is_email:
        # Cannot invite an outsider known only by name — no cross-tenant
        # directory read happens for a NAME needle.
        return {"tier": TIER_REFUSE, "reason": "no_match"}

    # EMAIL needle, no existing account. Match the admin-invite policy exactly:
    # no domain gate — any domain is invitable. The one cross-company refuse the
    # admin flow keeps (an email already a member of ANOTHER company) is retained
    # just above via email_belongs_to_other_company. This drops the project-only
    # same-domain restriction (the locked "cross-company hard refuse" domain gate).
    if email_belongs_to_other_company(company_id=company_id, email=raw):
        return {"tier": TIER_REFUSE, "reason": "other_company"}
    return {"tier": TIER_NEWUSER, "email": raw.strip().lower()}
