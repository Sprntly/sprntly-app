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
  /** Template “Brief header” row — derived from company + insights until the API adds fields. */
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
  /**
   * Single-letter glyph rendered in the connector logo box (sprntly_Design-3).
   * For example, "M" for Mixpanel. The legacy `logo` field stays for
   * back-compat with the dormant ConnectorsScreen.tsx.
   */
  logoText?: string
  /** Hex brand color for the logo box background (e.g. "#7856FF"). */
  logoColor?: string
  /** True if a working OAuth backend exists for this connector. */
  oauth?: boolean
  /**
   * Connector auth model. Defaults to "oauth" when unset so the existing
   * catalog rows (which use `oauth: true|false`) don't need a churn.
   * Use "apikey" for providers (e.g. Fireflies) whose primary auth path
   * is a user-issued API key pasted into a modal — no OAuth redirect.
   */
  authType?: "oauth" | "apikey"
}

export interface ConnectorCategoryRow {
  key: string
  title: string
  /** Longer prose descriptor (legacy field used by dormant ConnectorsScreen). */
  subtitle?: string
  /**
   * Short badge-style label shown to the right of the category title in
   * sprntly_Design-3 (e.g. "required", "powers On-Call Agent"). Distinct
   * from `subtitle`.
   */
  subLabel?: string
  /** Reserved for future API-driven icon keys (UI uses SVG placeholders). */
  icon?: string
  items: ConnectorItemRow[]
  /**
   * Human-readable accepted-types hint shown in the per-category upload
   * strip (sprntly_Design-3), e.g. "PDF · CSV · XLSX".
   */
  uploadAccept?: string
  /**
   * Machine-readable accepted extensions for the upload `<input accept="">`
   * attribute, e.g. [".pdf", ".csv", ".xlsx"].
   */
  uploadExtensions?: string[]
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

/** Evidence semantic-block section variants. Naming kept as `v2-*` for
 *  historical reasons; these are the canonical evidence block types (no
 *  v1 exists). The renderer (`EvidenceSections`) dispatches each variant
 *  to a dedicated subcomponent. */
export type EvidenceV2Tone = "negative" | "neutral" | "positive"
export type EvidenceV2Confidence = "High" | "Medium" | "Low"

export interface EvidenceV2HeroCard {
  label: string
  value: string
  delta?: string
  baseline?: string
  tone: EvidenceV2Tone
}

export interface EvidenceV2CutsIndexRow {
  n: number
  headline: string
  confidence: EvidenceV2Confidence
}

export interface EvidenceV2SourceChip {
  kind: "tool" | "period" | "sample" | "confidence" | string
  label: string
}

/** PRD semantic-block section variants — additive on PrdSection. The
 *  renderer (`PrdSections`) dispatches each `prd-*` variant to a
 *  dedicated subcomponent. `v2-context-chip` is deliberately shared with
 *  the evidence renderer so a single component handles both formats. */
export interface PrdProblemImpactCell {
  label: string
  value: string
  tone?: EvidenceV2Tone
}

export interface PrdMetricPoint {
  name: string
  current: string
  target: string
}

export interface PrdGuardrail {
  name: string
  baseline: string
  bound: string
}

export type PrdRequirementCategory =
  | "functional"
  | "flag"
  | "config"
  | "telemetry"
  | string

export interface PrdRequirementRow {
  behavior: string
  category: PrdRequirementCategory
  detail: string
}

export interface PrdAcceptanceCriterionRow {
  id: string
  kind: string
  givenWhenThen: string
  verifiedBy: string
}

export type PrdRiskSeverity = "high" | "medium" | "low" | string

export interface PrdRiskRow {
  risk: string
  severity: PrdRiskSeverity
  mitigation: string
}

export interface PrdMilestonePhase {
  phase: string
  items: string[]
}

/** F1 Design section. Both hint fields are optional — an empty `:::design`
 *  block still renders the prototype entry point; the hints feed later
 *  prototype generation (P1-05), not the P1 renderer. */
export type PrdDesignBlock = {
  type: "prd-design"
  platformHint?: "desktop" | "mobile" | "both"
  notes?: string
}

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
  // ---- Evidence variants ----
  | { type: "v2-hero"; cards: EvidenceV2HeroCard[] }
  | { type: "v2-context-chip"; text: string }
  | { type: "v2-cuts-index"; rows: EvidenceV2CutsIndexRow[] }
  | { type: "v2-source"; chips: EvidenceV2SourceChip[] }
  | { type: "v2-rules-callout"; supports: string; rulesOut: string }
  | { type: "v2-quote"; body: string; channel: string; context?: string }
  | { type: "v2-forecast-omitted"; reason: string }
  // ---- PRD variants ----
  | { type: "prd-tldr"; problem: string; fix: string; impact: string }
  | {
      type: "prd-problem"
      userStory: string
      impact: PrdProblemImpactCell[]
    }
  | {
      type: "prd-hypothesis"
      ifWe: string
      thenMetric: PrdMetricPoint
      because: string
      secondary?: string
    }
  | { type: "prd-requirements"; rows: PrdRequirementRow[] }
  | {
      type: "prd-acceptance-criteria"
      rows: PrdAcceptanceCriterionRow[]
    }
  | {
      type: "prd-metrics"
      primary: PrdMetricPoint
      secondary: PrdMetricPoint[]
      guardrails: PrdGuardrail[]
    }
  | { type: "prd-risks"; rows: PrdRiskRow[] }
  | { type: "prd-milestones"; phases: PrdMilestonePhase[] }
  | { type: "prd-dod"; items: string[] }
  | PrdDesignBlock

/**
 * The shared document-content shape: a title, a meta line and the parsed
 * semantic sections. Both PRDs (`PrdState`) and Evidence docs reuse it via
 * the markdown adapters. Extracted from `PrdState` so PRD-only identifiers
 * (`prd_id`) can be required on PRDs without forcing Evidence docs — which
 * carry an `evidence_id`, never a `prd_id` — to invent one.
 */
export interface PrdContent {
  metaLine: string
  title: string
  /** Plain sections; render as paragraphs / lists / tables / charts client-side */
  sections: PrdSection[]
}

/**
 * A loaded PRD document. Extends the shared `PrdContent` shape with the PRD's
 * DB id. `prd_id` is required: once a `PrdState` exists it represents a real
 * PRD row, and the F2 "Generate Prototype" flow needs the id to call
 * `designAgentApi.generate({ prd_id })`.
 */
export interface PrdState extends PrdContent {
  /** DB id of the loaded PRD (`PrdRecord.id`). Always present once a PRD is loaded. */
  prd_id: number
  /** Figma file key when the PRD has a connected Figma source; undefined/null when none. */
  figma_file_key?: string | null
}

export interface AppContentState {
  userName: string | null
  userEmail: string | null
  userInitials: string | null
  homeHeadline: string | null
  homeSub: string | null
  homeStarterCards: ChatHomeCard[]
  brief: BriefState
  /** Brief v2 render state — narrative-shaped (hero + supporting findings,
   *  KPI strip, convergence chips). Hydrated alongside `brief` so toggling
   *  formats on BriefScreen doesn't require a second fetch. `null` until
   *  the first brief load completes. */
  briefV2: import("../lib/brief-v2-adapter").BriefV2State | null
  pastWeeks: PastWeekRow[]
  shipped: ShippedState
  conversations: ConversationRow[]
  ondemandStarters: ChatHomeCard[]
  detail: DetailState | null
  /** Pre-built drill-down state per finding, indexed by `BriefFindingRow.detailKey`. */
  briefDetails: Record<string, DetailState>
  prd: PrdState | null
  /** Pointer to the brief insight that produced `prd`, kept around so
   *  PrdScreen can refetch / regenerate against the same source.
   *  Populated by DetailScreen.handleGeneratePrd alongside `prd`. */
  prdMeta: { briefId: number; insightIndex: number } | null
  /** Generated Evidence Page doc — shares the `PrdContent` base shape (markdown
   *  sections with tables and `chart` blocks) so it can reuse the markdown
   *  adapter. Evidence carries its own `evidence_id` on the wire and never a
   *  `prd_id`, so it is typed `PrdContent`, not `PrdState`. */
  evidence: PrdContent | null
  /** True while evidence is being generated from the chat flow (ChatScreen),
   *  so ContentPanel's EvidenceTab can show a loading state even when
   *  content.detail is null. */
  evidenceGenerating: boolean
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
