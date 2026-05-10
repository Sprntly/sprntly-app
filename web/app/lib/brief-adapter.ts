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

/** Coerce optional / legacy API fields so evidence sections still render. */
function insightArrays(insight: Insight) {
  return {
    why_this_ranks: Array.isArray(insight.why_this_ranks) ? insight.why_this_ranks : [],
    why_alternatives_dont_hold: Array.isArray(insight.why_alternatives_dont_hold)
      ? insight.why_alternatives_dont_hold
      : [],
    impact_math: Array.isArray(insight.impact_math) ? insight.impact_math : [],
    verification_metrics: Array.isArray(insight.verification_metrics)
      ? insight.verification_metrics
      : [],
    convergence: Array.isArray(insight.convergence) ? insight.convergence : [],
    user_quotes: Array.isArray(insight.user_quotes) ? insight.user_quotes : [],
  }
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
  if (typeof insight.confidence === "number") {
    tags.push({
      label: `CONFIDENCE ${insight.confidence.toFixed(2)}`,
      className: "tag-confidence",
    })
  }

  const metrics = (insight.metrics || []).map((m) => ({
    label: m.label,
    value: m.value,
    valueClass: tagMeta.tagType === "fix"
      ? ("neg" as const)
      : tagMeta.tagType === "double"
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

  const ranksHtml = bulletList(arrays.why_this_ranks)
  if (ranksHtml) evidenceSections.push({ sectionTitle: "Why this ranks", html: ranksHtml })

  const altsHtml = bulletList(arrays.why_alternatives_dont_hold)
  if (altsHtml)
    evidenceSections.push({
      sectionTitle: "Why competing explanations don't hold",
      html: altsHtml,
    })

  const convHtml = convergenceTable(arrays.convergence)
  if (convHtml)
    evidenceSections.push({ sectionTitle: "Convergence across sources", html: convHtml })

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

  const mathHtml = bulletList(arrays.impact_math)
  if (mathHtml) evidenceSections.push({ sectionTitle: "Impact math", html: mathHtml })

  const verifyHtml = bulletList(arrays.verification_metrics)
  if (verifyHtml)
    evidenceSections.push({ sectionTitle: "Verification metrics", html: verifyHtml })

  if (evidenceSections.length === 0) {
    const parts: string[] = []
    if (insight.subtitle) parts.push(`<p>${escapeHtml(insight.subtitle)}</p>`)
    if (insight.recommendation)
      parts.push(`<p><strong>Recommendation</strong> — ${escapeHtml(insight.recommendation)}</p>`)
    if (parts.length === 0 && insight.title) {
      parts.push(`<p>${escapeHtml(insight.title)}</p>`)
    }
    if (parts.length > 0) {
      evidenceSections.push({ sectionTitle: "Finding overview", html: parts.join("") })
    }
  }

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
