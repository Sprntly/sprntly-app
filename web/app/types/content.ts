/** Serializable app payload — hydrate from API / LLM via `setContent`. */

import type { AskResponse } from "../lib/api"

export type BriefTagType = "double" | "new" | "fix"

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

export interface ChatHomeCard {
  id: string
  icon: string
  title: string
  desc: string
  target: "brief" | "ondemand"
  prompt?: string
}

/** Home landing: go-to destinations plus a few prefilled prompts (brief uses AI bar; Ask uses `pendingOndemandDraft`). */
export const DEFAULT_HOME_STARTER_CARDS: ChatHomeCard[] = [
  {
    id: "home-goto-brief",
    icon: "✦",
    title: "This week's brief",
    desc: "Ranked findings, impact, and signals in one view.",
    target: "brief",
  },
  {
    id: "home-goto-ask",
    icon: "💬",
    title: "Ask Sprntly",
    desc: "Free-form Q&A across your product memory.",
    target: "ondemand",
  },
  {
    id: "home-prompt-ask",
    icon: "📈",
    title: "Compare our segments",
    desc: "Opens Ask with a retention question ready to send.",
    target: "ondemand",
    prompt:
      "Compare retention across our top three customer segments — what differs, what might explain it, and what we should validate next.",
  },
  {
    id: "home-prompt-brief",
    icon: "◇",
    title: "Challenge the ranking",
    desc: "Opens the brief with a question in the side assistant.",
    target: "brief",
    prompt:
      "Why is the #1 finding ranked higher than #2? What evidence supports that ordering?",
  },
]

/** Curated Ask Sprntly landing chips until org-specific starters load from the API. */
export const DEFAULT_ONDEMAND_STARTERS: ChatHomeCard[] = [
  {
    id: "od-default-q3",
    icon: "◇",
    title: "Q3 strategy",
    desc: "Turn product memory into priorities, bets, and risks.",
    target: "ondemand",
    prompt:
      "Generate a Q3 strategy from our product memory — priorities, bets, measurable goals, and the main risks to watch.",
  },
  {
    id: "od-default-prd",
    icon: "📄",
    title: "PRD for team folders",
    desc: "Draft scope, rollout, and open questions.",
    target: "ondemand",
    prompt:
      "Draft a PRD for team folder permissions: problem, users, requirements, rollout plan, metrics, and open questions for eng and design.",
  },
  {
    id: "od-default-retention",
    icon: "📈",
    title: "Retention comparison",
    desc: "Compare segments or cohorts we care about.",
    target: "ondemand",
    prompt:
      "Compare retention across our top three customer segments — what differs, what might explain it, and what we should validate next.",
  },
  {
    id: "od-default-ship",
    icon: "🚀",
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
  /** Single character / emoji for the group header tile */
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

export interface PrdState {
  metaLine: string
  title: string
  /** Plain sections; render as paragraphs / lists client-side */
  sections: Array<{ type: "h2" | "p" | "ul"; text?: string; items?: string[] }>
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
