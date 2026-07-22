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
// Set by a test right before mounting when it needs `handleGenDone` to see a
// non-null genProtoId. Mirrors the real GenerateModal's onKickoff callback,
// which fires once the kickoff POST returns an id — independently of, and
// before, onGenDone. Left null by default so existing tests keep their
// original genProtoId === null behaviour unchanged.
let kickoffId: number | null = null
vi.mock("../../../components/design-agent/GenerateModal", () => ({
  GenerateModal: ({
    open,
    onGenDone,
    onKickoff,
  }: {
    open: boolean
    onGenDone?: (r?: unknown) => void
    onKickoff?: (id: number) => void
  }) =>
    open
      ? React.createElement(
          React.Fragment,
          null,
          React.createElement(
            "button",
            {
              type: "button",
              "data-testid": "stub-fire-kickoff",
              onClick: () => {
                if (kickoffId != null) onKickoff?.(kickoffId)
              },
            },
            "fire kickoff",
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

// ── notificationStore: spy on markPending only (the lighter option per the
//    rig note — no sessionStorage fake needed for this file). Other exports
//    stay real since PrototypeRoute's other branches (e.g. wasCancelled) rely
//    on them. vi.hoisted so the spy exists before the (hoisted) vi.mock
//    factory below references it directly (not deferred inside a closure).
const { markPending } = vi.hoisted(() => ({ markPending: vi.fn() }))
vi.mock("../../../components/design-agent/notificationStore", async () => {
  const actual = await vi.importActual<
    typeof import("../../../components/design-agent/notificationStore")
  >("../../../components/design-agent/notificationStore")
  return { ...actual, markPending }
})

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
  kickoffId = null
  replace.mockClear()
  goTo.mockClear()
  markPending.mockClear()
})

afterEach(() => {
  cleanup()
  vi.clearAllMocks()
})

/** Mount the route, wait for getActiveByPrd to settle (panel visible), then fire
 *  the stubbed terminal generation outcome through handleGenDone. */
async function mountAndFail(result: unknown, opts?: { kickoffId?: number }) {
  nextGenResult = result
  render(React.createElement(PrototypeRoute))
  // getActiveByPrd resolves null → the generate panel mounts (intent seeded open).
  const fire = await screen.findByTestId("stub-fire-gen-done")
  if (opts?.kickoffId != null) {
    kickoffId = opts.kickoffId
    await act(async () => {
      fireEvent.click(screen.getByTestId("stub-fire-kickoff"))
    })
  }
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

  it("re-arms the pending notification instead of surfacing an error for a TIMEOUT-terminal result (the run may still be going)", async () => {
    await mountAndFail(
      { ok: false, message: "Generation timed out (6 minutes)", timedOut: true },
      { kickoffId: 501 },
    )

    // The local resume-poll's wait expired; the run is still going — do NOT
    // invite a duplicate paid regeneration with a false error banner.
    expect(screen.queryByTestId("prototype-route-gen-error")).toBeNull()
    // Instead, the sessionStorage recovery path is re-armed so the shell
    // notifies honestly once the run genuinely completes.
    expect(markPending).toHaveBeenCalledTimes(1)
    expect(markPending).toHaveBeenCalledWith(501, 42)
  })

  it("does not throw when a TIMEOUT result arrives with no known prototype id yet (genProtoId still null)", async () => {
    // No kickoffId supplied — genProtoId stays at its initial null, exactly as
    // it would if a timeout raced ahead of the kickoff POST's id.
    await mountAndFail({
      ok: false,
      message: "Generation timed out (6 minutes)",
      timedOut: true,
    })

    expect(screen.queryByTestId("prototype-route-gen-error")).toBeNull()
    expect(markPending).not.toHaveBeenCalled()
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
