// @vitest-environment jsdom
//
// Sidebar nav-wiring DOM tests.
//
// After the brief/chat unification, the home surface (`/`, ChatScreen) defaults
// to the pinned Top Insights tab on a fresh load. So the sidebar "New chat" `+`
// must NOT use the plain goTo("chat") nav (that would land on the brief) — it
// uses goToNewChat() (→ `/?new=1`, consumed by ChatScreen to start a fresh chat).
// The "Top Insights" and "All chats" rail items keep their plain goTo() nav.
//
// These tests mount the REAL Sidebar, mocking only the context boundaries it
// reads, and assert the click→nav wiring (not a re-implementation).
import * as React from "react"
import { cleanup, fireEvent, render, screen } from "@testing-library/react"
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest"

;(globalThis as typeof globalThis & { React?: typeof React }).React = React

const goTo = vi.fn()
const goToNewChat = vi.fn()
const goToWorkbench = vi.fn()
const openPalette = vi.fn()
const toggleSidebar = vi.fn()
let sidebarCollapsed = true

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
  goToWorkbench.mockClear()
  openPalette.mockClear()
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

  it("'Top Insights' and 'All chats' rail items keep their plain goTo() nav", () => {
    render(React.createElement(Sidebar))
    fireEvent.click(screen.getByLabelText("Top Insights"))
    expect(goTo).toHaveBeenCalledWith("brief")
    fireEvent.click(screen.getByLabelText("Chat history"))
    expect(goTo).toHaveBeenCalledWith("chats")
    // The new-chat helper was not triggered by either.
    expect(goToNewChat).not.toHaveBeenCalled()
  })
})

// ── Workbench: hidden from the rail (2026-08-07) ─────────────────────────────
// The Workbench trigger is commented out of the rail on a product call, the
// same way Search is. The surface behind it is NOT gone: goToWorkbench and the
// `/?tab=last` one-shot ChatScreen consumes are untouched, so these tests only
// assert the rail no longer offers the door — never that the tab stopped
// working. Top Insights keeps its own, separate door to the pinned brief tab.
describe("Sidebar — Workbench (hidden)", () => {
  it("no longer renders the Workbench trigger", () => {
    render(React.createElement(Sidebar))
    expect(screen.queryByTestId("sidebar-workbench")).toBeNull()
    expect(screen.queryByLabelText("Workbench")).toBeNull()
    expect(goToWorkbench).not.toHaveBeenCalled()
  })

  it("Top Insights is now the first rail item, and still routes to the pinned brief tab", () => {
    const { container } = render(React.createElement(Sidebar))
    const labels = Array.from(container.querySelectorAll(".sb-rail-nav .sb-rail-label")).map(
      (el) => el.textContent,
    )
    expect(labels[0]).toBe("Top Insights")
    fireEvent.click(screen.getByLabelText("Top Insights"))
    expect(goTo).toHaveBeenCalledWith("brief")
    expect(goToWorkbench).not.toHaveBeenCalled()
  })
})

// ── Shell restyle: every nav affordance is preserved ──────────────────────────
// The visual restyle of the rail must NOT drop any nav entry. This guards the
// full set so a future CSS/markup change can't silently remove one. Sign out
// deliberately does NOT appear here: it moved to Settings → Account, and the
// rail's user row is display-only.
describe("Sidebar — nav affordances preserved after restyle", () => {
  // The rail's Search trigger is hidden for now (product call, 2026-07-31). The
  // palette is NOT removed: AppShell still renders it and owns ⌘K, so this only
  // asserts the button is absent — never that search stopped working.
  it("no longer renders the Search trigger (palette + ⌘K are untouched)", () => {
    render(React.createElement(Sidebar))
    expect(screen.queryByTestId("palette-trigger")).toBeNull()
    expect(screen.queryByLabelText("Search (Ctrl+K)")).toBeNull()
    expect(openPalette).not.toHaveBeenCalled()
  })

  // Workbench is deliberately absent from this list — it is hidden on a product
  // call (2026-08-07), guarded by the "Workbench (hidden)" suite above.
  it("renders New chat, Top Insights, All chats, Templates, Guide, Settings + Feedback", () => {
    render(React.createElement(Sidebar))
    for (const label of [
      "New chat",
      "Top Insights",
      "Chat history",
      "Ideation",
      "Templates",
      "Guide",
      "Settings",
      "Feedback",
    ]) {
      expect(screen.getByLabelText(label)).toBeTruthy()
    }
  })

  // Templates came back on the rail with artifact formats (2026-08): the screen
  // now decides what every PRD, ticket and engineering spec Sprntly writes
  // LOOKS like, not only which finished examples it reads for voice. That is a
  // setting a PM sets up once and returns to, so it needs a door of its own —
  // while the screen was exemplars-only the item stayed commented out.
  it("renders the Templates rail item and navigates to the /templates screen", () => {
    render(React.createElement(Sidebar))
    const item = screen.getByLabelText("Templates")
    expect(item).toBeTruthy()
    fireEvent.click(item)
    // goTo("templates") is what routes.ts maps to "/templates"; the label
    // deliberately still matches ScreenId, MAIN_CHROME_TITLE and the palette.
    expect(goTo).toHaveBeenCalledWith("templates")
    expect(goToNewChat).not.toHaveBeenCalled()
  })

  it("Guide is an anchor to the public /docs site (not a goTo screen), opening safely in a new tab", () => {
    render(React.createElement(Sidebar))
    const guide = screen.getByTestId("sidebar-guide-link") as HTMLAnchorElement
    expect(guide.tagName).toBe("A")
    expect(guide.getAttribute("href")).toBe("/docs")
    expect(guide.getAttribute("target")).toBe("_blank")
    expect(guide.getAttribute("rel")).toBe("noopener noreferrer")
    // It navigates via the anchor, never through the SPA screen router.
    fireEvent.click(guide)
    expect(goTo).not.toHaveBeenCalled()
    expect(goToNewChat).not.toHaveBeenCalled()
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
