import { describe, expect, it } from "vitest"
import { SETTINGS_NAV } from "../SettingsLayout"

describe("SETTINGS_NAV — design-3 grouped structure (commit B)", () => {
  it("has exactly 4 groups in the design-3 order", () => {
    expect(SETTINGS_NAV.map((g) => g.groupLabel)).toEqual([
      "You",
      "Workspace",
      "Data & Integrations",
      "Account",
    ])
  })

  it("You group contains Profile and Comms & Brief", () => {
    const you = SETTINGS_NAV.find((g) => g.groupLabel === "You")!
    expect(you.items.map((i) => i.id)).toEqual(["profile", "comms-brief"])
  })

  it("Workspace group contains Product & Category, Business Context, Team & roles", () => {
    const ws = SETTINGS_NAV.find((g) => g.groupLabel === "Workspace")!
    expect(ws.items.map((i) => i.id)).toEqual([
      "product-category",
      "business-context",
      "team",
    ])
  })

  it("Data & Integrations group contains Connectors and MCP Access", () => {
    const dat = SETTINGS_NAV.find((g) => g.groupLabel === "Data & Integrations")!
    expect(dat.items.map((i) => i.id)).toEqual(["connectors", "mcp"])
  })

  it("Account group contains Billing and Security", () => {
    const acct = SETTINGS_NAV.find((g) => g.groupLabel === "Account")!
    expect(acct.items.map((i) => i.id)).toEqual(["billing", "security"])
  })

  it("uses the design-3 human labels", () => {
    const allItems = SETTINGS_NAV.flatMap((g) => g.items)
    const byId = Object.fromEntries(allItems.map((i) => [i.id, i.label]))
    expect(byId).toEqual({
      profile: "Profile",
      "comms-brief": "Comms & Brief",
      "product-category": "Product & Category",
      "business-context": "Business Context",
      team: "Team & roles",
      connectors: "Connectors",
      mcp: "MCP Access",
      billing: "Billing",
      security: "Security",
    })
  })

  it("does not surface dormant ids (strategic, flags), old ids (workspace, kpi, notifications), or the removed Goals & metrics / Prototypes panes", () => {
    const allIds = SETTINGS_NAV.flatMap((g) => g.items).map((i) => i.id)
    expect(allIds).not.toContain("strategic")
    expect(allIds).not.toContain("flags")
    expect(allIds).not.toContain("workspace")
    expect(allIds).not.toContain("kpi")
    expect(allIds).not.toContain("notifications")
    // Removed sections: Goals & metrics (KPI tree) and Prototypes (preview).
    expect(allIds).not.toContain("goals-metrics")
    expect(allIds).not.toContain("design-source")
  })

  it("marks Billing and Security as available stubs (not 'Soon' badge)", () => {
    const acct = SETTINGS_NAV.find((g) => g.groupLabel === "Account")!
    // Stubs are reachable; they render a 'Coming soon' panel from inside.
    // We don't want the nav greying them out.
    for (const item of acct.items) {
      expect(item.available).toBe(true)
    }
  })
})
