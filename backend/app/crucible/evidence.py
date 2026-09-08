"""What a selector may read about the evidence attached to a run — never the
evidence itself.

`recon.Table` already carries a workbook sheet or a signal bucket as rows in
memory, and `recon.profile` already characterises every column's shape. This
module adds three things a later SELECTION stage will need and does not yet
have: WHEN a table is evidence of, a SHAPE view of it a model may read
without ever being handed its rows, and which parts of it a deterministic
rule — not a model's judgement — has already decided must be carried through
rather than summarised.

NO SELECTION HAPPENS HERE. `schema_view` returns a description; nothing
downstream reads it yet, and the two marks below (`passthrough_sheet` on a
table, `passthrough_columns` within one) are recorded, not acted on. This
module decides what is RETRIEVABLE. A later one decides what is USED.

THE SCHEMA VIEW NEVER CARRIES A ROW. That boundary is why this exists as a
separate view rather than a method on `Table`: the deterministic engine
computes over `table.rows`, a model reads `schema_view(...)`, and a function
that could return either is a function a careless caller could hand a model
the numbers to grade its own homework against.

Run-scoped, same as the tables it describes. Nothing here is written to
`kg_signal`, `kg_entity`, or `kg_relationship` — see `crucible.prose`'s
opening docstring for why that boundary matters for evidence read once.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, replace
from datetime import datetime, timezone
from typing import Any, Optional, Sequence

from app.crucible.recon import ColumnProfile, Table, profile, profiles

# ── as_of: WHEN a table is evidence OF ───────────────────────────────────────
#
# Three rules, tried in the order the product cares about, and the rule that
# fired is recorded alongside the date — a reader (or a later staleness
# check) needs to know whether an `as_of` is a fact the data stated or a fact
# this run assumed because nothing more specific was there to read.

AS_OF_RULE_TABLE_DATE_COLUMN = "table_date_column"
AS_OF_RULE_HEADER_OR_NAME = "header_or_name"
AS_OF_RULE_UPLOAD_TIMESTAMP = "upload_timestamp"

#: Recognised spellings only — the calendar work (is this a real month, is
#: this year plausible) stays with `prose._plausible_date`, which already
#: does it. Sorted longest-first only to short-circuit backtracking; the
#: `\.?\s+(\d{4})\b` tail already forces a wrong-length match to fail and try
#: the next alternative.
_MONTH_NAMES: dict[str, int] = {
    "january": 1, "february": 2, "march": 3, "april": 4, "may": 5,
    "june": 6, "july": 7, "august": 8, "september": 9, "october": 10,
    "november": 11, "december": 12, "sept": 9, "jan": 1, "feb": 2,
    "mar": 3, "apr": 4, "jun": 6, "jul": 7, "aug": 8, "sep": 9,
    "oct": 10, "nov": 11, "dec": 12,
}
_MONTH_YEAR_RE = re.compile(
    r"\b(" + "|".join(sorted(_MONTH_NAMES, key=len, reverse=True)) + r")"
    r"\.?\s+(\d{4})\b",
    re.IGNORECASE,
)


def _month_year_to_iso(text: str) -> Optional[str]:
    """The first `<Month name> <Year>` in TEXT, as `YYYY-MM-01` — NOT a date
    parser. It recognises a spelling and nothing more; the calendar work (is
    this a real month, is this year plausible) is `prose._plausible_date`'s
    job and stays there, so a header's date and a document's date are
    validated by exactly one piece of logic.
    """
    m = _MONTH_YEAR_RE.search(text)
    if not m:
        return None
    month = _MONTH_NAMES[m.group(1).lower()]
    return f"{m.group(2)}-{month:02d}-01"


def derive_as_of(
    table: Table, *, now: datetime, upload_timestamp: Optional[datetime] = None,
) -> tuple[Optional[datetime], str]:
    """`(as_of, rule)` for one table, tried in the order the product cares
    about: what the data itself says, then what its label says, then when it
    arrived. Never raises — a table this cannot date at all still gets rule 3.
    """
    from app.crucible import prose  # lazy: prose imports recon at call time,
    # so importing it back here at module load would be circular.

    # Rule 1 — a date column in the table, the latest plausible value in it.
    # `profile(...).kind == "date"` is `recon`'s own judgement about which
    # column that is; this does not re-derive it.
    for column in table.columns:
        if profile(table, column).kind != "date":
            continue
        blob = "\n".join(
            str(v) for v in table.values(column)
            if v is not None and str(v).strip()
        )
        found = prose._latest_plausible_date(blob, now=now)
        if found is not None:
            return found, AS_OF_RULE_TABLE_DATE_COLUMN

    # Rule 2 — a date in the sheet/file name or a column header. The LATEST
    # one found, on the same "a document is observed at the end of what it
    # discusses" logic `prose._latest_plausible_date` already applies to text.
    best: Optional[datetime] = None
    for text in (table.name, table.label, *table.columns):
        text = str(text or "")
        iso = _month_year_to_iso(text)
        candidate = prose._plausible_date(iso, now=now) if iso else None
        if candidate is None:
            candidate = prose._plausible_date(text, now=now)
        if candidate is not None and (best is None or candidate > best):
            best = candidate
    if best is not None:
        return best, AS_OF_RULE_HEADER_OR_NAME

    # Rule 3 — the upload's own timestamp. Never `None`: this run's clock if
    # nothing more specific was supplied.
    return (upload_timestamp or now), AS_OF_RULE_UPLOAD_TIMESTAMP


def with_as_of(
    tables: Sequence[Table], *, now: Optional[datetime] = None,
    upload_timestamp: Optional[datetime] = None,
) -> tuple[Table, ...]:
    """`tables`, each carrying its derived `as_of` and `as_of_rule`.

    A NEW TUPLE OF NEW TABLES, because `Table` is frozen. `dataclasses.replace`
    copies rows, columns and source_type untouched — nothing about how a
    table reads changes here, only what it now says about when it is FROM.
    """
    now = now or datetime.now(timezone.utc)
    out = []
    for table in tables:
        as_of, rule = derive_as_of(
            table, now=now, upload_timestamp=upload_timestamp)
        out.append(replace(table, as_of=as_of, as_of_rule=rule))
    return tuple(out)


# ── the schema view: WHAT a selector may read about a table's SHAPE ─────────


def _present_values(table: Table, column: str) -> list[Any]:
    return [v for v in table.values(column) if v is not None
            and not (isinstance(v, str) and not v.strip())]


def _sample_values(table: Table, column: str, *, limit: int = 3) -> tuple[Any, ...]:
    """Up to `limit` DISTINCT present values, in the order they appear — three
    copies of the same value teach a reader nothing a schema couldn't
    already tell them."""
    out: list[Any] = []
    seen: set[str] = set()
    for v in _present_values(table, column):
        key = str(v)
        if key in seen:
            continue
        seen.add(key)
        out.append(v)
        if len(out) >= limit:
            break
    return tuple(out)


def _min_max(
    table: Table, column: str, prof: ColumnProfile,
) -> tuple[Optional[Any], Optional[Any]]:
    """min/max for a numeric or date column, `(None, None)` for anything else
    — a min/max of a coded field or free text is not a fact, it is an
    accident of sort order."""
    if prof.kind == "number":
        nums = [float(v) for v in _present_values(table, column)
                if isinstance(v, (int, float)) and not isinstance(v, bool)]
        return (min(nums), max(nums)) if nums else (None, None)
    if prof.kind == "date":
        vals = [str(v) for v in _present_values(table, column)]
        return (min(vals), max(vals)) if vals else (None, None)
    return None, None


#: A table this small is read whole rather than summarised — the worked case
#: is an 8-row methodology sheet whose last two rows are the entire
#: explanation for a churn finding, and a schema-level summary of it
#: ("8 rows, 2 columns") would lose the one thing worth reading.
PASSTHROUGH_SHEET_MAX_ROWS = 20

#: How much of a candidate "field" column has to be distinct values before a
#: two-column table reads as field/value rather than a narrow data table. A
#: real field/value sheet names a different field on every row; a genuine
#: two-column data table (e.g. `account`, `mrr`) repeats its label column
#: constantly, so a high bar here is what tells the two apart.
FIELD_VALUE_MIN_KEY_UNIQUENESS = 0.9


def _looks_like_field_value(table: Table) -> bool:
    if len(table.columns) != 2:
        return False
    key_col = table.columns[0]
    prof = profile(table, key_col)
    if prof.filled == 0:
        return False
    return (prof.distinct / prof.filled) >= FIELD_VALUE_MIN_KEY_UNIQUENESS


def is_passthrough_sheet(table: Table) -> bool:
    """A MARK, not a decision — see the module docstring. True for a table
    small enough to read whole, or shaped like a field/value sheet even if it
    runs longer than the row bound."""
    return len(table.rows) <= PASSTHROUGH_SHEET_MAX_ROWS \
        or _looks_like_field_value(table)


def passthrough_columns(table: Table) -> tuple[str, ...]:
    """Free-text columns that sit beside a coded column on the SAME table —
    the worked case is a free-text loss-notes column next to a coded
    loss-reason column, where the code is a pointer and the prose is the
    finding. A mark, not a decision: nothing here truncates or includes
    anything."""
    profs = profiles(table)
    if not any(p.is_coded for p in profs.values()):
        return ()
    return tuple(name for name, p in profs.items() if p.is_freetext)


def _jsonable(v: Any) -> Any:
    if isinstance(v, datetime):
        return v.isoformat()
    return v


@dataclass(frozen=True)
class SchemaColumn:
    """One column's SHAPE — never its rows."""
    name: str
    kind: str
    distinct: int
    filled: int
    missing_share: float
    is_coded: bool
    is_freetext: bool
    sample_values: tuple[Any, ...]
    min_value: Optional[Any] = None
    max_value: Optional[Any] = None

    def to_json(self) -> dict:
        return {
            "name": self.name, "kind": self.kind, "distinct": self.distinct,
            "filled": self.filled, "missing_share": self.missing_share,
            "is_coded": self.is_coded, "is_freetext": self.is_freetext,
            "sample_values": [str(v) for v in self.sample_values],
            "min_value": _jsonable(self.min_value),
            "max_value": _jsonable(self.max_value),
        }


@dataclass(frozen=True)
class TableSchema:
    """One table's SHAPE — name, dating, row count, and every column's
    shape. There is no `rows` field on this type, on purpose."""
    name: str
    label: str
    origin: str
    source_type: str
    as_of: Optional[datetime]
    as_of_rule: str
    row_count: int
    columns: tuple[SchemaColumn, ...]
    passthrough_sheet: bool
    passthrough_columns: tuple[str, ...]

    def to_json(self) -> dict:
        return {
            "name": self.name, "label": self.label, "origin": self.origin,
            "source_type": self.source_type,
            "as_of": self.as_of.isoformat() if self.as_of else None,
            "as_of_rule": self.as_of_rule, "row_count": self.row_count,
            "columns": [c.to_json() for c in self.columns],
            "passthrough_sheet": self.passthrough_sheet,
            "passthrough_columns": list(self.passthrough_columns),
        }


@dataclass(frozen=True)
class SchemaSnapshot:
    """The schema view of a run's tables, plus the workspace clock they were
    read against — carried alongside so a later stage can compute a gap from
    `as_of` without this module deciding what that gap means. No staleness
    filtering happens here; passing the fact through is the whole job."""
    today: datetime
    tables: tuple[TableSchema, ...]

    def to_json(self) -> dict:
        return {"today": self.today.isoformat(),
                "tables": [t.to_json() for t in self.tables]}


def schema_view(
    tables: Sequence[Table], *, today: Optional[datetime] = None,
) -> SchemaSnapshot:
    """The SHAPE of every table a selector may read — never a row.

    Pure: reads `table.columns` and profiles them. Whether `as_of` has been
    derived is entirely up to the caller (`with_as_of`) — a table that has
    not been dated yet simply reports `as_of=None, as_of_rule=""`; this
    function never derives one on a caller's behalf, so it cannot silently
    date a table differently than whatever produced it.
    """
    today = today or datetime.now(timezone.utc)
    out = []
    for table in tables:
        profs = profiles(table)
        columns = tuple(
            SchemaColumn(
                name=name, kind=prof.kind, distinct=prof.distinct,
                filled=prof.filled, missing_share=prof.missing_share,
                is_coded=prof.is_coded, is_freetext=prof.is_freetext,
                sample_values=_sample_values(table, name),
                min_value=_min_max(table, name, prof)[0],
                max_value=_min_max(table, name, prof)[1],
            )
            for name, prof in profs.items()
        )
        out.append(TableSchema(
            name=table.name, label=table.label, origin=table.origin,
            source_type=table.source_type, as_of=table.as_of,
            as_of_rule=table.as_of_rule, row_count=len(table.rows),
            columns=columns, passthrough_sheet=is_passthrough_sheet(table),
            passthrough_columns=passthrough_columns(table),
        ))
    return SchemaSnapshot(today=today, tables=tuple(out))
