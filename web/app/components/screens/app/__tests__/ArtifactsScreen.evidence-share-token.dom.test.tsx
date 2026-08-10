// @vitest-environment jsdom
//
// The Artifacts library's evidence-open path threads the evidence GET's
// canonical `share_token` onto `content.evidenceShareToken` in the SAME
// post-fetch setContent call that already sets `evidence`/`evidenceId` —
// mirroring how `evidenceId` itself is threaded. EvidenceShareControl reads
// that slot. A PRD row's open path never touches this slot.

import * as React from "react"
import { act, cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react"
import { afterEach, describe, expect, it, vi } from "vitest"

vi.hoisted(() => {
  // eslint-disable-next-line @typescript-eslint/no-require-imports
  ;(globalThis as Record<string, unknown>).React = require("react")
})

const artifactsList = vi.fn((..._a: unknown[]) => Promise.resolve<unknown[]>([]))
const evidenceGet = vi.fn((..._a: unknown[]) => Promise.resolve<unknown>(null))

vi.mock("../../../../lib/api", () => ({
  artifactsApi: { list: (...a: unknown[]) => artifactsList(...a) },
  askApi: { start: vi.fn(), get: vi.fn() },
  prdApi: { importDoc: vi.fn(), get: vi.fn() },
  evidenceApi: { get: (...a: unknown[]) => evidenceGet(...a) },
  reportsApi: { get: vi.fn(), share: vi.fn(), downloadPdf: vi.fn(), kinds: vi.fn() },
}))

const setContent = vi.fn()
const openContentPanel = vi.fn()
const openPrdTab = vi.fn()
const openReportTab = vi.fn()
const showToast = vi.fn()

vi.mock("../../../../context/NavigationContext", () => ({
  useNavigation: () => ({
    openContentPanel, openPrdTab, openReportTab, showToast, contentPanelTab: null,
  }),
}))
vi.mock("../../../../context/ContentContext", () => ({
  useContent: () => ({ setContent }),
}))
vi.mock("../../../../context/CompanyContext", () => ({
  useCompany: () => ({ activeCompany: "acme" }),
}))
vi.mock("next/navigation", () => ({ useRouter: () => ({ push: vi.fn() }) }))
vi.mock("../../../../lib/evidence-adapter", () => ({
  markdownToEvidenceState: (md: string) => ({ title: "Evidence", metaLine: "", sections: [], md }),
}))
vi.mock("../../../../lib/routes", () => ({ prototypePath: () => "/prototype" }))
vi.mock("../AppLayout", () => ({
  AppLayout: ({ children }: { children: React.ReactNode }) =>
    React.createElement("div", { "data-testid": "app-layout" }, children),
}))

import { ArtifactsScreen } from "../ArtifactsScreen"

const EVIDENCE_ROW = {
  type: "evidence" as const,
  id: 5,
  title: "Evidence A",
  status: "ready",
  created_at: "2026-08-01T00:00:00Z",
  source: { brief_id: 9, week_label: null, insight_index: 2 },
  open: { brief_id: 9, insight_index: 2, evidence_id: 501 },
}

const PRD_ROW = {
  type: "prd" as const,
  id: 3,
  title: "PRD C",
  status: "ready",
  created_at: "2026-08-01T00:00:00Z",
  source: { brief_id: 4, week_label: null, insight_index: 0 },
  open: { brief_id: 4, insight_index: 0, prd_id: 30 },
}

afterEach(() => {
  cleanup()
  vi.clearAllMocks()
})

describe("ArtifactsScreen — evidence-open path threads the canonical share token", () => {
  it("test_artifacts_evidence_open_threads_share_token — AC16", async () => {
    artifactsList.mockResolvedValue([EVIDENCE_ROW])
    evidenceGet.mockResolvedValue({
      id: 501, payload_md: "# doc", question: null, share_token: "canon-ev-tok-thread",
    })

    await act(async () => { render(<ArtifactsScreen />) })
    await waitFor(() => expect(screen.queryByText("Evidence A")).not.toBeNull())

    const row = document.querySelector('[data-artifact-type="evidence"]') as HTMLElement
    await act(async () => { fireEvent.click(row) })
    await waitFor(() => expect(evidenceGet).toHaveBeenCalledWith(501))

    // The token lands in the SAME post-fetch setContent that sets
    // evidence/evidenceId (mirroring how evidenceId itself is threaded).
    const lastPatch = setContent.mock.calls.at(-1)?.[0]
    expect(lastPatch).toMatchObject({
      evidenceId: 501,
      evidenceShareToken: "canon-ev-tok-thread",
      evidenceGenerating: false,
    })
  })

  it("a prd row does not set evidenceShareToken", async () => {
    artifactsList.mockResolvedValue([PRD_ROW])

    await act(async () => { render(<ArtifactsScreen />) })
    await waitFor(() => expect(screen.queryByText("PRD C")).not.toBeNull())

    const row = document.querySelector('[data-artifact-type="prd"]') as HTMLElement
    await act(async () => { fireEvent.click(row) })

    for (const [patch] of setContent.mock.calls) {
      expect(patch).not.toHaveProperty("evidenceShareToken")
    }
    expect(openPrdTab).toHaveBeenCalledTimes(1)
  })
})
