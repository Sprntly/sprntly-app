import type { GoalFinding } from "./api"

/** The findings-section heading's own clip bound. MIRRORS
 *  `backend/app/crucible/report.py`'s `MAX_STATEMENT_CHARS` exactly — not
 *  imported, because there is nothing to import from a Python module, but
 *  the two have to move together or the panel and the exported document
 *  disagree about where a claim gets cut. */
const MAX_STATEMENT_CHARS = 400

/** `text`, bounded, cut on a word boundary. MIRRORS `report.py`'s own
 *  `_clip` — same normalize-whitespace-then-cut-at-the-last-space rule, so a
 *  heading built here never disagrees with the same statement clipped in
 *  the document. */
function clip(text: string, limit: number): string {
  const t = (text || "").split(/\s+/).filter(Boolean).join(" ")
  if (t.length <= limit) return t
  const cut = t.slice(0, limit)
  const lastSpace = cut.lastIndexOf(" ")
  return (lastSpace === -1 ? cut : cut.slice(0, lastSpace)) + "…"
}

/** The findings-section heading: a CLAIM, not a label.
 *
 *  MIRRORS `report.py`'s `_findings_heading` EXACTLY: the same "print the
 *  top-ranked finding's own statement, cut before its example quote, and
 *  name how many more sit under it" rule, so the live panel and the
 *  exported document never show two different headings for the same run.
 *
 *  `findings` arrives already rank-ordered (the panel never re-sorts it —
 *  see this file's own `GoalAnalysisReport` docstring), so `findings[0]` is
 *  the strongest claim in the set without this function ranking anything
 *  itself.
 *
 *  CUT BEFORE THE FINDING'S OWN QUOTE, not after it — `findings[0]`'s
 *  `statement` embeds "— for example, "…"" exactly when it has no `label`,
 *  and that same quote already renders in the finding's own card below (or
 *  in its blockquote). Reusing the whole sentence here would put the
 *  identical quoted words in two headings back to back, about the same
 *  theme.
 *
 *  Returns the plain "What the evidence says (N)" heading when there are no
 *  findings or the top one has no statement, so a caller can render this
 *  unconditionally without an empty-heading branch of its own. */
export function findingsHeading(findings: GoalFinding[]): string {
  const count = findings.length
  const statement = (findings[0]?.statement || "").trim()
  if (!statement) return `What the evidence says (${count})`
  const core = statement.split("— for example,", 1)[0].trim()
  const claim = clip(core || statement, MAX_STATEMENT_CHARS)
  if (count > 1) {
    return `${claim} — the strongest of ${count.toLocaleString()} findings below`
  }
  return claim
}
