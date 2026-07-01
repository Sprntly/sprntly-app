// @vitest-environment jsdom
//
// BriefChat WIP / "generating" indicator.
//
// When the backend is generating this week's brief, useBriefHydration (called
// once in AppShell) reports kind === "generating" and mirrors it into
// ContentContext as `content.briefHydration`. BriefChat reads that flag and, as
// long as there's no brief to show yet, renders a distinct spinner + "Generating
// your Weekly brief…" WIP block IN PLACE OF the empty greeting / finding cards.
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
    skillType: "reliability",
    skillAccent: "#c0473c",
    skillLabel: "Reliability",
    ctas: [{ label: "View PRD", style: "primary" }, { label: "View prototype", style: "ghost" }],
    category: "Retention",
    priority: "P0",
    confidence: 0.82,
    prototypeable: true,
    title: "Day-30 retention is slipping",
    body: "Retention dropped 6 points week over week.",
    metricHighlight: "",
    fromSources: [],
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
  insufficientEvidence: false,
  emptyReason: null,
}

// An EMPTY brief (no hero/supporting) the backend saved because the KG lacked
// enough connected-source evidence — `insufficientEvidence` distinguishes it
// from a brand-new no-data account.
const INSUFFICIENT_EVIDENCE_BRIEF: BriefV2State = {
  headline: null,
  weekOf: null,
  company: "Asurion",
  productArea: "",
  kpiTiles: [],
  hero: null,
  supporting: [],
  sourcesLine: "",
  insufficientEvidence: true,
  emptyReason: null,
}

// A plain EMPTY brief — brand-new account, no data uploaded yet.
const PLAIN_EMPTY_BRIEF: BriefV2State = {
  ...INSUFFICIENT_EVIDENCE_BRIEF,
  insufficientEvidence: false,
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
    React.createElement(
      "button",
      {
        "data-testid": "set-insufficient-evidence",
        onClick: () =>
          set({ briefHydration: "ready", briefV2: INSUFFICIENT_EVIDENCE_BRIEF }),
      },
      "insufficient-evidence",
    ),
    React.createElement(
      "button",
      {
        "data-testid": "set-plain-empty",
        onClick: () => set({ briefHydration: "ready", briefV2: PLAIN_EMPTY_BRIEF }),
      },
      "plain-empty",
    ),
    React.createElement(
      "button",
      {
        "data-testid": "set-refreshing-over-ready",
        onClick: () =>
          set({ briefHydration: "ready", briefV2: READY_BRIEF, briefRegenerating: true }),
      },
      "refreshing-over-ready",
    ),
    React.createElement(
      "button",
      {
        "data-testid": "set-refreshing-no-brief",
        onClick: () =>
          set({ briefHydration: "generating", briefV2: null, briefRegenerating: true }),
      },
      "refreshing-no-brief",
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

const WIP_TITLE = "Generating your Weekly brief…"
const EMPTY_GREETING = "add and connect more sources"

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
    // …the WIP block replaces the greeting while generating: the single greeting
    // paragraph (salutation + capability + top-N tail) is not rendered yet…
    expect(document.querySelector(".bc-greeting")).toBeNull()
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

  it("test_insufficient_evidence_shows_distinct_greeting: an empty brief flagged insufficientEvidence acknowledges the upload", () => {
    mountHarness()
    act(() => {
      fireEvent.click(screen.getByTestId("set-insufficient-evidence"))
    })

    // The encouraging "we received your data" message is shown…
    expect(screen.getByText(/We've got your data/)).not.toBeNull()
    expect(
      screen.getByText(/there isn't enough connected evidence yet to build/),
    ).not.toBeNull()
    // …and the brand-new-empty "add more sources" copy is NOT used.
    expect(screen.queryByText(new RegExp(EMPTY_GREETING))).toBeNull()
  })

  it("test_plain_empty_shows_original_greeting: a plain empty brief (no flag) keeps the add-more-sources copy", () => {
    mountHarness()
    act(() => {
      fireEvent.click(screen.getByTestId("set-plain-empty"))
    })

    // The original brand-new-empty greeting is shown…
    expect(screen.getByText(new RegExp(EMPTY_GREETING))).not.toBeNull()
    // …and the insufficient-evidence acknowledgement is NOT.
    expect(screen.queryByText(/We've got your data/)).toBeNull()
  })

  it("test_refreshing_over_ready_shows_banner: regenerating over an existing brief shows the refresh banner above the brief, not the full WIP state", () => {
    mountHarness()
    act(() => {
      fireEvent.click(screen.getByTestId("set-refreshing-over-ready"))
    })

    // The lightweight "refreshing" banner is on screen with its live-region role.
    const banner = document.querySelector(".bc-refreshing")
    expect(banner).not.toBeNull()
    expect(screen.getByText(/Refreshing your brief/)).not.toBeNull()
    expect(banner?.querySelector(".bc-refreshing-spinner")).not.toBeNull()
    // The existing brief stays readable underneath (non-destructive)…
    expect(document.querySelector(".fc-title")?.textContent).toBe(
      "Day-30 retention is slipping",
    )
    // …and the full-screen generating WIP block is NOT used here.
    expect(screen.queryByText(WIP_TITLE)).toBeNull()
    expect(document.querySelector(".bc-generating")).toBeNull()
  })

  it("test_refreshing_without_brief_uses_full_wip_not_banner: with no brief yet, the full generating state owns the surface and the banner is suppressed", () => {
    mountHarness()
    act(() => {
      fireEvent.click(screen.getByTestId("set-refreshing-no-brief"))
    })

    // No brief to keep on screen → the full generating WIP block is shown…
    expect(screen.getByText(WIP_TITLE)).not.toBeNull()
    // …and the lightweight refresh banner is NOT (it would be redundant).
    expect(document.querySelector(".bc-refreshing")).toBeNull()
  })
})
