import { describe, expect, it } from "vitest"
import { resolveSectionId } from "../../SettingsScreen"

describe("resolveSectionId — graceful fallback for unknown/removed sections", () => {
  it("returns the id unchanged for an active section", () => {
    expect(resolveSectionId("connectors")).toBe("connectors")
    expect(resolveSectionId("profile")).toBe("profile")
  })

  it("keeps dormant ids (reachable by URL) unchanged", () => {
    expect(resolveSectionId("strategic")).toBe("strategic")
    expect(resolveSectionId("flags")).toBe("flags")
  })

  it("falls back to Profile for a missing section param", () => {
    expect(resolveSectionId(null)).toBe("profile")
    expect(resolveSectionId("")).toBe("profile")
  })

  it("falls back to Profile for the removed Goals & metrics (KPI tree) and Prototypes (preview) deep links", () => {
    expect(resolveSectionId("goals-metrics")).toBe("profile")
    expect(resolveSectionId("design-source")).toBe("profile")
  })

  it("falls back to Profile for any other unknown id", () => {
    expect(resolveSectionId("does-not-exist")).toBe("profile")
  })
})
