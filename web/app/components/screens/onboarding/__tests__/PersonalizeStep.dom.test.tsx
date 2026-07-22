// @vitest-environment jsdom
//
// Container mount test for onboarding step 09 — "Personalize your workspace"
// (v7 screenshot spec 2026-07-21). The closing NUMBERED step.
//
// Covers:
//   - insight-type chips + free-text note, persisted into the EXISTING
//     companies.notification_settings blob (never clobbering sibling keys)
//   - the delivery disclosure: frequency / destination / day / time / timezone,
//     written with the same keys Settings → Comms & Brief uses
//   - Microsoft Teams renders disabled — there is no backend delivery path
//   - THE GATE, which moved here from ReviewStep when personalize was inserted
//     between review and the define-metrics sub-flow: with a live analytics
//     connection Continue hands off to /onboarding/define-metrics; without one
//     it runs the shared closer and enters the app instead
//   - a connector probe that fails counts as "no analytics" (fail-open), so a
//     flaky list call can't strand the PM on the last step
//
// Matchers: native DOM only (no @testing-library/jest-dom).
import * as React from "react"
import { act, cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react"
import { afterEach, describe, expect, it, vi } from "vitest"

;(globalThis as typeof globalThis & { React?: typeof React }).React = React

const authMock = vi.fn()
const onboardingMock = vi.fn()
const routerMock = { push: vi.fn(), replace: vi.fn() }
const updateWorkspaceMock = vi.fn()
const connectorsListMock = vi.fn()
const finishMock = vi.fn()
const prefetchMetricsMock = vi.fn()

vi.mock("../../../../lib/auth", () => ({ useAuth: () => authMock() }))
vi.mock("../../../../context/OnboardingContext", () => ({
  useOnboarding: () => onboardingMock(),
}))
vi.mock("../../../../context/ContentContext", () => ({
  useContent: () => ({ setContent: vi.fn() }),
}))
vi.mock("next/navigation", () => ({ useRouter: () => routerMock }))
vi.mock("../../../../lib/onboarding/store", () => ({
  updateWorkspace: (...a: unknown[]) => updateWorkspaceMock(...a),
}))
vi.mock("../../../../lib/api", () => ({
  connectorsApi: { list: (...a: unknown[]) => connectorsListMock(...a) },
}))
vi.mock("../../../../lib/onboarding/draftPrefetch", () => ({
  prefetchMetricDefinitions: (...a: unknown[]) => prefetchMetricsMock(...a),
}))
vi.mock("../../../../lib/onboarding/finishOnboarding", () => ({
  finishOnboardingAndEnterApp: (...a: unknown[]) => finishMock(...a),
  POST_ONBOARDING_PATH: "/?new=1",
}))
// The real picker fetches Slack channels; stub to a marker.
vi.mock("../../../connectors/SlackChannelPicker", () => ({
  SlackChannelPicker: () =>
    React.createElement("div", { "data-testid": "slack-picker" }),
}))

import { PersonalizeStep } from "../PersonalizeStep"
import { makeWorkspace, makeOnboardingCtx } from "./fixtures"

/** A live Analytics connection — what keeps the define-metrics hand-off alive. */
function analyticsConnected() {
  connectorsListMock.mockResolvedValue({
    connections: [{ provider: "posthog", status: "active", types: ["analytics"] }],
  })
}

function mount(workspace = makeWorkspace({ onboarding_step: 9 })) {
  authMock.mockReturnValue({ kind: "authed", user: { id: "u-1" }, session: {} })
  onboardingMock.mockReturnValue(makeOnboardingCtx({ workspace }))
  updateWorkspaceMock.mockResolvedValue(workspace)
  finishMock.mockResolvedValue(undefined)
  prefetchMetricsMock.mockResolvedValue(undefined)
  return render(React.createElement(PersonalizeStep))
}

function continueBtn(): HTMLButtonElement {
  return Array.from(document.querySelectorAll(".onb-footer button")).find((b) =>
    /Next · define metrics|Looks right · enter Sprntly/.test(b.textContent ?? ""),
  ) as HTMLButtonElement
}

/**
 * A chip button by its label. Matched against the BUTTON's own text rather
 * than an exact text node, since disabled chips append a " — soon" suffix and
 * the step title/footer both contain "Personalize your workspace".
 */
function chip(label: string): HTMLButtonElement {
  const btn = Array.from(
    document.querySelectorAll(".onb-card .metric-chips button"),
  ).find((b) => (b.textContent ?? "").includes(label))
  expect(btn).not.toBeUndefined()
  return btn as HTMLButtonElement
}

/** Delivery (frequency / destination / day / time / tz) sits behind a
 *  disclosure, collapsed by default — open it before querying those chips. */
function openDelivery() {
  fireEvent.click(screen.getByText(/Delivery — when & where/))
}

afterEach(() => {
  cleanup()
  vi.clearAllMocks()
})

describe("PersonalizeStep (onboarding step 09 — surface + delivery)", () => {
  it("renders on step 9 of the dots with the insight chips", async () => {
    analyticsConnected()
    const { container } = mount()
    expect(
      (container.querySelector(".onb-dots") as HTMLElement).getAttribute("data-step"),
    ).toBe("9")
    expect(
      (container.querySelector(".onb-card .onb-h") as HTMLElement).textContent,
    ).toBe("Personalize your workspace.")
    expect(chip("Top user problems & opportunities")).not.toBeNull()
    expect(chip("Wins to celebrate")).not.toBeNull()
    await waitFor(() => expect(continueBtn().disabled).toBe(false))
  })

  it("renders Microsoft Teams disabled — there is no delivery path for it yet", async () => {
    analyticsConnected()
    mount()
    openDelivery()
    expect(chip("Microsoft Teams").disabled).toBe(true)
    // Slack and Email are real choices.
    expect(chip("Slack").disabled).toBe(false)
    expect(chip("Email").disabled).toBe(false)
    await waitFor(() => expect(continueBtn().disabled).toBe(false))
  })

  it("with analytics live, Continue saves preferences and hands off to define-metrics", async () => {
    analyticsConnected()
    mount()
    await waitFor(() => expect(continueBtn().disabled).toBe(false))

    // Toggle one chip off and another on so the saved array isn't just defaults.
    fireEvent.click(chip("Top user problems & opportunities"))
    fireEvent.click(chip("Wins to celebrate"))

    await act(async () => {
      continueBtn().click()
    })

    await waitFor(() => {
      expect(routerMock.push).toHaveBeenCalledWith("/onboarding/define-metrics")
    })
    const patch = updateWorkspaceMock.mock.calls[0][1]
    expect(patch.notification_settings.brief_insight_types).toEqual([
      "drive_metric",
      "wins",
    ])
    // The closer belongs to define-metrics on this branch.
    expect(finishMock).not.toHaveBeenCalled()
  })

  it("merges into notification_settings rather than clobbering sibling keys", async () => {
    analyticsConnected()
    mount(
      makeWorkspace({
        onboarding_step: 9,
        notification_settings: {
          email_recipients: ["ops@acme.com"],
          brief_hour: 14,
        },
      }),
    )
    await waitFor(() => expect(continueBtn().disabled).toBe(false))

    await act(async () => {
      continueBtn().click()
    })

    await waitFor(() => expect(updateWorkspaceMock).toHaveBeenCalled())
    const ns = updateWorkspaceMock.mock.calls[0][1].notification_settings
    // Untouched sibling key survives…
    expect(ns.email_recipients).toEqual(["ops@acme.com"])
    // …and the already-persisted hour seeds the form rather than resetting to 9.
    expect(ns.brief_hour).toBe(14)
    // The keys this step owns are written in the Settings-compatible shape.
    expect(ns.brief_minute).toBe(0)
    expect(typeof ns.brief_frequency).toBe("string")
    expect(typeof ns.timezone).toBe("string")
  })

  it("with NO analytics connector, Continue finishes onboarding instead of routing to define-metrics", async () => {
    // A non-analytics live connection plus a revoked analytics one: neither
    // keeps the sub-flow alive.
    connectorsListMock.mockResolvedValue({
      connections: [
        { provider: "github", status: "active", types: ["code"] },
        { provider: "mixpanel", status: "revoked", types: ["analytics"] },
      ],
    })
    mount()

    await waitFor(() => {
      expect(continueBtn().textContent).toMatch(/Looks right · enter Sprntly/)
    })

    await act(async () => {
      continueBtn().click()
    })

    await waitFor(() => {
      expect(routerMock.replace).toHaveBeenCalledWith("/?new=1")
    })
    expect(finishMock).toHaveBeenCalledTimes(1)
    expect(routerMock.push).not.toHaveBeenCalledWith("/onboarding/define-metrics")
    // Nothing to detect without analytics — the warm-up never fires either.
    expect(prefetchMetricsMock).not.toHaveBeenCalled()
  })

  it("treats a failed connector probe as 'no analytics' rather than stranding the PM", async () => {
    connectorsListMock.mockRejectedValue(new Error("connectors down"))
    mount()
    // Continue resolves to the finishing CTA instead of staying disabled.
    await waitFor(() => {
      expect(continueBtn().disabled).toBe(false)
      expect(continueBtn().textContent).toMatch(/Looks right · enter Sprntly/)
    })
  })

  it("Back routes to the review step", async () => {
    analyticsConnected()
    mount()
    fireEvent.click(screen.getByText("Back").closest("button") as HTMLElement)
    expect(routerMock.push).toHaveBeenCalledWith("/onboarding/review")
  })
})
