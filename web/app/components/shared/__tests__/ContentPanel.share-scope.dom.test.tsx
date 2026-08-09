// @vitest-environment jsdom
//
// Regression coverage for the content panel's PRD-acting controls (Share,
// header title, Tickets prototype CTA) reading a scoped PRD rather than the
// raw global `content.prd` slot. Reproduces the exact live sequence: a PRD is
// loaded and viewed, then an evidence document belonging to a DIFFERENT PRD is
// opened while the cached PRD is left in the shared slot — no control may act
// on the stale PRD once that evidence document is on screen.
import * as React from "react"
import { readFileSync } from "node:fs"
import { join } from "node:path"
import { act, cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react"
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
// artifactsList/evidenceGet back ArtifactsScreen, which this file also renders
// (for the cross-component "exact live sequence" regression below) — it shares
// this same resolved module with ContentPanel.
const artifactsListMock = vi.fn()
const evidenceGetMock = vi.fn()
vi.mock("../../../lib/api", async () => {
  const actual = await vi.importActual<typeof import("../../../lib/api")>("../../../lib/api")
  return {
    ...actual,
    storiesApi: storiesApiMock,
    artifactsApi: { list: (...a: unknown[]) => artifactsListMock(...a) },
    evidenceApi: { get: (...a: unknown[]) => evidenceGetMock(...a) },
  }
})

vi.mock("../../../context/CompanyContext", () => ({
  useCompany: () => ({ activeCompany: "acme" }),
}))
vi.mock("../../../lib/evidence-adapter", () => ({
  markdownToEvidenceState: (md: string) => ({ title: "Evidence A", metaLine: "", sections: [], md }),
}))
vi.mock("../../../lib/routes", () => ({ prototypePath: () => "/prototype" }))
vi.mock("../../screens/app/AppLayout", () => ({
  AppLayout: ({ children }: { children: React.ReactNode }) =>
    React.createElement("div", { "data-testid": "app-layout" }, children),
}))

const mintMock = vi.fn()
vi.mock("../../../lib/artifactShareApi", () => ({
  artifactShareApi: { mint: (...a: unknown[]) => mintMock(...a) },
}))

// Needed only because rendering the Tickets tab mounts GeneratePrototypeCTA,
// which reads useWorkspace — unrelated to the scope invariant under test.
vi.mock("../../../context/WorkspaceContext", () => ({
  useWorkspace: () => ({ loading: false, profile: null, workspace: null, refresh: async () => {} }),
}))

const navMock = vi.hoisted(() => ({ openContentPanel: vi.fn(), tab: "prd" as string }))
vi.mock("../../../context/NavigationContext", () => ({
  useNavigation: () => ({
    contentPanelTab: navMock.tab,
    openContentPanel: navMock.openContentPanel,
    closeContentPanel: vi.fn(),
    showToast: vi.fn(),
    expandAiPanel: vi.fn(),
    setAIBarValue: vi.fn(),
    openPrdTab: vi.fn(),
    openReportTab: vi.fn(),
  }),
}))

const contentMock = vi.hoisted(() => ({ value: {} as Record<string, unknown> }))
// setContent must be a STABLE reference across renders — see
// ContentPanel.guest-mode.dom.test.tsx for why a fresh vi.fn() per call would
// unbound-loop the evidence-loading effect. It also merges into contentMock.value
// (mirroring the real ContentProvider's mergeContent) so the cross-component
// "exact live sequence" regression below can observe ArtifactsScreen's writes
// from a subsequently-rendered ContentPanel.
const setContentMock = vi.fn((patch: Record<string, unknown>) => {
  Object.assign(contentMock.value, patch)
})
vi.mock("../../../context/ContentContext", () => ({
  useContent: () => ({ content: contentMock.value, setContent: setContentMock }),
}))

import { ContentPanel, isEvidenceTabHidden } from "../ContentPanel"
import { GuestSessionProvider } from "../../../context/GuestSessionContext"
import { ArtifactsScreen } from "../../screens/app/ArtifactsScreen"
import type { PrdState } from "../../../types/content"

const PRD_B: PrdState = {
  prd_id: 2,
  title: "PRD B",
  metaLine: "",
  sections: [],
  source: "brief",
  briefId: 9,
  insightIndex: 2,
}

const EVIDENCE_DOC = { title: "Evidence A", metaLine: "", sections: [] }

function renderPanel(opts: {
  tab: "prd" | "evidence" | "tickets"
  prd?: PrdState | null
  prdMeta?: { briefId: number; insightIndex: number } | null
  detail?: unknown
  evidence?: unknown
  guest?: boolean
}) {
  navMock.tab = opts.tab
  contentMock.value = {
    prd: opts.prd ?? null,
    prdMeta: opts.prdMeta ?? null,
    detail: opts.detail ?? null,
    evidence: opts.evidence ?? null,
    evidenceGenerating: false,
    connectedConnectorIds: [],
    threadReports: [],
    threadReportsStatus: "idle",
    reportFocusId: null,
  }
  const tree = <ContentPanel />
  return render(
    opts.guest
      ? (
        <GuestSessionProvider
          value={{
            token: "tok-1",
            publicId: null,
            sharerName: "Priya Shah",
            owningCompanyName: "Acme Co",
            artifactId: 482,
          }}
        >
          {tree}
        </GuestSessionProvider>
      )
      : tree,
  )
}

beforeEach(() => {
  loadEvidenceByInsightMock.mockResolvedValue(null)
  storiesApiMock.generate.mockResolvedValue({ job_id: 1 })
  storiesApiMock.getJob.mockResolvedValue({ status: "ready", stories: [] })
  storiesApiMock.getForPrd.mockResolvedValue({ status: "ready", fresh: true, stories: [] })
  mintMock.mockResolvedValue({ token: "tok-1" })
  vi.stubGlobal("navigator", {
    ...navigator,
    clipboard: { writeText: vi.fn().mockResolvedValue(undefined) },
  })
})

afterEach(() => {
  cleanup()
  vi.clearAllMocks()
  vi.unstubAllGlobals()
})

// The exact live-bug state: PRD B is cached, evidence tab shows a document,
// but nothing in context attributes that document to B (prdMeta/detail null).
const UNATTRIBUTABLE_EVIDENCE_STATE = {
  tab: "evidence" as const,
  prd: PRD_B,
  prdMeta: null,
  detail: null,
  evidence: EVIDENCE_DOC,
}

describe("ContentPanel — Share/header scope on unattributable evidence", () => {
  it("test_share_control_disabled_when_displayed_evidence_is_unattributable — AC7", () => {
    renderPanel(UNATTRIBUTABLE_EVIDENCE_STATE)
    const shareBtn = screen.getByRole("button", { name: /share/i })
    expect(shareBtn).toHaveProperty("disabled", true)
  })

  it("test_share_menu_does_not_open_for_a_foreign_displayed_artifact — AC8", () => {
    renderPanel(UNATTRIBUTABLE_EVIDENCE_STATE)
    const shareBtn = screen.getByRole("button", { name: /share/i })
    fireEvent.click(shareBtn)
    expect(screen.queryByRole("menu")).toBeNull()
    expect(screen.queryByText("Copy share link")).toBeNull()
  })

  it("test_panel_header_does_not_name_a_prd_it_is_not_showing — AC9", () => {
    renderPanel(UNATTRIBUTABLE_EVIDENCE_STATE)
    const header = document.querySelector(".cpanel-main-name")
    expect(header?.textContent).toBe("PRD")
    expect(header?.textContent).not.toContain("PRD B")
  })

  it("test_opening_evidence_after_viewing_another_prd_never_mints — AC10", async () => {
    // The exact live sequence, end to end: PRD B is cached from the reader
    // having viewed it on the prd tab, then they open PRD A's evidence document
    // from the Artifacts library — ArtifactsScreen's REAL (currently-under-test)
    // openArtifact runs, and ContentPanel is rendered against whatever content
    // that leaves behind. This is the one regression that a hand-built
    // "already patched" content object cannot prove RED at HEAD: passing
    // `prd: null` directly into ContentPanel is trivially safe with or without
    // ContentPanel's own fix, because ShareMenu's `enabled = !!prd` already
    // short-circuits on a null prd — only exercising the ACTUAL write path
    // (ArtifactsScreen.openArtifact, unfixed at HEAD, leaves prd/prdMeta on B)
    // can fail red on unfixed code.
    contentMock.value = {
      prd: PRD_B,
      prdMeta: { briefId: 9, insightIndex: 2 },
      detail: null,
      evidence: null,
      evidenceGenerating: false,
      connectedConnectorIds: [],
      threadReports: [],
      threadReportsStatus: "idle",
      reportFocusId: null,
    }
    artifactsListMock.mockResolvedValue([
      {
        type: "evidence",
        id: 5,
        title: "Evidence A",
        status: "ready",
        created_at: "2026-08-01T00:00:00Z",
        source: { brief_id: 4, week_label: null, insight_index: 0 },
        open: { brief_id: 4, insight_index: 0, evidence_id: 501 },
      },
    ])
    evidenceGetMock.mockResolvedValue({ id: 501, payload_md: "# doc", question: null })

    const { unmount } = render(<ArtifactsScreen />)
    await waitFor(() => expect(screen.queryByText("Evidence A")).not.toBeNull())
    const row = document.querySelector('[data-artifact-type="evidence"]') as HTMLElement
    await act(async () => { fireEvent.click(row) })
    await waitFor(() => expect(evidenceGetMock).toHaveBeenCalled())
    unmount()

    // Render the shared panel on the evidence tab against exactly the content
    // ArtifactsScreen's click left in the shared store.
    navMock.tab = "evidence"
    render(<ContentPanel />)
    const shareBtn = screen.getByRole("button", { name: /share/i })
    fireEvent.click(shareBtn)
    // If the menu opened (Share was wrongly enabled), also click "Copy share
    // link" — the actual mint trigger — so an unfixed HEAD is caught, not just
    // masked by a disabled-button short-circuit.
    const copyLink = screen.queryByText("Copy share link")
    if (copyLink) fireEvent.click(copyLink)
    expect(mintMock).not.toHaveBeenCalled()
    expect((navigator.clipboard.writeText as ReturnType<typeof vi.fn>)).not.toHaveBeenCalled()
  })
})

describe("ContentPanel — Tickets tab prototype CTA after the scope patch", () => {
  it("test_tickets_footer_prototype_cta_disabled_after_scope_patch — AC13", () => {
    renderPanel({
      tab: "tickets",
      prd: null, // evidenceOpenScopePatch() cleared it
      prdMeta: null,
      detail: null,
      evidence: null,
    })
    const cta = screen.getByTestId("tickets-footer-prototype-cta")
    expect(cta).toHaveProperty("disabled", true)
  })
})

describe("ContentPanel — resume-a-PRD flow is not regressed", () => {
  it("test_prd_in_scope_returns_the_prd_when_no_evidence_document_is_displayed keeps Share enabled", () => {
    renderPanel({ tab: "evidence", prd: PRD_B, prdMeta: null, detail: null, evidence: null })
    const shareBtn = screen.getByRole("button", { name: /share/i })
    expect(shareBtn).toHaveProperty("disabled", false)
  })
})

describe("ContentPanel — guest mode is unaffected by the scope helper", () => {
  it("test_guest_share_button_stays_disabled_with_its_own_reason — AC15", () => {
    renderPanel({ tab: "prd", prd: PRD_B, prdMeta: { briefId: 9, insightIndex: 2 }, guest: true })
    const shareBtn = screen.getByRole("button", { name: /share/i })
    expect(shareBtn).toHaveProperty("disabled", true)
    expect(shareBtn.getAttribute("title") || shareBtn.getAttribute("aria-label")).toMatch(
      /sign in to a full workspace to share/i,
    )
  })
})

describe("ContentPanel — public surface unchanged", () => {
  it("test_content_panel_public_surface_unchanged — AC16", () => {
    expect(typeof ContentPanel).toBe("function")
    expect(typeof isEvidenceTabHidden).toBe("function")
    const base = { evidence: null, evidenceGenerating: false }
    expect(isEvidenceTabHidden({ ...base, prd: null })).toBe(false)
    expect(isEvidenceTabHidden({ ...base, prd: { ...PRD_B, source: "brief" } })).toBe(false)
    expect(isEvidenceTabHidden({ ...base, prd: { ...PRD_B, source: "ideation" } })).toBe(true)
    expect(
      isEvidenceTabHidden({
        prd: { ...PRD_B, source: "upload" },
        evidence: { title: "E", metaLine: "", sections: [] },
        evidenceGenerating: false,
      }),
    ).toBe(false)
  })
})

describe("ContentPanel — guard: Share/TicketsBottomBar never read the raw global slot", () => {
  it("test_share_control_never_reads_the_global_prd_slot_directly — AC14", () => {
    const source = readFileSync(
      join(__dirname, "../ContentPanel.tsx"),
      "utf8",
    )
    // The <ShareMenu prd={…}> prop must not be the raw slot.
    const shareMenuMatch = source.match(/<ShareMenu[\s\S]*?\/>/)
    expect(shareMenuMatch).not.toBeNull()
    expect(shareMenuMatch![0]).not.toContain("prd={content.prd}")
    // TicketsBottomBar's prdId must not be assigned from the raw slot.
    expect(source).not.toMatch(/const prdId = content\.prd\?\.prd_id/)
    // The scope helper must be doing the routing — at least once for the panel
    // body, once for TicketsBottomBar, plus its own import line.
    const occurrences = source.split("prdInScopeFor").length - 1
    expect(occurrences).toBeGreaterThanOrEqual(3)
  })
})
