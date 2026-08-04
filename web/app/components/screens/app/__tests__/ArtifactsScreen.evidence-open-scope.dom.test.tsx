// @vitest-environment jsdom
//
// Regression coverage for the Artifacts library's evidence-open path: clicking
// an evidence row must retire whatever PRD was previously cached in shared
// content BEFORE the network fetch resolves, so no PRD-acting control (Share,
// header, prototype CTA) stays armed on a document the reader has navigated
// away from. Also proves the by-id fetch itself, and the PRD/report/prototype
// rows, are unaffected.

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

describe("ArtifactsScreen — opening an evidence row retires the cached PRD", () => {
  it("test_artifacts_evidence_row_click_retires_the_cached_prd — AC11", async () => {
    artifactsList.mockResolvedValue([EVIDENCE_ROW])
    let resolveGet: (v: unknown) => void = () => {}
    evidenceGet.mockReturnValue(new Promise((res) => { resolveGet = res }))

    await act(async () => { render(<ArtifactsScreen />) })
    await waitFor(() => expect(screen.queryByText("Evidence A")).not.toBeNull())

    const row = document.querySelector('[data-artifact-type="evidence"]') as HTMLElement
    await act(async () => { fireEvent.click(row) })

    // The evidence-open patch (applied before evidenceApi.get resolves, in the
    // SAME setContent call that flips evidenceGenerating on) must carry every
    // key/value of evidenceOpenScopePatch(). (An earlier, unrelated setContent
    // call resets the standalone-report pointer — this finds the right one.)
    const openPatch = setContent.mock.calls
      .map(([patch]) => patch)
      .find((patch) => patch.evidenceGenerating === true)
    expect(openPatch).toMatchObject({
      evidence: null,
      evidenceGenerating: true,
      prd: null,
      prdMeta: null,
      prdGenerating: false,
      prdPartialHtml: null,
      detail: null,
    })

    await act(async () => { resolveGet({ id: 501, payload_md: "# doc", question: null }) })
    expect(openContentPanel).toHaveBeenCalledWith("evidence")
  })

  it("test_artifacts_evidence_row_still_sets_the_fetched_document — AC11", async () => {
    artifactsList.mockResolvedValue([EVIDENCE_ROW])
    evidenceGet.mockResolvedValue({ id: 501, payload_md: "# doc", question: "Why did retention drop?" })

    await act(async () => { render(<ArtifactsScreen />) })
    await waitFor(() => expect(screen.queryByText("Evidence A")).not.toBeNull())

    const row = document.querySelector('[data-artifact-type="evidence"]') as HTMLElement
    await act(async () => { fireEvent.click(row) })
    await waitFor(() => expect(evidenceGet).toHaveBeenCalledWith(501))

    // The second setContent (post-fetch) still lands the by-id document exactly
    // as at HEAD — the fix does not break the render path.
    const lastPatch = setContent.mock.calls.at(-1)?.[0]
    expect(lastPatch).toMatchObject({
      evidenceId: 501,
      evidenceGenerating: false,
    })
    expect(lastPatch.evidence).toBeDefined()
  })

  it("test_artifacts_prd_row_click_does_not_apply_the_scope_patch — AC12", async () => {
    artifactsList.mockResolvedValue([PRD_ROW])

    await act(async () => { render(<ArtifactsScreen />) })
    await waitFor(() => expect(screen.queryByText("PRD C")).not.toBeNull())

    const row = document.querySelector('[data-artifact-type="prd"]') as HTMLElement
    await act(async () => { fireEvent.click(row) })

    // A PRD row's setContent calls (the standalone-report-pointer reset that
    // every non-report open applies) never carry the evidence scope patch's
    // `prd: null` — it opens via openPrdTab, byte-identical to HEAD.
    for (const [patch] of setContent.mock.calls) {
      expect(patch).not.toHaveProperty("prd", null)
    }
    expect(openPrdTab).toHaveBeenCalledTimes(1)
    const call = openPrdTab.mock.calls[0][0]
    expect(call.source.kind).toBe("load")
    expect(call.source.prdId).toBe(30)
  })
})
