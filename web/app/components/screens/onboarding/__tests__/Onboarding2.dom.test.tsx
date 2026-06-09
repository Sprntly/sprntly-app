// @vitest-environment jsdom
//
// Container-level mount test for onboarding step 02 — "Define what success
// looks like." The sibling view tests render only pure sub-views via
// renderToStaticMarkup, so they never exercise the stateful Onboarding2
// container and miss container-level crashes (the production "Application
// error: a client-side exception has occurred"). This file mounts the real
// default container under jsdom with mocked auth/onboarding/router so a
// render-time throw is caught.
//
// Matchers: native DOM only (no @testing-library/jest-dom).
import * as React from "react"
import { cleanup, render, screen } from "@testing-library/react"
import { afterEach, describe, expect, it, vi } from "vitest"

;(globalThis as typeof globalThis & { React?: typeof React }).React = React

const authMock = vi.fn()
const onboardingMock = vi.fn()
const routerMock = { push: vi.fn(), replace: vi.fn() }

vi.mock("../../../../lib/auth", () => ({ useAuth: () => authMock() }))
vi.mock("../../../../context/OnboardingContext", () => ({
  useOnboarding: () => onboardingMock(),
}))
vi.mock("next/navigation", () => ({ useRouter: () => routerMock }))
vi.mock("../../../../lib/onboarding/store", () => ({
  markSkippedFields: vi.fn(),
  saveKpiTree: vi.fn(),
}))

import { Onboarding2 } from "../Onboarding2"
import type { WorkspaceCompany } from "../../../../lib/onboarding/types"

// A loaded workspace matching the real Workspace type, as the context yields
// it mid-onboarding (step 2).
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
    onboarding_step: 2,
    onboarding_completed_at: null,
    ...over,
  }
}

afterEach(() => {
  cleanup()
  vi.clearAllMocks()
})

describe("Onboarding2 (container) — mounts without crashing", () => {
  it("renders the KPI-tree step for a loaded workspace", () => {
    authMock.mockReturnValue({ kind: "authed", user: { id: "u-1" }, session: {} })
    onboardingMock.mockReturnValue({
      loading: false,
      profile: null,
      workspace: makeWorkspace(),
      refresh: vi.fn(),
      setWorkspace: vi.fn(),
    })

    render(React.createElement(Onboarding2))
    expect(screen.getByText("Define what success looks like")).not.toBeNull()
  })

  it("shows the loading shell while the workspace is loading", () => {
    authMock.mockReturnValue({ kind: "loading" })
    onboardingMock.mockReturnValue({
      loading: true,
      profile: null,
      workspace: null,
      refresh: vi.fn(),
      setWorkspace: vi.fn(),
    })
    render(React.createElement(Onboarding2))
    expect(screen.getByText("Loading…")).not.toBeNull()
  })

  it("redirects to step 1 from an EFFECT (never during render) when there is no workspace", () => {
    // Regression for the client-side exception: the prior code called
    // router.replace() during render when loading had finished with no
    // workspace. Navigating as a render side-effect throws in React 19
    // (update-while-rendering) and surfaces as the production error boundary.
    // The fix moves the redirect into an effect and renders the loading shell
    // meanwhile — so this mount stays crash-free.
    authMock.mockReturnValue({ kind: "authed", user: { id: "u-1" }, session: {} })
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
    render(React.createElement(Onboarding2))
    spy.mockRestore()

    expect(routerMock.replace).toHaveBeenCalledWith("/onboarding/1")
    expect(screen.getByText("Loading…")).not.toBeNull()
    const sideEffectInRender = errors
      .map(String)
      .filter((m) => /while rendering a different component|Cannot update a component/.test(m))
    expect(sideEffectInRender).toEqual([])
  })
})
