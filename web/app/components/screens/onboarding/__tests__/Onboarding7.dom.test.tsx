// @vitest-environment jsdom
//
// Container-level mount test for onboarding step 07 — "Introducing your AI
// coworkers." Mounts the real default container under jsdom with mocked
// onboarding/router and the coworkers network client so a render-time throw
// is caught — the View-pattern tests never exercise the stateful container
// and miss the production "Application error: a client-side exception has
// occurred".
//
// Matchers: native DOM only (no @testing-library/jest-dom).
import * as React from "react"
import { cleanup, render, screen } from "@testing-library/react"
import { afterEach, describe, expect, it, vi } from "vitest"

;(globalThis as typeof globalThis & { React?: typeof React }).React = React

const onboardingMock = vi.fn()
const routerMock = { push: vi.fn(), replace: vi.fn() }

vi.mock("../../../../context/OnboardingContext", () => ({
  useOnboarding: () => onboardingMock(),
}))
vi.mock("next/navigation", () => ({ useRouter: () => routerMock }))
vi.mock("../../../../lib/onboarding/store", () => ({
  advanceOnboardingStep: vi.fn(),
}))
// Keep the pure helpers (COWORKERS, emptyCoworkerNames, canLaunchWorkspace)
// real; only stub the network client so the mount is offline.
vi.mock("../../../../lib/onboarding/coworkersApi", async (importOriginal) => {
  const actual = await importOriginal<
    typeof import("../../../../lib/onboarding/coworkersApi")
  >()
  return {
    ...actual,
    coworkersApi: { get: vi.fn().mockResolvedValue({}), put: vi.fn() },
  }
})

import { Onboarding7 } from "../Onboarding7"
import type { WorkspaceCompany } from "../../../../lib/onboarding/types"

function makeWorkspace(over: Partial<WorkspaceCompany> = {}): WorkspaceCompany {
  return {
    id: "ws-1",
    slug: "acme",
    display_name: "Acme",
    product_description: null,
    product: null,
    industry: "B2B SaaS",
    stage: "Seed",
    business_type: "SaaS",
    team_size: null,
    engineering_capacity: null,
    pm_engineer_ratio: null,
    competitors: [],
    tech_stack: [],
    okrs: null,
    recent_decisions: null,
    dead_ends: [],
    biggest_risk: null,
    kpi_tree: { north_star: "", north_star_description: "", metrics: [] },
    feature_flags: {
      weekly_brief: true,
      on_demand_analysis: true,
      auto_prd_generation: true,
      engineer_agent: false,
      research_agent: false,
      on_call_agent: false,
      claude_code_handoff: false,
    },
    notification_settings: {},
    onboarding_step: 7,
    onboarding_completed_at: null,
    ...over,
  }
}

afterEach(() => {
  cleanup()
  vi.clearAllMocks()
})

describe("Onboarding7 (container) — mounts without crashing", () => {
  it("renders the coworkers step for a loaded workspace", () => {
    onboardingMock.mockReturnValue({
      loading: false,
      profile: null,
      workspace: makeWorkspace(),
      refresh: vi.fn(),
      setWorkspace: vi.fn(),
    })

    render(React.createElement(Onboarding7))
    expect(
      screen.getByText("Introducing your AI coworkers. Give them a name."),
    ).not.toBeNull()
  })

  it("shows the loading shell while the workspace is loading", () => {
    onboardingMock.mockReturnValue({
      loading: true,
      profile: null,
      workspace: null,
      refresh: vi.fn(),
      setWorkspace: vi.fn(),
    })
    render(React.createElement(Onboarding7))
    expect(screen.getByText("Loading…")).not.toBeNull()
  })

  it("redirects to step 1 from an EFFECT (never during render) when there is no workspace", () => {
    onboardingMock.mockReturnValue({
      loading: false,
      profile: null,
      workspace: null,
      refresh: vi.fn(),
      setWorkspace: vi.fn(),
    })

    const errors: unknown[] = []
    const spy = vi
      .spyOn(console, "error")
      .mockImplementation((...args) => errors.push(args[0]))
    render(React.createElement(Onboarding7))
    spy.mockRestore()

    expect(routerMock.replace).toHaveBeenCalledWith("/onboarding/1")
    expect(screen.getByText("Loading…")).not.toBeNull()
    const sideEffectInRender = errors
      .map(String)
      .filter((m) => /while rendering a different component|Cannot update a component/.test(m))
    expect(sideEffectInRender).toEqual([])
  })
})
