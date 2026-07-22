// @vitest-environment jsdom
//
// Container-level mount test for onboarding step 05 — "Connect your tools."
// (v6 screenshot spec 2026-07-17). Mounts the real container under jsdom with
// mocked auth/onboarding/router/api/modal and asserts:
//   - categories come from wizardCategories() — only the v6 wizard categories
//     (docs + revenue are Settings-only), only SUPPORTED connectors, empty
//     categories hidden
//   - ONE AT A TIME: only the categories already reviewed (collapsed) plus the
//     open one are rendered. Unreached categories are absent from the DOM
//     entirely — there are no locked placeholder rows.
//   - the FOOTER drives it: Skip/Continue complete the open category and
//     reveal the next; only when none are left does Continue leave the step,
//     relabelled "Continue to workspace"
//   - a reviewed category collapses to a "Connected" row (there is no
//     "Skipped" variant) and stays re-openable; the "N of M reviewed" counter
//     + progress bar track position
//   - live connections render a non-togglable "Live" card (and keep an
//     otherwise-unsupported provider/category visible)
//   - connectable cards open the connect modal with the right provider
//   - connectors are OPTIONAL: leaving with none stamps skipped_fields
//   - leaving advances to step 6 and routes to /onboarding/workspace; Back goes
//     to /onboarding/api-key
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
const uploadFilesMock = vi.fn()

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
  companiesApi: { uploadFiles: (...a: unknown[]) => uploadFilesMock(...a) },
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
  uploadFilesMock.mockResolvedValue({ ingested: [], errors: [] })
  return render(React.createElement(Connectors))
}

/** The footer's primary button — it drives the accordion, then leaves. */
function footerContinue(container: HTMLElement): HTMLButtonElement {
  const btn = container.querySelector(
    ".onb-footer .btn-brand",
  ) as HTMLButtonElement
  expect(btn).not.toBeNull()
  return btn
}

/** The footer's Skip button. */
function footerSkip(container: HTMLElement): HTMLButtonElement {
  const btn = container.querySelector(
    ".onb-footer .btn-secondary",
  ) as HTMLButtonElement
  expect(btn).not.toBeNull()
  return btn
}

/**
 * Advance past every category, leaving the last one open. Counts against the
 * catalog rather than the DOM: only reached categories are rendered, so the
 * row count is not the total.
 */
function advanceToLastCategory(container: HTMLElement) {
  for (let n = 0; n < SHOWN_CATEGORIES.length - 1; n++) {
    fireEvent.click(footerContinue(container))
  }
}

afterEach(() => {
  cleanup()
  vi.clearAllMocks()
})

describe("Connectors (container) — v6 step 05 accordion", () => {
  it("renders ONLY the first category on arrival — later ones aren't in the DOM yet", () => {
    const { container } = mountLoaded()
    expect(screen.getByText(/Connect your/)).not.toBeNull()
    const steps = container.querySelectorAll(".conn-steps .conn-step")
    expect(steps.length).toBe(1)
    expect(steps[0].getAttribute("data-conn")).toBe(SHOWN_CATEGORIES[0].key)
    expect(steps[0].classList.contains("open")).toBe(true)
    // No status text on the open one, and no locked placeholders below it.
    expect(steps[0].querySelector(".conn-step-state")).toBeNull()
    expect(container.querySelector(".conn-step.locked")).toBeNull()
    // The NEXT category's header isn't rendered at all.
    expect(
      container.querySelector(
        '.conn-step[data-conn="' + SHOWN_CATEGORIES[1].key + '"]',
      ),
    ).toBeNull()
    for (const item of SHOWN_CATEGORIES[0].items) {
      expect(screen.getByText(item.name)).not.toBeNull()
    }
  })

  it("reveals one more category per Continue, collapsing the previous to Connected", () => {
    const { container } = mountLoaded()
    const rows = () => container.querySelectorAll(".conn-steps .conn-step")
    expect(rows().length).toBe(1)

    fireEvent.click(footerContinue(container))
    expect(rows().length).toBe(2)
    // Previous collapsed + Connected; the new one is open with no status.
    expect(rows()[0].classList.contains("open")).toBe(false)
    expect(
      (rows()[0].querySelector(".conn-step-state") as HTMLElement).textContent,
    ).toMatch(/Connected/)
    expect(rows()[1].classList.contains("open")).toBe(true)
    expect(rows()[1].querySelector(".conn-step-state")).toBeNull()

    fireEvent.click(footerContinue(container))
    expect(rows().length).toBe(3)
    expect(rows()[2].getAttribute("data-conn")).toBe(SHOWN_CATEGORIES[2].key)
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
    // Walk the whole list so every category has been revealed.
    advanceToLastCategory(container)
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

  it("relabels Continue to 'Continue to workspace' only on the final category", () => {
    const { container } = mountLoaded()
    // Categories remain → the footer just advances the accordion.
    expect(footerContinue(container).textContent).toMatch(/^Continue/)
    expect(footerContinue(container).textContent).not.toMatch(/workspace/)
    expect(footerSkip(container).textContent?.trim()).toBe("Skip")
    // On the last one, completing it leaves nothing incomplete → it leaves.
    advanceToLastCategory(container)
    expect(footerContinue(container).textContent).toMatch(/Continue to workspace/)
  })

  it("tracks position with the 'N of M reviewed' counter and the progress bar", () => {
    const { container } = mountLoaded()
    const total = SHOWN_CATEGORIES.length
    const bar = container.querySelector(".conn-progress") as HTMLElement
    expect(bar.getAttribute("aria-valuemax")).toBe(String(total))
    expect(bar.getAttribute("aria-valuenow")).toBe("0")
    expect(screen.getByText(`0 of ${total}`)).not.toBeNull()

    fireEvent.click(footerContinue(container))
    expect(screen.getByText(`1 of ${total}`)).not.toBeNull()
    expect(
      (container.querySelector(".conn-progress") as HTMLElement).getAttribute(
        "aria-valuenow",
      ),
    ).toBe("1")
  })

  it("a reviewed category always reads Connected — there is no Skipped state", () => {
    const { container } = mountLoaded()
    // Reviewed via Skip, with nothing selected: still collapses to Connected.
    // The row marks progress through the list, not connection state.
    fireEvent.click(footerSkip(container))
    const state = container.querySelector(
      '.conn-step[data-conn="' + SHOWN_CATEGORIES[0].key + '"] .conn-step-state',
    ) as HTMLElement
    expect(state.getAttribute("data-state")).toBe("connected")
    expect(state.textContent).toMatch(/Connected/)
    expect(screen.queryByText(/Skipped/)).toBeNull()
  })

  it("offers a manual upload fallback only on categories that allow it", async () => {
    const { container } = mountLoaded()
    uploadFilesMock.mockResolvedValue({
      ingested: [{ filename: "events.csv" }],
      errors: [],
    })
    // Analytics allows manual upload.
    const input = container.querySelector(
      ".conn-step.open .conn-upload input[type=file]",
    ) as HTMLInputElement
    expect(input).not.toBeNull()
    fireEvent.change(input, {
      target: { files: [new File(["a"], "events.csv", { type: "text/csv" })] },
    })
    await waitFor(() => {
      expect(uploadFilesMock).toHaveBeenCalled()
      expect(screen.getByText(/events\.csv uploaded/)).not.toBeNull()
    })
    // An uploaded category counts as Connected, not Skipped.
    fireEvent.click(footerContinue(container))
    expect(
      (
        container.querySelector(
          '.conn-step[data-conn="analytics"] .conn-step-state',
        ) as HTMLElement
      ).getAttribute("data-state"),
    ).toBe("connected")
  })

  it("hides the upload fallback on categories that opt out in the catalog", () => {
    const { container } = mountLoaded()
    // Communications (comms) sets allowsManualUpload: false — a one-off export
    // has no channel/permission model to sync against.
    advanceToLastCategory(container)
    const open = container.querySelector(".conn-step.open") as HTMLElement
    if (open.getAttribute("data-conn") === "comms") {
      expect(open.querySelector(".conn-upload")).toBeNull()
    }
  })

  it("hides unsupported connectors and empty categories", () => {
    const { container } = mountLoaded()
    // Analytics (Superset is credentials-wired) opens first, but its
    // unsupported connectors (Mixpanel, PostHog, …) stay hidden.
    expect(container.querySelector('.conn-step[data-conn="analytics"]')).not.toBeNull()
    expect(screen.getByText("Superset")).not.toBeNull()
    expect(screen.queryByText("Mixpanel")).toBeNull()
    expect(screen.queryByText("PostHog")).toBeNull()
    // MS Teams (coming soon) never renders anywhere in the wizard.
    expect(screen.queryByText("MS Teams")).toBeNull()
    // Monitoring has no supported connector today → the whole category is
    // dropped from the catalog, so it never appears however far you walk.
    expect(container.querySelector('.conn-step[data-conn="monitoring"]')).toBeNull()
    // Advance to Voice of Customer & Support (a category renders only once
    // reached, and its grid only while open): Sprinklr (oauth) + Fireflies
    // (api-key) show, but not Zendesk/Gong (coming soon).
    fireEvent.click(footerContinue(container))
    expect(screen.getByText("Sprinklr")).not.toBeNull()
    expect(screen.getByText("Fireflies")).not.toBeNull()
    expect(screen.queryByText("Zendesk")).toBeNull()
    expect(screen.queryByText("Gong")).toBeNull()
    // Advance to CRM: HubSpot (oauth) shows, the coming-soons don't.
    fireEvent.click(footerContinue(container))
    expect(screen.getByText("HubSpot")).not.toBeNull()
    expect(screen.queryByText("Salesforce")).toBeNull()
    // Design-kit-only names never appear.
    expect(screen.queryByText("Segment")).toBeNull()
    expect(screen.queryByText("Trello")).toBeNull()
    // Communications (Slack is OAuth-wired) is a real category — it appears
    // once walked to, and monitoring still never does.
    advanceToLastCategory(container)
    expect(container.querySelector('.conn-step[data-conn="comms"]')).not.toBeNull()
    expect(container.querySelector('.conn-step[data-conn="monitoring"]')).toBeNull()
  })

  it("does not render a category until it is reached", () => {
    const { container } = mountLoaded()
    const at = (key: string) =>
      container.querySelector('.conn-step[data-conn="' + key + '"]')
    // Only the first exists; the second and third are absent, not locked.
    expect(at(SHOWN_CATEGORIES[0].key)).not.toBeNull()
    expect(at(SHOWN_CATEGORIES[1].key)).toBeNull()
    expect(at(SHOWN_CATEGORIES[2].key)).toBeNull()

    fireEvent.click(footerContinue(container))
    expect(
      (at(SHOWN_CATEGORIES[0].key) as HTMLElement).classList.contains("done"),
    ).toBe(true)
    expect(
      (at(SHOWN_CATEGORIES[1].key) as HTMLElement).classList.contains("open"),
    ).toBe(true)
    // The one after it is still absent.
    expect(at(SHOWN_CATEGORIES[2].key)).toBeNull()
  })

  it("footer Skip also completes a category and opens the next one", () => {
    const { container } = mountLoaded()
    fireEvent.click(footerSkip(container))
    const steps = container.querySelectorAll(".conn-step")
    expect(steps[0].classList.contains("done")).toBe(true)
    expect(steps[1].classList.contains("open")).toBe(true)
    // Advancing within the accordion never leaves the step.
    expect(routerMock.push).not.toHaveBeenCalled()
  })

  it("re-opening a reviewed category does not strand the PM — Continue jumps back to the first incomplete one", () => {
    const { container } = mountLoaded()
    const total = SHOWN_CATEGORIES.length
    advanceToLastCategory(container) // 0..n-2 reviewed, last one open
    // Double-check the first category by re-opening it.
    const steps = container.querySelectorAll(".conn-steps .conn-step")
    fireEvent.click(steps[0].querySelector(".conn-step-h") as HTMLElement)
    expect(steps[0].classList.contains("open")).toBe(true)
    // The last category is still unreviewed, so Continue advances rather than
    // leaving — and it re-opens that outstanding category, not the next index.
    expect(footerContinue(container).textContent).not.toMatch(/workspace/)
    fireEvent.click(footerContinue(container))
    expect(
      container.querySelectorAll(".conn-step")[total - 1].classList.contains("open"),
    ).toBe(true)
    expect(routerMock.push).not.toHaveBeenCalled()
  })

  it("reviewed categories stay re-openable", () => {
    const { container } = mountLoaded()
    fireEvent.click(footerContinue(container))
    const first = container.querySelector(".conn-steps .conn-step") as HTMLElement
    fireEvent.click(first.querySelector(".conn-step-h") as HTMLElement)
    expect(first.classList.contains("open")).toBe(true)
    expect(first.classList.contains("done")).toBe(true)
    // Re-opened, its summary row gives way to the grid again.
    expect(first.querySelector(".conn-step-state")).toBeNull()
  })

  it("opens the connect modal for a connectable card", () => {
    const { container } = mountLoaded()
    // Advance to the CRM category, where HubSpot (oauth) lives.
    fireEvent.click(footerContinue(container))
    fireEvent.click(footerContinue(container))
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

  it("advances to step 6 and routes to workspace once a connection is live (no skip marking)", async () => {
    const { container } = mountLoaded([{ provider: "mixpanel", status: "active" }])
    await screen.findByText("Live")
    advanceToLastCategory(container)
    fireEvent.click(footerContinue(container))
    await waitFor(() => {
      expect(advanceStepMock).toHaveBeenCalledWith("ws-1", 6)
      expect(routerMock.push).toHaveBeenCalledWith("/onboarding/workspace")
    })
    expect(markSkippedMock).not.toHaveBeenCalled()
  })

  it("lets the PM leave with ZERO connectors — nothing here is required", async () => {
    const { container } = mountLoaded([])
    expect(footerContinue(container).disabled).toBe(false)
    advanceToLastCategory(container)
    fireEvent.click(footerContinue(container))
    await waitFor(() => {
      expect(advanceStepMock).toHaveBeenCalledWith("ws-1", 6)
      expect(routerMock.push).toHaveBeenCalledWith("/onboarding/workspace")
    })
    // Continue (not Skip) doesn't stamp the field as skipped, even at zero.
    expect(markSkippedMock).not.toHaveBeenCalled()
  })

  it("Skipping out of the LAST category with nothing wired records the skipped field", async () => {
    const { container } = mountLoaded([])
    advanceToLastCategory(container)
    fireEvent.click(footerSkip(container))
    await waitFor(() => {
      expect(markSkippedMock).toHaveBeenCalledWith("u-1", ["connectors"])
      expect(advanceStepMock).toHaveBeenCalledWith("ws-1", 6)
      expect(routerMock.push).toHaveBeenCalledWith("/onboarding/workspace")
    })
  })

  it("Skipping out does NOT record skipped_fields when something is wired", async () => {
    const { container } = mountLoaded([{ provider: "mixpanel", status: "active" }])
    await screen.findByText("Live")
    advanceToLastCategory(container)
    fireEvent.click(footerSkip(container))
    await waitFor(() => {
      expect(advanceStepMock).toHaveBeenCalledWith("ws-1", 6)
    })
    expect(markSkippedMock).not.toHaveBeenCalled()
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
