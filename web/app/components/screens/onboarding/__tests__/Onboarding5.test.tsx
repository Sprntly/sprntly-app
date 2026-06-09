// View tests for onboarding step 05 — "Set your success metrics."
// renderToStaticMarkup pattern (node-env, no jsdom, no hooks): the stateful
// container wires hooks, while SuccessMetricsView is pure and renders to
// static markup directly. Product name/website are captured on step 1 (the
// single source of truth) and must NOT be re-collected here.
import * as React from "react"
import { renderToStaticMarkup } from "react-dom/server"
import { describe, expect, it } from "vitest"

;(globalThis as typeof globalThis & { React?: typeof React }).React = React

import { SuccessMetricsView, type SuccessMetricsViewProps } from "../Onboarding5"

function noop() {}

function render(override: Partial<SuccessMetricsViewProps> = {}): string {
  const defaults: SuccessMetricsViewProps = {
    productName: "Acme",
    industry: "B2B SaaS",
    northStar: "",
    northStarDescription: "",
    supporting: [],
    customMetric: "",
    northStarHints: ["Net revenue retention", "Weekly active teams"],
    supportingHints: ["Activation rate (week 2)", "Weekly active users"],
    errors: {},
    error: null,
    onChangeNorthStar: noop,
    onChangeNorthStarDescription: noop,
    onPickNorthStar: noop,
    onToggleSupporting: noop,
    onChangeSupportingDescription: noop,
    onChangeCustomMetric: noop,
    onAddCustom: noop,
  }
  return renderToStaticMarkup(
    React.createElement(SuccessMetricsView, { ...defaults, ...override }),
  )
}

describe("SuccessMetricsView — no product re-collection", () => {
  it("does NOT render product name / website inputs (captured on step 1)", () => {
    const html = render()
    expect(html).not.toContain("Product name")
    expect(html).not.toContain("Product website")
    expect(html).not.toContain('type="url"')
    expect(html).not.toContain("yourproduct.com")
  })

  it("shows the product name read-only for context", () => {
    const html = render({ productName: "Acme" })
    expect(html).toContain("Success metrics for")
    expect(html).toContain("Acme")
  })
})

describe("SuccessMetricsView — North Star + supporting metrics", () => {
  it("renders the required North Star input with industry-tailored hints", () => {
    const html = render()
    expect(html).toContain("your North Star")
    expect(html).toContain("Common for B2B SaaS")
    expect(html).toContain("Net revenue retention")
  })

  it("renders the supporting-metric chips and a custom-metric input", () => {
    const html = render()
    expect(html).toContain("Supporting metrics")
    expect(html).toContain("Activation rate (week 2)")
    expect(html).toContain("Or write your own")
  })

  it("marks selected supporting chips and counts them", () => {
    const html = render({ supporting: [{ name: "Weekly active users", description: "" }] })
    expect(html).toContain("selected")
    expect(html).toContain("1 supporting metric")
  })
})

describe("SuccessMetricsView — metric descriptions, no numeric inputs", () => {
  it("renders a North Star description textarea", () => {
    const html = render()
    expect(html).toContain("<textarea")
    expect(html).toContain("Describe what this metric means")
  })

  it("renders a description textarea + label for each selected supporting metric", () => {
    const html = render({
      supporting: [
        { name: "Activation rate (week 2)", description: "Reach value fast." },
      ],
    })
    expect(html).toContain('data-metric="Activation rate (week 2)"')
    expect(html).toContain("Reach value fast.")
    expect(html).toContain("Describe what this metric means and why it matters")
  })

  it("renders NO weight / current-value / target-value inputs", () => {
    const html = render({
      supporting: [{ name: "Activation rate (week 2)", description: "" }],
    })
    expect(html).not.toContain('type="number"')
    expect(html).not.toContain("Weight")
    expect(html).not.toContain("Current (optional)")
    expect(html).not.toContain("Target (optional)")
    expect(html).not.toContain("placeholder=\"Weight\"")
  })

  it("surfaces a North Star validation error", () => {
    const html = render({
      errors: { northStar: "Set a North Star metric to anchor your KPI tree." },
    })
    expect(html).toContain("Set a North Star metric to anchor your KPI tree.")
    expect(html).toContain("has-error")
  })

  it("renders an error banner when error is set", () => {
    const html = render({ error: "Save failed" })
    expect(html).toContain("ob-form-error")
    expect(html).toContain("Save failed")
  })
})
