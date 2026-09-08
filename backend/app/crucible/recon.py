"""Look at the evidence BEFORE writing the plan. Deterministic, no model.

WHY A RECONNAISSANCE PASS EXISTS AT ALL. `plan.source_inventory` counts rows
per source type and reads no content, and the plan built from it could only
ever say how MUCH evidence there is. That is the wrong question. A run over a
book where two columns both claim to be the account's value, and disagree by
ten percent, does not have a volume problem; it has a problem no row count can
see, and a plan that promises to "read your revenue data" over that book is
promising to read the wrong column with great confidence.

So this pass reads the structure of the evidence — never its prose — and
returns what it saw as typed observations the planner can act on. The four
kinds it finds are the ones that change an answer rather than decorate it:

  · two columns that both claim to be the value, where a third explains the gap
  · a coded field the coder gave up on, where the human wrote it down anyway
  · two funnel stages that are the same number written twice
  · activity concentrated in accounts that are not where the money is

THERE IS NO MODEL IN THIS FILE, AND THAT IS THE POINT. Everything here is an
input to a model call one module over, and an observation is only worth
anything as a check on that call: `planner` refuses to ship a sentence
containing a number that did not come from an observation here. A recon pass
that itself asked a model would be verifying one draw against another.

IT IS ALSO NOT A DATAFRAME LIBRARY. Every check is parameterised over fields
and fires on any dataset with the same SHAPE — none of them knows the name of
a column or a customer in advance — but they are shaped like this product's
questions (accounts, money, funnels, coverage) rather than like a general
aggregation API. The bounds below exist because this now runs at the plan
gate, which used to return in about a second and must not become the expensive
thing it gates.
"""
from __future__ import annotations

import logging
import re
from collections import Counter
from dataclasses import asdict, dataclass, field, replace
from datetime import datetime
from pathlib import Path
from statistics import median
from typing import Any, Iterable, Mapping, Optional, Sequence

logger = logging.getLogger(__name__)

# ── BOUNDS. Stated, because this runs on the gate a reader is waiting at. ────

#: Rows read per table. Beyond this the checks sample the head, which is
#: enough for STRUCTURE — a column that is a duplicate of its neighbour is a
#: duplicate in the first two thousand rows too — and the sample size travels
#: on the observation so nothing claims to have read more than it did.
MAX_ROWS_SCANNED = 2_000
#: Columns profiled per table. The reconciliation check is cubic in this
#: number, so it is the one bound that actually protects the wall clock.
MAX_COLUMNS = 40
MAX_TABLES = 40
#: Observations kept per kind, and per table within a kind. The per-table cap
#: is what stops one wide sheet (a retention grid with twelve month columns)
#: from filling the whole budget and burying a single decisive finding in a
#: narrow one.
MAX_PER_KIND = 8
MAX_PER_TABLE_PER_KIND = 2

# ── THRESHOLDS. Every one is a judgement, so every one is named. ────────────

#: Rows must agree this often for a third column to be accepted as the thing
#: that explains a gap. Not 1.0: one bad row in a real export is a typo, not a
#: refutation of the relationship.
RECONCILE_MIN_AGREEMENT = 0.95
#: Relative tolerance when comparing two money figures. Floating-point export
#: noise, not a real difference.
NUMERIC_TOLERANCE = 1e-6
#: A gap smaller than this share of the larger column is not worth a step.
RECONCILE_MIN_MATERIALITY = 0.01
#: …and two columns that disagree on MORE than this share of rows are not two
#: readings of the same thing, they are two different things that happen to sum.
#: Without this bar the check "finds" that expansion revenue and total contract
#: value disagree on every row and reports that reading the smaller one
#: understates the book by 91% — arithmetically true, and not a fact about a
#: mistake anyone could make. Two columns that are genuinely rival readings of
#: one quantity agree on most rows and diverge on the rest; that IS the shape.
RECONCILE_MAX_DIFFERING_SHARE = 0.50

#: Average characters before a text column counts as free text rather than a
#: label. Measured against real exports: labels and names sit under 30, real
#: sentences sit well above it.
FREETEXT_MIN_LEN = 30
#: A coded field has few distinct values relative to how often it is filled.
#: Both bars, because a column with eleven distinct values out of eleven rows
#: is an identifier, not a code.
CODED_MAX_DISTINCT = 12
CODED_MAX_DISTINCT_RATIO = 0.5
#: How empty a coded field has to be before its emptiness is a finding.
CODING_GAP_MIN_MISSING_SHARE = 0.20
#: …and how often the free text IS there on exactly those rows. This is the
#: whole check: a coded field and its free text going missing TOGETHER is a
#: row that has not happened yet (an open deal has no loss reason and no loss
#: note), which is correct behaviour and must not fire.
CODING_GAP_MIN_TEXT_PRESENT = 0.60

#: How far the top groups' share of activity has to diverge from their share
#: of value before it is worth a step. 1.5 means "half again as much".
CONCENTRATION_MIN_RATIO = 1.5
#: Groups counted as "the top few". Three, because a decision-maker can hold
#: three account names in their head and cannot hold ten.
DEFAULT_TOP_N = 3

#: The fail-open sentinel `graph.triage` stamps when the triage call itself
#: errors. NOT a member of `TRIAGE_CATEGORIES`, so any reader has to tolerate
#: it — it means "we tried and could not", which is a different fact from "no
#: triage ran here" and is reported separately rather than folded into either.
TRIAGE_FAIL_OPEN = "uncategorized"

#: The categories that are a CUSTOMER speaking, as opposed to the company
#: describing itself. Declared here rather than inferred because it is a
#: judgement: a revenue answer resting almost entirely on internal product
#: documents is a real limitation of that answer, and naming which kinds count
#: as firsthand is what makes the limitation checkable rather than a vibe.
FIRSTHAND_CATEGORIES: frozenset[str] = frozenset({
    "customer_feedback", "support_ticket", "sales_deal", "escalation",
})

#: How many ordinal columns make a PERIOD GRID rather than a coincidence.
#: `month_1 … month_12`, `week_1 … week_8`, `d0 … d30` — a repeated prefix with
#: an incrementing integer suffix. A NAME SHAPE, not a name match: nothing here
#: knows the word "cohort", and the same detector fires on any periodic grid.
MIN_PERIOD_COLUMNS = 4
#: Rows that must agree on where the observation window ends before trailing
#: zeros are called censoring rather than genuine decline.
MIN_CENSORED_ROWS = 2

#: Column-name fragments that mean "this is money". A NAME heuristic, and it
#: is declared here rather than inferred so a reader can see exactly what the
#: engine will treat as value — inferring it from magnitude would read a
#: six-digit session count as revenue.
MONETARY_HINTS: tuple[str, ...] = (
    "usd", "acv", "arr", "mrr", "revenue", "amount", "value", "price",
    "billing", "billings", "spend", "cost", "eur", "gbp",
)
#: The narrower set that means "this is what an account is worth PER YEAR".
#: Separate from `MONETARY_HINTS` on purpose and used only by
#: `unit_value_derivable`: a deal `amount_usd` is money, but it is one
#: opportunity's size, not an account's annual value, and suppressing "what is
#: one account worth?" on the strength of it would answer the question with the
#: wrong number — worse than asking.
RECURRING_VALUE_HINTS: tuple[str, ...] = (
    "acv", "arr", "mrr", "annual", "contract_value", "subscription",
)
#: …and the fragments that mean "this column identifies a customer". Used only
#: to decide whether a per-unit value is DERIVABLE, which is the one place a
#: question gets suppressed rather than asked.
ACCOUNT_HINTS: tuple[str, ...] = ("account", "customer", "company", "client")

#: The observation kinds, closed and declared. A planner that switched on a
#: kind this set does not contain would be reasoning about something nothing
#: emits.
KINDS: tuple[str, ...] = (
    "value_columns_disagree",
    "coding_gap",
    "stage_collapse",
    "concentration_divergence",
    "unit_value_derivable",
    "censored_periods",
    "evidence_mix",
    "dating_unreliable",
    "account_attribution_gap",
    "monetary_coverage_gap",
    "source_concentration",
    "claim_mix",
    "priceable_coverage",
)

SEVERITIES: tuple[str, ...] = ("high", "medium", "low")


# ── THE EVIDENCE, IN A SHAPE THE CHECKS CAN READ ────────────────────────────


def source_label(name: str) -> str:
    """A storage key rendered as something a reader recognises.

    `03_product_analytics:activation_funnel` is a filename and a sheet name
    joined by a colon. It is the right handle for a join and the wrong thing to
    put in a document — a reader who sees it learns nothing and is reminded
    they are looking at a machine's notes. Keys stay on the step's parameters,
    where they are addressing an operation; prose gets this.
    """
    text = str(name or "").strip()
    if not text:
        return ""
    parts = [re.sub(r"^\d+[_-]", "", p).replace("_", " ").strip()
             for p in text.split(":") if p.strip()]
    return " — ".join(p for p in parts if p)


def origin_label(name: str) -> str:
    """The same, but the SOURCE only — no sheet. Several sheets of one
    workbook are one source to a reader, and counting them as several is how a
    plan claims eleven sources over four files."""
    head = str(name or "").split(":")[0]
    return re.sub(r"^\d+[_-]", "", head).replace("_", " ").strip()


@dataclass(frozen=True)
class Table:
    """One rectangle of evidence: a spreadsheet sheet, a CSV, or a set of
    signals whose `properties` share a shape.

    `columns` is kept SEPARATELY from the rows and in declared order, because
    two of the checks are about column ORDER and adjacency (a funnel is a
    sequence) and dict key order off a sparse row set is not the export's
    order.

    `as_of` and `as_of_rule` are NOT set by anything in this module — every
    constructor here (`make_table` and everything that calls it) leaves them
    at their defaults. Deriving a table's date is `crucible.evidence`'s job
    (`with_as_of`), kept out of this file so a table this module reads is
    read exactly as it always was; nothing here changes because a later stage
    learned how to date what it already produced.
    """
    name: str
    rows: tuple[Mapping[str, Any], ...]
    columns: tuple[str, ...]
    source_type: str = ""
    #: When this table is evidence OF — see `crucible.evidence.derive_as_of`.
    #: `None` until something derives it.
    as_of: Optional[datetime] = None
    #: Which of the three rules produced `as_of` — one of
    #: `crucible.evidence.AS_OF_RULE_*`, or `""` if `as_of` is `None`.
    as_of_rule: str = ""

    def values(self, column: str) -> list[Any]:
        return [r.get(column) for r in self.rows]

    @property
    def label(self) -> str:
        """The table's name as a person would say it."""
        return source_label(self.name)

    @property
    def origin(self) -> str:
        """The SOURCE this table came from, without the sheet."""
        return origin_label(self.name)


def make_table(
    name: str,
    records: Sequence[Mapping[str, Any]],
    *,
    columns: Sequence[str] = (),
    source_type: str = "",
) -> Table:
    """A `Table` from plain records, bounded and with a stable column order."""
    rows = tuple(dict(r) for r in list(records)[:MAX_ROWS_SCANNED]
                 if isinstance(r, Mapping))
    if columns:
        cols = tuple(dict.fromkeys(str(c) for c in columns))
    else:
        seen: dict[str, None] = {}
        for r in rows:
            for k in r:
                seen.setdefault(str(k), None)
        cols = tuple(seen)
    return Table(name=name, rows=rows, columns=cols[:MAX_COLUMNS],
                 source_type=source_type)


def tables_from_workbook(path: "str | Path", *, source_type: str = "") -> list[Table]:
    """One `Table` per sheet of an .xlsx.

    openpyxl rather than pandas, and read-only, because this reads STRUCTURE
    and pandas would helpfully coerce the structure away: a column that is
    empty on a third of its rows is the finding in `coding_gap`, and a NaN
    fill is exactly the wrong thing to do to it before looking. It also keeps
    dtypes, which `ingest.xlsx_to_md` does not — that path renders every cell
    through `str(v).strip()`, so a workbook that has been through it can no
    longer answer "is this column numeric", which is the first question every
    check here asks.
    """
    return read_workbook(path, source_type=source_type)[0]


def read_workbook(
    path: "str | Path", *, source_type: str = "",
) -> tuple[list[Table], tuple["UnreadFile", ...]]:
    """`tables_from_workbook`, plus the sheets the bound would not let it read.

    A WORKBOOK CAN BE BIGGER THAN THE WHOLE PASS. `wb.sheetnames[:MAX_TABLES]`
    is a real limit and a correct one, and taken alone it is also invisible:
    the file appears on the plan as read, with a table count that is simply
    smaller than the file. Reporting the remainder costs one comparison and
    turns a silent truncation into a stated one.
    """
    import openpyxl

    out: list[Table] = []
    unread: list[UnreadFile] = []
    wb = openpyxl.load_workbook(str(path), data_only=True, read_only=True)
    try:
        stem = Path(str(path)).stem
        if len(wb.sheetnames) > MAX_TABLES:
            unread.append(UnreadFile(
                name=Path(str(path)).name,
                reason=f"{len(wb.sheetnames) - MAX_TABLES} of its sheets were "
                       f"not read — this run reads at most {MAX_TABLES} tables",
            ))
        for sheet_name in wb.sheetnames[:MAX_TABLES]:
            ws = wb[sheet_name]
            rows = list(ws.iter_rows(values_only=True))
            if len(rows) < 2:
                continue
            header = [str(h).strip() if h is not None else "" for h in rows[0]]
            keep = [(i, h) for i, h in enumerate(header) if h][:MAX_COLUMNS]
            records = [
                {h: r[i] if i < len(r) else None for i, h in keep}
                for r in rows[1:MAX_ROWS_SCANNED + 1]
            ]
            out.append(make_table(
                f"{stem}:{sheet_name}", records,
                columns=[h for _, h in keep], source_type=source_type,
            ))
    finally:
        wb.close()
    return out, tuple(unread)


@dataclass(frozen=True)
class UnreadFile:
    """A file this run was handed and could NOT turn into a table.

    THE HALF THAT USED TO BE SILENT, AND WHY IT IS NOT OPTIONAL. Every reader
    below fails soft — an unreadable file costs its own table and never the
    pass — which is the right posture and was, on its own, a way of lying by
    omission: the plan lists what it read, so a file that produced nothing
    simply vanished, and a reader who attached six workbooks was shown three
    with nothing to say the other three were not among them. A plan may only
    promise what the engine does; it must equally state what it could not do.

    `reason` is PROSE, authored here rather than a code the client maps. The
    reason a file was not read is a fact this module holds and nothing
    downstream can reconstruct — a renderer given `"unsupported"` would have
    to re-derive "because it is a .pdf and this run reads spreadsheets", which
    is a second implementation of the same statement, free to drift.
    """
    name: str
    reason: str

    def to_json(self) -> dict:
        return {"name": self.name, "reason": self.reason}


#: The suffixes there is a reader for below. Anything else is a file this pass
#: has nothing to do with — not a failure, but not silence either.
TABULAR_SUFFIXES: tuple[str, ...] = (".xlsx", ".xls", ".csv")


def read_dir(
    directory: "str | Path", *, source_type: str = "",
) -> tuple[list[Table], tuple[UnreadFile, ...]]:
    """Every workbook and CSV in a directory, as tables — AND every file that
    did not become one, with the reason.

    Reuses `app.ds.staging`'s reading of what counts as an analysable upload
    rather than re-deciding it, so the plan gate and the data-science chat
    paths cannot disagree about which files an analysis "saw".

    Returns both halves because the caller needs both. `tables_from_dir` keeps
    the old single-value signature for the callers that only ever wanted the
    tables; nothing that needs to DISCLOSE what it skipped can use it.
    """
    from app.ds.staging import MAX_FILE_BYTES

    out: list[Table] = []
    unread: list[UnreadFile] = []
    #: The file whose sheets are currently at the end of `out` — so an
    #: overflow is attributed to the file that caused it rather than to
    #: whichever name happened to sort last.
    overflowed_by = ""
    files = [p for p in sorted(Path(str(directory)).iterdir()) if p.is_file()]
    for i, src in enumerate(files):
        if len(out) >= MAX_TABLES:
            # THE BOUND IS REPORTED, NOT MERELY APPLIED. Everything after this
            # point is a file the reader handed over and this pass will not
            # look at; stopping quietly is how an attachment becomes invisible.
            for rest in files[i:]:
                unread.append(UnreadFile(
                    name=rest.name,
                    reason=f"this run reads at most {MAX_TABLES} tables and "
                           f"had already reached that before this file",
                ))
            break
        if src.stat().st_size > MAX_FILE_BYTES:
            unread.append(UnreadFile(
                name=src.name,
                reason=f"it is larger than the "
                       f"{MAX_FILE_BYTES // (1024 * 1024)} MB a single file "
                       f"may be for this pass",
            ))
            continue
        suffix = src.suffix.lower()
        if suffix not in TABULAR_SUFFIXES:
            unread.append(UnreadFile(
                name=src.name,
                reason=(f"this pass reads the structure of spreadsheets and "
                        f"CSVs, and this is "
                        + (f"a {suffix} file" if suffix else "a file with no "
                           "extension")),
            ))
            continue
        before = len(out)
        try:
            if suffix in (".xlsx", ".xls"):
                sheets, over_sheets = read_workbook(src, source_type=source_type)
                out.extend(sheets)
                unread.extend(over_sheets)
            else:
                import csv as _csv

                with src.open(newline="", encoding="utf-8-sig") as fh:
                    rows = list(_csv.DictReader(fh))
                if rows:
                    out.append(make_table(
                        src.stem, rows, columns=list(rows[0].keys()),
                        source_type=source_type,
                    ))
        except Exception:  # noqa: BLE001 — an unreadable file is one fewer
            # table, never a failed plan. Same posture `ds.staging` takes.
            logger.warning("crucible recon: could not read %s", src.name,
                           exc_info=True)
            unread.append(UnreadFile(
                name=src.name, reason="it could not be opened"))
            continue
        if len(out) == before:
            # OPENED AND EMPTY IS NOT THE SAME AS UNOPENABLE, and the reader
            # can act on the difference: a workbook whose sheets are all one
            # row deep is a file to fix, not a file to re-attach.
            unread.append(UnreadFile(
                name=src.name,
                reason="it opened, but held no sheet with a header row and at "
                       "least one row of data under it",
            ))
        else:
            overflowed_by = src.name
    if len(out) > MAX_TABLES:
        # A single workbook can carry more sheets than the whole pass reads.
        unread.append(UnreadFile(
            name=overflowed_by,
            reason=f"{len(out) - MAX_TABLES} of its sheets were not read — "
                   f"this run reads at most {MAX_TABLES} tables",
        ))
        out = out[:MAX_TABLES]
    return out, tuple(unread)


def tables_from_dir(directory: "str | Path", *, source_type: str = "") -> list[Table]:
    """`read_dir`, tables only. Kept for callers that do not disclose."""
    return read_dir(directory, source_type=source_type)[0]


#: The `source_type` carried by a table that came from a file attached in
#: chat, as opposed to one reconstructed from the knowledge graph. It is the
#: only thing downstream has to distinguish the two, and both the plan's
#: opening sentence and its source inventory turn on it — an uploaded file is
#: read for one run and is NOT part of what the company has connected, and a
#: plan that presented the two identically would be claiming the first.
UPLOAD_SOURCE_TYPE = "upload"


def upload_filename(name: str, key: str = "") -> str:
    """A safe, on-disk filename for an attachment, keeping the name a person
    gave it.

    THE NAME IS LOAD-BEARING, WHICH IS WHY IT IS NOT SIMPLY THE KEY. Table
    names here are built from the file's stem, `source_label` turns that stem
    into the prose the plan shows, and a storage key's stem is a uuid — so
    reading the bytes under their key would produce a plan that tells the
    reader it read `9f3c1e2a-...`, which is no more use than not naming the
    source at all.

    The name is therefore CLIENT-SUPPLIED and treated as such: directory
    components are dropped, so it can only ever be a leaf; a name that is
    empty or reduces to nothing falls back to the key's own stem; and the
    EXTENSION comes from the key, never from the name, because the extension
    is what decides which reader runs and the key's extension is the one the
    write side validated. The name chooses a label; the key chooses a parser.
    """
    ext = Path(str(key or "")).suffix.lower()
    stem = Path(str(name or "").replace("\\", "/")).name
    stem = Path(stem).stem.strip().strip(".")
    stem = re.sub(r"[^\w.\- ]+", "", stem).strip()
    if not stem:
        stem = Path(str(key or "")).stem or "upload"
    return f"{stem}{ext}"


def tables_from_uploads(
    files: Sequence[tuple[str, bytes]], *, source_type: str = UPLOAD_SOURCE_TYPE,
) -> list[Table]:
    """Tables from files held in memory, named as the person named them.

    Materialises into a temporary directory and delegates to
    `tables_from_dir` RATHER THAN re-implementing the readers over byte
    streams. Two reasons, and the second is the one that matters: openpyxl and
    `csv` both want a path or a handle and would need separate call sites, and
    — far more importantly — an upload read through this function and the same
    upload read off disk must produce IDENTICAL tables. One reader, one size
    guard, one set of table names, so "what the plan saw" cannot depend on how
    the bytes arrived.

    The directory is removed when this returns; nothing here persists.
    """
    return read_uploads(files, source_type=source_type)[0]


def read_uploads(
    files: Sequence[tuple[str, bytes]], *, source_type: str = UPLOAD_SOURCE_TYPE,
) -> tuple[list[Table], tuple[UnreadFile, ...]]:
    """`tables_from_uploads`, plus every file that did not become a table.

    The disclosing half of the same pass — see `UnreadFile`. A caller that
    shows the reader what was read has to be able to show them what was not,
    and this is the only place that knows.
    """
    if not files:
        return [], ()
    import tempfile

    out: list[Table] = []
    unread: list[UnreadFile] = []
    with tempfile.TemporaryDirectory(prefix="crucible-upload-") as tmp:
        root = Path(tmp)
        used: set[str] = set()
        for name, data in files:
            if not data:
                unread.append(UnreadFile(
                    name=name, reason="the file arrived empty"))
                continue
            fname = name
            # Two attachments can share a filename; a second write would
            # otherwise silently replace the first and the plan would report
            # one table where the reader sent two.
            n = 1
            while fname in used:
                n += 1
                stem, dot, ext = fname.rpartition(".")
                fname = f"{stem} ({n}){dot}{ext}" if dot else f"{fname} ({n})"
            used.add(fname)
            try:
                (root / fname).write_bytes(data)
            except OSError:  # noqa: BLE001 — one unwritable name costs that
                # file, never the pass. Same posture as every reader here.
                logger.warning("crucible recon: could not stage upload %s", fname,
                               exc_info=True)
                unread.append(UnreadFile(
                    name=fname, reason="it could not be staged for reading"))
        out, skipped = read_dir(root, source_type=source_type)
    return out, tuple(unread) + skipped


def tables_from_signals(signals: Sequence[Mapping[str, Any]]) -> list[Table]:
    """Signals whose `properties` carry real numbers, grouped into tables.

    ONLY THE STRUCTURED ONES. A signal whose properties are empty is prose,
    and prose is what the rest of the pipeline is for — there is no structural
    check to run on it. `ds/analyses.py` already writes real numbers into
    `properties` for every anomaly it finds, which is the precedent this
    reads: the numbers are there, nothing had ever looked at them as a table.

    Grouped by `(source_type, kind)` because that pair is what makes a set of
    properties share a shape; grouping by source type alone mixes an anomaly's
    columns with a ticket's and produces a table that is mostly holes.
    """
    buckets: dict[tuple[str, str], list[Mapping[str, Any]]] = {}
    for s in signals:
        if not isinstance(s, Mapping):
            continue
        props = s.get("properties")
        if not isinstance(props, Mapping) or not props:
            continue
        key = (str(s.get("source_type") or ""), str(s.get("kind") or ""))
        buckets.setdefault(key, []).append(props)

    out: list[Table] = []
    for (source_type, kind), records in sorted(buckets.items()):
        if len(records) < 2:
            continue
        out.append(make_table(
            f"{source_type}:{kind}" if kind else source_type,
            records, source_type=source_type,
        ))
    return out[:MAX_TABLES]


# ── COLUMN PROFILING ────────────────────────────────────────────────────────


@dataclass(frozen=True)
class ColumnProfile:
    name: str
    kind: str            # "number" | "text" | "date" | "empty"
    n: int
    filled: int
    distinct: int
    avg_len: float

    @property
    def missing_share(self) -> float:
        return 0.0 if not self.n else 1.0 - (self.filled / self.n)

    @property
    def is_coded(self) -> bool:
        """Few distinct values, and few RELATIVE to how often it is filled.

        Both bars matter. Distinct-count alone calls an eleven-row date column
        a code; ratio alone calls a 350-row column with 60 distinct account
        names a code. A code is a small closed set that repeats.
        """
        if self.filled < 2 or self.kind == "empty":
            return False
        ratio = self.distinct / self.filled
        return self.distinct <= CODED_MAX_DISTINCT and ratio <= CODED_MAX_DISTINCT_RATIO

    @property
    def is_freetext(self) -> bool:
        return self.kind == "text" and self.avg_len >= FREETEXT_MIN_LEN


_DATE_RE = re.compile(r"^\d{4}-\d{2}(-\d{2})?$")


def _is_blank(v: Any) -> bool:
    return v is None or (isinstance(v, str) and not v.strip())


def _as_number(v: Any) -> Optional[float]:
    """A float, or None. `bool` is deliberately NOT a number here: a Yes/No
    column read as 1/0 would be averaged, ranked and reported as a quantity."""
    if isinstance(v, bool) or _is_blank(v):
        return None
    if isinstance(v, (int, float)):
        return float(v)
    return None


def profile(table: Table, column: str) -> ColumnProfile:
    vals = table.values(column)
    n = len(vals)
    present = [v for v in vals if not _is_blank(v)]
    numbers = [v for v in present if _as_number(v) is not None]
    strings = [v for v in present if isinstance(v, str)]
    if not present:
        kind = "empty"
    elif len(numbers) == len(present):
        kind = "number"
    elif strings and all(_DATE_RE.match(s.strip()) for s in strings) \
            and len(strings) == len(present):
        kind = "date"
    else:
        kind = "text"
    avg_len = (sum(len(s) for s in strings) / len(strings)) if strings else 0.0
    return ColumnProfile(
        name=column, kind=kind, n=n, filled=len(present),
        distinct=len({str(v) for v in present}), avg_len=avg_len,
    )


def profiles(table: Table) -> dict[str, ColumnProfile]:
    return {c: profile(table, c) for c in table.columns}


# ── THE OPERATIONS. Each is named by a registered primitive. ────────────────


def aggregate_by_group(
    table: Table, group_field: str, measure_field: str = "",
) -> dict[str, float]:
    """Total `measure_field` per group. An empty `measure_field` counts ROWS.

    Counting rows is not a degenerate case, it is the common one: "how many
    support tickets did this account file" is a volume measure with no column
    behind it, and it is exactly the measure that diverges from money.
    """
    out: dict[str, float] = {}
    for r in table.rows:
        g = r.get(group_field)
        if _is_blank(g):
            continue
        key = str(g).strip()
        if not measure_field:
            out[key] = out.get(key, 0.0) + 1.0
            continue
        v = _as_number(r.get(measure_field))
        if v is None:
            continue
        out[key] = out.get(key, 0.0) + v
    return out


def band_label(
    value: Optional[float], edges: Sequence[float],
) -> Optional[str]:
    """Which of ascending EDGES' bands VALUE falls into, as `"[lo, hi)"` —
    or `"[lo, hi]"` for the last band, so its own upper bound is never
    orphaned. `None` for a non-number, an edge list under two points, or a
    value outside `[edges[0], edges[-1]]`.

    A NAME, not a group. Grouping a banded numeric column is `aggregate_by_group`
    (or `median_by_group`, `rate_by_group`) run against a table where this has
    already relabelled the column — see `with_banded_column` — so a numeric
    dimension is grouped by exactly the same accumulator a coded one is,
    and neither of those functions needs to know banding exists.
    """
    if value is None or len(edges) < 2:
        return None
    lo0, hiN = edges[0], edges[-1]
    if value < lo0 or value > hiN:
        return None
    last = len(edges) - 2
    for i in range(len(edges) - 1):
        lo, hi = edges[i], edges[i + 1]
        if i == last:
            if lo <= value <= hi:
                return f"[{lo:g}, {hi:g}]"
        elif lo <= value < hi:
            return f"[{lo:g}, {hi:g})"
    return None


def with_banded_column(
    table: Table, field: str, edges: Sequence[float], *, as_field: str,
) -> Table:
    """TABLE, plus a new column AS_FIELD holding `field`'s `band_label`.

    The row-shape adapter that lets a numeric dimension be grouped the same
    way a coded one already is — REUSE, not a parallel engine. Nothing about
    `field` itself changes; a row whose value bands to `None` (blank, wrong
    type, or outside the edges) simply carries `None` in the new column, which
    `aggregate_by_group`'s own `_is_blank` check then excludes exactly as it
    excludes any other blank group key.
    """
    rows = tuple({**r, as_field: band_label(_as_number(r.get(field)), edges)}
                 for r in table.rows)
    columns = table.columns if as_field in table.columns \
        else table.columns + (as_field,)
    return replace(table, rows=rows, columns=columns)


def median_by_group(
    table: Table, group_field: str, measure_field: str,
) -> dict[str, float]:
    """Median `measure_field` per group. Sibling to `aggregate_by_group`,
    which totals: a median needs each group's whole list of values rather
    than a running sum, so it cannot share that function's accumulator, but
    it shares its blank-handling and its `str(v).strip()` group key.
    """
    buckets: dict[str, list[float]] = {}
    for r in table.rows:
        g = r.get(group_field)
        if _is_blank(g):
            continue
        v = _as_number(r.get(measure_field))
        if v is None:
            continue
        buckets.setdefault(str(g).strip(), []).append(v)
    return {k: median(vs) for k, vs in buckets.items()}


def count_present_by_group(
    table: Table, group_field: str, measure_field: str,
) -> dict[str, int]:
    """Rows per group where `measure_field` is a present NUMBER — exactly the
    denominator `median_by_group`'s own buckets are, and the denominator a
    mean built from `aggregate_by_group`'s sum divides by. A small accessor so
    a caller can ask "how many rows actually fed this number" without either
    of those functions needing to return anything but the number itself.
    """
    out: dict[str, int] = {}
    for r in table.rows:
        g = r.get(group_field)
        if _is_blank(g):
            continue
        if _as_number(r.get(measure_field)) is None:
            continue
        key = str(g).strip()
        out[key] = out.get(key, 0) + 1
    return out


def rate_by_group(
    table: Table, group_field: str, outcome_field: str, level: Any,
) -> dict[str, float]:
    """Share of each group's rows where `outcome_field` equals LEVEL — "what
    fraction of this group churned", not a total and not a mean.

    The DENOMINATOR is every row with a present group key, whether or not
    `outcome_field` is present on it — a blank outcome is a row that did not
    reach `level` and counts against the group the same way any other
    non-matching row does. Compared as a trimmed string, the same equality a
    coded field's own values are compared by, so `level="Yes"` matches a cell
    read as `" Yes "` the way a person reading the sheet would.
    """
    counted: dict[str, float] = {}
    matched: dict[str, float] = {}
    level_key = str(level).strip()
    for r in table.rows:
        g = r.get(group_field)
        if _is_blank(g):
            continue
        key = str(g).strip()
        counted[key] = counted.get(key, 0.0) + 1.0
        v = r.get(outcome_field)
        if not _is_blank(v) and str(v).strip() == level_key:
            matched[key] = matched.get(key, 0.0) + 1.0
    return {k: matched.get(k, 0.0) / n for k, n in counted.items()}


@dataclass(frozen=True)
class Concentration:
    """How much of a total sits in the biggest few groups."""
    group_field: str
    measure_field: str
    top_n: int
    top_groups: tuple[str, ...]
    top_total: float
    grand_total: float
    group_count: int

    @property
    def share(self) -> float:
        return 0.0 if not self.grand_total else self.top_total / self.grand_total

    @property
    def even_share(self) -> float:
        """What the top N would hold if everything were spread evenly — the
        only honest baseline for calling a share 'concentrated'."""
        return 0.0 if not self.group_count else min(self.top_n, self.group_count) / self.group_count


def concentration_share(
    table: Table, group_field: str, measure_field: str = "",
    top_n: int = DEFAULT_TOP_N,
) -> Concentration:
    totals = aggregate_by_group(table, group_field, measure_field)
    return _concentration(totals, group_field, measure_field, top_n)


def _concentration(
    totals: Mapping[str, float], group_field: str, measure_field: str,
    top_n: int,
) -> Concentration:
    # Ties broken by NAME, never by dict order: two accounts with the same
    # ticket count must not swap places between runs, or the plan citing them
    # stops being reproducible.
    ordered = sorted(totals.items(), key=lambda kv: (-kv[1], kv[0]))
    top = ordered[:max(1, top_n)]
    return Concentration(
        group_field=group_field, measure_field=measure_field, top_n=top_n,
        top_groups=tuple(g for g, _ in top),
        top_total=sum(v for _, v in top),
        grand_total=sum(totals.values()),
        group_count=len(totals),
    )


@dataclass(frozen=True)
class Divergence:
    """The top groups by ACTIVITY, and what share of the MONEY they hold."""
    group_field: str
    volume_label: str
    value_label: str
    top_groups: tuple[str, ...]
    volume_share: float
    value_share: float
    even_share: float
    group_count: int

    @property
    def ratio(self) -> float:
        return 0.0 if not self.value_share else self.volume_share / self.value_share


def concentration_divergence(
    volume_by_group: Mapping[str, float],
    value_by_group: Mapping[str, float],
    *,
    group_field: str,
    volume_label: str,
    value_label: str,
    top_n: int = DEFAULT_TOP_N,
) -> Optional[Divergence]:
    """Do the loudest groups and the most valuable groups agree?

    THE MOST CONSEQUENTIAL CHECK HERE, because getting it wrong is invisible.
    A run that ranks by how much a theme is MENTIONED reports the accounts
    that file the most tickets, and a reader takes that for where the money
    is. On a real book those are frequently different sets, and nothing in a
    row count can tell you so.

    Scored over the groups the two measures SHARE. An account with tickets and
    no contract cannot contribute to a comparison of the two, and including it
    on one side only would make the divergence an artefact of the join.
    """
    shared = set(volume_by_group) & set(value_by_group)
    if len(shared) < max(4, top_n + 1):
        return None
    vol = {g: volume_by_group[g] for g in shared}
    val = {g: value_by_group[g] for g in shared}
    vol_total, val_total = sum(vol.values()), sum(val.values())
    if vol_total <= 0 or val_total <= 0:
        return None
    top = _concentration(vol, group_field, volume_label, top_n).top_groups
    return Divergence(
        group_field=group_field,
        volume_label=volume_label, value_label=value_label,
        top_groups=top,
        volume_share=sum(vol[g] for g in top) / vol_total,
        value_share=sum(val[g] for g in top) / val_total,
        even_share=len(top) / len(shared),
        group_count=len(shared),
    )


@dataclass(frozen=True)
class Reconciliation:
    """Two columns that disagree, and the column that explains the gap."""
    base_field: str
    total_field: str
    explained_by: str
    rows_compared: int
    rows_differing: int
    rows_explained: int
    base_total: float
    total_total: float
    gap_total: float

    @property
    def agreement(self) -> float:
        return 0.0 if not self.rows_compared else self.rows_explained / self.rows_compared

    @property
    def gap_share(self) -> float:
        return 0.0 if not self.total_total else self.gap_total / self.total_total


def reconcile_value_columns(
    table: Table, base_field: str, total_field: str,
    *, candidates: Sequence[str] = (),
) -> Optional[Reconciliation]:
    """Find the third column that accounts for the gap between two others.

    A GENERAL RELATIONSHIP, not a rule about revenue: `base + c == total` is
    the shape of every partitioned total there is (base plus expansion, list
    minus discount, gross minus refunds). What makes it worth a plan step is
    that it identifies WHICH of two plausible value columns is the whole
    number — and picking the wrong one understates the book by exactly the
    third column, silently, on every downstream figure.
    """
    cols = list(candidates) if candidates else [
        c for c in table.columns
        if c not in (base_field, total_field) and profile(table, c).kind == "number"
    ]
    pairs: list[tuple[float, float]] = []
    for r in table.rows:
        b, t = _as_number(r.get(base_field)), _as_number(r.get(total_field))
        if b is None or t is None:
            continue
        pairs.append((b, t))
    if len(pairs) < 4:
        return None
    differing = sum(1 for b, t in pairs if not _close(b, t))
    if not differing:
        return None

    for c in cols:
        explained = 0
        compared = 0
        for r in table.rows:
            b, t = _as_number(r.get(base_field)), _as_number(r.get(total_field))
            x = _as_number(r.get(c))
            if b is None or t is None or x is None:
                continue
            compared += 1
            if _close(b + x, t):
                explained += 1
        if compared >= 4 and explained / compared >= RECONCILE_MIN_AGREEMENT:
            base_total = sum(b for b, _ in pairs)
            total_total = sum(t for _, t in pairs)
            return Reconciliation(
                base_field=base_field, total_field=total_field, explained_by=c,
                rows_compared=compared, rows_differing=differing,
                rows_explained=explained,
                base_total=base_total, total_total=total_total,
                gap_total=total_total - base_total,
            )
    return None


def _close(a: float, b: float) -> bool:
    return abs(a - b) <= max(NUMERIC_TOLERANCE, abs(b) * NUMERIC_TOLERANCE)


@dataclass(frozen=True)
class Coverage:
    """How often a field is actually filled in."""
    field_name: str
    n: int
    filled: int

    @property
    def missing_share(self) -> float:
        return 0.0 if not self.n else 1.0 - (self.filled / self.n)


def field_coverage(table: Table, field_name: str) -> Coverage:
    vals = table.values(field_name)
    return Coverage(field_name=field_name, n=len(vals),
                    filled=sum(1 for v in vals if not _is_blank(v)))


def identical_columns(table: Table, fields: Sequence[str]) -> bool:
    """Do these columns hold the same value on every row?

    Requires more than one DISTINCT value across them, because two columns
    that are both zero everywhere are identical and mean nothing — that is an
    empty sheet, not a collapsed funnel stage.
    """
    fields = [f for f in fields if f in table.columns]
    if len(fields) < 2:
        return False
    seen: set[float] = set()
    compared = 0
    for r in table.rows:
        vals = [_as_number(r.get(f)) for f in fields]
        if any(v is None for v in vals):
            return False
        compared += 1
        first = vals[0]
        if any(not _close(v, first) for v in vals[1:]):
            return False
        seen.add(first)
    return compared >= 2 and len(seen) >= 2


def prefer_field(table: Table, prefer: str, over: str) -> str:
    """Which of two columns describing the same thing to actually use.

    Deliberately trivial and deliberately real: it returns `prefer` when that
    column is at least as complete as `over`, and `over` otherwise. The value
    is not the arithmetic, it is that a plan step naming this operation is
    naming a decision the run will actually make and record, rather than a
    preference stated in prose and then quietly not applied.
    """
    a, b = field_coverage(table, prefer), field_coverage(table, over)
    return prefer if a.filled >= b.filled else over


# ── OBSERVATIONS ────────────────────────────────────────────────────────────


@dataclass(frozen=True)
class Observation:
    """One thing the recon pass saw, in a shape the planner can both act on
    and be checked against.

    `figures` IS THE LOAD-BEARING FIELD. `what` is prose for a reader, but
    every number in it also appears here under a name — and the planner's
    verification step will only accept a figure in a generated sentence if it
    can find it in some observation's `figures`. Storing the numbers only
    inside the sentence would make that check a substring match on formatted
    text, which passes for the wrong reasons.
    """
    id: str
    kind: str
    severity: str
    source: str
    fields: tuple[str, ...]
    what: str
    figures: Mapping[str, float] = field(default_factory=dict)

    @property
    def source_label(self) -> str:
        """The source as a reader would say it — never the storage key."""
        return source_label(self.source)

    def to_json(self) -> dict:
        d = asdict(self)
        d["fields"] = list(self.fields)
        d["source_label"] = self.source_label
        d["figures"] = {k: float(v) for k, v in self.figures.items()}
        return d


@dataclass(frozen=True)
class SourceCoverage:
    name: str
    source_type: str
    records: int
    columns: int
    earliest: str = ""
    latest: str = ""

    @property
    def label(self) -> str:
        return source_label(self.name)

    @property
    def origin(self) -> str:
        return origin_label(self.name)

    def to_json(self) -> dict:
        #: `label` and `origin` ride along in the stored blob so a renderer
        #: never has to reconstruct them from the key — and so a plan read
        #: back years from now still has the words, not just the filename.
        d = asdict(self)
        d["label"] = self.label
        d["origin"] = self.origin
        return d


@dataclass(frozen=True)
class ReconReport:
    observations: tuple[Observation, ...] = ()
    sources: tuple[SourceCoverage, ...] = ()
    #: Source types nothing connected can witness at all. Named rather than
    #: implied by absence, because the plan says what it CANNOT do and an
    #: absence the reader has to infer is not a disclosure.
    missing: tuple[str, ...] = ()
    total_records: int = 0
    #: FILES THIS PASS WAS HANDED AND COULD NOT READ, with the reason.
    #:
    #: THE SAME DISCLOSURE RULE AS `missing`, APPLIED ONE LEVEL DOWN. That
    #: field exists because an absent source type inferred from a gap in a
    #: list is not a disclosure; an attachment that produced no table is the
    #: identical failure with a worse blast radius, because the reader CHOSE
    #: the file and is entitled to assume it was read. Empty on every report
    #: built before this field existed and on every pass that read everything.
    unread: tuple[UnreadFile, ...] = ()
    #: ATTACHED DOCUMENTS READ AS PROSE — `crucible.prose.ProseDocument`s.
    #:
    #: Carried on this report rather than returned beside it because the plan
    #: builds from ONE object and a second channel is a second thing for a
    #: caller to forget: `unread` is already here for the files this pass could
    #: not read, and a document read as ten conversations belongs in the same
    #: place as the document that could not be opened at all.
    #:
    #: NOT OBSERVED, and that distinction is the point. `observe()` runs
    #: structural checks over rectangles; prose has no columns to reconcile and
    #: contributes no `Observation`, so it cannot change which prioritisation
    #: framework the run picks or close a data gap the company genuinely has.
    #: It contributes CLAIMS, after approval, and nothing before then.
    #:
    #: Typed loosely to keep this module free of a `crucible.prose` import —
    #: that module reads `UnreadFile` from here, and the pair must not become a
    #: cycle.
    prose: tuple[Any, ...] = ()

    def summary(self) -> dict:
        """The coverage facts a reader needs in one line, aggregated.

        The plan gate renders a stat strip from this — how much was read, over
        what window, from how many places. Aggregated HERE rather than on the
        client because the window is a min/max across every table and a
        renderer that recomputed it would be a second implementation of the
        same fact, free to disagree with the one the run used.

        WHAT IS DELIBERATELY NOT HERE: what is missing entirely. `RunPlan.
        cannot_answer` already carries that, with the reason and the remedy
        attached — a bare list of absent source types beside it would be the
        same fact twice, once without the half that makes it actionable.
        """
        # TO THE MONTH, BOTH ENDS. Tables date themselves differently — a
        # cohort column is `YYYY-MM` and a ticket column is `YYYY-MM-DD` — so
        # the raw min and max come back in two different shapes and render as
        # "2024-07 to 2026-09-02", which reads as a bug. The window is a
        # coarse fact and the month is the honest resolution for it.
        spans = [s.earliest[:7] for s in self.sources if s.earliest]
        ends = [s.latest[:7] for s in self.sources if s.latest]
        return {
            "records": self.total_records,
            "tables": len(self.sources),
            "origins": len({s.origin for s in self.sources if s.origin}),
            "earliest": min(spans) if spans else "",
            "latest": max(ends) if ends else "",
        }

    def inventory_figures(self) -> tuple[float, ...]:
        """The run's countable facts about ITSELF — how many tables, how many
        sources, how many records.

        SEPARATE FROM AN OBSERVATION, AND DELIBERATELY SO. An observation is a
        claim about the CONTENT of the evidence and has to be earned by a
        check. "You connected six sources" is a fact about the inventory, it
        is computed here from the same list the plan renders, and it is as
        checkable as anything a check produced. Without this the figure gate
        deletes the plan's own opening sentence for citing the number of
        sources it is about to read — which it did, before this existed.
        """
        origins = {s.origin for s in self.sources if s.origin}
        return (
            float(len(self.sources)),
            float(len(origins)),
            float(self.total_records),
        )

    def of_kind(self, kind: str) -> tuple[Observation, ...]:
        return tuple(o for o in self.observations if o.kind == kind)

    def to_json(self) -> dict:
        return {
            "observations": [o.to_json() for o in self.observations],
            "sources": [s.to_json() for s in self.sources],
            "missing": list(self.missing),
            "total_records": self.total_records,
            "unread": [u.to_json() for u in self.unread],
        }


def observations_from_json(blob: Any) -> tuple[Observation, ...]:
    """Rebuild stored observations. Anything malformed is skipped rather than
    raised on: a plan stored before this shipped has no observations at all,
    and that must read as 'none', never as a broken run."""
    if not isinstance(blob, list):
        return ()
    out: list[Observation] = []
    for item in blob:
        if not isinstance(item, Mapping):
            continue
        figures = item.get("figures")
        out.append(Observation(
            id=str(item.get("id") or ""),
            kind=str(item.get("kind") or ""),
            severity=str(item.get("severity") or "low"),
            source=str(item.get("source") or ""),
            fields=tuple(str(f) for f in (item.get("fields") or [])),
            what=str(item.get("what") or ""),
            figures={
                str(k): float(v) for k, v in (figures or {}).items()
                if isinstance(v, (int, float)) and not isinstance(v, bool)
            } if isinstance(figures, Mapping) else {},
        ))
    return tuple(out)


def _pct(x: float) -> str:
    return f"{x * 100:.1f}%"


def _money(x: float) -> str:
    return f"{x:,.0f}"


# ── THE FOUR STRUCTURAL CHECKS, AS OBSERVATION PRODUCERS ────────────────────


def _observe_value_columns(table: Table, prof: Mapping[str, ColumnProfile]) -> list[Observation]:
    """Two columns that both claim to be the value, and the third that
    explains the gap between them."""
    money = [c for c in table.columns
             if prof[c].kind == "number" and _looks_monetary(c)]
    out: list[Observation] = []
    for i, base in enumerate(money):
        for total in money[i + 1:]:
            rec = reconcile_value_columns(table, base, total)
            if rec is None:
                # The pair may simply be the other way round.
                rec = reconcile_value_columns(table, total, base)
            if rec is None or rec.gap_share < RECONCILE_MIN_MATERIALITY:
                continue
            if rec.rows_differing / max(1, rec.rows_compared) > RECONCILE_MAX_DIFFERING_SHARE:
                continue
            out.append(Observation(
                id=f"{table.name}:value_columns:{rec.base_field}:{rec.total_field}",
                kind="value_columns_disagree",
                severity="high" if rec.gap_share >= 0.05 else "medium",
                source=table.name,
                fields=(rec.base_field, rec.total_field, rec.explained_by),
                what=(
                    f"`{rec.base_field}` and `{rec.total_field}` both read as "
                    f"the account's value and disagree on "
                    f"{rec.rows_differing} of {rec.rows_compared} rows. The "
                    f"whole difference is `{rec.explained_by}`: they "
                    f"reconcile on {rec.rows_explained} of "
                    f"{rec.rows_compared} rows. Reading the smaller column "
                    f"understates the book by {_money(rec.gap_total)}, or "
                    f"{_pct(rec.gap_share)}."
                ),
                figures={
                    "rows_compared": float(rec.rows_compared),
                    "rows_differing": float(rec.rows_differing),
                    "rows_explained": float(rec.rows_explained),
                    "base_total": rec.base_total,
                    "total_total": rec.total_total,
                    "gap_total": rec.gap_total,
                    "gap_share": rec.gap_share,
                },
            ))
    return out


def _observe_coding_gap(table: Table, prof: Mapping[str, ColumnProfile]) -> list[Observation]:
    """A coded field the coder gave up on, where the human wrote it down
    anyway.

    The pairing matters and is the reason this is not just a null-rate report.
    A coded field empty on rows where its free text is ALSO empty is a row
    that has not happened yet — an open deal has no loss reason because it has
    not lost — and firing on that would produce a plan step chasing a hole
    that is not there.
    """
    coded = [c for c in table.columns
             if prof[c].is_coded and prof[c].missing_share >= CODING_GAP_MIN_MISSING_SHARE]
    texts = [c for c in table.columns if prof[c].is_freetext]
    out: list[Observation] = []
    for c in coded:
        for t in texts:
            missing_rows = [r for r in table.rows if _is_blank(r.get(c))]
            if not missing_rows:
                continue
            with_text = sum(1 for r in missing_rows if not _is_blank(r.get(t)))
            share_with_text = with_text / len(missing_rows)
            if share_with_text < CODING_GAP_MIN_TEXT_PRESENT:
                continue
            out.append(Observation(
                id=f"{table.name}:coding_gap:{c}:{t}",
                kind="coding_gap",
                severity="high" if prof[c].missing_share >= 0.30 else "medium",
                source=table.name,
                fields=(c, t),
                what=(
                    f"`{c}` is empty on {len(missing_rows)} of "
                    f"{prof[c].n} rows ({_pct(prof[c].missing_share)}), and "
                    f"on {with_text} of those the free-text `{t}` is filled "
                    f"in. Counting `{c}` alone silently drops "
                    f"{with_text} rows that do say something."
                ),
                figures={
                    "rows": float(prof[c].n),
                    "missing": float(len(missing_rows)),
                    "missing_share": prof[c].missing_share,
                    "missing_with_text": float(with_text),
                    "text_present_share": share_with_text,
                },
            ))
    return out


# ── THE KNOWLEDGE-GRAPH SIDE: checks that need no table at all ─────────────
#
# WHY THESE EXIST. Every check above needs columns, and a tenant whose evidence
# is call transcripts and Slack has none — so on a measured 1,275-signal
# corpus the whole pass produced exactly one observation and spent 128ms
# building and profiling seventeen tables that every check then declined to
# speak about. That is a yield problem, not a speed one: the facts a plan needs
# on a prose corpus are all THERE, in fields already fetched, and nothing was
# reading them.
#
# ALL FIVE ARE AGGREGATES OVER ROWS THE RUN ALREADY HAS. No new query, no new
# page, no extra round trip: `_load_signals` selects `properties`, `provenance`,
# `valid_at`, `created_at` and `kind` today, and these read what is already in
# hand.

#: Above this share of signals whose `valid_at` is just their `created_at`, the
#: corpus is dated by the ingest clock rather than by when anything happened,
#: and every date-based test is measuring our own backfill.
INGEST_CLOCK_SHARE = 0.6

#: How close `valid_at` and `created_at` must be to count as the same moment.
#: NOT an exact match: `valid_at` is stamped in Python when the Signal object
#: is built and `created_at` by the database on insert, with an embedding call
#: in between, so identical-in-intent timestamps routinely differ by seconds.
#: An exact second-prefix compare would miss the very pattern it looks for on
#: any tenant whose ingest is slightly slower than this one's.
INGEST_CLOCK_TOLERANCE_S = 120.0

#: Below this share of signals naming a thing, a run cannot weight by it —
#: it can only count. Not a quality bar and not tuned to a tenant: it is the
#: point past which a weighted total is decided by the handful of rows that
#: happen to carry the field, which is a worse answer than an honest count.
ATTRIBUTABLE_MIN_SHARE = 0.5

#: Below this share of what was read being PRICEABLE, a run counts rather than
#: weights, and says so.
#:
#: A DECISION, NOT A MEASUREMENT, and it is one constant rather than two on
#: purpose. It is set to `ATTRIBUTABLE_MIN_SHARE` because the engine should
#: tell one story about when it may weight: "below half of what I read, the
#: total is decided by whichever rows happen to carry the field" is the same
#: argument whether the field is an account name or a contract value, and two
#: different bars for two flavours of one question is a difference a reader
#: would have to be told about and could not check.
#:
#: IT IS DISCLOSED AS `weighting_threshold` ON THE STORED PLAN, beside the
#: share it was compared against — not as an `AssumedParam`, which this
#: comment used to claim. The only assumed parameters this engine emits are
#: `value_per_account`, so that sentence named a mechanism that does not
#: exist. The disclosure is real and the sentence was the wrong description of
#: it, which in a feature whose whole rule is "say only what you do" is not a
#: comment-level mistake.
WEIGHTING_MIN_PRICEABLE_SHARE = 0.5

#: What that share is a share OF, in words, declared ONCE.
#:
#: The plan states the denominator to the reader and the observation states it
#: too. Two copies of a sentence that names a unit is exactly the kind of
#: duplication that drifts silently: change the arithmetic in one place and the
#: other keeps describing the old one, with nothing to catch it because both
#: sentences still read fine.
WEIGHTING_DENOMINATOR = (
    "distinct accounts named in your evidence, other than your own"
)

#: How much of the corpus one document may account for before the row count
#: stops being a count of independent observations.
SOURCE_CONCENTRATION_MIN_RATIO = 1.5

#: Signals per distinct document, above which the corpus is a small shelf read
#: many times rather than a wide body of evidence.
SHELF_MIN_SIGNALS_PER_DOC = 20.0

#: Above this share of one claim kind, the corpus is mostly one sort of thing
#: and a reader should know which before they read a ranking of it.
CLAIM_MIX_MIN_TOP_SHARE = 0.5


def seconds_between(a: Any, b: Any) -> Optional[float]:
    """Absolute gap in seconds, or None if either side is unreadable."""
    from datetime import datetime, timezone

    parsed = []
    for value in (a, b):
        if not value:
            return None
        text = str(value).replace("Z", "+00:00")
        try:
            moment = datetime.fromisoformat(text)
        except ValueError:
            return None
        if moment.tzinfo is None:
            moment = moment.replace(tzinfo=timezone.utc)
        parsed.append(moment)
    return abs((parsed[0] - parsed[1]).total_seconds())


@dataclass(frozen=True)
class DatingReliability:
    signals: int
    ingest_clock: int

    @property
    def share(self) -> float:
        return 0.0 if not self.signals else self.ingest_clock / self.signals

    @property
    def unreliable(self) -> bool:
        return self.share >= INGEST_CLOCK_SHARE


def dating_reliability(signals: Sequence[Mapping[str, Any]]) -> DatingReliability:
    """Is this corpus dated by when we READ it rather than when it happened?

    `valid_at` defaults to now() at ingest and most pullers never set it, so a
    backfill gives thousands of signals the same few timestamps. Detected
    rather than assumed, because a tenant whose sources DO carry real dates
    should still get the full checks.

    MOVED HERE FROM THE ROUTE, WHICH STILL CALLS IT. The pipeline already acts
    on this — `_refute` skips its echo rule when the dates are the ingest clock,
    because otherwise every cluster looks like one conversation — and the plan
    now has to say the same thing to the reader. Two implementations of "are
    these dates real" would drift, and the bad direction is the likely one: a
    plan reporting a dating problem the run did not act on, or a run skipping
    its echo rule while the plan said the dates were fine.
    """
    same = 0
    total = 0
    for row in signals:
        if not isinstance(row, Mapping):
            continue
        total += 1
        gap = seconds_between(row.get("valid_at"), row.get("created_at"))
        if gap is not None and gap <= INGEST_CLOCK_TOLERANCE_S:
            same += 1
    return DatingReliability(signals=total, ingest_clock=same)


def dates_are_ingest_clock(signals: Sequence[Mapping[str, Any]]) -> bool:
    """The pipeline's own question, answered off the same measurement."""
    return bool(signals) and dating_reliability(signals).unreliable


@dataclass(frozen=True)
class FieldPresence:
    """How much of the corpus carries a given field at all."""
    field_path: str
    signals: int
    present: int
    #: Rows that DID carry the field and were still not counted, because the
    #: value named the tenant itself. Separated from the plain misses because
    #: the two are different facts about the corpus and only one of them is a
    #: decision this engine made: "3,691 signals never named anyone" is the
    #: data being thin, "2,357 named you" is us choosing not to count them.
    #: Rolling them together renders one number for two causes and hides the
    #: one a reader can act on.
    excluded: int = 0

    @property
    def share(self) -> float:
        return 0.0 if not self.signals else self.present / self.signals


def signal_field_presence(
    signals: Sequence[Mapping[str, Any]], *, path: Sequence[str],
    #: Account keys that name the TENANT rather than one of its customers —
    #: `claims.self_account_keys` output. Only the account path passes these;
    #: a company name is not a figure, so the monetary gap is measured with
    #: this empty and is unaffected.
    self_names: frozenset[str] = frozenset(),
) -> FieldPresence:
    """Count the signals carrying `path`, e.g. `("properties", "account")`.

    PARAMETERISED OVER THE PATH rather than written once per field, because
    "how much of this names an account" and "how much of this carries a
    figure" are the same question asked of two keys — and the next one will be
    a third key, not a third function.

    A ROW WHOSE ONLY ACCOUNT IS THE TENANT ITSELF HAS NOT ATTRIBUTED ANYTHING,
    and counting it is how the attribution gate came to pass on a corpus that
    should fail it: on a real tenant 67.6% of signals carried an account, but
    30.6% of those named the vendor, leaving a true 47.0% — under
    `ATTRIBUTABLE_MIN_SHARE`. The guard that stops the engine weighting an
    unattributable corpus was being satisfied by the vendor's own name.
    """
    present = 0
    total = 0
    excluded = 0
    for row in signals:
        if not isinstance(row, Mapping):
            continue
        total += 1
        cursor: Any = row
        for key in path:
            cursor = cursor.get(key) if isinstance(cursor, Mapping) else None
            if cursor is None:
                break
        if cursor is None or (isinstance(cursor, str) and not cursor.strip()):
            continue
        if self_names and isinstance(cursor, str):
            from app.crucible.claims import account_key
            if account_key(cursor) in self_names:
                # COUNTED ON THE WAY PAST. Dropping it silently is how the
                # measurement that justified this exclusion — 30.6% of the
                # attributed corpus — became invisible the moment it started
                # working.
                excluded += 1
                continue
        present += 1
    return FieldPresence(field_path=".".join(path), signals=total,
                         present=present, excluded=excluded)


@dataclass(frozen=True)
class Concentration2:
    """How much of a corpus rests on how few artifacts."""
    signals: int
    documents: int
    top_name: str
    top_count: int

    @property
    def top_share(self) -> float:
        return 0.0 if not self.signals else self.top_count / self.signals

    @property
    def even_share(self) -> float:
        return 0.0 if not self.documents else 1.0 / self.documents

    @property
    def per_document(self) -> float:
        return 0.0 if not self.documents else self.signals / self.documents


def source_concentration(
    signals: Sequence[Mapping[str, Any]],
) -> Concentration2:
    """How many distinct documents this corpus actually rests on.

    THE ROW COUNT IS NOT A COUNT OF OBSERVATIONS. A measured tenant had 1,275
    signals drawn from eleven documents, and every corroboration rule in the
    pipeline — "two independent claims", "not one conversation echoing" — reads
    differently once that is known. Reported as both the shelf size and the
    largest single document's share, because a corpus can be thin either by
    having few sources or by being dominated by one.
    """
    counts: dict[str, int] = {}
    total = 0
    for row in signals:
        if not isinstance(row, Mapping):
            continue
        total += 1
        prov = row.get("provenance")
        doc = prov.get("doc") if isinstance(prov, Mapping) else None
        name = str(doc).strip() if doc else ""
        if name:
            counts[name] = counts.get(name, 0) + 1
    if not counts:
        return Concentration2(signals=total, documents=0, top_name="", top_count=0)
    top_name, top_count = max(sorted(counts.items()), key=lambda kv: kv[1])
    return Concentration2(signals=total, documents=len(counts),
                          top_name=top_name, top_count=top_count)


@dataclass(frozen=True)
class ClaimMix:
    """What KIND of thing the corpus is mostly made of."""
    signals: int
    counts: Mapping[str, int]

    @property
    def top(self) -> tuple[str, int]:
        if not self.counts:
            return ("", 0)
        return max(sorted(self.counts.items()), key=lambda kv: kv[1])

    @property
    def top_share(self) -> float:
        return 0.0 if not self.signals else self.top[1] / self.signals


def claim_mix(signals: Sequence[Mapping[str, Any]]) -> ClaimMix:
    """The distribution of `kind` across a corpus.

    Distinct from `evidence_mix`, which reads the triage category the INGEST
    pass assigned to a document. This reads what the extractor decided each
    signal IS — and on a measured tenant 58% were `finding`, the engine's own
    inference, rather than anything a customer said.
    """
    counts: dict[str, int] = {}
    total = 0
    for row in signals:
        if not isinstance(row, Mapping):
            continue
        total += 1
        kind = str(row.get("kind") or "").strip()
        if kind:
            counts[kind] = counts.get(kind, 0) + 1
    return ClaimMix(signals=total, counts=counts)


# ── THE KNOWLEDGE GRAPH SIDE: what KIND of evidence this run is reading ─────


@dataclass(frozen=True)
class EvidenceMix:
    """The distribution of triage categories across a tenant's signals.

    WHY THIS IS WORTH A CHECK. Every structural check above needs columns, and
    a tenant whose evidence is call transcripts and Slack has none — so
    reconnaissance over a prose corpus yielded counts and dates and nothing a
    plan could act on. The triage pass has meanwhile been classifying every
    ingested document into a declared taxonomy and writing the answer to
    `provenance.triage_category`, and nothing has ever read it. It costs no
    extra query (both signal reads already select `provenance`), no tokens and
    no latency.

    WHAT IT ANSWERS THAT A ROW COUNT CANNOT: what the evidence IS. "Fourteen
    hundred signals" and "fourteen hundred signals of which four per cent are
    a customer speaking" support very different answers to a revenue question,
    and only the second lets a plan say so before the run rather than after.

    `uncategorized` IS NOT A CATEGORY. It is the fail-open marker, so it is
    counted apart from both the categorised and the uncategorised: "triage ran
    and failed" is a different fact from "triage never ran here", and merging
    them would overstate coverage.
    """
    signals: int
    categorised: int
    fail_open: int
    counts: Mapping[str, int]

    @property
    def coverage(self) -> float:
        return 0.0 if not self.signals else self.categorised / self.signals

    @property
    def top(self) -> tuple[str, int]:
        if not self.counts:
            return ("", 0)
        return max(sorted(self.counts.items()), key=lambda kv: kv[1])

    @property
    def firsthand(self) -> int:
        return sum(n for c, n in self.counts.items() if c in FIRSTHAND_CATEGORIES)


def evidence_mix(signals: Sequence[Mapping[str, Any]]) -> EvidenceMix:
    """Read `provenance.triage_category` off a tenant's signals.

    Total, and tolerant of every shape the writers actually produce: the key
    absent entirely (the checklist pass and the batched extract path both pin
    it to `None`), the fail-open sentinel, and a value outside the declared
    taxonomy should the taxonomy ever be versioned forward.
    """
    counts: dict[str, int] = {}
    total = fail_open = 0
    for sig in signals:
        if not isinstance(sig, Mapping):
            continue
        total += 1
        prov = sig.get("provenance")
        raw = prov.get("triage_category") if isinstance(prov, Mapping) else None
        if not isinstance(raw, str) or not raw.strip():
            continue
        value = raw.strip()
        if value == TRIAGE_FAIL_OPEN:
            fail_open += 1
            continue
        counts[value] = counts.get(value, 0) + 1
    return EvidenceMix(signals=total, categorised=sum(counts.values()),
                       fail_open=fail_open, counts=counts)


def _category_label(code: str) -> str:
    """The taxonomy's own one-line description, shortened to its head clause.

    Read from `graph.types` rather than restated, so a plan cannot describe a
    category in words the taxonomy no longer uses. Lazily imported: the
    structural checks have no business pulling in the graph package.
    """
    try:
        from app.graph.types import TRIAGE_CATEGORIES
    except Exception:  # noqa: BLE001 — a label is a nicety, never a failure
        return code.replace("_", " ")
    text = TRIAGE_CATEGORIES.get(code, "")
    if not text:
        return code.replace("_", " ")
    return text.split("—")[0].split(",")[0].strip().lower()


def _observe_dating(signals: Sequence[Mapping[str, Any]]) -> list[Observation]:
    """Dates that are the ingest clock, not the calendar.

    THE PIPELINE ALREADY ACTS ON THIS AND THE READER WAS NEVER TOLD. `_refute`
    skips its echo rule on a corpus like this, because otherwise every cluster
    looks like one conversation and the run returns nothing — with a reason
    stated confidently and false. That is the right behaviour and it is
    invisible: a plan that promises to weigh recent evidence more heavily, over
    a corpus whose dates are all the afternoon of the backfill, is promising
    something no code can do.
    """
    d = dating_reliability(signals)
    if not d.signals or not d.unreliable:
        return []
    return [Observation(
        id="knowledge_graph:dating",
        kind="dating_unreliable",
        severity="high",
        source="knowledge graph",
        fields=("valid_at", "created_at"),
        what=(
            f"{_pct(d.share)} of {d.signals:,} signals are dated within "
            f"{INGEST_CLOCK_TOLERANCE_S:.0f} seconds of when we read them, so "
            f"the dates record the import and not when anything happened. "
            f"Nothing here can be judged on recency, and the check that throws "
            f"out one conversation echoing is switched off, because over these "
            f"dates it would throw out everything."
        ),
        figures={
            "signals": float(d.signals),
            "ingest_clock": float(d.ingest_clock),
            "ingest_clock_share": d.share,
            "tolerance_seconds": float(INGEST_CLOCK_TOLERANCE_S),
        },
    )]


def _observe_attribution(
    signals: Sequence[Mapping[str, Any]],
    self_names: frozenset[str] = frozenset(),
) -> list[Observation]:
    """How much of the evidence names an account, and how much carries money.

    THE HONEST VERSION OF GRACEFUL DEGRADATION. A run that cannot attribute
    evidence to accounts cannot weight themes by the revenue behind them — it
    can only count how often something came up. It already does exactly that,
    silently, and a reader has no way to tell a considered count from a
    weighting that quietly failed. Stated up front, it is the difference
    between a plan that is trustworthy on a thin corpus and one that flatters
    itself: "weighting would cover 0.3% of what I read."

    WHERE THE SENTENCE STOPS IS THE POINT. It stops at the coverage and what
    the coverage means, and hands the consequence to the planner — which is
    the only layer that has the settled unit to condition it on. See the
    comment on the table below.
    """
    out: list[Observation] = []
    # `exclude_self` IS SET ON THE ACCOUNT PATH ONLY. The tenant's own name
    # disqualifies a row from having attributed anything; it says nothing
    # about whether that row carries a figure, so the monetary gap is counted
    # exactly as it always was.
    # AN OBSERVATION SAYS WHAT IT MEASURED. IT DOES NOT SAY WHAT THE RUN WILL
    # DO ABOUT IT, AND THESE TWO USED TO.
    #
    # The clauses that stood here — "themes can only be counted, never
    # weighted by the revenue behind them — and the report will say that is
    # what happened", and "nothing can be sized in money; every size is stated
    # in accounts touched" — are decisions, not measurements, and this pass is
    # not the thing that makes them. `weighting_verdict` does, later, from
    # `priceable_coverage` (priced accounts over accounts NAMED in the
    # evidence) and the recorded business model. Both are different ratios
    # over different denominators from the one measured here, and they move
    # independently: a transcript-heavy tenant with a contracts export has
    # most signals unattributed AND every named account priceable, which is
    # the common shape and is settled at `value`.
    #
    # So these sentences asserted a count on runs that weight. The planner's
    # own copies of them are gated on the settled unit; these were not, and
    # were invisible for as long as nothing rendered an observation outside
    # the gate. `report._observations_section` renders `what` verbatim, so
    # they would have reached the finished document of a weighted run — the
    # count claim in the same report whose findings are ranked by revenue.
    #
    # What is left is what this pass actually established: a coverage share,
    # and what that share means for anything that would rest on it. True at
    # either unit, because it is a fact about the evidence rather than a
    # forecast about the run.
    for path, kind, noun, consequence, exclude_self in (
        (("properties", "account"), "account_attribution_gap", "name an account",
         "weighting a theme by the revenue behind it would rest on that share "
         "of what was read and no more", True),
        (("properties", "amount"), "monetary_coverage_gap", "carry a figure",
         "the evidence itself cannot supply an amount — a figure in money has "
         "to come from somewhere else, or not at all", False),
    ):
        p = signal_field_presence(
            signals, path=path,
            self_names=self_names if exclude_self else frozenset())
        if not p.signals or p.share >= ATTRIBUTABLE_MIN_SHARE:
            continue
        out.append(Observation(
            id=f"knowledge_graph:{kind}",
            kind=kind,
            severity="high",
            source="knowledge graph",
            fields=(p.field_path,),
            what=(
                f"{p.present:,.0f} of {p.signals:,.0f} signals "
                f"({_pct(p.share)}) {noun}. At that coverage {consequence}."
                # NAMED WHENEVER IT MOVED THE NUMBER. Without this the reader
                # sees a coverage figure they cannot reconcile against their
                # own corpus and has no way to know a deliberate exclusion is
                # part of why it is low.
                + (f" A further {p.excluded:,.0f} name your own company and "
                   f"are not counted as an account."
                   if p.excluded else "")
            ),
            figures={
                "signals": float(p.signals),
                "present": float(p.present),
                "share": p.share,
                "self_excluded": float(p.excluded),
            },
        ))
    return out


def _observe_source_concentration(
    signals: Sequence[Mapping[str, Any]],
) -> list[Observation]:
    """How few documents a large row count actually rests on."""
    c = source_concentration(signals)
    if not c.documents or c.signals < 20:
        return []
    dominated = c.top_share >= c.even_share * SOURCE_CONCENTRATION_MIN_RATIO
    shelf = c.per_document >= SHELF_MIN_SIGNALS_PER_DOC
    if not dominated and not shelf:
        return []
    return [Observation(
        id="knowledge_graph:source_concentration",
        kind="source_concentration",
        severity="medium",
        source="knowledge graph",
        fields=("provenance.doc",),
        what=(
            f"{c.signals:,} signals come from {c.documents} documents — "
            f"{c.per_document:.0f} apiece — and the largest single document is "
            f"{_pct(c.top_share)} of everything. The row count is not a count "
            f"of independent observations, so two claims agreeing may be one "
            f"document read twice."
        ),
        figures={
            "signals": float(c.signals),
            "documents": float(c.documents),
            "per_document": c.per_document,
            "top_share": c.top_share,
            "even_share": c.even_share,
        },
    )]


def _kind_prose(kind: str) -> str:
    """A signal kind as a plural noun phrase — "deal blockers", not
    `deal_blocker`.

    NO TABLE, BECAUSE THE KINDS ARE ALREADY WORDS. Every one of them reads
    correctly once the underscore is a space and it is pluralised; inventing a
    lookup would be a vocabulary to maintain for no gain. What this stops is
    the raw key reaching a sentence, which is what did happen — a composed step
    called them "740 'finding' signals".
    """
    words = str(kind or "").replace("_", " ").strip() or "signals"
    return words if words.endswith("s") else f"{words}s"


def _observe_claim_mix(signals: Sequence[Mapping[str, Any]]) -> list[Observation]:
    """What the corpus is mostly made of."""
    m = claim_mix(signals)
    if m.signals < 20 or not m.counts or m.top_share < CLAIM_MIX_MIN_TOP_SHARE:
        return []
    top_kind, top_n = m.top
    return [Observation(
        id="knowledge_graph:claim_mix",
        kind="claim_mix",
        severity="medium",
        source="knowledge graph",
        fields=("kind",),
        what=(
            f"{_pct(m.top_share)} of the corpus is one kind of thing: "
            f"{top_n:,} of {m.signals:,} signals are "
            f"{_kind_prose(top_kind)}. A ranking over evidence that is mostly "
            f"one shape will reflect that shape."
        ),
        figures={
            "signals": float(m.signals),
            "top": float(top_n),
            "top_share": m.top_share,
            "kinds": float(len(m.counts)),
        },
    )]


def _observe_evidence_mix(mix: EvidenceMix) -> list[Observation]:
    """What kind of evidence this run is actually reading."""
    if mix.signals < 20 or not mix.counts:
        return []
    top_code, top_n = mix.top
    top_share = top_n / mix.categorised if mix.categorised else 0.0
    firsthand_share = mix.firsthand / mix.categorised if mix.categorised else 0.0

    detail = ""
    if mix.fail_open:
        detail = (f" A further {mix.fail_open} were attempted and the "
                  f"classifier failed, so they are counted as unclassified.")

    return [Observation(
        id="knowledge_graph:evidence_mix",
        kind="evidence_mix",
        severity="high" if firsthand_share < 0.15 else "medium",
        source="knowledge graph",
        fields=("triage_category",),
        what=(
            f"{mix.categorised:,} of {mix.signals:,} signals "
            f"({_pct(mix.coverage)}) are classified. The largest single kind "
            f"is {_category_label(top_code)} at {_pct(top_share)}, and "
            f"{_pct(firsthand_share)} is a customer speaking firsthand rather "
            f"than the company describing itself.{detail}"
        ),
        figures={
            "signals": float(mix.signals),
            "classified": float(mix.categorised),
            "coverage_share": mix.coverage,
            "unclassified": float(mix.signals - mix.categorised),
            "fail_open": float(mix.fail_open),
            "distinct_categories": float(len(mix.counts)),
            "top_share": top_share,
            "firsthand": float(mix.firsthand),
            "firsthand_share": firsthand_share,
        },
    )]


# ── PERIOD GRIDS: telling a retention curve from a funnel ───────────────────


@dataclass(frozen=True)
class PeriodGrid:
    """A cohort-by-period matrix: one row per cohort, one column per period.

    WHY THIS TYPE EXISTS AT ALL. Two different checks need the same structure
    for opposite reasons. `stage_collapse` has to RECOGNISE one so it can shut
    up — month 1 equalling the cohort size is retention starting at 100%, which
    is a definition, not a finding — and `censored_periods` has to recognise
    one so it can fire, because the trailing zeros in the same grid are the
    most expensive misreading available on the whole dataset.

    A SHAPE, NOT A VOCABULARY. Detection is a repeated column prefix with an
    incrementing integer suffix, values that do not increase along a row, and a
    RAGGED right edge. Nothing here matches on the word "cohort" or "month", so
    the same detector finds `week_1…week_8` and `d0…d30`.

    Raggedness is the discriminator that matters. A funnel named
    `step_1…step_5` is also wide, also ordinal and also non-increasing — and it
    is fully populated on every row, because every stage has happened. A time
    grid is ragged because the later periods have not happened yet, and that is
    exactly the difference between "this number is a drop" and "this number is
    not a number".
    """
    prefix: str
    columns: tuple[str, ...]
    key_field: str
    size_field: str
    #: Cohort key -> the last period index actually observed (1-based, 0 if
    #: none). "Observed" is the last non-zero cell: a run of trailing zeros in
    #: a ragged grid is the window ending, not a cohort reaching zero.
    last_observed: Mapping[str, int]
    #: Cohorts observed for the full width of the grid — the only ones a
    #: full-window rate may be computed over.
    mature: tuple[str, ...]
    #: True when the ragged rows agree on where the window ends.
    censored: bool = False

    @property
    def width(self) -> int:
        return len(self.columns)


_ORDINAL_RE = re.compile(r"^(.*?)[_\-]?(\d+)$")


def _ordinal_series(columns: Sequence[str],
                    prof: Mapping[str, ColumnProfile]) -> list[tuple[str, list[str]]]:
    """Groups of numeric columns sharing a prefix and an incrementing suffix."""
    groups: dict[str, list[tuple[int, str]]] = {}
    for c in columns:
        if prof[c].kind != "number":
            continue
        m = _ORDINAL_RE.match(c)
        if not m:
            continue
        groups.setdefault(m.group(1), []).append((int(m.group(2)), c))
    return [
        (prefix, [c for _, c in sorted(items)])
        for prefix, items in sorted(groups.items())
        if len(items) >= MIN_PERIOD_COLUMNS
    ]


def _period_index(value: Any) -> Optional[int]:
    """A `YYYY-MM` (or `YYYY-MM-DD`) label as a count of months.

    Months rather than a row position, because cohorts are not evenly spaced —
    a book that starts bimonthly and goes monthly would make rank-based
    arithmetic silently wrong, and the whole point of the censoring test is
    that the arithmetic is exact.
    """
    if _is_blank(value):
        return None
    m = re.match(r"^(\d{4})-(\d{2})", str(value).strip())
    if not m:
        return None
    return int(m.group(1)) * 12 + int(m.group(2))


def period_grid(table: Table,
                prof: Optional[Mapping[str, ColumnProfile]] = None) -> Optional[PeriodGrid]:
    """The cohort-by-period grid in this table, if it has one."""
    prof = prof or profiles(table)
    for prefix, cols in _ordinal_series(table.columns, prof):
        rows = table.rows
        if len(rows) < MIN_CENSORED_ROWS + 1:
            continue

        # Non-increasing along each row. A retention curve only falls; a series
        # that rises somewhere is a time series of something else.
        monotonic = 0
        for r in rows:
            vals = [_as_number(r.get(c)) for c in cols]
            if any(v is None for v in vals):
                continue
            if all(b <= a for a, b in zip(vals, vals[1:])):
                monotonic += 1
        if monotonic < len(rows):
            continue

        last: dict[str, int] = {}
        key_field = _period_key(table, prof)
        for r in rows:
            key = str(r.get(key_field)) if key_field else str(id(r))
            idx = 0
            for i, c in enumerate(cols, start=1):
                v = _as_number(r.get(c))
                if v is not None and v > 0:
                    idx = i
            last[key] = idx

        width = len(cols)
        ragged = [k for k, v in last.items() if 0 < v < width]
        if len(ragged) < MIN_CENSORED_ROWS:
            # Fully populated: a stage sequence, not a window that has not
            # finished. Nothing to suppress and nothing to warn about.
            continue

        # THE CENSORING SIGNATURE: every ragged cohort stops at the same
        # WALL-CLOCK moment. `cohort start + periods observed` is constant, and
        # that constant IS the last date the data covers. A cohort that merely
        # churned out early would break it.
        censored = False
        if key_field:
            frontiers = set()
            for r in rows:
                key = str(r.get(key_field))
                idx = last.get(key, 0)
                start = _period_index(r.get(key_field))
                if start is None or not (0 < idx < width):
                    continue
                frontiers.add(start + idx)
            censored = len(frontiers) == 1

        mature = tuple(sorted(k for k, v in last.items() if v >= width))
        return PeriodGrid(
            prefix=prefix, columns=tuple(cols), key_field=key_field or "",
            size_field=_period_size_field(table, prof, cols),
            last_observed=last, mature=mature, censored=censored,
        )
    return None


def _period_key(table: Table, prof: Mapping[str, ColumnProfile]) -> str:
    """The column naming each cohort — one row each, and parseable as a date."""
    for c in table.columns:
        if prof[c].kind not in ("date", "text"):
            continue
        if prof[c].distinct != prof[c].filled or prof[c].filled < 2:
            continue
        if all(_period_index(v) is not None
               for v in table.values(c) if not _is_blank(v)):
            return c
    return ""


def _period_size_field(table: Table, prof: Mapping[str, ColumnProfile],
                       cols: Sequence[str]) -> str:
    """The denominator: the numeric column outside the series that every first
    period sits inside. Chosen as the CLOSEST such column, because a retention
    grid's first period is usually the cohort size exactly."""
    first = cols[0]
    best, best_gap = "", None
    for c in table.columns:
        if c in cols or prof[c].kind != "number":
            continue
        gap = 0.0
        ok = True
        for r in table.rows:
            size, start = _as_number(r.get(c)), _as_number(r.get(first))
            if size is None or start is None or size < start:
                ok = False
                break
            gap += size - start
        if ok and (best_gap is None or gap < best_gap):
            best, best_gap = c, gap
    return best


def _observe_censored_periods(table: Table,
                              prof: Mapping[str, ColumnProfile]) -> list[Observation]:
    """Trailing zeros that are the calendar, not the customers.

    THE MOST EXPENSIVE MISREADING ON A RETENTION GRID, and it points the wrong
    way. Summing the last period over every cohort divides a number that only
    the mature cohorts could contribute to by a denominator that includes every
    cohort — including the ones signed last month, whose later periods are zero
    because they have not happened. The result invents a retention crisis and
    hides whatever the real number is.
    """
    grid = period_grid(table, prof)
    if grid is None or not grid.censored or not grid.mature or not grid.size_field:
        return []

    last_col = grid.columns[-1]
    mature_rows = [r for r in table.rows
                   if str(r.get(grid.key_field)) in set(grid.mature)]
    if not mature_rows:
        return []

    def _sum(rows, col):
        return sum(v for v in (_as_number(r.get(col)) for r in rows)
                   if v is not None)

    m_num, m_den = _sum(mature_rows, last_col), _sum(mature_rows, grid.size_field)
    n_num, n_den = _sum(table.rows, last_col), _sum(table.rows, grid.size_field)
    if m_den <= 0 or n_den <= 0:
        return []
    mature_rate, naive_rate = m_num / m_den, n_num / n_den
    immature = len(table.rows) - len(mature_rows)

    return [Observation(
        id=f"{table.name}:censored_periods:{grid.prefix}",
        kind="censored_periods",
        severity="high",
        source=table.name,
        fields=(grid.key_field, grid.size_field, last_col),
        what=(
            f"`{last_col}` is zero for {immature} of {len(table.rows)} cohorts "
            f"because those months have not happened yet, not because anyone "
            f"left: every one of them stops exactly at the end of the data. "
            f"Dividing the last period by every cohort gives "
            f"{_pct(naive_rate)}; over the {len(mature_rows)} cohorts old "
            f"enough to have a full window it is {_pct(mature_rate)} — a "
            f"{abs(mature_rate - naive_rate) * 100:.1f} point error, pointing "
            f"the wrong way."
        ),
        figures={
            "width": float(grid.width),
            "cohorts": float(len(table.rows)),
            "mature_cohorts": float(len(mature_rows)),
            "immature_cohorts": float(immature),
            "mature_numerator": m_num,
            "mature_denominator": m_den,
            "mature_rate": mature_rate,
            "naive_numerator": n_num,
            "naive_denominator": n_den,
            "naive_rate": naive_rate,
            "error_points": abs(mature_rate - naive_rate) * 100.0,
        },
    )]


def _observe_stage_collapse(table: Table, prof: Mapping[str, ColumnProfile]) -> list[Observation]:
    """Two adjacent stages holding identical values across every row.

    ADJACENT ONLY, in the export's own column order, because that order IS the
    funnel. Comparing every numeric pair would report that two unrelated
    columns happen to match and call it a collapsed stage.
    """
    out: list[Observation] = []
    # A PERIOD GRID IS NOT A FUNNEL, AND ADJACENT EQUALITY MEANS NOTHING IN
    # ONE. On a real retention grid this check fired twice and both were
    # definitions rather than findings: `month_1` equals `cohort_size` because
    # retention starts at 100%, and `month_1` equals `month_2` because nobody
    # happened to leave in the first month. Reporting either as "one
    # measurement recorded twice" is a false alarm at the top of the plan,
    # which is worse than silence — it spends the reader's trust on a
    # non-event.
    #
    # Suppressed for the whole grid, its denominator included, because the
    # grid's SHAPE is what makes adjacent equality meaningless there. A funnel
    # is untouched: it is fully populated, so `period_grid` does not match it.
    grid = period_grid(table, prof)
    in_grid = set(grid.columns) | ({grid.size_field} if grid else set()) if grid else set()

    numeric = [(i, c) for i, c in enumerate(table.columns) if prof[c].kind == "number"]
    for (i, a), (j, b) in zip(numeric, numeric[1:]):
        if j != i + 1:
            continue
        if a in in_grid or b in in_grid:
            continue
        if not identical_columns(table, [a, b]):
            continue
        out.append(Observation(
            id=f"{table.name}:stage_collapse:{a}:{b}",
            kind="stage_collapse",
            severity="high",
            source=table.name,
            fields=(a, b),
            what=(
                f"`{a}` and `{b}` hold the same value on all "
                f"{len(table.rows)} rows. They are one measurement recorded "
                f"twice, so no drop-off can be read between them and any "
                f"conversion computed across the pair is 100% by "
                f"construction."
            ),
            figures={"rows": float(len(table.rows))},
        ))
    return out


def _group_keys(table: Table, prof: Mapping[str, ColumnProfile]) -> list[str]:
    """The columns worth grouping by, with equivalent ones collapsed.

    `account` and `account_id` cut the same book into the same 59 pieces, so
    grouping by both produces the identical observation twice with a different
    field name on it. Keys inducing the same partition (same distinct count)
    are treated as one, and the surviving name is the one a reader recognises
    — a plan that says "the top three by account_id" has told them nothing.
    """
    by_grain: dict[int, list[str]] = {}
    for c in table.columns:
        p = prof[c]
        if p.kind != "text" or p.filled < 4:
            continue
        by_grain.setdefault(p.distinct, []).append(c)
    out: list[str] = []
    for _, names in sorted(by_grain.items()):
        out.append(sorted(names, key=lambda n: (n.lower().endswith("_id"), n))[0])
    return out


def _observe_concentration(tables: Sequence[Table]) -> list[Observation]:
    """Where the activity is against where the money is.

    Spans tables, joined on a column the two share. That join is the only
    cross-table operation in this module, and it is here because the question
    is inherently cross-table: activity is recorded one row per event and
    value is recorded one row per account, and no single sheet holds both.

    ONE OBSERVATION PER ACTIVITY SOURCE. Several sheets can supply the value
    side, and comparing ticket volume against each of them in turn says the
    same thing three times with slightly different percentages. The value
    source chosen is the one that actually COVERS the accounts being looked at
    — most groups in common with the activity, then the largest book — because
    a divergence measured against a value table that only knows a third of
    these accounts is mostly an artefact of the join.
    """
    prof_by_table = {t.name: profiles(t) for t in tables}

    # Value side: tables with ONE row per group and a monetary column. One row
    # per group is what makes a total meaningful — summing a per-month ACV
    # column would multiply the book by the number of months.
    value_maps: list[tuple[str, str, str, dict[str, float]]] = []
    for t in tables:
        prof = prof_by_table[t.name]
        for key in t.columns:
            if prof[key].kind != "text" or prof[key].distinct != prof[key].filled:
                continue
            if prof[key].filled < 4:
                continue
            money = [c for c in t.columns
                     if prof[c].kind == "number" and _looks_monetary(c)]
            if not money:
                continue
            # The fullest measure of the book: the monetary column with the
            # largest total. `reconcile_value_columns` separately reports that
            # a smaller sibling exists and why it is smaller, so choosing the
            # largest here is not hiding the choice.
            best = max(money, key=lambda c: sum(
                v for v in (_as_number(x) for x in t.values(c)) if v is not None))
            value_maps.append(
                (t.name, key, best, aggregate_by_group(t, key, best)))

    out: list[Observation] = []
    for t in tables:
        prof = prof_by_table[t.name]
        short = t.name.split(":")[-1]
        for key in _group_keys(t, prof):
            if prof[key].distinct == prof[key].filled:
                continue
            vol_label = f"{short} volume"
            vol = aggregate_by_group(t, key)
            # Pick the value source with the best join against THIS activity.
            ranked = sorted(
                (
                    (
                        -len(set(vol) & set(vals)),
                        -sum(vals.values()),
                        f"{vt_name}:{vt_money}",
                        vt_money,
                        vals,
                    )
                    for vt_name, vt_key, vt_money, vals in value_maps
                    if vt_key == key and vt_name != t.name
                ),
                key=lambda r: r[:3],
            )
            if not ranked:
                continue
            _, _, _, value_label, values = ranked[0]
            div = concentration_divergence(
                vol, values, group_field=key,
                volume_label=vol_label, value_label=value_label,
            )
            if div is None or div.ratio < CONCENTRATION_MIN_RATIO:
                continue
            out.append(Observation(
                id=f"{t.name}:concentration:{key}:{value_label}",
                kind="concentration_divergence",
                severity="high" if div.ratio >= 2.0 else "medium",
                source=t.name,
                fields=(key, value_label),
                what=(
                    f"The top {len(div.top_groups)} of {div.group_count} by "
                    f"{vol_label} hold {_pct(div.volume_share)} of the "
                    f"activity but only {_pct(div.value_share)} of "
                    f"`{value_label}` — {div.ratio:.1f} times their share of "
                    f"the money. Ranking by how often something is mentioned "
                    f"puts these accounts first; ranking by value does not."
                ),
                figures={
                    "top_n": float(len(div.top_groups)),
                    "groups": float(div.group_count),
                    "volume_share": div.volume_share,
                    "value_share": div.value_share,
                    "even_share": div.even_share,
                    "ratio": div.ratio,
                },
            ))
    return out


#: How many priced accounts a sheet must carry before it is a BOOK rather than
#: a handful of rows. Four is the existing bar `unit_value_derivable` has always
#: applied, kept identical so the figure the plan quotes and the map the run
#: weights by can never come from two different columns.
MIN_PRICED_ACCOUNTS = 4


@dataclass(frozen=True)
class AccountBook:
    """One table's per-account value column, and the totals it yields.

    EXTRACTED SO THERE IS EXACTLY ONE ANSWER TO "WHICH COLUMN IS THE MONEY".
    The plan tells the reader the figure was taken from a named column of a
    named sheet; if the weighting then read a different column the sentence
    would be false in the one place a reader cannot check it. Both callers go
    through here.
    """
    table: Table
    key_field: str
    field: str
    totals: Mapping[str, float]


def _per_account_books(tables: Sequence[Table]) -> list[AccountBook]:
    """Every table that carries a per-account recurring value, in table order.

    The column choice is the one `unit_value_derivable` has always made: the
    first text column that looks like an account and is one row per account,
    and the recurring-value column carrying the largest total.
    """
    books: list[AccountBook] = []
    for t in tables:
        prof = profiles(t)
        keys = [c for c in t.columns
                if prof[c].kind == "text" and _looks_like_account(c)
                and prof[c].distinct == prof[c].filled
                and prof[c].filled >= MIN_PRICED_ACCOUNTS]
        money = [c for c in t.columns
                 if prof[c].kind == "number" and _is_recurring_value(c)]
        if not keys or not money:
            continue
        key = keys[0]
        best = max(money, key=lambda c: sum(
            v for v in (_as_number(x) for x in t.values(c)) if v is not None))
        totals = aggregate_by_group(t, key, best)
        if len(totals) < MIN_PRICED_ACCOUNTS:
            continue
        books.append(AccountBook(table=t, key_field=key, field=best,
                                 totals=totals))
    return books


@dataclass(frozen=True)
class AccountValues:
    """What each account is worth, keyed the way the graph names accounts.

    RUN-SCOPED, AND NEVER PERSISTED. This is a copy of the reader's contract
    rows: sixty account names against sixty annual values. It is derived from
    the uploaded bytes each time it is needed and it does not go into
    `prioritisation.plan` — what IS stored is the distilled per-finding sum and
    the names that could not be priced, which is an output of the analysis
    rather than a copy of the input.

    KEYED ON `claims.account_key`, NOT ON THE SPELLING IN THE SPREADSHEET. The
    contracts sheet writes `Northwind Labs Inc.` and the graph writes
    `NorthwindLabs`; joining on the raw strings matches neither. One
    normalisation, used on both sides, is the whole reason the join is worth
    anything — and it is the same function the self-name exclusion uses, so
    there is no second definition of what makes two names one account.
    """
    values: Mapping[str, float]
    #: The table and column the figures came from, so the plan can say where.
    source: str = ""
    field: str = ""
    key_field: str = ""
    #: Raw spellings that collapsed onto the same key and were ADDED together.
    #: Named rather than counted because summing two rows into one account is
    #: the one operation here that can silently overstate a customer.
    merged: tuple[str, ...] = ()

    @property
    def accounts(self) -> int:
        return len(self.values)

    @property
    def total(self) -> float:
        return float(sum(self.values.values()))


def account_value_map(tables: Sequence[Table]) -> Optional[AccountValues]:
    """`account_key -> annual value`, from the reader's own contracts.

    ONE BOOK, CHOSEN THE SAME WAY `unit_value_derivable` CHOOSES IT: most
    accounts covered, then largest total, then name. Several sheets can carry
    a per-account figure and they will not agree; picking here, once, and
    naming the column is the disclosed version of that choice.

    `None` when nothing read carries one, which is the normal case for a
    tenant with no contracts attached and is what keeps the run counted.
    """
    ranked = sorted(
        _per_account_books(tables),
        key=lambda b: (-len(b.totals), -sum(b.totals.values()), b.table.name),
    )
    if not ranked:
        return None
    book = ranked[0]
    from app.crucible.claims import account_key

    values: dict[str, float] = {}
    seen: dict[str, list[str]] = {}
    for name, amount in book.totals.items():
        key = account_key(name)
        if not key:
            continue
        values[key] = values.get(key, 0.0) + float(amount)
        seen.setdefault(key, []).append(str(name))
    merged = tuple(sorted(
        n for names in seen.values() if len(names) > 1 for n in names))
    return AccountValues(
        values=values, source=book.table.name, field=book.field,
        key_field=book.key_field, merged=merged,
    )


@dataclass(frozen=True)
class PriceableCoverage:
    """How much of what was READ could be priced from the contracts.

    THE DENOMINATOR IS DISTINCT ACCOUNTS, AND THE ALTERNATIVE IS CIRCULAR.

    Two are defensible and they differ by more than twofold on real data: the
    share of ATTRIBUTED SIGNALS and the share of DISTINCT ACCOUNTS — 3.9% and
    1.5% on the tenant this was measured against. Accounts is the one used.

    A signal share is a LOUDNESS-WEIGHTED denominator, and loudness is the
    exact variable this feature exists to stop trusting. The gate asks "may I
    stop ranking by how often something was mentioned?", and answering it with
    a mention-count is asking the question in the units of the answer it is
    meant to check.

    The risk the gate exists to prevent is also per-ACCOUNT. A quiet, large,
    unpriced customer is precisely what a signal share cannot see — it
    contributes almost nothing to the denominator while carrying real revenue
    the weighting would then miss. An account share counts it once, like every
    other account.

    AND THE TWO DIVERGE IN THE UNSAFE DIRECTION. On that tenant the priced
    accounts carry roughly 2.6x the average signal volume, because contracted
    customers talk more than prospects and long-tail names do. So the signal
    share reads systematically HIGHER and errs toward weighting a run that
    should have counted. A false count is honest and disclosed; a false weight
    is the invariant violation.

    NO MINIMUM-SIGNALS FLOOR, deliberately. Trimming the long tail out of the
    denominator would reintroduce loudness through the back door and quietly
    raise the share. If the prospect and residue names in the account set ever
    need excluding, the honest instrument is the customer-side split
    `infer_account_sides` already computes, not a volume cut-off.

    The signal counts are still measured and still reported — they are a true
    fact about the corpus and a reader wants them. They are simply not what
    the gate divides by.
    """
    attributed_signals: int
    priceable_signals: int
    named_accounts: int
    priced_accounts: int
    #: The keys, so the plan can say which it could price and which it could
    #: not. Bounded by the caller when rendered, never when counted.
    priced_names: tuple[str, ...] = ()
    unpriced_names: tuple[str, ...] = ()

    @property
    def share(self) -> float:
        return (0.0 if not self.named_accounts
                else self.priced_accounts / self.named_accounts)

    @property
    def signal_share(self) -> float:
        """Reported, never divided by — see the class docstring."""
        return (0.0 if not self.attributed_signals
                else self.priceable_signals / self.attributed_signals)

    @property
    def denominator(self) -> str:
        return WEIGHTING_DENOMINATOR


def priceable_coverage(
    signals: Sequence[Mapping[str, Any]],
    values: Mapping[str, float],
    self_names: frozenset[str] = frozenset(),
) -> PriceableCoverage:
    """Of the evidence that names an account, how much names a PRICED one.

    A signal counts as priceable if ANY account it names is in the book. That
    is the generous reading and it is the right one here: this gate decides
    whether the run may weight at all, and a theme reached through one priced
    account of three is still a theme the money can speak to. The per-finding
    disclosure carries the harder truth — how many of THAT finding's accounts
    were priced — where a reader can see it beside the number.
    """
    from app.crucible.claims import (
        CUSTOMER_KEYS, PROSPECT_KEYS, account_key, normalise_account,
    )

    attributed = 0
    priceable = 0
    named: set[str] = set()
    priced: set[str] = set()
    for row in signals:
        props = row.get("properties") if isinstance(row, Mapping) else None
        if not isinstance(props, Mapping):
            continue
        keys = set()
        for field_name in (*CUSTOMER_KEYS, *PROSPECT_KEYS):
            name = normalise_account(props.get(field_name), self_names)
            if name:
                keys.add(account_key(name))
        if not keys:
            continue
        attributed += 1
        named |= keys
        hit = keys & set(values)
        if hit:
            priceable += 1
            priced |= hit
    return PriceableCoverage(
        attributed_signals=attributed,
        priceable_signals=priceable,
        named_accounts=len(named),
        priced_accounts=len(priced),
        priced_names=tuple(sorted(priced)),
        unpriced_names=tuple(sorted(named - priced)),
    )


def _observe_priceable_coverage(
    tables: Sequence[Table],
    signals: Sequence[Mapping[str, Any]],
    self_names: frozenset[str] = frozenset(),
) -> list[Observation]:
    """Could the evidence actually be weighted by revenue, and by how much?

    THE VERDICT THE READER APPROVES, AND THE ONE THING ON THIS PLAN THAT MUST
    NOT BE RECOMPUTED LATER. A connector that syncs every twenty minutes moves
    the denominator between the gate and the run, so a share taken again at
    execute can cross the threshold in either direction — the reader would
    approve a counted run and receive a weighted one. It is measured once,
    here, and carried on the plan as an observation exactly like every other
    fact the gate shows.

    EMITTED WHETHER THE ANSWER IS YES OR NO, which is the difference between a
    disclosure and a boast. The interesting case on real data is the negative
    one: on the tenant this was measured against 3.9% of attributed signals
    name an account the contracts can price, so the run counts, and the plan
    says so in those words instead of degrading to a count in silence.
    """
    book = account_value_map(tables)
    if book is None or not signals:
        return []
    cov = priceable_coverage(signals, book.values, self_names)
    if not cov.attributed_signals:
        return []
    weighted = cov.share >= WEIGHTING_MIN_PRICEABLE_SHARE
    conc = _concentration(dict(book.values), book.key_field, book.field,
                          DEFAULT_TOP_N)
    if weighted:
        # CLEARS THE BAR — IT DOES NOT SETTLE THE UNIT, AND THE TWO ARE NOT
        # THE SAME THING. This branch is `share >= threshold` and nothing
        # more; `plan.weighting_verdict` reads the same share and STILL
        # returns a count on two of its branches — a business recorded as
        # self-serve, and a business model not recorded at all, which is the
        # default state of the column. So "themes here are weighted by the
        # revenue behind them", which is what stood here, asserted a verdict
        # this pass does not make and is wrong on the commonest tenant shape
        # there is: one that cleared the bar and never filled in how it sells.
        #
        # It was invisible while the gate was the only surface that rendered
        # an observation and the verdict sat two lines below it. It is not
        # invisible now: `report._observations_section` restates `what` in the
        # finished document, where nothing contradicts it.
        #
        # The BELOW-threshold branch needs no such care and gets none:
        # `weighting_verdict`'s first branch is exactly this test, so a share
        # under the bar really does mean counted, and saying so is a
        # measurement rather than a forecast.
        headline = (
            f"{_pct(cov.share)} of the accounts named in your evidence are "
            f"ones your contracts can price, which clears the bar for themes "
            f"to be weighted by the revenue behind them rather than by how "
            f"many accounts raised them."
        )
    else:
        headline = (
            f"Only {_pct(cov.share)} of the accounts named in your evidence "
            f"could be priced, so this is counted, not weighted: a theme's "
            f"size is the number of accounts it touches, and never money."
        )
    return [Observation(
        id=f"{book.source}:priceable_coverage:{book.field}",
        kind="priceable_coverage",
        severity="medium" if weighted else "high",
        source=book.source,
        fields=(book.key_field, book.field),
        what=(
            f"{headline} `{book.field}` prices {book.accounts:,} accounts, "
            f"and {cov.priced_accounts:,} of the {cov.named_accounts:,} "
            f"accounts named in your evidence are among them. Those accounts "
            f"account for {cov.priceable_signals:,} of "
            f"{cov.attributed_signals:,} attributed signals — reported "
            f"because it is worth knowing, and deliberately not what the "
            f"decision above divides by. In the file itself the top "
            f"{min(DEFAULT_TOP_N, book.accounts)} accounts hold "
            f"{_pct(conc.share)} of the book."
        ),
        figures={
            "priceable_share": cov.share,
            #: The loudness-weighted view of the same join. REPORTED, NEVER
            #: THE GATE — see `PriceableCoverage`.
            "priceable_signal_share": cov.signal_share,
            "threshold": WEIGHTING_MIN_PRICEABLE_SHARE,
            "attributed_signals": float(cov.attributed_signals),
            "priceable_signals": float(cov.priceable_signals),
            "named_accounts": float(cov.named_accounts),
            "priced_accounts": float(cov.priced_accounts),
            "book_accounts": float(book.accounts),
            "top_n": float(min(DEFAULT_TOP_N, book.accounts)),
            "top_share": conc.share,
        },
    )]


def _observe_unit_value(tables: Sequence[Table]) -> list[Observation]:
    """Is what one account is worth already in the evidence?

    The one observation whose job is to STOP a question being asked. The plan
    gate has always asked "what is one account worth to you, per year?", and
    on a book that carries a per-account contract value that question is the
    engine asking a reader to supply a number it is already holding — which
    invites an estimate that then disagrees with the data underneath it.

    ONE OBSERVATION, NOT ONE PER TABLE. Several sheets can carry a per-account
    figure and they will not all agree (a contracts export including expansion
    against an analytics dimension that does not). Emitting all of them would
    leave the question-builder choosing between them, which is a decision
    about which number is the book's real value — so it is made here, once,
    and the observation names the column it used: the fullest coverage, then
    the largest total. `value_columns_disagree` separately reports WHY the
    smaller sibling is smaller, so the choice is disclosed rather than hidden.
    """
    candidates: list[tuple[int, float, str, Observation]] = []
    for book in _per_account_books(tables):
        t, key, best, totals = book.table, book.key_field, book.field, book.totals
        values = sorted(totals.values())
        mid = len(values) // 2
        median = values[mid] if len(values) % 2 else (values[mid - 1] + values[mid]) / 2
        obs = Observation(
            id=f"{t.name}:unit_value:{key}:{best}",
            kind="unit_value_derivable",
            severity="medium",
            source=t.name,
            fields=(key, best),
            what=(
                f"`{best}` gives a value for each of {len(values)} accounts, "
                f"so what one account is worth does not need to be supplied: "
                f"the median is {_money(median)} and the mean is "
                f"{_money(sum(values) / len(values))}."
            ),
            figures={
                "accounts": float(len(values)),
                "median": float(median),
                "mean": sum(values) / len(values),
                "total": sum(values),
                "minimum": float(values[0]),
                "maximum": float(values[-1]),
            },
        )
        candidates.append((len(values), sum(values), obs.id, obs))
    if not candidates:
        return []
    # Deterministic: most accounts covered, then largest book, then id.
    candidates.sort(key=lambda c: (-c[0], -c[1], c[2]))
    return [candidates[0][3]]


def _looks_monetary(column: str) -> bool:
    c = column.lower()
    return any(h in c for h in MONETARY_HINTS)


def _is_recurring_value(column: str) -> bool:
    c = column.lower()
    return any(h in c for h in RECURRING_VALUE_HINTS)


def _looks_like_account(column: str) -> bool:
    c = column.lower()
    # `account_id` is an identifier and `account` is the name; either works as
    # the grain, but a name is what a reader recognises in a plan.
    return any(h in c for h in ACCOUNT_HINTS) and not c.endswith("_id")


# ── THE PASS ────────────────────────────────────────────────────────────────


_SEVERITY_ORDER = {s: i for i, s in enumerate(SEVERITIES)}


def _cap(observations: Sequence[Observation]) -> tuple[Observation, ...]:
    """Keep the strongest, bounded per kind AND per table within a kind.

    The per-table cap is the one that matters. A wide retention grid produces
    a dozen adjacent-identical column pairs and a narrow activation funnel
    produces one — and the one is the finding, because it is the funnel a
    reader was about to draw a conversion rate from. Sorting by severity alone
    hands the whole budget to the wide sheet.

    Ties break on `id`, which is derived from the table and field names, so
    two runs over the same evidence keep the same observations.
    """
    ranked = sorted(
        observations,
        key=lambda o: (_SEVERITY_ORDER.get(o.severity, 99), o.id),
    )
    per_kind: Counter = Counter()
    per_table: Counter = Counter()
    out: list[Observation] = []
    for o in ranked:
        if per_kind[o.kind] >= MAX_PER_KIND:
            continue
        if per_table[(o.kind, o.source)] >= MAX_PER_TABLE_PER_KIND:
            continue
        per_kind[o.kind] += 1
        per_table[(o.kind, o.source)] += 1
        out.append(o)
    return tuple(out)


def _span(table: Table, prof: Mapping[str, ColumnProfile]) -> tuple[str, str]:
    dates: list[str] = []
    for c in table.columns:
        if prof[c].kind != "date":
            continue
        dates.extend(str(v).strip() for v in table.values(c) if not _is_blank(v))
    if not dates:
        return "", ""
    return min(dates), max(dates)


#: How full a table has to be before the table-side checks will speak about it.
#:
#: MEASURED, NOT CHOSEN. Real spreadsheets in a multi-source upload run
#: 0.878–1.000 dense — the floor is a CRM export whose loss-reason columns are
#: legitimately empty on open deals. The pseudo-tables built from a prose
#: corpus's `properties` run 0.032–0.111 for every large one, because those
#: forty columns are the UNION of keys across heterogeneous signals and each
#: row carries a handful. 0.6 sits in the gap with room on both sides.
RECTANGULAR_MIN_DENSITY = 0.6

#: Rows sampled to estimate it. The question is "is this a rectangle", which
#: the head answers as well as the whole; scanning every row to decide whether
#: to scan every row would defeat the purpose.
DENSITY_SAMPLE_ROWS = 50


def density(table: Table) -> float:
    """What share of this table's cells are filled, over a sample of rows."""
    rows = table.rows[:DENSITY_SAMPLE_ROWS]
    if not rows or not table.columns:
        return 0.0
    cells = len(rows) * len(table.columns)
    filled = sum(1 for r in rows for c in table.columns if not _is_blank(r.get(c)))
    return filled / cells


def is_rectangular(table: Table) -> bool:
    """Is this dense enough to be a table, or is it a union of stray keys?

    THE GATE ON THE TABLE-SIDE CHECKS, and it runs before profiling because
    profiling is the cost. Measured on a 1,275-signal prose corpus: the pass
    spent ~100ms building and profiling seventeen tables — one of them forty
    columns wide over 458 rows — and every check then declined to speak about
    any of them.

    DENSITY RATHER THAN COLUMN NAMES. The obvious gate is "does any column
    look like an account or an amount", and on real data it does not work: a
    prose corpus's `properties` are whatever the extractor's model wrote, and
    the measured tenant carries `valuation_usd`, `raise_usd`, `ask_usd`,
    `market_size_usd_2035` and `customer_name`. A name gate opens on
    essentially every prose tenant, which is exactly the tenant it was meant to
    spare.

    What actually separates the two is SHAPE. Every check above assumes a
    rectangle — two columns that both claim to be the value, two adjacent
    funnel stages, a cohort grid. A union of keys across heterogeneous signals
    is not a rectangle, and the checks are not wrong about it so much as
    inapplicable to it.

    PER TABLE, NOT PER PASS. An upload carrying a funnel and no money keeps its
    checks, because it is still a rectangle; a two-row scrap of signal
    properties keeps its own, because it costs nothing to look.
    """
    return density(table) >= RECTANGULAR_MIN_DENSITY


def reserve_for_uploads(
    uploaded: Sequence[Table], corpus: Sequence[Table],
) -> tuple[list[Table], tuple[UnreadFile, ...]]:
    """The two populations, fitted into `observe`'s table budget with the
    ATTACHMENTS FIRST — and whatever still did not fit, named.

    THE BUG THIS EXISTS TO REMOVE. `observe` takes `tables[:MAX_TABLES]`, and
    the reconnaissance pass used to hand it the knowledge-graph tables
    followed by the reader's attachments. On a small tenant that is invisible;
    on a real one the graph filled the budget first and the attachments were
    truncated off the end — measured on a tenant whose graph yielded 31
    tables, a twelve-file pack lost three files outright and a fourth lost its
    last sheet, silently, with the plan then listing the three survivors as
    though they were everything sent. The reader's own evidence was the first
    thing discarded, which is the exact inversion of what anyone would expect:
    a connected source is standing and re-readable, an attachment is a
    deliberate act of this one message.

    NOT A BIGGER NUMBER. Raising the bound moves the cliff and leaves the fall
    silent; the bound is there for the gate's wall clock and profiling is
    cubic in columns, so it stays exactly where it is. What changes is WHO
    gets the budget when it runs out, and that whatever is displaced says so.

    THE TWO POPULATIONS STILL GO INTO ONE `observe` CALL. Splitting them would
    lose every cross-population check, which is the whole reason they are
    observed together — this only decides the order and the split of a single
    budget.

    The corpus half is truncated silently, as it always was: a signal bucket
    is not named anywhere in the plan, so there is no claim about it to
    correct, and `observe`'s knowledge-graph checks read the raw signals
    rather than these tables and are untouched by the trim.
    """
    kept = list(uploaded)[:MAX_TABLES]
    over = list(uploaded)[MAX_TABLES:]
    room = max(0, MAX_TABLES - len(kept))
    trimmed = list(corpus)[:room]
    if len(corpus) > room:
        logger.info(
            "crucible recon: %d of %d corpus tables did not fit beside %d "
            "uploaded tables", len(corpus) - room, len(corpus), len(kept),
        )

    #: One entry per FILE, not per sheet — several sheets of one workbook are
    #: one upload to the person who attached it, which is the same grouping
    #: `plan.uploads_from_report` uses for the files that WERE read.
    #: THE RAW STEM, not `origin_label`. `plan.uploads_from_report` names a
    #: read upload by the stem of the filename the reader chose, and a list of
    #: files that were NOT read has to sit beside it in the same vocabulary —
    #: "08_sales_data" read and "sales data" not read reads as two files.
    lost: dict[str, int] = {}
    for t in over:
        stem = str(t.name).split(":")[0].strip()
        lost[stem] = lost.get(stem, 0) + 1
    unread = tuple(
        UnreadFile(
            name=stem,
            reason=f"{n} of its sheets did not fit — this run reads at most "
                   f"{MAX_TABLES} tables and the files ahead of it filled them",
        )
        for stem, n in lost.items()
    )
    return kept + trimmed, unread


def observe(
    tables: Sequence[Table],
    *,
    expected_sources: Iterable[str] = (),
    #: The raw signal rows, for the checks that read the knowledge graph
    #: rather than a table. Optional: a caller with only spreadsheets passes
    #: none and simply gets no evidence-mix observation.
    signals: Sequence[Mapping[str, Any]] = (),
    #: The tenant's own company name as `account_key` keys — see
    #: `claims.self_account_keys`. RESOLVED BY THE CALLER, NOT HERE: this pass
    #: is documented as deterministic and total, and a DB read for the display
    #: name inside it would make it neither. Empty excludes nothing, which is
    #: what every caller that cannot resolve a name gets.
    self_names: frozenset[str] = frozenset(),
) -> ReconReport:
    """Read the structure of the evidence and say what is there.

    Deterministic and total: no model, no clock, no randomness, and a check
    that raises on one table costs that table's observations rather than the
    whole pass. A plan gate that fell over because one sheet had a header row
    it did not like would be strictly worse than the row-count it replaced.
    """
    tables = list(tables)[:MAX_TABLES]
    observations: list[Observation] = []
    sources: list[SourceCoverage] = []
    present_types: set[str] = set()
    #: Tables that survived profiling, and ONLY those, are handed to the
    #: cross-table checks.
    #:
    #: A ONE-LINE FIX FOR A WHOLE-RUN FAILURE. The per-table checks already
    #: skipped a table they could not read, but the two cross-table checks
    #: took the raw list and ran inside a single try — so one unreadable sheet
    #: anywhere in the upload aborted the concentration and per-account-value
    #: checks for EVERY table, and the plan silently lost its two most
    #: consequential observations. Found by running the positive assertions
    #: against property-removed fixtures and reading the warnings.
    readable: list[Table] = []

    # THE TABLE-SIDE CHECKS ONLY RUN OVER TABLES THEY COULD SPEAK ABOUT.
    # Decided per table, on a sampled density, before anything is profiled.
    # GUARDED LIKE EVERYTHING ELSE HERE. The gate runs before the try that
    # wraps profiling, so an unreadable table would raise from inside it and
    # take the whole pass with it — the totality guarantee, lost to the very
    # change that was meant to make the pass cheaper. A table that cannot be
    # measured is not a rectangle, which skips it exactly as before.
    rectangles = []
    for t in tables:
        try:
            if is_rectangular(t):
                rectangles.append(t)
        except Exception:  # noqa: BLE001 — see above
            logger.warning("crucible recon: could not measure %s", t.name,
                           exc_info=True)
    tabular = bool(rectangles)
    rect_names = {t.name for t in rectangles}

    for t in tables:
        if t.name not in rect_names:
            # Coverage WITHOUT the date span, which is the one part that needs
            # profiling. A corpus whose tables are not rectangles reports its
            # dating from `valid_at`/`created_at` instead, which is the more
            # honest answer for it anyway — see `_observe_dating`.
            sources.append(SourceCoverage(
                name=t.name, source_type=t.source_type, records=len(t.rows),
                columns=len(t.columns),
            ))
            if t.source_type:
                present_types.add(t.source_type)
            continue
        try:
            prof = profiles(t)
        except Exception:  # noqa: BLE001 — see docstring
            logger.warning("crucible recon: could not profile %s", t.name,
                           exc_info=True)
            continue
        readable.append(t)
        earliest, latest = _span(t, prof)
        sources.append(SourceCoverage(
            name=t.name, source_type=t.source_type, records=len(t.rows),
            columns=len(t.columns), earliest=earliest, latest=latest,
        ))
        if t.source_type:
            present_types.add(t.source_type)
        for check in (_observe_value_columns, _observe_coding_gap,
                      _observe_stage_collapse, _observe_censored_periods):
            try:
                observations.extend(check(t, prof))
            except Exception:  # noqa: BLE001
                logger.warning("crucible recon: %s failed on %s",
                               check.__name__, t.name, exc_info=True)

    for cross in (_observe_concentration, _observe_unit_value) if tabular else ():
        try:
            observations.extend(cross(readable))
        except Exception:  # noqa: BLE001
            logger.warning("crucible recon: %s failed", cross.__name__,
                           exc_info=True)

    # `_observe_attribution` IS THE ONE CHECK THAT NEEDS MORE THAN THE ROWS.
    # It is bound with the tenant's own account keys here rather than given a
    # uniform signature across all four, so the other three keep reading
    # exactly what they always read.
    # NAME AND CALLABLE, because one of the four now carries a bound argument
    # and a bare lambda would log itself as "<lambda> failed" — the warning is
    # the only thing that says WHICH check a tenant lost.
    for check_name, kg_check in (
        ("_observe_dating", _observe_dating),
        ("_observe_attribution",
         lambda rows: _observe_attribution(rows, self_names)),
        ("_observe_source_concentration", _observe_source_concentration),
        ("_observe_claim_mix", _observe_claim_mix),
    ):
        if not signals:
            break
        try:
            observations.extend(kg_check(signals))
        except Exception:  # noqa: BLE001 — one check is never the run
            logger.warning("crucible recon: %s failed", check_name,
                           exc_info=True)
    if signals:
        try:
            observations.extend(_observe_evidence_mix(evidence_mix(signals)))
        except Exception:  # noqa: BLE001
            logger.warning("crucible recon: evidence mix failed", exc_info=True)

    # THE ONE CHECK THAT NEEDS BOTH SIDES. Every other observation reads either
    # the tables or the graph; this one asks whether they JOIN — how much of
    # what was read names an account the uploaded book can price — so it sits
    # outside both loops rather than being bent into either.
    try:
        observations.extend(
            _observe_priceable_coverage(readable, signals, self_names))
    except Exception:  # noqa: BLE001 — a run that cannot answer this counts,
        # which is the same thing it did before this check existed.
        logger.warning("crucible recon: priceable coverage failed",
                       exc_info=True)

    missing = tuple(sorted(
        str(s) for s in expected_sources if str(s) not in present_types))
    return ReconReport(
        observations=_cap(observations),
        sources=tuple(sources),
        missing=missing,
        total_records=sum(s.records for s in sources),
    )
