// @vitest-environment jsdom
//
// Container mount test for the onboarding step 05 — "Create your workspace"
// (design scene onbws). The final step: workspace name + invites, then it
// COMPLETES onboarding, kicks the first brief, and enters the app at /brief.
//
// Covers: name persist + invites sent + completeOnboarding + redirect, invite
// rows add/remove, and that a failed invite send does NOT block completion.
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
const sendInvitesMock = vi.fn()
const updateWorkspaceMock = vi.fn()

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
  sendWorkspaceInvites: (...a: unknown[]) => sendInvitesMock(...a),
  updateWorkspace: (...a: unknown[]) => updateWorkspaceMock(...a),
}))
vi.mock("../../../../lib/workspace-brief", () => ({
  ensureDatasetForWorkspace: vi.fn().mockResolvedValue(undefined),
  seedWorkspaceContextFiles: vi.fn().mockResolvedValue(undefined),
  fetchBriefWhenReady: vi.fn().mockResolvedValue(null),
  startBriefGeneration: vi.fn().mockResolvedValue(undefined),
}))
vi.mock("../../../../lib/brief-adapter", () => ({ briefToContentPatch: () => ({}) }))

import { Workspace } from "../Workspace"
import { makeOnboardingCtx, makeWorkspace } from "./fixtures"

beforeEach(() => {
  authMock.mockReturnValue({ kind: "authed", user: { id: "u-1" }, session: {} })
})
afterEach(() => {
  cleanup()
  vi.clearAllMocks()
})

describe("Workspace (onboarding step 05)", () => {
  it("renders the create-workspace heading + name seeded from the company", () => {
    onboardingMock.mockReturnValue(makeOnboardingCtx())
    render(React.createElement(Workspace))
    expect(screen.getByText(/create your/i)).not.toBeNull()
    const nameInput = document.querySelector(
      '[data-field="workspaceName"] input',
    ) as HTMLInputElement
    expect(nameInput.value).toBe("Acme")
  })

  it("Continue persists a changed name, sends invites, completes onboarding, enters /brief", async () => {
    onboardingMock.mockReturnValue(makeOnboardingCtx())
    updateWorkspaceMock.mockResolvedValue(makeWorkspace({ display_name: "Acme HQ" }))
    sendInvitesMock.mockResolvedValue(undefined)
    completeOnboardingMock.mockResolvedValue(undefined)

    render(React.createElement(Workspace))

    const nameInput = document.querySelector(
      '[data-field="workspaceName"] input',
    ) as HTMLInputElement
    fireEvent.change(nameInput, { target: { value: "Acme HQ" } })

    const emailInput = document.querySelector(
      '[data-field="invite-0"] input',
    ) as HTMLInputElement
    fireEvent.change(emailInput, { target: { value: "pm@acme.com" } })

    const finishBtn = Array.from(document.querySelectorAll("button")).find((b) =>
      /create workspace & enter/i.test(b.textContent ?? ""),
    ) as HTMLButtonElement
    await act(async () => {
      finishBtn.click()
    })

    expect(updateWorkspaceMock).toHaveBeenCalledWith("ws-1", { display_name: "Acme HQ" })
    expect(sendInvitesMock).toHaveBeenCalledTimes(1)
    const invites = sendInvitesMock.mock.calls[0][1] as { email: string }[]
    expect(invites[0].email).toBe("pm@acme.com")
    expect(completeOnboardingMock).toHaveBeenCalledWith("ws-1", "u-1")
    expect(routerMock.replace).toHaveBeenCalledWith("/brief")
  })

  it("completes even when invite sending fails (best-effort, never blocks)", async () => {
    onboardingMock.mockReturnValue(makeOnboardingCtx())
    sendInvitesMock.mockRejectedValue(new Error("invite service down"))
    completeOnboardingMock.mockResolvedValue(undefined)

    render(React.createElement(Workspace))
    const emailInput = document.querySelector(
      '[data-field="invite-0"] input',
    ) as HTMLInputElement
    fireEvent.change(emailInput, { target: { value: "pm@acme.com" } })

    const finishBtn = Array.from(document.querySelectorAll("button")).find((b) =>
      /create workspace & enter/i.test(b.textContent ?? ""),
    ) as HTMLButtonElement
    await act(async () => {
      finishBtn.click()
    })

    expect(completeOnboardingMock).toHaveBeenCalledWith("ws-1", "u-1")
    expect(routerMock.replace).toHaveBeenCalledWith("/brief")
  })

  it("does NOT send invites when no valid email is entered", async () => {
    onboardingMock.mockReturnValue(makeOnboardingCtx())
    completeOnboardingMock.mockResolvedValue(undefined)

    render(React.createElement(Workspace))
    const finishBtn = Array.from(document.querySelectorAll("button")).find((b) =>
      /create workspace & enter/i.test(b.textContent ?? ""),
    ) as HTMLButtonElement
    await act(async () => {
      finishBtn.click()
    })
    expect(sendInvitesMock).not.toHaveBeenCalled()
    expect(completeOnboardingMock).toHaveBeenCalledTimes(1)
  })

  it("can add another invite row", () => {
    onboardingMock.mockReturnValue(makeOnboardingCtx())
    render(React.createElement(Workspace))
    expect(document.querySelectorAll(".invite-row").length).toBe(1)
    const addBtn = Array.from(document.querySelectorAll("button")).find((b) =>
      /add another/i.test(b.textContent ?? ""),
    ) as HTMLButtonElement
    fireEvent.click(addBtn)
    expect(document.querySelectorAll(".invite-row").length).toBe(2)
  })
})
