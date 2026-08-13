"""Artifact format templates — company-scoped uploaded PRD / ticket /
engineering-spec FORMS (migration 20260805120000_artifact_templates.sql).

The structural twin of app/db/custom_skills.py, and deliberately so: a skill is
the METHOD, a template here is the FORM. Scoping is COMPANY-LEVEL — every read
filters by company_id, so all workspaces in a company share one library and one
active format per artifact type. workspace_id is stamped on each row (which
workspace uploaded it) and NEVER appears in a query filter, so a future move to
workspace-level scoping is a query change, not a backfill.

`section_map` and `compile_notes` are TEXT columns holding JSON — opaque
payloads, round-tripped whole and never queried into. Rows come back with both
decoded, and `is_active` coerced to a real bool, so callers never see either
encoding (or the SQLite fake's 0/1).

Two rules in here are not obvious and are the reason this module exists rather
than inline queries:

  - `activate_template` DEACTIVATES THE SIBLINGS FIRST, then activates. The
    partial unique index `artifact_templates_active_uniq` makes the other order
    fail on 23505, which surfaces as a 409 the user cannot act on.
  - `get_active_template` reads every row for (company_id, artifact_type) and
    picks the active one IN PYTHON rather than filtering `.eq("is_active",
    True)` server-side. Boolean round-tripping differs between real Postgres and
    the SQLite fake (tests/_fake_supabase.py), and this sidesteps it entirely
    for the one read that generation will depend on.
"""
from __future__ import annotations

import json
import logging
import uuid
from datetime import datetime, timezone

from app.db.client import require_client

logger = logging.getLogger(__name__)

# Postgres unique-violation SQLSTATE. supabase-py surfaces it on the raised
# error's `.code`; sqlite (the test fake) reports it via IntegrityError.
_UNIQUE_VIOLATION = "23505"


class ActiveTemplateConflict(ValueError):
    """Another template took this (company, artifact_type)'s active slot between
    the deactivate and the activate — the partial unique index refused the
    write. The route surfaces it as a 409 asking the caller to retry."""


def _now_iso() -> str:
    """Microsecond-precision UTC timestamp — unlike client.utc_now()'s second
    precision, so the library's newest-first ordering stays stable when two
    templates are uploaded within the same second."""
    return datetime.now(timezone.utc).isoformat()


def _is_unique_violation(exc: Exception) -> bool:
    """True if `exc` is a unique-constraint violation, across both real Supabase
    (PostgREST APIError, code 23505) and the SQLite test fake (sqlite3
    .IntegrityError on a UNIQUE index). Lifted verbatim from
    db/custom_skills.py:45 — same two engines, same two error shapes."""
    code = getattr(exc, "code", None)
    if code == _UNIQUE_VIOLATION:
        return True
    text = f"{type(exc).__name__}: {exc}".lower()
    return "unique" in text or _UNIQUE_VIOLATION in text


def _drop_planner_cache(company_id: str) -> None:
    """Tell the Ask Planner its cached view of this company's formats is stale.

    Lifted verbatim from db/custom_skills.py:70, because it is the same
    mechanism guarding the same failure: the planner lists this company's
    formats in every prompt and validates the id it picks against that list,
    memoising both in-process until something says otherwise
    (`ask_planner.invalidate_catalog_cache`). Entries live until the process
    restarts by design, so a write that skips this leaves the planner offering a
    deleted format — or blind to one just uploaded — for the life of the
    process, and a nominal week-long TTL is the only thing that eventually
    heals it.

    ACTIVATION MATTERS MOST. It is the write that changes what every future
    document is written into while changing no row a user is looking at, so a
    stale cache there means the planner keeps describing the OLD active format
    as the one in use — a wrong answer to "which format am I using" delivered
    with complete confidence.

    Imported lazily: `ask_planner` reaches back into `qa_agent` and the db
    layer, so a module-level import here would close a cycle. Never allowed to
    break a write — a cache that failed to clear is a stale prompt, which the
    next write or a restart fixes; a format that failed to save is not."""
    try:
        from app.ask_planner import invalidate_catalog_cache

        invalidate_catalog_cache(company_id)
    except Exception:  # noqa: BLE001 — best-effort, never fails the write
        logger.debug("planner cache invalidation failed for %s", company_id, exc_info=True)


def _decode(row: dict) -> dict:
    """DB row → caller shape: JSON text columns decoded, is_active a real bool.

    Only keys PRESENT in the row are touched, because the list read selects a
    narrower column set than the detail read — adding a phantom empty
    `section_map` to a list row would claim the map is empty when it simply
    wasn't fetched."""
    out = dict(row)
    for col, empty in (("section_map", {}), ("compile_notes", [])):
        if col not in out:
            continue
        raw = out.get(col)
        if raw is None or raw == "":
            out[col] = empty
            continue
        if isinstance(raw, str):
            try:
                out[col] = json.loads(raw)
            except (TypeError, ValueError):
                logger.warning(
                    "artifact_templates row %s has undecodable %s; treating as empty",
                    row.get("id"), col,
                )
                out[col] = empty
    if "is_active" in out:
        # SQLite hands back 0/1; Postgres a real bool. Callers branch on this,
        # and it is serialized straight into the API payload.
        out["is_active"] = bool(out["is_active"])
    return out


def insert_template(
    *,
    company_id: str,
    workspace_id: str,
    artifact_type: str,
    name: str,
    source_md: str,
    content_hash: str,
    uploader_id: str,
    uploader_name: str,
) -> dict:
    """Create the template row; returns the decoded row.

    Lands at `compile_status='pending'` with an empty `compiled` — a new
    template governs nothing until something compiles and validates it. The
    id/created_at are generated client-side (uuid4 / microsecond ISO) so the
    SQLite test fake behaves identically to Postgres.

    There is deliberately NO uniqueness on (company_id, name): template names
    are free text shown to teammates, not invocation triggers, so two formats
    may share a name and the upload path never has to deconflict one."""
    c = require_client()
    now = _now_iso()
    row = {
        "id": str(uuid.uuid4()),
        "company_id": company_id,
        "workspace_id": workspace_id,
        "artifact_type": artifact_type,
        "name": name,
        "source_md": source_md,
        "source_chars": len(source_md or ""),
        "compiled": "",
        "section_map": json.dumps({}),
        "compile_status": "pending",
        "compile_notes": json.dumps([]),
        # '' = "no summary yet"; the compile that validates this row writes the
        # real one (summarize.generate_summary), so a fresh upload is never
        # described by anything.
        "summary": "",
        "content_hash": content_hash,
        "is_active": False,
        "uploader_id": uploader_id,
        "uploader_name": uploader_name,
        "created_at": now,
        "updated_at": now,
    }
    resp = c.table("artifact_templates").insert(row).execute()
    _drop_planner_cache(company_id)
    return _decode(resp.data[0] if resp.data else row)


# List view omits the bulky content columns — `source_md` runs to 50k characters
# and `compiled` to more, and the library screen renders from metadata alone.
# `compile_notes` IS included: the row's reason line and its "See all 3"
# affordance are both derived from it, and a per-row detail fetch to fill a list
# is exactly what the list is supposed to avoid.
_LIST_COLUMNS = (
    "id, company_id, artifact_type, name, source_chars, compile_status, "
    "compile_notes, summary, content_hash, is_active, uploader_id, "
    "uploader_name, created_at, updated_at"
)


def list_templates(company_id: str, artifact_type: str | None = None) -> list[dict]:
    """The company's templates, newest first (metadata only), optionally
    narrowed to one artifact type.

    Company-filtered, which is the ONLY thing keeping one tenant's library out
    of another's — the backend holds the service-role key, so there is no RLS
    behind this."""
    c = require_client()
    q = (
        c.table("artifact_templates")
        .select(_LIST_COLUMNS)
        .eq("company_id", company_id)
    )
    if artifact_type:
        q = q.eq("artifact_type", artifact_type)
    resp = q.order("created_at", desc=True).execute()
    return [_decode(r) for r in (resp.data or [])]


def get_template_by_id(company_id: str, template_id: str) -> dict | None:
    """Full decoded row by company + row id, or None.

    Company-filtered so a foreign id is indistinguishable from a missing one —
    every route's ownership check goes through here, and every miss is a 404."""
    c = require_client()
    resp = (
        c.table("artifact_templates")
        .select("*")
        .eq("company_id", company_id)
        .eq("id", template_id)
        .limit(1)
        .execute()
    )
    return _decode(resp.data[0]) if resp.data else None


def update_template(
    *,
    company_id: str,
    template_id: str,
    name: str | None = None,
    source_md: str | None = None,
    content_hash: str | None = None,
    workspace_id: str | None = None,
    uploader_id: str | None = None,
    uploader_name: str | None = None,
    compile_status: str | None = None,
) -> dict | None:
    """Patch one company-owned template; returns the decoded row, or None when
    the id is missing or belongs to another company (indistinguishable, like the
    by-id lookup).

    Every argument DEFAULTS TO None meaning "leave this column alone", so a
    rename never disturbs the source and a source replacement never disturbs the
    name. `created_at` is deliberately never patched — the library's newest-first
    order should not reshuffle because someone fixed a typo in a format's name.

    Replacing `source_md` moves the row back to `pending` unless the caller says
    otherwise, because the stored `compiled`/`section_map` describe text that is
    no longer in the row. It does NOT clear `compiled`: the last good skeleton
    keeps serving generation until a new one validates, so one careless edit
    cannot silently drop the whole company back to the built-in format for the
    duration of a recompile.

    Both the existence check and the update are company-filtered, so a racing
    caller can never write a foreign row."""
    row = get_template_by_id(company_id, template_id)
    if row is None:
        return None
    patch: dict = {"updated_at": _now_iso()}
    if name is not None:
        patch["name"] = name
    if source_md is not None:
        patch["source_md"] = source_md
        patch["source_chars"] = len(source_md)
        # A new source invalidates the notes about the OLD source. `compiled`
        # and `section_map` stay put on purpose (see the docstring).
        patch["compile_notes"] = json.dumps([])
        patch.setdefault("compile_status", "pending")
    if content_hash is not None:
        patch["content_hash"] = content_hash
    if workspace_id is not None:
        patch["workspace_id"] = workspace_id
    if uploader_id is not None:
        patch["uploader_id"] = uploader_id
    if uploader_name is not None:
        patch["uploader_name"] = uploader_name
    if compile_status is not None:
        patch["compile_status"] = compile_status
    c = require_client()
    resp = (
        c.table("artifact_templates")
        .update(patch)
        .eq("company_id", company_id)
        .eq("id", template_id)
        .execute()
    )
    _drop_planner_cache(company_id)
    # PostgREST returns the updated representation; fall back to the row we
    # already read merged with the patch rather than reporting a failed update
    # (the caller reads None as "the row is gone" and would 404 a template that
    # is still there and has just been changed).
    return _decode(resp.data[0] if resp.data else {**row, **patch})


def set_compile_result(
    *,
    company_id: str,
    template_id: str,
    compile_status: str,
    compiled: str | None = None,
    section_map: dict | None = None,
    compile_notes: list | None = None,
    summary: str | None = None,
) -> dict | None:
    """Record what a compile produced; returns the decoded row, or None for a
    missing-or-foreign id.

    `compiled` and `section_map` DEFAULT TO None meaning "leave the previous
    ones standing", and that default is the whole point of this signature.
    Moving a row to `compiling` must not null the skeleton the company is
    currently generating with — the new value is written only when a compile
    succeeds, so the last good format keeps serving until the new one validates.
    Callers that genuinely want to clear them pass `""` / `{}` explicitly.

    `summary` follows the same None-means-leave-it rule, but with the OPPOSITE
    explicit case: a successful compile passes it even when generation failed
    and it is '' — a summary describing source text that has since been replaced
    is worse than no summary, so success paths overwrite and failure paths
    (which change no source) leave the old one standing.

    `compile_notes` is a list of {code, message} objects, never free text: the
    web keys a translation table on `code`, so a raw validator note must never
    reach a screen."""
    row = get_template_by_id(company_id, template_id)
    if row is None:
        return None
    patch: dict = {"compile_status": compile_status, "updated_at": _now_iso()}
    if compiled is not None:
        patch["compiled"] = compiled
    if section_map is not None:
        patch["section_map"] = json.dumps(section_map)
    if compile_notes is not None:
        patch["compile_notes"] = json.dumps(compile_notes)
    if summary is not None:
        patch["summary"] = summary
    c = require_client()
    resp = (
        c.table("artifact_templates")
        .update(patch)
        .eq("company_id", company_id)
        .eq("id", template_id)
        .execute()
    )
    # A compile is what turns an uploaded format into a usable one, so the
    # planner's cached `compile_status` is exactly what decides whether it may
    # be named — stale here means refusing a format that just became ready.
    _drop_planner_cache(company_id)
    return _decode(resp.data[0] if resp.data else {**row, **patch})


def set_template_summary(
    *, company_id: str, template_id: str, summary: str
) -> dict | None:
    """Write ONLY the summary; returns the decoded row, or None for a
    missing-or-foreign id.

    The self-heal backfill's writer (`summarize._summarize_row`) — a row
    uploaded before the summary column existed gets described without touching
    its compile state, notes, or skeleton. Deliberately not routed through
    `set_compile_result`: that signature requires a `compile_status`, and the
    backfill has no business restating one a compile already wrote (a racing
    recompile could have moved it between this path's read and write).

    Drops the planner's catalog cache like every other write here — the drop IS
    the self-heal loop's second half: the next plan re-reads the library and
    sees the summary this write landed."""
    row = get_template_by_id(company_id, template_id)
    if row is None:
        return None
    patch = {"summary": summary, "updated_at": _now_iso()}
    c = require_client()
    resp = (
        c.table("artifact_templates")
        .update(patch)
        .eq("company_id", company_id)
        .eq("id", template_id)
        .execute()
    )
    _drop_planner_cache(company_id)
    return _decode(resp.data[0] if resp.data else {**row, **patch})


def activate_template(
    company_id: str, artifact_type: str, template_id: str
) -> dict | None:
    """Make one company-owned template THE format for its artifact type;
    returns the decoded row, or None for a missing-or-foreign id (or an id whose
    row is of a different artifact type than the caller claimed).

    Order is load-bearing. `artifact_templates_active_uniq` is a PARTIAL unique
    index on (company_id, artifact_type) WHERE is_active, so activating before
    deactivating the outgoing sibling fails on 23505 every single time there is
    an outgoing sibling — which is the common case, not an edge one. Deactivate
    first, then activate.

    Between the two statements the company briefly has NO active template of
    this type, so a generation resolving in that window falls back to the
    built-in format. That is the correct degradation (a built-in PRD, not a
    failed one) and it is why this is not worth a lock.

    THE WINDOW MUST NOT BECOME PERMANENT. The two statements are not in one
    transaction, so anything that fails the second write — a 23505 race, a
    dropped connection, a statement timeout — used to leave the company with the
    outgoing format switched off and the incoming one never switched on: zero
    active templates, every future document silently back on the built-in, and
    the only signal a failed button click. So the previously-active row is read
    BEFORE the deactivate and put back best-effort on any failure of the
    activate, before the exception propagates.

    The restore is deliberately allowed to fail silently. On a genuine 23505
    race another caller legitimately holds the slot, and re-activating the old
    row would trip the same partial unique index — leaving the winner in place
    is the correct outcome, not an error to report.

    A 23505 on the second statement means another caller activated a different
    template inside that window; it surfaces as ActiveTemplateConflict → 409."""
    row = get_template_by_id(company_id, template_id)
    if row is None or row.get("artifact_type") != artifact_type:
        return None
    c = require_client()
    now = _now_iso()
    # Read the outgoing format BEFORE switching it off — it is the only thing
    # that can put the company back where it started if the activate fails.
    prior = get_active_template(company_id, artifact_type)
    prior_id = prior["id"] if prior and prior["id"] != template_id else None
    # Company- AND type-filtered: another tenant's active format, and this
    # company's format for a DIFFERENT artifact type, are both untouched.
    (
        c.table("artifact_templates")
        .update({"is_active": False, "updated_at": now})
        .eq("company_id", company_id)
        .eq("artifact_type", artifact_type)
        .execute()
    )
    try:
        resp = (
            c.table("artifact_templates")
            .update({"is_active": True, "updated_at": now})
            .eq("company_id", company_id)
            .eq("id", template_id)
            .execute()
        )
    except Exception as exc:  # noqa: BLE001 — narrowed to unique-violation below
        _restore_active(c, company_id, prior_id)
        # Dropped on the FAILURE path too: the deactivate above already landed,
        # so whatever the restore managed, the planner's cached copy no longer
        # describes the rows in the table.
        _drop_planner_cache(company_id)
        if _is_unique_violation(exc):
            raise ActiveTemplateConflict(artifact_type) from exc
        raise
    _drop_planner_cache(company_id)
    logger.info(
        "artifact_template_activated company_present=%s type=%s",
        bool(company_id), artifact_type,
    )
    return _decode(resp.data[0] if resp.data else {**row, "is_active": True})


def _restore_active(c, company_id: str, prior_id: str | None) -> None:
    """Put the outgoing format back after a failed activation. Best-effort by
    design — see activate_template's docstring for why a failure here is the
    right outcome rather than something to raise."""
    if not prior_id:
        return
    try:
        (
            c.table("artifact_templates")
            .update({"is_active": True, "updated_at": _now_iso()})
            .eq("company_id", company_id)
            .eq("id", prior_id)
            .execute()
        )
    except Exception:  # noqa: BLE001 — the original failure is what matters
        logger.warning(
            "artifact_template_activation_rollback_failed company_present=%s "
            "— the company may have no active template for this type",
            bool(company_id),
        )


def deactivate_template(company_id: str, template_id: str) -> dict | None:
    """Drop one company-owned template out of the active slot, leaving the
    artifact type on the built-in format; returns the decoded row, or None for a
    missing-or-foreign id.

    Idempotent — deactivating an already-inactive template is a no-op that still
    returns the row, so a double-click is not an error."""
    row = get_template_by_id(company_id, template_id)
    if row is None:
        return None
    c = require_client()
    resp = (
        c.table("artifact_templates")
        .update({"is_active": False, "updated_at": _now_iso()})
        .eq("company_id", company_id)
        .eq("id", template_id)
        .execute()
    )
    _drop_planner_cache(company_id)
    return _decode(resp.data[0] if resp.data else {**row, "is_active": False})


def delete_template(company_id: str, template_id: str) -> dict | None:
    """Delete one company-owned template row; returns the decoded deleted row
    (the route needs `is_active` to decide whether the company just fell back to
    the built-in format), or None when the id is missing or belongs to another
    company — indistinguishable, like the by-id lookup. The delete itself is
    also company-filtered so a racing caller can never remove a foreign row."""
    row = get_template_by_id(company_id, template_id)
    if row is None:
        return None
    c = require_client()
    (
        c.table("artifact_templates")
        .delete()
        .eq("company_id", company_id)
        .eq("id", template_id)
        .execute()
    )
    _drop_planner_cache(company_id)
    return row


def get_active_template(company_id: str, artifact_type: str) -> dict | None:
    """The company's active format for one artifact type, or None.

    THE read generation will depend on (milestone 3), so it is the most
    conservative one in the module: it fetches every row for (company_id,
    artifact_type) and picks the active one IN PYTHON rather than asking the
    database to filter on a boolean. Server-side `.eq("is_active", True)`
    depends on how each engine round-trips a Python bool — real Postgres and the
    SQLite fake (tests/_fake_supabase.py) do not agree — and a silently empty
    result here would mean "no custom format", i.e. a whole company's documents
    quietly reverting to the built-in with nothing logged.

    The partial unique index guarantees at most one match; if a legacy row ever
    slipped past it, the newest wins (the list is ordered newest-first).

    Returns the row WHATEVER its compile_status, deliberately: the generation-time
    resolver has to gate on whether a usable skeleton exists, not on the status,
    or a recompile of the active format drops the whole company to the built-in
    for its duration. **The predicate that resolver wants is `compiled != ""`,
    not `compiled IS NOT NULL`** — the column is `text not null default ''`
    (migration :64), so a NULL check is always true and would hand generation an
    empty skeleton the moment a format was uploaded but never compiled."""
    c = require_client()
    resp = (
        c.table("artifact_templates")
        .select("*")
        .eq("company_id", company_id)
        .eq("artifact_type", artifact_type)
        .order("created_at", desc=True)
        .execute()
    )
    for raw in resp.data or []:
        row = _decode(raw)
        if row.get("is_active"):
            return row
    return None
