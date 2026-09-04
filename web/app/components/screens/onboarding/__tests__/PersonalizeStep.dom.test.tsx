// @vitest-environment jsdom
//
// Container mount test for onboarding step 09 — "Personalize your workspace"
// (v7 screenshot spec 2026-07-21). The closing NUMBERED step.
//
// Covers:
//   - insight-type chips + free-text note, persisted into the EXISTING
//     companies.notification_settings blob (never clobbering sibling keys)
//   - delivery: frequency / destination / day / time / timezone, OPEN by
//     default (2026-09-03) rather than behind a click, written with the same
//     keys Settings → Comms & Brief uses
//   - Microsoft Teams is not offered at all (2026-09-03) — no backend delivery
//     path, so it is left out rather than shown disabled
//   - Slack is NEVER the pre-selected chip (2026-09-03): picking it while
//     unconnected opens the same connect modal Connectors uses, so choosing
//     Slack and connecting it happen in one motion
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
// The real modal drags in OAuth wiring + provider config slots — stub to a
// marker so tests can assert open/provider at the container boundary, same as
// Connectors.dom.test.tsx.
vi.mock("../../../connectors/ConnectorConnectModal", () => ({
  ConnectorConnectModal: (props: { providerId: string | null }) =>
    props.providerId
      ? React.createElement("div", {
          "data-testid": "connect-modal",
          "data-provider": props.providerId,
        })
      : null,
}))

import { PersonalizeStep } from "../PersonalizeStep"
import { makeWorkspace, makeOnboardingCtx } from "./fixtures"

/** A live Analytics connection — what keeps the define-metrics hand-off alive. */
function analyticsConnected() {
  connectorsListMock.mockResolvedValue({
    connections: [{ provider: "posthog", status: "active", types: ["analytics"] }],
  })
}

/** A live, personally-installed Slack connection (delivery reads THIS, not the
 *  company-shared row — see PersonalizeStep's own `slack` memo).
 *
 *  RETURNS THE PROMISE, so a test can wait for the list to be IN STATE rather
 *  than merely requested — see the click-ordering note in the test below. */
function slackConnected() {
  const listed = Promise.resolve({
    connections: [{ provider: "slack", status: "active", types: [] }],
  })
  connectorsListMock.mockReturnValue(listed)
  return listed
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

afterEach(() => {
  cleanup()
  vi.clearAllMocks()
})

describe("PersonalizeStep (onboarding step 09 — surface + delivery)", () => {
  it("renders on the last dot with the insight chips", async () => {
    // Derived (`stepForSlug`), not a literal — see Connectors.dom.test.tsx's
    // matching comment for why that distinction matters here.
    analyticsConnected()
    const { container } = mount()
    expect(
      (container.querySelector(".onb-dots") as HTMLElement).getAttribute("data-step"),
    ).toBe("4")
    expect(
      (container.querySelector(".onb-card .onb-h") as HTMLElement).textContent,
    ).toBe("Personalize your workspace.")
    expect(chip("Top Customer Problem")).not.toBeNull()
    expect(chip("Competitor & market moves")).not.toBeNull()
    await waitFor(() => expect(continueBtn().disabled).toBe(false))
  })

  it("offers only the insight types that have a skill behind them, in the specified order", async () => {
    analyticsConnected()
    const { container } = mount()
    const labels = Array.from(
      container.querySelectorAll('[data-field="surfaces"] button'),
    ).map((b) => (b.textContent ?? "").trim())
    expect(labels).toEqual([
      "Top Customer Problem",
      "Competitor & market moves",
      "What to build next",
    ])
    // The free-text override is gone with them.
    expect(container.querySelector("textarea")).toBeNull()
    await waitFor(() => expect(continueBtn().disabled).toBe(false))
  })

  it("delivery is visible on arrival — no click needed to reach frequency/destination", async () => {
    // The whole point of opening it by default: a PM must be able to pick a
    // schedule without discovering there was something to expand.
    analyticsConnected()
    mount()
    expect(chip("Weekly")).not.toBeNull()
    expect(chip("Slack")).not.toBeNull()
    expect(chip("Email")).not.toBeNull()
    await waitFor(() => expect(continueBtn().disabled).toBe(false))
  })

  it("does not offer Microsoft Teams at all — no backend delivery path", async () => {
    analyticsConnected()
    const { container } = mount()
    expect(screen.queryByText(/Microsoft Teams/)).toBeNull()
    expect(
      Array.from(container.querySelectorAll('[data-field="destination"] button')).map(
        (b) => b.textContent,
      ),
    ).toEqual(["Slack", "Email"])
  })

  it("Slack is never pre-selected — no destination chip is selected on arrival", async () => {
    analyticsConnected()
    mount()
    expect(chip("Slack").getAttribute("aria-pressed")).toBe("false")
    expect(chip("Email").getAttribute("aria-pressed")).toBe("false")
    // Nothing to connect and no channel picker until Slack is actually chosen.
    expect(screen.queryByTestId("slack-picker")).toBeNull()
    expect(screen.queryByTestId("connect-modal")).toBeNull()
  })

  it("picking Slack while it isn't connected opens the connect modal", async () => {
    // No wait for the list here, deliberately: this expectation holds whether
    // or not it has landed, because Slack is absent from it either way. Only
    // the test below — which needs a connection to be VISIBLE before it clicks
    // — has an ordering to get wrong.
    analyticsConnected() // no Slack connection in the connector list
    mount()
    fireEvent.click(chip("Slack"))

    expect(chip("Slack").getAttribute("aria-pressed")).toBe("true")
    const modal = screen.getByTestId("connect-modal")
    expect(modal.getAttribute("data-provider")).toBe("slack")
    // Not connected yet, so the picker doesn't render — the hint + its own
    // "Connect Slack" button do, as a second way in if the modal is dismissed.
    expect(screen.queryByTestId("slack-picker")).toBeNull()
    expect(screen.getByText(/Slack isn.t connected yet/)).not.toBeNull()
    expect(screen.getByText("Connect Slack")).not.toBeNull()
  })

  it("picking Slack while it IS connected shows the channel picker, no modal", async () => {
    // THE CLICK HAS TO LAND AFTER THE CONNECTIONS DO, and this used to wait
    // for the wrong thing. `toHaveBeenCalled()` is satisfied the moment the
    // request STARTS; the component only knows Slack is connected once that
    // promise resolves and `setConnections` runs. On a loaded CI runner the
    // click won that race, the step read Slack as unconnected, and it opened
    // the connect modal — a failure that never reproduced locally, where the
    // already-resolved mock always won.
    //
    // Awaiting the promise itself inside `act` is deterministic: it flushes
    // the resolution AND the React update it schedules, so there is no race
    // left to lose rather than a wider window to lose it in.
    const listed = slackConnected()
    mount()
    await act(async () => {
      await listed
    })
    fireEvent.click(chip("Slack"))

    expect(screen.getByTestId("slack-picker")).not.toBeNull()
    expect(screen.queryByTestId("connect-modal")).toBeNull()
  })

  it("with analytics live AND metrics picked, Continue hands off to define-metrics", async () => {
    // BOTH are required since 2026-09-03. The step that picked metrics was
    // removed, so a fresh signup reaches here with an empty list and the
    // sub-flow — which confirms a definition per picked metric — would open on
    // nothing. Metrics are chosen in Settings → KPI Settings now.
    analyticsConnected()
    const workspaceUnderTest = makeWorkspace({
      onboarding_step: 5,
      kpi_tree: {
        north_star: "",
        north_star_description: "",
        metrics: [{ name: "Activation rate", description: "" }],
      },
    })
    mount(workspaceUnderTest)
    await waitFor(() => expect(continueBtn().disabled).toBe(false))

    // Toggle one chip off and another on so the saved array isn't just defaults.
    fireEvent.click(chip("Top Customer Problem"))
    fireEvent.click(chip("Competitor & market moves"))

    await act(async () => {
      continueBtn().click()
    })

    await waitFor(() => {
      expect(routerMock.push).toHaveBeenCalledWith("/onboarding/define-metrics")
    })
    // The insight-type selection is WORKSPACE-level, persisted on
    // companies.notification_settings.brief_insight_types.
    const ns = updateWorkspaceMock.mock.calls[0][1].notification_settings
    // default ["top_problems","build_priorities"], toggled top_problems off +
    // competitor_moves on
    expect(ns.brief_insight_types).toEqual(["build_priorities", "competitor_moves"])
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

    // Wait for the button to be ENABLED, not merely labelled. `hasAnalytics`
    // starts null, which is falsy — so "Looks right · enter Sprntly" renders on
    // the very first paint, while `continueDisabled` (saving || hasAnalytics ===
    // null) still holds the button shut until the connector probe resolves.
    // Matching the label alone let the click land on a disabled button, where it
    // was swallowed and `replace` never ran; the test then failed on whether the
    // probe happened to win the race, which it lost on a loaded CI runner. The
    // sibling probe-failure test below already waits on `disabled` this way.
    await waitFor(() => {
      expect(continueBtn().disabled).toBe(false)
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
