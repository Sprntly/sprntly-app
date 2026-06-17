// @vitest-environment jsdom
//
// Integration test for the redesigned no-prototype EMPTY STATE ("Value hero",
// Direction 1) on the real PrototypeRoute (NOT a leaf). It mounts the actual
// route on a no-prototype PRD (`?prd=42`, NO `?generate=1` intent), and asserts:
//
//   1. The empty state renders the Value Hero: the benefit headline "Bring this
//      PRD to life", the approved subtext, and all three value chips.
//   2. Clicking "Generate prototype" fires the EXISTING generate trigger — it
//      flips the same `generateRequested` gate the old bare button drove, which
//      mounts the GenerateModal (the stub reveals its button). The click handler
//      is byte-identical to the prior empty state's (`setGenerateRequested(true)`),
//      so this proves the wiring is reused, not re-implemented.
//   3. Button-gate preserved: on mount, with no intent, the generate panel does NOT
//      auto-open (the stub GenerateModal button is absent until the user clicks),
//      i.e. the locate/generate pipeline never fires without user intent.
//
// Mirrors the harness in prototype-route-gen-recovery.dom.test.tsx (same module-
// boundary mocks): a stubbed GenerateModal that only renders when `open`, real
// AppLayout passthrough, getActiveByPrd → null (no existing/in-flight proto →
// empty state). The ONLY difference is searchString carries no `&generate=1`.

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

// Hoisted spies so the vi.mock factories (hoisted to top of file) can close over
// them without the "cannot access before initialization" TDZ error.
const { getActiveByPrd, getByPrdNull } = vi.hoisted(() => ({
  getActiveByPrd: vi.fn(async () => null),
  getByPrdNull: vi.fn(async () => null),
}))

// ── Router / search-params (real PrototypeRoute reads useSearchParams + useRouter)
const replace = vi.fn()
const routerBack = vi.fn()
// No `&generate=1`: a plain ?prd= nav MUST land on the empty state (button-gated).
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

// ── Contexts (mocked at module boundary). Stable refs.
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

// ── GenerateModal stub: renders its button ONLY when `open`. Its mere presence
//    is the signal that the route flipped generateRequested → the generate panel
//    mounted. Absent = the gate is closed (the empty state / button-gate default).
//    Its presence/absence proves whether the panel auto-opened on mount.
vi.mock("../../../components/design-agent/GenerateModal", () => ({
  GenerateModal: ({
    open,
    onGenStart,
  }: {
    open: boolean
    onGenStart?: (ctx?: unknown) => void
  }) => {
    // Surface a callback the test can call to assert the panel is mounted; but
    // critically it only exists when `open` is true.
    return open
      ? React.createElement(
          "div",
          { "data-testid": "generate-modal-open" },
          React.createElement(
            "button",
            {
              type: "button",
              "data-testid": "stub-fire-gen-start",
              onClick: () => onGenStart?.(undefined),
            },
            "fire gen start",
          ),
        )
      : null
  },
}))

// ── api: getActiveByPrd resolves null (no existing/in-flight proto → empty
//    state). Spy on getActiveByPrd so the read-only resolve is observable but
//    never triggers a generation. getByPrd null.
vi.mock("../../../lib/api", async () => {
  const actual = await vi.importActual<typeof import("../../../lib/api")>(
    "../../../lib/api",
  )
  return {
    ...actual,
    designAgentApi: {
      ...actual.designAgentApi,
      getActiveByPrd,
      getByPrd: getByPrdNull,
    },
  }
})

import { PrototypeRoute } from "../PrototypeRoute"

beforeEach(() => {
  searchString = "prd=42" // no intent → empty state is the default landing
  replace.mockClear()
  goTo.mockClear()
  getActiveByPrd.mockClear()
  getByPrdNull.mockClear()
})

afterEach(() => {
  cleanup()
  vi.clearAllMocks()
})

/** Mount the route and wait for getActiveByPrd to settle → the empty-state hero. */
async function mountEmpty() {
  render(React.createElement(PrototypeRoute))
  await screen.findByTestId("prototype-route-empty")
}

describe("PrototypeRoute — no-prototype empty state (Value hero)", () => {
  it("renders the Value Hero: headline, subtext, and all three value chips", async () => {
    await mountEmpty()

    // Benefit-led serif headline (replaces the bare "No prototype yet").
    expect(screen.getByText("Bring this PRD to life")).toBeTruthy()

    // Approved subtext (verbatim, allowing for the normalized whitespace/JSX dash).
    const sub = screen.getByTestId("prototype-route-empty").textContent ?? ""
    expect(sub).toContain(
      "Generate an interactive, clickable prototype straight from your PRD",
    )
    expect(sub).toContain("grounded in your connected codebase")
    expect(sub).toContain("Share it and refine it with comments.")

    // Meta line (verbatim fragments with dot separators).
    expect(sub).toContain("~2–3 min")
    expect(sub).toContain("scoped against your connected repo")
    expect(sub).toContain("you'll pick the screen")

    // Three value chips.
    expect(screen.getByText("Interactive & clickable")).toBeTruthy()
    expect(screen.getByText("Matches your app's UI")).toBeTruthy()
    expect(screen.getByText("Shareable + comments")).toBeTruthy()

    // The primary action is present and labelled.
    expect(
      screen.getByRole("button", { name: /Generate prototype/i }),
    ).toBeTruthy()
  })

  it("does NOT auto-open the generate panel on mount (no auto-popup, no locate)", async () => {
    await mountEmpty()

    // The GenerateModal is NOT mounted (open=false) → the empty state is the
    // landing, the locate/generate pipeline never fired without user intent.
    expect(screen.queryByTestId("generate-modal-open")).toBeNull()
    expect(screen.queryByTestId("stub-fire-gen-start")).toBeNull()
    // The read-only resolve ran once (getActiveByPrd) but no generation kicked off.
    expect(getActiveByPrd).toHaveBeenCalledTimes(1)
  })

  it("clicking 'Generate prototype' fires the EXISTING trigger (mounts the generate panel via generateRequested)", async () => {
    await mountEmpty()

    // Panel is closed before the click (gate default-closed).
    expect(screen.queryByTestId("generate-modal-open")).toBeNull()

    await act(async () => {
      fireEvent.click(
        screen.getByRole("button", { name: /Generate prototype/i }),
      )
    })

    // The click flipped generateRequested → the GenerateModal mounted. This is
    // the SAME gate/handler the prior bare empty-state button drove, so the
    // generate wiring is reused unchanged.
    await waitFor(() =>
      expect(screen.getByTestId("generate-modal-open")).toBeTruthy(),
    )
    // The hero empty state is replaced by the generate panel.
    expect(screen.queryByTestId("prototype-route-empty")).toBeNull()
  })
})
