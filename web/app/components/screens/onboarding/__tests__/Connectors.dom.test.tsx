// @vitest-environment jsdom
//
// Container-level mount test for onboarding step 05 — "Connect your tools."
// (v6 screenshot spec 2026-07-17). Mounts the real container under jsdom with
// mocked auth/onboarding/router/api/modal and asserts:
//   - categories render from wizardCategories() — only the v6 wizard
//     categories (docs + revenue are Settings-only), only SUPPORTED
//     connectors, empty categories hidden
//   - sequential unlock: category N+1 is locked until N is done/skipped,
//     done categories stay re-openable
//   - live connections render a non-togglable "Live" card (and keep an
//     otherwise-unsupported provider/category visible)
//   - connectable cards open the connect modal with the right provider
//   - the ≥1-live-connection gate applies to EVERYONE (no "Connect later")
//   - Continue advances to step 5 and routes to /onboarding/team; Back goes
//     to /onboarding/metrics
//     and routes to /onboarding/review
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
import {
  ONBOARDING_CONNECTOR_CATEGORIES,
  wizardCategories,
} from "../../../../lib/onboarding/connectorsWizard"
import { ONBOARDING_STEP_COUNT } from "../../../../lib/onboarding/types"
import { makeWorkspace, makeOnboardingCtx } from "./fixtures"

// What onboarding actually renders: the v6 wizard categories only (docs and
// revenue are Settings-only), supported connectors only, empty categories
// dropped.
const SHOWN_CATEGORIES = wizardCategories()

function mountLoaded(connections: unknown[] = []) {
  authMock.mockReturnValue({ kind: "authed", user: { id: "u-1" }, session: {} })
  onboardingMock.mockReturnValue(
    makeOnboardingCtx({
      workspace: makeWorkspace({ onboarding_step: 5 }),
    }),
  )
  listMock.mockResolvedValue({ connections })
  advanceStepMock.mockResolvedValue(makeWorkspace({ onboarding_step: 6 }))
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

describe("Connectors (container) — v6 step 05 accordion", () => {
  it("renders every SUPPORTED wizard category as an accordion step, first one open", () => {
    const { container } = mountLoaded()
    expect(screen.getByText(/Connect your/)).not.toBeNull()
    const steps = container.querySelectorAll(".conn-steps .conn-step")
    expect(steps.length).toBe(SHOWN_CATEGORIES.length)
    SHOWN_CATEGORIES.forEach((cat, i) => {
      expect(steps[i].getAttribute("data-conn")).toBe(cat.key)
    })
    // first category open ("In progress"), its supported items in the grid
    expect(steps[0].classList.contains("open")).toBe(true)
    expect(screen.getByText("In progress")).not.toBeNull()
    for (const item of SHOWN_CATEGORIES[0].items) {
      expect(screen.getByText(item.name)).not.toBeNull()
    }
  })

  it("renders the header + sub copy verbatim, on step 5 of the dots", () => {
    const { container } = mountLoaded()
    // Header: "Connect your tools." with the period inside the italic <em>.
    const h = container.querySelector(".onb-card .onb-h") as HTMLElement
    expect(h.textContent).toBe("Connect your tools.")
    expect((h.querySelector("em") as HTMLElement).textContent).toBe("tools.")
    const sub = container.querySelector(".onb-card .onb-sub") as HTMLElement
    expect(sub.textContent).toBe(
      "The more Sprntly can see, the sharper your briefs. Connect what you use — each one opens the next. Skip anything you'll wire later.",
    )
    // The chrome marks step 5 of the 10 numbered steps.
    expect(
      (container.querySelector(".onb-dots") as HTMLElement).getAttribute("data-step"),
    ).toBe("5")
    // Design accordion shell: onb-card → conn-steps → conn-step rows.
    expect(container.querySelector(".onb-card .conn-steps")).not.toBeNull()
    expect(container.querySelectorAll(".conn-steps .conn-step").length).toBeGreaterThan(0)
  })

  it("shows ONLY the v6 wizard categories — docs and revenue are Settings-only, crm is in", () => {
    const { container } = mountLoaded()
    const keys = Array.from(container.querySelectorAll(".conn-step")).map((s) =>
      s.getAttribute("data-conn"),
    )
    // Docs (Notion / Google Docs) and revenue (Stripe / ChartMogul) never
    // appear in the wizard.
    expect(keys).not.toContain("docs")
    expect(keys).not.toContain("revenue")
    // The new CRM category is a wizard step.
    expect(keys).toContain("crm")
    // Every shown key is one of the declared wizard categories, in order.
    for (const key of keys) {
      expect(ONBOARDING_CONNECTOR_CATEGORIES).toContain(key)
    }
  })

  it("uses the design footer labels: 'Skip' and 'Done · next ↓' (plain 'Done' on the last)", () => {
    const { container } = mountLoaded()
    const foot = container.querySelector(
      ".conn-step.open .conn-step-foot",
    ) as HTMLElement
    const [skip, done] = Array.from(foot.querySelectorAll("button"))
    expect(skip.textContent?.trim()).toBe("Skip")
    // Non-last category opens the one below → "Done · next" + a down arrow.
    expect(done.textContent).toMatch(/Done · next/)
    expect(done.querySelector("svg")).not.toBeNull()

    // Walk to the LAST category and assert it reads plain "Done" (nothing opens
    // below it) with no arrow.
    const stepCount = container.querySelectorAll(".conn-step").length
    for (let n = 0; n < stepCount - 1; n++) {
      fireEvent.click(
        container.querySelector(
          ".conn-step.open .conn-step-foot .btn-brand",
        ) as HTMLElement,
      )
    }
    const lastDone = container.querySelector(
      ".conn-step.open .conn-step-foot .btn-brand",
    ) as HTMLElement
    expect(lastDone.textContent?.trim()).toBe("Done")
    expect(lastDone.querySelector("svg")).toBeNull()
  })

  it("hides unsupported connectors and empty categories", () => {
    const { container } = mountLoaded()
    // Analytics (Superset is credentials-wired) opens first, but its
    // unsupported connectors (Mixpanel, PostHog, …) stay hidden.
    expect(container.querySelector('.conn-step[data-conn="analytics"]')).not.toBeNull()
    expect(screen.getByText("Superset")).not.toBeNull()
    expect(screen.queryByText("Mixpanel")).toBeNull()
    expect(screen.queryByText("PostHog")).toBeNull()
    // Communications is still shown (Slack is OAuth-wired) — its accordion step
    // exists in catalog order …
    expect(container.querySelector('.conn-step[data-conn="comms"]')).not.toBeNull()
    // … but MS Teams (coming soon) never renders in the accordion tree.
    expect(screen.queryByText("MS Teams")).toBeNull()
    // Monitoring has no supported connector today → the whole category hides.
    expect(container.querySelector('.conn-step[data-conn="monitoring"]')).toBeNull()
    // Advance to Voice of Customer & Support (steps render their grids only
    // while open): only supported connectors render — Sprinklr (oauth) +
    // Fireflies (api-key) but not Zendesk/Gong (coming soon).
    fireEvent.click(doneNextButton(container))
    expect(screen.getByText("Sprinklr")).not.toBeNull()
    expect(screen.getByText("Fireflies")).not.toBeNull()
    expect(screen.queryByText("Zendesk")).toBeNull()
    expect(screen.queryByText("Gong")).toBeNull()
    // Advance to CRM: HubSpot (oauth) shows, the coming-soons don't.
    fireEvent.click(doneNextButton(container))
    expect(screen.getByText("HubSpot")).not.toBeNull()
    expect(screen.queryByText("Salesforce")).toBeNull()
    // Design-kit-only names never appear.
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

  it("opens the connect modal for a connectable card", () => {
    const { container } = mountLoaded()
    // Advance to the CRM category, where HubSpot (oauth) lives.
    fireEvent.click(doneNextButton(container))
    fireEvent.click(doneNextButton(container))
    fireEvent.click(screen.getByText("HubSpot").closest(".conn") as HTMLElement)
    const modal = container.querySelector('[data-testid="connect-modal"]')
    expect(modal).not.toBeNull()
    expect(modal?.getAttribute("data-provider")).toBe("hubspot")
    // opening the modal does NOT pre-select the card
    expect(
      (screen.getByText("HubSpot").closest(".conn") as HTMLElement).classList.contains("on"),
    ).toBe(false)
  })

  it("keeps an otherwise-unsupported provider visible (and non-togglable) when it has a live connection", async () => {
    // Mixpanel is unsupported, but an ACTIVE connection keeps it — and its
    // category — visible.
    const { container } = mountLoaded([
      { provider: "mixpanel", status: "active" },
      { provider: "heap", status: "error" },
    ])
    await screen.findByText("Live")
    // Analytics opens first, so Mixpanel's live card is already rendered.
    const card = screen.getByText("Mixpanel").closest(".conn") as HTMLElement
    expect(card.classList.contains("on")).toBe(true)
    expect(card.classList.contains("live")).toBe(true)
    // clicking a live card neither deselects it nor opens the modal
    fireEvent.click(card)
    expect(card.classList.contains("on")).toBe(true)
    expect(container.querySelector('[data-testid="connect-modal"]')).toBeNull()
    // Heap (status "error", unsupported) is not kept — it never renders.
    expect(screen.queryByText("Heap")).toBeNull()
  })

  it("Continue advances to step 6 and routes to team once a connection is live (no skip marking)", async () => {
    mountLoaded([{ provider: "mixpanel", status: "active" }])
    await screen.findByText("Live")
    fireEvent.click(screen.getByText("Continue").closest("button") as HTMLElement)
    await waitFor(() => {
      expect(advanceStepMock).toHaveBeenCalledWith("ws-1", 6)
      expect(routerMock.push).toHaveBeenCalledWith("/onboarding/team")
    })
    expect(markSkippedMock).not.toHaveBeenCalled()
  })

  it("gates EVERYONE: Continue disabled with zero live connections, no 'Connect later' link", () => {
    mountLoaded([])
    const btn = screen.getByText("Continue").closest("button") as HTMLButtonElement
    expect(btn.disabled).toBe(true)
    expect(screen.queryByText("Connect later")).toBeNull()
    expect(
      screen.getByText(
        "Connect at least one source to continue — it's what your briefs are built from.",
      ),
    ).not.toBeNull()
  })

  it("Back routes to the api-key step", () => {
    mountLoaded()
    fireEvent.click(screen.getByText("Back").closest("button") as HTMLElement)
    expect(routerMock.push).toHaveBeenCalledWith("/onboarding/api-key")
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

    expect(routerMock.replace).toHaveBeenCalledWith("/onboarding/company")
    expect(screen.getByText("Loading…")).not.toBeNull()
    const sideEffectInRender = errors
      .map(String)
      .filter((m) => /while rendering a different component|Cannot update a component/.test(m))
    expect(sideEffectInRender).toEqual([])
  })
})
