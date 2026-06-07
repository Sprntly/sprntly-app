import { describe, expect, it } from "vitest"
import {
  categoryTitle,
  clampStep,
  hasRequiredConnector,
  isLastCategory,
  nextStep,
  REQUIRED_CATEGORY_KEY,
  requiredCategoryIds,
  toggleSelection,
  wizardCategories,
} from "../onboarding/connectorsWizard"

describe("wizard categories", () => {
  it("exposes the full catalog in order, Analytics first", () => {
    const cats = wizardCategories()
    expect(cats.length).toBeGreaterThan(0)
    expect(cats[0].key).toBe(REQUIRED_CATEGORY_KEY)
  })

  it("requiredCategoryIds returns the Analytics connector ids", () => {
    const ids = requiredCategoryIds()
    expect(ids).toContain("mixpanel")
    expect(ids).toContain("amplitude")
  })
})

describe("hasRequiredConnector", () => {
  it("is false with nothing selected", () => {
    expect(hasRequiredConnector(new Set())).toBe(false)
  })
  it("is true once any Analytics connector is selected", () => {
    expect(hasRequiredConnector(new Set(["mixpanel"]))).toBe(true)
  })
  it("is false when only non-Analytics connectors are selected", () => {
    expect(hasRequiredConnector(new Set(["linear"]))).toBe(false)
  })
})

describe("step navigation", () => {
  const last = wizardCategories().length - 1
  it("clamps below 0 and above last", () => {
    expect(clampStep(-3)).toBe(0)
    expect(clampStep(last + 5)).toBe(last)
  })
  it("nextStep advances but never past the last category", () => {
    expect(nextStep(0)).toBe(1)
    expect(nextStep(last)).toBe(last)
  })
  it("isLastCategory detects the final step", () => {
    expect(isLastCategory(0)).toBe(false)
    expect(isLastCategory(last)).toBe(true)
  })
})

describe("categoryTitle", () => {
  it("decorates the required category", () => {
    const analytics = wizardCategories()[0]
    expect(categoryTitle(analytics)).toMatch(/at least one required/i)
  })
  it("appends a sub-label when present and not required", () => {
    const withSub = { key: "x", title: "Code", subLabel: "repos", items: [] }
    expect(categoryTitle(withSub)).toBe("Code · repos")
  })
  it("uses the bare title when there is no sub-label", () => {
    const plain = { key: "x", title: "Design", items: [] }
    expect(categoryTitle(plain)).toBe("Design")
  })
})

describe("toggleSelection", () => {
  it("adds then removes an id, returning fresh sets", () => {
    const a = toggleSelection(new Set(), "mixpanel")
    expect(a.has("mixpanel")).toBe(true)
    const b = toggleSelection(a, "mixpanel")
    expect(b.has("mixpanel")).toBe(false)
    expect(a.has("mixpanel")).toBe(true) // original untouched
  })
})
