// @vitest-environment jsdom
//
// The on-join greeting's lead + Show more/less split. A greeting whose body
// carries the `MORE_MARKER` renders its pre-marker lead as the visible text plus
// a Show more/less toggle over the rest — and the literal `<!--more-->` marker
// never appears in either half (the bug this fixes: the marker leaked as inline
// text once the component that split it was deleted in the chat rewrite).
import { cleanup, fireEvent, render } from "@testing-library/react"
import { afterEach, describe, expect, it, vi } from "vitest"

// Classic JSX runtime needs a global React before the component modules evaluate,
// and AskReplyBody's simulated-stream hook reads window.matchMedia (absent in jsdom).
vi.hoisted(() => {
  // eslint-disable-next-line @typescript-eslint/no-require-imports
  ;(globalThis as Record<string, unknown>).React = require("react")
  if (typeof window !== "undefined" && !window.matchMedia) {
    window.matchMedia = ((q: string) => ({
      matches: false, media: q, onchange: null,
      addEventListener() {}, removeEventListener() {},
      addListener() {}, removeListener() {}, dispatchEvent() { return false },
    })) as unknown as typeof window.matchMedia
  }
})

import { GreetingTurnBody } from "../GreetingTurnBody"
import { MORE_MARKER } from "../../../../shared/chat-shell/types"

afterEach(cleanup)

const LEAD = "Welcome to the Payments Revamp project."
const REST = "**What we're solving**\n\nCheckout drop-off is up 12% this quarter."
const GREETING = `${LEAD}${MORE_MARKER}${REST}`

describe("GreetingTurnBody marker split", () => {
  it("renders the lead, hides the rest, and never prints the literal marker", () => {
    const { container } = render(<GreetingTurnBody answer={GREETING} />)
    expect(container.textContent).toContain("Welcome to the Payments Revamp project.")
    // The marker itself is gone (the original bug rendered it inline).
    expect(container.textContent).not.toContain(MORE_MARKER)
    expect(container.textContent).not.toContain("more--")
    // Collapsed by default: the rest is not shown yet.
    expect(container.textContent).not.toContain("Checkout drop-off is up 12%")
    // The toggle is present and starts collapsed.
    const toggle = container.querySelector("button")
    expect(toggle?.textContent).toBe("Show more")
    expect(toggle?.getAttribute("aria-expanded")).toBe("false")
  })

  it("reveals the rest on Show more and collapses again on Show less", () => {
    const { container } = render(<GreetingTurnBody answer={GREETING} />)
    const toggle = container.querySelector("button")!
    fireEvent.click(toggle)
    expect(container.textContent).toContain("Checkout drop-off is up 12%")
    expect(container.textContent).not.toContain(MORE_MARKER)
    expect(toggle.textContent).toBe("Show less")
    expect(toggle.getAttribute("aria-expanded")).toBe("true")
    fireEvent.click(toggle)
    expect(container.textContent).not.toContain("Checkout drop-off is up 12%")
    expect(toggle.textContent).toBe("Show more")
  })

  it("renders a marker-less greeting whole, with no toggle", () => {
    const { container } = render(<GreetingTurnBody answer={LEAD} />)
    expect(container.textContent).toContain("Welcome to the Payments Revamp project.")
    expect(container.querySelector("button")).toBeNull()
  })
})
