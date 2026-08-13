/**
 * SSR safety.
 *
 * `vega-embed` touches `window`/`document`/`canvas` at module scope and is not
 * SSR-safe. Next prerenders every one of these client components at build time,
 * so if the vega import ever escapes the effect into module scope, `next build`
 * breaks — for the whole route, not just the chart. This runs in the `node`
 * environment (no DOM at all) so an accidental browser reference throws here
 * long before it reaches a build.
 */
import { describe, expect, it, vi } from "vitest"
// Classic JSX runtime (tsconfig `jsx: "preserve"`) needs a global React before
// the component modules evaluate.
vi.hoisted(() => {
  // eslint-disable-next-line @typescript-eslint/no-require-imports
  ;(globalThis as Record<string, unknown>).React = require("react")
})

import { renderToString } from "react-dom/server"
import { InlineChart } from "../InlineChart"
import { VegaChart } from "../VegaChart"
import type { PrdChartKind } from "../../../types/content"

// Loading either module during prerender is the failure this test exists for.
vi.mock("vega-embed", () => {
  throw new Error("vega-embed must never be imported during SSR")
})
vi.mock("vega-interpreter", () => {
  throw new Error("vega-interpreter must never be imported during SSR")
})

const DATA = [
  { label: "Checkout", value: 42 },
  { label: "Search", value: 17 },
]

const KINDS: PrdChartKind[] = ["bar", "line", "pie", "donut", "stat", "gauge"]

describe("server rendering", () => {
  it.each(KINDS)("renders an old-format %s block to HTML", (kind) => {
    const html = renderToString(<InlineChart kind={kind} title="T" data={DATA} />)
    expect(html).toContain("prd-chart")
    expect(html).toContain(`prd-chart-${kind}`)
    // The chart itself is in the markup, not a placeholder — first paint of a
    // stored PRD is the chart, exactly as before.
    expect(html).toContain("Checkout")
  })

  it("renders a spec-bearing block without touching vega", () => {
    const html = renderToString(
      <InlineChart
        kind="bar"
        data={DATA}
        spec={{ mark: "bar", data: { values: [{ a: 1 }] } }}
      />,
    )
    expect(html).toContain("vega-chart-host")
    // Legacy rows are present as the pending rendering, so a chunk that never
    // arrives still leaves the reader with a chart.
    expect(html).toContain("Checkout")
  })

  it("renders VegaChart itself without a DOM", () => {
    const html = renderToString(<VegaChart spec={{ mark: "bar" }} />)
    expect(html).toContain("vega-chart-host")
  })

  it("emits the expand-to-table disclosure server-side", () => {
    const html = renderToString(<InlineChart kind="bar" data={DATA} />)
    expect(html).toContain("chart-data-disclosure")
    expect(html).toContain("View data")
  })
})
