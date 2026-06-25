// @vitest-environment jsdom
//
// Container mount test for the relocated company-shape section in
// Settings → Business Context. These fields (industry / business type /
// tech stack) moved off the onboarding business-context step (now
// narrative-only). They load from the workspace and persist via
// updateWorkspace to companies.industry / business_type / tech_stack.
import * as React from "react"
import { act, cleanup, fireEvent, render } from "@testing-library/react"
import { afterEach, describe, expect, it, vi } from "vitest"

;(globalThis as typeof globalThis & { React?: typeof React }).React = React

const useWorkspaceMock = vi.fn()
const updateWorkspaceMock = vi.fn()
const refreshMock = vi.fn()

vi.mock("../../../../../context/WorkspaceContext", () => ({
  useWorkspace: () => useWorkspaceMock(),
}))
vi.mock("../../../../../lib/onboarding/store", () => ({
  updateWorkspace: (...a: unknown[]) => updateWorkspaceMock(...a),
}))

import { CompanyShapeSettings } from "../BusinessContextSettings"

function makeWorkspace(over: Record<string, unknown> = {}) {
  return {
    id: "ws-1",
    industry: "Fintech",
    business_type: "Marketplace",
    tech_stack: ["Web"],
    ...over,
  }
}

afterEach(() => {
  cleanup()
  vi.clearAllMocks()
})

describe("CompanyShapeSettings (Settings → Business Context)", () => {
  it("loads the workspace company-shape fields into the controls", async () => {
    useWorkspaceMock.mockReturnValue({
      workspace: makeWorkspace(),
      loading: false,
      refresh: refreshMock,
    })

    await act(async () => {
      render(React.createElement(CompanyShapeSettings, { canEdit: true }))
    })

    const industry = document.querySelector(
      '[data-field="industry"] select',
    ) as HTMLSelectElement
    const bizType = document.querySelector(
      '[data-field="businessType"] select',
    ) as HTMLSelectElement
    expect(industry.value).toBe("Fintech")
    expect(bizType.value).toBe("Marketplace")
    // The pre-selected tech chip is marked selected.
    const webChip = Array.from(
      document.querySelectorAll('[data-field="techStack"] .metric-chip'),
    ).find((b) => b.textContent === "Web") as HTMLButtonElement
    expect(webChip.className).toContain("selected")
  })

  it("persists edits via updateWorkspace on Save", async () => {
    useWorkspaceMock.mockReturnValue({
      workspace: makeWorkspace({ tech_stack: [] }),
      loading: false,
      refresh: refreshMock,
    })
    updateWorkspaceMock.mockResolvedValue(makeWorkspace())
    refreshMock.mockResolvedValue(undefined)

    await act(async () => {
      render(React.createElement(CompanyShapeSettings, { canEdit: true }))
    })

    // Toggle a tech-stack chip on.
    const chip = document.querySelector(
      '[data-field="techStack"] .metric-chip',
    ) as HTMLButtonElement
    const chipLabel = chip.textContent ?? ""
    fireEvent.click(chip)

    const saveBtn = Array.from(document.querySelectorAll("button")).find((b) =>
      /save company shape/i.test(b.textContent ?? ""),
    ) as HTMLButtonElement
    await act(async () => {
      saveBtn.click()
    })

    expect(updateWorkspaceMock).toHaveBeenCalledTimes(1)
    const [id, patch] = updateWorkspaceMock.mock.calls[0] as [
      string,
      Record<string, unknown>,
    ]
    expect(id).toBe("ws-1")
    expect(patch.industry).toBe("Fintech")
    expect(patch.business_type).toBe("Marketplace")
    expect(patch.tech_stack).toEqual([chipLabel])
    expect(refreshMock).toHaveBeenCalledTimes(1)
  })

  it("non-admin cannot save (no Save button, controls disabled)", async () => {
    useWorkspaceMock.mockReturnValue({
      workspace: makeWorkspace(),
      loading: false,
      refresh: refreshMock,
    })

    await act(async () => {
      render(React.createElement(CompanyShapeSettings, { canEdit: false }))
    })

    const saveBtn = Array.from(document.querySelectorAll("button")).find((b) =>
      /save company shape/i.test(b.textContent ?? ""),
    )
    expect(saveBtn).toBeUndefined()
    const industry = document.querySelector(
      '[data-field="industry"] select',
    ) as HTMLSelectElement
    expect(industry.disabled).toBe(true)
  })
})
