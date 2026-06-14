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

  it("Workspace group contains Product & Category, Goals & metrics, Team & roles", () => {
    const ws = SETTINGS_NAV.find((g) => g.groupLabel === "Workspace")!
    expect(ws.items.map((i) => i.id)).toEqual([
      "product-category",
      "goals-metrics",
      "team",
    ])
  })

  it("Data & Integrations group contains Connectors and the design source (Prototypes)", () => {
    const dat = SETTINGS_NAV.find((g) => g.groupLabel === "Data & Integrations")!
    expect(dat.items.map((i) => i.id)).toEqual(["connectors", "design-source"])
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
      "goals-metrics": "Goals & metrics",
      team: "Team & roles",
      connectors: "Connectors",
      "design-source": "Prototypes",
      billing: "Billing",
      security: "Security",
    })
  })

  it("does not surface dormant ids (strategic, flags) or old ids (workspace, kpi, notifications)", () => {
    const allIds = SETTINGS_NAV.flatMap((g) => g.items).map((i) => i.id)
    expect(allIds).not.toContain("strategic")
    expect(allIds).not.toContain("flags")
    expect(allIds).not.toContain("workspace")
    expect(allIds).not.toContain("kpi")
    expect(allIds).not.toContain("notifications")
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
