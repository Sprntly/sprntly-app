// @vitest-environment jsdom
//
// BriefChat — Generate-PRD / Generate-Prototype CTA gating (June 20 #9.1).
//
// "Without date, no need to show Generate PRD and Generate Prototype" →
// clarified to: when the brief has NO real data behind it (the empty /
// insufficient-evidence / placeholder case), hide the Generate-PRD and
// Generate-Prototype affordances. They only make sense once there are real
// findings to act on.
//
// These tests mount the real BriefChat inside the real Navigation + Content
// providers and drive `content.briefV2` imperatively, proving:
//   1. a normal brief WITH findings still shows "Generate PRD" + "View
//      prototype" finding-card CTAs (behavior unchanged).
//   2. an insufficient-evidence empty brief shows NEITHER CTA — only the
//      greeting / "add more sources" guidance remains.
//   3. a plain empty brief (no flag, no findings) likewise shows neither CTA.
//
// Mirrors the mocking convention of BriefChat.generating.dom.test.tsx (api /
// pipeline / next-navigation / workspace stubbed so mounting hits no network).
import * as React from "react"
import { act, cleanup, fireEvent, render, screen } from "@testing-library/react"
import { afterEach, describe, expect, it, vi } from "vitest"

;(globalThis as typeof globalThis & { React?: typeof React }).React = React

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

vi.mock("../../../lib/usePipelineStatus", () => ({
  usePipelineStatus: () => ({
    runStatus: null,
    isTriggering: false,
    showCompleted: false,
    triggerRun: vi.fn(),
  }),
}))

vi.mock("next/navigation", () => ({
  useRouter: () => ({ push: vi.fn() }),
  usePathname: () => "/brief",
}))

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

// A normal brief — one prototypeable hero finding, so the card renders both the
// "Generate PRD" primary CTA and the "View prototype" secondary CTA.
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
  insufficientEvidence: false,
  emptyReason: null,
}

// An EMPTY brief the backend saved because the KG lacked enough connected-source
// evidence (no hero / supporting, insufficientEvidence flag set).
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

// A plain EMPTY brief — brand-new account, no data, no flag.
const PLAIN_EMPTY_BRIEF: BriefV2State = {
  ...INSUFFICIENT_EVIDENCE_BRIEF,
  insufficientEvidence: false,
}

function Harness() {
  const { setContent } = useContent()
  const set = (patch: Partial<AppContentState>) => setContent(patch)
  return React.createElement(
    "div",
    null,
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

const GENERATE_PRD = /Generate PRD/
const VIEW_PROTOTYPE = /View prototype/
const EMPTY_GREETING = "Please add more sources"

afterEach(() => {
  cleanup()
  vi.clearAllMocks()
})

describe("BriefChat — Generate-PRD / Generate-Prototype CTA gating", () => {
  it("test_ready_brief_shows_generate_ctas: a normal brief with findings still renders the Generate PRD + View prototype CTAs", () => {
    mountHarness()
    act(() => {
      fireEvent.click(screen.getByTestId("set-ready"))
    })

    // The finding card is on screen…
    expect(document.querySelector(".fc-title")?.textContent).toBe(
      "Day-30 retention is slipping",
    )
    // …with the Generate-PRD CTA and the View-prototype CTA (unchanged behavior).
    expect(document.querySelector(".fc-actions")).not.toBeNull()
    expect(screen.getByText(GENERATE_PRD)).not.toBeNull()
    expect(screen.getByText(VIEW_PROTOTYPE)).not.toBeNull()
  })

  it("test_insufficient_evidence_hides_generate_ctas: an insufficient-evidence empty brief renders NO Generate PRD / Generate Prototype CTA", () => {
    mountHarness()
    act(() => {
      fireEvent.click(screen.getByTestId("set-insufficient-evidence"))
    })

    // The encouraging insufficient-evidence greeting is shown…
    expect(screen.getByText(/We've got your data/)).not.toBeNull()
    // …and NO generate affordance renders anywhere on the brief.
    expect(screen.queryByText(GENERATE_PRD)).toBeNull()
    expect(screen.queryByText(VIEW_PROTOTYPE)).toBeNull()
    expect(document.querySelector(".fc-actions")).toBeNull()
  })

  it("test_plain_empty_hides_generate_ctas: a plain empty brief (no findings, no flag) renders NO Generate PRD / Generate Prototype CTA", () => {
    mountHarness()
    act(() => {
      fireEvent.click(screen.getByTestId("set-plain-empty"))
    })

    // Only the add-more-sources guidance remains…
    expect(screen.getByText(new RegExp(EMPTY_GREETING))).not.toBeNull()
    // …with no generate CTAs.
    expect(screen.queryByText(GENERATE_PRD)).toBeNull()
    expect(screen.queryByText(VIEW_PROTOTYPE)).toBeNull()
    expect(document.querySelector(".fc-actions")).toBeNull()
  })
})
