"""custom_artifacts — team documents of any kind (the "Others" library).

One row per document. Unlike every other artifact table, nothing here is a
by-product of a pipeline: a member asks for a leadership update, a launch plan,
a postmortem, and it is stored, edited and shared like any PRD or report.

TENANCY. Every read filters `company_id` IN THE QUERY rather than fetching by
id and comparing afterwards, so a foreign id returns None and the route turns
that into a 404 — "exists but not yours" is never distinguishable from "does
not exist". The backend holds the service-role key, so RLS is bypassed and this
filter IS the tenant boundary (the db/ticket_sets.py posture).

SHARING. Company-scoped, not creator-scoped: any member of the company can read
and write any document, which is the whole point of "shared within the team".
`created_by` / `updated_by` are attribution, never permission — no read or
write path filters on them, and a reviewer should treat any future code that
does as a bug unless explicit sharing has shipped.

CONCURRENCY. `version` starts at 1 and increments on every content write. A
writer may pass the version it started from as `base_version`; when the stored
row has moved on, the write is REFUSED (`VersionConflict`) instead of silently
overwriting a colleague's paragraph. Passing None opts out and means
last-write-wins, which is correct for a rename that cannot lose anything.

The compare-and-set is done as a filtered UPDATE (`eq("version", base)`), not
read-then-write, so two simultaneous saves cannot both observe version 3 and
both write version 4 — the loser matches zero rows and raises.
"""
from __future__ import annotations

import logging
from typing import Any

from app.custom_artifact_html import sanitize_artifact_html
from app.db.client import require_client, retry_on_disconnect, utc_now

logger = logging.getLogger(__name__)

# Columns a listing returns. `body_html` is deliberately absent: a list of N
# documents must not carry N full bodies over the wire (the same posture that
# keeps `html` out of the reports listing and `stories` out of ticket_sets).
_LIST_COLUMNS = (
    "id, kind, title, status, version, created_at, updated_at, "
    "conversation_id, workspace_id, created_by, updated_by"
)

# The three lifecycle states, matching prototypes / ticket_sets.
STATUSES = ("generating", "ready", "failed")

# Guard rail on a single stored body. Large enough that no real document hits
# it (a long leadership update is ~10-20KB of HTML) and small enough that a
# runaway paste or a looping generation cannot write a megabyte row into the
# shared database. Enforced here rather than in the route so every writer —
# HTTP save, LLM generation, future importers — is covered by one rule, and the
# route imports THIS constant rather than declaring its own so the two ceilings
# cannot drift apart.
MAX_BODY_CHARS = 400_000


class BodyTooLarge(ValueError):
    """A body exceeded `MAX_BODY_CHARS`.

    RAISED, never truncated. Slicing an over-long document to fit is the worst
    available outcome: the write succeeds, the user is told nothing, and they
    discover later that the end of their document is gone. Callers turn this
    into a 413.
    """


def _checked_body(body_html: str) -> str:
    """Sanitize, then bound. Every write goes through here.

    THE SANITIZER IS CALLED AT THE STORAGE CHOKEPOINT, not only by callers.
    Callers do sanitize, and the module docstring used to say so — but "every
    writer remembers" is a convention, and this content is rendered INLINE in a
    contenteditable where the sanitizer is the sole defence. A future importer,
    a backfill script or a new route that forgets is a stored-XSS bug; here it
    is structurally impossible. `sanitize_artifact_html` is idempotent, so
    callers that already sanitize pay nothing for the second pass.

    Bounding happens AFTER sanitizing because the sanitized string is what gets
    stored — see the route's note on `&`-expansion.
    """
    cleaned = sanitize_artifact_html(body_html)
    if len(cleaned) > MAX_BODY_CHARS:
        raise BodyTooLarge(f"body is {len(cleaned)} chars (max {MAX_BODY_CHARS})")
    return cleaned


class VersionConflict(RuntimeError):
    """A content write lost a compare-and-set against `version`.

    Carries the row as it now stands so the caller can tell the user WHO moved
    it and offer the current text, rather than just refusing.
    """

    def __init__(self, current: dict[str, Any] | None):
        super().__init__("custom artifact was modified by someone else")
        self.current = current


@retry_on_disconnect
def create_artifact(
    company_id: str,
    *,
    kind: str = "",
    title: str = "",
    body_html: str = "",
    status: str = "ready",
    workspace_id: str | None = None,
    conversation_id: int | None = None,
    created_by: str | None = None,
) -> dict[str, Any]:
    """Create a document and return the full row.

    `status='generating'` is used by the generation path, which creates the row
    BEFORE the multi-minute LLM call so the panel has an id to open and poll
    against — the ticket_sets lifecycle. A hand-created blank document is
    'ready' immediately because there is nothing to wait for.
    """
    row = {
        "company_id": company_id,
        "workspace_id": workspace_id,
        "conversation_id": conversation_id,
        "kind": (kind or "").strip()[:120],
        "title": (title or "").strip()[:300],
        "body_html": _checked_body(body_html or ""),
        "status": status if status in STATUSES else "ready",
        "created_by": created_by,
        "updated_by": created_by,
        # Stamped by the APP rather than left to the column default, matching
        # `utc_now()` on every other write in this module. The orphan sweep
        # compares `updated_at` against a cutoff string, so a row whose first
        # timestamp came from a database default is being compared against a
        # value written in a different format — which is fine in Postgres (a
        # real timestamptz comparison) and silently wrong anywhere the column
        # is text. One writer, one format, both environments.
        "created_at": utc_now(),
        "updated_at": utc_now(),
    }
    resp = require_client().table("custom_artifacts").insert(row).execute()
    rows = resp.data or []
    if not rows:
        raise RuntimeError("custom_artifacts insert returned no row")
    return rows[0]


@retry_on_disconnect
def get_artifact(company_id: str, artifact_id: int) -> dict[str, Any] | None:
    """One document with its body, or None when absent OR foreign."""
    rows = (
        require_client().table("custom_artifacts")
        .select("*")
        .eq("company_id", company_id)
        .eq("id", artifact_id)
        .limit(1)
        .execute()
        .data
        or []
    )
    return rows[0] if rows else None


@retry_on_disconnect
def list_artifacts_for_company(
    company_id: str, limit: int = 200
) -> list[dict[str, Any]]:
    """This company's documents, newest first, WITHOUT their bodies."""
    return (
        require_client().table("custom_artifacts")
        .select(_LIST_COLUMNS)
        .eq("company_id", company_id)
        .order("id", desc=True)
        .limit(limit)
        .execute()
        .data
        or []
    )


@retry_on_disconnect
def list_artifacts_for_conversation(
    company_id: str, conversation_id: int
) -> list[dict[str, Any]]:
    """The documents born in one chat, newest first.

    Company-scoped as well as conversation-scoped: conversation ids are
    sequential integers, so filtering on the id alone would hand a foreign
    tenant's document to anyone who guessed one.
    """
    return (
        require_client().table("custom_artifacts")
        .select(_LIST_COLUMNS)
        .eq("company_id", company_id)
        .eq("conversation_id", conversation_id)
        .order("id", desc=True)
        .execute()
        .data
        or []
    )


@retry_on_disconnect
def _current_version(company_id: str, artifact_id: int) -> int | None:
    """Just the version (None when absent/foreign).

    `get_artifact` selects `*`, which on this table means up to 400KB of
    `body_html` — pulled back on EVERY debounced autosave purely to compute
    `version + 1`. One column instead.
    """
    rows = (
        require_client().table("custom_artifacts")
        .select("version")
        .eq("company_id", company_id)
        .eq("id", artifact_id)
        .limit(1)
        .execute()
        .data
        or []
    )
    return int(rows[0]["version"]) if rows else None


def update_artifact(  # NOT @retry_on_disconnect — see below
    company_id: str,
    artifact_id: int,
    *,
    title: str | None = None,
    body_html: str | None = None,
    kind: str | None = None,
    base_version: int | None = None,
    updated_by: str | None = None,
) -> dict[str, Any] | None:
    """Apply an edit and return the new row (None when absent/foreign).

    Only the fields that are not None are written, so a title rename never
    touches the body and an autosave never touches the title.

    `base_version` is the version the editor started from. When given, the
    UPDATE is filtered on it: if the stored row has moved on, ZERO rows match
    and `VersionConflict` is raised carrying the current row. When omitted the
    write is unconditional (last-write-wins) — see the module docstring for why
    both modes exist.

    DELIBERATELY NOT `@retry_on_disconnect`, unlike every other function here.
    That decorator retries on `httpx.ReadError`, which fires AFTER the request
    was sent — so on a compare-and-set it can re-run a write that already
    landed, find its own bumped version, match zero rows, and report a
    CONFLICT for the user's own successful save. The editor would then tell
    them a colleague overwrote them and offer to discard text that is already
    stored: strictly worse than the transient error the retry exists to hide.

    A compare-and-set is not idempotent, so it cannot be transparently retried.
    A transport failure surfaces as an error and the editor retries from a
    fresh read, which is both correct and safe. The unconditional path (no
    `base_version`) IS idempotent, but it shares this function, and one rule
    that is always right beats two that depend on an argument.
    """
    patch: dict[str, Any] = {"updated_at": utc_now(), "updated_by": updated_by}
    if title is not None:
        patch["title"] = title.strip()[:300]
    if kind is not None:
        patch["kind"] = kind.strip()[:120]
    if body_html is not None:
        patch["body_html"] = _checked_body(body_html)

    # A pure-metadata write (nothing but updated_at/updated_by) must not burn a
    # version — the counter exists to detect CONTENT divergence, and bumping it
    # on a no-op edit would fail a colleague's next save for no reason.
    content_changed = any(k in patch for k in ("title", "kind", "body_html"))

    current_version = _current_version(company_id, artifact_id)
    if current_version is None:
        return None
    if content_changed:
        patch["version"] = current_version + 1

    q = (
        require_client().table("custom_artifacts")
        .update(patch)
        .eq("company_id", company_id)
        .eq("id", artifact_id)
    )
    if base_version is not None:
        # Compare-and-set. Two racing saves that both read version 3 cannot both
        # land: the second matches no row, because the first already wrote 4.
        q = q.eq("version", base_version)
    rows = q.execute().data or []
    if not rows:
        if base_version is not None:
            # Zero rows matched, and there are THREE ways to get here. Re-read
            # before deciding which, because the three want different answers:
            after = get_artifact(company_id, artifact_id)
            if after is None:
                # Deleted concurrently. NOT a conflict: the editor's conflict UI
                # exists to show "here is their version", and there is no
                # version to show. Reported as absent → 404, so the user is told
                # the document is gone rather than that a colleague overwrote
                # them.
                return None
            raise VersionConflict(after)
        # No base version and no matching row: the document was deleted between
        # the version read above and this write. Absent, not a conflict.
        return None
    return rows[0]


@retry_on_disconnect
def finish_artifact(
    company_id: str, artifact_id: int, *, title: str, body_html: str
) -> None:
    """Flip a `generating` document to `ready` with its generated body.

    Bypasses `update_artifact`'s compare-and-set deliberately: the generator is
    filling a row nobody has been able to edit yet (the panel shows a spinner
    while status is 'generating'), so there is no concurrent writer to lose to,
    and a version check here would fail the generation rather than protect
    anything.
    """
    current = get_artifact(company_id, artifact_id)
    if current is None:
        return
    require_client().table("custom_artifacts").update(
        {
            "title": (title or "").strip()[:300],
            "body_html": _checked_body(body_html or ""),
            "status": "ready",
            "error": None,
            # THE VERSION MOVES, and it has to. An editor that opened the row
            # while it was still `generating` holds version 1 and an empty
            # buffer; PATCH is not gated on status, so without this bump that
            # editor's next autosave passes the compare-and-set and replaces
            # the freshly written document with its empty buffer — precisely
            # the lost update the counter exists to catch. Bumping makes that
            # save 409 and offer the generated text instead.
            "version": int(current.get("version") or 1) + 1,
            "updated_at": utc_now(),
        }
    ).eq("company_id", company_id).eq("id", artifact_id).execute()


@retry_on_disconnect
def fail_artifact(company_id: str, artifact_id: int, error: str) -> None:
    """Record a failed generation. The stored message is for operators — the
    web maps failures onto its own recovery copy and never renders this."""
    require_client().table("custom_artifacts").update(
        {"status": "failed", "error": (error or "")[:500], "updated_at": utc_now()}
    ).eq("company_id", company_id).eq("id", artifact_id).execute()


@retry_on_disconnect
def delete_artifact(company_id: str, artifact_id: int) -> bool:
    """Delete a document. True when a row went, False when absent/foreign.

    Existence is established by a company-filtered READ rather than by counting
    what the DELETE returned. Deliberate: PostgREST returns the deleted rows,
    but that is a property of the transport's `Prefer: return=representation`
    rather than of the operation, and the same call through a client that does
    not set it returns nothing — which would report every successful delete as
    a 404. The read is one extra round trip for a boundary that does not
    depend on how the driver was configured.

    The DELETE stays company-filtered too, so a foreign id removes nothing even
    if the read above were ever wrong.
    """
    if get_artifact(company_id, artifact_id) is None:
        return False
    (
        require_client().table("custom_artifacts")
        .delete()
        .eq("company_id", company_id)
        .eq("id", artifact_id)
        .execute()
    )
    return True
