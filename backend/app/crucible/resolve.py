"""Resolving one claim id back to where it came from — a read, not a render.

A finding cites claim ids. A reader who wants to check one has no way to turn
that id into "this is what the tenant said, and here is where it was said" —
this module is exactly that lookup and nothing else. No rendering, no writes,
no LLM call: given a claim id and the run it was cited in, it returns an
ALLOWLISTED projection of the row, or says plainly that it cannot.

TWO ID SPACES. `kg_signal` primary keys are one; the run-scoped ids
`crucible.prose` mints for chat-attached evidence are the other, and they are
DELIBERATELY never written to `kg_signal` (see that module's docstring) — so a
lookup that only tries the table would report every prose-backed citation as
nonexistent. Both spaces are tried, tenant-scoped in every read.

THE PROJECTION IS AN ALLOWLIST, NOT A PASSTHROUGH. `properties` is never
returned: `properties.account` is the tenant's customer/partner name
(`app.graph.extractor.ACCOUNT_PROPERTY_KEY`), and handing back a row that
carries it defeats the same disclosure discipline the report itself now
applies to account names. `content` is safe to return — it is the extractor's
paraphrase of what was said, not raw transcript text, and it is already what
the report renders as the finding's own example.

THE SOURCE POINTER IS A HUMAN POINTER, NOT A UUID. A signal distilled from a
catalogued call points at `call_index` (title + date, the thing a reader could
actually go find); everything else points at `provenance["doc"]`, MINUS the
connector sync-batch label (`kg_ingest.runner._extraction_units`,
`f"{provider}-sync-batch-{i}"`) that covers most non-call volume and names no
document a human could open. See `_is_sync_batch_doc` for why that shape is
matched structurally rather than by an example list.

UNRESOLVED IS NEVER ONE STATE. `status` distinguishes three failures a caller
must be able to tell apart (`ClaimSource` docstring): the id existed in
neither space, the id was cited but the run's recovery blob dropped it for
size, and the id resolved to a real row that simply has no citable pointer.
Collapsing any two of these into one "unresolved" is the bug this module
exists to prevent.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any, Mapping, Optional

#: `kg_ingest.runner._extraction_units`'s `f"{provider}-sync-batch-{i}"`,
#: matched structurally. NOT a list of the examples measured on staging: a
#: hand-written list would silently miss the next connector's provider name,
#: while this shape is exactly what that f-string can ever produce — lower-
#: case provider, literal `-sync-batch-`, an integer batch index, nothing
#: else. A human-authored doc value (a filename, a Slack channel, a report
#: title) essentially never happens to match it.
_SYNC_BATCH_DOC = re.compile(r"^[a-z0-9_]+-sync-batch-\d+$")


def _is_sync_batch_doc(doc: str) -> bool:
    """True when `doc` is a connector sync-batch counter, not a document a
    human could open — see the module docstring and `_SYNC_BATCH_DOC`."""
    return bool(_SYNC_BATCH_DOC.match(doc.strip()))


@dataclass(frozen=True)
class ClaimSource:
    """The answer to "where did this claim come from" — always exactly one of
    four `status` values, never an exception and never a blank result.

    * ``"not_found"`` — the id is in neither `kg_signal` nor this run's prose
      claims, for this tenant. Indistinguishable, ON PURPOSE, from an id that
      exists but belongs to a different enterprise: `resolve_claim_source`
      never learns the difference, because every read it performs is already
      scoped to the caller's own `company_id`.
    * ``"dropped_for_space"`` — the id was cited by a stored finding and once
      known, but `crucible.prose.rows_for_recovery`'s cap (`MAX_PERSISTED_ROWS`)
      meant it did not survive into what the run persisted. It existed; it is
      not recoverable now. Only reachable once
      `crucible.prose.truncated_ids_from_meta` exists on the running build —
      see the module-level `_HAS_TRUNCATED_IDS` note.
    * ``"no_pointer"`` — a real row was found (`content`/`source_type`/
      `valid_at` are populated) but it carries no pointer a human could act
      on: no linked call, and its only `provenance["doc"]` value is a sync-
      batch counter. Reported as resolved-but-uncitable, not as an error.
    * ``"resolved"`` — a real row with a usable `pointer`, either
      ``{"kind": "call", "title": ..., "call_date": ...}`` or
      ``{"kind": "doc", "label": ...}``.

    `properties` is never a field here — see the module docstring.
    """
    status: str
    content: Optional[str] = None
    source_type: Optional[str] = None
    valid_at: Optional[str] = None
    pointer: Optional[dict] = None


def _provenance(signal: Mapping[str, Any]) -> Mapping[str, Any]:
    """A signal's `provenance` as a mapping, whatever shape it arrived in.

    Delegates to `crucible.claims`'s own helper rather than re-implementing
    the str-or-dict tolerance a second time — the exact reuse this ticket
    asks for `_load_signals_by_id`, applied to the other shared read.
    """
    from app.crucible.claims import _provenance as _claims_provenance

    return _claims_provenance(signal)


def _call_pointer(company_id: str, source_call_id: Any) -> Optional[dict]:
    """`kg_signal.source_call_id` -> a human pointer, or None.

    Tenant-scoped in the query (`.eq("company_id", company_id)`), the same
    posture `crucible.backfill._load_call_accounts` documents: the id is a
    global bigint, not namespaced per tenant, so an unscoped lookup would
    happily resolve another company's call.

    None (never a placeholder) when the id is not a real int, the row is
    gone, or its title is blank — a blank title is not a pointer a human
    could act on, so this falls back to the doc-provenance check exactly as
    if no call were linked at all.
    """
    if not isinstance(source_call_id, int) or isinstance(source_call_id, bool):
        return None
    from app.db.client import require_client

    try:
        rows = (
            require_client().table("call_index")
            .select("title,call_date")
            .eq("company_id", company_id)
            .eq("id", source_call_id)
            .limit(1)
            .execute()
        ).data or []
    except Exception:  # noqa: BLE001 — a lookup failure degrades to "no
        # pointer", never to an error surfaced for a read this incidental.
        return None
    if not rows:
        return None
    title = str(rows[0].get("title") or "").strip()
    if not title:
        return None
    return {"kind": "call", "title": title, "call_date": rows[0].get("call_date")}


def _doc_pointer(signal: Mapping[str, Any]) -> Optional[dict]:
    """`provenance["doc"]` -> a human pointer, or None when it is a sync-batch
    counter (see the module docstring) or simply absent."""
    doc = str(_provenance(signal).get("doc") or "").strip()
    if not doc or _is_sync_batch_doc(doc):
        return None
    return {"kind": "doc", "label": doc}


def _pointer_for(company_id: str, signal: Mapping[str, Any]) -> Optional[dict]:
    """The best pointer this signal has: a linked call first (the one
    measured to be usable 97.4% of the time it exists), the doc label
    otherwise, `None` if neither is usable."""
    call_pointer = _call_pointer(company_id, signal.get("source_call_id"))
    if call_pointer is not None:
        return call_pointer
    return _doc_pointer(signal)


def _project(company_id: str, signal: Mapping[str, Any]) -> ClaimSource:
    """A found row (real or prose) -> its `ClaimSource`. Never reads
    `properties` — see the module docstring."""
    pointer = _pointer_for(company_id, signal)
    return ClaimSource(
        status="resolved" if pointer is not None else "no_pointer",
        content=signal.get("content"),
        source_type=signal.get("source_type"),
        valid_at=signal.get("valid_at"),
        pointer=pointer,
    )


#: Mirrors `crucible.prose.TRUNCATED_KEY` for a build that has not yet
#: merged the sibling fix defining it
#: (`fix/crucible/a-cut-option-can-cite-a-claim-that-was-never-saved`, which
#: adds `prose.truncated_ids_from_meta` and writes this key). NEVER a second
#: source of truth: `_truncated_ids` prefers the real helper the instant it
#: exists and only falls back to reading this key by hand until it does.
#: Delete this constant and the fallback branch below once that PR merges.
_FALLBACK_TRUNCATED_KEY = "prose_claims_truncated"


def _truncated_ids(meta: Mapping[str, Any]) -> list[str]:
    """The ids this run wanted to persist but could not fit under the cap.

    Prefers `crucible.prose.truncated_ids_from_meta` once it exists on the
    running build. Until then, reads `_FALLBACK_TRUNCATED_KEY` directly —
    same key, same shape — so "dropped for space" is a real, testable state
    of this resolver regardless of merge order between the two branches.
    """
    from app.crucible import prose

    reader = getattr(prose, "truncated_ids_from_meta", None)
    if reader is not None:
        return reader(meta)
    stored = (meta or {}).get(_FALLBACK_TRUNCATED_KEY)
    if not isinstance(stored, list):
        return []
    return [str(i) for i in stored if i]


def resolve_claim_source(
    company_id: str,
    run_id: int,
    claim_id: str,
    *,
    run_row: Optional[dict] = None,
) -> ClaimSource:
    """Where `claim_id` (cited by a finding on `run_id`) came from, for
    `company_id` only.

    `run_row` lets a caller that already fetched the run (the route does, to
    404 a run belonging to another tenant) pass it in rather than paying for
    a second tenant-scoped read — the same reuse `_row_meta`/`_meta_of`
    already practise on this row elsewhere in the route module.

    TENANCY. Every read here is scoped to `company_id`: the `kg_signal` read
    reuses `_load_signals_by_id`, which filters `enterprise_id` in the query;
    the prose read comes off `run_row`, which the caller must have already
    fetched with `crucible_runs.get(run_id, company_id)` (tenant-scoped by
    construction) or supplied as `None` for a run that is not this tenant's —
    either way, a claim id that belongs to another enterprise is
    indistinguishable from one that never existed (`ClaimSource.not_found`).
    """
    from app.routes.crucible import _load_signals_by_id

    signals = _load_signals_by_id(company_id, {claim_id})
    if signals:
        return _project(company_id, signals[0])

    if run_row is None:
        from app.db import crucible_runs as runs_db

        run_row = runs_db.get(run_id, company_id)

    meta: Mapping[str, Any] = {}
    if run_row is not None:
        from app.routes.crucible import _row_meta

        meta = _row_meta(run_row)

    from app.crucible import prose

    for row in prose.rows_from_meta(meta):
        if str(row.get("id") or "") == claim_id:
            return _project(company_id, row)

    if claim_id in _truncated_ids(meta):
        return ClaimSource(status="dropped_for_space")

    return ClaimSource(status="not_found")
