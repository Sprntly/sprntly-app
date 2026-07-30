// @vitest-environment jsdom
//
// DOM tests for the roadmap upload/replace block in Settings → Process &
// Planning.
//
// Why it exists: the roadmap doc had exactly ONE entry point — the onboarding
// Workspace step. Both the onboarding copy ("add it later in Settings") and the
// /roadmap empty state ("or Settings") pointed at a place that didn't exist, so a
// PM who skipped the step, or whose roadmap changed, had no way to upload one.
//
// These tests prove:
//   (a) with a stored roadmap, the block shows filename + version + uploaded_at
//       and links to /roadmap, and the picker reads "Replace roadmap";
//   (b) with none stored, it says so and the picker reads "Upload roadmap";
//   (c) picking a file POSTs through roadmapDocApi.upload and the block then
//       shows the new filename/version (re-read from the server);
//   (d) the picker is busy/disabled during the upload;
//   (e) an upload failure surfaces an inline error and leaves the picker usable;
//   (f) a failed GET degrades to the empty state instead of breaking the pane.
import * as React from "react"
import { act, cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react"
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest"

;(globalThis as typeof globalThis & { React?: typeof React }).React = React

const useWorkspaceMock = vi.fn()
const refreshMock = vi.fn(() => Promise.resolve())
const roadmapGetMock = vi.fn()
const roadmapUploadMock = vi.fn()

vi.mock("../../../../../context/WorkspaceContext", () => ({
  useWorkspace: () => useWorkspaceMock(),
  profileDisplayName: () => null,
}))
vi.mock("../../../../../lib/onboarding/store", () => ({
  updateWorkspace: vi.fn(() => Promise.resolve({})),
}))
vi.mock("../../../../../lib/api", () => ({
  workspacesApi: { update: vi.fn(() => Promise.resolve({})) },
  roadmapDocApi: {
    get: () => roadmapGetMock(),
    upload: (file: File) => roadmapUploadMock(file),
  },
}))

import { ProcessSettings } from "../ProcessSettings"

function mount() {
  useWorkspaceMock.mockReturnValue({
    workspace: { id: "ws-1", team_name: "Growth" },
    workspaces: [{ id: "ws-1", is_default: true }],
    profile: null,
    loading: false,
    refresh: refreshMock,
  })
  return render(React.createElement(ProcessSettings))
}

/** The hidden file input behind the upload/replace strip. */
function picker(): HTMLInputElement {
  const el = document.getElementById("pr-roadmap-file") as HTMLInputElement | null
  if (!el) throw new Error("roadmap file input not found")
  return el
}

function block(): HTMLElement {
  const el = document.querySelector("[data-testid='roadmap-doc-block']")
  if (!el) throw new Error("roadmap block not rendered")
  return el as HTMLElement
}

function file(name: string): File {
  return new File(["BET A: onboarding"], name, { type: "text/markdown" })
}

const STORED = {
  filename: "H2-roadmap.pdf",
  content_type: "application/pdf",
  extracted_text: "# H2\n\nSelf-serve onboarding",
  uploaded_at: "2026-07-20T10:00:00Z",
  version: 2,
}

beforeEach(() => {
  roadmapGetMock.mockResolvedValue(null)
  roadmapUploadMock.mockResolvedValue({ ok: true, filename: "new.pdf", version: 3, extracted_chars: 120 })
})

afterEach(() => {
  cleanup()
  vi.clearAllMocks()
})

describe("Process & Planning — roadmap document block", () => {
  it("shows the stored roadmap's filename, version, upload date and a link to /roadmap", async () => {
    roadmapGetMock.mockResolvedValue(STORED)
    await act(async () => {
      mount()
    })
    const current = await waitFor(() =>
      screen.getByTestId("roadmap-doc-current"),
    )
    expect(current.textContent).toContain("H2-roadmap.pdf")
    expect(current.textContent).toContain("version 2")
    expect(current.textContent).toMatch(/uploaded /)
    const link = current.querySelector("a")
    expect(link?.getAttribute("href")).toBe("/roadmap")
    // The picker offers a REPLACE, not a first upload.
    expect(block().textContent).toContain("Replace roadmap")
  })

  it("offers a first upload when the company has no roadmap yet", async () => {
    roadmapGetMock.mockResolvedValue(null)
    await act(async () => {
      mount()
    })
    await waitFor(() => screen.getByTestId("roadmap-doc-empty"))
    expect(screen.getByTestId("roadmap-doc-empty").textContent).toContain(
      "No roadmap uploaded yet",
    )
    expect(block().textContent).toContain("Upload roadmap")
    // Never "dataset" — this is the company's roadmap.
    expect(block().textContent?.toLowerCase()).not.toContain("dataset")
  })

  it("posts the picked file and then shows the newly stored roadmap", async () => {
    roadmapGetMock.mockResolvedValueOnce(null)          // initial load: none
    await act(async () => {
      mount()
    })
    await waitFor(() => screen.getByTestId("roadmap-doc-empty"))

    // The post-upload re-read returns the freshly stored roadmap.
    roadmapGetMock.mockResolvedValue({ ...STORED, filename: "new.pdf", version: 3 })
    const f = file("new.pdf")
    await act(async () => {
      fireEvent.change(picker(), { target: { files: [f] } })
    })

    expect(roadmapUploadMock).toHaveBeenCalledTimes(1)
    expect(roadmapUploadMock.mock.calls[0][0]).toBe(f)
    const current = await waitFor(() => screen.getByTestId("roadmap-doc-current"))
    expect(current.textContent).toContain("new.pdf")
    expect(current.textContent).toContain("version 3")
  })

  it("falls back to the upload response when the re-read fails", async () => {
    roadmapGetMock.mockResolvedValueOnce(null)
    await act(async () => {
      mount()
    })
    await waitFor(() => screen.getByTestId("roadmap-doc-empty"))

    roadmapGetMock.mockRejectedValue(new Error("network"))
    await act(async () => {
      fireEvent.change(picker(), { target: { files: [file("new.pdf")] } })
    })
    const current = await waitFor(() => screen.getByTestId("roadmap-doc-current"))
    expect(current.textContent).toContain("new.pdf")
    expect(current.textContent).toContain("version 3")
  })

  it("disables the picker while the upload is in flight", async () => {
    roadmapGetMock.mockResolvedValue(null)
    let release: (v: unknown) => void = () => {}
    roadmapUploadMock.mockReturnValue(new Promise((r) => { release = r }))
    await act(async () => {
      mount()
    })
    await waitFor(() => screen.getByTestId("roadmap-doc-empty"))

    await act(async () => {
      fireEvent.change(picker(), { target: { files: [file("slow.pdf")] } })
    })
    expect(picker().disabled).toBe(true)
    expect(block().textContent).toContain("Uploading…")

    await act(async () => {
      release({ ok: true, filename: "slow.pdf", version: 1, extracted_chars: 10 })
    })
    await waitFor(() => expect(picker().disabled).toBe(false))
  })

  it("surfaces an inline error when the upload fails and stays usable", async () => {
    roadmapGetMock.mockResolvedValue(null)
    roadmapUploadMock.mockRejectedValue(new Error("File exceeds 20MB limit"))
    await act(async () => {
      mount()
    })
    await waitFor(() => screen.getByTestId("roadmap-doc-empty"))

    await act(async () => {
      fireEvent.change(picker(), { target: { files: [file("huge.pdf")] } })
    })
    await waitFor(() =>
      expect(block().textContent).toContain("File exceeds 20MB limit"),
    )
    expect(block().textContent).toContain("huge.pdf")
    expect(picker().disabled).toBe(false)   // retry is possible
    // Still no roadmap stored.
    expect(screen.getByTestId("roadmap-doc-empty")).toBeTruthy()
  })

  it("degrades to the empty state when the roadmap read fails", async () => {
    roadmapGetMock.mockRejectedValue(new Error("500"))
    await act(async () => {
      mount()
    })
    await waitFor(() => screen.getByTestId("roadmap-doc-empty"))
    // The rest of the pane still renders.
    expect(document.getElementById("pr-team-roadmap")).toBeTruthy()
  })

  it("does nothing when the picker is dismissed without a file", async () => {
    roadmapGetMock.mockResolvedValue(null)
    await act(async () => {
      mount()
    })
    await waitFor(() => screen.getByTestId("roadmap-doc-empty"))
    await act(async () => {
      fireEvent.change(picker(), { target: { files: [] } })
    })
    expect(roadmapUploadMock).not.toHaveBeenCalled()
  })
})
