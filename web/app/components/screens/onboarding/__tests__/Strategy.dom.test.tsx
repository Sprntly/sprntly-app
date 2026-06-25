// @vitest-environment jsdom
//
// Container mount test for the onboarding step 05 — "Strategy, leadership &
// your roadmap" (design scene onbstrat). In the redesign this is the FINAL
// step: it captures free-text priorities (→ companies.okrs) + a roadmap-doc
// upload (POST /v1/company/roadmap-doc), then COMPLETES onboarding — kicks the
// first brief, calls completeOnboarding, and enters the app at /brief.
//
// Covers: "Finish setup" persists priorities + completes onboarding + redirect,
// "Skip" completes without persisting priorities, the roadmap-doc upload calls
// the real API + shows the "uploaded" confirmation, and a failed upload surfaces
// a non-blocking notice without halting the step.
//
// Matchers: native DOM only.
import * as React from "react"
import { act, cleanup, fireEvent, render, screen } from "@testing-library/react"
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest"

;(globalThis as typeof globalThis & { React?: typeof React }).React = React

const authMock = vi.fn()
const onboardingMock = vi.fn()
const routerMock = { push: vi.fn(), replace: vi.fn() }
const completeOnboardingMock = vi.fn()
const updateWorkspaceMock = vi.fn()
const roadmapUploadMock = vi.fn()

vi.mock("../../../../lib/auth", () => ({ useAuth: () => authMock() }))
vi.mock("../../../../context/OnboardingContext", () => ({
  useOnboarding: () => onboardingMock(),
}))
vi.mock("../../../../context/ContentContext", () => ({
  useContent: () => ({ setContent: vi.fn() }),
}))
vi.mock("next/navigation", () => ({ useRouter: () => routerMock }))
vi.mock("../../../../lib/onboarding/store", () => ({
  completeOnboarding: (...a: unknown[]) => completeOnboardingMock(...a),
  updateWorkspace: (...a: unknown[]) => updateWorkspaceMock(...a),
}))
vi.mock("../../../../lib/workspace-brief", () => ({
  ensureDatasetForWorkspace: vi.fn().mockResolvedValue(undefined),
  seedWorkspaceContextFiles: vi.fn().mockResolvedValue(undefined),
  fetchBriefWhenReady: vi.fn().mockResolvedValue(null),
  startBriefGeneration: vi.fn().mockResolvedValue(undefined),
}))
vi.mock("../../../../lib/brief-adapter", () => ({ briefToContentPatch: () => ({}) }))
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

beforeEach(() => {
  authMock.mockReturnValue({ kind: "authed", user: { id: "u-1" }, session: {} })
})
afterEach(() => {
  cleanup()
  vi.clearAllMocks()
})

describe("Strategy (onboarding step 05 — completes onboarding)", () => {
  it("renders the strategy heading + roadmap-doc upload affordance", () => {
    onboardingMock.mockReturnValue(makeOnboardingCtx())
    render(React.createElement(Strategy))
    expect(screen.getByText(/your roadmap/i)).not.toBeNull()
    expect(document.querySelector('[data-field="roadmap-doc"]')).not.toBeNull()
  })

  it("'Finish setup' persists priorities, completes onboarding, enters /brief", async () => {
    onboardingMock.mockReturnValue(makeOnboardingCtx())
    updateWorkspaceMock.mockResolvedValue(undefined)
    completeOnboardingMock.mockResolvedValue(undefined)

    render(React.createElement(Strategy))
    const ta = document.querySelector("textarea") as HTMLTextAreaElement
    fireEvent.change(ta, { target: { value: "Ship reconciliation v2 this half." } })

    const finishBtn = Array.from(document.querySelectorAll("button")).find((b) =>
      /finish setup/i.test(b.textContent ?? ""),
    ) as HTMLButtonElement
    await act(async () => {
      finishBtn.click()
    })

    expect(updateWorkspaceMock).toHaveBeenCalledTimes(1)
    const [, patch] = updateWorkspaceMock.mock.calls[0] as [string, Record<string, unknown>]
    expect(patch.okrs).toBe("Ship reconciliation v2 this half.")
    expect(completeOnboardingMock).toHaveBeenCalledWith("ws-1", "u-1")
    expect(routerMock.replace).toHaveBeenCalledWith("/brief")
  })

  it("'Skip' completes onboarding without persisting priorities", async () => {
    onboardingMock.mockReturnValue(makeOnboardingCtx())
    completeOnboardingMock.mockResolvedValue(undefined)

    render(React.createElement(Strategy))
    const skip = Array.from(document.querySelectorAll("button")).find((b) =>
      /^skip$/i.test((b.textContent ?? "").trim()),
    ) as HTMLButtonElement
    await act(async () => {
      skip.click()
    })
    expect(updateWorkspaceMock).not.toHaveBeenCalled()
    expect(completeOnboardingMock).toHaveBeenCalledWith("ws-1", "u-1")
    expect(routerMock.replace).toHaveBeenCalledWith("/brief")
  })

  it("roadmap-doc upload calls the REAL API and shows the uploaded confirmation", async () => {
    onboardingMock.mockReturnValue(makeOnboardingCtx())
    roadmapUploadMock.mockResolvedValue({
      ok: true,
      filename: "roadmap.pdf",
      extracted_chars: 420,
      version: 1,
    })

    render(React.createElement(Strategy))
    const fileInput = document.querySelector(
      'input[type="file"]',
    ) as HTMLInputElement
    const file = new File(["roadmap"], "roadmap.pdf", { type: "application/pdf" })
    await act(async () => {
      fireEvent.change(fileInput, { target: { files: [file] } })
    })

    // The real endpoint is called with the picked file.
    expect(roadmapUploadMock).toHaveBeenCalledTimes(1)
    expect((roadmapUploadMock.mock.calls[0][0] as File).name).toBe("roadmap.pdf")
    // The design's "uploaded" confirmation state renders.
    expect(screen.getByText(/uploaded just now/i)).not.toBeNull()
    expect(
      document.querySelector('[data-field="roadmap-doc"][data-uploaded="true"]'),
    ).not.toBeNull()
  })

  it("roadmap-doc upload failure surfaces a non-blocking notice", async () => {
    onboardingMock.mockReturnValue(makeOnboardingCtx())
    roadmapUploadMock.mockRejectedValue(new Error("network error"))

    render(React.createElement(Strategy))
    const fileInput = document.querySelector(
      'input[type="file"]',
    ) as HTMLInputElement
    const file = new File(["roadmap"], "roadmap.pdf", { type: "application/pdf" })
    await act(async () => {
      fireEvent.change(fileInput, { target: { files: [file] } })
    })

    expect(roadmapUploadMock).toHaveBeenCalledTimes(1)
    // A friendly notice renders and the step is NOT blocked.
    expect(screen.getByText(/won't block setup/i)).not.toBeNull()
    expect(
      document.querySelector('[data-field="roadmap-doc"][data-uploaded="true"]'),
    ).toBeNull()
    // Finish setup still works after a failed upload.
    const finishBtn = Array.from(document.querySelectorAll("button")).find((b) =>
      /finish setup/i.test(b.textContent ?? ""),
    ) as HTMLButtonElement
    expect((finishBtn as HTMLButtonElement).disabled).toBe(false)
  })
})
