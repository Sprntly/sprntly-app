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
const TABLE_SEP = /^\|?\s*:?-+:?\s*(\|\s*:?-+:?\s*)+\|?\s*$/
const CHART_KINDS: PrdChartKind[] = ["bar", "line", "pie", "stat"]

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

function isAllSeparatorRow(cells: string[]): boolean {
  return cells.every((c) => /^:?-+:?$/.test(c.replace(/\s/g, "")))
}

function parseChartBlock(body: string): PrdSection | null {
  try {
    const raw = JSON.parse(body)
    if (!raw || typeof raw !== "object") return null
    const kind = String(raw.kind || "").toLowerCase()
    if (!CHART_KINDS.includes(kind as PrdChartKind)) return null
    const dataRaw = Array.isArray(raw.data) ? raw.data : []
    const data: PrdChartDatum[] = dataRaw
      .map((d: unknown) => {
        if (!d || typeof d !== "object") return null
        const obj = d as Record<string, unknown>
        const label = obj.label == null ? "" : String(obj.label)
        const valueRaw = obj.value
        if (valueRaw == null) return null
        const value: number | string =
          typeof valueRaw === "number" ? valueRaw : String(valueRaw)
        return { label, value }
      })
      .filter((d: PrdChartDatum | null): d is PrdChartDatum => d !== null)
    if (data.length === 0) return null
    return {
      type: "chart",
      kind: kind as PrdChartKind,
      title: typeof raw.title === "string" ? raw.title : undefined,
      subtitle: typeof raw.subtitle === "string" ? raw.subtitle : undefined,
      data,
    }
  } catch {
    return null
  }
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

    // Fenced code block — chart blocks become structured chart sections,
    // anything else is dropped (LLM occasionally emits stray fences).
    if (line.startsWith("```")) {
      flushBullets()
      const lang = line.slice(3).trim().toLowerCase()
      const bodyLines: string[] = []
      let j = i + 1
      while (j < lines.length && !lines[j].trim().startsWith("```")) {
        bodyLines.push(lines[j])
        j++
      }
      if (lang === "chart") {
        const block = parseChartBlock(bodyLines.join("\n"))
        if (block) sections.push(block)
      }
      i = j // skip closing fence (j stops at it)
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

    // Markdown table: header row, then a --- separator, then body rows.
    if (
      isTableRow(line) &&
      i + 1 < lines.length &&
      TABLE_SEP.test(lines[i + 1].trim())
    ) {
      flushBullets()
      const headers = splitRow(line)
      const rows: string[][] = []
      let j = i + 2
      while (j < lines.length && isTableRow(lines[j])) {
        const cells = splitRow(lines[j])
        if (!isAllSeparatorRow(cells)) rows.push(cells)
        j++
      }
      sections.push({ type: "table", headers, rows })
      i = j - 1
      continue
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
