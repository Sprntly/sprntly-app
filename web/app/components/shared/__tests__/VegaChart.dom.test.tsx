/**
 * @vitest-environment jsdom
 *
 * VegaChart: the dynamic-import boundary, the CSP-safe embed options, and the
 * degrade-to-table contract. A malformed spec in a stored PRD must cost the
 * reader a picture, never the page.
 */
import { cleanup, render, waitFor } from "@testing-library/react"
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest"
// Classic JSX runtime (tsconfig `jsx: "preserve"`) needs a global React before
// the component modules evaluate.
vi.hoisted(() => {
  // eslint-disable-next-line @typescript-eslint/no-require-imports
  ;(globalThis as Record<string, unknown>).React = require("react")
})

import {
  VegaChart,
  __resetVegaRuntimeForTests,
  __vegaRuntimeLoadCount,
  resolveChartSpec,
  specDataRows,
  specReachesOutward,
} from "../VegaChart"

const embed = vi.fn()
const finalize = vi.fn()
const INTERPRETER = { operator: () => {} }

vi.mock("vega-embed", () => ({
  default: (...args: unknown[]) => embed(...args),
}))
vi.mock("vega-interpreter", () => ({ expressionInterpreter: INTERPRETER }))

const SPEC = {
  $schema: "https://vega.github.io/schema/vega-lite/v6.json",
  mark: "bar",
  data: { values: [{ label: "Checkout", value: 42 }, { label: "Search", value: 17 }] },
  encoding: { x: { field: "label" }, y: { field: "value" } },
}

beforeEach(() => {
  __resetVegaRuntimeForTests()
  embed.mockReset()
  finalize.mockReset()
  embed.mockResolvedValue({ finalize })
})

afterEach(() => {
  cleanup()
})

describe("VegaChart", () => {
  it("shows a loading state before the runtime resolves", () => {
    const { container } = render(<VegaChart spec={SPEC} />)
    expect(container.querySelector('[data-testid="vega-chart-loading"]')).toBeTruthy()
    expect(container.querySelector('[data-testid="vega-chart-host"]')).toBeTruthy()
  })

  it("embeds with actions off and the CSP-safe interpreter, not codegen", async () => {
    render(<VegaChart spec={SPEC} />)
    await waitFor(() => expect(embed).toHaveBeenCalledTimes(1))
    const [, spec, opts] = embed.mock.calls[0] as [HTMLElement, unknown, Record<string, unknown>]
    expect(spec).toBe(SPEC)
    expect(opts.actions).toBe(false)
    expect(opts.renderer).toBe("svg")
    // `ast: true` = parse expressions to an AST instead of `Function`-constructor
    // codegen; `expr` = the interpreter that evaluates that AST. Both required.
    expect(opts.ast).toBe(true)
    expect(opts.expr).toBe(INTERPRETER)
    expect(opts.config).toBeTruthy()
  })

  it("reaches the ready phase and drops the loading state", async () => {
    const { container } = render(<VegaChart spec={SPEC} />)
    await waitFor(() =>
      expect(
        container.querySelector('[data-testid="vega-chart-host"]')?.getAttribute("data-phase"),
      ).toBe("ready"),
    )
    expect(container.querySelector('[data-testid="vega-chart-loading"]')).toBeNull()
  })

  it("degrades to the data table when the spec fails to render", async () => {
    const warn = vi.spyOn(console, "warn").mockImplementation(() => {})
    embed.mockRejectedValue(new Error("Invalid specification"))
    const { container } = render(<VegaChart spec={SPEC} />)
    await waitFor(() =>
      expect(container.querySelector('[data-testid="chart-data-fallback"]')).toBeTruthy(),
    )
    const table = container.querySelector('[data-testid="chart-data-table"]')
    expect(table!.textContent).toContain("Checkout")
    expect(table!.textContent).toContain("42")
    // The chart host is hidden, not removed — nothing crashed.
    expect(
      container.querySelector('[data-testid="vega-chart-host"]')?.hasAttribute("hidden"),
    ).toBe(true)
    warn.mockRestore()
  })

  it("degrades when the chunk itself fails to load", async () => {
    const warn = vi.spyOn(console, "warn").mockImplementation(() => {})
    embed.mockImplementation(() => {
      throw new Error("ChunkLoadError")
    })
    const { container } = render(<VegaChart spec={SPEC} />)
    await waitFor(() =>
      expect(container.querySelector('[data-testid="chart-data-fallback"]')).toBeTruthy(),
    )
    warn.mockRestore()
  })

  it("refuses a spec that reaches outward, and shows the table", async () => {
    const remote = { mark: "bar", data: { url: "https://example.test/rows.json" } }
    const { container } = render(
      <VegaChart spec={remote} tableRows={[{ label: "a", value: 1 }]} />,
    )
    await waitFor(() =>
      expect(container.querySelector('[data-testid="chart-data-fallback"]')).toBeTruthy(),
    )
    expect(embed).not.toHaveBeenCalled()
  })

  it("fetches the vega runtime ONCE for many charts on a page", async () => {
    expect(__vegaRuntimeLoadCount()).toBe(0)
    render(
      <>
        <VegaChart spec={SPEC} />
        <VegaChart spec={{ ...SPEC, mark: "line" }} />
        <VegaChart spec={{ ...SPEC, mark: "point" }} />
      </>,
    )
    await waitFor(() => expect(embed).toHaveBeenCalledTimes(3))
    // Three charts, three embeds, ONE runtime fetch. The counter increments
    // inside loadVegaRuntime's cache-miss branch, so this pins OUR module-scope
    // cache rather than vitest's module registry.
    expect(__vegaRuntimeLoadCount()).toBe(1)
  })

  it("does not re-embed when a caller rebuilds an identical spec object", async () => {
    // AskReplyBody re-parses the ```chart fence on every simulated-stream tick,
    // handing us a fresh object with identical contents ~30 times per answer.
    const { rerender } = render(<VegaChart spec={{ ...SPEC }} />)
    await waitFor(() => expect(embed).toHaveBeenCalledTimes(1))
    rerender(<VegaChart spec={{ ...SPEC }} />)
    rerender(<VegaChart spec={{ ...SPEC }} />)
    rerender(<VegaChart spec={{ ...SPEC }} />)
    await Promise.resolve()
    expect(embed).toHaveBeenCalledTimes(1)
    expect(finalize).not.toHaveBeenCalled()
  })

  it("does re-embed when the spec actually changes", async () => {
    const { rerender } = render(<VegaChart spec={{ ...SPEC }} />)
    await waitFor(() => expect(embed).toHaveBeenCalledTimes(1))
    rerender(<VegaChart spec={{ ...SPEC, mark: "line" }} />)
    await waitFor(() => expect(embed).toHaveBeenCalledTimes(2))
    expect(finalize).toHaveBeenCalled()
  })

  it("finalizes the view on unmount so nothing leaks", async () => {
    const { unmount } = render(<VegaChart spec={SPEC} />)
    await waitFor(() => expect(embed).toHaveBeenCalled())
    unmount()
    await waitFor(() => expect(finalize).toHaveBeenCalled())
  })

  it("renders the expand-to-table disclosure from the spec's own rows", async () => {
    const { container } = render(<VegaChart spec={SPEC} />)
    await waitFor(() => expect(embed).toHaveBeenCalled())
    const disclosure = container.querySelector('[data-testid="chart-data-disclosure"]')
    expect(disclosure!.textContent).toContain("Checkout")
  })
})

describe("specDataRows", () => {
  it("pulls table rows off an inline data-closed spec", () => {
    expect(specDataRows(SPEC)).toEqual(SPEC.data.values)
    expect(specDataRows(null)).toEqual([])
    expect(specDataRows({ mark: "bar" })).toEqual([])
    expect(specDataRows({ data: { url: "x" } })).toEqual([])
  })

  it("descends into a LAYERED spec whose data hangs off a layer", () => {
    // The shape every Phase 4 statistical chart takes (ITS, DiD, K-M): the
    // root carries no data at all.
    const layered = {
      layer: [
        { mark: "line", data: { values: [{ t: 1, y: 5 }] } },
        { mark: "rule", encoding: { x: { datum: 3 } } },
      ],
    }
    expect(specDataRows(layered)).toEqual([{ t: 1, y: 5 }])
  })

  it("descends into concat / hconcat / vconcat / facet-style `spec`", () => {
    const rows = [{ a: 1 }]
    expect(specDataRows({ vconcat: [{ hconcat: [{ data: { values: rows } }] }] })).toEqual(rows)
    expect(specDataRows({ concat: [{ data: { values: rows } }] })).toEqual(rows)
    expect(specDataRows({ facet: { field: "g" }, spec: { data: { values: rows } } })).toEqual(rows)
  })

  it("survives a pathologically nested spec instead of blowing the stack", () => {
    // specDataRows runs during RENDER, outside any try/catch — an unbounded
    // recursion here is a RangeError thrown out of React's render phase, which
    // takes the page down rather than degrading one chart.
    let deep: Record<string, unknown> = { data: { values: [{ a: 1 }] } }
    for (let i = 0; i < 50_000; i++) deep = { layer: [deep] }
    expect(() => specDataRows(deep)).not.toThrow()
    expect(specDataRows(deep)).toEqual([])
  })
})

describe("specReachesOutward", () => {
  it("allows a data-closed spec", () => {
    expect(specReachesOutward(SPEC)).toBe(false)
    expect(specReachesOutward({ datasets: { a: [{ v: 1 }] } })).toBe(false)
  })

  it("rejects remote data in `data.url` and `datasets`", () => {
    expect(specReachesOutward({ data: { url: "http://x" } })).toBe(true)
    expect(specReachesOutward({ datasets: { a: { url: "http://x" } } })).toBe(true)
  })

  it("rejects an image mark — a tracking pixel served from our own origin", () => {
    expect(specReachesOutward({ mark: "image" })).toBe(true)
    expect(specReachesOutward({ mark: { type: "image" } })).toBe(true)
    expect(
      specReachesOutward({
        mark: { type: "image" },
        encoding: { url: { value: "https://evil.test/px.gif" } },
      }),
    ).toBe(true)
  })

  it("rejects an href encoding — a real <a> in our own trusted DOM", () => {
    expect(
      specReachesOutward({ mark: "bar", encoding: { href: { value: "https://evil.test" } } }),
    ).toBe(true)
    expect(specReachesOutward({ mark: { type: "bar", href: "https://evil.test" } })).toBe(true)
  })

  it("rejects an href buried under layer / concat / facet `spec`", () => {
    expect(
      specReachesOutward({
        layer: [{ mark: "bar" }, { mark: "text", encoding: { href: { field: "u" } } }],
      }),
    ).toBe(true)
    expect(
      specReachesOutward({ vconcat: [{ hconcat: [{ encoding: { href: { field: "u" } } }] }] }),
    ).toBe(true)
    expect(
      specReachesOutward({ facet: { field: "g" }, spec: { mark: "image" } }),
    ).toBe(true)
  })

  it("does not mistake a data COLUMN named url/href for an outbound reference", () => {
    expect(
      specReachesOutward({
        mark: "bar",
        data: { values: [{ url: "not-a-fetch", href: "also-not" }] },
      }),
    ).toBe(false)
  })

  it("refuses a spec too deep to vet rather than passing it through", () => {
    let deep: Record<string, unknown> = { mark: "bar" }
    for (let i = 0; i < 100; i++) deep = { layer: [deep] }
    expect(specReachesOutward(deep)).toBe(true)
  })
})

describe("resolveChartSpec — the Phase 0 envelope", () => {
  const ROWS = [{ label: "a", value: 1 }, { label: "b", value: 2 }]

  it("passes a bare Vega-Lite spec straight through", () => {
    const out = resolveChartSpec(SPEC)
    expect(out.vlSpec).toBe(SPEC)
    expect(out.rows).toEqual(SPEC.data.values)
  })

  it("injects the envelope's rows into a data-free spec", () => {
    // Without this the chart embeds cleanly, reaches `ready`, throws nothing —
    // and draws a blank box with no table under it.
    const out = resolveChartSpec({
      spec: { mark: "bar", encoding: { x: { field: "label" } } },
      data: ROWS,
      title: "Envelope title",
    })
    expect((out.vlSpec as { data: { values: unknown } }).data.values).toEqual(ROWS)
    expect(out.rows).toEqual(ROWS)
    expect(out.title).toBe("Envelope title")
  })

  it("does not clobber data an inner spec already carries", () => {
    const inner = { mark: "bar", data: { values: [{ z: 9 }] } }
    const out = resolveChartSpec({ spec: inner, data: ROWS })
    expect((out.vlSpec as { data: { values: unknown } }).data.values).toEqual([{ z: 9 }])
  })

  it("treats a facet/repeat spec as a real spec, not an envelope", () => {
    const facet = { facet: { field: "g" }, spec: { mark: "bar", data: { values: ROWS } } }
    const out = resolveChartSpec(facet)
    expect(out.vlSpec).toBe(facet)
    const repeat = { repeat: ["a", "b"], spec: { mark: "bar" } }
    expect(resolveChartSpec(repeat).vlSpec).toBe(repeat)
  })

  it("carries subtitle and caption off the envelope", () => {
    const out = resolveChartSpec({
      spec: { mark: "bar" },
      data: ROWS,
      subtitle: "sub",
      caption: "cap",
    })
    expect(out.subtitle).toBe("sub")
    expect(out.caption).toBe("cap")
  })
})
