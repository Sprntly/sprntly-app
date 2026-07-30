// @vitest-environment jsdom
//
// Tests for the report viewer's Share menu: PDF download (the only format) and
// the opt-in link. The api module is stubbed so this exercises the menu's wiring.
import * as React from "react"
import { act, cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react"
import { afterEach, describe, expect, it, vi } from "vitest"

vi.hoisted(() => {
  ;(globalThis as Record<string, unknown>).React = require("react")
})

const downloadPdf = vi.fn((..._a: unknown[]) => Promise.resolve<unknown>(null))
const share = vi.fn((..._a: unknown[]) => Promise.resolve<unknown>(null))
const saveAs = vi.fn()

vi.mock("../../../lib/api", () => ({
  reportsApi: {
    downloadPdf: (...a: unknown[]) => downloadPdf(...a),
    share: (...a: unknown[]) => share(...a),
  },
}))
vi.mock("file-saver", () => ({ saveAs: (...a: unknown[]) => saveAs(...a) }))

import { ReportPanel } from "../ReportPanel"
// Type-only import: erased at runtime, so it does not defeat the module mock.
import type { ReportDoc } from "../../../lib/api"

const DOC: ReportDoc = {
  id: 4,
  skill: "voice-of-customer-report",
  title: "Voice of Customer · Q2",
  question: "",
  html: "<!DOCTYPE html><html><body>x</body></html>",
  created_at: new Date().toISOString(),
  conversation_id: null,
  prd_id: null,
  share_mode: "private",
  share_token: null,
}

const onToast = vi.fn()
const onShareChange = vi.fn()

afterEach(() => {
  cleanup()
  vi.clearAllMocks()
})

function openMenu(report = DOC) {
  render(
    <ReportPanel
      report={report}
      loading={false}
      onClose={() => {}}
      onToast={onToast}
      onShareChange={onShareChange}
    />,
  )
  fireEvent.click(screen.getByTestId("report-share-button"))
}

describe("ReportPanel — share menu", () => {
  it("offers PDF as the only download format", () => {
    openMenu()
    expect(screen.getByTestId("report-download-pdf")).toBeTruthy()
    // No DOCX/markdown item: flattening a layout-led report loses the document.
    expect(screen.queryByText(/docx/i)).toBeNull()
    expect(screen.queryByText(/markdown/i)).toBeNull()
  })

  it("downloads the PDF with the server-supplied filename", async () => {
    const blob = new Blob(["%PDF"], { type: "application/pdf" })
    downloadPdf.mockResolvedValue({ blob, filename: "voice-of-customer-q2.pdf" })

    openMenu()
    await act(async () => { fireEvent.click(screen.getByTestId("report-download-pdf")) })

    expect(downloadPdf).toHaveBeenCalledWith(4)
    await waitFor(() => expect(saveAs).toHaveBeenCalledWith(blob, "voice-of-customer-q2.pdf"))
  })

  it("says so when the renderer is unavailable instead of saving a broken file", async () => {
    downloadPdf.mockRejectedValue(new Error("503"))

    openMenu()
    await act(async () => { fireEvent.click(screen.getByTestId("report-download-pdf")) })

    await waitFor(() => expect(onToast).toHaveBeenCalled())
    expect(onToast.mock.calls[0][0]).toContain("PDF export failed")
    expect(saveAs).not.toHaveBeenCalled()
  })

  it("shows no link until sharing is turned on", () => {
    openMenu()
    expect(screen.getByTestId("report-toggle-share").textContent).toContain(
      "Create a share link",
    )
    // The menu can never display a URL that doesn't resolve.
    expect(screen.queryByTestId("report-copy-link")).toBeNull()
  })

  it("creates a public link and reports the new state upward", async () => {
    share.mockResolvedValue({ share_mode: "public", share_token: "tok-123" })

    openMenu()
    await act(async () => { fireEvent.click(screen.getByTestId("report-toggle-share")) })

    expect(share).toHaveBeenCalledWith(4, { share_mode: "public" })
    await waitFor(() =>
      expect(onShareChange).toHaveBeenCalledWith({
        share_mode: "public", share_token: "tok-123",
      }),
    )
  })

  it("offers the /r/<token> link once shared", () => {
    openMenu({ ...DOC, share_mode: "public", share_token: "tok-123" })
    const copy = screen.getByTestId("report-copy-link")
    expect(copy.textContent).toContain(`${window.location.origin}/r/tok-123`)
    // And the button reflects that the report is out there.
    expect(screen.getByTestId("report-share-button").textContent).toContain("Shared")
  })

  it("turns sharing back off", async () => {
    share.mockResolvedValue({ share_mode: "private", share_token: null })

    openMenu({ ...DOC, share_mode: "public", share_token: "tok-123" })
    expect(screen.getByTestId("report-toggle-share").textContent).toContain(
      "Turn off link sharing",
    )
    await act(async () => { fireEvent.click(screen.getByTestId("report-toggle-share")) })

    expect(share).toHaveBeenCalledWith(4, { share_mode: "private" })
  })
})
