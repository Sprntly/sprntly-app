// @vitest-environment jsdom
//
// The trial countdown in the rail.
//
// It exists because a countdown that lives only on the billing screen is a
// countdown nobody sees: you visit that screen once, at signup, and then not
// again until something has gone wrong. The point is that the fact travels
// with you — and that it costs a fully-paid workspace nothing.
import * as React from "react"
import { cleanup, fireEvent, render, screen } from "@testing-library/react"
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest"

;(globalThis as typeof globalThis & { React?: typeof React }).React = React

const push = vi.fn()
vi.mock("next/navigation", () => ({ useRouter: () => ({ push }) }))

let sidebarCollapsed = false
vi.mock("../../../context/NavigationContext", () => ({
  useNavigation: () => ({
    currentScreen: "brief",
    goTo: vi.fn(),
    goToNewChat: vi.fn(),
    goToWorkbench: vi.fn(),
    openPalette: vi.fn(),
    sidebarCollapsed,
    toggleSidebar: vi.fn(),
  }),
}))

vi.mock("../../../context/ContentContext", () => ({ useContent: () => ({ content: {} }) }))
vi.mock("../../../lib/auth", () => ({ useAuth: () => ({ kind: "anonymous", signOut: vi.fn() }) }))
vi.mock("../../../lib/usePipelineStatus", () => ({
  usePipelineStatus: () => ({
    runStatus: null,
    isTriggering: false,
    showCompleted: false,
    triggerRun: vi.fn(),
  }),
}))

let workspace: Record<string, unknown> | null = null
vi.mock("../../../context/WorkspaceContext", () => ({
  profileDisplayName: () => "Ada Lovelace",
  useWorkspace: () => ({
    profile: null,
    workspace,
    workspaces: [],
    activeWorkspace: null,
    orgRole: "owner",
    setActiveWorkspace: vi.fn(),
    refresh: vi.fn(),
  }),
}))

import { Sidebar } from "../Sidebar"

/** A trial ending `days` from now, as the company row stores it.
 *
 *  No padding on the interval: the countdown rounds UP, so `days + a minute`
 *  is legitimately `days + 1` and would make these fixtures lie about what
 *  they are testing. By the time the component reads the clock a few ms have
 *  passed, which puts it just under the whole number and rounds back to it. */
function trialing(days: number) {
  return {
    id: "ws-1",
    plan: "starter",
    subscription_status: "trialing",
    current_period_end: new Date(Date.now() + days * 86_400_000).toISOString(),
  }
}

beforeEach(() => {
  sidebarCollapsed = false
  workspace = null
  push.mockClear()
})
afterEach(() => cleanup())

describe("the trial countdown", () => {
  it("shows the days left while a trial is running", () => {
    workspace = trialing(6)
    render(React.createElement(Sidebar, { activeCompany: "acme" }))
    const strip = screen.getByTestId("sidebar-trial")
    expect(strip.textContent).toContain("Free trial")
    expect(strip.textContent).toContain("6 days left")
  })

  it("says day, not days, on the last one", () => {
    workspace = trialing(0.5)
    render(React.createElement(Sidebar, { activeCompany: "acme" }))
    expect(screen.getByTestId("sidebar-trial").textContent).toContain("1 day left")
  })

  it("opens billing when clicked — the question it raises is one click away", () => {
    workspace = trialing(3)
    render(React.createElement(Sidebar, { activeCompany: "acme" }))
    fireEvent.click(screen.getByTestId("sidebar-trial"))
    expect(push).toHaveBeenCalledWith("/settings?section=billing")
  })

  it("names itself to a screen reader, countdown included", () => {
    workspace = trialing(6)
    render(React.createElement(Sidebar, { activeCompany: "acme" }))
    expect(screen.getByTestId("sidebar-trial").getAttribute("aria-label")).toBe(
      "Free trial, 6 days left. Open billing.",
    )
  })

  it("keeps the number when the rail is collapsed", () => {
    // At 56px the words are gone (CSS), so the number has to be a real element
    // rather than part of a sentence — it is the only part that still reads.
    sidebarCollapsed = true
    workspace = trialing(6)
    const { container } = render(React.createElement(Sidebar, { activeCompany: "acme" }))
    expect(container.querySelector(".sidebar--collapsed")).toBeTruthy()
    expect(container.querySelector(".sb-trial-num")!.textContent).toBe("6")
  })
})

describe("the countdown costs a workspace that is not trialling nothing", () => {
  it("is absent on a paid subscription", () => {
    workspace = { id: "ws-1", plan: "starter", subscription_status: "active" }
    render(React.createElement(Sidebar, { activeCompany: "acme" }))
    expect(screen.queryByTestId("sidebar-trial")).toBeNull()
  })

  it("is absent on a plan that was never sold through Stripe", () => {
    workspace = { id: "ws-1", plan: "legacy", subscription_status: null }
    render(React.createElement(Sidebar, { activeCompany: "acme" }))
    expect(screen.queryByTestId("sidebar-trial")).toBeNull()
  })

  it("is absent when there is no workspace at all", () => {
    workspace = null
    render(React.createElement(Sidebar, { activeCompany: "acme" }))
    expect(screen.queryByTestId("sidebar-trial")).toBeNull()
  })

  it("is absent when a trialling row carries no end date to count to", () => {
    // Falls silent rather than rendering "NaN days left".
    workspace = { id: "ws-1", plan: "starter", subscription_status: "trialing", current_period_end: null }
    render(React.createElement(Sidebar, { activeCompany: "acme" }))
    expect(screen.queryByTestId("sidebar-trial")).toBeNull()
  })
})
