/** Serializable app payload — hydrate from API / LLM via `setContent`. */

import type { AskResponse, GeneratedStory, ReportSummary, TicketStub } from "../lib/api"

export type BriefTagType = "double" | "new" | "fix"

/** Top Insights template action accent (maps from API insight tags in the adapter). */
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
  /** Top-insights skill taxonomy: the finding type (one of the 8), its accent
   *  hex (derived from the type — not the model's mismatchable accent), and the
   *  type-name pill label (no P0/P1). Drives the card accent bar + category pill
   *  in the skill design. */
  skillType: string
  skillAccent: string
  skillLabel: string
  /** Skill card CTAs (View/Draft PRD, View/Generate prototype); empty for
   *  legacy briefs that predate the skill card — callers fall back. */
  ctas: Array<{ label: string; style: string }>

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
    title: "Show me this week's top insights",
    desc: "Ranked findings, impact, and signals in one view.",
    target: "brief",
  },
  {
    id: "home-prompt-customer-feedback",
    icon: "diamond",
    title: "Give me summary on last week's customer conversations",
    desc: "Fills Ask so you can edit or send.",
    target: "ondemand",
    prompt: "Give me summary on last week's customer conversations.",
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
    desc: "Draft scope, risks, and open questions.",
    target: "ondemand",
    prompt:
      "Draft a PRD for team folder permissions: problem, users, requirements, risks, and the input needed from eng and design.",
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
  /** The ChatScreen tab this rail entry mirrors — exactly ONE entry per tab,
   *  updated in place as the room's chat continues (never one per message). */
  _tabId?: string
  /** The Supabase conversation id, once persisted (tagged by ChatScreen). */
  _dbId?: number
  /** The PRD this conversation is about, when it was opened from a PRD tab (else
   *  null). Carried from `ConversationRecord.prd_id` so resuming a PRD chat from
   *  history can re-bind the tab to its PRD and reopen the content panel. */
  prd_id?: number | null
  /** The project this conversation is bound to (else null). Carried from
   *  `ConversationRecord.project_id` so resuming a project-bound chat from
   *  history restores the project-menu affordance in the content panel. */
  project_id?: number | null
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

/**
 * What a connector IS: drives feature availability across the app — e.g. the
 * ticket sync offers connected `task-management` connectors — instead of
 * features hardcoding provider ids. Mirrors the backend authority
 * (backend/app/connectors/catalog.py). Connectors may carry multiple types
 * with product sign-off per entry (2026-07-30) — Slack is the first
 * (communication + customer-voice); a multi-type connector renders a card in
 * every catalog category it belongs to.
 */
export type ConnectorType =
  | "task-management"
  | "communication"
  | "documents"
  | "customer-voice"
  | "meetings"
  | "analytics"
  | "revenue"
  | "crm"
  | "code"
  | "monitoring"
  | "design"
  // User research — interview/usability repositories (Marvin) and the research
  // artifacts a team uploads by hand. Distinct from "customer-voice" (inbound,
  // unsolicited) and "meetings" (sales/CSM calls): research is deliberately
  // gathered evidence about users. Mirrors backend catalog.py RESEARCH.
  | "research"

export interface ConnectorItemRow {
  id: string
  logo: string
  name: string
  /** The connector's types (e.g. Slack → ["communication", "customer-voice"]). */
  types?: ConnectorType[]
  /**
   * Single-letter glyph rendered in the connector logo box (sprntly_Design-3).
   * For example, "M" for Mixpanel. The legacy `logo` field stays for
   * back-compat with the dormant ConnectorsScreen.tsx.
   */
  logoText?: string
  /** Hex brand color for the logo box background (e.g. "#7856FF"). */
  logoColor?: string
  /**
   * Path to the connector's real full-color brand logo, bundled locally
   * under `web/public/connectors/<id>.svg` (e.g. "/connectors/slack.svg").
   * When set, the connector renders its actual logo on a white tile; if the
   * image fails to load the UI falls back to the single-letter `logoText`
   * glyph. Bundling the SVG locally keeps logos pixel-perfect at any size and
   * drops the runtime favicon fetch the catalog used previously.
   */
  logoSvg?: string
  /** True if a working OAuth backend exists for this connector. */
  oauth?: boolean
  /**
   * Connector auth model. Defaults to "oauth" when unset so the existing
   * catalog rows (which use `oauth: true|false`) don't need a churn.
   * Use "apikey" for providers (e.g. Fireflies) whose primary auth path
   * is a user-issued API key pasted into a modal — no OAuth redirect.
   * Use "credentials" for self-hosted tools (e.g. Superset) connected
   * with an instance URL + username + password form.
   * Use "upload" for the one connector with no third party behind it
   * (`uploads` — the user's own documents): the connect gesture is naming
   * a source and uploading files, so Connect opens the upload modal.
   */
  authType?: "oauth" | "apikey" | "credentials" | "upload"
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
  /**
   * Whether the Settings → Connectors card for this category shows its manual
   * "Upload <category> export" dropzone. Defaults to TRUE when unset — only
   * categories that explicitly set `false` hide the strip.
   *
   * Turned off for categories whose data should only ever arrive through the
   * real integration (Codebase, Project Management): a hand-uploaded
   * GitHub/Jira export has no sync, no permissions model, and no incremental
   * updates, so it produces stale, misleading sources. Company documentation
   * also opts out, for a different reason: it takes uploads through its own
   * named-source picker rather than the generic per-category strip.
   * The backend upload path is untouched — flip this back to `true` (or drop
   * the field) to restore the dropzone.
   */
  allowsManualUpload?: boolean
  /**
   * Keep this category visible in `connectableCatalog()` even when none of its
   * connectors is wired yet. Defaults to FALSE: normally an all-"Coming soon"
   * category is dropped so we never show a shelf the user can't act on.
   *
   * Set only for Research, where the manual upload strip — not the connector
   * grid — is the feature: Marvin is still coming-soon, so without this the
   * whole shelf (and the only way to hand us research) would vanish from
   * Settings AND the onboarding wizard. A category setting this must therefore
   * allow manual upload; a shelf with neither connectors nor uploads is empty.
   */
  keepWhenEmpty?: boolean
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

/** One generated QA test scenario row (from a `:::qa-scenarios` block). The
 *  JSON keys map directly (given/when/then/traces/risk/group/title/id). */
export interface QaScenarioRow {
  id: string
  group: "happy" | "edge" | "failure" | ""
  title: string
  given: string
  when: string
  then: string
  traces: string
  risk: "high" | "medium" | "low" | ""
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
  | { type: "qa-scenarios"; rows: QaScenarioRow[]; openQuestions: string[] }
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
  /**
   * Self-contained HTML escape hatch. When set, the document is NOT `:::block`
   * markdown but a complete HTML document (the v3 evidence-brief visual brief);
   * the renderer shows it in a sandboxed iframe and ignores `sections`. Empty
   * for `:::block` PRDs/evidence.
   */
  html?: string
  /** The originating chat question (`EvidenceRecord.question`) — set only for
   *  a chat-task Evidence doc; null/undefined otherwise (brief-insight docs,
   *  and any doc generated before this column existed). Mirrors
   *  `PrdState.question`; lives here (not on `PrdState`) so Evidence — which
   *  has no `PrdState` of its own — carries it too. */
  question?: string | null
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
  /** Opaque, unguessable external identifier (`PrdRecord.public_id`) —
   *  what `useArtifactUrlSync` reflects onto the `?prd=` URL instead of the
   *  sequential `prd_id`, so a copied/bookmarked link never discloses a
   *  blind-enumerable id. Optional: absent on any PrdState built before this
   *  field existed (none currently — every load path sets it — kept
   *  optional so a future load path that forgets it fails soft, not a type
   *  error blocking an unrelated build). */
  public_id?: string
  /** Figma file key when the PRD has a connected Figma source; undefined/null when none. */
  figma_file_key?: string | null
  /** Part B — the implementation-spec markdown (`PrdRecord.llm_part`). Rendered
   *  faithfully in the LLM-readable view. Undefined/empty when Part B wasn't
   *  generated or failed. */
  llmPart?: string
  /** The brief insight this PRD was generated from (`PrdRecord.brief_id` /
   *  `insight_index`). Carried on PrdState so EVERY load path (latest,
   *  open-generation, and the brief card's "View PRD" via loadPrdById) lets the
   *  panel fetch the matching QA test-scenarios doc. */
  briefId?: number
  insightIndex?: number
  /** How this PRD was created (`PrdRecord.source`). Only `'brief'` PRDs carry
   *  their own research Evidence; `'ideation'`, `'upload'` and `'chat'` PRDs
   *  have none, so the right-panel Evidence tab is hidden for them. Absent on
   *  legacy rows — treat missing as `'brief'` (show the tab). */
  source?: "brief" | "ideation" | "backlog" | "upload" | "chat"
  /** When this PRD was written (`PrdRecord.generated_at`). Used to order it
   *  against the thread's other artifacts — the tab strip's reopen button opens
   *  whichever was created LAST. Absent on paths that build a PrdState without a
   *  record behind it (a streaming draft), which read as "no timestamp" rather
   *  than as oldest. */
  generatedAt?: string
  /** Canonical artifact-share token for this PRD's Share link; read, never
   *  minted on open. Absent-safe. */
  shareToken?: string | null
  /** WHICH uploaded format wrote this PRD (`PrdRecord.artifact_template_id`).
   *  null = Sprntly's built-in; UNDEFINED = the load path predates the field —
   *  the panel's Format control hydrates it with one GET before rendering a
   *  label, so absence degrades to a fetch, never to a wrong name. */
  artifactTemplateId?: string | null
  /** That format's name (`PrdRecord.artifact_template_name`, resolved
   *  server-side). null when built-in or the format was deleted. */
  artifactTemplateName?: string | null
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
   *  formats on the brief surface doesn't require a second fetch. `null` until
   *  the first brief load completes. */
  briefV2: import("../lib/brief-v2-adapter").BriefV2State | null
  /** Coarse lifecycle of the current brief load, mirrored from
   *  `useBriefHydration` (called once in AppShell) so the brief surface can
   *  render a "generating…" WIP indicator without re-invoking the
   *  side-effectful hydration hook. `null` until the first hydration tick. */
  briefHydration: "idle" | "loading" | "ready" | "generating" | "failed" | "empty" | null
  /** A fresh brief is being built *over* the currently-cached one (e.g. after a
   *  connector was added and the workspace is regenerating). Mirrored from
   *  `useBriefHydration`; drives the lightweight "refreshing your brief" banner
   *  shown above the existing brief. `briefHydration` stays "ready" meanwhile. */
  briefRegenerating: boolean
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
  /** True while a PRD is being generated from any chat / card / composer flow,
   *  so ContentPanel's PrdPanelContent can show a generating spinner in the
   *  right rail even before `content.prd` is populated. Mirrors
   *  `evidenceGenerating`. Every "Generate/Create PRD" path opens the rail
   *  immediately and flips this on, so the PRD always surfaces on the right —
   *  never only as a bottom chat message. */
  prdGenerating: boolean
  /** Live streaming preview: the accumulating Part A HTML forwarded from the
   *  in-flight PRD generation's SSE stream (already throttled inside
   *  runPrdGeneration). Rendered by PrdPanelContent while `prdGenerating` and
   *  no `prd` has landed yet; every generation start resets it to null so a
   *  previous run's preview can never bleed into a new one. */
  prdPartialHtml: string | null
  /** Generated Evidence Page doc — shares the `PrdContent` base shape (markdown
   *  sections with tables and `chart` blocks) so it can reuse the markdown
   *  adapter. Evidence carries its own `evidence_id` on the wire and never a
   *  `prd_id`, so it is typed `PrdContent`, not `PrdState`. */
  evidence: PrdContent | null
  /** The `evidences` row id behind `content.evidence`, when the setter knows
   *  it (the Artifacts library's explicit open-by-id, and the `?evidence=`
   *  URL deep link). Used ONLY to reflect the artifact-link URL param back
   *  onto the address bar while the Evidence tab is showing this doc — NOT
   *  populated by every path that sets `evidence` (the brief/insight
   *  generate-or-resolve flows in ChatScreen/ContentPanel do not thread an id
   *  through), so a null here while `evidence` is set just means the URL
   *  won't carry `?evidence=` for that particular open — never an error. */
  evidenceId: number | null
  /** True while evidence is being generated from the chat flow (ChatScreen),
   *  so ContentPanel's EvidenceTab can show a loading state even when
   *  content.detail is null. */
  evidenceGenerating: boolean
  /** Live streaming preview: the accumulating evidence HTML forwarded from the
   *  in-flight generation's SSE stream (already throttled inside
   *  runEvidenceGeneration). Rendered by the Evidence tab while loading and no
   *  `evidence` has landed yet; every generation start resets it to null so a
   *  previous run's preview can never bleed into a new one. Mirrors
   *  `prdPartialHtml`. */
  evidencePartialHtml: string | null
  /** The active chat tab's conversation id, mirrored here by ChatScreen so the
   *  content panel knows which THREAD it is showing. The Reports tab lists this
   *  conversation's captured reports; null (a brand-new chat with nothing
   *  persisted yet, or the brief tab) means there is no thread to list. */
  conversationId: number | null
  /** The project a main-chat-generated PRD silently forked into (server-side
   *  `maybe_auto_create_project_for_prd`, returned on the generate response).
   *  Non-null ONLY while the current thread is bound to a project — it gates the
   *  project-menu affordance in the content panel header. Null (the normal state
   *  for every non-project chat) leaves the panel byte-identical to before. Cleared
   *  on thread-switch / new-chat alongside the other thread-scoped fields. */
  activeProjectId: number | null
  /** A main-chat composer draft carried across the seamless PRD-create →
   *  project auto-nav (client decision D1, 2026-09-02): the main-chat
   *  conversation effectively becomes the project's private chat, so
   *  whatever the user had half-typed moves with them instead of being lost
   *  on the page transition. Set (and the main-chat composer cleared) by
   *  `ChatScreen`'s `bindActiveProject` right before it navigates; consumed
   *  exactly once — read into the project chat's composer and cleared back
   *  to null — by `useProjectConversation` on mount. Null in every other
   *  case (nothing to hand off). */
  pendingComposerDraft: string | null
  /** A specific report to open in the Reports tab, set when the user arrived by
   *  clicking that exact document (e.g. an Artifacts row). The tab consumes it
   *  once — selecting the report and clearing this — so the user lands on what
   *  they clicked instead of a list they must search. */
  reportFocusId: number | null
  /** True when `reportFocusId` points at a report that has NO thread behind it —
   *  the Artifacts row for a report whose chat was deleted, which reads in the
   *  panel without a list under it.
   *
   *  Stated rather than inferred from `conversationId == null`, because a
   *  brand-new chat tab also has a null conversation id (a tab has none until its
   *  first ask persists). Reading that null as "standalone" is what used to
   *  render the PREVIOUS thread's document inside an empty new chat. */
  reportFocusStandalone: boolean
  /** A report is being WRITTEN for this thread right now.
   *
   *  A report is an artifact, so it generates where artifacts generate — in the
   *  panel, the same posture a PRD build takes (`prdGenerating`) — not as text
   *  scrolling through the chat it is about to appear beside. Set at send time
   *  from the intent envelope's `report` flag, which is the planner's own
   *  pipeline pick, and cleared when the answer settles (or fails, or is
   *  stopped) whatever it turned out to be: a report pipeline can still degrade
   *  to an apology, and a panel left generating over one would never resolve. */
  reportGenerating: boolean
  /** The report text as it streams, or null before the first delta.
   *
   *  Same role `prdPartialHtml` plays for a PRD: it is a PREVIEW, replaced by
   *  the captured document the moment the row exists. Markdown, because that is
   *  what every report pipeline answers in. */
  reportPartialMd: string | null
  /** The team document (custom artifact) open in the panel's Document tab, or
   *  null when this thread has none — which is the normal state, and what
   *  keeps the tab hidden. Set by the chat's `create_artifact` dispatch. */
  documentId: number | null
  /** True from the moment a document is requested until its first poll shows
   *  it ready. Purely presentational: the row's own `status` is the truth, and
   *  this only avoids a flash of "empty document" before the first fetch. */
  documentGenerating: boolean
  /** The Goal Analysis run open in the panel, or null when this thread has
   *  none — the normal state, and what keeps the tab hidden. */
  goalRunId: number | null
  /** The active thread's captured reports, newest first. Owned by
   *  `useThreadReportsSync` (called once in AppShell) and read by both the panel
   *  and ChatScreen — see that hook for why there is exactly one fetcher. */
  threadReports: ReportSummary[]
  /** The conversation `threadReports` was fetched FOR.
   *
   *  The list lives in shared content but the panel is global, so "which thread
   *  do these rows describe" cannot be inferred from the fact that they exist.
   *  React flushes ChatScreen's (child) effects before AppShell's (parent) ones,
   *  so on the commit where the active tab changes, the list is still the
   *  PREVIOUS thread's — which is how a brand-new chat came to auto-open the
   *  panel on another thread's report.
   *
   *  Every reader compares this against `conversationId` and treats a mismatch as
   *  "this thread's list hasn't landed yet", never as "this thread has none".
   *  Null = no thread in scope. */
  threadReportsConversationId: number | null
  /** Lifecycle of `threadReports`, because an empty list means different things:
   *   idle    — no thread in scope (nothing was ever asked for)
   *   loading — in flight; empty is "not yet", not "none"
   *   ready   — authoritative; empty genuinely means this chat has no reports
   *   error   — the fetch failed; empty says nothing at all
   *  The Reports tab hides only on a KNOWN-empty thread, so a failed load never
   *  makes the tab vanish. */
  threadReportsStatus: "idle" | "loading" | "ready" | "error"
  /** Bumped when an answer that IS a report lands, so the one fetcher above
   *  re-reads the thread's list.
   *
   *  Capture happens SERVER-side after the answer completes (`report_capture`),
   *  so the list fetched when the thread opened is one report short of the truth
   *  the moment the user watches one arrive — and nothing else in this content
   *  changes to say so. Any changing value works; the writer stamps a
   *  timestamp rather than reading a counter back out. */
  reportsRefreshKey?: number
  teamMembers: TeamMemberRow[]
  teamPending: TeamPendingRow[]
  connectorCategories: ConnectorCategoryRow[]
  connectedConnectorIds: string[]
  /** Whether `connectedConnectorIds` has actually been answered by the backend
   *  yet. It starts `[]`, which is indistinguishable from "this workspace has
   *  no connectors" — and the Top Insights surface turns that into a dead-end
   *  "connect a source" page. Surfaces that branch on the connector list must
   *  wait for this flag instead of reading the default. Set (to `true`) by
   *  AppShell on both success and failure of the connectors fetch, and reset to
   *  `false` on a workspace switch. */
  connectorsHydrated: boolean
  /** The workspace's Top Insights filter (companies.notification_settings.
   *  brief_insight_types), loaded once by AppShell. The Top Insights tab shows
   *  the findings whose types intersect it; empty/absent = surface everything
   *  (no filter). Optional so the default content state and existing fixtures
   *  need no change. */
  insightTypeFilter?: string[]
  /** `null` = hide count badge */
  sidebarBriefCount: number | null
  sidebarConvCount: number | null
  /** Override default AI chips per screen id; empty array = no chips */
  aiScreenChips: Partial<Record<string, string[]>>
  /** A guest session's pre-fetched ticket set (GuestArtifactViewer populates
   *  this directly from the artifact-share content endpoint) — the Tickets
   *  tab renders these instead of calling storiesApi when useGuestSession()
   *  is non-null. `null`/absent for every non-guest render. */
  guestTickets?: GeneratedStory[] | null
  /** The STANDALONE ticket set on screen — tickets generated from a chat with
   *  no PRD behind them (`ticket_sets`, backend commit 0edeea35).
   *
   *  Owned end to end by `lib/runTicketSetGeneration.ts`: it creates this slice
   *  at kick-off, republishes it as fan-out batches land, and writes the
   *  terminal value. The Tickets tab READS it and never polls — that is what
   *  makes a second, cost-incurring generation of the same set structurally
   *  impossible (the same guard the guest-tickets branch relies on).
   *
   *  Non-null here also means "the Tickets tab is showing a set, not a PRD's
   *  tickets": `prdInScopeFor` returns null for the tickets tab while it is
   *  set, so no PRD-acting control (Share, PDF, the prototype CTA) is left
   *  armed on a document that did not produce what is on screen. */
  ticketSet?: {
    /** `ticket_sets.id` — the tracker-sync scope (`set-{id}-{story_id}` keys). */
    id: number
    /** The set's LLM-derived name. May be `""` before the naming leg lands or
     *  when it never ran; the panel renders its own fallback line rather than
     *  collapsing the header. */
    title: string
    stories: GeneratedStory[]
    /** The chat this set was born in, or null when it was born outside one.
     *  NOT a "was the thread deleted" signal — see `ticketSetStandalone`. */
    conversationId: number | null
    /** `generating` | `ready` | `failed`, straight off the row. */
    status: string
    /** The request the set was generated from (`ticket_sets.source_text`) —
     *  what the in-panel "Try again" re-runs. */
    sourceText?: string
    /** Fan-out plan roster, while generating: planned-but-not-yet-written
     *  tickets, rendered as skeleton rows. Cleared on the terminal write. */
    stubs?: TicketStub[]
    /** Fan-out batch counter while generating, for the streaming banner. */
    progress?: { done: number; total: number } | null
    /** Why the run ended badly, as a CLASSIFIED KIND — never the raw backend
     *  message. The panel maps the kind to its own copy, so a stack trace or a
     *  provider error string can never reach the screen. */
    error?: TicketSetFailureKind | null
    /** Which uploaded TICKET format rendered this set (null = Sprntly's
     *  built-in), with its server-resolved display name — what the Tickets
     *  footer's "Format: {name}" label and switch control read. Undefined on
     *  slices written mid-run (the stamp lands with the terminal read). */
    artifactTemplateId?: string | null
    artifactTemplateName?: string | null
    /** A background format switch is running over this set. Distinct from
     *  `status`, which stays `ready` throughout — the tickets on screen are
     *  the previous format's and remain readable, pushable and editable while
     *  the re-lay runs. The panel shows a working strip over them and the
     *  Format control withholds a second switch until it clears. */
    relaying?: boolean
    /** The format being switched INTO, for the strip's copy. Null when the
     *  target is Sprntly's built-in layout. */
    relayingIntoName?: string | null
  } | null
  /** True from kick-off until the run reaches a terminal state. Distinct from
   *  `ticketSet.status`: the slice may not exist at all yet (the create call is
   *  still in flight), and the panel still owes the user a working state. */
  ticketSetGenerating?: boolean
  /** True when the ticket set on screen has NO chat behind it — the Artifacts
   *  row for a set whose thread was deleted.
   *
   *  Stated rather than inferred from `ticketSet.conversationId == null`, for
   *  exactly the reason documented on `reportFocusStandalone` above: a
   *  brand-new chat tab also has a null conversation id, and reading that null
   *  as "standalone" is a bug this codebase has already shipped once. */
  ticketSetStandalone?: boolean
  /** Bumped (any new value) to make the Tickets tab RE-READ a PRD's persisted
   *  tickets — the in-place format switch persists re-laid stories server-side
   *  and the tab's cache-first effect would otherwise keep rendering the copy
   *  it already loaded. A re-read, never a regeneration: the switch leaves
   *  `content_hash` untouched, so the re-run serves the fresh cache with no
   *  LLM call. Standalone sets don't need it (their slice IS the data). */
  ticketsRefreshNonce?: number
}

/** How a standalone ticket-set run ended badly. A KIND, not a message: the
 *  panel owns the words, so nothing a backend or a fetch failure produced is
 *  ever printed to the user.
 *
 *   timeout  — the poll budget ran out with the run still going
 *   network  — the browser lost the backend mid-poll
 *   notfound — the set is gone (404); no existence/access language either way
 *   failed   — the run itself reported failure
 */
export type TicketSetFailureKind = "timeout" | "network" | "notfound" | "failed"

export function isBriefEmpty(b: BriefState): boolean {
  return (
    b.sections.length === 0 || b.sections.every((s) => s.findings.length === 0)
  )
}
