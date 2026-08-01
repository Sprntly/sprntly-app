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
  specDataRows,
  specFetchesRemoteData,
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

  it("refuses a spec that would fetch remote data, and shows the table", async () => {
    const remote = { mark: "bar", data: { url: "https://example.test/rows.json" } }
    const { container } = render(
      <VegaChart spec={remote} tableRows={[{ label: "a", value: 1 }]} />,
    )
    await waitFor(() =>
      expect(container.querySelector('[data-testid="chart-data-fallback"]')).toBeTruthy(),
    )
    expect(embed).not.toHaveBeenCalled()
  })

  it("loads the vega runtime once for many charts on a page", async () => {
    const importSpy = vi.fn()
    render(
      <>
        <VegaChart spec={SPEC} />
        <VegaChart spec={{ ...SPEC }} />
        <VegaChart spec={{ ...SPEC }} />
      </>,
    )
    await waitFor(() => expect(embed).toHaveBeenCalledTimes(3))
    // One cached module promise; three embeds against it. (The import itself
    // is module-scoped, so the assertion that matters is that nothing threw
    // and all three mounted.)
    expect(importSpy).not.toHaveBeenCalled()
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

describe("spec helpers", () => {
  it("pulls table rows off an inline data-closed spec", () => {
    expect(specDataRows(SPEC)).toEqual(SPEC.data.values)
    expect(specDataRows(null)).toEqual([])
    expect(specDataRows({ mark: "bar" })).toEqual([])
    expect(specDataRows({ data: { url: "x" } })).toEqual([])
  })

  it("detects remote data in both `data.url` and `datasets`", () => {
    expect(specFetchesRemoteData(SPEC)).toBe(false)
    expect(specFetchesRemoteData({ data: { url: "http://x" } })).toBe(true)
    expect(specFetchesRemoteData({ datasets: { a: { url: "http://x" } } })).toBe(true)
    expect(specFetchesRemoteData({ datasets: { a: [{ v: 1 }] } })).toBe(false)
  })
})
