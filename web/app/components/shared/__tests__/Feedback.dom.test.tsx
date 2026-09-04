// @vitest-environment jsdom
//
// Feedback / feature-request DOM tests (June 20 #13 + #A).
//
// Two things matter for the slice:
//   1. The "Feedback" nav item renders in the sidebar's bottom group (next to
//      Settings — sign-out moved to Settings → Account and is NOT in the rail).
//   2. Clicking it opens the lightweight form; filling the message + submitting
//      calls feedbackApi.submit with the message + selected type.
//
// The modal itself is no longer embedded in the Sidebar: it moved to AppShell
// when the command palette gained a "Send feedback" action, so two triggers
// share one instance through NavigationContext (`openFeedback`). The wiring
// and the form are therefore tested apart — the rail button is asserted to
// ASK the shell to open it, and the form is mounted directly. Mocks cover only
// the context boundaries + the api module, so no network is hit.
import * as React from "react"
import { cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react"
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest"

;(globalThis as typeof globalThis & { React?: typeof React }).React = React

// The rail carries a trial countdown that links to billing, so it now uses the
// router directly — `goTo` takes a screen id and the settings SECTION rides
// the query string.
vi.mock("next/navigation", () => ({ useRouter: () => ({ push: vi.fn() }) }))

const openFeedback = vi.fn()
vi.mock("../../../context/NavigationContext", () => ({
  useNavigation: () => ({
    currentScreen: "brief",
    goTo: vi.fn(),
    goToNewChat: vi.fn(),
    openFeedback,
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
  useWorkspace: () => ({ profile: null, workspace: null }),
}))

const submit = vi.fn().mockResolvedValue({ id: "fb-1", type: "feature_request", email_sent: true })
vi.mock("../../../lib/api", () => ({
  feedbackApi: { submit: (...args: unknown[]) => submit(...args) },
  // The nav's recent-chats section reads this. Empty: these tests are about
  // the Feedback affordance, and an empty list renders no chats section at
  // all, which keeps the DOM they query unchanged.
  conversationsApi: { list: () => Promise.resolve({ conversations: [] }) },
}))

import { Sidebar } from "../Sidebar"
import { FeedbackModal } from "../FeedbackModal"

beforeEach(() => {
  submit.mockClear()
  openFeedback.mockClear()
})
afterEach(() => cleanup())

describe("Sidebar — Feedback entry", () => {
  it("renders Feedback as an icon in the identity row (no sign-out — it moved to Settings)", () => {
    render(React.createElement(Sidebar))
    const feedback = screen.getByLabelText("Feedback")
    expect(feedback).toBeTruthy()
    // It lives in the footer row now, beside Sync and Settings — three icons
    // on one line where there used to be three full-width rows. Sign
    // out is still deliberately absent from the rail: it is in
    // Settings -> Account.
    expect(feedback.closest(".sb-rail-user .sb-rail-actions")).toBeTruthy()
    expect(feedback.className).toContain("sb-rail-action")
    expect(screen.queryByLabelText("Sign out")).toBeNull()
  })

  // The rail button no longer owns the modal — it asks the shell for it, which
  // is what lets the command palette open the same one.
  it("clicking Feedback asks the shell to open the modal, and renders none itself", () => {
    const { container } = render(React.createElement(Sidebar))
    expect(screen.queryByLabelText("Send feedback")).toBeNull()

    fireEvent.click(screen.getByLabelText("Feedback"))
    expect(openFeedback).toHaveBeenCalledTimes(1)
    // Still none: AppShell renders the one instance, not this component.
    expect(container.querySelector('[aria-label="Send feedback"]')).toBeNull()
  })
})

describe("FeedbackModal — the form", () => {
  it("submit calls feedbackApi.submit with the message and type", async () => {
    render(React.createElement(FeedbackModal, { open: true, onClose: vi.fn() }))
    const dialog = screen.getByLabelText("Send feedback")

    const textarea = within(dialog).getByPlaceholderText(/Tell us what you'd like to see/i)
    fireEvent.change(textarea, { target: { value: "Add a Notion connector" } })

    const typeSelect = within(dialog).getByLabelText("Type") as HTMLSelectElement
    fireEvent.change(typeSelect, { target: { value: "connector_request" } })

    fireEvent.click(within(dialog).getByRole("button", { name: "Send feedback" }))

    await waitFor(() => expect(submit).toHaveBeenCalledTimes(1))
    expect(submit).toHaveBeenCalledWith({
      message: "Add a Notion connector",
      type: "connector_request",
    })
  })

  it("does not submit an empty message", () => {
    render(React.createElement(FeedbackModal, { open: true, onClose: vi.fn() }))
    const dialog = screen.getByLabelText("Send feedback")
    fireEvent.click(within(dialog).getByRole("button", { name: "Send feedback" }))
    expect(submit).not.toHaveBeenCalled()
  })
})
