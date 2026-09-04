// @vitest-environment jsdom
//
// ProjectArtifactsDrawer — the V2 document-upload UI. Covers the "+ Add ▾"
// split menu (Upload document / Add existing) + the always-present upload
// strip, the upload flow (optimistic processing row → resolved DOC row via the
// EXISTING row renderer), the status-mapped error path, drag-and-drop, and that
// "Add existing" still swaps to the reused AddArtifactPanel (not regressed).
//
// REUSES the network-boundary mock pattern of AddArtifactModal.dom.test.tsx:
// `projectsApi`/`artifactsApi` overridden on the real lib/api module; the
// heavy `DocumentRoute` (AppLayout/editor) is stubbed to its pure
// `documentPath` helper, which is all the drawer imports from it.
import * as React from "react"
import { act, cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react"
import { afterEach, describe, expect, it, vi } from "vitest"

;(globalThis as typeof globalThis & { React?: typeof React }).React = React

const artifactsMock = vi.fn()
const uploadDocumentMock = vi.fn()
const listMock = vi.fn()

vi.mock("../../../../../lib/api", async () => {
  const actual = await vi.importActual<typeof import("../../../../../lib/api")>("../../../../../lib/api")
  return {
    ...actual,
    artifactsApi: { ...actual.artifactsApi, list: (...a: unknown[]) => listMock(...a) },
    projectsApi: {
      ...actual.projectsApi,
      artifacts: (...a: unknown[]) => artifactsMock(...a),
      uploadDocument: (...a: unknown[]) => uploadDocumentMock(...a),
    },
  }
})

const { pushSpy } = vi.hoisted(() => ({ pushSpy: vi.fn() }))
vi.mock("next/navigation", () => ({
  useRouter: () => ({ push: pushSpy, replace: vi.fn(), prefetch: vi.fn() }),
  useSearchParams: () => new URLSearchParams(),
}))

// The drawer imports only `documentPath` (a pure href builder) from this module;
// stub it so the test doesn't pull in AppLayout / the document editor.
vi.mock("../../../../../(app)/artifacts/doc/DocumentRoute", () => ({
  documentPath: (id: number) => `/doc/${id}`,
}))

import { ProjectArtifactsDrawer } from "../ProjectArtifactsDrawer"
import { ApiError, type ArtifactItem } from "../../../../../lib/api"

const hoursAgo = (h: number) => new Date(Date.now() - h * 3600 * 1000).toISOString()

function docItem(id: number, title: string): ArtifactItem {
  return {
    type: "custom_artifact",
    id,
    title,
    status: "ready",
    created_at: hoursAgo(0),
    updated_at: hoursAgo(0),
    born_at: hoursAgo(0),
    kind: "document",
    source: { kind: "document", conversation_id: null, conversation_title: null },
    open: { custom_artifact_id: id },
  } as ArtifactItem
}

/** A deferred promise so a test controls when the upload resolves. */
function deferred<T>() {
  let resolve!: (v: T) => void
  let reject!: (e: unknown) => void
  const promise = new Promise<T>((res, rej) => {
    resolve = res
    reject = rej
  })
  return { promise, resolve, reject }
}

function renderDrawer(extra?: { onOpenInPlace?: (a: ArtifactItem) => void }) {
  return render(
    <ProjectArtifactsDrawer
      projectId={7}
      open
      onClose={vi.fn()}
      onArtifactsChanged={vi.fn()}
      onOpenInPlace={extra?.onOpenInPlace}
    />,
  )
}

afterEach(() => {
  cleanup()
  vi.clearAllMocks()
})

describe("ProjectArtifactsDrawer — V2 upload UI", () => {
  it("renders the '+ Add' split menu (Upload document / Add existing) and the upload strip", async () => {
    artifactsMock.mockResolvedValueOnce([])
    renderDrawer()

    // Upload strip is always present once the drawer is ready.
    await screen.findByTestId("artifacts-drawer-upload-strip")

    // The split-menu toggle is present; the menu opens on click with both items.
    const toggle = screen.getByTestId("artifacts-drawer-add")
    expect(screen.queryByTestId("artifacts-drawer-add-menu")).toBeNull()
    fireEvent.click(toggle)
    const menu = screen.getByTestId("artifacts-drawer-add-menu")
    expect(within(menu).getByTestId("artifacts-drawer-menu-upload")).toBeTruthy()
    expect(within(menu).getByTestId("artifacts-drawer-menu-existing")).toBeTruthy()
  })

  it("uploads a chosen file: optimistic processing row, then the resolved DOC row", async () => {
    artifactsMock.mockResolvedValueOnce([])
    const gate = deferred<ArtifactItem>()
    uploadDocumentMock.mockReturnValueOnce(gate.promise)
    renderDrawer()
    await screen.findByTestId("artifacts-drawer-upload-strip")

    const input = screen.getByTestId("artifacts-drawer-file-input") as HTMLInputElement
    const file = new File(["# Hi\n\nhello"], "Launch Plan.md", { type: "text/markdown" })
    fireEvent.change(input, { target: { files: [file] } })

    // The client method was called with (projectId, file).
    expect(uploadDocumentMock).toHaveBeenCalledTimes(1)
    expect(uploadDocumentMock.mock.calls[0][0]).toBe(7)
    expect(uploadDocumentMock.mock.calls[0][1]).toBe(file)

    // Optimistic processing row (spinner) shows the filename.
    const proc = await screen.findByText("Launch Plan.md")
    const procRow = proc.closest("[data-upload-state]") as HTMLElement
    expect(procRow.getAttribute("data-upload-state")).toBe("uploading")

    // Resolve → processing row removed, the real DOC row (returned DTO) appears.
    await act(async () => {
      gate.resolve(docItem(42, "Launch Plan"))
      await gate.promise
    })
    await waitFor(() =>
      expect(screen.getByTestId("artifacts-drawer-row-custom_artifact-42")).toBeTruthy(),
    )
    expect(screen.queryByText("Launch Plan.md")).toBeNull()
    expect(screen.getByText("Launch Plan")).toBeTruthy()
  })

  it("maps a 413 to a specific error and removes the processing spinner", async () => {
    artifactsMock.mockResolvedValueOnce([])
    const gate = deferred<ArtifactItem>()
    uploadDocumentMock.mockReturnValueOnce(gate.promise)
    renderDrawer()
    await screen.findByTestId("artifacts-drawer-upload-strip")

    const input = screen.getByTestId("artifacts-drawer-file-input") as HTMLInputElement
    fireEvent.change(input, {
      target: { files: [new File(["x"], "huge.pdf", { type: "application/pdf" })] },
    })
    await screen.findByText("huge.pdf")

    await act(async () => {
      gate.reject(new ApiError(413, { detail: "too large" }))
      await gate.promise.catch(() => {})
    })

    // Mapped, specific message; row flips to error (no progress spinner).
    await screen.findByText(/too large/i)
    const row = screen.getByText("huge.pdf").closest("[data-upload-state]") as HTMLElement
    expect(row.getAttribute("data-upload-state")).toBe("error")
  })

  it("maps a 422 (unreadable) to its own message", async () => {
    artifactsMock.mockResolvedValueOnce([])
    const gate = deferred<ArtifactItem>()
    uploadDocumentMock.mockReturnValueOnce(gate.promise)
    renderDrawer()
    await screen.findByTestId("artifacts-drawer-upload-strip")

    fireEvent.change(screen.getByTestId("artifacts-drawer-file-input"), {
      target: { files: [new File(["x"], "scan.png", { type: "image/png" })] },
    })
    await screen.findByText("scan.png")
    await act(async () => {
      gate.reject(new ApiError(422, { detail: "unreadable" }))
      await gate.promise.catch(() => {})
    })
    await screen.findByText(/couldn.t read any text/i)
  })

  it("dropping a file onto the drawer body starts an upload", async () => {
    artifactsMock.mockResolvedValueOnce([])
    uploadDocumentMock.mockReturnValueOnce(new Promise<never>(() => {}))
    renderDrawer()
    const body = await screen.findByTestId("artifacts-drawer-body")

    const file = new File(["hello"], "dropped.txt", { type: "text/plain" })
    fireEvent.drop(body, { dataTransfer: { files: [file] } })

    expect(uploadDocumentMock).toHaveBeenCalledTimes(1)
    expect(uploadDocumentMock.mock.calls[0][1]).toBe(file)
  })

  it("'Add existing artifact' still swaps to the reused add panel (not regressed)", async () => {
    artifactsMock.mockResolvedValueOnce([])
    listMock.mockResolvedValue([])
    renderDrawer()
    await screen.findByTestId("artifacts-drawer-upload-strip")

    fireEvent.click(screen.getByTestId("artifacts-drawer-add"))
    fireEvent.click(screen.getByTestId("artifacts-drawer-menu-existing"))

    // The add-view host (the reused AddArtifactPanel's shell) is now rendered.
    await screen.findByTestId("artifacts-drawer-add-host")
    expect(uploadDocumentMock).not.toHaveBeenCalled()
  })

  it("opens an uploaded document IN-PLACE (onOpenInPlace), not via full-page navigation", async () => {
    artifactsMock.mockResolvedValueOnce([docItem(42, "Uploaded teardown")])
    const onOpenInPlace = vi.fn()
    renderDrawer({ onOpenInPlace })

    const row = await screen.findByTestId("artifacts-drawer-row-custom_artifact-42")
    fireEvent.click(row)

    // Routed through the in-place seam (the same one PRD/report/ticket_set use),
    // NOT the full-page documentPath router.push.
    expect(onOpenInPlace).toHaveBeenCalledTimes(1)
    expect(onOpenInPlace.mock.calls[0][0].id).toBe(42)
    expect(onOpenInPlace.mock.calls[0][0].type).toBe("custom_artifact")
    expect(pushSpy).not.toHaveBeenCalled()
  })
})
