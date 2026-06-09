// @vitest-environment jsdom
//
// Integrity tests for the semantic-slug onboarding flow:
//   business-info → [analyzing] → metrics → connectors → coworkers → first-brief
//
// Asserts the slug→screen map renders the right component per numbered step (in
// the right order, no gaps), that an unknown slug falls back to the first step,
// that the dropped pages are unreachable, that the progress chrome renders
// exactly ONBOARDING_STEP_COUNT dots with the active one matching the step, and
// that the loader (analyzing) is NOT a counted step.
import * as React from "react"
import { cleanup, render } from "@testing-library/react"
import { afterEach, describe, expect, it, vi } from "vitest"

;(globalThis as typeof globalThis & { React?: typeof React }).React = React

const routerMock = { push: vi.fn(), replace: vi.fn() }
vi.mock("next/navigation", () => ({ useRouter: () => routerMock }))

// Stub each screen with a marker so we can assert which one a route renders,
// without dragging in their hooks/contexts.
vi.mock("../../screens/onboarding", () => ({
  BusinessInfo: () => React.createElement("div", { "data-screen": "business-info" }),
  Metrics: () => React.createElement("div", { "data-screen": "metrics" }),
  Connectors: () => React.createElement("div", { "data-screen": "connectors" }),
  Coworkers: () => React.createElement("div", { "data-screen": "coworkers" }),
  FirstBrief: () => React.createElement("div", { "data-screen": "first-brief" }),
  Analyzing: () => React.createElement("div", { "data-screen": "analyzing" }),
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
  "business-info",
  "metrics",
  "connectors",
  "coworkers",
  "first-brief",
] as const

describe("onboarding flow order — slug → screen", () => {
  it("ONBOARDING_STEP_SLUGS holds exactly the 5 numbered steps in flow order", () => {
    expect(ONBOARDING_STEP_COUNT).toBe(5)
    expect([...ONBOARDING_STEP_SLUGS]).toEqual([...EXPECTED_ORDER])
  })

  it("renders the metrics page at the 'metrics' slug (right after the loader)", () => {
    const { container } = render(
      React.createElement(OnboardingStep, { slug: "metrics" }),
    )
    expect(container.querySelector('[data-screen="metrics"]')).not.toBeNull()
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

  it("does not expose the dropped strategic/business-context pages as steps", () => {
    for (const slug of ["strategic-context", "business-context", "optimizing"]) {
      const { container, unmount } = render(
        React.createElement(OnboardingStep, { slug }),
      )
      expect(container.querySelector("[data-screen]")).toBeNull()
      unmount()
    }
  })

  it("does NOT render the analyzing loader from the numbered slug map", () => {
    // analyzing is its own route, not part of the numbered [slug] map.
    const { container } = render(
      React.createElement(OnboardingStep, { slug: "analyzing" }),
    )
    expect(container.querySelector('[data-screen="analyzing"]')).toBeNull()
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
})
