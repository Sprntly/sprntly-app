// @vitest-environment jsdom
//
// BriefChat WIP / "generating" indicator.
//
// When the backend is generating this week's brief, useBriefHydration (called
// once in AppShell) reports kind === "generating" and mirrors it into
// ContentContext as `content.briefHydration`. BriefChat reads that flag and, as
// long as there's no brief to show yet, renders a distinct spinner + "Generating
// your Monday brief…" WIP block IN PLACE OF the empty greeting / finding cards.
//
// These tests mount the real BriefChat inside the real Navigation + Content
// providers and drive `content` through a small harness, proving:
//   1. generating  → the WIP indicator renders; the empty greeting does NOT.
//   2. ready (brief present) → the brief greeting/findings render; NO WIP.
//   3. failed      → the failure path renders the normal empty greeting, never
//      the WIP indicator (we must not regress the failed/empty messaging).
//
// jsdom is opted into per-file (the global vitest config stays node-env), the
// same convention the other *.dom.test.tsx files in this repo follow. The api /
// pipeline / next-navigation modules are mocked so mounting BriefChat doesn't
// hit the network or a real router.
import * as React from "react"
import { act, cleanup, fireEvent, render, screen } from "@testing-library/react"
import { afterEach, describe, expect, it, vi } from "vitest"

;(globalThis as typeof globalThis & { React?: typeof React }).React = React

// BriefChat's composer/agent flows import the api module; mock it so nothing
// touches the network on mount.
vi.mock("../../../lib/api", () => {
  class ApiError extends Error {
    status = 0
  }
  return {
    ApiError,
    askApi: { ask: vi.fn() },
    briefApi: {
      current: vi.fn(),
      status: vi.fn(),
      regenerate: vi.fn(),
    },
  }
})

// usePipelineStatus polls a route on mount; stub it to an idle result.
vi.mock("../../../lib/usePipelineStatus", () => ({
  usePipelineStatus: () => ({
    runStatus: null,
    isTriggering: false,
    showCompleted: false,
    triggerRun: vi.fn(),
  }),
}))

// NavigationProvider depends on next/navigation — stub the router/pathname.
vi.mock("next/navigation", () => ({
  useRouter: () => ({ push: vi.fn() }),
  usePathname: () => "/brief",
}))

// BriefChat reads the active workspace via useWorkspace(), which throws outside a
// WorkspaceProvider; mock it to an idle workspace (these tests don't exercise it).
vi.mock("../../../context/WorkspaceContext", () => ({
  useWorkspace: () => ({
    loading: false,
    profile: null,
    workspace: null,
    refresh: async () => {},
  }),
}))

import { NavigationProvider } from "../../../context/NavigationContext"
import { ContentProvider, useContent } from "../../../context/ContentContext"
import type { AppContentState } from "../../../types/content"
import type { BriefV2State } from "../../../lib/brief-v2-adapter"
import { BriefChat } from "../BriefChat"

// A minimal ready brief — one hero finding so the "ready" render has something
// to show (greeting + finding card) and the WIP block must be absent.
const READY_BRIEF: BriefV2State = {
  headline: "This week",
  weekOf: "2026-06-08",
  company: "Asurion",
  productArea: "",
  kpiTiles: [],
  hero: {
    kind: "hero",
    detailKey: "fix-0",
    actionAccent: "fix",
    actionLabel: "FIX",
    tagType: "fix",
    tagLabel: "WHAT'S BROKEN",
    category: "Retention",
    priority: "P0",
    confidence: 0.82,
    prototypeable: true,
    title: "Day-30 retention is slipping",
    body: "Retention dropped 6 points week over week.",
    metricHighlight: "",
    statTiles: [],
    chart: null,
    convergence: [],
    secondaryCtaLabel: "",
    secondaryCtaBehavior: "open_analysis",
    askQuestion: "Why is retention slipping?",
    quote: null,
  },
  supporting: [],
  sourcesLine: "",
}

// Harness: renders BriefChat plus a hidden button per state we want to set, so
// each test can flip `content` (briefHydration + briefV2) imperatively.
function Harness() {
  const { setContent } = useContent()
  const set = (patch: Partial<AppContentState>) => setContent(patch)
  return React.createElement(
    "div",
    null,
    React.createElement(
      "button",
      {
        "data-testid": "set-generating",
        onClick: () => set({ briefHydration: "generating", briefV2: null }),
      },
      "generating",
    ),
    React.createElement(
      "button",
      {
        "data-testid": "set-ready",
        onClick: () => set({ briefHydration: "ready", briefV2: READY_BRIEF }),
      },
      "ready",
    ),
    React.createElement(
      "button",
      {
        "data-testid": "set-failed",
        onClick: () => set({ briefHydration: "failed", briefV2: null }),
      },
      "failed",
    ),
    React.createElement(BriefChat),
  )
}

function mountHarness() {
  return render(
    React.createElement(
      NavigationProvider,
      null,
      React.createElement(ContentProvider, null, React.createElement(Harness)),
    ),
  )
}

const WIP_TITLE = "Generating your Monday brief…"
const EMPTY_GREETING = "I don't see a brief for this week yet"

afterEach(() => {
  cleanup()
  vi.clearAllMocks()
})

describe("BriefChat — generating / WIP indicator", () => {
  it("test_generating_shows_wip_not_empty: generating renders the WIP block and hides the empty greeting", () => {
    mountHarness()
    act(() => {
      fireEvent.click(screen.getByTestId("set-generating"))
    })

    // The WIP indicator is on screen…
    expect(screen.getByText(WIP_TITLE)).not.toBeNull()
    expect(
      screen.getByText("Analyzing your sources — this usually takes a minute."),
    ).not.toBeNull()
    // …it carries the live-region role and the spinner element…
    const status = screen.getByRole("status")
    expect(status.querySelector(".bc-generating-spinner")).not.toBeNull()
    // …the header status line reflects WIP…
    expect(screen.getByText(/Monday brief · generating…/)).not.toBeNull()
    // …and the empty "no brief yet" greeting is NOT shown.
    expect(screen.queryByText(new RegExp(EMPTY_GREETING))).toBeNull()
  })

  it("test_ready_shows_brief_not_wip: a ready brief renders the greeting/finding and never the WIP block", () => {
    mountHarness()
    act(() => {
      fireEvent.click(screen.getByTestId("set-ready"))
    })

    // The real brief is shown (finding card title from READY_BRIEF). The title
    // text also echoes in the prototype-preview caption, so target the card
    // title element specifically rather than a text-match (which finds both).
    const title = document.querySelector(".fc-title")
    expect(title?.textContent).toBe("Day-30 retention is slipping")
    // …and the WIP indicator is gone.
    expect(screen.queryByText(WIP_TITLE)).toBeNull()
    expect(document.querySelector(".bc-generating")).toBeNull()
  })

  it("test_generating_then_ready_replaces_wip: WIP is replaced by the brief once hydration flips to ready", () => {
    mountHarness()
    act(() => {
      fireEvent.click(screen.getByTestId("set-generating"))
    })
    expect(screen.getByText(WIP_TITLE)).not.toBeNull()

    act(() => {
      fireEvent.click(screen.getByTestId("set-ready"))
    })
    // The WIP block has been replaced by the real brief.
    expect(screen.queryByText(WIP_TITLE)).toBeNull()
    expect(document.querySelector(".fc-title")?.textContent).toBe(
      "Day-30 retention is slipping",
    )
  })

  it("test_failed_shows_empty_messaging_not_wip: failed hydration shows the empty/failure greeting, never the WIP block", () => {
    mountHarness()
    act(() => {
      fireEvent.click(screen.getByTestId("set-failed"))
    })

    // The failed/empty path keeps the normal "no brief yet" greeting…
    expect(screen.getByText(new RegExp(EMPTY_GREETING))).not.toBeNull()
    // …and does NOT render the generating WIP indicator.
    expect(screen.queryByText(WIP_TITLE)).toBeNull()
    expect(document.querySelector(".bc-generating")).toBeNull()
  })
})
