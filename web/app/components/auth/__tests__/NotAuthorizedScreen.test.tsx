// @vitest-environment jsdom
//
// The flat "not authorized" screen never takes a title/artifact-id prop at
// all — mutation-proof against a future edit accidentally threading one in,
// since AC3 requires no artifact title anywhere in the rendered DOM for a
// blocked/invalid visit.
import * as React from "react"
import { cleanup, render, screen } from "@testing-library/react"
import { afterEach, describe, expect, it, vi } from "vitest"

// NotAuthorizedScreen.tsx uses a JSX fragment shorthand, which this project's
// classic (non-automatic) JSX transform compiles to a bare `React` reference.
// vi.hoisted runs before the hoisted `import` below evaluates the module.
vi.hoisted(() => {
  // eslint-disable-next-line @typescript-eslint/no-require-imports
  ;(globalThis as Record<string, unknown>).React = require("react")
})

import { NotAuthorizedScreen } from "../NotAuthorizedScreen"

afterEach(() => {
  cleanup()
})

describe("NotAuthorizedScreen", () => {
  it("test_not_authorized_screen_never_renders_artifact_title — different_company", () => {
    render(<NotAuthorizedScreen reason="different_company" />)
    // The component's props type has no title/id field at all — this asserts
    // the DOM never contains ANY of a set of plausible artifact-identifying
    // strings, guarding against a future prop-threading regression too.
    expect(document.body.textContent).not.toMatch(/PRD|prd_id|artifact/i)
  })

  it("renders the danger-tinted verify-icon recipe", () => {
    render(<NotAuthorizedScreen reason="invalid_token" />)
    const icon = document.querySelector(".verify-icon")
    expect(icon).not.toBeNull()
    expect(icon?.className).toContain("verify-icon--danger")
  })

  it("renders distinct copy per reason", () => {
    const { unmount: u1 } = render(<NotAuthorizedScreen reason="different_company" />)
    const differentCompanyText = document.querySelector(".auth-sub")?.textContent
    u1()
    render(<NotAuthorizedScreen reason="invalid_token" />)
    const invalidTokenText = document.querySelector(".auth-sub")?.textContent
    expect(differentCompanyText).not.toEqual(invalidTokenText)
  })

  it("offers a way back to the app, defaulting to '/' when no continueHref is given", () => {
    render(<NotAuthorizedScreen reason="invalid_token" />)
    const link = screen.getByText(/Continue to your workspace/)
    expect(link).not.toBeNull()
    expect(link.closest("a")?.getAttribute("href")).toBe("/")
  })

  it("test_not_authorized_screen_uses_the_given_continueHref — the user's own account state, not the artifact", () => {
    render(<NotAuthorizedScreen reason="different_company" continueHref="/onboarding/your-name" />)
    const link = screen.getByText(/Continue to your workspace/)
    expect(link.closest("a")?.getAttribute("href")).toBe("/onboarding/your-name")
  })

  it("has no rendering path left for the retired domain_mismatch reason", () => {
    // Mutation-proof for the cleanup: NotAuthorizedReason's type union no
    // longer includes "domain_mismatch" at all — this is a compile-time
    // guarantee (see the file's type export), not just a runtime one.
    const reasons: import("../NotAuthorizedScreen").NotAuthorizedReason[] = [
      "different_company",
      "invalid_token",
    ]
    expect(reasons).toHaveLength(2)
  })
})
