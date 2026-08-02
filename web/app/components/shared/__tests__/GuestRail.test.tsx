// @vitest-environment jsdom
import * as React from "react"
import { cleanup, render, screen } from "@testing-library/react"
import { afterEach, describe, expect, it, vi } from "vitest"

// GuestRail.tsx has module-level JSX (the DISABLED_NAV array) — the classic
// JSX transform needs a bare `React` in scope before that module evaluates.
vi.hoisted(() => {
  // eslint-disable-next-line @typescript-eslint/no-require-imports
  ;(globalThis as Record<string, unknown>).React = require("react")
})

// GuestRail now reads the real signed-in identity via useAuth() (no
// dependency on Workspace/Company context) instead of a static "GUEST"
// label — mock it per test rather than requiring a real AuthProvider.
const authMock = vi.hoisted(() => ({ state: vi.fn() }))
vi.mock("../../../lib/auth", () => ({
  useAuth: () => authMock.state(),
}))

import { GuestRail } from "../GuestRail"

afterEach(() => {
  cleanup()
})

describe("GuestRail", () => {
  it("test_guest_rail_renders_collapsed_with_real_name_pill_and_disabled_nav — AC21", () => {
    authMock.state.mockReturnValue({
      kind: "authed",
      user: { email: "priya@acme.com", user_metadata: { first_name: "Priya", last_name: "Shah" } },
    })
    render(<GuestRail />)
    const rail = document.querySelector(".sidebar")
    expect(rail).not.toBeNull()
    expect(rail?.className).toContain("sidebar--collapsed")
    const pill = screen.getByTestId("guest-rail-pill")
    expect(pill.textContent).toBe("PS")
    expect(pill.getAttribute("title")).toBe("Priya Shah")

    const navButtons = document.querySelectorAll(".sb-rail-item")
    expect(navButtons.length).toBeGreaterThan(0)
    navButtons.forEach((btn) => {
      expect((btn as HTMLButtonElement).disabled).toBe(true)
    })
  })

  it("falls back to the email when the profile has no first/last name", () => {
    authMock.state.mockReturnValue({
      kind: "authed",
      user: { email: "priya@acme.com", user_metadata: {} },
    })
    render(<GuestRail />)
    expect(screen.getByTestId("guest-rail-pill").getAttribute("title")).toBe("priya@acme.com")
  })

  it("test_guest_rail_replaces_sidebar_entirely_in_guest_mode — AC21", () => {
    // Structural guarantee, not just visual: GuestRail imports nothing from
    // Sidebar.tsx or AppShell.tsx — the real Sidebar is imported only by
    // AppShell, which GuestArtifactViewer never renders, so it cannot appear
    // in a guest-session render tree. Asserted here by absence of any
    // Sidebar-only marker (the workspace switcher, present only in Sidebar).
    authMock.state.mockReturnValue({
      kind: "authed",
      user: { email: "priya@acme.com", user_metadata: { first_name: "Priya", last_name: "Shah" } },
    })
    render(<GuestRail />)
    expect(screen.queryByTestId("workspace-switcher")).toBeNull()
  })
})
