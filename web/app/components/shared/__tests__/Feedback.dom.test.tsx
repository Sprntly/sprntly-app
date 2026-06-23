// @vitest-environment jsdom
//
// Feedback / feature-request DOM tests (June 20 #13 + #A).
//
// Two things matter for the slice:
//   1. The "Feedback" nav item renders in the sidebar's bottom group, next to
//      the sign-out control.
//   2. Clicking it opens the lightweight form; filling the message + submitting
//      calls feedbackApi.submit with the message + selected type.
//
// We mount the REAL Sidebar (and its embedded FeedbackModal), mocking only the
// context boundaries + the api module so no network is hit.
import * as React from "react"
import { cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react"
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest"

;(globalThis as typeof globalThis & { React?: typeof React }).React = React

vi.mock("../../../context/NavigationContext", () => ({
  useNavigation: () => ({ currentScreen: "brief", goTo: vi.fn(), goToNewChat: vi.fn() }),
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
}))

import { Sidebar } from "../Sidebar"

beforeEach(() => submit.mockClear())
afterEach(() => cleanup())

describe("Sidebar — Feedback entry", () => {
  it("renders a Feedback nav item next to the sign-out control", () => {
    render(React.createElement(Sidebar))
    const feedback = screen.getByLabelText("Feedback")
    expect(feedback).toBeTruthy()
    // It lives in the bottom rail group alongside the other bottom items, and
    // the sign-out control is present in the same sidebar.
    expect(feedback.closest(".sb-rail-bottom")).toBeTruthy()
    expect(screen.getByLabelText("Sign out")).toBeTruthy()
  })

  it("clicking Feedback opens the form; submit calls feedbackApi.submit", async () => {
    render(React.createElement(Sidebar))

    // Form not open yet.
    expect(screen.queryByLabelText("Send feedback")).toBeNull()

    fireEvent.click(screen.getByLabelText("Feedback"))
    const dialog = screen.getByLabelText("Send feedback")
    expect(dialog).toBeTruthy()

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
    render(React.createElement(Sidebar))
    fireEvent.click(screen.getByLabelText("Feedback"))
    const dialog = screen.getByLabelText("Send feedback")
    fireEvent.click(within(dialog).getByRole("button", { name: "Send feedback" }))
    expect(submit).not.toHaveBeenCalled()
  })
})
