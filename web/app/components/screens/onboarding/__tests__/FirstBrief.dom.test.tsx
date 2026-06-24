// @vitest-environment jsdom
//
// Container-level mount + behavior tests for the onboarding first-brief step —
// the design-v4 placeholder/loading page (page 16) on OnboardingChrome. The
// page never previews brief content; it narrates generation with the
// `.gen-stages` checklist and AUTO-FORWARDS to /brief when the brief lands.
// Mounts the real container under jsdom with mocked auth/onboarding/content/
// router and the brief-generation client, covering: the generating state
// (stages + disabled Continue), the READY auto-forward (completeOnboarding +
// localStorage + router.replace("/brief"), exactly once), the existing-brief
// short-circuit, the failed state (Retry / Add sources / "Enter Sprntly
// anyway" → "/"), the completeOnboarding-failure manual fallback, and the
// loading-shell + redirect-in-effect guard.
//
// Matchers: native DOM only (no @testing-library/jest-dom).
import * as React from "react"
import { act, cleanup, fireEvent, render, screen } from "@testing-library/react"
import { afterEach, describe, expect, it, vi } from "vitest"

;(globalThis as typeof globalThis & { React?: typeof React }).React = React

const authMock = vi.fn()
const onboardingMock = vi.fn()
const routerMock = { push: vi.fn(), replace: vi.fn() }
const setContentMock = vi.fn()
const completeMock = vi.fn()
const patchMock = vi.fn()
const ensureMock = vi.fn()
const seedMock = vi.fn()
const fetchBriefMock = vi.fn()
const pollMock = vi.fn()
const startGenMock = vi.fn()

vi.mock("../../../../lib/auth", () => ({ useAuth: () => authMock() }))
vi.mock("../../../../context/OnboardingContext", () => ({
  useOnboarding: () => onboardingMock(),
}))
vi.mock("../../../../context/ContentContext", () => ({
  useContent: () => ({ setContent: setContentMock }),
}))
vi.mock("next/navigation", () => ({ useRouter: () => routerMock }))
// The "Add sources →" action renders a next/link; render it as a plain
// anchor so the mount needs no Next app-router context.
vi.mock("next/link", () => ({
  default: ({
    href,
    children,
    ...rest
  }: React.PropsWithChildren<{ href: string } & Record<string, unknown>>) =>
    React.createElement("a", { href, ...rest }, children),
}))
vi.mock("../../../../lib/onboarding/store", () => ({
  completeOnboarding: (...args: unknown[]) => completeMock(...args),
}))
vi.mock("../../../../lib/brief-adapter", () => ({
  briefToContentPatch: (...args: unknown[]) => patchMock(...args),
}))
// The brief-generation client runs from the mount effect; stub it so the
// mount is offline and deterministic.
vi.mock("../../../../lib/workspace-brief", () => ({
  ensureDatasetForWorkspace: (...args: unknown[]) => ensureMock(...args),
  seedWorkspaceContextFiles: (...args: unknown[]) => seedMock(...args),
  fetchBriefWhenReady: (...args: unknown[]) => fetchBriefMock(...args),
  pollBriefStatus: (...args: unknown[]) => pollMock(...args),
  startBriefGeneration: (...args: unknown[]) => startGenMock(...args),
}))

import { FirstBrief } from "../FirstBrief"
import { makeWorkspace, makeOnboardingCtx } from "./fixtures"

afterEach(() => {
  cleanup()
  window.localStorage.clear()
  // resetAllMocks (not clearAllMocks): mock implementations set via
  // mockResolvedValue must not leak across tests.
  vi.resetAllMocks()
})

const makeBrief = (n = 6) => ({
  week_label: "Week 23",
  insights: Array.from({ length: n }, (_, i) => ({ id: `i-${i}` })),
})

/** Happy-path pipeline: no existing brief → generate → poll ready → brief. */
function wireGeneratedBrief(brief = makeBrief()) {
  ensureMock.mockResolvedValue(undefined)
  seedMock.mockResolvedValue(undefined)
  fetchBriefMock.mockResolvedValueOnce(null).mockResolvedValueOnce(brief)
  startGenMock.mockResolvedValue(undefined)
  pollMock.mockResolvedValue({ status: "ready" })
  return brief
}

// Mounts the loaded container and lets the generation pipeline (kicked off
// from the mount effect) settle inside act().
async function mountLoaded(ctxOver: Record<string, unknown> = {}) {
  patchMock.mockReturnValue({ patched: true })
  authMock.mockReturnValue({ kind: "authed", user: { id: "u-1" }, session: {} })
  onboardingMock.mockReturnValue(
    makeOnboardingCtx({
      workspace: makeWorkspace({ onboarding_step: 4 }),
      ...ctxOver,
    }),
  )
  let utils!: ReturnType<typeof render>
  await act(async () => {
    utils = render(React.createElement(FirstBrief))
  })
  return utils
}

function continueButton(label: string): HTMLButtonElement {
  const btn = Array.from(document.querySelectorAll("button")).find((b) =>
    (b.textContent ?? "").includes(label),
  )
  expect(btn).toBeTruthy()
  return btn as HTMLButtonElement
}

describe("FirstBrief (container) — first brief", () => {
  it("renders the generating placeholder: v4 chrome, stage checklist, disabled Continue", async () => {
    ensureMock.mockResolvedValue(undefined)
    seedMock.mockResolvedValue(undefined)
    fetchBriefMock.mockResolvedValue(null)
    startGenMock.mockResolvedValue(undefined)
    pollMock.mockReturnValue(new Promise(() => {})) // never settles → stays generating
    const { container } = await mountLoaded()

    expect(container.querySelector(".onb-h")?.textContent).toBe(
      "Setting up your workspace.",
    )
    // OnboardingChrome shell, first-brief = numbered step 4 (last dot).
    expect(container.querySelector(".onb-shell")).not.toBeNull()
    expect(container.querySelector(".onb-dots")?.getAttribute("data-step")).toBe("4")
    // Old InterviewLayout shell + KPI preview + brief preview are gone.
    expect(container.querySelector(".interview-shell")).toBeNull()
    expect(container.querySelector(".ob-brief-preview")).toBeNull()

    // Three-stage checklist: seeding done, "Analyzing your sources" active,
    // composing pending.
    const stages = container.querySelectorAll(".gen-stages .gen-stage")
    expect(stages.length).toBe(3)
    expect(container.querySelector(".gen-stage.done")?.textContent).toContain(
      "Workspace context saved",
    )
    expect(container.querySelector(".gen-stage.active")?.textContent).toContain(
      "Analyzing your sources",
    )
    expect(container.querySelector(".gen-stage.pending")?.textContent).toContain(
      "Composing your first Weekly Brief",
    )

    // Continue is the disabled brief handoff; footer narrates generation.
    expect(continueButton("Open your Brief").disabled).toBe(true)
    expect(
      screen.getByText("Generating… your Brief opens as soon as it's ready"),
    ).not.toBeNull()

    // Workspace summary strip replaces the old KPI-tree pane.
    const strip = container.querySelector(".ws-strip")
    expect(strip?.textContent).toContain("Acme")
    expect(strip?.textContent).toContain("North star")
  })

  it("AUTO-FORWARDS when the brief lands: completeOnboarding + localStorage + replace(/brief), exactly once", async () => {
    const brief = wireGeneratedBrief()
    completeMock.mockResolvedValue(undefined)
    const utils = await mountLoaded()

    // The brief preloads ContentContext, but is never previewed here.
    expect(patchMock).toHaveBeenCalledWith(brief)
    expect(setContentMock).toHaveBeenCalledWith({ patched: true })

    expect(completeMock).toHaveBeenCalledWith("ws-1", "u-1")
    expect(window.localStorage.getItem("sprntly_active_company")).toBe("acme")
    expect(routerMock.replace).toHaveBeenCalledWith("/brief")

    // Fires exactly once, even across re-renders.
    await act(async () => {
      utils.rerender(React.createElement(FirstBrief))
    })
    expect(completeMock).toHaveBeenCalledTimes(1)
    expect(routerMock.replace).toHaveBeenCalledTimes(1)
  })

  it("the existing-brief short-circuit also auto-forwards without generating", async () => {
    ensureMock.mockResolvedValue(undefined)
    seedMock.mockResolvedValue(undefined)
    fetchBriefMock.mockResolvedValue(makeBrief(3)) // brief already exists
    completeMock.mockResolvedValue(undefined)
    await mountLoaded()

    expect(startGenMock).not.toHaveBeenCalled()
    expect(pollMock).not.toHaveBeenCalled()
    expect(completeMock).toHaveBeenCalledWith("ws-1", "u-1")
    expect(window.localStorage.getItem("sprntly_active_company")).toBe("acme")
    expect(routerMock.replace).toHaveBeenCalledWith("/brief")
    expect(routerMock.replace).toHaveBeenCalledTimes(1)
  })

  it("falls back to a manual 'Open your Brief' button when completeOnboarding fails on auto-forward", async () => {
    wireGeneratedBrief()
    completeMock.mockRejectedValueOnce(new Error("network down"))
    const { container } = await mountLoaded()

    // Auto-forward attempted once, failed — no navigation, no crash/loop.
    expect(completeMock).toHaveBeenCalledTimes(1)
    expect(routerMock.replace).not.toHaveBeenCalled()
    expect(screen.getByText("network down")).not.toBeNull()
    // Ready handoff tile still renders (metadata only, no brief content).
    expect(container.querySelector(".gen-ready")?.textContent).toContain(
      "Your Weekly Brief is waiting",
    )
    // And it tells the user about the recurring Monday-6am brief cadence.
    const cadence = container.querySelector(".gen-ready .brief-cadence")?.textContent ?? ""
    expect(cadence).toContain("every Monday at 6:00 AM")
    expect(cadence).toMatch(/your timezone:|your local time/)

    // The footer button is the manual fallback and is enabled.
    const btn = continueButton("Open your Brief")
    expect(btn.disabled).toBe(false)
    completeMock.mockResolvedValueOnce(undefined)
    await act(async () => {
      fireEvent.click(btn)
    })
    expect(completeMock).toHaveBeenCalledTimes(2)
    expect(window.localStorage.getItem("sprntly_active_company")).toBe("acme")
    expect(routerMock.replace).toHaveBeenCalledWith("/brief")
  })

  it("failed state: error note, Retry re-runs generation, 'Enter Sprntly anyway' → home", async () => {
    ensureMock.mockResolvedValue(undefined)
    seedMock.mockResolvedValue(undefined)
    fetchBriefMock.mockResolvedValue(null)
    startGenMock.mockResolvedValue(undefined)
    pollMock.mockResolvedValue({ status: "failed", error: "Not enough source data." })
    const { container } = await mountLoaded()

    expect(container.querySelector(".onb-h")?.textContent).toBe("Almost there.")
    expect(container.querySelector(".gen-fail")?.textContent).toContain(
      "Not enough source data.",
    )
    expect(
      container.querySelector('a[href="/sources"]')?.textContent,
    ).toContain("Add sources")
    expect(completeMock).not.toHaveBeenCalled()

    // Retry re-runs the whole pipeline.
    expect(ensureMock).toHaveBeenCalledTimes(1)
    pollMock.mockReturnValue(new Promise(() => {}))
    await act(async () => {
      fireEvent.click(screen.getByText("Retry generation"))
    })
    expect(ensureMock).toHaveBeenCalledTimes(2)
    expect(startGenMock).toHaveBeenCalledTimes(2)
    expect(container.querySelector(".gen-stages")).not.toBeNull()
  })

  it("'Enter Sprntly anyway' on the failed path completes onboarding and lands on home", async () => {
    ensureMock.mockRejectedValue(new Error("seed exploded"))
    completeMock.mockResolvedValue(undefined)
    const { container } = await mountLoaded()

    expect(container.querySelector(".gen-fail")?.textContent).toContain("seed exploded")
    const btn = continueButton("Enter Sprntly anyway")
    expect(btn.disabled).toBe(false)
    await act(async () => {
      fireEvent.click(btn)
    })
    expect(completeMock).toHaveBeenCalledWith("ws-1", "u-1")
    expect(window.localStorage.getItem("sprntly_active_company")).toBe("acme")
    expect(routerMock.replace).toHaveBeenCalledWith("/")
  })

  it("Back routes to the connectors step", async () => {
    ensureMock.mockReturnValue(new Promise(() => {}))
    await mountLoaded()
    const back = Array.from(document.querySelectorAll("button")).find((b) =>
      /Back/.test(b.textContent ?? ""),
    )
    expect(back).toBeTruthy()
    fireEvent.click(back as HTMLButtonElement)
    expect(routerMock.push).toHaveBeenCalledWith("/onboarding/connectors")
  })

  it("shows the loading shell while the workspace is loading", () => {
    authMock.mockReturnValue({ kind: "loading" })
    onboardingMock.mockReturnValue(makeOnboardingCtx({ loading: true, workspace: null }))
    render(React.createElement(FirstBrief))
    expect(screen.getByText("Loading…")).not.toBeNull()
  })

  it("redirects to step 1 from an EFFECT (never during render) when there is no workspace", () => {
    authMock.mockReturnValue({ kind: "authed", user: { id: "u-1" }, session: {} })
    onboardingMock.mockReturnValue(makeOnboardingCtx({ workspace: null }))

    const errors: unknown[] = []
    const spy = vi
      .spyOn(console, "error")
      .mockImplementation((...args) => errors.push(args[0]))
    render(React.createElement(FirstBrief))
    spy.mockRestore()

    expect(routerMock.replace).toHaveBeenCalledWith("/onboarding/business-info")
    expect(screen.getByText("Loading…")).not.toBeNull()
    const sideEffectInRender = errors
      .map(String)
      .filter((m) => /while rendering a different component|Cannot update a component/.test(m))
    expect(sideEffectInRender).toEqual([])
  })
})
