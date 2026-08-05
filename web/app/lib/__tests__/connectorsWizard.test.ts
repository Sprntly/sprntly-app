import { describe, expect, it } from "vitest"
import {
  categoryTitle,
  clampStep,
  ONBOARDING_CONNECTOR_CATEGORIES,
  firstIncompleteCategory,
  hasLiveAnalyticsConnection,
  isCategoryUnlocked,
  isLastCategory,
  markCategoryDone,
  nextStep,
  REQUIRED_CATEGORY_KEY,
  requiredCategoryIds,
  toggleSelection,
  wizardCategories,
} from "../onboarding/connectorsWizard"
import { CONNECTOR_CATALOG, UPLOADS_PROVIDER_ID } from "../connectorsCatalog"

describe("wizard categories", () => {
  it("exposes only supported categories, in catalog order", () => {
    const cats = wizardCategories()
    expect(cats.length).toBeGreaterThan(0)
    // Analytics leads: Superset (credentials-wired) made the required
    // category connectable, so it now surfaces first in catalog order.
    expect(cats[0].key).toBe(REQUIRED_CATEGORY_KEY)
    expect(cats[0].items.map((i) => i.id)).toEqual(["superset"])
    // v6 order: Voice follows (Sprinklr OAuth + Fireflies API-key wired),
    // then CRM (HubSpot). Revenue — the last settings-only category — never
    // appears; Company documentation now does, last, in catalog order.
    expect(cats[1].key).toBe("voice")
    // Research follows Voice with NO connectors of its own (Marvin is still
    // coming-soon) — it survives the empty-category drop on `keepWhenEmpty`
    // because its upload strip is what onboarding is asking for.
    expect(cats.map((c) => c.key)).toEqual([
      "analytics", "voice", "research", "crm", "pm", "design", "code", "docs",
    ])
    expect(cats.find((c) => c.key === "research")!.items).toEqual([])
    expect(cats.map((c) => c.key)).not.toContain("revenue")
  })

  it("has no Communications step — Slack is asked for once, on the Voice shelf", () => {
    // Removing the catalog category without removing the wizard key would have
    // left onboarding walking a step Settings → Connectors no longer has.
    const cats = wizardCategories()
    expect(cats.map((c) => c.key)).not.toContain("comms")
    expect(ONBOARDING_CONNECTOR_CATEGORIES).not.toContain("comms")
    const voice = cats.find((c) => c.key === "voice")!
    expect(voice.items.map((i) => i.id)).toContain("slack")
    // …and exactly once across the whole wizard.
    const slackSteps = cats.filter((c) => c.items.some((i) => i.id === "slack"))
    expect(slackSteps.map((c) => c.key)).toEqual(["voice"])
  })

  it("offers Confluence + Google Docs under Company documentation", () => {
    const docs = wizardCategories().find((c) => c.key === "docs")
    expect(docs).toBeTruthy()
    // Both are OAuth-wired; Notion is still coming-soon so it drops out.
    expect(docs!.items.map((i) => i.id)).toEqual(["google_drive", "confluence"])
    expect(docs!.items.map((i) => i.id)).not.toContain("notion")
  })

  it("never offers `uploads` as a wizard connector tile", () => {
    // It has no auth flow for the connect modal to open — its "Add a document
    // source" picker is a Settings-only surface. Mirrors ConnectorsSettings,
    // which excludes it from its connector rows for the same reason.
    const ids = wizardCategories().flatMap((c) => c.items.map((i) => i.id))
    expect(ids).not.toContain(UPLOADS_PROVIDER_ID)
    // Even a live `uploads` connection must not resurrect the tile.
    const withLive = wizardCategories(new Set([UPLOADS_PROVIDER_ID]))
    expect(
      withLive.flatMap((c) => c.items.map((i) => i.id)),
    ).not.toContain(UPLOADS_PROVIDER_ID)
    // ...and Company documentation survives on its real connectors.
    expect(withLive.find((c) => c.key === "docs")!.items.length).toBe(2)
  })

  it("drops connectors we don't support yet (e.g. Linear, Notion)", () => {
    const ids = wizardCategories().flatMap((c) => c.items.map((i) => i.id))
    expect(ids).toContain("slack") // supported
    expect(ids).not.toContain("linear") // coming soon
    expect(ids).not.toContain("notion") // coming soon
    // MS Teams left the catalog entirely with the Communications category —
    // it was a coming-soon delivery target, never a connectable source.
    expect(ids).not.toContain("msteams")
  })

  it("keeps a live-but-unwired provider (and its category) visible", () => {
    const cats = wizardCategories(new Set(["mixpanel"]))
    const analytics = cats.find((c) => c.key === REQUIRED_CATEGORY_KEY)
    expect(analytics).toBeTruthy()
    // Live-but-unwired Mixpanel joins the wired Superset, catalog order.
    expect(analytics!.items.map((i) => i.id)).toEqual(["mixpanel", "superset"])
  })

  it("requiredCategoryIds still reflects the raw Analytics category", () => {
    const ids = requiredCategoryIds()
    expect(ids).toContain("mixpanel")
    expect(ids).toContain("amplitude")
  })
})

// Gates the post-review define-metrics sub-flow, so "live" is strict: only an
// ACTIVE analytics connection counts.
describe("hasLiveAnalyticsConnection", () => {
  it("is false with no connections at all", () => {
    expect(hasLiveAnalyticsConnection([])).toBe(false)
  })

  it("is true for an active connection typed analytics by the backend", () => {
    expect(
      hasLiveAnalyticsConnection([
        { provider: "posthog", status: "active", types: ["analytics"] },
      ]),
    ).toBe(true)
  })

  it("falls back to the local catalog when the payload predates `types`", () => {
    expect(
      hasLiveAnalyticsConnection([{ provider: "amplitude", status: "active" }]),
    ).toBe(true)
  })

  it("is false when the only analytics connection is not active", () => {
    expect(
      hasLiveAnalyticsConnection([
        { provider: "mixpanel", status: "revoked", types: ["analytics"] },
        { provider: "heap", status: "error", types: ["analytics"] },
      ]),
    ).toBe(false)
  })

  it("is false for active connections in other categories", () => {
    expect(
      hasLiveAnalyticsConnection([
        { provider: "github", status: "active", types: ["code"] },
        { provider: "linear", status: "active", types: ["task-management"] },
      ]),
    ).toBe(false)
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
