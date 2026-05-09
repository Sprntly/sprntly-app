import type {
  AppContentState,
  BriefFindingRow,
  BriefImpactStat,
  BriefSectionRow,
  BriefState,
  BriefTagType,
  DetailState,
} from "../types/content"
import type { Brief, Insight } from "./api"

const TAG_MAP: Record<
  string,
  {
    tagType: BriefTagType
    tagLabel: string
    titlePrefix: string
    titleEmphasis: string
    subtotalClass: "pos" | "neg" | "warn"
    detailTagClass: string
  }
> = {
  something_better: {
    tagType: "double",
    tagLabel: "DOUBLE DOWN",
    titlePrefix: "Double",
    titleEmphasis: "down",
    subtotalClass: "pos",
    detailTagClass: "tag-double",
  },
  something_new: {
    tagType: "new",
    tagLabel: "WORTH EXPLORING",
    titlePrefix: "Something",
    titleEmphasis: "new",
    subtotalClass: "warn",
    detailTagClass: "tag-new",
  },
  something_broken: {
    tagType: "fix",
    tagLabel: "WHAT'S BROKEN",
    titlePrefix: "What's",
    titleEmphasis: "broken",
    subtotalClass: "neg",
    detailTagClass: "tag-fix",
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

function bulletList(items: string[] | undefined): string | null {
  if (!items || items.length === 0) return null
  return (
    "<ul>" +
    items.map((s) => `<li>${escapeHtml(s)}</li>`).join("") +
    "</ul>"
  )
}

function convergenceTable(rows: Insight["convergence"] | undefined): string | null {
  if (!rows || rows.length === 0) return null
  const head =
    "<thead><tr><th>Source</th><th>Signal</th><th>Strength</th></tr></thead>"
  const body =
    "<tbody>" +
    rows
      .map(
        (r) =>
          `<tr><td>${escapeHtml(r.source)}</td><td>${escapeHtml(
            r.signal,
          )}</td><td>${escapeHtml(r.strength)}</td></tr>`,
      )
      .join("") +
    "</tbody>"
  return `<table class="convergence">${head}${body}</table>`
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
    desc: insight.subtitle,
    impacts,
    askQuestion: `Tell me more about: ${insight.title.slice(0, 80)}`,
    detailKey: detailKeyFor(meta.tagType, rank),
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

function detailFromInsight(insight: Insight, rank: number): DetailState {
  const meta = TAG_MAP[insight.tag] || TAG_MAP.something_broken
  const tags: DetailState["tags"] = [
    { label: meta.tagLabel, className: meta.detailTagClass },
  ]
  if (insight.domain) tags.push({ label: insight.domain.toUpperCase(), className: "tag-domain" })
  if (insight.subdomain) tags.push({ label: insight.subdomain.toUpperCase(), className: "tag-sub" })
  if (typeof insight.confidence === "number") {
    tags.push({
      label: `CONFIDENCE ${insight.confidence.toFixed(2)}`,
      className: "tag-confidence",
    })
  }

  const metrics = (insight.metrics || []).map((m) => ({
    label: m.label,
    value: m.value,
    valueClass: meta.tagType === "fix"
      ? ("neg" as const)
      : meta.tagType === "double"
      ? ("pos" as const)
      : undefined,
  }))

  const evidenceSections: DetailState["evidenceSections"] = []

  if (insight.headline) {
    evidenceSections.push({
      sectionTitle: "The headline",
      html: `<p>${escapeHtml(insight.headline)}</p>`,
    })
  }

  const ranksHtml = bulletList(insight.why_this_ranks)
  if (ranksHtml) evidenceSections.push({ sectionTitle: "Why this ranks", html: ranksHtml })

  const altsHtml = bulletList(insight.why_alternatives_dont_hold)
  if (altsHtml)
    evidenceSections.push({
      sectionTitle: "Why competing explanations don't hold",
      html: altsHtml,
    })

  const convHtml = convergenceTable(insight.convergence)
  if (convHtml)
    evidenceSections.push({ sectionTitle: "Convergence across sources", html: convHtml })

  if (insight.user_quotes && insight.user_quotes.length > 0) {
    evidenceSections.push({
      sectionTitle: "User quotes",
      quoteRows: insight.user_quotes.map((q) => ({
        source: q.source,
        quote: q.quote,
        meta: [],
      })),
    })
  }

  const mathHtml = bulletList(insight.impact_math)
  if (mathHtml) evidenceSections.push({ sectionTitle: "Impact math", html: mathHtml })

  const verifyHtml = bulletList(insight.verification_metrics)
  if (verifyHtml)
    evidenceSections.push({ sectionTitle: "Verification metrics", html: verifyHtml })

  return {
    backLabel: "← Back to brief",
    tags,
    title: insight.headline || insight.title,
    summary: insight.subtitle,
    metrics,
    evidenceSections,
    cta: insight.recommendation
      ? {
          headline: "Recommendation",
          sub: insight.recommendation,
          dismissLabel: "Snooze",
          primaryLabel: "Generate PRD",
        }
      : null,
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

  return {
    weekRange: brief.week_label || null,
    subline: brief.summary_headline || null,
    impactEyebrow: "This week",
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
  const grouped: Record<string, Insight[]> = {}
  for (const ins of insights) {
    const key = TAG_MAP[ins.tag] ? ins.tag : "something_broken"
    ;(grouped[key] ||= []).push(ins)
  }
  const map: Record<string, DetailState> = {}
  for (const tag of Object.keys(grouped) as (keyof typeof TAG_MAP)[]) {
    grouped[tag].forEach((insight, idx) => {
      const meta = TAG_MAP[tag]
      const rank = idx + 1
      const key = detailKeyFor(meta.tagType, rank)
      map[key] = detailFromInsight(insight, rank)
    })
  }
  return map
}

export type BriefHydrationPatch = Partial<AppContentState>

export function briefToContentPatch(brief: Brief): BriefHydrationPatch {
  return {
    brief: briefToBriefState(brief),
    briefDetails: briefToDetailMap(brief),
  }
}
