// Registry invariants for the ⌘K palette (lib/search/registry.ts): settings
// items stay derived from SETTINGS_NAV (never drift from the settings sidebar),
// page paths stay real SCREEN_PATH routes, and ids never collide.
import { describe, expect, it } from "vitest"

import { SETTINGS_NAV } from "../../components/screens/app/settings/SettingsLayout"
import { SCREEN_PATH } from "../routes"
import {
  STATIC_PAGE_ITEMS,
  buildSettingsItems,
  buildStaticItems,
} from "../search/registry"

describe("buildSettingsItems", () => {
  it("emits one item per available SETTINGS_NAV entry, with the ?section= url", () => {
    const items = buildSettingsItems()
    const navIds = SETTINGS_NAV.flatMap((g) =>
      g.items.filter((i) => i.available).map((i) => i.id),
    )
    expect(items.map((i) => i.id)).toEqual(navIds.map((id) => `settings:${id}`))
    for (const it of items) {
      const sectionId = it.id.replace(/^settings:/, "")
      expect(it.url).toBe(`/settings?section=${sectionId}`)
      expect(it.action).toEqual({ kind: "path", path: `/settings?section=${sectionId}` })
      expect(it.breadcrumb[0]).toBe("Settings")
    }
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
