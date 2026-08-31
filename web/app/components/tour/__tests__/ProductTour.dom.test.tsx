// @vitest-environment jsdom
//
// When the first-run tour runs, and what it does once it is running.
//
// The costly bugs in something like this are not visual, they are "it showed
// again", "it never showed", and "it showed on top of onboarding" — so the
// trigger conditions get most of the attention here.
//
// Matchers: native DOM only (no @testing-library/jest-dom).
import * as React from "react"
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react"
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest"

;(globalThis as typeof globalThis & { React?: typeof React }).React = React

const markProductTourSeen = vi.fn().mockResolvedValue(undefined)
const refresh = vi.fn().mockResolvedValue(undefined)

vi.mock("../../../lib/onboarding/store", () => ({
  markProductTourSeen: (...a: unknown[]) => markProductTourSeen(...a),
}))

let authState: unknown = { kind: "authed", user: { id: "u-1", email: "a@b.c" } }
vi.mock("../../../lib/auth", () => ({ useAuth: () => authState }))

type Ws = {
  profile: Record<string, unknown> | null
  workspace: Record<string, unknown> | null
  orgRole: string | null
  refresh: () => Promise<void>
}
let wsState: Ws
vi.mock("../../../context/WorkspaceContext", () => ({
  useWorkspace: () => wsState,
}))

import { ProductTour } from "../ProductTour"

const PROFILE = {
  id: "u-1",
  first_name: "Ada",
  product_tour_completed_at: null as string | null,
}
const WORKSPACE = {
  onboarding_completed_at: "2026-08-01T00:00:00Z",
  subscription_status: "trialing",
  feature_flags: {},
}

function setup(over: Partial<Ws> = {}) {
  wsState = {
    profile: { ...PROFILE },
    workspace: { ...WORKSPACE },
    orgRole: "owner",
    refresh,
    ...over,
  }
}

/** jsdom gives every element a 0x0 box, which ProductTour reads as "absent"
 *  and centres. Stub a real box so the spotlight path is exercised too. */
function anchorAt(anchor: string) {
  const el = document.createElement("button")
  el.setAttribute("data-tour", anchor)
  el.getBoundingClientRect = () =>
    ({ top: 100, left: 20, width: 40, height: 40 }) as DOMRect
  document.body.appendChild(el)
  return el
}

beforeEach(() => {
  authState = { kind: "authed", user: { id: "u-1", email: "a@b.c" } }
  setup()
  markProductTourSeen.mockClear()
  refresh.mockClear()
})

afterEach(() => {
  cleanup()
  document.body.innerHTML = ""
})

describe("ProductTour — whether it runs at all", () => {
  it("runs for a signed-in user who has never seen it", () => {
    render(<ProductTour />)
    expect(screen.getByTestId("product-tour-bubble")).toBeTruthy()
    expect(screen.getByText("A quick tour of Sprntly")).toBeTruthy()
  })

  it("does NOT run for someone who already finished or skipped it", () => {
    // The whole point of the profiles column. A tour that reappears is worse
    // than one that never showed.
    setup({ profile: { ...PROFILE, product_tour_completed_at: "2026-08-30T00:00:00Z" } })
    render(<ProductTour />)
    expect(screen.queryByTestId("product-tour-bubble")).toBeNull()
  })

  it("does NOT run on top of onboarding", () => {
    // Onboarding is its own guided sequence; two at once is a pile-up.
    setup({ workspace: { ...WORKSPACE, onboarding_completed_at: null } })
    render(<ProductTour />)
    expect(screen.queryByTestId("product-tour-bubble")).toBeNull()
  })

  it("does NOT run before the profile or workspace have loaded", () => {
    setup({ profile: null })
    const { unmount } = render(<ProductTour />)
    expect(screen.queryByTestId("product-tour-bubble")).toBeNull()
    unmount()

    setup({ workspace: null })
    render(<ProductTour />)
    expect(screen.queryByTestId("product-tour-bubble")).toBeNull()
  })

  it("does NOT run for a signed-out visitor", () => {
    authState = { kind: "anonymous" }
    render(<ProductTour />)
    expect(screen.queryByTestId("product-tour-bubble")).toBeNull()
  })
})

describe("ProductTour — moving through it", () => {
  it("Next advances and Back returns", () => {
    render(<ProductTour />)
    expect(screen.getByText("A quick tour of Sprntly")).toBeTruthy()
    // Back is deliberately absent on the first step — there is nowhere back to.
    expect(screen.queryByTestId("product-tour-back")).toBeNull()

    fireEvent.click(screen.getByTestId("product-tour-next"))
    expect(screen.queryByText("A quick tour of Sprntly")).toBeNull()

    fireEvent.click(screen.getByTestId("product-tour-back"))
    expect(screen.getByText("A quick tour of Sprntly")).toBeTruthy()
  })

  it("arrow keys move, and Escape closes", async () => {
    render(<ProductTour />)
    fireEvent.keyDown(document, { key: "ArrowRight" })
    expect(screen.queryByText("A quick tour of Sprntly")).toBeNull()
    fireEvent.keyDown(document, { key: "ArrowLeft" })
    expect(screen.getByText("A quick tour of Sprntly")).toBeTruthy()

    fireEvent.keyDown(document, { key: "Escape" })
    await waitFor(() => expect(screen.queryByTestId("product-tour-bubble")).toBeNull())
    expect(markProductTourSeen).toHaveBeenCalledWith("u-1")
  })
})

describe("ProductTour — it only offers itself once", () => {
  it("Skip records it, so it cannot come back", async () => {
    // A skip is a decision. Recording it is the difference between a welcome
    // mat and a nuisance.
    render(<ProductTour />)
    fireEvent.click(screen.getByTestId("product-tour-skip"))
    await waitFor(() => expect(markProductTourSeen).toHaveBeenCalledWith("u-1"))
    expect(screen.queryByTestId("product-tour-bubble")).toBeNull()
  })

  it("finishing the last step records it too", async () => {
    render(<ProductTour />)
    // Walk to the end. Bounded rather than while(true) so a regression that
    // stops the tour advancing fails here instead of hanging the suite.
    for (let i = 0; i < 40; i++) {
      const next = screen.queryByTestId("product-tour-next")
      if (!next) break
      if (next.textContent === "Get started") {
        fireEvent.click(next)
        break
      }
      fireEvent.click(next)
    }
    await waitFor(() => expect(markProductTourSeen).toHaveBeenCalledWith("u-1"))
  })

  it("does not re-open when the context refreshes underneath it", async () => {
    // `refresh()` re-reads the profile, and for an instant the flag is still
    // null. Without the one-shot guard that re-opens the tour the user just
    // closed — the most likely way this feature becomes hated.
    const { rerender } = render(<ProductTour />)
    fireEvent.click(screen.getByTestId("product-tour-skip"))
    await waitFor(() => expect(screen.queryByTestId("product-tour-bubble")).toBeNull())

    setup({ profile: { ...PROFILE, product_tour_completed_at: null } })
    rerender(<ProductTour />)
    expect(screen.queryByTestId("product-tour-bubble")).toBeNull()
  })
})

describe("ProductTour — anchors", () => {
  it("spotlights a real element when its anchor is on the page", () => {
    anchorAt("composer")
    const { container } = render(<ProductTour />)
    fireEvent.click(screen.getByTestId("product-tour-next")) // welcome -> ask
    // A spotlight box exists, and there is no full-page scrim (they are
    // mutually exclusive — the spotlight's own shadow IS the dimming).
    const spot = container.querySelector('[class*="spotlight"]')
    expect(spot).toBeTruthy()
    expect((spot as HTMLElement).style.top).toBe(`${100 - 8}px`)
  })

  it("falls back to a centred card when the anchor is missing", () => {
    // No anchor appended: this is the below-900px case, where the rail is
    // display:none and every rail anchor genuinely is absent. The step must
    // still read, not spotlight a 0x0 point in the corner.
    const { container } = render(<ProductTour />)
    fireEvent.click(screen.getByTestId("product-tour-next"))
    expect(container.querySelector('[class*="spotlight"]')).toBeNull()
    expect(container.querySelector('[class*="scrim"]')).toBeTruthy()
    expect(screen.getByTestId("product-tour-bubble").className).toMatch(/bubbleCentred/)
  })
})
