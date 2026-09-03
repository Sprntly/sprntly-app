"""Deterministic backfill: recover a stated commercial figure a historical
`kg_signal.content` paraphrase already carries, with zero LLM calls.

WHY THIS EXISTS. The extraction pass now writes a grounded
`amount`/`currency`/`basis`/`certainty` shape onto `commercial_term`/`pricing`
signals (see `app.graph.extractor._grounded_amount_properties`), but only at
ingest time. Every signal written before that change predates it, so the
feature finds nothing on a historical corpus. This module re-reads the
paraphrase text those old signals already carry and, where it holds an
unambiguous dollar figure, fills the same two fields.

THE PROVENANCE PROBLEM, AND WHY THIS MODULE NEVER WRITES `certainty` VIA THE
SHARED VALIDATOR. `content` is the model's paraphrase, not the source text — a
figure read back out of it has no `verbatim_quote` behind it, so it is not
grounded the same way an ingest-time figure is (transcription error, not
fabrication: the paraphrase itself was written under a grounding gate, so the
number came from verified text, but the model could have copied it wrong).
A backfilled row must therefore be distinguishable from an ingest-time one.
`_grounded_amount_properties`'s `certainty` vocabulary
(`quoted`/`asked`/`estimated-by-speaker`) is deliberately CLOSED to states an
extraction call can actually observe — calling it with anything else silently
drops the key, which is exactly the point: this module reuses that function
for the numeric SHAPE (float coercion, currency normalisation), then stamps
`certainty=BACKFILL_CERTAINTY` itself, a sentinel value the real extractor's
own gate would never let through. A downstream reader can trust
`certainty in {"quoted", "asked", "estimated-by-speaker"}` as "this number
came off a verbatim-grounded utterance" and treat `BACKFILL_CERTAINTY` as
"this number came off a paraphrase, hedge accordingly" — never the same claim.

`basis` is left untouched either way: it is not recoverable from a paraphrase
and this module never guesses it (a missing field stays missing, per I3).

ELIGIBILITY MIRRORS INGEST EXACTLY. Only signals whose `kind` is in
`app.graph.extractor._AMOUNT_ELIGIBLE_KINDS` (`commercial_term`, `pricing`)
are considered — the same gate `extract_document`/the checklist pass apply —
so a backfilled row is eligible for `amount` in exactly the cases an ingest-
time row would have been, never a wider set.

RESUMABLE AND IDEMPOTENT WITHOUT A SEPARATE QUEUE. A signal that already
carries `amount` is skipped outright (never re-derived — see R4 in the
ticket this implements). That skip IS the resume checkpoint: a crashed or
re-invoked run simply re-scans the company's eligible signals from the start,
and everything already enriched is a no-op, so a second run over the same
population enriches exactly zero new rows. `app.db.crucible_backfill_runs`
records each invocation for audit, but carries no per-signal claim state —
there is nothing to claim, because the signal row itself already is the
completion marker.
"""
from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from typing import Any, Optional

from app.db import crucible_backfill_runs
from app.db.client import require_client
from app.graph.extractor import _AMOUNT_ELIGIBLE_KINDS, _grounded_amount_properties

logger = logging.getLogger(__name__)

#: Bump this whenever the parsing rules below change, so an old run's numbers
#: are never silently compared against a newer pattern (R5 auditability).
PATTERN_VERSION = "dollar-v1"

#: The sentinel `certainty` value a backfilled row carries. Deliberately NOT
#: a member of `extractor._COMMERCIAL_CERTAINTY_VALUES` — see module docstring.
BACKFILL_CERTAINTY = "derived-from-summary"

_PAGE_SIZE = 500

#: A run-away safety valve, not a business rule: no single invocation reads
#: more than this many candidate rows, so a mis-typed `--company` against a
#: much larger tenant than expected fails loud (KeyError-free `None` company
#: never reaches this far) rather than paging for an unbounded time.
_MAX_ROWS_PER_RUN = 200_000

#: Scale words a stated dollar figure may carry ("$500k", "$2.4 million").
_DOLLAR_SCALE: dict[str, float] = {
    "k": 1e3, "thousand": 1e3,
    "m": 1e6, "million": 1e6,
    "b": 1e9, "billion": 1e9,
}

#: A single dollar figure, `$`-prefixed only.
#:
#: WHY `$`-ONLY. The costing pass measured the `$`-prefixed subset at 1,989
#: hits and called it "the high-precision set to trust"; the bare
#: digit-plus-k/m subset (819, no currency symbol) was measured as "looser"
#: and the source of the probe's false positives — a bare "10m" or "3k" in a
#: paraphrase is at least as often a headcount or a percentage as a dollar
#: figure. This pattern only ever matches text with an explicit `$`, which is
#: an unambiguous currency marker by construction — a "no currency marker"
#: ambiguity case therefore never reaches this regex at all; it is excluded
#: by the pattern, not by a runtime check. Extending to `£`/`€` would be a
#: small change but is unverified against any measured data and is left out
#: deliberately.
#:
#: WHY THE NUMBER GROUP IS SHAPED THIS WAY. The costing pass's own probe
#: sample showed "clipping mid-number" (`$NN,NNN,` — a truncated match that
#: silently kept only a prefix of the real figure). `\d{1,3}(?:,\d{3})+`
#: requires PROPERLY grouped thousands separators end-to-end (never stops
#: after one comma group the way a naive `[\d,]+` can), and `\d+` on its own
#: covers a plain ungrouped number ("$1500"). A malformed group (say "$12,34"
#: — a 2-digit second group) matches neither alternative in full, so this
#: pattern never returns a truncated prefix of a bad number; at worst it
#: fails to match at all, which this module treats as "no figure found",
#: never a wrong figure.
_DOLLAR_FIGURE = re.compile(
    r"\$\s?(?P<num>\d{1,3}(?:,\d{3})+|\d+)(?!,?\d)(?:\.(?P<cents>\d+))?"
    r"\s?(?P<scale>k|m|b|thousand|million|billion)?\b",
    re.IGNORECASE,
)


def find_dollar_figures(text: str) -> list[float]:
    """Every DISTINCT dollar amount `text` states, after applying any k/m/b
    scale word. Order-preserving, de-duplicated by resolved value — the same
    figure mentioned twice ("the deal is $50,000... so $50,000 total") is one
    figure, not two, and is not treated as ambiguous."""
    out: list[float] = []
    seen: set[float] = set()
    for m in _DOLLAR_FIGURE.finditer(text or ""):
        num_str = m.group("num")
        cents = m.group("cents")
        scale = m.group("scale")
        try:
            num = float(num_str.replace(",", ""))
        except ValueError:  # pragma: no cover - defensive, regex guarantees digits
            continue
        if cents:
            num += float(f"0.{cents}")
        mult = _DOLLAR_SCALE.get(scale.lower(), 1.0) if scale else 1.0
        amount = round(num * mult, 2)
        if amount not in seen:
            seen.add(amount)
            out.append(amount)
    return out


@dataclass
class BackfillCounts:
    examined: int = 0
    enriched: int = 0
    skipped_already_has_amount: int = 0
    skipped_no_figure_found: int = 0
    skipped_ambiguous_multiple_figures: int = 0

    def as_dict(self) -> dict[str, int]:
        return {
            "already_has_amount": self.skipped_already_has_amount,
            "no_figure_found": self.skipped_no_figure_found,
            "ambiguous_multiple_figures": self.skipped_ambiguous_multiple_figures,
        }

    @property
    def total_skipped(self) -> int:
        return (
            self.skipped_already_has_amount
            + self.skipped_no_figure_found
            + self.skipped_ambiguous_multiple_figures
        )


@dataclass
class SignalDecision:
    """What the sweep decided about one signal — used by both the live write
    path and the mutation-proof/unit-test path so the two never drift apart."""

    signal_id: str
    outcome: str  # "enriched" | "already_has_amount" | "no_figure_found" | "ambiguous_multiple_figures"
    new_properties: Optional[dict[str, Any]] = None


def decide_for_signal(properties: dict[str, Any] | None, content: str) -> SignalDecision:
    """Pure decision function: given one signal's existing `properties` and
    `content`, decide whether it is already enriched, unresolvable, ambiguous,
    or ready to be enriched — and if the latter, return the exact new
    `properties` dict to write (R4/R6: touches `amount`/`currency`/`certainty`
    only, everything else in `properties` passes through unchanged)."""
    props = dict(properties or {})
    existing_amount = props.get("amount")
    if isinstance(existing_amount, (int, float)) and not isinstance(existing_amount, bool):
        return SignalDecision(signal_id="", outcome="already_has_amount")

    figures = find_dollar_figures(content or "")
    if not figures:
        return SignalDecision(signal_id="", outcome="no_figure_found")
    if len(figures) > 1:
        return SignalDecision(signal_id="", outcome="ambiguous_multiple_figures")

    validated = _grounded_amount_properties({"amount": figures[0], "currency": "USD"})
    if "amount" not in validated:
        # Reachable: the shared validator's `_is_number` excludes a literal
        # `0` (a stated figure of zero is not a real quoted amount either —
        # same exclusion the extractor applies at ingest), so a parsed "$0"
        # lands here rather than being written as a real amount.
        return SignalDecision(signal_id="", outcome="no_figure_found")

    new_props = dict(props)
    new_props["amount"] = validated["amount"]
    if "currency" in validated:
        new_props["currency"] = validated["currency"]
    new_props["certainty"] = BACKFILL_CERTAINTY
    return SignalDecision(signal_id="", outcome="enriched", new_properties=new_props)


def _page_eligible_signals(client: Any, company_id: str, page: int) -> list[dict[str, Any]]:
    offset = page * _PAGE_SIZE
    resp = (
        client.table("kg_signal")
        .select("id, kind, content, properties")
        .eq("enterprise_id", company_id)
        .in_("kind", sorted(_AMOUNT_ELIGIBLE_KINDS))
        .order("id")
        .range(offset, offset + _PAGE_SIZE - 1)
        .execute()
    )
    return resp.data or []


def run_backfill(*, company_id: str, apply: bool, limit: Optional[int] = None) -> dict[str, Any]:
    """Sweep every `commercial_term`/`pricing` signal for `company_id`,
    filling `amount`/`currency` (+ the backfill `certainty` marker) where the
    signal's `content` states exactly one unambiguous dollar figure and the
    signal does not already carry an ingest-time `amount`.

    `apply=False` (the default a caller must opt out of explicitly) performs
    every read and every decision but writes nothing — R2's dry-run-first
    contract. Returns a summary dict shaped for both the CLI printer and
    tests; also persists a `crucible_backfill_runs` audit row either way.
    """
    if not company_id:
        raise ValueError("company_id is required — there is no global mode")

    client = require_client()
    counts = BackfillCounts()
    mode = "apply" if apply else "dry_run"
    run_row = crucible_backfill_runs.start(
        company_id=company_id, mode=mode, pattern_version=PATTERN_VERSION,
    )
    run_id = run_row.get("id")

    try:
        page = 0
        while True:
            rows = _page_eligible_signals(client, company_id, page)
            if not rows:
                break
            for row in rows:
                if limit is not None and counts.examined >= limit:
                    break
                if counts.examined >= _MAX_ROWS_PER_RUN:
                    break
                counts.examined += 1
                decision = decide_for_signal(row.get("properties"), row.get("content") or "")
                if decision.outcome == "already_has_amount":
                    counts.skipped_already_has_amount += 1
                elif decision.outcome == "no_figure_found":
                    counts.skipped_no_figure_found += 1
                elif decision.outcome == "ambiguous_multiple_figures":
                    counts.skipped_ambiguous_multiple_figures += 1
                elif decision.outcome == "enriched":
                    counts.enriched += 1
                    if apply:
                        (
                            client.table("kg_signal")
                            .update({"properties": decision.new_properties})
                            .eq("enterprise_id", company_id)
                            .eq("id", row["id"])
                            .execute()
                        )
            if (
                (limit is not None and counts.examined >= limit)
                or counts.examined >= _MAX_ROWS_PER_RUN
                or len(rows) < _PAGE_SIZE
            ):
                break
            page += 1

        crucible_backfill_runs.finish(
            run_id=run_id,
            company_id=company_id,
            status="completed",
            examined_count=counts.examined,
            enriched_count=counts.enriched,
            skipped_counts=counts.as_dict(),
        )
    except Exception as exc:  # noqa: BLE001 - record the failure, then re-raise
        crucible_backfill_runs.finish(
            run_id=run_id,
            company_id=company_id,
            status="failed",
            examined_count=counts.examined,
            enriched_count=counts.enriched,
            skipped_counts=counts.as_dict(),
            error=str(exc),
        )
        logger.warning(
            "crucible_backfill_run_error company_id=%s run_id=%s", company_id, run_id,
            exc_info=True,
        )
        raise

    return {
        "run_id": run_id,
        "company_id": company_id,
        "mode": mode,
        "pattern_version": PATTERN_VERSION,
        "examined": counts.examined,
        "enriched": counts.enriched,
        "skipped": counts.as_dict(),
        "total_skipped": counts.total_skipped,
    }
