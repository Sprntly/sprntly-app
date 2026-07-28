// @vitest-environment jsdom
//
// The artifact panel animates BOTH ways. Opening is easy — the element mounts
// and the CSS plays. Closing is the part that needs code: `contentPanelTab`
// goes null the instant the × is clicked, and an unconditional unmount there
// made the panel vanish while the main column was still easing its padding
// shut. These tests lock the lifecycle that fixes it:
//
//   1. the panel stays mounted, carrying .cpanel--out, for the exit duration
//   2. it still renders its last tab's body while sliding out (not a blank box)
//   3. it is inert while leaving, so nothing can be clicked or tabbed into
//   4. the enter transform class comes OFF once the panel settles — it must not
//      linger, because a transform on .cpanel makes it the containing block for
//      the `position: fixed` pickers/modals rendered inside it
import * as React from "react"
import { act, cleanup, render, screen } from "@testing-library/react"
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest"

vi.hoisted(() => {
  // eslint-disable-next-line @typescript-eslint/no-require-imports
  ;(globalThis as Record<string, unknown>).React = require("react")
})

vi.mock("next/navigation", () => ({
  useRouter: () => ({ push: vi.fn(), replace: vi.fn(), prefetch: vi.fn() }),
}))

// Stub the tab bodies — the lifecycle is what's under test, not their fetches.
vi.mock("../PrdPanelContent", () => ({
  PrdPanelContent: () => React.createElement("div", { "data-testid": "prd-body" }),
}))

const navMock = vi.hoisted(() => ({
  tab: "prd" as "prd" | "evidence" | "tickets" | null,
  openContentPanel: vi.fn(),
  closeContentPanel: vi.fn(),
}))
vi.mock("../../../context/NavigationContext", () => ({
  useNavigation: () => ({
    contentPanelTab: navMock.tab,
    openContentPanel: navMock.openContentPanel,
    closeContentPanel: navMock.closeContentPanel,
    showToast: vi.fn(),
    expandAiPanel: vi.fn(),
    setAIBarValue: vi.fn(),
  }),
}))

const contentMock = vi.hoisted(() => ({ value: {} as Record<string, unknown> }))
vi.mock("../../../context/ContentContext", () => ({
  useContent: () => ({ content: contentMock.value, setContent: vi.fn() }),
}))

import { ContentPanel } from "../ContentPanel"
import type { PrdState } from "../../../types/content"

const prd: PrdState = { prd_id: 1, title: "Scheduled Send", metaLine: "", sections: [], source: "brief" }

// Comfortably past both --cpanel-in-ms (260) and --cpanel-out-ms (200).
const PAST_ANIMATION = 400

const panel = () => document.querySelector("aside.cpanel")

function renderOpen() {
  navMock.tab = "prd"
  contentMock.value = { prd, evidence: null, evidenceGenerating: false }
  return render(React.createElement(ContentPanel))
}

/** Flip the context to closed and let `rerender` re-read the mock. */
function close(rerender: (ui: React.ReactElement) => void) {
  navMock.tab = null
  act(() => { rerender(React.createElement(ContentPanel)) })
}

beforeEach(() => { vi.useFakeTimers() })

afterEach(() => {
  vi.runOnlyPendingTimers()
  vi.useRealTimers()
  cleanup()
  navMock.tab = "prd"
  navMock.closeContentPanel.mockClear()
})

describe("ContentPanel — open/close transition", () => {
  it("plays the enter animation on open, then drops the transform class", () => {
    renderOpen()
    expect(panel()?.className).toContain("cpanel--in")

    act(() => { vi.advanceTimersByTime(PAST_ANIMATION) })

    // Settled: no animation class at all. A lingering transform would re-anchor
    // every `position: fixed` child (the Jira modal, the destination picker)
    // to the panel instead of the viewport.
    expect(panel()?.className).toBe("cpanel")
  })

  it("keeps the panel mounted, marked leaving, for the exit animation", () => {
    const { rerender } = renderOpen()
    act(() => { vi.advanceTimersByTime(PAST_ANIMATION) })

    close(rerender)

    const leaving = panel()
    expect(leaving).not.toBeNull()
    expect(leaving?.className).toContain("cpanel--out")
    // Still showing what the user was reading — not an empty white slab.
    expect(screen.queryByTestId("prd-body")).not.toBeNull()
  })

  it("takes the leaving panel out of the a11y tree and the tab order", () => {
    const { rerender } = renderOpen()
    act(() => { vi.advanceTimersByTime(PAST_ANIMATION) })

    close(rerender)

    const leaving = panel()
    expect(leaving?.getAttribute("aria-hidden")).toBe("true")
    expect(leaving?.hasAttribute("inert")).toBe(true)
  })

  it("unmounts once the exit animation has run", () => {
    const { rerender } = renderOpen()
    act(() => { vi.advanceTimersByTime(PAST_ANIMATION) })

    close(rerender)
    act(() => { vi.advanceTimersByTime(PAST_ANIMATION) })

    expect(panel()).toBeNull()
  })

  it("re-entering mid-exit cancels the unmount and replays the enter", () => {
    const { rerender } = renderOpen()
    act(() => { vi.advanceTimersByTime(PAST_ANIMATION) })

    close(rerender)
    // Reopened before the exit timer fires — the panel must survive it.
    navMock.tab = "evidence"
    act(() => { rerender(React.createElement(ContentPanel)) })
    act(() => { vi.advanceTimersByTime(PAST_ANIMATION) })

    expect(panel()).not.toBeNull()
    expect(panel()?.className).toBe("cpanel")
  })

  it("renders nothing when it has never been opened", () => {
    navMock.tab = null
    contentMock.value = { prd, evidence: null, evidenceGenerating: false }
    render(React.createElement(ContentPanel))
    expect(panel()).toBeNull()
  })
})
