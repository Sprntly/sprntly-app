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

vi.mock("../../../context/WorkspaceContext", () => ({
  profileDisplayName: () => "Ada Lovelace",
  useWorkspace: () => ({ profile: null, workspace: null }),
}))

import { Sidebar } from "../Sidebar"

beforeEach(() => {
  goTo.mockClear()
  goToNewChat.mockClear()
  toggleSidebar.mockClear()
  sidebarCollapsed = true
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
    fireEvent.click(screen.getByLabelText("History"))
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
  it("renders New chat, Weekly brief, All chats, Sources, Settings + Feedback", () => {
    render(React.createElement(Sidebar))
    for (const label of [
      "New chat",
      "Weekly brief",
      "History",
      "Sources",
      "Settings",
      "Feedback",
    ]) {
      expect(screen.getByLabelText(label)).toBeTruthy()
    }
  })

  it("no longer renders a Sign out affordance (it lives in Settings → Account)", () => {
    render(React.createElement(Sidebar))
    expect(screen.queryByLabelText("Sign out")).toBeNull()
  })

  it("no longer renders the Backlog rail icon (functionality kept, icon removed)", () => {
    render(React.createElement(Sidebar))
    expect(screen.queryByLabelText("Backlog Projects")).toBeNull()
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
