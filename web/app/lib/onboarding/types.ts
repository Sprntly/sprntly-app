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
  weekly_brief: boolean
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
}

export type WorkspaceCompany = {
  id: string
  slug: string
  display_name: string
  /** @deprecated Use `product` — kept for rows not yet migrated to products table */
  product_description: string | null
  product: WorkspaceProduct | null
  industry: string | null
  stage: string | null
  business_type: string | null
  team_size: number | null
  engineering_capacity: number | null
  pm_engineer_ratio: string | null
  competitors: string[]
  tech_stack: string[]
  okrs: string | null
  recent_decisions: string | null
  dead_ends: string[]
  biggest_risk: string | null
  kpi_tree: KpiTree
  feature_flags: FeatureFlags
  notification_settings: Record<string, unknown>
  onboarding_step: number
  onboarding_completed_at: string | null
}

export type UserProfile = {
  id: string
  email: string | null
  first_name: string | null
  last_name: string | null
  role: string | null
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

export const DEFAULT_FEATURE_FLAGS: FeatureFlags = {
  weekly_brief: true,
  on_demand_analysis: true,
  auto_prd_generation: true,
  engineer_agent: false,
  research_agent: false,
  on_call_agent: false,
  claude_code_handoff: false,
}

export const ONBOARDING_STEP_COUNT = 8

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
