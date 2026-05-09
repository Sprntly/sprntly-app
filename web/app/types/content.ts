/** Serializable app payload — hydrate from API / LLM via `setContent`. */

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

export interface ConversationRow {
  id: string
  title: string
  time: string
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
