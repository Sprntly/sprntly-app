// @vitest-environment jsdom
//
// NavigationContext — the content panel must survive `openPrdTab`'s navigation.
//
// Root cause of the regression this guards (PR 585 follow-up): "View/Generate
// PRD" from the brief or backlog surface calls `openPrdTab`, which routes to `/`
// so ChatScreen can spawn a chat tab and slide the Evidence/PRD/Tickets panel
// over it. But NavigationContext closes that panel on EVERY pathname change —
// and `/brief`→`/` / `/backlog`→`/` is a real pathname change. Next updates
// usePathname inside a transition, so the close could land after ChatScreen's
// deferred open and swallow it: the tab opened, the panel never showed.
//
// The fix: openPrdTab flags the imminent arrival at `/` as "don't close". These
// tests prove (a) a normal navigation still closes an open panel, and (b) the
// navigation openPrdTab kicks off does NOT close it.
import * as React from "react"
import { act, cleanup, fireEvent, render, screen } from "@testing-library/react"
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest"

;(globalThis as typeof globalThis & { React?: typeof React }).React = React

// openPrdTab calls window.scrollTo (unimplemented in jsdom) — stub it.
if (typeof window !== "undefined") window.scrollTo = () => {}

// A mutable pathname the "router" drives. `push` is a pure spy that does NOT
// mutate pathname synchronously — Next commits the pathname change later, inside
// a transition. Tests flip pathname via navigateTo() to model that deferred
// commit, which is exactly the timing window this guard protects.
let pathname = "/brief"
const push = vi.fn()
vi.mock("next/navigation", () => ({
  useRouter: () => ({ push, replace: vi.fn(), prefetch: vi.fn() }),
  usePathname: () => pathname,
}))

import { NavigationProvider, useNavigation, type PrdTabRequest } from "../NavigationContext"

// Surfaces contentPanelTab and exposes the two actions under test.
function Probe() {
  const { contentPanelTab, openContentPanel, openPrdTab } = useNavigation()
  return (
    <div>
      <span data-testid="panel">{contentPanelTab ?? "none"}</span>
      <button onClick={() => openContentPanel("prd")}>open-panel</button>
      <button
        onClick={() =>
          openPrdTab({
            title: "PRD",
            source: { kind: "ready", prd: { prd_id: 1 }, meta: null },
          } as unknown as PrdTabRequest)
        }
      >
        open-prd-tab
      </button>
    </div>
  )
}

// Re-renders the WHOLE tree (incl. NavigationProvider) so the provider re-reads
// usePathname() after the router mutates the module-level `pathname`.
let rerenderTree: (() => void) | null = null
function Harness() {
  const [, tick] = React.useState(0)
  rerenderTree = () => tick((n) => n + 1)
  return (
    <NavigationProvider>
      <Probe />
    </NavigationProvider>
  )
}

/** Simulate the router landing on `to`: mutate pathname, then re-render so the
 *  provider observes the new usePathname() value and runs its [pathname] effect. */
function navigateTo(to: string) {
  act(() => {
    pathname = to
    rerenderTree?.()
  })
}

beforeEach(() => {
  pathname = "/brief"
  push.mockClear()
})
afterEach(() => {
  cleanup()
  rerenderTree = null
})

describe("NavigationContext — content panel vs navigation", () => {
  it("closes an open content panel on a normal navigation", () => {
    render(<Harness />)
    act(() => fireEvent.click(screen.getByText("open-panel")))
    expect(screen.getByTestId("panel").textContent).toBe("prd")

    navigateTo("/") // a plain nav away from /brief
    expect(screen.getByTestId("panel").textContent).toBe("none")
  })

  it("keeps the panel open across the navigation openPrdTab kicks off", () => {
    render(<Harness />)
    // openPrdTab routes to `/` (push mutates pathname) and flags the skip.
    act(() => fireEvent.click(screen.getByText("open-prd-tab")))
    expect(push).toHaveBeenCalledWith("/")

    // Stand in for ChatScreen opening the panel after it mounts on `/`.
    act(() => fireEvent.click(screen.getByText("open-panel")))
    expect(screen.getByTestId("panel").textContent).toBe("prd")

    // The router's pathname change to `/` now commits. WITHOUT the guard this
    // fired setContentPanelTab(null) and the panel vanished.
    navigateTo("/")
    expect(screen.getByTestId("panel").textContent).toBe("prd")

    // The skip is one-shot: a subsequent real navigation closes normally.
    navigateTo("/backlog")
    expect(screen.getByTestId("panel").textContent).toBe("none")
  })
})
