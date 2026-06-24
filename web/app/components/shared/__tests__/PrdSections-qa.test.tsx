import * as React from "react"
import { renderToStaticMarkup } from "react-dom/server"
import { describe, expect, it } from "vitest"
import { PrdSections } from "../PrdSections"
import type { PrdSection, QaScenarioRow } from "../../../types/content"

// PrdSections.tsx relies on the automatic JSX runtime Next.js supplies; this
// repo's vitest transform uses the classic runtime, so expose a global React
// (same shim the sibling PrdSections-design test uses).
;(globalThis as typeof globalThis & { React?: typeof React }).React = React

function row(over: Partial<QaScenarioRow>): QaScenarioRow {
  return {
    id: "",
    group: "",
    title: "",
    given: "",
    when: "",
    then: "",
    traces: "",
    risk: "",
    ...over,
  }
}

function renderQa(rows: QaScenarioRow[], openQuestions: string[] = []): string {
  const sections: PrdSection[] = [{ type: "qa-scenarios", rows, openQuestions }]
  return renderToStaticMarkup(React.createElement(PrdSections, { sections }))
}

describe("PrdSections — qa-scenarios block", () => {
  it("renders grouped scenarios with Given/When/Then labels + risk chip", () => {
    const html = renderQa([
      row({
        id: "QA-001",
        group: "happy",
        title: "Completes with default",
        given: "a returning claimant",
        when: "they accept the default",
        then: "the claim is submitted",
        traces: "REQ-1 default",
        risk: "low",
      }),
      row({
        id: "QA-002",
        group: "failure",
        title: "Network drop",
        given: "a claimant on submit",
        when: "the network drops",
        then: "the draft is preserved",
        traces: "REQ-4 resilience",
        risk: "high",
      }),
    ])

    // Group labels (happy → failure ordering enforced by QA_GROUP_ORDER).
    expect(html).toContain("Happy path")
    expect(html).toContain("Failure modes")
    expect(html.indexOf("Happy path")).toBeLessThan(html.indexOf("Failure modes"))

    // Ids + titles.
    expect(html).toContain("QA-001")
    expect(html).toContain("Completes with default")

    // Given/When/Then labels rendered for each row.
    expect(html).toContain(">Given<")
    expect(html).toContain(">When<")
    expect(html).toContain(">Then<")
    expect(html).toContain("the claim is submitted")

    // "Verifies:" trace footer.
    expect(html).toContain("Verifies:")
    expect(html).toContain("REQ-1 default")

    // Risk chips with tone-specific classes.
    expect(html).toContain("prdv2-qa-risk-low")
    expect(html).toContain("prdv2-qa-risk-high")
    expect(html).toContain("high risk")
  })

  it("renders open questions when present and omits the block when empty", () => {
    const withOq = renderQa(
      [row({ id: "QA-1", group: "edge", given: "g", when: "w", then: "t" })],
      ["Should partial drafts expire?"],
    )
    expect(withOq).toContain("Open questions")
    expect(withOq).toContain("Should partial drafts expire?")
    expect(withOq).toContain("Edge cases")

    const noOq = renderQa([row({ id: "QA-1", group: "edge", given: "g", when: "w", then: "t" })])
    expect(noOq).not.toContain("Open questions")
  })

  it("buckets ungrouped scenarios into an Other group", () => {
    const html = renderQa([row({ id: "QA-9", group: "", given: "g", when: "w", then: "t" })])
    expect(html).toContain("Other")
    expect(html).toContain("QA-9")
  })
})
