// Shared test fixtures for the onboarding container mount tests. Keeps the
// repeated WorkspaceCompany / UserProfile / AnalyzeWebsiteResponse factories
// in one place.
import type {
  UserProfile,
  WorkspaceCompany,
} from "../../../../lib/onboarding/types"
import type { AnalyzeWebsiteResponse } from "../../../../lib/api"

export function makeWorkspace(
  over: Partial<WorkspaceCompany> = {},
): WorkspaceCompany {
  return {
    id: "ws-1",
    slug: "acme",
    display_name: "Acme",
    product_description: null,
    product: null,
    account_type: "company",
    industry: "B2B SaaS",
    stage: "Seed",
    business_type: "SaaS",
    team_size: null,
    engineering_capacity: null,
    pm_engineer_ratio: null,
    competitors: [],
    tech_stack: [],
    okrs: null,
    mission: null,
    strategy: null,
    portfolio: null,
    icp: { segment: null, buyer_persona: null, buyer: null },
    tone_voice: { brand: null, tone: null, colors: [] },
    planning_cycle: null,
    team_name: null,
    team_scope: null,
    prioritization_framework: null,
    sizing_methodology: null,
    team_strategy: null,
    team_roadmap: null,
    decision_process: null,
    additional_context: null,
    business_context_summary: null,
    business_context_accepted_at: null,
    metric_definitions: [],
    recent_decisions: null,
    dead_ends: [],
    biggest_risk: null,
    kpi_tree: { north_star: "", north_star_description: "", metrics: [] },
    feature_flags: {
      agents: true,
      weekly_brief: true,
      on_demand_analysis: true,
      auto_prd_generation: true,
      engineer_agent: false,
      research_agent: false,
      on_call_agent: false,
      claude_code_handoff: false,
    },
    notification_settings: {},
    design_source: null,
    onboarding_step: 1,
    onboarding_completed_at: null,
    use_platform_key: false,
    ...over,
  }
}

export function makeAnalysis(
  over: Partial<AnalyzeWebsiteResponse> = {},
): AnalyzeWebsiteResponse {
  return {
    ok: true,
    reason: null,
    url: "https://acme.com",
    industry: "Fintech",
    sub_vertical: "Payments",
    business_type: "Marketplace",
    stage: "Growth",
    business_context: "Acme helps SMBs reconcile payments across providers.",
    suggested_metrics: [
      { metric: "Reconciled volume", description: "Total $ reconciled / week." },
      { metric: "Active connected accounts", description: "Accounts with a live sync." },
    ],
    provenance: "website",
    business_context_version: 1,
    ...over,
  }
}

export function makeProfile(over: Partial<UserProfile> = {}): UserProfile {
  return {
    id: "u-1",
    email: "u@example.com",
    first_name: "Ada",
    last_name: "Lovelace",
    role: "PM",
    priorities: null,
    timezone: null,
    account_type: "company",
    onboarding_step: 1,
    onboarding_completed_at: null,
    skipped_fields: [],
    ...over,
  }
}

/** A default onboarding-context value with overridable fields. */
export function makeOnboardingCtx(over: Record<string, unknown> = {}) {
  return {
    loading: false,
    refreshing: false,
    profile: makeProfile(),
    workspace: makeWorkspace(),
    refresh: () => Promise.resolve(),
    setWorkspace: () => {},
    websiteAnalysis: null,
    setWebsiteAnalysis: () => {},
    startWebsiteAnalysis: () => {},
    ...over,
  }
}
