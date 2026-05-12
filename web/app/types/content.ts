/** Serializable app payload — hydrate from API / LLM via `setContent`. */

import type { AskResponse } from "../lib/api"

export type BriefTagType = "double" | "new" | "fix"

/** Weekly brief template action accent (maps from API insight tags in the adapter). */
export type BriefActionAccent = "build" | "fix" | "decide" | "optimize" | "investigate" | "monitor"

export type BriefSecondaryCtaBehavior =
  | "generate_prd"
  | "strategy"
  | "open_analysis"
  | "set_alert"

export interface BriefFindingRow {
  rank: number
  tagType: BriefTagType
  tagLabel: string
  impactLabel: string
  confidence: number
  title: string
  desc: string
  impacts: Array<{
    label: string
    value: string
    positive?: boolean
    negative?: boolean
  }>
  askQuestion: string
  /** Stable key for looking up the matching DetailState in `briefDetails`. */
  detailKey?: string
  /** Template: BUILD / FIX / OPTIMIZE — left rail color + secondary CTA. */
  actionAccent: BriefActionAccent
  actionLabel: string
  /** Template: headline metric (e.g. +$12M LTV / yr), accent-colored. */
  metricHighlight: string
  /** Template: italic footer line of signal sources. */
  signalLine: string
  secondaryCtaLabel: string
  secondaryCtaBehavior: BriefSecondaryCtaBehavior
}

export interface BriefDocHeader {
  company: string
  weekOf: string
  productArea: string
}

export interface BriefDocFooter {
  totalAtRiskOrUpside: string
  recoverableRange: string
  sourcesThisWeek: string
}

export interface BriefSectionRow {
  titlePrefix: string
  titleEmphasis: string
  subtotal: string
  subtotalClass: "pos" | "neg" | "warn"
  findings: BriefFindingRow[]
}

export interface BriefImpactStat {
  value: string
  label: string
  valueClass?: "pos" | "neg"
}

export interface BriefState {
  weekRange: string | null
  subline: string | null
  /** Grey line under the main doc title (API summary or template tagline). */
  docSubline: string | null
  /** Optional one-line week summary from the API (`summary_headline`). */
  docKicker: string | null
  /** Template “Brief header” row — derived from dataset + insights until the API adds fields. */
  docHeader: BriefDocHeader | null
  /** Template footer strip (three columns) — derived from metrics + convergence. */
  docFooter: BriefDocFooter | null
  impactEyebrow: string | null
  impactHeadlineLead: string | null
  impactHeadlineEmphasis1: string | null
  impactHeadlineMid: string | null
  impactHeadlineEmphasis2: string | null
  impactHeadlineTrail: string | null
  impactStats: BriefImpactStat[]
  metaLines: string[]
  sections: BriefSectionRow[]
}

/** Stable id for suggestion-tile SVG (no emoji in UI). */
export type ChatCardIconId =
  | "sparkle"
  | "message"
  | "chart"
  | "diamond"
  | "document"
  | "rocket"

export interface ChatHomeCard {
  id: string
  icon: ChatCardIconId
  title: string
  desc: string
  target: "brief" | "ondemand"
  prompt?: string
}

/** Home landing: go-to destinations plus a few prefilled prompts (brief uses AI bar; Ask uses `pendingOndemandDraft`). */
export const DEFAULT_HOME_STARTER_CARDS: ChatHomeCard[] = [
  {
    id: "home-goto-brief",
    icon: "sparkle",
    title: "This week's brief",
    desc: "Ranked findings, impact, and signals in one view.",
    target: "brief",
  },
  {
    id: "home-prompt-revenue",
    icon: "chart",
    title: "What are the biggest revenue drivers",
    desc: "Fills Ask so you can edit or send.",
    target: "ondemand",
    prompt: "What are the biggest revenue drivers",
  },
  {
    id: "home-prompt-cost",
    icon: "document",
    title: "What are the biggest cost drivers",
    desc: "Fills Ask so you can edit or send.",
    target: "ondemand",
    prompt: "What are the biggest cost drivers",
  },
]

/** Curated Ask Sprntly landing chips until org-specific starters load from the API. */
export const DEFAULT_ONDEMAND_STARTERS: ChatHomeCard[] = [
  {
    id: "od-default-q3",
    icon: "diamond",
    title: "Q3 strategy",
    desc: "Turn product memory into priorities, bets, and risks.",
    target: "ondemand",
    prompt:
      "Generate a Q3 strategy from our product memory — priorities, bets, measurable goals, and the main risks to watch.",
  },
  {
    id: "od-default-prd",
    icon: "document",
    title: "PRD for team folders",
    desc: "Draft scope, rollout, and open questions.",
    target: "ondemand",
    prompt:
      "Draft a PRD for team folder permissions: problem, users, requirements, rollout plan, metrics, and open questions for eng and design.",
  },
  {
    id: "od-default-retention",
    icon: "chart",
    title: "Retention comparison",
    desc: "Compare segments or cohorts we care about.",
    target: "ondemand",
    prompt:
      "Compare retention across our top three customer segments — what differs, what might explain it, and what we should validate next.",
  },
  {
    id: "od-default-ship",
    icon: "rocket",
    title: "What to ship next",
    desc: "Stack-rank ideas against impact and cost.",
    target: "ondemand",
    prompt:
      "Given what we know in product memory, what should we ship next? Stack-rank a few options with impact, cost, and dependencies.",
  },
]

export interface PastFindingRow {
  title: string
  status: string
  sub: string
  positive?: boolean
}

export interface PastWeekRow {
  date: string
  label: string
  findings: PastFindingRow[]
}

export interface ShippedItemRow {
  title: string
  date: string
  mrr: string | null
  metric: string | null
  tickets: string | null
}

export interface ShippedState {
  stats: Array<{ value: string; label: string; valueClass?: "pos" }>
  primary: ShippedItemRow[]
  supporting: ShippedItemRow[]
}

/** One completed (or in-flight) Q&A for Ask Sprntly; used to restore the thread when a sidebar row is clicked. */
export interface ConversationSavedTurn {
  id: string
  query: string
  reply?: AskResponse
  error?: string
}

export interface ConversationRow {
  id: string
  title: string
  time: string
  savedTurn?: ConversationSavedTurn | null
}

export interface TeamMemberRow {
  id: string
  name: string
  email: string
  initials: string
  role: "Admin" | "Viewer"
  color?: string
  isSelf?: boolean
}

export interface TeamPendingRow {
  email: string
  role: string
  sent: string
}

export interface ConnectorItemRow {
  id: string
  logo: string
  name: string
}

export interface ConnectorCategoryRow {
  key: string
  title: string
  /** Shown under the title (e.g. in connectors management UI) */
  subtitle?: string
  /** Reserved for future API-driven icon keys (UI uses SVG placeholders). */
  icon?: string
  items: ConnectorItemRow[]
}

export interface DetailQuoteRow {
  source: string
  quote: string
  meta: string[]
  badge?: string
}

export interface DetailEvidenceSection {
  sectionTitle: string
  quoteRows?: DetailQuoteRow[]
  /** Trusted HTML (e.g. chart SVG) from your server-side renderer */
  html?: string | null
  /** Inline chart specs rendered via InlineChart. Used for the data-science
   *  slicing infographics on the Evidence section. */
  charts?: Array<{
    kind: PrdChartKind
    title?: string
    subtitle?: string
    data: PrdChartDatum[]
  }>
}

export interface DetailState {
  backLabel: string
  tags: Array<{ label: string; className: string }>
  title: string
  summary: string
  metrics: Array<{
    label: string
    value: string
    note?: string
    valueClass?: "pos" | "neg"
  }>
  evidenceSections: DetailEvidenceSection[]
  cta?: {
    headline: string
    sub: string
    dismissLabel: string
    primaryLabel: string
  } | null
  /** Source-of-truth pointer — used by 'Generate PRD' to tell the backend
   * which brief insight to PRD-ify. */
  meta?: {
    briefId: number
    insightIndex: number
  }
}

export type PrdChartKind = "bar" | "line" | "pie" | "donut" | "stat" | "gauge"

export type PrdChartDatum = { label: string; value: number | string }

export type PrdSection =
  | { type: "h2"; text: string }
  | { type: "p"; text: string }
  | { type: "ul"; items: string[] }
  | { type: "table"; headers: string[]; rows: string[][] }
  | {
      type: "chart"
      kind: PrdChartKind
      title?: string
      subtitle?: string
      data: PrdChartDatum[]
    }

export interface PrdState {
  metaLine: string
  title: string
  /** Plain sections; render as paragraphs / lists / tables / charts client-side */
  sections: PrdSection[]
}

export interface AppContentState {
  userName: string | null
  userEmail: string | null
  userInitials: string | null
  homeHeadline: string | null
  homeSub: string | null
  homeStarterCards: ChatHomeCard[]
  brief: BriefState
  pastWeeks: PastWeekRow[]
  shipped: ShippedState
  conversations: ConversationRow[]
  ondemandStarters: ChatHomeCard[]
  detail: DetailState | null
  /** Pre-built drill-down state per finding, indexed by `BriefFindingRow.detailKey`. */
  briefDetails: Record<string, DetailState>
  prd: PrdState | null
  /** Generated Evidence Page doc — same PrdState shape (markdown sections
   *  with tables and `chart` blocks) so it can reuse the markdown adapter. */
  evidence: PrdState | null
  teamMembers: TeamMemberRow[]
  teamPending: TeamPendingRow[]
  connectorCategories: ConnectorCategoryRow[]
  connectedConnectorIds: string[]
  /** `null` = hide count badge */
  sidebarBriefCount: number | null
  sidebarConvCount: number | null
  /** Override default AI chips per screen id; empty array = no chips */
  aiScreenChips: Partial<Record<string, string[]>>
}

export function isBriefEmpty(b: BriefState): boolean {
  return (
    b.sections.length === 0 || b.sections.every((s) => s.findings.length === 0)
  )
}
