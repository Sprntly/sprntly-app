/**
 * Convert the Markdown PRD returned by /v1/prd/generate into the
 * structured PrdState shape that PrdScreen renders.
 */
import type {
  PrdChartDatum,
  PrdChartKind,
  PrdSection,
  PrdState,
} from "../types/content"

const HEADING_RULE = /^─+$/
const CHART_KINDS: PrdChartKind[] = ["bar", "line", "pie", "donut", "stat"]

function splitRow(line: string): string[] {
  let s = line.trim()
  if (s.startsWith("|")) s = s.slice(1)
  if (s.endsWith("|")) s = s.slice(0, -1)
  return s.split("|").map((c) => c.trim())
}

function isTableRow(line: string): boolean {
  const t = line.trim()
  return t.startsWith("|") && t.indexOf("|", 1) > 0
}

function isSeparatorRow(cells: string[]): boolean {
  return (
    cells.length > 0 &&
    cells.every((c) => /^:?-+:?$/.test(c.replace(/\s/g, "")))
  )
}

function looksLikeChartSpec(value: unknown): boolean {
  if (!value || typeof value !== "object") return false
  const obj = value as Record<string, unknown>
  const kind = String(obj.kind || "").toLowerCase()
  return CHART_KINDS.includes(kind as PrdChartKind) && Array.isArray(obj.data)
}

function buildChartSection(value: unknown): PrdSection | null {
  if (!looksLikeChartSpec(value)) return null
  const obj = value as Record<string, unknown>
  const kind = String(obj.kind).toLowerCase() as PrdChartKind
  const dataRaw = (obj.data as unknown[]) || []
  const data: PrdChartDatum[] = dataRaw
    .map((d) => {
      if (!d || typeof d !== "object") return null
      const item = d as Record<string, unknown>
      const label = item.label == null ? "" : String(item.label)
      const valueRaw = item.value
      if (valueRaw == null) return null
      const value: number | string =
        typeof valueRaw === "number" ? valueRaw : String(valueRaw)
      return { label, value }
    })
    .filter((d: PrdChartDatum | null): d is PrdChartDatum => d !== null)
  if (data.length === 0) return null
  return {
    type: "chart",
    kind,
    title: typeof obj.title === "string" ? obj.title : undefined,
    subtitle: typeof obj.subtitle === "string" ? obj.subtitle : undefined,
    data,
  }
}

function parseChartBody(body: string): PrdSection | null {
  const trimmed = body.trim()
  // Try plain JSON first.
  try {
    return buildChartSection(JSON.parse(trimmed))
  } catch {
    // Fall through to the salvage path.
  }
  // Salvage: extract the first {...} JSON object from the body.
  const start = trimmed.indexOf("{")
  const end = trimmed.lastIndexOf("}")
  if (start >= 0 && end > start) {
    const inner = trimmed.slice(start, end + 1)
    try {
      return buildChartSection(JSON.parse(inner))
    } catch {
      // give up
    }
  }
  return null
}

export function markdownToPrdState(markdown: string): PrdState {
  const lines = markdown.replace(/\r\n/g, "\n").split("\n")
  let title = ""
  const sections: PrdSection[] = []
  let currentBullets: string[] | null = null
  const flushBullets = () => {
    if (currentBullets && currentBullets.length > 0) {
      sections.push({ type: "ul", items: currentBullets })
    }
    currentBullets = null
  }

  for (let i = 0; i < lines.length; i++) {
    const raw = lines[i]
    const line = raw.trim()
    if (!line) {
      flushBullets()
      continue
    }
    if (HEADING_RULE.test(line)) {
      flushBullets()
      continue
    }

    // Fenced code block. If the body parses as a chart spec, render a chart.
    // Otherwise drop it (keeps the PRD body clean of stray code blocks).
    if (line.startsWith("```")) {
      flushBullets()
      const bodyLines: string[] = []
      let j = i + 1
      while (j < lines.length && !lines[j].trim().startsWith("```")) {
        bodyLines.push(lines[j])
        j++
      }
      const chart = parseChartBody(bodyLines.join("\n"))
      if (chart) sections.push(chart)
      i = j // skip closing fence
      continue
    }

    if (line.startsWith("# ")) {
      flushBullets()
      const t = line.slice(2).trim()
      if (!title) title = t
      else sections.push({ type: "h2", text: t })
      continue
    }
    if (line.startsWith("## ")) {
      flushBullets()
      sections.push({ type: "h2", text: line.slice(3).trim() })
      continue
    }
    if (line.startsWith("### ") || line.startsWith("#### ")) {
      flushBullets()
      sections.push({ type: "h2", text: line.replace(/^#+\s*/, "").trim() })
      continue
    }
    if (line.startsWith("- ") || line.startsWith("* ")) {
      currentBullets ??= []
      currentBullets.push(line.slice(2).trim())
      continue
    }
    if (/^\d+\.\s/.test(line)) {
      currentBullets ??= []
      currentBullets.push(line.replace(/^\d+\.\s/, "").trim())
      continue
    }

    // Markdown table. Forgiving: any block of 2+ consecutive `| ... |` rows
    // becomes a table. The first row is header; any all-dashes row is treated
    // as a separator and dropped.
    if (isTableRow(line)) {
      flushBullets()
      const tableRows: string[][] = [splitRow(line)]
      let j = i + 1
      while (j < lines.length && isTableRow(lines[j])) {
        tableRows.push(splitRow(lines[j]))
        j++
      }
      // Drop separator rows like ['---','---'].
      const cleaned = tableRows.filter((r) => !isSeparatorRow(r))
      if (cleaned.length >= 2) {
        const [headers, ...rows] = cleaned
        sections.push({ type: "table", headers, rows })
        i = j - 1
        continue
      }
      // Single row that isn't really a table — fall through to paragraph.
    }

    flushBullets()
    sections.push({ type: "p", text: line })
  }
  flushBullets()

  return {
    metaLine: `Generated ${new Date().toLocaleDateString()}`,
    title: title || "PRD",
    sections,
  }
}
