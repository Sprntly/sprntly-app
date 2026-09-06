"""Extraction results for run-scoped prose, held in this process only.

WHAT IT IS FOR. A model call is a draw, not a lookup — the discipline
`crucible.figure_class.classify_figures` states for figure classes, for exactly
this reason: two draws over one corpus produced two different committed totals,
so the same evidence ranked two different ways. Within a single run an attached
document is read once and the segments are extracted once, and any repeat of
the same bytes inside this process — a retry, a re-read, a second run started
before the worker recycles — gets the first answer rather than a second draw.

WHAT IT DELIBERATELY IS *NOT*: A DURABLE STORE.
-----------------------------------------------
This was built as a Postgres table first, and it was removed on purpose. The
reasoning is worth keeping here, because "add a table" is the obvious fix the
next person will reach for.

  * WITHIN-RUN RECOVERY DOES NOT NEED IT. The one place a run genuinely has to
    survive losing its prose claims is a stalled-enrichment sweep, and that is
    already solved without any new store: `routes.crucible._remember_prose`
    writes the rows a finding cites onto `crucible_runs.prioritisation` — the
    run's own record, scoped to it and deleted with it. A durable cache would
    have been a second answer to a question already answered.

  * SO A TABLE BUYS EXACTLY ONE THING: reproducibility ACROSS runs — the same
    document attached to two separate runs producing an identical claim set.

  * AND THE ENGINE DOES NOT PROMISE THAT ANYWHERE ELSE. The relevance gate
    stores its verdicts per run and judges fresh on a new one by design; that
    is why it was made draw-once WITHIN a run after a measured 29% swing in
    kept findings between identical runs. Giving attached prose a stronger
    guarantee than the rest of the Crucible has, through a cache, would be
    deciding a question about the whole engine as a side effect of a PDF
    reader — and would cost a migration on staging and prod to do it.

THE COST, STATED RATHER THAN IMPLIED: re-attaching the same document to a
SECOND run, in a fresh worker, re-extracts. That spends the model call again
and may yield a slightly different claim set. It matches the variability the
relevance gate already has, so it is consistent rather than a new weakness.

IF ACROSS-RUN REPRODUCIBILITY LATER BECOMES A REAL PRODUCT PROPERTY, this
module is the whole seam: `get`/`put` keep their signatures, a durable
implementation replaces the dict, and no caller changes. It should be decided
for the engine as a whole — the relevance gate and the figure classifier have
the same question open — rather than inherited from here.
"""
from __future__ import annotations

import logging
import threading
from collections import OrderedDict
from typing import Any, Optional

logger = logging.getLogger(__name__)

#: Segments kept. Bounded because this lives for the life of the process and
#: an unbounded dict of extraction results is a slow leak on a long-lived
#: worker. Sized for several full documents at
#: `prose.MAX_PROSE_FILES` x `prose.MAX_SEGMENTS_PER_FILE`, so a single run's
#: attachments can never evict each other mid-run — which is the one case
#: where a miss would cost a second draw over evidence already read.
MAX_ENTRIES = 512

_LOCK = threading.Lock()
#: `(enterprise_id, content_sha256, prompt_version) -> the model's output`.
#: THE TENANT IS IN THE KEY. This process serves every workspace, and a cache
#: keyed on the document hash alone would return one tenant's extracted claims
#: for another tenant's identically-worded file.
_ENTRIES: "OrderedDict[tuple[str, str, str], dict]" = OrderedDict()


def get(
    *, enterprise_id: str, content_sha256: str, prompt_version: str,
) -> Optional[dict]:
    """The cached extraction output for this text under this prompt, or None.

    THE MODEL'S WHOLE `{"signals": [...]}` OBJECT, not the array inside it, so
    a cache hit and a live call hand the caller identically-shaped data and
    there is no unwrapping that only one of the two paths performs.

    None means "call the model", and a real miss and a malformed entry are
    indistinguishable to the caller on purpose — the correct response to both
    is the same.
    """
    if not (enterprise_id and content_sha256 and prompt_version):
        return None
    key = (enterprise_id, content_sha256, prompt_version)
    with _LOCK:
        output = _ENTRIES.get(key)
        if output is None:
            return None
        # LRU: a document re-read inside a run stays warm while a document from
        # an hour ago is the one that ages out.
        _ENTRIES.move_to_end(key)
    if not isinstance(output, dict) or not isinstance(
            output.get("signals"), list):
        # Only reachable by a hand-written entry, and re-extracting is both
        # cheap and correct; passing it through would hand the projection
        # something it would have to defend against a second time.
        return None
    return output


def put(
    *, enterprise_id: str, content_sha256: str, prompt_version: str,
    output: Any,
) -> None:
    """Keep one extraction. Never raises — a cache may not fail a run."""
    if not (enterprise_id and content_sha256 and prompt_version):
        return
    if not isinstance(output, dict) or not isinstance(
            output.get("signals"), list):
        return
    key = (enterprise_id, content_sha256, prompt_version)
    with _LOCK:
        _ENTRIES[key] = output
        _ENTRIES.move_to_end(key)
        while len(_ENTRIES) > MAX_ENTRIES:
            _ENTRIES.popitem(last=False)


def clear() -> None:
    """Drop everything. For tests, which must not inherit each other's draws."""
    with _LOCK:
        _ENTRIES.clear()
