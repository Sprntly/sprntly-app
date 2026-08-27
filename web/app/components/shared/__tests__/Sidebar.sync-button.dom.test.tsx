// @vitest-environment jsdom
//
// Sync-your-data button (2026-08-13) — the rail's user row carries the one
// global "run the pipeline now" trigger, reachable from every screen in both
// rail modes. These tests mount the REAL Sidebar with usePipelineStatus
// mocked at the module boundary (the polling loop is the hook's own concern)
// and assert the button's wiring and states:
//
//   - idle       → click fires triggerRun
//   - running    → spinner class, clicks are swallowed (server coalesces too,
//                  but the UI shouldn't even ask)
//   - completed  → transient check/done state
//   - failed     → failed tint, click retries
//   - no company → disabled (nothing to sync against yet)
//
// The user row's IDENTITY chrome must stay display-only. The row carries three
// actions now — sync, feedback, settings, moved down from the bottom rail block
// — but the avatar and name are inert, and sign-out lives in Settings → Account
// rather than behind a click on your own face.
import { readFileSync } from "node:fs"
import { dirname, join } from "node:path"
import { fileURLToPath } from "node:url"
import * as React from "react"
import { cleanup, fireEvent, render, screen } from "@testing-library/react"
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest"

;(globalThis as typeof globalThis & { React?: typeof React }).React = React

const goTo = vi.fn()
const goToNewChat = vi.fn()
const goToWorkbench = vi.fn()
const openPalette = vi.fn()
const toggleSidebar = vi.fn()
let sidebarCollapsed = false

vi.mock("../../../context/NavigationContext", () => ({
  useNavigation: () => ({
    currentScreen: "brief",
    goTo,
    goToNewChat,
    goToWorkbench,
    openPalette,
    sidebarCollapsed,
    toggleSidebar,
  }),
}))

vi.mock("../../../context/ContentContext", () => ({
  useContent: () => ({ content: {} }),
}))

vi.mock("../../../lib/auth", () => ({
  useAuth: () => ({ kind: "anonymous", signOut: vi.fn() }),
}))

vi.mock("../../../context/WorkspaceContext", () => ({
  profileDisplayName: () => "Ada Lovelace",
  useWorkspace: () => ({
    profile: null,
    workspace: null,
    workspaces: [],
    activeWorkspace: null,
    orgRole: null,
    setActiveWorkspace: vi.fn(),
    refresh: vi.fn(),
  }),
}))

const triggerRun = vi.fn()
let hookState: {
  runStatus: { status: string; completed_at: string | null } | null
  isTriggering: boolean
  showCompleted: boolean
} = { runStatus: null, isTriggering: false, showCompleted: false }

vi.mock("../../../lib/usePipelineStatus", () => ({
  usePipelineStatus: (company: string) => {
    hookCompanyArg = company
    return { ...hookState, triggerRun }
  },
}))
let hookCompanyArg: string | null = null

import { Sidebar } from "../Sidebar"

beforeEach(() => {
  goTo.mockClear()
  triggerRun.mockClear()
  sidebarCollapsed = false
  hookCompanyArg = null
  hookState = { runStatus: null, isTriggering: false, showCompleted: false }
})
afterEach(() => cleanup())

describe("Sidebar — sync-your-data button", () => {
  it("renders in the user row and passes the active company to the status hook", () => {
    const { container } = render(React.createElement(Sidebar, { activeCompany: "acme" }))
    const btn = screen.getByTestId("sidebar-sync")
    expect(container.querySelector(".sb-rail-user .sb-rail-actions .sb-sync-btn")).toBe(btn)
    expect(hookCompanyArg).toBe("acme")
  })

  it("idle: click fires triggerRun (the backend collapses duplicates server-side)", () => {
    render(React.createElement(Sidebar, { activeCompany: "acme" }))
    fireEvent.click(screen.getByTestId("sidebar-sync"))
    expect(triggerRun).toHaveBeenCalledTimes(1)
    // A sync trigger is not a navigation.
    expect(goTo).not.toHaveBeenCalled()
  })

  it("running: shows the spinner state and swallows clicks instead of re-triggering", () => {
    hookState = {
      runStatus: { status: "running", completed_at: null },
      isTriggering: false,
      showCompleted: false,
    }
    render(React.createElement(Sidebar, { activeCompany: "acme" }))
    const btn = screen.getByTestId("sidebar-sync")
    expect(btn.className).toContain("sb-sync-btn--running")
    expect(btn.getAttribute("aria-busy")).toBe("true")
    fireEvent.click(btn)
    expect(triggerRun).not.toHaveBeenCalled()
  })

  it("triggering (request in flight, before the first running poll) also swallows clicks", () => {
    hookState = { runStatus: null, isTriggering: true, showCompleted: false }
    render(React.createElement(Sidebar, { activeCompany: "acme" }))
    const btn = screen.getByTestId("sidebar-sync")
    expect(btn.className).toContain("sb-sync-btn--running")
    fireEvent.click(btn)
    expect(triggerRun).not.toHaveBeenCalled()
  })

  it("completed flash: shows the done state", () => {
    hookState = {
      runStatus: { status: "completed", completed_at: new Date().toISOString() },
      isTriggering: false,
      showCompleted: true,
    }
    render(React.createElement(Sidebar, { activeCompany: "acme" }))
    expect(screen.getByTestId("sidebar-sync").className).toContain("sb-sync-btn--done")
  })

  it("failed: shows the failed tint and a click retries", () => {
    hookState = {
      runStatus: { status: "failed", completed_at: null },
      isTriggering: false,
      showCompleted: false,
    }
    render(React.createElement(Sidebar, { activeCompany: "acme" }))
    const btn = screen.getByTestId("sidebar-sync")
    expect(btn.className).toContain("sb-sync-btn--failed")
    expect(btn.getAttribute("title")).toContain("failed")
    fireEvent.click(btn)
    expect(triggerRun).toHaveBeenCalledTimes(1)
  })

  it("no active company: the button is disabled and inert", () => {
    render(React.createElement(Sidebar))
    const btn = screen.getByTestId("sidebar-sync") as HTMLButtonElement
    expect(btn.disabled).toBe(true)
    fireEvent.click(btn)
    expect(triggerRun).not.toHaveBeenCalled()
  })

  it("stays present when the rail is collapsed (stacks with the avatar)", () => {
    sidebarCollapsed = true
    const { container } = render(React.createElement(Sidebar, { activeCompany: "acme" }))
    expect(container.querySelector(".sidebar--collapsed")).toBeTruthy()
    expect(container.querySelector(".sb-rail-user .sb-sync-btn")).toBeTruthy()
  })

  it("the row's interactive elements are its three ACTIONS, and nothing else", () => {
    // This used to assert exactly one — the sync button — and the intent it
    // protects is that the IDENTITY chrome stays display-only: the avatar and
    // name were once a sign-out target, and clicking your own face to sign out
    // is how people sign out by accident.
    //
    // Feedback and Settings joined the row deliberately (they came off the
    // bottom rail block), so the count moved. The invariant did not: name the
    // three, and prove the avatar and username are still inert.
    const { container } = render(React.createElement(Sidebar, { activeCompany: "acme" }))
    const row = container.querySelector(".sb-rail-user")!
    const actions = row.querySelector(".sb-rail-actions")!

    // Three actions, in the order they read left to right.
    expect(Array.from(actions.querySelectorAll("button, a")).map(
      (el) => el.getAttribute("aria-label"),
    )).toEqual(["Sync your data", "Feedback", "Settings"])

    // And they are ALL of the row's interactive elements — nothing has been
    // added beside them.
    expect(row.querySelectorAll("button, a").length).toBe(3)

    for (const selector of [".sb-rail-avatar", ".sb-rail-username"]) {
      const el = row.querySelector(selector)!
      expect(el.tagName.toLowerCase(), `${selector} became interactive`).toBe("span")
      expect(el.closest("button, a")).toBeNull()
    }
  })

  it("the initials chip is a fixed circle — the NAME is what truncates", () => {
    // The reported bug: as the row ran out of room the "FT" chip squashed into
    // an oval. A flex child defaults to `flex-shrink: 1`, so the 32px box gave
    // way before the text did — and a squashed box with `border-radius: 50%`
    // is not a circle. jsdom computes no layout, so this asserts the RULE.
    const css = readFileSync(
      join(dirname(fileURLToPath(import.meta.url)), "..", "..", "..", "globals.css"),
      "utf8",
    )
    const avatar = /^\s*\.sb-rail-avatar \{([^}]*)\}/m.exec(css)?.[1] ?? ""
    expect(avatar, ".sb-rail-avatar rule not found").not.toBe("")
    expect(avatar).toMatch(/flex:\s*none/)
    expect(avatar).toMatch(/width:\s*32px/)
    expect(avatar).toMatch(/height:\s*32px/)

    // …and the name is the flexible one, so it takes whatever is left and
    // ellipsizes into it as the rail is dragged.
    const username = /^\s*\.sb-rail-username \{([^}]*)\}/m.exec(css)?.[1] ?? ""
    expect(username).toMatch(/text-overflow:\s*ellipsis/)
    expect(username).toMatch(/min-width:\s*0/)
  })
})
