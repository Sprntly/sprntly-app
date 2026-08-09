// @vitest-environment jsdom
//
// Container mount test for the onboarding "api-key" step (step 04) — pick the
// AI provider and collect the company's own key for it BEFORE connectors.
//
// Covers: a valid key saves via the backend then advances to product; a key
// that doesn't match the chosen provider is rejected inline (no save); picking
// OpenAI persists immediately and re-targets the key field; the step is
// OPTIONAL for EVERYONE (restored 2026-07-19) — a skip link is always shown,
// Continue is enabled with an empty field, and an empty Continue skips without
// saving.
import * as React from "react"
import { act, cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react"
import { afterEach, describe, expect, it, vi } from "vitest"

;(globalThis as typeof globalThis & { React?: typeof React }).React = React

const authMock = vi.fn()
const onboardingMock = vi.fn()
const routerMock = { push: vi.fn(), replace: vi.fn() }
const advanceStepMock = vi.fn()
const markSkippedMock = vi.fn()
const getLlmConfigMock = vi.fn()
const setLlmKeyMock = vi.fn()
const setLlmProviderMock = vi.fn()

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
    getLlmConfig: (...a: unknown[]) => getLlmConfigMock(...a),
    setLlmKey: (...a: unknown[]) => setLlmKeyMock(...a),
    setLlmProvider: (...a: unknown[]) => setLlmProviderMock(...a),
  },
  ApiError: class ApiError extends Error {},
  apiErrorMessage: () => "err",
}))

const NO_KEYS = {
  anthropic: { configured: false, masked: null },
  openai: { configured: false, masked: null },
}

import { ApiKey } from "../ApiKey"
import { makeOnboardingCtx, makeWorkspace } from "./fixtures"

afterEach(() => {
  cleanup()
  vi.clearAllMocks()
})

function mount() {
  authMock.mockReturnValue({ kind: "authed", user: { id: "u-1" }, session: {} })
  advanceStepMock.mockResolvedValue(makeWorkspace())
  getLlmConfigMock.mockResolvedValue({ provider: "anthropic", providers: NO_KEYS })
  setLlmKeyMock.mockResolvedValue({ configured: true, masked: "sk-ant-…WXYZ" })
  setLlmProviderMock.mockResolvedValue({ provider: "openai", providers: NO_KEYS })
  return render(React.createElement(ApiKey))
}

function keyInput() {
  return document.querySelector('input[type="password"]') as HTMLInputElement
}

describe("ApiKey (onboarding step 04 — optional provider + key)", () => {
  it("saves a valid key via the backend, then advances to product", async () => {
    onboardingMock.mockReturnValue(makeOnboardingCtx({ workspace: makeWorkspace() }))
    mount()
    await act(async () => {
      fireEvent.change(keyInput(), { target: { value: "sk-ant-abcdef123456" } })
    })
    await act(async () => {
      fireEvent.click(screen.getByRole("button", { name: /continue/i }))
    })
    await waitFor(() =>
      expect(setLlmKeyMock).toHaveBeenCalledWith("sk-ant-abcdef123456", "anthropic"),
    )
    expect(advanceStepMock).toHaveBeenCalledWith("ws-1", 5)
    expect(routerMock.push).toHaveBeenCalledWith("/onboarding/product")
  })

  it("rejects a key that doesn't match the chosen provider, without saving", async () => {
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

  it("is OPTIONAL for everyone — shows a skip link and Continue is enabled while empty", () => {
    onboardingMock.mockReturnValue(
      makeOnboardingCtx({ workspace: makeWorkspace({ use_platform_key: false }) }),
    )
    mount()
    expect(screen.getByText(/skip for now/i)).not.toBeNull()
    const cont = screen.getByRole("button", { name: /continue/i }) as HTMLButtonElement
    expect(cont.disabled).toBe(false)
  })

  it("an empty Continue skips (marks api_key skipped, no save) and advances to product", async () => {
    onboardingMock.mockReturnValue(makeOnboardingCtx({ workspace: makeWorkspace() }))
    mount()
    await act(async () => {
      fireEvent.click(screen.getByRole("button", { name: /continue/i }))
    })
    await waitFor(() => expect(markSkippedMock).toHaveBeenCalledWith("u-1", ["api_key"]))
    expect(setLlmKeyMock).not.toHaveBeenCalled()
    expect(advanceStepMock).toHaveBeenCalledWith("ws-1", 5)
    expect(routerMock.push).toHaveBeenCalledWith("/onboarding/product")
  })

  it("the footer skip link advances without saving", async () => {
    onboardingMock.mockReturnValue(makeOnboardingCtx({ workspace: makeWorkspace() }))
    mount()
    const skip = screen.getByText(/skip for now/i)
    await act(async () => {
      fireEvent.click(skip)
    })
    await waitFor(() => expect(markSkippedMock).toHaveBeenCalledWith("u-1", ["api_key"]))
    expect(advanceStepMock).toHaveBeenCalledWith("ws-1", 5)
    expect(routerMock.push).toHaveBeenCalledWith("/onboarding/product")
    expect(setLlmKeyMock).not.toHaveBeenCalled()
  })

  it("offers both providers, with Claude selected by default", async () => {
    onboardingMock.mockReturnValue(makeOnboardingCtx({ workspace: makeWorkspace() }))
    mount()
    await waitFor(() => expect(getLlmConfigMock).toHaveBeenCalled())

    const claude = screen.getByRole("radio", { name: /Claude/ })
    const openai = screen.getByRole("radio", { name: /OpenAI/ })
    expect(claude.getAttribute("aria-checked")).toBe("true")
    expect(openai.getAttribute("aria-checked")).toBe("false")
  })

  it("picking OpenAI persists the choice immediately and re-targets the key field", async () => {
    // Saved on pick, not deferred to Continue: someone who chooses OpenAI and
    // then skips the key must still end up on the provider they chose.
    onboardingMock.mockReturnValue(makeOnboardingCtx({ workspace: makeWorkspace() }))
    mount()
    await waitFor(() => expect(getLlmConfigMock).toHaveBeenCalled())

    await act(async () => {
      fireEvent.click(screen.getByRole("radio", { name: /OpenAI/ }))
    })
    await waitFor(() => expect(setLlmProviderMock).toHaveBeenCalledWith("openai"))
    expect(screen.getByLabelText(/OpenAI API key/i)).not.toBeNull()

    await act(async () => {
      fireEvent.change(keyInput(), { target: { value: "sk-proj-abcdef123456" } })
    })
    await act(async () => {
      fireEvent.click(screen.getByRole("button", { name: /continue/i }))
    })
    await waitFor(() =>
      expect(setLlmKeyMock).toHaveBeenCalledWith("sk-proj-abcdef123456", "openai"),
    )
  })

  it("rejects a Claude key pasted into the OpenAI field", async () => {
    // `sk-ant-` also starts with `sk-`, so a bare prefix check would accept it.
    onboardingMock.mockReturnValue(makeOnboardingCtx({ workspace: makeWorkspace() }))
    mount()
    await waitFor(() => expect(getLlmConfigMock).toHaveBeenCalled())

    await act(async () => {
      fireEvent.click(screen.getByRole("radio", { name: /OpenAI/ }))
    })
    await act(async () => {
      fireEvent.change(keyInput(), { target: { value: "sk-ant-wrongfield" } })
    })
    await act(async () => {
      fireEvent.click(screen.getByRole("button", { name: /continue/i }))
    })
    expect(screen.getByRole("alert").textContent).toMatch(/OpenAI/)
    expect(setLlmKeyMock).not.toHaveBeenCalled()
    expect(advanceStepMock).not.toHaveBeenCalled()
  })

  it("restores a previously chosen provider when the step is revisited", async () => {
    onboardingMock.mockReturnValue(makeOnboardingCtx({ workspace: makeWorkspace() }))
    authMock.mockReturnValue({ kind: "authed", user: { id: "u-1" }, session: {} })
    advanceStepMock.mockResolvedValue(makeWorkspace())
    getLlmConfigMock.mockResolvedValue({
      provider: "openai",
      providers: {
        anthropic: { configured: false, masked: null },
        openai: { configured: true, masked: "sk-…WXYZ" },
      },
    })
    render(React.createElement(ApiKey))

    await waitFor(() =>
      expect(
        screen.getByRole("radio", { name: /OpenAI/ }).getAttribute("aria-checked"),
      ).toBe("true"),
    )
    // A saved key means Continue can proceed without re-entering it, and
    // WITHOUT marking the step skipped.
    expect(screen.getByLabelText(/already saved/i)).not.toBeNull()
    await act(async () => {
      fireEvent.click(screen.getByRole("button", { name: /continue/i }))
    })
    await waitFor(() => expect(advanceStepMock).toHaveBeenCalledWith("ws-1", 5))
    expect(markSkippedMock).not.toHaveBeenCalled()
  })

  it("Back returns to the connectors step", () => {
    onboardingMock.mockReturnValue(makeOnboardingCtx({ workspace: makeWorkspace() }))
    mount()
    fireEvent.click(screen.getByRole("button", { name: /back/i }))
    expect(routerMock.push).toHaveBeenCalledWith("/onboarding/connectors")
  })
})
