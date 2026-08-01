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
  specViolatesContract,
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
    // A stripped copy, not the caller's object — `usermeta` never reaches embed.
    expect(spec).toEqual(SPEC)
    expect(spec).not.toHaveProperty("usermeta")
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

  it("degrades instead of embedding an envelope with no rows", async () => {
    // vega would embed this cleanly, resolve, and paint an empty box with
    // nothing to explain it. Degrade at the one point the blankness is
    // actually detectable.
    const { container } = render(<VegaChart spec={{ spec: { mark: "bar" }, data: [] }} />)
    await waitFor(() =>
      expect(container.querySelector('[data-testid="vega-chart-empty"]')).toBeTruthy(),
    )
    expect(embed).not.toHaveBeenCalled()
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

describe("specViolatesContract", () => {
  it("allows a data-closed spec", () => {
    expect(specViolatesContract(SPEC)).toBe(false)
    expect(specViolatesContract({ datasets: { a: [{ v: 1 }] } })).toBe(false)
  })

  it("is not the gate for usermeta or config — those are STRIPPED, not detected", () => {
    // Documented so nobody later "fixes" the guard to cover them and deletes
    // the strip. A JSON-Patch hides its URL in a VALUE, so no key-based check
    // can see it; and rejecting would kill legitimate altair specs.
    expect(
      specViolatesContract({ mark: "bar", usermeta: { embedOptions: { ast: false } } }),
    ).toBe(false)
    expect(specViolatesContract({ mark: "bar", config: { background: "#ff0000" } })).toBe(false)
  })

  it("rejects remote data in `data.url`", () => {
    expect(specViolatesContract({ data: { url: "http://x" } })).toBe(true)
    expect(specViolatesContract({ layer: [{ data: { url: "http://x" } }] })).toBe(true)
  })

  it("does not walk `datasets` payloads — those are rows, not references", () => {
    // Vega-Lite compiles `datasets` entries to inline `values`; they never
    // fetch. A "top referrer URLs" chart legitimately has a `url` COLUMN.
    expect(specViolatesContract({ datasets: { top: [{ url: "https://a.test", hits: 4 }] } })).toBe(
      false,
    )
    expect(
      specViolatesContract({ mark: "bar", data: { values: [{ url: "https://a.test" }] } }),
    ).toBe(false)
  })

  it("rejects a malformed inline payload — the shape check earns the skip", () => {
    // The walk skips the CONTENTS of `values`/`datasets` so a `url` column is
    // not a false positive. That skip is only safe because the payload is
    // known to be an array of rows. Without this check,
    // `{"data":{"values":{"url":"…"}}}` walks straight past the guard.
    expect(specViolatesContract({ data: { values: { url: "https://evil.test/x.json" } } })).toBe(
      true,
    )
    expect(specViolatesContract({ data: { values: "not-an-array" } })).toBe(true)
    expect(specViolatesContract({ datasets: { a: { url: "http://x" } } })).toBe(true)
    expect(specViolatesContract({ datasets: { a: "nope" } })).toBe(true)
    // Empty is a legitimate shape.
    expect(specViolatesContract({ data: { values: [] } })).toBe(false)
  })

  it("rejects `params` — sliders and brushes nobody designed, in a PRD panel", () => {
    // Inert on the server, NOT here: vega-embed renders a real
    // <input type="range"> into the panel, and `select` wires live brush /
    // pan / zoom onto the chart. The backend rejects this specifically so
    // this renderer can collect on it.
    expect(
      specViolatesContract({
        mark: "bar",
        params: [{ name: "s", value: 5, bind: { input: "range", min: 0, max: 10 } }],
      }),
    ).toBe(true)
    expect(specViolatesContract({ mark: "point", params: [{ name: "p", select: "interval" }] })).toBe(
      true,
    )
    expect(
      specViolatesContract({
        layer: [{ mark: "point", params: [{ name: "g", select: "interval", bind: "scales" }] }],
      }),
    ).toBe(true)
  })

  it("rejects `expr` value refs — a client/server divergence, not a hole", () => {
    // Sandboxed here by `ast: true` + the interpreter, so this is not a
    // security gap. It is refused because the backend refuses it: the server
    // would reject the spec while the browser drew it.
    expect(
      specViolatesContract({ mark: { type: "bar", width: { expr: "width/3" } } }),
    ).toBe(true)
    expect(
      specViolatesContract({ mark: "bar", encoding: { y: { value: { expr: "height" } } } }),
    ).toBe(true)
  })

  it("rejects an image mark — a tracking pixel served from our own origin", () => {
    expect(specViolatesContract({ mark: "image" })).toBe(true)
    expect(specViolatesContract({ mark: { type: "image" } })).toBe(true)
    expect(
      specViolatesContract({
        mark: { type: "image" },
        encoding: { url: { value: "https://evil.test/px.gif" } },
      }),
    ).toBe(true)
  })

  it("rejects an href encoding — a real <a> in our own trusted DOM", () => {
    expect(
      specViolatesContract({ mark: "bar", encoding: { href: { value: "https://evil.test" } } }),
    ).toBe(true)
    expect(specViolatesContract({ mark: { type: "bar", href: "https://evil.test" } })).toBe(true)
  })

  it("rejects an href buried under layer / concat / facet `spec`", () => {
    expect(
      specViolatesContract({
        layer: [{ mark: "bar" }, { mark: "text", encoding: { href: { field: "u" } } }],
      }),
    ).toBe(true)
    expect(
      specViolatesContract({ vconcat: [{ hconcat: [{ encoding: { href: { field: "u" } } }] }] }),
    ).toBe(true)
    expect(
      specViolatesContract({ facet: { field: "g" }, spec: { mark: "image" } }),
    ).toBe(true)
  })

  it("does not mistake a data COLUMN named url/href for an outbound reference", () => {
    expect(
      specViolatesContract({
        mark: "bar",
        data: { values: [{ url: "not-a-fetch", href: "also-not" }] },
      }),
    ).toBe(false)
  })

  it("refuses a spec too deep to vet rather than passing it through", () => {
    let deep: Record<string, unknown> = { mark: "bar" }
    for (let i = 0; i < 200; i++) deep = { layer: [deep] }
    expect(specViolatesContract(deep)).toBe(true)
  })

  it("does not refuse a deep-but-legitimate spec (cap agrees with Phase 0)", () => {
    // `facet -> spec -> vconcat -> layer -> encoding -> color -> condition ->
    // scale -> domain` is ~12 levels before any transform detail. A cap that
    // trips here would have the browser degrade a chart the server renders.
    let nested: Record<string, unknown> = { mark: "bar", data: { values: [{ a: 1 }] } }
    for (let i = 0; i < 20; i++) nested = { layer: [nested] }
    expect(specViolatesContract(nested)).toBe(false)
  })
})

describe("resolveChartSpec — the Phase 0 envelope", () => {
  const ROWS = [{ label: "a", value: 1 }, { label: "b", value: 2 }]

  it("passes a bare Vega-Lite spec through unchanged (bar usermeta)", () => {
    const out = resolveChartSpec(SPEC)
    expect(out.vlSpec).toEqual(SPEC)
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

  it("leaves a spec that inlines its own rows alone when the envelope is empty", () => {
    // Mirrors `spec.py`'s `if self.data:` carve-out. `ChartSpec.data` defaults
    // to `[]` and Phase 1's DS sandbox writes altair `*.vl.json` that carries
    // its own rows (inline, or via a named `datasets` reference — altair's
    // default), so `{spec: <carries rows>, data: []}` is routine.
    // Injecting unconditionally would blank every one of those charts in the
    // browser while the server drew them correctly.
    const inner = { mark: "bar", data: { values: [{ z: 9 }] } }
    const out = resolveChartSpec({ spec: inner, data: [] })
    expect((out.vlSpec as { data: { values: unknown } }).data.values).toEqual([{ z: 9 }])
    expect(out.drawable).toBe(true)
    expect(out.rows).toEqual([{ z: 9 }])
  })

  it("does not mutate the caller's inner spec when it does inject", () => {
    const inner = { mark: "bar" }
    const out = resolveChartSpec({ spec: inner, data: ROWS })
    expect((out.vlSpec as { data: { values: unknown } }).data.values).toEqual(ROWS)
    expect(inner).toEqual({ mark: "bar" })
  })

  it("marks an envelope undrawable only when NEITHER side has rows", () => {
    expect(resolveChartSpec({ spec: { mark: "bar" }, data: [] }).drawable).toBe(false)
  })

  it("declares a bare spec undrawable when its `values` is present and empty", () => {
    expect(resolveChartSpec({ mark: "bar", data: { values: [] } }).drawable).toBe(false)
    expect(resolveChartSpec({ layer: [{ mark: "line", data: { values: [] } }] }).drawable).toBe(
      false,
    )
  })

  it("gives the benefit of the doubt when rows are not visible from here", () => {
    // A generator or a named `datasets` reference: we cannot see the rows, so
    // we must not refuse to draw.
    expect(
      resolveChartSpec({ mark: "bar", data: { sequence: { start: 0, stop: 10 } } }).drawable,
    ).toBe(true)
    // A named reference now RESOLVES, so this is drawable because we can see
    // the rows — not merely because we cannot.
    const named = resolveChartSpec({
      mark: "bar",
      data: { name: "a" },
      datasets: { a: [{ v: 1 }] },
    })
    expect(named.drawable).toBe(true)
    expect(named.rows).toEqual([{ v: 1 }])
    expect(resolveChartSpec(SPEC).drawable).toBe(true)
  })

  it("strips top-level `config` so a spec cannot repaint itself out of the theme", () => {
    // A spec's own `config` WINS over the config we pass, so this would
    // repaint the chart red inside a Sprntly panel.
    const loud = { mark: "bar", data: { values: [{ a: 1 }] }, config: { background: "#ff0000" } }
    expect(resolveChartSpec(loud).vlSpec).not.toHaveProperty("config")
    expect(resolveChartSpec(loud).drawable).toBe(true)
    expect(resolveChartSpec({ spec: loud, data: [{ a: 1 }] }).vlSpec).not.toHaveProperty("config")
  })

  it("strips `usermeta` so a spec cannot hand itself embed options", () => {
    const hostile = {
      mark: "bar",
      data: { values: [{ a: 1 }] },
      usermeta: {
        embedOptions: { actions: true, ast: false, patch: "https://evil.test/p.json" },
      },
    }
    expect(resolveChartSpec(hostile).vlSpec).not.toHaveProperty("usermeta")
    // Stripped, not rejected — altair writes a benign usermeta and the chart
    // must still draw.
    expect(resolveChartSpec(hostile).drawable).toBe(true)
    // And on the envelope path too.
    const env = resolveChartSpec({ spec: hostile, data: [{ a: 1 }] })
    expect(env.vlSpec).not.toHaveProperty("usermeta")
  })

  it("treats a facet/repeat spec as a real spec, not an envelope", () => {
    const facet = { facet: { field: "g" }, spec: { mark: "bar", data: { values: ROWS } } }
    // Not unwrapped: the returned spec is the facet spec itself (a stripped
    // copy), not its `spec` child.
    const out = resolveChartSpec(facet)
    expect(out.vlSpec).toEqual(facet)
    const repeat = { repeat: ["a", "b"], spec: { mark: "bar" } }
    expect(resolveChartSpec(repeat).vlSpec).toEqual(repeat)
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
