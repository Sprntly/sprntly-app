// @vitest-environment jsdom
//
// ChatScreen — horizontal tab-strip scrolling, and the pinned Top Insights tab.
//
// Once enough tabs are open they overflow the 44px strip, so the strip scrolls
// sideways: natively for a trackpad's left/right swipe, and via a redirect for a
// plain mouse wheel (there is nothing to scroll vertically in a 44px strip, so a
// vertical gesture over it must move the tabs rather than the thread beneath).
// Through all of it the pinned "Top Insights" tab must NOT scroll away — it
// sticks to the strip's left edge while the chat tabs slide under it.
//
// jsdom has no layout engine (every box measures 0 and nothing really scrolls),
// so the geometry-dependent parts can't be asserted here — these cover what is
// observable: the sticky/opaque pin, and the wheel redirect's arithmetic with
// the scroll metrics stubbed in.
import * as React from "react"
import { act, cleanup, render, screen, within } from "@testing-library/react"
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest"

;(globalThis as typeof globalThis & { React?: typeof React }).React = React

if (typeof window !== "undefined" && !window.matchMedia) {
  window.matchMedia = (query: string) =>
    ({
      matches: false, media: query, onchange: null,
      addEventListener: () => {}, removeEventListener: () => {},
      addListener: () => {}, removeListener: () => {}, dispatchEvent: () => false,
    }) as unknown as MediaQueryList
}
window.scrollTo = (() => {}) as typeof window.scrollTo

// ── Boundary mocks (network / router / heavy contexts) ─────────────────────
vi.mock("../../../../lib/api", () => {
  class ApiError extends Error {
    status = 0
    body: unknown = null
  }
  return {
    ApiError,
    skillsApi: { list: vi.fn().mockResolvedValue({ skills: [] }) },
    askApi: { ask: vi.fn(), skills: vi.fn().mockResolvedValue({ skills: [] }) },
    briefApi: { current: vi.fn().mockResolvedValue({ id: 1, insights: [] }) },
    conversationsApi: {
      create: vi.fn(),
      addTurn: vi.fn(),
      byPrd: vi.fn().mockResolvedValue({ conversation: null, turns: [] }),
      listTurns: vi.fn().mockResolvedValue({ turns: [] }),
    },
  }
})

vi.mock("../../../../lib/runAskGeneration", () => ({
  runAskGeneration: vi.fn(),
  resumeAskGeneration: vi.fn(),
  getPendingAsk: vi.fn(() => null),
}))

vi.mock("../../../../lib/usePipelineStatus", () => ({
  usePipelineStatus: () => ({
    runStatus: null, isTriggering: false, showCompleted: false, triggerRun: vi.fn(),
  }),
}))

vi.mock("next/navigation", () => ({
  useRouter: () => ({ push: vi.fn(), replace: vi.fn(), prefetch: vi.fn() }),
  usePathname: () => "/",
  useSearchParams: () => new URLSearchParams(""),
}))

vi.mock("../../../../context/WorkspaceContext", () => ({
  profileDisplayName: () => "Ada Lovelace",
  useWorkspace: () => ({ loading: false, profile: null, workspace: null, refresh: async () => {} }),
}))

vi.mock("../../../../context/CompanyContext", () => ({
  useCompany: () => ({ activeCompany: "acme", setActiveCompany: vi.fn() }),
}))

vi.mock("../../../../lib/auth", () => ({ useAuth: () => ({ kind: "anonymous" }) }))

vi.mock("../../../design-agent/useBriefPrototypeMap", () => ({
  useBriefPrototypeMap: () => ({ entriesByInsight: {}, refetch: vi.fn() }),
}))

import { NavigationProvider } from "../../../../context/NavigationContext"
import { ContentProvider } from "../../../../context/ContentContext"
import { ChatScreen } from "../ChatScreen"

function renderScreen() {
  return render(
    React.createElement(
      NavigationProvider,
      null,
      React.createElement(ContentProvider, null, React.createElement(ChatScreen)),
    ),
  )
}

const TABS_KEY = "sprntly_chat_tabs_anon_acme"

/** Seed enough open tabs that the strip would overflow in a real browser. */
function seedManyTabs() {
  const tabs = Array.from({ length: 12 }, (_, i) => ({
    id: `tab-${i}`, title: `Conversation number ${i}`, thread: [],
    dbConvId: null, briefMeta: null, insightBody: null, prdId: null,
  }))
  sessionStorage.setItem(TABS_KEY, JSON.stringify(tabs))
}

const list = () => screen.getByTestId("chat-tab-bar") as HTMLElement
const pinnedTab = () => list().querySelector<HTMLElement>("[data-tab-pinned='true']")!

/** jsdom measures everything as 0, so nothing "overflows" and the wheel handler
 *  bails at its first guard. Stub the metrics to describe an overflowing strip. */
function stubOverflow(el: HTMLElement, { scrollWidth = 1200, clientWidth = 400 } = {}) {
  Object.defineProperty(el, "scrollWidth", { value: scrollWidth, configurable: true })
  Object.defineProperty(el, "clientWidth", { value: clientWidth, configurable: true })
  el.scrollLeft = 0
}

/** Dispatch a real (non-React) wheel event — the handler is attached natively so
 *  it can call preventDefault, which React's passive root listener cannot. */
function wheel(el: HTMLElement, init: WheelEventInit) {
  const e = new WheelEvent("wheel", { bubbles: true, cancelable: true, ...init })
  act(() => { el.dispatchEvent(e) })
  return e
}

/** jsdom ships no PointerEvent, and the pan handler only reads pointerType /
 *  button / clientX — so a MouseEvent with pointerType grafted on drives it
 *  exactly like the real thing (listeners key off the type string). */
function pointer(
  el: EventTarget,
  type: string,
  { clientX = 0, button = 0, pointerType = "mouse" } = {},
) {
  const e = new MouseEvent(type, { bubbles: true, cancelable: true, clientX, button })
  Object.defineProperty(e, "pointerType", { value: pointerType })
  act(() => { el.dispatchEvent(e) })
  return e
}

/** Press on `from`, move the pointer by `dx`, release. */
function drag(from: HTMLElement, dx: number, opts: { pointerType?: string } = {}) {
  pointer(from, "pointerdown", { clientX: 100, ...opts })
  pointer(window, "pointermove", { clientX: 100 + dx, ...opts })
  pointer(window, "pointerup", { clientX: 100 + dx, ...opts })
}

beforeEach(() => {
  localStorage.clear()
  sessionStorage.clear()
})
afterEach(() => {
  cleanup()
  localStorage.clear()
  sessionStorage.clear()
})

describe("ChatScreen — pinned Top Insights tab", () => {
  it("is sticky at the strip's left edge so it never scrolls out of reach", () => {
    seedManyTabs()
    renderScreen()
    const pin = pinnedTab()
    expect(within(pin).getByText("Top Insights")).toBeTruthy()
    expect(pin.style.position).toBe("sticky")
    // FLUSH. Any inset here is a gap the scrolling tabs show through, to the
    // left of the pin.
    expect(pin.style.left).toBe("0px")
  })

  it("leaves no gap beside it: the strip's lead-in is the pin's own padding", () => {
    seedManyTabs()
    renderScreen()
    // The list must carry no padding-left — that was the gap.
    expect(list().style.paddingLeft).toBe("")
    // …and the pin absorbs it (14px base + the 8px lead-in), so the label sits
    // where it always did but the fill reaches the strip's edge.
    expect(pinnedTab().style.paddingLeft).toBe("22px")
  })

  it("is opaque when inactive, so scrolled tabs pass UNDER it, not through it", () => {
    seedManyTabs()
    sessionStorage.setItem("sprntly_chat_active_tab_anon_acme", "tab-3")
    renderScreen()
    const pin = pinnedTab()
    // Inactive (a chat tab is active) — but still painted with the strip's own
    // colour rather than left transparent.
    expect(pin.dataset.tabActive).toBeUndefined()
    expect(pin.style.background).toContain("--surface-2")
    expect(pin.style.background).not.toBe("transparent")
    // It outranks the scroller's overflow fade (z-index 2).
    expect(Number(pin.style.zIndex)).toBeGreaterThan(2)
  })

  it("stays the first tab in the strip", () => {
    seedManyTabs()
    renderScreen()
    expect(list().firstElementChild).toBe(pinnedTab())
  })
})

describe("ChatScreen — wheel over the strip scrolls it sideways", () => {
  it("redirects a vertical mouse wheel into horizontal scroll", () => {
    seedManyTabs()
    renderScreen()
    const el = list()
    stubOverflow(el)

    const e = wheel(el, { deltaY: 120, deltaX: 0 })
    expect(el.scrollLeft).toBe(120)
    // The thread underneath must not scroll too.
    expect(e.defaultPrevented).toBe(true)

    // …and back the other way.
    wheel(el, { deltaY: -50, deltaX: 0 })
    expect(el.scrollLeft).toBe(70)
  })

  it("converts LINE-mode wheels to pixels (a Firefox notch is deltaY 3, not 3px)", () => {
    seedManyTabs()
    renderScreen()
    const el = list()
    stubOverflow(el)

    // deltaMode 1 = DOM_DELTA_LINE. Taken raw this moved the strip 3px.
    wheel(el, { deltaY: 3, deltaX: 0, deltaMode: 1 })
    expect(el.scrollLeft).toBe(48)
  })

  it("leaves a genuine horizontal gesture (trackpad swipe) to the browser", () => {
    seedManyTabs()
    renderScreen()
    const el = list()
    stubOverflow(el)

    // Horizontal-dominant: native overflow-x already handles it, so the handler
    // must not preventDefault or double-apply the delta.
    const e = wheel(el, { deltaX: 80, deltaY: 4 })
    expect(e.defaultPrevented).toBe(false)
    expect(el.scrollLeft).toBe(0)
  })

  it("does nothing when the tabs fit (no overflow to scroll)", () => {
    renderScreen()
    const el = list()
    stubOverflow(el, { scrollWidth: 300, clientWidth: 400 })

    const e = wheel(el, { deltaY: 120, deltaX: 0 })
    expect(e.defaultPrevented).toBe(false)
    expect(el.scrollLeft).toBe(0)
  })
})

describe("ChatScreen — drag the strip to pan it", () => {
  const aTab = () => within(list()).getByText("Conversation number 2").closest(".chat-tab") as HTMLElement

  it("dragging left pulls the tabs along (press anywhere on the strip)", () => {
    seedManyTabs()
    renderScreen()
    const el = list()
    stubOverflow(el)

    // Pull the content 60px to the LEFT → the viewport moves 60px right.
    drag(aTab(), -60)
    expect(el.scrollLeft).toBe(60)
  })

  it("dragging right pans back the other way", () => {
    seedManyTabs()
    renderScreen()
    const el = list()
    stubOverflow(el)
    el.scrollLeft = 200

    drag(aTab(), 75)
    expect(el.scrollLeft).toBe(125)
  })

  it("a drag does NOT also select the tab it was released on", () => {
    seedManyTabs()
    sessionStorage.setItem("sprntly_chat_active_tab_anon_acme", "tab-0")
    renderScreen()
    const el = list()
    stubOverflow(el)

    const tab = aTab()
    drag(tab, -60)
    // The click browsers fire after the drag must be swallowed…
    act(() => { tab.dispatchEvent(new MouseEvent("click", { bubbles: true, cancelable: true })) })
    // …so tab-0 is still the active one, not the dragged-over tab-2.
    expect(tab.dataset.tabActive).toBeUndefined()
  })

  it("a plain click (no movement past the threshold) still selects the tab", () => {
    seedManyTabs()
    sessionStorage.setItem("sprntly_chat_active_tab_anon_acme", "tab-0")
    renderScreen()
    stubOverflow(list())

    const tab = aTab()
    // 2px of shake — under the 5px threshold, so it stays a click.
    drag(tab, 2)
    act(() => { tab.dispatchEvent(new MouseEvent("click", { bubbles: true, cancelable: true })) })
    expect(tab.dataset.tabActive).toBe("true")
  })

  it("leaves touch pointers to the browser's own panning", () => {
    seedManyTabs()
    renderScreen()
    const el = list()
    stubOverflow(el)

    drag(aTab(), -60, { pointerType: "touch" })
    expect(el.scrollLeft).toBe(0)
  })

  it("does not pan when the tabs already fit", () => {
    seedManyTabs()
    renderScreen()
    const el = list()
    stubOverflow(el, { scrollWidth: 300, clientWidth: 400 })

    drag(aTab(), -60)
    expect(el.scrollLeft).toBe(0)
  })

  it("never starts a pan from the × close button", () => {
    seedManyTabs()
    renderScreen()
    const el = list()
    stubOverflow(el)

    const close = within(aTab()).getByTitle("Close tab")
    drag(close, -60)
    expect(el.scrollLeft).toBe(0)
  })
})

// When the artifact panel opens it puts padding-right on .main-column, so the
// tab strip narrows under whatever was scrolled to its right edge — the active
// tab ends up clipped behind the panel with nothing to bring it back. jsdom
// can't lay any of that out, so these drive keepActiveTabVisible directly by
// stubbing the geometry it measures: a strip whose right edge has moved in.
describe("ChatScreen — the active tab stays clear of the artifact panel", () => {
  /** Describe a strip running 0…`stripRight`, with the sticky pin occupying
   *  0…100 and the active tab currently painted at [tabLeft, tabRight].
   *
   *  The active tab's rect TRACKS scrollLeft, as it would in a browser — a fixed
   *  rect would let a second call re-apply the same correction and drift, making
   *  the result depend on how many times the handler happened to run. The pin's
   *  doesn't: it's sticky, so it stays put through the scroll. */
  function stubGeometry(
    { stripRight, tabLeft, tabRight, scrollLeft = 0 }:
      { stripRight: number; tabLeft: number; tabRight: number; scrollLeft?: number },
  ) {
    const el = list()
    stubOverflow(el, { scrollWidth: 2000, clientWidth: stripRight })
    el.scrollLeft = scrollLeft
    const base = scrollLeft
    el.getBoundingClientRect = () => ({ left: 0, right: stripRight, width: stripRight }) as DOMRect
    pinnedTab().getBoundingClientRect = () => ({ left: 0, right: 100, width: 100 }) as DOMRect
    const active = el.querySelector<HTMLElement>("[data-tab-active='true']:not([data-tab-pinned])")!
    active.getBoundingClientRect = () => {
      const shift = el.scrollLeft - base
      return { left: tabLeft - shift, right: tabRight - shift, width: tabRight - tabLeft } as DOMRect
    }
    return el
  }

  beforeEach(() => {
    seedManyTabs()
    sessionStorage.setItem("sprntly_chat_active_tab_anon_acme", "tab-5")
  })

  it("pulls the tab back into view when the panel shrinks the strip", async () => {
    renderScreen()
    // Strip now ends at 600 (the panel took the rest); the active tab runs to
    // 780, well past it.
    const el = stubGeometry({ stripRight: 600, tabLeft: 640, tabRight: 780 })

    // A resize is what the panel opening produces.
    await act(async () => { window.dispatchEvent(new Event("resize")) })

    // Scrolled by exactly the overshoot plus the 12px of air: 780 - (600 - 12).
    expect(el.scrollLeft).toBe(192)
  })

  it("leaves an already-visible tab alone (no jump)", async () => {
    renderScreen()
    const el = stubGeometry({ stripRight: 600, tabLeft: 300, tabRight: 440 })

    await act(async () => { window.dispatchEvent(new Event("resize")) })

    expect(el.scrollLeft).toBe(0)
  })

  it("never parks the tab under the pinned Top Insights tab", async () => {
    renderScreen()
    // The tab is off to the LEFT, behind the pin (which ends at 100).
    const el = stubGeometry({ stripRight: 600, tabLeft: 40, tabRight: 180, scrollLeft: 300 })

    await act(async () => { window.dispatchEvent(new Event("resize")) })

    // Scrolled back by 60 — enough to clear the pin's right edge, not the
    // strip's left edge.
    expect(el.scrollLeft).toBe(240)
  })

  it("shows the START of a tab too wide for the remaining corridor", async () => {
    renderScreen()
    // Corridor is 100…588 (488 wide); the tab is 700 wide, so both bounds can't
    // be met — its left edge wins so the title reads from the beginning.
    const el = stubGeometry({ stripRight: 600, tabLeft: 200, tabRight: 900 })

    await act(async () => { window.dispatchEvent(new Event("resize")) })

    expect(el.scrollLeft).toBe(100)
  })
})

// The separator hairlines themselves are drawn by globals.css (`.chat-tab +
// .chat-tab::before`), which jsdom does not load — so what's asserted here is
// the hook that CSS hangs off: every tab carries the class, and the active one
// is still marked so the rules that suppress the separator beside it can match.
describe("ChatScreen — tab separators", () => {
  it("marks every tab with the .chat-tab class the separator rules key off", () => {
    seedManyTabs()
    renderScreen()
    const chips = list().querySelectorAll(".chat-tab")
    // 12 chat tabs + the pinned one.
    expect(chips.length).toBe(13)
    expect(chips[0].getAttribute("data-tab-pinned")).toBe("true")
  })

  it("marks the active tab, so the separators either side of it can be hidden", () => {
    seedManyTabs()
    sessionStorage.setItem("sprntly_chat_active_tab_anon_acme", "tab-4")
    renderScreen()
    const active = list().querySelectorAll("[data-tab-active='true']")
    expect(active.length).toBe(1)
    expect(active[0].textContent).toContain("Conversation number 4")
  })

  it("positions tabs relatively, so the separator pseudo-element can anchor", () => {
    seedManyTabs()
    renderScreen()
    const tab = within(list()).getByText("Conversation number 2").closest(".chat-tab") as HTMLElement
    expect(tab.style.position).toBe("relative")
  })
})
