// View tests for the onboarding metrics page — "Set your success metrics" (the
// REDESIGNED pick-exactly-3 page). renderToStaticMarkup pattern (node-env, no
// jsdom, no hooks): the stateful container wires hooks, while MetricsSetupView
// is pure and renders to static markup directly.
//
// The redesign DROPS the explicit North-Star input and the supporting-metric
// split. Instead it shows a FLAT list of candidate metric cards; the user picks
// exactly 3, and selected cards carry the green `.sel` selected state. The
// North Star is inferred server-side, so there is NO north-star input here.
import * as React from "react"
import { renderToStaticMarkup } from "react-dom/server"
import { describe, expect, it } from "vitest"

;(globalThis as typeof globalThis & { React?: typeof React }).React = React

import { MetricsSetupView, type MetricsSetupViewProps } from "../Metrics"

function noop() {}

function render(override: Partial<MetricsSetupViewProps> = {}): string {
  const defaults: MetricsSetupViewProps = {
    industry: "Fintech",
    businessType: "Marketplace",
    candidates: [
      { name: "Reconciled volume", description: "Weekly total." },
      { name: "Active connected accounts", description: "Live syncs." },
      { name: "Incremental revenue", description: "" },
      { name: "Conversion rate", description: "" },
    ],
    selected: [],
    customMetric: "",
    errors: {},
    error: null,
    onChangeIndustry: noop,
    onChangeBusinessType: noop,
    onToggle: noop,
    onChangeCustomMetric: noop,
    onAddCustom: noop,
  }
  return renderToStaticMarkup(
    React.createElement(MetricsSetupView, { ...defaults, ...override }),
  )
}

describe("MetricsSetupView — NO explicit North Star / supporting split", () => {
  it("renders no north-star input and no supporting-metric section", () => {
    const html = render()
    // The dedicated North Star input + its hint chips are gone.
    expect(html).not.toContain("your North Star")
    expect(html).not.toContain("Common for")
    expect(html).not.toContain("The one metric that best captures product value")
    // The old "Supporting metrics" section + tree source are gone.
    expect(html).not.toContain("Supporting metrics")
    expect(html).not.toContain("mt-source")
    expect(html).not.toContain("Primary leads to")
  })

  it("prompts to pick exactly 3 metrics", () => {
    const html = render()
    expect(html).toContain("Pick your 3 success metrics")
  })
})

describe("MetricsSetupView — flat candidate list with green selected state", () => {
  it("renders every candidate as a toggleable card", () => {
    const html = render()
    expect(html).toContain('id="metricCandidates"')
    expect(html).toContain('data-metric="Reconciled volume"')
    expect(html).toContain('data-metric="Conversion rate"')
    // candidate descriptions surface when present
    expect(html).toContain("Weekly total.")
  })

  it("marks selected cards with the green .sel selected state", () => {
    const html = render({
      selected: ["Reconciled volume", "Incremental revenue"],
    })
    // selected cards carry .sel + aria-selected/aria-pressed true
    expect(html).toMatch(
      /<button[^>]*class="mt-target metric-card sel"[^>]*data-metric="Reconciled volume"/,
    )
    expect(html).toContain('aria-selected="true"')
    expect(html).toContain('aria-pressed="true"')
    // an unselected candidate is NOT marked selected
    expect(html).toMatch(
      /<button[^>]*class="mt-target metric-card "[^>]*data-metric="Conversion rate"/,
    )
  })

  it("disables unselected cards once 3 are already picked (but not the selected ones)", () => {
    const html = render({
      selected: ["Reconciled volume", "Active connected accounts", "Incremental revenue"],
    })
    // the 4th, unselected candidate is disabled
    expect(html).toMatch(
      /<button[^>]*data-metric="Conversion rate"[^>]*disabled/,
    )
    // a selected card stays enabled (toggle-off must remain possible)
    expect(html).not.toMatch(
      /<button[^>]*data-metric="Reconciled volume"[^>]*disabled/,
    )
  })
})

describe("MetricsSetupView — the pick counter", () => {
  it("shows N of 3 with a 'pick more' nudge below 3", () => {
    expect(render({ selected: [] })).toContain("0</strong> of 3 metric")
    expect(render({ selected: ["Reconciled volume"] })).toContain("pick 2 more")
  })

  it("shows a ready state at exactly 3", () => {
    const html = render({
      selected: ["Reconciled volume", "Active connected accounts", "Incremental revenue"],
    })
    expect(html).toContain("3</strong> of 3 metric")
    expect(html).toContain("ready")
  })

  it("surfaces the pick-exactly-3 validation error", () => {
    const html = render({
      errors: { metrics: "Pick exactly 3 metrics to continue." },
    })
    expect(html).toContain("Pick exactly 3 metrics to continue.")
  })
})

describe("MetricsSetupView — add your own + dropdowns", () => {
  it("renders the custom metric input and an Add button", () => {
    const html = render()
    expect(html).toContain("Or write your own")
    expect(html).toContain('aria-label="Custom metric name"')
    expect(html).toContain("Add</button>")
  })

  it("disables Add when the custom name is empty", () => {
    const html = render({ customMetric: "" })
    expect(html).toMatch(/<button[^>]*disabled/)
  })

  it("renders editable, pre-filled industry / business-type dropdowns", () => {
    const html = render({ industry: "Fintech", businessType: "Marketplace" })
    expect(html).toContain('aria-label="Industry"')
    expect(html).toContain('aria-label="Business type"')
    expect(html).toMatch(/<option[^>]*selected[^>]*>Fintech<\/option>/)
    expect(html).toMatch(/<option[^>]*selected[^>]*>Marketplace<\/option>/)
    expect(html).not.toMatch(/<select[^>]*disabled/)
    expect(html).toContain("Gaming / Entertainment")
  })

  it("renders an error banner when error is set", () => {
    const html = render({ error: "Save failed" })
    expect(html).toContain("onb-form-error")
    expect(html).toContain("Save failed")
  })
})
