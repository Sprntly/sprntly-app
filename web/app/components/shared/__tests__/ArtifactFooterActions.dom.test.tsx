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
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest"

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

// The prototype chip now resolves REAL per-PRD existence via getByPrd (the same
// per-PRD endpoint ApproveModal gates on), so the View/Generate decision is
// async. Stub only getByPrd; preserve the rest of the api module.
const getByPrd = vi.fn()
vi.mock("../../../lib/api", async (orig) => {
  const actual = await orig<typeof import("../../../lib/api")>()
  return {
    ...actual,
    designAgentApi: {
      ...actual.designAgentApi,
      getByPrd: (...args: unknown[]) => getByPrd(...args),
    },
  }
})

import { ArtifactFooterActions } from "../ArtifactFooterActions"
import { prototypePath } from "../../../lib/routes"

beforeEach(() => {
  // Default: this PRD has a ready prototype, so the chip resolves to "View".
  getByPrd.mockResolvedValue({ status: "ready", bundle_url: "https://x/b.html" })
})

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
  it("PRD view → Evidence, Prototype, Ticket (not PRD)", async () => {
    content = {
      prd: { prd_id: 42, title: "PRD" },
      evidence: { title: "Ev" },
    }
    render(<ArtifactFooterActions current="prd" />)

    // Prototype label resolves async off getByPrd; wait for it to settle.
    await screen.findByRole("button", { name: "View prototype" })
    expect(chipLabels()).toEqual([
      "View evidence",
      "View prototype",
      "View ticket",
    ])
    expect(screen.queryByRole("button", { name: /prd/i })).toBeNull()
  })

  it("Ticket view → Evidence, PRD, Prototype (not Ticket)", async () => {
    content = {
      prd: { prd_id: 7, title: "PRD" },
      evidence: { title: "Ev" },
    }
    render(<ArtifactFooterActions current="tickets" />)

    await screen.findByRole("button", { name: "View prototype" })
    expect(chipLabels()).toEqual([
      "View evidence",
      "View PRD",
      "View prototype",
    ])
    expect(screen.queryByRole("button", { name: /ticket/i })).toBeNull()
  })

  it("Evidence view → PRD, Ticket, Prototype (not Evidence)", async () => {
    content = {
      prd: { prd_id: 7, title: "PRD" },
      evidence: { title: "Ev" },
    }
    render(<ArtifactFooterActions current="evidence" />)

    await screen.findByRole("button", { name: "View prototype" })
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
  it("Prototype chip pushes prototypePath(prdId) with view-intent when ready", async () => {
    // getByPrd → ready prototype → view-intent (no generate=1).
    content = { prd: { prd_id: 7, title: "PRD" }, evidence: { title: "Ev" } }
    render(<ArtifactFooterActions current="prd" />)

    fireEvent.click(await screen.findByRole("button", { name: "View prototype" }))
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

describe("ArtifactFooterActions — per-PRD prototype existence gate", () => {
  it("reads 'View prototype' and navigates view-intent when this PRD has a ready prototype", async () => {
    getByPrd.mockResolvedValue({ status: "ready", bundle_url: "https://x/b.html" })
    content = { prd: { prd_id: 7, title: "PRD" }, evidence: { title: "Ev" } }
    render(<ArtifactFooterActions current="prd" />)

    const chip = await screen.findByRole("button", { name: "View prototype" })
    fireEvent.click(chip)
    // View intent: bare ?prd=, no generate flag.
    expect(pushSpy).toHaveBeenCalledWith(prototypePath(7))
    expect(getByPrd).toHaveBeenCalledWith(7)
  })

  it("reads 'Generate prototype' and carries generate intent when this PRD has no prototype", async () => {
    getByPrd.mockResolvedValue(null) // 404 / no ready prototype for this PRD
    content = { prd: { prd_id: 7, title: "PRD" }, evidence: { title: "Ev" } }
    render(<ArtifactFooterActions current="prd" />)

    const chip = await screen.findByRole("button", { name: "Generate prototype" })
    fireEvent.click(chip)
    expect(pushSpy).toHaveBeenCalledWith(prototypePath(7, { generate: true }))
  })

  it("generates when this PRD has no prototype even if a sibling PRD does", async () => {
    // Load-bearing per-PRD-not-per-insight guard. The component keys the gate
    // ONLY on getByPrd(thisPrdId); it never consults a per-insight signal that a
    // duplicate sibling PRD's prototype could satisfy. So when getByPrd(thisPrd)
    // → null, the chip MUST read "Generate prototype" + carry generate intent,
    // regardless of any same-insight sibling having a ready prototype. (A
    // per-insight signal would mis-read "View" here and dead-end on the empty
    // Generate page — the live duplicate-PRD bug this fix closes.)
    getByPrd.mockResolvedValue(null)
    content = { prd: { prd_id: 250, title: "PRD" }, evidence: { title: "Ev" } }
    render(<ArtifactFooterActions current="prd" />)

    const chip = await screen.findByRole("button", { name: "Generate prototype" })
    fireEvent.click(chip)
    expect(pushSpy).toHaveBeenCalledWith(prototypePath(250, { generate: true }))
    // Proof the gate is per-PRD: only THIS prdId was ever looked up.
    expect(getByPrd).toHaveBeenCalledWith(250)
    expect(getByPrd).toHaveBeenCalledTimes(1)
  })

  it("disables the prototype chip and does not navigate while existence is resolving", () => {
    getByPrd.mockReturnValue(new Promise(() => {})) // never resolves → in flight
    content = { prd: { prd_id: 7, title: "PRD" }, evidence: { title: "Ev" } }
    render(<ArtifactFooterActions current="prd" />)

    const chip = screen.getByRole("button", { name: /prototype/i }) as HTMLButtonElement
    expect(chip.disabled).toBe(true)
    fireEvent.click(chip)
    expect(pushSpy).not.toHaveBeenCalled()
  })
})
