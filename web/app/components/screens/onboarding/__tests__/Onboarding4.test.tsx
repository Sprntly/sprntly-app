// View tests for onboarding step 04 — "Set your success metrics" (the single
// consolidated metrics page). renderToStaticMarkup pattern (node-env, no
// jsdom, no hooks): the stateful container wires hooks, while MetricsSetupView
// is pure and renders to static markup directly.
import * as React from "react"
import { renderToStaticMarkup } from "react-dom/server"
import { describe, expect, it } from "vitest"

;(globalThis as typeof globalThis & { React?: typeof React }).React = React

import { MetricsSetupView, type MetricsSetupViewProps } from "../Onboarding4"
import type { SuggestedMetric } from "../../../../lib/api"

function noop() {}

const SUGGESTED: SuggestedMetric[] = [
  { metric: "Reconciled volume", description: "Total $ reconciled / week." },
  { metric: "Active accounts", description: "Accounts with a live sync." },
]

function render(override: Partial<MetricsSetupViewProps> = {}): string {
  const defaults: MetricsSetupViewProps = {
    industry: "Fintech",
    businessType: "Marketplace",
    northStar: "",
    northStarDescription: "",
    northStarHints: ["Net revenue retention", "Activated accounts"],
    suggestedMetrics: SUGGESTED,
    supporting: [],
    customMetric: "",
    customDescription: "",
    errors: {},
    error: null,
    onChangeIndustry: noop,
    onChangeBusinessType: noop,
    onChangeNorthStar: noop,
    onChangeNorthStarDescription: noop,
    onPickNorthStar: noop,
    onToggleSuggested: noop,
    onChangeSupportingDescription: noop,
    onRemoveSupporting: noop,
    onChangeCustomMetric: noop,
    onChangeCustomDescription: noop,
    onAddCustom: noop,
  }
  return renderToStaticMarkup(
    React.createElement(MetricsSetupView, { ...defaults, ...override }),
  )
}

describe("MetricsSetupView — suggested metrics (selectable, metric-tree)", () => {
  it("renders each suggested metric in the metric-tree as a selectable option", () => {
    const html = render()
    expect(html).toContain("Supporting metrics")
    expect(html).toContain("metric-tree")
    expect(html).toContain("mt-suggested")
    expect(html).toContain("Reconciled volume")
    // description is carried as the option's title (hover) text
    expect(html).toContain('title="Total $ reconciled / week."')
    expect(html).toContain("Active accounts")
    // selectable buttons carry aria-pressed
    expect(html).toContain('aria-pressed="false"')
    expect(html).toContain('data-metric="Reconciled volume"')
  })

  it("marks a suggested metric's chip as selected when it's in `supporting`", () => {
    const html = render({
      supporting: [{ name: "Reconciled volume", description: "Total $ reconciled / week." }],
    })
    expect(html).toContain('aria-pressed="true"')
    expect(html).toContain("metric mt-suggested sel")
    expect(html).toContain("1</strong> supporting metric selected")
  })

  it("falls back to an add-your-own prompt when there are NO suggestions", () => {
    const html = render({ suggestedMetrics: [] })
    expect(html).toContain("No suggestions yet")
    expect(html).toContain("Or write your own")
  })
})

describe("MetricsSetupView — selected metrics render as tree targets", () => {
  it("renders each selected supporting metric as a tree target with name, editable description, and a delete control", () => {
    const html = render({
      supporting: [{ name: "Reconciled volume", description: "Weekly total." }],
    })
    // targets live inside the metric-tree (source → targets), not a separate block
    expect(html).toContain("metric-tree")
    expect(html).toContain('class="mt-targets mt-targets-cards"')
    expect(html).toContain('class="mt-target"')
    expect(html).toContain('data-metric="Reconciled volume"')
    // name + editable description textarea
    expect(html).toContain("Reconciled volume")
    expect(html).toContain('aria-label="Description for Reconciled volume"')
    expect(html).toContain("Weekly total.")
    // delete control
    expect(html).toContain('aria-label="Remove Reconciled volume"')
    expect(html).toMatch(/<button[^>]*type="button"[^>]*aria-label="Remove Reconciled volume"/)
    // the old separate bottom cards section is gone
    expect(html).not.toContain("metric-desc-block")
  })

  it("shows a targets empty state (not the bottom cards) when nothing is selected", () => {
    const html = render({ supporting: [] })
    expect(html).toContain("mt-targets-empty")
    expect(html).not.toContain('class="mt-target"')
    expect(html).toContain("0</strong> supporting metrics selected")
  })
})

describe("MetricsSetupView — add your own (metric-other)", () => {
  it("renders the custom metric name + description inputs and an Add button", () => {
    const html = render()
    expect(html).toContain("metric-other")
    expect(html).toContain("Or write your own")
    expect(html).toContain('aria-label="Custom metric name"')
    expect(html).toContain('aria-label="Custom metric description"')
    expect(html).toContain("Add</button>")
  })

  it("disables Add when the custom name is empty", () => {
    const html = render({ customMetric: "" })
    expect(html).toMatch(/<button[^>]*disabled/)
  })
})

describe("MetricsSetupView — editable industry / business-type dropdowns", () => {
  it("renders BOTH as <select> dropdowns pre-filled with the predicted values", () => {
    const html = render({ industry: "Fintech", businessType: "Marketplace" })
    expect(html).toContain('aria-label="Industry"')
    expect(html).toContain('aria-label="Business type"')
    // pre-filled selection surfaces as the selected option
    expect(html).toMatch(/<option[^>]*selected[^>]*>Fintech<\/option>/)
    expect(html).toMatch(/<option[^>]*selected[^>]*>Marketplace<\/option>/)
    // and they're editable (not disabled / not read-only text)
    expect(html).not.toMatch(/<select[^>]*disabled/)
    expect(html).toContain("predicted from your website")
  })
})

describe("MetricsSetupView — North Star", () => {
  it("renders the required North Star input with industry-tailored hints", () => {
    const html = render()
    expect(html).toContain("your North Star")
    expect(html).toContain("Common for Fintech")
    expect(html).toContain("Net revenue retention")
  })

  it("surfaces a North Star validation error", () => {
    const html = render({
      errors: { northStar: "Set a North Star metric to anchor your KPI tree." },
    })
    expect(html).toContain("Set a North Star metric to anchor your KPI tree.")
    expect(html).toContain("has-error")
  })

  it("renders a description textarea for each selected supporting metric", () => {
    const html = render({
      supporting: [{ name: "Reconciled volume", description: "Weekly total." }],
    })
    expect(html).toContain('data-metric="Reconciled volume"')
    expect(html).toContain("Weekly total.")
  })

  it("renders an error banner when error is set", () => {
    const html = render({ error: "Save failed" })
    expect(html).toContain("onb-form-error")
    expect(html).toContain("Save failed")
  })
})
