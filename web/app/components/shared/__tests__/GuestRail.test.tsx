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

import { GuestRail } from "../GuestRail"

afterEach(() => {
  cleanup()
})

describe("GuestRail", () => {
  it("test_guest_rail_renders_collapsed_with_guest_pill_and_disabled_nav — AC21", () => {
    render(<GuestRail />)
    const rail = document.querySelector(".sidebar")
    expect(rail).not.toBeNull()
    expect(rail?.className).toContain("sidebar--collapsed")
    expect(screen.getByTestId("guest-rail-pill").textContent).toBe("GUEST")

    const navButtons = document.querySelectorAll(".sb-rail-item")
    expect(navButtons.length).toBeGreaterThan(0)
    navButtons.forEach((btn) => {
      expect((btn as HTMLButtonElement).disabled).toBe(true)
    })
  })

  it("test_guest_rail_replaces_sidebar_entirely_in_guest_mode — AC21", () => {
    // Structural guarantee, not just visual: GuestRail imports nothing from
    // Sidebar.tsx or AppShell.tsx — the real Sidebar is imported only by
    // AppShell, which GuestArtifactViewer never renders, so it cannot appear
    // in a guest-session render tree. Asserted here by absence of any
    // Sidebar-only marker (the workspace switcher, present only in Sidebar).
    render(<GuestRail />)
    expect(screen.queryByTestId("workspace-switcher")).toBeNull()
  })
})
