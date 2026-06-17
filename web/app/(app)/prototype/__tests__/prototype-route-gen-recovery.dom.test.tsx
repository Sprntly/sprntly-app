// @vitest-environment jsdom
//
// Integration test for the in-tab generation-failure RECOVERY UI on the real
// PrototypeRoute (NOT a leaf). It mounts the actual route, drives a terminal
// FAILED generation through the real `handleGenDone` chokepoint (via a stubbed
// GenerateModal that fires `onGenDone({ ok: false, message })` exactly like the
// real modal's poll outcome), and asserts the route renders the
// GenerationErrorBanner + Retry instead of silently collapsing to the bare
// empty/PRD screen.
//
// FAIL-WITHOUT-FIX direction: against the pre-fix code, handleGenDone's non-ok
// branch did ONLY setGenLoading(false) and fell through, so the route dropped
// back to the `prototype-route-empty` / generate-panel state with NO error
// surface — the `generation-error-banner` assertion fails and the
// `prototype-route-gen-error` testid is absent. The fix introduces the genError
// state + error render branch, making it pass.

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

// ── Router / search-params (real PrototypeRoute reads useSearchParams + useRouter)
const replace = vi.fn()
const routerBack = vi.fn()
// Mutable search state so we can seed `?prd=<id>` (+ optionally `?generate=1`).
let searchString = "prd=42"
vi.mock("next/navigation", () => ({
  useRouter: () => ({
    replace,
    push: vi.fn(),
    prefetch: vi.fn(),
    back: routerBack,
  }),
  useSearchParams: () => new URLSearchParams(searchString),
  usePathname: () => "/prototype",
}))

// ── Contexts (mocked at module boundary, PrdRightRail-style). Stable refs.
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

// ── AppLayout → transparent passthrough (avoid the full app shell + its deps).
vi.mock("../../../components/screens/app/AppLayout", () => ({
  AppLayout: ({ children }: { children: React.ReactNode }) =>
    React.createElement("div", { "data-testid": "app-layout" }, children),
}))

// ── GenerationLoadingScreen → inert stub (not under test here).
vi.mock("../../../components/design-agent/GenerationLoadingScreen", () => ({
  GenerationLoadingScreen: () => null,
}))

// ── GenerateModal stub: exposes a button that fires onGenDone with whatever
//    DesignAgentGenResult the test stashed — this mimics the real modal handing
//    the terminal poll outcome to the parent's handleGenDone chokepoint.
let nextGenResult: unknown = undefined
vi.mock("../../../components/design-agent/GenerateModal", () => ({
  GenerateModal: ({
    open,
    onGenDone,
  }: {
    open: boolean
    onGenDone?: (r?: unknown) => void
  }) =>
    open
      ? React.createElement(
          "button",
          {
            type: "button",
            "data-testid": "stub-fire-gen-done",
            onClick: () => onGenDone?.(nextGenResult),
          },
          "fire gen done",
        )
      : null,
}))

// ── api: getActiveByPrd resolves null (no existing/in-flight proto → generate
//    panel), getByPrd null. Real GenerationErrorBanner / boundary are NOT mocked.
vi.mock("../../../lib/api", async () => {
  const actual = await vi.importActual<typeof import("../../../lib/api")>(
    "../../../lib/api",
  )
  return {
    ...actual,
    designAgentApi: {
      ...actual.designAgentApi,
      getActiveByPrd: vi.fn(async () => null),
      getByPrd: vi.fn(async () => null),
    },
  }
})

import { PrototypeRoute } from "../PrototypeRoute"

beforeEach(() => {
  searchString = "prd=42&generate=1" // intent → generate panel opens on mount
  nextGenResult = undefined
  replace.mockClear()
  goTo.mockClear()
})

afterEach(() => {
  cleanup()
  vi.clearAllMocks()
})

/** Mount the route, wait for getActiveByPrd to settle (panel visible), then fire
 *  the stubbed terminal generation outcome through handleGenDone. */
async function mountAndFail(result: unknown) {
  nextGenResult = result
  render(React.createElement(PrototypeRoute))
  // getActiveByPrd resolves null → the generate panel mounts (intent seeded open).
  const fire = await screen.findByTestId("stub-fire-gen-done")
  await act(async () => {
    fireEvent.click(fire)
  })
}

describe("PrototypeRoute — generation-failure recovery", () => {
  it("renders the GenerationErrorBanner + Retry on a FAILED poll (does NOT collapse to empty)", async () => {
    await mountAndFail({ ok: false, message: "ViteBuildError: vite build exit=1" })

    // The dedicated error branch renders (proof it did not collapse to empty).
    await waitFor(() =>
      expect(screen.getByTestId("prototype-route-gen-error")).toBeTruthy(),
    )
    // The EXISTING GenerationErrorBanner is the surface (reused, not bespoke).
    expect(screen.getByTestId("generation-error-banner")).toBeTruthy()
    expect(screen.getByTestId("generation-error-retry")).toBeTruthy()
    // reasonCopy mapped the ViteBuildError → curated copy (raw never on the DOM).
    expect(screen.getByTestId("generation-error-message").textContent).toContain(
      "failed to build",
    )
    expect(
      screen.getByTestId("generation-error-message").textContent,
    ).not.toContain("vite build exit=1")

    // It did NOT silently collapse to the bare empty / generate-panel state.
    expect(screen.queryByTestId("prototype-route-empty")).toBeNull()
    expect(screen.queryByTestId("stub-fire-gen-done")).toBeNull()
  })

  it("surfaces the error for a TIMEOUT terminal result too", async () => {
    await mountAndFail({ ok: false, message: "Generation timed out (6 minutes)" })
    await waitFor(() =>
      expect(screen.getByTestId("prototype-route-gen-error")).toBeTruthy(),
    )
    expect(screen.getByTestId("generation-error-message").textContent).toContain(
      "timed out",
    )
  })

  it("surfaces the error for an INVALIDATED terminal result too", async () => {
    await mountAndFail({ ok: false, message: "Template invalidated; retry" })
    await waitFor(() =>
      expect(screen.getByTestId("prototype-route-gen-error")).toBeTruthy(),
    )
    expect(screen.getByTestId("generation-error-message").textContent).toContain(
      "template changed",
    )
  })

  it("surfaces the error for the no-arg (thrown / catch) onGenDone path", async () => {
    await mountAndFail(undefined)
    await waitFor(() =>
      expect(screen.getByTestId("prototype-route-gen-error")).toBeTruthy(),
    )
    expect(screen.getByTestId("generation-error-message").textContent).toContain(
      "Generation failed",
    )
  })

  it("Retry clears the error and re-opens the generate panel (re-arms kickoff)", async () => {
    await mountAndFail({ ok: false, message: "Generation failed" })
    await waitFor(() =>
      expect(screen.getByTestId("prototype-route-gen-error")).toBeTruthy(),
    )

    await act(async () => {
      fireEvent.click(screen.getByTestId("generation-error-retry"))
    })

    // Error gone, generate panel back (the stub button re-appears → re-armed).
    await waitFor(() =>
      expect(screen.queryByTestId("prototype-route-gen-error")).toBeNull(),
    )
    expect(screen.getByTestId("stub-fire-gen-done")).toBeTruthy()
  })

  it("a SUCCESS result never sets genError (success path unchanged: no error surface)", async () => {
    // A ready row with a bundle → handleGenDone reveals it; no error branch.
    const proto = {
      id: 7,
      prd_id: 42,
      status: "ready",
      bundle_url: "https://cdn/x/bundle/index.html",
      is_complete: false,
      share_token: null,
    }
    await mountAndFail({ ok: true, prototype: proto })
    // No error surface at any point.
    await waitFor(() =>
      expect(screen.queryByTestId("prototype-route-gen-error")).toBeNull(),
    )
    expect(screen.queryByTestId("generation-error-banner")).toBeNull()
  })
})
