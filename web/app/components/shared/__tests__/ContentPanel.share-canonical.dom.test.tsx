// @vitest-environment jsdom
//
// The PRD Share dropdown reads a pre-existing canonical token and renders
// the link inline — it never mints on open. Mirrors the vi.hoisted
// context-mock harness from ContentPanel.guest-mode.dom.test.tsx.
import * as React from "react"
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react"
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

vi.mock("../../../lib/runEvidenceGeneration", () => ({
  runEvidenceGeneration: vi.fn(),
  loadEvidenceByInsight: vi.fn(),
}))

const mintMock = vi.fn()
vi.mock("../../../lib/artifactShareApi", () => ({
  artifactShareApi: { mint: (...a: unknown[]) => mintMock(...a) },
}))

const downloadPrdPdfMock = vi.fn(async (..._a: unknown[]) => {})
vi.mock("../../../lib/prdExport", async () => {
  const actual = await vi.importActual<typeof import("../../../lib/prdExport")>(
    "../../../lib/prdExport",
  )
  return { ...actual, downloadPrdPdf: (...a: unknown[]) => downloadPrdPdfMock(...a) }
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
const setContentMock = vi.fn()
vi.mock("../../../context/ContentContext", () => ({
  useContent: () => ({ content: contentMock.value, setContent: setContentMock }),
}))

import { ContentPanel } from "../ContentPanel"
import { GuestSessionProvider } from "../../../context/GuestSessionContext"
import type { PrdState } from "../../../types/content"

const BASE_PRD: PrdState = {
  prd_id: 42,
  public_id: "pub-42",
  title: "Handoff Threshold PRD",
  metaLine: "",
  sections: [],
}

const GUEST_SESSION = {
  token: "tok-guest",
  publicId: null,
  sharerName: "Priya Shah",
  owningCompanyName: "Acme Co",
  artifactId: 482,
}

function renderPanel(prd: PrdState | null, opts: { guest?: boolean } = {}) {
  navMock.tab = "prd"
  contentMock.value = {
    prd,
    prdMeta: null,
    evidence: null,
    evidenceGenerating: false,
    detail: null,
    guestTickets: [],
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

const writeTextMock = vi.fn(async () => {})

beforeEach(() => {
  vi.stubGlobal("navigator", {
    ...navigator,
    clipboard: { writeText: writeTextMock },
  })
})

afterEach(() => {
  cleanup()
  vi.clearAllMocks()
  vi.unstubAllGlobals()
})

describe("ContentPanel — PRD Share, canonical token", () => {
  it("test_share_menu_renders_inline_url_from_canonical_token — AC12", () => {
    renderPanel({ ...BASE_PRD, shareToken: "canon-tok-1" })
    fireEvent.click(screen.getByRole("button", { name: /share/i }))
    const expected = `${window.location.origin}/?prd=pub-42&share=canon-tok-1`
    expect(screen.getByText(expected).tagName.toLowerCase()).toBe("code")
  })

  it("test_share_url_uses_public_id_not_prd_id — AC12", () => {
    renderPanel({ ...BASE_PRD, public_id: "pub-42", shareToken: "canon-tok-2" })
    fireEvent.click(screen.getByRole("button", { name: /share/i }))
    expect(screen.getByText(`${window.location.origin}/?prd=pub-42&share=canon-tok-2`)).not.toBeNull()
  })

  it("falls back to prd_id when public_id is absent — AC12", () => {
    renderPanel({ ...BASE_PRD, public_id: undefined, shareToken: "canon-tok-3" })
    fireEvent.click(screen.getByRole("button", { name: /share/i }))
    expect(screen.getByText(`${window.location.origin}/?prd=42&share=canon-tok-3`)).not.toBeNull()
  })

  it("test_copy_control_writes_url_and_flips_to_copied — AC13", async () => {
    renderPanel({ ...BASE_PRD, shareToken: "canon-tok-4" })
    fireEvent.click(screen.getByRole("button", { name: /share/i }))
    const expected = `${window.location.origin}/?prd=pub-42&share=canon-tok-4`
    fireEvent.click(screen.getByRole("button", { name: "Copy" }))
    await waitFor(() => expect(writeTextMock).toHaveBeenCalledWith(expected))
    expect(screen.getByRole("button", { name: "Copied!" })).not.toBeNull()
  })

  it("test_opening_share_menu_never_mints — AC14 (RED if mint-on-click is reintroduced)", async () => {
    renderPanel({ ...BASE_PRD, shareToken: "canon-tok-5" })
    fireEvent.click(screen.getByRole("button", { name: /share/i }))
    fireEvent.click(screen.getByRole("button", { name: "Copy" }))
    await waitFor(() => expect(writeTextMock).toHaveBeenCalled())
    expect(mintMock).not.toHaveBeenCalled()
  })

  it("test_share_menu_disabled_without_token_never_mints — AC15", async () => {
    renderPanel({ ...BASE_PRD, shareToken: null })
    fireEvent.click(screen.getByRole("button", { name: /share/i }))
    expect(screen.queryByText(/\/\?prd=/)).toBeNull()
    const preparing = screen.getByRole("button", { name: /preparing link/i })
    expect(preparing).toHaveProperty("disabled", true)
    fireEvent.click(preparing)
    expect(writeTextMock).not.toHaveBeenCalled()
    expect(mintMock).not.toHaveBeenCalled()
  })

  it("test_download_pdf_item_unchanged — AC16", async () => {
    renderPanel({ ...BASE_PRD, shareToken: "canon-tok-6" })
    fireEvent.click(screen.getByRole("button", { name: /share/i }))
    expect(screen.getByText("Download PDF")).not.toBeNull()
    fireEvent.click(screen.getByText("Download PDF"))
    await waitFor(() => expect(downloadPrdPdfMock).toHaveBeenCalled())
    expect(downloadPrdPdfMock.mock.calls[0][0]).toMatchObject({ prd_id: 42 })
  })

  it("test_guest_share_button_stays_disabled_no_url — AC17", () => {
    renderPanel({ ...BASE_PRD, shareToken: "canon-tok-7" }, { guest: true })
    const shareBtn = screen.getByRole("button", { name: /share/i })
    expect(shareBtn).toHaveProperty("disabled", true)
    fireEvent.click(shareBtn)
    expect(screen.queryByRole("menu")).toBeNull()
    expect(screen.queryByText(/\/\?prd=/)).toBeNull()
    expect(screen.queryByRole("button", { name: "Copy" })).toBeNull()
  })
})
