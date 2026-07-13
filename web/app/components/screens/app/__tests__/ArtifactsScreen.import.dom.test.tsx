// @vitest-environment jsdom
//
// Integration tests for the "Upload PRD" flow on ArtifactsScreen: the button
// posts the file to prdApi.importDoc, polls prdApi.get until ready, then opens
// the standard PRD page (setContent + openContentPanel("prd")) and refreshes
// the artifacts list. The api + contexts are stubbed so this exercises the
// screen's wiring, not the network.

import * as React from "react"
import { act, cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react"
import { afterEach, describe, expect, it, vi } from "vitest"

vi.hoisted(() => {
  ;(globalThis as Record<string, unknown>).React = require("react")
})

const importDoc = vi.fn()
const prdGet = vi.fn()
const artifactsList = vi.fn(() => Promise.resolve([]))

vi.mock("../../../../lib/api", () => ({
  artifactsApi: { list: (...a: unknown[]) => artifactsList(...a) },
  prdApi: {
    importDoc: (...a: unknown[]) => importDoc(...a),
    get: (...a: unknown[]) => prdGet(...a),
  },
  evidenceApi: { get: vi.fn() },
}))

const setContent = vi.fn()
const openContentPanel = vi.fn()
const openPrdTab = vi.fn()
vi.mock("../../../../context/NavigationContext", () => ({
  useNavigation: () => ({ openContentPanel, openPrdTab }),
}))
vi.mock("../../../../context/ContentContext", () => ({
  useContent: () => ({ setContent }),
}))
vi.mock("../../../../context/CompanyContext", () => ({
  useCompany: () => ({ activeCompany: "acme" }),
}))
vi.mock("next/navigation", () => ({ useRouter: () => ({ push: vi.fn() }) }))
vi.mock("../../../../lib/prd-adapter", () => ({ markdownToPrdState: () => ({}) }))
vi.mock("../../../../lib/evidence-adapter", () => ({ markdownToEvidenceState: () => ({}) }))
vi.mock("../../../../lib/routes", () => ({ prototypePath: () => "/prototype" }))
vi.mock("../AppLayout", () => ({
  AppLayout: ({ children }: { children: React.ReactNode }) =>
    React.createElement("div", { "data-testid": "app-layout" }, children),
}))

import { ArtifactsScreen } from "../ArtifactsScreen"

afterEach(() => {
  cleanup()
  vi.clearAllMocks()
})

function selectFile() {
  const input = screen.getByTestId("prd-import-input") as HTMLInputElement
  const file = new File(["# My PRD\n\nGoal: X."], "My Great PRD.md", { type: "text/markdown" })
  fireEvent.change(input, { target: { files: [file] } })
  return file
}

describe("ArtifactsScreen — Upload PRD", () => {
  it("renders the Upload PRD button", async () => {
    await act(async () => { render(<ArtifactsScreen />) })
    expect(screen.getByTestId("prd-import-button").textContent).toContain("Upload PRD")
  })

  it("uploads → opens the chat window immediately (kind:resume), no blocking poll", async () => {
    importDoc.mockResolvedValue({ prd_id: 7, status: "generating", title: "My Great PRD" })

    await act(async () => { render(<ArtifactsScreen />) })
    const file = selectFile()

    await waitFor(() => expect(importDoc).toHaveBeenCalledWith(file, "acme"))
    // Opens a chat window immediately with kind:"resume" — the PRD panel polls to
    // ready in-tab, so a slow generation never looks like a hung upload.
    await waitFor(() => expect(openPrdTab).toHaveBeenCalled())
    const arg = openPrdTab.mock.calls.at(-1)![0]
    expect(arg.source.kind).toBe("resume")
    expect(arg.source.prdId).toBe(7)
    expect(arg.title).toContain("PRD ·")
    // The screen never polls prdApi.get itself — that's the tab's job now.
    expect(prdGet).not.toHaveBeenCalled()
    // Refreshes the artifacts list after import (initial mount + post-import).
    expect(artifactsList.mock.calls.length).toBeGreaterThanOrEqual(2)
  })

  it("surfaces an error when the import request itself fails", async () => {
    importDoc.mockRejectedValue(new Error("network boom"))

    await act(async () => { render(<ArtifactsScreen />) })
    selectFile()

    await waitFor(() =>
      expect(screen.queryByTestId("prd-import-error")).not.toBeNull(),
    )
    expect(openPrdTab).not.toHaveBeenCalled()
  })
})
