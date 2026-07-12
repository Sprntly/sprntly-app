import { describe, expect, it } from "vitest"
import { buildWorkspaceContextMarkdown } from "../workspace-brief"
import type { WorkspaceCompany } from "../onboarding/types"
import { DEFAULT_FEATURE_FLAGS, emptyKpiTree } from "../onboarding/types"

const workspace: WorkspaceCompany = {
  id: "1",
  slug: "sprntly",
  display_name: "Sprntly Inc",
  product_description: null,
  product: { id: "p1", company_id: "1", name: "Sprntly Platform", website: "https://sprntly.ai", description: null, is_primary: true },
  industry: "B2B SaaS",
  stage: "Growth",
  business_type: "SaaS",
  team_size: 12,
  engineering_capacity: null,
  pm_engineer_ratio: null,
  competitors: ["Acme"],
  tech_stack: ["Web"],
  okrs: "Grow activation",
  recent_decisions: null,
  dead_ends: [],
  biggest_risk: null,
  kpi_tree: {
    north_star: "WAU",
    north_star_description: "Weekly active users in a 7-day window",
    metrics: [{ name: "Activation", description: "Reach value by week 2" }],
  },
  feature_flags: DEFAULT_FEATURE_FLAGS,
  notification_settings: {},
  design_source: null,
  onboarding_step: 8,
  onboarding_completed_at: null,
  use_platform_key: false,
}

describe("buildWorkspaceContextMarkdown", () => {
  it("includes company, product, and KPI tree", () => {
    const md = buildWorkspaceContextMarkdown(workspace)
    expect(md).toContain("Sprntly Inc")
    expect(md).toContain("Sprntly Platform")
    expect(md).toContain("WAU")
    expect(md).toContain("Weekly active users in a 7-day window")
    expect(md).toContain("Activation — Reach value by week 2")
    expect(md).toContain("Grow activation")
    // No weights / numeric metric values leak into the brief context.
    expect(md).not.toContain("weight")
    expect(md).not.toContain("%")
  })
})
