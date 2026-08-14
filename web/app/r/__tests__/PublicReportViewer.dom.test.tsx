// @vitest-environment jsdom
//
// Tests for the `/r/<token>` shared-report viewer — what someone outside the
// workspace sees. The public api module is stubbed; the token comes from the
// live URL (static export, see reportTokenFromPathname.ts).
import * as React from "react"
import { act, cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react"
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest"

vi.hoisted(() => {
  ;(globalThis as Record<string, unknown>).React = require("react")
})

const publicGet = vi.fn((..._a: unknown[]) => Promise.resolve<unknown>(null))
const publicUnlock = vi.fn((..._a: unknown[]) => Promise.resolve<unknown>(null))
const publicPdf = vi.fn((..._a: unknown[]) => Promise.resolve<unknown>(null))
const saveBlob = vi.fn()

// The ApiError class is defined INSIDE the factory: the factory body runs at
// import time (before the module's top-level consts initialise), so referencing
// an outer class here would hit the temporal dead zone. The function properties
// below are safe because they only close over the mocks, evaluated on call.
vi.mock("../../lib/api", () => {
  class ApiError extends Error {
    constructor(public status: number, message = "") {
      super(message)
    }
  }
  return {
    ApiError,
    publicReportsApi: {
      get: (...a: unknown[]) => publicGet(...a),
      unlock: (...a: unknown[]) => publicUnlock(...a),
      downloadPdf: (...a: unknown[]) => publicPdf(...a),
    },
  }
})
vi.mock("../../lib/saveBlob", () => ({ saveBlob: (...a: unknown[]) => saveBlob(...a) }))

import { ApiError as FakeApiError } from "../../lib/api"
import { PublicReportViewer } from "../PublicReportViewer"

const REPORT = {
  title: "Voice of Customer · Q2",
  kind: "Voice of customer report",
  skill: "voice-of-customer-report",
  html: "<!DOCTYPE html><html><body><h1>VoC</h1></body></html>",
  created_at: "2026-07-30T10:00:00+00:00",
}

const SAVED_CHAT_REPORT = {
  title: "Saved from chat",
  kind: "Saved chat",
  skill: "saved-chat",
  html: "# Prioritization\n\n**Ship A** first",
  created_at: "2026-07-30T10:00:00+00:00",
}

function setPath(path: string) {
  window.history.replaceState({}, "", path)
}

beforeEach(() => setPath("/r/tok-123"))
afterEach(() => {
  cleanup()
  vi.clearAllMocks()
})

async function mount() {
  await act(async () => { render(<PublicReportViewer />) })
}

describe("PublicReportViewer", () => {
  it("resolves the token from the live URL and renders the document", async () => {
    publicGet.mockResolvedValue(REPORT)

    await mount()

    // Not the prerender sentinel — the real token from the address bar.
    expect(publicGet).toHaveBeenCalledWith("tok-123")
    await waitFor(() => expect(screen.getByTestId("public-report")).toBeTruthy())
    const frame = document.querySelector("iframe") as HTMLIFrameElement
    expect(frame.getAttribute("srcdoc")).toContain("<h1>VoC</h1>")
    expect(screen.getByText("Voice of Customer · Q2")).toBeTruthy()
  })

  it("renders a skill='saved-chat' report as markdown, not the HTML iframe", async () => {
    publicGet.mockResolvedValue(SAVED_CHAT_REPORT)

    await mount()

    await waitFor(() => expect(screen.getByTestId("saved-chat-markdown")).toBeTruthy())
    const body = screen.getByTestId("saved-chat-markdown")
    expect(body.querySelector("h1")?.textContent).toBe("Prioritization")
    expect(body.querySelector("strong")?.textContent).toBe("Ship A")
    expect(document.querySelector("iframe")).toBeNull()
  })

  it("still renders a non-'saved-chat' shared report in the sandboxed iframe, unchanged", async () => {
    publicGet.mockResolvedValue(REPORT)

    await mount()

    await waitFor(() => expect(screen.getByTestId("public-report")).toBeTruthy())
    const frame = document.querySelector("iframe") as HTMLIFrameElement
    expect(frame.getAttribute("srcdoc")).toContain("<h1>VoC</h1>")
    expect(screen.queryByTestId("saved-chat-markdown")).toBeNull()
  })

  it("never shows internal provenance to an outsider", async () => {
    publicGet.mockResolvedValue(REPORT)

    await mount()
    await waitFor(() => expect(screen.getByTestId("public-report")).toBeTruthy())

    // The attachment (chat room / PRD) names internal work — it must not appear
    // on a page handed to someone outside the workspace.
    const text = document.body.textContent ?? ""
    expect(text).not.toMatch(/from .*themes/i)
    expect(text).not.toMatch(/on PRD/i)
  })

  it("shows a dead-link message for an unknown or revoked token", async () => {
    publicGet.mockRejectedValue(new FakeApiError(404, null))

    await mount()

    await waitFor(() => expect(screen.getByTestId("public-report-gone")).toBeTruthy())
    expect(document.body.textContent).toContain("isn't available")
  })

  it("treats the prerender sentinel as no token at all", async () => {
    setPath("/r/_")

    await mount()

    // Never send the build-time placeholder to the API as a real token.
    expect(publicGet).not.toHaveBeenCalled()
    expect(screen.getByTestId("public-report-gone")).toBeTruthy()
  })

  it("prompts for a passcode on a gated link, then renders on success", async () => {
    publicGet.mockRejectedValue(new FakeApiError(401, "passcode_required"))
    publicUnlock.mockResolvedValue(REPORT)

    await mount()
    await waitFor(() => expect(screen.getByTestId("public-report-passcode")).toBeTruthy())
    // The document is not in the DOM before unlocking.
    expect(document.querySelector("iframe")).toBeNull()

    fireEvent.change(screen.getByLabelText("Passcode"), { target: { value: "letmein" } })
    await act(async () => {
      fireEvent.submit(screen.getByTestId("public-report-passcode"))
    })

    expect(publicUnlock).toHaveBeenCalledWith("tok-123", "letmein")
    await waitFor(() => expect(screen.getByTestId("public-report")).toBeTruthy())
  })

  it("reports a wrong passcode and a throttle differently", async () => {
    publicGet.mockRejectedValue(new FakeApiError(401, "passcode_required"))
    publicUnlock.mockRejectedValue(new FakeApiError(401, null))

    await mount()
    await waitFor(() => expect(screen.getByTestId("public-report-passcode")).toBeTruthy())
    fireEvent.change(screen.getByLabelText("Passcode"), { target: { value: "nope" } })
    await act(async () => { fireEvent.submit(screen.getByTestId("public-report-passcode")) })
    expect(screen.getByTestId("public-report-passcode-error").textContent).toContain(
      "didn't work",
    )

    publicUnlock.mockRejectedValue(new FakeApiError(429, null))
    await act(async () => { fireEvent.submit(screen.getByTestId("public-report-passcode")) })
    expect(screen.getByTestId("public-report-passcode-error").textContent).toContain(
      "Too many attempts",
    )
  })

  it("downloads the PDF, carrying the passcode for a gated link", async () => {
    publicGet.mockRejectedValue(new FakeApiError(401, "passcode_required"))
    publicUnlock.mockResolvedValue(REPORT)
    const blob = new Blob(["%PDF"], { type: "application/pdf" })
    publicPdf.mockResolvedValue({ blob, filename: "voc-q2.pdf" })

    await mount()
    await waitFor(() => expect(screen.getByTestId("public-report-passcode")).toBeTruthy())
    fireEvent.change(screen.getByLabelText("Passcode"), { target: { value: "letmein" } })
    await act(async () => { fireEvent.submit(screen.getByTestId("public-report-passcode")) })
    await waitFor(() => expect(screen.getByTestId("public-report")).toBeTruthy())

    await act(async () => {
      fireEvent.click(screen.getByTestId("public-report-download"))
    })

    expect(publicPdf).toHaveBeenCalledWith("tok-123", "letmein")
    await waitFor(() => expect(saveBlob).toHaveBeenCalledWith(blob, "voc-q2.pdf"))
  })
})
