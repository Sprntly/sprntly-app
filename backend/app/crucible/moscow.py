"""MoSCoW, for the corpus RICE cannot size.

Sibling to `rice.py`, same discipline: every bucket is derived from the
corpus's own claim types, nothing is invented, and nothing here reorders a
finding — `pipeline._rank` already froze the order (I10) before this module
ever runs. This is arithmetic-free rendering over scores that already exist,
same as RICE's table.

WHY MOSCOW, NOT A NEW SCALE. `claims.AUTHORITATIVE_FOR` grants voting rights
on a corpus with no numeric source to exactly two claim types:
`constraint` ("this is stopping us") and `preference` ("we asked for this").
Those map onto MoSCoW's own vocabulary without a manufactured middle step —
`constraint` IS a MUST, `preference` IS a SHOULD/COULD — so the mapping is
the corpus's own claim taxonomy read straight through, not a scale invented
for this table (hand-graded against 26 real findings from the real pipeline
and found the resulting order defensible).

GRADED BY INDEPENDENT DOCUMENTS, NOT CLAIM COUNT. Measured why this
matters: the pipeline's own echo rule already kills exact restatements, but
what survives can still be one voice paraphrased six ways across a corpus
that is mostly restatement. A finding backed by twelve claims from one
document is one witness twelve times; a finding backed by three claims from
three documents is three witnesses once each — plainly stronger evidence
that a raw claim count rewards backwards. `surfaced_by` already carries the
distinct source documents on the finding (see `pipeline._sources_of`), so
this reuses it rather than re-deriving it — but it is PRE-FORMATTED for
display (`"doc-a (14)"`, `"doc-b (3)"`, and — past `MAX_NAMED_SOURCES` —
one trailing `"+2 more documents"` summary entry), so `len(surfaced_by)`
undercounts: `_document_count` below expands that trailing entry back into
the real number it summarises.

`?` MARKS A THIN MUST, NOT A DEMOTION. A single-claim, single-document
blocker is still real evidence of something stopping an account — dropping
it to SHOULD would be Impact quietly reading corroboration, an I1 violation
in a different costume. Flagging it instead says the same thing a human
reviewer would: confirm this one before you commit to it.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Optional, Sequence

#: Matches `pipeline._sources_of`'s overflow entry exactly (`f"+{n} more
#: documents"`). Anything else in `surfaced_by` is a real, already-unique
#: `"doc (n)"` entry and counts as one document.
_MORE_DOCS_RE = re.compile(r"^\+(\d+) more documents?$")

#: A finding earns a bucket from the STRONGEST claim type it carries — same
#: "strongest, not the average" rule as `rice.impact_for`, and for the same
#: reason: one blocked deal among ten descriptions is still about a blocked
#: deal.
_MUST_TYPES = frozenset({"constraint"})
_SHOULD_TYPES = frozenset({"preference"})

#: Below this many independent source documents, a MUST is real but thin —
#: said plainly with `?` rather than silently ranked as though it were as
#: well-attested as a MUST backed by several documents.
THIN_EVIDENCE_DOCS = 2


@dataclass(frozen=True)
class MoscowRow:
    """One finding's MoSCoW bucket, with the evidence behind it visible."""
    label: str
    bucket: str                 # "MUST" | "MUST?" | "SHOULD" | "COULD" | "unranked"
    bucket_basis: str
    doc_count: int
    reach: Optional[float]
    reach_unit: str


def bucket_for(claim_types: Sequence[str], doc_count: int) -> tuple[str, str]:
    """The bucket a finding earns, and why — mirrors `rice.impact_for`'s
    "strongest type decides, and says which"."""
    kinds = set(claim_types)
    if kinds & _MUST_TYPES:
        if doc_count < THIN_EVIDENCE_DOCS:
            return (
                "MUST?",
                "a stated blocker, but from a single source document — real, "
                "and worth confirming before you commit to it",
            )
        return (
            "MUST",
            f"a stated blocker, corroborated across {doc_count} independent "
            f"source documents",
        )
    if kinds & _SHOULD_TYPES:
        return (
            "SHOULD" if doc_count >= THIN_EVIDENCE_DOCS else "COULD",
            f"a stated preference, seen in {doc_count} "
            f"{'documents' if doc_count != 1 else 'document'}"
            if doc_count >= THIN_EVIDENCE_DOCS
            else "a stated preference, seen in a single source document",
        )
    return (
        "unranked",
        "neither a stated blocker nor a stated preference — describes the "
        "world rather than asking for or blocking something",
    )


def _document_count(surfaced_by: Sequence[str]) -> int:
    """How many independent documents `surfaced_by` actually names.

    `surfaced_by` arrives PRE-FORMATTED for display (`pipeline._sources_of`):
    up to `MAX_NAMED_SOURCES` real `"doc (n)"` entries, most-cited first, and
    — only once there are more documents than that — one trailing
    `"+K more documents"` summary. `len(surfaced_by)` would count that
    summary as ONE more document instead of the K it stands for, silently
    undercounting corroboration on exactly the findings with the MOST of it.
    """
    n = 0
    for s in surfaced_by:
        s = (s or "").strip()
        if not s:
            continue
        m = _MORE_DOCS_RE.match(s)
        n += int(m.group(1)) if m else 1
    return n


def moscow_for(
    *,
    label: str,
    reach: Optional[float],
    reach_unit: str,
    claim_types: Sequence[str],
    surfaced_by: Sequence[str],
) -> MoscowRow:
    doc_count = _document_count(surfaced_by)
    bucket, basis = bucket_for(claim_types, doc_count)
    return MoscowRow(
        label=label,
        bucket=bucket,
        bucket_basis=basis,
        doc_count=doc_count,
        reach=reach,
        reach_unit=reach_unit or "accounts",
    )


#: MoSCoW's own vocabulary, for anything that wants to validate a bucket
#: value against the real set rather than a magic string.
BUCKET_ORDER = ("MUST", "MUST?", "SHOULD", "COULD", "unranked")
