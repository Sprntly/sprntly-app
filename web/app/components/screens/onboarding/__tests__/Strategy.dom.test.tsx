// @vitest-environment jsdom
//
// Container mount test for the onboarding step 04 — "Strategy, leadership &
// your roadmap" (design scene onbstrat). Captures free-text priorities (→
// companies.okrs) and a roadmap-doc upload affordance stubbed to
// POST /v1/company/roadmap-doc (roadmapDocApi.upload).
//
// Covers: priorities persist + advance to workspace, skip, and the roadmap-doc
// upload SOFT-FAILS (the assumed endpoint may not exist) without blocking.
//
// Matchers: native DOM only.
import * as React from "react"
import { act, cleanup, fireEvent, render, screen } from "@testing-library/react"
import { afterEach, describe, expect, it, vi } from "vitest"

;(globalThis as typeof globalThis & { React?: typeof React }).React = React

const onboardingMock = vi.fn()
const routerMock = { push: vi.fn(), replace: vi.fn() }
const advanceStepMock = vi.fn()
const updateWorkspaceMock = vi.fn()
const roadmapUploadMock = vi.fn()

vi.mock("../../../../context/OnboardingContext", () => ({
  useOnboarding: () => onboardingMock(),
}))
vi.mock("next/navigation", () => ({ useRouter: () => routerMock }))
vi.mock("../../../../lib/onboarding/store", () => ({
  advanceOnboardingStep: (...a: unknown[]) => advanceStepMock(...a),
  updateWorkspace: (...a: unknown[]) => updateWorkspaceMock(...a),
}))
vi.mock("../../../../lib/onboarding/useFormDraft", () => ({
  saveDraft: vi.fn(),
  loadDraft: () => null,
  clearDraft: vi.fn(),
}))
vi.mock("../../../../lib/api", async () => {
  const actual = await vi.importActual<typeof import("../../../../lib/api")>(
    "../../../../lib/api",
  )
  return {
    ...actual,
    roadmapDocApi: { upload: (...a: unknown[]) => roadmapUploadMock(...a) },
  }
})

import { Strategy } from "../Strategy"
import { makeOnboardingCtx } from "./fixtures"

afterEach(() => {
  cleanup()
  vi.clearAllMocks()
})

describe("Strategy (onboarding step 04)", () => {
  it("renders the strategy heading + roadmap-doc upload affordance", () => {
    onboardingMock.mockReturnValue(makeOnboardingCtx())
    render(React.createElement(Strategy))
    expect(screen.getByText(/your roadmap/i)).not.toBeNull()
    expect(document.querySelector('[data-field="roadmap-doc"]')).not.toBeNull()
  })

  it("Continue persists priorities to okrs and advances to workspace", async () => {
    onboardingMock.mockReturnValue(makeOnboardingCtx())
    updateWorkspaceMock.mockResolvedValue(undefined)

    render(React.createElement(Strategy))
    const ta = document.querySelector("textarea") as HTMLTextAreaElement
    fireEvent.change(ta, { target: { value: "Ship reconciliation v2 this half." } })

    const continueBtn = Array.from(document.querySelectorAll("button")).find((b) =>
      /create workspace|continue/i.test(b.textContent ?? ""),
    ) as HTMLButtonElement
    await act(async () => {
      continueBtn.click()
    })

    expect(updateWorkspaceMock).toHaveBeenCalledTimes(1)
    const [, patch] = updateWorkspaceMock.mock.calls[0] as [string, Record<string, unknown>]
    expect(patch.okrs).toBe("Ship reconciliation v2 this half.")
    expect(patch.onboarding_step).toBe(5)
    expect(routerMock.push).toHaveBeenCalledWith("/onboarding/workspace")
  })

  it("Skip for now advances without persisting priorities", async () => {
    onboardingMock.mockReturnValue(makeOnboardingCtx())
    advanceStepMock.mockResolvedValue(undefined)

    render(React.createElement(Strategy))
    const skip = Array.from(document.querySelectorAll("button")).find((b) =>
      /skip for now/i.test(b.textContent ?? ""),
    ) as HTMLButtonElement
    await act(async () => {
      skip.click()
    })
    expect(updateWorkspaceMock).not.toHaveBeenCalled()
    expect(advanceStepMock).toHaveBeenCalledWith("ws-1", 5)
    expect(routerMock.push).toHaveBeenCalledWith("/onboarding/workspace")
  })

  it("roadmap-doc upload calls the stub endpoint and SOFT-FAILS without blocking", async () => {
    onboardingMock.mockReturnValue(makeOnboardingCtx())
    // The assumed endpoint isn't implemented yet — simulate a rejection.
    roadmapUploadMock.mockRejectedValue(new Error("404 Not Found"))

    render(React.createElement(Strategy))
    const fileInput = document.querySelector(
      'input[type="file"]',
    ) as HTMLInputElement
    const file = new File(["roadmap"], "roadmap.pdf", { type: "application/pdf" })
    await act(async () => {
      fireEvent.change(fileInput, { target: { files: [file] } })
    })

    expect(roadmapUploadMock).toHaveBeenCalledTimes(1)
    // Soft-fail: a friendly notice renders and the step is NOT blocked.
    expect(screen.getByText(/won't block setup|roadmap import is enabled/i)).not.toBeNull()
    // Continue still works after a failed upload.
    const continueBtn = Array.from(document.querySelectorAll("button")).find((b) =>
      /create workspace|continue/i.test(b.textContent ?? ""),
    ) as HTMLButtonElement
    expect((continueBtn as HTMLButtonElement).disabled).toBe(false)
  })
})
