// @vitest-environment jsdom
//
// Projects rail item is gated behind NEXT_PUBLIC_PROJECTS_ENABLED (build-time
// cosmetic gate — the real dark-guarantee is the backend's request-time
// PROJECTS_ENABLED 404). Mounts the REAL Sidebar, mocking only the context
// boundaries it reads, same shape as Sidebar.dom.test.tsx.
import * as React from "react"
import { cleanup, render, screen } from "@testing-library/react"
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest"

;(globalThis as typeof globalThis & { React?: typeof React }).React = React

const goTo = vi.fn()
const goToNewChat = vi.fn()
const goToWorkbench = vi.fn()
const openPalette = vi.fn()
const toggleSidebar = vi.fn()

vi.mock("../../../context/NavigationContext", () => ({
  useNavigation: () => ({
    currentScreen: "brief",
    goTo,
    goToNewChat,
    goToWorkbench,
    openPalette,
    sidebarCollapsed: true,
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

import { Sidebar } from "../Sidebar"

beforeEach(() => {
  goTo.mockClear()
})
afterEach(() => {
  cleanup()
  vi.unstubAllEnvs()
})

describe("Sidebar — Projects rail item gated by NEXT_PUBLIC_PROJECTS_ENABLED", () => {
  it("hides the Projects rail item when the flag is off (unset)", () => {
    vi.stubEnv("NEXT_PUBLIC_PROJECTS_ENABLED", "")
    render(React.createElement(Sidebar))
    expect(screen.queryByLabelText("Projects")).toBeNull()
  })

  it("shows exactly one Projects rail item when the flag is on", () => {
    vi.stubEnv("NEXT_PUBLIC_PROJECTS_ENABLED", "1")
    render(React.createElement(Sidebar))
    expect(screen.getAllByLabelText("Projects")).toHaveLength(1)
  })
})
