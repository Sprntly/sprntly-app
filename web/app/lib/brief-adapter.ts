import type {
  AppContentState,
  BriefFindingRow,
  BriefImpactStat,
  BriefSectionRow,
  BriefState,
  BriefTagType,
} from "../types/content"
import type { Brief, Insight } from "./api"

const TAG_MAP: Record<string, { tagType: BriefTagType; tagLabel: string; titlePrefix: string; titleEmphasis: string; subtotalClass: "pos" | "neg" | "warn" }> = {
  something_better: {
    tagType: "double",
    tagLabel: "DOUBLE DOWN",
    titlePrefix: "Double",
    titleEmphasis: "down",
    subtotalClass: "pos",
  },
  something_new: {
    tagType: "new",
    tagLabel: "WORTH EXPLORING",
    titlePrefix: "Something",
    titleEmphasis: "new",
    subtotalClass: "warn",
  },
  something_broken: {
    tagType: "fix",
    tagLabel: "WHAT'S BROKEN",
    titlePrefix: "What's",
    titleEmphasis: "broken",
    subtotalClass: "neg",
  },
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
  }
}

function sectionFromInsights(tag: keyof typeof TAG_MAP, insights: Insight[]): BriefSectionRow {
  const meta = TAG_MAP[tag]
  const findings = insights.map((ins, i) => findingFromInsight(ins, i + 1))
  // Pull the most prominent dollar/percent from the first finding's first metric.
  const subtotal = findings[0]?.impacts[0]?.value ?? ""
  return {
    titlePrefix: meta.titlePrefix,
    titleEmphasis: meta.titleEmphasis,
    subtotal,
    subtotalClass: meta.subtotalClass,
    findings,
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

export type BriefHydrationPatch = Partial<AppContentState>

export function briefToContentPatch(brief: Brief): BriefHydrationPatch {
  return {
    brief: briefToBriefState(brief),
  }
}
