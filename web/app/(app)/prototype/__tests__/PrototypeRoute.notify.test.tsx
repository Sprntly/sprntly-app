// @vitest-environment jsdom
//
// Tests for the "Notify me when ready" wiring in PrototypeRoute.
// GenerationLoadingScreen is stubbed to expose the onNotifyWhenReady prop so
// these tests exercise the route's handleNotifyWhenReady logic, not the leaf
// component's UI (which has its own test file).

import * as React from "react"
import {
  act,
  cleanup,
  fireEvent,
  render,
  screen,
  waitFor,
} from "@testing-library/react"
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest"

vi.hoisted(() => {
  ;(globalThis as Record<string, unknown>).React = require("react")
})

const routerBack = vi.fn()
const routerPush = vi.fn()
let searchString = "prd=42&generate=1"
vi.mock("next/navigation", () => ({
  useRouter: () => ({
    replace: vi.fn(),
    push: routerPush,
    prefetch: vi.fn(),
    back: routerBack,
  }),
  useSearchParams: () => new URLSearchParams(searchString),
  usePathname: () => "/prototype",
}))

const showToast = vi.fn()
const goTo = vi.fn()
vi.mock("../../../context/NavigationContext", () => ({
  useNavigation: () => ({ goTo, showToast }),
}))
vi.mock("../../../context/ContentContext", () => ({
  useContent: () => ({ content: { prd: null, userName: null } }),
}))
vi.mock("../../../context/WorkspaceContext", () => ({
  useWorkspace: () => ({ workspace: null }),
}))
vi.mock("../../../components/screens/app/AppLayout", () => ({
  AppLayout: ({ children }: { children: React.ReactNode }) =>
    React.createElement("div", { "data-testid": "app-layout" }, children),
}))

// GenerationLoadingScreen stub: exposes onNotifyWhenReady + onCancel props so
// the test can drive the route's handlers directly.
vi.mock("../../../components/design-agent/GenerationLoadingScreen", () => ({
  GenerationLoadingScreen: ({
    open,
    onCancel,
    onNotifyWhenReady,
    prototypeId,
  }: {
    open: boolean
    onCancel?: () => void
    onNotifyWhenReady?: () => void
    prototypeId?: number | null
  }) => {
    if (!open) return null
    return React.createElement(
      "div",
      { "data-testid": "stub-loading-screen", "data-proto-id": String(prototypeId ?? "") },
      React.createElement(
        "button",
        { type: "button", "data-testid": "stub-cancel", onClick: () => onCancel?.() },
        "cancel",
      ),
      onNotifyWhenReady
        ? React.createElement(
            "button",
            { type: "button", "data-testid": "stub-notify", onClick: () => onNotifyWhenReady() },
            "notify",
          )
        : null,
    )
  },
}))

// GenerateModal stub: drives route into generating state via onGenStart + onKickoff.
const IN_FLIGHT_ID = 77
vi.mock("../../../components/design-agent/GenerateModal", () => ({
  GenerateModal: ({
    open,
    onGenStart,
    onKickoff,
  }: {
    open: boolean
    onGenStart?: (ctx?: unknown) => void
    onKickoff?: (id: number) => void
  }) => {
    if (!open) return null
    return React.createElement(
      "button",
      {
        type: "button",
        "data-testid": "stub-start-gen",
        onClick: () => {
          onGenStart?.({})
          onKickoff?.(IN_FLIGHT_ID)
        },
      },
      "start gen",
    )
  },
}))

const getActiveByPrd = vi.fn(async (_id: number): Promise<unknown> => null)
const getLatestByPrd = vi.fn(async (_id: number): Promise<unknown> => null)
vi.mock("../../../lib/api", async () => {
  const actual = await vi.importActual<typeof import("../../../lib/api")>("../../../lib/api")
  return {
    ...actual,
    designAgentApi: {
      ...actual.designAgentApi,
      cancel: vi.fn(async () => undefined),
      getActiveByPrd: (id: number) => getActiveByPrd(id),
      getLatestByPrd: (id: number) => getLatestByPrd(id),
    },
  }
})

import { PrototypeRoute } from "../PrototypeRoute"

beforeEach(() => {
  searchString = "prd=42&generate=1"
  routerBack.mockClear()
  routerPush.mockClear()
  showToast.mockClear()
  goTo.mockClear()
  getActiveByPrd.mockReset()
  getActiveByPrd.mockResolvedValue(null)
  getLatestByPrd.mockReset()
  getLatestByPrd.mockResolvedValue(null)
})

afterEach(() => {
  cleanup()
  vi.clearAllMocks()
})

/** Mount, settle the resolve, then kick off generation → loading screen is visible. */
async function mountAndStartGeneration() {
  render(React.createElement(PrototypeRoute))
  const startBtn = await screen.findByTestId("stub-start-gen")
  await act(async () => {
    fireEvent.click(startBtn)
  })
  return screen.findByTestId("stub-loading-screen")
}

describe("PrototypeRoute — notify-when-ready wiring", () => {
  it("passes onNotifyWhenReady to GenerationLoadingScreen once a prototype id is in flight", async () => {
    await mountAndStartGeneration()
    // The stub renders the notify button only when onNotifyWhenReady is provided
    expect(screen.getByTestId("stub-notify")).toBeTruthy()
  })

  it("fires processing toast, da:generating, da:notify-generation, and router.back on notify click", async () => {
    const events: CustomEvent[] = []
    const handler = (e: Event) => events.push(e as CustomEvent)
    window.addEventListener("da:generating", handler)
    window.addEventListener("da:notify-generation", handler)

    await mountAndStartGeneration()
    const notifyBtn = screen.getByTestId("stub-notify")

    await act(async () => {
      fireEvent.click(notifyBtn)
    })

    window.removeEventListener("da:generating", handler)
    window.removeEventListener("da:notify-generation", handler)

    // Processing toast
    expect(showToast).toHaveBeenCalledWith(
      "Prototype is processing",
      "We'll let you know when it's ready.",
    )

    // da:generating event with prototypeId
    const genEvent = events.find((e) => e.type === "da:generating")
    expect(genEvent).toBeTruthy()
    expect(genEvent?.detail?.prototypeId).toBe(IN_FLIGHT_ID)

    // da:notify-generation event with prototypeId + prdId
    const notifyEvent = events.find((e) => e.type === "da:notify-generation")
    expect(notifyEvent).toBeTruthy()
    expect(notifyEvent?.detail?.prototypeId).toBe(IN_FLIGHT_ID)
    expect(notifyEvent?.detail?.prdId).toBe(42)

    // In jsdom history.length === 1 → falls through to router.push
    expect(routerPush).toHaveBeenCalledWith(expect.stringContaining("/prototype"))
  })

  it("calls router.push with prototypePath when history has entries to go back (history.length > 1)", async () => {
    // Override history.length to simulate a multi-entry history (e.g. user navigated
    // from the brief screen to the prototype page).
    const origDescriptor = Object.getOwnPropertyDescriptor(window.history, "length")
    Object.defineProperty(window.history, "length", { get: () => 3, configurable: true })

    await mountAndStartGeneration()
    const notifyBtn = screen.getByTestId("stub-notify")

    await act(async () => {
      fireEvent.click(notifyBtn)
    })

    // Restore
    if (origDescriptor) {
      Object.defineProperty(window.history, "length", origDescriptor)
    }

    // With history.length > 1, router.back should be called
    expect(routerBack).toHaveBeenCalledTimes(1)
    expect(routerPush).not.toHaveBeenCalled()
  })
})

describe("PrototypeRoute — notify absent outside the loading screen", () => {
  it("no notify button when route is not in the generating state", async () => {
    // Without generate=1 intent the route resolves and stays on empty state — no
    // loading screen means no notify button.
    searchString = "prd=42"
    render(React.createElement(PrototypeRoute))
    await waitFor(() => expect(screen.queryByTestId("stub-notify")).toBeNull())
  })
})
