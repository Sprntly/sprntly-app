// @vitest-environment jsdom
//
// Container mount test for onboarding step 05 — "Your team" (v6 screenshot
// spec 2026-07-17). The step is now team name* + scope of work* ONLY: the
// prioritization framework moved to the metrics step, teammate invites to the
// invite step (08), and the weekly-brief day to Settings. The team name is a
// COMPANY field (companies.team_name), not the workspaces row.
//
// Covers: the two fields render (no framework select, no invite disclosure);
// empty fields block Continue (error, no persistence, no navigation); a valid
// Continue persists via updateWorkspace (team_name + team_scope +
// onboarding_step 6) and routes to /onboarding/strategy; Back goes to the
//
// Matchers: native DOM only (no @testing-library/jest-dom).
import * as React from "react"
import { act, cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react"
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest"

;(globalThis as typeof globalThis & { React?: typeof React }).React = React

const authMock = vi.fn()
const onboardingMock = vi.fn()
const routerMock = { push: vi.fn(), replace: vi.fn() }
const advanceStepMock = vi.fn()
const updateWorkspaceMock = vi.fn()

vi.mock("../../../../lib/auth", () => ({ useAuth: () => authMock() }))
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

import { TeamStep } from "../TeamStep"
import { ONBOARDING_STEP_COUNT } from "../../../../lib/onboarding/types"
import { makeWorkspace, makeOnboardingCtx } from "./fixtures"

function mount(workspace = makeWorkspace({ onboarding_step: 5 })) {
  onboardingMock.mockReturnValue(makeOnboardingCtx({ workspace }))
  return render(React.createElement(TeamStep))
}

function nameInput(): HTMLInputElement {
  return document.querySelector(
    'input[placeholder="e.g. Nutrition & Sleep"]',
  ) as HTMLInputElement
}

function scopeTextarea(): HTMLTextAreaElement {
  return document.querySelector('[data-field="teamScope"] textarea') as HTMLTextAreaElement
}

function continueBtn(): HTMLButtonElement {
  return Array.from(document.querySelectorAll("button")).find((b) =>
    /^next$/i.test((b.textContent ?? "").trim()),
  ) as HTMLButtonElement
}

beforeEach(() => {
  authMock.mockReturnValue({ kind: "authed", user: { id: "u-1" }, session: {} })
})
afterEach(() => {
  cleanup()
  vi.clearAllMocks()
})

describe("TeamStep (onboarding step 05 — team name* + scope*)", () => {
  it("renders ONLY the team-name input and scope textarea — no framework, no invites, no brief day", () => {
    mount()
    expect(nameInput()).not.toBeNull()
    expect(scopeTextarea()).not.toBeNull()
    // Both fields are starred.
    expect(
      (document.querySelector('[data-field="teamName"]') as HTMLElement).querySelector(".req"),
    ).not.toBeNull()
    expect(
      (document.querySelector('[data-field="teamScope"]') as HTMLElement).querySelector(".req"),
    ).not.toBeNull()
    // The framework select moved to the metrics step, invites to step 08, the
    // brief day to Settings.
    expect(
      document.querySelector('select[aria-label="Prioritization framework"]'),
    ).toBeNull()
    expect(document.querySelector('input[aria-label="Teammate email"]')).toBeNull()
    expect(screen.queryByText(/brief day/i)).toBeNull()
  })

  it("seeds from the saved workspace", () => {
    mount(
      makeWorkspace({
        onboarding_step: 5,
        team_name: "Growth",
        team_scope: "Activation & onboarding funnels",
      }),
    )
    expect(nameInput().value).toBe("Growth")
    expect(scopeTextarea().value).toBe("Activation & onboarding funnels")
  })

  it("Continue with empty fields shows errors and does NOT persist or navigate", async () => {
    mount()
    await act(async () => {
      continueBtn().click()
    })
    expect(screen.getByText("Name your team.")).not.toBeNull()
    expect(updateWorkspaceMock).not.toHaveBeenCalled()
    expect(routerMock.push).not.toHaveBeenCalled()
  })

  it("Continue with a name but no scope still blocks", async () => {
    mount()
    fireEvent.change(nameInput(), { target: { value: "Growth" } })
    await act(async () => {
      continueBtn().click()
    })
    expect(screen.getByText("Describe the area this team owns.")).not.toBeNull()
    expect(updateWorkspaceMock).not.toHaveBeenCalled()
    expect(routerMock.push).not.toHaveBeenCalled()
  })

  it("a valid Continue persists team_name + team_scope (step 6) and routes to strategy", async () => {
    updateWorkspaceMock.mockResolvedValue(makeWorkspace({ onboarding_step: 6 }))
    mount()

    fireEvent.change(nameInput(), { target: { value: "Growth" } })
    fireEvent.change(scopeTextarea(), { target: { value: "notifications" } })
    await act(async () => {
      continueBtn().click()
    })

    await waitFor(() => {
      expect(routerMock.push).toHaveBeenCalledWith("/onboarding/strategy")
    })
    expect(updateWorkspaceMock).toHaveBeenCalledWith("ws-1", {
      team_name: "Growth",
      team_scope: "notifications",
      onboarding_step: 6,
    })
    expect(advanceStepMock).not.toHaveBeenCalled()
  })

  it("Back routes to the connectors step", () => {
    mount()
    fireEvent.click(screen.getByText("Back").closest("button") as HTMLElement)
    expect(routerMock.push).toHaveBeenCalledWith("/onboarding/connectors")
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
