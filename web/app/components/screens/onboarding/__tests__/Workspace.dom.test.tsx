// @vitest-environment jsdom
//
// Container mount test for the onboarding step 02 — "Create your workspace"
// (design scene onbws). In the redesign this is a SLIM, EARLY, name-only step:
// it captures the (optional) workspace name and continues to connectors. It NO
// LONGER owns invites, the first-brief kickoff, or onboarding completion — those
// moved to Settings → Team and the final Strategy step respectively.
//
// Covers: name seeded from the company, the auth-card minimal layout, Continue
// persists a changed name + advances to connectors, Continue with an unchanged
// name only advances the step, and that there is NO invite UI / no completion.
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

vi.mock("../../../../context/OnboardingContext", () => ({
  useOnboarding: () => onboardingMock(),
}))
vi.mock("next/navigation", () => ({ useRouter: () => routerMock }))
vi.mock("../../../../lib/onboarding/store", () => ({
  advanceOnboardingStep: (...a: unknown[]) => advanceStepMock(...a),
  updateWorkspace: (...a: unknown[]) => updateWorkspaceMock(...a),
}))

import { Workspace } from "../Workspace"
import { makeOnboardingCtx, makeWorkspace } from "./fixtures"

afterEach(() => {
  cleanup()
  vi.clearAllMocks()
})

describe("Workspace (onboarding step 02 — name-only)", () => {
  it("renders the create-workspace heading + name seeded from the company", () => {
    onboardingMock.mockReturnValue(makeOnboardingCtx())
    render(React.createElement(Workspace))
    expect(screen.getByText(/create your/i)).not.toBeNull()
    const nameInput = document.querySelector(
      '[data-field="workspaceName"] input',
    ) as HTMLInputElement
    expect(nameInput.value).toBe("Acme")
  })

  it("uses the design's minimal auth-card layout (no numbered onb-shell chrome)", () => {
    onboardingMock.mockReturnValue(makeOnboardingCtx())
    const { container } = render(React.createElement(Workspace))
    expect(container.querySelector(".auth-card")).not.toBeNull()
    // No progress dots on this early auth-card step.
    expect(container.querySelector(".onb-dots")).toBeNull()
  })

  it("has NO invite UI and does NOT complete onboarding (those moved away)", () => {
    onboardingMock.mockReturnValue(makeOnboardingCtx())
    const { container } = render(React.createElement(Workspace))
    expect(container.querySelector(".invite-row")).toBeNull()
    const labels = Array.from(container.querySelectorAll("button")).map((b) =>
      (b.textContent ?? "").trim(),
    )
    expect(labels.some((l) => /add another/i.test(l))).toBe(false)
    expect(labels.some((l) => /create workspace & enter|finish/i.test(l))).toBe(false)
  })

  it("Continue persists a changed name + advances to connectors", async () => {
    onboardingMock.mockReturnValue(makeOnboardingCtx())
    updateWorkspaceMock.mockResolvedValue(makeWorkspace({ display_name: "Acme HQ" }))

    render(React.createElement(Workspace))
    const nameInput = document.querySelector(
      '[data-field="workspaceName"] input',
    ) as HTMLInputElement
    fireEvent.change(nameInput, { target: { value: "Acme HQ" } })

    const continueBtn = Array.from(document.querySelectorAll("button")).find((b) =>
      /continue/i.test(b.textContent ?? ""),
    ) as HTMLButtonElement
    await act(async () => {
      continueBtn.click()
    })

    // Name + resume marker persisted in the same write; advance never doubled.
    expect(updateWorkspaceMock).toHaveBeenCalledWith("ws-1", {
      display_name: "Acme HQ",
      onboarding_step: 3,
    })
    expect(advanceStepMock).not.toHaveBeenCalled()
    expect(routerMock.push).toHaveBeenCalledWith("/onboarding/connectors")
  })

  it("Continue with an unchanged name only advances the step (no name write)", async () => {
    onboardingMock.mockReturnValue(makeOnboardingCtx())
    advanceStepMock.mockResolvedValue(makeWorkspace({ onboarding_step: 3 }))

    render(React.createElement(Workspace))
    const continueBtn = Array.from(document.querySelectorAll("button")).find((b) =>
      /continue/i.test(b.textContent ?? ""),
    ) as HTMLButtonElement
    await act(async () => {
      continueBtn.click()
    })

    expect(updateWorkspaceMock).not.toHaveBeenCalled()
    expect(advanceStepMock).toHaveBeenCalledWith("ws-1", 3)
    expect(routerMock.push).toHaveBeenCalledWith("/onboarding/connectors")
  })

  it("Back routes to the business-info page", () => {
    onboardingMock.mockReturnValue(makeOnboardingCtx())
    render(React.createElement(Workspace))
    const back = Array.from(document.querySelectorAll("a")).find((a) =>
      /^back$/i.test((a.textContent ?? "").trim()),
    ) as HTMLAnchorElement
    fireEvent.click(back)
    expect(routerMock.push).toHaveBeenCalledWith("/onboarding/business-info")
  })
})
