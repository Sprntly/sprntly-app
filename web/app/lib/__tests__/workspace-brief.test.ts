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
  kpi_tree: { north_star: "WAU", metrics: [{ name: "Activation", weight: 1, current_value: "40%", target_value: "55%" }] },
  feature_flags: DEFAULT_FEATURE_FLAGS,
  notification_settings: {},
  onboarding_step: 8,
  onboarding_completed_at: null,
}

describe("buildWorkspaceContextMarkdown", () => {
  it("includes company, product, and KPI tree", () => {
    const md = buildWorkspaceContextMarkdown(workspace)
    expect(md).toContain("Sprntly Inc")
    expect(md).toContain("Sprntly Platform")
    expect(md).toContain("WAU")
    expect(md).toContain("Grow activation")
  })
})
