// View + formatting tests for the Usage pane (LLM spend and token usage).
import * as React from "react"
import { renderToStaticMarkup } from "react-dom/server"
import { describe, expect, it } from "vitest"

;(globalThis as typeof globalThis & { React?: typeof React }).React = React

import {
  UsageSettingsView,
  buildChartPoints,
  featureLabel,
  formatCount,
  formatUsd,
} from "../UsageSettings"
import type { UsageBucket, UsageSummary } from "../../../../../lib/api"

function noop() {}

const ZERO: UsageBucket = {
  calls: 0,
  failed_calls: 0,
  input_tokens: 0,
  output_tokens: 0,
  cache_creation_input_tokens: 0,
  cache_read_input_tokens: 0,
  est_cost_usd: 0,
}

function bucket(over: Partial<UsageBucket> = {}): UsageBucket {
  return { ...ZERO, ...over }
}

function summary(over: Partial<UsageSummary> = {}): UsageSummary {
  return {
    range: {
      start: "2026-06-26T00:00:00Z",
      end: "2026-07-26T00:00:00Z",
      days: 30,
      tz: "UTC",
    },
    cost_basis: "estimated_from_tokens",
    scope: "customer_key",
    totals: bucket({
      calls: 120,
      failed_calls: 3,
      input_tokens: 1_500_000,
      output_tokens: 250_000,
      est_cost_usd: 8.25,
    }),
    daily: [
      { ...bucket({ calls: 40, est_cost_usd: 2.0 }), day: "2026-07-24" },
      { ...bucket({ calls: 0 }), day: "2026-07-25" },
      { ...bucket({ calls: 80, est_cost_usd: 6.25 }), day: "2026-07-26" },
    ],
    by_feature: [
      { ...bucket({ calls: 60, est_cost_usd: 6.0 }), feature: "design_agent" },
      { ...bucket({ calls: 60, est_cost_usd: 2.25 }), feature: "prd" },
    ],
    by_model: [
      { ...bucket({ calls: 120, est_cost_usd: 8.25 }), model: "claude-sonnet-4-6" },
    ],
    by_provider: [],
    by_operation: [],
    ...over,
  }
}

function render(
  override: Partial<React.ComponentProps<typeof UsageSettingsView>> = {},
): string {
  return renderToStaticMarkup(
    <UsageSettingsView
      data={summary()}
      days={30}
      view="daily"
      keyConfigured={true}
      restricted={false}
      loading={false}
      error={null}
      onRangeChange={noop}
      onViewChange={noop}
      {...override}
    />,
  )
}

describe("formatUsd", () => {
  it("keeps precision on small amounts so they don't read as free", () => {
    // $0.004 rounded to 2dp would render "$0.00" on a page about cost.
    expect(formatUsd(0.004)).toBe("$0.00400")
    expect(formatUsd(0.0105)).toBe("$0.011")
    expect(formatUsd(12.5)).toBe("$12.50")
    expect(formatUsd(0)).toBe("$0.00")
  })
})

describe("formatCount", () => {
  it("abbreviates large counts", () => {
    expect(formatCount(950)).toBe("950")
    expect(formatCount(1500)).toBe("1.5K")
    expect(formatCount(2_400_000)).toBe("2.4M")
  })
})

describe("featureLabel", () => {
  it("maps known slugs to product language", () => {
    expect(featureLabel("design_agent")).toBe("Prototype generation")
    expect(featureLabel("prd")).toBe("PRD generation")
  })

  it("de-slugifies unknown features instead of dropping them", () => {
    // A newly-added agent must stay readable before anyone updates the map.
    expect(featureLabel("brand_new_thing")).toBe("Brand new thing")
  })
})

describe("buildChartPoints", () => {
  it("maps the largest value to the top of the plot and zero to the baseline", () => {
    const pts = buildChartPoints(
      [
        { day: "a", est_cost_usd: 0 },
        { day: "b", est_cost_usd: 10 },
      ],
      10,
    )
    expect(pts).toHaveLength(2)
    expect(pts[1].y).toBeLessThan(pts[0].y) // larger value plots higher
    expect(pts[0].x).toBeLessThan(pts[1].x) // days advance left to right
  })

  it("does not divide by zero when every day is empty", () => {
    const pts = buildChartPoints(
      [
        { day: "a", est_cost_usd: 0 },
        { day: "b", est_cost_usd: 0 },
      ],
      0,
    )
    expect(pts.every((p) => Number.isFinite(p.y))).toBe(true)
  })
})

describe("UsageSettingsView", () => {
  it("shows a restricted message for non-admins and no spend figures", () => {
    const html = render({ restricted: true })
    expect(html).toMatch(/restricted to owners and admins/i)
    expect(html).not.toMatch(/\$8\.25/)
  })

  it("renders the estimated total and headline stats", () => {
    const html = render()
    expect(html).toMatch(/\$8\.25/)
    expect(html).toMatch(/Estimated spend/i)
    expect(html).toMatch(/1\.5M/) // input tokens
  })

  it("always labels the money as estimated, never as billed", () => {
    const html = render()
    expect(html).toMatch(/estimated/i)
    expect(html).not.toMatch(/billed amount(?!s)/i)
    // The footnote points at the authoritative source.
    expect(html).toMatch(/Anthropic console/i)
  })

  it("frames the total as spend on the customer's OWN key", () => {
    const html = render()
    expect(html).toMatch(/Estimated spend on your key/i)
    expect(html).toMatch(/billed to your account/i)
  })

  it("explains that nothing is billed when no key is saved", () => {
    // The point of the whole surface: with no key of their own, every call runs
    // on Sprntly's key at our cost — showing a spend figure would invent a bill.
    const html = render({ keyConfigured: false })
    expect(html).toMatch(/No API key saved/i)
    expect(html).toMatch(/at our cost/i)
    // No figures at all — not even a zero, which would imply we measured usage
    // that belonged to them.
    expect(html).not.toMatch(/\$8\.25/)
    expect(html).not.toMatch(/usage-hero-value/)
  })

  it("explains an unused key rather than showing a blank chart", () => {
    const html = render({
      keyConfigured: true,
      data: summary({ totals: bucket() }),
    })
    expect(html).toMatch(/No usage on your key in this period/i)
    // Sets the expectation that pre-key activity is deliberately not counted.
    expect(html).toMatch(/was on our key/i)
  })

  it("offers both breakdown views alongside the date range", () => {
    const html = render()
    // Two independent pill groups on one toolbar.
    expect(html).toMatch(/aria-label="Date range"/)
    expect(html).toMatch(/aria-label="Breakdown"/)
    expect(html).toMatch(/>Daily</)
    expect(html).toMatch(/>By feature</)
    expect(html).toMatch(/>By model</)
  })

  it("shows the daily chart by default, not a breakdown", () => {
    const html = render()
    expect(html).toMatch(/Estimated daily spend/)
    expect(html).toMatch(/usage-chart-svg/)
    expect(html).not.toMatch(/usage-bar-row/)
  })

  it("swaps the chart for feature bars when the feature view is active", () => {
    const html = render({ view: "feature" })
    expect(html).toMatch(/Estimated spend by feature/)
    expect(html).toMatch(/usage-bar-row/)
    // The line chart is replaced, not stacked below.
    expect(html).not.toMatch(/usage-chart-svg/)
    // Features are labelled in product language, with detail visible without hover.
    expect(html).toMatch(/Prototype generation/)
    expect(html).toMatch(/PRD generation/)
    expect(html).toMatch(/60 calls/)
  })

  it("swaps the chart for model bars when the model view is active", () => {
    const html = render({ view: "model" })
    expect(html).toMatch(/Estimated spend by model/)
    expect(html).toMatch(/claude-sonnet-4-6/)
    expect(html).toMatch(/120 calls/)
    expect(html).not.toMatch(/usage-chart-svg/)
  })

  it("marks the active view and leaves the range untouched", () => {
    const html = render({ view: "model", days: 7 })
    // Both selections coexist — the two groups are independent.
    expect(html).toMatch(/aria-pressed="true">7 days</)
    expect(html).toMatch(/aria-pressed="true">By model</)
  })

  it("explains an empty breakdown instead of rendering a bare axis", () => {
    const html = render({
      data: summary({ by_model: [] }),
      view: "model",
    })
    expect(html).toMatch(/Nothing recorded in this period/i)
  })

  it("marks the active range and offers the other windows", () => {
    const html = render({ days: 7 })
    expect(html).toMatch(/aria-pressed="true"[^>]*>7 days|7 days/)
    expect(html).toMatch(/30 days/)
    expect(html).toMatch(/90 days/)
  })

  it("surfaces a load error", () => {
    const html = render({ error: "Could not load usage." })
    expect(html).toMatch(/Could not load usage/)
  })
})
