"use client"

/**
 * The rows behind a chart, as a table.
 *
 * Two jobs, one component:
 *  1. **Disclosure** — every chart gets a collapsed "View data" affordance
 *     underneath it. Provenance is the product's trust story; a picture whose
 *     numbers you cannot read is a claim, not evidence.
 *  2. **Degrade path** — when a Vega spec fails to compile or render, the same
 *     table renders *expanded, in place of the chart*, so a malformed spec in a
 *     stored PRD costs the reader a picture, never the page.
 */

export type ChartTableRow = Record<string, unknown>

/** Header order = key order of the first row, then any keys later rows add. */
export function chartTableColumns(rows: ChartTableRow[]): string[] {
  const seen: string[] = []
  for (const row of rows) {
    if (!row || typeof row !== "object") continue
    for (const key of Object.keys(row)) {
      if (!seen.includes(key)) seen.push(key)
    }
  }
  return seen
}

function cellText(v: unknown): string {
  if (v == null) return ""
  if (typeof v === "string") return v
  if (typeof v === "number" || typeof v === "boolean") return String(v)
  try {
    return JSON.stringify(v)
  } catch {
    return String(v)
  }
}

export function ChartDataTable({
  rows,
  /** `disclosure` = collapsed <details> under a chart. `static` = always-open
   *  table standing in for a chart that failed to render. */
  variant = "disclosure",
  label = "View data",
  note,
}: {
  rows: ChartTableRow[]
  variant?: "disclosure" | "static"
  label?: string
  note?: string
}) {
  const usable = (rows || []).filter(
    (r): r is ChartTableRow => !!r && typeof r === "object" && !Array.isArray(r),
  )
  if (usable.length === 0) return null
  const cols = chartTableColumns(usable)
  if (cols.length === 0) return null

  const table = (
    <div className="chart-data-table-wrap">
      <table className="chart-data-table" data-testid="chart-data-table">
        <thead>
          <tr>
            {cols.map((c) => (
              <th key={c}>{c}</th>
            ))}
          </tr>
        </thead>
        <tbody>
          {usable.map((row, i) => (
            <tr key={i}>
              {cols.map((c) => (
                <td key={c}>{cellText(row[c])}</td>
              ))}
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  )

  if (variant === "static") {
    return (
      <div className="chart-data-fallback" data-testid="chart-data-fallback">
        {note ? <div className="chart-data-fallback-note">{note}</div> : null}
        {table}
      </div>
    )
  }

  return (
    <details className="chart-data-disclosure" data-testid="chart-data-disclosure">
      <summary className="chart-data-summary">{label}</summary>
      {table}
    </details>
  )
}
