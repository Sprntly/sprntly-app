// @vitest-environment jsdom
//
// Container mount test for onboarding step 09 — "Here's what we learned" (v6
// screenshot spec 2026-07-17, NEW closing numbered step). On mount it requests
// an AI business-context draft (onboardingApi.draftBusinessContext) unless the
// workspace already carries an accepted summary; the prose is fully editable.
// Continue saves via saveBusinessContextSummary, then branches on whether a
// LIVE analytics connection exists: with one it hands off to
// /onboarding/define-metrics ("Next · define metrics"); without one that
// sub-flow has nothing to detect, so this is the last screen and Continue
// ("Looks right · enter Sprntly") runs the shared closer and enters the app.
// A failed draft shows the manual-entry hint; Continue stays disabled while
// drafting, while the connector probe is unresolved, or while empty.
//
// Matchers: native DOM only.
import * as React from "react"
import { act, cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react"
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest"

;(globalThis as typeof globalThis & { React?: typeof React }).React = React

const authMock = vi.fn()
const onboardingMock = vi.fn()
const routerMock = { push: vi.fn(), replace: vi.fn() }
const saveSummaryMock = vi.fn()
const draftMock = vi.fn()
const setContentMock = vi.fn()
const connectorsListMock = vi.fn()
const finishMock = vi.fn()

vi.mock("../../../../lib/auth", () => ({ useAuth: () => authMock() }))
vi.mock("../../../../context/OnboardingContext", () => ({
  useOnboarding: () => onboardingMock(),
}))
vi.mock("../../../../context/ContentContext", () => ({
  useContent: () => ({ setContent: setContentMock }),
}))
vi.mock("next/navigation", () => ({ useRouter: () => routerMock }))
vi.mock("../../../../lib/onboarding/store", () => ({
  saveBusinessContextSummary: (...a: unknown[]) => saveSummaryMock(...a),
}))
vi.mock("../../../../lib/api", () => ({
  onboardingApi: { draftBusinessContext: (...a: unknown[]) => draftMock(...a) },
  connectorsApi: { list: (...a: unknown[]) => connectorsListMock(...a) },
}))
vi.mock("../../../../lib/onboarding/finishOnboarding", () => ({
  finishOnboardingAndEnterApp: (...a: unknown[]) => finishMock(...a),
  POST_ONBOARDING_PATH: "/settings",
}))
vi.mock("../../../../lib/onboarding/useFormDraft", () => ({
  saveDraft: vi.fn(),
  loadDraft: () => null,
  clearDraft: vi.fn(),
}))

import { ReviewStep } from "../ReviewStep"
import { _resetDraftPrefetchForTests } from "../../../../lib/onboarding/draftPrefetch"
import { makeWorkspace, makeOnboardingCtx } from "./fixtures"

const DRAFT_TEXT =
  "Acme helps SMBs reconcile payments across providers, monetized by subscription."

function mount(workspace = makeWorkspace({ onboarding_step: 9 })) {
  onboardingMock.mockReturnValue(makeOnboardingCtx({ workspace }))
  return render(React.createElement(ReviewStep))
}

function summaryTextarea(): HTMLTextAreaElement {
  return document.querySelector(
    'textarea[aria-label="Business context"]',
  ) as HTMLTextAreaElement
}

function accurateCheckbox(): HTMLInputElement {
  return document.querySelector('input[type="checkbox"]') as HTMLInputElement
}

function continueBtn(): HTMLButtonElement {
  return Array.from(document.querySelectorAll("button")).find((b) =>
    /Next · define metrics|Looks right · enter Sprntly/.test(b.textContent ?? ""),
  ) as HTMLButtonElement
}

/** A live Analytics connection — what keeps the define-metrics hand-off alive. */
function analyticsConnected() {
  connectorsListMock.mockResolvedValue({
    connections: [{ provider: "posthog", status: "active", types: ["analytics"] }],
  })
}

beforeEach(() => {
  _resetDraftPrefetchForTests()
  authMock.mockReturnValue({ kind: "authed", user: { id: "u-1" }, session: {} })
  // Default to analytics present so the define-metrics hand-off is exercised;
  // the no-analytics path is asserted explicitly below.
  analyticsConnected()
})
afterEach(() => {
  cleanup()
  vi.clearAllMocks()
})

describe("ReviewStep (onboarding step 09 — AI business context, review & accept)", () => {
  it("requests a draft on mount, shows the skeleton loading state, then fills the editable textarea", async () => {
    let resolveDraft: (v: { draft: string }) => void = () => {}
    draftMock.mockReturnValue(
      new Promise<{ draft: string }>((res) => {
        resolveDraft = res
      }),
    )
    const { container } = mount()

    // Step 9 of the dots. While drafting the card keeps its shape — a shimmer
    // standing in for the textarea plus an announced status — and Continue
    // stays disabled rather than sitting live over an empty page.
    expect(
      (container.querySelector(".onb-dots") as HTMLElement).getAttribute("data-step"),
    ).toBe("10")
    expect(draftMock).toHaveBeenCalledTimes(1)
    const skeleton = container.querySelector(".onb-draft-skel") as HTMLElement
    expect(skeleton).not.toBeNull()
    expect(skeleton.querySelectorAll(".assistant-skel-line").length).toBeGreaterThan(1)
    const status = screen.getByText(/Generating your business context/)
    expect(status.getAttribute("role")).toBe("status")
    // The real editor is withheld until the prose lands.
    expect(summaryTextarea()).toBeNull()
    expect(continueBtn().disabled).toBe(true)

    await act(async () => {
      resolveDraft({ draft: DRAFT_TEXT })
    })

    await waitFor(() => {
      expect(summaryTextarea()).not.toBeNull()
    })
    // Skeleton and status are gone once the real editor renders.
    expect(container.querySelector(".onb-draft-skel")).toBeNull()
    expect(screen.queryByText(/Generating your business context/)).toBeNull()
    expect(summaryTextarea().value).toBe(DRAFT_TEXT)
    // The prose is editable and the accept checkbox renders.
    fireEvent.change(summaryTextarea(), { target: { value: `${DRAFT_TEXT} Edited.` } })
    expect(summaryTextarea().value).toBe(`${DRAFT_TEXT} Edited.`)
    expect(accurateCheckbox()).not.toBeNull()
    expect(continueBtn().disabled).toBe(false)
  })

  it("does NOT request a draft when the workspace already has an accepted summary", () => {
    draftMock.mockResolvedValue({ draft: "unused" })
    mount(
      makeWorkspace({
        onboarding_step: 9,
        business_context_summary: "Already accepted prose.",
      }),
    )
    expect(draftMock).not.toHaveBeenCalled()
    expect(summaryTextarea().value).toBe("Already accepted prose.")
  })

  it("Continue saves the summary (+ accuracy flag) and routes to define-metrics", async () => {
    draftMock.mockResolvedValue({ draft: DRAFT_TEXT })
    saveSummaryMock.mockResolvedValue(
      makeWorkspace({
        onboarding_step: 9,
        business_context_summary: DRAFT_TEXT,
      }),
    )
    mount()

    await waitFor(() => {
      expect(summaryTextarea()).not.toBeNull()
    })
    fireEvent.click(accurateCheckbox())

    await act(async () => {
      continueBtn().click()
    })

    await waitFor(() => {
      expect(routerMock.push).toHaveBeenCalledWith("/onboarding/define-metrics")
    })
    expect(saveSummaryMock).toHaveBeenCalledWith("ws-1", DRAFT_TEXT, true)
  })

  it("a failed draft shows the manual-entry hint and Continue stays disabled until text is typed", async () => {
    draftMock.mockRejectedValue(new Error("llm down"))
    const { container } = mount()

    await waitFor(() => {
      expect(screen.getByText(/couldn't draft this automatically/i)).not.toBeNull()
    })
    // The skeleton clears on failure too — no shimmer left spinning forever.
    expect(container.querySelector(".onb-draft-skel")).toBeNull()
    // Empty textarea → Continue disabled.
    expect(summaryTextarea().value).toBe("")
    expect(continueBtn().disabled).toBe(true)

    fireEvent.change(summaryTextarea(), {
      target: { value: "We reconcile payments for SMBs." },
    })
    // Also waits out the connector probe, which gates Continue until resolved.
    await waitFor(() => {
      expect(continueBtn().disabled).toBe(false)
    })
  })

  it("with NO analytics connector, Continue finishes onboarding instead of routing to define-metrics", async () => {
    // Non-analytics live connection + a planned-but-not-live analytics one:
    // neither should keep the define-metrics sub-flow alive.
    connectorsListMock.mockResolvedValue({
      connections: [
        { provider: "github", status: "active", types: ["code"] },
        { provider: "mixpanel", status: "revoked", types: ["analytics"] },
      ],
    })
    draftMock.mockResolvedValue({ draft: DRAFT_TEXT })
    saveSummaryMock.mockResolvedValue(
      makeWorkspace({ onboarding_step: 9, business_context_summary: DRAFT_TEXT }),
    )
    finishMock.mockResolvedValue(undefined)
    mount()

    await waitFor(() => {
      expect(summaryTextarea()).not.toBeNull()
    })
    // The closing CTA reframes: this is now the last screen.
    await waitFor(() => {
      expect(continueBtn().textContent).toMatch(/Looks right · enter Sprntly/)
    })

    await act(async () => {
      continueBtn().click()
    })

    await waitFor(() => {
      expect(routerMock.replace).toHaveBeenCalledWith("/settings")
    })
    expect(saveSummaryMock).toHaveBeenCalledWith("ws-1", DRAFT_TEXT, false)
    expect(finishMock).toHaveBeenCalledTimes(1)
    // Never detours through the metrics sub-flow.
    expect(routerMock.push).not.toHaveBeenCalledWith("/onboarding/define-metrics")
  })

  it("Back routes to the invite step", async () => {
    draftMock.mockResolvedValue({ draft: DRAFT_TEXT })
    mount()
    await waitFor(() => {
      expect(summaryTextarea()).not.toBeNull()
    })
    fireEvent.click(screen.getByText("Back").closest("button") as HTMLElement)
    expect(routerMock.push).toHaveBeenCalledWith("/onboarding/invite")
  })

  it("shows the loading shell while the workspace is loading", () => {
    onboardingMock.mockReturnValue(makeOnboardingCtx({ loading: true, workspace: null }))
    render(React.createElement(ReviewStep))
    expect(screen.getByText("Loading…")).not.toBeNull()
  })

  it("redirects to step 1 from an EFFECT (never during render) when there is no workspace", () => {
    onboardingMock.mockReturnValue(makeOnboardingCtx({ workspace: null }))

    const errors: unknown[] = []
    const spy = vi
      .spyOn(console, "error")
      .mockImplementation((...args) => errors.push(args[0]))
    render(React.createElement(ReviewStep))
    spy.mockRestore()

    expect(routerMock.replace).toHaveBeenCalledWith("/onboarding/company")
    expect(screen.getByText("Loading…")).not.toBeNull()
    const sideEffectInRender = errors
      .map(String)
      .filter((m) => /while rendering a different component|Cannot update a component/.test(m))
    expect(sideEffectInRender).toEqual([])
  })
})
