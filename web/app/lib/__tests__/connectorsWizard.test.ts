import { describe, expect, it } from "vitest"
import {
  categoryTitle,
  clampStep,
  firstIncompleteCategory,
  hasRequiredConnector,
  isCategoryUnlocked,
  isLastCategory,
  markCategoryDone,
  nextStep,
  REQUIRED_CATEGORY_KEY,
  requiredCategoryIds,
  toggleSelection,
  wizardCategories,
} from "../onboarding/connectorsWizard"
import { CONNECTOR_CATALOG } from "../connectorsCatalog"

describe("wizard categories", () => {
  it("exposes only supported categories, in catalog order", () => {
    const cats = wizardCategories()
    expect(cats.length).toBeGreaterThan(0)
    // Analytics has no supported connector today → hidden from onboarding.
    expect(cats.map((c) => c.key)).not.toContain(REQUIRED_CATEGORY_KEY)
    // Project Management leads (its ClickUp is OAuth-wired).
    expect(cats[0].key).toBe("pm")
  })

  it("drops connectors we don't support yet (e.g. Linear, MS Teams)", () => {
    const ids = wizardCategories().flatMap((c) => c.items.map((i) => i.id))
    expect(ids).toContain("slack") // supported
    expect(ids).not.toContain("msteams") // coming soon
    expect(ids).not.toContain("linear") // coming soon
  })

  it("keeps a live-but-unwired provider (and its category) visible", () => {
    const cats = wizardCategories(new Set(["mixpanel"]))
    const analytics = cats.find((c) => c.key === REQUIRED_CATEGORY_KEY)
    expect(analytics).toBeTruthy()
    expect(analytics!.items.map((i) => i.id)).toEqual(["mixpanel"])
  })

  it("requiredCategoryIds still reflects the raw Analytics category", () => {
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
  // clampStep/nextStep/isLastCategory index into the full catalog.
  const last = CONNECTOR_CATALOG.length - 1
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
    const analytics = { key: "analytics", title: "Analytics", subLabel: "required", items: [] }
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

describe("accordion helpers (sequential unlock)", () => {
  it("markCategoryDone adds an index without mutating the original", () => {
    const a = new Set<number>()
    const b = markCategoryDone(a, 0)
    expect(b.has(0)).toBe(true)
    expect(a.has(0)).toBe(false)
    // idempotent
    expect(markCategoryDone(b, 0).size).toBe(1)
  })

  it("isCategoryUnlocked: first is always unlocked, N+1 needs N done", () => {
    const none = new Set<number>()
    expect(isCategoryUnlocked(none, 0)).toBe(true)
    expect(isCategoryUnlocked(none, 1)).toBe(false)
    expect(isCategoryUnlocked(none, 2)).toBe(false)
    const firstDone = new Set([0])
    expect(isCategoryUnlocked(firstDone, 1)).toBe(true)
    expect(isCategoryUnlocked(firstDone, 2)).toBe(false)
  })

  it("isCategoryUnlocked: done categories stay unlocked (re-openable)", () => {
    const done = new Set([0, 1])
    expect(isCategoryUnlocked(done, 0)).toBe(true)
    expect(isCategoryUnlocked(done, 1)).toBe(true)
    expect(isCategoryUnlocked(done, 2)).toBe(true)
    expect(isCategoryUnlocked(done, 3)).toBe(false)
  })

  it("firstIncompleteCategory finds the frontier, null when all done", () => {
    expect(firstIncompleteCategory(new Set(), 3)).toBe(0)
    expect(firstIncompleteCategory(new Set([0]), 3)).toBe(1)
    expect(firstIncompleteCategory(new Set([0, 2]), 3)).toBe(1)
    expect(firstIncompleteCategory(new Set([0, 1, 2]), 3)).toBeNull()
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
