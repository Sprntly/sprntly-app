// @vitest-environment jsdom
//
// Integrity tests for the semantic-slug onboarding flow (v6 screenshot spec
// 2026-07-17 + the restored optional api-key step 2026-07-19, 10 steps):
//   company → product → metrics → api-key → connectors → team → strategy →
//   decisions → invite → review
// (api-key is an OPTIONAL/skippable step — also editable in Settings → Admin.
//  Still retired: the closing workspace-naming step. The review step closes the
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
  ApiKey: () => React.createElement("div", { "data-screen": "api-key" }),
  Connectors: () => React.createElement("div", { "data-screen": "connectors" }),
  WorkspaceStep: () => React.createElement("div", { "data-screen": "workspace" }),
  InviteStep: () => React.createElement("div", { "data-screen": "invite" }),
  ReviewStep: () => React.createElement("div", { "data-screen": "review" }),
  PersonalizeStep: () =>
    React.createElement("div", { "data-screen": "personalize" }),
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
  "api-key",
  "connectors",
  "workspace",
  "invite",
  "review",
  "personalize",
] as const

describe("onboarding flow order — slug → screen", () => {
  it("ONBOARDING_STEP_SLUGS holds exactly the 9 numbered steps in flow order", () => {
    expect(ONBOARDING_STEP_COUNT).toBe(9)
    expect([...ONBOARDING_STEP_SLUGS]).toEqual([...EXPECTED_ORDER])
  })

  it("renders the closing personalize page at the 'personalize' slug (the last step)", () => {
    const { container } = render(
      React.createElement(OnboardingStep, { slug: "personalize" }),
    )
    expect(container.querySelector('[data-screen="personalize"]')).not.toBeNull()
    expect(ONBOARDING_STEP_SLUGS[ONBOARDING_STEP_COUNT - 1]).toBe("personalize")
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

  it("does not expose the dropped/folded pages as steps", () => {
    // api-key is a numbered step. `workspace` IS a step now, but it is the
    // merged team/strategy/decisions card — the three slugs it replaced must
    // not resolve. business-info split into company/product/metrics; the
    // business-context review became the numbered review step;
    // first-brief/coworkers stay retired.
    for (const slug of [
      "team",
      "strategy",
      "decisions",
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

  it("never renders a 'Skip to end' header link (removed 2026-07-17)", () => {
    const { container } = render(
      React.createElement(OnboardingChrome, {
        step: 2,
        title: "T",
        children: null,
        onContinue: vi.fn(),
      }),
    )
    expect(
      Array.from(container.querySelectorAll("button")).some((b) =>
        /Skip to end/.test(b.textContent ?? ""),
      ),
    ).toBe(false)
  })
})
