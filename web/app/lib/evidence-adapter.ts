/**
 * Convert evidence Markdown into a PrdState with semantic-block sections
 * (`v2-hero`, `v2-context-chip`, `v2-cuts-index`, `v2-source`,
 * `v2-rules-callout`, `v2-quote`, `v2-forecast-omitted`).
 *
 * The `v2-*` prefix on the section types is historical — it dates back to
 * the original v1/v2 sample-build split. After v2 was promoted to be the
 * only evidence format the prefix was kept (the churn of renaming across
 * the renderer + types wasn't worth it). These are the canonical evidence
 * blocks; no v1 exists.
 *
 * Lenient on JSON-inside-blocks: if a `:::hero` body fails to parse, the
 * block becomes a plain paragraph with the raw body so the doc doesn't
 * disappear. Same fallback for every JSON-bodied block.
 */
import type {
  EvidenceV2Confidence,
  EvidenceV2HeroCard,
  EvidenceV2Tone,
  PrdChartDatum,
  PrdChartKind,
  PrdSection,
  PrdContent,
} from "../types/content"

const HEADING_RULE = /^─+$/
const CHART_KINDS: PrdChartKind[] = ["bar", "line", "pie", "donut", "stat", "gauge"]
const TONES: EvidenceV2Tone[] = ["negative", "neutral", "positive"]
const CONFIDENCES: EvidenceV2Confidence[] = ["High", "Medium", "Low"]

/* ---------- small helpers (mirrored from prd-adapter) ---------- */

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

function tryParseJson(body: string): unknown | null {
  const trimmed = body.trim()
  try {
    return JSON.parse(trimmed)
  } catch {
    // Salvage: extract the first {...} or [...] from the body.
    const startObj = trimmed.indexOf("{")
    const startArr = trimmed.indexOf("[")
    const starts = [startObj, startArr].filter((i) => i >= 0)
    if (starts.length === 0) return null
    const start = Math.min(...starts)
    const opener = trimmed[start]
    const closer = opener === "{" ? "}" : "]"
    const end = trimmed.lastIndexOf(closer)
    if (end > start) {
      try {
        return JSON.parse(trimmed.slice(start, end + 1))
      } catch {
        return null
      }
    }
    return null
  }
}

/* ---------- chart parsing (shape-compatible with prd-adapter) ---------- */

function buildChartSection(value: unknown): PrdSection | null {
  if (!value || typeof value !== "object") return null
  const obj = value as Record<string, unknown>
  const kindRaw = String(obj.kind || "").toLowerCase()
  if (!CHART_KINDS.includes(kindRaw as PrdChartKind)) return null
  const dataRaw = Array.isArray(obj.data) ? (obj.data as unknown[]) : []
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
    kind: kindRaw as PrdChartKind,
    title: typeof obj.title === "string" ? obj.title : undefined,
    subtitle: typeof obj.subtitle === "string" ? obj.subtitle : undefined,
    data,
  }
}

/* ---------- semantic block parsers (one per :::name) ---------- */

function parseHeroBlock(body: string): PrdSection | null {
  const parsed = tryParseJson(body)
  if (!Array.isArray(parsed)) return null
  const cards: EvidenceV2HeroCard[] = []
  for (const raw of parsed) {
    if (!raw || typeof raw !== "object") continue
    const r = raw as Record<string, unknown>
    const label = typeof r.label === "string" ? r.label : ""
    const value = r.value == null ? "" : String(r.value)
    if (!label || !value) continue
    const toneRaw = typeof r.tone === "string" ? r.tone.toLowerCase() : "neutral"
    const tone: EvidenceV2Tone = (TONES as string[]).includes(toneRaw)
      ? (toneRaw as EvidenceV2Tone)
      : "neutral"
    cards.push({
      label,
      value,
      delta: typeof r.delta === "string" ? r.delta : undefined,
      baseline: typeof r.baseline === "string" ? r.baseline : undefined,
      tone,
    })
  }
  if (cards.length === 0) return null
  return { type: "v2-hero", cards }
}

function parseCutsIndexBlock(body: string): PrdSection | null {
  const parsed = tryParseJson(body)
  if (!Array.isArray(parsed)) return null
  const rows = parsed
    .map((raw) => {
      if (!raw || typeof raw !== "object") return null
      const r = raw as Record<string, unknown>
      const n =
        typeof r.n === "number" ? r.n : parseInt(String(r.n ?? ""), 10) || 0
      const headline = typeof r.headline === "string" ? r.headline : ""
      const confRaw = typeof r.confidence === "string" ? r.confidence : "Medium"
      const confidence: EvidenceV2Confidence = (CONFIDENCES as string[]).includes(
        confRaw,
      )
        ? (confRaw as EvidenceV2Confidence)
        : "Medium"
      if (!headline) return null
      return { n, headline, confidence }
    })
    .filter((r): r is { n: number; headline: string; confidence: EvidenceV2Confidence } => r !== null)
  if (rows.length === 0) return null
  return { type: "v2-cuts-index", rows }
}

function parseSourceBlock(body: string): PrdSection | null {
  const parsed = tryParseJson(body)
  if (!Array.isArray(parsed)) return null
  const chips = parsed
    .map((raw) => {
      if (!raw || typeof raw !== "object") return null
      const r = raw as Record<string, unknown>
      const kind = typeof r.kind === "string" ? r.kind : "tool"
      const label = typeof r.label === "string" ? r.label : ""
      if (!label) return null
      return { kind, label }
    })
    .filter((c): c is { kind: string; label: string } => c !== null)
  if (chips.length === 0) return null
  return { type: "v2-source", chips }
}

function parseQuoteBlock(body: string): PrdSection | null {
  const parsed = tryParseJson(body)
  if (!parsed || typeof parsed !== "object" || Array.isArray(parsed)) return null
  const obj = parsed as Record<string, unknown>
  const text = typeof obj.body === "string" ? obj.body : ""
  const channel = typeof obj.channel === "string" ? obj.channel : ""
  if (!text || !channel) return null
  return {
    type: "v2-quote",
    body: text,
    channel,
    context: typeof obj.context === "string" ? obj.context : undefined,
  }
}

function parseRulesCalloutBlock(body: string): PrdSection | null {
  // Body is two `**Supports:** ...` and `**Rules out:** ...` lines (or just
  // `Supports:` / `Rules out:` with no bold). Be forgiving.
  const lines = body
    .split("\n")
    .map((l) => l.trim())
    .filter(Boolean)
  let supports = ""
  let rulesOut = ""
  for (const l of lines) {
    const s = l.replace(/^\*\*Supports:\*\*\s*/i, "")
    if (s !== l) {
      supports = s.trim()
      continue
    }
    const r = l.replace(/^\*\*Rules out:\*\*\s*/i, "")
    if (r !== l) {
      rulesOut = r.trim()
      continue
    }
    // Plain-prefix fallback
    if (/^supports:\s*/i.test(l) && !supports) {
      supports = l.replace(/^supports:\s*/i, "").trim()
    } else if (/^rules out:\s*/i.test(l) && !rulesOut) {
      rulesOut = l.replace(/^rules out:\s*/i, "").trim()
    }
  }
  if (!supports && !rulesOut) return null
  return { type: "v2-rules-callout", supports, rulesOut }
}

/* ---------- block matchers ---------- */

const BLOCK_OPEN_RE = /^:::([a-z][a-z0-9-]*)(\s+.*)?$/
const BLOCK_CLOSE_RE = /^:::$/

interface BlockHeader {
  name: string
  attrs: string
}

function parseBlockHeader(line: string): BlockHeader | null {
  const m = line.match(BLOCK_OPEN_RE)
  if (!m) return null
  return { name: m[1], attrs: (m[2] || "").trim() }
}

function parseAttr(attrs: string, key: string): string | null {
  // matches `key="value"` (double-quoted)
  const re = new RegExp(`${key}="([^"]*)"`)
  const m = attrs.match(re)
  return m ? m[1] : null
}

function fallbackParagraphFromBlock(
  name: string,
  body: string,
): PrdSection {
  // Last-resort fallback so a malformed block doesn't vanish from the doc.
  const compact = body.trim().replace(/\s+/g, " ").slice(0, 240)
  return {
    type: "p",
    text: `[${name} block — could not parse: ${compact}…]`,
  }
}

function parseSemanticBlock(
  name: string,
  attrs: string,
  body: string,
): PrdSection[] {
  switch (name) {
    case "hero":
      return [parseHeroBlock(body) ?? fallbackParagraphFromBlock(name, body)]
    case "context-chip":
      return [{ type: "v2-context-chip", text: body.trim() }]
    case "cuts-index":
      return [parseCutsIndexBlock(body) ?? fallbackParagraphFromBlock(name, body)]
    case "source":
      return [parseSourceBlock(body) ?? fallbackParagraphFromBlock(name, body)]
    case "callout": {
      const type = parseAttr(attrs, "type") || ""
      if (type === "rules") {
        return [
          parseRulesCalloutBlock(body) ?? fallbackParagraphFromBlock(name, body),
        ]
      }
      // Unknown callout type — degrade to paragraph.
      return [{ type: "p", text: body.trim() }]
    }
    case "quote":
      return [parseQuoteBlock(body) ?? fallbackParagraphFromBlock(name, body)]
    case "forecast": {
      const reason = parseAttr(attrs, "omitted")
      if (reason != null) {
        return [{ type: "v2-forecast-omitted", reason }]
      }
      // Non-omitted forecast block doesn't normally appear (the template uses
      // a regular markdown section with a chart); but if it does, render body
      // as a paragraph so it isn't lost.
      return [{ type: "p", text: body.trim() }]
    }
    default:
      return [fallbackParagraphFromBlock(name, body)]
  }
}

/* ---------- main entry ---------- */

export function markdownToEvidenceState(markdown: string): PrdContent {
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

    // Semantic block (:::name [...])
    const header = parseBlockHeader(line)
    if (header) {
      flushBullets()
      // Single-line block with attrs and no body (e.g. `:::forecast omitted="..."`).
      // We treat it as a self-closing block only when no body lines follow before
      // the next `:::`. We look ahead to either the closing `:::` or another block.
      const bodyLines: string[] = []
      let j = i + 1
      let closed = false
      while (j < lines.length) {
        const next = lines[j].trim()
        if (BLOCK_CLOSE_RE.test(next)) {
          closed = true
          break
        }
        bodyLines.push(lines[j])
        j++
      }
      if (closed) {
        for (const s of parseSemanticBlock(
          header.name,
          header.attrs,
          bodyLines.join("\n"),
        )) {
          sections.push(s)
        }
        i = j // skip past the closing :::
      } else {
        // No closing fence — treat the opener as a self-closing single-line
        // block (works for :::forecast omitted="..." and similar).
        for (const s of parseSemanticBlock(header.name, header.attrs, "")) {
          sections.push(s)
        }
      }
      continue
    }

    // Fenced ```chart``` block.
    if (line.startsWith("```")) {
      flushBullets()
      const bodyLines: string[] = []
      let j = i + 1
      while (j < lines.length && !lines[j].trim().startsWith("```")) {
        bodyLines.push(lines[j])
        j++
      }
      const chart = buildChartSection(tryParseJson(bodyLines.join("\n")))
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

    if (isTableRow(line)) {
      flushBullets()
      const tableRows: string[][] = [splitRow(line)]
      let j = i + 1
      while (j < lines.length && isTableRow(lines[j])) {
        tableRows.push(splitRow(lines[j]))
        j++
      }
      const cleaned = tableRows.filter((r) => !isSeparatorRow(r))
      if (cleaned.length >= 2) {
        const [headers, ...rows] = cleaned
        sections.push({ type: "table", headers, rows })
        i = j - 1
        continue
      }
    }

    flushBullets()
    sections.push({ type: "p", text: line })
  }
  flushBullets()

  return {
    metaLine: `Generated ${new Date().toLocaleDateString()}`,
    title: title || "Evidence",
    sections,
  }
}
