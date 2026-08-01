// @vitest-environment jsdom
import * as React from "react"
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react"
import { afterEach, describe, expect, it, vi } from "vitest"

// EntryGateScreen renders NotAuthorizedScreen (JSX fragment shorthand) on the
// invalid-token path — the classic JSX transform needs a bare `React` in
// scope before that module evaluates. vi.hoisted runs before hoisted imports.
vi.hoisted(() => {
  // eslint-disable-next-line @typescript-eslint/no-require-imports
  ;(globalThis as Record<string, unknown>).React = require("react")
})

const routerMock = { push: vi.fn(), replace: vi.fn() }
vi.mock("next/navigation", () => ({ useRouter: () => routerMock }))

const getMetadataMock = vi.fn()
vi.mock("../../../lib/artifactShareApi", () => ({
  artifactShareApi: { getMetadata: (...a: unknown[]) => getMetadataMock(...a) },
}))

import { EntryGateScreen } from "../EntryGateScreen"

afterEach(() => {
  cleanup()
  vi.clearAllMocks()
})

describe("EntryGateScreen", () => {
  it("test_entry_gate_screen_renders_domain_hint_before_form_fill — AC2", async () => {
    getMetadataMock.mockResolvedValue({
      artifact_type: "prd",
      title: "Q3 Retention PRD",
      sharer_name: "Priya Shah",
      owning_company_name: "Acme Co",
      required_email_domain: "acme.com",
    })
    render(<EntryGateScreen token="tok-123" />)

    await waitFor(() => {
      expect(screen.getByTestId("entry-gate-domain-hint")).not.toBeNull()
    })
    // No form field exists on this screen at all — the domain hint is shown
    // with nothing to "fill" yet, trivially satisfying "before any form field
    // is rendered/filled".
    expect(document.querySelector("input")).toBeNull()
    expect(screen.getByText(/acme\.com/)).not.toBeNull()
  })

  it("shows sharer name and title, and routes Create account / Sign in with the token", async () => {
    getMetadataMock.mockResolvedValue({
      artifact_type: "prd",
      title: "Q3 Retention PRD",
      sharer_name: "Priya Shah",
      owning_company_name: "Acme Co",
      required_email_domain: null,
    })
    render(<EntryGateScreen token="tok-123" />)

    await waitFor(() => {
      expect(screen.getByText(/Priya Shah/)).not.toBeNull()
    })
    fireEvent.click(screen.getByRole("button", { name: /create account/i }))
    expect(routerMock.push).toHaveBeenCalledWith("/sign-up?share=tok-123")

    fireEvent.click(screen.getByRole("button", { name: /sign in/i }))
    expect(routerMock.push).toHaveBeenCalledWith("/sign-in?share=tok-123")
  })

  it("test_entry_gate_screen_shows_not_authorized_on_invalid_token — AC3", async () => {
    getMetadataMock.mockRejectedValue(new Error("not found"))
    render(<EntryGateScreen token="bad-token" />)

    await waitFor(() => {
      expect(document.querySelector(".verify-icon--danger")).not.toBeNull()
    })
    expect(document.body.textContent).not.toMatch(/Q3 Retention PRD/)
  })
})
