export type KpiMetric = {
  name: string
  current_value?: string
  target_value?: string
  weight: number
}

export type KpiTree = {
  north_star: string
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
  return { north_star: "", metrics: [] }
}

export function parseKpiTree(raw: unknown): KpiTree {
  if (!raw || typeof raw !== "object") return emptyKpiTree()
  const o = raw as Record<string, unknown>
  return {
    north_star: typeof o.north_star === "string" ? o.north_star : "",
    metrics: Array.isArray(o.metrics)
      ? o.metrics.map((m) => {
          const x = m as Record<string, unknown>
          return {
            name: String(x.name ?? ""),
            current_value: x.current_value != null ? String(x.current_value) : undefined,
            target_value: x.target_value != null ? String(x.target_value) : undefined,
            weight: Number(x.weight) || 0,
          }
        })
      : [],
  }
}

export function parseFeatureFlags(raw: unknown): FeatureFlags {
  if (!raw || typeof raw !== "object") return { ...DEFAULT_FEATURE_FLAGS }
  return { ...DEFAULT_FEATURE_FLAGS, ...(raw as Partial<FeatureFlags>) }
}
