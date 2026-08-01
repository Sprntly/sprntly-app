/**
 * @vitest-environment jsdom
 *
 * The old-format compatibility contract, exercised as the user sees it.
 *
 * Every one of the six legacy kinds must still render its DOM implementation
 * from a bare `{kind, data}` block — no spec, no Vega, no network. Stored PRDs
 * and evidence documents hold exactly these blocks and re-render them on every
 * open; if this file goes red, artifacts customers already have break.
 */
import { cleanup, render, screen, within } from "@testing-library/react"
import { afterEach, describe, expect, it, vi } from "vitest"
// Classic JSX runtime (tsconfig `jsx: "preserve"`) needs a global React before
// the component modules evaluate.
vi.hoisted(() => {
  // eslint-disable-next-line @typescript-eslint/no-require-imports
  ;(globalThis as Record<string, unknown>).React = require("react")
})

import { InlineChart, parseChartBody } from "../InlineChart"
import type { PrdChartKind } from "../../../types/content"

// If any of these tests loads the vega runtime, the legacy path has regressed.
const embedSpy = vi.fn()
vi.mock("vega-embed", () => ({
  default: (...args: unknown[]) => {
    embedSpy(...args)
    return Promise.resolve({ finalize: () => {} })
  },
}))
vi.mock("vega-interpreter", () => ({ expressionInterpreter: {} }))

const DATA = [
  { label: "Checkout", value: 42 },
  { label: "Search", value: 17 },
]

afterEach(() => {
  cleanup()
  embedSpy.mockClear()
})

describe("InlineChart — legacy {kind, data} blocks", () => {
  const kinds: PrdChartKind[] = ["bar", "line", "pie", "donut", "stat", "gauge"]

  it.each(kinds)("renders the DOM implementation for kind=%s", (kind) => {
    const { container } = render(<InlineChart kind={kind} title="T" data={DATA} />)
    const figure = container.querySelector("figure.prd-chart")
    expect(figure).toBeTruthy()
    expect(figure!.className).toContain(`prd-chart-${kind}`)
    // Not the Vega path.
    expect(container.querySelector('[data-testid="vega-chart-host"]')).toBeNull()
    expect(embedSpy).not.toHaveBeenCalled()
  })

  it("keeps each kind's distinctive markup", () => {
    const marks: Array<[PrdChartKind, string]> = [
      ["bar", ".prd-bars"],
      ["line", "svg.prd-line"],
      ["pie", ".prd-pie"],
      ["donut", ".prd-pie"],
      ["stat", ".prd-stats"],
      ["gauge", ".prd-gauge"],
    ]
    for (const [kind, selector] of marks) {
      const { container, unmount } = render(<InlineChart kind={kind} data={DATA} />)
      expect(container.querySelector(selector), `${kind} → ${selector}`).toBeTruthy()
      unmount()
    }
  })

  it("renders the title and subtitle", () => {
    render(<InlineChart kind="bar" title="Top areas" subtitle="last 30 days" data={DATA} />)
    expect(screen.getByText("Top areas")).toBeTruthy()
    expect(screen.getByText("last 30 days")).toBeTruthy()
  })

  it("puts the data one click away under every chart", () => {
    const { container } = render(<InlineChart kind="bar" data={DATA} />)
    const disclosure = container.querySelector('[data-testid="chart-data-disclosure"]')
    expect(disclosure).toBeTruthy()
    const table = within(disclosure as HTMLElement).getByTestId("chart-data-table")
    expect(table.textContent).toContain("Checkout")
    expect(table.textContent).toContain("42")
  })

  it("does not render a disclosure when there is no data to show", () => {
    const { container } = render(<InlineChart kind="bar" data={[]} />)
    expect(container.querySelector('[data-testid="chart-data-disclosure"]')).toBeNull()
  })
})

describe("InlineChart — additive spec path", () => {
  it("routes to the Vega renderer when a spec is present", () => {
    const spec = { mark: "bar", data: { values: [{ a: 1 }] } }
    const { container } = render(<InlineChart kind="bar" data={DATA} spec={spec} />)
    expect(container.querySelector('[data-testid="vega-chart-host"]')).toBeTruthy()
    expect(container.querySelector(".prd-chart-vega")).toBeTruthy()
  })

  it("paints the DOM chart while the vega chunk is in flight, not a spinner", () => {
    // First paint of a stored artifact must look exactly as it does today, and
    // must SURVIVE the chunk never arriving (a real risk with `output: export`
    // when a deploy rotates chunk hashes).
    const spec = { mark: "bar", data: { values: [{ a: 1 }] } }
    const { container } = render(<InlineChart kind="bar" data={DATA} spec={spec} />)
    expect(container.querySelector('[data-testid="vega-chart-pending"]')).toBeTruthy()
    expect(container.querySelector(".prd-bars")).toBeTruthy()
    expect(container.querySelector('[data-testid="vega-chart-loading"]')).toBeNull()
  })

  it("shows the loading line for a spec-only block, which has no DOM form", () => {
    const spec = { mark: "line", data: { values: [{ a: 1 }] } }
    const { container } = render(<InlineChart kind="bar" data={[]} spec={spec} />)
    expect(container.querySelector('[data-testid="vega-chart-loading"]')).toBeTruthy()
    expect(container.querySelector(".prd-bars")).toBeNull()
  })

  it("ignores a malformed spec and falls back to the legacy rendering", () => {
    const { container } = render(
      // A non-object spec is treated as absent — degrade to the old chart,
      // never drop the block.
      <InlineChart kind="bar" data={DATA} spec={undefined} />,
    )
    expect(container.querySelector(".prd-bars")).toBeTruthy()
  })

  it("still shows the legacy rows in the disclosure on the spec path", () => {
    const spec = { mark: "bar", data: { values: [{ a: 1 }] } }
    const { container } = render(<InlineChart kind="bar" data={DATA} spec={spec} />)
    const table = container.querySelector('[data-testid="chart-data-table"]')
    expect(table!.textContent).toContain("Checkout")
  })
})

describe("parseChartBody", () => {
  it("parses a legacy block exactly as before", () => {
    const out = parseChartBody(
      JSON.stringify({ kind: "pie", title: "Share", data: DATA }),
    )
    expect(out).toEqual({ kind: "pie", title: "Share", subtitle: undefined, data: DATA })
    expect(out).not.toHaveProperty("spec")
  })

  it("rejects an unknown kind with no spec, as before", () => {
    expect(parseChartBody(JSON.stringify({ kind: "sankey", data: DATA }))).toBeNull()
  })

  it("rejects a legacy block with no usable rows, as before", () => {
    expect(parseChartBody(JSON.stringify({ kind: "bar", data: [] }))).toBeNull()
  })

  it("accepts a spec-only block and defaults the kind", () => {
    const out = parseChartBody(JSON.stringify({ spec: { mark: "line" }, title: "Trend" }))
    expect(out).not.toBeNull()
    expect(out!.spec).toEqual({ mark: "line" })
    expect(out!.kind).toBe("bar")
    expect(out!.data).toEqual([])
  })

  it("accepts a block carrying both a spec and legacy rows", () => {
    const out = parseChartBody(
      JSON.stringify({ kind: "line", data: DATA, spec: { mark: "line" } }),
    )
    expect(out!.kind).toBe("line")
    expect(out!.data).toEqual(DATA)
    expect(out!.spec).toEqual({ mark: "line" })
  })

  it("drops a non-object spec rather than the whole block", () => {
    const out = parseChartBody(JSON.stringify({ kind: "bar", data: DATA, spec: "nope" }))
    expect(out).not.toBeNull()
    expect(out).not.toHaveProperty("spec")
    expect(out!.data).toEqual(DATA)
  })
})
