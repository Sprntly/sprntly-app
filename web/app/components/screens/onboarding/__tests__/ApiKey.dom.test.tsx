// @vitest-environment jsdom
//
// Container mount test for the onboarding "api-key" step — optionally collect
// the company's own Claude key BEFORE connectors.
//
// Covers: a valid key saves via the backend then advances to connectors; a
// non-anthropic key is rejected inline (no save); the step is always
// SKIPPABLE (workspaces without a key run on Sprntly's default account key),
// and Continue with an empty field advances while marking the step skipped.
import * as React from "react"
import { act, cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react"
import { afterEach, describe, expect, it, vi } from "vitest"

;(globalThis as typeof globalThis & { React?: typeof React }).React = React

const authMock = vi.fn()
const onboardingMock = vi.fn()
const routerMock = { push: vi.fn(), replace: vi.fn() }
const advanceStepMock = vi.fn()
const markSkippedMock = vi.fn()
const getLlmKeyMock = vi.fn()
const setLlmKeyMock = vi.fn()

vi.mock("../../../../lib/auth", () => ({ useAuth: () => authMock() }))
vi.mock("../../../../context/OnboardingContext", () => ({
  useOnboarding: () => onboardingMock(),
}))
vi.mock("next/navigation", () => ({ useRouter: () => routerMock }))
vi.mock("../../../../lib/onboarding/store", () => ({
  advanceOnboardingStep: (...a: unknown[]) => advanceStepMock(...a),
  markSkippedFields: (...a: unknown[]) => markSkippedMock(...a),
}))
vi.mock("../../../../lib/api", () => ({
  adminApi: {
    getLlmKey: (...a: unknown[]) => getLlmKeyMock(...a),
    setLlmKey: (...a: unknown[]) => setLlmKeyMock(...a),
  },
  ApiError: class ApiError extends Error {},
  apiErrorMessage: () => "err",
}))

import { ApiKey } from "../ApiKey"
import { makeOnboardingCtx, makeWorkspace } from "./fixtures"

afterEach(() => {
  cleanup()
  vi.clearAllMocks()
})

function mount() {
  authMock.mockReturnValue({ kind: "authed", user: { id: "u-1" }, session: {} })
  advanceStepMock.mockResolvedValue(makeWorkspace())
  getLlmKeyMock.mockResolvedValue({ configured: false, masked: null })
  setLlmKeyMock.mockResolvedValue({ configured: true, masked: "sk-ant-…WXYZ" })
  return render(React.createElement(ApiKey))
}

function keyInput() {
  return document.querySelector('input[type="password"]') as HTMLInputElement
}

describe("ApiKey (onboarding step 03 — Claude key)", () => {
  it("saves a valid key via the backend, then advances to connectors", async () => {
    onboardingMock.mockReturnValue(makeOnboardingCtx({ workspace: makeWorkspace() }))
    mount()
    await act(async () => {
      fireEvent.change(keyInput(), { target: { value: "sk-ant-abcdef123456" } })
    })
    await act(async () => {
      fireEvent.click(screen.getByRole("button", { name: /continue/i }))
    })
    await waitFor(() => expect(setLlmKeyMock).toHaveBeenCalledWith("sk-ant-abcdef123456"))
    expect(advanceStepMock).toHaveBeenCalledWith("ws-1", 4)
    expect(routerMock.push).toHaveBeenCalledWith("/onboarding/connectors")
  })

  it("rejects a non-anthropic key inline without saving", async () => {
    onboardingMock.mockReturnValue(makeOnboardingCtx({ workspace: makeWorkspace() }))
    mount()
    await act(async () => {
      fireEvent.change(keyInput(), { target: { value: "sk-openai-nope" } })
    })
    await act(async () => {
      fireEvent.click(screen.getByRole("button", { name: /continue/i }))
    })
    expect(screen.getByText(/sk-ant-/i)).not.toBeNull()
    expect(setLlmKeyMock).not.toHaveBeenCalled()
    expect(advanceStepMock).not.toHaveBeenCalled()
  })

  it("is skippable for every workspace (no key → Sprntly's default account key)", async () => {
    onboardingMock.mockReturnValue(
      makeOnboardingCtx({ workspace: makeWorkspace({ use_platform_key: false }) }),
    )
    mount()
    const skip = screen.getByText(/skip for now/i)
    await act(async () => {
      fireEvent.click(skip)
    })
    await waitFor(() => expect(markSkippedMock).toHaveBeenCalledWith("u-1", ["api_key"]))
    expect(advanceStepMock).toHaveBeenCalledWith("ws-1", 4)
    expect(routerMock.push).toHaveBeenCalledWith("/onboarding/connectors")
    expect(setLlmKeyMock).not.toHaveBeenCalled()
  })

  it("continues with an empty field, marking the step skipped", async () => {
    onboardingMock.mockReturnValue(makeOnboardingCtx({ workspace: makeWorkspace() }))
    mount()
    const cont = screen.getByRole("button", { name: /continue/i }) as HTMLButtonElement
    expect(cont.disabled).toBe(false)
    await act(async () => {
      fireEvent.click(cont)
    })
    await waitFor(() => expect(markSkippedMock).toHaveBeenCalledWith("u-1", ["api_key"]))
    expect(advanceStepMock).toHaveBeenCalledWith("ws-1", 4)
    expect(setLlmKeyMock).not.toHaveBeenCalled()
  })
})
