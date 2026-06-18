// @vitest-environment jsdom
//
// Sidebar nav-wiring DOM tests.
//
// After the brief/chat unification, the home surface (`/`, ChatScreen) defaults
// to the pinned Monday-brief tab on a fresh load. So the sidebar "New chat" `+`
// must NOT use the plain goTo("chat") nav (that would land on the brief) — it
// uses goToNewChat() (→ `/?new=1`, consumed by ChatScreen to start a fresh chat).
// The "Monday brief" and "All chats" rail items keep their plain goTo() nav.
//
// These tests mount the REAL Sidebar, mocking only the context boundaries it
// reads, and assert the click→nav wiring (not a re-implementation).
import * as React from "react"
import { cleanup, fireEvent, render, screen } from "@testing-library/react"
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest"

;(globalThis as typeof globalThis & { React?: typeof React }).React = React

const goTo = vi.fn()
const goToNewChat = vi.fn()

vi.mock("../../../context/NavigationContext", () => ({
  useNavigation: () => ({ currentScreen: "brief", goTo, goToNewChat }),
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
})
afterEach(() => cleanup())

describe("Sidebar — New chat wiring", () => {
  it("'New chat' uses goToNewChat (fresh chat), never goTo('chat') (would land on brief)", () => {
    render(React.createElement(Sidebar))
    fireEvent.click(screen.getByLabelText("New chat"))
    expect(goToNewChat).toHaveBeenCalledTimes(1)
    expect(goTo).not.toHaveBeenCalledWith("chat")
  })

  it("'Monday brief' and 'All chats' rail items keep their plain goTo() nav", () => {
    render(React.createElement(Sidebar))
    fireEvent.click(screen.getByLabelText("Monday brief"))
    expect(goTo).toHaveBeenCalledWith("brief")
    fireEvent.click(screen.getByLabelText("All chats"))
    expect(goTo).toHaveBeenCalledWith("chats")
    // The new-chat helper was not triggered by either.
    expect(goToNewChat).not.toHaveBeenCalled()
  })
})
