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
vi.mock("../../../../context/NavigationContext", () => ({
  useNavigation: () => ({ openContentPanel }),
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

  it("uploads → polls until ready → opens the PRD page", async () => {
    importDoc.mockResolvedValue({ prd_id: 7, status: "generating", title: "My Great PRD" })
    // Ready on the first poll (the 2.5s inter-poll wait is only hit while still
    // generating; covering that path would need fake timers — not worth it here).
    prdGet.mockResolvedValue({ id: 7, status: "ready", payload_md: "<h1>PRD</h1>" })

    await act(async () => { render(<ArtifactsScreen />) })
    const file = selectFile()

    await waitFor(() => expect(importDoc).toHaveBeenCalledWith(file, "acme"))
    // Opens the standard PRD page once ready.
    await waitFor(() => expect(openContentPanel).toHaveBeenCalledWith("prd"))
    expect(setContent).toHaveBeenCalled()
    const arg = setContent.mock.calls.at(-1)![0]
    expect(arg.prd.prd_id).toBe(7)
    // Refreshes the artifacts list after import (initial mount + post-import).
    expect(artifactsList.mock.calls.length).toBeGreaterThanOrEqual(2)
  }, 15000)

  it("surfaces an error when generation fails", async () => {
    importDoc.mockResolvedValue({ prd_id: 9, status: "generating", title: "x" })
    prdGet.mockResolvedValue({ id: 9, status: "failed", payload_md: "" })

    await act(async () => { render(<ArtifactsScreen />) })
    selectFile()

    await waitFor(() =>
      expect(screen.queryByTestId("prd-import-error")).not.toBeNull(),
    )
    expect(openContentPanel).not.toHaveBeenCalled()
  })
})
