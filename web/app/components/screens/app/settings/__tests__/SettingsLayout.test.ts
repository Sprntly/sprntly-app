import { readFileSync } from "node:fs"
import { dirname, join } from "node:path"
import { fileURLToPath } from "node:url"
import { describe, expect, it } from "vitest"
import { LEAF_LABELS, SETTINGS_NAV, SETTINGS_PANES } from "../SettingsLayout"

describe("SETTINGS_NAV — design-3 grouped structure (commit B)", () => {
  it("is a short list of panes, not a long list of settings", () => {
    // Fifteen rows was a list you read rather than scanned. The two worst were
    // Workspace (six) and Account (three) — rows visited rarely and never
    // together, which is exactly what a pane nav is for.
    expect(SETTINGS_NAV.flatMap((g) => g.items).length).toBeLessThanOrEqual(9)
    expect(SETTINGS_NAV.map((g) => g.groupLabel)).toEqual([
      "You",
      "Workspace",
      "How Sprntly writes",
      "Data & Integrations",
      "Account",
    ])
  })

  it("carries Templates and Skills as panes of its own", () => {
    // They came off the main nav — both are set-up-once-and-return-to, which
    // is what Settings is for. They render EMBEDDED (no AppLayout of their
    // own), so the settings nav stays on screen the way it does for every
    // other row; a screen bringing its own layout would draw a second sidebar
    // over this one.
    const group = SETTINGS_NAV.find((g) => g.groupLabel === "How Sprntly writes")!
    expect(group.items.map((i) => i.id)).toEqual(["templates", "skills"])
    expect(group.items.every((i) => i.available)).toBe(true)
  })

  it("gives Templates and Skills the full pane width", () => {
    // `.pset-body` caps a pane at 860px. Their card grids are
    // `repeat(auto-fill, minmax(280px, 1fr))`, so under that cap they lay out
    // two across with the right third of the pane empty — and the screen's
    // generic pane bar stacks a second header over the one each already draws.
    // Both are fixed by the same list.
    const source = readFileSync(
      join(dirname(fileURLToPath(import.meta.url)), "..", "..", "SettingsScreen.tsx"),
      "utf8",
    )
    const block = source.slice(
      source.indexOf("const FULL_BLEED_SECTIONS"),
      source.indexOf("])", source.indexOf("const FULL_BLEED_SECTIONS")),
    )
    expect(block).toContain('"templates"')
    expect(block).toContain('"skills"')
  })

  it("every row is a section the screen can actually render", () => {
    // A nav row whose id has no case in `renderSection` falls through to
    // Profile: the row highlights and the pane beside it shows somebody's
    // account settings.
    const source = readFileSync(
      join(dirname(fileURLToPath(import.meta.url)), "..", "..", "SettingsScreen.tsx"),
      "utf8",
    )
    for (const item of SETTINGS_NAV.flatMap((g) => g.items)) {
      expect(source, `no renderSection case for "${item.id}"`).toContain(
        `case "${item.id}":`,
      )
    }
  })

  it("LOSES NOTHING — every setting that existed is still reachable", () => {
    // The whole risk of a consolidation. A pane owns its leaves; a standalone
    // row is its own. Between them they must still cover every section the
    // flat nav had, or a setting became unreachable to anyone who did not
    // already know its URL.
    const BEFORE = [
      "profile", "comms-brief", "workspaces",
      "product-category", "company-profile", "process", "metrics",
      "business-context", "team",
      "templates", "skills",
      "connectors", "mcp",
      "billing", "security", "admin",
    ]
    const reachable = new Set(
      SETTINGS_NAV.flatMap((g) => g.items).flatMap((row) => {
        const pane = SETTINGS_PANES.find((p) => p.id === row.id)
        return pane ? pane.leaves : [row.id]
      }),
    )
    expect(BEFORE.filter((id) => !reachable.has(id))).toEqual([])
  })

  it("keeps every `?section=` link working — a row's id is one of its leaves", () => {
    // Six places in this app deep-link to `?section=connectors` and
    // `?section=team`, and app_map hands those links to customers. A row named
    // after an id that is not a real section would land them on Profile.
    for (const pane of SETTINGS_PANES) {
      expect(pane.leaves, `${pane.label} has no leaves`).not.toHaveLength(0)
      expect(pane.leaves[0], `${pane.label}'s row id is not its first leaf`).toBe(pane.id)
    }
  })

  it("no section belongs to two panes", () => {
    // `paneFor` returns the first match, so an id in two panes would resolve
    // arbitrarily and highlight the wrong row.
    const seen = SETTINGS_PANES.flatMap((p) => p.leaves)
    expect(new Set(seen).size).toBe(seen.length)
  })

  it("names every leaf it shows in a pane nav", () => {
    // The row label is the pane's ("Company"); the sub-nav needs the view's
    // own ("Product & Category"). A missing one renders a raw id.
    for (const leaf of SETTINGS_PANES.flatMap((p) => p.leaves)) {
      expect(LEAF_LABELS[leaf], `no label for "${leaf}"`).toBeTruthy()
    }
  })

  it("uses pane labels on the rows, and the view's own name inside", () => {
    const byId = Object.fromEntries(
      SETTINGS_NAV.flatMap((g) => g.items).map((i) => [i.id, i.label]),
    )
    expect(byId).toEqual({
      profile: "Profile",
      workspaces: "Workspaces",
      "product-category": "Company",
      team: "Team & process",
      templates: "Templates",
      skills: "Skills",
      connectors: "Integrations",
      billing: "Account",
    })
    // …and the leaf keeps its own name where it is actually shown.
    expect(LEAF_LABELS["product-category"]).toBe("Product & Category")
    expect(LEAF_LABELS.billing).toBe("Billing")
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
