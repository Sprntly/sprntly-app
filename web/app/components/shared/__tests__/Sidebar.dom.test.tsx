// @vitest-environment jsdom
//
// Sidebar nav-wiring DOM tests.
//
// After the brief/chat unification, the home surface (`/`, ChatScreen) defaults
// to the pinned Weekly-brief tab on a fresh load. So the sidebar "New chat" `+`
// must NOT use the plain goTo("chat") nav (that would land on the brief) — it
// uses goToNewChat() (→ `/?new=1`, consumed by ChatScreen to start a fresh chat).
// The "Weekly brief" and "All chats" rail items keep their plain goTo() nav.
//
// These tests mount the REAL Sidebar, mocking only the context boundaries it
// reads, and assert the click→nav wiring (not a re-implementation).
import * as React from "react"
import { cleanup, fireEvent, render, screen } from "@testing-library/react"
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest"

;(globalThis as typeof globalThis & { React?: typeof React }).React = React

const goTo = vi.fn()
const goToNewChat = vi.fn()
const toggleSidebar = vi.fn()
let sidebarCollapsed = true

vi.mock("../../../context/NavigationContext", () => ({
  useNavigation: () => ({ currentScreen: "brief", goTo, goToNewChat, sidebarCollapsed, toggleSidebar }),
}))

vi.mock("../../../context/ContentContext", () => ({
  useContent: () => ({ content: {} }),
}))

vi.mock("../../../lib/auth", () => ({
  useAuth: () => ({ kind: "anonymous", signOut: vi.fn() }),
}))

const setActiveWorkspace = vi.fn()
let workspacesState: Array<{
  id: string
  name: string
  slug: string
  is_default: boolean
  product_id: string | null
  dataset: string | null
  role: string
}> = []
let activeWorkspaceState: (typeof workspacesState)[number] | null = null
// Company-level role — workspace creation gates on THIS, not the
// per-workspace effective role each summary row carries.
let orgRoleState: string | null = null

vi.mock("../../../context/WorkspaceContext", () => ({
  profileDisplayName: () => "Ada Lovelace",
  useWorkspace: () => ({
    profile: null,
    workspace: null,
    workspaces: workspacesState,
    activeWorkspace: activeWorkspaceState,
    orgRole: orgRoleState,
    setActiveWorkspace,
    refresh: vi.fn(),
  }),
}))

import { Sidebar } from "../Sidebar"

beforeEach(() => {
  goTo.mockClear()
  goToNewChat.mockClear()
  toggleSidebar.mockClear()
  setActiveWorkspace.mockClear()
  sidebarCollapsed = true
  workspacesState = []
  activeWorkspaceState = null
  orgRoleState = null
})
afterEach(() => cleanup())

describe("Sidebar — New chat wiring", () => {
  it("'New chat' uses goToNewChat (fresh chat), never goTo('chat') (would land on brief)", () => {
    render(React.createElement(Sidebar))
    fireEvent.click(screen.getByLabelText("New chat"))
    expect(goToNewChat).toHaveBeenCalledTimes(1)
    expect(goTo).not.toHaveBeenCalledWith("chat")
  })

  it("'Weekly brief' and 'All chats' rail items keep their plain goTo() nav", () => {
    render(React.createElement(Sidebar))
    fireEvent.click(screen.getByLabelText("Weekly brief"))
    expect(goTo).toHaveBeenCalledWith("brief")
    fireEvent.click(screen.getByLabelText("Chat history"))
    expect(goTo).toHaveBeenCalledWith("chats")
    // The new-chat helper was not triggered by either.
    expect(goToNewChat).not.toHaveBeenCalled()
  })
})

// ── Shell restyle: every nav affordance is preserved ──────────────────────────
// The visual restyle of the rail must NOT drop any nav entry. This guards the
// full set so a future CSS/markup change can't silently remove one. Sign out
// deliberately does NOT appear here: it moved to Settings → Account, and the
// rail's user row is display-only.
describe("Sidebar — nav affordances preserved after restyle", () => {
  it("renders New chat, Weekly brief, All chats, Settings + Feedback", () => {
    render(React.createElement(Sidebar))
    for (const label of [
      "New chat",
      "Weekly brief",
      "Chat history",
      "Ideation",
      "Settings",
      "Feedback",
    ]) {
      expect(screen.getByLabelText(label)).toBeTruthy()
    }
  })

  it("no longer renders a Sources rail item (hidden from the rail; screen + route kept)", () => {
    render(React.createElement(Sidebar))
    expect(screen.queryByLabelText("Sources")).toBeNull()
  })

  it("no longer renders a Sign out affordance (it lives in Settings → Account)", () => {
    render(React.createElement(Sidebar))
    expect(screen.queryByLabelText("Sign out")).toBeNull()
  })

  it("renders the Ideation rail icon (restored to the nav)", () => {
    render(React.createElement(Sidebar))
    fireEvent.click(screen.getByLabelText("Ideation"))
    expect(goTo).toHaveBeenCalledWith("ideation")
  })

  it("Feedback opens the feedback modal (not a nav)", () => {
    render(React.createElement(Sidebar))
    fireEvent.click(screen.getByLabelText("Feedback"))
    // Feedback is a modal trigger, not a screen nav.
    expect(goTo).not.toHaveBeenCalled()
    expect(goToNewChat).not.toHaveBeenCalled()
  })

  it("renders the brand mark with its accent dot", () => {
    const { container } = render(React.createElement(Sidebar))
    expect(container.querySelector(".sb-rail-logo-dot")).toBeTruthy()
  })
})

// ── Workspace switcher (multi-workspace 2026-07) ─────────────────────────────
describe("Sidebar — workspace switcher", () => {
  const twoWorkspaces = () => {
    workspacesState = [
      { id: "ws-a", name: "Acme App", slug: "default", is_default: true, product_id: null, dataset: "acme", role: "admin" },
      { id: "ws-b", name: "Notifications", slug: "notifications", is_default: false, product_id: null, dataset: "acme--notifications", role: "admin" },
    ]
    activeWorkspaceState = workspacesState[0]
    orgRoleState = "admin"
    sidebarCollapsed = false
  }

  it("shows the active workspace name as the brand and opens the menu", () => {
    twoWorkspaces()
    render(React.createElement(Sidebar))
    const trigger = screen.getByTestId("workspace-switcher")
    expect(trigger.textContent).toContain("Acme App")
    fireEvent.click(trigger)
    expect(screen.getByText("Notifications")).toBeTruthy()
  })

  it("selecting a workspace calls setActiveWorkspace and closes the menu", () => {
    twoWorkspaces()
    const { container } = render(React.createElement(Sidebar))
    fireEvent.click(screen.getByTestId("workspace-switcher"))
    fireEvent.click(screen.getByText("Notifications"))
    expect(setActiveWorkspace).toHaveBeenCalledWith("ws-b")
    expect(container.querySelector(".sb-ws-menu")).toBeNull()
  })

  it("org admins see '+ New workspace'; the trigger is static for a lone non-admin workspace", () => {
    twoWorkspaces()
    render(React.createElement(Sidebar))
    fireEvent.click(screen.getByTestId("workspace-switcher"))
    expect(screen.getByText("+ New workspace")).toBeTruthy()
    cleanup()

    workspacesState = [
      { id: "ws-a", name: "Acme App", slug: "default", is_default: true, product_id: null, dataset: "acme", role: "member" },
    ]
    activeWorkspaceState = workspacesState[0]
    orgRoleState = "member"
    const { container } = render(React.createElement(Sidebar))
    expect(
      container.querySelector(".sb-ws-trigger--static"),
    ).toBeTruthy()
  })

  it("a WORKSPACE-level admin who is a plain org member gets no create button (org-admin gated)", () => {
    twoWorkspaces()
    // Effective role on the rows is admin, but the company-level role is not.
    orgRoleState = "member"
    render(React.createElement(Sidebar))
    fireEvent.click(screen.getByTestId("workspace-switcher"))
    expect(screen.queryByText("+ New workspace")).toBeNull()
  })

  it("an org OWNER sees the create button (owner ⊇ admin)", () => {
    twoWorkspaces()
    orgRoleState = "owner"
    render(React.createElement(Sidebar))
    fireEvent.click(screen.getByTestId("workspace-switcher"))
    expect(screen.getByText("+ New workspace")).toBeTruthy()
  })
})

// ── Expand / collapse toggle ──────────────────────────────────────────────────
describe("Sidebar — expand/collapse toggle", () => {
  it("renders the collapsed rail and an Expand control that fires toggleSidebar", () => {
    sidebarCollapsed = true
    const { container } = render(React.createElement(Sidebar))
    expect(container.querySelector(".sidebar--collapsed")).toBeTruthy()
    fireEvent.click(screen.getByLabelText("Expand sidebar"))
    expect(toggleSidebar).toHaveBeenCalledTimes(1)
  })

  it("renders the expanded rail with a Collapse control when not collapsed", () => {
    sidebarCollapsed = false
    const { container } = render(React.createElement(Sidebar))
    expect(container.querySelector(".sidebar--expanded")).toBeTruthy()
    fireEvent.click(screen.getByLabelText("Collapse sidebar"))
    expect(toggleSidebar).toHaveBeenCalledTimes(1)
  })
})
