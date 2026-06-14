// Shared test fixtures for the onboarding container mount tests. Keeps the
// repeated WorkspaceCompany / AnalyzeWebsiteResponse factories in one place.
import type { WorkspaceCompany } from "../../../../lib/onboarding/types"
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
    industry: "B2B SaaS",
    stage: "Seed",
    business_type: "SaaS",
    team_size: null,
    engineering_capacity: null,
    pm_engineer_ratio: null,
    competitors: [],
    tech_stack: [],
    okrs: null,
    recent_decisions: null,
    dead_ends: [],
    biggest_risk: null,
    kpi_tree: { north_star: "", north_star_description: "", metrics: [] },
    feature_flags: {
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

/** A default onboarding-context value with overridable fields. */
export function makeOnboardingCtx(over: Record<string, unknown> = {}) {
  return {
    loading: false,
    profile: null,
    workspace: makeWorkspace(),
    refresh: () => Promise.resolve(),
    setWorkspace: () => {},
    websiteAnalysis: null,
    setWebsiteAnalysis: () => {},
    ...over,
  }
}
