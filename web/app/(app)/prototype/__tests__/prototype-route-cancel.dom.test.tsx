// @vitest-environment jsdom
//
// Integration tests for the cancel/back escape hatches on the real PrototypeRoute.
//   1. Cancelling the generating overlay calls designAgentApi.cancel with the
//      in-flight prototype id and returns the user to the PRD (brief) screen.
//   2. A cancel request that REJECTS must still clear loading + navigate (never
//      trap the user).
//   3. The transient `resolving` state exposes a back control that navigates back.
//
// GenerationLoadingScreen is stubbed to surface the onCancel prop it receives (so
// this exercises the ROUTE's handleGenCancel wiring, not the leaf's controls —
// those are covered by GenerationLoadingScreen.cancel.dom.test.tsx). GenerateModal
// is stubbed to drive the route into the generating state via onGenStart +
// onKickoff, exactly like the real modal.

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
let searchString = "prd=42&generate=1"
vi.mock("next/navigation", () => ({
  useRouter: () => ({
    replace: vi.fn(),
    push: vi.fn(),
    prefetch: vi.fn(),
    back: routerBack,
  }),
  useSearchParams: () => new URLSearchParams(searchString),
  usePathname: () => "/prototype",
}))

const goTo = vi.fn()
vi.mock("../../../context/NavigationContext", () => ({
  useNavigation: () => ({ goTo }),
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

// GenerationLoadingScreen stub: surfaces the onCancel prop + the in-flight id so
// the test drives the route's handleGenCancel.
vi.mock("../../../components/design-agent/GenerationLoadingScreen", () => ({
  GenerationLoadingScreen: ({
    open,
    onCancel,
    prototypeId,
  }: {
    open: boolean
    onCancel?: () => void
    prototypeId?: number | null
  }) =>
    open
      ? React.createElement(
          "button",
          {
            type: "button",
            "data-testid": "stub-cancel",
            "data-proto-id": String(prototypeId ?? ""),
            onClick: () => onCancel?.(),
          },
          "cancel",
        )
      : null,
}))

// GenerateModal stub: a button that kicks off generation (onGenStart) and reports
// the in-flight id (onKickoff), mirroring the real modal's POST-returns-id moment.
const IN_FLIGHT_ID = 99
vi.mock("../../../components/design-agent/GenerateModal", () => ({
  GenerateModal: ({
    open,
    onGenStart,
    onKickoff,
  }: {
    open: boolean
    onGenStart?: (ctx?: unknown) => void
    onKickoff?: (id: number) => void
  }) =>
    open
      ? React.createElement(
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
      : null,
}))

const cancelApi = vi.fn(async (_id: number): Promise<void> => undefined)
const getActiveByPrd = vi.fn(async (_id: number): Promise<unknown> => null)
const getLatestByPrd = vi.fn(async (_id: number): Promise<unknown> => null)
vi.mock("../../../lib/api", async () => {
  const actual = await vi.importActual<typeof import("../../../lib/api")>(
    "../../../lib/api",
  )
  return {
    ...actual,
    designAgentApi: {
      ...actual.designAgentApi,
      cancel: (id: number) => cancelApi(id),
      getActiveByPrd: (id: number) => getActiveByPrd(id),
      getLatestByPrd: (id: number) => getLatestByPrd(id),
    },
  }
})

import { PrototypeRoute } from "../PrototypeRoute"

beforeEach(() => {
  searchString = "prd=42&generate=1"
  routerBack.mockClear()
  goTo.mockClear()
  cancelApi.mockReset()
  cancelApi.mockResolvedValue(undefined)
  getActiveByPrd.mockReset()
  getActiveByPrd.mockResolvedValue(null)
  getLatestByPrd.mockReset()
  getLatestByPrd.mockResolvedValue(null)
})

afterEach(() => {
  cleanup()
  vi.clearAllMocks()
})

/** Mount, settle the resolve, then kick off a generation → the lightweight
 *  "generating in the background" card is up (genLoading true, in-flight id set
 *  via onKickoff). Background generation replaced the full-screen overlay, so the
 *  cancel affordance is now the card's "Cancel generation" button. */
async function mountAndStartGeneration() {
  render(React.createElement(PrototypeRoute))
  const start = await screen.findByTestId("stub-start-gen")
  await act(async () => {
    fireEvent.click(start)
  })
  await screen.findByTestId("prototype-route-generating")
  return screen.getByText("Cancel generation")
}

describe("PrototypeRoute — cancel the background-generating card", () => {
  it("cancels with the in-flight id and returns to the PRD screen", async () => {
    const cancelBtn = await mountAndStartGeneration()

    await act(async () => {
      fireEvent.click(cancelBtn)
    })

    // handleGenCancel cancels with the id captured via onKickoff.
    expect(cancelApi).toHaveBeenCalledWith(IN_FLIGHT_ID)
    expect(goTo).toHaveBeenCalledWith("brief")
    // Card cleared (loading state reset).
    await waitFor(() =>
      expect(screen.queryByTestId("prototype-route-generating")).toBeNull(),
    )
  })

  it("still clears loading + navigates when the cancel request rejects", async () => {
    cancelApi.mockRejectedValue(new Error("network"))
    const cancelBtn = await mountAndStartGeneration()

    await act(async () => {
      fireEvent.click(cancelBtn)
    })

    // Best-effort: a failed cancel must NOT trap the user.
    expect(cancelApi).toHaveBeenCalledWith(IN_FLIGHT_ID)
    expect(goTo).toHaveBeenCalledWith("brief")
    await waitFor(() =>
      expect(screen.queryByTestId("prototype-route-generating")).toBeNull(),
    )
  })
})

describe("PrototypeRoute — resolving state is not a dead-end", () => {
  it("renders a back control that navigates back", async () => {
    searchString = "prd=42" // no generate intent → the resolve path
    // getActiveByPrd never resolves → the route stays in the `resolving` state.
    getActiveByPrd.mockImplementation(() => new Promise<null>(() => {}))

    render(React.createElement(PrototypeRoute))

    const back = await screen.findByTestId("prototype-route-loading-back")
    fireEvent.click(back)
    expect(routerBack).toHaveBeenCalledTimes(1)
  })
})
