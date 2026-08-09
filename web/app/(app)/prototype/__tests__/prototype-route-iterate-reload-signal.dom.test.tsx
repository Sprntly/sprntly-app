// @vitest-environment jsdom
//
// Proves InTabCanvas's onComplete callback no longer force-bumps
// bundleReloadNonce on an iterate completion (Race A closed at its source),
// while the manual "Refresh preview" trigger (onRefreshBundle) still does —
// that path is orthogonal to checkpoint tracking and untouched by this fix.
//
// Follows PrototypeRoute.pidparam.test.tsx's mocking scaffold. PostGenerationResult
// is mocked (necessarily — isolating InTabCanvas's own wiring) to a stub that
// exposes its received `bundleReloadNonce` prop via a data-* attribute, plus a
// button wired to `onRefreshBundle`. useIterateRun is additionally mocked so the
// test can synchronously invoke the `onComplete` callback the real hook was
// constructed with — the real hook's own SSE/poll machinery is irrelevant here;
// only InTabCanvas's onComplete wiring is under test.
//
// Intentional gap this file does NOT close: because PostGenerationResult (and
// therefore useViewGrant) is mocked here, this file cannot prove anything about
// useViewGrant's own reload-timing behavior. That end-to-end proof lives in
// PostGenerationResult.grant.dom.test.tsx's
// test_post_generation_result_defers_iframe_reload_until_checkpoint_advance_mint_resolves.
import * as React from "react"
import { act, cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react"
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest"

vi.hoisted(() => {
  ;(globalThis as Record<string, unknown>).React = require("react")
})

const apiMocks = vi.hoisted(() => ({
  getActiveByPrd: vi.fn(),
  getLatestByPrd: vi.fn(),
}))

let searchString = "prd=1"
vi.mock("next/navigation", () => ({
  useRouter: () => ({
    replace: vi.fn(),
    push: vi.fn(),
    prefetch: vi.fn(),
    back: vi.fn(),
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

vi.mock("../../../components/design-agent/GenerationLoadingScreen", () => ({
  GenerationLoadingScreen: () => null,
}))

vi.mock("../../../components/design-agent/GenerateModal", () => ({
  GenerateModal: () => null,
}))

// Captures the onComplete callback the REAL InTabCanvas constructs the real
// useIterateRun with, so the test can invoke it synchronously — exactly the
// "test seam" the ticket's Implementation Notes describe for this new file.
const iterateRunHandle = vi.hoisted(() => ({
  onComplete: null as null | ((fresh: unknown, opts?: { reloadBundle?: boolean }) => void),
}))

vi.mock("../../../components/design-agent/useIterateRun", () => ({
  useIterateRun: ({
    onComplete,
  }: {
    onComplete: (fresh: unknown, opts?: { reloadBundle?: boolean }) => void
  }) => {
    iterateRunHandle.onComplete = onComplete
    return {
      running: false,
      activity: [],
      pendingQuestion: null,
      error: null,
      answerQuestion: vi.fn(),
      dismissQuestion: vi.fn(async () => {}),
      runIterate: vi.fn(),
      appendActivity: vi.fn(),
    }
  },
}))

vi.mock("../../../components/design-agent/PostGenerationResult", () => ({
  PostGenerationResult: ({
    prototype,
    bundleReloadNonce,
    onRefreshBundle,
  }: {
    prototype: { id: number }
    bundleReloadNonce?: number
    onRefreshBundle?: () => void
  }) =>
    React.createElement(
      "div",
      {
        "data-testid": "rendered-prototype",
        "data-bundle-reload-nonce": String(bundleReloadNonce ?? 0),
      },
      React.createElement(
        "button",
        {
          type: "button",
          "data-testid": "stub-refresh-bundle",
          onClick: onRefreshBundle,
        },
        "refresh",
      ),
      `prototype ${prototype.id}`,
    ),
}))

vi.mock("../../../lib/api", async () => {
  const actual = await vi.importActual<typeof import("../../../lib/api")>(
    "../../../lib/api",
  )
  return {
    ...actual,
    designAgentApi: {
      ...actual.designAgentApi,
      getActiveByPrd: apiMocks.getActiveByPrd,
      getLatestByPrd: apiMocks.getLatestByPrd,
    },
  }
})

import { PrototypeRoute } from "../PrototypeRoute"

const readyProto = {
  id: 250,
  prd_id: 1,
  status: "ready",
  bundle_url: "https://cdn.example/prototype/index.html",
  is_complete: false,
  share_token: null,
  current_checkpoint_id: 1,
}

beforeEach(() => {
  searchString = "prd=1"
  iterateRunHandle.onComplete = null
  apiMocks.getActiveByPrd.mockReset()
  apiMocks.getLatestByPrd.mockReset()
  apiMocks.getLatestByPrd.mockResolvedValue(null)
  apiMocks.getActiveByPrd.mockResolvedValue(readyProto)
})

afterEach(() => {
  cleanup()
  vi.clearAllMocks()
})

describe("InTabCanvas — no longer force-bumps bundleReloadNonce on iterate completion", () => {
  it("test_in_tab_canvas_iterate_complete_no_longer_force_bumps_bundle_reload_nonce", async () => {
    render(React.createElement(PrototypeRoute))

    await waitFor(() =>
      expect(screen.getByTestId("rendered-prototype").textContent).toContain("250"),
    )
    expect(iterateRunHandle.onComplete).not.toBeNull()

    const before = screen
      .getByTestId("rendered-prototype")
      .getAttribute("data-bundle-reload-nonce")
    expect(before).toBe("0")

    // Fire the REAL onComplete the real InTabCanvas wired useIterateRun with,
    // exactly as a completed iterate would (opts.reloadBundle: true — today's
    // unfixed code bumps bundleReloadNonce unconditionally here).
    act(() => {
      iterateRunHandle.onComplete?.(readyProto, { reloadBundle: true })
    })

    const after = screen
      .getByTestId("rendered-prototype")
      .getAttribute("data-bundle-reload-nonce")
    expect(after).toBe(before) // UNCHANGED — no longer forces a reload here
  })

  it("test_in_tab_canvas_manual_refresh_still_bumps_bundle_reload_nonce", async () => {
    render(React.createElement(PrototypeRoute))

    await waitFor(() =>
      expect(screen.getByTestId("rendered-prototype").textContent).toContain("250"),
    )
    const before = Number(
      screen.getByTestId("rendered-prototype").getAttribute("data-bundle-reload-nonce"),
    )

    fireEvent.click(screen.getByTestId("stub-refresh-bundle"))

    const after = Number(
      screen.getByTestId("rendered-prototype").getAttribute("data-bundle-reload-nonce"),
    )
    expect(after).toBe(before + 1) // the manual affordance is UNCHANGED by this ticket
  })
})
