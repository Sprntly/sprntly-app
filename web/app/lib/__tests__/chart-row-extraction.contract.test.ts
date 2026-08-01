/**
 * CROSS-LANGUAGE CONTRACT — where a Vega-Lite spec's rows live.
 *
 * Two implementations read rows out of the same spec: `specDataRows` here, and
 * `ChartSpec.row_count()` on the backend. They drifted once already, in a way
 * that produced THREE different answers for one spec:
 *
 *   - the client's envelope path said "no rows" and rendered the empty state,
 *   - the client's bare path drew the chart,
 *   - the server printed "No data." for a chart vl_convert would have drawn.
 *
 * The cause was two independent readers of one shape: the client walked nested
 * containers but not named `datasets` references; the server read the root
 * only. Neither was wrong about its own half, and nothing bound them together.
 *
 * `__fixtures__/chart-row-extraction.json` is that binding. It is the single
 * statement of the rule, and both implementations assert against it — this
 * file, and the pytest that PR #986 adds for `row_count()`. A row-extraction
 * bug gets a case added HERE first, so the fix has to land on both sides.
 */
import { describe, expect, it } from "vitest"
import { specDataRows } from "../../components/shared/VegaChart"
import fixture from "../__fixtures__/chart-row-extraction.json"

interface Case {
  name: string
  spec: Record<string, unknown>
  rowCount: number
}

const cases = fixture.cases as unknown as Case[]

describe("row extraction contract", () => {
  it("ships cases covering every container and the named-dataset shape", () => {
    // A fixture that quietly loses its interesting cases stops binding
    // anything, and the failure mode is silence.
    expect(cases.length).toBeGreaterThanOrEqual(14)
    const names = cases.map((c) => c.name).join(" | ")
    expect(names).toContain("layer")
    expect(names).toContain("vconcat")
    expect(names).toContain("facet")
    expect(names).toContain("altair default")
  })

  it.each(cases.map((c) => [c.name, c] as const))(
    "%s",
    (_name, testCase) => {
      expect(specDataRows(testCase.spec)).toHaveLength(testCase.rowCount)
    },
  )

  it("states the rule in prose next to the cases", () => {
    // The cases are examples; `$definition` is the thing the backend author
    // reads when implementing the other half.
    const definition = (fixture as unknown as { $definition: string }).$definition
    expect(definition).toContain("datasets")
    expect(definition).toContain("layer")
  })
})
