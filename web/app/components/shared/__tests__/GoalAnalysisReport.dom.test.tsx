// @vitest-environment jsdom
//
// The Goal Analysis REPORT. Every test here guards a way the document could
// read as more than it knows:
//
//   1. An unsized finding rendered as 0 — "we could not size this" and "this
//      is worth nothing" lead to OPPOSITE decisions (I3). Guarded in both
//      places a size appears, because the headline repeats the leading one.
//   2. A finding with no visible provenance. If the source documents behind a
//      claim are dropped, the reader has to take it on faith, which is the one
//      thing this engine is supposed to remove.
//   3. "What this cannot tell you" going missing. That section is the product's
//      actual claim; a report that silently omits it looks complete.
//   4. What was read going unstated — including what the USER dropped, which a
//      report that lists it among its sources would be lying about.
import { cleanup, render, screen } from "@testing-library/react"
import { afterEach, describe, expect, it, vi } from "vitest"

vi.hoisted(() => {
  // eslint-disable-next-line @typescript-eslint/no-require-imports
  ;(globalThis as Record<string, unknown>).React = require("react")
})

import { GoalAnalysisReport } from "../GoalAnalysisReport"

const PLAN = {
  goal_text: "raise net revenue retention",
  definition_text: "expansion minus churn across renewing accounts",
  currency: "accounts",
  total_signals: 412,
  sources: [
    {
      source_type: "customer_voice",
      signal_count: 260,
      label: "calls and customer tickets",
      witnesses: "what customers asked for and reported",
    },
    {
      source_type: "project_mgmt",
      signal_count: 152,
      label: "the tracker",
      witnesses: "what was built, broken, blocked or attempted",
    },
  ],
  cannot_answer: [
    {
      question: "How many points will this move the metric?",
      because: "nothing connected here carries numbers",
      remedy: "connect Amplitude, or upload a cohort export",
    },
  ],
  will_produce: ["Themes ranked by how much of your book they touch"],
  excluded_sources: [] as string[],
  hypotheses: [] as string[],
}

const SIZED = {
  id: 1,
  statement: "9 claims across 4 accounts concern export latency.",
  claim_ids: ["c1", "c2"],
  adjudication: "corroborated",
  impact_value: 4,
  currency: "accounts",
  confidence_band: "medium",
  assumed_params: [
    { name: "value_per_account", basis: "no revenue data connected" },
  ],
  surfaced_by: ["Renewal call — Vandelay Industries", "NW-2140"],
  impact: { value: 4, affected_population: 4 },
  confidence: {
    band: "medium",
    weakest_leg: "problem",
    weakest_leg_reason: "one account carries 6 of the 9 claims",
    cap_reason: null,
  },
}

const UNSIZED = {
  ...SIZED,
  id: 2,
  statement: "Onboarding hand-off is raised repeatedly and never quantified.",
  impact_value: null,
  claim_ids: ["c7"],
  surfaced_by: ["Kickoff notes — Initech"],
  impact: { value: null, affected_population: null },
}

const RUN = {
  id: 7,
  status: "ready" as const,
  goal_text: "raise net revenue retention",
  error_code: null,
  coverage_notes: [] as { reason: string; actual: string }[],
  claim_count: 412,
  conversation_id: null,
  // No document has been rendered from this run. The read-only report is what
  // every test in this file is about; the editable half is
  // GoalAnalysisTab.document.dom.test.tsx.
  artifact_id: null,
  created_at: null,
  finished_at: null,
  findings: [SIZED],
  considered: [],
  prioritisation: { plan: PLAN },
}

afterEach(cleanup)

describe("sizing", () => {
  it("renders an unsized finding as unsized, never as zero", async () => {
    // THE ONE THAT MATTERS. A dash and a 0 look similar and mean opposites.
    render(<GoalAnalysisReport run={{ ...RUN, findings: [UNSIZED] }} />)
    expect(screen.getByTestId("goal-unsized").textContent).toBe(
      "Could not be sized",
    )
    const report = screen.getByTestId("goal-report")
    expect(report.textContent).not.toMatch(/\b0 accounts\b/)
    expect(screen.queryByTestId("goal-sized")).toBeNull()
  })

  it("does not size the HEADLINE as zero either", async () => {
    // The headline repeats the leading finding, so it is a second place the
    // same lie can be told — and the more prominent of the two.
    render(<GoalAnalysisReport run={{ ...RUN, findings: [UNSIZED] }} />)
    const headline = screen.getByTestId("goal-headline")
    expect(headline.textContent).toContain("Could not be sized")
    expect(headline.textContent).not.toMatch(/\b0\b/)
    expect(screen.queryByTestId("goal-headline-sized")).toBeNull()
  })

  it("renders a sized finding in the goal's own currency", async () => {
    render(<GoalAnalysisReport run={RUN} />)
    expect(screen.getByTestId("goal-sized").textContent).toBe("4 accounts")
  })

  it("says the ranking is by reach, not by effect on the metric", async () => {
    // The whole ranking rests on a proxy. Presenting it without saying so is
    // how "4 accounts" gets read as "worth more points".
    render(<GoalAnalysisReport run={RUN} />)
    expect(screen.getByTestId("goal-report").textContent).toMatch(/reach/i)
  })
})

describe("provenance", () => {
  it("renders the source documents for EVERY finding that has them", async () => {
    // Not "for the first one". A report that traces its headline and leaves
    // the rest bare is the same unfalsifiable list in nicer type.
    render(<GoalAnalysisReport run={{ ...RUN, findings: [SIZED, UNSIZED] }} />)
    const findings = screen.getAllByTestId("goal-finding")
    const withSources = findings.filter((el) =>
      el.querySelector('[data-testid="goal-sources"]'),
    )
    expect(findings.length).toBe(2)
    expect(withSources.length).toBe(2)
    expect(screen.getAllByTestId("goal-sources").map((el) => el.textContent))
      .toEqual([
        expect.stringContaining("Renewal call — Vandelay Industries"),
        expect.stringContaining("Kickoff notes — Initech"),
      ])
  })

  it("shows no provenance line for a finding that carries none", async () => {
    // The control for the test above: a source line that appears whether or
    // not there are sources proves nothing about the ones that have them.
    render(
      <GoalAnalysisReport
        run={{ ...RUN, findings: [{ ...SIZED, surfaced_by: [] }] }}
      />,
    )
    expect(screen.queryByTestId("goal-sources")).toBeNull()
  })

  it("discloses every assumed parameter beside the number it produced", async () => {
    // I8. A methodology page nobody opens is not a disclosure.
    render(<GoalAnalysisReport run={RUN} />)
    expect(screen.getByTestId("goal-report").textContent).toContain(
      "no revenue data connected",
    )
  })
})

describe("what this cannot tell you", () => {
  it("renders the closing section whenever the plan has gaps", async () => {
    render(<GoalAnalysisReport run={RUN} />)
    const limits = screen.getByTestId("goal-limits")
    expect(screen.getAllByTestId("goal-gap").length).toBe(1)
    expect(limits.textContent).toContain(
      "How many points will this move the metric?",
    )
    // The gap without the fix is a shrug. Both halves render.
    expect(limits.textContent).toContain("nothing connected here carries numbers")
    expect(limits.textContent).toContain("connect Amplitude")
  })

  it("renders every gap, not just the first", async () => {
    render(
      <GoalAnalysisReport
        run={{
          ...RUN,
          prioritisation: {
            plan: {
              ...PLAN,
              cannot_answer: [
                ...PLAN.cannot_answer,
                {
                  question: "Did a change like this work last time?",
                  because: "no measured outcomes are connected",
                  remedy: "connect your experiment tool",
                },
              ],
            },
          },
        }}
      />,
    )
    expect(screen.getAllByTestId("goal-gap").length).toBe(2)
    expect(screen.getByTestId("goal-limits").textContent).toContain(
      "Did a change like this work last time?",
    )
  })

  it("still states the standing limits when the run kept no plan", async () => {
    // An old run has no gap list. Rendering nothing would read as "no limits",
    // which is the opposite of true.
    render(<GoalAnalysisReport run={{ ...RUN, prioritisation: {} }} />)
    const limits = screen.getByTestId("goal-limits")
    expect(limits.textContent).toMatch(/point estimate/i)
    expect(screen.queryAllByTestId("goal-gap").length).toBe(0)
  })
})

describe("what was read", () => {
  it("names each source with its count", async () => {
    render(<GoalAnalysisReport run={RUN} />)
    const read = screen.getByTestId("goal-what-was-read")
    expect(screen.getAllByTestId("goal-read-source").length).toBe(2)
    expect(read.textContent).toContain("calls and customer tickets")
    expect(read.textContent).toContain("260")
    expect(read.textContent).toContain("152")
  })

  it("says what the USER excluded, rather than quietly omitting it", async () => {
    render(
      <GoalAnalysisReport
        run={{
          ...RUN,
          prioritisation: {
            plan: {
              ...PLAN,
              sources: [PLAN.sources[0]],
              total_signals: 260,
              excluded_sources: ["project_mgmt"],
            },
          },
        }}
      />,
    )
    // Readable, not a column name: the label went with the entry the run
    // dropped, so the key is softened rather than printed raw.
    expect(screen.getByTestId("goal-excluded").textContent).toContain(
      "project mgmt",
    )
    expect(screen.getByTestId("goal-excluded").textContent).not.toContain(
      "project_mgmt",
    )
  })

  it("admits when a run kept no record of what it read", async () => {
    render(<GoalAnalysisReport run={{ ...RUN, prioritisation: {} }} />)
    expect(screen.getByTestId("goal-no-plan")).toBeTruthy()
  })

  it("puts the coverage notes ABOVE the findings they qualify", async () => {
    // A note that a third of the evidence was undated changes how every line
    // beneath it reads, so it cannot sit under them.
    render(
      <GoalAnalysisReport
        run={{
          ...RUN,
          coverage_notes: [
            { reason: "undated evidence", actual: "40 of 300 signals carried no date" },
          ],
        }}
      />,
    )
    const report = screen.getByTestId("goal-report")
    expect(screen.getByTestId("goal-coverage").textContent).toContain(
      "40 of 300 signals carried no date",
    )
    expect(report.textContent!.indexOf("undated evidence")).toBeLessThan(
      report.textContent!.indexOf(SIZED.statement),
    )
  })
})

describe("the goal and its definition", () => {
  it("quotes the confirmed definition verbatim", async () => {
    render(<GoalAnalysisReport run={RUN} />)
    expect(screen.getByTestId("goal-definition").textContent).toContain(
      "expansion minus churn across renewing accounts",
    )
  })

  it("says so when no definition was recorded, instead of showing none", async () => {
    render(<GoalAnalysisReport run={{ ...RUN, prioritisation: {} }} />)
    expect(screen.getByTestId("goal-no-definition")).toBeTruthy()
  })
})

describe("the user's own hypotheses", () => {
  it("renders them, and refuses to imply a verdict on them", async () => {
    // The engine does not test a stated hypothesis against the claims. Showing
    // these beside the findings without saying so would let their absence be
    // read as "not supported" — a conclusion nothing produced.
    render(
      <GoalAnalysisReport
        run={{
          ...RUN,
          prioritisation: {
            plan: { ...PLAN, hypotheses: ["pricing is the blocker"] },
          },
        }}
      />,
    )
    const section = screen.getByTestId("goal-hypotheses")
    expect(section.textContent).toContain("pricing is the blocker")
    expect(section.textContent).toMatch(/did not test/i)
  })

  it("omits the section entirely when none were given", async () => {
    render(<GoalAnalysisReport run={RUN} />)
    expect(screen.queryByTestId("goal-hypotheses")).toBeNull()
  })
})

describe("the ruled-out ledger", () => {
  const rejection = (i: number) => ({
    id: i,
    label: `candidate ${i}`,
    reason: `only ${i} supporting claims`,
    stopped_at_stage: "verification",
    claim_ids: [],
  })

  it("keeps the closing section reachable when the ledger is long", async () => {
    // A run can drop a hundred candidates. Rendering all hundred expanded
    // buries "what this cannot tell you" under them — and that section is the
    // one a reader has to reach.
    const many = Array.from({ length: 40 }, (_, i) => rejection(i + 1))
    render(<GoalAnalysisReport run={{ ...RUN, considered: many }} />)
    const ledger = screen.getByTestId("goal-considered")
    // The COUNT is visible whether or not the list is expanded, so a thinner
    // ledger can never hide behind the fold.
    expect(ledger.querySelector("summary")!.textContent).toContain("40")
    expect(ledger.querySelector("details")!.open).toBe(false)
    // Every reason is still in the document, one click away — not dropped.
    expect(ledger.textContent).toContain("only 40 supporting claims")
    expect(screen.getByTestId("goal-limits")).toBeTruthy()
  })

  it("stays open when there are few enough to read at once", async () => {
    render(<GoalAnalysisReport run={{ ...RUN, considered: [rejection(1), rejection(2)] }} />)
    expect(
      screen.getByTestId("goal-considered").querySelector("details")!.open,
    ).toBe(true)
  })
})

describe("a run where nothing survived", () => {
  it("says so, and points at the list of why", async () => {
    render(
      <GoalAnalysisReport
        run={{
          ...RUN,
          findings: [],
          considered: [
            {
              id: 1,
              label: "onboarding friction",
              reason: "all 4 supporting claims land within 6 days",
              stopped_at_stage: "verification",
              claim_ids: ["c9"],
            },
          ],
        }}
      />,
    )
    expect(screen.getByTestId("goal-headline").textContent).toContain(
      "Nothing survived verification",
    )
    expect(screen.getByTestId("goal-considered").textContent).toContain(
      "all 4 supporting claims land within 6 days",
    )
    // And no headline size is invented for a finding that does not exist.
    expect(screen.queryByTestId("goal-headline-sized")).toBeNull()
    expect(screen.queryByTestId("goal-headline-unsized")).toBeNull()
  })
})
