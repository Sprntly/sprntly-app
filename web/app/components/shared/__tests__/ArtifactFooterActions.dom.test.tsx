// @vitest-environment jsdom
//
// ArtifactFooterActions is the contextual footer at the BOTTOM of each rail
// artifact (PRD / Evidence / Ticket / Prototype). It shows a status label +
// exactly THREE chips pointing at the OTHER artifacts, omitting the one you're
// already on. A chip whose artifact EXISTS reads "View …" and opens it; a
// missing one reads "Generate …" and kicks the generate flow. Each chip wires
// to existing behavior: openContentPanel(tab) for Evidence/PRD/Ticket, and
// router.push(prototypePath(prdId, { generate })) for Prototype. The Prototype
// + Ticket chips need a PRD, and Prototype is additionally hidden when the
// finding isn't prototypeable. These tests assert the per-view chip set +
// order, the View/Generate semantics, the click wiring, and the gating.
import * as React from "react"
import { cleanup, fireEvent, render, screen } from "@testing-library/react"
import { afterEach, describe, expect, it, vi } from "vitest"

// The component's JSX compiles to React.createElement under the repo's classic
// JSX runtime, so a global React must exist before the import below evaluates.
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

/** Chip labels rendered, in DOM order. */
function chipLabels(): string[] {
  return screen
    .getAllByRole("button")
    .map((b) => (b.textContent ?? "").trim())
}

describe("ArtifactFooterActions — per-view chip set", () => {
  it("PRD view → Evidence, Prototype, Ticket (not PRD)", () => {
    content = {
      prd: { prd_id: 42, title: "PRD" },
      evidence: { title: "Ev" },
    }
    render(<ArtifactFooterActions current="prd" />)

    expect(chipLabels()).toEqual([
      "View evidence",
      "View prototype",
      "View ticket",
    ])
    expect(screen.queryByRole("button", { name: /prd/i })).toBeNull()
  })

  it("Ticket view → Evidence, PRD, Prototype (not Ticket)", () => {
    content = {
      prd: { prd_id: 7, title: "PRD" },
      evidence: { title: "Ev" },
    }
    render(<ArtifactFooterActions current="tickets" />)

    expect(chipLabels()).toEqual([
      "View evidence",
      "View PRD",
      "View prototype",
    ])
    expect(screen.queryByRole("button", { name: /ticket/i })).toBeNull()
  })

  it("Evidence view → PRD, Ticket, Prototype (not Evidence)", () => {
    content = {
      prd: { prd_id: 7, title: "PRD" },
      evidence: { title: "Ev" },
    }
    render(<ArtifactFooterActions current="evidence" />)

    expect(chipLabels()).toEqual([
      "View PRD",
      "View ticket",
      "View prototype",
    ])
    expect(screen.queryByRole("button", { name: /evidence/i })).toBeNull()
  })

  it("renders a contextual status label", () => {
    content = { prd: { prd_id: 7, title: "PRD" }, evidence: { title: "Ev" } }
    render(<ArtifactFooterActions current="evidence" />)
    expect(screen.getByText(/Evidence ready/i)).toBeTruthy()
  })

  it("carries the design footer/chip classes — primary chip is .is-primary", () => {
    // Visual restyle (#475): styling lives in CSS classes (design `.art-foot` +
    // `.chip` / `.chip.b`). Assert the contract the stylesheet keys off so a
    // future regression that drops the classes is caught.
    content = { prd: { prd_id: 7, title: "PRD" }, evidence: { title: "Ev" } }
    const { container } = render(<ArtifactFooterActions current="evidence" />)
    expect(container.querySelector(".artifact-foot-actions")).toBeTruthy()
    const chips = container.querySelectorAll(".artifact-foot-chip")
    expect(chips.length).toBe(3)
    // Only the first (most-forward) sibling is the brand-primary chip.
    expect(container.querySelectorAll(".artifact-foot-chip.is-primary").length).toBe(1)
    expect(chips[0].classList.contains("is-primary")).toBe(true)
  })
})

describe("ArtifactFooterActions — View vs Generate semantics", () => {
  it("an existing sibling reads 'View …', a missing one reads 'Generate …'", () => {
    // PRD exists, evidence does NOT → on the Ticket view the Evidence chip
    // should be a Generate, the PRD chip a View.
    content = { prd: { prd_id: 7, title: "PRD" }, evidence: null }
    render(<ArtifactFooterActions current="tickets" />)

    expect(screen.getByRole("button", { name: "Generate evidence" })).toBeTruthy()
    expect(screen.getByRole("button", { name: "View PRD" })).toBeTruthy()
  })

  it("an existing artifact's chip opens it, a missing one generates", () => {
    // From the PRD view: evidence missing → "Generate evidence", but both still
    // route through openContentPanel (the tab owns load-or-generate).
    content = { prd: { prd_id: 7, title: "PRD" }, evidence: null }
    render(<ArtifactFooterActions current="prd" />)

    fireEvent.click(screen.getByRole("button", { name: "Generate evidence" }))
    expect(openContentPanel).toHaveBeenCalledWith("evidence")
  })

  it("Ticket chip opens the tickets tab", () => {
    content = { prd: { prd_id: 7, title: "PRD" }, evidence: { title: "Ev" } }
    render(<ArtifactFooterActions current="prd" />)

    fireEvent.click(screen.getByRole("button", { name: /ticket/i }))
    expect(openContentPanel).toHaveBeenCalledWith("tickets")
  })
})

describe("ArtifactFooterActions — prototype wiring + gating", () => {
  it("Prototype chip pushes prototypePath(prdId) with view-intent when ready", () => {
    // A PRD-bearing prototype counts as existing → view-intent (no generate=1).
    content = { prd: { prd_id: 7, title: "PRD" }, evidence: { title: "Ev" } }
    render(<ArtifactFooterActions current="prd" />)

    fireEvent.click(screen.getByRole("button", { name: /prototype/i }))
    expect(pushSpy).toHaveBeenCalledWith(prototypePath(7))
  })

  it("hides the Prototype chip when the finding is not prototypeable", () => {
    content = { prd: { prd_id: 7, title: "PRD" }, evidence: { title: "Ev" } }
    render(<ArtifactFooterActions current="prd" prototypeable={false} />)

    expect(screen.queryByRole("button", { name: /prototype/i })).toBeNull()
    // The other two siblings still render.
    expect(screen.getByRole("button", { name: /evidence/i })).toBeTruthy()
    expect(screen.getByRole("button", { name: /ticket/i })).toBeTruthy()
  })

  it("hides Prototype + Ticket chips when no PRD is loaded", () => {
    content = { prd: null, evidence: { title: "Ev" } }
    render(<ArtifactFooterActions current="evidence" />)

    // From Evidence with no PRD: only the PRD chip survives (Generate PRD).
    expect(screen.getByRole("button", { name: "Generate PRD" })).toBeTruthy()
    expect(screen.queryByRole("button", { name: /prototype/i })).toBeNull()
    expect(screen.queryByRole("button", { name: /ticket/i })).toBeNull()
  })
})
