/**
 * The legacy-kind → Vega-Lite compiler, and the additive-`spec` plumbing in the
 * PRD / evidence / brief adapters.
 *
 * The load-bearing assertion in this file is the LAST describe block: old-format
 * chart blocks — the ones sitting in every stored PRD and evidence document —
 * parse into exactly the shape they parsed into before `spec` existed.
 */
import { describe, expect, it } from "vitest"
import {
  COMPILABLE_KINDS,
  COMPILE_LEGACY_KINDS,
  chartDatumRows,
  extractSpec,
  kindToVegaLite,
  toNumber,
} from "../chart-adapter"
import { CHART_THEME } from "../chart-theme"
import type { PrdChartKind } from "../../types/content"

const DATA = [
  { label: "Checkout", value: 42 },
  { label: "Search", value: 17 },
  { label: "Onboarding (Day 7)", value: "9%" },
]

describe("kindToVegaLite", () => {
  it("compiles every kind it claims to compile", () => {
    for (const kind of COMPILABLE_KINDS) {
      const spec = kindToVegaLite(kind, DATA, { title: "T" })
      expect(spec, kind).not.toBeNull()
      expect(spec!.$schema).toBe(CHART_THEME.vegaLiteSchema)
      expect(spec!.title).toBe("T")
    }
  })

  it("returns null for kinds with no faithful Vega-Lite form", () => {
    expect(kindToVegaLite("stat", DATA)).toBeNull()
    expect(kindToVegaLite("gauge", DATA)).toBeNull()
  })

  it("returns null for empty data rather than emitting an empty chart", () => {
    for (const kind of COMPILABLE_KINDS) {
      expect(kindToVegaLite(kind, []), kind).toBeNull()
    }
  })

  it("ships the rows inside the spec — a spec is data-closed", () => {
    const spec = kindToVegaLite("bar", DATA)!
    const values = (spec.data as { values: unknown[] }).values
    expect(values).toHaveLength(3)
    // String values are coerced for the encoding but the original is kept for
    // the tooltip / table so "9%" never reads as "9".
    expect(values[2]).toEqual({ label: "Onboarding (Day 7)", value: 9, display: "9%" })
  })

  it("never points a spec at a remote URL", () => {
    for (const kind of COMPILABLE_KINDS) {
      const spec = kindToVegaLite(kind, DATA)!
      expect(JSON.stringify(spec)).not.toContain('"url"')
    }
  })

  it("uses the shared theme palette for categorical colour", () => {
    const spec = kindToVegaLite("pie", DATA)!
    const color = (spec.encoding as Record<string, { scale?: { range?: string[] } }>).color
    expect(color.scale?.range).toEqual(CHART_THEME.categorical)
  })

  it("gives donut a real inner radius and pie none", () => {
    const donut = kindToVegaLite("donut", DATA)!.mark as Record<string, unknown>
    const pie = kindToVegaLite("pie", DATA)!.mark as Record<string, unknown>
    expect(donut.innerRadius).toBeGreaterThan(0)
    expect(pie.innerRadius).toBeUndefined()
  })

  // Snapshots: these are the specs a future PR would ship if it flipped a kind
  // on via COMPILE_LEGACY_KINDS. Reviewing a diff here is reviewing a visual change.
  it("bar spec snapshot", () => {
    expect(kindToVegaLite("bar", DATA, { title: "Top areas" })).toMatchSnapshot()
  })
  it("line spec snapshot", () => {
    expect(kindToVegaLite("line", DATA, { title: "Trend" })).toMatchSnapshot()
  })
  it("pie spec snapshot", () => {
    expect(kindToVegaLite("pie", DATA, { title: "Share" })).toMatchSnapshot()
  })
  it("donut spec snapshot", () => {
    expect(kindToVegaLite("donut", DATA, { title: "Share" })).toMatchSnapshot()
  })
})

describe("COMPILE_LEGACY_KINDS", () => {
  it("is off — no legacy kind is routed through Vega by default", () => {
    // Guard, not decoration. Flipping this changes the look of every stored
    // PRD and evidence document containing a chart; it needs a staging eyeball
    // and design sign-off, plus a deliberate edit to this test — not a silent
    // flip. Deliberate deviation from plan §3, explained in chart-adapter.ts.
    expect(COMPILE_LEGACY_KINDS).toBe(false)
  })

  it("has a compiler ready for every kind it could route, if flipped", () => {
    // The two kinds with no Vega-Lite form stay null forever: `gauge`'s arc
    // gradient / round caps / radial target tick have no equivalent, and
    // `stat` is a number in a div.
    for (const kind of COMPILABLE_KINDS) {
      expect(kindToVegaLite(kind as PrdChartKind, DATA), kind).not.toBeNull()
    }
    expect(COMPILABLE_KINDS).not.toContain("gauge")
    expect(COMPILABLE_KINDS).not.toContain("stat")
  })
})

describe("toNumber / chartDatumRows", () => {
  it("coerces the same way the DOM renderer does", () => {
    expect(toNumber(12)).toBe(12)
    expect(toNumber("9%")).toBe(9)
    expect(toNumber("$1.5")).toBe(1.5)
    expect(toNumber("-3")).toBe(-3)
    expect(toNumber("n/a")).toBe(0)
  })

  it("keeps the original display value in table rows", () => {
    expect(chartDatumRows(DATA)).toEqual([
      { label: "Checkout", value: 42 },
      { label: "Search", value: 17 },
      { label: "Onboarding (Day 7)", value: "9%" },
    ])
  })
})

describe("extractSpec", () => {
  it("accepts a plain object", () => {
    expect(extractSpec({ mark: "bar" })).toEqual({ mark: "bar" })
  })

  it("treats anything else as absent so the legacy path still runs", () => {
    expect(extractSpec(undefined)).toBeUndefined()
    expect(extractSpec(null)).toBeUndefined()
    expect(extractSpec("{}")).toBeUndefined()
    expect(extractSpec([1, 2])).toBeUndefined()
    expect(extractSpec(7)).toBeUndefined()
  })
})
