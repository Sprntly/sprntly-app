// @vitest-environment jsdom
//
// Round-trip tests for the Templates screen ("what good looks like"): it lists
// the company's gold-standard templates, uploads a new one (calling
// templatesApi.upload), and removes one (calling templatesApi.remove) — then
// re-fetches via templatesApi.list each time.
import * as React from "react"
import { act, cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react"
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest"

;(globalThis as typeof globalThis & { React?: typeof React }).React = React

const listMock = vi.fn()
const uploadMock = vi.fn()
const removeMock = vi.fn()

vi.mock("../../../../lib/api", () => ({
  templatesApi: {
    list: (...a: unknown[]) => listMock(...a),
    upload: (...a: unknown[]) => uploadMock(...a),
    remove: (...a: unknown[]) => removeMock(...a),
  },
}))

// AppLayout drags in app contexts; the screen logic under test doesn't need it.
vi.mock("../AppLayout", () => ({
  AppLayout: ({ children }: { children: React.ReactNode }) =>
    React.createElement("div", null, children),
}))

import { TemplatesScreen } from "../TemplatesScreen"
import type { CompanyTemplate } from "../../../../lib/api"

const T1: CompanyTemplate = {
  id: "t1",
  label: "Guest Deal Alerts — PRD",
  type: "prd",
  filename: "guest.md",
  content_type: "text/markdown",
  extracted_chars: 4200,
  uploaded_at: "2026-06-01T00:00:00Z",
}

function fileInput(): HTMLInputElement {
  const el = document.querySelector(
    '[data-testid="template-file-input"]',
  ) as HTMLInputElement | null
  if (!el) throw new Error("file input not found")
  return el
}

beforeEach(() => {
  listMock.mockResolvedValue([T1])
  uploadMock.mockResolvedValue({ ok: true, ...T1, id: "t2", filename: "new.md" })
  removeMock.mockResolvedValue({ ok: true, id: "t1" })
})

afterEach(() => {
  cleanup()
  vi.clearAllMocks()
})

describe("TemplatesScreen", () => {
  it("lists templates fetched from templatesApi.list on mount", async () => {
    await act(async () => {
      render(React.createElement(TemplatesScreen))
    })
    await waitFor(() => expect(listMock).toHaveBeenCalled())
    expect(screen.getByText("Guest Deal Alerts — PRD")).toBeTruthy()
  })

  it("uploads a picked file via templatesApi.upload, then refetches", async () => {
    await act(async () => {
      render(React.createElement(TemplatesScreen))
    })
    await waitFor(() => expect(listMock).toHaveBeenCalledTimes(1))

    const file = new File(["# Gold"], "new.md", { type: "text/markdown" })
    await act(async () => {
      fireEvent.change(fileInput(), { target: { files: [file] } })
    })

    await waitFor(() => expect(uploadMock).toHaveBeenCalledTimes(1))
    // The picked file is passed through to the API.
    const [passedFile] = uploadMock.mock.calls[0]
    expect((passedFile as File).name).toBe("new.md")
    // A refetch follows a successful upload.
    await waitFor(() => expect(listMock).toHaveBeenCalledTimes(2))
  })

  it("removes a template via templatesApi.remove, then refetches", async () => {
    await act(async () => {
      render(React.createElement(TemplatesScreen))
    })
    await waitFor(() => expect(listMock).toHaveBeenCalledTimes(1))

    const removeBtn = screen.getByRole("button", {
      name: /remove guest deal alerts/i,
    })
    await act(async () => {
      fireEvent.click(removeBtn)
    })

    await waitFor(() => expect(removeMock).toHaveBeenCalledWith("t1"))
    await waitFor(() => expect(listMock).toHaveBeenCalledTimes(2))
  })

  it("filters the visible cards by type client-side", async () => {
    listMock.mockResolvedValue([
      T1,
      { ...T1, id: "t2", label: "Strategy doc", type: "strategy", filename: "s.md" },
    ])
    await act(async () => {
      render(React.createElement(TemplatesScreen))
    })
    await waitFor(() => expect(screen.getByText("Strategy doc")).toBeTruthy())

    // Click the PRD filter pill → only the PRD card remains.
    await act(async () => {
      fireEvent.click(screen.getByRole("tab", { name: "PRD" }))
    })
    expect(screen.queryByText("Strategy doc")).toBeNull()
    expect(screen.getByText("Guest Deal Alerts — PRD")).toBeTruthy()
    // No refetch — filtering is client-side.
    expect(listMock).toHaveBeenCalledTimes(1)
  })

  it("surfaces an error when loading fails", async () => {
    listMock.mockRejectedValueOnce(new Error("network down"))
    await act(async () => {
      render(React.createElement(TemplatesScreen))
    })
    await waitFor(() => expect(screen.getByText(/network down/i)).toBeTruthy())
  })
})
