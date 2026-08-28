"""Targeted-edit output contract for document edits (PRD first; goal-report next).

Behind the `TARGETED_EDIT_ENABLED` flag (default OFF). When off, every caller
keeps its current full-document re-emit path byte-for-byte. When on, the edit
LLM call is asked for ONLY the changed sections as splice ops instead of the
whole re-emitted document, and this module splices them back into the stored
document deterministically — validating the result against six gates before any
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
      "mode": "targeted" | "full",
      "ops": [{"op": "replace"|"delete"|"insert_after",
               "section": <delimiter text>,
               "after": <delimiter text>,        # insert_after only
               "new_html": <the section block incl. its own delimiter>}],
      "full_html": <full document>,              # mode == "full" only
      "summary": <one line>
    }

`mode:"full"` is the model's own escape hatch for edits that cannot be expressed
as section replacements (reorder, "make it shorter", restructure) — the server
takes `full_html` through the existing write path unchanged.

This module is deliberately dependency-light (stdlib only) so the splice engine
and all six gates are unit-testable without the app/LLM/DB stack, and so it adds
no new dependency. The per-document differences live in a small `SectionModel`.
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
        "mode": {"type": "string", "enum": ["targeted", "full"]},
        "ops": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "op": {
                        "type": "string",
                        "enum": ["replace", "delete", "insert_after"],
                    },
                    "section": {"type": "string"},
                    "after": {"type": "string"},
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
        "Return ONLY the structured object."
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


# ── The splice engine + six validation gates ─────────────────────────────────

def apply_targeted_edit(
    stored_doc: str, ops: list, model: SectionModel
) -> str:
    """Splice the targeted ops into `stored_doc`, validate against six gates, and
    return the new full document. Raises `FallbackNeeded` on ANY gate failure so
    the caller re-runs the proven full-emit path.

    Gates (all deterministic, all before any write):
      1. anchor resolves 1:1        2. payload matches its target
      3. result is well-formed      4. section-set invariant (no silent drop)
      5. preamble/wrapper frozen    6. size-collapse guard
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

    for op in ops:
        if not isinstance(op, dict):
            raise FallbackNeeded("gate1: op is not an object")
        kind = op.get("op")
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

def interpret(
    out: dict,
    *,
    stored_doc: str,
    model: SectionModel,
    strip_fence: Callable[[str], str],
) -> Tuple[str, list]:
    """Turn a targeted-schema LLM response into `(full_html, sections_changed)`.

    Raises `FallbackNeeded` on anything that can't be trusted, so the caller runs
    the full-emit path. Handles both `mode:"full"` (take `full_html`) and
    `mode:"targeted"` (splice + validate via `apply_targeted_edit`).
    """
    mode = out.get("mode")
    if mode == "full":
        html = strip_fence((out.get("full_html") or "").strip())
        if not html:
            raise FallbackNeeded("mode:full returned empty full_html")
        # Lightweight well-formedness check on the full-rewrite output. Today's
        # (flag-off) full-emit writes whatever the model returns with no such
        # check, so this is STRICTLY safer, not a behavior regression: a truncated
        # full_html (token wall) is caught and re-run through the proven full-emit
        # path (fail-to-slow), instead of persisting a broken document. A
        # false-positive only ever costs one extra call (same latency as today's
        # single call), never a corruption — the same fail-to-slow contract the
        # six splice gates use.
        if not _is_well_formed(html):
            raise FallbackNeeded("mode:full full_html is not well-formed")
        # `sections_changed` is the model's own list for full mode (there are no
        # ops to derive it from) — this keeps the chat's "Updated: X, Y"
        # confirmation populated, matching today's behavior. Fall back to any ops
        # sections if the field is absent.
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
    if mode == "targeted":
        ops = out.get("ops") or []
        html = apply_targeted_edit(stored_doc, ops, model)
        secs = [
            op.get("section")
            for op in ops
            if isinstance(op, dict) and op.get("section")
        ]
        return html, secs
    raise FallbackNeeded(f"unknown mode {mode!r}")
