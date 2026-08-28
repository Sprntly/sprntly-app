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
})

describe("buildStaticItems", () => {
  it("has globally unique ids", () => {
    const ids = buildStaticItems().map((i) => i.id)
    expect(new Set(ids).size).toBe(ids.length)
  })
})
