/**
 * Which columns of a markdown table are LABELS and which are PROSE.
 *
 * The auto table-layout algorithm sizes a column by how much text it holds,
 * and when a table mixes a short-value column with a paragraph-length one it
 * resolves that by wrapping the cheap column rather than the expensive one. A
 * "Dimension / What It Is" table therefore renders `Inputs & Data Sources`
 * broken over two lines next to prose running the full width of the message —
 * not broken exactly (globals.css already stops the one-letter collapse), but
 * squeezed for no reason, since giving that column its whole label back costs
 * the prose column almost nothing.
 *
 * The browser cannot know that a column is a label, so we tell it: classify
 * each column from its own content, then let the caller mark the label columns
 * so CSS can size them to their content instead of wrapping them.
 *
 * DELIBERATELY CHARACTER-COUNTING, NOT MEASURING. The accurate way is to
 * measure real max-content widths in the rendered font after mount, but
 * answers STREAM — `AskReplyBody` renders a partial table on every token — and
 * a measurement pass would re-run and re-assign widths continuously, so the
 * columns would visibly jitter for the whole time the table is being written.
 * A character count on a partial table is a stable guess that only improves as
 * rows arrive, and it costs no layout.
 *
 * Everything here is pure so the heuristic can be tested without a DOM.
 */

export type ColumnKind = "label" | "prose"

/** At or under this many characters, a column's widest cell is a label. */
const LABEL_MAX_CHARS = 24

/**
 * A table is only worth intervening in when some column is genuinely
 * paragraph-length. Below this the columns are all comparable and the default
 * layout already balances them — marking one would just add dead space.
 */
const PROSE_MIN_CHARS = 40

/**
 * Past this many columns the table is heading for a horizontal scroll anyway
 * (globals.css makes it its own scroller), and pinning several columns to
 * their content makes that worse rather than better.
 */
const MAX_COLUMNS = 8

/** A hast-ish node, kept structural so this module needs no react-markdown types. */
type HastNode = {
  type?: string
  tagName?: string
  value?: string
  children?: HastNode[]
}

const CELL_TAGS = new Set(["td", "th"])
const ROW_TAG = "tr"

/** All text under a node, flattened — cells carry inline markup (bold, links). */
function textOf(node: HastNode | undefined): string {
  if (!node) return ""
  if (node.type === "text") return node.value || ""
  return (node.children || []).map(textOf).join("")
}

/** Every `tr` under a table node, whatever row group wraps it. */
function rowsOf(node: HastNode | undefined): HastNode[] {
  if (!node) return []
  if (node.tagName === ROW_TAG) return [node]
  return (node.children || []).flatMap(rowsOf)
}

/**
 * The table's cell text as a grid — header row included, because a column is
 * sized by the widest of (header, content) and a short column under a long
 * header must not be classified on its values alone.
 */
export function tableRows(node: unknown): string[][] {
  const rows = rowsOf(node as HastNode)
  return rows.map((row) =>
    (row.children || [])
      .filter((c) => c.tagName && CELL_TAGS.has(c.tagName))
      .map((cell) => textOf(cell).trim()),
  )
}

/**
 * Classify each column. Returns [] when the table should be left alone —
 * no rows, a single column, too many columns, or no column long enough to be
 * crowding the others.
 */
export function columnKinds(rows: string[][]): ColumnKind[] {
  const width = rows.reduce((n, r) => Math.max(n, r.length), 0)
  if (width < 2 || width > MAX_COLUMNS) return []

  const longest: number[] = new Array(width).fill(0)
  for (const row of rows) {
    for (let i = 0; i < width; i++) {
      longest[i] = Math.max(longest[i], (row[i] || "").length)
    }
  }

  const kinds: ColumnKind[] = longest.map((n) =>
    n <= LABEL_MAX_CHARS ? "label" : "prose",
  )

  // Only act on a genuine imbalance: at least one label column to pin AND at
  // least one column long enough to absorb the width we hand back. A table of
  // uniformly short columns already renders at its natural width.
  const hasLabel = kinds.some((k) => k === "label")
  const hasProse = longest.some((n) => n >= PROSE_MIN_CHARS)
  if (!hasLabel || !hasProse) return []

  // Every column being a label is caught by hasProse above, but a table whose
  // ONLY long column is also its only column would slip through the width
  // check — guard the degenerate case explicitly.
  if (kinds.every((k) => k === "label")) return []

  return kinds
}

/**
 * The classes that mark label columns for CSS. Positional rather than a
 * colgroup: `<col>` accepts width but not `white-space`, and the label column
 * needs BOTH — width alone still lets the browser wrap the label mid-phrase.
 * See the `md-label-col-*` rules in globals.css.
 */
export function labelColumnClasses(kinds: ColumnKind[]): string {
  return kinds
    .map((kind, i) => (kind === "label" ? `md-label-col-${i + 1}` : ""))
    .filter(Boolean)
    .join(" ")
}
