// Registry invariants for the ⌘K palette (lib/search/registry.ts): settings
// items stay derived from SETTINGS_NAV (never drift from the settings sidebar),
// page paths stay real SCREEN_PATH routes, and ids never collide.
import { describe, expect, it } from "vitest"

import { SETTINGS_NAV, SETTINGS_PANES } from "../../components/screens/app/settings/SettingsLayout"
import { SCREEN_PATH } from "../routes"
import {
  STATIC_PAGE_ITEMS,
  buildSettingsItems,
  buildStaticItems,
} from "../search/registry"

describe("buildSettingsItems", () => {
  it("emits one item per settings VIEW — every leaf, not every nav row", () => {
    // The nav consolidated: Connectors and MCP Access are views inside a row
    // called "Integrations", Metrics inside one called "Company". Built from
    // rows, the palette would offer "Integrations" and match nothing for
    // "connectors" — so someone who knows exactly what they want types its
    // name and is told it does not exist. Search reaches a thing by ITS name,
    // whatever the nav calls the drawer it lives in.
    const items = buildSettingsItems()
    const expected = SETTINGS_NAV.flatMap((g) =>
      g.items
        .filter((i) => i.available)
        .flatMap((row) => {
          const pane = SETTINGS_PANES.find((p) => p.id === row.id)
          return pane ? pane.leaves : [row.id]
        }),
    )
    expect(items.map((i) => i.id)).toEqual(expected.map((id) => `settings:${id}`))

    for (const it of items) {
      const sectionId = it.id.replace(/^settings:/, "")
      const row = SETTINGS_NAV.flatMap((g) => g.items).find((i) => i.id === sectionId)
      // A row that opens somewhere else (Guide) keeps its own href.
      const url = row?.href ?? `/settings?section=${sectionId}`
      expect(it.url).toBe(url)
      expect(it.action).toEqual({ kind: "path", path: url })
      expect(it.breadcrumb[0]).toBe("Settings")
    }
  })

  it("titles each result with the VIEW's name, not the row's", () => {
    const items = buildSettingsItems()
    expect(items.find((i) => i.id === "settings:connectors")?.title).toBe("Connectors")
    expect(items.find((i) => i.id === "settings:mcp")?.title).toBe("MCP Access")
    expect(items.find((i) => i.id === "settings:metrics")?.title).toBe("Metrics")
  })

  it("carries the nav group label as the second breadcrumb segment", () => {
    const items = buildSettingsItems()
    const connectors = items.find((i) => i.id === "settings:connectors")!
    expect(connectors.breadcrumb).toEqual(["Settings", "Data & Integrations"])
  })
})

describe("STATIC_PAGE_ITEMS", () => {
  it("only points screen actions at real SCREEN_PATH routes", () => {
    const validPaths = new Set(Object.values(SCREEN_PATH))
    for (const it of STATIC_PAGE_ITEMS) {
      if (it.action.kind === "screen") {
        expect(SCREEN_PATH[it.action.screen]).toBeDefined()
        if (it.url) expect(validPaths.has(it.url)).toBe(true)
      }
    }
  })

  // Feedback had no palette entry either, for a different reason: it is a
  // MODAL, not a route, so it fell outside a list built from pages. It is the
  // one thing a stuck user most wants to find by typing what they want.
  it("can find Send feedback, which has no route of its own", () => {
    const item = STATIC_PAGE_ITEMS.find((i) => i.id === "action:feedback")!
    expect(item).toBeDefined()
    expect(item.action).toEqual({ kind: "feedback" })
    // No url: a modal has nowhere to point, and the row must not render one.
    expect(item.url).toBeUndefined()
  })

  // EVERY PART OF THE APP IS SEARCHABLE, AND THIS IS WHAT KEEPS IT THAT WAY.
  //
  // Projects and Backlog were live rail items the palette could not find, and
  // Tickets, Roadmap, Shipped, Past, Evidence and Prototype had no door at all
  // — no rail item, no in-app link, and no search result. STATIC_PAGE_ITEMS is
  // hand-maintained while SCREEN_PATH is the real route table, so the two
  // drifted silently and a whole screen could go unreachable indefinitely.
  //
  // So this asserts COVERAGE, derived: every screen in SCREEN_PATH is either
  // searchable or listed below with a reason. Adding a screen and forgetting
  // the palette now fails here — the exclusion list is the only way past, and
  // it costs a sentence explaining why.
  it("makes every screen in SCREEN_PATH reachable from search", () => {
    /** Screens that legitimately have no page entry of their own. */
    const EXEMPT = new Map<string, string>([
      ["ondemand", "an alias of `chat` — same path, and Chat already lists it"],
      ["connectors", "an alias of the settings pane, searchable as settings:connectors"],
      ["templates", "searchable as settings:templates (it moved into Settings)"],
      ["skills", "searchable as settings:skills (it moved into Settings)"],
    ])

    const searchable = new Set(
      STATIC_PAGE_ITEMS.flatMap((i) =>
        i.action.kind === "screen" ? [i.action.screen as string] : [],
      ),
    )

    const missing = Object.keys(SCREEN_PATH).filter((screen) => {
      // Onboarding steps are a linear flow you are placed into, not a
      // destination you navigate to.
      if (screen.startsWith("ob-")) return false
      if (searchable.has(screen)) return false
      return !EXEMPT.has(screen)
    })

    expect(
      missing,
      `these screens exist but nothing in the palette reaches them: ${missing.join(", ")}`,
    ).toEqual([])
  })

  // /roadmap has no ScreenId at all — it is a route-only artifact view, so its
  // entry is a `path` action. The coverage test above cannot see it; this does.
  it("reaches the route-only surfaces that have no ScreenId", () => {
    const paths = new Set(
      STATIC_PAGE_ITEMS.flatMap((i) => (i.action.kind === "path" ? [i.action.path] : [])),
    )
    expect(paths.has("/roadmap")).toBe(true)
  })
})

describe("buildStaticItems", () => {
  it("has globally unique ids", () => {
    const ids = buildStaticItems().map((i) => i.id)
    expect(new Set(ids).size).toBe(ids.length)
  })
})
