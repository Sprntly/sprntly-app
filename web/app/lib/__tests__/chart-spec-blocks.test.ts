/**
 * The additive-`spec` block contract, across every adapter that parses a chart.
 *
 * The old-format assertions here are the guarantee that matters: a stored PRD
 * or evidence document written before Vega existed parses into the identical
 * section shape it always did, with no `spec` key at all.
 */
import { describe, expect, it } from "vitest"
import { markdownToPrdState } from "../prd-adapter"
import { markdownToEvidenceState } from "../evidence-adapter"
import { briefToBriefV2State } from "../brief-v2-adapter"
import type { Brief } from "../api"

const LEGACY_BLOCK = [
  "## Context",
  "",
  "```chart",
  JSON.stringify({
    kind: "bar",
    title: "Where users drop",
    data: [
      { label: "Checkout", value: 42 },
      { label: "Search", value: 17 },
    ],
  }),
  "```",
  "",
].join("\n")

const SPEC_BLOCK = [
  "## Context",
  "",
  "```chart",
  JSON.stringify({
    title: "Weekly signups",
    spec: {
      $schema: "https://vega.github.io/schema/vega-lite/v6.json",
      mark: "line",
      data: { values: [{ w: 1, n: 10 }] },
    },
  }),
  "```",
  "",
].join("\n")

function chartSection(sections: Array<{ type: string }>) {
  return sections.find((s) => s.type === "chart") as
    | (Record<string, unknown> & { type: "chart" })
    | undefined
}

describe.each([
  ["prd-adapter", markdownToPrdState],
  ["evidence-adapter", markdownToEvidenceState],
] as const)("%s chart blocks", (_name, parse) => {
  it("parses an OLD-FORMAT block into exactly the pre-Vega shape", () => {
    const chart = chartSection(parse(LEGACY_BLOCK).sections)
    expect(chart).toBeTruthy()
    expect(chart!.kind).toBe("bar")
    expect(chart!.title).toBe("Where users drop")
    expect(chart!.data).toEqual([
      { label: "Checkout", value: 42 },
      { label: "Search", value: 17 },
    ])
    // No spec key at all — the block is byte-identical in meaning to before.
    expect(chart).not.toHaveProperty("spec")
  })

  it("carries a spec through when the block has one", () => {
    const chart = chartSection(parse(SPEC_BLOCK).sections)
    expect(chart).toBeTruthy()
    expect((chart!.spec as Record<string, unknown>).mark).toBe("line")
    // Kind stays total so the block union keeps type-checking.
    expect(chart!.kind).toBe("bar")
    expect(chart!.data).toEqual([])
  })

  it("still drops a block with neither a known kind nor a spec", () => {
    const bad = ["```chart", JSON.stringify({ kind: "sankey", data: [] }), "```"].join("\n")
    expect(chartSection(parse(bad).sections)).toBeUndefined()
  })

  it("ignores a non-object spec and keeps the legacy rendering", () => {
    const md = [
      "```chart",
      JSON.stringify({ kind: "pie", data: [{ label: "a", value: 1 }], spec: "oops" }),
      "```",
    ].join("\n")
    const chart = chartSection(parse(md).sections)
    expect(chart!.kind).toBe("pie")
    expect(chart).not.toHaveProperty("spec")
  })
})

/* ------------------------------------------------------------------ */

function briefWith(chartHint: Record<string, unknown>): Brief {
  return {
    id: 1,
    company: "acme",
    generated_at: "2026-08-01T00:00:00Z",
    week_label: "Week of Aug 1, 2026",
    summary_headline: "One finding this week",
    insights: [
      {
        tag: "fix",
        title: "Checkout drop-off",
        subtitle: "sub",
        headline: "headline",
        domain: "retention",
        subdomain: "checkout",
        confidence: 0.7,
        metrics: [{ label: "ARR at risk", value: "$1M/yr" }],
        why_this_ranks: [],
        why_alternatives_dont_hold: [],
        recommendation: "Ship the fix",
        impact_math: [],
        verification_metrics: [],
        convergence: [{ source: "Zendesk", signal: "top reason", strength: "Strong" }],
        user_quotes: [{ quote: "I quit at the deductible step.", source: "Helpscout" }],
        chart_hints: [chartHint],
      },
    ],
  } as unknown as Brief
}

describe("brief-v2-adapter chart hints", () => {
  it("keeps an old-format hint spec-free", () => {
    const state = briefToBriefV2State(
      briefWith({ kind: "bar", title: "T", data: [{ label: "a", value: 3 }] }),
    )
    const chart = state.hero?.chart
    expect(chart).toBeTruthy()
    expect(chart!.kind).toBe("bar")
    expect(chart).not.toHaveProperty("spec")
  })

  it("passes a hint's spec through", () => {
    const state = briefToBriefV2State(
      briefWith({
        kind: "bar",
        title: "T",
        data: [{ label: "a", value: 3 }],
        spec: { mark: "arc" },
      }),
    )
    expect(state.hero?.chart?.spec).toEqual({ mark: "arc" })
  })
})
