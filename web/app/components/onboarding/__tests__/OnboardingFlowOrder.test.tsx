// @vitest-environment jsdom
//
// Integrity tests for the semantic-slug onboarding flow (v6 screenshot spec
// 2026-07-17, 9 steps):
//   company → product → metrics → connectors → team → strategy → decisions →
//   invite → review
// (Retired in v6: the api-key step — Settings → Admin — and the closing
//  workspace-naming step; the new decisions/invite/review steps close the
//  numbered flow, then the UNNUMBERED define-metrics sub-flow completes
//  onboarding.)
//
// Asserts the slug→screen map renders the right component per numbered step (in
// the right order, no gaps), that an unknown slug falls back to the first step,
// that the dropped pages are unreachable, and that the progress chrome renders
// exactly ONBOARDING_STEP_COUNT dots with the active one matching the step.
import * as React from "react"
import { cleanup, render } from "@testing-library/react"
import { afterEach, describe, expect, it, vi } from "vitest"

;(globalThis as typeof globalThis & { React?: typeof React }).React = React

const routerMock = { push: vi.fn(), replace: vi.fn() }
vi.mock("next/navigation", () => ({ useRouter: () => routerMock }))

// Stub each screen with a marker so we can assert which one a route renders,
// without dragging in their hooks/contexts.
vi.mock("../../screens/onboarding", () => ({
  CompanyStep: () => React.createElement("div", { "data-screen": "company" }),
  ProductStep: () => React.createElement("div", { "data-screen": "product" }),
  MetricsStep: () => React.createElement("div", { "data-screen": "metrics" }),
  Connectors: () => React.createElement("div", { "data-screen": "connectors" }),
  TeamStep: () => React.createElement("div", { "data-screen": "team" }),
  Strategy: () => React.createElement("div", { "data-screen": "strategy" }),
  DecisionsStep: () => React.createElement("div", { "data-screen": "decisions" }),
  InviteStep: () => React.createElement("div", { "data-screen": "invite" }),
  ReviewStep: () => React.createElement("div", { "data-screen": "review" }),
}))

import { OnboardingStep } from "../../../(app)/onboarding/[slug]/OnboardingStep"
import { OnboardingChrome } from "../OnboardingChrome"
import {
  ONBOARDING_STEP_COUNT,
  ONBOARDING_STEP_SLUGS,
} from "../../../lib/onboarding/types"

afterEach(() => {
  cleanup()
  routerMock.push.mockClear()
  routerMock.replace.mockClear()
})

// The expected slug → screen order is exactly ONBOARDING_STEP_SLUGS (each slug
// renders the screen with the same data-screen marker).
const EXPECTED_ORDER = [
  "company",
  "product",
  "metrics",
  "connectors",
  "team",
  "strategy",
  "decisions",
  "invite",
  "review",
] as const

describe("onboarding flow order — slug → screen", () => {
  it("ONBOARDING_STEP_SLUGS holds exactly the 9 numbered steps in flow order", () => {
    expect(ONBOARDING_STEP_COUNT).toBe(9)
    expect([...ONBOARDING_STEP_SLUGS]).toEqual([...EXPECTED_ORDER])
  })

  it("renders the closing review page at the 'review' slug (the last step)", () => {
    const { container } = render(
      React.createElement(OnboardingStep, { slug: "review" }),
    )
    expect(container.querySelector('[data-screen="review"]')).not.toBeNull()
    expect(ONBOARDING_STEP_SLUGS[ONBOARDING_STEP_COUNT - 1]).toBe("review")
  })

  it("maps every numbered slug to the expected screen, in order, with no gaps", () => {
    ONBOARDING_STEP_SLUGS.forEach((slug, i) => {
      const { container, unmount } = render(
        React.createElement(OnboardingStep, { slug }),
      )
      const el = container.querySelector("[data-screen]") as HTMLElement
      expect(el).not.toBeNull()
      expect(el.getAttribute("data-screen")).toBe(EXPECTED_ORDER[i])
      unmount()
    })
  })

  it("falls back to the first step (in an effect) for an unknown slug", () => {
    const { container } = render(
      React.createElement(OnboardingStep, { slug: "does-not-exist" }),
    )
    // renders nothing for the unknown slug...
    expect(container.querySelector("[data-screen]")).toBeNull()
    // ...and redirects to the first numbered step.
    expect(routerMock.replace).toHaveBeenCalledWith(
      `/onboarding/${ONBOARDING_STEP_SLUGS[0]}`,
    )
  })

  it("does not expose the dropped api-key/workspace/business-info/first-brief/coworkers pages as steps", () => {
    // api-key moved to Settings → Admin and the workspace-naming closer was
    // retired in v6; business-info split into company/product/metrics; the
    // business-context review became the numbered review step;
    // first-brief/coworkers stay retired.
    for (const slug of [
      "api-key",
      "workspace",
      "business-info",
      "strategic-context",
      "first-brief",
      "optimizing",
      "coworkers",
    ]) {
      const { container, unmount } = render(
        React.createElement(OnboardingStep, { slug }),
      )
      expect(container.querySelector("[data-screen]")).toBeNull()
      unmount()
    }
  })

  it("does NOT render an analyzing screen (the loader route was removed)", () => {
    // `analyzing` is no longer a route at all — it isn't in the numbered [slug]
    // map, so an unknown slug renders nothing (and bounces to the first step).
    const { container } = render(
      React.createElement(OnboardingStep, { slug: "analyzing" }),
    )
    expect(container.querySelector('[data-screen="analyzing"]')).toBeNull()
  })

  it("does NOT render the define-metrics sub-flow as a numbered step (own route, no dots)", () => {
    // /onboarding/define-metrics is an unnumbered route with its own page —
    // it must not resolve through the numbered [slug] map.
    expect([...ONBOARDING_STEP_SLUGS]).not.toContain("define-metrics")
    const { container } = render(
      React.createElement(OnboardingStep, { slug: "define-metrics" }),
    )
    expect(container.querySelector("[data-screen]")).toBeNull()
  })
})

describe("OnboardingChrome — progress dots", () => {
  it("renders exactly ONBOARDING_STEP_COUNT dots", () => {
    const { container } = render(
      React.createElement(OnboardingChrome, {
        step: 1,
        title: "T",
        children: null,
      }),
    )
    expect(container.querySelectorAll(".onb-dots .od").length).toBe(
      ONBOARDING_STEP_COUNT,
    )
  })

  it("marks the current step active and prior steps done", () => {
    const { container } = render(
      React.createElement(OnboardingChrome, {
        step: 2,
        title: "T",
        children: null,
      }),
    )
    const dots = Array.from(container.querySelectorAll(".onb-dots .od"))
    expect(dots[0].className).toContain("done")
    expect(dots[1].className).toContain("cur")
    expect(dots[2].className).not.toContain("cur")
  })

  it("renders Back/Continue only when handlers are provided (back-next integrity)", () => {
    const onBack = vi.fn()
    const onContinue = vi.fn()
    const { container } = render(
      React.createElement(OnboardingChrome, {
        step: 2,
        title: "T",
        children: null,
        onBack,
        onContinue,
      }),
    )
    const labels = Array.from(container.querySelectorAll("button")).map((b) =>
      (b.textContent ?? "").trim(),
    )
    expect(labels.some((l) => /Back/.test(l))).toBe(true)
    expect(labels.some((l) => /Continue/.test(l))).toBe(true)
  })

  it("omits Back when no onBack is given (e.g. the first step)", () => {
    const { container } = render(
      React.createElement(OnboardingChrome, {
        step: 1,
        title: "T",
        children: null,
        onContinue: vi.fn(),
      }),
    )
    const labels = Array.from(container.querySelectorAll("button")).map((b) =>
      (b.textContent ?? "").trim(),
    )
    expect(labels.some((l) => /^Back$/.test(l))).toBe(false)
  })

  it("renders the 'Skip to end ⇥' header link only when onSkipToEnd is provided", () => {
    const onSkipToEnd = vi.fn()
    const withSkip = render(
      React.createElement(OnboardingChrome, {
        step: 2,
        title: "T",
        children: null,
        onSkipToEnd,
      }),
    )
    const skipBtn = Array.from(withSkip.container.querySelectorAll("button")).find(
      (b) => /Skip to end/.test(b.textContent ?? ""),
    ) as HTMLButtonElement
    expect(skipBtn).not.toBeUndefined()
    skipBtn.click()
    expect(onSkipToEnd).toHaveBeenCalledTimes(1)
    withSkip.unmount()

    const withoutSkip = render(
      React.createElement(OnboardingChrome, {
        step: 2,
        title: "T",
        children: null,
      }),
    )
    expect(
      Array.from(withoutSkip.container.querySelectorAll("button")).some((b) =>
        /Skip to end/.test(b.textContent ?? ""),
      ),
    ).toBe(false)
  })
})
