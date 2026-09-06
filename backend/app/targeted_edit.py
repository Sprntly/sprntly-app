"""Targeted-edit output contract for document edits (PRD first; goal-report next).

Behind the `TARGETED_EDIT_ENABLED` flag (default OFF). When off, every caller
keeps its current full-document re-emit path byte-for-byte. When on, the edit
LLM call is asked for ONLY the changed sections as splice ops instead of the
whole re-emitted document, and this module splices them back into the stored
document deterministically — validating the result against every gate before any
write, and falling back to the current full-emit call on ANY gate failure.

Why this exists (edit-latency reduction): a PRD edit re-emits the whole
~5.5k-token document even when one or two sections change, and output tokens are
essentially the entire wall-clock (~16.7 ms/tok), so a 1-2 section edit costs
~92s of which ~90% is re-typing unchanged bytes. Emitting only the changed
sections drops the output to ~300-800 tokens (~12-18s). See
`TARGETED-EDIT-DESIGN.md` for the measurements and the go decision.

The safety argument is the whole point: text-keyed splicing CAN corrupt a
document if the model names the wrong anchor or truncates a payload, so the
design is **fail-to-slow, never fail-to-corrupt** — every gate is deterministic,
and any failure discards the splice and re-runs the proven full-emit path. Net
correctness risk vs today is zero: the fallback IS today's behavior.

Shape of the contract (when ON), replacing the `{html: <full doc>}` schema:

    {
      "mode": "targeted" | "full" | "none",
      "ops": [
        # section-level (the original unit: a whole top-level section block)
        {"op": "replace"|"delete"|"insert_after",
         "section": <delimiter text>,
         "after": <delimiter text>,          # insert_after only
         "new_html": <the section block incl. its own delimiter>},
        # block-level, behind `TARGETED_EDIT_BLOCKS_ENABLED`
        {"op": "replace_blocks"|"delete_blocks",
         "section": <delimiter text>,
         "from": <ordinal>, "to": <ordinal>,  # 0-based, inclusive
         "anchor_text": <echo of the target block's visible text>,
         "new_html": <the replacement blocks alone>},
        {"op": "insert_blocks_after",
         "section": <delimiter text>, "after": <ordinal>,  # -1 prepends
         "anchor_text": <...>, "new_html": <the new blocks alone>},
      ],
      "full_html": <full document>,          # mode == "full" only
      "sections_changed": [<name>, ...],     # mode == "full" only
      "summary": <one line>
    }

`mode:"full"` is the model's own escape hatch for edits that cannot be expressed
as replacements (reorder, "make it shorter", restructure) — the server takes
`full_html` through the existing write path unchanged.

`mode:"none"` says the instruction asked for NO document change — it was a
question or a comment. Without it the base prompt's "return the document
UNCHANGED" rule could only be expressed as an empty `ops` array, which is a
rejection, so a question typed into the chat cost a full document rewrite.

An `<ordinal>` is 0-based and addresses a BLOCK: a top-level element inside a
section (each `<p>`, `<table>`, `<ul>`, `<div>`), not counting the section's own
delimiter. `"2.3"` — a quoted, dotted string — addresses child 3 of block 2, and
that is the depth cap: only the `<tr>` of a table's `<tbody>` or the `<li>` of a
list. `anchor_text` is a short echo of the target's visible text: an ordinal is
unique but UNVERIFIED, so without the echo an off-by-one would splice silently
onto the wrong block — a corruption class the section-only design does not have.

The degradation ladder, fail-to-slow at every rung. Everything below L1 is
today's system, unchanged:

    L0   mode:"none"                    -> no splice, no write             1 call
    L1   targeted, block-level ops      -> block splice + gates            1 call
    L2   targeted, section-level ops    -> section splice + gates          1 call
    L3   mode:"full"                    -> well-formedness + write         1 call
    L3b  targeted, no ops, good full_html -> treated as L3 (salvage)       1 call
    L4   ANY gate failure               -> discard, re-run full-emit      2 calls
    L5   full-emit returns no HTML      -> RuntimeError, doc untouched    2 calls

Granularity is chosen per-op by the model and L1/L2 mix freely in one response,
so an edit that genuinely rewrites a section still emits a section op and costs
exactly what it costs today: no working edit gets larger. L1 is strictly
additive — every failure of it lands on L4, which IS today's behavior — so net
corruption risk versus today is zero.

Block ops sit behind their own `TARGETED_EDIT_BLOCKS_ENABLED` sub-flag, the same
way `goalreport_enabled` gates the goal-report path, so the finer unit can roll
back on its own. OFF is a true kill switch: the contract clause omits the block
verbs AND the splice engine rejects them as an unknown op kind.

This module is deliberately dependency-light (stdlib only) so the splice engine
and every gate are unit-testable without the app/LLM/DB stack, and so it adds no
new dependency. The per-document differences live in a small `SectionModel`.
"""
from __future__ import annotations

import logging
import os
import re
from dataclasses import dataclass
from html.parser import HTMLParser
from typing import Callable, List, Optional, Tuple

logger = logging.getLogger(__name__)


# ── Flag ─────────────────────────────────────────────────────────────────────

def enabled() -> bool:
    """Read at CALL time so the flag is flippable without a redeploy.

    Default OFF: absent/empty env var => full-emit path, byte-identical to today.
    `TARGETED_EDIT_ENABLED=1|true|yes|on` turns it on.
    """
    raw = (os.environ.get("TARGETED_EDIT_ENABLED") or "").strip().lower()
    return raw in {"1", "true", "yes", "on"}


def goalreport_enabled() -> bool:
    """Dedicated sub-gate for the goal-report path, independent of the PRD flag.

    Default OFF: `TARGETED_EDIT_GOALREPORT_ENABLED=1|true|yes|on` turns it on.
    Goal-report gets its own gate so it can dark-launch / roll back without
    disturbing the proven PRD path — its win profile differs (partial on the big
    findings section) and its `count_heading` normalize path is live-untested.
    """
    raw = (os.environ.get("TARGETED_EDIT_GOALREPORT_ENABLED") or "").strip().lower()
    return raw in {"1", "true", "yes", "on"}


def blocks_enabled() -> bool:
    """Dedicated sub-gate for BLOCK-level ops, independent of the two above.

    Default OFF: `TARGETED_EDIT_BLOCKS_ENABLED=1|true|yes|on` turns it on. Block
    ops get their own gate — the same pattern `goalreport_enabled` uses — so the
    finer emit unit can dark-launch and roll back without disturbing the proven
    section-level path. OFF is a true kill switch, not just a prompt change: the
    contract clause omits the block verbs AND the splice engine rejects them as
    an unknown op kind, which is byte-for-byte today's behavior.
    """
    raw = (os.environ.get("TARGETED_EDIT_BLOCKS_ENABLED") or "").strip().lower()
    return raw in {"1", "true", "yes", "on"}


# ── Fallback signal ──────────────────────────────────────────────────────────

class FallbackNeeded(Exception):
    """Raised when the targeted splice cannot be trusted. The caller catches this
    and transparently re-runs the current full-emit call — the slow-but-proven
    lane. Carries the gate/reason for the warning log."""


# ── SectionModel: per-document adapter ───────────────────────────────────────

@dataclass(frozen=True)
class SecondaryDelimiter:
    """A NON-primary, self-contained block that is addressable as its own section
    even though it carries no primary delimiter and is nested inside the last
    primary section.

    The live case: the v4.7 legacy "User input needed" appendix
    `<div class="appendix">…</div>`, which real in-production PRDs carry as the
    final block INSIDE the last `<div class="eyebrow">Risks</div>` section. The
    model naturally names it "Appendix" (or "User input needed"), so without this
    it can never resolve and `apply_answers` falls back on every such document.

    * `pattern` — matches the block's OPENING tag at a section boundary
      (`<div class="appendix">`).
    * `label` — the canonical section name (`Appendix`).
    * `aliases` — other names the model reliably uses for the same block
      (`User input needed` — the block's own `<h3>`), all resolving to `label`.

    A secondary block is treated as a flat top-level section: it starts at its
    opening tag and runs to the next delimiter (or the document wrapper). This is
    safe precisely because the appendix is the LAST content block before the
    `.page`/`.frame` close, so peeling it into its own section leaves both the
    preceding primary section and the wrapper div-balanced.
    """

    pattern: "re.Pattern[str]"
    label: str
    aliases: tuple = ()

    def names(self) -> tuple:
        return (self.label,) + tuple(self.aliases)


@dataclass(frozen=True)
class SectionModel:
    """The small per-document differences the shared splice engine needs.

    * `name` — for logging.
    * `delimiter_re` — the PRIMARY delimiter, group(1) = the section name
      (PRD: `<div class="eyebrow">NAME</div>`; goal-report: `<h2>NAME</h2>`).
    * `count_heading` — strip a trailing ` (\\d+)` count from dynamic headings
      before matching (goal-report's "What the evidence says (63)"; PRD has no
      such headings, so this is False there).
    * `secondary` — extra addressable blocks that carry no primary delimiter
      (PRD's legacy `<div class="appendix">`). Empty for house-format v4.8 PRDs
      and for goal-report, so those paths are unchanged.
    """

    name: str
    delimiter_re: "re.Pattern[str]"
    count_heading: bool = False
    secondary: tuple = ()

    def normalize(self, section_name: str) -> str:
        """Canonicalize a section name for anchor matching: collapse whitespace,
        casefold, and (when the doc has dynamic-count headings) drop a trailing
        ` (N)` count so "What the evidence says" matches "…(63)"."""
        s = (section_name or "").strip()
        if self.count_heading:
            s = re.sub(r"\s*\(\s*\d+\s*\)\s*$", "", s)
        return re.sub(r"\s+", " ", s).strip().casefold()

    def _alias_map(self) -> dict:
        """normalized-alias -> normalized-canonical, for the secondary blocks.
        So the model naming the appendix "User input needed" resolves the same
        section as naming it "Appendix"."""
        m: dict = {}
        for sd in self.secondary:
            canon = self.normalize(sd.label)
            for n in sd.names():
                m[self.normalize(n)] = canon
        return m

    def resolve(self, section_name: str) -> str:
        """Normalize `section_name` and fold any secondary alias onto its
        canonical name. Primary sections normalize to themselves."""
        n = self.normalize(section_name)
        return self._alias_map().get(n, n)

    def iter_delimiters(self, text: str) -> list:
        """All delimiter marks in `text` as (start, canonical_name), primary and
        secondary, in document order."""
        marks: list = []
        for m in self.delimiter_re.finditer(text):
            marks.append((m.start(), m.group(1).strip()))
        for sd in self.secondary:
            for m in sd.pattern.finditer(text):
                marks.append((m.start(), sd.label))
        marks.sort(key=lambda x: x[0])
        return marks

    def delimiter_name_at_start(self, text: str) -> Optional[str]:
        """If `text` begins with a delimiter (primary or secondary), its canonical
        name; else None. Used by gate 2 to check a payload's leading delimiter."""
        m = self.delimiter_re.match(text)
        if m:
            return m.group(1).strip()
        for sd in self.secondary:
            if sd.pattern.match(text):
                return sd.label
        return None


# PRD: primary delimiter `<div class="eyebrow">NAME</div>`; the fixed v4.8 spine
# (Context, Problem, Evidence, Users, Goal, Hypothesis, Requirements, Risks — no
# dynamic counts) so identity normalize. PRD is never sanitized, so the classes
# survive. The legacy v4.7 `<div class="appendix">` "User input needed" block —
# still live in pre-v4.8 / company-template PRDs, and the whole reason
# `apply_answers` exists — is registered as a secondary addressable section so the
# model's "Appendix"/"User input needed" op resolves instead of falling back.
PRD_SECTION_MODEL = SectionModel(
    name="prd",
    delimiter_re=re.compile(r'<div class="eyebrow">(.*?)</div>', re.DOTALL),
    count_heading=False,
    secondary=(
        SecondaryDelimiter(
            pattern=re.compile(r'<div class="appendix"\s*>'),
            label="Appendix",
            aliases=("User input needed",),
        ),
    ),
)


# Goal-report: primary delimiter `<h2>NAME</h2>`. The doc is machine-rendered by
# crucible/report.render_report_html and SANITIZED on every write to a bare tag
# allowlist (no class/id/data-*), so heading TEXT is the only anchor. Two
# headings carry a live count — "What the evidence says (N)" and "Considered and
# ruled out (N)" — so count_heading strips the trailing " (N)" and the anchor
# matches whether the model echoes, drops, or changes the count. The preamble is
# just <h1>{goal}</h1> (no .frame/.page/<style> wrapper), so _div_net(preamble)
# == 0 and there is no wrapper to peel. No appendix / nested addressable block →
# no secondary delimiters. The <h3> sub-headings (per-finding blocks, the
# coverage note) ride inside their parent <h2> and are not independently
# addressable — an edit to one re-emits its whole parent section.
GOALREPORT_SECTION_MODEL = SectionModel(
    name="goal_report",
    delimiter_re=re.compile(r"<h2>(.*?)</h2>", re.DOTALL),
    count_heading=True,
    secondary=(),
)


# ── Output contract schema (replaces the `html` full-doc field, when ON) ──────

TARGETED_EDIT_SCHEMA: dict = {
    "type": "object",
    "properties": {
        # "none" is the model's way to say the instruction asked for no document
        # change (a question or a comment). Without it the base prompt's "return
        # the document UNCHANGED" rule can only be expressed as an empty `ops`
        # array, which is a rejection — so a question cost a full rewrite.
        "mode": {"type": "string", "enum": ["targeted", "full", "none"]},
        "ops": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "op": {
                        "type": "string",
                        "enum": [
                            # section-level (unchanged)
                            "replace", "delete", "insert_after",
                            # block-level, behind `TARGETED_EDIT_BLOCKS_ENABLED`
                            "replace_blocks", "insert_blocks_after",
                            "delete_blocks",
                        ],
                    },
                    "section": {"type": "string"},
                    # section-level `insert_after` names the PRECEDING SECTION
                    # here; block-level `insert_blocks_after` puts a 0-based
                    # block ordinal here instead. The op verb disambiguates.
                    #
                    # Every ordinal is declared as a STRING — "2", "2.3", "-1" —
                    # and this field keeps the exact type it has today. That is
                    # deliberate: this dict is handed to the API verbatim as a
                    # tool `input_schema`, so a union type here would be a new
                    # risk on the SECTION path (which is proven) to buy nothing
                    # on the block path. `_parse_ordinal` accepts a bare integer
                    # anyway, so a model that emits `2` instead of "2" still
                    # works; it just is not what we ask for.
                    "after": {"type": "string"},
                    "from": {"type": "string"},
                    "to": {"type": "string"},
                    # A short echo of the target block's VISIBLE text. An ordinal
                    # is unique but unverified; the echo is what makes an
                    # off-by-one fail loudly instead of splicing silently onto
                    # the wrong block. ~12 output tokens per op.
                    "anchor_text": {"type": "string"},
                    "new_html": {"type": "string"},
                },
                "required": ["op", "section"],
            },
        },
        "full_html": {"type": "string"},
        # For mode:"full" the model lists the changed section names here (there
        # are no ops to derive them from), so the chat's "Updated: X, Y"
        # confirmation keeps working — matching today's `sections_changed`.
        "sections_changed": {"type": "array", "items": {"type": "string"}},
        "summary": {"type": "string"},
    },
    "required": ["mode", "summary"],
}


# ── Prompt derivation ────────────────────────────────────────────────────────

def _targeted_contract_clause(model: SectionModel) -> str:
    delim_example = (
        '<div class="eyebrow">Goal</div>…'
        if model.name == "prd"
        else "<h2>Section name</h2>…"
    )
    return (
        "\n\n=== OUTPUT CONTRACT (targeted edit) ===\n"
        "Do NOT re-emit the whole document. Return ONLY the sections your edit "
        "actually changes, as a list of ops.\n"
        '- Set `mode` to "targeted" and return `ops`, one per changed section. '
        "Each `op` is:\n"
        '  • "replace": the section changed — `section` is the EXACT current '
        "section name (the delimiter text), and `new_html` is that section's "
        "FULL new HTML INCLUDING its own leading delimiter "
        f"(e.g. `{delim_example}`).\n"
        '  • "delete": remove a whole section — `section` names it, omit '
        "`new_html`.\n"
        '  • "insert_after": add a new section — `after` is the EXISTING section '
        "to place it after, `section` is the new section's name, and `new_html` "
        "is the new section block including its delimiter.\n"
        "- Leave every unchanged section OUT of `ops` entirely — the server keeps "
        "them byte-for-byte. Never include the title, byline, `<style>`, or the "
        "document wrapper in any `new_html`.\n"
        '- Set `mode` to "full" and return the ENTIRE document in `full_html` '
        "ONLY when the edit cannot be expressed as section replacements — a "
        "reorder, a document-wide rewrite (\"make it shorter\"), or a "
        "restructure. Prefer targeted ops whenever possible. In `full` mode, "
        "ALSO list the human-readable names of the sections you changed in "
        '`sections_changed` (e.g. ["Requirements", "Goal"]).\n'
        "- `summary`: one line describing the edit.\n"
        + _no_change_clause()
        + _block_ops_clause(model)
        + "Return ONLY the structured object."
    )


def _no_change_clause() -> str:
    """Change A1. The base edit prompt already says "if the instruction does not
    request a change, return the document UNCHANGED" — but under the ops
    contract there is no way to SAY that: an empty `ops` array is a rejection,
    which costs a whole second full-document call. `mode:"none"` makes the
    cheap, correct answer expressible."""
    return (
        '- Set `mode` to "none" when the instruction does not request a change '
        "to the document at all — it is a question, an observation, or a "
        "comment. Return NO ops and NO `full_html`; put the reason in "
        "`summary`. Do NOT re-emit the document just to leave it unchanged.\n"
    )


def _block_ops_clause(model: SectionModel) -> str:
    """Change B, behind `TARGETED_EDIT_BLOCKS_ENABLED`. Flag OFF returns "" so
    the prompt is byte-identical to the section-only contract.

    Blocks are the top-level elements INSIDE a section, 0-based, in document
    order. Two levels is the cap — blocks, and the rows/items of a table or list
    block — because that covers "add a requirement" and "add a risk" (the common
    asks that today cost a whole table or list) while keeping the gate matrix
    small enough to review.
    """
    if not blocks_enabled():
        return ""
    child_example = (
        '"2.3" means row 3 of the table in block 2'
        if model.name == "prd"
        else '"2.3" means item 3 of the list in block 2'
    )
    return (
        "- PREFER a BLOCK op over a whole-section `replace` whenever the edit "
        "touches only part of a section — it is far cheaper. Blocks are the "
        "top-level elements inside a section (each `<p>`, `<table>`, `<ul>`, "
        "`<div>`), numbered from 0 in document order, NOT counting the "
        "section's own delimiter. Block ops are:\n"
        '  • "replace_blocks": `section`, `from` and `to` (the 0-based, '
        "inclusive block range — use the same number for a single block), "
        "`anchor_text`, and `new_html` = the replacement blocks ONLY. The "
        "replacement's first element must be the same tag as the block at "
        "`from`.\n"
        '  • "insert_blocks_after": `section`, `after` = the 0-based block to '
        "place the new blocks after, `anchor_text`, and `new_html` = the new "
        "blocks. **To ADD something to a section, append: set `after` to the "
        "LAST block's number.** Use `after: -1` to put the new blocks first "
        "(no `anchor_text` needed for -1).\n"
        '  • "delete_blocks": `section`, `from`, `to`, `anchor_text`, no '
        "`new_html`.\n"
        "- `anchor_text` is REQUIRED on every block op (except `after: -1`): "
        "copy the FIRST 24 characters of the VISIBLE TEXT of the block at "
        "`from` / `after`, exactly as it reads (or all of it, if it is "
        "shorter). This is how the server checks it is editing the block you "
        "meant; if it does not match, your whole edit is discarded and redone "
        "the slow way. Copy it, do not paraphrase it.\n"
        "- Ordinals are always QUOTED strings: `\"2\"`, `\"-1\"`.\n"
        "- To address ONE ROW of a table or ONE ITEM of a list, use a quoted "
        'DOTTED ordinal: `"2.3"` — ' + child_example + ". Rows of a "
        "`<thead>` are NOT addressable; only the body rows are. That is the "
        "deepest address there is — never nest further.\n"
        "- `new_html` in a block op is the block elements ALONE. It must be a "
        "whole number of complete elements, and must NEVER contain a section "
        "delimiter. Blocks you do not name are kept byte-for-byte.\n"
        "- Block ops and section ops can appear in the SAME `ops` list, but "
        "never both on the same section, and block ranges must not overlap.\n"
    )


def targeted_system(base_system: str, model: SectionModel) -> str:
    """The full-emit system prompt with its 'return the FULL HTML' instruction
    replaced by the targeted-ops contract.

    The base prompt's editing DISCIPLINE (change only what the instruction
    reaches, invent nothing, keep the house style) is preserved verbatim; only
    the final 'Return the FULL updated HTML …' paragraph is swapped for the ops
    contract. Matched by anchor phrase; if the anchor drifts, the clause is
    appended anyway (the contract still lands) and a warning is logged.

    The word "document" is OPTIONAL in the anchor: the PRD prompts say "Return
    the FULL updated HTML document in `html`, …" while the goal-report prompt
    says "Return the FULL updated HTML in `html`, …". Both must be REPLACED (not
    appended to) — a stray "return the full HTML" left in the prompt contradicts
    the "do NOT re-emit" ops contract and silently makes the model append instead
    of splice. Broadening the anchor here (rather than editing the goal-report
    prompt) keeps every base `_EDIT_SYSTEM` byte-identical to today on the
    flag-off path.
    """
    anchor = re.compile(
        r"Return the FULL updated HTML(?: document)?.*?a one-line `summary`[^.]*\.",
        re.DOTALL,
    )
    clause = _targeted_contract_clause(model).lstrip("\n")
    out, n = anchor.subn(clause, base_system)
    if n == 0:
        logger.warning(
            "targeted_edit: full-emit anchor not found in system prompt; "
            "appending contract clause instead"
        )
        out = base_system + _targeted_contract_clause(model)
    return out


# ── Well-formedness (stdlib HTMLParser — always available, no new dep) ────────

class _BalanceParser(HTMLParser):
    """Counts open/close of block tags to detect truncation / unbalanced splice.

    Void/self-closing tags are ignored. We only assert that every non-void tag
    opened is closed and vice-versa — a token-wall truncation leaves an open tag
    dangling, which this catches. Deliberately lenient about ordering (HTML is
    not XML); the section-set + preamble gates carry structural correctness.
    """

    _VOID = {
        "area", "base", "br", "col", "embed", "hr", "img", "input",
        "link", "meta", "param", "source", "track", "wbr",
    }

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.counts: dict = {}
        self.error_seen = False

    def handle_starttag(self, tag, attrs):
        if tag not in self._VOID:
            self.counts[tag] = self.counts.get(tag, 0) + 1

    def handle_endtag(self, tag):
        if tag not in self._VOID:
            self.counts[tag] = self.counts.get(tag, 0) - 1


def _is_well_formed(doc: str) -> bool:
    try:
        p = _BalanceParser()
        p.feed(doc)
        p.close()
    except Exception:  # noqa: BLE001 — any parser blow-up = treat as malformed
        return False
    return all(v == 0 for v in p.counts.values())


# ── Div balance helpers (wrapper detection) ──────────────────────────────────

_DIV_OPEN_RE = re.compile(r"<div\b", re.IGNORECASE)
_DIV_CLOSE_RE = re.compile(r"</div>", re.IGNORECASE)


def _div_net(s: str) -> int:
    return len(_DIV_OPEN_RE.findall(s)) - len(_DIV_CLOSE_RE.findall(s))


def _split_wrapper(region_full: str, net_open: int) -> Tuple[Optional[str], Optional[str]]:
    """Peel the closing document wrapper (the `net_open` `</div>` plus any
    `</body></html>`) off the tail of the sections region.

    `net_open` is how many `<div>` the preamble left open (PRD: `.frame` +
    `.page` = 2). The sections themselves are internally balanced, so the LAST
    `net_open` closing `</div>` in the trailing close-tag run are the wrapper.
    Returns (sections_region, suffix) or (None, None) if the tail can't be split
    cleanly (→ fallback).
    """
    if net_open < 0:
        return None, None
    m = re.search(r"((?:\s|</div>|</body>|</html>)+)$", region_full, re.IGNORECASE)
    run = m.group(1) if m else ""
    run_start = len(region_full) - len(run)
    if net_open == 0:
        # No wrapper divs to peel; suffix is only trailing body/html/whitespace.
        tail = re.search(r"((?:\s|</body>|</html>)*)$", region_full, re.IGNORECASE)
        suffix = tail.group(1) if tail else ""
        region = region_full[: len(region_full) - len(suffix)]
        return (region, suffix) if _div_net(region) == 0 else (None, None)
    div_pos = [mm.start() for mm in re.finditer(r"</div>", run, re.IGNORECASE)]
    if len(div_pos) < net_open:
        return None, None
    cut = div_pos[len(div_pos) - net_open]
    suffix = run[cut:]
    region = region_full[:run_start] + run[:cut]
    if _div_net(region) != 0:
        return None, None
    return region, suffix


def _tokenize(doc: str, model: SectionModel):
    """(preamble, [(name, block)…], suffix) or None if the doc isn't parseable
    as a delimiter-sectioned house-format document (→ fallback).

    Each `block` is the delimiter plus its body up to (not including) the next
    delimiter (primary OR secondary). Preamble is everything before the first
    delimiter; suffix is the closing document wrapper.
    """
    all_marks = model.iter_delimiters(doc)
    if not all_marks:
        return None
    first = all_marks[0][0]
    preamble = doc[:first]
    region_full = doc[first:]
    net_open = _div_net(preamble)
    region, suffix = _split_wrapper(region_full, net_open)
    if region is None:
        return None
    marks = model.iter_delimiters(region)
    if not marks:
        return None
    sections: List[Tuple[str, str]] = []
    for i, (start, name) in enumerate(marks):
        end = marks[i + 1][0] if i + 1 < len(marks) else len(region)
        sections.append((name, region[start:end]))
    return preamble, sections, suffix


# ── Block-level addressing: the section algebra, recursed exactly one level ──
#
# A "block" is a top-level element INSIDE a section (each `<p>`, `<table>`,
# `<ul>`, `<div>` …), 0-based in document order, not counting the section's own
# delimiter. A block op addresses a contiguous RANGE of them and carries a short
# echo of the target's visible text.
#
# Why an ordinal AND an echo, when each looks sufficient alone: an ordinal is
# guaranteed unique but UNVERIFIED — an off-by-one splices silently onto the
# wrong block, a corruption class the section-only design does not have. A text
# anchor is verified but not guaranteed unique. Together they are both, for
# ~12 output tokens per op.
#
# The depth cap is two levels — blocks, and the `<tr>`/`<li>` children of a
# table/list block. That covers "add a requirement" and "add a risk" (which
# today cost a whole table or list) while keeping the gate matrix reviewable;
# arbitrary path addressing is unbounded and un-gateable.

_BLOCK_OP_KINDS = ("replace_blocks", "insert_blocks_after", "delete_blocks")

# Containers whose children are addressable at the second (and last) level.
_CHILD_TAG = {"table": "tr", "ul": "li", "ol": "li"}

# How much of the target block's visible text the model must echo back. Short
# enough that copying it is easy (a whole string invites paraphrase, and every
# false mismatch costs a second full call); long enough that it actually
# discriminates between neighbouring blocks.
_ANCHOR_PREFIX = 24

# The floor on how much must actually arrive. It is deliberately BELOW
# `_ANCHOR_PREFIX`: a model copying "the first 24 characters" can land its cut
# inside a whitespace run, and normalizing then collapses it to 23 — measured on
# the real Requirements table, where the honest anchor for row 4 arrives 23 chars
# long. Rejecting that would manufacture a fallback (two full calls) for a
# CORRECT anchor, which is the one way this work can make the product worse.
#
# This is a floor on length, not on strictness: the discriminating check is
# still `startswith` over the full 24-char prefix, so a WRONG anchor of any
# length still fails. The floor exists only to stop a degenerate one-character
# echo making the gate decorative.
_ANCHOR_MIN = 16


class _BlockSplitter(HTMLParser):
    """Split a region into its TOP-LEVEL elements by depth counting (stdlib).

    `ok` goes False on anything that is not a clean sequence of COMPLETE
    elements: a stray close tag, non-whitespace text between elements, a comment
    or declaration at depth 0, or an element still open at the end. That
    strictness IS gate 0b — refusing to address blocks in a region we cannot
    decompose losslessly is what keeps the untouched-bytes-are-byte-identical
    property (the whole safety story) true rather than hopeful.
    """

    def __init__(self, data: str) -> None:
        super().__init__(convert_charrefs=True)
        self.data = data
        self._line_starts = [0]
        for i, ch in enumerate(data):
            if ch == "\n":
                self._line_starts.append(i + 1)
        self.depth = 0
        # each span: [tag, start, content_start, content_end, end]
        self.spans: List[list] = []
        self.cur: Optional[list] = None
        self.ok = True

    def _off(self) -> int:
        line, col = self.getpos()
        if line - 1 >= len(self._line_starts):
            self.ok = False
            return 0
        return self._line_starts[line - 1] + col

    def _complete(self, tag: str) -> None:
        """A void / self-closing element: a whole element in one token."""
        off = self._off()
        raw = self.get_starttag_text() or ""
        end = off + len(raw)
        if self.depth == 0:
            self.spans.append([tag, off, end, end, end])

    def handle_starttag(self, tag, attrs):
        if not self.ok:
            return
        if tag in _BalanceParser._VOID:
            self._complete(tag)
            return
        off = self._off()
        raw = self.get_starttag_text() or ""
        if self.depth == 0:
            self.cur = [tag, off, off + len(raw), None, None]
        self.depth += 1

    def handle_startendtag(self, tag, attrs):
        # Overridden so the default start-then-end dispatch does not double-count.
        if not self.ok:
            return
        self._complete(tag)

    def handle_endtag(self, tag):
        if not self.ok:
            return
        if tag in _BalanceParser._VOID:
            return
        if self.depth == 0:
            self.ok = False  # stray close tag at top level
            return
        self.depth -= 1
        if self.depth != 0:
            return
        if self.cur is None or self.cur[0] != tag:
            self.ok = False
            return
        off = self._off()
        gt = self.data.find(">", off)
        if gt == -1:
            self.ok = False
            return
        self.cur[3] = off
        self.cur[4] = gt + 1
        self.spans.append(self.cur)
        self.cur = None

    def handle_data(self, data):
        if self.ok and self.depth == 0 and data.strip():
            self.ok = False

    def handle_comment(self, data):
        if self.depth == 0:
            self.ok = False

    def handle_decl(self, decl):
        if self.depth == 0:
            self.ok = False

    def handle_pi(self, data):
        if self.depth == 0:
            self.ok = False

    def unknown_decl(self, data):
        if self.depth == 0:
            self.ok = False


def _top_level_spans(region: str):
    """(leading_whitespace, [span…]) or None if `region` is not a clean sequence
    of complete top-level elements separated only by whitespace."""
    p = _BlockSplitter(region)
    try:
        p.feed(region)
        p.close()
    except Exception:  # noqa: BLE001 — any parser blow-up = not decomposable
        return None
    if not p.ok or p.depth != 0 or p.cur is not None:
        return None
    if not p.spans:
        return (region, []) if not region.strip() else None
    lead = region[: p.spans[0][1]]
    if lead.strip():
        return None
    for i, sp in enumerate(p.spans):
        nxt = p.spans[i + 1][1] if i + 1 < len(p.spans) else len(region)
        if region[sp[4]:nxt].strip():
            return None
    return lead, p.spans


def _split_blocks(region: str):
    """(leading_whitespace, [(tag, raw_text_including_trailing_whitespace)…]).

    `lead + "".join(texts) == region` exactly — the byte-identity round-trip the
    whole design depends on. None if `region` is not decomposable.
    """
    r = _top_level_spans(region)
    if r is None:
        return None
    lead, spans = r
    items: List[Tuple[str, str]] = []
    for i, sp in enumerate(spans):
        end = spans[i + 1][1] if i + 1 < len(spans) else len(region)
        items.append((sp[0], region[sp[1]:end]))
    return lead, items


def _decompose_section(section_core: str, model: SectionModel):
    """(head, [(tag, text)…]) for ONE section block, where `head` is its own
    delimiter plus any whitespace before the first block, or None (gate 0b).

    `head + "".join(texts) == section_core`. `section_core` must already be
    rstripped — the section's own trailing whitespace is the caller's to hold,
    so a splice that touches the last block cannot eat the blank line that
    separates this section from the next.
    """
    dlen: Optional[int] = None
    m = model.delimiter_re.match(section_core)
    if m:
        dlen = m.end()
    else:
        for sd in model.secondary:
            mm = sd.pattern.match(section_core)
            if mm:
                dlen = mm.end()
                break
    if dlen is None:
        return None
    sub = _split_blocks(section_core[dlen:])
    if sub is None:
        return None
    lead, items = sub
    if not items:
        return None
    return section_core[:dlen] + lead, items


def _child_items(parent_text: str, parent_tag: str):
    """(head, [(tag, text)…], tail) for the addressable children of a container
    block — the `<tr>` of a table's single `<tbody>`, or the `<li>` of a
    `<ul>`/`<ol>` — or None. `head + "".join(texts) + tail == parent_text`.

    A `<thead>` row is deliberately NOT addressable: "add a requirement" adds a
    body row, and a header change (adding a column) genuinely dirties every row,
    so it must degrade to a coarser op rather than be expressed here. A table
    with anything other than exactly one `<tbody>` is refused → fallback.
    """
    want = _CHILD_TAG.get(parent_tag)
    if want is None:
        return None
    core = parent_text.rstrip()
    tail_ws = parent_text[len(core):]
    r = _top_level_spans(core)
    if r is None:
        return None
    _, spans = r
    if len(spans) != 1 or spans[0][0] != parent_tag:
        return None
    inner_start, inner_end = spans[0][2], spans[0][3]
    if inner_start is None or inner_end is None:
        return None
    if parent_tag == "table":
        ri = _top_level_spans(core[inner_start:inner_end])
        if ri is None:
            return None
        bodies = [sp for sp in ri[1] if sp[0] == "tbody"]
        if len(bodies) != 1:
            return None
        b = bodies[0]
        inner_start, inner_end = inner_start + b[2], inner_start + b[3]
    sub = _split_blocks(core[inner_start:inner_end])
    if sub is None:
        return None
    sublead, items = sub
    if not items or any(t != want for t, _ in items):
        return None
    return core[:inner_start] + sublead, items, core[inner_end:] + tail_ws


class _TextParser(HTMLParser):
    """Visible text only — what the model sees rendered, and therefore what it
    can echo back accurately."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.chunks: List[str] = []

    def handle_data(self, data):
        self.chunks.append(data)


def _visible_text(html: str) -> str:
    p = _TextParser()
    try:
        p.feed(html)
        p.close()
    except Exception:  # noqa: BLE001
        return ""
    # Join chunks with a space so adjacent cells/items read the way they render
    # (`<td>R2</td><td>Tone approval</td>` -> "R2 Tone approval", not
    # "R2Tone approval"), then collapse. A mid-word inline tag is the one shape
    # this over-separates; that costs a false mismatch, i.e. a fallback, which
    # is the safe direction.
    return re.sub(r"\s+", " ", " ".join(p.chunks)).strip()


def _norm_anchor(s: str) -> str:
    return re.sub(r"\s+", " ", s or "").strip().casefold()


def _check_anchor(anchor, target_html: str, what: str) -> None:
    """Gate 2b(iii): the echoed prefix must match the block actually sitting at
    the named ordinal. This is the entire defence against a silent off-by-one,
    so it is deliberately not lenient about being ABSENT or trivially short —
    a one-character echo would make the gate decorative.

    Comparison is whitespace-normalized, casefolded, prefix-only. Failure raises
    (never logs the anchor or the block text — the message carries lengths only).
    """
    if not isinstance(anchor, str):
        raise FallbackNeeded(f"gate2b: anchor_text is missing for {what}")
    a = _norm_anchor(anchor)
    t = _norm_anchor(_visible_text(target_html))
    need = min(_ANCHOR_MIN, len(t))
    if len(a) < need:
        raise FallbackNeeded(
            f"gate2b: anchor_text for {what} is too short "
            f"({len(a)} chars, need {need})"
        )
    if not t.startswith(a[:_ANCHOR_PREFIX]):
        raise FallbackNeeded(
            f"gate2b: anchor_text for {what} does not match the block at that "
            f"ordinal (off-by-one?)"
        )


def _parse_ordinal(value, what: str) -> tuple:
    """A 0-based block address: an int (`2`), or a QUOTED dotted string for the
    one permitted second level (`"2.3"`).

    A bare JSON float (`2.3` unquoted) is refused rather than guessed at: `2.10`
    and `2.1` are the same float, so interpreting one would be a silent
    off-by-nine. Refusing costs a fallback; guessing could cost a document.
    """
    if isinstance(value, bool) or value is None:
        raise FallbackNeeded(f"gate1b: {what} ordinal {value!r} is not an integer")
    if isinstance(value, int):
        return (value,)
    if isinstance(value, str):
        parts = value.strip().split(".")
        if len(parts) > 2:
            raise FallbackNeeded(
                f"gate1b: {what} ordinal {value!r} exceeds the two-level depth cap"
            )
        try:
            return tuple(int(x) for x in parts)
        except ValueError:
            raise FallbackNeeded(
                f"gate1b: {what} ordinal {value!r} is not an integer"
            ) from None
    raise FallbackNeeded(f"gate1b: {what} ordinal {value!r} is not an integer")


def _payload_blocks(payload, what: str) -> List[Tuple[str, str]]:
    """Gate 2b(i): `new_html` must be a whole number of COMPLETE block elements —
    no partial element (a token-wall truncation), no loose text either side."""
    if not isinstance(payload, str) or not payload.strip():
        raise FallbackNeeded(
            f"gate2b: {what} has an empty new_html; expected complete block elements"
        )
    sub = _split_blocks(payload.strip())
    if sub is None or not sub[1]:
        raise FallbackNeeded(
            f"gate2b: {what} new_html is not a whole number of complete block elements"
        )
    return sub[1]


def _normalize_block_op(op: dict, label: str) -> dict:
    """Parse one block op into `{kind, depth, parent, lo, hi, anchor, payload}`.

    `lo`/`hi` are the ordinals AT the addressed level; `parent` is the top-level
    block index when the address is two levels deep.
    """
    verb = op.get("op")
    if verb == "insert_blocks_after":
        lo_addr = _parse_ordinal(op.get("after"), f"{label} insert_blocks_after.after")
        hi_addr = lo_addr
        kind = "insert"
    else:
        raw_to = op.get("to")
        if raw_to is None:
            raw_to = op.get("from")
        lo_addr = _parse_ordinal(op.get("from"), f"{label} {verb}.from")
        hi_addr = _parse_ordinal(raw_to, f"{label} {verb}.to")
        kind = "delete" if verb == "delete_blocks" else "replace"
    if len(lo_addr) != len(hi_addr):
        raise FallbackNeeded(f"gate1b: {label} range mixes address depths")
    depth = len(lo_addr)
    if depth == 2 and lo_addr[0] != hi_addr[0]:
        raise FallbackNeeded(f"gate1b: {label} range spans two parent blocks")
    return {
        "kind": kind,
        "depth": depth,
        "parent": lo_addr[0] if depth == 2 else None,
        "lo": lo_addr[-1],
        "hi": hi_addr[-1],
        "anchor": op.get("anchor_text"),
        "payload": op.get("new_html"),
    }


def _splice_items(items, nops, label: str, want_tag: Optional[str] = None):
    """Apply block ops to one ordered item list and return
    `[(text, origin_index_or_None)…]`.

    Every ordinal resolves against the PRE-EDIT list — insertions are collected
    and applied after, exactly as `insertions` already works at section level —
    so ops in one response cannot shift each other's addresses.

    Runs gates 1b (range validity, no overlap) and 2b (shape, tag agreement,
    echo) as it goes.
    """
    n = len(items)
    trailing = [t[len(t.rstrip()):] for _, t in items]
    covered: set = set()
    repl: dict = {}      # lo -> (hi, payload or None)
    inserts: dict = {}   # after_index -> payload

    for o in nops:
        kind, lo, hi = o["kind"], o["lo"], o["hi"]

        # Gate 1b: the address is in range and not inverted.
        if kind == "insert":
            if not (-1 <= lo < n):
                raise FallbackNeeded(
                    f"gate1b: {label} insert anchor {lo} out of range -1..{n - 1}"
                )
        else:
            if lo > hi:
                raise FallbackNeeded(
                    f"gate1b: {label} range {lo}..{hi} is inverted (from > to), "
                    f"out of range"
                )
            if not (0 <= lo and hi < n):
                raise FallbackNeeded(
                    f"gate1b: {label} range {lo}..{hi} out of range 0..{n - 1}"
                )

        # Gate 2b(iii): the echo verifies the ordinal. `after: -1` is the one
        # deterministic position (before everything) and needs no anchor.
        if not (kind == "insert" and lo < 0):
            _check_anchor(o["anchor"], items[lo][1], f"{label} block {lo}")

        if kind == "delete":
            rng = set(range(lo, hi + 1))
            if rng & covered:
                raise FallbackNeeded(
                    f"gate1b: overlapping {label} ranges at {lo}..{hi}"
                )
            covered |= rng
            repl[lo] = (hi, None)
            continue

        # Gate 2b(i): the payload is complete elements.
        blocks = _payload_blocks(o["payload"], f"{label} block {lo}")
        if want_tag and any(t != want_tag for t, _ in blocks):
            raise FallbackNeeded(
                f"gate2b: {label} payload tag must be <{want_tag}> at this depth"
            )
        if kind == "replace":
            # Gate 2b(ii): tag agreement catches an off-by-one that crosses a
            # tag boundary (a <p> addressed where a <table> sits).
            if blocks[0][0] != items[lo][0]:
                raise FallbackNeeded(
                    f"gate2b: {label} payload tag <{blocks[0][0]}> != target tag "
                    f"<{items[lo][0]}> at block {lo}"
                )
            rng = set(range(lo, hi + 1))
            if rng & covered:
                raise FallbackNeeded(
                    f"gate1b: overlapping {label} ranges at {lo}..{hi}"
                )
            covered |= rng
            repl[lo] = (hi, o["payload"])
        else:
            if lo in inserts:
                raise FallbackNeeded(
                    f"gate1b: two {label} inserts after block {lo}"
                )
            inserts[lo] = o["payload"]

    for a in inserts:
        if a in covered:
            raise FallbackNeeded(
                f"gate1b: {label} insert anchors on a replaced/deleted block {a}"
            )

    out: List[Tuple[str, Optional[int]]] = []
    if -1 in inserts:
        out.append((inserts[-1].rstrip() + (trailing[0] if n else ""), None))
    i = 0
    while i < n:
        if i in repl:
            hi, payload = repl[i]
            if payload is not None:
                # Re-append the ORIGINAL boundary whitespace, exactly as the
                # section-level splice does, so an identical replace is
                # byte-identical rather than merely equivalent.
                out.append((payload.rstrip() + trailing[hi], None))
            i = hi + 1
        else:
            out.append((items[i][1], i))
            if i in inserts:
                out.append((inserts[i].rstrip() + trailing[i], None))
            i += 1
    return out


def _reconcile(new_region: str, decompose, before_items, out, what: str):
    """Gate 4b: re-tokenize what we just built and reconcile it against what we
    MEANT to build — the count must match, and every block we did not address
    must be byte-identical.

    Byte-identity is free here and is the strongest available statement of "only
    what was addressed changed". Construction alone does not prove it: a payload
    could concatenate with its neighbour into a different element sequence than
    the one we counted, and this is what would catch that.
    """
    dec = decompose(new_region)
    if dec is None:
        raise FallbackNeeded(f"gate4b: spliced {what} no longer decomposes")
    new_items = dec
    if len(new_items) != len(out):
        raise FallbackNeeded(
            f"gate4b: {what} count {len(new_items)} != expected {len(out)} after splice"
        )
    for (txt, origin), (_, actual) in zip(out, new_items):
        if actual.rstrip() != txt.rstrip():
            raise FallbackNeeded(
                f"gate4b: spliced {what} does not match what was constructed"
            )
        if origin is not None and actual.rstrip() != before_items[origin][1].rstrip():
            raise FallbackNeeded(
                f"gate4b: untouched {what} {origin} is not byte-identical after the splice"
            )


def _verify_block_splice(new_core, head, before_items, out, model) -> None:
    """Gate 4b at the block level, plus the delimiter-head freeze."""
    dec = _decompose_section(new_core, model)
    if dec is None:
        raise FallbackNeeded("gate4b: spliced section no longer decomposes into blocks")
    if dec[0] != head:
        raise FallbackNeeded("gate4b: section delimiter changed during a block splice")
    _reconcile(new_core, lambda _r: dec[1], before_items, out, "block")


def _verify_child_splice(new_parent, ptag, khead, ktail, before_items, out) -> None:
    """Gate 4b one level down, plus the container head/tail freeze."""
    kid = _child_items(new_parent, ptag)
    if kid is None:
        raise FallbackNeeded("gate4b: spliced container no longer decomposes into children")
    if kid[0] != khead or kid[2] != ktail:
        raise FallbackNeeded("gate4b: container head/tail changed during a child splice")
    _reconcile(new_parent, lambda _r: kid[1], before_items, out, "child")


def _apply_block_ops(section_block: str, bops: list, model: SectionModel, label: str) -> str:
    """Splice every block op for ONE section and return its new block text.

    Ordering: child (level-2) ops resolve and apply first, against the pre-edit
    parent; then top-level ops, against the pre-edit block list. A section may
    not carry a top-level op and a child op on the SAME parent block — that is
    the same 1:1 discipline gate 1 enforces at section level, one level down.
    """
    core = section_block.rstrip()
    sect_trail = section_block[len(core):]
    dec = _decompose_section(core, model)
    if dec is None:
        raise FallbackNeeded(
            f"gate0b: section {label!r} body is not a clean sequence of top-level blocks"
        )
    head, items = dec

    normalized = [_normalize_block_op(op, label) for op in bops]
    top_ops = [o for o in normalized if o["depth"] == 1]
    child_ops: dict = {}
    for o in normalized:
        if o["depth"] == 2:
            child_ops.setdefault(o["parent"], []).append(o)

    # Gate 1b: a top-level replace/delete and a child op must not claim the same
    # parent block. (A top-level INSERT after a block does not conflict with
    # editing that block's children, so it is deliberately not included here.)
    top_claimed: set = set()
    for o in top_ops:
        if o["kind"] in ("replace", "delete"):
            top_claimed |= set(range(o["lo"], o["hi"] + 1))

    for parent, cops in child_ops.items():
        if not (0 <= parent < len(items)):
            raise FallbackNeeded(
                f"gate1b: {label} parent block {parent} out of range 0..{len(items) - 1}"
            )
        if parent in top_claimed:
            raise FallbackNeeded(
                f"gate1b: {label} block {parent} has both a block-level and a "
                f"child-level op"
            )
        ptag = items[parent][0]
        kid = _child_items(items[parent][1], ptag)
        if kid is None:
            raise FallbackNeeded(
                f"gate0b: {label} block {parent} (<{ptag}>) has no addressable children"
            )
        khead, kitems, ktail = kid
        kout = _splice_items(
            kitems, cops, f"{label} block {parent} child", want_tag=_CHILD_TAG[ptag]
        )
        new_parent = khead + "".join(t for t, _ in kout) + ktail
        _verify_child_splice(new_parent, ptag, khead, ktail, kitems, kout)
        items[parent] = (ptag, new_parent)

    out = _splice_items(items, top_ops, label)
    new_core = head + "".join(t for t, _ in out)
    _verify_block_splice(new_core, head, items, out, model)
    new_block = new_core + sect_trail

    # Gate 6b: the document-level 50% floor cannot fire for an op that touches
    # one block, so it would go slack. Restore it as a per-section band.
    if not any(o["kind"] == "delete" for o in normalized):
        if len(new_block) < 0.5 * len(section_block):
            raise FallbackNeeded(
                f"gate6b: section {label!r} collapsed to "
                f"{len(new_block)}/{len(section_block)} bytes"
            )
    return new_block


# ── The splice engine + the validation gates ────────────────────────────────

def apply_targeted_edit(
    stored_doc: str, ops: list, model: SectionModel
) -> str:
    """Splice the targeted ops into `stored_doc`, validate against every gate, and
    return the new full document. Raises `FallbackNeeded` on ANY gate failure so
    the caller re-runs the proven full-emit path.

    Section-level gates (all deterministic, all before any write):
      0. document parses as a sectioned house document
      1. anchor resolves 1:1        2. payload matches its target
      3. result is well-formed      4. section-set invariant (no silent drop)
      5. preamble/wrapper frozen    6. size-collapse guard

    Block-level analogues, when an op addresses blocks inside a section. None of
    them relaxes an assertion above; 4b and 5b assert things nothing did before:
      0b. the section body decomposes into complete top-level elements
      1b. ordinals in range, no overlapping ranges, no section-op collision
      2b. payload is complete elements, its tag agrees with the target's, and
          the echoed `anchor_text` verifies the ordinal (the off-by-one defence)
      3.  unchanged — and more load-bearing, since payloads are smaller
      4b. block count reconciles AND every untouched block is byte-identical
      5b. a block payload may not contain a section delimiter
      6b. a section touched only by non-delete block ops keeps a size band,
          restoring gate 6, which a one-block edit could never trip
    """
    if not isinstance(ops, list) or not ops:
        raise FallbackNeeded("no ops in targeted response")

    tok = _tokenize(stored_doc, model)
    if tok is None:
        raise FallbackNeeded("gate0: document not parseable as sectioned house format")
    preamble, sections, suffix = tok

    before_names = [n for n, _ in sections]
    # Direct name index (normalize only, NO alias): keeps distinct sections
    # distinct even where an alias would collapse them — e.g. a doc carrying BOTH
    # a "User input needed" eyebrow AND an appendix. This is what makes the
    # section-set invariant (gate 4) reliable rather than falsely collapsing.
    direct_index: dict = {}
    for i, n in enumerate(before_names):
        direct_index.setdefault(model.normalize(n), []).append(i)
    alias_map = model._alias_map()

    def resolve_op(name: str) -> str:
        """Doc-aware resolution: prefer a LITERAL section-name match; fall to a
        secondary alias only when no literal section claims the name AND the alias
        target actually exists in THIS document. So "User input needed" resolves
        to the eyebrow section when the doc has one (v4.8 layout) and to the
        appendix only when it does not (v4.7 layout) — no collision either way."""
        n = model.normalize(name)
        if n in direct_index:
            return n
        canon = alias_map.get(n)
        if canon and canon in direct_index:
            return canon
        return n

    # Trailing whitespace of each original block. Re-appended to a replaced/
    # inserted block so the splice-boundary whitespace is byte-identical to a full
    # re-emit (which keeps the blank line between sections); a naive splice drops
    # it whenever the model omits the trailing newline from its `new_html`.
    trailing = [b[len(b.rstrip()):] for _, b in sections]

    blocks: List[Optional[str]] = [b for _, b in sections]
    deleted_norm: set = set()
    inserted_norm: set = set()
    # insertions collected as (anchor_index, new_block, new_norm) applied after.
    insertions: List[Tuple[int, str, str]] = []

    # ── Block-level ops (L1), applied BEFORE the section-level loop ──────────
    # Each one rewrites the INSIDE of one section block; the section-level loop
    # below then runs unchanged over the result. A section carrying block ops
    # may not also carry a section-level replace/delete — same 1:1 discipline as
    # gate 1, one level down.
    block_ops_by_section: dict = {}
    section_op_targets: set = set()
    for op in ops:
        if not isinstance(op, dict):
            raise FallbackNeeded("gate1: op is not an object")
        kind = op.get("op")
        if kind in _BLOCK_OP_KINDS:
            if not blocks_enabled():
                # Flag OFF is a true kill switch: a block verb is simply an
                # unknown op kind, exactly as it is today.
                raise FallbackNeeded(f"gate1: unknown op kind {kind!r}")
            block_ops_by_section.setdefault(
                resolve_op(op.get("section") or ""), []
            ).append(op)
        elif kind in ("replace", "delete"):
            section_op_targets.add(resolve_op(op.get("section") or ""))

    if block_ops_by_section:
        # Gate 5b: a block payload may not carry a section delimiter. Checked up
        # front, across every block op, so it fails with this reason rather than
        # as a downstream tag mismatch or a gate-4 section drop.
        for bops in block_ops_by_section.values():
            for op in bops:
                nh = op.get("new_html") or ""
                if model.delimiter_re.search(nh) or any(
                    sd.pattern.search(nh) for sd in model.secondary
                ):
                    raise FallbackNeeded(
                        "gate5b: block new_html contains a section delimiter"
                    )

    for nsec, bops in block_ops_by_section.items():
        label = (bops[0].get("section") or "")
        if nsec in section_op_targets:
            raise FallbackNeeded(
                f"gate1b: section {label!r} has both a section-level and a "
                f"block-level op"
            )
        matches = direct_index.get(nsec, [])
        if len(matches) != 1:
            raise FallbackNeeded(
                f"gate1b: section {label!r} resolved to {len(matches)} delimiters"
            )
        idx = matches[0]
        current = blocks[idx]
        if current is None:
            raise FallbackNeeded(f"gate1b: section {label!r} is not present")
        blocks[idx] = _apply_block_ops(current, bops, model, label)
        # Keep the section-level boundary-whitespace bookkeeping consistent: the
        # block splice preserved the section's own trailing whitespace, so the
        # cached `trailing` entry is still correct by construction.

    for op in ops:
        if not isinstance(op, dict):
            raise FallbackNeeded("gate1: op is not an object")
        kind = op.get("op")
        if kind in _BLOCK_OP_KINDS:
            continue  # already applied above
        section = op.get("section") or ""
        new_html = (op.get("new_html") or "")

        if kind in ("replace", "delete"):
            nsec = resolve_op(section)
            # Gate 1: anchor resolves 1:1
            matches = direct_index.get(nsec, [])
            if len(matches) != 1:
                raise FallbackNeeded(
                    f"gate1: section {section!r} resolved to {len(matches)} delimiters"
                )
            idx = matches[0]
            if kind == "delete":
                blocks[idx] = None
                deleted_norm.add(nsec)
            else:  # replace
                # Gate 2: payload begins with a delimiter whose name == section
                _gate2_payload_matches(new_html, section, model, resolve_op)
                blocks[idx] = new_html.rstrip() + trailing[idx]

        elif kind == "insert_after":
            after = op.get("after") or ""
            nafter = resolve_op(after)
            matches = direct_index.get(nafter, [])
            if len(matches) != 1:
                raise FallbackNeeded(
                    f"gate1: insert_after anchor {after!r} resolved to "
                    f"{len(matches)} delimiters"
                )
            _gate2_payload_matches(new_html, section, model, resolve_op)
            new_norm = model.normalize(section)
            new_block = new_html.rstrip() + trailing[matches[0]]
            insertions.append((matches[0], new_block, new_norm))
            inserted_norm.add(new_norm)
        else:
            raise FallbackNeeded(f"gate1: unknown op kind {kind!r}")

    # Reassemble: kept/replaced blocks in order, with insertions after anchors.
    out_blocks: List[str] = []
    after_norms: List[str] = []
    for i, block in enumerate(blocks):
        if block is not None:
            out_blocks.append(block)
            after_norms.append(model.normalize(before_names[i]))
        for anchor_idx, new_block, new_norm in insertions:
            if anchor_idx == i:
                out_blocks.append(new_block)
                after_norms.append(new_norm)

    result = preamble + "".join(out_blocks) + suffix

    # Gate 5: preamble + wrapper frozen. By construction we reuse them verbatim,
    # so verify the model didn't smuggle wrapper/preamble bytes into a payload.
    for op in ops:
        nh = op.get("new_html") or ""
        if "</body>" in nh.lower() or "</html>" in nh.lower():
            raise FallbackNeeded("gate5: new_html contains document wrapper close")
    if not result.startswith(preamble) or not result.endswith(suffix):
        raise FallbackNeeded("gate5: preamble/suffix not preserved after splice")

    # Gate 4: section-set invariant. after_set == before_set - deleted + inserted.
    # Keyed by DIRECT normalize so two alias-sharing sections stay distinct.
    before_set = set(model.normalize(n) for n in before_names)
    expected = (before_set - deleted_norm) | inserted_norm
    actual = set(after_norms)
    if actual != expected:
        raise FallbackNeeded(
            f"gate4: section-set changed unexpectedly "
            f"(missing={expected - actual}, extra={actual - expected})"
        )

    # Gate 3: well-formed / not truncated.
    if not _is_well_formed(result):
        raise FallbackNeeded("gate3: reassembled document is not well-formed")

    # Gate 6: size-collapse guard. With no deletes the result should be in a sane
    # band of the original; a splice that ate the doc down to one section trips.
    if not deleted_norm and len(result) < 0.5 * len(stored_doc):
        raise FallbackNeeded(
            f"gate6: result collapsed to {len(result)}/{len(stored_doc)} bytes"
        )

    return result


def _gate2_payload_matches(new_html: str, section: str, model, resolve_op) -> None:
    """Gate 2: `new_html` must begin with a delimiter (primary or secondary) whose
    doc-resolved name equals the op's doc-resolved `section`. Catches the model
    pasting the wrong section's content under the right name (or truncating the
    leading delimiter). Uses the same doc-aware `resolve_op` as gate 1 so the two
    checks agree on which section a name refers to in THIS document."""
    lead = model.delimiter_name_at_start(new_html.lstrip())
    if lead is None:
        raise FallbackNeeded(
            f"gate2: new_html for {section!r} does not start with a delimiter"
        )
    if resolve_op(lead) != resolve_op(section):
        raise FallbackNeeded(
            f"gate2: new_html leading delimiter {lead!r} != section {section!r}"
        )


# ── Response interpretation (shared by callers) ──────────────────────────────

# Keys the contract defines. Anything else the model returns is logged BY NAME
# (never by value) so a rejection is diagnosable instead of a mystery.
_CONTRACT_KEYS = ("mode", "ops", "full_html", "sections_changed", "summary")


def _response_shape(out) -> str:
    """A one-line description of a response's SHAPE — key names and value sizes,
    never values. No document content, no PII.

    This exists because a `FallbackNeeded` today discards everything the model
    produced, unexamined: the only live occurrence of the `no ops` rejection
    burned 1,831 output tokens and left no record of where they went, so its root
    cause is still unknown after two sightings. Shape is enough to tell a refusal
    from a mis-moded `full_html` from content under an unread key.
    """
    if not isinstance(out, dict):
        return f"<not a dict: {type(out).__name__}>"

    def _len(key) -> int:
        v = out.get(key)
        return len(v) if isinstance(v, (str, list)) else 0

    mode = out.get("mode")
    parts = [
        f"mode={mode if isinstance(mode, str) else repr(mode)}",
        f"ops={_len('ops')}",
        f"full_html={_len('full_html')}",
        f"sections_changed={_len('sections_changed')}",
        f"summary={_len('summary')}",
        f"unknown_keys={sorted(k for k in out if k not in _CONTRACT_KEYS)}",
    ]
    ops = out.get("ops")
    if isinstance(ops, list):
        kinds = sorted(
            {o.get("op") for o in ops
             if isinstance(o, dict) and isinstance(o.get("op"), str)}
        )
        if kinds:
            parts.append(f"op_kinds={kinds}")
    return " ".join(parts)


def _take_full(out: dict, strip_fence: Callable[[str], str]) -> Tuple[str, list]:
    """The `mode:"full"` lane: strip the fence, verify well-formedness, and carry
    the model's own `sections_changed` through."""
    html = strip_fence((out.get("full_html") or "").strip())
    if not html:
        raise FallbackNeeded("mode:full returned empty full_html")
    # Lightweight well-formedness check on the full-rewrite output. Today's
    # (flag-off) full-emit writes whatever the model returns with no such check,
    # so this is STRICTLY safer, not a behavior regression: a truncated full_html
    # (token wall) is caught and re-run through the proven full-emit path
    # (fail-to-slow), instead of persisting a broken document. A false-positive
    # only ever costs one extra call (same latency as today's single call), never
    # a corruption — the same fail-to-slow contract the six splice gates use.
    if not _is_well_formed(html):
        raise FallbackNeeded("mode:full full_html is not well-formed")
    # `sections_changed` is the model's own list for full mode (there are no ops
    # to derive it from) — this keeps the chat's "Updated: X, Y" confirmation
    # populated, matching today's behavior. Fall back to any ops sections if the
    # field is absent.
    secs = [
        s for s in (out.get("sections_changed") or []) if isinstance(s, str) and s
    ]
    if not secs:
        secs = [
            op.get("section")
            for op in (out.get("ops") or [])
            if isinstance(op, dict) and op.get("section")
        ]
    return html, secs


def interpret(
    out: dict,
    *,
    stored_doc: str,
    model: SectionModel,
    strip_fence: Callable[[str], str],
) -> Tuple[str, list]:
    """Turn a targeted-schema LLM response into `(full_html, sections_changed)`.

    Raises `FallbackNeeded` on anything that can't be trusted, so the caller runs
    the full-emit path — logging the response's SHAPE (never its content) on the
    way out, so a rejection is diagnosable.
    """
    try:
        return _interpret(
            out, stored_doc=stored_doc, model=model, strip_fence=strip_fence
        )
    except FallbackNeeded as exc:
        logger.warning(
            "targeted edit rejected (%s) shape: %s", exc, _response_shape(out)
        )
        raise


def _interpret(
    out: dict,
    *,
    stored_doc: str,
    model: SectionModel,
    strip_fence: Callable[[str], str],
) -> Tuple[str, list]:
    """Handles `mode:"none"` (no write), `mode:"full"` (take `full_html`) and
    `mode:"targeted"` (splice + validate via `apply_targeted_edit`)."""
    mode = out.get("mode")
    if mode == "none":
        # L0. The instruction asked for no document change. Return the stored
        # document with an EMPTY sections_changed, which the existing no-write
        # contract already reads as "skip the snapshot and skip the write". One
        # small call instead of a full rewrite, and the document is untouched.
        return stored_doc, []
    if mode == "full":
        # L3.
        return _take_full(out, strip_fence)
    if mode == "targeted":
        ops = out.get("ops") or []
        if not (isinstance(ops, list) and ops) and (out.get("full_html") or "").strip():
            # L3b: SALVAGE. The model returned no usable ops but did return a
            # whole document. Today that combination is thrown away and a second
            # full-document call redoes work already paid for — the measured
            # 161 s worst case was exactly this double payment. Route it through
            # the SAME `mode:"full"` lane, including its well-formedness check,
            # so this adds no new trust: either the document passes the checks a
            # full re-emit would have to pass anyway, or we fall back as before.
            return _take_full(out, strip_fence)
        # L1/L2. An empty ops array with no `full_html` stays a rejection: since
        # `mode:"none"` now gives the model a correct way to say "no change",
        # an empty ops array is a genuine malformation, not a no-op request.
        html = apply_targeted_edit(stored_doc, ops, model)
        secs = [
            op.get("section")
            for op in ops
            if isinstance(op, dict) and op.get("section")
        ]
        # Dedupe while preserving order: block ops make several ops per section
        # common, and the chat's "Updated: X, Y" line should not repeat a name.
        seen: set = set()
        deduped = []
        for name in secs:
            if name not in seen:
                seen.add(name)
                deduped.append(name)
        return html, deduped
    raise FallbackNeeded(f"unknown mode {mode!r}")
