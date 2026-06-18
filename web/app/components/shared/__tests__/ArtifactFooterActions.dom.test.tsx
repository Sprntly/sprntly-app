// @vitest-environment jsdom
//
// ArtifactFooterActions is the row of contextual actions at the BOTTOM of each
// rail artifact (PRD / Evidence / Tickets). It surfaces the sibling artifacts +
// the primary next action, omitting the one you're already on, and wires each
// to existing behavior: openContentPanel(tab) for the rail tabs and
// router.push(prototypePath(prdId)) for the prototype. These tests assert the
// per-artifact action set, the gating (prototype/tickets need a PRD), and the
// click wiring.
import * as React from "react"
import { cleanup, fireEvent, render, screen } from "@testing-library/react"
import { afterEach, describe, expect, it, vi } from "vitest"

// The component's JSX compiles to React.createElement under the repo's classic
// JSX runtime, so a global React must exist before the import below evaluates.
// vi.hoisted runs before hoisted imports (mirrors TicketsTab.dom.test.tsx).
vi.hoisted(() => {
  // eslint-disable-next-line @typescript-eslint/no-require-imports
  ;(globalThis as Record<string, unknown>).React = require("react")
})

const pushSpy = vi.fn()
vi.mock("next/navigation", () => ({
  useRouter: () => ({ push: pushSpy, replace: vi.fn(), prefetch: vi.fn() }),
}))

const openContentPanel = vi.fn()
vi.mock("../../../context/NavigationContext", async (orig) => {
  const actual = await orig<typeof import("../../../context/NavigationContext")>()
  return { ...actual, useNavigation: () => ({ openContentPanel }) }
})

let content: Record<string, unknown> = {}
vi.mock("../../../context/ContentContext", async (orig) => {
  const actual = await orig<typeof import("../../../context/ContentContext")>()
  return { ...actual, useContent: () => ({ content, setContent: vi.fn() }) }
})

import { ArtifactFooterActions } from "../ArtifactFooterActions"
import { prototypePath } from "../../../lib/routes"

afterEach(() => {
  cleanup()
  vi.clearAllMocks()
})

describe("ArtifactFooterActions", () => {
  it("on the PRD artifact shows Create ticket / View evidence / View prototype (not View PRD)", () => {
    content = { prd: { prd_id: 42, title: "Onboarding PRD" } }
    render(<ArtifactFooterActions current="prd" />)

    expect(screen.getByRole("button", { name: /create ticket/i })).toBeTruthy()
    expect(screen.getByRole("button", { name: /view evidence/i })).toBeTruthy()
    expect(screen.getByRole("button", { name: /view prototype/i })).toBeTruthy()
    // The artifact you're already on is omitted.
    expect(screen.queryByRole("button", { name: /view prd/i })).toBeNull()
  })

  it("wires View evidence and Create ticket to openContentPanel(tab)", () => {
    content = { prd: { prd_id: 42, title: "PRD" } }
    render(<ArtifactFooterActions current="prd" />)

    fireEvent.click(screen.getByRole("button", { name: /view evidence/i }))
    expect(openContentPanel).toHaveBeenCalledWith("evidence")

    fireEvent.click(screen.getByRole("button", { name: /create ticket/i }))
    expect(openContentPanel).toHaveBeenCalledWith("tickets")
  })

  it("wires View prototype to router.push(prototypePath(prdId))", () => {
    content = { prd: { prd_id: 7, title: "PRD" } }
    render(<ArtifactFooterActions current="prd" />)

    fireEvent.click(screen.getByRole("button", { name: /view prototype/i }))
    expect(pushSpy).toHaveBeenCalledWith(prototypePath(7))
  })

  it("on the Tickets artifact shows View evidence / View PRD / View prototype (not Create ticket)", () => {
    content = { prd: { prd_id: 7, title: "PRD" } }
    render(<ArtifactFooterActions current="tickets" />)

    expect(screen.getByRole("button", { name: /view evidence/i })).toBeTruthy()
    expect(screen.getByRole("button", { name: /view prd/i })).toBeTruthy()
    expect(screen.getByRole("button", { name: /view prototype/i })).toBeTruthy()
    expect(screen.queryByRole("button", { name: /create ticket/i })).toBeNull()
  })

  it("hides prototype + ticket actions when no PRD is loaded", () => {
    content = { prd: null }
    render(<ArtifactFooterActions current="evidence" />)

    // View PRD is always available; prototype + create-ticket need a PRD.
    expect(screen.getByRole("button", { name: /view prd/i })).toBeTruthy()
    expect(screen.queryByRole("button", { name: /view prototype/i })).toBeNull()
    expect(screen.queryByRole("button", { name: /create ticket/i })).toBeNull()
  })
})
