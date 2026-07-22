export type KpiMetric = {
  name: string
  /** Free-text context for goal-fit scoring (replaces the old numeric fields). */
  description: string
}

export type KpiTree = {
  north_star: string
  /** Free-text context for the North Star metric. */
  north_star_description: string
  metrics: KpiMetric[]
}

export type FeatureFlags = {
  /** The single Agents module (staff admin) — absorbs the four agent-ish
   *  legacy keys below. Old rows without it fall back to the legacy keys at
   *  display time (see StaffAdminScreen); stored data is never rewritten. */
  agents: boolean
  weekly_brief: boolean
  // Legacy keys — superseded by `agents` but kept so old stored rows and the
  // dormant FeatureFlagsSettings surface still typecheck.
  on_demand_analysis: boolean
  auto_prd_generation: boolean
  engineer_agent: boolean
  research_agent: boolean
  on_call_agent: boolean
  claude_code_handoff: boolean
}

export type WorkspaceProduct = {
  id: string
  company_id: string
  name: string
  website: string | null
  description: string | null
  is_primary: boolean
  /** Where the product ships (registration spec 2026-07): 'web','mobile','api','hardware'. */
  surfaces: string[]
  /** User personas, free-text chips (legacy — v6 collects users_description prose instead). */
  personas: string[]
  /** Product positioning (settings-only). */
  positioning: string | null
  /** Single pick in the v6 wizard (stored as a 0/1-element array for column
   *  compat): see MONETIZATION_OPTIONS. */
  monetization: string[]
  /** "Tell us about your users" — free prose (v6 step 2). */
  users_description: string | null
  /** Spec's "State" — 'enterprise','mid-market','startup','early-stage'
   *  (settings-only; named to avoid the companies.stage collision). */
  maturity: string | null
}

/** Metric definition confirmed in the post-wizard sub-flow: the plain-English
 *  definition plus the analytics event mapping (both fully editable), and the
 *  best-effort current value shown on the review screen (null → "—"). Stored
 *  on companies.metric_definitions jsonb. */
export type MetricDefinition = {
  metric: string
  definition: string
  mapping: string
  baseline: string | null
}

export type DesignSourcePreference = {
  design_source: "figma" | "github" | "website"
  figma_file_key?: string | null
  github_repo?: string | null
  website_url?: string | null
}

/** ICP (settings-only) — companies.icp jsonb. */
export type CompanyIcp = {
  segment: string | null
  buyer_persona: string | null
  buyer: string | null
}

/** Tone & voice (settings-only) — companies.tone_voice jsonb. */
export type CompanyToneVoice = {
  brand: string | null
  tone: string | null
  colors: string[]
}

export type WorkspaceCompany = {
  id: string
  slug: string
  display_name: string
  /** @deprecated Use `product` — kept for rows not yet migrated to products table */
  product_description: string | null
  product: WorkspaceProduct | null
  account_type: AccountType | null
  industry: string | null
  stage: string | null
  business_type: string | null
  team_size: number | null
  engineering_capacity: number | null
  pm_engineer_ratio: string | null
  competitors: string[]
  tech_stack: string[]
  okrs: string | null
  mission: string | null
  strategy: string | null
  portfolio: string | null
  icp: CompanyIcp
  tone_voice: CompanyToneVoice
  planning_cycle: string | null
  /** v6 step 5 — the team's name (a company field, NOT the workspaces row,
   *  which stays "Default" until renamed in Settings → Workspaces). */
  team_name: string | null
  team_scope: string | null
  prioritization_framework: string | null
  sizing_methodology: string | null
  /** v6 steps 6-7 typed (not uploaded) blocks. */
  team_strategy: string | null
  team_roadmap: string | null
  decision_process: string | null
  additional_context: string | null
  /** v6 step 9 — the accepted AI-drafted business-context prose. */
  business_context_summary: string | null
  business_context_accepted_at: string | null
  /** v6 metric sub-flow — confirmed per-metric definitions/mappings. */
  metric_definitions: MetricDefinition[]
  recent_decisions: string | null
  dead_ends: string[]
  biggest_risk: string | null
  kpi_tree: KpiTree
  feature_flags: FeatureFlags
  notification_settings: Record<string, unknown>
  design_source: DesignSourcePreference | null
  onboarding_step: number
  onboarding_completed_at: string | null
  /** DB-only flag (Sprntly sets it for contracted customers): when true the
   *  workspace may run on the platform key. The onboarding Claude-key step is
   *  now skippable for EVERYONE regardless of this flag; the flag only tailors
   *  the step's copy (platform usage included vs. bring-your-own). */
  use_platform_key: boolean
}

/** RETIRED from the UI (onboarding v6, 2026-07-17): the company/personal
 *  split is gone — every new signup is "company" and the wizard's starred
 *  fields are mandatory for everyone. The type + column survive for existing
 *  'personal' rows; nothing reads them for branching anymore. */
export type AccountType = "company" | "personal"

export function parseAccountType(raw: unknown): AccountType | null {
  return raw === "company" || raw === "personal" ? raw : null
}

export type UserProfile = {
  id: string
  email: string | null
  first_name: string | null
  last_name: string | null
  role: string | null
  /** "Your priorities — what you're focused on right now" (sign-up About-you,
   *  v6). Free text, optional. */
  priorities: string | null
  /** IANA timezone (e.g. "America/New_York"); drives the weekly brief send time.
   *  null until captured at signup or set in settings → backend falls back to UTC. */
  timezone: string | null
  account_type: AccountType | null
  onboarding_step: number
  onboarding_completed_at: string | null
  skipped_fields: string[]
}

export const INDUSTRIES = [
  "B2B SaaS",
  "B2C",
  "Marketplace",
  "Fintech",
  "Healthtech",
  "E-commerce",
  "Developer Tools",
  "Gaming / Entertainment",
  "Other",
] as const

export const STAGES = ["Seed", "Growth", "Scale"] as const

export const BUSINESS_TYPES = ["SaaS", "Marketplace", "Consumer"] as const

export const TECH_STACK_OPTIONS = [
  "Web",
  "Mobile (iOS)",
  "Mobile (Android)",
  "API/Backend",
  "Other",
] as const

export const ROLE_OPTIONS = [
  "Founder",
  "PM",
  "Engineer",
  "Data Scientist",
  "Designer",
  "Other",
] as const

// ── Registration-spec option vocabularies (2026-07) ─────────────────────────
// Canonical stored values, with display labels where they differ.

export const SURFACE_OPTIONS = [
  { value: "web", label: "Web" },
  { value: "mobile", label: "Mobile app" },
  { value: "api", label: "API" },
  { value: "hardware", label: "Hardware" },
] as const

/** v6: a SINGLE dropdown pick in the wizard (stored as a 0/1-element array
 *  for products.monetization column compat). Values are client-validated —
 *  no DB CHECK — so the three v6 additions need no migration. */
export const MONETIZATION_OPTIONS = [
  { value: "subscription", label: "Subscription" },
  { value: "seat", label: "Seat-based" },
  { value: "usage", label: "Usage-based" },
  { value: "transaction-fee", label: "Transaction fee" },
  { value: "advertising", label: "Advertising" },
  { value: "partner-rev-share", label: "Partner rev-share" },
  { value: "one-time", label: "One-time purchase" },
  { value: "free", label: "Free" },
] as const

/** Job roles for step-8 teammate invites (distinct from the member/admin/viewer
 *  permission). Display-only free text on workspace_invites.job_role. */
export const JOB_ROLE_OPTIONS = [
  "Product Manager",
  "Engineer",
  "Data Science",
  "Designer",
  "Founder / CEO",
  "Customer Success",
  "Marketing",
  "Operations",
  "Other",
] as const

export const PRIORITIZATION_FRAMEWORKS = [
  { value: "goal-based", label: "Based on goal" },
  { value: "rice", label: "RICE" },
  { value: "wsjf", label: "WSJF" },
  { value: "moscow", label: "MoSCoW" },
  { value: "kano", label: "Kano" },
  { value: "volume-severity", label: "Volume / severity" },
] as const

export const MATURITY_OPTIONS = [
  { value: "enterprise", label: "Enterprise" },
  { value: "mid-market", label: "Mid-market" },
  { value: "startup", label: "Startup" },
  { value: "early-stage", label: "Early stage" },
] as const

export const PLANNING_CYCLES = [
  { value: "half", label: "Every half" },
  { value: "quarterly", label: "Quarterly" },
  { value: "annual", label: "Annual" },
  { value: "monthly", label: "Monthly" },
] as const

export const DEFAULT_FEATURE_FLAGS: FeatureFlags = {
  agents: true,
  weekly_brief: true,
  on_demand_analysis: true,
  auto_prd_generation: true,
  engineer_agent: false,
  research_agent: false,
  on_call_agent: false,
  claude_code_handoff: false,
}

/**
 * The semantic slugs of the numbered onboarding steps, in flow order. This is
 * the single source of truth for the onboarding route order. The flow follows
 * the 2026-07-21 screenshot spec (which collapsed team/strategy/decisions into
 * one workspace step and added a personalize step) + the optional api-key step
 * the spec omits but we keep — 9 steps + the define-metrics sub-flow:
 *
 *   1. company     → CompanyStep         (name* + website + strategy/OKRs;
 *                                         mission, portfolio + planning cycle
 *                                         behind "Add more". Kicks the website
 *                                         analysis in the BACKGROUND.)
 *   2. product     → ProductStep         (name* + website + surfaces* +
 *                                         monetization + users; competitors
 *                                         behind a disclosure)
 *   3. metrics     → MetricsStep         (pick up to 5 success metrics* +
 *                                         prioritization framework*)
 *   4. api-key     → ApiKey              (the workspace's own Claude/Anthropic
 *                                         key — OPTIONAL, skippable; set now so
 *                                         the token-heavy knowledge-graph build
 *                                         runs on it, or later in Settings →
 *                                         Admin)
 *   5. connectors  → Connectors          (connect your tools — OPTIONAL,
 *                                         skippable; zero connectors is a
 *                                         supported finish)
 *   6. workspace   → WorkspaceStep       (workspace name* + what it works on* +
 *                                         team strategy/roadmap; sizing +
 *                                         anything else behind "Add more")
 *   7. invite      → InviteStep          (teammates: email + job role +
 *                                         permission, bulk paste, CSV import;
 *                                         skippable)
 *   8. review      → ReviewStep          (AI-drafted business context — read,
 *                                         edit, accept)
 *   9. personalize → PersonalizeStep     (what the workspace surfaces + brief
 *                                         delivery cadence/channel/time)
 *
 * After step 9 the UNNUMBERED define-metrics sub-flow (route
 * /onboarding/define-metrics, no progress dots — like the your-name gate)
 * confirms a definition + analytics mapping per picked metric, reviews them,
 * and "generate knowledge graph" COMPLETES onboarding + kicks the first brief.
 * That sub-flow is GATED on a live analytics connection: with none there is
 * nothing to map events against, so PersonalizeStep finishes onboarding
 * directly instead. The gate lives on step 9 (it moved off Review when
 * personalize was inserted between them) — see hasLiveAnalyticsConnection.
 *
 * The api-key step (restored 2026-07-19) is OPTIONAL/skippable for everyone —
 * skip it and the workspace runs on the platform key until a key is added here
 * or in Settings → Admin.
 *
 * The step-6 workspace NAME is a company field (companies.team_name), not the
 * workspaces row — renaming an actual workspace still lives in
 * Settings → Workspaces.
 *
 * `onboarding_step` (the integer DB column) is the 1-based INDEX into this
 * array. Use `slugForStep` / `stepForSlug` to convert, and `clampStep` to keep
 * persisted values (including stale ones from older flows) in range.
 */
export const ONBOARDING_STEP_SLUGS = [
  "company",
  "product",
  "metrics",
  "api-key",
  "connectors",
  "workspace",
  "invite",
  "review",
  "personalize",
] as const

export type OnboardingStepSlug = (typeof ONBOARDING_STEP_SLUGS)[number]

export const ONBOARDING_STEP_COUNT = ONBOARDING_STEP_SLUGS.length

/** True for a valid numbered-step slug. */
export function isOnboardingStepSlug(slug: string): slug is OnboardingStepSlug {
  return (ONBOARDING_STEP_SLUGS as readonly string[]).includes(slug)
}

/**
 * Clamp a persisted 1-based `onboarding_step` into [1, ONBOARDING_STEP_COUNT].
 * Existing users mid an older, longer flow may carry a step index past the end
 * (e.g. the removed coworkers step, or the old 7-step order); those land on the
 * last valid step rather than crashing. Non-finite / <1 values clamp up to 1.
 */
export function clampStep(step: number): number {
  if (!Number.isFinite(step)) return 1
  return Math.min(Math.max(Math.trunc(step), 1), ONBOARDING_STEP_COUNT)
}

/** 1-based step index → its slug (clamped). */
export function slugForStep(step: number): OnboardingStepSlug {
  return ONBOARDING_STEP_SLUGS[clampStep(step) - 1]
}

/** Slug → its 1-based step index, or null when it isn't a numbered step. */
export function stepForSlug(slug: string): number | null {
  const i = (ONBOARDING_STEP_SLUGS as readonly string[]).indexOf(slug)
  return i === -1 ? null : i + 1
}

export function emptyKpiTree(): KpiTree {
  return { north_star: "", north_star_description: "", metrics: [] }
}

/**
 * Parse a stored `companies.kpi_tree` jsonb into the workspace KpiTree shape.
 *
 * The canonical stored shape is the backend's: a `north_star` object plus
 * `primary_metrics` + `secondary_signals`, each `{ metric, description }`.
 * We flatten primaries + signals into a single ordered `metrics` list. Both
 * legacy shapes are tolerated on read:
 *   - `north_star` as a bare string (pre-object rows), and
 *   - the old workspace `{ north_star: string, metrics: [{ name, weight, … }] }`
 *     shape — the extra numeric fields are simply ignored.
 */
export function parseKpiTree(raw: unknown): KpiTree {
  if (!raw || typeof raw !== "object") return emptyKpiTree()
  const o = raw as Record<string, unknown>

  // North star: object { metric, description } | bare string | legacy string.
  let northStar = ""
  let northStarDescription = ""
  const ns = o.north_star
  if (typeof ns === "string") {
    northStar = ns
  } else if (ns && typeof ns === "object") {
    const x = ns as Record<string, unknown>
    northStar = String(x.metric ?? "")
    northStarDescription = String(x.description ?? "")
  }

  const toMetric = (m: unknown): KpiMetric => {
    const x = (m ?? {}) as Record<string, unknown>
    // New shape uses `metric`; the old workspace shape used `name`.
    return {
      name: String(x.metric ?? x.name ?? ""),
      description: String(x.description ?? ""),
    }
  }

  let metrics: KpiMetric[] = []
  if (Array.isArray(o.primary_metrics) || Array.isArray(o.secondary_signals)) {
    const primary = Array.isArray(o.primary_metrics) ? o.primary_metrics : []
    const secondary = Array.isArray(o.secondary_signals) ? o.secondary_signals : []
    metrics = [...primary, ...secondary].map(toMetric)
  } else if (Array.isArray(o.metrics)) {
    metrics = o.metrics.map(toMetric)
  }
  metrics = metrics.filter((m) => m.name.trim().length > 0)

  return { north_star: northStar, north_star_description: northStarDescription, metrics }
}

export function parseFeatureFlags(raw: unknown): FeatureFlags {
  if (!raw || typeof raw !== "object") return { ...DEFAULT_FEATURE_FLAGS }
  return { ...DEFAULT_FEATURE_FLAGS, ...(raw as Partial<FeatureFlags>) }
}

export function parseCompanyIcp(raw: unknown): CompanyIcp {
  const o = raw && typeof raw === "object" ? (raw as Record<string, unknown>) : {}
  const str = (v: unknown) => (typeof v === "string" && v.trim() ? v : null)
  return {
    segment: str(o.segment),
    buyer_persona: str(o.buyer_persona),
    buyer: str(o.buyer),
  }
}

export function parseCompanyToneVoice(raw: unknown): CompanyToneVoice {
  const o = raw && typeof raw === "object" ? (raw as Record<string, unknown>) : {}
  const str = (v: unknown) => (typeof v === "string" && v.trim() ? v : null)
  return {
    brand: str(o.brand),
    tone: str(o.tone),
    colors: Array.isArray(o.colors) ? o.colors.map(String).filter(Boolean) : [],
  }
}

/** Parse companies.metric_definitions jsonb (unknown/legacy shapes → []). */
export function parseMetricDefinitions(raw: unknown): MetricDefinition[] {
  if (!Array.isArray(raw)) return []
  return raw
    .map((m) => {
      const o = m && typeof m === "object" ? (m as Record<string, unknown>) : {}
      const metric = typeof o.metric === "string" ? o.metric.trim() : ""
      return {
        metric,
        definition: typeof o.definition === "string" ? o.definition : "",
        mapping: typeof o.mapping === "string" ? o.mapping : "",
        baseline:
          typeof o.baseline === "string" && o.baseline.trim() ? o.baseline : null,
      }
    })
    .filter((m) => m.metric.length > 0)
}

export function parseDesignSourcePreference(raw: unknown): DesignSourcePreference | null {
  if (!raw || typeof raw !== "object") return null
  const o = raw as Record<string, unknown>
  const ds = o.design_source
  if (ds !== "figma" && ds !== "github" && ds !== "website") return null
  return {
    design_source: ds,
    figma_file_key: (o.figma_file_key as string | null | undefined) ?? null,
    github_repo: (o.github_repo as string | null | undefined) ?? null,
    website_url: (o.website_url as string | null | undefined) ?? null,
  }
}
