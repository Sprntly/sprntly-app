import type {
  AppContentState,
  BriefActionAccent,
  BriefDocFooter,
  BriefDocHeader,
  BriefFindingRow,
  BriefImpactStat,
  BriefSecondaryCtaBehavior,
  BriefSectionRow,
  BriefState,
  BriefTagType,
  DetailState,
} from "../types/content"
import type { Brief, ChartHint, ConvergenceItem, Insight } from "./api"
import { briefToBriefV2State, companyLabel } from "./brief-v2-adapter"
import { accentForInsight, labelForInsight, resolveSkillType } from "./brief-skill-taxonomy"

type TagMeta = {
  tagType: BriefTagType
  tagLabel: string
  titlePrefix: string
  titleEmphasis: string
  subtotalClass: "pos" | "neg" | "warn"
  detailTagClass: string
  actionAccent: BriefActionAccent
  actionLabel: string
  secondaryCtaLabel: string
  secondaryCtaBehavior: BriefSecondaryCtaBehavior
}

const TAG_MAP: Record<string, TagMeta> = {
  something_better: {
    tagType: "double",
    tagLabel: "DOUBLE DOWN",
    titlePrefix: "Double",
    titleEmphasis: "down",
    subtotalClass: "pos",
    detailTagClass: "tag-double",
    actionAccent: "optimize",
    actionLabel: "OPTIMIZE",
    secondaryCtaLabel: "Generate PRD →",
    secondaryCtaBehavior: "generate_prd",
  },
  something_new: {
    tagType: "new",
    tagLabel: "WORTH EXPLORING",
    titlePrefix: "Something",
    titleEmphasis: "new",
    subtotalClass: "warn",
    detailTagClass: "tag-new",
    actionAccent: "build",
    actionLabel: "BUILD",
    secondaryCtaLabel: "Generate PRD →",
    secondaryCtaBehavior: "generate_prd",
  },
  something_broken: {
    tagType: "fix",
    tagLabel: "WHAT'S BROKEN",
    titlePrefix: "What's",
    titleEmphasis: "broken",
    subtotalClass: "neg",
    detailTagClass: "tag-fix",
    actionAccent: "fix",
    actionLabel: "FIX",
    secondaryCtaLabel: "Generate PRD →",
    secondaryCtaBehavior: "generate_prd",
  },
}

function detailKeyFor(tagType: BriefTagType, rank: number): string {
  return `${tagType}-${rank}`
}

function escapeHtml(s: string): string {
  return s
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;")
    .replace(/'/g, "&#39;")
}

function strengthToValue(s: string): number {
  const n = parseFloat(s)
  if (!Number.isNaN(n)) return n
  const t = s.toLowerCase()
  if (t.includes("very high") || t.includes("strong")) return 4
  if (t.includes("high")) return 3
  if (t.includes("med")) return 2
  if (t.includes("low") || t.includes("weak")) return 1
  return 1
}

function chartHtml(chart: ChartHint): string {
  const data = chart.data || []
  if (data.length === 0) return ""
  const max = Math.max(...data.map((d) => d.value), 1)
  const title = `<div class="ch-chart-title">${escapeHtml(chart.title)}</div>`

  if (chart.kind === "stat") {
    const items = data
      .map(
        (d) => `
        <div class="ch-stat">
          <div class="ch-stat-val">${escapeHtml(String(d.value))}</div>
          <div class="ch-stat-lbl">${escapeHtml(d.label)}</div>
        </div>`,
      )
      .join("")
    return `<div class="ch-chart">${title}<div class="ch-stats">${items}</div></div>`
  }

  if (chart.kind === "line") {
    const w = 480
    const h = 110
    const pad = 8
    const n = data.length
    const points = data
      .map((d, i) => {
        const x = pad + (i * (w - pad * 2)) / Math.max(n - 1, 1)
        const y = h - pad - (d.value / max) * (h - pad * 2)
        return `${x.toFixed(1)},${y.toFixed(1)}`
      })
      .join(" ")
    const dots = data
      .map((d, i) => {
        const x = pad + (i * (w - pad * 2)) / Math.max(n - 1, 1)
        const y = h - pad - (d.value / max) * (h - pad * 2)
        return `<circle cx="${x.toFixed(1)}" cy="${y.toFixed(1)}" r="2.5" />`
      })
      .join("")
    const labels = data
      .map((d, i) => {
        const x = pad + (i * (w - pad * 2)) / Math.max(n - 1, 1)
        return `<text x="${x.toFixed(1)}" y="${h - 1}" text-anchor="middle" class="ch-axis">${escapeHtml(d.label)}</text>`
      })
      .join("")
    return `<div class="ch-chart">${title}<svg viewBox="0 0 ${w} ${h}" class="ch-line" preserveAspectRatio="none"><polyline points="${points}" fill="none" stroke="currentColor" stroke-width="2" />${dots}${labels}</svg></div>`
  }

  // bar (default)
  const rows = data
    .map((d) => {
      const pct = (d.value / max) * 100
      return `
        <div class="ch-bar-row">
          <div class="ch-bar-label">${escapeHtml(d.label)}</div>
          <div class="ch-bar-track"><div class="ch-bar-fill" style="width:${pct.toFixed(1)}%"></div></div>
          <div class="ch-bar-val">${escapeHtml(String(d.value))}</div>
        </div>`
    })
    .join("")
  return `<div class="ch-chart">${title}<div class="ch-bars">${rows}</div></div>`
}

function convergenceVisualHtml(
  conv: ConvergenceItem[],
  hints: ChartHint[],
): string | null {
  const charts: string[] = []
  if (hints.length > 0) {
    for (const h of hints) {
      const html = chartHtml(h)
      if (html) charts.push(html)
    }
  }
  if (conv.length > 0) {
    charts.push(
      chartHtml({
        kind: "bar",
        title: "Signal strength by source",
        data: conv.map((c) => ({
          label: `${c.source} — ${c.signal}`,
          value: strengthToValue(c.strength),
        })),
      }),
    )
  }
  if (charts.length === 0) return null
  return charts.join("")
}

/** Coerce optional / legacy API fields so evidence sections still render. */
function insightArrays(insight: Insight) {
  return {
    convergence: Array.isArray(insight.convergence) ? insight.convergence : [],
    user_quotes: Array.isArray(insight.user_quotes) ? insight.user_quotes : [],
    chart_hints: Array.isArray(insight.chart_hints) ? insight.chart_hints : [],
    impact_math: Array.isArray(insight.impact_math) ? insight.impact_math : [],
  }
}

/** Parse "Label: value" strings from insight.impact_math into hero metrics
 * for the Estimated impact callout. Falls back to insight.metrics if the
 * impact_math entries don't follow the convention.
 *
 * Backend now emits 2–3 highlighted metrics in this shape, e.g.
 *   "Revenue at risk: $143M/yr"
 *   "Retention impact: +15pp"
 */
function heroMetricsFor(
  insight: Insight,
  valueClass: "pos" | "neg" | undefined,
): { label: string; value: string; valueClass?: "pos" | "neg" }[] {
  const arrays = insightArrays(insight)
  const parsed: { label: string; value: string }[] = []
  for (const entry of arrays.impact_math.slice(0, 3)) {
    if (typeof entry !== "string") continue
    const m = entry.match(/^\s*([^:]{1,60}?)\s*:\s*(.+?)\s*$/)
    if (m) parsed.push({ label: m[1], value: m[2] })
  }
  if (parsed.length >= 2) {
    return parsed.map((p) => ({ ...p, valueClass }))
  }
  return (insight.metrics || []).slice(0, 3).map((m) => ({
    label: m.label,
    value: m.value,
    valueClass,
  }))
}

function signalLineFromInsight(insight: Insight): string {
  const conv = insightArrays(insight).convergence
  if (conv.length > 0) {
    return conv
      .map((c) => c.source)
      .filter(Boolean)
      .slice(0, 6)
      .join(" · ")
  }
  const m = insight.metrics || []
  if (m.length > 0) return m.map((x) => x.label).slice(0, 4).join(" · ")
  return "Grounded in weekly product corpus"
}

function findingBodyDesc(insight: Insight): string {
  const parts = [insight.subtitle?.trim(), insight.recommendation?.trim()].filter(Boolean)
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

function buildDocHeader(brief: Brief, insights: Insight[]): BriefDocHeader {
  const first = insights[0]
  const productArea =
    first.domain && first.subdomain
      ? `${first.domain} · ${first.subdomain}`
      : first.domain || "Product"
  return {
    company: companyLabel(brief),
    weekOf: brief.week_label || brief.generated_at?.slice(0, 10) || "—",
    productArea,
  }
}

function buildDocFooter(insights: Insight[]): BriefDocFooter {
  const values = insights
    .map((i) => i.metrics?.[0])
    .filter((m): m is NonNullable<typeof m> => Boolean(m))
  const total =
    values.length > 0 ? values.map((m) => `${m.value}`.trim()).join(" · ") : "—"
  const recover =
    insights.length > 1
      ? `${insights.length} ranked findings · triage near-term vs. ideation`
      : "Single ranked focus this week"
  const src = [
    ...new Set(insights.flatMap((i) => (i.convergence || []).map((c) => c.source))),
  ].filter(Boolean)
  const sources =
    src.length > 0 ? src.slice(0, 12).join(" · ") : "Corpus + model synthesis"
  return {
    totalAtRiskOrUpside: total,
    recoverableRange: recover,
    sourcesThisWeek: sources,
  }
}

function findingFromInsight(insight: Insight, rank: number): BriefFindingRow {
  const meta = TAG_MAP[insight.tag] || TAG_MAP.something_broken
  const impacts = (insight.metrics || []).map((m) => ({
    label: m.label,
    value: m.value,
    negative: meta.tagType === "fix",
    positive: meta.tagType === "double",
  }))
  const headline = (impacts[0]?.value ? `${impacts[0].value}` : "") +
    (impacts[0]?.label ? ` · ${impacts[0].label}` : "")
  return {
    rank,
    tagType: meta.tagType,
    tagLabel: meta.tagLabel,
    impactLabel: headline.trim() || (insight.domain ?? ""),
    confidence: insight.confidence ?? 0,
    title: insight.title,
    desc: findingBodyDesc(insight),
    impacts,
    askQuestion: `Tell me more about: ${insight.title}`,
    detailKey: detailKeyFor(meta.tagType, rank),
    actionAccent: meta.actionAccent,
    actionLabel: meta.actionLabel,
    skillType: resolveSkillType(insight),
    skillAccent: accentForInsight(insight),
    skillLabel: labelForInsight(insight),
    ctas: Array.isArray(insight._card?.ctas)
      ? insight._card!.ctas.map((c) => ({ label: String(c.label), style: String(c.style) }))
      : [],
    metricHighlight: metricHighlightFor(insight, meta.actionAccent),
    signalLine: signalLineFromInsight(insight),
    secondaryCtaLabel: meta.secondaryCtaLabel,
    secondaryCtaBehavior: meta.secondaryCtaBehavior,
  }
}

function sectionFromInsights(
  tag: keyof typeof TAG_MAP,
  insights: Insight[],
): BriefSectionRow {
  const meta = TAG_MAP[tag]
  const findings = insights.map((ins, i) => findingFromInsight(ins, i + 1))
  const subtotal = findings[0]?.impacts[0]?.value ?? ""
  return {
    titlePrefix: meta.titlePrefix,
    titleEmphasis: meta.titleEmphasis,
    subtotal,
    subtotalClass: meta.subtotalClass,
    findings,
  }
}

function buildSummary(insight: Insight): string {
  const parts: string[] = []
  if (insight.subtitle) parts.push(insight.subtitle.trim())
  if (insight.headline && insight.headline.trim() !== insight.title.trim()) {
    parts.push(insight.headline.trim())
  }
  if (insight.domain || insight.subdomain) {
    const scope = [insight.domain, insight.subdomain].filter(Boolean).join(" · ")
    if (scope) parts.push(`Scope: ${scope}.`)
  }
  return parts.join(" ")
}

function detailFromInsight(
  insight: Insight,
  rank: number,
  source?: { briefId: number; insightIndex: number },
): DetailState {
  const tagMeta = TAG_MAP[insight.tag] || TAG_MAP.something_broken
  const arrays = insightArrays(insight)
  const tags: DetailState["tags"] = [
    { label: tagMeta.tagLabel, className: tagMeta.detailTagClass },
  ]
  if (insight.domain) tags.push({ label: insight.domain.toUpperCase(), className: "tag-domain" })
  if (insight.subdomain) tags.push({ label: insight.subdomain.toUpperCase(), className: "tag-sub" })

  const valueClass =
    tagMeta.tagType === "fix"
      ? ("neg" as const)
      : tagMeta.tagType === "double"
      ? ("pos" as const)
      : undefined
  const metrics = heroMetricsFor(insight, valueClass)

  const evidenceSections: DetailState["evidenceSections"] = []

  // Structured chart specs from chart_hints — rendered inline by DetailScreen.
  // The chart_hints API field is the brief insight's data-science slicing.
  type ChartSpec = NonNullable<DetailState["evidenceSections"][number]["charts"]>[number]
  const charts: ChartSpec[] = []
  for (const h of arrays.chart_hints) {
    if (!h || typeof h !== "object") continue
    const data = Array.isArray(h.data) ? h.data : []
    if (data.length === 0) continue
    const kind = String(h.kind || "bar").toLowerCase()
    if (kind !== "bar" && kind !== "line" && kind !== "pie" && kind !== "stat")
      continue
    charts.push({
      kind,
      title: h.title,
      data: data.map((d) => ({
        label: d.label,
        value: typeof d.value === "number" ? d.value : Number(d.value) || 0,
      })),
    })
  }

  if (charts.length > 0) {
    evidenceSections.push({ sectionTitle: "Evidence", charts })
  } else if (arrays.convergence.length > 0) {
    // Fallback: if no chart_hints (older brief), still show a bar of
    // signal strength so the section isn't empty.
    const html = convergenceVisualHtml(arrays.convergence, [])
    if (html) evidenceSections.push({ sectionTitle: "Evidence", html })
  }

  if (arrays.user_quotes.length > 0) {
    evidenceSections.push({
      sectionTitle: "User quotes",
      quoteRows: arrays.user_quotes.map((q) => ({
        source: q.source,
        quote: q.quote,
        meta: [],
      })),
    })
  }

  return {
    backLabel: "← Back to brief",
    tags,
    title: insight.headline || insight.title,
    summary: buildSummary(insight),
    metrics,
    evidenceSections,
    cta: {
      headline: "",
      sub: "",
      dismissLabel: "Snooze",
      primaryLabel: "Generate PRD",
    },
    meta: source,
  }
}

function impactStatsFromInsights(insights: Insight[]): BriefImpactStat[] {
  const stats: BriefImpactStat[] = []
  for (const ins of insights) {
    const m = ins.metrics?.[0]
    if (!m) continue
    const cls: "pos" | "neg" =
      ins.tag === "something_better" ? "pos" : "neg"
    stats.push({ value: m.value, label: m.label, valueClass: cls })
  }
  return stats.slice(0, 3)
}

const SECTION_ORDER: (keyof typeof TAG_MAP)[] = [
  "something_broken",
  "something_better",
  "something_new",
]

export function briefToBriefState(brief: Brief): BriefState {
  const insights = brief.insights || []
  const grouped: Record<string, Insight[]> = {}
  for (const ins of insights) {
    const key = TAG_MAP[ins.tag] ? ins.tag : "something_broken"
    ;(grouped[key] ||= []).push(ins)
  }
  const sections: BriefSectionRow[] = []
  for (const tag of SECTION_ORDER) {
    if (grouped[tag]?.length) sections.push(sectionFromInsights(tag, grouped[tag]))
  }

  const hasFindings = sections.some((s) => s.findings.length > 0)

  return {
    weekRange: brief.week_label || null,
    subline: brief.summary_headline || null,
    docSubline: null,
    docKicker: brief.summary_headline?.trim() || null,
    docHeader: hasFindings && insights.length > 0 ? buildDocHeader(brief, insights) : null,
    docFooter: hasFindings && insights.length > 0 ? buildDocFooter(insights) : null,
    impactEyebrow: null,
    impactHeadlineLead: null,
    impactHeadlineEmphasis1: null,
    impactHeadlineMid: null,
    impactHeadlineEmphasis2: null,
    impactHeadlineTrail: null,
    impactStats: impactStatsFromInsights(insights),
    metaLines: [
      `${insights.length} insight${insights.length === 1 ? "" : "s"}`,
      `Generated ${brief.generated_at?.slice(0, 10) ?? ""}`.trim(),
    ].filter(Boolean),
    sections,
  }
}

export function briefToDetailMap(brief: Brief): Record<string, DetailState> {
  const insights = brief.insights || []
  const map: Record<string, DetailState> = {}

  // Build per-tag groupings to assign rank-within-tag for the detail key.
  const grouped: Record<string, Insight[]> = {}
  for (const ins of insights) {
    const key = TAG_MAP[ins.tag] ? ins.tag : "something_broken"
    ;(grouped[key] ||= []).push(ins)
  }

  for (const tag of Object.keys(grouped) as (keyof typeof TAG_MAP)[]) {
    grouped[tag].forEach((insight, idx) => {
      const tagMeta = TAG_MAP[tag]
      const rank = idx + 1
      const key = detailKeyFor(tagMeta.tagType, rank)
      // insight_index is the position in the *original* insights array,
      // which is what the backend's /v1/prd/generate expects.
      const insightIndex = insights.indexOf(insight)
      map[key] = detailFromInsight(insight, rank, {
        briefId: brief.id,
        insightIndex,
      })
    })
  }
  return map
}

export type BriefHydrationPatch = Partial<AppContentState>

export function briefToContentPatch(brief: Brief): BriefHydrationPatch {
  return {
    brief: briefToBriefState(brief),
    briefV2: briefToBriefV2State(brief),
    briefDetails: briefToDetailMap(brief),
  }
}

/** Match weekly-brief section order: broken → better → new, then rank. */
const DETAIL_KEY_TAG_ORDER = ["fix", "double", "new"] as const

export function pickDefaultDetailKey(briefDetails: Record<string, DetailState>): string | null {
  const keys = Object.keys(briefDetails)
  if (keys.length === 0) return null
  const sorted = [...keys].sort((a, b) => {
    const [ta, na] = a.split("-")
    const [tb, nb] = b.split("-")
    const ia = DETAIL_KEY_TAG_ORDER.indexOf(ta as (typeof DETAIL_KEY_TAG_ORDER)[number])
    const ib = DETAIL_KEY_TAG_ORDER.indexOf(tb as (typeof DETAIL_KEY_TAG_ORDER)[number])
    const sa = ia === -1 ? 99 : ia
    const sb = ib === -1 ? 99 : ib
    if (sa !== sb) return sa - sb
    return (Number(na) || 0) - (Number(nb) || 0)
  })
  return sorted[0] ?? null
}
