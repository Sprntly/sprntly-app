/**
 * Brief v2 adapter — turns the raw `/v1/brief/current` payload into the
 * narrative-shaped state the BriefV2Render component consumes:
 *
 *   - one hero finding (LLM picks via `is_headline`; fallback: highest
 *     confidence) with an inline chart + optional verbatim quote
 *   - 0–2 compact supporting findings
 *   - 3-tile KPI strip at the top (total at risk / recoverable / sources)
 *   - convergence chips with strength badges per finding
 *
 * Detail-key parity with the v1 adapter: a finding's `detailKey` here is
 * the same `${tagType}-${rankWithinTag}` string the v1 adapter computes,
 * so View-evidence routing through `briefDetails` keeps working unchanged.
 */
import type {
  BriefActionAccent,
  BriefSecondaryCtaBehavior,
  BriefTagType,
  PrdChartDatum,
  PrdChartKind,
} from "../types/content"
import type { Brief, ChartHint, Insight } from "./api"

// ---- Types ----------------------------------------------------------------

export type BriefV2Strength = "Strong" | "Moderate" | "Weak"

export interface BriefV2Convergence {
  source: string
  signal: string
  strength: BriefV2Strength
}

export interface BriefV2InlineChart {
  kind: PrdChartKind
  title: string
  subtitle?: string
  data: PrdChartDatum[]
}

export interface BriefV2Quote {
  body: string
  source: string
}

export type BriefV2KpiTone = "positive" | "negative" | "neutral"

export interface BriefV2StatTile {
  value: string
  label: string
  tone: BriefV2KpiTone
}

interface BriefV2CardBase {
  detailKey: string | undefined
  actionAccent: BriefActionAccent
  actionLabel: string
  tagType: BriefTagType
  tagLabel: string
  category: string
  priority: string
  confidence: number
  // Whether this finding's fix can be visualized as a UI prototype. Drives
  // whether the "Generate/View prototype" option is offered. Defaults to true
  // for legacy briefs that predate the LLM `prototypeable` flag.
  prototypeable: boolean
  title: string
  body: string
  metricHighlight: string
  statTiles: BriefV2StatTile[]
  // Every insight ships 2–4 chart_hints (required by the synthesis schema), so
  // every card — hero and supporting alike — carries an inline chart.
  chart: BriefV2InlineChart | null
  convergence: BriefV2Convergence[]
  secondaryCtaLabel: string
  secondaryCtaBehavior: BriefSecondaryCtaBehavior
  askQuestion: string
}

export interface BriefV2HeroFinding extends BriefV2CardBase {
  kind: "hero"
  quote: BriefV2Quote | null
}

export interface BriefV2CompactFinding extends BriefV2CardBase {
  kind: "compact"
  extraConvergenceCount: number
}

export interface BriefV2KpiTile {
  label: string
  value: string
  tone: BriefV2KpiTone
}

export interface BriefV2State {
  headline: string | null
  weekOf: string | null
  company: string
  productArea: string
  kpiTiles: BriefV2KpiTile[]
  hero: BriefV2HeroFinding | null
  supporting: BriefV2CompactFinding[]
  sourcesLine: string
}

// ---- Internal helpers -----------------------------------------------------

const TAG_META: Record<string, {
  tagType: BriefTagType
  tagLabel: string
  actionAccent: BriefActionAccent
  actionLabel: string
}> = {
  something_better: {
    tagType: "double",
    tagLabel: "DOUBLE DOWN",
    actionAccent: "optimize",
    actionLabel: "OPTIMIZE",
  },
  something_new: {
    tagType: "new",
    tagLabel: "WORTH EXPLORING",
    actionAccent: "build",
    actionLabel: "BUILD",
  },
  something_broken: {
    tagType: "fix",
    tagLabel: "WHAT'S BROKEN",
    actionAccent: "fix",
    actionLabel: "FIX",
  },
}

function metaFor(tag: string) {
  return TAG_META[tag] || TAG_META.something_broken
}

function detailKeyFor(tagType: BriefTagType, rank: number): string {
  return `${tagType}-${rank}`
}

function isHeadlineFlag(insight: Insight): boolean {
  // Optional v4 schema field — older briefs won't have it.
  const flag = (insight as unknown as { is_headline?: unknown }).is_headline
  return flag === true
}

function pickHeroIndex(insights: Insight[]): number {
  // 1) Exactly one marked is_headline → take it.
  // 2) If zero or multiple are marked → highest confidence wins.
  const marked = insights
    .map((ins, i) => (isHeadlineFlag(ins) ? i : -1))
    .filter((i) => i >= 0)
  if (marked.length === 1) return marked[0]
  let best = 0
  for (let i = 1; i < insights.length; i++) {
    if ((insights[i].confidence ?? 0) > (insights[best].confidence ?? 0)) best = i
  }
  return best
}

function rankWithinTag(insights: Insight[]): Map<number, number> {
  // For each insight (by original index), compute its 1-based rank inside
  // the bucket of insights sharing its tag — mirrors the v1 adapter so
  // detail-key lookups stay aligned across both renders.
  const counters: Record<string, number> = {}
  const rank = new Map<number, number>()
  insights.forEach((ins, i) => {
    const key = TAG_META[ins.tag] ? ins.tag : "something_broken"
    counters[key] = (counters[key] || 0) + 1
    rank.set(i, counters[key])
  })
  return rank
}

function strengthOf(raw: string): BriefV2Strength {
  const t = (raw || "").toLowerCase()
  if (t.includes("strong") || t.includes("very high") || t.startsWith("high")) {
    return "Strong"
  }
  if (t.includes("weak") || t.includes("low")) return "Weak"
  return "Moderate"
}

function convergenceRows(insight: Insight): BriefV2Convergence[] {
  const conv = Array.isArray(insight.convergence) ? insight.convergence : []
  return conv
    .filter((c) => c && (c.source || c.signal))
    .map((c) => ({
      source: c.source || "",
      signal: c.signal || "",
      strength: strengthOf(c.strength || ""),
    }))
}

function bodyFor(insight: Insight): string {
  const parts = [insight.subtitle?.trim(), insight.recommendation?.trim()].filter(
    Boolean,
  )
  let t = parts.join(" ")
  if (!t.trim()) t = insight.headline?.trim() || insight.title
  if (t.length > 560) return `${t.slice(0, 557)}…`
  return t
}

function metricHighlightFor(insight: Insight, accent: BriefActionAccent): string {
  const m0 = insight.metrics?.[0]
  if (!m0) return accent === "fix" ? "Impact · scale · effort" : "Opportunity signal"
  const v = String(m0.value).trim()
  const lab = String(m0.label).trim()
  if (accent === "fix") return `${v} ${lab}`.trim()
  if (v.startsWith("+") || v.startsWith("$") || v.startsWith("-")) return `${v} ${lab}`.trim()
  if (accent === "build") return `+${v} ${lab}`.replace(/^\+\+/, "+")
  return `${v} · ${lab}`
}

function categoryFor(insight: Insight, accent: BriefActionAccent): string {
  const d = insight.domain?.trim()
  if (d) return d.toUpperCase()
  if (accent === "fix") return "RETENTION"
  if (accent === "build") return "GROWTH"
  return "REVENUE"
}

function statTilesFor(insight: Insight, accent: BriefActionAccent): BriefV2StatTile[] {
  const metrics = insight.metrics || []
  if (metrics.length === 0) return []
  const firstTone: BriefV2KpiTone =
    accent === "fix" ? "negative" : accent === "build" ? "positive" : "neutral"
  return metrics.slice(0, 3).map((m, i) => ({
    value: String(m.value ?? "").trim(),
    label: String(m.label ?? "").trim(),
    tone: i === 0 ? firstTone : "neutral",
  }))
}

function toInlineChart(h: ChartHint): BriefV2InlineChart {
  const kind = String(h.kind || "bar").toLowerCase() as PrdChartKind
  return {
    kind,
    title: h.title || "",
    subtitle: (h as ChartHint & { subtitle?: string }).subtitle,
    data: (Array.isArray(h.data) ? h.data : []).map((d) => ({
      label: d.label,
      value: typeof d.value === "number" ? d.value : Number(d.value) || 0,
    })),
  }
}

// Pick the chart_hint that renders best in the card's compact inline slot.
// bar / pie read cleanly at mini size (a few comparable categories or a
// share-of-whole ring); `stat` is heterogeneous hero numbers that map poorly
// to bars, so rank it last. Every insight ships 2–4 hints (mixed kinds), so a
// well-suited one is almost always available.
const CHART_KIND_RANK: Record<string, number> = {
  bar: 0,
  pie: 1,
  donut: 1,
  line: 2,
  gauge: 2,
  stat: 5,
}

function pickInsightChart(insight: Insight): BriefV2InlineChart | null {
  const hints = (Array.isArray(insight.chart_hints) ? insight.chart_hints : []).filter(
    (h) => h && typeof h === "object" && Array.isArray(h.data) && h.data.length > 0,
  )
  if (hints.length === 0) return null
  const best = hints
    .map((h, i) => ({ h, i, rank: CHART_KIND_RANK[String(h.kind || "").toLowerCase()] ?? 3 }))
    // Stable: lowest rank wins; ties keep the LLM's original ordering.
    .sort((a, b) => a.rank - b.rank || a.i - b.i)[0]
  return toInlineChart(best.h)
}

function pickHeroQuote(insight: Insight): BriefV2Quote | null {
  const qs = Array.isArray(insight.user_quotes) ? insight.user_quotes : []
  for (const q of qs) {
    if (!q || !q.quote) continue
    return { body: q.quote, source: q.source || "" }
  }
  return null
}

function buildCardBase(
  insight: Insight,
  rank: number,
  priority: string,
): BriefV2CardBase {
  const m = metaFor(insight.tag)
  return {
    detailKey: detailKeyFor(m.tagType, rank),
    actionAccent: m.actionAccent,
    actionLabel: m.actionLabel,
    tagType: m.tagType,
    tagLabel: m.tagLabel,
    category: categoryFor(insight, m.actionAccent),
    priority,
    confidence: insight.confidence ?? 0,
    // Missing flag (legacy briefs) ⇒ treat as prototypeable so we don't hide
    // the option on briefs generated before the flag existed.
    prototypeable: insight.prototypeable !== false,
    title: insight.title,
    body: bodyFor(insight),
    metricHighlight: metricHighlightFor(insight, m.actionAccent),
    statTiles: statTilesFor(insight, m.actionAccent),
    chart: pickInsightChart(insight),
    convergence: convergenceRows(insight),
    secondaryCtaLabel: "Generate PRD →",
    secondaryCtaBehavior: "generate_prd",
    askQuestion: `Tell me more about: ${insight.title}`,
  }
}

function buildHero(insight: Insight, rank: number): BriefV2HeroFinding {
  return {
    kind: "hero",
    ...buildCardBase(insight, rank, "P0"),
    quote: pickHeroQuote(insight),
  }
}

const COMPACT_CHIP_CAP = 2

function buildCompact(insight: Insight, rank: number, priority: string): BriefV2CompactFinding {
  const base = buildCardBase(insight, rank, priority)
  const trimmed = base.convergence.slice(0, COMPACT_CHIP_CAP)
  return {
    kind: "compact",
    ...base,
    convergence: trimmed,
    extraConvergenceCount: Math.max(0, base.convergence.length - trimmed.length),
  }
}

function prettyCompany(company: string): string {
  const c = (company || "company").trim()
  return c.replace(/[-_]/g, " ").replace(/\b\w/g, (c) => c.toUpperCase())
}

/** Display label for a brief's company: prefer the human-readable
 * `company_name` from the backend; the slug is an internal key and only
 * shows (prettified) when no companies row exists (legacy demo datasets). */
export function companyLabel(brief: Pick<Brief, "company" | "company_name">): string {
  return brief.company_name?.trim() || prettyCompany(brief.company || "")
}

function buildKpiTiles(insights: Insight[]): BriefV2KpiTile[] {
  // Tile 1: lead impact metric (from hero) — tone follows hero tag.
  // Tile 2: secondary scale metric (hero's second metric, or the first
  // metric of the next-strongest insight as a fallback).
  // The source-count tile used to live here but felt like instrumentation
  // rather than a business signal; sources are still listed below the
  // card stack via `sourcesLine`.
  if (insights.length === 0) return []
  const heroIdx = pickHeroIndex(insights)
  const hero = insights[heroIdx]
  const heroMeta = metaFor(hero.tag)
  const tone: BriefV2KpiTone =
    heroMeta.tagType === "fix"
      ? "negative"
      : heroMeta.tagType === "double"
      ? "positive"
      : "neutral"

  const tiles: BriefV2KpiTile[] = []
  const m0 = hero.metrics?.[0]
  if (m0) {
    tiles.push({ label: m0.label || "Lead impact", value: String(m0.value), tone })
  }

  const m1 = hero.metrics?.[1]
  if (m1) {
    tiles.push({ label: m1.label || "Scale", value: String(m1.value), tone: "neutral" })
  } else {
    const other = insights.find((_, i) => i !== heroIdx)
    const om = other?.metrics?.[0]
    if (om) tiles.push({ label: om.label || "Scale", value: String(om.value), tone: "neutral" })
  }

  return tiles
}

function buildSourcesLine(insights: Insight[]): string {
  const seen = new Set<string>()
  for (const ins of insights) {
    for (const c of ins.convergence || []) {
      if (c.source) seen.add(c.source)
    }
  }
  return Array.from(seen).slice(0, 8).join(" · ")
}

// ---- Public entry point ---------------------------------------------------

export function briefToBriefV2State(brief: Brief): BriefV2State {
  const insights = (brief.insights || []).filter((i) => Boolean(i))
  const empty: BriefV2State = {
    headline: null,
    weekOf: null,
    company: companyLabel(brief),
    productArea: "",
    kpiTiles: [],
    hero: null,
    supporting: [],
    sourcesLine: "",
  }
  if (insights.length === 0) return empty

  const rankMap = rankWithinTag(insights)
  const heroIdx = pickHeroIndex(insights)
  const heroInsight = insights[heroIdx]
  const heroRank = rankMap.get(heroIdx) ?? 1
  const hero = buildHero(heroInsight, heroRank)

  const supporting: BriefV2CompactFinding[] = []
  let supportingIdx = 0
  insights.forEach((ins, i) => {
    if (i === heroIdx) return
    const r = rankMap.get(i) ?? 1
    supportingIdx += 1
    supporting.push(buildCompact(ins, r, `P${supportingIdx}`))
  })

  const productArea =
    heroInsight.domain && heroInsight.subdomain
      ? `${heroInsight.domain} · ${heroInsight.subdomain}`
      : heroInsight.domain || "Product"

  return {
    headline: brief.summary_headline?.trim() || null,
    weekOf: brief.week_label || brief.generated_at?.slice(0, 10) || null,
    company: companyLabel(brief),
    productArea,
    kpiTiles: buildKpiTiles(insights),
    hero,
    supporting,
    sourcesLine: buildSourcesLine(insights),
  }
}
