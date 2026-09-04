// @vitest-environment jsdom
//
// The settings nav is ONE column. A row that owns several views opens under
// itself; nothing navigational lives on the right edge of the screen any more.
//
// What these lock down is the part that is easy to break twice: every leaf is
// still a real `?section=` link (six places in this app deep-link to
// `?section=connectors` and `?section=team`), and the drawer follows the URL
// rather than only the click — a command-palette jump to `?section=metrics` has
// to arrive with Company already open. (This used `?section=mcp` and
// Integrations until that pane was dissolved: Connectors and MCP Access are
// standalone rows now and open no drawer, so they can no longer stand as the
// example of one.)
import * as React from "react"
import { cleanup, fireEvent, render, screen } from "@testing-library/react"
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest"

;(globalThis as typeof globalThis & { React?: typeof React }).React = React

let sectionParam: string | null = null
const replace = vi.fn((url: string) => {
  sectionParam = new URL(url, "http://x").searchParams.get("section")
})

vi.mock("next/navigation", () => ({
  useRouter: () => ({ replace, push: vi.fn(), refresh: vi.fn() }),
  useSearchParams: () => new URLSearchParams(sectionParam ? `section=${sectionParam}` : ""),
}))

vi.mock("../../AppLayout", () => ({
  AppLayout: ({ children }: { children?: React.ReactNode }) => <div>{children}</div>,
}))

vi.mock("../../../../../lib/auth", () => ({
  useAuth: () => ({ kind: "user", signOut: vi.fn() }),
}))

vi.mock("../../../../../context/WorkspaceContext", () => ({
  profileDisplayName: () => "Ada Lovelace",
  useWorkspace: () => ({ workspace: { display_name: "Acme" }, profile: null }),
}))

// Every pane body is irrelevant here — the nav is the subject, and the real
// panes drag in the whole API client.
for (const mod of [
  "ProfileSettings", "WorkspaceSettings", "CompanyProfileSettings", "ProcessSettings",
  "KpiSettings", "WorkspacesSettings", "BusinessContextSettings", "StrategicSettings",
  "FeatureFlagsSettings", "NotificationsSettings", "BillingSettings", "SecuritySettings",
  "AdminSettings", "ConnectorsSettings", "McpSettings", "TeamSettings",
]) {
  vi.doMock(`../${mod}`, () => ({ [mod]: () => <div data-testid={`pane-${mod}`} /> }))
}
vi.mock("../../TemplatesScreen", () => ({ TemplatesScreen: () => <div /> }))
vi.mock("../../SkillsScreen", () => ({ SkillsScreen: () => <div /> }))

const { SettingsScreen } = await import("../../SettingsScreen")

const drawerOf = (rowId: string) =>
  screen.getByTestId(`settings-nav-${rowId}`).nextElementSibling as HTMLElement

beforeEach(() => {
  sectionParam = null
  replace.mockClear()
})
afterEach(() => cleanup())

describe("a settings row is a dropdown, not a second nav on the right", () => {
  it("puts no pane rail on the right at all", () => {
    // The whole point of the change: `.setpane-nav` was the rail on the far
    // side of the pane, and finding "MCP Access" meant discovering it.
    const { container } = render(<SettingsScreen />)
    expect(container.querySelector(".setpane-nav")).toBeNull()
  })

  it("opens the row you are inside, and marks it expanded", () => {
    render(<SettingsScreen />)   // no ?section= → Profile
    const row = screen.getByTestId("settings-nav-profile")
    expect(row.getAttribute("aria-expanded")).toBe("true")
    expect(drawerOf("profile").getAttribute("data-open")).toBe("true")
  })

  it("leaves every other row shut", () => {
    // Only rows that OWN a multi-view pane. `connectors` was in this list until
    // the Integrations pane was dissolved; it is standalone now and carries no
    // aria-expanded at all, which the next case asserts instead.
    render(<SettingsScreen />)
    for (const id of ["product-category", "team", "billing"]) {
      expect(screen.getByTestId(`settings-nav-${id}`).getAttribute("aria-expanded")).toBe("false")
    }
  })

  it("gives a row with only one view no dropdown at all", () => {
    // Workspaces stands alone. A one-item drawer is furniture.
    render(<SettingsScreen />)
    expect(
      screen.getByTestId("settings-nav-workspaces").getAttribute("aria-expanded"),
    ).toBeNull()
  })

  it("shows every one of a pane's views, by their own labels", () => {
    render(<SettingsScreen />)
    fireEvent.click(screen.getByTestId("settings-nav-product-category"))
    expect(
      Array.from(drawerOf("product-category").querySelectorAll("button")).map(
        (b) => b.textContent,
      ),
    ).toEqual(["Product & Category", "Company Profile", "Business Context", "Metrics"])
  })

  it("a leaf still navigates to its own ?section= — the deep links are the contract", () => {
    render(<SettingsScreen />)
    fireEvent.click(screen.getByTestId("settings-nav-product-category"))
    fireEvent.click(screen.getByTestId("settings-nav-leaf-metrics"))
    expect(replace).toHaveBeenLastCalledWith("/settings?section=metrics", { scroll: false })
  })

  it("a dissolved pane's rows navigate straight there, with no drawer", () => {
    // Connectors and MCP Access were the two leaves of an "Integrations" pane.
    // They are rows now, so clicking one goes to its `?section=` directly —
    // and neither is expandable, because there is nothing under it.
    render(<SettingsScreen />)
    for (const id of ["connectors", "mcp"]) {
      const row = screen.getByTestId(`settings-nav-${id}`)
      expect(row.getAttribute("aria-expanded"), `${id} still opens a drawer`).toBeNull()
      fireEvent.click(row)
      expect(replace).toHaveBeenLastCalledWith(`/settings?section=${id}`, { scroll: false })
    }
  })

  it("opening a row you are not in also takes you to its first view", () => {
    render(<SettingsScreen />)
    fireEvent.click(screen.getByTestId("settings-nav-billing"))
    expect(replace).toHaveBeenLastCalledWith("/settings?section=billing", { scroll: false })
  })

  it("shutting the row you ARE in does not navigate anywhere", () => {
    // Tidying the nav is not a request to go somewhere.
    render(<SettingsScreen />)
    fireEvent.click(screen.getByTestId("settings-nav-profile"))
    expect(drawerOf("profile").getAttribute("data-open")).toBe("false")
    expect(replace).not.toHaveBeenCalled()
  })

  it("only one row is open at a time", () => {
    render(<SettingsScreen />)
    fireEvent.click(screen.getByTestId("settings-nav-team"))
    expect(drawerOf("team").getAttribute("data-open")).toBe("true")
    expect(drawerOf("profile").getAttribute("data-open")).toBe("false")
  })

  it("keeps a shut drawer out of the tab order", () => {
    render(<SettingsScreen />)
    for (const b of drawerOf("billing").querySelectorAll("button")) {
      expect(b.getAttribute("tabindex")).toBe("-1")
    }
    fireEvent.click(screen.getByTestId("settings-nav-billing"))
    for (const b of drawerOf("billing").querySelectorAll("button")) {
      expect(b.getAttribute("tabindex")).toBeNull()
    }
  })
})

describe("the drawer follows the URL, not just the click", () => {
  it("a deep link straight to a leaf arrives with its row open and the leaf current", () => {
    // Landing on a leaf with its drawer shut would leave the nav claiming you
    // were nowhere. Uses Metrics/Company since Connectors and MCP Access are
    // standalone rows now — see the standalone case below.
    sectionParam = "metrics"
    render(<SettingsScreen />)
    expect(
      screen.getByTestId("settings-nav-product-category").getAttribute("aria-expanded"),
    ).toBe("true")
    expect(
      screen.getByTestId("settings-nav-leaf-metrics").getAttribute("aria-current"),
    ).toBe("page")
  })

  it("a deep link to a STANDALONE row marks it current and opens nothing", () => {
    // `?section=connectors` is what the command palette and app_map hand a
    // customer who asks where to connect Jira — six places in the app link to
    // it — so it has to keep landing, and now land on a row rather than inside
    // a drawer.
    sectionParam = "connectors"
    render(<SettingsScreen />)
    const row = screen.getByTestId("settings-nav-connectors")
    expect(row.className).toContain("active")
    expect(row.getAttribute("aria-expanded")).toBeNull()
    // Nothing else is left hanging open behind it.
    expect(document.querySelector('[aria-expanded="true"]')).toBeNull()
  })

  it("the row highlights for ANY leaf it owns, not only the one it is named after", () => {
    sectionParam = "metrics"
    render(<SettingsScreen />)
    expect(screen.getByTestId("settings-nav-product-category").className).toContain("active")
  })
})
