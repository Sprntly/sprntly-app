// @vitest-environment jsdom
//
// Integration test for the cancel-aware failure guard on the real PrototypeRoute.
// When a user CANCELS an in-flight generation, the cancel path deletes the
// prototype row; the still-running background task's next write then 404s and
// the poll resolves `{ ok: false }`. handleGenDone must NOT surface the in-panel
// generation-error for that user-cancelled id — while a genuine, never-cancelled
// failure STILL surfaces (proven by prototype-route-gen-recovery.dom.test.tsx and
// re-asserted here as a control).
//
// The stubs mirror the route's real prop wiring: GenerateModal fires onGenStart +
// onKickoff(id) (setting genProtoId) and onGenDone(result); GenerationLoadingScreen
// exposes onCancel (the route's handleGenCancel, which marks the id cancelled).
// After cancel, genLoading flips false so GenerateModal re-opens — that is where
// the terminal `{ ok: false }` is then delivered, exactly like the real poll.

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
import { __resetPageLoadGuards } from "../../../components/design-agent/notificationStore"

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

// GenerationLoadingScreen stub: when open, exposes onCancel (drives the route's
// handleGenCancel → markCancelled(genProtoId)).
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

// GenerateModal stub: when open, exposes a "start" button (onGenStart + onKickoff,
// setting genProtoId) and a "gen-done" button (onGenDone with the stashed result).
const IN_FLIGHT_ID = 99
let nextGenResult: unknown = undefined
vi.mock("../../../components/design-agent/GenerateModal", () => ({
  GenerateModal: ({
    open,
    onGenStart,
    onKickoff,
    onGenDone,
  }: {
    open: boolean
    onGenStart?: (ctx?: unknown) => void
    onKickoff?: (id: number) => void
    onGenDone?: (r?: unknown) => void
  }) =>
    open
      ? React.createElement(
          "div",
          null,
          React.createElement(
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
          ),
          React.createElement(
            "button",
            {
              type: "button",
              "data-testid": "stub-fire-gen-done",
              onClick: () => onGenDone?.(nextGenResult),
            },
            "fire gen done",
          ),
        )
      : null,
}))

const cancelApi = vi.fn(async (_id: number): Promise<void> => undefined)
vi.mock("../../../lib/api", async () => {
  const actual = await vi.importActual<typeof import("../../../lib/api")>(
    "../../../lib/api",
  )
  return {
    ...actual,
    designAgentApi: {
      ...actual.designAgentApi,
      cancel: (id: number) => cancelApi(id),
      getActiveByPrd: vi.fn(async () => null),
      getByPrd: vi.fn(async () => null),
    },
  }
})

import { PrototypeRoute } from "../PrototypeRoute"

beforeEach(() => {
  searchString = "prd=42&generate=1"
  nextGenResult = undefined
  routerBack.mockClear()
  goTo.mockClear()
  cancelApi.mockReset()
  cancelApi.mockResolvedValue(undefined)
  __resetPageLoadGuards()
})

afterEach(() => {
  cleanup()
  vi.clearAllMocks()
  __resetPageLoadGuards()
})

describe("PrototypeRoute — cancel suppresses the false generation-error", () => {
  it("a cancelled run's terminal { ok:false } does NOT surface the error banner", async () => {
    nextGenResult = { ok: false, message: "Not found" }
    render(React.createElement(PrototypeRoute))

    // Kick off → genProtoId := 99, background-generating card up (modal closes).
    const start = await screen.findByTestId("stub-start-gen")
    await act(async () => {
      fireEvent.click(start)
    })

    // Cancel the in-flight run via the card's "Cancel generation" button →
    // handleGenCancel marks id 99 cancelled + navigates.
    await screen.findByTestId("prototype-route-generating")
    const cancelBtn = screen.getByText("Cancel generation")
    await act(async () => {
      fireEvent.click(cancelBtn)
    })
    expect(cancelApi).toHaveBeenCalledWith(IN_FLIGHT_ID)
    expect(goTo).toHaveBeenCalledWith("brief")

    // The still-running task's terminal 404 now arrives via the re-opened modal.
    const fireDone = await screen.findByTestId("stub-fire-gen-done")
    await act(async () => {
      fireEvent.click(fireDone)
    })

    // Guard holds: NO error surface for the user-cancelled id.
    await waitFor(() =>
      expect(screen.queryByTestId("prototype-route-gen-error")).toBeNull(),
    )
    expect(screen.queryByTestId("generation-error-banner")).toBeNull()
  })

  it("a genuine (never-cancelled) { ok:false } STILL surfaces the error banner", async () => {
    nextGenResult = { ok: false, message: "Generation failed" }
    render(React.createElement(PrototypeRoute))

    // No cancel: genProtoId stays null, so the guard does not apply and the real
    // failure surface renders — the no-regression control.
    const fireDone = await screen.findByTestId("stub-fire-gen-done")
    await act(async () => {
      fireEvent.click(fireDone)
    })

    await waitFor(() =>
      expect(screen.getByTestId("prototype-route-gen-error")).toBeTruthy(),
    )
    expect(screen.getByTestId("generation-error-banner")).toBeTruthy()
  })
})
