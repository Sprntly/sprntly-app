// @vitest-environment jsdom
//
// AddArtifactModal — the "Add existing artifact" company-library picker.
// REUSES `artifactsApi.list`/`projectsApi.addArtifact` — no new artifact API
// (mocked here at the network boundary). Covers the library list render,
// the multi-select-then-confirm write, and existing-key rows disabled/
// non-toggleable.
import * as React from "react"
import { readFileSync } from "node:fs"
import { join } from "node:path"
import { act, cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react"
import { afterEach, describe, expect, it, vi } from "vitest"

;(globalThis as typeof globalThis & { React?: typeof React }).React = React

const listMock = vi.fn()
const addArtifactMock = vi.fn()

vi.mock("../../../../../lib/api", async () => {
  const actual = await vi.importActual<typeof import("../../../../../lib/api")>("../../../../../lib/api")
  return {
    ...actual,
    artifactsApi: {
      ...actual.artifactsApi,
      list: (...a: unknown[]) => listMock(...a),
    },
    projectsApi: {
      ...actual.projectsApi,
      addArtifact: (...a: unknown[]) => addArtifactMock(...a),
    },
  }
})

vi.mock("../../../../../context/CompanyContext", () => ({
  useCompany: () => ({ activeCompany: "acme", setActiveCompany: vi.fn(), activeCompanyDisplayName: "Acme" }),
}))

import { AddArtifactModal } from "../AddArtifactModal"
import type { ArtifactItem } from "../../../../../lib/api"

const hoursAgo = (h: number) => new Date(Date.now() - h * 3600 * 1000).toISOString()

const LIBRARY: ArtifactItem[] = [
  {
    type: "prd",
    id: 1,
    title: "Instant-quote flow — v3",
    status: "ready",
    created_at: hoursAgo(2),
    source: { brief_id: 1, week_label: "wk 32", insight_index: null },
    open: { brief_id: 1, insight_index: null, prd_id: 1 },
  } as ArtifactItem,
  {
    type: "evidence",
    id: 3,
    title: "Xometry call — quoting friction",
    status: "ready",
    created_at: hoursAgo(70),
    source: { brief_id: 1, week_label: "wk 31", insight_index: null },
    open: { brief_id: 1, insight_index: null, evidence_id: 3 },
  } as ArtifactItem,
  {
    type: "prd",
    id: 2,
    title: "Onboarding v2",
    status: "ready",
    created_at: hoursAgo(5),
    source: { brief_id: 1, week_label: "wk 32", insight_index: null },
    open: { brief_id: 1, insight_index: null, prd_id: 2 },
  } as ArtifactItem,
]

afterEach(() => {
  cleanup()
  listMock.mockReset()
  addArtifactMock.mockReset()
})

describe("AddArtifactModal — company library (AC-10)", () => {
  it("test_lists_company_library — opening it fetches artifactsApi.list(activeCompany) and renders one row per artifact", async () => {
    listMock.mockResolvedValue(LIBRARY)
    await act(async () => {
      render(
        React.createElement(AddArtifactModal, {
          projectId: 101,
          open: true,
          existingKeys: new Set<string>(),
          onClose: vi.fn(),
          onAdded: vi.fn(),
        }),
      )
    })
    await waitFor(() => expect(listMock).toHaveBeenCalledWith("acme"))
    const list = await screen.findByTestId("add-artifact-modal-list")
    expect(within(list).getAllByRole("button")).toHaveLength(3)
    expect(list.textContent).toContain("Instant-quote flow — v3")
    expect(list.textContent).toContain("Xometry call — quoting friction")
  })

  it("fetches nothing while closed", () => {
    render(
      React.createElement(AddArtifactModal, {
        projectId: 101,
        open: false,
        existingKeys: new Set<string>(),
        onClose: vi.fn(),
        onAdded: vi.fn(),
      }),
    )
    expect(listMock).not.toHaveBeenCalled()
  })

  it("test_add_selected_writes_via_projectsApi — selecting N artifacts and confirming writes each via projectsApi.addArtifact and fires onAdded", async () => {
    listMock.mockResolvedValue(LIBRARY)
    addArtifactMock.mockResolvedValue({ project_id: 101, artifact_type: "prd", artifact_id: 1 })
    const onAdded = vi.fn()
    const onClose = vi.fn()
    await act(async () => {
      render(
        React.createElement(AddArtifactModal, {
          projectId: 101,
          open: true,
          existingKeys: new Set<string>(),
          onClose,
          onAdded,
        }),
      )
    })
    await screen.findByTestId("add-artifact-modal-list")

    await act(async () => {
      fireEvent.click(screen.getByTestId("add-artifact-row-prd-1"))
      fireEvent.click(screen.getByTestId("add-artifact-row-evidence-3"))
    })
    const confirm = screen.getByTestId("add-artifact-modal-confirm") as HTMLButtonElement
    expect(confirm.disabled).toBe(false)
    expect(confirm.textContent).toContain("2 artifacts")

    await act(async () => {
      fireEvent.click(confirm)
    })

    await waitFor(() => expect(addArtifactMock).toHaveBeenCalledTimes(2))
    expect(addArtifactMock).toHaveBeenCalledWith(101, "prd", 1)
    expect(addArtifactMock).toHaveBeenCalledWith(101, "evidence", 3)
    await waitFor(() => expect(onAdded).toHaveBeenCalledTimes(1))
    expect(onClose).toHaveBeenCalledTimes(1)
  })

  it("test_existing_keys_rows_disabled — an existingKeys row is disabled/non-selectable, reads 'On this project'", async () => {
    listMock.mockResolvedValue(LIBRARY)
    await act(async () => {
      render(
        React.createElement(AddArtifactModal, {
          projectId: 101,
          open: true,
          existingKeys: new Set<string>(["prd-1"]),
          onClose: vi.fn(),
          onAdded: vi.fn(),
        }),
      )
    })
    await screen.findByTestId("add-artifact-modal-list")

    const existingRow = screen.getByTestId("add-artifact-row-prd-1") as HTMLButtonElement
    expect(existingRow.disabled).toBe(true)
    expect(within(existingRow).getByTestId("add-artifact-existing-prd-1").textContent).toBe("On this project")

    await act(async () => {
      fireEvent.click(existingRow)
    })
    // Disabled + click is a no-op — never enters the selection set.
    expect(screen.getByTestId("add-artifact-modal-confirm").hasAttribute("disabled")).toBe(true)

    // A non-existing row IS selectable.
    await act(async () => {
      fireEvent.click(screen.getByTestId("add-artifact-row-evidence-3"))
    })
    expect((screen.getByTestId("add-artifact-modal-confirm") as HTMLButtonElement).disabled).toBe(false)
  })

  it("renders a graceful error state on a failed fetch, never a crash", async () => {
    listMock.mockRejectedValue(new Error("network blip"))
    await act(async () => {
      render(
        React.createElement(AddArtifactModal, {
          projectId: 101,
          open: true,
          existingKeys: new Set<string>(),
          onClose: vi.fn(),
          onAdded: vi.fn(),
        }),
      )
    })
    await waitFor(() => expect(screen.getByTestId("add-artifact-modal-error")).toBeTruthy())
  })

  it("filtering by type + search narrows the list", async () => {
    listMock.mockResolvedValue(LIBRARY)
    await act(async () => {
      render(
        React.createElement(AddArtifactModal, {
          projectId: 101,
          open: true,
          existingKeys: new Set<string>(),
          onClose: vi.fn(),
          onAdded: vi.fn(),
        }),
      )
    })
    await screen.findByTestId("add-artifact-modal-list")
    fireEvent.click(screen.getByTestId("add-artifact-filter-prd"))
    expect(within(screen.getByTestId("add-artifact-modal-list")).getAllByRole("button")).toHaveLength(2)

    fireEvent.change(screen.getByTestId("add-artifact-search"), { target: { value: "Onboarding" } })
    expect(within(screen.getByTestId("add-artifact-modal-list")).getAllByRole("button")).toHaveLength(1)
    expect(screen.getByTestId("add-artifact-modal-list").textContent).toContain("Onboarding v2")
  })

  it("closes on Cancel without writing anything", async () => {
    listMock.mockResolvedValue(LIBRARY)
    const onClose = vi.fn()
    await act(async () => {
      render(
        React.createElement(AddArtifactModal, {
          projectId: 101,
          open: true,
          existingKeys: new Set<string>(),
          onClose,
          onAdded: vi.fn(),
        }),
      )
    })
    await screen.findByTestId("add-artifact-modal-list")
    fireEvent.click(screen.getByTestId("add-artifact-modal-cancel"))
    expect(onClose).toHaveBeenCalledTimes(1)
    expect(addArtifactMock).not.toHaveBeenCalled()
  })
})

describe("AddArtifactModal.module.css — tokens only", () => {
  it("resolves every color to a globals.css custom property — no new palette", () => {
    const css = readFileSync(join(__dirname, "../AddArtifactModal.module.css"), "utf8")
    const found = css.match(/#[0-9A-Fa-f]{3,8}/g) ?? []
    // Same tolerance ArtifactsModal.test.tsx takes: plain white is not a
    // "new palette" entry.
    const disallowed = found.filter((hex) => hex.toLowerCase() !== "#fff")
    expect(disallowed).toEqual([])
  })
})
