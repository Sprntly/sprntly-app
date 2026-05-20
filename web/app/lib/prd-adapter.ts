/**
 * Convert the PRD Markdown into a PrdState with typed semantic-block
 * sections (`prd-tldr`, `prd-problem`, `prd-hypothesis`,
 * `prd-requirements`, `prd-acceptance-criteria`, `prd-metrics`,
 * `prd-risks`, `prd-milestones`, `prd-dod`), plus the shared
 * `:::context-chip` block (rendered with the same `v2-context-chip`
 * variant the evidence renderer uses).
 *
 * Mirrors `evidence-adapter` block-for-block: same `:::name` open/close
 * regex, same lenient JSON parsing with salvage fallback, same
 * paragraph-fallback for malformed bodies so a bad block never deletes
 * itself from the rendered doc. Helpers are duplicated rather than
 * imported so each adapter stays self-contained.
 */
import type {
  EvidenceV2Tone,
  PrdAcceptanceCriterionRow,
  PrdChartDatum,
  PrdChartKind,
  PrdGuardrail,
  PrdMetricPoint,
  PrdMilestonePhase,
  PrdProblemImpactCell,
  PrdRequirementRow,
  PrdRiskRow,
  PrdSection,
  PrdState,
} from "../types/content"

const HEADING_RULE = /^─+$/
const CHART_KINDS: PrdChartKind[] = ["bar", "line", "pie", "donut", "stat", "gauge"]
const TONES: EvidenceV2Tone[] = ["negative", "neutral", "positive"]
const SEVERITIES = ["high", "medium", "low"] as const
const REQ_CATEGORIES = ["functional", "flag", "config", "telemetry"] as const

/* ---------- small helpers (mirrored from evidence-adapter) ---------- */

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

function toStr(v: unknown, fallback = ""): string {
  if (v == null) return fallback
  if (typeof v === "string") return v
  return String(v)
}

function normalizeSeverity(raw: unknown): string {
  const s = typeof raw === "string" ? raw.toLowerCase() : ""
  return (SEVERITIES as readonly string[]).includes(s) ? s : (s || "low")
}

function normalizeCategory(raw: unknown): string {
  const s = typeof raw === "string" ? raw.toLowerCase() : ""
  return (REQ_CATEGORIES as readonly string[]).includes(s)
    ? s
    : s || "functional"
}

function normalizeTone(raw: unknown): EvidenceV2Tone {
  const t = typeof raw === "string" ? raw.toLowerCase() : ""
  return (TONES as string[]).includes(t) ? (t as EvidenceV2Tone) : "neutral"
}

/* ---------- chart parsing ---------- */

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

function parseMetricPoint(raw: unknown): PrdMetricPoint | null {
  if (!raw || typeof raw !== "object") return null
  const r = raw as Record<string, unknown>
  const name = toStr(r.name)
  if (!name) return null
  return {
    name,
    current: toStr(r.current),
    target: toStr(r.target),
  }
}

function parseTldrBlock(body: string): PrdSection | null {
  const parsed = tryParseJson(body)
  if (!parsed || typeof parsed !== "object" || Array.isArray(parsed)) return null
  const r = parsed as Record<string, unknown>
  const problem = toStr(r.problem)
  const fix = toStr(r.fix)
  const impact = toStr(r.impact)
  if (!problem && !fix && !impact) return null
  return { type: "prd-tldr", problem, fix, impact }
}

function parseProblemBlock(body: string): PrdSection | null {
  const parsed = tryParseJson(body)
  if (!parsed || typeof parsed !== "object" || Array.isArray(parsed)) return null
  const r = parsed as Record<string, unknown>
  const userStory = toStr(r.user_story)
  const impactRaw = Array.isArray(r.impact) ? (r.impact as unknown[]) : []
  const impact: PrdProblemImpactCell[] = impactRaw
    .map((cell) => {
      if (!cell || typeof cell !== "object") return null
      const c = cell as Record<string, unknown>
      const label = toStr(c.label)
      const value = toStr(c.value)
      if (!label && !value) return null
      const out: PrdProblemImpactCell = { label, value }
      if (typeof c.tone === "string") {
        out.tone = normalizeTone(c.tone)
      }
      return out
    })
    .filter((c): c is PrdProblemImpactCell => c !== null)
  if (!userStory && impact.length === 0) return null
  return { type: "prd-problem", userStory, impact }
}

function parseHypothesisBlock(body: string): PrdSection | null {
  const parsed = tryParseJson(body)
  if (!parsed || typeof parsed !== "object" || Array.isArray(parsed)) return null
  const r = parsed as Record<string, unknown>
  const ifWe = toStr(r.if_we)
  const because = toStr(r.because)
  const thenMetric = parseMetricPoint(r.then_metric)
  if (!ifWe && !because && !thenMetric) return null
  return {
    type: "prd-hypothesis",
    ifWe,
    thenMetric: thenMetric ?? { name: "", current: "", target: "" },
    because,
    secondary:
      typeof r.secondary === "string" && r.secondary
        ? r.secondary
        : undefined,
  }
}

function parseRequirementsBlock(body: string): PrdSection | null {
  const parsed = tryParseJson(body)
  if (!Array.isArray(parsed)) return null
  const rows: PrdRequirementRow[] = parsed
    .map((raw) => {
      if (!raw || typeof raw !== "object") return null
      const r = raw as Record<string, unknown>
      const behavior = toStr(r.behavior)
      const detail = toStr(r.detail)
      if (!behavior) return null
      return {
        behavior,
        category: normalizeCategory(r.category),
        detail,
      }
    })
    .filter((r): r is PrdRequirementRow => r !== null)
  if (rows.length === 0) return null
  return { type: "prd-requirements", rows }
}

function parseAcceptanceCriteriaBlock(body: string): PrdSection | null {
  const parsed = tryParseJson(body)
  if (!Array.isArray(parsed)) return null
  const rows: PrdAcceptanceCriterionRow[] = parsed
    .map((raw) => {
      if (!raw || typeof raw !== "object") return null
      const r = raw as Record<string, unknown>
      const id = toStr(r.id)
      const kind = toStr(r.kind)
      const givenWhenThen = toStr(r.given_when_then)
      const verifiedBy = toStr(r.verified_by)
      if (!givenWhenThen) return null
      return { id, kind, givenWhenThen, verifiedBy }
    })
    .filter((r): r is PrdAcceptanceCriterionRow => r !== null)
  if (rows.length === 0) return null
  return { type: "prd-acceptance-criteria", rows }
}

function parseMetricsBlock(body: string): PrdSection | null {
  const parsed = tryParseJson(body)
  if (!parsed || typeof parsed !== "object" || Array.isArray(parsed)) return null
  const r = parsed as Record<string, unknown>
  const primary =
    parseMetricPoint(r.primary) ?? { name: "", current: "", target: "" }
  const secondaryRaw = Array.isArray(r.secondary) ? (r.secondary as unknown[]) : []
  const secondary: PrdMetricPoint[] = secondaryRaw
    .map(parseMetricPoint)
    .filter((m): m is PrdMetricPoint => m !== null)
  const guardrailsRaw = Array.isArray(r.guardrails)
    ? (r.guardrails as unknown[])
    : []
  const guardrails: PrdGuardrail[] = guardrailsRaw
    .map((raw) => {
      if (!raw || typeof raw !== "object") return null
      const g = raw as Record<string, unknown>
      const name = toStr(g.name)
      if (!name) return null
      return {
        name,
        baseline: toStr(g.baseline),
        bound: toStr(g.bound),
      }
    })
    .filter((g): g is PrdGuardrail => g !== null)
  if (!primary.name && secondary.length === 0 && guardrails.length === 0) {
    return null
  }
  return { type: "prd-metrics", primary, secondary, guardrails }
}

function parseRisksBlock(body: string): PrdSection | null {
  const parsed = tryParseJson(body)
  if (!Array.isArray(parsed)) return null
  const rows: PrdRiskRow[] = parsed
    .map((raw) => {
      if (!raw || typeof raw !== "object") return null
      const r = raw as Record<string, unknown>
      const risk = toStr(r.risk)
      if (!risk) return null
      return {
        risk,
        severity: normalizeSeverity(r.severity),
        mitigation: toStr(r.mitigation),
      }
    })
    .filter((r): r is PrdRiskRow => r !== null)
  if (rows.length === 0) return null
  return { type: "prd-risks", rows }
}

function parseMilestonesBlock(body: string): PrdSection | null {
  const parsed = tryParseJson(body)
  if (!Array.isArray(parsed)) return null
  const phases: PrdMilestonePhase[] = parsed
    .map((raw) => {
      if (!raw || typeof raw !== "object") return null
      const p = raw as Record<string, unknown>
      const phase = toStr(p.phase)
      const itemsRaw = Array.isArray(p.items) ? (p.items as unknown[]) : []
      const items = itemsRaw.map((it) => toStr(it)).filter(Boolean)
      if (!phase && items.length === 0) return null
      return { phase, items }
    })
    .filter((p): p is PrdMilestonePhase => p !== null)
  if (phases.length === 0) return null
  return { type: "prd-milestones", phases }
}

function parseDodBlock(body: string): PrdSection | null {
  const parsed = tryParseJson(body)
  if (!Array.isArray(parsed)) return null
  const items = (parsed as unknown[]).map((it) => toStr(it)).filter(Boolean)
  if (items.length === 0) return null
  return { type: "prd-dod", items }
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

function fallbackParagraphFromBlock(name: string, body: string): PrdSection {
  // Last-resort fallback so a malformed block doesn't vanish from the doc.
  const compact = body.trim().replace(/\s+/g, " ").slice(0, 240)
  return {
    type: "p",
    text: `[${name} block — could not parse: ${compact}…]`,
  }
}

function parseSemanticBlock(name: string, _attrs: string, body: string): PrdSection[] {
  switch (name) {
    case "context-chip":
      // Shared with evidence — same renderer handles both formats.
      return [{ type: "v2-context-chip", text: body.trim() }]
    case "tldr":
      return [parseTldrBlock(body) ?? fallbackParagraphFromBlock(name, body)]
    case "problem":
      return [parseProblemBlock(body) ?? fallbackParagraphFromBlock(name, body)]
    case "hypothesis":
      return [
        parseHypothesisBlock(body) ?? fallbackParagraphFromBlock(name, body),
      ]
    case "requirements":
      return [
        parseRequirementsBlock(body) ?? fallbackParagraphFromBlock(name, body),
      ]
    case "acceptance-criteria":
      return [
        parseAcceptanceCriteriaBlock(body) ??
          fallbackParagraphFromBlock(name, body),
      ]
    case "metrics":
      return [parseMetricsBlock(body) ?? fallbackParagraphFromBlock(name, body)]
    case "risks":
      return [parseRisksBlock(body) ?? fallbackParagraphFromBlock(name, body)]
    case "milestones":
      return [
        parseMilestonesBlock(body) ?? fallbackParagraphFromBlock(name, body),
      ]
    case "dod":
      return [parseDodBlock(body) ?? fallbackParagraphFromBlock(name, body)]
    default:
      return [fallbackParagraphFromBlock(name, body)]
  }
}

/* ---------- main entry ---------- */

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

    // Semantic block (:::name [...]).
    const header = parseBlockHeader(line)
    if (header) {
      flushBullets()
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
        // No closing fence — treat opener as self-closing single-line block.
        for (const s of parseSemanticBlock(header.name, header.attrs, "")) {
          sections.push(s)
        }
      }
      continue
    }

    // Fenced ```chart``` block (PRD templates can emit a chart in
    // ## Context if the LLM chooses).
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
    title: title || "PRD",
    sections,
  }
}
