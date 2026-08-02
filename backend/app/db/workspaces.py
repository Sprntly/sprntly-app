"""Workspaces + workspace membership.

Multi-workspace model (2026-07): 1 company → N workspaces (workspace ≈ a
product area / team). Two-level roles:

  * company_members.role (owner/admin/member/viewer) — the ORG role. Org
    owners/admins implicitly access every workspace (require_workspace maps
    them to workspace_role='admin' without needing a row here).
  * workspace_members.role (admin/member/viewer) — access inside one
    workspace, for plain org members.

Every company has exactly ONE default workspace (is_default, DB-enforced by
the workspaces_one_default_per_company partial unique index). Existing data
was backfilled onto it; ensure_default_workspace() self-heals companies
created through paths that predate workspace creation.

Datasets: the dataset text slug is the workspace's corpus key. The DEFAULT
workspace keeps the bare company slug (zero migration for existing data);
additional workspaces get "{company_slug}--{workspace_slug}". The mapping's
source of truth is datasets.workspace_id — never parse slugs.

All access via require_client() (service role; the route layer is the
tenancy boundary, matching db/team.py).
"""
from __future__ import annotations

import logging
import re
import uuid

from app.db.authcache import default_ws_cache, workspace_cache, workspace_member_cache
from app.db.client import require_client, retry_on_disconnect

logger = logging.getLogger(__name__)

_WORKSPACE_COLUMNS = (
    "id, company_id, product_id, name, slug, is_default, created_at, "
    "team_scope, team_strategy, team_roadmap, sizing_methodology, "
    "additional_context"
)

# The optional workspace-owned fields update_workspace can patch (2026-07-22:
# moved off the companies row). A sentinel distinguishes "not provided" from an
# explicit None (which clears the column).
_UNSET = object()
_WORKSPACE_PATCH_FIELDS = (
    "team_scope",
    "team_strategy",
    "team_roadmap",
    "sizing_methodology",
    "additional_context",
)

# Cached "no workspace_members row" marker. A sentinel object (never a string
# or dict — real rows are dicts) so it can't collide with row data. Caching
# absence is safe HERE because every workspace-member write goes through a
# backend route, which invalidates (unlike company_members — see
# app.db.authcache's docstring).
_ABSENT = object()

# Must satisfy the DB CHECK: '^[a-z0-9][a-z0-9_-]{0,62}$'
_SLUG_STRIP_RE = re.compile(r"[^a-z0-9_-]+")


def slugify_workspace_name(name: str) -> str:
    """A workspace name → a CHECK-satisfying slug ('Notifications & Alerts'
    → 'notifications-alerts'). Falls back to a random token when nothing
    usable survives."""
    s = _SLUG_STRIP_RE.sub("-", name.strip().lower()).strip("-_")
    s = re.sub(r"-{2,}", "-", s)[:63]
    if not s or not re.match(r"^[a-z0-9]", s):
        s = f"ws-{uuid.uuid4().hex[:8]}"
    return s


@retry_on_disconnect
def get_workspace(workspace_id: str) -> dict | None:
    cached = workspace_cache.get(workspace_id)
    if cached is not None:
        return cached
    rows = (
        require_client()
        .table("workspaces")
        .select(_WORKSPACE_COLUMNS)
        .eq("id", workspace_id)
        .limit(1)
        .execute()
        .data
        or []
    )
    ws = rows[0] if rows else None
    if ws is not None:
        workspace_cache.set(workspace_id, ws)
    return ws


@retry_on_disconnect
def list_workspaces_for_company(company_id: str) -> list[dict]:
    """All of a company's workspaces, default first then by creation time."""
    rows = (
        require_client()
        .table("workspaces")
        .select(_WORKSPACE_COLUMNS)
        .eq("company_id", company_id)
        .execute()
        .data
        or []
    )
    rows.sort(key=lambda r: (not r.get("is_default"), r.get("created_at") or ""))
    return rows


@retry_on_disconnect
def default_workspace_for_company(company_id: str) -> dict | None:
    cached = default_ws_cache.get(company_id)
    if cached is not None:
        return cached
    rows = (
        require_client()
        .table("workspaces")
        .select(_WORKSPACE_COLUMNS)
        .eq("company_id", company_id)
        .eq("is_default", True)
        .limit(1)
        .execute()
        .data
        or []
    )
    ws = rows[0] if rows else None
    if ws is not None:
        default_ws_cache.set(company_id, ws)
    return ws


def ensure_default_workspace(company_id: str) -> dict:
    """The company's default workspace, creating it if missing (self-healing
    for companies created by paths that predate workspace rows). Race-safe:
    the workspaces_one_default_per_company partial unique index makes a
    concurrent double-create fail — we re-read on any insert error."""
    existing = default_workspace_for_company(company_id)
    if existing:
        return existing
    client = require_client()
    try:
        client.table("workspaces").insert(
            {
                "id": str(uuid.uuid4()),
                "company_id": company_id,
                "name": "Default",
                "slug": "default",
                "is_default": True,
            }
        ).execute()
    except Exception:  # noqa: BLE001 — lost the race; the row now exists
        logger.info(
            "ensure_default_workspace: concurrent create for %s — re-reading",
            company_id,
        )
    created = default_workspace_for_company(company_id)
    if not created:
        raise RuntimeError(f"could not ensure default workspace for {company_id}")
    return created


def create_workspace(
    company_id: str, name: str, *, product_id: str | None = None
) -> dict:
    """Create an additional (non-default) workspace. The slug is derived from
    the name and de-duplicated with numeric suffixes against the company's
    existing workspaces (unique(company_id, slug))."""
    client = require_client()
    base = slugify_workspace_name(name)
    taken = {w["slug"] for w in list_workspaces_for_company(company_id)}
    slug = base
    n = 2
    while slug in taken:
        slug = f"{base[:60]}-{n}"
        n += 1
    wid = str(uuid.uuid4())
    client.table("workspaces").insert(
        {
            "id": wid,
            "company_id": company_id,
            "product_id": product_id,
            "name": name.strip(),
            "slug": slug,
            "is_default": False,
        }
    ).execute()
    return get_workspace(wid) or {
        "id": wid,
        "company_id": company_id,
        "product_id": product_id,
        "name": name.strip(),
        "slug": slug,
        "is_default": False,
    }


def update_workspace(
    workspace_id: str,
    *,
    name: str | None = None,
    team_scope: object = _UNSET,
    team_strategy: object = _UNSET,
    team_roadmap: object = _UNSET,
    sizing_methodology: object = _UNSET,
    additional_context: object = _UNSET,
) -> dict | None:
    """Partial-update a workspace. `name` is the cosmetic rename (slug and
    dataset binding unchanged, so it never churns corpus paths or dataset-keyed
    rows); the five workspace-owned fields (2026-07-22, moved off the companies
    row) are each optional — pass a value to set it, None to clear it, or omit
    to leave it untouched. Only the provided keys are written."""
    patch: dict = {}
    if name is not None:
        # Respect the DB workspaces_name_nonempty check — a blank rename is a
        # no-op on the name (callers upstream validate/require it).
        cleaned = name.strip()
        if cleaned:
            patch["name"] = cleaned
    for field, value in (
        ("team_scope", team_scope),
        ("team_strategy", team_strategy),
        ("team_roadmap", team_roadmap),
        ("sizing_methodology", sizing_methodology),
        ("additional_context", additional_context),
    ):
        if value is not _UNSET:
            patch[field] = value
    if not patch:
        return get_workspace(workspace_id)
    require_client().table("workspaces").update(patch).eq(
        "id", workspace_id
    ).execute()
    # Drop the cached pre-update row so the re-read below (and the route's
    # response) reflects the new values; the route's coarse invalidation runs
    # only after this returns.
    workspace_cache.invalidate(workspace_id)
    return get_workspace(workspace_id)


def delete_workspace(workspace_id: str) -> None:
    """Hard-delete a workspace. FKs cascade (workspace_members, the scoped
    product-data rows, datasets). Callers must refuse the default workspace
    first — deleting it would orphan the company's bare-slug dataset world."""
    require_client().table("workspaces").delete().eq("id", workspace_id).execute()


# ─────────────────────── membership ───────────────────────


@retry_on_disconnect
def get_workspace_member(workspace_id: str, user_id: str) -> dict | None:
    key = (workspace_id, user_id)
    cached = workspace_member_cache.get(key)
    if cached is not None:
        return None if cached is _ABSENT else cached
    rows = (
        require_client()
        .table("workspace_members")
        .select("id, workspace_id, user_id, role, created_at")
        .eq("workspace_id", workspace_id)
        .eq("user_id", user_id)
        .limit(1)
        .execute()
        .data
        or []
    )
    row = rows[0] if rows else None
    workspace_member_cache.set(key, _ABSENT if row is None else row)
    return row


@retry_on_disconnect
def list_workspace_members(workspace_id: str) -> list[dict]:
    """workspace_members rows enriched with profile display data (mirrors
    db/team.list_company_members)."""
    client = require_client()
    members = (
        client.table("workspace_members")
        .select("id, user_id, role, created_at")
        .eq("workspace_id", workspace_id)
        .execute()
        .data
        or []
    )
    if not members:
        return []
    user_ids = [m["user_id"] for m in members]
    profiles = (
        client.table("profiles")
        .select("id, email, full_name, first_name, last_name, avatar_url")
        .in_("id", user_ids)
        .execute()
        .data
        or []
    )
    by_id = {p["id"]: p for p in profiles}
    enriched: list[dict] = []
    for m in members:
        prof = by_id.get(m["user_id"]) or {}
        full = (prof.get("full_name") or "").strip()
        first = (prof.get("first_name") or "").strip()
        last = (prof.get("last_name") or "").strip()
        display = full or (f"{first} {last}".strip() if (first or last) else None) or None
        enriched.append(
            {
                "id": m.get("id"),
                "user_id": m["user_id"],
                "role": m.get("role"),
                "created_at": m.get("created_at"),
                "display_name": display,
                "email": prof.get("email"),
                "avatar_url": prof.get("avatar_url"),
            }
        )
    return enriched


def upsert_workspace_member(workspace_id: str, user_id: str, role: str) -> dict:
    """Grant or update a workspace membership (idempotent on the
    unique(workspace_id, user_id) key)."""
    client = require_client()
    existing = get_workspace_member(workspace_id, user_id)
    if existing:
        if existing.get("role") != role:
            client.table("workspace_members").update({"role": role}).eq(
                "id", existing["id"]
            ).execute()
            existing = {**existing, "role": role}
            workspace_member_cache.invalidate((workspace_id, user_id))
        return existing
    row = {
        "id": str(uuid.uuid4()),
        "workspace_id": workspace_id,
        "user_id": user_id,
        "role": role,
    }
    try:
        client.table("workspace_members").insert(row).execute()
    except Exception:  # noqa: BLE001 — lost a concurrent-grant race; re-read
        found = get_workspace_member(workspace_id, user_id)
        if found:
            return found
        raise
    # Drop the cached "absent" marker set by the get above, so the next read
    # sees the new grant (matters for direct-helper callers, not just routes).
    workspace_member_cache.invalidate((workspace_id, user_id))
    return row


def delete_workspace_member(workspace_id: str, user_id: str) -> None:
    require_client().table("workspace_members").delete().eq(
        "workspace_id", workspace_id
    ).eq("user_id", user_id).execute()
    workspace_member_cache.invalidate((workspace_id, user_id))


@retry_on_disconnect
def workspace_ids_by_user(company_id: str) -> dict[str, list[str]]:
    """user_id → [workspace_id] map of the explicit workspace_members grants
    across all of a company's workspaces. Org owners/admins may have no rows
    here — their access is implicit (require_workspace maps them to admin)."""
    ws_ids = [w["id"] for w in list_workspaces_for_company(company_id)]
    if not ws_ids:
        return {}
    rows = (
        require_client()
        .table("workspace_members")
        .select("workspace_id, user_id")
        .in_("workspace_id", ws_ids)
        .execute()
        .data
        or []
    )
    out: dict[str, list[str]] = {}
    for r in rows:
        out.setdefault(r["user_id"], []).append(r["workspace_id"])
    return out


def set_member_workspaces(
    company_id: str, user_id: str, workspace_ids: list[str]
) -> list[str]:
    """Replace a user's explicit workspace grants within one company. Grants
    that survive keep their per-workspace role; new grants default to
    'member'. Returns the resulting workspace_id list (company order)."""
    company_ws = [w["id"] for w in list_workspaces_for_company(company_id)]
    target = {w for w in workspace_ids if w in company_ws}
    current = set(workspace_ids_by_user(company_id).get(user_id, []))
    for wid in target - current:
        upsert_workspace_member(wid, user_id, "member")
    for wid in current - target:
        delete_workspace_member(wid, user_id)
    return [w for w in company_ws if w in target]


@retry_on_disconnect
def list_workspaces_for_user(
    company_id: str, user_id: str, org_role: str | None
) -> list[dict]:
    """The workspaces this user can enter, each carrying the caller's
    effective role. Org owner/admin → every workspace as 'admin' (implicit);
    plain members/viewers → only where a workspace_members row exists."""
    all_ws = list_workspaces_for_company(company_id)
    if org_role in ("owner", "admin"):
        return [{**w, "role": "admin"} for w in all_ws]
    rows = (
        require_client()
        .table("workspace_members")
        .select("workspace_id, role")
        .eq("user_id", user_id)
        .execute()
        .data
        or []
    )
    role_by_ws = {r["workspace_id"]: r.get("role") or "member" for r in rows}
    return [
        {**w, "role": role_by_ws[w["id"]]} for w in all_ws if w["id"] in role_by_ws
    ]


# ─────────────────────── dataset binding ───────────────────────


@retry_on_disconnect
def dataset_slug_for_workspace(workspace_id: str) -> str | None:
    """The dataset slug bound to a workspace (datasets.workspace_id), or None
    when no dataset row is bound yet."""
    rows = (
        require_client()
        .table("datasets")
        .select("slug")
        .eq("workspace_id", workspace_id)
        .limit(1)
        .execute()
        .data
        or []
    )
    return rows[0]["slug"] if rows else None


@retry_on_disconnect
def workspace_for_dataset_slug(slug: str) -> dict | None:
    """{workspace_id, company_id} for a dataset slug via its bound workspace,
    or None when the dataset is unbound (legacy demo datasets)."""
    client = require_client()
    ds = (
        client.table("datasets")
        .select("slug, workspace_id")
        .eq("slug", slug)
        .limit(1)
        .execute()
        .data
        or []
    )
    wid = (ds[0] if ds else {}).get("workspace_id")
    if not wid:
        return None
    ws = get_workspace(str(wid))
    if not ws:
        return None
    return {"workspace_id": ws["id"], "company_id": ws["company_id"]}


def register_workspace_dataset(workspace: dict, *, company_slug: str) -> str:
    """Ensure the workspace has a bound dataset row (+ corpus dir) and return
    its slug. Default workspace → the bare company slug (existing data keeps
    working); additional workspaces → '{company_slug}--{workspace_slug}'
    (length-capped to the 63-char slug format)."""
    from app.config import settings
    from app.db.datasets import bind_dataset_workspace, get_dataset, insert_dataset

    existing = dataset_slug_for_workspace(workspace["id"])
    if existing:
        return existing

    if workspace.get("is_default"):
        slug = company_slug
    else:
        slug = f"{company_slug}--{workspace['slug']}"[:63].rstrip("-_")

    if get_dataset(slug):
        bind_dataset_workspace(slug, workspace["id"])
    else:
        insert_dataset(slug, workspace.get("name") or slug, workspace_id=workspace["id"])
    try:
        (settings.data_path / slug).mkdir(parents=True, exist_ok=True)
    except OSError:
        logger.warning("register_workspace_dataset: could not mkdir corpus for %s", slug)
    return slug
