// @vitest-environment jsdom
//
// Container mount test for onboarding step 06 — "Your team" (registration
// spec 2026-07, Team section). Covers: the scope input + prioritization-
// framework select (options from PRIORITIZATION_FRAMEWORKS) render; COMPANY
// accounts are blocked on an empty scope (error, no persistence, no
// navigation); a filled scope + framework persists via updateWorkspace
// (team_scope + prioritization_framework + onboarding_step 7) and routes to
// /onboarding/strategy; the optional disclosure reveals the invite inputs and
// a queued invite is sent through teamApi.invite on Continue; PERSONAL
// accounts continue with everything empty and record the skipped fields.
//
// Matchers: native DOM only (no @testing-library/jest-dom).
import * as React from "react"
import { act, cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react"
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest"

;(globalThis as typeof globalThis & { React?: typeof React }).React = React

const authMock = vi.fn()
const onboardingMock = vi.fn()
const routerMock = { push: vi.fn(), replace: vi.fn() }
const updateWorkspaceMock = vi.fn()
const saveBriefDayMock = vi.fn()
const markSkippedMock = vi.fn()
const inviteMock = vi.fn()

vi.mock("../../../../lib/auth", () => ({ useAuth: () => authMock() }))
vi.mock("../../../../context/OnboardingContext", () => ({
  useOnboarding: () => onboardingMock(),
}))
vi.mock("next/navigation", () => ({ useRouter: () => routerMock }))
vi.mock("../../../../lib/onboarding/store", () => ({
  updateWorkspace: (...a: unknown[]) => updateWorkspaceMock(...a),
  saveNotificationBriefDay: (...a: unknown[]) => saveBriefDayMock(...a),
  markSkippedFields: (...a: unknown[]) => markSkippedMock(...a),
}))
vi.mock("../../../../lib/teamApi", () => ({
  teamApi: { invite: (...a: unknown[]) => inviteMock(...a) },
}))
vi.mock("../../../../lib/onboarding/useFormDraft", () => ({
  saveDraft: vi.fn(),
  loadDraft: () => null,
  clearDraft: vi.fn(),
}))

import { TeamStep } from "../TeamStep"
import { PRIORITIZATION_FRAMEWORKS } from "../../../../lib/onboarding/types"
import { makeWorkspace, makeOnboardingCtx, makeProfile } from "./fixtures"

function mount(accountType: "company" | "personal" = "company") {
  onboardingMock.mockReturnValue(
    makeOnboardingCtx({
      workspace: makeWorkspace({ onboarding_step: 6 }),
      profile: makeProfile({ account_type: accountType }),
    }),
  )
  return render(React.createElement(TeamStep))
}

function scopeInput(): HTMLInputElement {
  return document.querySelector('input[placeholder="e.g. notifications"]') as HTMLInputElement
}

function frameworkSelect(): HTMLSelectElement {
  return document.querySelector(
    'select[aria-label="Prioritization framework"]',
  ) as HTMLSelectElement
}

function continueBtn(): HTMLButtonElement {
  return Array.from(document.querySelectorAll("button")).find((b) =>
    /^continue$/i.test((b.textContent ?? "").trim()),
  ) as HTMLButtonElement
}

function openDisclosure() {
  fireEvent.click(screen.getByText("Invite teammates & pick your brief day"))
}

beforeEach(() => {
  authMock.mockReturnValue({ kind: "authed", user: { id: "u-1" }, session: {} })
})
afterEach(() => {
  cleanup()
  vi.clearAllMocks()
})

describe("TeamStep (onboarding step 06 — scope + framework + invites)", () => {
  it("renders the scope input and the framework select with the PRIORITIZATION_FRAMEWORKS options", () => {
    mount("company")
    expect(scopeInput()).not.toBeNull()
    const sel = frameworkSelect()
    expect(sel).not.toBeNull()
    const options = Array.from(sel.options)
    // Placeholder first, then the framework vocabulary in order.
    expect(options[0].value).toBe("")
    expect(options.slice(1).map((o) => o.value)).toEqual(
      PRIORITIZATION_FRAMEWORKS.map((f) => f.value),
    )
    expect(options.slice(1).map((o) => o.textContent)).toEqual(
      PRIORITIZATION_FRAMEWORKS.map((f) => f.label),
    )
  })

  it("COMPANY: Continue with an empty scope shows an error and does NOT persist or navigate", async () => {
    mount("company")
    await act(async () => {
      continueBtn().click()
    })
    expect(screen.getByText("Name the product area this team owns.")).not.toBeNull()
    expect(updateWorkspaceMock).not.toHaveBeenCalled()
    expect(routerMock.push).not.toHaveBeenCalled()
  })

  it("COMPANY: a filled scope + framework persists and routes to the strategy step", async () => {
    updateWorkspaceMock.mockResolvedValue(makeWorkspace({ onboarding_step: 7 }))
    mount("company")

    fireEvent.change(scopeInput(), { target: { value: "notifications" } })
    fireEvent.change(frameworkSelect(), { target: { value: "rice" } })
    await act(async () => {
      continueBtn().click()
    })

    await waitFor(() => {
      expect(routerMock.push).toHaveBeenCalledWith("/onboarding/strategy")
    })
    expect(updateWorkspaceMock).toHaveBeenCalledWith("ws-1", {
      team_scope: "notifications",
      prioritization_framework: "rice",
      onboarding_step: 7,
    })
    // No brief day was picked, so the notification settings stay untouched.
    expect(saveBriefDayMock).not.toHaveBeenCalled()
    expect(markSkippedMock).not.toHaveBeenCalled()
    expect(inviteMock).not.toHaveBeenCalled()
  })

  it("the disclosure opens to reveal the invite inputs; a queued invite sends via teamApi.invite on Continue", async () => {
    updateWorkspaceMock.mockResolvedValue(makeWorkspace({ onboarding_step: 7 }))
    inviteMock.mockResolvedValue({ id: "inv-1", email: "teammate@acme.com", role: "member" })
    mount("company")

    // Hidden until the disclosure opens.
    expect(document.querySelector('input[aria-label="Teammate email"]')).toBeNull()
    openDisclosure()
    const emailInput = document.querySelector(
      'input[aria-label="Teammate email"]',
    ) as HTMLInputElement
    expect(emailInput).not.toBeNull()
    expect(document.querySelector('select[aria-label="Teammate role"]')).not.toBeNull()

    // Queue an invite chip.
    fireEvent.change(emailInput, { target: { value: "teammate@acme.com" } })
    const addBtn = Array.from(document.querySelectorAll("button")).find(
      (b) => (b.textContent ?? "").trim() === "Add",
    ) as HTMLButtonElement
    fireEvent.click(addBtn)
    expect(screen.getByText(/teammate@acme\.com · member/)).not.toBeNull()

    fireEvent.change(scopeInput(), { target: { value: "notifications" } })
    fireEvent.change(frameworkSelect(), { target: { value: "goal-based" } })
    await act(async () => {
      continueBtn().click()
    })

    await waitFor(() => {
      expect(routerMock.push).toHaveBeenCalledWith("/onboarding/strategy")
    })
    expect(inviteMock).toHaveBeenCalledWith("teammate@acme.com", "member")
  })

  it("PERSONAL: continues with everything empty and records the skipped fields", async () => {
    updateWorkspaceMock.mockResolvedValue(makeWorkspace({ onboarding_step: 7 }))
    markSkippedMock.mockResolvedValue(undefined)
    mount("personal")

    await act(async () => {
      continueBtn().click()
    })

    await waitFor(() => {
      expect(routerMock.push).toHaveBeenCalledWith("/onboarding/strategy")
    })
    expect(updateWorkspaceMock).toHaveBeenCalledWith("ws-1", {
      team_scope: null,
      prioritization_framework: null,
      onboarding_step: 7,
    })
    expect(markSkippedMock).toHaveBeenCalledWith("u-1", [
      "team_scope",
      "prioritization_framework",
    ])
  })

  it("shows the loading shell while the workspace is loading", () => {
    onboardingMock.mockReturnValue(makeOnboardingCtx({ loading: true, workspace: null }))
    render(React.createElement(TeamStep))
    expect(screen.getByText("Loading…")).not.toBeNull()
  })

  it("redirects to step 1 from an EFFECT (never during render) when there is no workspace", () => {
    onboardingMock.mockReturnValue(makeOnboardingCtx({ workspace: null }))

    const errors: unknown[] = []
    const spy = vi
      .spyOn(console, "error")
      .mockImplementation((...args) => errors.push(args[0]))
    render(React.createElement(TeamStep))
    spy.mockRestore()

    expect(routerMock.replace).toHaveBeenCalledWith("/onboarding/company")
    expect(screen.getByText("Loading…")).not.toBeNull()
    const sideEffectInRender = errors
      .map(String)
      .filter((m) => /while rendering a different component|Cannot update a component/.test(m))
    expect(sideEffectInRender).toEqual([])
  })
})
