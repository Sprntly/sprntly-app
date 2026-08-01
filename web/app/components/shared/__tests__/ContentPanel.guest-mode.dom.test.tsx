// @vitest-environment jsdom
//
// Guest-mode guards on ContentPanel: the Evidence tab's prdMeta effect and the
// Tickets tab's generation effect must never authed-fetch when useGuestSession()
// is non-null (AC11/AC12), and the READ-ONLY badge / disabled Share / context
// strip render only in guest mode (AC15/AC16). Additive alongside the existing
// ContentPanel.*.dom.test.tsx suite — none of those files are modified.
import * as React from "react"
import { cleanup, render, screen } from "@testing-library/react"
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest"

;(globalThis as typeof globalThis & { React?: typeof React }).React = React

vi.hoisted(() => {
  // eslint-disable-next-line @typescript-eslint/no-require-imports
  ;(globalThis as Record<string, unknown>).React = require("react")
})

vi.mock("next/navigation", () => ({
  useRouter: () => ({ push: vi.fn(), replace: vi.fn(), prefetch: vi.fn() }),
}))

vi.mock("../PrdPanelContent", () => ({
  PrdPanelContent: () => React.createElement("div", { "data-testid": "prd-body" }),
}))

const loadEvidenceByInsightMock = vi.fn()
vi.mock("../../../lib/runEvidenceGeneration", () => ({
  runEvidenceGeneration: vi.fn(),
  loadEvidenceByInsight: (...a: unknown[]) => loadEvidenceByInsightMock(...a),
}))

const storiesApiMock = vi.hoisted(() => ({
  generate: vi.fn(),
  getJob: vi.fn(),
  getForPrd: vi.fn(),
}))
vi.mock("../../../lib/api", async () => {
  const actual = await vi.importActual<typeof import("../../../lib/api")>("../../../lib/api")
  return {
    ...actual,
    storiesApi: storiesApiMock,
  }
})

const navMock = vi.hoisted(() => ({ openContentPanel: vi.fn(), tab: "prd" as string }))
vi.mock("../../../context/NavigationContext", () => ({
  useNavigation: () => ({
    contentPanelTab: navMock.tab,
    openContentPanel: navMock.openContentPanel,
    closeContentPanel: vi.fn(),
    showToast: vi.fn(),
    expandAiPanel: vi.fn(),
    setAIBarValue: vi.fn(),
  }),
}))

const contentMock = vi.hoisted(() => ({ value: {} as Record<string, unknown> }))
// setContent must be a STABLE reference across renders (the real
// ContentContext memoizes it via useCallback) — a fresh vi.fn() per call
// would make every effect that lists setContent in its deps re-fire on every
// render, and since this mock never actually updates contentMock.value, the
// evidence-loading effect's "already loaded" guard can never become true —
// infinite effect retrigger → unbounded fetch loop → OOM.
const setContentMock = vi.fn()
vi.mock("../../../context/ContentContext", () => ({
  useContent: () => ({ content: contentMock.value, setContent: setContentMock }),
}))

import { ContentPanel } from "../ContentPanel"
import { GuestSessionProvider } from "../../../context/GuestSessionContext"
import type { PrdState } from "../../../types/content"

const prd: PrdState = {
  prd_id: 1,
  title: "Q3 Retention PRD",
  metaLine: "",
  sections: [],
  source: "brief",
  briefId: 9,
  insightIndex: 2,
}

const GUEST_SESSION = {
  token: "tok-1",
  sharerName: "Priya Shah",
  owningCompanyName: "Acme Co",
  artifactId: 482,
}

function renderPanel(opts: {
  tab: "prd" | "evidence" | "tickets"
  guest?: boolean
  guestTickets?: unknown[]
}) {
  navMock.tab = opts.tab
  contentMock.value = {
    prd,
    prdMeta: { briefId: 9, insightIndex: 2 },
    evidence: opts.guest ? { metaLine: "", title: "Evidence", sections: [] } : null,
    evidenceGenerating: false,
    detail: null,
    guestTickets: opts.guestTickets ?? [],
    connectedConnectorIds: [],
    threadReports: [],
    threadReportsStatus: "idle",
    reportFocusId: null,
  }
  const tree = <ContentPanel />
  return render(
    opts.guest ? <GuestSessionProvider value={GUEST_SESSION}>{tree}</GuestSessionProvider> : tree,
  )
}

beforeEach(() => {
  loadEvidenceByInsightMock.mockResolvedValue(null)
  storiesApiMock.generate.mockResolvedValue({ job_id: 1 })
  storiesApiMock.getJob.mockResolvedValue({ status: "ready", stories: [] })
  storiesApiMock.getForPrd.mockResolvedValue({ status: "ready", fresh: true, stories: [] })
})

afterEach(() => {
  cleanup()
  vi.clearAllMocks()
})

describe("ContentPanel — guest mode", () => {
  it("test_content_panel_guest_mode_evidence_effect_skips_fetch — AC11", () => {
    renderPanel({ tab: "evidence", guest: true })
    expect(loadEvidenceByInsightMock).not.toHaveBeenCalled()
  })

  it("fetches evidence via loadEvidenceByInsight for a non-guest render with the same prdMeta", () => {
    renderPanel({ tab: "evidence", guest: false })
    expect(loadEvidenceByInsightMock).toHaveBeenCalledWith(9, 2)
  })

  it("test_content_panel_guest_mode_tickets_effect_skips_stories_api — AC12", () => {
    renderPanel({ tab: "tickets", guest: true, guestTickets: [{ title: "Ticket one" }] })
    expect(storiesApiMock.generate).not.toHaveBeenCalled()
    expect(storiesApiMock.getJob).not.toHaveBeenCalled()
    expect(storiesApiMock.getForPrd).not.toHaveBeenCalled()
  })

  it("test_content_panel_guest_mode_tickets_render_from_context — renders the guestTickets count, no network", () => {
    renderPanel({
      tab: "tickets",
      guest: true,
      guestTickets: [{ title: "Ticket one" }, { title: "Ticket two" }],
    })
    expect(screen.getByText(/2 implementable tickets/)).not.toBeNull()
  })

  it("test_content_panel_guest_mode_shows_read_only_badge_and_context_strip — AC16", () => {
    renderPanel({ tab: "prd", guest: true })
    expect(screen.getByTestId("cpanel-readonly-badge")).not.toBeNull()
    expect(document.querySelector('[data-testid="share-context-strip"]')).not.toBeNull()
  })

  it("test_content_panel_guest_mode_share_button_disabled_with_reason — AC15", () => {
    renderPanel({ tab: "prd", guest: true })
    const shareBtn = screen.getByRole("button", { name: /share/i })
    expect(shareBtn).toHaveProperty("disabled", true)
    expect(shareBtn.getAttribute("title") || shareBtn.getAttribute("aria-label")).toMatch(
      /sign in/i,
    )
  })

  it("does not show the guest badge/strip or disable Share in normal (non-guest) mode", () => {
    renderPanel({ tab: "prd", guest: false })
    expect(screen.queryByTestId("cpanel-readonly-badge")).toBeNull()
    expect(document.querySelector('[data-testid="share-context-strip"]')).toBeNull()
    const shareBtn = screen.getByRole("button", { name: /share/i })
    expect(shareBtn).toHaveProperty("disabled", false)
  })

  it("test_content_panel_guest_mode_all_three_tabs_enabled — AC14", () => {
    renderPanel({ tab: "prd", guest: true })
    expect(screen.getByRole("button", { name: /Evidence/i })).not.toBeNull()
    expect(screen.getByRole("button", { name: /PRD/i })).not.toBeNull()
    expect(screen.getByRole("button", { name: /Tickets/i })).not.toBeNull()
  })

  it("test_content_panel_guest_mode_no_write_affordances_present — AC15", () => {
    renderPanel({ tab: "tickets", guest: true, guestTickets: [] })
    // The bottom pipeline bar (Generate Prototype — a mutation trigger) is
    // withheld entirely in guest mode.
    expect(screen.queryByTestId("tickets-footer-prototype-cta")).toBeNull()
  })
})
