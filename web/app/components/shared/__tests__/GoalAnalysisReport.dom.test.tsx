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
import type { GoalFinding } from "../../../lib/api"

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
    //
    // It used to say this by printing "Could not be sized" straight after "It
    // is the largest thing this reading found:", which is the two halves of
    // one sentence contradicting each other. The absence is now stated as a
    // sentence instead of spliced into a superlative — so what this test
    // guards is the MEANING (no number, and it says the size is unknown), not
    // the particular words that used to carry it.
    render(<GoalAnalysisReport run={{ ...RUN, findings: [UNSIZED] }} />)
    const headline = screen.getByTestId("goal-headline")
    // One finding, and it has no size — so the honest sentence is that
    // nothing in this reading could be sized. What must never appear is a
    // number, or a superlative resting on one.
    expect(headline.textContent).toMatch(
      /nothing in this reading could be sized/i,
    )
    expect(headline.textContent).not.toMatch(/\b0\b/)
    expect(headline.textContent).not.toMatch(/largest/i)
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

describe("what the ordering is allowed to claim", () => {
  // THIS PANEL IS THE SECOND RENDERER OF THE SAME REPORT, and it is the one a
  // reader looks at — `backend/app/crucible/report.py` renders the exported
  // document. Every rule below exists identically in both, because a fix
  // applied to one of them leaves the other telling the reader the same
  // falsehood in a more prominent place.
  const BIG = { ...SIZED, id: 3, impact_value: 900, claim_ids: ["c9"] }

  it("does not call the top the largest while anything went unsized", () => {
    // "The largest thing this reading found" quantifies over EVERYTHING, and
    // an unsized finding is not a small one — its size is unknown, and an
    // unknown can be bigger.
    render(<GoalAnalysisReport run={{ ...RUN, findings: [SIZED, UNSIZED] }} />)
    const note = screen.getByTestId("goal-headline-note").textContent ?? ""
    expect(note).not.toMatch(/largest thing this reading found/i)
    expect(note).toMatch(/largest of the ones that could be sized/i)
    expect(note).toMatch(/One of these could not be sized/i)
  })

  it("keeps the superlative when every finding was sized", () => {
    // The weaker sentence must not swallow the strong one: hedging a claim
    // the run DID establish is its own kind of inaccuracy.
    render(<GoalAnalysisReport run={{ ...RUN, findings: [BIG, SIZED] }} />)
    const note = screen.getByTestId("goal-headline-note").textContent ?? ""
    expect(note).toMatch(/largest thing this reading found/i)
    expect(note).not.toMatch(/could be sized/i)
  })

  it("does not call a conflict-led run's top row the largest", () => {
    // `_rank`'s dominant term places an authoritative disagreement above
    // everything that is not one, so the top row here is a 4-account finding
    // sitting above a 900-account one.
    const CONFLICT = { ...SIZED, id: 4, adjudication: "conflict" }
    render(
      <GoalAnalysisReport run={{ ...RUN, findings: [CONFLICT, BIG] }} />,
    )
    const note = screen.getByTestId("goal-headline-note").textContent ?? ""
    expect(note).not.toMatch(/largest/i)
    expect(note).toMatch(/placed above every finding that is not one/i)
  })

  it("does not claim a reach ranking when nothing could be sized", () => {
    render(<GoalAnalysisReport run={{ ...RUN, findings: [UNSIZED] }} />)
    const lede = screen.getByTestId("goal-findings-lede").textContent ?? ""
    expect(lede).toMatch(/not ranked by reach/i)
    expect(lede).toMatch(/ordered by confidence/i)
  })

  it("says when the order rests on something the reader cannot see", () => {
    // `_rank`'s last term is a confidence SCORE, never rendered — the reader
    // sees bands. With no outcome evidence every band comes out the same, so a
    // list that LOOKS ranked is read as ranked. Position is the most
    // persuasive thing on a page.
    const flat = { ...UNSIZED, confidence_band: "medium" }
    render(
      <GoalAnalysisReport
        run={{ ...RUN, findings: [
          { ...flat, id: 1, claim_ids: ["c1"] },
          { ...flat, id: 2, claim_ids: ["c2"] },
        ] }}
      />,
    )
    const lede = screen.getByTestId("goal-findings-lede").textContent ?? ""
    expect(lede).toMatch(/same confidence band/i)
    expect(lede).toMatch(/not as a verdict on which matters more/i)
  })

  it("does not disclaim an order the bands actually justify", () => {
    // The control: when the bands differ the order IS checkable from the page,
    // and telling the reader to discount it would be its own inaccuracy.
    render(
      <GoalAnalysisReport
        run={{ ...RUN, findings: [
          { ...UNSIZED, id: 1, confidence_band: "high", claim_ids: ["c1"] },
          { ...UNSIZED, id: 2, confidence_band: "low", claim_ids: ["c2"] },
        ] }}
      />,
    )
    const lede = screen.getByTestId("goal-findings-lede").textContent ?? ""
    expect(lede).toMatch(/not ranked by reach/i)
    expect(lede).not.toMatch(/same confidence band/i)
  })

  it("says how many it could not size, once, when the ranking is partial", () => {
    // THE DISCLOSURE MOVED, IT DID NOT GO. The headline and this lede both
    // carried the count and the caveat, three lines apart, and a real report
    // read "…257 of these could not be sized at all, and a missing size is not
    // a small one" immediately above "…and 257 of them could not be sized at
    // all. An unsized theme sorts last without being small". With a SIZED top
    // row the headline says both, so the lede says neither.
    render(<GoalAnalysisReport run={{ ...RUN, findings: [SIZED, UNSIZED] }} />)
    const head = screen.getByTestId("goal-headline").textContent ?? ""
    const lede = screen.getByTestId("goal-findings-lede").textContent ?? ""

    expect(head).toMatch(/one of these could not be sized at all/i)
    expect(head).toMatch(/is not a small one/i)
    expect(lede).not.toMatch(/could not be sized at all/i)
    expect(lede).not.toMatch(/its size is unknown, not zero/i)
  })

  it("still names the count when the headline states only the caveat", () => {
    // THE CASE A BOOLEAN GOT WRONG. With an UNSIZED top row the headline says
    // "a missing size is not a small one" and never names how many — so
    // suppressing the whole lede clause drops the count out of the page.
    render(<GoalAnalysisReport
      run={{ ...RUN, findings: [UNSIZED, { ...UNSIZED, id: 99 }, SIZED] }} />)
    const lede = screen.getByTestId("goal-findings-lede").textContent ?? ""
    expect(lede).toMatch(/2 of them could not be sized at all/i)
    // And does not repeat the caveat the headline just made.
    expect(lede).not.toMatch(/its size is unknown, not zero/i)
  })
})

describe("one fact about the corpus is not many about the findings", () => {
  // A corpus with no outcome evidence anywhere gives EVERY finding the same
  // weakest link. Printed on all 32 rows it reads as 32 separate judgements
  // about 32 different themes, and a reader who meets an identical sentence
  // three times stops reading the section — which is how a genuine
  // per-finding difference would later go unnoticed.
  // TYPED, not `unknown`. An `unknown` here is not assignable to
  // `GoalFinding` and added 11 tsc errors — a fixture that does not typecheck
  // is the same silent-drift problem as the untyped `Record<string, unknown>`
  // fixtures that let unknown keys vanish elsewhere in this suite.
  type Conf = GoalFinding["confidence"]
  const conf = (over: Partial<Conf>): Conf => ({
    band: "medium", weakest_leg: null, weakest_leg_reason: null,
    cap_reason: null, ...over,
  })
  const withConf = (id: number, confidence: Conf): GoalFinding => ({
    ...SIZED, id, confidence, claim_ids: [`c${id}`],
  } as GoalFinding)
  const SAME = conf({ weakest_leg_reason: "no outcome evidence exists" })

  it("states a weakest link shared by every finding exactly once", () => {
    render(
      <GoalAnalysisReport
        run={{ ...RUN, findings: [withConf(1, SAME), withConf(2, SAME), withConf(3, SAME)] }}
      />,
    )
    expect(screen.getByTestId("goal-shared-weakest").textContent ?? "")
      .toMatch(/every finding below has the same weakest link/i)
    const report = screen.getByTestId("goal-report").textContent ?? ""
    expect(report.split("no outcome evidence exists").length - 1).toBe(1)
    expect(report).not.toMatch(/Weakest link\./)
  })

  it("joins the cap onto the weakest link as a clause, not a new sentence", () => {
    // `cap_reason` arrives uncapitalised ("capped at medium: …"), so joining
    // it after a full stop rendered "…the diagnosis are not. capped at
    // medium". Shipped once and only caught by reading the rendered panel —
    // hence this test, since nothing else in the suite reads the join.
    const withCap = conf({
      weakest_leg_reason: "no outcome evidence exists",
      cap_reason: "capped at medium: no outcome evidence in the corpus",
    })
    render(
      <GoalAnalysisReport
        run={{ ...RUN, findings: [withConf(1, withCap), withConf(2, withCap)] }}
      />,
    )
    const said = screen.getByTestId("goal-shared-weakest").textContent ?? ""
    expect(said).toContain("exists; capped at medium")
    expect(said).not.toMatch(/\.\s+capped at medium/)
  })

  it("puts two different weakest links back on their own rows", () => {
    // The control. Detected, not assumed: the moment they differ, the sentence
    // is about the finding again and belongs beside it.
    render(
      <GoalAnalysisReport
        run={{
          ...RUN,
          findings: [
            withConf(1, SAME),
            withConf(2, conf({ band: "low", weakest_leg_reason: "one account carries it" })),
          ],
        }}
      />,
    )
    expect(screen.queryByTestId("goal-shared-weakest")).toBeNull()
    const report = screen.getByTestId("goal-report").textContent ?? ""
    expect(report).toContain("no outcome evidence exists")
    expect(report).toContain("one account carries it")
  })

  it("leaves a lone finding's weakest link where it is", () => {
    // One finding is not a corpus-wide pattern; "every finding below" would be
    // a claim about a set of one.
    render(<GoalAnalysisReport run={{ ...RUN, findings: [withConf(1, SAME)] }} />)
    expect(screen.queryByTestId("goal-shared-weakest")).toBeNull()
    expect(screen.getByTestId("goal-report").textContent ?? "")
      .toMatch(/Weakest link\./)
  })

  it("says once that every rejection died for the same reason", () => {
    // One group is the degenerate case of grouping: the reason belongs to the
    // group heading, not to each of the four rows beneath it.
    const considered = [0, 1, 2, 3].map((i) => ({
      id: i, label: `candidate ${i}`,
      reason: "no source that may speak to this claim type reported it",
      stopped_at_stage: "verification", claim_ids: [],
    }))
    render(<GoalAnalysisReport run={{ ...RUN, considered }} />)
    const said = screen.getByTestId("goal-considered").textContent ?? ""
    expect(said).toMatch(/every one of them died for the same one/i)
    expect(
      said.split("no source that may speak to this claim type reported it").length - 1,
    ).toBe(1)
    expect(screen.getAllByTestId("goal-ruled-out-group")).toHaveLength(1)
    for (const i of [0, 1, 2, 3]) expect(said).toContain(`candidate ${i}`)
  })

  it("groups rejections by reason, biggest cause first", () => {
    // THE SHAPE OF THE ANSWER. A real run rejected 102 candidates for five
    // reasons — 49 one way, 47 another — and the flat list repeated each
    // reason beside each label, so a reader could not see that half the ledger
    // died one way and half another without counting by hand.
    const considered = [
      ...[0, 1, 2].map((i) => ({
        id: i, label: `a${i}`, reason: "no authoritative source",
        stopped_at_stage: "verification", claim_ids: [],
      })),
      ...[0, 1, 2, 3, 4].map((i) => ({
        id: 90 + i, label: `b${i}`, reason: "only 1 supporting claim",
        stopped_at_stage: "clustering", claim_ids: [],
      })),
    ]
    render(<GoalAnalysisReport run={{ ...RUN, considered }} />)
    const said = screen.getByTestId("goal-considered").textContent ?? ""
    expect(screen.getAllByTestId("goal-ruled-out-group")).toHaveLength(2)
    // Each reason once, as a heading over its own group.
    expect(said.split("no authoritative source").length - 1).toBe(1)
    expect(said.split("only 1 supporting claim").length - 1).toBe(1)
    // Biggest cause first.
    expect(said.indexOf("only 1 supporting claim"))
      .toBeLessThan(said.indexOf("no authoritative source"))
    for (const l of ["a0", "a2", "b0", "b4"]) expect(said).toContain(l)
  })

  it("keeps differing rejection reasons on their own rows", () => {
    const considered = [
      { id: 1, label: "alpha", reason: "one account only",
        stopped_at_stage: "verification", claim_ids: [] },
      { id: 2, label: "beta", reason: "one conversation echoing",
        stopped_at_stage: "verification", claim_ids: [] },
    ]
    render(<GoalAnalysisReport run={{ ...RUN, considered }} />)
    const said = screen.getByTestId("goal-considered").textContent ?? ""
    expect(said).not.toMatch(/died for the same reason/i)
    expect(said).toContain("one account only")
    expect(said).toContain("one conversation echoing")
  })
})

describe("what the report admits it did not do", () => {
  it("says the findings were not selected for the goal", () => {
    // Claim selection never sees the definition: `build_findings` takes a
    // `goal_accounts` filter that production does not pass. Unstated, the
    // panel LOOKS like it answered the question it was asked.
    render(<GoalAnalysisReport run={RUN} />)
    const said = screen.getByTestId("goal-not-selected").textContent ?? ""
    expect(said).toMatch(/not selected for your goal/i)
    expect(said).toMatch(/filtered or ranked by relevance/i)
  })

  it("does not also claim the definition chose them", () => {
    // The two sentences are five sections apart and used to contradict each
    // other, with the false one in the more prominent position.
    render(<GoalAnalysisReport run={RUN} />)
    const report = screen.getByTestId("goal-report").textContent ?? ""
    expect(report).not.toMatch(/measured against that sentence/i)
    expect(
      screen.getByTestId("goal-definition-note").textContent ?? "",
    ).toMatch(/did not decide which findings appear below/i)
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

describe("an assumption every finding makes is stated once", () => {
  // "lots of irrelevant information". On a corpus with no revenue connected
  // every finding carries the identical "value_per_account: no revenue data
  // connected; accounts weighted equally" — a real report printed it on all
  // 279. That is not disclosure, it is what the reader has to look past to
  // find the assumptions that ARE per-finding.
  it("hoists it out of the findings and states it once", () => {
    render(<GoalAnalysisReport
      run={{ ...RUN, findings: [SIZED, { ...SIZED, id: 2, statement: "b" }] }} />)
    const page = document.body.textContent ?? ""
    expect(screen.getByTestId("goal-shared-assumptions")).toBeTruthy()
    // Stated — not lost.
    expect(page).toContain("value_per_account")
    // Once.
    expect(page.split("value_per_account").length - 1).toBe(1)
  })

  it("leaves assumptions that differ on their own findings", () => {
    // The moment two findings assume different things the hoist is wrong: it
    // would attribute one finding's assumption to every other.
    render(<GoalAnalysisReport run={{ ...RUN, findings: [
      SIZED,
      { ...SIZED, id: 2, statement: "b",
        assumed_params: [{ name: "value_per_account", basis: "cohort median" }] },
    ] }} />)
    expect(screen.queryByTestId("goal-shared-assumptions")).toBeNull()
    const page = document.body.textContent ?? ""
    expect(page).toContain("no revenue data connected")
    expect(page).toContain("cohort median")
  })

  it("leaves a lone finding's assumption on itself", () => {
    // Hoisting out of one finding moves the disclosure away from the number it
    // qualifies, for no saving at all.
    render(<GoalAnalysisReport run={{ ...RUN, findings: [SIZED] }} />)
    expect(screen.queryByTestId("goal-shared-assumptions")).toBeNull()
    expect(document.body.textContent).toContain("no revenue data connected")
  })

  it("fires when only the sized findings carry an assumption", () => {
    // THE SHAPE REAL DATA HAS. A live run had 326 findings — 30 sized and
    // carrying value_per_account, 296 unsized and carrying nothing. Asking
    // whether EVERY finding matched never fired, so the line stayed repeated
    // 30 times on the page written to de-duplicate it.
    render(<GoalAnalysisReport run={{ ...RUN, findings: [
      SIZED,
      { ...SIZED, id: 2, statement: "s2" },
      { ...UNSIZED, id: 3, statement: "u1", assumed_params: [] },
      { ...UNSIZED, id: 4, statement: "u2", assumed_params: [] },
    ] }} />)
    const page = document.body.textContent ?? ""
    expect(page.split("value_per_account").length - 1).toBe(1)
    // And says how many it speaks for, rather than claiming all of them.
    expect(page).toContain("2 of the findings below rest on the same assumption")
    expect(page).not.toContain("Every finding below rests on the same assumption")
  })

  it("does not hoist a single carrier", () => {
    render(<GoalAnalysisReport run={{ ...RUN, findings: [
      SIZED,
      { ...UNSIZED, id: 3, statement: "u1", assumed_params: [] },
    ] }} />)
    expect(screen.queryByTestId("goal-shared-assumptions")).toBeNull()
    expect(document.body.textContent).toContain("no revenue data connected")
  })
})

describe("the finding is a card, not a sentence", () => {
  // Apurva: make the document "display data in a more beautiful manner, so
  // that the user is able to understand the wins". The heading used to be the
  // whole sentence, so the one word a reader scans for sat mid-clause, in
  // quotes, behind two numbers the chips repeat verbatim.
  const CARD = {
    ...SIZED,
    statement: '25 claims across 2 accounts concern “AI tabletop generation” — for example, “x”.',
    label: "AI tabletop generation",
    example: "Northwind tailors scenarios by role and complexity",
  }

  it("leads with the theme and sets the quote as a quote", () => {
    render(<GoalAnalysisReport run={{ ...RUN, findings: [CARD] }} />)
    const head = screen.getAllByTestId("goal-finding")[0]
      .querySelector(".ga-finding-statement")!.textContent
    expect(head).toBe("AI tabletop generation")
    expect(head).not.toMatch(/claims across/)
    expect(screen.getByTestId("goal-finding-example").textContent)
      .toContain("Northwind tailors scenarios by role and complexity")
  })

  it("falls back to the sentence for a run stored before the theme existed", () => {
    // An empty heading would be a worse regression than the run-on it replaced.
    const old = { ...SIZED, statement: "9 claims concern export latency.", label: undefined }
    render(<GoalAnalysisReport run={{ ...RUN, findings: [old] }} />)
    expect(screen.getAllByTestId("goal-finding")[0]
      .querySelector(".ga-finding-statement")!.textContent)
      .toContain("export latency")
    expect(screen.queryByTestId("goal-finding-example")).toBeNull()
  })

  it("does not print the quote twice when the sentence is the heading", () => {
    const old = {
      ...SIZED, label: undefined,
      statement: '4 claims concern “x” — for example, “the export times out”.',
      example: "the export times out",
    }
    render(<GoalAnalysisReport run={{ ...RUN, findings: [old] }} />)
    const section = screen.getAllByTestId("goal-finding")[0].textContent ?? ""
    expect(section.split("the export times out").length - 1).toBe(1)
  })
})

describe("the card leads with what to do", () => {
  // Apurva: "we should start with a recommendation on how to solve this, this
  // is only the issues, no suggestion on how to solve or what's the exact
  // recommendation from it".
  const withRec = (extra: unknown[], findings = [SIZED]) => ({
    ...RUN, findings,
    prioritisation: { ...(RUN.prioritisation ?? {}), findings_extra_by_rank: extra },
  })

  it("shows the recommendation above the counts, with its justification", () => {
    render(<GoalAnalysisReport run={withRec([{
      recommendation: {
        action: "Route export tickets to the rendering on-call team",
        because: "three accounts named export in a renewal call",
      },
    }]) as never} />)
    const card = screen.getAllByTestId("goal-finding")[0].textContent ?? ""
    expect(card).toContain("Route export tickets to the rendering on-call team")
    expect(card).toContain("three accounts named export in a renewal call")
    // It LEADS: above the counts, not a footnote under them.
    expect(card.indexOf("Recommended.")).toBeLessThan(card.indexOf("medium confidence"))
  })

  it("renders nothing when a finding has no recommendation", () => {
    // Only the top findings get one, and anything that failed a check was
    // dropped rather than repaired. Absent is the normal case.
    render(<GoalAnalysisReport run={{ ...RUN, findings: [SIZED] }} />)
    expect(screen.queryByTestId("goal-finding-recommendation")).toBeNull()
    expect(document.body.textContent).toContain("medium confidence")
  })

  it("does not render half a recommendation", () => {
    render(<GoalAnalysisReport run={withRec([{
      recommendation: { action: "Do the thing", because: "" },
    }]) as never} />)
    expect(screen.queryByTestId("goal-finding-recommendation")).toBeNull()
    expect(document.body.textContent).not.toContain("Do the thing")
  })

  it("ignores extras that do not line up with the findings", () => {
    // The merge is positional. Attaching one finding's recommendation to
    // another is far worse than showing none.
    render(<GoalAnalysisReport run={withRec(
      [{ recommendation: { action: "A", because: "b" } }],
      [SIZED, { ...SIZED, id: 2, statement: "second" }],
    ) as never} />)
    expect(screen.queryByTestId("goal-finding-recommendation")).toBeNull()
  })
})

describe("the goal-relevance gate, in the panel", () => {
  // Apurva ruled for a gate after a run for "grow revenue by 5%" led with three
  // descriptions of the company's OWN product — the order is how many accounts
  // mentioned a theme, and what gets mentioned most on a sales call is the
  // vendor's own demo.
  const withAside = (reasons: (string | null)[], findings: unknown[]) => ({
    ...RUN, findings,
    prioritisation: { ...(RUN.prioritisation ?? {}), set_aside_by_rank: reasons },
  })

  const A = { ...SIZED, id: 1, statement: "renewals stall", label: "renewals" }
  const B = { ...SIZED, id: 2, statement: "our platform does X", label: "our platform" }

  it("moves a set-aside finding to the appendix, with its reason", () => {
    render(<GoalAnalysisReport run={withAside(
      [null, "describes our own product, not a customer problem"], [A, B]) as never} />)

    const main = screen.getAllByTestId("goal-finding").map(n => n.textContent).join(" ")
    expect(main).toContain("renewals")
    expect(main).not.toContain("our platform")
    // Moved, not deleted — and it took its reason with it.
    const aside = screen.getByTestId("goal-set-aside").textContent ?? ""
    expect(aside).toContain("our platform")
    expect(aside).toContain("describes our own product, not a customer problem")
  })

  it("states the funnel before the findings", () => {
    // A filtered list that does not say it was filtered is the more
    // confident-looking of the two, and the less honest.
    render(<GoalAnalysisReport run={withAside([null, "off-topic"], [A, B]) as never} />)
    const funnel = screen.getByTestId("goal-funnel").textContent ?? ""
    expect(funnel).toContain("2 themes were found")
    expect(funnel).toContain("1 bear on this goal")
  })

  it("says nothing when nothing was set aside", () => {
    render(<GoalAnalysisReport run={withAside([null, null], [A, B]) as never} />)
    expect(screen.queryByTestId("goal-funnel")).toBeNull()
    expect(screen.queryByTestId("goal-set-aside")).toBeNull()
    expect(screen.getAllByTestId("goal-finding")).toHaveLength(2)
  })

  it("sets nothing aside when the lists do not line up", () => {
    // The split is positional. Setting aside the WRONG finding is far worse
    // than setting none aside.
    render(<GoalAnalysisReport run={withAside(["off-topic"], [A, B]) as never} />)
    expect(screen.queryByTestId("goal-set-aside")).toBeNull()
    expect(screen.getAllByTestId("goal-finding")).toHaveLength(2)
  })
})

describe("the RICE table in the panel", () => {
  // TWO RENDERERS OF ONE RANKING. The document grew a scoring table and the
  // panel did not, which would have shipped a feature the reader never sees —
  // the panel is what is open while the document is a thing you save.
  const withRice = (findings: unknown[]) => ({
    ...RUN, findings,
    // MERGED, not replaced: the panel's "what was read" section reads
    // `plan.total_signals`, so a fixture that swaps the whole plan out crashes
    // the render before the table is reached.
    prioritisation: {
      ...(RUN.prioritisation ?? {}),
      plan: { ...((RUN.prioritisation ?? {}).plan ?? {}), framework: "RICE" },
    },
  })
  const F = (id: number, label: string, reach: number | null, types: string[]) => ({
    ...SIZED, id, label, statement: `${label} stmt`,
    impact_value: reach, claim_types: types,
  })

  it("shows every term and names the one it cannot fill", () => {
    render(<GoalAnalysisReport run={withRice([F(1, "blocked", 5, ["constraint"])]) as never} />)
    const t = screen.getByTestId("goal-rice").textContent ?? ""
    for (const h of ["Reach", "Impact", "Confidence", "Effort", "Score", "Inputs"]) {
      expect(t).toContain(h)
    }
    expect(t).toContain("person-month")
    // NAMED IN THE CELL, not merely in the key above it. Asserting the word
    // appears anywhere passed against a mutation that filled the cell with "1"
    // — a table quietly supplying the one number nothing supports.
    const effortCells = [...screen.getByTestId("goal-rice")
      .querySelectorAll("tbody tr")].map(r => r.querySelectorAll("td")[4]?.textContent)
    expect(effortCells).toEqual(["Unquantified"])
  })

  it("scores a blocker above a bigger theme that only describes", () => {
    // THE POINT OF SCORING AT ALL. Reach alone put commentary above blocked
    // revenue.
    render(<GoalAnalysisReport run={withRice([
      F(1, "chatter", 11, ["mechanism"]),
      F(2, "blocked", 5, ["constraint"]),
    ]) as never} />)
    const cells = [...screen.getByTestId("goal-rice").querySelectorAll("tbody tr")]
      .map(r => [...r.querySelectorAll("td")].map(c => c.textContent))
    const chatter = Number(cells[0][5])
    const blocked = Number(cells[1][5])
    expect(blocked).toBeGreaterThan(chatter)
  })

  it("renders an unsized finding as no score, never zero", () => {
    render(<GoalAnalysisReport run={withRice([F(1, "unsized", null, ["preference"])]) as never} />)
    const cells = [...screen.getByTestId("goal-rice").querySelectorAll("tbody td")]
      .map(c => c.textContent)
    expect(cells).toContain("—")
    expect(cells).not.toContain("0.0")
  })

  it("does not reorder the findings", () => {
    // `_rank` froze the order; a table that re-sorted would be the
    // prioritisation step mutating the ranking (I10).
    render(<GoalAnalysisReport run={withRice([
      F(1, "first", 1, ["mechanism"]),
      F(2, "second", 50, ["constraint"]),
    ]) as never} />)
    const rows = [...screen.getByTestId("goal-rice").querySelectorAll("tbody tr")]
      .map(r => r.querySelector("td")?.textContent)
    expect(rows).toEqual(["first", "second"])
  })

  it("says nothing when no framework is set", () => {
    render(<GoalAnalysisReport run={{ ...RUN, findings: [SIZED] }} />)
    expect(screen.queryByTestId("goal-rice")).toBeNull()
  })
})
