// @vitest-environment jsdom
//
// Container-level mount test for onboarding page 06 — "Connect your tools."
// (design-v4 accordion port). Mounts the real container under jsdom with
// mocked auth/onboarding/router/api/modal and asserts:
//   - categories render from CONNECTOR_CATALOG (not the design kit's list)
//   - sequential unlock: category N+1 is locked until N is done/skipped,
//     done categories stay re-openable
//   - live connections render a non-togglable "Live" card
//   - connectable cards open the connect modal with the right provider
//   - Continue advances to step 4 and routes to /onboarding/coworkers
//   - "Connect later" marks skipped fields first, then advances
//   - NO required-Analytics gate: Continue is enabled with zero selections
//   - the no-workspace redirect happens in an EFFECT, never during render
//
// Matchers: native DOM only (no @testing-library/jest-dom).
import * as React from "react"
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react"
import { afterEach, describe, expect, it, vi } from "vitest"

;(globalThis as typeof globalThis & { React?: typeof React }).React = React

const authMock = vi.fn()
const onboardingMock = vi.fn()
const routerMock = { push: vi.fn(), replace: vi.fn() }
const advanceStepMock = vi.fn()
const markSkippedMock = vi.fn()
const listMock = vi.fn()

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
  connectorsApi: { list: (...a: unknown[]) => listMock(...a) },
}))
// The modal's real implementation drags in OAuth wiring + provider config
// slots; stub it to a marker so we can assert open/provider at the container
// boundary.
vi.mock("../../../connectors/ConnectorConnectModal", () => ({
  ConnectorConnectModal: (props: { providerId: string | null }) =>
    props.providerId
      ? React.createElement("div", {
          "data-testid": "connect-modal",
          "data-provider": props.providerId,
        })
      : null,
}))

import { Connectors } from "../Connectors"
import { CONNECTOR_CATALOG } from "../../../../lib/connectorsCatalog"
import { makeWorkspace, makeOnboardingCtx } from "./fixtures"

function mountLoaded(connections: unknown[] = []) {
  authMock.mockReturnValue({ kind: "authed", user: { id: "u-1" }, session: {} })
  onboardingMock.mockReturnValue(
    makeOnboardingCtx({ workspace: makeWorkspace({ onboarding_step: 3 }) }),
  )
  listMock.mockResolvedValue({ connections })
  advanceStepMock.mockResolvedValue(makeWorkspace({ onboarding_step: 4 }))
  markSkippedMock.mockResolvedValue(undefined)
  return render(React.createElement(Connectors))
}

/** The "Done · next" / "Done" primary button of the open accordion step. */
function doneNextButton(container: HTMLElement): HTMLButtonElement {
  const btn = container.querySelector(
    ".conn-step.open .conn-step-foot .btn-brand",
  ) as HTMLButtonElement
  expect(btn).not.toBeNull()
  return btn
}

afterEach(() => {
  cleanup()
  vi.clearAllMocks()
})

describe("Connectors (container) — design-v4 accordion", () => {
  it("renders every catalog category as an accordion step, first one open", () => {
    const { container } = mountLoaded()
    expect(screen.getByText(/Connect your/)).not.toBeNull()
    const steps = container.querySelectorAll(".conn-steps .conn-step")
    expect(steps.length).toBe(CONNECTOR_CATALOG.length)
    CONNECTOR_CATALOG.forEach((cat, i) => {
      expect(steps[i].getAttribute("data-conn")).toBe(cat.key)
    })
    // first category open ("In progress"), its catalog items in the grid
    expect(steps[0].classList.contains("open")).toBe(true)
    expect(screen.getByText("In progress")).not.toBeNull()
    for (const item of CONNECTOR_CATALOG[0].items) {
      expect(screen.getByText(item.name)).not.toBeNull()
    }
  })

  it("renders the catalog connectors, not the design kit's hardcoded list", () => {
    mountLoaded()
    // PostHog is catalog-only relative to old hardcoded lists; Segment and
    // Trello exist only in the design kit and must NOT render.
    expect(screen.getByText("PostHog")).not.toBeNull()
    expect(screen.queryByText("Segment")).toBeNull()
    expect(screen.queryByText("Trello")).toBeNull()
  })

  it("locks later categories until the previous one is done/skipped", () => {
    const { container } = mountLoaded()
    const steps = container.querySelectorAll(".conn-step")
    expect(steps[1].classList.contains("locked")).toBe(true)
    // clicking a locked header does nothing
    fireEvent.click(steps[1].querySelector(".conn-step-h") as HTMLElement)
    expect(steps[1].classList.contains("open")).toBe(false)

    // Done · next on the open (first) category → first done, second open
    fireEvent.click(doneNextButton(container))
    expect(steps[0].classList.contains("done")).toBe(true)
    expect(steps[0].classList.contains("open")).toBe(false)
    expect(steps[1].classList.contains("open")).toBe(true)
    expect(steps[1].classList.contains("locked")).toBe(false)
    // third remains locked
    expect(steps[2].classList.contains("locked")).toBe(true)
  })

  it("Skip also completes a category and opens the next one", () => {
    const { container } = mountLoaded()
    const skip = container.querySelector(
      ".conn-step.open .conn-step-foot .btn-ghost",
    ) as HTMLButtonElement
    fireEvent.click(skip)
    const steps = container.querySelectorAll(".conn-step")
    expect(steps[0].classList.contains("done")).toBe(true)
    expect(steps[1].classList.contains("open")).toBe(true)
  })

  it("done categories stay re-openable", () => {
    const { container } = mountLoaded()
    fireEvent.click(doneNextButton(container))
    const steps = container.querySelectorAll(".conn-step")
    // re-open the completed first category
    fireEvent.click(steps[0].querySelector(".conn-step-h") as HTMLElement)
    expect(steps[0].classList.contains("open")).toBe(true)
    expect(steps[0].classList.contains("done")).toBe(true)
  })

  it("toggles a non-connectable card into the planned selection", () => {
    const { container } = mountLoaded()
    // Mixpanel (analytics) has no connect backend → pure selection toggle
    const card = screen.getByText("Mixpanel").closest(".conn") as HTMLElement
    fireEvent.click(card)
    expect(card.classList.contains("on")).toBe(true)
    expect(container.querySelector('[data-testid="connect-modal"]')).toBeNull()
    fireEvent.click(card)
    expect(card.classList.contains("on")).toBe(false)
  })

  it("opens the connect modal for a connectable card", () => {
    const { container } = mountLoaded()
    // unlock Project Management, where ClickUp (oauth: true) lives
    fireEvent.click(doneNextButton(container))
    fireEvent.click(screen.getByText("ClickUp").closest(".conn") as HTMLElement)
    const modal = container.querySelector('[data-testid="connect-modal"]')
    expect(modal).not.toBeNull()
    expect(modal?.getAttribute("data-provider")).toBe("clickup")
    // opening the modal does NOT pre-select the card
    expect(
      (screen.getByText("ClickUp").closest(".conn") as HTMLElement).classList.contains("on"),
    ).toBe(false)
  })

  it("shows an active connection as a non-togglable Live card", async () => {
    const { container } = mountLoaded([
      { provider: "mixpanel", status: "active" },
      { provider: "heap", status: "error" },
    ])
    await screen.findByText("Live")
    const card = screen.getByText("Mixpanel").closest(".conn") as HTMLElement
    expect(card.classList.contains("on")).toBe(true)
    expect(card.classList.contains("live")).toBe(true)
    // clicking a live card neither deselects it nor opens the modal
    fireEvent.click(card)
    expect(card.classList.contains("on")).toBe(true)
    expect(container.querySelector('[data-testid="connect-modal"]')).toBeNull()
    // non-active statuses don't count as live
    const heap = screen.getByText("Heap").closest(".conn") as HTMLElement
    expect(heap.classList.contains("live")).toBe(false)
  })

  it("Continue advances to step 4 and routes to coworkers (no skip marking)", async () => {
    mountLoaded()
    fireEvent.click(screen.getByText("Continue").closest("button") as HTMLElement)
    await waitFor(() => {
      expect(advanceStepMock).toHaveBeenCalledWith("ws-1", 4)
      expect(routerMock.push).toHaveBeenCalledWith("/onboarding/coworkers")
    })
    expect(markSkippedMock).not.toHaveBeenCalled()
  })

  it("'Connect later' marks connectors skipped, then advances", async () => {
    mountLoaded()
    fireEvent.click(screen.getByText("Connect later"))
    await waitFor(() => {
      expect(markSkippedMock).toHaveBeenCalledWith("u-1", ["connectors"])
      expect(advanceStepMock).toHaveBeenCalledWith("ws-1", 4)
      expect(routerMock.push).toHaveBeenCalledWith("/onboarding/coworkers")
    })
  })

  it("has NO analytics requirement gate — Continue enabled with zero selections", () => {
    mountLoaded()
    const btn = screen.getByText("Continue").closest("button") as HTMLButtonElement
    expect(btn.disabled).toBe(false)
  })

  it("Back routes to the metrics page", () => {
    mountLoaded()
    fireEvent.click(screen.getByText("Back").closest("button") as HTMLElement)
    expect(routerMock.push).toHaveBeenCalledWith("/onboarding/metrics")
  })

  it("shows the loading shell while the workspace is loading", () => {
    authMock.mockReturnValue({ kind: "loading" })
    onboardingMock.mockReturnValue(makeOnboardingCtx({ loading: true, workspace: null }))
    render(React.createElement(Connectors))
    expect(screen.getByText("Loading…")).not.toBeNull()
  })

  it("redirects to step 1 from an EFFECT (never during render) when there is no workspace", () => {
    authMock.mockReturnValue({ kind: "authed", user: { id: "u-1" }, session: {} })
    onboardingMock.mockReturnValue(makeOnboardingCtx({ workspace: null }))

    const errors: unknown[] = []
    const spy = vi
      .spyOn(console, "error")
      .mockImplementation((...args) => errors.push(args[0]))
    render(React.createElement(Connectors))
    spy.mockRestore()

    expect(routerMock.replace).toHaveBeenCalledWith("/onboarding/business-info")
    expect(screen.getByText("Loading…")).not.toBeNull()
    const sideEffectInRender = errors
      .map(String)
      .filter((m) => /while rendering a different component|Cannot update a component/.test(m))
    expect(sideEffectInRender).toEqual([])
  })
})
