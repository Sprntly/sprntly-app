// @vitest-environment jsdom
//
// Chat history top-bar "New chat" wiring.
//
// The home surface (`/`, ChatScreen) restores whatever tab was last open, so a
// plain goTo("chat") from Chat history re-opens the PREVIOUS chat instead of
// starting a fresh one. The top-bar pill must use goToNewChat() (→ `/?new=1`,
// consumed by ChatScreen to open a new tab) — the same hand-off the sidebar's
// `+` uses. Row clicks keep their plain goTo("chat") resume nav.
import * as React from "react"
import { cleanup, fireEvent, render, screen } from "@testing-library/react"
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest"

;(globalThis as typeof globalThis & { React?: typeof React }).React = React

const goTo = vi.fn()
const goToNewChat = vi.fn()

vi.mock("../../../../context/NavigationContext", () => ({
  useNavigation: () => ({ currentScreen: "chats", goTo, goToNewChat }),
}))

vi.mock("../../../../context/ContentContext", () => ({
  useContent: () => ({ content: { conversations: [] } }),
}))

vi.mock("../../../../context/CompanyContext", () => ({
  useCompany: () => ({ activeCompany: "acme", setActiveCompany: vi.fn() }),
}))

vi.mock("../../../../lib/auth", () => ({
  useAuth: () => ({ kind: "anonymous" }),
}))

vi.mock("../../../../lib/api", () => ({
  conversationsApi: {
    list: vi.fn().mockResolvedValue({ conversations: [] }),
    remove: vi.fn(),
    update: vi.fn(),
  },
  briefApi: { current: vi.fn().mockRejectedValue(new Error("no brief")) },
}))

// The screen's chrome is irrelevant here — render children only so the test
// doesn't drag in the whole app shell.
vi.mock("../AppLayout", () => ({
  AppLayout: ({ children }: { children?: React.ReactNode }) =>
    React.createElement("div", null, children),
}))

import { ChatsScreen } from "../ChatsScreen"

beforeEach(() => {
  goTo.mockClear()
  goToNewChat.mockClear()
})
afterEach(() => cleanup())

describe("ChatsScreen — 'New chat' top-bar pill", () => {
  it("opens a NEW chat tab (goToNewChat), never goTo('chat') (would resume the last tab)", () => {
    render(React.createElement(ChatsScreen))
    fireEvent.click(screen.getByLabelText("New chat"))
    expect(goToNewChat).toHaveBeenCalledTimes(1)
    expect(goTo).not.toHaveBeenCalled()
  })
})
